"""Helpers for exercising the streaming generated-identity release writer.

`feature_metadata.write_generated_id_release` writes straight to disk and never
returns record collections, so tests read the written artifacts back to assert
on their contents.
"""

from __future__ import annotations

import gzip
import json
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence
from unittest import mock

from ingestion.common import feature_metadata


def write_generated_release(
    features: Sequence[Mapping[str, Any]],
    **kwargs: Any,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], feature_metadata.GeneratedIdentityRelease]:
    """Write a release from `features` and read the artifacts back.

    `features` must be a re-iterable sequence: the writer makes one identity
    pass and one materialization pass over it.
    """

    with tempfile.TemporaryDirectory(prefix="release-streaming-") as tmp:
        tmp_path = Path(tmp)
        enriched_path = tmp_path / "enriched.geojsonseq"
        sidecar_path = tmp_path / "metadata.ndjson.gz"
        with mock.patch("scripts.slack_notify.notify", return_value=True):
            result = feature_metadata.write_generated_id_release(
                open_features=lambda: iter(list(features)),
                enriched_features_path=enriched_path,
                sidecar_path=sidecar_path,
                **kwargs,
            )
        enriched = [
            json.loads(line)
            for line in enriched_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        with gzip.open(sidecar_path, "rt", encoding="utf-8") as handle:
            sidecar = [json.loads(line) for line in handle if line.strip()]
    return enriched, sidecar, result
