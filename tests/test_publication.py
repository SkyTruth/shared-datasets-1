from __future__ import annotations

import json
import os
import tempfile
import unittest
from dataclasses import asdict, replace
from pathlib import Path
from unittest import mock

from ingestion.common import publication as p

ROOT = "100-geographic-reference/110-boundaries/example-asset"
URI = f"gs://bucket/{ROOT}"
SLUG = "example-asset"


def publication_temp_directory():
    root = Path(os.environ.get("SHARED_DATASETS_WORKDIR") or Path(tempfile.gettempdir()) / "shared-datasets-1") / "_scratch"
    root.mkdir(parents=True, exist_ok=True)
    return tempfile.TemporaryDirectory(prefix="test-publication-", dir=root)


class LostResponse(RuntimeError):
    pass


class MemoryStore:
    """Immutable generations, fixed CAS, and post-commit transport failure."""
    def __init__(self):
        self.objects = {}
        self.history = {}
        self.serial = 0
        self.events = []
        self.inspect_calls = []
        self.fail_after = None
        self.before_write = None

    def head(self, uri):
        version = self.objects.get(uri)
        if version is None:
            return None
        return p.ObjectHead(version.path, version.generation, version.size, version.content_type, version.cache_control, version.metadata)

    def list_heads(self, prefix):
        return [self.head(uri) for uri in self.objects if uri.startswith(prefix)]

    def inspect(self, uri, generation=None):
        self.inspect_calls.append((uri, generation))
        if generation is None:
            return self.objects.get(uri)
        entry = self.history.get((uri, generation))
        return entry[0] if entry else None

    def read_json(self, uri):
        version = self.objects.get(uri)
        if version is None:
            return None
        return p.JsonObject(json.loads(self.history[(uri, version.generation)][1]), version)

    def write_json(self, uri, value, expected):
        return self.write_bytes(uri, p.canonical(value), expected, {}, "application/json", "no-cache")

    def write_bytes(self, uri, data, expected, tags, content_type, cache_control):
        if self.before_write:
            callback, self.before_write = self.before_write, None
            callback(uri)
        current = self.objects.get(uri)
        if expected != (current.generation if current else 0):
            raise p.Conflict("generation mismatch")
        self.serial += 1
        version = p.ObjectVersion(uri, self.serial, p.digest(data), len(data), content_type, cache_control, tuple(sorted(tags.items())))
        self.objects[uri] = version
        self.history[(uri, version.generation)] = (version, data)
        self.events.append(uri)
        if self.fail_after == len(self.events):
            raise LostResponse(uri)
        return version

    def upload(self, uri, path, expected, tags, content_type, cache_control):
        data = path.read_bytes()
        p.require(p.digest(data) == tags["publication-sha256"], "changed local bytes")
        return self.write_bytes(uri, data, expected, tags, content_type, cache_control)

    def copy(self, source, uri, expected, tags, content_type, cache_control):
        entry = self.history.get((source.path, source.generation))
        if entry is None:
            raise p.NotRecoverableSource("source is gone")
        p.require(entry[0].sha256 == source.sha256, "source changed")
        return self.write_bytes(uri, entry[1], expected, tags, content_type, cache_control)


def context(key="a"):
    return p.Context(key * 64, "d" * 64, "e" * 40, p.FINALIZATION_VERSION, "bucket", ROOT, SLUG, "test-v1")


def seed(store, *, generated=True, genesis=False):
    baseline = None
    if not genesis:
        release = store.write_bytes(f"{URI}/releases/2026-09-01/{SLUG}.manifest.json", b"old manifest", 0, {}, "application/json", "")
        latest = store.write_bytes(f"{URI}/latest/{SLUG}.manifest.json", b"old manifest", 0, {}, "application/json", "")
        baseline = {"release": "2026-09-01", "release_manifest": release.identity(), "latest_manifest": latest.identity()}
    adoption_uri = f"{URI}/publications/receipts/{'f' * 64}.json"
    record = {"schema_version": 1, "kind": "adoption", "asset_slug": SLUG, "asset_root": ROOT, "bucket": "bucket", "baseline": baseline, "reserved_next_feature_id": (1 if genesis else 42) if generated else None, "evidence_sha256": "b" * 64, "identity_contract": "test-v1" if generated else None, "reset_release": None}
    store.write_json(adoption_uri, record, 0)
    current = None if genesis else {**baseline, "transaction_id": p.digest(p.canonical(record)), "receipt_uri": adoption_uri}
    store.write_json(context().state_uri, {"schema_version": 1, "asset_slug": SLUG, "adoption_receipt": adoption_uri, "reserved_next_feature_id": record["reserved_next_feature_id"], "current": current, "active": None}, 0)
    store.events.clear()


