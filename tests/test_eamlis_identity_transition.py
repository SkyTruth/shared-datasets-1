"""Producer -> pinned replay -> reviewed installation -> serialized comparison."""
from copy import deepcopy
import csv
import datetime as dt
import json
from pathlib import Path
from unittest import mock

import pytest
from google.api_core.exceptions import NotFound, PreconditionFailed

from ingestion.common import feature_metadata, reset_controls
from ingestion.common.publication import PublicationError
from ingestion.eamlis_monthly import identity as ids, identity_installation as installation
from ingestion.eamlis_monthly import identity_state as state, identity_translations as translations
from ingestion.eamlis_monthly import run as eamlis, source_quality as quality
from scripts import compare_releases, dataset_mutation_authorization as auth
from scripts import eamlis_identity_audit as audit, eamlis_identity_transition as transition
from scripts import feature_metadata_localization as localization, release_feature_model as model
from scripts import reviewed_dataset_plan as plans, slack_notify
from scripts import eamlis_source_loss_review as source_review, publish_workflow, gcs_asset
from scripts import finalize_promoted_release_metadata as finalizer
from ingestion.common.gcs import GcsPublisher
from tests.test_eamlis_identity import area, feature, problem
from tests.test_eamlis_monthly import sample_source_state
from tests.test_source_loss import Client, copy_transport


def output(*, source, extract, workdir, release_date, previous_records, identity_reservation=None):
    features = feature_metadata.iter_geojson_features(extract.geojson)
    args = dict(asset_slug=quality.SLUG, release=release_date.isoformat(), provenance={"source": "fixture"})
    if identity_reservation is None:
        enriched, rows = feature_metadata.enrich_features_with_source_field_ids(features, id_field="OBJECTID", **args)
    else:
        enriched, rows = ids.enrich(features, ledger=identity_reservation.ledger, **args)
    paths = {role: workdir / (quality.SLUG + suffix) for role, suffix in
             {"fgb": ".fgb", "pmtiles": ".pmtiles", "metadata": ".metadata.ndjson.gz",
              "schema": ".schema.json", "manifest": ".manifest.json"}.items()}
    import geopandas as gpd
    gpd.GeoDataFrame.from_features(enriched, crs="EPSG:4326").to_file(paths["fgb"], driver="FlatGeobuf")
    paths["pmtiles"].write_bytes((Path(__file__).parent / "browser/fixtures/old.pmtiles").read_bytes())
    feature_metadata.write_sidecar(rows, paths["metadata"])
    schema = feature_metadata.schema_from_records(asset_slug=quality.SLUG, release=release_date.isoformat(), records=rows)
    feature_metadata.write_schema(schema, paths["schema"])
    values = dict(**paths, row_count=len(rows), sha256={r: quality.digest(p.read_bytes()) for r, p in paths.items() if r != "manifest"},
                  schema_payload=schema, sidecar_records=tuple(rows))
    return eamlis.AssetOutput(**values) if identity_reservation is None else eamlis.KeyedAssetOutput(**values, reservation=identity_reservation)


def run(client, properties, date, *, builder=output):
    def download(*, source, dest, page_size):
        dest.write_bytes(quality.encode({"type": "FeatureCollection", "features": [feature(p) for p in properties]}))
        return eamlis.SourceExtract(dest, len(properties), 0)
    with mock.patch.dict("os.environ", {"RUN_DATE": date}, clear=True), \
         mock.patch.object(eamlis, "require_binary"), \
         mock.patch.object(eamlis.storage, "Client", return_value=client), \
         mock.patch.object(eamlis, "fetch_source_state", return_value=sample_source_state(feature_count=len(properties))), \
         mock.patch.object(eamlis, "download_source_geojson", side_effect=download), \
         mock.patch.object(eamlis, "build_asset_output", side_effect=builder), \
         mock.patch.object(slack_notify, "notify"):
        return eamlis.run()[0]


def rows():
    return [problem("PZ", OBJECTID=1), problem("PA", OBJECTID=2), area("AREA3", OBJECTID=3)]


