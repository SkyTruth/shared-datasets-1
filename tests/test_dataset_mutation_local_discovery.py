"""Real immutable Git histories at the mutation-selection CLI boundary."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile

import pytest

from scripts import dataset_mutation_authorization as auth


class GitHistory:
    def __init__(self, root):
        self.root = root
        self.env = {**os.environ, "GIT_AUTHOR_NAME": "Fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
                    "GIT_COMMITTER_NAME": "Fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
                    "GIT_AUTHOR_DATE": "2026-10-22T16:00:00Z", "GIT_COMMITTER_DATE": "2026-10-22T16:00:00Z",
                    "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}
        self.git("init", "--quiet", "--initial-branch=main")

    def git(self, *args, body=None):
        return subprocess.check_output(["git", *args], cwd=self.root, input=body, env=self.env, stderr=subprocess.PIPE)

    def commit(self, files, *parents):
        # Only this disposable test repository has an index or object writes.
        self.git("read-tree", "--empty")
        for name, body in files.items():
            blob = self.git("hash-object", "-w", "--stdin", body=body.encode()).decode().strip()
            self.git("update-index", "--add", "--cacheinfo", "100644", blob, name)
        tree = self.git("write-tree").decode().strip()
        options = [item for parent in parents for item in ("-p", parent)]
        commit = self.git("commit-tree", tree, *options, "-m", "fixture").decode().strip()
        self.git("update-ref", "refs/heads/main", commit)
        return commit


@pytest.fixture
def history(monkeypatch):
    work_root = Path(os.environ.get("SHARED_DATASETS_WORKDIR", Path(tempfile.gettempdir()) / "shared-datasets-1"))
    parent = work_root / "_scratch/skip-unnecessary-github-checks-tests"
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=parent) as temporary:
        result = GitHistory(Path(temporary))
        monkeypatch.chdir(result.root)
        yield result


class UnavailableAPI:
    def __init__(self):
        self.calls = []

    def pages(self, path):
        self.calls.append(path)
        raise subprocess.CalledProcessError(1, ["gh", "api", path], stderr=b"associated PR read unavailable")

    def get(self, path):
        raise AssertionError("no successful PR enumeration can authorize this read")


def context(before, after):
    event = {"repository": {"id": 100, "full_name": "SkyTruth/shared-datasets-1", "default_branch": "main"},
             "ref": "refs/heads/main", "before": before, "after": after, "forced": False, "deleted": False}
    environment = {"GITHUB_REF": "refs/heads/main", "GITHUB_SHA": after, "GITHUB_EVENT_NAME": "push"}
    return event, environment


def discover(api, before, after, *, documents_only=True):
    event, environment = context(before, after)
    return auth.discover_push_prs(api, event, environment, documents_only=documents_only)


def test_code_only_history_needs_no_associated_pr_api(history):
    before = history.commit({"README.md": "before"})
    middle = history.commit({"README.md": "after"}, before)
    after = history.commit({"README.md": "after", "scripts/example.py": "pass"}, middle)
    api = UnavailableAPI()
    assert discover(api, before, after) == []
    assert api.calls == []


def test_real_discovery_cli_serializes_no_mutation_despite_associated_pr_outage(history, monkeypatch, capsys):
    before = history.commit({"README.md": "before"})
    after = history.commit({"README.md": "after"}, before)
    event, environment = context(before, after)
    event_path, outputs = history.root / "event.json", history.root / "outputs"
    event_path.write_text(json.dumps(event))
    for key, value in environment.items():
        monkeypatch.setenv(key, value)
    api = UnavailableAPI()
    monkeypatch.setattr(auth, "GitHub", lambda: api)
    assert auth.main(["discover", "--event-path", str(event_path), "--github-output", str(outputs)]) == 0
    assert outputs.read_text() == "pr_numbers=[]\nhas_mutations=false\n"
    assert api.calls == [] and capsys.readouterr().err == ""


@pytest.mark.parametrize("case", ["added-removed", "modified-reverted", "deleted", "rename-in", "rename-out", "rename-extension",
                                 "merge-resolution", "second-parent", "octopus", "nested-odd-path", "side-root"])
def test_every_candidate_history_keeps_api_failure_visible(history, case):
    plan = ".github/dataset-plans/demo/proposal/plan.json"
    initial = {"README.md": "before"}
    if case in {"modified-reverted", "deleted", "rename-out", "rename-extension"}:
        initial[plan] = "original"
    if case == "rename-in":
        initial["candidate.txt"] = "original"
    before = history.commit(initial)
    if case == "added-removed":
        middle = history.commit({**initial, plan: "temporary"}, before)
        after = history.commit(initial, middle)
    elif case == "modified-reverted":
        middle = history.commit({**initial, plan: "changed"}, before)
        after = history.commit(initial, middle)
    elif case == "deleted":
        after = history.commit({"README.md": "before"}, before)
    elif case in {"rename-out", "rename-extension"}:
        after = history.commit({"README.md": "before", "plan.txt" if case == "rename-out" else plan[:-5] + ".txt": "original"}, before)
    elif case == "rename-in":
        after = history.commit({"README.md": "before", plan: "original"}, before)
    elif case == "nested-odd-path":
        after = history.commit({**initial, ".github/dataset-plans/demo/a\nb\t/odd name.json": "candidate"}, before)
    else:
        left = history.commit({**initial, "left.py": "pass"}, before)
        right_files = {**initial, "right.py": "pass"}
        if case == "second-parent":
            right_files[plan] = "side branch"
        right = history.commit(right_files, *([] if case == "side-root" else [before]))
        parents = [left, right]
        if case == "octopus":
            third = history.commit({**initial, plan: "third parent"}, before)
            parents.append(third)
        merged = {**initial, "left.py": "pass", "right.py": "pass"}
        if case in {"merge-resolution", "side-root"}:
            merged[plan] = "merge-only plan"
        after = history.commit(merged, *parents)
    api = UnavailableAPI()
    with pytest.raises(subprocess.CalledProcessError, match="gh"):
        discover(api, before, after)
    assert api.calls  # Absence was not inferred from the final endpoint tree.


def test_code_only_merge_and_non_json_plan_directory_changes_still_skip_apis(history):
    before = history.commit({"README.md": "before"})
    left = history.commit({"README.md": "before", ".github/dataset-plans/README.md": "guide"}, before)
    right = history.commit({"README.md": "before", ".github/dataset-plans/demo/template.txt": "template"}, before)
    after = history.commit({"README.md": "before", ".github/dataset-plans/README.md": "guide",
                            ".github/dataset-plans/demo/template.txt": "template"}, left, right)
    assert discover(UnavailableAPI(), before, after) == []


def test_merge_delta_against_only_second_parent_is_not_silently_omitted(history):
    plan = ".github/dataset-plans/demo/proposal/plan.json"
    common = history.commit({"README.md": "before"})
    before = history.commit({"README.md": "before", plan: "existing main plan"}, common)
    left = history.commit({"README.md": "before", plan: "existing main plan", "left.py": "pass"}, before)
    right = history.commit({"README.md": "before", "right.py": "pass"}, common)
    after = history.commit({"README.md": "before", plan: "existing main plan", "left.py": "pass", "right.py": "pass"}, left, right)
    # No in-range single-parent commit or endpoint diff changes a plan. Only
    # comparing the merge against its second parent reveals the candidate.
    api = UnavailableAPI()
    with pytest.raises(subprocess.CalledProcessError):
        discover(api, before, after)
    assert api.calls


def test_documents_false_keeps_existing_associated_pr_discovery(history, monkeypatch):
    before = history.commit({"README.md": "before"})
    after = history.commit({"README.md": "after"}, before)
    api, commands = UnavailableAPI(), []
    original = subprocess.check_output
    def output(command, **kwargs):
        commands.append(command)
        return original(command, **kwargs)
    monkeypatch.setattr(auth.subprocess, "check_output", output)
    with pytest.raises(subprocess.CalledProcessError):
        discover(api, before, after, documents_only=False)
    assert api.calls and commands == [["git", "rev-list", "--reverse", f"{before}..{after}"]]


def test_shallow_history_cannot_prove_absence(history, monkeypatch):
    before = history.commit({"README.md": "before"})
    after = history.commit({"README.md": "after"}, before)
    clone = history.root / "shallow"
    history.git("clone", "--quiet", "--depth=1", history.root.as_uri(), str(clone))
    monkeypatch.chdir(clone)
    with pytest.raises(auth.Error, match="non-shallow"):
        discover(UnavailableAPI(), before, after)


def test_missing_tree_object_is_not_an_empty_delta(history):
    before = history.commit({"README.md": "before"})
    middle = history.commit({"README.md": "middle"}, before)
    after = history.commit({"README.md": "after"}, middle)
    tree = history.git("show", "-s", "--format=%T", after).decode().strip()
    (history.root / ".git/objects" / tree[:2] / tree[2:]).unlink()
    api = UnavailableAPI()
    with pytest.raises(subprocess.CalledProcessError):
        discover(api, before, after)
    assert api.calls == []


def test_replace_refs_cannot_hide_a_real_immutable_plan_change(history):
    before = history.commit({"README.md": "before"})
    replacement = history.commit({"README.md": "code only"}, before)
    after = history.commit({"README.md": "before", ".github/dataset-plans/demo/plan.json": "real candidate"}, before)
    history.git("replace", after, replacement)
    api = UnavailableAPI()
    with pytest.raises(subprocess.CalledProcessError):
        discover(api, before, after)
    assert api.calls


@pytest.mark.parametrize("broken", [b"not-nul-terminated", b"README.md\0\0", b"\0"])
def test_malformed_path_enumeration_is_not_absence(history, monkeypatch, broken):
    before = history.commit({"README.md": "before"})
    after = history.commit({"README.md": "after"}, before)
    original = subprocess.check_output
    def output(command, **kwargs):
        return broken if "diff-tree" in command else original(command, **kwargs)
    monkeypatch.setattr(auth.subprocess, "check_output", output)
    with pytest.raises(auth.Error):
        discover(UnavailableAPI(), before, after)


def test_all_commit_ids_are_validated_before_any_early_return_or_api_read(history, monkeypatch):
    before = history.commit({"README.md": "before"})
    after = history.commit({"README.md": "after"}, before)
    original = subprocess.check_output
    def output(command, **kwargs):
        return after + "\ninvalid\n" if "rev-list" in command else original(command, **kwargs)
    monkeypatch.setattr(auth.subprocess, "check_output", output)
    api = UnavailableAPI()
    with pytest.raises(auth.Error, match="full commit SHA"):
        discover(api, before, after)
    assert api.calls == []
