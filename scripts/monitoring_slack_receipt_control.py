"""Admit and verify protected, receipt-only reconciliation of one relay claim.

This caller reads deployment/Cloud Run/GCS evidence. Only the already deployed
controller performs its generation-CAS; this module has no Slack or GCS writes.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import deployment_revision as deployments
from scripts import monitoring_slack_deploy as deploy
from scripts import operational_retry
from scripts.github_api_read import read
from services.monitoring_slack import model, receipts
from services.monitoring_slack.run import Monitoring
from services.monitoring_slack.store import Store

REPOSITORY = "SkyTruth/shared-datasets-1"
WORKFLOW = ".github/workflows/monitoring-slack-relay-receipt.yml"
JOB = f"projects/{deploy.PROJECT}/locations/{deploy.REGION}/jobs/monitoring-slack-relay"
PROOF_SCHEMA = "monitoring-slack-receipt-control-v1"


class GitHub(deployments.GitHub):
    def get(self, path):
        return json.loads(read(path, no_cache=True))


def admit(api, executor, source_run, source_attempt, *, environment=None, bootstrap=False):
    """The control is a separate main-only authority, never a deployment target."""
    env = os.environ if environment is None else environment
    model.require(env.get("GITHUB_REPOSITORY") == REPOSITORY
                  and env.get("GITHUB_REF") == "refs/heads/main"
                  and env.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
                  and env.get("GITHUB_WORKFLOW_REF") == f"{REPOSITORY}/{WORKFLOW}@refs/heads/main",
                  "receipt control requires its exact main workflow")
    model.require(isinstance(executor, str) and re.fullmatch(r"[0-9a-f]{40}", executor)
                  and env.get("GITHUB_SHA") == env.get("GITHUB_WORKFLOW_SHA") == executor,
                  "controller must use its exact current main executor")
    run_id, attempt = env.get("GITHUB_RUN_ID", ""), env.get("GITHUB_RUN_ATTEMPT", "")
    model.require(re.fullmatch(r"[1-9][0-9]*", run_id) and attempt == "1",
                  "receipt control needs a new authenticated dispatch, not a rerun")
    repo = api.get(f"repos/{REPOSITORY}")
    workflow = api.get(f"repos/{REPOSITORY}/actions/workflows/{WORKFLOW.rsplit('/', 1)[1]}")
    current = api.get(f"repos/{REPOSITORY}/actions/runs/{run_id}/attempts/1")
    model.require(type(repo.get("id")) is int and repo["id"] > 0 and repo.get("full_name") == REPOSITORY
                  and type(workflow.get("id")) is int and workflow["id"] > 0 and workflow.get("path") == WORKFLOW,
                  "controller repository or workflow identity differs")
    model.require(current.get("id") == int(run_id) and current.get("run_attempt") == 1
                  and current.get("workflow_id") == workflow["id"] and current.get("path") == WORKFLOW
                  and current.get("head_sha") == executor and current.get("head_branch") == "main"
                  and current.get("event") == "workflow_dispatch"
                  and all(current.get(key, {}).get("id") == repo["id"]
                          and current.get(key, {}).get("full_name") == REPOSITORY
                          for key in ("repository", "head_repository")), "untrusted controller run identity")
    actor = current.get("actor", {})
    model.require(actor.get("login") == env.get("GITHUB_ACTOR") and actor.get("type") == "User"
                  and type(actor.get("id")) is int and actor["id"] > 0
                  and current.get("triggering_actor", actor) == actor, "controller actor is not the authenticated human")
    checkout = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    expected = env.get("BOOTSTRAP_WORKFLOW_SHA") if bootstrap else executor
    model.require(checkout == expected == executor and deployments.git_ancestor(executor, "origin/main"),
                  "controller checkout is not its merged immutable main executor")
    deployments.verify_ci(api, REPOSITORY, executor, source_run, source_attempt)
    return {"executor_sha": executor, "source_run_id": source_run, "source_run_attempt": source_attempt,
            "github_run_url": f"https://github.com/{REPOSITORY}/actions/runs/{run_id}",
            "actor": {key: actor[key] for key in ("login", "id", "type")}}


def installed_deployment(api, executor):
    rows = api.pages(f"repos/{REPOSITORY}/deployments?environment=production-monitoring-slack-relay&per_page=100")
    model.require(rows and all(type(row.get("id")) is int and row["id"] > 0 for row in rows)
                  and len({row["id"] for row in rows}) == len(rows), "deployment inventory is empty or ambiguous")
    for row in rows:
        payload = deployments.record_payload(row)
        model.require(payload["target"] == "monitoring-slack-relay", "foreign deployment target")
    latest = max(rows, key=lambda row: row["id"])
    payload = deployments.verify_record(api, REPOSITORY, latest)
    statuses = deployments.verified_statuses(api, REPOSITORY, latest)
    model.require(statuses and statuses[0].get("state") == "success"
                  and statuses[0].get("description") == "verified", "latest installed deployment is not terminal verified")
    model.require(latest["sha"] == executor, "controller source differs from installed verified source; reviewed migration required")
    model.require(deploy.IMAGE.fullmatch(payload["artifact"]), "installed artifact is not the exact relay image")
    return {"deployment_id": latest["id"], "status_id": statuses[0]["id"], "image": payload["artifact"]}


def job_config(raw, image):
    model.require(isinstance(raw, dict) and raw.get("name") == JOB
                  and raw.get("reconciling", False) is False and not raw.get("deleteTime"), "wrong or unsettled relay job")
    outer = raw.get("template", {})
    task = outer.get("template", {})
    containers = task.get("containers")
    model.require(type(outer.get("taskCount")) is int and outer["taskCount"] == 1
                  and type(outer.get("parallelism")) is int and outer["parallelism"] == 1
                  and task.get("serviceAccount") == deploy.RUNTIME
                  and type(task.get("maxRetries")) is int and task["maxRetries"] == 0
                  and isinstance(containers, list) and len(containers) == 1, "job execution contract changed")
    container = containers[0]
    model.require(container.get("image") == image and container.get("command") == ["python", "-m", "services.monitoring_slack.run"]
                  and container.get("args", []) == [], "job does not use the installed exact controller image")
    entries = container.get("env")
    model.require(isinstance(entries, list) and len(entries) == 1
                  and entries[0].keys() == {"name", "value"}
                  and entries[0]["name"] == "MONITORING_SLACK_CONFIG", "unexpected job environment")
    config = model.Config.parse(model.parse_json(entries[0]["value"].encode()))
    registry = deploy.registry()
    model.require(config["relay_image"] == image and config["relay_job"] == JOB
                  and config["project"] == deploy.PROJECT and config["state_bucket"] == deploy.BUCKET
                  and config.identity == {"channel": registry["slack_channel"], "team": registry["slack_team"], "user": registry["slack_user"]}
                  and config["policies"] == sorted([{"name": name, "kind": "health" if kind == "health" else "security"}
                                                   for kind, name in registry["policies"].items()], key=lambda item: item["name"])
                  and re.fullmatch(r"projects/(?:shared-datasets-1|[1-9][0-9]*)/secrets/" + deploy.SECRET_ID + r"/versions/[1-9][0-9]*", config["slack_secret_version"]),
                  "installed configuration differs from the reviewed relay scope")
    return config


def inventory(reader):
    """All pages including deleted entries; an HTTP error is never empty."""
    def snapshot():
        rows, names, tokens, token = [], set(), set(), None
        for _ in range(100):
            query = {"pageSize": 1000, "showDeleted": "true", **({"pageToken": token} if token else {})}
            value = reader._read(JOB + "/executions?" + urlencode(query), api="run")
            model.require(isinstance(value, dict) and value.keys() <= {"executions", "nextPageToken"}, "malformed execution inventory")
            page = value.get("executions", [])
            model.require(isinstance(page, list), "missing execution inventory")
            for row in page:
                name = row.get("name") if isinstance(row, dict) else None
                model.require(isinstance(name, str) and re.fullmatch(re.escape(JOB) + r"/executions/monitoring-slack-relay-[a-z0-9]{5}", name)
                              and name not in names, "wrong or duplicate execution resource")
                names.add(name)
                rows.append(row)
            model.require(len(rows) <= 5000, "execution inventory exceeds reviewed bound")
            token = value.get("nextPageToken", "")
            model.require(isinstance(token, str) and len(token) <= 8192 and token not in tokens, "incomplete execution pagination")
            if not token:
                return rows
            tokens.add(token)
        raise model.Invalid("execution inventory exceeds reviewed page bound")
    from scripts import gcp_read_retry
    return operational_retry.run_read(snapshot, classify=gcp_read_retry.classify, label="relay complete execution inventory")


def request(value, config):
    parsed = model.receipt_request(model.parse_json(value.encode()), config)
    model.require(model.canonical(parsed).decode() == value and "|" not in value, "request must be canonical JSON without argument delimiters")
    return parsed


def current(api, reader, executor):
    installed = installed_deployment(api, executor)
    with operational_retry.operation(profile=operational_retry.OBSERVER_READ):
        raw = reader.read(JOB, api="run")
    config = job_config(raw, installed["image"])
    # Monitoring.execution enforces this exact verified job boundary itself.
    reader.config = config
    return installed, config


def prepare(api, reader, store, identity, request_json, *, clock=model.utc_now):
    installed, config = current(api, reader, identity["executor_sha"])
    with operational_retry.operation(profile=operational_retry.OBSERVER_READ):
        wanted = request(request_json, config)
        rows = inventory(reader)
        snapshot = store.load()
        model.require(snapshot.value is not None, "relay state is missing")
        state = model.validate_state(snapshot.value, config, clock())
        model.require(wanted["request_id"] not in state["receipts"], "receipt is already confirmed; inspect the recorded audit without another execution")
        model.require(snapshot.generation == wanted["expected_generation"], "exact state generation changed")
        episode = state["episodes"].get(wanted["alert_name"])
        operation = episode["operation"] if episode else None
        model.require(operation is not None and operation["id"] == wanted["operation_id"]
                      and operation["sha256"] == wanted["operation_sha256"], "exact pending operation changed")
        model.require(operation["phase"] == ("claimed" if wanted["outcome"] == "confirm_not_delivered" else "sending"), "requested outcome cannot be established from the claim phase")
        name = operation["writer"]["execution"]
        model.require(sum(row["name"] == name for row in rows) == 1, "claimed writer is absent from complete execution inventory")
        raw = reader.execution(name)
        writer = receipts.execution_proof(raw, config, name, clock(), args=[], terminal=True)
        model.require(writer == operation["writer"], "terminal execution differs from the exact claim writer")
        model.require(model.instant(operation["started_at"]) <= model.instant(raw["completionTime"]),
                      "writer completion predates the claimed operation")
        return {"schema": PROOF_SCHEMA, "identity": identity, "installed": installed, "config": config.value,
                "request": wanted, "writer": writer, "writer_completed_at": raw["completionTime"], "prepared_at": clock()}


def verify(api, reader, store, identity, request_json, prepared, *, clock=model.utc_now):
    model.require(isinstance(prepared, dict) and prepared.keys() == {"schema", "identity", "installed", "config", "request", "writer", "writer_completed_at", "prepared_at"}
                  and prepared["schema"] == PROOF_SCHEMA and prepared["identity"] == identity, "prepared control context changed")
    installed, config = current(api, reader, identity["executor_sha"])
    with operational_retry.operation(profile=operational_retry.OBSERVER_READ):
        wanted = request(request_json, config)
        model.require(installed == prepared["installed"] and config.value == prepared["config"]
                      and wanted == prepared["request"], "installed deployment, config or request changed during control")
        args = ["--reconcile-receipt-json", request_json]
        candidates = []
        for row in inventory(reader):
            containers = row.get("template", {}).get("containers", [])
            if len(containers) == 1 and containers[0].get("args") == args:
                env = containers[0].get("env", [])
                if {"name": "SHARED_DATASETS_GITHUB_ACTIONS_RUN_URL", "value": identity["github_run_url"]} in env:
                    candidates.append(row["name"])
        model.require(len(candidates) == 1, "control execution is missing or ambiguous")
        raw = reader.execution(candidates[0])
        controller = receipts.execution_proof(raw, config, candidates[0], clock(), args=args,
                                              github_run_url=identity["github_run_url"], terminal=True)
        model.require(any(row.get("type") == "Completed" and row.get("state") == "CONDITION_SUCCEEDED" for row in raw["conditions"])
                      and raw.get("succeededCount") == 1
                      and model.instant(controller["created_at"]) >= model.instant(prepared["prepared_at"]), "controller did not finish this exact request successfully")
        writer_raw = reader.execution(prepared["writer"]["execution"])
        model.require(receipts.execution_proof(writer_raw, config, prepared["writer"]["execution"], clock(), args=[], terminal=True) == prepared["writer"]
                      and writer_raw["completionTime"] == prepared["writer_completed_at"], "original writer terminal proof changed")
        snapshot = store.load()
        model.require(snapshot.value is not None, "receipt state is missing")
        state = model.validate_state(snapshot.value, config, clock())
        audit = state["receipts"].get(wanted["request_id"])
        model.require(audit is not None and audit["request"] == wanted and audit["request_sha256"] == model.digest(wanted)
                      and audit["controller"] == controller and audit["github_run_url"] == identity["github_run_url"]
                      and audit["writer"] == prepared["writer"] and audit["writer_completed_at"] == prepared["writer_completed_at"]
                      and snapshot.generation != wanted["expected_generation"]
                      and model.instant(audit["confirmed_at"]) <= model.instant(raw["completionTime"]), "terminal execution has no exact durable accepted receipt")
        operational_retry.request_timeout(15)  # GCS reads cannot admit a snapshot after its shared deadline.
        return {"verified": True, "public_operations": 0, "receipt": audit, "state_generation": snapshot.generation,
                "controller": controller, "actor": identity["actor"], "installed": installed}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("admit", "prepare", "verify"))
    parser.add_argument("--executor-sha", required=True)
    parser.add_argument("--source-run-id", type=int, required=True)
    parser.add_argument("--source-run-attempt", type=int, required=True)
    parser.add_argument("--bootstrap", action="store_true")
    parser.add_argument("--request-json")
    parser.add_argument("--prepared-proof", type=Path)
    args = parser.parse_args(argv)
    model.require((args.command == "admit") == (args.request_json is None and args.prepared_proof is None)
                  and (not args.bootstrap or args.command == "admit"), "invalid control command fields")
    api = GitHub()
    identity = admit(api, args.executor_sha, args.source_run_id, args.source_run_attempt, bootstrap=args.bootstrap)
    if args.command == "admit":
        print(model.canonical(identity).decode())
        return
    model.require(args.request_json is not None and args.prepared_proof is not None, "request and proof path are required")
    from google.cloud import storage
    reader = Monitoring(deploy.session(), None)
    store = Store(storage.Client(project=deploy.PROJECT), deploy.BUCKET)
    if args.command == "prepare":
        proof = prepare(api, reader, store, identity, args.request_json, clock=model.utc_now)
        args.prepared_proof.write_bytes(model.canonical(proof))
        deploy.output("request_json", args.request_json)
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with Path(summary).open("a") as stream:
                stream.write("### Receipt-only request\n\n```json\n" + json.dumps(proof, indent=2) + "\n```\nNo Slack messages, dataset writes or health success are authorized.\n")
    else:
        proof = model.parse_json(args.prepared_proof.read_bytes())
        result = verify(api, reader, store, identity, args.request_json, proof, clock=model.utc_now)
        print(model.canonical(result).decode())


if __name__ == "__main__":
    main()
