import ast
import json
import re
import sys
from graphlib import TopologicalSorter
from pathlib import Path

import pytest

from scripts.ci_contract import ALWAYS, DEPLOYMENTS, select_deployments, select_suites
from scripts import ci_preflight
from scripts.ci_preflight import suite_commands
from workflow_helpers import load_workflow, workflow_triggers

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("paths", [None, ["unknown/component"], ["docs/ci-reliability.md"], ["tests/test_publication.py"], [".github/workflows/ci.yml"]])
def test_broad_validation_never_authorizes_unrelated_deployments(paths):
    assert select_deployments(paths) == []
    assert ALWAYS <= set(select_suites(paths)[0])


@pytest.mark.parametrize("job", ["mpatlas_monthly", "eamlis_monthly", "sea_ice_daily", "wdpa_monthly", "dataset_usage"])
def test_ingestion_readme_changes_do_not_launch_production_jobs(job):
    path = f"ingestion/{job}/README.md"
    assert select_deployments([path]) == []
    assert set(select_suites([path])[0]) == ALWAYS


@pytest.mark.parametrize("job,target", [("eamlis_monthly", "eamlis"), ("sea_ice_daily", "sea_ice")])
@pytest.mark.parametrize("runtime_file", ["run.py", "Dockerfile", "README.md.py"])
def test_readme_exclusion_keeps_mixed_runtime_changes_deployable(job, target, runtime_file):
    paths = [f"ingestion/{job}/README.md", f"ingestion/{job}/{runtime_file}"]
    assert set(select_deployments(paths)) == {target}
    assert {"geospatial-integration", "production-images"} <= set(select_suites(paths)[0])


def test_producer_changes_require_staged_evidence_before_launching_production_validation():
    assert select_deployments(["ingestion/wdpa_monthly/Dockerfile", "scripts/wdpa_input_memory_probe.py"]) == []
    assert select_deployments(["catalog/wdpa-staged-validation.json"]) == []
    assert "production-images" in select_suites(["ingestion/wdpa_monthly/Dockerfile"])[0]


@pytest.mark.parametrize("path,target", [("ingestion/eamlis_monthly/run.py", "eamlis"), ("ingestion/sea_ice_daily/run.py", "sea_ice"), ("ingestion/wdpa_monthly/Dockerfile.promotion", "wdpa")])
def test_runtime_release_does_not_select_iam_syncs(path, target):
    assert set(select_deployments([path])) == {target}


def test_iam_only_bootstrap_cannot_launch_unready_ingestion_dependents():
    assert select_deployments(["terraform/envs/prod/scheduled_ingestion_deploy_iam.tf"]) == ["ingestion_iam"]


def test_shared_runtime_and_terraform_modules_select_cross_component_dependents():
    assert set(select_deployments(["ingestion/common/publication.py"])) == {"mpatlas", "eamlis", "wdpa", "sea_ice"}
    assert "catalog_viewer" in select_deployments(["terraform/modules/cloud_run_job/main.tf"])
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
    assert {"geospatial-integration", "production-images"} <= set(select_suites([path])[0])


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


def test_release_contracts_run_before_python_tests_for_selected_release_changes():
    plan = {"base": "base", "tested_sha": "head", "deployments": ["wdpa", "ingestion_iam"]}
    commands = [args for args, _ in suite_commands("tests", ROOT, plan, ROOT)]
    gate = next(args for args in commands if "scripts/release_contracts.py" in args)
    assert gate[-4:] == ["--target", "iam", "--target", "wdpa"]
    assert commands.index(gate) < next(index for index, args in enumerate(commands) if "pytest" in args)


