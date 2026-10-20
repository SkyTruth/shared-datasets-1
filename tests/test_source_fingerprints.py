"""Exercise serialized inputs and the success-only comparison boundary."""

from copy import deepcopy
from dataclasses import replace
import datetime as dt
import gzip
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from ingestion.common import source_fingerprint as fp
from ingestion.eamlis_monthly import run as eamlis
from ingestion.sea_ice_daily import run as sea_ice
from ingestion.wdpa_monthly.source_fingerprint import retained_fingerprint
from tests.test_eamlis_monthly import sample_source_state


FEATURES = [
    {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [1, 2]},
        "properties": {"OBJECTID": 1, "PA_NAME": "Mine A"},
    },
    {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [3, 4]},
        "properties": {"OBJECTID": 2, "PA_NAME": "Mine B"},
    },
]


def test_multiset_contract_in_memory_and_disk(tmp_path):
    for values in ([], [None], [{"b": 1, "a": 2}, {"x": 3}], [1, 1, 2]):
        expected = fp.unordered_digest(values)
        assert expected == fp.unordered_digest(reversed(values))
        assert expected == fp.unordered_digest(values, workdir=tmp_path)
    assert fp.unordered_digest([1, 1]) != fp.unordered_digest([1])
    with pytest.raises(ValueError):
        fp.digest(float("nan"))


def test_geometry_number_spelling_preserves_exact_values():
    equivalent = deepcopy(FEATURES)
    equivalent[0]["geometry"]["coordinates"] = [1.0, 2.0]
    assert fp.feature_digest(FEATURES) == fp.feature_digest(equivalent)
    equivalent[0]["geometry"]["coordinates"][0] = 1.000000000000001
    assert fp.feature_digest(FEATURES) != fp.feature_digest(equivalent)


def test_only_successful_compatible_fingerprint_can_skip():
    fingerprint = fp.Fingerprint(
        "example-v1", {"features": fp.feature_digest(FEATURES)}
    )
    for status in ("success", "skipped", "failed", "partial", "running"):
        assert fingerprint.matches(
            {"status": status, **fingerprint.record_fields()}
        ) == (status == "success")
    assert not fingerprint.matches(None)
    assert not fingerprint.matches(
        {"status": "success", "source_fingerprint_hash": fingerprint.sha256}
    )
    assert not fp.Fingerprint("example-v2", fingerprint.inputs).matches(
        {"status": "success", **fingerprint.record_fields()}
    )
    with pytest.raises(ValueError, match="does not match"):
        fingerprint.matches(
            {
                "status": "success",
                **fingerprint.record_fields(),
                "source_fingerprint_hash": "0" * 64,
            }
        )


def test_canonical_value_types_are_part_of_fingerprint_equality():
    integer = fp.Fingerprint("example-v1", {"value": 1})
    floating = fp.Fingerprint("example-v1", {"value": 1.0})
    assert integer.sha256 != floating.sha256
    assert not integer.matches({"status": "success", **floating.record_fields()})


def test_latest_success_ignores_intervening_skip():
    publisher = Mock()
    publisher.bucket.name = "bucket"
    publisher.load_json.side_effect = [
        {
            "latest_release": {
                "run_record_path": "gs://bucket/asset/runs/2026-10-01.json"
            },
            "latest_run": {
                "run_record_path": "gs://bucket/asset/runs/2026-10-02.json",
                "status": "skipped",
            },
        },
        {"status": "success", "release_date": "2026-10-01"},
    ]
    assert (
        fp.latest_success_record(
            publisher, Mock(slug="asset", runs_prefix="asset/runs/")
        )["release_date"]
        == "2026-10-01"
    )
    assert publisher.load_json.call_args.args == ("asset/runs/2026-10-01.json",)


def eamlis_fingerprint(tmp_path, features=FEATURES, source=None):
    path = tmp_path / "source.geojson"
    path.write_text(json.dumps({"type": "FeatureCollection", "features": features}))
    translations = tmp_path / "translations.csv"
    if not translations.exists():
        translations.write_text(
            "feature_id,field,locale,source_value_hash,value,review_state,notes\n1,PA_NAME,es,hash,Mina,,\n"
        )
    state = eamlis.fingerprint_source(
        source or sample_source_state(),
        eamlis.SourceExtract(path, len(features), 0),
        translations,
    )
    return fp.Fingerprint(state.fingerprint["definition"], state.fingerprint["inputs"])


