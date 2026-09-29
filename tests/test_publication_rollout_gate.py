from __future__ import annotations

import itertools
import contextlib
import csv
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest import mock

from scripts import publication_rollout_gate as gate
from workflow_helpers import load_workflow, workflow_triggers


ROOT = Path(__file__).resolve().parents[1]
JOBS = ("wdpa-monthly", "sea-ice-daily", "eamlis-monthly")
GATE_NAME = "Enforce publication rollout HOLD"


def checked_gate_steps(workflow, job_name):
    """Constrain the real Actions control flow before executing its local fence."""
    assert set(workflow["jobs"]) == {"deploy"}, "an additional job could bypass the fence"
    job = workflow["jobs"]["deploy"]
    assert job["if"] == "${{ github.event_name != 'pull_request' }}", "unexpected job condition"
    assert not job.get("continue-on-error"), "job must retain failure"
    steps = job["steps"]
    assert [step["name"] for step in steps[:3]] == [
        "Validate main ref", "Check out repository", GATE_NAME,
    ], "only main-ref validation and checkout may precede the fence"
    gate = steps[2]
    assert "if" not in gate and "continue-on-error" not in gate, "gate must be unconditional and fatal"
    assert gate["shell"] == "bash", "gate must propagate its process exit code"
    assert gate["run"].strip() == f"python scripts/publication_rollout_gate.py --job {job_name}", "gate command cannot accept overrides"
    for step in steps[3:]:
        # Actions adds success() to step conditions without a status function.
        # No downstream step may opt out of that failure propagation.
        assert not re.search(r"\b(?:always|failure|cancelled|success)\s*\(", str(step.get("if", ""))), "downstream status function could bypass HOLD"
    return steps


class PublicationRolloutPolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = json.loads(gate.POLICY_PATH.read_text())

    def test_valid_policy_is_hold_for_every_job_even_with_override_like_environment(self):
        gate.validate_policy(self.policy, gate.CATALOG_PATH)
        for name in JOBS:
            with self.subTest(job=name), mock.patch.dict(os.environ, {"PUBLICATION_ROLLOUT_STAGE": "active", "SHARED_DATASETS_ALLOW_CANONICAL_MUTATION": "1"}):
                with contextlib.redirect_stderr(io.StringIO()) as output:
                    self.assertEqual(gate.main(["--job", name]), 1)
                self.assertIn("PUBLICATION_ROLLOUT_HOLD", output.getvalue())

    def test_malformed_policy_cannot_authorize_deployment(self):
        malformed = [None, [], True, {**self.policy, "allow": True}]
        malformed.extend({key: value for key, value in self.policy.items() if key != missing} for missing in self.policy)
        for field, values in (("schema_version", (True, 0, 2, "1")), ("stage", (None, True, "active", "install_guard", "HOLD", " hold", "")), ("jobs", ({}, [], None))):
            malformed.extend({**self.policy, field: value} for value in values)
        for name in JOBS:
            missing = deepcopy(self.policy)
            del missing["jobs"][name]
            malformed.append(missing)
            for assets in ({}, None, [], {"foreign": "100-other/110-other/foreign"}):
                changed = deepcopy(self.policy)
                changed["jobs"][name] = assets
                malformed.append(changed)
            for root in (None, [], "gs://foreign/wrong/root", "100-other/110-other/foreign"):
                changed = deepcopy(self.policy)
                slug = next(iter(changed["jobs"][name]))
                changed["jobs"][name][slug] = root
                malformed.append(changed)
        extra = deepcopy(self.policy)
        extra["jobs"]["other-job"] = {}
        malformed.append(extra)
        extra_asset = deepcopy(self.policy)
        extra_asset["jobs"]["wdpa-monthly"]["foreign"] = "100-other/110-other/foreign"
        malformed.append(extra_asset)
        for value in malformed:
            with self.subTest(value=value), self.assertRaises(ValueError):
                gate.validate_policy(value, gate.CATALOG_PATH)

    def test_duplicate_fields_and_nonfinite_json_are_invalid(self):
        for data in ('{"stage":"active","stage":"hold"}', '{"jobs":{"wdpa-monthly":{},"wdpa-monthly":{}}}', '{"stage":NaN}', '{"stage":Infinity}', '{"stage":-Infinity}'):
            with self.subTest(data=data), self.assertRaises(ValueError):
                gate.strict_json(data)

    def test_catalog_registration_is_exact_and_complete(self):
        with gate.CATALOG_PATH.open(newline="") as handle:
            reader = csv.DictReader(handle)
            fields = reader.fieldnames
            rows = list(reader)
        for name, expected in gate.REQUIRED_ASSETS_BY_JOB.items():
            for slug in expected:
                for defect in ("missing", "duplicate", "bucket", "path", "category", "subcategory"):
                    changed = deepcopy(rows)
                    row = next(row for row in changed if row["asset_slug"] == slug)
                    if defect == "missing":
                        changed.remove(row)
                    elif defect == "duplicate":
                        changed.append(dict(row))
                    elif defect == "bucket":
                        row["canonical_path"] = row["canonical_path"].replace(gate.BUCKET, "other-bucket")
                    elif defect == "path":
                        row["canonical_path"] = row["canonical_path"].replace("/latest/", "/releases/2026-09-22/")
                    else:
                        row[defect] = "../escape"
                    with self.subTest(job=name, slug=slug, defect=defect), tempfile.TemporaryDirectory() as directory:
                        path = Path(directory) / "catalog.csv"
                        with path.open("w", newline="") as handle:
                            writer = csv.DictWriter(handle, fieldnames=fields)
                            writer.writeheader()
                            writer.writerows(changed)
                        with self.assertRaises(ValueError):
                            gate.validate_policy(self.policy, path)

    def test_catalog_columns_cannot_be_missing_or_ambiguous(self):
        for header in ("asset_slug,category,subcategory", "asset_slug,category,subcategory,canonical_path,canonical_path"):
            with self.subTest(header=header), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "catalog.csv"
                path.write_text(header + "\n")
                with self.assertRaisesRegex(ValueError, "missing/duplicate"):
                    gate.validate_policy(self.policy, path)

    def test_missing_corrupt_or_incomplete_local_config_is_an_explicit_invalid_result(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            for data in (None, "{", '{"schema_version":1,"stage":"active","jobs":{}}'):
                if data is not None:
                    path.write_text(data)
                with self.subTest(data=data), mock.patch.object(gate, "POLICY_PATH", path), contextlib.redirect_stderr(io.StringIO()) as output:
                    self.assertEqual(gate.main(["--job", JOBS[0]]), 2)
                    self.assertIn("PUBLICATION_ROLLOUT_INVALID", output.getvalue())
            with mock.patch.object(gate, "CATALOG_PATH", Path(directory) / "missing.csv"), contextlib.redirect_stderr(io.StringIO()) as output:
                self.assertEqual(gate.main(["--job", JOBS[0]]), 2)
                self.assertIn("PUBLICATION_ROLLOUT_INVALID", output.getvalue())

    def test_cli_is_stdlib_only_and_has_no_override_or_unknown_job(self):
        script = ROOT / "scripts/publication_rollout_gate.py"
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, "-I", "-S", str(script), "--job", JOBS[0]], cwd=directory, capture_output=True, text=True)
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn("PUBLICATION_ROLLOUT_HOLD", result.stderr)
            for args in (("--job", "unknown"), ("--job", JOBS[0], "--allow"), ("--job", JOBS[0], "--policy", "other.json"), ()):
                with self.subTest(args=args):
                    result = subprocess.run([sys.executable, "-I", "-S", str(script), *args], cwd=directory, capture_output=True, text=True)
                    self.assertEqual(result.returncode, 2, result.stderr)


