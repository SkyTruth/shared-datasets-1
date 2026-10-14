"""Opt-in, daily application activity receipts; never part of data access authority."""
from __future__ import annotations

import datetime as dt
import json
import logging
import re
import threading
import time
import uuid

DEFAULT_USAGE_BUCKET = "skytruth-shared-datasets-1-usage-raw"
_SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_LOG = logging.getLogger(__name__)


class UsageReporter:
    """Buffer one observation per asset/activity/UTC day and explicitly flush.

    The producer prefix is bound to the runtime identity by bucket IAM. No
    application or user identity is accepted in the receipt body. Reporters
    never read, list, overwrite or delete objects. There is no offline queue.
    """

    def __init__(self, producer: str, *, bucket: str = DEFAULT_USAGE_BUCKET,
                 client=None, now=None, diagnostic=None):
        if not isinstance(producer, str) or not 1 <= len(producer) <= 128 or not _SLUG.fullmatch(producer):
            raise ValueError("Usage producer requires a registered identifier")
        self.producer, self.bucket, self.client = producer, bucket, client
        self.now = now or (lambda: dt.datetime.now(dt.timezone.utc))
        self.diagnostic = diagnostic or (lambda code: _LOG.warning("Dataset usage reporting failed: %s", code))
        self._pending, self._sent, self._blocked = {}, set(), {}
        self._sending, self._inflight = set(), False
        self._lock = threading.Lock()

    def recordUsage(self, assetSlug: str, activity: str = "use") -> None:
        if not isinstance(assetSlug, str) or not 1 <= len(assetSlug) <= 128 or not _SLUG.fullmatch(assetSlug) or activity not in {"use", "interest"}:
            raise ValueError("Usage requires a dataset slug and use/interest activity")
        observed = self.now().astimezone(dt.timezone.utc)
        day = observed.date().isoformat()
        key = (day, assetSlug, activity)
        with self._lock:
            self._sent = {entry for entry in self._sent if entry[0] == day}
            self._blocked = {entry: until for entry, until in self._blocked.items() if until > time.monotonic()}
            if key in self._sent or key in self._pending or key in self._blocked or key in self._sending:
                return
            if len(self._pending) >= 1024:
                self.diagnostic("buffer_full")
                return
            body = json.dumps({"schema_version": 1, "asset_slug": assetSlug,
                               "activity": activity, "observed_at": observed.isoformat().replace("+00:00", "Z")}, separators=(",", ":"))
            name = f"app-usage/{self.producer}/v1/{day}/{uuid.uuid4().hex}.json"
            self._pending[key] = (name, body)

    def flush(self) -> bool:
        """Bound the entire flush to three seconds, including credentials.

        Uploads attempt at most twice with a one-second request timeout. One
        daemon worker handles a stalled credential/transport boundary; further
        failed observations are dropped and diagnosed until its work completes.
        No worker or pending upload delays process exit or persists offline.
        """
        done, outcome = threading.Event(), [False]
        with self._lock:
            pending, self._pending = self._pending, {}
            if not pending:
                return not self._inflight
            if self._inflight:
                self._blocked.update((key, time.monotonic() + 300) for key in pending)
                busy = True
            else:
                self._inflight, self._sending, busy = True, set(pending), False
        if busy:
            self.diagnostic("flush_in_flight")
            return False
        deadline = time.monotonic() + 3

        def upload():
            try:
                outcome[0] = self._flush_batch(pending, deadline)
            finally:
                with self._lock:
                    self._inflight, self._sending = False, set()
                done.set()

        # A single daemon worker bounds ADC discovery/refresh as well as upload.
        # A stalled transport cannot accumulate threads or delay process exit.
        threading.Thread(target=upload, daemon=True, name="dataset-usage-flush").start()
        if not done.wait(max(0, deadline - time.monotonic())):
            with self._lock:
                self._blocked.update((key, time.monotonic() + 300) for key in pending)
            self.diagnostic("flush_deadline")
            return False
        return outcome[0]

    def _flush_batch(self, pending, deadline):
        try:
            if self.client is None:
                from google.cloud import storage
                self.client = storage.Client()
            from google.api_core.exceptions import PreconditionFailed
            destination = self.client.bucket(self.bucket)
        except Exception as exc:
            with self._lock:
                self._blocked.update((key, time.monotonic() + 300) for key in pending)
            self.diagnostic(type(exc).__name__)
            return False
        success = True
        for key, (name, body) in pending.items():
            if time.monotonic() >= deadline:
                success = False
                self.diagnostic("flush_deadline")
                with self._lock:
                    self._blocked[key] = time.monotonic() + 300
                continue
            for attempt in range(2):
                try:
                    destination.blob(name).upload_from_string(body, content_type="application/json",
                        if_generation_match=0, timeout=max(0.01, min(1, deadline - time.monotonic())), retry=None)
                except PreconditionFailed:
                    # Our retained random name makes a lost-response retry
                    # idempotent; an existing immutable receipt is sufficient.
                    pass
                except Exception as exc:
                    if attempt == 0:
                        continue
                    success = False
                    self.diagnostic(type(exc).__name__)
                    with self._lock:
                        self._blocked[key] = time.monotonic() + 300
                    break
                with self._lock:
                    self._sent.add(key)
                break
        return success
