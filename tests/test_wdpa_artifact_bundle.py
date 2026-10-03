import hashlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from ingestion.common.publication import PublicationError
from ingestion.common.identity_reset import CONTRACT_ID
from ingestion.wdpa_monthly import artifact_bundle as bundle, run as wdpa
from scripts import release_feature_model as model, wdpa_processing_gate as gate
from tests.test_wdpa_execution_observer import accepted_evidence


class Objects:
    """An immutable object store that enforces real generation preconditions."""

    def __init__(self):
        self.data = {}
        self.calls = []

    def bucket(self, name):
        assert name == bundle.BUCKET
        return self

    def blob(self, name, generation=None):
        store = self

        class Blob:
            def __init__(self):
                self.name, self.generation = name, generation

            def upload_from_filename(self, path, *, if_generation_match, checksum):
                assert (
                    if_generation_match == 0
                    and name not in store.data
                    and checksum == "crc32c"
                )
                self.generation = len(store.data) + 1
                store.data[name] = (self.generation, Path(path).read_bytes())

            def download_to_filename(self, path, *, if_generation_match, checksum):
                actual, data = store.data[name]
                assert (
                    actual == self.generation == if_generation_match
                    and checksum == "crc32c"
                )
                store.calls.append(name)
                Path(path).write_bytes(data)

        return Blob()


@pytest.fixture
def built(tmp_path):
    report = accepted_evidence()["build"]
    report.pop("artifact_bundle")
    store = Objects()
    stager = bundle.BuildStager(store, report["cloud_execution"])
    staged = {}
    for asset in wdpa.ASSETS:
        directory = tmp_path / asset.slug
        directory.mkdir()
        paths = {}
        for role in (
            *bundle.ROLES,
            *(f"metadata_{locale}" for locale in bundle.LOCALES),
        ):
            path = directory / (asset.slug + "." + role)
            path.write_bytes((asset.slug + ":" + role).encode())
            paths[role] = path
        snapshot = model.GeneratedIdentitySnapshot(
            f"gs://{bundle.BUCKET}/{asset.root}/latest/{asset.slug}.manifest.json",
            101,
            "a" * 64,
        )
        hashes = {role: wdpa.sha256_file(path) for role, path in paths.items()}
        hashes["csv"] = hashes["metadata_translations"]
        summary = report["assets"][asset.slug]
        outputs = wdpa.AssetOutputs(
            **{role: paths[role] for role in bundle.ROLES},
            localized_metadata={
                locale: paths[f"metadata_{locale}"] for locale in bundle.LOCALES
            },
            manifest=directory / "unused.json",
            row_count=summary["rows"],
            sha256=hashes,
            schema_payload={},
            next_generated_feature_id=summary["next_generated_feature_id"],
            previous_generated_feature_id=10,
            previous_release="2026-09-30",
            identity_baseline_snapshot=snapshot,
            identity_contract=CONTRACT_ID,
            identity_decisions={},
            localization_report={},
        )
        staged[asset.slug] = stager.stage_asset(
            asset, outputs, (wdpa.FieldSpec("SITE_PID", "String", ""),)
        )
        summary["artifact_sha256"] = hashes
        for path in paths.values():
            path.unlink()
    report["staged_assets"] = staged
    ref = stager.commit(report, tmp_path)
    return store, ref, report


def publisher_for(report):
    publisher = Mock(spec=wdpa.GcsPublisher)
    publisher.resume.return_value = None
    publisher.load_successful_run_record.return_value = None

    def state(asset):
        facts = report["staged_assets"][asset.slug]["outputs"]
        return SimpleNamespace(
            value={
                "active": None,
                "current": {"latest_manifest": facts["identity_baseline_snapshot"]},
                "reserved_next_feature_id": facts["previous_generated_feature_id"],
            }
        )

    publisher.state.side_effect = state
    return publisher


