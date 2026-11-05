"""The validation contract shared by local preflight and GitHub Actions.

Suite selection is deliberately conservative: a path without a known dependency
rule selects every suite. Results are evidence for an exact tree and contract,
not permission to publish or deploy.
"""

from __future__ import annotations

import ast
from functools import lru_cache
from fnmatch import fnmatchcase
import hashlib
import shlex
import json
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree

from scripts.check_geospatial_test_results import REQUIRED_TESTS
from scripts.ci_source_proof import job_name
from scripts.ci_toolchain import TOOLCHAIN
from scripts.ingestion_targets import TARGETS as INGESTION_CONFIGS
from scripts.catalog_csv import read_catalog_rows_text
from scripts.tested_image_bundle import BUILD_TARGETS, RECIPES, ROOT, ImageError, TARGETS as IMAGE_TARGETS, validate_manifest


SUITES = (
    "lint", "tests", "geospatial-integration", "production-images", "sdk-node22", "sdk-node24", "browser",
)
DEPLOYMENTS = (
    "mpatlas", "eamlis", "wdpa", "sea_ice", "ingestion_iam",
    "pmtiles_cdn", "catalog_viewer", "dataset_usage", "artifact_registry_iam", "preview_terraform_iam",
    "scratch_cleanup_iam", "cron_alert_policy",
)
INGESTION_DEPLOYMENTS = frozenset(config["selection"] for config in INGESTION_CONFIGS.values())
DEPLOYMENT_IMAGES = {
    **{config["selection"]: target for target, config in INGESTION_CONFIGS.items()}, "catalog_viewer": "catalog-viewer", "dataset_usage": "dataset-usage",
    "cron_alert_policy": "monitoring-slack",
}
INGESTION_ROLLOUT_HOLDS = ".github/ingestion-rollout-holds.json"
ALWAYS = {"lint", "tests"}


def rollout_holds(value, *, historical=False, previous=None, now=None, targets=INGESTION_DEPLOYMENTS) -> tuple[str, ...]:
    # Old commits used bare target names. Only comparison baselines may read that
    # persisted format; all new plans require accountable, bounded holds.
    if historical and isinstance(value, list) and all(isinstance(row, str) for row in value):
        value = dict.fromkeys(value)
    if not isinstance(value, dict) or not set(value) <= set(targets):
        raise ValueError("rollout holds must map scheduled deployment targets to owner, reason and expires_at")
    if not historical:
        owners = {word.removeprefix("@") for line in (Path(__file__).resolve().parents[1] / ".github/CODEOWNERS").read_text().splitlines()
                  if line.strip() and not line.lstrip().startswith("#") for word in line.split()[1:]}
        for target, hold in value.items():
            if (not isinstance(hold, dict) or set(hold) != {"owner", "reason", "expires_at"}
                    or any(not isinstance(item, str) or not item.strip() for item in hold.values())):
                raise ValueError(f"{target}: rollout hold requires owner, reason and expires_at")
            expiry = datetime.fromisoformat(hold["expires_at"].replace("Z", "+00:00"))
            if expiry.tzinfo is None or hold["owner"] not in owners:
                raise ValueError(f"{target}: rollout hold requires a timezone and CODEOWNER")
            if previous is not None and hold != (previous.get(target) if isinstance(previous, dict) else None) and expiry <= (now or datetime.now(timezone.utc)):
                raise ValueError(f"{target}: changed rollout hold is already expired")
    return tuple(value)
