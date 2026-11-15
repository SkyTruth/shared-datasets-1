"""Generated release bundles published under the existing durable owner.

The adapter consumes explicit reset/adoption state. It never installs that state
or falls back to the unowned writer. Runtime identity is fixed before discovery;
an incomplete receipt resumes its captured inputs or fails with its claim held.
"""

from __future__ import annotations

import datetime as dt
import os
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any

from ingestion.common import publication as p, release_index
from ingestion.common.gcs import GcsPublisher
from ingestion.common.identity_reset import ASSET_ROOTS, CONTRACT_ID, load_reset_candidate, translation_update_release
from ingestion.common.publication_gcs import GcsStore
from ingestion.common.publication_bundle import blob_info, derive, operation as bundle_operation, prepare_bundle
from ingestion.common import release_feature_model as model


def validate_translation_template(payload, baseline, locales):
    model.validate_translation_bundle_manifest(payload, locales)
    p.require({key: value for key, value in payload.items() if key not in {"artifacts", "translations"}} ==
              {key: value for key, value in baseline.items() if key not in {"artifacts", "translations"}},
              "translation update cannot change release identity, schema, or source metadata")
    def base_artifacts(manifest):
        return [entry for entry in manifest["artifacts"] if not entry["path"].endswith(".metadata-translations.csv")
                and not re.search(r"\.metadata\.[a-z]{2,3}(?:_[a-z0-9]{2,8})*\.ndjson\.gz$", entry["path"])]
    p.require(base_artifacts(payload) == base_artifacts(baseline), "translation update cannot change base artifact snapshots")




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

    def load_generated_identity_baseline(self, asset, *, contract_id: str, identity_index_path: Path | None = None):
        p.require(contract_id == CONTRACT_ID, "unexpected runtime identity contract")
        state = self.state(asset).value
        p.require(state["active"] is None, "publication is incomplete; resume its original execution")
        if state["current"]["receipt_uri"] == state["adoption_receipt"]:
            adoption = self.store.read_json(state["adoption_receipt"]).value
            reset = load_reset_candidate(self.store, self.context(asset), adoption)
            return reset.allocation_baseline()
        baseline = super().load_generated_identity_baseline(asset, contract_id=contract_id, identity_index_path=identity_index_path)
        try:
            p.require(asdict(baseline.snapshot) == state["current"]["latest_manifest"], "latest manifest differs from publication state")
            p.require(baseline.next_feature_id == state["reserved_next_feature_id"], "manifest sequence differs from durable reservation")
        except BaseException:
            if isinstance(baseline.records, model.IdentityRecordIndex):
                baseline.records.close()
            raise
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

    def latest_success_record(self, asset):
        """Use durable committed state, never an attempted or skipped run."""
        state = self.state(asset).value
        p.require(state["active"] is None, "publication is incomplete; resume its original execution")
        if state["current"]["receipt_uri"] == state["adoption_receipt"]:
            return None
        receipt = self.store.read_json(state["current"]["receipt_uri"])
        p.require(receipt is not None, "committed successful receipt is missing")
        record = self.result(receipt.value)
        p.require(record.get("status") == "success", "committed publication run is not successful")
        return record

    def committed_artifacts(self, asset, *, suffixes):
        """Read the committed manifest, including reviewed translation edits."""
        state = self.state(asset).value
        p.require(state["active"] is None, "cannot load translation inputs during an incomplete publication")
        current = state["current"]
        if current["receipt_uri"] == state["adoption_receipt"]:
            return None
        receipt = self.store.read_json(current["receipt_uri"])
        prefix = f"gs://{self.bucket.name}/{asset.root}/releases/{current['release']}/{asset.slug}"
        manifest = self.store.read_json(current["release_manifest"]["path"])
        p.require(manifest is not None and manifest.version.identity() == current["release_manifest"], "committed manifest snapshot changed")
        model.validate_release_manifest(manifest.value, require_generations=True)
        artifacts = {entry["path"]: entry for entry in manifest.value["artifacts"]}
        results = {}
        # Persisted v1 manifests omitted extras; their receipt is the only
        # recorded source for those files. New manifests must be complete.
        legacy = any(op["source"].get("parameters", {}).get("kind") == "manifest" for op in receipt.value["intent"]["operations"])
        old_results = {value["path"]: value for value in receipt.value["results"].values()} if legacy else {}
        for suffix in suffixes:
            path = prefix + suffix
            entry = artifacts.get(path) or old_results.get(path)
            p.require(entry is not None, "committed translation source bundle is incomplete")
            # The downloader verifies these captured bytes once, while streaming.
            results[path] = p.ObjectVersion(path, entry["generation"], str(entry["sha256"]).removeprefix("sha256:"), entry["size"])
        return {suffix: results[prefix + suffix] for suffix in suffixes}

    def reset_translation_supplement(self, asset):
        """Return the immutable first-build input approved by reset adoption."""
        state = self.state(asset).value
        p.require(state["current"]["receipt_uri"] == state["adoption_receipt"] and state["active"] is None,
                  "translation supplement is only available before the first owned publication")
        adoption = self.store.read_json(state["adoption_receipt"]).value
        candidate = load_reset_candidate(self.store, self.context(asset), adoption)
        snapshot = candidate.value["translation_supplement"]
        p.require(snapshot is not None, "asset has no approved translation supplement")
        version = self.store.inspect(snapshot["path"], snapshot["generation"])
        p.require(version is not None and version.identity() == snapshot, "approved translation supplement is missing or changed")
        return version

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

    operation = staticmethod(bundle_operation)

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
            # These inputs are captured completely in the intent. Validate their
            # derived bytes before persisting a receipt or acquiring ownership.
            derive(operations["release-index"]["source"]["parameters"], {})
            return
        if value["mode"] == "metadata_update":
            parameters = operations["release-manifest"]["source"]["parameters"]
            p.require(parameters["kind"] == "translation-manifest-v1", "unsupported metadata derivation")
            p.require(parameters["previous_current"]["latest_manifest"] == value["predecessor"],
                      "translation update must retain its committed predecessor")
            validate_translation_template(parameters["payload"], parameters["baseline"], parameters["locales"])
            translation_update_release({"asset_slug": context.asset_slug, "promotions": [
                {"destination_uri": op["destination"]} for op in value["operations"] if op["phase"] in {"data", "commit"}
            ]}, bucket=context.bucket, locales=parameters["locales"])
            p.require("run-record" in operations and "release-index" in operations, "metadata update requires run and index effects")
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

    def publish_translation_update(self, *, asset, plan):
        """Apply a reviewed current-release language bundle using the same owner."""
        from scripts import catalog_csv

        context = self.context(asset)
        existing = self.resume(asset)
        if existing is not None:
            return existing
        state = self.state(asset).value
        current = state["current"]
        p.require(state["active"] is None, "another publication owns the asset")
        p.require(current["receipt_uri"] != state["adoption_receipt"], "translation updates require a completed new-contract release")
        row = catalog_csv.catalog_row(asset.slug)
        locales = row["translation_locales"].split(";")
        release = translation_update_release(plan, bucket=context.bucket, locales=locales)
        p.require(release == current["release"], "translation update must target the current release")
        promotions = {item["destination_uri"]: item for item in plan["promotions"]}
        baseline = self.store.read_json(current["release_manifest"]["path"])
        p.require(baseline is not None and baseline.version.identity() == current["release_manifest"], "current release manifest changed")
        for snapshot in (current["release_manifest"], current["latest_manifest"]):
            p.require(int(promotions[snapshot["path"]]["destination_generation"] or 0) == snapshot["generation"], "reviewed manifest generation is no longer current")

        # Validate the prepared manifest and every pinned source before claiming
        # ownership. The executor captures the exact bytes for crash recovery.
        sources = {}
        for target, promotion in promotions.items():
            version = self.store.inspect(promotion["source_uri"], int(promotion["source_generation"]))
            p.require(version is not None, "reviewed translation source is missing")
            sources[target] = version
        manifest_source = sources[current["release_manifest"]["path"]]
        staged = self.store.read_json(manifest_source.path)
        p.require(staged is not None and staged.version.identity() == manifest_source.identity(), "reviewed manifest source changed")
        p.require(sources[current["latest_manifest"]["path"]].sha256 == manifest_source.sha256, "release and latest must use the same prepared manifest")
        template = staged.value
        validate_translation_template(template, baseline.value, locales)
        artifacts = {entry["path"]: entry for entry in template["artifacts"]}
        suffixes = [("translation-source", ".metadata-translations.csv"), *((f"metadata-{locale.replace('_', '-')}", f".metadata.{locale}.ndjson.gz") for locale in locales)]
        operations = []
        hash_roles = {"csv": "release-translation-source"}
        for role, suffix in suffixes:
            uri = f"gs://{context.bucket}/{asset.root}/releases/{release}/{asset.slug}{suffix}"
            source = sources[uri]
            entry = artifacts.get(uri)
            p.require(entry is not None and str(entry["sha256"]).removeprefix("sha256:") == source.sha256 and entry["size"] == source.size, "prepared artifact differs from reviewed source")
            latest_uri = uri.replace(f"/releases/{release}/", "/latest/")
            p.require(sources[latest_uri].sha256 == source.sha256, "release and latest translation bytes must agree")
            for prefix, target in (("release", uri), ("latest", latest_uri)):
                promotion = promotions[target]
                captured = {"kind": "gcs", "path": source.path, "generation": source.generation, "sha256": source.sha256, "size": source.size} if prefix == "release" else {"kind": "result", "operation": f"release-{role}"}
                operations.append(self.operation(f"{prefix}-{role}", "data", target, captured, int(promotion["destination_generation"] or 0), asset.slug))
            hash_roles["metadata_translations" if role == "translation-source" else role.replace("-", "_")] = f"release-{role}"
        data_ids = [op["id"] for op in operations]
        parameters = {"kind": "translation-manifest-v1", "payload": template, "baseline": baseline.value,
                      "locales": locales, "previous_current": current}
        operations.append(self.operation("release-manifest", "commit", current["release_manifest"]["path"],
            {"kind": "derived", "version": p.FINALIZATION_VERSION, "parameters": parameters, "dependencies": data_ids}, current["release_manifest"]["generation"], asset.slug))
        operations.append(self.operation("latest-manifest", "commit", current["latest_manifest"]["path"],
            {"kind": "result", "operation": "release-manifest"}, current["latest_manifest"]["generation"], asset.slug))
        for operation in operations:
            promotion = promotions[operation["destination"]]
            for field in ("content_type", "cache_control"):
                if promotion.get(field):
                    operation[field] = promotion[field]
        dependencies = [op["id"] for op in operations]
        run_uri = f"gs://{context.bucket}/{asset.run_record_object(dt.date.fromisoformat(release))}"
        run = self.store.read_json(run_uri)
        p.require(run is not None, "current release run record is missing")
        record = {**run.value, "localization": {"translation_locales": locales, "translation_fields": row["translation_fields"].split(";"), "translations": template["translations"]}}
        run_parameters = {"kind": "translation-run-v1", "payload": record, "hash_roles": hash_roles, "artifact_operations": dependencies}
        operations.append(self.operation("run-record", "asset_derived", run_uri,
            {"kind": "derived", "version": p.FINALIZATION_VERSION, "parameters": run_parameters, "dependencies": dependencies}, run.version.generation, asset.slug))
        index_uri = release_index.release_index_uri(context.bucket, asset.slug)
        index = self.store.read_json(index_uri)
        p.require(index is not None, "current release index is missing")
        now = dt.datetime.now(dt.UTC).isoformat()
        parameters = {"kind": "index", "run_parameters": run_parameters, "index": index.value, "updated_at": now}
        operations.append(self.operation("release-index", "asset_derived", index_uri,
            {"kind": "derived", "version": p.FINALIZATION_VERSION, "parameters": parameters, "dependencies": [*dependencies, "run-record"]}, index.version.generation, asset.slug))
        high_water = state["reserved_next_feature_id"]
        intent = p.Intent.build({"schema_version": 1, "context": asdict(context), "mode": "metadata_update", "release": release,
            "predecessor": current["latest_manifest"], "reservation": {"start": high_water, "next": high_water},
            "prepared_at": now, "operations": operations, "notification": None})
        return self.result(self.engine.run(context, prepare=lambda: intent, local_sources={}))

    def publish_generated(self, *, asset, run_date, outputs, identity, object_metadata, source_inputs, record, extra_suffix_paths=(), asset_readme: Path | None = None, translations=None):
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
        intent, local_sources = prepare_bundle(context=context, store=self.store, asset=asset, run_date=run_date,
            outputs=outputs, object_metadata=object_metadata, source_inputs=source_inputs, identity=identity,
            record={**record, "identity_contract": CONTRACT_ID}, current=current,
            reservation={"start": outputs.previous_generated_feature_id, "next": outputs.next_generated_feature_id},
            translations=translations, extra_suffix_paths=extra_suffix_paths, asset_readme=asset_readme)
        return self.result(self.engine.run(context, prepare=lambda: intent, local_sources=local_sources))

    def upload_new_object(self, **kwargs):
        raise p.PublicationError("generated assets require owned publication")

    def replace_latest_object(self, **kwargs):
        raise p.PublicationError("generated assets require owned publication")

    def write_run_record(self, **kwargs):
        raise p.PublicationError("generated run records require owned publication")

    def replace_latest_metadata_from_run_record(self, *args, **kwargs):
        raise p.PublicationError("generated metadata updates require owned publication")
