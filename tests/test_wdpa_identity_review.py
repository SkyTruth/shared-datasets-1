"""Real disk allocation, pinned baseline reads, staging and offline report edges."""

from contextlib import closing
import copy
import datetime as dt
import io
import json
from pathlib import Path
import subprocess
import shutil
import sys
from types import SimpleNamespace
from unittest import mock

from google.api_core.exceptions import PreconditionFailed
import pytest

from ingestion.common import (
    decision_events,
    feature_metadata as metadata,
    publication as p,
)
from ingestion.common.identity_index import DiskIdentityPlan, DiskIdentityRecords
from ingestion.wdpa_monthly import identity_review as review, run as wdpa
from ingestion.wdpa_monthly.artifact_bundle import BuildStager
from scripts import release_feature_model as model
from scripts.wdpa_processing_gate import source_digest


class Storage:
    name = review.BUCKET

    def __init__(self, objects):
        self.objects = objects
        self.reads, self.writes = [], []

    def bucket(self, name):
        assert name == self.name
        return self

    def blob(self, name, generation=None):
        bucket = self

        class Blob:
            def __init__(self):
                self.name = name

            def open(self, mode, *, chunk_size, if_generation_match):
                assert mode == "rb"
                raw, current = bucket.objects[name]
                assert generation == if_generation_match
                if generation != current:
                    raise PreconditionFailed("frozen generation unavailable")
                bucket.reads.append((name, generation))
                return io.BytesIO(raw)

            def upload_from_filename(self, path, *, if_generation_match, checksum):
                assert if_generation_match == 0 and checksum == "crc32c"
                assert name.startswith("_scratch/wdpa-builds/wdpa-monthly-abcde/")
                if name in bucket.objects:
                    raise PreconditionFailed("review object already exists")
                self.generation = 1001
                bucket.objects[name] = (Path(path).read_bytes(), self.generation)
                bucket.writes.append(name)

        return Blob()


def feature(key, x, **properties):
    return {
        "geometry": {"type": "Point", "coordinates": [float(x), 0.0]},
        "properties": {"SITE_PID": key, **properties},
    }


