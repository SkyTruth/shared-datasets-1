"""Snapshot trusted entrypoints and their local import closure before checkout."""
from __future__ import annotations

import io
from pathlib import Path
import sys
import tarfile

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from scripts.tested_image_bundle import dependency_graph, dependency_closure, repository_files

ENTRYPOINTS = ("scripts/deployment_revision.py", "scripts/terraform_plan_allowlist.py", "scripts/install_deployment_verifier.py")


def snapshot(revision, destination, *, root=Path.cwd()):
    tracked = repository_files(revision, root)
    selected = dependency_closure(dependency_graph(tracked), ENTRYPOINTS)
    selected.update(path for path in tracked if path.startswith((".github/actions/deployment-receipt/", ".github/actions/deployment-auth/", ".github/actions/deployment-bootstrap/verify_cdn.py")))
    selected.add(".github/terraform-targets.json")
    files = {path: tracked[path] for path in selected}
    # Absent trusted initializers must overwrite candidate package injections.
    for path in selected:
        for parent in list(Path(path).parents)[:-1]:
            initializer = str(parent / "__init__.py")
            files[initializer] = tracked.get(initializer, b"")
    with tarfile.open(destination, "w") as archive:
        for path, raw in sorted(files.items()):
            entry = tarfile.TarInfo(path)
            entry.size, entry.mode = len(raw), 0o644
            archive.addfile(entry, io.BytesIO(raw))


if __name__ == "__main__":
    snapshot(sys.argv[1], sys.argv[2])
