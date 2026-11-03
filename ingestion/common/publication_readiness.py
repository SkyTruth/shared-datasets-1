"""Complete, retained decision discovery shared by dataset publication flows.

Analysis is local evidence, never approval or allocation authority. Producers
declare the complete batch and supply normalized inputs using their real reader.
"""

from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile
from typing import Callable

from ingestion.common.identity_index import DiskIdentityPlan
from ingestion.common.runtime import sha256_file
from scripts import release_feature_model as model

SCHEMA = "publication-readiness-v1"


def engine_digest():
    root = Path(__file__).resolve().parents[2]
    paths = sorted((root / "ingestion/common").glob("*.py"))
    paths.append(root / "scripts/release_feature_model.py")
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.relative_to(root).as_posix().encode() + b"\0" + path.read_bytes())
    return digest.hexdigest()


@dataclass(frozen=True)
class Target:
    asset_slug: str
    release: str
    binding: dict
    prepare: Callable[[Path], dict]
    baseline: model.GeneratedIdentityBaseline | None = None
    decisions: tuple = ()
    match_properties: bool = True
    blockers: tuple[str, ...] = ()


def write_json(path, value):
    path.write_text(model.canonical_json(value) + "\n", encoding="utf-8")


def verify_retained(directory, binding):
    manifest = json.loads((directory / "analysis.json").read_text())
    if manifest["schema"] != SCHEMA or manifest["binding"] != binding:
        raise ValueError("retained analysis input or processing version changed")
    files = manifest["files"]
    actual = {p.relative_to(directory).as_posix() for p in directory.rglob("*") if p.is_file()}
    if actual != set(files) | {"analysis.json"}:
        raise ValueError("retained analysis file set changed")
    for name, digest in files.items():
        path = directory / name
        if path.is_symlink() or not path.resolve().is_relative_to(directory.resolve()):
            raise ValueError("retained analysis path escapes its directory")
        if sha256_file(path) != digest:
            raise ValueError("retained analysis checksum changed: " + name)
    return manifest


def stage_retained(report, upload):
    """Retain exact analysis bytes through a producer's create-only scratch stager."""
    references = {}
    for slug, result in report["assets"].items():
        if not result.get("analysis_complete"):
            continue
        directory = Path(result["analysis"])
        manifest = json.loads((directory / "analysis.json").read_text())
        verify_retained(directory, manifest["binding"])
        files = {}
        for name in (*manifest["files"], "analysis.json"):
            files[name] = upload(f"{slug}/analysis-{name}", directory / name)
        references[slug] = {"binding": manifest["binding"], "files": files}
    return {"schema": SCHEMA, "expected_assets": report["expected_assets"],
            "discovery_state": report["state"], "assets": references}


def restore_retained(reference, directory, download):
    """Restore pinned scratch objects; input and engine checks still run on reuse."""
    if reference["schema"] != SCHEMA:
        raise ValueError("unsupported retained analysis reference")
    directory.mkdir(parents=True, exist_ok=True)
    for slug, item in reference["assets"].items():
        binding = item["binding"]
        if binding["asset_slug"] != slug or binding["engine"] != engine_digest():
            raise ValueError("retained analysis asset or engine changed")
        key = hashlib.sha256(model.canonical_json(binding).encode()).hexdigest()
        parent = directory / slug
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug):
            raise ValueError("invalid retained asset slug")
        parent.mkdir(parents=True, exist_ok=True)
        destination = parent / key
        if destination.exists():
            verify_retained(destination, binding)
            continue
        with tempfile.TemporaryDirectory(prefix="restoring-", dir=parent) as temp:
            staging = Path(temp) / "complete"
            staging.mkdir()
            for name, ref in item["files"].items():
                path = staging / name
                if not path.resolve().is_relative_to(staging.resolve()):
                    raise ValueError("retained analysis path escapes its directory")
                path.parent.mkdir(parents=True, exist_ok=True)
                download(ref, path)
                if sha256_file(path) != ref["sha256"] or path.stat().st_size != ref["size"]:
                    raise ValueError("restored analysis checksum or size changed")
            verify_retained(staging, binding)
            staging.rename(destination)