NATIVE_TESTS = (
    "tests/test_feature_metadata_localization.py",
    "tests/test_translation_local_io.py",
    "tests/test_raster_standards.py",
    "tests/test_wdpa_monthly.py",
    "tests/test_wdpa_disk_processing.py",
    "tests/test_sea_ice_daily.py",
    "tests/test_eamlis_monthly.py",
    "tests/test_mpatlas_monthly.py",
)
CONTRACT_FILES = (
    "ingestion/common/stage_checkpoints.py",
    "scripts/publication_runtime_check.py", "scripts/publication_readiness.py",
    "scripts/ci_contract.py", "scripts/ci_preflight.py", "scripts/ci_install_tools.py", "scripts/ci_toolchain.py",
    ".github/docker/preflight.Dockerfile", ".github/docker/geospatial-ci.Dockerfile",
    "pyproject.toml", "uv.lock", "api/typescript/package-lock.json",
    "tests/browser/package-lock.json", "scripts/check_geospatial_test_results.py",
    "scripts/check_workflow_syntax.py",
    ".claude/skills/shared-datasets-compliance-audit/scripts/audit_shared_datasets.py",
    "scripts/check_identity_resolutions.py",
    "scripts/ci_source_proof.py",
    "scripts/sdk_release_authorization.py", "scripts/dataset_mutation_authorization.py",
    "scripts/reviewed_dataset_plan.py", "scripts/github_api_read.py",
    "scripts/deployment_revision.py", "scripts/workflow_identity.py",
    ".github/actions/ci-tools/action.yml", ".github/workflows/ci.yml",
    "scripts/release_contracts.py", "scripts/deployment_permissions.py",
    "scripts/wdpa_processing_gate.py",
    "scripts/production_image_contracts.py",
    "scripts/dataset_usage_cold_start.py",
    "scripts/terraform_target_contracts.py", "terraform/iam-plan-prerequisites.json",
    ".github/terraform-targets.json", "scripts/ingestion_targets.py", "scripts/ingestion_verification.py",
    ".github/actions/deployment-bootstrap/action.yml",
    "tests/fixtures/terraform_targets/failed-main.json",
    "tests/fixtures/terraform_targets/shared_bucket_public.failed.tf",
    "scripts/tested_image_bundle.py", "scripts/tested_image_authorization.py",
    "scripts/dataset_usage_deploy.py",
    "scripts/scheduled_job_contracts.py",
    "scripts/cdn_plan_readiness.py",
    "scripts/terraform_plan_permissions.py", "scripts/catalog_csv.py",
    "scripts/deployment_emission.py", "scripts/install_deployment_verifier.py",
    ".github/actions/deployment-receipt/action.yml",
    ".github/workflows/ingestion-deploy.yml", "scripts/deployment_receipt_contracts.py",
)


