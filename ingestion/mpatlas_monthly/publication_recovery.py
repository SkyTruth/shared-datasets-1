"""Reviewed continuation of one terminal MPAtlas transaction; never a takeover."""
from __future__ import annotations

from copy import deepcopy
import re

from ingestion.common import publication as p, source_quality
from ingestion.common.reset_controls import BUCKET, PROJECT, REGION

KIND = "mpatlas-source-field-v1"
SLUG = source_quality.MPATLAS.SLUG
ASSET_ROOT = source_quality.MPATLAS.ROOT
ROOT = f"gs://{BUCKET}/{ASSET_ROOT}"
CONTROL = f"gs://{BUCKET}/{source_quality.MPATLAS.CONTROL}"
JOB = f"projects/{PROJECT}/locations/{REGION}/jobs/mpatlas-monthly"


def execution(reader, name, *, executor_sha):
    p.require(isinstance(name, str) and re.fullmatch(r"mpatlas-monthly-[a-z0-9]+", name), "invalid original MPAtlas execution")
    value = reader.read(f"https://run.googleapis.com/v2/{JOB}/executions/{name}")
    p.require(value.get("name") == f"{JOB}/executions/{name}" and value.get("job") == JOB,
              "original execution identity differs")
    p.require(value.get("completionTime") and not value.get("reconciling") and not value.get("runningCount")
              and not value.get("succeededCount") and (value.get("failedCount") or value.get("cancelledCount")),
              "original execution must be terminal and unsuccessful")
    containers = value["template"]["containers"]
    p.require(len(containers) == 1, "recovery requires the original single-container publisher")
    container = containers[0]
    p.require(re.fullmatch(r"us-central1-docker.pkg.dev/shared-datasets-1/shared-datasets-jobs/mpatlas-monthly@sha256:[0-9a-f]{64}", container["image"]),
              "original publisher must have an immutable image")
    fields = container.get("env", [])
    p.require(len({item["name"] for item in fields}) == len(fields), "original execution has duplicate environment names")
    values = {item["name"]: item["value"] for item in fields if item["name"].startswith("MPATLAS_") or item["name"] in {"RUN_DATE", "SHARED_DATASETS_EXECUTOR_SHA"}}
    p.require("SHARED_DATASETS_EXECUTOR_SHA" not in values or values["SHARED_DATASETS_EXECUTOR_SHA"] == executor_sha,
              "execution overrides the image's verified executor")
    return {"configuration": {key: value for key, value in values.items() if key != "SHARED_DATASETS_EXECUTOR_SHA"},
            "image": container["image"]}


def validate_plan(plan):
    p.keys(plan, {"asset_slug", "proposal_id", "promotions", "breaking_changes", "release_index_asset_slugs", "publication_recovery"}, "MPAtlas recovery plan")
    p.require(plan["asset_slug"] == SLUG and plan["promotions"] == plan["breaking_changes"] == plan["release_index_asset_slugs"] == [],
              "MPAtlas recovery cannot add promotions, deletions, index rebuilds or contract changes")
    recovery = plan["publication_recovery"]
    p.keys(recovery, {"kind", "execution", "image", "executor_sha", "state", "receipt", "control", "captured_state", "captured_receipt"}, "MPAtlas recovery")
    p.require(recovery["kind"] == KIND, "unsupported MPAtlas recovery")
    p.require(isinstance(recovery["image"], str) and re.fullmatch(r"us-central1-docker.pkg.dev/shared-datasets-1/shared-datasets-jobs/mpatlas-monthly@sha256:[0-9a-f]{64}", recovery["image"]),
              "recovery must name the original immutable publisher image")
    for field in ("state", "receipt", "control"):
        p.identity_snapshot(recovery[field])
    context = p.receipt_context(recovery["captured_receipt"])
    p.require((context.bucket, context.asset_root, context.asset_slug, context.identity_contract) == (BUCKET, ASSET_ROOT, SLUG, None),
              "recovery receipt has a foreign publication context")
    p.require(recovery["executor_sha"] == context.trusted_executor_sha, "original executor identity changed")
    p.require(isinstance(recovery["execution"], str) and re.fullmatch(r"mpatlas-monthly-[a-z0-9]+", recovery["execution"]), "invalid original execution")
    p.require(context.proposal_key == p.digest(p.canonical({"execution": recovery["execution"], "asset": SLUG, "bucket": BUCKET})),
              "receipt belongs to another execution")
    p.require(recovery["state"]["path"] == context.state_uri and recovery["receipt"]["path"] == context.receipt_uri
              and recovery["control"]["path"] == CONTROL, "recovery anchors are outside the original transaction")
    p.require(p.digest(p.canonical(recovery["captured_receipt"])) == recovery["receipt"]["sha256"]
              and p.digest(p.canonical(recovery["captured_state"])) == recovery["state"]["sha256"], "captured evidence differs from reviewed hashes")
    intent, _results = p.parse_receipt(recovery["captured_receipt"], context)
    p.validate_state(recovery["captured_state"], context)
    owner = {"transaction_id": intent.transaction_id, "receipt_uri": context.receipt_uri}
    p.require(recovery["captured_state"]["active"] in (None, owner), "captured state has another owner")
    p.require(intent.value["mode"] == "complete_release" and intent.value["notification"] is None,
              "recovery must retain a notification-free complete-release intent")
    return recovery


