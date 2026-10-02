import copy
import hashlib
import json
from dataclasses import replace

import pytest

from scripts import compare_releases as compare
from scripts import release_feature_model as model
from tests.comparison_fixtures import bundle, record, generated

A, B = "2026-01-01", "2026-02-01"


def run(tmp_path, a, b, **kwargs):
    engine = compare.Comparison(tmp_path / "index", **kwargs)
    engine.run(a[0], b[0], a[1], b[1])
    return engine


def test_complete_mutually_exclusive_classifications_and_inspection(tmp_path):
    old = [record(i, A, name=f"record-{i}") for i in range(1, 7)]
    new = [record(i, B, name=f"record-{i}") for i in range(2, 8)]
    new[0]["geometry_hash"] = model.geometry_hash(
        {"type": "Point", "coordinates": [10, 0]}
    )
    new[1]["properties"]["name"] = "changed"
    new[1]["properties_hash"] = model.properties_hash(new[1]["properties"])
    new[2]["geometry_hash"] = model.geometry_hash(
        {"type": "Point", "coordinates": [11, 0]}
    )
    new[2]["properties"]["name"] = "also changed"
    new[2]["properties_hash"] = model.properties_hash(new[2]["properties"])
    engine = run(
        tmp_path,
        bundle(tmp_path / "old", A, old),
        bundle(tmp_path / "new", B, new, generation=20),
    )
    assert engine.summary["counts"] == dict(
        added=1, removed=1, geometry_only=1, properties_only=1, both=1, unchanged=2
    )
    assert [
        (r["feature_id"], r["classification"])
        for r in engine.page(query="also")["rows"]
    ] == [("4", "both")]
    assert engine.page(limit=2, offset=2)["total"] == 7
    assert engine.inspect("3")["property_changes"] == [
        {
            "field": "name",
            "before": {"present": True, "value": "record-3"},
            "after": {"present": True, "value": "changed"},
        }
    ]
    engine.export(tmp_path / "report.json")
    report = json.loads((tmp_path / "report.json").read_text())
    assert len(report["features"]) == 7
    assert report["summary"]["inputs"]["baseline"]["files"]["manifest"]["generation"]


def test_absent_null_schema_only_and_provenance_are_distinct(tmp_path):
    fields = [
        {"name": "feature_id", "type": "string", "nullable": False},
        {"name": "name", "type": "string"},
        {"name": "optional", "type": "string"},
    ]
    old = [record(1, A), record(2, A)]
    new = [record(1, B, extra={"optional": None}), record(2, B)]
    new[1]["provenance"] = {"provider": "Changed provenance"}
    engine = run(
        tmp_path,
        bundle(tmp_path / "a", A, old),
        bundle(tmp_path / "b", B, new, fields=fields),
    )
    assert engine.summary["counts"]["properties_only"] == 1
    assert engine.summary["counts"]["unchanged"] == 1
    assert engine.inspect("1")["property_changes"] == [
        {
            "field": "optional",
            "before": {"present": False},
            "after": {"present": True, "value": None},
        }
    ]
    assert engine.summary["schema_changes"]["added"][0]["name"] == "optional"


def test_schema_datatype_change_without_value_change(tmp_path):
    old, new = [record(1, A)], [record(1, B)]
    fields = [
        {"name": "feature_id", "type": "string", "nullable": False},
        {"name": "name", "type": "json"},
    ]
    engine = run(
        tmp_path,
        bundle(tmp_path / "a", A, old),
        bundle(tmp_path / "b", B, new, fields=fields),
    )
    assert engine.summary["counts"]["unchanged"] == 1
    assert engine.summary["schema_changes"]["datatype_changes"] == [
        {"field": "name", "before": "string", "after": "json"}
    ]


def test_order_is_irrelevant_and_same_date_generations_change_key(tmp_path):
    records = [record(i, A) for i in range(1, 5)]
    a = bundle(tmp_path / "a", A, records)
    b = bundle(tmp_path / "b", A, list(reversed(records)), generation=50)
    one = run(tmp_path / "one", a, b)
    two = run(tmp_path / "two", a, bundle(tmp_path / "c", A, records, generation=50))
    assert one.page() == two.page()
    assert one.summary["counts"] == two.summary["counts"]
    # There is intentionally no result cache; input identities still distinguish corrections.
    changed = copy.deepcopy(a[0])
    changed["files"]["manifest"]["generation"] = "999"
    three = run(tmp_path / "three", (changed, a[1]), b)
    assert one.summary["input_key"] != three.summary["input_key"]


