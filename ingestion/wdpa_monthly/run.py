"""Simplified monthly WDPA/WDOECM publisher.

The job intentionally performs only format conversion and a MARINE split. It
does not rename fields, derive metadata tables, buffer point records, or update
external databases.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import mimetypes
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from google.api_core.exceptions import NotFound, PreconditionFailed
from google.cloud import storage


LOGGER = logging.getLogger("wdpa_monthly")

DEFAULT_PROJECT_ID = "shared-datasets-1"
DEFAULT_BUCKET = "skytruth-shared-datasets-1"
DEFAULT_SOURCE_URL_TEMPLATE = (
    "https://d1gam3xoknrgr2.cloudfront.net/current/"
    "WDPA_WDOECM_{month_token}_Public_all_shp.zip"
)
ASSET_PARENT = "100-geographic-reference/130-protected-areas"
RUN_RECORD_VERSION = 1


@dataclass(frozen=True)
class AssetSpec:
    slug: str
    title: str
    tile_layer: str
    where: str

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


@dataclass(frozen=True)
class AssetOutputs:
    fgb: Path
    pmtiles: Path
    row_count: int
    sha256: dict[str, str]


ASSETS: tuple[AssetSpec, ...] = (
    AssetSpec(
        slug="wdpa-marine",
        title="WDPA Marine Protected and Conserved Areas",
        tile_layer="wdpa_marine",
        where="MARINE IN ('1', '2')",
    ),
    AssetSpec(
        slug="wdpa-terrestrial",
        title="WDPA Terrestrial Protected and Conserved Areas",
        tile_layer="wdpa_terrestrial",
        where="MARINE = '0'",
    ),
)


def configure_logging() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def parse_run_date(value: str | None) -> dt.date:
    if not value:
        return dt.datetime.now(dt.UTC).date()
    return dt.date.fromisoformat(value)


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


def require_binary(name: str) -> None:
    if not shutil.which(name):
        raise RuntimeError(f"Required executable not found on PATH: {name}")


def run_command(
    args: list[str],
    *,
    capture_json: bool = False,
    capture_text: bool = False,
) -> Any:
    LOGGER.info("running command: %s", " ".join(shlex.quote(a) for a in args))
    completed = subprocess.run(
        args,
        check=False,
        text=True,
        stdout=subprocess.PIPE if capture_json or capture_text else None,
        stderr=subprocess.PIPE,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "Command failed with exit code "
            f"{completed.returncode}: {' '.join(shlex.quote(a) for a in args)}\n"
            f"{completed.stderr}"
        )
    if capture_json:
        try:
            return json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"Command did not return JSON: {' '.join(args)}\n{completed.stdout}"
            ) from exc
    if capture_text:
        return completed.stdout
    if completed.stderr:
        LOGGER.debug(completed.stderr)
    return None


def download_file(url: str, dest: Path) -> None:
    LOGGER.info("downloading source: %s", url)
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "shared-datasets-1-wdpa-monthly/1.0"},
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        status = getattr(response, "status", response.getcode())
        if status >= 400:
            raise RuntimeError(f"Download failed with HTTP {status}: {url}")
        with dest.open("wb") as file_obj:
            shutil.copyfileobj(response, file_obj)
    if dest.stat().st_size == 0:
        raise RuntimeError(f"Downloaded zero-byte source file: {url}")


def source_dataset_path(path: Path) -> str:
    if path.suffix.lower() == ".zip":
        return f"/vsizip/{path}"
    return str(path)


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


def discover_source_layers(source: str) -> list[SourceLayer]:
    try:
        payload = run_command(["ogrinfo", "-json", source], capture_json=True)
        layers = parse_layers(payload)
    except RuntimeError:
        layers = parse_ogrinfo_text_layers(ogrinfo_text(source, all_layers=True))

    candidate_layers = [
        layer
        for layer in layers
        if any(field.name == "MARINE" for field in layer.fields)
    ]
    if not candidate_layers:
        raise RuntimeError("No geometry layers with a MARINE field found in WDPA source")

    expected_fields = candidate_layers[0].fields
    mismatched = [
        layer.name for layer in candidate_layers[1:] if layer.fields != expected_fields
    ]
    if mismatched:
        raise RuntimeError(
            "WDPA source layers do not have identical fields: "
            f"{', '.join(mismatched)}"
        )

    LOGGER.info(
        "selected source layers: %s",
        ", ".join(f"{layer.name} ({layer.geometry_type})" for layer in candidate_layers),
    )
    return candidate_layers


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
    return sum(feature_count(source, layer.name, where) for layer in layers)


def remove_if_exists(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def build_filtered_gpkg(
    *,
    source: str,
    source_layers: list[SourceLayer],
    asset: AssetSpec,
    output: Path,
) -> None:
    remove_if_exists(output)
    for index, layer in enumerate(source_layers):
        args = ["ogr2ogr", "-f", "GPKG"]
        if index > 0:
            args.extend(["-update", "-append"])
        args.extend(
            [
                str(output),
                source,
                layer.name,
                "-nln",
                asset.tile_layer,
                "-nlt",
                "GEOMETRY",
                "-where",
                asset.where,
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


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_asset_outputs(
    *,
    source: str,
    source_layers: list[SourceLayer],
    source_fields: tuple[FieldSpec, ...],
    asset: AssetSpec,
    workdir: Path,
) -> AssetOutputs:
    expected_rows = expected_feature_count(source, source_layers, asset.where)
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
        output=gpkg,
    )
    convert_gpkg_to_fgb(gpkg, asset, fgb)
    convert_gpkg_to_geojsonseq(gpkg, asset, geojsonseq)
    build_pmtiles(geojsonseq, asset, pmtiles)

    actual_rows = feature_count(str(fgb))
    if actual_rows != expected_rows:
        raise RuntimeError(
            f"{asset.slug} row count mismatch: expected {expected_rows}, got {actual_rows}"
        )
    output_fields = layer_fields(fgb)
    if output_fields != source_fields:
        raise RuntimeError(f"{asset.slug} FGB schema does not match source schema")
    validate_pmtiles(pmtiles)

    return AssetOutputs(
        fgb=fgb,
        pmtiles=pmtiles,
        row_count=actual_rows,
        sha256={
            "fgb": sha256_file(fgb),
            "pmtiles": sha256_file(pmtiles),
        },
    )


def content_type_for(path: Path) -> str | None:
    if path.suffix == ".fgb":
        return "application/octet-stream"
    if path.suffix == ".pmtiles":
        return "application/vnd.pmtiles"
    if path.suffix == ".json":
        return "application/json"
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed


class GcsPublisher:
    def __init__(self, client: storage.Client, bucket_name: str) -> None:
        self.bucket = client.bucket(bucket_name)

    def blob_exists(self, name: str) -> bool:
        blob = self.bucket.blob(name)
        try:
            blob.reload()
            return True
        except NotFound:
            return False

    def load_json(self, name: str) -> dict[str, Any] | None:
        blob = self.bucket.blob(name)
        try:
            blob.reload()
        except NotFound:
            return None
        return json.loads(blob.download_as_text())

    def successful_run_record(self, asset: AssetSpec, run_date: dt.date) -> bool:
        record = self.load_json(asset.run_record_object(run_date))
        return bool(record and record.get("status") == "success")

    def assert_no_partial_release(self, asset: AssetSpec, run_date: dt.date) -> None:
        existing = [
            name
            for name in (
                asset.release_object(run_date, ".fgb"),
                asset.release_object(run_date, ".pmtiles"),
            )
            if self.blob_exists(name)
        ]
        if existing:
            raise RuntimeError(
                "Release object(s) already exist without a successful run record: "
                + ", ".join(f"gs://{self.bucket.name}/{name}" for name in existing)
            )

    def upload_new_object(
        self,
        *,
        local_path: Path,
        object_name: str,
        metadata: dict[str, str],
    ) -> dict[str, Any]:
        blob = self.bucket.blob(object_name)
        blob.metadata = metadata
        try:
            blob.upload_from_filename(
                local_path,
                content_type=content_type_for(local_path),
                if_generation_match=0,
            )
        except PreconditionFailed as exc:
            raise RuntimeError(
                f"Refusing to overwrite existing release object: "
                f"gs://{self.bucket.name}/{object_name}"
            ) from exc
        blob.reload()
        return {
            "path": f"gs://{self.bucket.name}/{object_name}",
            "generation": int(blob.generation),
            "size": int(blob.size or 0),
        }

    def replace_latest_object(
        self,
        *,
        local_path: Path,
        object_name: str,
        metadata: dict[str, str],
    ) -> dict[str, Any]:
        blob = self.bucket.blob(object_name)
        blob.metadata = metadata
        try:
            blob.reload()
            generation_match = int(blob.generation)
        except NotFound:
            generation_match = 0
        try:
            blob.upload_from_filename(
                local_path,
                content_type=content_type_for(local_path),
                if_generation_match=generation_match,
            )
        except PreconditionFailed as exc:
            raise RuntimeError(
                f"Latest object generation changed before upload: "
                f"gs://{self.bucket.name}/{object_name}"
            ) from exc
        blob.reload()
        return {
            "path": f"gs://{self.bucket.name}/{object_name}",
            "generation": int(blob.generation),
            "size": int(blob.size or 0),
        }

    def write_run_record(
        self,
        *,
        asset: AssetSpec,
        run_date: dt.date,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        object_name = asset.run_record_object(run_date)
        blob = self.bucket.blob(object_name)
        blob.metadata = {
            "asset_slug": asset.slug,
            "run_date": run_date.isoformat(),
        }
        try:
            blob.upload_from_string(
                json.dumps(payload, indent=2, sort_keys=True) + "\n",
                content_type="application/json",
                if_generation_match=0,
            )
        except PreconditionFailed as exc:
            existing = self.load_json(object_name)
            if existing and existing.get("status") == "success":
                LOGGER.info("%s run record already exists", asset.slug)
                blob.reload()
            else:
                raise RuntimeError(
                    f"Run record already exists and is not successful: "
                    f"gs://{self.bucket.name}/{object_name}"
                ) from exc
        blob.reload()
        return {
            "path": f"gs://{self.bucket.name}/{object_name}",
            "generation": int(blob.generation),
            "size": int(blob.size or 0),
        }


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
    if publisher.successful_run_record(asset, run_date):
        LOGGER.info("%s already has a successful run record; skipping", asset.slug)
        return {"asset_slug": asset.slug, "status": "skipped"}

    publisher.assert_no_partial_release(asset, run_date)

    metadata = {
        "asset_slug": asset.slug,
        "run_date": run_date.isoformat(),
        "source_version": source_version,
    }
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


def run() -> list[dict[str, Any]]:
    configure_logging()
    for binary in ("ogrinfo", "ogr2ogr", "tippecanoe", "pmtiles"):
        require_binary(binary)

    project_id = os.environ.get("GOOGLE_CLOUD_PROJECT", DEFAULT_PROJECT_ID)
    bucket_name = os.environ.get("SHARED_DATASETS_BUCKET", DEFAULT_BUCKET)
    run_date = parse_run_date(os.environ.get("RUN_DATE"))
    source_template = os.environ.get("WDPA_SOURCE_URL_TEMPLATE", DEFAULT_SOURCE_URL_TEMPLATE)
    source_url = build_source_url(source_template, run_date)
    source_version = source_version_for(run_date)

    publisher = GcsPublisher(storage.Client(project=project_id), bucket_name)

    publish_specs = [
        asset
        for asset in ASSETS
        if not publisher.successful_run_record(asset, run_date)
    ]
    if not publish_specs:
        LOGGER.info("all WDPA assets already have successful run records for %s", run_date)
        return [
            {
                "asset_slug": asset.slug,
                "run_date": run_date.isoformat(),
                "status": "skipped",
            }
            for asset in ASSETS
        ]

    for asset in publish_specs:
        publisher.assert_no_partial_release(asset, run_date)

    with tempfile.TemporaryDirectory(prefix="wdpa-monthly-") as tmp:
        workdir = Path(tmp)
        source_zip = workdir / "wdpa.zip"
        download_file(source_url, source_zip)
        source = source_dataset_path(source_zip)
        source_layers = discover_source_layers(source)
        source_fields = source_layers[0].fields

        records = []
        for asset in ASSETS:
            if asset not in publish_specs:
                records.append(
                    {
                        "asset_slug": asset.slug,
                        "run_date": run_date.isoformat(),
                        "status": "skipped",
                    }
                )
                continue
            outputs = build_asset_outputs(
                source=source,
                source_layers=source_layers,
                source_fields=source_fields,
                asset=asset,
                workdir=workdir,
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
