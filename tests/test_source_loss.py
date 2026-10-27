"""Producer -> immutable candidate -> preview -> reviewed publication regression."""
import copy
import datetime as dt
import json
from pathlib import Path
import subprocess
from unittest import mock

import pytest
from google.api_core.exceptions import PreconditionFailed

from ingestion.common import feature_metadata
from ingestion.eamlis_monthly import source_quality as loss
from ingestion.common.gcs import GcsPublisher
from ingestion.eamlis_monthly import run as eamlis
from scripts import catalog_site, compare_releases, eamlis_source_loss_review as review
from scripts import finalize_promoted_release_metadata as finalizer
from scripts import publish_workflow, reviewed_dataset_plan, slack_notify
from scripts import gcs_asset
from tests.test_eamlis_monthly import FakeBucket, sample_source_state, fixture_download


class Client:
    def __init__(self):
        self.buckets = {}

    def bucket(self, name):
        if name not in self.buckets:
            b = FakeBucket()
            b.name = name
            self.buckets[name] = b
        return self.buckets[name]

    def list_blobs(self, name, *, prefix):
        return self.bucket(name).list_blobs(prefix=prefix)


def seed_baseline(bucket, count=100):
    loss.put(bucket, loss.INDEX, loss.encode({"schema_version": 1, "asset_slug": loss.SLUG,
        "latest_release": {"date": "2026-04-02", "rows": count, "files": []}, "releases": []}), generation=0)


@pytest.mark.parametrize("count,blocked", [(101, False), (100, False), (96, False), (95, False), (94, True), (0, True)])
def test_strict_five_percent_boundary_is_persisted(count, blocked):
    client = Client()
    bucket = client.bucket(loss.BUCKET)
    seed_baseline(bucket)
    with loss.Guard(bucket) as gate:
        assert gate.assess(count, selection={"where": "same"}) is blocked
        if blocked:
            with pytest.raises(loss.SourceLossError, match="forbids"):
                gate.check_publication()
        else:
            gate.check_publication()
    with loss.Guard(bucket) as next_run:
        assert next_run.held is blocked
        # A healthy subsequent download never clears a hold automatically.
        assert next_run.assess(100, selection={"where": "same"}) is blocked


def test_baseline_is_published_release_not_latest_attempt():
    bucket = Client().bucket(loss.BUCKET)
    seed_baseline(bucket)
    payload, ref = loss.read(bucket, loss.INDEX)
    data = json.loads(payload)
    data["latest_run"] = {"status": "failed", "row_count": 40}
    loss.put(bucket, loss.INDEX, loss.encode(data), generation=int(ref["generation"]))
    with loss.Guard(bucket) as gate:
        assert gate.assess(94, selection={})
        assert gate.state["hold"]["baseline"]["release"]["rows"] == 100


def test_selection_change_and_baseline_race_block_publication():
    bucket = Client().bucket(loss.BUCKET)
    seed_baseline(bucket)
    with loss.Guard(bucket) as gate:
        assert gate.assess(100, selection={"where": "new"}, previous_selection={"where": "old"})
        assert gate.state["hold"]["reason"] == "source_selection_changed"
    other = Client().bucket(loss.BUCKET)
    seed_baseline(other)
    with loss.Guard(other) as gate:
        data, ref = loss.read(other, loss.INDEX)
        index = json.loads(data)
        index["latest_release"]["date"] = "2026-04-03"
        loss.put(other, loss.INDEX, loss.encode(index), generation=int(ref["generation"]))
        with pytest.raises(loss.SourceLossError, match="baseline changed"):
            gate.check_publication()


def test_concurrent_or_interrupted_execution_cannot_steal_claim():
    bucket = Client().bucket(loss.BUCKET)
    with loss.Guard(bucket, owner="first"), mock.patch.object(loss, "execution_completed", return_value=False):
        with pytest.raises(loss.SourceLossError, match="active or interrupted"):
            with loss.Guard(bucket, owner="second"):
                pytest.fail("a second writer was admitted")
    gate = loss.Guard(bucket, owner="crashed")
    gate.__enter__()
    with mock.patch.object(loss, "execution_completed", return_value=False):
        with pytest.raises(loss.SourceLossError, match="active or interrupted"):
            loss.Guard(bucket).__enter__()


