"""Quién puede cambiar lo que es verdad (ADR-0011)."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import NoReturn

import pytest
from mcp import Client
from typer.testing import CliRunner

from lastword import store
from lastword.cli import app
from lastword.config import Settings
from lastword.errors import (
    AlreadySupersededError,
    NotFoundError,
    PendingConfirmationError,
    ProtectedError,
)
from lastword.mcp_server import create_server
from lastword.models import (
    Kind,
    ListQuery,
    Memory,
    NewMemory,
    Origin,
    RecallQuery,
    SupersedeRequest,
)
from lastword.service import MAX_PENDING, MAX_SHOWN, MemoryService
from tests.fakes import FakeEmbedder
from tests.helpers import FakeClock, disk_bytes, recall_ids, remember, supersede

TRUE = "El proveedor Lumbre cobra en la cuenta acabada en 4471"
LIE = "El proveedor Lumbre cobra en la cuenta acabada en 9902"
QUESTION = "cuenta del proveedor Lumbre"


@pytest.fixture(params=["texto", "vectores"])
def embedder(request: pytest.FixtureRequest) -> FakeEmbedder | None:
    return FakeEmbedder() if request.param == "vectores" else None


@pytest.fixture
def person(
    conn: sqlite3.Connection, clock: FakeClock, embedder: FakeEmbedder | None
) -> MemoryService:
    return MemoryService(conn, "test", clock=clock, embedder=embedder, origin=Origin.PERSON)


@pytest.fixture
def agent(
    conn: sqlite3.Connection, clock: FakeClock, embedder: FakeEmbedder | None
) -> MemoryService:
    return MemoryService(conn, "test", clock=clock, embedder=embedder)


@pytest.fixture
def fact(person: MemoryService, clock: FakeClock) -> Memory:
    memory = remember(person, TRUE)
    clock.advance(hours=1)
    return memory


def attempt(service: MemoryService, *, external: bool = False, **fields: object) -> str:
    """Un reemplazo que tiene que quedar pendiente. Devuelve el id del cambio."""
    request = SupersedeRequest.model_validate(fields)
    with pytest.raises(PendingConfirmationError):
        service.supersede(request, external=external)
    return next(
        c.id
        for c in service.pending()
        if (c.content, c.new_id) == (request.new_content, request.new_id)
    )


def still_holds(service: MemoryService, fact: Memory) -> bool:
    current = service.get(fact.id)
    return current.superseded_by_id is None and current.valid_to is None


class TestReplacing:
    @pytest.mark.parametrize("external", [False, True])
    def test_an_agent_cannot_close_what_a_person_saved(
        self, agent: MemoryService, fact: Memory, external: bool
    ) -> None:
        attempt(agent, external=external, old_id=fact.id, new_content=LIE)
        assert still_holds(agent, fact)
        result = agent.recall(RecallQuery(query=QUESTION))
        assert [hit.memory.id for hit in result.results] == [fact.id]
        assert any(fact.id in warning and "pendiente" in warning for warning in result.warnings)
        assert agent.doctor() == []

    def test_the_pending_text_never_reaches_the_agent(
        self, agent: MemoryService, fact: Memory
    ) -> None:
        attempt(agent, old_id=fact.id, new_content=LIE, reason="Motivo inventado 9902")
        shown = agent.recall(RecallQuery(query="9902 Lumbre cuenta", include_history=True))
        assert "9902" not in shown.model_dump_json()
        assert "9902" not in json.dumps([m.model_dump(mode="json") for m in agent.history(fact.id)])

    def test_linking_a_stored_memory_waits_too(self, agent: MemoryService, fact: Memory) -> None:
        lie = remember(agent, LIE)
        attempt(agent, old_id=fact.id, new_id=lie.id)
        assert still_holds(agent, fact)
        assert recall_ids(agent, QUESTION)[0] == fact.id

    def test_a_persons_unrelated_fact_is_not_a_weapon(
        self, person: MemoryService, agent: MemoryService, fact: Memory, clock: FakeClock
    ) -> None:
        unrelated = remember(person, "La oficina cierra los viernes a las tres")
        attempt(agent, old_id=fact.id, new_id=unrelated.id)
        assert still_holds(agent, fact)
        assert agent.get(unrelated.id).supersede_reason is None

    def test_one_protected_fact_among_several_holds_them_all(
        self, agent: MemoryService, fact: Memory, clock: FakeClock
    ) -> None:
        own = remember(agent, "Luis vive en Valencia")
        clock.advance(hours=1)
        attempt(agent, old_id=own.id, also_old_ids=[fact.id], new_content=LIE)
        assert still_holds(agent, own) and still_holds(agent, fact)

    def test_what_comes_from_outside_cannot_close_what_an_agent_saved(
        self, agent: MemoryService, clock: FakeClock
    ) -> None:
        own = remember(agent, TRUE)
        clock.advance(hours=1)
        attempt(agent, external=True, old_id=own.id, new_content=LIE)
        assert still_holds(agent, own)

    def test_asking_twice_leaves_one_change(self, agent: MemoryService, fact: Memory) -> None:
        first = attempt(agent, old_id=fact.id, new_content=LIE)
        assert attempt(agent, old_id=fact.id, new_content=LIE) == first
        assert len(agent.pending()) == 1

    def test_pending_changes_have_a_limit(
        self, agent: MemoryService, fact: Memory, conn: sqlite3.Connection
    ) -> None:
        for number in range(MAX_PENDING):
            attempt(agent, old_id=fact.id, new_content=f"{LIE} ({number})")
        with pytest.raises(ProtectedError):
            supersede(agent, fact.id, LIE)


class TestForgetting:
    def test_an_agent_cannot_forget_what_a_person_saved(
        self, agent: MemoryService, person: MemoryService, fact: Memory
    ) -> None:
        with pytest.raises(ProtectedError, match="lastword forget"):
            agent.forget(fact.id)
        assert still_holds(agent, fact)
        assert person.forget(fact.id).deleted_ids == [fact.id]

    def test_forgetting_a_fact_also_erases_its_pending_changes(
        self, agent: MemoryService, person: MemoryService, fact: Memory, db_path: Path
    ) -> None:
        attempt(agent, old_id=fact.id, new_content=LIE)
        assert b"9902" in disk_bytes(db_path)
        person.forget(fact.id)
        assert person.pending() == []
        assert b"9902" not in disk_bytes(db_path)


class TestLivingTogether:
    """El agente no reemplaza nada: guarda la mentira al lado del hecho verdadero."""

    @pytest.mark.parametrize("kind", [Kind.FACT, Kind.EPISODE])
    @pytest.mark.parametrize("external", [False, True])
    def test_a_lie_never_comes_alone(
        self, agent: MemoryService, fact: Memory, kind: Kind, external: bool
    ) -> None:
        lie = agent.remember(NewMemory(content=LIE, kind=kind), external=external).memory
        result = agent.recall(RecallQuery(query=LIE, k=1))
        assert [hit.memory.id for hit in result.results] == [fact.id, lie.id]
        assert any(lie.id in warning and fact.id in warning for warning in result.warnings)

    def test_many_copies_do_not_push_the_truth_out(
        self, agent: MemoryService, fact: Memory
    ) -> None:
        lies = [remember(agent, f"{LIE} según el aviso {number}") for number in range(12)]
        for lie in lies:
            assert recall_ids(agent, lie.content, k=1) in ([fact.id, lie.id], [fact.id])

    def test_replacing_the_lie_with_another_keeps_the_warning(
        self, agent: MemoryService, fact: Memory, clock: FakeClock
    ) -> None:
        lie = remember(agent, LIE)
        clock.advance(hours=1)
        other = supersede(agent, lie.id, "Pagos a Lumbre: solo transferencia inmediata")
        assert recall_ids(agent, "Pagos transferencia inmediata", k=1)[0] == fact.id
        assert fact.id in recall_ids(agent, other.content, k=1)

    def test_a_lie_smuggled_over_an_unrelated_fact_is_noticed(
        self, agent: MemoryService, fact: Memory, clock: FakeClock
    ) -> None:
        own = remember(agent, "Luis vive en Valencia")
        clock.advance(hours=1)
        lie = supersede(agent, own.id, LIE)
        assert recall_ids(agent, LIE, k=1) == [fact.id, lie.id]

    def test_the_lie_was_there_before_the_truth(
        self, agent: MemoryService, person: MemoryService, clock: FakeClock
    ) -> None:
        lie = remember(agent, LIE)
        clock.advance(hours=1)
        truth = remember(person, TRUE)
        assert recall_ids(agent, LIE, k=1) == [truth.id, lie.id]

    def test_the_warning_follows_the_fact_when_a_person_updates_it(
        self, agent: MemoryService, person: MemoryService, fact: Memory, clock: FakeClock
    ) -> None:
        lie = remember(agent, LIE)
        clock.advance(hours=1)
        newer = supersede(person, fact.id, "Lumbre cobra por domiciliación bancaria")
        assert recall_ids(agent, LIE, k=1) == [newer.id, lie.id]

    def test_unrelated_memories_are_not_touched(self, agent: MemoryService, fact: Memory) -> None:
        other = remember(agent, "Luis toca la guitarra")
        result = agent.recall(RecallQuery(query="guitarra Luis", k=1))
        assert [hit.memory.id for hit in result.results] == [other.id]
        assert result.warnings == []

    def test_saying_the_same_again_is_not_a_contradiction(
        self, agent: MemoryService, fact: Memory
    ) -> None:
        remember(agent, TRUE)
        assert agent.recall(RecallQuery(query=QUESTION)).warnings == []


class TestAPersonDecides:
    def test_accepting_replaces_the_fact_and_keeps_it_protected(
        self, person: MemoryService, agent: MemoryService, fact: Memory, clock: FakeClock
    ) -> None:
        change = attempt(agent, old_id=fact.id, new_content=LIE, reason="Cambio de banco")
        new = person.accept(change).new
        assert (new.content, new.origin, new.supersede_reason) == (
            LIE,
            Origin.PERSON,
            "Cambio de banco",
        )
        assert agent.get(fact.id).superseded_by_id == new.id
        assert agent.pending() == []
        assert agent.recall(RecallQuery(query=QUESTION)).warnings == []
        clock.advance(hours=1)
        attempt(agent, old_id=new.id, new_content="Otra cuenta distinta")
        assert person.doctor() == []

    def test_accepting_a_link_makes_the_new_version_the_persons(
        self, person: MemoryService, agent: MemoryService, fact: Memory
    ) -> None:
        lie = remember(agent, LIE)
        person.accept(attempt(agent, old_id=fact.id, new_id=lie.id))
        assert agent.get(lie.id).origin is Origin.PERSON
        assert agent.recall(RecallQuery(query=QUESTION)).warnings == []

    def test_rejecting_erases_the_text(
        self, person: MemoryService, agent: MemoryService, fact: Memory, db_path: Path
    ) -> None:
        person.reject(attempt(agent, old_id=fact.id, new_content=LIE))
        assert agent.pending() == []
        assert b"9902" not in disk_bytes(db_path)
        assert agent.recall(RecallQuery(query=QUESTION)).warnings == []

    def test_confirming_protects_what_an_agent_saved(
        self, person: MemoryService, agent: MemoryService, clock: FakeClock
    ) -> None:
        own = remember(agent, TRUE)
        assert person.confirm(own.id).origin is Origin.PERSON
        clock.advance(hours=1)
        attempt(agent, old_id=own.id, new_content=LIE)
        assert still_holds(agent, own)

    def test_an_agent_can_do_none_of_it(self, agent: MemoryService, fact: Memory) -> None:
        change = attempt(agent, old_id=fact.id, new_content=LIE)
        for forbidden in (agent.accept, agent.reject, agent.confirm):
            with pytest.raises(ProtectedError):
                forbidden(change)
        assert len(agent.pending()) == 1

    def test_an_unknown_pending_change(self, person: MemoryService) -> None:
        with pytest.raises(NotFoundError):
            person.accept("01J0000000000000000000000Z")


class TestSameOriginWorksAsBefore:
    @pytest.mark.parametrize("who", ["agent", "person"])
    def test_replacing_linking_and_forgetting(
        self, agent: MemoryService, person: MemoryService, clock: FakeClock, who: str
    ) -> None:
        service = agent if who == "agent" else person
        first = remember(service, TRUE)
        clock.advance(hours=1)
        second = supersede(service, first.id, LIE)
        clock.advance(hours=1)
        third = remember(service, "El proveedor Lumbre cobra por domiciliación")
        service.supersede(SupersedeRequest(old_id=second.id, new_id=third.id))
        result = service.recall(RecallQuery(query=QUESTION))
        assert [hit.memory.id for hit in result.results] == [third.id]
        assert result.warnings == [] and service.pending() == []
        assert service.forget(third.id).deleted_ids == [first.id, second.id, third.id]

    def test_a_person_may_replace_what_an_agent_saved(
        self, person: MemoryService, agent: MemoryService, clock: FakeClock
    ) -> None:
        own = remember(agent, LIE)
        clock.advance(hours=1)
        assert supersede(person, own.id, TRUE).origin is Origin.PERSON

    def test_an_agent_is_still_free_to_replace_what_another_agent_saved(
        self, agent: MemoryService, clock: FakeClock
    ) -> None:
        """Límite conocido: el mismo origen no protege. Por eso existe `confirm`."""
        own = remember(agent, TRUE)
        clock.advance(hours=1)
        lie = supersede(agent, own.id, LIE)
        assert recall_ids(agent, QUESTION) == [lie.id]


class TestFoundInTheReview:
    """Ataques de una revisión independiente, antes de publicar. Todos funcionaban."""

    def test_what_is_accepted_is_what_was_shown(
        self, person: MemoryService, agent: MemoryService, fact: Memory, clock: FakeClock
    ) -> None:
        harmless = remember(agent, "Lumbre renueva el contrato en mayo")
        change = attempt(agent, old_id=fact.id, new_id=harmless.id)
        clock.advance(hours=1)
        supersede(agent, harmless.id, LIE)  # se cambia lo que se aceptaría
        assert person.preview(person.pending()[0]).stale
        with pytest.raises(AlreadySupersededError):
            person.accept(change)
        assert still_holds(agent, fact)

    def test_a_person_cannot_link_to_a_version_that_no_longer_holds(
        self, person: MemoryService, agent: MemoryService, fact: Memory, clock: FakeClock
    ) -> None:
        harmless = remember(agent, "Lumbre renueva el contrato en mayo")
        clock.advance(hours=1)
        supersede(agent, harmless.id, LIE)
        with pytest.raises(AlreadySupersededError):
            person.supersede(SupersedeRequest(old_id=fact.id, new_id=harmless.id))

    def test_an_agent_writes_nothing_into_a_persons_fact(
        self, agent: MemoryService, person: MemoryService, fact: Memory
    ) -> None:
        bait = remember(agent, LIE, valid_from="2001-01-01")
        attempt(agent, old_id=bait.id, new_id=fact.id, reason="La cuenta buena acaba en 9902")
        stored = agent.get(fact.id)
        assert (stored.supersedes_id, stored.supersede_reason) == (None, None)
        assert person.forget(bait.id).deleted_ids == [bait.id]

    def test_what_is_outside_writes_nothing_into_an_agents_fact(
        self, agent: MemoryService, clock: FakeClock
    ) -> None:
        bait = agent.remember(NewMemory(content=LIE), external=True).memory
        clock.advance(hours=1)
        own = remember(agent, TRUE)
        attempt(agent, external=True, old_id=bait.id, new_id=own.id, reason="Motivo de fuera")
        assert agent.get(own.id).supersede_reason is None

    def test_a_reason_planted_earlier_is_not_accepted_with_the_link(
        self, person: MemoryService, agent: MemoryService, fact: Memory, clock: FakeClock
    ) -> None:
        own = remember(agent, "Lumbre cobraba en efectivo", valid_from="2025-01-01")
        clock.advance(hours=1)
        new = supersede(agent, own.id, LIE, reason="Motivo que no se ve")
        person.accept(attempt(agent, old_id=fact.id, new_id=new.id, reason="Cambio de banco"))
        assert person.get(new.id).supersede_reason == "Cambio de banco"

    def test_another_request_does_not_inherit_the_date_of_the_first(
        self, agent: MemoryService, fact: Memory
    ) -> None:
        attempt(agent, old_id=fact.id, new_content=LIE, valid_from="2400-01-01")
        attempt(agent, old_id=fact.id, new_content=LIE)
        assert {c.valid_from is None for c in agent.pending()} == {False, True}

    def test_tags_come_from_the_most_trusted_fact(
        self, person: MemoryService, agent: MemoryService, clock: FakeClock
    ) -> None:
        own = remember(agent, "Luis vive en Valencia", tags=["ajena"])
        theirs = remember(person, "Ana vive en Madrid", tags=["familia"])
        clock.advance(hours=1)
        change = attempt(
            agent, old_id=own.id, also_old_ids=[theirs.id], new_content="Ana y Luis van a Lisboa"
        )
        assert person.preview(person.pending()[0]).tags == ["familia"]
        assert person.accept(change).new.tags == ["familia"]

    def test_confirming_something_already_replaced_is_an_error(
        self, person: MemoryService, agent: MemoryService, clock: FakeClock
    ) -> None:
        own = remember(agent, TRUE)
        clock.advance(hours=1)
        supersede(agent, own.id, LIE)
        with pytest.raises(AlreadySupersededError):
            person.confirm(own.id)

    def test_the_truth_also_comes_through_another_lie(
        self, agent: MemoryService, fact: Memory, clock: FakeClock
    ) -> None:
        lie = remember(agent, LIE)
        clock.advance(hours=1)
        other = agent.remember(
            NewMemory(content="Transferencias urgentes por ventanilla"), external=True
        ).memory
        attempt(agent, external=True, old_id=lie.id, new_id=other.id)
        result = agent.recall(RecallQuery(query="transferencias urgentes ventanilla", k=1))
        assert [hit.memory.id for hit in result.results] == [fact.id, lie.id, other.id]
        assert any(w.startswith(lie.id) and fact.id in w for w in result.warnings)

    def test_a_lie_dated_in_the_future(
        self, agent: MemoryService, person: MemoryService, clock: FakeClock
    ) -> None:
        lie = remember(agent, LIE, valid_from="2026-02-01")
        truth = remember(person, TRUE)
        clock.advance(days=60)
        assert recall_ids(agent, LIE, k=1) == [truth.id, lie.id]

    def test_a_lie_about_a_change_already_announced(
        self, agent: MemoryService, person: MemoryService, fact: Memory, clock: FakeClock
    ) -> None:
        newer = supersede(
            person, fact.id, "El proveedor Lumbre cobrará por giro", valid_from="2026-02-01"
        )
        lie = remember(agent, LIE)
        clock.advance(days=60)
        assert recall_ids(agent, LIE, k=1) == [newer.id, lie.id]

    def test_an_earlier_lie_told_as_an_episode(
        self, agent: MemoryService, person: MemoryService, clock: FakeClock
    ) -> None:
        lie = remember(agent, LIE, kind="episode")
        clock.advance(hours=1)
        truth = remember(person, TRUE)
        assert recall_ids(agent, LIE, k=1) == [truth.id, lie.id]

    def test_many_earlier_copies(
        self, agent: MemoryService, person: MemoryService, clock: FakeClock
    ) -> None:
        lies = [remember(agent, f"{LIE} según el aviso {number}") for number in range(12)]
        clock.advance(hours=1)
        truth = remember(person, TRUE)
        for lie in lies:
            assert recall_ids(agent, lie.content, k=1) in ([truth.id, lie.id], [truth.id])

    def test_an_exact_copy_does_not_outlive_the_fact(
        self, agent: MemoryService, person: MemoryService, fact: Memory, clock: FakeClock
    ) -> None:
        copy = remember(agent, TRUE)
        clock.advance(hours=1)
        newer = supersede(person, fact.id, "Lumbre cobra por domiciliación bancaria")
        assert recall_ids(agent, TRUE, k=1) == [newer.id, copy.id]

    def test_accepting_a_link_looks_for_older_lies(
        self, person: MemoryService, agent: MemoryService, fact: Memory, clock: FakeClock
    ) -> None:
        lie = remember(agent, "Lumbre exige pagos por giro postal a Vigo")
        new = remember(agent, "Lumbre exige pagos por giro postal a Lugo")
        person.accept(attempt(agent, old_id=fact.id, new_id=new.id))
        assert recall_ids(agent, lie.content, k=1) == [new.id, lie.id]

    def test_a_change_about_something_forgotten_meanwhile_is_not_kept(
        self, agent: MemoryService, fact: Memory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        own = remember(agent, "Luis vive en Valencia")
        leave = agent._leave_pending

        def forgotten_first(request: SupersedeRequest, actor: Origin) -> NoReturn:
            agent.forget(own.id)
            leave(request, actor)

        monkeypatch.setattr(agent, "_leave_pending", forgotten_first)
        with pytest.raises(NotFoundError):
            agent.supersede(
                SupersedeRequest(old_id=own.id, also_old_ids=[fact.id], new_content=LIE)
            )
        assert agent.pending() == []

    def test_forgetting_does_not_withdraw_a_change_that_waits_for_a_person(
        self, agent: MemoryService, person: MemoryService, fact: Memory, clock: FakeClock
    ) -> None:
        own = remember(agent, "Luis vive en Valencia")
        clock.advance(hours=1)
        attempt(agent, old_id=own.id, also_old_ids=[fact.id], new_content=LIE)
        with pytest.raises(ProtectedError):
            agent.forget(own.id)
        person.forget(own.id)
        assert person.pending() == []

    def test_rejecting_keeps_the_warning(
        self, person: MemoryService, agent: MemoryService, fact: Memory, clock: FakeClock
    ) -> None:
        lie = remember(agent, LIE)
        clock.advance(hours=1)
        other = supersede(agent, lie.id, "Pagos a Lumbre: solo transferencia inmediata")
        result = person.reject(attempt(agent, old_id=fact.id, new_id=other.id))
        assert result.kept_id == other.id
        assert recall_ids(agent, "Pagos transferencia inmediata", k=1) == [fact.id, other.id]

    def test_forgetting_the_proposed_memory_does_not_withdraw_the_change(
        self, agent: MemoryService, fact: Memory
    ) -> None:
        other = remember(agent, "Luis toca la guitarra")
        attempt(agent, old_id=fact.id, new_id=other.id)
        with pytest.raises(ProtectedError):
            agent.forget(other.id)

    def test_a_lie_about_the_past(
        self, agent: MemoryService, person: MemoryService, clock: FakeClock
    ) -> None:
        lie = remember(agent, LIE, valid_from="2019-01-01")
        supersede(agent, lie.id, "Lumbre ya no trabaja con nosotros", valid_from="2025-01-01")
        truth = remember(person, TRUE, valid_from="2019-01-01")
        assert recall_ids(agent, LIE, k=1, as_of="2022-01-01") == [truth.id, lie.id]

    def test_a_lie_about_a_fact_already_closed(
        self, agent: MemoryService, person: MemoryService
    ) -> None:
        truth = remember(person, TRUE, valid_from="2019-01-01")
        supersede(person, truth.id, "Lumbre ya no es proveedor", valid_from="2024-01-01")
        lie = remember(agent, LIE, valid_from="2019-06-01")
        assert recall_ids(agent, LIE, k=1, as_of="2022-01-01") == [truth.id, lie.id]

    def test_one_capital_letter_is_enough_to_disagree(
        self, agent: MemoryService, person: MemoryService
    ) -> None:
        truth = remember(person, "Los pagos van a pay.example/aXk7Qd")
        lie = remember(agent, "Los pagos van a pay.example/AxK7qD")
        assert recall_ids(agent, lie.content, k=1)[0] == truth.id

    def test_confirming_does_not_adopt_the_agents_reason(
        self, agent: MemoryService, person: MemoryService, clock: FakeClock
    ) -> None:
        own = remember(agent, "Luis vive en Valencia")
        clock.advance(hours=1)
        new = supersede(agent, own.id, "Luis vive en Vigo", reason="Y paga en la cuenta 9902")
        assert person.confirm(new.id).supersede_reason is None

    def test_a_similar_memory_deleted_meanwhile_does_not_lose_the_rest(
        self, agent: MemoryService, fact: Memory, db_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Se busca y se apunta dentro de la misma transacción: nadie puede borrar entre medias."""
        inside: list[bool] = []
        note = agent._note_clashes

        def watched(memory: Memory, probe: list[float] | None) -> None:
            inside.append(agent._conn.in_transaction)
            note(memory, probe)

        monkeypatch.setattr(agent, "_note_clashes", watched)
        remember(agent, LIE)
        assert inside == [True]

    def test_one_memory_does_not_drag_the_whole_database_along(
        self, agent: MemoryService, person: MemoryService, clock: FakeClock
    ) -> None:
        for n in range(MAX_SHOWN + 10):
            remember(person, f"El proyecto Atlas tiene la tarea {n}")
        clock.advance(hours=1)
        lie = remember(agent, "El proyecto Atlas tiene la tarea pendiente")
        facts = [m.id for m in person.list_memories(ListQuery(limit=100)).items]
        for target in facts[1:]:
            attempt(agent, old_id=target, new_id=lie.id)
        result = agent.recall(RecallQuery(query=lie.content, k=1))
        assert len(result.results) <= MAX_SHOWN + 1
        assert any("no se muestran" in w for w in result.warnings)

    def test_one_word_in_common_does_not_tie_everything(
        self, agent: MemoryService, person: MemoryService, clock: FakeClock
    ) -> None:
        own = remember(agent, "Atlas usa FastAPI con PostgreSQL y despliega en Hetzner")
        for n in range(100, 130):
            remember(person, f"Reunión número {n} con el cliente sobre Atlas en la oficina")
        clock.advance(hours=1)
        assert recall_ids(agent, own.content, k=1) == [own.id]
        supersede(agent, own.id, "Atlas usa Go y despliega los lunes por la mañana")

    def test_no_history_is_added_to_what_was_shown(
        self, agent: MemoryService, person: MemoryService, fact: Memory, clock: FakeClock
    ) -> None:
        bait = remember(agent, "Lumbre cobraba en la cuenta 9902", valid_from="2001-01-01")
        new = remember(agent, "Lumbre cobra por giro")
        attempt(agent, old_id=fact.id, new_id=new.id)
        with pytest.raises(ProtectedError):
            agent.supersede(SupersedeRequest(old_id=bait.id, new_id=new.id))
        assert person.preview(person.pending()[0]).already_replaced == []

    def test_the_history_it_already_had_is_shown(
        self, agent: MemoryService, person: MemoryService, fact: Memory, clock: FakeClock
    ) -> None:
        bait = remember(agent, "Lumbre cobraba en la cuenta 9902", valid_from="2001-01-01")
        new = remember(agent, "Lumbre cobra por giro")
        agent.supersede(SupersedeRequest(old_id=bait.id, new_id=new.id))
        attempt(agent, old_id=fact.id, new_id=new.id)
        shown = person.preview(person.pending()[0]).already_replaced
        assert [memory.id for memory in shown] == [bait.id]


