from __future__ import annotations

from pathlib import Path

from workflow_helpers import (
    load_workflow,
    python_literal_string_set,
    terraform_targets,
    workflow_steps_by_name,
    workflow_triggers,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / ".github/workflows/metadata-index-loader-iam-sync.yml"


def test_metadata_index_loader_iam_sync_is_protected_and_targeted():
    workflow = load_workflow(WORKFLOW)
    trigger = workflow_triggers(workflow)
    job = workflow["jobs"]["sync"]
    steps = workflow_steps_by_name(workflow, "sync")
    plan_run = steps["Terraform plan"]["run"]
    enforce_run = steps["Enforce metadata index-loader resource-change allowlist"]["run"]

    assert workflow["name"] == "Feature metadata index-loader IAM sync"
    assert trigger["push"]["branches"] == ["main"]
    assert job["environment"] == "shared-datasets-production"
    assert job["concurrency"] == {"group": "prod-terraform-state", "cancel-in-progress": False}
    assert steps["Check out repository"]["with"]["ref"] == "main"
    assert "GCP_TERRAFORM_WORKLOAD_IDENTITY_PROVIDER" in workflow["env"]["TERRAFORM_WORKLOAD_IDENTITY_PROVIDER"]
    assert "GCP_TERRAFORM_SERVICE_ACCOUNT" in workflow["env"]["TERRAFORM_SERVICE_ACCOUNT"]

    expected_targets = {
        "google_firestore_database.feature_metadata",
        "google_project_iam_member.metadata_index_loader_firestore_user",
        "google_service_account_iam_member.metadata_index_loader_github_wif",
        "google_storage_bucket_iam_member.metadata_index_loader_index_load_creator",
        "google_storage_bucket_iam_member.metadata_index_loader_object_viewer",
        "module.metadata_index_loader_service_account.google_service_account.this",
    }
    assert terraform_targets(plan_run) == expected_targets
    assert "-refresh=false" in plan_run
    assert "unused-by-metadata-index-loader-iam-sync" in plan_run
    assert "terraform -chdir=terraform/envs/prod apply" in steps["Terraform apply"]["run"]

    assert python_literal_string_set(enforce_run, "allowed_exact") >= {
        "google_firestore_database.feature_metadata",
        "google_project_iam_member.metadata_index_loader_firestore_user",
        "google_service_account_iam_member.metadata_index_loader_github_wif",
        "google_storage_bucket_iam_member.metadata_index_loader_index_load_creator",
        "google_storage_bucket_iam_member.metadata_index_loader_object_viewer",
    }
    assert "module\\.metadata_index_loader_service_account" in enforce_run
    assert "metadata_service" not in "\n".join(sorted(expected_targets))
