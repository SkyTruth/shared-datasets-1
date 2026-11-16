#!/usr/bin/env python3
"""Prepare a read-only MPAtlas recovery proposal; execution uses reviewed authority."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
MAX_REVIEW_BODY_CHARACTERS = 60_000


def load_runtime(root):
    # In the resume subprocess, every publication import resolves to the
    # archived original executor. Only this reviewed recovery adapter is new.
    sys.path.insert(0, str(root))
    spec = importlib.util.spec_from_file_location("reviewed_mpatlas_recovery", ROOT / "ingestion/mpatlas_monthly/publication_recovery.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verify_original_revision(revision):
    from ingestion.common import publication as p
    p.require(subprocess.run(["git", "--no-replace-objects", "merge-base", "--is-ancestor", revision, "HEAD"], cwd=ROOT).returncode == 0,
              "original executor must be an ancestor of the accepted recovery executor")
    subprocess.run(["git", "--no-replace-objects", "cat-file", "-e", f"{revision}:uv.lock"], cwd=ROOT, check=True)


def verify_image_executor(image, revision, *, api=None):
    """Bind an immutable registry image to an attested tested deployment SHA.

    Cloud Run execution env excludes image-baked defaults. Durable signed
    deployment evidence survives expiry of the original image TAR artifact.
    """
    from scripts import deployment_revision as deployments
    from ingestion.common import publication as p
    if "RUNNER_TEMP" not in os.environ:
        from ingestion.common.publication_gcs import work_root
        os.environ["RUNNER_TEMP"] = str(work_root() / "mpatlas-recovery")
    repository = "SkyTruth/shared-datasets-1"
    api = deployments.GitHub() if api is None else api
    records = api.pages(f"repos/{repository}/deployments?sha={revision}&environment=production-mpatlas-monthly&per_page=100")
    matches = [record for record in records if record.get("sha") == revision
        and deployments.record_payload(record).get("artifact") == image]
    p.require(bool(matches), "original image has no matching signed deployment evidence")
    record = max(matches, key=lambda item: item["id"])
    payload = deployments.verify_record(api, repository, record)
    p.require(payload["target"] == "mpatlas-monthly" and payload["artifact"] == image,
              "signed deployment does not bind the original image and executor")


def proposal_document(plan):
    from scripts import reviewed_dataset_plan as plans
    from ingestion.common import publication as p
    document = plans.normalize_document({"plan_version": 1, "finalization_version": plans.FINALIZATION_VERSION, "publish": plan})
    size = len(plans.render_document(document))
    p.require(size <= MAX_REVIEW_BODY_CHARACTERS,
        f"recovery review body is {size} characters; limit is {MAX_REVIEW_BODY_CHARACTERS} to leave room below GitHub's PR body limit. "
        "The captured receipt includes the full release index and recorded results. Stop preparation; a separately reviewed bounded-reference recovery contract is required. "
        "Do not truncate the intent, prune history, or bypass the matching plan fence.")
    return document


def run_original(plan, *, directory, digest):
    from ingestion.common import publication as p
    from ingestion.common.publication_gcs import work_root
    revision = plan["publication_recovery"]["executor_sha"]
    verify_original_revision(revision)
    verify_image_executor(plan["publication_recovery"]["image"], revision)
    with tempfile.TemporaryDirectory(prefix="mpatlas-original-executor-", dir=work_root()) as temporary:
        work = Path(temporary)
        executor = work / "executor"
        executor.mkdir()
        archive = subprocess.check_output(["git", "--no-replace-objects", "archive", revision], cwd=ROOT)
        with tarfile.open(fileobj=io.BytesIO(archive)) as tree:
            tree.extractall(executor, filter="data")
        payload = work / "reviewed-plan.json"
        payload.write_bytes(p.canonical(plan))
        subprocess.run(["uv", "run", "--project", str(executor), "--frozen", "--no-dev", "--python", "3.12.12",
            "python", "-I", str(Path(__file__).resolve()), "resume", "--executor-root", str(executor), "--plan", str(payload),
            "--directory", str(directory.resolve()), "--expected-sha256", digest], cwd=ROOT,
            env={**os.environ, "UV_PROJECT_ENVIRONMENT": str(executor / ".venv")}, check=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare", help="Read paused/drained original execution and generation-pinned transaction evidence; write an unapproved proposal")
    prepare.add_argument("--execution", required=True)
    prepare.add_argument("--proposal-id", required=True)
    prepare.add_argument("--output", type=Path, required=True)
    for name in ("check", "verify", "resume"):
        command = commands.add_parser(name, help=argparse.SUPPRESS)
        command.add_argument("--directory", type=Path, required=True)
        command.add_argument("--expected-sha256", required=True)
        if name in {"check", "verify"}:
            command.add_argument("--plan-sha256", required=True)
        else:
            command.add_argument("--executor-root", type=Path, required=True)
            command.add_argument("--plan", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "resume":
        # Verify with current trusted code before importing original modules.
        subprocess.run([sys.executable, "-I", str(Path(__file__).resolve()), "verify", "--directory", str(args.directory),
            "--expected-sha256", args.expected_sha256, "--plan-sha256", hashlib.sha256(args.plan.read_bytes()).hexdigest()], cwd=ROOT, check=True)
    runtime = load_runtime(args.executor_root if args.command == "resume" else ROOT)
    from ingestion.common.reset_controls import GoogleControlReader, PROJECT, PUBLISHER_ACCOUNT, DEPLOYER_ACCOUNT
    import google.auth
    from google.auth.transport.requests import AuthorizedSession

    if args.command in {"check", "verify"}:
        from scripts import install_feature_id_reset as installer, dataset_mutation_authorization as auth
        _envelope, plan = installer.authorized_plan(args.directory, args.expected_sha256, api=auth.GitHub(), env=dict(os.environ))
        if hashlib.sha256(runtime.p.canonical(plan)).hexdigest() != args.plan_sha256:
            raise runtime.p.PublicationError("runtime plan differs from the immutable authorization")
        credentials, _ = google.auth.load_credentials_from_file(os.environ["RESET_CONTROL_CREDENTIALS"], scopes=["https://www.googleapis.com/auth/cloud-platform"])
        runtime.p.require(getattr(credentials, "service_account_email", None) == DEPLOYER_ACCOUNT, "protected observer identity is required")
        reader = GoogleControlReader(AuthorizedSession(credentials))
        reader.check_job_quiescent("mpatlas-monthly")
        recovery = plan["publication_recovery"]
        original = runtime.execution(reader, recovery["execution"], executor_sha=recovery["executor_sha"])
        runtime.p.require(original["image"] == recovery["image"], "original execution identity changed")
        if args.command == "verify":
            verify_image_executor(original["image"], recovery["executor_sha"])
        return 0

    from google.cloud import storage
    from ingestion.common.gcs import GcsPublisher
    from ingestion.common.publication_gcs import GcsStore
    credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    client = storage.Client(project=PROJECT, credentials=credentials)
    publisher, store = GcsPublisher(client, bucket_name=runtime.BUCKET), GcsStore(client)
    if args.command == "prepare":
        plan = runtime.prepare(publisher, store, GoogleControlReader(AuthorizedSession(credentials)), execution_name=args.execution,
            proposal_id=args.proposal_id, verify_image_executor=verify_image_executor)
        verify_original_revision(plan["publication_recovery"]["executor_sha"])
        document = proposal_document(plan)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(document["publish"], indent=2) + "\n")
        print("Prepared unapproved recovery payload. Create its immutable reviewed PR with reviewed_dataset_plan.py prepare --publish.")
        return 0

    runtime.p.require(getattr(credentials, "service_account_email", None) == PUBLISHER_ACCOUNT, "approved publisher identity is required")
    plan = runtime.p.strict_json(args.plan.read_bytes())
    plan_digest = hashlib.sha256(runtime.p.canonical(plan)).hexdigest()

    def guard():
        subprocess.run([sys.executable, "-I", str(Path(__file__).resolve()), "check", "--directory", str(args.directory),
            "--expected-sha256", args.expected_sha256, "--plan-sha256", plan_digest], cwd=ROOT, check=True)

    guard()
    control_credentials, _ = google.auth.load_credentials_from_file(os.environ["RESET_CONTROL_CREDENTIALS"], scopes=["https://www.googleapis.com/auth/cloud-platform"])
    def verified_binding(image, revision):
        runtime.p.require((image, revision) == (plan["publication_recovery"]["image"], plan["publication_recovery"]["executor_sha"]),
                          "runtime image differs from the current-code verified binding")

    result = runtime.recover(publisher, store, plan, GoogleControlReader(AuthorizedSession(control_credentials)),
        check_authority_and_jobs=guard, verify_image_executor=verified_binding)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
