"""Fail preflight if a deployment replaces CI image bytes or loses their proof."""

import copy
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys

import pytest

from scripts.ci_contract import ALWAYS, expected_tools, verify_results
from scripts.tested_image_bundle import TARGETS as IMAGE_TARGETS
from scripts.ingestion_targets import DEPLOY_TARGETS as INGESTION_CONFIGS
from scripts.deployment_receipt_contracts import expanded_steps
from ci_result_fixtures import production_images
from workflow_helpers import load_workflow

ROOT = Path(__file__).resolve().parents[1]
TARGETS = ("wdpa-monthly", "mpatlas-monthly", "eamlis-monthly", "sea-ice-daily", "catalog-viewer", "dataset-usage")


def deployment_steps(target):
    value = load_workflow(ROOT / ".github/workflows" / f"{target}-deploy.yml")
    if value['jobs'].get('deploy', {}).get('uses') == './.github/workflows/ingestion-deploy.yml':
        shared = load_workflow(ROOT / '.github/workflows/ingestion-deploy.yml')
        config = INGESTION_CONFIGS[target]
        steps = copy.deepcopy(list(expanded_steps(ROOT, shared['jobs']['prepare'])) + list(expanded_steps(ROOT, shared['jobs']['apply'])))
        for step in steps:
            if 'run' in step:
                step['run'] = step['run'].replace('$WORKFLOW_FILE', config['workflow']).replace('$TARGET', target)
        return steps
    return [step for name in ('prepare', 'deploy') for step in expanded_steps(ROOT, value['jobs'][name])]


def image_contract(steps, target):
    runs = [step.get("run", "").replace("\\\n", " ") for step in steps]
    assert not any(re.search(r"\bdocker\s+(?:(?:image|buildx)\s+)?build(?:\s|$)", run) for run in runs), "deployment must not rebuild tested bytes"
    loaders = [index for index, run in enumerate(runs) if "tested_image_authorization.py" in run]
    assert len(loaders) == 1, "one authenticated retained-image loader is required"
    loaded = loaders[0]
    assert steps[loaded].get("id") == "tested-image"
    assert not steps[loaded].get("continue-on-error") and "||" not in runs[loaded], "loader cannot have a fallback"
    arguments = shlex.split(runs[loaded])
    for flag, expected in (("--workflow", target + "-deploy.yml"), ("--target", target), ("--executor-sha", "$EXECUTOR_SHA"),
                           ("--source-run-id", "$SOURCE_RUN_ID"), ("--source-run-attempt", "$SOURCE_RUN_ATTEMPT")):
        assert arguments[arguments.index(flag) + 1] == expected, "retained image must bind exact workflow, target and tested source"
    assert steps[loaded].get("env", {}).get("GH_TOKEN") == "${{ github.token }}"
    assert any("deployment_revision.py verify --bootstrap" in run for run in runs[:loaded]), "trusted source proof must precede loading"
    assert any("deployment_revision.py check" in run for run in runs[:loaded]), "replay proof must precede loading"
    tagged = [index for index, run in enumerate(runs) if re.search(r"\bdocker\s+tag\s", run)]
    assert len(tagged) == 1 and tagged[0] > loaded
    tag_step = steps[tagged[0]]
    assert tag_step.get("env", {}).get("TESTED_IMAGE_ID") == "${{ steps.tested-image.outputs.image_id }}"
    assert 'docker tag "$TESTED_IMAGE_ID"' in runs[tagged[0]], "only the verified daemon image reference may be tagged"
    if target == "catalog-viewer":
        assert tag_step.get("env", {}).get("TESTED_IMAGE_CONFIG") == "${{ steps.tested-image.outputs.config_digest }}"
        claim = next(step for step in steps if 'deployment_revision.py start --workflow catalog-viewer-deploy.yml' in step.get('run', ''))
        assert claim['env']['CATALOG_VIEWER_LOCAL_DIGEST'] == '${{ steps.tested-image.outputs.config_digest }}', "viewer claim must bind canonical config bytes"
        assert '--artifact "catalog-viewer-image@${CATALOG_VIEWER_LOCAL_DIGEST}"' in claim['run']
    pushed = [index for index, run in enumerate(runs) if re.search(r"\bdocker\s+push\s", run)]
    assert len(pushed) == 1 and pushed[0] > tagged[0]
    push = steps[pushed[0]]
    assert push.get("env", {}).get("TESTED_IMAGE_CONFIG") == "${{ steps.tested-image.outputs.config_digest }}"
    assert re.search(r"\bimagetools\s+inspect\s+--raw\s", runs[pushed[0]]), "registry config must be read from the actual pushed manifest"
    assert 'manifest.get("config", {}).get("digest")' in runs[pushed[0]] and "assert actual == expected" in runs[pushed[0]], "registry must retain the tested config"
    assert runs[pushed[0]].index("assert actual == expected") < runs[pushed[0]].index("image_ref="), "unverified registry bytes cannot become a deployment input"
    applied = [index for index, run in enumerate(runs) if re.search(r"(?:terraform_retry\.sh|\bterraform\b).*\sapply(?:\s|$)", run)]
    assert applied and all(index > pushed[0] for index in applied), "registry config proof must precede every Terraform mutation"
    return push


