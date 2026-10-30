"""Exercise real main push comparisons, merged PR coverage and the selector CLI."""

import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.dataset_mutation_authorization import discover_push_selection, discover_push_prs
from scripts import reviewed_dataset_plan as plans


def should_refresh(*args):
    return discover_push_selection(*args)["has_catalog_changes"]


ROOT = Path(__file__).resolve().parents[1]
REPO = {"id": 100, "full_name": "SkyTruth/shared-datasets-1", "default_branch": "main"}
PREFIX = f"repos/{REPO['full_name']}"


class GitHub:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def get(self, path):
        self.calls.append(path)
        return self.responses[path]

    def pages(self, path):
        self.calls.append(path)
        return self.responses[path]


@pytest.fixture
def case(tmp_path, monkeypatch):
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=tmp_path, text=True).strip()

    git("init", "-b", "main")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "Catalog test")
    git("commit", "--allow-empty", "-m", "base")
    before = git("rev-parse", "HEAD")
    monkeypatch.chdir(tmp_path)

    def build(paths=("web/catalog/app.js",), *, mutation=False, count=1):
        if mutation:
            document = plans.normalize_document({"plan_version": 1,
                "finalization_version": plans.FINALIZATION_VERSION,
                "publish": {"asset_slug": "catalog-web", "proposal_id": "test",
                    "promotions": [{"source_uri": "gs://skytruth-shared-datasets-1/_scratch/pending-publishes/catalog-web/test/app.js",
                                    "source_generation": "10", "destination_uri": "gs://skytruth-shared-datasets-1/_catalog/web/app.js",
                                    "destination_generation": "20"}]}})
            raw = plans.canonical_bytes(document)
            plan_path = plans.document_path(document)
            paths = (*paths, plan_path)
        responses = {}
        for number in range(1, count + 1):
            for path in paths:
                target = tmp_path / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(raw if mutation and path == plan_path else str(number).encode())
            git("add", "--all")
            git("commit", "-m", f"merged PR {number}")
            head = git("rev-parse", "HEAD")
            pr = {"number": number, "state": "closed", "merged": True, "merged_at": "2026-10-13T00:00:00Z",
                  "draft": False, "head": {"sha": head, "repo": REPO},
                  "base": {"ref": "main", "repo": REPO}, "merge_commit_sha": head,
                  "changed_files": len(paths), "body": ""}
            responses[f"{PREFIX}/commits/{head}/pulls?per_page=100"] = [pr]
            responses[f"{PREFIX}/pulls/{number}"] = pr
            responses[f"{PREFIX}/pulls/{number}/files?per_page=100"] = [
                {"filename": path, "status": "added", "sha": git("rev-parse", f"HEAD:{path}")}
                for path in paths]
            if mutation:
                blob = hashlib.sha1(f"blob {len(raw)}\0".encode() + raw).hexdigest()
                responses[f"{PREFIX}/git/trees/{head}?recursive=1"] = {"truncated": False,
                    "tree": [{"path": plan_path, "mode": "100644", "type": "blob", "sha": blob}]}
                responses[f"{PREFIX}/git/blobs/{blob}"] = {"encoding": "base64", "content": base64.b64encode(raw).decode(), "sha": blob, "size": len(raw)}
        event = {"repository": REPO, "before": before, "after": head,
                 "ref": "refs/heads/main", "forced": False, "deleted": False}
        env = {"GITHUB_EVENT_NAME": "push", "GITHUB_REF": "refs/heads/main",
               "GITHUB_REPOSITORY": REPO["full_name"], "GITHUB_SHA": head,
               "GITHUB_WORKFLOW_REF": f"{REPO['full_name']}/.github/workflows/ci.yml@refs/heads/main"}
        return GitHub(responses), event, env

    return build


@pytest.mark.parametrize("count", [1, 2])
def test_merged_catalog_prs_refresh_once_after_successful_ci(case, count):
    assert should_refresh(*case(count=count))


@pytest.mark.parametrize("paths", [("docs/ci-preflight.md",), ("web/unrelated/app.js",), ("tests/test_catalog_delivery_selection.py",)])
def test_unrelated_changes_do_not_allocate_publisher(case, paths):
    assert not should_refresh(*case(paths))


def test_publication_owns_the_refresh_for_mixed_ui_and_mutation_pr(case):
    assert not should_refresh(*case(mutation=True))


@pytest.mark.parametrize("path", ["web/catalog/styles.css", "scripts/catalog_site.py",
    "catalog/shared-datasets-catalog.csv", "docs/assets/example.md", "scripts/dataset_mutation_authorization.py"])
def test_changed_bundle_and_catalog_inputs_refresh(case, path):
    assert should_refresh(*case((path,)))


def test_git_filenames_match_nul_delimited_push_contract(case):
    assert should_refresh(*case(("web/catalog/legend-é.svg",)))


@pytest.mark.parametrize("field,value", [("GITHUB_EVENT_NAME", "pull_request"),
    ("GITHUB_REF", "refs/heads/feature"), ("GITHUB_SHA", "b" * 40),
    ("GITHUB_REPOSITORY", "evil/fork"),
    ("GITHUB_WORKFLOW_REF", f"{REPO['full_name']}/.github/workflows/other.yml@refs/heads/main")])
def test_untrusted_push_context_is_rejected(case, field, value):
    args = case()
    args[2][field] = value
    with pytest.raises(ValueError):
        should_refresh(*args)


@pytest.mark.parametrize("field,value", [("forced", True), ("deleted", True),
    ("ref", "refs/heads/feature"), ("after", "b" * 40), ("before", "0" * 40)])
