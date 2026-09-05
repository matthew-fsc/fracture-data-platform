"""Deterministic hashing.

`_record_hash` on every raw row and the byte-identical reissue guarantee on
packs both depend on one thing: a canonical byte representation of a Python
object that does not vary with dict ordering, float formatting, or locale.
"""

from __future__ import annotations

import datetime as dt
import decimal
import hashlib
import json
import uuid
from typing import Any


def _default(obj: Any) -> Any:
    if isinstance(obj, decimal.Decimal):
        # Normalised text, not float: 1.50 and 1.5 must hash identically.
        return format(obj.normalize(), "f")
    if isinstance(obj, (dt.datetime, dt.date)):
        return obj.isoformat()
    if isinstance(obj, uuid.UUID):
        return str(obj)
    if isinstance(obj, (bytes, bytearray)):
        return obj.hex()
    if isinstance(obj, set):
        return sorted(obj, key=repr)
    raise TypeError(f"{type(obj).__name__} is not canonically serialisable")


def canonical_json(payload: Any) -> bytes:
    """UTF-8 bytes of `payload` with keys sorted and no insignificant whitespace."""
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=_default,
    ).encode("utf-8")


def sha256_bytes(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def record_hash(payload: Any) -> bytes:
    """SHA-256 of the canonical-ordered payload, as stored in `raw._record_hash`."""
    return sha256_bytes(canonical_json(payload))


def hexdigest(payload: Any) -> str:
    return record_hash(payload).hex()
