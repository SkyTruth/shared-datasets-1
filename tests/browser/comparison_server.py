"""Run the real catalog viewer/comparison engine with local pinned fixture bytes."""

import json
import hashlib
import os
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from services.catalog_viewer import comparisons, run as viewer

work = Path(sys.argv[1])


class FixtureStore(viewer.LocalCatalogWebStore):
    def read_static(self, name):
        if name.startswith("releases/"):
            data = (work / "inputs/indexes" / name.split("/")[-1]).read_bytes()
            return viewer.StaticObject(data, "application/json")
        return super().read_static(name)


def fixture_path(ref):
    indexes = [
        json.loads(p.read_text()) for p in (work / "inputs/indexes").glob("*.json")
    ]
    files = [
        f
        for index in indexes
        for release in index["releases"]
        for f in release["files"]
    ]
    if not any(
        f["path"] == ref["path"] and str(f["generation"]) == ref["generation"]
        for f in files
    ):
        raise OSError("Fixture generation not retained")
    source = work / "objects" / ref["path"].split("example-bucket/", 1)[1]
    return source


def reader(ref, target, *, bucket_name, comparison):
    target.write_bytes(fixture_path(ref).read_bytes())
    comparison.verify_bytes(target, ref)


def geometry_opener(ref, *, bucket_name):
    return fixture_path(ref).open("rb")


class ScenarioJobs:
    """Give each browser scenario a fresh real job store with production limits."""

    limits = comparisons.VIEWER_LIMITS

    def __init__(self):
        self.scenarios = {}
        self.lock = threading.Lock()

    def for_owner(self, owner):
        with self.lock:
            if owner not in self.scenarios:
                self.scenarios[owner] = comparisons.ComparisonJobs(
                    root=work / "comparison-jobs" / hashlib.sha256(owner.encode()).hexdigest(),
                    reader=reader,
                    geometry_opener=geometry_opener,
                )
            return self.scenarios[owner]

    def start(self, owner, slug, inputs, bucket_name):
        return self.for_owner(owner).start(owner, slug, inputs, bucket_name)

    def get(self, job_id, owner):
        return self.for_owner(owner).get(job_id, owner)

    def cancel(self, job):
        return self.for_owner(job.owner).cancel(job)

    def prepare(self, job):
        return self.for_owner(job.owner).prepare(job)


store = FixtureStore(work / "site")
jobs = ScenarioJobs()
handler = viewer.make_handler(
    catalog_cache=viewer.CatalogJsonCache(loader=store.read_catalog_json),
    object_store=store,
    signer=None,
    bucket_name="example-bucket",
    signed_url_ttl_seconds=900,
    allowed_email_domains=("skytruth.org",),
    comparison_jobs=jobs,
    usage_reader=None,
)
ThreadingHTTPServer(
    ("127.0.0.1", int(os.environ.get("CATALOG_BROWSER_PORT", "4179"))), handler
).serve_forever()
