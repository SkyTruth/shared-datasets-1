#!/usr/bin/env python3
"""Bounded, reproducible CPU/IO benchmarks; never accesses cloud services."""

import argparse
from contextlib import closing
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import statistics
import sys
import time

repo = Path(
    os.environ.get("PUBLICATION_BENCHMARK_REPO", Path(__file__).resolve().parents[1])
)
sys.path.insert(0, str(repo))
from ingestion.common import feature_metadata as fm
from ingestion.common.identity_index import DiskIdentityPlan, DiskIdentityRecords
from ingestion.wdpa_monthly.geometry_store import NormalizedGeometryStore
from scripts import (
    feature_metadata_translation_reuse as reuse,
    feature_metadata_localization as loc,
)
from scripts import release_feature_model as model


def benchmark(root, rows, vertices, repeats):
    root.mkdir(parents=True, exist_ok=False)
    ring = [
        [
            round(math.cos(i * 2 * math.pi / vertices), 7),
            round(math.sin(i * 2 * math.pi / vertices), 7),
        ]
        for i in range(vertices)
    ]
    ring.append(ring[0])
    timings, digests = [], []
    for repeat in range(repeats):
        directory = root / f"metadata-{repeat}"
        directory.mkdir()
        with (
            closing(DiskIdentityPlan(directory / "plan.sqlite")) as plan,
            closing(
                NormalizedGeometryStore(directory / "spool.gpkg", layer_name="fixture")
            ) as store,
        ):
            for ordinal in range(1, rows + 1):
                feature = {
                    "type": "Feature",
                    "geometry": {"type": "Polygon", "coordinates": [ring]},
                    "properties": {
                        "SITE_PID": str(ordinal),
                        "name": f"Site {ordinal}",
                        "count": ordinal,
                    },
                }
                plan.add(ordinal, feature, source_fields=("SITE_PID",))
                store.add(feature, ordinal)
            store.finish_input()
            store.close()
            store = NormalizedGeometryStore.open(
                directory / "spool.gpkg", layer_name="fixture"
            )
            next_id, _ = plan.resolve(
                baseline=model.GeneratedIdentityBaseline.genesis(
                    contract_id="benchmark-v1"
                ),
                asset_slug="benchmark",
                release="2026-11-01",
            )
            start = time.perf_counter()
            if (repo / "ingestion/wdpa_monthly/metadata.py").is_file():
                from ingestion.wdpa_monthly import metadata as stage

                os.environ["SHARED_DATASETS_CHECKPOINT_DIR"] = str(
                    directory / "checkpoints"
                )
                completed, payload = stage.prepare(
                    store.path,
                    directory / "plan.sqlite",
                    layer="fixture",
                    slug="benchmark",
                    release="2026-11-01",
                    source="fixture",
                    where="1=1",
                    next_feature_id=next_id,
                    decisions=[],
                    versions={"gdal": "3.6.2"},
                )
                metadata = completed / "benchmark.metadata.ndjson.gz"
                store.attributes_path = completed / "attributes.gpkg"
                store.field_names = tuple(payload["field_names"])
            else:
                store.prepare_fields()
                accumulator = fm.SchemaAccumulator()

                def sidecars():
                    for record in store.sidecar_records(
                        plan=plan,
                        asset_slug="benchmark",
                        release="2026-11-01",
                        provenance={
                            "source": "fixture",
                            "where": "1=1",
                            "identity_strategy": "generated_sequence_source_fields",
                        },
                    ):
                        accumulator.observe(record)
                        yield record

                metadata = directory / "benchmark.metadata.ndjson.gz"
                with closing(
                    DiskIdentityRecords(directory / "validation.sqlite")
                ) as index:
                    assert (
                        fm.write_sidecar(sidecars(), metadata, identity_index=index)
                        == rows
                    )
                    index.seal().validate_baseline(next_id)
                fm.write_schema(
                    accumulator.payload(asset_slug="benchmark", release="2026-11-01"),
                    directory / "schema.json",
                )
            store.projection_sql()
            timings.append(time.perf_counter() - start)
            digests.append(hashlib.sha256(metadata.read_bytes()).hexdigest())
            store.close()
    source = root / "source"
    source.mkdir()
    records = [
        {
            "schema_version": 2,
            "asset_slug": "benchmark",
            "release": "2026-10-01",
            "feature_id": str(i),
            "identity_key": [str(i)],
            "geometry_hash": "sha256:" + "a" * 64,
            "properties_hash": "sha256:" + "b" * 64,
            "properties": {"SITE_PID": str(i), "name": f"Site {i}"},
            "provenance": {},
        }
        for i in range(1, rows + 1)
    ]
    sidecar, translations = source / "metadata.ndjson.gz", source / "translations.csv"
    model.write_metadata_sidecar(records, sidecar)
    with translations.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=reuse.COLUMNS)
        writer.writeheader()
        for record in records:
            text = record["properties"]["name"]
            writer.writerow(
                dict(
                    feature_id=record["feature_id"],
                    field="name",
                    locale="fr",
                    source_value_hash=loc.source_value_hash(text),
                    value="FR " + text,
                    review_state="machine_translated",
                    notes="benchmark",
                )
            )
    sources = [
        {
            "asset_slug": "benchmark",
            "release": "2026-10-01",
            "canonical_sidecar": str(sidecar),
            "translation_source": str(translations),
        }
    ]
    translation_times = []
    indexes = []
    os.environ["SHARED_DATASETS_CHECKPOINT_DIR"] = str(root / "translation-checkpoints")
    for name in ("sample", "complete"):
        start = time.perf_counter()
        options = dict(
            sources=sources,
            fields=["name"],
            locales=["fr"],
            source_key_fields=["SITE_PID"],
        )
        if hasattr(reuse, "open_memory"):
            memory = reuse.open_memory(**options)
        else:
            database = root / f"{name}.sqlite"
            reuse.build_memory(database=database, **options)
            memory = reuse.TranslationMemory(database)
        with closing(memory):
            assert memory.direct("benchmark", '["1"]')[0][0][1] == "FR Site 1"
            indexes.append(str(memory.database))
        translation_times.append(time.perf_counter() - start)
    return {
        "fixture": {
            "rows": rows,
            "vertices_per_geometry": vertices,
            "repeats": repeats,
        },
        "python": platform.python_version(),
        "metadata_seconds": timings,
        "metadata_median_seconds": statistics.median(timings),
        "metadata_sha256": digests,
        "translation_index_seconds": translation_times,
        "translation_total_seconds": sum(translation_times),
        "distinct_translation_indexes": len(set(indexes)),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", required=True, type=Path)
    parser.add_argument("--rows", default=1000, type=int)
    parser.add_argument("--vertices", default=256, type=int)
    parser.add_argument("--repeats", default=3, type=int)
    args = parser.parse_args()
    result = benchmark(args.work_dir, args.rows, args.vertices, args.repeats)
    (args.work_dir / "benchmark.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
