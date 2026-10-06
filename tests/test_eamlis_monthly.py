from __future__ import annotations

import datetime as dt
import csv
import hashlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from google.api_core.exceptions import NotFound, PreconditionFailed

from ingestion.eamlis_monthly import run as eamlis
from ingestion.common.gcs import GcsPublisher

VALID_FGB_SHA = "a" * 64
VALID_PMTILES_SHA = "b" * 64
VALID_METADATA_SHA = "c" * 64
VALID_SCHEMA_SHA = "d" * 64


def runnable_command(command: list[str]) -> bool:
    executable = shutil.which(command[0])
    if not executable:
        return False
    try:
        completed = subprocess.run(
            [executable, *command[1:]],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0


def gdal_binaries_work() -> bool:
    for command in (["ogrinfo", "--version"], ["ogr2ogr", "--version"], ["pmtiles", "version"]):
        try:
            if not runnable_command(command):
                return False
        except OSError:
            return False
    return True


class FakeBlob:
    def __init__(self, name: str, *, exists: bool = False, generation: int = 1) -> None:
        self.name = name
        self.exists = exists
        self.generation = generation
        self.size = 0
        self.metadata = None
        self.content_type = None
        self.text = ""
        self.data = b""
        self.uploads = []

    def reload(self) -> None:
        if not self.exists:
            raise NotFound("not found")

    def download_as_text(self) -> str:
        self.reload()
        return self.text

    def download_as_bytes(self) -> bytes:
        self.reload()
        return self.data

    def download_to_filename(self, filename, *, if_generation_match):
        self.reload()
        self._check_generation(if_generation_match)
        Path(filename).write_bytes(self.data)

    def upload_from_filename(self, filename, *, content_type=None, if_generation_match=None):
        self._check_generation(if_generation_match)
        self.exists = True
        self.generation += 1
        self.content_type = content_type
        self.data = Path(filename).read_bytes()
        self.text = self.data.decode("utf-8", errors="replace")
        self.size = len(self.data)
        self.uploads.append(("filename", if_generation_match, content_type))

    def upload_from_string(self, data, *, content_type=None, if_generation_match=None):
        self._check_generation(if_generation_match)
        self.exists = True
        self.generation += 1
        self.content_type = content_type
        self.text = data
        self.data = data.encode()
        self.size = len(data.encode())
        self.uploads.append(("string", if_generation_match, content_type))

    def _check_generation(self, if_generation_match):
        if if_generation_match == 0 and self.exists:
            raise PreconditionFailed("exists")
        if if_generation_match not in (None, 0) and if_generation_match != self.generation:
            raise PreconditionFailed("generation mismatch")


class FakeBucket:
    def __init__(self) -> None:
        self.name = "test-bucket"
        self.blobs = {}

    def blob(self, name: str) -> FakeBlob:
        if name not in self.blobs:
            self.blobs[name] = FakeBlob(name)
        return self.blobs[name]

    def list_blobs(self, *, prefix: str):
        return [blob for name, blob in self.blobs.items() if name.startswith(prefix) and blob.exists]


class FakeClient:
    def __init__(self, bucket: FakeBucket) -> None:
        self._bucket = bucket

    def bucket(self, _name: str) -> FakeBucket:
        return self._bucket


class FakeHttpResponse:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self._body = io.BytesIO(body)

    def read(self, size: int = -1) -> bytes:
        return self._body.read(size)

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> bool:
        return False


def sample_metadata(*, data_last_edit_date: int = 2000) -> dict:
    return {
        "serviceItemId": "service-1",
        "editingInfo": {
            "dataLastEditDate": data_last_edit_date,
            "schemaLastEditDate": 1000,
        },
        "fields": [
            {"name": "AMLIS_KEY", "type": "esriFieldTypeString", "alias": "AMLIS_KEY", "length": 11, "nullable": True},
            {"name": "LAT_DEG", "type": "esriFieldTypeSmallInteger", "alias": "LAT_DEG", "nullable": True},
            {"name": "DATE_REVISED", "type": "esriFieldTypeDate", "alias": "DATE_REVISED", "nullable": True},
            {"name": "OBJECTID", "type": "esriFieldTypeOID", "alias": "OBJECTID", "nullable": False},
        ],
    }


def sample_stats(feature_count: int = 2) -> eamlis.SourceStats:
    return eamlis.SourceStats(
        feature_count=feature_count,
        max_date_revised=1777546800000,
        max_objectid=feature_count,
    )


def sample_source_state(*, fingerprint_hash: str = "abc123", feature_count: int = 2) -> eamlis.SourceState:
    fields = eamlis.normalized_fields(sample_metadata())
    fingerprint = {
        "layer_url": "https://example.test/layer",
        "where": eamlis.DEFAULT_WHERE,
        "service_item_id": "service-1",
        "data_last_edit_date": 2000,
        "schema_last_edit_date": 1000,
        "field_schema_hash": eamlis.stable_hash(fields),
        "feature_count": feature_count,
        "max_date_revised": 1777546800000,
    }
    return eamlis.SourceState(
        layer_url="https://example.test/layer",
        where=eamlis.DEFAULT_WHERE,
        service_item_id="service-1",
        data_last_edit_date=2000,
        schema_last_edit_date=1000,
        fields=fields,
        field_schema_hash=eamlis.stable_hash(fields),
        stats=sample_stats(feature_count),
        fingerprint_hash=fingerprint_hash,
        fingerprint=fingerprint,
    )


def fake_asset_output(
    tmp_path: Path,
    *,
    fgb_sha: str = VALID_FGB_SHA,
    pmtiles_sha: str = VALID_PMTILES_SHA,
) -> eamlis.AssetOutput:
    fgb = tmp_path / "asset.fgb"
    pmtiles = tmp_path / "asset.pmtiles"
    metadata = tmp_path / "asset.metadata.ndjson.gz"
    schema = tmp_path / "asset.schema.json"
    manifest = tmp_path / "asset.manifest.json"
    for path, data in (
        (fgb, b"fgb"),
        (pmtiles, b"pmtiles"),
        (metadata, b"metadata"),
        (schema, b'{"schema_version":2}\n'),
    ):
        path.write_bytes(data)
    schema_payload = {
        "schema_version": 2,
        "asset_slug": eamlis.ASSET.slug,
        "release": "2026-05-02",
        "fields": [{"name": "OBJECTID", "type": "Integer", "nullable": False, "projectable": True}],
    }
    eamlis.feature_metadata.write_sidecar([
        {"schema_version": 2, "asset_slug": eamlis.ASSET.slug, "release": "2026-05-02",
         "feature_id": str(index), "identity_key": [str(index)],
         "geometry_hash": "sha256:" + "a" * 64, "properties_hash": "sha256:" + "b" * 64,
         "properties": {"OBJECTID": index, "PA_NAME": name}, "provenance": {"source": "fixture"}}
        for index, name in ((1, "Mine Alpha"), (2, "Mine Beta"))
    ], metadata)
    return eamlis.AssetOutput(
        fgb=fgb,
        pmtiles=pmtiles,
        metadata=metadata,
        schema=schema,
        manifest=manifest,
        row_count=2,
        sha256={
            "fgb": fgb_sha,
            "pmtiles": pmtiles_sha,
            "metadata": VALID_METADATA_SHA,
            "schema": VALID_SCHEMA_SHA,
        },
        schema_payload=schema_payload,
        sidecar_records=(),
    )


class EamlisMonthlyTests(unittest.TestCase):
    def test_csv_reconciliation_precedes_single_materialization_of_all_required_locales(self):
        bucket = FakeBucket()
        blob = bucket.blob(eamlis.ASSET.latest_object(".metadata-translations.csv"))
        blob.exists, blob.generation = True, 9
        baseline = [
            {"feature_id": fid, "field": "PA_NAME", "locale": locale,
             "source_value_hash": eamlis.localization.source_value_hash(text),
             "value": value, "review_state": "human_reviewed", "notes": "provider evidence"}
            for fid, locale, text, value in (
                ("1", "es", "Mine Alpha", "Mina Alfa"),
                ("2", "es", "Old name", "Nombre anterior"),
                ("99", "es", "Mine Beta", "Mina Beta"),
                ("99", "fr", "Mine Beta", "Mine Bêta"),
            )
        ]
        stream = io.StringIO()
        writer = csv.DictWriter(stream, fieldnames=eamlis.translation_reuse.COLUMNS)
        writer.writeheader()
        writer.writerows(baseline)
        blob.data = stream.getvalue().encode()
        original_sha = hashlib.sha256(blob.data).hexdigest()
        publisher = GcsPublisher(FakeClient(bucket), bucket.name)
        materialize = eamlis.localization.materialize_locale_sidecars

        def after_reconciliation(**kwargs):
            with kwargs["translation_source"].open(newline="") as handle:
                reconciled = list(csv.DictReader(handle))
            self.assertEqual(reconciled[:len(baseline)], baseline)
            self.assertEqual([r["locale"] for r in reconciled[len(baseline):]], ["es", "fr"])
            return materialize(**kwargs)

        with tempfile.TemporaryDirectory() as tmpdir, \
             mock.patch.object(eamlis, "TRANSLATION_LOCALES", ("es", "fr")), \
             mock.patch.object(eamlis.localization, "materialize_locale_sidecars", side_effect=after_reconciliation) as generated, \
             mock.patch.object(eamlis.localization, "_materialize_locale_sidecar", wraps=eamlis.localization._materialize_locale_sidecar) as each_locale:
            output = fake_asset_output(Path(tmpdir))
            before = {path: path.read_bytes() for path in (output.metadata, output.schema, output.fgb, output.pmtiles)}
            record = eamlis.publish_changed_asset(publisher=publisher, run_date=dt.date(2026, 5, 2), source=sample_source_state(), output=output)
            generated.assert_called_once()
            self.assertEqual(generated.call_args.kwargs["locales"], ("es", "fr"))
            self.assertEqual([call.kwargs["locale"] for call in each_locale.call_args_list], ["es", "fr"])
            self.assertEqual({path: path.read_bytes() for path in before}, before)
        self.assertEqual(record["localization"]["translations"]["locales"]["es"]["current"], 2)
        self.assertEqual(record["localization"]["translations"]["locales"]["fr"]["current"], 1)
        manifest = json.loads(bucket.blob(eamlis.ASSET.release_object(dt.date(2026, 5, 2), ".manifest.json")).text)
        self.assertEqual(manifest["source_inputs"][1]["sha256"], original_sha)
        for locale, expected in (("es", ["Mina Alfa", "Mina Beta"]), ("fr", ["Mine Alpha", "Mine Bêta"])):
            sidecar = bucket.blob(eamlis.ASSET.release_object(dt.date(2026, 5, 2), f".metadata.{locale}.ndjson.gz"))
            rows = list(eamlis.feature_metadata.release_feature_model.read_metadata_sidecar_bytes(sidecar.data))
            self.assertEqual([r["properties"]["PA_NAME"] for r in rows], expected)

    def test_translation_reuse_pins_input_and_publishes_partial_coverage_in_same_release(self):
        bucket = FakeBucket()
        blob = bucket.blob(eamlis.ASSET.latest_object(".metadata-translations.csv"))
        blob.exists, blob.generation = True, 9
        stream = io.StringIO()
        writer = csv.DictWriter(stream, fieldnames=(*eamlis.localization.REQUIRED_TRANSLATION_COLUMNS, *eamlis.localization.OPTIONAL_TRANSLATION_COLUMNS))
        writer.writeheader()
        writer.writerow({"feature_id": "1", "field": "PA_NAME", "locale": "es",
                         "source_value_hash": eamlis.localization.source_value_hash("Mine Alpha"),
                         "value": "Mina Alfa", "review_state": "machine_translated", "notes": ""})
        blob.data = stream.getvalue().encode()
        publisher = GcsPublisher(FakeClient(bucket), bucket.name)
        with tempfile.TemporaryDirectory() as tmpdir, mock.patch.object(blob, "download_to_filename", wraps=blob.download_to_filename) as download:
            output = fake_asset_output(Path(tmpdir))
            record = eamlis.publish_changed_asset(publisher=publisher, run_date=dt.date(2026, 5, 2), source=sample_source_state(), output=output)
            self.assertEqual(download.call_args.kwargs["if_generation_match"], 9)
        self.assertEqual(record["localization"]["translations"]["locales"]["es"]["coverage"], 0.5)
        sidecar = bucket.blob(eamlis.ASSET.release_object(dt.date(2026, 5, 2), ".metadata.es.ndjson.gz"))
        rows = list(eamlis.feature_metadata.release_feature_model.read_metadata_sidecar_bytes(sidecar.data))
        self.assertEqual([row["properties"]["PA_NAME"] for row in rows], ["Mina Alfa", "Mine Beta"])
        self.assertTrue(all(row["release"] == "2026-05-02" for row in rows))
        manifest = json.loads(bucket.blob(eamlis.ASSET.release_object(dt.date(2026, 5, 2), ".manifest.json")).text)
        self.assertEqual(manifest["translations"], record["localization"]["translations"])
        self.assertEqual(manifest["source_inputs"][1]["generation"], 9)
        self.assertEqual(len(manifest["artifacts"]), 7)

    def test_translation_input_race_or_corruption_prevents_all_canonical_writes(self):
        for problem in (PreconditionFailed("changed"), None):
            with self.subTest(problem=problem), tempfile.TemporaryDirectory() as tmpdir:
                bucket = FakeBucket()
                blob = bucket.blob(eamlis.ASSET.latest_object(".metadata-translations.csv"))
                blob.exists, blob.data = True, b"corrupted CSV"
                publisher = GcsPublisher(FakeClient(bucket), bucket.name)
                output = fake_asset_output(Path(tmpdir))
                with mock.patch.object(blob, "download_to_filename", side_effect=problem, wraps=blob.download_to_filename):
                    with self.assertRaises((PreconditionFailed, eamlis.localization.FeatureMetadataLocalizationError)):
                        eamlis.publish_changed_asset(publisher=publisher, run_date=dt.date(2026, 5, 2), source=sample_source_state(), output=output)
                self.assertFalse(any(item.uploads for item in bucket.blobs.values()))

    def test_request_json_retries_transient_transport_failure(self):
        calls = []

        def flaky(_request, *, timeout):
            calls.append(timeout)
            if len(calls) == 1:
                raise urllib.error.HTTPError(
                    "https://example.test/layer",
                    500,
                    "server error",
                    hdrs=None,
                    fp=None,
                )
            return FakeHttpResponse(200, b'{"ok": true}')

        with (
            mock.patch.object(eamlis.urllib.request, "urlopen", flaky),
            mock.patch("ingestion.common.http.time.sleep"),
        ):
            payload = eamlis.request_json("https://example.test/layer", {"f": "json"})

        self.assertEqual(payload, {"ok": True})
        self.assertEqual(len(calls), 2)

    def test_request_json_rejects_non_json_response(self):
        with mock.patch.object(
            eamlis.urllib.request,
            "urlopen",
            return_value=FakeHttpResponse(200, b"not json"),
        ):
            with self.assertRaisesRegex(RuntimeError, "not JSON"):
                eamlis.request_json("https://example.test/layer", {"f": "json"})

    def test_request_json_rejects_arcgis_application_error(self):
        with mock.patch.object(
            eamlis.urllib.request,
            "urlopen",
            return_value=FakeHttpResponse(200, b'{"error": {"message": "bad"}}'),
        ):
            with self.assertRaisesRegex(RuntimeError, "ArcGIS returned an error"):
                eamlis.request_json("https://example.test/layer", {"f": "json"})

    def test_source_fingerprint_includes_edit_dates_and_stats(self):
        metadata = sample_metadata(data_last_edit_date=1234)
        fields = eamlis.normalized_fields(metadata)
        with (
            mock.patch.object(eamlis, "request_json", side_effect=[metadata, {
                "features": [
                    {
                        "attributes": {
                            "feature_count": 7,
                            "max_date_revised": 1777546800000,
                            "max_objectid": 9,
                        }
                    }
                ]
            }]),
        ):
            state = eamlis.fetch_source_state("https://example.test/layer", eamlis.DEFAULT_WHERE)

        self.assertEqual(state.data_last_edit_date, 1234)
        self.assertEqual(state.stats.feature_count, 7)
        self.assertEqual(state.field_schema_hash, eamlis.stable_hash(fields))
        self.assertEqual(state.fingerprint["max_date_revised"], 1777546800000)

    def test_same_date_completed_run_returns_original_record_without_polling_or_rebuilding(self):
        for status in ("success", "skipped"):
            with self.subTest(status=status):
                bucket = FakeBucket()
                current = bucket.blob(eamlis.ASSET.run_record_object(dt.date(2026, 5, 2)))
                record = {
                    "run_date": "2026-05-02", "status": status,
                    "source_fingerprint_hash": "earlier-source",
                    "reason": "generated FGB hash unchanged" if status == "skipped" else None,
                    "release_path": "gs://test-bucket/releases/2026-05-01/",
                    "source_fingerprint": {"feature_count": 2},
                }
                current.exists, current.text = True, json.dumps(record)
                with (
                    mock.patch.dict(eamlis.os.environ, {"RUN_DATE": "2026-05-02"}, clear=True),
                    mock.patch.object(eamlis, "require_binary"),
                    mock.patch.object(eamlis.storage, "Client", return_value=FakeClient(bucket)),
                    mock.patch.object(eamlis, "fetch_source_state", return_value=sample_source_state(
                        fingerprint_hash="later-source", feature_count=3,
                    )) as fetch,
                    mock.patch.object(eamlis, "download_source_geojson") as download,
                    mock.patch.object(eamlis, "build_asset_output") as build,
                    mock.patch.object(GcsPublisher, "record_existing_successful_release", return_value=None) as index,
                ):
                    records = eamlis.run()
                self.assertEqual(records, [record])
                fetch.assert_not_called()
                download.assert_not_called()
                build.assert_not_called()
                self.assertEqual(index.call_count, int(status == "success"))
                self.assertEqual(current.text, json.dumps(record))
                self.assertFalse(any(blob.uploads for blob in bucket.blobs.values()))

    def test_same_date_noncompleted_record_fails_without_polling_or_writes(self):
        for record in ({}, *({"status": status} for status in ("failed", "partial", "running", "unknown", None))):
            with self.subTest(record=record):
                bucket = FakeBucket()
                current = bucket.blob(eamlis.ASSET.run_record_object(dt.date(2026, 5, 2)))
                current.exists, current.text = True, json.dumps(record)
                with (
                    mock.patch.dict(eamlis.os.environ, {"RUN_DATE": "2026-05-02"}, clear=True),
                    mock.patch.object(eamlis, "require_binary"),
                    mock.patch.object(eamlis.storage, "Client", return_value=FakeClient(bucket)),
                    mock.patch.object(eamlis, "fetch_source_state") as fetch,
                    self.assertRaisesRegex(RuntimeError, "Run record already exists"),
                ):
                    eamlis.run()
                fetch.assert_not_called()
                self.assertFalse(any(blob.uploads for blob in bucket.blobs.values()))

    def test_run_skips_when_latest_success_fingerprint_matches(self):
        bucket = FakeBucket()
        previous = bucket.blob(eamlis.ASSET.run_record_object(dt.date(2026, 4, 2)))
        previous.exists = True
        previous.text = json.dumps(
            {
                "run_date": "2026-04-02",
                "status": "success",
                "source_fingerprint_hash": "same",
                "release_path": "gs://test-bucket/release/",
                "release_paths": [
                    {"path": "gs://test-bucket/release/asset.fgb"},
                    {"path": "gs://test-bucket/release/asset.metadata.ndjson.gz"},
                    {"path": "gs://test-bucket/release/asset.schema.json"},
                    {"path": "gs://test-bucket/release/asset.manifest.json"},
                ],
                "latest_paths": [{"path": "gs://test-bucket/latest.fgb"}],
                "sha256": {"fgb": "old-sha"},
            }
        )
        release_index = bucket.blob(f"_catalog/releases/{eamlis.ASSET.slug}.json")
        release_index.exists = True
        release_index.text = json.dumps(
            {
                "schema_version": 1,
                "asset_slug": eamlis.ASSET.slug,
                "latest_release": {
                    "date": "2026-04-02",
                    "run_record_path": f"gs://test-bucket/{previous.name}",
                },
            }
        )
        source = sample_source_state(fingerprint_hash="same")

        with (
            mock.patch.dict(eamlis.os.environ, {"RUN_DATE": "2026-05-02"}, clear=True),
                mock.patch.object(eamlis, "require_binary", lambda _binary: None),
                mock.patch.object(eamlis.storage, "Client", lambda project: FakeClient(bucket)),
                mock.patch.object(eamlis, "fetch_source_state", return_value=source),
                mock.patch.object(GcsPublisher, "release_metadata_contract_issue", return_value=None),
                mock.patch.object(eamlis, "download_source_geojson") as download,
            ):
                records = eamlis.run()

        self.assertEqual(records[0]["status"], "skipped")
        self.assertEqual(records[0]["reason"], "source fingerprint unchanged")
        self.assertFalse(download.called)
        run_blob = bucket.blob(eamlis.ASSET.run_record_object(dt.date(2026, 5, 2)))
        self.assertEqual(run_blob.uploads[0][1], 0)

    def test_missing_metadata_contract_refreshes_unchanged_source_and_output(self):
        bucket = FakeBucket()
        previous = bucket.blob(eamlis.ASSET.run_record_object(dt.date(2026, 4, 2)))
        previous.exists = True
        previous.text = json.dumps(
            {
                "run_date": "2026-04-02",
                "status": "success",
                "source_fingerprint_hash": "same",
                "release_path": "gs://test-bucket/release/",
                "release_paths": [{"path": "gs://test-bucket/release/asset.fgb"}],
                "latest_paths": [{"path": "gs://test-bucket/latest.fgb"}],
                "sha256": {"fgb": VALID_FGB_SHA},
            }
        )
        release_index = bucket.blob(f"_catalog/releases/{eamlis.ASSET.slug}.json")
        release_index.exists = True
        release_index.text = json.dumps(
            {
                "schema_version": 1,
                "asset_slug": eamlis.ASSET.slug,
                "latest_release": {
                    "date": "2026-04-02",
                    "run_record_path": f"gs://test-bucket/{previous.name}",
                },
            }
        )
        source = sample_source_state(fingerprint_hash="same")
        with tempfile.TemporaryDirectory() as tmp:
            output = fake_asset_output(Path(tmp), fgb_sha=VALID_FGB_SHA)
            with (
                mock.patch.dict(eamlis.os.environ, {"RUN_DATE": "2026-05-02"}, clear=True),
                mock.patch.object(eamlis, "require_binary", lambda _binary: None),
                mock.patch.object(eamlis.storage, "Client", lambda project: FakeClient(bucket)),
                mock.patch.object(eamlis, "fetch_source_state", return_value=source),
                mock.patch.object(eamlis, "download_source_geojson", return_value=mock.Mock()),
                mock.patch.object(eamlis, "build_asset_output", return_value=output),
            ):
                records = eamlis.run()

        self.assertEqual(records[0]["status"], "success")
        self.assertEqual(len(records[0]["release_paths"]), 7)
        release = bucket.blob(eamlis.ASSET.release_object(dt.date(2026, 5, 2), ".fgb"))
        release_metadata = bucket.blob(eamlis.ASSET.release_object(dt.date(2026, 5, 2), ".metadata.ndjson.gz"))
        self.assertTrue(release.uploads)
        self.assertTrue(release_metadata.uploads)

    def test_download_source_geojson_pages_until_expected_count(self):
        source = sample_source_state(feature_count=3)
        pages = [
            {
                "type": "FeatureCollection",
                "features": [
                    self._feature(1, 1777546800000),
                    self._feature(2, 1777460400000),
                ],
                "properties": {"exceededTransferLimit": True},
            },
            {
                "type": "FeatureCollection",
                "features": [self._feature(3, None)],
                "properties": {"exceededTransferLimit": False},
            },
        ]
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "source.geojson"
            with mock.patch.object(eamlis, "request_json", side_effect=pages):
                extract = eamlis.download_source_geojson(source=source, dest=dest, page_size=2)
            payload = json.loads(dest.read_text())

        self.assertEqual(extract.row_count, 3)
        self.assertEqual(payload["features"][0]["properties"]["DATE_REVISED"], "2026-04-30")
        self.assertEqual(payload["features"][2]["properties"]["DATE_REVISED"], None)

    def test_publish_changed_asset_uses_safe_gcs_preconditions_and_run_record(self):
        bucket = FakeBucket()
        latest = bucket.blob(eamlis.ASSET.latest_object(".fgb"))
        latest.exists = True
        latest.generation = 7
        latest_pmtiles = bucket.blob(eamlis.ASSET.latest_object(".pmtiles"))
        publisher = GcsPublisher(
            FakeClient(bucket),
            bucket.name,
            release_suffixes=eamlis.RELEASE_SUFFIXES,
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            output = fake_asset_output(tmp_path)
            record = eamlis.publish_changed_asset(
                publisher=publisher,
                run_date=dt.date(2026, 5, 2),
                source=sample_source_state(fingerprint_hash="new"),
                output=output,
            )

        release = bucket.blob(eamlis.ASSET.release_object(dt.date(2026, 5, 2), ".fgb"))
        release_pmtiles = bucket.blob(eamlis.ASSET.release_object(dt.date(2026, 5, 2), ".pmtiles"))
        run_blob = bucket.blob(eamlis.ASSET.run_record_object(dt.date(2026, 5, 2)))
        self.assertEqual(release.uploads[0][1], 0)
        self.assertEqual(release_pmtiles.uploads[0][1], 0)
        self.assertEqual(latest.uploads[0][1], 7)
        self.assertEqual(latest_pmtiles.uploads[0][1], 0)
        self.assertEqual(run_blob.uploads[0][1], 0)
        self.assertEqual(record["status"], "success")
        self.assertEqual(record["sha256"]["fgb"], VALID_FGB_SHA)
        self.assertEqual(record["sha256"]["pmtiles"], VALID_PMTILES_SHA)
        self.assertEqual(len(record["release_paths"]), 7)
        self.assertEqual(len(record["latest_paths"]), 7)
        manifest_blob = bucket.blob(eamlis.ASSET.release_object(dt.date(2026, 5, 2), ".manifest.json"))
        manifest = json.loads(manifest_blob.text)
        artifacts = {artifact["role"]: artifact for artifact in manifest["artifacts"]}
        release_by_role = dict(zip(("fgb", "pmtiles", "metadata", "schema"), record["release_paths"][:4], strict=True))
        latest_by_role = dict(zip(("fgb", "pmtiles", "metadata", "schema"), record["latest_paths"][:4], strict=True))
        for role in ("fgb", "pmtiles", "metadata", "schema"):
            self.assertEqual(artifacts[role]["path"], release_by_role[role]["path"])
            self.assertEqual(artifacts[role]["generation"], release_by_role[role]["generation"])
            self.assertEqual(artifacts[role]["latest_path"], latest_by_role[role]["path"])
            self.assertEqual(artifacts[role]["latest_generation"], latest_by_role[role]["generation"])
        self.assertNotIn("generation", artifacts["manifest"])
        self.assertNotIn("latest_generation", artifacts["manifest"])
        manifest_sha = hashlib.sha256(manifest_blob.data).hexdigest()
        self.assertEqual(record["sha256"]["manifest"], manifest_sha)
        self.assertEqual(json.loads(run_blob.text)["sha256"]["manifest"], manifest_sha)

    def test_output_hash_unchanged_writes_skipped_record_without_publish(self):
        bucket = FakeBucket()
        previous = bucket.blob(eamlis.ASSET.run_record_object(dt.date(2026, 4, 2)))
        previous.exists = True
        previous.text = json.dumps(
            {
                "run_date": "2026-04-02",
                "status": "success",
                "source_fingerprint_hash": "old",
                "release_path": "gs://test-bucket/release/",
                "release_paths": [
                    {"path": "gs://test-bucket/release/asset.fgb"},
                    {"path": "gs://test-bucket/release/asset.metadata.ndjson.gz"},
                    {"path": "gs://test-bucket/release/asset.schema.json"},
                    {"path": "gs://test-bucket/release/asset.manifest.json"},
                ],
                "latest_paths": [{"path": "gs://test-bucket/latest.fgb"}],
                "sha256": {"fgb": VALID_FGB_SHA},
            }
        )
        release_index = bucket.blob(f"_catalog/releases/{eamlis.ASSET.slug}.json")
        release_index.exists = True
        release_index.text = json.dumps(
            {
                "schema_version": 1,
                "asset_slug": eamlis.ASSET.slug,
                "latest_release": {
                    "date": "2026-04-02",
                    "run_record_path": f"gs://test-bucket/{previous.name}",
                },
            }
        )
        source = sample_source_state(fingerprint_hash="new")
        with tempfile.TemporaryDirectory() as tmp:
            output = fake_asset_output(Path(tmp), fgb_sha=VALID_FGB_SHA)
            with (
                mock.patch.dict(eamlis.os.environ, {"RUN_DATE": "2026-05-02"}, clear=True),
                mock.patch.object(eamlis, "require_binary", lambda _binary: None),
                mock.patch.object(eamlis.storage, "Client", lambda project: FakeClient(bucket)),
                mock.patch.object(eamlis, "fetch_source_state", return_value=source),
                mock.patch.object(GcsPublisher, "release_metadata_contract_issue", return_value=None),
                mock.patch.object(eamlis, "download_source_geojson", return_value=mock.Mock()),
                mock.patch.object(eamlis, "build_asset_output", return_value=output),
            ):
                records = eamlis.run()

        self.assertEqual(records[0]["status"], "skipped")
        self.assertEqual(records[0]["reason"], "generated FGB hash unchanged")
        release = bucket.blob(eamlis.ASSET.release_object(dt.date(2026, 5, 2), ".fgb"))
        release_pmtiles = bucket.blob(eamlis.ASSET.release_object(dt.date(2026, 5, 2), ".pmtiles"))
        self.assertFalse(release.uploads)
        self.assertFalse(release_pmtiles.uploads)

    def test_parse_ogrinfo_summary_from_text_output(self):
        summary = eamlis.parse_ogrinfo_summary(
            """
INFO: Open of `example.fgb'
      using driver `FlatGeobuf' successful.

Layer name: eamlis_abandoned_mine_land_inventory
Geometry: Point
Feature Count: 2
Extent: (-150.000000, 40.000000) - (-149.000000, 41.000000)
Layer SRS WKT:
GEOGCRS["WGS 84"]
FID Column = fid
Geometry Column = geometry
AMLIS_KEY: String (0.0)
LAT_DEG: Integer (0.0)
DATE_REVISED: Date (0.0)
"""
        )

        self.assertEqual(summary["feature_count"], 2)
        self.assertEqual(summary["geometry_type"], "Point")
        self.assertEqual(summary["fields"], ["AMLIS_KEY", "LAT_DEG", "DATE_REVISED"])

    def test_source_field_metadata_records_use_plain_objectid_feature_ids(self):
        features = [self._feature(1, 1777546800000)]

        enriched, records = eamlis.feature_metadata.enrich_features_with_source_field_ids(
            features,
            asset_slug=eamlis.ASSET.slug,
            release="2026-06-16",
            id_field="OBJECTID",
            provenance={"source": "https://example.test/layer", "where": eamlis.DEFAULT_WHERE},
        )

        self.assertEqual(records[0]["schema_version"], 2)
        self.assertEqual(records[0]["feature_id"], "1")
        self.assertRegex(records[0]["geometry_hash"], r"^sha256:[0-9a-f]{64}$")
        self.assertRegex(records[0]["properties_hash"], r"^sha256:[0-9a-f]{64}$")
        self.assertNotIn("feature_hash", records[0])
        self.assertNotIn("ext_id", records[0]["properties"])
        self.assertEqual(enriched[0]["properties"]["feature_id"], "1")
        self.assertIn("geometry_hash", enriched[0]["properties"])
        self.assertIn("properties_hash", enriched[0]["properties"])
        self.assertNotIn("ext_id", enriched[0]["properties"])

    @staticmethod
    def _feature(objectid: int, date_revised: int | None) -> dict:
        return {
            "type": "Feature",
            "id": objectid,
            "geometry": {"type": "Point", "coordinates": [-100 - objectid, 40 + objectid]},
            "properties": {
                "AMLIS_KEY": f"AK{objectid:06d}",
                "LAT_DEG": 40 + objectid,
                "DATE_REVISED": date_revised,
                "OBJECTID": objectid,
            },
        }


@unittest.skipUnless(
    bool(os.environ.get("RUN_GDAL_INTEGRATION_TESTS")) and gdal_binaries_work(),
    "requires RUN_GDAL_INTEGRATION_TESTS=1 and runnable GDAL binaries",
)
class EamlisMonthlyIntegrationTests(unittest.TestCase):
    def test_fixture_geojson_builds_stable_fgb_output(self):
        source = sample_source_state(feature_count=2)
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            geojson = tmp_path / "source.geojson"
            geojson.write_text(
                json.dumps(
                    {
                        "type": "FeatureCollection",
                        "features": [
                            EamlisMonthlyTests._feature(1, 1777546800000),
                            EamlisMonthlyTests._feature(2, 1777460400000),
                        ],
                    }
                )
            )
            extract = eamlis.SourceExtract(
                geojson=geojson,
                row_count=2,
                null_geometry_count=0,
            )
            output_one = eamlis.build_asset_output(
                source=source,
                extract=extract,
                workdir=tmp_path,
                release_date=dt.date(2026, 5, 2),
            )
            first_hash = output_one.sha256["fgb"]
            output_one.fgb.unlink()
            output_two = eamlis.build_asset_output(
                source=source,
                extract=extract,
                workdir=tmp_path,
                release_date=dt.date(2026, 5, 2),
            )

        self.assertEqual(output_two.row_count, 2)
        self.assertEqual(output_two.sha256["fgb"], first_hash)


if __name__ == "__main__":
    unittest.main()
