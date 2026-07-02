#!/usr/bin/env python3
"""Repo-level guardrails for shared-datasets safety contracts."""

from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = "catalog/shared-datasets-catalog.csv"
ASSET_DOC_RE = re.compile(r"^docs/assets/([a-z0-9]+(?:-[a-z0-9]+)*)\.md$")
STATUS_RENAME_COPY_RE = re.compile(r"^[RC]")

APPROVAL_LABELS = {
    "taxonomy": {"approved-taxonomy-change", "approved-category-change"},
    "canonical_format": {"approved-canonical-format-change"},
    "iac_framework": {"approved-iac-framework-change", "approved-second-iac-framework"},
}

FORMAT_CONSTANTS = {
    "scripts/catalog_docs.py": ("APPROVED_CANONICAL_FORMATS",),
    "scripts/catalog_site.py": ("APPROVED_FORMATS",),
    "scripts/publish_release.py": ("SINGLE_OBJECT_FORMATS",),
    "scripts/gcs_asset.py": ("APPROVED_DATA_EXTENSIONS",),
}

SECOND_IAC_PATH_RE = re.compile(
    r"(^|/)(Pulumi\.(ya?ml|json)|cdk\.json|cdktf\.json|terragrunt\.hcl)$"
    r"|(^|/)(pulumi|terragrunt|cdktf\.out|cdk\.out)/",
    re.IGNORECASE,
)
SECOND_IAC_CONTENT_RE = re.compile(
    r"(@pulumi/|aws-cdk-lib|constructs\"|constructs'|from\s+pulumi\s+import|"
    r"import\s+pulumi|terraform-aws-modules/.*/aws|cdktf)",
    re.IGNORECASE,
)

SECRET_FILE_RE = re.compile(
    r"(^|/)\.env($|[.])|(^|/)(service-account|credentials?|secrets?)\.(json|ya?ml|env)$",
    re.IGNORECASE,
)
SECRET_FILE_ALLOW_RE = re.compile(r"(\.example|\.sample|template|README|\.md$)", re.IGNORECASE)
PRIVATE_KEY_MARKER_RE = "-" * 5 + r"BEGIN [A-Z ]*PRIVATE KEY" + "-" * 5
SERVICE_ACCOUNT_PRIVATE_KEY_RE = r'"private_key"\s*:\s*"' + "-" * 5 + r"BEGIN PRIVATE KEY" + "-" * 5
SECRET_PATTERNS = (
    ("private key", re.compile(PRIVATE_KEY_MARKER_RE)),
    ("Google OAuth access token", re.compile(r"\bya29\.[0-9A-Za-z_-]{20,}")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{30,}")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{30,}")),
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("Slack webhook", re.compile(r"https://hooks\.slack\.com/services/[A-Za-z0-9/_-]+")),
    ("GCS signed URL", re.compile(r"X-Goog-(Algorithm|Credential|Signature)=")),
    ("service account private key JSON", re.compile(SERVICE_ACCOUNT_PRIVATE_KEY_RE)),
)

INGESTION_JOB_EXCLUDES = {"common", "__pycache__"}
GCS_DELETE_PATTERNS = (
    re.compile(r"\b(?:blob|bucket|client)\.(?:delete|delete_blob|delete_blobs)\s*\("),
    re.compile(r"\bstorage\.objects\.delete\b"),
    re.compile(r"\bgcloud\s+storage\s+rm\b"),
)

TERRAFORM_FORBIDDEN_PATTERNS = (
    ("Cloud Storage object ACL workflow", re.compile(r"google_storage_.*acl|predefined_acl|object_access_control")),
    ("Terraform-managed dataset object", re.compile(r'resource\s+"google_storage_bucket_object"')),
)
TERRAFORM_APPLY_GUIDANCE_GLOBS = (
    "AGENTS.md",
    ".claude/skills/**/SKILL.md",
    "docs/**/*.md",
    "scripts/README.md",
)
TERRAFORM_APPLY_GUIDANCE_RE = re.compile(
    r"\bterraform\s+(?:-[^\s`]+(?:\s+|$))*apply\b|scripts/terraform_prod_apply\.py",
    re.IGNORECASE,
)
TERRAFORM_APPLY_ALLOWED_CONTEXT_RE = re.compile(
    r"("
    r"\bdo not\b|"
    r"\bmust not\b|"
    r"\bnot (?:a )?local apply\b|"
    r"\bprotected (?:github actions )?workflows?\b|"
    r"\bprotected production workflows?\b|"
    r"\bprotected-terraform-apply\b|"
    r"\bprotected terraform apply\b|"
    r"\breviewed PR\b|"
    r"\bafter review and merge\b|"
    r"\bbreak-glass\b|"
    r"\bemergency\b|"
    r"\breserved for explicitly approved break-glass"
    r")",
    re.IGNORECASE,
)

