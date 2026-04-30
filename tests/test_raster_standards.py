from __future__ import annotations

import datetime as dt
import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from ingestion.common.runtime import run_command
from scripts.gcs_asset import content_type_for
from scripts.raster_asset import validate_cog


REPO_ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = REPO_ROOT / ".claude/skills/shared-datasets-compliance-audit/scripts/audit_shared_datasets.py"
SPEC = importlib.util.spec_from_file_location("audit_shared_datasets", AUDIT_PATH)
audit = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = audit
SPEC.loader.exec_module(audit)


BUCKET = "skytruth-shared-datasets-1"
ROOT = "200-imagery-derived/220-optical-derived/example-raster"

README_BASE = """# Example Raster

**Status:** active
**Owner:** SkyTruth
**Last updated:** 2026-04-29
**Update cadence:** manual
**Canonical file:** `latest/example-raster.tif`
**Source:** Example source
**License / terms:** Example terms

## What this is

Example.

## Files

| File | Purpose |
|---|---|
| `latest/example-raster.tif` | Canonical COG |

## Schema notes

Example.

## Properties / columns

| Name | Type | Description |
|---|---|---|
| `band_1` | uint16 | Example band. |

## Update notes

Manual.
"""

RASTER_README = README_BASE + """
## Raster metadata

| Field | Value |
|---|---|
| CRS | EPSG:4326 |
| Pixel size / resolution | 30 m |
| Dimensions | 1024 x 1024 x 1 |
| Band semantics | Example |
| Data type / nodata | UInt16 / 0 |
| Units / scale / offset | unitless / 1 / 0 |
| Sampling | area |
| Validation | COG valid; internal overviews; no sidecars |
"""


def blob(name: str) -> audit.BlobInfo:
    return audit.BlobInfo(
        name=name,
        size=1,
        generation="1",
        updated=dt.datetime(2026, 4, 29, tzinfo=dt.timezone.utc).isoformat(),
        content_type=None,
        metadata={},
    )


def catalog_row(*, fmt: str, canonical_path: str, available_formats: str = "cog") -> dict[str, str]:
    return {
        "asset_slug": "example-raster",
        "category": "200-imagery-derived",
        "subcategory": "220-optical-derived",
        "canonical_path": canonical_path,
        "canonical_format": fmt,
        "available_formats": available_formats,
        "metadata_paths": "README.md",
    }


def validate_with_text(
    blobs: list[audit.BlobInfo],
    row: dict[str, str],
    *,
    readme_text: str = RASTER_README,
    manifest_text: str | None = None,
) -> list[audit.Finding]:
    old_readme = audit.download_readme_text
    old_object = audit.download_object_text
    try:
        audit.download_readme_text = lambda *_args: readme_text
        if manifest_text is not None:
            audit.download_object_text = lambda *_args: manifest_text
        return audit.validate_asset_roots(
            BUCKET,
            blobs,
            {"200-imagery-derived": {"220-optical-derived"}},
            [row],
            {"example-raster": row},
            skip_readme_content=False,
            prefix="",
        )
    finally:
        audit.download_readme_text = old_readme
        audit.download_object_text = old_object


