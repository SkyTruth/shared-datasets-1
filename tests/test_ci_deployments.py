import json
import re
import shutil
import subprocess
from graphlib import TopologicalSorter
from functools import cache
from pathlib import Path

import pytest

from scripts.ci_contract import ALWAYS, DEPLOYMENTS, delivery_outputs, rollout_holds, select_deployments, resolve_selection
from scripts import ci_preflight
from scripts.ci_preflight import suite_commands
from workflow_helpers import deployment_prerequisites, load_workflow, workflow_triggers, workflow_steps_by_name

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('path', ['scripts/ingestion_targets.py'])
def test_shared_ingestion_executor_selects_all_callers_and_preserves_rollout_holds(path):
    paths = [path]
    targets = {"mpatlas", "eamlis", "wdpa", "sea_ice", "dataset_usage"}
    if path == "scripts/ingestion_targets.py":
        targets |= {"catalog_viewer", "pmtiles_cdn"}
    assert set(select_deployments(paths)) == targets
    assert set(select_deployments(paths, ingestion_holds=("wdpa", "sea_ice"))) == targets - {"wdpa", "sea_ice"}
    assert "production-images" in resolve_selection(paths)["suites"]
    assert {"mpatlas-monthly", "eamlis-monthly", "wdpa-monthly", "sea-ice-daily", "dataset-usage"} <= set(resolve_selection(paths)["production_image_targets"])


def test_usage_hold_blocks_delivery_but_preserves_validation_and_reviewed_release(tmp_path):
    from test_ci_preflight import fixture_repo, emit_plan

    root, git = fixture_repo(tmp_path)
    path = root / ci_preflight.INGESTION_ROLLOUT_HOLDS
    holds = json.loads((ROOT / ci_preflight.INGESTION_ROLLOUT_HOLDS).read_text())
    holds['dataset_usage'] = dict(owner='jonaraphael', reason='review', expires_at='2099-01-01T00:00:00Z')
    path.write_text(json.dumps(holds))
    git('add', '.')
    git('commit', '-m', 'hold merge deployments')
    base = git('rev-parse', 'HEAD')
    workflow = root / '.github/workflows/dataset-usage-deploy.yml'
    workflow.write_text(workflow.read_text() + '\n# changed usage deployment\n')
    git('add', '.')
    git('commit', '-m', 'change held usage deployment')
    selected, outputs = emit_plan(root, base, tmp_path / 'held')
    assert 'dataset_usage' not in selected['deployments']
    assert outputs['dataset_usage'] == 'false'
    assert delivery_outputs(selected, 'push', 'refs/heads/main')['dataset-usage'] is False
    assert 'dataset-usage' in selected['production_image_targets']
    assert 'production-images' in selected['suites']

    base = git('rev-parse', 'HEAD')
    holds = json.loads(path.read_text())
    previous = holds.copy()
    del holds['dataset_usage']
    assert rollout_holds(holds, previous=previous) == tuple(holds)
    path.write_text(json.dumps(holds))
    git('add', '.')
    git('commit', '-m', 'release usage after review')
    released, outputs = emit_plan(root, base, tmp_path / 'released')
    assert released['deployments'] == ['dataset_usage']
    assert outputs['dataset_usage'] == 'true'
    assert delivery_outputs(released, 'push', 'refs/heads/main')['dataset-usage'] is True
    assert released['production_image_targets'] == ['dataset-usage']


def test_usage_hold_does_not_allow_unrelated_deployment_targets():
    hold = dict(owner='jonaraphael', reason='review', expires_at='2099-01-01T00:00:00Z')
    assert rollout_holds({'dataset_usage': hold}) == ('dataset_usage',)
    with pytest.raises(ValueError, match='rollout holds must map'):
        rollout_holds({'scratch_cleanup_iam': hold})


@pytest.mark.parametrize('selection,workflow,image', [
    ('mpatlas', 'mpatlas-monthly-deploy.yml', 'mpatlas-monthly'),
    ('eamlis', 'eamlis-monthly-deploy.yml', 'eamlis-monthly'),
    ('wdpa', 'wdpa-monthly-deploy.yml', 'wdpa-monthly'),
    ('sea_ice', 'sea-ice-daily-deploy.yml', 'sea-ice-daily'),
    ('dataset_usage', 'dataset-usage-deploy.yml', 'dataset-usage'),
])
def test_workflow_only_changes_keep_the_held_deployments_image_validation_floor(selection, workflow, image):
    paths = ['.github/workflows/' + workflow]
    ready = resolve_selection(paths)
    held = resolve_selection(paths, ingestion_holds=(selection,))
    assert selection in ready['deployments'] and selection not in held['deployments']
    assert image in ready['production_image_targets']
    assert held['production_image_targets'] == ready['production_image_targets']
    assert held['suites'] == ready['suites']