@pytest.mark.parametrize("target", TARGETS)
def test_actual_registry_manifest_verifier_rejects_another_config(tmp_path, target):
    push = image_contract(deployment_steps(target), target)
    line = next(line.strip() for line in push["run"].splitlines() if line.strip().startswith("python -c "))
    source = shlex.split(line)[2]
    expected = "sha256:" + "1" * 64
    manifest = tmp_path / "manifest.json"
    for actual in (expected, "sha256:" + "2" * 64, None):
        manifest.write_text(json.dumps({"config": {"digest": actual}}))
        completed = subprocess.run([sys.executable, "-c", source, str(manifest)], env={"TESTED_IMAGE_CONFIG": expected, "TESTED_IMAGE_ID": "sha256:" + "3" * 64},
                                   capture_output=True, text=True)
        assert (completed.returncode == 0) == (actual == expected), completed.stderr


def image_results():
    selected = sorted(ALWAYS | {"production-images"})
    plan = {"schema_version": 1, "base": "a" * 40, "head": "b" * 40, "tested_sha": "c" * 40,
            "tree": "d" * 40, "contract_digest": "e" * 64, "suites": selected, "production_image_targets": sorted(IMAGE_TARGETS)}
    results = [{**{key: plan[key] for key in ("base", "head", "tested_sha", "tree", "contract_digest")},
                "suite": suite, "status": "success", "tools": expected_tools(suite),
                "commands": [{"argv": ["actual-small-image-fixtures"], "exit_code": 0}]} for suite in selected]
    image_result = next(result for result in results if result["suite"] == "production-images")
    image_result["production_images"] = production_images(plan["tested_sha"])
    return plan, results, image_result


@pytest.mark.parametrize("defect", ("missing", "partial", "foreign-sha", "foreign-image-tag", "wrong-platform", "oversized", "duplicate-kind", "daemon-id-field"))
def test_ci_ready_rejects_success_without_complete_exact_images(defect):
    plan, results, image_result = image_results()
    manifest = image_result["production_images"]
    if defect == "missing":
        del image_result["production_images"]
    elif defect == "partial":
        del manifest["images"]["sea-ice-daily"]
    elif defect == "foreign-sha":
        manifest["tested_sha"] = "f" * 40
    elif defect == "foreign-image-tag":
        manifest["images"]["catalog-viewer"]["source_tag"] = "unverified:latest"
    elif defect == "wrong-platform":
        manifest["images"]["eamlis-monthly"]["platform"] = "linux/arm64"
    elif defect == "oversized":
        manifest["images"]["sea-ice-daily"]["archive_size"] = 3 * 1024 ** 3
    elif defect == "daemon-id-field":
        image = manifest["images"]["catalog-viewer"]
        image["image_id"] = image.pop("config_digest")
    else:
        manifest["images"]["another-image"] = copy.deepcopy(manifest["images"]["catalog-viewer"])
    with pytest.raises(ValueError):
        verify_results(plan, results)



@pytest.mark.parametrize("paths,targets", [
    (["ingestion/eamlis_monthly/run.py"], {"eamlis-monthly"}),
    (["ingestion/wdpa_monthly/run.py"], {"catalog-viewer", "wdpa-monthly"}),
])
def test_gate_requires_exact_selected_subset_including_wdpa_only(paths, targets):
    plan, results, image_result = image_results()
    plan.update(changed_paths=paths, production_image_targets=sorted(targets))
    all_images = image_result["production_images"]["images"]
    image_result["production_images"]["images"] = {target: all_images[target] for target in targets}
    verify_results(plan, results)
    image_result["production_images"]["images"]["monitoring-slack"] = all_images["monitoring-slack"]
    with pytest.raises(ValueError, match="differs from validation selection"):
        verify_results(plan, results)
    image_result["production_images"]["images"] = {}
    if targets:
        with pytest.raises(ValueError, match="differs from validation selection"):
            verify_results(plan, results)
    plan["production_image_targets"] = []
    with pytest.raises(ValueError, match="invalid resolved"):
        verify_results(plan, results)


