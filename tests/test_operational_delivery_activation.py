from copy import deepcopy
import json
from unittest import mock

import pytest

from scripts import operational_delivery_activation as selector
from test_operational_cutover import rollout


def context(tmp_path, *, mode="unified", prior_mode="unified"):
    root = tmp_path
    current = (json.dumps(rollout(mode), sort_keys=True) + "\n").encode()
    prior = (json.dumps(rollout(prior_mode), sort_keys=True) + "\n").encode()
    (root / selector.cutover.CONFIG).parent.mkdir(parents=True)
    (root / selector.cutover.CONFIG).write_bytes(current)
    for path in selector.PROTOCOL_FILES:
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("exact protocol\n")
    def source(_, revision, path):
        if path == selector.cutover.CONFIG:
            return current if revision == "a" * 40 else prior
        return (root / path).read_bytes()
    records = [{"id": 100, "sha": "b" * 40}]
    class API:
        def pages(self, path):
            assert path == f"repos/SkyTruth/shared-datasets-1/deployments?environment=production-{selector.POLICY_TARGET}&per_page=100"
            return records
    targets = selector.cutover.OPERATIONAL_POLICIES | selector.cutover.REQUIRED_NATIVE | {selector.routing.LEGACY_SCHEMA_ADDRESS}
    payload = {"target": selector.POLICY_TARGET, "targets": sorted(targets)}
    statuses = [{"id": 200, "state": "success", "description": "applied"}]
    return root, API(), source, records, payload, statuses


def activate(root, api, source, payload, statuses):
    with mock.patch.object(selector.deployments, "verify_record", return_value=payload), mock.patch.object(selector.deployments, "verified_statuses", return_value=statuses):
        return selector.activation(api, "SkyTruth/shared-datasets-1", root, "a" * 40, read_source=source, ancestor=lambda a, b: True)


def test_native_configuration_never_contacts_receipts(tmp_path):
    root, api, source, _, payload, statuses = context(tmp_path, mode="native")
    with mock.patch.object(api, "pages", side_effect=AssertionError("native used network")):
        proof = activate(root, api, source, payload, statuses)
    assert proof == {"mode": "shadow", "policy_deployment_id": None, "policy_status_id": None, "config_sha256": selector.cutover.sha256((root / selector.cutover.CONFIG).read_bytes())}


def test_config_merge_without_cutover_receipt_remains_shadow(tmp_path):
    root, api, source, _, payload, statuses = context(tmp_path, prior_mode="native")
    assert activate(root, api, source, payload, statuses)["mode"] == "shadow"


def test_latest_signed_exact_saved_plan_receipt_activates_and_is_rechecked(tmp_path):
    root, api, source, _, payload, statuses = context(tmp_path)
    proof = activate(root, api, source, payload, statuses)
    assert proof["mode"] == "unified" and proof["policy_deployment_id"] == 100 and proof["policy_status_id"] == 200
    selector.same_activation(proof, deepcopy(proof))
    with pytest.raises(ValueError, match="activation changed"):
        selector.same_activation(proof, {**proof, "policy_status_id": 201})


@pytest.mark.parametrize("defect", ["failed", "unsigned", "wrong_targets", "newer_unknown", "other_protocol", "different_config", "forged_identity", "duplicate"])
def test_cannot_reuse_old_unified_success_or_forge_the_activation_binding(tmp_path, defect):
    root, api, source, records, payload, statuses = context(tmp_path)
    if defect == "failed":
        statuses[0]["state"] = "failure"
    elif defect == "wrong_targets":
        payload["targets"].pop()
    elif defect == "newer_unknown":
        records.append({"id": 101, "sha": "c" * 40})
        statuses[0]["description"] = "unknown"
    elif defect == "duplicate":
        records.append(records[0])
    elif defect == "other_protocol":
        original = source
        def source(root, sha, path):
            return b"old gate" if path == selector.PROTOCOL_FILES[0] else original(root, sha, path)
    elif defect == "different_config":
        original = source
        def source(root, sha, path):
            if sha == "b" * 40 and path == selector.cutover.CONFIG:
                return original(root, sha, path).replace(b'"c' + b'c' * 63, b'"d' + b'd' * 63)
            return original(root, sha, path)
    elif defect == "forged_identity":
        payload["target"] = "other"
    if defect == "unsigned":
        with mock.patch.object(selector.deployments, "verify_record", side_effect=ValueError("invalid signature")):
            with pytest.raises(ValueError, match="invalid signature"):
                selector.activation(api, "SkyTruth/shared-datasets-1", root, "a" * 40, read_source=source, ancestor=lambda a, b: True)
        return
    with pytest.raises(ValueError):
        activate(root, api, source, payload, statuses)
