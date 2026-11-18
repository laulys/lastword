"""Prueba de integración con un modelo real. Requiere Ollama en local:

VIGENTE_TEST_OLLAMA=1 uv run pytest tests/test_ollama.py
"""

from __future__ import annotations

import os
import sqlite3

import pytest

from tests.helpers import FakeClock, recall_ids, remember
from vigente.config import DEFAULT_EMBED_MODEL
from vigente.embeddings import OpenAICompatibleEmbedder
from vigente.models import NewMemory
from vigente.service import MemoryService

pytestmark = pytest.mark.skipif(
    not os.environ.get("VIGENTE_TEST_OLLAMA"), reason="requiere Ollama: VIGENTE_TEST_OLLAMA=1"
)
MODEL = os.environ.get("VIGENTE_TEST_OLLAMA_MODEL", DEFAULT_EMBED_MODEL)


def test_a_real_model_detects_a_paraphrased_update(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    embedder = OpenAICompatibleEmbedder(MODEL, timeout=120)
    service = MemoryService(conn, "test", clock=clock, embedder=embedder)
    database = remember(service, "El proyecto usa PostgreSQL 15 como base de datos")
    ana = remember(service, "Ana vive en Madrid")
    clock.advance(days=1)

    result = service.remember(NewMemory(content="Migramos a SQLite para el MVP"))
    assert [hit.memory.id for hit in result.possible_supersedes] == [database.id]
    assert recall_ids(service, "¿Dónde vive Ana?")[0] == ana.id
    assert service.doctor() == []
    embedder.close()
