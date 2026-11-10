"""The validation contract shared by local preflight and GitHub Actions.

Suite selection is deliberately conservative: a path without a known dependency
rule selects every suite. Results are evidence for an exact tree and contract,
not permission to publish or deploy.
"""

from __future__ import annotations

from functools import lru_cache, partial
from fnmatch import fnmatchcase
import hashlib
import shlex
import json
from datetime import datetime, timezone
from pathlib import Path
from scripts.ci_source_proof import job_name
from scripts.ci_toolchain import TOOLCHAIN
from scripts.ingestion_targets import DEPLOY_TARGETS, TARGETS as INGESTION_CONFIGS
from scripts.catalog_csv import read_catalog_rows_text
from scripts.production_image_contracts import HOST_PROBE_FILES
from scripts.terraform_target_contracts import registry, resource_index, scope_inputs
from scripts.tested_image_bundle import BUILD_TARGETS, RECIPES, ROOT, ImageError, TARGETS as IMAGE_TARGETS, validate_manifest, dependency_graph, dependency_closure, repository_files


SUITES = (
    "lint", "tests", "tests-control-plane", "geospatial-integration", "production-images", "sdk-node22", "sdk-node24", "browser",
)
DEPLOYMENTS = tuple(registry(section="deployments"))
INGESTION_DEPLOYMENTS = frozenset(config["selection"] for config in INGESTION_CONFIGS.values())
ROLLOUT_HOLD_TARGETS = frozenset(config["selection"] for config in DEPLOY_TARGETS.values())
DEPLOYMENT_IMAGES = {name: row["image"] for name, row in registry(section="deployments").items() if "image" in row}
INGESTION_ROLLOUT_HOLDS = ".github/ingestion-rollout-holds.json"
ALWAYS = {"lint", "tests", "tests-control-plane"}
DELIVERY_FILTERS = {'refresh-reviewed-catalog': 'has_catalog_changes',
                    'announce-repo-functionality': 'has_repo_alert', 'publish-reviewed-dataset': 'has_mutations'}


def delivery_condition(caller):
    selection = (" && needs.dataset-mutation-selection.outputs." + DELIVERY_FILTERS[caller] + " == 'true'") if caller in DELIVERY_FILTERS else ''
    return "${{ !cancelled() && !failure() && !contains(needs.*.result, 'cancelled') && fromJSON(needs.ci-ready.outputs.delivery || '{}')." + caller + selection + " }}"


def delivery_outputs(plan, event, ref):
    """Successful CI emits main-only eligibility; later prerequisite results stay live."""
    enabled = event == 'push' and ref == 'refs/heads/main'
    return {**{name.replace('_', '-'): enabled and name in plan['deployments'] for name in DEPLOYMENTS},
            'typescript-sdk': enabled and 'sdk-node22' in plan['suites'],
            **dict.fromkeys(('dataset-mutation-selection', 'refresh-reviewed-catalog', 'announce-repo-functionality',
                             'publish-reviewed-dataset', 'identity-decision-reconcile'), enabled)}


def rollout_holds(value, *, historical=False, previous=None, now=None, targets=ROLLOUT_HOLD_TARGETS) -> tuple[str, ...]:
    # Old commits used bare target names. Only comparison baselines may read that
    # persisted format; all new plans require accountable, bounded holds.
    if historical and isinstance(value, list) and all(isinstance(row, str) for row in value):
        value = dict.fromkeys(value)
    if not isinstance(value, dict) or not set(value) <= set(targets):
        raise ValueError("rollout holds must map scheduled deployment targets to owner, reason and expires_at")
    if not historical:
        if previous is not None and len(set(previous) - set(value)) > 1:
            raise ValueError("release at most one rollout hold per change")
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
    for pattern in ("scripts/*.py", "ingestion/common/*.py", ".github/actions/**/*.yml", ".github/actions/**/*.py", ".github/docker/*Dockerfile")
    for path in ROOT.glob(pattern)
} | {
    "pyproject.toml", "uv.lock", "api/typescript/package-lock.json", "tests/browser/package-lock.json",
    ".github/workflows/ci.yml", ".github/workflows/ingestion-deploy.yml",
    ".github/workflows/pr-terraform-plan.yml",
    ".github/terraform-targets.json",
    ".claude/skills/shared-datasets-compliance-audit/scripts/audit_shared_datasets.py",
    "tests/fixtures/terraform_targets/failed-main.json", "tests/fixtures/terraform_targets/shared_bucket_public.failed.tf",
}))


