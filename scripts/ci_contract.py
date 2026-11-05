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
import re
from datetime import datetime, timezone
from pathlib import Path
from scripts.ci_source_proof import job_name
from scripts.ci_toolchain import TOOLCHAIN
from scripts.ingestion_targets import DEPLOY_TARGETS, TARGETS as INGESTION_CONFIGS
from scripts.catalog_csv import read_catalog_rows_text
from scripts.terraform_target_contracts import registry
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
    **{config["selection"]: target for target, config in DEPLOY_TARGETS.items()}, "catalog_viewer": "catalog-viewer",
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
# Hash complete validator/action modules so new imports and nested composites
# cannot escape evidence invalidation through an omitted hand-maintained entry.
# Selection remains derived independently from COPY/import consumers below.
CONTRACT_FILES = tuple(sorted({
    path.relative_to(ROOT).as_posix()
    for pattern in ("scripts/*.py", "ingestion/common/*.py", ".github/actions/**/*.yml", ".github/docker/*Dockerfile")
    for path in ROOT.glob(pattern)
} | {
    "pyproject.toml", "uv.lock", "api/typescript/package-lock.json", "tests/browser/package-lock.json",
    ".github/workflows/ci.yml", ".github/workflows/ingestion-deploy.yml",
    ".github/terraform-targets.json", "terraform/iam-plan-prerequisites.json",
    ".claude/skills/shared-datasets-compliance-audit/scripts/audit_shared_datasets.py",
    "tests/fixtures/terraform_targets/failed-main.json", "tests/fixtures/terraform_targets/shared_bucket_public.failed.tf",
}))


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
            "scripts/production_image_contracts.py", "scripts/installed_ingestion_fixture.py", "scripts/tested_image_bundle.py", "terraform/iam-plan-prerequisites.json",
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


def resolve_selection(paths, **deployment_inputs):
    """Resolve deployments first, then their validation floor, exactly once."""
    if paths is not None and (not isinstance(paths, list) or any(not isinstance(path, str) for path in paths)):
        raise ValueError("selection requires a changed-path list")
    deployments = select_deployments(paths, **deployment_inputs)
    images = {DEPLOYMENT_IMAGES[target] for target in deployments if target in DEPLOYMENT_IMAGES}
    suites, tiers = set(ALWAYS), set()
    reason = "comparison history unavailable"
    if paths is None:
        suites.update(SUITES)
        images.update(BUILD_TARGETS)
    else:
        inputs = dependency_inputs()[0]
        for path in paths:
            tier, required = classify_change(path)
            suites.update(required)
            tiers.add(tier)
            if tier in {"shared", "unknown"}:
                images.update(BUILD_TARGETS)
                reason = f"{tier} dependency: {path}"
            elif tier != "documentation":
                images.update(target for target, copied in inputs.items() if matches(path, copied))
        if not tiers & {"shared", "unknown"}:
            reason = "tiers: " + ", ".join(sorted(tiers))
    suites.discard("production-images")
    if images:
        suites.add("production-images")
    if deployment_inputs.get("released_holds"):
        suites.add("geospatial-integration")
    return dict(deployments=deployments, production_image_targets=[target for target in BUILD_TARGETS if target in images],
                suites=[suite for suite in SUITES if suite in suites], selection_reason=reason)


def planned_images(plan):
    """Consume the resolved plan; never infer a different matrix from paths."""
    images = plan["production_image_targets"]
    if (not isinstance(images, list) or images != [target for target in BUILD_TARGETS if target in images]
            or bool(images) != ("production-images" in plan["suites"])
            or not {DEPLOYMENT_IMAGES[target] for target in plan.get("deployments", []) if target in DEPLOYMENT_IMAGES} <= set(images)):
        raise ValueError("invalid resolved production-image selection")
    return images


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


