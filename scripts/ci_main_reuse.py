"""Reuse successful same-tree PR validation only at the main-push boundary.

Lookup failures mean full validation. The successful selector's immutable plan
retains the checked API/artifact snapshot; the gate consumes that proof without
another opportunistic network lookup. Re-run all jobs for fresh validation.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.ci_contract import REUSABLE_SUITES, expected_tools, write_json
from scripts.deployment_revision import DeploymentError, require
from scripts.reviewed_dataset_plan import PlanValidationError
from scripts.sdk_release_authorization import GitHub, artifact_files, execution_time, gate_execution

WORKFLOW = ".github/workflows/ci.yml"
LOOKUP_TIMEOUT_SECONDS = 60


def lookup_expired(_signal, _frame):
    raise TimeoutError("PR reuse lookup exceeded its overall deadline")


def context(environment=os.environ):
    path = environment.get("GITHUB_EVENT_PATH")
    event = json.loads(Path(path).read_text()) if path else {}
    return {
        "event": environment.get("GITHUB_EVENT_NAME"),
        "repository": environment.get("GITHUB_REPOSITORY"),
        "ref": environment.get("GITHUB_REF"),
        "sha": environment.get("GITHUB_SHA"),
        "before": event.get("before"),
        "pr_number": event.get("number"),
    }


def result_summary(chosen):
    return {key: {field: result[field] for field in ("status", "tools", "source")}
            for key, result in chosen.items()}


def fields(value, names):
    return {key: value[key] for key in names.split()}


def check_snapshot(plan, snapshot, current):
    """Recheck identities using freshness established by the selector's admission."""
    require(current["event"] == "push" and current["ref"] == "refs/heads/main", "reuse requires a main push")
    require(plan["head"] == plan["tested_sha"] == current["sha"]
            and plan["base"] == current["before"], "push identity changed")
    pr, run, gate, artifact, evidence = (snapshot[key] for key in ("pr", "run", "gate", "artifact", "evidence"))
    repository = current["repository"]
    require(pr["merged"] is True and pr["state"] == "closed" and pr["merged_at"] and not pr["draft"]
            and pr["merge_commit_sha"] == current["sha"] and pr["base"]["ref"] == "main", "PR is not the exact merged push")
    require(all(pr[side]["repo"]["full_name"] == repository for side in ("head", "base")), "fork PR cannot supply reuse")
    require(pr["head"]["repo"]["id"] == pr["base"]["repo"]["id"], "PR repository identity mismatch")
    require(type(run["id"]) is int and run["id"] > 0 and type(run["run_attempt"]) is int and run["run_attempt"] > 0,
            "invalid PR run identity")
    require(run["path"] == WORKFLOW and run["event"] == "pull_request" and run["head_sha"] == pr["head"]["sha"]
            and run["status"] == "completed" and run["conclusion"] == "success", "PR CI did not succeed")
    require(all(run[key]["full_name"] == repository and run[key]["id"] == pr["base"]["repo"]["id"]
                for key in ("repository", "head_repository")), "PR run repository mismatch")
    require(gate["name"] == "ci-ready" and gate["status"] == "completed" and gate["conclusion"] == "success",
            "PR ci-ready did not succeed")
    start, end = gate_execution(gate)
    admitted = execution_time(snapshot["admitted_at"])
    require(artifact["name"] == f"ci-ready-evidence-attempt{run['run_attempt']}"
            and artifact["workflow_run"]["id"] == run["id"] and not artifact["expired"]
            and execution_time(artifact["created_at"]) <= admitted < execution_time(artifact["expires_at"])
            and start <= execution_time(artifact["created_at"]) <= end, "PR evidence is expired or belongs to another execution")
    require(evidence["source"] == {"run_id": str(run["id"]), "run_attempt": run["run_attempt"]}
            and evidence["schema_version"] == 1 and evidence["status"] == "success", "PR evidence source mismatch")
    require(evidence["validation_context"]["event"] == "pull_request"
            and evidence["validation_context"]["repository"] == repository
            and evidence["validation_context"]["pr_number"] == pr["number"], "PR evidence event mismatch")
    require(evidence["head"] == pr["head"]["sha"] and re.fullmatch(r"[0-9a-f]{40}", evidence["tested_sha"])
            and all(evidence[key] == plan[key] for key in ("tree", "base", "contract_digest", "suites")),
            "PR tree, base, selection or contract differs from the main push")
    tools = {suite: expected_tools(suite) for suite in plan["suites"]}
    require(evidence["toolchains"] == tools, "PR toolchain identities changed")
    reused = sorted(set(plan["suites"]) & REUSABLE_SUITES)
    require(snapshot["reused_suites"] == reused, "reuse includes an unauthorized suite")
    for suite in reused:
        result = evidence["validation_results"][suite]
        require(result["status"] == "success" and result["tools"] == tools[suite]
                and result["source"]["run_id"] == str(run["id"])
                and type(result["source"]["run_attempt"]) is int
                and 1 <= result["source"]["run_attempt"] <= run["run_attempt"], "PR suite did not pass with the current tools")
    return frozenset(reused)