def prepared(tmp_path, *, history=None):
    client = Client()
    for n, props in enumerate(history or [rows()], 1):
        run(client, props, f"2026-05-{n:02d}")
    bucket = client.bucket(quality.BUCKET)
    baseline = quality.baseline(bucket)
    index = json.loads(quality.read(bucket, quality.INDEX)[0])
    inventory = []
    for release in index["releases"]:
        ref = next(ref for ref in release["files"] if ref["path"].endswith(".metadata.ndjson.gz"))
        path = tmp_path / (release["date"] + ".gz")
        path.write_bytes(quality.pinned(client, ref))
        inventory.append({**ref, "format": "metadata", "rows": release["rows"], "date": release["date"], "local": str(path)})
    first = (dt.date.fromisoformat(baseline["release"]["date"]) + dt.timedelta(days=1)).isoformat()
    plan = installation.prepare(client, inventory, first_release=first, directory=tmp_path, proposal_id="key-cutover")
    document = {"plan_version": 1, "finalization_version": plans.FINALIZATION_VERSION, "publish": plan}
    path = plans.write_document(document, repo_root=tmp_path)
    normalized = plans.read_document(path.read_bytes(), path=path.relative_to(tmp_path).as_posix())
    return client, normalized["publish"], inventory


def install(client, plan, tmp_path, check=None):
    return installation.install(client, plan, directory=tmp_path, check_authority_and_jobs=check or (lambda: None))


def snapshot(client, date, directory):
    bucket = client.bucket(quality.BUCKET)
    index = json.loads(quality.read(bucket, quality.INDEX)[0])
    entry = next(r for r in index["releases"] if r["date"] == date)
    refs = {role: next(f for f in entry["files"] if f["path"].endswith(suffix)) for role, suffix in
            {"metadata": ".metadata.ndjson.gz", "schema": ".schema.json", "manifest": ".manifest.json"}.items()}
    paths = {}
    for role, ref in refs.items():
        path = directory / (date + role)
        path.write_bytes(quality.pinned(client, ref))
        paths[role] = path
    return {"asset_slug": quality.SLUG, "release": date, "files": refs}, paths


def test_full_cutover_skips_objectid_churn_and_compares_real_edits(tmp_path):
    client, plan, _ = prepared(tmp_path)
    before = snapshot(client, "2026-05-01", tmp_path)
    check = mock.Mock()
    assert install(client, plan, tmp_path, check)["status"] == "prepared"
    assert check.call_count == 4
    with pytest.raises(ids.IdentityError, match="first reviewed"):
        transition.require_published(client)
    assert run(client, rows(), "2026-05-02")["identity_contract"] == ids.CONTRACT
    assert transition.require_published(client)["phase"] == "active"
    first = snapshot(client, "2026-05-02", tmp_path)
    renumbered = [{**p, "OBJECTID": 100 + n} for n, p in enumerate(rows())]
    assert run(client, renumbered, "2026-05-03")["status"] == "skipped"
    changed = [dict(p) for p in renumbered]
    changed[0]["COMP_COST"] = 1234
    assert run(client, changed, "2026-05-04")["status"] == "success"
    second = snapshot(client, "2026-05-04", tmp_path)
    summary = compare_releases.Comparison(tmp_path / "compare").run(first[0], second[0], first[1], second[1])
    assert summary["counts"]["removed"] == summary["counts"]["added"] == 0
    assert summary["counts"]["properties_only"] == 1
    incompatible = compare_releases.Comparison(tmp_path / "legacy-compare").run(before[0], first[0], before[1], first[1])
    assert incompatible["identity"]["compatible"] is False
    assert install(client, plan, tmp_path)["status"] == "already_installed"


def test_history_omission_and_reappearance_never_recycle_ids(tmp_path):
    original = rows()
    # A changed property forces the second complete legacy release; one key disappears.
    client, plan, _ = prepared(tmp_path, history=[original, [{**p, "PA_NAME": "revised"} for p in original[1:]]])
    install(client, plan, tmp_path)
    bucket = client.bucket(quality.BUCKET)
    with quality.Guard(bucket) as guard:
        ledger = state.selected_ledger(guard)
        assert len(ledger.entries) == 3
        reserved = state.reserve(guard, [problem("NEW")], tmp_path)
        assert reserved.ledger.ids[("problem", "NEW")] == "4"
        original_id = ledger.ids[("problem", "PZ")]
    with quality.Guard(bucket) as guard:
        again = state.reserve(guard, original, tmp_path)
        assert again.ledger.ids[("problem", "PZ")] == original_id
        assert again.ledger.next_feature_id == 5


