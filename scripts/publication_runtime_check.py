#!/usr/bin/env python3
"""Fast, credential-free publication boundary probes before expensive CI."""

from __future__ import annotations

from contextlib import closing
from dataclasses import replace
import json
import ast
import os
from pathlib import Path
import subprocess
import shutil
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ingestion.common import publication_readiness as readiness
from ingestion.common.identity_index import DiskIdentityPlan
from scripts import release_feature_model as model
from scripts.check_geospatial_test_results import REQUIRED_TESTS


def check_native_registration(root):
    """Catch unregistered GDAL fixtures before the non-native suite skips them."""
    for path in sorted((root / "tests").glob("test_*.py")):
        source = path.read_text()
        if "importorskip" not in source:
            continue
        tree = ast.parse(source)
        functions = [(node, f"tests.{path.stem}") for node in tree.body if isinstance(node, ast.FunctionDef)]
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                functions.extend((method, f"tests.{path.stem}.{node.name}")
                    for method in node.body if isinstance(method, ast.FunctionDef))
        for function, classname in functions:
            if not function.name.startswith("test_"):
                continue
            native = any(isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                and call.func.attr == "importorskip" and call.args
                and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str)
                and call.args[0].value.startswith("osgeo") for call in ast.walk(function))
            if native and (classname, function.name) not in REQUIRED_TESTS:
                raise RuntimeError(f"Native fixture is missing required CI registration: {classname}.{function.name}")


def check(directory):
    """Exercise full discovery, persistence, decision application and allocation."""
    geometry = {"type": "Point", "coordinates": [1, 2]}
    old = {"type": "Feature", "geometry": geometry, "properties": {"key": "old"}}
    incoming = {**old, "properties": {"key": "new"}}
    hashes = model.content_hashes(geometry=geometry, properties=old["properties"])
    baseline = model.GeneratedIdentityBaseline(({
        "feature_id": "1", "identity_key": ("old",),
        "geometry_hash": hashes[0], "properties_hash": hashes[1],
    },), 2, "2026-01-01", contract_id="runtime-probe-v1")
    preparations = []
    def prepare(path):
        preparations.append(path)
        with closing(DiskIdentityPlan(path / "identity.sqlite")) as plan:
            plan.add(1, incoming, source_fields=("key",))
            plan.db.commit()
        return {"rows": 1}
    targets = [readiness.Target(slug, "2026-02-01", {"source_sha256": "a" * 64}, prepare,
        baseline=baseline) for slug in ("probe-a", "probe-b")]
    pending = readiness.discover_batch(targets, directory)
    if pending["state"] != "NEEDS_DECISION" or [v["unresolved"] for v in pending["assets"].values()] != [1, 1]:
        raise RuntimeError("complete batch discovery did not retain both decision sets")
    accepted = []
    for target in targets:
        item = pending["assets"][target.asset_slug]
        case = json.loads(Path(item["cases"]).read_text())
        case.pop("ambiguity_type")
        decision = {**case, "release": target.release, "action": "assign_new_feature_id",
                    "rationale": "Synthetic negative-control fixture; never publication approval",
                    "reviewer": "runtime-probe", "pr_reference": "runtime-probe"}
        accepted.append(replace(target, decisions=(decision,)))
    ready = readiness.discover_batch(accepted, directory)
    if ready["state"] != "READY" or len(preparations) != 2:
        raise RuntimeError("reviewed decisions did not reuse immutable normalized analysis")
    # Run through the persisted CLI report boundary, not an in-memory assertion alone.
    stored = json.loads((directory / "readiness.json").read_text())
    readiness.require_ready(stored)
    for target in accepted:
        source = Path(ready["assets"][target.asset_slug]["analysis"]) / "identity.sqlite"
        destination = directory / f"{target.asset_slug}.allocation.sqlite"
        shutil.copyfile(source, destination)
        with closing(DiskIdentityPlan.open(destination)) as plan:
            next_id, _ = plan.resolve(baseline=baseline, asset_slug=target.asset_slug,
                release=target.release, decisions=target.decisions)
            if next_id != 3 or plan.record(1)["feature_id"] != "2":
                raise RuntimeError("complete allocation diverged from retained discovery")
    return {"state": "READY", "assets": 2, "preparations": len(preparations)}


def main():
    work_root = Path(os.environ.get("SHARED_DATASETS_WORKDIR", Path(tempfile.gettempdir()) / "shared-datasets-1"))
    work_root = work_root / "_scratch"
    work_root.mkdir(parents=True, exist_ok=True)
    # An explicit child-process timeout bounds this gate before Docker/CI fanout.
    if "--worker" in sys.argv:
        check_native_registration(Path(__file__).resolve().parents[1])
        with tempfile.TemporaryDirectory(prefix="publication-runtime-", dir=work_root) as temp:
            print(json.dumps(check(Path(temp)), sort_keys=True))
    else:
        subprocess.run([sys.executable, __file__, "--worker"], check=True, timeout=30)


if __name__ == "__main__":
    main()
