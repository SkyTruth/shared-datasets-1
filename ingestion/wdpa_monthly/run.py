"""Simplified monthly WDPA/WDOECM publisher.

The job intentionally performs only format conversion and a source-field split.
It does not rename fields, derive metadata tables, buffer point records, or
update external databases.
"""

from __future__ import annotations

import datetime as dt
import csv
import json
import logging
import os
import re
import shutil
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from google.api_core.exceptions import NotFound
from google.cloud import storage

from ingestion.common import feature_metadata, vector_pipeline
from ingestion.common.gcs import GcsPublisher
from ingestion.common.runtime import (
    SourceNotAvailableError as SourceNotAvailableError,
    bind_run_command,
    configure_logging,
    content_type_for as content_type_for,
    download_file as common_download_file,
    parse_run_date as common_parse_run_date,
    remove_if_exists,
    require_binary,
    run_job_main,
    sha256_file,
)
from scripts import feature_metadata_localization, release_feature_model


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
PMTILES_MINZOOM = 0
PMTILES_MAXZOOM = 12
PMTILES_PROPERTIES = (feature_metadata.FEATURE_ID_COLUMN,)
TRANSLATION_LOCALE = "es"
TRANSLATION_FIELD = "NAME_ENG"
TRANSLATION_REVIEW_STATE = "needs_review"
TRANSLATION_NOTES = "Initial Spanish sidecar preserves source proper-name value pending human review."


@dataclass(frozen=True)
class AssetSpec(vector_pipeline.AssetPaths):
    slug: str
    title: str
    tile_layer: str
    split_group: str
    parent: str = ASSET_PARENT


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
    metadata: Path
    metadata_es: Path
    metadata_translations: Path
    schema: Path
    manifest: Path
    row_count: int
    sha256: dict[str, str]
    schema_payload: dict[str, Any]
    sidecar_records: tuple[dict[str, Any], ...]
    localization_report: dict[str, Any]


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
    return common_parse_run_date(value, default_factory=default_run_date)


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


run_command = bind_run_command(LOGGER)


def download_file(url: str, dest: Path) -> None:
    common_download_file(
        url,
        dest,
        user_agent=USER_AGENT,
        timeout_seconds=REQUEST_TIMEOUT_SECONDS,
        logger=LOGGER,
        not_ready_status_codes=SOURCE_NOT_READY_STATUS_CODES,
        not_ready_label="WDPA source",
        opener=urllib.request.urlopen,
    )


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