def test_all_automatic_deployment_callers_require_ci_ready_and_pass_the_exact_tested_sha():
    workflow = load_workflow(ROOT / ".github/workflows/ci.yml")
    jobs = workflow["jobs"]
    assert "production-images" in jobs["ci-ready"]["needs"]
    for target in DEPLOYMENTS:
        job = jobs[target.replace("_", "-")]
        assert "ci-ready" in job["needs"]
        assert "github.event_name == 'push'" in job["if"]
        assert "needs.ci-ready.result == 'success'" in job["if"]
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
    assert "needs.ci-ready.result == 'success'" in publisher["if"]
    assert "needs.pmtiles-cdn.result == 'success'" in publisher["if"]
    assert "needs.geospatial-changes.outputs.pmtiles_cdn != 'true'" in publisher["if"]


def test_actual_production_images_are_part_of_the_gate_and_use_the_shared_command():
    command = [args for args, _ in suite_commands("production-images", ROOT, {"base": "base", "tested_sha": "head"}, ROOT)]
    assert command[-1][-3:] == ["scripts/production_image_contracts.py", "--output", str(ROOT)]
    suite = load_workflow(ROOT / ".github/workflows/ci.yml")["jobs"]["production-images"]
    assert "scripts/ci_preflight.py run-suite" in next(step["run"] for step in suite["steps"] if "run" in step)
    upload = next(step for step in suite["steps"] if step.get("id") == "retained-results")
    assert upload["with"]["compression-level"] == 0
    assert upload["with"]["if-no-files-found"] == "error"


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
    assert select_deployments([path]) == (["artifact_registry_iam", "cron_alert_policy"] if path.startswith("terraform/") else ["cron_alert_policy"])
    assert "production-images" in select_suites([path])[0]


# These edges order mutations only when both owners are selected. Other direct
# prerequisites (apart from the selector already verified by ci-ready) must pass.
OPTIONAL_NEEDS = {
    "ingestion-iam": ("dataset-usage",),
    "scratch-cleanup-iam": ("dataset-usage",),
    "mpatlas": ("ingestion-iam", "artifact-registry-iam", "cron-alert-policy"),
    "wdpa": ("ingestion-iam", "artifact-registry-iam", "cron-alert-policy"),
    "dataset-usage": ("artifact-registry-iam", "catalog-viewer", "pmtiles-cdn"),
    "cron-alert-policy": ("ingestion-iam", "artifact-registry-iam"),
    "eamlis": ("ingestion-iam", "artifact-registry-iam"),
    "sea-ice": ("ingestion-iam", "artifact-registry-iam"),
    "catalog-viewer": ("artifact-registry-iam",),
    "publish-reviewed-dataset": ("pmtiles-cdn",),
    "refresh-reviewed-catalog": ("pmtiles-cdn",),
}
MAIN_CALLERS = [target.replace("_", "-") for target in DEPLOYMENTS] + [
    "dataset-mutation-selection", "announce-repo-functionality", "publish-reviewed-dataset", "refresh-reviewed-catalog",
]


def condition_result(job, values, *, cancelled=False):
    expression = job["if"].removeprefix("${{").removesuffix("}}").strip()
    # This is a required Actions scheduling contract, not a local reimplementation
    # of its scheduler: without a status function GitHub injects success(), which
    # propagates skipped ancestors even when the explicit condition is true.
    assert "!cancelled()" in expression
    expression = expression.replace("!cancelled()", repr(not cancelled))
    expression = re.sub(r"(?:needs|github|inputs)\.[\w.-]+", lambda match: repr(values[match[0]]), expression)
    expression = re.sub(r"!(?!=)", "not ", expression)
    tree = ast.parse(expression.replace("&&", " and ").replace("||", " or "), mode="eval")
    assert all(isinstance(node, (ast.Expression, ast.BoolOp, ast.And, ast.Or,
                                 ast.Compare, ast.Eq, ast.NotEq, ast.Constant, ast.UnaryOp, ast.Not)) for node in ast.walk(tree))
    return eval(compile(tree, "actual-ci-condition", "eval"), {"__builtins__": {}})