def test_production_cache_keeps_deployment_artifacts():
    job = load_workflow(ROOT / '.github/workflows/ci.yml')['jobs']['production-images']
    steps = job['steps']
    buildx = next(i for i, step in enumerate(steps) if step.get('uses') == 'docker/setup-buildx-action@v3')
    runtime = next(i for i, step in enumerate(steps) if step.get('name') == 'Expose the ephemeral Actions cache runtime')
    producer = next(i for i, step in enumerate(steps) if step.get('name') == 'Test the actual production image recipes')
    assert buildx < runtime < producer
    assert steps[producer]['env']['SHARED_DATASETS_GITHUB_IMAGE_CACHE'] == 'true'
    steps = load_workflow(ROOT / '.github/actions/ci-image-result/action.yml')['runs']['steps']
    retained = next(step['with'] for step in steps if step.get('id') == 'retained-results')
    assert retained['retention-days'] == 14
    assert '!${{ runner.temp }}/ci-results/production-images/${{ inputs.target }}/**/*.docker.tar' in retained['path'].splitlines()


@pytest.mark.parametrize('deployments', [[], ['sea_ice'], ['cron_alert_policy'], ['catalog_viewer', 'dataset_usage']])
def test_tar_retention_uses_only_selected_push_deliveries(deployments):
    from scripts.ci_contract import DEPLOYMENT_IMAGES, deploy_image_targets, resolve_selection
    workflow = load_workflow(ROOT / '.github/workflows/ci.yml')
    plan = {**resolve_selection(None), 'deployments': deployments}
    assert set(deploy_image_targets(plan)) == {DEPLOYMENT_IMAGES[name] for name in deployments}
    steps = workflow['jobs']['production-images']['steps']
    uploads = [step for step in steps if step.get('uses') == './.github/actions/ci-image-result']
    assert {step['with']['target'] for step in uploads} == set(IMAGE_TARGETS)
    for step in uploads:
        target = step['with']['target']
        assert step['with']['retain-image'] == "${{ github.event_name == 'push' && contains(fromJSON(needs.geospatial-changes.outputs.deploy_image_targets), '" + target + "') }}"
    artifact = next(step for step in load_workflow(ROOT / '.github/actions/ci-image-result/action.yml')['runs']['steps']
                    if step.get('id') == 'retained-image')
    assert artifact['with']['retention-days'] == 2
    assert workflow['env']['DOCKER_BUILD_RECORD_UPLOAD'] == 'false'


@pytest.mark.parametrize('target', sorted(IMAGE_TARGETS))
@pytest.mark.parametrize('outcome', ['success', 'failure', 'cancelled', 'skipped'])
def test_matrix_retention_requires_exact_target_tar_and_both_uploads(tmp_path, target, outcome):
    from test_ci_gate_artifact_transport import package_upload
    job = load_workflow(ROOT / '.github/workflows/ci.yml')['jobs']['production-images']
    assert job['strategy']['fail-fast'] is True
    assert job['strategy']['matrix']['group'] == '${{ fromJSON(needs.geospatial-changes.outputs.production_image_groups) }}'
    steps = load_workflow(ROOT / '.github/actions/ci-image-result/action.yml')['runs']['steps']
    retained = next(step for step in steps if step.get('id') == 'retained-image')
    path = tmp_path / 'ci-results/production-images' / target / 'images' / (target + '.docker.tar')
    path.parent.mkdir(parents=True)
    path.write_bytes(target.encode())
    name, archive = package_upload(retained, tmp_path, 'production-images-' + target, 1, tmp_path)
    assert name == f'ci-image-{target}-attempt1'
    import zipfile
    with zipfile.ZipFile(archive) as bundle:
        assert bundle.namelist() == [path.name]
        assert bundle.read(path.name) == target.encode()
    compact = next(step for step in steps if step.get('with', {}).get('name', '').startswith('ci-gate-result-'))
    for failed in ('retained-image', 'retained-results'):
        expression = compact['if'].removeprefix('${{').removesuffix('}}').replace('always()', 'True').replace('inputs.retain-image', repr('true'))
        for upload in ('retained-image', 'retained-results'):
            expression = expression.replace('steps.' + upload + '.outcome', repr(outcome if upload == failed else 'success'))
        assert eval(expression.replace('&&', ' and ').replace('||', ' or '), {'__builtins__': {}}) is (outcome == 'success')


@pytest.mark.parametrize('retention_outcome', ['failure', 'cancelled', 'skipped'])
def test_successful_local_image_checks_cannot_hide_failed_hosted_retention(retention_outcome):
    plan, results, _ = image_results()
    jobs = {name: {'result': 'success'} for name in ('geospatial-changes', 'lint', 'tests')}
    jobs['tests-control-plane'] = {'result': 'success' if 'tests-control-plane' in plan['suites'] else 'skipped'}
    jobs.update({name: {'result': 'skipped'} for name in ('geospatial-integration', 'browser', 'sdk-validation')})
    jobs['production-images'] = {'result': retention_outcome}
    with pytest.raises(ValueError, match='job production-images'):
        verify_results(plan, results, jobs)
