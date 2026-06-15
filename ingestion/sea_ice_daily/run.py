"""Daily IMS sea-ice extent publisher."""

from __future__ import annotations

import datetime as dt
import gzip
import json
import logging
import os
import re
import shutil
import sys
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from google.cloud import storage

from ingestion.common import feature_metadata
from ingestion.common.gcs import GcsPublisher
from ingestion.common.http import (
    STATUS_NOT_READY,
    STATUS_SUCCESS,
    STATUS_TRANSIENT,
    RequestOutcome,
    request_with_retries,
)
from ingestion.common.runtime import (
    configure_logging,
    remove_if_exists,
    require_binary,
    run_command as common_run_command,
    sha256_file,
)


LOGGER = logging.getLogger("sea_ice_daily")

DEFAULT_PROJECT_ID = "shared-datasets-1"
DEFAULT_BUCKET = "skytruth-shared-datasets-1"
DEFAULT_SOURCE_URL_TEMPLATE = (
    "https://noaadata.apps.nsidc.org/NOAA/G02156/GIS/4km/{yyyy}/{file_name}"
)
DEFAULT_MAX_LOOKBACK_DAYS = 14
REQUEST_TIMEOUT_SECONDS = 120
ICE_CLASS_VALUE = 3
ASSET_PARENT = "200-imagery-derived/250-weather-climate"
RUN_RECORD_VERSION = 1
REPROJECT_SEGMENTIZE_MAX_DISTANCE = 10_000
USER_AGENT = "shared-datasets-1-sea-ice-daily/1.0"
PMTILES_MINZOOM = 0
PMTILES_MAXZOOM = 8
PMTILES_PROPERTIES = (feature_metadata.FEATURE_ID_COLUMN,)
FEATURE_COUNT_RE = re.compile(r"^\s*Feature Count:\s*(\d+)\s*$", re.MULTILINE)
FIELD_LINE_RE = re.compile(
    r"^\s*([^:\s][^:]*):\s+"
    r"(Integer64|Integer|Real|String|DateTime|Date|Time|Binary)(?:\s|\(|$)"
)


@dataclass(frozen=True)
class AssetSpec:
    slug: str
    title: str
    tile_layer: str

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
class AvailableSource:
    filename_date: dt.date
    source_url: str
    source_filename: str

    @property
    def documented_valid_date(self) -> dt.date:
        return documented_valid_date_for_filename_date(self.filename_date)


@dataclass(frozen=True)
class SourceLookupResult:
    source: AvailableSource | None
    source_request_warnings: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class DownloadedSource:
    filename_date: dt.date
    source_url: str
    source_filename: str
    gz_path: Path

    @property
    def documented_valid_date(self) -> dt.date:
        return documented_valid_date_for_filename_date(self.filename_date)


@dataclass(frozen=True)
class AssetOutputs:
    fgb: Path
    pmtiles: Path
    metadata: Path
    schema: Path
    manifest: Path
    row_count: int
    sha256: dict[str, str]
    schema_payload: dict[str, Any]
    sidecar_records: tuple[dict[str, Any], ...]


ASSET = AssetSpec(
    slug="ims-sea-ice-extent",
    title="IMS Sea-Ice Extent",
    tile_layer="ims_sea_ice_extent",
)


def parse_run_date(value: str | None) -> dt.date:
    if not value:
        return dt.datetime.now(dt.UTC).date()
    return dt.date.fromisoformat(value)


def parse_positive_int(value: str | None, default: int, name: str) -> int:
    if value is None or value == "":
        return default
    parsed = int(value)
    if parsed <= 0:
        raise RuntimeError(f"{name} must be greater than 0")
    return parsed


def ims_filename_for_day(target_day: dt.date) -> str:
    yyyydoy = f"{target_day.year}{target_day.timetuple().tm_yday:03d}"
    return f"ims{yyyydoy}_4km_GIS_v1.3.tif.gz"


def documented_valid_date_for_filename_date(filename_date: dt.date) -> dt.date:
    return filename_date + dt.timedelta(days=1)


