#!/usr/bin/env python3
"""Workflow-step commands for the approved dataset mutation workflows.

Each subcommand replaces an inline Python block previously embedded in
`.github/workflows/publish-dataset.yml` or
`.github/workflows/dataset-breaking-change-alert.yml`.

The module is stdlib-only at import time because several steps run on the
runner's system Python before `uv sync`. The `promote` subcommand imports the
GCS client stack lazily and must run under `uv run`.
"""

from __future__ import annotations

import argparse
import base64
import copy
import csv
import json
import os
import pathlib
import shlex
import subprocess
import sys
import tempfile
import urllib.parse
from typing import Any

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import catalog_csv

CATALOG_CSV_PATH = str(catalog_csv.DEFAULT_CATALOG_CSV)
SCHEMA_SUFFIXES = {".fgb", ".geojson", ".ndgeojson", ".csv"}
REQUIRED_REVIEWER = "jonaraphael"
BREAKING_ALERT_MARKER_PREFIX = "shared-datasets-breaking-alert"
IGNORED_GENERATED_AT = "<ignored-generated-at>"
CATALOG_ROW_FILES = (
    ("current-catalog-row.json", "--current-catalog-row-json"),
    ("proposed-catalog-row.json", "--proposed-catalog-row-json"),
)
SCHEMA_RESULTS_DIR = "schema-results"


def bucket_name() -> str:
    return os.environ["SHARED_DATASETS_BUCKET"]


def repository() -> str:
    return os.environ["GITHUB_REPOSITORY"]


def load_plan(plan_json: str) -> dict[str, Any]:
    return json.loads(pathlib.Path(plan_json).read_text())


def plan_asset_slug() -> str:
    for path in ("publish-plan.json", "delete-plan.json"):
        plan_path = pathlib.Path(path)
        if plan_path.exists():
            return json.loads(plan_path.read_text())["asset_slug"]
    return ""


def row_from_catalog_text(text: str, asset_slug: str) -> dict[str, str] | None:
    for row in csv.DictReader(text.splitlines()):
        if row.get("asset_slug") == asset_slug:
            return row
    return None


def catalog_row(asset_slug: str) -> dict[str, str] | None:
    return catalog_csv.catalog_row(asset_slug)


def is_schema_target(asset_slug: str, destination_uri: str, *, bucket: str, row: dict[str, str] | None) -> bool:
    """Decide whether a promotion destination carries the asset's schema contract.

    `row` is the asset's catalog row (or None when the asset is not in the
    catalog yet). Single owner for a rule that previously existed in five
    inline copies across two workflows.
    """
    prefix = f"gs://{bucket}/"
    if not destination_uri.startswith(prefix):
        return False
    destination_name = destination_uri.removeprefix(prefix)
    filename = destination_name.rsplit("/", 1)[-1]
    suffix = pathlib.Path(filename).suffix.lower()
    if suffix not in SCHEMA_SUFFIXES or not ("/latest/" in destination_name or "/releases/" in destination_name):
        return False
    if row:
        canonical_path = row.get("canonical_path", "")
        canonical_filename = canonical_path.rsplit("/", 1)[-1]
        if destination_uri == canonical_path:
            return True
        if "/latest/" in canonical_path:
            asset_root = canonical_path.split("/latest/", 1)[0]
            if destination_uri.startswith(f"{asset_root}/releases/") and filename == canonical_filename:
                return True
        return False
    return pathlib.Path(filename).stem == asset_slug


def gcs_asset_args(*args: str) -> list[str]:
    return ["uv", "run", "python", "scripts/gcs_asset.py", *args]


def gh_api_json(path: str) -> Any:
    return json.loads(subprocess.check_output(["gh", "api", path], text=True))


def download_promotion_source(promotion: dict[str, Any], dataset_path: pathlib.Path) -> None:
    subprocess.run(
        gcs_asset_args(
            "download",
            promotion["source_uri"],
            str(dataset_path),
            "--generation",
            promotion["source_generation"],
        ),
        check=True,
    )


def command_check_approved_review(args: argparse.Namespace) -> int:
    reviews = json.loads(pathlib.Path(args.reviews_json).read_text())
    approved = [
        review
        for review in reviews
        if review.get("state") == "APPROVED"
        and review.get("user", {}).get("login") == REQUIRED_REVIEWER
    ]
    if not approved:
        print(f"Merged PR does not have an APPROVED review from {REQUIRED_REVIEWER}", file=sys.stderr)
        return 1
    return 0


