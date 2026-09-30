from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from google.auth.credentials import AnonymousCredentials
from google.cloud.storage import Client

from ingestion.common import publication as p
from ingestion.common.publication_gcs import GcsStore
from test_publication import publication_temp_directory


class GcsPublicationTests(unittest.TestCase):
    def test_download_headers_do_not_replace_stored_object_metadata(self):
        client = Client(project="test", credentials=AnonymousCredentials())
        data = b'{"active":null}'
        uri = "gs://bucket/state.json"
        stored = {"generation": "17", "size": str(len(data)), "contentType": "application/json",
                  "cacheControl": "no-cache", "metadata": {"owner": "reviewed"}}
        response = SimpleNamespace(headers={"Content-Type": "application/octet-stream",
                                            "Cache-Control": "no-cache, no-store, max-age=0, must-revalidate",
                                            "X-goog-generation": "17"})
        def download(blob, *args, if_generation_match):
            self.assertEqual(if_generation_match, 17)
            blob._extract_headers_from_download(response)
            if args:
                args[0].write(data)
            else:
                return data
        with (
            publication_temp_directory() as temporary,
            mock.patch.dict("os.environ", {"SHARED_DATASETS_WORKDIR": temporary}),
            mock.patch.object(client._connection, "api_request", side_effect=lambda **_kwargs: dict(stored)),
            mock.patch("google.cloud.storage.blob.Blob.download_as_bytes", download),
            mock.patch("google.cloud.storage.blob.Blob.download_to_file", download),
        ):
            store = GcsStore(client)
            expected = p.ObjectVersion(uri, 17, p.digest(data), len(data), "application/json", "no-cache", (("owner", "reviewed"),))
            self.assertEqual(store.read_json(uri), p.JsonObject({"active": None}, expected))
            self.assertEqual(store.inspect(uri, 17), expected)

    def test_real_sdk_rewrite_atomically_sets_tags_and_both_preconditions(self):
        client = Client(project="test", credentials=AnonymousCredentials())
        store = GcsStore(client)
        source = p.ObjectVersion("gs://bucket/source", 17, p.digest(b"data"), 4)
        tags = {"asset_slug": "example-asset", "publication-transaction": "a" * 64}
        response = {"done": True, "totalBytesRewritten": "4", "objectSize": "4", "resource": {
            "name": "destination", "bucket": "bucket", "generation": "23", "size": "4",
            "contentType": "application/json", "cacheControl": "no-cache", "metadata": tags,
        }}
        with (
            mock.patch.object(store, "inspect", return_value=source),
            mock.patch.object(client._connection, "api_request", side_effect=[{"done": False, "rewriteToken": "continue", "totalBytesRewritten": "2", "objectSize": "4"}, response]) as request,
        ):
            version = store.copy(source, "gs://bucket/destination", 19, tags, "application/json", "no-cache")
        self.assertEqual(version.generation, 23)
        self.assertEqual(request.call_count, 2)
        for call in request.call_args_list:
            params = call.kwargs["query_params"]
            self.assertEqual(params["ifGenerationMatch"], 19)
            self.assertEqual(params["ifSourceGenerationMatch"], 17)
            self.assertEqual(params["sourceGeneration"], 17)
            self.assertEqual(call.kwargs["data"]["metadata"], tags)
            self.assertEqual(call.kwargs["data"]["cacheControl"], "no-cache")
        self.assertEqual(request.call_args_list[1].kwargs["query_params"]["rewriteToken"], "continue")

    def test_checkpoint_upload_uses_verified_private_copy_and_response_generation(self):
        client = Client(project="test", credentials=AnonymousCredentials())
        store = GcsStore(client)
        with publication_temp_directory() as temporary:
            root = Path(temporary)
            source = root / "source.fgb"
            source.write_bytes(b"reviewed")
            captured = []

            def upload(blob, filename, *, content_type, if_generation_match):
                source.write_bytes(b"mutated")
                captured.append(Path(filename))
                self.assertEqual(Path(filename).read_bytes(), b"reviewed")
                self.assertEqual(if_generation_match, 0)
                blob._properties.update(generation="42", size="8", contentType=content_type)

            tags = {"publication-sha256": p.digest(b"reviewed")}
            with mock.patch.dict("os.environ", {"SHARED_DATASETS_WORKDIR": str(root / "work")}), mock.patch("google.cloud.storage.blob.Blob.upload_from_filename", upload):
                version = store.upload("gs://bucket/checkpoint", source, 0, tags, "application/octet-stream", "")
            self.assertEqual(version.generation, 42)
            self.assertTrue(all(not path.exists() for path in captured))
            self.assertEqual(list((root / "work" / "_scratch").iterdir()), [])

    def test_authority_reads_reject_duplicate_and_nonfinite_json(self):
        client = Client(project="test", credentials=AnonymousCredentials())
        for data in (b'{"active":null,"active":{}}', b'{"nested":{"x":1,"x":2}}', b'{"x":NaN}', b'{"x":Infinity}', b'{"x":1e999}', b'[]'):
            with self.subTest(data=data):
                blob = mock.Mock(generation=10, size=len(data), content_type="application/json", cache_control="", metadata={})
                blob.download_as_bytes.return_value = data
                store = GcsStore(client)
                with mock.patch.object(store, "_blob", return_value=blob), self.assertRaises(p.PublicationError):
                    store.read_json("gs://bucket/state.json")
                blob.download_as_bytes.assert_called_once_with(if_generation_match=10)

    def test_changed_checkpoint_bytes_never_reach_upload(self):
        client = Client(project="test", credentials=AnonymousCredentials())
        with publication_temp_directory() as temporary:
            root = Path(temporary)
            source = root / "source.fgb"
            source.write_bytes(b"changed")
            with (
                mock.patch.dict("os.environ", {"SHARED_DATASETS_WORKDIR": str(root / "work")}),
                mock.patch("google.cloud.storage.blob.Blob.upload_from_filename") as upload,
                self.assertRaisesRegex(p.PublicationError, "changed since preparation"),
            ):
                GcsStore(client).upload("gs://bucket/checkpoint", source, 0, {"publication-sha256": p.digest(b"approved")}, "application/octet-stream", "")
            upload.assert_not_called()


if __name__ == "__main__":
    unittest.main()
