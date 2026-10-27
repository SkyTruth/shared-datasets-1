"""Exact source keys survive renumbering, omissions, edits and reappearance."""
import copy
import json

import pytest

from ingestion.eamlis_monthly import identity as ids


def area(key="AL000133", **changes):
    return {**dict.fromkeys(ids.PROBLEM_FIELDS), "AMLIS_KEY": key, "PROBLEM_KEY": None,
            "OBJECTID": 1, "PA_NAME": "Example area", **changes}


def problem(key="AL0001331", *, area_key="AL000133", **changes):
    return area(area_key, PROBLEM_KEY=key, NUMBER=1, PROB_TY_CD="PW", **changes)


def feature(properties, coordinates=(-87.7, 34.1)):
    return {"type": "Feature", "properties": properties,
            "geometry": {"type": "Point", "coordinates": list(coordinates)}}


def test_typed_keys_keep_area_records_and_colocated_problems_separate():
    rows = ids.classify_release([area("AREA1"), problem("AREA1", area_key="AREA2"), problem("P2", area_key="AREA2")])
    ledger = ids.Ledger.genesis().reserve(rows)
    assert len(ledger.ids) == 3
    assert ledger.ids[("area", "AREA1")] != ledger.ids[("problem", "AREA1")]
    assert ledger == ids.Ledger.genesis().reserve(tuple(reversed(rows)))


@pytest.mark.parametrize("bad", [
    area(NUMBER=0), area(COMP_COST=0), area(STATUS0="unknown"),
    area(PROBLEM_KEY=" "), area(PROBLEM_KEY=123), area(AMLIS_KEY=" A1"),
    {"AMLIS_KEY": "AREA1", "NUMBER": 1}, {"AMLIS_KEY": "AREA1", "PROBLEM_KEY": None},
])
def test_missing_or_invalid_keys_never_use_number_geometry_or_truthiness(bad):
    with pytest.raises(ids.IdentityError):
        ids.classify(bad)


def test_duplicate_keys_and_area_problem_mixture_fail_before_allocation():
    for rows in ([], [problem(), problem()], [area(), problem()]):
        with pytest.raises(ids.IdentityError):
            ids.classify_release(rows)


def test_persisted_ledger_retains_absent_keys_without_recycling_or_renumbering():
    first = ids.Ledger.genesis().reserve(ids.classify_release([problem("P1"), problem("P2")]))
    second = first.reserve(ids.classify_release([problem("P2"), problem("P3")]))
    second.require_extension_of(first)
    reread = ids.Ledger.parse(json.loads(ids.encoded(second)))
    third = reread.reserve(ids.classify_release([problem("P1"), problem("P4")]))
    assert third.ids[("problem", "P1")] == first.ids[("problem", "P1")]
    assert third.ids[("problem", "P4")] == "4"
    assert third.next_feature_id == 5
    assert len(third.entries) == 4
    with pytest.raises(ids.IdentityError, match="remove, renumber"):
        first.require_extension_of(second)


def test_key_reuse_on_another_area_fails_and_leaves_original_ledger_unchanged():
    first = ids.Ledger.genesis().reserve(ids.classify_release([problem()]))
    before = ids.encoded(first)
    with pytest.raises(ids.IdentityError, match="another AMLIS_KEY"):
        first.reserve(ids.classify_release([problem(area_key="NEWAREA")]))
    assert ids.encoded(first) == before


@pytest.mark.parametrize("change", [
    lambda v: v.update(next_feature_id=1),
    lambda v: v.update(contract_id="wrong-contract"),
    lambda v: v["entries"][0].update(feature_id="2"),
    lambda v: v["entries"].append(copy.deepcopy(v["entries"][0])),
])
def test_corrupt_ledger_is_rejected(change):
    value = ids.Ledger.genesis().reserve(ids.classify_release([problem()])).value
    change(value)
    with pytest.raises(ids.IdentityError):
        ids.Ledger.parse(value)


def test_real_metadata_serialization_excludes_objectid_but_preserves_source_edits(tmp_path):
    from ingestion.common import feature_metadata
    from scripts import release_feature_model as model
    original = problem()
    ledger = ids.Ledger.genesis().reserve(ids.classify_release([original]))
    sidecars = []
    for n, props in enumerate([original, {**original, "OBJECTID": 9999}, {**original, "COMP_COST": 500}]):
        enriched, rows = ids.enrich([feature(props)], ledger=ledger, release=f"2026-05-0{n+1}", asset_slug="eamlis", provenance={})
        path = tmp_path / f"{n}.ndjson.gz"
        feature_metadata.write_sidecar(rows, path)
        reread = tuple(model.read_metadata_sidecar(path))
        assert model.validate_sidecar_records(reread).valid
        assert enriched[0]["properties"]["OBJECTID"] == props["OBJECTID"]
        assert reread[0]["identity_key"] == ["problem", original["PROBLEM_KEY"]]
        sidecars.append(reread[0])
    assert len({row["feature_id"] for row in sidecars}) == 1
    assert sidecars[0]["properties_hash"] == sidecars[1]["properties_hash"]
    assert sidecars[1]["properties_hash"] != sidecars[2]["properties_hash"]
    assert sidecars[0]["geometry_hash"] == sidecars[2]["geometry_hash"]


def test_unreserved_keys_and_upstream_derived_column_collisions_cannot_build():
    ledger = ids.Ledger.genesis().reserve(ids.classify_release([problem()]))
    for props in (problem("NEW"), problem(**{ids.KEY_FIELD: "pretend"})):
        with pytest.raises(ids.IdentityError):
            ids.enrich([feature(props)], ledger=ledger, release="2026-05-01", asset_slug="eamlis", provenance={})
