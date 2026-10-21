"""Read-only Actions provenance for selective validation reruns."""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.request

CONVERGENCE_SECONDS = 120
CONVERGENCE_OFFSETS = (0, 5, 15, 35, 65, 110)
NONTERMINAL = {"queued", "in_progress", "waiting", "pending", "requested"}


def job_key(suite: str) -> str:
    return "sdk-validation" if suite in {"sdk-node22", "sdk-node24"} else suite


def job_name(suite: str) -> str:
    if suite in {"sdk-node22", "sdk-node24"}:
        return f"{job_key(suite)} (Node {suite.removeprefix('sdk-node')})"
    return job_key(suite)


def api(path: str, *, timeout: float = 30) -> dict:
    base = os.environ.get("GITHUB_API_URL", "https://api.github.com")
    request = urllib.request.Request(base + path, headers={
        "Authorization": "Bearer " + os.environ["GITHUB_TOKEN"],
        "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
        "Cache-Control": "no-cache",
    })
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def _expected_jobs(plan, results, needs, run_id, current_attempt):
    """Read eligibility is narrower than the final, unchanged evidence checks."""
    if type(current_attempt) is not int or current_attempt < 1 or not isinstance(needs, dict):
        raise ValueError("invalid current Actions observation context")
    chosen, seen = {}, set()
    for result in results:
        if not isinstance(result, dict) or not isinstance(result.get("source"), dict):
            raise ValueError("malformed source result identity")
        source = result.get("source", {})
        attempt, suite = source.get("run_attempt"), result.get("suite")
        if (source.get("run_id") != run_id or type(attempt) is not int or not 1 <= attempt <= current_attempt
                or suite not in plan["suites"] or (suite, attempt) in seen
                or any(result.get(key) != plan[key] for key in ("base", "head", "tested_sha", "tree", "contract_digest"))):
            raise ValueError("invalid source result identity before Actions status observation")
        seen.add((suite, attempt))
        if suite not in chosen or attempt > chosen[suite]["source"]["run_attempt"]:
            chosen[suite] = result
    if set(chosen) != set(plan["suites"]):
        raise ValueError("missing selected suite results before Actions status observation")
    required, eligible = {}, set()
    for suite, result in chosen.items():
        if result.get("status") != "success":
            raise ValueError(f"{suite} did not execute successfully")
        attempt = result["source"]["run_attempt"]
        required.setdefault(attempt, set()).add(job_name(suite))
        if attempt == current_attempt and needs.get(job_key(suite), {}).get("result") == "success":
            eligible.add(job_name(suite))
    source = plan["source"]
    required.setdefault(source["run_attempt"], set()).add("geospatial-changes")
    if (source == {"run_id": run_id, "run_attempt": current_attempt}
            and needs.get("geospatial-changes", {}).get("result") == "success"):
        eligible.add("geospatial-changes")
    return required, eligible


def _snapshot(repository, run_id, attempt, plan, fetch):
    prefix = f"/repos/{repository}/actions/runs/{run_id}/attempts/{attempt}"
    run = fetch(prefix)
    expected_head = plan["head"] if run.get("event") == "pull_request" else plan["tested_sha"]
    if (
        str(run.get("id")) != run_id or run.get("run_attempt") != attempt
        or run.get("path") != ".github/workflows/ci.yml"
        or run.get("repository", {}).get("full_name") != repository
        or run.get("event") not in {"pull_request", "push", "workflow_dispatch"}
        or run.get("head_sha") != expected_head
    ):
        raise ValueError(f"CI attempt {attempt} does not prove the expected source revision")
    jobs = []
    expected_count = None
    for page in range(1, 101):
        listing = fetch(prefix + f"/jobs?per_page=100&page={page}")
        if (not isinstance(listing, dict) or type(listing.get("total_count")) is not int
                or listing["total_count"] < 0 or not isinstance(listing.get("jobs"), list)):
            raise ValueError("malformed Actions job enumeration")
        if expected_count is None:
            expected_count = listing["total_count"]
        elif listing["total_count"] != expected_count:
            raise ValueError("Actions job enumeration changed during validation")
        batch = listing["jobs"]
        if any(not isinstance(job, dict) or type(job.get("id")) is not int
               or not isinstance(job.get("name"), str) or not job["name"]
               or not isinstance(job.get("status"), str) or "conclusion" not in job for job in batch):
            raise ValueError("malformed source job")
        jobs.extend(batch)
        if len(jobs) == expected_count:
            break
        if not batch or len(jobs) > expected_count:
            raise ValueError("Actions job enumeration is incomplete")
    else:
        raise ValueError("Actions job enumeration exceeds the supported complete range")
    if len({job["id"] for job in jobs}) != len(jobs):
        raise ValueError("duplicate source jobs")
    names = [job["name"] for job in jobs]
    if len(names) != len(set(names)):
        raise ValueError("ambiguous source job names")
    return run, jobs


