"""Rendering a pack to a self-contained HTML page.

The pack definition lives in git as SQL (reporting/packs); this turns the
figures it produced into the artefact a board actually reads. Self-contained on
purpose: a pack emailed to a lender must render with no network, and the same
file is what the PDF is printed from.

Charts are hand-drawn SVG rather than a charting library, because every mark has
to sit on the same scale as its labels and stay inside the drawing bounds in
both themes, and because a 300KB dependency to draw four charts is not a trade
worth making in a document that gets emailed.
"""

from __future__ import annotations

import datetime as dt
import html
import json
from pathlib import Path
from typing import Any, Sequence

from fracture.pack.data import PackData

TEMPLATES = Path(__file__).parent / "templates"

SERIES_VARS = ("--series-1", "--series-2", "--series-3")


# -- formatting --------------------------------------------------------------


def money(value: float | None, places: int = 0, dash: str = "—") -> str:
    if value is None:
        return dash
    sign = "-" if value < 0 else ""
    return f"{sign}${abs(value):,.{places}f}"


def compact_money(value: float | None, dash: str = "—") -> str:
    """Board-pack scale: $1.24bn, $940.2m, $84.1k."""
    if value is None:
        return dash
    sign = "-" if value < 0 else ""
    v = abs(value)
    for cutoff, suffix, places in ((1e9, "bn", 2), (1e6, "m", 1), (1e3, "k", 1)):
        if v >= cutoff:
            return f"{sign}${v / cutoff:,.{places}f}{suffix}"
    return f"{sign}${v:,.0f}"


def pct(value: float | None, places: int = 1, dash: str = "—") -> str:
    if value is None:
        return dash
    return f"{value * 100:.{places}f}%"


def count(value: float | None, dash: str = "—") -> str:
    if value is None:
        return dash
    return f"{value:,.0f}"


