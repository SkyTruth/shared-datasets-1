"""Complete metadata and attribute checkpoint, independent of tile-building code."""

from contextlib import closing

from ingestion.common import feature_metadata as fm
from ingestion.common.identity_index import DiskIdentityPlan, DiskIdentityRecords
from ingestion.common.stage_checkpoints import StageStore, digest
from ingestion.wdpa_monthly.geometry_store import NormalizedGeometryStore


def prepare(
    normalized,
    plan_path,
    *,
    layer,
    slug,
    release,
    source,
    where,
    next_feature_id,
    decisions,
    versions,
):
    def produce(directory):
        metadata = directory / (slug + ".metadata.ndjson.gz")
        schema = directory / (slug + ".schema.json")
        with (
            closing(DiskIdentityPlan.open(plan_path)) as plan,
            closing(
                NormalizedGeometryStore.open(normalized, layer_name=layer)
            ) as store,
        ):
            store.prepare_fields(directory / "attributes.gpkg")
            accumulator = fm.SchemaAccumulator()

            def records():
                for record in store.sidecar_records(
                    plan=plan,
                    asset_slug=slug,
                    release=release,
                    provenance={
                        "source": source,
                        "where": where,
                        "identity_strategy": "generated_sequence_source_fields",
                    },
                ):
                    accumulator.observe(record)
                    yield record

            with closing(DiskIdentityRecords(directory / "validation.sqlite")) as index:
                count = fm.write_sidecar(records(), metadata, identity_index=index)
                if count != len(plan):
                    raise RuntimeError(
                        "metadata count differs from completed allocation"
                    )
                index.seal().validate_baseline(next_feature_id)
            (directory / "validation.sqlite").unlink()
            schema_payload = accumulator.payload(asset_slug=slug, release=release)
            fm.write_schema(schema_payload, schema)
            return {
                "schema_payload": schema_payload,
                "field_names": list(store.field_names),
                "rows": count,
            }

    return StageStore().run(
        "wdpa-metadata",
        {
            "asset_slug": slug,
            "release": release,
            "layer": layer,
            "source": source,
            "where": where,
            "decisions_sha256": digest(list(decisions)),
            "next_feature_id": next_feature_id,
            "native_versions": versions,
        },
        produce,
        files={
            "normalized": normalized,
            "seal": normalized.with_suffix(".sha256"),
            "witness": normalized.with_suffix(".schema.geojsonseq"),
            "allocation": plan_path,
        },
        code=(
            "ingestion/wdpa_monthly/metadata.py",
            "ingestion/wdpa_monthly/geometry_store.py",
            "ingestion/common/feature_metadata.py",
            "ingestion/common/identity_index.py",
            "scripts/release_feature_model.py",
        ),
    )
