from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from scripts import deployment_revision as d
from scripts import deployment_permissions as permissions

REPO = "SkyTruth/shared-datasets-1"
OLD, CURRENT, NEW = "a" * 40, "b" * 40, "c" * 40
IMAGE = "us-central1-docker.pkg.dev/shared-datasets-1/shared-datasets-jobs/wdpa-monthly@sha256:" + "d" * 64


class DeploymentProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.run = {"id": 13, "run_attempt": 2, "workflow_id": 12, "path": ".github/workflows/ci.yml", "event": "push", "head_branch": "main", "head_sha": CURRENT,
                    "repository": {"id": 17, "full_name": REPO}, "head_repository": {"id": 17, "full_name": REPO}}
        self.api = Mock()
        self.api.get.side_effect = self.route
        self.api.pages.return_value = [{"name": "ci-ready", "status": "completed", "conclusion": "success"}]

    def route(self, path, run=None):
        if path == f"repos/{REPO}":
            return {"id": 17, "full_name": REPO}
        if path.endswith("/actions/workflows/ci.yml"):
            return {"id": 12, "path": ".github/workflows/ci.yml"}
        return self.run if run is None else run

    def test_completed_gate_in_still_running_main_ci_can_authorize_dependents(self):
        self.run["status"] = "in_progress"
        self.assertEqual(d.verify_ci(self.api, REPO, CURRENT, 13, 2), self.run)
        self.api.pages.assert_called_once_with(f"repos/{REPO}/actions/runs/13/attempts/2/jobs?per_page=100", "jobs")

    def test_rejects_untested_or_untrusted_source_before_mutation(self):
        for key, value in [("event", "pull_request"), ("head_branch", "feature"), ("head_sha", OLD), ("path", ".github/workflows/fake.yml"),
                           ("run_attempt", 1), ("repository", {"full_name": "attacker/repo"}), ("head_repository", {"full_name": "attacker/repo"})]:
            with self.subTest(key=key), patch.object(self.api, "get", side_effect=lambda path: self.route(path, {**self.run, key: value})):
                with self.assertRaises(d.DeploymentError):
                    d.verify_ci(self.api, REPO, CURRENT, 13, 2)
        self.api.post.assert_not_called()

    def test_missing_cancelled_failed_skipped_and_duplicate_gates_fail(self):
        for jobs in [[], [{"name": "ci-ready", "status": "completed", "conclusion": state} for state in ("success", "success")],
                     *[[{"name": "ci-ready", "status": "completed", "conclusion": state}] for state in ("skipped", "cancelled", "failure", None)],
                     [{"name": "ci-ready", "status": "in_progress", "conclusion": "success"}]]:
            with self.subTest(jobs=jobs), patch.object(self.api, "pages", return_value=jobs):
                with self.assertRaises(d.DeploymentError):
                    d.verify_ci(self.api, REPO, CURRENT, 13, 2)