@pytest.mark.parametrize("job_id", MAIN_CALLERS)
def test_main_callers_require_selection_gate_and_prerequisites(job_id, subtests):
    job = load_workflow(ROOT / ".github/workflows/ci.yml")["jobs"][job_id]
    needs = job["needs"] if isinstance(job["needs"], list) else [job["needs"]]
    optional = OPTIONAL_NEEDS.get(job_id, ())
    assert set(optional) <= set(needs)
    values = {
        "github.event_name": "push", "github.ref": "refs/heads/main",
        **{f"needs.{name}.result": "success" for name in needs},
        **{f"needs.geospatial-changes.outputs.{target}": "true" for target in DEPLOYMENTS},
        "needs.dataset-mutation-selection.outputs.has_repo_alert": "true",
        "needs.dataset-mutation-selection.outputs.has_mutations": "true",
        "needs.dataset-mutation-selection.outputs.has_catalog_changes": "true",
    }
    assert condition_result(job, values) is True
    assert condition_result(job, values, cancelled=True) is False
    selection = {
        "announce-repo-functionality": "needs.dataset-mutation-selection.outputs.has_repo_alert",
        "publish-reviewed-dataset": "needs.dataset-mutation-selection.outputs.has_mutations",
        "refresh-reviewed-catalog": "needs.dataset-mutation-selection.outputs.has_catalog_changes",
    }.get(job_id, f"needs.geospatial-changes.outputs.{job_id.replace('-', '_')}")
    if job_id != "dataset-mutation-selection":
        with subtests.test(selection="unselected"):
            assert condition_result(job, {**values, selection: "false"}) is False
    for event, ref in [("pull_request", "refs/heads/main"), ("workflow_dispatch", "refs/heads/main"),
                       ("push", "refs/heads/topic")]:
        with subtests.test(event=event, ref=ref):
            assert condition_result(job, {**values, "github.event_name": event, "github.ref": ref}) is False
    for dependency in set(needs) - {"geospatial-changes"}:
        for result in ("failure", "cancelled", "skipped"):
            with subtests.test(dependency=dependency, result=result):
                assert condition_result(job, {**values, f"needs.{dependency}.result": result}) is False
    for dependency in optional:
        with subtests.test(unselected_dependency=dependency):
            assert condition_result(job, {
                **values, f"needs.{dependency}.result": "skipped",
                f"needs.geospatial-changes.outputs.{dependency.replace('-', '_')}": "false",
            }) is True
    if optional:
        with subtests.test(all_optional_dependencies="unselected"):
            assert condition_result(job, {
                **values, **{f"needs.{name}.result": "skipped" for name in optional},
                **{f"needs.geospatial-changes.outputs.{name.replace('-', '_')}": "false" for name in optional},
            }) is True


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
     {"wdpa", "ingestion_iam", "artifact_registry_iam"}),
    (["terraform/envs/prod/mpatlas_monthly.tf"],
     {"mpatlas", "ingestion_iam", "artifact_registry_iam", "cron_alert_policy"}),
])
def test_plan_outputs_drive_only_selected_callers_with_optional_ancestors_skipped(tmp_path, monkeypatch, paths, expected):
    from test_ci_preflight import fixture_repo

    jobs = load_workflow(ROOT / ".github/workflows/ci.yml")["jobs"]
    root, git = fixture_repo(tmp_path)
    base = git("rev-parse", "HEAD")
    for path in paths:
        destination = root / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text("changed fixture\n")
    git("add", ".")
    git("commit", "--allow-empty", "-m", "selected changes")
    monkeypatch.chdir(root)
    monkeypatch.setattr(sys, "argv", ["ci_preflight.py", "plan", "--base", base,
                                     "--output", str(tmp_path / "plan.json"),
                                     "--github-output", str(tmp_path / "outputs")])
    assert ci_preflight.main() == 0
    assert set(json.loads((tmp_path / "plan.json").read_text())["deployments"]) == expected
    outputs = dict(line.split("=", 1) for line in (tmp_path / "outputs").read_text().splitlines())
    values = {
        "github.event_name": "push", "github.ref": "refs/heads/main",
        **{f"needs.geospatial-changes.outputs.{key}": value for key, value in outputs.items()},
        **{f"needs.{name}.result": "success" if name.replace("-", "_") in expected else "skipped"
           for name in jobs},
        "needs.ci-ready.result": "success", "needs.geospatial-changes.result": "success",
    }
    # Selected runtime jobs must run while unrelated and unselected IAM jobs skip.
    assert values["needs.dataset-usage.result"] == "skipped"
    for target in DEPLOYMENTS:
        assert condition_result(jobs[target.replace("_", "-")], values) is (target in expected)


