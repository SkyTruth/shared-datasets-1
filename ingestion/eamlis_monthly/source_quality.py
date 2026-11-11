"""EAMLIS net-count policy and identity hooks for the shared hold flow.

Stdlib-only at import time; reviewed-plan validation imports this module before
application dependencies are installed.
"""
from pathlib import Path
import json

from ingestion.common.source_quality import EAMLIS, now, require
from ingestion.eamlis_monthly.identity_state import (
    selected_ledger, validate, validate_candidate, require_metadata_only, activate,
)

__all__ = ["selected_ledger", "validate", "validate_candidate", "require_metadata_only", "activate"]

POLICY_FILE = Path(__file__).resolve().parents[2] / "catalog/ingestion-quality.json"


def policy():
    value = json.loads(POLICY_FILE.read_text())
    require(value["schema_version"] == 1, "unsupported ingestion quality policy")
    limit = value["assets"][EAMLIS.SLUG]["maximum_geometry_count_drop_percent"]
    require(type(limit) is int and 0 <= limit < 100, "invalid geometry-count loss threshold")
    return limit


def assess(guard, count, *, selection, previous_selection=None):
    require(type(count) is int and count >= 0, "invalid geometry count")
    observation = {"geometry_count": count, "selection": selection, "checked_at": now(),
                   "maximum_drop_percent": policy()}
    if count == 0:
        guard.block("empty_source", observation)
    elif guard.before and count * 100 < guard.before["release"]["rows"] * (100 - policy()):
        guard.block("geometry_count_drop", observation)
    elif previous_selection is not None and selection != previous_selection:
        guard.block("source_selection_changed", observation)
    elif guard.held:
        guard.state["hold"]["observation"] = observation
        guard.save()
    return guard.held


def verify_finished(guard, plan):
    from ingestion.eamlis_monthly import checksum_repair
    checksum_repair.verify_finished(guard, plan)
