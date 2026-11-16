#!/usr/bin/env python3
"""Verify execution recovery before editing recorded parents; never poll or post."""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import google.auth
from google.auth.transport.requests import AuthorizedSession
from google.cloud import storage
from services.monitoring_slack.run import INCIDENT, PROJECT, Store, deliver, require
from scripts.slack_incident_api import Slack, CHANNEL
from services.monitoring_slack import executions
from ingestion.common.decision_events import digest

PARENT_LIMIT = 200
FAILURE_LIMIT = 100
EDIT_LIMIT = 25


class NotEligible(ValueError):
    """Normal evidence absence, distinct from broken reads or persisted state."""


class FailureBudgetExhausted(Exception):
    """Quiet deferral; never accept a partially verified parent."""


class ReadBudget:
    def __init__(self):
        self.remaining = FAILURE_LIMIT

    def take(self):
        if self.remaining == 0:
            raise FailureBudgetExhausted
        self.remaining -= 1


def execution_alias(key, store, identity, shared=None):
    alias, generation = store.load(key)
    require(alias is not None and alias.get("schema") == executions.RECEIPT_SCHEMA
            and set(alias) == {"schema", "identity", "ts", "source", "closed"}
            and type(alias["closed"]) is bool
            and alias["identity"] == identity, "recovery requires the original execution alias")
    ctx = executions.context(alias["source"])
    if shared is None:
        shared, _ = executions.load(store, ctx, identity)
    require(shared is not None and shared["source"] == ctx and shared["ts"] == alias["ts"] and key in shared["failures"],
            "recovery differs from the original failure parent")
    return alias, generation


def verified_recovery(key, alert, store, identity, api, deployment_id=None, *, proof_memo=None, shared=None):
    from scripts import slack_incidents as incidents
    from scripts.ingestion_targets import TARGETS

    if deployment_id is not None:
        require(deployment_id.isdecimal() and int(deployment_id) > 0, "invalid recovery deployment ID")
    alias, generation = execution_alias(key, store, identity, shared)
    ctx = executions.context(alias["source"])
    resource = alert.get("resource", {})
    require(alert["policy"]["displayName"] == executions.FAILURE_POLICY
            and resource == {"type": "cloud_run_job", "labels": {"project_id": ctx["project"],
                "location": "us-central1", "job_name": ctx["job"]}}
            and alert.get("log", {}).get("extractedLabels", {}).get("execution") == ctx["execution"]
            and ctx["job"] in TARGETS, "recovery requires the exact managed execution failure")
    memo = {} if proof_memo is None else proof_memo
    job = ctx["job"]
    if job not in memo:
        try:
            try:
                evidence, _ = incidents.terminal_success(api, "deployment:" + job)
            except subprocess.CalledProcessError as error:
                # gh 2.96.0 emits this only after a successful, empty lookup.
                # HTTP/auth/signature/format faults remain loud.
                lines = (error.stderr or "").splitlines()
                absent = ([line for line in lines if line.startswith("Error:")] == ["Error: no attestations found"]
                          and any(re.fullmatch(r"✗ No attestations found for subject sha256:[0-9a-f]{64}", line) for line in lines))
                if deployment_id is None and error.returncode == 1 and error.cmd[:3] == ["gh", "attestation", "verify"] and absent:
                    raise NotEligible("latest deployment has no signed receipt") from error
                raise
            if evidence is None:
                raise NotEligible("recovery requires the latest signed verified deployment")
            record = api.get(f"repos/{incidents.REPOSITORY}/deployments/{evidence['proof']['record_id']}")
            require(record["id"] == evidence["proof"]["record_id"], "deployment read differs from signed evidence")
            memo[job] = evidence, record
        except Exception as error:
            # Reuse absence and faults too: a sweep never repeats a job lookup.
            memo[job] = error
    if isinstance(memo[job], Exception):
        raise memo[job]
    evidence, record = memo[job]
    latest_id = str(evidence["proof"]["record_id"])
    require(deployment_id is None or int(latest_id) == int(deployment_id),
            "recovery requires the latest signed verified deployment")
    failed_at = datetime.fromisoformat(alert["openTime"].replace("Z", "+00:00"))
    if not (incidents.instant(record["created_at"]) > failed_at and incidents.instant(evidence["time"]) > failed_at):
        raise NotEligible("verified recovery predates the failure")
    proof = executions.recovery_proof({"deployment_id": evidence["proof"]["record_id"],
        "status_id": evidence["proof"]["status_id"], "run_id": evidence["run_id"], "attempt": evidence["attempt"]})
    return alias, generation, proof


