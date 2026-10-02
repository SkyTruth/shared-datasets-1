"""Scratch-storage contract and structured per-phase resource measurements."""

from __future__ import annotations

import json
import logging
import os
import resource
import stat
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


CGROUP_ROOT = Path("/sys/fs/cgroup")


def memory_controller(root=CGROUP_ROOT):
    """Select the namespaced controller exposed by Docker or Cloud Run."""
    if (root / "memory.current").exists():
        return root, "memory.current", "memory.peak", "memory.max"
    if (root / "memory/memory.usage_in_bytes").exists():
        return (
            root / "memory",
            "memory.usage_in_bytes",
            "memory.max_usage_in_bytes",
            "memory.limit_in_bytes",
        )
    return None


def cgroup_limits(root=CGROUP_ROOT):
    cpu = None
    if (root / "cpu.max").exists():
        quota, period = (root / "cpu.max").read_text().split()
        if quota != "max":
            cpu = int(quota) / int(period)
    elif (root / "cpu,cpuacct/cpu.cfs_quota_us").exists():
        controller = root / "cpu,cpuacct"
        quota = int((controller / "cpu.cfs_quota_us").read_text())
        if quota >= 0:
            cpu = quota / int((controller / "cpu.cfs_period_us").read_text())
    controller = memory_controller(root)
    memory = None
    if controller:
        directory, _current, _peak, limit = controller
        value = (directory / limit).read_text().strip()
        if value != "max":
            memory = int(value)
    return cpu, memory


def validation_limits(cpu, memory):
    # The protected job spec pins 4 CPU. Cloud Run's measured CPU quota is
    # 3.72; never substitute configuration or affinity for the kernel reading.
    return (
        isinstance(cpu, (int, float))
        and not isinstance(cpu, bool)
        and 0 < cpu <= 4
        and memory == 8 * 1024**3
    )


def cgroup_memory(root=CGROUP_ROOT):
    controller = memory_controller(root)
    if controller is None:
        return None, None
    directory, current, peak, _limit = controller
    peak_path = directory / peak
    return int((directory / current).read_text()), (
        int(peak_path.read_text()) if peak_path.exists() else None
    )


def cgroup_memory_stat(root=CGROUP_ROOT):
    controller = memory_controller(root)
    if controller is None:
        return {}
    directory, _current, _peak, _limit = controller
    return {
        key: int(value)
        for key, value in (
            line.split()
            for line in (directory / "memory.stat").read_text().splitlines()
        )
        if key
        in {
            "anon",
            "file",
            "file_dirty",
            "file_mapped",
            "kernel",
            "shmem",
            "rss",
            "cache",
            "dirty",
            "mapped_file",
            "swap",
        }
    }


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


def release_file_cache(path):
    """Ask Linux to release unused regular-file cache without changing bytes.

    DONTNEED keeps dirty and mapped pages intact and schedules their writeback.
    Only the explicitly tracked scratch and frozen-input trees are eligible.
    """
    if not hasattr(os, "posix_fadvise"):
        return
    for root, _directories, files in os.walk(path):
        for name in files:
            file_path = Path(root) / name
            try:
                if not stat.S_ISREG(file_path.lstat().st_mode):
                    continue
                descriptor = os.open(
                    file_path,
                    os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
                )
            except FileNotFoundError:
                continue
            try:
                if stat.S_ISREG(os.fstat(descriptor).st_mode):
                    os.posix_fadvise(descriptor, 0, 0, os.POSIX_FADV_DONTNEED)
            finally:
                os.close(descriptor)


class PhaseProfiler:
    def __init__(
        self,
        workdir: Path,
        *,
        versions=None,
        interval=0.5,
        scratch_root=None,
        input_cache_roots=(),
    ):
        self.workdir, self.versions, self.interval = workdir, versions or {}, interval
        self.scratch_root = scratch_root or workdir
        disk = os.statvfs(self.scratch_root)
        self.initial_free_bytes = disk.f_bfree * disk.f_frsize
        self.records = []
        self.cache_pressure_bytes = 4 * 1024**3
        # Replay inputs are read-only frozen files outside production scratch.
        self.cache_roots = (self.scratch_root, *input_cache_roots)

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
        cache_releases = 0
        peak_breakdown = {}
        done = threading.Event()
        sampling_errors = []

        def sample():
            nonlocal peak_memory, peak_scratch, cache_releases, peak_breakdown
            current, _peak = cgroup_memory()
            if current and current > peak_memory:
                peak_breakdown = cgroup_memory_stat()
            peak_memory = max(peak_memory, current or 0)
            if current and current > self.cache_pressure_bytes:
                for root in self.cache_roots:
                    release_file_cache(root)
                cache_releases += 1
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
                "memory_breakdown_at_sampled_peak": peak_breakdown,
                "scratch_cache_releases": cache_releases,
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
