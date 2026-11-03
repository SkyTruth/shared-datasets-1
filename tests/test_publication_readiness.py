"""Complete-batch, persisted-boundary and cache invalidation regressions."""

from contextlib import closing
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys

import pytest

from ingestion.common import publication_readiness as r, publication_sources as sources
from ingestion.common.identity_index import DiskIdentityPlan
from scripts import publication_runtime_check, release_feature_model as model


def feature(key):
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [1, 2]},
            "properties": {"key": key}}


def baseline():
    old = feature("old")
    geometry, properties = model.content_hashes(geometry=old["geometry"], properties=old["properties"])
    return model.GeneratedIdentityBaseline(({
        "identity_key": ["old"], "feature_id": "1", "geometry_hash": geometry, "properties_hash": properties,
    },), 2, "2026-01-01", contract_id="test-v1")


def target(slug, calls):
    def prepare(directory):
        calls.append(slug)
        with closing(DiskIdentityPlan(directory / "identity.sqlite")) as plan:
            plan.add(1, feature("new"), source_fields=("key",))
            plan.db.commit()
        return {"rows": 1}
    return r.Target(slug, "2026-02-01", {"source_sha256": "a" * 64}, prepare, baseline=baseline())


def test_full_runtime_boundary_probe(tmp_path):
    assert publication_runtime_check.check(tmp_path) == {"state": "READY", "assets": 2, "preparations": 2}


def test_fast_gate_rejects_missing_native_fixture_registration(monkeypatch):
    root = Path(__file__).resolve().parents[1]
    publication_runtime_check.check_native_registration(root)
    monkeypatch.setattr(publication_runtime_check, "REQUIRED_TESTS", tuple(
        item for item in publication_runtime_check.REQUIRED_TESTS
        if item[1] != "test_complete_discovery_cli_normalizes_both_realms_and_reuses_spools"))
    with pytest.raises(RuntimeError, match="missing required CI registration"):
        publication_runtime_check.check_native_registration(root)


def test_all_assets_discovered_and_no_ids_allocated_when_first_needs_decision(tmp_path):
    calls = []
    report = r.discover_batch([target("first", calls), target("second", calls)], tmp_path)
    assert report["state"] == "NEEDS_DECISION"
    assert calls == ["first", "second"]
    for result in report["assets"].values():
        assert result["unresolved"] == 1
        with closing(DiskIdentityPlan.open(Path(result["analysis"]) / "identity.sqlite")) as plan:
            assert plan.db.execute("SELECT count(*) FROM planned WHERE feature_id IS NOT NULL").fetchone()[0] == 0
    with pytest.raises(RuntimeError) as caught:
        r.require_ready(report)
    assert len(caught.value.blocked_assets) == 2


def test_broken_asset_does_not_hide_other_asset_decisions(tmp_path):
    calls = []
    def broken(_):
        raise RuntimeError("native normalization broke")
    report = r.discover_batch([replace(target("broken", calls), prepare=broken), target("good", calls)], tmp_path)
    assert report["state"] == "INCOMPLETE"
    assert report["assets"]["good"]["unresolved"] == 1
    assert "native normalization broke" in report["assets"]["broken"]["error"]
    with pytest.raises(RuntimeError, match="did not complete"):
        r.require_ready(report)


@pytest.mark.parametrize("change", ["bytes", "files", "baseline", "source", "engine"])
def test_changed_evidence_or_inputs_cannot_reuse_ready_analysis(tmp_path, monkeypatch, change):
    calls = []
    first = target("example", calls)
    report = r.discover_batch([first], tmp_path)
    retained = Path(report["assets"]["example"]["analysis"])
    if change == "bytes":
        (retained / "identity.sqlite").write_bytes(b"corrupt")
    elif change == "files":
        (retained / "unexpected").write_text("changed")
    elif change == "baseline":
        first = replace(first, baseline=replace(first.baseline, next_feature_id=3))
    elif change == "source":
        first = replace(first, binding={"source_sha256": "b" * 64})
    else:
        monkeypatch.setattr(r, "engine_digest", lambda: "c" * 64)
    result = r.discover_batch([first], tmp_path)
    if change in {"source", "engine", "baseline"}:
        assert len(calls) == 2
        assert not result["assets"]["example"]["reused"]
    else:
        assert result["state"] == "INCOMPLETE"


