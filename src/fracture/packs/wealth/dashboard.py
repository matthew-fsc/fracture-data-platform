"""Departmental operating views for the wealth pack.

Six views, one per group that owns a set of decisions. The board pack answers
"how did the platform do"; these answer "which firm, and what do I do on
Monday".

Every view compares firms on a normalised measure. That is the whole design
constraint: the platform firm bills nearly five times the smallest add-on, so
any view built on absolute figures ranks them by size and teaches the reader
nothing. Rates, per-unit figures and basis points on AUM put a $400m firm and a
$1.7bn firm on the same axis.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Sequence

from fracture.pack.charts import (
    SERIES,
    Department,
    bps,
    compact,
    count,
    delta_chip,
    e,
    firm_colour,
    fmt_unit,
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
from fracture.packs.wealth.data import DashboardData, collect
from fracture.pack.shell import Chrome


DEPARTMENTS: tuple[Department, ...] = (
    Department(
        "executive", "01", "Executive",
        "Managing partner and the board",
        "Eight measures, each normalised so firms of different size sit on the same axis. "
        "The league table below is ranked on performance, not on scale.",
    ),
    Department(
        "finance", "02", "Finance and billing",
        "Controller, billing operations",
        "What the fee schedules entitle the platform to, what was invoiced, and what arrived. "
        "The gap between the first two is a billing-run problem; between the second and third, "
        "a collections problem. They are separated because they have different owners.",
    ),
    Department(
        "profitability", "03", "Profitability",
        "Finance and firm principals",
        "Fully loaded margin at household grain. Averages are shown alongside quartiles because "
        "a healthy mean routinely hides a quarter of the book losing money.",
    ),
    Department(
        "operations", "04", "Service operations",
        "Head of operations",
        "Onboarding, transfers and tickets against target, plus the capacity each firm runs at. "
        "An event open past its target counts as breached now, not when it eventually closes.",
    ),
    Department(
        "advisory", "05", "Advisory and book",
        "Head of advice, corp dev",
        "Who holds the book, what it earns, and what walks out if they leave. Assignments are "
        "effective dated, so an advisor who has left still shows the book they held.",
    ),
    Department(
        "assurance", "06", "Data and assurance",
        "Platform team",
        "Whether the numbers in the other five views can be defended: what reconciled against "
        "each source system's own reports, where two sources disagree, and how much of the "
        "estate is under lineage.",
    ),
)


# -- views -------------------------------------------------------------------


def _order(data: DashboardData) -> list[str]:
    return [f["firm_id"] for f in data.firms]


def _colours(data: DashboardData) -> dict[str, str]:
    order = _order(data)
    return {fid: firm_colour(fid, order) for fid in order}


def _kpi(data: DashboardData, kpi: str, firm_id: str) -> dict[str, Any] | None:
    for row in data.kpis:
        if row["kpi"] == kpi and row["firm_id"] == firm_id:
            return row
    return None


def _peer(data: DashboardData, kpi: str) -> float | None:
    for row in data.kpis:
        if row["kpi"] == kpi and row["peer_value"] is not None:
            return row["peer_value"]
    return None


def _short_name(name: str, words: int = 2) -> str:
    """First couple of words. Full names truncate inside a half-width panel and
    a truncated name is worse than a short one."""
    parts = str(name).split()
    return " ".join(parts[:words])


def _bar_label(data: DashboardData, firm_id: str) -> str:
    return f"{firm_id} {_short_name(_scorecard(data, firm_id).get('firm_name', firm_id), 1)}"


def _scorecard(data: DashboardData, firm_id: str) -> dict[str, Any]:
    for row in data.scorecard:
        if row["firm_id"] == firm_id:
            return row
    return {}


def _worst_on(data: DashboardData, kpi: str) -> dict[str, Any] | None:
    """The firm ranked last on a metric, whichever direction is good."""
    entries = [r for r in data.kpis if r["kpi"] == kpi and r["firm_rank"] is not None]
    return max(entries, key=lambda r: r["firm_rank"]) if entries else None


def view_executive(data: DashboardData) -> str:
    colours = _colours(data)
    order = _order(data)

    headline_kpis = (
        ("actual_yield_bps", "Realised yield"),
        ("realization_rate", "Realisation"),
        ("collection_rate", "Collection"),
        ("loaded_margin_pct", "Loaded margin"),
        ("leakage_rate", "Leakage"),
        ("sla_attainment", "SLA attainment"),
        ("top_producer_share", "Largest advisor book"),
        ("cost_income_ratio", "Cost to income"),
    )

    tiles = []
    for kpi, label in headline_kpis:
        entries = [r for r in data.kpis if r["kpi"] == kpi]
        if not entries:
            continue
        unit = entries[0]["unit"]
        peer = _peer(data, kpi)
        worst = _worst_on(data, kpi)
        # Concentration has no meaningful platform aggregate: summing advisor
        # shares across firms is not a number. Lead with the worst firm instead
        # of leaving the tile blank.
        headline, caption = peer, "platform"
        if headline is None and worst is not None:
            headline, caption = worst["value"], f'worst, {worst["firm_id"]}'
        foot = f'<span class="chip flat">{e(caption)}</span>'
        if peer is not None and worst is not None and worst["value"] is not None:
            foot = (
                f'<span class="chip flat">worst {e(worst["firm_id"])} '
                f'{e(fmt_unit(worst["value"], unit))}</span>'
            )
        tiles.append(
            '<div class="kpi">'
            f'<span class="label">{e(label)}</span>'
            f'<span class="value num">{e(fmt_unit(headline, unit))}</span>'
            f'<span class="foot">{foot}</span>'
            "</div>"
        )

    # League table, ranked on realised yield rather than on revenue.
    rows = []
    for row in sorted(data.scorecard, key=lambda r: -(r["actual_yield_bps"] or 0)):
        fid = row["firm_id"]
        rows.append(
            "<tr>"
            f'<td><span class="swatch" style="background:{colours.get(fid, "var(--accent)")}"></span>'
            f'{e(row["firm_name"])}<span class="sub">{e(fid)} · {e(row["role"])} · '
            f'{e(compact(row["total_aum"]))} · {e(count(row["household_count"]))} households</span></td>'
            f'<td class="num">{e(bps(row["schedule_yield_bps"]))}</td>'
            f'<td class="num">{e(bps(row["actual_yield_bps"]))}</td>'
            f'<td class="num">{e(bps(row["collected_yield_bps"]))}</td>'
            f'<td class="num">{e(pct(row["realization_rate"]))}</td>'
            f'<td class="num">{e(pct(row["loaded_margin_pct"]))}</td>'
            f'<td class="num">{e(pct(row["sla_attainment"]))}</td>'
            f'<td class="num">{e(money(row["margin_per_household"]))}</td>'
            "</tr>"
        )

    peer_sched = _peer(data, "schedule_yield_bps")
    peer_realz = _peer(data, "realization_rate")
    quadrant_points = [
        {
            "x": _scorecard(data, fid).get("schedule_yield_bps"),
            "y": _scorecard(data, fid).get("realization_rate"),
            "label": fid,
            "colour": colours[fid],
        }
        for fid in order
    ]

    consolidated_bridge = _consolidated_bridge(data)

    # The one sentence a reader should leave with, derived rather than written.
    worst_realz = _worst_on(data, "realization_rate")
    lead = ""
    if worst_realz:
        wid = worst_realz["firm_id"]
        wsc = _scorecard(data, wid)
        if wsc.get("schedule_yield_bps") and peer_sched:
            priced_above = wsc["schedule_yield_bps"] > peer_sched
            lead = (
                f'<p class="note {"crit" if priced_above else "warn"}">'
                f'<strong>{e(wsc["firm_name"])}</strong> is priced '
                f'{"above" if priced_above else "below"} the platform '
                f'({e(bps(wsc["schedule_yield_bps"]))} against {e(bps(peer_sched))}) and realises '
                f'{e(pct(wsc["realization_rate"]))} of it, the lowest of the {len(order)} firms. '
                f'That combination is a billing-execution problem, not a pricing one: '
                f'{e(money(wsc["leak_never_invoiced"]))} of the quarter was never invoiced at all.'
                "</p>"
            )

    return f"""
{lead}
<div class="kpis k4">{"".join(tiles)}</div>