def convert_geojsonseq_to_fgb(geojsonseq: Path, asset: AssetSpec, output: Path) -> None:
    remove_if_exists(output)
    run_command(
        [
            "ogr2ogr",
            "-f",
            "FlatGeobuf",
            str(output),
            f"GeoJSONSeq:{geojsonseq}",
            "-nln",
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
    run_command(
        [
            "tippecanoe",
            "-o",
            str(mbtiles),
            "-l",
            asset.tile_layer,
            "-Z",
            str(PMTILES_MINZOOM),
            "-z",
            str(PMTILES_MAXZOOM),
            "--force",
            "--drop-densest-as-needed",
            "--extend-zooms-if-still-dropping",
            "--drop-rate=1",
            f"--name={asset.title}",
            f"--description={asset.title} metadata lookup vector tiles",
            "-y",
            feature_metadata.FEATURE_ID_COLUMN,
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


def write_translation_source(records: Sequence[Mapping[str, Any]], path: Path) -> int:
    """Write initial proper-name translation rows from canonical metadata."""
    count = 0
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "feature_id",
                "field",
                "locale",
                "source_value_hash",
                "value",
                "review_state",
                "notes",
            ],
        )
        writer.writeheader()
        for record in records:
            properties = record.get("properties")
            if not isinstance(properties, Mapping):
                continue
            value = properties.get(TRANSLATION_FIELD)
            if value is None or str(value) == "":
                continue
            writer.writerow(
                {
                    "feature_id": str(record.get("feature_id") or ""),
                    "field": TRANSLATION_FIELD,
                    "locale": TRANSLATION_LOCALE,
                    "source_value_hash": feature_metadata_localization.source_value_hash(value),
                    "value": str(value),
                    "review_state": TRANSLATION_REVIEW_STATE,
                    "notes": TRANSLATION_NOTES,
                }
            )
            count += 1
    if count == 0:
        raise RuntimeError(f"{TRANSLATION_FIELD} translation source would be empty")
    return count


def materialize_localized_metadata(
    *,
    metadata: Path,
    metadata_translations: Path,
    metadata_es: Path,
    asset: AssetSpec,
    run_date: dt.date,
) -> dict[str, Any]:
    report = feature_metadata_localization.materialize_locale_sidecar(
        canonical_sidecar=metadata,
        translation_source=metadata_translations,
        output_sidecar=metadata_es,
        locale=TRANSLATION_LOCALE,
        translatable_fields={TRANSLATION_FIELD},
        expected_asset_slug=asset.slug,
        expected_release=run_date.isoformat(),
        fail_on_stale=True,
    )
    return report.to_dict()


def legacy_wdpa_previous_records(records: Sequence[Mapping[str, Any]], *, asset: AssetSpec) -> list[dict[str, Any]]:
    """Adapt old WDPA metadata sidecars into generated-ID previous records."""
    converted: list[dict[str, Any]] = []
    errors: list[str] = []
    for index, record in enumerate(records, start=1):
        if record.get("schema_version") != 1:
            errors.append(f"record {index} is not legacy schema_version 1")
            continue
        if record.get("asset_slug") not in (None, asset.slug):
            errors.append(f"record {index} asset_slug does not match {asset.slug}")
            continue
        properties = record.get("properties")
        if not isinstance(properties, Mapping):
            errors.append(f"record {index} properties must be an object")
            continue
        site_pid = str(properties.get("SITE_PID") or "").strip()
        ext_id = str(properties.get("ext_id") or "").strip()
        if not site_pid:
            errors.append(f"record {index} is missing properties.SITE_PID")
            continue
        try:
            release_feature_model.validate_feature_id(ext_id)
        except release_feature_model.ReleaseFeatureModelError as exc:
            errors.append(f"record {index} properties.ext_id is not a reusable feature_id: {exc}")
            continue
        converted.append(
            {
                "feature_id": ext_id,
                "identity_key": [site_pid],
            }
        )
    if errors:
        raise RuntimeError("legacy WDPA metadata sidecar cannot preserve generated IDs: " + "; ".join(errors[:10]))
    if not converted:
        raise RuntimeError("legacy WDPA metadata sidecar did not contain any reusable ID mappings")
    return converted


def load_previous_records_for_asset(
    publisher: GcsPublisher,
    asset: AssetSpec,
) -> list[dict[str, Any]] | None:
    try:
        return publisher.load_latest_metadata_records(asset)
    except RuntimeError as original_error:
        object_name = asset.latest_object(".metadata.ndjson.gz")
        blob = publisher.bucket.blob(object_name)
        try:
            blob.reload()
        except NotFound:
            return None
        try:
            raw_records = list(
                release_feature_model.read_metadata_sidecar_bytes(
                    blob.download_as_bytes(),
                    label=f"gs://{publisher.bucket.name}/{object_name}",
                )
            )
            converted = legacy_wdpa_previous_records(raw_records, asset=asset)
        except Exception as legacy_error:
            raise original_error from legacy_error
        LOGGER.warning(
            "%s latest metadata sidecar uses legacy feature_id contract; preserving generated IDs from properties.ext_id and SITE_PID",
            asset.slug,
        )
        return converted


def build_asset_outputs(
    *,
    source: str,
    source_layers: list[SourceLayer],
    source_fields: tuple[FieldSpec, ...],
    asset: AssetSpec,
    where: str,
    workdir: Path,
    run_date: dt.date,
    cleanup_after_gpkg: tuple[Path, ...] = (),
    previous_records: Sequence[Mapping[str, Any]] | None = None,
    identity_resolution_decisions: Sequence[Mapping[str, Any]] = (),
) -> AssetOutputs:
    expected_rows = expected_feature_count(source, source_layers, where)
    if expected_rows <= 0:
        raise RuntimeError(f"{asset.slug} filter produced no rows")

    gpkg = workdir / f"{asset.slug}.gpkg"
    fgb = workdir / f"{asset.slug}.fgb"
    geojsonseq = workdir / f"{asset.slug}.geojsonseq"
    enriched_geojsonseq = workdir / f"{asset.slug}.metadata.geojsonseq"
    pmtiles = workdir / f"{asset.slug}.pmtiles"
    metadata = workdir / f"{asset.slug}.metadata.ndjson.gz"
    metadata_es = workdir / f"{asset.slug}.metadata.{TRANSLATION_LOCALE}.ndjson.gz"
    metadata_translations = workdir / f"{asset.slug}.metadata-translations.csv"
    schema = workdir / f"{asset.slug}.schema.json"
    manifest = workdir / f"{asset.slug}.manifest.json"

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
    enriched_features, sidecar_records, ambiguities = feature_metadata.enrich_features_with_generated_ids(
        feature_metadata.iter_geojsonseq(geojsonseq),
        asset_slug=asset.slug,
        release=run_date.isoformat(),
        source_fields=["SITE_PID"],
        provenance={"source": source, "where": where, "identity_strategy": "generated_sequence_source_fields"},
        previous_records=previous_records,
        identity_resolution_decisions=identity_resolution_decisions,
    )
    if ambiguities:
        feature_metadata.raise_unresolved_identity_ambiguities(
            asset_slug=asset.slug,
            release=run_date.isoformat(),
            ambiguities=ambiguities,
        )
    feature_metadata.write_geojsonseq(enriched_features, enriched_geojsonseq)
    feature_metadata.write_sidecar(sidecar_records, metadata)
    translation_count = write_translation_source(sidecar_records, metadata_translations)
    schema_payload = feature_metadata.schema_from_records(
        asset_slug=asset.slug,
        release=run_date.isoformat(),
        records=sidecar_records,
    )
    feature_metadata.write_schema(schema_payload, schema)
    localization_report = materialize_localized_metadata(
        metadata=metadata,
        metadata_translations=metadata_translations,
        metadata_es=metadata_es,
        asset=asset,
        run_date=run_date,
    )
    if localization_report.get("applied_translation_count") != translation_count:
        raise RuntimeError(
            f"{asset.slug} localized metadata applied "
            f"{localization_report.get('applied_translation_count')} of {translation_count} translation rows"
        )
    build_pmtiles(enriched_geojsonseq, asset, pmtiles)
    remove_if_exists(geojsonseq)

    convert_geojsonseq_to_fgb(enriched_geojsonseq, asset, fgb)
    remove_if_exists(enriched_geojsonseq)

    actual_rows = feature_count(str(fgb))
    if actual_rows != expected_rows:
        raise RuntimeError(
            f"{asset.slug} row count mismatch: expected {expected_rows}, got {actual_rows}"
        )
    output_fields = layer_fields(fgb)
    output_field_names = {field.name for field in output_fields}
    required_field_names = {field.name for field in source_fields} | {
        feature_metadata.FEATURE_ID_COLUMN,
        feature_metadata.GEOMETRY_HASH_COLUMN,
        feature_metadata.PROPERTIES_HASH_COLUMN,
    }
    if not required_field_names.issubset(output_field_names):
        missing = sorted(required_field_names - output_field_names)
        raise RuntimeError(f"{asset.slug} FGB schema is missing required fields: {', '.join(missing)}")
    validate_pmtiles(pmtiles)
    feature_metadata.validate_release_vector_contract(
        fgb_path=fgb,
        pmtiles_path=pmtiles,
        decode_zoom=PMTILES_MINZOOM,
    )
    remove_if_exists(gpkg)

    return AssetOutputs(
        fgb=fgb,
        pmtiles=pmtiles,
        metadata=metadata,
        metadata_es=metadata_es,
        metadata_translations=metadata_translations,
        schema=schema,
        manifest=manifest,
        row_count=actual_rows,
        sha256={
            "fgb": sha256_file(fgb),
            "pmtiles": sha256_file(pmtiles),
            "metadata": sha256_file(metadata),
            f"metadata_{TRANSLATION_LOCALE}": sha256_file(metadata_es),
            "csv": sha256_file(metadata_translations),
            "metadata_translations": sha256_file(metadata_translations),
            "schema": sha256_file(schema),
        },
        schema_payload=schema_payload,
        sidecar_records=tuple(sidecar_records),
        localization_report=localization_report,
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
        }

    publisher.assert_no_partial_release(asset, run_date)
    bundle = vector_pipeline.publish_vector_bundle(
        publisher=publisher,
        asset=asset,
        run_date=run_date,
        outputs=outputs,
        object_metadata=metadata,
        source_inputs=[{"uri": source_url}],
        identity=feature_metadata.release_feature_model.build_identity_metadata(
            strategy="generated_sequence_source_fields",
            source_fields=["SITE_PID"],
            next_generated_feature_id_after_release=feature_metadata.next_generated_feature_id(outputs.sidecar_records),
        ),
        extra_suffix_paths=(
            (f".metadata.{TRANSLATION_LOCALE}.ndjson.gz", outputs.metadata_es),
            (".metadata-translations.csv", outputs.metadata_translations),
        ),
    )

    record = {
        "schema_version": 1,
        "record_version": RUN_RECORD_VERSION,
        "asset_slug": asset.slug,
        "run_date": run_date.isoformat(),
        "release_date": run_date.isoformat(),
        "status": "success",
        "source": source_url,
        "source_version": source_version,
        "release_path": f"gs://{publisher.bucket.name}/{asset.release_prefix(run_date)}/",
        "release_paths": bundle.release_paths,
        "latest_paths": bundle.latest_paths,
        "row_count": outputs.row_count,
        "sha256": bundle.sha256,
        "field_count": len(source_fields),
        "notes": "Generated by simplified monthly WDPA job; fields preserved from source.",
        "localization": {
            "translation_locale": TRANSLATION_LOCALE,
            "translation_field": TRANSLATION_FIELD,
            "report": outputs.localization_report,
        },
    }
    record["release_paths"].extend(bundle.extra_release_paths)
    record["latest_paths"].extend(bundle.extra_latest_paths)
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
            record = {
                "schema_version": 1,
                "asset_slug": asset.slug,
                "run_date": attempt_date.isoformat(),
                "target_release_date": run_date.isoformat(),
                "release_date": run_date.isoformat(),
                "status": "skipped",
                "reason": "monthly source already published",
                "source": source_url,
                "source_version": source_version,
            }
            if successful_release_index:
                record["successful_release_index"] = successful_release_index
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
                    "schema_version": 1,
                    "asset_slug": asset.slug,
                    "run_date": attempt_date.isoformat(),
                    "target_release_date": run_date.isoformat(),
                    "release_date": run_date.isoformat(),
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
                    "schema_version": 1,
                    "asset_slug": asset.slug,
                    "run_date": run_date.isoformat(),
                    "release_date": run_date.isoformat(),
                    "status": "skipped",
                }
                release_index_info = publisher.record_existing_successful_release(
                    asset,
                    run_date,
                )
                if release_index_info:
                    record["release_index"] = release_index_info
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
                run_date=run_date,
                cleanup_after_gpkg=(
                    (workdir / "source-zips",) if asset == final_publish_asset else ()
                ),
                previous_records=load_previous_records_for_asset(publisher, asset),
                identity_resolution_decisions=release_feature_model.load_identity_resolution_decisions(
                    asset_slug=asset.slug,
                    release=run_date.isoformat(),
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
            remove_if_exists(outputs.metadata)
            remove_if_exists(outputs.metadata_es)
            remove_if_exists(outputs.metadata_translations)
            remove_if_exists(outputs.schema)
            remove_if_exists(outputs.manifest)
        return records


def main() -> None:
    run_job_main(run, logger=LOGGER, failure_message="wdpa monthly job failed")


if __name__ == "__main__":
    main()