class ReplayTests(unittest.TestCase):
    @staticmethod
    def record(sha, identifier=1):
        return {"id": identifier, "sha": sha, "ref": sha, "environment": "production-wdpa-monthly", "creator": {"login": "github-actions[bot]", "type": "Bot"},
                "payload": {"schema": d.SCHEMA, "target": "wdpa-monthly", "ci_run_id": 13, "ci_run_attempt": 2, "execution_run_id": 13, "execution_run_attempt": 2, "artifact": IMAGE, "targets": []}}

    @staticmethod
    def ancestor(older, newer):
        return [OLD, CURRENT, NEW].index(older) <= [OLD, CURRENT, NEW].index(newer)

    def test_new_revision_can_follow_older_attempts(self):
        self.assertEqual(d.replay_decision([self.record(OLD)], CURRENT, self.ancestor, Mock()), "proceed")

    def test_successful_exact_revision_is_noop(self):
        self.assertEqual(d.replay_decision([self.record(CURRENT)], CURRENT, self.ancestor, lambda _: [{"state": "success", "description": "verified"}]), "noop")

    def test_catalog_distinct_current_bundle_can_refresh_same_executor(self):
        record = self.record(CURRENT)
        record["environment"] = "production-catalog-web"
        record["payload"]["target"] = "catalog-web"
        def status(_):
            return [{"state": "success", "description": "applied"}]
        self.assertEqual(d.replay_decision([record], CURRENT, self.ancestor, status, artifact=IMAGE, target="catalog-web"), "noop")
        self.assertEqual(d.replay_decision([record], CURRENT, self.ancestor, status, artifact=IMAGE.replace("d" * 64, "e" * 64), target="catalog-web"), "proceed")
        with self.assertRaises(d.DeploymentError):
            d.replay_decision([self.record(CURRENT)], CURRENT, self.ancestor, status, artifact=IMAGE.replace("d" * 64, "e" * 64), target="wdpa-monthly")

    def test_newer_verified_attempt_supersedes_older_revision_without_mutation(self):
        for records in [[self.record(CURRENT), self.record(NEW, 2)], [self.record(NEW, 2), self.record(CURRENT)]]:
            with self.subTest(records=records):
                self.assertEqual(d.replay_decision(records, CURRENT, self.ancestor, lambda _: [{"state": "success", "description": "verified"}]), "superseded")
        self.assertEqual(d.replay_decision([self.record(NEW)], CURRENT, self.ancestor, Mock()), "superseded")

    def test_explicit_monthly_build_can_repeat_successful_producer_revision(self):
        record = self.record(CURRENT)
        record["payload"]["target"] = "wdpa-monthly-build"
        record["environment"] = "production-wdpa-monthly-build"
        def status(_):
            return [{"state": "success", "description": "applied"}]
        for artifact in (None, IMAGE, IMAGE.replace("d" * 64, "e" * 64)):
            with self.subTest(artifact=artifact):
                self.assertEqual(d.replay_decision([record], CURRENT, self.ancestor, status,
                    artifact=artifact, target="wdpa-monthly-build"), "proceed")
        self.assertEqual(d.replay_decision([record, self.record(NEW, 2)], CURRENT,
            self.ancestor, status, target="wdpa-monthly-build"), "superseded")
        for terminal in ([], [{"state": "failure", "description": "failed"}],
                         [{"state": "in_progress", "description": "started"}]):
            with self.subTest(terminal=terminal), self.assertRaisesRegex(d.DeploymentError, "reconcile"):
                d.replay_decision([record], CURRENT, self.ancestor,
                    lambda _: terminal, target="wdpa-monthly-build")

    def test_divergent_history_and_same_revision_failure_cannot_be_hidden_as_superseded(self):
        with self.assertRaisesRegex(d.DeploymentError, "divergent"):
            d.replay_decision([self.record(NEW)], CURRENT, lambda *_: False, Mock())
        for status in ([], [{"state": "failure", "description": "failed"}], [{"state": "in_progress", "description": "started"}]):
            with self.subTest(status=status), self.assertRaisesRegex(d.DeploymentError, "reconcile"):
                d.replay_decision([self.record(CURRENT), self.record(NEW, 2)], CURRENT, self.ancestor, lambda _: status)

    def test_failed_unknown_pending_or_unrecognized_records_require_reconciliation(self):
        for status in ([], [{"state": "failure", "description": "failed"}], [{"state": "in_progress", "description": "verification_pending"}], [{"state": "success", "description": "arbitrary"}], [{"state": "success", "description": "applied"}]):
            with self.subTest(status=status), self.assertRaises(d.DeploymentError):
                d.replay_decision([self.record(CURRENT)], CURRENT, self.ancestor, lambda _: status)
        with self.assertRaises(d.DeploymentError):
            d.replay_decision([{"id": 1, "sha": OLD, "payload": {"schema": "foreign"}}], CURRENT, self.ancestor, Mock())

    def test_missing_history_fails_instead_of_treating_unknown_commit_as_old(self):
        with patch.object(d.subprocess, "run", return_value=Mock(returncode=128)), self.assertRaisesRegex(d.DeploymentError, "history"):
            d.git_ancestor(OLD, CURRENT)


