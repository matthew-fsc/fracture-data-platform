"""Writing canonical records.

Two things happen here that do not happen anywhere else:

1. System-time versioning. A canonical row is never updated in place. When a
   source restates a fact, the existing row's `superseded_at` closes and a new
   row opens. A pack pinned to the earlier system time therefore still
   reproduces the earlier number, which is the whole commercial argument in
   spec 6.3.
2. Lineage. Every row written gets edges back to the raw records it came from.
   A canonical row with no lineage is rejected, not warned about.

Fan-in across sources is governed by `fracture.canon.precedence`, not by
execution order. A lower-authority source that disagrees with the record of
truth does not overwrite it; the disagreement is written to `recon.source_variance`
and becomes a reconciliation finding.

Writes are batched per entity: current rows are loaded once, compared in memory,
and inserted with a single multi-row statement. A per-row round trip is fine at
fixture scale and unusable at the volumes in spec 1.2.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Iterable, Sequence

from psycopg2.extensions import connection as PGConnection
from psycopg2.extras import Json

from fracture.adapters.base import CanonicalRecord
from fracture.core import db
from fracture.core.errors import AIBoundaryViolation, FractureError
from fracture.core.logging import get_logger
from fracture.core.timeutil import utcnow
from fracture.canon import precedence
from fracture.ingest.lineage import LineageWriter

log = get_logger("canon.writer")

#: Canonical entity -> (table, natural-key columns). The natural key is what
#: makes two rows from two different sources the same fact.
ENTITY_KEYS: dict[str, tuple[str, ...]] = {
    "firm": ("firm_id",),
    "party": ("party_id",),
    "household": ("household_id",),
    "household_member": ("household_id", "party_id"),
    "producer": ("producer_id",),
    "producer_crosswalk": ("system", "external_key"),
    "book_assignment": ("producer_id", "household_id", "valid_from"),
    "account": ("account_id",),
    "balance_snapshot": ("account_id", "as_of_date"),
    "policy_term": ("account_id", "term_seq"),
    "fee_schedule": ("schedule_id",),
    "fee_tier": ("schedule_id", "tier_seq"),
    "schedule_assignment": ("scope_type", "scope_id", "schedule_id", "valid_from"),
    "revenue_event": ("revenue_event_id",),
    "invoice": ("invoice_id",),
    "invoice_line": ("invoice_id", "line_no"),
    "cash_receipt": ("receipt_id",),
    "receipt_application": ("receipt_id", "invoice_id"),
    "cost_line": ("cost_id",),
    "fte_allocation": ("person_id", "period_start", "producer_id", "household_id"),
    "service_event": ("service_event_id",),
}

#: Entities that carry business-time validity. The rest are system-time only
#: (a receipt happened; it does not stop being true).
BUSINESS_TIME_ENTITIES: frozenset[str] = frozenset(
    {
        "firm", "party", "household", "household_member", "producer",
        "book_assignment", "account", "balance_snapshot", "policy_term",
        "fee_schedule", "schedule_assignment", "revenue_event", "invoice",
    }
)

#: Numeric columns per entity. Populating one of these from an unconfirmed AI
#: proposal is the forbidden case in spec 8.
NUMERIC_COLUMNS: dict[str, frozenset[str]] = {
    "balance_snapshot": frozenset({"market_value", "cash_value", "billable_value"}),
    "policy_term": frozenset({"premium", "commission_rate"}),
    "fee_tier": frozenset({"lower_bound", "upper_bound", "annual_rate_bps", "flat_amount"}),
    "revenue_event": frozenset({"amount", "basis_amount"}),
    "invoice": frozenset({"total_amount"}),
    "invoice_line": frozenset({"amount"}),
    "cash_receipt": frozenset({"amount"}),
    "receipt_application": frozenset({"amount_applied"}),
    "cost_line": frozenset({"amount"}),
    "fte_allocation": frozenset({"hours", "hourly_cost"}),
    "book_assignment": frozenset({"split_pct"}),
}

DEFAULT_VALID_FROM = dt.date(1900, 1, 1)


@dataclass
class _PendingInsert:
    """One canonical row staged for insert, with every ref that fed it."""

    values: dict[str, Any]
    refs: list[Any]
    contribution: str = "source"
    ai_proposal_id: Any = None


@dataclass
class WriteStats:
    inserted: int = 0
    superseded: int = 0
    unchanged: int = 0
    #: Rows a lower-authority source tried to overwrite and was refused.
    deferred: int = 0
    #: Refusals where the two sources genuinely disagreed on a material column.
    variances: int = 0
    by_entity: dict[str, int] = field(default_factory=dict)

    def record(self, entity: str, n: int = 1) -> None:
        self.by_entity[entity] = self.by_entity.get(entity, 0) + n

    def __str__(self) -> str:
        return (
            f"{self.inserted} inserted, {self.superseded} superseded, "
            f"{self.unchanged} unchanged, {self.deferred} deferred "
            f"({self.variances} variances)"
        )


class CanonWriter:
    """Applies canonical records to the tenant database."""

    def __init__(
        self,
        conn: PGConnection,
        lineage: LineageWriter | None = None,
        system_time: dt.datetime | None = None,
    ) -> None:
        self.conn = conn
        self.lineage = lineage or LineageWriter(conn)
        self.system_time = system_time or utcnow()

    # -- public ------------------------------------------------------------

    def write(self, records: Sequence[CanonicalRecord]) -> WriteStats:
        stats = WriteStats()
        for entity, group in _group_by_entity(records).items():
            self._write_entity(entity, group, stats)
        self.lineage.flush()
        log.info("canon write: %s", stats)
        return stats

    # -- internals ---------------------------------------------------------

    def _write_entity(
        self, entity: str, records: Sequence[CanonicalRecord], stats: WriteStats
    ) -> None:
        if entity not in ENTITY_KEYS:
            raise FractureError(
                f"no canonical table registered for entity {entity!r}; "
                "add it to ENTITY_KEYS or fix the adapter mapping"
            )
        key_cols = ENTITY_KEYS[entity]
        has_business_time = entity in BUSINESS_TIME_ENTITIES
        firm_ids = {r.firm_id for r in records}
        current = self._load_current(entity, key_cols, firm_ids)

        to_supersede: list[int] = []
        to_insert: list[_PendingInsert] = []
        # Natural key -> the pending insert already staged for it. Two records
        # for the same key inside one batch are the same fact seen twice (Orion
        # reports a household once per account), not two rows.
        pending: dict[tuple[Any, ...], _PendingInsert] = {}
        variances: list[tuple[Any, ...]] = []

        for record in records:
            values = dict(record.values)
            values["firm_id"] = record.firm_id
            values["source_id"] = record.source_id
            if has_business_time:
                values.setdefault("valid_from", record.valid_from or DEFAULT_VALID_FROM)
                values.setdefault("valid_to", record.valid_to)

            self._check_ai_boundary(entity, record, values)

            key = (record.firm_id,) + tuple(
                _key_value(values.get(c)) for c in key_cols if c != "firm_id"
            )
            existing = current.get(key)

            staged = pending.get(key)
            if staged is not None:
                staged.values = _merge(staged.values, values)
                staged.refs.extend(record.refs)
                if record.ai_proposal_id is not None:
                    staged.ai_proposal_id = record.ai_proposal_id
                stats.unchanged += 1
                continue

            if existing is None:
                staged = _PendingInsert(
                    values=values,
                    refs=list(record.refs),
                    contribution=record.contribution,
                    ai_proposal_id=record.ai_proposal_id,
                )
                to_insert.append(staged)
                pending[key] = staged
                continue

            existing_source = existing.get("source_id") or "unknown"
            diffs = (
                precedence.material_variance(entity, existing, values)
                if existing_source != record.source_id else []
            )

            if _values_match(existing, values):
                stats.unchanged += 1
                # A second source confirming the same fact is evidence. Dropping
                # it would leave a hole in the drill-through.
                self.lineage.record(
                    f"canon.{entity}", existing["canon_id"], record.refs, "source"
                )
                continue

            if not precedence.wins(entity, record.source_id, existing_source):
                stats.deferred += 1
                self.lineage.record(
                    f"canon.{entity}", existing["canon_id"], record.refs, "source"
                )
                if diffs:
                    stats.variances += 1
                    variances.append(
                        (
                            record.firm_id, entity, str(existing["canon_id"]),
                            existing_source, record.source_id, Json(diffs),
                        )
                    )
                continue

            # The authoritative source wins -- but the disagreement is still a
            # finding. Recording it only when the *loser* arrives second would
            # mean the reconciliation result depended on asset execution order,
            # which is precisely the silent behaviour this design rejects.
            if diffs:
                stats.variances += 1
                variances.append(
                    (
                        record.firm_id, entity, str(existing["canon_id"]),
                        record.source_id, existing_source, Json(diffs),
                    )
                )

            to_supersede.append(existing["canon_id"])
            staged = _PendingInsert(
                values=_merge(existing, values),
                refs=list(record.refs),
                contribution=record.contribution,
                ai_proposal_id=record.ai_proposal_id,
            )
            to_insert.append(staged)
            pending[key] = staged

        if to_supersede:
            db.execute(
                self.conn,
                f"update canon.{entity} set superseded_at = %s "
                "where canon_id = any(%s) and superseded_at is null",
                (self.system_time, to_supersede),
            )
            stats.superseded += len(to_supersede)

        if variances:
            db.execute_values(
                self.conn,
                """
                insert into recon.source_variance
                  (firm_id, entity, canon_id, authoritative_source, deferred_source, detail)
                values %s
                """,
                variances,
            )

        if to_insert:
            self._bulk_insert(entity, to_insert, stats)

    def _load_current(
        self, entity: str, key_cols: Sequence[str], firm_ids: Iterable[str]
    ) -> dict[tuple[Any, ...], dict[str, Any]]:
        """Load every open row for these firms once, keyed by natural key.

        One query per entity per batch instead of one per record. At the volumes
        in spec 1.2 the difference is minutes versus hours.
        """
        firm_list = sorted(firm_ids)
        if not firm_list:
            return {}
        rows = db.query(
            self.conn,
            f"select * from canon.{entity} where firm_id = any(%s) and superseded_at is null",
            (firm_list,),
        )
        out: dict[tuple[Any, ...], dict[str, Any]] = {}
        for row in rows:
            key = (row["firm_id"],) + tuple(
                _key_value(row.get(c)) for c in key_cols if c != "firm_id"
            )
            # Several open rows for one key would mean an earlier supersede was
            # missed; keep the newest and let the integrity check surface it.
            previous = out.get(key)
            if previous is None or row["canon_id"] > previous["canon_id"]:
                out[key] = row
        return out

    def _bulk_insert(
        self, entity: str, batch: Sequence["_PendingInsert"], stats: WriteStats
    ) -> None:
        columns = sorted({c for staged in batch for c in staged.values})
        columns.append("recorded_at")
        rows = [
            tuple(
                staged.values.get(c) if c != "recorded_at" else self.system_time
                for c in columns
            )
            for staged in batch
        ]
        placeholders = ", ".join(columns)
        # execute_values with fetch=True returns the RETURNING rows in input order,
        # which is what lets lineage be attached without a second lookup.
        with self.conn.cursor() as cur:
            from psycopg2.extras import execute_values

            ids = execute_values(
                cur,
                f"insert into canon.{entity} ({placeholders}) values %s returning canon_id",
                rows,
                page_size=len(rows),
                fetch=True,
            )
        stats.inserted += len(rows)
        stats.record(entity, len(rows))
        for staged, (canon_id,) in zip(batch, ids):
            self.lineage.record(
                f"canon.{entity}", canon_id, staged.refs, staged.contribution
            )
            if staged.ai_proposal_id is not None:
                self._record_ai_edges(
                    entity, canon_id, staged.values, staged.ai_proposal_id
                )

    # -- AI boundary -------------------------------------------------------

    def _check_ai_boundary(
        self, entity: str, record: CanonicalRecord, values: dict[str, Any]
    ) -> None:
        """A record carrying an AI proposal may not populate a numeric column
        unless the proposal is confirmed (spec 8).

        The database enforces this too, via the trigger on `lineage.ai_edge`.
        Checking here as well means the caller gets a sentence naming the column
        rather than a constraint violation from three frames down.
        """
        if record.ai_proposal_id is None:
            return
        numeric = NUMERIC_COLUMNS.get(entity, frozenset())
        touched = sorted(c for c in numeric if values.get(c) is not None)
        if not touched:
            return
        proposal = db.query_one(
            self.conn,
            "select confirmed_by, rejected_reason, kind, materiality "
            "from ai.proposal where proposal_id = %s",
            (record.ai_proposal_id,),
        )
        if proposal is None:
            raise AIBoundaryViolation(
                f"canonical record {entity}/{record.natural_key} references proposal "
                f"{record.ai_proposal_id} which does not exist"
            )
        if proposal["rejected_reason"]:
            raise AIBoundaryViolation(
                f"canonical record {entity}/{record.natural_key} references rejected "
                f"proposal {record.ai_proposal_id}: {proposal['rejected_reason']}"
            )
        if proposal["confirmed_by"]:
            return
        threshold = db.scalar(
            self.conn,
            "select materiality_threshold from ai.policy where kind = %s",
            (proposal["kind"],),
        )
        threshold = Decimal(str(threshold if threshold is not None else 0))
        materiality = proposal["materiality"]
        materiality = Decimal(str(materiality)) if materiality is not None else None
        if materiality is not None and materiality < threshold:
            return  # below the per-tenant materiality threshold
        raise AIBoundaryViolation(
            f"unconfirmed AI proposal {record.ai_proposal_id} would populate numeric "
            f"column(s) {', '.join(touched)} on canon.{entity} "
            f"({record.natural_key}); a human must confirm it first"
        )

    def _record_ai_edges(
        self, entity: str, canon_id: int, values: dict[str, Any], proposal_id: Any
    ) -> None:
        numeric = NUMERIC_COLUMNS.get(entity, frozenset())
        rows = [
            (f"canon.{entity}", str(canon_id), column, proposal_id, column in numeric)
            for column, value in values.items()
            if value is not None and column not in ("recorded_at", "superseded_at")
        ]
        if rows:
            db.execute_values(
                self.conn,
                "insert into lineage.ai_edge "
                "(target_table, target_pk, target_column, proposal_id, is_numeric) values %s",
                rows,
            )


def _key_value(value: Any) -> Any:
    """Normalise a natural-key component so a date and its ISO string match."""
    if isinstance(value, dt.datetime):
        return value.isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value.normalize())
    if value is None:
        return None
    return str(value)


def _group_by_entity(
    records: Iterable[CanonicalRecord],
) -> dict[str, list[CanonicalRecord]]:
    grouped: dict[str, list[CanonicalRecord]] = {}
    for record in records:
        grouped.setdefault(record.entity, []).append(record)
    return grouped


#: Columns that belong to the row's bookkeeping, not to the fact it states.
_BOOKKEEPING = frozenset({"canon_id", "recorded_at", "superseded_at", "source_id"})


def _merge(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    """Build the new version: the existing row, overlaid with what the incoming
    record actually populates.

    A null in the incoming record is absence of knowledge, not an assertion of
    emptiness. The Schwab file knows an account's value and nothing about its
    household; letting its nulls through would detach every account from its
    household the moment the custodian feed ran, and household AUM would quietly
    go to zero. No source here emits a tombstone, so there is no case where a
    null should erase.
    """
    merged = {
        column: value
        for column, value in existing.items()
        if column not in _BOOKKEEPING and value is not None
    }
    merged.update({c: v for c, v in incoming.items() if v is not None})
    # valid_to is meaningful when null (an open period), so it is carried
    # explicitly rather than dropped by the not-None filter above.
    if "valid_to" in incoming:
        merged["valid_to"] = incoming["valid_to"]
    elif "valid_to" in existing:
        merged["valid_to"] = existing["valid_to"]
    merged["source_id"] = incoming.get("source_id", existing.get("source_id", "unknown"))
    return merged


def _values_match(existing: dict[str, Any], incoming: dict[str, Any]) -> bool:
    """Compare only the columns the incoming record actually populates.

    A partial source (the custodian file, which knows nothing about households)
    must not supersede a richer row merely by not knowing about a column.
    """
    for column, value in incoming.items():
        if column in _BOOKKEEPING:
            continue
        if value is None:
            continue
        if column not in existing:
            return False
        current = existing[column]
        if isinstance(current, Decimal) or isinstance(value, Decimal):
            if current is None or value is None:
                if current is not value:
                    return False
                continue
            if Decimal(str(current)) != Decimal(str(value)):
                return False
            continue
        if isinstance(current, dt.datetime) and isinstance(value, dt.datetime):
            if current != value:
                return False
            continue
        if current != value:
            return False
    return True