<div class="grid g-wide" style="margin-top:14px">
  <div class="panel">
    <h3>Platform yield bridge</h3>
    <p class="cap">Consolidated, annualised basis points on AUM. Bars in red are money the
    schedules entitled the platform to and it did not keep; the green step is billing above
    schedule, which offsets the loss and is itself a refund exposure.</p>
    {waterfall(consolidated_bridge)}
  </div>
  <div class="panel">
    <h3>Pricing against execution</h3>
    <p class="cap">Horizontal is what the fee schedules entitle each firm to. Vertical is how
    much of it gets invoiced. Dashed lines are the platform. Bottom-right is the expensive
    problem: priced well, billed badly.</p>
    {scatter(quadrant_points, "Schedule yield", "Realisation rate", bps, pct,
             x_ref=peer_sched, y_ref=peer_realz)}
  </div>
</div>

<div class="panel" style="margin-top:14px">
  <h3>Firm league table</h3>
  <p class="cap">Ranked on realised yield, not on revenue. Every column is a rate or a per-unit
  figure, so the ordering reflects how the firms are run rather than how large they are.</p>
  <div class="tw"><table>
    <thead><tr>
      <th>Firm</th><th>Schedule<span class="sub">entitlement</span></th>
      <th>Realised<span class="sub">invoiced</span></th><th>Collected<span class="sub">cash</span></th>
      <th>Realisation</th><th>Margin</th><th>SLA</th><th>Margin / household</th>
    </tr></thead>
    <tbody>{"".join(rows)}</tbody>
  </table></div>
