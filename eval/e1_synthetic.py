"""E1: ¿una memoria con reemplazos devuelve menos datos obsoletos que una ingenua?

Protocolo y reglas de decisión, fijadas antes de ejecutar, en eval/README.md. Uso:
    uv run python eval/e1_synthetic.py
"""

from __future__ import annotations

import json
import sqlite3
import statistics
from collections import defaultdict
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from charts import bar_chart
from embedding_choice import DATA as HANDWRITTEN
from embedding_choice import score_candidates
from exact_cases import has_codes
from metrics import cosine, f_beta, pct, rank_metrics
from synthetic import Dataset, Distractor, Question, Version, generate

from lastword import retrieval, store
from lastword.config import EmbeddingSettings
from lastword.embeddings import Embedder, OpenAICompatibleEmbedder, profile_for
from lastword.models import Hit, Kind, Memory, NewMemory, SupersedeRequest
from lastword.service import MemoryService

HERE = Path(__file__).parent
RESULTS_MD = HERE / "results" / "e1_synthetic.md"
RESULTS_JSON = HERE / "results" / "e1_synthetic.json"
CHART = HERE / "results" / "e1_obsoletos.svg"
WORKSPACE = "e1"
POOL = 20  # candidatos que se piden a cada búsqueda antes de fusionar
K = 10
RECENCY_WINDOW = 5
VECTOR_WEIGHT = 2.0  # variante «híbrida ponderada», fijada antes de ejecutar
CURRENT_THRESHOLD = 0.51  # el del ADR-0004
MAX_RECALL5_DROP = 0.02  # regla de la tesis
THRESHOLDS = [round(0.30 + 0.01 * i, 2) for i in range(66)]
MODES = ("texto", "vector", "híbrida", "híbrida ponderada", "automática")  # automática: E4
SYSTEMS = ("ingenua", "ingenua + recencia", "lastword")
QUESTION_TYPES = ("presente", "con fecha", "todas")
DEFAULT_MODE = "híbrida"  # el modo por defecto cuando se fijó el protocolo
NEW_DEFAULT_MODE = "vector"  # el que sale de las reglas de decisión de este hito


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


@dataclass
class Proposal:
    target: str | None  # versión que la nueva reemplaza (None si es la primera)
    candidates: list[tuple[str, float]]  # (id, similitud) de los 3 más parecidos


@dataclass
class Built:
    conn: sqlite3.Connection
    service: MemoryService
    clock: Clock
    model: str
    ids: dict[tuple[str, int], str] = field(default_factory=dict)
    proposals: list[Proposal] = field(default_factory=list)


def build(dataset: Dataset, embedder: Embedder, *, link: bool) -> Built:
    """Ingiere en orden cronológico. Con `link`, cada versión reemplaza a la anterior."""
    conn = store.connect(":memory:")
    clock = Clock(dataset.versions[0].valid_from)
    service = MemoryService(conn, WORKSPACE, clock=clock, embedder=embedder)
    built = Built(conn, service, clock, embedder.model)
    events: list[tuple[datetime, Version | Distractor]] = [
        (v.valid_from, v) for v in dataset.versions
    ] + [(d.date, d) for d in dataset.distractors]
    head: dict[str, str] = {}
    for date, event in sorted(events, key=lambda pair: pair[0]):
        clock.now = date
        if isinstance(event, Distractor):
            service.remember(NewMemory(content=event.text, kind=Kind.EPISODE, valid_from=date))
            continue
        result = service.remember(NewMemory(content=event.text, valid_from=date))
        target = head.get(event.chain)
        candidates = [(hit.memory.id, hit.similarity or 0.0) for hit in result.possible_supersedes]
        built.proposals.append(Proposal(target, candidates))
        if link and target is not None:
            service.supersede(SupersedeRequest(old_id=target, new_id=result.memory.id))
        head[event.chain] = result.memory.id
        built.ids[(event.chain, event.index)] = result.memory.id
    clock.now = dataset.now
    return built


def search(built: Built, question: Question, vector: list[float], mode: str) -> list[Memory]:
    at_ms = store.to_ms(question.as_of or built.clock.now)
    text_hits = retrieval.search_fts(built.conn, WORKSPACE, question.text, k=POOL, at_ms=at_ms)
    space = store.find_embedding_space(built.conn, built.model, len(vector))
    assert space is not None
    vector_hits = retrieval.search_vector(built.conn, space, WORKSPACE, vector, k=POOL, at_ms=at_ms)
    hits: list[Hit]
    if mode == "texto":
        hits = text_hits[:K]
    elif mode == "vector":
        hits = vector_hits[:K]
    elif mode == "automática":  # vector, salvo que la pregunta traiga un código (E4)
        if has_codes(question.text):
            hits = retrieval.hybrid(text_hits, vector_hits, limit=K)
        else:
            hits = vector_hits[:K]
    else:
        weight = VECTOR_WEIGHT if mode == "híbrida ponderada" else 1.0
        hits = retrieval.hybrid(text_hits, vector_hits, limit=K, vector_weight=weight)
    return [hit.memory for hit in hits]