def fixture(root, *, native=False, compared_values=(), asset_slug="wdpa-marine"):
    root.mkdir(exist_ok=True)
    slug, previous_release, release = asset_slug, "2026-10-01", "2026-11-01"
    previous = [
        feature("old-a", 1, NAME="Reserve A", NULL=None, FLAG=True),
        feature("old-b", 1, NAME="Reserve B"),
        feature("existing-key", 2, NAME="Key owner"),
    ]
    incoming = [
        feature("new-key", 1, NAME='<script>alert("unsafe")</script>', ADDED=None),
        feature("existing-key", 1, NAME="Key owner"),
        previous[0],
    ]
    for field, before, after in compared_values:
        previous[0]["properties"][field] = before
        incoming[0]["properties"][field] = after
    if native:
        for row in previous + incoming:
            if row["geometry"]["type"] == "Point":
                x, y = row["geometry"]["coordinates"]
                row["geometry"] = {
                    "type": "Polygon",
                    "coordinates": [[[x, y], [x + 0.2, y], [x + 0.2, y + 0.2], [x, y]]],
                }
    records = []
    for index, row in enumerate(previous, 1):
        geometry_hash, properties_hash = model.content_hashes(**row)
        records.append(
            {
                "schema_version": model.METADATA_SIDECAR_SCHEMA_VERSION,
                "asset_slug": slug,
                "release": previous_release,
                "feature_id": str(index),
                "geometry_hash": geometry_hash,
                "properties_hash": properties_hash,
                "properties": row["properties"],
                "identity_key": [row["properties"]["SITE_PID"]],
                "provenance": {},
            }
        )
    inputs, replay = root / "inputs", root / "replay"
    inputs.mkdir()
    replay.mkdir()
    sidecar = inputs / f"{slug}.metadata.ndjson.gz"
    model.write_metadata_sidecar(records, sidecar)
    canonical = root / "baseline.fgb"
    if native:
        source = root / "previous.geojsonseq"
        source.write_text(
            "".join(
                json.dumps(
                    {
                        "type": "Feature",
                        "geometry": row["geometry"],
                        "properties": {
                            **row["properties"],
                            **{
                                key: record[key]
                                for key in (
                                    "feature_id",
                                    "geometry_hash",
                                    "properties_hash",
                                )
                            },
                        },
                    }
                )
                + "\n"
                for row, record in zip(previous, records, strict=True)
            )
        )
        wdpa.convert_geojsonseq_to_fgb(source, wdpa.ASSETS[0], canonical)
    else:
        canonical.write_bytes(
            b"FGB HTTP boundary; native corpus supplies real stored geometry"
        )
    schema = metadata.SchemaAccumulator()
    for row in records:
        schema.observe(row)
    artifacts = []
    for generation, role in enumerate(model.REQUIRED_VECTOR_ARTIFACT_ROLES, 700):
        suffix = {
            "metadata": "metadata.ndjson.gz",
            "schema": "schema.json",
            "manifest": "manifest.json",
        }.get(role, role)
        local = sidecar if role == "metadata" else canonical
        item = {
            "role": role,
            "format": role,
            "path": f"{review.root(slug)}releases/{previous_release}/{slug}.{suffix}",
            "sha256": wdpa.sha256_file(local),
            "size": local.stat().st_size,
        }
        if role != "manifest":
            item["generation"] = generation
        artifacts.append(item)
    manifest = model.build_release_manifest(
        asset_slug=slug,
        release=previous_release,
        source_inputs=[],
        artifacts=artifacts,
        schema=schema.payload(asset_slug=slug, release=previous_release),
        identity=model.build_identity_metadata(
            strategy="generated_sequence_source_fields",
            source_fields=["SITE_PID"],
            contract_id=wdpa.CONTRACT_ID,
            next_generated_feature_id_before_release=1,
            next_generated_feature_id_after_release=4,
        ),
        validation={"feature_count": 3},
    )
    manifest_path = inputs / f"{slug}.manifest.json"
    manifest_path.write_bytes(p.canonical(manifest))
    snapshot = model.GeneratedIdentitySnapshot(
        f"{review.root(slug)}latest/{slug}.manifest.json",
        900,
        wdpa.sha256_file(manifest_path),
    )
    versions = model.validate_release_manifest(
        manifest,
        expected_asset_slug=slug,
        expected_release=previous_release,
        require_generations=True,
    )
    (inputs / "pins.json").write_text(
        json.dumps(
            {
                slug: {
                    "manifest_uri": snapshot.path,
                    "manifest_generation": snapshot.generation,
                    "manifest_sha256": snapshot.sha256,
                    "metadata": versions["metadata"],
                }
            }
        )
    )
    with closing(DiskIdentityRecords(root / "baseline.sqlite")) as baseline_records:
        for row in records:
            baseline_records.add(row)
        baseline = model.GeneratedIdentityBaseline(
            baseline_records.seal(),
            4,
            previous_release,
            snapshot,
            contract_id=wdpa.CONTRACT_ID,
        )
        with closing(DiskIdentityPlan(root / "plan.sqlite")) as plan:
            if native:
                from ingestion.wdpa_monthly.geometry_store import (
                    NormalizedGeometryStore,
                )

                store_context = closing(
                    NormalizedGeometryStore(
                        root / "incoming.gpkg", layer_name="incoming"
                    )
                )
            else:
                from contextlib import nullcontext

                store_context = nullcontext(
                    SimpleNamespace(
                        source_feature=lambda ordinal: incoming[ordinal - 1]
                    )
                )
            with store_context as store:
                for ordinal, row in enumerate(incoming, 1):
                    plan.add(ordinal, row, source_fields=("SITE_PID",))
                    if native:
                        store.add(row, ordinal)
                with mock.patch.object(decision_events, "publish", return_value=False):
                    with pytest.raises(metadata.IdentityDecisionRequired) as caught:
                        plan.resolve(
                            baseline=baseline, asset_slug=slug, release=release
                        )
                captured = review.capture(
                    plan,
                    store,
                    baseline,
                    asset_slug=slug,
                    release=release,
                    directory=replay,
                )
    source = root / "source.zip"
    source.write_bytes(b"verified upstream source")
    report = {
        "state": "blocked",
        "run_date": release,
        "source_sha256": wdpa.sha256_file(source),
        "source_tree_sha256": source_digest(),
        "sample_fraction": 1,
        "sample_seed": 7919,
        "native_versions": {"gdal_python": "3.6.2"},
    }
    (replay / "benchmark.json").write_text(json.dumps(report))
    fgb = versions["fgb"]
    storage = Storage(
        {
            fgb["path"].split(review.BUCKET + "/", 1)[1]: (
                canonical.read_bytes(),
                fgb["generation"],
            )
        }
    )
    producer = {
        "execution": "wdpa-monthly-abcde",
        "executor_sha": "a" * 40,
        "image": "us-central1-docker.pkg.dev/shared-datasets-1/jobs/wdpa:fixture",
        "image_digest": "sha256:" + "c" * 64,
    }
    return SimpleNamespace(
        root=root,
        inputs=inputs,
        replay=replay,
        captured=captured,
        blocked=caught.value,
        storage=storage,
        source=source,
        records=records,
        previous=previous,
        incoming=incoming,
        producer=producer,
    )


