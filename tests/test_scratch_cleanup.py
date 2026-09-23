from __future__ import annotations

import datetime as dt
import json
import unittest
from dataclasses import dataclass, replace
from unittest.mock import Mock, mock_open, patch

from google.api_core.exceptions import NotFound, PreconditionFailed
from typer.testing import CliRunner

from scripts import scratch_cleanup


def blob(
    name: str,
    *,
    days_old: int = 0,
    size: int = 10,
    generation: str = "1",
) -> scratch_cleanup.BlobRecord:
    now = dt.datetime(2026, 5, 17, tzinfo=dt.UTC)
    return scratch_cleanup.BlobRecord(
        name=name,
        size=size,
        updated=now - dt.timedelta(days=days_old),
        generation=generation,
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
            warning_marker=marker,
        )

        self.assertEqual(decision["action"], "keep")


NOW = dt.datetime(2026, 5, 17, tzinfo=dt.UTC)
PREFIX = "_scratch/pending-publishes/example-asset/pr-1/"
MARKER = "_scratch/cleanup-audit/pending-publishes/example-asset/pr-1.json"
ASSET_ROOT = "100-geographic-reference/130-protected-areas/example-asset"


@dataclass(frozen=True)
class StoredObject:
    name: str
    generation: str = "1"
    updated: dt.datetime = NOW
    content: str = "unchanged data"
    crc32c: str = "same"


def warning_for(obj, *, generation="55"):
    return StoredObject(
        MARKER,
        generation,
        content=json.dumps({
            "asset_slug": "example-asset",
            "proposal_id": "pr-1",
            "prefix": PREFIX,
            "warned_at": (NOW - dt.timedelta(days=30)).isoformat(),
            "newest_object_name": obj.name,
            "newest_generation": obj.generation,
            "newest_updated": obj.updated.isoformat(),
        }),
    )


class FakeBlob:
    def __init__(self, client, name, stored=None):
        self.client = client
        self.name = name
        self.generation = stored.generation if stored else None
        self.updated = stored.updated if stored else None
        self.size = len(stored.content) if stored else None
        self.crc32c = stored.crc32c if stored else None

    def download_as_text(self, *, if_generation_match):
        self.client.downloads.append((self.name, self.generation, if_generation_match))
        if int(self.generation) != if_generation_match:
            raise PreconditionFailed("download did not match listed generation")
        if self.client.before_download:
            self.client.before_download(self.name)
        current = self.client.objects.get(self.name)
        # A blob returned by list_blobs is pinned to its listed generation.
        if current is None or current.generation != self.generation:
            raise NotFound("listed generation no longer exists")
        return current.content

    def upload_from_string(self, content, *, content_type, if_generation_match):
        self.client.uploads.append((self.name, if_generation_match, content_type))
        if self.client.before_upload:
            self.client.before_upload(self.name)
        current = self.client.objects.get(self.name)
        actual = int(current.generation) if current else 0
        if actual != if_generation_match:
            raise PreconditionFailed("object changed before upload")
        self.client.objects[self.name] = StoredObject(
            self.name, str(actual + 100), content=content
        )

    def delete(self, *, if_generation_match):
        self.client.deletes.append((self.name, if_generation_match))
        if self.client.before_delete:
            self.client.before_delete(self.name)
        current = self.client.objects.get(self.name)
        if current is None:
            raise NotFound("object already gone")
        if int(current.generation) != if_generation_match:
            raise PreconditionFailed("object changed before delete")
        del self.client.objects[self.name]


class FakeClient:
    def __init__(self, objects):
        self.objects = {obj.name: obj for obj in objects}
        self.listed_prefixes = []
        self.downloads = []
        self.uploads = []
        self.deletes = []
        self.before_download = None
        self.before_upload = None
        self.before_delete = None

    def list_blobs(self, bucket, *, prefix):
        self.listed_prefixes.append(prefix)
        return [
            FakeBlob(self, obj.name, obj)
            for obj in self.objects.values()
            if obj.name.startswith(prefix)
        ]

    def bucket(self, bucket):
        return self

    def blob(self, name):
        return FakeBlob(self, name)


