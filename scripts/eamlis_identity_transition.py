#!/usr/bin/env python3
"""Stage a reviewed EAMLIS identity installation, or verify safe schedule resume."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from ingestion.eamlis_monthly import identity as ids, identity_installation as installation
from ingestion.eamlis_monthly import identity_state, source_quality as quality


def require_published(client):
    guard = quality.Guard(client.bucket(quality.BUCKET))
    guard.state, guard.generation = guard.load()
    guard.before = quality.baseline(guard.bucket)
    ids.require(guard.state["owner"] is None, "wait for the current EAMLIS execution to finish")
    ids.require(guard.state.get("identity", {}).get("phase") == "active", "complete the first reviewed identity publication before resuming")
    ledger = identity_state.selected_ledger(guard)
    ids.require(ledger is not None and guard.state["identity"]["phase"] == "active", "complete the first reviewed identity publication before resuming")
    identity_state.activate(guard)
    return {"contract_id": ids.CONTRACT, "phase": "active", "held": guard.held}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    stage = sub.add_parser("stage-plan", help="Read current production evidence; write scratch sources and a local immutable PR document only")
    stage.add_argument("--inventory", type=Path, required=True)
    stage.add_argument("--directory", type=Path, required=True)
    stage.add_argument("--first-release", required=True)
    stage.add_argument("--proposal-id", required=True)
    stage.add_argument("--repo-root", type=Path, default=REPO)
    sub.add_parser("require-published")
    args = parser.parse_args(argv)
    from google.cloud import storage
    client = storage.Client(project="shared-datasets-1")
    if args.command == "require-published":
        print(json.dumps(require_published(client), sort_keys=True))
    else:
        from scripts import reviewed_dataset_plan as plans
        args.directory.mkdir(parents=True, exist_ok=False)
        plan = installation.prepare(client, json.loads(args.inventory.read_bytes()), first_release=args.first_release,
                                    directory=args.directory, proposal_id=args.proposal_id)
        document = {"plan_version": 1, "finalization_version": plans.FINALIZATION_VERSION, "publish": plan}
        plans.write_document(document, repo_root=args.repo_root)
        print(plans.render_document(document))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