def discover(api, plan, current, *, now):
    repository, sha = current["repository"], current["sha"]
    pulls = api.pages(f"repos/{repository}/commits/{sha}/pulls?per_page=100")
    require(len(pulls) == 1, "push has missing or ambiguous associated PRs")
    pr = api.get(f"repos/{repository}/pulls/{pulls[0]['number']}")
    workflow = api.get(f"repos/{repository}/actions/workflows/ci.yml")
    runs = api.pages(f"repos/{repository}/actions/workflows/ci.yml/runs?event=pull_request&head_sha={pr['head']['sha']}&per_page=100",
                     field="workflow_runs")
    require(bool(runs), "no PR CI run")
    # A newer failed attempt/run must not fall back to an older green one.
    latest = max(runs, key=lambda run: run["id"])
    run = api.get(f"repos/{repository}/actions/runs/{latest['id']}/attempts/{latest['run_attempt']}")
    require(run["id"] == latest["id"] and run["run_attempt"] == latest["run_attempt"]
            and run["workflow_id"] == workflow["id"] and workflow["path"] == WORKFLOW, "PR workflow identity mismatch")
    jobs = api.pages(f"repos/{repository}/actions/runs/{run['id']}/attempts/{run['run_attempt']}/jobs?per_page=100", field="jobs")
    gates = [job for job in jobs if job.get("name") == "ci-ready"]
    require(len(gates) == 1, "PR ci-ready is missing or ambiguous")
    artifacts = api.pages(f"repos/{repository}/actions/runs/{run['id']}/artifacts?per_page=100", field="artifacts")
    name = f"ci-ready-evidence-attempt{run['run_attempt']}"
    matches = [artifact for artifact in artifacts if artifact.get("name") == name]
    require(len(matches) == 1 and type(matches[0].get("size_in_bytes")) is int
            and 0 < matches[0]["size_in_bytes"] <= 2 * 1024 * 1024, "PR evidence is missing, ambiguous or oversized")
    evidence = json.loads(artifact_files(api, repository, run["id"], artifacts, name)["evidence.json"])
    # Keep only verification inputs; PR bodies, logs and image payloads never
    # become part of the small retained proof.
    pr = fields(pr, "number merged state merged_at draft merge_commit_sha head base")
    pr = {**pr, **{side: {**fields(pr[side], "sha ref"), "repo": fields(pr[side]["repo"], "id full_name")}
                   for side in ("head", "base")}}
    run = fields(run, "id run_attempt workflow_id path event head_sha status conclusion repository head_repository")
    run.update({key: fields(run[key], "id full_name") for key in ("repository", "head_repository")})
    snapshot = {"schema_version": 1, "main_source": plan["source"],
                "admitted_at": (now or datetime.now(timezone.utc)).isoformat().replace("+00:00", "Z"), "pr": pr, "run": run,
                "gate": fields(gates[0], "name status conclusion started_at completed_at"),
                "artifact": fields(matches[0], "id name workflow_run expired expires_at created_at digest size_in_bytes"),
                "evidence": fields(evidence, "schema_version status source validation_context head tested_sha tree base contract_digest suites toolchains validation_results"),
                "reused_suites": sorted(set(plan["suites"]) & REUSABLE_SUITES)}
    check_snapshot(plan, snapshot, current)
    return snapshot


def select_reuse(api, plan, current, *, attempt, now=None):
    """Every external lookup/shape/archive failure takes the full-validation path."""
    if current["event"] != "push" or current["ref"] != "refs/heads/main" or attempt != 1:
        return None
    # The Ubuntu selector runs on its main thread. One alarm spans every gh
    # request, retry sleep and archive read; subprocess.run kills/waits on its
    # child when interrupted, including the otherwise unbounded ZIP download.
    previous_handler = signal.signal(signal.SIGALRM, lookup_expired)
    try:
        signal.setitimer(signal.ITIMER_REAL, LOOKUP_TIMEOUT_SECONDS)
        return discover(api, plan, current, now=now)
    except (DeploymentError, PlanValidationError, ValueError, KeyError, TypeError, OSError,
            subprocess.SubprocessError, zipfile.BadZipFile, RuntimeError) as error:
        print(f"PR validation reuse unavailable; running the full selection: {type(error).__name__}: {error}", file=sys.stderr)
        return None
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)


def verified_reuse(plan, current, *, run_id, attempt):
    snapshot = plan.get("main_reuse")
    if snapshot is None:
        return frozenset()
    # A selective gate rerun cannot reinterpret a previous attempt's job skips.
    # All-job reruns make a fresh plan and deliberately run every selected suite.
    require(attempt == 1 and snapshot["main_source"] == plan["source"] == {"run_id": run_id, "run_attempt": attempt},
            "reuse plan is from another attempt; re-run all jobs for full validation")
    require(snapshot["schema_version"] == 1, "invalid main reuse proof")
    return check_snapshot(plan, snapshot, current)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--github-output", required=True, type=Path)
    args = parser.parse_args()
    plan = json.loads(args.plan.read_text())
    plan.pop("main_reuse", None)
    snapshot = select_reuse(GitHub(), plan, context(), attempt=int(os.environ["GITHUB_RUN_ATTEMPT"]))
    if snapshot:
        plan["main_reuse"] = snapshot
    write_json(args.plan, plan)
    with args.github_output.open("a") as output:
        output.write(f"reuse_validation={'true' if snapshot else 'false'}\n")


if __name__ == "__main__":
    main()
