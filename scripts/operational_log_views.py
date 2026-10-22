"""Exact read-only Logging view contract shared with production Terraform.

A view is one four-atom conjunction. The union is expressed by distinct view
resources, never by nesting Boolean branches inside an IAM access filter.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import MappingProxyType

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = "catalog/operational-log-views.json"
PROJECT = "shared-datasets-1"
REGION = "us-central1"
BUCKETS = ("_Default", "_Required")
APPROVED_FAMILIES = MappingProxyType({
    "run-stderr": ("cloud_run_job", "run.googleapis.com/stderr"),
    "run-activity": ("cloud_run_job", "cloudaudit.googleapis.com/activity"),
    "run-data-access": ("cloud_run_job", "cloudaudit.googleapis.com/data_access"),
    "scheduler-executions": ("cloud_scheduler_job", "cloudscheduler.googleapis.com/executions"),
})
FAMILY_KEYS = tuple(APPROVED_FAMILIES)
VIEW_NAMES = tuple("shared-datasets-operations-" + family for family in FAMILY_KEYS)


def unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate operational log-view contract key")
        value[key] = item
    return value


def load_families(root=ROOT):
    value = json.loads((root / CONTRACT).read_text(), object_pairs_hook=unique_object)
    if not isinstance(value, dict) or set(value) != set(FAMILY_KEYS):
        raise ValueError("operational log-view contract requires every exact family")
    for family, row in value.items():
        if (not isinstance(row, dict) or set(row) != {"resource_type", "log_id"}
                or (row["resource_type"], row["log_id"]) != APPROVED_FAMILIES[family]):
            raise ValueError("invalid operational log-view family: " + family)
    return MappingProxyType({family: MappingProxyType(value[family]) for family in FAMILY_KEYS})


FAMILIES = load_families()
VIEWS = tuple(f"projects/{PROJECT}/locations/global/buckets/{bucket}/views/{name}"
              for bucket in BUCKETS for name in VIEW_NAMES)


def filter_for(family, *, families=FAMILIES):
    row = families[family]
    return " AND ".join((
        f'resource.labels.project_id="{PROJECT}"',
        f'resource.labels.location="{REGION}"',
        f'resource.type="{row["resource_type"]}"',
        f'log_id("{row["log_id"]}")',
    ))


def expected_filters(root=ROOT):
    families = load_families(root)
    return {f"projects/{PROJECT}/locations/global/buckets/{bucket}/views/{name}": filter_for(family, families=families)
            for bucket in BUCKETS for family, name in zip(FAMILY_KEYS, VIEW_NAMES, strict=True)}