def read_alert(key, session, *, allow_missing=False):
    require(isinstance(key, str) and INCIDENT.fullmatch(key), "invalid incident ID")
    name = f"projects/{PROJECT}/alerts/{key}"
    response = session.get("https://monitoring.googleapis.com/v3/" + name, timeout=30)
    if allow_missing and response.status_code == 404:
        return None
    response.raise_for_status()
    alert = response.json()
    require(alert.get("name") == name and alert.get("state") in {"OPEN", "CLOSED"}, "invalid Monitoring alert")
    return alert


def reconcile(key, session, store, slack, identity, *, deployment_id="", api=None):
    alert = read_alert(key, session)
    require(alert.get("state") == "CLOSED" and alert.get("closeTime"), "Monitoring has not confirmed closure")
    if deployment_id:
        alias, generation, proof = verified_recovery(key, alert, store, identity, api, deployment_id)
        executions.report(store, slack, identity, alias["source"], failure=(key, True, ""), recovery=proof)
        store.save(key, {**alias, "closed": True}, generation)
        return
    deliver({"incident_id": key, "state": "closed", "policy_name": alert["policy"]["displayName"],
             "summary": "Closed by Monitoring. This does not establish dataset or deployment recovery."},
            store, slack, identity, create=False)


def selection(path, session, store, identity, api, budget, *, channel=None, proof_memo=None):
    """Read-only discovery, also used for live first-run impact measurement."""
    require(re.fullmatch(r"executions/[0-9a-f]{64}\.json", path), "invalid execution receipt path")
    raw, _ = store.read(path)
    require(isinstance(raw, dict), "missing execution receipt")
    recorded_identity = raw["identity"]
    require(isinstance(recorded_identity, dict) and set(recorded_identity) == {"team", "user", "channel"}
            and all(isinstance(value, str) for value in recorded_identity.values())
            and re.fullmatch(r"T[A-Z0-9]+", recorded_identity["team"])
            and re.fullmatch(r"U[A-Z0-9]+", recorded_identity["user"])
            and CHANNEL.fullmatch(recorded_identity["channel"])
            and (channel is None or recorded_identity["channel"] == channel), "incompatible execution bot identity")
    identity = recorded_identity if identity is None else identity
    ctx = executions.context(raw["source"])
    require(executions.path(ctx) == path, "execution receipt path differs from its source")
    executions.validate(raw, ctx, identity)
    shared = raw
    result = {"path": path, "source": ctx, "identity": identity, "job": ctx["job"], "execution": ctx["execution"],
              "status": "waiting", "reason": "", "proof": shared.get("recovery"), "recoveries": []}
    if not shared["failures"]:
        result.update(status="ignored", reason="no execution failures")
        return result
    from scripts.ingestion_targets import TARGETS
    if ctx["job"] not in TARGETS:
        result.update(status="ignored", reason="job is outside managed recovery targets")
        return result
    if any(not shared["decisions"].get(key, {}).get("resolved", False)
           for key in shared["reviews"] | shared["decisions"]):
        result["reason"] = "pending identity request"
        return result
    resolved = shared.get("recovery") and all(item["closed"] for item in shared["failures"].values())
    if resolved and shared["view"] == digest(executions.render(shared)):
        result.update(status="resolved", reason="recorded parent already acknowledged")
        return result
    memo = {} if proof_memo is None else proof_memo
    for key, failure in sorted(shared["failures"].items()):
        budget.take()
        if shared.get("recovery") and failure["closed"]:
            alias, generation = execution_alias(key, store, identity, shared)
        else:
            alert = read_alert(key, session, allow_missing=True)
            if alert is None or alert["state"] != "CLOSED":
                result["reason"] = "Monitoring incident unavailable (404)" if alert is None else "Monitoring incident is open"
                return result
            require(alert.get("closeTime"), "closed Monitoring incident lacks closeTime")
            try:
                alias, generation, proof = verified_recovery(key, alert, store, identity, api, proof_memo=memo, shared=shared)
            except NotEligible as error:
                result["reason"] = str(error)
                return result
            if result["proof"] is None:
                result["proof"] = proof
        result["recoveries"].append({"incident": key, "alias": alias, "generation": generation})
    result.update(status="eligible", reason="acknowledge recorded recovery" if resolved else "closed failures and later signed verified deployment")
    return result


