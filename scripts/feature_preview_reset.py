#!/usr/bin/env python3
"""Generation-safe reset of the stable feature-preview data plane."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping, Sequence
from typing import Any


PROJECT_ID = "shared-datasets-1"
BUCKET_NAME = "skytruth-shared-datasets-1-preview"
FIRESTORE_DATABASE = "feature-preview"
COLLECTION_ROOT = "feature_preview_index"
EXPECTED_ENV = {
    "GOOGLE_CLOUD_PROJECT": PROJECT_ID,
    "SHARED_DATASETS_BUCKET": BUCKET_NAME,
    "FEATURE_PREVIEW_FIRESTORE_DATABASE": FIRESTORE_DATABASE,
    "FEATURE_PREVIEW_COLLECTION_ROOT": COLLECTION_ROOT,
}


class PreviewResetError(RuntimeError):
    """Raised when the reset boundary or postcondition is violated."""


def validate_environment(env: Mapping[str, str]) -> None:
    for name, expected in EXPECTED_ENV.items():
        actual = env.get(name)
        if actual != expected:
            raise PreviewResetError(f"{name} must be exactly {expected!r}, got {actual!r}")


def reset_preview_data(
    *,
    storage_client: Any,
    firestore_client: Any,
    dry_run: bool = False,
) -> dict[str, Any]:
    blobs = list(storage_client.list_blobs(BUCKET_NAME))
    objects = []
    for blob in blobs:
        generation = getattr(blob, "generation", None)
        if generation is None:
            raise PreviewResetError(f"preview object is missing a generation: {blob.name}")
        objects.append({"name": blob.name, "generation": int(generation)})

    collection = firestore_client.collection(COLLECTION_ROOT)
    if not dry_run:
        for blob, record in zip(blobs, objects, strict=True):
            blob.delete(if_generation_match=record["generation"])
        firestore_client.recursive_delete(collection)

        remaining_objects = [blob.name for blob in storage_client.list_blobs(BUCKET_NAME, max_results=1)]
        if remaining_objects:
            raise PreviewResetError(
                "preview bucket changed during reset; objects remain and must be reviewed: "
                + ", ".join(remaining_objects)
            )
        if next(iter(collection.limit(1).stream()), None) is not None:
            raise PreviewResetError(f"Firestore collection {COLLECTION_ROOT!r} is not empty after reset")

    return {
        "project": PROJECT_ID,
        "bucket": BUCKET_NAME,
        "firestore_database": FIRESTORE_DATABASE,
        "collection_root": COLLECTION_ROOT,
        "object_count": len(objects),
        "objects": objects,
        "dry_run": dry_run,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        validate_environment(os.environ)
        from google.cloud import firestore, storage

        result = reset_preview_data(
            storage_client=storage.Client(project=PROJECT_ID),
            firestore_client=firestore.Client(project=PROJECT_ID, database=FIRESTORE_DATABASE),
            dry_run=args.dry_run,
        )
    except PreviewResetError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