@lru_cache(maxsize=1)
def dependency_inputs():
    """Generate dependencies from local imports, script calls and Docker COPY.

    Include package initializers, relative imports and literal subprocess paths.
    Directory COPY declarations remain prefixes, including newly added files.
    Parsing errors fail selection rather than silently omitting a dependency.
    """
    sources = {path.relative_to(ROOT).as_posix(): path for directory in
               ("scripts", "ingestion", "services", "api/python/src", "tests")
               for path in (ROOT / directory).rglob("*.py") if "__pycache__" not in path.parts}
    modules = {name.removeprefix("api/python/src/").removesuffix(".py").replace("/", "."): name
               for name in sources}
    modules.update({Path(name).stem: name for name in sources if name.startswith("tests/") and name.count("/") == 1})
    edges = {}
    for name, path in sources.items():
        dependencies = set()
        package = name.removeprefix("api/python/src/").split("/")[:-1]
        for node in ast.walk(ast.parse(path.read_text(), filename=name)):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                prefix = ".".join(package[:len(package) - node.level + 1]) if node.level else ""
                module = ".".join(filter(None, (prefix, node.module)))
                names = [module, *(f"{module}.{alias.name}" for alias in node.names)]
            elif isinstance(node, ast.Call):
                function = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
                if function in {"run", "run_command", "Popen", "check_output", "check_call"}:
                    dependencies.update(item.value for arg in node.args for item in ast.walk(arg)
                                        if isinstance(item, ast.Constant) and isinstance(item.value, str) and item.value in sources)
                elif function == "import_module" and node.args and isinstance(node.args[0], ast.Constant):
                    names = [node.args[0].value]
            for module in names:
                parts = module.split(".")
                for index in range(1, len(parts) + 1):
                    prefix = ".".join(parts[:index])
                    dependencies.update(modules[key] for key in (prefix, prefix + ".__init__") if key in modules)
        edges[name] = dependencies

    def closure(roots):
        seen, pending = set(), list(roots)
        while pending:
            name = pending.pop()
            if name not in seen:
                seen.add(name)
                pending.extend(edges.get(name, ()))
        return seen

    images, copies = {}, {}
    for target, recipe in RECIPES.items():
        copied = {recipe, ".dockerignore"}
        for line in (ROOT / recipe).read_text().replace("\\\n", " ").splitlines():
            operation, _, arguments = line.strip().partition(" ")
            if operation.upper() != "COPY" or arguments.startswith("--from="):
                continue
            words = json.loads(arguments) if arguments.startswith("[") else shlex.split(arguments, comments=True)
            if len(words) < 2 or any(word.startswith("--") or "$" in word for word in words):
                raise ValueError(f"unsupported COPY inputs in {recipe}: {arguments}")
            for source in words[:-1]:
                source = source.removeprefix("./").rstrip("/")
                source = "" if source == "." else source
                copied.add(source + "/" if (ROOT / source).is_dir() else source)
        copies[target] = set(copied)
        fixture = f"tests/fixtures/production_images/{target}.json"
        if (ROOT / fixture).is_file():
            copied.add(fixture)
        copied.add(f"tests/test_{target.replace('-', '_')}.py")
        images[target] = copied | closure(name for name in sources if matches(name, copied) and not name.startswith("tests/"))
    native = closure(NATIVE_TESTS)
    native.update({name for name, inputs in edges.items() if name.startswith("tests/")
                   and inputs & {item for item in native if not item.endswith("/__init__.py")}})
    browser = closure(name for name in sources if name.startswith("tests/browser/"))
    return images, native, browser, sources.keys(), copies


def matches(path, inputs):
    return any((item == "/" or path.startswith(item)) if item.endswith("/") else fnmatchcase(path, item) for item in inputs)


def classify_change(path: str) -> tuple[str, set[str]]:
    """Runtime selection follows generated dependencies; unknown paths stay broad."""
    if (path.startswith("scripts/ci_") or path in {
            "pyproject.toml", "uv.lock", ".github/workflows/ci.yml", ".github/actions/ci-tools/action.yml",
            ".github/docker/preflight.Dockerfile", ".github/docker/geospatial-ci.Dockerfile",
            "scripts/production_image_contracts.py", "scripts/tested_image_bundle.py", "terraform/iam-plan-prerequisites.json",
        } or path.startswith("tests/fixtures/") and path.endswith(".md")):
        return "shared", set(SUITES)
    if path.startswith(("catalog/", "templates/", "docs/assets/")):
        suites = {"sdk-node22", "sdk-node24", "browser"}
        if path.startswith("catalog/") or any(matches(path, inputs) for inputs in dependency_inputs()[0].values()):
            suites.add("geospatial-integration")
        return "metadata", suites
    if path == "api/typescript/README.md":
        return "component", {"sdk-node22", "sdk-node24", "browser"}
    if path.endswith(".md") or path == "LICENSE":
        return "documentation", set()
    images, native, browser, sources, _ = dependency_inputs()
    suites = set()
    if any(matches(path, inputs) for inputs in images.values()):
        suites.add("production-images")
    if path in native or path in NATIVE_TESTS or path.startswith("ingestion/") or any(
            path.startswith(f"tests/test_{target.replace('-', '_')}") for target in INGESTION_CONFIGS):
        suites.add("geospatial-integration")
    if path in browser or path.startswith(("services/catalog_viewer/", "api/", "web/", "tests/browser/")):
        suites.add("browser")
    if path.startswith(("api/", "web/", "tests/browser/")) or path == "scripts/catalog_web_publish.py":
        suites.update({"sdk-node22", "sdk-node24", "browser"})
    if suites or path in sources or path.startswith(("ingestion/", "services/", "api/", "web/", "tests/browser/")):
        return "component", suites
    if path.startswith(".github/") and (ROOT / path).is_file():
        return "component", suites
    if path.startswith("terraform/") or path in {".gitignore", ".gitleaksignore"}:
        return "infrastructure", suites
    return "unknown", set(SUITES)


