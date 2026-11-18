"""Gráficos de barras en SVG, sin dependencias. Fondo blanco: se leen igual en modo oscuro."""

from __future__ import annotations

import math
from html import escape
from pathlib import Path

PALETTE = ("#9aa4b2", "#e0913b", "#2f7d5b", "#4c72b0")


def bar_chart(
    path: Path,
    title: str,
    groups: list[str],
    series: dict[str, list[float]],
    *,
    as_percent: bool = True,
    colors: tuple[str, ...] = PALETTE,
) -> None:
    """Barras agrupadas: un grupo por categoría y una barra por serie."""
    width, height = 640, 340
    left, right, top, bottom = 56, 16, 48, 72
    plot_w, plot_h = width - left - right, height - top - bottom
    peak = max((v for values in series.values() for v in values), default=1.0) or 1.0
    ceiling = 1.0 if as_percent else _nice_ceiling(peak * 1.1)
    group_w = plot_w / len(groups)
    bar_w = group_w * 0.7 / len(series)

    def y(value: float) -> float:
        return top + plot_h * (1 - value / ceiling)

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" font-family="system-ui, sans-serif" font-size="12">',
        f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
        f'<text x="{left}" y="28" font-size="15" font-weight="600" fill="#1f2328">'
        f"{escape(title)}</text>",
    ]
    for tick in range(0, 5):
        value = ceiling * tick / 4
        label = f"{value:.0%}" if as_percent else f"{value:g}"
        parts.append(
            f'<line x1="{left}" x2="{width - right}" y1="{y(value):.1f}" y2="{y(value):.1f}" '
            f'stroke="#d0d7de" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{left - 8}" y="{y(value) + 4:.1f}" text-anchor="end" fill="#57606a">'
            f"{label}</text>"
        )
    for g, group in enumerate(groups):
        x0 = left + g * group_w + group_w * 0.15
        for s, (name, values) in enumerate(series.items()):
            value = values[g]
            x = x0 + s * bar_w
            label = f"{value:.0%}" if as_percent else f"{value:.1f}"
            parts.append(
                f'<rect x="{x:.1f}" y="{y(value):.1f}" width="{bar_w - 4:.1f}" '
                f'height="{top + plot_h - y(value):.1f}" fill="{colors[s % len(colors)]}">'
                f"<title>{escape(name)}: {label}</title></rect>"
            )
            parts.append(
                f'<text x="{x + (bar_w - 4) / 2:.1f}" y="{y(value) - 5:.1f}" text-anchor="middle" '
                f'fill="#1f2328">{label}</text>'
            )
        parts.append(
            f'<text x="{left + g * group_w + group_w / 2:.1f}" y="{top + plot_h + 20}" '
            f'text-anchor="middle" fill="#1f2328">{escape(group)}</text>'
        )
    legend_x = left
    for s, name in enumerate(series):
        parts.append(
            f'<rect x="{legend_x}" y="{height - 26}" width="12" height="12" '
            f'fill="{colors[s % len(colors)]}"/>'
        )
        parts.append(
            f'<text x="{legend_x + 18}" y="{height - 16}" fill="#1f2328">{escape(name)}</text>'
        )
        legend_x += 30 + 7 * len(name)
    parts.append("</svg>")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(parts) + "\n", encoding="utf-8")


def _nice_ceiling(value: float) -> float:
    """Techo redondo para el eje: 10, 20, 25, 50, 100…"""
    magnitude = 10.0 ** math.floor(math.log10(value))
    return next(step * magnitude for step in (1, 2, 2.5, 5, 10) if step * magnitude >= value)
