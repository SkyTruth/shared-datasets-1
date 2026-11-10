"""Exercise identity boundaries using workflow commands and real API transport."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

from scripts.release_contracts import role_permissions

from identity_policy_helpers import cel
from terraform_helpers import evaluate, load_terraform, resources
from workflow_helpers import load_workflow

ROOT = Path(__file__).resolve().parents[1]
PROD = ROOT / "terraform/envs/prod"
REPO = "SkyTruth/shared-datasets-1"
SHA = "a" * 40


def resource(filename, kind, name):
    return next(body for type_, name_, body in resources(load_terraform(PROD / filename))
                if (type_, name_) == (kind, name))


def storage_condition(expression):
    locals_ = load_terraform(PROD / "canonical_mutation_iam.tf")["locals"][0]
    values = {key: locals_[key] for key in ("shared_bucket_object_resource_prefix", "shared_bucket_folder_resource_prefix",
              "canonical_mutation_publisher_condition", "pending_publish_source_condition", "pending_publish_cleanup_condition")}
    values["canonical_dataset_top_level_prefixes"] = ["100-test/"]
    values["condition"] = expression
    return evaluate(values, "local.condition", variables={"bucket_name": "fixture"}).replace("resource.name", "assertion.name")


def test_production_authenticated_jobs_never_check_out_selected_source():
    for path in (ROOT / ".github/workflows").glob("*.yml"):
        workflow = load_workflow(path)
        for job in workflow["jobs"].values():
            steps = job.get("steps", [])
            production = any(step.get("uses") == "./.github/actions/deployment-auth"
                             and not step.get("with", {}).get("service-account")
                             or step.get("uses") == "./.github/actions/deployment-bootstrap/terraform"
                             or step.get("with", {}).get("service_account") == "${{ env.TERRAFORM_SERVICE_ACCOUNT }}"
                             for step in steps)
            if production:
                for step in steps:
                    if step.get("uses") == "actions/checkout@v4":
                        ref = step.get("with", {}).get("ref", "")
                        assert ref == "${{ github.workflow_sha }}", (path.name, ref)
                        assert "preview-source" not in step.get("with", {}).get("path", "")


@pytest.mark.parametrize("filename", ["feature-preview-deploy.yml", "feature-preview-destroy.yml"])
def test_preview_owner_acceptance_precedes_authentication(filename):
    workflow = load_workflow(ROOT / ".github/workflows" / filename)
    assert workflow["permissions"]["pull-requests"] == "read"
    for job in workflow["jobs"].values():
        steps = job["steps"]
        check = next(i for i, step in enumerate(steps) if step.get("name") == "Verify owner acceptance")
        assert steps[check]["run"] == "python -I -S scripts/deployment_revision.py accept"
        assert steps[check]["env"]["EXECUTOR_SHA"] == "${{ github.workflow_sha }}"
        assert check < next(i for i, step in enumerate(steps) if "auth" in step.get("uses", ""))


@pytest.mark.parametrize("selected,accepted", [(SHA, True), ("b" * 40, False), ("", False)])
def test_preview_resolves_source_before_dependencies_or_credentials(tmp_path, selected, accepted):
    steps = load_workflow(ROOT / ".github/workflows/feature-preview-deploy.yml")["jobs"]["preview"]["steps"]
    guard_index = next(i for i, step in enumerate(steps) if step.get("name") == "Validate selected ref")
    guard = steps[guard_index]["run"]
    assert "FETCH_HEAD" in guard and "GITHUB_SHA" in guard
    assert guard_index < next(i for i, step in enumerate(steps) if "uv sync" in step.get("run", ""))
    assert guard_index < next(i for i, step in enumerate(steps) if step.get("uses") == "./.github/actions/deployment-auth")
    git = tmp_path / "git"
    git.write_text("#!/bin/sh\ncase \"$1\" in\ncheck-ref-format|fetch) exit 0;;\nrev-parse) printf '%s\\n' \"$SELECTED_SHA\";;\n*) exit 91;;\nesac\n")
    git.chmod(0o755)
    result = subprocess.run(["bash", "-euo", "pipefail", "-c", guard], capture_output=True, text=True,
                            env={**os.environ, "PATH": str(tmp_path) + ":" + os.environ["PATH"],
                                 "GITHUB_SHA": SHA, "PREVIEW_REF": "refs/heads/fixture", "PREVIEW_DATA_MODE": "preserve",
                                 "SELECTED_SHA": selected})
    assert (result.returncode == 0) is accepted, result.stdout + result.stderr


def test_additive_member_waits_for_valid_provider_mapping():
    binding = resource("github_job_identities.tf", "google_service_account_iam_member", "github_actions_terraform_wif")
    assert "${google_iam_workload_identity_pool_provider.github}" in binding["depends_on"]
    source = (PROD / "github_job_identities.tf").read_text()
    assert "has(assertion." not in source
    assert "'job_workflow_ref' in assertion" in source


@pytest.mark.parametrize("name,allowed", [
    ("_catalog/web/catalog.json", True), ("100-test/asset/releases/2026-11-05/data.fgb", True),
    ("README.md", True), ("_scratch/pending-publishes/asset/proposal/data.fgb", True),
    ("000-system/terraform/state/prod/default.tfstate", False), ("000-system/other", False),
    ("_scratch/translation-debt/private.json", False),
])
def test_new_scoped_advisory_grant_excludes_state(name, allowed):
    grants = [resource("github_readonly_iam.tf", "google_storage_bucket_iam_member", name)
              for name in ("github_readonly_canonical_object_viewer", "github_readonly_pending_publish_viewer")]
    assert any(cel(storage_condition(grant["condition"][0]["expression"]),
                   {"name": "projects/_/buckets/fixture/objects/" + name}) for grant in grants) is allowed


def test_cleanup_has_no_canonical_publisher_authority():
    workflow = load_workflow(ROOT / ".github/workflows/scratch-cleanup-audit.yml")
    auth = next(step for step in workflow["jobs"]["cleanup"]["steps"] if step.get("uses") == "google-github-actions/auth@v2")
    assert auth["with"]["service_account"] == "shared-datasets-cleanup@shared-datasets-1.iam.gserviceaccount.com"
    registry = json.loads((PROD / "github-workflow-identities.json").read_text())
    assert registry["cleanup"] == ["scratch-cleanup-audit.yml"]
    assert "scratch-cleanup-audit.yml" not in registry["publisher"]
    grant = resource("canonical_mutation_iam.tf", "google_storage_bucket_iam_member", "shared_datasets_publisher_pending_publish_cleanup_user")
    # Keep the publisher's existing staging/composite-upload permissions intact.
    assert "module.shared_datasets_publisher_service_account.member" in grant["member"]
    assert grant["condition"][0]["expression"] == "${local.pending_publish_cleanup_condition}"
    values = load_terraform(PROD / "github_job_identities.tf")["locals"][0]
    values["github_workflow_identities"] = registry
    permissions = evaluate(values, "local.github_job_permissions", variables={"github_repository": REPO})
    assert set(permissions["cleanup"]) == {"storage.objects.list", "storage.objects.get", "storage.objects.create", "storage.objects.delete"}
    binding = resource("github_job_identities.tf", "google_project_iam_member", "github_job")
    condition = binding["dynamic"][0]["condition"]
    assert 'each.key == "cleanup"' in condition["for_each"]
    assert condition["content"][0]["expression"] == "resource.name == 'projects/_/buckets/${var.bucket_name}' || (${local.pending_publish_cleanup_condition})"


@pytest.mark.parametrize("name,allowed", [
    ("_scratch/pending-publishes/asset/proposal/data.fgb", True), ("_scratch/cleanup-audit/pending-publishes/warning.json", True),
    ("gcloud/tmp/parallel_composite_uploads/see_gcloud_storage_cp_help_for_details/part", True),
    ("100-test/asset/latest/data.fgb", False), ("_catalog/web/catalog.json", False),
    ("000-system/terraform/state/prod/default.tfstate", False),
])
def test_cleanup_condition_rejects_canonical_and_state_objects(name, allowed):
    binding = resource("github_job_identities.tf", "google_project_iam_member", "github_job")
    expression = binding["dynamic"][0]["condition"]["content"][0]["expression"]
    assert cel(storage_condition(expression), {"name": "projects/_/buckets/fixture/objects/" + name}) is allowed


@pytest.mark.parametrize("case,accepted", [
    ("approved", True), ("self", True), ("missing", False), ("unmerged", False),
    ("wrong-merge", False), ("stale", False), ("dismissed", False), ("changes", False),
    ("foreign", False), ("ambiguous", False), ("api-failure", False),
])
def test_hosted_approval_gate_uses_clean_stdlib_and_exact_merged_pr(tmp_path, case, accepted):
    repo = {"id": 123, "full_name": REPO, "default_branch": "main"}
    pr = {"number": 27, "state": "closed", "merged": True, "merged_at": "2026-11-05T00:00:00Z",
          "merge_commit_sha": SHA, "draft": False, "user": {"login": "jonaraphael" if case == "self" else "contributor"},
          "head": {"sha": "b" * 40, "repo": repo}, "base": {"ref": "main", "repo": repo}}
    review = {"id": 1, "pull_request_url": f"https://api.github.com/repos/{REPO}/pulls/27",
              "state": {"dismissed": "DISMISSED", "changes": "CHANGES_REQUESTED"}.get(case, "APPROVED"),
              "user": {"login": "jonaraphael"}, "commit_id": "c" * 40 if case == "stale" else "b" * 40,
              "submitted_at": "2026-11-04T00:00:00Z"}
    if case == "unmerged":
        pr["merged"] = False
    if case == "wrong-merge":
        pr["merge_commit_sha"] = "d" * 40
    if case == "foreign":
        pr["head"] = {**pr["head"], "repo": {**repo, "id": 124, "full_name": "foreign/repo"}}
    associated = [] if case == "missing" else [pr] * (2 if case == "ambiguous" else 1)
    fixture = {f"repos/{REPO}": repo, f"repos/{REPO}/commits/{SHA}/pulls?per_page=100": [associated],
               f"repos/{REPO}/pulls/27": pr, f"repos/{REPO}/pulls/27/reviews?per_page=100": [[review]]}
    gh = tmp_path / "gh"
    gh.write_text(f"#!{sys.executable}\nimport json,sys\nfixture={fixture!r}\n"
                  + ("sys.exit(1)\n" if case == "api-failure" else "print(json.dumps(fixture[sys.argv[-1]]))\n"))
    gh.chmod(0o755)
    action = load_workflow(ROOT / ".github/actions/deployment-bootstrap/action.yml")
    steps = action["runs"]["steps"]
    index = next(i for i, step in enumerate(steps) if step.get("name") == "Verify owner acceptance before candidate checkout")
    assert index < next(i for i, step in enumerate(steps) if step.get("uses") == "actions/checkout@v4")
    # No site packages, dev venv, user imports or PYTHONPATH: exactly the gate's declared stdlib environment.
    command = steps[index]["run"].split()
    assert command[:3] == ["python", "-I", "-S"]
    result = subprocess.run([sys.executable, *command[1:]], cwd=ROOT, capture_output=True, text=True,
                            env={**os.environ, "PATH": str(tmp_path) + ":" + os.environ["PATH"],
                                 "GITHUB_REPOSITORY": REPO, "EXECUTOR_SHA": SHA})
    assert (result.returncode == 0) is accepted, result.stdout + result.stderr
    if accepted:
        receipt = json.loads(result.stdout)
        assert receipt["executor_sha"] == SHA and receipt["pr_number"] == 27
        assert receipt["acceptance"]["kind"] == ("self_authored_merge" if case == "self" else "review")


def test_all_bootstrap_callers_preserve_pull_request_read_permission():
    workflows = {path.name: yaml.safe_load(path.read_text())
                 for path in (ROOT / '.github/workflows').glob('*.yml')}
    owners = {name for name, workflow in workflows.items()
              if any(step.get('uses', '').startswith('./.github/actions/deployment-bootstrap')
                     for job in workflow['jobs'].values() for step in job.get('steps', []))}
    def calls(job):
        return job.get('uses', '').removeprefix('./.github/workflows/') in owners
    while True:
        parents = {name for name, workflow in workflows.items() if any(calls(job) for job in workflow['jobs'].values())}
        if parents <= owners:
            break
        owners |= parents
    for name in owners:
        workflow = workflows[name]
        for identifier, job in workflow['jobs'].items():
            if calls(job) or any(step.get('uses', '').startswith('./.github/actions/deployment-bootstrap')
                                 for step in job.get('steps', [])):
                assert job.get('permissions', workflow.get('permissions', {})).get('pull-requests') in {'read', 'write'}, (name, identifier)


def test_role_permission_contract_uses_hcl_declarations_not_comment_decoys(tmp_path):
    path = tmp_path / 'terraform/envs/prod/roles.tf'
    path.parent.mkdir(parents=True)
    path.write_text('''# resource "google_project_iam_custom_role" "reader" { permissions = ["unsafe.write"] }
  resource "google_project_iam_custom_role" "reader" {
    permissions = ["storage.objects.get", "storage.objects.list"]
  }
''')
    assert role_permissions(tmp_path, 'roles.tf', 'reader') == {'storage.objects.get', 'storage.objects.list'}
    assert role_permissions(tmp_path, 'roles.tf', 'missing') == set()
