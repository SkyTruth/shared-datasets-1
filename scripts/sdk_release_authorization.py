#!/usr/bin/env python3
"""Download and verify the exact Node 24 package tested by passing main CI."""
from __future__ import annotations

import argparse
import base64
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
from scripts.ci_contract import contract_digest, expected_tools
from scripts.dataset_mutation_authorization import GitHub as ArtifactGitHub
from scripts.deployment_revision import DeploymentError, require, verify_ci, verify_context

class GitHub(ArtifactGitHub):
    def pages(self, path, field=None):
        return super().pages(path, field=field)


MAX_ARCHIVE = 100 * 1024 * 1024
IDENTITY = ("schema_version", "base", "head", "tested_sha", "tree", "contract_digest")


def admit_source(api, repository, run_id, attempt, sha, event):
    """Filter verified CI completions before strict deployment authorization."""
    require(type(run_id) is int and run_id > 0 and type(attempt) is int and attempt > 0
            and re.fullmatch(r"[0-9a-f]{40}", sha), "invalid SDK source identity")
    repo = api.get(f"repos/{repository}")
    workflow = api.get(f"repos/{repository}/actions/workflows/ci.yml")
    require(type(repo.get("id")) is int and repo["id"] > 0 and repo.get("full_name") == repository,
            "SDK repository identity mismatch")
    require(type(workflow.get("id")) is int and workflow["id"] > 0 and workflow.get("path") == ".github/workflows/ci.yml",
            "SDK source workflow identity mismatch")
    source = event.get("workflow_run", {})
    current = api.get(f"repos/{repository}/actions/runs/{run_id}")
    original = api.get(f"repos/{repository}/actions/runs/{run_id}/attempts/{attempt}")
    for run in (source, current, original):
        require(run.get("id") == run_id and run.get("head_sha") == sha
                and run.get("name") == "CI" and run.get("path") == workflow["path"]
                and run.get("workflow_id") == workflow["id"] and run.get("event") == "push"
                and run.get("head_branch") == "main"
                and all(run.get(key, {}).get("id") == repo["id"] and run.get(key, {}).get("full_name") == repository
                        for key in ("repository", "head_repository")), "SDK event does not identify trusted main CI")
    require(event.get("repository", {}).get("id") == repo["id"] and event["repository"].get("full_name") == repository,
            "SDK event repository mismatch")
    require(source.get("run_attempt") == attempt and original.get("run_attempt") == attempt
            and source.get("status") == original.get("status") == "completed"
            and source.get("conclusion") == original.get("conclusion"), "SDK source completion differs from its attempt")
    require(original.get("conclusion") in {"success", "failure", "cancelled", "timed_out", "action_required", "stale", "startup_failure", "skipped", "neutral"},
            "SDK source has no terminal conclusion")
    require(type(current.get("run_attempt")) is int and current["run_attempt"] >= attempt,
            "SDK source has an invalid current attempt")
    jobs = api.pages(f"repos/{repository}/actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100", field="jobs")
    ready = [job for job in jobs if job.get("name") == "ci-ready"]
    require(len(ready) <= 1, "ambiguous ci-ready source job")
    if current["run_attempt"] > attempt or original["conclusion"] == "cancelled":
        return False
    require(current.get("status") == "completed" and current.get("conclusion") == original["conclusion"],
            "SDK source no longer identifies the completed attempt")
    return bool(ready and ready[0].get("status") == "completed" and ready[0].get("conclusion") == "success")


def admit_manual_source(api, repository, run_id, attempt, sha, event):
    require(os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
            and os.environ.get("GITHUB_REF") == "refs/heads/main"
            and os.environ.get("GITHUB_WORKFLOW_REF") == f"{repository}/.github/workflows/publish-typescript-sdk.yml@refs/heads/main",
            "manual SDK recovery must use the trusted main workflow")
    current_id = os.environ.get("GITHUB_RUN_ID", "")
    current_attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "")
    require(re.fullmatch(r"[1-9][0-9]*", current_id) and re.fullmatch(r"[1-9][0-9]*", current_attempt),
            "manual SDK recovery lacks current run identity")
    repo = api.get(f"repos/{repository}")
    workflow = api.get(f"repos/{repository}/actions/workflows/publish-typescript-sdk.yml")
    current = api.get(f"repos/{repository}/actions/runs/{current_id}/attempts/{current_attempt}")
    require(type(repo.get("id")) is int and repo["id"] > 0 and repo.get("full_name") == repository
            and event.get("repository", {}).get("id") == repo["id"]
            and event["repository"].get("full_name") == repository, "manual SDK repository mismatch")
    require(type(workflow.get("id")) is int and workflow["id"] > 0
            and workflow.get("path") == ".github/workflows/publish-typescript-sdk.yml"
            and current.get("id") == int(current_id) and current.get("run_attempt") == int(current_attempt)
            and current.get("workflow_id") == workflow["id"] and current.get("path") == workflow["path"]
            and current.get("event") == "workflow_dispatch" and current.get("head_branch") == "main"
            and all(current.get(key, {}).get("id") == repo["id"] and current.get(key, {}).get("full_name") == repository
                    for key in ("repository", "head_repository")), "manual SDK execution identity mismatch")
    verify_ci(api, repository, sha, run_id, attempt)
    return True


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
        return {name: archive.read(name) for name in names if name in {"plan.json", "evidence.json", "result.json", "package/candidate.json"} or re.fullmatch(r"package/[A-Za-z0-9_.-]+\.tgz", name)}