@pytest.mark.parametrize("failure", ["marker", "control"])
def test_partial_install_fails_closed_and_exact_retry_finishes(tmp_path, failure):
    client, plan, _ = prepared(tmp_path)
    bucket = client.bucket(quality.BUCKET)
    target = state.MARKER if failure == "marker" else quality.CONTROL
    real = quality.put
    def fail(b, name, data, *, generation):
        if name == target:
            raise RuntimeError("lost response")
        return real(b, name, data, generation=generation)
    with mock.patch.object(quality, "put", side_effect=fail), pytest.raises(RuntimeError, match="lost response"):
        install(client, plan, tmp_path)
    if failure == "control":
        with pytest.raises(ids.IdentityError, match="selected state is missing"):
            with quality.Guard(bucket):
                pytest.fail("legacy writer admitted after marker")
        # Refusal precedes the legacy claim, preserving the reviewed CAS.
        assert install(client, plan, tmp_path)["status"] == "prepared"
    else:
        assert install(client, plan, tmp_path)["status"] == "prepared"


def test_lost_control_response_recognizes_installed_state_without_rewind(tmp_path):
    client, plan, _ = prepared(tmp_path)
    real = quality.put
    def fail(b, name, data, *, generation):
        result = real(b, name, data, generation=generation)
        if name == quality.CONTROL:
            raise RuntimeError("lost committed response")
        return result
    with mock.patch.object(quality, "put", side_effect=fail), pytest.raises(RuntimeError):
        install(client, plan, tmp_path)
    assert install(client, plan, tmp_path)["status"] == "already_installed"
    with quality.Guard(client.bucket(quality.BUCKET)) as guard:
        state.reserve(guard, [problem("NEW")], tmp_path)
    assert install(client, plan, tmp_path)["status"] == "already_installed"
    with quality.Guard(client.bucket(quality.BUCKET)) as guard:
        assert state.selected_ledger(guard).next_feature_id == 5


def test_authority_denial_and_stale_control_never_mutate_canonical(tmp_path):
    client, plan, _ = prepared(tmp_path)
    bucket = client.bucket(quality.BUCKET)
    before = quality.read(bucket, quality.CONTROL)
    with pytest.raises(RuntimeError, match="denied"):
        install(client, plan, tmp_path, mock.Mock(side_effect=RuntimeError("denied")))
    assert quality.read(bucket, quality.CONTROL) == before
    assert quality.read(bucket, state.MARKER)[0] is None
    with quality.Guard(bucket):
        pass
    with pytest.raises(ids.IdentityError, match="control changed"):
        install(client, plan, tmp_path)


def test_failed_reservation_cas_does_not_select_or_expose_ids(tmp_path):
    client, plan, _ = prepared(tmp_path)
    install(client, plan, tmp_path)
    bucket = client.bucket(quality.BUCKET)
    with quality.Guard(bucket) as guard:
        original = state.selected_ledger(guard)
        with mock.patch.object(guard, "save", side_effect=PreconditionFailed("race")), pytest.raises(PreconditionFailed):
            state.reserve(guard, [problem("NEW")], tmp_path)
        # Reread persisted authority; failed proposal is only an orphan object.
        persisted, _ = guard.load()
        guard.state = persisted
        assert state.selected_ledger(guard) == original


def test_all_25_area_only_rows_keep_distinct_keys_and_new_columns(tmp_path):
    props = [area(f"AREA{n}", OBJECTID=n+1) for n in range(25)]
    ledger = ids.Ledger.genesis().reserve(ids.classify_release(props))
    _features, records = ids.enrich([feature(p) for p in props], ledger=ledger,
                                  release="2026-05-02", asset_slug=quality.SLUG, provenance={})
    path = tmp_path / "area-only.gz"
    feature_metadata.write_sidecar(records, path)
    actual = state.validate_records(ledger, model.read_metadata_sidecar(path))
    assert len(actual) == len({row["feature_id"] for row in actual}) == 25
    assert {tuple(row["identity_key"]) for row in actual} == {("area", p["AMLIS_KEY"]) for p in props}


