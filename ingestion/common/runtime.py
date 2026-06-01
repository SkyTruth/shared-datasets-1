"""Runtime helpers shared by scheduled ingestion jobs."""

from __future__ import annotations

import hashlib
import json
import logging
import mimetypes
import os
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any


LOGGER = logging.getLogger(__name__)


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
