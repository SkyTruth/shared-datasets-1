"""Simplified monthly WDPA/WDOECM publisher.

The job intentionally performs only format conversion and a source-field split.
It does not rename fields, derive metadata tables, buffer point records, or
update external databases.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import re
import shlex
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from google.cloud import storage

from ingestion.common.gcs import GcsPublisher
from ingestion.common.http import (
    STATUS_NOT_READY,
    STATUS_SUCCESS,
    request_with_retries,
)
from ingestion.common.runtime import (
    configure_logging,
    content_type_for as content_type_for,
    remove_if_exists,
    require_binary,
    run_command as common_run_command,
    sha256_file,
)


LOGGER = logging.getLogger("wdpa_monthly")

DEFAULT_PROJECT_ID = "shared-datasets-1"
DEFAULT_BUCKET = "skytruth-shared-datasets-1"
DEFAULT_SOURCE_URL_TEMPLATE = (
    "https://d1gam3xoknrgr2.cloudfront.net/current/"
    "WDPA_WDOECM_{month_token}_Public_all_shp.zip"
)
REQUEST_TIMEOUT_SECONDS = 120
SOURCE_NOT_READY_STATUS_CODES = {403, 404}
USER_AGENT = "shared-datasets-1-wdpa-monthly/1.0"
ASSET_PARENT = "100-geographic-reference/130-protected-areas"
RUN_RECORD_VERSION = 1


class SourceNotAvailableError(FileNotFoundError):
    """Raised when the monthly upstream file has not appeared yet."""


@dataclass(frozen=True)
class AssetSpec:
    slug: str
    title: str
    tile_layer: str
    split_group: str

    @property
    def root(self) -> str:
        return f"{ASSET_PARENT}/{self.slug}"

    def release_prefix(self, run_date: dt.date) -> str:
        return f"{self.root}/releases/{run_date.isoformat()}"

    def release_object(self, run_date: dt.date, suffix: str) -> str:
        return f"{self.release_prefix(run_date)}/{self.slug}{suffix}"

    def latest_object(self, suffix: str) -> str:
        return f"{self.root}/latest/{self.slug}{suffix}"

    def run_record_object(self, run_date: dt.date) -> str:
        return f"{self.root}/runs/{run_date.isoformat()}.json"


@dataclass(frozen=True)
class FieldSpec:
    name: str
    type: str
    subtype: str = ""


@dataclass(frozen=True)
class SourceLayer:
    name: str
    fields: tuple[FieldSpec, ...]
    geometry_type: str
    source: str = ""


@dataclass(frozen=True)
class AssetOutputs:
    fgb: Path
    pmtiles: Path
    row_count: int
    sha256: dict[str, str]


@dataclass(frozen=True)
class SampleSpec:
    fraction: float
    seed: int
    field: str = "SITE_ID"
    modulus: int = 1_000_000

    @property
    def threshold(self) -> int:
        return max(1, min(self.modulus, int(self.fraction * self.modulus)))


ASSETS: tuple[AssetSpec, ...] = (
    AssetSpec(
        slug="wdpa-marine",
        title="WDPA Marine Protected and Conserved Areas",
        tile_layer="wdpa_marine",
        split_group="marine",
    ),
    AssetSpec(
        slug="wdpa-terrestrial",
        title="WDPA Terrestrial Protected and Conserved Areas",
        tile_layer="wdpa_terrestrial",
        split_group="terrestrial",
    ),
)

SPLIT_FIELD_PRIORITY = ("MARINE", "REALM")


def default_run_date(today: dt.date | None = None) -> dt.date:
    """Use one stable release date for repeated early-month scheduler attempts."""
    current_date = today or dt.datetime.now(dt.UTC).date()
    return current_date.replace(day=1)


def parse_run_date(value: str | None) -> dt.date:
    if not value:
        return default_run_date()
    return dt.date.fromisoformat(value)


def parse_sample_spec() -> SampleSpec | None:
    raw_fraction = os.environ.get("WDPA_SAMPLE_FRACTION")
    if not raw_fraction:
        return None
    fraction = float(raw_fraction)
    if not 0 < fraction <= 1:
        raise RuntimeError("WDPA_SAMPLE_FRACTION must be greater than 0 and at most 1")
    if fraction == 1:
        return None
    seed = int(os.environ.get("WDPA_SAMPLE_SEED", "7919"))
    return SampleSpec(fraction=fraction, seed=seed)


def build_source_url(template: str, run_date: dt.date) -> str:
    month_token = run_date.strftime("%b%Y")
    return template.format(
        run_date=run_date.isoformat(),
        year=f"{run_date.year:04d}",
        month=f"{run_date.month:02d}",
        month_token=month_token,
    )


def source_version_for(run_date: dt.date) -> str:
    return run_date.strftime("%b%Y")


def run_command(
    args: list[str],
    *,
    capture_json: bool = False,
    capture_text: bool = False,
) -> Any:
    return common_run_command(
        args,
        capture_json=capture_json,
        capture_text=capture_text,
        logger=LOGGER,
    )


def download_file(url: str, dest: Path) -> None:
    LOGGER.info("downloading source: %s", url)
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT},
    )

    def write_response(response) -> None:
        with dest.open("wb") as file_obj:
            shutil.copyfileobj(response, file_obj)

    outcome, _payload = request_with_retries(
        request,
        timeout_seconds=REQUEST_TIMEOUT_SECONDS,
        not_ready_status_codes=SOURCE_NOT_READY_STATUS_CODES,
        response_reader=write_response,
        opener=urllib.request.urlopen,
        logger=LOGGER,
    )
    if outcome.status == STATUS_NOT_READY:
        raise SourceNotAvailableError(
            f"WDPA source not available yet ({outcome.reason}): {url}"
        )
    if outcome.status != STATUS_SUCCESS:
        raise RuntimeError(
            f"Download failed with {outcome.reason or outcome.status}: {url}"
        )
    if dest.stat().st_size == 0:
        raise RuntimeError(f"Downloaded zero-byte source file: {url}")


def source_dataset_path(path: Path) -> str:
    if path.suffix.lower() == ".zip":
        return f"/vsizip/{path}"
    return str(path)


def shapefile_members(archive: zipfile.ZipFile) -> list[str]:
    return sorted(
        name
        for name in archive.namelist()
        if name.lower().endswith(".shp") and not name.endswith("/")
    )


def prepare_source_datasets(path: Path, workdir: Path) -> list[str]:
    if path.suffix.lower() != ".zip":
        return [str(path)]

    try:
        with zipfile.ZipFile(path) as archive:
            shapefiles = shapefile_members(archive)
            if shapefiles:
                zip_path = source_dataset_path(path)
                return [f"{zip_path}/{member}" for member in shapefiles]

            nested_zips = sorted(
                name
                for name in archive.namelist()
                if name.lower().endswith(".zip") and not name.endswith("/")
            )
            if nested_zips:
                nested_dir = workdir / "source-zips"
                nested_dir.mkdir(exist_ok=True)
                sources = []
                for member in nested_zips:
                    dest = nested_dir / Path(member).name
                    if dest.exists():
                        raise RuntimeError(f"Duplicate nested source ZIP name: {dest.name}")
                    LOGGER.info("extracting nested source zip: %s", member)
                    with archive.open(member) as source_obj, dest.open("wb") as dest_obj:
                        shutil.copyfileobj(source_obj, dest_obj)
                    inner_zip_path = source_dataset_path(dest)
                    with zipfile.ZipFile(dest) as inner_archive:
                        inner_shapefiles = shapefile_members(inner_archive)
                    if inner_shapefiles:
                        sources.extend(
                            f"{inner_zip_path}/{shp_member}"
                            for shp_member in inner_shapefiles
                        )
                    else:
                        sources.append(inner_zip_path)
                remove_if_exists(path)
                LOGGER.info("prepared %s source dataset path(s)", len(sources))
                return sources
    except zipfile.BadZipFile as exc:
        raise RuntimeError(f"Downloaded source is not a valid ZIP file: {path}") from exc

    raise RuntimeError(f"No shapefiles or nested ZIP files found in source archive: {path}")


def parse_field(raw: dict[str, Any]) -> FieldSpec:
    return FieldSpec(
        name=str(raw.get("name", "")),
        type=str(raw.get("type", "")),
        subtype=str(raw.get("subType", raw.get("subtype", "")) or ""),
    )


def geometry_type(raw_layer: dict[str, Any]) -> str:
    fields = raw_layer.get("geometryFields") or []
    if fields:
        return str(fields[0].get("type", fields[0].get("geometryType", "")) or "")
    return str(raw_layer.get("geometryType", "") or "")


def parse_layers(payload: dict[str, Any]) -> list[SourceLayer]:
    layers = []
    for raw in payload.get("layers", []):
        fields = tuple(parse_field(field) for field in raw.get("fields", []))
        geom_type = geometry_type(raw)
        if not geom_type:
            continue
        layers.append(
            SourceLayer(
                name=str(raw.get("name", "")),
                fields=fields,
                geometry_type=geom_type,
            )
        )
    return layers


FIELD_LINE_RE = re.compile(r"^\s*([^:\s][^:]*):\s+([A-Za-z][A-Za-z0-9 ]*)(?:\s+\(|$)")
SKIP_TEXT_FIELD_NAMES = {
    "extent",
    "feature count",
    "fid column",
    "geometry",
    "geometry column",
    "layer name",
}


def ogrinfo_text(
    source: str,
    *,
    layer_name: str | None = None,
    where: str | None = None,
    all_layers: bool = False,
) -> str:
    args = ["ogrinfo", "-ro", "-so"]
    if all_layers:
        args.append("-al")
    if where:
        args.extend(["-where", where])
    args.append(source)
    if layer_name:
        args.append(layer_name)
    return run_command(args, capture_text=True)


def parse_ogrinfo_text_layers(text: str) -> list[SourceLayer]:
    layers = []
    blocks = re.split(r"(?=^Layer name:\s*)", text, flags=re.MULTILINE)
    for block in blocks:
        name_match = re.search(r"^Layer name:\s*(.+)$", block, flags=re.MULTILINE)
        if not name_match:
            continue
        geometry_match = re.search(r"^Geometry:\s*(.+)$", block, flags=re.MULTILINE)
        fields = []
        for line in block.splitlines():
            match = FIELD_LINE_RE.match(line)
            if not match:
                continue
            name = match.group(1).strip()
            if name.lower() in SKIP_TEXT_FIELD_NAMES:
                continue
            fields.append(FieldSpec(name=name, type=match.group(2).strip()))
        layers.append(
            SourceLayer(
                name=name_match.group(1).strip(),
                fields=tuple(fields),
                geometry_type=geometry_match.group(1).strip() if geometry_match else "",
            )
        )
    return layers


def parse_text_feature_count(text: str, source: str, layer_name: str | None) -> int:
    match = re.search(r"^Feature Count:\s*(\d+)\s*$", text, flags=re.MULTILINE)
    if not match:
        detail = f" layer {layer_name}" if layer_name else ""
        raise RuntimeError(f"Unable to determine feature count for{detail}: {source}")
    return int(match.group(1))


def set_layer_source(layers: list[SourceLayer], source: str) -> list[SourceLayer]:
    return [
        SourceLayer(
            name=layer.name,
            fields=layer.fields,
            geometry_type=layer.geometry_type,
            source=source,
        )
        for layer in layers
    ]


def source_layer_summary(layers: list[SourceLayer]) -> list[dict[str, Any]]:
    return [
        {
            "source": layer.source,
            "name": layer.name,
            "geometry_type": layer.geometry_type,
            "fields": [field.name for field in layer.fields[:25]],
            "field_count": len(layer.fields),
        }
        for layer in layers
    ]


def layer_field_lookup(layer: SourceLayer) -> dict[str, FieldSpec]:
    return {field.name.upper(): field for field in layer.fields}


def choose_split_field(layers: list[SourceLayer]) -> str:
    for field_name in SPLIT_FIELD_PRIORITY:
        if all(field_name in layer_field_lookup(layer) for layer in layers):
            return layer_field_lookup(layers[0])[field_name].name

    LOGGER.error("discovered source layers without supported split field: %s", json.dumps(source_layer_summary(layers)))
    raise RuntimeError("No geometry layers with MARINE or REALM field found in WDPA source")


def source_field_union(layers: list[SourceLayer]) -> tuple[FieldSpec, ...]:
    fields: dict[str, FieldSpec] = {}
    ordered_names: list[str] = []
    for layer in layers:
        for field in layer.fields:
            key = field.name.upper()
            if key not in fields:
                fields[key] = field
                ordered_names.append(key)
                continue
            if fields[key].type != field.type or fields[key].subtype != field.subtype:
                raise RuntimeError(
                    "WDPA source field type mismatch for "
                    f"{field.name}: {fields[key].type}/{fields[key].subtype} vs "
                    f"{field.type}/{field.subtype}"
                )
    return tuple(fields[name] for name in ordered_names)


def asset_where_clause(asset: AssetSpec, split_field: str) -> str:
    split_field_upper = split_field.upper()
    if split_field_upper == "MARINE":
        if asset.split_group == "marine":
            return f"{split_field} IN ('1', '2')"
        if asset.split_group == "terrestrial":
            return f"{split_field} = '0'"
    if split_field_upper == "REALM":
        if asset.split_group == "marine":
            return f"{split_field} IN ('Marine', 'Coastal')"
        if asset.split_group == "terrestrial":
            return f"{split_field} = 'Terrestrial'"
    raise RuntimeError(f"Unsupported WDPA split field/group: {split_field}/{asset.split_group}")


def sampled_where_clause(base_where: str, sample: SampleSpec | None) -> str:
    if sample is None:
        return base_where
    sample_predicate = (
        f"((({sample.field} * 1103515245 + {sample.seed}) % {sample.modulus}) "
        f"< {sample.threshold})"
    )
    return f"({base_where}) AND {sample_predicate}"


def assert_sample_field_available(layers: list[SourceLayer], sample: SampleSpec | None) -> None:
    if sample is None:
        return
    missing = [
        f"{layer.name} from {layer.source}"
        for layer in layers
        if sample.field.upper() not in layer_field_lookup(layer)
    ]
    if missing:
        raise RuntimeError(
            f"WDPA sample field {sample.field} missing from layer(s): "
            + ", ".join(missing)
        )


def discover_source_layers(sources: str | list[str]) -> tuple[list[SourceLayer], str, tuple[FieldSpec, ...]]:
    source_list = [sources] if isinstance(sources, str) else sources
    layers = []
    for source in source_list:
        try:
            payload = run_command(["ogrinfo", "-json", source], capture_json=True)
            source_layers = parse_layers(payload)
        except RuntimeError:
            source_layers = parse_ogrinfo_text_layers(ogrinfo_text(source, all_layers=True))
        layers.extend(set_layer_source(source_layers, source))

    if not layers:
        raise RuntimeError("No geometry layers found in WDPA source")

    split_field = choose_split_field(layers)
    source_fields = source_field_union(layers)

    LOGGER.info(
        "selected source layers using %s split: %s",
        split_field,
        ", ".join(
            f"{layer.name} ({layer.geometry_type}) from {layer.source}"
            for layer in layers
        ),
    )
    return layers, split_field, source_fields


def feature_count(
    source: str,
    layer_name: str | None = None,
    where: str | None = None,
) -> int:
    args = ["ogrinfo", "-json", "-so"]
    if where:
        args.extend(["-where", where])
    args.append(source)
    if layer_name:
        args.append(layer_name)
    try:
        payload = run_command(args, capture_json=True)
        layers = payload.get("layers", [])
        if not layers or layers[0].get("featureCount") is None:
            detail = f" layer {layer_name}" if layer_name else ""
            raise RuntimeError(f"Unable to determine feature count for{detail}: {source}")
        return int(layers[0]["featureCount"])
    except RuntimeError:
        return parse_text_feature_count(
            ogrinfo_text(
                source,
                layer_name=layer_name,
                where=where,
                all_layers=layer_name is None,
            ),
            source,
            layer_name,
        )


def expected_feature_count(source: str, layers: list[SourceLayer], where: str) -> int:
    return sum(feature_count(layer.source or source, layer.name, where) for layer in layers)


def build_filtered_gpkg(
    *,
    source: str,
    source_layers: list[SourceLayer],
    asset: AssetSpec,
    where: str,
    output: Path,
) -> None:
    remove_if_exists(output)
    for index, layer in enumerate(source_layers):
        args = ["ogr2ogr", "-f", "GPKG"]
        if index > 0:
            args.extend(["-update", "-append", "-addfields"])
        args.extend(
            [
                str(output),
                layer.source or source,
                layer.name,
                "-nln",
                asset.tile_layer,
                "-nlt",
                "GEOMETRY",
                "-where",
                where,
            ]
        )
        run_command(args)


def convert_gpkg_to_fgb(gpkg: Path, asset: AssetSpec, output: Path) -> None:
    remove_if_exists(output)
    run_command(
        [
            "ogr2ogr",
            "-f",
            "FlatGeobuf",
            str(output),
            str(gpkg),
            asset.tile_layer,
            "-nlt",
            "GEOMETRY",
        ]
    )


def convert_gpkg_to_geojsonseq(gpkg: Path, asset: AssetSpec, output: Path) -> None:
    remove_if_exists(output)
    run_command(
        [
            "ogr2ogr",
            "-f",
            "GeoJSONSeq",
            "-lco",
            "RS=NO",
            str(output),
            str(gpkg),
            asset.tile_layer,
        ]
    )


def build_pmtiles(geojsonseq: Path, asset: AssetSpec, output: Path) -> None:
    remove_if_exists(output)
    mbtiles = output.with_suffix(".mbtiles")
    remove_if_exists(mbtiles)
    zoom_args = shlex.split(os.environ.get("TIPPECANOE_ZOOM_ARGS", "-Z0 -z8"))
    extra_args = shlex.split(os.environ.get("TIPPECANOE_EXTRA_ARGS", ""))
    default_args = [
        "--drop-densest-as-needed",
        "--extend-zooms-if-still-dropping",
    ]
    run_command(
        [
            "tippecanoe",
            *zoom_args,
            "--projection=EPSG:4326",
            "--force",
            "-o",
            str(mbtiles),
            "-l",
            asset.tile_layer,
            "-P",
            *(extra_args or default_args),
            str(geojsonseq),
        ]
    )
    run_command(["pmtiles", "convert", str(mbtiles), str(output)])
    remove_if_exists(mbtiles)


def layer_fields(path: Path, layer_name: str | None = None) -> tuple[FieldSpec, ...]:
    args = ["ogrinfo", "-json", "-so", str(path)]
    if layer_name:
        args.append(layer_name)
    try:
        payload = run_command(args, capture_json=True)
        layers = parse_layers(payload)
    except RuntimeError:
        layers = parse_ogrinfo_text_layers(
            ogrinfo_text(str(path), layer_name=layer_name, all_layers=layer_name is None)
        )
    if not layers:
        raise RuntimeError(f"No layers found in output: {path}")
    return layers[0].fields


def validate_pmtiles(path: Path) -> None:
    if shutil.which("pmtiles"):
        run_command(["pmtiles", "show", str(path)])
    else:
        run_command(["tippecanoe-decode", "-S", str(path)], capture_json=True)


def build_asset_outputs(
    *,
    source: str,
    source_layers: list[SourceLayer],
    source_fields: tuple[FieldSpec, ...],
    asset: AssetSpec,
    where: str,
    workdir: Path,
    cleanup_after_gpkg: tuple[Path, ...] = (),
) -> AssetOutputs:
    expected_rows = expected_feature_count(source, source_layers, where)
    if expected_rows <= 0:
        raise RuntimeError(f"{asset.slug} filter produced no rows")

    gpkg = workdir / f"{asset.slug}.gpkg"
    fgb = workdir / f"{asset.slug}.fgb"
    geojsonseq = workdir / f"{asset.slug}.geojsonseq"
    pmtiles = workdir / f"{asset.slug}.pmtiles"

    build_filtered_gpkg(
        source=source,
        source_layers=source_layers,
        asset=asset,
        where=where,
        output=gpkg,
    )
    for path in cleanup_after_gpkg:
        LOGGER.info("removing source intermediate after filtered copy: %s", path)
        remove_if_exists(path)

    convert_gpkg_to_geojsonseq(gpkg, asset, geojsonseq)
    build_pmtiles(geojsonseq, asset, pmtiles)
    remove_if_exists(geojsonseq)

    convert_gpkg_to_fgb(gpkg, asset, fgb)

    actual_rows = feature_count(str(fgb))
    if actual_rows != expected_rows:
        raise RuntimeError(
            f"{asset.slug} row count mismatch: expected {expected_rows}, got {actual_rows}"
        )
    output_fields = layer_fields(fgb)
    if output_fields != source_fields:
        raise RuntimeError(f"{asset.slug} FGB schema does not match source schema")
    validate_pmtiles(pmtiles)
    remove_if_exists(gpkg)

    return AssetOutputs(
        fgb=fgb,
        pmtiles=pmtiles,
        row_count=actual_rows,
        sha256={
            "fgb": sha256_file(fgb),
            "pmtiles": sha256_file(pmtiles),
        },
    )


def publish_asset(
    *,
    publisher: GcsPublisher,
    asset: AssetSpec,
    outputs: AssetOutputs,
    run_date: dt.date,
    source_url: str,
    source_version: str,
    source_fields: tuple[FieldSpec, ...],
) -> dict[str, Any]:
    metadata = metadata_for_asset(
        asset=asset,
        run_date=run_date,
        source_version=source_version,
    )
    if publisher.successful_run_record(asset, run_date):
        LOGGER.info("%s already has a successful run record; skipping", asset.slug)
        return {
            "asset_slug": asset.slug,
            "status": "skipped",
            "release_index": publisher.record_existing_successful_release(asset, run_date),
            "latest_metadata": publisher.replace_latest_metadata_from_run_record(
                asset,
                run_date,
                metadata,
            ),
        }

    publisher.assert_no_partial_release(asset, run_date)

    release_fgb = publisher.upload_new_object(
        local_path=outputs.fgb,
        object_name=asset.release_object(run_date, ".fgb"),
        metadata=metadata,
    )
    release_pmtiles = publisher.upload_new_object(
        local_path=outputs.pmtiles,
        object_name=asset.release_object(run_date, ".pmtiles"),
        metadata=metadata,
    )
    latest_fgb = publisher.replace_latest_object(
        local_path=outputs.fgb,
        object_name=asset.latest_object(".fgb"),
        metadata=metadata,
    )
    latest_pmtiles = publisher.replace_latest_object(
        local_path=outputs.pmtiles,
        object_name=asset.latest_object(".pmtiles"),
        metadata=metadata,
    )

    record = {
        "record_version": RUN_RECORD_VERSION,
        "asset_slug": asset.slug,
        "run_date": run_date.isoformat(),
        "status": "success",
        "source": source_url,
        "source_version": source_version,
        "release_path": f"gs://{publisher.bucket.name}/{asset.release_prefix(run_date)}/",
        "release_paths": [release_fgb, release_pmtiles],
        "latest_paths": [latest_fgb, latest_pmtiles],
        "rows": outputs.row_count,
        "sha256": outputs.sha256,
        "field_count": len(source_fields),
        "notes": "Generated by simplified monthly WDPA job; fields preserved from source.",
    }
    run_record = publisher.write_run_record(
        asset=asset,
        run_date=run_date,
        payload=record,
    )
    record["run_record"] = run_record
    LOGGER.info("published %s", asset.slug)
    return record


def metadata_for_asset(
    *,
    asset: AssetSpec,
    run_date: dt.date,
    source_version: str,
) -> dict[str, str]:
    return {
        "asset_slug": asset.slug,
        "run_date": run_date.isoformat(),
        "source_version": source_version,
    }


def run() -> list[dict[str, Any]]:
    configure_logging()
    for binary in ("ogrinfo", "ogr2ogr", "tippecanoe", "pmtiles"):
        require_binary(binary)

    project_id = os.environ.get("GOOGLE_CLOUD_PROJECT", DEFAULT_PROJECT_ID)
    bucket_name = os.environ.get("SHARED_DATASETS_BUCKET", DEFAULT_BUCKET)
    raw_run_date = os.environ.get("RUN_DATE")
    run_date = parse_run_date(raw_run_date)
    attempt_date = run_date if raw_run_date else dt.datetime.now(dt.UTC).date()
    source_template = os.environ.get("WDPA_SOURCE_URL_TEMPLATE", DEFAULT_SOURCE_URL_TEMPLATE)
    source_url = build_source_url(source_template, run_date)
    source_version = source_version_for(run_date)
    sample_spec = parse_sample_spec()
    if sample_spec and os.environ.get("ALLOW_SAMPLED_PUBLISH") != "true":
        raise RuntimeError(
            "WDPA_SAMPLE_FRACTION is for local sandbox/testing only. "
            "Set ALLOW_SAMPLED_PUBLISH=true only if you intentionally want sampled GCS outputs."
        )

    publisher = GcsPublisher(storage.Client(project=project_id), bucket_name, logger=LOGGER)

    publish_specs = [
        asset
        for asset in ASSETS
        if not publisher.successful_run_record(asset, run_date)
    ]
    if not publish_specs:
        LOGGER.info("all WDPA assets already have successful run records for %s", run_date)
        records = []
        for asset in ASSETS:
            successful_release_index = publisher.record_existing_successful_release(
                asset,
                run_date,
            )
            latest_metadata = publisher.replace_latest_metadata_from_run_record(
                asset,
                run_date,
                metadata_for_asset(
                    asset=asset,
                    run_date=run_date,
                    source_version=source_version,
                ),
            )
            record = {
                "asset_slug": asset.slug,
                "run_date": attempt_date.isoformat(),
                "target_release_date": run_date.isoformat(),
                "status": "skipped",
                "reason": "monthly source already published",
                "source": source_url,
                "source_version": source_version,
            }
            if successful_release_index:
                record["successful_release_index"] = successful_release_index
            if latest_metadata:
                record["latest_metadata"] = latest_metadata
            record["release_index"] = publisher.update_latest_run_index(
                asset=asset,
                payload=record,
            )
            records.append(record)
        return records

    for asset in publish_specs:
        publisher.assert_no_partial_release(asset, run_date)

    with tempfile.TemporaryDirectory(prefix="wdpa-monthly-") as tmp:
        workdir = Path(tmp)
        source_zip = workdir / "wdpa.zip"
        try:
            download_file(source_url, source_zip)
        except SourceNotAvailableError as exc:
            LOGGER.info("%s", exc)
            records = []
            for asset in publish_specs:
                record = {
                    "asset_slug": asset.slug,
                    "run_date": attempt_date.isoformat(),
                    "target_release_date": run_date.isoformat(),
                    "status": "skipped",
                    "reason": str(exc),
                    "source": source_url,
                    "source_version": source_version,
                }
                record["release_index"] = publisher.update_latest_run_index(
                    asset=asset,
                    payload=record,
                )
                records.append(record)
            return records
        source_datasets = prepare_source_datasets(source_zip, workdir)
        source = source_datasets[0]
        source_layers, split_field, source_fields = discover_source_layers(source_datasets)
        assert_sample_field_available(source_layers, sample_spec)

        records = []
        final_publish_asset = publish_specs[-1]
        for asset in ASSETS:
            if asset not in publish_specs:
                record = {
                    "asset_slug": asset.slug,
                    "run_date": run_date.isoformat(),
                    "status": "skipped",
                }
                release_index_info = publisher.record_existing_successful_release(
                    asset,
                    run_date,
                )
                if release_index_info:
                    record["release_index"] = release_index_info
                latest_metadata = publisher.replace_latest_metadata_from_run_record(
                    asset,
                    run_date,
                    metadata_for_asset(
                        asset=asset,
                        run_date=run_date,
                        source_version=source_version,
                    ),
                )
                if latest_metadata:
                    record["latest_metadata"] = latest_metadata
                records.append(record)
                continue
            where = sampled_where_clause(asset_where_clause(asset, split_field), sample_spec)
            outputs = build_asset_outputs(
                source=source,
                source_layers=source_layers,
                source_fields=source_fields,
                asset=asset,
                where=where,
                workdir=workdir,
                cleanup_after_gpkg=(
                    (workdir / "source-zips",) if asset == final_publish_asset else ()
                ),
            )
            records.append(
                publish_asset(
                    publisher=publisher,
                    asset=asset,
                    outputs=outputs,
                    run_date=run_date,
                    source_url=source_url,
                    source_version=source_version,
                    source_fields=source_fields,
                )
            )
            remove_if_exists(outputs.fgb)
            remove_if_exists(outputs.pmtiles)
        return records


def main() -> None:
    try:
        records = run()
    except Exception:
        LOGGER.exception("wdpa monthly job failed")
        raise
    json.dump(records, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