def test_confirmed_dead_execution_is_reclaimed_only_into_a_hold():
    bucket = Client().bucket(loss.BUCKET)
    seed_baseline(bucket)
    loss.Guard(bucket, owner="eamlis-monthly-dead").__enter__()
    with mock.patch.object(loss, "execution_completed", return_value=True):
        with loss.Guard(bucket, owner="next") as gate:
            assert gate.held
            assert gate.state["hold"]["reason"] == "interrupted_execution"
            assert gate.assess(100, selection={})
            with pytest.raises(loss.SourceLossError, match="forbids"):
                gate.check_publication()


@pytest.mark.parametrize("completion,reconciling,running,expected", [
    (None, False, 1, False), ("2026-10-27T00:00:00Z", True, 0, False),
    ("2026-10-27T00:00:00Z", False, 1, False), ("2026-10-27T00:00:00Z", False, 0, True),
])
def test_execution_recovery_requires_google_terminal_evidence(completion, reconciling, running, expected):
    job = "projects/shared-datasets-1/locations/us-central1/jobs/eamlis-monthly"
    response = mock.Mock()
    response.json.return_value = {"name": job + "/executions/eamlis-monthly-dead", "job": job,
                                  "completionTime": completion, "reconciling": reconciling, "runningCount": running}
    with mock.patch("google.auth.default", return_value=(object(), "shared-datasets-1")), \
         mock.patch("google.auth.transport.requests.AuthorizedSession") as session:
        session.return_value.__enter__.return_value.get.return_value = response
        assert loss.execution_completed("eamlis-monthly-dead") is expected
        response.raise_for_status.assert_called_once()
        response.json.return_value["job"] = "another job"
        with pytest.raises(loss.SourceLossError, match="identity differs"):
            loss.execution_completed("eamlis-monthly-dead")
        response.raise_for_status.side_effect = OSError("unauthorized or unavailable")
        with pytest.raises(OSError):
            loss.execution_completed("eamlis-monthly-dead")


def test_existing_dataset_without_counted_index_cannot_be_a_first_publication():
    bucket = Client().bucket(loss.BUCKET)
    bucket.blob(f"{loss.ROOT}/latest/{loss.SLUG}.fgb").upload_from_string(b"existing")
    with pytest.raises(loss.SourceLossError, match="without its counted baseline"):
        loss.Guard(bucket).__enter__()
    assert loss.Guard(bucket).load()[0]["owner"] is None


@pytest.mark.parametrize("fails", [False, True])
def test_one_alert_claim_survives_delivery_failure_and_repeated_checks(fails):
    bucket = Client().bucket(loss.BUCKET)
    seed_baseline(bucket)
    with mock.patch.object(slack_notify, "notify", side_effect=OSError("lost response") if fails else None) as send:
        with loss.Guard(bucket) as gate:
            gate.assess(90, selection={})
            if fails:
                with pytest.raises(OSError):
                    gate.notify()
            else:
                gate.notify()
        with loss.Guard(bucket) as again:
            assert again.assess(100, selection={})
            again.notify()
            assert again.state["hold"]["notification"] == ("unknown" if fails else "sent")
        assert send.call_count == 1


