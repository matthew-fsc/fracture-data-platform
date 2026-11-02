"""Departmental operating views for the professional services pack.

Six views, one per group that owns a set of decisions in a practice. The design
constraint is the wealth pack's, restated: a four-partner practice and a
forty-partner practice cannot be compared on fees, so every figure here is a
rate, a per-hour figure, a day count or a percentage.

The spine is the realisation bridge. Four stages, three gaps, three different
owners. A practice that reports one realisation percentage cannot tell the
partner who approved unbillable work from the partner who under-billed from the
finance team that did not collect, and so it sends the wrong person to fix it.
"""

from __future__ import annotations

from typing import Any, Callable, Sequence

from fracture.pack.charts import (
    SERIES,
    Department,
    compact,
    count,
    e,
    firm_colour,
    grouped_bars,
    hbar_group,
    hours,
    legend,
    lines,
    money,
    pct,
    pp,
    scatter,
    waterfall,
)
from fracture.pack.shell import Chrome
from fracture.packs.services.data import ServicesData, collect

DEPARTMENTS: tuple[Department, ...] = (
    Department(
        "executive", "01", "Executive",
        "Managing partner and the board",
        "Nine measures, each a rate, a per-hour figure or a day count, so practices "
        "of different size sit on the same axis. The league table is ranked on "
        "performance, not on headcount.",
    ),
    Department(
        "realisation", "02", "Realisation",
        "Engagement partners",
        "Five stages from recorded time to banked cash, and the four gaps between "
        "them. Write-off at entry happens in the timesheet and is the gap most "
        "practices never see; pricing, the invoice and the collection are the rest.",
    ),
    Department(
        "people", "03", "People and capacity",
        "Head of people, resourcing",
        "Utilisation against each timekeeper's own target, not a calendar year. Shown "
        "beside entry realisation, because someone at target on recorded hours and "
        "well below on billable is the case utilisation alone cannot see.",
    ),
    Department(
        "cash", "04", "Cash and lockup",
        "Finance",
        "Lockup split into the two legs that have different owners: work done and not "
        "yet invoiced, and invoiced and not yet collected. Reported as one number, "
        "each team points at the other.",
    ),
    Department(
        "pipeline", "05", "Pipeline and delivery",
        "Managing partner, business development",
        "The forward book by stage, weighted on a flat published factor rather than a "
        "fitted one, and the engagements running past their agreed date.",
    ),
    Department(
        "assurance", "06", "Data and assurance",
        "Platform team",
        "Whether the numbers in the other five views can be defended: what reconciled "
        "against each source system's own reports, and how much of the estate is "
        "under lineage.",
    ),
)

STEP_LABEL = {
    "time_value": "Time at card",
    "writeoff_at_entry": "Write-off at entry",
    "entitled": "Entitled",
    "realisation_gap": "Realisation gap",
    "billed_above_entitlement": "Billed above",
    "billed": "Billed",
    "uncollected": "Uncollected",
    "collected": "Collected",
}
SHORT_STEP_LABEL = {
    "Time at card": "Card",
    "Write-off at entry": "Write-off",
    "Realisation gap": "Realisation",
    "Billed above": "Write-on",
    "Uncollected": "Uncoll.",
}
REASON_LABEL = {
    "scope": "Out of scope",
    "goodwill": "Goodwill",
    "training": "Training",
    "admin": "Admin",
    "rework": "Rework",
    "unspecified": "Unspecified",
}


# -- helpers -----------------------------------------------------------------


def _order(data: ServicesData) -> list[str]:
    return [r["firm_id"] for r in data.scorecard]


def _colours(data: ServicesData) -> dict[str, str]:
    order = _order(data)
    return {fid: firm_colour(fid, order) for fid in order}


def _card(data: ServicesData, firm_id: str) -> dict[str, Any]:
    for row in data.scorecard:
        if row["firm_id"] == firm_id:
            return row
    return {}


def _signed_money(value: float) -> str:
    return ("+" if value > 0 else "") + money(value)


def _fixed(value: Any, places: int = 0, suffix: str = "", dash: str = "\u2014") -> str:
    """A plain fixed-point number. A helper rather than an inline f-string
    because Python 3.11 refuses the same quote character inside an f-string
    expression, and the nested-subscript version of this is unreadable anyway."""
    if value is None:
        return dash
    return f"{float(value):.{places}f}{suffix}"


