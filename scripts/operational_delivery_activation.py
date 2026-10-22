"""Read-only activation of cloud delivery after the protected cutover receipt.

A reviewed config merge does not activate public cloud delivery. The latest
policy-sync record must prove the evidence-gated exact saved plan was applied.
The caller retains this result and re-verifies it before every delivery and
checkpoint/heartbeat commit. No earlier successful receipt can replace a newer
failed, unsigned, unknown or otherwise incompatible attempt.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
from copy import deepcopy

from scripts import deployment_revision as deployments
from scripts import monitoring_routing_audit as routing
from scripts import operational_cutover as cutover

POLICY_TARGET = "terraform-" + hashlib.sha256(b"Cron alert policy sync").hexdigest()[:16]
PROTOCOL_FILES = ("scripts/operational_cutover.py", "scripts/operational_delivery_activation.py", ".github/workflows/prod-terraform-target-apply.yml",
                  ".github/workflows/unattended-workflow-alert.yml",
                  "scripts/operational_reconcile.py", "scripts/cloud_observation_ledger.py", "scripts/cloud_operational_observations.py",
                  "scripts/deployment_revision.py", "scripts/github_api_read.py", "terraform/envs/prod/operational_observer.tf",
                  "catalog/operational-log-views.json", "scripts/operational_log_views.py")


def source_file(root, revision, path):
    return subprocess.check_output(["git", "show", f"{revision}:{path}"], cwd=root)


def activation(api, repository, root, executor_sha, *, read_source=source_file, ancestor=deployments.git_ancestor, verified_receipts=None):
    root = Path(root)
    cutover.require(cutover.SHA.fullmatch(executor_sha), "activation requires exact main revision")
    raw = read_source(root, executor_sha, cutover.CONFIG)
    cutover.require((root / cutover.CONFIG).read_bytes() == raw, "activation configuration differs from current main bytes")
    current = cutover.config(cutover.strict_json(raw))
    rows = api.pages(f"repos/{repository}/deployments?environment=production-{POLICY_TARGET}&per_page=100")
    cutover.require(rows, "no supported installed policy receipt; cloud coverage and health must wait")
    cutover.require(all(type(row.get("id")) is int and row["id"] > 0 for row in rows) and len({row["id"] for row in rows}) == len(rows), "invalid policy deployment inventory")
    latest = max(rows, key=lambda row: row["id"])
    statuses = api.pages(f"repos/{repository}/deployments/{latest['id']}/statuses?per_page=100")
    # Only reuse cryptographic verification for identical freshly enumerated
    # immutable bytes within this process. A changed/new latest record or status
    # always requires full provenance verification, never an older success.
    key = cutover.sha256(cutover.canonical({"repository": repository, "signer": ".github/workflows/prod-terraform-target-apply.yml",
                                          "record": latest, "statuses": statuses}))
    if verified_receipts is not None and key in verified_receipts:
        payload = deepcopy(verified_receipts[key])
    else:
        payload = deployments.verify_record(api, repository, latest)
        cutover.require(deployments.verified_statuses(api, repository, latest) == statuses, "policy status changed during verification")
    cutover.require(payload["target"] == POLICY_TARGET and statuses and statuses[0].get("state") == "success" and statuses[0].get("description") == "applied", "latest policy sync has no verified terminal success")
    expected = cutover.OPERATIONAL_POLICIES | cutover.REQUIRED_NATIVE | {routing.LEGACY_SCHEMA_ADDRESS}
    cutover.require(isinstance(payload.get("targets"), list) and len(payload["targets"]) == len(expected) and set(payload["targets"]) == expected,
                    "policy receipt omits a protected or operational policy")
    for path in PROTOCOL_FILES:
        cutover.require(read_source(root, latest["sha"], path) == (root / path).read_bytes(), "policy receipt used another fenced coverage/heartbeat protocol")
    prior = cutover.config(cutover.strict_json(read_source(root, latest["sha"], cutover.CONFIG)))
    cutover.require(not (current["mode"] == "native" and prior["mode"] == "unified"), "native worker cannot cover an installed unified policy; wait for protected native restoration")
    cutover.require(ancestor(latest["sha"], executor_sha), "policy deployment is outside current main ancestry")
    result = {"mode": "shadow", "policy_deployment_id": latest["id"], "policy_status_id": statuses[0]["id"], "config_sha256": cutover.sha256(raw)}
    if prior["mode"] == "native":
        # A unified-config worker may stage shadow observations while the
        # supported signed native policy still owns public delivery.
        cutover.require(current["mode"] == "unified" or prior == current, "installed native policy differs from worker configuration")
    else:
        cutover.require(prior["cutover_evidence"] == current["cutover_evidence"], "latest unified policy receipt has different immutable cutover evidence")
        result["mode"] = "unified"
    if verified_receipts is not None:
        verified_receipts.clear()
        verified_receipts[key] = deepcopy(payload)
    return result


def same_activation(expected, actual):
    cutover.require(json.dumps(expected, sort_keys=True) == json.dumps(actual, sort_keys=True), "operational delivery activation changed during the cycle")