@pytest.mark.parametrize('path,expected', [
    *[(path, {'sea_ice', 'artifact_registry_iam', 'catalog_viewer', 'pmtiles_cdn'}) for path in (
        'scripts/deployment_revision.py', 'scripts/deployment_emission.py', 'scripts/tested_image_bundle.py',
        '.github/actions/deployment-auth/action.yml', '.github/actions/deployment-bootstrap/action.yml',
        '.github/actions/deployment-receipt/action.yml',
        'scripts/terraform_target_contracts.py',
        'scripts/terraform_plan_allowlist.py',
        'scripts/install_deployment_verifier.py')],
    ('scripts/tested_image_authorization.py', {'sea_ice', 'catalog_viewer', 'cron_alert_policy'}),
    ('.github/workflows/ingestion-deploy.yml', {'sea_ice'}),
    ('scripts/ingestion_verification.py', {'sea_ice'}),
    ('.github/workflows/prod-terraform-target-apply.yml', {'artifact_registry_iam'}),
    ('.github/actions/deployment-bootstrap/terraform/action.yml', {'artifact_registry_iam'}),
])
def test_executor_machinery_selects_signed_readiness_without_live_rollouts(path, expected):
    plan = resolve_selection([path])
    assert plan['deployments'] == []
    assert set(plan['readiness_targets']) == expected
    assert ALWAYS <= set(plan['suites'])
    held = resolve_selection([path], ingestion_holds=('sea_ice',))
    assert held['readiness_targets'] == plan['readiness_targets']
    assert 'sea_ice' not in select_deployments([path], ingestion_holds=('sea_ice',))


def test_variable_file_change_keeps_every_potential_consumer():
    # Conservative file selection covers every default, including defaults used
    # by IAM scopes. No variable is executor-only content.
    assert set(select_deployments(['terraform/envs/prod/variables.tf'])) == set(DEPLOYMENTS)


def test_registry_change_selects_every_owner():
    assert set(select_deployments(['.github/terraform-targets.json'])) == set(DEPLOYMENTS)


@pytest.mark.parametrize('target,path', [
    ('mpatlas', 'terraform/envs/prod/mpatlas_monthly.tf'),
    ('eamlis', 'terraform/envs/prod/eamlis_monthly.tf'),
    ('wdpa', 'terraform/envs/prod/wdpa_monthly.tf'),
    ('sea_ice', 'terraform/envs/prod/sea_ice_daily.tf'),
    ('ingestion_iam', 'terraform/envs/prod/scheduled_ingestion_deploy_iam.tf'),
    ('pmtiles_cdn', 'terraform/envs/prod/shared_bucket_public.tf'),
    ('catalog_viewer', 'terraform/envs/prod/catalog_viewer.tf'),
    ('dataset_usage', 'terraform/envs/prod/dataset_usage.tf'),
    ('artifact_registry_iam', 'terraform/envs/prod/artifact_registry_iam.tf'),
    ('preview_terraform_iam', 'terraform/envs/prod/preview_terraform_iam.tf'),
    ('scratch_cleanup_iam', 'terraform/envs/prod/canonical_mutation_iam.tf'),
    ('cron_alert_policy', 'terraform/envs/prod/monitoring.tf'),
])
def test_target_content_retains_deployment_and_exact_image_floor(target, path):
    from scripts.ci_contract import DEPLOYMENT_IMAGES
    assert (ROOT / path).is_file()
    plan = resolve_selection([path])
    assert target in plan['deployments']
    if target in DEPLOYMENT_IMAGES:
        assert DEPLOYMENT_IMAGES[target] in plan['production_image_targets']


@pytest.mark.parametrize('target,image', [
    ('mpatlas', 'mpatlas-monthly'), ('eamlis', 'eamlis-monthly'), ('wdpa', 'wdpa-monthly'),
    ('sea_ice', 'sea-ice-daily'), ('dataset_usage', 'dataset-usage'),
    ('catalog_viewer', 'catalog-viewer'), ('cron_alert_policy', 'monitoring-slack'),
])
def test_each_production_recipe_still_selects_its_exact_deployment_and_image(target, image):
    from scripts.tested_image_bundle import RECIPES
    plan = resolve_selection([RECIPES[image]])
    assert plan['deployments'] == [target]
    assert image in plan['production_image_targets']


def test_executor_selection_cuts_replay_fanout_without_losing_content_targets():
    from scripts.ci_contract import INGESTION_DEPLOYMENTS
    replay = json.loads((ROOT / 'tests/fixtures/ci-selection-replay.json').read_text())
    plans = [resolve_selection(row['paths']) for row in replay]
    assert len(plans) == 120
    assert sum(len(plan['deployments']) == len(DEPLOYMENTS) for plan in plans) <= 3
    assert sum(len(plan['deployments']) for plan in plans) / len(plans) < 4.05
    assert sum(bool(set(plan['deployments']) & INGESTION_DEPLOYMENTS) for plan in plans) <= 64


