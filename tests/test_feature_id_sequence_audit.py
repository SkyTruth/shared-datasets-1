from __future__ import annotations

from copy import deepcopy
import gzip
import json
from pathlib import Path

import pytest

from scripts import feature_id_sequence_audit as audit
from scripts import release_feature_model as model
from test_ingestion_common import GeneratedBaselineLoaderTests


def fixture(tmp_path: Path):
    bucket, asset, manifest, latest, metadata = (
        GeneratedBaselineLoaderTests().setup_baseline()
    )
    root = f"gs://{bucket.name}/asset"
    manifest["identity"].pop("sequence_state_version")
    manifest["identity"].pop("next_generated_feature_id_before_release")
    manifest["identity"]["next_generated_feature_id_after_release"] = (
        2  # old writer regressed
    )
    for artifact in manifest["artifacts"]:
        artifact["path"] = artifact["path"].replace("/asset.", "/test-asset.")
    artifact_bytes = {
        role: f"synthetic {role} artifact".encode()
        for role in ("fgb", "pmtiles", "schema")
    }
    for artifact in manifest["artifacts"]:
        if artifact["role"] in artifact_bytes:
            artifact.update(
                sha256=model.sha256_hex(artifact_bytes[artifact["role"]]), generation=2
            )
    manifest_bytes = audit.json_bytes(manifest)
    objects, events = [], []

    def add(name, data, kind, release, when, generation=1, publication="committed"):
        local = tmp_path / f"object-{len(objects)}"
        local.write_bytes(data)
        item = dict(
            path=f"{root}/{name}",
            generation=generation,
            sha256=model.sha256_hex(data),
            size=len(data),
            local_path=local.name,
            kind=kind,
            release=release,
            publication=publication,
        )
        objects.append(item)
        events.append(
            dict(
                path=item["path"],
                generation=generation,
                operation="write",
                observed_at=when + "T00:00:00Z",
            )
        )
        return {k: item[k] for k in ("path", "generation", "sha256", "size")}

    previous = json.loads(gzip.decompress(metadata.content))
    retired = {
        **previous,
        "release": "2026-04-01",
        "feature_id": "99",
        "identity_key": ["retired"],
    }
    add(
        "releases/2026-04-01/test-asset.metadata.ndjson.gz",
        gzip.compress(json.dumps(retired).encode() + b"\n"),
        "metadata",
        "2026-04-01",
        "2026-04-01",
    )
    metadata_anchor = add(
        "releases/2026-05-01/test-asset.metadata.ndjson.gz",
        metadata.content,
        "metadata",
        "2026-05-01",
        "2026-05-01",
        metadata.generation,
    )
    release_anchor = add(
        "releases/2026-05-01/test-asset.manifest.json",
        manifest_bytes,
        "manifest",
        "2026-05-01",
        "2026-05-01",
    )
    latest_anchor = add(
        "latest/test-asset.manifest.json",
        manifest_bytes,
        "manifest",
        "2026-05-01",
        "2026-05-01",
        10,
    )
    coverage_bytes = b"synthetic authoritative journal export; only a fixture, not real historical proof"
    (tmp_path / "coverage").write_bytes(coverage_bytes)
    # A full release includes the actual FGB/PMTiles/schema write inventory.
    # These synthetic bytes map explicitly to the exact sidecar generation;
    # the fixture coverage export also stands in for reviewed build lineage.
    for role, data in artifact_bytes.items():
        suffix = ".schema.json" if role == "schema" else "." + role
        add(
            "releases/2026-05-01/test-asset" + suffix,
            data,
            "allocation_artifact",
            "2026-05-01",
            "2026-05-01",
            generation=2,
        )
        objects[-1].update(
            allocation_evidence=metadata_anchor,
            mapping_evidence_sha256=model.sha256_hex(coverage_bytes),
        )
    return dict(
        schema_version=1,
        asset_slug=asset.slug,
        asset_root=root,
        objects=objects,
        events=events,
        coverage=dict(
            genesis="2026-04-01T00:00:00Z",
            cutoff="2026-05-01T00:00:00Z",
            evidence=[
                dict(
                    local_path="coverage",
                    size=len(coverage_bytes),
                    sha256=model.sha256_hex(coverage_bytes),
                    provenance="synthetic fixture",
                )
            ],
        ),
        anchor=dict(
            latest_manifest=latest_anchor,
            release_manifest=release_anchor,
            metadata=metadata_anchor,
        ),
    )


