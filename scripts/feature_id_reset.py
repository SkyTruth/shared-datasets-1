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
from ingestion.common.publication import strict_json


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    candidate = IdentityResetCandidate.build(strict_json(args.inventory.read_bytes()))
    args.output.write_text(json.dumps(candidate.review_envelope(), indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
