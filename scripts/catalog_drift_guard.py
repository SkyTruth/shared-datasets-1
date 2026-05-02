#!/usr/bin/env python3
"""Fail when live catalog contract objects drift from repo-generated outputs.

The guard is intentionally read-only. It downloads the small bucket-side catalog
contract files and compares them to the local repo contract expected from the
current checkout.
"""

from __future__ import annotations

import argparse
import copy
import difflib
import hashlib
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from google.api_core.exceptions import GoogleAPIError, NotFound
from google.cloud import storage

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import catalog_site


DEFAULT_BUCKET = "skytruth-shared-datasets-1"
REMOTE_CSV_OBJECT = "_catalog/shared-datasets-catalog.csv"
REMOTE_WEB_CATALOG_OBJECT = "_catalog/web/catalog.json"
IGNORED_GENERATED_AT = "<ignored-generated-at>"
MAX_DIFF_LINES = 120


@dataclass(frozen=True)
class RemoteObject:
    uri: str
    name: str
    generation: str
    updated: str
    size: int
    text: str


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    message: str
    remote_uri: str
    remote_generation: str = ""
    remote_updated: str = ""
    diff: str = ""


class CatalogDriftGuardError(RuntimeError):
    """Raised when the guard cannot complete its read-only checks."""


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check live shared-datasets catalog objects for drift.")
    parser.add_argument(
        "--bucket",
        default=os.environ.get("SHARED_DATASETS_BUCKET", DEFAULT_BUCKET),
        help="GCS bucket name, without gs://.",
    )
    parser.add_argument("--catalog", type=Path, default=Path("catalog/shared-datasets-catalog.csv"))
    parser.add_argument("--categories", type=Path, default=Path("catalog/categories.yaml"))
    parser.add_argument("--docs-dir", type=Path, default=Path("docs/assets"))
    parser.add_argument("--site-prefix", default=catalog_site.DEFAULT_SITE_PREFIX)
    parser.add_argument(
        "--summary-file",
        type=Path,
        default=Path(os.environ["GITHUB_STEP_SUMMARY"]) if os.environ.get("GITHUB_STEP_SUMMARY") else None,
        help="Optional Markdown file to append the check report to.",
    )
    return parser.parse_args(argv)


def get_client() -> storage.Client:
    return storage.Client(project=os.environ.get("GOOGLE_CLOUD_PROJECT") or None)


def download_text(bucket_name: str, object_name: str, *, client: storage.Client | None = None) -> RemoteObject:
    client = client or get_client()
    blob = client.bucket(bucket_name).blob(object_name)
    uri = f"gs://{bucket_name}/{object_name}"
    try:
        blob.reload()
    except NotFound as exc:
        raise CatalogDriftGuardError(f"remote contract object is missing: {uri}") from exc

    generation = str(blob.generation or "")
    if generation:
        download_blob = client.bucket(bucket_name).blob(object_name, generation=int(generation))
    else:
        download_blob = blob
    try:
        text = download_blob.download_as_text()
    except NotFound as exc:
        message = f"remote contract object generation is missing: {uri}#{generation}"
        raise CatalogDriftGuardError(message) from exc

    return RemoteObject(
        uri=uri,
        name=object_name,
        generation=generation,
        updated=blob.updated.isoformat() if blob.updated else "",
        size=int(blob.size or 0),
        text=text,
    )


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def unified_diff(*, remote_text: str, expected_text: str, remote_label: str, expected_label: str) -> str:
    lines = list(
        difflib.unified_diff(
            remote_text.splitlines(),
            expected_text.splitlines(),
            fromfile=remote_label,
            tofile=expected_label,
            lineterm="",
        )
    )
    if len(lines) > MAX_DIFF_LINES:
        omitted = len(lines) - MAX_DIFF_LINES
        lines = lines[:MAX_DIFF_LINES] + [f"... diff truncated; {omitted} line(s) omitted ..."]
    return "\n".join(lines)


def check_csv_contract(local_text: str, remote: RemoteObject) -> CheckResult:
    local_hash = sha256_text(local_text)
    remote_hash = sha256_text(remote.text)
    if remote.text == local_text:
        return CheckResult(
            name="Bucket CSV catalog",
            ok=True,
            message=f"Remote CSV matches the repo catalog (sha256 {local_hash}).",
            remote_uri=remote.uri,
            remote_generation=remote.generation,
            remote_updated=remote.updated,
        )

    diff = unified_diff(
        remote_text=remote.text,
        expected_text=local_text,
        remote_label=f"{remote.uri} (live)",
        expected_label="catalog/shared-datasets-catalog.csv (repo)",
    )
    return CheckResult(
        name="Bucket CSV catalog",
        ok=False,
        message=(
            "Remote _catalog/shared-datasets-catalog.csv differs from the repo catalog "
            f"(live sha256 {remote_hash}, repo sha256 {local_hash})."
        ),
        remote_uri=remote.uri,
        remote_generation=remote.generation,
        remote_updated=remote.updated,
        diff=diff,
    )