def test_representative_executor_override_still_discovers_new_content_producers(tmp_path):
    root = tmp_path / 'source'
    for name in ('.github', 'scripts', 'terraform', 'ingestion', 'services', 'catalog', 'api'):
        shutil.copytree(ROOT / name, root / name,
                        ignore=shutil.ignore_patterns('.terraform', '__pycache__', 'node_modules'))
    workflow = root / '.github/workflows/ingestion-deploy.yml'
    with workflow.open('a') as stream:
        stream.write('\n  fixture-content:\n    steps:\n    - run: python scripts/new_content.py\n')
    (root / 'scripts/new_content.py').write_text('CONTENT = True\n')
    assert set(select_deployments(['scripts/new_content.py'], root=root)) == {
        'mpatlas', 'eamlis', 'wdpa', 'sea_ice', 'dataset_usage'}


def test_every_tracked_path_validates_all_images_consumed_by_selected_deployments():
    # Enumerate the repository, rather than maintaining examples of IaC paths.
    # This independent map is the actual tested-image deployment contract.
    images = {
        "mpatlas": "mpatlas-monthly", "eamlis": "eamlis-monthly", "wdpa": "wdpa-monthly",
        "sea_ice": "sea-ice-daily", "catalog_viewer": "catalog-viewer",
        "dataset_usage": "dataset-usage", "cron_alert_policy": "monitoring-slack",
    }
    paths = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT, text=True).split("\0")
    for path in filter(None, paths):
        required = {images[target] for target in select_deployments([path]) if target in images}
        if required:
            plan = resolve_selection([path])
            assert "production-images" in plan["suites"], path
            assert required <= set(plan["production_image_targets"]), path


def test_sdk_packaged_readme_runs_release_policy_before_merge(tmp_path):
    paths = ["api/typescript/README.md"]
    suites = resolve_selection(paths)["suites"]
    assert {"sdk-node22", "sdk-node24"} <= set(suites)
    # Exercise the selected SDK command against real Git snapshots. An unchanged
    # package version must reject changed packaged documentation.
    from test_ci_preflight import fixture_repo

    source, git = fixture_repo(tmp_path)
    package = source / "api/typescript"
    package.mkdir(parents=True, exist_ok=True)
    for filename in ("package.json", "package-lock.json", "scripts/release-policy.mjs"):
        destination = package / filename
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((ROOT / "api/typescript" / filename).read_bytes())
    (package / "README.md").write_text("Original documentation\n")
    git("add", ".")
    git("commit", "-m", "SDK package")
    base = git("rev-parse", "HEAD")
    (package / "README.md").write_text("Changed package contents\n")
    git("add", ".")
    git("commit", "-m", "Change README without bump")
    command, cwd = suite_commands("sdk-node22", source, {"base": base, "tested_sha": "HEAD"}, tmp_path)[0]
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True)
    assert result.returncode != 0
    assert "SDK package content changed" in result.stderr


@pytest.mark.parametrize("paths", [None, ["unknown/component"], ["docs/ci-reliability.md"], ["tests/test_publication.py"], [".github/workflows/ci.yml"]])
def test_broad_validation_never_authorizes_unrelated_deployments(paths):
    assert select_deployments(paths) == []
    assert ALWAYS <= set(resolve_selection(paths)["suites"])


@pytest.mark.parametrize("job", ["mpatlas_monthly", "eamlis_monthly", "sea_ice_daily", "wdpa_monthly", "dataset_usage"])
def test_ingestion_readme_changes_do_not_launch_production_jobs(job):
    path = f"ingestion/{job}/README.md"
    assert select_deployments([path]) == []
    assert set(resolve_selection([path])["suites"]) == ALWAYS


@pytest.mark.parametrize("job,target", [("eamlis_monthly", "eamlis"), ("sea_ice_daily", "sea_ice")])
@pytest.mark.parametrize("runtime_file", ["run.py", "Dockerfile", "README.md.py"])
def test_readme_exclusion_keeps_mixed_runtime_changes_deployable(job, target, runtime_file):
    paths = [f"ingestion/{job}/README.md", f"ingestion/{job}/{runtime_file}"]
    assert set(select_deployments(paths)) == {target}
    assert {"geospatial-integration", "production-images"} <= set(resolve_selection(paths)["suites"])


def test_producer_changes_require_staged_evidence_before_launching_production_validation():
    assert select_deployments(["ingestion/wdpa_monthly/Dockerfile", "scripts/wdpa_input_memory_probe.py"]) == ["wdpa"]
    assert select_deployments(["catalog/wdpa-staged-validation.json"]) == []
    assert "production-images" in resolve_selection(["ingestion/wdpa_monthly/Dockerfile"])["suites"]


@pytest.mark.parametrize("path,target", [("ingestion/eamlis_monthly/run.py", "eamlis"), ("ingestion/sea_ice_daily/run.py", "sea_ice"), ("ingestion/wdpa_monthly/Dockerfile", "wdpa")])
def test_runtime_release_does_not_select_iam_syncs(path, target):
    assert set(select_deployments([path])) == {target}


@pytest.mark.parametrize("path", ["services/catalog_viewer/run.py", "services/feature_preview_service/run.py",
    "services/http_base.py", "services/__init__.py", "api/python/src/skytruth_shared_datasets/client.py", "scripts/compare_releases.py"])