def producing_job(api, repository, run_id, attempt, sha, name, workflow_id, repository_id):
    run = api.get(f"repos/{repository}/actions/runs/{run_id}/attempts/{attempt}")
    require(run.get("id") == run_id and run.get("run_attempt") == attempt and run.get("head_sha") == sha
            and run.get("workflow_id") == workflow_id and run.get("path") == ".github/workflows/ci.yml"
            and run.get("event") == "push" and run.get("head_branch") == "main"
            and all(run.get(key, {}).get("id") == repository_id and run.get(key, {}).get("full_name") == repository for key in ("repository", "head_repository")), "artifact producer is not the exact main CI revision")
    jobs = api.pages(f"repos/{repository}/actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100", field="jobs")
    found = [job for job in jobs if job.get("name") == name]
    require(len(found) == 1 and found[0].get("status") == "completed" and found[0].get("conclusion") == "success", "artifact producing job did not complete successfully")


def reference_attempt(reference, prefix, current):
    match = re.fullmatch(re.escape(prefix) + r"-attempt([1-9][0-9]*)", str(reference))
    require(match is not None and 1 <= int(match[1]) <= current, "invalid validation artifact attempt")
    return int(match[1])


def verify_candidate(candidate, package, manifest, sha):
    require(candidate.get("name") == manifest["name"] == "@skytruth/shared-datasets" and candidate.get("version") == manifest["version"], "candidate package identity mismatch")
    require(candidate.get("tested_sha") == sha and re.fullmatch(r"[A-Za-z0-9_.-]+\.tgz", str(candidate.get("tarball"))), "candidate is not bound to its tested revision")
    require(candidate.get("sha256") == hashlib.sha256(package).hexdigest(), "tested package SHA-256 mismatch")
    require(candidate.get("integrity") == "sha512-" + base64.b64encode(hashlib.sha512(package).digest()).decode(), "tested package integrity mismatch")


def verified_plan(api, repository, run_id, attempt, sha, root):
    run = verify_ci(api, repository, sha, run_id, attempt)
    artifacts = api.pages(f"repos/{repository}/actions/runs/{run_id}/artifacts?per_page=100", field="artifacts")
    evidence = json.loads(artifact_files(api, repository, run_id, artifacts, f"ci-ready-evidence-attempt{attempt}")["evidence.json"])
    require(evidence.get("source") == {"run_id": str(run_id), "run_attempt": attempt} and evidence.get("status") == "success" and evidence.get("tested_sha") == sha and evidence.get("head") == sha, "ci-ready evidence source mismatch")
    plan_attempt = reference_attempt(evidence.get("plan_artifact"), "ci-validation-plan", attempt)
    plan = json.loads(artifact_files(api, repository, run_id, artifacts, evidence["plan_artifact"])["plan.json"])
    require(plan.get("source") == {"run_id": str(run_id), "run_attempt": plan_attempt} and all(plan.get(key) == evidence.get(key) for key in (*IDENTITY, "suites", "changed_paths", "selection_reason")), "validation plan differs from ci-ready evidence")
    require(plan.get("schema_version") == 1 and plan.get("tree") == subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], cwd=root, text=True).strip() and plan.get("contract_digest") == contract_digest(root), "validation tree or contract changed")
    producing_job(api, repository, run_id, plan_attempt, sha, "geospatial-changes", run["workflow_id"], run["repository"]["id"])
    return run, artifacts, evidence, plan


def download(api, repository, run_id, attempt, sha, directory, root):
    run, artifacts, evidence, plan = verified_plan(api, repository, run_id, attempt, sha, root)
    require(("sdk-node22" in plan["suites"]) == ("sdk-node24" in plan["suites"]), "incomplete SDK runtime coverage")
    needed = subprocess.check_output(["node", "api/typescript/scripts/release-policy.mjs", "changes", plan["base"], sha], cwd=root, text=True).strip()
    require(needed in {"release_needed=true", "release_needed=false"}, "invalid SDK version policy result")
    outputs = {"release_needed": needed.partition("=")[2], "base_sha": plan["base"]}
    if "sdk-node24" not in plan["suites"]:
        require(outputs["release_needed"] == "false", "release requires missing SDK validation")
        return outputs
    reference = evidence.get("suite_artifacts", {}).get("sdk-node24")
    suite_attempt = reference_attempt(reference, "ci-result-sdk-node24", attempt)
    files = artifact_files(api, repository, run_id, artifacts, reference)
    result = json.loads(files["result.json"])
    require(result.get("source") == {"run_id": str(run_id), "run_attempt": suite_attempt} and result.get("suite") == "sdk-node24" and result.get("status") == "success", "SDK result source or status mismatch")
    require(all(result.get(key) == plan.get(key) for key in IDENTITY), "SDK result differs from tested plan")
    require(result.get("tools") == expected_tools("sdk-node24") and result.get("commands") and all(command.get("exit_code") == 0 for command in result["commands"]), "SDK validation was skipped, failed or used another toolchain")
    producing_job(api, repository, run_id, suite_attempt, sha, "sdk-validation (Node 24)", run["workflow_id"], run["repository"]["id"])
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
        admission = admit_manual_source if os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch" else admit_source
        eligible = admission(api, os.environ["GITHUB_REPOSITORY"], args.source_run_id, args.source_run_attempt, args.executor_sha, event)
        if not eligible:
            print("Verified CI source is obsolete, cancelled, or lacks passing ci-ready; no SDK release is authorized.")
            outputs = {"source_eligible": "false", "release_needed": "false"}
        elif args.admit_source:
            outputs = {"source_eligible": "true"}
        else:
            # Transport variants share the same strict boundary; named artifact
            # pagination additionally needs the keyword-only field argument.
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
