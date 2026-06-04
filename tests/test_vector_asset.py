from __future__ import annotations

import json
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock
from pathlib import Path

from scripts import vector_asset
from scripts import pmtiles_zoom


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
                maxzoom=8,
                maxzoom_reason="Natural Earth 1:10m source scale maps to zoom 8.",
                tile_simplify=0.01,
                title="Natural Earth 10m Land",
                description="Natural Earth land polygons",
            )

        self.assertEqual(plan.output_dir, str(work_dir / "publish"))
        self.assertEqual(plan.fgb_path, str(work_dir / "publish/natural-earth-10m-land.fgb"))
        self.assertEqual(plan.mbtiles_path, str(work_dir / "build/natural-earth-10m-land.mbtiles"))
        self.assertEqual(plan.pmtiles_path, str(work_dir / "publish/natural-earth-10m-land.pmtiles"))
        self.assertEqual(plan.pmtiles_profile_path, str(work_dir / "publish/pmtiles-profile.json"))
        self.assertIn("ogr2ogr", plan.tool_paths)
        self.assertNotIn("tippecanoe", plan.tool_paths)
        self.assertIn("pmtiles", plan.tool_paths)
        self.assertIn("ogr2ogr", plan.tool_versions)
        self.assertNotIn("tippecanoe", plan.tool_versions)
        self.assertIn("pmtiles", plan.tool_versions)
        self.assertEqual(plan.commands[0].argv[:3], ["ogr2ogr", "-f", "FlatGeobuf"])
        self.assertIn("-makevalid", plan.commands[0].argv)
        self.assertIn("SPATIAL_INDEX=YES", plan.commands[0].argv)
        self.assertEqual(plan.commands[1].kind, "command")
        self.assertEqual(plan.commands[1].argv[:3], ["ogr2ogr", "-f", "MBTiles"])
        self.assertIn("-simplify", plan.commands[1].argv)
        self.assertIn("0.01", plan.commands[1].argv)
        self.assertEqual(plan.commands[1].argv[-2:], [plan.mbtiles_path, plan.fgb_path])
        self.assertEqual(plan.commands[2].argv, ["pmtiles", "convert", plan.mbtiles_path, plan.pmtiles_path])
        self.assertEqual(plan.maxzoom, 8)

    def test_gdal_mbtiles_plan_converts_to_pmtiles(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.geojson"
            source.write_text('{"type":"FeatureCollection","features":[]}\n')

            plan = vector_asset.build_plan(
                source=source,
                asset_slug="example-asset",
                work_dir=Path(tmp) / "work",
                maxzoom=8,
                maxzoom_reason="GDAL MBTiles fixture.",
            )

        self.assertEqual(plan.commands[1].argv[:3], ["ogr2ogr", "-f", "MBTiles"])
        self.assertEqual(plan.commands[2].argv[1], "convert")

    def test_auto_dry_run_payload_previews_gdal_mbtiles_conversion(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.geojson"
            source.write_text('{"type":"FeatureCollection","features":[]}\n')

            plan = vector_asset.build_plan(
                source=source,
                asset_slug="example-asset",
                work_dir=Path(tmp) / "work",
            )

        payload = vector_asset.dry_run_payload(plan)

        self.assertEqual(payload["commands"][0]["kind"], "command")
        self.assertEqual(payload["pmtiles_commands_after_profile"][0]["kind"], "command")
        self.assertEqual(payload["pmtiles_commands_after_profile"][0]["argv"][:3], ["ogr2ogr", "-f", "MBTiles"])
        self.assertEqual(payload["pmtiles_commands_after_profile"][1]["argv"][1], "convert")

    def test_group_id_field_adds_native_column_generation_before_fgb(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.geojson"
            source.write_text('{"type":"FeatureCollection","features":[]}\n')

            plan = vector_asset.build_plan(
                source=source,
                asset_slug="global-coral-reefs",
                work_dir=Path(tmp) / "work",
                group_id_fields=("NAME",),
            )

        self.assertEqual(plan.group_id_fields, ("NAME",))
        self.assertIn(vector_asset.GENERATED_GROUP_ID_COLUMN, plan.required_properties)
        self.assertIn("shared_dataset_group_ids.py", plan.commands[0].argv[1])
        self.assertIn("--ogr-source", plan.commands[0].argv)
        self.assertIn("--out-map", plan.commands[0].argv)
        self.assertIn("--out-vrt", plan.commands[0].argv)
        self.assertIn("--grouping-field", plan.commands[0].argv)
        self.assertEqual(plan.commands[1].argv[:3], ["ogr2ogr", "-f", "FlatGeobuf"])
        self.assertEqual(plan.commands[1].argv[plan.commands[1].argv.index("-t_srs") + 1], "EPSG:4326")
        self.assertEqual(plan.commands[1].argv[plan.commands[1].argv.index("-sql") + 1], vector_asset.group_id_join_sql())
        self.assertIn(plan.group_id_input_path, plan.commands[1].argv)

    def test_row_id_generation_adds_native_column_without_grouping_field(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.geojson"
            source.write_text('{"type":"FeatureCollection","features":[]}\n')

            plan = vector_asset.build_plan(
                source=source,
                asset_slug="example-asset",
                work_dir=Path(tmp) / "work",
                generate_row_id=True,
            )

        self.assertTrue(plan.generate_row_id)
        self.assertEqual(plan.group_id_fields, ())
        self.assertEqual(plan.generated_id_column, vector_asset.GENERATED_ROW_ID_COLUMN)
        self.assertEqual(plan.generated_id_algorithm, vector_asset.GENERATED_ROW_ID_ALGORITHM)
        self.assertIn(vector_asset.GENERATED_ROW_ID_COLUMN, plan.required_properties)
        self.assertIn("shared_dataset_group_ids.py", plan.commands[0].argv[1])
        self.assertIn("--row-id", plan.commands[0].argv)
        self.assertNotIn("--grouping-field", plan.commands[0].argv)
        self.assertIn("-sql", plan.commands[1].argv)
        self.assertEqual(
            plan.commands[1].argv[plan.commands[1].argv.index("-sql") + 1],
            vector_asset.generated_id_join_sql(vector_asset.GENERATED_ROW_ID_COLUMN),
        )
        self.assertIn(plan.group_id_input_path, plan.commands[1].argv)

    def test_row_id_and_group_id_modes_are_mutually_exclusive(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.geojson"
            source.write_text('{"type":"FeatureCollection","features":[]}\n')

            with self.assertRaisesRegex(ValueError, "cannot be combined"):
                vector_asset.build_plan(
                    source=source,
                    asset_slug="example-asset",
                    work_dir=Path(tmp) / "work",
                    group_id_fields=("NAME",),
                    generate_row_id=True,
                )

    def test_group_id_strict_ambiguity_flag_is_passed_to_helper(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.geojson"
            source.write_text('{"type":"FeatureCollection","features":[]}\n')

            plan = vector_asset.build_plan(
                source=source,
                asset_slug="global-coral-reefs",
                work_dir=Path(tmp) / "work",
                group_id_fields=("NAME",),
                group_id_fail_on_ambiguous_geometry=True,
            )

        self.assertTrue(plan.group_id_fail_on_ambiguous_geometry)
        self.assertIn("--fail-on-ambiguous-geometry", plan.commands[0].argv)

    def test_pmtiles_commands_add_source_layer_for_geometry_only_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.geojson"
            source.write_text('{"type":"FeatureCollection","features":[]}\n')
            plan = vector_asset.build_plan(
                source=source,
                asset_slug="cerulean-s1-envelope",
                work_dir=Path(tmp) / "work",
            )
            profile = pmtiles_zoom.FgbProfile(
                path=plan.fgb_path,
                feature_count=1,
                geometry_types=("MultiPolygon",),
                bounds=(-179, -79, 179, 89),
                point_feature_count=0,
                sampled_feature_count=1,
                sampled_segment_count=4,
                segment_length_m_p25=100_000,
                segment_length_m_p50=100_000,
                feature_min_dimension_m_p10=1_000_000,
                feature_min_dimension_m_p25=1_000_000,
                envelope_like=True,
                property_keys=(),
            )

        commands = vector_asset.pmtiles_commands(plan, 6, profile=profile)

        self.assertEqual(commands[0].kind, "command")
        self.assertIn("-sql", commands[0].argv)
        sql = commands[0].argv[commands[0].argv.index("-sql") + 1]
        self.assertIn("'cerulean_s1_envelope' AS \"source_layer\"", sql)

    def test_pmtiles_commands_preserve_existing_properties_without_synthetic_layer(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.geojson"
            source.write_text('{"type":"FeatureCollection","features":[]}\n')
            plan = vector_asset.build_plan(
                source=source,
                asset_slug="example-asset",
                work_dir=Path(tmp) / "work",
            )
            profile = pmtiles_zoom.FgbProfile(
                path=plan.fgb_path,
                feature_count=1,
                geometry_types=("Point",),
                bounds=(-76.5, 38.9, -76.5, 38.9),
                point_feature_count=1,
                sampled_feature_count=1,
                sampled_segment_count=0,
                segment_length_m_p25=None,
                segment_length_m_p50=None,
                feature_min_dimension_m_p10=None,
                feature_min_dimension_m_p25=None,
                envelope_like=False,
                property_keys=("id",),
            )

        commands = vector_asset.pmtiles_commands(plan, 12, profile=profile)

        self.assertEqual(commands[0].kind, "command")
        self.assertNotIn("-sql", commands[0].argv)

    def test_pmtiles_feature_id_property_projects_tiles_to_metadata_lookup_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.geojson"
            source.write_text('{"type":"FeatureCollection","features":[]}\n')
            plan = vector_asset.build_plan(
                source=source,
                asset_slug="example-asset",
                work_dir=Path(tmp) / "work",
                pmtiles_feature_id_property=vector_asset.FEATURE_ID_COLUMN,
            )
            profile = pmtiles_zoom.FgbProfile(
                path=plan.fgb_path,
                feature_count=1,
                geometry_types=("Point",),
                bounds=(-76.5, 38.9, -76.5, 38.9),
                point_feature_count=1,
                sampled_feature_count=1,
                sampled_segment_count=0,
                segment_length_m_p25=None,
                segment_length_m_p50=None,
                feature_min_dimension_m_p10=None,
                feature_min_dimension_m_p25=None,
                envelope_like=False,
                property_keys=("feature_id", "ext_id", "name", "source_id"),
            )

        commands = vector_asset.pmtiles_commands(plan, 12, profile=profile)

        self.assertIn(vector_asset.FEATURE_ID_COLUMN, plan.required_properties)
        self.assertIn(vector_asset.FEATURE_HASH_COLUMN, plan.required_properties)
        self.assertEqual(
            plan.required_fgb_properties,
            (vector_asset.FEATURE_ID_COLUMN, vector_asset.EXT_ID_COLUMN, vector_asset.FEATURE_HASH_COLUMN),
        )
        self.assertEqual(plan.required_pmtiles_properties, (vector_asset.FEATURE_ID_COLUMN, vector_asset.EXT_ID_COLUMN))
        self.assertEqual(plan.exact_pmtiles_properties, (vector_asset.FEATURE_ID_COLUMN, vector_asset.EXT_ID_COLUMN))
        self.assertEqual(plan.pmtiles_feature_id_property, vector_asset.FEATURE_ID_COLUMN)
        self.assertEqual(commands[0].kind, "command")
        sql = commands[0].argv[commands[0].argv.index("-sql") + 1]
        self.assertEqual(sql, 'SELECT "feature_id", "ext_id" FROM "example_asset"')

    def test_pmtiles_feature_id_property_must_use_standard_feature_id_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.geojson"
            source.write_text('{"type":"FeatureCollection","features":[]}\n')

            with self.assertRaisesRegex(ValueError, "must be feature_id"):
                vector_asset.build_plan(
                    source=source,
                    asset_slug="example-asset",
                    work_dir=Path(tmp) / "work",
                    pmtiles_feature_id_property="source_id",
                )

    def test_low_maxzoom_requires_documented_exception(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.geojson"
            source.write_text('{"type":"FeatureCollection","features":[]}\n')

            with self.assertRaisesRegex(ValueError, "manual PMTiles maxzoom requires"):
                vector_asset.build_plan(
                    source=source,
                    asset_slug="example-asset",
                    work_dir=Path(tmp) / "work",
                    maxzoom=7,
                )

            with self.assertRaisesRegex(ValueError, "below 8"):
                vector_asset.build_plan(
                    source=source,
                    asset_slug="example-asset",
                    work_dir=Path(tmp) / "work",
                    maxzoom=7,
                    maxzoom_reason="Coarse display exception.",
                )

            plan = vector_asset.build_plan(
                source=source,
                asset_slug="example-asset",
                work_dir=Path(tmp) / "work",
                maxzoom=7,
                maxzoom_reason="Coarse display exception.",
                allow_low_maxzoom=True,
            )

        self.assertEqual(plan.maxzoom, 7)

    def test_validation_rejects_pmtiles_features_without_properties(self):
        with tempfile.TemporaryDirectory() as tmp:
            fgb = Path(tmp) / "example.fgb"
            pmtiles = Path(tmp) / "example.pmtiles"
            fgb.write_bytes(b"fgb")
            pmtiles.write_bytes(b"pmtiles")

            def which(name):
                if name in {"pmtiles", "tippecanoe-decode"}:
                    return f"/usr/bin/{name}"
                return None

            def run(command, **kwargs):
                if command[0] == "pmtiles":
                    return SimpleNamespace(returncode=0, stdout="", stderr="")
                if command[0] == "tippecanoe-decode":
                    return SimpleNamespace(
                        returncode=0,
                        stdout=json.dumps(
                            {
                                "features": [
                                    {
                                        "properties": {"layer": "example"},
                                        "features": [{"properties": {}}],
                                    }
                                ]
                            }
                        ),
                        stderr="",
                    )
                raise AssertionError(f"unexpected command: {command}")

            with mock.patch.object(vector_asset.shutil, "which", side_effect=which), mock.patch.object(
                vector_asset.subprocess,
                "run",
                side_effect=run,
            ):
                result = vector_asset.validate_outputs(fgb, pmtiles)

        self.assertFalse(result.valid)
        self.assertIn("no feature properties", result.errors[0])

    def test_validation_accepts_pmtiles_with_inspector_properties(self):
        with tempfile.TemporaryDirectory() as tmp:
            fgb = Path(tmp) / "example.fgb"
            pmtiles = Path(tmp) / "example.pmtiles"
            fgb.write_bytes(b"fgb")
            pmtiles.write_bytes(b"pmtiles")

            def which(name):
                if name in {"pmtiles", "tippecanoe-decode"}:
                    return f"/usr/bin/{name}"
                return None

            def run(command, **kwargs):
                if command[0] == "pmtiles":
                    return SimpleNamespace(returncode=0, stdout="", stderr="")
                if command[0] == "tippecanoe-decode":
                    return SimpleNamespace(
                        returncode=0,
                        stdout=json.dumps(
                            {
                                "features": [
                                    {
                                        "properties": {"layer": "example"},
                                        "features": [{"properties": {"source_layer": "example"}}],
                                    }
                                ]
                            }
                        ),
                        stderr="",
                    )
                raise AssertionError(f"unexpected command: {command}")

            with mock.patch.object(vector_asset.shutil, "which", side_effect=which), mock.patch.object(
                vector_asset.subprocess,
                "run",
                side_effect=run,
            ):
                result = vector_asset.validate_outputs(fgb, pmtiles)

        self.assertTrue(result.valid)
        self.assertEqual(result.errors, ())

    def test_validation_requires_group_id_in_fgb_and_pmtiles(self):
        with tempfile.TemporaryDirectory() as tmp:
            fgb = Path(tmp) / "example.fgb"
            pmtiles = Path(tmp) / "example.pmtiles"
            fgb.write_bytes(b"fgb")
            pmtiles.write_bytes(b"pmtiles")

            def which(name):
                if name in {"ogrinfo", "pmtiles", "tippecanoe-decode"}:
                    return f"/usr/bin/{name}"
                return None

            def run(command, **kwargs):
                if command[:5] == ["ogrinfo", "-ro", "-al", "-so", "-json"]:
                    return SimpleNamespace(
                        returncode=0,
                        stdout=json.dumps(
                            {
                                "layers": [
                                    {
                                        "name": "example",
                                        "featureCount": 1,
                                        "fields": [{"name": vector_asset.GENERATED_GROUP_ID_COLUMN}],
                                    }
                                ]
                            }
                        ),
                        stderr="",
                    )
                if command[:4] == ["ogrinfo", "-ro", "-q", "-dialect"]:
                    return SimpleNamespace(
                        returncode=0,
                        stdout="invalid_geometry_count (Integer) = 0\n",
                        stderr="",
                    )
                if command[0] == "pmtiles":
                    return SimpleNamespace(returncode=0, stdout="", stderr="")
                if command[0] == "tippecanoe-decode":
                    return SimpleNamespace(
                        returncode=0,
                        stdout=json.dumps(
                            {
                                "features": [
                                    {
                                        "properties": {},
                                        "features": [
                                            {"properties": {vector_asset.GENERATED_GROUP_ID_COLUMN: "abc12345"}}
                                        ],
                                    }
                                ]
                            }
                        ),
                        stderr="",
                    )
                raise AssertionError(f"unexpected command: {command}")

            with mock.patch.object(vector_asset.shutil, "which", side_effect=which), mock.patch.object(
                vector_asset.subprocess,
                "run",
                side_effect=run,
            ):
                result = vector_asset.validate_outputs(
                    fgb,
                    pmtiles,
                    required_properties=(vector_asset.GENERATED_GROUP_ID_COLUMN,),
                )

        self.assertTrue(result.valid)
        self.assertEqual(result.required_properties, (vector_asset.GENERATED_GROUP_ID_COLUMN,))

    def test_metadata_mode_validation_requires_fgb_hash_and_pmtiles_lookup_ids_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            fgb = Path(tmp) / "example.fgb"
            pmtiles = Path(tmp) / "example.pmtiles"
            fgb.write_bytes(b"fgb")
            pmtiles.write_bytes(b"pmtiles")

            def which(name):
                if name in {"ogrinfo", "pmtiles", "tippecanoe-decode"}:
                    return f"/usr/bin/{name}"
                return None

            def run(command, **kwargs):
                if command[:5] == ["ogrinfo", "-ro", "-al", "-so", "-json"]:
                    return SimpleNamespace(
                        returncode=0,
                        stdout=json.dumps(
                            {
                                "layers": [
                                    {
                                        "name": "example",
                                        "featureCount": 1,
                                        "fields": [{"name": vector_asset.FEATURE_ID_COLUMN}],
                                    }
                                ]
                            }
                        ),
                        stderr="",
                    )
                if command[:4] == ["ogrinfo", "-ro", "-q", "-dialect"]:
                    return SimpleNamespace(
                        returncode=0,
                        stdout="invalid_geometry_count (Integer) = 0\n",
                        stderr="",
                    )
                if command[0] == "pmtiles":
                    return SimpleNamespace(returncode=0, stdout="", stderr="")
                if command[0] == "tippecanoe-decode":
                    return SimpleNamespace(
                        returncode=0,
                        stdout=json.dumps(
                            {
                                "features": [
                                    {
                                        "features": [
                                            {
                                                "properties": {
                                                    vector_asset.FEATURE_ID_COLUMN: "src:id:1",
                                                    vector_asset.EXT_ID_COLUMN: "1",
                                                    "name": "extra",
                                                }
                                            }
                                        ]
                                    }
                                ]
                            }
                        ),
                        stderr="",
                    )
                raise AssertionError(f"unexpected command: {command}")

            with mock.patch.object(vector_asset.shutil, "which", side_effect=which), mock.patch.object(
                vector_asset.subprocess,
                "run",
                side_effect=run,
            ):
                result = vector_asset.validate_outputs(
                    fgb,
                    pmtiles,
                    required_fgb_properties=(
                        vector_asset.FEATURE_ID_COLUMN,
                        vector_asset.EXT_ID_COLUMN,
                        vector_asset.FEATURE_HASH_COLUMN,
                    ),
                    required_pmtiles_properties=(vector_asset.FEATURE_ID_COLUMN, vector_asset.EXT_ID_COLUMN),
                    exact_pmtiles_properties=(vector_asset.FEATURE_ID_COLUMN, vector_asset.EXT_ID_COLUMN),
                )

        self.assertFalse(result.valid)
        self.assertTrue(any("FGB is missing" in error and "feature_hash" in error for error in result.errors))
        self.assertTrue(any("must be exactly" in error for error in result.errors))

    def test_validation_treats_empty_sampled_tile_as_pmtiles_property_unverified(self):
        with tempfile.TemporaryDirectory() as tmp:
            fgb = Path(tmp) / "example.fgb"
            pmtiles = Path(tmp) / "example.pmtiles"
            fgb.write_bytes(b"fgb")
            pmtiles.write_bytes(b"pmtiles")

            def which(name):
                if name in {"ogrinfo", "pmtiles", "tippecanoe-decode"}:
                    return f"/usr/bin/{name}"
                return None

            def run(command, **kwargs):
                if command[:5] == ["ogrinfo", "-ro", "-al", "-so", "-json"]:
                    return SimpleNamespace(
                        returncode=0,
                        stdout=json.dumps(
                            {
                                "layers": [
                                    {
                                        "name": "example",
                                        "featureCount": 1,
                                        "fields": [{"name": vector_asset.GENERATED_GROUP_ID_COLUMN}],
                                    }
                                ]
                            }
                        ),
                        stderr="",
                    )
                if command[:4] == ["ogrinfo", "-ro", "-q", "-dialect"]:
                    return SimpleNamespace(
                        returncode=0,
                        stdout="invalid_geometry_count (Integer) = 0\n",
                        stderr="",
                    )
                if command[0] == "pmtiles":
                    return SimpleNamespace(returncode=0, stdout="", stderr="")
                if command[0] == "tippecanoe-decode":
                    return SimpleNamespace(returncode=0, stdout=json.dumps({"features": []}), stderr="")
                raise AssertionError(f"unexpected command: {command}")

            with mock.patch.object(vector_asset.shutil, "which", side_effect=which), mock.patch.object(
                vector_asset.subprocess,
                "run",
                side_effect=run,
            ):
                result = vector_asset.validate_outputs(
                    fgb,
                    pmtiles,
                    required_properties=(vector_asset.GENERATED_GROUP_ID_COLUMN,),
                )

        self.assertTrue(result.valid)
        self.assertEqual(result.decoded_feature_count, 0)
        self.assertEqual(result.decoded_property_keys, ())
        self.assertEqual(result.decoded_tile, "0/0/0")

    def test_validation_decodes_required_properties_at_requested_zoom(self):
        with tempfile.TemporaryDirectory() as tmp:
            fgb = Path(tmp) / "example.fgb"
            pmtiles = Path(tmp) / "example.pmtiles"
            fgb.write_bytes(b"fgb")
            pmtiles.write_bytes(b"pmtiles")
            profile = pmtiles_zoom.FgbProfile(
                path=str(fgb),
                feature_count=1,
                geometry_types=("Point",),
                bounds=(-75.0, 40.0, -74.0, 41.0),
                point_feature_count=1,
                sampled_feature_count=1,
                sampled_segment_count=0,
                segment_length_m_p25=None,
                segment_length_m_p50=None,
                feature_min_dimension_m_p10=None,
                feature_min_dimension_m_p25=None,
                envelope_like=False,
                property_keys=(vector_asset.GENERATED_GROUP_ID_COLUMN,),
                errors=(),
            )
            decoded_commands = []

            def which(name):
                if name in {"ogrinfo", "pmtiles", "tippecanoe-decode"}:
                    return f"/usr/bin/{name}"
                return None

            def run(command, **kwargs):
                if command[:5] == ["ogrinfo", "-ro", "-al", "-so", "-json"]:
                    return SimpleNamespace(
                        returncode=0,
                        stdout=json.dumps(
                            {
                                "layers": [
                                    {
                                        "name": "example",
                                        "featureCount": 1,
                                        "fields": [{"name": vector_asset.GENERATED_GROUP_ID_COLUMN}],
                                    }
                                ]
                            }
                        ),
                        stderr="",
                    )
                if command[:4] == ["ogrinfo", "-ro", "-q", "-dialect"]:
                    return SimpleNamespace(
                        returncode=0,
                        stdout="invalid_geometry_count (Integer) = 0\n",
                        stderr="",
                    )
                if command[0] == "pmtiles":
                    return SimpleNamespace(returncode=0, stdout="", stderr="")
                if command[0] == "tippecanoe-decode":
                    decoded_commands.append(command)
                    return SimpleNamespace(
                        returncode=0,
                        stdout=json.dumps(
                            {
                                "features": [
                                    {
                                        "features": [
                                            {"properties": {vector_asset.GENERATED_GROUP_ID_COLUMN: "abc12345"}}
                                        ]
                                    }
                                ]
                            }
                        ),
                        stderr="",
                    )
                raise AssertionError(f"unexpected command: {command}")

            with mock.patch.object(vector_asset.shutil, "which", side_effect=which), mock.patch.object(
                vector_asset.subprocess,
                "run",
                side_effect=run,
            ):
                result = vector_asset.validate_outputs(
                    fgb,
                    pmtiles,
                    profile=profile,
                    required_properties=(vector_asset.GENERATED_GROUP_ID_COLUMN,),
                    decode_zoom=4,
                )

        expected_x, expected_y = vector_asset.lonlat_to_tile(-74.5, 40.5, 4)
        self.assertTrue(result.valid)
        self.assertEqual(decoded_commands[0][2:], ["4", str(expected_x), str(expected_y)])
        self.assertEqual(result.decoded_tile, f"4/{expected_x}/{expected_y}")
        self.assertEqual(result.decoded_property_keys, (vector_asset.GENERATED_GROUP_ID_COLUMN,))
        self.assertIsNone(result.decoded_z0_feature_count)
        self.assertIsNone(result.point_retention_valid)

    def test_validation_rejects_missing_required_group_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            fgb = Path(tmp) / "example.fgb"
            pmtiles = Path(tmp) / "example.pmtiles"
            fgb.write_bytes(b"fgb")
            pmtiles.write_bytes(b"pmtiles")

            def which(name):
                if name in {"ogrinfo", "pmtiles", "tippecanoe-decode"}:
                    return f"/usr/bin/{name}"
                return None

            def run(command, **kwargs):
                if command[:5] == ["ogrinfo", "-ro", "-al", "-so", "-json"]:
                    return SimpleNamespace(
                        returncode=0,
                        stdout=json.dumps({"layers": [{"name": "example", "featureCount": 1, "fields": [{"name": "NAME"}]}]}),
                        stderr="",
                    )
                if command[:4] == ["ogrinfo", "-ro", "-q", "-dialect"]:
                    return SimpleNamespace(
                        returncode=0,
                        stdout="invalid_geometry_count (Integer) = 0\n",
                        stderr="",
                    )
                if command[0] == "pmtiles":
                    return SimpleNamespace(returncode=0, stdout="", stderr="")
                if command[0] == "tippecanoe-decode":
                    return SimpleNamespace(
                        returncode=0,
                        stdout=json.dumps({"features": [{"features": [{"properties": {"NAME": "Alpha"}}]}]}),
                        stderr="",
                    )
                raise AssertionError(f"unexpected command: {command}")

            with mock.patch.object(vector_asset.shutil, "which", side_effect=which), mock.patch.object(
                vector_asset.subprocess,
                "run",
                side_effect=run,
            ):
                result = vector_asset.validate_outputs(
                    fgb,
                    pmtiles,
                    required_properties=(vector_asset.GENERATED_GROUP_ID_COLUMN,),
                )

        self.assertFalse(result.valid)
        self.assertTrue(any("FGB is missing" in error for error in result.errors))
        self.assertTrue(any("PMTiles decoded features are missing" in error for error in result.errors))

    def test_validation_rejects_invalid_fgb_geometries(self):
        with tempfile.TemporaryDirectory() as tmp:
            fgb = Path(tmp) / "example.fgb"
            pmtiles = Path(tmp) / "example.pmtiles"
            fgb.write_bytes(b"fgb")
            pmtiles.write_bytes(b"pmtiles")

            def which(name):
                if name == "ogrinfo":
                    return f"/usr/bin/{name}"
                return None

            def run(command, **kwargs):
                if command[:5] == ["ogrinfo", "-ro", "-al", "-so", "-json"]:
                    return SimpleNamespace(
                        returncode=0,
                        stdout='{"layers":[{"name":"example","featureCount":3,"fields":[]}]}',
                        stderr="",
                    )
                if command[:5] == ["ogrinfo", "-ro", "-q", "-dialect", "SQLite"]:
                    return SimpleNamespace(
                        returncode=0,
                        stdout="Layer name: SELECT\nOGRFeature(SELECT):0\n  invalid_geometry_count (Integer) = 2\n",
                        stderr="",
                    )
                raise AssertionError(f"unexpected command: {command}")

            with mock.patch.object(vector_asset.shutil, "which", side_effect=which), mock.patch.object(
                vector_asset.subprocess,
                "run",
                side_effect=run,
            ):
                result = vector_asset.validate_outputs(fgb, pmtiles)

        self.assertFalse(result.valid)
        self.assertIn("2 invalid geometries", result.errors[0])

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

    def test_scale_and_resolution_formula_examples(self):
        self.assertEqual(pmtiles_zoom.zoom_for_scale_denominator(10_000_000), 8)
        self.assertEqual(pmtiles_zoom.zoom_for_resolution_meters(4_000), 8)

    def test_profile_recommendation_prefers_zoom_12_for_points(self):
        profile = pmtiles_zoom.profile_geojson_features(
            [
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [-76.5, 38.9]},
                    "properties": {},
                }
            ]
        )

        recommendation = pmtiles_zoom.recommend_maxzoom(profile)

        self.assertEqual(recommendation.maxzoom, 12)
        self.assertEqual(recommendation.evidence["source"], "fgb_profile_points")
        self.assertEqual(profile.property_keys, ())

    def test_profile_records_sampled_property_keys(self):
        profile = pmtiles_zoom.profile_geojson_features(
            [
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [-76.5, 38.9]},
                    "properties": {"source_layer": "example", "id": 1},
                }
            ]
        )

        self.assertEqual(profile.property_keys, ("id", "source_layer"))

    def test_profile_recommendation_identifies_coarse_envelope(self):
        profile = pmtiles_zoom.profile_geojson_features(
            [
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[[-170, -70], [170, -70], [170, 70], [-170, 70], [-170, -70]]],
                    },
                    "properties": {},
                }
            ]
        )

        recommendation = pmtiles_zoom.recommend_maxzoom(profile)

        self.assertEqual(recommendation.maxzoom, 6)
        self.assertEqual(recommendation.evidence["source"], "fgb_profile_envelope")

    def test_profile_recommendation_uses_detailed_polygon_geometry(self):
        profile = pmtiles_zoom.profile_geojson_features(
            [
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[[-76.5, 38.9], [-76.4999, 38.9], [-76.4999, 38.9001], [-76.5, 38.9]]],
                    },
                    "properties": {},
                }
            ]
        )

        recommendation = pmtiles_zoom.recommend_maxzoom(profile)

        self.assertEqual(recommendation.maxzoom, 12)
        self.assertEqual(recommendation.evidence["source"], "fgb_profile_geometry_detail")

    def test_run_build_profiles_generated_fgb_before_pmtiles(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.geojson"
            source.write_text('{"type":"FeatureCollection","features":[]}\n')
            plan = vector_asset.build_plan(
                source=source,
                asset_slug="example-asset",
                work_dir=Path(tmp) / "work",
            )
            executed = []

            def fake_run_command(command):
                executed.append(command)
                if command[:3] == ["ogr2ogr", "-f", "FlatGeobuf"]:
                    Path(plan.fgb_path).parent.mkdir(parents=True, exist_ok=True)
                    Path(plan.fgb_path).write_bytes(b"fgb")
                if command[:3] == ["ogr2ogr", "-f", "MBTiles"]:
                    Path(plan.mbtiles_path).parent.mkdir(parents=True, exist_ok=True)
                    Path(plan.mbtiles_path).write_bytes(b"mbtiles")
                if command[:2] == ["pmtiles", "convert"]:
                    Path(plan.pmtiles_path).write_bytes(b"pmtiles")

            profile = pmtiles_zoom.FgbProfile(
                path=plan.fgb_path,
                feature_count=1,
                geometry_types=("Point",),
                bounds=(-76.5, 38.9, -76.5, 38.9),
                point_feature_count=1,
                sampled_feature_count=1,
                sampled_segment_count=0,
                segment_length_m_p25=None,
                segment_length_m_p50=None,
                feature_min_dimension_m_p10=None,
                feature_min_dimension_m_p25=None,
                envelope_like=False,
            )

            with mock.patch.object(vector_asset, "require_executable", return_value="ok"), mock.patch.object(
                vector_asset,
                "run_command",
                side_effect=fake_run_command,
            ), mock.patch.object(vector_asset, "profile_fgb", return_value=profile), mock.patch.object(
                vector_asset.shutil,
                "which",
                return_value=None,
            ):
                result = vector_asset.run_build(plan, overwrite=True)
            payload = json.loads(Path(plan.pmtiles_profile_path).read_text())

            self.assertTrue(result.valid)
            self.assertEqual(executed[0][:3], ["ogr2ogr", "-f", "FlatGeobuf"])
            self.assertEqual(executed[1][:3], ["ogr2ogr", "-f", "MBTiles"])
            self.assertIn("MAXZOOM=12", executed[1])
            self.assertEqual(executed[2], ["pmtiles", "convert", plan.mbtiles_path, plan.pmtiles_path])
            self.assertEqual(payload["recommendation"]["maxzoom"], 12)

    def test_recommend_maxzoom_command_profiles_existing_fgb(self):
        with tempfile.TemporaryDirectory() as tmp:
            fgb = Path(tmp) / "asset.fgb"
            fgb.write_bytes(b"fgb")
            profile = pmtiles_zoom.FgbProfile(
                path=str(fgb),
                feature_count=1,
                geometry_types=("Point",),
                bounds=(-76.5, 38.9, -76.5, 38.9),
                point_feature_count=1,
                sampled_feature_count=1,
                sampled_segment_count=0,
                segment_length_m_p25=None,
                segment_length_m_p50=None,
                feature_min_dimension_m_p10=None,
                feature_min_dimension_m_p25=None,
                envelope_like=False,
            )

            with mock.patch.object(vector_asset, "profile_fgb", return_value=profile), mock.patch("builtins.print") as printed:
                exit_code = vector_asset.main(["recommend-maxzoom", "--fgb", str(fgb)])

        self.assertEqual(exit_code, 0)
        payload = json.loads(printed.call_args.args[0])
        self.assertEqual(payload["recommendation"]["maxzoom"], 12)


if __name__ == "__main__":
    unittest.main()
