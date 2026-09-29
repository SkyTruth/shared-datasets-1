from __future__ import annotations

import copy
import unittest
from dataclasses import asdict, replace
from unittest import mock

from ingestion.common import identity_reset as reset
from ingestion.common import publication as p
from scripts import release_feature_model as model
from test_publication import LostResponse, MemoryStore, derive, operation


def fixture(slug="wdpa-marine"):
    store = MemoryStore()
    root = reset.ASSET_ROOTS[slug]
    uri = f"gs://bucket/{root}"
    objects = []
    for suffix in reset.BASE_SUFFIXES:
        version = store.write_bytes(f"{uri}/latest/{slug}{suffix}", b"old " + suffix.encode(), 0, {}, "application/octet-stream", "")
        objects.append(version.identity())
    old_release = store.write_bytes(f"{uri}/releases/2026-09-01/{slug}.manifest.json", b"old .manifest.json", 0, {}, "application/json", "")
    manifest = next(item for item in objects if item["path"].endswith(".manifest.json"))
    candidate = reset.IdentityResetCandidate.build({
        "schema_version": 1, "asset_slug": slug, "bucket": "bucket", "contract_id": reset.CONTRACT_ID,
        "first_release": "2026-10-01", "latest_objects": sorted(objects, key=lambda item: item["path"]),
        "baseline": {"release": "2026-09-01", "release_manifest": old_release.identity(), "latest_manifest": manifest},
    })
    ctx = p.Context("a" * 64, "b" * 64, "c" * 40, p.FINALIZATION_VERSION, "bucket", root, slug, reset.CONTRACT_ID)
    return store, candidate, ctx


def install_fixture(store, candidate):
    candidate.validate_current(store)
    for item in candidate.review_envelope()["objects"]:
        store.write_json(item["path"], item["value"], item["expected_generation"])


def intent_for(store, candidate, ctx):
    version = store.write_bytes("gs://bucket/_scratch/pending-publishes/reset/input.fgb", b"new data", 0, {}, "application/octet-stream", "")
    source = {"kind": "gcs", "path": version.path, "generation": version.generation, "sha256": version.sha256, "size": version.size}
    operations = []
    for index, anchor in enumerate(candidate.value["latest_objects"]):
        if anchor["path"].endswith(".manifest.json"):
            continue
        operations.append(operation(f"latest-{index}", "data", anchor["path"], source, anchor["generation"]))
    manifest = {"kind": "derived", "version": p.FINALIZATION_VERSION, "parameters": {"kind": "manifest"}, "dependencies": [op["id"] for op in operations]}
    operations.extend([
        operation("release-manifest", "commit", f"{candidate.root_uri}/releases/2026-10-01/{ctx.asset_slug}.manifest.json", manifest),
        operation("latest-manifest", "commit", candidate.value["baseline"]["latest_manifest"]["path"], {"kind": "result", "operation": "release-manifest"}, candidate.value["baseline"]["latest_manifest"]["generation"]),
    ])
    return p.Intent.build({"schema_version": 1, "context": asdict(ctx), "mode": "complete_release", "release": "2026-10-01",
                           "predecessor": candidate.value["baseline"]["latest_manifest"], "reservation": {"start": 1, "next": 3},
                           "prepared_at": "2026-09-29T12:00:00Z", "operations": operations, "notification": None})


def executor(store):
    # These tests exercise reset ownership. Artifact semantics belong to the
    # producer adapter, not the generic core's opaque-byte protocol.
    return p.Executor(store, validate_semantics=lambda intent: None, preflight_sources=lambda intent: None, derive=derive)


