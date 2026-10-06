#!/usr/bin/env python3
"""Load only retained production image bytes proven by passing exact main CI."""
from __future__ import annotations

import argparse
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tempfile
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import sdk_release_authorization as proof
from scripts.deployment_revision import DeploymentError, GitHub as DeploymentGitHub, TARGET_WORKFLOWS, require, verify_context
from scripts.tested_image_bundle import ImageError, MAX_IMAGE, MAX_JSON, TARGETS, hash_file, load_image, read_json, validate_manifest

MAX_ARTIFACT = len(TARGETS) * MAX_IMAGE + 100 * 1024 ** 2


class GitHub(proof.GitHub):
    def archive_to(self, repository, artifact_id, path):
        # Avoid retaining the multi-image ZIP or its expanded members in memory.
        with Path(path).open("xb") as stream:
            command = ["gh", "api", f"repos/{repository}/actions/artifacts/{artifact_id}/zip"]
            with subprocess.Popen(command, stdout=subprocess.PIPE) as process:
                try:
                    size = 0
                    for chunk in iter(lambda: process.stdout.read(1024 ** 2), b""):
                        size += len(chunk)
                        require(size <= MAX_ARTIFACT, "retained-image download exceeds limit")
                        stream.write(chunk)
                    if process.wait() != 0:
                        raise subprocess.CalledProcessError(process.returncode, command)
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.wait()


def selected_image(api, repository, run_id, artifacts, reference, path, target, sha, plan, suite_attempt):
    found = [artifact for artifact in artifacts if artifact.get("name") == reference]
    require(len(found) == 1, "missing or ambiguous retained-image artifact")
    artifact = found[0]
    require(type(artifact.get("id")) is int and artifact["id"] > 0 and not artifact.get("expired")
            and artifact.get("workflow_run", {}).get("id") == run_id
            and type(artifact.get("size_in_bytes")) is int and 0 < artifact["size_in_bytes"] <= MAX_ARTIFACT,
            "retained-image artifact is expired, oversized or belongs to another run")
    with tempfile.TemporaryDirectory(prefix="tested-image-download-", dir=path.parent) as work:
        archive_path = Path(work) / "artifact.zip"
        api.archive_to(repository, artifact["id"], archive_path)
        require(0 < archive_path.stat().st_size <= MAX_ARTIFACT
                and artifact.get("digest") == "sha256:" + hash_file(archive_path), "retained-image API archive digest mismatch")
        with zipfile.ZipFile(archive_path) as archive:
            entries = archive.infolist()
            names = [entry.filename for entry in entries]
            require(len(entries) <= 4096 and len(names) == len(set(names)), "duplicate or excessive retained-image artifact members")
            require(all(not PurePosixPath(name).is_absolute() and ".." not in PurePosixPath(name).parts
                        and "\\" not in name for name in names), "unsafe retained-image artifact member")
            require(sum(entry.file_size for entry in entries) <= MAX_ARTIFACT
                    and all(entry.file_size <= MAX_IMAGE and not entry.flag_bits & 1 for entry in entries), "retained-image artifact expansion exceeds limit")

            def metadata(name):
                require(name in names and archive.getinfo(name).file_size <= MAX_JSON, "missing or oversized retained-image evidence")
                return read_json(archive.read(name))

            result = metadata("result.json")
            require(result.get("source") == {"run_id": str(run_id), "run_attempt": suite_attempt}
                    and result.get("suite") == "production-images" and result.get("status") == "success"
                    and all(result.get(key) == plan.get(key) for key in proof.IDENTITY), "image result differs from tested plan or source attempt")
            require(result.get("tools") == proof.expected_tools("production-images") and result.get("commands")
                    and all(command.get("exit_code") == 0 for command in result["commands"]), "production image validation failed, skipped or used another toolchain")
            manifest = metadata("images/manifest.json")
            require(result.get("production_images") == manifest, "image manifest differs from recorded validation result")
            images = validate_manifest(manifest, sha)
            require(set(images) == TARGETS and target in images, "complete tested production-image set is missing")
            image = images[target]
            member = "images/" + image["archive"]
            require(member in names and archive.getinfo(member).file_size == image["archive_size"], "selected tested image archive is missing or has another size")
            require({name for name in names if name.startswith("images/") and not name.endswith("/")} ==
                    {"images/manifest.json", *("images/" + item["archive"] for item in images.values())}, "unexpected retained image artifact member")
            # Exactly one safe target archive is copied; other images and artifact
            # executables remain inside the authenticated ZIP and are not loaded.
            with archive.open(member) as source, path.open("xb") as destination:
                shutil.copyfileobj(source, destination, length=1024 ** 2)
    return image


def download(api, repository, run_id, attempt, sha, target, directory, root):
    require(target in TARGETS, "target has no retained-image contract")
    run, artifacts, evidence, plan = proof.verified_plan(api, repository, run_id, attempt, sha, root)
    require("production-images" in plan["suites"], "production images were not selected for validation")
    reference = evidence.get("suite_artifacts", {}).get("production-images")
    suite_attempt = proof.reference_attempt(reference, "ci-result-production-images", attempt)
    proof.producing_job(api, repository, run_id, suite_attempt, sha, "production-images", run["workflow_id"], run["repository"]["id"])
    directory.mkdir(parents=True, exist_ok=True)
    archive = directory / f"{target}.docker.tar"
    image = selected_image(api, repository, run_id, artifacts, reference, archive, target, sha, plan, suite_attempt)
    image_id = load_image(archive, image)
    return {"image_id": image_id, "config_digest": image["config_digest"], "source_tag": image["source_tag"], "archive": str(archive.resolve()),
            "artifact": target + "@" + image["config_digest"], "tested_sha": sha}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflow", required=True)
    parser.add_argument("--target", required=True, choices=sorted(TARGETS))
    parser.add_argument("--executor-sha", required=True)
    parser.add_argument("--source-run-id", required=True, type=int)
    parser.add_argument("--source-run-attempt", required=True, type=int)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        require(TARGET_WORKFLOWS[args.target] == args.workflow, "retained-image target differs from the authorized workflow")
        verify_context(args, DeploymentGitHub())
        outputs = download(GitHub(), os.environ["GITHUB_REPOSITORY"], args.source_run_id, args.source_run_attempt,
                           args.executor_sha, args.target, args.output, Path.cwd())
        with Path(os.environ["GITHUB_OUTPUT"]).open("a") as stream:
            for key, value in outputs.items():
                stream.write(f"{key}={value}\n")
    except (DeploymentError, ImageError, OSError, ValueError, KeyError, zipfile.BadZipFile, subprocess.CalledProcessError) as error:
        parser.exit(1, f"TESTED_IMAGE_NOT_READY: {error}\n")


if __name__ == "__main__":
    main()
