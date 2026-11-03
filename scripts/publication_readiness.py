#!/usr/bin/env python3
"""Discover a complete publication batch locally, without cloud or Git writes."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ingestion.common import publication_readiness as readiness
from ingestion.common.publication_sources import run

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path)
    args = parser.parse_args(argv)
    config = json.loads(args.plan.read_text())
    try:
        report = run(config)
    except (ValueError, RuntimeError, OSError) as exc:
        # Invalid batch inputs cannot leave an old READY result behind.
        report = {"schema": readiness.SCHEMA, "state": "INCOMPLETE", "error": str(exc)}
        directory = Path(config["work_dir"])
        directory.mkdir(parents=True, exist_ok=True)
        readiness.write_json(directory / "readiness.json", report)
    print(json.dumps(report, sort_keys=True))
    return {"READY": 0, "NEEDS_DECISION": 2}.get(report["state"], 1)


if __name__ == "__main__":
    raise SystemExit(main())
