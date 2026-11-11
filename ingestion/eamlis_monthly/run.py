"""Monthly OSMRE e-AMLIS publisher with upstream change detection."""

from __future__ import annotations

import datetime as dt
import csv
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import urllib.parse
import urllib.request
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from google.api_core.exceptions import NotFound
from google.cloud import storage

from ingestion.common import feature_metadata, lifecycle, publication_readiness, published_baseline
from ingestion.common import source_fingerprint as fingerprints
from ingestion.common.source_fingerprint import digest as stable_hash
from ingestion.common import vector_pipeline
from ingestion.common.source_quality import EAMLIS as source_loss
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

    @property
    def geometry_hash_algorithm(self):
        return feature_metadata.release_feature_model.GEOMETRY_HASH_ALGORITHM


@dataclass(frozen=True)
class KeyedAssetOutput(AssetOutput):
    reservation: identity_state.Reservation

    @property
    def identity_metadata(self):
        return self.reservation.metadata

    @property
    def geometry_hash_algorithm(self):
        return feature_metadata.release_feature_model.GEOMETRY_HASH_ALGORITHM_V2


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


def stage_inputs(source, release_date, identity_reservation):
    return {"asset_slug": ASSET.slug, "release": release_date.isoformat(), "source": source.layer_url,
        "where": source.where, "fields": source.fields, "expected_rows": source.stats.feature_count,
        "reservation": identity_reservation.reference if identity_reservation is not None else None}


def build_asset_output(
    *, source, extract, workdir, release_date, identity_reservation=None,
):
    from ingestion.common.stage_checkpoints import cached_build, native_tools
    inputs = {**stage_inputs(source, release_date, identity_reservation),
        "tools": native_tools(("ogr2ogr", "ogrinfo", "tippecanoe-decode", "pmtiles"))}
    return cached_build("eamlis-artifacts", inputs, workdir,
        lambda directory: _build_asset_output(source=source, extract=extract, workdir=directory,
            release_date=release_date, identity_reservation=identity_reservation),
        files={"source": extract.geojson}, result_type=AssetOutput if identity_reservation is None else KeyedAssetOutput,
        extra={} if identity_reservation is None else {"reservation": identity_reservation},
        code=("ingestion/eamlis_monthly/run.py", "ingestion/eamlis_monthly/identity.py",
            "ingestion/common/feature_metadata.py", "ingestion/common/runtime.py",
            "scripts/pmtiles_zoom.py", "scripts/release_feature_model.py", "scripts/vector_asset.py"))


def _prepare_metadata(source, extract, workdir, release_date, identity_reservation):
    metadata = workdir / f"{ASSET.slug}.metadata.ndjson.gz"
    schema = workdir / f"{ASSET.slug}.schema.json"
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
    return enriched_geojsonseq, metadata, schema, schema_payload, tuple(sidecar_records)


def prepare_metadata(source, extract, workdir, release_date, identity_reservation):
    from ingestion.common.stage_checkpoints import cached_build
    return cached_build(
        "eamlis-metadata", stage_inputs(source, release_date, identity_reservation), workdir,
        lambda directory: _prepare_metadata(source, extract, directory, release_date, identity_reservation),
        files={"source": extract.geojson}, result_type=None,
        code=("ingestion/eamlis_monthly/run.py", "ingestion/eamlis_monthly/identity.py",
            "ingestion/common/feature_metadata.py", "scripts/release_feature_model.py"))