def test_viewer_runtime_does_not_redeploy_usage_collector(path):
    selected = select_deployments([path, "terraform/README.md"])
    assert "dataset_usage" not in selected
    assert set(selected) == ({"eamlis", "wdpa", "catalog_viewer"} if path == "scripts/compare_releases.py" else {"catalog_viewer"})


@pytest.mark.parametrize("path", ["terraform/envs/prod/catalog_viewer.tf", "terraform/modules/service_account/main.tf",
    "catalog/dataset-usage.json", "catalog/dataset-usage-activation.json"])
def test_viewer_and_usage_shared_resources_keep_both_deployments(path):
    expected = {"catalog_viewer"} if path.endswith("/catalog_viewer.tf") else {"catalog_viewer", "dataset_usage"}
    assert expected <= set(select_deployments([path]))


def test_iam_only_bootstrap_cannot_launch_unready_ingestion_dependents():
    assert select_deployments(["terraform/envs/prod/scheduled_ingestion_deploy_iam.tf"]) == ["ingestion_iam"]


def test_shared_runtime_and_terraform_modules_select_cross_component_dependents():
    assert set(select_deployments(["ingestion/common/publication.py"])) == {"mpatlas", "eamlis", "wdpa", "sea_ice", "catalog_viewer"}
    assert "catalog_viewer" not in select_deployments(["terraform/modules/cloud_run_job/main.tf"])  # Viewer is a service, not a job.
    assert set(select_deployments(["catalog/shared-datasets-catalog.csv"])) == {"eamlis", "wdpa", "pmtiles_cdn", "dataset_usage"}


@pytest.mark.parametrize("path,targets", [
    ("scripts/pmtiles_zoom.py", {"mpatlas", "eamlis", "wdpa", "sea_ice"}),
    ("scripts/slack_notify.py", {"mpatlas", "eamlis", "wdpa", "sea_ice"}),
    ("scripts/catalog_csv.py", {"eamlis", "wdpa", "dataset_usage"}),
    ("scripts/feature_metadata_translation_reuse.py", {"eamlis", "wdpa"}),
    ("catalog/feature-identity-resolutions/wdpa.json", {"wdpa", "sea_ice"}),
    ("docs/assets/ims-sea-ice-extent.md", {"sea_ice"}),
])
def test_image_copies_are_release_and_native_validation_dependencies(path, targets):
    assert set(select_deployments([path])) == targets
    assert {"geospatial-integration", "production-images"} <= set(resolve_selection([path])["suites"])


@pytest.mark.parametrize("path", ["catalog/shared-datasets-catalog.csv", "terraform/envs/prod/shared_bucket_public.tf", "terraform/envs/prod/variables.tf", "terraform/envs/prod/versions.tf"])
def test_cdn_catalog_and_shared_bucket_dependencies_select_route_sync(path):
    assert "pmtiles_cdn" in select_deployments([path])


def test_catalog_metadata_updates_do_not_redeploy_consumers_but_contract_changes_do():
    before = "asset_slug,title,canonical_path,translation_locales,translation_fields,access_tier,status,has_pmtiles,available_formats\neamlis-abandoned-mine-land-inventory,Original,gs://skytruth-shared-datasets-1/example/latest/a.fgb,es,PA_NAME,public,active,true,fgb;pmtiles\n"
    path = ["catalog/shared-datasets-catalog.csv"]
    assert select_deployments(path, catalog_snapshots=(before, before.replace("Original", "Updated"))) == []
    assert set(select_deployments(path, catalog_snapshots=(before, before.replace(",es,", ",es;fr,")))) == {"eamlis"}
    assert select_deployments(path, catalog_snapshots=(before, before.replace("/example/", "/new-prefix/"))) == ["pmtiles_cdn", "dataset_usage"]
    assert "catalog_viewer" not in select_deployments(path, catalog_snapshots=(before, before.replace("/example/", "/new-prefix/")))
    assert "catalog_viewer" in select_deployments(path + ["scripts/compare_releases.py"], catalog_snapshots=(before, before))


def test_catalog_snapshot_uncertainty_and_duplicate_slugs_cannot_suppress_validation():
    path = ["catalog/shared-datasets-catalog.csv"]
    assert set(select_deployments(path)) == {"eamlis", "wdpa", "pmtiles_cdn", "dataset_usage"}
    duplicate = "asset_slug,title\nwdpa-marine,One\nwdpa-marine,Two\n"
    with pytest.raises(ValueError, match="unique nonempty"):
        select_deployments(path, catalog_snapshots=(duplicate, duplicate))


@pytest.mark.parametrize("paths", [["terraform/envs/prod/preview_terraform_iam.tf"],
    [".github/workflows/prod-terraform-target-apply.yml"], ["scripts/deployment_permissions.py"]])
