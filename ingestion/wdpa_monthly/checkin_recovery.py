"""Reviewed recovery of a captured WDPA skip with the old run_date field.

The original intent, context, transaction and destination CAS remain intact.
Only its derivation interprets run_date as release_date. The publication engine
records the result and releases ownership after the exact derived write.
"""
from __future__ import annotations

from ingestion.common import publication as p
from ingestion.common.identity_reset import ASSET_ROOTS, CONTRACT_ID
from ingestion.common.reset_controls import BUCKET

SLUG = "wdpa-marine"
ROOT = ASSET_ROOTS[SLUG]
PREFIX = f"gs://{BUCKET}/{ROOT}"
INDEX = f"gs://{BUCKET}/_catalog/releases/{SLUG}.json"
KIND = "wdpa-skip-run-date-v1"


def validate_plan(plan):
    p.require(plan["asset_slug"] == SLUG, "check-in recovery is restricted to WDPA marine")
    p.require(not {"identity_reset", "identity_transition", "source_loss_review", "historical_checksum_repair"} & plan.keys(),
              "check-in recovery cannot combine other mutation subtypes")
    p.require(not plan["release_index_asset_slugs"] and not plan["breaking_changes"],
              "check-in recovery cannot rebuild indexes or change dataset contracts")
    recovery = plan["publication_recovery"]
    p.keys(recovery, {"kind", "state", "receipt", "transaction_id", "result_sha256"}, "check-in recovery")
    p.require(recovery["kind"] == KIND and p.hash_value(recovery["transaction_id"])
              and p.hash_value(recovery["result_sha256"]), "invalid check-in recovery identity")
    for field in ("state", "receipt"):
        p.identity_snapshot(recovery[field])
    p.require(recovery["state"]["path"] == PREFIX + "/publications/state.json",
              "recovery state must be the WDPA marine publication state")
    receipt_path = recovery["receipt"]["path"]
    bucket, name = p.split_uri(receipt_path)
    p.require(bucket == BUCKET and name.startswith(ROOT + "/publications/receipts/")
              and p.protocol_path_kind(name) == "receipt", "recovery receipt is outside WDPA marine")
    p.require(len(plan["promotions"]) == 1, "recovery must stage exactly its derived check-in index")
    promotion = plan["promotions"][0]
    p.require(promotion["destination_uri"] == INDEX and promotion["destination_generation"]
              and promotion["content_type"] == "application/json" and promotion["cache_control"] == "no-cache"
              and promotion["compatibility_waiver"] is None, "recovery has an invalid index effect")
    return recovery


def recovered_bytes(intent):
    """Interpret this malformed persisted producer record without editing it."""
    from ingestion.common.owned_publication import derive

    operations = intent.value["operations"]
    p.require(intent.value["mode"] == "object_repair" and intent.value["reservation"] is None
              and intent.value["notification"] is None and len(operations) == 1,
              "recovery supports one unallocated, notification-free check-in only")
    operation = operations[0]
    p.require(operation["id"] == "release-index" and operation["phase"] == "asset_derived"
              and operation["destination"] == INDEX and operation["source"]["kind"] == "derived"
              and not operation["source"]["dependencies"], "recovery cannot change dataset artifacts")
    parameters = operation["source"]["parameters"]
    p.keys(parameters, {"kind", "index", "record", "updated_at"}, "captured skip parameters")
    p.require(parameters["kind"] == "skip-index", "recovery requires a skip-index derivation")
    record = parameters["record"]
    p.keys(record, {"schema_version", "asset_slug", "status", "run_date", "target_release_date", "reason", "source", "source_version"},
           "captured malformed check-in")
    p.require(record["schema_version"] == 1 and record["asset_slug"] == SLUG and record["status"] == "skipped"
              and p.valid_date(record["run_date"]) and p.valid_date(record["target_release_date"])
              and record["target_release_date"] == record["run_date"][:8] + "01",
              "captured check-in has a different failure or monthly target")
    corrected = {key: value for key, value in record.items() if key != "run_date"}
    corrected["release_date"] = record["run_date"]
    return derive({**parameters, "record": corrected}, {})


