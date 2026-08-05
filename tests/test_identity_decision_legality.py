"""Which action a decision may use is decided by state, not by judgement.

The August wdpa-terrestrial review picked `reuse_previous_feature_id` for a key
that already owned a feature_id. That merges two records, and it was only caught
72 minutes into a job by a feature_id override conflict. The rule that rules it
out needs no context beyond the decision and the previous release, so these tests
pin it as a rule: every state has a determined set of legal actions, and every
rejection names the action to use instead.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts import check_identity_resolutions as checker
from scripts import release_feature_model as model


HASH_A = "sha256:" + "a" * 64
HASH_B = "sha256:" + "b" * 64


def ambiguity(identity_key: tuple[str, ...], matched: tuple[str, ...] = ("300615",)) -> model.IdentityAmbiguity:
    return model.IdentityAmbiguity(
        ambiguity_type="same_geometry_changed_properties",
        identity_key=identity_key,
        geometry_hash=HASH_A,
        properties_hash=HASH_B,
        matching_geometry_feature_ids=matched,
        matching_properties_feature_ids=(),
    )


def legality(action: str, *, key: str, reuse: str | None, baseline: dict[tuple[str, ...], str]) -> str:
    return model.identity_decision_legality(
        action=action,
        identity_key=(key,),
        reuse_feature_id=reuse,
        ambiguity=ambiguity((key,)),
        previous_feature_ids=baseline,
    )


class LegalActionsByStateTests(unittest.TestCase):
    """One fact — does the key already own a feature_id — settles the mechanism."""

    KEY_OWNS = {("555682754",): "300616"}
    KEY_NEW: dict[tuple[str, ...], str] = {}

    def test_key_that_owns_an_id_may_not_reuse_another_feature(self):
        problem = legality(
            "reuse_previous_feature_id", key="555682754", reuse="300615", baseline=self.KEY_OWNS
        )

        self.assertIn("already owns feature_id 300616", problem)
        self.assertIn("merge two records", problem)
        # The rejection must name the correct action, not just refuse.
        self.assertIn("keep_previous_key_mapping", problem)

    def test_key_that_owns_an_id_may_keep_its_mapping(self):
        self.assertEqual(
            legality("keep_previous_key_mapping", key="555682754", reuse=None, baseline=self.KEY_OWNS),
            "",
        )

    def test_key_that_owns_an_id_may_deliberately_take_a_new_one(self):
        self.assertEqual(
            legality("assign_new_feature_id", key="555682754", reuse=None, baseline=self.KEY_OWNS),
            "",
        )

    def test_new_key_may_reuse_a_matched_feature(self):
        self.assertEqual(
            legality("reuse_previous_feature_id", key="555884979", reuse="297926", baseline=self.KEY_NEW),
            "",
        )

    def test_new_key_may_not_keep_a_mapping_it_does_not_have(self):
        problem = legality(
            "keep_previous_key_mapping", key="555884979", reuse=None, baseline=self.KEY_NEW
        )

        self.assertIn("no feature_id in the previous release", problem)
        self.assertIn("reuse_previous_feature_id", problem)
        self.assertIn("assign_new_feature_id", problem)

    def test_reuse_naming_the_keys_own_id_is_allowed(self):
        """Redundant but not wrong: the outcome equals keeping the mapping."""
        self.assertEqual(
            legality("reuse_previous_feature_id", key="555682754", reuse="300616", baseline=self.KEY_OWNS),
            "",
        )

    def test_every_action_is_covered_by_the_rule(self):
        """A new action cannot be added without deciding its legality."""
        for action in model.IDENTITY_RESOLUTION_ACTIONS:
            with self.subTest(action=action):
                for baseline in (self.KEY_OWNS, self.KEY_NEW):
                    reuse = "300615" if action == "reuse_previous_feature_id" else None
                    # Must not raise for any known action in any state.
                    legality(action, key="555682754", reuse=reuse, baseline=baseline)


class ValidationEnforcesLegalityTests(unittest.TestCase):
    def decision(self, action: str, **extra) -> dict:
        return {
            "release": "2026-08-01",
            "action": action,
            "new_identity_key": ["555682754"],
            "new_geometry_hash": HASH_A,
            "new_properties_hash": HASH_B,
            "matching_geometry_feature_ids": ["300615"],
            "matching_properties_feature_ids": [],
            "matching_geometry_properties_hashes": [],
            "matching_properties_geometry_hashes": [],
            "rationale": "r",
            "reviewer": "jonaraphael",
            "pr_reference": "https://github.com/SkyTruth/shared-datasets-1/pull/137",
            **extra,
        }

    def test_validation_refuses_the_illegal_action_when_given_the_baseline(self):
        with self.assertRaisesRegex(model.ReleaseFeatureModelError, "already owns feature_id 300616"):
            model.validate_identity_resolutions(
                release="2026-08-01",
                ambiguities=[ambiguity(("555682754",))],
                decisions=[self.decision("reuse_previous_feature_id", reuse_feature_id="300615")],
                previous_feature_ids={("555682754",): "300616"},
            )

    def test_validation_without_a_baseline_stays_backward_compatible(self):
        resolutions = model.validate_identity_resolutions(
            release="2026-08-01",
            ambiguities=[ambiguity(("555682754",))],
            decisions=[self.decision("reuse_previous_feature_id", reuse_feature_id="300615")],
        )

        self.assertEqual(len(resolutions), 1)

    def test_reuse_target_still_held_in_this_release_is_refused(self):
        resolutions = model.validate_identity_resolutions(
            release="2026-08-01",
            ambiguities=[ambiguity(("555884979",))],
            decisions=[
                self.decision(
                    "reuse_previous_feature_id",
                    new_identity_key=["555884979"],
                    reuse_feature_id="300615",
                )
            ],
        )

        collisions = model.check_reuse_target_collisions(
            resolutions,
            # The key that still holds 300615 is publishing in this release too.
            release_identity_keys=[("555884979",), ("555682755",)],
            previous_feature_ids={("555682755",): "300615"},
        )

        self.assertEqual(len(collisions), 1)
        self.assertIn("still holds it in this release", collisions[0])
        self.assertIn("assign_new_feature_id", collisions[0])

    def test_no_collision_when_the_holder_has_left_the_source(self):
        resolutions = model.validate_identity_resolutions(
            release="2026-08-01",
            ambiguities=[ambiguity(("555884979",))],
            decisions=[
                self.decision(
                    "reuse_previous_feature_id",
                    new_identity_key=["555884979"],
                    reuse_feature_id="300615",
                )
            ],
        )

        self.assertEqual(
            model.check_reuse_target_collisions(
                resolutions,
                release_identity_keys=[("555884979",)],
                previous_feature_ids={("555682755",): "300615"},
            ),
            (),
        )


class CheckerCliTests(unittest.TestCase):
    """The pre-merge checker reproduces the rule offline, given a baseline."""

    def write_decisions(self, tmp: Path, action: str, **extra) -> Path:
        path = tmp / "wdpa-terrestrial.json"
        path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "asset_slug": "wdpa-terrestrial",
                    "decisions": [
                        {
                            "release": "2026-08-01",
                            "action": action,
                            "new_identity_key": ["555682754"],
                            "new_geometry_hash": HASH_A,
                            "new_properties_hash": HASH_B,
                            "matching_geometry_feature_ids": ["300615"],
                            "matching_properties_feature_ids": [],
                            "rationale": "r",
                            "reviewer": "jonaraphael",
                            "pr_reference": "https://example.invalid/pr/1",
                            **extra,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        return path

    def test_checker_flags_the_august_mistake(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write_decisions(Path(tmp), "reuse_previous_feature_id", reuse_feature_id="300615")

            problems = checker.check_file(path, previous_feature_ids={("555682754",): "300616"})

        self.assertEqual(len(problems), 1)
        self.assertIn("already owns feature_id 300616", problems[0])
        self.assertIn("keep_previous_key_mapping", problems[0])

    def test_checker_accepts_the_corrected_decision(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write_decisions(Path(tmp), "keep_previous_key_mapping")

            self.assertEqual(
                checker.check_file(path, previous_feature_ids={("555682754",): "300616"}),
                [],
            )

    def test_checker_rejects_an_unknown_action(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write_decisions(Path(tmp), "do_whatever_seems_right")

            problems = checker.check_file(path, previous_feature_ids={})

        self.assertEqual(len(problems), 1)
        self.assertIn("unsupported action", problems[0])

    def test_shipped_terrestrial_decisions_pass_the_rule(self):
        repo_root = Path(__file__).resolve().parents[1]
        path = repo_root / "catalog/feature-identity-resolutions/wdpa-terrestrial.json"
        # 555682754 already owned 300616; the other six keys were new.
        baseline = {("555682754",): "300616"}

        self.assertEqual(checker.check_file(path, previous_feature_ids=baseline), [])


if __name__ == "__main__":
    unittest.main()
