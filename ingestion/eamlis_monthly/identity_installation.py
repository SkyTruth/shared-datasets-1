"""Explicit, reviewed installation of EAMLIS's permanent source-key ledger.

The immutable marker precedes the control CAS. An interrupted installation then
blocks legacy writers; exact retries can finish without rewinding allocations.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
import re

from ingestion.eamlis_monthly import identity as ids, identity_state as state, source_quality as quality

URI = f"gs://{quality.BUCKET}/"
CONTROL_URI = URI + quality.CONTROL
MARKER_URI = URI + state.MARKER


def date(value):
    ids.require(isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value), "invalid identity release date")
    try:
        dt.date.fromisoformat(value)
    except ValueError as exc:
        raise ids.IdentityError("invalid identity release date") from exc
    return value


def sha(value):
    ids.require(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value), "invalid identity SHA256")
    return value


def snapshot(value):
    state.reference(value, ledger=False)
    ids.require(value["path"].startswith(URI), "identity evidence must be in the shared bucket")
    return value


def same_snapshot(left, right):
    return all(str(left[key]) == str(right[key]) for key in ("path", "generation", "size", "sha256"))


def validate_marker(marker):
    fields = {"schema_version", "contract_id", "installation_id", "ledger_sha256", "control_source", "legacy_control",
              "baseline", "first_release", "sources", "metadata_source", "translation_source"}
    ids.require(isinstance(marker, dict) and set(marker) == fields and type(marker["schema_version"]) is int
                and marker["schema_version"] == 1 and marker["contract_id"] == ids.CONTRACT, "invalid identity installation marker")
    sha(marker["ledger_sha256"])
    value = {key: item for key, item in marker.items() if key != "installation_id"}
    ids.require(marker["installation_id"] == quality.digest(quality.encode(value)), "installation marker hash changed")
    snapshot(marker["control_source"])
    ids.require(marker["control_source"]["path"] == CONTROL_URI, "installation must replace the existing control")
    legacy = quality.validate_control(marker["legacy_control"])
    ids.require(legacy["schema_version"] == 1 and legacy["owner"] is None
                and quality.digest(quality.encode(legacy)) == marker["control_source"]["sha256"], "legacy control evidence differs from captured state")
    baseline = marker["baseline"]
    ids.require(isinstance(baseline, dict) and set(baseline) == {"release", "token", "index"}
                and baseline["token"] == quality.digest(quality.encode(baseline["release"])), "invalid initial baseline")
    snapshot(baseline["index"])
    ids.require(baseline["index"]["path"] == URI + quality.INDEX, "wrong identity baseline index")
    release = baseline["release"]
    date(release["date"])
    ids.require(date(marker["first_release"]) > release["date"], "cutover requires a new dated release")
    ids.require(isinstance(marker["sources"], list) and marker["sources"], "installation requires historical source evidence")
    dates = []
    for item in marker["sources"]:
        ids.require(isinstance(item, dict) and set(item) == {"date", "format", "rows", "path", "generation", "size", "sha256"}, "invalid audited source")
        dates.append(date(item["date"]))
        snapshot({key: item[key] for key in ("path", "generation", "size", "sha256")})
        ids.require(item["format"] in {"fgb", "metadata"} and type(item["rows"]) is int and item["rows"] > 0, "invalid audited source format/count")
        suffix = ".fgb" if item["format"] == "fgb" else ".metadata.ndjson.gz"
        ids.require(item["path"] == f"{URI}{quality.ROOT}/releases/{item['date']}/{quality.SLUG}{suffix}", "audited source must be a concrete EAMLIS release")
    ids.require(dates == sorted(set(dates)) and dates[-1] == release["date"], "audit must end at the current baseline without repeated dates")
    for key, suffix in (("metadata_source", ".metadata.ndjson.gz"), ("translation_source", ".metadata-translations.csv")):
        snapshot(marker[key])
        ids.require(marker[key]["path"] == f"{URI}{quality.ROOT}/releases/{release['date']}/{quality.SLUG}{suffix}", "migration inputs require the concrete current release")
    ids.require(any(same_snapshot(marker["metadata_source"], f)
                for f in release["files"] if f["path"] == marker["metadata_source"]["path"]),
                "canonical metadata must match the current release index")
    return marker


def read_marker(bucket):
    raw, _ = quality.read(bucket, state.MARKER)
    ids.require(raw is not None, "identity installation marker is missing")
    return validate_marker(json.loads(raw))


def validate_plan(plan):
    ids.require(plan["asset_slug"] == quality.SLUG and not plan.get("breaking_changes")
                and not plan.get("release_index_asset_slugs")
                and "identity_reset" not in plan and "source_loss_review" not in plan, "identity installation cannot include other mutations")
    value = plan["identity_transition"]
    fields = {"schema_version", "contract_id", "installation_id", "first_release", "baseline_token",
              "ledger_sha256", "marker_sha256", "control_sha256"}
    ids.require(isinstance(value, dict) and set(value) == fields and type(value["schema_version"]) is int
                and value["schema_version"] == 1 and value["contract_id"] == ids.CONTRACT, "invalid reviewed identity transition")
    date(value["first_release"])
    for key in fields - {"schema_version", "contract_id", "first_release"}:
        sha(value[key])
    targets = {URI + state.LEDGERS + value["ledger_sha256"] + ".json", MARKER_URI, CONTROL_URI}
    items = plan["promotions"]
    ids.require(len(items) == 3 and {p["destination_uri"] for p in items} == targets, "installation requires exactly ledger, marker and control promotions")
    for item in items:
        ids.require(item.get("content_type") == "application/json" and item.get("cache_control") == quality.NO_CACHE
                    and not item.get("compatibility_waiver"), "installation requires JSON/no-cache and no compatibility waiver")
        expected = item["destination_generation"]
        ids.require(bool(re.fullmatch(r"[1-9][0-9]*", str(expected))) if item["destination_uri"] == CONTROL_URI else expected == "",
                    "installation requires an existing control and create-only immutable evidence")
    return value


def desired_control(legacy, marker, ledger_ref):
    ids.require(legacy["schema_version"] == 1 and legacy["owner"] is None, "pause and drain the legacy writer before installation")
    quality.validate_control(legacy)
    return {**legacy, "schema_version": 2, "identity": {
        "schema_version": 1, "contract_id": ids.CONTRACT, "installation_id": marker["installation_id"],
        "initial_baseline_token": marker["baseline"]["token"], "first_release": marker["first_release"],
        "phase": "prepared", "ledger": ledger_ref, "initial_ledger": ledger_ref, "activation": None}}


def source_refs(marker):
    return [{key: item[key] for key in ("path", "generation", "size", "sha256")} for item in marker["sources"]]


def migration_inputs(client, baseline):
    from scripts import release_feature_model as model
    release = baseline["release"]
    files = release["files"]
    refs = [f for f in files if f["path"].endswith(".manifest.json")]
    ids.require(len(refs) == 1, "baseline requires one concrete manifest")
    manifest = json.loads(quality.pinned(client, refs[0]))
    artifacts = model.validate_release_manifest(manifest, expected_asset_slug=quality.SLUG,
                                                expected_release=release["date"], require_generations=True)
    ids.require(manifest["identity"]["strategy"] == "source_field" and manifest["identity"]["source_fields"] == ["OBJECTID"],
                "cutover requires the deployed legacy OBJECTID baseline")
    result = {}
    for key, suffix in (("metadata_source", ".metadata.ndjson.gz"), ("translation_source", ".metadata-translations.csv")):
        matches = [f for f in files if f["path"].endswith(suffix)]
        entries = [entry for entry in manifest["artifacts"] if entry["path"].endswith(suffix)]
        ids.require(len(matches) == len(entries) == 1, "baseline lacks a unique concrete migration input: " + suffix)
        entry, indexed = entries[0], matches[0]
        ids.require(all(str(entry[k]) == str(indexed[k]) for k in ("path", "generation", "size")), "migration input differs from baseline index")
        result[key] = {k: entry[k] for k in ("path", "generation", "size", "sha256")}
        result[key]["sha256"] = result[key]["sha256"].removeprefix("sha256:")
    ids.require(artifacts["metadata"]["path"] == result["metadata_source"]["path"], "ambiguous canonical metadata")
    return result


def verify_replay(client, marker, ledger_bytes, directory):
    from scripts.eamlis_identity_audit import replay
    index = json.loads(quality.pinned(client, marker["baseline"]["index"]))
    ids.require(all(marker[key] == ref for key, ref in migration_inputs(client, marker["baseline"]).items()), "migration inputs differ from the canonical legacy manifest")
    releases = {row["date"]: row for row in index["releases"]}
    ids.require(set(releases) == {item["date"] for item in marker["sources"]}, "audit must cover every indexed historical release")
    for item, ref in zip(marker["sources"], source_refs(marker)):
        release = releases[item["date"]]
        ids.require(item["rows"] == release["rows"] and any(same_snapshot(ref, f)
                     for f in release["files"] if f["path"] == item["path"]),
                    "audited source differs from release-index evidence")
    inventory = []
    for n, (item, ref) in enumerate(zip(marker["sources"], source_refs(marker))):
        path = directory / f"source-{n}"
        path.write_bytes(quality.pinned(client, ref))
        inventory.append({**item, "local": str(path)})
    _report, ledger = replay(inventory)
    ids.require(ids.encoded(ledger) == ledger_bytes, "proposed ledger differs from complete audited replay")
    # Verify the translation snapshot too; re-keying happens at the producer.
    quality.pinned(client, marker["metadata_source"])
    quality.pinned(client, marker["translation_source"])
    return ledger


def install(client, plan, *, check_authority_and_jobs, directory):
    value = validate_plan(plan)
    bucket = client.bucket(quality.BUCKET)
    check_authority_and_jobs()
    data = {}
    for item in plan["promotions"]:
        destination = item["destination_uri"]
        kind = "control" if destination == CONTROL_URI else "marker" if destination == MARKER_URI else "ledger"
        blob = bucket.blob(item["source_uri"][len(URI):], generation=int(item["source_generation"]))
        raw = blob.download_as_bytes(if_generation_match=int(item["source_generation"]))
        ids.require(quality.digest(raw) == value[kind + "_sha256"], "reviewed installation source hash changed")
        data[kind] = raw
    marker = validate_marker(json.loads(data["marker"]))
    ids.require(all(marker[k] == value[k] for k in ("contract_id", "installation_id", "first_release", "ledger_sha256"))
                and marker["baseline"]["token"] == value["baseline_token"], "plan differs from immutable marker")
    ledger = ids.Ledger.parse(json.loads(data["ledger"]))
    legacy = marker["legacy_control"]
    ledger_path = state.LEDGERS + value["ledger_sha256"] + ".json"
    placeholder = {"path": URI + ledger_path, "generation": "0", "size": len(data["ledger"]), "sha256": value["ledger_sha256"]}
    expected_control = desired_control(legacy, marker, placeholder)
    ids.require(data["control"] == quality.encode(expected_control), "reviewed control template changed")
    control_item = next(p for p in plan["promotions"] if p["destination_uri"] == CONTROL_URI)
    ids.require(str(marker["control_source"]["generation"]) == control_item["destination_generation"], "control precondition differs from reviewed snapshot")
    current_raw, current_ref = quality.read(bucket, quality.CONTROL)
    ids.require(current_raw is not None, "missing control cannot be recreated")
    current = quality.validate_control(json.loads(current_raw))
    if current["schema_version"] == 2:
        guard = quality.Guard(bucket)
        guard.state, guard.generation, guard.before = current, int(current_ref["generation"]), quality.baseline(bucket)
        ids.require(current["owner"] is None and current["identity"]["installation_id"] == marker["installation_id"], "another owner/installation is selected")
        selected = state.selected_ledger(guard)
        selected.require_extension_of(ledger)
        return {"installation_id": marker["installation_id"], "status": "already_installed"}
    verify_replay(client, marker, data["ledger"], directory)
    ids.require(current_ref == marker["control_source"] and current_raw == quality.encode(legacy), "legacy control changed; obtain a new review")
    ids.require(quality.baseline(bucket) == marker["baseline"], "published baseline/index changed; obtain a new review")
    release = marker["first_release"]
    for prefix in (f"{quality.ROOT}/releases/{release}/", f"{quality.ROOT}/runs/{release}.json"):
        ids.require(not list(client.list_blobs(quality.BUCKET, prefix=prefix)), "first release date is already occupied")
    refs = {}
    for kind, name in (("ledger", ledger_path), ("marker", state.MARKER)):
        check_authority_and_jobs()
        raw, ref = quality.read(bucket, name)
        if raw is not None:
            ids.require(raw == data[kind], "immutable installation object changed")
        else:
            ref = quality.put(bucket, name, data[kind], generation=0)
        refs[kind] = ref
    prepared = desired_control(legacy, marker, refs["ledger"])
    check_authority_and_jobs()
    ids.require(quality.baseline(bucket) == marker["baseline"], "baseline changed during installation")
    quality.put(bucket, quality.CONTROL, quality.encode(prepared), generation=int(current_ref["generation"]))
    return {"installation_id": marker["installation_id"], "status": "prepared", "first_release": release}


def prepare(client, inventory, *, first_release, directory: Path, proposal_id):
    """Read current controls and stage only the three declared review sources."""
    from scripts.eamlis_identity_audit import replay
    from scripts.reviewed_dataset_plan import normalize_publish_plan
    bucket = client.bucket(quality.BUCKET)
    raw, control_ref = quality.read(bucket, quality.CONTROL)
    ids.require(raw is not None, "run the deployed legacy guard once before preparing an installation")
    legacy = quality.validate_control(json.loads(raw))
    baseline = quality.baseline(bucket)
    ids.require(baseline is not None, "identity cutover requires a complete published baseline")
    _report, ledger = replay(inventory)
    ledger_bytes = ids.encoded(ledger)
    marker = {"schema_version": 1, "contract_id": ids.CONTRACT, "ledger_sha256": quality.digest(ledger_bytes),
        "control_source": control_ref, "legacy_control": legacy, "baseline": baseline, "first_release": first_release,
        "sources": sorted([{key: item[key] for key in ("date", "format", "rows", "path", "generation", "size", "sha256")}
                            for item in inventory if item["format"] != "manifest"], key=lambda item: item["date"]),
        **migration_inputs(client, baseline)}
    marker["installation_id"] = quality.digest(quality.encode(marker))
    validate_marker(marker)
    verify_replay(client, marker, ledger_bytes, directory)
    ledger_uri = URI + state.LEDGERS + marker["ledger_sha256"] + ".json"
    placeholder = {"path": ledger_uri, "generation": "0", "size": len(ledger_bytes), "sha256": marker["ledger_sha256"]}
    control = desired_control(legacy, marker, placeholder)
    payloads = {"ledger": ledger_bytes, "marker": quality.encode(marker), "control": quality.encode(control)}
    plan = {"asset_slug": quality.SLUG, "proposal_id": proposal_id, "promotions": [],
            "identity_transition": {"schema_version": 1, "contract_id": ids.CONTRACT,
            "installation_id": marker["installation_id"], "first_release": first_release, "baseline_token": baseline["token"],
            **{kind + "_sha256": quality.digest(data) for kind, data in payloads.items()}}}
    # Validate proposal/source scope before the first scratch upload.
    from scripts.reviewed_dataset_plan import require_slug_and_proposal
    require_slug_and_proposal(plan)
    for kind, destination in (("ledger", ledger_uri), ("marker", MARKER_URI), ("control", CONTROL_URI)):
        path = directory / (kind + ".json")
        path.write_bytes(payloads[kind])
        ref = quality.immutable_file(bucket, f"_scratch/pending-publishes/{quality.SLUG}/{proposal_id}/{kind}.json", path)
        plan["promotions"].append({"source_uri": ref["path"], "source_generation": ref["generation"],
            "destination_uri": destination, "destination_generation": str(control_ref["generation"]) if kind == "control" else "",
            "content_type": "application/json", "cache_control": quality.NO_CACHE})
    return normalize_publish_plan(plan)