def _bridge_for(data: ServicesData, firm_id: str) -> list[dict[str, Any]]:
    """Map the long-format bridge rows onto the chassis waterfall's shape."""
    steps = [r for r in data.bridge if r["firm_id"] == firm_id]
    out: list[dict[str, Any]] = []
    for row in sorted(steps, key=lambda r: r["step_order"]):
        label = STEP_LABEL.get(row["step"], row["step"])
        amount = float(row["amount"] or 0)
        if row["direction"] in ("total", "subtotal"):
            out.append({"step_kind": "total", "bps": amount, "label": label})
        else:
            out.append(
                {"step_kind": "gain" if row["direction"] == "gain" else "loss",
                 "delta_bps": amount, "label": label}
            )
    return out


def _table(headers: Sequence[str], rows: Sequence[Sequence[str]], caption: str = "") -> str:
    head = "".join(f"<th>{e(h)}</th>" for h in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows
    )
    cap = f"<p class='cap'>{e(caption)}</p>" if caption else ""
    return f"<table class='tw'><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>{cap}"


def _kpi(label: str, value: str, sub: str = "") -> str:
    return (
        f"<div class='kpi'><span class='label'>{e(label)}</span>"
        f"<span class='value num'>{e(value)}</span>"
        + (f"<span class='sub'>{e(sub)}</span>" if sub else "")
        + "</div>"
    )


# -- views -------------------------------------------------------------------


def view_executive(data: ServicesData) -> str:
    colours = _colours(data)
    total_billed = sum(float(r["billed_amount"] or 0) for r in data.scorecard)
    total_hours = sum(float(r["recorded_hours"] or 0) for r in data.scorecard)
    weighted = lambda key: (  # noqa: E731 - a local, used four lines below
        sum(float(r[key] or 0) * float(r["recorded_hours"] or 0) for r in data.scorecard)
        / (total_hours or 1)
    )

    kpis = "".join([
        _kpi("Billed", money(total_billed), f"{count(total_hours)} recorded hours"),
        _kpi("Realised per hour", money(weighted("billed_per_hour"), 2),
             f"entitled {money(weighted('entitled_per_hour'), 2)}"),
        _kpi("Realisation", pct(weighted("realisation_rate")),
             "billed over entitled"),
        _kpi("Write-off at entry", pct(weighted("writeoff_rate")),
             "lost before any invoice"),
        _kpi("Utilisation", pct(weighted("utilisation")), "against own targets"),
        _kpi("Lockup", f"{weighted('lockup_days'):.0f} days",
             f"WIP {weighted('wip_days'):.0f} + debtor {weighted('debtor_days'):.0f}"),
        _kpi("Margin", pct(weighted("margin_pct")), "fully loaded"),
        _kpi("Fee per head", money(weighted("fee_per_head")), "per timekeeper"),
    ])

    peer = (
        sum(float(r["billed_amount"] or 0) for r in data.scorecard)
        / (total_hours or 1)
    )
    yields = hbar_group(
        [
            (r["firm_id"], float(r["billed_per_hour"] or 0), colours[r["firm_id"]])
            for r in data.scorecard
        ],
        peer=peer,
        formatter=lambda v: money(v, 0),
        peer_label="practice",
    ) if data.scorecard else ""

    rows = []
    for r in sorted(data.scorecard,
                    key=lambda r: -(float(r["billed_per_hour"] or 0))):
        rows.append([
            f"<strong>{e(r['firm_id'])}</strong>",
            f"<span class='num'>{e(money(r['billed_per_hour'], 2))}</span>",
            f"<span class='num'>{e(money(r['entitled_per_hour'], 2))}</span>",
            f"<span class='num'>{e(pct(r['realisation_rate']))}</span>",
            f"<span class='num'>{e(pct(r['writeoff_rate']))}</span>",
            f"<span class='num'>{e(pct(r['utilisation']))}</span>",
            f"<span class='num'>{e(_fixed(r['lockup_days'], 0))}</span>",
            f"<span class='num'>{e(pct(r['margin_pct']))}</span>",
            f"<span class='num'>{e(money(r['fee_per_head']))}</span>",
        ])

    return (
        f"<div class='kpis k4'>{kpis}</div>"
        "<div class='grid g2'>"
        f"<div class='panel'><h2>Realised against entitled, per recorded hour</h2>{yields}"
        "<p class='cap'>The bar is what an hour actually billed; the mark is what the "
        "rate card entitled it to. The distance between them is the realisation gap, "
        "and it is a pricing question only when the bar and the mark are both low.</p>"
        "</div>"
        f"<div class='panel'><h2>League table</h2>"
        + _table(
            ["Practice", "Billed/hr", "Entitled/hr", "Realis.", "Write-off",
             "Util.", "Lockup", "Margin", "Fee/head"],
            rows,
            "Ranked on realised rate per recorded hour. Per recorded rather than "
            "billable hour, because an hour written off at entry still consumed "
            "capacity.",
        )
        + "</div></div>"
    )


