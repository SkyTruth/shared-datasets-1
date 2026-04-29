"""Run a sampled WDPA conversion locally without publishing to GCS."""

from __future__ import annotations

import json
import math
import os
import random
import shutil
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ingestion.wdpa_monthly import run as wdpa


DEFAULT_SOURCE = "/data/WDPA_WDOECM_Apr2026_Public_all_shp.zip"
DEFAULT_EXTRACTED_DIR = "/data/source-zips"
DEFAULT_SHAPEFILE_DIR = "/data/source-shps"
DEFAULT_WORKDIR = "/data/wdpa-sample-output"
SAMPLE_SOURCE_LAYER = "wdpa_sample_source"


@dataclass(frozen=True)
class LayerSample:
    layer: wdpa.SourceLayer
    fids: tuple[int, ...]


def source_paths_from_zips(path: Path, shapefile_dir: Path) -> list[str]:
    sources = []
    for zip_path in sorted(path.glob("*.zip")):
        dest = shapefile_dir / zip_path.stem
        dest.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path) as archive:
            members = wdpa.shapefile_members(archive)
            if not all((dest / member).exists() for member in members):
                archive.extractall(dest)
            sources.extend(str(dest / member) for member in members)
    return sources


def source_paths_from_zip(path: Path, workdir: Path) -> list[str]:
    source_copy = workdir / "source.zip"
    shutil.copyfile(path, source_copy)
    return wdpa.prepare_source_datasets(source_copy, workdir)


def deterministic_seed(value: str) -> int:
    return int.from_bytes(value.encode("utf-8"), "little", signed=False)


def sample_layer_fids(
    layers: list[wdpa.SourceLayer],
    *,
    fraction: float,
    seed: int,
) -> list[LayerSample]:
    samples = []
    for layer in layers:
        row_count = wdpa.feature_count(layer.source, layer.name)
        sample_count = max(1, math.ceil(row_count * fraction)) if row_count else 0
        rng = random.Random(deterministic_seed(f"{seed}:{layer.source}:{layer.name}"))
        fids = tuple(sorted(rng.sample(range(row_count), sample_count))) if sample_count else ()
        samples.append(LayerSample(layer=layer, fids=fids))
    return samples


def build_sample_source_gpkg(
    *,
    layer_samples: list[LayerSample],
    output: Path,
) -> int:
    wdpa.remove_if_exists(output)
    copied = 0
    for sample in layer_samples:
        for fid in sample.fids:
            args = ["ogr2ogr", "-f", "GPKG", "-fid", str(fid)]
            if copied > 0:
                args.extend(["-update", "-append", "-addfields"])
            args.extend(
                [
                    str(output),
                    sample.layer.source,
                    sample.layer.name,
                    "-nln",
                    SAMPLE_SOURCE_LAYER,
                    "-nlt",
                    "GEOMETRY",
                ]
            )
            wdpa.run_command(args)
            copied += 1
    if copied == 0:
        raise RuntimeError("Local WDPA sample selected no source rows")
    return copied


def build_sample_outputs(
    *,
    sample_source: Path,
    source_fields: tuple[wdpa.FieldSpec, ...],
    asset: wdpa.AssetSpec,
    base_where: str,
    workdir: Path,
) -> wdpa.AssetOutputs:
    gpkg = workdir / f"{asset.slug}.gpkg"
    fgb = workdir / f"{asset.slug}.fgb"
    geojsonseq = workdir / f"{asset.slug}.geojsonseq"
    pmtiles = workdir / f"{asset.slug}.pmtiles"

    wdpa.remove_if_exists(gpkg)
    expected_rows = wdpa.feature_count(str(sample_source), SAMPLE_SOURCE_LAYER, base_where)
    if expected_rows <= 0:
        raise RuntimeError(f"{asset.slug} sampled filter produced no rows")
    wdpa.run_command(
        [
            "ogr2ogr",
            "-f",
            "GPKG",
            str(gpkg),
            str(sample_source),
            SAMPLE_SOURCE_LAYER,
            "-nln",
            asset.tile_layer,
            "-nlt",
            "GEOMETRY",
            "-where",
            base_where,
        ]
    )

    outputs = build_outputs_from_gpkg(
        gpkg=gpkg,
        asset=asset,
        expected_rows=expected_rows,
        source_fields=source_fields,
        fgb=fgb,
        geojsonseq=geojsonseq,
        pmtiles=pmtiles,
    )
    return outputs


