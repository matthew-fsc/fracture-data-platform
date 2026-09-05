"""Value coercion shared by every adapter.

Raw payloads are JSON. Canonical columns are typed. Every conversion between the
two happens here so that a source sending "1,234.50" and a source sending
1234.5 land as the same Decimal, and so that a bad value raises rather than
silently becoming zero.
"""

from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal, InvalidOperation
from typing import Any

from fracture.core.errors import AdapterError

_NUMERIC_NOISE = re.compile(r"[,\s$]")
_PARENS_NEGATIVE = re.compile(r"^\((.*)\)$")

_DATE_FORMATS = (
    "%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%Y%m%d", "%m-%d-%Y", "%b %d, %Y", "%d-%b-%Y",
)


def as_decimal(value: Any, field: str = "value", default: Decimal | None = None) -> Decimal:
    """Parse a money or rate value.

    Handles thousands separators, currency symbols, and accounting negatives in
    parentheses -- all three appear in custodian and carrier file feeds. An
    unparseable value raises; it does not become zero, because a zero that
    should have been $84,000 reconciles to nothing and nobody notices.
    """
    if value is None or value == "":
        if default is not None:
            return default
        raise AdapterError(f"{field}: expected a number, got empty")
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        raise AdapterError(f"{field}: boolean is not a number")
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    text = _NUMERIC_NOISE.sub("", str(value).strip())
    negative = False
    match = _PARENS_NEGATIVE.match(text)
    if match:
        negative, text = True, match.group(1)
    if text.endswith("-"):  # trailing-sign format from older systems
        negative, text = True, text[:-1]
    try:
        parsed = Decimal(text)
    except InvalidOperation as exc:
        raise AdapterError(f"{field}: cannot parse {value!r} as a number") from exc
    return -parsed if negative else parsed


def as_date(value: Any, field: str = "date", default: dt.date | None = None) -> dt.date:
    if value is None or value == "":
        if default is not None:
            return default
        raise AdapterError(f"{field}: expected a date, got empty")
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    text = str(value).strip()
    if "T" in text:
        text = text.split("T", 1)[0]
    for fmt in _DATE_FORMATS:
        try:
            return dt.datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise AdapterError(f"{field}: cannot parse {value!r} as a date")


def as_datetime(value: Any, field: str = "timestamp", default: dt.datetime | None = None) -> dt.datetime:
    if value is None or value == "":
        if default is not None:
            return default
        raise AdapterError(f"{field}: expected a timestamp, got empty")
    if isinstance(value, dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return dt.datetime.combine(as_date(value, field), dt.time.min, tzinfo=dt.timezone.utc)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)


def as_text(value: Any, field: str = "text", required: bool = True, max_len: int | None = None) -> str:
    if value is None:
        if required:
            raise AdapterError(f"{field}: required text field is null")
        return ""
    text = str(value).strip()
    if required and not text:
        raise AdapterError(f"{field}: required text field is empty")
    return text[:max_len] if max_len else text


def optional_text(value: Any, max_len: int | None = None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[:max_len] if max_len else text


def as_bool(value: Any, default: bool | None = None) -> bool:
    if isinstance(value, bool):
        return value
    if value is None or value == "":
        if default is None:
            raise AdapterError("expected a boolean, got empty")
        return default
    return str(value).strip().lower() in {"1", "true", "t", "yes", "y"}


def last4(value: Any) -> str | None:
    """Keep the last four characters of an identifier and nothing else.

    Full taxpayer identifiers never enter the canonical layer. There is no
    reporting question that needs one, and holding them multiplies the blast
    radius of any breach.
    """
    text = optional_text(value)
    if not text:
        return None
    digits = re.sub(r"\D", "", text)
    return digits[-4:] if len(digits) >= 4 else None