def prove_attempts(repository: str, run_id: str, attempts: set[int], plan: dict, *,
                   current_attempt=None, results=None, needs=None, clock=time.monotonic, sleep=time.sleep) -> dict[int, set[str]]:
    required, eligible = ({}, set()) if results is None or needs is None else _expected_jobs(
        plan, results, needs, run_id, current_attempt)
    started = clock()
    deadline = started + CONVERGENCE_SECONDS if eligible else None

    def fetch(path):
        if deadline is None:
            return api(path)
        remaining = deadline - clock()
        if remaining <= 0:
            raise ValueError("Actions producing-job visibility did not converge within 120 seconds")
        value = api(path, timeout=min(30, remaining))
        if clock() > deadline:
            raise ValueError("Actions producing-job visibility exceeded 120 seconds")
        return value

    proofs = {}
    for attempt in sorted(attempts):
        while True:
            run, jobs = _snapshot(repository, run_id, attempt, plan, fetch)
            by_name = {job["name"]: job for job in jobs}
            pending = []
            for name in sorted(required.get(attempt, ())):
                job = by_name.get(name)
                if job is not None:
                    if (str(job.get("run_id")) != run_id or job.get("run_attempt") != attempt
                            or job.get("head_sha") != run["head_sha"]):
                        raise ValueError(f"{name} has incompatible source job identity")
                    if job["status"] == "completed":
                        if job["conclusion"] != "success":
                            raise ValueError(f"{name} has terminal source outcome {job['conclusion']}")
                        continue
                    if job["status"] not in NONTERMINAL or job["conclusion"] is not None:
                        raise ValueError(f"{name} has malformed or unknown source outcome")
                if attempt != current_attempt or name not in eligible:
                    raise ValueError(f"{name} has no successful producing job in its source attempt")
                pending.append(f"{name}: id={job['id']} status={job['status']}" if job else f"{name}: absent")
            if not pending:
                break
            diagnostic = f"CI attempt {attempt} awaiting terminal source visibility: " + "; ".join(pending)
            print(diagnostic, file=sys.stderr)
            remaining = deadline - clock()
            if remaining <= 0:
                raise ValueError(diagnostic + "; exhausted 120-second read-convergence budget")
            next_read = next((started + offset for offset in CONVERGENCE_OFFSETS if started + offset > clock()), deadline)
            sleep(max(0, next_read - clock()))
            if clock() >= deadline:
                raise ValueError(diagnostic + "; exhausted 120-second read-convergence budget")
        proofs[attempt] = {job["name"] for job in jobs if job["status"] == "completed" and job["conclusion"] == "success"}
    return proofs


def select_plan(candidates: list[dict], *, run_id: str, attempt: int) -> dict:
    if not candidates:
        raise ValueError("missing validation plan")
    identities = set()
    for candidate in candidates:
        source = candidate.get("source", {})
        source_attempt = source.get("run_attempt")
        if source.get("run_id") != run_id or type(source_attempt) is not int or not 1 <= source_attempt <= attempt:
            raise ValueError("plan has an invalid source attempt")
        if source_attempt in identities:
            raise ValueError("duplicate plan attempt")
        identities.add(source_attempt)
    selected = max(candidates, key=lambda candidate: candidate["source"]["run_attempt"])
    comparable = {key: value for key, value in selected.items() if key != "source"}
    if any({key: value for key, value in candidate.items() if key != "source"} != comparable for candidate in candidates):
        raise ValueError("source attempt plans disagree about the validation contract")
    return selected
