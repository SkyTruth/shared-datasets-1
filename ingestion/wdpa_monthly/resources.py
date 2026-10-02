"""Scratch-storage contract and structured per-phase resource measurements."""

from __future__ import annotations

import json
import logging
import os
import resource
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path


LOGGER = logging.getLogger(__name__)


def prepare_scratch(
    *, mount: Path = Path("/work"), mountinfo: Path = Path("/proc/self/mountinfo")
):
    if os.environ.get("CLOUD_RUN_EXECUTION"):
        mounts = {}
        for line in mountinfo.read_text().splitlines():
            before, after = line.split(" - ", 1)
            mounts[before.split()[4]] = after.split()[0]
        filesystem = mounts.get(str(mount))
        if filesystem is None or filesystem in {"tmpfs", "ramfs"}:
            raise RuntimeError(
                "WDPA requires disk-backed /work; configure the ephemeral DISK volume before running"
            )
        for name in ("TMPDIR", "SHARED_DATASETS_WORKDIR"):
            value = Path(os.environ.get(name, "")).resolve()
            if not value.is_relative_to(mount):
                raise RuntimeError(f"{name} must be under the disk-backed /work mount")
    temporary = Path(os.environ.get("TMPDIR", "/tmp"))
    temporary.mkdir(parents=True, exist_ok=True)
    os.environ["CPL_TMPDIR"] = str(temporary)
    os.environ["SQLITE_TMPDIR"] = str(temporary)


def cgroup_memory():
    root = Path("/sys/fs/cgroup")
    current = root / "memory.current"
    if current.exists():
        peak = root / "memory.peak"
        return int(current.read_text()), int(
            peak.read_text()
        ) if peak.exists() else None
    return None, None


def scratch_bytes(path):
    total = 0
    for root, _directories, files in os.walk(path):
        for name in files:
            try:
                stat = (Path(root) / name).stat()
                total += stat.st_blocks * 512
            except FileNotFoundError:
                # Sampling races deliberate removal of completed intermediates.
                pass
    return total


class PhaseProfiler:
    def __init__(self, workdir: Path, *, versions=None, interval=5, scratch_root=None):
        self.workdir, self.versions, self.interval = workdir, versions or {}, interval
        self.scratch_root = scratch_root or workdir
        disk = os.statvfs(self.scratch_root)
        self.initial_free_bytes = disk.f_bfree * disk.f_frsize
        self.records = []

    @contextmanager
    def phase(self, name):
        started = time.monotonic()
        LOGGER.info(
            "%s",
            json.dumps(
                {
                    "event": "wdpa_phase_started",
                    "phase": name,
                    "native_versions": self.versions,
                }
            ),
        )
        peak_memory, peak_scratch = 0, 0
        done = threading.Event()
        sampling_errors = []

        def sample():
            nonlocal peak_memory, peak_scratch
            current, _peak = cgroup_memory()
            peak_memory = max(peak_memory, current or 0)
            disk = os.statvfs(self.scratch_root)
            # Native tools can unlink temporary files while keeping them open.
            # Directory sizes alone would miss their live disk consumption.
            used_delta = max(0, self.initial_free_bytes - disk.f_bfree * disk.f_frsize)
            peak_scratch = max(
                peak_scratch, scratch_bytes(self.scratch_root), used_delta
            )

        def monitor():
            try:
                while not done.wait(self.interval):
                    sample()
            except Exception as exc:
                sampling_errors.append(exc)

        sample()
        thread = threading.Thread(target=monitor, daemon=True)
        thread.start()
        state = "failed"
        try:
            yield
            state = "succeeded"
        finally:
            done.set()
            thread.join()
            sample()
            _current, job_peak = cgroup_memory()
            rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            record = {
                "event": "wdpa_phase_resources",
                "phase": name,
                "state": state,
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "phase_memory_peak_bytes": peak_memory or None,
                "cgroup_memory_peak_bytes": job_peak,
                "process_rss_peak_bytes": rss
                if sys.platform == "darwin"
                else rss * 1024,
                "scratch_peak_bytes": peak_scratch,
                "native_versions": self.versions,
                "artifact_sizes": {
                    p.name: p.stat().st_size
                    for p in self.workdir.iterdir()
                    if p.is_file()
                },
            }
            self.records.append(record)
            LOGGER.info("%s", json.dumps(record, sort_keys=True))
            if sampling_errors and state == "succeeded":
                raise RuntimeError("WDPA resource sampler failed") from sampling_errors[
                    0
                ]
