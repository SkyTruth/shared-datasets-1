from __future__ import annotations

import base64
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import zipfile

from scripts import dataset_mutation_authorization as auth
from scripts import reviewed_dataset_plan as plans

REPO = {"id": 100, "full_name": "SkyTruth/shared-datasets-1", "default_branch": "main"}
HEAD, MERGE, EXECUTOR = "a" * 40, "b" * 40, "c" * 40


def document():
    return plans.normalize_document(
        {
            "plan_version": 1,
            "finalization_version": plans.FINALIZATION_VERSION,
            "publish": {
                "asset_slug": "demo",
                "proposal_id": "pr-7",
                "promotions": [
                    {
                        "source_uri": "gs://skytruth-shared-datasets-1/_scratch/pending-publishes/demo/pr-7/demo.csv",
                        "source_generation": "10",
                        "destination_uri": "gs://skytruth-shared-datasets-1/100-geographic-reference/110-boundaries/demo/latest/demo.csv",
                        "destination_generation": "20",
                    }
                ],
            },
        }
    )


def review(state="APPROVED", *, identifier=10, head=HEAD):
    return {
        "id": identifier,
        "state": state,
        "user": {"login": "jonaraphael"},
        "commit_id": head,
        "submitted_at": "2026-09-22T10:00:00Z",
        "pull_request_url": "https://api.github.com/repos/SkyTruth/shared-datasets-1/pulls/7",
    }


class FakeGitHub:
    def __init__(self):
        self.doc = document()
        self.raw = plans.canonical_bytes(self.doc)
        self.blob = hashlib.sha1(f"blob {len(self.raw)}\0".encode() + self.raw).hexdigest()
        self.path = plans.document_path(self.doc)
        self.pr = {
            "number": 7,
            "state": "closed",
            "merged": True,
            "merged_at": "2026-09-22T11:00:00Z",
            "draft": False,
            "head": {"sha": HEAD, "repo": REPO},
            "base": {"ref": "main", "repo": REPO},
            "merge_commit_sha": MERGE,
            "user": {"login": "contributor"},
            "body": plans.render_document(self.doc),
            "changed_files": 1,
        }
        self.reviews = [review()]
        self.files = [{"filename": self.path, "status": "added", "sha": self.blob}]
        # Real closed-PR runs use source branch/head metadata (e.g. repo run 35806651282).
        self.run = {
            "id": 700,
            "run_attempt": 1,
            "workflow_id": 99,
            "path": auth.WORKFLOW,
            "head_sha": HEAD,
            "head_branch": "feature/demo",
            "event": "pull_request",
            "repository": REPO,
            "head_repository": REPO,
            "status": "completed",
            "conclusion": "success",
        }
        self.workflow = {"id": 99, "path": auth.WORKFLOW}
        self.tree = {
            "truncated": False,
            "tree": [{"path": self.path, "type": "blob", "mode": "100644", "sha": self.blob}],
        }
        self.compare = {"status": "ahead", "merge_base_commit": {"sha": MERGE}}
        self.overrides = {}
        self.calls = []
        self.artifact = None

    def get(self, path):
        self.calls.append(path)
        if path in self.overrides:
            return deepcopy(self.overrides[path])
        if path.endswith("/pulls/7"):
            return deepcopy(self.pr)
        if "/compare/" in path:
            return deepcopy(self.compare)
        if "/git/trees/" in path:
            return deepcopy(self.tree)
        if "/git/blobs/" in path:
            return {
                "sha": self.blob,
                "encoding": "base64",
                "size": len(self.raw),
                "content": base64.b64encode(self.raw).decode(),
            }
        if "/attempts/" in path:
            return deepcopy(self.run)
        if path.endswith("/workflows/publish-dataset.yml"):
            return deepcopy(self.workflow)
        if path.endswith("/artifacts?per_page=100"):
            return {"total_count": 1, "artifacts": [deepcopy(self.artifact)]}
        raise AssertionError(path)

    def pages(self, path, *, field=None):
        self.calls.append(path)
        if field == "artifacts":
            return [deepcopy(self.artifact)]
        return deepcopy(self.reviews if "/reviews?" in path else self.files)

    def archive(self, repository, artifact_id):
        assert repository == REPO["full_name"] and artifact_id == self.artifact["id"]
        return self.archive_bytes

    def set_artifact(self, envelope, *, extra=False):
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w") as archive:
            archive.writestr(auth.ENVELOPE_FILE, plans.canonical_bytes(envelope))
            if extra:
                archive.writestr("../extra", "untrusted")
        self.archive_bytes = data.getvalue()
        self.artifact = {
            "id": 70,
            "name": auth.artifact_name(700, 1),
            "expired": False,
            "workflow_run": {"id": 700},
            "digest": "sha256:" + plans.sha256(self.archive_bytes),
            "size_in_bytes": len(self.archive_bytes),
        }


