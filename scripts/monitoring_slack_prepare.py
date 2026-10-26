#!/usr/bin/env python3
"""Protected Monitoring caller: publish tested bytes and copy the existing bot credential."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.slack_incident_api import Slack


def main():
    if os.environ.get("CALLER_WORKFLOW") != "cron-alert-policy-sync.yml" or os.environ.get("GITHUB_REF") != "refs/heads/main":
        raise ValueError("Monitoring transport preparation requires its protected main caller")
    token, channel = os.environ["SHARED_DATASETS_SLACK_BOT_TOKEN"], os.environ["SHARED_DATASETS_SLACK_CHANNEL_ID"]
    Slack(token, channel).identity()  # read-only; never send a rehearsal message
    image, config = os.environ["TESTED_IMAGE_ID"], os.environ["TESTED_IMAGE_CONFIG"]
    if not all(re.fullmatch(r"sha256:[0-9a-f]{64}", value) for value in (image, config)):
        raise ValueError("exact tested image identity unavailable")
    registry = "us-central1-docker.pkg.dev/shared-datasets-1/shared-datasets-jobs/monitoring-slack"
    tag = registry + ":" + os.environ["EXECUTOR_SHA"] + "-" + os.environ["GITHUB_RUN_ID"]
    subprocess.run(["gcloud", "auth", "configure-docker", "us-central1-docker.pkg.dev", "--quiet"], check=True)
    subprocess.run(["docker", "tag", image, tag], check=True)
    subprocess.run(["docker", "push", tag], check=True)
    manifest = json.loads(subprocess.check_output(["docker", "buildx", "imagetools", "inspect", "--raw", tag]))
    if manifest.get("config", {}).get("digest") != config:
        raise ValueError("registry image differs from tested bytes")
    digest = subprocess.check_output(["docker", "buildx", "imagetools", "inspect", tag, "--format", "{{.Manifest.Digest}}"], text=True).strip()
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise ValueError("immutable registry manifest unavailable")
    version = subprocess.run(["gcloud", "secrets", "versions", "add", "shared-datasets-slack-bot-token", "--project=shared-datasets-1", "--data-file=-", "--format=value(name)"], input=token.encode(), check=True, stdout=subprocess.PIPE).stdout.decode().strip()
    if not re.fullmatch(r"projects/[0-9]+/secrets/shared-datasets-slack-bot-token/versions/[1-9][0-9]*", version):
        raise ValueError("immutable bot credential version unavailable")
    # Terraform receives only image/channel identities. The credential never
    # enters a plan, Terraform state, command arguments or retained evidence.
    with Path(os.environ["GITHUB_ENV"]).open("a") as stream:
        stream.write(f"TF_VAR_monitoring_slack_image={registry}@{digest}\nTF_VAR_monitoring_slack_channel_id={channel}\nTF_VAR_monitoring_slack_token_version={version.rsplit('/', 1)[-1]}\n")


if __name__ == "__main__":
    main()