def fixture_output(*, source, extract, workdir, release_date, previous_records):
    """Native builders are external here; real metadata, schema and manifest flow through."""
    enriched, records = feature_metadata.enrich_features_with_source_field_ids(
        feature_metadata.iter_geojson_features(extract.geojson), asset_slug=loss.SLUG,
        release=release_date.isoformat(), id_field="OBJECTID", provenance={"source": "fixture"})
    paths = {role: workdir / (loss.SLUG + suffix) for role, suffix in
             {"fgb": ".fgb", "pmtiles": ".pmtiles", "metadata": ".metadata.ndjson.gz",
              "schema": ".schema.json", "manifest": ".manifest.json"}.items()}
    # Exercise real canonical FGB serialization as well as gzip/JSON sidecars.
    import geopandas as gpd
    gpd.GeoDataFrame.from_features(enriched, crs="EPSG:4326").to_file(paths["fgb"], driver="FlatGeobuf")
    paths["pmtiles"].write_bytes((Path(__file__).parent / "browser/fixtures/old.pmtiles").read_bytes())
    feature_metadata.write_sidecar(records, paths["metadata"])
    schema = feature_metadata.schema_from_records(asset_slug=loss.SLUG, release=release_date.isoformat(), records=records)
    feature_metadata.write_schema(schema, paths["schema"])
    return eamlis.AssetOutput(**paths, row_count=len(records),
        sha256={r: loss.digest(p.read_bytes()) for r, p in paths.items() if r != "manifest"},
        schema_payload=schema, sidecar_records=tuple(records))


def execute(client, count, date, *, build=fixture_output):
    with mock.patch.dict(eamlis.os.environ, {"RUN_DATE": date}, clear=True), \
         mock.patch.object(eamlis, "require_binary"), \
         mock.patch.object(eamlis.storage, "Client", return_value=client), \
         mock.patch.object(eamlis, "fetch_source_state", return_value=sample_source_state(feature_count=count)), \
         mock.patch.object(eamlis, "download_source_geojson", side_effect=fixture_download), \
         mock.patch.object(eamlis, "build_asset_output", side_effect=build), \
         mock.patch.object(slack_notify, "notify"):
        return eamlis.run()[0]


def staged(client):
    execute(client, 3, "2026-04-02")
    original = client.bucket(loss.BUCKET).blob(f"{loss.ROOT}/latest/{loss.SLUG}.fgb").data
    held = execute(client, 2, "2026-05-02")
    assert held["status"] == "held"
    assert client.bucket(loss.BUCKET).blob(f"{loss.ROOT}/latest/{loss.SLUG}.fgb").data == original
    assert loss.baseline(client.bucket(loss.BUCKET))["release"]["date"] == "2026-04-02"
    return held


def copy_transport(client, *, fail_at=None):
    """Only the external CLI transport is replaced; promotion logic stays real."""
    copied = []

    def run(args, **kwargs):
        if args[3] == "scripts/dataset_alerts.py":
            return subprocess.CompletedProcess(args, 0)
        assert args[3] == "scripts/gcs_asset.py"
        command, uri = args[4:6]
        bucket, name = uri[5:].split("/", 1)
        blob = client.bucket(bucket).blob(name)
        blob.reload()
        if command == "copy":
            if len(copied) == fail_at:
                raise RuntimeError("interrupted copy")
            target_bucket, target_name = args[6][5:].split("/", 1)
            target = client.bucket(target_bucket).blob(target_name)
            target.cache_control = args[args.index("--cache-control") + 1]
            expected = int(args[args.index("--replace-generation") + 1]) if "--replace-generation" in args else 0
            data = blob.download_as_bytes(if_generation_match=int(args[args.index("--source-generation") + 1]))
            target.upload_from_string(data, content_type=args[args.index("--content-type") + 1], if_generation_match=expected)
            copied.append(args[6])
        elif command == "download":
            Path(args[6]).write_bytes(blob.download_as_bytes(if_generation_match=int(args[args.index("--generation") + 1])))
        else:
            assert command == "stat"
        return subprocess.CompletedProcess(args, 0)

    return run, copied


def test_entrypoint_zero_or_build_failure_never_publishes_or_loses_hold():
    client = Client()
    execute(client, 3, "2026-04-02")
    assert execute(client, 0, "2026-05-02")["status"] == "held"
    with pytest.raises(RuntimeError, match="broken builder"):
        execute(client, 3, "2026-05-03", build=mock.Mock(side_effect=RuntimeError("broken builder")))
    state, _ = loss.Guard(client.bucket(loss.BUCKET)).load()
    assert state["hold"] and state["owner"] is None
    assert loss.baseline(client.bucket(loss.BUCKET))["release"]["rows"] == 3
    # Even a completed-date retry cannot rebuild the production index past a hold.
    assert execute(client, 3, "2026-04-02")["status"] == "held"


