"""Production Terraform workflows get their own concurrency lane.

Seven workflows shared one `prod-terraform-state` group. GitHub keeps at most one
running plus one pending run per group and cancels the older pending run, so a
merge touching shared paths silently dropped deploys — seven times, once leaving
a merged fix undeployed for hours while the merge itself looked green.

Serializing state access is still required; the shared group was a crude proxy
for it. Terraform's own GCS state lock is the real mechanism, so every plan and
apply waits for the lock instead of failing, and the lanes only stop the runs
from cancelling each other.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from workflow_helpers import load_workflow


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = REPO_ROOT / ".github/workflows"
LOCK_TIMEOUT = "-lock-timeout=20m"
TERRAFORM_MUTATION_RE = re.compile(r"terraform\s+-chdir=\S+\s+(plan|apply)\b([^\n]*)")


def workflows_with_prod_lanes() -> dict[Path, str]:
    lanes: dict[Path, str] = {}
    for path in sorted(WORKFLOW_DIR.glob("*.yml")):
        text = path.read_text(encoding="utf-8")
        if "prod-terraform-state" not in text:
            continue
        workflow = load_workflow(path)
        for job in (workflow.get("jobs") or {}).values():
            group = ((job or {}).get("concurrency") or {}).get("group")
            if isinstance(group, str) and group.startswith("prod-terraform-state"):
                lanes[path] = group
    return lanes


class ProdTerraformLaneTests(unittest.TestCase):
    def test_every_prod_terraform_workflow_has_its_own_lane(self):
        lanes = workflows_with_prod_lanes()

        self.assertGreaterEqual(len(lanes), 7, f"expected the known prod Terraform workflows, got {lanes}")
        self.assertEqual(
            [path.name for path, group in lanes.items() if group == "prod-terraform-state"],
            [],
            "no workflow may sit in the shared lane: a third queued run cancels the pending one",
        )
        duplicates = {
            group for group in lanes.values() if list(lanes.values()).count(group) > 1
        }
        self.assertEqual(duplicates, set(), f"lanes must be distinct per workflow, shared: {duplicates}")

    def test_the_reusable_caller_gets_a_lane_per_sync(self):
        reusable = WORKFLOW_DIR / "prod-terraform-target-apply.yml"
        workflow = load_workflow(reusable)

        group = workflow["jobs"]["sync"]["concurrency"]["group"]
        self.assertEqual(group, "prod-terraform-state-${{ inputs.sync_name }}")
        self.assertIs(workflow["jobs"]["sync"]["concurrency"]["cancel-in-progress"], False)

    def test_no_prod_lane_cancels_a_run_in_progress(self):
        for path, group in workflows_with_prod_lanes().items():
            workflow = load_workflow(path)
            for job_name, job in (workflow.get("jobs") or {}).items():
                concurrency = (job or {}).get("concurrency") or {}
                if str(concurrency.get("group", "")).startswith("prod-terraform-state"):
                    with self.subTest(workflow=path.name, job=job_name):
                        self.assertIs(concurrency.get("cancel-in-progress"), False)

    def test_every_plan_and_apply_waits_for_the_state_lock(self):
        """Separate lanes are only safe because Terraform serializes on the lock."""
        for path in sorted(workflows_with_prod_lanes()):
            text = path.read_text(encoding="utf-8")
            for subcommand, tail in TERRAFORM_MUTATION_RE.findall(text):
                with self.subTest(workflow=path.name, subcommand=subcommand):
                    self.assertIn(
                        LOCK_TIMEOUT,
                        tail,
                        f"{path.name}: terraform {subcommand} must wait for the state lock, "
                        "otherwise a concurrent lane fails on lock acquisition",
                    )


if __name__ == "__main__":
    unittest.main()
