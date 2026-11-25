from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from tests.helpers import T0, FakeClock, disk_bytes, recall_ids, remember, supersede
from vigente import store
from vigente.errors import (
    AlreadySupersededError,
    InvalidValidityError,
    NotFoundError,
    NotSupersedableError,
)
from vigente.models import Kind, ListQuery, NewMemory, RecallQuery, Status, SupersedeRequest
from vigente.service import MemoryService


class TestRemember:
    def test_stores_a_fact_that_is_true_from_now_on(
        self, service: MemoryService, clock: FakeClock
    ) -> None:
        memory = remember(service, "  Ana vive en Madrid  ", tags=["Personas"])
        assert memory.content == "Ana vive en Madrid"
        assert memory.kind is Kind.FACT
        assert memory.tags == ["personas"]
        assert memory.status is Status.ACTIVE
        assert memory.created_at == memory.valid_from == clock.now
        assert memory.valid_to is None
        assert service.get(memory.id) == memory

    @pytest.mark.parametrize(
        "fields",
        [
            {"content": ""},
            {"content": "   "},
            {"content": "x" * 4001},
            {"content": "ok", "tags": ["con espacio"]},
            {"content": "ok", "tags": [f"t{i}" for i in range(21)]},
            {"content": "ok", "kind": "otro"},
            {"content": "ok", "confidence": 1.5},
            {"content": "ok", "campo_extra": 1},
        ],
    )
    def test_rejects_invalid_input(self, fields: dict[str, Any]) -> None:
        with pytest.raises(ValidationError):
            NewMemory.model_validate(fields)

    def test_an_episode_cannot_happen_in_the_future(
        self, service: MemoryService, clock: FakeClock
    ) -> None:
        with pytest.raises(InvalidValidityError):
            remember(
                service, "Demo con el cliente", kind="episode", valid_from=clock.now + timedelta(1)
            )

    def test_suggests_current_facts_that_may_be_replaced(
        self, service: MemoryService, clock: FakeClock
    ) -> None:
        madrid = remember(service, "Ana vive en Madrid")
        remember(service, "El proyecto usa PostgreSQL")
        clock.advance(days=1)
        result = service.remember(NewMemory(content="Ana se mudó a Lisboa"))
        assert [hit.memory.id for hit in result.possible_supersedes] == [madrid.id]

    def test_does_not_suggest_replaced_facts_or_episodes(
        self, service: MemoryService, clock: FakeClock
    ) -> None:
        madrid = remember(service, "Ana vive en Madrid")
        clock.advance(days=1)
        sevilla = supersede(service, madrid.id, "Ana vive en Sevilla")
        remember(service, "Ana visitó Lisboa en verano", kind="episode")
        clock.advance(days=1)
        result = service.remember(NewMemory(content="Ana vive en Lisboa"))
        assert [hit.memory.id for hit in result.possible_supersedes] == [sevilla.id]

    def test_episodes_get_no_suggestions(self, service: MemoryService) -> None:
        remember(service, "Ana vive en Madrid")
        result = service.remember(NewMemory(content="Ana visitó Madrid", kind=Kind.EPISODE))
        assert result.possible_supersedes == []