class TerminalTests(unittest.TestCase):
    def raw(self, status):
        return {"spec": {"template": {"spec": {"containers": [{"image": IMAGE}]}}}, "status": status}

    def test_started_execution_is_pending_and_only_matching_terminal_success_verifies(self):
        self.assertEqual(d.terminal_execution(self.raw({"runningCount": 1}), IMAGE), "verification_pending")
        self.assertEqual(d.terminal_execution(self.raw({"completionTime": "now", "succeededCount": 1}), IMAGE), "verified")
        self.assertEqual(d.terminal_execution(self.raw({"completionTime": "now"}), IMAGE), "unknown")
        self.assertEqual(d.terminal_execution(self.raw({"failedCount": 1}), IMAGE), "failed")
        self.assertEqual(d.terminal_execution(self.raw({"cancelledCount": 1}), IMAGE), "failed")
        with self.assertRaises(d.DeploymentError):
            d.terminal_execution(self.raw({"completionTime": "now", "succeededCount": 1}), IMAGE.replace("d", "e"))

    def test_running_old_image_defers_new_mutation_before_canary_is_skipped(self):
        self.assertFalse(d.verification_window([self.raw({"runningCount": 1})]))
        self.assertFalse(d.verification_window([self.raw({"runningCount": 0})]))
        self.assertTrue(d.verification_window([self.raw({"completionTime": "now", "succeededCount": 1})]))
        self.assertTrue(d.verification_window([self.raw({"runningCount": 1})], allow_cancel=True))

    def test_idle_wdpa_can_deploy_without_a_monthly_canary_date(self):
        self.assertTrue(d.verification_window([]))
        self.assertTrue(d.verification_window([], canary_date="2026-10-01"))
        with self.assertRaises(d.DeploymentError):
            d.verification_window([], canary_date="bad;exit 0")


class PermissionReadinessTests(unittest.TestCase):
    def test_secret_is_tested_on_actual_conditioned_resource(self):
        checks = permissions.checks("translation-bootstrap")
        self.assertEqual(len(checks), 1)
        self.assertIn("/secrets/shared-datasets-slack-webhook-url:testIamPermissions", checks[0][0])
        self.assertIn("secretmanager.secrets.setIamPolicy", checks[0][1])

    def test_artifact_and_bucket_policy_probes_use_actual_resources(self):
        registry = permissions.checks("artifact-registry")
        self.assertEqual(len(registry), 1)
        self.assertIn("/locations/us-central1/repositories/shared-datasets-jobs:testIamPermissions", registry[0][0])
        self.assertEqual(set(registry[0][1]), {"artifactregistry.repositories.getIamPolicy", "artifactregistry.repositories.setIamPolicy"})
        bucket = permissions.checks("bucket-iam")
        self.assertIn("/b/skytruth-shared-datasets-1/iam/testPermissions?", bucket[0][0])
        self.assertIn("storage.buckets.setIamPolicy", bucket[0][1])

    def test_propagation_retries_reads_then_passes(self):
        probe = Mock(side_effect=[["secretmanager.secrets.get"], []])
        pause = Mock()
        permissions.verify("translation-bootstrap", probe, attempts=2, pause=pause)
        pause.assert_called_once_with(10)
        self.assertEqual(probe.call_count, 2)

    def test_missing_authority_fails_without_mutation_or_unbounded_retry(self):
        probe, pause = Mock(return_value=["run.jobs.runWithOverrides"]), Mock()
        with self.assertRaisesRegex(RuntimeError, "runWithOverrides"):
            permissions.verify("sea-ice-daily", probe, attempts=2, pause=pause)
        self.assertEqual(probe.call_count, 2)
        self.assertEqual(pause.call_count, 1)

    def test_network_error_is_visible_and_not_reclassified_as_propagation(self):
        with self.assertRaises(OSError):
            permissions.verify("sea-ice-daily", Mock(side_effect=OSError("network")), attempts=2)


