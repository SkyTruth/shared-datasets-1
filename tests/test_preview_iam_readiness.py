"""The main-push preview IAM plan must prove its existing refresh dependencies."""
from __future__ import annotations

import io
import json
from pathlib import Path
import re
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

import yaml

from scripts import deployment_permissions as live

ROOT = Path(__file__).resolve().parents[1]
FAILURE = json.loads((ROOT / "tests/fixtures/ci_failures/preview_iam_pool_read.json").read_text())
POOL_URL = "https://iam.googleapis.com/v1/" + FAILURE["resource"] + ":testIamPermissions"
POOL_READ = FAILURE["permission"]


class PreviewIamReadinessTests(unittest.TestCase):
    def test_grant_adds_only_read_authority_on_the_existing_pool_class(self):
        source = (ROOT / "terraform/envs/prod/preview_terraform_iam.tf").read_text()
        role = source.split('resource "google_project_iam_custom_role" "preview_terraform" {', 1)[1].split('\nmodule "', 1)[0]
        permissions = re.findall(r'"(iam\.[^"]+)"', role)
        self.assertEqual({p for p in permissions if p.startswith("iam.workloadIdentityPools.")}, {POOL_READ})
        # Preserve the managed identity-pool dependency and exact reviewed member.
        self.assertIn('member             = "principal://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/subject/repo:${var.github_repository}:environment:${var.github_publish_environment}"', source)
        self.assertIn('role               = "roles/iam.workloadIdentityUser"', source)

    def test_read_probe_uses_actual_pool_and_all_observed_refresh_dependencies(self):
        required = dict(live.checks("preview-service-account-iam"))
        self.assertEqual(required[POOL_URL], (POOL_READ,))
        project_url = f"https://cloudresourcemanager.googleapis.com/v1/projects/{live.PROJECT}:testIamPermissions"
        self.assertEqual(set(required[project_url]), {
            "iam.serviceAccounts.create", "iam.serviceAccounts.get",
            "iam.serviceAccounts.getIamPolicy", "iam.serviceAccounts.setIamPolicy",
            "iam.roles.get", "serviceusage.services.get",
        })
        self.assertEqual(len(required), 2)

    def test_pool_probe_cannot_pass_on_project_permission_hints_alone(self):
        calls = []

        def project_only(url, required):
            calls.append((url, required))
            return [POOL_READ] if url == POOL_URL else []

        with self.assertRaisesRegex(RuntimeError, "iam.workloadIdentityPools.get"):
            live.verify("preview-service-account-iam", project_only, attempts=1)
        self.assertIn((POOL_URL, (POOL_READ,)), calls)

    def test_complete_live_read_contract_passes_without_mutation(self):
        probe = Mock(return_value=[])
        live.verify("preview-service-account-iam", probe, attempts=1)
        self.assertEqual(probe.call_args_list, [unittest.mock.call(url, permissions) for url, permissions in live.checks("preview-service-account-iam")])
        self.assertTrue(all(call.args[0].endswith(":testIamPermissions") for call in probe.call_args_list))

    def test_missing_other_refresh_reads_fail_before_reporting_ready(self):
        for permission in ("iam.roles.get", "serviceusage.services.get"):
            with self.subTest(permission=permission), self.assertRaisesRegex(RuntimeError, re.escape(permission)):
                live.verify("preview-service-account-iam", lambda url, required: [permission] if permission in required else [], attempts=1)

    def test_exact_supported_pool_permission_request_has_no_mutation_body(self):
        with patch.object(live.urllib.request, "urlopen", return_value=io.BytesIO(json.dumps({"permissions": [POOL_READ]}).encode())) as open_url:
            self.assertEqual(live.request(POOL_URL, (POOL_READ,), "fixture-token"), [])
        request = open_url.call_args.args[0]
        self.assertEqual(request.full_url, POOL_URL)
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(json.loads(request.data), {"permissions": [POOL_READ]})
        self.assertEqual(open_url.call_args.kwargs, {"timeout": 30})

    def test_denied_permission_response_and_recorded_403_remain_failures(self):
        with patch.object(live.urllib.request, "urlopen", return_value=io.BytesIO(b'{"permissions":[]}')):
            self.assertEqual(live.request(POOL_URL, (POOL_READ,), "fixture-token"), [POOL_READ])
        denied = HTTPError(POOL_URL, FAILURE["http_status"], FAILURE["reason"], None, None)

        def deny_only_pool(request, **kwargs):
            if request.full_url == POOL_URL:
                raise denied
            return io.BytesIO(json.dumps({"permissions": json.loads(request.data)["permissions"]}).encode())

        with patch.object(live.urllib.request, "urlopen", side_effect=deny_only_pool), self.assertRaises(HTTPError):
            live.verify("preview-service-account-iam", lambda url, required: live.request(url, required, "fixture-token"), attempts=1)

    def test_cli_never_prints_ready_when_exact_pool_read_is_denied(self):
        request = Mock(side_effect=lambda url, permissions, token: [POOL_READ] if url == POOL_URL else [])
        verify = live.verify_checks
        with patch("sys.argv", ["deployment_permissions.py", "--target", "preview-service-account-iam"]), patch.object(live.subprocess, "check_output", return_value="fixture-token"), patch.object(live, "request", request), patch.object(live, "verify_checks", side_effect=lambda required, probe: verify(required, probe, attempts=1)), patch("builtins.print") as output:
            with self.assertRaisesRegex(RuntimeError, "iam.workloadIdentityPools.get"):
                live.main()
        output.assert_not_called()

    def test_preview_workflow_requires_probe_before_plan_claim_and_apply(self):
        wrapper = yaml.safe_load((ROOT / ".github/workflows/preview-terraform-iam-sync.yml").read_text())
        self.assertEqual(wrapper["jobs"]["sync"]["with"]["readiness_target"], "preview-service-account-iam")
        self.assertEqual(wrapper["jobs"]["sync"]["needs"], "bootstrap")
        leaf = yaml.safe_load((ROOT / ".github/workflows/prod-terraform-target-apply.yml").read_text())
        steps = leaf["jobs"]["sync"]["steps"]
        by_name = {step["name"]: index for index, step in enumerate(steps)}
        probe = by_name["Verify prerequisite deployment permissions"]
        self.assertEqual(steps[probe]["env"]["READINESS_TARGET"], "${{ inputs.readiness_target }}")
        self.assertIn('deployment_permissions.py --target "$READINESS_TARGET"', steps[probe]["run"])
        for step in ("Terraform plan", "Claim tested deployment revision", "Terraform apply"):
            self.assertLess(probe, by_name[step])


if __name__ == "__main__":
    unittest.main()
