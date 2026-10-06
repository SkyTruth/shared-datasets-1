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

    def notification(self):
        source = Path(__file__).resolve().parents[1] / ".github/workflows" / contracts.NOTIFICATION_WORKFLOW
        return yaml.safe_load(source.read_text())

    def test_exact_main_notification_writer_preserves_production_boundaries(self):
        value = self.notification()
        self.assertTrue(contracts.notification_boundary(contracts.NOTIFICATION_WORKFLOW, "incidents", value, value["jobs"]["incidents"]))
        self.write(contracts.NOTIFICATION_WORKFLOW, value)
        self.assertEqual(contracts.permissions(self.root), [])
        self.assertEqual(contracts.boundaries(self.root), [])
        self.assertFalse(contracts.notification_boundary("other.yml", "incidents", value, value["jobs"]["incidents"]))
        self.assertFalse(contracts.notification_boundary(contracts.NOTIFICATION_WORKFLOW, "other", value, value["jobs"]["incidents"]))

    def test_notification_cannot_broaden_triggers_tokens_ref_or_authorization(self):
        mutations = {
            "PR": lambda value, job: value[True].update(pull_request={}),
            "PR target": lambda value, job: value[True].update(pull_request_target={}),
            "reusable": lambda value, job: value[True].update(workflow_call={}),
            "nonmain upstream": lambda value, job: value[True]["workflow_run"].update(branches=["feature"]),
            "unfinished upstream": lambda value, job: value[True]["workflow_run"].update(types=["requested"]),
            "global writer": lambda value, job: value.update(permissions={"deployments": "write"}),
            "extra writer": lambda value, job: job["permissions"].update(contents="write"),
            "all writer": lambda value, job: job.update(permissions="write-all"),
            "missing main": lambda value, job: job.update({"if": job["if"].replace("github.ref == 'refs/heads/main'", "true")}),
            "OR bypass": lambda value, job: job.update({"if": job["if"] + " || true"}),
            "removed guard": lambda value, job: job["steps"].pop(0),
            "disabled guard": lambda value, job: job["steps"][0].update({"if": False}),
            "weak guard": lambda value, job: job["steps"][0].update(run=job["steps"][0]["run"].replace("exit 1", "exit 0")),
            "production env": lambda value, job: job.update(environment=contracts.PROTECTED),
            "cloud env": lambda value, job: value.update(env={"GOOGLE_APPLICATION_CREDENTIALS": "credential.json"}),
            "workflow shell override": lambda value, job: value.update(defaults={"run": {"shell": "python"}}),
            "job shell override": lambda value, job: job.update(defaults={"run": {"shell": "python"}}),
            "other runner": lambda value, job: job.update({"runs-on": "self-hosted"}),
            "cancellation": lambda value, job: job["concurrency"].update({"cancel-in-progress": True}),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                value = self.notification()
                job = value["jobs"]["incidents"]
                mutate(value, job)
                self.assertFalse(contracts.notification_boundary(contracts.NOTIFICATION_WORKFLOW, "incidents", value, job))
                # A protected environment must not disguise a PR-capable writer.
                if label != "production env":
                    self.write(contracts.NOTIFICATION_WORKFLOW, value)
                    self.assertTrue(contracts.permissions(self.root))

    def test_notification_requires_trusted_checkout_commands_and_signed_delivery(self):
        mutations = {
            "upstream checkout": lambda steps: steps[1]["with"].update(ref="${{ github.event.workflow_run.head_sha }}"),
            "shallow checkout": lambda steps: steps[1]["with"].update({"fetch-depth": 1}),
            "persisted token": lambda steps: steps[1]["with"].update({"persist-credentials": True}),
            "second checkout": lambda steps: steps.append(copy.deepcopy(steps[1])),
            "shadowed checkout ID": lambda steps: steps[1].update(id="prepare"),
            "shadowed setup ID": lambda steps: steps[2].update(id="deliver"),
            "untrusted artifact": lambda steps: steps.append({"uses": "actions/download-artifact@v4"}),
            "GCP auth": lambda steps: steps.append({"uses": "google-github-actions/auth@v3"}),
            "production command": lambda steps: steps.append({"run": "terraform apply saved.tfplan"}),
            "changed command": lambda steps: steps[6].update(run=steps[6]["run"] + "\npython arbitrary.py"),
            "disabled prepare": lambda steps: steps[6].update({"if": "false && true"}),
            "GCP credential": lambda steps: steps[6]["env"].update(GOOGLE_APPLICATION_CREDENTIALS="credential.json"),
            "wrong signer": lambda steps: steps[7].update(uses="actions/attest@unreviewed"),
            "broad subjects": lambda steps: steps[7]["with"].update({"subject-path": "${{ runner.temp }}/*.json"}),
            "storage write": lambda steps: steps[7]["with"].update({"create-storage-record": True}),
            "unsigned send": lambda steps: steps[8].update({"if": "always()"}),
            "late claim signature": lambda steps: steps.insert(9, steps.pop(7)),
            "lost failed outcome": lambda steps: steps[9].update({"if": "success()"}),
            "missing verification": lambda steps: steps.pop(),
            "unpinned Python": lambda steps: steps[2]["with"].update({"python-version": "3.12"}),
            "unpinned uv": lambda steps: steps[3]["with"].update(version="latest"),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                value = self.notification()
                job = value["jobs"]["incidents"]
                mutate(job["steps"])
                self.assertFalse(contracts.notification_boundary(contracts.NOTIFICATION_WORKFLOW, "incidents", value, job))
                self.write(contracts.NOTIFICATION_WORKFLOW, value)
                self.assertTrue(contracts.permissions(self.root))

    def test_notification_exception_cannot_authorize_another_writer_job(self):
        value = self.notification()
        value["jobs"]["other"] = copy.deepcopy(value["jobs"]["incidents"])
        self.write(contracts.NOTIFICATION_WORKFLOW, value)
        self.assertTrue(contracts.permissions(self.root))
        value = self.notification()
        value[True]["pull_request"] = {}
        value["jobs"]["incidents"]["environment"] = contracts.PROTECTED
        self.write(contracts.NOTIFICATION_WORKFLOW, value)
        self.assertTrue(contracts.permissions(self.root))