def apply_selection(result, store, slack, identity):
    require(result["status"] == "eligible" and result["identity"] == identity, "cannot apply ineligible or foreign execution recovery")
    for item in result["recoveries"]:
        if not item["alias"]["closed"]:
            store.save(item["incident"], {**item["alias"], "closed": True}, item["generation"])
    # Close aliases first so an interrupted Slack acknowledgement cannot strand
    # open aliases behind an already acknowledged resolved parent.
    executions.report(store, slack, identity, result["source"], recovery=result["proof"],
                      closed_failures=[item["incident"] for item in result["recoveries"]])


def newest_parents(store):
    """One listing supplies names and update times; no traversal state is saved."""
    blobs = sorted(store.bucket.list_blobs(prefix="executions/"), key=lambda blob: (blob.updated, blob.name), reverse=True)
    return [blob.name for blob in blobs[:PARENT_LIMIT]], max(0, len(blobs) - PARENT_LIMIT)


def sweep(session, store, slack, identity, api):
    paths, outside_limit = newest_parents(store)
    logging.info("Execution recovery deferred %d parents beyond parent limit %d", outside_limit, PARENT_LIMIT)
    budget, errors, confirmed_identity, memo, edits = ReadBudget(), [], identity, {}, 0
    for index, path in enumerate(paths):
        if edits == EDIT_LIMIT:
            logging.info("Execution recovery deferred %d parents at edit limit %d", len(paths) - index, EDIT_LIMIT)
            break
        if budget.remaining == 0:
            logging.info("Execution recovery deferred %d parents at failure read budget %d", len(paths) - index, FAILURE_LIMIT)
            break
        try:
            result = selection(path, session, store, identity, api, budget,
                               channel=slack.channel if identity is None else identity["channel"], proof_memo=memo)
            logging.info("Execution recovery %s: %s (%s)", result["execution"], result["status"], result["reason"])
            if result["status"] == "eligible":
                edits += 1
                if confirmed_identity is None:
                    confirmed_identity = slack.identity()
                apply_selection(result, store, slack, confirmed_identity)
        except FailureBudgetExhausted:
            logging.info("Execution recovery deferred %d parents at failure read budget %d", len(paths) - index, FAILURE_LIMIT)
            break
        except Exception as error:
            # Isolate parent faults, then fail the run after other bounded work.
            # One corrupt historical parent must not starve every other parent.
            logging.error("Execution recovery failed for %s: %s", path, type(error).__name__)
            errors.append(error)
    if errors:
        raise ExceptionGroup("execution recovery faults", errors)


def automatic_authority(environment):
    repository = "SkyTruth/shared-datasets-1"
    require(environment.get("GITHUB_REF") == "refs/heads/main"
            and environment.get("GITHUB_REPOSITORY") == repository
            and environment.get("GITHUB_WORKFLOW_REF") == repository + "/.github/workflows/resolve-workflow-alert.yml@refs/heads/main"
            and environment.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
            and not any(environment.get(key) for key in ("INCIDENT_ID", "RECOVERY_DEPLOYMENT_ID", "ORIGINAL_RUN_ID", "ORIGINAL_MESSAGE_TS")),
            "automatic reconciliation requires trusted main with no caller-selected IDs")


def main():
    automatic = sys.argv[1:] == ["--automatic"]
    if automatic:
        automatic_authority(os.environ)
    else:
        require(len(sys.argv) == 2 and os.environ.get("GITHUB_REF") == "refs/heads/main"
                and os.environ.get("GITHUB_ACTOR") == "jonaraphael", "owner-only main reconciliation required")
    logging.basicConfig(level=logging.INFO)
    slack = Slack(os.environ["SHARED_DATASETS_SLACK_BOT_TOKEN"], os.environ["SHARED_DATASETS_SLACK_CHANNEL_ID"])
    credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    store = Store(storage.Client(project=PROJECT).bucket("skytruth-shared-datasets-1-monitoring-slack"))
    from scripts.dataset_mutation_authorization import GitHub

    if automatic:
        from scripts.monitoring_slack_sweep_api import BoundedGitHub
        sweep(AuthorizedSession(credentials), store, slack, None, BoundedGitHub())
    else:
        reconcile(sys.argv[1], AuthorizedSession(credentials), store, slack, slack.identity(),
                  deployment_id=os.environ.get("RECOVERY_DEPLOYMENT_ID", ""), api=GitHub())


if __name__ == "__main__":
    main()