def view_realisation(data: ServicesData) -> str:
    panels = []
    for r in data.scorecard:
        fid = r["firm_id"]
        bridge = _bridge_for(data, fid)
        chart = waterfall(
            bridge, width=620, height=260, short_labels=True,
            fmt=lambda v: compact(v), delta_fmt=lambda v: ("+" if v > 0 else "") + compact(v),
            short_step=SHORT_STEP_LABEL,
            aria=f"Realisation bridge for {fid}, time at card rate through to cash collected",
        )
        panels.append(
            f"<div class='panel'><h2>{e(fid)} &middot; recorded time to banked cash</h2>"
            f"{chart}"
            "<p class='cap'>Four stages, three gaps. The first gap is written off in "
            "the timesheet and never reaches an invoice; the second is the bill that "
            "went out; the third is the cash that has not. A write-on bar means an "
            "engagement billed above entitlement, which is a refund exposure rather "
            "than a windfall, so it is shown rather than netted off.</p></div>"
        )

    reasons = [r for r in data.writeoff_reasons]
    reason_rows = [
        [
            e(REASON_LABEL.get(r["reason"], r["reason"])),
            f"<span class='num'>{e(r['firm_id'])}</span>",
            f"<span class='num'>{e(count(r['hours']))}</span>",
            f"<span class='num'>{e(count(r['entries']))}</span>",
        ]
        for r in reasons[:12]
    ]

    worst_rows = [
        [
            f"<strong>{e(r.get('engagement_name') or r['engagement_id'])}</strong>"
            f"<br><span class='sub'>{e(r.get('client_name') or '')}</span>",
            f"<span class='tag'>{e(r['fee_basis'].replace('_', ' '))}</span>",
            f"<span class='num'>{e(count(r['recorded_hours']))}</span>",
            f"<span class='num'>{e(money(r['billed_amount']))}</span>",
            f"<span class='num'>{e(money(r['effective_rate'], 2))}</span>",
            f"<span class='num'>{e(money(r['entitled_rate'], 2))}</span>",
            f"<span class='num'>{e(pct(r['margin_pct']))}</span>",
        ]
        for r in data.worst_engagements
    ]

    return (
        "".join(panels)
        + "<div class='grid g2'>"
        + "<div class='panel'><h2>Where billable time went</h2>"
        + _table(
            ["Reason", "Practice", "Hours", "Entries"], reason_rows,
            "A write-off without a recorded reason is a number nobody can act on, so "
            "the canonical model refuses one. Unspecified rows come from sources that "
            "do not carry a reason and are a fold-in gap, not a data-entry failure.",
        )
        + "</div>"
        + "<div class='panel'><h2>Thinnest engagements</h2>"
        + _table(
            ["Engagement", "Basis", "Hours", "Billed", "Eff./hr", "Entitled/hr", "Margin"],
            worst_rows,
            "Effective rate against entitled rate separates an engagement that was "
            "under-billed from one that was priced thin to begin with. A fixed-fee "
            "engagement well below its entitled rate has overrun, not a pricing fault.",
        )
        + "</div></div>"
    )


