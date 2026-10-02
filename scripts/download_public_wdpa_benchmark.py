#!/usr/bin/env python3
"""Fetch reviewed, already-public frozen WDPA inputs without credentials."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path
import re
import urllib.parse
import urllib.request


def download_url(item):
    uri = item["uri"]
    if uri.startswith("gs://skytruth-shared-datasets-1/"):
        name = uri.split("/", 3)[3]
        if not name.startswith("100-geographic-reference/130-protected-areas/wdpa-"):
            raise ValueError("benchmark inputs must be published WDPA objects")
        generation = str(item["generation"])
        if not re.fullmatch(r"[1-9][0-9]*", generation):
            raise ValueError("benchmark object generation must be pinned")
        return "https://storage.googleapis.com/skytruth-shared-datasets-1/" + urllib.parse.quote(name, safe="/") + "?generation=" + generation
    if uri != "https://d1gam3xoknrgr2.cloudfront.net/current/WDPA_WDOECM_Oct2026_Public_all_shp.zip":
        raise ValueError("benchmark source must be the reviewed public October ZIP")
    return uri


def fetch(item, root, *, opener=urllib.request.urlopen):
    target = (root / item["target"]).resolve()
    if not target.is_relative_to(root.resolve()):
        raise ValueError("benchmark target escapes the input directory")
    url = download_url(item)
    target.parent.mkdir(parents=True, exist_ok=True)
    raw_hash, stored_hash, size = hashlib.sha256(), hashlib.sha256(), 0
    request = urllib.request.Request(url, headers={"Accept-Encoding": "gzip"})
    created = False
    try:
        with opener(request, timeout=120) as source, target.open("xb") as raw:
            created = True
            if item.get("generation") and source.headers.get("x-goog-generation") != str(item["generation"]):
                raise RuntimeError("public object generation differs from the frozen snapshot")
            output = gzip.GzipFile(filename="", fileobj=raw, mode="wb", compresslevel=1, mtime=0) if item["compress"] else raw
            try:
                while data := source.read(8 * 1024 * 1024):
                    size += len(data)
                    if size > item["size"]:
                        raise RuntimeError("public input exceeds its reviewed size")
                    raw_hash.update(data)
                    output.write(data)
            finally:
                if item["compress"]:
                    output.close()
        if size != item["size"] or raw_hash.hexdigest() != item["sha256"]:
            raise RuntimeError("public input size/hash differs from the frozen snapshot")
        with target.open("rb") as stored:
            while data := stored.read(8 * 1024 * 1024):
                stored_hash.update(data)
        if stored_hash.hexdigest() != item.get("stored_sha256", item["sha256"]):
            raise RuntimeError("frozen compressed input hash differs")
    except BaseException:
        if created:
            target.unlink(missing_ok=True)
        raise
    print(json.dumps({"target": item["target"], "bytes": size, "sha256": raw_hash.hexdigest()}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recipe", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    recipe = json.loads(args.recipe.read_text())
    if recipe["schema_version"] != 1:
        raise ValueError("unsupported benchmark input recipe")
    args.out.mkdir(parents=True, exist_ok=False)
    fetch(recipe["source"], args.out)
    for item in recipe["files"]:
        fetch(item, args.out)
    frozen = args.out / "frozen-inputs"
    for name, payload in [("pins.json", recipe["baseline_pins"]), ("translation-sources.json", recipe["translation_sources"])]:
        (frozen / name).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