def discover_batch(targets, directory, *, on_pending=None):
    """Analyze every declared target before returning any decision gate.

    Cache keys exclude decisions so reviewed curation can reuse normalization.
    Source/baseline pins, producer versions and exact selection belong in binding.
    Incomplete cache entries are never reused or presented as complete evidence.
    """
    targets = tuple(targets)
    names = [target.asset_slug for target in targets]
    if not names or len(set(names)) != len(names):
        raise ValueError("readiness requires a nonempty batch of unique asset slugs")
    directory.mkdir(parents=True, exist_ok=True)
    report = {"schema": SCHEMA, "expected_assets": names, "assets": {}, "state": "INCOMPLETE"}
    write_json(directory / "readiness.json", report)
    for target in targets:
        slug = target.asset_slug
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug):
            raise ValueError("invalid discovery asset slug")
        binding = {"asset_slug": slug, "release": target.release, "inputs": target.binding,
                   "engine": engine_digest(), "match_properties": target.match_properties}
        key = hashlib.sha256(model.canonical_json(binding).encode()).hexdigest()
        retained = directory / slug / key
        result = {"state": "INCOMPLETE", "analysis": str(retained), "unresolved": 0,
                  "blockers": list(target.blockers), "reused": retained.exists()}
        report["assets"][slug] = result
        try:
            if not retained.exists():
                retained.parent.mkdir(parents=True, exist_ok=True)
                # Commit the directory only after its entire preparation succeeds.
                with tempfile.TemporaryDirectory(prefix="preparing-", dir=retained.parent) as temp:
                    staging = Path(temp) / "complete"
                    staging.mkdir()
                    payload = target.prepare(staging)
                    if target.baseline is not None:
                        with closing(DiskIdentityPlan.open(staging / "identity.sqlite")) as plan:
                            plan.discover(baseline=target.baseline, match_properties=target.match_properties)
                    manifest = {"schema": SCHEMA, "binding": binding, "payload": payload,
                                "files": {p.relative_to(staging).as_posix(): sha256_file(p)
                                          for p in staging.rglob("*") if p.is_file()}}
                    write_json(staging / "analysis.json", manifest)
                    staging.rename(retained)
            manifest = verify_retained(retained, binding)
            result["analysis_complete"] = True
            result["payload"] = manifest["payload"]
            result["analysis_sha256"] = sha256_file(retained / "analysis.json")
            if target.baseline is not None:
                # Review mutates only a disposable copy; retained matches stay immutable.
                with tempfile.TemporaryDirectory(prefix="review-", dir=directory) as temp:
                    working = Path(temp) / "identity.sqlite"
                    shutil.copyfile(retained / "identity.sqlite", working)
                    with closing(DiskIdentityPlan.open(working)) as plan:
                        _, unresolved = plan.review(baseline=target.baseline, release=target.release,
                            decisions=target.decisions, match_properties=target.match_properties)
                        result["unresolved"] = len(unresolved)
                        cases = directory / f"{slug}.cases.ndjson"
                        with cases.open("w", encoding="utf-8") as stream:
                            for case in unresolved:
                                stream.write(model.canonical_json(model.identity_ambiguity_to_dict(case)) + "\n")
                        result["cases"] = str(cases)
                        if unresolved and on_pending is not None:
                            on_pending(target, plan, retained, result)
            result["state"] = ("BLOCKED" if result["blockers"] else
                               "NEEDS_DECISION" if result["unresolved"] else "READY")
        except (ValueError, RuntimeError, OSError) as exc:
            result["state"] = "INCOMPLETE"
            result["error"] = str(exc)
        write_json(directory / "readiness.json", report)
    states = {item["state"] for item in report["assets"].values()}
    report["state"] = next(state for state in ("INCOMPLETE", "BLOCKED", "NEEDS_DECISION", "READY") if state in states)
    write_json(directory / "readiness.json", report)
    return report


def require_ready(report, *, notify=False):
    """A decision pause is distinct from invalid or incomplete discovery."""
    if report["state"] == "READY":
        return
    if report["state"] != "NEEDS_DECISION":
        raise RuntimeError("publication discovery did not complete: " + model.canonical_json(report))
    from ingestion.common.feature_metadata import IdentityDecisionRequired
    blocked = []
    for slug, item in report["assets"].items():
        if not item["unresolved"]:
            continue
        manifest = json.loads((Path(item["analysis"]) / "analysis.json").read_text())
        release = manifest["binding"]["release"]
        if notify:
            from ingestion.common.feature_metadata import identity_decision_required
            with Path(item["cases"]).open() as stream:
                cases = tuple(model.IdentityAmbiguity(
                    ambiguity_type=raw["ambiguity_type"], identity_key=tuple(raw["new_identity_key"]),
                    geometry_hash=raw["new_geometry_hash"], properties_hash=raw["new_properties_hash"],
                    matching_geometry_feature_ids=tuple(raw["matching_geometry_feature_ids"]),
                    matching_properties_feature_ids=tuple(raw["matching_properties_feature_ids"]),
                    matching_geometry_properties_hashes=tuple(raw["matching_geometry_properties_hashes"]),
                    matching_properties_geometry_hashes=tuple(raw["matching_properties_geometry_hashes"]),
                ) for raw in (json.loads(line) for line in stream))
            blocked.append(identity_decision_required(asset_slug=slug, release=release, ambiguities=cases))
        else:
            blocked.append(IdentityDecisionRequired("complete batch awaits reviewed decisions",
                asset_slug=slug, release=release, ambiguity_count=item["unresolved"]))
    error = blocked[0]
    error.blocked_assets = blocked
    error.readiness_report = report
    raise error


def validate_ready_report(path, *, asset_slug, source_sha256, release=None):
    """Revalidate retained evidence at a manual workflow's build/PR boundary."""
    report = json.loads(path.read_text())
    if (report.get("schema") != SCHEMA or report.get("state") != "READY"
            or set(report.get("expected_assets", ())) != set(report.get("assets", {}))
            or asset_slug not in report.get("assets", {})):
        raise ValueError("complete READY publication report required")
    for slug, item in report["assets"].items():
        if item["state"] != "READY" or item["unresolved"] or item["blockers"]:
            raise ValueError("every declared asset must be READY")
        directory = Path(item["analysis"])
        if sha256_file(directory / "analysis.json") != item["analysis_sha256"]:
            raise ValueError("retained analysis manifest changed")
        manifest = json.loads((directory / "analysis.json").read_text())
        binding = manifest["binding"]
        if binding["asset_slug"] != slug or binding["engine"] != engine_digest():
            raise ValueError("retained analysis asset or processing version changed")
        verify_retained(directory, binding)
        inputs = binding["inputs"]
        if "source" in inputs:
            from ingestion.common.publication_sources import input_digest
            if input_digest(Path(inputs["source"])) != inputs["source_sha256"]:
                raise ValueError("publication source changed since discovery")
        if slug == asset_slug:
            if inputs.get("original_source_sha256", inputs.get("source_sha256")) != source_sha256:
                raise ValueError("publication source changed since discovery")
            if release and binding["release"] != release:
                raise ValueError("publication release changed since discovery")
    return report
