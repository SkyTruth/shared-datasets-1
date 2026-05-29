import unittest
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = REPO_ROOT / ".github/workflows/publish-typescript-sdk.yml"


class TypeScriptSdkReleaseWorkflowTest(unittest.TestCase):
    def load_workflow(self):
        workflow = yaml.safe_load(WORKFLOW_PATH.read_text())
        return workflow, workflow.get("on") or workflow.get(True)

    def test_publish_workflow_triggers_on_package_version_files(self):
        _workflow, trigger = self.load_workflow()

        self.assertIn("workflow_dispatch", trigger)
        self.assertEqual(trigger["push"]["branches"], ["main"])
        self.assertEqual(
            set(trigger["push"]["paths"]),
            {
                "api/typescript/package.json",
                "api/typescript/package-lock.json",
                ".github/workflows/publish-typescript-sdk.yml",
            },
        )

    def test_publish_workflow_only_publishes_greater_registry_version(self):
        workflow, _trigger = self.load_workflow()
        steps = workflow["jobs"]["publish"]["steps"]
        steps_by_name = {step["name"]: step for step in steps}

        check_step = steps_by_name["Check release version"]
        self.assertEqual(check_step["id"], "release-version")
        self.assertIn('npm", ["view", pkg.name, "version", "--json"]', check_step["run"])
        self.assertIn("compareSemver(pkg.version, publishedVersion)", check_step["run"])
        self.assertIn("should_publish=${shouldPublish}", check_step["run"])

        for step_name in ("Install dependencies", "Test package", "Verify package contents", "Publish package"):
            self.assertEqual(
                steps_by_name[step_name]["if"],
                "steps.release-version.outputs.should_publish == 'true'",
            )


if __name__ == "__main__":
    unittest.main()
