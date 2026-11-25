"""Carga de LongMemEval-cleaned para E2. Solo lee el JSON: lo trata como datos."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from lastword.models import MAX_CONTENT_CHARS

PATH = Path(__file__).parent / "datasets" / "longmemeval_s_cleaned.json"


@dataclass(frozen=True)
class Turn:
    session_id: str
    text: str  # recortado a MAX_CONTENT_CHARS
    date: datetime
    has_answer: bool


@dataclass(frozen=True)
class Item:
    question_id: str
    question_type: str
    question: str
    question_date: datetime
    turns: list[Turn]  # solo mensajes del usuario
    evidence_sessions: list[str]
    session_dates: dict[str, datetime]


def parse_date(raw: str) -> datetime:
    """«2023/04/23 (Sun) 08:57» → datetime UTC."""
    day, _, hour = raw.partition(" (")
    return datetime.strptime(f"{day} {hour.split(') ')[1]}", "%Y/%m/%d %H:%M").replace(tzinfo=UTC)


def load(path: Path = PATH) -> tuple[list[Item], int]:
    """Preguntas evaluables (sin las de abstención) y cuántos mensajes se recortaron."""
    items, truncated = [], 0
    for raw in json.loads(path.read_text(encoding="utf-8")):
        if raw["question_id"].endswith("_abs"):
            continue  # sin respuesta en el historial: se excluyen, como en el benchmark
        turns, dates = [], {}
        for session_id, date_raw, session in zip(
            raw["haystack_session_ids"],
            raw["haystack_dates"],
            raw["haystack_sessions"],
            strict=True,
        ):
            date = dates[session_id] = parse_date(date_raw)
            for turn in session:
                if turn["role"] != "user" or not turn["content"].strip():
                    continue
                text = turn["content"].strip()
                truncated += len(text) > MAX_CONTENT_CHARS
                turns.append(
                    Turn(session_id, text[:MAX_CONTENT_CHARS], date, bool(turn.get("has_answer")))
                )
        items.append(
            Item(
                question_id=raw["question_id"],
                question_type=raw["question_type"],
                question=raw["question"],
                question_date=parse_date(raw["question_date"]),
                turns=turns,
                evidence_sessions=list(raw["answer_session_ids"]),
                session_dates=dates,
            )
        )
    return items, truncated


if __name__ == "__main__":
    # Precalcula todos los embeddings de E2 (unos 25 min la primera vez; luego, caché).
    from cache import CachedEmbedder

    from lastword.config import EmbeddingSettings

    items, truncated = load()
    print(f"{len(items)} preguntas, {truncated} mensajes recortados a {MAX_CONTENT_CHARS}")
    inner = EmbeddingSettings.from_env().build()
    assert inner is not None, "E2 necesita un modelo de embeddings"
    embedder = CachedEmbedder(inner)
    embedder.warm([item.question for item in items], "query")
    embedder.warm([turn.text for item in items for turn in item.turns], "document")
    embedder.close()
    inner.close()