@lru_cache(maxsize=4)
def dependency_inputs(revision=None, root=None):
    """Generate dependencies from local imports, script calls and Docker COPY.

    Include package initializers, relative imports and literal subprocess paths.
    Directory COPY declarations remain prefixes, including newly added files.
    Parsing errors fail selection rather than silently omitting a dependency.
    """
    root = root or ROOT
    snapshot = repository_files(revision, root) if revision is not None else None
    sources = {path.relative_to(root).as_posix(): path.read_text() for directory in
               ("scripts", "ingestion", "services", "api/python/src", "tests")
               for path in (root / directory).rglob("*.py") if "__pycache__" not in path.parts} if snapshot is None else {
                   name: raw.decode() for name, raw in snapshot.items()
                   if name.endswith(".py") and name.startswith(("scripts/", "ingestion/", "services/", "api/python/src/", "tests/"))}
    edges = dependency_graph(sources | dict.fromkeys((path.relative_to(root).as_posix() for path in root.glob("api/**/*.mjs")), ""))
    closure = partial(dependency_closure, edges)

    images, copies = {}, {}
    for target, recipe in RECIPES.items():
        if snapshot is not None and recipe not in snapshot:
            continue
        copied = {recipe, ".dockerignore"}
        recipe_text = (root / recipe).read_text() if snapshot is None else snapshot[recipe].decode()
        for line in recipe_text.replace("\\\n", " ").splitlines():
            operation, _, arguments = line.strip().partition(" ")
            if operation.upper() != "COPY" or arguments.startswith("--from="):
                continue
            words = json.loads(arguments) if arguments.startswith("[") else shlex.split(arguments, comments=True)
            if len(words) < 2 or any(word.startswith("--") or "$" in word for word in words):
                raise ValueError(f"unsupported COPY inputs in {recipe}: {arguments}")
            for source in words[:-1]:
                source = source.removeprefix("./").rstrip("/")
                source = "" if source == "." else source
                directory = (root / source).is_dir() if snapshot is None else any(name.startswith(source + "/") for name in snapshot)
                copied.add(source + "/" if directory else source)
        copies[target] = set(copied)
        copied.add(f"tests/fixtures/production_images/{target}.json")
        checks = json.dumps((registry(root, section="image_checks") if snapshot is None else json.loads(snapshot.get(".github/terraform-targets.json", b"{}")).get("image_checks", {})).get(target, {}))
        copied.update(path for key, path in HOST_PROBE_FILES.items() if "{" + key + "}" in checks)
        images[target] = copied
    native = closure(NATIVE_TESTS)
    native.update({name for name, inputs in edges.items() if name.startswith("tests/")
                   and inputs & {item for item in native if not item.endswith("/__init__.py")}})
    browser = closure(name for name in sources if name.startswith("tests/browser/"))
    return images, native, browser, sources.keys() | {name for name in snapshot or {} if name.startswith(".github/")}, copies


def matches(path, inputs):
    return any((item == "/" or path.startswith(item)) if item.endswith("/") else fnmatchcase(path, item) for item in inputs)


def classify_change(path: str, inputs=None, *, root=ROOT) -> tuple[str, set[str]]:
    """Runtime selection follows generated dependencies; unknown paths stay broad."""
    if (path in {
            "pyproject.toml", "uv.lock", ".github/actions/ci-tools/action.yml",
            ".github/docker/preflight.Dockerfile", ".github/docker/geospatial-ci.Dockerfile",
            "scripts/production_image_contracts.py", "scripts/tested_image_bundle.py",
        } or path.startswith("tests/fixtures/") and path.endswith(".md")):
        return "component" if path in {"pyproject.toml", "uv.lock"} else "shared", set(SUITES)
    images, native, browser, sources, _ = inputs or dependency_inputs(root=root)
    if path.startswith(("catalog/", "templates/", "docs/assets/")):
        suites = {"sdk-node22", "sdk-node24", "browser"}
        if path.startswith("catalog/") or any(matches(path, copied) for copied in images.values()):
            suites.add("geospatial-integration")
        return "metadata", suites
    if path == "api/typescript/README.md":
        return "component", {"sdk-node22", "sdk-node24", "browser"}
    if path.endswith(".md") or path == "LICENSE":
        return "documentation", set()
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
    if path.startswith(".github/") and ((root / path).is_file() or path in sources):
        return "component", suites
    if path.startswith("terraform/") or path in {".gitignore", ".gitleaksignore"}:
        return "infrastructure", suites
    return "unknown", set(SUITES)