@pytest.mark.parametrize(
    "mutation",
    [
        "duplicate",
        "bad_hash",
        "wrong_asset",
        "extra_field",
        "wrong_hash",
        "wrong_source_id",
        "identity_duplicate",
    ],
)
def test_malformed_complete_inputs_rejected(tmp_path, mutation):
    records = [record(1, A), record(2, A)]
    if mutation == "duplicate":
        records.append(records[0])
    if mutation == "bad_hash":
        records[0]["geometry_hash"] = "bad"
    if mutation == "wrong_asset":
        records[0]["asset_slug"] = "other"
    if mutation == "extra_field":
        records[0]["properties"]["extra"] = 2
    if mutation == "wrong_hash":
        records[0]["properties"]["name"] = "incorrect"
    if mutation == "wrong_source_id":
        records[0]["properties"]["feature_id"] = "other"
    if mutation == "identity_duplicate":
        records[1]["geometry_hash"] = records[0]["geometry_hash"]
    with pytest.raises((compare.ComparisonError, model.ReleaseFeatureModelError)):
        run(
            tmp_path,
            bundle(tmp_path / "a", A, records),
            bundle(tmp_path / "b", B, [record(1, B)]),
        )


@pytest.mark.parametrize(
    "kind",
    [
        "generated_reset",
        "legacy_generated",
        "source_field_schema",
        "source_field_name",
        "missing_identity",
    ],
)
def test_incompatible_identity_withholds_feature_results(tmp_path, kind):
    identity_a = generated() if "generated" in kind else None
    identity_b = (
        generated("contract-v2")
        if kind == "generated_reset"
        else copy.deepcopy(identity_a)
    )
    if kind == "legacy_generated":
        for key in (
            "contract_id",
            "sequence_state_version",
            "next_generated_feature_id_before_release",
        ):
            identity_b.pop(key)
    fields = None
    if kind == "source_field_schema":
        fields = [
            {"name": "feature_id", "type": "integer"},
            {"name": "name", "type": "string"},
        ]
    if kind == "source_field_name":
        identity_b = model.build_identity_metadata(
            strategy="source_field", source_fields=["name"]
        )
    new = record(1, B, name="1" if kind == "source_field_name" else "value")
    a = bundle(tmp_path / "a", A, [record(1, A)], identity=identity_a)
    b = bundle(tmp_path / "b", B, [new], identity=identity_b, fields=fields)
    if kind == "missing_identity":
        manifest = json.loads(b[1]["manifest"].read_text())
        manifest.pop("identity")
        b[1]["manifest"].write_text(json.dumps(manifest))
        b[0]["files"]["manifest"].pop("sha256")
        b[0]["files"]["manifest"].pop("size")
    engine = run(tmp_path, a, b)
    assert not engine.summary["identity"]["compatible"]
    assert engine.summary["counts"] is None
    with pytest.raises(compare.ComparisonError):
        engine.page()
    with pytest.raises(compare.ComparisonError):
        engine.inspect("1")


def test_localized_sidecars_never_used(tmp_path):
    a = bundle(tmp_path / "a", A, [record(1, A)])
    b = bundle(tmp_path / "b", B, [record(1, B)])
    engine = run(tmp_path, a, b)
    assert engine.summary["counts"]["unchanged"] == 1
    b[0]["files"]["metadata"]["path"] = b[0]["files"]["metadata"]["path"].replace(
        ".metadata.", ".metadata.es."
    )
    with pytest.raises(compare.ComparisonError, match="canonical source-language"):
        run(tmp_path / "bad", a, b)


@pytest.mark.parametrize(
    "limit", ["max_rows", "max_input_bytes", "max_expanded_bytes", "max_disk_bytes"]
)
def test_limits_have_no_partial_summary(tmp_path, limit):
    a = bundle(tmp_path / "a", A, [record(1, A), record(2, A)])
    b = bundle(tmp_path / "b", B, [record(1, B)])
    engine = compare.Comparison(
        tmp_path / "index", limits=replace(compare.Limits(), **{limit: 1})
    )
    with pytest.raises(compare.ComparisonLimit):
        engine.run(a[0], b[0], a[1], b[1])
    assert engine.summary is None