def view_people(data: ServicesData) -> str:
    colours = _colours(data)
    grades = [r["grade"] for r in data.by_grade]
    grade_chart = grouped_bars(
        grades,
        [(
            "Billable utilisation",
            [float(r["utilisation"] or 0) for r in data.by_grade],
            "var(--series-1)",
        ), (
            "Entry realisation",
            [float(r["entry_realisation"] or 0) for r in data.by_grade],
            "var(--series-2)",
        )],
        formatter=lambda v: pct(v, 0),
    ) if data.by_grade else ""

    points = [
        {
            "x": float(r["billable_utilisation"] or 0),
            "y": float(r["yield_multiple"] or 0),
            "label": r["timekeeper_id"],
            "colour": colours.get(r["firm_id"], SERIES[0]),
        }
        for r in data.timekeepers
        if r["billable_utilisation"] is not None and r["yield_multiple"] is not None
    ]
    capacity = scatter(
        points, "Billable utilisation", "Realised yield over cost",
        x_fmt=lambda v: pct(v, 0), y_fmt=lambda v: f"{(v or 0):.1f}x",
        x_ref=1.0, y_ref=1.0,
    ) if points else ""

    grade_rows = [
        [
            f"<strong>{e(r['grade'].title())}</strong>",
            f"<span class='num'>{e(count(r['people']))}</span>",
            f"<span class='num'>{e(count(r['recorded_hours']))}</span>",
            f"<span class='num'>{e(pct(r['utilisation']))}</span>",
            f"<span class='num'>{e(pct(r['entry_realisation']))}</span>",
            f"<span class='num'>{e(money(r['realised_per_hour'], 2))}</span>",
            f"<span class='num'>{e(money(r['cost_per_hour'], 2))}</span>",
        ]
        for r in data.by_grade
    ]

    worst = [r for r in data.timekeepers if r["yield_multiple"] is not None][:10]
    keeper_rows = [
        [
            f"<strong>{e(r['timekeeper_id'])}</strong>",
            f"<span class='tag'>{e(r['grade'])}</span>",
            f"<span class='num'>{e(count(r['recorded_hours']))}</span>",
            f"<span class='num'>{e(pct(r['billable_utilisation']))}</span>",
            f"<span class='num'>{e(pct(r['entry_realisation']))}</span>",
            f"<span class='num'>{e(money(r['realised_per_hour'], 2))}</span>",
            f"<span class='num'>{e(_fixed(r['yield_multiple'], 2, 'x'))}</span>",
        ]
        for r in worst
    ]

    return (
        "<div class='grid g2'>"
        f"<div class='panel'><h2>Utilisation by grade</h2>{grade_chart}"
        "<p class='cap'>Against each timekeeper's own annual target, apportioned "
        "monthly. A part-time or newly joined person measured against a calendar "
        "year reads as underperforming, which makes the number useless for the "
        "conversation it exists to support.</p></div>"
        f"<div class='panel'><h2>Capacity against yield</h2>{capacity}"
        "<p class='cap'>Horizontal is how full someone is; vertical is what an hour "
        "of their time earns over its fully loaded cost. Below 1.0x an hour loses "
        "money at the rates it was sold at, and being busy makes that worse rather "
        "than better.</p></div></div>"
        "<div class='grid g2'>"
        + "<div class='panel'><h2>By grade</h2>"
        + _table(
            ["Grade", "People", "Hours", "Util.", "Entry realis.", "Realised/hr", "Cost/hr"],
            grade_rows,
            "Entry realisation is the share of recorded hours that survived to "
            "billable. A grade at target on hours and low here is recording work "
            "nobody can bill.",
        )
        + "</div>"
        + "<div class='panel'><h2>Lowest yield per hour</h2>"
        + _table(
            ["Timekeeper", "Grade", "Hours", "Util.", "Entry realis.", "Realised/hr", "Yield"],
            keeper_rows,
            "Yield is realised revenue per billable hour over fully loaded cost per "
            "hour. Loaded cost, not salary: salary alone makes every engagement look "
            "profitable.",
        )
        + "</div></div>"
    )


