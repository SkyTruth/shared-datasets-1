import json
import hashlib
from unittest.mock import Mock

import pytest

from scripts import dataset_mutation_authorization as auth
from scripts import wdpa_build_authorization as approval
from scripts import wdpa_processing_gate as gate
from tests.test_dataset_mutation_authorization import (
    FakeGitHub,
    HEAD,
    MERGE,
    REPO,
    review,
)
from tests.test_wdpa_execution_observer import accepted_evidence


@pytest.fixture
def approved(tmp_path, monkeypatch):
    evidence = accepted_evidence()
    build = evidence["build"]
    document = {
        "schema_version": 1,
        "kind": "wdpa_owned_build_promotion",
        **{
            key: build[key]
            for key in (
                "artifact_bundle",
                "cloud_execution",
                "cloud_image",
                "image_digest",
                "source_tree_sha256",
                "run_date",
                "assets",
            )
        },
    }
    api = FakeGitHub()
    api.raw = json.dumps(document).encode()
    api.blob = hashlib.sha1(f"blob {len(api.raw)}\0".encode() + api.raw).hexdigest()
    api.path = evidence["promotion_plan"]
    api.files[0].update(filename=api.path, sha=api.blob)
    api.tree["tree"][0].update(path=api.path, sha=api.blob)
    api.pr["body"] = (
        "```shared-datasets-publish-plan\n" + json.dumps(document) + "\n```"
    )
    api.overrides[f"repos/{approval.REPOSITORY}"] = REPO
    path = tmp_path / api.path
    path.parent.mkdir(parents=True)
    path.write_bytes(api.raw)
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    monkeypatch.setattr(gate, "source_digest", lambda: evidence["source_tree_sha256"])
    monkeypatch.setattr(approval.subprocess, "run", Mock())
    return api, evidence, tmp_path


def test_reviewed_bundle_requires_exact_head_and_merge_bytes(approved):
    api, evidence, root = approved
    result = approval.verify(api, evidence, root=root)
    assert result["kind"] == "review" and result["reviewed_commit"] == HEAD
    approval.subprocess.run.assert_called_once_with(
        ["git", "merge-base", "--is-ancestor", MERGE, "HEAD"], cwd=root, check=True
    )
    # The unmanaged object publisher must never execute this owned plan.
    assert (
        auth.discover_document(
            api, api.pr, REPO["full_name"], merged=True, check_body=True
        )
        is None
    )


@pytest.mark.parametrize(
    "defect",
    [
        "open",
        "older_approval",
        "withdrawn",
        "changed_fence",
        "modified_document",
        "changed_merge",
        "changed_current",
    ],
)
def test_a_reviewed_plan_cannot_authorize_changed_or_unaccepted_bytes(approved, defect):
    api, evidence, root = approved
    if defect == "open":
        api.pr.update(state="open", merged=False)
    elif defect == "older_approval":
        api.reviews = [review(head="d" * 40)]
    elif defect == "withdrawn":
        api.reviews.append(review("CHANGES_REQUESTED", identifier=11))
    elif defect == "changed_fence":
        api.pr["body"] = "```shared-datasets-publish-plan\n{}\n```"
    elif defect == "modified_document":
        api.files[0]["status"] = "modified"
    elif defect == "changed_merge":
        api.overrides[f"repos/{REPO['full_name']}/git/trees/{MERGE}?recursive=1"] = {
            "truncated": False,
            "tree": [],
        }
    else:
        (root / api.path).write_bytes(api.raw + b"\n")
    with pytest.raises(auth.Error):
        approval.verify(api, evidence, root=root)
    approval.subprocess.run.assert_not_called()