def source_url_for_day(source_template: str, target_day: dt.date) -> str:
    file_name = ims_filename_for_day(target_day)
    yyyydoy = f"{target_day.year}{target_day.timetuple().tm_yday:03d}"
    if any(
        token in source_template
        for token in ("{file_name}", "{yyyy}", "{year}", "{yyyydoy}")
    ):
        return source_template.format(
            file_name=file_name,
            yyyy=f"{target_day.year:04d}",
            year=target_day.year,
            yyyydoy=yyyydoy,
        )
    if source_template.endswith(".gz"):
        return source_template
    return f"{source_template.rstrip('/')}/{target_day.year}/{file_name}"


def iter_source_days(anchor_day: dt.date, max_lookback_days: int):
    for days_back in range(max_lookback_days):
        yield anchor_day - dt.timedelta(days=days_back)


def probe_source(source_url: str) -> RequestOutcome:
    request = urllib.request.Request(
        source_url,
        method="HEAD",
        headers={"User-Agent": USER_AGENT},
    )
    outcome, _payload = request_with_retries(
        request,
        timeout_seconds=REQUEST_TIMEOUT_SECONDS,
        not_ready_status_codes=frozenset({404}),
        opener=urllib.request.urlopen,
        logger=LOGGER,
    )
    return outcome


def find_latest_available_source(
    source_template: str,
    anchor_day: dt.date,
    max_lookback_days: int,
) -> SourceLookupResult:
    source_request_warnings: list[dict[str, Any]] = []
    for source_day in iter_source_days(anchor_day, max_lookback_days):
        source_url = source_url_for_day(source_template, source_day)
        LOGGER.info("probing IMS source %s", source_url)
        probe_result = probe_source(source_url)
        if probe_result.status == STATUS_SUCCESS:
            return SourceLookupResult(
                source=AvailableSource(
                    filename_date=source_day,
                    source_url=source_url,
                    source_filename=ims_filename_for_day(source_day),
                ),
                source_request_warnings=tuple(source_request_warnings),
            )
        if probe_result.status == STATUS_NOT_READY:
            continue
        if probe_result.status == STATUS_TRANSIENT:
            warning = {
                **probe_result.warning_payload(),
                "source_date": source_day.isoformat(),
            }
            LOGGER.warning("skipping transient IMS source probe failure: %s", warning)
            source_request_warnings.append(warning)
            continue
        raise RuntimeError(
            f"Source probe failed with {probe_result.reason or probe_result.status}: "
            f"{source_url}"
        )
    return SourceLookupResult(
        source=None,
        source_request_warnings=tuple(source_request_warnings),
    )


def add_source_request_warnings(
    record: dict[str, Any],
    source_request_warnings: tuple[dict[str, Any], ...] | list[dict[str, Any]],
) -> dict[str, Any]:
    if source_request_warnings:
        record["source_request_warnings"] = list(source_request_warnings)
    return record


def no_available_source_record(
    *,
    asset: AssetSpec,
    anchor_day: dt.date,
    max_lookback_days: int,
    source_request_warnings: tuple[dict[str, Any], ...],
) -> dict[str, Any]:
    reason = (
        f"No IMS 4 km GeoTIFF available from {anchor_day.isoformat()} "
        f"within the last {max_lookback_days} day(s)"
    )
    return add_source_request_warnings(
        {
            "schema_version": 1,
            "asset_slug": asset.slug,
            "run_date": anchor_day.isoformat(),
            "release_date": anchor_day.isoformat(),
            "status": "skipped",
            "reason": reason,
        },
        source_request_warnings,
    )


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
    LOGGER.info("downloading IMS source: %s", url)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    dest.parent.mkdir(parents=True, exist_ok=True)

    def write_response(response) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with dest.open("wb") as file_obj:
            shutil.copyfileobj(response, file_obj)

    outcome, _payload = request_with_retries(
        request,
        timeout_seconds=REQUEST_TIMEOUT_SECONDS,
        response_reader=write_response,
        opener=urllib.request.urlopen,
        logger=LOGGER,
    )
    if outcome.status != STATUS_SUCCESS:
        raise RuntimeError(
            f"Download failed with {outcome.reason or outcome.status}: {url}"
        )
    if dest.stat().st_size == 0:
        raise RuntimeError(f"Downloaded zero-byte IMS source file: {url}")


