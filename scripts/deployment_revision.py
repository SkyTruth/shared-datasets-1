#!/usr/bin/env python3
"""Verify tested-main provenance and serialize attempts under the protected target lock."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.workflow_identity import WORKFLOW_NAMES
from scripts import deployment_emission as emissions
from scripts.dataset_mutation_authorization import GitHub as ReadOnlyGitHub, executor_acceptance
from scripts.terraform_target_contracts import registry
from scripts.ingestion_targets import DEPLOY_TARGETS as INGESTION_CONFIGS, image_variables
from scripts.terraform_plan_allowlist import plan_changes

SHA = re.compile(r"[0-9a-f]{40}")
TARGET = re.compile(r"[a-z][a-z0-9-]{0,80}")
SCHEMA = "shared-datasets-deployment-v1"
TARGET_WORKFLOWS = {row['image'] if row['record'] is True else row['record']: filename
                    for filename, row in registry(section='workflows').items() if row.get('record')}
TERRAFORM_SYNCS = registry()
TERRAFORM_WORKFLOWS = {
    "terraform-" + hashlib.sha256(name.encode()).hexdigest()[:16]: target["workflow"]
    for name, target in TERRAFORM_SYNCS.items()
}
TERRAFORM_DEPLOYMENT_HOLDS = {
    "terraform-" + hashlib.sha256(name.encode()).hexdigest()[:16]: target["deployment_hold"]
    for name, target in TERRAFORM_SYNCS.items() if target.get("deployment_hold")
}
INGESTION_TARGETS = frozenset(INGESTION_CONFIGS)
INGESTION_SIGNER = ".github/workflows/ingestion-deploy.yml"


class DeploymentError(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise DeploymentError(message)


EXECUTION_URL = "https://console.cloud.google.com/run/jobs/executions/details/us-central1/{}?project=shared-datasets-1"


def execution_name(url):
    match = re.fullmatch(re.escape(EXECUTION_URL).replace(r"\{\}", r"([a-z][a-z0-9-]{0,62})"), url)
    require(match is not None, "deployment lacks its exact canary execution identity")
    return match[1]


class GitHub(ReadOnlyGitHub):
    require = staticmethod(require)
    def post(self, path, payload):
        return json.loads(subprocess.check_output(["gh", "api", "--method", "POST", path, "--input", "-"], input=json.dumps(payload).encode()))


def verify_run(api, repository, run, *, paths, events, repo=None, workflow=None,
               reason="untrusted deployment execution workflow/event"):
    """Validate an API run's repository, workflow and main provenance once."""
    repo = repo if repo is not None else api.get(f"repos/{repository}")
    require(type(repo.get("id")) is int and repo["id"] > 0 and repo.get("full_name") == repository, "repository identity mismatch")
    require(run.get("path") in paths and run.get("event") in events and run.get("head_branch") == "main", reason)
    workflow = workflow if workflow is not None else api.get(f"repos/{repository}/actions/workflows/{run['path'].rsplit('/', 1)[-1]}")
    require(type(workflow.get("id")) is int and workflow["id"] > 0
            and workflow["id"] == run.get("workflow_id") and workflow.get("path") == run["path"], "workflow identity mismatch")
    require(all(run.get(key, {}).get("full_name") == repository and run.get(key, {}).get("id") == repo["id"]
                for key in ("repository", "head_repository")), "execution repository mismatch")
    return repo


def verify_ci(api, repository, executor, run_id, attempt):
    require(SHA.fullmatch(executor), "executor must be a complete SHA")
    require(type(run_id) is int and run_id > 0 and type(attempt) is int and attempt > 0, "source run and attempt must be positive integers")
    repo = api.get(f"repos/{repository}")
    workflow = api.get(f"repos/{repository}/actions/workflows/ci.yml")
    run = api.get(f"repos/{repository}/actions/runs/{run_id}/attempts/{attempt}")
    verify_run(api, repository, run, paths={".github/workflows/ci.yml"}, events={"push"}, repo=repo, workflow=workflow,
               reason="source must be the repository CI workflow on main push")
    require(run.get("id") == run_id and run.get("run_attempt") == attempt, "CI run attempt mismatch")
    require(run.get("head_sha") == executor, "executor differs from tested CI revision")
    jobs = api.pages(f"repos/{repository}/actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100", field="jobs")
    ready = [job for job in jobs if job.get("name") == "ci-ready"]
    require(len(ready) == 1 and ready[0].get("status") == "completed" and ready[0].get("conclusion") == "success", "ci-ready is missing, ambiguous, cancelled or unsuccessful")
    return run


