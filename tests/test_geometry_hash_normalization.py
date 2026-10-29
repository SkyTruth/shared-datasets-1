"""Versioned geometry hashes survive numeric serialization without moving IDs."""
import copy
import hashlib
import json
from types import SimpleNamespace

import pytest

from ingestion.common import feature_metadata
from ingestion.eamlis_monthly import identity as ids, identity_state
from scripts import release_feature_model as model
from tests.test_eamlis_identity import feature, problem


V2 = "sha256:canonical-geometry:v2"


@pytest.mark.parametrize("geometry", [
    None,
    {"type": "LineString", "coordinates": []},
    {"type": "Point", "coordinates": [-81.038888888889, 38]},
    {"type": "Point", "coordinates": [0, -0.0, 38.0]},
    {"type": "GeometryCollection", "geometries": [
        {"type": "Point", "coordinates": [38.0, -80]},
        {"type": "LineString", "coordinates": [[1, 2.0], [1.000000000000001, 3]]},
    ]},
])
def test_v2_hash_survives_json_numeric_spelling_without_rounding(geometry):
    # Model the double coordinates read back from a vector format.
    def doubles(value):
        if isinstance(value, dict):
            return {k: doubles(v) for k, v in value.items()}
        if isinstance(value, list):
            return [doubles(v) for v in value]
        return float(value) if type(value) is int else value

    original = copy.deepcopy(geometry)
    decoded = json.loads(json.dumps(doubles(geometry)))
    assert model.geometry_hash(geometry, algorithm=V2) == model.geometry_hash(decoded, algorithm=V2)
    assert geometry == original


def test_v1_bytes_stay_frozen_and_v2_preserves_actual_coordinate_edits():
    integer = {"type": "Point", "coordinates": [-81.038888888889, 38]}
    decimal = {"type": "Point", "coordinates": [-81.038888888889, 38.0]}
    assert model.geometry_hash(integer) == "sha256:3937d1d63e58dd99d82949b6a8bd87e58ae44b96ca9cbc5abb86ca67a83c9a11"
    assert model.geometry_hash(decimal) == "sha256:125778c755113233a911c22991ab7222a8269e060077bddbf9c5a549dc163818"
    expected = "sha256:" + hashlib.sha256(model.canonical_json(integer).encode()).hexdigest()
    assert model.geometry_hash(decimal, algorithm=V2) == expected
    moved = {**integer, "coordinates": [-81.038888888889, 38.00000000000001]}
    assert model.geometry_hash(moved, algorithm=V2) != expected
    with pytest.raises(model.ReleaseFeatureModelError, match="geometry hash algorithm"):
        model.geometry_hash(integer, algorithm="sha256:unknown")


def test_eamlis_sidecar_round_trip_retains_reserved_ids_with_v2_hashes(tmp_path):
    props = [problem("P1"), problem("P2", area_key="AREA2")]
    ledger = ids.Ledger.genesis().reserve(ids.classify_release(props))
    before = ids.encoded(ledger)
    hashes = []
    for number, coordinates in enumerate([(-81.038888888889, 38), (-81.038888888889, 38.0)]):
        enriched, rows = ids.enrich([feature(p, coordinates) for p in props], ledger=ledger,
            release=f"2026-11-0{number + 1}", asset_slug="eamlis", provenance={})
        path = tmp_path / f"{number}.metadata.ndjson.gz"
        feature_metadata.write_sidecar(rows, path)
        reread = tuple(model.read_metadata_sidecar(path))
        assert [r["feature_id"] for r in reread] == [ledger.ids[ids.classify(p).key] for p in props]
        assert all(r["geometry_hash"] == model.geometry_hash(f["geometry"], algorithm=V2)
                   for r, f in zip(reread, enriched))
        hashes.append([r["geometry_hash"] for r in reread])
    assert hashes[0] == hashes[1]
    assert ids.encoded(ledger) == before


