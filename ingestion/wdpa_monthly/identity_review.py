"""Bounded, immutable WDPA identity evidence and offline review reports.

Captures are internal subprocess output. Only complete bundles with verified
baseline records and canonical geometry may be staged or rendered for review.
Neither format is an allocation decision or a publication input.
"""

from __future__ import annotations

import argparse
from contextlib import closing
from dataclasses import asdict
import datetime as dt
import html
import json
from pathlib import Path
import re

from ingestion.common import decision_events, publication as p
from ingestion.common.identity_index import DiskAmbiguities, key_text
from ingestion.common.runtime import sha256_file
from scripts import release_feature_model as model

CAPTURE_SCHEMA = "wdpa-identity-review-capture-v1"
BUNDLE_SCHEMA = "wdpa-identity-review-v1"
MAX_CASES = 1024
MAX_PREVIOUS_RECORDS = 10_000
MAX_JSON_BYTES = 64 * 1024**2
MAX_REPORT_BYTES = 128 * 1024**2
MAX_FGB_BYTES = 16 * 1024**3
BUCKET = "skytruth-shared-datasets-1"
ASSETS = {"wdpa-marine", "wdpa-terrestrial"}


def fields(value, names, label):
    p.require(
        isinstance(value, dict) and set(value) == set(names.split()),
        f"invalid {label} fields",
    )


def positive_id(value):
    p.require(
        isinstance(value, str) and model.GENERATED_FEATURE_ID_RE.fullmatch(value),
        "invalid previous feature ID",
    )


def root(slug):
    p.require(slug in ASSETS, "unmanaged review asset")
    return f"gs://{BUCKET}/100-geographic-reference/130-protected-areas/{slug}/"


def capture_path(directory, slug):
    root(slug)
    return directory / f"{slug}.identity-review.capture.json"


def read_json(path):
    p.require(
        path.stat().st_size <= MAX_JSON_BYTES, "identity review exceeds JSON byte limit"
    )
    return json.loads(path.read_bytes())


def write_json(path, value):
    raw = p.canonical(value)
    p.require(len(raw) <= MAX_JSON_BYTES, "identity review exceeds JSON byte limit")
    with path.open("xb") as stream:
        stream.write(raw)


def validate_capture(value):
    fields(
        value,
        "schema asset_slug release baseline_snapshot cases previous_keys_present",
        "review capture",
    )
    p.require(value["schema"] == CAPTURE_SCHEMA, "unsupported review capture schema")
    prefix = root(value["asset_slug"])
    release = value["release"]
    p.require(
        isinstance(release, str)
        and dt.date.fromisoformat(release).isoformat() == release,
        "invalid review release",
    )
    snapshot = model.GeneratedIdentitySnapshot(**value["baseline_snapshot"])
    p.require(
        re.fullmatch(
            re.escape(prefix)
            + r"(?:latest|releases/\d{4}-\d{2}-\d{2})/"
            + re.escape(value["asset_slug"])
            + r"\.manifest\.json",
            snapshot.path,
        ),
        "foreign review baseline snapshot",
    )
    cases = value["cases"]
    p.require(
        isinstance(cases, list) and 0 < len(cases) <= MAX_CASES,
        "identity review exceeds case limit or is empty",
    )
    keys, case_ids, needed = set(), set(), set()
    for item in cases:
        fields(
            item,
            "case_id ambiguity incoming previous_feature_id_for_key",
            "review case",
        )
        evidence = decision_events.case(item["ambiguity"])
        p.require(
            item["case_id"]
            == decision_events.case_key(value["asset_slug"], release, evidence),
            "review case ID differs from ambiguity",
        )
        key = tuple(evidence["new_identity_key"])
        p.require(
            key not in keys and item["case_id"] not in case_ids, "duplicate review case"
        )
        keys.add(key)
        case_ids.add(item["case_id"])
        incoming = item["incoming"]
        fields(incoming, "source_row_number geometry properties", "incoming feature")
        p.require(
            type(incoming["source_row_number"]) is int
            and incoming["source_row_number"] > 0,
            "invalid incoming source ordinal",
        )
        p.require(
            isinstance(incoming["properties"], dict), "invalid incoming properties"
        )
        p.require(
            incoming["geometry"] is None or isinstance(incoming["geometry"], dict),
            "invalid incoming geometry",
        )
        p.require(
            model.source_fields_identity_key(incoming["properties"], ("SITE_PID",))
            == key,
            "incoming source key differs from ambiguity",
        )
        p.require(
            model.content_hashes(
                geometry=incoming["geometry"], properties=incoming["properties"]
            )
            == (evidence["new_geometry_hash"], evidence["new_properties_hash"]),
            "incoming content differs from ambiguity hashes",
        )
        owner = item["previous_feature_id_for_key"]
        if owner is not None:
            positive_id(owner)
            needed.add(owner)
        needed.update(
            evidence["matching_geometry_feature_ids"]
            + evidence["matching_properties_feature_ids"]
        )
    p.require(
        len(needed) <= MAX_PREVIOUS_RECORDS,
        "identity review exceeds previous-record limit",
    )
    present = value["previous_keys_present"]
    p.require(
        isinstance(present, list)
        and len(present) == len(set(present))
        and set(present) <= needed,
        "invalid retained previous keys",
    )
    return needed


