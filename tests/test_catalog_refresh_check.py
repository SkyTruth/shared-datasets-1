"""Exercise the probe entrypoint through real catalog generation and outputs."""
import csv
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from scripts import catalog_drift_guard as guard, catalog_refresh_check as probe, catalog_site, deployment_revision, deployment_source
from test_catalog_site import DOC


@pytest.fixture
def main_ci(monkeypatch):
    repository, sha = "SkyTruth/shared-datasets-1", "a" * 40
    monkeypatch.setenv("GITHUB_REPOSITORY", repository)
    monkeypatch.setenv("GITHUB_SHA", sha)
    repo = {"id": 1, "full_name": repository}
    run = {"id": 11, "run_attempt": 1, "workflow_id": 7, "path": ".github/workflows/ci.yml",
           "head_sha": sha, "status": "completed", "event": "push", "head_branch": "main",
           "repository": repo, "head_repository": repo}
    ready = {"name": "ci-ready", "status": "completed", "conclusion": "success"}
    runs = {"total_count": 1, "workflow_runs": [run]}

    def read(path, *, paginate=False):
        prefix = f"repos/{repository}"
        payload = {
            prefix: repo,
            f"{prefix}/actions/workflows/ci.yml": {"id": 7, "path": ".github/workflows/ci.yml"},
            f"{prefix}/actions/runs/11/attempts/1": run,
            f"{prefix}/actions/workflows/ci.yml/runs?head_sha={sha}&event=push&branch=main&per_page=100": runs,
            f"{prefix}/actions/runs/11/attempts/1/jobs?per_page=100": {"total_count": 1, "jobs": [ready]},
        }[path]
        return json.dumps([payload] if paginate else payload).encode()

    transport = Mock(side_effect=read)
    monkeypatch.setattr(deployment_revision, "read", transport)
    return SimpleNamespace(run=run, ready=ready, runs=runs, transport=transport)


@pytest.fixture
def workspace(tmp_path, monkeypatch, main_ci):
    monkeypatch.chdir(tmp_path)
    Path("catalog").mkdir()
    Path("docs/assets").mkdir(parents=True)
    Path("catalog/categories.yaml").write_text(
        "categories:\n  100-geographic-reference:\n    subcategories:\n      110-boundaries: Boundaries\n")
    output, summary = tmp_path / "output", tmp_path / "summary"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))

    def setup(count):
        with Path("catalog/shared-datasets-catalog.csv").open("w") as stream:
            writer = csv.DictWriter(stream, fieldnames=catalog_site.REQUIRED_FIELDS)
            writer.writeheader()
            for number in range(count):
                slug = f"example-{number}"
                Path(f"docs/assets/{slug}.md").write_text(DOC.replace("example-asset", slug))
                writer.writerow({"asset_slug": slug, "title": slug, "category": "100-geographic-reference",
                    "subcategory": "110-boundaries", "status": "active", "access_tier": "public", "owner": "SkyTruth",
                    "update_cadence": "manual", "canonical_format": "fgb", "available_formats": "fgb",
                    "canonical_path": f"gs://{guard.DEFAULT_BUCKET}/100-geographic-reference/110-boundaries/{slug}/latest/{slug}.fgb",
                    "metadata_paths": "README.md", "source": "Example source", "license": "Example license", "citation": "Example citation"})
        baseline = tmp_path / "initial-indexes"
        baseline.mkdir()
        objects = {}
        for number in range(count):
            slug = f"example-{number}"
            release = {"date": "2026-10-18", "files": [{"format": "fgb", "path":
                f"gs://{guard.DEFAULT_BUCKET}/100-geographic-reference/110-boundaries/{slug}/releases/2026-10-18/{slug}.fgb"}]}
            raw = json.dumps({"schema_version": 1, "asset_slug": slug, "releases": [release], "latest_release": release}).encode()
            (baseline / f"{slug}.json").write_bytes(raw)
            objects[f"_catalog/releases/{slug}.json"] = raw
        args = guard.parse_args(["--release-index-dir", str(baseline), "--latest-from-release-index"])
        live = guard.expected_web_payload(args)
        assert len(live["assets"]) == count
        live["generated_at"] = "2020-01-01T00:00:00Z"
        objects.update({
            guard.REMOTE_CSV_OBJECT: Path("catalog/shared-datasets-catalog.csv").read_text().encode(),
            guard.REMOTE_WEB_CATALOG_OBJECT: json.dumps(live).encode(),
        })
        client = Mock()
        client.list_blobs.return_value = [SimpleNamespace(name=name, generation=7, size=len(raw))
                                        for name, raw in objects.items() if name.startswith("_catalog/releases/")]
        def blob(name, generation=None):
            value = Mock()
            value.generation, value.updated, value.size = 7, None, len(objects[name])
            value.download_as_text.side_effect = lambda: objects[name].decode()
            value.download_as_bytes.side_effect = lambda **_: objects[name]
            return value
        client.bucket.return_value.blob.side_effect = blob
        monkeypatch.setattr(guard, "get_client", lambda: client)
        return SimpleNamespace(client=client, objects=objects, live=live, output=output, summary=summary)
    return setup


@pytest.mark.parametrize("count", [0, 1, 2])
def test_unchanged_catalog_writes_false_even_with_a_new_generated_at(workspace, tmp_path, count, main_ci):
    state = workspace(count)
    probe.main(["--work-dir", str(tmp_path / "probe")])
    assert state.output.read_text() == "changed=false\n"
    assert "unchanged" in state.summary.read_text()
    main_ci.transport.assert_not_called()


