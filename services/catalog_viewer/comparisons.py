"""Bounded, process-local comparison jobs for the authenticated catalog viewer."""

from __future__ import annotations

import json
import re
import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from http import HTTPStatus
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from scripts import compare_releases as engine
from scripts import release_feature_model as model
from services.http_base import (
    authenticated_user_email,
    email_domain_allowed,
    json_response,
    split_gs_uri,
)

MAX_RESPONSE_BYTES = 10 * 1024 * 1024
MAX_JOBS = 8
MAX_RUNNING = 2
JOB_TTL_SECONDS = 900
JOB_PATH = re.compile(
    r"^/api/comparisons/(?P<id>[a-f0-9]{32})(?:/(?P<action>cancel|report|map))?$"
)


@dataclass
class Job:
    id: str
    owner: str
    slug: str
    inputs: dict
    comparison: engine.Comparison
    created: float = field(default_factory=time.monotonic)
    cancel: threading.Event = field(default_factory=threading.Event)
    state: str = "running"
    phase: str = "downloading"
    rows: int = 0
    error: str | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)

    def update(self, phase, rows):
        with self.lock:
            self.phase, self.rows = phase, rows

    def payload(self):
        with self.lock:
            result = {
                "job_id": self.id,
                "state": self.state,
                "progress": {"phase": self.phase, "rows": self.rows},
                "inputs": self.inputs,
                "error": self.error,
            }
            if self.state == "complete":
                result["summary"] = self.comparison.summary
            return result


class ComparisonJobs:
    def __init__(
        self,
        *,
        root: Path | None = None,
        limits=engine.Limits(),
        reader=None,
        geometry_opener=None,
    ):
        self.root = root or engine.work_root() / "comparisons" / (
            "viewer-" + uuid.uuid4().hex
        )
        self.limits, self.reader = limits, reader or download_input
        self.geometry_opener = geometry_opener or open_geometry
        self.jobs = {}
        self.lock = threading.Lock()
        self.pool = ThreadPoolExecutor(
            max_workers=MAX_RUNNING, thread_name_prefix="release-comparison"
        )

    def start(self, owner, slug, inputs, bucket_name):
        with self.lock:
            for key, job in list(self.jobs.items()):
                if (
                    job.state != "running"
                    and time.monotonic() - job.created > JOB_TTL_SECONDS
                ):
                    shutil.rmtree(job.comparison.directory)
                    del self.jobs[key]
            if len(self.jobs) >= MAX_JOBS:
                raise engine.ComparisonLimit(
                    "Eight retained comparison jobs are in use; retry after the 15-minute TTL or use the local CLI"
                )
            if sum(j.state == "running" for j in self.jobs.values()) >= MAX_RUNNING:
                raise engine.ComparisonLimit(
                    "Comparison capacity reached; cancel or wait for an active comparison"
                )
            job_id = uuid.uuid4().hex
            comparison = engine.Comparison(self.root / job_id, limits=self.limits)
            job = Job(job_id, owner, slug, inputs, comparison)
            comparison.cancelled, comparison.progress = job.cancel.is_set, job.update
            self.jobs[job_id] = job
            self.pool.submit(self._run, job, bucket_name)
            return job

    def _run(self, job, bucket_name):
        try:
            paths = []
            for side in ("baseline", "target"):
                local = {}
                for role in ("manifest", "schema", "metadata"):
                    job.comparison.check()
                    ref = job.inputs[side]["files"][role]
                    if ref.get("size", 0) > self.limits.max_input_bytes:
                        raise engine.ComparisonLimit(
                            "Input exceeds max_input_bytes; download the exact bundle and use scripts/compare_releases.py"
                        )
                    target = job.comparison.directory / f"{side}-{role}"
                    self.reader(
                        ref, target, bucket_name=bucket_name, comparison=job.comparison
                    )
                    local[role] = target
                paths.append(local)
            job.comparison.run(
                job.inputs["baseline"],
                job.inputs["target"],
                *paths,
                open_geometry=lambda ref: self.geometry_opener(
                    ref, bucket_name=bucket_name
                ),
            )
            job.comparison.check()
            with job.lock:
                job.state, job.phase = "complete", "complete"
        except engine.ComparisonCancelled as exc:
            with job.lock:
                job.state, job.error = "cancelled", str(exc)
        except (
            engine.ComparisonError,
            model.ReleaseFeatureModelError,
            OSError,
            ValueError,
            KeyError,
        ) as exc:
            with job.lock:
                job.state, job.error = "failed", str(exc)
        except Exception:
            # External storage/backend errors must not expose credentials or signed URLs.
            with job.lock:
                job.state, job.error = (
                    "failed",
                    "Comparison input is unavailable at the selected generation or the backend failed. No latest substitution was made.",
                )

    def get(self, job_id, owner):
        with self.lock:
            job = self.jobs.get(job_id)
            if (
                job is None
                or job.owner != owner
                or time.monotonic() - job.created > JOB_TTL_SECONDS
            ):
                return None
            return job


