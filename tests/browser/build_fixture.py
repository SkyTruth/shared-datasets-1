"""Create tiny input contracts and build the unmodified production catalog bundle."""

from __future__ import annotations

import csv
import gzip
import hashlib
import json
import os
import subprocess
import shutil
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def build(work: Path) -> None:
    for name, digest in {"old": "dd0fcd07c883059a6d8ec76cc9cb9088bca7904887a253aa509e11f2af673927",
                         "new": "1d0d868fb77f04bbe0c00704a84896db380347d8186e2d33c088374947497871"}.items():
        archive = (FIXTURES / f"{name}.pmtiles").read_bytes()
        if archive[:8] != b"PMTiles\x03" or hashlib.sha256(archive).hexdigest() != digest:
            raise ValueError(f"Unverified PMTiles fixture: {name}")
    inputs = work / "inputs"
    docs = inputs / "docs"
    indexes = inputs / "indexes"
    objects = work / "objects"
    for directory in (docs, indexes, objects):
        directory.mkdir(parents=True, exist_ok=True)
    # One shared two-release identity fixture is also consumed by JS/SDK unit tests.
    contract = json.loads((REPO / "tests/fixtures/historical-consumers.json").read_text())
    rows = []
    for tier in ("public", "private", "internal"):
        slug = f"smoke-{tier}"
        root = f"gs://example-bucket/category/subcategory/{slug}"
        row = dict(asset_slug=slug, title=f"Smoke {tier}", category="category", subcategory="subcategory",
                   status="active", access_tier=tier, owner="SkyTruth", update_cadence="manual",
                   canonical_path=f"{root}/latest/{slug}.fgb", canonical_format="fgb",
                   available_formats="fgb;pmtiles", metadata_paths="README.md", source="Synthetic fixture",
                   license="CC0", citation="Repository-authored browser test fixture")
        rows.append(row)
        metadata = dict(row, canonical_file=f"latest/{slug}.fgb", available_formats=["fgb", "pmtiles"],
                        metadata_paths=["README.md"], geometry_type="Point", row_count=2,
                        feature_identity={"strategy": "source_field", "source_fields": ["feature_id"]},
                        feature_metadata={"storage": "metadata_sidecar_v1", "feature_id_column": "feature_id",
                                          "geometry_hash_column": "geometry_hash", "properties_hash_column": "properties_hash",
                                          "sidecar_file": f"latest/{slug}.metadata.ndjson.gz",
                                          "schema_file": f"latest/{slug}.schema.json",
                                          "manifest_file": f"latest/{slug}.manifest.json", "provenance_default": True})
        (docs / f"{slug}.md").write_text(f"---\n{yaml.safe_dump(metadata)}---\n# Smoke {tier}\n\nSynthetic two-release points.\n")
        index = json.loads(json.dumps(contract["index"]))
        index["asset_slug"] = slug
        for release in index["releases"]:
            old = release["date"] == "2026-01-01"
            letter, label = ("a", "Old") if old else ("b", "New")
            records = [{"feature_id": f"{letter}{i}", "release": release["date"], "asset_slug": slug,
                        "properties": {"name": f"{label} footprint {i}"}} for i in (1, 2)]
            for file in release["files"]:
                file["path"] = file["path"].replace("example-layer", slug)
                if file["format"] == "pmtiles":
                    data = (FIXTURES / ("old.pmtiles" if old else "new.pmtiles")).read_bytes()
                elif file["format"] == "metadata":
                    data = gzip.compress(("\n".join(json.dumps(record) for record in records) + "\n").encode(), mtime=0)
                elif file["format"] == "schema":
                    data = json.dumps({"fields": [{"name": "name", "type": "string"}]}).encode()
                else:
                    data = b"Download URL assertion only; not a FlatGeobuf fixture."
                file["size"] = len(data)
                file["sha256"] = hashlib.sha256(data).hexdigest()
                target = objects / file["path"].split("example-bucket/", 1)[1]
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
        index["latest_release"] = index["releases"][0]
        (indexes / f"{slug}.json").write_text(json.dumps(index))
    with (inputs / "catalog.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    (inputs / "categories.yaml").write_text("categories:\n  category:\n    subcategories:\n      subcategory: Fixtures\n")
    subprocess.run([sys.executable, "scripts/catalog_site.py", "--catalog", str(inputs / "catalog.csv"),
                    "--categories", str(inputs / "categories.yaml"), "--docs-dir", str(docs),
                    "--release-index-dir", str(indexes), "--bucket", "example-bucket",
                    "--generated-at", "2026-09-24T00:00:00Z", "--out", str(work / "site")], cwd=REPO, check=True)

    shutil.copytree(REPO / "api/typescript/dist", work / "site/sdk", dirs_exist_ok=True)

    if os.environ.get("CATALOG_BROWSER_NEGATIVE_CONTROL") == "stale-inspector":
        app_path = work / "site/app.js"
        app = app_path.read_text()
        guard = "  if (requestSerial !== state.featureLookupSerial) {\n    return;\n  }\n"
        if app.count(guard) != 2:
            raise ValueError("Stale-inspector negative control no longer matches the product")
        app_path.write_text(app.replace(guard, "", 1))


if __name__ == "__main__":
    build(Path(sys.argv[1]))