def operation(operation_id, phase, destination, source, expected=0):
    return {"id": operation_id, "phase": phase, "destination": destination, "expected_generation": expected, "source": source,
            "content_type": "application/json" if destination.endswith(".json") else "application/octet-stream", "cache_control": "no-cache", "user_metadata": {"asset_slug": SLUG, "source_version": "fixture", "format": "fixture"}}


def make_intent(store, ctx=None, *, source_path=None, release="2026-09-22", next_id=50, global_output=False):
    ctx = ctx or context()
    state = store.read_json(ctx.state_uri).value
    current = state["current"]
    data = source_path.read_bytes() if source_path else b"dataset"
    source = {"kind": "local", "key": "data", "sha256": p.digest(data), "size": len(data)}
    if source_path is None:
        upstream = "gs://bucket/_scratch/pending-publishes/source/data.fgb"
        if upstream not in store.objects:
            store.write_bytes(upstream, data, 0, {}, "application/octet-stream", "")
        version = store.objects[upstream]
        source = {"kind": "gcs", "path": upstream, "generation": version.generation, "sha256": version.sha256, "size": version.size}
    operations = []
    if source_path:
        operations.append(operation("input", "checkpoint", f"{URI}/publications/inputs/{ctx.proposal_key}/0.fgb", source))
        source = {"kind": "result", "operation": "input"}
    operations.append(operation("data", "data", f"{URI}/releases/{release}/{SLUG}.fgb", source))
    derived = {"kind": "derived", "version": p.FINALIZATION_VERSION, "parameters": {"kind": "manifest", "release": release}, "dependencies": ["data"]}
    operations.extend([
        operation("release-manifest", "commit", f"{URI}/releases/{release}/{SLUG}.manifest.json", derived),
        operation("latest-manifest", "commit", f"{URI}/latest/{SLUG}.manifest.json", {"kind": "result", "operation": "release-manifest"}, current["latest_manifest"]["generation"] if current else 0),
        operation("index", "asset_derived", "gs://bucket/_catalog/releases/example-asset.json", {"kind": "derived", "version": p.FINALIZATION_VERSION, "parameters": {"kind": "index"}, "dependencies": ["release-manifest"]}, store.objects["gs://bucket/_catalog/releases/example-asset.json"].generation if "gs://bucket/_catalog/releases/example-asset.json" in store.objects else 0),
    ])
    if global_output:
        operations.append(operation("catalog", "global_derived", "gs://bucket/_catalog/web/catalog.json", {"kind": "derived", "version": p.FINALIZATION_VERSION, "parameters": {"kind": "catalog"}, "dependencies": ["index"]}))
    return p.Intent.build({"schema_version": 1, "context": asdict(ctx), "mode": "complete_release", "release": release,
                           "predecessor": current["latest_manifest"] if current else None,
                           "reservation": {"start": state["reserved_next_feature_id"], "next": next_id} if state["reserved_next_feature_id"] is not None else None,
                           "prepared_at": "2026-09-22T12:00:00Z", "operations": operations, "notification": None})


def derive(parameters, results):
    return p.canonical({"parameters": parameters, "results": {key: result.record() for key, result in results.items()}})


def executor(store, **kwargs):
    return p.Executor(store, validate_semantics=kwargs.get("validate_semantics", lambda intent: p.require(intent.value["mode"] in p.MODES, "mode")),
                      preflight_sources=kwargs.get("preflight_sources", lambda intent: p.require(bool(intent.value["operations"]), "operations")), derive=derive)


