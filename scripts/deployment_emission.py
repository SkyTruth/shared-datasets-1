"""Verify durable run-bound receipts using GitHub's Sigstore attestations.

Deployment API payloads, creators and log URLs are untrusted claims. A receipt
binds their exact record/status IDs to a signature whose Fulcio certificate
identifies the actual main workflow, revision and run attempt. Receipt bytes
are reconstructed from the durable API record; ordinary Actions artifact
retention does not participate in this protocol.
"""
from __future__ import annotations

import hashlib
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SCHEMA = "shared-datasets-deployment-emission-v1"
PREDICATE = "https://slsa.dev/provenance/v1"
PHASES = {
    "started": "in_progress", "verification_pending": "in_progress",
    "applied": "success", "verified": "success", "failed": "failure", "unknown": "error",
}
OBSERVER = ".github/workflows/deployment-verification.yml"
RECOVERY = ".github/workflows/deployment-recovery.yml"
REHEARSAL_SIGNERS = {
    "deployment-receipt-rehearsal.yml", "prod-terraform-target-apply.yml",
    "deployment-readiness.yml", "deployment-verification.yml", "deployment-recovery.yml",
    "wdpa-monthly-deploy.yml", "wdpa-processing-validation-deploy.yml",
    "eamlis-monthly-deploy.yml", "sea-ice-daily-deploy.yml", "publish-typescript-sdk.yml",
    "pmtiles-cdn-sync.yml", "catalog-web-deploy.yml", "catalog-viewer-deploy.yml",
}


class ReadOnlyGitHub:
    """Rehearsal can land before deployment writers and never creates a record."""

    def get(self, path):
        return json.loads(subprocess.check_output(["gh", "api", path]))


class EmissionError(RuntimeError):
    pass


def require(value, message):
    if not value:
        raise EmissionError("DEPLOYMENT_EMISSION: " + message)


def receipt(repository, record, status):
    payload = record["payload"]
    if isinstance(payload, str):
        payload = json.loads(payload)
    require(type(record.get("id")) is int and record["id"] > 0, "record ID unavailable")
    require(type(status.get("id")) is int and status["id"] > 0, "status ID unavailable")
    require(status.get("description") in PHASES and status.get("state") == PHASES[status["description"]], "unrecognized or incompatible phase")
    return {
        "schema": SCHEMA, "repository": repository, "deployment_id": record["id"],
        "executor_sha": record["sha"], "environment": record["environment"], "payload": payload,
        "status": {key: status.get(key, "") for key in ("id", "state", "description", "environment_url", "log_url")},
    }


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode()


def name(value):
    return f"record-{value['deployment_id']}-status-{value['status']['id']}.json"


def emit(repository, record, status):
    value = receipt(repository, record, status)
    directory = Path(os.environ["RUNNER_TEMP"]) / "deployment-receipts"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name(value)
    path.write_bytes(canonical(value))
    with Path(os.environ["GITHUB_OUTPUT"]).open("a") as stream:
        stream.write(f"receipt_path={path}\n")
    return path


def invocation(repository, run_id, attempt):
    return f"https://github.com/{repository}/actions/runs/{run_id}/attempts/{attempt}"


def policy(repository, repo_id, value, run, signer):
    source = "https://github.com/" + repository
    require(run.get("head_branch") == "main" and run.get("event") in {"push", "workflow_dispatch", "workflow_run", "schedule"}, "receipt emitter is not trusted main")
    require(re.fullmatch(r"[0-9a-f]{40}", str(run.get("head_sha", ""))), "emitter revision unavailable")
    require(all(run.get(key, {}).get("id") == repo_id and run.get(key, {}).get("full_name") == repository for key in ("repository", "head_repository")), "emitter repository identity mismatch")
    url = invocation(repository, run["id"], run["run_attempt"])
    require(value["status"]["log_url"] == url, "phase must identify its exact emitter attempt")
    return {
        "issuer": "https://token.actions.githubusercontent.com", "runnerEnvironment": "github-hosted",
        "sourceRepositoryURI": source, "sourceRepositoryIdentifier": str(repo_id),
        "sourceRepositoryRef": "refs/heads/main", "sourceRepositoryDigest": run["head_sha"],
        "buildSignerURI": source + "/" + signer + "@refs/heads/main",
        "buildSignerDigest": run["head_sha"],
        "buildConfigURI": source + "/" + run["path"] + "@refs/heads/main",
        "buildConfigDigest": run["head_sha"], "buildTrigger": run["event"], "runInvocationURI": url,
    }


def verify_result(results, value, expected):
    require(isinstance(results, list) and results, "no cryptographically verified receipt")
    digest = hashlib.sha256(canonical(value)).hexdigest()
    for result in results:
        verified = result.get("verificationResult", {})
        cert = verified.get("signature", {}).get("certificate", {})
        statement = verified.get("statement", {})
        if (
            all(cert.get(key) == item for key, item in expected.items())
            and verified.get("verifiedTimestamps")
            and statement.get("_type") == "https://in-toto.io/Statement/v1"
            and statement.get("predicateType") == PREDICATE
            and statement.get("subject") == [{"name": name(value), "digest": {"sha256": digest}}]
        ):
            return
    raise EmissionError("DEPLOYMENT_EMISSION: signed receipt does not bind the exact record, phase and protected emitter")


