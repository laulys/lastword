from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from lastword import store
from lastword.service import MemoryService
from tests.helpers import FakeClock


@pytest.fixture(autouse=True)
def _spanish(monkeypatch: pytest.MonkeyPatch) -> None:
    """Los tests leen los mensajes en español, el idioma fuente, sea cual sea el de la máquina
    (en la CI de GitHub, LANG es C.UTF-8 y saldrían en inglés)."""
    monkeypatch.setenv("LASTWORD_LANG", "es")


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "lastword.db"


@pytest.fixture
def conn(db_path: Path) -> Iterator[sqlite3.Connection]:
    connection = store.connect(db_path)
    yield connection
    connection.close()


@pytest.fixture
def service(conn: sqlite3.Connection, clock: FakeClock) -> MemoryService:
    return MemoryService(conn, "test", clock=clock)
