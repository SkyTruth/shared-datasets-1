"""EAMLIS identity hooks for the shared hold flow.

Stdlib-only at import time; reviewed-plan validation imports this module before
application dependencies are installed.
"""
from ingestion.eamlis_monthly.identity_state import (
    selected_ledger, validate, validate_candidate, require_metadata_only, activate,
)

__all__ = ["selected_ledger", "validate", "validate_candidate", "require_metadata_only", "activate"]


def verify_finished(guard, plan):
    from ingestion.eamlis_monthly import checksum_repair
    checksum_repair.verify_finished(guard, plan)
