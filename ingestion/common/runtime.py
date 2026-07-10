"""Runtime helpers shared by scheduled ingestion jobs."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import mimetypes
import os
import shlex
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any, Callable

from ingestion.common.http import (
    STATUS_NOT_READY,
    STATUS_SUCCESS,
    request_with_retries,
)


LOGGER = logging.getLogger(__name__)


class SourceNotAvailableError(FileNotFoundError):
    """Raised when the upstream source file has not appeared yet."""


def configure_logging() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def require_binary(name: str) -> None:
    if not shutil.which(name):
        raise RuntimeError(f"Required executable not found on PATH: {name}")


def run_command(
    args: list[str],
    *,
    capture_json: bool = False,
    capture_text: bool = False,
    logger: logging.Logger | None = None,
) -> Any:
    active_logger = logger or LOGGER
    active_logger.info("running command: %s", " ".join(shlex.quote(a) for a in args))
    completed = subprocess.run(
        args,
        check=False,
        text=True,
        stdout=subprocess.PIPE if capture_json or capture_text else None,
        stderr=subprocess.PIPE,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "Command failed with exit code "
            f"{completed.returncode}: {' '.join(shlex.quote(a) for a in args)}\n"
            f"{completed.stderr}"
        )
    if capture_json:
        try:
            return json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"Command did not return JSON: {' '.join(args)}\n{completed.stdout}"
            ) from exc
    if capture_text:
        return completed.stdout
    if completed.stderr:
        active_logger.debug(completed.stderr)
    return None


def bind_run_command(logger: logging.Logger) -> Callable[..., Any]:
    """Return a run_command bound to a pipeline logger."""

    def bound_run_command(
        args: list[str],
        *,
        capture_json: bool = False,
        capture_text: bool = False,
    ) -> Any:
        return run_command(
            args,
            capture_json=capture_json,
            capture_text=capture_text,
            logger=logger,
        )

    return bound_run_command


def parse_run_date(
    value: str | None,
    *,
    default_factory: Callable[[], dt.date] | None = None,
) -> dt.date:
    if not value:
        if default_factory is not None:
            return default_factory()
        return dt.datetime.now(dt.UTC).date()
    return dt.date.fromisoformat(value)


def parse_positive_int(value: str | None, default: int, name: str) -> int:
    if value is None or value == "":
        return default
    parsed = int(value)
    if parsed <= 0:
        raise RuntimeError(f"{name} must be greater than 0")
    return parsed


def download_file(
    url: str,
    dest: Path,
    *,
    user_agent: str,
    timeout_seconds: int,
    logger: logging.Logger | None = None,
    source_label: str = "source",
    not_ready_status_codes: set[int] | frozenset[int] = frozenset(),
    not_ready_label: str | None = None,
    opener: Callable[..., Any] | None = None,
) -> None:
    active_logger = logger or LOGGER
    active_logger.info("downloading %s: %s", source_label, url)
    request = urllib.request.Request(
        url,
        headers={"User-Agent": user_agent},
    )
    dest.parent.mkdir(parents=True, exist_ok=True)

    def write_response(response) -> None:
        with dest.open("wb") as file_obj:
            shutil.copyfileobj(response, file_obj)

    outcome, _payload = request_with_retries(
        request,
        timeout_seconds=timeout_seconds,
        not_ready_status_codes=not_ready_status_codes,
        response_reader=write_response,
        opener=opener or urllib.request.urlopen,
        logger=active_logger,
    )
    if outcome.status == STATUS_NOT_READY:
        raise SourceNotAvailableError(
            f"{not_ready_label or source_label} not available yet ({outcome.reason}): {url}"
        )
    if outcome.status != STATUS_SUCCESS:
        raise RuntimeError(
            f"Download failed with {outcome.reason or outcome.status}: {url}"
        )
    if dest.stat().st_size == 0:
        raise RuntimeError(f"Downloaded zero-byte {source_label} file: {url}")


def run_job_main(
    run: Callable[[], Any],
    *,
    logger: logging.Logger,
    failure_message: str,
) -> None:
    """Run a pipeline entry point and emit its records as JSON on stdout."""
    try:
        records = run()
    except Exception:
        logger.exception(failure_message)
        raise
    json.dump(records, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


def remove_if_exists(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def content_type_for(path: Path) -> str | None:
    if path.name.endswith(".ndjson.gz"):
        return "application/x-ndjson"
    suffix = path.suffix.lower()
    if suffix in {".tif", ".tiff"}:
        return "image/tiff; application=geotiff; profile=cloud-optimized"
    if path.suffix == ".fgb":
        return "application/octet-stream"
    if path.suffix == ".pmtiles":
        return "application/vnd.pmtiles"
    if suffix in {".json", ".geojson"}:
        return "application/json"
    if suffix == ".ndgeojson":
        return "application/x-ndjson"
    if suffix == ".png":
        return "image/png"
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".webp":
        return "image/webp"
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed
