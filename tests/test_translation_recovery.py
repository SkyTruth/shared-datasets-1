"""Offline canonical bytes, HTTP construction and immutable recovery proofs."""
from __future__ import annotations

import base64
import copy
from datetime import datetime, timezone
import hashlib
import json

from google.api_core import exceptions as api_errors
import google_crc32c
import pytest
from requests import exceptions as request_errors

from ingestion.common import feature_metadata
from scripts import operational_retry as retry, release_feature_model as model
from scripts import translation_recovery as recovery

SLUG = "wdpa-marine"
ROOT = "100-geographic-reference/130-protected-areas/wdpa-marine"
RELEASE = "2026-10-21"
URI = f"gs://{recovery.BUCKET}/{ROOT}/releases/{RELEASE}/{SLUG}.manifest.json"
CREATED = "2026-10-21T12:01:00.123456789Z"
FAILED = "2026-10-21T12:00:00Z"


def manifest(*, total=100, debt=0):
    value = feature_metadata.manifest_payload(asset_slug=SLUG, release=RELEASE, bucket_name=recovery.BUCKET, asset_root=ROOT,
        sha256_by_role={role: "a" * 64 for role in ("fgb", "pmtiles", "metadata", "schema")},
        schema=feature_metadata.schema_from_records(asset_slug=SLUG, release=RELEASE, records=[]), source_inputs=[],
        identity=model.build_identity_metadata(strategy="source_field", source_fields=["SITE_PID"]), feature_count=total)
    for artifact in value["artifacts"]:
        if artifact["role"] != "manifest":
            artifact.update(generation=10, size=1)
    locales = recovery.registered_assets()[SLUG]["locales"]
    value["translations"] = {"schema_version": 1, "locales": {locale: {
        "translatable_values": total, "current": total - debt, "stale": 0, "missing": debt,
        "orphan": 0, "removed_fields": 0, "coverage": (total - debt) / total if total else None,
        "review_states": {"human_reviewed": total - debt}} for locale in locales}}
    for role, suffix in [("translations", ".metadata-translations.csv"), *[(f"metadata-{locale}", f".metadata.{locale}.ndjson.gz") for locale in locales]]:
        value["artifacts"].append({"role": role, "format": "metadata", "path": URI.removesuffix(".manifest.json") + suffix, "generation": 11, "size": 1, "sha256": "b" * 64})
    return value


def encoded(value):
    return json.dumps(value, ensure_ascii=False).encode()


def metadata(body, *, generation=20, created=CREATED):
    return {"bucket": recovery.BUCKET, "name": URI.removeprefix(f"gs://{recovery.BUCKET}/"), "generation": str(generation),
        "size": str(len(body)), "timeCreated": created, "updated": created,
        "crc32c": base64.b64encode(google_crc32c.value(body).to_bytes(4, "big")).decode()}


class Response:
    def __init__(self, value=None, *, status=200, body=None, chunks=None):
        self.value, self.status_code, self.body, self.chunks = value, status, body, chunks
        self.closed = False

    def json(self):
        return self.value

    def iter_content(self, *, chunk_size):
        assert chunk_size == 65536
        for chunk in self.chunks if self.chunks is not None else [self.body]:
            if isinstance(chunk, Exception):
                raise chunk
            yield chunk

    def close(self):
        self.closed = True


class Session:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def session_for(value=None, *, body=None, generation=20, created=CREATED):
    body = encoded(value if value is not None else manifest()) if body is None else body
    return Session([Response(metadata(body, generation=generation, created=created)), Response(body=body)])


@pytest.fixture
def clock(monkeypatch):
    elapsed, sleeps = [0.0], []
    monkeypatch.setattr(retry, "monotonic", lambda: elapsed[0])
    def sleep(seconds):
        sleeps.append(seconds)
        elapsed[0] += seconds
    monkeypatch.setattr(retry, "sleep", sleep)
    return elapsed, sleeps