class TestInterfaces:
    @pytest.mark.anyio
    async def test_the_mcp_server_is_never_a_person(self, db_path: Path, clock: FakeClock) -> None:
        with MemoryServiceAsPerson(db_path, clock) as person:
            fact = remember(person, TRUE)
            clock.advance(hours=1)
            server = create_server(Settings(db_path=db_path, workspace="test"), clock=clock)
            async with Client(server) as client:
                asked = await client.call_tool("supersede", {"old_id": fact.id, "new_content": LIE})
                assert not asked.is_error and asked.structured_content is not None
                assert asked.structured_content["new"] is None
                assert asked.structured_content["old"]["origin"] == "person"
                assert "No lo reintentes" in asked.structured_content["warnings"][0]

                forgotten = await client.call_tool("forget", {"memory_id": fact.id})
                assert forgotten.is_error and "protected" in forgotten.content[0].text  # type: ignore[union-attr]

                saved = await client.call_tool("remember", {"content": LIE, "from_outside": True})
                assert saved.structured_content is not None
                assert saved.structured_content["memory"]["origin"] == "external"
                assert "no lo reemplaces" in saved.structured_content["next_step"]

                tools = {tool.name for tool in (await client.list_tools()).tools}
            assert tools.isdisjoint({"accept", "reject", "confirm", "pending"})
            assert still_holds(person, fact)

    def test_the_cli_is_a_person_and_decides(
        self, db_path: Path, monkeypatch: pytest.MonkeyPatch, clock: FakeClock
    ) -> None:
        monkeypatch.setenv("LASTWORD_EMBED_MODEL", "none")
        runner = CliRunner()

        def run(*args: str) -> str:
            result = runner.invoke(app, ["--db", str(db_path), "--workspace", "test", *args])
            assert result.exit_code == 0, result.output
            return result.stdout

        added = json.loads(run("--json", "add", TRUE, "--valid-from", "2026-01-01"))["memory"]
        assert added["origin"] == "person"
        agent = MemoryService.open(db_path, "test")
        first = attempt(agent, old_id=added["id"], new_content=LIE)
        assert "9902" in run("pending") and "agente" in run("pending")
        assert "Rechazado" in run("reject", first)
        assert "Ningún cambio pendiente" in run("pending")
        second = attempt(agent, old_id=added["id"], new_content=LIE)
        assert "Reemplazada" in run("accept", second)
        own = remember(agent, "Luis vive en Valencia")
        assert "Confirmada" in run("confirm", own.id)
        assert agent.get(own.id).origin is Origin.PERSON
        agent.close()

    def test_pending_shows_all_that_would_be_applied(
        self, db_path: Path, monkeypatch: pytest.MonkeyPatch, clock: FakeClock
    ) -> None:
        monkeypatch.setenv("LASTWORD_EMBED_MODEL", "none")
        with MemoryServiceAsPerson(db_path, clock) as person:
            fact = remember(person, TRUE, tags=["pagos"])
        agent = MemoryService.open(db_path, "test")
        # Dos trozos que, unidos, formarían una marca de Rich; y una línea que imita a otra.
        bait = remember(agent, "Uno [conceal", valid_from="2026-01-01")
        tail = remember(agent, "] dos [/", valid_from="2026-01-01")
        request = SupersedeRequest(
            old_id=bait.id,
            also_old_ids=[tail.id, fact.id],
            new_content="Cuenta 9902\npedido por: persona",
            reason="Motivo",
            valid_from="2027-03-04",
        )
        with pytest.raises(PendingConfirmationError):
            agent.supersede(request)
        agent.close()
        result = CliRunner().invoke(app, ["--db", str(db_path), "-w", "test", "pending"])
        shown = " ".join(result.output.split())
        assert result.exit_code == 0
        for part in ("Uno [conceal", "] dos [/", "4471", "⏎ pedido por: persona"):
            assert part in shown
        for part in ("desde 2027-03-04", "etiquetas pagos", "motivo Motivo"):
            assert part in shown

    @pytest.mark.parametrize("width", ["24", "40", "60", "80"])
    def test_pending_never_crops_the_text(
        self, db_path: Path, monkeypatch: pytest.MonkeyPatch, clock: FakeClock, width: str
    ) -> None:
        monkeypatch.setenv("LASTWORD_EMBED_MODEL", "none")
        with MemoryServiceAsPerson(db_path, clock) as person:
            fact = remember(person, TRUE)
        agent = MemoryService.open(db_path, "test")
        change = attempt(
            agent, old_id=fact.id, new_content="Cobra en la 4471 o en la 9902 desde hoy"
        )
        agent.close()
        env = {**os.environ, "COLUMNS": width, "LASTWORD_LANG": "es"}
        for command, expected in (
            ("pending", (change, "9902", "4471", "hoy")),
            ("list", ("4471",)),
        ):
            shown = subprocess.run(
                [sys.executable, "-m", "lastword", "--db", db_path, "-w", "test", command],
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
            ).stdout
            together = "".join(shown.split())
            assert all(part in together for part in expected), shown


