"""Exercise the probe entrypoint through real catalog generation and outputs."""
import csv
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from google.api_core.exceptions import Forbidden, ServiceUnavailable

from scripts import catalog_drift_guard as guard, catalog_refresh_check as probe, catalog_site
from scripts import operational_retry
from test_catalog_site import DOC


@pytest.fixture
def workspace(tmp_path, monkeypatch):
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
            value.download_as_text.side_effect = lambda **_: objects[name].decode()
            value.download_as_bytes.side_effect = lambda **_: objects[name]
            return value
        client.bucket.return_value.blob.side_effect = blob
        monkeypatch.setattr(guard, "get_client", lambda: client)
        return SimpleNamespace(client=client, objects=objects, live=live, output=output, summary=summary)
    return setup


@pytest.mark.parametrize("count", [0, 1, 2])
def test_unchanged_catalog_writes_false_even_with_a_new_generated_at(workspace, tmp_path, count):
    state = workspace(count)
    probe.main(["--work-dir", str(tmp_path / "probe")])
    assert state.output.read_text() == "changed=false\n"
    assert "unchanged" in state.summary.read_text()


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


@pytest.fixture
def fake_clock(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(operational_retry.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(operational_retry.time, "sleep", lambda delay: now.__setitem__(0, now[0] + delay))
    return now


def test_persistent_transport_outage_exhausts_one_bounded_window(workspace, tmp_path, fake_clock):
    state = workspace(1)
    attempts = []

    def unavailable(*args, **kwargs):
        attempts.append(fake_clock[0])
        raise ServiceUnavailable("unavailable")

    state.client.list_blobs.side_effect = unavailable
    with pytest.raises(ServiceUnavailable):
        probe.main(["--work-dir", str(tmp_path / "probe")])
    assert attempts == [0, 5, 20, 60, 180, 420, 900, 1740]
    assert not state.output.exists() and not state.summary.exists()
    assert list((tmp_path / "probe").iterdir()) == []


def test_permission_failure_is_actionable_without_waiting(workspace, tmp_path, fake_clock):
    state = workspace(1)
    state.client.list_blobs.side_effect = Forbidden("denied")
    with pytest.raises(Forbidden):
        probe.main(["--work-dir", str(tmp_path / "probe")])
    assert state.client.list_blobs.call_count == 1
    assert fake_clock[0] == 0
    assert not state.output.exists()


def test_retry_discards_partial_snapshot_and_validates_complete_new_bytes(workspace, tmp_path, fake_clock, monkeypatch):
    state = workspace(2)
    original_blob = state.client.bucket.return_value.blob.side_effect
    failed = [False]
    collected = []
    original_collect = probe.collect

    def blob(name, generation=None):
        value = original_blob(name, generation)
        if name == "_catalog/releases/example-1.json" and not failed[0]:
            def download(**kwargs):
                failed[0] = True
                raise ServiceUnavailable("download interrupted")
            value.download_as_bytes.side_effect = download
        return value

    def collect(client, bucket_name, destination, **kwargs):
        collected.append(destination)
        assert not destination.exists()
        return original_collect(client, bucket_name, destination, **kwargs)

    state.client.bucket.return_value.blob.side_effect = blob
    monkeypatch.setattr(probe, "collect", collect)
    probe.main(["--work-dir", str(tmp_path / "probe")])
    assert state.output.read_text() == "changed=false\n"
    assert fake_clock[0] == 5 and state.client.list_blobs.call_count == 2
    assert len(set(collected)) == 2 and all(not path.exists() for path in collected)
    assert list((tmp_path / "probe").iterdir()) == []
    for call in state.client.list_blobs.call_args_list:
        assert call.kwargs["retry"] is None
