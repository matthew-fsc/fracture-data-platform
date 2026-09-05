"""Departmental operating dashboards.

Six views, one per group that owns a set of decisions. The board pack answers
"how did the platform do"; these answer "which firm, and what do I do on Monday".

Every view compares firms on a normalised measure. That is the whole design
constraint: the platform firm bills nearly five times the smallest add-on, so
any view built on absolute figures ranks them by size and teaches the reader
nothing. Rates, per-unit figures and basis points on AUM put a $400m firm and a
$1.7bn firm on the same axis.
"""

from __future__ import annotations

import html
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

from fracture.pack.dashboard_data import DashboardData

TEMPLATES = Path(__file__).parent / "templates"
SERIES = ("--series-1", "--series-2", "--series-3")


@dataclass(frozen=True)
class Department:
    key: str
    index: str
    title: str
    owner: str
    lede: str


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


# -- formatting --------------------------------------------------------------


def e(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def money(value: float | None, places: int = 0, dash: str = "—") -> str:
    if value is None:
        return dash
    sign = "-" if value < 0 else ""
    return f"{sign}${abs(value):,.{places}f}"


def compact(value: float | None, dash: str = "—") -> str:
    if value is None:
        return dash
    sign = "-" if value < 0 else ""
    v = abs(value)
    for cut, suffix, places in ((1e9, "bn", 2), (1e6, "m", 1), (1e3, "k", 1)):
        if v >= cut:
            return f"{sign}${v / cut:,.{places}f}{suffix}"
    return f"{sign}${v:,.0f}"


def pct(value: float | None, places: int = 1, dash: str = "—") -> str:
    return dash if value is None else f"{value * 100:.{places}f}%"


def pp(value: float | None, places: int = 1, dash: str = "—") -> str:
    """Percentage points, signed. Used for variance against the platform."""
    if value is None:
        return dash
    return f"{value * 100:+.{places}f}pp"


def bps(value: float | None, places: int = 1, dash: str = "—") -> str:
    return dash if value is None else f"{value:,.{places}f}bps"


def count(value: float | None, dash: str = "—") -> str:
    return dash if value is None else f"{value:,.0f}"


def hours(value: float | None, dash: str = "—") -> str:
    if value is None:
        return dash
    if value >= 48:
        return f"{value / 24:,.1f}d"
    return f"{value:,.0f}h"


UNIT_FORMAT: dict[str, Callable[[float | None], str]] = {
    "bps": bps, "ratio": pct, "usd": lambda v: money(v, 0),
    "count": count, "hours": hours,
}


def fmt_unit(value: float | None, unit: str) -> str:
    return UNIT_FORMAT.get(unit, lambda v: count(v))(value)


def _nice(value: float) -> float:
    import math

    if value <= 0:
        return 1.0
    mag = 10 ** math.floor(math.log10(value))
    for step in (1, 1.25, 1.5, 2, 2.5, 3, 4, 5, 7.5, 10):
        if value <= step * mag:
            return step * mag
    return 10 * mag


# -- reusable marks ----------------------------------------------------------


def firm_colour(firm_id: str, order: Sequence[str]) -> str:
    idx = order.index(firm_id) if firm_id in order else 0
    return f"var({SERIES[idx % len(SERIES)]})"


def hbar_group(
    rows: Sequence[tuple[str, float | None, str]],
    peer: float | None = None,
    formatter: Callable[[float | None], str] = pct,
    peer_label: str = "platform",
) -> str:
    """Horizontal comparison bars with the platform figure marked on each track.

    The peer mark is the point: a firm at 78% means nothing until you can see
    the platform is at 91% without looking anything up.
    """
    values = [v for _, v, _ in rows if v is not None]
    if not values:
        return '<p class="cap">no data</p>'
    peak = max(values + ([peer] if peer is not None else []))
    peak = peak if peak > 0 else 1.0
    out = []
    for name, value, colour in rows:
        width = 0 if value is None else max(0.0, min(100.0, value / peak * 100))
        mark = ""
        if peer is not None and peer > 0:
            mark = (
                f'<span class="peer-mark" style="left: {min(99.5, peer / peak * 100):.2f}%" '
                f'title="{e(peer_label)} {e(formatter(peer))}"></span>'
            )
        out.append(
            '<div class="hbar">'
            f'<span class="name">{e(name)}</span>'
            f'<span class="track">{mark}<span class="fill" style="width:{width:.2f}%;'
            f'background:{colour}"></span></span>'
            f'<span class="val">{e(formatter(value))}</span>'
            "</div>"
        )
    if peer is not None:
        out.append(
            '<div class="hbar"><span class="name" style="color:var(--faint)">'
            f'{e(peer_label)}</span><span class="track" style="background:transparent;'
            'border-top:1px dashed var(--rule-strong);height:1px"></span>'
            f'<span class="val" style="color:var(--muted)">{e(formatter(peer))}</span></div>'
        )
    return f'<div class="hbars">{"".join(out)}</div>'


#: Step labels shortened for the per-firm bridges, which render at roughly half
#: the width of the consolidated one and collide at full length.
SHORT_STEP = {
    "Schedule entitlement": "Schedule",
    "Never invoiced": "Never inv.",
    "Billed below schedule": "Below sch.",
    "Billed above schedule": "Above sch.",
    "Not collected": "Uncollected",
}


def waterfall(
    steps: Sequence[dict[str, Any]], width: int = 560, height: int = 230,
    short_labels: bool = False,
) -> str:
    """The yield bridge, drawn to one scale.

    Totals sit on the baseline; losses and gains float between the running
    total before and after. Every bar is labelled with its own value, because a
    waterfall whose steps you have to subtract by eye is a decoration.
    """
    if not steps:
        return ""
    pad_l, pad_r, pad_t, pad_b = 46, 12, 16, 44
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b

    running = 0.0
    bars: list[dict[str, Any]] = []
    for step in steps:
        if step["step_kind"] == "total":
            value = float(step["bps"] or 0)
            label = SHORT_STEP.get(step["label"], step["label"]) if short_labels else step["label"]
            bars.append({"lo": 0.0, "hi": value, "kind": "total", "label": label,
                         "text": bps(value), "delta": None})
            running = value
        else:
            delta = float(step["delta_bps"] or 0)
            lo, hi = (running + delta, running) if delta < 0 else (running, running + delta)
            label = SHORT_STEP.get(step["label"], step["label"]) if short_labels else step["label"]
            bars.append({"lo": lo, "hi": hi, "kind": step["step_kind"], "label": label,
                         "text": f"{delta:+.2f}", "delta": delta})
            running += delta

    y_max = _nice(max(b["hi"] for b in bars) * 1.12)
    slot = plot_w / len(bars)
    bar_w = min(52.0, slot * 0.6)

    parts: list[str] = []
    for frac in (0, 0.5, 1.0):
        gy = pad_t + plot_h * (1 - frac)
        parts.append(
            f'<line class="grid-line" x1="{pad_l}" y1="{gy:.1f}" x2="{width - pad_r}" y2="{gy:.1f}"/>'
            f'<text class="axis-label" x="{pad_l - 7}" y="{gy + 3.5:.1f}" text-anchor="end">'
            f"{y_max * frac:.0f}</text>"
        )

    def y(v: float) -> float:
        return pad_t + plot_h * (1 - v / y_max)

    colour_for = {"total": "var(--accent)", "loss": "var(--crit)", "gain": "var(--good)"}
    for i, bar in enumerate(bars):
        cx = pad_l + slot * (i + 0.5)
        x = cx - bar_w / 2
        top, bottom = y(bar["hi"]), y(bar["lo"])
        h = max(2.0, bottom - top)
        parts.append(
            f'<rect x="{x:.1f}" y="{top:.1f}" width="{bar_w:.1f}" height="{h:.1f}" rx="2" '
            f'fill="{colour_for[bar["kind"]]}" '
            f'fill-opacity="{0.95 if bar["kind"] == "total" else 0.85}">'
            f'<title>{e(bar["label"])}: {e(bar["text"])}</title></rect>'
        )
        parts.append(
            f'<text class="val-label" x="{cx:.1f}" y="{top - 5:.1f}" text-anchor="middle">'
            f'{e(bar["text"])}</text>'
        )
        words = str(bar["label"]).split()
        mid = (len(words) + 1) // 2
        line1, line2 = " ".join(words[:mid]), " ".join(words[mid:])
        parts.append(
            f'<text class="axis-label" x="{cx:.1f}" y="{height - 26}" text-anchor="middle">'
            f"{e(line1)}</text>"
        )
        if line2:
            parts.append(
                f'<text class="axis-label" x="{cx:.1f}" y="{height - 15}" text-anchor="middle">'
                f"{e(line2)}</text>"
            )
    parts.append(
        f'<line class="axis-line" x1="{pad_l}" y1="{pad_t + plot_h}" '
        f'x2="{width - pad_r}" y2="{pad_t + plot_h}"/>'
    )
    return (
        f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" '
        'aria-label="Yield bridge from schedule entitlement to cash collected, in basis points">'
        + "".join(parts) + "</svg>"
    )


def scatter(
    points: Sequence[dict[str, Any]],
    x_label: str,
    y_label: str,
    x_fmt: Callable[[float | None], str],
    y_fmt: Callable[[float | None], str],
    width: int = 520,
    height: int = 320,
    x_ref: float | None = None,
    y_ref: float | None = None,
) -> str:
    """Two measures, one mark per firm, with reference lines cutting quadrants.

    Used for pricing against execution, which is the one comparison that
    separates "charges too little" from "charges correctly and fails to bill".
    """
    if not points:
        return ""
    pad_l, pad_r, pad_t, pad_b = 58, 96, 18, 46
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b
    xs = [p["x"] for p in points if p["x"] is not None]
    ys = [p["y"] for p in points if p["y"] is not None]
    if not xs or not ys:
        return ""
    x_lo, x_hi = min(xs + ([x_ref] if x_ref else [])), max(xs + ([x_ref] if x_ref else []))
    y_lo, y_hi = min(ys + ([y_ref] if y_ref else [])), max(ys + ([y_ref] if y_ref else []))
    x_pad = (x_hi - x_lo) * 0.28 or abs(x_hi) * 0.1 or 1
    y_pad = (y_hi - y_lo) * 0.28 or abs(y_hi) * 0.1 or 1
    x_lo, x_hi = x_lo - x_pad, x_hi + x_pad
    y_lo, y_hi = y_lo - y_pad, y_hi + y_pad

    def px(v: float) -> float:
        return pad_l + plot_w * (v - x_lo) / (x_hi - x_lo)

    def py(v: float) -> float:
        return pad_t + plot_h * (1 - (v - y_lo) / (y_hi - y_lo))

    parts: list[str] = []
    for frac in (0, 0.5, 1.0):
        gy = pad_t + plot_h * (1 - frac)
        parts.append(
            f'<line class="grid-line" x1="{pad_l}" y1="{gy:.1f}" x2="{pad_l + plot_w}" y2="{gy:.1f}"/>'
        )
        parts.append(
            f'<text class="axis-label" x="{pad_l - 7}" y="{gy + 3.5:.1f}" text-anchor="end">'
            f"{e(y_fmt(y_lo + (y_hi - y_lo) * frac))}</text>"
        )
    for frac in (0, 0.5, 1.0):
        gx = pad_l + plot_w * frac
        parts.append(
            f'<text class="axis-label" x="{gx:.1f}" y="{height - 26}" text-anchor="middle">'
            f"{e(x_fmt(x_lo + (x_hi - x_lo) * frac))}</text>"
        )
    if x_ref is not None:
        parts.append(
            f'<line class="zero-line" x1="{px(x_ref):.1f}" y1="{pad_t}" '
            f'x2="{px(x_ref):.1f}" y2="{pad_t + plot_h}"/>'
        )
    if y_ref is not None:
        parts.append(
            f'<line class="zero-line" x1="{pad_l}" y1="{py(y_ref):.1f}" '
            f'x2="{pad_l + plot_w}" y2="{py(y_ref):.1f}"/>'
        )
    for point in points:
        if point["x"] is None or point["y"] is None:
            continue
        cx, cy = px(point["x"]), py(point["y"])
        parts.append(
            f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="7" fill="{point["colour"]}" '
            f'stroke="var(--surface)" stroke-width="2">'
            f'<title>{e(point["label"])}: {e(x_fmt(point["x"]))} / {e(y_fmt(point["y"]))}</title>'
            "</circle>"
        )
        anchor = "start" if cx < pad_l + plot_w * 0.75 else "end"
        dx = 12 if anchor == "start" else -12
        parts.append(
            f'<text class="val-label" x="{cx + dx:.1f}" y="{cy + 4:.1f}" text-anchor="{anchor}">'
            f'{e(point["label"])}</text>'
        )
    parts.append(
        f'<line class="axis-line" x1="{pad_l}" y1="{pad_t + plot_h}" '
        f'x2="{pad_l + plot_w}" y2="{pad_t + plot_h}"/>'
    )
    parts.append(
        f'<text class="axis-label" x="{pad_l + plot_w / 2:.1f}" y="{height - 6}" '
        f'text-anchor="middle">{e(x_label)}</text>'
    )
    parts.append(
        f'<text class="axis-label" x="{14}" y="{pad_t + plot_h / 2:.1f}" text-anchor="middle" '
        f'transform="rotate(-90 14 {pad_t + plot_h / 2:.1f})">{e(y_label)}</text>'
    )
    return (
        f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="{e(y_label)} against {e(x_label)}, one mark per firm">'
        + "".join(parts) + "</svg>"
    )


def grouped_bars(
    categories: Sequence[str],
    series: Sequence[tuple[str, list[float | None], str]],
    formatter: Callable[[float | None], str] = pct,
    width: int = 560,
    height: int = 220,
) -> str:
    if not categories or not series:
        return ""
    pad_l, pad_r, pad_t, pad_b = 52, 10, 16, 34
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b
    flat = [v for _, vals, _ in series for v in vals if v is not None]
    if not flat:
        return ""
    y_max = _nice(max(flat) * 1.15)
    group_w = plot_w / len(categories)
    gap = 2
    bar_w = max(6.0, (group_w * 0.7 - gap * (len(series) - 1)) / len(series))

    parts: list[str] = []
    for frac in (0, 0.5, 1.0):
        gy = pad_t + plot_h * (1 - frac)
        parts.append(
            f'<line class="grid-line" x1="{pad_l}" y1="{gy:.1f}" x2="{width - pad_r}" y2="{gy:.1f}"/>'
            f'<text class="axis-label" x="{pad_l - 7}" y="{gy + 3.5:.1f}" text-anchor="end">'
            f"{e(formatter(y_max * frac))}</text>"
        )
    for ci, category in enumerate(categories):
        block = bar_w * len(series) + gap * (len(series) - 1)
        start = pad_l + group_w * ci + (group_w - block) / 2
        for si, (name, values, colour) in enumerate(series):
            value = values[ci] if ci < len(values) else None
            if value is None:
                continue
            bar_h = plot_h * (value / y_max) if y_max else 0
            bx = start + si * (bar_w + gap)
            by = pad_t + plot_h - bar_h
            parts.append(
                f'<rect x="{bx:.1f}" y="{by:.1f}" width="{bar_w:.1f}" '
                f'height="{max(bar_h, 1):.1f}" rx="2" fill="{colour}">'
                f'<title>{e(name)} — {e(category)}: {e(formatter(value))}</title></rect>'
            )
        parts.append(
            f'<text class="axis-label" x="{pad_l + group_w * (ci + 0.5):.1f}" y="{height - 12}" '
            f'text-anchor="middle">{e(category)}</text>'
        )
    parts.append(
        f'<line class="axis-line" x1="{pad_l}" y1="{pad_t + plot_h}" '
        f'x2="{width - pad_r}" y2="{pad_t + plot_h}"/>'
    )
    return (
        f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" '
        'aria-label="Grouped comparison by firm">' + "".join(parts) + "</svg>"
    )


def lines(
    series: Sequence[tuple[str, list[tuple[str, float | None]], str]],
    formatter: Callable[[float | None], str] = bps,
    width: int = 560,
    height: int = 210,
) -> str:
    if not series:
        return ""
    dates = [d for d, _ in series[0][1]]
    n = len(dates)
    if n < 2:
        return ""
    pad_l, pad_r, pad_t, pad_b = 52, 14, 14, 28
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b
    flat = [v for _, pts, _ in series for _, v in pts if v is not None]
    if not flat:
        return ""
    y_hi = _nice(max(flat) * 1.08)
    y_lo = 0.0

    def x(i: int) -> float:
        return pad_l + plot_w * i / (n - 1)

    def y(v: float) -> float:
        return pad_t + plot_h * (1 - (v - y_lo) / (y_hi - y_lo))

    parts: list[str] = []
    for frac in (0, 0.5, 1.0):
        gy = pad_t + plot_h * (1 - frac)
        parts.append(
            f'<line class="grid-line" x1="{pad_l}" y1="{gy:.1f}" x2="{width - pad_r}" y2="{gy:.1f}"/>'
            f'<text class="axis-label" x="{pad_l - 7}" y="{gy + 3.5:.1f}" text-anchor="end">'
            f"{e(formatter(y_lo + (y_hi - y_lo) * frac))}</text>"
        )
    for name, points, colour in series:
        d = " ".join(
            f"{'M' if i == 0 else 'L'}{x(i):.1f},{y(v):.1f}"
            for i, (_, v) in enumerate(points) if v is not None
        )
        parts.append(
            f'<path d="{d}" fill="none" stroke="{colour}" stroke-width="2" '
            'stroke-linejoin="round" stroke-linecap="round"/>'
        )
        last = [(i, v) for i, (_, v) in enumerate(points) if v is not None]
        if last:
            i, v = last[-1]
            parts.append(
                f'<circle cx="{x(i):.1f}" cy="{y(v):.1f}" r="3.5" fill="{colour}" '
                'stroke="var(--surface)" stroke-width="1.5"/>'
            )
    step = max(1, (n - 1) // 4)
    import datetime as _dt

    for i in range(0, n, step):
        label = _dt.date.fromisoformat(dates[i]).strftime("%b %y")
        anchor = "start" if i == 0 else ("end" if i >= n - 1 else "middle")
        parts.append(
            f'<text class="axis-label" x="{x(i):.1f}" y="{height - 8}" '
            f'text-anchor="{anchor}">{e(label)}</text>'
        )
    parts.append(
        f'<line class="axis-line" x1="{pad_l}" y1="{pad_t + plot_h}" '
        f'x2="{width - pad_r}" y2="{pad_t + plot_h}"/>'
    )
    return (
        f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" '
        'aria-label="Trend by firm">' + "".join(parts) + "</svg>"
    )


def legend(names: Sequence[str], colours: Sequence[str]) -> str:
    items = "".join(
        f'<span class="item"><span class="swatch" style="background:{c}"></span>{e(n)}</span>'
        for n, c in zip(names, colours)
    )
    return f'<div class="legend">{items}</div>'


def delta_chip(value: float | None, direction: str, unit: str = "ratio") -> str:
    """A change, coloured by whether it is good rather than by its sign."""
    if value is None or direction == "neutral":
        return ""
    good = (value > 0) if direction == "higher_better" else (value < 0)
    cls = "flat" if abs(value) < 0.0005 else ("up" if good else "down")
    arrow = "→" if cls == "flat" else ("↑" if value > 0 else "↓")
    return f'<span class="delta {cls}">{arrow} {abs(value) * 100:.1f}%</span>'


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


def render(data: DashboardData) -> str:
    css = (TEMPLATES / "dashboard.css").read_text()

    nav = "".join(
        f'<button type="button" role="tab" id="tab-{d.key}" data-view="{d.key}" '
        f'aria-controls="view-{d.key}" aria-selected="{"true" if i == 0 else "false"}">'
        f'<span class="idx">{d.index}</span><span>{e(d.title)}</span></button>'
        for i, d in enumerate(DEPARTMENTS)
    )

    views = []
    for i, dept in enumerate(DEPARTMENTS):
        body = VIEWS[dept.key](data)
        views.append(
            f'<section class="view" id="view-{dept.key}" role="tabpanel" '
            f'aria-labelledby="tab-{dept.key}"{"" if i == 0 else " hidden"}>'
            '<header class="viewhead">'
            f"<h1>{e(dept.title)}</h1>"
            f'<p class="lede">{e(dept.lede)}</p>'
            f'<p class="owner">Owner: {e(dept.owner)} &middot; quarter ending '
            f"{e(data.period_end)} &middot; {len(data.firms)} firms</p>"
            "</header>"
            f"{body}"
            "</section>"
        )

    firm_lines = "".join(
        f'<div>{e(f["firm_id"])} &middot; {e(compact(f["total_aum"]))}</div>'
        for f in data.firms
    )

    return f"""<title>Meridian Operating Console</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;500;600&family=Source+Serif+4:opsz,wght@8..60,400;8..60,600&display=swap">
<style>{css}</style>
<div class="shell">
  <aside class="rail">
    <div class="brand">
      <span class="mark">Fracture Systems</span>
      <span class="tenant">{e(data.tenant_name)}</span>
      <span class="sub">Operating console</span>
    </div>
    <nav class="deptnav" role="tablist" aria-label="Departments">{nav}</nav>
    <div class="rail-note">
      <div><strong>Quarter ending</strong><br>{e(data.period_end)}</div>
      <div>{firm_lines}</div>
      <div>Every measure here is a rate, a per-unit figure or basis points on AUM, so firms
      of different size compare directly.</div>
    </div>
  </aside>
  <main class="main">
    {"".join(views)}
    <p class="foot">Figures are current as at {e(data.generated_at)}Z, drawn from the same
    canonical model as the issued board pack and reconciled against each source system's own
    reported totals. Yields are annualised: quarterly amounts multiplied by four over average
    assets. No figure on this page was computed by a model.</p>
  </main>
</div>
<script>
(function () {{
  var tabs = Array.prototype.slice.call(document.querySelectorAll('.deptnav button'));
  var views = {{}};
  tabs.forEach(function (tab) {{
    views[tab.dataset.view] = document.getElementById('view-' + tab.dataset.view);
  }});

  function show(key, push) {{
    tabs.forEach(function (tab) {{
      var on = tab.dataset.view === key;
      tab.setAttribute('aria-selected', on ? 'true' : 'false');
      if (views[tab.dataset.view]) views[tab.dataset.view].hidden = !on;
    }});
    try {{ localStorage.setItem('fracture.dept', key); }} catch (err) {{ /* private mode */ }}
    if (push && window.history && window.history.replaceState) {{
      window.history.replaceState(null, '', '#' + key);
    }}
    window.scrollTo(0, 0);
  }}

  tabs.forEach(function (tab, index) {{
    tab.addEventListener('click', function () {{ show(tab.dataset.view, true); }});
    tab.addEventListener('keydown', function (event) {{
      var next = null;
      if (event.key === 'ArrowDown' || event.key === 'ArrowRight') next = tabs[index + 1];
      if (event.key === 'ArrowUp' || event.key === 'ArrowLeft') next = tabs[index - 1];
      if (next) {{ event.preventDefault(); next.focus(); show(next.dataset.view, true); }}
    }});
  }});

  // Deep link wins over the remembered tab, so a shared URL opens where the
  // sender meant rather than where the recipient last was.
  var fromHash = (window.location.hash || '').replace('#', '');
  var remembered = null;
  try {{ remembered = localStorage.getItem('fracture.dept'); }} catch (err) {{ remembered = null; }}
  var initial = views[fromHash] ? fromHash : (views[remembered] ? remembered : null);
  if (initial) show(initial, false);
}})();
</script>
"""


def write(data: DashboardData, path: Path | str) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render(data), encoding="utf-8")
    return target