class PublicationRolloutWorkflowTests(unittest.TestCase):
    def test_actual_workflows_stop_before_every_cloud_step_on_push_and_dispatch(self):
        for name in JOBS:
            workflow = load_workflow(ROOT / f".github/workflows/{name}-deploy.yml")
            steps = checked_gate_steps(workflow, name)
            triggers = workflow_triggers(workflow)
            self.assertEqual(set(triggers), {"push", "workflow_dispatch"})
            self.assertTrue({"catalog/publication-rollout.json", "scripts/publication_rollout_gate.py"} <= set(triggers["push"]["paths"]))
            for event, cancel, resume in itertools.product(("push", "workflow_dispatch"), ("true", "false"), ("true", "false")):
                with self.subTest(job=name, event=event, cancel=cancel, resume=resume):
                    env = {**os.environ, "GITHUB_REF": "refs/heads/main", "GITHUB_EVENT_NAME": event,
                           "CANCEL_RUNNING_CANARY": cancel, "RESUME_SCHEDULER": resume}
                    attempted = []
                    success = True
                    for step in steps:
                        if not success:
                            # checked_gate_steps proved every remaining Actions condition
                            # has implicit success(), independent of its input predicates.
                            continue
                        attempted.append(step["name"])
                        if step["name"] == "Check out repository":
                            self.assertTrue(step["uses"].startswith("actions/checkout@"))
                            continue  # the local checkout already supplies these exact files
                        self.assertIn(step["name"], {"Validate main ref", GATE_NAME})
                        result = subprocess.run(["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", step["run"]], cwd=ROOT, env=env, capture_output=True, text=True)
                        success = result.returncode == 0
                        if step["name"] == GATE_NAME:
                            self.assertEqual(result.returncode, 1, result.stderr)
                            self.assertIn("PUBLICATION_ROLLOUT_HOLD", result.stderr)
                    self.assertEqual(attempted, ["Validate main ref", "Check out repository", GATE_NAME])
                    self.assertFalse(success)

    def test_workflow_checker_rejects_realistic_fence_bypasses(self):
        for name in JOBS:
            workflow = load_workflow(ROOT / f".github/workflows/{name}-deploy.yml")
            checked_gate_steps(workflow, name)
            for defect in ("conditional", "ignored", "early-auth", "always", "failure", "extra-job", "help"):
                changed = deepcopy(workflow)
                steps = changed["jobs"]["deploy"]["steps"]
                if defect == "conditional":
                    steps[2]["if"] = "${{ github.event_name == 'push' }}"
                elif defect == "ignored":
                    steps[2]["continue-on-error"] = True
                elif defect == "early-auth":
                    steps.insert(2, steps.pop(next(i for i, step in enumerate(steps) if "auth@" in step.get("uses", ""))))
                elif defect in {"always", "failure"}:
                    steps[-1]["if"] = "${{ " + defect + "() }}"
                elif defect == "extra-job":
                    changed["jobs"]["unguarded"] = {"steps": []}
                else:
                    steps[2]["run"] += " --help"
                with self.subTest(job=name, defect=defect), self.assertRaises(AssertionError):
                    checked_gate_steps(changed, name)


if __name__ == "__main__":
    unittest.main()
