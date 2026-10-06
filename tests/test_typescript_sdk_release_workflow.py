import unittest
from pathlib import Path

from workflow_helpers import load_workflow, workflow_steps_by_name, workflow_triggers


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = REPO_ROOT / ".github/workflows/publish-typescript-sdk.yml"
VALIDATION_PATH = REPO_ROOT / ".github/workflows/ci.yml"


class TypeScriptSdkReleaseWorkflowTest(unittest.TestCase):
    def test_release_is_main_only_read_only_and_still_uses_oidc(self):
        workflow = load_workflow(WORKFLOW_PATH)
        trigger = workflow_triggers(workflow)
        self.assertIn("workflow_dispatch", trigger)
        self.assertEqual(trigger["push"]["branches"], ["main"])
        self.assertEqual(set(trigger["push"]["paths"]), {
            "api/typescript/src/**", "api/typescript/README.md", "api/typescript/package.json",
            "api/typescript/package-lock.json", "api/typescript/tsconfig.json",
            ".github/workflows/publish-typescript-sdk.yml",
            "api/typescript/scripts/copy-snapshot-contract.mjs", "web/catalog/workspace-contract.js",
        })
        self.assertEqual(workflow["permissions"], {"contents": "read", "id-token": "write"})
        job = workflow["jobs"]["publish"]
        self.assertEqual(job["if"], "github.repository == 'SkyTruth/shared-datasets-1' && github.ref == 'refs/heads/main'")
        self.assertFalse(job["concurrency"]["cancel-in-progress"])
        self.assertEqual(job["timeout-minutes"], 15)
        steps = workflow_steps_by_name(workflow, "publish")
        self.assertFalse(steps["Check out reviewed revision"]["with"]["persist-credentials"])
        all_runs = "\n".join(str(step.get("run", "")) for step in job["steps"])
        for forbidden in ("git push", "git commit", "git config", "git add", "npm version"):
            self.assertNotIn(forbidden, all_runs)

    def test_publisher_requires_reviewed_version_then_checks_exact_tarball(self):
        workflow = load_workflow(WORKFLOW_PATH)
        steps = workflow_steps_by_name(workflow, "publish")
        check = steps["Require reviewed version increase"]
        self.assertEqual(check["if"], "github.event_name == 'push'")
        self.assertEqual(check["env"], {"BASE_SHA": "${{ github.event.before }}", "HEAD_SHA": "${{ github.sha }}"})
        self.assertEqual(check["run"], 'node scripts/release-policy.mjs changes "$BASE_SHA" "$HEAD_SHA"')
        condition = "github.event_name == 'workflow_dispatch' || steps.changes.outputs.release_needed == 'true'"
        for name in ("Install dependencies", "Test package", "Validate packed consumer", "Check registry version and integrity"):
            self.assertEqual(steps[name]["if"], condition)
        self.assertEqual(steps["Validate packed consumer"]["run"], "npm run test:pack")
        registry = steps["Check registry version and integrity"]
        self.assertEqual(registry["env"]["CANDIDATE"], "${{ steps.package.outputs.candidate }}")
        self.assertEqual(registry["run"], 'node scripts/release-policy.mjs registry "$CANDIDATE"')
        publish = steps["Publish validated package"]
        self.assertEqual(publish["if"], "steps.registry.outputs.should_publish == 'true'")
        self.assertEqual(publish["env"]["TARBALL"], "${{ steps.package.outputs.tarball }}")
        self.assertEqual(publish["run"], 'npm publish "$TARBALL" --access public --ignore-scripts --registry=https://registry.npmjs.org')
        self.assertEqual(workflow["env"]["NODE_VERSION"], "24")

    def test_sdk_validation_is_shared_and_required_for_both_node_versions(self):
        from scripts.ci_preflight import suite_commands
        workflow = load_workflow(VALIDATION_PATH)
        trigger = workflow_triggers(workflow)
        self.assertIn('pull_request', trigger)
        self.assertIsNone(trigger['pull_request'])
        self.assertEqual(workflow['permissions'], {'contents':'read'})
        job = workflow['jobs']['sdk-validation']
        self.assertEqual(job['strategy']['matrix']['node'], ['22', '24'])
        self.assertFalse(job['strategy']['fail-fast'])
        steps = workflow_steps_by_name(workflow, 'sdk-validation')
        self.assertIn('scripts/ci_preflight.py run-suite', steps['Run shared SDK version, runtime and packed consumer validation']['run'])
        for suite in ('sdk-node22', 'sdk-node24'):
            commands = [args for args, _ in suite_commands(suite, REPO_ROOT, {'base':'base','tested_sha':'head'}, REPO_ROOT)]
            self.assertIn(['node', 'scripts/release-policy.mjs', 'changes', 'base', 'head'], commands)
            self.assertIn(['npm','test'], commands)
            self.assertIn(['npm','run','test:pack'], commands)
            self.assertNotIn(['npm','publish'], commands)
        self.assertIn('sdk-validation', workflow['jobs']['ci-ready']['needs'])


if __name__ == "__main__":
    unittest.main()