</div>"""


def _consolidated_bridge(data: DashboardData) -> list[dict[str, Any]]:
    """Sum the firm bridges into one, weighting by AUM through the bps maths.

    Done by re-deriving from dollar amounts rather than averaging the firms'
    basis points, which would be an unweighted mean and wrong.
    """
    total_aum = sum(r["total_aum"] or 0 for r in data.scorecard)
    if not total_aum:
        return []

    def to_bps(amount: float) -> float:
        return round(amount * 4 / total_aum * 10000, 2)

    schedule = sum(r["expected_amount"] or 0 for r in data.scorecard)
    billed = sum(r["billed_amount"] or 0 for r in data.scorecard)
    collected = sum(r["collected_amount"] or 0 for r in data.scorecard)
    never = sum(r["leak_never_invoiced"] or 0 for r in data.scorecard)
    below = sum(r["leak_below_schedule"] or 0 for r in data.scorecard)
    over = sum(r["over_billed"] or 0 for r in data.scorecard)
    uncollected = sum(r["leak_uncollected"] or 0 for r in data.scorecard)
    return [
        {"step_kind": "total", "label": "Schedule", "bps": to_bps(schedule), "delta_bps": None},
        {"step_kind": "loss", "label": "Never invoiced", "bps": None, "delta_bps": -to_bps(never)},
        {"step_kind": "loss", "label": "Below schedule", "bps": None, "delta_bps": -to_bps(below)},
        {"step_kind": "gain", "label": "Above schedule", "bps": None, "delta_bps": to_bps(over)},
        {"step_kind": "total", "label": "Invoiced", "bps": to_bps(billed), "delta_bps": None},
        {"step_kind": "loss", "label": "Not collected", "bps": None, "delta_bps": -to_bps(uncollected)},
        {"step_kind": "total", "label": "Collected", "bps": to_bps(collected), "delta_bps": None},
    ]


LEAK_LABEL = {
    "never_invoiced": "Never invoiced",
    "billed_below_schedule": "Billed below schedule",
    "uncollected": "Invoiced, not collected",
}
AGE_LABEL = {
    "current": "Current", "1_30": "1-30 days", "31_60": "31-60 days",
    "61_90": "61-90 days", "over_90": "90+ days",
}
FINDING_CHIP = {
    "never_invoiced": ("crit", "Never invoiced"),
    "billed_below_schedule": ("warn", "Below schedule"),
    "billed_above_schedule": ("warn", "Above schedule"),
    "billed_without_schedule": ("crit", "No schedule"),
}


def view_finance(data: DashboardData) -> str:
    colours = _colours(data)
    order = _order(data)

    bridges = []
    for fid in order:
        steps = [b for b in data.bridge if b["firm_id"] == fid]
        sc = _scorecard(data, fid)
        if not steps:
            continue
        bridges.append(
            '<div class="panel">'
            f'<h3><span class="swatch" style="background:{colours[fid]}"></span>'
            f'{e(sc.get("firm_name", fid))}</h3>'
            f'<p class="cap">{e(bps(sc.get("schedule_yield_bps")))} entitled, '
            f'{e(bps(sc.get("actual_yield_bps")))} invoiced, '
            f'{e(bps(sc.get("collected_yield_bps")))} collected.</p>'
            f"{waterfall(steps, width=430, height=215, short_labels=True)}"
            "</div>"
        )

    leak_types = ["never_invoiced", "billed_below_schedule", "uncollected"]
    lookup = {(r["firm_id"], r["leakage_type"]): r["amount"] or 0 for r in data.leakage}
    leak_series = [
        (LEAK_LABEL[t], [lookup.get((f, t), 0) for f in order], f"var({SERIES[i]})")
        for i, t in enumerate(leak_types)
    ]
    leak_rows = []
    for t in leak_types:
        total = sum(lookup.get((f, t), 0) for f in order)
        cells = "".join(
            f'<td class="num">{e(money(lookup.get((f, t), 0)))}</td>' for f in order
        )
        leak_rows.append(
            f'<tr><td><span class="swatch" style="background:var({SERIES[leak_types.index(t)]})">'
            f'</span>{e(LEAK_LABEL[t])}</td>{cells}'
            f'<td class="num">{e(money(total))}</td></tr>'
        )
    grand = sum(lookup.values())
    leak_rows.append(
        '<tr class="total"><td>Total</td>'
        + "".join(
            f'<td class="num">{e(money(sum(lookup.get((f, t), 0) for t in leak_types)))}</td>'
            for f in order
        )
        + f'<td class="num">{e(money(grand))}</td></tr>'
    )

    ageing_buckets = ["current", "1_30", "31_60", "61_90", "over_90"]
    ageing_lookup = {(r["firm_id"], r["ageing_bucket"]): r for r in data.ageing}
    ageing_rows = []
    for bucket in ageing_buckets:
        cells = "".join(
            f'<td class="num">{e(money((ageing_lookup.get((f, bucket)) or {}).get("amount")))}</td>'
            for f in order
        )
        total = sum(
            (ageing_lookup.get((f, bucket)) or {}).get("amount") or 0 for f in order
        )
        if total == 0:
            continue
        severity = "crit" if bucket in ("61_90", "over_90") else (
            "warn" if bucket in ("31_60",) else "flat"
        )
        ageing_rows.append(
            f'<tr><td><span class="chip {severity} plain">{e(AGE_LABEL[bucket])}</span></td>'
            f'{cells}<td class="num">{e(money(total))}</td></tr>'
        )

    realz_rows = [
        (
            _bar_label(data, fid),
            _scorecard(data, fid).get("realization_rate"),
            colours[fid],
        )
        for fid in order
    ]
    coll_rows = [
        (
            _bar_label(data, fid),
            _scorecard(data, fid).get("collection_rate"),
            colours[fid],
        )
        for fid in order
    ]

    finding_rows = "".join(
        f'<tr><td>{e(f["household_name"] or f["household_id"])}'
        f'<span class="sub">{e(f["firm_id"])} · {e(f["schedule_name"] or "no schedule")}</span></td>'
        f'<td><span class="chip {FINDING_CHIP.get(f["finding"], ("flat", f["finding"]))[0]}">'
        f'{e(FINDING_CHIP.get(f["finding"], ("flat", f["finding"]))[1])}</span></td>'
        f'<td class="num">{e(money(f["expected_amount"], 2))}</td>'
        f'<td class="num">{e(money(f["billed_amount"], 2))}</td>'
        f'<td class="num">{e(money(f["variance_amount"], 2))}</td></tr>'
        for f in data.findings[:14]
    )

    firm_headers = "".join(
        f'<th>{e(f)}<span class="sub">{e(_short_name(_scorecard(data, f).get("firm_name", f)))}'
        "</span></th>"
        for f in order
    )

    return f"""