def test_cancel_and_checksum_rejection(tmp_path):
    a = bundle(tmp_path / "a", A, [record(1, A)])
    b = bundle(tmp_path / "b", B, [record(1, B)])
    with pytest.raises(compare.ComparisonCancelled):
        run(tmp_path / "cancel", a, b, cancelled=lambda: True)
    a[0]["files"]["manifest"]["sha256"] = "0" * 64
    with pytest.raises(compare.ComparisonError, match="checksum"):
        run(tmp_path, a, b)


def test_union_colors_geometry_membership_separately_from_feature_identity(tmp_path):
    a = bundle(tmp_path / "a", A, [record(1, A, name="before")])
    changed = record(
        2, B, name="after", geometry={"type": "Point", "coordinates": [1, 0]}
    )
    b = bundle(tmp_path / "b", B, [changed])
    engine = run(tmp_path, a, b)
    assert engine.summary["counts"]["added"] == 1
    assert engine.summary["counts"]["removed"] == 1
    assert engine.inspect("1")["map_before"]["change"] == "metadata_changed"
    assert engine.inspect("2")["map_after"]["change"] == "metadata_changed"


def test_generated_source_assignment_key_and_namespace_are_validated(tmp_path):
    identity = model.build_identity_metadata(
        strategy="generated_sequence_source_fields",
        source_fields=["name"],
        contract_id="example-v1",
        next_generated_feature_id_before_release=1,
        next_generated_feature_id_after_release=3,
    )
    old, new = record(1, A, name="stable"), record(1, B, name="stable")
    old["identity_key"] = new["identity_key"] = list(
        model.source_fields_identity_key(old["properties"], ["name"])
    )
    a = bundle(tmp_path / "a", A, [old], identity=identity)
    b = bundle(tmp_path / "b", B, [new], identity=identity)
    assert run(tmp_path, a, b).summary["counts"]["unchanged"] == 1
    new["identity_key"] = ["wrong"]
    with pytest.raises(compare.ComparisonError, match="assignment key"):
        run(tmp_path / "bad", a, bundle(tmp_path / "c", B, [new], identity=identity))


def test_generated_content_edit_is_add_remove_not_an_invented_match(tmp_path):
    a = bundle(tmp_path / "a", A, [record(1, A, name="old")], identity=generated())
    b = bundle(
        tmp_path / "b",
        B,
        [record(2, B, name="new", geometry={"type": "Point", "coordinates": [1, 0]})],
        identity=generated(),
    )
    engine = run(tmp_path, a, b)
    assert engine.summary["counts"]["added"] == engine.summary["counts"]["removed"] == 1
    assert engine.inspect("2")["map_after"]["change"] == "metadata_changed"


@pytest.mark.parametrize("action", ["reuse", "force_new"])
def test_generated_comparison_honors_producer_reviewed_identity_decisions(
    tmp_path, action
):
    old = record(1, A, name="stable")
    new = record(1, B, name="changed" if action == "reuse" else "stable")
    key = model.identity_key_from_record(new)
    baseline = model.GeneratedIdentityBaseline(
        records=(old,), next_feature_id=10, release=A, contract_id="contract-v1"
    )
    allocation = model.assign_generated_feature_ids(
        [key],
        baseline=baseline,
        feature_id_overrides={key: "1"} if action == "reuse" else {},
        force_new_identity_keys=[key] if action == "force_new" else [],
    )
    new["feature_id"] = allocation.ids_by_key[key]
    identity = model.build_identity_metadata(
        strategy="generated_sequence_content_hash",
        contract_id="contract-v1",
        previous_release=A,
        next_generated_feature_id_before_release=10,
        next_generated_feature_id_after_release=allocation.next_feature_id,
    )
    engine = run(
        tmp_path,
        bundle(tmp_path / "a", A, [old], identity=generated()),
        bundle(tmp_path / "b", B, [new], identity=identity),
    )
    if action == "reuse":
        assert engine.summary["counts"]["properties_only"] == 1
    else:
        assert (
            engine.summary["counts"]["added"]
            == engine.summary["counts"]["removed"]
            == 1
        )