def context(api):
    return {
        "repository": deepcopy(REPO),
        "action": "closed",
        "pull_request": deepcopy(api.pr),
    }, {
        "GITHUB_EVENT_NAME": "pull_request",
        "GITHUB_REPOSITORY": REPO["full_name"],
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_WORKFLOW_REF": f"{REPO['full_name']}/{auth.WORKFLOW}@refs/heads/main",
        "GITHUB_WORKFLOW_SHA": EXECUTOR,
        "GITHUB_SHA": MERGE,
        "GITHUB_RUN_ID": "700",
        "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_ACTOR": "contributor",
    }


class AuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.api = FakeGitHub()
        self.event, self.env = context(self.api)

    def capture(self):
        return auth.capture(self.api, self.event, self.env)

    def test_valid_source_branch_pr_run_binds_head_merge_executor_and_bytes(self):
        envelope = self.capture()
        self.assertEqual(envelope["source_run"]["head_sha"], HEAD)
        self.assertEqual(envelope["merge_sha"], MERGE)
        self.assertEqual(envelope["trusted_executor_sha"], EXECUTOR)
        self.assertEqual(envelope["document"], self.api.doc)
        auth.revalidate(self.api, envelope)

    def test_stale_revoked_dismissed_and_wrong_association_reviews_fail(self):
        cases = [
            [review(head="d" * 40)],
            [review(), review("CHANGES_REQUESTED", identifier=11)],
            [review(), review("DISMISSED", identifier=11)],
            [review("COMMENTED")],
            [review("PENDING")],
            [review(), review()],
            [
                {
                    **review(),
                    "pull_request_url": "https://api.github.com/repos/other/repo/pulls/7",
                }
            ],
            [{**review(), "submitted_at": "bad"}],
            [{**review(), "state": "UNKNOWN"}],
        ]
        for reviews in cases:
            with self.subTest(reviews=reviews), self.assertRaises(auth.Error):
                self.api.reviews = reviews
                self.capture()

    def test_latest_decisive_review_is_order_independent_comments_do_not_revoke(self):
        self.api.reviews = [
            review("COMMENTED", identifier=14),
            review(identifier=13),
            review("CHANGES_REQUESTED", identifier=12),
        ]
        self.assertEqual(self.capture()["acceptance"]["review_id"], 13)

    def test_later_page_review_is_not_lost(self):
        self.api.reviews = [{**review("COMMENTED", identifier=i), "user": {"login": "other"}} for i in range(1, 101)]
        self.api.reviews += [review(), review("CHANGES_REQUESTED", identifier=101)]
        # Remove duplicate ID from the earlier comments, then prove later decisive rejection.
        self.api.reviews[9]["id"] = 500
        with self.assertRaisesRegex(auth.Error, "latest effective"):
            self.capture()

    def test_paginated_transport_preserves_all_pages_and_fails_bad_shapes(self):
        pages = [[review()], [review("CHANGES_REQUESTED", identifier=11)]]
        with mock.patch.object(auth.subprocess, "check_output", return_value=json.dumps(pages).encode()) as command:
            self.assertEqual(len(auth.GitHub().pages("endpoint")), 2)
            self.assertIn("--paginate", command.call_args.args[0])
            self.assertIn("--slurp", command.call_args.args[0])
        for raw in (b"{}", b"[]", b"[[null]]"):
            with (
                mock.patch.object(auth.subprocess, "check_output", return_value=raw),
                self.assertRaises(auth.Error),
            ):
                auth.GitHub().pages("endpoint")

    def test_self_authored_exception_does_not_bypass_merge_or_repository(self):
        self.api.pr["user"]["login"] = "jonaraphael"
        self.api.reviews = []
        self.assertEqual(self.capture()["acceptance"]["kind"], "self_authored_merge")
        self.api.pr["merged"] = False
        with self.assertRaises(auth.Error):
            self.capture()

    def test_dispatch_requires_merged_same_repo_and_current_acceptance(self):
        self.event["inputs"] = {"pr_number": "7"}
        self.env.update(
            GITHUB_EVENT_NAME="workflow_dispatch",
            GITHUB_ACTOR="jonaraphael",
            GITHUB_SHA=EXECUTOR,
        )
        self.api.run.update(event="workflow_dispatch", head_branch="main", head_sha=EXECUTOR)
        self.assertEqual(self.capture()["pr_number"], 7)
        self.api.pr.update(state="open", merged=False)
        with self.assertRaises(auth.Error):
            self.capture()
        self.api.pr.update(state="closed", merged=True)
        self.api.reviews = [review("CHANGES_REQUESTED")]
        with self.assertRaises(auth.Error):
            self.capture()
        self.env["GITHUB_ACTOR"] = "other"
        with self.assertRaises(auth.Error):
            self.capture()

    def test_wrong_pr_context_and_lineage_fail(self):
        for mutate in (
            lambda api: api.pr.update(number=8),
            lambda api: api.pr.update(draft=True),
            lambda api: api.pr["head"].update(repo={**REPO, "id": 200}),
            lambda api: api.pr["base"].update(ref="other"),
            lambda api: api.pr.update(merge_commit_sha="d" * 40),
            lambda api: api.compare.update(status="diverged"),
            lambda api: api.pr.update(state="closed", merged=False),
        ):
            with self.subTest(mutate=mutate):
                self.api = FakeGitHub()
                mutate(self.api)
                with self.assertRaises(auth.Error):
                    self.capture()

    def test_document_discovery_refuses_missing_duplicate_modified_truncated_and_symlink(
        self,
    ):
        for mutate in (
            lambda api: api.files.clear(),
            lambda api: api.files.append(deepcopy(api.files[0])),
            lambda api: api.files[0].update(status="modified"),
            lambda api: api.tree.update(truncated=True),
            lambda api: api.tree["tree"][0].update(mode="120000"),
            lambda api: api.files[0].update(sha="d" * 40),
        ):
            with self.subTest(mutate=mutate):
                self.api = FakeGitHub()
                mutate(self.api)
                with self.assertRaises(auth.Error):
                    self.capture()

    def test_head_merge_disagreement_and_corrupt_blob_fail(self):
        key = f"repos/{REPO['full_name']}/git/trees/{MERGE}?recursive=1"
        self.api.overrides[key] = {"truncated": False, "tree": []}
        with self.assertRaises(auth.Error):
            self.capture()
        self.api.overrides.clear()
        self.api.raw += b" "
        with self.assertRaisesRegex(auth.Error, "blob hash"):
            self.capture()

    def test_body_edit_cannot_replace_authority_and_between_job_edit_is_ignored(self):
        envelope = self.capture()
        self.api.pr["body"] = "```shared-datasets-publish-plan\n{}\n```"
        auth.revalidate(self.api, envelope)
        with self.assertRaises(auth.Error):
            self.capture()
        self.api.pr["body"] = plans.render_document(self.api.doc) * 2
        with self.assertRaisesRegex(auth.Error, "multiple"):
            self.capture()

    def test_review_changed_while_queued_blocks_execution(self):
        envelope = self.capture()
        self.api.reviews = [review("DISMISSED")]
        with self.assertRaises(auth.Error):
            auth.revalidate(self.api, envelope)

    def test_envelope_tampering_rejected(self):
        envelope = self.capture()
        for key, value in (
            ("normalized_plan_sha256", "0" * 64),
            ("head_sha", "e" * 40),
            ("proposal_key", "0" * 64),
            ("source_run", {}),
            ("acceptance", {}),
            ("authorization_version", True),
        ):
            with self.subTest(key=key), self.assertRaises((auth.Error, KeyError)):
                auth.validate_envelope({**envelope, key: value})

    def test_localization_exact_upstream_artifact_and_provenance(self):
        envelope = self.capture()
        self.api.set_artifact(envelope)
        event = {"repository": REPO, "workflow_run": deepcopy(self.api.run)}
        self.assertEqual(auth.from_run(self.api, event), envelope)
        for mutate in (
            lambda api: api.artifact.update(expired=True),
            lambda api: api.artifact.update(name="wrong"),
            lambda api: api.artifact.update(digest="sha256:" + "0" * 64),
            lambda api: api.artifact["workflow_run"].update(id=701),
            lambda api: api.run.update(conclusion="failure"),
            lambda api: api.run.update(workflow_id=101),
            lambda api: api.run.update(path=".github/workflows/untrusted.yml"),
            lambda api: api.run.update(head_repository={**REPO, "id": 101}),
            lambda api: api.run.update(run_attempt=2),
            lambda api: api.run.update(head_sha="e" * 40),
        ):
            with self.subTest(mutate=mutate):
                api = FakeGitHub()
                api.set_artifact(envelope)
                mutate(api)
                with self.assertRaises(auth.Error):
                    auth.from_run(api, event)
        self.api.set_artifact(envelope, extra=True)
        with self.assertRaisesRegex(auth.Error, "only authorization"):
            auth.from_run(self.api, event)

    def test_attempt_identity_is_separate_and_executor_change_refuses_resume(self):
        original = self.capture()
        attempt = deepcopy(original)
        attempt["source_run"].update(id=800, run_attempt=2)
        self.assertEqual(auth.identity_digests(original), auth.identity_digests(attempt))
        auth.require_same_execution_contract(original, attempt)
        attempt["trusted_executor_sha"] = "d" * 40
        self.assertEqual(
            auth.identity_digests(original)["proposal_key"],
            auth.identity_digests(attempt)["proposal_key"],
        )
        self.assertNotEqual(
            auth.identity_digests(original)["execution_contract_sha256"],
            auth.identity_digests(attempt)["execution_contract_sha256"],
        )
        with self.assertRaisesRegex(auth.Error, "executor contract changed"):
            auth.require_same_execution_contract(original, attempt)
        self.assertEqual(
            attempt["document"]["publish"]["promotions"][0]["destination_generation"],
            "20",
        )

    def test_cli_verify_requires_outer_hash_exact_run_checkout_and_no_extra_files(self):
        envelope = self.capture()
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / "auth"
            auth.save_envelope(envelope, directory, None)
            raw = (directory / auth.ENVELOPE_FILE).read_bytes()
            argv = [
                "verify",
                "--directory",
                str(directory),
                "--expected-sha256",
                plans.sha256(raw),
                "--output-dir",
                str(Path(tmp) / "out"),
            ]
            with (
                mock.patch.dict(auth.os.environ, self.env, clear=True),
                mock.patch.object(auth, "GitHub", return_value=self.api),
                mock.patch.object(auth.subprocess, "check_output", return_value=EXECUTOR + "\n"),
            ):
                self.assertEqual(auth.main(argv), 0)
                self.assertEqual(
                    plans.strict_json_loads((Path(tmp) / "out/publish-plan.json").read_bytes()),
                    self.api.doc["publish"],
                )
                with mock.patch.dict(auth.os.environ, {"GITHUB_RUN_ID": "999"}):
                    self.assertEqual(auth.main(argv), 2)
                (directory / "extra").write_text("bad")
                self.assertEqual(auth.main(argv), 2)

    def test_no_mutation_result_is_explicit_and_missing_artifact_is_not_noop(self):
        self.api.files = [{"filename": "README.md", "status": "modified"}]
        self.api.pr["body"] = "Ordinary documentation update"
        self.api.reviews = []
        envelope = self.capture()
        self.assertEqual(envelope["outcome"], "no_mutation")
        self.api.set_artifact(envelope)
        event = {"repository": REPO, "workflow_run": deepcopy(self.api.run)}
        self.assertEqual(auth.from_run(self.api, event), envelope)
        self.api.artifact["name"] = "unrelated"
        with self.assertRaisesRegex(auth.Error, "missing/ambiguous"):
            auth.from_run(self.api, event)

    def test_checked_in_shared_fixture_preserves_replay_contract(self):
        fixture = plans.strict_json_loads(
            (Path(__file__).parent / "fixtures/dataset-mutation-authorization-v1.json").read_bytes()
        )
        original, attempt = fixture["original"], fixture["later_attempt"]
        auth.validate_envelope(original)
        auth.validate_envelope(attempt)
        auth.require_same_execution_contract(original, attempt)
        self.assertEqual(auth.identity_digests(original), auth.identity_digests(attempt))
        with self.assertRaisesRegex(auth.Error, "executor contract changed"):
            auth.require_same_execution_contract(original, fixture["incompatible_executor"])

    def test_named_artifact_pagination_requires_complete_consistent_count(self):
        pages = [{"total_count": 2, "artifacts": [{"id": 1}]}, {"total_count": 2, "artifacts": [{"id": 2}]}]
        with mock.patch.object(auth.subprocess, "check_output", return_value=json.dumps(pages).encode()):
            self.assertEqual(auth.GitHub().pages("endpoint", field="artifacts"), [{"id": 1}, {"id": 2}])
        with mock.patch.object(auth.subprocess, "check_output", return_value=json.dumps(pages[:1]).encode()), self.assertRaisesRegex(auth.Error, "incomplete"):
            auth.GitHub().pages("endpoint", field="artifacts")

    def test_cli_capture_produces_typed_handoff_and_refuses_wrong_checkout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            event_path = root / "event.json"
            event_path.write_bytes(plans.canonical_bytes(self.event))
            outputs = root / "outputs"
            argv = ["capture", "--event-path", str(event_path), "--directory", str(root / "artifact"), "--github-output", str(outputs)]
            with mock.patch.dict(auth.os.environ, self.env, clear=True), mock.patch.object(auth, "GitHub", return_value=self.api):
                with mock.patch.object(auth.subprocess, "check_output", return_value=EXECUTOR + "\n"):
                    self.assertEqual(auth.main(argv), 0)
                captured = plans.strict_json_loads((root / "artifact/authorization.json").read_bytes())
                self.assertEqual(captured["document"], self.api.doc)
                self.assertIn("has_publish_plan=true", outputs.read_text())
                with mock.patch.object(auth.subprocess, "check_output", return_value=HEAD + "\n"):
                    self.assertEqual(auth.main(argv), 2)
