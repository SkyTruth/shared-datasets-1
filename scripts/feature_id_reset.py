#!/usr/bin/env python3
"""Prepare a pre-launch identity reset for review without network calls/writes."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ingestion.common.identity_reset import IdentityResetCandidate
from ingestion.common.publication import canonical, strict_json


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--objects-directory", type=Path, help="Create a new local directory with the three exact JSON objects for scratch staging.")
    args = parser.parse_args(argv)
    candidate = IdentityResetCandidate.build(strict_json(args.inventory.read_bytes()))
    if args.objects_directory:
        args.objects_directory.mkdir(parents=True, exist_ok=False)
        for index, item in enumerate(candidate.review_envelope()["objects"]):
            (args.objects_directory / f"{index}.json").write_bytes(canonical(item["value"]))
    args.output.write_text(json.dumps(candidate.review_envelope(), indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