@pytest.mark.parametrize("target", ["catalog-viewer", "dataset-usage", "cron-alert-policy"])
def test_nested_iam_bootstraps_use_the_same_infrastructure_selection(target):
    caller = load_workflow(ROOT / ".github/workflows/ci.yml")["jobs"][target]
    assert caller["with"]["sync_iam"] == "${{ needs.geospatial-changes.outputs.artifact_registry_iam == 'true' }}"
    workflow = load_workflow(ROOT / caller["uses"].removeprefix("./"))
    assert workflow_triggers(workflow)["workflow_call"]["inputs"]["sync_iam"] == {"required": True, "type": "boolean"}
    if target == "catalog-viewer":
        steps = workflow["jobs"]["deploy"]["steps"]
        start = next(i for i, step in enumerate(steps) if step.get("name") == "Verify existing project IAM bootstrap authority")
        end = next(i for i, step in enumerate(steps) if step.get("name") == "Verify catalog-viewer live dependencies")
        for step in steps[start:end]:
            assert "(github.event_name == 'workflow_dispatch' || inputs.sync_iam)" in step["if"]
        return
    jobs = workflow["jobs"]
    values = {"github.event_name": "push", "github.ref": "refs/heads/main", "inputs.sync_iam": False,
              "needs.bootstrap.result": "skipped", "needs.resources.result": "skipped"}
    assert condition_result(jobs["bootstrap"], values) is False
    consumer = jobs["deploy" if target == "dataset-usage" else "sync"]
    assert condition_result(consumer, values) is True
    values["inputs.sync_iam"] = True
    assert condition_result(jobs["bootstrap"], values) is True
    assert condition_result(consumer, values) is False
    values.update({"needs.bootstrap.result": "success", "needs.resources.result": "success"})
    assert condition_result(consumer, values) is True
    manual = {**values, "github.event_name": "workflow_dispatch", "inputs.sync_iam": False}
    assert condition_result(jobs["bootstrap"], manual) is True
    assert condition_result(consumer, {**manual, "needs.bootstrap.result": "skipped"}) is False
    for dependency in consumer["needs"] if isinstance(consumer["needs"], list) else [consumer["needs"]]:
        assert condition_result(consumer, {**values, f"needs.{dependency}.result": "failure"}) is False


@pytest.mark.parametrize("has_mutations", ["true", "false"])
def test_authoritative_mutation_selection_keeps_its_own_refresh_path(has_mutations):
    jobs = load_workflow(ROOT / '.github/workflows/ci.yml')['jobs']
    step = next(step for step in jobs['dataset-mutation-selection']['steps'] if step.get('id') == 'catalog')
    expression = step['if'].removeprefix('${{').removesuffix('}}').strip()
    expression = expression.replace('steps.discover.outputs.has_mutations', repr(has_mutations))
    assert eval(expression, {"__builtins__": {}}) is (has_mutations == 'false')
    values = {"github.event_name": "push", "github.ref": "refs/heads/main",
              "needs.ci-ready.result": "success", "needs.dataset-mutation-selection.result": "success",
              "needs.geospatial-changes.outputs.pmtiles_cdn": "false", "needs.pmtiles-cdn.result": "skipped",
              "needs.dataset-mutation-selection.outputs.has_mutations": has_mutations,
              # A skipped catalog step has no should_deploy output.
              "needs.dataset-mutation-selection.outputs.has_catalog_changes": "" if has_mutations == 'true' else "true"}
    assert condition_result(jobs['publish-reviewed-dataset'], values) is (has_mutations == 'true')
    assert condition_result(jobs['refresh-reviewed-catalog'], values) is (has_mutations == 'false')
