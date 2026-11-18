"""Aplica las reglas fijadas en eval/README.md a los resultados de E1 y E2. Uso:
uv run python eval/decisions.py
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).parent / "results"
CURRENT_MODE = "híbrida"
MIN_MRR_GAIN = 0.01


def main() -> None:
    e1 = json.loads((HERE / "e1_synthetic.json").read_text(encoding="utf-8"))
    e2 = json.loads((HERE / "e2_longmemeval.json").read_text(encoding="utf-8"))
    # Los cuatro modos del hito 4; el automático se decidió después, en E4.
    modes = ["texto", "vector", "híbrida", "híbrida ponderada"]
    mean = {mode: (e1["mrr"][mode] + e2["mrr"][mode]) / 2 for mode in modes}
    best = max(modes, key=lambda mode: mean[mode])
    gain = mean[best] - mean[CURRENT_MODE]
    change = best != CURRENT_MODE and gain >= MIN_MRR_GAIN
    threshold = e1["threshold"]
    lines = [
        "# Decisiones del hito 4",
        "",
        "Generado por `eval/decisions.py` aplicando las reglas de [`eval/README.md`](../README.md)"
        " a [E1](e1_synthetic.md) y [E2](e2_longmemeval.md).",
        "",
        "## Modo de búsqueda por defecto",
        "",
        "| Modo | MRR en E1 | MRR en E2 | Media |",
        "|---|---:|---:|---:|",
        *(
            f"| {mode} | {e1['mrr'][mode]:.3f} | {e2['mrr'][mode]:.3f} | {mean[mode]:.3f} |"
            for mode in modes
        ),
        "",
        (
            f"**Cambia a «{best}»**: su MRR medio supera al de «{CURRENT_MODE}» en {gain:.3f}."
            if change
            else f"**Se mantiene «{CURRENT_MODE}»**: la mejora de «{best}» ({gain:.3f}) no "
            f"llega a {MIN_MRR_GAIN}."
            if best != CURRENT_MODE
            else f"**Se mantiene «{CURRENT_MODE}»**: es el mejor modo."
        ),
        "",
        "## Umbral de posibles reemplazos",
        "",
        f"Mejor F2 en E1: {threshold['f2_best']:.2f}. Tras validar con el conjunto escrito a "
        f"mano: **{threshold['chosen']:.2f}** "
        f"({'cambia' if threshold['chosen'] != threshold['current'] else 'se mantiene'}). "
        "Detalle en E1.",
    ]
    text = "\n".join(lines) + "\n"
    (HERE / "decisions.md").write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
