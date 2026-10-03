from __future__ import annotations

import datetime as dt
import gzip
import hashlib
import json
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from ingestion.common import publication as p
from ingestion.common.identity_reset import CONTRACT_ID, IdentityResetCandidate
from ingestion.common.owned_publication import OwnedGeneratedPublisher
from ingestion.sea_ice_daily import run as sea_ice
from ingestion.wdpa_monthly import run as wdpa
from test_identity_reset import fixture, install_fixture
from test_publication import LostResponse, publication_temp_directory
from test_sea_ice_daily import fake_asset_outputs as sea_outputs
from test_wdpa_monthly import fake_asset_outputs as wdpa_outputs


DATE = dt.date(2026, 10, 1)


def publisher_fixture(asset, *, execution="execution-1", store=None):
    if store is None:
        store, candidate, _ctx = fixture(asset.slug)
        install_fixture(store, candidate)
    client = SimpleNamespace(bucket=lambda name: SimpleNamespace(name=name))
    publisher = OwnedGeneratedPublisher(client, "bucket", execution_id=execution, executor_sha="c" * 40, configuration={}, store=store)
    return publisher, store


def outputs_fixture(directory, asset, date=DATE):
    if asset.slug.startswith("wdpa-"):
        outputs = wdpa_outputs(directory, asset=asset, release=date.isoformat())
        properties = {"SITE_PID": "test"}
    else:
        outputs = sea_outputs(directory, release=date.isoformat())
        properties = {"ice_date": date.isoformat()}
    rows = [dict(schema_version=2, asset_slug=asset.slug, release=date.isoformat(), feature_id=str(index),
                 geometry_hash="sha256:" + str(index) * 64, properties_hash="sha256:" + "b" * 64,
                 identity_key=[str(index)], properties=properties, provenance={}) for index in (1, 2)]
    data = gzip.compress(b"".join(json.dumps(row).encode() + b"\n" for row in rows))
    outputs.metadata.write_bytes(data)
    if asset.slug.startswith("wdpa-"):
        for path in outputs.localized_metadata.values():
            path.write_bytes(data)
    outputs.schema.write_text(json.dumps(outputs.schema_payload))
    hashes = {key: hashlib.sha256(getattr(outputs, key).read_bytes()).hexdigest() for key in ("fgb", "pmtiles", "metadata", "schema")}
    if asset.slug.startswith("wdpa-"):
        hashes.update({f"metadata_{locale}": hashlib.sha256(data).hexdigest() for locale in outputs.localized_metadata})
        hashes["metadata_translations"] = hashlib.sha256(outputs.metadata_translations.read_bytes()).hexdigest()
    return replace(outputs, sha256=hashes, next_generated_feature_id=3, identity_contract=CONTRACT_ID)


def publish(publisher, asset, outputs, date=DATE):
    if asset.slug.startswith("wdpa-"):
        return wdpa.publish_asset(publisher=publisher, asset=asset, outputs=outputs, run_date=date,
                                  source_url="https://example.test/source.zip", source_version="Oct2026", source_fields=())
    source_date = date - dt.timedelta(days=1)
    readme = outputs.fgb.parent / "asset_README.md"
    readme.write_text("# IMS Sea-Ice Extent\nReviewed new-contract documentation.\n")
    return sea_ice.publish_outputs(publisher=publisher, asset=asset, outputs=outputs, asset_readme=readme,
                                   source=sea_ice.AvailableSource(filename_date=source_date, source_url="https://example.test/ims.tif.gz", source_filename=sea_ice.ims_filename_for_day(source_date)))


def translation_plan(store, asset):
    prefix = f"gs://bucket/{asset.root}/releases/{DATE.isoformat()}/{asset.slug}"
    manifest = store.read_json(prefix + ".manifest.json").value
    plan = {"asset_slug": asset.slug, "promotions": []}
    for suffix in (".metadata-translations.csv", *(f".metadata.{locale}.ndjson.gz" for locale in wdpa.translations.LOCALES), ".manifest.json"):
        uri = prefix + suffix
        if suffix == ".manifest.json":
            data = p.canonical(manifest)
        else:
            data = ("reviewed " + suffix).encode()
            artifact = next(entry for entry in manifest["artifacts"] if entry["path"] == uri)
            artifact.update(sha256=p.digest(data), size=len(data))
            artifact.pop("generation")
            artifact.pop("latest_generation")
        source = store.write_bytes(f"gs://bucket/_scratch/pending-publishes/edit/{asset.slug}{suffix}", data, 0, {}, "application/octet-stream", "")
        for target in (uri, uri.replace(f"/releases/{DATE.isoformat()}/", "/latest/")):
            plan["promotions"].append({"source_uri": source.path, "source_generation": str(source.generation),
                "destination_uri": target, "destination_generation": str(store.head(target).generation)})
    return plan


