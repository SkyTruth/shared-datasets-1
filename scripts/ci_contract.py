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
from scripts.ci_source_proof import job_name
from scripts.ci_toolchain import TOOLCHAIN
from scripts.catalog_csv import read_catalog_rows_text
from scripts.tested_image_bundle import ImageError, TARGETS as IMAGE_TARGETS, validate_manifest


SUITES = (
    "lint", "tests", "geospatial-integration", "production-images", "sdk-node22", "sdk-node24", "browser",
)
DEPLOYMENTS = (
    "eamlis", "wdpa", "sea_ice", "wdpa_processing", "ingestion_iam",
    "pmtiles_cdn", "catalog_viewer", "dataset_usage", "artifact_registry_iam", "preview_terraform_iam",
    "scratch_cleanup_iam", "cron_alert_policy",
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
    "scripts/ci_contract.py", "scripts/ci_preflight.py", "scripts/ci_install_tools.py", "scripts/ci_toolchain.py",
    ".github/docker/preflight.Dockerfile", ".github/docker/geospatial-ci.Dockerfile",
    "pyproject.toml", "uv.lock", "api/typescript/package-lock.json",
    "tests/browser/package-lock.json", "scripts/check_geospatial_test_results.py",
    "scripts/check_workflow_syntax.py",
    ".claude/skills/shared-datasets-compliance-audit/scripts/audit_shared_datasets.py",
    "scripts/check_identity_resolutions.py",
    "scripts/ci_source_proof.py", "scripts/ci_runtime.py", "scripts/ci_host_runtime.py",
    ".github/actions/ci-tools/action.yml", ".github/workflows/ci.yml",
    "scripts/release_contracts.py", "scripts/deployment_permissions.py",
    "scripts/wdpa_staged_image_readiness.py", "scripts/wdpa_processing_gate.py",
    "scripts/production_image_contracts.py",
    "scripts/tested_image_bundle.py", "scripts/tested_image_authorization.py",
    "scripts/dataset_usage_deploy.py",
    "scripts/scheduled_job_contracts.py",
    "scripts/cdn_plan_readiness.py",
    "scripts/terraform_plan_permissions.py", "scripts/catalog_csv.py",
    "scripts/deployment_emission.py", "scripts/install_deployment_verifier.py",
    ".github/actions/deployment-receipt/action.yml",
)


def select_suites(paths: list[str] | None) -> tuple[list[str], str]:
    if paths is None:
        return list(SUITES), "comparison history unavailable"
    selected = set(ALWAYS)
    for path in paths:
        if path in {"pyproject.toml", "uv.lock"} or path.startswith(".github/"):
            return list(SUITES), f"shared validation or workflow dependency: {path}"
        if path.startswith("ingestion/") or path in NATIVE_TESTS:
            selected.update({"geospatial-integration", "production-images"})
        elif path.startswith("api/"):
            selected.update({"sdk-node22", "sdk-node24", "browser"})
            if path.startswith("api/python/"):
                selected.add("production-images")
        elif path.startswith("services/catalog_viewer/"):
            selected.update({"production-images", "browser"})
        elif path.startswith(("web/", "tests/browser/", "catalog/", "templates/", "docs/assets/")):
            selected.update({"sdk-node22", "sdk-node24", "browser"})
            if path.startswith("catalog/") or path == "docs/assets/ims-sea-ice-extent.md":
                selected.update({"geospatial-integration", "production-images"})
        elif path.startswith("scripts/"):
            # Scripts are imported by runtime, SDK fixtures, and browser rendering.
            # An explicit narrower rule must prove those dependencies absent.
            return list(SUITES), f"shared script dependency: {path}"
        elif path.startswith("tests/"):
            if any(word in path for word in ("geospatial", "raster", "wdpa", "sea_ice", "eamlis", "translation")):
                selected.update({"geospatial-integration", "production-images"})
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


