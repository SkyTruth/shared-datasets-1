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
from scripts import operational_policy as policy
from scripts.publication_receipt_recovery import REPAIR_STEP
from scripts.slack_incident_api import CHANNEL, TIMESTAMP, Slack
from scripts.slack_notify import build_slack_payload

SCHEMA = "shared-datasets-slack-incident-v3"
PREVIOUS_SCHEMA = "shared-datasets-slack-incident-v2"
LEGACY_SCHEMA = "shared-datasets-slack-incident-v1"
SCHEMAS = {SCHEMA, PREVIOUS_SCHEMA, LEGACY_SCHEMA}
ENVIRONMENT = "slack-incidents"
WORKFLOW = ".github/workflows/unattended-workflow-alert.yml"
REPOSITORY = alerts.REPOSITORY
ROOT = Path(__file__).resolve().parents[1]
RUNTIME_TARGETS = {"wdpa-monthly", "mpatlas-monthly", "eamlis-monthly", "sea-ice-daily"}
SDK_WORKFLOW = "publish-typescript-sdk.yml"
SDK_CANDIDATE_STEP = "Verify tested SDK candidate and reviewed version"
PUBLICATION_RECEIPT_JOB = re.compile(r"Complete publication receipt \(PR #([1-9][0-9]*); run ([1-9][0-9]*); attempt ([1-9][0-9]*)\)")


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


def binding_for(observation):
    scope = observation["scope"]
    publication = re.fullmatch(r"publication:pr-([1-9][0-9]*):(?:apply|reset)", scope)
    return policy.OperationBinding(scope, scope.removeprefix("deployment:"), observation["sha"],
                                   int(publication[1]) if publication else None)


def operational_fact(observation, value=None):
    """Normalize verified evidence without inferring failure text or safety.

    An unclassified failed deployment has an uncertain mutation frontier. An
    explicit adapter may supply narrower verified facts; a job name or red CI
    conclusion alone cannot authorize replay or a transient suppression.
    """
    binding = binding_for(observation)
    source_id = "github:" + observation_key(observation)
    if value is None:
        failed = observation["conclusion"] != "success"
        mutation = observation["kind"] in {"deployment", "publication"}
        value = {**binding.to_dict(), "source_id": source_id, "observed_at": observation["time"],
                 "phase": "verification" if mutation else "validation",
                 "reason": "unknown" if failed else "healthy",
                 "mutation_status": "uncertain" if failed and mutation else "not_required",
                 "recovery": "none",
                 "required_action": "Inspect the exact source evidence and establish the mutation outcome before recovery."
                    if mutation else "Inspect the exact failed source and repair its authorization, configuration or implementation.",
                 "impact": "Impact is unknown; the failed source has not established consumer impact.",
                 "published_data_state": "Unknown; inspect authoritative publication/deployment evidence before replay."
                    if mutation else "This observation does not establish a published-data change.",
                 "evidence": [run_link(observation)]}
    fact = policy.OperationalObservation.from_dict(value)
    require(all(getattr(fact.binding, key) == getattr(binding, key) for key in ("operation_id", "target", "revision"))
            and (binding.pr_number is None or fact.pr_number == binding.pr_number)
            and fact.source_id == source_id and fact.observed_at == observation["time"],
            "operational classification differs from its verified source")
    return fact


def episode_decisions(state, *, now=None):
    now = now or policy.timestamp(datetime.now(timezone.utc))
    incident = state["incident"]
    if incident is None or incident["resolution"] is not None:
        return []
    parent = incident["posts"].get("parent")
    decisions = []
    for observation in required_failures(state):
        if observation["scope"] in state["recoveries"]:
            continue
        fact = operational_fact(observation, state["operational"].get(observation_key(observation)))
        owner = state["supervision"].get(fact.operation_id)
        # A signed owner for a different revision or PR has no suppression
        # authority over this exact failed source.
        if owner is not None and not policy.covers_binding(owner, fact.binding):
            owner = None
        decision = policy.decide(fact, first_failure_at=state["first_failure_at"], now=now,
                                 supervision=owner, ownership_verified=owner is not None,
                                 public_parent_exists=bool(parent and parent["state"] == "delivered"))
        decisions.append((fact, decision))
    return decisions


def latch_escalation(state, now):
    if state["escalation_at"] is not None:
        return
    eligible = [decision for _, decision in episode_decisions(state, now=now)
                if decision.disposition == policy.Disposition.ESCALATE]
    if eligible:
        first = min(eligible, key=lambda decision: instant(decision.eligible_at))
        state["escalation_at"] = max(state["first_failure_at"], first.eligible_at)
        state["escalation_reason"] = first.reason


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
            elif inherited and (filename, key) not in {
                (SDK_WORKFLOW, "candidate"), ("catalog-web-deploy.yml", "check-inputs"),
            }:
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
    receipt = PUBLICATION_RECEIPT_JOB.fullmatch(name)
    if receipt and workflow == "publish-dataset.yml":
        pr = receipt[1]
        return f"publication:pr-{pr}:apply", "publication", f"Dataset PR #{pr} (apply)"
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
    if alerts.catalog_probe_only(run, jobs):
        # A complete read-only probe can resolve only its own observation
        # failure. It supplies no proof that a catalog deployment recovered.
        return [from_job(run, next(job for job in jobs if job["name"] == "Check for catalog input changes"), targets)]
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
                  and (alerts.WORKFLOW_PATHS[run["name"]], registered_name(job["name"])) not in targets
                  and (alerts.WORKFLOW_PATHS[run["name"]] != SDK_WORKFLOW or job["name"] != "candidate"
                       or candidate_verified(job))
                  and (not job["name"].startswith("Complete publication receipt (")
                       or step_verified(job, REPAIR_STEP))]
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


def candidate_verified(job):
    return step_verified(job, SDK_CANDIDATE_STEP)


def step_verified(job, name):
    steps = [step for step in job.get("steps", []) if step["name"] == name]
    return len(steps) == 1 and steps[0]["status"] == "completed" and steps[0]["conclusion"] == "success"


