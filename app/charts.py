"""Dependency-free SVG chart builders (server-rendered, themeable, offline-safe).

Each function returns an SVG string with a viewBox so it scales to its container
width via CSS. Colours are passed in so the templates control the palette.
"""
from __future__ import annotations

import math
from decimal import Decimal

# Chart-friendly palette derived from the app accent (#3b82f6) + complements.
SERIES = [
    "#3b82f6", "#22d3ee", "#a78bfa", "#34d399", "#f59e0b",
    "#f87171", "#ec4899", "#84cc16", "#fbbf24", "#60a5fa",
]


def _f(n) -> float:
    return float(Decimal(str(n or 0)))


def _money(n) -> str:
    return f"{Decimal(str(n or 0)):,.0f}"


def _empty(width: int, height: int, label: str = "No data yet") -> str:
    return (
        f'<svg viewBox="0 0 {width} {height}" class="chart" role="img" '
        f'preserveAspectRatio="xMidYMid meet">'
        f'<text x="{width/2}" y="{height/2}" class="chart-empty" '
        f'text-anchor="middle" dominant-baseline="middle">{label}</text></svg>'
    )


# ── line / area: liquid-cash trend ────────────────────────────────────────────

def line_chart(points: list[tuple[str, float]], width: int = 760, height: int = 220,
               pad_x: int = 16, pad_y: int = 22, color: str = "#3b82f6",
               grad_id: str = "nwgrad") -> str:
    pts = [(lbl, _f(v)) for lbl, v in points]
    if not pts:
        return _empty(width, height)

    values = [v for _, v in pts]
    vmin, vmax = min(values), max(values)
    span = (vmax - vmin) or (abs(vmax) or 1)
    # pad the value range a little so the line isn't glued to the edges
    vmin -= span * 0.12
    vmax += span * 0.12
    vrange = (vmax - vmin) or 1

    plot_w = width - 2 * pad_x
    plot_h = height - 2 * pad_y
    n = len(pts)

    def x(i: int) -> float:
        return pad_x + (plot_w * (i / (n - 1)) if n > 1 else plot_w / 2)

    def y(v: float) -> float:
        return pad_y + plot_h * (1 - (v - vmin) / vrange)

    coords = [(x(i), y(v)) for i, (_, v) in enumerate(pts)]
    line_pts = " ".join(f"{px:.1f},{py:.1f}" for px, py in coords)
    area = (f"M {coords[0][0]:.1f},{height - pad_y:.1f} "
            + " ".join(f"L {px:.1f},{py:.1f}" for px, py in coords)
            + f" L {coords[-1][0]:.1f},{height - pad_y:.1f} Z")

    # subtle horizontal gridlines (min / mid / max)
    grid = ""
    for frac in (0.0, 0.5, 1.0):
        gy = pad_y + plot_h * frac
        grid += f'<line x1="{pad_x}" y1="{gy:.1f}" x2="{width - pad_x}" y2="{gy:.1f}" class="chart-grid"/>'

    dots = "".join(
        f'<circle cx="{px:.1f}" cy="{py:.1f}" r="{3.5 if i == n-1 else 2.2}" '
        f'fill="{color}" class="{ "chart-dot-last" if i == n-1 else "chart-dot" }"/>'
        for i, (px, py) in enumerate(coords)
    )

    # axis labels: max/min on the left, first/last date on the bottom
    lbl_max = f'<text x="{pad_x}" y="{pad_y - 6}" class="chart-axis">{_money(vmax - span*0.12)}</text>'
    lbl_min = f'<text x="{pad_x}" y="{height - 6}" class="chart-axis">{_money(vmin + span*0.12)}</text>'
    lbl_first = f'<text x="{pad_x}" y="{height - 6}" class="chart-axis" text-anchor="start">{pts[0][0]}</text>'
    lbl_last = f'<text x="{width - pad_x}" y="{height - 6}" class="chart-axis" text-anchor="end">{pts[-1][0]}</text>'
    last_val = f'<text x="{coords[-1][0]-4:.1f}" y="{coords[-1][1]-9:.1f}" class="chart-axis chart-value" text-anchor="end">{_money(values[-1])}</text>'

    return (
        f'<svg viewBox="0 0 {width} {height}" class="chart" role="img" '
        f'preserveAspectRatio="xMidYMid meet">'
        f'<defs><linearGradient id="{grad_id}" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0%" stop-color="{color}" stop-opacity="0.35"/>'
        f'<stop offset="100%" stop-color="{color}" stop-opacity="0.02"/>'
        f'</linearGradient></defs>'
        f'{grid}'
        f'<path d="{area}" fill="url(#{grad_id})"/>'
        f'<polyline points="{line_pts}" fill="none" stroke="{color}" '
        f'stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"/>'
        f'{dots}{lbl_first}{lbl_last}{last_val}'
        f'</svg>'
    )