def test_audit_uses_historical_deleted_ids_but_never_certifies_completeness(tmp_path):
    inventory = fixture(tmp_path)
    seed = audit.audit(inventory, directory=tmp_path)
    assert seed["status"] == "prepared_for_review"
    assert seed["next_feature_id"] == 100
    assert seed["human_review_requirements"]
    assert "not independently established" in seed["limitation"]
    candidate = audit.prepare_manifest(inventory, seed, directory=tmp_path)
    assert candidate["destination_preconditions"] == [
        inventory["anchor"]["release_manifest"],
        inventory["anchor"]["latest_manifest"],
    ]
    identity = candidate["candidate_manifest"]["identity"]
    assert identity["next_generated_feature_id_after_release"] == 100
    assert identity["next_generated_feature_id_before_release"] == 100
    assert (
        candidate["candidate_manifest"]["artifacts"]
        == json.loads((tmp_path / "object-2").read_bytes())["artifacts"]
    )


def test_inventory_order_is_deterministic_and_seed_cannot_be_edited(tmp_path):
    inventory = fixture(tmp_path)
    seed = audit.audit(inventory, directory=tmp_path)
    reordered = deepcopy(inventory)
    reordered["objects"].reverse()
    reordered["events"].reverse()
    assert audit.audit(reordered, directory=tmp_path) == seed
    seed["next_feature_id"] += 1
    with pytest.raises(audit.SequenceAuditError, match="exact reproducible"):
        audit.prepare_manifest(inventory, seed, directory=tmp_path)


@pytest.mark.parametrize(
    "fault",
    [
        "no_coverage",
        "missing_object",
        "missing_event",
        "wrong_generation",
        "wrong_hash",
        "wrong_anchor",
        "coverage_gap",
        "latest_only",
        "partial_unknown",
        "reuse",
    ],
)
def test_incomplete_or_conflicting_evidence_is_not_ready(tmp_path, fault):
    inventory = fixture(tmp_path)
    if fault == "no_coverage":
        inventory["coverage"]["evidence"] = []
        inventory["complete"] = True
    elif fault == "missing_object":
        inventory["objects"].pop(0)
    elif fault == "missing_event":
        inventory["events"].pop(0)
    elif fault == "wrong_generation":
        inventory["objects"][0]["generation"] = True
    elif fault == "wrong_hash":
        (tmp_path / "object-0").write_bytes(b"truncated")
    elif fault == "wrong_anchor":
        inventory["anchor"]["metadata"]["sha256"] = "0" * 64
    elif fault == "coverage_gap":
        inventory["coverage"]["genesis"] = "2026-03-01"
    elif fault == "latest_only":
        inventory["objects"] = inventory["objects"][1:]
        inventory["events"] = inventory["events"][1:]
    elif fault == "partial_unknown":
        inventory["objects"][0].update(
            kind="unresolved_reservation", publication="partial"
        )
    elif fault == "reuse":
        path = tmp_path / "object-0"
        record = json.loads(gzip.decompress(path.read_bytes()))
        record["feature_id"] = "1"
        data = gzip.compress(json.dumps(record).encode() + b"\n")
        path.write_bytes(data)
        inventory["objects"][0].update(sha256=model.sha256_hex(data), size=len(data))
    result = audit.audit(inventory, directory=tmp_path)
    assert result["status"] == "not_ready"
    assert result["next_feature_id"] is None
    assert result["errors"]
    with pytest.raises(audit.SequenceAuditError):
        audit.prepare_manifest(inventory, result, directory=tmp_path)


def test_partial_sidecar_reservations_raise_the_candidate_floor(tmp_path):
    inventory = fixture(tmp_path)
    inventory["objects"][0]["publication"] = "partial"
    seed = audit.audit(inventory, directory=tmp_path)
    assert seed["next_feature_id"] == 100
    assert seed["counts"]["partial_objects"] == 1


