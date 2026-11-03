"""Authenticated, bounded reads of an exact WDPA identity-evidence case."""

from __future__ import annotations

import hashlib
import re
from contextlib import contextmanager
from threading import BoundedSemaphore
from urllib.parse import parse_qs, urlsplit

from google.api_core.exceptions import (
    Forbidden,
    GoogleAPICallError,
    NotFound,
    PreconditionFailed,
)
from google.auth.exceptions import DefaultCredentialsError

from ingestion.common import decision_events as wire, publication as p
from ingestion.wdpa_monthly import identity_review as evidence
from scripts import release_feature_model as model
from services.http_base import (
    authenticated_user_email,
    email_domain_allowed,
    json_response,
    Response,
    api_headers,
)

MAX_BUNDLE_BYTES = 16 * 1024**2
MAX_RESPONSE_BYTES = 16 * 1024**2
_case_read_slot = BoundedSemaphore(1)


class ReviewError(ValueError):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


@contextmanager
def case_read_slot():
    if not _case_read_slot.acquire(blocking=False):
        raise ReviewError(429, "Another identity case is loading; try again shortly")
    try:
        yield
    finally:
        _case_read_slot.release()


def parse_reference(path):
    params = parse_qs(urlsplit(path).query, keep_blank_values=True)
    wire.require(
        set(params) == set(wire.REVIEW_PARAMS)
        and all(len(values) == 1 for values in params.values()),
        "Review link is incomplete or contains unknown or duplicate parameters",
    )
    values = {key: items[0] for key, items in params.items()}
    case_id = values["review_case"]
    wire.require(re.fullmatch(r"[0-9a-f]{64}", case_id), "Invalid review case")
    for key in ("review_generation", "review_size"):
        wire.require(
            re.fullmatch(r"[1-9][0-9]{0,19}", values[key]),
            "Invalid review generation or size",
        )
    source = wire.context(
        {
            "project": wire.PROJECT,
            "job": "wdpa-monthly",
            "execution": values["review_execution"],
            "asset_slug": values["review_asset"],
            "release": values["review_release"],
        }
    )
    ref = wire.review_reference(
        {
            "uri": f"gs://{evidence.BUCKET}/_scratch/wdpa-builds/{source['execution']}/{source['asset_slug']}/identity-review.json",
            "generation": int(values["review_generation"]),
            "size": int(values["review_size"]),
            "sha256": values["review_sha256"],
        },
        source,
    )
    return source, ref, case_id


class ReviewReader:
    def __init__(self, client=None):
        self.client = client

    def read(self, ref):
        if self.client is None:
            from google.cloud import storage

            self.client = storage.Client()
        name = ref["uri"].removeprefix(f"gs://{evidence.BUCKET}/")
        return (
            self.client.bucket(evidence.BUCKET)
            .blob(name, generation=ref["generation"])
            .download_as_bytes(
                start=0,
                end=ref["size"],
                if_generation_match=ref["generation"],
                timeout=30,
            )
        )


