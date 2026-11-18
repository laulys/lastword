"""Un hecho nuevo que reemplaza a varios: «Ana y Luis se mudaron a Lisboa» (ADR-0008)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from tests.helpers import FakeClock, recall_ids, remember, supersede
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
