"""Assembling everything a rendered pack needs, in one pass.

Kept separate from rendering so the same payload can drive the HTML pack, a PDF,
and the drill-through API without three different sets of queries drifting apart.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from psycopg2.extensions import connection as PGConnection

from fracture.control.models import Tenant
from fracture.core import db
from fracture.pack.build import figures_for
from fracture.pack.drill import resolve


def _f(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


@dataclass
class PackData:
    tenant_slug: str
    tenant_name: str
    motion: str
    period_start: dt.date
    period_end: dt.date
    system_time: dt.datetime
    content_hash: str
    pack_run_id: str
    figure_count: int
    firms: list[dict[str, Any]] = field(default_factory=list)
    figures: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    section_titles: dict[str, str] = field(default_factory=dict)
    aum_series: list[dict[str, Any]] = field(default_factory=list)
    leakage_by_firm: list[dict[str, Any]] = field(default_factory=list)
    concentration: list[dict[str, Any]] = field(default_factory=list)
    sla: list[dict[str, Any]] = field(default_factory=list)
    firm_table: list[dict[str, Any]] = field(default_factory=list)
    findings: list[dict[str, Any]] = field(default_factory=list)
    ageing: list[dict[str, Any]] = field(default_factory=list)
    recon: list[dict[str, Any]] = field(default_factory=list)
    provenance: list[dict[str, Any]] = field(default_factory=list)
    drill_example: dict[str, Any] | None = None
    coverage: dict[str, Any] = field(default_factory=dict)

    def metric(self, section: str, name: str, firm_id: str | None = None) -> float | None:
        for row in self.figures.get(section, []):
            if row["metric"] == name and (firm_id is None or row["firm_id"] == firm_id):
                return _f(row["numeric_value"])
        return None


def collect(
    conn: PGConnection,
    tenant: Tenant,
    pack_run_id: uuid.UUID,
    firms: list[dict[str, Any]],
    coverage: dict[str, Any] | None = None,
) -> PackData:
    manifest = db.query_one(
        conn, "select * from pack.manifest where pack_run_id = %s", (pack_run_id,)
    )
    if manifest is None:
        raise ValueError(f"no manifest for pack run {pack_run_id}")

    rows = figures_for(conn, pack_run_id)
    figures: dict[str, list[dict[str, Any]]] = {}
    titles: dict[str, str] = {}
    for row in rows:
        figures.setdefault(row["section"], []).append(row)
        titles[row["section"]] = row["section_title"] or row["section"]

    period_end = manifest["period_end"]
    firm_names = {f["firm_id"]: f["legal_name"] for f in firms}
    firm_roles = {f["firm_id"]: f["role"] for f in firms}

    data = PackData(
        tenant_slug=tenant.slug,
        tenant_name=tenant.legal_name,
        motion=tenant.motion,
        period_start=manifest["period_start"],
        period_end=period_end,
        system_time=manifest["system_time"],
        content_hash=bytes(manifest["content_hash"]).hex(),
        pack_run_id=str(pack_run_id),
        figure_count=manifest["figure_count"],
        firms=firms,
        figures=figures,
        section_titles=titles,
        coverage=coverage or {},
    )

    data.aum_series = [
        {
            "firm_id": r["firm_id"],
            "firm_name": firm_names.get(r["firm_id"], r["firm_id"]),
            "period_end": r["period_end"].isoformat(),
            "total_aum": _f(r["total_aum"]),
            "billable_aum": _f(r["billable_aum"]),
        }
        for r in db.query(
            conn,
            "select firm_id, period_end, total_aum, billable_aum from mart.firm_month "
            "order by period_end, firm_id",
        )
    ]

    data.leakage_by_firm = [
        {
            "firm_id": r["firm_id"],
            "firm_name": firm_names.get(r["firm_id"], r["firm_id"]),
            "leakage_type": r["leakage_type"],
            "amount": _f(r["amount"]),
            "item_count": r["item_count"],
        }
        for r in db.query(
            conn,
            "select firm_id, leakage_type, sum(amount) as amount, sum(item_count) as item_count "
            "from mart.leakage where period_end = %s group by 1,2 order by 1,2",
            (period_end,),
        )
    ]

    data.concentration = [
        {
            "firm_id": r["firm_id"],
            "firm_name": firm_names.get(r["firm_id"], r["firm_id"]),
            "producer_id": r["producer_id"],
            "producer_name": r["producer_name"] or r["producer_id"],
            "book_value": _f(r["book_value"]),
            "book_share": _f(r["book_share"]),
            "household_count": r["household_count"],
            "has_departed": r["has_departed"],
            "book_rank": r["book_rank"],
        }
        for r in db.query(
            conn,
            "select * from mart.concentration where book_rank <= 6 "
            "order by firm_id, book_rank",
        )
    ]

    data.sla = [
        {
            "firm_id": r["firm_id"],
            "firm_name": firm_names.get(r["firm_id"], r["firm_id"]),
            "event_type": r["event_type"],
            "event_count": r["event_count"],
            "breach_count": r["breach_count"],
            "still_open_count": r["still_open_count"],
            "breach_rate": _f(r["breach_rate"]),
            "p90_elapsed_hours": _f(r["p90_elapsed_hours"]),
        }
        for r in db.query(
            conn,
            """
            select firm_id, event_type, sum(event_count) as event_count,
                   sum(breach_count) as breach_count, sum(still_open_count) as still_open_count,
                   round(sum(breach_count)::numeric / nullif(sum(event_count),0), 6) as breach_rate,
                   max(p90_elapsed_hours) as p90_elapsed_hours
              from mart.sla_summary group by 1,2 order by 1,2
            """,
        )
    ]

    data.firm_table = [
        {
            "firm_id": r["firm_id"],
            "firm_name": firm_names.get(r["firm_id"], r["firm_id"]),
            "role": firm_roles.get(r["firm_id"], "addon"),
            "total_aum": _f(r["total_aum"]),
            "billable_aum": _f(r["billable_aum"]),
            "household_count": r["household_count"],
            "expected_amount": _f(r["expected_amount"]),
            "billed_amount": _f(r["billed_amount"]),
            "collected_amount": _f(r["collected_amount"]),
            "outstanding_amount": _f(r["outstanding_amount"]),
            "total_leakage": _f(r["total_leakage"]),
            "loaded_margin": _f(r["loaded_margin"]),
            "loaded_margin_pct": _f(r["loaded_margin_pct"]),
            "leakage_rate": _f(r["leakage_rate"]),
        }
        for r in db.query(
            conn,
            "select * from mart.firm_month where period_end = %s order by firm_id",
            (period_end,),
        )
    ]

    data.findings = [
        {
            "firm_id": r["firm_id"],
            "firm_name": firm_names.get(r["firm_id"], r["firm_id"]),
            "household_id": r["household_id"],
            "household_name": r["household_name"] or r["household_id"],
            "finding": r["finding"],
            "expected_amount": _f(r["expected_amount"]),
            "billed_amount": _f(r["billed_amount"]),
            "variance_amount": _f(r["variance_amount"]),
            "schedule_name": r["schedule_name"],
            "drill_query": f"mart.unbilled|{r['firm_id']}|{r['household_id']}",
        }
        for r in db.query(
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
               and u.finding in ('never_invoiced','billed_below_schedule','billed_above_schedule')
             order by abs(u.variance_amount) desc
             limit 12
            """,
            (period_end,),
        )
    ]

    data.ageing = [
        {"bucket": r["ageing_bucket"], "amount": _f(r["amount"]), "invoices": r["invoices"]}
        for r in db.query(
            conn,
            """
            select ageing_bucket, sum(outstanding_amount) as amount, count(*) as invoices
              from mart.receivables_ageing group by 1
             order by case ageing_bucket
               when 'current' then 1 when '1_30' then 2 when '31_60' then 3
               when '61_90' then 4 else 5 end
            """,
        )
    ]

    data.recon = [
        {
            "check_name": r["check_name"],
            "firm_id": r["firm_id"],
            "period_end": r["period_end"].isoformat(),
            "expected": _f(r["expected"]),
            "actual": _f(r["actual"]),
            "variance_pct": _f(r["variance_pct"]),
            "tolerance_pct": _f(r["tolerance_pct"]),
            "passed": r["passed"],
        }
        for r in db.query(
            conn,
            "select * from recon.latest_result order by passed, check_name, firm_id, period_end desc",
        )
    ]

    data.provenance = [
        {
            "source_id": r["source_id"],
            "stream": r["stream"],
            "loads": r["loads"],
            "rows": r["rows"],
            "last_extracted_at": r["last_extracted_at"].isoformat() if r["last_extracted_at"] else None,
        }
        for r in db.query(
            conn,
            """
            select source_id, stream, count(*) as loads, sum(row_count) as rows,
                   max(extracted_at) as last_extracted_at
              from raw._load group by 1,2 order by 1,2
            """,
        )
    ]

    # A worked drill-through for the largest finding: the pack's proof that the
    # figures open, shown rather than claimed.
    if data.findings:
        top = data.findings[0]
        drill = resolve(conn, top["drill_query"], limit=1, evidence_limit=4)
        data.drill_example = {
            "finding": top,
            "canon_rows": [
                {
                    "canon_table": r.get("canon_table"),
                    "canon_pk": r.get("canon_pk"),
                    "source_id": r.get("source_id"),
                    "recorded_at": r["recorded_at"].isoformat() if r.get("recorded_at") else None,
                }
                for r in drill.canon_rows[:4]
            ],
            "evidence": [e.as_dict() for e in drill.evidence[:3]],
            "source_count": drill.source_count,
        }
    return data
