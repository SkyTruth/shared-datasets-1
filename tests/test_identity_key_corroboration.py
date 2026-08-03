"""Tests for identity-key corroboration in the ambiguity gate.

The gate compares content hashes. Sources that file several records on one
footprint (WDPA files a National Park and a Ramsar Site on byte-identical
boundaries) therefore produce partial hash matches for records whose identity
never changed. Corroboration suppresses exactly those: when the identity key is
unchanged *and* the previous feature carrying that key has the same geometry
hash, the content agrees with the key and there is nothing to decide.

Both halves are load-bearing, so each is flipped independently below.
"""

from __future__ import annotations

import unittest

from ingestion.common import feature_metadata
from release_streaming_helpers import write_generated_release
from scripts import release_feature_model as model


HASH_PARK = "sha256:" + "a" * 64
HASH_RAMSAR_PROPS = "sha256:" + "b" * 64
HASH_PARK_PROPS = "sha256:" + "c" * 64
HASH_ELSEWHERE = "sha256:" + "d" * 64
HASH_NEW_PROPS = "sha256:" + "e" * 64


def wdpa_baseline() -> list[dict]:
    """Two sibling designations filed on one shared footprint, as WDPA does."""
    return [
        {
            "feature_id": "16969",
            "identity_key": ["12884"],
            "geometry_hash": HASH_PARK,
            "properties_hash": HASH_PARK_PROPS,
        },
        {
            "feature_id": "16970",
            "identity_key": ["902275"],
            "geometry_hash": HASH_PARK,
            "properties_hash": HASH_RAMSAR_PROPS,
        },
    ]


def scan(new_record: dict, previous: list[dict] | None = None, **kwargs) -> model.IdentityAmbiguityScan:
    return model.find_identity_ambiguities(
        [new_record],
        previous_records=previous if previous is not None else wdpa_baseline(),
        **kwargs,
    )


