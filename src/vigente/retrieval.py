"""Recuperación: texto (FTS5/BM25), vectores (sqlite-vec) y fusión de ambos rankings."""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Sequence

from vigente import store
from vigente.models import Hit, Kind, Origin

MAX_QUERY_TOKENS = 32
RRF_K = 60  # constante habitual de Reciprocal Rank Fusion
_SOURCE_ORDER = {"fts": 0, "vector": 1}
_TOKEN = re.compile(r"\w+")

# Palabras vacías mínimas (ES + EN). Solo se quitan de la consulta, nunca del índice.
STOPWORDS: frozenset[str] = frozenset(
    """
    a al algo con como cual cuál de del donde dónde el ella ellos en entre era es esa ese eso
    esta este esto fue ha hay la las le les lo los mas más me mi mis muy no nos o para pero
    por que qué se ser si sí sin son su sus te tu tus un una uno unos unas y ya yo
    an and are as at be been but by do does did for from had has have he her his i if in is
    it its me my not of on or our she so that the their them they this to was we were what
    when where which who will with you your
    """.split()  # noqa: SIM905 - más legible que 120 cadenas sueltas
)


def fts_match_expression(text: str) -> str | None:
    """Convierte texto libre en una expresión MATCH segura para FTS5.

    Cada token va entre comillas, así que la sintaxis de FTS5 (comillas, `*`, `NEAR`, `AND`,
    `-`) nunca llega al motor como operador. Se quitan palabras vacías salvo que no quede
    ninguna otra. Devuelve None si el texto no tiene tokens.
    """
    tokens = [token.lower() for token in _TOKEN.findall(text)]
    meaningful = [token for token in tokens if token not in STOPWORDS]
    chosen = list(dict.fromkeys(meaningful or tokens))[:MAX_QUERY_TOKENS]
    if not chosen:
        return None
    return " OR ".join(f'"{token}"' for token in chosen)


def words(text: str) -> frozenset[str]:
    """Las palabras de un texto, sin repetir ni contar las vacías."""
    return frozenset(_TOKEN.findall(text.lower())) - STOPWORDS


def share_most(one: frozenset[str], other: frozenset[str]) -> bool:
    """¿Comparten la mitad o más de las palabras del texto más corto?"""
    shortest = min(len(one), len(other))
    return shortest > 0 and 2 * len(one & other) >= shortest


def search_fts(
    conn: sqlite3.Connection,
    workspace_id: str,
    text: str,
    *,
    k: int,
    at_ms: int | None,
    kind: Kind | None = None,
    exclude_id: str | None = None,
    origins: Sequence[Origin] | None = None,
) -> list[Hit]:
    expression = fts_match_expression(text)
    if expression is None:
        return []
    rows = store.fts_search(
        conn,
        workspace_id,
        expression,
        limit=k,
        at_ms=at_ms,
        kind=kind,
        exclude_id=exclude_id,
        origins=origins,
    )
    return [Hit(memory=memory, score=-rank, matched_by=["fts"]) for memory, rank in rows]


def search_vector(
    conn: sqlite3.Connection,
    space: store.EmbeddingSpace,
    workspace_id: str,
    vector: Sequence[float],
    *,
    k: int,
    at_ms: int | None,
    kind: Kind | None = None,
    min_similarity: float = 0.0,
) -> list[Hit]:
    """Vecinos más cercanos. Score = similitud coseno (1 - distancia).

    `min_similarity` 0 significa sin umbral: un vecino con similitud negativa también cuenta.
    """
    rows = store.vector_search(conn, space, workspace_id, vector, limit=k, at_ms=at_ms, kind=kind)
    hits = []
    for memory, distance in rows:
        similarity = 1.0 - distance
        if min_similarity <= 0 or similarity >= min_similarity:
            hits.append(
                Hit(memory=memory, score=similarity, matched_by=["vector"], similarity=similarity)
            )
    return hits


def reciprocal_rank_fusion(
    rankings: Sequence[Sequence[Hit]],
    *,
    limit: int,
    k: int = RRF_K,
    weights: Sequence[float] | None = None,
) -> list[Hit]:
    """Combina rankings con RRF: score(d) = suma de 1 / (k + posición de d en cada ranking).

    Solo usa posiciones, así que no hace falta normalizar BM25 y coseno, que están en escalas
    incomparables. `weights` multiplica la aportación de cada ranking (por defecto, 1).

    Desempate: mejor posición individual y, si coincide, la lista que va antes en `rankings`
    (pásalas por prioridad). No se desempata por id: los ULID tienen una parte aleatoria y el
    orden cambiaría entre ejecuciones.
    """
    weights = [1.0] * len(rankings) if weights is None else list(weights)
    if len(weights) != len(rankings):
        raise ValueError("Hace falta un peso por ranking")
    scores: dict[str, float] = {}
    best: dict[str, tuple[int, int]] = {}  # (mejor posición, índice de su lista)
    merged: dict[str, Hit] = {}
    for index, (ranking, weight) in enumerate(zip(rankings, weights, strict=True)):
        for rank, hit in enumerate(ranking, start=1):
            key = hit.memory.id
            scores[key] = scores.get(key, 0.0) + weight / (k + rank)
            best[key] = min(best.get(key, (rank, index)), (rank, index))
            previous = merged.get(key)
            if previous is None:
                merged[key] = hit
                continue
            sources = sorted(
                {*previous.matched_by, *hit.matched_by}, key=lambda s: _SOURCE_ORDER.get(s, 99)
            )
            similarity = previous.similarity if previous.similarity is not None else hit.similarity
            merged[key] = previous.model_copy(
                update={"matched_by": sources, "similarity": similarity}
            )
    order = sorted(scores, key=lambda key: (-scores[key], best[key]))
    return [merged[key].model_copy(update={"score": scores[key]}) for key in order[:limit]]


def hybrid(
    text_hits: Sequence[Hit],
    vector_hits: Sequence[Hit],
    *,
    limit: int,
    vector_weight: float = 1.0,
) -> list[Hit]:
    """Fusión híbrida de Vigente: los vectores tienen prioridad en los empates, porque fueron
    el modo más preciso en la evaluación (eval/results/decisions.md)."""
    return reciprocal_rank_fusion(
        [vector_hits, text_hits], limit=limit, weights=[vector_weight, 1.0]
    )