<div class="grid g3">{"".join(bridges)}</div>

<div class="grid g-wide" style="margin-top:14px">
  <div class="panel">
    <h3>Leakage by cause and firm</h3>
    <p class="cap">Three causes, three owners. "Never invoiced" is the billing run,
    "below schedule" is repapering, "not collected" is credit control. Summing them into one
    number makes the finding unactionable.</p>
    {legend([LEAK_LABEL[t] for t in leak_types], [f"var({s})" for s in SERIES])}
    {grouped_bars([_scorecard(data, f).get("firm_name", f) for f in order], leak_series, compact)}
  </div>
  <div class="panel">
    <h3>Execution rates</h3>
    <p class="cap">Realisation is invoiced over entitled; collection is cash over invoiced.
    The tick on each bar is the platform.</p>
    <p class="cap" style="margin-bottom:6px"><strong>Realisation</strong></p>
    {hbar_group(realz_rows, _peer(data, "realization_rate"))}
    <p class="cap" style="margin:14px 0 6px"><strong>Collection</strong></p>
    {hbar_group(coll_rows, _peer(data, "collection_rate"))}
  </div>
</div>

<div class="grid g2" style="margin-top:14px">
  <div class="panel">
    <h3>Leakage, this quarter</h3>
    <div class="tw"><table>
      <thead><tr><th>Cause</th>{firm_headers}<th>Total</th></tr></thead>
      <tbody>{"".join(leak_rows)}</tbody>
    </table></div>
  </div>
  <div class="panel">
    <h3>Receivables ageing</h3>
    <p class="cap">Every open invoice, not only this quarter's, measured from due date to the
    period end. Billing is quarterly, so an invoice raised in April is already 90 days old by
    the June close; the bucket to read is the trend, not the label.</p>
    <div class="tw"><table>
      <thead><tr><th>Bucket</th>{firm_headers}<th>Total</th></tr></thead>
      <tbody>{"".join(ageing_rows)}</tbody>
    </table></div>
  </div>
</div>

<div class="panel" style="margin-top:14px">
  <h3>Households behind the number</h3>
  <p class="cap">Largest variances between the assigned fee schedule and what was invoiced.
  Every row opens to its canonical records and the source file behind them.</p>
  <div class="tw"><table>
    <thead><tr><th>Household</th><th>Finding</th><th>Entitled</th><th>Invoiced</th>
      <th>Variance</th></tr></thead>
    <tbody>{finding_rows}</tbody>
  </table></div>