def complete(fx, target):
    return review.complete(
        fx.storage,
        inputs=fx.inputs,
        replay_directory=fx.replay,
        directory=target,
        blocked=fx.blocked,
        source=fx.source,
        source_url="https://source.example/month.zip",
        producer=fx.producer,
    )


@pytest.fixture
def example(tmp_path, monkeypatch):
    fx = fixture(tmp_path)

    def native_boundary(path, records):
        for feature_id, record in records.items():
            record["geometry"] = fx.previous[int(feature_id) - 1]["geometry"]

    monkeypatch.setattr(review, "previous_geometries", native_boundary)
    return fx


def test_complete_capture_retains_siblings_owner_and_source_and_renders_offline(
    example, tmp_path
):
    path = complete(example, tmp_path / "complete")
    bundle = review.read_json(path)
    assert review.validate_bundle(bundle) == bundle
    assert len(bundle["capture"]["cases"]) == 2
    assert set(bundle["previous_records"]) == {"1", "2", "3"}
    assert bundle["capture"]["previous_keys_present"] == ["1", "3"]
    assert (
        bundle["previous_records"]["1"]["geometry"]
        == bundle["previous_records"]["2"]["geometry"]
    )
    assert (
        bundle["previous_records"]["1"]["properties"]
        != bundle["previous_records"]["2"]["properties"]
    )
    new, existing = bundle["capture"]["cases"]
    assert new["previous_feature_id_for_key"] is None
    assert existing["previous_feature_id_for_key"] == "3"
    assert existing["ambiguity"]["matching_properties_feature_ids"] == ["3"]
    assert "eligible targets: 2" in review.legal_actions(bundle, new)
    assert (
        review.legal_actions(bundle, existing)
        == "keep_previous_key_mapping; assign_new_feature_id"
    )
    assert example.storage.reads == [
        (
            bundle["baseline"]["fgb"]["path"].split(review.BUCKET + "/", 1)[1],
            bundle["baseline"]["fgb"]["generation"],
        )
    ]
    staged = BuildStager(example.storage, example.producer["execution"]).upload(
        "wdpa-marine/identity-review.json", path
    )
    assert staged["sha256"] == wdpa.sha256_file(path) and staged["generation"] == 1001
    with pytest.raises(PreconditionFailed):
        BuildStager(example.storage, example.producer["execution"]).upload(
            "wdpa-marine/identity-review.json", path
        )
    assert example.storage.writes == [
        "_scratch/wdpa-builds/wdpa-monthly-abcde/wdpa-marine/identity-review.json"
    ]
    output = tmp_path / "report"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ingestion.wdpa_monthly.identity_review",
            str(path),
            "--out",
            str(output),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    html = (output / "report.html").read_text()
    assert "&lt;script&gt;" in html and "<script>" not in html
    assert "<em>absent</em>" in html and "null" in html and 'class="changed"' in html
    assert (
        "Full release not enumerated" not in html and "Complete release input" in html
    )
    assert "default-src" in html and "wdpa-monthly-abcde" in html
    assert "eligible targets: 2" in html and "Previous feature 3" in html
    footprints = json.loads((output / "footprints.geojson").read_text())
    assert len(footprints["features"]) == 5
    assert {row["properties"]["side"] for row in footprints["features"]} == {
        "incoming",
        "previous",
    }
    with pytest.raises(FileExistsError):
        review.render_report(bundle, output)


