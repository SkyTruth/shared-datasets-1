"""Tests for the Terraform state-lock retry wrapper.

PR #141 gave each prod Terraform workflow its own concurrency lane and trusted
`-lock-timeout` to serialize state access. It does not cover the GCS backend's
create-race: two runs starting together produced

    Error acquiring the state lock
      * writing ".../default.tflock" failed: Error 412 ... conditionNotMet
      * storage: object doesn't exist

and the backend treats that compound error as fatal rather than retryable. The
lanes were reverted. They can only come back on top of a retry that is actually
exercised, which is what these tests do — including that it never masks a real
Terraform failure.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
WRAPPER = REPO_ROOT / "scripts/terraform_retry.sh"

LOCK_ERROR = textwrap.dedent(
    """\
    Error: Error acquiring the state lock
    Error message: 2 errors occurred:
      * writing "gs://bucket/default.tflock" failed: googleapi: Error 412: conditionNotMet
      * storage: object doesn't exist
    """
)


def fake_terraform(script_body: str) -> tuple[str, Path]:
    """Write a stand-in terraform executable and return its path and state file."""
    directory = Path(tempfile.mkdtemp(prefix="terraform-retry-"))
    counter = directory / "attempts"
    counter.write_text("0", encoding="utf-8")
    binary = directory / "terraform"
    binary.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            count=$(cat "{counter}")
            count=$((count + 1))
            echo "$count" > "{counter}"
            {script_body}
            """
        ),
        encoding="utf-8",
    )
    binary.chmod(0o755)
    return str(binary), counter