def test_nonordinary_push_is_rejected(case, field, value):
    args = case()
    args[1][field] = value
    with pytest.raises(ValueError):
        should_refresh(*args)


def test_irrelevant_range_does_not_read_github(case):
    api, event, env = case(("docs/ci-preflight.md",))
    api.responses.clear()
    assert not should_refresh(api, event, env)


def test_fork_pr_cannot_cover_catalog_changes(case):
    api, event, env = case()
    api.responses[f"{PREFIX}/pulls/1"]["head"]["repo"] = {"id": 200, "full_name": "evil/fork"}
    with pytest.raises(ValueError, match="merged same-repository PR"):
        should_refresh(api, event, env)


def test_missing_merged_pr_or_unreviewed_catalog_file_is_rejected(case):
    args = case()
    commit_path = f"{PREFIX}/commits/{args[1]['after']}/pulls?per_page=100"
    pr = args[0].responses[commit_path][0]
    args[0].responses[commit_path] = []
    with pytest.raises(ValueError, match="merged same-repository PR"):
        should_refresh(*args)
    args[0].responses[commit_path] = [pr]
    args[0].responses[f"{PREFIX}/pulls/1/files?per_page=100"] = [{"filename": "README.md"}]
    with pytest.raises(ValueError, match="not covered"):
        should_refresh(*args)


def run_discovery_cli(api, event, env, tmp_path):
    fixture_dir = tmp_path / "fixture"
    fixture_dir.mkdir()
    event_path = fixture_dir / "event.json"
    event_path.write_text(json.dumps(event))
    responses = fixture_dir / "responses.json"
    responses.write_text(json.dumps(api.responses))
    gh = fixture_dir / "gh"
    gh.write_text(f"#!{sys.executable}\nimport json,os,sys\nvalue=json.load(open(os.environ['GH_FIXTURE']))[sys.argv[-1]]\nprint(json.dumps([value] if '--slurp' in sys.argv else value))\n")
    gh.chmod(0o755)
    output = fixture_dir / "github-output"
    result = subprocess.run([sys.executable, str(ROOT / "scripts/dataset_mutation_authorization.py"), "discover"],
        env={**os.environ, **env, "PATH": str(fixture_dir) + os.pathsep + os.environ["PATH"],
             "GH_FIXTURE": str(responses), "GITHUB_EVENT_PATH": str(event_path), "GITHUB_OUTPUT": str(output)},
        capture_output=True, text=True)
    return result, dict(line.split("=", 1) for line in output.read_text().splitlines()) if output.exists() else {}


@pytest.mark.parametrize("paths,mutation,expected", [
    (("docs/ci-preflight.md",), False, ([], "false")),
    (("web/catalog/app.js",), False, ([], "true")),
    (("docs/ci-preflight.md",), True, ([1], "false")),
    (("web/catalog/app.js",), True, ([1], "false")),
])
def test_real_cli_consumes_main_push_and_writes_workflow_output(case, tmp_path, paths, mutation, expected):
    result, fields = run_discovery_cli(*case(paths, mutation=mutation), tmp_path)
    assert result.returncode == 0, result.stderr
    assert fields == {"pr_numbers": json.dumps(expected[0], separators=(",", ":")),
                      "has_mutations": str(bool(expected[0])).lower(), "has_catalog_changes": expected[1]}


@pytest.mark.parametrize("remove_plan", [False, True])
def test_real_cli_mutation_owns_refresh_despite_uncovered_catalog_and_intermediate_plan(case, tmp_path, remove_plan):
    api, event, env = case(mutation=True)
    plan_path = next(tmp_path.glob(".github/dataset-plans/**/*.json"))
    if remove_plan:
        plan_path.unlink()
    target = tmp_path / "web/catalog/uncovered.js"
    target.write_text("unreviewed catalog change covered by mutation refresh")
    subprocess.run(["git", "add", "--all"], check=True)
    subprocess.run(["git", "commit", "-m", "later endpoint change"], check=True, capture_output=True)
    after = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    event["after"] = env["GITHUB_SHA"] = after
    api.responses[f"{PREFIX}/commits/{after}/pulls?per_page=100"] = []
    result, fields = run_discovery_cli(api, event, env, tmp_path)
    assert result.returncode == 0, result.stderr
    assert fields == {"pr_numbers": "[1]", "has_mutations": "true", "has_catalog_changes": "false"}


def test_real_cli_uncovered_catalog_fails_without_partial_outputs(case, tmp_path):
    api, event, env = case()
    api.responses[f"{PREFIX}/pulls/1/files?per_page=100"] = [{"filename": "README.md"}]
    result, fields = run_discovery_cli(api, event, env, tmp_path)
    assert result.returncode != 0
    assert "not covered" in result.stderr
    assert fields == {}


def test_catalog_selection_reads_each_pr_file_list_and_identity_once(case):
    api, event, env = case(count=2)
    assert should_refresh(api, event, env)
    for number in (1, 2):
        assert api.calls.count(f"{PREFIX}/pulls/{number}") == 1
        assert api.calls.count(f"{PREFIX}/pulls/{number}/files?per_page=100") == 1
    # Before unification, the catalog selector read each PR identity twice.
    assert len(api.calls) == 6


def test_authorization_capture_membership_still_enumerates_non_catalog_pr_without_plan_reads(case):
    api, event, env = case(("docs/ci-preflight.md",))
    del api.responses[f"{PREFIX}/pulls/1/files?per_page=100"]
    assert discover_push_prs(api, event, env) == [1]
    assert api.calls == [f"{PREFIX}/commits/{event['after']}/pulls?per_page=100", f"{PREFIX}/pulls/1"]