def by_recency(memories: list[Memory]) -> list[Memory]:
    window = sorted(memories[:RECENCY_WINDOW], key=lambda m: m.valid_from, reverse=True)
    return window + memories[RECENCY_WINDOW:]


@dataclass
class Tally:
    ranks: list[int | None] = field(default_factory=list)
    stale1: list[bool] = field(default_factory=list)
    stale5: list[bool] = field(default_factory=list)
    other1: list[bool] = field(default_factory=list)

    def add(self, ids: list[str], expected: str, same_chain: set[str]) -> None:
        self.ranks.append(ids.index(expected) + 1 if expected in ids else None)
        top = ids[0] if ids else None
        self.stale1.append(top in same_chain and top != expected)
        self.stale5.append(any(i in same_chain and i != expected for i in ids[:5]))
        self.other1.append(top is not None and top not in same_chain)

    def summary(self) -> dict[str, float]:
        return rank_metrics(self.ranks, ks=(1, 5)) | {
            "stale@1": statistics.mean(self.stale1),
            "stale@5": statistics.mean(self.stale5),
            "other@1": statistics.mean(self.other1),
            "n": len(self.ranks),
        }


def evaluate(
    dataset: Dataset, naive: Built, lastword: Built, vectors: list[list[float]]
) -> dict[tuple[str, str, str], dict[str, float]]:
    tallies: dict[tuple[str, str, str], Tally] = defaultdict(Tally)
    chains = dataset.chains
    for question, vector in zip(dataset.questions, vectors, strict=True):
        qtype = "presente" if question.as_of is None else "con fecha"
        for system in SYSTEMS:
            built = lastword if system == "lastword" else naive
            expected = built.ids[(question.chain, question.expected)]
            same_chain = {built.ids[(question.chain, v.index)] for v in chains[question.chain]}
            for mode in MODES:
                memories = search(built, question, vector, mode)
                if system == "ingenua + recencia":
                    memories = by_recency(memories)
                ids = [memory.id for memory in memories]
                for bucket in (qtype, "todas"):
                    tallies[(system, mode, bucket)].add(ids, expected, same_chain)
    return {key: tally.summary() for key, tally in tallies.items()}


@dataclass(frozen=True)
class Sweep:
    threshold: float
    precision: float
    recall: float

    @property
    def f1(self) -> float:
        return f_beta(self.precision, self.recall, 1)

    @property
    def f2(self) -> float:
        return f_beta(self.precision, self.recall, 2)


def sweep(proposals: list[Proposal]) -> list[Sweep]:
    """Mismas reglas que el benchmark del hito 2: los 3 más parecidos por encima del umbral."""
    results = []
    for threshold in THRESHOLDS:
        tp = fp = fn = 0
        for proposal in proposals:
            proposed = {cid for cid, sim in proposal.candidates if sim >= threshold}
            if proposal.target is None:
                fp += len(proposed)
            else:
                tp += proposal.target in proposed
                fn += proposal.target not in proposed
                fp += len(proposed - {proposal.target})
        precision = tp / (tp + fp) if tp + fp else 1.0
        results.append(Sweep(threshold, precision, tp / (tp + fn)))
    return results


def best(sweeps: list[Sweep], beta: int) -> Sweep:
    return max(sweeps, key=lambda s: (round(f_beta(s.precision, s.recall, beta), 6), s.threshold))


def handwritten(embedder: Embedder) -> dict[float, Sweep]:
    """Conjunto escrito a mano del hito 2: validación, nunca se usa para calibrar."""
    data = json.loads(HANDWRITTEN.read_text(encoding="utf-8"))
    keys = list(data["facts"])
    facts = embedder.embed([data["facts"][key] for key in keys], "document")
    statements = (
        [(u["new"], u["replaces"], "update") for u in data["updates"]]
        + [(r["new"], None, "related") for r in data["related"]]
        + [(text, None, "unrelated") for text in data["unrelated"]]
    )
    vectors = embedder.embed([s[0] for s in statements], embedder.profile.candidate_purpose)
    sims = [[cosine(vector, fact) for fact in facts] for vector in vectors]
    scores = score_candidates(sims, [s[1] for s in statements], keys, [s[2] for s in statements])
    return {s.threshold: Sweep(s.threshold, s.precision, s.recall) for s in scores}