def migrate_sdk_candidate(api, state):
    """Reclassify only a signed open episode proven to precede publication."""
    incident = state["incident"]
    if state["scope"] != "deployment:typescript-sdk" or incident is None or incident["resolution"] is not None:
        return state
    observations = incident["failures"] + state["deferred"]
    if not any(item["job_name"] == "candidate" for item in observations):
        return state
    require(all(item["job_name"] == "candidate" and item["proof"] is None
                and item["conclusion"] in alerts.FAILED_CONCLUSIONS for item in observations),
            "mixed SDK candidate/publication incident cannot be reclassified")
    for item in observations:
        prefix = f"repos/{REPOSITORY}/actions/runs/{item['run_id']}/attempts/{item['attempt']}"
        run = api.get(prefix)
        require(run.get("id") == item["run_id"] and run.get("name") == "Publish TypeScript SDK" and run.get("path") == ".github/workflows/" + SDK_WORKFLOW
                and run.get("run_attempt") == item["attempt"] and run.get("head_sha") == item["sha"]
                and alerts.verify_source_event(api, run), "untrusted historical SDK candidate failure")
        jobs = api.pages(prefix + "/jobs?per_page=100", field="jobs")
        candidate = [job for job in jobs if job["name"] == "candidate"]
        publish = [job for job in jobs if job["name"] == "publish"]
        require(len(candidate) == len(publish) == 1 and candidate[0]["id"] == item["job_id"]
                and candidate[0]["status"] == "completed" and candidate[0]["conclusion"] == item["conclusion"]
                and candidate[0]["completed_at"] == item["time"] and publish[0]["status"] == "completed"
                and publish[0]["conclusion"] == "skipped", "historical SDK failure did not precede publication")
    migrated = copy.deepcopy(state)
    scope, kind, label = scope_for_job("candidate", {}, SDK_WORKFLOW)
    migrated.update(scope=scope, kind=kind, label=label, healthy=None)
    for item in migrated["incident"]["failures"] + migrated["deferred"]:
        item.update(scope=scope, kind=kind, label=label)
    migrated.update(operation_id=scope, aliases=[scope], operational={}, recoveries={}, watermarks={}, supervision={})
    # The original signed bytes retain the old deployment watermark as audit
    # history; it cannot describe health of the newly distinct read-only job.
    return validate_state(migrated)


def same_migrated_episode(original, current):
    """A scope alias must preserve the acknowledged parent and old failures."""
    if original["identity"] != current["identity"] or original["incident"]["id"] != current["incident"]["id"]:
        return False
    old, new = original["incident"], current["incident"]
    failures = {observation_key(item): item for item in new["failures"] + current["deferred"]}
    return ((not old["permalink"] or old["permalink"] == new["permalink"])
            and all(failures.get(observation_key(item)) == item for item in old["failures"] + original["deferred"])
            and all(new["posts"].get(key) == post for key, post in old["posts"].items() if post["state"] == "delivered"))


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
            "identity": identity, "healthy": None, "incident": None, "deferred": [],
            "operation_id": observation["scope"], "first_failure_at": None, "stable_since": None,
            "escalation_at": None, "escalation_reason": None,
            "aliases": [observation["scope"]], "source_aliases": [], "operational": {},
            "recoveries": {}, "watermarks": {}, "supervision": {}}


def source_alias(observation):
    return f"github:{observation['run_id']}:{observation['attempt']}"


def required_failures(state):
    """Specific same-attempt evidence supersedes a generic workflow failure.

    A public episode can cover several targets, but each target retains its own
    recovery requirement. Workflow completion cannot stand in for those proofs.
    """
    failures = state["incident"]["failures"]
    specific = {source_alias(item) for item in failures if item["kind"] != "workflow"}
    return [item for item in failures if item["kind"] != "workflow" or source_alias(item) not in specific]


def covers_failure(failure, evidence, ancestor):
    if failure["scope"] != evidence["scope"] or not ancestor(failure["sha"], evidence["sha"]):
        return False
    if instant(evidence["time"]) <= instant(failure["time"]) and (
            failure["kind"] != "deployment" or failure["sha"] == evidence["sha"]):
        return False
    receipt = PUBLICATION_RECEIPT_JOB.fullmatch(evidence["job_name"])
    if receipt and (failure["run_id"] != int(receipt[2]) or failure["attempt"] != int(receipt[3])
            or failure["job_name"] not in {f"Apply approved PR mutation plans (PR #{receipt[1]})",
                f"Publish PR #{receipt[1]} / Apply approved PR mutation plans (PR #{receipt[1]})"}):
        return False
    return failure["kind"] != "job" or evidence["job_name"] == failure["job_name"]


def fold(state, observation, identity, *, ancestor=deployments.git_ancestor, correlate=False):
    validate_observation(observation)
    state = copy.deepcopy(state or new_state(observation, identity))
    if observation["scope"] not in state["aliases"]:
        require(correlate, "observation scope changed")
        state["aliases"].append(observation["scope"])
        state["aliases"].sort()
    if observation["scope"] == state["scope"]:
        require(observation["kind"] == state["kind"], "observation kind changed")
    require(state["identity"] == identity, "incident belongs to another Slack bot, workspace or channel")
    incident = state["incident"]
    if observation["conclusion"] == "success":
        # Healthy runs do not create a ledger or a Slack message by themselves.
        if incident is None:
            return state
        if incident["resolution"] is not None:
            covered = [item for item in required_failures(state) if item["scope"] == observation["scope"]]
            healthy = state["watermarks"].get(observation["scope"])
            if healthy is not None and instant(observation["time"]) <= instant(healthy["time"]):
                return state
            if covered and all(covers_failure(item, observation, ancestor) for item in covered) and (
                    healthy is None or (instant(observation["time"]) > instant(healthy["time"])
                                        and ancestor(healthy["sha"], observation["sha"]))):
                state["watermarks"][observation["scope"]] = observation
                state["recoveries"][observation["scope"]] = observation
                state["healthy"] = max(state["watermarks"].values(), key=lambda item: instant(item["time"]))
            return state
        failures = required_failures(state)
        covered = [item for item in failures if item["scope"] == observation["scope"]]
        if not covered or not all(covers_failure(item, observation, ancestor) for item in covered):
            return state
        state["recoveries"][observation["scope"]] = observation
        state["watermarks"][observation["scope"]] = observation
        if all(item["scope"] in state["recoveries"] and covers_failure(
                item, state["recoveries"][item["scope"]], ancestor) for item in failures):
            resolution = max(state["recoveries"].values(), key=lambda item: instant(item["time"]))
            incident["resolution"] = resolution
            state["healthy"] = resolution
            state["stable_since"] = resolution["time"]
        return state
    if incident is not None and observation in incident["failures"]:
        return state
    if observation["scope"] in state["watermarks"]:
        healthy = state["watermarks"][observation["scope"]]
        if observation["sha"] == healthy["sha"]:
            if instant(observation["time"]) <= instant(healthy["time"]):
                return state
        elif ancestor(observation["sha"], healthy["sha"]):
            return state  # A superseded execution can finish after recovery.
    stable = (incident is not None and incident["resolution"] is not None
              and instant(observation["time"]) < instant(state["stable_since"]) + timedelta(seconds=policy.DEFAULT_POLICY.stable_recovery_seconds))
    if incident is None or (incident["resolution"] is not None and not stable):
        digest = hashlib.sha256((state["scope"] + ":" + observation_key(observation)).encode()).hexdigest()[:12].upper()
        state["incident"] = {"id": "SD-" + digest, "failures": [observation], "resolution": None, "posts": {}, "permalink": "", "parent_view_sha256": None}
        state["first_failure_at"] = observation["time"]
        state["escalation_at"], state["escalation_reason"] = None, None
        state["recoveries"] = {}
        state["operational"] = {}
        state["source_aliases"] = [source_alias(observation)]
        if incident is not None:
            # Stable recovery ends the old ownership episode. Its immutable
            # signed checkpoints retain that responsibility and deadline;
            # stale ownership cannot change a new issue's recovery budget.
            state["supervision"] = {key: owner for key, owner in state["supervision"].items()
                                    if owner["first_failure_at"] is None}
        state["aliases"] = sorted({state["scope"], observation["scope"], *state["supervision"], *state["watermarks"]})
    elif observation_key(observation) not in {observation_key(item) for item in incident["failures"]}:
        incident["failures"].append(observation)
        incident["failures"].sort(key=lambda item: (instant(item["time"]), observation_key(item)))
        incident["resolution"] = None
        state["first_failure_at"] = min(item["time"] for item in incident["failures"])
    state["stable_since"] = None
    state["recoveries"].pop(observation["scope"], None)
    state["source_aliases"] = sorted(set(state["source_aliases"]) | {source_alias(observation)})
    state["operational"].setdefault(observation_key(observation), operational_fact(observation).to_dict())
    return state


