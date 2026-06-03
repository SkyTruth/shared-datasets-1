#!/usr/bin/env python3
"""Materialize localized metadata sidecars from translation-source updates."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from google.api_core.exceptions import NotFound, PreconditionFailed  # noqa: E402

from scripts import feature_metadata_localization, gcs_asset, reviewed_dataset_plan  # noqa: E402


TRANSLATION_SOURCE_SUFFIX = ".metadata-translations.csv"
CANONICAL_METADATA_SUFFIX = ".metadata.ndjson.gz"
RELEASE_RE = re.compile(r"/releases/(\d{4}-\d{2}-\d{2})/")
NO_CACHE_CONTROL = "no-cache, max-age=0, must-revalidate"


class FeatureMetadataTranslationPipelineError(ValueError):
    """Raised when a translation-source update cannot be materialized."""


def translation_source_uris_from_publish_plan(path: Path, *, bucket: str) -> list[str]:
    plan = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(plan, dict):
        raise FeatureMetadataTranslationPipelineError("publish plan must be a JSON object")
    normalized = reviewed_dataset_plan.normalize_publish_plan(plan, bucket=bucket)
    uris: list[str] = []
    seen: set[str] = set()
    for promotion in normalized["promotions"]:
        destination_uri = promotion["destination_uri"]
        if not destination_uri.endswith(TRANSLATION_SOURCE_SUFFIX) or destination_uri in seen:
            continue
        uris.append(destination_uri)
        seen.add(destination_uri)
    return uris


def sibling_uri(translation_source_uri: str, suffix: str) -> str:
    if not translation_source_uri.endswith(TRANSLATION_SOURCE_SUFFIX):
        raise FeatureMetadataTranslationPipelineError(
            f"translation source URI must end with {TRANSLATION_SOURCE_SUFFIX}: {translation_source_uri}"
        )
    return translation_source_uri[: -len(TRANSLATION_SOURCE_SUFFIX)] + suffix


def localized_destination_uri(canonical_sidecar_uri: str, locale: str) -> str:
    if not canonical_sidecar_uri.endswith(CANONICAL_METADATA_SUFFIX):
        raise FeatureMetadataTranslationPipelineError(
            f"canonical sidecar URI must end with {CANONICAL_METADATA_SUFFIX}: {canonical_sidecar_uri}"
        )
    stem = canonical_sidecar_uri[: -len(CANONICAL_METADATA_SUFFIX)]
    return f"{stem}.metadata.{feature_metadata_localization.normalize_locale(locale)}.ndjson.gz"


def release_from_uri(uri: str) -> str | None:
    match = RELEASE_RE.search(uri)
    return match.group(1) if match else None


def download_object(uri: str, destination: Path) -> dict[str, Any]:
    blob = gcs_asset.get_blob(uri)
    try:
        blob.reload()
    except NotFound as exc:
        raise FeatureMetadataTranslationPipelineError(f"required input object does not exist: {uri}") from exc
    generation = int(blob.generation)
    destination.parent.mkdir(parents=True, exist_ok=True)
    blob.download_to_filename(destination, if_generation_match=generation)
    return {
        "uri": uri,
        "generation": str(generation),
        "size": blob.size,
        "content_type": blob.content_type,
    }


def upload_object_with_current_generation(src: Path, uri: str) -> dict[str, Any]:
    gcs_asset.require_mutation_allowed(uri, operation="upload")
    blob = gcs_asset.get_blob(uri)
    try:
        blob.reload()
        generation_match = int(blob.generation)
    except NotFound:
        generation_match = 0
    if "/latest/" in uri:
        blob.cache_control = NO_CACHE_CONTROL
    try:
        blob.upload_from_filename(
            src,
            content_type=gcs_asset.content_type_for(src, None),
            if_generation_match=generation_match,
        )
    except PreconditionFailed as exc:
        raise FeatureMetadataTranslationPipelineError(
            f"destination changed while uploading generated sidecar: {uri}"
        ) from exc
    blob.reload()
    return {
        "uri": uri,
        "generation": blob.generation,
        "size": blob.size,
        "content_type": blob.content_type,
        "cache_control": blob.cache_control,
        "replace_generation": "" if generation_match == 0 else str(generation_match),
    }


def materialize_translation_source(
    *,
    translation_source_uri: str,
    work_dir: Path,
    asset_slug: str | None,
    release: str | None,
    upload: bool,
    fail_on_stale: bool,
) -> dict[str, Any]:
    canonical_sidecar_uri = sibling_uri(translation_source_uri, CANONICAL_METADATA_SUFFIX)
    schema_uri = sibling_uri(translation_source_uri, ".schema.json")
    inferred_release = release or release_from_uri(translation_source_uri)
    source_root = work_dir / re.sub(r"[^A-Za-z0-9_.-]+", "_", translation_source_uri.removeprefix("gs://"))
    canonical_sidecar = source_root / Path(canonical_sidecar_uri).name
    translation_source = source_root / Path(translation_source_uri).name
    schema = source_root / Path(schema_uri).name
    output_dir = source_root / "localized"
    report_dir = source_root / "reports"

    inputs = [
        download_object(canonical_sidecar_uri, canonical_sidecar),
        download_object(translation_source_uri, translation_source),
        download_object(schema_uri, schema),
    ]

    fields = feature_metadata_localization.resolved_translatable_fields(schema=schema, fields=[])
    reports = feature_metadata_localization.materialize_locale_sidecars(
        canonical_sidecar=canonical_sidecar,
        translation_source=translation_source,
        output_dir=output_dir,
        locales=None,
        translatable_fields=fields,
        expected_asset_slug=asset_slug,
        expected_release=inferred_release,
        fail_on_stale=fail_on_stale,
        report_dir=report_dir,
    )
    uploads = []
    if upload:
        for report in reports:
            uploads.append(
                upload_object_with_current_generation(
                    Path(report.output_sidecar),
                    localized_destination_uri(canonical_sidecar_uri, report.locale),
                )
            )
    return {
        "translation_source_uri": translation_source_uri,
        "canonical_sidecar_uri": canonical_sidecar_uri,
        "schema_uri": schema_uri,
        "asset_slug": asset_slug,
        "release": inferred_release,
        "inputs": inputs,
        "locales": [report.locale for report in reports],
        "reports": [report.to_dict() for report in reports],
        "uploads": uploads,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--publish-plan", type=Path, help="Reviewed publish plan JSON containing promoted objects.")
    parser.add_argument(
        "--translation-source-uri",
        action="append",
        default=[],
        help="Canonical or reviewed translation source URI. May be repeated.",
    )
    parser.add_argument("--bucket", default=reviewed_dataset_plan.DEFAULT_BUCKET)
    parser.add_argument("--asset-slug", help="Expected asset slug for sidecar validation.")
    parser.add_argument("--release", help="Expected YYYY-MM-DD release for sidecar validation.")
    parser.add_argument("--work-dir", required=True, type=Path)
    parser.add_argument("--report", type=Path, help="Pipeline summary JSON path.")
    parser.add_argument("--upload", action="store_true", help="Upload generated sidecars with generation preconditions.")
    parser.add_argument("--fail-on-stale", action="store_true", help="Fail if stale translations are detected.")
    return parser


def unique_uris(values: Sequence[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        uri = value.strip()
        if not uri or uri in seen:
            continue
        result.append(uri)
        seen.add(uri)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        uris = unique_uris(args.translation_source_uri)
        if args.publish_plan:
            uris.extend(
                uri
                for uri in translation_source_uris_from_publish_plan(args.publish_plan, bucket=args.bucket)
                if uri not in set(uris)
            )
        payload: dict[str, Any] = {
            "valid": True,
            "upload": args.upload,
            "translation_source_count": len(uris),
            "translation_sources": [],
        }
        for uri in uris:
            payload["translation_sources"].append(
                materialize_translation_source(
                    translation_source_uri=uri,
                    work_dir=args.work_dir,
                    asset_slug=args.asset_slug,
                    release=args.release,
                    upload=args.upload,
                    fail_on_stale=args.fail_on_stale,
                )
            )
    except (
        FeatureMetadataTranslationPipelineError,
        feature_metadata_localization.FeatureMetadataLocalizationError,
        reviewed_dataset_plan.PlanValidationError,
        OSError,
        json.JSONDecodeError,
    ) as exc:
        print(f"feature-metadata-translation-pipeline failed: {exc}", file=sys.stderr)
        return 2

    summary = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(summary, encoding="utf-8")
    print(summary, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
