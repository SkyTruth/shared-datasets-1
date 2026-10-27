"""Monthly OSMRE e-AMLIS publisher with upstream change detection."""

from __future__ import annotations

import datetime as dt
import csv
import json
import logging
import os
import re
import tempfile
import urllib.parse
import urllib.request
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from google.api_core.exceptions import NotFound, PreconditionFailed
from google.cloud import storage

from ingestion.common import feature_metadata
from ingestion.common import source_fingerprint as fingerprints
from ingestion.common.source_fingerprint import latest_success_record, digest as stable_hash
from ingestion.common import vector_pipeline
from ingestion.eamlis_monthly import source_quality as source_loss
from ingestion.eamlis_monthly import identity as keyed_identity, identity_state
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
from scripts import catalog_csv, feature_metadata_localization as localization
from scripts import feature_metadata_translation_reuse as translation_reuse


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
PMTILES_MINZOOM = 0
PMTILES_MAXZOOM = 8
PMTILES_PROPERTIES = (feature_metadata.FEATURE_ID_COLUMN,)
TRANSLATION_LOCALES, TRANSLATION_FIELDS = catalog_csv.translation_config(
    ASSET_SLUG, Path(__file__).resolve().parents[2] / "catalog/shared-datasets-catalog.csv",
)
RELEASE_SUFFIXES = (*feature_metadata.VECTOR_BUNDLE_SUFFIXES, ".metadata-translations.csv",
                    *(f".metadata.{locale}.ndjson.gz" for locale in TRANSLATION_LOCALES))


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

    @property
    def identity_metadata(self):
        return feature_metadata.release_feature_model.build_identity_metadata(strategy="source_field", source_fields=["OBJECTID"])


