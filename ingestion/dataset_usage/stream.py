"""Bounded CSV parsing for native bucket usage logs."""
from __future__ import annotations
import csv
import io

MAX_RECORD_BYTES = 512 * 1024
USAGE_COLUMNS = {"time_micros", "cs_bucket", "cs_method", "cs_uri", "sc_status", "s_request_id"}


def usage_records(stream):
    class BoundedLines:
        record_bytes = 0
        def __iter__(self):
            return self

        def __next__(self):
            line = stream.readline(MAX_RECORD_BYTES + 1)
            if not line:
                raise StopIteration
            self.record_bytes += len(line.encode())
            if self.record_bytes > MAX_RECORD_BYTES:
                raise ValueError("Usage CSV record exceeds its budget")
            return line

    lines = BoundedLines()
    reader = csv.DictReader(lines, strict=True)
    if reader.fieldnames and len(reader.fieldnames) > 256:
        raise ValueError("Usage CSV header exceeds its budget")
    if not reader.fieldnames or not USAGE_COLUMNS.issubset(reader.fieldnames) or len(set(reader.fieldnames)) != len(reader.fieldnames) or "" in reader.fieldnames:
        raise ValueError("Usage CSV header is missing required fields or is ambiguous")
    lines.record_bytes = 0
    for row in reader:
        lines.record_bytes = 0
        if None in row or any(value is None for value in row.values()):
            raise ValueError("Usage CSV row does not match its header")
        yield row


def text_stream(binary):
    return io.TextIOWrapper(binary, encoding="utf-8", newline="")
