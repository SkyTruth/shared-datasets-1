import gzip
from contextlib import nullcontext
import hashlib
import io
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from unittest import mock

from ingestion.common import publication as publication
from ingestion.wdpa_monthly import translations
from scripts.feature_metadata_translation_reuse import build_memory
from test_feature_metadata_translation_reuse import record, row, source_bundle
from test_publication import publication_temp_directory


class TranslationInputTests(unittest.TestCase):
    def test_monthly_run_builds_and_cleans_all_locales_for_both_assets(self):
        from ingestion.wdpa_monthly import run as wdpa
        from test_wdpa_monthly import fake_asset_outputs
        from scripts import release_feature_model as model
        publisher = mock.Mock(spec=wdpa.GcsPublisher)
        publisher.resume.return_value = None
        publisher.successful_run_record.return_value = False
        publisher.load_generated_identity_baseline.return_value = model.GeneratedIdentityBaseline.genesis(contract_id="test-v1")
        memory = object()
        made = []
        def build(**kwargs):
            self.assertIs(kwargs["translation_memory"], memory)
            outputs = fake_asset_outputs(kwargs["workdir"], asset=kwargs["asset"])
            made.append(outputs)
            return outputs
        with (
            patch.dict("os.environ", {"RUN_DATE": "2026-05-01"}, clear=True),
            patch.object(wdpa, "require_binary"),
            patch.object(wdpa.storage, "Client"),
            patch.object(wdpa.GcsPublisher, "from_runtime", return_value=publisher),
            patch.object(wdpa, "download_file"),
            patch.object(wdpa, "prepare_source_datasets", return_value=["fixture"]),
            patch.object(wdpa, "discover_source_layers", return_value=([], "REALM", ())),
            patch.object(wdpa.release_feature_model, "load_identity_resolution_decisions", return_value=[]),
            patch.object(wdpa.translations, "prepare_memory", return_value=nullcontext(memory)) as prepare,
            patch.object(wdpa, "build_asset_outputs", side_effect=build),
            patch.object(wdpa, "publish_asset", side_effect=lambda **kw: {"asset_slug": kw["asset"].slug}),
            patch.object(wdpa, "remove_if_exists", wraps=wdpa.remove_if_exists) as remove,
        ):
            result = wdpa.run()
            self.assertEqual(len(result), 2)
            prepare.assert_called_once()
            removed = {call.args[0] for call in remove.call_args_list}
            self.assertTrue(all(path in removed for outputs in made for path in outputs.localized_metadata.values()))

    def test_generation_pinned_download_verifies_uncompressed_bytes(self):
        data = b"translation source\n"
        version = publication.ObjectVersion("gs://test-bucket/asset/source.csv", 11, hashlib.sha256(data).hexdigest(), len(data))
        calls = []
        class Bucket:
            name = "test-bucket"
            def blob(self, name, generation):
                calls.append((name, generation))
                def open(mode, **kwargs):
                    calls.append((mode, kwargs["if_generation_match"]))
                    return io.BytesIO(data)
                return SimpleNamespace(open=open)
        with publication_temp_directory() as temp:
            destination = Path(temp) / "source.csv.gz"
            translations.download_source(Bucket(), version, destination, compress=True)
            self.assertEqual(gzip.decompress(destination.read_bytes()), data)
            self.assertEqual(calls, [("asset/source.csv", 11), ("rb", 11)])
            wrong = publication.ObjectVersion(version.path, 11, "a" * 64, len(data))
            with self.assertRaisesRegex(publication.PublicationError, "hash differs"):
                translations.download_source(Bucket(), wrong, Path(temp) / "wrong", compress=False)

    def test_monthly_memory_reuses_committed_receipt_sources_for_all_locales(self):
        with publication_temp_directory() as temp:
            root = Path(temp)
            slug = "wdpa-marine"
            old = record("1", "site-a", "Alpha", slug=slug)
            old["properties"]["NAME_ENG"] = old["properties"].pop("name")
            rows = [{**row("1", "Alpha", "translated " + locale), "field": "NAME_ENG", "locale": locale} for locale in translations.LOCALES]
            bundle = source_bundle(root / "source", [old], rows)
            versions = {}
            contents = {}
            for suffix, key in zip(translations.SUFFIXES, ("canonical_sidecar", "translation_source"), strict=True):
                data = Path(bundle[key]).read_bytes()
                uri = f"gs://test-bucket/root/releases/2026-06-09/{slug}{suffix}"
                versions[suffix] = publication.ObjectVersion(uri, 42, hashlib.sha256(data).hexdigest(), len(data))
                contents[uri] = data
            publisher = SimpleNamespace(bucket=SimpleNamespace(name="test-bucket"), committed_artifacts=lambda asset, suffixes: versions)
            def download(bucket, version, destination, *, compress):
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(gzip.compress(contents[version.path]) if compress else contents[version.path])
            with patch.object(translations, "download_source", side_effect=download), patch.object(translations, "build_memory", wraps=build_memory) as build:
                with translations.prepare_memory(publisher, [SimpleNamespace(slug=slug, root="root")], root / "work") as memory:
                    direct = memory.direct(slug, '["site-a"]')
                    self.assertEqual(len(direct), 6)
                    self.assertEqual({value[1] for value in direct.values()}, {"translated " + locale for locale in translations.LOCALES})
                    self.assertEqual(build.call_args.kwargs["sources"][0]["release"], "2026-06-09")

    def test_legacy_evidence_cannot_be_used_for_an_unreviewed_bucket(self):
        publisher = SimpleNamespace(bucket=SimpleNamespace(name="different-bucket"), committed_artifacts=lambda asset, suffixes: None)
        with publication_temp_directory() as temp, self.assertRaisesRegex(publication.PublicationError, "pinned to the production"):
            with translations.prepare_memory(publisher, [SimpleNamespace(slug="wdpa-marine")], Path(temp)):
                self.fail("must reject")