def record_payload(record):
    payload = record.get("payload")
    if isinstance(payload, str):
        payload = json.loads(payload)
    require(isinstance(payload, dict) and payload.get("schema") == SCHEMA, "unrecognized deployment record; reconcile before mutation")
    require(SHA.fullmatch(str(record.get("sha", ""))) and record.get("ref") == record["sha"], "deployment record has no immutable SHA")
    require(TARGET.fullmatch(str(payload.get("target", ""))) and record.get("environment") == f"production-{payload['target']}", "deployment target mismatch")
    require(all(type(payload.get(key)) is int and payload[key] > 0 for key in ("ci_run_id", "ci_run_attempt", "execution_run_id", "execution_run_attempt")), "deployment record lacks run provenance")
    require(re.fullmatch(r"[a-z0-9./_-]+@sha256:[0-9a-f]{64}", str(payload.get("artifact", ""))), "deployment record lacks immutable artifact")
    require(record.get("creator", {}).get("login") == "github-actions[bot]" and record.get("creator", {}).get("type") == "Bot", "deployment record was not created by Actions")
    return payload



def verify_record_identity(api, repository, record):
    """Check claimed API identity; this alone never proves protected emission."""
    payload = record_payload(record)
    repo = verify_ci(api, repository, record["sha"], payload["ci_run_id"], payload["ci_run_attempt"])["repository"]
    execution = api.get(f"repos/{repository}/actions/runs/{payload['execution_run_id']}/attempts/{payload['execution_run_attempt']}")
    target_workflow = TERRAFORM_WORKFLOWS.get(payload["target"], TARGET_WORKFLOWS.get(payload["target"]))
    require(target_workflow is not None, "unknown deployment target contract")
    require(execution.get("id") == payload["execution_run_id"] and execution.get("run_attempt") == payload["execution_run_attempt"], "deployment execution attempt mismatch")
    allowed_callers = {".github/workflows/ci.yml", f".github/workflows/{target_workflow}"}
    if payload["target"] == "catalog-web":
        allowed_callers.add(".github/workflows/publish-dataset.yml")
    allowed_events = {"push", "workflow_dispatch", "workflow_run"}
    if payload["target"] == "catalog-web" and execution.get("path") == ".github/workflows/catalog-web-deploy.yml" and execution.get("head_sha") == record["sha"]:
        allowed_events.add("schedule")
    verify_run(api, repository, execution, paths=allowed_callers, events=allowed_events, repo=repo)
    if execution["event"] == "push":
        require(execution.get("head_sha") == record["sha"] and payload["execution_run_id"] == payload["ci_run_id"] and payload["execution_run_attempt"] == payload["ci_run_attempt"], "record is not its tested push attempt")
    return payload


def verify_record(api, repository, record, *, statuses=None, requested_status=None):
    payload = verify_record_identity(api, repository, record)
    if statuses is None:
        statuses = api.pages(f"repos/{repository}/deployments/{record['id']}/statuses?per_page=100")
    started = [status for status in statuses if status.get("description") in {"started", "deferred"}]
    require(len(started) == 1, "record lacks an unambiguous protected initial emission")
    # A signed same-execution failure proves ownership after claim signing failed; mutation still requires a signed claim.
    original = emissions.invocation(repository, payload["execution_run_id"], payload["execution_run_attempt"])
    failure_proofs = [status for status in statuses if status.get("description") in {"failed", "unknown"} and status.get("log_url") == original]
    proof = max(failure_proofs, key=lambda status: status["id"]) if failure_proofs else started[0]
    verify_receipt(api, repository, record, proof)
    if requested_status is not None and requested_status != proof:
        verify_receipt(api, repository, record, requested_status)
    return payload


def receipt_signer(api, repository, target, run_id, attempt):
    """Accept historical wrappers or immutable registered leaves bound to the emitting run graph."""
    signer = "prod-terraform-target-apply.yml" if target in TERRAFORM_WORKFLOWS else TARGET_WORKFLOWS.get(target)
    require(signer is not None, "receipt target has no trusted signer contract")
    if target in INGESTION_TARGETS:
        run = api.get(f"repos/{repository}/actions/runs/{run_id}/attempts/{attempt}")
        require(run.get("id") == run_id and run.get("run_attempt") == attempt, "signer execution attempt mismatch")
        # GitHub may flatten a nested reusable chain or include child references.
        # Inspect the entire immutable graph; never select an arbitrary leaf.
        def leaves(references):
            require(isinstance(references, list), "invalid immutable signer graph")
            for item in references:
                require(isinstance(item, dict), "invalid immutable signer reference")
                yield item
                yield from leaves(item.get("referenced_workflows", []))

        path = f"{repository}/{INGESTION_SIGNER}@"
        shared = [item for item in leaves(run.get("referenced_workflows", []))
                  if str(item.get("path", "")).startswith(path)]
        if shared:
            require(SHA.fullmatch(str(run.get("head_sha", ""))) and all(
                    item.get("path") in {path + run["head_sha"], path + "refs/heads/main"}
                    and item.get("sha") == run["head_sha"] and item.get("ref") == "refs/heads/main" for item in shared),
                    "shared signer revision differs from immutable emitting workflow")
            return INGESTION_SIGNER
    return ".github/workflows/" + signer


