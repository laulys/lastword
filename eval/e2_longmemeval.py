"""E2: búsqueda sobre conversaciones reales (LongMemEval-cleaned, 470 preguntas).

Cada pregunta trae su propio historial (unas 48 sesiones). Cada mensaje del usuario se guarda
en Vigente como `episode` con la fecha de su sesión, y se mide si la búsqueda encuentra las
sesiones con la evidencia. Protocolo en eval/README.md. Uso:
    uv run python eval/download_longmemeval.py
    uv run python eval/longmemeval.py      # embeddings en caché
    uv run python eval/e2_longmemeval.py [--limit N]
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from cache import CachedEmbedder
from charts import bar_chart
from exact_cases import has_codes
from longmemeval import Item, load
from metrics import pct

from vigente import retrieval, store
from vigente.config import EmbeddingSettings
from vigente.embeddings import Embedder
from vigente.models import MAX_CONTENT_CHARS, Hit, Kind, NewMemory
from vigente.service import MemoryService

HERE = Path(__file__).parent
RESULTS_MD = HERE / "results" / "e2_longmemeval.md"
RESULTS_JSON = HERE / "results" / "e2_longmemeval.json"
CHART = HERE / "results" / "e2_actualizaciones.svg"
WORKSPACE = "e2"
POOL = 50  # mensajes que se piden a cada búsqueda
VECTOR_WEIGHT = 2.0
MODES = ("texto", "vector", "híbrida", "híbrida ponderada", "automática")  # automática: E4


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


@dataclass(frozen=True)
class Outcome:
    question_type: str
    any5: bool  # alguna sesión con evidencia entre las 5 primeras
    all5: bool  # todas
    reciprocal: float  # 1 / posición de la primera sesión con evidencia
    update_order: str | None  # solo knowledge-update: quién aparece antes


def answer_time(item: Item) -> datetime:
    """Momento en que se responde: después de todo el historial, como pide LongMemEval.

    En 69 preguntas hay sesiones fechadas hasta 24 h después de `question_date` (un detalle
    de los datos). Buscar en `question_date` las ocultaría con el filtro temporal.
    """
    return max(item.question_date, *item.session_dates.values())


def session_ranking(hits: list[Hit], session_of: dict[str, str]) -> list[str]:
    """Sesiones en el orden en que aparece su primer mensaje."""
    return list(dict.fromkeys(session_of[hit.memory.id] for hit in hits))


def update_order(sessions: list[str], item: Item) -> str:
    old, new = sorted(item.evidence_sessions, key=lambda s: item.session_dates[s])
    rank = {session: i for i, session in enumerate(sessions)}
    if old not in rank and new not in rank:
        return "ninguna"
    if new not in rank:
        return "solo la vieja"
    if old not in rank:
        return "solo la nueva"
    return "la nueva delante" if rank[new] < rank[old] else "la vieja delante"


def evaluate(item: Item, embedder: Embedder) -> dict[str, Outcome]:
    conn = store.connect(":memory:")
    now = answer_time(item)
    service = MemoryService(conn, WORKSPACE, clock=Clock(now), embedder=embedder)
    session_of: dict[str, str] = {}
    for turn in item.turns:
        # LongMemEval es público y E2 mide la búsqueda, no los filtros de seguridad (ADR-0007):
        # unos pocos mensajes traen tokens en URLs o código, y deben guardarse igual que antes.
        memory = service.remember(
            NewMemory(content=turn.text, kind=Kind.EPISODE, valid_from=turn.date),
            allow_secrets=True,
        ).memory
        session_of[memory.id] = turn.session_id
    at_ms = store.to_ms(now)
    (query_vector,) = embedder.embed([item.question], "query")
    text_hits = retrieval.search_fts(conn, WORKSPACE, item.question, k=POOL, at_ms=at_ms)
    space = store.find_embedding_space(conn, embedder.model, len(query_vector))
    assert space is not None
    vector_hits = retrieval.search_vector(conn, space, WORKSPACE, query_vector, k=POOL, at_ms=at_ms)
    rankings = {
        "texto": text_hits,
        "vector": vector_hits,
        "híbrida": retrieval.hybrid(text_hits, vector_hits, limit=POOL),
        "híbrida ponderada": retrieval.hybrid(
            text_hits, vector_hits, limit=POOL, vector_weight=VECTOR_WEIGHT
        ),
    }
    # Automática (E4): vector, salvo que la pregunta traiga un código.
    rankings["automática"] = rankings["híbrida"] if has_codes(item.question) else rankings["vector"]
    conn.close()
    evidence = set(item.evidence_sessions)
    outcomes = {}
    for mode, hits in rankings.items():
        sessions = session_ranking(hits, session_of)
        first = next((i for i, s in enumerate(sessions, start=1) if s in evidence), None)
        outcomes[mode] = Outcome(
            question_type=item.question_type,
            any5=bool(evidence & set(sessions[:5])),
            all5=evidence <= set(sessions[:5]),
            reciprocal=1 / first if first else 0.0,
            update_order=update_order(sessions, item)
            if item.question_type == "knowledge-update"
            else None,
        )
    return outcomes


def summarize(outcomes: list[Outcome]) -> dict[str, float]:
    return {
        "recall_any@5": statistics.mean(o.any5 for o in outcomes),
        "recall_all@5": statistics.mean(o.all5 for o in outcomes),
        "mrr": statistics.mean(o.reciprocal for o in outcomes),
        "n": len(outcomes),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None, help="Solo las N primeras preguntas")
    args = parser.parse_args()
    settings = EmbeddingSettings.from_env()
    inner = settings.build()
    assert inner is not None, "E2 necesita un modelo de embeddings"
    embedder = CachedEmbedder(inner)
    items, truncated = load()
    items = items[: args.limit] if args.limit else items
    by_mode: dict[str, list[Outcome]] = defaultdict(list)
    start = time.perf_counter()
    for number, item in enumerate(items, start=1):
        for mode, outcome in evaluate(item, embedder).items():
            by_mode[mode].append(outcome)
        if number % 50 == 0:
            print(
                f"  {number}/{len(items)} preguntas · {time.perf_counter() - start:.0f} s",
                flush=True,
            )
    embedder.close()
    inner.close()

    results = {mode: summarize(outcomes) for mode, outcomes in by_mode.items()}
    types = sorted({o.question_type for o in by_mode["híbrida"]})
    per_type = {
        mode: {
            qtype: summarize([o for o in outcomes if o.question_type == qtype]) for qtype in types
        }
        for mode, outcomes in by_mode.items()
    }
    orders = {
        mode: Counter(o.update_order for o in outcomes if o.update_order is not None)
        for mode, outcomes in by_mode.items()
    }
    RESULTS_JSON.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_JSON.write_text(
        json.dumps(
            {"model": settings.model, "questions": len(items),
             "mrr": {mode: r["mrr"] for mode, r in results.items()}},
            indent=2, ensure_ascii=False,
        ),
        encoding="utf-8",
    )  # fmt: skip
    ku_total = sum(orders["híbrida"].values())
    bar_chart(
        CHART,
        f"LongMemEval, {ku_total} preguntas de actualización: qué versión sale antes",
        list(MODES),
        {
            "la nueva (vigente)": [orders[m]["la nueva delante"] / ku_total for m in MODES],
            "la vieja (obsoleta)": [orders[m]["la vieja delante"] / ku_total for m in MODES],
        },
        colors=("#2f7d5b", "#e0913b"),
    )
    text = report(items, truncated, results, per_type, orders, time.perf_counter() - start)
    RESULTS_MD.write_text(text, encoding="utf-8")
    print(text)


def report(
    items: list[Item],
    truncated: int,
    results: dict[str, dict[str, float]],
    per_type: dict[str, dict[str, dict[str, float]]],
    orders: dict[str, Counter[str]],
    seconds: float,
) -> str:
    turns = sum(len(item.turns) for item in items)
    lines = [
        "# E2. LongMemEval: búsqueda sobre conversaciones reales",
        "",
        "Generado por `eval/e2_longmemeval.py` sobre `longmemeval_s_cleaned.json` (licencia MIT, "
        "[LongMemEval](https://github.com/xiaowu0162/LongMemEval)). Protocolo en "
        "[`eval/README.md`](../README.md).",
        "",
        f"- Fecha: {datetime.now(UTC):%Y-%m-%d}",
        f"- Embeddings: `{EmbeddingSettings.from_env().model}`",
        f"- {len(items)} preguntas (sin las de abstención), {turns} mensajes de usuario guardados "
        f"como episodios ({truncated} recortados a {MAX_CONTENT_CHARS} caracteres). "
        f"Tiempo total: {seconds:.0f} s.",
        f"- Se responde después de todo el historial, como pide LongMemEval: en "
        f"{sum(answer_time(i) > i.question_date for i in items)} preguntas hay sesiones "
        "fechadas (menos de 24 h) después de la pregunta, y buscar en su fecha exacta "
        "ocultaría esa evidencia con el filtro temporal.",
        "- Métricas por sesión: una sesión cuenta como encontrada si aparece alguno de sus "
        "mensajes. Solo se indexan mensajes del usuario: en *single-session-assistant* la "
        "evidencia está en las respuestas del asistente.",
        "",
        "## Modos de búsqueda",
        "",
        "| Modo | Recall@5 (any) | Recall@5 (all) | MRR |",
        "|---|---:|---:|---:|",
    ]
    for mode in MODES:
        r = results[mode]
        lines.append(
            f"| {mode} | {pct(r['recall_any@5'])} | {pct(r['recall_all@5'])} | {r['mrr']:.2f} |"
        )
    lines += [
        "",
        "## Por tipo de pregunta (Recall@5 any)",
        "",
        "| Tipo | n | " + " | ".join(MODES) + " |",
        "|---|---:|" + "---:|" * len(MODES),
    ]
    for qtype, summary in per_type["híbrida"].items():
        cells = " | ".join(pct(per_type[mode][qtype]["recall_any@5"]) for mode in MODES)
        lines.append(f"| {qtype} | {summary['n']:.0f} | {cells} |")
    categories = [
        "la nueva delante",
        "la vieja delante",
        "solo la nueva",
        "solo la vieja",
        "ninguna",
    ]
    total = sum(orders["híbrida"].values())
    lines += [
        "",
        "## Actualizaciones: ¿qué versión aparece antes?",
        "",
        f"En las {total} preguntas de *knowledge-update* el historial contiene la versión vieja "
        "del dato y la nueva, en sesiones distintas. Ninguna búsqueda sabe cuál está vigente: "
        "es el problema que resuelven los reemplazos, que aquí no hay, porque los mensajes se "
        "guardan tal cual.",
        "",
        "![Qué versión sale antes en las preguntas de actualización](e2_actualizaciones.svg)",
        "",
        "| Modo | " + " | ".join(categories) + " |",
        "|---|" + "---:|" * len(categories),
    ]
    for mode in MODES:
        counts = orders[mode]
        lines.append(f"| {mode} | " + " | ".join(pct(counts[c] / total) for c in categories) + " |")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
