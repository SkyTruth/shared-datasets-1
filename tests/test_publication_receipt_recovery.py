"""Receipt repair consumes real serialized provenance; no data/Slack replay exists."""
from copy import deepcopy
import json
from unittest import mock

import pytest

from scripts import dataset_mutation_authorization as auth
from scripts import publication_receipt_recovery as recovery
from test_dataset_mutation_authorization import FakeGitHub, REPO, EXECUTOR, HEAD, MERGE


BODY = ("<!-- shared-datasets-breaking-alert:live:publish:demo:cc0b2e6c8feebc61 -->\n"
        "Live breaking change Slack alert sent for `demo` (publish).")


def log(body=BODY, pr=7):
    command = ["gh", "api", f"repos/{REPO['full_name']}/issues/{pr}/comments", "-f", f"body={body}"]
    return ("gh: Resource not accessible by integration (HTTP 403)\n"
            "    post_pr_comment(pr_number, body)\n"
            f"subprocess.CalledProcessError: Command '{command}' returned non-zero exit status 1.\n")


class PublicationAPI(FakeGitHub):
    def __init__(self):
        super().__init__()
        self.run["conclusion"] = "failure"
        envelope = {
            "authorization_version": 2, "repository": REPO, "pr_number": 7,
            "head_sha": HEAD, "merge_sha": MERGE, "trusted_executor_sha": EXECUTOR,
            "source_run": {key: self.run[key] for key in ("id", "run_attempt", "workflow_id", "path", "head_sha", "event")},
            "outcome": "mutation", "document": self.doc, "plan_path": self.path, "plan_blob_sha": self.blob,
            "normalized_plan_sha256": auth.plans.sha256(self.raw),
            "acceptance": {"kind": "review", "review_id": 10, "reviewed_commit": HEAD},
        }
        envelope.update(auth.identity_digests(envelope))
        self.set_artifact(envelope)
        self.job = {"id": 900, "name": "Publish PR #7 / Apply approved PR mutation plans (PR #7)",
                    "status": "completed", "conclusion": "failure", "steps": [
                        {"name": name, "status": "completed", "conclusion": "success"} for name in (
                            "Promote approved staged objects", "Finalize promoted release metadata",
                            "Rebuild promoted release index", "Record completed publication")]
                    + [{"name": "Send breaking change alert", "status": "completed", "conclusion": "failure"}]}
        self.comments = []

    def get(self, path):
        if path == f"repos/{REPO['full_name']}":
            return deepcopy(REPO)
        if path.endswith("/actions/runs/700"):
            return deepcopy(self.run)
        return super().get(path)

    def pages(self, path, *, field=None):
        if field == "jobs":
            return [deepcopy(self.job)]
        if "/comments?" in path:
            return deepcopy(self.comments)
        return super().pages(path, field=field)


def test_original_failure_repairs_only_the_receipt_and_reuses_its_exact_marker():
    api = PublicationAPI()

    def transport(command, **kwargs):
        if command[-1].endswith("/logs"):
            return log()
        assert command[:4] == ["gh", "api", "repos/SkyTruth/shared-datasets-1/issues/7/comments", "-f"]
        body = command[4].removeprefix("body=")
        assert body.startswith(BODY + "\n\n")
        return json.dumps({"id": 500, "body": body, "user": {"login": "github-actions[bot]"}})

    with mock.patch.object(recovery.subprocess, "check_output", side_effect=transport) as send:
        report = recovery.repair(api, 7, 700, 1)
    assert report["receipt_comment_id"] == 500 and report["source_job_id"] == 900
    assert send.call_count == 2  # One log GET and one PR receipt, no other mutation.


@pytest.mark.parametrize("comments", [[{"id": 500, "body": BODY, "user": {"login": "github-actions[bot]"}}],
                                      [{"id": 501, "body": BODY, "user": {"login": "jonaraphael"}}]])
def test_existing_receipt_never_replays_slack_or_posts_another_comment(comments):
    api = PublicationAPI()
    api.comments = comments
    with mock.patch.object(recovery.subprocess, "check_output", return_value=log()) as send:
        report = recovery.repair(api, 7, 700, 1)
    assert report["receipt_comment_id"] == comments[0]["id"] and send.call_count == 1


@pytest.mark.parametrize("change", ["wrong-pr", "new-attempt", "partial", "other-failure", "forged-plan", "expired-artifact", "unapproved"])
def test_ineligible_or_unauthorized_publication_fails_before_any_write(change):
    api = PublicationAPI()
    pr = 7
    if change == "wrong-pr":
        pr = 8
    elif change == "new-attempt":
        api.run["run_attempt"] = 2
    elif change == "partial":
        api.job["steps"][1]["conclusion"] = "failure"
    elif change == "other-failure":
        api.job["steps"].append({"name": "Delete promoted scratch source objects", "conclusion": "failure"})
    elif change == "forged-plan":
        api.raw += b" "
    elif change == "expired-artifact":
        api.artifact["expired"] = True
    elif change == "unapproved":
        api.reviews[0]["state"] = "CHANGES_REQUESTED"
    with mock.patch.object(recovery.subprocess, "check_output") as send:
        with pytest.raises(auth.Error):
            recovery.repair(api, pr, 700, 1)
    send.assert_not_called()


@pytest.mark.parametrize("text", ["", log(pr=8), log(body=BODY.replace("demo", "other")), log() + log(),
                                    log() + "Slack notification skipped: timeout", log().replace("HTTP 403", "HTTP 500")])
def test_uncertain_slack_and_other_failures_cannot_be_acknowledged(text):
    with pytest.raises(auth.Error):
        recovery.receipt_from_log(text, 7, "demo")


@pytest.mark.parametrize("actor,event,ref", [("contributor", "workflow_dispatch", "refs/heads/main"),
    ("jonaraphael", "pull_request", "refs/pull/7/merge"), ("jonaraphael", "workflow_dispatch", "refs/heads/other")])
def test_real_cli_rejects_untrusted_execution_before_any_transport(monkeypatch, actor, event, ref):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_EVENT_NAME", event)
    monkeypatch.setenv("GITHUB_REF", ref)
    monkeypatch.setenv("GITHUB_REPOSITORY", REPO["full_name"])
    monkeypatch.setenv("GITHUB_WORKFLOW_REF", f"{REPO['full_name']}/{auth.WORKFLOW}@refs/heads/main")
    monkeypatch.setenv("GITHUB_ACTOR", actor)
    monkeypatch.setattr("sys.argv", ["publication_receipt_recovery.py", "--pr-number", "7", "--source-run-id", "700", "--source-attempt", "1"])
    with mock.patch.object(recovery.subprocess, "check_output") as transport:
        with pytest.raises(auth.Error):
            recovery.main()
    transport.assert_not_called()
