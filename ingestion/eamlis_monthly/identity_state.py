"""EAMLIS identity state selected atomically by the existing publication owner."""
from __future__ import annotations

from dataclasses import dataclass
import datetime as dt
import json
from pathlib import Path
import re

from ingestion.eamlis_monthly import identity

ROOT = "300-infrastructure-industrial/320-mining/eamlis-abandoned-mine-land-inventory"
SLUG = "eamlis-abandoned-mine-land-inventory"
BUCKET = "skytruth-shared-datasets-1"
LEDGERS = f"{ROOT}/publications/identity-ledgers/"
MARKER = f"{ROOT}/publications/identity-installation.json"


def reference(value, *, ledger=True):
    identity.require(isinstance(value, dict) and set(value) == {"path", "generation", "size", "sha256"}, "invalid identity snapshot reference")
    identity.require(re.fullmatch(r"[1-9][0-9]*", str(value["generation"])) is not None
        and type(value["size"]) is int and value["size"] > 0
        and isinstance(value["sha256"], str) and re.fullmatch(r"[0-9a-f]{64}", value["sha256"]), "invalid identity snapshot evidence")
    if ledger:
        identity.require(value["path"] == f"gs://{BUCKET}/{LEDGERS}{value['sha256']}.json", "identity ledger must use its immutable content-addressed path")
    return value


def validate(value):
    fields = {"schema_version", "contract_id", "installation_id", "initial_baseline_token", "first_release",
              "phase", "ledger", "initial_ledger", "activation"}
    identity.require(isinstance(value, dict) and set(value) == fields and type(value["schema_version"]) is int and value["schema_version"] == 1
        and value["contract_id"] == identity.CONTRACT and value["phase"] in {"prepared", "active"}, "invalid installed identity state")
    for key in ("installation_id", "initial_baseline_token"):
        identity.require(isinstance(value[key], str) and re.fullmatch(r"[0-9a-f]{64}", value[key]), "invalid identity installation binding")
    try:
        dt.date.fromisoformat(value["first_release"])
    except (TypeError, ValueError) as exc:
        raise identity.IdentityError("invalid first identity release date") from exc
    reference(value["ledger"])
    reference(value["initial_ledger"])
    activation = value["activation"]
    if value["phase"] == "prepared":
        identity.require(activation is None, "prepared identity cannot claim publication completion")
    else:
        identity.require(isinstance(activation, dict) and set(activation) == {"release", "manifest"}, "active identity requires first publication evidence")
        reference(activation["manifest"], ledger=False)
        identity.require(activation["release"] == value["first_release"] and activation["manifest"]["path"] ==
            f"gs://{BUCKET}/{ROOT}/releases/{activation['release']}/{SLUG}.manifest.json", "invalid identity activation release")
    return value


def ledger_for(guard, ref):
    from ingestion.eamlis_monthly import source_quality as quality
    reference(ref)
    return identity.Ledger.parse(json.loads(quality.pinned(guard.bucket.client, ref)))


def selected_ledger(guard):
    from ingestion.eamlis_monthly import source_quality as quality
    value = guard.state.get("identity")
    marker_data, _ = quality.read(guard.bucket, MARKER)
    if marker_data is None:
        identity.require(value is None, "identity state has no immutable installation evidence")
        identity.require(not guard.before or guard.before["release"].get("identity_contract") != identity.CONTRACT,
                         "published identity contract lost its installed state")
        return None
    from ingestion.eamlis_monthly.identity_installation import validate_marker
    marker = validate_marker(json.loads(marker_data))
    identity.require(value is not None, "identity installation exists but its selected state is missing; reviewed recovery required")
    validate(value)
    identity.require(marker["installation_id"] == value["installation_id"]
                     and marker["ledger_sha256"] == value["initial_ledger"]["sha256"]
                     and marker["first_release"] == value["first_release"]
                     and marker["baseline"]["token"] == value["initial_baseline_token"], "identity installation evidence changed")
    ledger = ledger_for(guard, value["ledger"])
    initial = ledger if value["ledger"] == value["initial_ledger"] else ledger_for(guard, value["initial_ledger"])
    ledger.require_extension_of(initial)
    contract = guard.before["release"].get("identity_contract") if guard.before else None
    if value["phase"] == "active":
        identity.require(contract == identity.CONTRACT, "current release no longer belongs to the installed identity contract")
    elif contract != identity.CONTRACT:
        identity.require(quality.baseline_token(guard.before) == value["initial_baseline_token"], "identity cutover baseline changed")
    else:
        # A completed data/index commit can outlive a lost activation response.
        activate(guard)
    return ledger


