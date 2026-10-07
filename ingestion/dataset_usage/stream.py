"""Bounded JSON array/NDJSON and CSV parsing for exported log shards."""
from __future__ import annotations
import csv
import io
import json

MAX_RECORD_BYTES = 512 * 1024
USAGE_COLUMNS = {"time_micros", "cs_bucket", "cs_method", "cs_uri", "sc_status", "s_request_id"}


def json_records(stream, limit=MAX_RECORD_BYTES):
    decoder = json.JSONDecoder()
    buffer = ""
    eof = False

    def fill():
        nonlocal buffer, eof
        chunk = stream.read(min(64 * 1024, limit))
        buffer += chunk
        eof = not chunk

    def whitespace():
        nonlocal buffer
        while True:
            buffer = buffer.lstrip()
            if buffer or eof:
                return
            fill()

    whitespace()
    array = buffer.startswith("[")
    if array:
        buffer = buffer[1:]
    first = True
    while True:
        whitespace()
        if array and buffer.startswith("]"):
            if not first:
                raise ValueError("Trailing comma in exported JSON")
            buffer = buffer[1:]
            whitespace()
            if buffer:
                raise ValueError("Data after exported JSON array")
            return
        if not buffer and eof:
            if array:
                raise ValueError("Truncated exported JSON array")
            return
        while True:
            try:
                value, end = decoder.raw_decode(buffer)
                break
            except json.JSONDecodeError as exc:
                if eof:
                    raise ValueError("Malformed exported JSON") from exc
                if len(buffer.encode()) > limit:
                    raise ValueError("Log record exceeds its budget")
                fill()
        if len(buffer[:end].encode()) > limit or not isinstance(value, dict):
            raise ValueError("Log record exceeds its budget or is not an object")
        yield value
        buffer = buffer[end:]
        if array:
            whitespace()
            if buffer.startswith("]"):
                buffer = buffer[1:]
                whitespace()
                if buffer:
                    raise ValueError("Data after exported JSON array")
                return
            if not buffer.startswith(","):
                raise ValueError("Missing exported JSON delimiter")
            buffer = buffer[1:]
            first = False
        else:
            if not buffer and not eof:
                fill()
            # NDJSON allows only whitespace between object records.
            if buffer and not buffer[0].isspace():
                raise ValueError("Missing NDJSON separator")


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
