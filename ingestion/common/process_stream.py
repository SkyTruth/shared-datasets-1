"""Native process streams with bounded diagnostics and complete error checks."""

from __future__ import annotations

import json
import subprocess
from contextlib import contextmanager
from pathlib import Path


def stop(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def diagnostic(path: Path) -> str:
    with path.open("rb") as stream:
        stream.seek(max(0, path.stat().st_size - 16384))
        return stream.read().decode("utf-8", errors="replace")


@contextmanager
def feature_stream(args: list[str], *, log_path: Path):
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("wb") as errors:
        process = subprocess.Popen(
            args, stdout=subprocess.PIPE, stderr=errors, text=True
        )
        try:
            exhausted = False

            def records():
                nonlocal exhausted
                for number, line in enumerate(process.stdout, 1):
                    line = line.lstrip("\x1e").strip()
                    if not line:
                        continue
                    record = json.loads(line)
                    if not isinstance(record, dict) or record.get("type") != "Feature":
                        raise RuntimeError(
                            f"native stream record {number} is not a GeoJSON feature"
                        )
                    yield record
                exhausted = True

            yield records()
            if not exhausted:
                raise RuntimeError("native feature stream was not completely consumed")
            code = process.wait()
            if code:
                raise RuntimeError(
                    f"native producer failed ({code}): {args!r}\n{diagnostic(log_path)}"
                )
        finally:
            process.stdout.close()
            stop(process)


def pipe_commands(producer_args: list[str], consumer_args: list[str], *, log_dir: Path):
    log_dir.mkdir(parents=True, exist_ok=True)
    producer_log, consumer_log = log_dir / "producer.log", log_dir / "consumer.log"
    with (
        producer_log.open("wb") as producer_errors,
        consumer_log.open("wb") as consumer_errors,
    ):
        producer = subprocess.Popen(
            producer_args, stdout=subprocess.PIPE, stderr=producer_errors
        )
        consumer = None
        try:
            consumer = subprocess.Popen(
                consumer_args, stdin=producer.stdout, stderr=consumer_errors
            )
            producer.stdout.close()
            consumer_code = consumer.wait()
            if consumer_code:
                stop(producer)
            producer_code = producer.wait()
            if producer_code or consumer_code:
                raise RuntimeError(
                    f"native pipeline failed (producer={producer_code}, consumer={consumer_code}):\n"
                    f"{diagnostic(producer_log)}\n{diagnostic(consumer_log)}"
                )
        finally:
            producer.stdout.close()
            stop(producer)
            if consumer is not None:
                stop(consumer)