def test_generated_id_cannot_exceed_manifest_allocation_state(tmp_path):
    a = bundle(tmp_path / "a", A, [record(1, A)], identity=generated())
    b = bundle(tmp_path / "b", B, [record(10, B)], identity=generated())
    with pytest.raises(compare.ComparisonError, match="allocation state"):
        run(tmp_path, a, b)


def test_inputs_and_workspace_cannot_change_asset_or_reuse_an_index(tmp_path):
    a = bundle(tmp_path / "a", A, [record(1, A)])
    b = bundle(tmp_path / "b", B, [record(1, B)])
    run(tmp_path, a, b)
    with pytest.raises(compare.ComparisonError, match="fresh work directory"):
        run(tmp_path, a, b)
    altered = copy.deepcopy(a[0])
    altered["files"]["pmtiles"]["path"] = altered["files"]["pmtiles"]["path"].replace(
        "example.pmtiles", "other.pmtiles"
    )
    with pytest.raises(compare.ComparisonError, match="Map input"):
        compare.snapshot(altered)


def test_cli_input_boundaries_report_errors_without_partial_outputs(tmp_path, capsys):
    a = bundle(tmp_path / "a", A, [record(1, A)])
    b = bundle(tmp_path / "b", B, [record(1, B)])
    invalid = copy.deepcopy(a[0])
    invalid["release"] = "2026-02-30"
    with pytest.raises(compare.ComparisonError, match="calendar date"):
        compare.snapshot(invalid)
    with pytest.raises(compare.ComparisonError, match="descriptor"):
        compare.artifact([])
    before, after, output = (
        tmp_path / f"{name}.json" for name in ("before", "after", "report")
    )
    before.write_text(json.dumps({**a[0], "local_paths": {"metadata": None}}))
    after.write_text(
        json.dumps(
            {**b[0], "local_paths": {role: str(path) for role, path in b[1].items()}}
        )
    )
    assert (
        compare.main(
            [
                "--baseline",
                str(before),
                "--target",
                str(after),
                "--output",
                str(output),
                "--work-dir",
                str(tmp_path / "cli-index"),
            ]
        )
        == 2
    )
    assert "error:" in capsys.readouterr().err
    assert not output.exists()


def test_time_limit_and_json_boundaries(tmp_path):
    engine = compare.Comparison(tmp_path / "time")
    engine.started -= engine.limits.max_seconds + 1
    with pytest.raises(compare.ComparisonLimit, match="time budget"):
        engine.check()
    for payload in ('{"feature_id":"1","feature_id":"2"}', '{"name":NaN}', "[]"):
        with pytest.raises(compare.ComparisonError):
            compare.read_json(payload)


def test_schema_changes_include_nonprojectable_fields(tmp_path):
    a = bundle(tmp_path / "a", A, [record(1, A)])
    fields = [
        {"name": "feature_id", "type": "string", "nullable": False},
        {"name": "name", "type": "string"},
        {"name": "hidden", "type": "string", "projectable": False},
    ]
    b = bundle(tmp_path / "b", B, [record(1, B)], fields=fields)
    assert run(tmp_path, a, b).summary["schema_changes"]["added"][0]["name"] == "hidden"


def test_geometry_colors_survive_generated_id_reset_without_matching_ids(tmp_path):
    a = bundle(tmp_path / "a", A, [record(1, A)], identity=generated("before"))
    b = bundle(
        tmp_path / "b",
        B,
        [record(1, B, geometry={"type": "Point", "coordinates": [9, 0]})],
        identity=generated("after"),
    )
    engine = run(tmp_path, a, b)
    assert engine.summary["counts"] is None
    assert engine.summary["geometry_counts"] == {
        "novel": 1,
        "removed": 1,
        "metadata_changed": 0,
        "unchanged": 0,
    }
    assert engine.map_features("baseline", ["1"]) == [
        {"feature_id": "1", "change": "removed"}
    ]
    assert engine.map_features("target", ["1"]) == [
        {"feature_id": "1", "change": "novel"}
    ]
    for side, ids in [
        ("latest", ["1"]),
        ([], ["1"]),
        ("target", []),
        ("target", ["1"] * 201),
        ("target", [1]),
        ("target", ["1", "1"]),
        ("target", ["2"]),
    ]:
        with pytest.raises(compare.ComparisonError):
            engine.map_features(side, ids)


