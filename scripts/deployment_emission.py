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
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.github_api_read import read

SCHEMA = "shared-datasets-deployment-emission-v1"
PREDICATE = "https://slsa.dev/provenance/v1"
PHASES = {
    "started": "in_progress", "verification_pending": "in_progress",
    "applied": "success", "verified": "success", "failed": "failure", "unknown": "error",
}
OBSERVER = ".github/workflows/deployment-verification.yml"
RECOVERY = ".github/workflows/deployment-recovery.yml"
REHEARSAL_SIGNERS = {
    "dataset-usage-deploy.yml",
    "deployment-receipt-rehearsal.yml", "prod-terraform-target-apply.yml",
    "deployment-readiness.yml", "deployment-verification.yml", "deployment-recovery.yml",
    "wdpa-monthly-deploy.yml",
    "mpatlas-monthly-deploy.yml", "eamlis-monthly-deploy.yml", "sea-ice-daily-deploy.yml", "publish-typescript-sdk.yml",
    "pmtiles-cdn-sync.yml", "catalog-web-deploy.yml", "catalog-viewer-deploy.yml",
}


class ReadOnlyGitHub:
    """Rehearsal can land before deployment writers and never creates a record."""

    def get(self, path):
        return json.loads(read(path))


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


def _subject(value):
    return {"name": name(value), "digest": {"sha256": hashlib.sha256(canonical(value)).hexdigest()}}


def _matching_statements(results, value, expected, *, batch=False):
    """Yield only authenticated statements binding this exact anchor/context."""
    require(isinstance(results, list) and results, "no cryptographically verified receipt")
    subject = _subject(value)
    for result in results:
        if not isinstance(result, dict):
            continue
        verified = result.get("verificationResult", {})
        if not isinstance(verified, dict) or not isinstance(verified.get("signature", {}), dict):
            continue
        cert = verified.get("signature", {}).get("certificate", {})
        statement = verified.get("statement", {})
        if not isinstance(cert, dict) or not isinstance(statement, dict):
            continue
        subjects = statement.get("subject")
        subjects_match = subjects == [subject]
        if batch:
            # Notification checkpoints may be attested together. Require this
            # exact subject once; production receipts retain single-subject
            # verification unless the caller explicitly selects batch mode.
            subjects_match = isinstance(subjects, list) and 0 < len(subjects) <= 1024 and subjects.count(subject) == 1
        if (
            all(cert.get(key) == item for key, item in expected.items())
            and verified.get("verifiedTimestamps")
            and statement.get("_type") == "https://in-toto.io/Statement/v1"
            and statement.get("predicateType") == PREDICATE
            and subjects_match
        ):
            yield statement


def verify_result(results, value, expected, *, batch=False):
    require(next(_matching_statements(results, value, expected, batch=batch), None) is not None,
            "signed receipt does not bind the exact record, phase and protected emitter")


def verified_output(command, repository, digest):
    """Retry the observed read-only service outage, never failed verification."""
    outage = re.compile(
        r"Error: HTTP (?:503: trust-metadata-api service unavailable|502: Server Error) \(https://api\.github\.com/repos/"
        + re.escape(repository) + r"/attestations/sha256:" + digest + r"(?:\?[^\s)]*)?\)"
    )
    for attempt, delay in enumerate((5, 15, None), 1):
        try:
            return subprocess.check_output(command, text=True, stderr=subprocess.PIPE)
        except subprocess.CalledProcessError as error:
            stderr = error.stderr or ""
            print(stderr, end="" if stderr.endswith("\n") else "\n", file=sys.stderr)
            errors = [line for line in stderr.splitlines() if line.startswith("Error:")]
            if delay is None or len(errors) != 1 or not outage.fullmatch(errors[0]):
                raise
            print(f"DEPLOYMENT_EMISSION: attestation service unavailable; retrying verification ({attempt + 1}/3) in {delay}s", file=sys.stderr)
            time.sleep(delay)


def _verification_context(api, repository, record, status, original_signer):
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
    return value, expected, signer


def _verified_results(repository, value, expected, signer):
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
        # Only a confirmed transient service outage retries this same read.
        # A final nonzero exit is fatal; raw bundles never become evidence.
        return json.loads(verified_output(command, repository, hashlib.sha256(canonical(value)).hexdigest()))


def verify(api, repository, record, status, *, original_signer, batch=False):
    value, expected, signer = _verification_context(api, repository, record, status, original_signer)
    results = _verified_results(repository, value, expected, signer)
    verify_result(results, value, expected, batch=batch)


def verify_batch(api, repository, pairs, *, original_signer):
    """Verify one co-signed manifest/part group with a single anchor invocation.

    Every durable pair derives its own authoritative emitter policy. Only the
    cryptographically authenticated statement returned for the canonical first
    receipt can prove its siblings; separate statements are never combined.
    """
    require(isinstance(pairs, (list, tuple)) and 1 <= len(pairs) <= 513,
            "receipt batch must contain 1..513 record/status pairs")
    require(all(isinstance(pair, (list, tuple)) and len(pair) == 2 for pair in pairs),
            "receipt batch requires record/status pairs")
    contexts = [_verification_context(api, repository, record, status, original_signer) for record, status in pairs]
    anchor, expected, signer = contexts[0]
    require(all(candidate_expected == expected and candidate_signer == signer
                for _, candidate_expected, candidate_signer in contexts),
            "receipt batch mixes protected emitter provenance")
    subjects = [_subject(value) for value, _, _ in contexts]
    require(len({subject["name"] for subject in subjects}) == len(subjects),
            "receipt batch contains duplicate receipt names")
    results = _verified_results(repository, anchor, expected, signer)
    for statement in _matching_statements(results, anchor, expected, batch=True):
        members = statement["subject"]
        names = [member.get("name") if isinstance(member, dict) else None for member in members]
        if (all(isinstance(item, str) and item for item in names)
                and len(set(names)) == len(names)
                and all(members.count(subject) == 1 for subject in subjects)):
            return
    raise EmissionError("DEPLOYMENT_EMISSION: no authenticated statement binds the complete exact receipt batch")


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
