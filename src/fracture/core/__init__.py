"""Shared primitives: config, database access, hashing, redaction, time."""

from fracture.core.config import Settings, settings
from fracture.core.errors import (
    FractureError,
    AdapterError,
    ReconciliationBreach,
    AIBoundaryViolation,
    TenantIsolationError,
)
from fracture.core.hashing import canonical_json, record_hash, sha256_bytes
from fracture.core.redaction import Redactor, redact
from fracture.core.timeutil import utcnow, freeze_system_time

__all__ = [
    "Settings",
    "settings",
    "FractureError",
    "AdapterError",
    "ReconciliationBreach",
    "AIBoundaryViolation",
    "TenantIsolationError",
    "canonical_json",
    "record_hash",
    "sha256_bytes",
    "Redactor",
    "redact",
    "utcnow",
    "freeze_system_time",
]