def build_outputs_from_gpkg(
    *,
    gpkg: Path,
    asset: wdpa.AssetSpec,
    expected_rows: int,
    source_fields: tuple[wdpa.FieldSpec, ...],
    fgb: Path,
    geojsonseq: Path,
    pmtiles: Path,
) -> wdpa.AssetOutputs:
    wdpa.convert_gpkg_to_fgb(gpkg, asset, fgb)
    wdpa.convert_gpkg_to_geojsonseq(gpkg, asset, geojsonseq)
    wdpa.build_pmtiles(geojsonseq, asset, pmtiles)

    actual_rows = wdpa.feature_count(str(fgb))
    if actual_rows != expected_rows:
        raise RuntimeError(
            f"{asset.slug} row count mismatch: expected {expected_rows}, got {actual_rows}"
        )
    if wdpa.layer_fields(fgb) != source_fields:
        raise RuntimeError(f"{asset.slug} FGB schema does not match source schema")
    wdpa.validate_pmtiles(pmtiles)
    wdpa.remove_if_exists(gpkg)
    wdpa.remove_if_exists(geojsonseq)

    return wdpa.AssetOutputs(
        fgb=fgb,
        pmtiles=pmtiles,
        row_count=actual_rows,
        sha256={
            "fgb": wdpa.sha256_file(fgb),
            "pmtiles": wdpa.sha256_file(pmtiles),
        },
    )


def main() -> None:
    wdpa.configure_logging()
    for binary in ("ogrinfo", "ogr2ogr", "tippecanoe", "pmtiles"):
        wdpa.require_binary(binary)

    os.environ.setdefault("WDPA_SAMPLE_FRACTION", "0.001")
    sample = wdpa.parse_sample_spec()
    if sample is None:
        raise RuntimeError("Set WDPA_SAMPLE_FRACTION to a value greater than 0 and less than 1")

    workdir = Path(os.environ.get("LOCAL_WDPA_WORKDIR", DEFAULT_WORKDIR))
    workdir.mkdir(parents=True, exist_ok=True)

    extracted_dir = Path(os.environ.get("LOCAL_WDPA_EXTRACTED_DIR", DEFAULT_EXTRACTED_DIR))
    shapefile_dir = Path(os.environ.get("LOCAL_WDPA_SHAPEFILE_DIR", DEFAULT_SHAPEFILE_DIR))
    source = Path(os.environ.get("LOCAL_WDPA_SOURCE", DEFAULT_SOURCE))
    if extracted_dir.exists():
        sources = source_paths_from_zips(extracted_dir, shapefile_dir)
    elif source.exists():
        sources = source_paths_from_zip(source, workdir)
    else:
        raise FileNotFoundError(
            f"No local WDPA source found at {source} or extracted zips at {extracted_dir}"
        )

    layers, split_field, source_fields = wdpa.discover_source_layers(sources)
    layer_samples = sample_layer_fids(layers, fraction=sample.fraction, seed=sample.seed)
    sample_source = workdir / "wdpa-source-sample.gpkg"
    sampled_source_rows = build_sample_source_gpkg(
        layer_samples=layer_samples,
        output=sample_source,
    )

    records = []
    for asset in wdpa.ASSETS:
        where = wdpa.asset_where_clause(asset, split_field)
        outputs = build_sample_outputs(
            sample_source=sample_source,
            source_fields=source_fields,
            asset=asset,
            base_where=where,
            workdir=workdir,
        )
        records.append(
            {
                "asset_slug": asset.slug,
                "where": where,
                "rows": outputs.row_count,
                "fgb": str(outputs.fgb),
                "fgb_size": outputs.fgb.stat().st_size,
                "pmtiles": str(outputs.pmtiles),
                "pmtiles_size": outputs.pmtiles.stat().st_size,
                "sampled_source_rows": sampled_source_rows,
                "sha256": outputs.sha256,
            }
        )

    print(json.dumps(records, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
