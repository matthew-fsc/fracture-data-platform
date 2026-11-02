"""Time handling for the bitemporal model (spec section 6.3).

Business time  : valid_from / valid_to   -- when the fact was true
System time    : recorded_at / superseded_at -- when we learned it

A pack pins system time. Every read inside a pack run must be issued
`as of` that instant or the byte-identical reissue guarantee is void.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import threading
from typing import Iterator

#: Postgres 'infinity' for timestamptz; used as the open end of a validity range.
INFINITY = dt.datetime(9999, 12, 31, 23, 59, 59, tzinfo=dt.timezone.utc)

_local = threading.local()


def utcnow() -> dt.datetime:
    """Current instant, or the frozen system time when inside `freeze_system_time`."""
    frozen = getattr(_local, "frozen", None)
    if frozen is not None:
        return frozen
    return dt.datetime.now(dt.timezone.utc)


@contextlib.contextmanager
def freeze_system_time(instant: dt.datetime) -> Iterator[dt.datetime]:
    """Pin system time for the duration of a pack run.

    Nested freezes are an error rather than a silent override: two different
    frozen instants inside one call stack means one of the two is producing
    numbers nobody can reproduce.
    """
    if instant.tzinfo is None:
        raise ValueError("system time must be timezone-aware")
    previous = getattr(_local, "frozen", None)
    if previous is not None and previous != instant:
        raise RuntimeError(
            f"system time already frozen at {previous.isoformat()}; "
            f"refusing to re-freeze at {instant.isoformat()}"
        )
    _local.frozen = instant
    try:
        yield instant
    finally:
        _local.frozen = previous


def current_system_time() -> dt.datetime | None:
    return getattr(_local, "frozen", None)


def month_bounds(year: int, month: int) -> tuple[dt.date, dt.date]:
    start = dt.date(year, month, 1)
    end = dt.date(year + (month == 12), (month % 12) + 1, 1)
    return start, end