def _build_asset_output(
    *, source: SourceState, extract: SourceExtract, workdir: Path, release_date: dt.date,
    identity_reservation: identity_state.Reservation | None = None,
) -> AssetOutput:
    enriched_geojsonseq, metadata, schema, schema_payload, sidecar_records = prepare_metadata(
        source, extract, workdir, release_date, identity_reservation)
    fgb = workdir / f"{ASSET.slug}.fgb"
    pmtiles = workdir / f"{ASSET.slug}.pmtiles"
    manifest = workdir / f"{ASSET.slug}.manifest.json"

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
    fingerprint = fingerprints.Fingerprint("eamlis-keyed-content-v2" if identity_contract else "eamlis-content-v1", {
        **({"geometry_hash_algorithm": feature_metadata.release_feature_model.GEOMETRY_HASH_ALGORITHM_V2} if identity_contract else {}),
        "layer_url": source.layer_url, "where": source.where,
        "service_item_id": source.service_item_id,
        "fields": sorted(source.fields, key=lambda field: str(field["name"])),
        "features": fingerprints.feature_digest(features),
        "translation_source": fingerprints.csv_digest(translation_source),
        "translation_fields": sorted(TRANSLATION_FIELDS), "translation_locales": sorted(TRANSLATION_LOCALES),
    })
    return replace(source, fingerprint=fingerprint.payload, fingerprint_hash=fingerprint.sha256)


def reconcile_translations(metadata: Path, translation_source: Path, release: dt.date) -> None:
    reconciliation = translation_reuse.reconcile_translation_source(
        canonical_sidecar=metadata, translation_source=translation_source,
        fields=TRANSLATION_FIELDS, locales=TRANSLATION_LOCALES,
        asset_slug=ASSET.slug, release=release.isoformat(),
    )
    LOGGER.info("translation reconciliation: %s", reconciliation)


def prepare_changed_asset(output: AssetOutput, translation_source: Path, release: dt.date):
    # The reconciled source is already fingerprinted by describe. Materialize the
    # complete language bundle before either staging or the first canonical write.
    return localization.materialize_locale_sidecars(
        canonical_sidecar=output.metadata, translation_source=translation_source,
        output_dir=output.metadata.parent, locales=TRANSLATION_LOCALES,
        translatable_fields=set(TRANSLATION_FIELDS), expected_asset_slug=ASSET.slug,
        expected_release=release.isoformat(), protected_inputs=[output.schema],
    )


def publish_changed_asset(
    *, publisher: GcsPublisher, record: dict[str, Any], output: AssetOutput,
    translation_source: Path, source_inputs: Sequence[Mapping[str, Any]],
    reports: Sequence[localization.LocalizationReport],
) -> dict[str, Any]:
    release = dt.date.fromisoformat(record["release_date"])
    bundle = vector_pipeline.publish_vector_bundle(
        publisher=publisher, asset=ASSET, run_date=release, outputs=output,
        object_metadata={"asset_slug": ASSET.slug, "run_date": record["run_date"],
                         "source_fingerprint_hash": record["source_fingerprint_hash"]},
        source_inputs=source_inputs, translations=localization.translations_payload(reports),
        extra_suffix_paths=[(".metadata-translations.csv", translation_source),
                            *((f".metadata.{r.locale}.ndjson.gz", Path(r.output_sidecar)) for r in reports)],
        identity=output.identity_metadata, geometry_hash_algorithm=output.geometry_hash_algorithm,
    )
    record.update(release_paths=bundle.release_paths, latest_paths=bundle.latest_paths, sha256=bundle.sha256,
        localization=localization.batch_report_payload(canonical_sidecar=output.metadata,
            translation_source=translation_source, output_dir=output.metadata.parent, reports=reports))
    record["run_record"] = publisher.write_run_record(asset=ASSET,
        run_date=dt.date.fromisoformat(record["run_date"]), payload=record)
    return record