def test_pr_validation_checks_all_offline_deployment_contracts_without_credentials(paths):
    plan = {"base": "base", "tested_sha": "head", "changed_paths": paths, "deployments": []}
    commands = [args for args, _ in suite_commands("tests-control-plane", ROOT, plan, ROOT)]
    gate = next(args for args in commands if "scripts/release_contracts.py" in args)
    assert gate[-2:] == ["--target", "all"]
    assert sum(args[-1:] == ["validate"] for args in commands) == 2
    assert any("scripts/terraform_target_contracts.py" in args for args in commands)
    assert not any("gcloud" in args or "apply" in args for args in commands)


def test_all_automatic_deployment_callers_require_ci_ready_and_pass_the_exact_tested_sha():
    workflow = load_workflow(ROOT / ".github/workflows/ci.yml")
    jobs = workflow["jobs"]
    assert "production-images" in jobs["ci-ready"]["needs"]
    for target in DEPLOYMENTS:
        job = jobs[target.replace("_", "-")]
        assert "ci-ready" in job["needs"]
        assert "!failure()" in job["if"]
        assert "fromJSON(needs.ci-ready.outputs.delivery || '{}')." in job["if"]
        assert job["with"]["executor_sha"] == "${{ needs.ci-ready.outputs.tested_sha }}"
        assert "concurrency" not in job  # Mutating leaf owns the one production state lock.
        callee = load_workflow(ROOT / job["uses"].removeprefix("./"))
        assert "push" not in workflow_triggers(callee)
        assert {"executor_sha", "source_run_id", "source_run_attempt"} <= set(workflow_triggers(callee)["workflow_call"]["inputs"])
    for target in ("eamlis", "wdpa", "sea-ice"):
        assert "ingestion-iam" in jobs[target]["needs"]
    for target in ("eamlis", "wdpa", "sea-ice", "catalog-viewer"):
        assert "artifact-registry-iam" in jobs[target]["needs"]
    publisher = jobs["publish-reviewed-dataset"]
    assert "pmtiles-cdn" in publisher["needs"]
    assert "fromJSON(needs.ci-ready.outputs.delivery || '{}').publish-reviewed-dataset" in publisher["if"]
    assert "!failure()" in publisher["if"]


def test_actual_production_images_are_part_of_the_gate_and_use_the_shared_command():
    commands = [args for args, _ in suite_commands("production-images", ROOT, {"base": "base", "tested_sha": "head", "production_image_limits": ci_preflight.production_image_limits(), **ci_preflight.resolve_selection(None)}, ROOT)]
    from scripts.tested_image_bundle import BUILD_TARGETS
    assert [args[args.index("--target") + 1] for args in commands] == list(BUILD_TARGETS)
    for args, target in zip(commands, BUILD_TARGETS, strict=True):
        assert "scripts/production_image_contracts.py" in args
        assert args.count("--target") == 1
        assert args[-2:] == ["--output", str(ROOT / target)]
    suite = load_workflow(ROOT / ".github/workflows/ci.yml")["jobs"]["production-images"]
    assert any("scripts/ci_preflight.py run-image-group" in step.get("run", "") for step in suite["steps"])
    upload = next(step for step in load_workflow(ROOT / ".github/actions/ci-image-result/action.yml")["runs"]["steps"] if step.get("id") == "retained-results")
    assert upload["with"]["compression-level"] == 0
    assert upload["with"]["if-no-files-found"] == "error"


@pytest.mark.parametrize("sdk_selected", [False, True])
def test_sdk_caller_consumes_emitted_plan_selection_after_the_gate(tmp_path, sdk_selected):
    from test_ci_preflight import fixture_repo, emit_plan

    root, git = fixture_repo(tmp_path)
    base = git("rev-parse", "HEAD")
    source = root / ("api/typescript/src/index.ts" if sdk_selected else "docs/example.md")
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text((source.read_text() if source.exists() else "") + "\n// fixture change\n")
    git("add", ".")
    git("commit", "-m", "SDK selection")
    plan, outputs = emit_plan(root, base, tmp_path)
    workflow = load_workflow(ROOT / ".github/workflows/ci.yml")
    selector = workflow["jobs"]["geospatial-changes"]["outputs"]["sdk"]
    suite = re.fullmatch(r"\$\{\{ steps.plan.outputs.([\w-]+) }}", selector)[1]
    assert (outputs[suite] == "true") is ("sdk-node24" in plan["suites"]) is sdk_selected
    caller = workflow["jobs"]["typescript-sdk"]
    assert caller["needs"] == "ci-ready"
    assert "uses" not in caller and "id-token" not in caller["permissions"]
    assert caller["permissions"]["actions"] == "write"
    step = caller["steps"][0]
    assert "gh workflow run publish-typescript-sdk.yml --ref main" in step["run"]
    assert step["env"]["EXECUTOR_SHA"] == "${{ needs.ci-ready.outputs.tested_sha }}"
    values = {"github.event_name": "push", "github.ref": "refs/heads/main",
              "needs.ci-ready.result": "success", "needs.geospatial-changes.outputs.sdk": outputs[suite]}
    assert condition_result(caller, values) is sdk_selected
    for key, value in [("github.event_name", "pull_request"), ("github.ref", "refs/heads/feature"),
                       ("needs.ci-ready.result", "failure"), ("needs.ci-ready.result", "skipped")]:
        assert condition_result(caller, values | {key: value}) is False