def verify(api, repository, record, status, *, original_signer):
    value = receipt(repository, record, status)
    match = re.fullmatch(r"https://github\.com/" + re.escape(repository) + r"/actions/runs/([1-9][0-9]*)/attempts/([1-9][0-9]*)", status.get("log_url", ""))
    require(match is not None, "receipt lacks exact run/attempt identity")
    run_id, attempt = map(int, match.groups())
    run = api.get(f"repos/{repository}/actions/runs/{run_id}/attempts/{attempt}")
    require(run.get("id") == run_id and run.get("run_attempt") == attempt, "emitter attempt mismatch")
    payload = value["payload"]
    if (run_id, attempt) == (payload["execution_run_id"], payload["execution_run_attempt"]):
        signer = original_signer
    else:
        require(status["description"] != "started" and run.get("path") in {OBSERVER, RECOVERY}, "phase update came from an unrelated execution")
        signer = run["path"]
    repo = api.get(f"repos/{repository}")
    workflow = api.get(f"repos/{repository}/actions/workflows/{run['path'].rsplit('/', 1)[-1]}")
    require(workflow.get("id") == run.get("workflow_id") and workflow.get("path") == run["path"], "emitter workflow identity mismatch")
    expected = policy(repository, repo["id"], value, run, signer)
    receipts = Path(os.environ["RUNNER_TEMP"]) / "deployment-receipts"
    receipts.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="verify-", dir=receipts) as directory:
        path = Path(directory) / name(value)
        path.write_bytes(canonical(value))
        command = [
            "gh", "attestation", "verify", str(path), "--repo", repository,
            "--signer-workflow", repository + "/" + signer,
            "--signer-digest", expected["buildSignerDigest"],
            "--source-digest", expected["sourceRepositoryDigest"],
            "--source-ref", "refs/heads/main", "--deny-self-hosted-runners", "--format", "json",
        ]
        # A nonzero exit (including unavailable attestations or trusted roots)
        # is fatal. Never parse a raw bundle as verified evidence.
        results = json.loads(subprocess.check_output(command, text=True))
    verify_result(results, value, expected)


def rehearsal(api, repository):
    run_id, attempt = int(os.environ["GITHUB_RUN_ID"]), int(os.environ["GITHUB_RUN_ATTEMPT"])
    require(os.environ.get("GITHUB_REF") == "refs/heads/main", "rehearsal requires main")
    current = api.get(f"repos/{repository}/actions/runs/{run_id}/attempts/{attempt}")
    require(current.get("id") == run_id and current.get("run_attempt") == attempt, "rehearsal attempt mismatch")
    claim = {
        "id": 1, "sha": current["head_sha"], "environment": "receipt-rehearsal",
        "payload": {"schema": SCHEMA, "target": "receipt-rehearsal", "artifact": "receipt-rehearsal@sha256:" + "0" * 64,
                    "execution_run_id": run_id, "execution_run_attempt": attempt},
    }
    phase = {"id": 1, "state": "in_progress", "description": "started", "environment_url": "", "log_url": invocation(repository, run_id, attempt)}
    return claim, phase


def verify_rehearsal(api, repository, signer):
    claim, phase = rehearsal(api, repository)
    verify(api, repository, claim, phase, original_signer=signer)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("prepare-rehearsal")
    rehearsal_check = commands.add_parser("verify-rehearsal")
    rehearsal_check.add_argument("--signer-workflow", required=True)
    file_check = commands.add_parser("verify-file")
    file_check.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    api, repository = ReadOnlyGitHub(), os.environ["GITHUB_REPOSITORY"]
    if args.command == "prepare-rehearsal":
        emit(repository, *rehearsal(api, repository))
    elif args.command == "verify-rehearsal":
        require(args.signer_workflow in REHEARSAL_SIGNERS, "unknown rehearsal signer")
        verify_rehearsal(api, repository, ".github/workflows/" + args.signer_workflow)
    else:
        from scripts import deployment_revision as revision
        api = revision.GitHub()
        value = json.loads(args.receipt.read_bytes())
        require(value.get("schema") == SCHEMA and value.get("repository") == repository, "invalid receipt file")
        record = api.get(f"repos/{repository}/deployments/{value['deployment_id']}")
        matches = [phase for phase in api.pages(f"repos/{repository}/deployments/{record['id']}/statuses?per_page=100") if phase.get("id") == value["status"]["id"]]
        require(len(matches) == 1 and receipt(repository, record, matches[0]) == value, "receipt differs from its durable record/status")
        revision.verify_record(api, repository, record)
        revision.verify_receipt(api, repository, record, matches[0])


if __name__ == "__main__":
    main()
