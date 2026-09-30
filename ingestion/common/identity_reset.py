"""Prepare a new identity history while preserving the old release objects.

A candidate is review material, never publication authority. Installation belongs
to the protected publisher after review and old-writer exclusion. This module
does not write remote objects or infer any historical allocation ceiling.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from ingestion.common import publication as p
from scripts import release_feature_model as model


CONTRACT_ID = "generated-2026-v1"
ASSET_ROOTS = {
    "wdpa-marine": "100-geographic-reference/130-protected-areas/wdpa-marine",
    "wdpa-terrestrial": "100-geographic-reference/130-protected-areas/wdpa-terrestrial",
    "ims-sea-ice-extent": "200-imagery-derived/250-weather-climate/ims-sea-ice-extent",
}
BASE_SUFFIXES = (".fgb", ".pmtiles", ".metadata.ndjson.gz", ".schema.json", ".manifest.json")


def require_unmanaged_target(name: str, *, expected_generation: int | None = None) -> None:
    """Protect identity-bearing roots; allow only new independent load reports."""
    for slug, root in ASSET_ROOTS.items():
        if name.startswith(root + "/index-loads/"):
            parts = name.removeprefix(root + "/").split("/")
            if (type(expected_generation) is int and expected_generation == 0
                    and len(parts) == 3 and p.valid_date(parts[1])
                    and re.fullmatch(r"[A-Za-z0-9._-]+\.json", parts[2])):
                return
        if name == root or name.startswith(root + "/") or name in {
            f"_catalog/releases/{slug}.json", f"_catalog/schema-snapshots/{slug}.json",
        }:
            raise p.PublicationError(f"{slug} requires owned publication; this mutation path is unavailable during rollout")


@dataclass(frozen=True)
class IdentityResetCandidate:
    encoded: bytes

    def __post_init__(self) -> None:
        value = p.strict_json(self.encoded)
        p.keys(value, {"schema_version", "asset_slug", "bucket", "contract_id", "first_release", "baseline", "latest_objects", "translation_supplement"}, "identity reset inventory")
        p.require(type(value["schema_version"]) is int and value["schema_version"] == 1, "unsupported reset inventory version")
        slug = value["asset_slug"]
        p.require(isinstance(slug, str) and slug in ASSET_ROOTS, "asset is not approved for a pre-launch identity reset")
        p.require(value["contract_id"] == CONTRACT_ID, "reset must name the approved identity contract")
        # Context validates the bucket and canonical three-component root.
        p.Context("a" * 64, "b" * 64, "c" * 40, p.FINALIZATION_VERSION, value["bucket"], ASSET_ROOTS[slug], slug, value["contract_id"])
        supplement = value["translation_supplement"]
        if slug.startswith("wdpa-"):
            p.identity_snapshot(supplement)
            bucket, path = p.split_uri(supplement["path"])
            p.require(bucket == value["bucket"] and path.startswith("_scratch/pending-publishes/") and path.endswith(".ndjson"),
                      "WDPA reset requires a generation/hash-pinned staged translation supplement")
        else:
            p.require(supplement is None, "sea-ice reset does not accept translation inputs")
        baseline = value["baseline"]
        p.keys(baseline, {"release", "release_manifest", "latest_manifest"}, "retired baseline")
        p.require(p.valid_date(baseline["release"]) and p.valid_date(value["first_release"]) and value["first_release"] > baseline["release"], "reset requires a new dated release after the retired baseline")
        for field, suffix in (("release_manifest", f"releases/{baseline['release']}"), ("latest_manifest", "latest")):
            p.identity_snapshot(baseline[field])
            p.require(baseline[field]["path"] == f"{self.root_uri}/{suffix}/{slug}.manifest.json", "retired manifest is outside this asset/release")
        p.require(baseline["release_manifest"]["sha256"] == baseline["latest_manifest"]["sha256"], "retired release/latest manifests must agree")
        objects = value["latest_objects"]
        p.require(isinstance(objects, list) and bool(objects), "exact current latest objects are required")
        paths = set()
        for snapshot in objects:
            p.identity_snapshot(snapshot)
            path = snapshot["path"]
            p.require(path.startswith(self.root_uri + "/latest/") and "/" not in path.removeprefix(self.root_uri + "/latest/"), "latest anchor is outside this asset")
            p.require(path not in paths, "duplicate latest anchor")
            paths.add(path)
        p.require({f"{self.root_uri}/latest/{slug}{suffix}" for suffix in BASE_SUFFIXES} <= paths, "reset inventory requires the complete current vector bundle")
        p.require(baseline["latest_manifest"] in objects, "latest manifest must match its captured object anchor")
        p.require(objects == sorted(objects, key=lambda item: item["path"]), "latest anchors must be sorted by path")
        p.require(self.encoded == p.canonical(value), "reset inventory must use canonical JSON")

    @classmethod
    def build(cls, inventory: dict[str, Any]) -> IdentityResetCandidate:
        return cls(p.canonical(inventory))

    @property
    def value(self) -> dict[str, Any]:
        return p.strict_json(self.encoded)

    @property
    def root_uri(self) -> str:
        value = self.value
        return f"gs://{value['bucket']}/{ASSET_ROOTS[value['asset_slug']]}"

    @property
    def evidence_uri(self) -> str:
        return f"{self.root_uri}/publications/inputs/{p.digest(self.encoded)}/0.catalog.json"

    @property
    def adoption(self) -> dict[str, Any]:
        value = self.value
        return {
            "schema_version": 1, "kind": "adoption", "asset_slug": value["asset_slug"],
            "asset_root": ASSET_ROOTS[value["asset_slug"]], "bucket": value["bucket"],
            "baseline": value["baseline"], "reserved_next_feature_id": 1,
            "evidence_sha256": p.digest(self.encoded), "identity_contract": value["contract_id"],
            "reset_release": value["first_release"],
        }

    @property
    def adoption_uri(self) -> str:
        return f"{self.root_uri}/publications/receipts/{p.digest(p.canonical(self.adoption))}.json"

    @property
    def initial_state(self) -> dict[str, Any]:
        return {
            "schema_version": 1, "asset_slug": self.value["asset_slug"],
            "adoption_receipt": self.adoption_uri, "reserved_next_feature_id": 1,
            "current": {**self.value["baseline"], "transaction_id": p.digest(p.canonical(self.adoption)), "receipt_uri": self.adoption_uri},
            "active": None,
        }

    def allocation_baseline(self) -> model.GeneratedIdentityBaseline:
        """Local build input only; the old replacement anchor lives in adoption."""
        return model.GeneratedIdentityBaseline.genesis(contract_id=self.value["contract_id"])

    def validate_current(self, store: p.Store) -> None:
        """Read-only preflight; must be repeated under the approved writer fence."""
        p.require(store.head(f"{self.root_uri}/publications/state.json") is None, "identity state already exists; reset cannot be repeated")
        self.validate_anchors(store)
        value = self.value
        snapshots = [*value["latest_objects"], value["baseline"]["release_manifest"]]
        if value["translation_supplement"] is not None:
            snapshots.append(value["translation_supplement"])
        for snapshot in snapshots:
            version = store.inspect(snapshot["path"], snapshot["generation"])
            p.require(version is not None and version.identity() == snapshot, "captured object generation/hash is unavailable or changed")

    def validate_anchors(self, store: p.Store) -> None:
        """Cheap freshness checks between installer writes after full hashing."""
        value = self.value
        current = list(store.list_heads(f"{self.root_uri}/latest/"))
        expected = {item["path"]: item for item in value["latest_objects"]}
        p.require({head.path for head in current} == set(expected), "current latest object inventory changed")
        for head in current:
            p.require(head.generation == expected[head.path]["generation"], "current latest generation changed")
        anchor = value["baseline"]["release_manifest"]
        head = store.head(anchor["path"])
        p.require(head is not None and head.generation == anchor["generation"], "retired release manifest changed")
        p.require(not list(store.list_heads(f"{self.root_uri}/releases/{value['first_release']}/")), "reset destination release is not empty")
        p.require(store.head(f"{self.root_uri}/runs/{value['first_release']}.json") is None, "reset destination run record already exists")

    def review_envelope(self) -> dict[str, Any]:
        """Prepared objects still require a separately authorized installation."""
        return {
            "status": "prepared_for_review", "inventory_sha256": p.digest(self.encoded),
            "objects": [
                {"path": self.evidence_uri, "expected_generation": 0, "value": self.value},
                {"path": self.adoption_uri, "expected_generation": 0, "value": self.adoption},
                {"path": f"{self.root_uri}/publications/state.json", "expected_generation": 0, "value": self.initial_state},
            ],
        }


def validate_installation_marker(value: dict[str, Any], inventory_sha256: str) -> None:
    """One persisted marker schema shared by installation and runtime admission."""
    p.keys(value, {"schema_version", "inventory_sha256", "fence_sha256", "proposal_key", "execution_contract_sha256", "phase", "state_generation"}, "reset installation marker")
    p.require(type(value["schema_version"]) is int and value["schema_version"] == 1
              and value["phase"] in {"prepared", "activating", "complete"}
              and value["inventory_sha256"] == inventory_sha256
              and all(p.hash_value(value[k]) for k in ("fence_sha256", "proposal_key", "execution_contract_sha256")),
              "invalid reset installation marker")
    p.require((p.integer(value["state_generation"], 1) if value["phase"] == "complete" else value["state_generation"] is None),
              "invalid reset installation state generation")


def load_reset_candidate(store: p.Store, context: p.Context, adoption: dict[str, Any]) -> IdentityResetCandidate:
    """Verify the durable reset evidence used by the publication owner."""
    uri = f"gs://{context.bucket}/{context.asset_root}/publications/inputs/{adoption['evidence_sha256']}/0.catalog.json"
    evidence = store.read_json(uri)
    p.require(evidence is not None, "reset evidence is missing")
    candidate = IdentityResetCandidate.build(evidence.value)
    p.require(p.digest(candidate.encoded) == adoption["evidence_sha256"] and candidate.adoption == adoption, "reset evidence differs from adoption")
    p.require((candidate.value["bucket"], candidate.value["asset_slug"], candidate.value["contract_id"]) == (context.bucket, context.asset_slug, context.identity_contract), "reset belongs to another asset or contract")
    marker = store.read_json(f"{candidate.root_uri}/publications/reset.json")
    p.require(marker is not None, "reset installation marker is missing")
    validate_installation_marker(marker.value, p.digest(candidate.encoded))
    p.require(marker.value["phase"] == "complete", "reset installation is incomplete")
    return candidate