def remote_catalog_text(ref: str) -> str | None:
    if not ref:
        return None
    quoted_ref = urllib.parse.quote(ref, safe="")
    try:
        payload = gh_api_json(f"repos/{repository()}/contents/{CATALOG_CSV_PATH}?ref={quoted_ref}")
    except subprocess.CalledProcessError as exc:
        print(f"Could not read catalog at {ref}: {exc}")
        return None
    return base64.b64decode(str(payload.get("content") or "")).decode("utf-8")


def first_parent(ref: str) -> str:
    if not ref:
        return ""
    quoted_ref = urllib.parse.quote(ref, safe="")
    try:
        payload = gh_api_json(f"repos/{repository()}/commits/{quoted_ref}")
    except subprocess.CalledProcessError as exc:
        print(f"Could not inspect merge commit {ref}: {exc}")
        return ""
    parents = payload.get("parents") or []
    return str(parents[0].get("sha") or "") if parents else ""


def command_collect_catalog_rows(args: argparse.Namespace) -> int:
    asset_slug = plan_asset_slug()
    if not asset_slug:
        return 0
    event = json.loads(pathlib.Path(args.event_path).read_text())
    pull_request = event.get("pull_request") or {}
    proposed_ref = str(pull_request.get("merge_commit_sha") or pull_request.get("head", {}).get("sha") or "")
    current_ref = first_parent(proposed_ref) or str(pull_request.get("base", {}).get("sha") or "")

    current_text = remote_catalog_text(current_ref)
    proposed_text = remote_catalog_text(proposed_ref)
    if proposed_text is None and pathlib.Path(CATALOG_CSV_PATH).exists():
        proposed_text = pathlib.Path(CATALOG_CSV_PATH).read_text()

    if current_text:
        row = row_from_catalog_text(current_text, asset_slug)
        if row:
            pathlib.Path("current-catalog-row.json").write_text(json.dumps(row, indent=2, sort_keys=True) + "\n")
    if proposed_text:
        row = row_from_catalog_text(proposed_text, asset_slug)
        if row:
            pathlib.Path("proposed-catalog-row.json").write_text(json.dumps(row, indent=2, sort_keys=True) + "\n")
    if not pathlib.Path("current-catalog-row.json").exists():
        print(f"No baseline catalog row found for {asset_slug}; skipping catalog contract comparison.")
    if not pathlib.Path("proposed-catalog-row.json").exists():
        print(f"No proposed catalog row found for {asset_slug}; skipping catalog contract comparison.")
    return 0


def command_collect_proposed_catalog_row(args: argparse.Namespace) -> int:
    asset_slug = plan_asset_slug()
    if not asset_slug:
        return 0
    ref = urllib.parse.quote(args.head_sha, safe="")
    try:
        payload = gh_api_json(f"repos/{repository()}/contents/{CATALOG_CSV_PATH}?ref={ref}")
    except subprocess.CalledProcessError as exc:
        print(f"Could not read proposed catalog at {args.head_sha}: {exc}")
        return 0
    content = base64.b64decode(str(payload.get("content") or "")).decode("utf-8")
    row = row_from_catalog_text(content, asset_slug)
    if row:
        pathlib.Path("proposed-catalog-row.json").write_text(json.dumps(row, indent=2, sort_keys=True) + "\n")
    else:
        print(f"No proposed catalog row found for {asset_slug}; skipping catalog contract comparison.")
    return 0


def command_validate_plan_paths(args: argparse.Namespace) -> int:
    plan = load_plan(args.plan_json)
    if args.plan_type == "publish":
        uris = [promotion["destination_uri"] for promotion in plan["promotions"]]
    else:
        uris = [deletion["uri"] for deletion in plan["deletions"]]
    for uri in uris:
        subprocess.run(gcs_asset_args("validate-path", uri), check=True)
    return 0


def command_detect_schema_targets(args: argparse.Namespace) -> int:
    plan = load_plan(args.plan_json)
    asset_slug = plan["asset_slug"]
    row = catalog_row(asset_slug)
    bucket = bucket_name()
    targets = [
        promotion["destination_uri"]
        for promotion in plan["promotions"]
        if promotion.get("destination_generation")
        and is_schema_target(asset_slug, promotion["destination_uri"], bucket=bucket, row=row)
    ]
    with open(args.github_output, "a") as output:
        output.write(f"has_schema_targets={str(bool(targets)).lower()}\n")
        output.write(f"schema_target_count={len(targets)}\n")
    return 0