WORKFLOW_GCP_AUTH_MARKERS = (
    "google-github-actions/auth",
    "service_account: ${{ env.PUBLISHER_SERVICE_ACCOUNT }}",
    "service_account: ${{ env.TERRAFORM_SERVICE_ACCOUNT }}",
    "service_account: ${{ env.INDEX_LOADER_SERVICE_ACCOUNT }}",
    "service_account: ${{ vars.GCP_READONLY_SERVICE_ACCOUNT }}",
)
WORKFLOW_MAIN_REF_GUARD = 'GITHUB_REF}" != "refs/heads/main"'
WORKFLOW_TRUSTED_CHECKOUT_MARKERS = (
    "ref: main",
    "ref: ${{ github.event.repository.default_branch }}",
)
WORKFLOW_SINGLE_OBJECT_FALLBACK_MARKERS = (
    "Single-object fallback",
    "Promote staged object manually",
    "github.event.inputs.pr_number == ''",
)
FEATURE_PREVIEW_DROPDOWN_DEPLOY_MARKERS = (
    "PREVIEW_REF: ${{ github.ref_name }}",
    "PREVIEW_SOURCE_REF: ${{ github.ref }}",
    "ref: ${{ github.ref }}",
    "Select the branch or tag to deploy from the workflow branch dropdown.",
)


@dataclass(frozen=True)
class ChangedFile:
    status: str
    path: str
    old_path: str = ""


class GuardrailError(ValueError):
    """Raised when guardrail inputs cannot be evaluated."""


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
        raise GuardrailError(f"git diff failed: {result.stderr.strip()}")
    changes: list[ChangedFile] = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        status = parts[0]
        if STATUS_RENAME_COPY_RE.match(status) and len(parts) >= 3:
            changes.append(ChangedFile(status=status, old_path=parts[1], path=parts[2]))
        elif len(parts) >= 2:
            changes.append(ChangedFile(status=status, path=parts[1]))
    return changes


def git_show(ref: str, path: str, *, repo_root: Path) -> str | None:
    result = run_git(["show", f"{ref}:{path}"], repo_root=repo_root)
    if result.returncode != 0:
        return None
    return result.stdout


def labels_from_event(event_path: Path | None) -> set[str]:
    if event_path is None or not event_path.exists():
        return set()
    payload = json.loads(event_path.read_text())
    labels: set[str] = set()
    pull_request = payload.get("pull_request") or {}
    for label in pull_request.get("labels") or []:
        name = label.get("name") if isinstance(label, dict) else str(label)
        if name:
            labels.add(str(name))
    label = payload.get("label")
    if isinstance(label, dict) and label.get("name"):
        labels.add(str(label["name"]))
    return labels


def has_label(labels: set[str], kind: str) -> bool:
    return bool(labels & APPROVAL_LABELS[kind])


def parse_catalog_rows(text: str | None) -> dict[str, dict[str, str]]:
    if text is None:
        return {}
    return {row["asset_slug"]: row for row in csv.DictReader(text.splitlines()) if row.get("asset_slug")}


def changed_catalog_slugs(base_text: str | None, head_text: str | None) -> set[str]:
    base = parse_catalog_rows(base_text)
    head = parse_catalog_rows(head_text)
    slugs = set(base) | set(head)
    return {slug for slug in slugs if base.get(slug) != head.get(slug)}


def changed_asset_doc_slugs(changes: Sequence[ChangedFile]) -> set[str]:
    slugs: set[str] = set()
    for change in changes:
        for path in (change.path, change.old_path):
            match = ASSET_DOC_RE.fullmatch(path or "")
            if match:
                slugs.add(match.group(1))
    return slugs


