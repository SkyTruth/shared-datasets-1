"""Read-only Actions provenance for selective validation reruns."""

from __future__ import annotations

import json
import os
import urllib.request


def job_name(suite: str) -> str:
    if suite in {"sdk-node22", "sdk-node24"}:
        return f"sdk-validation (Node {suite.removeprefix('sdk-node')})"
    return suite


def api(path: str) -> dict:
    base = os.environ.get("GITHUB_API_URL", "https://api.github.com")
    request = urllib.request.Request(base + path, headers={
        "Authorization": "Bearer " + os.environ["GITHUB_TOKEN"],
        "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
    })
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def prove_attempts(repository: str, run_id: str, attempts: set[int], plan: dict) -> dict[int, set[str]]:
    proofs = {}
    for attempt in sorted(attempts):
        prefix = f"/repos/{repository}/actions/runs/{run_id}/attempts/{attempt}"
        run = api(prefix)
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
            listing = api(prefix + f"/jobs?per_page=100&page={page}")
            if expected_count is None:
                expected_count = listing["total_count"]
            elif listing["total_count"] != expected_count:
                raise ValueError("Actions job enumeration changed during validation")
            batch = listing["jobs"]
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
