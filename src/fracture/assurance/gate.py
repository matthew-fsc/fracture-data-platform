"""Recording a gate run against the control plane.

A passing CI job proves the tree was clean. It does not prove a person decided
to ship it, and the difference is the whole content of "human decision gates".
So a gate run becomes a row: the revision, both hashes, the verdict, who wrote
the change, whether it was an agent, and which human approved it.

The rules that matter are database constraints, not code here: an
agent-authored change needs a named human approver, the approver cannot be the
author, and a failing gate can never be approved. That mirrors the data-side
boundary, where a service account cannot confirm its own proposal.
"""

from __future__ import annotations

import datetime as dt
import json
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from fracture.assurance.engine import AssuranceReport
from fracture.core import db
from fracture.core.errors import FractureError
from fracture.core.logging import get_logger
from fracture.control.registry import ControlPlane

log = get_logger("assurance.gate")

AuthorKind = Literal["human", "agent"]


class GateRecordError(FractureError):
    """The gate record was refused. The message is the constraint that refused it."""


@dataclass(frozen=True)
class GateRecord:
    change_gate_id: uuid.UUID
    revision: str | None
    branch: str | None
    verdict: str
    authored_by: str
    author_kind: str
    approved_by: str | None
    approved_at: dt.datetime | None
    violation_count: int

    @property
    def shippable(self) -> bool:
        return self.verdict == "pass" and self.approved_by is not None

    def __str__(self) -> str:
        who = f"{self.authored_by} ({self.author_kind})"
        approval = (
            f"approved by {self.approved_by}" if self.approved_by else "UNAPPROVED"
        )
        return (
            f"gate {str(self.change_gate_id)[:8]} {self.verdict} on "
            f"{(self.revision or 'dirty tree')[:12]}: {who}, {approval}, "
            f"{self.violation_count} violation(s)"
        )


def _branch(root: Path) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=10, check=False,
        )
    except (OSError, subprocess.SubprocessError):  # pragma: no cover
        return None
    name = out.stdout.strip()
    return name or None


def record_gate(
    control: ControlPlane,
    report: AssuranceReport,
    authored_by: str,
    author_kind: AuthorKind = "agent",
    approved_by: str | None = None,
    approved_at: dt.datetime | None = None,
    summary: str = "",
) -> GateRecord:
    """Write one gate run. Raises if the control plane refuses it.

    Approval is a separate argument from the report on purpose: the common case
    is to record the run unapproved at the moment the agent finishes, then have
    a human approve it, which is a second write and a second decision.
    """
    if approved_by is not None and approved_at is None:
        approved_at = dt.datetime.now(dt.timezone.utc)

    import psycopg2

    with control.connection() as conn:
        try:
            row = db.query_one(
                conn,
                """
                insert into control.change_gate
                  (revision, branch, tree_hash, report_hash, policy_count,
                   violation_count, verdict, authored_by, author_kind,
                   approved_by, approved_at, summary, report)
                values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                returning change_gate_id, revision, branch, verdict, authored_by,
                          author_kind, approved_by, approved_at, violation_count
                """,
                (
                    report.revision,
                    _branch(Path(report.root)),
                    report.tree_hash,
                    report.content_hash,
                    len(report.results),
                    len(report.violations),
                    "pass" if report.passed else "fail",
                    authored_by,
                    author_kind,
                    approved_by,
                    approved_at,
                    summary,
                    json.dumps(report.as_dict(), sort_keys=True),
                ),
            )
        except psycopg2.errors.CheckViolation as exc:
            # The constraint name is the explanation, so surface it rather than
            # wrapping it in prose that is one refactor from being wrong.
            raise GateRecordError(str(exc).strip()) from exc

    assert row is not None
    record = _record_from_row(row)
    log.info("%s", record)
    return record


def approve_gate(
    control: ControlPlane,
    change_gate_id: uuid.UUID | str,
    approved_by: str,
    approved_at: dt.datetime | None = None,
) -> GateRecord:
    """A human signs off a recorded run.

    The database refuses self-approval and refuses approving a failed gate, so
    this does not re-check either: a second check in Python that can disagree
    with the first is worse than no second check.
    """
    import psycopg2

    with control.connection() as conn:
        try:
            row = db.query_one(
                conn,
                """
                update control.change_gate
                   set approved_by = %s,
                       approved_at = coalesce(%s, now())
                 where change_gate_id = %s
                returning change_gate_id, revision, branch, verdict, authored_by,
                          author_kind, approved_by, approved_at, violation_count
                """,
                (approved_by, approved_at, str(change_gate_id)),
            )
        except psycopg2.errors.CheckViolation as exc:
            raise GateRecordError(str(exc).strip()) from exc

    if row is None:
        raise GateRecordError(f"no gate run {change_gate_id}")
    record = _record_from_row(row)
    log.info("approved: %s", record)
    return record


def list_gates(control: ControlPlane, limit: int = 20) -> list[GateRecord]:
    with control.connection() as conn:
        rows = db.query(
            conn,
            """
            select change_gate_id, revision, branch, verdict, authored_by,
                   author_kind, approved_by, approved_at, violation_count
              from control.change_gate
             order by created_at desc
             limit %s
            """,
            (limit,),
        )
    return [_record_from_row(r) for r in rows]


def _record_from_row(row: dict[str, Any]) -> GateRecord:
    return GateRecord(
        change_gate_id=row["change_gate_id"],
        revision=row["revision"],
        branch=row["branch"],
        verdict=row["verdict"],
        authored_by=row["authored_by"],
        author_kind=row["author_kind"],
        approved_by=row["approved_by"],
        approved_at=row["approved_at"],
        violation_count=row["violation_count"],
    )
