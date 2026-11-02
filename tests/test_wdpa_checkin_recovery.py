from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from ingestion.common import publication as p, release_index
from ingestion.common.owned_publication import OwnedGeneratedPublisher
from ingestion.common.reset_controls import BUCKET
from ingestion.wdpa_monthly import checkin_recovery as recovery, run as wdpa
from scripts import dataset_mutation_authorization as auth, reviewed_dataset_plan as plans
from scripts import install_feature_id_reset as installer
from test_identity_reset import fixture, install_fixture
from test_owned_publication import outputs_fixture, publish
from test_publication import LostResponse, publication_temp_directory


def captured_failure():
    asset = wdpa.ASSETS[0]
    store, candidate, _ = fixture(asset.slug, bucket=BUCKET)
    install_fixture(store, candidate)
    client = SimpleNamespace(bucket=lambda name: SimpleNamespace(name=name))
    publisher = OwnedGeneratedPublisher(client, BUCKET, execution_id="october", executor_sha="c" * 40,
                                         configuration={}, store=store)
    with publication_temp_directory() as directory, patch(
        "ingestion.common.owned_publication.vector_asset.validate_metadata_lookup_bundle",
        return_value=SimpleNamespace(valid=True, errors=()),
    ):
        publish(publisher, asset, outputs_fixture(Path(directory), asset))
    checker = OwnedGeneratedPublisher(client, BUCKET, execution_id="november", executor_sha="d" * 40,
                                      configuration={"RUN_DATE": "2026-11-01"}, store=store)
    record = {"schema_version": 1, "asset_slug": asset.slug, "status": "skipped", "run_date": "2026-11-01",
              "target_release_date": "2026-11-01", "reason": "WDPA source not available yet (HTTP 404)",
              "source": "https://example.test/Nov2026.zip", "source_version": "Nov2026"}
    # Reproduce the old runtime through the real persisted transport boundary.
    # Only its newly added pre-claim semantic check is disabled here.
    with patch.object(checker.engine, "validate_semantics", return_value=None):
        with pytest.raises(release_index.ReleaseIndexError, match="missing release_date"):
            checker.update_latest_run_index(asset=asset, payload=record)
    context = checker.context(asset)
    receipt = store.read_json(context.receipt_uri)
    intent, _ = p.parse_receipt(receipt.value, context)
    data = recovery.recovered_bytes(intent)
    staged = store.write_bytes(f"gs://{BUCKET}/_scratch/pending-publishes/{asset.slug}/recovery-test/index.json",
                               data, 0, {}, "application/json", "no-cache")
    plan = plans.normalize_publish_plan({
        "asset_slug": asset.slug, "proposal_id": "recovery-test",
        "promotions": [{"source_uri": staged.path, "source_generation": str(staged.generation),
                        "destination_uri": recovery.INDEX,
                        "destination_generation": str(intent.value["operations"][0]["expected_generation"]),
                        "content_type": "application/json", "cache_control": "no-cache"}],
        "publication_recovery": {"kind": recovery.KIND, "state": store.read_json(context.state_uri).version.identity(),
                                 "receipt": receipt.version.identity(), "transaction_id": intent.transaction_id,
                                 "result_sha256": p.digest(data)},
    })
    store.events.clear()
    return store, checker, context, plan