class OwnedPublicationTests(unittest.TestCase):
    def setUp(self):
        native = mock.patch("ingestion.common.owned_publication.vector_asset.validate_metadata_lookup_bundle", return_value=SimpleNamespace(valid=True, errors=()))
        self.native = native.start()
        self.addCleanup(native.stop)

    def test_first_release_commits_partial_translations_without_weakening_identity_ownership(self):
        asset = wdpa.ASSETS[0]
        with publication_temp_directory() as temporary:
            publisher, store = publisher_fixture(asset)
            approved = publisher.reset_translation_supplement(asset)
            self.assertIn("/_scratch/pending-publishes/", approved.path)
            outputs = outputs_fixture(Path(temporary), asset)
            incomplete = replace(outputs, localization_report={**outputs.localization_report, "requested_rows_complete": False,
                "translations": {"schema_version": 1, "locales": {
                    locale: {**counts, "current": 1, "missing": 1, "coverage": 0.5, "review_states": {"human_reviewed": 1}}
                    for locale, counts in outputs.localization_report["translations"]["locales"].items()}}})
            record = publish(publisher, asset, incomplete)
            self.assertEqual(record["status"], "success")
            state = store.read_json(publisher.context(asset).state_uri).value
            self.assertEqual(state["reserved_next_feature_id"], 3)
            self.assertIsNone(state["active"])
            manifest = store.read_json(f"gs://bucket/{asset.release_object(DATE, '.manifest.json')}").value
            self.assertTrue(all(counts["missing"] == 1 for counts in manifest["translations"]["locales"].values()))
            with self.assertRaisesRegex(p.PublicationError, "only available before"):
                publisher.reset_translation_supplement(asset)

    def test_real_job_publishers_commit_complete_bundle_run_and_index_under_one_owner(self):
        for asset in (*wdpa.ASSETS, sea_ice.ASSET):
            with self.subTest(asset=asset.slug), publication_temp_directory() as temporary:
                publisher, store = publisher_fixture(asset)
                outputs = outputs_fixture(Path(temporary), asset)
                store.events.clear()
                record = publish(publisher, asset, outputs)
                ctx = publisher.context(asset)
                state = store.read_json(ctx.state_uri).value
                self.assertEqual(record["status"], "success")
                self.assertEqual(record["identity_contract"], CONTRACT_ID)
                self.assertEqual(state["reserved_next_feature_id"], 3)
                self.assertIsNone(state["active"])
                self.assertEqual(store.events[1], ctx.state_uri)  # reserve before even checkpoint inputs
                manifest = store.read_json(f"gs://bucket/{asset.release_object(DATE, '.manifest.json')}").value
                self.assertEqual(manifest["identity"]["contract_id"], CONTRACT_ID)
                self.assertEqual(manifest["identity"]["next_generated_feature_id_after_release"], 3)
                for artifact in manifest["artifacts"]:
                    if artifact["role"] == "manifest":
                        self.assertNotIn("generation", artifact)
                    else:
                        self.assertEqual(store.head(artifact["path"]).generation, artifact["generation"])
                        self.assertEqual(store.head(artifact["latest_path"]).generation, artifact["latest_generation"])
                index = store.read_json(f"gs://bucket/_catalog/releases/{asset.slug}.json").value
                self.assertEqual(index["latest_release"]["date"], DATE.isoformat())
                run = store.read_json(record["run_record"]["path"])
                if asset.slug == sea_ice.ASSET.slug:
                    readme_uri = f"gs://bucket/{asset.root}/README.md"
                    version = store.inspect(readme_uri)
                    self.assertEqual(version.sha256, p.digest((outputs.fgb.parent / "asset_README.md").read_bytes()))
                self.assertEqual(run.value["sha256"]["manifest"], store.inspect(f"gs://bucket/{asset.release_object(DATE, '.manifest.json')}").sha256)
                if asset.slug.startswith("wdpa-"):
                    self.assertEqual(len(record["release_paths"]), 12)
                    self.assertEqual(record["localization"]["translation_locales"], list(wdpa.translations.LOCALES))
                    self.assertEqual(manifest["translations"], outputs.localization_report["translations"])
                    self.assertEqual({entry["path"] for entry in manifest["artifacts"]}, {entry["path"] for entry in record["release_paths"]})

    def test_old_captured_manifest_derivation_remains_byte_stable(self):
        from ingestion.common.owned_publication import derive

        asset = wdpa.ASSETS[0]
        with publication_temp_directory() as temporary:
            publisher, store = publisher_fixture(asset)
            publish(publisher, asset, outputs_fixture(Path(temporary), asset))
            receipt = store.read_json(publisher.context(asset).receipt_uri).value
            operation = next(op for op in receipt["intent"]["operations"] if op["id"] == "release-manifest")
            parameters = operation["source"]["parameters"]
            results = {key: p.ObjectVersion.parse(value) for key, value in receipt["results"].items()}
            new_manifest = p.strict_json(derive(parameters, results))
            parameters = {"kind": "manifest", "payload": {key: value for key, value in parameters["payload"].items() if key != "translations"}}
            # Old receipts already contain extra locale results, but their v1
            # manifest omitted them. Resuming must keep that exact old shape.
            expected = {key: value for key, value in new_manifest.items() if key != "translations"}
            expected["artifacts"] = [entry for entry in expected["artifacts"] if not entry["role"].startswith("extra-")]
            self.assertEqual(derive(parameters, results), p.canonical(expected))

    def test_actual_publishers_resume_after_every_durable_write_without_rebuilding(self):
        for asset in (wdpa.ASSETS[0], sea_ice.ASSET):
            with publication_temp_directory() as temporary:
                publisher, store = publisher_fixture(asset)
                outputs = outputs_fixture(Path(temporary), asset)
                store.events.clear()
                publish(publisher, asset, outputs)
                writes = len(store.events)
                for crash_after in range(1, writes + 1):
                    with self.subTest(asset=asset.slug, crash_after=crash_after):
                        publisher, store = publisher_fixture(asset)
                        store.events.clear()
                        store.fail_after = crash_after
                        with self.assertRaises(LostResponse):
                            publish(publisher, asset, outputs)
                        store.fail_after = None
                        # Before every local checkpoint is durable, exact local
                        # bytes remain necessary. Later resumes need no source.
                        receipt = store.read_json(publisher.context(asset).receipt_uri)
                        pending_local = any(op["source"]["kind"] == "local" and op["id"] not in receipt.value["results"] and store.head(op["destination"]) is None for op in receipt.value["intent"]["operations"])
                        if pending_local:
                            with self.assertRaises(p.NotRecoverableSource):
                                publisher.resume(asset)
                            self.assertEqual(store.read_json(publisher.context(asset).state_uri).value["reserved_next_feature_id"], 3)
                        else:
                            self.assertEqual(publisher.resume(asset)["status"], "success")
                            self.assertIsNone(store.read_json(publisher.context(asset).state_uri).value["active"])

    def test_two_actual_publishers_cannot_publish_the_same_reset_range(self):
        asset = sea_ice.ASSET
        with publication_temp_directory() as temporary:
            first, store = publisher_fixture(asset)
            second, _ = publisher_fixture(asset, store=store, execution="execution-2")
            outputs = outputs_fixture(Path(temporary), asset)
            store.events.clear()
            store.fail_after = 2
            with self.assertRaises(LostResponse):
                publish(first, asset, outputs)
            store.fail_after = None
            before = list(store.events)
            with self.assertRaisesRegex(p.PublicationError, "another publication"):
                publish(second, asset, outputs)
            self.assertEqual(store.events, before)

    def test_changed_execution_configuration_cannot_resume_receipt(self):
        asset = sea_ice.ASSET
        with publication_temp_directory() as temporary:
            publisher, store = publisher_fixture(asset)
            publish(publisher, asset, outputs_fixture(Path(temporary), asset))
            publisher.configuration_digest = "f" * 64
            before = list(store.events)
            with self.assertRaisesRegex(p.PublicationError, "authorization/executor changed"):
                publisher.resume(asset)
            self.assertEqual(store.events, before)

    def test_corrupt_native_or_metadata_bytes_fail_before_any_write(self):
        for bad in ("native", "metadata", "hash", "contract"):
            with self.subTest(bad=bad), publication_temp_directory() as temporary:
                publisher, store = publisher_fixture(sea_ice.ASSET)
                outputs = outputs_fixture(Path(temporary), sea_ice.ASSET)
                self.native.return_value = SimpleNamespace(valid=bad != "native", errors=("bad PMTiles",) if bad == "native" else ())
                if bad == "metadata":
                    outputs.metadata.write_bytes(b"invalid gzip")
                elif bad == "hash":
                    outputs.fgb.write_bytes(b"different")
                elif bad == "contract":
                    outputs = replace(outputs, identity_contract="retired")
                before = list(store.events)
                with self.assertRaises((p.PublicationError, ValueError, OSError)):
                    publish(publisher, sea_ice.ASSET, outputs)
                self.assertEqual(store.events, before)

    def test_skip_preserves_counter_current_release_and_existing_history(self):
        asset = sea_ice.ASSET
        with publication_temp_directory() as temporary:
            publisher, store = publisher_fixture(asset)
            publish(publisher, asset, outputs_fixture(Path(temporary), asset))
            next_publisher, _ = publisher_fixture(asset, execution="next-job", store=store)
            before = store.read_json(publisher.context(asset).state_uri).value
            next_publisher.update_latest_run_index(asset=asset, payload={"schema_version": 1, "asset_slug": asset.slug, "status": "skipped", "release_date": "2026-10-02", "reason": "unchanged"})
            after = store.read_json(publisher.context(asset).state_uri).value
            self.assertEqual(after["reserved_next_feature_id"], before["reserved_next_feature_id"])
            self.assertEqual(after["current"], before["current"])
            self.assertIsNone(after["active"])
            index = store.read_json(f"gs://bucket/_catalog/releases/{asset.slug}.json").value
            self.assertEqual(index["latest_release"]["date"], DATE.isoformat())
            self.assertEqual(index["latest_run"]["date"], "2026-10-02")

    def test_legacy_success_never_skips_first_new_contract_release(self):
        publisher, store = publisher_fixture(sea_ice.ASSET)
        store.write_json(f"gs://bucket/{sea_ice.ASSET.run_record_object(DATE)}", {"status": "success"}, 0)
        self.assertIsNone(publisher.load_successful_run_record(sea_ice.ASSET, DATE))
        self.assertEqual(publisher.load_generated_identity_baseline(sea_ice.ASSET, contract_id=CONTRACT_ID).next_feature_id, 1)

    def test_reset_rebuilds_every_legacy_locale_alias_under_one_owner(self):
        for asset in wdpa.ASSETS:
            with self.subTest(asset=asset.slug), publication_temp_directory() as temporary:
                store, original, _ctx = fixture(asset.slug)
                inventory = original.value
                old_aliases = []
                for locale in ("fr", "id", "pt", "pt_br", "sw"):
                    path = f"{original.root_uri}/latest/{asset.slug}.metadata.{locale}.ndjson.gz"
                    old = store.write_bytes(path, b"old translated IDs", 0, {}, "application/x-ndjson", "")
                    old_aliases.append(old)
                    inventory["latest_objects"].append(old.identity())
                inventory["latest_objects"].sort(key=lambda item: item["path"])
                install_fixture(store, IdentityResetCandidate.build(inventory))
                publisher, _ = publisher_fixture(asset, store=store)
                store.events.clear()
                publish(publisher, asset, outputs_fixture(Path(temporary), asset))
                state = store.read_json(publisher.context(asset).state_uri).value
                self.assertEqual(state["reserved_next_feature_id"], 3)
                self.assertIsNone(state["active"])
                for old in old_aliases:
                    self.assertNotEqual(store.inspect(old.path).generation, old.generation)
                    self.assertEqual(store.inspect(old.path, old.generation), old)

    def test_missing_supported_locale_refuses_before_any_publication_write(self):
        asset = wdpa.ASSETS[0]
        with publication_temp_directory() as temporary:
            publisher, store = publisher_fixture(asset)
            outputs = outputs_fixture(Path(temporary), asset)
            outputs.localized_metadata.pop("fr")
            store.events.clear()
            with self.assertRaisesRegex(RuntimeError, "every supported locale"):
                publish(publisher, asset, outputs)
            self.assertEqual(store.events, [])

    def test_translation_inputs_use_committed_release_generations(self):
        asset = wdpa.ASSETS[0]
        with publication_temp_directory() as temporary:
            publisher, store = publisher_fixture(asset)
            self.assertIsNone(publisher.committed_artifacts(asset, suffixes=wdpa.translations.SUFFIXES))
            publish(publisher, asset, outputs_fixture(Path(temporary), asset))
            versions = publisher.committed_artifacts(asset, suffixes=wdpa.translations.SUFFIXES)
            for suffix, version in versions.items():
                self.assertEqual(version.identity(), store.inspect(f"gs://bucket/{asset.release_object(DATE, suffix)}").identity())

    def test_translation_edit_preserves_identity_and_becomes_next_build_input(self):
        asset = wdpa.ASSETS[0]
        with publication_temp_directory() as temporary:
            publisher, store = publisher_fixture(asset)
            publish(publisher, asset, outputs_fixture(Path(temporary), asset))
            before = store.read_json(publisher.context(asset).state_uri).value
            base = store.inspect(f"gs://bucket/{asset.release_object(DATE, '.metadata.ndjson.gz')}")
            plan = translation_plan(store, asset)
            editor, _ = publisher_fixture(asset, execution="reviewed-edit", store=store)
            record = editor.publish_translation_update(asset=asset, plan=plan)
            after = store.read_json(editor.context(asset).state_uri).value
            self.assertEqual(after["reserved_next_feature_id"], before["reserved_next_feature_id"])
            self.assertEqual(after["current"]["release"], before["current"]["release"])
            self.assertNotEqual(after["current"]["receipt_uri"], before["current"]["receipt_uri"])
            self.assertEqual(store.inspect(base.path), base)
            self.assertIsNone(after["active"])
            self.assertEqual(editor.load_successful_run_record(asset, DATE)[0], record)
            versions = editor.committed_artifacts(asset, suffixes=wdpa.translations.SUFFIXES)
            csv = versions[".metadata-translations.csv"]
            self.assertEqual(csv.identity(), store.inspect(csv.path).identity())
            self.assertEqual(csv.sha256, p.digest(b"reviewed .metadata-translations.csv"))
            self.assertEqual(len(record["release_paths"]), 12)
            before_retry = list(store.events)
            self.assertEqual(editor.publish_translation_update(asset=asset, plan=plan), record)
            self.assertEqual(store.events, before_retry)

    def test_translation_edit_resumes_after_every_durable_write(self):
        asset = wdpa.ASSETS[0]
        with publication_temp_directory() as temporary:
            outputs = outputs_fixture(Path(temporary), asset)
            def setup():
                publisher, store = publisher_fixture(asset)
                publish(publisher, asset, outputs)
                plan = translation_plan(store, asset)
                editor, _ = publisher_fixture(asset, execution="reviewed-edit", store=store)
                store.events.clear()
                return editor, store, plan
            editor, store, plan = setup()
            editor.publish_translation_update(asset=asset, plan=plan)
            for position in range(1, len(store.events) + 1):
                with self.subTest(position=position):
                    editor, store, plan = setup()
                    store.fail_after = position
                    with self.assertRaises(LostResponse):
                        editor.publish_translation_update(asset=asset, plan=plan)
                    store.fail_after = None
                    self.assertEqual(editor.resume(asset)["status"], "success")
                    state = store.read_json(editor.context(asset).state_uri).value
                    self.assertIsNone(state["active"])
                    self.assertEqual(state["reserved_next_feature_id"], 3)

    def test_translation_edit_rejects_incomplete_or_changed_bundle_before_claim(self):
        asset = wdpa.ASSETS[0]
        for bad in ("missing-locale", "old-generation", "changed-source", "identity", "wrong-release"):
            with self.subTest(bad=bad), publication_temp_directory() as temporary:
                publisher, store = publisher_fixture(asset)
                publish(publisher, asset, outputs_fixture(Path(temporary), asset))
                plan = translation_plan(store, asset)
                if bad == "missing-locale":
                    plan["promotions"].pop(0)
                elif bad == "old-generation":
                    plan["promotions"][-1]["destination_generation"] = "1"
                elif bad == "wrong-release":
                    for item in plan["promotions"]:
                        item["destination_uri"] = item["destination_uri"].replace(DATE.isoformat(), "2026-09-01")
                else:
                    item = plan["promotions"][-1]
                    staged = store.read_json(item["source_uri"])
                    if bad == "identity":
                        staged.value["identity"]["next_generated_feature_id_after_release"] = 999
                    version = store.write_json(item["source_uri"], staged.value, staged.version.generation)
                    if bad == "identity":
                        for item in plan["promotions"][-2:]:
                            item["source_generation"] = str(version.generation)
                editor, _ = publisher_fixture(asset, execution="reviewed-edit", store=store)
                store.events.clear()
                with self.assertRaises(p.PublicationError):
                    editor.publish_translation_update(asset=asset, plan=plan)
                self.assertEqual(store.events, [])

    def test_runtime_requires_execution_identity_and_pinned_executor(self):
        publisher, _ = publisher_fixture(sea_ice.ASSET)
        with mock.patch.dict("os.environ", {}, clear=True), self.assertRaises(p.PublicationError):
            OwnedGeneratedPublisher.from_runtime(SimpleNamespace(bucket=lambda name: publisher.bucket), "bucket")
        publisher.executor_sha = "unversioned"
        with self.assertRaises(p.PublicationError):
            publisher.resume(sea_ice.ASSET)
