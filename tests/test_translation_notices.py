import csv
import html
import io
import json
from pathlib import Path
from unittest import mock

import pytest
from google.api_core.exceptions import PreconditionFailed

from ingestion.common import feature_metadata, translation_notices as notices
from scripts import feature_metadata_localization as loc, release_feature_model as model, translation_local_io
from test_feature_metadata_localization import sidecar_record, VALID_HASH_A


class Bucket:
    def __init__(self):
        self.objects = {}
        self.serial = 0
        self.downloads = []

    def blob(self, name, generation=None):
        bucket = self
        class Blob:
            def reload(self):
                self.generation = bucket.objects[name][0]

            def download_as_bytes(self, *, if_generation_match):
                current, data = bucket.objects[name]
                assert if_generation_match == current
                if generation is not None:
                    assert generation == current
                return data

            def download_to_filename(self, path, *, if_generation_match):
                bucket.downloads.append(name)
                Path(path).write_bytes(self.download_as_bytes(if_generation_match=if_generation_match))

            def upload_from_string(self, data, *, content_type, if_generation_match):
                if bucket.objects.get(name, (0,))[0] != if_generation_match:
                    raise PreconditionFailed("generation changed")
                bucket.serial += 1
                self.generation = bucket.serial
                bucket.objects[name] = (self.generation, data.encode() if isinstance(data, str) else data)

            def upload_from_filename(self, path, **kwargs):
                self.upload_from_string(Path(path).read_bytes(), **kwargs)
        return Blob()


def fixture(tmp_path, *, count=2, access="public", values=None, translation_rows=()):
    bucket = Bucket()
    root = "category/group/example-asset"
    prefix = f"{root}/releases/2026-05-01/example-asset"
    values = values if values is not None else [f"Sensitive <@ALL> {i}" for i in range(1, count + 1)]
    count = len(values)
    records = [sidecar_record(str(i), VALID_HASH_A, {"name": value}) for i, value in enumerate(values, 1)]
    manifest = feature_metadata.manifest_payload(asset_slug="example-asset", release="2026-05-01", bucket_name="bucket", asset_root=root,
        sha256_by_role={role: "a" * 64 for role in ("fgb", "pmtiles", "metadata", "schema")},
        schema=feature_metadata.schema_from_records(asset_slug="example-asset", release="2026-05-01", records=[]),
        source_inputs=[], identity=model.build_identity_metadata(strategy="source_field", source_fields=["name"]), feature_count=count)
    for entry in manifest["artifacts"]:
        if entry["role"] != "manifest":
            entry.update(generation=1, size=1)
    reports = []
    for locale in ("es", "fr"):
        report = loc.LocalizationReport(locale, "", "", "", ["name"])
        path = tmp_path / f"{locale}.ndjson.gz"
        model.write_metadata_sidecar((loc.localize_record(record,
            rows=[item for item in translation_rows if item.locale == locale and item.feature_id == record["feature_id"]],
            report=report) for record in records), path)
        name = prefix + f".metadata.{locale}.ndjson.gz"
        blob = bucket.blob(name)
        blob.upload_from_filename(path, content_type="application/gzip", if_generation_match=0)
        manifest["artifacts"].append({"role": f"metadata-{locale}", "format": "metadata", "path": f"gs://bucket/{name}",
            "generation": blob.generation, "size": path.stat().st_size, "sha256": translation_local_io.file_sha256(path)})
        reports.append(report)
    manifest["translations"] = loc.translations_payload(reports)
    manifest_name = prefix + ".manifest.json"
    bucket.blob(manifest_name).upload_from_string(json.dumps(manifest), content_type="application/json", if_generation_match=0)
    client = mock.Mock()
    client.bucket.return_value = bucket
    row = {"translation_locales": "es;fr", "access_tier": access}
    return client, bucket, f"gs://bucket/{manifest_name}", row


@pytest.mark.parametrize("total,debt,expected", [(0, 0, False), (101, 1, False), (100, 1, True), (99, 1, True), (200, 2, True)])
def test_inclusive_integer_threshold(total, debt, expected):
    assert notices.debt_at_threshold({"translatable_values": total, "stale": debt, "missing": 0}) is expected