def download_source(source: AvailableSource, workdir: Path) -> DownloadedSource:
    gz_path = workdir / source.source_filename
    download_file(source.source_url, gz_path)
    return DownloadedSource(
        filename_date=source.filename_date,
        source_url=source.source_url,
        source_filename=source.source_filename,
        gz_path=gz_path,
    )


def gunzip_file(source_path: Path, destination: Path) -> None:
    remove_if_exists(destination)
    with gzip.open(source_path, "rb") as src, destination.open("wb") as dst:
        shutil.copyfileobj(src, dst)
    if destination.stat().st_size == 0:
        raise RuntimeError(f"Expanded zero-byte GeoTIFF: {destination}")


def build_ice_mask_raster(source_tif: Path, mask_tif: Path) -> None:
    remove_if_exists(mask_tif)
    run_command(
        [
            "gdal_calc.py",
            "-A",
            str(source_tif),
            f"--calc={ICE_CLASS_VALUE}*(A=={ICE_CLASS_VALUE})",
            "--type=Byte",
            "--NoDataValue=0",
            f"--outfile={mask_tif}",
            "--co=COMPRESS=DEFLATE",
            "--overwrite",
        ]
    )


def polygonize_ice_mask(mask_tif: Path, gpkg: Path) -> None:
    remove_if_exists(gpkg)
    run_command(
        [
            "gdal_polygonize.py",
            "-8",
            str(mask_tif),
            "-f",
            "GPKG",
            str(gpkg),
            ASSET.tile_layer,
            "DN",
        ]
    )


def filter_ice_polygons(raw_gpkg: Path, output: Path, source_date: dt.date) -> int:
    remove_if_exists(output)
    sql = (
        "SELECT "
        f"DN, '{source_date.isoformat()}' AS ice_date, geom "
        f"FROM {ASSET.tile_layer} "
        f"WHERE DN = {ICE_CLASS_VALUE}"
    )
    run_command(
        [
            "ogr2ogr",
            "-f",
            "GPKG",
            "-dialect",
            "SQLite",
            "-sql",
            sql,
            "-nln",
            ASSET.tile_layer,
            "-nlt",
            "PROMOTE_TO_MULTI",
            str(output),
            str(raw_gpkg),
        ]
    )
    row_count = feature_count(output)
    if row_count <= 0:
        raise RuntimeError(
            f"IMS source produced no class {ICE_CLASS_VALUE} sea/lake ice polygons"
        )
    return row_count


def convert_gpkg_to_fgb(gpkg: Path, output: Path) -> None:
    remove_if_exists(output)
    run_command(
        [
            "ogr2ogr",
            "-f",
            "FlatGeobuf",
            "-t_srs",
            "EPSG:4326",
            "-wrapdateline",
            "-segmentize",
            str(REPROJECT_SEGMENTIZE_MAX_DISTANCE),
            "-makevalid",
            "-nln",
            ASSET.tile_layer,
            "-nlt",
            "PROMOTE_TO_MULTI",
            str(output),
            str(gpkg),
            ASSET.tile_layer,
        ]
    )


def convert_fgb_to_geojsonseq(fgb: Path, output: Path) -> None:
    remove_if_exists(output)
    run_command(
        [
            "ogr2ogr",
            "-f",
            "GeoJSONSeq",
            "-lco",
            "RS=NO",
            str(output),
            str(fgb),
        ]
    )


def convert_geojsonseq_to_fgb(geojsonseq: Path, output: Path) -> None:
    remove_if_exists(output)
    run_command(
        [
            "ogr2ogr",
            "-f",
            "FlatGeobuf",
            "-nln",
            ASSET.tile_layer,
            "-nlt",
            "PROMOTE_TO_MULTI",
            str(output),
            f"GeoJSONSeq:{geojsonseq}",
        ]
    )


def build_pmtiles(geojsonseq: Path, output: Path) -> None:
    remove_if_exists(output)
    run_command(
        [
            "tippecanoe",
            f"--output={output}",
            "--force",
            "--quiet",
            f"--layer={ASSET.tile_layer}",
            f"--minimum-zoom={PMTILES_MINZOOM}",
            f"--maximum-zoom={PMTILES_MAXZOOM}",
            "--no-tile-size-limit",
            "--no-feature-limit",
            "--no-tile-compression",
            "--include",
            feature_metadata.FEATURE_ID_COLUMN,
            str(geojsonseq),
        ]
    )