@dataclass(frozen=True)
class Reservation:
    ledger: identity.Ledger
    reference: dict
    before: int
    previous_release: str | None

    @property
    def metadata(self):
        value = identity.metadata(previous_release=self.previous_release, before=self.before, after=self.ledger.next_feature_id)
        value["allocation_ledger"] = dict(self.reference)
        return value


def reserve(guard, properties, directory: Path) -> Reservation | None:
    """Persist the selected immutable mapping before a builder can consume IDs."""
    from ingestion.eamlis_monthly import source_quality as quality
    guard.require_owner()
    ledger = selected_ledger(guard)
    if ledger is None:
        return None
    rows = identity.classify_release(properties)
    proposed = ledger.reserve(rows)
    value = guard.state["identity"]
    if proposed != ledger:
        path = directory / "identity-ledger.json"
        path.write_bytes(identity.encoded(proposed))
        sha = quality.digest(path.read_bytes())
        ref = quality.immutable_file(guard.bucket, f"{LEDGERS}{sha}.json", path)
        ref.pop("content_type")
        value["ledger"] = ref
        guard.save()  # A failed CAS cannot expose the proposed IDs.
    previous = guard.before["release"]["date"] if value["phase"] == "active" else None
    return Reservation(proposed, dict(value["ledger"]), ledger.next_feature_id, previous)


def require_run_date(guard, date, record=None):
    value = guard.state.get("identity")
    if value is None:
        return
    if value["phase"] == "prepared":
        identity.require(date == value["first_release"], "first identity publication requires its reviewed RUN_DATE")
    if record is not None:
        identity.require(record.get("identity_contract") == identity.CONTRACT, "cannot reactivate a legacy run in the new identity contract")


def validate_records(ledger, records):
    from scripts import release_feature_model as model
    records = tuple(records)
    identity.require(model.validate_sidecar_records(records).valid, "published metadata violates the sidecar contract")
    typed = identity.classify_release(row["properties"] for row in records)
    identity.require(ledger.reserve(typed) == ledger, "published metadata contains unreserved identities")
    assignments = ledger.ids
    for record, key in zip(records, typed):
        identity.require(record["feature_id"] == assignments[key.key] and tuple(record["identity_key"]) == key.key,
                         "published metadata does not match the permanent identity mapping")
        identity.require(record["properties"].get(identity.KIND_FIELD) == key.kind
                         and record["properties"].get(identity.KEY_FIELD) == key.source_key,
                         "published assignment columns differ from their exact source keys")
        identity.require(record["properties_hash"] == model.properties_hash(record["properties"], exclude_properties=identity.EXCLUDED_PROPERTIES),
                         "published metadata property hash differs from identity semantics")
    return records


def validate_candidate(guard, candidate):
    """Review cannot replace the installed contract or its reserved assignments."""
    from ingestion.eamlis_monthly import source_quality as quality
    from scripts import release_feature_model as model
    manifest_ref = next(ref for ref in candidate["files"] if ref["role"] == "manifest")
    manifest = json.loads(quality.pinned(guard.bucket.client, manifest_ref))
    model.validate_release_manifest(manifest, expected_asset_slug=SLUG, expected_release=candidate["release"], require_generations=True)
    descriptor = manifest["identity"]
    value = guard.state.get("identity")
    if value is None:
        identity.require("contract_id" not in descriptor, "candidate cannot install an unreviewed identity contract")
        return
    require_run_date(guard, candidate["release"])
    identity.require(descriptor.get("contract_id") == identity.CONTRACT
                     and descriptor["source_fields"] == list(identity.ASSIGNMENT_FIELDS)
                     and descriptor["properties_hash_excluded_properties"] == list(identity.EXCLUDED_PROPERTIES),
                     "candidate differs from installed identity semantics")
    ledger = ledger_for(guard, descriptor["allocation_ledger"])
    ledger.require_extension_of(ledger_for(guard, value["initial_ledger"]))
    ledger_for(guard, value["ledger"]).require_extension_of(ledger)
    identity.require(descriptor["next_generated_feature_id_after_release"] == ledger.next_feature_id, "candidate has inconsistent allocation high-water")
    metadata_ref = next(ref for ref in candidate["files"] if ref["role"] == "metadata")
    records = validate_records(ledger, model.read_metadata_sidecar_bytes(quality.pinned(guard.bucket.client, metadata_ref)))
    identity.require(len(records) == candidate["geometry_count"] == manifest["validation"]["feature_count"], "candidate identity counts differ")
    run_ref = next(ref for ref in candidate["files"] if ref["role"] == "run_record")
    record = json.loads(quality.pinned(guard.bucket.client, run_ref))
    identity.require(record.get("identity_contract") == identity.CONTRACT, "candidate run omits its identity contract")


