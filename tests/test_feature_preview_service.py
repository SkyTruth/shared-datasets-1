from __future__ import annotations

import gzip
import json
import unittest

from services.feature_preview_service import run


PREVIEW_BUCKET = "skytruth-shared-datasets-1-preview"
VALID_GEOMETRY_HASH = "sha256:" + "a" * 64
VALID_PROPERTIES_HASH = "sha256:" + "b" * 64
SIDECAR_URI = (
    f"gs://{PREVIEW_BUCKET}/100-geographic-reference/130-protected-areas/"
    "wdpa-marine/releases/2026-06-01/wdpa-marine.metadata.ndjson.gz"
)
SIDECAR_OBJECT = SIDECAR_URI.removeprefix(f"gs://{PREVIEW_BUCKET}/")


class NotFound(Exception):
    pass


class FakeGcsBlob:
    def __init__(
        self,
        name: str,
        payload,
        generation: int | None = 1,
        *,
        missing: bool = False,
        fail_reload: bool = False,
        fail_download: bool = False,
    ) -> None:
        self.name = name
        self.payload = payload
        self.generation = generation
        self.missing = missing
        self.fail_reload = fail_reload
        self.fail_download = fail_download
        self.reload_count = 0
        self.download_count = 0

    def reload(self) -> None:
        self.reload_count += 1
        if self.missing:
            raise NotFound(self.name)
        if self.fail_reload:
            raise RuntimeError("metadata unavailable")

    def download_as_text(self) -> str:
        if self.missing:
            raise NotFound(self.name)
        if isinstance(self.payload, bytes):
            return self.payload.decode("utf-8")
        return json.dumps(self.payload)

    def download_as_bytes(self, **kwargs) -> bytes:
        self.download_count += 1
        if self.missing:
            raise NotFound(self.name)
        if self.fail_download:
            raise RuntimeError("download failed")
        expected_generation = kwargs.get("if_generation_match")
        if expected_generation is not None and self.generation is not None and int(expected_generation) != int(self.generation):
            raise RuntimeError("generation mismatch")
        if isinstance(self.payload, bytes):
            return self.payload
        return json.dumps(self.payload).encode("utf-8")


class FakeGcsBucket:
    def __init__(self, blobs: dict[str, FakeGcsBlob]) -> None:
        self.blobs = blobs

    def blob(self, name: str) -> FakeGcsBlob:
        return self.blobs.get(name, FakeGcsBlob(name, b"", missing=True))


class FakeGcsClient:
    def __init__(self, bucket: FakeGcsBucket) -> None:
        self.bucket_obj = bucket

    def bucket(self, _name: str) -> FakeGcsBucket:
        return self.bucket_obj


class FakeResolver:
    def resolve(self, asset_slug: str, release: str) -> run.ResolvedRelease:
        if asset_slug != "wdpa-marine":
            raise run.ApiError(404, "not_found", "missing asset")
        resolved = "2026-06-01" if release == "latest" else release
        return run.ResolvedRelease(release, resolved, 7, SIDECAR_URI, 1001)


class FakeIndex:
    def lookup(
        self,
        asset_slug: str,
        release: str,
        feature_ids: list[str],
        *,
        sidecar_uri: str,
        sidecar_generation: int | None,
    ):
        self.last_lookup = (asset_slug, release, feature_ids, sidecar_uri, sidecar_generation)
        return {
            "1": {
                "feature_id": "1",
                "geometry_hash": VALID_GEOMETRY_HASH,
                "properties_hash": VALID_PROPERTIES_HASH,
                "properties": {"feature_id": "1", "name": "A", "status": "active"},
                "provenance": {"source": "test"},
            }
        }

    def lookup_by_feature_ids(
        self,
        asset_slug: str,
        release: str,
        feature_ids: list[str],
        *,
        sidecar_uri: str,
        sidecar_generation: int | None,
    ):
        self.last_ext_lookup = (asset_slug, release, feature_ids, sidecar_uri, sidecar_generation)
        return {
            "1": {
                "feature_id": "1",
                "geometry_hash": VALID_GEOMETRY_HASH,
                "properties_hash": VALID_PROPERTIES_HASH,
                "properties": {"feature_id": "1", "name": "A", "status": "active"},
                "provenance": {"source": "test"},
            }
        }