def test_historical_coral_geometry_uses_full_precision_without_joining_ids(tmp_path):
    from tests.comparison_fixtures import historical_bundle

    old, geom = historical_bundle(tmp_path / "before", A)
    new = record(1, B, geometry=geom, name="reef")
    new["properties"] = {"name": "reef"}
    new["properties_hash"] = model.properties_hash(new["properties"])
    engine = run(
        tmp_path,
        old,
        bundle(
            tmp_path / "after",
            B,
            [new],
            identity=generated(),
            fields=[{"name": "name", "type": "string"}],
        ),
    )
    assert engine.summary["geometry_counts"] == dict(
        novel=0, removed=0, metadata_changed=0, unchanged=1
    )
    assert engine.summary["counts"] is None
    assert engine.map_features("baseline", ["gen:coral-example"]) == [
        {"feature_id": "gen:coral-example", "change": "unchanged"}
    ]


@pytest.mark.parametrize(
    "failure", ["checksum", "truncated", "properties", "budget", "missing"]
)
def test_historical_geometry_boundary_rejects_incomplete_or_unpinned_bytes(
    tmp_path, failure
):
    from tests.comparison_fixtures import historical_bundle

    a, _ = historical_bundle(tmp_path / "a", A)
    b = bundle(tmp_path / "b", B, [record(1, B)])
    limits = compare.Limits()
    if failure == "checksum":
        # Pin intact bytes against an incorrect declared digest, exercising the
        # final stream checksum rather than failing early during FGB decoding.
        a[0]["files"]["fgb"]["sha256"] = "0" * 64
        manifest = json.loads(a[1]["manifest"].read_text())
        for item in manifest["artifacts"]:
            if item["role"] == "fgb":
                item["sha256"] = "0" * 64
        a[1]["manifest"].write_text(json.dumps(manifest))
        a[0]["files"]["manifest"].update(
            size=a[1]["manifest"].stat().st_size,
            sha256=hashlib.sha256(a[1]["manifest"].read_bytes()).hexdigest(),
        )
    elif failure == "truncated":
        a[1]["fgb"].write_bytes(a[1]["fgb"].read_bytes()[:-8])
    elif failure == "properties":
        import gzip

        with gzip.open(a[1]["metadata"], "rt") as stream:
            value = json.load(stream)
        value["properties"]["name"] = "altered"
        with gzip.open(a[1]["metadata"], "wt") as stream:
            stream.write(json.dumps(value))
        evidence = {
            "size": a[1]["metadata"].stat().st_size,
            "sha256": hashlib.sha256(a[1]["metadata"].read_bytes()).hexdigest(),
        }
        a[0]["files"]["metadata"].update(evidence)
        manifest = json.loads(a[1]["manifest"].read_text())
        for item in manifest["artifacts"]:
            if item["role"] == "metadata":
                item.update(evidence)
        a[1]["manifest"].write_text(json.dumps(manifest))
        a[0]["files"]["manifest"].update(
            size=a[1]["manifest"].stat().st_size,
            sha256=hashlib.sha256(a[1]["manifest"].read_bytes()).hexdigest(),
        )
    elif failure == "budget":
        limits = replace(limits, max_geometry_bytes=1)
    else:
        a[0]["files"].pop("fgb")
    expected = {
        "checksum": "checksum",
        "truncated": "truncated",
        "properties": "metadata differs",
        "budget": "streaming geometry budget",
        "missing": "exact canonical FGB",
    }
    with pytest.raises(compare.ComparisonError, match=expected[failure]):
        run(tmp_path, a, b, limits=limits)


def test_historical_identity_cannot_claim_modern_id_continuity():
    fields = {
        "feature_id": model.ReleaseSchemaField("feature_id", "string", False, True)
    }
    modern = {
        "release_feature_model_schema_version": 2,
        "identity": generated(),
        "hashes": {
            "geometry_hash_algorithm": model.GEOMETRY_HASH_ALGORITHM,
            "properties_hash_algorithm": model.PROPERTIES_HASH_ALGORITHM,
        },
    }
    old = {**modern, "release_feature_model_schema_version": 1}
    assert not compare.identity_compatibility(old, modern, fields, fields)["compatible"]