def test_current_metadata_then_pinned_bytes_actual_http_and_persisted_serialization():
    session = session_for()
    responses = list(session.responses)
    proof = recovery.Reader(session).read(URI)
    saved = json.loads(json.dumps(proof))
    assert recovery.validate(saved) == proof
    assert recovery.complete(saved)
    assert recovery.manifest(saved)["translations"]["locales"] == manifest()["translations"]["locales"]
    assert proof["sha256"] == hashlib.sha256(encoded(manifest())).hexdigest()
    assert len(session.calls) == 2
    method, url, request = session.calls[0]
    assert method == "GET" and url == f"https://storage.googleapis.com/storage/v1/b/{recovery.BUCKET}/o/{ROOT.replace('/', '%2F')}%2Freleases%2F{RELEASE}%2F{SLUG}.manifest.json"
    assert request == {"params": {"fields": recovery.FIELDS}, "timeout": 30}
    assert session.calls[1] == ("GET", url, {"params": {"generation": "20", "alt": "media"}, "timeout": 30, "stream": True})
    assert all(response.closed for response in responses)


@pytest.mark.parametrize("total,debt,complete", [(0, 0, True), (100, 0, True), (101, 1, True), (100, 1, False), (99, 1, False)])
def test_shared_threshold_valid_debt_is_evidence_not_recovery(total, debt, complete):
    proof = recovery.read(session_for(manifest(total=total, debt=debt)), URI)
    assert recovery.complete(proof) is complete
    assert recovery.covers(proof, URI, 19, FAILED) is complete


def test_exact_newer_publication_boundary_with_precision():
    proof = recovery.read(session_for(), URI)
    assert recovery.covers(proof, URI, None, FAILED)
    assert recovery.covers(proof, URI, 19, "2026-10-21T12:01:00.123456788Z")
    assert not recovery.covers(proof, URI, 20, FAILED)
    assert recovery.covers(proof, URI, 21, FAILED)
    assert not recovery.covers(proof, URI, 19, CREATED)
    assert not recovery.covers(proof, URI, 19, "2026-10-21T12:01:00.123456790Z")
    assert not recovery.covers(proof, URI.replace(RELEASE, "2026-10-20"), 19, FAILED)
    assert recovery.covers(proof, URI, 19, datetime(2026, 10, 21, 12, tzinfo=timezone.utc))


def test_distinct_lower_numbered_current_generation_can_establish_newer_publication():
    original = recovery.read(session_for(generation=20, created=FAILED), URI)
    newer = recovery.read(session_for(generation=7), URI)
    assert recovery.follows(original, newer)
    assert recovery.covers(newer, URI, 20, FAILED)
    assert not recovery.follows(newer, original)
    assert not recovery.covers(original, URI, 7, CREATED)
    same_time = recovery.read(session_for(generation=21, created=FAILED), URI)
    assert not recovery.follows(original, same_time)
    metadata_only = copy.deepcopy(original)
    metadata_only["updated_at"] = CREATED
    assert not recovery.follows(original, metadata_only)


def test_creation_order_keeps_nanoseconds_for_distinct_versions():
    first = recovery.read(session_for(generation=20), URI)
    later = recovery.read(session_for(generation=7, created="2026-10-21T12:01:00.123456790Z"), URI)
    assert recovery.follows(first, later)
    assert not recovery.follows(later, first)


@pytest.mark.parametrize("uri", [URI.replace(recovery.BUCKET, "other"), URI.replace("/releases/", "/latest/"), URI.replace(".manifest.json", ".metadata.ndjson.gz"), URI.replace(RELEASE, "2026-02-30"), URI.replace(SLUG, "unregistered"), URI + "/nested", URI + "?generation=20", URI.replace("/releases/", "/releases/../")])
def test_unregistered_or_non_manifest_reads_refused_before_http(uri):
    session = Session([])
    with pytest.raises(ValueError):
        recovery.read(session, uri)
    assert not session.calls


