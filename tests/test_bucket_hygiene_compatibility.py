"""Persisted producer records remain readable without granting write authority."""
import copy
import hashlib
import json
from pathlib import Path
from unittest import mock

import pytest

from ingestion.common import publication
from ingestion.eamlis_monthly import identity_state
from scripts import compare_releases, gcs_asset, publish_workflow, release_feature_model as model
from tests.test_compliance_audit import audit, blob_info, download_from


FIXTURES = Path(__file__).parent / "fixtures/bucket_hygiene"
LEGACY_GEOMETRY_HASH = "sha256:source-gpkg-geometry-blob:v1"
EAMLIS_LEDGERS = (
    "40e04b02c226bc6f4a5b8b13d50562372aea7b5f532fba2df80d87fb3b3b48e3",
    "7005c313d240803e0b72289b66266086d3d16954e89c4289983ff336b86a13f7",
    "a5a5028f00b545503c969d253ec50d3cdbfa04fe486215cfe33e820690339455",
)


@pytest.mark.parametrize("name,kind", [
    (identity_state.MARKER, "identity-installation"),
    *((f"{identity_state.LEDGERS}{key}.json", "identity-ledger") for key in EAMLIS_LEDGERS),
])
def test_eamlis_identity_paths_are_recognized_and_remain_protected(name, kind):
    assert publication.protocol_path_kind(name) == kind
    assert audit.validate_object_layout(identity_state.ROOT, blob_info(name)) == []
    categories = {"300-infrastructure-industrial": {"320-mining"}}
    assert gcs_asset.validate_asset_object_name(name, categories) == []

    uri = f"gs://{identity_state.BUCKET}/{name}"
    with mock.patch.dict(gcs_asset.os.environ, {gcs_asset.ALLOW_CANONICAL_MUTATION_ENV: "1"}):
        for operation in ("upload", "delete"):
            with pytest.raises(gcs_asset.typer.BadParameter, match="publication executor"):
                gcs_asset.require_mutation_allowed(uri, operation=operation)
    for plan in ({"promotions": [{"destination_uri": uri}]}, {"deletions": [{"uri": uri}]}):
        with pytest.raises(publication.PublicationError, match="operational objects require their own executor"):
            publish_workflow.require_unmanaged_plan(plan)
    context = publication.Context("a" * 64, "b" * 64, "c" * 40,
        publication.FINALIZATION_VERSION, identity_state.BUCKET, identity_state.ROOT, identity_state.SLUG)
    with pytest.raises(publication.PublicationError, match="cannot write publication control objects"):
        publication.target_class(context, uri)


@pytest.mark.parametrize("root,relative", [
    (identity_state.ROOT, "identity-installation.json/extra"),
    (identity_state.ROOT, "identity-installation.json\n"),
    (identity_state.ROOT, "identity-installation.json.bak"),
    (identity_state.ROOT, "identity-ledgers/.json"),
    (identity_state.ROOT, f"identity-ledgers/{'a' * 63}.json"),
    (identity_state.ROOT, f"identity-ledgers/{'a' * 65}.json"),
    (identity_state.ROOT, f"identity-ledgers/{'A' * 64}.json"),
    (identity_state.ROOT, f"identity-ledgers/{'g' * 64}.json"),
    (identity_state.ROOT, f"identity-ledgers/{'a' * 64}.json\n"),
    (identity_state.ROOT, f"identity-ledgers/{'a' * 64}.json/extra"),
    (identity_state.ROOT, f"identity-ledgers/nested/{'a' * 64}.json"),
    (identity_state.ROOT, f"identity-ledgers//{'a' * 64}.json"),
    (identity_state.ROOT, "unknown.json"),
    ("300-infrastructure-industrial/320-mining/another-asset", "identity-installation.json"),
    ("300-infrastructure-industrial/320-mining/another-asset", f"identity-ledgers/{'a' * 64}.json"),
    ("100-geographic-reference/110-boundaries/eamlis-abandoned-mine-land-inventory", "identity-installation.json"),
])
def test_eamlis_identity_path_grammar_rejects_malformed_and_foreign_records(root, relative):
    name = f"{root}/publications/{relative}"
    assert publication.protocol_path_kind(name) is None
    assert audit.validate_object_layout(root, blob_info(name))
    assert gcs_asset.validate_asset_object_name(name, {
        "300-infrastructure-industrial": {"320-mining"},
        "100-geographic-reference": {"110-boundaries"},
    })


@pytest.fixture
def historical_manifest():
    return json.loads((FIXTURES / "world-polygons.manifest.json").read_text())