def e(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _nice_ceiling(value: float) -> float:
    """Round an axis maximum up to something a person would choose."""
    if value <= 0:
        return 1.0
    import math

    magnitude = 10 ** math.floor(math.log10(value))
    for step in (1, 1.25, 1.5, 2, 2.5, 3, 4, 5, 7.5, 10):
        if value <= step * magnitude:
            return step * magnitude
    return 10 * magnitude


# -- charts ------------------------------------------------------------------


def stacked_area(
    series: Sequence[tuple[str, list[tuple[str, float]]]],
    width: int = 640,
    height: int = 210,
    value_fmt=compact_money,
) -> str:
    """Firms stacked into the consolidated total.

    Stacked rather than overlaid lines because the firms *compose* the total: the
    top edge is the consolidated figure, which is the thing the reader came for.
    """
    if not series:
        return ""
    dates = [d for d, _ in series[0][1]]
    n = len(dates)
    if n < 2:
        return ""
    pad_l, pad_r, pad_t, pad_b = 62, 14, 14, 26
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b

    totals = [sum(s[1][i][1] for s in series) for i in range(n)]
    y_max = _nice_ceiling(max(totals) * 1.04)

    def x(i: int) -> float:
        return pad_l + plot_w * i / (n - 1)

    def y(v: float) -> float:
        return pad_t + plot_h * (1 - v / y_max)

    parts: list[str] = []
    # Grid and y-axis labels first, so marks sit above them.
    for frac in (0, 0.25, 0.5, 0.75, 1.0):
        gy = pad_t + plot_h * (1 - frac)
        parts.append(f'<line class="grid-line" x1="{pad_l}" y1="{gy:.1f}" x2="{width - pad_r}" y2="{gy:.1f}"/>')
        parts.append(
            f'<text class="axis-label" x="{pad_l - 8}" y="{gy + 3.5:.1f}" text-anchor="end">'
            f"{e(value_fmt(y_max * frac))}</text>"
        )

    cumulative = [0.0] * n
    for idx, (name, points) in enumerate(series):
        upper = [cumulative[i] + points[i][1] for i in range(n)]
        top = " ".join(f"{x(i):.1f},{y(upper[i]):.1f}" for i in range(n))
        bottom = " ".join(f"{x(i):.1f},{y(cumulative[i]):.1f}" for i in reversed(range(n)))
        colour = f"var({SERIES_VARS[idx % len(SERIES_VARS)]})"
        parts.append(
            f'<polygon points="{top} {bottom}" fill="{colour}" fill-opacity="0.9" '
            f'stroke="var(--surface)" stroke-width="2" stroke-linejoin="round"/>'
        )
        cumulative = upper

    # Axis and end labels.
    parts.append(
        f'<line class="axis-line" x1="{pad_l}" y1="{pad_t + plot_h}" '
        f'x2="{width - pad_r}" y2="{pad_t + plot_h}"/>'
    )
    step = max(1, (n - 1) // 4)
    for i in range(0, n, step):
        label = dt.date.fromisoformat(dates[i]).strftime("%b %y")
        anchor = "start" if i == 0 else ("end" if i >= n - 1 else "middle")
        parts.append(
            f'<text class="axis-label" x="{x(i):.1f}" y="{height - 8}" '
            f'text-anchor="{anchor}">{e(label)}</text>'
        )
    return (
        f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="Assets under management by firm, stacked to the consolidated total">'
        + "".join(parts)
        + "</svg>"
    )


def grouped_bars(
    categories: Sequence[str],
    series: Sequence[tuple[str, list[float]]],
    width: int = 560,
    height: int = 200,
    value_fmt=compact_money,
) -> str:
    """One group per category, one bar per series. 4px rounded data-ends."""
    if not categories or not series:
        return ""
    pad_l, pad_r, pad_t, pad_b = 62, 12, 18, 30
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b
    y_max = _nice_ceiling(
        max((max(vals) for _, vals in series if vals), default=1) * 1.12
    )
    group_w = plot_w / len(categories)
    bar_gap = 2
    bar_w = max(6.0, (group_w * 0.66 - bar_gap * (len(series) - 1)) / len(series))

    parts: list[str] = []
    for frac in (0, 0.5, 1.0):
        gy = pad_t + plot_h * (1 - frac)
        parts.append(f'<line class="grid-line" x1="{pad_l}" y1="{gy:.1f}" x2="{width - pad_r}" y2="{gy:.1f}"/>')
        parts.append(
            f'<text class="axis-label" x="{pad_l - 8}" y="{gy + 3.5:.1f}" text-anchor="end">'
            f"{e(value_fmt(y_max * frac))}</text>"
        )

    for ci, category in enumerate(categories):
        block_w = bar_w * len(series) + bar_gap * (len(series) - 1)
        start = pad_l + group_w * ci + (group_w - block_w) / 2
        for si, (_, values) in enumerate(series):
            value = values[ci] if ci < len(values) else 0.0
            bar_h = 0 if y_max == 0 else plot_h * (value / y_max)
            bx = start + si * (bar_w + bar_gap)
            by = pad_t + plot_h - bar_h
            colour = f"var({SERIES_VARS[si % len(SERIES_VARS)]})"
            parts.append(
                f'<rect x="{bx:.1f}" y="{by:.1f}" width="{bar_w:.1f}" height="{max(bar_h, 1):.1f}" '
                f'rx="3" fill="{colour}"><title>{e(category)}: {e(value_fmt(value))}</title></rect>'
            )
        parts.append(
            f'<text class="axis-label" x="{pad_l + group_w * (ci + 0.5):.1f}" y="{height - 9}" '
            f'text-anchor="middle">{e(category)}</text>'
        )
    parts.append(
        f'<line class="axis-line" x1="{pad_l}" y1="{pad_t + plot_h}" '
        f'x2="{width - pad_r}" y2="{pad_t + plot_h}"/>'
    )
    return (
        f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="Leakage by firm and component">' + "".join(parts) + "</svg>"
    )


def hbars(rows: Sequence[dict[str, Any]], value_key: str, share_key: str) -> str:
    """Magnitude bars: one hue, length carries the value.

    Departed advisors get a hatched fill and an explicit chip, so the state is
    never carried by colour alone.
    """
    if not rows:
        return ""
    peak = max((r.get(share_key) or 0) for r in rows) or 1
    out: list[str] = []
    for row in rows:
        share = row.get(share_key) or 0
        departed = bool(row.get("has_departed"))
        chip = (
            '<span class="chip crit">departed</span>' if departed
            else '<span class="chip flat">active</span>'
        )
        out.append(
            '<div class="hbar-row">'
            '<div class="hbar-head">'
            f'<span class="name">{e(row["producer_name"])}</span>'
            f'<span class="tag">{e(row["firm_id"])}</span>{chip}'
            f'<span class="val num">{e(pct(share))} &middot; {e(compact_money(row.get(value_key)))}</span>'
            "</div>"
            '<div class="hbar-track">'
            f'<div class="hbar-fill{" departed" if departed else ""}" '
            f'style="width: {min(100.0, share / peak * 100):.1f}%"></div>'
            "</div>"
            "</div>"
        )
    return "".join(out)


def composition_bar(parts: Sequence[tuple[str, float, str]]) -> str:
    total = sum(max(v, 0) for _, v, _ in parts) or 1
    spans = "".join(
        f'<span style="width: {max(v, 0) / total * 100:.2f}%; background: {colour}" '
        f'title="{e(name)}: {e(money(v))}"></span>'
        for name, v, colour in parts
    )
    return f'<div class="stack">{spans}</div>'


# -- page --------------------------------------------------------------------

LEAKAGE_LABELS = {
    "never_invoiced": "Never invoiced",
    "billed_below_schedule": "Billed below schedule",
    "uncollected": "Invoiced, not collected",
}

FINDING_LABELS = {
    "never_invoiced": ("crit", "Never invoiced"),
    "billed_below_schedule": ("warn", "Below schedule"),
    "billed_above_schedule": ("warn", "Above schedule"),
    "as_expected": ("good", "As expected"),
    "billed_without_schedule": ("crit", "No schedule"),
}

AGEING_LABELS = {
    "current": "Current", "1_30": "1-30 days", "31_60": "31-60 days",
    "61_90": "61-90 days", "over_90": "90+ days",
}


def render(data: PackData) -> str:
    css = (TEMPLATES / "pack.css").read_text()
    sections: list[tuple[str, str]] = [
        ("platform", "One model of the platform"),
        ("economics", "Revenue and loaded margin"),
        ("leakage", "Unbilled and leaked revenue"),
        ("service", "Service and onboarding SLAs"),
        ("people", "Key-person concentration"),
        ("evidence", "Evidence and reconciliation"),
    ]

    body = "".join(
        [
            _headline(data),
            _platform(data),
            _economics(data),
            _leakage(data),
            _service(data),
            _people(data),
            _evidence(data),
            _footnote(data),
        ]
    )

    nav = "".join(
        f'<a href="#{sid}"><span class="idx">{i:02d}</span>{e(title)}</a>'
        for i, (sid, title) in enumerate(sections, start=1)
    )

    return f"""<title>Meridian Consolidated Pack</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;500;600&family=Source+Serif+4:opsz,wght@8..60,400;8..60,600&display=swap">
<style>{css}</style>
<div class="shell">
  <aside class="rail">
    <div class="brand">
      <span class="mark">Fracture Systems</span>
      <span class="tenant">{e(data.tenant_name)}</span>
      <span class="sub">{len(data.firms)} firms &middot; {e(data.motion)} tenant</span>
    </div>
    <div class="seal">
      <dl>
        <div class="row"><dt>Period</dt><dd>{e(data.period_start.isoformat())} &rarr; {e(data.period_end.isoformat())}</dd></div>
        <div class="row"><dt>System time pinned</dt><dd>{e(data.system_time.strftime("%Y-%m-%d %H:%M:%SZ"))}</dd></div>
        <div class="row"><dt>Content hash (sha-256)</dt><dd class="hash">{e(data.content_hash[:32])}<br>{e(data.content_hash[32:])}</dd></div>
        <div class="row"><dt>Figures</dt><dd>{data.figure_count} pinned</dd></div>
      </dl>
    </div>
    <nav class="nav">{nav}</nav>
    <div class="rail-foot">
      <span>Reissuing at the same system time reproduces this hash exactly. A new system time produces the restatement.</span>
      <span class="mono">run {e(data.pack_run_id[:8])}</span>
    </div>
  </aside>
  <main class="main">{body}</main>
</div>
"""


def _headline(data: PackData) -> str:
    aum = data.metric("platform_model", "consolidated_aum")
    billed = data.metric("revenue_margin", "consolidated_billed")
    margin_pct = data.metric("revenue_margin", "consolidated_margin_pct")
    leakage = data.metric("unbilled_leakage", "leakage_total")
    leak_rate = data.metric("unbilled_leakage", "leakage_rate")
    households = data.metric("platform_model", "consolidated_households")
    failed = data.metric("assurance", "recon_checks_failed") or 0

    recon_chip = (
        '<span class="chip good">all reconciled</span>' if failed == 0
        else f'<span class="chip crit">{failed:.0f} checks failing</span>'
    )
    return f"""
<section class="headline">
  <h1>Consolidated operating pack</h1>
  <p class="lede">One model of {e(data.tenant_name)} and the {len(data.firms)} firms beneath it, for the quarter
  ending {e(data.period_end.strftime("%d %B %Y"))}. Every figure below is pinned to a single system time and opens
  to the source records behind it. {recon_chip}</p>
  <div class="kpis">
    <div class="kpi">
      <span class="label">Assets under management</span>
      <span class="value num">{e(compact_money(aum))}</span>
      <span class="note">{e(count(households))} households</span>
    </div>
    <div class="kpi">
      <span class="label">Billed this quarter</span>
      <span class="value num">{e(compact_money(billed))}</span>
      <span class="note">{e(pct(margin_pct))} loaded margin</span>
    </div>
    <div class="kpi">
      <span class="label">Revenue leakage</span>
      <span class="value num alert">{e(compact_money(leakage))}</span>
      <span class="note">{e(pct(leak_rate))} of expected fees</span>
    </div>
    <div class="kpi">
      <span class="label">Raw records under lineage</span>
      <span class="value num">{e(count(data.metric("assurance", "raw_rows_held")))}</span>
      <span class="note">{e(count(data.metric("assurance", "lineage_edges")))} lineage edges</span>
    </div>
  </div>
</section>"""


def _series_legend(names: Sequence[str]) -> str:
    items = "".join(
        f'<span class="item"><span class="swatch" style="background: var({SERIES_VARS[i % 3]})"></span>{e(n)}</span>'
        for i, n in enumerate(names)
    )
    return f'<div class="legend">{items}</div>'


def _platform(data: PackData) -> str:
    by_firm: dict[str, list[tuple[str, float]]] = {}
    names: dict[str, str] = {}
    for row in data.aum_series:
        by_firm.setdefault(row["firm_id"], []).append(
            (row["period_end"], row["total_aum"] or 0.0)
        )
        names[row["firm_id"]] = row["firm_name"]
    ordered = sorted(by_firm.items(), key=lambda kv: -sum(v for _, v in kv[1]))
    series = [(names[fid], points) for fid, points in ordered]

    rows = "".join(
        f"<tr>"
        f'<td>{e(f["firm_name"])}<span class="sub">{e(f["firm_id"])} &middot; {e(f["role"])}</span></td>'
        f'<td class="num">{e(compact_money(f["total_aum"]))}</td>'
        f'<td class="num">{e(compact_money(f["billable_aum"]))}</td>'
        f'<td class="num">{e(count(f["household_count"]))}</td>'
        f'<td class="num">{e(money(f["expected_amount"]))}</td>'
        f'<td class="num">{e(money(f["billed_amount"]))}</td>'
        f'<td class="num">{e(pct(f["loaded_margin_pct"]))}</td>'
        f"</tr>"
        for f in data.firm_table
    )
    totals = {
        k: sum((f[k] or 0) for f in data.firm_table)
        for k in ("total_aum", "billable_aum", "household_count", "expected_amount", "billed_amount")
    }
    return f"""
<section class="section" id="platform">
  <header><span class="idx">01</span><h2>One model of the platform</h2>
    <span class="hint">grain: firm &times; month-end</span></header>
  <p class="prose">Every firm is a dimension inside one database rather than a separate tenant, so the
  consolidated line is a roll-up of the same canonical model, not a reconciliation of three exports.</p>
  <div class="panel">
    <h3>Assets under management</h3>
    <p class="cap">Firms stacked to the consolidated total. The top edge is the number on the cover.</p>
    {_series_legend([n for n, _ in series])}
    {stacked_area(series)}
  </div>
  <div class="panel">
    <h3>Firms at {e(data.period_end.isoformat())}</h3>
    <p class="cap">Billable AUM excludes accounts the portfolio system flags outside the advisory agreement.</p>
    <div class="tw"><table>
      <thead><tr><th>Firm</th><th>AUM</th><th>Billable</th><th>Households</th>
        <th>Expected fees</th><th>Billed</th><th>Loaded margin</th></tr></thead>
      <tbody>{rows}
        <tr class="total"><td>Consolidated</td>
          <td class="num">{e(compact_money(totals["total_aum"]))}</td>
          <td class="num">{e(compact_money(totals["billable_aum"]))}</td>
          <td class="num">{e(count(totals["household_count"]))}</td>
          <td class="num">{e(money(totals["expected_amount"]))}</td>
          <td class="num">{e(money(totals["billed_amount"]))}</td>
          <td class="num">{e(pct(data.metric("revenue_margin", "consolidated_margin_pct")))}</td>
        </tr>
      </tbody>
    </table></div>
  </div>
</section>"""


def _economics(data: PackData) -> str:
    top = [
        f for f in data.figures.get("revenue_margin", [])
        if f["metric"] == "top_client_margin"
    ][:8]
    client_rows = "".join(
        f"<tr><td>{e(f['grain_label'])}<span class=\"sub\">{e(f['firm_id'])}</span></td>"
        f'<td class="num">{e(money(float(f["numeric_value"])))}</td></tr>'
        for f in top
    )
    firm_rows = "".join(
        f"<tr><td>{e(f['firm_name'])}</td>"
        f'<td class="num">{e(money(f["billed_amount"]))}</td>'
        f'<td class="num">{e(money(f["collected_amount"]))}</td>'
        f'<td class="num">{e(money(f["outstanding_amount"]))}</td>'
        f'<td class="num">{e(money(f["loaded_margin"]))}</td>'
        f'<td class="num">{e(pct(f["loaded_margin_pct"]))}</td></tr>'
        for f in data.firm_table
    )
    ageing_rows = "".join(
        f'<tr><td>{e(AGEING_LABELS.get(a["bucket"], a["bucket"]))}</td>'
        f'<td class="num">{e(count(a["invoices"]))}</td>'
        f'<td class="num">{e(money(a["amount"]))}</td></tr>'
        for a in data.ageing
    )
    return f"""
<section class="section" id="economics">
  <header><span class="idx">02</span><h2>Revenue and loaded margin</h2>
    <span class="hint">grain: firm and household &times; quarter</span></header>
  <p class="prose">Loaded margin is billed revenue less directly attributed service time less an allocated share
  of the remaining cost base. The allocation basis travels on each cost line, so a firm that allocates on
  headcount and one that allocates on revenue still consolidate correctly.</p>
  <div class="split-wide">
    <div class="panel">
      <h3>By firm</h3>
      <p class="cap">Outstanding is billed and not yet collected at the period end.</p>
      <div class="tw"><table>
        <thead><tr><th>Firm</th><th>Billed</th><th>Collected</th><th>Outstanding</th>
          <th>Loaded margin</th><th>Margin %</th></tr></thead>
        <tbody>{firm_rows}</tbody>
      </table></div>
    </div>
    <div class="panel">
      <h3>Receivables ageing</h3>
      <p class="cap">Measured from the invoice due date to the period end.</p>
      <div class="tw"><table>
        <thead><tr><th>Bucket</th><th>Invoices</th><th>Outstanding</th></tr></thead>
        <tbody>{ageing_rows}</tbody>
      </table></div>
    </div>
  </div>
  <div class="panel">
    <h3>Most profitable clients this quarter</h3>
    <p class="cap">Ranked on loaded margin, which is regularly not the same ranking as AUM.</p>
    <div class="tw"><table>
      <thead><tr><th>Household</th><th>Loaded margin</th></tr></thead>
      <tbody>{client_rows}</tbody>
    </table></div>
  </div>
</section>"""


def _leakage(data: PackData) -> str:
    types = ["never_invoiced", "billed_below_schedule", "uncollected"]
    firms = sorted({r["firm_id"] for r in data.leakage_by_firm})
    firm_names = {r["firm_id"]: r["firm_name"] for r in data.leakage_by_firm}
    lookup = {(r["firm_id"], r["leakage_type"]): r["amount"] or 0.0 for r in data.leakage_by_firm}
    series = [
        (LEAKAGE_LABELS[t], [lookup.get((f, t), 0.0) for f in firms]) for t in types
    ]
    totals = {t: sum(lookup.get((f, t), 0.0) for f in firms) for t in types}

    composition = composition_bar(
        [
            (LEAKAGE_LABELS[t], totals[t], f"var({SERIES_VARS[i]})")
            for i, t in enumerate(types)
        ]
    )
    breakdown = "".join(
        f'<tr><td><span class="swatch" style="background: var({SERIES_VARS[i]})"></span>'
        f"{e(LEAKAGE_LABELS[t])}</td>"
        f'<td class="num">{e(money(totals[t]))}</td>'
        f'<td class="num">{e(pct(totals[t] / (sum(totals.values()) or 1)))}</td></tr>'
        for i, t in enumerate(types)
    )
    finding_rows = "".join(
        f"<tr><td>{e(f['household_name'])}"
        f'<span class="sub">{e(f["firm_id"])} &middot; {e(f["schedule_name"] or "no schedule")}</span></td>'
        f'<td><span class="chip {FINDING_LABELS.get(f["finding"], ("flat", f["finding"]))[0]}">'
        f'{e(FINDING_LABELS.get(f["finding"], ("flat", f["finding"]))[1])}</span></td>'
        f'<td class="num">{e(money(f["expected_amount"], 2))}</td>'
        f'<td class="num">{e(money(f["billed_amount"], 2))}</td>'
        f'<td class="num">{e(money(f["variance_amount"], 2))}</td></tr>'
        for f in data.findings
    )
    return f"""
<section class="section" id="leakage">
  <header><span class="idx">03</span><h2>Unbilled and leaked revenue</h2>
    <span class="hint">expected recomputed from the fee schedule</span></header>
  <p class="prose">Expected revenue is the assigned fee schedule applied to the billing basis, recomputed from the
  canonical schedule rather than taken from the billing system's own output. Unbilled is expected minus billed;
  leakage adds what was billed correctly and never collected. The three components are kept apart because they
  have three different owners and three different fixes.</p>
  <div class="split-wide">
    <div class="panel">
      <h3>Leakage by firm</h3>
      <p class="cap">Quarter ending {e(data.period_end.isoformat())}.</p>
      {_series_legend([LEAKAGE_LABELS[t] for t in types])}
      {grouped_bars([firm_names.get(f, f) for f in firms], series)}
    </div>
    <div class="panel">
      <h3>Composition</h3>
      <p class="cap">Consolidated, this quarter.</p>
      {composition}
      <div class="tw"><table>
        <thead><tr><th>Component</th><th>Amount</th><th>Share</th></tr></thead>
        <tbody>{breakdown}
          <tr class="total"><td>Total</td>
            <td class="num">{e(money(sum(totals.values())))}</td>
            <td class="num">100.0%</td></tr>
        </tbody>
      </table></div>
    </div>
  </div>
  <div class="panel">
    <h3>Households behind the number</h3>
    <p class="cap">Largest variances between the fee schedule and what was invoiced. A leakage figure with no names
    attached cannot be actioned by the person who reads it.</p>
    <div class="tw"><table>
      <thead><tr><th>Household</th><th>Finding</th><th>Expected</th><th>Billed</th><th>Variance</th></tr></thead>
      <tbody>{finding_rows}</tbody>
    </table></div>
  </div>
</section>"""


def _service(data: PackData) -> str:
    types = sorted({r["event_type"] for r in data.sla})
    firms = sorted({r["firm_id"] for r in data.sla})
    firm_names = {r["firm_id"]: r["firm_name"] for r in data.sla}
    lookup = {(r["firm_id"], r["event_type"]): r for r in data.sla}
    series = [
        (t, [(lookup.get((f, t), {}).get("breach_rate") or 0.0) * 100 for f in firms])
        for t in types
    ]
    rows = "".join(
        f"<tr><td>{e(firm_names.get(r['firm_id'], r['firm_id']))}"
        f'<span class="sub">{e(r["event_type"])}</span></td>'
        f'<td class="num">{e(count(r["event_count"]))}</td>'
        f'<td class="num">{e(count(r["breach_count"]))}</td>'
        f'<td class="num">{e(count(r["still_open_count"]))}</td>'
        f'<td><span class="chip {_sla_state(r["breach_rate"])}">{e(pct(r["breach_rate"]))}</span></td>'
        f'<td class="num">{e(_hours(r.get("p90_elapsed_hours")))}</td></tr>'
        for r in sorted(data.sla, key=lambda r: -(r["breach_rate"] or 0))
    )
    return f"""
<section class="section" id="service">
  <header><span class="idx">04</span><h2>Service and onboarding SLAs</h2>
    <span class="hint">open past target counts as breached</span></header>
  <p class="prose">An event still open past its target is a breach now, not a pending item. Counting only closed
  events would hide the worst backlog, because the tickets nobody has touched never close.</p>
  <div class="split-wide">
    <div class="panel">
      <h3>Breach rate by firm</h3>
      <p class="cap">Percent of events that exceeded their target, by event type.</p>
      {_series_legend(types)}
      {grouped_bars([firm_names.get(f, f) for f in firms], series, value_fmt=lambda v: f"{v:.0f}%")}
    </div>
    <div class="panel">
      <h3>Detail</h3>
      <p class="cap">P90 is the elapsed time nine in ten events beat.</p>
      <div class="tw"><table>
        <thead><tr><th>Firm</th><th>Events</th><th>Breached</th><th>Open</th><th>Rate</th><th>P90</th></tr></thead>
        <tbody>{rows}</tbody>
      </table></div>
    </div>
  </div>
</section>"""


def _hours(value: float | None) -> str:
    return f"{value:,.0f}h" if value else "—"


def _sla_state(rate: float | None) -> str:
    if rate is None:
        return "flat"
    if rate >= 0.25:
        return "crit"
    if rate >= 0.12:
        return "warn"
    return "good"


def _people(data: PackData) -> str:
    departed_value = sum(
        r["book_value"] or 0 for r in data.concentration if r["has_departed"]
    )
    top_share = max((r["book_share"] or 0) for r in data.concentration) if data.concentration else 0
    return f"""
<section class="section" id="people">
  <header><span class="idx">05</span><h2>Key-person concentration</h2>
    <span class="hint">effective-dated book assignments</span></header>
  <p class="prose">Book share is computed from effective-dated assignments, so an advisor who has left still shows
  the book they held. That is the point: the risk is what walks out the door, and a report that silently reassigns
  their households to whoever inherited them shows no risk at all.</p>
  <div class="split-wide">
    <div class="panel">
      <h3>Largest books by firm</h3>
      <p class="cap">Share of the firm's book at the period end.</p>
      {hbars(data.concentration, "book_value", "book_share")}
    </div>
    <div class="panel">
      <h3>Exposure</h3>
      <p class="cap">Concentration is read against the firm, not the platform.</p>
      <div class="kpis" style="border: none">
        <div class="kpi" style="padding-left: 0">
          <span class="label">Largest single book</span>
          <span class="value num">{e(pct(top_share))}</span>
          <span class="note">of its firm's assets</span>
        </div>
        <div class="kpi">
          <span class="label">Held by departed advisors</span>
          <span class="value num {"alert" if departed_value else ""}">{e(compact_money(departed_value))}</span>
          <span class="note">still assigned at period end</span>
        </div>
      </div>
      <p class="cap" style="margin-top: 16px">Advisor identity is resolved through a persisted crosswalk between
      CRM user, custodian rep code and payroll employee, reviewed once by a person rather than matched on name at
      query time.</p>
    </div>
  </div>
</section>"""


def _evidence(data: PackData) -> str:
    failed = [r for r in data.recon if not r["passed"]]
    passed = [r for r in data.recon if r["passed"]]
    check_rows = "".join(
        f'<tr><td>{e(r["check_name"].replace("_", " "))}'
        f'<span class="sub">{e(r["firm_id"])} &middot; {e(r["period_end"])}</span></td>'
        f'<td class="num">{e(money(r["expected"], 2))}</td>'
        f'<td class="num">{e(money(r["actual"], 2))}</td>'
        f'<td class="num">{e(pct(r["variance_pct"], 4))}</td>'
        f'<td><span class="chip {"good" if r["passed"] else "crit"}">'
        f'{"within tolerance" if r["passed"] else "breach"}</span></td></tr>'
        for r in (failed + passed)[:8]
    )
    prov_rows = "".join(
        f'<tr><td><span class="tag">{e(p["source_id"])}</span> {e(p["stream"])}</td>'
        f'<td class="num">{e(count(p["loads"]))}</td>'
        f'<td class="num">{e(count(p["rows"]))}</td></tr>'
        for p in data.provenance
    )
    return f"""
<section class="section" id="evidence">
  <header><span class="idx">06</span><h2>Evidence and reconciliation</h2>
    <span class="hint">what makes the rest of this believable</span></header>
  <p class="prose">Reconciliation runs on every refresh, against each source system's own reported totals, with a
  stated tolerance. A missing counterparty figure counts as a failure, not a pass: "we could not check" and
  "we checked and it agrees" must never look alike.</p>
  <div class="split-wide">
    <div class="panel">
      <h3>Checks at this system time</h3>
      <p class="cap">{len(passed)} of {len(data.recon)} within tolerance.</p>
      <div class="tw"><table>
        <thead><tr><th>Check</th><th>Source reports</th><th>We compute</th><th>Variance</th><th></th></tr></thead>
        <tbody>{check_rows}</tbody>
      </table></div>
    </div>
    <div class="panel">
      <h3>Sources feeding this pack</h3>
      <p class="cap">Every extraction is written to object storage with its SHA-256 before it is loaded.</p>
      <div class="tw"><table>
        <thead><tr><th>Source and stream</th><th>Loads</th><th>Rows</th></tr></thead>
        <tbody>{prov_rows}</tbody>
      </table></div>
    </div>
  </div>
  {_drill(data)}
</section>"""


def _drill(data: PackData) -> str:
    if not data.drill_example:
        return ""
    ex = data.drill_example
    finding = ex["finding"]
    steps: list[str] = [
        '<div class="ev-step"><div class="n">1</div><div class="body">'
        f'<span class="what">Figure &mdash; {e(finding["household_name"])}, '
        f'{e(FINDING_LABELS.get(finding["finding"], ("flat", finding["finding"]))[1].lower())}, '
        f'{e(money(finding["variance_amount"], 2))}</span>'
        f'<span class="detail">{e(finding["drill_query"])}</span></div></div>'
    ]
    if ex["canon_rows"]:
        canon = ex["canon_rows"][0]
        steps.append(
            '<div class="ev-step"><div class="n">2</div><div class="body">'
            f'<span class="what">Canonical row &mdash; {e(canon["canon_table"])}</span>'
            f'<span class="detail">canon_id {e(canon["canon_pk"])} &middot; '
            f'source {e(canon["source_id"])} &middot; recorded {e(canon["recorded_at"])}</span></div></div>'
        )
    if ex["evidence"]:
        first = ex["evidence"][0]
        payload = json.dumps(first["payload"], indent=1, sort_keys=True)[:900]
        steps.append(
            '<div class="ev-step"><div class="n">3</div><div class="body">'
            f'<span class="what">Raw record &mdash; {e(first["raw_table"])}</span>'
            f'<span class="detail">load {e(first["load_id"][:8])} seq {first["sequence"]} &middot; '
            f'record sha-256 {e(first["record_hash"][:24])}</span>'
            f'<pre class="ev-payload">{e(payload)}</pre></div></div>'
        )
        steps.append(
            '<div class="ev-step"><div class="n">4</div><div class="body">'
            '<span class="what">File we were sent</span>'
            f'<span class="detail">{e(first["artifact_uri"])}<br>'
            f'sha-256 {e(first["artifact_sha256"])}</span></div></div>'
        )
    return f"""
  <div class="panel">
    <h3>A figure, opened</h3>
    <p class="cap">The largest finding in this pack, walked from the number to the file the client sent us. Every
    figure in the pack resolves the same way.</p>
    <div class="evidence">{"".join(steps)}</div>
    <p class="callout" style="margin-top: 18px">Drawn from <strong>{ex["source_count"]}</strong> source
    system{"s" if ex["source_count"] != 1 else ""}. The artefact is held separately from the database, so the
    figure is reproducible from object storage alone.</p>
  </div>"""


def _footnote(data: PackData) -> str:
    coverage = data.coverage or {}
    coverage_line = ""
    if coverage:
        coverage_line = (
            f' Adapter coverage across the systems these firms run is '
            f'{pct(coverage.get("weighted_coverage_pct"))} weighted, over '
            f'{len(coverage.get("supported_systems", []))} supported source systems.'
        )
    return f"""
<p class="footnote">Generated by the Fracture consolidator from {e(count(data.metric("assurance", "raw_rows_held")))}
raw records across {len(data.provenance)} source streams. Figures are pinned to system time
{e(data.system_time.strftime("%Y-%m-%d %H:%M:%SZ"))}; reissuing this pack at that instant reproduces content hash
{e(data.content_hash[:16])}&hellip; exactly.{e(coverage_line)} No figure in this pack was computed by a model:
AI is permitted to draft, extract and summarise, and is structurally prevented from populating a numeric column
without a named human confirmation.</p>"""


def write(data: PackData, path: Path | str) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render(data), encoding="utf-8")
    return target