def test_eamlis_order_and_edit_service_metadata_do_not_change_content(tmp_path):
    original = eamlis_fingerprint(tmp_path)
    reordered = deepcopy(list(reversed(FEATURES)))
    reordered[0]["properties"] = dict(
        reversed(list(reordered[0]["properties"].items()))
    )
    source = replace(
        sample_source_state(),
        data_last_edit_date=987654,
        schema_last_edit_date=876543,
        fields=tuple(reversed(sample_source_state().fields)),
    )
    assert original == eamlis_fingerprint(tmp_path, reordered, source)


@pytest.mark.parametrize(
    "change", ["geometry", "attribute", "identity", "schema", "translations", "filter"]
)
def test_eamlis_all_relevant_content_changes_are_detected(tmp_path, change):
    original = eamlis_fingerprint(tmp_path)
    features, source = deepcopy(FEATURES), sample_source_state()
    if change == "geometry":
        features[0]["geometry"]["coordinates"][0] += 1
    elif change == "attribute":
        features[0]["properties"]["PA_NAME"] = "Changed mine"
    elif change == "identity":
        features[0]["properties"]["OBJECTID"] = 99
    elif change == "schema":
        source = replace(
            source,
            fields=(*source.fields, {"name": "ADDED", "type": "esriFieldTypeString"}),
        )
    elif change == "filter":
        source = replace(source, where="OBJECTID > 1")
    else:
        path = tmp_path / "translations.csv"
        path.write_text(path.read_text().replace("Mina", "Nueva mina"))
    assert original != eamlis_fingerprint(tmp_path, features, source)


def test_csv_record_and_column_order_are_not_content(tmp_path):
    path = tmp_path / "translations.csv"
    path.write_text("id,value\n1,Alpha\n2,Beta\n")
    original = fp.csv_digest(path)
    path.write_text("value,id\r\nBeta,2\r\nAlpha,1\r\n")
    assert fp.csv_digest(path) == original
    path.write_text("id,id\n1,2\n")
    with pytest.raises(ValueError):
        fp.csv_digest(path)


def test_sea_ice_filename_pixels_and_grid_are_inputs(tmp_path, monkeypatch):
    info = {
        "size": [2, 1],
        "geoTransform": [0, 1, 0, 0, 0, -1],
        "coordinateSystem": {"wkt": "EPSG:4326"},
        "bands": [{"type": "Byte", "noDataValue": 0}],
    }
    samples = b"\x03\x01"

    def gdal(command, **_kwargs):
        if command[0] == "gdalinfo":
            return info
        Path(command[-1]).write_bytes(samples)

    monkeypatch.setattr(sea_ice, "run_command", gdal)
    day = dt.date(2026, 10, 1)
    source = sea_ice.DownloadedSource(
        day,
        "https://example/ims",
        sea_ice.ims_filename_for_day(day),
        tmp_path / "ims.gz",
    )
    original = sea_ice.fingerprint_source(source, tmp_path / "source.tif", tmp_path)
    assert original == sea_ice.fingerprint_source(
        source, tmp_path / "repacked.tif", tmp_path
    )
    next_day = day + dt.timedelta(days=1)
    assert original != sea_ice.fingerprint_source(
        replace(
            source,
            filename_date=next_day,
            source_filename=sea_ice.ims_filename_for_day(next_day),
        ),
        tmp_path / "same.tif",
        tmp_path,
    )
    samples = b"\x03\x03"
    assert original != sea_ice.fingerprint_source(
        source, tmp_path / "source.tif", tmp_path
    )
    samples = b"\x03\x01"
    info["geoTransform"][0] = 1
    assert original != sea_ice.fingerprint_source(
        source, tmp_path / "source.tif", tmp_path
    )