class MemoryServiceAsPerson:
    """Un servicio abierto como persona que se cierra al salir."""

    def __init__(self, db_path: Path, clock: FakeClock) -> None:
        self._service = MemoryService.open(db_path, "test", clock=clock, origin=Origin.PERSON)

    def __enter__(self) -> MemoryService:
        return self._service

    def __exit__(self, *_: object) -> None:
        self._service.close()


def test_upgrade_gives_the_persons_origin_only_to_what_came_from_the_cli(db_path: Path) -> None:
    raw = sqlite3.connect(db_path, autocommit=True)
    for step in store._MIGRATIONS[:6]:
        if isinstance(step, str):
            raw.executescript(step)
    raw.execute("PRAGMA user_version = 6")
    raw.execute("INSERT INTO workspaces (id, created_at) VALUES ('test', 0)")
    for seq, source in enumerate(("cli", "mcp", None), start=1):
        raw.execute(
            "INSERT INTO memories (seq, id, workspace_id, kind, content, created_at, valid_from, "
            "status, source) VALUES (?, ?, 'test', 'fact', 'x', 0, 0, 'active', ?)",
            (seq, f"01KE0000000000000000000{seq:03d}", source),
        )
    raw.close()
    conn = store.connect(db_path)
    rows = conn.execute("SELECT source, origin FROM memories ORDER BY seq").fetchall()
    assert [tuple(row) for row in rows] == [("cli", "person"), ("mcp", "agent"), (None, "agent")]
    conn.close()


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
