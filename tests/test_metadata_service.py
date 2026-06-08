from __future__ import annotations

import contextlib
import io
import json
import unittest
from http import HTTPStatus

from scripts import release_feature_model
from services.metadata_service.run import ApiError, CatalogReleaseResolver, IndexNotReady, ResolvedRelease, handle_request


class FakeResolver:
    def __init__(self) -> None:
        self.calls = []

    def resolve(self, asset_slug: str, release: str) -> ResolvedRelease:
        self.calls.append((asset_slug, release))
        if release == "latest":
            return ResolvedRelease(
                release,
                "2026-05-01",
                42,
                schema_generation=43,
                manifest_generation=44,
                index_load_id="load-1",
                schema_fields=("feature_id", "name", "source_id", "nullable_field"),
            )
        if release == "2026-05-01":
            return ResolvedRelease(
                release,
                release,
                42,
                schema_generation=43,
                manifest_generation=44,
                index_load_id="load-1",
                schema_fields=("feature_id", "name", "source_id", "nullable_field"),
            )
        raise AssertionError("unexpected release")


class NotReadyResolver(FakeResolver):
    def resolve(self, asset_slug: str, release: str) -> ResolvedRelease:
        raise IndexNotReady("not loaded")


class InactiveResolver(FakeResolver):
    def resolve(self, asset_slug: str, release: str) -> ResolvedRelease:
        self.calls.append((asset_slug, release))
        return ResolvedRelease(
            release,
            "2026-05-01",
            42,
            schema_generation=43,
            manifest_generation=44,
            index_load_id="inactive",
            schema_fields=("feature_id", "name", "source_id", "nullable_field"),
        )


class FakeIndex:
    def __init__(self) -> None:
        self.calls = []
        self.feature_id_calls = []
        self.documents = {
            "1": {
                "feature_id": "1",
                "geometry_hash": "sha256:aaa",
                "properties_hash": "sha256:abc",
                "properties": {"feature_id": "1", "name": "A", "source_id": "1"},
                "provenance": {"source": "fixture"},
            },
            "2": {
                "feature_id": "2",
                "geometry_hash": "sha256:ddd",
                "properties_hash": "sha256:def",
                "properties": {"feature_id": "2", "name": "B", "source_id": "2"},
                "provenance": {"source": "fixture"},
            },
        }

    def lookup(self, *, asset_slug: str, release: str, index_load_id: str, feature_ids: list[str]):
        self.calls.append((asset_slug, release, index_load_id, feature_ids))
        return {feature_id: self.documents[feature_id] for feature_id in feature_ids if feature_id in self.documents}

    def lookup_by_feature_ids(self, *, asset_slug: str, release: str, index_load_id: str, feature_ids: list[str]):
        self.feature_id_calls.append((asset_slug, release, index_load_id, feature_ids))
        by_feature_id = {document["properties"]["feature_id"]: document for document in self.documents.values()}
        return {feature_id: by_feature_id[feature_id] for feature_id in feature_ids if feature_id in by_feature_id}


class FailingIndex(FakeIndex):
    def lookup(self, *, asset_slug: str, release: str, index_load_id: str, feature_ids: list[str]):
        raise ApiError(HTTPStatus.SERVICE_UNAVAILABLE, "index_unavailable", "feature metadata index lookup failed")

    def lookup_by_feature_ids(self, *, asset_slug: str, release: str, index_load_id: str, feature_ids: list[str]):
        raise ApiError(HTTPStatus.SERVICE_UNAVAILABLE, "index_unavailable", "feature metadata feature_id lookup failed")


class FakeGcsBlob:
    def __init__(self, name: str, payload: dict, generation: int) -> None:
        self.name = name
        self._payload = payload
        self.generation = generation
        self.size = len(json.dumps(payload))

    def reload(self) -> None:
        return None

    def download_as_text(self) -> str:
        return json.dumps(self._payload)


class FakeGcsBucket:
    def __init__(self, blobs: dict[str, FakeGcsBlob]) -> None:
        self._blobs = blobs

    def blob(self, name: str) -> FakeGcsBlob:
        return self._blobs[name]

    def list_blobs(self, *, prefix: str):
        return [blob for name, blob in self._blobs.items() if name.startswith(prefix)]


class FakeGcsClient:
    def __init__(self, bucket: FakeGcsBucket) -> None:
        self._bucket = bucket

    def bucket(self, _name: str) -> FakeGcsBucket:
        return self._bucket