def check_catalog_csv_source(changes: Sequence[ChangedFile], *, base: str, head: str, repo_root: Path) -> list[str]:
    if not any(change.path == CATALOG_PATH or change.old_path == CATALOG_PATH for change in changes):
        return []
    catalog_slugs = changed_catalog_slugs(
        git_show(base, CATALOG_PATH, repo_root=repo_root),
        git_show(head, CATALOG_PATH, repo_root=repo_root),
    )
    doc_slugs = changed_asset_doc_slugs(changes)
    if not catalog_slugs:
        return [
            f"{CATALOG_PATH} changed without row-level asset metadata changes; update docs/assets/*.md and regenerate instead."
        ]
    missing = sorted(catalog_slugs - doc_slugs)
    if missing:
        return [
            f"{CATALOG_PATH} row(s) changed for {', '.join(missing)}, but matching docs/assets/{{asset-slug}}.md files did not change."
        ]
    return []


def top_level_categories(text: str | None) -> set[str]:
    if text is None:
        return set()
    payload = yaml.safe_load(text) or {}
    categories = payload.get("categories") or {}
    return {str(name) for name in categories}


def check_top_level_categories(*, base: str, head: str, repo_root: Path, labels: set[str]) -> list[str]:
    path = "catalog/categories.yaml"
    before = top_level_categories(git_show(base, path, repo_root=repo_root))
    after = top_level_categories(git_show(head, path, repo_root=repo_root))
    if before == after or has_label(labels, "taxonomy"):
        return []
    return [
        "top-level dataset category changes require an approval label: "
        + ", ".join(sorted(APPROVAL_LABELS["taxonomy"]))
    ]


def extract_constant_set(text: str | None, constant: str) -> set[str]:
    if text is None:
        return set()
    match = re.search(rf"\b{re.escape(constant)}\s*=\s*\{{(.*?)\}}", text, flags=re.DOTALL)
    if not match:
        return set()
    return set(re.findall(r"""["']([^"']+)["']""", match.group(1)))


def check_approved_formats(*, base: str, head: str, repo_root: Path, labels: set[str]) -> list[str]:
    changed: list[str] = []
    for path, constants in FORMAT_CONSTANTS.items():
        base_text = git_show(base, path, repo_root=repo_root)
        head_text = git_show(head, path, repo_root=repo_root)
        for constant in constants:
            if extract_constant_set(base_text, constant) != extract_constant_set(head_text, constant):
                changed.append(f"{path}:{constant}")
    if not changed or has_label(labels, "canonical_format"):
        return []
    return [
        "approved canonical/data format constants changed without an approval label: "
        + ", ".join(changed)
        + "; add one of "
        + ", ".join(sorted(APPROVAL_LABELS["canonical_format"]))
    ]


def changed_paths(changes: Sequence[ChangedFile]) -> set[str]:
    return {path for change in changes for path in (change.path, change.old_path) if path}


def file_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return ""


def check_second_iac_framework(
    changes: Sequence[ChangedFile],
    *,
    repo_root: Path,
    labels: set[str],
) -> list[str]:
    if has_label(labels, "iac_framework"):
        return []
    matches: list[str] = []
    for path in sorted(changed_paths(changes)):
        if SECOND_IAC_PATH_RE.search(path):
            matches.append(path)
            continue
        full_path = repo_root / path
        if full_path.is_file() and path != "scripts/repo_guardrails.py" and not path.startswith(("docs/", "AGENTS.md", "README.md")):
            text = file_text(full_path)
            if SECOND_IAC_CONTENT_RE.search(text):
                matches.append(path)
    if not matches:
        return []
    return [
        "second IaC framework files or dependencies require an approval label: "
        + ", ".join(matches)
        + "; add one of "
        + ", ".join(sorted(APPROVAL_LABELS["iac_framework"]))
    ]


def production_ingestion_jobs(repo_root: Path) -> list[str]:
    jobs: list[str] = []
    ingestion_root = repo_root / "ingestion"
    for path in sorted(ingestion_root.iterdir()):
        if path.name in INGESTION_JOB_EXCLUDES or not path.is_dir():
            continue
        if (path / "run.py").exists() and (path / "README.md").exists():
            jobs.append(path.name)
    return jobs


