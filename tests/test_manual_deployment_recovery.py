"""Exact-source manual deployment contracts retain authority and replay barriers."""
from __future__ import annotations

import copy
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import yaml
from scripts import deployment_revision as deployment

ROOT = Path(__file__).resolve().parents[1]
REPO = "SkyTruth/shared-datasets-1"
SHA = "a" * 40
CURRENT_MAIN = "b" * 40
WORKFLOWS = ("pmtiles-cdn-sync.yml", "catalog-viewer-deploy.yml", "publish-typescript-sdk.yml")


def dispatch_errors(value):
    triggers = value.get("on", value.get(True, {}))
    manual = triggers.get("workflow_dispatch", {}) or {}
    inputs = manual.get("inputs", {})
    return [name for name in ("executor_sha", "source_run_id", "source_run_attempt") if not inputs.get(name, {}).get("required") or inputs[name].get("type") not in {"string", "number"}]


class ManualWorkflowTests(unittest.TestCase):
    def test_all_existing_manual_entrypoints_require_exact_source_identity(self):
        for filename in WORKFLOWS:
            value = yaml.safe_load((ROOT / ".github/workflows" / filename).read_text())
            with self.subTest(workflow=filename):
                self.assertEqual(dispatch_errors(value), [])
                self.assertNotIn("pull_request", value.get("on", value.get(True)))
                env = value["env"]
                for key, source in (("EXECUTOR_SHA", "executor_sha"), ("SOURCE_RUN_ID", "source_run_id"), ("SOURCE_RUN_ATTEMPT", "source_run_attempt")):
                    self.assertIn("inputs." + source, env[key])
                job = value["jobs"]["publish" if filename == "publish-typescript-sdk.yml" else "sync" if filename == "pmtiles-cdn-sync.yml" else "deploy"]
                steps = job["steps"]
                bootstrap = next(i for i, step in enumerate(steps) if step.get("with", {}).get("ref") == "${{ github.workflow_sha }}")
                candidate = next(i for i, step in enumerate(steps) if step.get("with", {}).get("ref") in {"${{ inputs.executor_sha }}", "${{ env.EXECUTOR_SHA }}"})
                proofs = [i for i, step in enumerate(steps) if "--bootstrap" in step.get("run", "") and "deployment_revision.py verify" in step["run"]]
                self.assertEqual(len(proofs), 1)
                self.assertLess(bootstrap, proofs[0])
                self.assertLess(proofs[0], candidate)
                if filename == "publish-typescript-sdk.yml":
                    # Verified obsolete CI events finish as no-ops; candidate
                    # checkout must have exactly the same admission gate.
                    self.assertEqual(steps[proofs[0]]["if"], "steps.source.outputs.source_eligible == 'true'")
                    self.assertEqual(steps[candidate]["if"], steps[proofs[0]]["if"])
                else:
                    self.assertNotIn("if", steps[proofs[0]])

    def test_missing_optional_or_wrongly_typed_manual_identity_cannot_pass(self):
        complete = {"on": {"workflow_dispatch": {"inputs": {name: {"required": True, "type": "string"} for name in ("executor_sha", "source_run_id", "source_run_attempt")}}}}
        self.assertEqual(dispatch_errors(complete), [])
        for name in ("executor_sha", "source_run_id", "source_run_attempt"):
            for change in ("missing", "optional", "boolean"):
                value = copy.deepcopy(complete)
                inputs = value["on"]["workflow_dispatch"]["inputs"]
                if change == "missing":
                    inputs.pop(name)
                else:
                    inputs[name]["required" if change == "optional" else "type"] = False if change == "optional" else "boolean"
                with self.subTest(name=name, change=change):
                    self.assertIn(name, dispatch_errors(value))


