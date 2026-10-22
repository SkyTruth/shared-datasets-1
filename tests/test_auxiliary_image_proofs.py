"""Phase-owned image receipts do not replace fresh execution/receipt checks."""
from copy import deepcopy
from unittest.mock import Mock, patch

import pytest

from scripts import cloud_incident_adapter as adapter, deployment_emission as emissions, slack_incidents as incidents
from test_cloud_incident_adapter import at, auxiliary_source, proposal, published
from test_slack_incident_policy import PersistedLedger
from test_slack_incidents import IDENTITY


def facts():
    values = []
    for index in range(3):
        fact = auxiliary_source(str(index), status="success", success=True)
        name = "wdpa-execution-observer-case" + str(index)
        fact["execution"]["name"] = fact["execution"]["name"].rsplit("/", 1)[0] + "/" + name
        fact["execution"]["uid"] = f"10be5376-cc93-4eed-9b11-ab2d3ef8d79{index}"
        fact["fact"]["context"]["execution"] = name
        values.append(fact)
    return values


def reader(values):
    api = Mock()
    raw = {fact["execution"]["name"]: {"name": fact["execution"]["name"], "uid": fact["execution"]["uid"],
        "createTime": fact["execution"]["create_time"], "startTime": fact["execution"]["start_time"],
        "completionTime": fact["execution"]["completion_time"],
        "template": {"containers": [{"image": image} for image in fact["execution"]["images"]]},
        "conditions": [{"type": "Completed", "state": "CONDITION_SUCCEEDED"}]} for fact in values}
    api.execution.side_effect = lambda name: deepcopy(raw[name])
    return api


def github(value):
    api = Mock()
    record = {"id": 17, "sha": "b" * 40, "payload": {"target": "wdpa-monthly", "artifact": value["execution"]["images"][0],
        "targets": [incidents.deployments.AUXILIARY_CLOUD_JOBS["wdpa-execution-observer"]["resource"]]}}
    statuses = [{"id": 18, "description": "verified", "state": "success"}]
    api.pages.side_effect = lambda path: [deepcopy(record)] if "?environment=" in path else deepcopy(statuses)
    api.get.side_effect = lambda path: deepcopy(record) if path.endswith("/17") else (_ for _ in ()).throw(ValueError("Unknown exact record"))
    return api, record


def verified(record):
    return patch.object(incidents.deployments, "verify_record", side_effect=lambda _api, _repository, row: deepcopy(row["payload"]))


def test_prepare_and_current_recovery_share_one_receipt_but_fetch_every_execution():
    values = facts()
    api, record = github(values[0])
    cloud_reader = reader(values)
    image_proofs = incidents.AuxiliaryImageProofs(api)
    ledger = PersistedLedger()
    with verified(record) as records, patch.object(incidents.deployments, "verify_receipt") as receipts:
        prepared, evaluation = incidents.prepare_cloud(ledger, IDENTITY, proposal(*values), now=at(10), github_api=api,
            image_proofs=image_proofs, recovery_verifier=lambda observation: incidents.reverify_saved_cloud_success(
                cloud_reader, observation, github_api=api, image_proofs=image_proofs))
    assert prepared == [] and ledger.states == {}
    assert evaluation["cloud_counts"]["verified_observer_health"] == 3
    assert records.call_count == receipts.call_count == 1
    assert cloud_reader.execution.call_count == 3
    assert len({call.args[0] for call in cloud_reader.execution.call_args_list}) == 3
    assert api.post.call_count == cloud_reader.run_receipt.call_count == 0


def test_execution_change_still_stops_preparation_even_after_image_proof_was_verified():
    values = facts()
    api, record = github(values[0])
    cloud_reader = reader(values)
    original = cloud_reader.execution.side_effect
    def changed(name):
        value = original(name)
        if name == values[1]["execution"]["name"]:
            value["uid"] = "changed"
        return value
    cloud_reader.execution.side_effect = changed
    context = incidents.AuxiliaryImageProofs(api)
    ledger = PersistedLedger()
    with verified(record), patch.object(incidents.deployments, "verify_receipt"), pytest.raises(incidents.IncidentError, match="execution changed"):
        incidents.prepare_cloud(ledger, IDENTITY, proposal(*values), now=at(10), github_api=api, image_proofs=context,
            recovery_verifier=lambda observation: incidents.reverify_saved_cloud_success(
                cloud_reader, observation, github_api=api, image_proofs=context))
    assert ledger.states == {} and api.post.call_count == 0