def require_metadata_only(guard, plan):
    """Allow one README or a complete translation bundle, never feature replacement."""
    from ingestion.eamlis_monthly import source_quality as quality
    from scripts import release_feature_model as model
    value = guard.state.get("identity")
    if value is None:
        return
    identity.require(value["phase"] == "active", "first identity publication requires the reviewed RUN_DATE job or held candidate")
    items = plan["promotions"]
    identity.require(not plan.get("release_index_asset_slugs"), "metadata-only update cannot rebuild the release index")
    if len(items) == 1 and items[0]["destination_uri"] == f"gs://{BUCKET}/{ROOT}/README.md":
        return
    release = guard.before["release"]["date"]
    suffixes = (".manifest.json", ".metadata-translations.csv", ".metadata.es.ndjson.gz")
    targets = {f"gs://{BUCKET}/{ROOT}/{folder}/{SLUG}{suffix}"
               for folder in (f"releases/{release}", "latest") for suffix in suffixes}
    identity.require(len(items) == len(targets) and {item["destination_uri"] for item in items} == targets,
                     "installed EAMLIS accepts only its README or the complete current translation bundle without a held-candidate review")
    old_ref = next(ref for ref in guard.before["release"]["files"] if ref["path"].endswith(".manifest.json"))
    old = json.loads(quality.pinned(guard.bucket.client, old_ref))
    for item in items:
        if item["destination_uri"].endswith(".manifest.json"):
            blob = guard.bucket.blob(item["source_uri"].split("/", 3)[3], generation=int(item["source_generation"]))
            proposed = json.loads(blob.download_as_bytes(if_generation_match=int(item["source_generation"])))
            model.validate_release_manifest(proposed, expected_asset_slug=SLUG, expected_release=release, require_generations=True)
            identity.require(proposed["identity"] == old["identity"] and proposed["hashes"] == old["hashes"],
                             "translation update changed identity or hash semantics")
            def core(manifest):
                return [entry for entry in manifest["artifacts"] if entry["role"] in {"fgb", "pmtiles", "metadata", "schema"}]
            identity.require(core(proposed) == core(old) and proposed["validation"]["feature_count"] == old["validation"]["feature_count"], "translation update changed canonical feature artifacts")


def activate(guard):
    """Confirm complete canonical evidence before marking first publication done."""
    from ingestion.eamlis_monthly import source_quality as quality
    from scripts import release_feature_model as model
    current = quality.baseline(guard.bucket)
    identity.require(current is not None and current["release"].get("identity_contract") == identity.CONTRACT,
                     "new identity release is not committed in the catalog index")
    release = current["release"]
    refs = [ref for ref in release["files"] if ref["path"].endswith(".manifest.json")]
    identity.require(len(refs) == 1, "published release requires one manifest")
    manifest_ref = refs[0]
    manifest = json.loads(quality.pinned(guard.bucket.client, manifest_ref))
    artifacts = model.validate_release_manifest(manifest, expected_asset_slug=SLUG, expected_release=release["date"], require_generations=True)
    metadata = manifest["identity"]
    identity.require(metadata["contract_id"] == identity.CONTRACT and metadata["properties_hash_excluded_properties"] == list(identity.EXCLUDED_PROPERTIES),
                     "published manifest has different identity/hash semantics")
    ledger_ref = reference(metadata["allocation_ledger"])
    ledger = ledger_for(guard, ledger_ref)
    current_ledger = ledger_for(guard, guard.state["identity"]["ledger"])
    current_ledger.require_extension_of(ledger)
    identity.require(metadata["next_generated_feature_id_after_release"] == ledger.next_feature_id, "manifest allocation high-water is inconsistent")
    for entry in manifest["artifacts"]:
        if entry["role"] != "manifest":
            quality.pinned(guard.bucket.client, {**entry, "sha256": entry["sha256"].removeprefix("sha256:")})
    data = quality.pinned(guard.bucket.client, {**artifacts["metadata"], "sha256": artifacts["metadata"]["sha256"].removeprefix("sha256:")})
    import gzip
    records = validate_records(ledger, (json.loads(line) for line in gzip.decompress(data).splitlines()))
    identity.require(len(records) == release["rows"] == manifest["validation"]["feature_count"], "published identity row counts differ")
    value = guard.state["identity"]
    if value["phase"] == "prepared":
        identity.require(release["date"] == value["first_release"], "first identity release differs from its reviewed date")
        value.update(phase="active", activation={"release": release["date"],
            "manifest": {key: manifest_ref[key] for key in ("path", "generation", "size", "sha256")}})
        guard.save()
