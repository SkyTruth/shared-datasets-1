#!/usr/bin/env python3
"""Raster asset validation helpers for shared-datasets-1."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Sequence


COG_CONTENT_TYPE = "image/tiff; application=geotiff; profile=cloud-optimized"
RASTER_CANONICAL_FORMATS = {"cog", "zarr"}
COG_EXTENSIONS = {".tif", ".tiff"}
PREVIEW_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
RASTER_SOURCE_EXTENSIONS = {".nc", ".grib", ".grib2", ".hdf", ".h5", ".hdf5", ".tif", ".tiff"}
RASTER_SIDECAR_SUFFIXES = (".aux.xml", ".ovr", ".tfw", ".tifw", ".tiffw")


@dataclass(frozen=True)
class CogValidationResult:
    path: str
    valid: bool
    errors: tuple[str, ...]
    metadata: dict[str, Any]


def raster_sidecars_for(path: Path) -> tuple[Path, ...]:
    """Return existing sidecar files that would make a raster non-self-contained."""
    candidates = []
    name = path.name.lower()
    for candidate in path.parent.iterdir() if path.parent.exists() else ():
        candidate_name = candidate.name.lower()
        if candidate == path:
            continue
        if candidate_name.startswith(name) and candidate_name.endswith(RASTER_SIDECAR_SUFFIXES):
            candidates.append(candidate)
    return tuple(sorted(candidates))


def _run_gdalinfo_json(path: Path) -> dict[str, Any]:
    if not shutil.which("gdalinfo"):
        raise RuntimeError("gdalinfo is required to validate COG files")
    completed = subprocess.run(
        ["gdalinfo", "-json", str(path)],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"gdalinfo failed for {path}: {completed.stderr.strip()}")
    return json.loads(completed.stdout)


def validate_cog(path: Path) -> CogValidationResult:
    """Validate the repo's minimum local COG contract."""
    errors: list[str] = []
    metadata: dict[str, Any] = {}

    if path.suffix.lower() not in COG_EXTENSIONS:
        errors.append("COG files must use .tif or .tiff.")
    if not path.exists():
        return CogValidationResult(str(path), False, ("File does not exist.", *errors), metadata)

    sidecars = raster_sidecars_for(path)
    if sidecars:
        errors.append(
            "COG must be self-contained; sidecar files found: "
            + ", ".join(sidecar.name for sidecar in sidecars)
        )

    try:
        metadata = _run_gdalinfo_json(path)
    except (RuntimeError, json.JSONDecodeError) as exc:
        errors.append(str(exc))
        return CogValidationResult(str(path), False, tuple(errors), metadata)

    image_structure = (metadata.get("metadata") or {}).get("IMAGE_STRUCTURE") or {}
    if image_structure.get("LAYOUT") != "COG":
        errors.append("GDAL IMAGE_STRUCTURE metadata does not report LAYOUT=COG.")
    if "coordinateSystem" not in metadata:
        errors.append("COG is missing coordinate system metadata.")
    if "geoTransform" not in metadata:
        errors.append("COG is missing geotransform metadata.")

    bands = metadata.get("bands") or []
    if not bands:
        errors.append("COG has no bands.")
    if bands and not all(band.get("block") for band in bands):
        errors.append("Every COG band must report an internal block size.")
    if bands and not any(band.get("overviews") for band in bands):
        errors.append("COG must contain internal overviews.")

    return CogValidationResult(str(path), not errors, tuple(errors), metadata)


def validate_zarr_manifest_payload(
    payload: dict[str, Any],
    *,
    bucket: str,
    asset_root: str,
    asset_slug: str,
) -> tuple[str, ...]:
    """Validate the minimum latest/manifest.json pointer shape for Zarr assets."""
    errors: list[str] = []
    if payload.get("asset_slug") != asset_slug:
        errors.append(f"manifest asset_slug must be {asset_slug!r}.")
    if payload.get("canonical_format") != "zarr":
        errors.append("manifest canonical_format must be 'zarr'.")

    release_path = str(payload.get("release_path") or payload.get("zarr_path") or "")
    expected_prefix = f"gs://{bucket}/{asset_root}/releases/"
    if not release_path.startswith(expected_prefix):
        errors.append(f"manifest release_path must start with {expected_prefix}.")
    if not release_path.rstrip("/").endswith(".zarr"):
        errors.append("manifest release_path must point at a .zarr prefix.")
    if "/releases/" in release_path:
        release_part = release_path.split("/releases/", 1)[1].split("/", 1)[0]
        if len(release_part) != 10 or release_part[4] != "-" or release_part[7] != "-":
            errors.append("manifest release_path must include releases/YYYY-MM-DD/.")
    if not payload.get("updated"):
        errors.append("manifest must include updated.")
    return tuple(errors)


def _cmd_validate_cog(args: argparse.Namespace) -> int:
    result = validate_cog(Path(args.path))
    print(json.dumps(asdict(result), indent=2, sort_keys=True))
    return 0 if result.valid else 1


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate shared-datasets raster assets.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate_cog_parser = subparsers.add_parser("validate-cog", help="Validate a local Cloud Optimized GeoTIFF.")
    validate_cog_parser.add_argument("path", help="Path to a .tif/.tiff file.")
    validate_cog_parser.set_defaults(func=_cmd_validate_cog)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
