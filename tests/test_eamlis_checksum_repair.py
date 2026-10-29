"""Legacy producer records -> approved serialization -> CAS repair -> strict index."""

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest import mock

import pytest
from google.api_core.exceptions import PreconditionFailed

from ingestion.common import release_index
from ingestion.eamlis_monthly import (
    checksum_repair as repair,
    identity as ids,
    source_quality as quality,
)
from scripts import publish_workflow, reviewed_dataset_plan as plans, gcs_asset
from tests.test_eamlis_identity_transition import prepared, install, run, rows
from tests.test_source_loss import copy_transport


@pytest.fixture
def example(tmp_path):
    client, installation, _ = prepared(tmp_path)
    install(client, installation, tmp_path)
    run(client, rows(), "2026-05-02")
    bucket = client.bucket(quality.BUCKET)
    record = bucket.blob(f"{quality.ROOT}/runs/2026-05-01.json")
    original = json.loads(record.download_as_bytes())
    manifest = bucket.blob(
        f"{quality.ROOT}/releases/2026-05-01/{quality.SLUG}.manifest.json"
    )
    digest = quality.digest(manifest.download_as_bytes())
    original["sha256"]["manifest"] = "1" * 64
    for entry in original["release_paths"]:
        if entry["path"].endswith(".manifest.json"):
            entry["sha256"] = digest
    record.upload_from_string(
        quality.encode(original),
        content_type="application/json",
        if_generation_match=record.generation,
    )
    manifest.metadata = {
        **(manifest.metadata or {}),
        "sha256": "2" * 64,
        "retained": "label",
    }
    manifest.metageneration = 3

    def patch(*, if_generation_match, if_metageneration_match):
        if (
            manifest.generation != if_generation_match
            or manifest.metageneration != if_metageneration_match
        ):
            raise PreconditionFailed("manifest CAS")
        manifest.metageneration += 1

    manifest.patch = mock.Mock(side_effect=patch)
    index = release_index.load_release_index(bucket, quality.SLUG)
    csv_blob = bucket.blob(f"{quality.ROOT}/releases/2026-05-01/legacy.csv")
    csv_blob.upload_from_string(b"keep,this,checksum\n", if_generation_match=0)
    for release in index.payload["releases"]:
        if release["date"] == "2026-05-01":
            for entry in release["files"]:
                if entry["path"].endswith(".manifest.json"):
                    entry["sha256"] = "2" * 64
            release["files"].append(
                {
                    "path": repair.URI + csv_blob.name,
                    "format": "csv",
                    "generation": csv_blob.generation,
                    "size": csv_blob.size,
                    "sha256": quality.digest(csv_blob.download_as_bytes()),
                }
            )
            release["files"].sort(key=lambda entry: (entry["format"], entry["path"]))
    release_index.write_release_index(
        bucket, quality.SLUG, index.payload, generation=index.generation
    )
    corrected = deepcopy(original)
    corrected["sha256"]["manifest"] = digest
    source = bucket.blob(f"_scratch/pending-publishes/{quality.SLUG}/repair/run.json")
    source.upload_from_string(
        quality.encode(corrected),
        content_type="application/json",
        if_generation_match=0,
    )
    source.cache_control = "no-cache, max-age=0, must-revalidate"
    plan = {
        "asset_slug": quality.SLUG,
        "proposal_id": "repair",
        "breaking_changes": [],
        "release_index_asset_slugs": [quality.SLUG],
        "promotions": [
            {
                "source_uri": repair.URI + source.name,
                "source_generation": str(source.generation),
                "destination_uri": repair.URI + record.name,
                "destination_generation": str(record.generation),
                "content_type": "application/json",
                "cache_control": source.cache_control,
            }
        ],
        repair.KEY: {
            "schema_version": 1,
            "release": "2026-05-01",
            "baseline_token": quality.baseline(bucket)["token"],
            "original_record": original,
            "original_record_sha256": quality.digest(record.download_as_bytes()),
            "manifest": {
                "path": repair.URI + manifest.name,
                "generation": str(manifest.generation),
                "metageneration": str(manifest.metageneration),
                "size": manifest.size,
                "sha256": digest,
                "metadata": deepcopy(manifest.metadata),
                "content_type": manifest.content_type,
                "cache_control": manifest.cache_control,
            },
        },
    }
    plan = plans.normalize_publish_plan(plan)
    row = {
        "asset_slug": quality.SLUG,
        "canonical_path": f"{repair.URI}{quality.ROOT}/latest/{quality.SLUG}.fgb",
        "canonical_format": "fgb",
    }
    path = tmp_path / "plan.json"
    path.write_bytes(plans.canonical_bytes(plan))
    return client, plan, path, row, manifest, record, source