def ready_release_bucket(
    *,
    manifest_metadata_generation: int = 12,
    release_index_policy_path: str | None = None,
) -> FakeGcsBucket:
    asset_root = "100-geographic-reference/110-boundaries/example-asset"
    metadata_uri = f"gs://test-bucket/{asset_root}/releases/2026-05-01/example-asset.metadata.ndjson.gz"
    schema_uri = f"gs://test-bucket/{asset_root}/releases/2026-05-01/example-asset.schema.json"
    manifest_uri = f"gs://test-bucket/{asset_root}/releases/2026-05-01/example-asset.manifest.json"
    files = [
        {"format": "metadata", "path": metadata_uri, "generation": 12, "size": 10},
        {"format": "schema", "path": schema_uri, "generation": 13, "size": 10},
        {"format": "manifest", "path": manifest_uri, "generation": 14, "size": 10},
    ]
    latest_release = {
        "date": "2026-05-01",
        "files": files,
        "index_load_status": "Firestore metadata serving is inactive",
        "index_status_policy": {
            "mode": "inactive_firestore_serving",
            "path": release_index_policy_path,
        },
    }
    release_index = {
        "asset_slug": "example-asset",
        "latest_release": latest_release,
        "releases": [latest_release],
    }
    schema = {
        "schema_version": release_feature_model.RELEASE_SCHEMA_SCHEMA_VERSION,
        "asset_slug": "example-asset",
        "release": "2026-05-01",
        "fields": [{"name": "name", "type": "String", "nullable": True, "projectable": True}],
    }
    manifest = {
        "schema_version": release_feature_model.RELEASE_MANIFEST_SCHEMA_VERSION,
        "asset_slug": "example-asset",
        "release": "2026-05-01",
        "release_feature_model_schema_version": release_feature_model.RELEASE_FEATURE_MODEL_SCHEMA_VERSION,
        "artifacts": [
            {"role": "metadata", "path": metadata_uri, "generation": manifest_metadata_generation},
            {"role": "schema", "path": schema_uri, "generation": 13},
            {"role": "manifest", "path": manifest_uri},
        ],
        "schema": schema,
        "identity": release_feature_model.build_identity_metadata(strategy="source_field", source_fields=["id"]),
        "index_load_status": "Firestore metadata serving is inactive",
        "index_status_policy": {"mode": "inactive_firestore_serving", "path": None},
    }
    index_load = {
        "status": "success",
        "dry_run": False,
        "asset_slug": "example-asset",
        "release": "2026-05-01",
        "completed_at": "2026-05-01T00:00:00+00:00",
        "sidecar_uri": metadata_uri,
        "sidecar_generation": 12,
        "schema_uri": schema_uri,
        "schema_generation": 13,
        "manifest_uri": manifest_uri,
        "manifest_generation": 14,
    }
    index_load["load_id"] = "load-1"
    blobs = {
        "_catalog/releases/example-asset.json": FakeGcsBlob("_catalog/releases/example-asset.json", release_index, 42),
        f"{asset_root}/releases/2026-05-01/example-asset.schema.json": FakeGcsBlob(
            f"{asset_root}/releases/2026-05-01/example-asset.schema.json",
            schema,
            13,
        ),
        f"{asset_root}/releases/2026-05-01/example-asset.manifest.json": FakeGcsBlob(
            f"{asset_root}/releases/2026-05-01/example-asset.manifest.json",
            manifest,
            14,
        ),
        f"{asset_root}/index-loads/2026-05-01/load-1.json": FakeGcsBlob(
            f"{asset_root}/index-loads/2026-05-01/load-1.json",
            index_load,
            15,
        ),
    }
    return FakeGcsBucket(blobs)


def post_lookup(body, *, headers=None, max_response_bytes=10 * 1024 * 1024, path="/v1/assets/example-asset/releases/latest:lookup"):
    resolver = FakeResolver()
    index = FakeIndex()
    request_headers = (
        {"X-Goog-Authenticated-User-Email": "accounts.google.com:jona@skytruth.org"}
        if headers is None
        else headers
    )
    response = handle_request(
        "POST",
        path,
        request_headers,
        json.dumps(body).encode("utf-8"),
        release_resolver=resolver,
        feature_index=index,
        max_response_bytes=max_response_bytes,
    )
    return response, resolver, index