def documentation_only(paths: list[str] | None, *, root=ROOT) -> bool:
    return paths is not None and all(classify_change(path, root=root)[0] == "documentation" for path in paths)


def resolve_selection(paths, *, base=None, root=None, **deployment_inputs):
    """Resolve the validation floor before rollout holds suppress delivery."""
    if paths is not None and (not isinstance(paths, list) or any(not isinstance(path, str) for path in paths)):
        raise ValueError("selection requires a changed-path list")
    root = root or ROOT
    holds = deployment_inputs.pop('ingestion_holds', ())
    deployments = select_deployments(paths, root=root, **deployment_inputs)
    images = {row["image"] for target, row in registry(root, section="deployments").items() if target in deployments and "image" in row}
    deployments = [target for target in deployments if target not in holds]
    suites, tiers = set(ALWAYS), set()
    reason = "comparison history unavailable"
    if paths is None:
        suites.update(SUITES)
        images.update(BUILD_TARGETS)
    else:
        for path in paths:
            inputs = dependency_inputs(base if base and not (root / path).exists() else None, root)
            tier, required = classify_change(path, inputs, root=root)
            if path == ".github/workflows/ci.yml":
                required = changed_ci_suites(base, root)
                if "production-images" in required:
                    images.update(BUILD_TARGETS)
            suites.update(required)
            tiers.add(tier)
            if tier in {"shared", "unknown"}:
                images.update(BUILD_TARGETS)
                reason = f"{tier} dependency: {path}"
            elif tier != "documentation":
                images.update(target for target, copied in inputs[0].items() if matches(path, copied))
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


def deploy_image_targets(plan):
    """Only selected deliveries need retained TARs; holds still require validation."""
    deploying = {DEPLOYMENT_IMAGES[target] for target in plan["deployments"] if target in DEPLOYMENT_IMAGES}
    return [target for target in planned_images(plan) if target in deploying]


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


@lru_cache(maxsize=4)
def deployment_paths(root=None):
    """Workflow/action/script closure plus registered Terraform and image inputs."""
    root = root or ROOT
    files = {path.relative_to(root).as_posix(): path.read_bytes()
             for directory in (".github", "scripts", "ingestion", "services", "catalog", "api")
             for path in (root / directory).rglob("*")
             if path.is_file() and not set(path.parts) & {"__pycache__", "node_modules", ".venv", ".terraform"}}
    declarations, scopes, syncs = (registry(root, section=section) for section in ("deployments", "plan_scopes", "targets"))
    graphs = [dependency_graph(files, disabled_conditions=("env.PREPARE_MONITORING_SLACK == 'true'",)), dependency_graph(files)]
    # These entrypoints validate an already-selected rollout. Their imported
    # policy changes invalidate CI evidence, not the resources being delivered.
    exceptions = registry(root, section="selection_exceptions")
    for row in exceptions.values():
        reasons = row.get("deployments", {}).values() if row["kind"] == "include" else [row["reason"]]
        if (row["kind"] not in {"boundary", "exclude", "include"} or not reasons or any(not reason.strip() for reason in reasons)
                or set(row.get("deployments", {})) - declarations.keys()):
            raise ValueError("selection exceptions require a reason, kind and registered deployments")
    for edges in graphs:
        for name, row in exceptions.items():
            if row["kind"] != "include" and not (name.endswith((".yml", ".yaml")) and row.get("deployments")):
                edges[name] = set()
    sources = {name for path, rows in resource_index(root).items()
               if Path(path).parent.as_posix() == "terraform/envs/prod" for name in rows}
    for job, row in syncs.items():
        for resource in row['prerequisites']:
            if not any(resource == name or resource.startswith(name + ".") for name in sources):
                raise ValueError(f"{job}: IAM prerequisite has no Terraform declaration: {resource}")
    paths = {}
    for target, row in declarations.items():
        workflow = row["workflow"]
        edges = graphs[any(config.get('prepare_monitoring_slack') for config in syncs.values() if config['workflow'] == workflow)]
        resources = [resource for config in DEPLOY_TARGETS.values() if config["workflow"] == workflow for resource in config["targets"]]
        resources += [resource for config in syncs.values() if config["workflow"] == workflow for resource in config["resources"]]
        resources += [resource for scope in scopes.get(workflow, {}).values()
                      for resource in [*scope.get("exact", []), *scope.get("instances", []), *scope.get("modules", {})]]
        inputs = dependency_closure(edges, [".github/workflows/" + workflow]) | scope_inputs(resources, root)
        if resources:
            inputs.update("terraform/envs/prod/" + name for name in ("main.tf", "production.auto.tfvars", "variables.tf", "versions.tf"))
        if "image" in row:
            inputs.update(dependency_inputs(root=root)[4][row["image"]])
        inputs.add(".github/terraform-targets.json")
        inputs.difference_update(name for name, item in exceptions.items() if item["kind"] == "exclude")
        for path in inputs | {name for name, item in exceptions.items() if target in item.get("deployments", [])}:
            paths.setdefault(path, set()).add(target)
    return paths


