"""Un hecho nuevo que reemplaza a varios: «Ana y Luis se mudaron a Lisboa» (ADR-0008)."""

from __future__ import annotations

import sqlite3

import pytest
from pydantic import ValidationError

from tests.helpers import FakeClock, recall_ids, remember, supersede
from vigente import store
from vigente.errors import AlreadySupersededError, NotSupersedableError
from vigente.models import Memory, SupersedeRequest
from vigente.service import MemoryService


@pytest.fixture
def two_cities(service: MemoryService, clock: FakeClock) -> tuple[Memory, Memory]:
    ana = remember(service, "Ana vive en Madrid")
    clock.advance(minutes=1)
    luis = remember(service, "Luis vive en Valencia")
    clock.advance(days=30)
    return ana, luis


def test_one_new_fact_closes_both(
    service: MemoryService, two_cities: tuple[Memory, Memory]
) -> None:
    ana, luis = two_cities
    result = service.supersede(
        SupersedeRequest(
            old_id=ana.id,
            also_old_ids=[luis.id],
            new_content="Ana y Luis se mudaron juntos a Lisboa",
            reason="Se fueron a vivir juntos",
        )
    )
    assert result.old.id == ana.id
    assert [memory.id for memory in result.also_old] == [luis.id]
    for old in (result.old, *result.also_old):
        assert old.superseded_by_id == result.new.id
        assert old.valid_to == result.new.valid_from
    assert recall_ids(service, "Ana Luis") == [result.new.id]
    assert service.doctor() == []


def test_the_past_still_shows_both(
    service: MemoryService, clock: FakeClock, two_cities: tuple[Memory, Memory]
) -> None:
    ana, luis = two_cities
    before = clock()
    clock.advance(days=1)
    supersede(service, ana.id, "Ana y Luis viven en Lisboa", also_old_ids=[luis.id])
    assert sorted(recall_ids(service, "vive", as_of=before)) == sorted([ana.id, luis.id])


def test_an_existing_memory_can_be_linked_to_both(
    service: MemoryService, two_cities: tuple[Memory, Memory]
) -> None:
    ana, luis = two_cities
    new = remember(service, "Ana y Luis viven en Lisboa")
    result = service.supersede(
        SupersedeRequest(old_id=ana.id, also_old_ids=[luis.id], new_id=new.id)
    )
    assert result.new.supersedes_id == ana.id
    assert service.get(luis.id).superseded_by_id == new.id
    assert service.doctor() == []


def test_history_from_any_branch_shows_every_version(
    service: MemoryService, clock: FakeClock, two_cities: tuple[Memory, Memory]
) -> None:
    ana, luis = two_cities
    merged = supersede(service, ana.id, "Ana y Luis viven en Lisboa", also_old_ids=[luis.id])
    clock.advance(days=10)
    later = supersede(service, merged.id, "Ana y Luis viven en Oporto")
    expected = [ana.id, luis.id, merged.id, later.id]
    for start in expected:
        assert [memory.id for memory in service.history(start)] == expected


def test_forget_removes_every_connected_version(
    service: MemoryService, two_cities: tuple[Memory, Memory]
) -> None:
    ana, luis = two_cities
    merged = supersede(service, ana.id, "Ana y Luis viven en Lisboa", also_old_ids=[luis.id])
    other = remember(service, "Marta vive en Valencia")
    assert sorted(service.forget(luis.id).deleted_ids) == sorted([ana.id, luis.id, merged.id])
    assert recall_ids(service, "vive", include_history=True) == [other.id]
    assert service.doctor() == []


def test_every_old_fact_must_still_be_current(
    service: MemoryService, clock: FakeClock, two_cities: tuple[Memory, Memory]
) -> None:
    ana, luis = two_cities
    newer = supersede(service, luis.id, "Luis vive en Bilbao")
    clock.advance(days=1)
    with pytest.raises(AlreadySupersededError) as caught:
        supersede(service, ana.id, "Ana y Luis viven en Lisboa", also_old_ids=[luis.id])
    assert caught.value.current_id == newer.id
    assert service.get(ana.id).superseded_by_id is None  # nada a medias


def test_episodes_cannot_be_merged(
    service: MemoryService, two_cities: tuple[Memory, Memory]
) -> None:
    ana, _ = two_cities
    episode = remember(service, "Mudanza el sábado", kind="episode")
    with pytest.raises(NotSupersedableError):
        supersede(service, ana.id, "Ana vive en Lisboa", also_old_ids=[episode.id])


def test_the_same_fact_cannot_be_listed_twice(two_cities: tuple[Memory, Memory]) -> None:
    ana, _ = two_cities
    with pytest.raises(ValidationError):
        SupersedeRequest(old_id=ana.id, also_old_ids=[ana.id], new_content="Ana vive en Lisboa")


def test_another_fact_can_be_added_later_to_the_same_replacement(
    service: MemoryService, clock: FakeClock, two_cities: tuple[Memory, Memory]
) -> None:
    """«Ah, y eso también dejó obsoleto lo de Luis»: se enlaza después, sin duplicar nada."""
    ana, luis = two_cities
    merged = supersede(service, ana.id, "Ana y Luis viven en Lisboa", reason="mudanza")
    clock.advance(days=1)
    result = service.supersede(SupersedeRequest(old_id=luis.id, new_id=merged.id))
    assert (result.new.supersedes_id, result.new.supersede_reason) == (ana.id, "mudanza")
    assert result.old.superseded_by_id == merged.id
    assert result.old.valid_to == merged.valid_from
    assert [m.id for m in service.history(luis.id)] == [ana.id, luis.id, merged.id]
    assert service.doctor() == []


def test_a_fact_with_thousands_of_versions_can_still_be_read_and_forgotten(
    service: MemoryService, conn: sqlite3.Connection
) -> None:
    """Había un tope de 10.000 versiones al leer, pero no al escribir: pasado el tope, el
    hecho ya no se podía consultar ni, lo que es peor, olvidar."""
    total = 10_050
    ids = [f"01KE{number:022d}" for number in range(total)]
    with store.transaction(conn):  # con SQL: crearlas una a una con supersede tarda 25 s
        conn.execute("PRAGMA defer_foreign_keys = ON")  # cada versión apunta a la siguiente
        for number, memory_id in enumerate(ids):
            last = number == total - 1
            conn.execute(
                "INSERT INTO memories (seq, id, workspace_id, kind, content, created_at, "
                "valid_from, valid_to, status, supersedes_id, superseded_by_id) "
                "VALUES (?, ?, 'test', 'fact', ?, 0, ?, ?, ?, ?, ?)",
                (
                    number + 1,
                    memory_id,
                    f"Contador: {number}",
                    number,
                    store.OPEN_END_MS if last else number + 1,
                    "active" if last else "superseded",
                    ids[number - 1] if number else None,
                    None if last else ids[number + 1],
                ),
            )
            conn.execute(
                "INSERT INTO memories_fts (rowid, content, tags) VALUES (?, ?, '')",
                (number + 1, f"Contador: {number}"),
            )
    assert service.doctor() == []
    assert [memory.id for memory in service.history(ids[0])] == ids
    with pytest.raises(AlreadySupersededError) as caught:
        supersede(service, ids[0], "Contador: otro")
    assert caught.value.current_id == ids[-1]
    assert len(service.forget(ids[5000]).deleted_ids) == total