def release_index_payload(
    *,
    include_sidecar: bool = True,
    sidecar_generation: int = 1001,
    sidecar_format: str = "metadata",
    sidecar_role: str | None = None,
) -> dict:
    files = [
        {
            "format": "fgb",
            "path": (
                f"gs://{PREVIEW_BUCKET}/100-geographic-reference/130-protected-areas/"
                "wdpa-marine/releases/2026-06-01/wdpa-marine.fgb"
            ),
        }
    ]
    if include_sidecar:
        sidecar = {
            "format": sidecar_format,
            "path": SIDECAR_URI,
            "generation": sidecar_generation,
        }
        if sidecar_role is not None:
            sidecar["role"] = sidecar_role
        files.append(sidecar)
    release = {"date": "2026-06-01", "files": files}
    return {
        "asset_slug": "wdpa-marine",
        "latest_release": release,
        "releases": [release],
    }


def release_index_bucket(payload: dict) -> FakeGcsBucket:
    return FakeGcsBucket(
        {
            "_catalog/releases/wdpa-marine.json": FakeGcsBlob(
                "_catalog/releases/wdpa-marine.json",
                payload,
                generation=7,
            )
        }
    )


def sidecar_bytes(records: list[dict]) -> bytes:
    lines = [json.dumps(record, sort_keys=True, separators=(",", ":")) for record in records]
    return gzip.compress(("\n".join(lines) + "\n").encode("utf-8"))


def sidecar_record(feature_id: str = "1", *, name: str = "A") -> dict:
    return {
        "schema_version": run.release_feature_model.METADATA_SIDECAR_SCHEMA_VERSION,
        "asset_slug": "wdpa-marine",
        "release": "2026-06-01",
        "feature_id": feature_id,
        "geometry_hash": VALID_GEOMETRY_HASH,
        "properties_hash": VALID_PROPERTIES_HASH,
        "properties": {"feature_id": feature_id.removeprefix("src:id:"), "name": name, "status": "active"},
        "provenance": {"source": "test"},
    }


def lookup(body, headers=None, path="/v1/assets/wdpa-marine/releases/latest:lookup"):
    index = FakeIndex()
    response = run.handle_request(
        "POST",
        path,
        headers or {"X-Goog-Authenticated-User-Email": "accounts.google.com:jona@skytruth.org"},
        json.dumps(body).encode("utf-8"),
        release_resolver=FakeResolver(),
        feature_index=index,
    )
    return response, index


