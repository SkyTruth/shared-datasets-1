"""Generated release bundles published under the existing durable owner.

The adapter consumes explicit reset/adoption state. It never installs that state
or falls back to the unowned writer. Runtime identity is fixed before discovery;
an incomplete receipt resumes its captured inputs or fails with its claim held.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping

from ingestion.common import feature_metadata, publication as p, release_index
from ingestion.common.gcs import GcsPublisher
from ingestion.common.identity_reset import ASSET_ROOTS, CONTRACT_ID, load_reset_candidate
from ingestion.common.publication_gcs import GcsStore
from ingestion.common.runtime import content_type_for
from scripts import release_feature_model as model, vector_asset


def blob_info(version: p.ObjectVersion) -> dict[str, Any]:
    return {"path": version.path, "generation": version.generation, "size": version.size}


def derive(parameters: dict[str, Any], results: Mapping[str, p.ObjectVersion]) -> bytes:
    kind = parameters["kind"]
    if kind == "manifest":
        payload = feature_metadata.final_manifest_payload(
            **parameters["payload"],
            release_blob_info_by_role={role: blob_info(results[f"release-{role}"]) for role in ("fgb", "pmtiles", "metadata", "schema")},
            latest_blob_info_by_role={role: blob_info(results[f"latest-{role}"]) for role in ("fgb", "pmtiles", "metadata", "schema")},
        )
    elif kind == "run":
        payload = {**parameters["payload"],
                   "release_paths": [blob_info(results[key]) for key in parameters["release_operations"]],
                   "latest_paths": [blob_info(results[key]) for key in parameters["latest_operations"]],
                   "sha256": {**parameters["hashes"], "manifest": results["release-manifest"].sha256}}
    elif kind == "index":
        run_parameters = parameters["run_parameters"]
        record = p.strict_json(derive(run_parameters, results))
        run_info = blob_info(results["run-record"])
        payload = release_index.merge_successful_release(
            parameters["index"], release_index.release_entry_from_record(record, run_record_info=run_info),
            latest_run=release_index.run_entry_from_record(record, run_record_info=run_info), updated_at=parameters["updated_at"],
        )
    elif kind == "skip-index":
        payload = release_index.merge_latest_run(parameters["index"], release_index.run_entry_from_record(parameters["record"]), updated_at=parameters["updated_at"])
    else:
        raise p.PublicationError("unsupported generated publication derivation")
    return p.canonical(payload)


class OwnedGeneratedPublisher(GcsPublisher):
    def __init__(self, client, bucket_name: str, *, execution_id: str, executor_sha: str, configuration: dict[str, Any], store=None, **kwargs):
        super().__init__(client, bucket_name, **kwargs)
        p.require(isinstance(execution_id, str) and bool(execution_id.strip()), "stable execution identity is required")
        self.store = store if store is not None else GcsStore(client)
        self.execution_id = execution_id
        self.executor_sha = executor_sha
        self.configuration_digest = p.digest(p.canonical(configuration))
        self.engine = p.Executor(self.store, validate_semantics=self.validate_semantics, preflight_sources=self.preflight, derive=derive)

    @classmethod
    def from_runtime(cls, client, bucket_name: str, **kwargs):
        configuration = {key: value for key, value in os.environ.items() if key.startswith(("WDPA_", "SEA_ICE_")) or key in {"RUN_DATE", "ALLOW_SAMPLED_PUBLISH"}}
        return cls(client, bucket_name, execution_id=os.environ.get("CLOUD_RUN_EXECUTION", ""),
                   executor_sha=os.environ.get("SHARED_DATASETS_EXECUTOR_SHA", ""), configuration=configuration, **kwargs)

    def context(self, asset) -> p.Context:
        p.require(asset.slug in ASSET_ROOTS and asset.root == ASSET_ROOTS[asset.slug], "unmanaged generated asset")
        key = p.digest(p.canonical({"execution": self.execution_id, "asset": asset.slug, "bucket": self.bucket.name}))
        return p.Context(key, self.configuration_digest, self.executor_sha, p.FINALIZATION_VERSION,
                         self.bucket.name, asset.root, asset.slug, CONTRACT_ID)

    def state(self, asset):
        state = self.engine._state(self.context(asset))
        p.require(state.value["current"] is not None, "managed generated assets require an explicit reset baseline")
        return state

    def resume(self, asset) -> dict[str, Any] | None:
        context = self.context(asset)
        self.state(asset)
        if self.store.read_json(context.receipt_uri) is None:
            return None
        def refuse_prepare():
            raise p.PublicationError("existing execution receipt disappeared")
        receipt = self.engine.run(context, prepare=refuse_prepare, local_sources={})
        return self.result(receipt)

    @staticmethod
    def result(receipt: dict[str, Any]) -> dict[str, Any]:
        intent = p.Intent.build(receipt["intent"])
        results = {key: p.ObjectVersion.parse(value) for key, value in receipt["results"].items()}
        operations = {op["id"]: op for op in intent.value["operations"]}
        if "run-record" not in operations:
            return operations["release-index"]["source"]["parameters"]["record"]
        parameters = operations["run-record"]["source"]["parameters"]
        return {**p.strict_json(derive(parameters, results)), "run_record": blob_info(results["run-record"])}

    def load_generated_identity_baseline(self, asset, *, contract_id: str):
        p.require(contract_id == CONTRACT_ID, "unexpected runtime identity contract")
        state = self.state(asset).value
        p.require(state["active"] is None, "publication is incomplete; resume its original execution")
        if state["current"]["receipt_uri"] == state["adoption_receipt"]:
            adoption = self.store.read_json(state["adoption_receipt"]).value
            reset = load_reset_candidate(self.store, self.context(asset), adoption)
            return reset.allocation_baseline()
        baseline = super().load_generated_identity_baseline(asset, contract_id=contract_id)
        p.require(asdict(baseline.snapshot) == state["current"]["latest_manifest"], "latest manifest differs from publication state")
        p.require(baseline.next_feature_id == state["reserved_next_feature_id"], "manifest sequence differs from durable reservation")
        return baseline

    def load_successful_run_record(self, asset, run_date):
        state = self.state(asset).value
        current = state["current"]
        # Legacy success records are historical facts, never new-contract skips.
        if current["receipt_uri"] == state["adoption_receipt"] or current["release"] != run_date.isoformat():
            return None
        receipt = self.store.read_json(current["receipt_uri"])
        record = self.result(receipt.value)
        return record, record["run_record"]

    def committed_artifacts(self, asset, *, suffixes):
        """Read build inputs from the owned receipt, never mutable latest aliases."""
        state = self.state(asset).value
        p.require(state["active"] is None, "cannot load translation inputs during an incomplete publication")
        current = state["current"]
        if current["receipt_uri"] == state["adoption_receipt"]:
            return None
        receipt = self.store.read_json(current["receipt_uri"])
        prefix = f"gs://{self.bucket.name}/{asset.root}/releases/{current['release']}/{asset.slug}"
        results = {version.path: version for version in (p.ObjectVersion.parse(value) for value in receipt.value["results"].values())}
        p.require(all(prefix + suffix in results for suffix in suffixes), "committed translation source bundle is incomplete")
        return {suffix: results[prefix + suffix] for suffix in suffixes}

    def record_existing_successful_release(self, asset, run_date):
        # The owned commit already updated this index. Do not re-activate history.
        loaded = self.load_successful_run_record(asset, run_date)
        p.require(loaded is not None, "release is not current in the new identity contract")
        version = self.store.head(release_index.release_index_uri(self.bucket.name, asset.slug))
        return {"path": version.path, "generation": version.generation, "size": version.size} if version else None

    def update_latest_run_index(self, *, asset, payload, run_record_info=None):
        context = self.context(asset)
        state = self.state(asset).value
        p.require(state["active"] is None, "cannot write a skip while publication is incomplete")
        p.require(state["current"]["receipt_uri"] != state["adoption_receipt"], "first new-contract release must complete before recording skips")
        uri = release_index.release_index_uri(self.bucket.name, asset.slug)
        loaded = self.store.read_json(uri)
        p.require(loaded is not None, "committed release index is missing")
        source = {"kind": "derived", "version": p.FINALIZATION_VERSION, "parameters": {
            "kind": "skip-index", "index": loaded.value, "record": payload, "updated_at": dt.datetime.now(dt.UTC).isoformat()}, "dependencies": []}
        intent = p.Intent.build({"schema_version": 1, "context": asdict(context), "mode": "object_repair",
                                "release": state["current"]["release"], "predecessor": state["current"]["latest_manifest"],
                                "reservation": None, "prepared_at": source["parameters"]["updated_at"], "notification": None,
                                "operations": [self.operation("release-index", "asset_derived", uri, source, loaded.version.generation, asset.slug)]})
        receipt = self.engine.run(context, prepare=lambda: intent, local_sources={})
        return blob_info(p.ObjectVersion.parse(receipt["results"]["release-index"]))

    @staticmethod
    def operation(key, phase, destination, source, expected, slug, metadata=None):
        return {"id": key, "phase": phase, "destination": destination, "expected_generation": expected,
                "source": source, "content_type": content_type_for(Path(destination)), "cache_control": "no-cache",
                "user_metadata": metadata if metadata is not None else {"asset_slug": slug}}

    def preflight(self, intent):
        for op in intent.value["operations"]:
            head = self.store.head(op["destination"])
            p.require((head.generation if head else 0) == op["expected_generation"], "original publication destination changed")

    def validate_semantics(self, intent):
        value = intent.value
        context = p.Context(**value["context"])
        p.require(context.identity_contract == CONTRACT_ID and ASSET_ROOTS.get(context.asset_slug) == context.asset_root, "unsupported generated publication contract")
        operations = {op["id"]: op for op in value["operations"]}
        if value["mode"] == "object_repair":
            p.require(set(operations) == {"release-index"} and operations["release-index"]["source"]["parameters"]["kind"] == "skip-index", "only run-index skips are supported without a release")
            return
        p.require(value["mode"] == "complete_release", "unsupported generated publication mode")
        parameters = operations["release-manifest"]["source"]["parameters"]
        identity = parameters["payload"]["identity"]
        model.validate_identity_metadata(identity)
        p.require(identity["contract_id"] == context.identity_contract, "manifest belongs to another identity contract")
        p.require(value["reservation"] == {"start": identity["next_generated_feature_id_before_release"], "next": identity["next_generated_feature_id_after_release"]}, "manifest sequence does not match reservation")
        for role in ("fgb", "pmtiles", "metadata", "schema"):
            p.require(f"release-{role}" in operations and f"latest-{role}" in operations, "incomplete vector bundle")
        p.require("run-record" in operations and "release-index" in operations, "publication must include run and release-index effects")

    def publish_generated(self, *, asset, run_date, outputs, identity, object_metadata, source_inputs, record, extra_suffix_paths=()):
        context = self.context(asset)
        existing = self.resume(asset)
        if existing is not None:
            return existing
        state = self.state(asset).value
        p.require(state["active"] is None, "another publication owns the asset")
        current = state["current"]
        reset_pending = current["receipt_uri"] == state["adoption_receipt"]
        if reset_pending:
            adoption = self.store.read_json(state["adoption_receipt"]).value
            reset = load_reset_candidate(self.store, context, adoption)
            p.require(run_date.isoformat() == reset.value["first_release"] and outputs.previous_release is None and outputs.identity_baseline_snapshot is None,
                      "reset build must use the approved empty baseline and release")
        else:
            p.require(outputs.identity_baseline_snapshot is not None and asdict(outputs.identity_baseline_snapshot) == current["latest_manifest"], "builder used a stale identity baseline")
        p.require(outputs.identity_contract == CONTRACT_ID, "builder belongs to another identity contract")
        roles = {role: (suffix, getattr(outputs, role)) for role, suffix in feature_metadata.ROLE_SUFFIXES.items() if role != "manifest"}
        for index, (suffix, path) in enumerate(extra_suffix_paths):
            roles[f"extra-{index}"] = (suffix, path)
        native = vector_asset.validate_metadata_lookup_bundle(outputs.fgb, outputs.pmtiles)
        p.require(native.valid, "native generated bundle validation failed: " + "; ".join(native.errors))
        validation = model.validate_sidecar_records(model.read_metadata_sidecar(outputs.metadata), expected_asset_slug=asset.slug, expected_release=run_date.isoformat())
        p.require(validation.valid and validation.feature_count == outputs.row_count, "generated metadata bundle is invalid")
        records = tuple(model.project_identity_records(model.read_metadata_sidecar(outputs.metadata)))
        model.GeneratedIdentityBaseline(records, outputs.next_generated_feature_id, run_date.isoformat(), contract_id=CONTRACT_ID)
        p.require(p.strict_json(outputs.schema.read_bytes()) == outputs.schema_payload, "schema bytes differ from manifest schema")
        model.validate_release_schema(outputs.schema_payload, expected_asset_slug=asset.slug, expected_release=run_date.isoformat())
        operations, local_sources = [], {}
        for index, (role, (suffix, path)) in enumerate(roles.items()):
            with path.open("rb") as handle:
                sha = hashlib.file_digest(handle, "sha256").hexdigest()
            if role in outputs.sha256:
                p.require(sha == outputs.sha256[role], "generated artifact changed since validation")
            local_sources[role] = path
            source = {"kind": "local", "key": role, "sha256": sha, "size": path.stat().st_size}
            checkpoint_suffix = suffix if suffix != ".metadata-translations.csv" else ".csv"
            operations.append(self.operation(f"input-{role}", "checkpoint", f"gs://{context.bucket}/{asset.root}/publications/inputs/{context.proposal_key}/{index}{checkpoint_suffix}", source, 0, asset.slug))
        for prefix in ("release", "latest"):
            for role, (suffix, _path) in roles.items():
                destination = f"gs://{context.bucket}/" + (asset.release_object(run_date, suffix) if prefix == "release" else asset.latest_object(suffix))
                head = self.store.head(destination)
                expected = (head.generation if head else 0) if prefix == "latest" else 0
                source = {"kind": "result", "operation": f"input-{role}" if prefix == "release" else f"release-{role}"}
                operations.append(self.operation(f"{prefix}-{role}", "data", destination, source, expected, asset.slug, object_metadata))
        payload = {"asset_slug": asset.slug, "release": run_date.isoformat(), "bucket_name": context.bucket, "asset_root": asset.root,
                   "sha256_by_role": outputs.sha256, "schema": outputs.schema_payload, "source_inputs": source_inputs, "identity": identity,
                   "feature_count": outputs.row_count, "manifest_release_path": f"gs://{context.bucket}/{asset.release_object(run_date, '.manifest.json')}",
                   "manifest_latest_path": f"gs://{context.bucket}/{asset.latest_object('.manifest.json')}"}
        dependencies = [op["id"] for op in operations if op["phase"] == "data"]
        source = {"kind": "derived", "version": p.FINALIZATION_VERSION, "parameters": {"kind": "manifest", "payload": payload}, "dependencies": dependencies}
        operations.append(self.operation("release-manifest", "commit", payload["manifest_release_path"], source, 0, asset.slug, object_metadata))
        operations.append(self.operation("latest-manifest", "commit", payload["manifest_latest_path"], {"kind": "result", "operation": "release-manifest"}, current["latest_manifest"]["generation"], asset.slug, object_metadata))
        ordered_roles = ["fgb", "pmtiles", "metadata", "schema", "manifest", *[role for role in roles if role.startswith("extra-")]]
        run_parameters = {"kind": "run", "payload": {**record, "identity_contract": CONTRACT_ID}, "hashes": outputs.sha256,
                          "release_operations": [f"release-{role}" for role in ordered_roles], "latest_operations": [f"latest-{role}" for role in ordered_roles]}
        dependencies = [op["id"] for op in operations if op["phase"] in {"data", "commit"}]
        operations.append(self.operation("run-record", "asset_derived", f"gs://{context.bucket}/{asset.run_record_object(run_date)}",
                                         {"kind": "derived", "version": p.FINALIZATION_VERSION, "parameters": run_parameters, "dependencies": dependencies}, 0, asset.slug))
        index_uri = release_index.release_index_uri(context.bucket, asset.slug)
        index = self.store.read_json(index_uri)
        now = dt.datetime.now(dt.UTC).isoformat()
        parameters = {"kind": "index", "run_parameters": run_parameters, "index": release_index.coerce_release_index(index.value if index else None, asset.slug), "updated_at": now}
        operations.append(self.operation("release-index", "asset_derived", index_uri,
                                         {"kind": "derived", "version": p.FINALIZATION_VERSION, "parameters": parameters, "dependencies": [*dependencies, "run-record"]}, index.version.generation if index else 0, asset.slug))
        intent = p.Intent.build({"schema_version": 1, "context": asdict(context), "mode": "complete_release", "release": run_date.isoformat(),
                                "predecessor": current["latest_manifest"], "reservation": {"start": outputs.previous_generated_feature_id, "next": outputs.next_generated_feature_id},
                                "prepared_at": now, "operations": operations, "notification": None})
        return self.result(self.engine.run(context, prepare=lambda: intent, local_sources=local_sources))

    def upload_new_object(self, **kwargs):
        raise p.PublicationError("generated assets require owned publication")

    def replace_latest_object(self, **kwargs):
        raise p.PublicationError("generated assets require owned publication")

    def write_run_record(self, **kwargs):
        raise p.PublicationError("generated run records require owned publication")

    def replace_latest_metadata_from_run_record(self, *args, **kwargs):
        raise p.PublicationError("generated metadata updates require owned publication")