@lru_cache(maxsize=1)
def deployment_paths():
    """Derive IaC source files from the same exact scopes used by executors."""
    workflows = {config["workflow"]: config["selection"] for config in DEPLOY_TARGETS.values()}
    workflows.update({target.replace("_", "-") + ("-deploy.yml" if target in {"catalog_viewer", "dataset_usage"} else "-sync.yml"): target
                      for target in DEPLOYMENTS if target not in INGESTION_DEPLOYMENTS})
    workflows["scheduled-ingestion-deploy-iam-sync.yml"] = "ingestion_iam"
    sources = {}
    for path in (ROOT / "terraform/envs/prod").glob("*.tf"):
        for kind, first, second in re.findall(r'^(resource|module|data) "([^"\n]+)"(?: "([^"\n]+)")?\s*{', path.read_text(), re.M):
            sources[(first + "." + second) if kind == "resource" else kind + "." + first + ("." + second if second else "")] = path.relative_to(ROOT).as_posix()
    policy = json.loads((ROOT / "terraform/iam-plan-prerequisites.json").read_text())
    for job, prerequisites in policy["jobs"].items():
        for resource in prerequisites:
            if not any(resource == root or resource.startswith(root + ".") for root in sources):
                raise ValueError(f"{job}: IAM prerequisite has no Terraform declaration: {resource}")
    scopes = [(config["workflow"], config["targets"]) for config in DEPLOY_TARGETS.values()]
    scopes += [(config["workflow"], config["resources"]) for config in registry(ROOT).values()]
    scopes += [(workflow, [*scope.get("exact", []), *scope.get("instances", []), *scope.get("modules", {})])
               for workflow, rows in registry(ROOT, section="plan_scopes").items() for scope in rows.values()]
    paths = {".github/workflows/" + workflow: {target} for workflow, target in workflows.items()}
    for workflow, resources in scopes:
        if workflow not in workflows:
            continue  # Manual-only executors never become automatic deployments.
        for resource in resources:
            source = sources.get(".".join(resource.split(".")[:2]).split("[", 1)[0])
            if source:
                for path in (source, source.removesuffix(".tf") + "_variables.tf"):
                    paths.setdefault(path, set()).add(workflows[workflow])
    return paths