def catalog_deployment_targets(before: str, after: str) -> set[str]:
    """Compare the catalog fields actually compiled into release consumers."""
    def contracts(raw):
        rows = read_catalog_rows_text(raw)
        by_slug = {}
        for row in rows:
            slug = row.get("asset_slug")
            if not slug or slug in by_slug:
                raise ValueError("catalog release comparison requires unique nonempty asset slugs")
            by_slug[slug] = row
        def translation(slug):
            row = by_slug.get(slug)
            if row is None:
                return None
            return tuple(tuple(filter(None, row.get(field, "").split(";"))) for field in ("translation_locales", "translation_fields"))
        routes_and_folders = frozenset(tuple(row.get(field, "") for field in (
            "asset_slug", "canonical_path", "access_tier", "status", "has_pmtiles", "available_formats",
        )) for row in rows)
        return {
            "eamlis": translation("eamlis-abandoned-mine-land-inventory"),
            "wdpa": (translation("wdpa-marine"), translation("wdpa-terrestrial")),
            "pmtiles_cdn": routes_and_folders,
            "dataset_usage": routes_and_folders,
        }
    previous, current = contracts(before), contracts(after)
    return {target for target in previous if previous[target] != current[target]}


def select_deployments(paths: list[str] | None, *, catalog_snapshots: tuple[str, str] | None = None) -> list[str]:
    """Explicit release dependencies; unknown changes broaden tests, not mutations."""
    selected = set()
    iam = {"artifact_registry_iam", "preview_terraform_iam", "scratch_cleanup_iam", "cron_alert_policy", "ingestion_iam"}
    ingestion = {"eamlis", "sea_ice", "wdpa"}
    for path in paths or []:
        if path.startswith(("terraform/modules/cloud_run_job/", "terraform/modules/scheduler_job/", "terraform/modules/service_account/")):
            selected.update(ingestion | {"catalog_viewer", "dataset_usage"})
        if path in {"pyproject.toml", "uv.lock"}:
            selected.update(ingestion | {"catalog_viewer", "dataset_usage"})
        if path.startswith("ingestion/common/") or path in {
            "scripts/release_feature_model.py", "scripts/vector_asset.py", "scripts/raster_asset.py",
            "scripts/feature_metadata_localization.py", "scripts/translation_local_io.py",
            "scripts/pmtiles_zoom.py", "scripts/slack_notify.py",
        }:
            selected.update(ingestion)
        if path == "scripts/catalog_csv.py":
            selected.update({"eamlis", "wdpa", "dataset_usage"})
        if path == "catalog/shared-datasets-catalog.csv":
            selected.update(catalog_deployment_targets(*catalog_snapshots) if catalog_snapshots is not None else {"eamlis", "wdpa", "pmtiles_cdn", "dataset_usage"})
        if path == "scripts/feature_metadata_translation_reuse.py":
            selected.update({"eamlis", "wdpa"})
        if path.startswith("catalog/feature-identity-resolutions/"):
            selected.update({"sea_ice", "wdpa"})
        if path == "docs/assets/ims-sea-ice-extent.md":
            selected.add("sea_ice")
        if path.startswith("ingestion/eamlis_monthly/") or path in {
            ".github/workflows/eamlis-monthly-deploy.yml", "terraform/envs/prod/eamlis_monthly.tf",
        }:
            selected.add("eamlis")
        if path.startswith("ingestion/sea_ice_daily/") or path in {
            ".github/workflows/sea-ice-daily-deploy.yml", "terraform/envs/prod/sea_ice_daily.tf",
        }:
            selected.add("sea_ice")
        if (path.startswith("ingestion/wdpa_monthly/") and path not in {
            "ingestion/wdpa_monthly/Dockerfile", "ingestion/wdpa_monthly/README.md",
        }) or path in {
            ".github/workflows/wdpa-monthly-deploy.yml", "terraform/envs/prod/wdpa_monthly.tf",
        }:
            selected.add("wdpa")
        # New producer bytes require their own complete retained evidence. A
        # producer/bootstrap change cannot launch an unready publication job.
        if path == "catalog/wdpa-staged-validation.json":
            selected.add("wdpa_processing")
        if path.startswith("api/python/src/") or path.startswith("services/catalog_viewer/") or path in {
            ".github/workflows/catalog-viewer-deploy.yml", "terraform/envs/prod/catalog_viewer.tf",
            "terraform/envs/prod/catalog_viewer_variables.tf",
            "scripts/compare_releases.py", "scripts/release_feature_model.py",
            "terraform/envs/prod/main.tf", "terraform/envs/prod/variables.tf", "terraform/envs/prod/versions.tf",
            "terraform/envs/prod/pmtiles_cdn.tf", "terraform/envs/prod/pmtiles_cdn_variables.tf",
            "terraform/envs/prod/canonical_mutation_iam.tf", "catalog/categories.yaml",
        }:
            selected.add("catalog_viewer")
        if path.startswith("terraform/modules/pmtiles-cdn/") or path in {
            ".github/workflows/pmtiles-cdn-sync.yml", "terraform/envs/prod/pmtiles_cdn.tf",
            "terraform/envs/prod/pmtiles_cdn_variables.tf", "scripts/pmtiles_cdn_sync.py",
            "terraform/envs/prod/shared_bucket_public.tf",
            "terraform/envs/prod/variables.tf", "terraform/envs/prod/versions.tf",
        }:
            selected.add("pmtiles_cdn")
        if path in {".github/workflows/scheduled-ingestion-deploy-iam-sync.yml", "terraform/envs/prod/scheduled_ingestion_deploy_iam.tf"}:
            selected.add("ingestion_iam")
        for target, filename in {
            "artifact_registry_iam": "artifact_registry_iam.tf",
            "preview_terraform_iam": "preview_terraform_iam.tf",
            "scratch_cleanup_iam": "canonical_mutation_iam.tf",
        }.items():
            if path == f"terraform/envs/prod/{filename}" or path == f".github/workflows/{target.replace('_', '-')}-sync.yml":
                selected.add(target)
        if path in {
            ".github/workflows/cron-alert-policy-sync.yml", "terraform/envs/prod/canonical_mutation_iam.tf",
            "terraform/envs/prod/monitoring.tf", "terraform/envs/prod/monitoring_alert_policy_iam.tf",
            "terraform/envs/prod/monitoring_variables.tf", "terraform/envs/prod/wdpa_execution_observer.tf",
        }:
            selected.add("cron_alert_policy")
        if path == "catalog/categories.yaml":
            selected.update({"scratch_cleanup_iam", "cron_alert_policy"})
        if path in {".github/workflows/prod-terraform-target-apply.yml", "terraform/envs/prod/variables.tf", "terraform/envs/prod/versions.tf"}:
            selected.update(iam)
        if path == "terraform/envs/prod/main.tf":
            selected.update({"artifact_registry_iam", "preview_terraform_iam"})
        if path.startswith("ingestion/dataset_usage/") or path in {
            ".github/workflows/dataset-usage-deploy.yml", "terraform/envs/prod/dataset_usage.tf", "terraform/envs/prod/shared_bucket_public.tf",
            "catalog/dataset-usage.json", "catalog/dataset-usage-activation.json", "scripts/dataset_usage_deploy.py",
        }:
            selected.add("dataset_usage")
    # Viewer infrastructure reads the usage state bucket. Deploy usage first;
    # this also gives the worker the current catalog baked into its tested image.
    if "catalog_viewer" in selected:
        selected.add("dataset_usage")
    if selected & ingestion:
        selected.add("ingestion_iam")
    if selected & (ingestion | {"catalog_viewer", "wdpa_processing", "dataset_usage"}):
        selected.add("artifact_registry_iam")
    return [target for target in DEPLOYMENTS if target in selected]


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
        if not all(identity):
            raise ValueError("test result is missing its case identity")
        if case.find("failure") is not None or case.find("error") is not None:
            raise ValueError(f"failed test: {'.'.join(identity)}")
        if case.find("skipped") is not None and identity not in allowed_skips:
            raise ValueError(f"unexpected skipped test: {'.'.join(identity)}")


