"""Bounded retries for read-only GitHub CLI transport failures.

Consumers still parse and validate every successful response. Failed commands,
including partial pagination output, never supply evidence. Writes do not use
this helper; authorization, rate limits and unknown CLI errors fail immediately.
"""
from __future__ import annotations

import re
import subprocess
import sys
import time


HTTP_ERROR = re.compile(rb"gh: [^\r\n]+ \(HTTP ([0-9]{3})\)")
TRANSIENT_STATUSES = {500, 502, 503, 504}
CONNECTION_ERROR = (
    b"error connecting to api.github.com\n"
    b"check your internet connection or https://githubstatus.com"
)
READ_TIMEOUT_SECONDS = 60


def read(path: str, *, paginate: bool = False) -> bytes:
    command = ["gh", "api", "--method", "GET"]
    if paginate:
        command += ["--paginate", "--slurp"]
    command.append(path)
    for attempt, delay in enumerate((5, 15, None), 1):
        try:
            return subprocess.check_output(command, stderr=subprocess.PIPE, timeout=READ_TIMEOUT_SECONDS)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
            if isinstance(error, subprocess.TimeoutExpired):
                reason, transient = "read timeout", True
            else:
                stderr = (error.stderr or b"").strip()
                status = HTTP_ERROR.fullmatch(stderr)
                reason = f"HTTP {int(status[1])}" if status else "GitHub CLI error"
                transient = bool(status and int(status[1]) in TRANSIENT_STATUSES) or stderr == CONNECTION_ERROR
                if stderr == CONNECTION_ERROR:
                    reason = "connection failure"
            diagnostic = f"GitHub GET {path}: attempt {attempt}/3 failed ({reason})"
            if not transient or delay is None:
                print(diagnostic + "; giving up", file=sys.stderr)
                raise
            print(diagnostic + f"; retrying in {delay}s", file=sys.stderr)
            time.sleep(delay)