@pytest.mark.parametrize("change", ["metadata", "release", "run", "csv"])
def test_actual_catalog_inputs_select_a_refresh(workspace, tmp_path, change):
    state = workspace(1)
    if change == "csv":
        state.objects[guard.REMOTE_CSV_OBJECT] = b"older CSV"
    elif change == "metadata":
        doc = Path("docs/assets/example-0.md")
        doc.write_text(doc.read_text().replace("Example.\n", "Updated description.\n"))
    else:
        payload = {"schema_version": 1, "asset_slug": "example-0", "updated_at": "2026-10-19T00:00:00Z", "releases": []}
        payload["releases"] = [{"date": "2026-10-19", "files": [{"format": "fgb", "path":
            f"gs://{guard.DEFAULT_BUCKET}/100-geographic-reference/110-boundaries/example-0/releases/2026-10-19/example-0.fgb"}]}]
        payload["latest_release"] = payload["releases"][0]
        if change == "run":
            # Publish the same release first; only the new skipped-run evidence
            # should select a refresh in this case.
            baseline = tmp_path / "baseline"
            baseline.mkdir()
            (baseline / "example-0.json").write_text(json.dumps(payload))
            expected = guard.expected_web_payload(guard.parse_args([
                "--release-index-dir", str(baseline), "--latest-from-release-index"]))
            state.objects[guard.REMOTE_WEB_CATALOG_OBJECT] = json.dumps(expected).encode()
            payload["latest_run"] = {"status": "skipped", "finished_at": "2026-10-19T00:00:00Z"}
        name = "_catalog/releases/example-0.json"
        state.objects[name] = json.dumps(payload).encode()
        state.client.list_blobs.return_value = [SimpleNamespace(name=name, generation=7, size=len(state.objects[name]))]
    probe.main(["--work-dir", str(tmp_path / "probe")])
    assert state.output.read_text() == "changed=true\n"


@pytest.mark.parametrize("status", ["queued", "in_progress", "waiting", "pending", "requested"])
def test_pending_ci_defers_changed_catalog_until_a_later_probe(workspace, tmp_path, main_ci, status):
    state = workspace(1)
    state.objects[guard.REMOTE_CSV_OBJECT] = b"older CSV"
    main_ci.run["status"] = status
    probe.main(["--work-dir", str(tmp_path / "pending")])
    assert not state.output.exists()  # The workflow must not select the publisher.
    assert "deferred" in state.summary.read_text()

    main_ci.run["status"] = "completed"
    probe.main(["--work-dir", str(tmp_path / "ready")])
    assert state.output.read_text() == "changed=true\n"


@pytest.mark.parametrize("failure", ["missing", "failed", "cancelled", "wrong-sha", "wrong-repository",
                                     "unknown-status", "incomplete-list", "api", "failed-and-pending"])
def test_unproven_ci_fails_without_selecting_the_publisher(workspace, tmp_path, main_ci, failure):
    state = workspace(1)
    state.objects[guard.REMOTE_CSV_OBJECT] = b"older CSV"
    if failure == "missing":
        main_ci.runs.update(total_count=0, workflow_runs=[])
    elif failure in {"failed", "cancelled"}:
        main_ci.ready["conclusion"] = "failure" if failure == "failed" else "cancelled"
    elif failure == "wrong-sha":
        main_ci.run.update(head_sha="b" * 40, status="in_progress")
    elif failure == "wrong-repository":
        main_ci.run["head_repository"] = {"id": 2, "full_name": "someone/fork"}
    elif failure == "unknown-status":
        main_ci.run["status"] = "unknown"
    elif failure == "incomplete-list":
        main_ci.runs["total_count"] = 2
    elif failure == "failed-and-pending":
        main_ci.ready["conclusion"] = "failure"
        main_ci.runs["workflow_runs"].append({**main_ci.run, "id": 12, "status": "in_progress"})
        main_ci.runs["total_count"] = 2
    else:
        main_ci.transport.side_effect = RuntimeError("GitHub unavailable")
    with pytest.raises(RuntimeError):
        probe.main(["--work-dir", str(tmp_path / "probe")])
    assert not state.output.exists()
    assert not state.summary.exists()


def test_deployment_cli_still_rejects_pending_ci(main_ci, tmp_path, monkeypatch):
    main_ci.run["status"] = "in_progress"
    event = tmp_path / "event.json"
    event.write_text('{"schedule": "17 * * * *"}')
    output = tmp_path / "output"
    for name, value in {"GITHUB_EVENT_PATH": str(event), "GITHUB_OUTPUT": str(output),
                        "GITHUB_RUN_ID": "12", "GITHUB_RUN_ATTEMPT": "1"}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr("sys.argv", ["deployment_source.py", "--bootstrap", "--workflow",
                                    "catalog-web-deploy.yml", "--executor-sha", main_ci.run["head_sha"]])
    with pytest.raises(SystemExit) as error:
        deployment_source.main()
    assert error.value.code == 1
    assert not output.exists()


@pytest.mark.parametrize("failure", ["listing", "catalog-read", "malformed-catalog", "malformed-index"])
def test_outages_and_invalid_snapshots_never_emit_a_selection(workspace, tmp_path, failure):
    state = workspace(1)
    if failure == "listing":
        state.client.list_blobs.side_effect = RuntimeError("503 UNAVAILABLE")
    elif failure == "catalog-read":
        state.client.bucket.side_effect = RuntimeError("503 UNAVAILABLE")
    elif failure == "malformed-catalog":
        state.objects[guard.REMOTE_WEB_CATALOG_OBJECT] = b"not JSON"
    else:
        state.client.list_blobs.return_value = [SimpleNamespace(name="invalid.json", generation=7, size=2)]
    with pytest.raises((RuntimeError, ValueError)):
        probe.main(["--work-dir", str(tmp_path / "probe")])
    assert not state.output.exists()
    assert not state.summary.exists()