class MetadataServiceTests(unittest.TestCase):
    def test_healthz_does_not_require_iap(self):
        response = handle_request(
            "GET",
            "/healthz",
            {},
            release_resolver=FakeResolver(),
            feature_index=FakeIndex(),
        )

        self.assertEqual(response.status, 200)

    def test_lookup_requires_iap(self):
        response, _resolver, _index = post_lookup({"ids": ["1"]}, headers={})

        self.assertEqual(response.status, 401)

    def test_lookup_resolves_latest_and_preserves_duplicate_order(self):
        response, resolver, index = post_lookup({"ids": ["1", "1", "missing"]})

        self.assertEqual(response.status, 200)
        payload = json.loads(response.body)
        self.assertEqual(payload["requested_release"], "latest")
        self.assertEqual(payload["resolved_release"], "2026-05-01")
        self.assertEqual(payload["release_index_generation"], 42)
        self.assertEqual(payload["schema_generation"], 43)
        self.assertEqual(payload["manifest_generation"], 44)
        self.assertEqual(payload["index_load_id"], "load-1")
        self.assertEqual(payload["deduplicated_lookup_count"], 2)
        self.assertEqual([item["feature_id"] for item in payload["items"]], ["1", "1", "missing"])
        self.assertTrue(payload["items"][0]["found"])
        self.assertEqual(payload["items"][0]["feature_id"], "1")
        self.assertEqual(payload["items"][0]["provenance"], {"source": "fixture"})
        self.assertEqual(payload["items"][2], {"feature_id": "missing", "found": False})
        self.assertEqual(resolver.calls, [("example-asset", "latest")])
        self.assertEqual(index.calls, [("example-asset", "2026-05-01", "load-1", ["1", "missing"])])

    def test_lookup_by_feature_id_resolves_latest_and_preserves_duplicate_order(self):
        response, resolver, index = post_lookup(
            {"feature_ids": ["1", "1", "missing"], "fields": ["name"], "include_provenance": False},
            path="/v1/assets/example-asset/releases/latest:lookup",
        )

        self.assertEqual(response.status, 200)
        payload = json.loads(response.body)
        self.assertEqual(payload["requested_release"], "latest")
        self.assertEqual(payload["resolved_release"], "2026-05-01")
        self.assertEqual(payload["deduplicated_lookup_count"], 2)
        self.assertEqual(payload["items"][0]["feature_id"], "1")
        self.assertEqual(payload["items"][0]["feature_id"], "1")
        self.assertEqual(payload["items"][0]["properties"], {"name": "A"})
        self.assertNotIn("provenance", payload["items"][0])
        self.assertEqual(payload["items"][1]["feature_id"], "1")
        self.assertEqual(payload["items"][2], {"feature_id": "missing", "found": False})
        self.assertEqual(resolver.calls, [("example-asset", "latest")])
        self.assertEqual(index.calls[-1], ("example-asset", "2026-05-01", "load-1", ["1", "missing"]))

    def test_lookup_by_feature_id_rejects_invalid_public_ids(self):
        response, _resolver, index = post_lookup(
            {"feature_ids": ["bad-id"]},
            path="/v1/assets/example-asset/releases/latest:lookup",
        )

        self.assertEqual(response.status, 400)
        self.assertEqual(json.loads(response.body)["error"]["code"], "invalid_argument")
        self.assertEqual(index.feature_id_calls, [])

    def test_lookup_without_release_segment_is_rejected(self):
        response, resolver, index = post_lookup(
            {"ids": ["1"]},
            path="/v1/assets/example-asset:lookup",
        )

        self.assertEqual(response.status, 404)
        self.assertEqual(resolver.calls, [])
        self.assertEqual(index.calls, [])

    def test_field_projection_can_return_no_properties(self):
        response, _resolver, _index = post_lookup(
            {"ids": ["1"], "fields": [], "include_provenance": False}
        )

        self.assertEqual(response.status, 200)
        item = json.loads(response.body)["items"][0]
        self.assertEqual(item["properties"], {})
        self.assertNotIn("provenance", item)

    def test_valid_projected_field_missing_from_document_returns_null(self):
        response, _resolver, _index = post_lookup({"ids": ["1"], "fields": ["nullable_field"]})

        self.assertEqual(response.status, 200)
        item = json.loads(response.body)["items"][0]
        self.assertEqual(item["properties"], {"nullable_field": None})

    def test_invalid_requested_field_is_400(self):
        response, _resolver, _index = post_lookup({"ids": ["1"], "fields": ["missing_field"]})

        self.assertEqual(response.status, 400)
        payload = json.loads(response.body)
        self.assertEqual(payload["error"]["code"], "invalid_field")

    def test_invalid_requested_field_is_400_even_when_ids_are_missing(self):
        response, _resolver, index = post_lookup({"ids": ["missing"], "fields": ["missing_field"]})

        self.assertEqual(response.status, 400)
        self.assertEqual(index.calls, [])
        self.assertEqual(json.loads(response.body)["error"]["code"], "invalid_field")

    def test_index_not_ready_is_409_before_lookup(self):
        response = handle_request(
            "POST",
            "/v1/assets/example-asset/releases/latest:lookup",
            {"X-Goog-Authenticated-User-Email": "accounts.google.com:jona@skytruth.org"},
            json.dumps({"ids": ["1"]}).encode("utf-8"),
            release_resolver=NotReadyResolver(),
            feature_index=FakeIndex(),
        )

        self.assertEqual(response.status, 409)
        self.assertEqual(json.loads(response.body)["error"]["code"], "index_not_ready")

    def test_inactive_resolver_returns_409_before_lookup(self):
        resolver = InactiveResolver()
        index = FakeIndex()
        response = handle_request(
            "POST",
            "/v1/assets/example-asset/releases/latest:lookup",
            {"X-Goog-Authenticated-User-Email": "accounts.google.com:jona@skytruth.org"},
            json.dumps({"ids": ["1"]}).encode("utf-8"),
            release_resolver=resolver,
            feature_index=index,
        )

        self.assertEqual(response.status, 409)
        self.assertEqual(json.loads(response.body)["error"]["code"], "index_not_ready")
        self.assertEqual(resolver.calls, [("example-asset", "latest")])
        self.assertEqual(index.calls, [])

    def test_catalog_resolver_rejects_matching_manifest_and_inactive_firestore_policy(self):
        resolver = CatalogReleaseResolver(
            bucket_name="test-bucket",
            client=FakeGcsClient(ready_release_bucket()),
            ttl_seconds=0,
        )

        with self.assertRaisesRegex(IndexNotReady, "Firestore metadata serving is inactive"):
            resolver.resolve("example-asset", "latest")

    def test_catalog_resolver_rejects_non_null_inactive_policy_path(self):
        resolver = CatalogReleaseResolver(
            bucket_name="test-bucket",
            client=FakeGcsClient(
                ready_release_bucket(
                    release_index_policy_path=(
                        "gs://other-bucket/100-geographic-reference/110-boundaries/"
                        "example-asset/index-loads/2026-05-01/"
                    )
                )
            ),
            ttl_seconds=0,
        )

        with self.assertRaisesRegex(IndexNotReady, "index_status_policy is invalid"):
            resolver.resolve("example-asset", "latest")

    def test_catalog_resolver_rejects_manifest_generation_mismatch(self):
        resolver = CatalogReleaseResolver(
            bucket_name="test-bucket",
            client=FakeGcsClient(ready_release_bucket(manifest_metadata_generation=999)),
            ttl_seconds=0,
        )

        with self.assertRaisesRegex(IndexNotReady, "generation changed"):
            resolver.resolve("example-asset", "latest")

    def test_catalog_resolver_rejects_missing_inactive_status(self):
        bucket = ready_release_bucket()
        index = bucket.blob("_catalog/releases/example-asset.json")._payload
        index["latest_release"].pop("index_load_status")
        resolver = CatalogReleaseResolver(
            bucket_name="test-bucket",
            client=FakeGcsClient(bucket),
            ttl_seconds=0,
        )

        with self.assertRaisesRegex(IndexNotReady, "serving active or unknown"):
            resolver.resolve("example-asset", "latest")

    def test_firestore_lookup_failure_is_503(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            response = handle_request(
                "POST",
                "/v1/assets/example-asset/releases/latest:lookup",
                {"X-Goog-Authenticated-User-Email": "accounts.google.com:jona@skytruth.org"},
                json.dumps({"ids": ["1"]}).encode("utf-8"),
                release_resolver=FakeResolver(),
                feature_index=FailingIndex(),
            )

        self.assertEqual(response.status, 503)
        self.assertEqual(json.loads(response.body)["error"]["code"], "index_unavailable")
        log_payload = json.loads(output.getvalue())
        self.assertEqual(log_payload["severity"], "ERROR")
        self.assertEqual(log_payload["status"], 503)
        self.assertEqual(log_payload["code"], "index_unavailable")

    def test_client_error_does_not_emit_error_log(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            response, _resolver, _index = post_lookup({"ids": ["1"], "fields": ["missing_field"]})

        self.assertEqual(response.status, 400)
        self.assertEqual(output.getvalue(), "")

    def test_limits_are_enforced(self):
        too_many_ids = [str(index) for index in range(501)]
        response, _resolver, _index = post_lookup({"ids": too_many_ids})

        self.assertEqual(response.status, 400)
        self.assertEqual(json.loads(response.body)["error"]["code"], "limit_exceeded")

    def test_response_size_limit_is_413(self):
        response, _resolver, _index = post_lookup({"ids": ["1"]}, max_response_bytes=10)

        self.assertEqual(response.status, 413)
        self.assertEqual(json.loads(response.body)["error"]["code"], "response_too_large")

    def test_etag_supports_not_modified(self):
        response, _resolver, _index = post_lookup({"ids": ["1"]})
        etag = response.headers["ETag"]
        second_response = handle_request(
            "POST",
            "/v1/assets/example-asset/releases/latest:lookup",
            {
                "X-Goog-Authenticated-User-Email": "accounts.google.com:jona@skytruth.org",
                "If-None-Match": etag,
            },
            json.dumps({"ids": ["1"]}).encode("utf-8"),
            release_resolver=FakeResolver(),
            feature_index=FakeIndex(),
        )

        self.assertEqual(second_response.status, 304)
        self.assertEqual(second_response.body, b"")


if __name__ == "__main__":
    unittest.main()