def validate_state(state, *, legacy=False, schema=SCHEMA):
    if legacy:
        schema = LEGACY_SCHEMA
    require(schema in SCHEMAS, "unknown incident state schema")
    fields = {"scope", "kind", "label", "identity", "healthy", "incident", "deferred"}
    if schema == SCHEMA:
        fields |= {"operation_id", "first_failure_at", "stable_since", "aliases", "source_aliases",
                   "operational", "recoveries", "watermarks", "supervision", "escalation_at", "escalation_reason"}
    require(isinstance(state, dict) and set(state) == fields, "invalid incident state schema")
    aliases = state["aliases"] if schema == SCHEMA else [state["scope"]]
    require(isinstance(aliases, list) and aliases and state["scope"] in aliases
            and len(aliases) == len(set(aliases))
            and all(isinstance(scope, str) and re.fullmatch(r"[a-z][a-z0-9:._-]{0,150}", scope) for scope in aliases),
            "invalid incident aliases")
    identity = state["identity"]
    require(isinstance(identity, dict) and set(identity) == {"channel", "team", "user"}
            and CHANNEL.fullmatch(identity["channel"]) and re.fullmatch(r"T[A-Z0-9]+", identity["team"])
            and re.fullmatch(r"U[A-Z0-9]+", identity["user"]), "invalid incident Slack identity")
    if state["healthy"] is not None:
        validate_observation(state["healthy"])
        require(state["healthy"]["scope"] in aliases and state["healthy"]["conclusion"] == "success", "invalid healthy watermark")
    require(isinstance(state["deferred"], list), "invalid deferred observation queue")
    deferred_events = set()
    for item in state["deferred"]:
        validate_observation(item)
        require(item["scope"] in aliases and observation_key(item) not in deferred_events, "incompatible/duplicate deferred observation")
        deferred_events.add(observation_key(item))
    incident = state["incident"]
    if schema == SCHEMA:
        require(state["operation_id"] == state["scope"], "operation identity differs from canonical scope")
        require(isinstance(state["source_aliases"], list) and len(state["source_aliases"]) == len(set(state["source_aliases"]))
                and all(isinstance(value, str) and re.fullmatch(r"github:[1-9][0-9]*:[1-9][0-9]*", value)
                        for value in state["source_aliases"]), "invalid source aliases")
        require(isinstance(state["operational"], dict) and isinstance(state["recoveries"], dict)
                and isinstance(state["watermarks"], dict), "invalid operational evidence")
        require(isinstance(state["supervision"], dict), "invalid accountable supervision map")
        for operation_id, owner in state["supervision"].items():
            policy.validate_supervision(owner, first_failure_at=state["first_failure_at"])
            require(operation_id == owner["operation_id"] and operation_id in aliases, "supervision differs from source scope")
        if state["first_failure_at"] is not None:
            instant(state["first_failure_at"])
        if state["stable_since"] is not None:
            instant(state["stable_since"])
        require((state["escalation_at"] is None) == (state["escalation_reason"] is None), "incomplete escalation eligibility")
        if state["escalation_at"] is not None:
            require(state["first_failure_at"] is not None
                    and instant(state["escalation_at"]) >= instant(state["first_failure_at"])
                    and isinstance(state["escalation_reason"], str) and re.fullmatch(r"[a-z_]{1,100}", state["escalation_reason"]),
                    "invalid escalation eligibility")
        for scope, evidence in list(state["watermarks"].items()) + list(state["recoveries"].items()):
            validate_observation(evidence)
            require(scope in aliases and evidence["scope"] == scope and evidence["conclusion"] == "success",
                    "invalid target recovery proof")
        if incident is None:
            require(bool(state["supervision"]) and state["first_failure_at"] is None
                    and state["stable_since"] is None and not state["operational"] and not state["recoveries"] and not state["watermarks"]
                    and not state["source_aliases"] and not state["deferred"] and state["healthy"] is None,
                    "a prospective registration cannot claim a failure or recovery")
            return state
    fields = {"id", "failures", "resolution", "posts", "permalink"}
    require(isinstance(incident, dict) and set(incident) == (fields if schema == LEGACY_SCHEMA else fields | {"parent_view_sha256"}), "invalid incident episode")
    require(re.fullmatch(r"SD-[0-9A-F]{12}", incident["id"]), "invalid incident ID")
    require(isinstance(incident["failures"], list) and incident["failures"], "incident must retain original failures")
    seen = set()
    for item in incident["failures"]:
        validate_observation(item)
        require(item["scope"] in aliases and (schema == SCHEMA or item["kind"] == state["kind"])
                and item["conclusion"] in alerts.FAILED_CONCLUSIONS and observation_key(item) not in seen, "incompatible/duplicate incident failure")
        seen.add(observation_key(item))
    if incident["resolution"] is not None:
        validate_observation(incident["resolution"])
        require(incident["resolution"]["scope"] in aliases and incident["resolution"]["conclusion"] == "success"
                and state["healthy"] is not None and instant(state["healthy"]["time"]) >= instant(incident["resolution"]["time"]), "invalid incident recovery")
    require(isinstance(incident["posts"], dict), "invalid Slack outbox")
    allowed = set(failure_posts(state))
    if incident["resolution"] is not None:
        # Historical recovery sends remain signed audit evidence. They are
        # never part of the active outbox, including unconfirmed old claims.
        allowed.add("resolved:" + observation_key(incident["resolution"]))
    for key, post in incident["posts"].items():
        require(key in allowed and isinstance(post, dict) and set(post) == {"state", "ts"}
                and post["state"] in {"claimed", "delivered"}, "invalid Slack delivery operation")
        require((post["state"] == "claimed" and post["ts"] is None)
                or (post["state"] == "delivered" and isinstance(post["ts"], str) and TIMESTAMP.fullmatch(post["ts"])), "invalid Slack delivery acknowledgement")
    link = incident["permalink"]
    require(isinstance(link, str), "invalid incident permalink")
    if link:
        parent = incident["posts"].get("parent")
        require(parent and parent["state"] == "delivered" and re.fullmatch(
            r"https://[a-z0-9-]+\.slack\.com/archives/" + identity["channel"] + "/p" + parent["ts"].replace(".", ""), link), "permalink does not identify the parent")
    if schema != LEGACY_SCHEMA:
        view = incident["parent_view_sha256"]
        require(view is None or (isinstance(view, str) and re.fullmatch(r"[0-9a-f]{64}", view)), "invalid parent view acknowledgement")
        if view is not None:
            parent = incident["posts"].get("parent")
            require(parent and parent["state"] == "delivered", "parent view requires an acknowledged original message")
    if schema == SCHEMA:
        require(state["first_failure_at"] == min(item["time"] for item in incident["failures"]), "original failure clock changed")
        require((incident["resolution"] is None) == (state["stable_since"] is None), "invalid stable recovery clock")
        if incident["resolution"] is not None:
            require(state["stable_since"] == incident["resolution"]["time"], "stable recovery clock was renewed")
        require(set(state["operational"]) <= seen, "classification has no retained source observation")
        for item in incident["failures"]:
            if observation_key(item) in state["operational"]:
                operational_fact(item, state["operational"][observation_key(item)])
        require(set(state["source_aliases"]) == {source_alias(item) for item in incident["failures"]}, "source alias lacks exact retained failure evidence")
    return state


