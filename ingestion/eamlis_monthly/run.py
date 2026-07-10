"""Monthly OSMRE e-AMLIS publisher with upstream change detection."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import os
import re
import tempfile
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from google.api_core.exceptions import PreconditionFailed
from google.cloud import storage

from ingestion.common import feature_metadata
from ingestion.common import release_index
from ingestion.common import vector_pipeline
from ingestion.common.gcs import GcsPublisher
from ingestion.common.http import STATUS_SUCCESS, request_with_retries
from ingestion.common.runtime import (
    bind_run_command,
    configure_logging,
    parse_positive_int,
    parse_run_date as parse_run_date,
    remove_if_exists,
    require_binary,
    run_job_main,
    sha256_file,
)


LOGGER = logging.getLogger("eamlis_monthly")

DEFAULT_PROJECT_ID = "shared-datasets-1"
DEFAULT_BUCKET = "skytruth-shared-datasets-1"
DEFAULT_LAYER_URL = (
    "https://services.arcgis.com/Vsy5ieu7PwNdunLd/arcgis/rest/services/"
    "eAMLISExternalView/FeatureServer/0"
)
DEFAULT_WHERE = "LAT_DEG > 0"
DEFAULT_PAGE_SIZE = 2000
REQUEST_TIMEOUT_SECONDS = 120
USER_AGENT = "shared-datasets-1-eamlis-monthly/1.0"
ASSET_PARENT = "300-infrastructure-industrial/320-mining"
ASSET_SLUG = "eamlis-abandoned-mine-land-inventory"
LAYER_NAME = "eamlis_abandoned_mine_land_inventory"
RUN_RECORD_VERSION = 1
RELEASE_SUFFIXES = feature_metadata.VECTOR_BUNDLE_SUFFIXES
PMTILES_MINZOOM = 0
PMTILES_MAXZOOM = 8
PMTILES_PROPERTIES = (feature_metadata.FEATURE_ID_COLUMN,)


@dataclass(frozen=True)
class AssetSpec(vector_pipeline.AssetPaths):
    slug: str = ASSET_SLUG
    parent: str = ASSET_PARENT


@dataclass(frozen=True)
class SourceStats:
    feature_count: int
    max_date_revised: int | None
    max_objectid: int | None


@dataclass(frozen=True)
class SourceState:
    layer_url: str
    where: str
    service_item_id: str
    data_last_edit_date: int | None
    schema_last_edit_date: int | None
    fields: tuple[dict[str, Any], ...]
    field_schema_hash: str
    stats: SourceStats
    fingerprint_hash: str
    fingerprint: dict[str, Any]


@dataclass(frozen=True)
class SourceExtract:
    geojson: Path
    row_count: int
    null_geometry_count: int


@dataclass(frozen=True)
class AssetOutput:
    fgb: Path
    pmtiles: Path
    metadata: Path
    schema: Path
    manifest: Path
    row_count: int
    sha256: dict[str, str]
    schema_payload: dict[str, Any]
    sidecar_records: tuple[dict[str, Any], ...]


ASSET = AssetSpec()


def parse_page_size(value: str | None) -> int:
    return parse_positive_int(value, DEFAULT_PAGE_SIZE, "EAMLIS_PAGE_SIZE")


def request_json(url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    query = urllib.parse.urlencode(params or {}, doseq=True)
    full_url = f"{url}?{query}" if query else url
    request = urllib.request.Request(
        full_url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    outcome, body = request_with_retries(
        request,
        timeout_seconds=REQUEST_TIMEOUT_SECONDS,
        response_reader=lambda response: response.read(),
        opener=urllib.request.urlopen,
        logger=LOGGER,
    )
    if outcome.status != STATUS_SUCCESS:
        raise RuntimeError(
            f"ArcGIS request failed with {outcome.reason or outcome.status}: {full_url}"
        )
    try:
        payload = json.loads((body or b"").decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"ArcGIS response was not JSON: {full_url}") from exc
    if "error" in payload:
        raise RuntimeError(f"ArcGIS returned an error for {full_url}: {payload['error']}")
    return payload


def stable_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def normalized_fields(metadata: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    fields = []
    for field in metadata.get("fields", []):
        if field.get("type") == "esriFieldTypeGeometry":
            continue
        fields.append(
            {
                "name": field.get("name"),
                "type": field.get("type"),
                "alias": field.get("alias"),
                "length": field.get("length"),
                "nullable": field.get("nullable"),
            }
        )
    return tuple(fields)


def field_names(fields: Iterable[dict[str, Any]]) -> list[str]:
    return [str(field["name"]) for field in fields if field.get("name")]


def date_field_names(fields: Iterable[dict[str, Any]]) -> set[str]:
    return {
        str(field["name"])
        for field in fields
        if field.get("name") and field.get("type") == "esriFieldTypeDate"
    }


def query_source_stats(layer_url: str, where: str) -> SourceStats:
    stats = [
        {
            "statisticType": "max",
            "onStatisticField": "DATE_REVISED",
            "outStatisticFieldName": "max_date_revised",
        },
        {
            "statisticType": "max",
            "onStatisticField": "OBJECTID",
            "outStatisticFieldName": "max_objectid",
        },
        {
            "statisticType": "count",
            "onStatisticField": "OBJECTID",
            "outStatisticFieldName": "feature_count",
        },
    ]
    payload = request_json(
        f"{layer_url}/query",
        {
            "f": "json",
            "where": where,
            "outStatistics": json.dumps(stats, separators=(",", ":")),
        },
    )
    features = payload.get("features") or []
    if not features:
        raise RuntimeError("ArcGIS statistics query returned no features")
    attributes = features[0].get("attributes") or {}
    return SourceStats(
        feature_count=int(attributes.get("feature_count") or 0),
        max_date_revised=optional_int(attributes.get("max_date_revised")),
        max_objectid=optional_int(attributes.get("max_objectid")),
    )


def optional_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


def fetch_source_state(layer_url: str, where: str) -> SourceState:
    metadata = request_json(layer_url, {"f": "json"})
    fields = normalized_fields(metadata)
    schema_hash = stable_hash(fields)
    stats = query_source_stats(layer_url, where)
    if stats.feature_count <= 0:
        raise RuntimeError(f"e-AMLIS source filter produced no rows: {where}")

    editing_info = metadata.get("editingInfo") or {}
    fingerprint = {
        "layer_url": layer_url,
        "where": where,
        "service_item_id": metadata.get("serviceItemId"),
        "data_last_edit_date": editing_info.get("dataLastEditDate"),
        "schema_last_edit_date": editing_info.get("schemaLastEditDate"),
        "field_schema_hash": schema_hash,
        "feature_count": stats.feature_count,
        "max_date_revised": stats.max_date_revised,
    }
    return SourceState(
        layer_url=layer_url,
        where=where,
        service_item_id=str(metadata.get("serviceItemId") or ""),
        data_last_edit_date=optional_int(editing_info.get("dataLastEditDate")),
        schema_last_edit_date=optional_int(editing_info.get("schemaLastEditDate")),
        fields=fields,
        field_schema_hash=schema_hash,
        stats=stats,
        fingerprint_hash=stable_hash(fingerprint),
        fingerprint=fingerprint,
    )


def arcgis_millis_to_date(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return dt.datetime.fromtimestamp(float(value) / 1000, tz=dt.UTC).date().isoformat()


def normalize_feature(
    feature: dict[str, Any],
    *,
    names: list[str],
    date_names: set[str],
) -> dict[str, Any]:
    source_properties = feature.get("properties") or {}
    properties = {}
    for name in names:
        value = source_properties.get(name)
        properties[name] = arcgis_millis_to_date(value) if name in date_names else value
    return {
        "type": "Feature",
        "id": feature.get("id"),
        "properties": properties,
        "geometry": feature.get("geometry"),
    }


def iter_source_pages(
    *,
    source: SourceState,
    page_size: int,
) -> Iterable[list[dict[str, Any]]]:
    fetched = 0
    while fetched < source.stats.feature_count:
        payload = request_json(
            f"{source.layer_url}/query",
            {
                "f": "geojson",
                "where": source.where,
                "outFields": "*",
                "returnGeometry": "true",
                "outSR": 4326,
                "orderByFields": "OBJECTID ASC",
                "resultOffset": fetched,
                "resultRecordCount": page_size,
            },
        )
        features = payload.get("features") or []
        if not features:
            raise RuntimeError(
                "ArcGIS query returned no features before expected count "
                f"({fetched}/{source.stats.feature_count})"
            )
        fetched += len(features)
        yield features


def download_source_geojson(
    *,
    source: SourceState,
    dest: Path,
    page_size: int,
) -> SourceExtract:
    names = field_names(source.fields)
    date_names = date_field_names(source.fields)
    row_count = 0
    null_geometry_count = 0
    first = True
    with dest.open("w", encoding="utf-8") as file_obj:
        file_obj.write('{"type":"FeatureCollection","features":[')
        for page in iter_source_pages(source=source, page_size=page_size):
            for raw_feature in page:
                feature = normalize_feature(raw_feature, names=names, date_names=date_names)
                if feature["geometry"] is None:
                    null_geometry_count += 1
                if not first:
                    file_obj.write(",")
                json.dump(feature, file_obj, separators=(",", ":"), ensure_ascii=False)
                first = False
                row_count += 1
        file_obj.write("]}\n")

    if row_count != source.stats.feature_count:
        raise RuntimeError(
            f"Downloaded row count mismatch: expected {source.stats.feature_count}, "
            f"got {row_count}"
        )
    if null_geometry_count:
        raise RuntimeError(f"Downloaded source contains {null_geometry_count} null geometries")
    return SourceExtract(
        geojson=dest,
        row_count=row_count,
        null_geometry_count=null_geometry_count,
    )


run_command = bind_run_command(LOGGER)


def convert_geojson_to_fgb(source: Path, output: Path) -> None:
    remove_if_exists(output)
    source_arg = f"GeoJSONSeq:{source}" if source.suffix == ".geojsonseq" else str(source)
    run_command(
        [
            "ogr2ogr",
            "-f",
            "FlatGeobuf",
            str(output),
            source_arg,
            "-nln",
            LAYER_NAME,
            "-t_srs",
            "EPSG:4326",
            "-lco",
            "SPATIAL_INDEX=YES",
        ]
    )


def convert_geojson_to_pmtiles(source: Path, output: Path) -> None:
    remove_if_exists(output)
    mbtiles = output.with_suffix(".mbtiles")
    remove_if_exists(mbtiles)
    run_command(
        [
            "ogr2ogr",
            "-f",
            "MBTiles",
            "-nln",
            LAYER_NAME,
            "-nlt",
            "PROMOTE_TO_MULTI",
            "-dsco",
            "NAME=OSMRE e-AMLIS Abandoned Mine Land Inventory",
            "-dsco",
            "DESCRIPTION=OSMRE e-AMLIS abandoned mine land inventory metadata lookup vector tiles",
            "-dsco",
            f"MINZOOM={PMTILES_MINZOOM}",
            "-dsco",
            f"MAXZOOM={PMTILES_MAXZOOM}",
            "-lco",
            f"NAME={LAYER_NAME}",
            "-lco",
            f"MINZOOM={PMTILES_MINZOOM}",
            "-lco",
            f"MAXZOOM={PMTILES_MAXZOOM}",
            "-select",
            ",".join(PMTILES_PROPERTIES),
            str(mbtiles),
            f"GeoJSONSeq:{source}",
        ]
    )
    run_command(["pmtiles", "convert", str(mbtiles), str(output)])
    remove_if_exists(mbtiles)
    if not output.exists() or output.stat().st_size <= 0:
        raise RuntimeError(f"PMTiles output is missing or empty: {output}")


FIELD_LINE_RE = re.compile(
    r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*:\s*"
    r"(Integer|Integer64|Real|String|Date|DateTime|Time|Binary|JSON|"
    r"IntegerList|Integer64List|RealList|StringList)\b"
)


def parse_ogrinfo_summary(text: str) -> dict[str, Any]:
    feature_count: int | None = None
    geometry_type = ""
    fields: list[str] = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("Feature Count:"):
            feature_count = int(line.split(":", 1)[1].strip())
            continue
        if line.startswith("Geometry:"):
            geometry_type = line.split(":", 1)[1].strip()
            continue
        field_match = FIELD_LINE_RE.match(raw_line)
        if field_match:
            fields.append(field_match.group(1))

    if feature_count is None:
        raise RuntimeError("Could not parse ogrinfo feature count")
    if not geometry_type:
        raise RuntimeError("Could not parse ogrinfo geometry type")

    return {
        "feature_count": feature_count,
        "geometry_type": geometry_type,
        "fields": fields,
    }


def output_layer_summary(path: Path) -> dict[str, Any]:
    text = run_command(["ogrinfo", "-so", "-al", str(path)], capture_text=True)
    return parse_ogrinfo_summary(text)


def build_asset_output(
    *,
    source: SourceState,
    extract: SourceExtract,
    workdir: Path,
    release_date: dt.date,
    previous_records: Sequence[Mapping[str, Any]] | None = None,
) -> AssetOutput:
    fgb = workdir / f"{ASSET.slug}.fgb"
    pmtiles = workdir / f"{ASSET.slug}.pmtiles"
    metadata = workdir / f"{ASSET.slug}.metadata.ndjson.gz"
    schema = workdir / f"{ASSET.slug}.schema.json"
    manifest = workdir / f"{ASSET.slug}.manifest.json"
    enriched_geojsonseq = workdir / f"{ASSET.slug}.metadata.geojsonseq"
    enriched_features, sidecar_records = feature_metadata.enrich_features_with_source_field_ids(
        feature_metadata.iter_geojson_features(extract.geojson),
        asset_slug=ASSET.slug,
        release=release_date.isoformat(),
        id_field="OBJECTID",
        provenance={"source": source.layer_url, "where": source.where},
    )
    feature_metadata.write_geojsonseq(enriched_features, enriched_geojsonseq)
    feature_metadata.write_sidecar(sidecar_records, metadata)
    schema_payload = feature_metadata.schema_from_records(
        asset_slug=ASSET.slug,
        release=release_date.isoformat(),
        records=sidecar_records,
    )
    feature_metadata.write_schema(schema_payload, schema)

    convert_geojson_to_fgb(enriched_geojsonseq, fgb)
    summary = output_layer_summary(fgb)
    if summary["feature_count"] != source.stats.feature_count:
        raise RuntimeError(
            f"FGB row count mismatch: expected {source.stats.feature_count}, "
            f"got {summary['feature_count']}"
        )
    if "Point" not in summary["geometry_type"]:
        raise RuntimeError(f"Expected point FGB geometry, got {summary['geometry_type']}")

    missing_fields = sorted(
        (
            set(field_names(source.fields))
            | {
                feature_metadata.FEATURE_ID_COLUMN,
                feature_metadata.GEOMETRY_HASH_COLUMN,
                feature_metadata.PROPERTIES_HASH_COLUMN,
            }
        )
        - set(summary["fields"])
    )
    if missing_fields:
        raise RuntimeError("FGB output is missing source fields: " + ", ".join(missing_fields))

    convert_geojson_to_pmtiles(enriched_geojsonseq, pmtiles)
    feature_metadata.validate_release_vector_contract(
        fgb_path=fgb,
        pmtiles_path=pmtiles,
        decode_zoom=PMTILES_MINZOOM,
    )
    remove_if_exists(enriched_geojsonseq)

    return AssetOutput(
        fgb=fgb,
        pmtiles=pmtiles,
        metadata=metadata,
        schema=schema,
        manifest=manifest,
        row_count=summary["feature_count"],
        sha256={
            "fgb": sha256_file(fgb),
            "pmtiles": sha256_file(pmtiles),
            "metadata": sha256_file(metadata),
            "schema": sha256_file(schema),
        },
        schema_payload=schema_payload,
        sidecar_records=tuple(sidecar_records),
    )


def latest_success_record(
    publisher: GcsPublisher,
    asset: AssetSpec,
    *,
    exclude_run_date: dt.date | None = None,
) -> dict[str, Any] | None:
    index = publisher.load_json(release_index.release_index_object(asset.slug)) or {}
    latest_release = index.get("latest_release")
    if not isinstance(latest_release, dict):
        return None

    latest_run = index.get("latest_run")
    run_record_path = latest_release.get("run_record_path")
    if not run_record_path and isinstance(latest_run, dict) and latest_run.get("date") == latest_release.get("date"):
        run_record_path = latest_run.get("run_record_path")
    if not run_record_path:
        return None

    try:
        bucket_name, object_name = release_index.split_gs_uri(str(run_record_path))
    except release_index.ReleaseIndexError:
        return None
    if bucket_name != publisher.bucket.name:
        return None

    excluded_name = asset.run_record_object(exclude_run_date) if exclude_run_date else None
    if excluded_name and object_name == excluded_name:
        return None

    record = publisher.load_json(object_name)
    if record and record.get("status") == "success":
        return record
    return None


def load_current_run_record(
    publisher: GcsPublisher,
    asset: AssetSpec,
    run_date: dt.date,
) -> dict[str, Any] | None:
    return publisher.load_json(asset.run_record_object(run_date))


def write_run_record_once(
    *,
    publisher: GcsPublisher,
    asset: AssetSpec,
    run_date: dt.date,
    payload: dict[str, Any],
) -> dict[str, Any]:
    object_name = asset.run_record_object(run_date)
    blob = publisher.bucket.blob(object_name)
    blob.metadata = {
        "asset_slug": asset.slug,
        "run_date": run_date.isoformat(),
    }
    serialized = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    try:
        blob.upload_from_string(
            serialized,
            content_type="application/json",
            if_generation_match=0,
        )
    except PreconditionFailed as exc:
        existing = publisher.load_json(object_name)
        if existing and existing == payload:
            blob.reload()
        else:
            raise RuntimeError(
                f"Run record already exists with different content: "
                f"gs://{publisher.bucket.name}/{object_name}"
            ) from exc
    blob.reload()
    return {
        "path": f"gs://{publisher.bucket.name}/{object_name}",
        "generation": int(blob.generation),
        "size": int(blob.size or 0),
    }


def skipped_record(
    *,
    publisher: GcsPublisher,
    run_date: dt.date,
    source: SourceState,
    previous_record: dict[str, Any] | None,
    reason: str,
) -> dict[str, Any]:
    record = {
        "schema_version": 1,
        "record_version": RUN_RECORD_VERSION,
        "asset_slug": ASSET.slug,
        "run_date": run_date.isoformat(),
        "release_date": run_date.isoformat(),
        "status": "skipped",
        "reason": reason,
        "source": source.layer_url,
        "source_version": source.fingerprint_hash,
        "source_fingerprint": source.fingerprint,
        "source_fingerprint_hash": source.fingerprint_hash,
        "source_stats": {
            "feature_count": source.stats.feature_count,
            "max_date_revised": source.stats.max_date_revised,
            "max_objectid": source.stats.max_objectid,
        },
        "release_path": (previous_record or {}).get("release_path", ""),
        "latest_paths": (previous_record or {}).get("latest_paths", []),
        "row_count": source.stats.feature_count,
        "notes": "Generated by monthly e-AMLIS job; source unchanged.",
    }
    run_record = write_run_record_once(
        publisher=publisher,
        asset=ASSET,
        run_date=run_date,
        payload=record,
    )
    record["run_record"] = run_record
    record["release_index"] = publisher.update_latest_run_index(
        asset=ASSET,
        payload=record,
        run_record_info=run_record,
    )
    return record


def publish_changed_asset(
    *,
    publisher: GcsPublisher,
    run_date: dt.date,
    source: SourceState,
    output: AssetOutput,
) -> dict[str, Any]:
    metadata = metadata_for_source(run_date=run_date, source=source)
    bundle = vector_pipeline.publish_vector_bundle(
        publisher=publisher,
        asset=ASSET,
        run_date=run_date,
        outputs=output,
        object_metadata=metadata,
        source_inputs=[{"uri": source.layer_url, "where": source.where}],
        identity=feature_metadata.release_feature_model.build_identity_metadata(
            strategy="source_field",
            source_fields=["OBJECTID"],
        ),
    )

    record = {
        "schema_version": 1,
        "record_version": RUN_RECORD_VERSION,
        "asset_slug": ASSET.slug,
        "run_date": run_date.isoformat(),
        "release_date": run_date.isoformat(),
        "status": "success",
        "source": source.layer_url,
        "source_version": source.fingerprint_hash,
        "source_fingerprint": source.fingerprint,
        "source_fingerprint_hash": source.fingerprint_hash,
        "source_stats": {
            "feature_count": source.stats.feature_count,
            "max_date_revised": source.stats.max_date_revised,
            "max_objectid": source.stats.max_objectid,
        },
        "release_path": f"gs://{publisher.bucket.name}/{ASSET.release_prefix(run_date)}/",
        "release_paths": bundle.release_paths,
        "latest_paths": bundle.latest_paths,
        "row_count": output.row_count,
        "sha256": bundle.sha256,
        "field_count": len(source.fields),
        "notes": "Generated by monthly e-AMLIS job from the public ArcGIS hosted feature layer.",
    }
    run_record = publisher.write_run_record(asset=ASSET, run_date=run_date, payload=record)
    record["run_record"] = run_record
    LOGGER.info("published %s", ASSET.slug)
    return record


def metadata_for_source(*, run_date: dt.date, source: SourceState) -> dict[str, str]:
    return {
        "asset_slug": ASSET.slug,
        "run_date": run_date.isoformat(),
        "source_fingerprint_hash": source.fingerprint_hash,
    }


def assert_current_record_allows_run(
    current_record: dict[str, Any] | None,
    *,
    run_date: dt.date,
    source: SourceState,
) -> dict[str, Any] | None:
    if not current_record:
        return None
    status = current_record.get("status")
    if status == "success":
        LOGGER.info("%s already has a successful run record for %s", ASSET.slug, run_date)
        return current_record
    if (
        status == "skipped"
        and current_record.get("source_fingerprint_hash") == source.fingerprint_hash
    ):
        LOGGER.info("%s already has a skipped run record for %s", ASSET.slug, run_date)
        return current_record
    raise RuntimeError(
        f"Run record already exists for {run_date.isoformat()} with status {status!r}. "
        "Use a different RUN_DATE if this source state should be published."
    )


def needs_metadata_contract_refresh(
    publisher: GcsPublisher,
    record: dict[str, Any] | None,
) -> bool:
    if not record:
        return False
    issue = publisher.release_metadata_contract_issue(ASSET, record)
    if issue:
        LOGGER.info(
            "%s previous release metadata contract needs refresh: %s",
            ASSET.slug,
            issue,
        )
        return True
    return False


def run() -> list[dict[str, Any]]:
    configure_logging()
    for binary in ("ogrinfo", "ogr2ogr", "pmtiles"):
        require_binary(binary)

    project_id = os.environ.get("GOOGLE_CLOUD_PROJECT", DEFAULT_PROJECT_ID)
    bucket_name = os.environ.get("SHARED_DATASETS_BUCKET", DEFAULT_BUCKET)
    run_date = parse_run_date(os.environ.get("RUN_DATE"))
    layer_url = os.environ.get("EAMLIS_LAYER_URL", DEFAULT_LAYER_URL).rstrip("/")
    where = os.environ.get("EAMLIS_WHERE", DEFAULT_WHERE)
    page_size = parse_page_size(os.environ.get("EAMLIS_PAGE_SIZE"))

    publisher = GcsPublisher(
        storage.Client(project=project_id),
        bucket_name,
        release_suffixes=RELEASE_SUFFIXES,
        logger=LOGGER,
    )

    source = fetch_source_state(layer_url, where)
    current_record = load_current_run_record(publisher, ASSET, run_date)
    existing_record = assert_current_record_allows_run(
        current_record,
        run_date=run_date,
        source=source,
    )
    if existing_record:
        if existing_record.get("status") == "success":
            existing_record = dict(existing_record)
            release_index_info = publisher.record_existing_successful_release(
                ASSET,
                run_date,
            )
            if release_index_info:
                existing_record["release_index"] = release_index_info
        return [existing_record]

    previous_record = latest_success_record(publisher, ASSET, exclude_run_date=run_date)
    contract_refresh = needs_metadata_contract_refresh(publisher, previous_record)
    if previous_record and previous_record.get("source_fingerprint_hash") == source.fingerprint_hash:
        if contract_refresh:
            LOGGER.info(
                "%s source fingerprint unchanged, but previous release lacks metadata contract; publishing refresh",
                ASSET.slug,
            )
        else:
            LOGGER.info("%s source fingerprint unchanged; skipping", ASSET.slug)
            return [
                skipped_record(
                    publisher=publisher,
                    run_date=run_date,
                    source=source,
                    previous_record=previous_record,
                    reason="source fingerprint unchanged",
                )
            ]

    publisher.assert_no_partial_release(ASSET, run_date, suffixes=RELEASE_SUFFIXES)

    with tempfile.TemporaryDirectory(prefix="eamlis-monthly-") as tmp:
        workdir = Path(tmp)
        extract = download_source_geojson(
            source=source,
            dest=workdir / f"{ASSET.slug}.geojson",
            page_size=page_size,
        )
        output = build_asset_output(
            source=source,
            extract=extract,
            workdir=workdir,
            release_date=run_date,
            # EAMLIS feature IDs are copied directly from the source OBJECTID
            # field, so prior generated-ID mappings are not needed. Older live
            # EAMLIS sidecars used a retired feature_id shape and must not block
            # a source-field contract refresh.
            previous_records=None,
        )
        previous_sha = ((previous_record or {}).get("sha256") or {}).get("fgb")
        if previous_sha and previous_sha == output.sha256["fgb"] and not contract_refresh:
            LOGGER.info("%s output hash unchanged; skipping", ASSET.slug)
            return [
                skipped_record(
                    publisher=publisher,
                    run_date=run_date,
                    source=source,
                    previous_record=previous_record,
                    reason="generated FGB hash unchanged",
                )
            ]
        record = publish_changed_asset(
            publisher=publisher,
            run_date=run_date,
            source=source,
            output=output,
        )
        remove_if_exists(output.fgb)
        remove_if_exists(output.pmtiles)
        return [record]


def main() -> None:
    run_job_main(run, logger=LOGGER, failure_message="e-AMLIS monthly job failed")


if __name__ == "__main__":
    main()
