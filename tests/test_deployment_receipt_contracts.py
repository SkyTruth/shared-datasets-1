"""Negative workflow controls for exact receipt links and narrow write tokens."""
from __future__ import annotations

import copy
from pathlib import Path
import tempfile
import unittest

import yaml
from scripts import deployment_receipt_contracts as contracts


def leaf(name):
    return {"on": {"workflow_call": {}}, "permissions": {"contents": "read"}, "jobs": {"deploy": {
        "environment": contracts.PROTECTED, "permissions": {"deployments": "write", "attestations": "write"},
        "steps": [
            {"uses": contracts.ACTION, "with": {"mode": "rehearsal", "signer-workflow": name}},
            {"name": "Stage tested image", "run": "docker push immutable-image"},
            {"name": "Claim", "id": "claim", "run": "python scripts/deployment_revision.py start --target example"},
            {"id": "claim-receipt", "uses": contracts.ACTION, "with": {"mode": "receipt", "receipt-path": "${{ steps.claim.outputs.receipt_path }}"}},
            {"name": "Apply", "run": "bash scripts/terraform_retry.sh apply -input=false exact.tfplan"},
            {"name": "Record outcome", "id": "outcome", "if": "always()", "run": "python scripts/deployment_revision.py finish --phase unknown"},
            {"uses": contracts.ACTION, "if": "always() && steps.outcome.outputs.receipt_path != ''", "with": {"mode": "receipt", "receipt-path": "${{ steps.outcome.outputs.receipt_path }}"}},
        ],
    }}}


class ReceiptContractTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.workflows = self.root / ".github/workflows"
        self.workflows.mkdir(parents=True)
        for name in contracts.WORKFLOWS:
            self.write(name, leaf(name))

    def write(self, name, value):
        (self.workflows / name).write_text(yaml.safe_dump(value))

    def test_all_registered_leaves_have_valid_linked_claims_and_outcomes(self):
        self.assertEqual(contracts.boundaries(self.root), [])
        self.assertEqual(contracts.permissions(self.root), [])

    def test_removed_disabled_late_or_wrong_signer_rehearsals_fail(self):
        name = "wdpa-monthly-deploy.yml"
        for change in ("removed", "disabled", "late", "wrong-signer"):
            with self.subTest(change=change):
                value = leaf(name)
                steps = value["jobs"]["deploy"]["steps"]
                first = steps.pop(0)
                if change == "disabled":
                    first["if"] = False
                    steps.insert(0, first)
                elif change == "late":
                    steps.append(first)
                elif change == "wrong-signer":
                    first["with"]["signer-workflow"] = "unknown.yml"
                    steps.insert(0, first)
                self.write(name, value)
                self.assertTrue(contracts.boundaries(self.root))

    def test_wrong_output_late_unsigned_or_always_apply_cannot_mutate(self):
        name = "prod-terraform-target-apply.yml"
        for change in ("wrong-output", "removed", "disabled", "late", "always"):
            with self.subTest(change=change):
                value = leaf(name)
                steps = value["jobs"]["deploy"]["steps"]
                signer = steps[3]
                if change == "wrong-output":
                    signer["with"]["receipt-path"] = "${{ steps.other.outputs.receipt_path }}"
                elif change == "disabled":
                    signer["if"] = False
                elif change == "always":
                    steps[4]["if"] = "always()"
                else:
                    steps.remove(signer)
                    if change == "late":
                        steps.append(signer)
                self.write(name, value)
                self.assertTrue(contracts.boundaries(self.root))
        value = leaf(name)
        value["jobs"]["deploy"]["steps"][4]["if"] = "always() && steps.claim-receipt.outcome == 'success'"
        self.write(name, value)
        self.assertEqual(contracts.boundaries(self.root), [])

    def test_unknown_outcome_requires_always_signing_of_its_own_file(self):
        name = "deployment-verification.yml"
        value = leaf(name)
        value["jobs"]["deploy"]["steps"][-1]["if"] = "success()"
        self.write(name, value)
        self.assertTrue(contracts.boundaries(self.root))

    def test_pr_validation_cannot_receive_record_or_signing_write_tokens(self):
        value = {"on": {"pull_request": {}, "push": {}}, "permissions": {"contents": "read"}, "jobs": {"validation": {"permissions": {"deployments": "write"}, "steps": []}}}
        self.write("ci.yml", value)
        self.assertTrue(contracts.permissions(self.root))
        value["jobs"]["validation"]["permissions"] = {"contents": "read", "attestations": "read"}
        self.write("ci.yml", value)
        self.assertEqual(contracts.permissions(self.root), [])
        caller = copy.deepcopy(value)
        caller["jobs"] = {"production": {"if": "github.event_name == 'push' && github.ref == 'refs/heads/main'", "uses": "./.github/workflows/wdpa-monthly-deploy.yml", "permissions": {"deployments": "write"}}}
        self.write("ci.yml", caller)
        self.assertEqual(contracts.permissions(self.root), [])

    def test_unprotected_discovery_and_global_writer_defaults_fail(self):
        value = leaf("deployment-verification.yml")
        value["permissions"] = {"deployments": "write"}
        value["jobs"]["discover"] = {"steps": []}
        self.write("deployment-verification.yml", value)
        self.assertTrue(contracts.permissions(self.root))

    def test_sdk_existing_oidc_boundary_requires_main_and_trusted_bootstrap(self):
        job = {"steps": [
            {"run": 'if [[ "$GITHUB_REF" != refs/heads/main ]]; then exit 1; fi\ncase "$GITHUB_WORKFLOW_REF" in publish-typescript-sdk.yml@refs/heads/main) ;; *) exit 1 ;; esac'},
            {"uses": "actions/checkout@v4", "with": {"ref": "${{ github.workflow_sha }}"}},
            {"run": "python scripts/deployment_revision.py verify --bootstrap --workflow publish-typescript-sdk.yml"},
        ]}
        self.assertTrue(contracts.sdk_boundary("publish-typescript-sdk.yml", "publish", job))
        for index in range(3):
            changed = copy.deepcopy(job)
            changed["steps"].pop(index)
            with self.subTest(index=index):
                self.assertFalse(contracts.sdk_boundary("publish-typescript-sdk.yml", "publish", changed))