class IdentityResetTests(unittest.TestCase):
    def test_candidate_preserves_old_anchors_but_allocates_from_new_empty_contract(self):
        store, candidate, _ = fixture()
        before = list(store.events)
        candidate.validate_current(store)
        self.assertEqual(store.events, before)
        allocation = model.assign_generated_feature_ids([("new",)], baseline=candidate.allocation_baseline())
        self.assertEqual(allocation.ids_by_key[("new",)], "1")
        self.assertIsNone(candidate.allocation_baseline().snapshot)
        self.assertEqual(candidate.initial_state["current"]["latest_manifest"], candidate.value["baseline"]["latest_manifest"])
        self.assertEqual(candidate.review_envelope()["status"], "prepared_for_review")

    def test_old_history_need_not_be_reconstructed_and_is_never_deleted(self):
        store, candidate, ctx = fixture()
        historical = f"{candidate.root_uri}/releases/2020-01-01/{ctx.asset_slug}.fgb"
        old = store.write_bytes(historical, b"unreconstructable old IDs", 0, {}, "application/octet-stream", "")
        install_fixture(store, candidate)
        intent = intent_for(store, candidate, ctx)
        executor(store).run(ctx, prepare=lambda: intent, local_sources={})
        self.assertEqual(store.inspect(historical), old)
        self.assertEqual(store.read_json(ctx.state_uri).value["reserved_next_feature_id"], 3)

    def test_reset_resume_after_every_durable_write_preserves_reservation(self):
        store, candidate, ctx = fixture()
        install_fixture(store, candidate)
        intent = intent_for(store, candidate, ctx)
        store.events.clear()
        executor(store).run(ctx, prepare=lambda: intent, local_sources={})
        for crash_after in range(1, len(store.events) + 1):
            with self.subTest(crash_after=crash_after):
                store, candidate, ctx = fixture()
                install_fixture(store, candidate)
                intent = intent_for(store, candidate, ctx)
                store.events.clear()
                store.fail_after = crash_after
                with self.assertRaises(LostResponse):
                    executor(store).run(ctx, prepare=lambda: intent, local_sources={})
                store.fail_after = None
                receipt = executor(store).run(ctx, prepare=mock.Mock(side_effect=AssertionError("no replanning")), local_sources={})
                self.assertEqual(receipt["phase"], "derived_complete")
                self.assertEqual(store.read_json(ctx.state_uri).value["reserved_next_feature_id"], 3)
                for op in intent.value["operations"]:
                    self.assertEqual(store.events.count(op["destination"]), 1)

    def test_competing_reset_builder_cannot_publish_same_new_ids(self):
        store, candidate, ctx = fixture()
        install_fixture(store, candidate)
        first = intent_for(store, candidate, ctx)
        other_ctx = replace(ctx, proposal_key="d" * 64)
        other = p.Intent.build({**first.value, "context": asdict(other_ctx)})
        store.events.clear()
        store.fail_after = 2
        with self.assertRaises(LostResponse):
            executor(store).run(ctx, prepare=lambda: first, local_sources={})
        store.fail_after = None
        with self.assertRaisesRegex(p.PublicationError, "owns the asset"):
            executor(store).run(other_ctx, prepare=lambda: other, local_sources={})
        self.assertEqual(store.read_json(ctx.state_uri).value["reserved_next_feature_id"], 3)
        self.assertTrue(all("/publications/" in path for path in store.events))

    def test_current_drift_and_already_installed_state_block_reset(self):
        for change in ("latest", "extra", "release", "state"):
            with self.subTest(change=change):
                store, candidate, ctx = fixture()
                if change == "state":
                    install_fixture(store, candidate)
                elif change == "latest":
                    anchor = candidate.value["latest_objects"][0]
                    store.write_bytes(anchor["path"], b"changed", anchor["generation"], {}, "application/octet-stream", "")
                else:
                    path = f"{candidate.root_uri}/latest/extra.fgb" if change == "extra" else f"{candidate.root_uri}/releases/2026-10-01/{ctx.asset_slug}.fgb"
                    store.write_bytes(path, b"unexpected", 0, {}, "application/octet-stream", "")
                before = list(store.events)
                with self.assertRaises(p.PublicationError):
                    candidate.validate_current(store)
                self.assertEqual(store.events, before)

    def test_reset_cannot_ignore_changed_or_omitted_latest_anchor(self):
        for mutate in ("changed", "omitted"):
            with self.subTest(mutate=mutate):
                store, candidate, ctx = fixture()
                install_fixture(store, candidate)
                intent = intent_for(store, candidate, ctx)
                first = intent.value["operations"][0]
                if mutate == "changed":
                    store.write_bytes(first["destination"], b"changed", first["expected_generation"], {}, "application/octet-stream", "")
                else:
                    value = intent.value
                    value["operations"] = value["operations"][1:]
                    value["operations"][-2]["source"]["dependencies"].remove(first["id"])
                    intent = p.Intent.build(value)
                store.events.clear()
                with self.assertRaises(p.PublicationError):
                    executor(store).run(ctx, prepare=lambda: intent, local_sources={})
                self.assertTrue(all("/publications/receipts/" in path for path in store.events))
                self.assertEqual(store.read_json(ctx.state_uri).value["reserved_next_feature_id"], 1)

    def test_missing_reset_evidence_or_foreign_contract_refuses_before_writes(self):
        for change in ("evidence", "contract", "state"):
            with self.subTest(change=change):
                store, candidate, ctx = fixture()
                install_fixture(store, candidate)
                intent = intent_for(store, candidate, ctx)
                if change == "contract":
                    ctx = replace(ctx, identity_contract="other-contract")
                else:
                    del store.objects[candidate.evidence_uri if change == "evidence" else ctx.state_uri]
                store.events.clear()
                with self.assertRaises(p.PublicationError):
                    executor(store).run(ctx, prepare=lambda: intent, local_sources={})
                self.assertEqual(store.events, [])

    def test_inventory_rejects_incomplete_foreign_or_malformed_boundaries(self):
        _, candidate, _ = fixture()
        mutations = [
            lambda value: value.update(schema_version=True),
            lambda value: value.update(contract_id="retired"),
            lambda value: value.update(first_release="2026-09-01"),
            lambda value: value.update(asset_slug="eamlis-abandoned-mine-land-inventory"),
            lambda value: value["latest_objects"].pop(),
            lambda value: value["latest_objects"][0].update(generation=True),
            lambda value: value["baseline"]["release_manifest"].update(sha256="f" * 64),
        ]
        for mutate in mutations:
            value = copy.deepcopy(candidate.value)
            mutate(value)
            with self.subTest(value=value), self.assertRaises((p.PublicationError, model.ReleaseFeatureModelError)):
                reset.IdentityResetCandidate.build(value)