def test_translation_crosswalk_uses_release_key_and_value_hash_not_id_equality(tmp_path):
    original = [problem("Z", OBJECTID=1), problem("A", OBJECTID=2)]
    _enriched, records = feature_metadata.enrich_features_with_source_field_ids([feature(p) for p in original],
        asset_slug=quality.SLUG, release="2026-05-01", id_field="OBJECTID", provenance={})
    ledger = ids.Ledger.genesis().reserve(ids.classify_release(original))
    source, target = tmp_path / "legacy.csv", tmp_path / "keyed.csv"
    columns = (*localization.REQUIRED_TRANSLATION_COLUMNS, *localization.OPTIONAL_TRANSLATION_COLUMNS)
    with source.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows([
            dict(feature_id="1", field="PA_NAME", locale="es", source_value_hash=localization.source_value_hash(original[0]["PA_NAME"]), value="Traducido", review_state="human_reviewed"),
            dict(feature_id="2", field="PA_NAME", locale="es", source_value_hash=localization.source_value_hash("stale text"), value="No"),
            dict(feature_id="99", field="PA_NAME", locale="es", source_value_hash=localization.source_value_hash(original[0]["PA_NAME"]), value="Orphan"),
        ])
    report = translations.remap(ledger, records, source, target, fields=("PA_NAME",), locales=("es",), provenance="pinned-release")
    remapped = localization.read_translation_source(target)
    assert (report["matched"], report["archived_unmapped"]) == (1, 2)
    assert remapped[0].feature_id == ledger.ids[("problem", "Z")] == "2"
    assert remapped[0].value == "Traducido" and remapped[0].review_state == "human_reviewed"
    assert "legacy_feature_id=1" in remapped[0].notes


def test_typed_plan_routes_only_to_installer_and_blocks_scope_expansion(tmp_path):
    client, plan, _ = prepared(tmp_path)
    assert auth.plan_outputs({"publish": plan}) == {"has_publish_plan": False, "has_delete_plan": False, "has_identity_reset_plan": True}
    for change in (
        lambda v: v["promotions"].pop(),
        lambda v: v["promotions"][0].update(destination_generation="123"),
        lambda v: v.update(release_index_asset_slugs=["unrelated"]),
        lambda v: v.update(source_loss_review={}),
    ):
        invalid = deepcopy(plan)
        change(invalid)
        with pytest.raises(plans.PlanValidationError):
            plans.normalize_publish_plan(invalid)


def test_pinned_replay_rejects_corrupt_and_missing_historical_bytes(tmp_path):
    client, plan, inventory = prepared(tmp_path)
    assert audit.verified_rows(inventory[0])
    baseline = quality.baseline(client.bucket(quality.BUCKET))
    fgb_ref = next(ref for ref in baseline["release"]["files"] if ref["path"].endswith(".fgb"))
    path = tmp_path / "real.fgb"
    path.write_bytes(quality.pinned(client, fgb_ref))
    actual = ids.classify_release(audit.properties(path, "fgb"))
    assert {row.key for row in actual} == {row.key for row in ids.classify_release(rows())}
    Path(inventory[0]["local"]).write_bytes(b"corrupt")
    with pytest.raises(ids.IdentityError, match="pinned inventory"):
        audit.replay(inventory)
    with pytest.raises(ids.IdentityError, match="duplicate release"):
        audit.replay([inventory[0], inventory[0]])
    for data in (b"", b"fgb\x03fgb\x01\x10\x00\x00\x00short"):
        path = tmp_path / "bad.fgb"
        path.write_bytes(data)
        with pytest.raises(ids.IdentityError):
            audit.properties(path, "fgb")


def test_eamlis_pause_drain_checks_use_fixed_job_and_every_page():
    from tests.test_reset_controls import ResetControlTests
    helper = ResetControlTests()
    session = helper.session(pages=[{"nextPageToken": "next"}, {"executions": [{"completionTime": "done"}]}])
    reset_controls.GoogleControlReader(session).check_job_quiescent("eamlis-monthly")
    assert len(session.get.call_args_list) == 3
    assert "/jobs/eamlis-monthly" in session.get.call_args_list[0].args[0]
    with pytest.raises(PublicationError, match="outside identity installation"):
        reset_controls.GoogleControlReader(session).check_job_quiescent("other")