def verify_results(
    plan: dict, results: list[dict], jobs: dict | None = None, *,
    source_proofs: dict[int, set[str]] | None = None,
    run_id: str | None = None, run_attempt: int | None = None,
) -> dict[str, dict]:
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
    seen_attempts = set()
    for result in results:
        suite = result.get("suite")
        if suite not in selected:
            raise ValueError(f"duplicate or unexpected suite result: {suite}")
        if source_proofs is not None:
            source = result.get("source", {})
            attempt = source.get("run_attempt")
            if source.get("run_id") != run_id or type(attempt) is not int or not 1 <= attempt <= run_attempt:
                raise ValueError(f"invalid {suite} source attempt")
            key = (suite, attempt)
            if key in seen_attempts:
                raise ValueError(f"duplicate {suite} result in attempt {attempt}")
            seen_attempts.add(key)
            if any(result.get(field) != plan.get(field) for field in ("base", "head", "tested_sha", "tree", "contract_digest")):
                raise ValueError(f"stale or mismatched {suite} evidence")
            if suite in by_suite and by_suite[suite]["source"]["run_attempt"] > attempt:
                continue
        elif suite in by_suite:
            raise ValueError(f"duplicate or unexpected suite result: {suite}")
        by_suite[suite] = result
    if set(by_suite) != set(selected):
        raise ValueError(f"missing suite results: {sorted(set(selected) - set(by_suite))}")
    identity = ("base", "head", "tested_sha", "tree", "contract_digest")
    for suite, result in by_suite.items():
        if source_proofs is not None and job_name(suite) not in source_proofs.get(result["source"]["run_attempt"], set()):
            raise ValueError(f"{suite} has no successful producing job in its source attempt")
        if result.get("status") != "success" or not result.get("commands"):
            raise ValueError(f"{suite} did not execute successfully")
        if any(result.get(field) != plan.get(field) for field in identity):
            raise ValueError(f"stale or mismatched {suite} evidence")
        if any(command.get("exit_code") != 0 for command in result["commands"]):
            raise ValueError(f"{suite} contains a failed command")
        if result.get("tools") != expected_tools(suite):
            raise ValueError(f"{suite} used an unexpected toolchain")
        if suite == "production-images":
            try:
                images = validate_manifest(result.get("production_images"), plan["tested_sha"])
            except ImageError as exc:
                raise ValueError(f"invalid production-image evidence: {exc}") from exc
            if set(images) != IMAGE_TARGETS:
                raise ValueError("complete tested production-image set is missing")
    if jobs is not None:
        expected = {suite: suite for suite in ("lint", "tests", "geospatial-integration", "production-images", "browser")}
        for suite, job in expected.items():
            required = "success" if suite in selected else "skipped"
            if jobs.get(job, {}).get("result") != required:
                raise ValueError(f"job {job}: expected {required}, got {jobs.get(job, {}).get('result')}")
        sdk_required = "success" if "sdk-node22" in selected else "skipped"
        if jobs.get("sdk-validation", {}).get("result") != sdk_required:
            raise ValueError("SDK matrix failed, was cancelled, missing, or unexpectedly skipped")
        if jobs.get("geospatial-changes", {}).get("result") != "success":
            raise ValueError("validation selection did not succeed")
    return by_suite


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
    elif suite == "production-images":
        pass  # Docker availability and the actual installed image tools are probed by the command.
    else:
        raise ValueError(f"unknown suite: {suite}")
    return tools


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
