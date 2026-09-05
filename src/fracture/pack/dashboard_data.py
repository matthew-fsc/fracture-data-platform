"""Everything the departmental dashboards read, collected in one pass.

Separate from `pack.data` because the audience is different. The board pack is
issued to a client, a board or a lender and is pinned to a system time. These
dashboards are the operating console the platform team works from: current
state, comparable across firms, and organised by who acts on it.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass, field
from typing import Any

from psycopg2.extensions import connection as PGConnection

from fracture.control.models import Tenant
from fracture.core import db


def _f(value: Any) -> float | None:
    return None if value is None else float(value)


def _rows(conn: PGConnection, sql: str, params: Any = None) -> list[dict[str, Any]]:
    out = []
    for row in db.query(conn, sql, params):
        clean: dict[str, Any] = {}
        for key, value in row.items():
            if isinstance(value, (dt.date, dt.datetime)):
                clean[key] = value.isoformat()
            elif hasattr(value, "quantize"):
                clean[key] = float(value)
            elif isinstance(value, uuid.UUID):
                clean[key] = str(value)
            else:
                clean[key] = value
        out.append(clean)
    return out


@dataclass
class DashboardData:
    tenant_slug: str
    tenant_name: str
    period_end: str
    generated_at: str
    firms: list[dict[str, Any]] = field(default_factory=list)
    scorecard: list[dict[str, Any]] = field(default_factory=list)
    scorecard_history: list[dict[str, Any]] = field(default_factory=list)
    kpis: list[dict[str, Any]] = field(default_factory=list)
    bridge: list[dict[str, Any]] = field(default_factory=list)
    leakage: list[dict[str, Any]] = field(default_factory=list)
    ageing: list[dict[str, Any]] = field(default_factory=list)
    distribution: list[dict[str, Any]] = field(default_factory=list)
    worst_clients: list[dict[str, Any]] = field(default_factory=list)
    best_clients: list[dict[str, Any]] = field(default_factory=list)
    producers: list[dict[str, Any]] = field(default_factory=list)
    sla: list[dict[str, Any]] = field(default_factory=list)
    open_events: list[dict[str, Any]] = field(default_factory=list)
    recon: list[dict[str, Any]] = field(default_factory=list)
    coverage: list[dict[str, Any]] = field(default_factory=list)
    assurance: dict[str, Any] = field(default_factory=dict)
    findings: list[dict[str, Any]] = field(default_factory=list)

    def firm_ids(self) -> list[str]:
        return [f["firm_id"] for f in self.firms]


def collect(
    conn: PGConnection, tenant: Tenant, firms: list[dict[str, Any]]
) -> DashboardData:
    period_end = db.scalar(conn, "select max(period_end) from mart.firm_scorecard")
    if period_end is None:
        raise ValueError("mart.firm_scorecard is empty; build the marts first")

    firm_meta = {f["firm_id"]: f for f in firms}
    data = DashboardData(
        tenant_slug=tenant.slug,
        tenant_name=tenant.legal_name,
        period_end=period_end.isoformat(),
        generated_at=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
    )

    data.scorecard = _rows(
        conn, "select * from mart.firm_scorecard where period_end = %s order by firm_id",
        (period_end,),
    )
    for row in data.scorecard:
        meta = firm_meta.get(row["firm_id"], {})
        row["firm_name"] = meta.get("legal_name", row["firm_id"])
        row["role"] = meta.get("role", "addon")
        row["close_date"] = meta.get("close_date")

    data.firms = [
        {
            "firm_id": r["firm_id"], "firm_name": r["firm_name"], "role": r["role"],
            "close_date": r["close_date"], "total_aum": r["total_aum"],
        }
        for r in sorted(data.scorecard, key=lambda r: -(r["total_aum"] or 0))
    ]

    data.scorecard_history = _rows(
        conn,
        "select firm_id, period_end, total_aum, actual_yield_bps, schedule_yield_bps, "
        "collected_yield_bps, realization_rate, collection_rate, loaded_margin_pct, "
        "leakage_rate, cost_income_ratio, household_count "
        "from mart.firm_scorecard order by period_end, firm_id",
    )

    data.kpis = _rows(
        conn,
        "select * from mart.firm_kpi order by department, sort_order, firm_id",
    )
    for row in data.kpis:
        row["firm_name"] = firm_meta.get(row["firm_id"], {}).get(
            "legal_name", row["firm_id"]
        )

    data.bridge = _rows(
        conn,
        "select * from mart.yield_bridge where period_end = %s order by firm_id, step_order",
        (period_end,),
    )

    data.leakage = _rows(
        conn,
        "select firm_id, leakage_type, sum(amount) as amount, sum(item_count) as item_count "
        "from mart.leakage where period_end = %s group by 1,2 order by 1,2",
        (period_end,),
    )

    data.ageing = _rows(
        conn,
        """
        select firm_id, ageing_bucket, sum(outstanding_amount) as amount,
               count(*) as invoices
          from mart.receivables_ageing group by 1,2
         order by 1, case ageing_bucket when 'current' then 1 when '1_30' then 2
                       when '31_60' then 3 when '61_90' then 4 else 5 end
        """,
    )

    data.distribution = _rows(conn, "select * from mart.household_distribution order by firm_id")

    data.worst_clients = _rows(
        conn,
        """
        select firm_id, household_id, household_name, segment, aum, billed_amount,
               cost_to_serve, loaded_margin, loaded_margin_pct, actual_yield_bps, finding
          from mart.household_economics
         order by loaded_margin asc limit 12
        """,
    )
    data.best_clients = _rows(
        conn,
        """
        select firm_id, household_id, household_name, segment, aum, billed_amount,
               cost_to_serve, loaded_margin, loaded_margin_pct, actual_yield_bps, finding
          from mart.household_economics
         order by loaded_margin desc limit 12
        """,
    )

    data.producers = _rows(
        conn,
        "select * from mart.producer_scorecard order by firm_id, book_share desc nulls last",
    )

    data.sla = _rows(
        conn,
        """
        select firm_id, event_type, sum(event_count) as event_count,
               sum(breach_count) as breach_count, sum(still_open_count) as still_open,
               round(sum(breach_count)::numeric / nullif(sum(event_count),0), 6) as breach_rate,
               max(p90_elapsed_hours) as p90_hours,
               round(avg(avg_elapsed_hours)::numeric, 1) as avg_hours
          from mart.sla_summary group by 1,2 order by 1,2
        """,
    )

    data.open_events = _rows(
        conn,
        """
        select firm_id, service_event_id, event_type, household_id, actor_producer_id,
               opened_at, sla_target_hours, elapsed_hours
          from mart.service_sla
         where still_open and breached
         order by elapsed_hours desc limit 15
        """,
    )

    data.recon = _rows(
        conn,
        """
        select firm_id, check_name, period_end, expected, actual, variance, variance_pct,
               tolerance_pct, passed
          from recon.latest_result order by passed, abs(coalesce(variance_pct,0)) desc limit 20
        """,
    )

    data.coverage = _rows(
        conn,
        """
        select firm_id, source_id, count(*) as loads, sum(row_count) as rows,
               max(extracted_at) as last_extracted_at
          from raw._load group by 1,2 order by 1,2
        """,
    )

    data.findings = _rows(
        conn,
        """
        select u.firm_id, u.household_id, h.name as household_name, u.finding,
               u.expected_amount, u.billed_amount, u.variance_amount, u.schedule_name
          from mart.unbilled u
          left join (
            select distinct on (firm_id, household_id) firm_id, household_id, name
              from canon.household where superseded_at is null
             order by firm_id, household_id, recorded_at desc
          ) h on h.firm_id = u.firm_id and h.household_id = u.household_id
         where u.period_end = %s
           and u.finding <> 'as_expected'
         order by abs(u.variance_amount) desc limit 20
        """,
        (period_end,),
    )

    data.assurance = {
        "recon_checks": int(db.scalar(conn, "select count(*) from recon.latest_result") or 0),
        "recon_failed": int(
            db.scalar(conn, "select count(*) from recon.latest_result where not passed") or 0
        ),
        "open_variances": int(
            db.scalar(conn, "select count(*) from recon.source_variance where resolved_by is null")
            or 0
        ),
        "unacked_drift": int(
            db.scalar(conn, "select count(*) from recon.schema_drift where acknowledged_by is null")
            or 0
        ),
        "raw_rows": int(db.scalar(conn, "select coalesce(sum(row_count),0) from raw._load") or 0),
        "lineage_edges": int(
            db.scalar(
                conn,
                "select (select count(*) from lineage.edge) + "
                "(select count(*) from lineage.mart_edge)",
            )
            or 0
        ),
        "ai_violations": int(
            db.scalar(conn, "select count(*) from ai.boundary_violation") or 0
        ),
        "canonical_rows": int(
            db.scalar(
                conn,
                "select count(*) from canon.balance_snapshot where superseded_at is null",
            )
            or 0
        ),
    }
    return data