def main() -> None:
    settings = EmbeddingSettings.from_env()
    assert settings.model is not None, "E1 necesita un modelo de embeddings"
    # Umbral 0 durante la ingesta: se registran siempre los 3 candidatos y su similitud, y
    # el barrido de umbrales se hace después.
    embedder = OpenAICompatibleEmbedder(
        settings.model,
        base_url=settings.base_url,
        api_key=settings.api_key,
        allow_remote=settings.allow_remote,
        timeout=300,
        profile=replace(profile_for(settings.model), candidate_threshold=0.0),
    )
    dataset = generate()
    naive = build(dataset, embedder, link=False)
    lastword = build(dataset, embedder, link=True)
    assert lastword.service.doctor() == [] and naive.service.doctor() == []
    vectors = embedder.embed([q.text for q in dataset.questions], "query")
    results = evaluate(dataset, naive, lastword, vectors)

    sweeps = sweep(lastword.proposals)
    by_f1, by_f2 = best(sweeps, 1), best(sweeps, 2)
    validation = handwritten(embedder)
    current, proposed = validation[CURRENT_THRESHOLD], validation[by_f2.threshold]
    adopt = proposed.f2 >= current.f2
    chosen = by_f2.threshold if adopt else CURRENT_THRESHOLD
    embedder.close()

    naive_default = results[("ingenua", DEFAULT_MODE, "todas")]
    lastword_default = results[("lastword", DEFAULT_MODE, "todas")]
    thesis = lastword_default["stale@1"] < naive_default["stale@1"] and (
        lastword_default["recall@5"] >= naive_default["recall@5"] - MAX_RECALL5_DROP
    )
    summary: dict[str, Any] = {
        "model": settings.model,
        "mrr": {mode: results[("lastword", mode, "todas")]["mrr"] for mode in MODES},
        "thesis_holds": thesis,
        "threshold": {"current": CURRENT_THRESHOLD, "f2_best": by_f2.threshold, "chosen": chosen},
        "results": {" | ".join(key): value for key, value in results.items()},
    }
    RESULTS_JSON.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_JSON.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    todas = [results[(system, NEW_DEFAULT_MODE, "todas")] for system in SYSTEMS]
    bar_chart(
        CHART,
        f"Qué es el primer resultado (búsqueda por vectores, {len(dataset.questions)} preguntas)",
        list(SYSTEMS),
        {
            "el dato correcto": [r["recall@1"] for r in todas],
            "una versión obsoleta": [r["stale@1"] for r in todas],
            "otro dato o un episodio": [r["other@1"] for r in todas],
        },
        colors=("#2f7d5b", "#e0913b", "#9aa4b2"),
    )
    text = report(dataset, results, sweeps, by_f1, by_f2, validation, adopt, chosen, thesis)
    RESULTS_MD.write_text(text, encoding="utf-8")
    print(text)