@pytest.mark.parametrize("access", ["public", "internal", "private"])
def test_one_combined_notice_exports_all_debt_and_respects_privacy(tmp_path, monkeypatch, access):
    monkeypatch.setenv("SHARED_DATASETS_WORKDIR", str(tmp_path))
    client, bucket, uri, row = fixture(tmp_path, count=30, access=access)
    with mock.patch.object(notices.catalog_csv, "catalog_row", return_value=row), mock.patch.object(notices.slack_notify, "notify", return_value=True) as send:
        assert notices.notify_release(client, uri) == "delivered"
        assert notices.notify_release(client, uri) == "already_claimed"
    send.assert_called_once()
    body = send.call_args.kwargs["body"]
    assert "es: 0/30 current" in body and "fr: 0/30 current" in body
    assert body.count("Sensitive") <= 25
    assert ("Sensitive" in body) == (access == "public")
    assert "<@ALL>" not in body
    assert notices._slack_length(body) <= 3000
    assert ("machine_placeholder" in body) == (access == "public")
    for locale in ("es", "fr"):
        _, data = bucket.objects[f"_scratch/translation-debt/example-asset/2026-05-01/{locale}.csv"]
        debt = list(csv.DictReader(io.StringIO(data.decode())))
        assert len(debt) == 30
        assert all(item["source_value_hash"] == loc.source_value_hash(item["source_value"]) for item in debt)
    marker = next(json.loads(data) for name, (_, data) in bucket.objects.items() if name.endswith("translation-notice.json"))
    assert marker["status"] == "delivered"
    assert len(marker["debt_files"]) == 2
    assert len(bucket.downloads) == 2


def test_unknown_delivery_is_not_reported_as_delivered_or_retried(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("SHARED_DATASETS_WORKDIR", str(tmp_path))
    client, bucket, uri, row = fixture(tmp_path)
    with mock.patch.object(notices.catalog_csv, "catalog_row", return_value=row), mock.patch.object(notices.slack_notify, "notify", return_value=False) as send:
        assert notices.notify_release(client, uri) == "delivery_unknown"
        assert notices.notify_release(client, uri) == "already_claimed"
    send.assert_called_once()
    marker = next(json.loads(data) for name, (_, data) in bucket.objects.items() if name.endswith("translation-notice.json"))
    assert marker["status"] == "delivery_unknown"
    assert "delivery unconfirmed" in caplog.text


def test_prompt_csv_roundtrips_through_machine_and_expert_review(tmp_path, monkeypatch):
    monkeypatch.setenv("SHARED_DATASETS_WORKDIR", str(tmp_path))
    values = ['Parque "Marino", <Norte> & Sur\nReserva 🐠']
    client, _, uri, row = fixture(tmp_path, values=values)
    with mock.patch.object(notices.catalog_csv, "catalog_row", return_value=row), mock.patch.object(notices.slack_notify, "notify", return_value=True) as send:
        assert notices.notify_release(client, uri) == "delivered"
    prompt = html.unescape(send.call_args.kwargs["body"].split("```\n", 1)[1].rsplit("```", 1)[0])
    assert "known official target-language form" in prompt
    assert "feature_id,field,locale,source_value_hash,value,review_state,notes" in prompt
    samples = list(csv.DictReader(io.StringIO(prompt.split("Sample input CSV:\n", 1)[1])))
    assert len(samples) == 2
    assert all(item["source_value"] == values[0] for item in samples)
    canonical = tmp_path / "example-asset.metadata.ndjson.gz"
    model.write_metadata_sidecar([sidecar_record("1", VALID_HASH_A, {"name": values[0]})], canonical)
    source = tmp_path / "translations.csv"
    for state, value in (("machine_placeholder", "Machine name"), ("human_reviewed", "Expert name")):
        with source.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=(*loc.REQUIRED_TRANSLATION_COLUMNS, *loc.OPTIONAL_TRANSLATION_COLUMNS))
            writer.writeheader()
            for item in samples:
                writer.writerow({key: item[key] for key in loc.REQUIRED_TRANSLATION_COLUMNS[:-1]} |
                                {"value": value, "review_state": state, "notes": "awaiting human review" if state == "machine_placeholder" else "expert checked"})
        assert len(loc.read_translation_source(source, translatable_fields={"name"})) == 2
        reports = loc.materialize_locale_sidecars(canonical_sidecar=canonical, translation_source=source,
            output_dir=tmp_path / state, locales=["es", "fr"], translatable_fields={"name"})
        for report in reports:
            assert report.current == 1 and report.stale == report.missing == 0
            assert report.review_states == {state: 1}
            record = next(model.read_metadata_sidecar(Path(report.output_sidecar)))
            assert record["feature_id"] == "1" and record["properties"]["name"] == value


