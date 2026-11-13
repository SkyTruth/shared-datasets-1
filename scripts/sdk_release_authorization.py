#!/usr/bin/env python3
"""Download and verify the exact Node 24 package tested by passing main CI."""
from __future__ import annotations

import argparse
import base64
from datetime import datetime
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.ci_contract import contract_digest, expected_tools, validate_obligations
from scripts.ci_source_proof import job_name
from scripts.dataset_mutation_authorization import GitHub as ArtifactGitHub
from scripts.deployment_revision import DeploymentError, require, verify_ci, verify_context

class GitHub(ArtifactGitHub):
    """Adapt CI's positional page API to the archive transport."""
    def pages(self, path, field=None):
        return super().pages(path, field=field)

MAX_ARCHIVE = 100 * 1024 * 1024
IDENTITY = ("schema_version", "base", "head", "tested_sha", "tree", "contract_digest")


def admit_source(api, repository, run_id, attempt, sha, event):
    """npm must see this top-level workflow for automatic and recovery releases."""
    from scripts.workflow_failure_alert import context
    current = context(api, workflow=".github/workflows/publish-typescript-sdk.yml")
    require(os.environ.get("GITHUB_EVENT_NAME") == current["event"] == "workflow_dispatch", "SDK publishing requires dispatch")
    source = verify_ci(api, repository, sha, run_id, attempt)
    require(all(event.get("repository", {}).get(key) == source["repository"][key] for key in ("id", "full_name")),
            "SDK event repository mismatch")
    jobs = api.pages(f"repos/{repository}/actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100", field="jobs")
    sdk = [job for job in jobs if job.get("name") == job_name("sdk-node24")]
    require(len(sdk) <= 1, "ambiguous SDK validation source job")
    return bool(sdk and sdk[0].get("status") == "completed" and sdk[0].get("conclusion") == "success")


def artifact_files(api, repository, run_id, artifacts, name):
    found = [artifact for artifact in artifacts if artifact.get("name") == name]
    require(len(found) == 1, "missing or ambiguous tested artifact: " + name)
    artifact = found[0]
    require(not artifact.get("expired") and artifact.get("workflow_run", {}).get("id") == run_id,
            "artifact is expired or belongs to another run")
    raw = api.archive(repository, artifact["id"])
    require(len(raw) <= MAX_ARCHIVE and artifact.get("digest") == "sha256:" + hashlib.sha256(raw).hexdigest(), "artifact archive digest mismatch")
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        names = archive.namelist()
        require(len(names) == len(set(names)), "duplicate artifact member")
        require(all(not PurePosixPath(name).is_absolute() and ".." not in PurePosixPath(name).parts and "\\" not in name for name in names), "unsafe artifact member")
        require(sum(entry.file_size for entry in archive.infolist()) <= MAX_ARCHIVE, "artifact expansion exceeds limit")
        # Only JSON evidence and the selected package bytes are consumed. Logs,
        # fixture workspaces and executable artifact members are never extracted.
        return {name: archive.read(name) for name in names if name in {"plan.json", "evidence.json", "result.json", "images/manifest.json", "package/candidate.json"} or re.fullmatch(r"package/[A-Za-z0-9_.-]+\.tgz", name)}


def producing_job(api, repository, run_id, attempt, sha, name, workflow_id, repository_id):
    run = api.get(f"repos/{repository}/actions/runs/{run_id}/attempts/{attempt}")
    require(run.get("id") == run_id and run.get("run_attempt") == attempt and run.get("head_sha") == sha
            and run.get("workflow_id") == workflow_id and run.get("path") == ".github/workflows/ci.yml"
            and run.get("event") == "push" and run.get("head_branch") == "main"
            and all(run.get(key, {}).get("id") == repository_id and run.get(key, {}).get("full_name") == repository for key in ("repository", "head_repository")), "artifact producer is not the exact main CI revision")
    jobs = api.pages(f"repos/{repository}/actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100", field="jobs")
    found = [job for job in jobs if job.get("name") == name]
    require(len(found) == 1 and found[0].get("status") == "completed" and found[0].get("conclusion") == "success", "artifact producing job did not complete successfully")
    return found[0]