def case_payload(raw, source, ref, case_id):
    if len(raw) != ref["size"] or hashlib.sha256(raw).hexdigest() != ref["sha256"]:
        raise ReviewError(409, "Captured evidence changed or failed its checksum")
    bundle = evidence.validate_bundle(p.strict_json(raw))
    captured = bundle["capture"]
    if (
        captured["asset_slug"],
        captured["release"],
        bundle["producer"]["execution"],
    ) != (source["asset_slug"], source["release"], source["execution"]):
        raise ReviewError(
            409, "Evidence belongs to a different asset, release or execution"
        )
    item = next(
        (item for item in captured["cases"] if item["case_id"] == case_id), None
    )
    if item is None:
        raise ReviewError(404, "Case is absent from this captured evidence")
    ambiguity = item["ambiguity"]
    ids = set(
        ambiguity["matching_geometry_feature_ids"]
        + ambiguity["matching_properties_feature_ids"]
    )
    if item["previous_feature_id_for_key"] is not None:
        ids.add(item["previous_feature_id_for_key"])
    baseline = {
        **bundle["baseline"],
        **{
            role: {
                **bundle["baseline"][role],
                "generation": str(bundle["baseline"][role]["generation"]),
            }
            for role in ("metadata", "fgb")
        },
    }
    result = {
        "schema": "identity-review-case-v1",
        **source,
        "case_id": case_id,
        "review_bundle": {**ref, "generation": str(ref["generation"])},
        "baseline": baseline,
        "incoming": item["incoming"],
        "ambiguity": ambiguity,
        "previous_feature_id_for_key": item["previous_feature_id_for_key"],
        "previous": [],
        "choices": evidence.action_options(bundle, item),
        "cases": [
            {
                "case_id": row["case_id"],
                "source_key": row["ambiguity"]["new_identity_key"],
            }
            for row in captured["cases"]
        ],
        "sample_fraction": bundle["sample_fraction"],
        "source": bundle["source"],
        "producer": bundle["producer"],
        "native_versions": bundle["native_versions"],
    }
    remaining = MAX_RESPONSE_BYTES

    def charge(size):
        nonlocal remaining
        remaining -= size
        if remaining < 0:
            raise ReviewError(
                413,
                "This case exceeds the viewer size limit; use the offline review report",
            )

    charge(len(p.canonical(result)) + 1)
    for feature_id in sorted(ids):
        before = bundle["previous_records"][feature_id]
        row = {
            **before,
            "fields": evidence.property_differences(
                before["properties"], item["incoming"]["properties"]
            ),
            "key_present_in_input": feature_id in captured["previous_keys_present"],
            "geometry_matches": feature_id
            in ambiguity["matching_geometry_feature_ids"],
            "properties_match": feature_id
            in ambiguity["matching_properties_feature_ids"],
        }
        charge(len(p.canonical(row)) + bool(result["previous"]))
        result["previous"].append(row)
    return result


def handle(method, path, headers, *, reader, domains):
    email = authenticated_user_email(headers)
    if not email:
        return json_response(
            401,
            {
                "error": "SkyTruth sign-in is required to read captured identity evidence"
            },
        )
    if not email_domain_allowed(email, domains):
        return json_response(403, {"error": "SkyTruth catalog access is required"})
    if method not in {"GET", "HEAD"}:
        return json_response(
            405, {"error": "Use GET or HEAD for read-only identity evidence"}
        )
    try:
        source, ref, case_id = parse_reference(path)
    except (ValueError, KeyError, TypeError) as error:
        return json_response(400, {"error": str(error)})
    if reader is None:
        return json_response(
            503,
            {
                "error": "Identity evidence is unavailable in this viewer; use the authenticated production catalog"
            },
        )
    if ref["size"] > MAX_BUNDLE_BYTES:
        return json_response(
            413,
            {
                "error": "This bundle exceeds the viewer read limit; use the offline review report"
            },
        )
    try:
        with case_read_slot():
            raw = reader.read(ref)
            result = case_payload(raw, source, ref, case_id)
            body = p.canonical(result) + b"\n"
            assert len(body) <= MAX_RESPONSE_BYTES, (
                "case producer exceeded its serialized budget"
            )
    except ReviewError as error:
        return json_response(error.status, {"error": str(error)})
    except NotFound:
        return json_response(
            404,
            {
                "error": "Captured evidence is missing or its exact generation is no longer retained"
            },
        )
    except PreconditionFailed:
        return json_response(
            409, {"error": "The captured evidence generation is unavailable"}
        )
    except Forbidden:
        return json_response(
            503,
            {
                "error": "The viewer cannot read captured evidence; its reader permission may not be deployed"
            },
        )
    except (GoogleAPICallError, DefaultCredentialsError):
        return json_response(503, {"error": "Captured evidence storage is unavailable"})
    except (
        ValueError,
        TypeError,
        KeyError,
        p.PublicationError,
        model.ReleaseFeatureModelError,
    ):
        return json_response(
            502, {"error": "Captured evidence is corrupt or has an unsupported schema"}
        )
    return Response(
        200,
        {**api_headers(), "Content-Length": str(len(body))},
        body if method == "GET" else b"",
    )
