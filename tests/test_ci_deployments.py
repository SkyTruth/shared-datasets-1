from pathlib import Path

import pytest

from scripts.ci_contract import ALWAYS, DEPLOYMENTS, select_deployments, select_suites
from scripts.ci_preflight import suite_commands
from workflow_helpers import load_workflow, workflow_triggers

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("paths", [None, ["unknown/component"], ["docs/ci-reliability.md"], ["tests/test_publication.py"], [".github/workflows/ci.yml"]])
def test_broad_validation_never_authorizes_unrelated_deployments(paths):
    assert select_deployments(paths) == []
    assert ALWAYS <= set(select_suites(paths)[0])


def test_producer_changes_require_staged_evidence_before_launching_production_validation():
    assert select_deployments(["ingestion/wdpa_monthly/Dockerfile", "scripts/wdpa_input_memory_probe.py"]) == []
    assert select_deployments(["catalog/wdpa-staged-validation.json"]) == ["wdpa_processing", "artifact_registry_iam"]
    assert "production-images" in select_suites(["ingestion/wdpa_monthly/Dockerfile"])[0]


@pytest.mark.parametrize("path,target", [("ingestion/eamlis_monthly/run.py", "eamlis"), ("ingestion/sea_ice_daily/run.py", "sea_ice"), ("ingestion/wdpa_monthly/Dockerfile.promotion", "wdpa")])
def test_ingestion_release_selects_its_shared_bootstrap_dependency_once(path, target):
    assert set(select_deployments([path])) == {target, "ingestion_iam", "artifact_registry_iam"}


def test_iam_only_bootstrap_cannot_launch_unready_ingestion_dependents():
    assert select_deployments(["terraform/envs/prod/scheduled_ingestion_deploy_iam.tf"]) == ["ingestion_iam"]


def test_shared_runtime_and_terraform_modules_select_cross_component_dependents():
    assert set(select_deployments(["ingestion/common/publication.py"])) == {"eamlis", "wdpa", "sea_ice", "ingestion_iam", "artifact_registry_iam"}
    assert "catalog_viewer" in select_deployments(["terraform/modules/cloud_run_job/main.tf"])
    assert set(select_deployments(["catalog/shared-datasets-catalog.csv"])) == {"eamlis", "wdpa", "ingestion_iam", "pmtiles_cdn", "artifact_registry_iam"}


@pytest.mark.parametrize("path,targets", [
    ("scripts/pmtiles_zoom.py", {"eamlis", "wdpa", "sea_ice"}),
    ("scripts/slack_notify.py", {"eamlis", "wdpa", "sea_ice"}),
    ("scripts/catalog_csv.py", {"eamlis", "wdpa"}),
    ("scripts/feature_metadata_translation_reuse.py", {"wdpa"}),
    ("catalog/feature-identity-resolutions/wdpa.json", {"wdpa", "sea_ice"}),
    ("docs/assets/ims-sea-ice-extent.md", {"sea_ice"}),
])
def test_image_copies_are_release_and_native_validation_dependencies(path, targets):
    assert set(select_deployments([path])) == targets | {"ingestion_iam", "artifact_registry_iam"}
    assert {"geospatial-integration", "production-images"} <= set(select_suites([path])[0])


@pytest.mark.parametrize("path", ["catalog/shared-datasets-catalog.csv", "terraform/envs/prod/shared_bucket_public.tf", "terraform/envs/prod/variables.tf", "terraform/envs/prod/versions.tf"])
def test_cdn_catalog_and_shared_bucket_dependencies_select_route_sync(path):
    assert "pmtiles_cdn" in select_deployments([path])


def test_catalog_metadata_updates_do_not_redeploy_consumers_but_contract_changes_do():
    before = "asset_slug,title,canonical_path,translation_locales,translation_fields,access_tier,status,has_pmtiles,available_formats\neamlis-abandoned-mine-land-inventory,Original,gs://skytruth-shared-datasets-1/example/latest/a.fgb,es,PA_NAME,public,active,true,fgb;pmtiles\n"
    path = ["catalog/shared-datasets-catalog.csv"]
    assert select_deployments(path, catalog_snapshots=(before, before.replace("Original", "Updated"))) == []
    assert set(select_deployments(path, catalog_snapshots=(before, before.replace(",es,", ",es;fr,")))) == {"eamlis", "ingestion_iam", "artifact_registry_iam"}
    assert select_deployments(path, catalog_snapshots=(before, before.replace("/example/", "/new-prefix/"))) == ["pmtiles_cdn"]
    assert "catalog_viewer" not in select_deployments(path, catalog_snapshots=(before, before.replace("/example/", "/new-prefix/")))
    assert "catalog_viewer" in select_deployments(path + ["scripts/compare_releases.py"], catalog_snapshots=(before, before))


def test_catalog_snapshot_uncertainty_and_duplicate_slugs_cannot_suppress_validation():
    path = ["catalog/shared-datasets-catalog.csv"]
    assert set(select_deployments(path)) == {"eamlis", "wdpa", "ingestion_iam", "pmtiles_cdn", "artifact_registry_iam"}
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
    for target in ("eamlis", "wdpa", "sea-ice", "wdpa-processing", "catalog-viewer"):
        assert "artifact-registry-iam" in jobs[target]["needs"]
    publisher = jobs["publish-reviewed-dataset"]
    assert "pmtiles-cdn" in publisher["needs"]
    assert "always()" in publisher["if"]  # A legitimately unselected CDN job is skipped.
    assert "needs.ci-ready.result == 'success'" in publisher["if"]
    assert "needs.pmtiles-cdn.result == 'success'" in publisher["if"]
    assert "needs.geospatial-changes.outputs.pmtiles_cdn != 'true'" in publisher["if"]


def test_actual_production_images_are_part_of_the_gate_and_use_the_shared_command():
    command = [args for args, _ in suite_commands("production-images", ROOT, {"base": "base", "tested_sha": "head"}, ROOT)]
    assert command[-1] == ["uv", "run", "--no-sync", "python", "scripts/production_image_contracts.py", "--output", str(ROOT)]
    suite = load_workflow(ROOT / ".github/workflows/ci.yml")["jobs"]["production-images"]
    assert "scripts/ci_preflight.py run-suite" in next(step["run"] for step in suite["steps"] if "run" in step)
    upload = next(step for step in suite["steps"] if step.get("uses", "").startswith("actions/upload-artifact@"))
    assert upload["with"]["compression-level"] == 0
    assert upload["with"]["if-no-files-found"] == "error"