def capture(plan, store, baseline, *, asset_slug, release, directory):
    """Copy only escalated source rows while their native/disk stores are open."""
    ambiguities = DiskAmbiguities(plan.db, unresolved=True)
    p.require(
        0 < len(ambiguities) <= MAX_CASES,
        "identity review exceeds case limit or is empty",
    )
    p.require(
        baseline.snapshot is not None, "identity review requires a frozen baseline"
    )
    previous = model.previous_feature_id_mapping(baseline.records)
    cases, needed, case_bytes = [], set(), 0
    for ambiguity in ambiguities:
        evidence = decision_events.case(
            {
                key: model.identity_ambiguity_to_dict(ambiguity)[key]
                for key in decision_events.CASE_FIELDS
            }
        )
        ordinal = plan.db.execute(
            "SELECT ordinal FROM planned WHERE identity_key=?",
            (key_text(ambiguity.identity_key),),
        ).fetchone()[0]
        feature = store.source_feature(ordinal)
        owner = previous.get(ambiguity.identity_key)
        if owner is not None:
            needed.add(owner)
        needed.update(model.matching_feature_ids_for_ambiguity(ambiguity))
        p.require(
            len(needed) <= MAX_PREVIOUS_RECORDS,
            "identity review exceeds previous-record limit",
        )
        cases.append(
            {
                "case_id": decision_events.case_key(asset_slug, release, evidence),
                "ambiguity": evidence,
                "incoming": {"source_row_number": ordinal, **feature},
                "previous_feature_id_for_key": owner,
            }
        )
        case_bytes += len(p.canonical(cases[-1]))
        p.require(
            case_bytes <= MAX_JSON_BYTES,
            "identity review exceeds JSON byte limit",
        )
    present = []
    for record in baseline.records:
        if (
            record["feature_id"] in needed
            and plan.db.execute(
                "SELECT 1 FROM planned WHERE identity_key=?",
                (key_text(model.identity_key_from_record(record)),),
            ).fetchone()
        ):
            present.append(record["feature_id"])
    value = {
        "schema": CAPTURE_SCHEMA,
        "asset_slug": asset_slug,
        "release": release,
        "baseline_snapshot": asdict(baseline.snapshot),
        "cases": cases,
        "previous_keys_present": sorted(present),
    }
    validate_capture(value)
    write_json(capture_path(directory, asset_slug), value)
    return value


def artifact(raw, *, slug, release, role):
    suffix = "fgb" if role == "fgb" else "metadata.ndjson.gz"
    result = {key: raw[key] for key in ("path", "generation", "size", "sha256")}
    result["sha256"] = result["sha256"].removeprefix("sha256:")
    fields(result, "path generation size sha256", "review artifact")
    p.require(
        result["path"] == f"{root(slug)}releases/{release}/{slug}.{suffix}",
        "foreign review artifact",
    )
    p.require(
        type(result["generation"]) is int and result["generation"] > 0,
        "unpinned review artifact",
    )
    limit = MAX_FGB_BYTES if role == "fgb" else 256 * 1024**2
    p.require(
        type(result["size"]) is int and 0 < result["size"] <= limit,
        "review artifact exceeds byte limit",
    )
    p.require(
        re.fullmatch(r"[0-9a-f]{64}", result["sha256"]),
        "invalid review artifact SHA256",
    )
    return result