def documentation_only(paths: list[str] | None) -> bool:
    return paths is not None and all(classify_change(path)[0] == "documentation" for path in paths)


def select_suites(paths: list[str] | None) -> tuple[list[str], str]:
    if paths is None:
        return list(SUITES), "comparison history unavailable"
    selected, tiers = set(ALWAYS), set()
    for path in paths:
        tier, suites = classify_change(path)
        if tier in {"shared", "unknown"}:
            return list(SUITES), f"{tier} dependency: {path}"
        selected.update(suites)
        tiers.add(tier)
    if select_production_images(paths):
        selected.add("production-images")
    else:
        selected.discard("production-images")
    return [suite for suite in SUITES if suite in selected], "tiers: " + ", ".join(sorted(tiers))


def select_production_images(paths: list[str] | None) -> list[str]:
    """Select COPY/import consumers plus the exact-image deployment floor."""
    if paths is None:
        return list(BUILD_TARGETS)
    if not isinstance(paths, list) or any(not isinstance(path, str) for path in paths):
        raise ValueError("image selection requires a changed-path list")
    inputs = dependency_inputs()[0]
    selected = set()
    for path in paths:
        tier, _ = classify_change(path)
        if tier in {"shared", "unknown"}:
            return list(BUILD_TARGETS)
        if tier != "documentation":
            selected.update(target for target, copied in inputs.items() if matches(path, copied))
    selected.update(DEPLOYMENT_IMAGES[target] for target in select_deployments(paths) if target in DEPLOYMENT_IMAGES)
    return [target for target in BUILD_TARGETS if target in selected]


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


