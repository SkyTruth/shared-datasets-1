#!/usr/bin/env python3
"""Replay verified local EAMLIS releases without remote writes or publication.

The inventory lists date, format (fgb/metadata), local, rows, size, sha256, path
and generation. Existing download-evidence inventories may also contain manifests;
those are not allocation inputs. Early releases without PROBLEM_KEY are reported
as unsupported, never reconstructed using NUMBER or co-located geometry.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import gzip
import hashlib
import json
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from ingestion.eamlis_monthly import identity
from scripts import concierge_profiling


def properties(path, kind):
    if kind == "metadata":
        with gzip.open(path, "rt") as stream:
            return [json.loads(line)["properties"] for line in stream]
    identity.require(kind == "fgb", "unsupported historical source format")
    import struct
    from flatgeobuf.FlatGeobuf.Feature import Feature
    from flatgeobuf.header_meta import from_byte_buffer
    from flatgeobuf.packedrtree import calc_tree_size
    from flatgeobuf.generic.feature import parse_properties
    with path.open("rb") as stream:
        def read(size):
            identity.require(0 <= size <= path.stat().st_size, "invalid FlatGeobuf frame size")
            data = stream.read(size)
            identity.require(len(data) == size, "truncated FlatGeobuf input")
            return data
        identity.require(read(8) == b"fgb\x03fgb\x01", "invalid FlatGeobuf magic")
        header = from_byte_buffer(bytearray(read(struct.unpack("<I", read(4))[0])))
        identity.require(header.geometry_type == 1 and header.features_count > 0, "historical input must be nonempty points")
        if header.index_node_size:
            read(calc_tree_size(header.features_count, header.index_node_size))
        rows = []
        for _ in range(header.features_count):
            feature = Feature.GetRootAsFeature(bytearray(read(struct.unpack("<I", read(4))[0])))
            rows.append({**dict.fromkeys(column.name for column in header.columns),
                         **parse_properties(feature, header.columns)})
        identity.require(stream.read(1) == b"", "unexpected trailing FlatGeobuf bytes")
        return rows


def verified_rows(item):
    path = Path(item["local"])
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    identity.require(path.stat().st_size == item["size"] and digest == item["sha256"], "historical bytes differ from pinned inventory")
    rows = properties(path, item["format"])
    identity.require(type(item["rows"]) is int and len(rows) == item["rows"] and rows, "historical row count differs from inventory")
    return rows


def replay(inventory):
    inputs = sorted((item for item in inventory if item["format"] != "manifest"), key=lambda item: item["date"])
    identity.require(len({item["date"] for item in inputs}) == len(inputs), "duplicate release dates in replay inventory")
    ledger = identity.Ledger.genesis()
    previous = set()
    seen = set()
    reports = []
    for item in inputs:
        rows = verified_rows(item)
        reference = {name: item[name] for name in ("path", "generation", "size", "sha256")}
        columns = set().union(*(row.keys() for row in rows))
        report = {"date": item["date"], "rows": len(rows), "columns": len(columns), "source": reference}
        if "PROBLEM_KEY" not in columns:
            report.update(status="unsupported", reason="PROBLEM_KEY column absent; no historical inference")
        else:
            typed = identity.classify_release(rows)
            current = {row.key for row in typed}
            before = ledger
            ledger = ledger.reserve(typed)
            ledger.require_extension_of(before)
            old_ids, new_ids = before.ids, ledger.ids
            identity.require(all(new_ids[key] == old_ids[key] for key in current.intersection(old_ids)), "historical identity moved")
            report.update(status="verified", problems=sum(row.kind == "problem" for row in typed),
                area_only=sum(row.kind == "area" for row in typed),
                retained_from_previous=len(current & previous), added=len(current - seen),
                absent_since_previous=len(previous - current), reappeared=len((current - previous) & seen),
                permanent_allocations=len(ledger.entries), next_feature_id=ledger.next_feature_id)
            if item is inputs[-1]:
                profiles = concierge_profiling.build_field_profiles([
                    {key: row.get(key) for key in ("OBJECTID", "PROBLEM_KEY", "AMLIS_KEY", "NUMBER")} for row in rows])
                report["identity_candidates"] = [asdict(profile) for profile in profiles]
            previous = current
            seen.update(current)
        reports.append(report)
        print(json.dumps({key: value for key, value in report.items() if key not in {"source", "identity_candidates"}}), flush=True)
    identity.require(any(report["status"] == "verified" for report in reports), "no keyed historical release was verified")
    return {"schema_version": 1, "contract_id": identity.CONTRACT, "releases": reports,
            "permanent_allocations": len(ledger.entries), "next_feature_id": ledger.next_feature_id}, ledger


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", required=True, type=Path)
    parser.add_argument("--output-directory", required=True, type=Path)
    args = parser.parse_args(argv)
    args.output_directory.mkdir(parents=True, exist_ok=False)
    report, ledger = replay(json.loads(args.inventory.read_bytes()))
    (args.output_directory / "replay.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    (args.output_directory / "proposed-ledger.json").write_bytes(identity.encoded(ledger))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
