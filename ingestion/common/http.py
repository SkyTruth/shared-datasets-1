"""HTTP request helpers for scheduled ingestion jobs."""

from __future__ import annotations

import logging
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, TypeVar


STATUS_SUCCESS = "success"
STATUS_NOT_READY = "not_ready"
STATUS_TRANSIENT = "transient"
STATUS_FATAL = "fatal"

DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_RETRY_BACKOFF_SECONDS = 2
DEFAULT_TRANSIENT_STATUS_CODES = frozenset({429, 500, 502, 503, 504})

T = TypeVar("T")


@dataclass(frozen=True)
class RequestOutcome:
    status: str
    url: str
    attempts: int
    http_status: int | None = None
    reason: str | None = None

    def warning_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "url": self.url,
            "status": self.status,
            "attempts": self.attempts,
        }
        if self.http_status is not None:
            payload["http_status"] = self.http_status
        if self.reason:
            payload["reason"] = self.reason
        return payload


def is_transient_status(
    status: int,
    transient_status_codes: set[int] | frozenset[int],
) -> bool:
    return status in transient_status_codes or 500 <= status <= 599


def response_status(response: Any) -> int:
    status = getattr(response, "status", None)
    if status is None:
        status = response.getcode()
    return int(status)


def classify_http_status(
    status: int,
    *,
    url: str,
    attempt: int,
    not_ready_status_codes: set[int] | frozenset[int],
    transient_status_codes: set[int] | frozenset[int],
) -> RequestOutcome:
    if status < 400:
        return RequestOutcome(
            status=STATUS_SUCCESS,
            url=url,
            attempts=attempt,
            http_status=status,
        )
    if status in not_ready_status_codes:
        return RequestOutcome(
            status=STATUS_NOT_READY,
            url=url,
            attempts=attempt,
            http_status=status,
            reason=f"HTTP {status}",
        )
    if is_transient_status(status, transient_status_codes):
        return RequestOutcome(
            status=STATUS_TRANSIENT,
            url=url,
            attempts=attempt,
            http_status=status,
            reason=f"HTTP {status}",
        )
    return RequestOutcome(
        status=STATUS_FATAL,
        url=url,
        attempts=attempt,
        http_status=status,
        reason=f"HTTP {status}",
    )


def request_with_retries(
    request: urllib.request.Request,
    *,
    timeout_seconds: int,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    retry_backoff_seconds: int = DEFAULT_RETRY_BACKOFF_SECONDS,
    not_ready_status_codes: set[int] | frozenset[int] = frozenset(),
    transient_status_codes: set[int] | frozenset[int] = DEFAULT_TRANSIENT_STATUS_CODES,
    response_reader: Callable[[Any], T] | None = None,
    opener: Callable[..., Any] | None = None,
    logger: logging.Logger | None = None,
) -> tuple[RequestOutcome, T | None]:
    """Open a urllib request with bounded retries for transient failures."""

    if max_attempts <= 0:
        raise RuntimeError("HTTP max_attempts must be greater than zero")

    active_opener = opener or urllib.request.urlopen
    active_logger = logger or logging.getLogger(__name__)
    url = request.full_url
    last_outcome: RequestOutcome | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            with active_opener(request, timeout=timeout_seconds) as response:
                http_status = response_status(response)
                outcome = classify_http_status(
                    http_status,
                    url=url,
                    attempt=attempt,
                    not_ready_status_codes=not_ready_status_codes,
                    transient_status_codes=transient_status_codes,
                )
                if outcome.status == STATUS_SUCCESS:
                    payload = response_reader(response) if response_reader else None
                    return outcome, payload
        except urllib.error.HTTPError as exc:
            outcome = classify_http_status(
                exc.code,
                url=url,
                attempt=attempt,
                not_ready_status_codes=not_ready_status_codes,
                transient_status_codes=transient_status_codes,
            )
        except urllib.error.URLError as exc:
            outcome = RequestOutcome(
                status=STATUS_TRANSIENT,
                url=url,
                attempts=attempt,
                reason=str(exc.reason),
            )
        except TimeoutError as exc:
            outcome = RequestOutcome(
                status=STATUS_TRANSIENT,
                url=url,
                attempts=attempt,
                reason=str(exc),
            )

        if outcome.status in {STATUS_NOT_READY, STATUS_FATAL}:
            return outcome, None
        last_outcome = outcome
        if attempt < max_attempts:
            active_logger.warning(
                "transient HTTP request failure %s/%s for %s: %s",
                attempt,
                max_attempts,
                url,
                outcome.reason or outcome.http_status or "unknown error",
            )
            time.sleep(retry_backoff_seconds * attempt)

    if last_outcome is None:
        raise RuntimeError(f"HTTP request did not run: {url}")
    return last_outcome, None
