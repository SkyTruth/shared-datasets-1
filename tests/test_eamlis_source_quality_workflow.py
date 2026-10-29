"""Trusted staging identities and post-publication hold resolution boundaries."""
from pathlib import Path

from workflow_helpers import load_workflow, workflow_triggers

from scripts import deployment_permissions, repo_guardrails
from tests.test_scheduled_ingestion_iam_tf import terraform_resource_block

ROOT = Path(__file__).resolve().parents[1]


def test_preview_downloads_production_then_switches_to_preview_writer():
    workflow = load_workflow(ROOT / ".github/workflows/eamlis-held-candidate-preview.yml")
    assert set(workflow_triggers(workflow)) == {"workflow_dispatch"}
    job = workflow["jobs"]["inspect"]
    assert job["environment"] == "shared-datasets-production"
    assert workflow["concurrency"] == {"group": "feature-branch-preview", "cancel-in-progress": False}
    steps = job["steps"]
    assert "refs/heads/main" in steps[0]["run"] and "GITHUB_WORKFLOW_REF" in steps[0]["run"]
    assert repo_guardrails.has_guarded_main_workflow_checkout(
        workflow, ".github/workflows/eamlis-held-candidate-preview.yml")
    checkout = next(step for step in steps if step.get("uses", "").startswith("actions/checkout@"))
    assert checkout["with"]["ref"] == "${{ github.workflow_sha }}"
    auth = [(n, step["with"]["service_account"]) for n, step in enumerate(steps)
            if step.get("uses", "").startswith("google-github-actions/auth@")]
    assert auth[0][1] == "shared-datasets-publisher@shared-datasets-1.iam.gserviceaccount.com"
    assert auth[1][1] == "feature-preview-loader@shared-datasets-1.iam.gserviceaccount.com"
    download = next(n for n, step in enumerate(steps) if " download-preview " in step.get("run", ""))
    upload = next(n for n, step in enumerate(steps) if " upload-preview " in step.get("run", ""))
    assert auth[0][0] < download < auth[1][0] < upload
    assert job["env"]["PREVIEW_BUCKET"] == "skytruth-shared-datasets-1-preview"
    assert not any(" resolve " in step.get("run", "") for step in steps)


def test_hold_resolution_requires_verified_publication_and_same_approval_envelope():
    workflow = load_workflow(ROOT / ".github/workflows/publish-dataset.yml")
    steps = next(job["steps"] for job in workflow["jobs"].values()
                 if any(s.get("name") == "Resolve reviewed source-quality publication claim" for s in job.get("steps", [])))
    names = [step.get("name") for step in steps]
    resolver = names.index("Resolve reviewed source-quality publication claim")
    assert names.index("Finalize promoted release metadata") < names.index("Rebuild promoted release index") < resolver
    assert resolver < names.index("Record completed publication")
    authority = steps[names.index("Promote approved staged objects")]["env"]
    assert authority == {
        "GH_TOKEN": "${{ github.token }}",
        "EXPECTED_SHA256": "${{ needs.reviewed_pr_plans.outputs.envelope_sha256 }}",
    }
    assert steps[resolver]["env"] == authority
    assert steps[names.index("Rebuild promoted release index")].get("env") == authority
    assert "has_publish_plan == 'true'" in steps[resolver]["if"]


def test_claim_recovery_permissions_are_limited_to_the_eamlis_job():
    text = (ROOT / "terraform/envs/prod/eamlis_monthly.tf").read_text()
    binding = terraform_resource_block(text, "google_project_iam_member", "github_actions_eamlis_quality_iam_manager")
    assert "startsWith" not in binding
    assert "resource.name == 'projects/${var.project_id}/locations/${var.region}/jobs/eamlis-monthly'" in binding
    role = terraform_resource_block(text, "google_project_iam_custom_role", "eamlis_quality_iam_manager")
    assert 'permissions = ["run.jobs.getIamPolicy", "run.jobs.setIamPolicy"]' in role
    reader = terraform_resource_block(text, "google_cloud_run_v2_job_iam_member", "eamlis_quality_execution_reader")
    assert '"roles/run.viewer"' in reader and '"eamlis-monthly"' in reader
    assert "module.eamlis_monthly_job" not in reader
    checks = deployment_permissions.checks("ingestion-iam")
    assert any(url.endswith("/jobs/eamlis-monthly:testIamPermissions") and
               set(permissions) == {"run.jobs.getIamPolicy", "run.jobs.setIamPolicy"} for url, permissions in checks)