def test_exact_reviewed_transition_evidence_explains_historic_reuse(tmp_path):
    inventory = fixture(tmp_path)
    path = tmp_path / "object-0"
    record = json.loads(gzip.decompress(path.read_bytes()))
    record["feature_id"] = "1"
    data = gzip.compress(json.dumps(record).encode() + b"\n")
    path.write_bytes(data)
    inventory["objects"][0].update(sha256=model.sha256_hex(data), size=len(data))
    decisions = audit.json_bytes(
        {
            "decisions": [
                {
                    "action": "reuse_previous_feature_id",
                    "reuse_feature_id": "1",
                    "previous_identity_key": ["retired"],
                    "new_identity_key": ["1"],
                    "release": "2026-05-01",
                    "reviewer": "fixture-reviewer",
                    "pr_reference": "https://example.test/review/1",
                    "rationale": "synthetic reviewed same-feature transition",
                }
            ]
        }
    )
    (tmp_path / "decisions.json").write_bytes(decisions)
    inventory["decision_evidence"] = [
        dict(
            local_path="decisions.json",
            size=len(decisions),
            sha256=model.sha256_hex(decisions),
        )
    ]
    seed = audit.audit(inventory, directory=tmp_path)
    assert seed["status"] == "prepared_for_review", seed["errors"]
    assert seed[
        "human_review_requirements"
    ]  # review provenance is not independently certified


def test_cli_refuses_output_alias_and_preserves_evidence(tmp_path, monkeypatch):
    inventory = fixture(tmp_path)
    inventory_path = tmp_path / "inventory.json"
    inventory_path.write_bytes(audit.json_bytes(inventory))
    original = inventory_path.read_bytes()
    alias = tmp_path / "alias.json"
    alias.symlink_to(inventory_path)
    monkeypatch.setattr(
        "sys.argv",
        ["audit", "audit", "--inventory", str(inventory_path), "--output", str(alias)],
    )
    with pytest.raises(SystemExit):
        audit.main()
    assert inventory_path.read_bytes() == original


def test_legacy_only_anchor_cannot_produce_trusted_manifest(tmp_path):
    inventory = fixture(tmp_path)
    inventory["anchor"].pop("release_manifest")
    result = audit.audit(inventory, directory=tmp_path)
    assert result["status"] == "not_ready"
    with pytest.raises(audit.SequenceAuditError):
        audit.prepare_manifest(inventory, result, directory=tmp_path)


def test_offset_timestamps_normalize_to_the_same_instants(tmp_path):
    inventory = fixture(tmp_path)
    original = audit.audit(inventory, directory=tmp_path)
    inventory["coverage"]["genesis"] = "2026-03-31T20:00:00-04:00"
    inventory["coverage"]["cutoff"] = "2026-05-01T02:00:00+02:00"
    inventory["events"][0]["observed_at"] = "2026-04-01T05:30:00+05:30"
    assert audit.audit(inventory, directory=tmp_path) == original


@pytest.mark.parametrize("fault", [True, 1.0, 2, None, "1"])
def test_schema_requires_supported_non_boolean_integer(tmp_path, fault):
    inventory = fixture(tmp_path)
    inventory["schema_version"] = fault
    assert audit.audit(inventory, directory=tmp_path)["status"] == "not_ready"


@pytest.mark.parametrize(
    "field,value",
    [
        ("objects", [None]),
        ("objects", {}),
        ("events", ["bad"]),
        ("coverage", []),
        ("anchor", []),
        ("decision_evidence", [1]),
    ],
)
def test_malformed_inventory_shape_is_rejected_before_canonicalization(
    tmp_path, field, value
):
    inventory = fixture(tmp_path)
    inventory[field] = value
    assert audit.audit(inventory, directory=tmp_path)["status"] == "not_ready"


def test_naive_event_timestamp_is_rejected(tmp_path):
    inventory = fixture(tmp_path)
    inventory["events"][0]["observed_at"] = "2026-04-01T00:00:00"
    result = audit.audit(inventory, directory=tmp_path)
    assert result["status"] == "not_ready"
    assert "timezone" in result["errors"][0]