def test_all_registered_maintained_assets_have_exact_canonical_manifest_scope():
    assets = recovery.registered_assets()
    assert set(assets) == {"wdpa-marine", "wdpa-terrestrial", "iho-world-seas", "marine-regions-eez", "petrodata", "eamlis-abandoned-mine-land-inventory"}
    for slug, asset in assets.items():
        assert recovery.identity(f"gs://{recovery.BUCKET}/{asset['root']}/releases/{RELEASE}/{slug}.manifest.json")[:2] == (slug, RELEASE)


def test_future_metadata_never_greens_before_authoritative_observation_clock():
    proof = recovery.read(session_for(), URI)
    assert not recovery.observed_by(proof, "2026-10-21T12:01:00.123456788Z")
    assert recovery.observed_by(proof, CREATED)
    proof["updated_at"] = "2026-10-21T12:02:00Z"
    assert not recovery.observed_by(proof, CREATED)
    assert recovery.observed_by(proof, "2026-10-21T12:02:00Z")


@pytest.mark.parametrize("mutate", [
    lambda value: value.pop("translations"),
    lambda value: value["translations"]["locales"].pop("es"),
    lambda value: value["translations"]["locales"].update(de={"translatable_values": 0}),
    lambda value: value["translations"]["locales"]["es"].update(current=99),
    lambda value: value["translations"]["locales"]["es"].update(missing=-1),
    lambda value: value["translations"]["locales"]["es"].update(coverage=True),
    lambda value: value["translations"]["locales"]["es"].update(review_states={"human_reviewed": 99}),
    lambda value: value["artifacts"].pop(),
    lambda value: value["artifacts"].append(copy.deepcopy(value["artifacts"][-1])),
    lambda value: value["artifacts"][0].update(generation=True),
    lambda value: value["artifacts"][0].update(generation=0),
    lambda value: value["artifacts"][0].pop("generation"),
    lambda value: value["artifacts"][0].update(path=URI.replace(RELEASE, "2026-10-20")),
    lambda value: value["artifacts"][0].update(path=URI + "/nested"),
    lambda value: value.update(asset_slug="wrong"),
    lambda value: value.update(release="2026-10-20"),
])
def test_malformed_or_incomplete_canonical_inventory_fails_without_retry(mutate, clock):
    value = manifest()
    mutate(value)
    session = session_for(value)
    with pytest.raises(ValueError):
        recovery.read(session, URI)
    assert len(session.calls) == 2 and not clock[1]


@pytest.mark.parametrize("body", [b'{"schema_version":2,"schema_version":2}', b'{"n":NaN}', b'{"n":Infinity}', b"[]", b"invalid JSON"])
def test_invalid_json_cannot_be_a_recovery_proof(body, clock):
    with pytest.raises(ValueError):
        recovery.read(session_for(body=body), URI)
    assert not clock[1]


@pytest.mark.parametrize("field,value", [("bucket", "other"), ("name", "other"), ("generation", "020"), ("generation", True), ("generation", "0"), ("size", str(recovery.MAX_BYTES + 1)), ("timeCreated", "invalid"), ("updated", FAILED), ("crc32c", "AAAAAA==")])
def test_wrong_metadata_or_bytes_rejected(field, value, clock):
    session = session_for()
    session.responses[0].value[field] = value
    with pytest.raises(ValueError):
        recovery.read(session, URI)
    assert len(session.calls) <= 2 and not clock[1]


def test_wrong_body_same_size_rejected_by_provider_checksum():
    session = session_for()
    session.responses[1].body = session.responses[1].body.replace(b'"current": 100', b'"current": 101', 1)
    with pytest.raises(ValueError, match="CRC32C"):
        recovery.read(session, URI)


def test_bounded_stream_stops_at_metadata_size_and_closes():
    session = session_for()
    response = session.responses[1]
    response.chunks = [b"x" * recovery.MAX_BYTES, b"unreachable"]
    with pytest.raises(ValueError, match="exceed metadata size"):
        recovery.read(session, URI)
    assert response.closed


