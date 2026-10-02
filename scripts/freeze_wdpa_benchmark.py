#!/usr/bin/env python3
"""Freeze verified WDPA publication inputs locally, without any GCS writes."""

from contextlib import ExitStack
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from google.cloud import storage
from ingestion.common.owned_publication import OwnedGeneratedPublisher
from ingestion.wdpa_monthly import run as wdpa, translations
from scripts.feature_metadata_translation_reuse import build_memory


def copy_generation(bucket, uri, generation, destination, expected_sha256):
    name = uri.split("/", 3)[3]
    digest = hashlib.sha256()
    with (
        bucket.blob(name, generation=generation).open(
            "rb", chunk_size=8 * 1024 * 1024, if_generation_match=generation
        ) as source,
        destination.open("xb") as target,
    ):
        while data := source.read(8 * 1024 * 1024):
            digest.update(data)
            target.write(data)
    if digest.hexdigest() != expected_sha256.removeprefix("sha256:"):
        raise RuntimeError("frozen input hash disagrees with its verified generation")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument(
        "--build-translation-cache",
        action="store_true",
        help="Also build a debug cache; complete acceptance replays rebuild from the frozen input files",
    )
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    wdpa.configure_logging()
    publisher = OwnedGeneratedPublisher(
        storage.Client(project=wdpa.DEFAULT_PROJECT_ID),
        wdpa.DEFAULT_BUCKET,
        execution_id="wdpa-benchmark-read-only",
        executor_sha="0" * 40,
        configuration={"benchmark": True},
    )
    pins = {}
    with ExitStack() as stack:
        for asset in wdpa.ASSETS:
            baseline = publisher.load_generated_identity_baseline(
                asset,
                contract_id=wdpa.CONTRACT_ID,
                identity_index_path=args.out / f"{asset.slug}.verified.sqlite",
            )
            if baseline.snapshot is None:
                raise RuntimeError(
                    "acceptance requires a committed baseline, not genesis/reset adoption"
                )
            stack.callback(baseline.records.close)
            snapshot = baseline.snapshot
            manifest_path = args.out / f"{asset.slug}.manifest.json"
            copy_generation(
                publisher.bucket,
                snapshot.path,
                snapshot.generation,
                manifest_path,
                snapshot.sha256,
            )
            manifest = json.loads(manifest_path.read_text())
            metadata = wdpa.release_feature_model.validate_release_manifest(
                manifest,
                expected_asset_slug=asset.slug,
                expected_release=baseline.release,
                require_generations=True,
            )["metadata"]
            copy_generation(
                publisher.bucket,
                metadata["path"],
                metadata["generation"],
                args.out / f"{asset.slug}.metadata.ndjson.gz",
                metadata["sha256"],
            )
            pins[asset.slug] = {
                "manifest_uri": snapshot.path,
                "manifest_generation": snapshot.generation,
                "manifest_sha256": snapshot.sha256,
                "metadata": metadata,
            }
        sources, supplement = translations.download_inputs(
            publisher, wdpa.ASSETS, args.out
        )
        config = {
            "schema_version": 1,
            "sources": sources,
            "files": {},
            "supplement": None,
        }
        for source in sources:
            for key in ("canonical_sidecar", "translation_source"):
                path = Path(source[key])
                relative = str(path.relative_to(args.out))
                config["files"][relative] = wdpa.sha256_file(path)
                source[key] = relative
        if supplement is not None:
            path = args.out / "supplement.ndjson"
            translations.download_source(
                publisher.bucket, supplement, path, compress=False
            )
            config["supplement"] = path.name
            config["files"][path.name] = wdpa.sha256_file(path)
        (args.out / "translation-sources.json").write_text(
            json.dumps(config, indent=2, sort_keys=True) + "\n"
        )
        if args.build_translation_cache:
            cache_sources = [
                {
                    **source,
                    **{
                        key: str(args.out / source[key])
                        for key in ("canonical_sidecar", "translation_source")
                    },
                }
                for source in sources
            ]
            build_memory(
                database=args.out / "translation-memory.sqlite",
                sources=cache_sources,
                fields=translations.FIELDS,
                locales=translations.LOCALES,
                source_key_fields=("SITE_PID",),
            )
        (args.out / "pins.json").write_text(
            json.dumps(pins, indent=2, sort_keys=True) + "\n"
        )
    # Retain only replay inputs; disposable verification indexes have no authority.
    for asset in wdpa.ASSETS:
        (args.out / f"{asset.slug}.verified.sqlite").unlink(missing_ok=True)


if __name__ == "__main__":
    main()