def test_report_highlights_json_type_changes(tmp_path, monkeypatch):
    fx = fixture(
        tmp_path,
        compared_values=(
            ("NUMBER_KIND", 1, 1.0),
            ("BOOLEAN_KIND", True, 1),
            ("NESTED_KIND", {"values": [1, False]}, {"values": [1.0, 0]}),
            ("SAME_OBJECT", {"a": 1, "b": 2}, {"b": 2, "a": 1}),
        ),
    )

    def native_boundary(path, records):
        for feature_id, record in records.items():
            record["geometry"] = fx.previous[int(feature_id) - 1]["geometry"]

    monkeypatch.setattr(review, "previous_geometries", native_boundary)
    bundle = complete(fx, tmp_path / "complete")
    output = tmp_path / "report"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ingestion.wdpa_monthly.identity_review",
            str(bundle),
            "--out",
            str(output),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    html = (output / "report.html").read_text()
    comparison = html.split("<h3>Previous feature 1</h3>", 1)[1].split("<h3>", 1)[0]
    for field in ("NUMBER_KIND", "BOOLEAN_KIND", "NESTED_KIND"):
        assert f'<tr class="changed"><th>{field}</th>' in comparison
    assert '<tr class=""><th>SAME_OBJECT</th>' in comparison


@pytest.mark.parametrize(
    "defect",
    [
        "geometry",
        "properties",
        "owner",
        "missing",
        "extra",
        "case_id",
        "snapshot",
        "generation",
        "source",
        "sample",
        "schema",
        "matches",
    ],
)
def test_bundle_reader_rejects_inconsistent_evidence(example, tmp_path, defect):
    bundle = review.read_json(complete(example, tmp_path / "complete"))
    if defect == "geometry":
        bundle["previous_records"]["1"]["geometry"]["coordinates"][0] = 10
    if defect == "properties":
        bundle["capture"]["cases"][0]["incoming"]["properties"]["NAME"] = "changed"
    if defect == "owner":
        bundle["capture"]["cases"][0]["previous_feature_id_for_key"] = "3"
    if defect == "missing":
        del bundle["previous_records"]["2"]
    if defect == "extra":
        bundle["previous_records"]["99"] = copy.deepcopy(
            bundle["previous_records"]["2"]
        )
    if defect == "case_id":
        bundle["capture"]["cases"][0]["case_id"] = "0" * 64
    if defect == "snapshot":
        bundle["capture"]["baseline_snapshot"]["path"] = bundle["capture"][
            "baseline_snapshot"
        ]["path"].replace("wdpa-marine", "wdpa-terrestrial")
    if defect == "generation":
        bundle["baseline"]["fgb"]["generation"] = 0
    if defect == "source":
        bundle["source"]["sha256"] = "not-a-hash"
    if defect == "sample":
        bundle["sample_fraction"] = True
    if defect == "schema":
        bundle["schema"] = "future-version"
    if defect == "matches":
        bundle["capture"]["cases"][0]["ambiguity"][
            "matching_geometry_properties_hashes"
        ] = []
    with pytest.raises((p.PublicationError, ValueError)):
        review.render_report(bundle, tmp_path / "report")
    assert not (tmp_path / "report").exists()


@pytest.mark.parametrize(
    "defect",
    [
        "sidecar",
        "fgb",
        "generation",
        "capture",
        "source",
        "code",
        "omitted_match",
        "wrong_owner",
    ],
)
def test_completion_rejects_unverified_inputs(example, tmp_path, defect):
    if defect == "sidecar":
        (example.inputs / "wdpa-marine.metadata.ndjson.gz").write_bytes(b"changed")
    if defect == "fgb":
        name, (raw, generation) = next(iter(example.storage.objects.items()))
        example.storage.objects[name] = (raw + b"changed", generation)
    if defect == "generation":
        name, (raw, generation) = next(iter(example.storage.objects.items()))
        example.storage.objects[name] = (raw, generation + 1)
    if defect == "capture":
        review.capture_path(example.replay, "wdpa-marine").unlink()
    if defect == "source":
        example.source.write_bytes(b"different source")
    if defect == "code":
        path = example.replay / "benchmark.json"
        report = review.read_json(path)
        report["source_tree_sha256"] = "0" * 64
        path.write_text(json.dumps(report))
    if defect in {"omitted_match", "wrong_owner"}:
        captured = copy.deepcopy(example.captured)
        item = captured["cases"][0]
        if defect == "omitted_match":
            item["ambiguity"]["matching_geometry_feature_ids"] = ["1"]
            item["ambiguity"]["matching_geometry_properties_hashes"] = [
                example.records[0]["properties_hash"]
            ]
        else:
            item["previous_feature_id_for_key"] = "3"
        item["case_id"] = decision_events.case_key(
            "wdpa-marine", "2026-11-01", item["ambiguity"]
        )
        review.capture_path(example.replay, "wdpa-marine").write_text(
            json.dumps(captured)
        )
    with pytest.raises(
        (FileNotFoundError, p.PublicationError, RuntimeError, PreconditionFailed)
    ):
        complete(example, tmp_path / "complete")
    assert example.storage.writes == []