def command_check_schema_compatibility(args: argparse.Namespace) -> int:
    skip_label = "schema compatibility check" if args.phase == "live" else "planned schema alert"
    plan = load_plan(args.plan_json)
    asset_slug = plan["asset_slug"]
    row = catalog_row(asset_slug)
    bucket = bucket_name()
    pathlib.Path(SCHEMA_RESULTS_DIR).mkdir(exist_ok=True)
    for index, promotion in enumerate(plan["promotions"], start=1):
        destination_uri = promotion["destination_uri"]
        if not promotion.get("destination_generation"):
            print(f"Skipping {skip_label} for new destination {index}: {destination_uri}")
            continue
        if not is_schema_target(asset_slug, destination_uri, bucket=bucket, row=row):
            print(f"Skipping {skip_label} for promotion {index}: {destination_uri}")
            continue
        filename = destination_uri.rsplit("/", 1)[-1]
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            dataset_path = tmp_path / filename
            download_promotion_source(promotion, dataset_path)
            check_args = [
                "uv",
                "run",
                "python",
                "scripts/dataset_alerts.py",
                "check-schema-compatibility",
                "--asset-slug",
                asset_slug,
                "--dataset-path",
                str(dataset_path),
            ]
            waiver = promotion.get("compatibility_waiver")
            if waiver:
                waiver_path = tmp_path / "compatibility-waiver.json"
                waiver_path.write_text(json.dumps(waiver, indent=2, sort_keys=True) + "\n")
                check_args.extend(["--compatibility-waiver", str(waiver_path)])
            completed = subprocess.run(check_args, check=True, capture_output=True, text=True)
            output = completed.stdout.strip()
            if output:
                if args.phase == "live":
                    print(output)
                pathlib.Path(SCHEMA_RESULTS_DIR, f"promotion-{index}.json").write_text(output + "\n")
    return 0


def normalize_catalog_json(text: str, *, label: str) -> dict[str, Any]:
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object")
    generated_at = payload.get("generated_at")
    if not isinstance(generated_at, str) or not generated_at.strip():
        raise ValueError(f"{label} generated_at must be a non-empty string")
    normalized = copy.deepcopy(payload)
    normalized["generated_at"] = IGNORED_GENERATED_AT
    return normalized