def test_stage_restore_preserves_exact_analysis_and_rejects_corruption(tmp_path):
    calls = []
    report = r.discover_batch([target("example", calls)], tmp_path / "original")
    objects = {}
    def upload(name, path):
        raw = path.read_bytes()
        objects[name] = raw
        return {"uri": name, "generation": 123, "size": len(raw), "sha256": model.sha256_hex(raw)}
    reference = r.stage_retained(report, upload)
    def download(ref, path):
        assert ref["generation"] == 123
        path.write_bytes(objects[ref["uri"]])
    restored = tmp_path / "restored"
    r.restore_retained(reference, restored, download)
    result = r.discover_batch([target("example", calls)], restored)
    assert result["assets"]["example"]["reused"]
    assert calls == ["example"]
    objects[next(iter(objects))] += b"corrupt"
    with pytest.raises(ValueError, match="checksum or size"):
        r.restore_retained(reference, tmp_path / "corrupt", download)


def test_review_capacity_failure_retains_complete_analysis_without_ready_claim(tmp_path):
    def capacity(*_):
        raise RuntimeError("bounded review capacity exceeded")
    report = r.discover_batch([target("example", [])], tmp_path / "analysis", on_pending=capacity)
    assert report["state"] == "INCOMPLETE"
    assert report["assets"]["example"]["analysis_complete"]
    objects = []
    reference = r.stage_retained(report, lambda name, path: objects.append(name) or {
        "uri": name, "generation": 1, "size": path.stat().st_size, "sha256": model.sha256_hex(path.read_bytes())})
    assert reference["discovery_state"] == "INCOMPLETE" and "example" in reference["assets"]
    assert any(name.endswith("analysis-identity.sqlite") for name in objects)
    with pytest.raises(RuntimeError, match="did not complete"):
        r.require_ready(report)


def config_for_file(tmp_path, strategy="source_field"):
    source = tmp_path / "input.csv"
    source.write_text("key,name\n1,A\n2,B\n")
    return {"release": "2026-02-01", "work_dir": str(tmp_path / "analysis"), "assets": [{
        "asset_slug": "table-example", "source": str(source), "source_sha256": sources.input_digest(source),
        "format": "csv", "identity_strategy": strategy, "source_fields": ["key"], "expected_rows": 2,
    }]}


def test_real_cli_handles_table_and_nonfeature_assets_and_reuses_analysis(tmp_path):
    config = config_for_file(tmp_path)
    raster = tmp_path / "source.tif"
    raster.write_bytes(b"source bytes; raster validity remains a separate build check")
    config["assets"].append({"asset_slug": "raster-example", "source": str(raster),
        "source_sha256": sources.input_digest(raster), "format": "cog", "identity_strategy": "not_applicable",
        "identity_not_applicable_reason": "Raster has no feature rows"})
    config_path = tmp_path / "plan.json"
    config_path.write_text(json.dumps(config))
    command = [sys.executable, "scripts/publication_readiness.py", "--plan", str(config_path)]
    completed = subprocess.run(command, text=True, capture_output=True)
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["expected_assets"] == ["table-example", "raster-example"]
    result = sources.run(config)
    assert all(item["reused"] for item in result["assets"].values())
    config["assets"][0]["expected_rows"] = 3
    result = sources.run(config)
    assert result["state"] == "INCOMPLETE"
    assert result["assets"]["raster-example"]["state"] == "READY"


def test_invalid_first_source_does_not_hide_second_source(tmp_path):
    config = config_for_file(tmp_path)
    config["assets"].append({**config["assets"][0], "asset_slug": "second"})
    config["assets"][0]["source_sha256"] = "0" * 64
    result = sources.run(config)
    assert result["state"] == "INCOMPLETE"
    assert result["assets"]["second"]["state"] == "READY"


def test_source_field_duplicates_and_undecided_policy_block_before_build(tmp_path):
    config = config_for_file(tmp_path)
    source = Path(config["assets"][0]["source"])
    source.write_text("key,name\n1,A\n1,B\n")
    config["assets"][0]["source_sha256"] = sources.input_digest(source)
    assert sources.run(config)["state"] == "INCOMPLETE"
    config["assets"][0]["identity_strategy"] = "not_applicable"
    config["assets"][0]["identity_not_applicable_reason"] = "must not bypass table identity"
    assert sources.run(config)["state"] == "INCOMPLETE"
    source.write_text("key,name\n1,A\n2,B\n")
    config["assets"][0]["source_sha256"] = sources.input_digest(source)
    config["assets"][0]["identity_strategy"] = "source_field"
    config["assets"][0]["blockers"] = ["source license requires a decision"]
    assert sources.run(config)["state"] == "BLOCKED"


def test_zarr_source_digest_covers_all_members_and_paths(tmp_path):
    directory = tmp_path / "source.zarr"
    directory.mkdir()
    (directory / "zarr.json").write_text("{}")
    first = sources.input_digest(directory)
    (directory / "chunk").write_bytes(b"data")
    assert sources.input_digest(directory) != first


