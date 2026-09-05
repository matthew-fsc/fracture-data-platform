"""AI boundary: proposals, confirmation, and the checks that enforce the line."""

from fracture.ai.boundary import (
    NUMERIC_CAPABLE_KINDS,
    Proposal,
    assert_no_violations,
    attach,
    confirm,
    get,
    pending,
    record_proposal,
    reject,
    set_materiality_threshold,
    violations,
)

__all__ = [
    "record_proposal", "confirm", "reject", "get", "pending", "attach",
    "violations", "assert_no_violations", "set_materiality_threshold",
    "Proposal", "NUMERIC_CAPABLE_KINDS",
]