def recover(store, plan, *, check_authority_and_jobs):
    recovery = validate_plan(plan)
    loaded = store.read_json(recovery["receipt"]["path"])
    p.require(loaded is not None, "original recovery receipt is missing")
    context = p.receipt_context(loaded.value)
    p.require((context.bucket, context.asset_root, context.asset_slug, context.identity_contract)
              == (BUCKET, ROOT, SLUG, CONTRACT_ID), "recovery receipt has a foreign context")
    p.require(context.receipt_uri == recovery["receipt"]["path"], "receipt path differs from original context")
    intent, results = p.parse_receipt(loaded.value, context)
    p.require(intent.transaction_id == recovery["transaction_id"], "original recovery transaction changed")
    # Initial receipts are canonical. This binds every captured input even after
    # a retry finds a journal advanced by the same executor's successful write.
    p.require(p.digest(p.canonical(p.receipt_value(intent, {}))) == recovery["receipt"]["sha256"],
              "captured prepared receipt differs from reviewed bytes")
    if not results:
        p.require(loaded.version.identity() == recovery["receipt"], "prepared receipt generation changed")
    data = recovered_bytes(intent)
    p.require(p.digest(data) == recovery["result_sha256"], "derived recovery differs from reviewed output")
    operation = intent.value["operations"][0]
    promotion = plan["promotions"][0]
    p.require(operation["expected_generation"] == int(promotion["destination_generation"]),
              "recovery cannot refresh the original destination expectation")
    if not results:
        staged = store.inspect(promotion["source_uri"], int(promotion["source_generation"]))
        p.require(staged is not None and staged.sha256 == p.digest(data) and staged.size == len(data),
                  "staged recovery bytes differ from the captured derivation")
    if results:
        result = p.ObjectVersion.parse(results["release-index"])
        p.require(result.sha256 == p.digest(data) and result.size == len(data), "recovery result differs from approved bytes")
    state = store.read_json(context.state_uri)
    p.require(state is not None, "original publication state is missing")
    p.validate_state(state.value, context)
    p.validate_state_references(store, state.value, context)
    owner = {"transaction_id": intent.transaction_id, "receipt_uri": context.receipt_uri}
    if results and state.value["active"] != owner:
        # A complete old receipt cannot reacquire ownership or touch newer data.
        check_authority_and_jobs()
        return {"status": "already_completed", "receipt": loaded.version.identity()}
    p.require(state.value["active"] == owner and state.version.identity() == recovery["state"],
              "original claim or publication state changed")
    p.require(state.value["current"]["latest_manifest"] == intent.value["predecessor"], "recovery predecessor changed")
    finished_state = {**state.value, "active": None}

    class CheckedStore:
        # Reads retain the ordinary executor's full state/reference validation.
        head = store.head
        list_heads = store.list_heads
        read_json = store.read_json
        inspect = store.inspect

        def write_json(self, uri, value, expected):
            if uri == context.state_uri:
                p.require(value == finished_state and expected == recovery["state"]["generation"],
                          "recovery cannot alter committed identity state")
            else:
                p.require(uri == context.receipt_uri and value["intent"] == intent.value,
                          "recovery cannot replace the original intent")
                _intent, written = p.parse_receipt(value, context)
                p.require(value["phase"] == "derived_complete" and written["release-index"]["sha256"] == p.digest(data),
                          "recovery must complete the original index effect")
            check_authority_and_jobs()
            return store.write_json(uri, value, expected)

        def write_bytes(self, uri, actual, expected, tags, content_type, cache_control):
            p.require(uri == INDEX and actual == data and expected == operation["expected_generation"]
                      and tags == p.operation_tags(intent, operation, p.digest(data))
                      and content_type == operation["content_type"] and cache_control == operation["cache_control"],
                      "recovery write differs from the approved original effect")
            check_authority_and_jobs()
            return store.write_bytes(uri, actual, expected, tags, content_type, cache_control)

    def require_original(candidate):
        p.require(candidate == intent, "recovery cannot prepare another transaction")

    def no_prepare():
        raise p.PublicationError("original recovery receipt disappeared")

    check_authority_and_jobs()
    executor = p.Executor(CheckedStore(), validate_semantics=require_original,
                          preflight_sources=require_original, derive=lambda _parameters, _results: data)
    receipt = executor.run(context, prepare=no_prepare, local_sources={})
    p.require(receipt["intent"] == loaded.value["intent"] and receipt["phase"] == "derived_complete",
              "recovery did not complete the captured transaction")
    return {"status": "recovered", "transaction_id": intent.transaction_id,
            "receipt": store.read_json(context.receipt_uri).version.identity(),
            "state": store.read_json(context.state_uri).version.identity(),
            "release_index": receipt["results"]["release-index"]}
