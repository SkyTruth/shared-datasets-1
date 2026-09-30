#!/usr/bin/env python3
"""Allow deployment once the affected assets have valid publication state."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ingestion.common import publication as p
from ingestion.common.identity_reset import ASSET_ROOTS, CONTRACT_ID
from ingestion.common.reset_controls import ASSET_JOBS, BUCKET, PROJECT


def check_deployment(store: p.Store, job: str, *, require_published: bool = False) -> None:
    """Read the same durable state/receipts required by the runtime publishers."""
    for slug, owner in ASSET_JOBS.items():
        if owner != job:
            continue
        context = p.Context("0" * 64, "0" * 64, "0" * 40, p.FINALIZATION_VERSION,
                            BUCKET, ASSET_ROOTS[slug], slug, CONTRACT_ID)
        state = store.read_json(context.state_uri)
        p.require(state is not None, f"{slug}: install the reviewed feature-ID reset before deploying {job}; see docs/feature-id-reset-installation.md")
        p.validate_state(state.value, context)
        p.validate_state_references(store, state.value, context)
        if require_published:
            current = state.value["current"]
            p.require(state.value["active"] is None and current is not None
                      and current["receipt_uri"] != state.value["adoption_receipt"],
                      f"{slug}: complete its first publication before resuming the schedule")
            receipt = store.read_json(current["receipt_uri"])
            p.require(receipt.value["phase"] == "derived_complete",
                      f"{slug}: finish publication finalization before resuming the schedule")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job", required=True, choices=sorted(set(ASSET_JOBS.values())))
    parser.add_argument("--require-published", action="store_true", help="Require completed publication before scheduler resumption.")
    args = parser.parse_args(argv)
    from google.cloud import storage
    from ingestion.common.publication_gcs import GcsStore

    try:
        check_deployment(GcsStore(storage.Client(project=PROJECT)), args.job, require_published=args.require_published)
    except p.PublicationError as exc:
        print(f"FEATURE_ID_DEPLOYMENT_NOT_READY: {exc}", file=sys.stderr)
        return 1
    print(f"{args.job}: publication state verified; deployment may proceed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