def check_ingestion_no_gcs_deletes(repo_root: Path) -> list[str]:
    errors: list[str] = []
    for run_path in sorted((repo_root / "ingestion").rglob("*.py")):
        if "__pycache__" in run_path.parts:
            continue
        text = file_text(run_path)
        for pattern in GCS_DELETE_PATTERNS:
            if pattern.search(text):
                errors.append(f"{run_path.relative_to(repo_root)} contains a GCS delete operation; scheduled jobs must not delete releases.")
                break
    return errors


def check_ingestion_skip_tests(repo_root: Path) -> list[str]:
    errors: list[str] = []
    for job in production_ingestion_jobs(repo_root):
        test_path = repo_root / "tests" / f"test_{job}.py"
        if not test_path.exists():
            errors.append(f"ingestion/{job} needs tests/test_{job}.py with an unchanged/skipped-output fixture.")
            continue
        text = file_text(test_path)
        has_skip_fixture = re.search(r"def\s+test_[^(]*skip", text) and "skipped" in text
        if not has_skip_fixture:
            errors.append(f"{test_path.relative_to(repo_root)} needs a skipped/unchanged-output fixture.")
    return errors


def tracked_files(repo_root: Path) -> list[str]:
    result = run_git(["ls-files"], repo_root=repo_root)
    if result.returncode != 0:
        raise GuardrailError(f"git ls-files failed: {result.stderr.strip()}")
    return [line for line in result.stdout.splitlines() if line]


def check_secrets(repo_root: Path) -> list[str]:
    errors: list[str] = []
    for path in tracked_files(repo_root):
        if SECRET_FILE_RE.search(path) and not SECRET_FILE_ALLOW_RE.search(path):
            errors.append(f"{path}: tracked credential-like file is not allowed")
            continue
        full_path = repo_root / path
        if not full_path.is_file() or full_path.stat().st_size > 2_000_000:
            continue
        text = file_text(full_path)
        if not text:
            continue
        for label, pattern in SECRET_PATTERNS:
            if pattern.search(text):
                errors.append(f"{path}: possible {label} committed")
                break
    return errors


def check_terraform_static(repo_root: Path) -> list[str]:
    errors: list[str] = []
    for path in sorted((repo_root / "terraform").rglob("*.tf")):
        text = file_text(path)
        for label, pattern in TERRAFORM_FORBIDDEN_PATTERNS:
            if pattern.search(text):
                errors.append(f"{path.relative_to(repo_root)}: {label} is not allowed")
                break
    return errors


def terraform_apply_guidance_paths(repo_root: Path) -> list[Path]:
    paths: set[Path] = set()
    for pattern in TERRAFORM_APPLY_GUIDANCE_GLOBS:
        paths.update(path for path in repo_root.glob(pattern) if path.is_file())
    return sorted(paths)


def check_no_local_terraform_apply_guidance(repo_root: Path) -> list[str]:
    errors: list[str] = []
    for path in terraform_apply_guidance_paths(repo_root):
        text = file_text(path)
        if not text:
            continue
        lines = text.splitlines()
        for line_number, line in enumerate(lines, start=1):
            if not TERRAFORM_APPLY_GUIDANCE_RE.search(line):
                continue
            if line_number <= 3 and path.name == "SKILL.md" and line.lstrip().startswith("description:"):
                continue
            context = "\n".join(lines[max(0, line_number - 2) : min(len(lines), line_number + 1)])
            if TERRAFORM_APPLY_ALLOWED_CONTEXT_RE.search(context):
                continue
            errors.append(
                f"{path.relative_to(repo_root)}:{line_number}: local production Terraform apply guidance must route through protected PR workflows or explicit break-glass"
            )
    return errors