def migrate_verified_state(state, schema):
    """Upgrade only verified historical bytes; never assume an update succeeded."""
    state = copy.deepcopy(state)
    if schema == LEGACY_SCHEMA:
        state["incident"]["parent_view_sha256"] = None
    if schema != SCHEMA:
        incident = state["incident"]
        state.update(operation_id=state["scope"],
                     first_failure_at=min(item["time"] for item in incident["failures"]),
                     stable_since=incident["resolution"]["time"] if incident["resolution"] else None,
                     aliases=[state["scope"]],
                     escalation_at=min(item["time"] for item in incident["failures"]) if incident["posts"].get("parent") else None,
                     escalation_reason="historical_public_delivery" if incident["posts"].get("parent") else None,
                     source_aliases=sorted({source_alias(item) for item in incident["failures"]}),
                     operational={}, recoveries={state["scope"]: incident["resolution"]} if incident["resolution"] else {},
                     watermarks={state["scope"]: state["healthy"]} if state["healthy"] else {}, supervision={})
    return validate_state(state)


def failure_posts(state):
    """Lifecycle order is independent of canonical JSON object-key sorting."""
    return {"parent": None, **{"attempt:" + observation_key(item): item for item in state["incident"]["failures"][1:]}}


def desired_posts(state, *, now=None):
    incident = state["incident"]
    if incident is None:
        return {}
    parent = incident["posts"].get("parent")
    if incident["resolution"] is not None:
        # Existing failed-attempt claims still need owner reconciliation, but
        # corrected failures cannot authorize any new message or retry reply.
        return {"parent": None} if parent and parent["state"] == "delivered" else {}
    if state["escalation_at"] is not None:
        return {"parent": None}
    # Historical attempt claims remain auditable and require reconciliation;
    # new attempts only change the original parent's count and evidence.
    decisions = episode_decisions(state, now=now)
    return {"parent": None} if any(decision.disposition == policy.Disposition.ESCALATE for _, decision in decisions) else {}


def pending_posts(state):
    if state["incident"] is None:
        return {}
    posts = state["incident"]["posts"]
    return {key: posts[key] for key in failure_posts(state) if key in posts and posts[key]["state"] == "claimed"}


def run_link(observation):
    return emissions.invocation(REPOSITORY, observation["run_id"], observation["attempt"])