def previous_geometries(path, records):
    """Extract stored canonical footprints; PMTiles never supply review evidence."""
    from osgeo import gdal

    dataset = gdal.OpenEx(str(path), gdal.OF_VECTOR)
    p.require(dataset is not None, "canonical review FGB cannot be opened")
    p.require(
        dataset.GetDriver().ShortName == "FlatGeobuf",
        "review geometry is not canonical FlatGeobuf",
    )
    layer = dataset.GetLayer(0)
    ids = sorted(records)
    for feature_id in ids:
        positive_id(feature_id)
    p.require(
        layer.SetAttributeFilter(
            "feature_id IN (" + ",".join(f"'{feature_id}'" for feature_id in ids) + ")"
        )
        == 0,
        "review feature filter failed",
    )
    found, record_bytes = set(), 0
    for row in layer:
        feature_id = row.GetField("feature_id")
        p.require(
            feature_id in records and feature_id not in found,
            "duplicate or foreign canonical review feature",
        )
        record = records[feature_id]
        p.require(
            (row.GetField("geometry_hash"), row.GetField("properties_hash"))
            == (record["geometry_hash"], record["properties_hash"]),
            "canonical feature hashes differ from sidecar",
        )
        geometry = row.GetGeometryRef()
        record["geometry"] = (
            json.loads(geometry.ExportToJson()) if geometry is not None else None
        )
        p.require(
            model.geometry_hash(record["geometry"]) == record["geometry_hash"],
            "canonical footprint differs from sidecar geometry hash",
        )
        record_bytes += len(p.canonical(record))
        p.require(
            record_bytes <= MAX_JSON_BYTES,
            "identity review exceeds JSON byte limit",
        )
        found.add(feature_id)
    p.require(found == set(records), "previous canonical review features are missing")


def validate_bundle(value):
    fields(
        value,
        "schema capture baseline previous_records source producer sample_fraction sample_seed native_versions",
        "review bundle",
    )
    p.require(value["schema"] == BUNDLE_SCHEMA, "unsupported identity review schema")
    needed = validate_capture(value["capture"])
    captured = value["capture"]
    baseline = value["baseline"]
    fields(baseline, "release identity_contract metadata fgb", "review baseline")
    p.require(
        isinstance(baseline["release"], str)
        and dt.date.fromisoformat(baseline["release"]).isoformat()
        == baseline["release"]
        and baseline["release"] <= captured["release"],
        "invalid previous review release",
    )
    model.validate_identity_contract(baseline["identity_contract"])
    for role in ("metadata", "fgb"):
        p.require(
            artifact(
                baseline[role],
                slug=captured["asset_slug"],
                release=baseline["release"],
                role=role,
            )
            == baseline[role],
            "noncanonical review artifact",
        )
    records = value["previous_records"]
    p.require(
        isinstance(records, dict) and set(records) == needed,
        "review is missing previous records or contains extras",
    )
    owners = {}
    for feature_id, record in records.items():
        fields(
            record,
            "feature_id identity_key geometry_hash properties_hash properties geometry",
            "previous review feature",
        )
        positive_id(feature_id)
        p.require(
            record["feature_id"] == feature_id
            and isinstance(record["properties"], dict),
            "invalid previous review feature",
        )
        p.require(
            record["geometry"] is None or isinstance(record["geometry"], dict),
            "invalid previous review geometry",
        )
        key = model.source_fields_identity_key(record["properties"], ("SITE_PID",))
        p.require(
            list(key) == record["identity_key"] and key not in owners,
            "invalid or duplicate previous identity key",
        )
        owners[key] = feature_id
        p.require(
            model.content_hashes(
                geometry=record["geometry"], properties=record["properties"]
            )
            == (record["geometry_hash"], record["properties_hash"]),
            "previous review content hashes differ",
        )
    for item in captured["cases"]:
        evidence = item["ambiguity"]
        p.require(
            owners.get(tuple(evidence["new_identity_key"]))
            == item["previous_feature_id_for_key"],
            "previous source-key ownership differs",
        )
        same_geometry = [
            records[feature_id]
            for feature_id in evidence["matching_geometry_feature_ids"]
        ]
        same_properties = [
            records[feature_id]
            for feature_id in evidence["matching_properties_feature_ids"]
        ]
        p.require(
            all(
                record["geometry_hash"] == evidence["new_geometry_hash"]
                for record in same_geometry
            )
            and {record["properties_hash"] for record in same_geometry}
            == set(evidence["matching_geometry_properties_hashes"]),
            "geometry-match evidence differs from previous records",
        )
        p.require(
            all(
                record["properties_hash"] == evidence["new_properties_hash"]
                for record in same_properties
            )
            and {record["geometry_hash"] for record in same_properties}
            == set(evidence["matching_properties_geometry_hashes"]),
            "properties-match evidence differs from previous records",
        )
    fields(value["source"], "url sha256 size", "review source")
    p.require(
        isinstance(value["source"]["url"], str)
        and value["source"]["url"].startswith("https://")
        and type(value["source"]["size"]) is int
        and value["source"]["size"] > 0,
        "invalid upstream review source",
    )
    fields(
        value["producer"],
        "execution executor_sha source_tree_sha256 image image_digest",
        "review producer",
    )
    p.require(
        re.fullmatch(r"wdpa-monthly-[a-z0-9]+", value["producer"]["execution"]),
        "invalid review execution",
    )
    p.require(
        re.fullmatch(r"[0-9a-f]{40}", value["producer"]["executor_sha"]),
        "invalid review executor revision",
    )
    for digest in (value["source"]["sha256"], value["producer"]["source_tree_sha256"]):
        p.require(
            isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest),
            "invalid review source digest",
        )
    p.require(
        isinstance(value["producer"]["image"], str)
        and value["producer"]["image"]
        and re.fullmatch(r"sha256:[0-9a-f]{64}", value["producer"]["image_digest"]),
        "invalid review image identity",
    )
    p.require(
        type(value["sample_fraction"]) in (int, float)
        and 0 < value["sample_fraction"] <= 1
        and type(value["sample_seed"]) is int,
        "invalid review sample scope",
    )
    p.require(
        isinstance(value["native_versions"], dict) and value["native_versions"],
        "missing native review tool evidence",
    )
    p.require(
        len(p.canonical(value)) <= MAX_JSON_BYTES,
        "identity review exceeds JSON byte limit",
    )
    return value


