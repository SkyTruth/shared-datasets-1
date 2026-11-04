import csv
import io
import json
from pathlib import Path
from unittest import mock

import pytest
from google.api_core.exceptions import Forbidden, NotFound, PreconditionFailed

from ingestion.common import feature_metadata, translation_notices as notices
from scripts import feature_metadata_localization as loc, gcs_asset, release_feature_model as model, translation_local_io
from test_compliance_audit import audit, blob_info
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
                if name not in bucket.objects:
                    raise NotFound(name)
                self.generation = bucket.objects[name][0]

            def download_as_bytes(self, *, if_generation_match):
                if name not in bucket.objects:
                    raise NotFound(name)
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


def fixture(tmp_path, *, count=2, access="public", values=None, properties=None, translation_rows=(), root="category/group/example-asset"):
    bucket = Bucket()
    prefix = f"{root}/releases/2026-05-01/example-asset"
    values = values if values is not None else [f"Sensitive <@ALL> {i}" for i in range(1, count + 1)]
    properties = properties if properties is not None else [{"name": value} for value in values]
    count = len(properties)
    records = [sidecar_record(str(i), VALID_HASH_A, props) for i, props in enumerate(properties, 1)]
    manifest = feature_metadata.manifest_payload(asset_slug="example-asset", release="2026-05-01", bucket_name="bucket", asset_root=root,
        sha256_by_role={role: "a" * 64 for role in ("fgb", "pmtiles", "metadata", "schema")},
        schema=feature_metadata.schema_from_records(asset_slug="example-asset", release="2026-05-01", records=records),
        source_inputs=[], identity=model.build_identity_metadata(strategy="source_field", source_fields=["name"]), feature_count=count)
    for entry in manifest["artifacts"]:
        if entry["role"] != "manifest":
            entry.update(generation=1, size=1)
    reports = []
    for locale in ("es", "fr"):
        report = loc.LocalizationReport(locale, "", "", "", sorted({field for props in properties for field in props}))
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
    with (
        mock.patch.object(notices.catalog_csv, "catalog_row", return_value=row),
        mock.patch.object(notices.slack_notify, "load_webhook_url", return_value="https://example.test/webhook"),
        mock.patch.object(notices.slack_notify, "post_webhook") as send,
    ):
        assert notices.notify_release(client, uri) == "delivered"
        assert notices.notify_release(client, uri) == "already_claimed"
    send.assert_called_once()
    payload = send.call_args.args[1]
    body = payload["blocks"][1]["text"]["text"]
    assert "Published successfully." in body
    assert "*0.00%* of translation entries are current across *2* languages. *100.00%* fall back to the original text." in body
    assert "Previous-release comparison unavailable." in body
    assert "Fill in missing translations and refresh outdated translations for example-asset, release 2026-05-01, and prepare a review PR." in body
    assert "stale" not in body
    assert "Untranslated text *100.00%*" in body
    assert "Lowest language coverage" not in body
    assert f"<https://storage.cloud.google.com/{uri.removeprefix('gs://')}|Coverage details>" in body
    assert body.count("https://") == 1
    assert payload["blocks"][0]["text"]["text"].startswith("🌐 Translations")
    assert "```" not in body
    assert ("Sensitive" in payload["text"]) == (access == "public")
    assert ("Needs translation" in body) == (access == "public")
    assert "Illustrative" not in body
    assert "<@ALL>" not in body
    assert notices._slack_length(body) <= 3000
    for locale in ("es", "fr"):
        _, data = bucket.objects[f"_scratch/translation-debt/example-asset/2026-05-01/{locale}.csv"]
        debt = list(csv.DictReader(io.StringIO(data.decode())))
        assert len(debt) == 30
        assert all(item["source_value_hash"] == loc.source_value_hash(item["source_value"]) for item in debt)
    marker = next(json.loads(data) for name, (_, data) in bucket.objects.items() if name.endswith("translation-notice.json"))
    assert marker["status"] == "delivered"
    assert len(marker["debt_files"]) == 2
    assert len(bucket.downloads) == 2


