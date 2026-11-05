"""Validate the published ingestion release and its feature metadata hygiene."""
from __future__ import annotations

import argparse
import gzip
import json
import os
from pathlib import Path
import struct
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.ingestion_targets import TARGETS
from scripts.release_feature_model import (
    read_metadata_sidecar_bytes, validate_release_manifest, validate_release_schema, validate_sidecar_records,
)


def release_contract():
    bucket = os.environ["BUCKET"]
    config = TARGETS[os.environ["TARGET"]]
    asset_slug = config["asset_slug"]
    asset_root = config["asset_root"]
    index_uri = f"gs://{bucket}/_catalog/releases/{asset_slug}.json"
    index = json.loads(subprocess.check_output(["gcloud", "storage", "cat", index_uri]))
    release = index["latest_release"]["date"]
    base = f"gs://{bucket}/{asset_root}/releases/{release}/{asset_slug}"

    manifest = json.loads(subprocess.check_output(["gcloud", "storage", "cat", base + ".manifest.json"]))
    validate_release_manifest(
        manifest,
        expected_asset_slug=asset_slug,
        expected_release=release,
        require_generations=True,
    )

    schema = json.loads(subprocess.check_output(["gcloud", "storage", "cat", base + ".schema.json"]))
    validate_release_schema(schema, expected_asset_slug=asset_slug, expected_release=release)

    metadata_bytes = subprocess.check_output(["gcloud", "storage", "cat", base + ".metadata.ndjson.gz"])
    records = list(read_metadata_sidecar_bytes(metadata_bytes, label=base + ".metadata.ndjson.gz"))
    sidecar = validate_sidecar_records(
        records,
        expected_asset_slug=asset_slug,
        expected_release=release,
    )
    if not sidecar.valid:
        raise RuntimeError("; ".join(sidecar.errors))

    fgb = subprocess.check_output(["gcloud", "storage", "cat", base + ".fgb"])
    fgb_fields = {
        key: key.encode() in fgb
        for key in ("feature_id", "geometry_hash", "properties_hash")
    }
    if not all(fgb_fields.values()):
        raise RuntimeError(f"FGB missing required fields: {fgb_fields}")

    raw_header = subprocess.check_output(["gcloud", "storage", "cat", "--range=0-126", base + ".pmtiles"])
    _root_offset, _root_length, metadata_offset, metadata_length = struct.unpack("<QQQQ", raw_header[8:40])
    metadata_raw = subprocess.check_output([
        "gcloud",
        "storage",
        "cat",
        f"--range={metadata_offset}-{metadata_offset + metadata_length - 1}",
        base + ".pmtiles",
    ])
    pmtiles_metadata = json.loads(gzip.decompress(metadata_raw).decode("utf-8"))
    layers = [
        {"id": layer.get("id"), "fields": sorted((layer.get("fields") or {}).keys())}
        for layer in pmtiles_metadata.get("vector_layers") or []
    ]
    if not layers or any(layer["fields"] != ["feature_id"] for layer in layers):
        raise RuntimeError(f"PMTiles metadata fields are not feature_id-only: {layers}")

    print(json.dumps({
        "latest_release": release,
        "sidecar_count": sidecar.feature_count,
        "fgb_required_fields": fgb_fields,
        "pmtiles_layers": layers,
    }, indent=2, sort_keys=True))


def hygiene(path):
    asset_slug = TARGETS[os.environ["TARGET"]]["asset_slug"]
    payload = json.loads(Path(path).read_text())
    findings = payload.get("findings", [])
    asset_findings = [
        finding for finding in findings
        if asset_slug in json.dumps(finding, sort_keys=True)
        and "feature-metadata" in json.dumps(finding, sort_keys=True).lower()
    ]
    if asset_findings:
        print(json.dumps(asset_findings, indent=2, sort_keys=True))
        raise SystemExit("Asset feature-metadata hygiene findings remain")
    print("No asset feature-metadata hygiene findings remain.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("hygiene_report", nargs="?")
    args = parser.parse_args()
    hygiene(args.hygiene_report) if args.hygiene_report else release_contract()