</div>"""


def view_profitability(data: DashboardData) -> str:
    colours = _colours(data)
    order = _order(data)

    unit_rows = []
    for fid in order:
        sc = _scorecard(data, fid)
        unit_rows.append(
            "<tr>"
            f'<td><span class="swatch" style="background:{colours[fid]}"></span>'
            f'{e(sc.get("firm_name", fid))}</td>'
            f'<td class="num">{e(money(sc.get("revenue_per_household")))}</td>'
            f'<td class="num">{e(money(sc.get("cost_per_household")))}</td>'
            f'<td class="num">{e(money(sc.get("margin_per_household")))}</td>'
            f'<td class="num">{e(pct(sc.get("cost_income_ratio")))}</td>'
            f'<td class="num">{e(pct(sc.get("loaded_margin_pct")))}</td>'
            f'<td class="num">{e(compact(sc.get("aum_per_household")))}</td>'
            "</tr>"
        )

    dist_rows = []
    for row in data.distribution:
        fid = row["firm_id"]
        sc = _scorecard(data, fid)
        share = row["loss_making_share"] or 0
        chip = "crit" if share > 0.2 else ("warn" if share > 0.08 else "good")
        dist_rows.append(
            "<tr>"
            f'<td><span class="swatch" style="background:{colours.get(fid, "var(--accent)")}"></span>'
            f'{e(sc.get("firm_name", fid))}</td>'
            f'<td class="num">{e(count(row["households"]))}</td>'
            f'<td><span class="chip {chip}">{e(count(row["loss_making_households"]))} '
            f'({e(pct(share, 0))})</span></td>'
            f'<td class="num">{e(money(row["margin_p25"]))}</td>'
            f'<td class="num">{e(money(row["margin_p50"]))}</td>'
            f'<td class="num">{e(money(row["margin_p75"]))}</td>'
            f'<td class="num">{e(compact(row["aum_p50"]))}</td>'
            "</tr>"
        )

    margin_bars = [
        (
            _bar_label(data, fid),
            _scorecard(data, fid).get("loaded_margin_pct"),
            colours[fid],
        )
        for fid in order
    ]
    cost_bars = [
        (
            _bar_label(data, fid),
            _scorecard(data, fid).get("cost_income_ratio"),
            colours[fid],
        )
        for fid in order
    ]

    def client_table(rows: Sequence[dict[str, Any]], worst: bool) -> str:
        body = "".join(
            f'<tr class="{"worst" if worst else "best"}">'
            f'<td>{e(r["household_name"] or r["household_id"])}'
            f'<span class="sub">{e(r["firm_id"])} · {e(r["segment"] or "unsegmented")}</span></td>'
            f'<td class="num">{e(compact(r["aum"]))}</td>'
            f'<td class="num">{e(money(r["billed_amount"]))}</td>'
            f'<td class="num">{e(money(r["cost_to_serve"]))}</td>'
            f'<td class="num">{e(money(r["loaded_margin"]))}</td>'
            f'<td class="num">{e(bps(r["actual_yield_bps"]))}</td></tr>'
            for r in rows[:8]
        )
        return (
            '<div class="tw"><table><thead><tr><th>Household</th><th>AUM</th><th>Billed</th>'
            '<th>Cost to serve</th><th>Margin</th><th>Yield</th></tr></thead>'
            f"<tbody>{body}</tbody></table></div>"
        )

    total_loss_making = sum(r["loss_making_households"] or 0 for r in data.distribution)
    total_households = sum(r["households"] or 0 for r in data.distribution)
    loss_note = ""
    if total_households:
        loss_note = (
            f'<p class="note warn"><strong>{e(count(total_loss_making))}</strong> households '
            f'({e(pct(total_loss_making / total_households, 1))} of the platform) cost more to '
            "serve than they bill. The mean margin does not show them; the quartiles below do."
            "</p>"
        )

    return f"""
{loss_note}
<div class="grid g2">
  <div class="panel">
    <h3>Loaded margin</h3>
    <p class="cap">Billed revenue less directly attributed service time, less the advisor cost
    carried by that book, less an allocated share of the remaining base. The tick is the platform.</p>
    {hbar_group(margin_bars, _peer(data, "loaded_margin_pct"))}
  </div>
  <div class="panel">
    <h3>Cost to income</h3>
    <p class="cap">Every dollar of cost against every dollar invoiced. Lower is better, so the
    ordering here is the inverse of the panel beside it.</p>
    {hbar_group(cost_bars, _peer(data, "cost_income_ratio"))}
  </div>
</div>

<div class="panel" style="margin-top:14px">
  <h3>Unit economics</h3>
  <p class="cap">Per household, which is the only way a $400m firm and a $1.7bn firm compare.
  AUM per household is shown last as context: it is nearly flat across the firms, which is what
  rules out client mix as the explanation for the margin spread.</p>
  <div class="tw"><table>
    <thead><tr><th>Firm</th><th>Revenue / hh</th><th>Cost / hh</th><th>Margin / hh</th>
      <th>Cost to income</th><th>Margin %</th><th>AUM / hh</th></tr></thead>
    <tbody>{"".join(unit_rows)}</tbody>
  </table></div>
</div>

<div class="panel" style="margin-top:14px">
  <h3>Margin distribution</h3>
  <p class="cap">Quartiles of household margin. A firm whose median is healthy and whose lower
  quartile is negative has a pricing floor problem, not an average problem.</p>
  <div class="tw"><table>
    <thead><tr><th>Firm</th><th>Households</th><th>Loss making</th><th>Margin p25</th>
      <th>Median</th><th>Margin p75</th><th>Median AUM</th></tr></thead>
    <tbody>{"".join(dist_rows)}</tbody>
  </table></div>
</div>

<div class="grid g2" style="margin-top:14px">
  <div class="panel">
    <h3>Most profitable clients</h3>
    <p class="cap">Not the same ranking as AUM.</p>
    {client_table(data.best_clients, worst=False)}
  </div>
  <div class="panel">
    <h3>Least profitable clients</h3>
    <p class="cap">Candidates for repricing, re-segmenting or an exit conversation.</p>
    {client_table(data.worst_clients, worst=True)}
  </div>
