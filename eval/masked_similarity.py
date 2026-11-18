"""¿Separa la similitud con los valores tapados una actualización de una frase del mismo tema?

Una actualización cambia el valor de un atributo («Ana vive en Madrid» → «Ana se mudó a
Lisboa»). Una frase del mismo tema cambia de atributo («Ana trabaja como diseñadora»). Si se
tapan los valores (nombres propios y cifras) antes de comparar, la similitud debería medir
sobre todo el atributo, y las frases del mismo tema dejarían de recibir propuestas.

Usa el conjunto escrito a mano del mini-benchmark (`data/embedding_choice.json`). Cada frase
nueva se compara con los hechos guardados y se proponen los más parecidos que pasan el umbral,
como mucho `CANDIDATE_LIMIT`, igual que hace `remember`. Cada variante se mide con su mejor
umbral. Resultado y decisión: ADR-0004, «Revisión en la v1.2».

Uso:
    uv run python eval/masked_similarity.py
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from metrics import cosine, f_beta

from vigente.config import EmbeddingSettings
from vigente.service import CANDIDATE_LIMIT

HERE = Path(__file__).parent
DATA = HERE / "data" / "embedding_choice.json"
RESULTS = HERE / "results" / "masked_similarity.md"
THRESHOLDS = [round(0.30 + 0.01 * i, 2) for i in range(66)]
WORD = re.compile(r"\b\w[\w.-]*\b")

Similarity = Callable[[int, int], float]  # (frase nueva, hecho guardado) -> similitud


@dataclass(frozen=True)
class Statement:
    text: str
    kind: str  # update, related o unrelated
    replaces: str | None = None  # el hecho que actualiza, si es una actualización


@dataclass(frozen=True)
class Score:
    threshold: float
    precision: float
    recall: float
    f2: float
    related_with_proposal: int


def mask(text: str) -> str:
    """Tapa los valores: palabras con cifras y nombres propios (en mayúscula, salvo al empezar)."""
    position = -1

    def hide(match: re.Match[str]) -> str:
        nonlocal position
        position += 1
        word = match.group()
        is_value = any(char.isdigit() for char in word) or (position > 0 and word[0].isupper())
        return "X" if is_value else word

    return WORD.sub(hide, text)


def score(statements: list[Statement], keys: list[str], sim: Similarity, threshold: float) -> Score:
    hits = wrong = missed = related = 0
    for i, statement in enumerate(statements):
        ranked = sorted(((sim(i, j), key) for j, key in enumerate(keys)), reverse=True)
        proposed = [key for value, key in ranked[:CANDIDATE_LIMIT] if value >= threshold]
        if statement.kind == "update":
            hits += statement.replaces in proposed
            missed += statement.replaces not in proposed
            wrong += sum(key != statement.replaces for key in proposed)
        else:
            wrong += len(proposed)
            related += statement.kind == "related" and bool(proposed)
    precision = hits / (hits + wrong) if hits + wrong else 0.0
    recall = hits / (hits + missed)
    return Score(threshold, precision, recall, f_beta(precision, recall, 2), related)


def main() -> None:
    data = json.loads(DATA.read_text(encoding="utf-8"))
    keys = list(data["facts"])
    facts = [data["facts"][key] for key in keys]
    statements = [Statement(u["new"], "update", u["replaces"]) for u in data["updates"]]
    statements += [Statement(r["new"], "related") for r in data["related"]]
    statements += [Statement(text, "unrelated") for text in data["unrelated"]]
    related_total = len(data["related"])

    embedder = EmbeddingSettings.from_env().build()
    if embedder is None:
        raise SystemExit("Hace falta un modelo de embeddings (VIGENTE_EMBED_MODEL).")
    current = embedder.profile.candidate_threshold
    texts = [statement.text for statement in statements]
    fact_vectors = embedder.embed(facts, "document")
    masked_fact_vectors = embedder.embed([mask(text) for text in facts], "document")
    vectors = embedder.embed(texts, "document")
    masked_vectors = embedder.embed([mask(text) for text in texts], "document")

    def full(i: int, j: int) -> float:
        return cosine(vectors[i], fact_vectors[j])

    def masked(i: int, j: int) -> float:
        return cosine(masked_vectors[i], masked_fact_vectors[j])

    def mean(i: int, j: int) -> float:
        return (full(i, j) + masked(i, j)) / 2

    def best(sim: Similarity) -> Score:
        scores = [score(statements, keys, sim, t) for t in THRESHOLDS]
        return max(scores, key=lambda s: (s.f2, s.threshold))

    rows = [
        ("completa (la de Vigente)", best(full)),
        ("con los valores tapados", best(masked)),
        ("media de las dos", best(mean)),
    ]
    lines = [
        "# Propuestas de reemplazo con los valores tapados",
        "",
        f"Generado por `eval/masked_similarity.py` con `{embedder.model}`, sobre el conjunto "
        f"escrito a mano del mini-benchmark: {len(data['updates'])} actualizaciones, "
        f"{related_total} frases del mismo tema y {len(data['unrelated'])} sin relación. "
        f"Se proponen como mucho {CANDIDATE_LIMIT} hechos, como en `remember`.",
        "",
        f"Ejemplo: «{facts[1]}» se compara como «{mask(facts[1])}».",
        "",
        f"Cada variante, con su mejor umbral. El umbral actual de Vigente es {current}.",
        "",
        "| Similitud | Umbral | F2 | Precisión | Recall | Frases del mismo tema con propuesta |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for name, result in rows:
        lines.append(
            f"| {name} | {result.threshold:.2f} | {result.f2:.3f} | {result.precision:.0%} "
            f"| {result.recall:.0%} | {result.related_with_proposal} de {related_total} |"
        )
    lines += [
        "",
        "Decisión en el [ADR-0004](../../docs/decisions/0004-modelo-de-embeddings.md): se "
        "mantiene la similitud completa.",
        "",
    ]
    RESULTS.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    embedder.close()


if __name__ == "__main__":
    main()
