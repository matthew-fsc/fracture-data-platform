"""Chart marks and number formatting for rendered views.

Chassis, not domain: a waterfall, a scatter with a fitted trend, grouped bars
and a horizontal bar group with a peer mark are the same drawings whatever is
plotted on them, and the number formats (basis points, compact currency,
percentage points) are shared by every pack.

Nothing here knows what is being measured. The `Department` record is here
rather than in a pack because its shape -- a key, an index, a title, the group
that owns the decisions, and one line saying what the view is for -- is a
chassis convention that every pack fills in.
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

TEMPLATES = Path(__file__).parent / "templates"
SERIES = ("--series-1", "--series-2", "--series-3")


@dataclass(frozen=True)
class Department:
    key: str
    index: str
    title: str
    owner: str
    lede: str


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
    fmt: Callable[[float | None], str] | None = None,
    delta_fmt: Callable[[float], str] | None = None,
    short_step: Mapping[str, str] | None = None,
    aria: str = (
        "Yield bridge from schedule entitlement to cash collected, in basis points"
    ),
) -> str:
    """A bridge, drawn to one scale.

    Totals sit on the baseline; losses and gains float between the running
    total before and after. Every bar is labelled with its own value, because a
    waterfall whose steps you have to subtract by eye is a decoration.

    `fmt` and `delta_fmt` are parameters because this is chassis: the wealth
    pack bridges basis points on AUM and the services pack bridges money, and
    a copy of this function per unit would be two places to fix the next layout
    bug in.
    """
    fmt = fmt or bps
    delta_fmt = delta_fmt or (lambda v: f"{v:+.2f}")
    labels = SHORT_STEP if short_step is None else short_step
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
            label = labels.get(step["label"], step["label"]) if short_labels else step["label"]
            bars.append({"lo": 0.0, "hi": value, "kind": "total", "label": label,
                         "text": fmt(value), "delta": None})
            running = value
        else:
            delta = float(step["delta_bps"] or 0)
            lo, hi = (running + delta, running) if delta < 0 else (running, running + delta)
            label = labels.get(step["label"], step["label"]) if short_labels else step["label"]
            bars.append({"lo": lo, "hi": hi, "kind": step["step_kind"], "label": label,
                         "text": delta_fmt(delta), "delta": delta})
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
        f'aria-label="{e(aria)}">'
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
