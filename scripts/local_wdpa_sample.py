#!/usr/bin/env python3
"""Run the production WDPA processing path with frozen local inputs, no writes to GCS."""

from __future__ import annotations

import argparse
from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
import shutil
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ingestion.common.identity_index import DiskIdentityRecords
from ingestion.common import feature_metadata
from ingestion.common.process_stream import feature_stream
from ingestion.wdpa_monthly import run as wdpa
from ingestion.wdpa_monthly.resources import PhaseProfiler, prepare_scratch
from scripts import release_feature_model as model
from scripts.feature_metadata_translation_reuse import TranslationMemory
from scripts.wdpa_processing_gate import source_digest


def load_baseline(directory, asset, workdir):
    pins = json.loads((directory / "pins.json").read_text())[asset.slug]
    manifest_path = directory / f"{asset.slug}.manifest.json"
    sidecar = directory / f"{asset.slug}.metadata.ndjson.gz"
    if wdpa.sha256_file(manifest_path) != pins["manifest_sha256"]:
        raise RuntimeError("frozen manifest does not match its snapshot")
    manifest = json.loads(manifest_path.read_text())
    artifact = model.validate_release_manifest(
        manifest,
        expected_asset_slug=asset.slug,
        expected_release=manifest["release"],
        require_generations=True,
    )["metadata"]
    if wdpa.sha256_file(sidecar) != artifact["sha256"].removeprefix("sha256:"):
        raise RuntimeError("frozen baseline sidecar hash differs from manifest")
    snapshot = model.GeneratedIdentitySnapshot(
        pins["manifest_uri"], pins["manifest_generation"], pins["manifest_sha256"]
    )
    index = DiskIdentityRecords(workdir / f"{asset.slug}.baseline.sqlite")
    try:
        validation = model.validate_sidecar_records(
            model.read_metadata_sidecar(sidecar),
            expected_asset_slug=asset.slug,
            expected_release=manifest["release"],
            identity_index=index,
        )
        if (
            not validation.valid
            or validation.feature_count != manifest["validation"]["feature_count"]
        ):
            raise RuntimeError("frozen sidecar count/contract differs from manifest")
        return model.generated_baseline_from_manifest(
            manifest,
            index.seal(),
            expected_contract_id=wdpa.CONTRACT_ID,
            snapshot=snapshot,
        )
    except BaseException:
        index.close()
        raise


def semantic_summary(path):
    digest = hashlib.sha256()
    rows = india_rows = 0
    india_sites = set()
    for row in model.read_metadata_sidecar(path):
        rows += 1
        properties = row["properties"]
        if "IND" in str(properties.get("ISO3", "")).split(";"):
            india_rows += 1
            india_sites.add(str(properties.get("SITE_ID")))
        digest.update(
            model.canonical_json(
                {
                    key: row[key]
                    for key in (
                        "feature_id",
                        "identity_key",
                        "geometry_hash",
                        "properties_hash",
                        "properties",
                    )
                }
            ).encode()
        )
        digest.update(b"\n")
    return {
        "rows": rows,
        "india_rows": india_rows,
        "india_sites": len(india_sites),
        "semantic_sha256": digest.hexdigest(),
    }


def cgroup_limit(name):
    path = Path("/sys/fs/cgroup") / name
    if not path.exists():
        return None
    values = path.read_text().split()
    if values[0] == "max":
        return None
    return int(values[0]) / int(values[1]) if name == "cpu.max" else int(values[0])


def legacy_sample(
    workdir, *, layers, asset, where, source, baseline, memory, run_date, decisions
):
    """Retained pre-refactor path, restricted to a deterministic small sample."""
    workdir.mkdir()
    gpkg = workdir / "filtered.gpkg"
    raw = workdir / "raw.geojsonseq"
    enriched = workdir / "enriched.geojsonseq"
    metadata = workdir / f"{asset.slug}.metadata.ndjson.gz"
    schema = workdir / f"{asset.slug}.schema.json"
    fgb = workdir / f"{asset.slug}.fgb"
    wdpa.build_filtered_gpkg(
        source=source, source_layers=layers, asset=asset, where=where, output=gpkg
    )
    wdpa.convert_gpkg_to_geojsonseq(gpkg, asset, raw)
    result = feature_metadata.write_generated_id_release(
        open_features=lambda: feature_metadata.iter_geojsonseq(raw),
        asset_slug=asset.slug,
        release=run_date.isoformat(),
        provenance={
            "source": source,
            "where": where,
            "identity_strategy": "generated_sequence_source_fields",
        },
        source_fields=("SITE_PID",),
        baseline=baseline,
        identity_resolution_decisions=decisions,
        enriched_features_path=enriched,
        sidecar_path=metadata,
    )
    feature_metadata.write_schema(result.schema_payload, schema)
    wdpa.convert_geojsonseq_to_fgb(enriched, asset, fgb)
    localized = workdir / "localized"
    memory.rebuild(
        canonical_sidecar=metadata,
        schema=schema,
        asset_slug=asset.slug,
        release=run_date.isoformat(),
        output_dir=localized,
    )
    return metadata, schema, fgb, localized, result