class IdentityKeyCorroborationTests(unittest.TestCase):
    def test_unchanged_record_on_a_shared_footprint_is_suppressed(self):
        """The 983-record class: nothing changed, but a sibling shares the footprint."""
        result = scan(
            {"identity_key": ["12884"], "geometry_hash": HASH_PARK, "properties_hash": HASH_PARK_PROPS}
        )

        self.assertEqual(result.ambiguities, ())
        self.assertEqual(result.key_corroborated_count, 1)

    def test_edited_attributes_on_a_shared_footprint_are_suppressed(self):
        """The 6-record class: shared footprint and upstream attribute edits."""
        result = scan(
            {"identity_key": ["12884"], "geometry_hash": HASH_PARK, "properties_hash": HASH_NEW_PROPS}
        )

        self.assertEqual(result.ambiguities, ())
        self.assertEqual(result.key_corroborated_count, 1)

    def test_edited_attributes_on_a_unique_footprint_are_suppressed(self):
        """The 58-record class: unique footprint, attributes edited upstream."""
        previous = [
            {
                "feature_id": "200",
                "identity_key": ["solo"],
                "geometry_hash": HASH_ELSEWHERE,
                "properties_hash": HASH_PARK_PROPS,
            }
        ]
        result = scan(
            {"identity_key": ["solo"], "geometry_hash": HASH_ELSEWHERE, "properties_hash": HASH_NEW_PROPS},
            previous,
        )

        self.assertEqual(result.ambiguities, ())
        self.assertEqual(result.key_corroborated_count, 1)

    def test_recycled_key_on_different_geometry_still_escalates(self):
        """Upstream reassigned the key to another footprint: identity is in question."""
        result = scan(
            {"identity_key": ["12884"], "geometry_hash": HASH_ELSEWHERE, "properties_hash": HASH_RAMSAR_PROPS}
        )

        self.assertEqual(len(result.ambiguities), 1)
        self.assertEqual(result.key_corroborated_count, 0)

    def test_new_key_landing_on_an_existing_footprint_still_escalates(self):
        result = scan(
            {"identity_key": ["999999"], "geometry_hash": HASH_PARK, "properties_hash": HASH_NEW_PROPS}
        )

        self.assertEqual(len(result.ambiguities), 1)
        self.assertEqual(result.ambiguities[0].ambiguity_type, "same_geometry_changed_properties")
        self.assertEqual(result.key_corroborated_count, 0)

    def test_unchanged_key_with_moved_geometry_still_escalates(self):
        previous = [
            {
                "feature_id": "7",
                "identity_key": ["solo"],
                "geometry_hash": HASH_ELSEWHERE,
                "properties_hash": HASH_PARK_PROPS,
            }
        ]
        result = scan(
            {"identity_key": ["solo"], "geometry_hash": HASH_PARK, "properties_hash": HASH_PARK_PROPS},
            previous,
        )

        self.assertEqual(len(result.ambiguities), 1)
        self.assertEqual(result.key_corroborated_count, 0)

    def test_suppression_requires_both_halves(self):
        record = {"identity_key": ["12884"], "geometry_hash": HASH_PARK, "properties_hash": HASH_NEW_PROPS}

        self.assertEqual(scan(record).ambiguities, (), "key + geometry should suppress")

        wrong_key = dict(record, identity_key=["different"])
        self.assertEqual(len(scan(wrong_key).ambiguities), 1, "key alone missing must escalate")

        # Geometry differing from the key's own previous footprint must escalate
        # whenever it lands on some other previous feature. (A footprint matching
        # nothing at all was, and remains, silent — there is no prior feature to
        # confuse it with.)
        neighbour = wdpa_baseline() + [
            {
                "feature_id": "40000",
                "identity_key": ["neighbour"],
                "geometry_hash": HASH_ELSEWHERE,
                "properties_hash": HASH_RAMSAR_PROPS,
            }
        ]
        wrong_geometry = dict(record, geometry_hash=HASH_ELSEWHERE)
        self.assertEqual(
            len(scan(wrong_geometry, neighbour).ambiguities),
            1,
            "geometry alone missing must escalate",
        )

    def test_a_key_that_mapped_to_two_footprints_never_corroborates(self):
        previous = [
            {"feature_id": "1", "identity_key": ["dupe"], "geometry_hash": HASH_PARK, "properties_hash": HASH_PARK_PROPS},
            {"feature_id": "2", "identity_key": ["dupe"], "geometry_hash": HASH_ELSEWHERE, "properties_hash": HASH_RAMSAR_PROPS},
        ]
        result = scan(
            {"identity_key": ["dupe"], "geometry_hash": HASH_PARK, "properties_hash": HASH_NEW_PROPS},
            previous,
        )

        self.assertEqual(len(result.ambiguities), 1)
        self.assertEqual(result.key_corroborated_count, 0)

    def test_unchanged_key_is_treated_consistently_however_much_changed(self):
        """Resolves the old inconsistency: the mildest change no longer escalates
        while the most drastic one passes silently."""
        previous = [
            {
                "feature_id": "7",
                "identity_key": ["solo"],
                "geometry_hash": HASH_ELSEWHERE,
                "properties_hash": HASH_PARK_PROPS,
            }
        ]
        attributes_only = scan(
            {"identity_key": ["solo"], "geometry_hash": HASH_ELSEWHERE, "properties_hash": HASH_NEW_PROPS},
            previous,
        )
        both_changed = scan(
            {"identity_key": ["solo"], "geometry_hash": "sha256:" + "f" * 64, "properties_hash": HASH_NEW_PROPS},
            previous,
        )

        self.assertEqual(attributes_only.ambiguities, ())
        self.assertEqual(both_changed.ambiguities, ())

    def test_content_hash_identity_assets_are_unaffected(self):
        """sea-ice keys are derived from content, so a changed record changes its
        key and cannot be corroborated."""
        previous = [
            {
                "feature_id": "7",
                "identity_key": [HASH_PARK, HASH_PARK_PROPS],
                "geometry_hash": HASH_PARK,
                "properties_hash": HASH_PARK_PROPS,
            }
        ]
        result = scan(
            {
                "identity_key": [HASH_PARK, HASH_NEW_PROPS],
                "geometry_hash": HASH_PARK,
                "properties_hash": HASH_NEW_PROPS,
            },
            previous,
            match_properties=False,
        )

        self.assertEqual(len(result.ambiguities), 1)
        self.assertEqual(result.key_corroborated_count, 0)


class CorroborationEndToEndTests(unittest.TestCase):
    def test_a_shared_footprint_release_publishes_without_reviewed_decisions(self):
        """The August wdpa-marine shape: siblings on one footprint, one renumbered."""
        geometry = {"type": "Point", "coordinates": [0, 0]}
        park_props = {"SITE_PID": "12884", "NAME": "Isla Contoy", "DESIG": "National Park"}
        ramsar_props = {"SITE_PID": "902275", "NAME": "Parque Nacional Isla Contoy", "DESIG": "Ramsar"}
        previous = []
        for feature_id, props in (("16969", park_props), ("16970", ramsar_props)):
            geometry_hash, properties_hash = feature_metadata.content_hashes(
                geometry=geometry, properties=props, exclude_properties=()
            )
            previous.append(
                {
                    "feature_id": feature_id,
                    "identity_key": [props["SITE_PID"]],
                    "geometry_hash": geometry_hash,
                    "properties_hash": properties_hash,
                }
            )
        features = [
            {"type": "Feature", "properties": dict(park_props), "geometry": geometry},
            {"type": "Feature", "properties": dict(ramsar_props), "geometry": geometry},
        ]

        _enriched, sidecar, result = write_generated_release(
            features,
            asset_slug="wdpa-marine",
            release="2026-09-01",
            provenance={},
            source_fields=["SITE_PID"],
            previous_records=previous,
        )

        self.assertEqual([record["feature_id"] for record in sidecar], ["16969", "16970"])
        decisions = result.identity_decisions
        self.assertEqual(decisions["ambiguities_detected"], 2)
        self.assertEqual(decisions["auto_resolved_key_corroborated"], 2)
        self.assertEqual(decisions["escalated_for_review"], 0)
        self.assertEqual(decisions["reviewed_decisions_applied"], 0)
        self.assertEqual(decisions["policy"], model.IDENTITY_DECISION_POLICY)