def test_restore_rejects_paths_outside_analysis(tmp_path):
    with pytest.raises(ValueError, match="escapes"):
        r.restore_retained({"schema": r.SCHEMA, "assets": {"example": {
            "binding": {"asset_slug": "example", "engine": r.engine_digest()},
            "files": {"../../escape": {}},
        }}}, tmp_path, lambda *_: pytest.fail("must reject before download"))


def test_new_generated_table_uses_explicit_genesis_without_allocating(tmp_path):
    config = config_for_file(tmp_path, "generated")
    config["assets"][0]["baseline"] = {"genesis": True, "contract_id": "approved-test-v1"}
    report = sources.run(config)
    assert report["state"] == "READY"
    path = Path(report["assets"]["table-example"]["analysis"])
    with closing(DiskIdentityPlan.open(path / "identity.sqlite")) as plan:
        assert len(plan) == 2
        assert plan.db.execute("SELECT count(*) FROM planned WHERE feature_id IS NOT NULL").fetchone()[0] == 0


def test_competing_reuse_decisions_fail_discovery_before_allocation(tmp_path):
    def prepare(directory):
        with closing(DiskIdentityPlan(directory / "identity.sqlite")) as plan:
            for index, key in enumerate(("new-a", "new-b"), 1):
                plan.add(index, feature(key), source_fields=("key",))
            plan.db.commit()
        return {"rows": 2}
    unit = replace(target("example", []), prepare=prepare)
    pending = r.discover_batch([unit], tmp_path)
    decisions = []
    for line in Path(pending["assets"]["example"]["cases"]).read_text().splitlines():
        case = json.loads(line)
        case.pop("ambiguity_type")
        decisions.append({**case, "release": unit.release, "action": "reuse_previous_feature_id",
            "reuse_feature_id": "1", "rationale": "competing fixture choices",
            "reviewer": "test", "pr_reference": "test"})
    report = r.discover_batch([replace(unit, decisions=tuple(decisions))], tmp_path)
    assert report["state"] == "INCOMPLETE"
    assert "multiple" in report["assets"]["example"]["error"]


def test_manual_concierge_rejects_missing_or_stale_readiness_before_build(tmp_path):
    from scripts import publishing_concierge as concierge
    config = config_for_file(tmp_path)
    state = {"source": config["assets"][0]["source"], "plan": {
        "asset_slug": "table-example", "release_date": config["release"], "canonical_format": "csv"},
        "steps": {"profile-fields": {"status": "completed", "evidence": {
            "feature_id_decision": "source-field", "feature_id_fields": ["key"]}}}}
    with pytest.raises(concierge.WorkflowError, match="readiness_report"):
        concierge.validate_build_artifacts(state, {"artifacts": []})
    sources.run(config)
    evidence = {"readiness_report": str(Path(config["work_dir"]) / "readiness.json")}
    assert concierge.validate_decision_readiness(state, evidence) == evidence
    Path(state["source"]).write_text("key,name\n3,Changed\n")
    with pytest.raises(concierge.WorkflowError, match="source changed"):
        concierge.validate_decision_readiness(state, evidence)


@pytest.mark.parametrize("changed", ["decisions", "plan"])
def test_real_cli_ready_report_rejects_changed_review_inputs(tmp_path, changed):
    config = config_for_file(tmp_path, "generated")
    config["assets"][0]["baseline"] = {"genesis": True, "contract_id": "test-v1"}
    decisions = tmp_path / "decisions.json"
    decisions.write_text(json.dumps({"schema_version": 1, "asset_slug": "table-example", "decisions": []}))
    config["assets"][0]["decisions"] = str(decisions)
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps(config))
    completed = subprocess.run([sys.executable, "scripts/publication_readiness.py", "--plan", str(plan)],
        text=True, capture_output=True)
    assert completed.returncode == 0, completed.stderr
    report_path = Path(config["work_dir"]) / "readiness.json"
    kwargs = {"asset_slug": "table-example", "source_sha256": config["assets"][0]["source_sha256"]}
    r.validate_ready_report(report_path, **kwargs)
    if changed == "decisions":
        decisions.write_text(decisions.read_text() + "\n")
    else:
        config["assets"][0]["source_fields"] = ["name"]
        plan.write_text(json.dumps(config))
    with pytest.raises(ValueError, match="review input changed"):
        r.validate_ready_report(report_path, **kwargs)
    if changed == "decisions":
        completed = subprocess.run([sys.executable, "scripts/publication_readiness.py", "--plan", str(plan)],
            text=True, capture_output=True)
        assert completed.returncode == 0, completed.stderr
        assert json.loads(completed.stdout)["assets"]["table-example"]["reused"]
        r.validate_ready_report(report_path, **kwargs)