def command_promote(args: argparse.Namespace) -> int:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from google.api_core.exceptions import NotFound

    from scripts import gcs_asset

    bucket = bucket_name()
    catalog_web_catalog_uri = f"gs://{bucket}/_catalog/web/catalog.json"

    def catalog_json_destination_already_current(promotion: dict[str, Any]) -> bool:
        if promotion["destination_uri"] != catalog_web_catalog_uri:
            return False

        client = gcs_asset.get_client()
        destination_blob = gcs_asset.get_blob(promotion["destination_uri"])
        try:
            destination_blob.reload()
        except NotFound:
            return False

        if promotion["content_type"] and destination_blob.content_type != promotion["content_type"]:
            return False
        if promotion["cache_control"] and destination_blob.cache_control != promotion["cache_control"]:
            return False

        source_bucket, source_name = gcs_asset.parse_gs_uri(promotion["source_uri"])
        destination_bucket, destination_name = gcs_asset.parse_gs_uri(promotion["destination_uri"])
        source_blob = client.bucket(source_bucket).blob(
            source_name,
            generation=int(promotion["source_generation"]),
        )
        current_destination_blob = client.bucket(destination_bucket).blob(
            destination_name,
            generation=int(destination_blob.generation),
        )

        try:
            source_payload = normalize_catalog_json(
                source_blob.download_as_text(),
                label=promotion["source_uri"],
            )
            destination_payload = normalize_catalog_json(
                current_destination_blob.download_as_text(),
                label=promotion["destination_uri"],
            )
        except (json.JSONDecodeError, ValueError):
            return False

        if source_payload != destination_payload:
            return False

        print(
            "Skipping catalog.json promotion because the destination already matches "
            "the staged source after ignoring generated_at, with matching content "
            "type and cache-control metadata."
        )
        return True

    plan = load_plan(args.plan_json)
    asset_slug = plan["asset_slug"]
    row = catalog_row(asset_slug)
    for index, promotion in enumerate(plan["promotions"], start=1):
        print(f"Promoting object {index} of {len(plan['promotions'])}: {promotion['destination_uri']}")
        subprocess.run(gcs_asset_args("stat", promotion["source_uri"]), check=True)
        if catalog_json_destination_already_current(promotion):
            continue
        copy_args = gcs_asset_args(
            "copy",
            promotion["source_uri"],
            promotion["destination_uri"],
            "--source-generation",
            promotion["source_generation"],
        )
        if promotion["destination_generation"]:
            copy_args.extend(["--replace-generation", promotion["destination_generation"]])
        if promotion["content_type"]:
            copy_args.extend(["--content-type", promotion["content_type"]])
        if promotion["cache_control"]:
            copy_args.extend(["--cache-control", promotion["cache_control"]])
        print("running:", " ".join(shlex.quote(part) for part in copy_args))
        subprocess.run(copy_args, check=True)
        subprocess.run(gcs_asset_args("stat", promotion["destination_uri"]), check=True)
        if not is_schema_target(asset_slug, promotion["destination_uri"], bucket=bucket, row=row):
            print(f"Skipping schema snapshot update for promotion {index}: {promotion['destination_uri']}")
            continue
        filename = promotion["destination_uri"].rsplit("/", 1)[-1]
        with tempfile.TemporaryDirectory() as tmp:
            dataset_path = pathlib.Path(tmp) / filename
            download_promotion_source(promotion, dataset_path)
            subprocess.run(
                [
                    "uv",
                    "run",
                    "python",
                    "scripts/dataset_alerts.py",
                    "check-schema",
                    "--asset-slug",
                    asset_slug,
                    "--dataset-path",
                    str(dataset_path),
                    "--upload-snapshot",
                ],
                check=True,
            )
    return 0


def command_rebuild_release_index(args: argparse.Namespace) -> int:
    plan = load_plan(args.plan_json)
    requested_slugs = [plan["asset_slug"], *plan.get("release_index_asset_slugs", [])]
    asset_slugs = []
    seen = set()
    catalog_slugs = set(catalog_csv.load_catalog())
    for index, asset_slug in enumerate(requested_slugs):
        if not asset_slug or asset_slug in seen:
            continue
        if asset_slug not in catalog_slugs:
            if index == 0:
                print(f"Skipping release-index rebuild because {asset_slug!r} is not a catalog asset.")
                seen.add(asset_slug)
                continue
            raise RuntimeError(f"requested release-index rebuild asset is not in catalog: {asset_slug!r}")
        asset_slugs.append(asset_slug)
        seen.add(asset_slug)
    if not asset_slugs:
        print("No catalog asset release indexes requested for rebuild.")
        return 0

    for asset_slug in asset_slugs:
        subprocess.run(
            gcs_asset_args("release-index", "rebuild", "--asset-slug", asset_slug),
            check=True,
        )
    return 0


def breaking_alert_base_args(
    *,
    phase: str,
    plan_type: str,
    plan_json: str,
    run_url: str | None,
    pr_number: str,
    pr_url: str,
    include_current_row: bool,
    include_schema_results: bool,
) -> list[str]:
    alert_args = [
        "uv",
        "run",
        "python",
        "scripts/dataset_alerts.py",
        "breaking-alert",
        "--phase",
        phase,
        "--plan-type",
        plan_type,
        "--plan-json",
        plan_json,
    ]
    if run_url:
        alert_args.extend(["--run-url", run_url])
    if pr_number:
        alert_args.extend(["--pr-number", pr_number])
    if pr_url:
        alert_args.extend(["--pr-url", pr_url])
    for row_path, option in CATALOG_ROW_FILES:
        if option == "--current-catalog-row-json" and not include_current_row:
            continue
        if pathlib.Path(row_path).exists():
            alert_args.extend([option, row_path])
    if include_schema_results:
        for path in sorted(pathlib.Path(SCHEMA_RESULTS_DIR).glob("*.json")):
            alert_args.extend(["--schema-result", str(path)])
    return alert_args


