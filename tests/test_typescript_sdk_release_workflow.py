import unittest
from pathlib import Path

from workflow_helpers import load_workflow, workflow_steps_by_name, workflow_triggers


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = REPO_ROOT / ".github/workflows/publish-typescript-sdk.yml"
VALIDATION_PATH = REPO_ROOT / ".github/workflows/sdk-validation.yml"


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

    def test_node24_validation_runs_on_all_prs_with_no_publish_permission(self):
        workflow = load_workflow(VALIDATION_PATH)
        trigger = workflow_triggers(workflow)
        self.assertIn("pull_request", trigger)
        self.assertIsNone(trigger["pull_request"])
        self.assertEqual(trigger["push"], {"branches": ["main"]})
        self.assertEqual(workflow["permissions"], {"contents": "read"})
        job = workflow["jobs"]["sdk-validation"]
        self.assertEqual(job["timeout-minutes"], 15)
        steps = workflow_steps_by_name(workflow, "sdk-validation")
        self.assertEqual(steps["Set up Node"]["with"]["node-version"], "24")
        self.assertFalse(steps["Check out repository"]["with"]["persist-credentials"])
        self.assertEqual(steps["Require reviewed version increase"]["env"]["BASE_SHA"], "${{ github.event.pull_request.base.sha }}")
        self.assertEqual(steps["Test package on release runtime"]["run"], "npm test")
        self.assertEqual(steps["Validate packed consumer"]["run"], "npm run test:pack")
        self.assertNotIn("npm publish", "\n".join(str(step.get("run", "")) for step in job["steps"]))


if __name__ == "__main__":
    unittest.main()
