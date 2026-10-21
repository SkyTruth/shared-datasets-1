"""Bounded retries for read-only GitHub CLI transport failures.

Consumers still parse and validate every successful response. Failed commands,
including partial pagination output, never supply evidence. Writes do not use
this helper; authorization, rate limits and unknown CLI errors fail immediately.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
import tempfile
import threading
from scripts.operational_retry import request_timeout, run_read


HTTP_ERROR = re.compile(rb"gh: (?:HTTP ([0-9]{3})|[^\r\n]+ \(HTTP ([0-9]{3})\))")
TRANSIENT_STATUSES = {500, 502, 503, 504}
CONNECTION_ERROR = (
    b"error connecting to api.github.com\n"
    b"check your internet connection or https://githubstatus.com"
)
READ_TIMEOUT_SECONDS = 60


def transport_failure(error: Exception) -> str | None:
    if isinstance(error, subprocess.TimeoutExpired):
        return "read timeout"
    if not isinstance(error, subprocess.CalledProcessError):
        return None
    stderr = (error.stderr or b"").strip()
    status = HTTP_ERROR.fullmatch(stderr)
    if status:
        code = int(status[1] or status[2])
        if code in TRANSIENT_STATUSES:
            return f"HTTP {code}"
    if stderr == CONNECTION_ERROR:
        return "connection failure"
    return None


def read(path: str, *, paginate: bool = False, allow_escape_sequences: bool = False) -> bytes:
    command = ["gh", "api", "--method", "GET"]
    if paginate:
        command += ["--paginate", "--slurp"]
    if allow_escape_sequences:
        command.append("--allow-escape-sequences")
    command.append(path)
    return run_read(
        lambda: subprocess.check_output(command, stderr=subprocess.PIPE, timeout=request_timeout(READ_TIMEOUT_SECONDS)),
        classify=transport_failure, label=f"GitHub GET {path}",
    )


def download(path: str, destination: Path, *, max_bytes: int) -> None:
    """Stream one bounded artifact, discarding incomplete bytes before retry.

    The exclusive destination belongs to this operation only. Content/digest
    validation occurs after this function returns and cannot trigger a retry.
    """
    if type(max_bytes) is not int or max_bytes <= 0:
        raise ValueError("download requires a positive byte limit")
    command = ["gh", "api", "--method", "GET", path]

    def attempt() -> None:
        timeout = request_timeout(READ_TIMEOUT_SECONDS)
        created = False
        try:
            with destination.open("xb") as stream, tempfile.TemporaryFile() as stderr:
                created = True
                with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=stderr) as process:
                    errors: list[Exception] = []

                    def copy() -> None:
                        try:
                            size = 0
                            while chunk := process.stdout.read(1024 ** 2):
                                size += len(chunk)
                                if size > max_bytes:
                                    raise ValueError("GitHub artifact download exceeds byte limit")
                                stream.write(chunk)
                        except Exception as error:
                            errors.append(error)
                            if process.poll() is None:
                                process.kill()

                    reader = threading.Thread(target=copy, daemon=True)
                    reader.start()
                    try:
                        process.wait(timeout=timeout)
                    finally:
                        if process.poll() is None:
                            process.kill()
                            process.wait()
                        reader.join()
                    if errors:
                        raise errors[0]
                    if process.returncode:
                        stderr.seek(0)
                        raise subprocess.CalledProcessError(process.returncode, command, stderr=stderr.read())
        except BaseException:
            if created:
                destination.unlink()
            raise

    run_read(attempt, classify=transport_failure, label=f"GitHub GET {path}")
