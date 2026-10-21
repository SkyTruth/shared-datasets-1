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

from scripts import deployment_revision as deployments
from scripts import monitoring_routing_audit as routing
from scripts import operational_cutover as cutover

POLICY_TARGET = "terraform-" + hashlib.sha256(b"Cron alert policy sync").hexdigest()[:16]
PROTOCOL_FILES = ("scripts/operational_cutover.py", "scripts/operational_delivery_activation.py", ".github/workflows/prod-terraform-target-apply.yml")


def source_file(root, revision, path):
    return subprocess.check_output(["git", "show", f"{revision}:{path}"], cwd=root)


def activation(api, repository, root, executor_sha, *, read_source=source_file, ancestor=deployments.git_ancestor):
    root = Path(root)
    cutover.require(cutover.SHA.fullmatch(executor_sha), "activation requires exact main revision")
    raw = read_source(root, executor_sha, cutover.CONFIG)
    cutover.require((root / cutover.CONFIG).read_bytes() == raw, "activation configuration differs from current main bytes")
    current = cutover.config(cutover.strict_json(raw))
    result = {"mode": "shadow", "policy_deployment_id": None, "policy_status_id": None, "config_sha256": cutover.sha256(raw)}
    if current["mode"] == "native":
        return result
    rows = api.pages(f"repos/{repository}/deployments?environment=production-{POLICY_TARGET}&per_page=100")
    if not rows:
        return result  # No protected cutover has been attempted yet.
    cutover.require(all(type(row.get("id")) is int and row["id"] > 0 for row in rows) and len({row["id"] for row in rows}) == len(rows), "invalid policy deployment inventory")
    latest = max(rows, key=lambda row: row["id"])
    payload = deployments.verify_record(api, repository, latest)
    statuses = deployments.verified_statuses(api, repository, latest)
    cutover.require(payload["target"] == POLICY_TARGET and statuses and statuses[0].get("state") == "success" and statuses[0].get("description") == "applied", "latest policy sync has no verified terminal success")
    cutover.require(ancestor(latest["sha"], executor_sha), "policy deployment is outside current main ancestry")
    prior_raw = read_source(root, latest["sha"], cutover.CONFIG)
    prior = cutover.config(cutover.strict_json(prior_raw))
    if prior["mode"] == "native":
        return result  # Native routes remain responsible until the gated apply.
    cutover.require(prior["cutover_evidence"] == current["cutover_evidence"], "latest unified policy receipt has different immutable cutover evidence")
    expected = cutover.OPERATIONAL_POLICIES | cutover.REQUIRED_NATIVE | {routing.LEGACY_SCHEMA_ADDRESS}
    cutover.require(isinstance(payload.get("targets"), list) and len(payload["targets"]) == len(expected) and set(payload["targets"]) == expected,
                    "policy receipt omits a protected or operational policy")
    for path in PROTOCOL_FILES:
        cutover.require(read_source(root, latest["sha"], path) == (root / path).read_bytes(), "policy receipt used another evidence-gate protocol")
    result.update(mode="unified", policy_deployment_id=latest["id"], policy_status_id=statuses[0]["id"])
    return result


def same_activation(expected, actual):
    cutover.require(json.dumps(expected, sort_keys=True) == json.dumps(actual, sort_keys=True), "operational delivery activation changed during the cycle")
