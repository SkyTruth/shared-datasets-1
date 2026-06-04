#!/usr/bin/env python3
"""Fail PRs that add datasets or ingestion jobs without admission evidence."""

from __future__ import annotations

import argparse
import math
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

import yaml


FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n?", re.DOTALL)
ASSET_DOC_RE = re.compile(r"^docs/assets/([a-z0-9]+(?:-[a-z0-9]+)*)\.md$")
INGESTION_JOB_FILE_RE = re.compile(r"^ingestion/([^/]+)/(README\.md|Dockerfile|run\.py)$")
PLACEHOLDER_BRACES_RE = re.compile(r"^\{[^{}]*\}$")
FOOTPRINT_RE = re.compile(r"[-+]?\d+(?:\.\d+)?")

MISSING_TEXT_VALUES = {
    "n/a",
    "na",
    "none",
    "tbd",
    "todo",
    "unknown",
}

REQUIRED_TEXT_FIELDS = {
    "citation": "citation",
    "intended_consumers": "admission.intended_consumers",
    "shared_rationale": "admission.shared_rationale",
    "steward": "admission.steward",
    "update_expectations": "admission.update_expectations",
    "alternatives_considered": "admission.alternatives_considered",
    "deprecation_policy": "admission.deprecation_policy",
}


@dataclass(frozen=True)
class ChangedFile:
    status: str
    path: str
    old_path: str = ""


@dataclass(frozen=True)
class AdmissionResult:
    errors: tuple[str, ...]
    added_asset_docs: tuple[str, ...]
    new_ingestion_jobs: tuple[str, ...]


class AdmissionCheckError(ValueError):
    """Raised when admission inputs cannot be parsed."""


