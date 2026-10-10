"""Replay the six-resource saved-plan boundary that failed on main."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import re

import pytest

from scripts import deployment_permissions as live
from scripts.ci_contract import select_deployments
from scripts.deployment_revision import TERRAFORM_SYNCS
from scripts.terraform_plan_allowlist import blocked_changes
from scripts.terraform_plan_permissions import plan_checks
from scripts.terraform_target_contracts import callers, declared
from test_scheduled_ingestion_iam_tf import terraform_resource_block

ROOT = Path(__file__).resolve().parents[1]
MISSING = {"iam.roles.delete", "iam.serviceAccounts.delete", "run.jobs.delete"}


def saved_plan():
    return json.loads((ROOT / "tests/fixtures/terraform_targets/wdpa-retirement.json").read_text())


def grant():
    text = (ROOT / "terraform/envs/prod/wdpa_validation_retirement_iam.tf").read_text()
    role = terraform_resource_block(text, "google_project_iam_custom_role", "wdpa_validation_retirement")
    permissions = re.search(r"permissions\s*=\s*\[(.*?)\]", role, re.S)[1]
    return set(re.findall(r'"([^"]+)"', permissions))


@pytest.mark.parametrize("missing", [MISSING, *({permission} for permission in sorted(MISSING))])
def test_saved_plan_reproduces_missing_live_delete_authority(missing):
    required = plan_checks(saved_plan(), project_number="123456789")
    with pytest.raises(RuntimeError, match="deployment identity is not ready"):
        live.verify_checks(required, lambda _url, permissions: sorted(set(permissions) & missing), attempts=1)
    # Readiness hints and permissions merely declared in Terraform cannot pass.
    assert not MISSING & {p for _, permissions in live.checks("bucket-iam") for p in permissions}
    assert grant() == MISSING
    live.verify_checks(required, lambda _url, permissions: sorted((set(permissions) & missing) - grant()), attempts=1)


def test_retirement_and_bootstrap_have_disjoint_scopes_and_verified_order():
    jobs = callers(ROOT)
    retirement = jobs["scratch-cleanup-iam-sync.yml/retire-wdpa-validation"]
    bootstrap = jobs["scratch-cleanup-iam-sync.yml/retirement-bootstrap"]
    assert retirement["needs"] == "retirement-bootstrap"
    scope = declared(retirement["with"]["allowed_exact"])
    assert scope == {row["address"] for row in saved_plan()["resource_changes"]}
    assert len(scope) == 6
    assert not blocked_changes(saved_plan(), allowed_exact=scope, allowed_patterns=[], block_deletes=False)
    bootstrap_scope = declared(bootstrap["with"]["targets"])
    assert bootstrap_scope == declared(bootstrap["with"]["allowed_exact"])
    assert bootstrap_scope == {
        "google_project_iam_custom_role.wdpa_validation_retirement",
        "google_project_iam_member.github_actions_wdpa_validation_retirement",
    }
    assert scope.isdisjoint(bootstrap_scope)
    assert bootstrap["with"]["block_deletes"] is True
    assert bootstrap["with"]["readiness_target"] == "iam-bootstrap"
    assert bootstrap["with"]["post_apply_wait_seconds"] == 30
    assert TERRAFORM_SYNCS[bootstrap["with"]["sync_name"]] == "scratch-cleanup-iam-sync.yml"
    assert blocked_changes(saved_plan(), allowed_exact=bootstrap_scope, allowed_patterns=[], block_deletes=True)
    outside = copy.deepcopy(saved_plan())
    outside["resource_changes"][-1]["address"] = "module.wdpa_job_service_account.google_service_account.this"
    assert blocked_changes(outside, allowed_exact=scope, allowed_patterns=[], block_deletes=False)


def test_temporary_authority_is_separate_expires_and_selects_its_actual_owner():
    text = (ROOT / "terraform/envs/prod/wdpa_validation_retirement_iam.tf").read_text()
    binding = terraform_resource_block(text, "google_project_iam_member", "github_actions_wdpa_validation_retirement")
    assert 'role    = google_project_iam_custom_role.wdpa_validation_retirement.name' in binding
    assert 'member  = "serviceAccount:${var.github_actions_terraform_service_account_email}"' in binding
    assert "request.time < timestamp('2026-10-11T12:00:00Z')" in binding
    assert "resource.name" not in binding  # Unsupported for these IAM resources.
    persistent = (ROOT / "terraform/envs/prod/scheduled_ingestion_deploy_iam.tf").read_text()
    assert not MISSING & set(re.findall(r'"([^"]+)"', persistent))
    assert select_deployments(["terraform/envs/prod/wdpa_validation_retirement_iam.tf"]) == ["scratch_cleanup_iam"]