def open_geometry(ref, *, bucket_name):
    from google.cloud import storage

    bucket, name = split_gs_uri(ref["path"])
    if bucket != bucket_name:
        raise engine.ComparisonError("Geometry input is outside the catalog bucket")
    generation = int(ref["generation"])
    return (
        storage.Client()
        .bucket(bucket)
        .blob(name, generation=generation)
        .open(
            "rb",
            if_generation_match=generation,
            chunk_size=8 * 1024 * 1024,
            timeout=15,
            retry=None,
        )
    )


def download_input(ref, target, *, bucket_name, comparison):
    from google.cloud import storage

    bucket, name = split_gs_uri(ref["path"])
    if bucket != bucket_name:
        raise engine.ComparisonError("Comparison input is outside the catalog bucket")
    generation = int(ref["generation"])
    blob = storage.Client().bucket(bucket).blob(name, generation=generation)
    # Timeouts/retries are bounded independently of the task deadline.
    with (
        blob.open(
            "rb", if_generation_match=generation, timeout=15, retry=None
        ) as source,
        target.open("wb") as destination,
    ):
        size = 0
        while True:
            comparison.check()
            data = source.read(1024 * 1024)
            if not data:
                break
            size += len(data)
            if size > comparison.limits.max_input_bytes:
                raise engine.ComparisonLimit(
                    "Input exceeds max_input_bytes; use the local CLI"
                )
            destination.write(data)
    comparison.verify_bytes(target, ref)


def resolve_snapshot(asset, release, *, object_store, bucket_name):
    from services.catalog_viewer import run as viewer

    files = {}
    index_object = object_store.read_static(f"releases/{asset['slug']}.json")

    class SnapshotStore:
        def read_static(self, name):
            return index_object

    snapshot_store = SnapshotStore()
    # Resolve each role through the same catalog-owned path boundary as downloads.
    roles = (
        ("metadata", "schema", "manifest", "pmtiles")
        if viewer.asset_has_pmtiles(asset)
        else ("metadata", "schema", "manifest")
    )
    if asset.get("canonical_format") == "fgb":
        roles = (*roles, "fgb")
    for role in roles:
        selected = viewer.resolve_artifact(
            asset, role, release, locale="", object_store=snapshot_store
        )
        if split_gs_uri(selected.uri)[0] != bucket_name:
            raise engine.ComparisonError(
                "Comparison input is outside the configured bucket"
            )
        files[role] = engine.artifact(
            {
                "path": selected.uri,
                "generation": selected.generation,
                **({"sha256": selected.sha256} if selected.sha256 else {}),
                **({"size": selected.size} if selected.size is not None else {}),
            }
        )
    return engine.snapshot(
        {"asset_slug": asset["slug"], "release": release, "files": files}
    )


def bounded_response(status, payload):
    if len(json.dumps(payload).encode()) > MAX_RESPONSE_BYTES:
        return json_response(
            413,
            {
                "error": "Response exceeds 10 MiB; use the local CLI for a complete report"
            },
        )
    return json_response(status, payload)


