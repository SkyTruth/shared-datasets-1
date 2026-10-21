#!/usr/bin/env python3
"""Select a scheduled catalog refresh only when its published data would change.

This read-only probe is not a deployment attempt. Failed reads must leave the
selection unset, never manufacture an empty snapshot or report unchanged data.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import catalog_drift_guard as guard
from scripts.collect_release_indexes import collect
from scripts import gcp_read_retry
from scripts.operational_retry import UNATTENDED, operation, run_read


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    args.work_dir.mkdir(parents=True, exist_ok=True)

    def compare():
        with TemporaryDirectory(prefix="release-indexes-", dir=args.work_dir) as directory:
            indexes = Path(directory) / "snapshot"
            collect(guard.get_client(), guard.DEFAULT_BUCKET, indexes, retry=None)
            # Reuse actual publication acceptance. Each retry reads a complete
            # new snapshot and discards all prior partial bytes. SDK retries
            # are disabled so nested readers share this operation's budget.
            return guard.run_checks(guard.parse_args([
                "--bucket", guard.DEFAULT_BUCKET,
                "--release-index-dir", str(indexes.resolve()), "--latest-from-release-index",
            ]), retry=None)

    with operation(profile=UNATTENDED):
        checks = run_read(compare, classify=gcp_read_retry.classify, label="catalog input probe")
    changed = any(not check.ok for check in checks)
    message = ("Catalog inputs changed; selecting the protected deployment."
               if changed else "Catalog inputs unchanged; deployment skipped.")
    print(message)
    with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a") as stream:
        stream.write(message + "\n")
    with Path(os.environ["GITHUB_OUTPUT"]).open("a") as stream:
        stream.write(f"changed={str(changed).lower()}\n")


if __name__ == "__main__":
    main()