def test_one_build_survives_local_cleanup_and_promotes_identical_bytes(
    built, tmp_path, monkeypatch
):
    store, ref, report = built
    publisher = publisher_for(report)
    published = []

    def publish(*, asset, outputs, **_kwargs):
        # Both realm downloads finished before any publication starts.
        assert len(store.calls) == 1 + 2 * (len(bundle.ROLES) + len(bundle.LOCALES))
        for role, path in bundle.artifact_paths(outputs).items():
            original = report["staged_assets"][asset.slug]["artifacts"][role]
            assert hashlib.sha256(path.read_bytes()).hexdigest() == original["sha256"]
        published.append(asset.slug)
        return {"asset_slug": asset.slug, "status": "success"}

    monkeypatch.setattr(wdpa, "publish_asset", publish)
    for name in ("build_asset_outputs", "download_file", "prepare_source_datasets"):
        monkeypatch.setattr(
            wdpa, name, lambda *_a, **_k: pytest.fail("promotion rebuilt the dataset")
        )
    result = bundle.promote(store, publisher, ref, tmp_path / "promotion")
    assert published == [asset.slug for asset in wdpa.ASSETS]
    assert len(result) == 2


def test_changed_staged_file_fails_before_any_publication(built, tmp_path, monkeypatch):
    store, ref, report = built
    item = report["staged_assets"]["wdpa-terrestrial"]["artifacts"]["metadata"]
    name = bundle.check_reference(item)
    generation, data = store.data[name]
    store.data[name] = generation, b"x" * len(data)
    publisher = publisher_for(report)
    publish = Mock()
    monkeypatch.setattr(wdpa, "publish_asset", publish)
    with pytest.raises(PublicationError, match="bytes"):
        bundle.promote(store, publisher, ref, tmp_path / "promotion")
    publish.assert_not_called()


def test_stale_baseline_in_either_realm_fails_before_publication(
    built, tmp_path, monkeypatch
):
    store, ref, report = built
    publisher = publisher_for(report)
    original = publisher.state.side_effect

    def stale(asset):
        state = original(asset)
        if asset.slug == "wdpa-terrestrial":
            state.value["reserved_next_feature_id"] += 1
        return state

    publisher.state.side_effect = stale
    publish = Mock()
    monkeypatch.setattr(wdpa, "publish_asset", publish)
    with pytest.raises(PublicationError, match="counter"):
        bundle.promote(store, publisher, ref, tmp_path / "promotion")
    publish.assert_not_called()
    assert len(store.calls) == 1


@pytest.mark.parametrize(
    "defect",
    [
        "memory",
        "partial",
        "missing_bundle",
        "canonical",
        "generation",
        "hash",
        "counts",
    ],
)
def test_single_build_acceptance_does_not_accept_missing_or_unverified_bytes(defect):
    run = accepted_evidence()["build"]
    assert gate.check_build(run) == []
    if defect == "memory":
        run["memory_peak_bytes"] = 7 * 1024**3
    elif defect == "partial":
        run["assets"].pop("wdpa-terrestrial")
    elif defect == "missing_bundle":
        run.pop("artifact_bundle")
    elif defect == "canonical":
        run["artifact_bundle"]["uri"] = (
            "gs://skytruth-shared-datasets-1/_catalog/build-bundle.json"
        )
    elif defect == "generation":
        run["artifact_bundle"]["generation"] = 0
    elif defect == "hash":
        run["artifact_bundle"]["sha256"] = "missing"
    else:
        run["assets"]["wdpa-terrestrial"]["india_rows"] -= 1
    assert gate.check_build(run)


def test_failed_build_cannot_commit_a_promotable_descriptor(built, tmp_path):
    store, _ref, report = built
    report["state"] = "failed"
    before = len(store.data)
    with pytest.raises(PublicationError, match="acceptance"):
        bundle.BuildStager(store, report["cloud_execution"]).commit(report, tmp_path)
    assert len(store.data) == before


def test_missing_sidecar_prevents_the_final_bundle_commit(built, tmp_path):
    store, _ref, report = built
    report["staged_assets"]["wdpa-terrestrial"]["artifacts"].pop("metadata_sw")
    before = len(store.data)
    with pytest.raises(PublicationError, match="roles"):
        bundle.BuildStager(store, report["cloud_execution"]).commit(report, tmp_path)
    assert len(store.data) == before


def test_generation_pinning_prevents_replacement(built, tmp_path):
    store, ref, _report = built
    ref["generation"] += 1
    with pytest.raises(AssertionError):
        bundle.load_bundle(store, ref, tmp_path / "load")
