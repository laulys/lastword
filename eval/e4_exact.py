"""E4: búsquedas que dependen de un código exacto (E-4031, pedido 7781, F-2026-0117…).

Protocolo y regla de decisión, fijados antes de implementar el modo automático, en
eval/README.md. Necesita el modelo de embeddings, y los resultados de E1 y E2 para aplicar
la regla. Uso:
    uv run python eval/e4_exact.py
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from exact_cases import DISTRACTORS, cases, has_codes
from metrics import pct, rank_metrics

from vigente import retrieval, store
from vigente.config import EmbeddingSettings
from vigente.models import Hit, Kind, NewMemory
from vigente.service import MemoryService

HERE = Path(__file__).parent
RESULTS_MD = HERE / "results" / "e4_exact.md"
RESULTS_JSON = HERE / "results" / "e4_exact.json"
WORKSPACE = "e4"
POOL = 20
K = 10
MODES = ("texto", "vector", "híbrida", "automática")
# Regla fijada antes de medir (eval/README.md).
MIN_E4_GAIN = 0.05
MAX_E1_E2_LOSS = 0.005
NOW = datetime(2026, 11, 18, tzinfo=UTC)


def main() -> None:
    settings = EmbeddingSettings.from_env()
    embedder = settings.build()
    assert embedder is not None, "E4 necesita un modelo de embeddings"
    conn = store.connect(":memory:")
    service = MemoryService(conn, WORKSPACE, clock=lambda: NOW, embedder=embedder)
    ids: dict[str, str] = {}
    for _, code in cases():
        ids[code.code] = service.remember(NewMemory(content=code.fact)).memory.id
    for text in DISTRACTORS:
        service.remember(NewMemory(content=text))
    space = store.find_embedding_space(conn, embedder.model)
    assert space is not None

    questions = [(group, code) for group, code in cases()]
    vectors = embedder.embed([code.question for _, code in questions], "query")
    ranks: dict[str, list[int | None]] = {mode: [] for mode in MODES}
    per_group: dict[tuple[str, str], list[int | None]] = {}
    at_ms = store.to_ms(NOW)
    for (group, code), vector in zip(questions, vectors, strict=True):
        text_hits = retrieval.search_fts(
            conn, WORKSPACE, code.question, k=POOL, at_ms=at_ms, kind=Kind.FACT
        )
        vector_hits = retrieval.search_vector(
            conn, space, WORKSPACE, vector, k=POOL, at_ms=at_ms, kind=Kind.FACT
        )
        hybrid = retrieval.hybrid(text_hits, vector_hits, limit=K)
        rankings: dict[str, list[Hit]] = {
            "texto": text_hits[:K],
            "vector": vector_hits[:K],
            "híbrida": hybrid,
            "automática": hybrid if has_codes(code.question) else vector_hits[:K],
        }
        for mode, hits in rankings.items():
            found = [hit.memory.id for hit in hits]
            rank = found.index(ids[code.code]) + 1 if ids[code.code] in found else None
            ranks[mode].append(rank)
            per_group.setdefault((mode, group), []).append(rank)
    conn.close()
    embedder.close()

    metrics = {mode: rank_metrics(ranks[mode]) for mode in MODES}
    groups = list(dict.fromkeys(group for group, _ in questions))
    by_group = {
        mode: {g: rank_metrics(per_group[(mode, g)])["recall@1"] for g in groups} for mode in MODES
    }
    decision = decide(metrics)
    payload = {"model": embedder.model, "metrics": metrics, "by_group": by_group} | decision
    RESULTS_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", "utf-8")
    text = report(embedder.model, metrics, by_group, groups, decision, len(questions))
    RESULTS_MD.write_text(text, encoding="utf-8")
    print(text)


def decide(metrics: dict[str, dict[str, float]]) -> dict[str, object]:
    """Aplica la regla con los MRR de E1 y E2, que también miden el modo automático."""
    e1 = json.loads((HERE / "results" / "e1_synthetic.json").read_text(encoding="utf-8"))
    e2 = json.loads((HERE / "results" / "e2_longmemeval.json").read_text(encoding="utf-8"))
    gain = metrics["automática"]["mrr"] - metrics["vector"]["mrr"]
    losses = {
        "E1": e1["mrr"]["vector"] - e1["mrr"]["automática"],
        "E2": e2["mrr"]["vector"] - e2["mrr"]["automática"],
    }
    adopt = gain >= MIN_E4_GAIN and all(loss <= MAX_E1_E2_LOSS for loss in losses.values())
    return {
        "gain_e4": round(gain, 4),
        "mrr_e1": {mode: e1["mrr"][mode] for mode in ("vector", "automática")},
        "mrr_e2": {mode: e2["mrr"][mode] for mode in ("vector", "automática")},
        "loss": {name: round(loss, 4) for name, loss in losses.items()},
        "adopt_auto": adopt,
    }


def report(
    model: str,
    metrics: dict[str, dict[str, float]],
    by_group: dict[str, dict[str, float]],
    groups: list[str],
    decision: dict[str, object],
    total: int,
) -> str:
    rows = [
        f"| {mode} | {pct(metrics[mode]['recall@1'])} | {pct(metrics[mode]['recall@5'])} | "
        f"{metrics[mode]['mrr']:.3f} |"
        for mode in MODES
    ]
    group_rows = [
        f"| {g} | " + " | ".join(pct(by_group[mode][g]) for mode in MODES) + " |" for g in groups
    ]
    mrr_e1 = decision["mrr_e1"]
    mrr_e2 = decision["mrr_e2"]
    loss = decision["loss"]
    assert isinstance(mrr_e1, dict) and isinstance(mrr_e2, dict) and isinstance(loss, dict)
    verdict = (
        "**El modo automático pasa a ser el de por defecto.**"
        if decision["adopt_auto"]
        else "**Se queda vector como modo por defecto.**"
    )
    return "\n".join(
        [
            "# E4: búsquedas con códigos exactos",
            "",
            f"Generado por `eval/e4_exact.py` con `{model}`. {total} preguntas que nombran un "
            "código (errores, pedidos, facturas, incidencias, versiones, productos y envíos), "
            "sobre memorias que solo se diferencian en el código, más 15 sin códigos.",
            "",
            "| Modo | Recall@1 | Recall@5 | MRR |",
            "|---|---:|---:|---:|",
            *rows,
            "",
            "El modo **automático** busca por vectores y combina con texto solo cuando la "
            "consulta trae un código: una palabra de al menos 3 caracteres con alguna cifra, "
            "salvo años y fechas.",
            "",
            "## Acierto a la primera, por tipo",
            "",
            "| Tipo | " + " | ".join(MODES) + " |",
            "|---|" + "---:|" * len(MODES),
            *group_rows,
            "",
            "## Decisión, según la regla fijada antes",
            "",
            f"- En E4, el MRR del automático supera al de vector en {decision['gain_e4']:.3f} "
            f"(la regla pide al menos {MIN_E4_GAIN}).",
            f"- En E1, MRR {mrr_e1['vector']:.3f} con vector y {mrr_e1['automática']:.3f} con el "
            f"automático; en E2, {mrr_e2['vector']:.3f} y {mrr_e2['automática']:.3f}. La regla "
            f"permite perder como mucho {MAX_E1_E2_LOSS} (se pierde {loss['E1']:.3f} y "
            f"{loss['E2']:.3f}).",
            "",
            verdict,
            "",
        ]
    )


if __name__ == "__main__":
    main()
