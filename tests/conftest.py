from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.helpers import FakeClock
from vigente import store
from vigente.service import MemoryService


@pytest.fixture(autouse=True)
def _spanish(monkeypatch: pytest.MonkeyPatch) -> None:
    """Los tests leen los mensajes en español, el idioma fuente, sea cual sea el de la máquina
    (en la CI de GitHub, LANG es C.UTF-8 y saldrían en inglés)."""
    monkeypatch.setenv("VIGENTE_LANG", "es")


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "vigente.db"


@pytest.fixture
def conn(db_path: Path) -> Iterator[sqlite3.Connection]:
    connection = store.connect(db_path)
    yield connection
    connection.close()


@pytest.fixture
def service(conn: sqlite3.Connection, clock: FakeClock) -> MemoryService:
    return MemoryService(conn, "test", clock=clock)