def report(
    dataset: Dataset,
    results: dict[tuple[str, str, str], dict[str, float]],
    sweeps: list[Sweep],
    by_f1: Sweep,
    by_f2: Sweep,
    validation: dict[float, Sweep],
    adopt: bool,
    chosen: float,
    thesis: bool,
) -> str:
    updates = sum(1 for v in dataset.versions if v.index > 0)
    as_of = sum(1 for q in dataset.questions if q.as_of is not None)
    naive = results[("ingenua", DEFAULT_MODE, "todas")]
    vig = results[("lastword", DEFAULT_MODE, "todas")]
    naive_new = results[("ingenua", NEW_DEFAULT_MODE, "todas")]
    vig_new = results[("lastword", NEW_DEFAULT_MODE, "todas")]
    lines = [
        "# E1. Datos sintéticos: ¿los reemplazos evitan datos obsoletos?",
        "",
        "Generado por `eval/e1_synthetic.py`. Protocolo y reglas, fijadas antes de ejecutar, en "
        "[`eval/README.md`](../README.md).",
        "",
        f"- Fecha: {datetime.now(UTC):%Y-%m-%d}",
        f"- Embeddings: `{EmbeddingSettings.from_env().model}`",
        f"- Datos: {len(dataset.chains)} atributos, {len(dataset.versions)} versiones "
        f"({updates} cambios), {len(dataset.distractors)} episodios de distracción, "
        f"{len(dataset.questions)} preguntas ({len(dataset.questions) - as_of} sobre el presente, "
        f"{as_of} con fecha).",
        "",
        "## Resultado",
        "",
        f"**La tesis {'se sostiene' if thesis else 'no se sostiene'}.** Con la búsqueda híbrida "
        f"(el modo por defecto cuando se fijó el protocolo), el primer resultado es una versión "
        f"obsoleta del dato en el {pct(naive['stale@1'])} de las preguntas con la memoria "
        f"ingenua y en el {pct(vig['stale@1'])} con Lastword. Recall@5: {pct(naive['recall@5'])} "
        f"frente a {pct(vig['recall@5'])}.",
        "",
        f"Con la búsqueda por vectores, que pasa a ser la de por defecto tras este hito "
        f"([decisiones](decisions.md)): {pct(naive_new['stale@1'])} frente a "
        f"{pct(vig_new['stale@1'])} de obsoletas, y Recall@5 de {pct(naive_new['recall@5'])} "
        f"frente a {pct(vig_new['recall@5'])}.",
        "",
        "![Qué es el primer resultado según el sistema](e1_obsoletos.svg)",
        "",
        "## Búsqueda híbrida (modo por defecto al fijar el protocolo)",
        "",
        "*Obsoleta* = otra versión del mismo dato. *Ajena* = una memoria de otro dato o un "
        "episodio de distracción.",
        "",
        "| Sistema | Preguntas | Recall@1 | Recall@5 | MRR | 1.ª obsoleta | Obsoleta en top-5 "
        "| 1.ª ajena |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for system in SYSTEMS:
        for qtype in QUESTION_TYPES[:2]:
            r = results[(system, DEFAULT_MODE, qtype)]
            lines.append(
                f"| {system} | {qtype} ({r['n']:.0f}) | {pct(r['recall@1'])} | "
                f"{pct(r['recall@5'])} | {r['mrr']:.2f} | {pct(r['stale@1'])} | "
                f"{pct(r['stale@5'])} | {pct(r['other@1'])} |"
            )
    lines += [
        "",
        "## Modos de búsqueda (todas las preguntas)",
        "",
        "| Sistema | Modo | Recall@1 | Recall@5 | MRR | 1.ª obsoleta |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for system in ("ingenua", "lastword"):
        for mode in MODES:
            r = results[(system, mode, "todas")]
            lines.append(
                f"| {system} | {mode} | {pct(r['recall@1'])} | {pct(r['recall@5'])} | "
                f"{r['mrr']:.2f} | {pct(r['stale@1'])} |"
            )
    nearby = sorted(
        {by_f1.threshold, by_f2.threshold, CURRENT_THRESHOLD}
        | {t for t in THRESHOLDS if abs(t - by_f2.threshold) <= 0.04 and round(t * 100) % 2 == 0}
    )
    table = {s.threshold: s for s in sweeps}
    lines += [
        "",
        "## Umbral de posibles reemplazos",
        "",
        f"Se registraron los 3 candidatos de cada uno de los {len(dataset.versions)} hechos "
        f"durante la ingesta ({updates} reemplazan a otro). Criterio fijado de antemano: "
        f"mejor F2 en E1, validado con el conjunto escrito a mano del hito 2.",
        "",
        "| Umbral | E1: precisión | E1: recall | E1: F1 | E1: F2 | Validación: precisión | "
        "Validación: recall | Validación: F2 |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for threshold in nearby:
        s, v = table[threshold], validation[threshold]
        marks = []
        if threshold == by_f2.threshold:
            marks.append("mejor F2 en E1")
        if threshold == by_f1.threshold:
            marks.append("mejor F1 en E1")
        if threshold == CURRENT_THRESHOLD:
            marks.append("actual")
        label = f"{threshold:.2f}" + (f" ({', '.join(marks)})" if marks else "")
        lines.append(
            f"| {label} | {pct(s.precision)} | {pct(s.recall)} | {s.f1:.2f} | {s.f2:.2f} | "
            f"{pct(v.precision)} | {pct(v.recall)} | {v.f2:.2f} |"
        )
    decision = f"se adopta {chosen:.2f}" if adopt else f"se mantiene {CURRENT_THRESHOLD:.2f}"
    lines += [
        "",
        f"**Decisión según la regla:** {decision} (F2 en validación: "
        f"{validation[by_f2.threshold].f2:.2f} con {by_f2.threshold:.2f} frente a "
        f"{validation[CURRENT_THRESHOLD].f2:.2f} con {CURRENT_THRESHOLD:.2f}).",
        "",
        "Aviso: las frases de E1 salen de plantillas, así que se parecen más entre sí que las "
        "reales. Por eso el umbral se valida con datos escritos a mano y distintos.",
    ]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
