#!/usr/bin/env python3
"""Version gate and clean installed-package proof for Python SDK releases."""
from __future__ import annotations

import argparse
import ast
import copy
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import tarfile
import tempfile
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SDK = "api/python"
VERSION_FILE = SDK + "/src/skytruth_shared_datasets/_version.py"


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def version(ref):
    project = tomllib.loads(git("show", f"{ref}:{SDK}/pyproject.toml"))["project"]
    if "version" in project:  # Comparison baselines predate the single source.
        value = project["version"]
    else:
        tree = ast.parse(git("show", f"{ref}:{VERSION_FILE}"))
        if len(tree.body) != 1 or not isinstance(tree.body[0], ast.Assign) or len(tree.body[0].targets) != 1 or not isinstance(tree.body[0].targets[0], ast.Name) or tree.body[0].targets[0].id != "__version__":
            raise ValueError("SDK version must be one literal __version__ assignment")
        value = ast.literal_eval(tree.body[0].value)
    if not isinstance(value, str) or not re.fullmatch(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", value):
        raise ValueError("SDK version must be a stable major.minor.patch version")
    return value


def version_change(before, after, changed):
    needed = any(path.startswith(SDK + "/") for path in changed)
    if needed and tuple(map(int, after.split("."))) <= tuple(map(int, before.split("."))):
        raise ValueError("Python SDK package changed without a version increase")
    return needed


def source_revision(source):
    """Keep an existing tag's package identity across unrelated main merges."""
    if not isinstance(source, str) or not re.fullmatch(r"[0-9a-f]{40}", source):
        raise ValueError("SDK source must be a complete commit SHA")
    if subprocess.run(["git", "merge-base", "--is-ancestor", source, "HEAD"], cwd=ROOT, check=False).returncode:
        raise ValueError("SDK source is not an ancestor of the accepted checkout")
    if git("rev-parse", f"{source}:{SDK}") != git("rev-parse", f"HEAD:{SDK}"):
        raise ValueError("SDK tag source differs from the accepted package tree")
    return source


def normalize_sdist(path, epoch):
    """Canonical archive metadata; retain every original member's contents."""
    result = io.BytesIO()
    with tarfile.open(path, "r:gz") as original:
        with gzip.GzipFile(fileobj=result, mode="wb", filename="", mtime=int(epoch)) as compressed:
            with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as normalized:
                for member in sorted(original.getmembers(), key=lambda item: item.name):
                    if not (member.isfile() or member.isdir()):
                        raise ValueError("SDK source archive contains a non-file/directory member")
                    item = copy.copy(member)
                    item.uid = item.gid = 0
                    item.uname = item.gname = ""
                    item.mtime = int(epoch)
                    item.mode = 0o755 if item.isdir() else 0o644
                    item.pax_headers = {}
                    normalized.addfile(item, original.extractfile(member) if member.isfile() else None)
    path.write_bytes(result.getvalue())


def run(*args, cwd=ROOT, env=None):
    subprocess.run(list(map(str, args)), cwd=cwd, env=env, check=True)


def wheel_contents(path):
    with zipfile.ZipFile(path) as archive:
        if len(archive.namelist()) != len(set(archive.namelist())):
            raise ValueError("duplicate wheel members")
        return {name: archive.read(name) for name in archive.namelist()}


def build(output, interpreters, source_sha=None):
    output.mkdir(parents=True, exist_ok=True)
    if list(output.iterdir()):
        raise ValueError("package output directory must be empty")
    clean = not git("status", "--porcelain")
    revision = source_revision(git("rev-parse", "HEAD") if source_sha is None else source_sha)
    source = revision if clean else None
    epoch = git("show", "-s", "--format=%ct", revision)
    env = {**os.environ, "SOURCE_DATE_EPOCH": epoch}
    constraints = ROOT / SDK / "build-constraints.txt"
    command = ("uv", "build", "--no-sources", "--no-create-gitignore", "--build-constraints", constraints, "--require-hashes")
    run(*command, ROOT / SDK, "--out-dir", output, env=env)
    wheel, = output.glob("*.whl")
    sdist, = output.glob("*.tar.gz")
    normalize_sdist(sdist, epoch)
    package = wheel_contents(wheel)
    with tempfile.TemporaryDirectory(prefix="python-sdk-check-", dir=output.parent) as scratch:
        work = Path(scratch)
        run(*command, sdist, "--wheel", "--out-dir", work / "rebuilt", env=env)
        rebuilt, = (work / "rebuilt").glob("*.whl")
        if wheel_contents(rebuilt) != package:
            raise ValueError("source archive does not reproduce the wheel contents")
        for index, interpreter in enumerate(interpreters):
            install = work / f"installed-{index}"
            run("uv", "venv", "--no-project", "--python", interpreter, install, cwd=work)
            python = install / "bin/python"
            run("uv", "pip", "install", "--python", python, "--no-deps", "--no-index", wheel)
            # -I plus a cwd outside the checkout excludes source/PYTHONPATH imports.
            run(python, "-I", "-c", "\n".join((
                "import importlib.metadata as m, pathlib, sys",
                "import skytruth_shared_datasets as sdk",
                "assert sdk.__version__ == m.version('skytruth-shared-datasets')",
                "assert pathlib.Path(sdk.__file__).resolve().is_relative_to(pathlib.Path(sys.prefix))",
                "assert pathlib.Path(sdk.__file__).with_name('py.typed').is_file()",
                "catalog = sdk.Catalog.from_csv_text('asset_slug,title,canonical_path,canonical_format,available_formats,access_tier\\nexample,Example,gs://example/a.csv,csv,csv,public\\n')",
                "assert catalog.resolve('example', access='public').gs_uri == 'gs://example/a.csv'",
            )), cwd=work)
            run(install / "bin/skytruth-datasets", "--help", cwd=work)
    match = re.fullmatch(r"skytruth_shared_datasets-([0-9]+\.[0-9]+\.[0-9]+)-py3-none-any.whl", wheel.name)
    if not match:
        raise ValueError("unexpected SDK wheel filename")
    hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in (wheel, sdist)}
    (output / "provenance.json").write_text(json.dumps({"schema_version": 1, "version": match[1], "source_sha": source,
        "source_epoch": int(epoch), "files": hashes, "installed_python": interpreters}, sort_keys=True, indent=2) + "\n")
    (output / "SHA256SUMS").write_text("".join(f"{digest}  {name}\n" for name, digest in sorted(hashes.items())))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check")
    check.add_argument("--base", required=True)
    check.add_argument("--head", required=True)
    builder = commands.add_parser("build")
    builder.add_argument("--output", type=Path, required=True)
    builder.add_argument("--python", action="append", required=True)
    builder.add_argument("--source-sha", help="Existing immutable tag commit with an identical SDK tree")
    args = parser.parse_args()
    if args.command == "build":
        build(args.output.resolve(), args.python, args.source_sha)
    else:
        changed = git("diff", "--no-renames", "--name-only", args.base, args.head).splitlines()
        # Lightweight docs/fixture checkouts need not contain the SDK at all.
        needed = any(path.startswith(SDK + "/") for path in changed)
        if needed:
            version_change(version(args.base), version(args.head), changed)
        result = {"package_changed": str(needed).lower()}
        print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
