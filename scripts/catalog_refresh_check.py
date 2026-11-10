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

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import deployment_source


def catalog_changed(client, directory):
    from scripts import catalog_drift_guard as guard, collect_release_indexes
    indexes = directory / "release-indexes"
    collect_release_indexes.collect(client, guard.DEFAULT_BUCKET, indexes)
    return any(not check.ok for check in guard.run_checks(guard.parse_args([
        "--bucket", guard.DEFAULT_BUCKET,
        "--release-index-dir", str(indexes.resolve()), "--latest-from-release-index",
    ])))


def main(argv=None):
    from scripts import catalog_drift_guard as guard
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    # Reuse the actual publication acceptance contract. generated_at alone is
    # not an input change; release history, latest runs and metadata all are.
    changed = catalog_changed(guard.get_client(), args.work_dir)
    try:
        if changed:
            deployment_source.resolve_main_ci(deployment_source.GitHub(),
                os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_SHA"])
    except deployment_source.PendingCI:
        message = "Catalog refresh deferred: main CI is still pending. The next daily probe will check again."
    else:
        message = ("Catalog inputs changed; selecting the protected deployment."
                   if changed else "Catalog inputs unchanged; deployment skipped.")
        with Path(os.environ["GITHUB_OUTPUT"]).open("a") as stream:
            stream.write(f"changed={str(changed).lower()}\n")
    print(message)
    with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a") as stream:
        stream.write(message + "\n")


if __name__ == "__main__":
    main()