def test_bucket_cors_owner_precedes_logging_without_a_deployment_cycle():
    jobs = load_workflow(ROOT / '.github/workflows/ci.yml')['jobs']
    order = list(TopologicalSorter({
        name: [needs] if isinstance(needs := job.get('needs', []), str) else needs
        for name, job in jobs.items()
    }).static_order())
    assert order.index('pmtiles-cdn') < order.index('dataset-usage')
    assert 'needs.dataset-usage' not in jobs['pmtiles-cdn']['if']


@pytest.mark.parametrize("path", [
    "services/monitoring_slack/run.py", "terraform/envs/prod/monitoring_slack.tf",
    "terraform/envs/prod/monitoring.tf", "scripts/slack_incident_api.py",
    "scripts/monitoring_slack_prepare.py",
])
def test_monitoring_runtime_reuses_registry_iam_and_infrastructure_syncs_it(path):
    expected = {"cron_alert_policy"}
    if path.startswith('terraform/'):
        expected.add('artifact_registry_iam')
    if path == 'terraform/envs/prod/monitoring.tf':
        expected.add('dataset_usage')  # This file owns the usage health resources.
    assert set(select_deployments([path])) == expected
    assert "production-images" in resolve_selection([path])["suites"]


MAIN_CALLERS = [target.replace("_", "-") for target in DEPLOYMENTS] + [
    "dataset-mutation-selection", "announce-repo-functionality", "publish-reviewed-dataset", "refresh-reviewed-catalog",
    "deployment-readiness",
]


@cache
def delivery_jobs():
    return load_workflow(ROOT / ".github/workflows/ci.yml")["jobs"]


def condition_result(job, values, *, cancelled=False):
    """Evaluate the fixed three-part scheduling contract, with no expression parser.

    Exact expression equality below binds the truth table to the checked-in job.
    GitHub failure() covers transitive ancestors; skipped jobs are not failures.
    """
    from scripts.ci_contract import delivery_outputs, delivery_condition, DELIVERY_FILTERS
    jobs = delivery_jobs()
    caller, = [name for name, row in jobs.items() if row == job]
    assert job["if"] == delivery_condition(caller)
    plan = {'deployments': [target for target in DEPLOYMENTS if values.get(f'needs.geospatial-changes.outputs.{target}') == 'true'],
            'readiness_targets': ['sea_ice'] if values.get('needs.geospatial-changes.outputs.deployment_readiness') == 'true' else [],
            'suites': ['sdk-node22'] if values.get('needs.geospatial-changes.outputs.sdk') == 'true' else []}
    delivery = json.loads(json.dumps(delivery_outputs(plan, values['github.event_name'], values['github.ref'])))
    ancestors = set()
    pending = [caller]
    while pending:
        needs = jobs[pending.pop()].get('needs', [])
        for parent in ([needs] if isinstance(needs, str) else needs):
            if parent not in ancestors:
                ancestors.add(parent)
                pending.append(parent)
    results = {values.get(f'needs.{parent}.result', 'success') for parent in ancestors}
    direct = jobs[caller].get('needs', [])
    direct_results = {values.get(f'needs.{parent}.result', 'success') for parent in ([direct] if isinstance(direct, str) else direct)}
    selected = caller not in DELIVERY_FILTERS or values.get('needs.dataset-mutation-selection.outputs.' + DELIVERY_FILTERS[caller]) == 'true'
    # failure() sees every ancestor; contains(needs.*.result, 'cancelled') sees direct needs.
    return bool(not cancelled and 'failure' not in results and 'cancelled' not in direct_results
                and values['needs.ci-ready.result'] == 'success' and delivery[caller] and selected)