class ScratchCleanupAuditTests(unittest.TestCase):
    def audit(self, client, *, apply_changes=True, summary_path=None):
        return scratch_cleanup.run_cleanup(
            client=client,
            bucket="bucket",
            now=NOW,
            warn_age_days=60,
            delete_age_days=90,
            apply_changes=apply_changes,
            summary_path=summary_path,
        )

    def mixed_proposal(self, *, days_old):
        updated = NOW - dt.timedelta(days=days_old)
        return [
            StoredObject(PREFIX + "example-asset.fgb", "101", updated),
            StoredObject(PREFIX + "README.md", "102", updated, "unpublished README", "new"),
            StoredObject(ASSET_ROOT + "/releases/2026-01-01/example-asset.fgb", "99"),
        ]

    def test_fresh_mixed_proposal_matching_historical_data_is_kept(self):
        for apply_changes in (False, True):
            with self.subTest(apply_changes=apply_changes):
                client = FakeClient(self.mixed_proposal(days_old=0))
                summary = self.audit(client, apply_changes=apply_changes)
                self.assertEqual(summary["deletions"], [])
                self.assertEqual(summary["warnings"], [])
                self.assertEqual(summary["kept"][0]["reason"], "not-eligible")
                self.assertEqual(client.uploads, [])
                self.assertEqual(client.deletes, [])

    def test_old_unwarned_mixed_proposal_warns_instead_of_deleting(self):
        for apply_changes in (False, True):
            with self.subTest(apply_changes=apply_changes):
                client = FakeClient(self.mixed_proposal(days_old=95))
                summary = self.audit(client, apply_changes=apply_changes)
                self.assertEqual(summary["deletions"], [])
                self.assertEqual(summary["warnings"][0]["reason"], "stale-warning")
                self.assertEqual(client.deletes, [])
                self.assertEqual(len(client.uploads), int(apply_changes))

    def test_unchanged_data_with_new_metadata_and_partial_publish_stays_unverified(self):
        for days_old in (0, 95):
            for metadata_name in ("README.md", "schema.json", "manifest.json", "example-asset.metadata.ndjson.gz"):
                for published_count in (0, 1, 2):
                    with self.subTest(age=days_old, metadata=metadata_name, published=published_count):
                        updated = NOW - dt.timedelta(days=days_old)
                        pending = [
                            StoredObject(PREFIX + "example-asset.fgb", "101", updated),
                            StoredObject(PREFIX + "example-asset.pmtiles", "102", updated),
                            StoredObject(PREFIX + metadata_name, "103", updated, "new metadata", "new"),
                        ]
                        published = [
                            replace(obj, name=ASSET_ROOT + "/releases/2026-01-01/" + obj.name.rsplit("/", 1)[1])
                            for obj in pending[:published_count]
                        ]
                        client = FakeClient(pending + published)
                        summary = self.audit(client)
                        self.assertEqual(summary["deletions"], [])
                        self.assertEqual(len(summary["warnings"]), int(days_old >= 60))
                        self.assertEqual(client.deletes, [])
                        self.assertEqual(
                            client.listed_prefixes,
                            [scratch_cleanup.PENDING_PREFIX, scratch_cleanup.WARNING_PREFIX],
                        )
                        self.assertTrue(all(obj.name in client.objects for obj in pending))

    def test_age_boundaries_are_independent_of_proposal_id(self):
        for proposal_id in ("pr-1", "opaque-proposal"):
            for days_old, expected in ((59.99, "keep"), (60, "warn"), (89.99, "warn"), (90, "warn")):
                with self.subTest(proposal_id=proposal_id, age=days_old):
                    obj = StoredObject(
                        PREFIX.replace("pr-1", proposal_id) + "example-asset.fgb",
                        updated=NOW - dt.timedelta(days=days_old),
                    )
                    client = FakeClient([obj])
                    summary = self.audit(client, apply_changes=False)
                    decisions = summary["kept"] + summary["warnings"] + summary["deletions"]
                    self.assertEqual(decisions[0]["action"], expected)
                    self.assertEqual(client.uploads, [])
                    self.assertEqual(client.deletes, [])

    def test_existing_valid_warning_deletes_only_at_delete_age(self):
        for days_old in (60, 89.99, 90, 95):
            with self.subTest(age=days_old):
                obj = StoredObject(PREFIX + "example-asset.fgb", "101", NOW - dt.timedelta(days=days_old))
                client = FakeClient([obj, warning_for(obj)])
                summary = self.audit(client)
                expected = int(days_old >= 90)
                self.assertEqual(summary["deleted_object_count"], expected)
                self.assertEqual(summary["deletion_count"], expected)
                self.assertEqual(client.uploads, [])
                self.assertEqual(client.downloads, [(MARKER, "55", 55)])
                self.assertEqual(client.deletes, [(obj.name, 101), (MARKER, 55)] if expected else [])

    def test_late_first_warning_can_authorize_a_later_audit(self):
        obj = StoredObject(PREFIX + "example-asset.fgb", "101", NOW - dt.timedelta(days=95))
        client = FakeClient([obj])
        first = self.audit(client)
        self.assertEqual(first["deletion_count"], 0)
        self.assertEqual(client.uploads, [(MARKER, 0, "application/json")])
        marker = json.loads(client.objects[MARKER].content)
        self.assertEqual(marker["warned_at"], NOW.isoformat())
        self.assertEqual(marker["newest_generation"], "101")
        second = self.audit(client)
        self.assertEqual(second["deletions"][0]["reason"], "stale-after-warning")
        self.assertEqual(client.deletes, [(obj.name, 101), (MARKER, 100)])

    def test_malformed_and_incomplete_marker_is_not_deletion_evidence(self):
        obj = StoredObject(PREFIX + "example-asset.fgb", "101", NOW - dt.timedelta(days=95))
        valid = json.loads(warning_for(obj).content)
        contents = ["{", *[json.dumps(value) for value in ([], None, False, 17, "warning")]]
        for field in ("newest_object_name", "newest_generation", "newest_updated"):
            contents.append(json.dumps({key: value for key, value in valid.items() if key != field}))
            for invalid in ("", None, False, True, 101, [valid[field]]):
                contents.append(json.dumps({**valid, field: invalid}))
        for content in contents:
            for apply_changes in (False, True):
                with self.subTest(content=content, apply_changes=apply_changes):
                    client = FakeClient([obj, StoredObject(MARKER, "55", content=content)])
                    summary = self.audit(client, apply_changes=apply_changes)
                    self.assertEqual(summary["deletions"], [])
                    self.assertEqual(summary["warnings"][0]["reason"], "stale-warning")
                    self.assertEqual(client.deletes, [])
                    self.assertEqual(client.uploads, [(MARKER, 55, "application/json")] if apply_changes else [])
                    self.assertIn(obj.name, client.objects)
                    if apply_changes:
                        self.assertEqual(json.loads(client.objects[MARKER].content)["newest_generation"], "101")

    def test_changed_warning_state_requires_a_new_warning(self):
        obj = StoredObject(PREFIX + "example-asset.fgb", "101", NOW - dt.timedelta(days=95))
        valid = json.loads(warning_for(obj).content)
        for field in ("newest_object_name", "newest_generation", "newest_updated"):
            with self.subTest(field=field):
                marker = StoredObject(MARKER, "55", content=json.dumps({**valid, field: "old-state"}))
                client = FakeClient([obj, marker])
                summary = self.audit(client)
                self.assertEqual(summary["deletions"], [])
                self.assertEqual(client.uploads, [(MARKER, 55, "application/json")])
                self.assertEqual(client.deletes, [])

    def test_invalid_observed_object_generations_refuse_all_mutations(self):
        obj = StoredObject(PREFIX + "example-asset.fgb", "101", NOW - dt.timedelta(days=95))
        for target in ("pending", "marker"):
            for invalid in (None, "", "0", 0, "-1", -1, True, False, 1.5, "1.5", " ", "１"):
                with self.subTest(target=target, generation=invalid):
                    objects = [replace(obj, generation=invalid), warning_for(obj)] if target == "pending" else [
                        obj, replace(warning_for(obj), generation=invalid)
                    ]
                    client = FakeClient(objects)
                    with self.assertRaisesRegex(ValueError, "Invalid object generation"):
                        self.audit(client)
                    self.assertEqual(client.uploads, [])
                    self.assertEqual(client.deletes, [])

    def test_marker_replacement_between_list_and_read_aborts_without_mutations(self):
        obj = StoredObject(PREFIX + "example-asset.fgb", "101", NOW - dt.timedelta(days=95))
        client = FakeClient([obj, warning_for(obj)])
        replacement = StoredObject(MARKER, "56", content="{}")
        client.before_download = lambda name: client.objects.update({name: replacement})
        with self.assertRaises(NotFound):
            self.audit(client)
        self.assertEqual(client.downloads, [(MARKER, "55", 55)])
        self.assertEqual(client.uploads, [])
        self.assertEqual(client.deletes, [])
        self.assertEqual(client.objects[MARKER], replacement)
        client.before_download = None
        retried = self.audit(client)
        self.assertEqual(retried["deletions"], [])
        self.assertEqual(client.uploads, [(MARKER, 56, "application/json")])

    def test_warning_write_collision_aborts_before_any_delete(self):
        obj = StoredObject(PREFIX + "example-asset.fgb", "101", NOW - dt.timedelta(days=95))
        for existing in (False, True):
            with self.subTest(existing_marker=existing):
                objects = [obj]
                if existing:
                    objects.append(StoredObject(MARKER, "55", content="{}"))
                other = StoredObject(PREFIX.replace("pr-1", "pr-2") + "example-asset.fgb", "201", obj.updated)
                other_marker = replace(warning_for(other), name=MARKER.replace("pr-1", "pr-2"))
                client = FakeClient(objects + [other, other_marker])
                replacement = StoredObject(MARKER, "56", content="{}")
                client.before_upload = lambda name: client.objects.update({name: replacement})
                with self.assertRaises(PreconditionFailed):
                    self.audit(client)
                self.assertEqual(client.uploads, [(MARKER, 55 if existing else 0, "application/json")])
                self.assertEqual(client.deletes, [])
                self.assertEqual(client.objects[MARKER], replacement)

    def test_changed_pending_generation_survives_and_only_successful_deletes_count(self):
        obj = StoredObject(PREFIX + "example-asset.fgb", "101", NOW - dt.timedelta(days=95))
        older = StoredObject(PREFIX + "README.md", "99", NOW - dt.timedelta(days=96))
        client = FakeClient([obj, older, warning_for(obj)])
        replacement = replace(obj, generation="102", updated=NOW)

        def replace_pending(name):
            if name == obj.name:
                client.objects[name] = replacement

        client.before_delete = replace_pending
        summary = self.audit(client)
        self.assertEqual(summary["deletion_count"], 1)
        self.assertEqual(summary["deleted_object_count"], 1)
        self.assertEqual(client.deletes, [(older.name, 99), (obj.name, 101), (MARKER, 55)])
        self.assertEqual(client.objects, {obj.name: replacement})
        client.before_delete = None
        retried = self.audit(client)
        self.assertEqual(retried["deletions"], [])
        self.assertEqual(retried["warnings"], [])
        self.assertEqual(retried["kept"][0]["newest_generation"], "102")

    def test_new_object_after_listing_is_never_included_in_deletion(self):
        obj = StoredObject(PREFIX + "example-asset.fgb", "101", NOW - dt.timedelta(days=95))
        client = FakeClient([obj, warning_for(obj)])
        added = StoredObject(PREFIX + "new-metadata.json", "102")
        client.before_delete = lambda name: client.objects.update({added.name: added})
        summary = self.audit(client)
        self.assertEqual(summary["deleted_object_count"], 1)
        self.assertEqual(client.deletes, [(obj.name, 101), (MARKER, 55)])
        self.assertEqual(client.objects, {added.name: added})

    def test_marker_replacement_before_delete_is_not_deleted(self):
        obj = StoredObject(PREFIX + "example-asset.fgb", "101", NOW - dt.timedelta(days=95))
        client = FakeClient([obj, warning_for(obj)])
        replacement = warning_for(obj, generation="56")

        def replace_marker(name):
            if name == MARKER:
                client.objects[MARKER] = replacement

        client.before_delete = replace_marker
        with self.assertRaises(PreconditionFailed):
            self.audit(client)
        self.assertEqual(client.deletes, [(obj.name, 101), (MARKER, 55)])
        self.assertEqual(client.objects, {MARKER: replacement})

    def test_eligible_multi_object_cleanup_uses_each_generation_and_tolerates_missing_marker(self):
        obj = StoredObject(PREFIX + "example-asset.fgb", "101", NOW - dt.timedelta(days=95))
        older = StoredObject(PREFIX + "README.md", "99", NOW - dt.timedelta(days=96))
        for marker_disappears in (False, True):
            with self.subTest(marker_disappears=marker_disappears):
                client = FakeClient([obj, older, warning_for(obj)])
                if marker_disappears:
                    def delete_marker(name):
                        if name == MARKER:
                            client.objects.pop(MARKER)
                    client.before_delete = delete_marker
                summary = self.audit(client)
                self.assertEqual(summary["deletions"][0]["reason"], "stale-after-warning")
                self.assertEqual(summary["deleted_object_count"], 2)
                self.assertEqual(client.deletes, [(older.name, 99), (obj.name, 101), (MARKER, 55)])
                self.assertEqual(client.objects, {})

    def test_dry_run_summary_separates_candidates_from_actual_deletions(self):
        obj = StoredObject(PREFIX + "example-asset.fgb", "101", NOW - dt.timedelta(days=95))
        client = FakeClient([obj, warning_for(obj)])
        summary_path = Mock()
        summary_path.open = mock_open()
        summary = self.audit(client, apply_changes=False, summary_path=summary_path)
        rendered = summary_path.open().write.call_args.args[0]
        self.assertTrue(summary["dry_run"])
        self.assertEqual(summary["deletion_count"], 1)
        self.assertEqual(summary["deleted_object_count"], 0)
        self.assertIn("Deletion candidates: `1`", rendered)
        self.assertIn("Objects deleted: `0`", rendered)
        self.assertIn("Dry run: `True`", rendered)
        self.assertNotIn("Prefixes deleted:", rendered)
        self.assertEqual(client.uploads, [])
        self.assertEqual(client.deletes, [])

    def test_cli_defaults_to_dry_run_and_rejects_invalid_age_policy(self):
        client = FakeClient(self.mixed_proposal(days_old=95))
        with patch.object(scratch_cleanup, "get_client", return_value=client) as get_client:
            result = CliRunner().invoke(scratch_cleanup.app, ["--bucket", "bucket"])
            self.assertEqual(result.exit_code, 0, result.output)
            self.assertTrue(json.loads(result.output)["dry_run"])
            self.assertEqual(client.uploads, [])
            self.assertEqual(client.deletes, [])
            get_client.reset_mock()
            for options in (
                ["--warn-age-days", "90", "--delete-age-days", "90"],
                ["--warn-age-days", "91", "--delete-age-days", "90"],
                ["--warn-age-days", "0"],
            ):
                with self.subTest(options=options):
                    result = CliRunner().invoke(scratch_cleanup.app, options)
                    self.assertNotEqual(result.exit_code, 0)
                    get_client.assert_not_called()


if __name__ == "__main__":
    unittest.main()