def reference_attempt(reference, prefix, current):
    match = re.fullmatch(re.escape(prefix) + r"-attempt([1-9][0-9]*)", str(reference))
    require(match is not None and 1 <= int(match[1]) <= current, "invalid validation artifact attempt")
    return int(match[1])


def verify_candidate(candidate, package, manifest, sha):
    require(candidate.get("name") == manifest["name"] == "@skytruth/shared-datasets" and candidate.get("version") == manifest["version"], "candidate package identity mismatch")
    require(candidate.get("tested_sha") == sha and re.fullmatch(r"[A-Za-z0-9_.-]+\.tgz", str(candidate.get("tarball"))), "candidate is not bound to its tested revision")
    require(candidate.get("sha256") == hashlib.sha256(package).hexdigest(), "tested package SHA-256 mismatch")
    require(candidate.get("integrity") == "sha512-" + base64.b64encode(hashlib.sha512(package).digest()).decode(), "tested package integrity mismatch")


def execution_time(value):
    require(isinstance(value, str) and re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z", value), "missing or invalid gate execution timestamp")
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def gate_execution(job):
    start, end = (execution_time(job.get(key)) for key in ("started_at", "completed_at"))
    require(start <= end, "invalid gate execution window")
    return start, end


def verified_plan(api, repository, run_id, attempt, sha, root):
    run = verify_ci(api, repository, sha, run_id, attempt)
    gate = producing_job(api, repository, run_id, attempt, sha, "ci-ready", run["workflow_id"], run["repository"]["id"])
    start, end = gate_execution(gate)
    artifacts = api.pages(f"repos/{repository}/actions/runs/{run_id}/artifacts?per_page=100", field="artifacts")
    # Selective downstream reruns retain the gate's original execution window,
    # while Actions rewrites its job ID and run_attempt. Only that execution's
    # upload is eligible; an older successful gate is never a fallback.
    candidates = [artifact for artifact in artifacts
                  if re.fullmatch(r"ci-ready-evidence-attempt[1-9][0-9]*", str(artifact.get("name")))
                  and not artifact.get("expired") and start <= execution_time(artifact.get("created_at")) <= end]
    require(len(candidates) == 1, "missing or ambiguous evidence for the successful ci-ready execution")
    reference = candidates[0]["name"]
    gate_attempt = reference_attempt(reference, "ci-ready-evidence", attempt)
    producer = gate if gate_attempt == attempt else producing_job(api, repository, run_id, gate_attempt, sha, "ci-ready", run["workflow_id"], run["repository"]["id"])
    require(gate_execution(producer) == (start, end), "ci-ready evidence belongs to another gate execution")
    evidence = json.loads(artifact_files(api, repository, run_id, artifacts, reference)["evidence.json"])
    require(evidence.get("source") == {"run_id": str(run_id), "run_attempt": gate_attempt} and evidence.get("status") == "success" and evidence.get("tested_sha") == sha and evidence.get("head") == sha, "ci-ready evidence source mismatch")
    plan_attempt = reference_attempt(evidence.get("plan_artifact"), "ci-validation-plan", gate_attempt)
    plan = json.loads(artifact_files(api, repository, run_id, artifacts, evidence["plan_artifact"])["plan.json"])
    require(plan.get("source") == {"run_id": str(run_id), "run_attempt": plan_attempt} and all(plan.get(key) == evidence.get(key) for key in (*IDENTITY, "suites", "changed_paths", "selection_reason", "deployments", "held_deployment_targets", "readiness_targets", "bootstrap_targets")), "validation plan differs from ci-ready evidence")
    validate_obligations(plan)
    require(plan.get("schema_version") == 1 and plan.get("tree") == subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], cwd=root, text=True).strip() and plan.get("contract_digest") == contract_digest(root), "validation tree or contract changed")
    producing_job(api, repository, run_id, plan_attempt, sha, "geospatial-changes", run["workflow_id"], run["repository"]["id"])
    print(f"Verified CI trigger attempt {attempt} using ci-ready evidence from attempt {gate_attempt}.")
    return run, artifacts, evidence, plan