class FeaturePreviewServiceTests(unittest.TestCase):
    def test_catalog_resolver_extracts_sidecar_uri_and_generation(self):
        resolver = run.CatalogReleaseResolver(
            bucket_name=PREVIEW_BUCKET,
            client=FakeGcsClient(release_index_bucket(release_index_payload())),
            ttl_seconds=0,
        )

        resolved = resolver.resolve("wdpa-marine", "latest")

        self.assertEqual(resolved.resolved_release, "2026-06-01")
        self.assertEqual(resolved.release_index_generation, 7)
        self.assertEqual(resolved.sidecar_uri, SIDECAR_URI)
        self.assertEqual(resolved.sidecar_generation, 1001)

    def test_catalog_resolver_rejects_non_metadata_sidecar_format(self):
        for sidecar_format in ("fgb", "pmtiles"):
            with self.subTest(sidecar_format=sidecar_format):
                resolver = run.CatalogReleaseResolver(
                    bucket_name=PREVIEW_BUCKET,
                    client=FakeGcsClient(
                        release_index_bucket(release_index_payload(sidecar_format=sidecar_format, sidecar_role=None))
                    ),
                    ttl_seconds=0,
                )

                with self.assertRaisesRegex(run.ApiError, "release metadata sidecar is not indexed"):
                    resolver.resolve("wdpa-marine", "latest")

    def test_lookup_requires_iap_identity(self):
        response = run.handle_request(
            "POST",
            "/v1/assets/wdpa-marine/releases/latest:lookup",
            {},
            b'{"ids":["1"]}',
            release_resolver=FakeResolver(),
            feature_index=FakeIndex(),
        )

        self.assertEqual(response.status, 401)

    def test_lookup_rejects_forwarded_email_without_iap_identity(self):
        response = run.handle_request(
            "POST",
            "/v1/assets/wdpa-marine/releases/latest:lookup",
            {"X-Forwarded-Email": "jona@skytruth.org"},
            b'{"ids":["1"]}',
            release_resolver=FakeResolver(),
            feature_index=FakeIndex(),
        )

        self.assertEqual(response.status, 401)

    def test_lookup_resolves_latest_filters_fields_and_marks_missing_ids(self):
        response, index = lookup({"ids": ["1", "2"], "fields": ["name"], "include_provenance": True})

        self.assertEqual(response.status, 200)
        self.assertEqual(index.last_lookup, ("wdpa-marine", "2026-06-01", ["1", "2"], SIDECAR_URI, 1001))
        payload = json.loads(response.body)
        self.assertEqual(payload["resolved_release"], "2026-06-01")
        self.assertEqual(payload["items"][0]["properties"], {"name": "A"})
        self.assertEqual(payload["items"][0]["provenance"], {"source": "test"})
        self.assertEqual(payload["items"][0]["feature_id"], "1")
        self.assertEqual(payload["items"][1], {"feature_id": "2", "found": False})
        self.assertIn("ETag", response.headers)

    def test_lookup_by_feature_id_resolves_latest_filters_fields_and_marks_missing_ids(self):
        response, index = lookup(
            {"feature_ids": ["1", "2", "1"], "fields": ["name"], "include_provenance": False},
            path="/v1/assets/wdpa-marine/releases/latest:lookup",
        )

        self.assertEqual(response.status, 200)
        self.assertEqual(index.last_lookup, ("wdpa-marine", "2026-06-01", ["1", "2"], SIDECAR_URI, 1001))
        payload = json.loads(response.body)
        self.assertEqual(payload["resolved_release"], "2026-06-01")
        self.assertEqual(payload["items"][0]["feature_id"], "1")
        self.assertEqual(payload["items"][0]["feature_id"], "1")
        self.assertEqual(payload["items"][0]["properties"], {"name": "A"})
        self.assertNotIn("provenance", payload["items"][0])
        self.assertEqual(payload["items"][1], {"feature_id": "2", "found": False})
        self.assertEqual(payload["items"][2]["feature_id"], "1")
        self.assertIn("ETag", response.headers)

    def test_lookup_by_feature_id_rejects_invalid_public_ids(self):
        response, _index = lookup(
            {"feature_ids": ["bad-id"]},
            path="/v1/assets/wdpa-marine/releases/latest:lookup",
        )

        self.assertEqual(response.status, 400)
        self.assertEqual(json.loads(response.body)["error"]["code"], "invalid_request")

    def test_lookup_supports_etag_not_modified(self):
        response, _index = lookup({"ids": ["1"]})
        cached, _index = lookup({"ids": ["1"]}, headers={"X-Goog-Authenticated-User-Email": "accounts.google.com:jona@skytruth.org", "If-None-Match": response.headers["ETag"]})

        self.assertEqual(cached.status, 304)
        self.assertEqual(cached.body, b"")

    def test_sidecar_cache_returns_found_and_missing_features(self):
        sidecar_blob = FakeGcsBlob(SIDECAR_OBJECT, sidecar_bytes([sidecar_record()]), generation=1001)
        index = run.GcsSidecarFeatureIndex(
            bucket_name=PREVIEW_BUCKET,
            client=FakeGcsClient(FakeGcsBucket({SIDECAR_OBJECT: sidecar_blob})),
        )

        response = run.handle_request(
            "POST",
            "/v1/assets/wdpa-marine/releases/latest:lookup",
            {"X-Goog-Authenticated-User-Email": "accounts.google.com:jona@skytruth.org"},
            json.dumps({"ids": ["1", "2"], "fields": ["name"], "include_provenance": True}).encode("utf-8"),
            release_resolver=FakeResolver(),
            feature_index=index,
        )

        self.assertEqual(response.status, 200)
        payload = json.loads(response.body)
        self.assertEqual(payload["items"][0]["properties"], {"name": "A"})
        self.assertEqual(payload["items"][0]["provenance"], {"source": "test"})
        self.assertEqual(payload["items"][0]["feature_id"], "1")
        self.assertEqual(payload["items"][1], {"feature_id": "2", "found": False})
        self.assertEqual(sidecar_blob.download_count, 1)

    def test_sidecar_cache_supports_lookup_by_feature_id(self):
        sidecar_blob = FakeGcsBlob(SIDECAR_OBJECT, sidecar_bytes([sidecar_record()]), generation=1001)
        index = run.GcsSidecarFeatureIndex(
            bucket_name=PREVIEW_BUCKET,
            client=FakeGcsClient(FakeGcsBucket({SIDECAR_OBJECT: sidecar_blob})),
        )

        response = run.handle_request(
            "POST",
            "/v1/assets/wdpa-marine/releases/latest:lookup",
            {"X-Goog-Authenticated-User-Email": "accounts.google.com:jona@skytruth.org"},
            json.dumps({"feature_ids": ["1", "2", "1"], "fields": ["name"], "include_provenance": True}).encode("utf-8"),
            release_resolver=FakeResolver(),
            feature_index=index,
        )

        self.assertEqual(response.status, 200)
        payload = json.loads(response.body)
        self.assertEqual(payload["items"][0]["feature_id"], "1")
        self.assertEqual(payload["items"][0]["feature_id"], "1")
        self.assertEqual(payload["items"][0]["properties"], {"name": "A"})
        self.assertEqual(payload["items"][0]["provenance"], {"source": "test"})
        self.assertEqual(payload["items"][1], {"feature_id": "2", "found": False})
        self.assertEqual(payload["items"][2]["feature_id"], "1")
        self.assertEqual(sidecar_blob.download_count, 1)

    def test_sidecar_cache_reuses_loaded_release(self):
        sidecar_blob = FakeGcsBlob(SIDECAR_OBJECT, sidecar_bytes([sidecar_record()]), generation=1001)
        index = run.GcsSidecarFeatureIndex(
            bucket_name=PREVIEW_BUCKET,
            client=FakeGcsClient(FakeGcsBucket({SIDECAR_OBJECT: sidecar_blob})),
        )

        for _index in range(2):
            response = run.handle_request(
                "POST",
                "/v1/assets/wdpa-marine/releases/latest:lookup",
                {"X-Goog-Authenticated-User-Email": "accounts.google.com:jona@skytruth.org"},
                json.dumps({"ids": ["1"]}).encode("utf-8"),
                release_resolver=FakeResolver(),
                feature_index=index,
            )
            self.assertEqual(response.status, 200)

        self.assertEqual(sidecar_blob.download_count, 1)

    def test_sidecar_cache_key_includes_generation(self):
        sidecar_blob = FakeGcsBlob(SIDECAR_OBJECT, sidecar_bytes([sidecar_record("1", name="A")]), generation=1001)
        index = run.GcsSidecarFeatureIndex(
            bucket_name=PREVIEW_BUCKET,
            client=FakeGcsClient(FakeGcsBucket({SIDECAR_OBJECT: sidecar_blob})),
        )

        first = index.lookup("wdpa-marine", "2026-06-01", ["1"], sidecar_uri=SIDECAR_URI, sidecar_generation=1001)
        sidecar_blob.payload = sidecar_bytes([sidecar_record("2", name="B")])
        sidecar_blob.generation = 1002
        second = index.lookup("wdpa-marine", "2026-06-01", ["2"], sidecar_uri=SIDECAR_URI, sidecar_generation=1002)

        self.assertEqual(first["1"]["properties"]["name"], "A")
        self.assertEqual(second["2"]["properties"]["name"], "B")
        self.assertEqual(sidecar_blob.download_count, 2)

    def test_missing_metadata_sidecar_file_returns_not_ready(self):
        resolver = run.CatalogReleaseResolver(
            bucket_name=PREVIEW_BUCKET,
            client=FakeGcsClient(release_index_bucket(release_index_payload(include_sidecar=False))),
            ttl_seconds=0,
        )

        response = run.handle_request(
            "POST",
            "/v1/assets/wdpa-marine/releases/latest:lookup",
            {"X-Goog-Authenticated-User-Email": "accounts.google.com:jona@skytruth.org"},
            b'{"ids":["1"]}',
            release_resolver=resolver,
            feature_index=FakeIndex(),
        )

        self.assertEqual(response.status, 409)
        self.assertEqual(json.loads(response.body)["error"]["code"], "index_not_ready")

    def test_missing_sidecar_object_returns_not_ready(self):
        index = run.GcsSidecarFeatureIndex(bucket_name=PREVIEW_BUCKET, client=FakeGcsClient(FakeGcsBucket({})))

        response = run.handle_request(
            "POST",
            "/v1/assets/wdpa-marine/releases/latest:lookup",
            {"X-Goog-Authenticated-User-Email": "accounts.google.com:jona@skytruth.org"},
            b'{"ids":["1"]}',
            release_resolver=FakeResolver(),
            feature_index=index,
        )

        self.assertEqual(response.status, 409)
        self.assertEqual(json.loads(response.body)["error"]["code"], "index_not_ready")

    def test_sidecar_download_failure_returns_unavailable(self):
        sidecar_blob = FakeGcsBlob(SIDECAR_OBJECT, b"", generation=1001, fail_download=True)
        index = run.GcsSidecarFeatureIndex(
            bucket_name=PREVIEW_BUCKET,
            client=FakeGcsClient(FakeGcsBucket({SIDECAR_OBJECT: sidecar_blob})),
        )

        response = run.handle_request(
            "POST",
            "/v1/assets/wdpa-marine/releases/latest:lookup",
            {"X-Goog-Authenticated-User-Email": "accounts.google.com:jona@skytruth.org"},
            b'{"ids":["1"]}',
            release_resolver=FakeResolver(),
            feature_index=index,
        )

        self.assertEqual(response.status, 503)
        self.assertEqual(json.loads(response.body)["error"]["code"], "index_unavailable")

    def test_bad_sidecar_contents_return_not_ready(self):
        duplicate = sidecar_record("1")
        unsafe_feature_id = sidecar_record("badid")
        unsafe_feature_id["feature_id"] = "bad-id"
        duplicate_feature_id = sidecar_record("1")
        malformed_hash = sidecar_record("2")
        malformed_hash["geometry_hash"] = "not-a-hash"
        missing_asset_slug = sidecar_record("2")
        del missing_asset_slug["asset_slug"]
        missing_release = sidecar_record("2")
        del missing_release["release"]
        duplicate_identity_a = sidecar_record("1")
        duplicate_identity_a["identity_key"] = ["same-source"]
        duplicate_identity_b = sidecar_record("2")
        duplicate_identity_b["identity_key"] = ["same-source"]
        cases = {
            "bad gzip": b"not gzip",
            "bad ndjson": gzip.compress(b"{bad\n"),
            "duplicate feature_id": sidecar_bytes([duplicate, duplicate]),
            "invalid feature_id": sidecar_bytes([unsafe_feature_id]),
            "duplicate feature_id second pass": sidecar_bytes([sidecar_record("1"), duplicate_feature_id]),
            "malformed hash": sidecar_bytes([malformed_hash]),
            "missing asset_slug": sidecar_bytes([missing_asset_slug]),
            "missing release": sidecar_bytes([missing_release]),
            "duplicate identity_key": sidecar_bytes([duplicate_identity_a, duplicate_identity_b]),
        }

        for label, payload in cases.items():
            with self.subTest(label=label):
                sidecar_blob = FakeGcsBlob(SIDECAR_OBJECT, payload, generation=1001)
                index = run.GcsSidecarFeatureIndex(
                    bucket_name=PREVIEW_BUCKET,
                    client=FakeGcsClient(FakeGcsBucket({SIDECAR_OBJECT: sidecar_blob})),
                )

                response = run.handle_request(
                    "POST",
                    "/v1/assets/wdpa-marine/releases/latest:lookup",
                    {"X-Goog-Authenticated-User-Email": "accounts.google.com:jona@skytruth.org"},
                    b'{"ids":["1"]}',
                    release_resolver=FakeResolver(),
                    feature_index=index,
                )

                self.assertEqual(response.status, 409)
                self.assertEqual(json.loads(response.body)["error"]["code"], "index_not_ready")


if __name__ == "__main__":
    unittest.main()