def toolchain() -> dict[str, dict[str, str]]:
    tools = {}
    for name, arg in (("ogr2ogr", "--version"), ("ogrinfo", "--version"),
                      ("pmtiles", "version"), ("tippecanoe-decode", "--version")):
        require_binary(name)
        path = str(Path(shutil.which(name)).resolve())
        # The decoder has no version flag; its sibling executable identifies
        # the installed suite that supplied it.
        version_path = str(Path(path).with_name("tippecanoe")) if name == "tippecanoe-decode" else path
        completed = subprocess.run([version_path, arg], check=True, text=True,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        version = completed.stdout.strip()
        if not version:
            raise RuntimeError(f"Native tool returned no version: {path}")
        tools[name] = {"path": path, "version": version}
    return tools


class Refresh:
    """The e-AMLIS stages of lifecycle.run; ordering and gate rules live there."""
    slug = ASSET.slug

    def __init__(self, publisher: GcsPublisher, workdir: Path, *, layer_url: str, where: str, page_size: int):
        self.publisher, self.workdir = publisher, workdir
        self.layer_url, self.where, self.page_size = layer_url, where, page_size
        self.loss_limit = lifecycle.loss_limit(self.slug)
        self.reservation = None
        self.publication_started = False

    def resume(self, attempt: dt.date) -> dict[str, Any] | None:
        existing = self.publisher.load_json(ASSET.run_record_object(attempt))
        identity_state.require_run_date(self.gate, attempt.isoformat(), existing)
        if existing is None:
            self.publisher.assert_no_partial_release(ASSET, attempt, suffixes=RELEASE_SUFFIXES)
            return None
        if self.gate.held:
            self.gate.notify()
            return self.gate.record()
        if existing.get("status") not in {"success", "skipped"}:
            raise RuntimeError(f"Run record already exists for {attempt} with status {existing.get('status')!r}; reviewed investigation required")
        self.gate.check_publication()
        existing = dict(existing)
        if existing["status"] == "success":
            info = self.publisher.record_existing_successful_release(ASSET, attempt)
            if info:
                existing["release_index"] = info
        else:
            blob = self.publisher.bucket.blob(ASSET.run_record_object(attempt))
            blob.reload()
            info = {"path": f"gs://{self.publisher.bucket.name}/{blob.name}",
                    "generation": int(blob.generation), "size": int(blob.size or 0)}
            existing["release_index"] = self.publisher.update_latest_run_index(
                asset=ASSET, payload=existing, run_record_info=info)
        return existing

    def baseline(self) -> lifecycle.Baseline | None:
        self.previous = None
        self.baseline_ids = frozenset()
        if self.gate.before is None:
            lifecycle.require(not any(self.publisher.bucket.list_blobs(prefix=ASSET.root + "/releases/"))
                and not any(self.publisher.bucket.list_blobs(prefix=ASSET.root + "/latest/")),
                "existing e-AMLIS artifacts have no verified baseline; reviewed repair required")
            return None
        release = self.gate.before["release"]["date"]
        try:
            verified = published_baseline.read(self.publisher, ASSET)
            self.previous, manifest, records = verified.record, verified.manifest, verified.records
            lifecycle.require(manifest["release"] == release and len(records) == self.gate.before["release"]["rows"],
                              "baseline differs from the claimed publication")
            descriptor = manifest["identity"]
            ledger = identity_state.selected_ledger(self.gate)
            if descriptor.get("contract_id") == keyed_identity.CONTRACT:
                lifecycle.require(ledger is not None, "keyed baseline has no installed ledger")
                identity_state.validate_records(ledger, records)
            else:
                lifecycle.require(descriptor["strategy"] == "source_field" and descriptor["source_fields"] == ["OBJECTID"],
                                  "baseline has an unsupported identity contract")
            # During the reviewed cutover the baseline still has legacy IDs.
            # Compare both releases in the installed permanent key space.
            self.baseline_ids = frozenset(ledger.ids[key.key] for key in keyed_identity.classify_release(
                row["properties"] for row in records)) if ledger is not None else frozenset(row["feature_id"] for row in records)
        except (NotFound, ValueError, KeyError, lifecycle.LifecycleError) as exc:
            raise RuntimeError(f"e-AMLIS baseline failed its contract; reviewed repair required: {exc}") from exc
        return lifecycle.Baseline(dt.date.fromisoformat(release), self.previous.get("source_fingerprint_hash"),
            len(records), self.gate.state["hold"]["reason"] if self.gate.held else None)

    def acquire(self, attempt: dt.date) -> lifecycle.Source:
        self.source = fetch_source_state(self.layer_url, self.where)
        self.extract = download_source_geojson(source=self.source,
            dest=self.workdir / f"{ASSET.slug}.geojson", page_size=self.page_size)
        return lifecycle.Source(attempt, self.source.layer_url, sha256_file(self.extract.geojson))

    def describe(self, source: lifecycle.Source, baseline: lifecycle.Baseline | None) -> lifecycle.Content:
        selection = {key: getattr(self.source, key) for key in ("layer_url", "where", "service_item_id")}
        previous_inputs = (self.previous or {}).get("source_fingerprint", {}).get("inputs", {})
        selection_changed = "layer_url" in previous_inputs and selection != {key: previous_inputs.get(key) for key in selection}
        held = self.gate.state["hold"]["reason"] if self.gate.held else None
        reason = held or ("source_selection_changed" if selection_changed else None)
        if selection_changed:
            self.gate.block("source_selection_changed", {"geometry_count": self.extract.row_count,
                "selection": selection, "checked_at": source_loss.now()})
        if self.extract.row_count == 0:
            return lifecycle.Content({"source_fingerprint_hash": source.version}, 0,
                len(self.baseline_ids) if baseline else None, reason)
        def discover_identity(directory):
            features = list(feature_metadata.iter_geojson_features(self.extract.geojson))
            if len(features) != self.extract.row_count:
                raise RuntimeError("complete EAMLIS extract count changed before identity reservation")
            if self.gate.state.get("identity") is not None:
                keyed_identity.classify_release(row["properties"] for row in features)
                strategy = keyed_identity.CONTRACT
            else:
                keys = [feature_metadata.source_field_feature_id("OBJECTID", row["properties"].get("OBJECTID")) for row in features]
                if len(set(keys)) != len(keys):
                    raise RuntimeError("duplicate EAMLIS source IDs")
                strategy = "source_field"
            return {"rows": len(features), "identity_strategy": strategy}
        discovery = publication_readiness.discover_batch([
            publication_readiness.Target(self.slug, source.release.isoformat(),
                {"source_sha256": source.version, "expected_rows": self.extract.row_count,
                 "producer": sha256_file(Path(__file__)), "identity_state": self.gate.state.get("identity")},
                discover_identity)
        ], self.workdir / "analysis")
        keyed_identity.require(discovery["state"] == "READY",
            "publication identity discovery did not complete: " + json.dumps(discovery, sort_keys=True))
        self.reservation = identity_state.reserve(self.gate,
            (feature["properties"] for feature in feature_metadata.iter_geojson_features(self.extract.geojson)), self.workdir)
        self.translation_source, self.translation_inputs = translation_input(self.publisher, self.workdir, gate=self.gate)
        contract = keyed_identity.CONTRACT if self.reservation is not None else None
        _, metadata, _, _, records = prepare_metadata(self.source, self.extract, self.workdir, source.release, self.reservation)
        reconcile_translations(metadata, self.translation_source, source.release)
        lost = len(self.baseline_ids - {row["feature_id"] for row in records}) if baseline else None
        self.source = fingerprint_source(self.source, self.extract, self.translation_source, identity_contract=contract)
        return lifecycle.Content({"source_fingerprint": self.source.fingerprint,
                                  "source_fingerprint_hash": self.source.fingerprint_hash},
            self.extract.row_count, lost, reason,
            {"record_version": RUN_RECORD_VERSION, **({"identity_contract": contract} if contract else {}),
             "source_stats": {"feature_count": self.extract.row_count, "max_date_revised": self.source.stats.max_date_revised,
                              "max_objectid": self.source.stats.max_objectid}, "field_count": len(self.source.fields)})

    def build(self, source: lifecycle.Source, content: lifecycle.Content, baseline) -> lifecycle.Built:
        self.output = build_asset_output(source=self.source, extract=self.extract, workdir=self.workdir,
            release_date=source.release, **({"identity_reservation": self.reservation} if self.reservation is not None else {}))
        self.reports = prepare_changed_asset(self.output, self.translation_source, source.release)
        self.source_inputs = [{"uri": source.uri, "where": self.source.where,
            "source_fingerprint_hash": self.source.fingerprint_hash}, *self.translation_inputs]
        return lifecycle.Built(self.output.row_count, self.tools,
            f"gs://{self.publisher.bucket.name}/{ASSET.release_prefix(source.release)}/")

    def publish(self, built: lifecycle.Built, record: dict[str, Any]) -> dict[str, Any]:
        self.gate.check_publication()
        self.publication_started = True
        record = publish_changed_asset(publisher=self.publisher, record=record, output=self.output,
            translation_source=self.translation_source, source_inputs=self.source_inputs, reports=self.reports)
        if self.reservation is not None:
            identity_state.activate(self.gate)
        return record

    def check_in(self, record: dict[str, Any]) -> dict[str, Any]:
        record.update(release_path=self.previous["release_path"], latest_paths=self.previous.get("latest_paths", []))
        record["run_record"] = self.publisher.write_run_record(asset=ASSET,
            run_date=dt.date.fromisoformat(record["run_date"]), payload=record)
        return record

    def hold(self, reason: str, source: lifecycle.Source, content: lifecycle.Content, built) -> dict[str, Any]:
        self.gate.block(reason, {"geometry_count": content.rows, "lost_objects": content.lost,
            "maximum_lost_object_percent": self.loss_limit,
            "selection": {key: getattr(self.source, key) for key in ("layer_url", "where", "service_item_id")},
            "checked_at": source_loss.now()})
        if built is not None:
            record = lifecycle.published(self.slug, attempt=source.release, source=source, content=content,
                                         built=built, lost=content.lost)
            source_loss.stage_candidate(self.gate, output=self.output, fingerprint=self.source.fingerprint,
                release=source.release.isoformat(), identity=self.output.identity_metadata,
                geometry_hash_algorithm=self.output.geometry_hash_algorithm,
                record=record,
                source_inputs=self.source_inputs, translations=localization.translations_payload(self.reports),
                extra_files=[(".metadata-translations.csv", self.translation_source),
                             *((f".metadata.{r.locale}.ndjson.gz", Path(r.output_sidecar)) for r in self.reports)],
                source_input=self.extract.geojson,
                evidence={"lifecycle_record": source_loss.digest(source_loss.encode(record))})
        self.gate.notify()
        return self.gate.record()


def ingest(publisher: GcsPublisher, run_date: dt.date, workdir: Path, *, layer_url: str, where: str, page_size: int):
    refresh = Refresh(publisher, workdir, layer_url=layer_url, where=where, page_size=page_size)
    refresh.tools = toolchain()
    with source_loss.Guard(publisher.bucket) as refresh.gate:
        try:
            return lifecycle.run(refresh, run_date)
        except Exception as exc:
            reservation = refresh.reservation
            requires_review = isinstance(exc, keyed_identity.IdentityError) or refresh.publication_started or (reservation is not None and
                    reservation.ledger.next_feature_id > reservation.before)
            if refresh.gate.held or requires_review:
                hold = refresh.gate.state["hold"]
                observation = dict(hold["observation"]) if hold else {"geometry_count": None}
                observation.update(error=str(exc)[:1000], checked_at=source_loss.now())
                refresh.gate.block(hold["reason"] if hold else "identity_publication_requires_review", observation)
                refresh.gate.notify()
            raise


def run() -> list[dict[str, Any]]:
    configure_logging()
    project_id = os.environ.get("GOOGLE_CLOUD_PROJECT", DEFAULT_PROJECT_ID)
    run_date = parse_run_date(os.environ.get("RUN_DATE"))
    publisher = GcsPublisher(storage.Client(project=project_id),
        os.environ.get("SHARED_DATASETS_BUCKET", DEFAULT_BUCKET), release_suffixes=RELEASE_SUFFIXES, logger=LOGGER)
    work_root = Path(os.environ.get("SHARED_DATASETS_WORKDIR", str(Path(tempfile.gettempdir()) / "shared-datasets-1"))) / "vector-assets" / ASSET.slug
    work_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="monthly-", dir=work_root) as tmp:
        return [ingest(publisher, run_date, Path(tmp),
            layer_url=os.environ.get("EAMLIS_LAYER_URL", DEFAULT_LAYER_URL).rstrip("/"),
            where=os.environ.get("EAMLIS_WHERE", DEFAULT_WHERE),
            page_size=parse_page_size(os.environ.get("EAMLIS_PAGE_SIZE")))]


def main() -> None:
    run_job_main(run, logger=LOGGER, failure_message="e-AMLIS monthly job failed")


if __name__ == "__main__":
    main()