def run_wrapper(binary: str, *, attempts: int = 5, delay: int = 0) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "TERRAFORM_BIN": binary,
        "TERRAFORM_RETRY_ATTEMPTS": str(attempts),
        "TERRAFORM_RETRY_BASE_DELAY": str(delay),
    }
    return subprocess.run(
        [str(WRAPPER), "plan", "-input=false"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


class TerraformRetryTests(unittest.TestCase):
    def test_success_passes_straight_through(self):
        binary, counter = fake_terraform('echo "Plan: 1 to add"; exit 0')

        result = run_wrapper(binary)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(counter.read_text().strip(), "1")
        self.assertIn("Plan: 1 to add", result.stdout)

    def test_lock_contention_is_retried_until_it_succeeds(self):
        binary, counter = fake_terraform(
            f'if [[ "$count" -lt 3 ]]; then cat <<\'EOF\'\n{LOCK_ERROR}EOF\n  exit 1; fi; echo "Apply complete"; exit 0'
        )

        result = run_wrapper(binary)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(counter.read_text().strip(), "3", "should retry until the lock frees")
        self.assertIn("Apply complete", result.stdout)
        self.assertIn("state lock contended", result.stderr)

    def test_a_real_terraform_error_fails_immediately(self):
        """The wrapper must never turn a genuine failure into a retry loop."""
        binary, counter = fake_terraform('echo "Error: Invalid resource type"; exit 1')

        result = run_wrapper(binary)

        self.assertEqual(result.returncode, 1)
        self.assertEqual(counter.read_text().strip(), "1", "non-lock errors must not be retried")
        self.assertIn("Invalid resource type", result.stdout)

    def test_permission_errors_are_not_retried(self):
        binary, counter = fake_terraform('echo "Error: googleapi: Error 403: Permission denied"; exit 1')

        result = run_wrapper(binary)

        self.assertEqual(result.returncode, 1)
        self.assertEqual(counter.read_text().strip(), "1")

    def test_persistent_contention_eventually_gives_up_with_the_original_status(self):
        binary, counter = fake_terraform(f"cat <<'EOF'\n{LOCK_ERROR}EOF\nexit 2")

        result = run_wrapper(binary, attempts=3)

        self.assertEqual(result.returncode, 2, "must surface Terraform's own exit status")
        self.assertEqual(counter.read_text().strip(), "3")
        self.assertIn("giving up", result.stderr)

    def test_arguments_reach_terraform_unchanged(self):
        binary, _counter = fake_terraform('echo "args:$*"; exit 0')

        result = run_wrapper(binary)

        self.assertIn("args:plan -input=false", result.stdout)

    def test_missing_arguments_is_a_usage_error(self):
        result = subprocess.run([str(WRAPPER)], capture_output=True, text=True, check=False)

        self.assertEqual(result.returncode, 2)
        self.assertIn("usage:", result.stderr)


class WrapperIsUsedForProdMutationsTests(unittest.TestCase):
    """Every prod plan/apply must go through the wrapper, or lanes are unsafe."""

    WORKFLOWS = (
        "prod-terraform-target-apply.yml",
        "wdpa-monthly-deploy.yml",
        "eamlis-monthly-deploy.yml",
        "sea-ice-daily-deploy.yml",
        "catalog-viewer-deploy.yml",
        "metadata-service-deploy.yml",
        "pmtiles-cdn-sync.yml",
    )

    def test_no_bare_terraform_plan_or_apply_against_prod(self):
        for name in self.WORKFLOWS:
            text = (REPO_ROOT / ".github/workflows" / name).read_text(encoding="utf-8")
            for line in text.splitlines():
                stripped = line.strip()
                if "terraform " not in stripped or stripped.startswith("#"):
                    continue
                if " plan" not in stripped and " apply" not in stripped:
                    continue
                if "terraform_retry.sh" in stripped:
                    continue
                with self.subTest(workflow=name, line=stripped[:90]):
                    self.fail(
                        f"{name}: plan/apply must run through scripts/terraform_retry.sh so a "
                        f"state-lock race is retried rather than failing the run: {stripped[:90]}"
                    )



class CanaryDuplicationGuardTests(unittest.TestCase):
    """A deploy must not start a canary alongside one already publishing.

    On 2026-08-05 two merges minutes apart started two wdpa-monthly canaries.
    The second rebuilt all 304,816 features over ~110 minutes before the publish
    step re-checked the run record and skipped. Nothing corrupted, but two
    ~2-hour 8-CPU runs were wasted. Skipping is preferred over cancelling
    because the in-flight run is usually executing identical code and may be
    most of the way through an actual release.
    """

    DEPLOYS = {
        "wdpa-monthly-deploy.yml": "wdpa-monthly",
        "eamlis-monthly-deploy.yml": "eamlis-monthly",
        "sea-ice-daily-deploy.yml": "sea-ice-daily",
    }

    def workflow(self, name: str) -> dict:
        import yaml

        return yaml.safe_load((REPO_ROOT / ".github/workflows" / name).read_text(encoding="utf-8"))

    def steps(self, name: str) -> list[dict]:
        return self.workflow(name)["jobs"]["deploy"]["steps"]

    def test_every_deploy_resolves_an_in_flight_canary_first(self):
        for name, job in self.DEPLOYS.items():
            with self.subTest(workflow=name):
                steps = self.steps(name)
                names = [s.get("name") for s in steps]
                self.assertIn("Resolve in-flight canary", names)
                self.assertLess(
                    names.index("Resolve in-flight canary"),
                    names.index(f"Execute {job} canary"),
                    "the guard must run before the canary",
                )

    def test_the_canary_is_skipped_when_one_is_already_running(self):
        for name, job in self.DEPLOYS.items():
            with self.subTest(workflow=name):
                steps = {s.get("name"): s for s in self.steps(name)}
                guard = steps["Resolve in-flight canary"]["run"]
                canary = steps[f"Execute {job} canary"]

                self.assertIn("status.runningCount", guard)
                self.assertIn("run_canary=false", guard)
                self.assertEqual(canary["if"], "${{ steps.inflight.outputs.run_canary == 'true' }}")

    def test_cancelling_is_available_but_opt_in(self):
        for name in self.DEPLOYS:
            with self.subTest(workflow=name):
                workflow = self.workflow(name)
                trigger = workflow.get("on") or workflow.get(True)
                option = trigger["workflow_dispatch"]["inputs"]["cancel_running_canary"]
                self.assertIs(option["default"], False, "skipping must be the default")

                guard = {s.get("name"): s for s in self.steps(name)}["Resolve in-flight canary"]["run"]
                self.assertIn("gcloud run jobs executions cancel", guard)
                self.assertIn('"${CANCEL_RUNNING_CANARY}" == "true"', guard)

    def test_the_skip_is_reported_rather_than_silent(self):
        for name in self.DEPLOYS:
            with self.subTest(workflow=name):
                guard = {s.get("name"): s for s in self.steps(name)}["Resolve in-flight canary"]["run"]
                self.assertIn("::notice::", guard)
                self.assertIn("cancel_running_canary=true", guard)

if __name__ == "__main__":
    unittest.main()
