"""Recuperación. Hito 1: texto completo con FTS5 (BM25) y filtro temporal."""

from __future__ import annotations

import re
import sqlite3

from vigente import store
from vigente.models import Hit, Kind

MAX_QUERY_TOKENS = 32
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


def search_fts(
    conn: sqlite3.Connection,
    workspace_id: str,
    text: str,
    *,
    k: int,
    at_ms: int | None,
    kind: Kind | None = None,
    exclude_id: str | None = None,
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
    )
    return [Hit(memory=memory, score=-rank, matched_by=["fts"]) for memory, rank in rows]