def handle_request(
    method,
    path,
    headers,
    body,
    *,
    object_store,
    bucket_name,
    allowed_email_domains,
    require_iap,
    jobs,
):
    from services.catalog_viewer import run as viewer

    email = authenticated_user_email(headers)
    if require_iap or email:
        if not email:
            return json_response(401, {"error": "IAP identity required"})
        if not email_domain_allowed(email, allowed_email_domains):
            return json_response(403, {"error": "SkyTruth IAP identity required"})
    owner = email or "local-public"
    url = urlsplit(path)
    match = JOB_PATH.fullmatch(url.path)
    try:
        if url.path == "/api/comparisons" and method == "GET":
            return json_response(
                200,
                {
                    "available": True,
                    "limits": asdict(jobs.limits),
                    "max_page_size": 100,
                    "job_ttl_seconds": JOB_TTL_SECONDS,
                    "max_report_bytes": MAX_RESPONSE_BYTES,
                },
            )
        if url.path == "/api/comparisons" and method == "POST":
            if len(body) > 16 * 1024:
                return json_response(
                    413, {"error": "Comparison request exceeds 16 KiB"}
                )
            payload = json.loads(body)
            if not isinstance(payload, dict) or set(payload) != {
                "slug",
                "baseline",
                "target",
                "expected",
            }:
                raise engine.ComparisonError(
                    "Select slug, baseline, target and expected snapshot identities"
                )
            slug = payload["slug"]
            if not isinstance(slug, str) or not viewer.SLUG_RE.fullmatch(slug):
                raise engine.ComparisonError("Invalid asset slug")
            job = None
        elif match:
            job = jobs.get(match["id"], owner)
            if job is None:
                return json_response(
                    404,
                    {
                        "error": "Comparison expired or unavailable to this user; run it again"
                    },
                )
            slug = job.slug
        else:
            return json_response(405, {"error": "Unsupported comparison operation"})
        # Fresh catalog authorization on every start/page/inspection/cancel/export.
        asset = viewer.catalog_asset(object_store.read_catalog_json(), slug)
        if asset is None:
            return json_response(404, {"error": "Unknown catalog asset"})
        if viewer.catalog_access_tier(asset) in viewer.RESTRICTED_ACCESS_TIERS:
            if not email:
                return json_response(401, {"error": "IAP identity required"})
            if not email_domain_allowed(email, allowed_email_domains):
                return json_response(403, {"error": "SkyTruth IAP identity required"})
        if job is None:
            inputs = {
                side: resolve_snapshot(
                    asset,
                    payload[side],
                    object_store=object_store,
                    bucket_name=bucket_name,
                )
                for side in ("baseline", "target")
            }
            expected = payload["expected"]
            if not isinstance(expected, dict) or set(expected) != {
                "baseline",
                "target",
            }:
                raise engine.ComparisonError("Expected identities are required")
            for side in inputs:
                actual = {
                    role: {"path": f["path"], "generation": f["generation"]}
                    for role, f in inputs[side]["files"].items()
                }
                if expected[side] != actual:
                    return json_response(
                        409,
                        {
                            "error": "Selected snapshot changed; reload the catalog and reselect the releases"
                        },
                    )
            job = jobs.start(owner, slug, inputs, bucket_name)
            return json_response(HTTPStatus.ACCEPTED, job.payload())
        if match["action"] == "cancel" and method == "POST":
            job.cancel.set()
            return json_response(
                200,
                {
                    "job_id": job.id,
                    "state": "cancelling" if job.state == "running" else job.state,
                },
            )
        result = job.payload()
        if match["action"] == "map":
            if method != "POST":
                return json_response(405, {"error": "Use POST for map feature lookups"})
            if len(body) > 16 * 1024:
                return json_response(413, {"error": "Map request exceeds 16 KiB"})
            if result["state"] != "complete":
                return json_response(409, {"error": "Comparison is not complete"})
            payload = json.loads(body)
            if not isinstance(payload, dict) or set(payload) != {"side", "feature_ids"}:
                raise engine.ComparisonError(
                    "Select a release side and map feature IDs"
                )
            return bounded_response(
                200,
                {
                    "inputs": job.inputs,
                    "map_features": job.comparison.map_features(
                        payload["side"], payload["feature_ids"]
                    ),
                },
            )
        if method != "GET":
            return json_response(405, {"error": "Use GET for comparison results"})
        if result["state"] != "complete":
            return json_response(200, result)
        if match["action"] == "report":
            report = job.comparison.directory / "report.json"
            # Report contains all classifications; never silently export only a page.
            with job.lock:
                job.comparison.export(report)
                if report.stat().st_size > MAX_RESPONSE_BYTES:
                    return json_response(
                        413,
                        {
                            "error": "Complete report exceeds 10 MiB; use scripts/compare_releases.py"
                        },
                    )
                from services.http_base import Response, api_headers

                return Response(
                    200,
                    {
                        **api_headers(),
                        "Content-Disposition": f'attachment; filename="{slug}-comparison.json"',
                    },
                    report.read_bytes(),
                )
        params = parse_qs(url.query, keep_blank_values=True)
        if any(len(v) != 1 for v in params.values()) or set(params) - {
            "offset",
            "limit",
            "query",
            "classification",
            "geometry_change",
            "feature_id",
        }:
            raise engine.ComparisonError("Invalid comparison page parameters")
        if result["summary"]["identity"]["compatible"]:
            if "feature_id" in params:
                result["feature"] = job.comparison.inspect(params["feature_id"][0])
            else:
                result["page"] = job.comparison.page(
                    offset=int(params.get("offset", [0])[0]),
                    limit=int(params.get("limit", [50])[0]),
                    query=params.get("query", [""])[0],
                    classification=params.get("classification", [""])[0],
                    geometry_change=params.get("geometry_change", [""])[0],
                )
        return bounded_response(200, result)
    except viewer.DownloadResolutionError as exc:
        return json_response(exc.status, {"error": exc.message})
    except engine.ComparisonLimit as exc:
        return json_response(429, {"error": str(exc)})
    except (
        engine.ComparisonError,
        model.ReleaseFeatureModelError,
        ValueError,
        TypeError,
        KeyError,
    ) as exc:
        return json_response(400, {"error": str(exc)})
    except Exception:
        return json_response(
            503, {"error": "Catalog comparison inputs or authorization are unavailable"}
        )
