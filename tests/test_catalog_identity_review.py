"""Private exact-case HTTP transport and frozen notification enrichment.

The tiny JSON fixture was produced by the real GDAL polygon capture/completion
test from PR #371. All names, IDs, source URLs and producer identities are
synthetic. It intentionally retains shared footprints and a distinct key owner.
"""

import base64
import copy
import hashlib
import json
from pathlib import Path
from unittest.mock import Mock
from urllib.parse import urlencode, urlsplit

import pytest
from google.api_core.exceptions import Forbidden, NotFound, PreconditionFailed

from ingestion.common import decision_events as wire, publication as p
from ingestion.wdpa_monthly import identity_review as evidence
from services.catalog_viewer import identity_review as api, run as viewer
from services.monitoring_slack import decisions, run as relay
from test_monitoring_slack import Bucket

FIXTURE = Path(__file__).parent / "fixtures/wdpa-identity-review.json"
ORIGIN = "https://catalog-viewer-example.a.run.app"
HEADERS = {
    "X-Goog-Authenticated-User-Email": "accounts.google.com:maintainer@skytruth.org"
}
IDENTITY = {"team": "T12345678", "user": "U12345678", "channel": "C12345678"}


@pytest.fixture
def example():
    raw = FIXTURE.read_bytes()
    bundle = evidence.validate_bundle(json.loads(raw))
    source = {
        "project": wire.PROJECT,
        "job": "wdpa-monthly",
        "execution": bundle["producer"]["execution"],
        "asset_slug": bundle["capture"]["asset_slug"],
        "release": bundle["capture"]["release"],
    }
    ref = {
        "uri": f"gs://{evidence.BUCKET}/_scratch/wdpa-builds/{source['execution']}/{source['asset_slug']}/identity-review.json",
        "generation": 1001,
        "size": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }
    ready = wire.event(
        {
            "schema": wire.REVIEW_SCHEMA,
            **source,
            "review_bundle": ref,
            "cases": [item["ambiguity"] for item in bundle["capture"]["cases"]],
        }
    )
    url = wire.review_url(ready, ORIGIN, bundle["capture"]["cases"][0]["ambiguity"])
    reader = Mock()
    reader.read.return_value = raw
    return bundle, source, ref, ready, url, reader


def request(example, *, path=None, headers=HEADERS, method="GET"):
    reader = example[-1]
    # Exercise the actual viewer route, including the rule that evidence remains
    # private when ordinary feature lookup is configured as public.
    return viewer.handle_request(
        method,
        path or "/api/identity-review?" + urlsplit(example[4]).query,
        headers,
        object_store=None,
        catalog_cache=None,
        signer=None,
        identity_review_reader=reader,
        feature_require_iap=False,
    )


def test_alert_link_selects_verified_case_and_keeps_shared_footprints_separate(example):
    bundle, source, ref, event, url, reader = example
    response = request(example)
    assert response.status == 200 and response.headers["Cache-Control"] == "no-store"
    result = json.loads(response.body)
    assert result["case_id"] == bundle["capture"]["cases"][0]["case_id"]
    reader.read.assert_called_once_with(ref)
    assert len(result["cases"]) == 2
    assert {row["feature_id"] for row in result["previous"]} == {"1", "2"}
    assert result["previous"][0]["geometry"] == result["previous"][1]["geometry"]
    assert result["previous"][0]["properties"] != result["previous"][1]["properties"]
    assert result["choices"] == [
        {"action": "reuse_previous_feature_id", "feature_ids": ["2"]},
        {"action": "assign_new_feature_id", "feature_ids": []},
    ]
    assert result["baseline"]["metadata"]["generation"] == str(
        bundle["baseline"]["metadata"]["generation"]
    )
    assert request(example, method="HEAD").body == b""


@pytest.mark.parametrize(
    "headers,status",
    [({}, 401), ({"X-Goog-Authenticated-User-Email": "outsider@example.org"}, 403)],
)
def test_auth_precedes_even_query_parsing_and_storage(example, headers, status):
    response = request(
        example, path="/api/identity-review?uri=gs://foreign/secret", headers=headers
    )
    assert response.status == status and response.headers["Cache-Control"] == "no-store"
    example[-1].read.assert_not_called()


@pytest.mark.parametrize(
    "change",
    [
        "missing",
        "duplicate",
        "extra",
        "uri",
        "slug",
        "execution",
        "date",
        "generation",
        "size",
        "checksum",
        "case",
    ],
)
def test_malformed_or_unmanaged_handles_do_not_read_storage(example, change):
    params = dict(api.parse_qs(urlsplit(example[4]).query, keep_blank_values=True))
    params = {key: items[0] for key, items in params.items()}
    if change == "missing":
        params.pop("review_case")
    elif change == "duplicate":
        params["review_case"] = [params["review_case"], params["review_case"]]
    elif change == "extra":
        params["review_other"] = "1"
    elif change == "uri":
        params["uri"] = "gs://foreign/secret"
    else:
        key, value = {
            "slug": ("review_asset", "ims-sea-ice-extent"),
            "execution": ("review_execution", "../../elsewhere"),
            "date": ("review_release", "2026-02-30"),
            "generation": ("review_generation", "0"),
            "size": ("review_size", str(wire.REVIEW_MAX_BYTES + 1)),
            "checksum": ("review_sha256", "wrong"),
            "case": ("review_case", "wrong"),
        }[change]
        params[key] = value
    assert (
        request(
            example, path="/api/identity-review?" + urlencode(params, doseq=True)
        ).status
        == 400
    )
    example[-1].read.assert_not_called()