class CallerAndRecordTests(unittest.TestCase):
    setUp = DeploymentProvenanceTests.setUp
    route = DeploymentProvenanceTests.route

    def test_scheduled_catalog_refresh_requires_its_exact_tested_main_revision(self):
        from types import SimpleNamespace
        args = SimpleNamespace(workflow="catalog-web-deploy.yml", executor_sha=CURRENT, source_run_id=13, source_run_attempt=2)
        execution = {**self.run, "id": 14, "run_attempt": 1, "event": "schedule", "workflow_id": 21,
                     "path": ".github/workflows/catalog-web-deploy.yml"}
        def route(path):
            if path.endswith("/actions/runs/14"):
                return execution
            if path.endswith("/actions/workflows/catalog-web-deploy.yml"):
                return {"id": 21, "path": execution["path"]}
            return self.route(path)
        env = {"GITHUB_REF": "refs/heads/main", "GITHUB_REPOSITORY": REPO, "GITHUB_RUN_ID": "14", "GITHUB_RUN_ATTEMPT": "1"}
        with patch.dict(d.os.environ, env), patch.object(self.api, "get", side_effect=route), patch.object(d.subprocess, "check_output", return_value=CURRENT), patch.object(d, "git_ancestor", return_value=True):
            self.assertEqual(d.verify_context(args, self.api), REPO)
            for key, value in [("head_sha", OLD), ("head_branch", "feature"), ("path", ".github/workflows/ci.yml")]:
                with self.subTest(key=key), patch.dict(execution, {key: value}), self.assertRaises(d.DeploymentError):
                    d.verify_context(args, self.api)
            with patch.object(self.api, "pages", return_value=[{"name": "ci-ready", "status": "completed", "conclusion": "failure"}]), self.assertRaises(d.DeploymentError):
                d.verify_context(args, self.api)
        self.api.post.assert_not_called()

    def test_scheduled_catalog_record_retains_execution_and_ci_provenance(self):
        record = ReplayTests.record(CURRENT)
        record["environment"] = "production-catalog-web"
        record["payload"].update(target="catalog-web", execution_run_id=14, execution_run_attempt=1)
        execution = {**self.run, "id": 14, "run_attempt": 1, "event": "schedule", "workflow_id": 21,
                     "path": ".github/workflows/catalog-web-deploy.yml"}
        def route(path):
            if path.endswith("/actions/runs/14/attempts/1"):
                return execution
            if path.endswith("/actions/workflows/catalog-web-deploy.yml"):
                return {"id": 21, "path": execution["path"]}
            return self.route(path)
        with patch.object(self.api, "get", side_effect=route):
            self.assertEqual(d.verify_record_identity(self.api, REPO, record)["target"], "catalog-web")
            for key, value in [("head_sha", OLD), ("head_branch", "feature"), ("path", ".github/workflows/ci.yml")]:
                with self.subTest(key=key), patch.dict(execution, {key: value}), self.assertRaises(d.DeploymentError):
                    d.verify_record_identity(self.api, REPO, record)
        self.api.post.assert_not_called()

    def test_trusted_bootstrap_proves_candidate_before_executing_it(self):
        from types import SimpleNamespace
        args = SimpleNamespace(workflow="wdpa-monthly-deploy.yml", executor_sha=CURRENT, source_run_id=13, source_run_attempt=2, bootstrap=True)
        env = {"GITHUB_REF": "refs/heads/main", "GITHUB_REPOSITORY": REPO, "GITHUB_RUN_ID": "13", "GITHUB_RUN_ATTEMPT": "2",
               "GITHUB_WORKFLOW_REF": REPO + "/.github/workflows/ci.yml@refs/heads/main", "BOOTSTRAP_WORKFLOW_SHA": OLD}
        with patch.dict(d.os.environ, env), patch.object(d.subprocess, "check_output", return_value=OLD), patch.object(d, "git_ancestor", return_value=True):
            self.assertEqual(d.verify_context(args, self.api), REPO)
            for key, value in [("GITHUB_WORKFLOW_REF", REPO + "/.github/workflows/ci.yml@refs/pull/1/merge"), ("BOOTSTRAP_WORKFLOW_SHA", CURRENT)]:
                with self.subTest(key=key), patch.dict(d.os.environ, {key: value}), self.assertRaises(d.DeploymentError):
                    d.verify_context(args, self.api)
        self.api.post.assert_not_called()

    def test_automatic_caller_cannot_replay_a_different_tested_source_attempt(self):
        from types import SimpleNamespace
        args = SimpleNamespace(workflow="wdpa-monthly-deploy.yml", executor_sha=CURRENT, source_run_id=13, source_run_attempt=2)
        env = {"GITHUB_REF": "refs/heads/main", "GITHUB_REPOSITORY": REPO, "GITHUB_RUN_ID": "13", "GITHUB_RUN_ATTEMPT": "2"}
        with patch.dict(d.os.environ, env), patch.object(d.subprocess, "check_output", return_value=CURRENT), patch.object(d, "git_ancestor", return_value=True):
            self.assertEqual(d.verify_context(args, self.api), REPO)
            for key, value in [("executor_sha", OLD), ("source_run_id", 14), ("source_run_attempt", 1)]:
                changed = SimpleNamespace(**{**vars(args), key: value})
                with self.subTest(key=key), self.assertRaises(d.DeploymentError):
                    d.verify_context(changed, self.api)
        self.api.post.assert_not_called()

    def test_deployment_record_requires_the_original_main_execution(self):
        record = ReplayTests.record(CURRENT)
        def pages(path, field=None):
            return [{"description": "started"}] if "/statuses?" in path else [{"name": "ci-ready", "status": "completed", "conclusion": "success"}]
        self.api.pages.side_effect = pages
        with patch.object(d, "verify_receipt") as receipt:
            self.assertEqual(d.verify_record(self.api, REPO, record)["artifact"], IMAGE)
        receipt.assert_called_once_with(self.api, REPO, record, {"description": "started"})
        for key, value in [("event", "pull_request"), ("workflow_id", 99), ("head_sha", NEW)]:
            def route(path):
                result = self.route(path)
                return {**result, key: value} if path.endswith("/attempts/2") else result
            with self.subTest(key=key), patch.object(self.api, "get", side_effect=route), self.assertRaises(d.DeploymentError):
                d.verify_record(self.api, REPO, record)

    def test_each_terraform_target_binds_its_actual_manual_caller(self):
        for sync, workflow in d.TERRAFORM_SYNCS.items():
            target = "terraform-" + d.hashlib.sha256(sync.encode()).hexdigest()[:16]
            record = ReplayTests.record(CURRENT)
            record["environment"] = "production-" + target
            record["payload"].update(target=target, execution_run_id=14, execution_run_attempt=1)
            execution = {**self.run, "id": 14, "run_attempt": 1, "event": "workflow_dispatch", "head_sha": NEW,
                         "path": ".github/workflows/" + workflow, "workflow_id": 21}
            def route(path):
                if path.endswith("/actions/runs/14/attempts/1"):
                    return execution
                if path.endswith("/actions/workflows/" + workflow):
                    return {"id": 21, "path": ".github/workflows/" + workflow}
                return self.route(path)
            def pages(path, field=None):
                return [{"description": "started"}] if "/statuses?" in path else [{"name": "ci-ready", "status": "completed", "conclusion": "success"}]
            with self.subTest(sync=sync), patch.object(self.api, "get", side_effect=route), patch.object(self.api, "pages", side_effect=pages), patch.object(d, "verify_receipt"):
                self.assertEqual(d.verify_record(self.api, REPO, record)["target"], target)

    def test_unknown_terraform_record_cannot_satisfy_a_noop(self):
        record = ReplayTests.record(CURRENT)
        record["environment"] = "production-terraform-unregistered"
        record["payload"]["target"] = "terraform-unregistered"
        with self.assertRaisesRegex(d.DeploymentError, "unknown deployment target"):
            d.verify_record(self.api, REPO, record)

    def test_fake_success_status_cannot_skip_a_deployment(self):
        record = ReplayTests.record(CURRENT)
        for status in [{"state": "success", "creator": {"login": "human"}},
                       {"state": "success", "creator": {"login": "github-actions[bot]"}, "log_url": "https://github.com/attacker/repo/actions/runs/13"}]:
            with self.subTest(status=status), patch.object(self.api, "pages", return_value=[status]), self.assertRaises(d.DeploymentError):
                d.verified_statuses(self.api, REPO, record)