def test_original_owner_stays_blocked_until_reviewed_recovery_completes_the_exact_transaction():
    store, checker, context, plan = captured_failure()
    before = deepcopy(store.read_json(context.state_uri).value)
    original = deepcopy(store.read_json(context.receipt_uri).value)
    with pytest.raises(release_index.ReleaseIndexError, match="missing release_date"):
        checker.resume(wdpa.ASSETS[0])
    assert store.events == []
    guard = Mock()
    result = recovery.recover(store, plan, check_authority_and_jobs=guard)
    assert result["status"] == "recovered"
    assert guard.call_count >= 4
    assert store.events == [recovery.INDEX, context.receipt_uri, context.state_uri]
    assert store.read_json(context.state_uri).value == {**before, "active": None}
    receipt = store.read_json(context.receipt_uri).value
    assert receipt["intent"] == original["intent"] and receipt["transaction_id"] == original["transaction_id"]
    assert receipt["phase"] == "derived_complete"
    index = store.read_json(recovery.INDEX).value
    assert index["latest_release"]["date"] == "2026-10-01"
    assert index["latest_run"]["date"] == "2026-11-01" and index["latest_run"]["status"] == "skipped"
    events = list(store.events)
    store.objects.pop(plan["promotions"][0]["source_uri"])
    assert recovery.recover(store, plan, check_authority_and_jobs=guard)["status"] == "already_completed"
    assert store.events == events


@pytest.mark.parametrize("failure", [1, 2, 3])
def test_recovery_resumes_after_each_durable_write_without_refreshing_any_expectation(failure):
    store, _checker, context, plan = captured_failure()
    state = deepcopy(store.read_json(context.state_uri).value)
    intent = deepcopy(store.read_json(context.receipt_uri).value["intent"])
    store.fail_after = failure
    with pytest.raises(LostResponse):
        recovery.recover(store, plan, check_authority_and_jobs=lambda: None)
    store.fail_after = None
    recovery.recover(store, plan, check_authority_and_jobs=lambda: None)
    assert store.events == [recovery.INDEX, context.receipt_uri, context.state_uri]
    assert store.read_json(context.state_uri).value == {**state, "active": None}
    assert store.read_json(context.receipt_uri).value["intent"] == intent


@pytest.mark.parametrize("field", ["transaction_id", "receipt_hash", "receipt_generation", "state_hash", "state_generation", "result_sha256", "destination_generation", "source_generation"])
def test_changed_reviewed_anchors_are_rejected_before_any_write(field):
    store, _checker, _context, plan = captured_failure()
    review = plan["publication_recovery"]
    if field in {"transaction_id", "result_sha256"}:
        review[field] = "f" * 64
    elif field in {"destination_generation", "source_generation"}:
        plan["promotions"][0][field] = str(int(plan["promotions"][0][field]) + 1)
    else:
        part, anchor = field.split("_")
        key = "sha256" if anchor == "hash" else "generation"
        review[part][key] = "f" * 64 if key == "sha256" else review[part][key] + 1
    with pytest.raises(p.PublicationError):
        recovery.recover(store, plan, check_authority_and_jobs=lambda: None)
    assert store.events == []


def test_changed_destination_and_failed_authority_or_job_checks_never_clear_the_claim():
    for changed in (False, True):
        store, _checker, context, plan = captured_failure()
        if changed:
            store.write_json(recovery.INDEX, {"foreign": True}, store.head(recovery.INDEX).generation)
        before = list(store.events)
        with pytest.raises(p.PublicationError):
            recovery.recover(store, plan, check_authority_and_jobs=lambda: p.require(changed, "job or approval changed"))
        assert store.events == before
        assert store.read_json(context.state_uri).value["active"] is not None


@pytest.mark.parametrize("mutation", ["foreign_asset", "extra_effect", "waiver", "other_subtype", "rebuild", "delete"])
def test_reviewed_plan_serialization_rejects_extra_authority(mutation):
    _store, _checker, _context, plan = captured_failure()
    document = {"plan_version": 1, "finalization_version": plans.FINALIZATION_VERSION, "publish": plan}
    if mutation == "foreign_asset":
        plan["asset_slug"] = "wdpa-terrestrial"
    elif mutation == "extra_effect":
        plan["promotions"].append({**plan["promotions"][0], "destination_uri": recovery.PREFIX + "/publications/state.json"})
    elif mutation == "waiver":
        plan["promotions"][0]["compatibility_waiver"] = {"invalid": True}
    elif mutation == "other_subtype":
        plan["identity_reset"] = {}
    elif mutation == "rebuild":
        plan["release_index_asset_slugs"] = [recovery.SLUG]
    else:
        document["delete"] = {"asset_slug": recovery.SLUG, "proposal_id": plan["proposal_id"],
                              "deletions": [{"uri": recovery.PREFIX + "/runs/2026-11-01.json", "generation": "1"}]}
    with pytest.raises(plans.PlanValidationError):
        plans.normalize_document(json.loads(json.dumps(document)))