def load_json_payload(text: str, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CatalogDriftGuardError(f"{label} is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise CatalogDriftGuardError(f"{label} must be a JSON object")
    return payload


def normalize_web_catalog_payload(payload: dict[str, Any], *, label: str) -> dict[str, Any]:
    if "generated_at" not in payload:
        raise CatalogDriftGuardError(f"{label} is missing required generated_at")
    if not isinstance(payload["generated_at"], str) or not payload["generated_at"].strip():
        raise CatalogDriftGuardError(f"{label} generated_at must be a non-empty string")

    normalized = copy.deepcopy(payload)
    normalized["generated_at"] = IGNORED_GENERATED_AT
    return normalized


def web_catalog_contract_text(payload: dict[str, Any]) -> str:
    return json.dumps(normalize_web_catalog_payload(payload, label="web catalog"), indent=2, sort_keys=True) + "\n"


def check_web_catalog_contract(expected_payload: dict[str, Any], remote: RemoteObject) -> CheckResult:
    remote_payload = load_json_payload(remote.text, label=remote.uri)
    remote_text = web_catalog_contract_text(remote_payload)
    expected_text = web_catalog_contract_text(expected_payload)
    local_hash = sha256_text(expected_text)
    remote_hash = sha256_text(remote_text)
    if remote_text == expected_text:
        return CheckResult(
            name="Static web catalog.json",
            ok=True,
            message=(
                "Remote _catalog/web/catalog.json matches the repo-generated static catalog "
                f"after ignoring generated_at (sha256 {local_hash})."
            ),
            remote_uri=remote.uri,
            remote_generation=remote.generation,
            remote_updated=remote.updated,
        )

    diff = unified_diff(
        remote_text=remote_text,
        expected_text=expected_text,
        remote_label=f"{remote.uri} (live, generated_at ignored)",
        expected_label="repo-generated _catalog/web/catalog.json (generated_at ignored)",
    )
    return CheckResult(
        name="Static web catalog.json",
        ok=False,
        message=(
            "Remote _catalog/web/catalog.json differs from the repo-generated static catalog "
            f"after ignoring generated_at (live sha256 {remote_hash}, repo sha256 {local_hash})."
        ),
        remote_uri=remote.uri,
        remote_generation=remote.generation,
        remote_updated=remote.updated,
        diff=diff,
    )


def expected_web_payload(args: argparse.Namespace) -> dict[str, Any]:
    return catalog_site.build_catalog_payload(
        catalog_path=args.catalog,
        categories_path=args.categories,
        docs_dir=args.docs_dir,
        bucket=args.bucket,
        site_prefix=args.site_prefix,
    )


def run_checks(args: argparse.Namespace) -> list[CheckResult]:
    local_catalog_text = args.catalog.read_text()
    expected_payload = expected_web_payload(args)
    client = get_client()
    remote_csv = download_text(args.bucket, REMOTE_CSV_OBJECT, client=client)
    remote_web_catalog = download_text(args.bucket, REMOTE_WEB_CATALOG_OBJECT, client=client)
    return [
        check_csv_contract(local_catalog_text, remote_csv),
        check_web_catalog_contract(expected_payload, remote_web_catalog),
    ]


def render_report(results: Sequence[CheckResult], *, bucket: str) -> str:
    failed = [result for result in results if not result.ok]
    lines = [
        "# Catalog drift guard",
        "",
        f"- Bucket: `gs://{bucket}/`",
        f"- Result: `{'FAIL' if failed else 'PASS'}`",
        "",
    ]
    for result in results:
        status = "PASS" if result.ok else "FAIL"
        lines.extend(
            [
                f"## {status}: {result.name}",
                "",
                result.message,
                "",
                f"- Remote object: `{result.remote_uri}`",
                f"- Remote generation: `{result.remote_generation or 'unknown'}`",
                f"- Remote updated: `{result.remote_updated or 'unknown'}`",
                "",
            ]
        )
        if result.diff:
            lines.extend(["```diff", result.diff, "```", ""])
    return "\n".join(lines).rstrip() + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        results = run_checks(args)
    except (CatalogDriftGuardError, catalog_site.CatalogSiteError, GoogleAPIError, OSError) as exc:
        print(f"catalog-drift-guard: {exc}", file=sys.stderr)
        return 2

    report = render_report(results, bucket=args.bucket)
    print(report)
    if args.summary_file:
        with args.summary_file.open("a") as handle:
            handle.write(report)

    return 1 if any(not result.ok for result in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