def select_deployments(
    paths: list[str] | None,
    *,
    catalog_snapshots: tuple[str, str] | None = None,
    ingestion_holds: tuple[str, ...] = (),
    released_holds: tuple[str, ...] = (),
) -> list[str]:
    """Explicit release dependencies; unknown changes broaden tests, not mutations."""
    selected = set(released_holds)
    iam = {"artifact_registry_iam", "preview_terraform_iam", "scratch_cleanup_iam", "cron_alert_policy", "ingestion_iam"}
    ingestion = INGESTION_DEPLOYMENTS
    for path in paths or []:
        if classify_change(path)[0] == "documentation":
            continue
        if path.startswith(("terraform/modules/cloud_run_job/", "terraform/modules/scheduler_job/", "terraform/modules/service_account/")):
            selected.update(ingestion | {"catalog_viewer", "dataset_usage"})
        if path in {"pyproject.toml", "uv.lock"}:
            selected.update(ingestion | {"catalog_viewer", "dataset_usage", "cron_alert_policy"})
        if path in {".github/workflows/ingestion-deploy.yml", "scripts/ingestion_targets.py", "scripts/ingestion_verification.py"}:
            selected.update(ingestion)
        if path == ".github/actions/deployment-bootstrap/action.yml":
            selected.update(ingestion | {"dataset_usage"})
        if path.startswith("scripts/"):
            selected.update(selection for selection, image in DEPLOYMENT_IMAGES.items()
                            if matches(path, dependency_inputs()[4][image]))
        if path.startswith("ingestion/common/") or path == "ingestion/__init__.py":
            selected.update(ingestion)
            if path in {"ingestion/__init__.py", "ingestion/common/__init__.py", "ingestion/common/decision_events.py"}:
                selected.add("cron_alert_policy")
        if path == "catalog/shared-datasets-catalog.csv":
            selected.update(catalog_deployment_targets(*catalog_snapshots) if catalog_snapshots is not None else {"eamlis", "wdpa", "pmtiles_cdn", "dataset_usage"})
        if path.startswith("catalog/feature-identity-resolutions/"):
            selected.update({"sea_ice", "wdpa"})
        if path == "docs/assets/ims-sea-ice-extent.md":
            selected.add("sea_ice")
        for target, config in INGESTION_CONFIGS.items():
            package = config["image_variable"].removesuffix("_image")
            if (path.startswith(f"ingestion/{package}/") or path.startswith(f"terraform/envs/prod/{package}")
                    or path == f".github/workflows/{config['workflow']}"):
                selected.add(config["selection"])
        if path == "catalog/ingestion-quality.json":
            selected.add("eamlis")
        if path == "catalog/wdpa-processing-acceptance.json":
            selected.add("wdpa")
        if path.startswith(("api/python/src/", "services/catalog_viewer/", "services/feature_preview_service/")) or path in {
            "services/__init__.py", "services/http_base.py",
            ".github/workflows/catalog-viewer-deploy.yml", "terraform/envs/prod/catalog_viewer.tf",
            "terraform/envs/prod/catalog_viewer_variables.tf",
            "terraform/envs/prod/main.tf", "terraform/envs/prod/variables.tf", "terraform/envs/prod/versions.tf",
            "terraform/envs/prod/pmtiles_cdn.tf", "terraform/envs/prod/pmtiles_cdn_variables.tf",
            "terraform/envs/prod/canonical_mutation_iam.tf", "catalog/categories.yaml",
        }:
            selected.add("catalog_viewer")
        if path in {"catalog/dataset-usage.json", "catalog/dataset-usage-activation.json"}:
            selected.add("catalog_viewer")
        if path.startswith("terraform/modules/pmtiles-cdn/") or path in {
            ".github/workflows/pmtiles-cdn-sync.yml", "terraform/envs/prod/pmtiles_cdn.tf",
            "terraform/envs/prod/pmtiles_cdn_variables.tf", "scripts/pmtiles_cdn_sync.py",
            "terraform/envs/prod/shared_bucket_public.tf",
            "terraform/envs/prod/variables.tf", "terraform/envs/prod/versions.tf",
        }:
            selected.add("pmtiles_cdn")
        if path in {".github/workflows/scheduled-ingestion-deploy-iam-sync.yml", "terraform/envs/prod/scheduled_ingestion_deploy_iam.tf", "terraform/envs/prod/wdpa_monthly_build.tf"}:
            selected.add("ingestion_iam")
        if path in {"terraform/envs/prod/wdpa_validation_retirement_iam.tf", "terraform/envs/prod/operational_retirement_iam.tf"}:
            selected.add("scratch_cleanup_iam")
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
            "terraform/envs/prod/monitoring_variables.tf",
        }:
            selected.add("cron_alert_policy")
        if path.startswith("services/monitoring_slack/") or path in {"terraform/envs/prod/monitoring_slack.tf", "scripts/monitoring_slack_prepare.py"}:
            selected.add("cron_alert_policy")
        if path == "catalog/categories.yaml":
            selected.update({"scratch_cleanup_iam", "cron_alert_policy"})
        if path in {".github/workflows/prod-terraform-target-apply.yml", "terraform/envs/prod/variables.tf", "terraform/envs/prod/versions.tf"}:
            selected.update(iam)
        if path in {".github/terraform-targets.json", "scripts/terraform_target_contracts.py"}:
            selected.update(iam | {"dataset_usage"})
        if path == "terraform/envs/prod/main.tf":
            selected.update({"artifact_registry_iam", "preview_terraform_iam"})
        if path.startswith("ingestion/dataset_usage/") or path in {
            ".github/workflows/dataset-usage-deploy.yml", "terraform/envs/prod/dataset_usage.tf", "terraform/envs/prod/shared_bucket_public.tf",
            "catalog/dataset-usage.json", "catalog/dataset-usage-activation.json", "scripts/dataset_usage_deploy.py",
        }:
            selected.add("dataset_usage")
    # Runtime deployment reuses installed IAM. Only infrastructure edits select
    # bootstrap syncs; direct IAM workflow/input rules above stay explicit.
    if any(path.startswith("terraform/") and classify_change(path)[0] != "documentation" for path in paths or []):
        # Viewer and usage share bucket/IAM resources, not runtime image inputs.
        if "catalog_viewer" in selected:
            selected.add("dataset_usage")
        if "mpatlas" in selected:
            selected.add("cron_alert_policy")
        if selected & ingestion:
            selected.add("ingestion_iam")
        if selected & (ingestion | {"catalog_viewer", "dataset_usage", "cron_alert_policy"}):
            selected.add("artifact_registry_iam")
    selected.difference_update(ingestion_holds)
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
    targets = select_production_images(plan.get("changed_paths"))
    matrix = any("image_target" in result for result in results)
    required_results = set(selected)
    if matrix and "production-images" in selected:
        required_results.remove("production-images")
        required_results.update("production-images-" + target for target in targets)
    by_suite: dict[str, dict] = {}
    seen_attempts = set()
    for result in results:
        suite = result.get("suite")
        key = suite + "-" + result["image_target"] if "image_target" in result and suite == "production-images" else suite
        if key not in required_results or "image_target" in result and suite != "production-images":
            raise ValueError(f"duplicate or unexpected suite result: {suite}")
        if source_proofs is not None:
            source = result.get("source", {})
            attempt = source.get("run_attempt")
            if source.get("run_id") != run_id or type(attempt) is not int or not 1 <= attempt <= run_attempt:
                raise ValueError(f"invalid {suite} source attempt")
            source_key = (key, attempt)
            if source_key in seen_attempts:
                raise ValueError(f"duplicate {suite} result in attempt {attempt}")
            seen_attempts.add(source_key)
            if any(result.get(field) != plan.get(field) for field in ("base", "head", "tested_sha", "tree", "contract_digest")):
                raise ValueError(f"stale or mismatched {suite} evidence")
            if key in by_suite and by_suite[key]["source"]["run_attempt"] > attempt:
                continue
        elif key in by_suite:
            raise ValueError(f"duplicate or unexpected suite result: {suite}")
        by_suite[key] = result
    if set(by_suite) != required_results:
        raise ValueError(f"missing suite results: {sorted(required_results - set(by_suite))}")
    identity = ("base", "head", "tested_sha", "tree", "contract_digest")
    for key, result in by_suite.items():
        suite = result["suite"]
        if source_proofs is not None and job_name(key) not in source_proofs.get(result["source"]["run_attempt"], set()):
            raise ValueError(f"{suite} has no successful producing job in its source attempt")
        if result.get("status") != "success" or not result.get("commands"):
            raise ValueError(f"{suite} did not execute successfully")
        if any(result.get(field) != plan.get(field) for field in identity):
            raise ValueError(f"stale or mismatched {suite} evidence")
        if any(command.get("exit_code") != 0 for command in result["commands"]):
            raise ValueError(f"{suite} contains a failed command")
        if result.get("tools") != expected_tools(suite):
            raise ValueError(f"{suite} used an unexpected toolchain")
        if "reused_from" in result:
            raise ValueError("cross-run validation reuse is not supported")
        if suite == "production-images":
            try:
                images = validate_manifest(result.get("production_images"), plan["tested_sha"])
            except ImageError as exc:
                raise ValueError(f"invalid production-image evidence: {exc}") from exc
            expected_images = {result["image_target"]} if matrix else set(targets) & IMAGE_TARGETS
            if not targets or set(images) != expected_images:
                raise ValueError("tested production-image set differs from validation selection")
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
        tools.update({"node": TOOLCHAIN["node22"], "gitleaks": TOOLCHAIN["gitleaks"],
                      "terraform": TOOLCHAIN["terraform"]})
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