def view_cash(data: ServicesData) -> str:
    colours = _colours(data)
    split = grouped_bars(
        [r["firm_id"] for r in data.scorecard],
        [(
            "WIP days",
            [float(r["wip_days"] or 0) for r in data.scorecard],
            "var(--series-1)",
        ), (
            "Debtor days",
            [float(r["debtor_days"] or 0) for r in data.scorecard],
            "var(--series-2)",
        )],
        formatter=lambda v: _fixed(v, 0),
    ) if data.scorecard else ""

    kpis = "".join([
        _kpi("WIP", money(sum(float(r["wip_amount"] or 0) for r in data.overdue)),
             "work done, not invoiced"),
        _kpi("Debtors", money(sum(float(r["debtor_amount"] or 0) for r in data.overdue)),
             "invoiced, not collected"),
        _kpi("Overdue", money(sum(float(r["overdue_amount"] or 0) for r in data.overdue)),
             f"{count(sum(float(r['overdue_invoices'] or 0) for r in data.overdue))} invoices"),
    ])

    lock_rows = [
        [
            f"<strong>{e(r.get('engagement_name') or r['engagement_id'])}</strong>"
            f"<br><span class='sub'>{e(r.get('client_name') or '')}</span>",
            f"<span class='num'>{e(money(r['entitled_amount']))}</span>",
            f"<span class='num'>{e(money(r['billed_amount']))}</span>",
            f"<span class='num'>{e(money(r['wip_amount']))}</span>",
            f"<span class='num'>{e(money(r['debtor_amount']))}</span>",
            f"<span class='num'>{e(_fixed(r['wip_days'], 0))}</span>",
            f"<span class='num'>{e(_fixed(r['debtor_days'], 0))}</span>",
            (f"<span class='chip crit'>{e(money(r['overdue_amount']))}</span>"
             if float(r["overdue_amount"] or 0) > 0 else "<span class='chip flat'>—</span>"),
        ]
        for r in data.lockup
    ]

    return (
        f"<div class='kpis k3'>{kpis}</div>"
        "<div class='grid g-wide'>"
        f"<div class='panel'><h2>Lockup, split by owner</h2>{split}"
        f"{legend(['WIP days', 'Debtor days'], ['var(--series-1)', 'var(--series-2)'])}"
        "<p class='cap'>The left bar belongs to whoever is running the engagement and "
        "has not raised the bill. The right belongs to finance. One combined figure "
        "lets each point at the other, which is why it never improves.</p></div>"
        "</div>"
        "<div class='panel'><h2>Largest locked-up engagements</h2>"
        + _table(
            ["Engagement", "Entitled", "Billed", "WIP", "Debtors",
             "WIP days", "Debtor days", "Overdue"],
            lock_rows,
            "Debtor days are weighted by invoice value, not averaged across invoices: "
            "one large old invoice beside twenty small fresh ones is a lockup problem "
            "that an unweighted mean hides. Overdue is measured against each client's "
            "agreed terms, not a firm-wide default.",
        )
        + "</div>"
    )


def view_pipeline(data: ServicesData) -> str:
    colours = _colours(data)
    stage_chart = grouped_bars(
        [r["stage"] for r in data.stages],
        [(
            "Weighted fee",
            [float(r["weighted_fee"] or 0) for r in data.stages],
            "var(--series-1)",
        ), (
            "Billed to date",
            [float(r["billed_to_date"] or 0) for r in data.stages],
            "var(--series-3)",
        )],
        formatter=compact,
    ) if data.stages else ""

    late = [
        r for r in data.pipeline
        if r.get("days_past_due") is not None and float(r["days_past_due"] or 0) > 0
    ]
    late_rows = [
        [
            f"<strong>{e(r.get('engagement_name') or r['engagement_id'])}</strong>"
            f"<br><span class='sub'>{e(r.get('client_name') or '')}</span>",
            f"<span class='tag'>{e(r['stage'])}</span>",
            f"<span class='num'>{e(money(r['agreed_fee']))}</span>",
            f"<span class='num'>{e(money(r['billed_to_date']))}</span>",
            (f"<span class='chip crit'>{e(money(r['fee_remaining']))}</span>"
             if r.get("fee_remaining") is not None and float(r["fee_remaining"]) < 0
             else f"<span class='num'>{e(money(r['fee_remaining']))}</span>"),
            f"<span class='chip crit'>{e(count(r['days_past_due']))} d</span>",
        ]
        for r in sorted(late, key=lambda r: -float(r["days_past_due"] or 0))[:12]
    ]

    stage_rows = [
        [
            f"<strong>{e(r['stage'].title())}</strong>",
            f"<span class='num'>{e(r['firm_id'])}</span>",
            f"<span class='num'>{e(count(r['engagements']))}</span>",
            f"<span class='num'>{e(money(r['agreed_fee']))}</span>",
            f"<span class='num'>{e(money(r['weighted_fee']))}</span>",
            f"<span class='num'>{e(money(r['billed_to_date']))}</span>",
        ]
        for r in data.stages
    ]

    return (
        "<div class='grid g2'>"
        f"<div class='panel'><h2>Weighted book by stage</h2>{stage_chart}"
        "<p class='cap'>The weighting is a flat published factor per stage, stated in "
        "the mart rather than tuned. A factor fitted to past conversion would be a "
        "model producing a figure with financial consequence, which this platform "
        "does not permit.</p></div>"
        "<div class='panel'><h2>By stage</h2>"
        + _table(
            ["Stage", "Practice", "Count", "Agreed fee", "Weighted", "Billed"],
            stage_rows,
        )
        + "</div></div>"
        "<div class='panel'><h2>Running past the agreed date</h2>"
        + (_table(
            ["Engagement", "Stage", "Agreed fee", "Billed", "Fee remaining", "Past due"],
            late_rows,
            "A negative fee remaining means the engagement has billed beyond its "
            "agreed fee. That needs a change order, not a note in the file.",
        ) if late_rows else
           "<p class='note'>No engagement is past its agreed date.</p>")
        + "</div>"
    )