@pytest.mark.parametrize(
    "error,status",
    [
        (NotFound("absent"), 404),
        (PreconditionFailed("changed"), 409),
        (Forbidden("private"), 503),
    ],
)
def test_missing_changed_and_inaccessible_evidence_are_explicit(example, error, status):
    example[-1].read.side_effect = error
    response = request(example)
    assert response.status == status and response.headers["Cache-Control"] == "no-store"
    assert example[-1].read.call_count == 1


@pytest.mark.parametrize(
    "key,value,status",
    [
        ("review_asset", "wdpa-terrestrial", 409),
        ("review_release", "2026-12-01", 409),
        ("review_execution", "wdpa-monthly-fffff", 409),
        ("review_case", "f" * 64, 404),
        ("review_sha256", "f" * 64, 409),
        ("review_size", "1", 409),
    ],
)
def test_valid_but_foreign_case_handles_never_switch_to_other_evidence(
    example, key, value, status
):
    params = {
        key: values[0]
        for key, values in api.parse_qs(urlsplit(example[4]).query).items()
    }
    params[key] = value
    assert (
        request(example, path="/api/identity-review?" + urlencode(params)).status
        == status
    )
    assert example[-1].read.call_count == 1


def test_corrupt_evidence_and_response_budget_fail_without_partial_output(
    example, monkeypatch
):
    raw = example[-1].read.return_value
    example[-1].read.return_value = raw[:-1] + b"!"
    assert request(example).status == 409
    example[-1].read.return_value = raw
    monkeypatch.setattr(api, "MAX_RESPONSE_BYTES", 1)
    assert request(example).status == 413
    assert request(example, method="POST").status == 405