def pr_comment_has_marker(pr_number: str, marker: str) -> bool:
    comments = gh_api_json(f"repos/{repository()}/issues/{pr_number}/comments?per_page=100")
    return any(marker in str(comment.get("body") or "") for comment in comments)


def post_pr_comment(pr_number: str, body: str) -> None:
    subprocess.run(
        ["gh", "api", f"repos/{repository()}/issues/{pr_number}/comments", "-f", f"body={body}"],
        check=True,
    )


def command_live_breaking_alert(args: argparse.Namespace) -> int:
    run_url = f"{os.environ['GITHUB_SERVER_URL']}/{repository()}/actions/runs/{os.environ['GITHUB_RUN_ID']}"
    pr_number = os.environ.get("PR_NUMBER", "").strip()
    pr_url = os.environ.get("PR_URL", "").strip()
    if not pr_url and pr_number:
        pr_url = f"{os.environ['GITHUB_SERVER_URL']}/{repository()}/pull/{pr_number}"
    base_args = breaking_alert_base_args(
        phase="live",
        plan_type=args.plan_type,
        plan_json=args.plan_json,
        run_url=run_url,
        pr_number=pr_number,
        pr_url=pr_url,
        include_current_row=True,
        include_schema_results=args.plan_type == "publish",
    )
    subprocess.run(base_args + ["--summary-json", args.summary_json, "--dry-run"], check=True)

    summary = json.loads(pathlib.Path(args.summary_json).read_text())
    if not summary.get("has_breaking_changes"):
        return 0
    marker = summary["marker"]
    if not marker.startswith(BREAKING_ALERT_MARKER_PREFIX):
        raise ValueError(f"unexpected breaking alert marker: {marker}")
    if pr_number and pr_comment_has_marker(pr_number, marker):
        print(f"Breaking alert already sent for marker {marker}; skipping Slack.")
        return 0
    subprocess.run(base_args, check=True)
    if pr_number:
        if args.plan_type == "publish":
            body = (
                f"<!-- {marker} -->\n"
                f"Live breaking change Slack alert sent for `{summary['asset_slug']}` "
                f"({summary['plan_type']})."
            )
        else:
            body = (
                f"<!-- {marker} -->\n"
                f"Live breaking deletion Slack alert sent for `{summary['asset_slug']}`."
            )
        post_pr_comment(pr_number, body)
    return 0


def command_planned_breaking_alert(args: argparse.Namespace) -> int:
    base_args = breaking_alert_base_args(
        phase="planned",
        plan_type=args.plan_type,
        plan_json=args.plan_json,
        run_url=None,
        pr_number=os.environ["PR_NUMBER"],
        pr_url=os.environ["PR_URL"],
        include_current_row=False,
        include_schema_results=args.plan_type == "publish",
    )
    subprocess.run(base_args + ["--summary-json", args.summary_json, "--dry-run"], check=True)
    return 0


def command_send_planned_breaking_alerts(args: argparse.Namespace) -> int:
    def post_if_needed(summary_path: str, plan_path: str) -> None:
        path = pathlib.Path(summary_path)
        if not path.exists():
            return
        summary = json.loads(path.read_text())
        if not summary.get("has_breaking_changes"):
            print(f"No breaking changes in {summary_path}; skipping Slack.")
            return
        marker = summary["marker"]
        if not marker.startswith(BREAKING_ALERT_MARKER_PREFIX):
            raise ValueError(f"unexpected breaking alert marker: {marker}")
        pr_number = os.environ["PR_NUMBER"]
        if pr_comment_has_marker(pr_number, marker):
            print(f"Breaking alert already sent for marker {marker}; skipping Slack.")
            return
        send_args = breaking_alert_base_args(
            phase=summary["phase"],
            plan_type=summary["plan_type"],
            plan_json=plan_path,
            run_url=None,
            pr_number=pr_number,
            pr_url=os.environ["PR_URL"],
            include_current_row=False,
            include_schema_results=summary["plan_type"] == "publish",
        )
        subprocess.run(send_args, check=True)
        body = (
            f"<!-- {marker} -->\n"
            f"Breaking change Slack alert sent for `{summary['asset_slug']}` "
            f"({summary['phase']}/{summary['plan_type']})."
        )
        post_pr_comment(pr_number, body)

    post_if_needed("publish-breaking-alert.json", "publish-plan.json")
    post_if_needed("delete-breaking-alert.json", "delete-plan.json")
    return 0