class IdentityDecisionProvenanceTests(unittest.TestCase):
    def resolution(self, action: str = "reuse_previous_feature_id") -> model.IdentityResolution:
        return model.IdentityResolution(
            action=action,
            release="2026-08-01",
            identity_key=("2",),
            geometry_hash=HASH_PARK,
            properties_hash=HASH_NEW_PROPS,
            matching_geometry_feature_ids=("7",),
            matching_properties_feature_ids=(),
            matching_geometry_properties_hashes=(HASH_PARK_PROPS,),
            matching_properties_geometry_hashes=(),
            rationale="Same footprint; upstream renamed the site.",
            reviewer="jonaraphael",
            pr_reference="https://github.com/SkyTruth/shared-datasets-1/pull/133",
            reuse_feature_id="7",
        )

    def test_decisions_record_counts_actions_reviewers_and_references(self):
        decisions = model.build_identity_decisions(
            ambiguities_detected=1,
            key_corroborated=15229,
            resolutions=[self.resolution()],
        )

        self.assertEqual(decisions["ambiguities_detected"], 15230)
        self.assertEqual(decisions["auto_resolved_key_corroborated"], 15229)
        self.assertEqual(decisions["escalated_for_review"], 1)
        self.assertEqual(decisions["reviewed_decisions_applied"], 1)
        self.assertEqual(decisions["reviewed_decision_actions"], {"reuse_previous_feature_id": 1})
        self.assertEqual(decisions["reviewers"], ["jonaraphael"])
        self.assertEqual(
            decisions["reviewed_decision_references"],
            ["https://github.com/SkyTruth/shared-datasets-1/pull/133"],
        )
        model.validate_identity_decisions(decisions)

    def test_decisions_travel_inside_the_published_manifest_identity(self):
        identity = model.build_identity_metadata(
            strategy="generated_sequence_source_fields",
            source_fields=["SITE_PID"],
            next_generated_feature_id_after_release=304611,
            decisions=model.build_identity_decisions(
                ambiguities_detected=0,
                key_corroborated=15229,
                resolutions=(),
            ),
        )

        self.assertEqual(identity["decisions"]["auto_resolved_key_corroborated"], 15229)
        self.assertEqual(identity["decisions"]["escalated_for_review"], 0)
        model.validate_identity_metadata(identity)

    def test_manifests_published_before_decision_provenance_stay_valid(self):
        identity = model.build_identity_metadata(
            strategy="generated_sequence_source_fields",
            source_fields=["SITE_PID"],
            next_generated_feature_id_after_release=1,
        )

        self.assertNotIn("decisions", identity)
        model.validate_identity_metadata(identity)

    def test_unaccounted_escalations_are_rejected(self):
        identity = model.build_identity_metadata(
            strategy="generated_sequence_source_fields",
            source_fields=["SITE_PID"],
            next_generated_feature_id_after_release=1,
            decisions=model.build_identity_decisions(
                ambiguities_detected=0,
                key_corroborated=3,
                resolutions=(),
            ),
        )
        identity["decisions"]["escalated_for_review"] = 2

        with self.assertRaisesRegex(model.ReleaseFeatureModelError, "account for every escalated ambiguity"):
            model.validate_identity_metadata(identity)

    def test_counts_that_do_not_sum_are_rejected(self):
        with self.assertRaisesRegex(model.ReleaseFeatureModelError, "must sum to ambiguities_detected"):
            model.validate_identity_decisions(
                {
                    "schema_version": model.IDENTITY_DECISIONS_SCHEMA_VERSION,
                    "policy": model.IDENTITY_DECISION_POLICY,
                    "ambiguities_detected": 10,
                    "auto_resolved_key_corroborated": 3,
                    "escalated_for_review": 3,
                    "reviewed_decisions_applied": 3,
                }
            )


if __name__ == "__main__":
    unittest.main()
