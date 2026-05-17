from __future__ import annotations

import datetime as dt
import unittest

from scripts import scratch_cleanup


def blob(
    name: str,
    *,
    days_old: int = 0,
    size: int = 10,
    generation: str = "1",
    crc32c: str = "abc",
) -> scratch_cleanup.BlobRecord:
    now = dt.datetime(2026, 5, 17, tzinfo=dt.UTC)
    return scratch_cleanup.BlobRecord(
        name=name,
        size=size,
        updated=now - dt.timedelta(days=days_old),
        generation=generation,
        crc32c=crc32c,
    )


class ScratchCleanupTests(unittest.TestCase):
    def test_groups_pending_publish_prefixes_by_asset_and_proposal(self):
        proposals = scratch_cleanup.group_pending_blobs(
            [
                blob("_scratch/pending-publishes/example-asset/pr-1/example-asset.fgb"),
                blob("_scratch/pending-publishes/example-asset/pr-1/example-asset.pmtiles"),
                blob("_scratch/pending-publishes/other-asset/pr-2/other-asset.fgb"),
                blob("_scratch/README.md"),
            ]
        )

        self.assertEqual([proposal.prefix for proposal in proposals], [
            "_scratch/pending-publishes/example-asset/pr-1/",
            "_scratch/pending-publishes/other-asset/pr-2/",
        ])
        self.assertEqual(len(proposals[0].blobs), 2)

    def test_matching_release_requires_filename_size_and_crc32c_match(self):
        proposal = scratch_cleanup.group_pending_blobs(
            [
                blob(
                    "_scratch/pending-publishes/example-asset/pr-1/example-asset.fgb",
                    size=42,
                    crc32c="same",
                )
            ]
        )[0]

        self.assertTrue(
            scratch_cleanup.proposal_has_matching_release(
                proposal,
                [
                    blob(
                        "100-geographic-reference/130-protected-areas/example-asset/releases/"
                        "2026-05-01/example-asset.fgb",
                        size=42,
                        crc32c="same",
                    )
                ],
            )
        )
        self.assertFalse(
            scratch_cleanup.proposal_has_matching_release(
                proposal,
                [
                    blob(
                        "100-geographic-reference/130-protected-areas/example-asset/releases/"
                        "2026-05-01/example-asset.fgb",
                        size=43,
                        crc32c="same",
                    )
                ],
            )
        )

    def test_stale_prefix_warns_before_it_can_be_deleted(self):
        now = dt.datetime(2026, 5, 17, tzinfo=dt.UTC)
        proposal = scratch_cleanup.group_pending_blobs(
            [blob("_scratch/pending-publishes/example-asset/pr-1/example-asset.fgb", days_old=95)]
        )[0]

        decision = scratch_cleanup.classify_proposal(
            proposal,
            now=now,
            warn_age_days=60,
            delete_age_days=90,
            has_matching_release=False,
            warning_marker=None,
        )

        self.assertEqual(decision["action"], "warn")
        self.assertEqual(decision["reason"], "stale-warning")

    def test_stale_prefix_deletes_after_warning_if_no_file_changed(self):
        now = dt.datetime(2026, 5, 17, tzinfo=dt.UTC)
        proposal = scratch_cleanup.group_pending_blobs(
            [
                blob(
                    "_scratch/pending-publishes/example-asset/pr-1/example-asset.fgb",
                    days_old=95,
                    generation="7",
                )
            ]
        )[0]
        marker = scratch_cleanup.WarningMarker(
            name=proposal.marker_name,
            generation="2",
            newest_object_name=proposal.newest_blob.name,
            newest_generation=proposal.newest_blob.generation,
            newest_updated=proposal.newest_blob.updated.isoformat(),
        )

        decision = scratch_cleanup.classify_proposal(
            proposal,
            now=now,
            warn_age_days=60,
            delete_age_days=90,
            has_matching_release=False,
            warning_marker=marker,
        )

        self.assertEqual(decision["action"], "delete")
        self.assertEqual(decision["reason"], "stale-after-warning")

    def test_changed_file_invalidates_prior_warning_marker(self):
        now = dt.datetime(2026, 5, 17, tzinfo=dt.UTC)
        proposal = scratch_cleanup.group_pending_blobs(
            [
                blob(
                    "_scratch/pending-publishes/example-asset/pr-1/example-asset.fgb",
                    days_old=30,
                    generation="8",
                )
            ]
        )[0]
        marker = scratch_cleanup.WarningMarker(
            name=proposal.marker_name,
            generation="2",
            newest_object_name=proposal.newest_blob.name,
            newest_generation="7",
            newest_updated=(proposal.newest_blob.updated - dt.timedelta(days=70)).isoformat(),
        )

        decision = scratch_cleanup.classify_proposal(
            proposal,
            now=now,
            warn_age_days=60,
            delete_age_days=90,
            has_matching_release=False,
            warning_marker=marker,
        )

        self.assertEqual(decision["action"], "keep")


if __name__ == "__main__":
    unittest.main()