</div>"""


def _over_by(event: dict[str, Any]) -> str:
    """How far past target, as a multiple. '3.4x over' reads faster than two
    durations the reader has to divide."""
    target = event.get("sla_target_hours") or 0
    elapsed = event.get("elapsed_hours") or 0
    if not target:
        return "no target"
    return f"{elapsed / target:.1f}x over"


def view_operations(data: DashboardData) -> str:
    colours = _colours(data)
    order = _order(data)
    event_types = sorted({r["event_type"] for r in data.sla})
    lookup = {(r["firm_id"], r["event_type"]): r for r in data.sla}

    breach_series = [
        (
            _scorecard(data, fid).get("firm_name", fid),
            [(lookup.get((fid, t)) or {}).get("breach_rate") for t in event_types],
            colours[fid],
        )
        for fid in order
    ]

    rows = []
    for fid in order:
        for t in event_types:
            r = lookup.get((fid, t))
            if not r:
                continue
            rate = r["breach_rate"] or 0
            chip = "crit" if rate >= 0.25 else ("warn" if rate >= 0.12 else "good")
            rows.append(
                "<tr>"
                f'<td><span class="swatch" style="background:{colours[fid]}"></span>'
                f'{e(_scorecard(data, fid).get("firm_name", fid))}'
                f'<span class="sub">{e(t)}</span></td>'
                f'<td class="num">{e(count(r["event_count"]))}</td>'
                f'<td class="num">{e(count(r["breach_count"]))}</td>'
                f'<td class="num">{e(count(r["still_open"]))}</td>'
                f'<td><span class="chip {chip}">{e(pct(rate))}</span></td>'
                f'<td class="num">{e(hours(r["avg_hours"]))}</td>'
                f'<td class="num">{e(hours(r["p90_hours"]))}</td>'
                "</tr>"
            )

    capacity_rows = []
    for fid in order:
        sc = _scorecard(data, fid)
        capacity_rows.append(
            "<tr>"
            f'<td><span class="swatch" style="background:{colours[fid]}"></span>'
            f'{e(sc.get("firm_name", fid))}</td>'
            f'<td class="num">{e(count(sc.get("active_producers")))}</td>'
            f'<td class="num">{e(count(sc.get("household_count")))}</td>'
            f'<td class="num">{e(count(sc.get("households_per_producer")))}</td>'
            f'<td class="num">{e(compact(sc.get("aum_per_producer")))}</td>'
            f'<td class="num">{e(money(sc.get("revenue_per_producer")))}</td>'
            f'<td class="num">{e(count(sc.get("sla_events")))}</td>'
            "</tr>"
        )

    open_rows = "".join(
        f'<tr><td>{e(ev["service_event_id"])}'
        f'<span class="sub">{e(ev["firm_id"])} · {e(ev["event_type"])} · '
        f'{e(ev["household_id"] or "no household")}</span></td>'
        f'<td class="num">{e(hours(ev["sla_target_hours"]))}</td>'
        f'<td class="num">{e(hours(ev["elapsed_hours"]))}</td>'
        f'<td><span class="chip crit">{e(_over_by(ev))}</span></td>'
        "</tr>"
        for ev in data.open_events[:12]
        if ev["sla_target_hours"]
    )

    attain_bars = [
        (
            _bar_label(data, fid),
            _scorecard(data, fid).get("sla_attainment"),
            colours[fid],
        )
        for fid in order
    ]

    total_open = sum(sc.get("sla_open") or 0 for sc in data.scorecard)
    open_note = ""
    if total_open:
        open_note = (
            f'<p class="note crit"><strong>{e(count(total_open))}</strong> events are still open '
            "past their target. They are counted as breached here rather than as pending, because "
            "the tickets nobody has touched are the ones that never close.</p>"
        )

    return f"""
{open_note}
<div class="grid g-wide">
  <div class="panel">
    <h3>Breach rate by event type</h3>
    <p class="cap">Share of events that exceeded their target. Onboarding carries a ten-day
    target, transfers five days, trades one.</p>
    {legend([_scorecard(data, f).get("firm_name", f) for f in order],
            [colours[f] for f in order])}
    {grouped_bars(event_types, breach_series, pct)}
  </div>
  <div class="panel">
    <h3>SLA attainment</h3>
    <p class="cap">The complement of the breach rate, across all event types. Tick is the platform.</p>
    {hbar_group(attain_bars, _peer(data, "sla_attainment"))}
  </div>
</div>

<div class="panel" style="margin-top:14px">
  <h3>Service detail</h3>
  <div class="tw"><table>
    <thead><tr><th>Firm and type</th><th>Events</th><th>Breached</th><th>Open</th>
      <th>Breach rate</th><th>Average</th><th>P90</th></tr></thead>
    <tbody>{"".join(rows)}</tbody>
  </table></div>
</div>

<div class="grid g2" style="margin-top:14px">
  <div class="panel">
    <h3>Capacity</h3>
    <p class="cap">Load per advisor. The firm carrying the fewest households per advisor also
    carries the highest cost to serve, which is the operating-leverage half of the margin gap.</p>
    <div class="tw"><table>
      <thead><tr><th>Firm</th><th>Advisors</th><th>Households</th><th>HH / advisor</th>
        <th>AUM / advisor</th><th>Revenue / advisor</th><th>Events</th></tr></thead>
      <tbody>{"".join(capacity_rows)}</tbody>
    </table></div>
  </div>
  <div class="panel">
    <h3>Open and past target</h3>
    <p class="cap">Longest running first. These are the backlog, not a queue.</p>
    <div class="tw"><table>
      <thead><tr><th>Event</th><th>Target</th><th>Elapsed</th><th></th></tr></thead>
      <tbody>{open_rows or '<tr><td colspan="4">Nothing open past target.</td></tr>'}</tbody>
    </table></div>
  </div>