def render(state):
    incident = state["incident"]
    first, latest = incident["failures"][0], incident["failures"][-1]
    resolution = incident["resolution"]
    status = "success" if resolution else "error"
    title = f"{'Resolved' if resolution else 'Open'} · {state['label']} · {incident['id']}"
    attempts = len({source_alias(item) for item in incident["failures"]})
    evidence = (f"*Original failure:* {escape(first['job_name'])[:400]} ({first['conclusion']})\n"
                f"<{run_link(first)}|Failed attempt> · `{first['sha'][:12]}` · {first['time']}\n"
                f"*Failed attempts:* {attempts} · {len(incident['failures'])} source observations")
    if latest != first:
        evidence += f"\n<{run_link(latest)}|Latest failed attempt> · {latest['time']}"
    unresolved = {}
    for item in required_failures(state):
        if item["scope"] not in state["recoveries"]:
            unresolved[item["scope"]] = item
    paragraphs = []
    for scope, item in sorted(unresolved.items())[:32]:
        fact = operational_fact(item, state["operational"].get(observation_key(item)))
        owner = state["supervision"].get(fact.operation_id)
        responsible = (owner["responsible_human"] if owner is not None and policy.covers_binding(owner, fact.binding)
                       else "Unassigned; repository maintainer action required.")
        paragraphs.append(f"*Target:* {escape(scope)}\n"
            f"*Impact:* {escape(fact.impact)[:250]}\n"
            f"*Published-data state:* {escape(fact.published_data_state)[:250]}\n"
            f"*Responsible owner:* {escape(responsible)}\n"
            f"*Required action:* {escape(fact.required_action)[:350]}\n"
            f"*Evidence:* <{run_link(item)}|Exact source attempt>")
    if len(unresolved) > 32:
        paragraphs.append(f"{len(unresolved) - 32} additional unresolved targets require inspection in the complete workflow evidence below.")
    if not unresolved:
        paragraphs.append("*Impact:* Required target outcomes have recovery proof.\n"
            "*Published-data state:* See the authoritative recovery evidence below.\n"
            "*Responsible owner:* Recovery verified by the trusted observer.\n"
            "*Required action:* None for this resolved episode.")
    paragraphs.append("*Recovery attempted:* Source observations do not specify completed recovery attempts. Inspect the linked evidence before retrying.")
    if state["escalation_at"] is not None and resolution is None:
        paragraphs.append(f"*Escalation eligible:* {state['escalation_at']} · {escape(state['escalation_reason'])}")
    paragraphs.append("*Evidence:*\n" + evidence)
    if resolution:
        recovery = f"*Recovery verified:* {resolution['time']}\n<{run_link(resolution)}|Recovery evidence> · `{resolution['sha'][:12]}`"
        if resolution["proof"]:
            recovery += f"\nDeployment record {resolution['proof']['record_id']} · `{resolution['proof']['artifact']}`"
        paragraphs.append(recovery)
    body = "\n\n".join(paragraphs)
    payload = build_slack_payload(title=title, body=body, status=status, emoji="✅" if status == "success" else "🔴")
    # Preserve every displayed target action rather than letting the generic
    # webhook helper truncate the entire incident at its first 3,000 characters.
    sections, section = [], ""
    for paragraph in paragraphs:
        if len(section) + len(paragraph) + 2 > 2900:
            sections.append(section)
            section = ""
        section += ("\n\n" if section else "") + paragraph
    if section:
        sections.append(section)
    payload["blocks"] = payload["blocks"][:1] + [
        {"type": "section", "text": {"type": "mrkdwn", "text": section}} for section in sections]
    # Labels may originate in job names; escape the notification fallback too.
    payload["text"] = (escape(title) + "\n" + body)[:39000]
    payload["metadata"] = {"event_type": "shared_datasets_incident", "event_payload": {"incident_id": incident["id"], "scope": state["scope"]}}
    return payload


def parent_view_sha256(state):
    return hashlib.sha256(emissions.canonical(render(state))).hexdigest()


def pending_recovery_update(state):
    incident = state["incident"]
    if incident is None:
        return False
    parent = incident["posts"].get("parent")
    return (incident["resolution"] is not None and parent and parent["state"] == "delivered"
            and incident["parent_view_sha256"] != parent_view_sha256(state))


def needs_parent_delivery(state):
    incident = state["incident"]
    if incident is None:
        return False
    parent = incident["posts"].get("parent")
    return parent and parent["state"] == "delivered" and (not incident["permalink"]
            or incident["parent_view_sha256"] != parent_view_sha256(state))


def admits_blocked_observations(state):
    """A known open parent can recover without replaying its uncertain replies."""
    incident = state["incident"]
    if incident is None:
        return False
    parent = incident["posts"].get("parent")
    return incident["resolution"] is None and parent and parent["state"] == "delivered"


