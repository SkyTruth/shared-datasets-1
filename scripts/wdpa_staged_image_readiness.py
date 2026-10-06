#!/usr/bin/env python3
"""Read-only availability of the exact reviewed WDPA producer image artifact."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import wdpa_processing_gate as gate

REPOSITORY = "SkyTruth/shared-datasets-1"
REPOSITORY_ID = 1213606315
ARTIFACT_NAME = "wdpa-benchmark-image"
QUEUE_DOWNLOAD_MARGIN = timedelta(hours=24)
MAX_API_BYTES = 1024 ** 2
MAX_ARTIFACT_BYTES = 2 * 1024 ** 3
MAX_SOURCE_ARCHIVE_BYTES = 128 * 1024 ** 2
MAX_SOURCE_EXPANSION_BYTES = 256 * 1024 ** 2
MAX_SOURCE_FILE_BYTES = 16 * 1024 ** 2


class ReadinessError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise ReadinessError(message)


def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate JSON key in retained image evidence")
        result[key] = value
    return result


def strict_json(raw):
    return json.loads(raw, object_pairs_hook=unique_pairs)


class GitHub:
    """Fixed public repository metadata API; credentials never leave GitHub."""
    def get(self, path):
        require(re.fullmatch(r"/repos/SkyTruth/shared-datasets-1/actions/artifacts/[1-9][0-9]*", path),
                "unsupported retained image metadata request")
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
        if token:
            headers["Authorization"] = "Bearer " + token
        request = urllib.request.Request("https://api.github.com" + path, headers=headers)
        try:
            with open_without_redirects(request) as response:
                raw = response.read(MAX_API_BYTES + 1)
                require(len(raw) <= MAX_API_BYTES, "retained image metadata exceeds size limit")
                return strict_json(raw)
        except urllib.error.HTTPError as error:
            raise ReadinessError(f"retained image metadata unavailable (GitHub HTTP {error.code})") from error
        except (urllib.error.URLError, TimeoutError) as error:
            raise ReadinessError("retained image metadata unavailable; check GitHub connectivity or API quota") from error


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        raise ReadinessError("retained image proof endpoint redirected; refusing to forward credentials")


def open_without_redirects(request):
    return urllib.request.build_opener(NoRedirect).open(request, timeout=30)


def source_snapshot(response, revision):
    """Read a bounded complete codeload snapshot; never extract or execute it."""
    directory = Path(os.environ.get("SHARED_DATASETS_WORKDIR", Path(tempfile.gettempdir()) / "shared-datasets-1")) / "_scratch"
    directory.mkdir(parents=True, exist_ok=True)
    prefix = f"shared-datasets-1-{revision}"
    with tempfile.TemporaryDirectory(prefix="wdpa-source-proof-", dir=directory) as work:
        compressed = Path(work) / "source.tar.gz"
        expanded = Path(work) / "source.tar"
        size = 0
        with compressed.open("xb") as output:
            for chunk in iter(lambda: response.read(1024 ** 2), b""):
                size += len(chunk)
                require(size <= MAX_SOURCE_ARCHIVE_BYTES, "original producer source archive exceeds download limit")
                output.write(chunk)
        require(size > 0, "original producer source archive is empty")
        size = 0
        with gzip.open(compressed, "rb") as source, expanded.open("xb") as output:
            for chunk in iter(lambda: source.read(1024 ** 2), b""):
                size += len(chunk)
                require(size <= MAX_SOURCE_EXPANSION_BYTES, "original producer source archive exceeds expansion limit")
                output.write(chunk)
        with tarfile.open(expanded, mode="r:") as archive:
            members = archive.getmembers()
            names = [member.name for member in members]
            require(members and len(members) <= 16384 and len(names) == len(set(names)),
                    "original producer archive has duplicate or excessive members")
            inventory = {}
            for member in members:
                path = PurePosixPath(member.name)
                require(path.parts and not path.is_absolute() and ".." not in path.parts and "\\" not in member.name
                        and path.parts[0] == prefix, "original producer archive has an unsafe path or another revision")
                if len(path.parts) > 1:
                    relative = path.relative_to(prefix).as_posix()
                    require(relative not in inventory, "original producer archive has ambiguous normalized paths")
                    inventory[relative] = member
            # A second tar stream or unenumerated trailing data cannot provide a
            # different source tree behind the first tar end marker.
            with expanded.open("rb") as tail:
                tail.seek(archive.offset)
                require(not any(byte for chunk in iter(lambda: tail.read(1024 ** 2), b"") for byte in chunk),
                        "original producer archive has unenumerated trailing members")
            digest = hashlib.sha256()
            for path in gate.source_paths(inventory):
                member = inventory.get(path)
                require(member is not None and member.isfile() and member.size <= MAX_SOURCE_FILE_BYTES,
                        f"original producer source file is missing, linked or oversized: {path}")
                raw = archive.extractfile(member).read()
                digest.update(path.encode() + b"\0" + raw + b"\0")
            return digest.hexdigest()


def original_source_digest(revision):
    """Hash exact original public source bytes independently of local Git history."""
    require(re.fullmatch(r"[0-9a-f]{40}", str(revision)), "invalid original producer revision")
    # No authorization header or configurable endpoint is used for public source.
    request = urllib.request.Request(f"https://codeload.github.com/{REPOSITORY}/tar.gz/{revision}")
    try:
        with open_without_redirects(request) as response:
            return source_snapshot(response, revision)
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as error:
        raise ReadinessError("original producer source snapshot is unavailable") from error


def reviewed_image(evidence, root):
    image = evidence["tested_image_artifact"]
    require(type(image.get("artifact_id")) is int and image["artifact_id"] > 0,
            "reviewed retained image must name an exact artifact ID")
    require(re.fullmatch(r"sha256:[0-9a-f]{64}", str(image.get("artifact_sha256"))),
            "reviewed retained image lacks its original ZIP digest")
    match = re.fullmatch(r"https://github.com/SkyTruth/shared-datasets-1/actions/runs/([1-9][0-9]*)",
                        str(image.get("workflow_run_url")))
    require(match is not None and re.fullmatch(r"[0-9a-f]{40}", str(image.get("head_sha"))),
            "reviewed retained image lacks its original run and revision")
    for name in ("small_fixture", "compatibility_sample"):
        check = evidence[name]
        require(check["workflow_run_url"] == image["workflow_run_url"] and check["head_sha"] == image["head_sha"]
                and check["image_digest"] == image["configuration_digest"],
                "reviewed small/sample checks differ from the retained producer image")
        path = (root / check["image_metadata_path"]).resolve()
        require(path.is_relative_to(root.resolve()) and path.is_file()
                and hashlib.sha256(path.read_bytes()).hexdigest() == check["image_metadata_sha256"],
                "reviewed producer image metadata bytes changed or are missing")
        require(strict_json(path.read_bytes())["containerimage.config.digest"] == image["configuration_digest"],
                "reviewed producer metadata names another config digest")
    configuration = (root / image["configuration_blob_path"]).resolve()
    require(configuration.is_relative_to(root.resolve()) and configuration.is_file(),
            "reviewed producer config blob is missing")
    raw = configuration.read_bytes()
    config_sha = hashlib.sha256(raw).hexdigest()
    require(image["configuration_blob_sha256"] == config_sha and image["configuration_digest"] == "sha256:" + config_sha,
            "reviewed producer config bytes differ from their original digest")
    config = strict_json(raw)
    require(config.get("os") == "linux" and config.get("architecture") == "amd64", "reviewed producer platform is not Linux/amd64")
    environment = config["config"]["Env"]
    executor = [entry.partition("=")[2] for entry in environment if entry.startswith("SHARED_DATASETS_EXECUTOR_SHA=")]
    require(executor == [image["head_sha"]], "reviewed producer config differs from the artifact's original executor")
    producer_digest = original_source_digest(executor[0])
    errors = gate.check_precloud(evidence, producer_source_sha256=producer_digest)
    require(not errors, "reviewed producer checks are incomplete: " + "; ".join(errors))
    return image, int(match[1])


def verify(api, evidence, *, root=ROOT, now=None):
    image, run_id = reviewed_image(evidence, root)
    artifact = api.get(f"/repos/{REPOSITORY}/actions/artifacts/{image['artifact_id']}")
    require(isinstance(artifact, dict), "retained image metadata is not an API object")
    require(artifact.get("id") == image["artifact_id"] and type(artifact.get("id")) is int
            and artifact.get("name") == ARTIFACT_NAME, "retained image API returned another artifact identity")
    source = artifact.get("workflow_run", {})
    require(source.get("id") == run_id and type(source.get("id")) is int
            and source.get("head_sha") == image["head_sha"]
            and source.get("repository_id") == REPOSITORY_ID and source.get("head_repository_id") == REPOSITORY_ID,
            "retained image artifact has another run, revision or repository provenance")
    require(artifact.get("digest") == image["artifact_sha256"], "retained image API digest differs from the reviewed ZIP bytes")
    require(type(artifact.get("size_in_bytes")) is int and 0 < artifact["size_in_bytes"] <= MAX_ARTIFACT_BYTES,
            "retained image API size is missing, empty or exceeds the supported bound")
    require(artifact.get("expired") is False, "reviewed retained image artifact is expired")
    expiry = datetime.strptime(artifact["expires_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    require(expiry > current + QUEUE_DOWNLOAD_MARGIN,
            "reviewed retained image expires within the required 24-hour queue/download margin")
    return {"artifact_id": str(image["artifact_id"]), "run_id": str(run_id),
            "config_digest": image["configuration_digest"], "source_digest": evidence["source_tree_sha256"],
            "expires_at": artifact["expires_at"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args()
    try:
        evidence = strict_json((ROOT / "catalog/wdpa-staged-validation.json").read_bytes())
        result = verify(GitHub(), evidence)
        if args.github_output:
            with args.github_output.open("a") as output:
                for key, value in result.items():
                    output.write(f"{key}={value}\n")
        print("Reviewed WDPA retained image is available with at least 24 hours before expiration.")
    except (ReadinessError, OSError, EOFError, ValueError, KeyError, TypeError, AttributeError, tarfile.TarError) as error:
        parser.exit(1, f"WDPA_RETAINED_IMAGE_NOT_READY: {error}\n")


if __name__ == "__main__":
    main()
