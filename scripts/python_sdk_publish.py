#!/usr/bin/env python3
"""Publish only a clean-install-tested Python package from accepted, passing main."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.dataset_mutation_authorization import GitHub, executor_acceptance
from scripts.deployment_revision import require, verify_ci, verify_run
from scripts.python_sdk_release import git, source_revision, version

WORKFLOW = ".github/workflows/python-sdk.yml"


def release_state(api, repository, head, release_version):
    """Reconcile the current package version, independent of merge shape/diffs."""
    tag = "python-sdk-v" + release_version
    refs = [ref for ref in api.pages(f"repos/{repository}/git/matching-refs/tags/{tag}") if ref["ref"] == "refs/tags/" + tag]
    require(len(refs) <= 1, "ambiguous Python SDK tag")
    source = head
    if refs:
        require(refs[0]["object"].get("type") == "commit", "Python SDK tag must name its immutable source commit")
        source = source_revision(refs[0]["object"]["sha"])
    releases = [release for release in api.pages(f"repos/{repository}/releases?per_page=100") if release["tag_name"] == tag]
    require(len(releases) <= 1, "ambiguous Python SDK release")
    require(not releases or refs, "Python release tag is missing")
    return source, not releases or releases[0]["draft"]


def authorize(api):
    repository = os.environ["GITHUB_REPOSITORY"]
    sha = os.environ["GITHUB_SHA"]
    require(os.environ.get("GITHUB_EVENT_NAME") == "workflow_run" and os.environ.get("GITHUB_REF") == "refs/heads/main", "Python releases require a main CI completion")
    require(os.environ.get("GITHUB_WORKFLOW_REF") == f"{repository}/{WORKFLOW}@refs/heads/main", "Python release workflow must originate from main")
    require(git("rev-parse", "HEAD") == sha == os.environ["GITHUB_WORKFLOW_SHA"], "Python release checkout differs from trusted workflow revision")
    current = api.get(f"repos/{repository}/actions/runs/{os.environ['GITHUB_RUN_ID']}")
    verify_run(api, repository, current, paths={WORKFLOW}, events={"workflow_run"})
    require(current.get("head_sha") == sha and current.get("run_attempt") == int(os.environ["GITHUB_RUN_ATTEMPT"]), "Python release run revision/attempt mismatch")
    source = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())["workflow_run"]
    require(source.get("head_sha") == sha and source.get("conclusion") == "success", "CI completion does not certify this revision")
    verified = verify_ci(api, repository, sha, source["id"], source["run_attempt"])
    require(verified.get("conclusion") == "success" and verified.get("status") == "completed", "source CI run is not successfully completed")
    require(api.get(f"repos/{repository}/git/ref/heads/main")["object"]["sha"] == sha, "Python release was superseded on main")
    executor_acceptance(api, repository, sha)
    return repository, sha


def candidate(directory, sha, expected_version):
    value = json.loads((directory / "provenance.json").read_text())
    require(value.get("schema_version") == 1 and value.get("source_sha") == sha and value.get("version") == expected_version, "Python candidate source/version mismatch")
    names = {f"skytruth_shared_datasets-{expected_version}-py3-none-any.whl", f"skytruth_shared_datasets-{expected_version}.tar.gz"}
    require(set(value["files"]) == names and set(path.name for path in directory.iterdir()) == names | {"provenance.json", "SHA256SUMS"}, "Python candidate package members differ")
    require(value.get("installed_python") == ["3.10.20", "3.12.12"], "Python candidate lacks supported-interpreter install proof")
    for name, digest in value["files"].items():
        require(hashlib.sha256((directory / name).read_bytes()).hexdigest() == digest, "Python package checksum mismatch")
    require((directory / "SHA256SUMS").read_text() == "".join(f"{digest}  {name}\n" for name, digest in sorted(value["files"].items())), "Python checksum list differs")
    return {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in directory.iterdir()}


def write(path, payload, *, method="POST"):
    return json.loads(subprocess.check_output(["gh", "api", "--method", method, path, "--input", "-"], input=json.dumps(payload).encode()))


def publish(api, directory, repository, sha, release_version):
    hashes = candidate(directory, sha, release_version)
    tag = "python-sdk-v" + release_version
    refs = [ref for ref in api.pages(f"repos/{repository}/git/matching-refs/tags/{tag}") if ref["ref"] == "refs/tags/" + tag]
    require(len(refs) <= 1, "ambiguous Python SDK tag")
    if refs:
        require(refs[0]["object"].get("type") == "commit" and refs[0]["object"].get("sha") == sha, "Python SDK tag already identifies another commit")
    else:
        write(f"repos/{repository}/git/refs", {"ref": "refs/tags/" + tag, "sha": sha})
    releases = [release for release in api.pages(f"repos/{repository}/releases?per_page=100") if release["tag_name"] == tag]
    require(len(releases) <= 1, "ambiguous Python SDK release")
    release = releases[0] if releases else write(f"repos/{repository}/releases", {
        "tag_name": tag, "name": "Python SDK " + release_version, "draft": True, "prerelease": False, "make_latest": "false",
        "body": f"Python SDK {release_version}, tested at {sha}. Includes a wheel, source archive, SHA-256 checksums and build provenance. Install from this versioned tag or the attached wheel. No PyPI publication is configured.",
    })
    assets = api.pages(f"repos/{repository}/releases/{release['id']}/assets?per_page=100")
    require(len({asset["name"] for asset in assets}) == len(assets) and {asset["name"] for asset in assets} <= hashes.keys(), "Python release contains unexpected or duplicate assets")
    for asset in assets:
        data = subprocess.check_output(["gh", "api", f"repos/{repository}/releases/assets/{asset['id']}", "-H", "Accept: application/octet-stream"])
        require(hashlib.sha256(data).hexdigest() == hashes[asset["name"]], "Python release asset differs; refusing overwrite")
    missing = hashes.keys() - {asset["name"] for asset in assets}
    require(release["draft"] or not missing, "published Python release is incomplete; refusing mutation")
    for name in sorted(missing):
        subprocess.run(["gh", "release", "upload", tag, str(directory / name), "--repo", repository], check=True)
    if release["draft"]:
        # Rerunning after an interrupted upload verifies existing bytes and adds
        # only absent assets. No tag moves or --clobber writes are permitted.
        uploaded = api.pages(f"repos/{repository}/releases/{release['id']}/assets?per_page=100")
        require({asset["name"] for asset in uploaded} == hashes.keys() and all(asset.get("digest") == "sha256:" + hashes[asset["name"]] for asset in uploaded), "uploaded Python assets are incomplete or corrupt")
        write(f"repos/{repository}/releases/{release['id']}", {"draft": False, "make_latest": "false"}, method="PATCH")
    print(f"Published {tag} at {sha}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "publish"))
    parser.add_argument("--directory", type=Path)
    args = parser.parse_args()
    api = GitHub()
    repository, sha = authorize(api)
    release_version = version("HEAD")
    source, needed = release_state(api, repository, sha, release_version)
    if args.command == "plan":
        result = {"source_sha": source, "release_needed": str(needed).lower()}
        print(json.dumps(result))
        with open(os.environ["GITHUB_OUTPUT"], "a") as stream:
            stream.write("".join(f"{key}={value}\n" for key, value in result.items()))
    elif needed:
        require(args.directory is not None, "Python candidate directory is required")
        publish(api, args.directory, repository, source, release_version)
    else:
        print("Python SDK version already published; no mutation needed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