def changed_ci_suites(base, root):
    """Diff job definitions; shared scheduling changes invalidate every suite."""
    import yaml
    path = ".github/workflows/ci.yml"
    if base is None:
        return set(SUITES)
    before = yaml.safe_load(repository_files(base, root)[path])
    after = yaml.safe_load((root / path).read_text())
    if {key: value for key, value in before.items() if key != "jobs"} != {key: value for key, value in after.items() if key != "jobs"}:
        return set(SUITES)
    changed = {name for name in before["jobs"].keys() | after["jobs"].keys() if before["jobs"].get(name) != after["jobs"].get(name)}
    if changed & {"geospatial-changes", "ci-ready"}:
        return set(SUITES)
    return (changed & set(SUITES)) | ({"sdk-node22", "sdk-node24"} if "sdk-validation" in changed else set())


def select_deployments(paths, *, catalog_snapshots=None, ingestion_holds=(), released_holds=(), root=ROOT):
    """Unknown paths broaden validation, never mutation authority."""
    selected = set(released_holds)
    for path in paths or []:
        if classify_change(path, root=root)[0] == "documentation":
            continue
        if path == "catalog/shared-datasets-catalog.csv":
            selected.update(catalog_deployment_targets(*catalog_snapshots) if catalog_snapshots is not None else {"eamlis", "wdpa", "pmtiles_cdn", "dataset_usage"})
            continue
        selected.update(target for pattern, targets in deployment_paths(root).items() if matches(path, {pattern}) for target in targets)
    return [target for target in registry(root, section="deployments") if target in selected and target not in ingestion_holds]


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
        for job in ("lint", "tests", "tests-control-plane", "geospatial-integration", "production-images", "browser"):
            required = "success" if job in selected else "skipped"
            if jobs.get(job, {}).get("result") != required:
                raise ValueError(f"job {job}: expected {required}, got {jobs.get(job, {}).get('result')}")
        sdk_required = "success" if "sdk-node22" in selected else "skipped"
        if jobs.get("sdk-validation", {}).get("result") != sdk_required:
            raise ValueError("SDK validation failed, was cancelled, missing, or unexpectedly skipped")
        if jobs.get("geospatial-changes", {}).get("result") != "success":
            raise ValueError("validation selection did not succeed")
    return by_suite


def expected_tools(suite: str) -> dict[str, str]:
    if suite not in SUITES:
        raise ValueError(f"unknown suite: {suite}")
    return {"python": TOOLCHAIN["python"], "uv": TOOLCHAIN["uv"]} | {
        "lint": {key: TOOLCHAIN[key] for key in ("terraform", "actionlint", "shellcheck")},
        **dict.fromkeys(("tests", "tests-control-plane"), {"node": TOOLCHAIN["node22"], "gitleaks": TOOLCHAIN["gitleaks"], "terraform": TOOLCHAIN["terraform"]}),
        "sdk-node22": {"node": TOOLCHAIN["node22"]}, "sdk-node24": {"node": TOOLCHAIN["node24"]},
        "browser": {"node": TOOLCHAIN["node22"]},
        "geospatial-integration": {"gdal": "3.6.2", "tippecanoe": "2.52.0", "pmtiles": "1.30.1"},
        "production-images": {},  # Installed image tools are probed in the production-image command.
    }[suite]


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