def verify_receipt(api, repository, record, status):
    payload = record_payload(record)
    signer = receipt_signer(api, repository, payload["target"], payload["execution_run_id"], payload["execution_run_attempt"])
    try:
        emissions.verify(api, repository, record, status, original_signer=signer)
    except emissions.EmissionError as error:
        raise DeploymentError(str(error)) from error


def status_log():
    return emissions.invocation(os.environ["GITHUB_REPOSITORY"], int(os.environ["GITHUB_RUN_ID"]), int(os.environ["GITHUB_RUN_ATTEMPT"]))


def post_status(api, repository, record, phase, environment_url=""):
    payload = {"state": emissions.PHASES[phase], "description": phase, "auto_inactive": False,
               "environment_url": environment_url, "log_url": status_log()}
    status = api.post(f"repos/{repository}/deployments/{record['id']}/statuses", payload)
    emissions.emit(repository, record, {**payload, **status})
    return status


def verified_statuses(api, repository, record):
    statuses = api.pages(f"repos/{repository}/deployments/{record['id']}/statuses?per_page=100")
    if statuses:
        verify_receipt(api, repository, record, statuses[0])
    return statuses

def replay_decision(records, executor, ancestor, statuses, *, artifact=None, target=None):
    """A newer attempted revision bars replay even after its deployment fails."""
    same = []
    superseded = False
    for record in records:
        record_payload(record)
        prior = record["sha"]
        if prior == executor:
            same.append(record)
        elif not ancestor(prior, executor):
            require(ancestor(executor, prior), "a divergent deployment attempt already exists; refusing incompatible replay")
            superseded = True
    if same:
        latest = max(same, key=lambda record: record["id"])
        terminal = statuses(latest)
        if terminal and terminal[0].get("description") == "deferred" and terminal[0].get("state") == "pending":
            require(artifact is None or record_payload(latest)["artifact"] == artifact,
                    "same revision now produces different artifact bytes; reconcile before recovery")
            return "superseded" if superseded else "proceed"
        accepted = {"verified"} if record_payload(latest)["target"] in INGESTION_TARGETS else {"applied", "verified"}
        require(terminal and terminal[0].get("state") == "success" and terminal[0].get("description") in accepted,
                "this revision has an incomplete or failed attempt; reconcile actual target state before recovery")
        # Scratch builds and newly authorized catalog bytes may repeat the same executor.
        if target != "wdpa-monthly-build":
            if artifact is None or record_payload(latest)["artifact"] == artifact:
                return "superseded" if superseded else "noop"
            require(target == "catalog-web", "same revision now produces different artifact bytes; reconcile before recovery")
    return "superseded" if superseded else "proceed"


def git_ancestor(older, newer):
    result = subprocess.run(["git", "merge-base", "--is-ancestor", older, newer], capture_output=True)
    require(result.returncode in (0, 1), "ancestry comparison failed; require full checkout history")
    return result.returncode == 0


