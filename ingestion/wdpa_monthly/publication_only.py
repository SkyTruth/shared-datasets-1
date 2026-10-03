"""Run an approved retained publication or a controlled pre-write failure."""

import os

from ingestion.common import publication as p
from ingestion.common.runtime import run_job_main
from ingestion.wdpa_monthly import run as wdpa


def run():
    p.require(
        "WDPA_PROMOTION_BUNDLE" in os.environ
        or os.environ.get("WDPA_FAIL_BEFORE_WRITES") == "true",
        "publication requires a reviewed retained bundle; source rebuilding is disabled",
    )
    return wdpa.run()


def main():
    run_job_main(run, logger=wdpa.LOGGER, failure_message="wdpa publication failed")


if __name__ == "__main__":
    main()