def test_real_producer_preview_comparison_and_exact_reviewed_promotion(tmp_path):
    client = Client()
    held = staged(client)
    guard, value, bound = loss.inspection(client)
    raw_plan = review.prepare_plan(client)
    document = {"plan_version": 1, "finalization_version": reviewed_dataset_plan.FINALIZATION_VERSION, "publish": raw_plan}
    path = reviewed_dataset_plan.write_document(document, repo_root=tmp_path)
    plan = reviewed_dataset_plan.read_document(path.read_bytes(), path=path.relative_to(tmp_path).as_posix())["publish"]
    assert plan["source_loss_review"] == bound
    assert loss.validate_candidate_plan(client, plan)["geometry_count"] == 2
    local = tmp_path / "preview"
    review.download_preview(client, local, value["candidate_id"])
    results = review.upload_preview(client, local)
    indexes = tmp_path / "indexes"
    review.collect_preview_indexes(client, indexes)
    assert catalog_site.main(["--bucket", loss.PREVIEW_BUCKET, "--release-index-dir", str(indexes),
        "--feature-preview", "--release-index-assets-only", "--latest-from-release-index",
        "--allow-release-index-only-assets", "--force-access-tier", "private", "--out", str(tmp_path / "site")]) == 0
    site = json.loads((tmp_path / "site/catalog.json").read_text())
    assert site["assets"][0]["row_count"] == 2
    # The real comparison consumes the exact preview metadata/schema/manifest.
    snapshots = []
    local_paths = []
    for n, entry in enumerate(results):
        selected = {r: next(f for f in entry["files"] if f["path"].endswith(suffix))
                    for r, suffix in {"metadata": ".metadata.ndjson.gz", "schema": ".schema.json", "manifest": ".manifest.json"}.items()}
        snapshots.append({"asset_slug": loss.SLUG, "release": entry["date"], "files": selected})
        paths = {}
        for role, ref in selected.items():
            path = tmp_path / f"compare-{n}-{role}"
            path.write_bytes(loss.pinned(client, ref))
            paths[role] = path
        local_paths.append(paths)
    engine = compare_releases.Comparison(tmp_path / "comparison")
    summary = engine.run(*snapshots, *local_paths)
    assert summary["counts"]["removed"] == 1
    assert loss.Guard(client.bucket(loss.BUCKET)).load()[0]["hold"] is not None

    # Exercise the actual workflow command, interruption, generation-safe retry,
    # finalization, index rebuild, then a retry after those metadata rewrites.
    plan_path = tmp_path / "publish-plan.json"
    plan_path.write_bytes(loss.encode(plan))
    with mock.patch.object(loss, "protected_authority", return_value="reviewed-pr-key"), mock.patch.dict(
        "os.environ", {"SHARED_DATASETS_ALLOW_CANONICAL_MUTATION": "1", "SHARED_DATASETS_BUCKET": loss.BUCKET}
    ), mock.patch.object(gcs_asset, "get_client", return_value=client):
        transport, first_copies = copy_transport(client, fail_at=3)
        with mock.patch.object(publish_workflow.subprocess, "run", side_effect=transport):
            with pytest.raises(RuntimeError, match="interrupted copy"):
                publish_workflow.main(["promote", "--plan-json", str(plan_path)])
        with mock.patch.object(loss, "execution_completed", return_value=False):
            with pytest.raises(loss.SourceLossError, match="active or interrupted"):
                loss.Guard(client.bucket(loss.BUCKET)).__enter__()
        transport, remaining = copy_transport(client)
        with mock.patch.object(publish_workflow.subprocess, "run", side_effect=transport):
            assert publish_workflow.main(["promote", "--plan-json", str(plan_path)]) == 0
        assert set(first_copies).isdisjoint(remaining)
        assert len(first_copies) + len(remaining) == len(plan["promotions"])
        finalizer.finalize_promoted_release_metadata(plan, client=client)
        publisher = GcsPublisher(client, loss.BUCKET, release_suffixes=eamlis.RELEASE_SUFFIXES)
        publisher.record_existing_successful_release(eamlis.ASSET, dt.date(2026, 5, 2))
        transport, copies = copy_transport(client)
        with mock.patch.object(publish_workflow.subprocess, "run", side_effect=transport):
            assert publish_workflow.main(["promote", "--plan-json", str(plan_path)]) == 0
        assert copies == []
        loss.finish_promotion(client, plan)
    state, _ = loss.Guard(client.bucket(loss.BUCKET)).load()
    assert state["hold"] is None and state["owner"] is None
    assert loss.baseline(client.bucket(loss.BUCKET))["release"]["rows"] == 2
    assert held["hold"]["id"] == bound["hold_id"]