def verify_context(args, api):
    repository = os.environ["GITHUB_REPOSITORY"]
    require(args.workflow in (TARGET_WORKFLOWS | TERRAFORM_WORKFLOWS).values(), "unknown deployment workflow contract")
    require(os.environ.get("GITHUB_REF") == "refs/heads/main", "production requires main ref")
    current = api.get(f"repos/{repository}/actions/runs/{os.environ['GITHUB_RUN_ID']}")
    allowed_path = f".github/workflows/{args.workflow}"
    allowed_callers = {".github/workflows/ci.yml", allowed_path}
    if args.workflow == "catalog-web-deploy.yml":
        allowed_callers.add(".github/workflows/publish-dataset.yml")
    allowed_events = {"push", "workflow_dispatch", "workflow_run"}
    if args.workflow == "catalog-web-deploy.yml" and current.get("path") == allowed_path and current.get("head_sha") == args.executor_sha:
        allowed_events.add("schedule")
    repo = verify_run(api, repository, current, paths=allowed_callers, events=allowed_events)
    if current.get("event") == "push":
        require(current.get("path") == ".github/workflows/ci.yml" and current.get("head_sha") == args.executor_sha,
                "automatic deployment must execute its current tested main push")
        require(args.source_run_id == int(os.environ["GITHUB_RUN_ID"]) and args.source_run_attempt == int(os.environ["GITHUB_RUN_ATTEMPT"]), "automatic deployment source must be its current run attempt")
    if current.get("event") == "workflow_run":
        event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
        source = event.get("workflow_run", {})
        if args.workflow == "catalog-web-deploy.yml" and source.get("name") in WORKFLOW_NAMES["release-index-rebuild.yml"]:
            upstream = api.get(f"repos/{repository}/actions/runs/{source['id']}/attempts/{source['run_attempt']}")
            upstream_workflow = api.get(f"repos/{repository}/actions/workflows/release-index-rebuild.yml")
            require(upstream.get("workflow_id") == upstream_workflow.get("id") and upstream.get("path") == ".github/workflows/release-index-rebuild.yml"
                    and upstream.get("head_sha") == args.executor_sha and upstream.get("head_branch") == "main" and upstream.get("conclusion") == "success"
                    and upstream.get("repository", {}).get("id") == repo["id"] and upstream.get("head_repository", {}).get("id") == repo["id"],
                    "untrusted release-index completion")
        else:
            require(source.get("id") == args.source_run_id and source.get("run_attempt") == args.source_run_attempt and source.get("head_sha") == args.executor_sha,
                    "deployment source differs from its upstream completion event")
    checkout = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    if getattr(args, "bootstrap", False):
        trusted = os.environ.get("BOOTSTRAP_WORKFLOW_SHA", "")
        require(SHA.fullmatch(trusted) and checkout == trusted, "bootstrap must execute the immutable trusted workflow revision")
        require(os.environ.get("GITHUB_WORKFLOW_REF") in {f"{repository}/{path}@refs/heads/main" for path in allowed_callers}, "bootstrap workflow must originate from main")
    else:
        require(checkout == args.executor_sha, "checkout is not the tested executor SHA")
    require(git_ancestor(args.executor_sha, "origin/main"), "executor is not a merged main revision")
    verify_ci(api, repository, args.executor_sha, args.source_run_id, args.source_run_attempt)
    return repository


def check(args, api):
    if args.target in TERRAFORM_DEPLOYMENT_HOLDS:
        raise DeploymentError("deployment held: " + TERRAFORM_DEPLOYMENT_HOLDS[args.target])
    repository = verify_context(args, api)
    require(TARGET.fullmatch(args.target) and args.target in (TARGET_WORKFLOWS | TERRAFORM_WORKFLOWS), "invalid deployment target")
    require((TARGET_WORKFLOWS | TERRAFORM_WORKFLOWS)[args.target] == args.workflow, "deployment target differs from its workflow contract")
    records = api.pages(f"repos/{repository}/deployments?environment=production-{args.target}&per_page=100")
    decision = replay_decision(records, args.executor_sha, git_ancestor, lambda record: verified_statuses(api, repository, record), artifact=getattr(args, "artifact", None), target=args.target)
    # Verify provenance of records that can block replay or establish a no-op under the target lock.
    decisive = [record for record in records if record["sha"] == args.executor_sha or not git_ancestor(record["sha"], args.executor_sha)]
    for record in decisive:
        verify_record(api, repository, record)
    if decision == "superseded":
        latest = max((record for record in decisive if record["sha"] != args.executor_sha), key=lambda record: record["id"])
        message = f"Deployment skipped: tested revision {args.executor_sha} was superseded by a newer recorded attempt at {latest['sha']}."
        print("::notice::" + message)
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with Path(summary).open("a") as stream:
                stream.write(message + " No deployment record or target state was changed.\n")
        return {"proceed": "false", "outcome": "superseded", "superseded_by_sha": latest["sha"]}
    if getattr(args, "deferred", False) and any(record["sha"] == args.executor_sha for record in decisive):
        return {"proceed": "false"}  # Keep the first signed deferral and its lag clock.
    if decision == "noop":
        print("A successful deployment of this exact revision already exists.")
        return {"proceed": "false"}
    return {"proceed": "true"}