def complete(
    client,
    *,
    inputs,
    replay_directory,
    directory,
    blocked,
    source,
    source_url,
    producer,
):
    """Verify and finish one capture before the runtime's scratch is removed."""
    from ingestion.wdpa_monthly import run as wdpa, translations
    from scripts.local_wdpa_sample import load_baseline
    from scripts.wdpa_processing_gate import source_digest

    captured = read_json(capture_path(replay_directory, blocked.asset_slug))
    needed = validate_capture(captured)
    p.require(
        (captured["asset_slug"], captured["release"], len(captured["cases"]))
        == (blocked.asset_slug, blocked.release, blocked.ambiguity_count),
        "capture differs from paused release",
    )
    report = read_json(replay_directory / "benchmark.json")
    p.require(
        report["state"] == "blocked"
        and report["run_date"] == blocked.release
        and report["source_sha256"] == sha256_file(source)
        and report["source_tree_sha256"] == source_digest(),
        "review source differs from replay",
    )
    directory.mkdir(exist_ok=False)
    asset = next(asset for asset in wdpa.ASSETS if asset.slug == blocked.asset_slug)
    baseline = load_baseline(inputs, asset, directory)
    with closing(baseline.records):
        p.require(
            asdict(baseline.snapshot) == captured["baseline_snapshot"],
            "review baseline changed since allocation",
        )
        manifest_bytes = (inputs / f"{asset.slug}.manifest.json").read_bytes()
        p.require(
            model.sha256_hex(manifest_bytes) == baseline.snapshot.sha256,
            "review manifest changed",
        )
        versions = model.validate_release_manifest(
            json.loads(manifest_bytes),
            expected_asset_slug=asset.slug,
            expected_release=baseline.release,
            require_generations=True,
        )
        records = {}
        for row in model.read_metadata_sidecar(
            inputs / f"{asset.slug}.metadata.ndjson.gz"
        ):
            if row["feature_id"] in needed:
                p.require(
                    row["feature_id"] not in records,
                    "duplicate previous review feature",
                )
                records[row["feature_id"]] = {
                    key: row[key]
                    for key in (
                        "feature_id",
                        "identity_key",
                        "geometry_hash",
                        "properties_hash",
                        "properties",
                    )
                }
        p.require(set(records) == needed, "previous review records are missing")
        for item in captured["cases"]:
            evidence = item["ambiguity"]
            key = tuple(evidence["new_identity_key"])
            p.require(
                baseline.records.mapping().get(key)
                == item["previous_feature_id_for_key"],
                "review source-key ownership differs from frozen baseline",
            )
            ambiguity, _ = model.classify_identity_matches(
                {
                    "identity_key": key,
                    "geometry_hash": evidence["new_geometry_hash"],
                    "properties_hash": evidence["new_properties_hash"],
                },
                geometry_records=baseline.records.matches(
                    "geometry_hash", evidence["new_geometry_hash"]
                ),
                properties_records=baseline.records.matches(
                    "properties_hash", evidence["new_properties_hash"]
                ),
                baseline_geometry=baseline.records.geometry_for_key(key),
                match_properties=True,
            )
            p.require(
                ambiguity is not None
                and decision_events.case(
                    {
                        key: model.identity_ambiguity_to_dict(ambiguity)[key]
                        for key in decision_events.CASE_FIELDS
                    }
                )
                == evidence,
                "review does not contain the exact baseline matches",
            )
        artifacts = {
            role: artifact(
                versions[role], slug=asset.slug, release=baseline.release, role=role
            )
            for role in ("metadata", "fgb")
        }
        fgb = artifacts["fgb"]
        canonical = directory / "previous.fgb"
        translations.download_source(
            client.bucket(BUCKET), p.ObjectVersion(**fgb), canonical, compress=False
        )
        previous_geometries(canonical, records)
        value = {
            "schema": BUNDLE_SCHEMA,
            "capture": captured,
            "baseline": {
                "release": baseline.release,
                "identity_contract": baseline.contract_id,
                **artifacts,
            },
            "previous_records": records,
            "source": {
                "url": source_url,
                "sha256": report["source_sha256"],
                "size": source.stat().st_size,
            },
            "producer": {
                **producer,
                "source_tree_sha256": report["source_tree_sha256"],
            },
            "sample_fraction": report["sample_fraction"],
            "sample_seed": report["sample_seed"],
            "native_versions": report["native_versions"],
        }
        validate_bundle(value)
        target = directory / "identity-review.json"
        write_json(target, value)
        return target