class VerifiedSupersessionTests(unittest.TestCase):
    setUp = DeploymentProvenanceTests.setUp
    route = DeploymentProvenanceTests.route

    def exercise(self, records, *, changed_source=None, ancestor=None):
        from types import SimpleNamespace
        args = SimpleNamespace(workflow="wdpa-monthly-deploy.yml", target="wdpa-monthly", executor_sha=CURRENT,
                               source_run_id=13, source_run_attempt=2, artifact=IMAGE, plan_json=None)
        newer = {**self.run, "id": 15, "run_attempt": 1, "head_sha": NEW, **(changed_source or {})}
        def route(path):
            if path.endswith("/actions/runs/15/attempts/1"):
                return newer
            return self.route(path)
        def pages(path, field=None):
            if "/deployments?" in path:
                return records
            if path.endswith("/statuses?per_page=100"):
                return [{"state": "failure", "description": "failed"}, {"state": "in_progress", "description": "started"}]
            return [{"name": "ci-ready", "status": "completed", "conclusion": "success"}]
        def ancestry(older, newer):
            if newer == "origin/main":
                return True
            return ReplayTests.ancestor(older, newer) if ancestor is None else ancestor(older, newer)
        env = {"GITHUB_REF": "refs/heads/main", "GITHUB_REPOSITORY": REPO, "GITHUB_RUN_ID": "13", "GITHUB_RUN_ATTEMPT": "2"}
        self.api.get.side_effect = route
        self.api.pages.side_effect = pages
        with patch.dict(d.os.environ, env), patch.object(d.subprocess, "check_output", return_value=CURRENT), patch.object(d, "git_ancestor", side_effect=ancestry), patch.object(d, "verify_receipt"):
            return d.start(args, self.api)

    @staticmethod
    def newer_record():
        record = ReplayTests.record(NEW, 2)
        record["payload"].update(ci_run_id=15, ci_run_attempt=1, execution_run_id=15, execution_run_attempt=1)
        return record

    def test_verified_newer_attempt_skips_before_claim_or_mutation_even_if_newer_failed(self):
        result = self.exercise([self.newer_record()])
        self.assertEqual(result, {"proceed": "false", "outcome": "superseded", "superseded_by_sha": NEW})
        self.api.post.assert_not_called()
        self.assertIn(unittest.mock.call(f"repos/{REPO}/actions/runs/15/attempts/1"), self.api.get.call_args_list)
        self.assertIn(unittest.mock.call(f"repos/{REPO}/actions/runs/15/attempts/1/jobs?per_page=100", "jobs"), self.api.pages.call_args_list)

    def test_forged_newer_source_or_untrusted_record_cannot_be_a_successful_supersession(self):
        for change in ({"head_sha": OLD}, {"workflow_id": 99}, {"event": "pull_request"}, {"repository": {"id": 18, "full_name": REPO}}):
            with self.subTest(change=change), self.assertRaises(d.DeploymentError):
                self.exercise([self.newer_record()], changed_source=change)
        record = self.newer_record()
        record["creator"] = {"login": "human", "type": "User"}
        with self.assertRaises(d.DeploymentError):
            self.exercise([record])
        self.api.post.assert_not_called()

    def test_divergent_or_failed_current_attempt_remains_blocked_without_record_writes(self):
        with self.assertRaisesRegex(d.DeploymentError, "divergent"):
            self.exercise([self.newer_record()], ancestor=lambda *_: False)
        with self.assertRaisesRegex(d.DeploymentError, "reconcile"):
            self.exercise([ReplayTests.record(CURRENT), self.newer_record()])
        self.api.post.assert_not_called()