def run_git(args: Sequence[str], *, repo_root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo_root,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def git_changed_files(base: str, head: str, *, repo_root: Path) -> list[ChangedFile]:
    result = run_git(["diff", "--name-status", "--find-renames", base, head], repo_root=repo_root)
    if result.returncode != 0:
        raise AdmissionCheckError(f"git diff failed: {result.stderr.strip()}")
    changes: list[ChangedFile] = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        status = parts[0]
        if status.startswith(("R", "C")) and len(parts) >= 3:
            changes.append(ChangedFile(status=status, old_path=parts[1], path=parts[2]))
        elif len(parts) >= 2:
            changes.append(ChangedFile(status=status, path=parts[1]))
    return changes


def git_path_exists(ref: str, path: str, *, repo_root: Path) -> bool:
    result = run_git(["cat-file", "-e", f"{ref}:{path}"], repo_root=repo_root)
    return result.returncode == 0


def split_frontmatter(text: str, path: Path) -> dict[str, Any]:
    match = FRONTMATTER_RE.match(text)
    if not match:
        raise AdmissionCheckError(f"{path}: missing YAML frontmatter")
    payload = yaml.safe_load(match.group(1)) or {}
    if not isinstance(payload, dict):
        raise AdmissionCheckError(f"{path}: frontmatter must be a YAML mapping")
    return payload


def is_missing_text(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, (list, tuple, set)):
        return not value or all(is_missing_text(item) for item in value)
    if isinstance(value, dict):
        return not value

    text = str(value).strip()
    lowered = text.lower()
    if not text:
        return True
    if PLACEHOLDER_BRACES_RE.fullmatch(text):
        return True
    if lowered in MISSING_TEXT_VALUES:
        return True
    if lowered.startswith(("todo", "tbd")):
        return True
    if "needs confirmation" in lowered or "need citation confirmation" in lowered:
        return True
    return False


def parse_footprint_gb(value: Any) -> float | None:
    if is_missing_text(value):
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        footprint = float(value)
    else:
        match = FOOTPRINT_RE.search(str(value))
        if match is None:
            return None
        footprint = float(match.group(0))
    if not math.isfinite(footprint) or footprint < 0:
        return None
    return footprint


def evidence_from_asset_doc(path: Path) -> tuple[dict[str, Any], list[str]]:
    try:
        metadata = split_frontmatter(path.read_text(), path)
    except AdmissionCheckError as exc:
        return {}, [str(exc)]

    admission = metadata.get("admission") or {}
    if not isinstance(admission, dict):
        return {}, [f"{path}: admission must be a YAML mapping"]

    return {
        "citation": metadata.get("citation"),
        "intended_consumers": admission.get("intended_consumers"),
        "shared_rationale": admission.get("shared_rationale"),
        "steward": admission.get("steward"),
        "update_expectations": admission.get("update_expectations"),
        "estimated_published_size_gb": admission.get("estimated_published_size_gb"),
        "large_data_exception": admission.get("large_data_exception"),
        "alternatives_considered": admission.get("alternatives_considered"),
        "deprecation_policy": admission.get("deprecation_policy"),
    }, []


def validate_admission_evidence(evidence: dict[str, Any], *, label: str) -> list[str]:
    errors: list[str] = []
    for key, display in REQUIRED_TEXT_FIELDS.items():
        if is_missing_text(evidence.get(key)):
            errors.append(f"{label}: missing {display}")

    footprint = parse_footprint_gb(evidence.get("estimated_published_size_gb"))
    if footprint is None:
        errors.append(f"{label}: missing numeric admission.estimated_published_size_gb")
    elif footprint >= 10 and is_missing_text(evidence.get("large_data_exception")):
        errors.append(f"{label}: missing admission.large_data_exception for footprint >= 10 GB")

    return errors


def is_asset_doc(path: str) -> bool:
    return ASSET_DOC_RE.fullmatch(path) is not None


def added_asset_docs(changes: Sequence[ChangedFile]) -> list[str]:
    return sorted(change.path for change in changes if change.status == "A" and is_asset_doc(change.path))


def changed_asset_docs(changes: Sequence[ChangedFile]) -> list[str]:
    return sorted(
        change.path
        for change in changes
        if change.status[:1] in {"A", "M", "R", "C"} and is_asset_doc(change.path)
    )


def new_ingestion_jobs(
    changes: Sequence[ChangedFile],
    *,
    base_ref: str,
    repo_root: Path,
    path_exists_at_base: Callable[[str], bool] | None = None,
) -> list[str]:
    exists = path_exists_at_base or (lambda path: git_path_exists(base_ref, path, repo_root=repo_root))
    jobs: set[str] = set()
    for change in changes:
        if change.status != "A":
            continue
        match = INGESTION_JOB_FILE_RE.fullmatch(change.path)
        if not match:
            continue
        job = match.group(1)
        if not exists(f"ingestion/{job}"):
            jobs.add(job)
    return sorted(jobs)


def complete_changed_asset_docs(repo_root: Path, paths: Sequence[str]) -> list[str]:
    complete: list[str] = []
    for path in paths:
        evidence, parse_errors = evidence_from_asset_doc(repo_root / path)
        if parse_errors:
            continue
        if not validate_admission_evidence(evidence, label=path):
            complete.append(path)
    return complete


def check_admission(
    *,
    repo_root: Path,
    changes: Sequence[ChangedFile],
    base_ref: str,
    path_exists_at_base: Callable[[str], bool] | None = None,
) -> AdmissionResult:
    errors: list[str] = []
    added_docs = added_asset_docs(changes)

    for path in added_docs:
        evidence, parse_errors = evidence_from_asset_doc(repo_root / path)
        errors.extend(parse_errors)
        if not parse_errors:
            errors.extend(validate_admission_evidence(evidence, label=path))

    jobs = new_ingestion_jobs(
        changes,
        base_ref=base_ref,
        repo_root=repo_root,
        path_exists_at_base=path_exists_at_base,
    )
    if jobs:
        changed_docs = changed_asset_docs(changes)
        if not complete_changed_asset_docs(repo_root, changed_docs):
            errors.append(
                "new ingestion pipeline(s) require complete admission evidence in changed asset-doc frontmatter: "
                + ", ".join(jobs)
            )

    return AdmissionResult(
        errors=tuple(errors),
        added_asset_docs=tuple(added_docs),
        new_ingestion_jobs=tuple(jobs),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, help="Base git ref or SHA.")
    parser.add_argument("--head", required=True, help="Head git ref or SHA.")
    parser.add_argument("--repo-root", type=Path, default=Path("."), help="Repository root.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    repo_root = args.repo_root.resolve()
    try:
        changes = git_changed_files(args.base, args.head, repo_root=repo_root)
        result = check_admission(
            repo_root=repo_root,
            changes=changes,
            base_ref=args.base,
        )
    except (AdmissionCheckError, OSError) as exc:
        print(f"admission-check: {exc}", file=sys.stderr)
        return 1

    if result.errors:
        for error in result.errors:
            print(f"error: {error}", file=sys.stderr)
        return 1

    print(
        "admission check passed "
        f"({len(result.added_asset_docs)} new asset doc(s), "
        f"{len(result.new_ingestion_jobs)} new ingestion pipeline(s))"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