def retained_paths(tmp_path):
    from ingestion.wdpa_monthly.translations import LOCALES

    paths = {
        role: tmp_path / role
        for role in (
            "metadata",
            "schema",
            "metadata_translations",
            *(f"metadata_{locale}" for locale in LOCALES),
        )
    }
    records = [
        {
            "feature_id": str(i),
            "identity_key": [str(i)],
            "release": "2026-10-01",
            "geometry_hash": "sha256:" + str(i) * 64,
            "provenance": {"retrieved": "today"},
            "properties": {"SITE_PID": str(i), "NAME": f"Park {i}"},
        }
        for i in (1, 2)
    ]
    for role, path in paths.items():
        if role == "schema":
            path.write_text(
                json.dumps({"fields": [{"name": "SITE_PID", "type": "String"}]})
            )
        elif role == "metadata_translations":
            path.write_text(
                "feature_id,field,locale,source_value_hash,value,review_state,notes\n1,NAME,es,hash,Parque,,\n2,NAME,es,hash,Parque dos,,\n"
            )
        else:
            path.write_bytes(
                gzip.compress(
                    "".join(json.dumps(row) + "\n" for row in records).encode()
                )
            )
    return paths


def wdpa_fingerprint(paths, tmp_path):
    return retained_fingerprint(
        paths,
        {"identity_contract": "generated-2026-v1", "identity_decisions": {}},
        [{"name": "SITE_PID", "type": "String"}],
        tmp_path,
    )


def test_wdpa_retained_content_excludes_packaging_order_release_and_retrieval(tmp_path):
    paths = retained_paths(tmp_path)
    original = wdpa_fingerprint(paths, tmp_path)
    for role, path in paths.items():
        if role in {"schema", "metadata_translations"}:
            continue
        records = [
            json.loads(line) for line in gzip.decompress(path.read_bytes()).splitlines()
        ]
        for record in records:
            record["release"] = "2026-11-01"
            record["provenance"] = {"retrieved": "tomorrow"}
            record["feature_id"] = str(int(record["feature_id"]) + 100)
        path.write_bytes(
            gzip.compress(
                "".join(
                    json.dumps(row, sort_keys=True) + "\n" for row in reversed(records)
                ).encode(),
                mtime=123,
            )
        )
    paths["metadata_translations"].write_text(
        "notes,review_state,value,source_value_hash,locale,field,feature_id\n,,Parque dos,hash,es,NAME,102\n,,Parque,hash,es,NAME,101\n"
    )
    assert original == wdpa_fingerprint(paths, tmp_path)


@pytest.mark.parametrize(
    "change", ["geometry", "attribute", "localized", "translation", "schema"]
)
def test_wdpa_retained_content_changes_are_detected(tmp_path, change):
    paths = retained_paths(tmp_path)
    original = wdpa_fingerprint(paths, tmp_path)
    if change in {"geometry", "attribute", "localized"}:
        from ingestion.wdpa_monthly.translations import LOCALES

        path = (
            paths[f"metadata_{LOCALES[0]}"]
            if change == "localized"
            else paths["metadata"]
        )
        records = [
            json.loads(line) for line in gzip.decompress(path.read_bytes()).splitlines()
        ]
        if change == "geometry":
            records[0]["geometry_hash"] = "sha256:" + "f" * 64
        else:
            records[0]["properties"]["NAME"] = "Changed name"
        path.write_bytes(
            gzip.compress("".join(json.dumps(row) + "\n" for row in records).encode())
        )
    elif change == "translation":
        paths["metadata_translations"].write_text(
            paths["metadata_translations"]
            .read_text()
            .replace("Parque dos", "Nuevo parque")
        )
    else:
        paths["schema"].write_text(
            json.dumps({"fields": [{"name": "SITE_PID", "type": "Integer"}]})
        )
    assert original != wdpa_fingerprint(paths, tmp_path)


def test_sea_ice_resumes_before_reading_source_or_comparing(monkeypatch):
    publisher = Mock()
    captured = {"status": "success", "source_version": "captured-before-interruption"}
    publisher.resume.return_value = captured
    monkeypatch.setattr(
        sea_ice.GcsPublisher, "from_runtime", Mock(return_value=publisher)
    )
    monkeypatch.setattr(sea_ice.storage, "Client", Mock())
    monkeypatch.setattr(sea_ice, "require_binary", Mock())
    lookup = Mock(side_effect=AssertionError("resume must precede new source work"))
    monkeypatch.setattr(sea_ice, "find_latest_available_source", lookup)
    assert sea_ice.run() == captured
    lookup.assert_not_called()
    publisher.latest_success_record.assert_not_called()


