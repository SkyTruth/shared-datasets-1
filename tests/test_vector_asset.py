from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts import vector_asset


class VectorAssetTests(unittest.TestCase):
    def test_default_work_dir_is_under_system_temp_namespace(self):
        work_dir = vector_asset.default_work_dir("natural-earth-10m-land", root=Path("/tmp"))

        self.assertEqual(
            work_dir,
            Path("/tmp/shared-datasets-1/vector-assets/natural-earth-10m-land"),
        )

    def test_build_plan_uses_standard_output_names_and_commands(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.geojson"
            source.write_text('{"type":"FeatureCollection","features":[]}\n')
            work_dir = Path(tmp) / "work"

            plan = vector_asset.build_plan(
                source=source,
                asset_slug="natural-earth-10m-land",
                layer_name="land",
                work_dir=work_dir,
                minzoom=0,
                maxzoom=6,
                tile_simplify=0.01,
                title="Natural Earth 10m Land",
                description="Natural Earth land polygons",
            )

        self.assertEqual(plan.output_dir, str(work_dir / "publish"))
        self.assertEqual(plan.fgb_path, str(work_dir / "publish/natural-earth-10m-land.fgb"))
        self.assertEqual(plan.tippecanoe_input_path, str(work_dir / "build/natural-earth-10m-land.geojson"))
        self.assertEqual(plan.mbtiles_path, str(work_dir / "build/natural-earth-10m-land.mbtiles"))
        self.assertEqual(plan.pmtiles_path, str(work_dir / "publish/natural-earth-10m-land.pmtiles"))
        self.assertEqual(plan.pmtiles_engine, "tippecanoe")
        self.assertIn("ogr2ogr", plan.tool_paths)
        self.assertIn("tippecanoe", plan.tool_paths)
        self.assertIn("pmtiles", plan.tool_paths)
        self.assertIn("ogr2ogr", plan.tool_versions)
        self.assertIn("tippecanoe", plan.tool_versions)
        self.assertIn("pmtiles", plan.tool_versions)
        self.assertEqual(plan.commands[0][:3], ["ogr2ogr", "-f", "FlatGeobuf"])
        self.assertIn("SPATIAL_INDEX=YES", plan.commands[0])
        self.assertEqual(plan.commands[1][:3], ["ogr2ogr", "-f", "GeoJSON"])
        self.assertIn("-simplify", plan.commands[1])
        self.assertIn("0.01", plan.commands[1])
        self.assertEqual(plan.commands[2][0], "tippecanoe")
        self.assertIn("--maximum-zoom", plan.commands[2])

    def test_gdal_mbtiles_fallback_plan_converts_to_pmtiles(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.geojson"
            source.write_text('{"type":"FeatureCollection","features":[]}\n')

            plan = vector_asset.build_plan(
                source=source,
                asset_slug="example-asset",
                work_dir=Path(tmp) / "work",
                pmtiles_engine="gdal-mbtiles",
            )

        self.assertEqual(plan.commands[1][:3], ["ogr2ogr", "-f", "MBTiles"])
        self.assertEqual(plan.commands[2][1], "convert")

    def test_repo_output_is_rejected_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.geojson"
            source.write_text('{"type":"FeatureCollection","features":[]}\n')

            with self.assertRaises(ValueError):
                vector_asset.build_plan(
                    source=source,
                    asset_slug="example-asset",
                    work_dir=vector_asset.REPO_ROOT / ".tmp-vector-assets/example-asset",
                )

    def test_asset_slug_must_be_lowercase_kebab_case(self):
        with self.assertRaises(ValueError):
            vector_asset.validate_asset_slug("Natural_Earth")

    def test_tile_simplification_must_be_positive(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.geojson"
            source.write_text('{"type":"FeatureCollection","features":[]}\n')

            with self.assertRaises(ValueError):
                vector_asset.build_plan(
                    source=source,
                    asset_slug="example-asset",
                    work_dir=Path(tmp) / "work",
                    tile_simplify=0,
                )


if __name__ == "__main__":
    unittest.main()
