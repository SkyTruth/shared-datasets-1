"""Durable source-field publication under the existing ingestion ownership guard.

Source IDs never allocate a sequence. The first managed run adopts a verified
published baseline without rewriting it. Complete inputs are staged before an
intent exists; once captured, recovery needs neither upstream data nor local files.
"""
from __future__ import annotations

from dataclasses import asdict
import datetime as dt
import os

from ingestion.common import publication as p, published_baseline, release_index
from ingestion.common import release_feature_model as model
from ingestion.common.publication_bundle import blob_info, derive, prepare_bundle
from ingestion.common.publication_gcs import GcsStore


def adopt_verified_baseline(publisher, asset, baseline, *, store, replace=False):
    """Called only while the source-quality guard owns the asset.

    Replacement is reserved for a completed reviewed promotion. An ordinary
    scheduled run never repairs or replaces existing managed authority.
    """
    state_uri = f"gs://{publisher.bucket.name}/{asset.root}/publications/state.json"
    previous = store.read_json(state_uri)
    if previous is not None:
        p.require(previous.value["active"] is None, "an incomplete publication requires its original execution")
        if not replace:
            return
    p.require(baseline is not None, "source-field adoption requires a verified published baseline")
    verified = published_baseline.read(publisher, asset, baseline["release"])
    p.require(verified.manifest["identity"]["strategy"] == "source_field" and verified.manifest["identity"]["source_fields"] == ["zone_id"], "cannot adopt generated identity as source-field")
    release = verified.manifest["release"]
    dated = store.read_json(f"gs://{publisher.bucket.name}/{asset.release_object(dt.date.fromisoformat(release), '.manifest.json')}")
    latest = store.read_json(f"gs://{publisher.bucket.name}/{asset.latest_object('.manifest.json')}")
    p.require(dated is not None and latest is not None and dated.value == latest.value == verified.manifest,
              "published release and latest manifests disagree")
    captured = next(ref for ref in baseline["release"]["files"] if ref["path"] == dated.version.path)
    p.require(dated.version.generation == int(captured["generation"]), "adoption baseline generation changed")
    index = store.read_json(release_index.release_index_uri(publisher.bucket.name, asset.slug))
    p.require(index is not None and index.version.generation == int(baseline["index"]["generation"]), "adoption release index changed")
    anchor = {"release": release, "release_manifest": dated.version.identity(), "latest_manifest": latest.version.identity()}
    record = {"schema_version": 1, "kind": "adoption", "asset_slug": asset.slug, "asset_root": asset.root,
              "bucket": publisher.bucket.name, "baseline": anchor, "reserved_next_feature_id": None,
              "evidence_sha256": p.digest(p.canonical({"baseline": baseline, "manifests": anchor})),
              "identity_contract": None, "reset_release": None}
    digest = p.digest(p.canonical(record))
    adoption_uri = f"gs://{publisher.bucket.name}/{asset.root}/publications/receipts/{digest}.json"
    existing = store.read_json(adoption_uri)
    if existing is None:
        store.write_json(adoption_uri, record, 0)
    else:
        p.require(existing.value == record, "adoption evidence differs")
    state = {"schema_version": 1, "asset_slug": asset.slug, "adoption_receipt": adoption_uri,
             "reserved_next_feature_id": None, "current": {**anchor, "transaction_id": digest, "receipt_uri": adoption_uri}, "active": None}
    store.write_json(state_uri, state, previous.version.generation if previous else 0)


