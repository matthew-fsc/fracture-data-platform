"""Row-grain lineage (spec section 6.2).

Column-level lineage from dbt is not sufficient for "every figure opens to the
records behind it". This is an explicit table, written by the mapping layer and
by mart models, not a metadata by-product. Drill-through is one query against it
joined back to `raw._payload` and `_artifact_uri`.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from psycopg2.extensions import connection as PGConnection

from fracture.core import db
from fracture.core.errors import LineageError
from fracture.core.logging import get_logger

log = get_logger("ingest.lineage")


@dataclass(frozen=True)
class RawRef:
    """A pointer at one row in one raw load."""

    load_id: uuid.UUID
    sequence: int


@dataclass(frozen=True)
class Edge:
    target_table: str
    target_pk: str
    load_id: uuid.UUID
    sequence: int
    contribution: str = "source"


class LineageWriter:
    """Batches lineage edges so mapping does not pay a round trip per row."""

    def __init__(self, conn: PGConnection, batch_size: int = 2000) -> None:
        self.conn = conn
        self.batch_size = batch_size
        self._edges: list[tuple[Any, ...]] = []
        self._mart_edges: list[tuple[Any, ...]] = []

    # -- canon <- raw ------------------------------------------------------

    def record(
        self,
        target_table: str,
        target_pk: Any,
        refs: Iterable[RawRef],
        contribution: str = "source",
    ) -> None:
        for ref in refs:
            self._edges.append(
                (target_table, str(target_pk), ref.load_id, ref.sequence, contribution)
            )
        if len(self._edges) >= self.batch_size:
            self.flush()

    # -- mart <- canon -----------------------------------------------------

    def record_mart(
        self,
        target_table: str,
        target_pk: Any,
        source_table: str,
        source_pks: Iterable[Any],
        contribution: str = "sum",
    ) -> None:
        for pk in source_pks:
            self._mart_edges.append(
                (target_table, str(target_pk), source_table, str(pk), contribution)
            )
        if len(self._mart_edges) >= self.batch_size:
            self.flush()

    def flush(self) -> None:
        if self._edges:
            db.execute_values(
                self.conn,
                "insert into lineage.edge "
                "(target_table, target_pk, load_id, sequence, contribution) values %s",
                self._edges,
            )
            self._edges.clear()
        if self._mart_edges:
            db.execute_values(
                self.conn,
                "insert into lineage.mart_edge "
                "(target_table, target_pk, source_table, source_pk, contribution) values %s",
                self._mart_edges,
            )
            self._mart_edges.clear()

    def __enter__(self) -> "LineageWriter":
        return self

    def __exit__(self, *exc: Any) -> None:
        if exc[0] is None:
            self.flush()


# -- drill-through -----------------------------------------------------------


def drill_to_canon(
    conn: PGConnection, mart_table: str, mart_pk: str
) -> list[dict[str, Any]]:
    """Mart row -> the canonical rows that contributed to it."""
    return db.query(
        conn,
        """
        select source_table, source_pk, contribution
          from lineage.mart_edge
         where target_table = %s and target_pk = %s
         order by source_table, source_pk
        """,
        (mart_table, mart_pk),
    )


def drill_to_raw(
    conn: PGConnection, canon_table: str, canon_pk: str
) -> list[dict[str, Any]]:
    """Canonical row -> the raw rows and the S3 artifacts behind them."""
    edges = db.query(
        conn,
        """
        select e.load_id, e.sequence, e.contribution,
               l.source_id, l.stream, l.artifact_uri, l.artifact_sha256, l.extracted_at
          from lineage.edge e
          join raw._load l on l.load_id = e.load_id
         where e.target_table = %s and e.target_pk = %s
         order by e.load_id, e.sequence
        """,
        (canon_table, canon_pk),
    )
    out: list[dict[str, Any]] = []
    for edge in edges:
        table = f"{edge['source_id']}__{edge['stream']}"
        payload = db.query_one(
            conn,
            f"select _payload, _record_hash from raw.{table} "
            "where _load_id = %s and _sequence = %s",
            (edge["load_id"], edge["sequence"]),
        )
        out.append(
            {
                **edge,
                "raw_table": f"raw.{table}",
                "payload": payload["_payload"] if payload else None,
                "record_hash": bytes(payload["_record_hash"]).hex() if payload else None,
                "artifact_sha256": bytes(edge["artifact_sha256"]).hex(),
            }
        )
    return out


def drill_through(
    conn: PGConnection, mart_table: str, mart_pk: str
) -> dict[str, Any]:
    """The full walk a pack link performs: mart -> canon -> raw -> artifact."""
    canon_rows = drill_to_canon(conn, mart_table, mart_pk)
    detail = []
    for row in canon_rows:
        detail.append(
            {
                "canon_table": row["source_table"],
                "canon_pk": row["source_pk"],
                "contribution": row["contribution"],
                "raw": drill_to_raw(conn, row["source_table"], row["source_pk"]),
            }
        )
    return {"mart_table": mart_table, "mart_pk": mart_pk, "canon": detail}


# -- integrity ---------------------------------------------------------------


def orphan_edges(conn: PGConnection) -> list[dict[str, Any]]:
    """Edges pointing at a load that does not exist. Should always be empty."""
    return db.query(
        conn,
        """
        select e.target_table, e.target_pk, e.load_id
          from lineage.edge e
          left join raw._load l on l.load_id = e.load_id
         where l.load_id is null
         limit 100
        """,
    )


def unlineaged_rows(
    conn: PGConnection, canon_table: str, limit: int = 100
) -> list[dict[str, Any]]:
    """Canonical rows with no edge back to raw.

    A canonical row nobody can trace is the exact failure this platform sells
    against, so the assertion is `= 0`, not `< some tolerance`.
    """
    return db.query(
        conn,
        f"""
        select c.canon_id
          from canon.{canon_table} c
          left join lineage.edge e
                 on e.target_table = %s and e.target_pk = c.canon_id::text
         where e.edge_id is null
         limit {int(limit)}
        """,
        (f"canon.{canon_table}",),
    )


def assert_fully_lineaged(conn: PGConnection, canon_tables: Sequence[str]) -> None:
    problems: dict[str, int] = {}
    for table in canon_tables:
        rows = unlineaged_rows(conn, table, limit=1)
        if rows:
            count = db.scalar(
                conn,
                f"""
                select count(*) from canon.{table} c
                 left join lineage.edge e
                        on e.target_table = %s and e.target_pk = c.canon_id::text
                 where e.edge_id is null
                """,
                (f"canon.{table}",),
            )
            problems[table] = int(count or 0)
    if problems:
        detail = ", ".join(f"canon.{t}: {n} rows" for t, n in problems.items())
        raise LineageError(f"canonical rows with no lineage back to raw -- {detail}")


def lineage_stats(conn: PGConnection) -> dict[str, Any]:
    return {
        "raw_edges": int(db.scalar(conn, "select count(*) from lineage.edge") or 0),
        "mart_edges": int(db.scalar(conn, "select count(*) from lineage.mart_edge") or 0),
        "ai_edges": int(db.scalar(conn, "select count(*) from lineage.ai_edge") or 0),
    }
