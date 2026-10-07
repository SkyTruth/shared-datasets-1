"""Owner-dispatched synthetic Slack lifecycle; never reads the incident ledger."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.slack_incident_api import Slack, SlackError, require

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "SkyTruth/shared-datasets-1"
WORKFLOW = ".github/workflows/slack-incident-rehearsal.yml"


def context(environment, checkout_sha):
    require(environment.get("GITHUB_REPOSITORY") == REPOSITORY
            and environment.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
            and environment.get("GITHUB_REF") == "refs/heads/main"
            and environment.get("GITHUB_WORKFLOW_REF") == REPOSITORY + "/" + WORKFLOW + "@refs/heads/main",
            "Slack rehearsal requires its owner-dispatched trusted main workflow")
    require(environment.get("GITHUB_ACTOR") == "jonaraphael"
            and environment.get("GITHUB_TRIGGERING_ACTOR") == "jonaraphael",
            "only the repository owner may dispatch a Slack rehearsal")
    sha = environment.get("GITHUB_WORKFLOW_SHA", "")
    require(re.fullmatch(r"[0-9a-f]{40}", sha)
            and sha == environment.get("GITHUB_SHA") == checkout_sha,
            "Slack rehearsal checkout must equal the exact trusted workflow revision")
    run, attempt = environment.get("GITHUB_RUN_ID", ""), environment.get("GITHUB_RUN_ATTEMPT", "")
    require(re.fullmatch(r"[1-9][0-9]*", run) and re.fullmatch(r"[1-9][0-9]*", attempt),
            "Slack rehearsal requires an exact run and attempt")
    return {"repository": REPOSITORY, "sha": sha, "run_id": run, "attempt": attempt}


def rehearse(slack, binding, work_dir):
    work_dir.mkdir(parents=True, exist_ok=True)
    path = work_dir / "result.json"
    label = f"SYNTHETIC REHEARSAL {binding['run_id']}/{binding['attempt']}"
    record = {"schema": "slack-incident-rehearsal-v1", "binding": binding,
              "identity": slack.identity(), "status": "started", "stage": "open",
              "acknowledgements": {}, "permalink": ""}

    def save():
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(record, sort_keys=True, indent=2) + "\n")
        temporary.replace(path)

    def acknowledge(operation, ts):
        record["acknowledgements"][operation] = ts
        save()

    save()
    try:
        parent = slack.post({"text": f"{label} · Open\nControlled notification test. No workflow, dataset or deployment has failed."})
        acknowledge("parent", parent)
        record["stage"] = "permalink"
        save()
        record["permalink"] = slack.permalink(parent)
        save()
        record["stage"] = "retry-thread"
        save()
        reply = slack.post({"text": f"{label} · Synthetic repeat observation\nThis is a lifecycle test, not another failed run."}, thread_ts=parent)
        acknowledge("retry", reply)
        record["stage"] = "resolved-update"
        save()
        slack.update(parent, {"text": f"{label} · Resolved\nSynthetic parent state updated; this does not represent production recovery."})
        acknowledge("resolved-update", parent)
        record["stage"] = "recovery-thread"
        save()
        reply = slack.post({"text": f"{label} · Synthetic recovery\nThe test parent was updated to Resolved. No deployment was executed or retried."}, thread_ts=parent, broadcast=True)
        acknowledge("recovery", reply)
    except SlackError:
        record["status"] = "failed"
        save()
        raise
    record.update(status="success", stage="complete")
    save()
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", required=True, type=Path)
    args = parser.parse_args()
    checkout = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    binding = context(os.environ, checkout)
    slack = Slack(os.environ.get("SHARED_DATASETS_SLACK_BOT_TOKEN", ""),
                  os.environ.get("SHARED_DATASETS_SLACK_CHANNEL_ID", ""))
    record = rehearse(slack, binding, args.work_dir)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a") as stream:
            stream.write(f"- [Synthetic Slack lifecycle rehearsal]({record['permalink']}): all operations acknowledged; production incident ledger untouched.\n")


if __name__ == "__main__":
    main()