def test_immutable_recovery_routes_only_to_the_existing_protected_installer():
    _store, _checker, _context, plan = captured_failure()
    document = {"plan_version": 1, "finalization_version": plans.FINALIZATION_VERSION, "publish": plan}
    assert auth.plan_outputs(document) == {"has_publish_plan": False, "has_delete_plan": False,
                                           "has_identity_reset_plan": True}
    with patch.object(auth, "require_production_context"), \
            patch.object(auth, "verified_envelope", return_value={"outcome": "mutation", "document": document}):
        assert installer.authorized_plan(Path("authorization"), "d" * 64, api=Mock(), env={})[1] == plan


def test_authority_loss_after_index_write_leaves_original_claim_for_safe_resume():
    store, _checker, context, plan = captured_failure()
    calls = 0

    def guard():
        nonlocal calls
        calls += 1
        p.require(calls < 3, "approval withdrawn")

    with pytest.raises(p.PublicationError, match="approval withdrawn"):
        recovery.recover(store, plan, check_authority_and_jobs=guard)
    assert store.events == [recovery.INDEX]
    assert store.read_json(context.state_uri).value["active"] is not None
    recovery.recover(store, plan, check_authority_and_jobs=lambda: None)
    assert store.events == [recovery.INDEX, context.receipt_uri, context.state_uri]


def test_changed_live_identity_history_is_not_overwritten():
    store, _checker, context, plan = captured_failure()
    state = store.read_json(context.state_uri)
    store.write_json(context.state_uri, {**state.value, "reserved_next_feature_id": 100}, state.version.generation)
    events = list(store.events)
    with pytest.raises(p.PublicationError, match="publication state changed"):
        recovery.recover(store, plan, check_authority_and_jobs=lambda: None)
    assert store.events == events
    assert store.read_json(context.state_uri).value["reserved_next_feature_id"] == 100


def test_protected_cli_uses_the_publisher_for_recovery_and_rechecks_review_and_quiescence():
    store, _checker, _context, plan = captured_failure()
    import google.auth
    from google.auth.transport import requests
    from google.cloud import storage
    from ingestion.common import publication_gcs, reset_controls as controls

    envelope = {"outcome": "mutation", "document": {"publish": plan}}
    publisher = Mock(service_account_email=controls.PUBLISHER_ACCOUNT)
    deployer = Mock(service_account_email=controls.DEPLOYER_ACCOUNT)
    reader = Mock()
    with patch.object(installer, "authorized_plan", return_value=(envelope, plan)), \
            patch.object(installer.auth, "revalidate") as reviewed, \
            patch.object(google.auth, "default", return_value=(publisher, controls.PROJECT)), \
            patch.object(google.auth, "load_credentials_from_file", return_value=(deployer, controls.PROJECT)), \
            patch.object(requests, "AuthorizedSession"), \
            patch.object(installer, "GoogleControlReader", return_value=reader), \
            patch.object(storage, "Client") as client, \
            patch.object(publication_gcs, "GcsStore", return_value=store), \
            patch.dict("os.environ", {"RESET_CONTROL_CREDENTIALS": "/workflow/control.json"}):
        assert installer.main(["--directory", "authorization", "--expected-sha256", "a" * 64]) == 0
        client.assert_called_once_with(project=controls.PROJECT, credentials=publisher)
        assert reviewed.call_count == reader.check_quiescent.call_count == 4
        assert reader.check_quiescent.call_args.args == (recovery.SLUG,)
