from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from vigente.models import Memory, NewMemory, RecallQuery, SupersedeRequest
from vigente.service import MemoryService

T0 = datetime(2026, 1, 1, tzinfo=UTC)


class FakeClock:
    """Reloj controlable: el tiempo solo avanza cuando el test lo pide."""

    def __init__(self, start: datetime = T0) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta: float) -> datetime:
        self.now += timedelta(**delta)
        return self.now


def remember(service: MemoryService, content: str, **fields: Any) -> Memory:
    request = NewMemory.model_validate({"content": content, **fields})
    return service.remember(request).memory


def supersede(service: MemoryService, old_id: str, content: str, **fields: Any) -> Memory:
    request = SupersedeRequest.model_validate({"old_id": old_id, "new_content": content, **fields})
    return service.supersede(request).new


def recall_ids(service: MemoryService, query: str, **fields: Any) -> list[str]:
    result = service.recall(RecallQuery.model_validate({"query": query, **fields}))
    return [hit.memory.id for hit in result.results]


def disk_bytes(db_path: Path) -> bytes:
    """Todo lo que SQLite tiene en disco para esta base: archivo principal y WAL."""
    wal = db_path.with_name(db_path.name + "-wal")
    return b"".join(path.read_bytes() for path in (db_path, wal) if path.exists())
