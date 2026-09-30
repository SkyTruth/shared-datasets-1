"""Opt-in publication v1 core. No existing writer is migrated by importing this.

Authorization and semantic bundle validation belong to trusted adapters. This
module owns immutable intents, ownership, fixed object CAS, and crash recovery.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Protocol

from scripts.release_feature_model import validate_identity_contract

VERSION = 1
FINALIZATION_VERSION = "finalize-promoted-release-v1"
HEX64 = re.compile(r"[0-9a-f]{64}")
SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
INPUT_NAME = re.compile(r"(?:0|[1-9][0-9]*)\.(?:fgb|pmtiles|geojson|ndgeojson|csv|tif|tiff|md|schema\.json|manifest\.json|metadata(?:\.[a-z]{2,3}(?:_[a-z0-9]{2,8})*)?\.ndjson\.gz|catalog\.json)")
GLOBAL_TARGETS = {"_catalog/shared-datasets-catalog.csv", "_catalog/web/catalog.json"}
PHASES = ("checkpoint", "data", "commit", "asset_derived", "global_derived")
MODES = {"complete_release", "metadata_update", "object_repair", "catalog_update"}


class PublicationError(RuntimeError):
    pass


class Conflict(PublicationError):
    """CAS/ownership conflict; never refresh the planned object expectation."""


class NotRecoverableSource(PublicationError):
    """Required approved bytes are absent; an existing reservation stays held."""


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def strict_json(data: bytes | str) -> Any:
    def pairs(items):
        value = {}
        for key, item in items:
            require(key not in value, f"duplicate JSON key: {key}")
            value[key] = item
        return value

    def constant(token):
        raise PublicationError(f"nonfinite JSON value: {token}")

    def floating(token):
        value = float(token)
        require(math.isfinite(value), "nonfinite JSON number")
        return value

    return json.loads(data, object_pairs_hook=pairs, parse_constant=constant, parse_float=floating)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise PublicationError(message)


def integer(value: Any, minimum: int = 0) -> bool:
    return type(value) is int and value >= minimum


def keys(value: Any, expected: set[str], label: str) -> None:
    require(isinstance(value, dict) and set(value) == expected, f"invalid {label} fields")


def hash_value(value: Any) -> bool:
    return isinstance(value, str) and bool(HEX64.fullmatch(value))


def split_uri(uri: str) -> tuple[str, str]:
    require(isinstance(uri, str) and uri.startswith("gs://"), "expected gs:// object URI")
    bucket, slash, name = uri[5:].partition("/")
    require(bool(bucket and slash and name) and all(part not in {"", ".", ".."} for part in name.split("/")), "invalid object URI")
    require(not any(char in uri for char in "?#\\\n\r"), "invalid object URI characters")
    return bucket, name


def protocol_path_kind(name: str) -> str | None:
    """Exact layout classifier; None includes malformed operational paths."""
    parts = name.split("/")
    if parts[:2] == ["_catalog", "publications"]:
        return "receipt" if len(parts) == 4 and parts[2] == "receipts" and re.fullmatch(r"[0-9a-f]{64}\.json", parts[3]) else None
    if len(parts) < 5 or parts[3] != "publications" or not all(SLUG.fullmatch(part) for part in parts[:3]):
        return None
    tail = parts[4:]
    if tail == ["state.json"]:
        return "state"
    if tail == ["reset.json"]:
        return "reset"
    if len(tail) == 2 and tail[0] == "receipts" and re.fullmatch(r"[0-9a-f]{64}\.json", tail[1]):
        return "receipt"
    if len(tail) == 3 and tail[0] == "inputs" and hash_value(tail[1]) and INPUT_NAME.fullmatch(tail[2]):
        return "checkpoint"
    return None


def is_protocol_namespace(name: str) -> bool:
    parts = name.split("/")
    return parts[:2] == ["_catalog", "publications"] or (
        len(parts) > 3 and parts[3] == "publications" and all(SLUG.fullmatch(part) for part in parts[:3])
    )


@dataclass(frozen=True)
class Context:
    """Normalized already-authorized E/B adapter input, not an approval verifier."""
    proposal_key: str
    authorization_digest: str
    trusted_executor_sha: str
    finalization_version: str
    bucket: str
    asset_root: str | None
    asset_slug: str | None
    identity_contract: str | None = None

    def __post_init__(self) -> None:
        if self.identity_contract is not None:
            validate_identity_contract(self.identity_contract)
        require(hash_value(self.proposal_key) and hash_value(self.authorization_digest), "invalid proposal/authorization digest")
        require(isinstance(self.trusted_executor_sha, str) and bool(re.fullmatch(r"[0-9a-f]{40}", self.trusted_executor_sha)), "invalid executor SHA")
        require(self.finalization_version == FINALIZATION_VERSION, "unsupported finalization version")
        require(isinstance(self.bucket, str) and bool(re.fullmatch(r"[a-z0-9][a-z0-9._-]*", self.bucket)), "invalid bucket")
        if self.asset_root is None:
            require(self.asset_slug is None, "global context cannot name an asset")
            require(self.identity_contract is None, "global context cannot name an identity contract")
        else:
            require(isinstance(self.asset_root, str), "invalid asset root")
            parts = self.asset_root.split("/")
            require(len(parts) == 3 and all(SLUG.fullmatch(part) for part in parts) and parts[-1] == self.asset_slug, "asset root must be catalog-resolved category/subcategory/slug")

    @property
    def receipt_uri(self) -> str:
        return f"gs://{self.bucket}/{self.asset_root or '_catalog'}/publications/receipts/{self.proposal_key}.json"

    @property
    def state_uri(self) -> str:
        require(self.asset_root is not None, "global catalog has no asset state")
        return f"gs://{self.bucket}/{self.asset_root}/publications/state.json"


def target_class(context: Context, uri: str) -> str:
    bucket, name = split_uri(uri)
    require(bucket == context.bucket, "destination is outside the publication bucket")
    if name in GLOBAL_TARGETS:
        return "global"
    if context.asset_root is not None:
        if name in {f"_catalog/releases/{context.asset_slug}.json", f"_catalog/schema-snapshots/{context.asset_slug}.json"}:
            return "protected"
        if name.startswith(context.asset_root + "/"):
            relative = name[len(context.asset_root) + 1:]
            if relative.startswith("publications/"):
                require(protocol_path_kind(name) == "checkpoint" and relative.split("/")[2] == context.proposal_key, "operation cannot write publication control objects")
                return "checkpoint"
            require(relative == "README.md" or relative.startswith(("latest/", "releases/", "runs/")), "unsupported asset destination")
            return "asset"
    raise PublicationError("destination is outside the one asset root or known global catalog targets")


def valid_date(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return dt.date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def receipt_context(value: Any) -> Context:
    require(isinstance(value, dict) and isinstance(value.get("intent"), dict), "invalid referenced receipt")
    candidate = value["intent"].get("context")
    keys(candidate, set(Context.__dataclass_fields__), "referenced context")
    return Context(**candidate)


def identity_snapshot(value: Any) -> None:
    """Accept exactly dataclasses.asdict(B.GeneratedIdentitySnapshot)."""
    keys(value, {"path", "generation", "sha256"}, "identity snapshot")
    split_uri(value["path"])
    require(integer(value["generation"], 1) and hash_value(value["sha256"]), "invalid identity snapshot")


@dataclass(frozen=True)
class ObjectVersion:
    path: str
    generation: int
    sha256: str
    size: int
    content_type: str = ""
    cache_control: str = ""
    metadata: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        split_uri(self.path)
        require(integer(self.generation, 1) and integer(self.size) and hash_value(self.sha256), "invalid object version")
        require(isinstance(self.content_type, str) and isinstance(self.cache_control, str), "invalid object content metadata")
        require(all(isinstance(k, str) and isinstance(v, str) for k, v in self.metadata), "invalid object tags")

    def record(self) -> dict[str, Any]:
        return {**asdict(self), "metadata": dict(self.metadata)}

    def identity(self) -> dict[str, Any]:
        return {"path": self.path, "generation": self.generation, "sha256": self.sha256}

    @classmethod
    def parse(cls, value: Any) -> ObjectVersion:
        keys(value, {"path", "generation", "sha256", "size", "content_type", "cache_control", "metadata"}, "object result")
        require(isinstance(value["metadata"], dict), "invalid result metadata")
        return cls(**{**value, "metadata": tuple(sorted(value["metadata"].items()))})


@dataclass(frozen=True)
class ObjectHead:
    path: str
    generation: int
    size: int
    content_type: str
    cache_control: str
    metadata: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class JsonObject:
    value: dict[str, Any]
    version: ObjectVersion


class Store(Protocol):
    def head(self, uri: str) -> ObjectHead | None: ...
    def list_heads(self, prefix: str) -> Iterable[ObjectHead]: ...
    def read_json(self, uri: str) -> JsonObject | None: ...
    def write_json(self, uri: str, value: dict[str, Any], expected: int) -> ObjectVersion: ...
    def inspect(self, uri: str, generation: int | None = None) -> ObjectVersion | None: ...
    def upload(self, uri: str, path: Path, expected: int, tags: Mapping[str, str], content_type: str, cache_control: str) -> ObjectVersion: ...
    def copy(self, source: ObjectVersion, uri: str, expected: int, tags: Mapping[str, str], content_type: str, cache_control: str) -> ObjectVersion: ...
    def write_bytes(self, uri: str, data: bytes, expected: int, tags: Mapping[str, str], content_type: str, cache_control: str) -> ObjectVersion: ...


@dataclass(frozen=True)
class Intent:
    """Immutable canonical bytes prevent callers mutating a prepared plan."""
    encoded: bytes

    def __post_init__(self) -> None:
        value = strict_json(self.encoded)
        validate_intent(value)
        require(self.encoded == canonical(value), "intent must use canonical JSON")

    @classmethod
    def build(cls, value: dict[str, Any]) -> Intent:
        return cls(canonical(value))

    @property
    def value(self) -> dict[str, Any]:
        return json.loads(self.encoded)

    @property
    def transaction_id(self) -> str:
        return digest(self.encoded)


def validate_intent(value: Any) -> None:
    keys(value, {"schema_version", "context", "mode", "release", "predecessor", "reservation", "prepared_at", "operations", "notification"}, "intent")
    require(type(value["schema_version"]) is int and value["schema_version"] == VERSION, "unsupported intent version")
    keys(value["context"], set(Context.__dataclass_fields__), "context")
    context = Context(**value["context"])
    require(isinstance(value["mode"], str) and value["mode"] in MODES, "unsupported mode")
    require((value["mode"] == "catalog_update") == (context.asset_root is None), "catalog_update cannot bypass asset ownership")
    if context.asset_root:
        require(valid_date(value["release"]), "invalid release")
    else:
        require(value["release"] is None and value["predecessor"] is None and value["reservation"] is None, "global catalog cannot carry asset activation")
    require(isinstance(value["prepared_at"], str) and bool(value["prepared_at"].strip()), "fixed preparation timestamp required")
    if value["predecessor"] is not None:
        identity_snapshot(value["predecessor"])
        require(value["predecessor"]["path"] == f"gs://{context.bucket}/{context.asset_root}/latest/{context.asset_slug}.manifest.json", "identity baseline must be the asset latest manifest")
    reservation = value["reservation"]
    if reservation is not None:
        require(context.identity_contract is not None, "generated reservation requires an identity contract")
        keys(reservation, {"start", "next"}, "reservation")
        require(integer(reservation["start"], 1) and integer(reservation["next"], 1) and reservation["start"] <= reservation["next"] <= 10**64, "invalid generated sequence range")
        require(value["mode"] == "complete_release", "repairs/metadata cannot allocate IDs")
    require(value["notification"] is None or isinstance(value["notification"], dict), "notification payload must be an immutable object or null")
    operations = value["operations"]
    require(isinstance(operations, list) and bool(operations), "operations must be a nonempty list")
    seen: set[str] = set()
    destinations: set[str] = set()
    previous_phase = -1
    for operation in operations:
        keys(operation, {"id", "phase", "destination", "expected_generation", "source", "content_type", "cache_control", "user_metadata"}, "operation (deletes are unsupported)")
        require(isinstance(operation["id"], str) and bool(SLUG.fullmatch(operation["id"])) and operation["id"] not in seen, "duplicate/invalid operation ID")
        require(operation["destination"] not in destinations and integer(operation["expected_generation"]), "duplicate destination or invalid original generation")
        require(operation["phase"] in PHASES and PHASES.index(operation["phase"]) >= previous_phase, "operations must follow checkpoint/data/commit/derived order")
        previous_phase = PHASES.index(operation["phase"])
        require(isinstance(operation["content_type"], str) and bool(operation["content_type"]) and isinstance(operation["cache_control"], str), "explicit content/cache metadata required")
        user_metadata = operation["user_metadata"]
        require(isinstance(user_metadata, dict) and all(isinstance(k, str) and isinstance(v, str) and not k.lower().startswith("publication-") for k, v in user_metadata.items()), "invalid/reserved user metadata")
        classification = target_class(context, operation["destination"])
        require((classification == "checkpoint") == (operation["phase"] == "checkpoint"), "checkpoint phase/path mismatch")
        require((classification == "global") == (operation["phase"] == "global_derived"), "global target phase mismatch")
        if classification == "protected" or operation["destination"].endswith("/README.md") or "/runs/" in operation["destination"]:
            require(operation["phase"] == "asset_derived", "asset-derived targets must retain asset ownership")
        if operation["phase"] == "checkpoint":
            require(operation["expected_generation"] == 0, "checkpoints are immutable")
        if classification == "asset" and not operation["destination"].endswith("/README.md") and "/runs/" not in operation["destination"]:
            relative = operation["destination"].split(context.asset_root + "/", 1)[1].split("/")
            require((len(relative) == 2 and relative[0] == "latest") or (len(relative) == 3 and relative[:2] == ["releases", value["release"]]), "data target must be in this release or latest")
            if relative[-1].endswith(".manifest.json"):
                require(operation["phase"] == "commit" and value["mode"] == "complete_release", "manifest activation requires complete_release")
            else:
                require(operation["phase"] == "data", "data bytes must precede manifest commit")
        if value["mode"] == "metadata_update" and classification == "asset" and operation["phase"] == "data":
            require(bool(re.search(r"\.metadata\.[a-z]{2,3}(?:_[a-z0-9]{2,8})*\.ndjson\.gz$", operation["destination"])) or operation["destination"].endswith(".metadata-translations.csv"), "metadata_update cannot change IDs or base data artifacts")
        source = operation["source"]
        require(isinstance(source, dict), "invalid source")
        kind = source.get("kind")
        if kind == "local":
            keys(source, {"kind", "key", "sha256", "size"}, "local source")
            require(operation["phase"] == "checkpoint" and isinstance(source["key"], str) and bool(source["key"]), "local inputs must pass through a checkpoint")
            require(hash_value(source["sha256"]) and integer(source["size"]), "invalid local input fingerprint")
        elif kind == "gcs":
            keys(source, {"kind", "path", "generation", "sha256", "size"}, "GCS source")
            split_uri(source["path"])
            require(integer(source["generation"], 1) and hash_value(source["sha256"]) and integer(source["size"]), "invalid pinned GCS source")
        elif kind == "result":
            keys(source, {"kind", "operation"}, "result source")
            require(source["operation"] in seen, "result must reference an earlier operation")
        elif kind == "derived":
            keys(source, {"kind", "version", "parameters", "dependencies"}, "derived source")
            require(source["version"] == context.finalization_version and isinstance(source["parameters"], dict), "unapproved derivation")
            require(isinstance(source["dependencies"], list) and all(item in seen for item in source["dependencies"]), "invalid derivation dependencies")
        else:
            raise PublicationError("unsupported source kind")
        seen.add(operation["id"])
        destinations.add(operation["destination"])
    commits = {operation["destination"] for operation in operations if operation["phase"] == "commit"}
    if value["mode"] == "complete_release":
        expected = {f"gs://{context.bucket}/{context.asset_root}/{part}/{context.asset_slug}.manifest.json" for part in (f"releases/{value['release']}", "latest")}
        require(commits == expected, "complete release requires release/latest manifests as final data commits")
    else:
        require(not commits, "repair/metadata/catalog modes cannot activate a new manifest")


def operation_tags(intent: Intent, operation: dict[str, Any], sha256: str) -> dict[str, str]:
    return {**operation["user_metadata"], "publication-transaction": intent.transaction_id, "publication-operation": digest(canonical(operation)), "publication-sha256": sha256}


def phase_for(intent: Intent, results: Mapping[str, Any]) -> str:
    operations = intent.value["operations"]
    if not results:
        return "prepared"
    required = [op for op in operations if op["phase"] != "global_derived"]
    if not required:  # global catalog has no asset commit point
        return "derived_complete" if len(results) == len(operations) else "partial"
    if all(op["id"] in results for op in required):
        return "derived_complete"
    if all(op["id"] in results for op in required if op["phase"] != "asset_derived"):
        return "committed"
    return "partial"


def receipt_value(intent: Intent, results: dict[str, Any], notification: dict[str, Any] | None = None) -> dict[str, Any]:
    if notification is None:
        notification = {"state": "pending" if intent.value["notification"] is not None else "not_requested", "attempt_id": None, "delivery_reference": None}
    return {"schema_version": VERSION, "transaction_id": intent.transaction_id, "intent": intent.value, "phase": phase_for(intent, results), "results": results, "notification": notification}


def parse_receipt(value: Any, context: Context) -> tuple[Intent, dict[str, Any]]:
    keys(value, {"schema_version", "transaction_id", "intent", "phase", "results", "notification"}, "receipt")
    require(type(value["schema_version"]) is int and value["schema_version"] == VERSION, "unsupported receipt version")
    intent = Intent.build(value["intent"])
    require(intent.value["context"] == asdict(context), "proposal authorization/executor changed; original receipt required")
    require(value["transaction_id"] == intent.transaction_id, "receipt intent digest mismatch")
    results = value["results"]
    require(isinstance(results, dict), "invalid receipt results")
    operations = {op["id"]: op for op in intent.value["operations"]}
    require(set(results) <= set(operations), "unknown receipt operation result")
    ids = list(operations)
    require(set(results) == set(ids[:len(results)]), "receipt results must follow operation order")
    for operation_id, raw in results.items():
        version = ObjectVersion.parse(raw)
        operation = operations[operation_id]
        require(version.path == operation["destination"] and dict(version.metadata) == operation_tags(intent, operation, version.sha256), "result ownership does not match operation")
        require(version.content_type == operation["content_type"] and version.cache_control == operation["cache_control"], "result content/cache metadata differs from operation")
        source = operation["source"]
        if source["kind"] == "derived":
            require(all(key in results for key in source["dependencies"]), "derived result dependencies missing")
        if source["kind"] in {"gcs", "local"}:
            require(version.sha256 == source["sha256"] and version.size == source["size"], "result differs from approved source")
        if source["kind"] == "result":
            require(source["operation"] in results, "result dependency missing")
            prior = ObjectVersion.parse(results[source["operation"]])
            require(version.sha256 == prior.sha256 and version.size == prior.size, "copy result differs from prior operation")
    notification = value["notification"]
    keys(notification, {"state", "attempt_id", "delivery_reference"}, "notification journal")
    state = notification["state"]
    require(isinstance(state, str) and state in {"not_requested", "pending", "sending", "sent", "failed", "unknown"}, "invalid notification state")
    require((state == "not_requested") == (intent.value["notification"] is None), "notification not authorized by intent")
    if state in {"not_requested", "pending"}:
        require(notification["attempt_id"] is None and notification["delivery_reference"] is None, "invalid pending notification")
    else:
        require(isinstance(notification["attempt_id"], str) and bool(notification["attempt_id"]) and len(results) == len(operations), "notification precedes completed operations")
        require(notification["delivery_reference"] is None or isinstance(notification["delivery_reference"], str), "invalid notification reference")
    require(value["phase"] == phase_for(intent, results), "receipt phase contradicts operation results")
    return intent, results


def validate_state(value: Any, context: Context) -> None:
    keys(value, {"schema_version", "asset_slug", "adoption_receipt", "reserved_next_feature_id", "current", "active"}, "publication state")
    require(type(value["schema_version"]) is int and value["schema_version"] == VERSION and value["asset_slug"] == context.asset_slug, "unsupported/mismatched publication state")
    def own_receipt(uri: str) -> None:
        bucket, name = split_uri(uri)
        require(bucket == context.bucket and name.startswith(context.asset_root + "/publications/receipts/") and protocol_path_kind(name) == "receipt", "foreign/malformed state receipt reference")

    own_receipt(value["adoption_receipt"])
    highwater = value["reserved_next_feature_id"]
    require(highwater is None or (integer(highwater, 1) and highwater <= 10**64), "invalid reserved high-water")
    current = value["current"]
    if current is not None:
        keys(current, {"release", "transaction_id", "receipt_uri", "release_manifest", "latest_manifest"}, "current state")
        require(valid_date(current["release"]), "invalid current release")
        require(hash_value(current["transaction_id"]), "invalid current transaction")
        for field in ("release_manifest", "latest_manifest"):
            identity_snapshot(current[field])
        require(current["latest_manifest"]["path"] == f"gs://{context.bucket}/{context.asset_root}/latest/{context.asset_slug}.manifest.json", "wrong current latest manifest")
        require(current["release_manifest"]["path"] == f"gs://{context.bucket}/{context.asset_root}/releases/{current['release']}/{context.asset_slug}.manifest.json", "wrong current release manifest")
        own_receipt(current["receipt_uri"])
    active = value["active"]
    if active is not None:
        keys(active, {"transaction_id", "receipt_uri"}, "active claim")
        require(hash_value(active["transaction_id"]), "invalid active transaction")
        own_receipt(active["receipt_uri"])


def validate_state_references(store: Store, state: dict[str, Any], context: Context) -> None:
    adoption = store.read_json(state["adoption_receipt"])
    require(adoption is not None, "explicit adoption record is missing")
    record = adoption.value
    keys(record, {"schema_version", "kind", "asset_slug", "asset_root", "bucket", "baseline", "reserved_next_feature_id", "evidence_sha256", "identity_contract", "reset_release"}, "adoption")
    require(type(record["schema_version"]) is int and record["schema_version"] == VERSION and record["kind"] == "adoption", "invalid adoption version/kind")
    require((record["asset_slug"], record["asset_root"], record["bucket"]) == (context.asset_slug, context.asset_root, context.bucket) and hash_value(record["evidence_sha256"]), "foreign/invalid adoption evidence")
    seed_highwater = record["reserved_next_feature_id"]
    require(record["identity_contract"] == context.identity_contract, "publication identity contract differs from adoption")
    require((seed_highwater is None) == (context.identity_contract is None), "adoption identity contract does not match allocation strategy")
    require((seed_highwater is None) == (state["reserved_next_feature_id"] is None), "identity strategy differs from adoption")
    if seed_highwater is not None:
        require(integer(seed_highwater, 1) and seed_highwater <= state["reserved_next_feature_id"], "reserved high-water regressed below adoption")
    baseline = record["baseline"]
    if baseline is not None:
        keys(baseline, {"release", "release_manifest", "latest_manifest"}, "adoption baseline")
        require(valid_date(baseline["release"]), "invalid adoption release")
        for field in ("release_manifest", "latest_manifest"):
            identity_snapshot(baseline[field])
        require(baseline["latest_manifest"]["path"] == f"gs://{context.bucket}/{context.asset_root}/latest/{context.asset_slug}.manifest.json" and baseline["release_manifest"]["path"] == f"gs://{context.bucket}/{context.asset_root}/releases/{baseline['release']}/{context.asset_slug}.manifest.json", "foreign adoption manifest")
    else:
        require(seed_highwater in (None, 1), "genesis adoption cannot guess an allocation high-water")
    reset_release = record["reset_release"]
    if reset_release is not None:
        require(baseline is not None and seed_highwater == 1 and valid_date(reset_release) and reset_release > baseline["release"], "reset requires an old baseline, a new release, and next-ID 1")
        from ingestion.common.identity_reset import load_reset_candidate
        reset = load_reset_candidate(store, context, record)
        require(state["adoption_receipt"] == reset.adoption_uri, "reset receipt path does not match its content")
    current = state["current"]
    if current is not None:
        if current["receipt_uri"] == state["adoption_receipt"]:
            expected = {**(baseline or {}), "transaction_id": digest(canonical(record)), "receipt_uri": state["adoption_receipt"]}
            require(current == expected, "current does not match explicit adoption baseline")
        else:
            committed = store.read_json(current["receipt_uri"])
            require(committed is not None, "current receipt is missing")
            committed_context = receipt_context(committed.value)
            require((committed_context.bucket, committed_context.asset_root, committed_context.asset_slug) == (context.bucket, context.asset_root, context.asset_slug) and committed_context.receipt_uri == current["receipt_uri"], "foreign current receipt")
            require(committed_context.identity_contract == context.identity_contract, "current receipt belongs to another identity contract")
            intent, results = parse_receipt(committed.value, committed_context)
            require(committed.value["phase"] in {"committed", "derived_complete"} and intent.value["mode"] == "complete_release" and intent.transaction_id == current["transaction_id"] and intent.value["release"] == current["release"], "state current lacks a committed activation receipt")
            allocation = intent.value["reservation"]
            require((allocation is None) == (state["reserved_next_feature_id"] is None), "current allocation strategy differs from state")
            if allocation is not None:
                require(state["reserved_next_feature_id"] >= allocation["next"], "reserved high-water regressed below committed allocation")
            versions = [ObjectVersion.parse(results[op["id"]]).identity() for op in intent.value["operations"] if op["phase"] == "commit"]
            require(current["release_manifest"] in versions and current["latest_manifest"] in versions, "state manifest snapshots differ from committed receipt")
    else:
        require(baseline is None, "state forgot an adopted current release")
    if state["active"] is not None:
        active = store.read_json(state["active"]["receipt_uri"])
        require(active is not None, "active receipt is missing")
        active_context = receipt_context(active.value)
        require((active_context.bucket, active_context.asset_root, active_context.asset_slug) == (context.bucket, context.asset_root, context.asset_slug) and active_context.receipt_uri == state["active"]["receipt_uri"], "foreign active receipt")
        require(active_context.identity_contract == context.identity_contract, "active receipt belongs to another identity contract")
        intent, _ = parse_receipt(active.value, active_context)
        require(intent.transaction_id == state["active"]["transaction_id"], "active receipt intent changed")
        reservation = intent.value["reservation"]
        if reservation is not None:
            require(state["reserved_next_feature_id"] == reservation["next"], "active reservation differs from persisted high-water")
            if current and current["transaction_id"] != intent.transaction_id:
                require(intent.value["predecessor"] == current["latest_manifest"], "active claim has a stale baseline")
                minimum = seed_highwater
                if current["receipt_uri"] != state["adoption_receipt"]:
                    previous = store.read_json(current["receipt_uri"])
                    minimum = previous.value["intent"]["reservation"]["next"]
                require(reservation["start"] >= minimum, "active range reuses previously committed allocations")


class Executor:
    def __init__(self, store: Store, *, validate_semantics: Callable[[Intent], None], preflight_sources: Callable[[Intent], None], derive: Callable[[dict[str, Any], Mapping[str, ObjectVersion]], bytes]):
        self.store = store
        self.validate_semantics = validate_semantics
        self.preflight_sources = preflight_sources
        self.derive = derive

    def _state(self, context: Context) -> JsonObject:
        loaded = self.store.read_json(context.state_uri)
        require(loaded is not None, "PUBLICATION_NOT_ADOPTED: explicit managed genesis/adoption required")
        validate_state(loaded.value, context)
        validate_state_references(self.store, loaded.value, context)
        return loaded

    def _claim(self, intent: Intent, context: Context) -> None:
        state = self._state(context)
        desired = {"transaction_id": intent.transaction_id, "receipt_uri": context.receipt_uri}
        if state.value["active"] == desired:
            return
        require(state.value["active"] is None, "another publication owns the asset")
        value = intent.value
        current = state.value["current"]
        if current and current["transaction_id"] == intent.transaction_id:
            return  # terminal resume; receipt checks below forbid new protected work
        require(value["predecessor"] == (current["latest_manifest"] if current else None), "stale identity baseline")
        if current and current["receipt_uri"] == state.value["adoption_receipt"]:
            adoption = self.store.read_json(state.value["adoption_receipt"]).value
            if adoption["reset_release"] is not None:
                require(value["mode"] == "complete_release" and value["release"] == adoption["reset_release"], "first publication must complete the approved reset release")
                from ingestion.common.identity_reset import load_reset_candidate
                reset = load_reset_candidate(self.store, context, adoption)
                operations = {op["destination"]: op for op in value["operations"]}
                current_heads = {head.path: head for head in self.store.list_heads(f"{reset.root_uri}/latest/")}
                require(set(current_heads) == {anchor["path"] for anchor in reset.value["latest_objects"]}, "reset latest object inventory changed before claim")
                for anchor in reset.value["latest_objects"]:
                    operation = operations.get(anchor["path"])
                    require(operation is not None and operation["expected_generation"] == anchor["generation"], "reset must replace the complete captured latest bundle at its original generations")
                    require(current_heads[anchor["path"]].generation == anchor["generation"], "reset latest generation changed before claim")
        if value["mode"] == "complete_release":
            require(current is None or value["release"] > current["release"], "older/same release cannot activate latest")
        else:
            require(current is not None and value["release"] == current["release"], "repair/metadata requires current committed release")
        reservation = value["reservation"]
        if value["mode"] == "complete_release":
            require((reservation is None) == (state.value["reserved_next_feature_id"] is None), "generated asset requires an explicit reservation")
        if reservation:
            require(reservation["start"] == state.value["reserved_next_feature_id"], "reservation does not match persisted generated high-water")
        next_state = {**state.value, "active": desired}
        if reservation:
            next_state["reserved_next_feature_id"] = reservation["next"]
        self.store.write_json(context.state_uri, next_state, state.version.generation)

    def _verify_genesis_objects(self, context: Context, intent: Intent) -> None:
        operations = {op["destination"]: op for op in intent.value["operations"]}
        for prefix in ("latest", "releases"):
            for head in self.store.list_heads(f"gs://{context.bucket}/{context.asset_root}/{prefix}/"):
                operation = operations.get(head.path)
                metadata = dict(head.metadata)
                sha256 = metadata.get("publication-sha256")
                require(operation is not None and hash_value(sha256) and metadata == operation_tags(intent, operation, sha256), "genesis has unaccounted allocation-prefix objects")

    def _assert_owner(self, context: Context, intent: Intent) -> None:
        state = self._state(context)
        require(state.value["active"] == {"transaction_id": intent.transaction_id, "receipt_uri": context.receipt_uri}, "transaction no longer owns the asset")

    def _save_result(self, context: Context, intent: Intent, operation: dict[str, Any], version: ObjectVersion) -> None:
        # Each CAS conflict reloads only the journal, never destination expectations.
        for _ in range(16):
            receipt = self.store.read_json(context.receipt_uri)
            require(receipt is not None, "active receipt is missing")
            stored_intent, results = parse_receipt(receipt.value, context)
            require(stored_intent == intent, "receipt intent changed")
            if operation["id"] in results:
                require(results[operation["id"]] == version.record(), "conflicting operation results")
                return
            updated = receipt_value(intent, {**results, operation["id"]: version.record()}, receipt.value["notification"])
            parse_receipt(updated, context)
            try:
                self.store.write_json(context.receipt_uri, updated, receipt.version.generation)
                return
            except Conflict:
                continue
        raise Conflict("receipt changed repeatedly; resume the same transaction")

    def _advance(self, context: Context, intent: Intent, results: Mapping[str, Any], *, clear: bool) -> None:
        state = self._state(context)
        current = state.value["current"]
        if state.value["active"] is None and current and current["transaction_id"] == intent.transaction_id:
            require(clear, "already completed transaction cannot reactivate")
            return
        self._assert_owner(context, intent)
        value = intent.value
        if value["mode"] == "complete_release":
            versions = [ObjectVersion.parse(results[op["id"]]) for op in value["operations"] if op["phase"] == "commit"]
            current = {"release": value["release"], "transaction_id": intent.transaction_id, "receipt_uri": context.receipt_uri,
                       "release_manifest": next(item.identity() for item in versions if "/releases/" in item.path),
                       "latest_manifest": next(item.identity() for item in versions if "/latest/" in item.path)}
        # Metadata/repair operations do not replace the activation authority.
        next_state = {**state.value, "current": current, "active": None if clear else state.value["active"]}
        if next_state != state.value:
            self.store.write_json(context.state_uri, next_state, state.version.generation)

    def begin_notification(self, context: Context, *, attempt_id: str, retry_failed: bool = False) -> dict[str, Any]:
        """Journal intent before external send. An unfinished send is not retried."""
        require(isinstance(attempt_id, str) and bool(attempt_id), "notification attempt ID required")
        receipt = self.store.read_json(context.receipt_uri)
        require(receipt is not None, "notification receipt is missing")
        intent, results = parse_receipt(receipt.value, context)
        self.validate_semantics(intent)
        require(type(retry_failed) is bool, "retry_failed must be explicit boolean")
        previous = receipt.value["notification"]
        eligible = previous["state"] == "pending" or (retry_failed and previous["state"] == "failed" and previous["attempt_id"] != attempt_id)
        require(len(results) == len(intent.value["operations"]) and eligible, "notification is incomplete, unrequested, or possibly already sent; failed retry needs a new attempt ID")
        journal = {"state": "sending", "attempt_id": attempt_id, "delivery_reference": None}
        self.store.write_json(context.receipt_uri, receipt_value(intent, results, journal), receipt.version.generation)
        return intent.value["notification"]

    def finish_notification(self, context: Context, *, attempt_id: str, outcome: str, delivery_reference: str | None = None) -> None:
        """Record transport knowledge without republishing or automatic resend."""
        require(outcome in {"sent", "failed", "unknown"}, "invalid delivery outcome")
        receipt = self.store.read_json(context.receipt_uri)
        require(receipt is not None, "notification receipt is missing")
        intent, results = parse_receipt(receipt.value, context)
        self.validate_semantics(intent)
        desired = {"state": outcome, "attempt_id": attempt_id, "delivery_reference": delivery_reference}
        if receipt.value["notification"] == desired:
            return
        require(receipt.value["notification"]["state"] == "sending" and receipt.value["notification"]["attempt_id"] == attempt_id, "notification attempt no longer owns delivery outcome")
        updated = receipt_value(intent, results, desired)
        parse_receipt(updated, context)
        self.store.write_json(context.receipt_uri, updated, receipt.version.generation)

    def _finish_owned(self, context: Context, intent: Intent, results: Mapping[str, Any]) -> None:
        require(phase_for(intent, results) == "derived_complete", "protected effects are incomplete")
        state = self._state(context)
        if state.value["active"] == {"transaction_id": intent.transaction_id, "receipt_uri": context.receipt_uri}:
            self._advance(context, intent, results, clear=True)
        # A terminal old receipt may report/retry only fixed-CAS global effects.
        # It must never reacquire an idle claim or alter a newer transaction.

    def run(self, context: Context, *, prepare: Callable[[], Intent], local_sources: Mapping[str, Path]) -> dict[str, Any]:
        """Look up by proposal BEFORE calling prepare or consulting live expectations.

        Local paths must be private verified snapshots (the F1 adapter captures
        them). Store.upload additionally freezes/checks them before each write.
        """
        receipt = self.store.read_json(context.receipt_uri)
        if receipt is None:
            if context.asset_root:
                self._state(context)  # reject corrupt authority before persisting a new proposal
            intent = prepare()
            require(intent.value["context"] == asdict(context), "prepared context mismatch")
            self.validate_semantics(intent)
            self.preflight_sources(intent)
            for operation in intent.value["operations"]:
                source = operation["source"]
                if source["kind"] == "gcs":
                    version = self.store.inspect(source["path"], source["generation"])
                    require(version is not None and version.sha256 == source["sha256"] and version.size == source["size"], "source preflight mismatch")
                elif source["kind"] == "local":
                    path = local_sources.get(source["key"])
                    require(path is not None and path.is_file(), "missing local source before preparation")
                    with path.open("rb") as handle:
                        require(hashlib.file_digest(handle, "sha256").hexdigest() == source["sha256"] and path.stat().st_size == source["size"], "local source preflight mismatch")
            try:
                self.store.write_json(context.receipt_uri, receipt_value(intent, {}), 0)
            except Conflict:
                pass  # converge only if the winning receipt has this exact intent
            receipt = self.store.read_json(context.receipt_uri)
            require(receipt is not None, "prepared receipt missing")
            stored_intent, _ = parse_receipt(receipt.value, context)
            require(stored_intent == intent, "proposal already prepared with different expectations")
        intent, results = parse_receipt(receipt.value, context)
        self.validate_semantics(intent)
        protected_complete = receipt.value["phase"] == "derived_complete"
        if context.asset_root:
            if protected_complete:
                self._finish_owned(context, intent, results)
            else:
                self._claim(intent, context)
                if intent.value["predecessor"] is None:
                    self._verify_genesis_objects(context, intent)
        advanced = protected_complete
        for operation in intent.value["operations"]:
            if context.asset_root and operation["phase"] in {"asset_derived", "global_derived"} and not advanced:
                require(all(op["id"] in results for op in intent.value["operations"] if op["phase"] in {"checkpoint", "data", "commit"}), "data commit incomplete")
                self._advance(context, intent, results, clear=phase_for(intent, results) == "derived_complete")
                advanced = True
            if operation["id"] in results:
                continue
            if context.asset_root and operation["phase"] == "global_derived":
                self._finish_owned(context, intent, results)
            if context.asset_root and operation["phase"] != "global_derived":
                self._assert_owner(context, intent)
            source = operation["source"]
            data: bytes | None = None
            source_version: ObjectVersion | None = None
            if source["kind"] in {"local", "gcs"}:
                expected_sha, expected_size = source["sha256"], source["size"]
            elif source["kind"] == "result":
                source_version = ObjectVersion.parse(results[source["operation"]])
                expected_sha, expected_size = source_version.sha256, source_version.size
            else:
                dependencies = {key: ObjectVersion.parse(results[key]) for key in source["dependencies"]}
                data = self.derive(source["parameters"], dependencies)
                require(type(data) is bytes, "derivation must return exact bytes")
                expected_sha, expected_size = digest(data), len(data)
            tags = operation_tags(intent, operation, expected_sha)
            head = self.store.head(operation["destination"])
            if head is not None and dict(head.metadata) == tags:
                found = self.store.inspect(operation["destination"], head.generation)
                require(found is not None, "tagged destination generation disappeared")
                require(found.sha256 == expected_sha and found.size == expected_size and found.content_type == operation["content_type"] and found.cache_control == operation["cache_control"], "tagged destination bytes/metadata differ")
                version = found
            else:
                if head is not None and head.generation != operation["expected_generation"]:
                    raise Conflict("destination differs from original expectation and has no matching ownership")
                args = (operation["destination"], operation["expected_generation"], tags, operation["content_type"], operation["cache_control"])
                if source["kind"] == "local":
                    path = local_sources.get(source["key"])
                    if path is None or not path.is_file():
                        raise NotRecoverableSource("NOT_RECOVERABLE_SOURCE: checkpoint input vanished; reservation retained")
                    version = self.store.upload(args[0], path, *args[1:])
                elif data is not None:
                    version = self.store.write_bytes(args[0], data, *args[1:])
                else:
                    if source_version is None:
                        source_version = ObjectVersion(source["path"], source["generation"], expected_sha, expected_size)
                    version = self.store.copy(source_version, args[0], *args[1:])
                require(version.sha256 == expected_sha and version.size == expected_size, "store wrote unexpected bytes")
            self._save_result(context, intent, operation, version)
            results[operation["id"]] = version.record()
        if context.asset_root:
            self._finish_owned(context, intent, results)
        final = self.store.read_json(context.receipt_uri)
        require(final is not None, "publication receipt disappeared")
        parse_receipt(final.value, context)
        return final.value