class RasterStandardsTests(unittest.TestCase):
    def test_content_type_for_raster_assets(self):
        self.assertEqual(
            content_type_for(Path("asset.tif"), None),
            "image/tiff; application=geotiff; profile=cloud-optimized",
        )
        self.assertEqual(content_type_for(Path("preview.webp"), None), "image/webp")
        self.assertEqual(content_type_for(Path("manifest.json"), None), "application/json")

    def test_valid_cog_catalog_and_layout_have_no_findings(self):
        canonical = f"gs://{BUCKET}/{ROOT}/latest/example-raster.tif"
        row = catalog_row(fmt="cog", canonical_path=canonical)
        findings = validate_with_text(
            [
                blob(f"{ROOT}/README.md"),
                blob(f"{ROOT}/latest/example-raster.tif"),
                blob(f"{ROOT}/releases/2026-04-29/example-raster.tif"),
            ],
            row,
        )

        self.assertEqual([finding.message for finding in findings], [])

    def test_raw_geotiff_canonical_is_flagged(self):
        canonical = f"gs://{BUCKET}/{ROOT}/latest/example-raster.tif"
        row = catalog_row(fmt="fgb", canonical_path=canonical, available_formats="fgb")
        findings = validate_with_text(
            [blob(f"{ROOT}/README.md"), blob(f"{ROOT}/latest/example-raster.tif")],
            row,
        )

        self.assertIn("raw-raster-canonical", {finding.check for finding in findings})

    def test_missing_raster_metadata_is_flagged(self):
        canonical = f"gs://{BUCKET}/{ROOT}/latest/example-raster.tif"
        row = catalog_row(fmt="cog", canonical_path=canonical)
        findings = validate_with_text(
            [blob(f"{ROOT}/README.md"), blob(f"{ROOT}/latest/example-raster.tif")],
            row,
            readme_text=README_BASE,
        )

        self.assertIn("readme-raster_metadata", {finding.check for finding in findings})

    def test_valid_zarr_manifest_pointer_layout(self):
        zarr_root = f"{ROOT}/releases/2026-04-29/example-raster.zarr"
        canonical = f"gs://{BUCKET}/{ROOT}/latest/manifest.json"
        row = catalog_row(fmt="zarr", canonical_path=canonical, available_formats="zarr")
        manifest = json.dumps(
            {
                "asset_slug": "example-raster",
                "canonical_format": "zarr",
                "updated": "2026-04-29",
                "release_path": f"gs://{BUCKET}/{zarr_root}/",
            }
        )
        findings = validate_with_text(
            [
                blob(f"{ROOT}/README.md"),
                blob(f"{ROOT}/latest/manifest.json"),
                blob(f"{zarr_root}/zarr.json"),
                blob(f"{zarr_root}/band/c/0/0"),
            ],
            row,
            manifest_text=manifest,
        )

        self.assertEqual([finding.message for finding in findings], [])

    def test_zarr_chunks_under_latest_are_flagged(self):
        canonical = f"gs://{BUCKET}/{ROOT}/latest/manifest.json"
        row = catalog_row(fmt="zarr", canonical_path=canonical, available_formats="zarr")
        findings = validate_with_text(
            [
                blob(f"{ROOT}/README.md"),
                blob(f"{ROOT}/latest/example-raster.zarr/zarr.json"),
            ],
            row,
            manifest_text="{}",
        )

        checks = {finding.check for finding in findings}
        self.assertIn("zarr-latest-layout", checks)
        self.assertIn("zarr-latest-manifest", checks)


@unittest.skipUnless(
    shutil.which("gdal_create") and shutil.which("gdal_translate") and shutil.which("gdalinfo"),
    "requires GDAL binaries",
)
class RasterCogIntegrationTests(unittest.TestCase):
    def test_tiny_cog_validates_with_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source = tmp_path / "source.tif"
            cog = tmp_path / "example-raster.tif"
            run_command(
                [
                    "gdal_create",
                    "-of",
                    "GTiff",
                    "-outsize",
                    "1024",
                    "1024",
                    "-bands",
                    "1",
                    "-burn",
                    "1",
                    "-ot",
                    "Byte",
                    "-a_srs",
                    "EPSG:4326",
                    "-a_ullr",
                    "0",
                    "1",
                    "1",
                    "0",
                    "-a_nodata",
                    "0",
                    str(source),
                ]
            )
            run_command(
                [
                    "gdal_translate",
                    "-of",
                    "COG",
                    "-co",
                    "COMPRESS=DEFLATE",
                    "-co",
                    "BIGTIFF=IF_SAFER",
                    "-co",
                    "BLOCKSIZE=512",
                    "-co",
                    "RESAMPLING=NEAREST",
                    str(source),
                    str(cog),
                ]
            )

            result = validate_cog(cog)

        self.assertTrue(result.valid, result.errors)
        self.assertEqual(result.metadata["size"], [1024, 1024])
        self.assertIn("coordinateSystem", result.metadata)
        self.assertEqual(result.metadata["bands"][0]["noDataValue"], 0.0)
        self.assertTrue(result.metadata["bands"][0]["overviews"])


if __name__ == "__main__":
    unittest.main()