def github_time(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def previous_attempt_statuses(api, repository, target):
    """Statuses of the target's newest attempt, or None when it has never been attempted.

    Read before claiming, so a failed read leaves no unfinished deployment record.
    """
    records = api.pages(f"repos/{repository}/deployments?environment=production-{target}&per_page=100")
    if not records:
        return None
    latest = max(records, key=lambda record: record["id"])
    return api.pages(f"repos/{repository}/deployments/{latest['id']}/statuses?per_page=100")


def propagation_deadline(plan, window, previous):
    """Before claiming: a fixed IAM propagation wait in seconds, or the GitHub time
    at which the previous attempt's window ends.

    A changing plan waits the full window. An empty plan waits out the rest of the
    previous attempt's window: that attempt may have granted IAM moments ago and
    released the serialized state lock before its own barrier finished.
    """
    if window <= 0:
        return 0
    if plan_changes(plan):
        return window
    if previous is None:
        return 0
    if not any(status.get("description") not in {"started", "deferred"} for status in previous):
        return window  # Without a terminal status the attempt may have mutated IAM at any later time.
    return max(github_time(status["created_at"]) for status in previous) + timedelta(seconds=window)


def propagation_seconds(deadline, window, claimed_at):
    """After claiming: seconds until the deadline, measured on GitHub's clock from
    this claim's started status, so runner clock skew cannot shorten the wait."""
    if not isinstance(deadline, datetime):
        return deadline
    return max(0, min(window, math.ceil((deadline - github_time(claimed_at)).total_seconds())))


def start(args, api):
    decision = check(args, api)
    if decision["proceed"] == "false":
        return decision
    repository = os.environ["GITHUB_REPOSITORY"]
    signer = receipt_signer(api, repository, args.target, int(os.environ["GITHUB_RUN_ID"]), int(os.environ["GITHUB_RUN_ATTEMPT"]))
    try:
        emissions.verify_rehearsal(api, repository, signer)
    except emissions.EmissionError as error:
        raise DeploymentError(str(error)) from error
    require(re.fullmatch(r"[a-z0-9./_-]+@sha256:[0-9a-f]{64}", args.artifact), "deployment artifact must be an immutable image digest")
    require(not getattr(args, "deferred", False) or args.target in INGESTION_TARGETS, "only ingestion rollouts can defer")
    plan = json.loads(Path(args.plan_json).read_text()) if args.plan_json else {}
    targets = [r["address"] for r in plan.get("resource_changes", [])]
    config_digest = plan.get("variables", {}).get("wdpa_monthly_image_config_digest", {}).get("value", "")
    if args.target == "wdpa-monthly" and not getattr(args, "deferred", False):
        image_variables(args.target, args.artifact, config_digest)
    if getattr(args, "plan_scope", None):
        targets = [target for target in args.plan_scope.splitlines() if target]
    require(all(re.fullmatch(r'[A-Za-z0-9_.\[\]"-]+', target) for target in targets), "invalid saved-plan target")
    window = getattr(args, "propagation_window", None)
    deadline = None
    if window is not None and not getattr(args, "deferred", False):
        # History reads and their timestamp parsing finish before the claim exists.
        previous = previous_attempt_statuses(api, repository, args.target) if window > 0 and not plan_changes(plan) else None
        deadline = propagation_deadline(plan, window, previous)
    record = api.post(f"repos/{repository}/deployments", {
        "ref": args.executor_sha, "environment": f"production-{args.target}",
        "auto_merge": False, "required_contexts": [], "production_environment": True,
        "payload": {"schema": SCHEMA, "target": args.target, "ci_run_id": args.source_run_id, "ci_run_attempt": args.source_run_attempt,
                    "artifact": args.artifact, "targets": targets, "execution_run_id": int(os.environ["GITHUB_RUN_ID"]), "execution_run_attempt": int(os.environ["GITHUB_RUN_ATTEMPT"]),
                    "image_config_digest": config_digest},
    })
    phase = "deferred" if getattr(args, "deferred", False) else "started"
    status = post_status(api, repository, record, phase)
    if getattr(args, "deferred", False):
        return {"proceed": "false"}
    result = {"proceed": "true", "deployment_id": str(record["id"])}
    if deadline is not None:
        result["propagation_seconds"] = str(propagation_seconds(deadline, window, status["created_at"]))
    return result


def finish(args, api):
    repository = os.environ["GITHUB_REPOSITORY"]
    record = api.get(f"repos/{repository}/deployments/{args.deployment_id}")
    payload = (verify_record_identity if args.phase in {"failed", "unknown"} else verify_record)(api, repository, record)
    require(payload.get("execution_run_id") == int(os.environ["GITHUB_RUN_ID"]) and payload.get("execution_run_attempt") == int(os.environ["GITHUB_RUN_ATTEMPT"]), "cannot finish another deployment attempt")
    require(not args.execution or TARGET.fullmatch(args.execution), "invalid Cloud Run execution identifier")
    environment_url = EXECUTION_URL.format(args.execution) if args.execution else ""
    post_status(api, repository, record, args.phase, environment_url)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with Path(summary).open("a") as stream:
            stream.write(f"Deployment {args.deployment_id}: **{args.phase}**; executor `{record['sha']}`.\n")
    require(args.phase != "unknown", "deployment has no terminal runtime evidence; reconcile before recovery")



def pending(api, repository):
    identifiers = []
    for target, config in INGESTION_CONFIGS.items():
        if config["canary_mode"] != "deferred":
            continue
        for record in api.pages(f"repos/{repository}/deployments?environment=production-{target}&per_page=100"):
            record_payload(record)
            statuses = api.pages(f"repos/{repository}/deployments/{record['id']}/statuses?per_page=100")
            if statuses and statuses[0].get("state") == "in_progress" and statuses[0].get("description") == "verification_pending":
                identifiers.append(record["id"])
    return identifiers


def terminal_execution(raw, image):
    require(raw.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [{}])[0].get("image") == image,
            "canary execution does not use the recorded tested image")
    status = raw.get("status", {})
    if status.get("cancelledCount", 0) or status.get("failedCount", 0):
        return "failed"
    if status.get("completionTime"):
        return "verified" if status.get("succeededCount") == 1 else "unknown"
    return "verification_pending"