@pytest.mark.parametrize("job_id", MAIN_CALLERS)
def test_main_callers_require_selection_gate_and_prerequisites(job_id, subtests):
    jobs = load_workflow(ROOT / ".github/workflows/ci.yml")["jobs"]
    job = jobs[job_id]
    needs = job["needs"] if isinstance(job["needs"], list) else [job["needs"]]
    from scripts.terraform_target_contracts import registry
    declarations = registry(section="deployments")
    name = job_id.replace('-', '_')
    optional = [parent.replace('_', '-') for parent in deployment_prerequisites(ROOT)[name]] if name in declarations else [parent for parent in needs if parent.replace('-', '_') in declarations]
    assert set(optional) == {parent for parent in needs if parent.replace('-', '_') in declarations}
    values = {
        "github.event_name": "push", "github.ref": "refs/heads/main",
        **{f"needs.{parent}.result": "success" for parent in needs},
        **{f"needs.geospatial-changes.outputs.{target}": "true" for target in DEPLOYMENTS},
        'needs.geospatial-changes.outputs.deployment_readiness': 'true',
        "needs.dataset-mutation-selection.outputs.has_repo_alert": "true",
        "needs.dataset-mutation-selection.outputs.has_mutations": "true",
        "needs.dataset-mutation-selection.outputs.has_catalog_changes": "true",
    }
    for event, ref in [('push', 'refs/heads/main'), ('push', 'refs/heads/topic'),
                       ('pull_request', 'refs/heads/main'), ('workflow_dispatch', 'refs/heads/main')]:
        for selected in (True, False):
            for result in ('success', 'failure', 'skipped', 'cancelled'):
                with subtests.test(event=event, selected=selected, result=result):
                    case = values | {'github.event_name': event, 'github.ref': ref,
                                     **{f'needs.geospatial-changes.outputs.{parent.replace("-", "_")}': str(selected).lower() for parent in optional},
                                     **{f'needs.{parent}.result': result for parent in optional}}
                    assert condition_result(job, case) is (event == 'push' and ref == 'refs/heads/main' and (not optional or result not in {'failure', 'cancelled'}))
    assert condition_result(job, values, cancelled=True) is False
    for dependency in set(needs) - {'geospatial-changes'}:
        for result in ('failure', 'cancelled'):
            with subtests.test(dependency=dependency, result=result):
                assert condition_result(job, values | {f'needs.{dependency}.result': result}) is False
    selection = {'announce-repo-functionality': 'needs.dataset-mutation-selection.outputs.has_repo_alert',
                 'publish-reviewed-dataset': 'needs.dataset-mutation-selection.outputs.has_mutations',
                 'refresh-reviewed-catalog': 'needs.dataset-mutation-selection.outputs.has_catalog_changes'}.get(job_id, f'needs.geospatial-changes.outputs.{name}')
    if job_id != 'dataset-mutation-selection':
        assert condition_result(job, values | {selection: 'false'}) is False


@pytest.mark.parametrize("paths,expected", [
    ([], set()),
    (["docs/ci-preflight.md"], set()),
    ([".github/workflows/ci.yml", "tests/test_ci_deployments.py"], set()),
    (["ingestion/mpatlas_monthly/run.py"], {"mpatlas"}),
    (["ingestion/wdpa_monthly/run.py"], {"wdpa"}),
    (["ingestion/wdpa_monthly/run.py", "terraform/README.md"], {"wdpa"}),
    (["ingestion/wdpa_monthly/run.py", "ingestion/eamlis_monthly/run.py"],
     {"wdpa", "eamlis"}),
    (["ingestion/wdpa_monthly/run.py", "terraform/envs/prod/wdpa_monthly.tf"],
     {"wdpa"}),
    (["terraform/envs/prod/mpatlas_monthly.tf"],
     {"mpatlas", "ingestion_iam"}),
])
def test_plan_outputs_drive_only_selected_callers_with_optional_ancestors_skipped(tmp_path, monkeypatch, paths, expected):
    from test_ci_preflight import fixture_repo, emit_plan

    jobs = load_workflow(ROOT / ".github/workflows/ci.yml")["jobs"]
    root, git = fixture_repo(tmp_path)
    base = git("rev-parse", "HEAD")
    for path in paths:
        destination = root / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text((destination.read_text() if destination.exists() else "") + "\n# fixture change\n")
    git("add", ".")
    git("commit", "--allow-empty", "-m", "selected changes")
    plan, outputs = emit_plan(root, base, tmp_path)
    assert set(plan["deployments"]) == expected
    values = {
        "github.event_name": "push", "github.ref": "refs/heads/main",
        **{f"needs.geospatial-changes.outputs.{key}": value for key, value in outputs.items()},
        **{f"needs.{name}.result": "success" if name.replace("-", "_") in expected else "skipped"
           for name in jobs},
        "needs.ci-ready.result": "success", "needs.geospatial-changes.result": "success",
    }
    # Selected runtime jobs must run while unrelated and unselected IAM jobs skip.
    assert values["needs.dataset-usage.result"] == ("success" if "dataset_usage" in expected else "skipped")
    for target in DEPLOYMENTS:
        assert condition_result(jobs[target.replace("_", "-")], values) is (target in expected)


@pytest.mark.parametrize("target,selection", [(None, "eamlis"), ("mpatlas-monthly", "mpatlas"), ("eamlis-monthly", "eamlis"), ("wdpa-monthly", "wdpa"), ("sea-ice-daily", "sea_ice")])
def test_plan_cli_emits_the_same_single_image_for_build_and_upload(tmp_path, monkeypatch, target, selection):
    from test_ci_preflight import fixture_repo, emit_plan

    root, git = fixture_repo(tmp_path)
    if target:
        holds = root / ci_preflight.INGESTION_ROLLOUT_HOLDS
        entry = dict(owner="jonaraphael", reason="review", expires_at="2099-01-01T00:00:00Z")
        holds.write_text(json.dumps({selection: entry}))
        git("add", ".")
        git("commit", "-m", "hold")
    base = git("rev-parse", "HEAD")
    if target:
        holds.write_text("{}\n")
    else:
        source = root / "ingestion/eamlis_monthly/run.py"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(source.read_text() + "\n# fixture change\n")
        target = "eamlis-monthly"
    git("add", ".")
    git("commit", "-m", "release")
    plan, outputs = emit_plan(root, base, tmp_path, isolated=target == 'wdpa-monthly')
    assert plan["deployments"] == [selection] and outputs[selection] == "true"
    assert "production-images" in plan["suites"] and "geospatial-integration" in plan["suites"]
    assert json.loads(outputs["production-image-targets"]) == plan["production_image_targets"] == [target]
    from test_ci_preflight import results_for, jobs_for
    ci_preflight.verify_results(plan, results_for(plan), jobs_for(plan))
    command = suite_commands("production-images", root, plan, tmp_path)[0][0]
    assert command[command.index("--target") + 1] == target
    assert json.loads(command[command.index("--limits") + 1]) == plan["production_image_limits"][target]
    assert command[-2:] == ["--output", str(tmp_path / target)]