def validate_pmtiles(path: Path) -> None:
    run_command(["pmtiles", "show", str(path)])


def ogrinfo_text(path: Path, layer_name: str | None = None) -> str:
    args = ["ogrinfo", "-so", str(path)]
    if layer_name:
        args.append(layer_name)
    return run_command(args, capture_text=True)


def parse_text_feature_count(text: str, path: Path) -> int:
    match = FEATURE_COUNT_RE.search(text)
    if not match:
        raise RuntimeError(f"Unable to determine feature count for {path}")
    return int(match.group(1))


def parse_text_layer_fields(text: str) -> tuple[str, ...]:
    fields = []
    for line in text.splitlines():
        match = FIELD_LINE_RE.match(line)
        if match:
            fields.append(match.group(1).strip())
    return tuple(fields)


def feature_count(path: Path) -> int:
    try:
        payload = run_command(
            ["ogrinfo", "-json", "-so", str(path), ASSET.tile_layer],
            capture_json=True,
        )
        layers = payload.get("layers", [])
        if not layers or layers[0].get("featureCount") is None:
            raise RuntimeError(f"Unable to determine feature count for {path}")
        return int(layers[0]["featureCount"])
    except RuntimeError:
        return parse_text_feature_count(ogrinfo_text(path, ASSET.tile_layer), path)


def layer_fields(path: Path) -> tuple[str, ...]:
    try:
        payload = run_command(
            ["ogrinfo", "-json", "-so", str(path), ASSET.tile_layer],
            capture_json=True,
        )
        layers = payload.get("layers", [])
        if not layers:
            raise RuntimeError(f"No layers found in {path}")
        return tuple(str(field.get("name", "")) for field in layers[0].get("fields", []))
    except RuntimeError:
        fields = parse_text_layer_fields(ogrinfo_text(path, ASSET.tile_layer))
        if not fields:
            raise RuntimeError(f"No fields found in {path}")
        return fields


