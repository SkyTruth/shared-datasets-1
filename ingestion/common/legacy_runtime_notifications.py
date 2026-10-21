"""Temporary explicit bridge while old/native runtime delivery is drained.

Unified mode never constructs a delivery capability. No Secret Manager lookup,
cross-chat messaging or retry occurs here. Remove this bridge after reviewed
cutover and exact old-image/drain evidence permit runtime secret retirement.
"""
from __future__ import annotations

import os

from scripts import slack_notify

MODE_ENV = "SHARED_DATASETS_OPERATIONAL_DELIVERY_MODE"


def mode(environment=None):
    value = (os.environ if environment is None else environment).get(MODE_ENV, "native")
    if value not in {"native", "unified"}:
        raise ValueError("Unsupported operational delivery mode")
    return value


def notify(**kwargs):
    if mode() == "unified":
        return False
    return slack_notify.notify(capability=slack_notify.webhook_capability("legacy_runtime_bridge"), **kwargs)