def test_partial_stream_transport_failure_discards_entire_snapshot(clock):
    body = encoded(manifest())
    failed = Response(chunks=[b'{"partial":', request_errors.ChunkedEncodingError("lost connection")])
    session = Session([Response(metadata(body)), failed, Response(metadata(body, generation=21)), Response(body=body)])
    proof = recovery.read(session, URI)
    assert proof["generation"] == 21 and recovery.complete(proof)
    assert clock[1] == [5] and failed.closed
    assert [request[2]["params"] for request in session.calls][::2] == [{"fields": recovery.FIELDS}] * 2


@pytest.mark.parametrize("status", [401, 403, 404, 412, 501])
def test_non_transport_errors_are_fatal(status, clock):
    session = Session([Response(status=status)])
    with pytest.raises(api_errors.GoogleAPICallError):
        recovery.read(session, URI)
    assert len(session.calls) == 1 and not clock[1]


def test_transport_retry_uses_one_shared_absolute_budget(clock):
    session = Session([Response(status=503) for _ in range(8)])
    with retry.operation(profile=retry.UNATTENDED):
        with pytest.raises(api_errors.ServiceUnavailable):
            recovery.read(session, URI)
        with pytest.raises(api_errors.ServiceUnavailable):
            recovery.read(session, URI)
    assert len(session.calls) == 8
    assert clock[1] == [5, 15, 40, 120, 240, 480, 840]


def test_reverification_requires_current_same_generation_even_if_old_bytes_complete():
    proof = recovery.read(session_for(), URI)
    current = session_for(manifest(debt=1), generation=21)
    with pytest.raises(ValueError, match="current.*generation"):
        recovery.reverify(current, proof)
    assert len(current.calls) == 1
    assert "generation" not in current.calls[0][2]["params"]
    assert recovery.reverify(session_for(), proof) == proof


def test_same_generation_mismatched_saved_proof_is_rejected():
    proof = recovery.read(session_for(), URI)
    current = session_for(manifest(total=101, debt=1))
    with pytest.raises(ValueError, match="differs from saved"):
        recovery.reverify(current, proof)


def test_same_generation_cosmetic_metadata_advance_preserves_immutable_proof():
    proof = recovery.read(session_for(), URI)
    current = session_for()
    current.responses[0].value["updated"] = "2026-10-21T12:02:00Z"
    verified = recovery.reverify(current, proof)
    assert verified["updated_at"] == "2026-10-21T12:02:00Z"
    assert recovery.same_generation(proof, verified)
    assert not recovery.same_generation(verified, proof)


@pytest.mark.parametrize("field", ["uri", "generation", "created_at", "size", "crc32c", "sha256", "body_base64", "asset_slug", "release"])
def test_cosmetic_metadata_cannot_hide_mutated_identity_or_bytes(field):
    original = recovery.read(session_for(), URI)
    changed = recovery.read(session_for(manifest(total=101, debt=1), generation=21, created="2026-10-21T12:02:00Z"), URI)
    # A complete second canonical generation is valid evidence, but cannot be
    # substituted for the exact immutable generation already prepared.
    if original[field] != changed[field]:
        assert not recovery.same_generation(original, changed)
    forged = copy.deepcopy(original)
    forged[field] = changed[field] if original[field] != changed[field] else None
    try:
        matches = recovery.same_generation(original, forged)
    except ValueError:
        matches = False
    assert not matches


def test_incomplete_local_snapshot_is_not_accepted_or_retried_as_transport(clock):
    session = session_for()
    response = session.responses[1]
    response.body = response.body[:-1]
    with pytest.raises(ValueError, match="incomplete"):
        recovery.read(session, URI)
    assert response.closed and not clock[1]


@pytest.mark.parametrize("mutate", [lambda proof: proof.update(generation=True), lambda proof: proof.update(sha256="0" * 64), lambda proof: proof.update(size=recovery.MAX_BYTES + 1), lambda proof: proof.update(body_base64="A" * (4 * ((recovery.MAX_BYTES + 2) // 3) + 4)), lambda proof: proof.update(legacy_delivery_state="delivered")])
def test_saved_proof_tampering_and_decode_bombs_fail(mutate):
    proof = recovery.read(session_for(), URI)
    mutate(proof)
    with pytest.raises(ValueError):
        recovery.validate(proof)