class ReconciliationTests(unittest.TestCase):
    def test_completed_iam_apply_can_be_reconciled_without_mutation(self):
        import json
        from pathlib import Path
        import tempfile
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as root:
            plan = Path(root) / "plan.json"
            plan.write_text(json.dumps({"resource_changes": []}))
            args = SimpleNamespace(command="reconcile", deployment_id=1, plan_json=str(plan), executor_dir=None)
            record = ReplayTests.record(CURRENT)
            target = "terraform-" + d.hashlib.sha256(b"Cron alert policy sync").hexdigest()[:16]
            record["environment"] = "production-" + target
            record["payload"]["target"] = target
            api = Mock()
            api.pages.return_value = [record]
            api.post.return_value = {"id": 81}
            output = Path(root) / "outputs"
            output.touch()
            with patch.dict(d.os.environ, {"GITHUB_REPOSITORY": REPO, "GITHUB_RUN_ID": "19", "GITHUB_RUN_ATTEMPT": "1", "RUNNER_TEMP": root, "GITHUB_OUTPUT": str(output)}), patch.object(d, "recovery_record", return_value=(record, record["payload"])), patch.object(d.subprocess, "check_output", return_value=CURRENT) as commands:
                d.recover(args, api)
            commands.assert_called_once_with(["git", "rev-parse", "HEAD"], text=True)
            self.assertEqual(api.post.call_args.args[1]["description"], "applied")
            self.assertIn("receipt_path=", output.read_text())

    def test_changed_target_plan_cannot_be_marked_recovered(self):
        import json
        from pathlib import Path
        import tempfile
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as root:
            plan = Path(root) / "plan.json"
            plan.write_text(json.dumps({"resource_changes": [{"address": "module.job", "change": {"actions": ["update"]}}]}))
            args = SimpleNamespace(command="reconcile", deployment_id=1, plan_json=str(plan), executor_dir=None)
            record = ReplayTests.record(CURRENT)
            api = Mock()
            api.pages.return_value = [record]
            with patch.dict(d.os.environ, {"GITHUB_REPOSITORY": REPO}), patch.object(d, "recovery_record", return_value=(record, record["payload"])), patch.object(d.subprocess, "check_output", return_value=CURRENT):
                with self.assertRaisesRegex(d.DeploymentError, "incomplete apply"):
                    d.recover(args, api)
            api.post.assert_not_called()