def action_options(bundle, item):
    if item["previous_feature_id_for_key"] is not None:
        return [
            {
                "action": "keep_previous_key_mapping",
                "feature_ids": [item["previous_feature_id_for_key"]],
            },
            {"action": "assign_new_feature_id", "feature_ids": []},
        ]
    evidence = item["ambiguity"]
    targets = sorted(
        (
            set(evidence["matching_geometry_feature_ids"])
            | set(evidence["matching_properties_feature_ids"])
        )
        - set(bundle["capture"]["previous_keys_present"])
    )
    options = [{"action": "assign_new_feature_id", "feature_ids": []}]
    if targets and bundle["sample_fraction"] == 1:
        options.insert(
            0, {"action": "reuse_previous_feature_id", "feature_ids": targets}
        )
    return options


def legal_actions(bundle, item):
    return "; ".join(
        option["action"]
        + (
            f" (eligible targets: {', '.join(option['feature_ids'])})"
            if option["action"] == "reuse_previous_feature_id"
            else ""
        )
        for option in action_options(bundle, item)
    )


def property_differences(previous, incoming):
    """Preserve JSON type distinctions through browser parsing and display."""

    def cell(properties, key):
        return {
            "present": key in properties,
            "display": model.canonical_json(properties[key])
            if key in properties
            else "absent",
        }

    rows = []
    for key in sorted(set(previous) | set(incoming)):
        before, after = cell(previous, key), cell(incoming, key)
        rows.append(
            {
                "field": key,
                "previous": before,
                "incoming": after,
                "changed": before != after,
            }
        )
    return rows


