"""Drill-through: from a figure in the pack to the file we were sent.

Every figure renders with a link that resolves here (spec section 11). The walk
is mart row -> canonical rows -> raw payloads -> S3 artifact and its SHA-256.
This is the difference between the offer and a Power BI consultant's, so it is a
first-class query path with its own tests, not a debugging convenience.

A `drill_query` token is `<mart table>[|<firm_id>[|<grain key>]]`, produced by
the pack section SQL alongside each figure.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from psycopg2.extensions import connection as PGConnection

from fracture.core import db
from fracture.core.errors import LineageError
from fracture.core.logging import get_logger
from fracture.ingest.lineage import drill_to_canon, drill_to_raw

log = get_logger("pack.drill")

#: How each mart's primary key is assembled from (firm_id, grain_key).
MART_KEY_SHAPE: dict[str, tuple[str, ...]] = {
    "mart.household_aum": ("firm_id", "household_id", "as_of_date"),
    "mart.billed_revenue": ("firm_id", "invoice_id"),
    "mart.unbilled": ("firm_id", "household_id", "period_end"),
}


@dataclass
class Evidence:
    """One raw record and the artifact it came from."""

    raw_table: str
    load_id: uuid.UUID
    sequence: int
    source_id: str
    stream: str
    artifact_uri: str
    artifact_sha256: str
    record_hash: str
    extracted_at: Any
    payload: dict[str, Any] | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "raw_table": self.raw_table,
            "load_id": str(self.load_id),
            "sequence": self.sequence,
            "source_id": self.source_id,
            "stream": self.stream,
            "artifact_uri": self.artifact_uri,
            "artifact_sha256": self.artifact_sha256,
            "record_hash": self.record_hash,
            "extracted_at": self.extracted_at.isoformat() if self.extracted_at else None,
            "payload": self.payload,
        }


@dataclass
class DrillResult:
    drill_query: str
    mart_table: str
    mart_keys: list[str] = field(default_factory=list)
    canon_rows: list[dict[str, Any]] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)

    @property
    def source_count(self) -> int:
        return len({e.source_id for e in self.evidence})

    def as_dict(self) -> dict[str, Any]:
        return {
            "drill_query": self.drill_query,
            "mart_table": self.mart_table,
            "mart_keys": self.mart_keys,
            "canon_rows": self.canon_rows,
            "evidence": [e.as_dict() for e in self.evidence],
            "source_count": self.source_count,
        }


def parse_token(drill_query: str) -> tuple[str, str | None, str | None]:
    parts = drill_query.split("|")
    table = parts[0]
    firm_id = parts[1] if len(parts) > 1 else None
    grain = parts[2] if len(parts) > 2 else None
    return table, firm_id, grain


def resolve(
    conn: PGConnection, drill_query: str, limit: int = 25, evidence_limit: int = 40
) -> DrillResult:
    """Resolve a figure's drill token to the records behind it."""
    table, firm_id, grain = parse_token(drill_query)
    result = DrillResult(drill_query=drill_query, mart_table=table)

    if table not in MART_KEY_SHAPE:
        # Not every figure is backed by a lineaged mart; the assurance section
        # points at operational tables. Say so rather than returning an empty
        # result that reads as "no evidence exists".
        result.canon_rows = [{"note": f"{table} is not a lineaged mart"}]
        return result

    keys = _matching_keys(conn, table, firm_id, grain, limit)
    result.mart_keys = keys
    if not keys:
        return result

    seen_evidence: set[tuple[uuid.UUID, int]] = set()
    for key in keys:
        for canon_edge in drill_to_canon(conn, table, key):
            row = _canon_row(conn, canon_edge["source_table"], canon_edge["source_pk"])
            if row is not None:
                result.canon_rows.append(
                    {
                        "canon_table": canon_edge["source_table"],
                        "canon_pk": canon_edge["source_pk"],
                        "contribution": canon_edge["contribution"],
                        **row,
                    }
                )
            for raw in drill_to_raw(conn, canon_edge["source_table"], canon_edge["source_pk"]):
                marker = (raw["load_id"], raw["sequence"])
                if marker in seen_evidence:
                    continue
                seen_evidence.add(marker)
                result.evidence.append(
                    Evidence(
                        raw_table=raw["raw_table"],
                        load_id=raw["load_id"],
                        sequence=raw["sequence"],
                        source_id=raw["source_id"],
                        stream=raw["stream"],
                        artifact_uri=raw["artifact_uri"],
                        artifact_sha256=raw["artifact_sha256"],
                        record_hash=raw["record_hash"] or "",
                        extracted_at=raw["extracted_at"],
                        payload=raw["payload"],
                    )
                )
                if len(result.evidence) >= evidence_limit:
                    return result
    return result


def _matching_keys(
    conn: PGConnection, table: str, firm_id: str | None, grain: str | None, limit: int
) -> list[str]:
    clauses = ["target_table = %s"]
    params: list[Any] = [table]
    if firm_id:
        clauses.append("target_pk like %s")
        params.append(f"{firm_id}|%")
    if grain:
        clauses.append("target_pk like %s")
        params.append(f"%|{grain}|%" if not firm_id else f"{firm_id}|{grain}|%")
    rows = db.query(
        conn,
        f"select distinct target_pk from lineage.mart_edge where {' and '.join(clauses)} "
        f"order by target_pk limit {int(limit)}",
        tuple(params),
    )
    return [r["target_pk"] for r in rows]


def _canon_row(conn: PGConnection, canon_table: str, canon_pk: str) -> dict[str, Any] | None:
    schema, table = canon_table.split(".", 1)
    if schema != "canon":
        return None
    return db.query_one(
        conn, f"select * from canon.{table} where canon_id = %s", (int(canon_pk),)
    )


def assert_drillable(conn: PGConnection, pack_run_id: uuid.UUID) -> None:
    """Every figure in a pack that claims a lineaged mart must resolve to raw.

    "Every figure opens to the records behind it" is a promise on the one-pager.
    This is the test of it, run at pack build time.
    """
    figures = db.query(
        conn,
        "select distinct drill_query from pack.figure where pack_run_id = %s "
        "and drill_query is not null",
        (pack_run_id,),
    )
    unresolved: list[str] = []
    for figure in figures:
        token = figure["drill_query"]
        table, _, _ = parse_token(token)
        if table not in MART_KEY_SHAPE:
            continue
        if not resolve(conn, token, limit=1, evidence_limit=1).evidence:
            unresolved.append(token)
    if unresolved:
        raise LineageError(
            f"{len(unresolved)} pack figure(s) cannot be opened to raw records: "
            + ", ".join(unresolved[:5])
        )