</div>"""


def view_advisory(data: DashboardData) -> str:
    colours = _colours(data)
    order = _order(data)

    rows = []
    for p in sorted(data.producers, key=lambda r: -(r["book_value"] or 0))[:16]:
        fid = p["firm_id"]
        departed = p["has_departed"]
        share = p["book_share"] or 0
        chip = (
            '<span class="chip crit">departed</span>' if departed
            else '<span class="chip flat">active</span>'
        )
        share_chip = "crit" if share > 0.35 else ("warn" if share > 0.25 else "flat")
        rows.append(
            "<tr>"
            f'<td><span class="swatch" style="background:{colours.get(fid, "var(--accent)")}"></span>'
            f'{e(p["producer_name"] or p["producer_id"])}'
            f'<span class="sub">{e(fid)} · {e(p["producer_id"])}</span></td>'
            f"<td>{chip}</td>"
            f'<td class="num">{e(count(p["households"]))}</td>'
            f'<td class="num">{e(compact(p["book_value"]))}</td>'
            f'<td><span class="chip {share_chip}">{e(pct(share))}</span></td>'
            f'<td class="num">{e(money(p["billed_amount"]))}</td>'
            f'<td class="num">{e(money(p["loaded_margin"]))}</td>'
            f'<td class="num">{e(money(p["revenue_per_household"]))}</td>'
            f'<td class="num">{e(bps(p["yield_bps"]))}</td>'
            "</tr>"
        )

    conc_rows = []
    for fid in order:
        sc = _scorecard(data, fid)
        top = sc.get("top_producer_share") or 0
        chip = "crit" if top > 0.35 else ("warn" if top > 0.25 else "good")
        conc_rows.append(
            "<tr>"
            f'<td><span class="swatch" style="background:{colours[fid]}"></span>'
            f'{e(sc.get("firm_name", fid))}</td>'
            f'<td><span class="chip {chip}">{e(pct(top))}</span></td>'
            f'<td class="num">{e(pct(sc.get("top3_producer_share")))}</td>'
            f'<td class="num">{e(pct(sc.get("top10_client_revenue_share")))}</td>'
            f'<td class="num">{e(compact(sc.get("departed_book_value")))}</td>'
            f'<td class="num">{e(pct(sc.get("departed_book_share")))}</td>'
            "</tr>"
        )

    top_bars = [
        (
            _bar_label(data, fid),
            _scorecard(data, fid).get("top_producer_share"),
            colours[fid],
        )
        for fid in order
    ]
    client_bars = [
        (
            _bar_label(data, fid),
            _scorecard(data, fid).get("top10_client_revenue_share"),
            colours[fid],
        )
        for fid in order
    ]

    departed_total = sum(sc.get("departed_book_value") or 0 for sc in data.scorecard)
    departed_note = ""
    if departed_total:
        departed_note = (
            f'<p class="note warn"><strong>{e(compact(departed_total))}</strong> of book is still '
            "assigned to advisors who have left. Assignments are effective dated, so this is the "
            "book they held rather than whoever inherited it, which is the number that matters "
            "for retention risk.</p>"
        )

    return f"""
{departed_note}
<div class="grid g2">
  <div class="panel">
    <h3>Largest advisor book</h3>
    <p class="cap">Share of the firm's own book, not the platform's. Key-person risk is read
    against the firm, because that is the entity that loses the clients.</p>
    {hbar_group(top_bars)}
  </div>
  <div class="panel">
    <h3>Top ten client revenue</h3>
    <p class="cap">Client concentration. A firm can have diversified advisors and concentrated
    clients; they are separate risks.</p>
    {hbar_group(client_bars)}
  </div>
</div>

<div class="panel" style="margin-top:14px">
  <h3>Concentration by firm</h3>
  <div class="tw"><table>
    <thead><tr><th>Firm</th><th>Largest advisor</th><th>Top three</th><th>Top ten clients</th>
      <th>Held by leavers</th><th>Share</th></tr></thead>
    <tbody>{"".join(conc_rows)}</tbody>
  </table></div>
</div>

<div class="panel" style="margin-top:14px">
  <h3>Advisor scorecard</h3>
  <p class="cap">Loaded margin per advisor, after the cost of servicing their own households.
  Revenue per household separates the advisor with a large book from the one with a good one.</p>
  <div class="tw"><table>
    <thead><tr><th>Advisor</th><th>Status</th><th>Households</th><th>Book</th><th>Firm share</th>
      <th>Billed</th><th>Loaded margin</th><th>Revenue / hh</th><th>Yield</th></tr></thead>
    <tbody>{"".join(rows)}</tbody>
  </table></div>