@pytest.mark.parametrize("count", [0, 1, 2])
def test_persisted_notice_claims_pass_audit_and_upload_validation(tmp_path, monkeypatch, count):
    monkeypatch.setenv("SHARED_DATASETS_WORKDIR", str(tmp_path))
    root = "100-geographic-reference/110-boundaries/example-asset"
    client, bucket, uri, row = fixture(tmp_path, count=count, root=root)
    with mock.patch.object(notices.catalog_csv, "catalog_row", return_value=row), mock.patch.object(notices.slack_notify, "notify", return_value=True):
        assert notices.notify_release(client, uri) == ("delivered" if count else "below_threshold")
    claims = [name for name in bucket.objects if "/runs/" in name]
    assert claims == ([f"{root}/runs/2026-05-01.translation-notice.json"] if count else [])
    for name in claims:
        assert json.loads(bucket.objects[name][1])["status"] == "delivered"
        assert audit.validate_object_layout(root, blob_info(name)) == []
        assert gcs_asset.validate_asset_object_name(name, {"100-geographic-reference": {"110-boundaries"}}) == []


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


def test_worklist_csv_roundtrips_through_machine_and_expert_review(tmp_path, monkeypatch):
    monkeypatch.setenv("SHARED_DATASETS_WORKDIR", str(tmp_path))
    values = ['Parque "Marino", <Norte> & Sur\nReserva 🐠']
    client, bucket, uri, row = fixture(tmp_path, values=values)
    with mock.patch.object(notices.catalog_csv, "catalog_row", return_value=row), mock.patch.object(notices.slack_notify, "notify", return_value=True):
        assert notices.notify_release(client, uri) == "delivered"
    worklist_rows = []
    for locale in ("es", "fr"):
        _, data = bucket.objects[f"_scratch/translation-debt/example-asset/2026-05-01/{locale}.csv"]
        worklist_rows.extend(csv.DictReader(io.StringIO(data.decode())))
    assert len(worklist_rows) == 2
    assert all(item["source_value"] == values[0] for item in worklist_rows)
    canonical = tmp_path / "example-asset.metadata.ndjson.gz"
    model.write_metadata_sidecar([sidecar_record("1", VALID_HASH_A, {"name": values[0]})], canonical)
    source = tmp_path / "translations.csv"
    for state, value in (("machine_placeholder", "Machine name"), ("human_reviewed", "Expert name")):
        with source.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=(*loc.REQUIRED_TRANSLATION_COLUMNS, *loc.OPTIONAL_TRANSLATION_COLUMNS))
            writer.writeheader()
            for item in worklist_rows:
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


def test_oversized_and_fenced_values_stay_in_the_worklist(tmp_path, monkeypatch):
    monkeypatch.setenv("SHARED_DATASETS_WORKDIR", str(tmp_path))
    values = ["🪸" * 3000, "source ``` with a fence", "Small valid name"]
    client, bucket, uri, row = fixture(tmp_path, values=values)
    with mock.patch.object(notices.catalog_csv, "catalog_row", return_value=row), mock.patch.object(notices.slack_notify, "notify", return_value=True) as send:
        assert notices.notify_release(client, uri) == "delivered"
    body = send.call_args.kwargs["body"]
    assert "```" not in body and notices._slack_length(body) <= 3000
    assert values[0] not in body and values[1] not in body
    assert "…" in body
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