def compare_legacy_sample(legacy, output, asset, memory, workdir):
    metadata, schema, fgb, localized, result = legacy
    if semantic_summary(metadata) != semantic_summary(output.metadata):
        raise RuntimeError(f"{asset.slug} legacy IDs, hashes or properties differ")
    if json.loads(schema.read_text()) != output.schema_payload:
        raise RuntimeError(f"{asset.slug} legacy metadata schema differs")
    if (
        result.next_generated_feature_id != output.next_generated_feature_id
        or result.identity_decisions != output.identity_decisions
    ):
        raise RuntimeError(f"{asset.slug} legacy allocation evidence differs")
    if wdpa.layer_fields(fgb) != wdpa.layer_fields(output.fgb):
        raise RuntimeError(f"{asset.slug} legacy field types differ")

    def geometry_rows(path):
        with feature_stream(
            ["ogr2ogr", "-f", "GeoJSONSeq", "-lco", "RS=NO", "/vsistdout/", str(path)],
            log_path=workdir / f"{path.parent.name}-comparison.log",
        ) as rows:
            return {row["properties"]["feature_id"]: row for row in rows}

    if geometry_rows(fgb) != geometry_rows(output.fgb):
        raise RuntimeError(f"{asset.slug} legacy geometry or FGB properties differ")
    for locale in memory.locales:
        old = localized / f"{asset.slug}.metadata.{locale}.ndjson.gz"
        if semantic_summary(old) != semantic_summary(output.localized_metadata[locale]):
            raise RuntimeError(f"{asset.slug} legacy {locale} metadata differs")
    if wdpa.sha256_file(
        localized / f"{asset.slug}.metadata-translations.csv"
    ) != wdpa.sha256_file(output.metadata_translations):
        raise RuntimeError(f"{asset.slug} legacy canonical translation CSV differs")
    shutil.rmtree(metadata.parent)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--workdir", required=True, type=Path)
    baseline = parser.add_mutually_exclusive_group(required=True)
    baseline.add_argument(
        "--baselines", type=Path, help="Frozen manifests, sidecars and pins.json"
    )
    baseline.add_argument(
        "--genesis",
        action="store_true",
        help="Fixture comparison only, never acceptance evidence",
    )
    parser.add_argument("--translation-memory", required=True, type=Path)
    parser.add_argument("--supplement", type=Path)
    parser.add_argument("--run-date", default="2026-10-01")
    parser.add_argument("--fraction", type=float, default=1)
    parser.add_argument("--seed", type=int, default=7919)
    parser.add_argument(
        "--compare-legacy",
        action="store_true",
        help="Compare the retained old path on a sample; not resource acceptance evidence",
    )
    args = parser.parse_args()
    if not 0 < args.fraction <= 1:
        parser.error("fraction must be in (0,1]")
    if args.compare_legacy and args.fraction == 1:
        parser.error("legacy comparison requires a sample fraction below 1")
    wdpa.configure_logging()
    prepare_scratch()
    args.workdir.mkdir(parents=True, exist_ok=False)
    run_date = wdpa.parse_run_date(args.run_date)
    scratch_root = (
        Path("/work") if args.workdir.is_relative_to(Path("/work")) else args.workdir
    )
    profiler = PhaseProfiler(
        args.workdir, versions=wdpa.native_versions(), scratch_root=scratch_root
    )
    report = {
        "schema_version": 1,
        "source_tree_sha256": source_digest(),
        "native_versions": profiler.versions,
        "source_sha256": wdpa.sha256_file(args.source),
        "translation_memory_sha256": wdpa.sha256_file(args.translation_memory),
        "baseline_snapshot_sha256": wdpa.sha256_file(args.baselines / "pins.json")
        if args.baselines
        else None,
        "cpu_limit": cgroup_limit("cpu.max"),
        "memory_limit_bytes": cgroup_limit("memory.max"),
        "sample_fraction": args.fraction,
        "sample_seed": args.seed,
        "genesis": args.genesis,
        "run_date": args.run_date,
        "assets": {},
        "state": "failed",
    }
    started = time.monotonic()
    try:
        with ExitStack() as stack:
            with profiler.phase("frozen-inputs"):
                source_copy = args.workdir / args.source.name
                shutil.copyfile(args.source, source_copy)
                sources = wdpa.prepare_source_datasets(source_copy, args.workdir)
                layers, split, fields = wdpa.discover_source_layers(sources)
                baselines = {}
                for asset in wdpa.ASSETS:
                    baselines[asset.slug] = (
                        model.GeneratedIdentityBaseline.genesis(
                            contract_id=wdpa.CONTRACT_ID
                        )
                        if args.genesis
                        else load_baseline(args.baselines, asset, args.workdir)
                    )
                    if isinstance(baselines[asset.slug].records, DiskIdentityRecords):
                        stack.callback(baselines[asset.slug].records.close)
                local_memory = args.workdir / "translation-memory.sqlite"
                shutil.copyfile(args.translation_memory, local_memory)
                memory = TranslationMemory(local_memory, supplement=args.supplement)
                stack.callback(memory.close)
            sample = (
                None
                if args.fraction == 1
                else wdpa.SampleSpec(args.fraction, args.seed)
            )
            wdpa.assert_sample_field_available(layers, sample)
            for asset in wdpa.ASSETS:
                where = wdpa.sampled_where_clause(
                    wdpa.asset_where_clause(asset, split), sample
                )
                decisions = model.load_identity_resolution_decisions(
                    asset_slug=asset.slug, release=args.run_date
                )
                legacy = None
                if args.compare_legacy:
                    with profiler.phase(f"{asset.slug}:legacy-sample"):
                        legacy = legacy_sample(
                            args.workdir / f"{asset.slug}-legacy",
                            layers=layers,
                            asset=asset,
                            where=where,
                            source=sources[0],
                            baseline=baselines[asset.slug],
                            memory=memory,
                            run_date=run_date,
                            decisions=decisions,
                        )
                output = wdpa.build_asset_outputs(
                    source=sources[0],
                    source_layers=layers,
                    source_fields=fields,
                    asset=asset,
                    where=where,
                    workdir=args.workdir,
                    run_date=run_date,
                    baseline=baselines[asset.slug],
                    translation_memory=memory,
                    identity_resolution_decisions=decisions,
                    cleanup_after_gpkg=(args.workdir / "source-zips", source_copy)
                    if asset == wdpa.ASSETS[-1]
                    else (),
                    profiler=profiler,
                )
                if legacy:
                    with profiler.phase(f"{asset.slug}:compatibility"):
                        compare_legacy_sample(
                            legacy, output, asset, memory, args.workdir
                        )
                report["assets"][asset.slug] = {
                    **semantic_summary(output.metadata),
                    "artifact_sha256": output.sha256,
                    "next_generated_feature_id": output.next_generated_feature_id,
                    "compatibility_verified": legacy is not None,
                    "baseline": vars(baselines[asset.slug].snapshot)
                    if baselines[asset.slug].snapshot
                    else None,
                }
                # Match production's lifetime: marine bytes are gone before the
                # terrestrial build. The summary keeps their validation evidence.
                for path in (
                    output.fgb,
                    output.pmtiles,
                    output.metadata,
                    output.schema,
                    output.metadata_translations,
                    *output.localized_metadata.values(),
                ):
                    path.unlink()
            report["state"] = "succeeded"
            report["contracts_verified"] = True
            report["compatibility_verified"] = args.compare_legacy
            if source_digest() != report["source_tree_sha256"]:
                report["state"] = "failed"
                raise RuntimeError("Processing source changed during the benchmark")
    finally:
        report["elapsed_seconds"] = time.monotonic() - started
        report["phases"] = profiler.records
        report["memory_peak_bytes"] = max(
            (
                p["cgroup_memory_peak_bytes"]
                for p in profiler.records
                if p["cgroup_memory_peak_bytes"] is not None
            ),
            default=None,
        )
        report["scratch_peak_bytes"] = max(
            (p["scratch_peak_bytes"] for p in profiler.records), default=None
        )
        (args.workdir / "benchmark.json").write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n"
        )
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
