"""Reviewed repair of one legacy run's manifest checksum declarations.

The manifest is independently hashed and its metadata is patched with generation
and metageneration preconditions. Its bytes/generation never change. The only
permitted run-record edit is sha256.manifest; all ordinary checksum validation
remains strict. Plan validation is stdlib-only; cloud index dependencies are
loaded at the publication boundary.
"""

from __future__ import annotations

from copy import deepcopy
import datetime as dt
import json
import re

from ingestion.eamlis_monthly import identity as ids, source_quality as quality

KEY = "historical_checksum_repair"
URI = f"gs://{quality.BUCKET}/"


def validate_plan(plan):
    ids.require(
        plan["asset_slug"] == quality.SLUG
        and len(plan["promotions"]) == 1
        and plan["release_index_asset_slugs"] == [quality.SLUG]
        and not plan.get("breaking_changes")
        and not {
            "identity_reset",
            "identity_transition",
            "source_loss_review",
        }.intersection(plan),
        "checksum repair permits only one historical EAMLIS run and its own index validation",
    )
    value = plan[KEY]
    fields = {
        "schema_version",
        "release",
        "baseline_token",
        "original_record",
        "original_record_sha256",
        "manifest",
    }
    ids.require(
        isinstance(value, dict)
        and set(value) == fields
        and type(value["schema_version"]) is int
        and value["schema_version"] == 1,
        "invalid historical checksum repair",
    )
    date = value["release"]
    ids.require(
        isinstance(date, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", date),
        "invalid historical release date",
    )
    dt.date.fromisoformat(date)
    for key in ("baseline_token", "original_record_sha256"):
        ids.require(
            isinstance(value[key], str) and re.fullmatch(r"[0-9a-f]{64}", value[key]),
            "invalid repair evidence hash",
        )
    manifest = value["manifest"]
    ids.require(
        isinstance(manifest, dict)
        and set(manifest)
        == {
            "path",
            "generation",
            "metageneration",
            "size",
            "sha256",
            "metadata",
            "content_type",
            "cache_control",
        },
        "invalid manifest repair evidence",
    )
    for key in ("generation", "metageneration"):
        ids.require(
            isinstance(manifest[key], str)
            and re.fullmatch(r"[1-9][0-9]*", manifest[key]),
            "invalid manifest generation",
        )
    ids.require(
        type(manifest["size"]) is int
        and manifest["size"] > 0
        and isinstance(manifest["sha256"], str)
        and re.fullmatch(r"[0-9a-f]{64}", manifest["sha256"])
        and isinstance(manifest["metadata"], dict)
        and all(
            isinstance(k, str) and isinstance(v, str)
            for k, v in manifest["metadata"].items()
        )
        and isinstance(manifest["content_type"], str)
        and (
            manifest["cache_control"] is None
            or isinstance(manifest["cache_control"], str)
        ),
        "invalid manifest checksum metadata",
    )
    expected = f"{URI}{quality.ROOT}/releases/{date}/{quality.SLUG}.manifest.json"
    ids.require(
        manifest["path"] == expected
        and re.fullmatch(r"[0-9a-f]{64}", manifest["metadata"].get("sha256", ""))
        and manifest["metadata"]["sha256"] != manifest["sha256"],
        "repair requires an incorrect checksum label on the exact historical manifest",
    )
    item = plan["promotions"][0]
    ids.require(
        item["destination_uri"] == f"{URI}{quality.ROOT}/runs/{date}.json"
        and item["destination_generation"]
        and item["content_type"] == "application/json"
        and item["cache_control"] == "no-cache, max-age=0, must-revalidate"
        and not item.get("compatibility_waiver"),
        "repair must replace exactly its pinned historical run record",
    )
    record = value["original_record"]
    ids.require(
        isinstance(record, dict)
        and record.get("asset_slug") == quality.SLUG
        and record.get("release_date") == date
        and record.get("status") == "success"
        and isinstance(record.get("sha256"), dict)
        and isinstance(record["sha256"].get("manifest"), str)
        and record["sha256"]["manifest"] != manifest["sha256"],
        "repair needs the original conflicting successful run",
    )
    refs = [
        entry
        for entry in record.get("release_paths", [])
        if isinstance(entry, dict) and entry.get("path") == expected
    ]
    ids.require(
        len(refs) == 1
        and str(refs[0].get("generation")) == manifest["generation"]
        and refs[0].get("size") == manifest["size"]
        and refs[0].get("sha256") == manifest["sha256"],
        "run's exact manifest reference must already match the independently verified manifest",
    )
    return value


def corrected_record(plan):
    value = validate_plan(plan)
    record = deepcopy(value["original_record"])
    record["sha256"]["manifest"] = value["manifest"]["sha256"]
    return record


def validate_live(guard, plan):
    value = validate_plan(plan)
    identity = guard.state.get("identity") or {}
    ids.require(
        identity.get("phase") == "active"
        and value["release"] < identity["first_release"],
        "checksum repair is restricted to releases before the active identity cutover",
    )
    ids.require(
        not guard.held
        and quality.baseline_token(guard.before) == value["baseline_token"],
        "checksum repair baseline changed or publication is held",
    )
    item = plan["promotions"][0]
    source = guard.bucket.client.bucket(quality.BUCKET).blob(
        item["source_uri"][len(URI) :], generation=int(item["source_generation"])
    )
    proposed = source.download_as_bytes(
        if_generation_match=int(item["source_generation"])
    )
    ids.require(
        quality.encode(json.loads(proposed)) == quality.encode(corrected_record(plan)),
        "repair changes fields other than sha256.manifest",
    )
    record = guard.bucket.blob(item["destination_uri"][len(URI) :])
    record.reload()
    raw = record.download_as_bytes(if_generation_match=int(record.generation))
    if str(record.generation) == item["destination_generation"]:
        ids.require(
            quality.digest(raw) == value["original_record_sha256"]
            and quality.encode(json.loads(raw))
            == quality.encode(value["original_record"]),
            "original run record differs from reviewed evidence",
        )
    else:
        ids.require(
            raw == proposed, "historical run record changed outside this repair"
        )
    evidence = value["manifest"]
    manifest = guard.bucket.blob(evidence["path"][len(URI) :])
    manifest.reload()
    ids.require(
        str(manifest.generation) == evidence["generation"]
        and int(manifest.size) == evidence["size"]
        and manifest.content_type == evidence["content_type"]
        and manifest.cache_control == evidence["cache_control"],
        "historical manifest identity or metadata changed",
    )
    # Keep the stored-resource metadata view separate from download headers.
    raw = quality.pinned(guard.bucket.client, evidence)
    from scripts.release_feature_model import validate_release_manifest

    validate_release_manifest(
        json.loads(raw),
        expected_asset_slug=quality.SLUG,
        expected_release=value["release"],
        require_generations=True,
    )
    old = evidence["metadata"]
    fixed = {**old, "sha256": evidence["sha256"]}
    ids.require(
        manifest.metadata in (old, fixed), "unreviewed manifest metadata change"
    )
    if manifest.metadata == old:
        ids.require(
            str(manifest.metageneration) == evidence["metageneration"],
            "manifest metageneration changed",
        )
    return manifest


def owned_guard(client, plan):
    guard = quality.Guard(
        client.bucket(quality.BUCKET), owner=quality.protected_authority(plan)
    )
    guard.state, guard.generation = guard.load()
    ids.require(
        guard.state["owner"] == guard.owner,
        "checksum repair does not own the publication claim",
    )
    guard.before = quality.baseline(guard.bucket)
    return guard


def patch_manifest(client, plan):
    guard = owned_guard(client, plan)
    blob = validate_live(guard, plan)
    evidence = plan[KEY]["manifest"]
    if blob.metadata == evidence["metadata"]:
        blob.metadata = {**blob.metadata, "sha256": evidence["sha256"]}
        blob.patch(
            if_generation_match=int(evidence["generation"]),
            if_metageneration_match=int(evidence["metageneration"]),
        )
    print(
        "Historical manifest checksum label verified; bytes and generation preserved."
    )


def repair_index(client, plan, row):
    from ingestion.common import release_index

    guard = owned_guard(client, plan)
    validate_live(guard, plan)
    rebuilt = release_index.rebuild_index_from_bucket(guard.bucket, row)
    loaded = release_index.load_release_index(guard.bucket, quality.SLUG)
    current = loaded.payload
    expected = deepcopy(current)
    evidence = plan[KEY]["manifest"]
    entries = [
        entry
        for release in expected["releases"]
        if release["date"] == plan[KEY]["release"]
        for entry in release["files"]
        if entry["path"] == evidence["path"]
    ]
    ids.require(
        len(entries) == 1
        and str(entries[0]["generation"]) == evidence["generation"]
        and entries[0].get("sha256")
        in {evidence["metadata"]["sha256"], evidence["sha256"]},
        "indexed historical manifest differs from reviewed evidence",
    )
    entries[0]["sha256"] = evidence["sha256"]
    # The rebuilder can omit an older indexed checksum absent from both the run
    # record and object labels. Preserve it only after independently hashing its
    # generation-pinned bytes; never drop an existing checksum as a side effect.
    by_path = {
        entry["path"]: entry
        for release in expected["releases"]
        for entry in release["files"]
    }
    for release in rebuilt["releases"]:
        for entry in release["files"]:
            previous = by_path.get(entry["path"], {})
            if "sha256" in previous and "sha256" not in entry:
                raw = quality.pinned(client, previous)
                ids.require(
                    quality.digest(raw) == previous["sha256"],
                    "unverified indexed checksum cannot be retained",
                )
                entry["sha256"] = previous["sha256"]
    ids.require(
        {k: v for k, v in rebuilt.items() if k != "updated_at"}
        == {k: v for k, v in expected.items() if k != "updated_at"},
        "checksum repair would change indexed releases or latest pointers",
    )
    if expected != current:
        expected["updated_at"] = release_index.utc_now_iso()
        release_index.write_release_index(
            guard.bucket, quality.SLUG, expected, generation=loaded.generation
        )
    print(
        "Strict index rebuild verified; only the reviewed historical checksum was repaired."
    )


def verify_finished(guard, plan):
    from ingestion.common import release_index

    guard.before = quality.baseline(guard.bucket)
    manifest = validate_live(guard, plan)
    ids.require(
        manifest.metadata["sha256"] == plan[KEY]["manifest"]["sha256"],
        "checksum label repair is incomplete",
    )
    item = plan["promotions"][0]
    ids.require(
        quality.already_promoted(guard.bucket.client, item, plan),
        "corrected historical run is not published",
    )
    index = release_index.load_release_index(guard.bucket, quality.SLUG).payload
    refs = [
        entry
        for release in index["releases"]
        if release["date"] == plan[KEY]["release"]
        for entry in release["files"]
        if entry["path"] == plan[KEY]["manifest"]["path"]
    ]
    ids.require(
        len(refs) == 1
        and refs[0]["sha256"] == plan[KEY]["manifest"]["sha256"]
        and str(refs[0]["generation"]) == plan[KEY]["manifest"]["generation"]
        and refs[0]["size"] == plan[KEY]["manifest"]["size"],
        "indexed checksum repair is incomplete",
    )