@pytest.mark.parametrize("prior_current,expected", [
    (0, "Coverage is up 50.00 percentage points since 2026-04-01."),
    (1, "Coverage is unchanged to two decimal places since 2026-04-01."),
    (2, "Coverage is down 50.00 percentage points since 2026-04-01."),
])
def test_notice_compares_immediately_previous_indexed_release(tmp_path, monkeypatch, prior_current, expected):
    monkeypatch.setenv("SHARED_DATASETS_WORKDIR", str(tmp_path))
    rows = [loc.TranslationRow(1, "1", "name", locale, loc.source_value_hash("Name 1"), "Translated", "machine_translated")
            for locale in ("es", "fr")]
    client, bucket, uri, row = fixture(tmp_path, values=["Name 1", "Name 2"], translation_rows=rows)
    name = uri.removeprefix("gs://bucket/")
    current = json.loads(bucket.objects[name][1])
    index = notices.release_index.empty_release_index("example-asset")
    for date in ("2026-03-01", "2026-06-01", "2026-04-01", "2026-05-01"):
        path = name.replace("2026-05-01", date)
        if date != "2026-05-01":
            previous = json.loads(json.dumps(current).replace("2026-05-01", date))
            for counts in previous["translations"]["locales"].values():
                counts.update(current=prior_current, missing=2 - prior_current, coverage=prior_current / 2,
                              review_states={"machine_translated": prior_current})
            bucket.blob(path).upload_from_string(json.dumps(previous), content_type="application/json", if_generation_match=0)
        index["releases"].append({"date": date, "files": [
            {"format": "manifest", "path": f"gs://bucket/{path}", "generation": bucket.objects[path][0]},
        ]})
    bucket.blob(notices.release_index.release_index_object("example-asset")).upload_from_string(
        json.dumps(index), content_type="application/json", if_generation_match=0,
    )
    with mock.patch.object(notices.catalog_csv, "catalog_row", return_value=row), mock.patch.object(notices.slack_notify, "notify", return_value=True) as send:
        assert notices.notify_release(client, uri) == "delivered"
    body = send.call_args.kwargs["body"]
    assert "*50.00%* of translation entries are current across *2* languages. *50.00%* fall back to the original text." in body
    assert expected in body