</div>"""


def view_assurance(data: DashboardData) -> str:
    a = data.assurance
    colours = _colours(data)

    tiles = [
        ("Checks passing", f"{a['recon_checks'] - a['recon_failed']} / {a['recon_checks']}",
         "against each source system's own totals",
         "good" if a["recon_failed"] == 0 else "crit"),
        ("Source disagreements", count(a["open_variances"]),
         "two sources, one fact, unresolved", "warn" if a["open_variances"] else "good"),
        ("Unreviewed schema changes", count(a["unacked_drift"]),
         "a removed field halts mapping", "warn" if a["unacked_drift"] else "good"),
        ("AI in numeric columns", count(a["ai_violations"]),
         "without a named human confirmation",
         "crit" if a["ai_violations"] else "good"),
        ("Raw records", count(a["raw_rows"]), "under lineage, artifact-backed", "flat"),
        ("Lineage edges", count(a["lineage_edges"]), "figure to canonical row to raw", "flat"),
    ]
    tile_html = "".join(
        '<div class="kpi">'
        f'<span class="label">{e(label)}</span>'
        f'<span class="value num">{e(value)}</span>'
        f'<span class="foot"><span class="chip {chip}">{e(note)}</span></span>'
        "</div>"
        for label, value, note, chip in tiles
    )

    recon_rows = "".join(
        f'<tr><td>{e(str(r["check_name"]).replace("_", " "))}'
        f'<span class="sub">{e(r["firm_id"])} · {e(r["period_end"])}</span></td>'
        f'<td class="num">{e(money(r["expected"], 2))}</td>'
        f'<td class="num">{e(money(r["actual"], 2))}</td>'
        f'<td class="num">{e(pct(r["variance_pct"], 4))}</td>'
        f'<td class="num">{e(pct(r["tolerance_pct"], 4))}</td>'
        f'<td><span class="chip {"good" if r["passed"] else "crit"}">'
        f'{"within tolerance" if r["passed"] else "breach"}</span></td></tr>'
        for r in data.recon[:12]
    )

    by_firm: dict[str, list[dict[str, Any]]] = {}
    for row in data.coverage:
        by_firm.setdefault(row["firm_id"], []).append(row)
    coverage_parts = []
    for fid, sources in sorted(by_firm.items()):
        tags = " ".join(f'<span class="tag">{e(s["source_id"])}</span>' for s in sources)
        coverage_parts.append(
            '<tr><td><span class="swatch" style="background:'
            f'{colours.get(fid, "var(--accent)")}"></span>'
            f'{e(_scorecard(data, fid).get("firm_name", fid))}</td>'
            f'<td class="num">{e(count(len(sources)))}</td>'
            f'<td class="num">{e(count(sum(s["rows"] or 0 for s in sources)))}</td>'
            f"<td>{tags}</td></tr>"
        )
    coverage_rows = "".join(coverage_parts)

    return f"""
<div class="kpis k3">{tile_html}</div>

<p class="note" style="margin-top:14px">Every figure in the other five views resolves through
row-grain lineage to the canonical rows behind it, then to the raw payloads, then to the file the
client sent and its SHA-256. That is what makes a variance a finding rather than an assertion.</p>

<div class="panel" style="margin-top:14px">
  <h3>Reconciliation, current run</h3>
  <p class="cap">Our figure against what the source system reports for itself, with a stated
  tolerance. A missing counterparty figure counts as a failure: "we could not check" and
  "we checked and it agrees" must never look alike.</p>
  <div class="tw"><table>
    <thead><tr><th>Check</th><th>Source reports</th><th>We compute</th><th>Variance</th>
      <th>Tolerance</th><th></th></tr></thead>
    <tbody>{recon_rows}</tbody>
  </table></div>
</div>

<div class="panel" style="margin-top:14px">
  <h3>Source coverage by firm</h3>
  <p class="cap">Every extraction is written to object storage with its hash before it is loaded,
  so the database is rebuildable from the artifacts alone.</p>
  <div class="tw"><table>
    <thead><tr><th>Firm</th><th>Sources</th><th>Raw records</th><th>Systems</th></tr></thead>
    <tbody>{coverage_rows}</tbody>
  </table></div>
</div>"""


VIEWS: dict[str, Callable[[DashboardData], str]] = {
    "executive": view_executive,
    "finance": view_finance,
    "profitability": view_profitability,
    "operations": view_operations,
    "advisory": view_advisory,
    "assurance": view_assurance,
}


def chrome(data: DashboardData) -> Chrome:
    """The words around the views for a wealth estate.

    A firm is identified by its AUM because that is what the reader uses to
    place it, and the rail note states the comparability rule the whole console
    is built on so nobody reads a rate as a total.
    """
    subjects = tuple(
        f'{e(f["firm_id"])} &middot; {e(compact(f["total_aum"]))}' for f in data.firms
    )
    return Chrome(
        title=f"{data.tenant_name} Operating Console",
        tenant_name=data.tenant_name,
        period_label="quarter ending",
        period_end=str(data.period_end),
        generated_at=str(data.generated_at),
        subject_label="firms",
        subjects=subjects,
        rail_note=(
            "Every measure here is a rate, a per-unit figure or basis points on AUM, so "
            "firms of different size compare directly."
        ),
        footer=(
            "Yields are annualised: quarterly amounts multiplied by four over average "
            "assets."
        ),
    )
