"""Exception hierarchy.

Every failure mode the platform is designed against (spec section 16) raises a
named exception. Nothing in this codebase is allowed to swallow one of these
and continue: silent degradation is the failure we are engineering against.
"""

from __future__ import annotations


class FractureError(Exception):
    """Base for every error this platform raises deliberately."""


class ConfigError(FractureError):
    """Missing or contradictory configuration."""


class TenantIsolationError(FractureError):
    """An operation would have crossed a tenant boundary."""


class AdapterError(FractureError):
    """An adapter misbehaved: bad contract, bad fixture, bad extraction."""


class SchemaDriftError(AdapterError):
    """A source's schema hash changed since the last fingerprint.

    Raised before mapping runs, because a silently dropped column is the
    canonical example of a failure nobody notices until a board pack is wrong.
    """


class MutatingVerbError(AdapterError):
    """Adapter source contains a statement that could write to a source system."""


class ReconciliationBreach(FractureError):
    """Variance against a source-of-truth control report exceeded tolerance."""


class AIBoundaryViolation(FractureError):
    """An AI-derived value reached a numeric column without human confirmation."""


class LineageError(FractureError):
    """A figure could not be traced back to raw records."""


class PackIntegrityError(FractureError):
    """A pack reissued at a frozen system time did not reproduce."""