def test_limits_and_sample_scope_are_explicit(example, tmp_path, monkeypatch):
    bundle = review.read_json(complete(example, tmp_path / "complete"))
    bundle["sample_fraction"] = 0.001
    report = review.render_report(bundle, tmp_path / "sample")
    assert "full release not enumerated" in report.read_text()
    with monkeypatch.context() as limited:
        limited.setattr(review, "MAX_REPORT_BYTES", 10)
        with pytest.raises(p.PublicationError, match="HTML byte limit"):
            review.render_report(bundle, tmp_path / "too-large")
        assert not (tmp_path / "too-large").exists()
    monkeypatch.setattr(review, "MAX_CASES", 1)
    with pytest.raises(p.PublicationError, match="case limit"):
        review.validate_bundle(bundle)
    monkeypatch.setattr(review, "MAX_JSON_BYTES", 10)
    with pytest.raises(p.PublicationError, match="byte limit"):
        review.read_json(report)


@pytest.mark.parametrize("case_count,accepted", [(1323, True), (2049, False)])
def test_full_release_review_capacity(tmp_path, monkeypatch, case_count, accepted):
    fx = fixture(tmp_path / "inputs", asset_slug="wdpa-terrestrial")
    slug = "wdpa-terrestrial"
    incoming = [
        feature(f"large-{index}", 1, NAME=f"Incoming case {index:04}")
        for index in range(case_count)
    ]
    replay = tmp_path / "large-replay"
    replay.mkdir()
    shutil.copyfile(fx.replay / "benchmark.json", replay / "benchmark.json")

    def native_boundary(path, records):
        for feature_id, record in records.items():
            record["geometry"] = fx.previous[int(feature_id) - 1]["geometry"]

    monkeypatch.setattr(review, "previous_geometries", native_boundary)
    with closing(DiskIdentityRecords(tmp_path / "large-baseline.sqlite")) as records:
        for row in fx.records:
            records.add(row)
        baseline = model.GeneratedIdentityBaseline(
            records.seal(),
            4,
            "2026-10-01",
            model.GeneratedIdentitySnapshot(**fx.captured["baseline_snapshot"]),
            contract_id=wdpa.CONTRACT_ID,
        )
        with closing(DiskIdentityPlan(tmp_path / "large-plan.sqlite")) as plan:
            for ordinal, row in enumerate(incoming, 1):
                plan.add(ordinal, row, source_fields=("SITE_PID",))
            with mock.patch.object(decision_events, "publish", return_value=False):
                with pytest.raises(metadata.IdentityDecisionRequired) as caught:
                    plan.resolve(
                        baseline=baseline, asset_slug=slug, release="2026-11-01"
                    )
            store = SimpleNamespace(
                source_feature=lambda ordinal: incoming[ordinal - 1]
            )
            if not accepted:
                with pytest.raises(p.PublicationError, match="case limit"):
                    review.capture(
                        plan,
                        store,
                        baseline,
                        asset_slug=slug,
                        release="2026-11-01",
                        directory=replay,
                    )
                assert not review.capture_path(replay, slug).exists()
                return
            review.capture(
                plan,
                store,
                baseline,
                asset_slug=slug,
                release="2026-11-01",
                directory=replay,
            )

    path = review.complete(
        fx.storage,
        inputs=fx.inputs,
        replay_directory=replay,
        directory=tmp_path / "complete-large",
        blocked=caught.value,
        source=fx.source,
        source_url="https://source.example/month.zip",
        producer=fx.producer,
    )
    bundle = review.read_json(path)
    assert review.validate_bundle(bundle) == bundle
    assert len(bundle["capture"]["cases"]) == case_count
    assert {
        item["incoming"]["properties"]["SITE_PID"]
        for item in bundle["capture"]["cases"]
    } == {row["properties"]["SITE_PID"] for row in incoming}
    staged = BuildStager(fx.storage, fx.producer["execution"]).upload(
        "wdpa-terrestrial/identity-review.json", path
    )
    assert staged["sha256"] == wdpa.sha256_file(path)
    raw, generation = fx.storage.objects[
        "_scratch/wdpa-builds/wdpa-monthly-abcde/wdpa-terrestrial/identity-review.json"
    ]
    assert generation == staged["generation"]
    assert json.loads(raw)["capture"]["cases"] == bundle["capture"]["cases"]
    document = review.render_report(
        review.read_json(path), tmp_path / "large-report"
    ).read_text()
    assert all(row["properties"]["NAME"] in document for row in incoming)