def test_keyed_fingerprint_forces_hash_upgrade_then_ignores_number_spelling(tmp_path):
    from ingestion.common.source_fingerprint import Fingerprint
    from ingestion.eamlis_monthly import run as eamlis
    from tests.test_eamlis_monthly import sample_source_state
    path = tmp_path / "source.geojson"
    csv = tmp_path / "translations.csv"
    csv.write_text("feature_id,field,locale,source_value_hash,value,review_state,notes\n")

    def fingerprint(coordinates, objectid=1):
        path.write_text(json.dumps({"type": "FeatureCollection", "features": [feature(problem(OBJECTID=objectid), coordinates)]}))
        return eamlis.fingerprint_source(sample_source_state(feature_count=1), eamlis.SourceExtract(path, 1, 0),
            csv, identity_contract=ids.CONTRACT)

    original = fingerprint((-81.038888888889, 38))
    assert original.fingerprint["definition"] == "eamlis-keyed-content-v2"
    assert original.fingerprint["inputs"]["geometry_hash_algorithm"] == V2
    old_inputs = dict(original.fingerprint["inputs"])
    old_inputs.pop("geometry_hash_algorithm")
    assert original.fingerprint_hash != Fingerprint("eamlis-keyed-content-v1", old_inputs).sha256
    assert original.fingerprint_hash == fingerprint((-81.038888888889, 38.0), 999).fingerprint_hash
    assert original.fingerprint_hash != fingerprint((-81.038888888889, 38.00000000000001)).fingerprint_hash


@pytest.mark.parametrize("algorithm", [model.GEOMETRY_HASH_ALGORITHM, V2])
def test_final_manifest_preserves_selected_geometry_algorithm(algorithm, tmp_path):
    from tests.test_eamlis_monthly import FakeBucket, FakeClient, fake_asset_output, fixture_fingerprint, sample_source_state
    from ingestion.common.gcs import GcsPublisher
    from ingestion.eamlis_monthly import run as eamlis, identity_state
    import datetime as dt
    bucket = FakeBucket()
    publisher = GcsPublisher(FakeClient(bucket), bucket.name)
    output = fake_asset_output(tmp_path)
    if algorithm == V2:
        values = dict(vars(output))
        ledger = ids.Ledger.genesis()
        output = eamlis.KeyedAssetOutput(**values, reservation=identity_state.Reservation(ledger, {"path": "fixture"}, 1, None))
    eamlis.publish_changed_asset(publisher=publisher, run_date=dt.date(2026, 5, 2),
        source=fixture_fingerprint(sample_source_state()), output=output)
    manifest = json.loads(output.manifest.read_text())
    assert manifest["hashes"]["geometry_hash_algorithm"] == algorithm
    model.validate_release_manifest(manifest, require_generations=True)
    manifest["hashes"]["geometry_hash_algorithm"] = "sha256:unknown"
    with pytest.raises(model.ReleaseFeatureModelError, match="unsupported hash algorithms"):
        model.validate_release_manifest(manifest)


@pytest.mark.parametrize("destinations,rebuild,phase,allowed", [
    (["README.md"], False, "active", True),
    (["README.md"], False, "prepared", False),
    (["README.md"], True, "active", False),
    (["README.md", "latest/example.fgb"], False, "active", False),
    (["latest/README.md"], False, "active", False),
    (["publications/identity-ledgers/new.json"], False, "active", False),
    ([], False, "active", False),
])
def test_reviewed_readme_update_cannot_replace_features_or_identity(destinations, rebuild, phase, allowed):
    guard = SimpleNamespace(state={"identity": {"phase": phase}}, before={"release": {"date": "2026-10-29"}})
    root = f"gs://{identity_state.BUCKET}/{identity_state.ROOT}/"
    plan = {"promotions": [{"destination_uri": root + path} for path in destinations]}
    if rebuild:
        plan["release_index_asset_slugs"] = [identity_state.SLUG]
    if allowed:
        identity_state.require_metadata_only(guard, plan)
    else:
        with pytest.raises(ids.IdentityError):
            identity_state.require_metadata_only(guard, plan)
