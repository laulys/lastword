"""Métricas compartidas por los scripts de evaluación."""

from __future__ import annotations

import math
from collections.abc import Sequence


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


def rank_metrics(ranks: Sequence[int | None], ks: Sequence[int] = (1, 5)) -> dict[str, float]:
    """`ranks`: posición (1 = primera) de la respuesta correcta, o None si no apareció."""
    n = len(ranks)
    metrics = {f"recall@{k}": sum(r is not None and r <= k for r in ranks) / n for k in ks}
    metrics["mrr"] = sum(1 / r for r in ranks if r is not None) / n
    return metrics


def f_beta(precision: float, recall: float, beta: float) -> float:
    """F1 con beta=1; con beta=2 el recall pesa el doble que la precisión."""
    if precision + recall == 0:
        return 0.0
    return (1 + beta**2) * precision * recall / (beta**2 * precision + recall)


def percentile(values: Sequence[float], q: float) -> float:
    """Percentil por rango más cercano (q entre 0 y 100)."""
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q / 100 * len(ordered)) - 1)]


def pct(value: float) -> str:
    return f"{value:.0%}"