# ── donut: spend by category ────────────────────────────────────────────────

def _polar(cx: float, cy: float, r: float, deg: float) -> tuple[float, float]:
    rad = math.radians(deg)
    return cx + r * math.cos(rad), cy + r * math.sin(rad)


def donut_chart(segments: list[dict], size: int = 220, thickness: int = 30,
                center_label: str = "", center_sub: str = "") -> str:
    """segments: [{'value': number, 'color': hex}] (order preserved)."""
    segs = [s for s in segments if _f(s["value"]) > 0]
    total = sum(_f(s["value"]) for s in segs)
    cx = cy = size / 2
    R = size / 2 - 2
    r = R - thickness

    if total <= 0:
        return _empty(size, size)

    if len(segs) == 1:
        paths = (
            f'<circle cx="{cx}" cy="{cy}" r="{(R + r) / 2:.1f}" fill="none" '
            f'stroke="{segs[0]["color"]}" stroke-width="{thickness}"/>'
        )
    else:
        paths = ""
        angle = -90.0
        for s in segs:
            frac = _f(s["value"]) / total
            end = angle + frac * 360.0
            large = 1 if (end - angle) > 180 else 0
            x0, y0 = _polar(cx, cy, R, angle)
            x1, y1 = _polar(cx, cy, R, end)
            xi1, yi1 = _polar(cx, cy, r, end)
            xi0, yi0 = _polar(cx, cy, r, angle)
            paths += (
                f'<path d="M {x0:.2f} {y0:.2f} A {R:.2f} {R:.2f} 0 {large} 1 {x1:.2f} {y1:.2f} '
                f'L {xi1:.2f} {yi1:.2f} A {r:.2f} {r:.2f} 0 {large} 0 {xi0:.2f} {yi0:.2f} Z" '
                f'fill="{s["color"]}"/>'
            )
            angle = end

    label = (f'<text x="{cx}" y="{cy - 4}" class="donut-center" text-anchor="middle">{center_label}</text>'
             if center_label else "")
    sub = (f'<text x="{cx}" y="{cy + 16}" class="donut-sub" text-anchor="middle">{center_sub}</text>'
           if center_sub else "")

    return (
        f'<svg viewBox="0 0 {size} {size}" class="chart donut" role="img" '
        f'preserveAspectRatio="xMidYMid meet">{paths}{label}{sub}</svg>'
    )


# ── bars: daily spend trend ─────────────────────────────────────────────────

def bar_chart(bars: list[dict], width: int = 760, height: int = 200,
              pad_x: int = 16, pad_y: int = 18, color: str = "#3b82f6",
              hi_color: str = "#22d3ee") -> str:
    """bars: [{'label': str, 'value': number, 'show_label': bool}]."""
    if not bars:
        return _empty(width, height)

    values = [_f(b["value"]) for b in bars]
    vmax = max(values) or 1
    plot_w = width - 2 * pad_x
    plot_h = height - 2 * pad_y
    n = len(bars)
    gap = max(2, plot_w / n * 0.22)
    bw = (plot_w - gap * (n - 1)) / n
    peak = max(range(n), key=lambda i: values[i])

    rects = ""
    labels = ""
    value_labels = ""
    for i, b in enumerate(bars):
        v = values[i]
        bh = (plot_h * (v / vmax)) if vmax else 0
        bx = pad_x + i * (bw + gap)
        by = pad_y + (plot_h - bh)
        fill = hi_color if i == peak and v > 0 else color
        rects += (
            f'<rect x="{bx:.1f}" y="{by:.1f}" width="{bw:.1f}" height="{max(bh,0):.1f}" '
            f'rx="2" fill="{fill}" class="bar"><title>{b["label"]}: {_money(v)}</title></rect>'
        )
        if b.get("show_label"):
            labels += (
                f'<text x="{bx + bw/2:.1f}" y="{height - 4}" class="chart-axis" '
                f'text-anchor="middle">{b["label"]}</text>'
            )
        if v > 0:
            value_labels += (
                f'<text x="{bx + bw/2:.1f}" y="{by - 3:.1f}" '
                f'class="chart-value" text-anchor="middle">{_money(v)}</text>'
            )
    return (
        f'<svg viewBox="0 0 {width} {height}" class="chart" role="img" '
        f'preserveAspectRatio="xMidYMid meet">{rects}{labels}{value_labels}</svg>'
    )
