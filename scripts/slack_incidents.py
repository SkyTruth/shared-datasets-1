"""Reconcile signed operational evidence with persistent Slack incident threads.

Incident records are notification bookkeeping, never deployment authority. The
worker prepares/signs claims before Slack posts, then signs delivery receipts even
when a later delivery fails. A claimed post is not automatically repeated.
"""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import yaml

from scripts import deployment_emission as emissions
from scripts import deployment_revision as deployments
from scripts import workflow_failure_alert as alerts
from scripts.slack_incident_api import CHANNEL, TIMESTAMP, Slack
from scripts.slack_notify import build_slack_payload

SCHEMA = "shared-datasets-slack-incident-v1"
ENVIRONMENT = "slack-incidents"
WORKFLOW = ".github/workflows/unattended-workflow-alert.yml"
REPOSITORY = alerts.REPOSITORY
ROOT = Path(__file__).resolve().parents[1]
RUNTIME_TARGETS = {"wdpa-monthly", "wdpa-processing-validation", "eamlis-monthly", "sea-ice-daily"}


class IncidentError(RuntimeError):
    pass


def require(value, message):
    if not value:
        raise IncidentError(message)


def instant(value):
    require(isinstance(value, str) and re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", value), "invalid operational timestamp")
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def positive(value):
    return type(value) is int and value > 0


def escape(value):
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def observation_key(value):
    proof = value["proof"]
    return f"{value['run_id']}:{value['attempt']}:{value['job_id']}:{proof['status_id'] if proof else 0}"


def validate_observation(value):
    require(isinstance(value, dict) and set(value) == {
        "scope", "kind", "label", "run_id", "attempt", "sha", "time", "job_id", "job_name", "conclusion", "proof",
    }, "invalid incident observation schema")
    require(value["kind"] in {"deployment", "workflow", "job", "publication"}, "invalid incident kind")
    require(re.fullmatch(r"[a-z][a-z0-9:._-]{0,150}", value["scope"]), "invalid incident scope")
    require(positive(value["run_id"]) and positive(value["attempt"]), "invalid observation attempt")
    require(value["job_id"] == 0 or positive(value["job_id"]), "invalid observation job ID")
    require(deployments.SHA.fullmatch(value["sha"]), "invalid observation revision")
    require(all(isinstance(value[key], str) and 0 < len(value[key]) <= 500 for key in ("label", "job_name")), "invalid observation display text")
    instant(value["time"])
    require(value["conclusion"] in alerts.FAILED_CONCLUSIONS | {"success"}, "nonterminal/skipped observation")
    proof = value["proof"]
    if proof is not None:
        require(value["kind"] == "deployment" and set(proof) == {"record_id", "status_id", "artifact"}
                and positive(proof["record_id"]) and positive(proof["status_id"])
                and re.fullmatch(r"[a-z0-9./_-]+@sha256:[0-9a-f]{64}", proof["artifact"]), "invalid terminal deployment proof")
    if value["kind"] == "deployment" and value["conclusion"] == "success":
        require(proof is not None, "deployment recovery requires a signed terminal receipt")
    return value


def job_targets(root=ROOT):
    """Derive complete registered job paths from the checked-in caller graph.

    Resolving leaf names alone would conflate IAM bootstrap and sync or different
    ingestion targets. Unregistered jobs retain their own exact job scope.
    """
    targets = {}
    reverse = {workflow: target for target, workflow in deployments.TARGET_WORKFLOWS.items()}

    def walk(filename, prefix="", inherited=None, inputs=None, source=None):
        source = source or filename
        document = yaml.safe_load((root / ".github/workflows" / filename).read_text())
        for key, job in document["jobs"].items():
            name = job.get("name", key)
            for input_name, value in (inputs or {}).items():
                name = name.replace("${{ inputs." + input_name + " }}", str(value))
            if name == "Publish PR #${{ matrix.pr_number }}":
                name = "Publish PR #<PR>"
            if "${{" in name:
                continue  # Reviewed publication matrix has a separate PR identity.
            path = prefix + name
            caller = job.get("uses", "")
            if caller.startswith("./.github/workflows/"):
                leaf = caller.rsplit("/", 1)[-1]
                supplied = job.get("with", {})
                target = reverse.get(leaf)
                if leaf == "prod-terraform-target-apply.yml":
                    sync = supplied.get("sync_name")
                    require(sync in deployments.TERRAFORM_SYNCS, "unregistered Terraform synchronization name")
                    target = "terraform-" + hashlib.sha256(sync.encode()).hexdigest()[:16]
                walk(leaf, path + " / ", target or inherited, supplied, source)
            elif inherited:
                targets[(source, path)] = inherited

    walk("ci.yml")
    for filename, target in reverse.items():
        walk(filename, inherited=target)
    for filename in set(deployments.TERRAFORM_WORKFLOWS.values()):
        walk(filename)
    return targets


def registered_name(name):
    return re.sub(r"^Publish PR #[1-9][0-9]* / ", "Publish PR #<PR> / ", name)


def scope_for_job(name, targets, workflow):
    registered = registered_name(name)
    if (workflow, registered) in targets:
        target = targets[(workflow, registered)]
        label = next((name for name in deployments.TERRAFORM_SYNCS if "terraform-" + hashlib.sha256(name.encode()).hexdigest()[:16] == target), target)
        return "deployment:" + target, "deployment", label
    mutation = re.fullmatch(r"(?:Publish PR #([1-9][0-9]*) / )?(Apply approved PR mutation plans \(PR #([1-9][0-9]*)\)|Install reviewed feature-ID reset)", name)
    if mutation and (mutation[3] or mutation[1]) and workflow in {"ci.yml", "publish-dataset.yml"}:
        pr = mutation[3] or mutation[1]
        require(pr is not None and (mutation[1] is None or mutation[1] == pr), "publication job has ambiguous PR identity")
        phase = "apply" if mutation[2].startswith("Apply") else "reset"
        return f"publication:pr-{pr}:{phase}", "publication", f"Dataset PR #{pr} ({phase})"
    return "job:" + hashlib.sha256((workflow + ":" + name).encode()).hexdigest(), "job", name


def from_job(run, job, targets):
    scope, kind, label = scope_for_job(job["name"], targets, alerts.WORKFLOW_PATHS[run["name"]])
    return validate_observation({
        "scope": scope, "kind": kind, "label": label, "run_id": run["id"], "attempt": run["run_attempt"],
        "sha": run["head_sha"], "time": job["completed_at"], "job_id": job["id"], "job_name": job["name"],
        "conclusion": job["conclusion"], "proof": None,
    })


def from_workflow(run):
    return validate_observation({
        "scope": "workflow:" + alerts.WORKFLOW_PATHS[run["name"]], "kind": "workflow", "label": run["name"],
        "run_id": run["id"], "attempt": run["run_attempt"], "sha": run["head_sha"],
        "time": run["updated_at"], "job_id": 0, "job_name": run["name"], "conclusion": run["conclusion"], "proof": None,
    })


def source_observations(api, event, targets):
    run = event["workflow_run"]
    if not alerts.verify_source_event(api, run):
        return []
    if run["conclusion"] not in alerts.FAILED_CONCLUSIONS | {"success"}:
        return []
    jobs = api.pages(f"repos/{REPOSITORY}/actions/runs/{run['id']}/attempts/{run['run_attempt']}/jobs?per_page=100", field="jobs")
    if run["name"] == "CI":
        if run["event"] != "push":
            return []
        failures = alerts.failed_ci_delivery_jobs(jobs)
        # Successful job evidence can close only the exact non-deployment job;
        # a deployment job success must also have its signed terminal record.
        successes = [job for job in jobs if job["status"] == "completed" and job["conclusion"] == "success"
                     and job["name"] not in alerts.VALIDATION_JOBS and ("ci.yml", registered_name(job["name"])) not in targets]
        return [from_job(run, job, targets) for job in failures + successes]
    if alerts.alert_for_run(event) is None and run["conclusion"] != "success":
        return []  # Supervised manual failures keep existing GitHub-only routing.
    if run["name"] in {"Bucket hygiene audit", "Scratch cleanup audit"}:
        return [from_workflow(run)]
    failures = [job for job in jobs if job["status"] == "completed" and job["conclusion"] in alerts.FAILED_CONCLUSIONS]
    # Observer failures need their own exact job scope. A healthy observer with
    # no verification matrix cannot cover a previously failed matrix record.
    successful = [job for job in jobs if job["status"] == "completed" and job["conclusion"] == "success"
                  and (alerts.WORKFLOW_PATHS[run["name"]], registered_name(job["name"])) not in targets]
    result = [from_job(run, job, targets) for job in failures + successful]
    if not failures or run["conclusion"] == "success":
        result.append(from_workflow(run))  # Includes startup failures with no allocated jobs.
    if run["name"] == "Approved dataset mutation":
        prs = {match[1] for job in jobs if (match := re.fullmatch(r"Apply approved PR mutation plans \(PR #([1-9][0-9]*)\)", job["name"]))}
        if len(prs) == 1:
            for item in result:
                if item["job_name"] == "Install reviewed feature-ID reset":
                    pr = next(iter(prs))
                    item.update(scope=f"publication:pr-{pr}:reset", kind="publication", label=f"Dataset PR #{pr} (reset)")
    return result


def replay_observations(api, targets, since, *, now=None):
    """Hourly observation catches bounded queue loss without another scheduler.

    Reconcile the last 24 hours, starting no earlier than explicit activation.
    Longer GitHub outages need an explicit replay, documented beside rollout.
    """
    now = now or datetime.now(timezone.utc)
    start = max(instant(since), now - timedelta(hours=24))
    query = urlencode({"branch": "main", "created": ">=" + start.strftime("%Y-%m-%dT%H:%M:%SZ"), "per_page": 100})
    runs = api.pages(f"repos/{REPOSITORY}/actions/runs?{query}", field="workflow_runs")
    observations = []
    for run in runs:
        if (run.get("status") == "completed" and run.get("name") in alerts.WORKFLOW_PATHS
                and run.get("head_branch") == "main" and run.get("head_repository", {}).get("full_name") == REPOSITORY):
            observations.extend(source_observations(api, {"repository": {"full_name": REPOSITORY}, "workflow_run": run}, targets))
    return observations


def terminal_success(api, scope):
    target = scope.removeprefix("deployment:")
    require(target in deployments.TARGET_WORKFLOWS | deployments.TERRAFORM_WORKFLOWS, "unregistered recovery target")
    records = api.pages(f"repos/{REPOSITORY}/deployments?environment=production-{target}&per_page=100")
    if not records:
        return None
    record = max(records, key=lambda item: item["id"])
    payload = deployments.verify_record(api, REPOSITORY, record)
    statuses = deployments.verified_statuses(api, REPOSITORY, record)
    phases = {"verified"} if target in RUNTIME_TARGETS else {"applied", "verified"}
    if not statuses or statuses[0]["state"] != "success" or statuses[0]["description"] not in phases:
        return None
    return terminal_observation(scope, record, payload, statuses[0])


def terminal_observation(scope, record, payload, status):
    target = scope.removeprefix("deployment:")
    phases = {"verified"} if target in RUNTIME_TARGETS else {"applied", "verified"}
    require(payload["target"] == target and status["state"] == "success" and status["description"] in phases,
            "receipt does not prove this target's terminal success")
    emitter = re.fullmatch(r"https://github\.com/" + re.escape(REPOSITORY) + r"/actions/runs/([1-9][0-9]*)/attempts/([1-9][0-9]*)", status["log_url"])
    require(emitter is not None, "terminal evidence has no exact emitter")
    return validate_observation({
        "scope": scope, "kind": "deployment", "label": target,
        "run_id": int(emitter[1]), "attempt": int(emitter[2]), "sha": record["sha"],
        "time": status["created_at"], "job_id": 0, "job_name": target, "conclusion": "success",
        "proof": {"record_id": record["id"], "status_id": status["id"], "artifact": payload["artifact"]},
    })


def verify_saved_success(api, observation):
    """Reverify historical recovery without confusing it with current health."""
    validate_observation(observation)
    proof = observation["proof"]
    require(observation["kind"] == "deployment" and observation["conclusion"] == "success" and proof is not None,
            "saved recovery must identify a terminal deployment receipt")
    record = api.get(f"repos/{REPOSITORY}/deployments/{proof['record_id']}")
    payload = deployments.verify_record(api, REPOSITORY, record)
    statuses = api.pages(f"repos/{REPOSITORY}/deployments/{record['id']}/statuses?per_page=100")
    matches = [status for status in statuses if status["id"] == proof["status_id"]]
    require(len(matches) == 1, "saved recovery receipt is unavailable or ambiguous")
    deployments.verify_receipt(api, REPOSITORY, record, matches[0])
    require(terminal_observation(observation["scope"], record, payload, matches[0]) == observation,
            "saved recovery differs from its exact signed receipt")


def new_state(observation, identity):
    return {"scope": observation["scope"], "kind": observation["kind"], "label": observation["label"],
            "identity": identity, "healthy": None, "incident": None, "deferred": []}


def fold(state, observation, identity, *, ancestor=deployments.git_ancestor):
    validate_observation(observation)
    state = copy.deepcopy(state or new_state(observation, identity))
    require(state["scope"] == observation["scope"] and state["kind"] == observation["kind"], "observation scope changed")
    require(state["identity"] == identity, "incident belongs to another Slack bot, workspace or channel")
    incident = state["incident"]
    if observation["conclusion"] == "success":
        # Healthy runs do not create a ledger or a Slack message by themselves.
        if incident is None:
            return state
        if incident["resolution"] is not None:
            healthy = state["healthy"]
            if instant(observation["time"]) > instant(healthy["time"]) and ancestor(healthy["sha"], observation["sha"]):
                state["healthy"] = observation
            return state
        failures = incident["failures"]
        if not all(ancestor(item["sha"], observation["sha"]) for item in failures):
            return state
        if any(instant(observation["time"]) <= instant(item["time"])
               and (state["kind"] != "deployment" or item["sha"] == observation["sha"]) for item in failures):
            return state
        if state["kind"] not in {"deployment", "publication"}:
            require(observation["job_name"] == failures[0]["job_name"], "successful job does not cover the incident")
        incident["resolution"] = observation
        state["healthy"] = observation
        return state
    if state["healthy"]:
        healthy = state["healthy"]
        if instant(observation["time"]) <= instant(healthy["time"]):
            return state
        if observation["sha"] != healthy["sha"] and ancestor(observation["sha"], healthy["sha"]):
            return state  # A superseded execution can finish after recovery.
    if incident is None or incident["resolution"] is not None:
        digest = hashlib.sha256((state["scope"] + ":" + observation_key(observation)).encode()).hexdigest()[:12].upper()
        state["incident"] = {"id": "SD-" + digest, "failures": [observation], "resolution": None, "posts": {}, "permalink": ""}
    elif observation_key(observation) not in {observation_key(item) for item in incident["failures"]}:
        incident["failures"].append(observation)
        incident["failures"].sort(key=lambda item: (instant(item["time"]), observation_key(item)))
    return state


def validate_state(state):
    require(isinstance(state, dict) and set(state) == {"scope", "kind", "label", "identity", "healthy", "incident", "deferred"}, "invalid incident state schema")
    identity = state["identity"]
    require(isinstance(identity, dict) and set(identity) == {"channel", "team", "user"}
            and CHANNEL.fullmatch(identity["channel"]) and re.fullmatch(r"T[A-Z0-9]+", identity["team"])
            and re.fullmatch(r"U[A-Z0-9]+", identity["user"]), "invalid incident Slack identity")
    if state["healthy"] is not None:
        validate_observation(state["healthy"])
        require(state["healthy"]["scope"] == state["scope"] and state["healthy"]["conclusion"] == "success", "invalid healthy watermark")
    require(isinstance(state["deferred"], list), "invalid deferred observation queue")
    deferred_events = set()
    for item in state["deferred"]:
        validate_observation(item)
        require(item["scope"] == state["scope"] and observation_key(item) not in deferred_events, "incompatible/duplicate deferred observation")
        deferred_events.add(observation_key(item))
    incident = state["incident"]
    require(isinstance(incident, dict) and set(incident) == {"id", "failures", "resolution", "posts", "permalink"}, "invalid incident episode")
    require(re.fullmatch(r"SD-[0-9A-F]{12}", incident["id"]), "invalid incident ID")
    require(isinstance(incident["failures"], list) and incident["failures"], "incident must retain original failures")
    seen = set()
    for item in incident["failures"]:
        validate_observation(item)
        require(item["scope"] == state["scope"] and item["kind"] == state["kind"]
                and item["conclusion"] in alerts.FAILED_CONCLUSIONS and observation_key(item) not in seen, "incompatible/duplicate incident failure")
        seen.add(observation_key(item))
    if incident["resolution"] is not None:
        validate_observation(incident["resolution"])
        require(incident["resolution"]["scope"] == state["scope"] and incident["resolution"]["conclusion"] == "success"
                and state["healthy"] is not None and instant(state["healthy"]["time"]) >= instant(incident["resolution"]["time"]), "invalid incident recovery")
    require(isinstance(incident["posts"], dict), "invalid Slack outbox")
    allowed = desired_posts(state)
    for key, post in incident["posts"].items():
        require(key in allowed and isinstance(post, dict) and set(post) == {"state", "ts"}
                and post["state"] in {"claimed", "delivered"}, "invalid Slack delivery operation")
        require((post["state"] == "claimed" and post["ts"] is None)
                or (post["state"] == "delivered" and TIMESTAMP.fullmatch(str(post["ts"]))), "invalid Slack delivery acknowledgement")
    link = incident["permalink"]
    require(isinstance(link, str), "invalid incident permalink")
    if link:
        parent = incident["posts"].get("parent")
        require(parent and parent["state"] == "delivered" and re.fullmatch(
            r"https://[a-z0-9-]+\.slack\.com/archives/" + identity["channel"] + "/p" + parent["ts"].replace(".", ""), link), "permalink does not identify the parent")
    return state


def desired_posts(state):
    incident = state["incident"]
    posts = {"parent": None}
    # The initial failure is already recorded in the parent. Subsequent attempts
    # remain threaded, including attempts discovered by reconciliation.
    for failure in incident["failures"][1:]:
        posts["attempt:" + observation_key(failure)] = failure
    if incident["resolution"] is not None:
        posts["resolved:" + observation_key(incident["resolution"])] = incident["resolution"]
    return posts


def run_link(observation):
    return emissions.invocation(REPOSITORY, observation["run_id"], observation["attempt"])


def render(state, *, reply=None):
    incident = state["incident"]
    first, latest = incident["failures"][0], incident["failures"][-1]
    resolution = incident["resolution"]
    if reply and reply["conclusion"] != "success":
        title = f"Another failed attempt · {incident['id']}"
        body = f"{escape(reply['job_name'])}: {reply['conclusion']}.\n<{run_link(reply)}|Attempt {reply['attempt']}> · `{reply['sha'][:12]}` · {reply['time']}"
        status = "error"
    else:
        status = "success" if resolution else "error"
        title = f"{'Resolved' if resolution else 'Open'} · {state['label']} · {incident['id']}"
        body = f"*Original failure:* {escape(first['job_name'])} ({first['conclusion']})\n<{run_link(first)}|Failed attempt> · `{first['sha'][:12]}` · {first['time']}\n*Failed attempts:* {len(incident['failures'])}"
        if latest != first:
            body += f"\n<{run_link(latest)}|Latest failed attempt> · {latest['time']}"
        if resolution:
            body += f"\n*Recovery verified:* {resolution['time']}\n<{run_link(resolution)}|Recovery evidence> · `{resolution['sha'][:12]}`"
            if resolution["proof"]:
                body += f"\nDeployment record {resolution['proof']['record_id']} · `{resolution['proof']['artifact']}`"
        if reply:
            title = f"Recovered · {state['label']} · {incident['id']}"
            body = f"Recovery verified at {resolution['time']}.\n<{run_link(resolution)}|Successful recovery> · `{resolution['sha'][:12]}`\n{len(incident['failures'])} failed attempt(s); original evidence remains in this thread."
    payload = build_slack_payload(title=title, body=body, status=status, emoji="✅" if status == "success" else "🔴")
    # Labels may originate in job names; escape the notification fallback too.
    payload["text"] = escape(title) + "\n" + body
    payload["metadata"] = {"event_type": "shared_datasets_incident", "event_payload": {"incident_id": incident["id"], "scope": state["scope"]}}
    return payload


def worker_context(api, environment=os.environ):
    require(environment.get("GITHUB_REPOSITORY") == REPOSITORY and environment.get("GITHUB_REF") == "refs/heads/main"
            and environment.get("GITHUB_WORKFLOW_REF") == REPOSITORY + "/" + WORKFLOW + "@refs/heads/main", "incidents require the trusted main workflow")
    run_id, attempt = int(environment["GITHUB_RUN_ID"]), int(environment["GITHUB_RUN_ATTEMPT"])
    run = api.get(f"repos/{REPOSITORY}/actions/runs/{run_id}/attempts/{attempt}")
    repo = api.get(f"repos/{REPOSITORY}")
    workflow = api.get(f"repos/{REPOSITORY}/actions/workflows/{WORKFLOW.rsplit('/', 1)[-1]}")
    require(run.get("id") == run_id and run.get("run_attempt") == attempt and run.get("path") == WORKFLOW
            and run.get("workflow_id") == workflow.get("id") and workflow.get("path") == WORKFLOW
            and run.get("head_branch") == "main" and run.get("event") in {"workflow_run", "workflow_dispatch"}
            and deployments.SHA.fullmatch(str(run.get("head_sha", "")))
            and all(run.get(key, {}).get("id") == repo.get("id") and run.get(key, {}).get("full_name") == REPOSITORY for key in ("repository", "head_repository")), "invalid incident worker provenance")
    return run


class Ledger:
    def __init__(self, api, run, directory):
        self.api, self.run, self.directory = api, run, directory

    def verify(self, record):
        payload = record["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        require(isinstance(payload, dict) and set(payload) == {"schema", "state", "execution_run_id", "execution_run_attempt"}
                and payload["schema"] == SCHEMA and record.get("task") == "slack-incident"
                and record.get("environment") == ENVIRONMENT and record.get("production_environment") is False
                and record.get("ref") == record.get("sha") and deployments.SHA.fullmatch(str(record.get("sha", "")))
                and record.get("creator", {}).get("login") == "github-actions[bot]", "invalid notification ledger identity")
        state = validate_state(payload["state"])
        statuses = self.api.pages(f"repos/{REPOSITORY}/deployments/{record['id']}/statuses?per_page=100")
        require(len(statuses) == 1 and statuses[0].get("description") == "applied" and statuses[0].get("state") == "success", "notification checkpoint is incomplete; reconcile before delivery")
        # The signed bytes bind the channel, parent timestamp, outbox claims and
        # observation history. A forged Actions-bot row cannot suppress alerts.
        emissions.verify(self.api, REPOSITORY, record, statuses[0], original_signer=WORKFLOW, batch=True)
        return state

    def load(self):
        records = self.api.pages(f"repos/{REPOSITORY}/deployments?environment={ENVIRONMENT}&task=slack-incident&per_page=100")
        latest = {}
        for record in records:
            payload = record["payload"]
            if isinstance(payload, str):
                payload = json.loads(payload)
            require(isinstance(payload, dict) and payload.get("schema") == SCHEMA, "unknown notification ledger schema")
            scope = payload["state"]["scope"]
            if scope not in latest or record["id"] > latest[scope]["id"]:
                latest[scope] = record
        return {scope: self.verify(record) for scope, record in latest.items()}

    def save(self, state, phase):
        validate_state(state)
        payload = {"schema": SCHEMA, "state": state,
                   "execution_run_id": self.run["id"], "execution_run_attempt": self.run["run_attempt"]}
        record = self.api.post(f"repos/{REPOSITORY}/deployments", {
            "ref": self.run["head_sha"], "task": "slack-incident", "environment": ENVIRONMENT,
            "auto_merge": False, "required_contexts": [], "production_environment": False,
            "transient_environment": False, "description": state["incident"]["id"] + " notification checkpoint", "payload": payload,
        })
        require(positive(record.get("id")) and record.get("payload") == payload, "ledger write was not acknowledged exactly")
        status = self.api.post(f"repos/{REPOSITORY}/deployments/{record['id']}/statuses", {
            "state": "success", "description": "applied", "auto_inactive": False, "environment_url": "",
            "log_url": emissions.invocation(REPOSITORY, self.run["id"], self.run["run_attempt"]),
        })
        value = emissions.receipt(REPOSITORY, record, status)
        directory = self.directory / phase
        directory.mkdir(parents=True, exist_ok=True)
        (directory / emissions.name(value)).write_bytes(emissions.canonical(value))
        output = os.environ.get("GITHUB_OUTPUT")
        if output:
            with Path(output).open("a") as stream:
                stream.write(f"{phase}=true\n")
        return record


def seal_checkpoint(ledger, identity, incident_id, operation):
    """Owner-reviewed recovery of an interrupted checkpoint, without Slack sends.

    The exact payload hash is a review receipt, not a substitute for its missing
    signature. The owner's main-only dispatch creates a new signed checkpoint;
    claimed posts still require separate delivery reconciliation.
    """
    require(ledger.run["event"] == "workflow_dispatch" and ledger.run.get("actor", {}).get("login") == "jonaraphael", "checkpoint reconciliation requires the repository owner on trusted main")
    match = re.fullmatch(r"checkpoint:([1-9][0-9]*):([0-9a-f]{64})", operation)
    require(match is not None, "require checkpoint:ID:SHA256 after reviewing the exact checkpoint")
    record = ledger.api.get(f"repos/{REPOSITORY}/deployments/{match[1]}")
    payload = record["payload"]
    if isinstance(payload, str):
        payload = json.loads(payload)
    require(isinstance(payload, dict) and payload.get("schema") == SCHEMA
            and hashlib.sha256(emissions.canonical(payload)).hexdigest() == match[2], "checkpoint differs from the owner's reviewed payload")
    state = validate_state(payload["state"])
    require(state["incident"]["id"] == incident_id and state["identity"] == identity
            and record.get("task") == "slack-incident" and record.get("environment") == ENVIRONMENT
            and record.get("production_environment") is False, "checkpoint belongs to another incident or Slack identity")
    latest = ledger.api.pages(f"repos/{REPOSITORY}/deployments?environment={ENVIRONMENT}&task=slack-incident&per_page=100")
    same_scope = [item for item in latest if (json.loads(item["payload"]) if isinstance(item["payload"], str) else item["payload"])["state"]["scope"] == state["scope"]]
    require(same_scope and max(item["id"] for item in same_scope) == record["id"], "checkpoint is no longer the latest incident state")
    # Recovery truth is independently rechecked even when the owner confirms
    # notification delivery bookkeeping from an interrupted writer.
    resolution = state["incident"]["resolution"]
    if resolution and state["kind"] == "deployment":
        verify_saved_success(ledger.api, resolution)
        healthy = state["healthy"]
        if healthy != resolution:
            verify_saved_success(ledger.api, healthy)
        require(all(deployments.git_ancestor(item["sha"], resolution["sha"]) for item in state["incident"]["failures"])
                and deployments.git_ancestor(resolution["sha"], healthy["sha"]), "checkpoint recovery has incompatible revision ancestry")
    ledger.save(state, "claims")
    return []


def prepare(ledger, identity, observations, *, ancestor=deployments.git_ancestor, success=terminal_success, on_saved=lambda _: None):
    states = ledger.load()
    original = copy.deepcopy(states)
    blocked = {scope for scope, state in states.items() if any(item["state"] == "claimed" for item in state["incident"]["posts"].values())}
    replay = list(observations)
    for scope, state in states.items():
        require(state["identity"] == identity, "configured Slack identity differs from the incident registry")
        if scope not in blocked:
            replay.extend(state["deferred"])
            state["deferred"] = []
    for observation in sorted(replay, key=lambda item: (instant(item["time"]), observation_key(item))):
        validate_observation(observation)
        scope = observation["scope"]
        if scope in blocked:
            state = states[scope]
            known = state["incident"]["failures"] + state["deferred"]
            if state["incident"]["resolution"]:
                known.append(state["incident"]["resolution"])
            if observation_key(observation) not in {observation_key(item) for item in known}:
                state["deferred"].append(observation)
            continue
        if observation["conclusion"] == "success" and observation["scope"] not in states:
            continue
        states[observation["scope"]] = fold(states.get(observation["scope"]), observation, identity, ancestor=ancestor)
    for scope, state in list(states.items()):
        if scope not in blocked and state["kind"] == "deployment":
            evidence = success(ledger.api, scope)
            if evidence:
                states[scope] = fold(state, evidence, identity, ancestor=ancestor)
    prepared = []
    for scope, state in states.items():
        require(state["identity"] == identity, "configured Slack identity differs from the incident registry")
        existing = original.get(scope)
        incident = state["incident"]
        if scope in blocked:
            message = f"{incident['id']} has an unconfirmed Slack post; owner reconciliation is required. {len(state['deferred'])} observation(s) retained."
            print(message, file=sys.stderr)
            if os.environ.get("GITHUB_STEP_SUMMARY"):
                with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a") as stream:
                    stream.write(f"- **Action required:** {message}\n")
            if state != existing:
                ledger.save(state, "claims")  # Sign retained events; never resend uncertain posts.
            continue
        for key in desired_posts(state):
            if key not in incident["posts"]:
                incident["posts"][key] = {"state": "claimed", "ts": None}
        if state != existing:
            record = ledger.save(state, "claims")
            if any(item["state"] == "claimed" for item in incident["posts"].values()):
                prepared.append(record)
                on_saved(record)
    return prepared


def deliver(ledger, records, slack):
    identity = slack.identity()
    for record in records:
        state = ledger.verify(record)
        require(state["identity"] == identity, "Slack bot, workspace or channel changed since claim")
        incident = state["incident"]
        posts = desired_posts(state)
        for key, observation in posts.items():
            post = incident["posts"][key]
            if post["state"] == "delivered":
                continue
            if key == "parent":
                ts = slack.post(render(state))
            else:
                parent = incident["posts"]["parent"]
                require(parent["state"] == "delivered", "thread requires an acknowledged parent")
                # Repeating an update to a known message is safe. The broadcast
                # post is claimed separately and must not be blindly repeated.
                slack.update(parent["ts"], render(state))
                ts = slack.post(render(state, reply=observation), thread_ts=parent["ts"], broadcast=observation["conclusion"] == "success")
            post.update(state="delivered", ts=ts)
            ledger.save(state, "outcomes")
        parent = incident["posts"]["parent"]
        slack.update(parent["ts"], render(state))
        if not incident["permalink"]:
            incident["permalink"] = slack.permalink(parent["ts"])
            ledger.save(state, "outcomes")
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with Path(summary).open("a") as stream:
                stream.write(f"- [{incident['id']}]({incident['permalink']}): {'resolved' if incident['resolution'] else 'open'}; {len(incident['failures'])} failed attempt(s).\n")


def reconcile(ledger, identity, incident_id, operation, result, ts):
    require(ledger.run["event"] == "workflow_dispatch" and ledger.run.get("actor", {}).get("login") == "jonaraphael", "delivery reconciliation requires the repository owner on trusted main")
    states = ledger.load()
    matches = [state for state in states.values() if state["incident"]["id"] == incident_id]
    require(len(matches) == 1, "incident ID is missing or ambiguous")
    state = matches[0]
    require(state["identity"] == identity, "Slack identity changed")
    post = state["incident"]["posts"].get(operation)
    require(post and post["state"] == "claimed", "operation does not have uncertain delivery")
    earliest = next(key for key in desired_posts(state) if state["incident"]["posts"][key]["state"] == "claimed")
    require(operation == earliest, "reconcile the first uncertain operation before later posts")
    if result == "delivered":
        require(TIMESTAMP.fullmatch(ts), "confirm-delivered requires the exact message timestamp")
        post.update(state="delivered", ts=ts)
    else:
        require(result == "not-delivered" and not ts, "confirm-not-delivered must have no message timestamp")
    return [ledger.save(state, "claims")]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare", "deliver", "verify"])
    parser.add_argument("--event-path", type=Path, default=Path(os.environ.get("GITHUB_EVENT_PATH", "event.json")))
    parser.add_argument("--work-dir", type=Path, required=True)
    args = parser.parse_args()
    api = deployments.GitHub()
    run = worker_context(api)
    ledger = Ledger(api, run, args.work_dir)
    args.work_dir.mkdir(parents=True, exist_ok=True)
    plan_path = args.work_dir / "prepared.json"
    if args.command == "verify":
        ledger.load()
        return
    slack = Slack(os.environ.get("SHARED_DATASETS_SLACK_BOT_TOKEN", ""), os.environ.get("SHARED_DATASETS_SLACK_CHANNEL_ID", ""))
    if args.command == "prepare":
        identity = slack.identity()
        event = json.loads(args.event_path.read_text())
        plan_path.write_text("[]\n")
        if run["event"] == "workflow_dispatch":
            inputs = event["inputs"]
            if inputs["delivery_result"] == "seal-checkpoint":
                require(not inputs.get("message_ts"), "checkpoint sealing does not acknowledge a Slack message")
                records = seal_checkpoint(ledger, identity, inputs["incident_id"], inputs["operation"])
            else:
                records = reconcile(ledger, identity, inputs["incident_id"], inputs["operation"], inputs["delivery_result"], inputs.get("message_ts", ""))
        else:
            targets = job_targets()
            since = os.environ.get("SHARED_DATASETS_SLACK_INCIDENTS_SINCE", "")
            activated = instant(since)
            observations = source_observations(api, event, targets)
            observations = [item for item in observations if instant(item["time"]) >= activated]
            if event["workflow_run"]["name"] == "Deployment terminal verification":
                observations.extend(replay_observations(api, targets, since))
            partial = []
            def retain(record):
                partial.append(record)
                plan_path.write_text(json.dumps(partial, sort_keys=True) + "\n")
            records = prepare(ledger, identity, observations, on_saved=retain)
        plan_path.write_text(json.dumps(records, sort_keys=True) + "\n")
        print(f"Prepared {len(records)} incident transition(s); routine healthy runs remain quiet.")
    else:
        records = json.loads(plan_path.read_text())
        # Only exact signed claims from this worker invocation may be delivered.
        for record in records:
            payload = record["payload"]
            require(payload["execution_run_id"] == run["id"] and payload["execution_run_attempt"] == run["run_attempt"], "prepared claim belongs to another worker")
            require(api.get(f"repos/{REPOSITORY}/deployments/{record['id']}") == record, "prepared claim changed")
        deliver(ledger, records, slack)


if __name__ == "__main__":
    main()