def render_report(bundle, directory):
    """Render verified source text as inert HTML and export stored footprints."""
    validate_bundle(bundle)

    def escape(value):
        return html.escape(str(value), quote=True)

    sections, footprints, previous_footprints = [], [], {}
    rendered_bytes = 0

    def section(markup):
        nonlocal rendered_bytes
        rendered_bytes += len(markup.encode())
        p.require(
            rendered_bytes <= MAX_REPORT_BYTES,
            "identity report exceeds HTML byte limit",
        )
        sections.append(markup)

    capture_value = bundle["capture"]
    for item in capture_value["cases"]:
        evidence, incoming = item["ambiguity"], item["incoming"]
        previous_ids = sorted(
            set(
                evidence["matching_geometry_feature_ids"]
                + evidence["matching_properties_feature_ids"]
            )
            | (
                {item["previous_feature_id_for_key"]}
                if item["previous_feature_id_for_key"]
                else set()
            )
        )
        section(
            f"<section><h2>Source key {escape(evidence['new_identity_key'])}</h2><p>Case <code>{item['case_id']}</code></p><p>Previous ID owned by this key: <strong>{escape(item['previous_feature_id_for_key'] or 'none — new key')}</strong></p><p>Mechanically available actions: {escape(legal_actions(bundle, item))}. A maintainer must still decide whether this is the same feature.</p>"
        )
        footprints.append(
            {
                "type": "Feature",
                "geometry": incoming["geometry"],
                "properties": {
                    "case_ids": [item["case_id"]],
                    "side": "incoming",
                    "feature_id": None,
                },
            }
        )
        for feature_id in previous_ids:
            before = bundle["previous_records"][feature_id]
            rows = []
            for row in property_differences(
                before["properties"], incoming["properties"]
            ):

                def cell(value):
                    return (
                        escape(value["display"])
                        if value["present"]
                        else "<em>absent</em>"
                    )

                rows.append(
                    f'<tr class="{"changed" if row["changed"] else ""}"><th>{escape(row["field"])}</th><td>{cell(row["previous"])}</td><td>{cell(row["incoming"])}</td></tr>'
                )
            same = before["geometry_hash"] == evidence["new_geometry_hash"]
            section(
                f"<h3>Previous feature {feature_id}</h3><p>Stored footprint: {'identical' if same else 'changed'}. Changed fields are highlighted.</p><table><thead><tr><th>Field</th><th>Previous</th><th>Incoming</th></tr></thead><tbody>{''.join(rows)}</tbody></table>"
            )
            if feature_id not in previous_footprints:
                previous_footprints[feature_id] = {
                    "type": "Feature",
                    "geometry": before["geometry"],
                    "properties": {
                        "case_ids": [],
                        "side": "previous",
                        "feature_id": feature_id,
                    },
                }
            previous_footprints[feature_id]["properties"]["case_ids"].append(
                item["case_id"]
            )
        section("</section>")
    title = f"{capture_value['asset_slug']} · {capture_value['release']}"
    scope = (
        "Complete release input"
        if bundle["sample_fraction"] == 1
        else f"Compatibility sample ({bundle['sample_fraction']:.1%}); full release not enumerated"
    )
    document = f"""<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'"><title>{escape(title)} — identity review</title><style>body{{font:16px system-ui;color:#17211b;background:#f6f7f4;margin:0;padding:24px}}main{{max-width:1100px;margin:auto}}section{{background:white;padding:20px;margin:20px 0;border:1px solid #dbe1dc;border-radius:12px;overflow:auto}}table{{border-collapse:collapse;width:100%;table-layout:fixed}}th,td{{text-align:left;vertical-align:top;padding:8px;border-bottom:1px solid #dbe1dc;overflow-wrap:anywhere;white-space:pre-wrap}}tr.changed{{background:#fff4d6}}code,pre{{overflow-wrap:anywhere;white-space:pre-wrap}}summary{{cursor:pointer}}@media(max-width:600px){{body{{padding:12px}}section{{padding:12px}}}}</style><main><h1>{escape(title)}</h1><p>{escape(scope)}. This report is review evidence, not an approved identity decision.</p><p>Baseline: {escape(bundle["baseline"]["release"])} · {len(capture_value["cases"])} cases</p><p><a href="footprints.geojson">Stored footprints (GeoJSON)</a></p><details><summary>Exact inputs and producer</summary><pre>{escape(json.dumps({key: bundle[key] for key in ("source", "producer", "baseline", "native_versions")}, ensure_ascii=False, indent=2))}</pre><pre>{escape(json.dumps(capture_value["baseline_snapshot"], indent=2))}</pre></details>{"".join(sections)}</main></html>"""
    p.require(
        len(document.encode()) <= MAX_REPORT_BYTES,
        "identity report exceeds HTML byte limit",
    )
    geometry_bytes = p.canonical(
        {
            "type": "FeatureCollection",
            "features": footprints + list(previous_footprints.values()),
        }
    )
    p.require(
        len(geometry_bytes) <= MAX_JSON_BYTES, "identity review exceeds JSON byte limit"
    )
    directory.mkdir(exist_ok=False)
    (directory / "report.html").write_text(document)
    (directory / "footprints.geojson").write_bytes(geometry_bytes)
    return directory / "report.html"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "bundle", type=Path, help="Downloaded, generation-pinned identity-review.json"
    )
    parser.add_argument(
        "--out", type=Path, required=True, help="New local report directory"
    )
    args = parser.parse_args()
    print(render_report(read_json(args.bundle), args.out))


if __name__ == "__main__":
    main()
