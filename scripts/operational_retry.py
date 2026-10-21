"""One bounded retry budget for a read-only operation and its nested helpers.

Callers classify only recognized transport failures. Validation and mutation
must remain outside the retried action. Unattended recovery uses absolute
offsets, so slow requests or nested readers cannot extend the deadline.
"""
from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import sys
import time
from typing import TypeVar


@dataclass(frozen=True)
class RetryProfile:
    name: str
    offsets: tuple[int, ...]
    deadline_seconds: int | None


SHORT = RetryProfile("short", (0, 5, 20), None)
UNATTENDED = RetryProfile("unattended", (0, 5, 20, 60, 180, 420, 900, 1740), 1800)
T = TypeVar("T")


class RetryBudget:
    """Share retry opportunities, including across successful nested reads.

    Successful reads do not consume retry opportunities. A failure in any
    nested helper advances the same schedule; starting another read cannot
    replenish it. The deadline starts when the operation is registered.
    """

    def __init__(self, profile: RetryProfile = SHORT):
        if profile not in (SHORT, UNATTENDED):
            raise ValueError("unsupported safe-read retry profile")
        self.profile = profile
        self.started = time.monotonic()
        self.retry_index = 0

    def run(self, action: Callable[[], T], *, classify: Callable[[Exception], str | None], label: str) -> T:
        while True:
            try:
                return action()
            except Exception as error:
                reason = classify(error)
                if reason is None:
                    raise
                offsets = self.profile.offsets
                attempt = self.retry_index + 1
                diagnostic = f"{label}: attempt {attempt}/{len(offsets)} failed ({reason})"
                elapsed = time.monotonic() - self.started
                if attempt == len(offsets) or (
                    self.profile.deadline_seconds is not None and elapsed >= self.profile.deadline_seconds
                ):
                    print(diagnostic + "; giving up", file=sys.stderr)
                    raise
                delay = (
                    offsets[attempt] - offsets[attempt - 1]
                    if self.profile.deadline_seconds is None
                    else max(0, offsets[attempt] - elapsed)
                )
                self.retry_index += 1
                print(diagnostic + f"; retrying in {delay:g}s", file=sys.stderr)
                time.sleep(delay)
                if self.profile.deadline_seconds is not None and time.monotonic() - self.started >= self.profile.deadline_seconds:
                    raise


_CURRENT: ContextVar[RetryBudget | None] = ContextVar("safe_read_retry_budget", default=None)


@contextmanager
def operation(*, profile: RetryProfile = SHORT) -> Iterator[RetryBudget]:
    """Select a budget once at the owning operation; nested scopes reuse it."""
    current = _CURRENT.get()
    if current is not None:
        yield current
        return
    budget = RetryBudget(profile)
    token = _CURRENT.set(budget)
    try:
        yield budget
    finally:
        _CURRENT.reset(token)


def run_read(action: Callable[[], T], *, classify: Callable[[Exception], str | None], label: str) -> T:
    with operation() as budget:
        return budget.run(action, classify=classify, label=label)


def request_timeout(maximum: float) -> float:
    """Cap a transport timeout at the owning operation's remaining window."""
    budget = _CURRENT.get()
    if budget is None or budget.profile.deadline_seconds is None:
        return maximum
    remaining = budget.profile.deadline_seconds - (time.monotonic() - budget.started)
    if remaining <= 0:
        raise TimeoutError("safe-read recovery deadline exhausted")
    return min(maximum, remaining)