class PublicationTests(unittest.TestCase):
    def test_claim_and_commit_preserve_metadata_and_monotonic_reservation(self):
        store = MemoryStore()
        seed(store)
        intent = make_intent(store)
        store.events.clear()
        result = executor(store).run(context(), prepare=lambda: intent, local_sources={})
        state = store.read_json(context().state_uri).value
        self.assertEqual(result["phase"], "derived_complete")
        self.assertEqual(state["reserved_next_feature_id"], 50)
        self.assertIsNone(state["active"])
        self.assertEqual(state["current"]["release"], "2026-09-22")
        self.assertEqual(store.events[0], context().receipt_uri)
        self.assertEqual(store.events[1], context().state_uri)
        self.assertEqual(result["results"]["data"]["metadata"]["source_version"], "fixture")

    def test_resume_after_every_durable_write_reuses_original_plan(self):
        initial = MemoryStore()
        seed(initial)
        intent = make_intent(initial, global_output=True)
        initial.events.clear()
        executor(initial).run(context(), prepare=lambda: intent, local_sources={})
        transitions = len(initial.events)
        for crash_after in range(1, transitions + 1):
            with self.subTest(crash_after=crash_after):
                store = MemoryStore()
                seed(store)
                intent = make_intent(store, global_output=True)
                store.events.clear()
                store.fail_after = crash_after
                with self.assertRaises(LostResponse):
                    executor(store).run(context(), prepare=lambda: intent, local_sources={})
                store.fail_after = None
                prepare = mock.Mock(side_effect=AssertionError("must not replan"))
                result = executor(store).run(context(), prepare=prepare, local_sources={})
                self.assertEqual(result["phase"], "derived_complete")
                self.assertEqual(result["transaction_id"], intent.transaction_id)
                self.assertIsNone(store.read_json(context().state_uri).value["active"])
                for op in intent.value["operations"]:
                    self.assertEqual(store.events.count(op["destination"]), 1)

    def test_competing_plan_cannot_reuse_baseline_or_reservation(self):
        store = MemoryStore()
        seed(store)
        first = make_intent(store)
        other = make_intent(store, context("b"), release="2026-09-23")
        store.events.clear()
        store.fail_after = 2  # claim persisted, no allocation-bearing data yet
        with self.assertRaises(LostResponse):
            executor(store).run(context(), prepare=lambda: first, local_sources={})
        store.fail_after = None
        before = set(store.objects)
        with self.assertRaisesRegex(p.PublicationError, "owns the asset"):
            executor(store).run(context("b"), prepare=lambda: other, local_sources={})
        self.assertEqual(set(store.objects) - before, {context("b").receipt_uri})
        executor(store).run(context(), prepare=lambda: first, local_sources={})
        with self.assertRaisesRegex(p.PublicationError, "stale identity baseline"):
            executor(store).run(context("b"), prepare=lambda: other, local_sources={})

    def test_local_checkpoint_loss_before_and_after_completion(self):
        for crash_after in (2, 3, 4):
            with self.subTest(crash_after=crash_after), publication_temp_directory() as tmp:
                source = Path(tmp) / "data.fgb"
                source.write_bytes(b"local bytes")
                store = MemoryStore()
                seed(store)
                intent = make_intent(store, source_path=source)
                store.events.clear()
                store.fail_after = crash_after
                with self.assertRaises(LostResponse):
                    executor(store).run(context(), prepare=lambda: intent, local_sources={"data": source})
                source.unlink()
                store.fail_after = None
                if crash_after == 2:
                    with self.assertRaises(p.NotRecoverableSource):
                        executor(store).run(context(), prepare=lambda: intent, local_sources={})
                    state = store.read_json(context().state_uri).value
                    self.assertEqual(state["reserved_next_feature_id"], 50)
                    self.assertIsNotNone(state["active"])
                    self.assertFalse(any("/releases/2026-09-22/" in path for path in store.events))
                else:
                    result = executor(store).run(context(), prepare=lambda: intent, local_sources={})
                    self.assertEqual(result["phase"], "derived_complete")

    def test_terminal_replay_after_newer_transaction_is_read_only(self):
        store = MemoryStore()
        seed(store)
        first = make_intent(store)
        executor(store).run(context(), prepare=lambda: first, local_sources={})
        second = make_intent(store, context("b"), release="2026-09-23", next_id=60)
        executor(store).run(context("b"), prepare=lambda: second, local_sources={})
        before = list(store.events)
        semantic = mock.Mock()
        preflight = mock.Mock(side_effect=AssertionError("sources may be gone"))
        result = executor(store, validate_semantics=semantic, preflight_sources=preflight).run(context(), prepare=mock.Mock(side_effect=AssertionError("no replanning")), local_sources={})
        self.assertEqual(store.events, before)
        self.assertEqual(result["transaction_id"], first.transaction_id)
        semantic.assert_called_once()
        preflight.assert_not_called()

    def test_executor_change_refuses_original_proposal_without_new_receipt(self):
        store = MemoryStore()
        seed(store)
        intent = make_intent(store)
        executor(store).run(context(), prepare=lambda: intent, local_sources={})
        before = list(store.events)
        with self.assertRaisesRegex(p.PublicationError, "executor changed"):
            executor(store).run(replace(context(), trusted_executor_sha="f" * 40), prepare=mock.Mock(side_effect=AssertionError("no replanning")), local_sources={})
        self.assertEqual(store.events, before)

    def test_semantic_and_source_preflight_refuse_before_receipt_or_claim(self):
        for field in ("validate_semantics", "preflight_sources"):
            with self.subTest(field=field):
                store = MemoryStore()
                seed(store)
                intent = make_intent(store)
                before = list(store.events)
                with self.assertRaisesRegex(p.PublicationError, "semantic refusal"):
                    executor(store, **{field: mock.Mock(side_effect=p.PublicationError("semantic refusal"))}).run(context(), prepare=lambda: intent, local_sources={})
                self.assertEqual(store.events, before)

    def test_stale_generated_range_and_backfill_are_refused(self):
        for mutate in (lambda value: value["reservation"].update(start=41), lambda value: value.update(release="2026-08-01")):
            with self.subTest(mutate=mutate):
                store = MemoryStore()
                seed(store)
                original = make_intent(store)
                value = original.value
                mutate(value)
                if value["release"] == "2026-08-01":
                    value["operations"] = [dict(op, destination=op["destination"].replace("2026-09-22", "2026-08-01")) for op in value["operations"]]
                intent = p.Intent.build(value)
                with self.assertRaises(p.PublicationError):
                    executor(store).run(context(), prepare=lambda: intent, local_sources={})
                self.assertIsNone(store.read_json(context().state_uri).value["active"])

    def test_equal_unowned_destination_is_not_recovery(self):
        store = MemoryStore()
        seed(store)
        intent = make_intent(store)
        destination = intent.value["operations"][0]["destination"]
        store.write_bytes(destination, b"dataset", 0, {}, "application/octet-stream", "no-cache")
        store.inspect_calls.clear()
        with self.assertRaises(p.Conflict):
            executor(store).run(context(), prepare=lambda: intent, local_sources={})
        self.assertFalse(any(path == destination for path, _ in store.inspect_calls))

    def test_malformed_receipts_fail_before_any_resume_mutation(self):
        mutations = [
            lambda r: r["results"]["data"].update(content_type="wrong"),
            lambda r: r["results"].pop("data"),
            lambda r: r.update(phase="prepared"),
            lambda r: r["intent"]["operations"][0].update(destination=f"gs://bucket/{ROOT}/../foreign.fgb"),
            lambda r: r["intent"]["reservation"].update(next=True),
        ]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                store = MemoryStore()
                seed(store)
                intent = make_intent(store)
                executor(store).run(context(), prepare=lambda: intent, local_sources={})
                receipt = store.read_json(context().receipt_uri)
                mutate(receipt.value)
                store.write_json(context().receipt_uri, receipt.value, receipt.version.generation)
                before = list(store.events)
                with self.assertRaises((p.PublicationError, ValueError)):
                    executor(store).run(context(), prepare=lambda: intent, local_sources={})
                self.assertEqual(store.events, before)

    def test_catalog_cannot_smuggle_protected_or_foreign_asset_targets(self):
        store = MemoryStore()
        seed(store)
        for destination in ("gs://bucket/_catalog/releases/example-asset.json", "gs://bucket/_catalog/schema-snapshots/example-asset.json", f"{URI}/latest/{SLUG}.fgb"):
            with self.subTest(destination=destination):
                ctx = replace(context(), asset_root=None, asset_slug=None, identity_contract=None)
                value = make_intent(store).value
                value.update(context=asdict(ctx), mode="catalog_update", release=None, predecessor=None, reservation=None)
                value["operations"] = [operation("catalog", "global_derived", destination, {"kind": "derived", "version": p.FINALIZATION_VERSION, "parameters": {}, "dependencies": []})]
                with self.assertRaises(p.PublicationError):
                    p.Intent.build(value)

    def test_metadata_and_repair_on_generated_assets_preserve_highwater_and_activation(self):
        for mode in ("metadata_update", "object_repair"):
            with self.subTest(mode=mode):
                store = MemoryStore()
                seed(store)
                baseline = store.read_json(context().state_uri).value
                value = make_intent(store).value
                value.update(mode=mode, release=baseline["current"]["release"], reservation=None)
                source = value["operations"][0]["source"]
                target = f"{URI}/latest/{SLUG}.metadata.fr.ndjson.gz" if mode == "metadata_update" else f"{URI}/README.md"
                phase = "data" if mode == "metadata_update" else "asset_derived"
                value["operations"] = [operation("metadata", phase, target, source)]
                intent = p.Intent.build(value)
                executor(store).run(context(), prepare=lambda: intent, local_sources={})
                state = store.read_json(context().state_uri).value
                self.assertEqual(state["reserved_next_feature_id"], 42)
                self.assertEqual(state["current"], baseline["current"])
                self.assertIsNone(state["active"])

    def test_genesis_claim_rechecks_existing_allocation_objects(self):
        for existing in (False, True):
            with self.subTest(existing=existing):
                store = MemoryStore()
                seed(store, genesis=True)
                intent = make_intent(store, next_id=9)
                if existing:
                    store.write_bytes(f"{URI}/releases/2020-01-01/{SLUG}.fgb", b"unaccounted allocations", 0, {}, "application/octet-stream", "")
                    with self.assertRaisesRegex(p.PublicationError, "genesis"):
                        executor(store).run(context(), prepare=lambda: intent, local_sources={})
                    self.assertEqual(store.read_json(context().state_uri).value["reserved_next_feature_id"], 9)
                else:
                    executor(store).run(context(), prepare=lambda: intent, local_sources={})
                    self.assertIsNone(store.read_json(context().state_uri).value["active"])

    def test_state_cannot_claim_foreign_missing_or_uncommitted_authority(self):
        for bad in ("foreign-adoption", "missing-adoption", "foreign-release", "missing-current", "partial-current", "foreign-active"):
            with self.subTest(bad=bad):
                store = MemoryStore()
                seed(store)
                first = make_intent(store)
                executor(store).run(context(), prepare=lambda: first, local_sources={})
                next_intent = make_intent(store, context("b"), release="2026-09-23", next_id=60)
                state = store.read_json(context().state_uri)
                if bad == "foreign-adoption":
                    state.value["adoption_receipt"] = "gs://bucket/other/asset/root/publications/receipts/" + "c" * 64 + ".json"
                elif bad == "missing-adoption":
                    del store.objects[state.value["adoption_receipt"]]
                elif bad == "foreign-release":
                    state.value["current"]["release_manifest"]["path"] = "gs://other/foreign.manifest.json"
                elif bad == "missing-current":
                    del store.objects[context().receipt_uri]
                elif bad == "partial-current":
                    receipt = store.read_json(context().receipt_uri)
                    store.write_json(context().receipt_uri, p.receipt_value(first, {}), receipt.version.generation)
                else:
                    state.value["active"] = {"transaction_id": "c" * 64, "receipt_uri": "gs://bucket/foreign/asset/root/publications/receipts/" + "c" * 64 + ".json"}
                store.write_json(context().state_uri, state.value, state.version.generation)
                before = list(store.events)
                with self.assertRaises(p.PublicationError):
                    executor(store).run(context("b"), prepare=lambda: next_intent, local_sources={})
                self.assertTrue(all(path == context("b").receipt_uri for path in store.events[len(before):]))

    def test_delayed_old_index_write_cannot_overwrite_newer_transaction(self):
        store = MemoryStore()
        seed(store)
        first = make_intent(store)
        target = "gs://bucket/_catalog/releases/example-asset.json"

        def pause_before_index(uri):
            if uri != target:
                store.before_write = pause_before_index
                return
            executor(store).run(context(), prepare=lambda: first, local_sources={})
            second = make_intent(store, context("b"), release="2026-09-23", next_id=60)
            executor(store).run(context("b"), prepare=lambda: second, local_sources={})

        store.before_write = pause_before_index
        with self.assertRaises(p.Conflict):
            executor(store).run(context(), prepare=lambda: first, local_sources={})
        self.assertEqual(store.read_json(context().state_uri).value["current"]["release"], "2026-09-23")
        self.assertEqual(dict(store.objects[target].metadata)["publication-transaction"], store.read_json(context("b").receipt_uri).value["transaction_id"])

    def test_missing_source_preflight_cannot_claim_and_unowned_hash_is_not_downloaded(self):
        store = MemoryStore()
        seed(store)
        intent = make_intent(store)
        source = intent.value["operations"][0]["source"]
        del store.history[(source["path"], source["generation"])]
        before = list(store.events)
        with self.assertRaisesRegex(p.PublicationError, "preflight mismatch"):
            executor(store).run(context(), prepare=lambda: intent, local_sources={})
        self.assertEqual(store.events, before)

    def test_notification_retry_is_independent_and_uncertain_send_is_not_repeated(self):
        store = MemoryStore()
        seed(store)
        value = make_intent(store).value
        value["notification"] = {"text": "Published example-asset"}
        intent = p.Intent.build(value)
        engine = executor(store)
        engine.run(context(), prepare=lambda: intent, local_sources={})
        before = list(store.events)
        self.assertEqual(engine.begin_notification(context(), attempt_id="run-1"), value["notification"])
        with self.assertRaises(p.PublicationError):
            engine.begin_notification(context(), attempt_id="run-2")
        engine.finish_notification(context(), attempt_id="run-1", outcome="unknown")
        with self.assertRaises(p.PublicationError):
            engine.begin_notification(context(), attempt_id="run-2", retry_failed=True)
        engine.run(context(), prepare=mock.Mock(side_effect=AssertionError("do not replan")), local_sources={})
        self.assertEqual(store.read_json(context().receipt_uri).value["notification"]["state"], "unknown")
        self.assertTrue(all(path == context().receipt_uri for path in store.events[len(before):]))

    def test_known_failed_notification_can_retry_explicitly_without_data_replay(self):
        store = MemoryStore()
        seed(store)
        value = make_intent(store).value
        value["notification"] = {"text": "Published example-asset"}
        intent = p.Intent.build(value)
        engine = executor(store)
        engine.run(context(), prepare=lambda: intent, local_sources={})
        engine.begin_notification(context(), attempt_id="first")
        engine.finish_notification(context(), attempt_id="first", outcome="failed", delivery_reference="rejected before delivery")
        before = list(store.events)
        for attempt, explicit in (("second", False), ("first", True)):
            with self.subTest(attempt=attempt, explicit=explicit), self.assertRaises(p.PublicationError):
                engine.begin_notification(context(), attempt_id=attempt, retry_failed=explicit)
        engine.begin_notification(context(), attempt_id="second", retry_failed=True)
        with self.assertRaises(p.PublicationError):
            engine.finish_notification(context(), attempt_id="first", outcome="sent", delivery_reference="stale callback")
        engine.finish_notification(context(), attempt_id="second", outcome="sent", delivery_reference="message-123")
        self.assertEqual(store.read_json(context().receipt_uri).value["notification"]["state"], "sent")
        self.assertTrue(all(path == context().receipt_uri for path in store.events[len(before):]))

    def test_resume_semantic_validation_is_mandatory_even_when_sources_are_gone(self):
        store = MemoryStore()
        seed(store)
        intent = make_intent(store)
        executor(store).run(context(), prepare=lambda: intent, local_sources={})
        before = list(store.events)
        with self.assertRaisesRegex(p.PublicationError, "authorization mismatch"):
            executor(store, validate_semantics=mock.Mock(side_effect=p.PublicationError("authorization mismatch"))).run(context(), prepare=lambda: intent, local_sources={})
        self.assertEqual(store.events, before)

    def test_committed_highwater_regression_refuses_before_any_new_write(self):
        store = MemoryStore()
        seed(store)
        first = make_intent(store)
        executor(store).run(context(), prepare=lambda: first, local_sources={})
        second = make_intent(store, context("b"), release="2026-09-23", next_id=60)
        state = store.read_json(context().state_uri)
        state.value["reserved_next_feature_id"] = 42
        store.write_json(context().state_uri, state.value, state.version.generation)
        before = list(store.events)
        prepare = mock.Mock(return_value=second)
        with self.assertRaisesRegex(p.PublicationError, "below committed allocation"):
            executor(store).run(context("b"), prepare=prepare, local_sources={})
        self.assertEqual(store.events, before)
        prepare.assert_not_called()

    def test_shared_E_fixture_preserves_proposal_and_executor_contract_identity(self):
        fixture = json.loads((Path(__file__).parent / "fixtures" / "dataset-mutation-authorization-v1.json").read_text())
        contexts = {}
        for label, envelope in fixture.items():
            self.assertEqual(envelope["outcome"], "mutation")
            contexts[label] = p.Context(
                envelope["proposal_key"], envelope["execution_contract_sha256"], envelope["trusted_executor_sha"],
                envelope["document"]["finalization_version"], "skytruth-shared-datasets-1", "100-geographic-reference/110-boundaries/demo", "demo",
            )
        self.assertEqual(contexts["original"], contexts["later_attempt"])
        self.assertEqual(contexts["original"].receipt_uri, contexts["incompatible_executor"].receipt_uri)
        self.assertNotEqual(contexts["original"], contexts["incompatible_executor"])
        self.assertNotEqual(fixture["original"]["source_run"], fixture["later_attempt"]["source_run"])

    def test_first_checkpoint_cannot_enable_data_when_second_input_vanishes(self):
        with publication_temp_directory() as temporary:
            source = Path(temporary) / "first.fgb"
            second = Path(temporary) / "second.csv"
            source.write_bytes(b"first")
            second.write_bytes(b"second")
            store = MemoryStore()
            seed(store)
            value = make_intent(store, source_path=source).value
            second_op = operation("second-input", "checkpoint", f"{URI}/publications/inputs/{context().proposal_key}/1.csv", {"kind": "local", "key": "second", "sha256": p.digest(b"second"), "size": 6})
            value["operations"].insert(1, second_op)
            intent = p.Intent.build(value)
            store.events.clear()
            store.fail_after = 4  # first checkpoint and its result are durable
            with self.assertRaises(LostResponse):
                executor(store).run(context(), prepare=lambda: intent, local_sources={"data": source, "second": second})
            store.fail_after = None
            second.unlink()
            with self.assertRaises(p.NotRecoverableSource):
                executor(store).run(context(), prepare=lambda: intent, local_sources={"data": source})
            self.assertFalse(any("/releases/2026-09-22/" in path for path in store.events))
            self.assertEqual(store.read_json(context().state_uri).value["reserved_next_feature_id"], 50)

    def test_protocol_does_not_support_deletes_or_unknown_versions(self):
        store = MemoryStore()
        seed(store)
        for change in (lambda value: value.update(schema_version=True), lambda value: value["operations"][0].update(action="delete")):
            value = make_intent(store).value
            change(value)
            with self.assertRaises(p.PublicationError):
                p.Intent.build(value)


if __name__ == "__main__":
    unittest.main()