def test_eamlis_interrupted_skip_repairs_index_without_polling(tmp_path, monkeypatch):
    from tests.test_eamlis_monthly import (
        FakeBucket,
        FakeClient,
        fixture_download,
        fake_asset_output,
    )

    bucket = FakeBucket()
    monkeypatch.setenv("RUN_DATE", "2026-05-02")
    monkeypatch.setenv("SHARED_DATASETS_BUCKET", bucket.name)
    monkeypatch.setattr(eamlis, "require_binary", Mock())
    monkeypatch.setattr(eamlis.storage, "Client", Mock(return_value=FakeClient(bucket)))
    monkeypatch.setattr(
        eamlis, "fetch_source_state", Mock(return_value=sample_source_state())
    )
    download = Mock(side_effect=fixture_download)
    monkeypatch.setattr(eamlis, "download_source_geojson", download)
    monkeypatch.setattr(
        eamlis, "build_asset_output", Mock(return_value=fake_asset_output(tmp_path))
    )
    # Contract fixture validation is covered independently by the publisher tests.
    monkeypatch.setattr(
        eamlis.GcsPublisher, "release_metadata_contract_issue", Mock(return_value=None)
    )
    original = eamlis.run()[0]
    assert original["status"] == "success"
    published_fingerprint = original["source_fingerprint_hash"]
    original_artifacts = {
        name: list(blob.uploads)
        for name, blob in bucket.blobs.items()
        if name.startswith(eamlis.ASSET.root + "/")
        and ("/releases/" in name or "/latest/" in name)
    }
    monkeypatch.setenv("RUN_DATE", "2026-05-03")
    update = eamlis.GcsPublisher.update_latest_run_index
    monkeypatch.setattr(
        eamlis.GcsPublisher,
        "update_latest_run_index",
        Mock(side_effect=RuntimeError("lost index response")),
    )
    with pytest.raises(RuntimeError, match="lost index response"):
        eamlis.run()
    run = bucket.blob(eamlis.ASSET.run_record_object(dt.date(2026, 5, 3)))
    skipped = json.loads(run.text)
    assert skipped["status"] == "skipped"
    assert skipped["source_fingerprint_hash"] == published_fingerprint
    monkeypatch.setattr(eamlis.GcsPublisher, "update_latest_run_index", update)
    monkeypatch.setattr(
        eamlis,
        "fetch_source_state",
        Mock(side_effect=AssertionError("completed skip must not repoll")),
    )
    result = eamlis.run()[0]
    assert result["status"] == "skipped"
    index = json.loads(bucket.blob(f"_catalog/releases/{eamlis.ASSET.slug}.json").text)
    assert index["latest_release"]["date"] == "2026-05-02"
    assert index["latest_run"]["date"] == "2026-05-03"
    assert index["latest_run"]["run_record_path"].endswith("runs/2026-05-03.json")
    assert {
        name: blob.uploads
        for name, blob in bucket.blobs.items()
        if name in original_artifacts
    } == original_artifacts
    assert len(run.uploads) == 1
    assert download.call_count == 2


def test_eamlis_partial_release_cannot_be_converted_to_a_skip(monkeypatch):
    publisher = Mock(spec=eamlis.GcsPublisher)
    publisher.load_json.return_value = None
    publisher.assert_no_partial_release.side_effect = RuntimeError("partial release")
    monkeypatch.setattr(eamlis, "GcsPublisher", Mock(return_value=publisher))
    monkeypatch.setattr(eamlis.storage, "Client", Mock())
    monkeypatch.setattr(eamlis, "require_binary", Mock())
    source = Mock(
        side_effect=AssertionError("incomplete publication must fail before polling")
    )
    monkeypatch.setattr(eamlis, "fetch_source_state", source)
    with pytest.raises(RuntimeError, match="partial release"):
        eamlis.run()
    source.assert_not_called()