def download(api, repository, run_id, attempt, sha, directory, root):
    run, artifacts, evidence, plan = verified_plan(api, repository, run_id, attempt, sha, root)
    require(("sdk-node22" in plan["suites"]) == ("sdk-node24" in plan["suites"]), "incomplete SDK runtime coverage")
    needed = subprocess.check_output(["node", "api/typescript/scripts/release-policy.mjs", "changes", plan["base"], sha], cwd=root, text=True).strip()
    require(needed in {"release_needed=true", "release_needed=false"}, "invalid SDK version policy result")
    outputs = {"release_needed": needed.partition("=")[2], "base_sha": plan["base"]}
    if outputs['release_needed'] == 'false':
        return outputs
    require('sdk-node24' in plan['suites'], 'release requires missing SDK validation')
    reference = evidence.get("suite_artifacts", {}).get("sdk-node24")
    suite_attempt = reference_attempt(reference, "ci-result-sdk-node24", evidence["source"]["run_attempt"])
    files = artifact_files(api, repository, run_id, artifacts, reference)
    result = json.loads(files["result.json"])
    require(result.get("source") == {"run_id": str(run_id), "run_attempt": suite_attempt} and result.get("suite") == "sdk-node24" and result.get("status") == "success", "SDK result source or status mismatch")
    require(all(result.get(key) == plan.get(key) for key in IDENTITY), "SDK result differs from tested plan")
    require(result.get("tools") == expected_tools("sdk-node24") and result.get("commands") and all(command.get("exit_code") == 0 for command in result["commands"]), "SDK validation was skipped, failed or used another toolchain")
    producing_job(api, repository, run_id, suite_attempt, sha, job_name("sdk-node24"), run["workflow_id"], run["repository"]["id"])
    candidate = json.loads(files["package/candidate.json"])
    require(result.get("package") == candidate, "candidate differs from the recorded tested package")
    package = files.get("package/" + str(candidate.get("tarball")))
    require(package is not None, "tested tarball is missing")
    verify_candidate(candidate, package, json.loads((root / "api/typescript/package.json").read_text()), sha)
    directory.mkdir(parents=True, exist_ok=True)
    tarball = directory / candidate["tarball"]
    tarball.write_bytes(package)
    candidate["tarball"] = str(tarball.resolve())
    candidate_path = directory / "candidate.json"
    candidate_path.write_text(json.dumps(candidate, sort_keys=True) + "\n")
    return {**outputs, "candidate": str(candidate_path.resolve()), "tarball": str(tarball.resolve()), "artifact": "sdk@sha256:" + candidate["sha256"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executor-sha", required=True)
    parser.add_argument("--source-run-id", required=True, type=int)
    parser.add_argument("--source-run-attempt", required=True, type=int)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--admit-source", action="store_true")
    args = parser.parse_args()
    if not args.admit_source and args.output is None:
        parser.error("--output is required for tested package download")
    args.workflow = "publish-typescript-sdk.yml"
    try:
        api = GitHub()
        event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
        eligible = admit_source(api, os.environ["GITHUB_REPOSITORY"], args.source_run_id, args.source_run_attempt, args.executor_sha, event)
        if not eligible:
            print("CI did not select successful SDK validation; no SDK release is authorized.")
            outputs = {"source_eligible": "false", "release_needed": "false"}
        elif args.admit_source:
            outputs = {"source_eligible": "true"}
        else:
            from scripts.deployment_revision import GitHub as DeploymentGitHub
            verify_context(args, DeploymentGitHub())
            outputs = download(api, os.environ["GITHUB_REPOSITORY"], args.source_run_id, args.source_run_attempt, args.executor_sha, args.output, Path.cwd())
        with Path(os.environ["GITHUB_OUTPUT"]).open("a") as stream:
            for key, value in outputs.items():
                stream.write(f"{key}={value}\n")
    except (DeploymentError, ValueError, KeyError, zipfile.BadZipFile) as error:
        parser.exit(1, f"SDK_RELEASE_NOT_READY: {error}\n")


if __name__ == "__main__":
    main()