def test_byte_verified_but_invalid_bundle_is_rejected(example):
    bundle, source, ref, event, url, reader = example
    bundle["schema"] = "unsupported"
    raw = p.canonical(bundle)
    ref = {**ref, "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
    event = wire.event({**event, "review_bundle": ref})
    changed = (*example[:4], wire.review_url(event, ORIGIN, event["cases"][0]), reader)
    reader.read.return_value = raw
    assert request(changed).status == 502


def test_gcs_reader_uses_only_exact_generation_and_bounded_range(example):
    ref = example[2]
    client = Mock()
    client.bucket.return_value.blob.return_value.download_as_bytes.return_value = (
        b"bytes"
    )
    assert api.ReviewReader(client).read(ref) == b"bytes"
    client.bucket.assert_called_once_with(evidence.BUCKET)
    client.bucket.return_value.blob.assert_called_once_with(
        ref["uri"].split(evidence.BUCKET + "/", 1)[1], generation=1001
    )
    client.bucket.return_value.blob.return_value.download_as_bytes.assert_called_once_with(
        start=0, end=ref["size"], if_generation_match=1001, timeout=30
    )


def test_same_source_key_owner_and_samples_limit_available_choices(example):
    bundle, source, ref, event, url, reader = example
    owned = bundle["capture"]["cases"][1]
    params = wire.review_parameters(source, ref, owned["case_id"])
    result = json.loads(
        request(example, path="/api/identity-review?" + urlencode(params)).body
    )
    assert result["previous_feature_id_for_key"] == "3"
    assert {row["feature_id"] for row in result["previous"]} == {"1", "2", "3"}
    assert [row["action"] for row in result["choices"]] == [
        "keep_previous_key_mapping",
        "assign_new_feature_id",
    ]
    bundle["sample_fraction"] = 0.001
    assert evidence.action_options(bundle, bundle["capture"]["cases"][0]) == [
        {"action": "assign_new_feature_id", "feature_ids": []}
    ]


def test_field_display_preserves_json_types_presence_and_object_key_order():
    rows = evidence.property_differences(
        {
            "flag": True,
            "number": 1,
            "nested": [1],
            "object": {"a": 1, "b": 2},
            "missing": None,
        },
        {
            "flag": 1,
            "number": 1.0,
            "nested": [1.0],
            "object": {"b": 2, "a": 1},
            "added": None,
        },
    )
    by_field = {row["field"]: row for row in rows}
    assert all(
        by_field[key]["changed"]
        for key in ("flag", "number", "nested", "missing", "added")
    )
    assert not by_field["object"]["changed"]
    assert by_field["number"]["incoming"]["display"] == "1.0"
    assert by_field["flag"]["previous"]["display"] == "true"
    assert by_field["missing"]["incoming"] == {"present": False, "display": "absent"}


def test_viewer_read_limit_is_checked_before_storage(example, monkeypatch):
    monkeypatch.setattr(api, "MAX_BUNDLE_BYTES", example[2]["size"] - 1)
    assert request(example).status == 413
    example[-1].read.assert_not_called()


def test_serialized_budget_stops_expansion_and_matches_actual_wire_bytes(
    example, monkeypatch
):
    expected = request(example)
    monkeypatch.setattr(api, "MAX_RESPONSE_BYTES", len(expected.body))
    assert request(example).status == 200
    monkeypatch.setattr(api, "MAX_RESPONSE_BYTES", len(expected.body) - 1)
    assert request(example).status == 413
    monkeypatch.setattr(api, "MAX_RESPONSE_BYTES", 1)
    differences = Mock(wraps=evidence.property_differences)
    monkeypatch.setattr(evidence, "property_differences", differences)
    assert request(example).status == 413
    differences.assert_not_called()


def test_busy_instance_rejects_another_case_and_releases_slot_after_error(example):
    raw = example[-1].read.return_value
    nested = []

    def read(ref):
        nested.append(request(example).status)
        raise NotFound("synthetic read failed")

    example[-1].read.side_effect = read
    assert request(example).status == 404
    assert nested == [429]
    example[-1].read.side_effect = None
    example[-1].read.return_value = raw
    assert request(example).status == 200


@pytest.mark.parametrize("first_ready", [False, True])
def test_real_pubsub_decode_and_persisted_retries_enrich_one_parent(
    example, monkeypatch, first_ready
):
    ready = example[3]
    original = wire.event(
        {key: value for key, value in ready.items() if key != "review_bundle"}
        | {"schema": wire.SCHEMA}
    )
    envelope = {
        "subscription": wire.SUBSCRIPTION,
        "message": {"data": base64.b64encode(wire.canonical(ready)).decode()},
    }
    decoded = relay.event(json.dumps(envelope).encode())
    assert decoded == ready
    monkeypatch.setenv("CATALOG_VIEWER_URL", ORIGIN)
    store = relay.Store(Bucket())
    slack = Mock()
    slack.post.return_value = "1234567890.000001"
    if not first_ready:
        decisions.deliver(original, store, slack, IDENTITY)
    decisions.deliver(decoded, store, slack, IDENTITY)
    # Recreate the store consumer and change deployment configuration: the
    # originally persisted origin and pinned bundle survive late events/retries.
    store = relay.Store(store.bucket)
    monkeypatch.setenv("CATALOG_VIEWER_URL", "https://catalog-viewer-other.a.run.app")
    for item in (original, decoded, original, decoded):
        decisions.deliver(item, store, slack, IDENTITY)
    slack.post.assert_called_once()
    # Quiet updates can repeat to repair a competing writer's stale Slack
    # update. The invariant is one parent with the original pinned evidence.
    assert slack.update.called
    assert all(call.args[0] == slack.post.return_value for call in slack.update.call_args_list)
    assert example[4] in slack.update.call_args.args[1]["text"]
    value, _ = decisions.load(store, wire.event_key(ready), IDENTITY)
    assert value["event"] == ready and value["catalog_url"] == ORIGIN
    assert value["schema"] == decisions.EXECUTION_EVENT_SCHEMA
    assert (
        example[4]
        in decisions.primary(value["event"], catalog_url=value["catalog_url"])["text"]
    )
    changed = copy.deepcopy(ready)
    changed["review_bundle"]["generation"] += 1
    with pytest.raises(ValueError, match="reference changed"):
        decisions.deliver(changed, store, slack, IDENTITY)


@pytest.mark.parametrize(
    "origin",
    [
        "http://catalog-viewer-example.a.run.app",
        "https://evil.example",
        ORIGIN + "/redirect",
        ORIGIN + "?next=elsewhere",
    ],
)
def test_review_link_cannot_replace_the_authenticated_origin(example, origin):
    with pytest.raises(ValueError):
        wire.review_url(example[3], origin, example[3]["cases"][0])


def test_failed_parent_enrichment_finishes_on_late_original_event(example, monkeypatch):
    ready = example[3]
    original = wire.event(
        {key: value for key, value in ready.items() if key != "review_bundle"}
        | {"schema": wire.SCHEMA}
    )
    monkeypatch.setenv("CATALOG_VIEWER_URL", ORIGIN)
    store = relay.Store(Bucket())
    slack = Mock()
    slack.post.return_value = "1234567890.000001"
    decisions.deliver(original, store, slack, IDENTITY)
    slack.update.side_effect = RuntimeError("synthetic Slack boundary failure")
    with pytest.raises(RuntimeError):
        decisions.deliver(ready, store, slack, IDENTITY)
    saved, _ = decisions.load(store, wire.event_key(ready), IDENTITY)
    assert saved["event"] == ready and saved["catalog_url"] == ORIGIN
    slack.update.side_effect = None
    before_retry = slack.update.call_count
    decisions.deliver(original, store, slack, IDENTITY)
    slack.post.assert_called_once()
    assert slack.update.call_count == before_retry + 1
    assert slack.update.call_args.args[0] == slack.post.return_value
    assert example[4] in slack.update.call_args.args[1]["text"]