def test_single_properties_match_keeps_its_old_footprint(example, tmp_path):
    bundle = review.read_json(complete(example, tmp_path / "complete"))
    before = bundle["previous_records"]["1"]
    incoming = {
        "source_row_number": 1,
        "properties": copy.deepcopy(before["properties"]),
        "geometry": {"type": "Point", "coordinates": [9.0, 0.0]},
    }
    geometry_hash, properties_hash = model.content_hashes(
        geometry=incoming["geometry"], properties=incoming["properties"]
    )
    evidence = {
        "new_identity_key": before["identity_key"],
        "new_geometry_hash": geometry_hash,
        "new_properties_hash": properties_hash,
        "matching_geometry_feature_ids": [],
        "matching_properties_feature_ids": ["1"],
        "matching_geometry_properties_hashes": [],
        "matching_properties_geometry_hashes": [before["geometry_hash"]],
    }
    bundle["capture"]["cases"] = [
        {
            "case_id": decision_events.case_key("wdpa-marine", "2026-11-01", evidence),
            "ambiguity": evidence,
            "incoming": incoming,
            "previous_feature_id_for_key": "1",
        }
    ]
    bundle["capture"]["previous_keys_present"] = ["1"]
    bundle["previous_records"] = {"1": before}
    assert review.validate_bundle(bundle) == bundle
    html = review.render_report(bundle, tmp_path / "report").read_text()
    assert "Stored footprint: changed" in html and "keep_previous_key_mapping" in html
    bundle["capture"]["cases"] = []
    with pytest.raises(p.PublicationError, match="empty"):
        review.validate_bundle(bundle)


def native_review_pipeline(root):
    """Called by the existing required native corpus; never a new optional skip."""
    fx = fixture(root, native=True)
    target = complete(fx, root / "complete")
    bundle = review.read_json(target)
    assert set(bundle["previous_records"]) == {"1", "2", "3"}
    assert review.validate_bundle(bundle) == bundle
    assert review.render_report(bundle, root / "report").is_file()
    # Exercise the real producer's exception boundary while its normalized
    # geometry and allocation transactions are still open.
    from scripts.local_wdpa_sample import load_baseline

    native_dir = root / "producer"
    native_dir.mkdir()
    incoming_source = root / "incoming.geojsonseq"
    incoming_source.write_text(
        "".join(json.dumps({"type": "Feature", **row}) + "\n" for row in fx.incoming)
    )
    source_fields = wdpa.layer_fields(incoming_source)
    layer = wdpa.SourceLayer(
        "incoming", source_fields, "GEOMETRY", str(incoming_source)
    )
    baseline = load_baseline(fx.inputs, wdpa.ASSETS[0], native_dir)
    with (
        closing(baseline.records),
        mock.patch.object(decision_events, "publish", return_value=False),
    ):
        with pytest.raises(metadata.IdentityDecisionRequired):
            wdpa.build_asset_outputs(
                source=str(incoming_source),
                source_layers=[layer],
                source_fields=source_fields,
                asset=wdpa.ASSETS[0],
                where="1=1",
                workdir=native_dir,
                run_date=dt.date(2026, 11, 1),
                baseline=baseline,
                translation_memory=object(),
                on_identity_review=lambda plan, store: review.capture(
                    plan,
                    store,
                    baseline,
                    asset_slug="wdpa-marine",
                    release="2026-11-01",
                    directory=native_dir,
                ),
            )
    assert (
        len(review.read_json(review.capture_path(native_dir, "wdpa-marine"))["cases"])
        == 2
    )
    assert not list(native_dir.glob("*.fgb")) and not list(native_dir.glob("*.pmtiles"))
    return fx, bundle


