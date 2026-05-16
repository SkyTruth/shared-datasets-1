from __future__ import annotations

import unittest
import tempfile
from pathlib import Path
from unittest import mock

from scripts import shared_dataset_group_ids as group_ids


def feature(name: str | None, x: float, y: float = 0.0) -> dict:
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [x, y]},
        "properties": {"NAME": name},
    }


class SharedDatasetGroupIdTests(unittest.TestCase):
    def test_token_length_matches_policy_examples(self):
        self.assertEqual(group_ids.token_length_for_group_count(200), 8)
        self.assertEqual(group_ids.token_length_for_group_count(17_500), 10)
        self.assertEqual(group_ids.token_length_for_group_count(1_600_000), 13)

    def test_groups_rows_by_curated_field_and_hashes_collective_geometry(self):
        features = [feature("Alpha", 0), feature("Beta", 1), feature("Alpha", 2)]

        result = group_ids.add_group_ids(features, asset_slug="global-coral-reefs", grouping_fields=["NAME"])

        alpha_id = result.features[0]["properties"]["shared_datasets_group_id"]
        self.assertEqual(result.group_count, 2)
        self.assertEqual(result.token_length, 8)
        self.assertEqual(alpha_id, result.features[2]["properties"]["shared_datasets_group_id"])
        self.assertNotEqual(alpha_id, result.features[1]["properties"]["shared_datasets_group_id"])
        self.assertNotIn("shared_datasets_group_id", features[0]["properties"])

    def test_generation_is_deterministic(self):
        features = [feature("Alpha", 0), feature("Beta", 1), feature("Alpha", 2)]

        one = group_ids.add_group_ids(features, asset_slug="global-coral-reefs", grouping_fields=["NAME"])
        two = group_ids.add_group_ids(features, asset_slug="global-coral-reefs", grouping_fields=["NAME"])

        self.assertEqual(
            [item["properties"]["shared_datasets_group_id"] for item in one.features],
            [item["properties"]["shared_datasets_group_id"] for item in two.features],
        )

    def test_blank_values_do_not_collapse_to_one_group(self):
        features = [feature(None, 0), feature("", 1), feature("   ", 2)]

        result = group_ids.add_group_ids(features, asset_slug="example-asset", grouping_fields=["NAME"])

        ids = [item["properties"]["shared_datasets_group_id"] for item in result.features]
        self.assertEqual(result.group_count, 3)
        self.assertEqual(result.blank_group_count, 3)
        self.assertEqual(len(set(ids)), 3)
        self.assertTrue(any("blank/null" in warning for warning in result.warnings))

    def test_identical_geometry_groups_are_reported_as_ambiguous_and_share_id(self):
        features = [feature("Alpha", 0), feature("Beta", 0)]

        result = group_ids.add_group_ids(features, asset_slug="example-asset", grouping_fields=["NAME"])

        self.assertEqual(result.group_count, 2)
        self.assertEqual(result.identical_preimage_group_count, 2)
        self.assertEqual(
            result.features[0]["properties"]["shared_datasets_group_id"],
            result.features[1]["properties"]["shared_datasets_group_id"],
        )
        self.assertEqual(len(result.ambiguous_identical_geometry_groups), 1)
        self.assertEqual(
            result.ambiguous_identical_geometry_groups[0].group_keys,
            (("__group__", ("Alpha",)), ("__group__", ("Beta",))),
        )
        self.assertTrue(any("potential aliases/duplicates" in warning for warning in result.warnings))

    def test_strict_mode_rejects_identical_geometry_ambiguity(self):
        features = [feature("Alpha", 0), feature("Beta", 0)]

        with self.assertRaisesRegex(group_ids.GroupIdError, "share identical collective geometry"):
            group_ids.add_group_ids(
                features,
                asset_slug="example-asset",
                grouping_fields=["NAME"],
                fail_on_ambiguous_geometry=True,
            )

    def test_explicit_token_length_rejects_true_hash_prefix_collision(self):
        features = [feature(f"Group {index}", index) for index in range(80)]

        with self.assertRaisesRegex(group_ids.GroupIdError, "at least 8"):
            group_ids.add_group_ids(
                features,
                asset_slug="example-asset",
                grouping_fields=["NAME"],
                token_length=1,
            )

    def test_auto_token_length_extends_for_true_hash_prefix_collision(self):
        features = [feature("Alpha", 0), feature("Beta", 1)]
        real_base62_token = group_ids.base62_token

        def colliding_once(digest, token_length):
            if token_length == 8:
                return "A" * 8
            return real_base62_token(digest, token_length)

        with mock.patch.object(group_ids, "base62_token", side_effect=colliding_once):
            result = group_ids.add_group_ids(features, asset_slug="example-asset", grouping_fields=["NAME"])

        self.assertEqual(result.token_length, 9)
        ids = [item["properties"]["shared_datasets_group_id"] for item in result.features]
        self.assertEqual(len(ids), len(set(ids)))

    def test_row_group_id_result_writes_rowid_map_and_vrt(self):
        features = [feature("Alpha", 0), feature("Beta", 1), feature("Alpha", 2)]

        result = group_ids.build_row_group_ids(features, asset_slug="example-asset", grouping_fields=["NAME"])

        self.assertEqual(result.group_count, 2)
        self.assertEqual(result.row_tokens[0], result.row_tokens[2])
        self.assertNotEqual(result.row_tokens[0], result.row_tokens[1])
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            map_path = root / "group-id-map.csv"
            vrt_path = root / "source.vrt"
            source_path = root / "source.fgb"
            group_ids.write_group_id_map_csv(map_path, result)
            group_ids.write_group_id_vrt(vrt_path, source=source_path, source_layer="source_layer", map_path=map_path)

            self.assertIn("rowid,shared_datasets_group_id", map_path.read_text().splitlines()[0])
            self.assertIn(f"0,{result.row_tokens[0]}", map_path.read_text())
            self.assertIn("<SrcLayer>source_layer</SrcLayer>", vrt_path.read_text())
            self.assertIn(str(map_path), vrt_path.read_text())


if __name__ == "__main__":
    unittest.main()