def contexts(client, row):
    return (
        mock.patch.object(gcs_asset, "get_client", return_value=client),
        mock.patch.object(publish_workflow, "catalog_row", return_value=row),
        mock.patch.object(
            quality, "protected_authority", return_value="same-reviewed-checksum-repair"
        ),
        mock.patch.dict(os.environ, {"SHARED_DATASETS_BUCKET": quality.BUCKET}),
    )


def test_standalone_plan_cli_renders_without_site_packages_or_pythonpath(
    example, tmp_path
):
    _, _, path, _, _, _, _ = example
    script = Path(plans.__file__).resolve()
    destination = tmp_path / "fresh-repo"
    destination.mkdir()
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    result = subprocess.run(
        [
            sys.executable,
            "-S",
            str(script),
            "prepare",
            "--publish",
            str(path),
            "--repo-root",
            str(destination),
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    documents = list((destination / ".github/dataset-plans").rglob("*.json"))
    assert len(documents) == 1
    document = plans.read_document(
        documents[0].read_bytes(), path=documents[0].relative_to(destination).as_posix()
    )
    assert document["publish"][repair.KEY]["release"] == "2026-05-01"
    assert "shared-datasets-publish-plan" in result.stdout


@pytest.mark.parametrize("extra_target", [False, True])
def test_authorization_preview_uses_stdlib_and_keeps_scope_validation(
    example, tmp_path, extra_target
):
    from tests.test_dataset_mutation_authorization import FakeGitHub, REPO

    _, plan, _, _, _, _, _ = example
    document = plans.normalize_document(
        {
            "plan_version": 1,
            "finalization_version": plans.FINALIZATION_VERSION,
            "publish": plan,
        }
    )
    body = plans.render_document(document)
    if extra_target:
        document["publish"]["release_index_asset_slugs"].append("unrelated")
    raw = plans.canonical_bytes(document)
    blob = hashlib.sha1(f"blob {len(raw)}\0".encode() + raw).hexdigest()
    path = f".github/dataset-plans/{quality.SLUG}/repair/{plans.sha256(raw)}.json"
    api = FakeGitHub()
    api.pr.update(state="open", merged=False, merged_at=None, body=body)
    api.pr["base"]["sha"] = "b" * 40
    fixture = tmp_path / "preview-fixture.json"
    fixture.write_text(
        json.dumps(
            {
                "pr": api.pr,
                "files": [{"filename": path, "status": "added", "sha": blob}],
                "tree": {
                    "truncated": False,
                    "tree": [
                        {"path": path, "type": "blob", "mode": "100644", "sha": blob}
                    ],
                },
                "blob": blob,
                "raw": raw.decode(),
                "event": {"repository": REPO, "pull_request": api.pr},
            }
        )
    )
    child = r"""
import importlib.util, json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
assert importlib.util.find_spec('google') is None
from scripts import dataset_mutation_authorization as auth
from tests.test_dataset_mutation_authorization import FakeGitHub
value = json.loads(Path(sys.argv[2]).read_text())
api = FakeGitHub()
for name in ('pr', 'files', 'tree', 'blob'):
    setattr(api, name, value[name])
api.raw = value['raw'].encode()
auth.GitHub = lambda: api  # Only external GitHub transport is substituted.
Path('event.json').write_text(json.dumps(value['event']))
raise SystemExit(auth.main(['preview', '--event-path', 'event.json',
    '--github-output', 'outputs', '--directory', 'authorization']))
"""
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    result = subprocess.run(
        [
            sys.executable,
            "-S",
            "-c",
            child,
            str(Path(plans.__file__).resolve().parents[1]),
            str(fixture),
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )
    assert not (tmp_path / "authorization").exists()
    if extra_target:
        assert result.returncode == 2, result.stderr
        assert "checksum repair permits only one historical EAMLIS run" in result.stderr
        assert not (tmp_path / "publish-plan.json").exists()
        assert not (tmp_path / "outputs").exists()
    else:
        assert result.returncode == 0, result.stderr
        assert json.loads((tmp_path / "publish-plan.json").read_bytes()) == plan
        assert "has_publish_plan=true" in (tmp_path / "outputs").read_text()


def test_real_workflow_repair_retries_without_recopy_and_preserves_manifest_generation(
    example,
):
    client, plan, path, row, manifest, record, _ = example
    bucket = client.bucket(quality.BUCKET)
    baseline = quality.baseline(bucket)["release"]
    identity = deepcopy(quality.Guard(bucket).load()[0]["identity"])
    before = manifest.download_as_bytes(), manifest.generation
    with pytest.raises(release_index.ReleaseIndexError, match="conflicting SHA"):
        release_index.rebuild_index_from_bucket(bucket, row)
    a, b, c, d = contexts(client, row)
    transport, copied = copy_transport(client)
    with (
        a,
        b,
        c,
        d,
        mock.patch.object(publish_workflow.subprocess, "run", side_effect=transport),
    ):
        assert publish_workflow.main(["promote", "--plan-json", str(path)]) == 0
        assert (
            quality.Guard(bucket).load()[0]["owner"] == "same-reviewed-checksum-repair"
        )
        assert publish_workflow.main(["promote", "--plan-json", str(path)]) == 0
        assert len(copied) == 1
        assert (
            publish_workflow.main(["rebuild-release-index", "--plan-json", str(path)])
            == 0
        )
        assert (
            publish_workflow.main(["rebuild-release-index", "--plan-json", str(path)])
            == 0
        )
        quality.finish_promotion(client, plan)
    assert (manifest.download_as_bytes(), manifest.generation) == before
    assert manifest.patch.call_count == 1
    manifest.patch.assert_called_once_with(
        if_generation_match=int(plan[repair.KEY]["manifest"]["generation"]),
        if_metageneration_match=int(plan[repair.KEY]["manifest"]["metageneration"]),
    )
    assert json.loads(record.download_as_bytes()) == repair.corrected_record(plan)
    assert quality.baseline(bucket)["release"] == baseline
    state = quality.Guard(bucket).load()[0]
    assert state["owner"] is None and state["identity"] == identity
    rebuilt = release_index.rebuild_index_from_bucket(bucket, row)
    current = release_index.load_release_index(bucket, quality.SLUG).payload
    assert rebuilt["latest_release"] == current["latest_release"]
    assert rebuilt["latest_run"] == current["latest_run"]
    csv_ref = next(
        entry
        for release in current["releases"]
        for entry in release["files"]
        if entry["path"].endswith("/legacy.csv")
    )
    assert csv_ref["sha256"] == quality.digest(b"keep,this,checksum\n")


def test_interruption_after_metadata_patch_retains_owner_and_resumes(example):
    client, plan, path, row, manifest, record, _ = example
    original = record.download_as_bytes()
    a, b, c, d = contexts(client, row)
    transport, copied = copy_transport(client)
    with a, b, c, d:

        def interrupted(command, **kwargs):
            if "copy" in command:
                raise RuntimeError("interrupted before record copy")
            return transport(command, **kwargs)

        with mock.patch.object(
            publish_workflow.subprocess, "run", side_effect=interrupted
        ):
            with pytest.raises(RuntimeError, match="interrupted before"):
                publish_workflow.main(["promote", "--plan-json", str(path)])
        assert record.download_as_bytes() == original
        assert (
            quality.Guard(client.bucket(quality.BUCKET)).load()[0]["owner"]
            == "same-reviewed-checksum-repair"
        )
        with mock.patch.object(
            publish_workflow.subprocess, "run", side_effect=transport
        ):
            publish_workflow.main(["promote", "--plan-json", str(path)])
        publish_workflow.main(["rebuild-release-index", "--plan-json", str(path)])
        quality.finish_promotion(client, plan)
    assert len(copied) == 1 and manifest.patch.call_count == 1


@pytest.mark.parametrize("change", ["unrelated_index", "unverified_checksum"])
def test_index_repair_rejects_unrelated_changes_before_writing(example, change):
    client, plan, path, row, _, _, _ = example
    bucket = client.bucket(quality.BUCKET)
    a, b, c, d = contexts(client, row)
    transport, _ = copy_transport(client)
    with (
        a,
        b,
        c,
        d,
        mock.patch.object(publish_workflow.subprocess, "run", side_effect=transport),
    ):
        publish_workflow.main(["promote", "--plan-json", str(path)])
        index = release_index.load_release_index(bucket, quality.SLUG)
        old = next(
            release
            for release in index.payload["releases"]
            if release["date"] == "2026-05-01"
        )
        if change == "unrelated_index":
            old["rows"] += 1
        else:
            next(
                entry for entry in old["files"] if entry["path"].endswith("/legacy.csv")
            )["sha256"] = "0" * 64
        release_index.write_release_index(
            bucket, quality.SLUG, index.payload, generation=index.generation
        )
        before = quality.read(bucket, quality.INDEX)
        with pytest.raises((ids.IdentityError, quality.SourceLossError)):
            publish_workflow.main(["rebuild-release-index", "--plan-json", str(path)])
        assert quality.read(bucket, quality.INDEX) == before
        assert (
            quality.Guard(bucket).load()[0]["owner"] == "same-reviewed-checksum-repair"
        )


@pytest.mark.parametrize(
    "change",
    [
        "source",
        "typed_source",
        "record",
        "manifest",
        "metadata",
        "metageneration",
        "baseline",
        "hold",
        "phase",
    ],
)
def test_changed_evidence_is_rejected_before_manifest_patch(example, change):
    client, plan, _, _, manifest, record, source = example
    bucket = client.bucket(quality.BUCKET)
    if change in {"source", "typed_source"}:
        value = json.loads(source.download_as_bytes())
        if change == "source":
            value["row_count"] += 1
        else:
            value["schema_version"] = True
        source.data = quality.encode(value)
    elif change == "record":
        record.upload_from_string(b"changed", if_generation_match=record.generation)
    elif change == "manifest":
        manifest.data += b" "
    elif change == "metadata":
        manifest.metadata["retained"] = "changed"
    elif change == "metageneration":
        manifest.metageneration += 1
    elif change == "baseline":
        plan[repair.KEY]["baseline_token"] = "3" * 64
    else:
        guard = quality.Guard(bucket)
        guard.state, guard.generation = guard.load()
        guard.before = quality.baseline(bucket)
        if change == "phase":
            guard.state["identity"]["phase"] = "prepared"
            guard.state["identity"]["activation"] = None
        else:
            guard.state["hold"] = {"id": "held"}
        with pytest.raises(ids.IdentityError):
            repair.validate_live(guard, plan)
        return
    with mock.patch.object(
        quality, "protected_authority", return_value="same-reviewed-checksum-repair"
    ):
        with pytest.raises(
            (ids.IdentityError, quality.SourceLossError, PreconditionFailed)
        ):
            quality.begin_promotion(client, plan)
    manifest.patch.assert_not_called()
    assert quality.Guard(bucket).load()[0]["owner"] is None


@pytest.mark.parametrize(
    "change", ["target", "generation", "index", "mixed", "unknown", "empty", "multiple"]
)
def test_plan_contract_rejects_expanded_authority(example, change):
    _, plan, _, _, _, _, _ = example
    if change == "target":
        plan["promotions"][0]["destination_uri"] = (
            repair.URI + quality.ROOT + "/README.md"
        )
    elif change == "generation":
        plan["promotions"][0]["destination_generation"] = ""
    elif change == "index":
        plan["release_index_asset_slugs"].append("other-asset")
    elif change == "mixed":
        plan["source_loss_review"] = {}
    elif change == "empty":
        plan["promotions"] = []
    elif change == "multiple":
        plan["promotions"].append(
            {
                **plan["promotions"][0],
                "destination_uri": repair.URI + quality.ROOT + "/README.md",
            }
        )
    else:
        plan[repair.KEY]["force"] = True
    with pytest.raises(plans.PlanValidationError):
        plans.normalize_publish_plan(plan)
