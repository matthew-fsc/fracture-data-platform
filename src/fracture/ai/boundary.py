"""The AI boundary (spec section 8).

AI drafts, extracts and summarises. It never computes a number with financial
consequence. Enforcing that with a code review guideline would last about a
quarter, so it is enforced in three places that all have to agree:

1. `ai.proposal` -- nothing an AI produced enters the system except as a
   proposal, with the model, the prompt hash and the input references recorded.
2. `lineage.ai_edge` plus its trigger -- a numeric canonical column cannot be
   populated from an unconfirmed proposal. The database refuses the insert.
3. `assert_no_violations` -- a standing check that runs with the reconciliation
   suite, so a violation introduced by any path at all shows up on the next run.

The permitted uses are real and useful: field mapping proposals during fold-in,
extraction from commission statement PDFs, anomaly triage narrative, pack
commentary. The line is not "no AI"; it is "no AI arithmetic".
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal, Sequence

from psycopg2.extensions import connection as PGConnection
from psycopg2.extras import Json

from fracture.core import db
from fracture.core.errors import AIBoundaryViolation
from fracture.core.hashing import record_hash
from fracture.core.logging import get_logger

log = get_logger("ai.boundary")

ProposalKind = Literal["field_mapping", "extraction", "classification", "narrative", "triage"]

#: Kinds that may produce a value destined for a numeric column at all. Anything
#: else reaching a numeric column is a bug regardless of confirmation.
NUMERIC_CAPABLE_KINDS: frozenset[str] = frozenset({"extraction"})


@dataclass(frozen=True)
class Proposal:
    proposal_id: uuid.UUID
    kind: str
    model: str
    output: dict[str, Any]
    materiality: Decimal | None
    confirmed_by: str | None
    confirmed_at: Any
    rejected_reason: str | None

    @property
    def is_confirmed(self) -> bool:
        return self.confirmed_by is not None

    @property
    def is_open(self) -> bool:
        return self.confirmed_by is None and self.rejected_reason is None


def record_proposal(
    conn: PGConnection,
    kind: ProposalKind,
    model: str,
    prompt: str,
    input_refs: dict[str, Any],
    output: dict[str, Any],
    materiality: Decimal | float | None = None,
) -> uuid.UUID:
    """Log an AI proposal. Nothing else in the platform accepts AI output.

    `prompt_hash` rather than the prompt: the prompt may contain client data,
    and the audit question is "was this the same prompt", not "what did it say".
    """
    row = db.query_one(
        conn,
        """
        insert into ai.proposal
          (kind, model, prompt_hash, input_refs, output, materiality)
        values (%s,%s,%s,%s,%s,%s)
        returning proposal_id
        """,
        (
            kind, model, record_hash(prompt), Json(input_refs), Json(output),
            Decimal(str(materiality)) if materiality is not None else None,
        ),
    )
    proposal_id = row["proposal_id"]
    log.info("recorded %s proposal %s from %s", kind, proposal_id, model)
    return proposal_id


def confirm(conn: PGConnection, proposal_id: uuid.UUID, confirmed_by: str) -> None:
    """A human takes responsibility for the number.

    `confirmed_by` is a person, not a service account. A system confirming its
    own proposals is the boundary with extra steps.
    """
    if not confirmed_by or confirmed_by.startswith("system:"):
        raise AIBoundaryViolation(
            f"confirmation must name a person, got {confirmed_by!r}; "
            "a service account cannot confirm an AI proposal"
        )
    updated = db.execute(
        conn,
        """
        update ai.proposal
           set confirmed_by = %s, confirmed_at = now()
         where proposal_id = %s and rejected_reason is null
        """,
        (confirmed_by, proposal_id),
    )
    if updated == 0:
        raise AIBoundaryViolation(
            f"proposal {proposal_id} does not exist or was already rejected"
        )


def reject(conn: PGConnection, proposal_id: uuid.UUID, reason: str) -> None:
    db.execute(
        conn,
        """
        update ai.proposal set rejected_reason = %s
         where proposal_id = %s and confirmed_by is null
        """,
        (reason, proposal_id),
    )


def get(conn: PGConnection, proposal_id: uuid.UUID) -> Proposal | None:
    row = db.query_one(
        conn, "select * from ai.proposal where proposal_id = %s", (proposal_id,)
    )
    if row is None:
        return None
    return Proposal(
        proposal_id=row["proposal_id"],
        kind=row["kind"],
        model=row["model"],
        output=row["output"],
        materiality=row["materiality"],
        confirmed_by=row["confirmed_by"],
        confirmed_at=row["confirmed_at"],
        rejected_reason=row["rejected_reason"],
    )


def pending(conn: PGConnection, kind: str | None = None) -> list[dict[str, Any]]:
    """Proposals waiting on a human. The fold-in review queue."""
    sql = (
        "select proposal_id, kind, model, output, materiality, created_at "
        "from ai.proposal where confirmed_by is null and rejected_reason is null"
    )
    params: tuple[Any, ...] = ()
    if kind:
        sql += " and kind = %s"
        params = (kind,)
    return db.query(conn, sql + " order by created_at", params)


def set_materiality_threshold(conn: PGConnection, kind: str, threshold: Decimal | float) -> None:
    """Per-tenant threshold (spec 8): below it a transcription may flow, at or
    above it a human confirms."""
    db.execute(
        conn,
        """
        insert into ai.policy (kind, materiality_threshold, updated_at)
        values (%s, %s, now())
        on conflict (kind) do update
          set materiality_threshold = excluded.materiality_threshold, updated_at = now()
        """,
        (kind, Decimal(str(threshold))),
    )


def violations(conn: PGConnection) -> list[dict[str, Any]]:
    return db.query(conn, "select * from ai.boundary_violation")


def assert_no_violations(conn: PGConnection) -> None:
    """Standing check. Runs with the reconciliation suite on every refresh."""
    found = violations(conn)
    if found:
        detail = ", ".join(
            f"{v['target_table']}.{v['target_column']} <- {v['proposal_id']}" for v in found[:5]
        )
        raise AIBoundaryViolation(
            f"{len(found)} numeric column(s) populated from unconfirmed AI proposals: {detail}"
        )


def attach(
    conn: PGConnection,
    proposal_id: uuid.UUID,
    target_table: str,
    target_pk: str,
    columns: Sequence[tuple[str, bool]],
) -> None:
    """Link a proposal to the row it produced.

    The database trigger runs on this insert, so an unconfirmed proposal aimed
    at a numeric column fails here rather than being discovered later.
    """
    db.execute_values(
        conn,
        "insert into lineage.ai_edge "
        "(target_table, target_pk, target_column, proposal_id, is_numeric) values %s",
        [(target_table, target_pk, column, proposal_id, is_numeric)
         for column, is_numeric in columns],
    )