def test_oversized_and_fenced_values_are_exported_without_breaking_prompt(tmp_path, monkeypatch):
    monkeypatch.setenv("SHARED_DATASETS_WORKDIR", str(tmp_path))
    values = ["🪸" * 3000, "source ``` with a fence", "Small valid name"]
    client, bucket, uri, row = fixture(tmp_path, values=values)
    with mock.patch.object(notices.catalog_csv, "catalog_row", return_value=row), mock.patch.object(notices.slack_notify, "notify", return_value=True) as send:
        assert notices.notify_release(client, uri) == "delivered"
    body = send.call_args.kwargs["body"]
    assert body.count("```") == 2 and notices._slack_length(body) <= 3000
    assert "Small valid name" in body and "🪸" not in body
    _, data = bucket.objects["_scratch/translation-debt/example-asset/2026-05-01/es.csv"]
    assert [item["source_value"] for item in csv.DictReader(io.StringIO(data.decode()))] == values


@pytest.mark.parametrize("count,missing,expected", [(0, 0, "below_threshold"), (101, 1, "below_threshold"), (100, 1, "delivered"), (100, 0, "below_threshold")])
def test_notice_threshold_is_per_locale_and_ignores_pending_expert_review(tmp_path, monkeypatch, count, missing, expected):
    monkeypatch.setenv("SHARED_DATASETS_WORKDIR", str(tmp_path))
    values = [f"Name {i}" for i in range(count)]
    rows = [loc.TranslationRow(i, str(i), "name", locale, loc.source_value_hash(value), f"Translated {i}", "machine_placeholder")
            for locale in ("es", "fr") for i, value in enumerate(values, 1) if locale == "fr" or i > missing]
    client, bucket, uri, row = fixture(tmp_path, values=values, translation_rows=rows)
    with mock.patch.object(notices.catalog_csv, "catalog_row", return_value=row), mock.patch.object(notices.slack_notify, "notify", return_value=True) as send:
        assert notices.notify_release(client, uri) == expected
    if expected == "delivered":
        send.assert_called_once()
        assert "es: 99/100 current; 0 stale, 1 missing" in send.call_args.kwargs["body"]
        assert "fr: 100/100 current; 0 stale, 0 missing" in send.call_args.kwargs["body"]
        assert len(bucket.downloads) == 1
    else:
        send.assert_not_called()
        assert not any(name.endswith("translation-notice.json") for name in bucket.objects)


def test_corrupt_sidecar_never_sends_and_does_not_fail_publication(tmp_path, monkeypatch):
    monkeypatch.setenv("SHARED_DATASETS_WORKDIR", str(tmp_path))
    client, bucket, uri, row = fixture(tmp_path)
    name = next(name for name in bucket.objects if name.endswith(".es.ndjson.gz"))
    bucket.objects[name] = (bucket.objects[name][0], b"corrupt")
    with mock.patch.object(notices.catalog_csv, "catalog_row", return_value=row), mock.patch.object(notices.slack_notify, "notify") as send:
        assert notices.notify_release(client, uri) == "failed"
        assert notices.notify_release(client, uri) == "already_claimed"
    send.assert_not_called()
