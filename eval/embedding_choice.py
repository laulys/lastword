"""Mini-benchmark para elegir el modelo de embeddings y calibrar sus umbrales.

Mide, con el código real de Lastword y un conjunto pequeño escrito a mano en español:

1. Detección de posibles reemplazos: ¿propone el hecho correcto cuando llega una
   actualización, sin proponer nada ante frases del mismo tema o sin relación?
2. Búsqueda: Recall@1, Recall@3 y MRR de texto (BM25), vectores e híbrida (RRF).
3. Preguntas sin respuesta: ¿separa la similitud las preguntas con respuesta de las que no?
4. Latencia de una consulta.

Uso:
    uv run python eval/embedding_choice.py [--models qwen3-embedding:0.6b embeddinggemma]
"""

from __future__ import annotations

import argparse
import json
import math
import platform
import statistics
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from metrics import cosine, rank_metrics

from lastword import retrieval, store
from lastword.embeddings import OpenAICompatibleEmbedder, Purpose
from lastword.models import Kind, NewMemory, RecallQuery, SearchMode
from lastword.service import MemoryService

HERE = Path(__file__).parent
DATA = HERE / "data" / "embedding_choice.json"
RESULTS = HERE / "results" / "embedding_choice.md"
DEFAULT_MODELS = ["qwen3-embedding:0.6b", "embeddinggemma"]
OLLAMA = "http://localhost:11434"
NOW = datetime(2026, 1, 1, tzinfo=UTC)
TOP = 3  # posibles reemplazos que se muestran (CANDIDATE_LIMIT en el servicio)
THRESHOLDS = [round(0.30 + 0.01 * i, 2) for i in range(66)]  # 0.30 … 0.95


@dataclass(frozen=True)
class CandidateScore:
    threshold: float
    precision: float
    recall: float
    f1: float
    related_flagged: int  # frases del mismo tema que recibieron alguna propuesta
    unrelated_flagged: int


def score_candidates(
    sims: list[list[float]], targets: list[str | None], keys: list[str], kinds: list[str]
) -> list[CandidateScore]:
    """`sims[i][j]`: similitud entre la frase nueva i y el hecho j. `targets[i]`: hecho que
    reemplaza (None si no reemplaza nada). Propuestas = top-3 con similitud >= umbral."""
    scores = []
    for threshold in THRESHOLDS:
        tp = fp = fn = related_flagged = unrelated_flagged = 0
        for row, target, kind in zip(sims, targets, kinds, strict=True):
            ranked = sorted(range(len(keys)), key=lambda j: -row[j])[:TOP]
            proposed = {keys[j] for j in ranked if row[j] >= threshold}
            if target is not None:
                tp += target in proposed
                fn += target not in proposed
                fp += len(proposed - {target})
            else:
                fp += len(proposed)
                related_flagged += kind == "related" and bool(proposed)
                unrelated_flagged += kind == "unrelated" and bool(proposed)
        precision = tp / (tp + fp) if tp + fp else 1.0
        recall = tp / (tp + fn)
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        scores.append(
            CandidateScore(threshold, precision, recall, f1, related_flagged, unrelated_flagged)
        )
    return scores


def best(scores: list[CandidateScore]) -> CandidateScore:
    # Mejor F1; a igualdad, el umbral más alto (menos ruido para el agente).
    return max(scores, key=lambda s: (round(s.f1, 6), s.threshold))


def rank_of(contents: list[str], expected: str) -> int | None:
    return contents.index(expected) + 1 if expected in contents else None


