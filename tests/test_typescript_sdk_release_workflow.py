import unittest
from pathlib import Path

from workflow_helpers import load_workflow, workflow_steps_by_name, workflow_triggers


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = REPO_ROOT / ".github/workflows/publish-typescript-sdk.yml"


class TypeScriptSdkReleaseWorkflowTest(unittest.TestCase):
    def test_publish_workflow_triggers_on_package_content_files(self):
        workflow = load_workflow(WORKFLOW_PATH)
        trigger = workflow_triggers(workflow)

        self.assertIn("workflow_dispatch", trigger)
        self.assertEqual(trigger["push"]["branches"], ["main"])
        self.assertEqual(
            set(trigger["push"]["paths"]),
            {
                "api/typescript/src/**",
                "api/typescript/README.md",
                "api/typescript/package.json",
                "api/typescript/package-lock.json",
                "api/typescript/tsconfig.json",
                ".github/workflows/publish-typescript-sdk.yml",
            },
        )
        self.assertEqual(workflow["permissions"]["contents"], "write")

    def test_publish_workflow_bumps_before_publish_when_needed(self):
        workflow = load_workflow(WORKFLOW_PATH)
        steps_by_name = workflow_steps_by_name(workflow, "publish")

        check_step = steps_by_name["Check release version"]
        self.assertEqual(check_step["id"], "release-version")
        self.assertIn('npm", ["view", pkg.name, "version", "--json"]', check_step["run"])
        self.assertIn("compareSemver(pkg.version, publishedVersion)", check_step["run"])
        self.assertIn("should_bump=${shouldBump}", check_step["run"])
        self.assertIn("target_version=${targetVersion}", check_step["run"])
        self.assertIn("should_publish=${shouldPublish}", check_step["run"])

        bump_step = steps_by_name["Bump package metadata"]
        self.assertEqual(bump_step["if"], "steps.release-version.outputs.should_bump == 'true'")
        self.assertIn("npm version --no-git-tag-version", bump_step["run"])
        self.assertIn("git commit -m", bump_step["run"])
        self.assertIn("git push", bump_step["run"])

        for step_name in ("Install dependencies", "Test package", "Verify package contents", "Publish package"):
            self.assertEqual(
                steps_by_name[step_name]["if"],
                "steps.release-version.outputs.should_publish == 'true'",
            )


if __name__ == "__main__":
    unittest.main()
