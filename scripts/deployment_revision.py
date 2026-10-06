#!/usr/bin/env python3
"""Verify tested main provenance and serialize per-target deployment attempts.

Run inside the target's existing non-cancelling production job lock. Deployment
records prevent replay; they never replace approvals, plan allowlists or GCS
publication transactions.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

SHA = re.compile(r"[0-9a-f]{40}")
TARGET = re.compile(r"[a-z][a-z0-9-]{0,80}")
SCHEMA = "shared-datasets-deployment-v1"
TARGET_WORKFLOWS = {
    "wdpa-monthly": "wdpa-monthly-deploy.yml",
    "wdpa-processing-validation": "wdpa-processing-validation-deploy.yml",
    "eamlis-monthly": "eamlis-monthly-deploy.yml",
    "sea-ice-daily": "sea-ice-daily-deploy.yml",
    "typescript-sdk": "publish-typescript-sdk.yml",
    "pmtiles-cdn": "pmtiles-cdn-sync.yml",
    "catalog-web": "catalog-web-deploy.yml",
    "catalog-viewer": "catalog-viewer-deploy.yml",
}
TERRAFORM_SYNCS = {
    "Translation notice secret IAM bootstrap": "scheduled-ingestion-deploy-iam-sync.yml",
    "Scheduled ingestion deploy IAM sync": "scheduled-ingestion-deploy-iam-sync.yml",
    "Artifact Registry IAM sync": "artifact-registry-iam-sync.yml",
    "Artifact Registry writer binding sync": "artifact-registry-iam-sync.yml",
    "Preview Terraform IAM bootstrap": "preview-terraform-iam-sync.yml",
    "Preview Terraform IAM sync": "preview-terraform-iam-sync.yml",
    "Scratch cleanup IAM sync": "scratch-cleanup-iam-sync.yml",
    "Monitoring alert policy IAM bootstrap": "cron-alert-policy-sync.yml",
    "Cron alert policy sync": "cron-alert-policy-sync.yml",
}
TERRAFORM_WORKFLOWS = {
    "terraform-" + hashlib.sha256(name.encode()).hexdigest()[:16]: workflow
    for name, workflow in TERRAFORM_SYNCS.items()
}


class DeploymentError(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise DeploymentError(message)


class GitHub:
    def get(self, path):
        return json.loads(subprocess.check_output(["gh", "api", path]))

    def pages(self, path, field=None):
        pages = json.loads(subprocess.check_output(["gh", "api", "--paginate", "--slurp", path]))
        require(isinstance(pages, list) and pages, "missing API pages")
        if field:
            count = pages[0].get("total_count")
            require(type(count) is int and all(p.get("total_count") == count for p in pages), "inconsistent API enumeration")
            rows = [row for page in pages for row in page[field]]
            require(len(rows) == count, "incomplete API enumeration")
            return rows
        require(all(isinstance(p, list) for p in pages), "invalid API pages")
        return [row for page in pages for row in page]

    def post(self, path, payload):
        return json.loads(subprocess.check_output(["gh", "api", "--method", "POST", path, "--input", "-"], input=json.dumps(payload).encode()))


def verify_ci(api, repository, executor, run_id, attempt):
    require(SHA.fullmatch(executor), "executor must be a complete SHA")
    require(type(run_id) is int and run_id > 0 and type(attempt) is int and attempt > 0, "source run and attempt must be positive integers")
    repo = api.get(f"repos/{repository}")
    workflow = api.get(f"repos/{repository}/actions/workflows/ci.yml")
    require(type(repo.get("id")) is int and repo["id"] > 0 and repo.get("full_name") == repository, "repository identity mismatch")
    require(type(workflow.get("id")) is int and workflow["id"] > 0 and workflow.get("path") == ".github/workflows/ci.yml", "CI workflow identity mismatch")
    run = api.get(f"repos/{repository}/actions/runs/{run_id}/attempts/{attempt}")
    require(run.get("id") == run_id and run.get("run_attempt") == attempt, "CI run attempt mismatch")
    require(run.get("workflow_id") == workflow["id"] and run.get("path") == ".github/workflows/ci.yml", "source must be the repository CI workflow")
    require(run.get("event") == "push" and run.get("head_branch") == "main", "source must be a main push")
    require(run.get("head_sha") == executor, "executor differs from tested CI revision")
    require(all(run.get(key, {}).get("full_name") == repository and run.get(key, {}).get("id") == repo["id"] for key in ("repository", "head_repository")), "source CI repository mismatch")
    jobs = api.pages(f"repos/{repository}/actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100", "jobs")
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



def verify_record(api, repository, record):
    payload = record_payload(record)
    verify_ci(api, repository, record["sha"], payload["ci_run_id"], payload["ci_run_attempt"])
    execution = api.get(f"repos/{repository}/actions/runs/{payload['execution_run_id']}/attempts/{payload['execution_run_attempt']}")
    target_workflow = TERRAFORM_WORKFLOWS.get(payload["target"], TARGET_WORKFLOWS.get(payload["target"]))
    require(target_workflow is not None, "unknown deployment target contract")
    require(execution.get("id") == payload["execution_run_id"] and execution.get("run_attempt") == payload["execution_run_attempt"], "deployment execution attempt mismatch")
    allowed_callers = {".github/workflows/ci.yml", f".github/workflows/{target_workflow}"}
    if payload["target"] == "catalog-web":
        allowed_callers.add(".github/workflows/publish-dataset.yml")
    require(execution.get("path") in allowed_callers and execution.get("event") in {"push", "workflow_dispatch", "workflow_run"}
            and execution.get("head_branch") == "main", "record originates from an untrusted execution")
    workflow = api.get(f"repos/{repository}/actions/workflows/{execution['path'].rsplit('/', 1)[-1]}")
    repo = api.get(f"repos/{repository}")
    require(workflow.get("id") == execution.get("workflow_id") and workflow.get("path") == execution["path"], "record execution workflow mismatch")
    require(all(execution.get(key, {}).get("id") == repo.get("id") and execution.get(key, {}).get("full_name") == repository for key in ("repository", "head_repository")), "record execution repository mismatch")
    if execution["event"] == "push":
        require(execution.get("head_sha") == record["sha"] and payload["execution_run_id"] == payload["ci_run_id"] and payload["execution_run_attempt"] == payload["ci_run_attempt"], "record is not its tested push attempt")
    return payload


def verified_statuses(api, repository, record):
    statuses = api.pages(f"repos/{repository}/deployments/{record['id']}/statuses?per_page=100")
    if statuses and statuses[0].get("state") == "success":
        status = statuses[0]
        require(status.get("creator", {}).get("login") == "github-actions[bot]", "untrusted deployment success status")
        match = re.fullmatch(r"https://github\.com/" + re.escape(repository) + r"/actions/runs/([1-9][0-9]*)", status.get("log_url", ""))
        require(match is not None, "success status lacks execution provenance")
        run = api.get(f"repos/{repository}/actions/runs/{match[1]}")
        repo = api.get(f"repos/{repository}")
        workflow = api.get(f"repos/{repository}/actions/workflows/{run.get('path', '').rsplit('/', 1)[-1]}")
        require(workflow.get("id") == run.get("workflow_id") and workflow.get("path") == run.get("path"), "success status workflow identity mismatch")
        require(all(run.get(key, {}).get("id") == repo.get("id") and run.get(key, {}).get("full_name") == repository for key in ("repository", "head_repository")), "success status repository identity mismatch")
        expected = record["payload"] if isinstance(record["payload"], dict) else json.loads(record["payload"])
        require(run.get("head_branch") == "main" and run.get("event") in {"push", "workflow_dispatch", "workflow_run", "schedule"}, "success status is not a trusted main execution")
        require(int(match[1]) == expected["execution_run_id"] or run.get("path") in {".github/workflows/deployment-verification.yml", ".github/workflows/deployment-recovery.yml"}, "success status has an unrelated execution")
    return statuses

def replay_decision(records, executor, ancestor, statuses, *, artifact=None, target=None):
    """A newer attempted revision bars replay even after its deployment fails."""
    same = []
    for record in records:
        record_payload(record)
        prior = record["sha"]
        if prior == executor:
            same.append(record)
        else:
            require(ancestor(prior, executor), "a newer or divergent deployment attempt already exists; refusing stale replay")
    if same:
        latest = max(same, key=lambda record: record["id"])
        terminal = statuses(latest)
        runtime_targets = {"wdpa-monthly", "eamlis-monthly", "sea-ice-daily"}
        accepted = {"verified"} if record_payload(latest)["target"] in runtime_targets else {"applied", "verified"}
        if terminal and terminal[0].get("state") == "success" and terminal[0].get("description") in accepted:
            prior_artifact = record_payload(latest)["artifact"]
            if artifact is None or prior_artifact == artifact:
                return "noop"
            require(target == "catalog-web", "same revision now produces different artifact bytes; reconcile before recovery")
            # Catalog bytes depend on authorized dataset/index mutations as well
            # as executor code. A distinct current-state bundle is a new refresh.
            return "proceed"
        raise DeploymentError("this revision has an incomplete or failed attempt; reconcile actual target state before recovery")
    return "proceed"


def git_ancestor(older, newer):
    for revision in (older, newer):
        result = subprocess.run(["git", "cat-file", "-e", f"{revision}^{{commit}}"], capture_output=True)
        require(result.returncode == 0, "comparison commit unavailable; require full checkout history")
    result = subprocess.run(["git", "merge-base", "--is-ancestor", older, newer], capture_output=True)
    require(result.returncode in (0, 1), "ancestry comparison failed")
    return result.returncode == 0


def verify_context(args, api):
    repository = os.environ["GITHUB_REPOSITORY"]
    require(args.workflow in set(TARGET_WORKFLOWS.values()) | set(TERRAFORM_SYNCS.values()), "unknown deployment workflow contract")
    require(os.environ.get("GITHUB_REF") == "refs/heads/main", "production requires main ref")
    current = api.get(f"repos/{repository}/actions/runs/{os.environ['GITHUB_RUN_ID']}")
    allowed_path = f".github/workflows/{args.workflow}"
    allowed_callers = {".github/workflows/ci.yml", allowed_path}
    if args.workflow == "catalog-web-deploy.yml":
        allowed_callers.add(".github/workflows/publish-dataset.yml")
    require(current.get("path") in allowed_callers, "untrusted deployment caller")
    require(current.get("event") in {"push", "workflow_dispatch", "workflow_run"} and current.get("head_branch") == "main", "untrusted deployment event")
    repo = api.get(f"repos/{repository}")
    workflow = api.get(f"repos/{repository}/actions/workflows/{current['path'].rsplit('/', 1)[-1]}")
    require(current.get("workflow_id") == workflow.get("id") and workflow.get("path") == current["path"], "deployment workflow identity mismatch")
    require(all(current.get(key, {}).get("full_name") == repository and current.get(key, {}).get("id") == repo["id"] for key in ("repository", "head_repository")), "deployment repository mismatch")
    if current.get("event") == "push":
        require(current.get("path") == ".github/workflows/ci.yml" and current.get("head_sha") == args.executor_sha,
                "automatic deployment must execute its current tested main push")
        require(args.source_run_id == int(os.environ["GITHUB_RUN_ID"]) and args.source_run_attempt == int(os.environ["GITHUB_RUN_ATTEMPT"]), "automatic deployment source must be its current run attempt")
    if current.get("event") == "workflow_run":
        event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
        source = event.get("workflow_run", {})
        if args.workflow == "catalog-web-deploy.yml" and source.get("name") == "Release index rebuild":
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
    repository = verify_context(args, api)
    require(TARGET.fullmatch(args.target) and args.target in (TARGET_WORKFLOWS | TERRAFORM_WORKFLOWS), "invalid deployment target")
    require((TARGET_WORKFLOWS | TERRAFORM_WORKFLOWS)[args.target] == args.workflow, "deployment target differs from its workflow contract")
    environment = f"production-{args.target}"
    records = api.pages(f"repos/{repository}/deployments?environment={environment}&per_page=100")
    for record in records:
        record_payload(record)
    # The serialized writer keeps revisions monotonic. Only records capable of
    # blocking replay or satisfying a no-op need expensive API provenance reads.
    decisive = [record for record in records if record["sha"] == args.executor_sha or not git_ancestor(record["sha"], args.executor_sha)]
    for record in decisive:
        verify_record(api, repository, record)
    decision = replay_decision(records, args.executor_sha, git_ancestor, lambda record: verified_statuses(api, repository, record), artifact=getattr(args, "artifact", None), target=args.target)
    if decision == "noop":
        print("A successful deployment of this exact revision already exists.")
        return {"proceed": "false"}
    return {"proceed": "true"}


def start(args, api):
    decision = check(args, api)
    if decision["proceed"] == "false":
        return decision
    repository = os.environ["GITHUB_REPOSITORY"]
    environment = f"production-{args.target}"
    require(re.fullmatch(r"[a-z0-9./_-]+@sha256:[0-9a-f]{64}", args.artifact), "deployment artifact must be an immutable image digest")
    targets = []
    if args.plan_json:
        plan = json.loads(Path(args.plan_json).read_text())
        targets = [r["address"] for r in plan.get("resource_changes", []) if r.get("change", {}).get("actions") not in ([], ["no-op"], ["read"])]
    if getattr(args, "plan_scope", None):
        targets = [target for target in args.plan_scope.splitlines() if target]
    require(all(re.fullmatch(r'[A-Za-z0-9_.\[\]"-]+', target) for target in targets), "invalid saved-plan target")
    record = api.post(f"repos/{repository}/deployments", {
        "ref": args.executor_sha, "environment": environment,
        "auto_merge": False, "required_contexts": [], "production_environment": True,
        "payload": {"schema": SCHEMA, "target": args.target, "ci_run_id": args.source_run_id, "ci_run_attempt": args.source_run_attempt,
                    "artifact": args.artifact, "targets": targets, "execution_run_id": int(os.environ["GITHUB_RUN_ID"]), "execution_run_attempt": int(os.environ["GITHUB_RUN_ATTEMPT"])},
    })
    api.post(f"repos/{repository}/deployments/{record['id']}/statuses", {"state": "in_progress", "description": "started", "auto_inactive": False})
    return {"proceed": "true", "deployment_id": str(record["id"])}


def finish(args, api):
    repository = os.environ["GITHUB_REPOSITORY"]
    record = api.get(f"repos/{repository}/deployments/{args.deployment_id}")
    payload = record_payload(record)
    require(payload.get("execution_run_id") == int(os.environ["GITHUB_RUN_ID"]) and payload.get("execution_run_attempt") == int(os.environ["GITHUB_RUN_ATTEMPT"]), "cannot finish another deployment attempt")
    state = {"applied": "success", "verified": "success", "verification_pending": "in_progress", "failed": "failure", "unknown": "error"}[args.phase]
    environment_url = ""
    if args.execution:
        require(TARGET.fullmatch(args.execution), "invalid Cloud Run execution identifier")
        environment_url = f"https://console.cloud.google.com/run/jobs/executions/details/us-central1/{args.execution}?project=shared-datasets-1"
    api.post(f"repos/{repository}/deployments/{args.deployment_id}/statuses", {
        "environment_url": environment_url, "state": state, "description": args.phase, "auto_inactive": False,
        "log_url": f"https://github.com/{repository}/actions/runs/{os.environ['GITHUB_RUN_ID']}",
    })
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with Path(summary).open("a") as stream:
            stream.write(f"Deployment {args.deployment_id}: **{args.phase}**; executor `{record['sha']}`.\n")
    require(args.phase != "unknown", "deployment has no terminal runtime evidence; reconcile before recovery")



def pending(api, repository):
    identifiers = []
    for target in ("wdpa-monthly", "wdpa-processing-validation"):
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
    require(args.target in {"wdpa-monthly", "eamlis-monthly", "sea-ice-daily"}, "invalid runtime verification target")
    executions = json.loads(subprocess.check_output(["gcloud", "run", "jobs", "executions", "list", "--job=" + args.target,
                                                    "--region=us-central1", "--project=shared-datasets-1", "--format=json"]))
    paused = False
    if args.target == "wdpa-monthly":
        state = subprocess.check_output(["gcloud", "scheduler", "jobs", "describe", args.target, "--location=us-central1",
                                         "--project=shared-datasets-1", "--format=value(state)"], text=True).strip()
        require(state in {"ENABLED", "PAUSED"}, "unknown scheduler state prevents runtime verification")
        paused = state == "PAUSED"
    ready = verification_window(executions, paused=paused, canary_date=args.canary_run_date, allow_cancel=args.allow_cancel == "true")
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
    require(payload.get("target") in {"wdpa-monthly", "wdpa-processing-validation"}, "only detached WDPA executions need terminal observation")
    require(git_ancestor(record["sha"], "origin/main"), "record is not from main history")
    verify_ci(api, repository, record["sha"], payload["ci_run_id"], payload["ci_run_attempt"])
    statuses = api.pages(f"repos/{repository}/deployments/{args.deployment_id}/statuses?per_page=100")
    require(statuses and statuses[0].get("description") == "verification_pending", "deployment is not awaiting verification")
    match = re.fullmatch(r"https://console\.cloud\.google\.com/run/jobs/executions/details/us-central1/([a-z][a-z0-9-]{0,62})\?project=shared-datasets-1", statuses[0].get("environment_url", ""))
    require(match is not None, "pending deployment lacks exact canary execution identity")
    raw = json.loads(subprocess.check_output(["gcloud", "run", "jobs", "executions", "describe", match[1], "--region=us-central1", "--project=shared-datasets-1", "--format=json"]))
    phase = terminal_execution(raw, payload["artifact"])
    if phase == "verification_pending":
        print(f"Execution {match[1]} is pending; terminal verification remains incomplete.")
        return
    api.post(f"repos/{repository}/deployments/{args.deployment_id}/statuses", {
        "state": {"verified": "success", "failed": "failure", "unknown": "error"}[phase],
        "description": phase, "auto_inactive": False, "environment_url": statuses[0]["environment_url"],
        "log_url": f"https://github.com/{repository}/actions/runs/{os.environ['GITHUB_RUN_ID']}",
    })
    with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a") as stream:
        stream.write(f"Canary `{match[1]}`: **{phase}**; image `{payload['artifact']}`; executor `{record['sha']}`.\n")
    require(phase == "verified", f"terminal canary outcome: {phase}")


def recovery_record(args, api):
    repository = os.environ["GITHUB_REPOSITORY"]
    require(os.environ.get("GITHUB_REF") == "refs/heads/main" and os.environ.get("GITHUB_WORKFLOW_REF") ==
            f"{repository}/.github/workflows/deployment-recovery.yml@refs/heads/main", "untrusted reconciliation workflow")
    record = api.get(f"repos/{repository}/deployments/{args.deployment_id}")
    payload = verify_record(api, repository, record)
    require(payload.get("target") in {"wdpa-monthly", "eamlis-monthly", "sea-ice-daily"} | set(TERRAFORM_WORKFLOWS), "infrastructure recovery requires a reviewed targeted workflow")
    verify_ci(api, repository, record["sha"], payload["ci_run_id"], payload["ci_run_attempt"])
    require(re.fullmatch(r"[a-z0-9./_-]+@sha256:[0-9a-f]{64}", payload.get("artifact", "")), "missing immutable recovery artifact")
    targets = payload.get("targets")
    require(isinstance(targets, list) and targets and all(re.fullmatch(r'[A-Za-z0-9_.\[\]"-]+', target) for target in targets), "record has no saved-plan scope; use reviewed recovery")
    return record, payload


def recover(args, api):
    record, payload = recovery_record(args, api)
    repository = os.environ["GITHUB_REPOSITORY"]
    if args.command == "recovery-inputs":
        with Path(os.environ["GITHUB_OUTPUT"]).open("a") as stream:
            stream.write(f"executor_sha={record['sha']}\ntarget={payload['target']}\nimage={payload['artifact']}\n")
            stream.write("targets=" + json.dumps(payload["targets"]) + "\n")
            stream.write("kind=" + ("terraform" if payload["target"] in TERRAFORM_WORKFLOWS else "ingestion") + "\n")
        return
    require(subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip() == record["sha"], "recovery checkout differs from original tested executor")
    for other in api.pages(f"repos/{repository}/deployments?environment={record['environment']}&per_page=100"):
        record_payload(other)
        require(other["id"] == record["id"] or (other["sha"] != record["sha"] and git_ancestor(other["sha"], record["sha"])), "a later attempt prevents reconciliation of this revision")
    plan = json.loads(Path(args.plan_json).read_text())
    require(all(r.get("change", {}).get("actions") in ([], ["no-op"], ["read"]) for r in plan.get("resource_changes", [])), "target has drift or an incomplete apply; reconciliation must not apply it automatically")
    if payload["target"] in TERRAFORM_WORKFLOWS:
        api.post(f"repos/{repository}/deployments/{args.deployment_id}/statuses", {
            "state": "success", "description": "applied", "auto_inactive": False,
            "log_url": f"https://github.com/{repository}/actions/runs/{os.environ['GITHUB_RUN_ID']}",
        })
        print("Original tested Terraform target scope has no changes; record reconciled without mutation.")
        return
    raw = json.loads(subprocess.check_output(["gcloud", "run", "jobs", "describe", payload["target"], "--region=us-central1", "--project=shared-datasets-1", "--format=json"]))
    image = raw.get("spec", {}).get("template", {}).get("spec", {}).get("template", {}).get("spec", {}).get("containers", [{}])[0].get("image")
    require(image == payload["artifact"], "live job does not use original tested artifact")
    executions = json.loads(subprocess.check_output(["gcloud", "run", "jobs", "executions", "list", f"--job={payload['target']}", "--region=us-central1", "--project=shared-datasets-1", "--format=json"]))
    matching = [entry for entry in executions if entry.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [{}])[0].get("image") == image]
    require(matching, "no execution of the exact image proves runtime success")
    latest = max(matching, key=lambda entry: entry.get("metadata", {}).get("creationTimestamp", ""))
    require(terminal_execution(latest, image) == "verified", "latest exact-image execution is incomplete or failed; fix or use reviewed recovery")
    api.post(f"repos/{repository}/deployments/{args.deployment_id}/statuses", {
        "state": "success", "description": "verified", "auto_inactive": False,
        "log_url": f"https://github.com/{repository}/actions/runs/{os.environ['GITHUB_RUN_ID']}",
    })
    print("Original image, no-change target plan and terminal success reconciled; no production mutations performed.")

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
        if command in {"check", "start"}:
            begin.add_argument("--target", required=True)
            begin.add_argument("--artifact", required=command == "start")
        if command == "start":
            begin.add_argument("--plan-json")
            begin.add_argument("--plan-scope")
    end = commands.add_parser("finish")
    end.add_argument("--deployment-id", required=True, type=int)
    end.add_argument("--execution", default="")
    end.add_argument("--phase", required=True, choices=["applied", "verification_pending", "verified", "failed", "unknown"])
    commands.add_parser("pending")
    window = commands.add_parser("window")
    window.add_argument("--target", required=True)
    window.add_argument("--canary-run-date", default="")
    window.add_argument("--allow-cancel", default="false")
    observer = commands.add_parser("observe")
    observer.add_argument("--deployment-id", required=True, type=int)
    for command in ("recovery-inputs", "reconcile"):
        recovery = commands.add_parser(command)
        recovery.add_argument("--deployment-id", required=True, type=int)
        if command == "reconcile":
            recovery.add_argument("--plan-json", required=True)
    args = parser.parse_args()
    try:
        if args.command == "window":
            runtime_window(args)
        elif args.command in {"recovery-inputs", "reconcile"}:
            recover(args, GitHub())
        elif args.command == "pending":
            with Path(os.environ["GITHUB_OUTPUT"]).open("a") as stream:
                stream.write("records=" + json.dumps(pending(GitHub(), os.environ["GITHUB_REPOSITORY"])) + "\n")
        elif args.command == "observe":
            observe(args, GitHub())
        elif args.command == "verify":
            verify_context(args, GitHub())
        elif args.command in {"check", "start"}:
            outputs = start(args, GitHub()) if args.command == "start" else check(args, GitHub())
            with Path(os.environ["GITHUB_OUTPUT"]).open("a") as stream:
                for key, value in outputs.items():
                    stream.write(f"{key}={value}\n")
        else:
            finish(args, GitHub())
    except DeploymentError as exc:
        parser.exit(1, f"DEPLOYMENT_NOT_READY: {exc}\n")


if __name__ == "__main__":
    main()