def test_historical_world_polygons_manifest_remains_readable(historical_manifest):
    original = copy.deepcopy(historical_manifest)
    artifacts = model.validate_release_manifest(historical_manifest,
        expected_asset_slug="world-polygons", expected_release="2026-06-26", require_generations=True)
    assert set(artifacts) == {"fgb", "pmtiles", "metadata", "schema", "manifest"}
    assert historical_manifest == original


@pytest.mark.parametrize("field,value", [
    ("geometry_hash_algorithm", "sha256:unknown"),
    ("geometry_hash_algorithm", None),
    ("geometry_hash_algorithm", []),
    ("properties_hash_algorithm", "sha256:unknown"),
])
def test_historical_manifest_still_rejects_invalid_hash_declarations(historical_manifest, field, value):
    historical_manifest["hashes"][field] = value
    with pytest.raises(model.ReleaseFeatureModelError, match="unsupported hash algorithms"):
        model.validate_release_manifest(historical_manifest, require_generations=True)


def test_historical_manifest_still_requires_artifact_generations(historical_manifest):
    next(item for item in historical_manifest["artifacts"] if item["role"] == "metadata").pop("generation")
    with pytest.raises(model.ReleaseFeatureModelError, match="generation"):
        model.validate_release_manifest(historical_manifest, require_generations=True)


def test_historical_hash_declaration_cannot_be_computed_or_used_by_new_builders(historical_manifest):
    with pytest.raises(model.ReleaseFeatureModelError, match="unsupported geometry hash algorithm"):
        model.geometry_hash({"type": "Point", "coordinates": [1, 2]}, algorithm=LEGACY_GEOMETRY_HASH)
    with pytest.raises(model.ReleaseFeatureModelError, match="unsupported geometry hash algorithm"):
        model.build_release_manifest(**{key: historical_manifest[key] for key in (
            "asset_slug", "release", "source_inputs", "artifacts", "schema", "identity", "validation",
        )}, geometry_hash_algorithm=LEGACY_GEOMETRY_HASH)


@pytest.mark.parametrize("before,after", [
    (LEGACY_GEOMETRY_HASH, LEGACY_GEOMETRY_HASH),
    (LEGACY_GEOMETRY_HASH, model.GEOMETRY_HASH_ALGORITHM),
    (model.GEOMETRY_HASH_ALGORITHM, LEGACY_GEOMETRY_HASH),
])
def test_readable_historical_hash_does_not_enable_release_comparison(before, after):
    def manifest(algorithm):
        return {"release_feature_model_schema_version": 2,
            "identity": model.build_identity_metadata(strategy="source_field", source_fields=["feature_id"]),
            "hashes": {"geometry_hash_algorithm": algorithm,
                       "properties_hash_algorithm": model.PROPERTIES_HASH_ALGORITHM}}
    fields = {"feature_id": model.ReleaseSchemaField("feature_id", "string", False)}
    result = compare_releases.identity_compatibility(manifest(before), manifest(after), fields, fields)
    assert result == {"compatible": False, "reason": "Hash semantics are missing or incompatible."}


def test_production_readiness_accepts_persisted_world_polygons_bundle(historical_manifest):
    index = json.loads((FIXTURES / "world-polygons.json").read_text())
    bucket = "skytruth-shared-datasets-1"
    prefix = f"gs://{bucket}/"
    slug = "world-polygons"
    index_name = f"_catalog/releases/{slug}.json"
    _, catalog = audit.load_catalog(Path(__file__).resolve().parents[1] / "catalog/shared-datasets-catalog.csv")
    row = catalog[slug]
    root = audit.row_asset_root(bucket, row)
    files = {item["format"]: item for item in index["latest_release"]["files"]}
    assert hashlib.sha256((FIXTURES / "world-polygons.manifest.json").read_bytes()).hexdigest() == files["manifest"]["sha256"]
    payloads = {
        index_name: index,
        files["manifest"]["path"].removeprefix(prefix): historical_manifest,
        files["schema"]["path"].removeprefix(prefix): historical_manifest["schema"],
    }
    blobs = [blob_info(index_name)]
    blobs.extend(blob_info(item["path"].removeprefix(prefix), generation=str(item["generation"])) for item in files.values())
    blobs.extend(blob_info(f"{root}/latest/{slug}{suffix}") for suffix in audit.FEATURE_METADATA_LATEST_FILES.values())
    with mock.patch.object(audit, "download_object_text", side_effect=download_from(payloads)):
        findings = audit.validate_feature_metadata_readiness(bucket=bucket, blobs=blobs,
            catalog_rows=[row], feature_metadata_docs={slug: {"storage": "metadata_sidecar_v1"}})
    assert findings == []
