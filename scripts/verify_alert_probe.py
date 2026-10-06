#!/usr/bin/env python3
"""Verify the controlled negative control; delivery needs explicit confirmation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re

PROBES = {
    "wdpa-monthly": ("WDPA_FAIL_BEFORE_WRITES", "true",
                     "Controlled WDPA execution failure before any dataset writes"),
    "wdpa-processing-validation": ("WDPA_FAIL_BEFORE_DATASET_WRITES", "1",
                                   "Controlled WDPA validation failure before any dataset writes"),
}


def verify_probe(execution: dict, logs: list[dict], *, job: str, execution_name: str,
                 delivery_confirmation_url: str = "") -> dict:
    flag, value, marker = PROBES[job]
    if not re.fullmatch(re.escape(job) + r"-[a-z0-9-]+", execution_name):
        raise ValueError("probe execution does not belong to the selected job")
    status = execution["status"]
    if (execution["metadata"]["name"] != execution_name or not status.get("completionTime")
            or status.get("failedCount") != 1 or status.get("succeededCount", 0)
            or status.get("cancelledCount", 0)):
        raise ValueError("probe must reach terminal failure without success or cancellation")
    env = execution["spec"]["template"]["spec"]["containers"][0]["env"]
    if sum(item.get("name") == flag and item.get("value") == value for item in env) != 1:
        raise ValueError("execution lacks the exact fail-before-write override")
    controlled = any(
        entry.get("labels", {}).get("run.googleapis.com/execution_name") == execution_name
        and entry.get("resource", {}).get("labels", {}).get("job_name") == job
        and marker in (entry.get("textPayload", "") or entry.get("jsonPayload", {}).get("message", ""))
        for entry in logs
    )
    if not controlled:
        raise ValueError("terminal failure lacks this execution's controlled pre-write failure marker")
    if delivery_confirmation_url and not re.fullmatch(
        r"https://[a-z0-9-]+\.slack\.com/archives/[A-Z0-9]+/p[0-9]{16}", delivery_confirmation_url,
    ):
        raise ValueError("delivery confirmation must link the matching Monitoring Slack message")
    return {"job": job, "execution": execution_name, "negative_control": "verified",
            "delivery": "confirmed" if delivery_confirmation_url else "pending",
            "delivery_confirmation_url": delivery_confirmation_url}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execution", type=Path, required=True)
    parser.add_argument("--logs", type=Path, required=True)
    parser.add_argument("--job", choices=sorted(PROBES), required=True)
    parser.add_argument("--execution-name", required=True)
    parser.add_argument("--delivery-confirmation-url", default="")
    parser.add_argument("--require-delivery", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = verify_probe(json.loads(args.execution.read_text()), json.loads(args.logs.read_text()),
                          job=args.job, execution_name=args.execution_name,
                          delivery_confirmation_url=args.delivery_confirmation_url)
    if args.require_delivery and report["delivery"] != "confirmed":
        raise ValueError("Monitoring delivery confirmation remains a prerequisite")
    args.output.write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