def prepare(publisher, store, reader, *, execution_name, proposal_id, verify_image_executor):
    from ingestion.common.source_field_publication import SourceFieldPublication
    from ingestion.mpatlas_monthly.run import ASSET
    reader.check_job_quiescent("mpatlas-monthly")
    key = p.digest(p.canonical({"execution": execution_name, "asset": SLUG, "bucket": BUCKET}))
    receipt = store.read_json(f"{ROOT}/publications/receipts/{key}.json")
    p.require(receipt is not None, "original publication receipt is missing")
    context = p.receipt_context(receipt.value)
    original = execution(reader, execution_name, executor_sha=context.trusted_executor_sha)
    verify_image_executor(original["image"], context.trusted_executor_sha)
    publication = SourceFieldPublication(publisher, ASSET, execution_id=execution_name,
        executor_sha=context.trusted_executor_sha, configuration=original["configuration"], store=store)
    state, receipt, control = (store.read_json(uri) for uri in (publication.context.state_uri, publication.context.receipt_uri, CONTROL))
    p.require(all(item is not None for item in (state, receipt, control)), "original publication evidence is missing")
    plan = {"asset_slug": SLUG, "proposal_id": proposal_id, "promotions": [], "breaking_changes": [], "release_index_asset_slugs": [],
        "publication_recovery": {"kind": KIND, "execution": execution_name, "image": original["image"], "executor_sha": context.trusted_executor_sha,
            "state": state.version.identity(), "receipt": receipt.version.identity(), "control": control.version.identity(),
            "captured_state": state.value, "captured_receipt": receipt.value}}
    validate_plan(plan)
    p.validate_state_references(store, state.value, publication.context)
    p.require(publication.context == p.receipt_context(receipt.value), "execution configuration differs from captured context")
    problems = []
    try:
        validate_control(control, plan["publication_recovery"])
    except p.PublicationError as error:
        problems.append(str(error))
    intent, results = p.parse_receipt(receipt.value, publication.context)
    publication.validate(intent)
    check_readiness(store, intent, results, problems=problems)
    return plan


def validate_control(control, recovery):
    p.require(control is not None and control.version.identity() == recovery["control"], "source-quality control changed")
    state = source_quality.MPATLAS.validate_control(control.value)
    p.require(state["hold"] is None and state["owner"] in (None, recovery["execution"]), "resolve the source-quality hold or foreign claim before recovery")


def check_readiness(store, intent, results, *, problems=()):
    """Inventory all discoverable missing inputs/conflicts before any write."""
    problems = list(problems)
    for operation in intent.value["operations"]:
        key, uri = operation["id"], operation["destination"]
        head = store.head(uri)
        source = operation["source"]
        if key in results:
            actual = store.inspect(uri, head.generation) if head else None
            if actual is None or actual.record() != results[key]:
                problems.append(f"{key}: recorded output generation, bytes or metadata changed")
            continue
        if source["kind"] == "gcs":
            original = store.inspect(source["path"], source["generation"])
            if original is None or original.identity() != {field: source[field] for field in ("path", "generation", "sha256")} or original.size != source["size"]:
                problems.append(f"{key}: captured input is missing or changed")
        if (head.generation if head else 0) == operation["expected_generation"]:
            continue
        metadata = dict(head.metadata) if head else {}
        digest = metadata.get("publication-sha256")
        if not head or not p.hash_value(digest) or metadata != p.operation_tags(intent, operation, digest):
            problems.append(f"{key}: original destination expectation conflicts with a foreign generation")
            continue
        actual = store.inspect(uri, head.generation)
        if actual is None or actual.sha256 != digest or actual.content_type != operation["content_type"] or actual.cache_control != operation["cache_control"]:
            problems.append(f"{key}: interrupted owned output has changed bytes or metadata")
    p.require(not problems, "recovery is blocked:\n" + "\n".join(problems))