def test_proof_rebinds_only_execution_and_returns_defensive_copies():
    values = facts()
    api, record = github(values[0])
    context = incidents.AuxiliaryImageProofs(api)
    with verified(record), patch.object(incidents.deployments, "verify_receipt") as signatures:
        first = context.verify(api, values[0])
        first["status_id"] = 99
        second = context.verify(api, values[1])
        assert second["status_id"] == 18 and second["execution"] == values[1]["execution"]["name"]
        assert context.verify(api, values[1], expected=second) == second
        assert signatures.call_count == 1
        # Another phase starts with fresh complete API/signature verification.
        assert incidents.AuxiliaryImageProofs(api).verify(api, values[1], expected=second) == second
        assert signatures.call_count == 2
    with pytest.raises(incidents.IncidentError, match="another API phase"):
        context.verify(Mock(), values[0])


@pytest.mark.parametrize("field,value", [("status_id", 19), ("record_id", 19), ("revision", "c" * 40)])
def test_different_expected_receipt_never_substitutes_cached_latest(field, value):
    fact = facts()[0]
    api, record = github(fact)
    context = incidents.AuxiliaryImageProofs(api)
    with verified(record), patch.object(incidents.deployments, "verify_receipt"):
        proof = context.verify(api, fact)
        changed = {**proof, field: value}
        reads = api.pages.call_count + api.get.call_count
        with pytest.raises((ValueError, incidents.IncidentError)):
            context.verify(api, fact, expected=changed)
        assert api.pages.call_count + api.get.call_count > reads


def test_failed_signature_is_not_cached_and_changed_image_cannot_reuse_prior_proof():
    fact = facts()[0]
    api, record = github(fact)
    context = incidents.AuxiliaryImageProofs(api)
    with verified(record), patch.object(incidents.deployments, "verify_receipt", side_effect=emissions.EmissionError("invalid signature")) as signatures:
        for _ in range(2):
            with pytest.raises(emissions.EmissionError):
                context.verify(api, fact)
        assert signatures.call_count == 2
    with verified(record), patch.object(incidents.deployments, "verify_receipt"):
        proof = context.verify(api, fact)
        changed = deepcopy(fact)
        changed["execution"]["images"] = [proof["artifact"].replace("a" * 64, "c" * 64)]
        changed["execution"]["revision"] = "sha256:" + "c" * 64
        assert context.verify(api, changed) is None
        with pytest.raises(incidents.IncidentError, match="differs from exact execution"):
            context.verify(api, changed, expected=proof)


def test_missing_uid_cannot_poison_a_later_valid_same_image_proof():
    fact = facts()[0]
    api, record = github(fact)
    context = incidents.AuxiliaryImageProofs(api)
    missing = deepcopy(fact)
    missing["execution"]["uid"] = None
    with verified(record), patch.object(incidents.deployments, "verify_receipt") as signatures:
        assert context.verify(api, missing) is None
        assert api.mock_calls == []
        assert context.verify(api, fact)["record_id"] == 17
        assert signatures.call_count == 1


def test_image_context_never_caches_canonical_run_receipt_or_current_execution():
    fact = published()
    cloud_reader = reader([fact])
    cloud_reader.run_receipt.side_effect = [deepcopy(fact["run_receipt"]), {**fact["run_receipt"], "sha256": "f" * 64}]
    api = Mock()
    context = incidents.AuxiliaryImageProofs(api)
    observation = adapter.normalize(fact)
    assert incidents.reverify_saved_cloud_success(cloud_reader, observation, github_api=api, image_proofs=context)
    with pytest.raises(incidents.IncidentError, match="canonical receipt changed"):
        incidents.reverify_saved_cloud_success(cloud_reader, observation, github_api=api, image_proofs=context)
    assert cloud_reader.execution.call_count == cloud_reader.run_receipt.call_count == 2
    assert api.mock_calls == []
