"""One stewardship and review-horizon contract for asset docs and safety checks."""
from __future__ import annotations

from datetime import date
import re

FRESHNESS_DAYS = {"daily": 2, "monthly": 31 + 7}
CONTACT = re.compile(r"(?:\B@[A-Za-z0-9][A-Za-z0-9-]{0,38}\b|\b[^\s@()]+@[^\s@()]+\.[A-Za-z]{2,}\b)")


def named_contact(value):
    return isinstance(value, str) and bool(CONTACT.search(value))


def validate(steward, cadence, review_by):
    """A named contact plus automated freshness or an explicit review date."""
    if not named_contact(steward):
        raise ValueError("admission.steward must name an accountable person with a GitHub handle or email")
    if not review_by:
        if cadence in FRESHNESS_DAYS:
            return None
        raise ValueError("review_by is required without automated daily/monthly freshness checks")
    if not isinstance(review_by, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", review_by):
        raise ValueError("review_by must be an ISO YYYY-MM-DD date")
    try:
        return date.fromisoformat(review_by)
    except ValueError as exc:
        raise ValueError("review_by must be a real calendar date") from exc


def review_problems(rows, *, today):
    problems = []
    for row in rows:
        if row["status"] != "active":
            continue
        try:
            due = validate(row.get("steward"), row["update_cadence"], row.get("review_by"))
        except ValueError as exc:
            problems.append(f"{row['asset_slug']}: {exc}")
            continue
        if due is not None and today > due:
            problems.append(f"{row['asset_slug']}: stewardship review overdue since {due}; contact {row['steward']}")
    return problems
