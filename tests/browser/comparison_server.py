"""Run the real catalog viewer/comparison engine with local pinned fixture bytes."""

import json
import os
import sys
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


store = FixtureStore(work / "site")
jobs = comparisons.ComparisonJobs(
    root=work / "comparison-jobs", reader=reader, geometry_opener=geometry_opener
)
handler = viewer.make_handler(
    catalog_cache=viewer.CatalogJsonCache(loader=store.read_catalog_json),
    object_store=store,
    signer=None,
    bucket_name="example-bucket",
    signed_url_ttl_seconds=900,
    allowed_email_domains=("skytruth.org",),
    comparison_jobs=jobs,
)
ThreadingHTTPServer(
    ("127.0.0.1", int(os.environ.get("CATALOG_BROWSER_PORT", "4179"))), handler
).serve_forever()