class SourceFieldPublication:
    def __init__(self, publisher, asset, *, execution_id, executor_sha, configuration, store=None):
        p.require(isinstance(execution_id, str) and bool(execution_id.strip()), "stable execution identity is required")
        self.publisher, self.asset = publisher, asset
        self.store = store if store is not None else GcsStore(publisher.bucket.client)
        self.context = p.Context(p.digest(p.canonical({"execution": execution_id, "asset": asset.slug, "bucket": publisher.bucket.name})),
            p.digest(p.canonical(configuration)), executor_sha, p.FINALIZATION_VERSION,
            publisher.bucket.name, asset.root, asset.slug)
        self.engine = p.Executor(self.store, validate_semantics=self.validate, preflight_sources=self.preflight, derive=derive)

    @classmethod
    def from_runtime(cls, publisher, asset):
        configuration = {key: value for key, value in os.environ.items() if key.startswith("MPATLAS_") or key == "RUN_DATE"}
        return cls(publisher, asset, execution_id=os.environ.get("CLOUD_RUN_EXECUTION", ""),
                   executor_sha=os.environ.get("SHARED_DATASETS_EXECUTOR_SHA", ""), configuration=configuration)

    def resume(self):
        if self.store.read_json(self.context.receipt_uri) is None:
            if self.store.read_json(self.context.state_uri) is not None:
                state = self.engine._state(self.context).value
                p.require(state["active"] is None, "an incomplete publication requires its original execution")
            return None
        def refuse_prepare():
            raise p.PublicationError("existing execution receipt disappeared")
        return self.result(self.engine.run(self.context, prepare=refuse_prepare, local_sources={}))

    @staticmethod
    def result(receipt):
        results = {key: p.ObjectVersion.parse(value) for key, value in receipt["results"].items()}
        operation = next(op for op in receipt["intent"]["operations"] if op["id"] == "run-record")
        return {**p.strict_json(derive(operation["source"]["parameters"], results)), "run_record": blob_info(results["run-record"])}

    def validate(self, intent):
        value = intent.value
        p.require(value["context"] == asdict(self.context) and value["mode"] == "complete_release" and value["reservation"] is None,
                  "source-field publication cannot change owner or allocate identities")
        operations = {op["id"]: op for op in value["operations"]}
        p.require({"release-manifest", "latest-manifest", "run-record", "release-index"} <= operations.keys(), "publication must capture manifests, run and release index")
        identity = operations["release-manifest"]["source"]["parameters"]["payload"]["identity"]
        model.validate_identity_metadata(identity)
        p.require(identity["strategy"] == "source_field" and identity["source_fields"] == ["zone_id"], "MPAtlas identity must remain source_field:zone_id")
        p.require(all(f"{prefix}-{role}" in operations for prefix in ("release", "latest") for role in ("fgb", "pmtiles", "metadata", "schema")), "incomplete vector bundle")

    def preflight(self, intent):
        for operation in intent.value["operations"]:
            head = self.store.head(operation["destination"])
            p.require((head.generation if head else 0) == operation["expected_generation"], "original publication destination changed")

    def publish(self, *, run_date, outputs, identity, object_metadata, source_inputs, record, baseline):
        existing = self.resume()
        if existing is not None:
            return existing
        adopt_verified_baseline(self.publisher, self.asset, baseline, store=self.store)
        state = self.engine._state(self.context).value
        p.require(state["active"] is None, "another publication owns the asset")
        latest = self.store.read_json(state["current"]["latest_manifest"]["path"])
        p.require(latest is not None and latest.version.identity() == state["current"]["latest_manifest"], "latest manifest changed outside managed publication")
        intent, local = prepare_bundle(context=self.context, store=self.store, asset=self.asset, run_date=run_date,
            outputs=outputs, object_metadata=object_metadata, source_inputs=source_inputs, identity=identity,
            record=record, current=state["current"])
        value = intent.value
        # Stage every input before persisting an intent or touching canonical
        # data. A staging failure is safely repeatable; a captured transaction
        # always has all original input generations available remotely.
        for operation in value["operations"]:
            if operation["phase"] != "checkpoint":
                continue
            source = operation["source"]
            name = operation["destination"].rsplit('/', 1)[-1]
            uri = f"gs://{self.context.bucket}/{self.asset.root}/publications/inputs/{source['sha256']}/{name}"
            head = self.store.head(uri)
            if head is None:
                version = self.store.upload(uri, local[source["key"]], 0,
                    {"publication-sha256": source["sha256"], "asset_slug": self.asset.slug}, operation["content_type"], "no-cache")
            else:
                version = self.store.inspect(uri, head.generation)
            p.require(version is not None and version.sha256 == source["sha256"] and version.size == source["size"], "staged input differs from validated bytes")
            operation["source"] = {"kind": "gcs", **version.identity(), "size": version.size}
        captured = p.Intent.build(value)
        return self.result(self.engine.run(self.context, prepare=lambda: captured, local_sources={}))