def evaluate(model: str, data: dict[str, Any]) -> dict[str, Any]:
    facts: dict[str, str] = data["facts"]
    keys = list(facts)
    embedder = OpenAICompatibleEmbedder(model, timeout=120)
    embedder.embed(["calentar el modelo"], "query")  # la primera llamada carga el modelo

    # 1. Posibles reemplazos, con cada formato de entrada para la frase nueva.
    fact_vectors = embedder.embed([facts[k] for k in keys], "document")
    statements = (
        [(u["new"], u["replaces"], "update") for u in data["updates"]]
        + [(r["new"], None, "related") for r in data["related"]]
        + [(text, None, "unrelated") for text in data["unrelated"]]
    )
    candidates: dict[str, dict[str, Any]] = {}
    purposes: list[Purpose] = ["document", "similarity"]
    for purpose in purposes:
        vectors = embedder.embed([s[0] for s in statements], purpose)
        sims = [[cosine(v, f) for f in fact_vectors] for v in vectors]
        scores = score_candidates(
            sims, [s[1] for s in statements], keys, [s[2] for s in statements]
        )
        update_sims = [
            sims[i][keys.index(target)]
            for i, (_, target, _) in enumerate(statements)
            if target is not None
        ]
        unrelated_max = [max(sims[i]) for i, s in enumerate(statements) if s[2] == "unrelated"]
        candidates[purpose] = {
            "best": best(scores),
            "update_sim_mean": statistics.mean(update_sims),
            "update_sim_min": min(update_sims),
            "unrelated_max_mean": statistics.mean(unrelated_max),
            "unrelated_max_max": max(unrelated_max),
        }

    # 2. Búsqueda con el código real: hechos + frases relacionadas y sin relación como ruido.
    conn = store.connect(":memory:")
    # La fila «híbrida» mide la fusión: se pide explícitamente, sea cual sea el modo por defecto.
    service = MemoryService(
        conn, "eval", clock=lambda: NOW, embedder=embedder, search_mode=SearchMode.HYBRID
    )
    corpus = list(facts.values()) + [r["new"] for r in data["related"]] + data["unrelated"]
    for text in corpus:
        service.remember(NewMemory(content=text))
    space = store.find_embedding_space(conn, model)
    assert space is not None
    at_ms = store.to_ms(NOW)
    ranks: dict[str, list[int | None]] = {"texto": [], "vector": [], "híbrida": []}
    top_similarity: list[float] = []
    latencies: list[float] = []
    for question in data["questions"]:
        expected = facts[question["answer"]]
        text_hits = retrieval.search_fts(conn, "eval", question["q"], k=10, at_ms=at_ms)
        start = time.perf_counter()
        (query_vector,) = embedder.embed([question["q"]], "query")
        latencies.append((time.perf_counter() - start) * 1000)
        vector_hits = retrieval.search_vector(conn, space, "eval", query_vector, k=10, at_ms=at_ms)
        hybrid = service.recall(RecallQuery(query=question["q"], k=10))
        ranks["texto"].append(rank_of([h.memory.content for h in text_hits], expected))
        ranks["vector"].append(rank_of([h.memory.content for h in vector_hits], expected))
        ranks["híbrida"].append(rank_of([h.memory.content for h in hybrid.results], expected))
        top_similarity.append(vector_hits[0].score)
    unanswerable_similarity = []
    for question in data["unanswerable"]:
        (vector,) = embedder.embed([question], "query")
        hits = retrieval.search_vector(conn, space, "eval", vector, k=1, at_ms=at_ms)
        unanswerable_similarity.append(hits[0].score)
    assert service.doctor() == []
    conn.close()
    embedder.close()

    return {
        "model": model,
        "dim": space.dim,
        "candidates": candidates,
        "search": {name: rank_metrics(r, ks=(1, 3)) for name, r in ranks.items()},
        "answerable_top_sim": (min(top_similarity), statistics.mean(top_similarity)),
        "unanswerable_top_sim": (
            statistics.mean(unanswerable_similarity),
            max(unanswerable_similarity),
        ),
        "latency_ms": (
            statistics.median(latencies),
            sorted(latencies)[math.ceil(0.95 * len(latencies)) - 1],
        ),
    }


def fts_candidate_baseline(data: dict[str, Any]) -> CandidateScore:
    """Lo que hacía el hito 1: top-3 por BM25, sin umbral."""
    conn = store.connect(":memory:")
    service = MemoryService(conn, "eval", clock=lambda: NOW)
    ids = {
        key: service.remember(NewMemory(content=text)).memory.id
        for key, text in data["facts"].items()
    }
    by_id = {memory_id: key for key, memory_id in ids.items()}
    tp = fp = fn = 0
    statements = (
        [(u["new"], u["replaces"]) for u in data["updates"]]
        + [(r["new"], None) for r in data["related"]]
        + [(text, None) for text in data["unrelated"]]
    )
    for text, target in statements:
        hits = retrieval.search_fts(
            conn, "eval", text, k=TOP, at_ms=store.to_ms(NOW), kind=Kind.FACT
        )
        proposed = {by_id[h.memory.id] for h in hits}
        if target is not None:
            tp += target in proposed
            fn += target not in proposed
            fp += len(proposed - {target})
        else:
            fp += len(proposed)
    conn.close()
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn)
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return CandidateScore(0.0, precision, recall, f1, 0, 0)


def environment(models: list[str]) -> list[str]:
    lines = [
        f"- Fecha: {datetime.now(UTC):%Y-%m-%d}",
        f"- Máquina: {platform.machine()}, {platform.system()} {platform.release()}",
        f"- Python {platform.python_version()}",
    ]
    try:
        version = httpx.get(f"{OLLAMA}/api/version", timeout=5).json()["version"]
        tags = {
            m["name"]: m["digest"][:12] for m in httpx.get(f"{OLLAMA}/api/tags").json()["models"]
        }
        lines.append(f"- Ollama {version}")
        for model in models:
            name = model if ":" in model else f"{model}:latest"
            lines.append(f"- `{model}`: digest {tags.get(name, '?')}")
    except httpx.HTTPError:
        lines.append("- Ollama: versión no disponible")
    return lines