def verification_window(executions, *, paused=False, canary_date="", allow_cancel=False):
    require(not canary_date or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", canary_date), "invalid canary date")
    if paused and not canary_date:
        return False
    return allow_cancel or all(execution.get("status", {}).get("completionTime") for execution in executions)


def runtime_window(args):
    require(args.target in INGESTION_TARGETS, "invalid runtime verification target")
    # First installation has no job/execution collection yet. A successful
    # regional inventory proves absence; permission/network errors still fail.
    config = INGESTION_CONFIGS[args.target]
    inspect_executions = config["plan_guard"] != "usage"
    if config["plan_guard"] == "install":
        jobs = json.loads(subprocess.check_output(["gcloud", "run", "jobs", "list", "--region=us-central1",
                                                   "--project=shared-datasets-1", "--format=json"], timeout=60))
        require(isinstance(jobs, list), "invalid Cloud Run job inventory")
        inspect_executions = any(job["metadata"]["name"].rsplit("/", 1)[-1] == args.target for job in jobs)
    paused = False
    if config["paused_gate"]:
        state = subprocess.check_output(["gcloud", "scheduler", "jobs", "describe", args.target, "--location=us-central1",
                                         "--project=shared-datasets-1", "--format=value(state)"], text=True, timeout=60).strip()
        require(state in {"ENABLED", "PAUSED"}, "unknown scheduler state prevents runtime verification")
        paused = state == "PAUSED"
    wait = getattr(args, "wait_seconds", 0)
    require(wait >= 0, "runtime wait must be nonnegative")
    deadline = time.monotonic() + wait
    cancelled = set()
    while True:
        executions = json.loads(subprocess.check_output([
            "gcloud", "run", "jobs", "executions", "list", "--job=" + args.target,
            "--region=us-central1", "--project=shared-datasets-1", "--format=json",
        ], timeout=60)) if inspect_executions else []
        require(isinstance(executions, list), "invalid Cloud Run execution inventory")
        active = [entry for entry in executions if not entry.get("status", {}).get("completionTime")]
        # Preparation/application may admit explicit cancellation. Verification
        # must actually wait for cancellation or scheduled work to drain.
        ready = verification_window(executions, paused=paused, canary_date=args.canary_run_date,
                                    allow_cancel=args.allow_cancel == "true" and not wait)
        if ready or not wait or paused and not args.canary_run_date:
            break
        if args.allow_cancel == "true":
            for entry in active:
                name = entry["metadata"]["name"]
                if name not in cancelled:
                    subprocess.check_call(["gcloud", "run", "jobs", "executions", "cancel", name,
                                           "--region=us-central1", "--project=shared-datasets-1", "--quiet"], timeout=60)
                    cancelled.add(name)
        remaining = deadline - time.monotonic()
        require(remaining > 0, "active execution did not drain within the runtime verification window")
        print("::notice::Waiting for the active execution before starting a canary.", flush=True)
        time.sleep(min(30, remaining))
    if not ready:
        print("::notice::Deployment deferred: runtime verification is unavailable while the schedule is paused or an execution is active.")
    with Path(os.environ["GITHUB_OUTPUT"]).open("a") as stream:
        stream.write("ready=" + str(ready).lower() + "\n")


def observe(args, api):
    repository = os.environ["GITHUB_REPOSITORY"]
    require(os.environ.get("GITHUB_REF") == "refs/heads/main" and os.environ.get("GITHUB_WORKFLOW_REF") ==
            f"{repository}/.github/workflows/deployment-verification.yml@refs/heads/main", "untrusted terminal verifier")
    record = api.get(f"repos/{repository}/deployments/{args.deployment_id}")
    payload = verify_record(api, repository, record)
    require(payload.get("target") in INGESTION_TARGETS and INGESTION_CONFIGS[payload["target"]]["canary_mode"] == "deferred",
            "only detached executions need terminal observation")
    require(git_ancestor(record["sha"], "origin/main"), "record is not from main history")
    statuses = verified_statuses(api, repository, record)
    require(statuses and statuses[0].get("description") == "verification_pending", "deployment is not awaiting verification")
    name = execution_name(statuses[0].get("environment_url", ""))
    raw = json.loads(subprocess.check_output(["gcloud", "run", "jobs", "executions", "describe", name, "--region=us-central1", "--project=shared-datasets-1", "--format=json"]))
    phase = terminal_execution(raw, payload["artifact"])
    if phase == "verification_pending":
        print(f"Execution {name} is pending; terminal verification remains incomplete.")
        return
    post_status(api, repository, record, phase, statuses[0]["environment_url"])
    with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a") as stream:
        stream.write(f"Canary `{name}`: **{phase}**; image `{payload['artifact']}`; executor `{record['sha']}`.\n")
    require(phase == "verified", f"terminal canary outcome: {phase}")


def recovery_record(args, api):
    repository = os.environ["GITHUB_REPOSITORY"]
    require(os.environ.get("GITHUB_REF") == "refs/heads/main" and os.environ.get("GITHUB_WORKFLOW_REF") ==
            f"{repository}/.github/workflows/deployment-recovery.yml@refs/heads/main", "untrusted reconciliation workflow")
    record = api.get(f"repos/{repository}/deployments/{args.deployment_id}")
    payload = verify_record(api, repository, record)
    require(payload.get("target") in INGESTION_TARGETS | {"typescript-sdk"} | set(TERRAFORM_WORKFLOWS), "recovery requires a reviewed target-specific workflow")
    targets = payload.get("targets")
    if payload["target"] == "typescript-sdk":
        require(re.fullmatch(r"sdk@sha256:[0-9a-f]{64}", payload["artifact"]) and targets == [], "SDK recovery requires its package digest and no Terraform scope")
    else:
        require(isinstance(targets, list) and targets and all(re.fullmatch(r'[A-Za-z0-9_.\[\]"-]+', target) for target in targets), "record has no saved-plan scope; use reviewed recovery")
    return record, payload


def reconcile_sdk(record, payload, root):
    """Consume exact passing CI evidence and compare actual public package bytes."""
    from urllib import request
    from scripts import sdk_release_authorization as release

    outputs = release.download(release.GitHub(), os.environ["GITHUB_REPOSITORY"],
                               payload["ci_run_id"], payload["ci_run_attempt"], record["sha"],
                               Path(os.environ["RUNNER_TEMP"]) / "sdk-reconciliation", root)
    require(outputs.get("artifact") == payload["artifact"] and outputs.get("release_needed") == "true", "SDK record differs from the original tested release")
    policy = Path(__file__).resolve().parents[1] / "api/typescript/scripts/release-policy.mjs"
    confirmed = subprocess.check_output(["node", str(policy), "registry", outputs["candidate"]],
                                        cwd=root / "api/typescript", text=True,
                                        env={**os.environ, "GITHUB_OUTPUT": ""}).strip()
    require(confirmed == "should_publish=false", "registry has not confirmed the original SDK version and integrity")
    candidate = json.loads(Path(outputs["candidate"]).read_text())
    require(re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", candidate["version"]), "invalid published SDK version")
    url = "https://registry.npmjs.org/@skytruth/shared-datasets/-/shared-datasets-" + candidate["version"] + ".tgz"
    with request.urlopen(url, timeout=30) as response:
        published = response.read(release.MAX_ARCHIVE + 1)
    require(len(published) <= release.MAX_ARCHIVE and published == Path(outputs["tarball"]).read_bytes(), "registry tarball differs from the exact tested SDK package")


def recover(args, api):
    record, payload = recovery_record(args, api)
    repository = os.environ["GITHUB_REPOSITORY"]
    if args.command == "recovery-inputs":
        with Path(os.environ["GITHUB_OUTPUT"]).open("a") as stream:
            stream.write(f"executor_sha={record['sha']}\ntarget={payload['target']}\nimage={payload['artifact']}\n")
            stream.write("targets=" + json.dumps(payload["targets"]) + "\n")
            stream.write("image_variables=" + json.dumps(image_variables(payload["target"], payload["artifact"], payload.get("image_config_digest", ""))) + "\n")
            stream.write("kind=" + ("sdk" if payload["target"] == "typescript-sdk" else "terraform" if payload["target"] in TERRAFORM_WORKFLOWS else "ingestion") + "\n")
        return
    sdk = payload["target"] == "typescript-sdk"
    require((args.executor_dir is not None) == sdk and (args.plan_json is None) == sdk,
            "SDK reconciliation requires only its original executor directory; infrastructure requires only its original target plan")
    executor = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True,
        **({"cwd": Path(args.executor_dir).resolve()} if sdk else {})).strip()
    require(executor == record["sha"], "recovery checkout differs from original tested executor")
    for other in api.pages(f"repos/{repository}/deployments?environment={record['environment']}&per_page=100"):
        record_payload(other)
        require(other["id"] == record["id"] or (other["sha"] != record["sha"] and git_ancestor(other["sha"], record["sha"])), "a later attempt prevents reconciliation of this revision")
    url = ""
    if sdk:
        reconcile_sdk(record, payload, Path(args.executor_dir).resolve())
    else:
        plan = json.loads(Path(args.plan_json).read_text())
        require(all(r.get("change", {}).get("actions") in ([], ["no-op"], ["read"]) for r in plan.get("resource_changes", [])), "target has drift or an incomplete apply; reconciliation must not apply it automatically")
        if payload["target"] in INGESTION_TARGETS:
            raw = json.loads(subprocess.check_output(["gcloud", "run", "jobs", "describe", payload["target"], "--region=us-central1", "--project=shared-datasets-1", "--format=json"]))
            image = raw.get("spec", {}).get("template", {}).get("spec", {}).get("template", {}).get("spec", {}).get("containers", [{}])[0].get("image")
            require(image == payload["artifact"], "live job does not use original tested artifact")
            executions = json.loads(subprocess.check_output(["gcloud", "run", "jobs", "executions", "list", f"--job={payload['target']}", "--region=us-central1", "--project=shared-datasets-1", "--format=json"]))
            matching = [entry for entry in executions if entry.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [{}])[0].get("image") == image]
            require(matching, "no execution of the exact image proves runtime success")
            latest = max(matching, key=lambda entry: entry.get("metadata", {}).get("creationTimestamp", ""))
            require(terminal_execution(latest, image) == "verified", "latest exact-image execution is incomplete or failed; fix or use reviewed recovery")
            url = EXECUTION_URL.format(latest["metadata"]["name"])
            execution_name(url)
    post_status(api, repository, record, "applied" if payload["target"] in TERRAFORM_WORKFLOWS else "verified", url)
    print("Original tested target and applicable terminal runtime evidence reconciled; no production mutations performed.")

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("verify", "check", "start"):
        begin = commands.add_parser(command)
        begin.add_argument("--workflow", required=True)
        begin.add_argument("--executor-sha", required=True)
        begin.add_argument("--source-run-id", required=True, type=int)
        begin.add_argument("--source-run-attempt", required=True, type=int)
        if command == "verify":
            begin.add_argument("--bootstrap", action="store_true")
        if command != "verify":
            begin.add_argument("--target", required=True)
            begin.add_argument("--artifact", required=command == "start")
        if command == "start":
            begin.add_argument("--deferred", action="store_true")
            begin.add_argument("--plan-json")
            begin.add_argument("--plan-scope")
            begin.add_argument("--propagation-window", type=int)
    commands.add_parser("accept").add_argument("--pr-number", type=int)
    end = commands.add_parser("finish")
    end.add_argument("--deployment-id", required=True, type=int)
    end.add_argument("--execution", default="")
    end.add_argument("--phase", required=True, choices=["applied", "verification_pending", "verified", "failed", "unknown"])
    commands.add_parser("pending")
    window = commands.add_parser("window")
    window.add_argument("--target", required=True)
    window.add_argument("--canary-run-date", default="")
    window.add_argument("--allow-cancel", default="false", choices=["true", "false"])
    window.add_argument("--wait-seconds", type=int, default=0)
    observer = commands.add_parser("observe")
    observer.add_argument("--deployment-id", required=True, type=int)
    for command in ("recovery-inputs", "reconcile"):
        recovery = commands.add_parser(command)
        recovery.add_argument("--deployment-id", required=True, type=int)
        if command == "reconcile":
            recovery.add_argument("--plan-json")
            recovery.add_argument("--executor-dir")
    args = parser.parse_args()
    api = GitHub()
    try:
        if args.command == "accept":
            print(json.dumps(executor_acceptance(api, os.environ["GITHUB_REPOSITORY"], os.environ["EXECUTOR_SHA"], args.pr_number)))
        elif args.command == "window":
            runtime_window(args)
        elif args.command == "pending":
            outputs = {"records": json.dumps(pending(api, os.environ["GITHUB_REPOSITORY"]))}
        else:
            outputs = {"recovery-inputs": recover, "reconcile": recover,
                       "observe": observe, "verify": verify_context, "check": check, "start": start, "finish": finish}[args.command](args, api)
        if args.command in {"pending", "check", "start"}:
            with Path(os.environ["GITHUB_OUTPUT"]).open("a") as stream:
                stream.writelines(f"{key}={value}\n" for key, value in outputs.items())
    except DeploymentError as exc:
        parser.exit(1, f"DEPLOYMENT_NOT_READY: {exc}\n")


if __name__ == "__main__":
    main()
