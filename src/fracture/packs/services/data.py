"""Everything the services console reads, collected in one pass.

One pass because the console is read on a laptop over a VPN and twelve
round-trips is the difference between a page people use and a page people
screenshot once.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

from psycopg2.extensions import connection as PGConnection

from fracture.control.models import Tenant
from fracture.core import db


@dataclass
class ServicesData:
    tenant_slug: str
    tenant_name: str
    period_end: str
    generated_at: str
    firms: list[dict[str, Any]] = field(default_factory=list)
    scorecard: list[dict[str, Any]] = field(default_factory=list)
    scorecard_history: list[dict[str, Any]] = field(default_factory=list)
    bridge: list[dict[str, Any]] = field(default_factory=list)
    writeoff_reasons: list[dict[str, Any]] = field(default_factory=list)
    worst_engagements: list[dict[str, Any]] = field(default_factory=list)
    best_engagements: list[dict[str, Any]] = field(default_factory=list)
    timekeepers: list[dict[str, Any]] = field(default_factory=list)
    by_grade: list[dict[str, Any]] = field(default_factory=list)
    lockup: list[dict[str, Any]] = field(default_factory=list)
    overdue: list[dict[str, Any]] = field(default_factory=list)
    pipeline: list[dict[str, Any]] = field(default_factory=list)
    stages: list[dict[str, Any]] = field(default_factory=list)
    recon: list[dict[str, Any]] = field(default_factory=list)
    coverage: list[dict[str, Any]] = field(default_factory=list)
    assurance: dict[str, Any] = field(default_factory=dict)

    def firm_ids(self) -> list[str]:
        return [f["firm_id"] for f in self.firms]


def _f(value: Any) -> float | None:
    return None if value is None else float(value)


def _rows(conn: PGConnection, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    return [
        {k: (_f(v) if isinstance(v, (int, float)) or hasattr(v, "quantize") else v)
         for k, v in row.items()}
        for row in db.query(conn, sql, params)
    ]


def collect(
    conn: PGConnection, tenant: Tenant, firms: list[dict[str, Any]]
) -> ServicesData:
    period_end = db.scalar(conn, "select max(period_end) from mart.firm_scorecard")
    if period_end is None:
        raise RuntimeError(
            "mart.firm_scorecard is empty; build the marts before the console"
        )

    data = ServicesData(
        tenant_slug=tenant.slug,
        tenant_name=tenant.legal_name,
        period_end=str(period_end),
        generated_at=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
    )

    data.scorecard = _rows(
        conn,
        "select * from mart.firm_scorecard where period_end = %s order by firm_id",
        (period_end,),
    )
    data.scorecard_history = _rows(
        conn,
        "select * from mart.firm_scorecard order by firm_id, period_end",
    )
    # The practice is whichever firm has timekeepers; clients are the rest.
    practice_ids = {r["firm_id"] for r in data.scorecard}
    data.firms = [f for f in firms if f["firm_id"] in practice_ids] or firms

    data.bridge = _rows(
        conn,
        "select * from mart.realisation_bridge where period_end = %s "
        "order by firm_id, step_order",
        (period_end,),
    )

    data.writeoff_reasons = _rows(
        conn,
        """
        select e.firm_id,
               coalesce(e.writeoff_reason, 'unspecified') as reason,
               sum(e.hours - e.billable_hours)            as hours,
               count(*)                                   as entries
          from canon.time_entry e
         where e.superseded_at is null
           and e.billable_hours < e.hours
         group by 1, 2
         order by e.firm_id, hours desc, reason
        """,
    )

    data.worst_engagements = _rows(
        conn,
        """
        select m.firm_id, m.engagement_id, p.engagement_name, p.client_name,
               m.fee_basis, m.recorded_hours, m.billed_amount, m.total_cost,
               m.margin_amount, m.margin_pct, m.effective_rate, m.entitled_rate
          from mart.engagement_margin m
          left join mart.pipeline p
            on p.firm_id = m.firm_id and p.engagement_id = m.engagement_id
         where m.period_end = %s
         order by m.margin_pct asc nulls last, m.firm_id, m.engagement_id
         limit 10
        """,
        (period_end,),
    )
    data.best_engagements = _rows(
        conn,
        """
        select m.firm_id, m.engagement_id, p.engagement_name, p.client_name,
               m.fee_basis, m.recorded_hours, m.billed_amount, m.margin_pct,
               m.effective_rate
          from mart.engagement_margin m
          left join mart.pipeline p
            on p.firm_id = m.firm_id and p.engagement_id = m.engagement_id
         where m.period_end = %s
         order by m.margin_pct desc nulls last, m.firm_id, m.engagement_id
         limit 6
        """,
        (period_end,),
    )

    data.timekeepers = _rows(
        conn,
        """
        select firm_id, timekeeper_id, grade, recorded_hours, billable_hours,
               target_hours, billable_utilisation, entry_realisation,
               realised_per_hour, cost_rate, yield_multiple, engagement_count
          from mart.utilisation
         where period_end = %s
         order by firm_id, yield_multiple asc nulls last, timekeeper_id
        """,
        (period_end,),
    )
    data.by_grade = _rows(
        conn,
        """
        select firm_id, grade,
               count(*)                                        as people,
               sum(recorded_hours)                             as recorded_hours,
               sum(billable_hours)                             as billable_hours,
               round(sum(billable_hours) / nullif(sum(target_hours), 0), 4)
                                                               as utilisation,
               round(sum(billable_hours) / nullif(sum(recorded_hours), 0), 4)
                                                               as entry_realisation,
               round(sum(realised_amount) / nullif(sum(billable_hours), 0), 2)
                                                               as realised_per_hour,
               round(sum(cost_rate * billable_hours) / nullif(sum(billable_hours), 0), 2)
                                                               as cost_per_hour
          from mart.utilisation
         where period_end = %s
         group by 1, 2
         order by firm_id, realised_per_hour desc nulls last, grade
        """,
        (period_end,),
    )

    data.lockup = _rows(
        conn,
        """
        select l.firm_id, l.engagement_id, p.engagement_name, p.client_name,
               l.entitled_amount, l.billed_amount, l.wip_amount, l.debtor_amount,
               l.wip_days, l.debtor_days, l.overdue_amount, l.overdue_invoices
          from mart.lockup l
          left join mart.pipeline p
            on p.firm_id = l.firm_id and p.engagement_id = l.engagement_id
         where l.period_end = %s
         order by (coalesce(l.wip_amount,0) + coalesce(l.debtor_amount,0)) desc,
                  l.firm_id, l.engagement_id
         limit 12
        """,
        (period_end,),
    )
    data.overdue = _rows(
        conn,
        """
        select firm_id,
               sum(debtor_amount)   as debtor_amount,
               sum(overdue_amount)  as overdue_amount,
               sum(wip_amount)      as wip_amount,
               sum(overdue_invoices) as overdue_invoices
          from mart.lockup where period_end = %s group by 1 order by 1
        """,
        (period_end,),
    )

    data.pipeline = _rows(
        conn,
        """
        select firm_id, engagement_id, engagement_name, client_name, industry,
               fee_basis, stage, agreed_fee, billed_to_date, fee_remaining,
               weighted_fee, days_past_due, due_on
          from mart.pipeline
         order by firm_id, weighted_fee desc nulls last, engagement_id
        """,
    )
    data.stages = _rows(
        conn,
        """
        select firm_id, stage,
               count(*)                as engagements,
               sum(agreed_fee)         as agreed_fee,
               sum(weighted_fee)       as weighted_fee,
               sum(billed_to_date)     as billed_to_date
          from mart.pipeline group by 1, 2 order by firm_id, stage
        """,
    )

    data.recon = _rows(
        conn,
        "select * from recon.latest_result "
        "order by passed, check_name, firm_id, period_end desc",
    )
    data.coverage = _rows(
        conn,
        """
        select source_id, count(*) as loads, max(loaded_at) as last_load
          from raw._load group by 1 order by 1
        """,
    )

    lineage_rows = db.scalar(conn, "select count(*) from lineage.mart_edge") or 0
    canon_rows = db.scalar(
        conn, "select count(*) from canon.time_entry where superseded_at is null"
    ) or 0
    data.assurance = {
        "lineage_edges": int(lineage_rows),
        "open_time_entries": int(canon_rows),
        "recon_total": len(data.recon),
        "recon_failed": sum(1 for r in data.recon if not r.get("passed")),
    }
    return data