def build_outputs(
    *,
    source_tif: Path,
    source_date: dt.date,
    workdir: Path,
    previous_records: Sequence[Mapping[str, Any]] | None = None,
    identity_resolution_decisions: Sequence[Mapping[str, Any]] = (),
) -> AssetOutputs:
    mask_tif = workdir / "ice-mask.tif"
    raw_gpkg = workdir / "ice-polygons.gpkg"
    filtered_gpkg = workdir / "ice-filtered.gpkg"
    fgb = workdir / f"{ASSET.slug}.fgb"
    normalized_fgb = workdir / f"{ASSET.slug}.normalized.fgb"
    geojsonseq = workdir / f"{ASSET.slug}.geojsonseq"
    enriched_geojsonseq = workdir / f"{ASSET.slug}.metadata.geojsonseq"
    pmtiles = workdir / f"{ASSET.slug}.pmtiles"
    metadata = workdir / f"{ASSET.slug}.metadata.ndjson.gz"
    schema = workdir / f"{ASSET.slug}.schema.json"
    manifest = workdir / f"{ASSET.slug}.manifest.json"

    build_ice_mask_raster(source_tif, mask_tif)
    polygonize_ice_mask(mask_tif, raw_gpkg)
    filter_ice_polygons(raw_gpkg, filtered_gpkg, source_date)
    convert_gpkg_to_fgb(filtered_gpkg, normalized_fgb)
    remove_if_exists(mask_tif)
    remove_if_exists(raw_gpkg)
    remove_if_exists(filtered_gpkg)

    actual_rows = feature_count(normalized_fgb)
    if actual_rows <= 0:
        raise RuntimeError(f"{ASSET.slug} output contains no features")

    fields = set(layer_fields(normalized_fgb))
    missing_fields = {"DN", "ice_date"} - fields
    if missing_fields:
        raise RuntimeError(
            f"{ASSET.slug} FGB missing required field(s): "
            + ", ".join(sorted(missing_fields))
        )

    convert_fgb_to_geojsonseq(normalized_fgb, geojsonseq)
    enriched_features, sidecar_records, ambiguities = feature_metadata.enrich_features_with_generated_ids(
        feature_metadata.iter_geojsonseq(geojsonseq),
        asset_slug=ASSET.slug,
        release=source_date.isoformat(),
        provenance={"source_date": source_date.isoformat(), "identity_strategy": "generated_sequence_content_hash"},
        previous_records=previous_records,
        identity_resolution_decisions=identity_resolution_decisions,
    )
    if ambiguities:
        feature_metadata.raise_unresolved_identity_ambiguities(
            asset_slug=ASSET.slug,
            release=source_date.isoformat(),
            ambiguities=ambiguities,
        )
    feature_metadata.write_geojsonseq(enriched_features, enriched_geojsonseq)
    feature_metadata.write_sidecar(sidecar_records, metadata)
    schema_payload = feature_metadata.schema_from_records(
        asset_slug=ASSET.slug,
        release=source_date.isoformat(),
        records=sidecar_records,
    )
    feature_metadata.write_schema(schema_payload, schema)
    convert_geojsonseq_to_fgb(enriched_geojsonseq, fgb)
    final_fields = set(layer_fields(fgb))
    missing_final_fields = {
        "DN",
        "ice_date",
        feature_metadata.FEATURE_ID_COLUMN,
        feature_metadata.GEOMETRY_HASH_COLUMN,
        feature_metadata.PROPERTIES_HASH_COLUMN,
    } - final_fields
    if missing_final_fields:
        raise RuntimeError(
            f"{ASSET.slug} FGB missing required field(s): "
            + ", ".join(sorted(missing_final_fields))
        )
    final_row_count = feature_count(fgb)
    build_pmtiles(enriched_geojsonseq, pmtiles)
    validate_pmtiles(pmtiles)
    feature_metadata.validate_release_vector_contract(
        fgb_path=fgb,
        pmtiles_path=pmtiles,
        decode_zoom=PMTILES_MINZOOM,
    )
    remove_if_exists(normalized_fgb)
    remove_if_exists(geojsonseq)
    remove_if_exists(enriched_geojsonseq)

    return AssetOutputs(
        fgb=fgb,
        pmtiles=pmtiles,
        metadata=metadata,
        schema=schema,
        manifest=manifest,
        row_count=final_row_count,
        sha256={
            "fgb": sha256_file(fgb),
            "pmtiles": sha256_file(pmtiles),
            "metadata": sha256_file(metadata),
            "schema": sha256_file(schema),
        },
        schema_payload=schema_payload,
        sidecar_records=tuple(sidecar_records),
    )