class TestSupersede:
    def test_links_both_versions_and_closes_the_old_one(
        self, service: MemoryService, clock: FakeClock
    ) -> None:
        old = remember(service, "El proyecto usa PostgreSQL", tags=["stack"])
        clock.advance(days=30)
        result = service.supersede(
            SupersedeRequest(
                old_id=old.id, new_content="El proyecto usa SQLite", reason="MVP más simple"
            )
        )
        new, closed = result.new, result.old
        assert new.supersedes_id == old.id
        assert closed.superseded_by_id == new.id
        assert closed.status is Status.SUPERSEDED
        assert closed.valid_to == new.valid_from == clock.now
        assert new.valid_to is None
        assert new.tags == ["stack"]
        assert new.supersede_reason == "MVP más simple"
        assert closed.content == "El proyecto usa PostgreSQL"

    def test_accepts_a_change_that_happened_in_the_past(
        self, service: MemoryService, clock: FakeClock
    ) -> None:
        old = remember(service, "Ana vive en Madrid")
        clock.advance(days=60)
        moved = T0 + timedelta(days=20)
        new = supersede(service, old.id, "Ana vive en Lisboa", valid_from=moved.isoformat())
        assert new.valid_from == moved
        assert new.created_at == clock.now
        assert service.get(old.id).valid_to == moved

    def test_the_new_version_must_start_after_the_old_one(
        self, service: MemoryService, clock: FakeClock
    ) -> None:
        old = remember(service, "Ana vive en Madrid")
        clock.advance(days=1)
        with pytest.raises(InvalidValidityError):
            supersede(service, old.id, "Ana vive en Lisboa", valid_from=old.valid_from)

    def test_superseding_in_the_same_millisecond_still_moves_forward(
        self, service: MemoryService
    ) -> None:
        old = remember(service, "Ana vive en Madrid")
        new = supersede(service, old.id, "Ana vive en Lisboa")
        assert new.valid_from == old.valid_from + timedelta(milliseconds=1)

    def test_episodes_cannot_be_superseded(self, service: MemoryService) -> None:
        episode = remember(service, "Reunión de arranque", kind="episode")
        with pytest.raises(NotSupersedableError):
            supersede(service, episode.id, "Otra reunión")

    def test_superseding_an_old_version_points_to_the_current_one(
        self, service: MemoryService, clock: FakeClock
    ) -> None:
        v1 = remember(service, "Responsable de pagos: Marta")
        clock.advance(days=1)
        v2 = supersede(service, v1.id, "Responsable de pagos: Luis")
        clock.advance(days=1)
        v3 = supersede(service, v2.id, "Responsable de pagos: Sara")
        with pytest.raises(AlreadySupersededError) as error:
            supersede(service, v1.id, "Responsable de pagos: Pablo")
        assert error.value.current_id == v3.id

    def test_is_atomic(
        self, service: MemoryService, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        old = remember(service, "El proyecto usa PostgreSQL")
        clock.advance(days=1)

        def fail(*_: object, **__: object) -> None:
            raise RuntimeError("fallo simulado a mitad del reemplazo")

        monkeypatch.setattr(store, "mark_superseded", fail)
        with pytest.raises(RuntimeError):
            supersede(service, old.id, "El proyecto usa SQLite")
        assert service.list_memories(ListQuery()).items == [old]
        assert recall_ids(service, "SQLite", include_history=True) == []
        assert service.doctor() == []


class TestHistory:
    def test_returns_the_whole_chain_from_any_version(
        self, service: MemoryService, clock: FakeClock
    ) -> None:
        v1 = remember(service, "Ana vive en Madrid")
        clock.advance(days=1)
        v2 = supersede(service, v1.id, "Ana vive en Sevilla")
        clock.advance(days=1)
        v3 = supersede(service, v2.id, "Ana vive en Lisboa")
        for version in (v1, v2, v3):
            assert [m.id for m in service.history(version.id)] == [v1.id, v2.id, v3.id]

    def test_accepts_lowercase_ids(self, service: MemoryService) -> None:
        memory = remember(service, "Ana vive en Madrid")
        assert service.get(memory.id.lower()) == memory


class TestForget:
    def test_deletes_every_version_and_nothing_else(
        self, service: MemoryService, clock: FakeClock
    ) -> None:
        v1 = remember(service, "Ana vive en Madrid")
        clock.advance(days=1)
        v2 = supersede(service, v1.id, "Ana vive en Lisboa")
        other = remember(service, "El proyecto usa SQLite")
        assert service.forget(v2.id).deleted_ids == [v1.id, v2.id]
        for memory_id in (v1.id, v2.id):
            with pytest.raises(NotFoundError):
                service.get(memory_id)
        assert recall_ids(service, "Ana vive", include_history=True) == []
        assert service.get(other.id) == other
        assert service.doctor() == []

    def test_erases_the_text_from_disk(
        self, service: MemoryService, clock: FakeClock, db_path: Path
    ) -> None:
        secret = "zanahoriasecreta"
        v1 = remember(service, f"La clave del portal es {secret}")
        clock.advance(days=1)
        supersede(service, v1.id, f"La clave nueva del portal es {secret}bis")
        assert secret.encode() in disk_bytes(db_path)  # control: estaba en disco
        service.forget(v1.id)
        assert secret.encode() not in disk_bytes(db_path)


class TestIsolation:
    def test_workspaces_cannot_see_or_touch_each_other(
        self, conn: sqlite3.Connection, clock: FakeClock
    ) -> None:
        ana = MemoryService(conn, "ana", clock=clock)
        bruno = MemoryService(conn, "bruno", clock=clock)
        memory = remember(ana, "Ana vive en Madrid")
        attempts: list[Callable[[], object]] = [
            lambda: bruno.get(memory.id),
            lambda: bruno.history(memory.id),
            lambda: bruno.forget(memory.id),
            lambda: supersede(bruno, memory.id, "Ana vive en Lisboa"),
        ]
        for attempt in attempts:
            with pytest.raises(NotFoundError):
                attempt()
        assert recall_ids(bruno, "Ana Madrid", include_history=True) == []
        assert bruno.list_memories(ListQuery()).items == []
        assert ana.get(memory.id) == memory

    @pytest.mark.parametrize("workspace", ["", "Mayúsculas", "con espacio", "../fuera", "a" * 65])
    def test_rejects_invalid_workspace_ids(self, conn: sqlite3.Connection, workspace: str) -> None:
        with pytest.raises(ValidationError):
            MemoryService(conn, workspace)


class TestList:
    def test_paginates_newest_first(self, service: MemoryService, clock: FakeClock) -> None:
        ids = []
        for number in range(5):
            ids.append(remember(service, f"hecho número {number}").id)
            clock.advance(minutes=1)
        seen = []
        cursor = None
        for _ in range(3):
            page = service.list_memories(ListQuery(limit=2, cursor=cursor))
            seen += [memory.id for memory in page.items]
            cursor = page.next_cursor
        assert seen == ids[::-1]
        assert cursor is None

    def test_filters_by_kind_status_and_tag(self, service: MemoryService, clock: FakeClock) -> None:
        old = remember(service, "El proyecto usa PostgreSQL", tags=["stack"])
        clock.advance(days=1)
        new = supersede(service, old.id, "El proyecto usa SQLite")
        episode = remember(service, "Reunión de arranque", kind="episode", tags=["reuniones"])

        def ids(**filters: Any) -> list[str]:
            page = service.list_memories(ListQuery.model_validate(filters))
            return [memory.id for memory in page.items]

        assert ids(kind="episode") == [episode.id]
        assert ids(status="superseded") == [old.id]
        assert ids(tag="stack") == [new.id, old.id]
        assert ids(kind="fact", status="active") == [new.id]


class TestDoctor:
    def test_clean_database_has_no_problems(self, service: MemoryService, clock: FakeClock) -> None:
        old = remember(service, "Ana vive en Madrid")
        clock.advance(days=1)
        supersede(service, old.id, "Ana vive en Lisboa")
        assert service.doctor() == []

    def test_detects_a_text_index_out_of_sync(
        self, service: MemoryService, conn: sqlite3.Connection
    ) -> None:
        remember(service, "Ana vive en Madrid")
        conn.execute("DELETE FROM memories_fts")
        assert any("índice de texto" in problem for problem in service.doctor())

    def test_detects_a_broken_chain(
        self, service: MemoryService, conn: sqlite3.Connection, clock: FakeClock
    ) -> None:
        old = remember(service, "Ana vive en Madrid")
        clock.advance(days=1)
        supersede(service, old.id, "Ana vive en Lisboa")
        conn.execute("UPDATE memories SET valid_to = valid_to - 1 WHERE id = ?", (old.id,))
        assert any(old.id in problem for problem in service.doctor())


def test_stats_counts_by_kind_and_status(service: MemoryService, clock: FakeClock) -> None:
    old = remember(service, "Ana vive en Madrid")
    clock.advance(days=1)
    supersede(service, old.id, "Ana vive en Lisboa")
    remember(service, "Reunión de arranque", kind="episode")
    assert service.stats() == {
        "facts_active": 1,
        "facts_superseded": 1,
        "episodes": 1,
        "total": 3,
    }


class TestLink:
    """supersede con new_id: la versión nueva ya estaba guardada (no se duplica el hecho)."""

    def test_links_a_memory_that_is_already_stored(
        self, service: MemoryService, clock: FakeClock
    ) -> None:
        old = remember(service, "El proyecto usa PostgreSQL")
        clock.advance(days=1)
        new = remember(service, "Migramos a SQLite")
        result = service.supersede(SupersedeRequest(old_id=old.id, new_id=new.id, reason="MVP"))
        assert (result.new.id, result.new.supersedes_id, result.new.supersede_reason) == (
            new.id,
            old.id,
            "MVP",
        )
        assert result.old.superseded_by_id == new.id
        assert result.old.valid_to == new.valid_from
        assert len(service.list_memories(ListQuery()).items) == 2
        assert [m.id for m in service.history(old.id)] == [old.id, new.id]
        assert service.doctor() == []

    def test_rejects_links_that_would_break_the_chain(
        self, service: MemoryService, clock: FakeClock
    ) -> None:
        v1 = remember(service, "Responsable de pagos: Marta")
        clock.advance(days=1)
        v2 = supersede(service, v1.id, "Responsable de pagos: Luis")
        clock.advance(days=1)
        episode = remember(service, "Reunión con Luis", kind="episode")
        other = remember(service, "Ana vive en Madrid")
        cases: list[tuple[str, str, type[Exception]]] = [
            (v2.id, episode.id, NotSupersedableError),  # la versión nueva debe ser un fact
            (other.id, other.id, NotSupersedableError),  # consigo misma
            (other.id, v2.id, InvalidValidityError),  # v2 empezó antes que el hecho a cerrar
            (v2.id, v1.id, NotSupersedableError),  # ciclo: v1 → v2 → v1
            (v1.id, other.id, AlreadySupersededError),  # v1 ya fue reemplazada
        ]
        for old_id, new_id, expected in cases:
            with pytest.raises(expected):
                service.supersede(SupersedeRequest(old_id=old_id, new_id=new_id))
        assert service.doctor() == []

    def test_the_new_version_must_start_later(
        self, service: MemoryService, clock: FakeClock
    ) -> None:
        earlier = remember(service, "Ana vive en Lisboa")
        clock.advance(days=1)
        later = remember(service, "Ana vive en Madrid")
        with pytest.raises(InvalidValidityError):
            service.supersede(SupersedeRequest(old_id=later.id, new_id=earlier.id))

    @pytest.mark.parametrize(
        "fields",
        [
            {},
            {"new_content": "x", "new_id": "01JD0000000000000000000001"},
            {"new_id": "01JD0000000000000000000001", "valid_from": "2026-01-01"},
            {"new_id": "01JD0000000000000000000001", "tags": ["a"]},
        ],
    )
    def test_needs_exactly_one_new_version(self, fields: dict[str, Any]) -> None:
        with pytest.raises(ValidationError):
            SupersedeRequest.model_validate({"old_id": "01JD0000000000000000000000", **fields})


def test_reading_does_not_wait_for_another_writer(db_path: Path, clock: FakeClock) -> None:
    """El servidor MCP abre el servicio en cada llamada: leer no debe tomar el bloqueo de
    escritura, o las búsquedas en paralelo esperarían a cualquier escritura en curso."""
    MemoryService.open(db_path, "test").close()  # el workspace ya existe
    writer = store.connect(db_path)
    writer.execute("BEGIN IMMEDIATE")  # otro proceso está escribiendo
    try:
        reader = store.connect(db_path)
        reader.execute("PRAGMA busy_timeout = 50")  # fallaría enseguida si intentara escribir
        service = MemoryService(reader, "test", clock=clock)
        assert service.recall(RecallQuery(query="Ana")).results == []
        service.close()
    finally:
        writer.execute("ROLLBACK")
        writer.close()


class TestDates:
    @pytest.mark.parametrize("year_only", ["2024", " 2024 ", "20250301", "1735689600.5", 2024])
    def test_a_bare_number_is_not_a_date(self, year_only: object) -> None:
        """Pydantic lo leería como segundos desde 1970 y «2024» se guardaría como 1970."""
        with pytest.raises(ValidationError, match="ISO 8601"):
            NewMemory.model_validate({"content": "Ana vive en Madrid", "valid_from": year_only})
        with pytest.raises(ValidationError, match="ISO 8601"):
            RecallQuery.model_validate({"query": "Ana", "as_of": year_only})

    @pytest.mark.parametrize(
        "impossible",
        ["0001-01-01T00:00:00+05:00", "9999-12-31T23:59:59-05:00", "9999-12-31T23:59:59.999Z"],
    )
    def test_dates_at_the_edge_of_the_calendar_are_rejected(self, impossible: str) -> None:
        with pytest.raises(ValidationError, match="fuera de rango"):
            NewMemory.model_validate({"content": "Ana vive en Madrid", "valid_from": impossible})

    def test_iso_dates_still_work(self, service: MemoryService) -> None:
        dated = remember(service, "Ana vive en Madrid", valid_from="2024-03-01")
        assert dated.valid_from.isoformat() == "2024-03-01T00:00:00+00:00"
        late = remember(service, "Luis vive en Valencia", valid_from="9998-12-31T23:59:59.998Z")
        assert supersede(service, late.id, "Luis vive en Lisboa").valid_from > late.valid_from


def test_forget_says_so_when_another_connection_keeps_the_text_on_disk(
    db_path: Path, clock: FakeClock
) -> None:
    """Con otra conexión leyendo, SQLite no puede vaciar el registro de cambios (WAL): el
    borrado es efectivo, pero el texto sigue en ese archivo hasta que la conexión acabe."""
    service = MemoryService.open(db_path, "test", clock=clock)
    memory = remember(service, "Marta tiene una cita confidencial")
    reader = sqlite3.connect(db_path, autocommit=True)
    reader.execute("BEGIN")
    reader.execute("SELECT count(*) FROM memories").fetchone()
    try:
        result = service.forget(memory.id)
    finally:
        reader.execute("ROLLBACK")
        reader.close()
    assert result.deleted_ids == [memory.id]
    assert len(result.warnings) == 1 and "en sus archivos" in result.warnings[0]
    with pytest.raises(NotFoundError):
        service.get(memory.id)
    again = remember(service, "Otro dato")
    assert service.forget(again.id).warnings == []  # sin lectores, el registro se vacía
    service.close()
    assert b"cita confidencial" not in disk_bytes(db_path)