class ManualAuthorityTests(unittest.TestCase):
    def setUp(self):
        self.current = {"id": 22, "run_attempt": 1, "path": ".github/workflows/pmtiles-cdn-sync.yml", "workflow_id": 14,
                        "head_branch": "main", "head_sha": CURRENT_MAIN, "event": "workflow_dispatch",
                        "repository": {"id": 17, "full_name": REPO}, "head_repository": {"id": 17, "full_name": REPO}}
        self.source = {**self.current, "id": 13, "run_attempt": 2, "path": ".github/workflows/ci.yml", "workflow_id": 12,
                       "head_sha": SHA, "event": "push"}
        self.args = SimpleNamespace(workflow="pmtiles-cdn-sync.yml", executor_sha=SHA, source_run_id=13, source_run_attempt=2)
        self.api = Mock()
        self.api.get.side_effect = self.route
        self.api.pages.return_value = [{"name": "ci-ready", "status": "completed", "conclusion": "success"}]
        environment = patch.dict(os.environ, {"GITHUB_REPOSITORY": REPO, "GITHUB_REF": "refs/heads/main", "GITHUB_RUN_ID": "22", "GITHUB_RUN_ATTEMPT": "1"})
        environment.start()
        self.addCleanup(environment.stop)
        checkout = patch.object(deployment.subprocess, "check_output", return_value=SHA)
        checkout.start()
        self.addCleanup(checkout.stop)
        ancestry = patch.object(deployment, "git_ancestor", return_value=True)
        self.ancestry = ancestry.start()
        self.addCleanup(ancestry.stop)

    def route(self, path):
        if path == f"repos/{REPO}":
            return {"id": 17, "full_name": REPO}
        if path.endswith("/actions/runs/22"):
            return self.current
        if path.endswith("/actions/runs/13/attempts/2"):
            return self.source
        if "/actions/workflows/" in path:
            name = path.rsplit("/", 1)[-1]
            return {"id": 12 if name == "ci.yml" else 14, "path": ".github/workflows/" + name}
        raise AssertionError("unexpected API read: " + path)

    def test_manual_recovery_preserves_older_exact_tested_main_for_each_target(self):
        for filename in WORKFLOWS:
            self.args.workflow = filename
            self.current["path"] = ".github/workflows/" + filename
            with self.subTest(workflow=filename):
                self.assertEqual(deployment.verify_context(self.args, self.api), REPO)
        self.api.post.assert_not_called()

    def test_pr_fork_missing_source_or_unsuccessful_ci_cannot_authorize_manual_mutation(self):
        cases = ("pr-ref", "foreign-current-repo", "foreign-source-repo", "untested-sha", "pr-ci", "wrong-ci-id", "missing-attempt", "cancelled-ready", "missing-ready", "not-main-ancestor")
        for case in cases:
            with self.subTest(case=case):
                current, source = copy.deepcopy(self.current), copy.deepcopy(self.source)
                args = copy.copy(self.args)
                self.api.pages.return_value = [{"name": "ci-ready", "status": "completed", "conclusion": "success"}]
                self.ancestry.return_value = True
                if case == "foreign-current-repo":
                    self.current["repository"]["id"] = 99
                elif case == "foreign-source-repo":
                    self.source["head_repository"]["id"] = 99
                elif case == "untested-sha":
                    self.source["head_sha"] = CURRENT_MAIN
                elif case == "pr-ci":
                    self.source["event"] = "pull_request"
                elif case == "wrong-ci-id":
                    self.source["workflow_id"] = 99
                elif case == "missing-attempt":
                    self.args.source_run_attempt = None
                elif case in {"cancelled-ready", "missing-ready"}:
                    self.api.pages.return_value = [] if case == "missing-ready" else [{"name": "ci-ready", "status": "completed", "conclusion": "cancelled"}]
                elif case == "not-main-ancestor":
                    self.ancestry.return_value = False
                with patch.dict(os.environ, {"GITHUB_REF": "refs/pull/1/merge" if case == "pr-ref" else "refs/heads/main"}), self.assertRaises(deployment.DeploymentError):
                    deployment.verify_context(self.args, self.api)
                self.current, self.source, self.args = current, source, args
                self.api.post.assert_not_called()

    def test_failed_same_revision_remains_blocked_after_manual_context_validation(self):
        deployment.verify_context(self.args, self.api)
        record = {"id": 71, "sha": SHA, "ref": SHA, "environment": "production-pmtiles-cdn", "creator": {"login": "github-actions[bot]", "type": "Bot"},
                  "payload": {"schema": deployment.SCHEMA, "target": "pmtiles-cdn", "artifact": "image@sha256:" + "c" * 64,
                              "ci_run_id": 13, "ci_run_attempt": 2, "execution_run_id": 22, "execution_run_attempt": 1}}
        with self.assertRaisesRegex(deployment.DeploymentError, "incomplete or failed"):
            deployment.replay_decision([record], SHA, lambda older, newer: True, lambda value: [{"state": "failure", "description": "failed"}])
        self.api.post.assert_not_called()