class PendingDiscoveryBoundaryTests(unittest.TestCase):
    def test_pending_cli_outputs_only_metadata_candidates_without_verification(self):
        import json
        from pathlib import Path
        import tempfile

        for identifiers in ([], [1], [1, 2]):
            with self.subTest(identifiers=identifiers), tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / "output"
                api = Mock()
                records = [ReplayTests.record(CURRENT, identifier) for identifier in identifiers]
                complete = ReplayTests.record(CURRENT, 9)
                def pages(path, field=None):
                    if "/deployments?" in path:
                        return records + [complete]
                    return [{"state": "success", "description": "verified"}] if "/9/" in path else [
                        {"state": "in_progress", "description": "verification_pending"}]
                api.pages.side_effect = pages
                with patch.dict(d.os.environ, {"GITHUB_REPOSITORY": REPO, "GITHUB_OUTPUT": str(output)}), \
                     patch.object(d.sys, "argv", ["deployment_revision.py", "pending"]), \
                     patch.object(d, "GitHub", return_value=api), \
                     patch.object(d.emissions, "verify", side_effect=AssertionError("discovery must remain metadata-only")):
                    d.main()
                self.assertEqual(json.loads(output.read_text().removeprefix("records=")), identifiers)
                api.get.assert_not_called()
                api.post.assert_not_called()

    def test_workflow_keeps_hourly_independent_poll_and_protected_verifier(self):
        from pathlib import Path
        import yaml

        workflow = yaml.safe_load((Path(__file__).resolve().parents[1] / ".github/workflows/deployment-verification.yml").read_text())
        triggers = workflow.get("on", workflow.get(True))
        self.assertEqual(triggers, {"schedule": [{"cron": "17 * * * *"}], "workflow_dispatch": None})
        pending, verify = (workflow["jobs"][name] for name in ("pending", "verify"))
        self.assertNotIn("needs", pending)
        self.assertNotIn("concurrency", pending)
        installer = "python scripts/install_deployment_verifier.py"
        self.assertNotIn(installer, [step.get("run") for step in pending["steps"]])
        self.assertIn(installer, [step.get("run") for step in verify["steps"]])
        self.assertEqual(verify["environment"], "shared-datasets-production")
        self.assertEqual(verify["needs"], "pending")

    def test_observer_rejects_invalid_signature_before_runtime_read_or_status_write(self):
        from types import SimpleNamespace

        fixture = DeploymentProvenanceTests()
        fixture.setUp()
        api = fixture.api
        record = ReplayTests.record(CURRENT)
        def get(path):
            return record if path == f"repos/{REPO}/deployments/1" else fixture.route(path)
        def pages(path, field=None):
            return [{"description": "started"}] if "/statuses?" in path else [
                {"name": "ci-ready", "status": "completed", "conclusion": "success"}]
        api.get.side_effect = get
        api.pages.side_effect = pages
        env = {"GITHUB_REPOSITORY": REPO, "GITHUB_REF": "refs/heads/main",
               "GITHUB_WORKFLOW_REF": f"{REPO}/.github/workflows/deployment-verification.yml@refs/heads/main"}
        with patch.dict(d.os.environ, env), \
             patch.object(d.emissions, "verify", side_effect=d.emissions.EmissionError("invalid signature")), \
             patch.object(d.subprocess, "check_output") as runtime:
            with self.assertRaisesRegex(d.DeploymentError, "invalid signature"):
                d.observe(SimpleNamespace(deployment_id=1), api)
        runtime.assert_not_called()
        api.post.assert_not_called()


def test_paused_scheduler_allows_deployment_but_active_execution_still_defers(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace
    output = tmp_path / "output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    for executions, expected in [([], "true"), ([{"status": {"runningCount": 1}}], "false")]:
        output.write_text("")
        with patch.object(d.subprocess, "check_output", side_effect=[json.dumps(executions), "PAUSED"]):
            d.runtime_window(SimpleNamespace(target="wdpa-monthly", canary_run_date="", allow_cancel="false"))
        assert output.read_text() == "ready=" + expected + "\n"