@pytest.mark.parametrize("target", ["catalog-viewer", "dataset-usage", "cron-alert-policy"])
def test_nested_iam_bootstraps_use_the_same_infrastructure_selection(target):
    caller = load_workflow(ROOT / ".github/workflows/ci.yml")["jobs"][target]
    assert caller["with"]["sync_iam"] == "${{ contains(fromJSON(needs.ci-ready.outputs.bootstrap || '[]'), '" + target.replace('-', '_') + "') }}"
    workflow = load_workflow(ROOT / caller["uses"].removeprefix("./"))
    assert workflow_triggers(workflow)["workflow_call"]["inputs"]["sync_iam"] == {"required": True, "type": "boolean"}
    if target == "catalog-viewer":
        steps = workflow["jobs"]["bootstrap"]["steps"]
        start = next(i for i, step in enumerate(steps) if step.get("name") == "Verify existing project IAM bootstrap authority")
        end = next(i for i, step in enumerate(steps) if step.get("name") == "Terraform apply Secret Manager IAM bootstrap") + 1
        assert workflow["jobs"]["ready"]["needs"] == ["prepare", "bootstrap"]
        assert "Verify catalog-viewer live dependencies" in workflow_steps_by_name(workflow, "ready")
        assert "(github.event_name == 'workflow_dispatch' || inputs.sync_iam)" in workflow['jobs']['bootstrap']['if']
        assert all('if' not in step for step in steps[start:end])
        return
    from scripts import deployment_receipt_contracts
    assert deployment_receipt_contracts.boundaries(ROOT) == []


@pytest.mark.parametrize("has_mutations", ["true", "false"])
def test_authoritative_mutation_selection_keeps_its_own_refresh_path(has_mutations):
    jobs = load_workflow(ROOT / '.github/workflows/ci.yml')['jobs']
    step = next(step for step in jobs['dataset-mutation-selection']['steps'] if step.get('id') == 'catalog')
    assert step["if"] == "${{ steps.discover.outputs.has_mutations == 'false' }}"
    values = {"github.event_name": "push", "github.ref": "refs/heads/main",
              "needs.ci-ready.result": "success", "needs.dataset-mutation-selection.result": "success",
              "needs.geospatial-changes.outputs.pmtiles_cdn": "false", "needs.pmtiles-cdn.result": "skipped",
              "needs.dataset-mutation-selection.outputs.has_mutations": has_mutations,
              # A skipped catalog step has no should_deploy output.
              "needs.dataset-mutation-selection.outputs.has_catalog_changes": "" if has_mutations == 'true' else "true"}
    assert condition_result(jobs['publish-reviewed-dataset'], values) is (has_mutations == 'true')
    assert condition_result(jobs['refresh-reviewed-catalog'], values) is (has_mutations == 'false')


def test_nested_terraform_bootstrap_requires_its_registered_readiness():
    plan = resolve_selection([".github/actions/deployment-bootstrap/terraform/action.yml"])
    assert plan['deployments'] == [] and plan['readiness_targets'] == ['artifact_registry_iam']


@pytest.mark.parametrize("declared", [True, False])
def test_plan_rejects_an_iam_prerequisite_without_its_declaration(tmp_path, monkeypatch, declared):
    from scripts import ci_contract as contract
    prod = tmp_path / "terraform/envs/prod"
    prod.mkdir(parents=True)
    (prod / "iam.tf").write_text('resource "google_project_iam_custom_role" "reader" {}' if declared else '')
    (tmp_path / ".github").mkdir()
    (tmp_path / ".github/terraform-targets.json").write_text(json.dumps({"schema_version": 1, "workflows": {}, "targets": {
        "sync/prepare": {"workflow": "fixture.yml", "resources": [], "prerequisites": ["google_project_iam_custom_role.reader"]}}}))
    monkeypatch.setattr(contract, "ROOT", tmp_path)
    contract.deployment_paths.cache_clear()
    try:
        if declared:
            contract.deployment_paths()
        else:
            with pytest.raises(ValueError, match="IAM prerequisite has no Terraform declaration"):
                contract.deployment_paths()
    finally:
        contract.deployment_paths.cache_clear()