def select_deployments(paths, *, catalog_snapshots=None, ingestion_holds=(), released_holds=()):
    """Unknown paths broaden validation, never mutation authority."""
    selected = set(released_holds)
    ingestion = INGESTION_DEPLOYMENTS
    executors = {config["selection"] for config in DEPLOY_TARGETS.values()}
    iam = set(DEPLOYMENTS) - ingestion - {"catalog_viewer", "dataset_usage", "pmtiles_cdn"}
    shared = {
        (".github/workflows/ingestion-deploy.yml", "scripts/ingestion_targets.py", "scripts/ingestion_verification.py"): executors,
        (".github/actions/deployment-bootstrap/action.yml",): set(DEPLOYMENTS),
        (".github/actions/deployment-bootstrap/terraform/action.yml",): iam | {"dataset_usage"},
        (".github/terraform-targets.json", "scripts/terraform_target_contracts.py"): iam | {"dataset_usage", "catalog_viewer", "pmtiles_cdn"},
        (".github/workflows/prod-terraform-target-apply.yml",): iam,
        ("catalog/ingestion-quality.json",): {"eamlis"},
        ("catalog/wdpa-processing-acceptance.json",): {"wdpa"},
        ("docs/assets/ims-sea-ice-extent.md",): {"sea_ice"},
        ("catalog/categories.yaml",): {"catalog_viewer", "scratch_cleanup_iam", "cron_alert_policy"},
        ("scripts/monitoring_slack_prepare.py", "services/monitoring_slack/", "ingestion/common/__init__.py", "ingestion/common/decision_events.py"): {"cron_alert_policy"},
        ("terraform/envs/prod/main.tf",): {"catalog_viewer", "artifact_registry_iam", "preview_terraform_iam"},
        ("terraform/envs/prod/canonical_mutation_iam.tf",): {"catalog_viewer", "cron_alert_policy"},
        ("terraform/envs/prod/shared_bucket_public.tf", "scripts/dataset_usage_deploy.py"): {"dataset_usage"},
        ("scripts/pmtiles_cdn_sync.py", "terraform/modules/pmtiles-cdn/"): {"pmtiles_cdn"},
        ("catalog/dataset-usage.json", "catalog/dataset-usage-activation.json"): {"dataset_usage", "catalog_viewer"},
        ("terraform/modules/cloud_run_job/", "terraform/modules/scheduler_job/", "terraform/modules/service_account/"): executors | {"catalog_viewer"},
        ("pyproject.toml", "uv.lock"): set(DEPLOYMENT_IMAGES),
        ("ingestion/common/",): ingestion,
        ("ingestion/__init__.py",): ingestion | {"cron_alert_policy"},
        ("api/python/src/", "services/catalog_viewer/", "services/feature_preview_service/", "services/__init__.py", "services/http_base.py"): {"catalog_viewer"},
        ("catalog/feature-identity-resolutions/",): {"sea_ice", "wdpa"},
        ("terraform/envs/prod/variables.tf", "terraform/envs/prod/versions.tf"): iam | {"catalog_viewer", "pmtiles_cdn"},
    }
    for config in DEPLOY_TARGETS.values():
        source = config["image_variable"].removesuffix("_image")
        shared[("ingestion/" + source + "/", "terraform/envs/prod/" + source + "*")] = {config["selection"]}
    for path in paths or []:
        if classify_change(path)[0] == "documentation":
            continue
        selected.update(deployment_paths().get(path, ()))
        selected.update(target for patterns, targets in shared.items() if matches(path, patterns) for target in targets)
        if path.startswith("scripts/"):
            selected.update(selection for selection, image in DEPLOYMENT_IMAGES.items() if matches(path, dependency_inputs()[4][image]))
        if path == "catalog/shared-datasets-catalog.csv":
            selected.update(catalog_deployment_targets(*catalog_snapshots) if catalog_snapshots is not None else {"eamlis", "wdpa", "pmtiles_cdn", "dataset_usage"})
    if any(path.startswith("terraform/") and classify_change(path)[0] != "documentation" for path in paths or []):
        if "catalog_viewer" in selected:
            selected.add("dataset_usage")
        if "mpatlas" in selected:
            selected.add("cron_alert_policy")
        if selected & ingestion:
            selected.add("ingestion_iam")
        if selected & (ingestion | {"catalog_viewer", "dataset_usage", "cron_alert_policy"}):
            selected.add("artifact_registry_iam")
    return [target for target in DEPLOYMENTS if target in selected and target not in ingestion_holds]


def contract_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for filename in CONTRACT_FILES:
        digest.update(filename.encode() + b"\0" + (root / filename).read_bytes() + b"\0")
    return digest.hexdigest()


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
    targets = planned_images(plan)
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
    if suite not in SUITES:
        raise ValueError(f"unknown suite: {suite}")
    return {"python": TOOLCHAIN["python"], "uv": TOOLCHAIN["uv"]} | {
        "lint": {key: TOOLCHAIN[key] for key in ("terraform", "actionlint", "shellcheck")},
        "tests": {"node": TOOLCHAIN["node22"], "gitleaks": TOOLCHAIN["gitleaks"], "terraform": TOOLCHAIN["terraform"]},
        "sdk-node22": {"node": TOOLCHAIN["node22"]}, "sdk-node24": {"node": TOOLCHAIN["node24"]},
        "browser": {"node": TOOLCHAIN["node22"]},
        "geospatial-integration": {"gdal": "3.6.2", "tippecanoe": "2.52.0", "pmtiles": "1.30.1"},
        "production-images": {},  # Installed image tools are probed in the production-image command.
    }[suite]


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
