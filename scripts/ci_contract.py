"""The validation contract shared by local preflight and GitHub Actions.

Suite selection is deliberately conservative: a path without a known dependency
rule selects every suite. Results are evidence for an exact tree and contract,
not permission to publish or deploy.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from xml.etree import ElementTree

from scripts.check_geospatial_test_results import REQUIRED_TESTS


TOOLCHAIN = {
    "python": "3.12.12",
    "uv": "0.11.8",
    "node22": "22.12.0",
    "node24": "24.13.1",
    "terraform": "1.8.5",
    "gitleaks": "8.30.1",
    "actionlint": "1.7.12",
}
SUITES = (
    "lint", "tests", "geospatial-integration", "sdk-node22", "sdk-node24", "browser",
)
ALWAYS = {"lint", "tests"}
NATIVE_TESTS = (
    "tests/test_feature_metadata_localization.py",
    "tests/test_translation_local_io.py",
    "tests/test_raster_standards.py",
    "tests/test_wdpa_monthly.py",
    "tests/test_wdpa_disk_processing.py",
    "tests/test_sea_ice_daily.py",
    "tests/test_eamlis_monthly.py",
)
CONTRACT_FILES = (
    "scripts/ci_contract.py", "scripts/ci_preflight.py", "scripts/ci_install_tools.py",
    ".github/docker/preflight.Dockerfile", ".github/docker/geospatial-ci.Dockerfile",
    "pyproject.toml", "uv.lock", "api/typescript/package-lock.json",
    "tests/browser/package-lock.json", "scripts/check_geospatial_test_results.py",
    "scripts/check_workflow_syntax.py",
    ".github/actions/ci-tools/action.yml", ".github/workflows/ci.yml",
)


def select_suites(paths: list[str] | None) -> tuple[list[str], str]:
    if paths is None:
        return list(SUITES), "comparison history unavailable"
    selected = set(ALWAYS)
    for path in paths:
        if path in {"pyproject.toml", "uv.lock"} or path.startswith(".github/"):
            return list(SUITES), f"shared validation or workflow dependency: {path}"
        if path.startswith("ingestion/") or path in NATIVE_TESTS:
            selected.add("geospatial-integration")
        elif path.startswith("api/"):
            selected.update({"sdk-node22", "sdk-node24", "browser"})
        elif path.startswith(("web/", "tests/browser/", "catalog/", "templates/", "docs/assets/")):
            selected.update({"sdk-node22", "sdk-node24", "browser"})
        elif path.startswith("scripts/"):
            # Scripts are imported by runtime, SDK fixtures, and browser rendering.
            # An explicit narrower rule must prove those dependencies absent.
            return list(SUITES), f"shared script dependency: {path}"
        elif path.startswith("tests/"):
            if any(word in path for word in ("geospatial", "raster", "wdpa", "sea_ice", "eamlis", "translation")):
                selected.add("geospatial-integration")
            elif any(word in path for word in ("typescript", "sdk", "snapshot", "catalog", "web", "preview")):
                selected.update({"sdk-node22", "sdk-node24", "browser"})
            else:
                return list(SUITES), f"test dependency is not classified: {path}"
        elif path.startswith(("terraform/", "docs/", ".claude/skills/", ".agents/")):
            pass
        elif path in {"README.md", "AGENTS.md", "CLAUDE.md", "LICENSE", ".gitignore", ".gitleaksignore"}:
            pass
        else:
            return list(SUITES), f"unknown path: {path}"
    return [suite for suite in SUITES if suite in selected], "complete path classification"


def contract_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for filename in CONTRACT_FILES:
        digest.update(filename.encode() + b"\0" + (root / filename).read_bytes() + b"\0")
    return digest.hexdigest()


def check_junit(report: Path, *, native: bool) -> None:
    cases = list(ElementTree.parse(report).iter("testcase"))
    if not cases:
        raise ValueError("test collection is empty")
    allowed_skips = set() if native else set(REQUIRED_TESTS)
    for case in cases:
        identity = (case.get("classname"), case.get("name"))
        if case.find("failure") is not None or case.find("error") is not None:
            raise ValueError(f"failed test: {'.'.join(identity)}")
        if case.find("skipped") is not None and identity not in allowed_skips:
            raise ValueError(f"unexpected skipped test: {'.'.join(identity)}")


def verify_results(plan: dict, results: list[dict], jobs: dict | None = None) -> None:
    if plan.get("schema_version") != 1 or not plan.get("suites"):
        raise ValueError("invalid validation plan")
    selected = plan["suites"]
    if len(set(selected)) != len(selected) or not set(selected) <= set(SUITES):
        raise ValueError("invalid selected suites")
    if not ALWAYS <= set(selected):
        raise ValueError("core validation suites must always be selected")
    if ("sdk-node22" in selected) != ("sdk-node24" in selected):
        raise ValueError("both SDK runtime suites must be selected together")
    by_suite: dict[str, dict] = {}
    for result in results:
        suite = result.get("suite")
        if suite in by_suite or suite not in selected:
            raise ValueError(f"duplicate or unexpected suite result: {suite}")
        by_suite[suite] = result
    if set(by_suite) != set(selected):
        raise ValueError(f"missing suite results: {sorted(set(selected) - set(by_suite))}")
    identity = ("base", "head", "tested_sha", "tree", "contract_digest")
    for suite, result in by_suite.items():
        if result.get("status") != "success" or not result.get("commands"):
            raise ValueError(f"{suite} did not execute successfully")
        if any(result.get(field) != plan.get(field) for field in identity):
            raise ValueError(f"stale or mismatched {suite} evidence")
        if any(command.get("exit_code") != 0 for command in result["commands"]):
            raise ValueError(f"{suite} contains a failed command")
        if result.get("tools") != expected_tools(suite):
            raise ValueError(f"{suite} used an unexpected toolchain")
    if jobs is not None:
        expected = {"lint": "lint", "tests": "tests", "geospatial-integration": "geospatial-integration", "browser": "browser"}
        for suite, job in expected.items():
            required = "success" if suite in selected else "skipped"
            if jobs.get(job, {}).get("result") != required:
                raise ValueError(f"job {job}: expected {required}, got {jobs.get(job, {}).get('result')}")
        sdk_required = "success" if "sdk-node22" in selected else "skipped"
        if jobs.get("sdk-validation", {}).get("result") != sdk_required:
            raise ValueError("SDK matrix failed, was cancelled, missing, or unexpectedly skipped")
        if jobs.get("geospatial-changes", {}).get("result") != "success":
            raise ValueError("validation selection did not succeed")


def expected_tools(suite: str) -> dict[str, str]:
    tools = {"python": TOOLCHAIN["python"], "uv": TOOLCHAIN["uv"]}
    if suite == "lint":
        tools.update({key: TOOLCHAIN[key] for key in ("terraform", "actionlint")})
    elif suite == "tests":
        tools.update({"node": TOOLCHAIN["node22"], "gitleaks": TOOLCHAIN["gitleaks"]})
    elif suite in {"sdk-node22", "sdk-node24", "browser"}:
        tools["node"] = TOOLCHAIN["node24" if suite == "sdk-node24" else "node22"]
    elif suite == "geospatial-integration":
        # Native executable package versions are pinned by the Dockerfile and
        # checked with real version probes in the native command log.
        tools["gdal"] = "3.6.2"
        tools["tippecanoe"] = "2.52.0"
        tools["pmtiles"] = "1.30.1"
    else:
        raise ValueError(f"unknown suite: {suite}")
    return tools


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