def report(results: list[dict[str, Any]], baseline: CandidateScore, data: dict[str, Any]) -> str:
    n_updates, n_related = len(data["updates"]), len(data["related"])
    n_unrelated, n_questions = len(data["unrelated"]), len(data["questions"])
    pct = "{:.0%}".format
    lines = [
        "# Elección del modelo de embeddings",
        "",
        "Generado por `eval/embedding_choice.py` con `eval/data/embedding_choice.json`.",
        f"Conjunto pequeño y escrito a mano: {n_updates} actualizaciones, {n_related} frases "
        f"del mismo tema que no reemplazan nada, {n_unrelated} sin relación y "
        f"{n_questions} preguntas. Sirve para elegir entre candidatos, no como resultado "
        "general.",
        "",
        *environment([r["model"] for r in results]),
        "",
        "## 1. Detección de posibles reemplazos",
        "",
        "Propuestas = los 3 hechos más parecidos con similitud ≥ umbral. Se elige el umbral "
        "con mejor F1. Cuenta como error cualquier propuesta que no sea el hecho reemplazado, "
        "también ante frases del mismo tema.",
        "",
        "| Modelo | Formato de la frase nueva | Umbral | Precisión | Recall | F1 "
        "| Mismo tema con propuesta | Sin relación con propuesta |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
        f"| BM25 (hito 1) | texto | sin umbral | {pct(baseline.precision)} "
        f"| {pct(baseline.recall)} | {baseline.f1:.2f} | n/a | n/a |",
    ]
    for result in results:
        for purpose, info in result["candidates"].items():
            s: CandidateScore = info["best"]
            lines.append(
                f"| `{result['model']}` | {purpose} | {s.threshold:.2f} | {pct(s.precision)} "
                f"| {pct(s.recall)} | {s.f1:.2f} | {s.related_flagged}/{n_related} "
                f"| {s.unrelated_flagged}/{n_unrelated} |"
            )
    lines += [
        "",
        "Separación: similitud entre cada actualización y el hecho que reemplaza, frente a la "
        "mayor similitud de las frases sin relación con cualquier hecho.",
        "",
        "| Modelo | Formato | Actualización: media / mínima | Sin relación: media / máxima |",
        "|---|---|---:|---:|",
    ]
    for result in results:
        for purpose, info in result["candidates"].items():
            lines.append(
                f"| `{result['model']}` | {purpose} | {info['update_sim_mean']:.2f} / "
                f"{info['update_sim_min']:.2f} | {info['unrelated_max_mean']:.2f} / "
                f"{info['unrelated_max_max']:.2f} |"
            )
    lines += [
        "",
        "## 2. Búsqueda",
        "",
        "Cada pregunta tiene una respuesta entre "
        f"{len(data['facts']) + n_related + n_unrelated} memorias.",
        "",
        "| Modelo | Búsqueda | Recall@1 | Recall@3 | MRR |",
        "|---|---|---:|---:|---:|",
    ]
    for result in results:
        for name, metrics in result["search"].items():
            lines.append(
                f"| `{result['model']}` | {name} | {pct(metrics['recall@1'])} "
                f"| {pct(metrics['recall@3'])} | {metrics['mrr']:.2f} |"
            )
    lines += [
        "",
        "## 3. Preguntas sin respuesta y latencia",
        "",
        "| Modelo | Dim. | Similitud top-1 con respuesta: mínima / media "
        "| Sin respuesta: media / máxima | Latencia consulta p50 / p95 |",
        "|---|---:|---:|---:|---:|",
    ]
    for result in results:
        lo, mean = result["answerable_top_sim"]
        u_mean, u_max = result["unanswerable_top_sim"]
        p50, p95 = result["latency_ms"]
        lines.append(
            f"| `{result['model']}` | {result['dim']} | {lo:.2f} / {mean:.2f} "
            f"| {u_mean:.2f} / {u_max:.2f} | {p50:.0f} / {p95:.0f} ms |"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", nargs="+", default=DEFAULT_MODELS)
    parser.add_argument("--out", type=Path, default=RESULTS)
    args = parser.parse_args()
    data = json.loads(DATA.read_text(encoding="utf-8"))
    results = [evaluate(model, data) for model in args.models]
    text = report(results, fts_candidate_baseline(data), data)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
