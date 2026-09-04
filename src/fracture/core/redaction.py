"""PII redaction for logs and error messages.

Adapters handle taxpayer identifiers, dates of birth, and custodial account
numbers. None of it may reach a log line, an exception message, or a Dagster
event. Redaction is applied by key name and by value pattern, because a source
that names its SSN column `field_17` still emits something that looks like an
SSN.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Iterable

MASK = "[REDACTED]"

#: Substrings that make a key sensitive regardless of the value it carries.
SENSITIVE_KEY_PARTS: tuple[str, ...] = (
    "ssn",
    "social_security",
    "tax_id",
    "tin",
    "ein",
    "dob",
    "date_of_birth",
    "birth_date",
    "account_number",
    "acct_no",
    "acctnum",
    "routing",
    "card",
    "passport",
    "drivers_license",
    "license_number",
    "email",
    "phone",
    "mobile",
    "street",
    "address_line",
    "postal",
    "zip",
    "password",
    "secret",
    "token",
    "api_key",
    "apikey",
    "credential",
    "authorization",
    "private_key",
)

#: Value shapes that are sensitive wherever they appear.
VALUE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("ein", re.compile(r"\b\d{2}-\d{7}\b")),
    ("email", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")),
    ("phone", re.compile(r"\b(?:\+1[ -]?)?\(?\d{3}\)?[ .-]?\d{3}[ .-]?\d{4}\b")),
    ("card", re.compile(r"\b(?:\d[ -]?){13,19}\b")),
    ("bearer", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{8,}")),
)


class Redactor:
    """Redacts sensitive keys and value shapes from arbitrary nested data."""

    def __init__(
        self,
        sensitive_keys: Iterable[str] = SENSITIVE_KEY_PARTS,
        patterns: Iterable[tuple[str, re.Pattern[str]]] = VALUE_PATTERNS,
        mask: str = MASK,
    ) -> None:
        self._keys = tuple(k.lower() for k in sensitive_keys)
        self._patterns = tuple(patterns)
        self._mask = mask

    def is_sensitive_key(self, key: str) -> bool:
        lowered = key.lower()
        return any(part in lowered for part in self._keys)

    def scrub_text(self, text: str) -> str:
        for _, pattern in self._patterns:
            text = pattern.sub(self._mask, text)
        return text

    def scrub(self, value: Any, _key: str | None = None) -> Any:
        if _key is not None and self.is_sensitive_key(_key):
            return self._mask
        if isinstance(value, dict):
            return {k: self.scrub(v, _key=str(k)) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            scrubbed = [self.scrub(v) for v in value]
            return type(value)(scrubbed) if isinstance(value, tuple) else scrubbed
        if isinstance(value, str):
            return self.scrub_text(value)
        return value

    def contains_pii(self, value: Any) -> bool:
        """True when `value` still holds something the redactor would mask.

        Used by the per-adapter redaction test: capture the adapter's log output
        and assert this returns False.
        """
        if isinstance(value, dict):
            for k, v in value.items():
                if self.is_sensitive_key(str(k)) and v not in (None, "", self._mask):
                    return True
                if self.contains_pii(v):
                    return True
            return False
        if isinstance(value, (list, tuple)):
            return any(self.contains_pii(v) for v in value)
        if isinstance(value, str):
            return any(pattern.search(value) for _, pattern in self._patterns)
        return False


_default = Redactor()


def redact(value: Any) -> Any:
    return _default.scrub(value)


def contains_pii(value: Any) -> bool:
    return _default.contains_pii(value)


class RedactingFilter(logging.Filter):
    """Logging filter that scrubs the formatted message and its args."""

    def __init__(self, redactor: Redactor | None = None) -> None:
        super().__init__()
        self._redactor = redactor or _default

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = self._redactor.scrub_text(record.msg)
        elif record.msg is not None:
            record.msg = self._redactor.scrub(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = self._redactor.scrub(record.args)
            else:
                record.args = tuple(self._redactor.scrub(a) for a in record.args)
        return True