def release_path_for(destinations: list[str]) -> str:
    for uri in destinations:
        prefix, marker, suffix = uri.partition("/releases/")
        if not marker:
            continue
        release_date = suffix.split("/", 1)[0]
        if release_date:
            return f"{prefix}/releases/{release_date}/"
    return ""


def command_upload_summary(args: argparse.Namespace) -> int:
    plan = load_plan(args.plan_json)
    asset_slug = plan["asset_slug"]
    row = catalog_row(asset_slug)
    if row is None:
        print(f"Skipping dataset upload summary because {asset_slug!r} is not a catalog asset.")
        return 0

    destinations = [promotion["destination_uri"] for promotion in plan["promotions"]]
    canonical_path = row.get("canonical_path", "")
    canonical_promotion = next(
        (
            promotion
            for promotion in plan["promotions"]
            if promotion["destination_uri"] == canonical_path
        ),
        None,
    )
    new_dataset = bool(
        canonical_promotion
        and not str(canonical_promotion.get("destination_generation", "")).strip()
    )
    summary_args = [
        "uv",
        "run",
        "python",
        "scripts/dataset_alerts.py",
        "upload-summary",
        "--asset-slug",
        asset_slug,
    ]
    if new_dataset:
        summary_args.append("--new-dataset")
    for destination in destinations:
        summary_args.extend(["--changed-path", destination])
    release_path = release_path_for(destinations)
    if release_path:
        summary_args.extend(["--release-path", release_path])

    bucket = bucket_name()
    schema_promotion = next(
        (
            promotion
            for promotion in plan["promotions"]
            if is_schema_target(asset_slug, promotion["destination_uri"], bucket=bucket, row=row)
        ),
        None,
    )
    if schema_promotion is None:
        subprocess.run(summary_args, check=True)
        return 0

    filename = schema_promotion["destination_uri"].rsplit("/", 1)[-1]
    with tempfile.TemporaryDirectory() as tmp:
        dataset_path = pathlib.Path(tmp) / filename
        download_promotion_source(schema_promotion, dataset_path)
        subprocess.run(summary_args + ["--dataset-path", str(dataset_path)], check=True)
    return 0


def delete_object_with_verification(uri: str, generation: str, *, exists_label: str, verify_label: str) -> int:
    """Delete an object by generation and verify its absence. Returns an exit code."""
    delete_args = gcs_asset_args("delete", uri, "--generation", generation, "--confirm", "DELETE")
    print("running:", " ".join(shlex.quote(part) for part in delete_args))
    subprocess.run(delete_args, check=True)
    exists = subprocess.run(gcs_asset_args("exists", uri), check=False)
    if exists.returncode == 0:
        print(f"{exists_label}: {uri}", file=sys.stderr)
        return 1
    if exists.returncode != 1:
        print(f"{verify_label}: {uri}", file=sys.stderr)
        return exists.returncode
    return 0


def command_delete_scratch_sources(args: argparse.Namespace) -> int:
    plan = load_plan(args.plan_json)
    seen = set()
    promotions = []
    for promotion in plan["promotions"]:
        key = (promotion["source_uri"], promotion["source_generation"])
        if key in seen:
            continue
        seen.add(key)
        promotions.append(promotion)

    for index, promotion in enumerate(promotions, start=1):
        print(f"Deleting promoted scratch source {index} of {len(promotions)}: {promotion['source_uri']}")
        exit_code = delete_object_with_verification(
            promotion["source_uri"],
            promotion["source_generation"],
            exists_label="deleted scratch source still exists",
            verify_label="could not verify scratch source absence",
        )
        if exit_code != 0:
            return exit_code
    return 0