def test_first_publication_can_be_reviewed_held_candidate_and_activate(tmp_path):
    client, plan, _ = prepared(tmp_path)
    install(client, plan, tmp_path)
    held = run(client, rows()[:2], "2026-05-02")
    assert held["status"] == "held"
    candidate_plan = plans.normalize_publish_plan(source_review.prepare_plan(client))
    path = tmp_path / "held-plan.json"
    path.write_bytes(quality.encode(candidate_plan))
    with mock.patch.object(quality, "protected_authority", return_value="exact-reviewed-plan"), \
         mock.patch.dict("os.environ", {"SHARED_DATASETS_ALLOW_CANONICAL_MUTATION": "1", "SHARED_DATASETS_BUCKET": quality.BUCKET}), \
         mock.patch.object(gcs_asset, "get_client", return_value=client):
        transport, _copies = copy_transport(client)
        with mock.patch.object(publish_workflow.subprocess, "run", side_effect=transport):
            assert publish_workflow.main(["promote", "--plan-json", str(path)]) == 0
        finalizer.finalize_promoted_release_metadata(candidate_plan, client=client)
        publisher = GcsPublisher(client, quality.BUCKET, release_suffixes=eamlis.RELEASE_SUFFIXES)
        publisher.record_existing_successful_release(eamlis.ASSET, dt.date(2026, 5, 2))
        quality.finish_promotion(client, candidate_plan)
    assert transition.require_published(client) == {"contract_id": ids.CONTRACT, "phase": "active", "held": False}


@pytest.mark.parametrize("failure", ["missing_control", "legacy_control", "missing_ledger", "wrong_marker"])
def test_installed_state_loss_never_falls_back_to_objectid(tmp_path, failure):
    client, plan, _ = prepared(tmp_path)
    install(client, plan, tmp_path)
    bucket = client.bucket(quality.BUCKET)
    value = json.loads(quality.read(bucket, quality.CONTROL)[0])
    if failure == "missing_control":
        bucket.blob(quality.CONTROL).exists = False
    elif failure == "legacy_control":
        value.pop("identity")
        value["schema_version"] = 1
        blob = bucket.blob(quality.CONTROL)
        quality.put(bucket, quality.CONTROL, quality.encode(value), generation=blob.generation)
    elif failure == "missing_ledger":
        bucket.blob(value["identity"]["ledger"]["path"][len(installation.URI):]).exists = False
    else:
        blob = bucket.blob(state.MARKER)
        quality.put(bucket, state.MARKER, b"{}", generation=blob.generation)
    with pytest.raises((ids.IdentityError, quality.SourceLossError, NotFound)):
        with quality.Guard(bucket):
            pytest.fail("legacy fallback admitted")
    assert not bucket.blob(f"{quality.ROOT}/releases/2026-05-02/{quality.SLUG}.fgb").exists


def test_ambiguous_source_persists_hold_and_reserved_ids_survive_build_failure(tmp_path):
    client, plan, _ = prepared(tmp_path)
    install(client, plan, tmp_path)
    with pytest.raises(ids.IdentityError, match="duplicate"):
        run(client, [rows()[0], rows()[0]], "2026-05-02")
    bucket = client.bucket(quality.BUCKET)
    assert quality.Guard(bucket).load()[0]["hold"] is not None
    with pytest.raises(RuntimeError, match="builder failed"):
        run(client, [*rows(), problem("NEW", OBJECTID=100)], "2026-05-02", builder=mock.Mock(side_effect=RuntimeError("builder failed")))
    with quality.Guard(bucket) as guard:
        ledger = state.selected_ledger(guard)
        assert ledger.ids[("problem", "NEW")] == "4" and guard.held
        assert state.reserve(guard, [problem("NEXT")], tmp_path).ledger.ids[("problem", "NEXT")] == "5"


def test_metadata_tampering_and_unreviewed_replacements_are_refused(tmp_path):
    client, plan, _ = prepared(tmp_path)
    install(client, plan, tmp_path)
    run(client, rows(), "2026-05-02")
    with quality.Guard(client.bucket(quality.BUCKET)) as guard:
        ledger = state.selected_ledger(guard)
        _features, records = ids.enrich([feature(p) for p in rows()], ledger=ledger,
                                      release="2026-05-02", asset_slug=quality.SLUG, provenance={})
        records[0]["properties"]["PA_NAME"] = "silently changed"
        with pytest.raises(ids.IdentityError, match="property hash"):
            state.validate_records(ledger, records)
        with pytest.raises(ids.IdentityError, match="README or the complete current translation"):
            state.require_metadata_only(guard, {"promotions": [{"destination_uri": "unrelated"}]})