@dataclass(frozen=True)
class KeyedAssetOutput(AssetOutput):
    reservation: identity_state.Reservation

    @property
    def identity_metadata(self):
        return self.reservation.metadata


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
    return tuple(sorted(fields, key=lambda field: str(field["name"])))


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
    if stats.feature_count < 0:
        raise RuntimeError("e-AMLIS source returned a negative row count")

    editing_info = metadata.get("editingInfo") or {}
    return SourceState(
        layer_url=layer_url,
        where=where,
        service_item_id=str(metadata.get("serviceItemId") or ""),
        data_last_edit_date=optional_int(editing_info.get("dataLastEditDate")),
        schema_last_edit_date=optional_int(editing_info.get("schemaLastEditDate")),
        fields=fields,
        field_schema_hash=schema_hash,
        stats=stats,
        fingerprint_hash="",
        fingerprint={},
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
    identity_reservation: identity_state.Reservation | None = None,
) -> AssetOutput:
    fgb = workdir / f"{ASSET.slug}.fgb"
    pmtiles = workdir / f"{ASSET.slug}.pmtiles"
    metadata = workdir / f"{ASSET.slug}.metadata.ndjson.gz"
    schema = workdir / f"{ASSET.slug}.schema.json"
    manifest = workdir / f"{ASSET.slug}.manifest.json"
    enriched_geojsonseq = workdir / f"{ASSET.slug}.metadata.geojsonseq"
    if identity_reservation is None:
        enriched_features, sidecar_records = feature_metadata.enrich_features_with_source_field_ids(
            feature_metadata.iter_geojson_features(extract.geojson),
            asset_slug=ASSET.slug, release=release_date.isoformat(), id_field="OBJECTID",
            provenance={"source": source.layer_url, "where": source.where})
    else:
        enriched_features, sidecar_records = keyed_identity.enrich(
            feature_metadata.iter_geojson_features(extract.geojson), ledger=identity_reservation.ledger,
            asset_slug=ASSET.slug, release=release_date.isoformat(),
            provenance={"source": source.layer_url, "where": source.where, "identity_contract": keyed_identity.CONTRACT})
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
            | (set(keyed_identity.ASSIGNMENT_FIELDS) if identity_reservation is not None else set())
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

    values = dict(
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
    return AssetOutput(**values) if identity_reservation is None else KeyedAssetOutput(**values, reservation=identity_reservation)


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
    identity_contract: str | None = None,
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
    if identity_contract:
        record["identity_contract"] = identity_contract
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


def translation_input(publisher: GcsPublisher, workdir: Path, *, gate=None):
    translation_source = workdir / f"{ASSET.slug}.metadata-translations.csv"
    source_inputs = []
    if gate is not None and gate.state.get("identity", {}).get("phase") == "prepared":
        from ingestion.eamlis_monthly.identity_translations import prepare_translation_source
        return prepare_translation_source(gate, translation_source, fields=TRANSLATION_FIELDS, locales=TRANSLATION_LOCALES)
    blob = publisher.bucket.blob(ASSET.latest_object(".metadata-translations.csv"))
    try:
        blob.reload()
    except NotFound:
        with translation_source.open("w", encoding="utf-8", newline="") as handle:
            csv.writer(handle).writerow((*localization.REQUIRED_TRANSLATION_COLUMNS, *localization.OPTIONAL_TRANSLATION_COLUMNS))
    else:
        generation = int(blob.generation)
        blob.download_to_filename(str(translation_source), if_generation_match=generation)
        source_inputs.append({"uri": f"gs://{publisher.bucket.name}/{blob.name}", "generation": generation,
                              "sha256": sha256_file(translation_source)})
    return translation_source, source_inputs


def fingerprint_source(source: SourceState, extract: SourceExtract, translation_source: Path, *, identity_contract=None) -> SourceState:
    features = feature_metadata.iter_geojson_features(extract.geojson)
    if identity_contract is not None:
        keyed_identity.require(identity_contract == keyed_identity.CONTRACT, "unsupported EAMLIS fingerprint identity")
        features = ({**feature, "properties": {name: value for name, value in feature["properties"].items()
                    if name not in keyed_identity.EXCLUDED_PROPERTIES}} for feature in features)
    fingerprint = fingerprints.Fingerprint("eamlis-keyed-content-v1" if identity_contract else "eamlis-content-v1", {
        "layer_url": source.layer_url, "where": source.where,
        "service_item_id": source.service_item_id,
        "fields": sorted(source.fields, key=lambda field: str(field["name"])),
        "features": fingerprints.feature_digest(features),
        "translation_source": fingerprints.csv_digest(translation_source),
        "translation_fields": sorted(TRANSLATION_FIELDS), "translation_locales": sorted(TRANSLATION_LOCALES),
    })
    return replace(source, fingerprint=fingerprint.payload, fingerprint_hash=fingerprint.sha256)


def prepare_changed_asset(
    *,
    publisher: GcsPublisher,
    run_date: dt.date,
    source: SourceState,
    output: AssetOutput,
    translation_source: Path | None = None,
    translation_inputs: Sequence[Mapping[str, Any]] = (),
) -> tuple[SourceState, list[dict[str, Any]], Path, list[localization.LocalizationReport]]:
    # Prepare the whole language bundle before the first canonical write. A
    # missing source CSV is initial translation debt, not a failed dataset build.
    if translation_source is None:
        translation_source, translation_inputs = translation_input(publisher, output.metadata.parent)
    source_inputs = [{"uri": source.layer_url, "where": source.where}, *translation_inputs]
    reconciliation = translation_reuse.reconcile_translation_source(
        canonical_sidecar=output.metadata, translation_source=translation_source,
        fields=TRANSLATION_FIELDS, locales=TRANSLATION_LOCALES,
        asset_slug=ASSET.slug, release=run_date.isoformat(),
    )
    LOGGER.info("translation reconciliation: %s", reconciliation)
    fingerprint = fingerprints.Fingerprint(source.fingerprint["definition"], {
        **source.fingerprint["inputs"], "translation_source": fingerprints.csv_digest(translation_source),
    })
    source = replace(source, fingerprint=fingerprint.payload, fingerprint_hash=fingerprint.sha256)
    reports = localization.materialize_locale_sidecars(
        canonical_sidecar=output.metadata, translation_source=translation_source,
        output_dir=output.metadata.parent, locales=TRANSLATION_LOCALES,
        translatable_fields=set(TRANSLATION_FIELDS), expected_asset_slug=ASSET.slug,
        expected_release=run_date.isoformat(), protected_inputs=[output.schema],
    )
    return source, source_inputs, translation_source, reports


def publish_changed_asset(
    *, publisher: GcsPublisher, run_date: dt.date, source: SourceState,
    output: AssetOutput, translation_source: Path | None = None,
    translation_inputs: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    source, source_inputs, translation_source, reports = prepare_changed_asset(
        publisher=publisher, run_date=run_date, source=source, output=output,
        translation_source=translation_source, translation_inputs=translation_inputs)
    metadata = metadata_for_source(run_date=run_date, source=source)
    bundle = vector_pipeline.publish_vector_bundle(
        publisher=publisher,
        asset=ASSET,
        run_date=run_date,
        outputs=output,
        object_metadata=metadata,
        source_inputs=source_inputs,
        translations=localization.translations_payload(reports),
        extra_suffix_paths=[(".metadata-translations.csv", translation_source),
                            *((f".metadata.{report.locale}.ndjson.gz", Path(report.output_sidecar)) for report in reports)],
        identity=output.identity_metadata,
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
        "localization": localization.batch_report_payload(canonical_sidecar=output.metadata,
            translation_source=translation_source, output_dir=output.metadata.parent, reports=reports),
        "field_count": len(source.fields),
        "notes": "Generated by monthly e-AMLIS job from the public ArcGIS hosted feature layer.",
    }
    if isinstance(output, KeyedAssetOutput):
        record["identity_contract"] = keyed_identity.CONTRACT
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
) -> dict[str, Any] | None:
    if current_record is None:
        return None
    status = current_record.get("status")
    if status in {"success", "skipped"}:
        LOGGER.info(
            "%s already has a %s run record for %s; source changes require a different RUN_DATE",
            ASSET.slug, status, run_date,
        )
        return current_record
    raise RuntimeError(
        f"Run record already exists for {run_date.isoformat()} with status {status!r}. "
        "Investigate the existing record before choosing a different RUN_DATE."
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
    for binary in ("ogrinfo", "ogr2ogr", "pmtiles", "tippecanoe-decode"):
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

    current_record = load_current_run_record(publisher, ASSET, run_date)
    existing_record = assert_current_record_allows_run(
        current_record,
        run_date=run_date,
    )
    if existing_record:
        with source_loss.Guard(publisher.bucket) as gate:
            identity_state.require_run_date(gate, run_date.isoformat(), existing_record)
            if gate.held:
                return [gate.record()]
            gate.check_publication()
            if existing_record.get("status") == "success":
                existing_record = dict(existing_record)
                release_index_info = publisher.record_existing_successful_release(
                    ASSET,
                    run_date,
                )
                if release_index_info:
                    existing_record["release_index"] = release_index_info
            else:
                existing_record = dict(existing_record)
                blob = publisher.bucket.blob(ASSET.run_record_object(run_date))
                blob.reload()
                info = {"path": f"gs://{publisher.bucket.name}/{blob.name}", "generation": int(blob.generation), "size": int(blob.size or 0)}
                existing_record["release_index"] = publisher.update_latest_run_index(asset=ASSET, payload=existing_record, run_record_info=info)
            return [existing_record]

    with source_loss.Guard(publisher.bucket) as gate:
        reservation = None
        publication_started = False
        try:
            identity_state.require_run_date(gate, run_date.isoformat())
            publisher.assert_no_partial_release(ASSET, run_date, suffixes=RELEASE_SUFFIXES)
            previous_record = latest_success_record(publisher, ASSET, exclude_run_date=run_date)
            source = fetch_source_state(layer_url, where)
            selection = {"layer_url": source.layer_url, "where": source.where, "service_item_id": source.service_item_id}
            previous_inputs = (previous_record or {}).get("source_fingerprint", {}).get("inputs", {})
            previous_selection = ({key: previous_inputs.get(key) for key in selection}
                                  if "layer_url" in previous_inputs else None)
            gate.assess(source.stats.feature_count, selection=selection, previous_selection=previous_selection)
            if source.stats.feature_count == 0:
                gate.notify()
                return [gate.record()]
            work_root = Path(os.environ.get("SHARED_DATASETS_WORKDIR", str(Path(tempfile.gettempdir()) / "shared-datasets-1"))) / "vector-assets" / ASSET.slug
            work_root.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="monthly-", dir=work_root) as tmp:
                workdir = Path(tmp)
                extract = download_source_geojson(
                    source=source,
                    dest=workdir / f"{ASSET.slug}.geojson",
                    page_size=page_size,
                )
                gate.assess(extract.row_count, selection=selection, previous_selection=previous_selection)
                reservation = identity_state.reserve(gate,
                    (feature["properties"] for feature in feature_metadata.iter_geojson_features(extract.geojson)), workdir)
                translation_source, translation_inputs = translation_input(publisher, workdir, gate=gate)
                contract = keyed_identity.CONTRACT if reservation is not None else None
                source = fingerprint_source(source, extract, translation_source, identity_contract=contract)
                fingerprint = fingerprints.Fingerprint(source.fingerprint["definition"], source.fingerprint["inputs"])
                if not gate.held and fingerprint.matches(previous_record) and not needs_metadata_contract_refresh(publisher, previous_record):
                    return [skipped_record(publisher=publisher, run_date=run_date, source=source,
                                           previous_record=previous_record, reason="source fingerprint unchanged", identity_contract=contract)]
                identity_arguments = {"identity_reservation": reservation} if reservation is not None else {}
                output = build_asset_output(
                    source=source,
                    extract=extract,
                    workdir=workdir,
                    release_date=run_date,
                    # Legacy source IDs need no baseline. Installed keyed IDs
                    # come from the durable reservation, including absent keys.
                    previous_records=None,
                    **identity_arguments,
                )
                source_loss.require(output.row_count == extract.row_count,
                                    "built geometry count differs from complete source extract")
                if gate.held:
                    source, source_inputs, translation_source, reports = prepare_changed_asset(
                        publisher=publisher, run_date=run_date, source=source, output=output,
                        translation_source=translation_source, translation_inputs=translation_inputs)
                    source_loss.stage_candidate(gate, output=output, source=source, release=run_date.isoformat(),
                        source_inputs=source_inputs, translations=localization.translations_payload(reports),
                        extra_files=[(".metadata-translations.csv", translation_source),
                                     *((f".metadata.{r.locale}.ndjson.gz", Path(r.output_sidecar)) for r in reports)], extract=extract)
                    gate.notify()
                    return [gate.record()]
                gate.check_publication()
                publication_started = True
                record = publish_changed_asset(
                    publisher=publisher,
                    run_date=run_date,
                    source=source,
                    output=output,
                    translation_source=translation_source,
                    translation_inputs=translation_inputs,
                )
                if reservation is not None:
                    identity_state.activate(gate)
                remove_if_exists(output.fgb)
                remove_if_exists(output.pmtiles)
                return [record]
        except Exception as exc:
            if isinstance(exc, keyed_identity.IdentityError) or (reservation is not None and
                    (reservation.ledger.next_feature_id > reservation.before or publication_started)):
                observation = {"geometry_count": None, "error": str(exc)[:1000], "checked_at": source_loss.now()}
                gate.block(gate.state["hold"]["reason"] if gate.held else "identity_publication_requires_review", observation)
            if gate.held:
                observation = dict(gate.state["hold"]["observation"])
                observation.update(error=str(exc)[:1000], checked_at=source_loss.now())
                gate.block(gate.state["hold"]["reason"], observation)
                gate.notify()
            raise


def main() -> None:
    run_job_main(run, logger=LOGGER, failure_message="e-AMLIS monthly job failed")


if __name__ == "__main__":
    main()