def publish_outputs(
    *,
    publisher: GcsPublisher,
    asset: AssetSpec,
    outputs: AssetOutputs,
    source: DownloadedSource | AvailableSource,
    source_request_warnings: tuple[dict[str, Any], ...] = (),
) -> dict[str, Any]:
    run_date = source.filename_date
    metadata = metadata_for_source(asset=asset, source=source)
    existing_record = load_existing_successful_release(
        publisher=publisher,
        asset=asset,
        run_date=run_date,
    )
    if existing_record is not None:
        LOGGER.info("%s already has a successful run record for %s", asset.slug, run_date)
        release_index_info = publisher.record_existing_successful_release(asset, run_date)
        return add_source_request_warnings(
            {
                "asset_slug": asset.slug,
                "run_date": run_date.isoformat(),
                "status": "skipped",
                "release_index": release_index_info,
            },
            source_request_warnings,
        )

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
    release_metadata = publisher.upload_new_object(
        local_path=outputs.metadata,
        object_name=asset.release_object(run_date, ".metadata.ndjson.gz"),
        metadata=metadata,
    )
    release_schema = publisher.upload_new_object(
        local_path=outputs.schema,
        object_name=asset.release_object(run_date, ".schema.json"),
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
    latest_metadata = publisher.replace_latest_object(
        local_path=outputs.metadata,
        object_name=asset.latest_object(".metadata.ndjson.gz"),
        metadata=metadata,
    )
    latest_schema = publisher.replace_latest_object(
        local_path=outputs.schema,
        object_name=asset.latest_object(".schema.json"),
        metadata=metadata,
    )
    manifest_release_object = asset.release_object(run_date, ".manifest.json")
    manifest_latest_object = asset.latest_object(".manifest.json")
    feature_metadata.write_manifest(
        feature_metadata.final_manifest_payload(
            asset_slug=asset.slug,
            release=run_date.isoformat(),
            bucket_name=publisher.bucket.name,
            asset_root=asset.root,
            sha256_by_role=outputs.sha256,
            schema=outputs.schema_payload,
            source_inputs=[{"uri": source.source_url, "source_filename": source.source_filename}],
            identity=feature_metadata.release_feature_model.build_identity_metadata(
                strategy="generated_sequence_content_hash",
                assignment_key=["geometry_hash", "properties_hash"],
                next_generated_feature_id_after_release=next_generated_feature_id(outputs.sidecar_records),
            ),
            feature_count=outputs.row_count,
            release_blob_info_by_role={
                "fgb": release_fgb,
                "pmtiles": release_pmtiles,
                "metadata": release_metadata,
                "schema": release_schema,
            },
            latest_blob_info_by_role={
                "fgb": latest_fgb,
                "pmtiles": latest_pmtiles,
                "metadata": latest_metadata,
                "schema": latest_schema,
            },
            manifest_release_path=f"gs://{publisher.bucket.name}/{manifest_release_object}",
            manifest_latest_path=f"gs://{publisher.bucket.name}/{manifest_latest_object}",
        ),
        outputs.manifest,
    )
    release_manifest = publisher.upload_new_object(
        local_path=outputs.manifest,
        object_name=manifest_release_object,
        metadata=metadata,
    )
    latest_manifest = publisher.replace_latest_object(
        local_path=outputs.manifest,
        object_name=manifest_latest_object,
        metadata=metadata,
    )
    sha256_values = {**outputs.sha256, "manifest": sha256_file(outputs.manifest)}

    record = add_source_request_warnings(
        {
            "schema_version": 1,
            "record_version": RUN_RECORD_VERSION,
            "asset_slug": asset.slug,
            "run_date": run_date.isoformat(),
            "release_date": run_date.isoformat(),
            "status": "success",
            "source": source.source_url,
            "source_url": source.source_url,
            "source_filename": source.source_filename,
            "source_filename_date": source.filename_date.isoformat(),
            "documented_valid_date": source.documented_valid_date.isoformat(),
            "source_version": source.source_filename,
            "release_path": f"gs://{publisher.bucket.name}/{asset.release_prefix(run_date)}/",
            "release_paths": [release_fgb, release_pmtiles, release_metadata, release_schema, release_manifest],
            "latest_paths": [latest_fgb, latest_pmtiles, latest_metadata, latest_schema, latest_manifest],
            "row_count": outputs.row_count,
            "sha256": sha256_values,
            "notes": (
                "Generated from raw IMS class 3, described by NSIDC as sea/lake ice. "
                "Release date and ice_date use the GeoTIFF filename date by repository "
                "decision; NSIDC documents GeoTIFF imagery as valid for the next day."
            ),
        },
        source_request_warnings,
    )
    run_record = publisher.write_run_record(
        asset=asset,
        run_date=run_date,
        payload=record,
    )
    record["run_record"] = run_record
    LOGGER.info("published %s for %s", asset.slug, run_date)
    return record


def load_existing_successful_release(
    *,
    publisher: GcsPublisher,
    asset: AssetSpec,
    run_date: dt.date,
) -> dict[str, Any] | None:
    loaded = publisher.load_successful_run_record(asset, run_date)
    if loaded is None:
        return None
    record, _run_record_info = loaded
    issue = publisher.release_metadata_contract_issue(asset, record)
    if issue:
        raise RuntimeError(
            f"{asset.slug} already has a successful run record for {run_date.isoformat()}, "
            f"but the release metadata contract is invalid: {issue}. "
            "Repair the release through a reviewed dataset publish plan before refreshing "
            "the release index."
        )
    return record


def metadata_for_source(
    *,
    asset: AssetSpec,
    source: DownloadedSource | AvailableSource,
) -> dict[str, str]:
    return {
        "asset_slug": asset.slug,
        "run_date": source.filename_date.isoformat(),
        "source_filename_date": source.filename_date.isoformat(),
        "documented_valid_date": source.documented_valid_date.isoformat(),
        "source_filename": source.source_filename,
        "source_url": source.source_url,
    }


def next_generated_feature_id(records: Sequence[Mapping[str, Any]]) -> int:
    numeric_ids = [
        int(str(record.get("feature_id") or ""))
        for record in records
        if str(record.get("feature_id") or "").isdigit()
    ]
    return max(numeric_ids, default=0) + 1


def run() -> dict[str, Any]:
    configure_logging()
    for binary in (
        "gdal_calc.py",
        "gdal_polygonize.py",
        "ogr2ogr",
        "ogrinfo",
        "tippecanoe",
        "pmtiles",
    ):
        require_binary(binary)

    project_id = os.environ.get("GOOGLE_CLOUD_PROJECT", DEFAULT_PROJECT_ID)
    bucket_name = os.environ.get("SHARED_DATASETS_BUCKET", DEFAULT_BUCKET)
    anchor_day = parse_run_date(os.environ.get("RUN_DATE"))
    source_template = os.environ.get(
        "SEA_ICE_SOURCE_URL_TEMPLATE",
        DEFAULT_SOURCE_URL_TEMPLATE,
    )
    max_lookback_days = parse_positive_int(
        os.environ.get("SEA_ICE_MAX_LOOKBACK_DAYS"),
        DEFAULT_MAX_LOOKBACK_DAYS,
        "SEA_ICE_MAX_LOOKBACK_DAYS",
    )

    publisher = GcsPublisher(storage.Client(project=project_id), bucket_name, logger=LOGGER)
    lookup = find_latest_available_source(
        source_template=source_template,
        anchor_day=anchor_day,
        max_lookback_days=max_lookback_days,
    )
    source_request_warnings = lookup.source_request_warnings
    if lookup.source is None:
        record = no_available_source_record(
            asset=ASSET,
            anchor_day=anchor_day,
            max_lookback_days=max_lookback_days,
            source_request_warnings=source_request_warnings,
        )
        LOGGER.info("%s", record["reason"])
        record["release_index"] = publisher.update_latest_run_index(
            asset=ASSET,
            payload=record,
        )
        return record

    available_source = lookup.source
    existing_record = load_existing_successful_release(
        publisher=publisher,
        asset=ASSET,
        run_date=available_source.filename_date,
    )
    if existing_record is not None:
        LOGGER.info(
            "%s already has a successful run record for %s",
            ASSET.slug,
            available_source.filename_date,
        )
        successful_release_index = publisher.record_existing_successful_release(
            ASSET,
            available_source.filename_date,
        )
        record = add_source_request_warnings(
            {
                "schema_version": 1,
                "asset_slug": ASSET.slug,
                "run_date": anchor_day.isoformat(),
                "release_date": available_source.filename_date.isoformat(),
                "status": "skipped",
                "reason": "latest available source already published",
                "source": available_source.source_url,
                "source_url": available_source.source_url,
                "source_filename": available_source.source_filename,
                "source_filename_date": available_source.filename_date.isoformat(),
                "documented_valid_date": available_source.documented_valid_date.isoformat(),
                "source_version": available_source.source_filename,
            },
            source_request_warnings,
        )
        if successful_release_index:
            record["successful_release_index"] = successful_release_index
        record["release_index"] = publisher.update_latest_run_index(
            asset=ASSET,
            payload=record,
        )
        return record
    publisher.assert_no_partial_release(ASSET, available_source.filename_date)

    with tempfile.TemporaryDirectory(prefix="sea-ice-daily-") as tmp:
        workdir = Path(tmp)
        downloaded = download_source(available_source, workdir)
        source_tif = workdir / downloaded.gz_path.with_suffix("").name
        gunzip_file(downloaded.gz_path, source_tif)
        remove_if_exists(downloaded.gz_path)

        outputs = build_outputs(
            source_tif=source_tif,
            source_date=downloaded.filename_date,
            workdir=workdir,
            previous_records=publisher.load_latest_metadata_records(ASSET),
            identity_resolution_decisions=feature_metadata.release_feature_model.load_identity_resolution_decisions(
                asset_slug=ASSET.slug,
                release=downloaded.filename_date.isoformat(),
            ),
        )
        return publish_outputs(
            publisher=publisher,
            asset=ASSET,
            outputs=outputs,
            source=downloaded,
            source_request_warnings=source_request_warnings,
        )


def main() -> None:
    try:
        record = run()
    except Exception:
        LOGGER.exception("sea ice daily job failed")
        raise
    json.dump(record, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
