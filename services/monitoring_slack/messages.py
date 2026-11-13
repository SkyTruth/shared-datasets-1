"""Compact operational messages: observed facts, one next action, exact evidence."""
from __future__ import annotations

import html
import re


def plain(value, limit=500):
    """Keep log excerpts single-line and redact credentials before persistence."""
    value = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", str(value))
    value = re.sub(r"(?:github_pat_|gh[pousr]_|xox[baprs]-)[A-Za-z0-9_-]+", "[redacted]", value)
    value = re.sub(r"(?i)\b(authorization\s*[:=]\s*(?:bearer|basic|token)\s+)\S+", r"\1[redacted]", value)
    value = re.sub(r"(?i)\b([\w-]*(?:token|secret|password|credential)[\w-]*\s*[:=]\s*)[^\s,;]+", r"\1[redacted]", value)
    value = re.sub(r"(https?://[^\s?]+)\?\S+", r"\1?[redacted]", value)
    value = " ".join(re.sub(r"[\x00-\x1f\x7f]", " ", value).split())
    return value if len(value) <= limit else value[:limit - 1] + "…"


def card(title, *, details=(), action, links, status):
    mark = {"error": ":red_circle:", "warning": ":warning:", "success": ":white_check_mark:",
            "decision": ":raising_hand:"}[status]
    def escape(value):
        return html.escape(plain(value), quote=False)
    lines = [f"{mark} {escape(title)}", *(escape(detail) for detail in details if detail),
             "*Next:* " + escape(action), " · ".join(f"<{url}|{label}>" for url, label in links)]
    return {"text": "\n".join(lines), "blocks": []}
