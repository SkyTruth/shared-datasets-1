"""Version 1 of the closed ingestion deployment target contract (stdlib only)."""
from __future__ import annotations

import json
import os
from pathlib import Path

VERSION = 1
DEFAULTS = {
    "image_config_variable": "", "canary_attempts": 125, "canary_timeout_phase": "failed",
    "canary_run_url": True, "validate_release": True, "resume_scheduler": False,
    "synthetic_smoke": False, "import_probe": "pass",
}
TARGETS = {
    "wdpa-monthly": {
        "workflow": "wdpa-monthly-deploy.yml",
        "image_variable": "wdpa_monthly_image",
        "targets": [
            "module.wdpa_monthly_job.google_cloud_run_v2_job.this",
            "google_iam_deny_policy.canonical_destructive_actions[0]"
        ],
        "rollout_gate": True,
        "paused_gate": True,
        "asset_slug": "",
        "asset_root": "",
        "selection": "wdpa",
        "image_config_variable": "wdpa_monthly_image_config_digest",
        "canary_attempts": 20,
        "canary_timeout_phase": "verification_pending",
        "canary_run_url": False,
        "validate_release": False,
        "import_probe": 'from osgeo import gdal; assert gdal.VersionInfo("RELEASE_NAME") == "3.6.2"; import ingestion.wdpa_monthly.automatic; import scripts.feature_metadata_localization, scripts.translation_local_io'
    },
    "mpatlas-monthly": {
        "workflow": "mpatlas-monthly-deploy.yml",
        "image_variable": "mpatlas_monthly_image",
        "targets": [
            "module.mpatlas_monthly_job.google_cloud_run_v2_job.this",
            "google_cloud_run_v2_job_iam_member.mpatlas_scheduler_invoker",
            "module.mpatlas_monthly_scheduler.google_cloud_scheduler_job.this",
            "google_iam_deny_policy.canonical_destructive_actions[0]"
        ],
        "rollout_gate": False,
        "paused_gate": False,
        "asset_slug": "mpatlas-zone-assessments",
        "asset_root": "100-geographic-reference/130-protected-areas/mpatlas-zone-assessments",
        "selection": "mpatlas",
    },
    "eamlis-monthly": {
        "workflow": "eamlis-monthly-deploy.yml",
        "image_variable": "eamlis_monthly_image",
        "targets": [
            "module.eamlis_monthly_job.google_cloud_run_v2_job.this"
        ],
        "rollout_gate": False,
        "paused_gate": True,
        "asset_slug": "eamlis-abandoned-mine-land-inventory",
        "asset_root": "300-infrastructure-industrial/320-mining/eamlis-abandoned-mine-land-inventory",
        "selection": "eamlis",
    },
    "sea-ice-daily": {
        "workflow": "sea-ice-daily-deploy.yml",
        "image_variable": "sea_ice_daily_image",
        "targets": [
            "module.sea_ice_daily_job.google_cloud_run_v2_job.this"
        ],
        "rollout_gate": True,
        "paused_gate": False,
        "asset_slug": "ims-sea-ice-extent",
        "asset_root": "200-imagery-derived/250-weather-climate/ims-sea-ice-extent",
        "selection": "sea_ice",
        "resume_scheduler": True,
        "synthetic_smoke": True,
    }
}
TARGETS = {target: DEFAULTS | settings for target, settings in TARGETS.items()}


def image_variables(target, image):
    variables = {config["image_variable"]: "unused-by-reconciliation" for config in TARGETS.values()}
    if target in TARGETS:
        variables[TARGETS[target]["image_variable"]] = image
    if target == "dataset-usage":
        variables["dataset_usage_image"] = image
    return variables


if __name__ == "__main__":
    target = os.environ["TARGET"]
    config = TARGETS[target]  # Closed registry: unknown targets fail before authentication.
    assert config["workflow"] == os.environ["WORKFLOW_FILE"]
    encoded = json.dumps(config, separators=(",", ":"))
    with Path(os.environ["GITHUB_OUTPUT"]).open("a") as stream:
        stream.write("config=" + encoded + "\n")
    with Path(os.environ["GITHUB_ENV"]).open("a") as stream:
        stream.write("TARGET_CONFIG=" + encoded + "\n")