def check_workflow_boundaries(repo_root: Path) -> list[str]:
    workflows_dir = repo_root / ".github" / "workflows"
    if not workflows_dir.exists():
        return []

    errors: list[str] = []
    for path in sorted(workflows_dir.glob("*.yml")):
        text = file_text(path)
        rel = path.relative_to(repo_root)

        if any(marker in text for marker in WORKFLOW_SINGLE_OBJECT_FALLBACK_MARKERS):
            errors.append(f"{rel}: single-object dataset publish fallback is not allowed; use reviewed PR plans")

        uses_gcp_auth = any(marker in text for marker in WORKFLOW_GCP_AUTH_MARKERS)
        if uses_gcp_auth and "workflow_dispatch:" in text:
            allows_dropdown_preview_ref = rel.as_posix() == ".github/workflows/feature-preview-deploy.yml" and all(
                marker in text for marker in FEATURE_PREVIEW_DROPDOWN_DEPLOY_MARKERS
            )
            if WORKFLOW_MAIN_REF_GUARD not in text and not allows_dropdown_preview_ref:
                errors.append(f"{rel}: GCP-auth workflow_dispatch paths must validate refs/heads/main")
            if not any(marker in text for marker in WORKFLOW_TRUSTED_CHECKOUT_MARKERS):
                errors.append(f"{rel}: GCP-auth workflow_dispatch paths must check out trusted main code")

        is_preview_workflow = "skytruth-shared-datasets-1-preview" in text or "feature branch preview" in text.lower()
        if is_preview_workflow and "gs://skytruth-shared-datasets-1/" in text:
            errors.append(f"{rel}: preview workflows must not accept production bucket URIs")

        if "terraform -chdir=terraform/envs/preview apply" in text and "terraform -chdir=terraform/envs/prod" in text:
            errors.append(f"{rel}: preview Terraform apply workflows must stay under terraform/envs/preview")

        if "terraform -chdir=terraform/envs/prod apply" in text:
            required = {
                "main ref validation": WORKFLOW_MAIN_REF_GUARD,
                "prod Terraform state concurrency": "group: prod-terraform-state",
                "resource-change allowlist": "allowed_exact",
            }
            for label, marker in required.items():
                if marker not in text:
                    errors.append(f"{rel}: prod Terraform apply workflow is missing {label}")

    return errors


def check_diff(args: argparse.Namespace) -> list[str]:
    repo_root = args.repo_root.resolve()
    changes = git_changed_files(args.base, args.head, repo_root=repo_root)
    labels = labels_from_event(args.event_path)
    errors: list[str] = []
    errors.extend(check_catalog_csv_source(changes, base=args.base, head=args.head, repo_root=repo_root))
    errors.extend(check_top_level_categories(base=args.base, head=args.head, repo_root=repo_root, labels=labels))
    errors.extend(check_approved_formats(base=args.base, head=args.head, repo_root=repo_root, labels=labels))
    errors.extend(check_second_iac_framework(changes, repo_root=repo_root, labels=labels))
    return errors


def check_static(args: argparse.Namespace) -> list[str]:
    repo_root = args.repo_root.resolve()
    errors: list[str] = []
    errors.extend(check_ingestion_no_gcs_deletes(repo_root))
    errors.extend(check_ingestion_skip_tests(repo_root))
    errors.extend(check_secrets(repo_root))
    errors.extend(check_terraform_static(repo_root))
    errors.extend(check_no_local_terraform_apply_guidance(repo_root))
    errors.extend(check_workflow_boundaries(repo_root))
    return errors


def report(errors: Iterable[str]) -> int:
    collected = list(errors)
    if not collected:
        print("repo guardrails passed")
        return 0
    for error in collected:
        print(f"error: {error}", file=sys.stderr)
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    subparsers = parser.add_subparsers(dest="command", required=True)

    diff = subparsers.add_parser("check-diff", help="Check PR/push diff-sensitive guardrails.")
    diff.add_argument("--base", required=True, help="Base git ref or SHA.")
    diff.add_argument("--head", required=True, help="Head git ref or SHA.")
    diff.add_argument("--event-path", type=Path, help="Optional GitHub event JSON for approval labels.")
    diff.set_defaults(func=check_diff)

    static = subparsers.add_parser("check-static", help="Check whole-repo static guardrails.")
    static.set_defaults(func=check_static)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return report(args.func(args))
    except (GuardrailError, OSError, json.JSONDecodeError, yaml.YAMLError) as exc:
        print(f"repo-guardrails: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