def test_stale_candidate_baseline_corrupt_bytes_and_unapproved_plans_are_rejected(tmp_path):
    client = Client()
    staged(client)
    plan = review.prepare_plan(client)
    with mock.patch.dict("os.environ", {}, clear=True), pytest.raises(Exception, match="trusted main"):
        loss.begin_promotion(client, plan)
    with mock.patch.object(loss, "protected_authority", return_value="reviewed-key"):
        wrong = copy.deepcopy(plan)
        wrong["promotions"][0]["source_generation"] = "999"
        with pytest.raises(loss.SourceLossError, match="differs from held"):
            loss.begin_promotion(client, wrong)
        with pytest.raises(loss.SourceLossError, match="review its exact candidate"):
            loss.begin_promotion(client, {k: v for k, v in plan.items() if k != "source_loss_review"})
        # A later healthy check creates a different exact candidate, not approval.
        execute(client, 3, "2026-05-03")
        with pytest.raises(loss.SourceLossError, match="candidate changed"):
            loss.begin_promotion(client, plan)
    fresh = review.prepare_plan(client)
    data, ref = loss.read(client.bucket(loss.BUCKET), loss.INDEX)
    index = json.loads(data)
    index["latest_release"]["rows"] = 4
    loss.put(client.bucket(loss.BUCKET), loss.INDEX, loss.encode(index), generation=int(ref["generation"]))
    with mock.patch.object(loss, "protected_authority", return_value="reviewed-key"):
        with pytest.raises(loss.SourceLossError, match="baseline changed"):
            loss.begin_promotion(client, fresh)
    bad = copy.deepcopy(fresh["source_loss_review"]["candidate"])
    bad["sha256"] = "0" * 64
    with pytest.raises(loss.SourceLossError, match="corrupt"):
        loss.candidate(client, bad)


def test_raw_control_mutation_and_copy_races_are_rejected():
    plan = {"promotions": [{"destination_uri": f"gs://{loss.BUCKET}/{loss.ROOT}/latest/a.fgb"},
                           {"destination_uri": f"gs://{loss.BUCKET}/{loss.CONTROL}"}]}
    with pytest.raises(loss.SourceLossError, match="own reviewed resolver"):
        loss.touches_asset(plan)
    with pytest.raises(Exception, match="own reviewed resolver"):
        publish_workflow.require_unmanaged_plan({"promotions": [], "deletions": [{"uri": f"gs://{loss.BUCKET}/{loss.CONTROL}"}]})
    bucket = Client().bucket(loss.BUCKET)
    gate = loss.Guard(bucket)
    gate.__enter__()
    data, ref = loss.read(bucket, loss.CONTROL)
    loss.put(bucket, loss.CONTROL, data, generation=int(ref["generation"]))
    with pytest.raises((loss.SourceLossError, PreconditionFailed)):
        gate.check_publication()


def test_quality_evidence_is_not_abandoned_by_age_cleanup():
    from scripts import scratch_cleanup
    from tests.test_scratch_cleanup import blob
    proposal = scratch_cleanup.group_pending_blobs([blob(
        f"_scratch/pending-publishes/{loss.SLUG}/source-loss-{'a' * 64}/candidate.json", days_old=400)])[0]
    decision = scratch_cleanup.classify_proposal(proposal, now=dt.datetime(2026, 5, 17, tzinfo=dt.UTC),
        warn_age_days=60, delete_age_days=90, warning_marker=None)
    assert decision["action"] == "keep"