@pytest.mark.parametrize("phase", ["sample", "complete"])
@pytest.mark.parametrize("missing_capture", [False, True])
def test_monthly_parent_stages_review_and_preserves_pause_without_publication(
    example, tmp_path, monkeypatch, capsys, phase, missing_capture
):
    from contextlib import nullcontext
    from ingestion.wdpa_monthly import build

    monkeypatch.setenv("CLOUD_RUN_EXECUTION", example.producer["execution"])
    monkeypatch.setenv("CLOUD_RUN_JOB", "wdpa-monthly")
    import base64
    original_publish = decision_events.publish
    session = mock.Mock()
    session.post.return_value.json.return_value = {"messageIds": ["confirmed-review"]}
    monkeypatch.setattr(decision_events, "publish", lambda **kwargs: original_publish(**kwargs, session=session))
    monkeypatch.setenv("WDPA_BUILD_IMAGE", example.producer["image"])
    monkeypatch.setenv(
        "WDPA_BUILD_IMAGE_CONFIG_DIGEST", example.producer["image_digest"]
    )
    monkeypatch.setenv("SHARED_DATASETS_EXECUTOR_SHA", example.producer["executor_sha"])
    monkeypatch.setattr(build, "prepare_scratch", lambda: None)
    monkeypatch.setattr(build, "cgroup_limits", lambda: (4, 8 * 1024**3))
    monkeypatch.setattr(build, "cgroup_memory", lambda: ({}, 5 * 1024**3))
    monkeypatch.setattr(wdpa, "native_versions", lambda: {"gdal_python": "3.6.2"})
    monkeypatch.setattr(
        build,
        "PhaseProfiler",
        lambda *args, **kwargs: SimpleNamespace(
            records=[], phase=lambda _: nullcontext()
        ),
    )
    monkeypatch.setattr(
        build,
        "freeze_inputs",
        lambda client, destination: shutil.copytree(example.inputs, destination),
    )
    monkeypatch.setattr(
        wdpa,
        "download_file",
        lambda url, path: path.write_bytes(example.source.read_bytes()),
    )
    compatibility = mock.Mock()
    monkeypatch.setattr(build.gate, "check_compatibility", compatibility)

    def replay(source, inputs, directory, release, *, sample):
        if directory.name != phase:
            return {"state": "succeeded"}
        directory.mkdir()
        payload = review.read_json(example.replay / "benchmark.json")
        payload["sample_fraction"] = 0.001 if sample else 1
        (directory / "benchmark.json").write_text(json.dumps(payload))
        if not missing_capture:
            shutil.copyfile(
                review.capture_path(example.replay, "wdpa-marine"),
                review.capture_path(directory, "wdpa-marine"),
            )
        raise example.blocked

    monkeypatch.setattr(build, "replay", replay)
    with pytest.raises(
        FileNotFoundError if missing_capture else metadata.IdentityDecisionRequired
    ) as stopped:
        build.build_month(example.storage, dt.date(2026, 11, 1), tmp_path / "monthly")
    compatibility.assert_not_called()
    if missing_capture:
        assert example.storage.writes == []
        session.post.assert_not_called()
    else:
        assert stopped.value is example.blocked
        assert example.storage.writes == [
            "_scratch/wdpa-builds/wdpa-monthly-abcde/wdpa-marine/identity-review.json"
        ]
        raw, _ = example.storage.objects[example.storage.writes[0]]
        bundle = review.validate_bundle(json.loads(raw))
        assert bundle["sample_fraction"] == (0.001 if phase == "sample" else 1)
        event = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
        assert (
            event["event"] == "wdpa_identity_review"
            and event["review_bundle"]["generation"] == 1001
        )
        assert event["awaiting_decisions"] == 2
        queued = json.loads(base64.b64decode(session.post.call_args.kwargs["json"]["messages"][0]["data"]))
        assert decision_events.event(queued) == queued
        assert queued["schema"] == decision_events.REVIEW_SCHEMA
        assert queued["review_bundle"] == event["review_bundle"]
        assert queued["cases"] == sorted([item["ambiguity"] for item in bundle["capture"]["cases"]], key=decision_events.canonical)