def view_assurance(data: ServicesData) -> str:
    a = data.assurance
    failed = [r for r in data.recon if not r.get("passed")]
    kpis = "".join([
        _kpi("Reconciliation",
             f"{a.get('recon_total', 0) - a.get('recon_failed', 0)}/{a.get('recon_total', 0)}",
             "checks against source totals"),
        _kpi("Lineage edges", count(a.get("lineage_edges")), "mart rows to canonical rows"),
        _kpi("Open time entries", count(a.get("open_time_entries")), "current, not superseded"),
    ])
    recon_rows = [
        [
            f"<strong>{e(r['check_name'])}</strong>",
            f"<span class='num'>{e(r.get('firm_id') or '—')}</span>",
            (f"<span class='chip crit'>failed</span>" if not r.get("passed")
             else "<span class='chip flat'>passed</span>"),
            f"<span class='num'>{e(str(r.get('variance') if r.get('variance') is not None else '—'))}</span>",
        ]
        for r in (failed or data.recon)[:16]
    ]
    coverage_rows = [
        [
            f"<strong>{e(r['source_id'])}</strong>",
            f"<span class='num'>{e(count(r['loads']))}</span>",
            f"<span class='num'>{e(str(r['last_load'])[:19] if r.get('last_load') else '—')}</span>",
        ]
        for r in data.coverage
    ]
    return (
        f"<div class='kpis k3'>{kpis}</div>"
        "<div class='grid g2'>"
        "<div class='panel'><h2>Reconciliation</h2>"
        + _table(["Check", "Practice", "Result", "Variance"], recon_rows,
                 "Failures first. A check that reconciles the platform's own figure "
                 "against the source system's reported total is the only evidence "
                 "that the mapping is right.")
        + "</div>"
        "<div class='panel'><h2>Source coverage</h2>"
        + _table(["Source", "Loads", "Last load"], coverage_rows,
                 "Every canonical row traces to a raw payload and its SHA-256 in the "
                 "artifact store, so the database is rebuildable from object storage "
                 "alone.")
        + "</div></div>"
    )


VIEWS: dict[str, Callable[[ServicesData], str]] = {
    "executive": view_executive,
    "realisation": view_realisation,
    "people": view_people,
    "cash": view_cash,
    "pipeline": view_pipeline,
    "assurance": view_assurance,
}


def chrome(data: ServicesData) -> Chrome:
    subjects = tuple(
        f"{e(r['firm_id'])} &middot; {e(money(r['billed_amount']))}"
        for r in data.scorecard
    )
    return Chrome(
        title=f"{data.tenant_name} Operating Console",
        tenant_name=data.tenant_name,
        period_label="month ending",
        period_end=str(data.period_end),
        generated_at=str(data.generated_at),
        subject_label="practices",
        subjects=subjects,
        rail_note=(
            "Every measure here is a rate, a per-hour figure or a day count, so "
            "practices of different size compare directly."
        ),
        footer=(
            "Rates are per recorded hour unless stated, because an hour written off "
            "at entry still consumed capacity."
        ),
    )


__all__ = ["DEPARTMENTS", "VIEWS", "chrome", "collect", "ServicesData"]