def test_history_outage_still_delivers_current_coverage(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("SHARED_DATASETS_WORKDIR", str(tmp_path))
    client, bucket, uri, row = fixture(tmp_path)
    original_blob = bucket.blob

    def blob(name, generation=None):
        if name == notices.release_index.release_index_object("example-asset"):
            result = mock.Mock()
            result.reload.side_effect = Forbidden("history unavailable")
            return result
        return original_blob(name, generation)

    with mock.patch.object(bucket, "blob", side_effect=blob), \
            mock.patch.object(notices.catalog_csv, "catalog_row", return_value=row), \
            mock.patch.object(notices.slack_notify, "notify", return_value=True) as send:
        assert notices.notify_release(client, uri) == "delivered"
    assert "*0.00%*" in send.call_args.kwargs["body"]
    assert "Previous-release comparison unavailable." in send.call_args.kwargs["body"]
    assert "Previous translation coverage unavailable" in caplog.text


@pytest.mark.parametrize("prior_locales", [{}, {"es": {"current": 1, "translatable_values": 1}},
    {locale: {"current": 0, "translatable_values": 0} for locale in ("es", "fr")}])
def test_unavailable_or_different_language_coverage_has_no_invented_change(tmp_path, prior_locales):
    _, bucket, uri, _ = fixture(tmp_path)
    current = json.loads(bucket.objects[uri.removeprefix("gs://bucket/")][1])
    previous = {"release": "2026-04-01", "translations": {"locales": prior_locales}}
    assert "Previous-release comparison unavailable." in notices._notice_body(current, uri, previous)


@pytest.mark.parametrize("currents,expected", [
    ({"es": 97, "fr": 97, "id": 97}, ""),
    ({"es": 97, "fr": 93, "id": 97}, ""),
    ({"es": 97, "fr": 92, "id": 97}, "fr at 92.00%"),
    ({"es": 97, "fr": 82, "id": 99}, "versus 98.00% median for the others"),
    ({"es": 97}, ""),
    ({}, ""),
])
def test_language_outlier_only_for_material_gap(currents, expected):
    locales = {locale: {"current": current, "translatable_values": 100} for locale, current in currents.items()}
    locales["pt"] = {"current": 0, "translatable_values": 0}
    line = notices._language_outlier(locales)
    if expected:
        assert expected in line
    else:
        assert line == ""


def test_examples_come_from_unresolved_source_text(tmp_path, monkeypatch):
    monkeypatch.setenv("SHARED_DATASETS_WORKDIR", str(tmp_path))
    rows = [loc.TranslationRow(1, "1", "name", locale, loc.source_value_hash("Old source"), "Old translation", "machine_translated")
            for locale in ("es", "fr")]
    client, _, uri, row = fixture(tmp_path, values=["Changed source", "Untranslated source"], translation_rows=rows)
    with mock.patch.object(notices.catalog_csv, "catalog_row", return_value=row), mock.patch.object(notices.slack_notify, "notify", return_value=True) as send:
        assert notices.notify_release(client, uri) == "delivered"
    body = send.call_args.kwargs["body"]
    assert "Illustrative" not in body
    assert "Outdated translations *50.00%* · Untranslated text *50.00%*" in body
    assert "Needs translation — name (es) “Changed source”" in body
    assert "Old translation" not in body
    assert "Visitor centre" not in body
    assert "stale" not in body
    assert notices._slack_length(body) < 1200


@pytest.mark.parametrize("baseline_state", ["valid", "different_identity", "corrupt"])
def test_counts_only_new_records_and_columns_with_gaps_once_across_languages(tmp_path, monkeypatch, baseline_state):
    monkeypatch.setenv("SHARED_DATASETS_WORKDIR", str(tmp_path))
    properties = [{"name": "Old one", "new_column": "New description"},
                  {"name": "New two", "new_column": "Another description"},
                  {"name": "Translated three", "new_column": "Translated description"}]
    rows = [loc.TranslationRow(1, "3", field, locale, loc.source_value_hash(value), "Translated", "machine_translated")
            for locale in ("es", "fr") for field, value in properties[2].items()]
    client, bucket, uri, row = fixture(tmp_path, properties=properties, translation_rows=rows)
    current = json.loads(bucket.objects[uri.removeprefix("gs://bucket/")][1])
    previous = json.loads(json.dumps(current).replace("2026-05-01", "2026-04-01"))
    previous.pop("translations")
    old_records = [sidecar_record("1", VALID_HASH_A, {"name": "Old one"}),
                   sidecar_record("4", VALID_HASH_A, {"name": "Removed four"})]
    for feature in old_records:
        feature["release"] = "2026-04-01"
    previous["schema"] = feature_metadata.schema_from_records(asset_slug="example-asset", release="2026-04-01", records=old_records)
    previous["validation"]["feature_count"] = len(old_records)
    if baseline_state == "different_identity":
        previous["identity"]["source_fields"] = ["other_key"]
    path = tmp_path / "previous.ndjson.gz"
    model.write_metadata_sidecar(old_records, path)
    artifact = next(entry for entry in previous["artifacts"] if entry["role"] == "metadata")
    blob = bucket.blob(artifact["path"].removeprefix("gs://bucket/"))
    blob.upload_from_filename(path, content_type="application/gzip", if_generation_match=0)
    artifact.update(generation=blob.generation, size=path.stat().st_size, sha256=translation_local_io.file_sha256(path))
    if baseline_state == "corrupt":
        artifact["sha256"] = "f" * 64
    previous_uri = uri.replace("2026-05-01", "2026-04-01")
    blob = bucket.blob(previous_uri.removeprefix("gs://bucket/"))
    blob.upload_from_string(json.dumps(previous), content_type="application/json", if_generation_match=0)
    index = notices.release_index.empty_release_index("example-asset")
    index["releases"] = [{"date": "2026-04-01", "files": [{"format": "manifest", "path": previous_uri, "generation": blob.generation}]}]
    bucket.blob(notices.release_index.release_index_object("example-asset")).upload_from_string(
        json.dumps(index), content_type="application/json", if_generation_match=0,
    )
    with mock.patch.object(notices.catalog_csv, "catalog_row", return_value=row), mock.patch.object(notices.slack_notify, "notify", return_value=True) as send:
        assert notices.notify_release(client, uri) == "delivered"
    body = send.call_args.kwargs["body"]
    if baseline_state == "valid":
        assert "New records with gaps *1* · New columns with gaps *1* (since 2026-04-01)." in body
    else:
        assert "New records with gaps" not in body
    assert "name (es) “Old one”" in body
    assert "new_column (es) “New description”" in body
    assert "Translated three" not in body


def test_examples_skip_url_only_values(tmp_path, monkeypatch):
    monkeypatch.setenv("SHARED_DATASETS_WORKDIR", str(tmp_path))
    client, _, uri, row = fixture(tmp_path, values=["https://example.test/plan.pdf", "Actual pending label"])
    with mock.patch.object(notices.catalog_csv, "catalog_row", return_value=row), mock.patch.object(notices.slack_notify, "notify", return_value=True) as send:
        assert notices.notify_release(client, uri) == "delivered"
    body = send.call_args.kwargs["body"]
    assert "Actual pending label" in body
    assert "plan.pdf" not in body
