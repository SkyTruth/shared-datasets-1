"""Snapshot trusted entrypoints and their local import closure before checkout."""
from __future__ import annotations

import ast
import io
from pathlib import Path
import subprocess
import sys
import tarfile

ENTRYPOINTS = ("scripts/deployment_revision.py", "scripts/terraform_plan_allowlist.py", "scripts/install_deployment_verifier.py")


def snapshot(revision, destination, *, root=Path.cwd()):
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=root)
    tracked = set(git("ls-tree", "-r", "--name-only", revision).decode().splitlines())
    pending = list(ENTRYPOINTS)
    files = {".github/terraform-targets.json": git("show", revision + ":.github/terraform-targets.json")}
    for path in tracked:
        if path.startswith((".github/actions/deployment-receipt/", ".github/actions/deployment-auth/")):
            files[path] = git("show", revision + ":" + path)
    while pending:
        path = pending.pop()
        if path in files:
            continue
        raw = git("show", revision + ":" + path)
        files[path] = raw
        # Restore package initializers too; an absent trusted initializer becomes
        # empty so a candidate cannot inject code into a namespace package.
        for parent in Path(path).parents:
            if str(parent) != ".":
                initializer = str(parent / "__init__.py")
                if initializer in tracked:
                    pending.append(initializer)
                else:
                    files[initializer] = b""
        for node in ast.walk(ast.parse(raw)):
            modules = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    base = ".".join((*Path(path).parts[:-node.level], *base.split("."))).strip(".")
                modules = [base, *(base + "." + alias.name for alias in node.names)]
            for module in modules:
                local = module.replace(".", "/")
                for candidate in (local + ".py", local + "/__init__.py"):
                    if candidate in tracked:
                        pending.append(candidate)
    with tarfile.open(destination, "w") as archive:
        for path, raw in sorted(files.items()):
            entry = tarfile.TarInfo(path)
            entry.size, entry.mode = len(raw), 0o644
            archive.addfile(entry, io.BytesIO(raw))


if __name__ == "__main__":
    snapshot(sys.argv[1], sys.argv[2])