@pytest.mark.parametrize("tamper", [False, True])
def test_readme_retry_reuses_approved_copy_without_rebuilding_or_resetting_identity(tmp_path, tamper):
    client, installation_plan, _ = prepared(tmp_path)
    install(client, installation_plan, tmp_path)
    run(client, rows(), "2026-05-02")
    bucket = client.bucket(quality.BUCKET)
    index_before = quality.read(bucket, quality.INDEX)
    identity_before = deepcopy(quality.Guard(bucket).load()[0]["identity"])
    local = tmp_path / "README.md"
    local.write_text("Reviewed activated EAMLIS identity documentation.\n")
    source = quality.immutable_file(bucket, f"_scratch/pending-publishes/{quality.SLUG}/readme-recovery/README.md", local)
    destination = bucket.blob(quality.ROOT + "/README.md")
    destination.upload_from_string(b"Old documentation", content_type="text/markdown", if_generation_match=0)
    plan = {"asset_slug": quality.SLUG, "promotions": [{
        "source_uri": source["path"], "source_generation": str(source["generation"]),
        "destination_uri": f"gs://{quality.BUCKET}/{destination.name}",
        "destination_generation": str(destination.generation), "content_type": "text/markdown",
        "cache_control": quality.NO_CACHE}], "release_index_asset_slugs": []}
    path = tmp_path / "readme-plan.json"
    path.write_bytes(quality.encode(plan))
    with mock.patch.object(quality, "protected_authority", return_value="same-reviewed-readme-proposal"), \
         mock.patch.dict("os.environ", {"SHARED_DATASETS_ALLOW_CANONICAL_MUTATION": "1", "SHARED_DATASETS_BUCKET": quality.BUCKET}), \
         mock.patch.object(gcs_asset, "get_client", return_value=client):
        transport, copied = copy_transport(client)
        with mock.patch.object(publish_workflow.subprocess, "run", side_effect=transport):
            assert publish_workflow.main(["promote", "--plan-json", str(path)]) == 0
        assert len(copied) == 1
        # Interruption after the confirmed copy retains the original owner.
        assert quality.Guard(bucket).load()[0]["owner"] == "same-reviewed-readme-proposal"
        if tamper:
            destination.upload_from_string(b"Unexpected replacement", if_generation_match=destination.generation)
        transport, recopied = copy_transport(client)
        with mock.patch.object(publish_workflow.subprocess, "run", side_effect=transport):
            if tamper:
                with pytest.raises(PreconditionFailed):
                    publish_workflow.main(["promote", "--plan-json", str(path)])
            else:
                assert publish_workflow.main(["promote", "--plan-json", str(path)]) == 0
                # Rebuilding here would fail the external transport assertion.
                assert publish_workflow.main(["rebuild-release-index", "--plan-json", str(path)]) == 0
                quality.finish_promotion(client, plan)
        assert recopied == []
    after = quality.Guard(bucket).load()[0]
    assert after["identity"] == identity_before and quality.read(bucket, quality.INDEX) == index_before
    assert after["owner"] == ("same-reviewed-readme-proposal" if tamper else None)


def test_candidate_identity_changes_when_only_the_reserved_ledger_advances(tmp_path):
    client, plan, _ = prepared(tmp_path)
    install(client, plan, tmp_path)
    first = run(client, rows()[:2], "2026-05-02")
    with quality.Guard(client.bucket(quality.BUCKET)) as guard:
        state.reserve(guard, [*rows(), problem("NEW")], tmp_path)
    second = run(client, rows()[:2], "2026-05-02")
    assert first["hold"]["candidate"] != second["hold"]["candidate"]
    assert first["hold"]["id"] == second["hold"]["id"]
    assert quality.candidate(client, second["hold"]["candidate"])["geometry_count"] == 2
    candidate = quality.candidate(client, second["hold"]["candidate"])
    manifest_ref = next(ref for ref in candidate["files"] if ref["role"] == "manifest")
    manifest = json.loads(quality.pinned(client, manifest_ref))
    assert manifest["hashes"]["geometry_hash_algorithm"] == feature_metadata.release_feature_model.GEOMETRY_HASH_ALGORITHM_V2