def command_delete_canonical_objects(args: argparse.Namespace) -> int:
    plan = load_plan(args.plan_json)
    for index, deletion in enumerate(plan["deletions"], start=1):
        print(f"Deleting object {index} of {len(plan['deletions'])}: {deletion['uri']}")
        subprocess.run(gcs_asset_args("stat", deletion["uri"]), check=True)
        exit_code = delete_object_with_verification(
            deletion["uri"],
            deletion["generation"],
            exists_label="deleted object still exists",
            verify_label="could not verify deleted object absence",
        )
        if exit_code != 0:
            return exit_code
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    check_review = subparsers.add_parser(
        "check-approved-review",
        help=f"Require an APPROVED review from {REQUIRED_REVIEWER} in a reviews JSON payload.",
    )
    check_review.add_argument("--reviews-json", required=True)
    check_review.set_defaults(func=command_check_approved_review)

    collect_rows = subparsers.add_parser(
        "collect-catalog-rows",
        help="Write current/proposed catalog row JSON for the merged PR's plan asset.",
    )
    collect_rows.add_argument("--event-path", required=True)
    collect_rows.set_defaults(func=command_collect_catalog_rows)

    collect_proposed = subparsers.add_parser(
        "collect-proposed-catalog-row",
        help="Write the proposed catalog row JSON from a PR head commit.",
    )
    collect_proposed.add_argument("--head-sha", required=True)
    collect_proposed.set_defaults(func=command_collect_proposed_catalog_row)

    validate_paths = subparsers.add_parser(
        "validate-plan-paths",
        help="Validate plan destination/target URIs against bucket layout rules.",
    )
    validate_paths.add_argument("--plan-type", required=True, choices=["publish", "delete"])
    validate_paths.add_argument("--plan-json", required=True)
    validate_paths.set_defaults(func=command_validate_plan_paths)

    detect_targets = subparsers.add_parser(
        "detect-schema-targets",
        help="Report whether a publish plan has schema-bearing replacement targets.",
    )
    detect_targets.add_argument("--plan-json", required=True)
    detect_targets.add_argument("--github-output", required=True)
    detect_targets.set_defaults(func=command_detect_schema_targets)

    check_schema = subparsers.add_parser(
        "check-schema-compatibility",
        help="Run schema compatibility checks for schema-bearing plan promotions.",
    )
    check_schema.add_argument("--plan-json", required=True)
    check_schema.add_argument("--phase", required=True, choices=["live", "planned"])
    check_schema.set_defaults(func=command_check_schema_compatibility)

    promote = subparsers.add_parser(
        "promote",
        help="Promote approved staged objects with generation preconditions. Run under uv.",
    )
    promote.add_argument("--plan-json", required=True)
    promote.set_defaults(func=command_promote)

    rebuild_index = subparsers.add_parser(
        "rebuild-release-index",
        help="Rebuild release indexes for the plan asset and requested catalog assets.",
    )
    rebuild_index.add_argument("--plan-json", required=True)
    rebuild_index.set_defaults(func=command_rebuild_release_index)

    live_alert = subparsers.add_parser(
        "live-breaking-alert",
        help="Send the live breaking change Slack alert with PR comment dedup.",
    )
    live_alert.add_argument("--plan-type", required=True, choices=["publish", "delete"])
    live_alert.add_argument("--plan-json", required=True)
    live_alert.add_argument("--summary-json", required=True)
    live_alert.set_defaults(func=command_live_breaking_alert)

    planned_alert = subparsers.add_parser(
        "planned-breaking-alert",
        help="Summarize a planned breaking change alert as JSON (dry run).",
    )
    planned_alert.add_argument("--plan-type", required=True, choices=["publish", "delete"])
    planned_alert.add_argument("--plan-json", required=True)
    planned_alert.add_argument("--summary-json", required=True)
    planned_alert.set_defaults(func=command_planned_breaking_alert)

    send_planned = subparsers.add_parser(
        "send-planned-breaking-alerts",
        help="Send planned breaking alerts from summary JSON files with PR comment dedup.",
    )
    send_planned.set_defaults(func=command_send_planned_breaking_alerts)

    upload_summary = subparsers.add_parser(
        "upload-summary",
        help="Send the dataset upload summary for a promoted publish plan.",
    )
    upload_summary.add_argument("--plan-json", required=True)
    upload_summary.set_defaults(func=command_upload_summary)

    delete_scratch = subparsers.add_parser(
        "delete-scratch-sources",
        help="Delete promoted scratch source objects and verify their absence.",
    )
    delete_scratch.add_argument("--plan-json", required=True)
    delete_scratch.set_defaults(func=command_delete_scratch_sources)

    delete_canonical = subparsers.add_parser(
        "delete-canonical-objects",
        help="Delete approved canonical objects and verify their absence.",
    )
    delete_canonical.add_argument("--plan-json", required=True)
    delete_canonical.set_defaults(func=command_delete_canonical_objects)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