def test_missing_input_invalidates_old_ready_and_continues_other_assets(tmp_path):
    config = config_for_file(tmp_path)
    assert sources.run(config)["state"] == "READY"
    config["assets"].append({**config["assets"][0], "asset_slug": "second"})
    del config["assets"][0]["source_sha256"]
    report = sources.run(config)
    assert report["state"] == "INCOMPLETE"
    assert report["assets"]["second"]["state"] == "READY"
    assert json.loads((Path(config["work_dir"]) / "readiness.json").read_text())["state"] == "INCOMPLETE"


def test_baseline_records_change_selects_new_analysis_even_without_snapshot(tmp_path):
    calls = []
    unit = target("example", calls)
    assert r.discover_batch([unit], tmp_path)["state"] == "NEEDS_DECISION"
    updated = replace(unit.baseline, records=({**unit.baseline.records[0], "geometry_hash": "0" * 64},))
    report = r.discover_batch([replace(unit, baseline=updated)], tmp_path)
    assert report["state"] == "READY"
    assert not report["assets"]["example"]["reused"]
    assert calls == ["example", "example"]


def test_manual_identity_profile_change_invalidates_ready(tmp_path):
    from scripts import publishing_concierge as concierge
    config = config_for_file(tmp_path)
    assert sources.run(config)["state"] == "READY"
    state = {"source": config["assets"][0]["source"], "plan": {
        "asset_slug": "table-example", "release_date": config["release"], "canonical_format": "csv"},
        "steps": {"profile-fields": {"status": "completed", "evidence": {
            "feature_id_decision": "source-field", "feature_id_fields": ["name"]}}}}
    with pytest.raises(concierge.WorkflowError, match="identity contract"):
        concierge.validate_decision_readiness(state,
            {"readiness_report": str(Path(config["work_dir"]) / "readiness.json")})


def test_feature_publication_rejects_nonfeature_readiness(tmp_path):
    from scripts import publishing_concierge as concierge
    config = config_for_file(tmp_path, "not_applicable")
    config["assets"][0].update(format="cog", identity_not_applicable_reason="Incorrect declaration")
    assert sources.run(config)["state"] == "READY"
    state = {"source": config["assets"][0]["source"], "plan": {
        "asset_slug": "table-example", "release_date": config["release"], "canonical_format": "csv"},
        "steps": {"profile-fields": {"status": "completed", "evidence": {
            "feature_id_decision": "source-field", "feature_id_fields": ["key"]}}}}
    with pytest.raises(concierge.WorkflowError, match="identity contract"):
        concierge.validate_decision_readiness(state,
            {"readiness_report": str(Path(config["work_dir"]) / "readiness.json")})


def test_nonfeature_strategy_requires_explicit_nonfeature_format(tmp_path):
    config = config_for_file(tmp_path, "not_applicable")
    config["assets"][0].pop("format")
    config["assets"][0]["identity_not_applicable_reason"] = "Omitted format cannot bypass identity"
    assert sources.run(config)["state"] == "INCOMPLETE"


@pytest.mark.parametrize("format_name", ["cog", "zarr"])
def test_nonfeature_concierge_requires_readiness_without_feature_profile(tmp_path, format_name):
    from scripts import publishing_concierge as concierge
    config = config_for_file(tmp_path, "not_applicable")
    config["assets"][0].update(format=format_name, identity_not_applicable_reason="No feature rows")
    assert sources.run(config)["state"] == "READY"
    state = {"source": config["assets"][0]["source"], "plan": {
        "asset_slug": "table-example", "release_date": config["release"], "canonical_format": format_name},
        "steps": {}}
    steps = {step.step_id: step for step in concierge.STEP_DEFINITIONS}
    assert not steps["profile-fields"].is_required(state)
    assert steps["decision-readiness"].is_required(state)
    concierge.validate_decision_readiness(state,
        {"readiness_report": str(Path(config["work_dir"]) / "readiness.json")})


def test_resolution_file_filters_decisions_to_requested_release(tmp_path):
    config = config_for_file(tmp_path, "generated")
    config["assets"][0]["baseline"] = {"genesis": True, "contract_id": "test-v1"}
    decisions = tmp_path / "decisions.json"
    decisions.write_text(json.dumps({"schema_version": 1, "asset_slug": "table-example", "decisions": [
        {"release": "2026-01-01", "action": "assign_new_feature_id"}]}))
    config["assets"][0]["decisions"] = str(decisions)
    assert sources.run(config)["state"] == "READY"