def test_duplicate_json_keys_in_files_and_sidecars_are_rejected(tmp_path):
    path = tmp_path / "duplicate.json"
    path.write_text('{"schema_version":1,"schema_version":1}')
    with pytest.raises(audit.SequenceAuditError, match="duplicate JSON key"):
        audit.read_json(path)
    inventory = fixture(tmp_path)
    sidecar = tmp_path / "object-0"
    row = gzip.decompress(sidecar.read_bytes()).strip()
    data = gzip.compress(row[:-1] + b',"feature_id":"99"}\n')
    sidecar.write_bytes(data)
    inventory["objects"][0].update(size=len(data), sha256=model.sha256_hex(data))
    result = audit.audit(inventory, directory=tmp_path)
    assert result["status"] == "not_ready"
    assert "duplicate JSON key" in result["errors"][0]


def rewrite_anchor_manifests(inventory, tmp_path, transform):
    manifest = json.loads((tmp_path / "object-2").read_bytes())
    transform(manifest)
    data = audit.json_bytes(manifest)
    for index, role in ((2, "release_manifest"), (3, "latest_manifest")):
        (tmp_path / f"object-{index}").write_bytes(data)
        inventory["objects"][index].update(
            size=len(data), sha256=model.sha256_hex(data)
        )
        inventory["anchor"][role].update(size=len(data), sha256=model.sha256_hex(data))


@pytest.mark.parametrize(
    "fault", ["manifest_release", "metadata_release", "count", "boolean_count"]
)
def test_manifest_sidecar_release_and_count_are_bound(tmp_path, fault):
    inventory = fixture(tmp_path)
    if fault == "manifest_release":
        rewrite_anchor_manifests(
            inventory, tmp_path, lambda manifest: manifest.update(release="2026-06-01")
        )
    elif fault == "metadata_release":
        path = tmp_path / "object-1"
        record = json.loads(gzip.decompress(path.read_bytes()))
        record["release"] = "2026-06-01"
        data = gzip.compress(json.dumps(record).encode() + b"\n")
        path.write_bytes(data)
        inventory["objects"][1].update(sha256=model.sha256_hex(data), size=len(data))
        inventory["anchor"]["metadata"].update(
            sha256=model.sha256_hex(data), size=len(data)
        )
        rewrite_anchor_manifests(
            inventory,
            tmp_path,
            lambda manifest: next(
                item for item in manifest["artifacts"] if item["role"] == "metadata"
            ).update(sha256=model.sha256_hex(data)),
        )
    else:
        count = True if fault == "boolean_count" else 2
        rewrite_anchor_manifests(
            inventory,
            tmp_path,
            lambda manifest: manifest["validation"].update(feature_count=count),
        )
    result = audit.audit(inventory, directory=tmp_path)
    assert result["status"] == "not_ready"
    with pytest.raises(audit.SequenceAuditError):
        audit.prepare_manifest(inventory, result, directory=tmp_path)


def test_full_release_artifacts_need_explicit_exact_allocation_mapping(tmp_path):
    inventory = fixture(tmp_path)
    assert audit.audit(inventory, directory=tmp_path)["status"] == "prepared_for_review"
    inventory["objects"][-1]["mapping_evidence_sha256"] = "0" * 64
    assert audit.audit(inventory, directory=tmp_path)["status"] == "not_ready"
    inventory = fixture(tmp_path)
    inventory["objects"][-1]["publication"] = "partial"
    inventory["objects"][-1]["allocation_evidence"] = {
        "path": "gs://test-bucket/asset/missing.metadata.ndjson.gz",
        "generation": 1,
        "sha256": "0" * 64,
    }
    assert audit.audit(inventory, directory=tmp_path)["status"] == "not_ready"


def test_manifest_artifact_cannot_be_omitted_from_normalized_inventory(tmp_path):
    inventory = fixture(tmp_path)
    removed = inventory["objects"].pop()
    inventory["events"] = [
        event
        for event in inventory["events"]
        if (event["path"], event["generation"])
        != (removed["path"], removed["generation"])
    ]
    result = audit.audit(inventory, directory=tmp_path)
    assert result["status"] == "not_ready"
    assert "write lacks exact generation/hash evidence" in result["errors"][0]


def test_hash_valid_nonobject_manifest_is_not_ready(tmp_path):
    inventory = fixture(tmp_path)
    data = b"[]\n"
    path = tmp_path / "object-2"
    path.write_bytes(data)
    inventory["objects"][2].update(size=len(data), sha256=model.sha256_hex(data))
    result = audit.audit(inventory, directory=tmp_path)
    assert result["status"] == "not_ready"
    assert result["errors"] == ["manifest evidence must be a JSON object"]