def recover(publisher, store, plan, reader, *, check_authority_and_jobs, verify_image_executor):
    from ingestion.common.source_field_publication import SourceFieldPublication
    from ingestion.mpatlas_monthly.run import ASSET
    recovery = validate_plan(plan)
    check_authority_and_jobs()
    original = execution(reader, recovery["execution"], executor_sha=recovery["executor_sha"])
    p.require(original["image"] == recovery["image"], "original image changed")
    verify_image_executor(original["image"], recovery["executor_sha"])
    publication = SourceFieldPublication(publisher, ASSET, execution_id=recovery["execution"],
        executor_sha=recovery["executor_sha"], configuration=original["configuration"], store=store)
    context = publication.context
    p.require(context == p.receipt_context(recovery["captured_receipt"]), "original execution configuration changed")
    current = store.read_json(context.receipt_uri)
    state = store.read_json(context.state_uri)
    p.require(current is not None and state is not None, "original receipt or state disappeared")
    intent, results = p.parse_receipt(current.value, context)
    captured_intent, captured_results = p.parse_receipt(recovery["captured_receipt"], context)
    p.require(intent == captured_intent and all(results.get(key) == value for key, value in captured_results.items()), "original intent or recorded results changed")
    if current.value == recovery["captured_receipt"]:
        p.require(current.version.identity() == recovery["receipt"], "unchanged receipt has a different generation")
    p.validate_state(state.value, context)
    p.validate_state_references(store, state.value, context)
    owner = {"transaction_id": intent.transaction_id, "receipt_uri": context.receipt_uri}
    complete = len(results) == len(intent.value["operations"])
    if complete and state.value["active"] is None:
        # Never reacquire an old receipt or clear a later run's source claim.
        control = store.read_json(CONTROL)
        p.require(control is not None, "source-quality control disappeared")
        if state.value["current"]["transaction_id"] != intent.transaction_id or control.version.identity() != recovery["control"]:
            return {"status": "already_completed", "receipt": current.version.identity()}
    else:
        p.require({key: value for key, value in state.value.items() if key not in {"active", "current"}}
                  == {key: value for key, value in recovery["captured_state"].items() if key not in {"active", "current"}},
                  "retained publication identity state changed")
        p.require(state.value["active"] in (None, owner), "another transaction owns the asset")
        if state.version.identity() != recovery["state"]:
            claimed = {**recovery["captured_state"], "active": owner}
            p.require(state.value == claimed or (state.value["active"] == owner
                      and state.value["current"]["transaction_id"] == intent.transaction_id),
                      "original publication state changed outside this recovery")

    def guard():
        check_authority_and_jobs()
        validate_control(store.read_json(CONTROL), recovery)

    class CheckedStore:
        head, list_heads, read_json, inspect = store.head, store.list_heads, store.read_json, store.inspect

        def write_json(self, *args):
            guard()
            return store.write_json(*args)

        def write_bytes(self, *args):
            guard()
            return store.write_bytes(*args)

        def copy(self, *args):
            guard()
            return store.copy(*args)

        def upload(self, *args):
            guard()
            return store.upload(*args)

    guard()
    check_readiness(store, intent, results)
    publication.store = CheckedStore()
    # Reconstruct the original engine, preserving the original context and CAS.
    publication.engine = p.Executor(publication.store, validate_semantics=publication.validate,
        preflight_sources=publication.preflight, derive=publication.engine.derive)
    result = publication.resume()
    p.require(result is not None and store.read_json(context.state_uri).value["active"] is None, "recovery did not finish its original transaction")
    guard()
    control = store.read_json(CONTROL)
    validate_control(control, recovery)
    if control.value["owner"] is not None:
        cleared = deepcopy(control.value)
        cleared["owner"] = None
        store.write_bytes(CONTROL, source_quality.encode(cleared), control.version.generation,
            dict(control.version.metadata), control.version.content_type, control.version.cache_control)
    return {"status": "completed", "receipt": store.read_json(context.receipt_uri).version.identity(), "run_record": result["run_record"]}
