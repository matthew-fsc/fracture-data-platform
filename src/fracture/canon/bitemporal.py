"""The two reads.

Every query against `canon` is either *current* or *as-of*. Nothing else is
permitted, because a query that filters on neither axis silently double-counts
superseded rows and produces a number that cannot be reproduced.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from fracture.core.errors import FractureError


@dataclass(frozen=True)
class AsOf:
    """A pinned read: system time from the pack run, business date from the period."""

    system_time: dt.datetime
    business_date: dt.date | None = None

    def predicate(self, alias: str = "", include_business_time: bool = True) -> str:
        """SQL predicate fragment. `alias` is the table alias, without a dot."""
        p = f"{alias}." if alias else ""
        parts = [
            f"{p}recorded_at <= %(system_time)s",
            f"({p}superseded_at is null or {p}superseded_at > %(system_time)s)",
        ]
        if include_business_time and self.business_date is not None:
            parts += [
                f"{p}valid_from <= %(business_date)s",
                f"({p}valid_to is null or {p}valid_to > %(business_date)s)",
            ]
        return " and ".join(parts)

    def params(self) -> dict[str, object]:
        params: dict[str, object] = {"system_time": self.system_time}
        if self.business_date is not None:
            params["business_date"] = self.business_date
        return params


def current_predicate(alias: str = "", include_business_time: bool = True) -> str:
    p = f"{alias}." if alias else ""
    parts = [f"{p}superseded_at is null"]
    if include_business_time:
        parts.append(f"({p}valid_to is null or {p}valid_to > current_date)")
    return " and ".join(parts)


def assert_temporal_filter(sql: str) -> None:
    """Guard used by the mart runner.

    Any SQL that reads a canon table must constrain system time. This is a
    lexical check, not a parse, so it is deliberately conservative: it looks for
    a reference to `canon.` and then for either `superseded_at` or the
    `%(system_time)s` parameter. False positives are cheap; a mart that
    double-counts a restated row is not.
    """
    lowered = sql.lower()
    if "canon." not in lowered and " canon " not in lowered:
        return
    if "superseded_at" in lowered or "%(system_time)s" in lowered:
        return
    if "canon.v_" in lowered:  # the *_current views already filter
        return
    raise FractureError(
        "SQL reads canon without a system-time filter; add the as-of predicate "
        "or read a canon.v_*_current view"
    )