def worker_context(api, environment=os.environ):
    require(environment.get("GITHUB_REPOSITORY") == REPOSITORY and environment.get("GITHUB_REF") == "refs/heads/main"
            and environment.get("GITHUB_WORKFLOW_REF") == REPOSITORY + "/" + WORKFLOW + "@refs/heads/main", "incidents require the trusted main workflow")
    run_id, attempt = int(environment["GITHUB_RUN_ID"]), int(environment["GITHUB_RUN_ATTEMPT"])
    run = api.get(f"repos/{REPOSITORY}/actions/runs/{run_id}/attempts/{attempt}")
    repo = api.get(f"repos/{REPOSITORY}")
    workflow = api.get(f"repos/{REPOSITORY}/actions/workflows/{WORKFLOW.rsplit('/', 1)[-1]}")
    require(run.get("id") == run_id and run.get("run_attempt") == attempt and run.get("path") == WORKFLOW
            and run.get("workflow_id") == workflow.get("id") and workflow.get("path") == WORKFLOW
            and run.get("head_branch") == "main" and run.get("event") in {"workflow_run", "workflow_dispatch", "schedule"}
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
                and payload["schema"] in SCHEMAS and record.get("task") == "slack-incident"
                and record.get("environment") == ENVIRONMENT and record.get("production_environment") is False
                and record.get("ref") == record.get("sha") and deployments.SHA.fullmatch(str(record.get("sha", "")))
                and record.get("creator", {}).get("login") == "github-actions[bot]", "invalid notification ledger identity")
        state = validate_state(payload["state"], schema=payload["schema"])
        statuses = self.api.pages(f"repos/{REPOSITORY}/deployments/{record['id']}/statuses?per_page=100")
        require(len(statuses) == 1 and statuses[0].get("description") == "applied" and statuses[0].get("state") == "success", "notification checkpoint is incomplete; reconcile before delivery")
        # The signed bytes bind the channel, parent timestamp, outbox claims and
        # observation history. A forged Actions-bot row cannot suppress alerts.
        emissions.verify(self.api, REPOSITORY, record, statuses[0], original_signer=WORKFLOW, batch=True)
        return migrate_verified_state(state, payload["schema"])

    def load(self):
        records = self.api.pages(f"repos/{REPOSITORY}/deployments?environment={ENVIRONMENT}&task=slack-incident&per_page=100")
        latest = {}
        for record in records:
            payload = record["payload"]
            if isinstance(payload, str):
                payload = json.loads(payload)
            require(isinstance(payload, dict) and payload.get("schema") in SCHEMAS, "unknown notification ledger schema")
            scope = payload["state"]["scope"]
            if scope not in latest or record["id"] > latest[scope]["id"]:
                latest[scope] = record
        states = {scope: self.verify(record) for scope, record in latest.items()}
        for scope, state in list(states.items()):
            for alias in state["aliases"]:
                if alias == scope or alias not in states:
                    continue
                prospective = states[alias]
                require(prospective["incident"] is None and latest[scope]["id"] > latest[alias]["id"],
                        "signed episode alias conflicts with an existing incident or newer registration")
                for operation_id, owner in prospective["supervision"].items():
                    current = state["supervision"].get(operation_id)
                    require(current is not None and all(current[key] == owner[key] for key in
                            ("responsible_human", "conversation_ref", "operation_id", "target"))
                            and instant(current["updated_at"]) >= instant(owner["updated_at"]),
                            "episode alias discarded prospective owner provenance")
                    if owner["deadline"] is not None:
                        require(current["deadline"] is not None and instant(current["deadline"]) <= instant(owner["deadline"]),
                                "episode alias restarted the supervision clock")
                del states[alias]
        old_scope = "deployment:typescript-sdk"
        if old_scope not in states:
            return states
        original = states[old_scope]
        migrated = migrate_sdk_candidate(self.api, original)
        if migrated["scope"] == old_scope:
            return states
        scope = migrated["scope"]
        if scope in states:
            current = states[scope]
            require(latest[scope]["id"] > latest[old_scope]["id"], "candidate migration checkpoint predates its source")
            if current["incident"]["id"] == migrated["incident"]["id"]:
                require(same_migrated_episode(migrated, current), "candidate migration conflicts with its Slack parent or history")
            else:
                # Later episodes may replace the current candidate state only
                # after a signed canonical checkpoint resolved the migrated one.
                bridges = []
                for record in records:
                    payload = record["payload"]
                    if isinstance(payload, str):
                        payload = json.loads(payload)
                    historical = payload["state"]
                    if (record["id"] < latest[scope]["id"] and historical["scope"] == scope
                            and historical["incident"]["id"] == migrated["incident"]["id"]):
                        bridges.append(record)
                require(bool(bridges), "candidate migration collides with another incident")
                bridge = self.verify(max(bridges, key=lambda record: record["id"]))
                require(bridge["incident"]["resolution"] is not None and not pending_posts(bridge)
                        and same_migrated_episode(migrated, bridge),
                        "candidate migration has no signed resolved predecessor")
        else:
            states[scope] = migrated
        del states[old_scope]
        return states

    def save(self, state, phase):
        validate_state(state)
        payload = {"schema": SCHEMA, "state": state,
                   "execution_run_id": self.run["id"], "execution_run_attempt": self.run["run_attempt"]}
        record = self.api.post(f"repos/{REPOSITORY}/deployments", {
            "ref": self.run["head_sha"], "task": "slack-incident", "environment": ENVIRONMENT,
            "auto_merge": False, "required_contexts": [], "production_environment": False,
            "transient_environment": False, "description": (state["incident"]["id"] if state["incident"] else state["operation_id"])[:70] + " notification checkpoint", "payload": payload,
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
    require(isinstance(payload, dict) and payload.get("schema") in SCHEMAS
            and hashlib.sha256(emissions.canonical(payload)).hexdigest() == match[2], "checkpoint differs from the owner's reviewed payload")
    state = validate_state(payload["state"], schema=payload["schema"])
    require(state["incident"] is not None and state["incident"]["id"] == incident_id and state["identity"] == identity
            and record.get("task") == "slack-incident" and record.get("environment") == ENVIRONMENT
            and record.get("production_environment") is False, "checkpoint belongs to another incident or Slack identity")
    latest = ledger.api.pages(f"repos/{REPOSITORY}/deployments?environment={ENVIRONMENT}&task=slack-incident&per_page=100")
    same_scope = [item for item in latest if (json.loads(item["payload"]) if isinstance(item["payload"], str) else item["payload"])["state"]["scope"] == state["scope"]]
    require(same_scope and max(item["id"] for item in same_scope) == record["id"], "checkpoint is no longer the latest incident state")
    # Recovery truth is independently rechecked even when the owner confirms
    # notification delivery bookkeeping from an interrupted writer.
    state = migrate_verified_state(state, payload["schema"])
    resolution = state["incident"]["resolution"]
    evidence = ([resolution] if resolution else []) + list(state["recoveries"].values()) + list(state["watermarks"].values())
    for item in {observation_key(item): item for item in evidence if item["kind"] == "deployment"}.values():
        verify_saved_success(ledger.api, item)
    if resolution:
        require(all(item["scope"] in state["recoveries"] and covers_failure(
            item, state["recoveries"][item["scope"]], deployments.git_ancestor) for item in required_failures(state)),
            "checkpoint recovery has incompatible revision ancestry or missing target proof")
    ledger.save(state, "claims")
    return []


def locate_scope(states, observation):
    matches = [scope for scope, state in states.items() if observation["scope"] in state["aliases"]]
    require(len(matches) <= 1, "source scope has conflicting signed episode aliases")
    # A workflow fallback and later exact job evidence from the same attempt
    # describe one episode. Different known targets do not merge merely because
    # they share a CI run; the coarse workflow observation is the bridge.
    candidates = [scope for scope, state in states.items() if state["incident"] is not None
                  and source_alias(observation) in state["source_aliases"]
                  and (observation["kind"] == "workflow" or any(
                      item["kind"] == "workflow" and source_alias(item) == source_alias(observation)
                      for item in state["incident"]["failures"]))]
    require(len(candidates) <= 1, "source attempt has conflicting public episode candidates")
    if matches:
        match = matches[0]
        if candidates and candidates[0] != match and states[match]["incident"] is None:
            canonical = states[candidates[0]]
            for operation_id, owner in states[match]["supervision"].items():
                require(operation_id not in canonical["supervision"], "correlated prospective owners conflict")
                canonical["supervision"][operation_id] = owner
            canonical["aliases"] = sorted(set(canonical["aliases"]) | set(states[match]["aliases"]))
            del states[match]
            return candidates[0]
        return match
    return candidates[0] if candidates else observation["scope"]


def bind_episode_owners(state, now):
    if state["incident"] is None or state["incident"]["resolution"] is not None:
        return
    for operation_id, owner in list(state["supervision"].items()):
        binding = policy.OperationBinding.from_dict({key: owner[key] for key in policy.OperationBinding.__dataclass_fields__})
        for item in required_failures(state):
            key = observation_key(item)
            fact = operational_fact(item, state["operational"].get(key))
            if (item["kind"] == "deployment" and fact.operation_id == operation_id
                    and fact.revision == binding.revision and fact.pr_number is None and binding.pr_number is not None):
                # supervise verified this exact merged PR/executor relation;
                # the signed registration can enrich absent source metadata.
                enriched = fact.to_dict()
                enriched["pr_number"] = binding.pr_number
                state["operational"][key] = operational_fact(item, enriched).to_dict()
        if not any(policy.covers_binding(owner, operational_fact(item, state["operational"].get(observation_key(item))).binding)
                   for item in required_failures(state)):
            continue
        if owner["first_failure_at"] is not None and instant(owner["first_failure_at"]) <= instant(state["first_failure_at"]):
            continue
        state["supervision"][operation_id] = policy.bind_supervision(
            owner, binding, first_failure_at=state["first_failure_at"], now=now)


def prepare(ledger, identity, observations, *, ancestor=deployments.git_ancestor, success=terminal_success,
            on_saved=lambda _: None, now=None, operational=None, initial_states=None, original_states=None):
    now = now or policy.timestamp(datetime.now(timezone.utc))
    policy.instant(now)
    states = ledger.load() if initial_states is None else initial_states
    original = copy.deepcopy(states if original_states is None else original_states)
    operational = operational or {}
    blocked = {scope for scope, state in states.items() if pending_posts(state)}
    replay = list(observations)
    for scope, state in states.items():
        require(state["identity"] == identity, "configured Slack identity differs from the incident registry")
        if scope not in blocked or admits_blocked_observations(state):
            replay.extend(state["deferred"])
            state["deferred"] = []
    for observation in sorted(replay, key=lambda item: (instant(item["time"]), observation_key(item))):
        validate_observation(observation)
        scope = locate_scope(states, observation)
        if ((scope in blocked and not admits_blocked_observations(states[scope]))
                or (scope in states and pending_recovery_update(states[scope]))):
            state = states[scope]
            if observation["scope"] not in state["aliases"]:
                state["aliases"].append(observation["scope"])
                state["aliases"].sort()
            known = state["incident"]["failures"] + state["deferred"]
            if state["incident"]["resolution"]:
                known.append(state["incident"]["resolution"])
            if observation_key(observation) not in {observation_key(item) for item in known}:
                state["deferred"].append(observation)
            continue
        if observation["conclusion"] == "success" and scope not in states:
            continue
        states[scope] = fold(states.get(scope), observation, identity, ancestor=ancestor, correlate=scope != observation["scope"])
        if observation["conclusion"] != "success":
            key = observation_key(observation)
            if key in operational and key in {observation_key(item) for item in states[scope]["incident"]["failures"]}:
                states[scope]["operational"][key] = operational_fact(observation, operational[key]).to_dict()
    for scope, state in list(states.items()):
        if (scope not in blocked or admits_blocked_observations(state)) and state["incident"] is not None:
            targets = {item["scope"] for item in required_failures(state) if item["kind"] == "deployment"}
            for target in sorted(targets):
                evidence = success(ledger.api, target)
                if evidence:
                    state = fold(state, evidence, identity, ancestor=ancestor, correlate=target != scope)
            states[scope] = state
        bind_episode_owners(state, now)
        latch_escalation(state, now)
    prepared = []
    for scope, state in states.items():
        require(state["identity"] == identity, "configured Slack identity differs from the incident registry")
        existing = original.get(scope)
        incident = state["incident"]
        if incident is None:
            if state != existing:
                ledger.save(state, "claims")
            continue
        if scope in blocked:
            message = f"{incident['id']} has an unconfirmed Slack post; owner reconciliation is required. {len(state['deferred'])} observation(s) retained."
            print(message, file=sys.stderr)
            if os.environ.get("GITHUB_STEP_SUMMARY"):
                with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a") as stream:
                    stream.write(f"- **Action required:** {message}\n")
            update_only = incident["resolution"] is not None and needs_parent_delivery(state)
            if state != existing or update_only:
                record = ledger.save(state, "claims")  # Never resend uncertain posts.
                if update_only:
                    prepared.append(record)
                    on_saved(record)
            continue
        for key in desired_posts(state, now=now):
            if key not in incident["posts"]:
                incident["posts"][key] = {"state": "claimed", "ts": None}
        needs_delivery = needs_parent_delivery(state)
        if state != existing or needs_delivery:
            record = ledger.save(state, "claims")
            if needs_delivery or pending_posts(state):
                prepared.append(record)
                on_saved(record)
    return prepared


def supervise(ledger, identity, request, *, now=None):
    """Apply authenticated main-only supervision without transferring authority."""
    request = policy.validate_supervision_request(request)
    now = now or policy.timestamp(datetime.now(timezone.utc))
    run = ledger.run
    require(run.get("event") == "workflow_dispatch" and run.get("path") == WORKFLOW
            and run.get("head_branch") == "main", "supervision requires an authenticated main dispatch")
    require(run.get("run_attempt") == 1, "supervision renewal requires a new dispatch, never a rerun")
    activity_at = run.get("created_at")
    require(policy.instant(activity_at) <= policy.instant(now), "supervision dispatch activity is from the future")
    authority = {"actor": run.get("actor", {}).get("login"), "repository": REPOSITORY,
                 "workflow": WORKFLOW, "ref": "refs/heads/main", "executor_sha": run.get("head_sha"),
                 "run_id": run.get("id"), "attempt": run.get("run_attempt")}
    policy.validate_authority(authority, responsible_human=request["responsible_human"])
    binding = policy.OperationBinding.from_dict(request["binding"])
    scope = binding.operation_id
    if scope.startswith("deployment:"):
        require(binding.target == scope.removeprefix("deployment:")
                and binding.target in deployments.TARGET_WORKFLOWS | deployments.TERRAFORM_WORKFLOWS,
                "supervision target is not a registered deployment")
        kind = "deployment"
        if binding.pr_number is not None:
            pr = ledger.api.get(f"repos/{REPOSITORY}/pulls/{binding.pr_number}")
            require(pr.get("merged") is True and pr.get("merge_commit_sha") == binding.revision
                    and pr.get("base", {}).get("ref") == "main"
                    and pr.get("base", {}).get("repo", {}).get("full_name") == REPOSITORY,
                    "supervision PR does not identify the exact merged deployment revision")
    elif scope.startswith("workflow:"):
        require(binding.target == scope and scope.removeprefix("workflow:") in alerts.WORKFLOW_PATHS.values(),
                "supervision workflow is not registered")
        kind = "workflow"
    elif (publication := re.fullmatch(r"publication:pr-([1-9][0-9]*):(?:apply|reset)", scope)):
        require(binding.target == scope and binding.pr_number == int(publication[1]), "supervision publication PR differs")
        kind = "publication"
    else:
        require(scope.startswith("job:") and binding.target == scope, "invalid exact job supervision scope")
        kind = "job"
    states = ledger.load()
    original = copy.deepcopy(states)
    matches = [state for state in states.values() if scope in state["aliases"]]
    require(len(matches) <= 1, "supervision scope has ambiguous signed aliases")
    if matches:
        state = matches[0]
        require(state["identity"] == identity, "supervision Slack identity changed")
    else:
        require(kind != "job", "new unregistered jobs cannot be concealed by guessed scopes")
        state = new_state({"scope": scope, "kind": kind, "label": binding.target}, identity)
        states[scope] = state
    previous = state["supervision"].get(scope)
    latch_escalation(state, now)
    if state["incident"] is not None and state["incident"]["resolution"] is not None and (
            instant(now) >= instant(state["stable_since"]) + timedelta(seconds=policy.DEFAULT_POLICY.stable_recovery_seconds)):
        previous = None  # Its signed historical checkpoint remains audit evidence.
    owner = policy.transition_supervision(previous, request["command"], binding, now=activity_at, authority=authority,
        responsible_human=request["responsible_human"], conversation_ref=request["conversation_ref"],
        current_action=request["current_action"], question_ref=request["question_ref"], handoff_ref=request["handoff_ref"],
        verified_bindings=[operational_fact(item, state["operational"].get(observation_key(item))).binding
                           for item in state["incident"]["failures"]] if state["incident"] is not None else [])
    state["supervision"][scope] = owner
    return prepare(ledger, identity, [], now=now, initial_states=states, original_states=original)


def deliver(ledger, records, slack):
    identity = slack.identity()
    for record in records:
        state = ledger.verify(record)
        require(state["identity"] == identity, "Slack bot, workspace or channel changed since claim")
        incident = state["incident"]
        posts = desired_posts(state)
        for key in posts:
            require(key == "parent", "only the original incident parent may be sent")
            post = incident["posts"][key]
            if post["state"] == "delivered":
                continue
            ts = slack.post(render(state))
            incident["parent_view_sha256"] = parent_view_sha256(state)
            post.update(state="delivered", ts=ts)
            ledger.save(state, "outcomes")
        parent = incident["posts"].get("parent")
        if not parent or parent["state"] != "delivered":
            continue
        view = parent_view_sha256(state)
        if incident["parent_view_sha256"] != view:
            slack.update(parent["ts"], render(state))
            incident["parent_view_sha256"] = view
            ledger.save(state, "outcomes")
        if not incident["permalink"]:
            incident["permalink"] = slack.permalink(parent["ts"])
            ledger.save(state, "outcomes")
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with Path(summary).open("a") as stream:
                stream.write(f"- [{incident['id']}]({incident['permalink']}): {'resolved' if incident['resolution'] else 'open'}; {len(incident['failures'])} failed attempt(s).\n")


def reconcile(ledger, identity, incident_id, operation, result, ts, *, ancestor=deployments.git_ancestor, success=terminal_success):
    require(ledger.run["event"] == "workflow_dispatch" and ledger.run.get("actor", {}).get("login") == "jonaraphael", "delivery reconciliation requires the repository owner on trusted main")
    states = ledger.load()
    matches = [state for state in states.values() if state["incident"] is not None and state["incident"]["id"] == incident_id]
    require(len(matches) == 1, "incident ID is missing or ambiguous")
    state = matches[0]
    require(state["identity"] == identity, "Slack identity changed")
    post = state["incident"]["posts"].get(operation)
    require(post and operation in pending_posts(state), "operation does not have uncertain active delivery")
    earliest = next(iter(pending_posts(state)))
    require(operation == earliest, "reconcile the first uncertain operation before later posts")
    if result == "delivered":
        require(TIMESTAMP.fullmatch(ts), "confirm-delivered requires the exact message timestamp")
        if operation != "parent":
            parent = state["incident"]["posts"].get("parent")
            require(parent and parent["state"] == "delivered", "confirmed thread delivery requires an acknowledged parent")
        post.update(state="delivered", ts=ts)
    else:
        require(result == "not-delivered" and not ts, "confirm-not-delivered must have no message timestamp")
        del state["incident"]["posts"][operation]
    if pending_posts(state):
        record = ledger.save(state, "claims")
        # A known original may turn green; other uncertain posts stay blocked.
        return [record] if state["incident"]["resolution"] is not None and needs_parent_delivery(state) else []
    replay = state["deferred"]
    state["deferred"] = []
    for item in sorted(replay, key=lambda value: (instant(value["time"]), observation_key(value))):
        if pending_recovery_update(state):
            state["deferred"].append(item)
        else:
            state = fold(state, item, identity, ancestor=ancestor)
    if state["kind"] == "deployment":
        evidence = success(ledger.api, state["scope"])
        if evidence:
            state = fold(state, evidence, identity, ancestor=ancestor)
    incident = state["incident"]
    for key in desired_posts(state):
        if key not in incident["posts"]:
            incident["posts"][key] = {"state": "claimed", "ts": None}
    record = ledger.save(state, "claims")
    if pending_posts(state) or needs_parent_delivery(state):
        return [record]
    return []


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
            action = inputs.get("action") or "reconcile-delivery"
            require(action in {"reconcile-delivery", "supervision"}, "unknown notification dispatch action")
            if action == "supervision":
                require(not any(inputs.get(key) for key in ("incident_id", "operation", "message_ts"))
                        and inputs.get("delivery_result", "none") in {"", "none"}, "supervision cannot also reconcile delivery")
                require(re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}", inputs.get("supervision_request_id", "")), "supervision requires its exact CLI request identity")
                records = supervise(ledger, identity, json.loads(inputs["supervision_request"]))
            elif inputs["delivery_result"] == "seal-checkpoint":
                require(not inputs.get("supervision_request") and not inputs.get("supervision_request_id"), "delivery reconciliation cannot contain supervision")
                require(not inputs.get("message_ts"), "checkpoint sealing does not acknowledge a Slack message")
                records = seal_checkpoint(ledger, identity, inputs["incident_id"], inputs["operation"])
            else:
                require(not inputs.get("supervision_request") and not inputs.get("supervision_request_id"), "delivery reconciliation cannot contain supervision")
                records = reconcile(ledger, identity, inputs["incident_id"], inputs["operation"], inputs["delivery_result"], inputs.get("message_ts", ""))
        elif run["event"] == "schedule":
            # Reconsider clocks even when no new source completion arrives.
            # The cloud shadow collector joins this same complete cycle in the
            # next rollout stage; pending ownership must already be bounded.
            records = prepare(ledger, identity, [])
        else:
            targets = job_targets()
            since = os.environ.get("SHARED_DATASETS_SLACK_INCIDENTS_SINCE", "")
            activated = instant(since)
            observations = source_observations(api, event, targets)
            observations = [item for item in observations if instant(item["time"]) >= activated]
            if alerts.WORKFLOW_PATHS[event["workflow_run"]["name"]] == "deployment-verification.yml":
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
        current_records = []
        for record in records:
            payload = record["payload"]
            require(payload["execution_run_id"] == run["id"] and payload["execution_run_attempt"] == run["run_attempt"], "prepared claim belongs to another worker")
            current = api.get(f"repos/{REPOSITORY}/deployments/{record['id']}")
            # Posting the checkpoint's status advances GitHub's updated_at.
            # Every other field must still match; delivery verifies its signature.
            require({key: value for key, value in current.items() if key != "updated_at"}
                    == {key: value for key, value in record.items() if key != "updated_at"}, "prepared claim changed")
            current_records.append(current)
        deliver(ledger, current_records, slack)


if __name__ == "__main__":
    main()
