from __future__ import annotations

import unittest

from scripts import feature_preview_reset


class FakeBlob:
    def __init__(self, name: str, generation: int | None) -> None:
        self.name = name
        self.generation = generation
        self.deletes = []

    def delete(self, **kwargs):
        self.deletes.append(kwargs)


class FakeStorageClient:
    def __init__(self, blobs):
        self.blobs = list(blobs)
        self.calls = 0

    def list_blobs(self, bucket, max_results=None):
        self.calls += 1
        if self.calls == 1:
            return list(self.blobs)
        return []


class FakeCollection:
    def __init__(self) -> None:
        self.limit_value = None

    def limit(self, value):
        self.limit_value = value
        return self

    def stream(self):
        return iter(())


class FakeFirestoreClient:
    def __init__(self) -> None:
        self.collection_ref = FakeCollection()
        self.deleted = []

    def collection(self, name):
        self.collection_name = name
        return self.collection_ref

    def recursive_delete(self, reference):
        self.deleted.append(reference)


class FeaturePreviewResetTests(unittest.TestCase):
    def test_environment_must_match_fixed_preview_boundary(self):
        env = dict(feature_preview_reset.EXPECTED_ENV)
        env["SHARED_DATASETS_BUCKET"] = "skytruth-shared-datasets-1"

        with self.assertRaisesRegex(feature_preview_reset.PreviewResetError, "must be exactly"):
            feature_preview_reset.validate_environment(env)

    def test_reset_deletes_exact_generations_and_collection_root(self):
        blobs = [FakeBlob("_catalog/web/index.html", 11), FakeBlob("asset/release.fgb", 12)]
        storage = FakeStorageClient(blobs)
        firestore = FakeFirestoreClient()

        result = feature_preview_reset.reset_preview_data(
            storage_client=storage,
            firestore_client=firestore,
        )

        self.assertEqual([blob.deletes for blob in blobs], [[{"if_generation_match": 11}], [{"if_generation_match": 12}]])
        self.assertEqual(firestore.collection_name, "feature_preview_index")
        self.assertEqual(firestore.deleted, [firestore.collection_ref])
        self.assertEqual(firestore.collection_ref.limit_value, 1)
        self.assertEqual(result["object_count"], 2)

    def test_dry_run_lists_without_mutating(self):
        blob = FakeBlob("asset/release.fgb", 12)
        storage = FakeStorageClient([blob])
        firestore = FakeFirestoreClient()

        result = feature_preview_reset.reset_preview_data(
            storage_client=storage,
            firestore_client=firestore,
            dry_run=True,
        )

        self.assertEqual(blob.deletes, [])
        self.assertEqual(firestore.deleted, [])
        self.assertTrue(result["dry_run"])

    def test_missing_generation_fails_before_mutation(self):
        blob = FakeBlob("asset/release.fgb", None)

        with self.assertRaisesRegex(feature_preview_reset.PreviewResetError, "missing a generation"):
            feature_preview_reset.reset_preview_data(
                storage_client=FakeStorageClient([blob]),
                firestore_client=FakeFirestoreClient(),
            )

        self.assertEqual(blob.deletes, [])


if __name__ == "__main__":
    unittest.main()
