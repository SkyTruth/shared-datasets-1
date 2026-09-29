"""One-time installation of an already authorized generated-ID reset.

The workflow adapter owns approval and live writer-fence verification. This
transaction owns ordering, generation preconditions, and crash recovery. A
durable marker prevents a missing allocation state from becoming a new genesis.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ingestion.common import publication as p
from ingestion.common.identity_reset import IdentityResetCandidate, validate_installation_marker


def validate_reset_plan(plan: dict[str, Any]) -> IdentityResetCandidate:
    reset = plan["identity_reset"]
    p.keys(reset, {"inventory", "fence_sha256"}, "identity reset")
    p.require(p.hash_value(reset["fence_sha256"]), "reviewed writer-fence digest is required")
    candidate = IdentityResetCandidate.build(reset["inventory"])
    p.require(candidate.value["bucket"] == "skytruth-shared-datasets-1", "reset is restricted to the production bucket")
    p.require(candidate.value["asset_slug"] == plan["asset_slug"], "reset asset differs from publish plan")
    p.require(not plan["release_index_asset_slugs"], "reset installation cannot rebuild release indexes")
    objects = candidate.review_envelope()["objects"]
    promotions = plan["promotions"]
    p.require(len(promotions) == len(objects), "reset must promote exactly evidence, adoption, and state")
    for promotion, item in zip(promotions, objects, strict=True):
        p.require(promotion["destination_uri"] == item["path"], "reset promotions must follow evidence/adoption/state order")
        p.require(promotion["destination_generation"] == "", "reset cannot replace any existing generation")
        p.require(promotion["content_type"] == "application/json" and promotion["cache_control"] == "no-cache", "reset JSON requires explicit no-cache metadata")
        p.require(promotion["compatibility_waiver"] is None, "reset cannot carry a compatibility waiver")
    return candidate


def install_reset(
    store: p.Store, plan: dict[str, Any], *, authorization: dict[str, str],
    check_authority_and_fence: Callable[[], None],
) -> dict[str, Any]:
    """Install once; recheck approval/fence before every remote mutation.

    No local or runtime fallback is provided. The caller must have verified the
    immutable PR envelope and use its execution identity as ``authorization``.
    A crash immediately before the state creation can be ambiguous: if the
    activating marker exists but state is missing, stop for reviewed recovery.
    Never recreate state that may have been created and subsequently lost.
    """
    candidate = validate_reset_plan(plan)
    p.keys(authorization, {"proposal_key", "execution_contract_sha256"}, "reset authorization identity")
    p.require(all(p.hash_value(v) for v in authorization.values()), "invalid reset authorization identity")
    check_authority_and_fence()
    marker_uri = f"{candidate.root_uri}/publications/reset.json"
    state_uri = f"{candidate.root_uri}/publications/state.json"
    identity = {"schema_version": 1, "inventory_sha256": p.digest(candidate.encoded),
                "fence_sha256": plan["identity_reset"]["fence_sha256"], **authorization}
    tags = {"identity-reset-execution": authorization["execution_contract_sha256"]}
    items = candidate.review_envelope()["objects"]

    def read_marker():
        marker = store.read_json(marker_uri)
        if marker is not None:
            validate_installation_marker(marker.value, identity["inventory_sha256"])
            p.require(all(marker.value[k] == v for k, v in identity.items()), "another reset installation owns this asset")
        return marker

    def matching_object(item):
        head = store.head(item["path"])
        if head is None:
            return None
        version = store.inspect(item["path"], head.generation)
        expected = p.canonical(item["value"])
        p.require(version is not None and version.sha256 == p.digest(expected) and version.size == len(expected)
                  and dict(version.metadata) == tags and version.content_type == "application/json"
                  and version.cache_control == "no-cache", "reset destination contains foreign or changed bytes")
        return version

    marker = read_marker()
    if marker is not None and marker.value["phase"] == "complete":
        # The publisher may already have advanced state. Verify its owned
        # current/active receipts instead of comparing to genesis or replacing it.
        context = p.Context(authorization["proposal_key"], authorization["execution_contract_sha256"],
                            "0" * 40, p.FINALIZATION_VERSION, candidate.value["bucket"],
                            candidate.adoption["asset_root"], candidate.value["asset_slug"], candidate.value["contract_id"])
        state = store.read_json(state_uri)
        p.require(state is not None, "installed allocation state is missing; automatic reset is forbidden")
        p.validate_state(state.value, context)
        p.require(state.value["adoption_receipt"] == candidate.adoption_uri, "installed state belongs to another reset")
        p.validate_state_references(store, state.value, context)
        return {"status": "already_installed", "marker": marker.version.identity(), "state": state.version.identity()}

    candidate.validate_anchors(store)
    sources = []
    for promotion, item in zip(plan["promotions"], items, strict=True):
        version = store.inspect(promotion["source_uri"], int(promotion["source_generation"]))
        expected = p.canonical(item["value"])
        p.require(version is not None and version.sha256 == p.digest(expected) and version.size == len(expected), "staged reset source differs from approved candidate")
        sources.append(version)

    if marker is None:
        candidate.validate_current(store)
        p.require(not list(store.list_heads(f"{candidate.root_uri}/publications/")), "unowned publication objects already exist")
        check_authority_and_fence()
        store.write_json(marker_uri, {**identity, "phase": "prepared", "state_generation": None}, 0)
        marker = read_marker()

    allowed = {marker_uri, *(item["path"] for item in items)}
    p.require(all(head.path in allowed for head in store.list_heads(f"{candidate.root_uri}/publications/")), "publication began before reset installation completed")
    if marker.value["phase"] == "prepared":
        p.require(store.head(state_uri) is None, "allocation state already exists before activation")
        for item, source in zip(items[:2], sources[:2], strict=True):
            if matching_object(item) is None:
                check_authority_and_fence()
                candidate.validate_anchors(store)
                store.copy(source, item["path"], 0, tags, "application/json", "no-cache")
        check_authority_and_fence()
        candidate.validate_anchors(store)
        store.write_json(marker_uri, {**identity, "phase": "activating", "state_generation": None}, marker.version.generation)
        marker = read_marker()
        check_authority_and_fence()
        candidate.validate_anchors(store)
        store.copy(sources[2], state_uri, 0, tags, "application/json", "no-cache")
    else:
        p.require(store.head(state_uri) is not None, "activation was interrupted and state is absent; reviewed recovery required")

    for item in items:
        p.require(matching_object(item) is not None, "reset installation is incomplete")
    state = matching_object(items[2])
    check_authority_and_fence()
    candidate.validate_anchors(store)
    completed = store.write_json(marker_uri, {**identity, "phase": "complete", "state_generation": state.generation}, marker.version.generation)
    return {"status": "installed", "marker": completed.identity(), "state": state.identity()}
