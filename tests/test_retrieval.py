from __future__ import annotations

from datetime import timedelta

import pytest
from pydantic import ValidationError

from lastword.models import Kind, RecallQuery, Status
from lastword.retrieval import MAX_QUERY_TOKENS, fts_match_expression
from lastword.service import MemoryService
from tests.helpers import T0, FakeClock, recall_ids, remember, supersede


class TestMatchExpression:
    def test_quotes_every_meaningful_token(self) -> None:
        assert fts_match_expression("Ana vive en Madrid") == '"ana" OR "vive" OR "madrid"'

    def test_neutralizes_fts_syntax(self) -> None:
        expression = fts_match_expression('foo" OR bar* NEAR(x) -baz')
        assert expression == '"foo" OR "bar" OR "near" OR "x" OR "baz"'

    def test_keeps_stopwords_when_nothing_else_is_left(self) -> None:
        assert fts_match_expression("¿Qué es?") == '"qué" OR "es"'

    @pytest.mark.parametrize("text", ["", "   ", "¿?!*()"])
    def test_returns_none_without_tokens(self, text: str) -> None:
        assert fts_match_expression(text) is None

    def test_deduplicates_and_caps_tokens(self) -> None:
        text = " ".join(f"palabra{i}" for i in range(100)) + " palabra0"
        expression = fts_match_expression(text)
        assert expression is not None
        assert expression.count(" OR ") == MAX_QUERY_TOKENS - 1


@pytest.fixture
def database_switch(service: MemoryService, clock: FakeClock) -> tuple[str, str]:
    """El ejemplo del README: PostgreSQL hasta el día 60, SQLite desde entonces."""
    postgres = remember(service, "El proyecto usa PostgreSQL 15 como base de datos")
    clock.advance(days=60)
    sqlite = supersede(service, postgres.id, "El proyecto migró a SQLite como base de datos")
    clock.advance(days=10)
    return postgres.id, sqlite.id


class TestRecall:
    def test_by_default_only_what_is_true_now(
        self, service: MemoryService, database_switch: tuple[str, str]
    ) -> None:
        _, sqlite = database_switch
        assert recall_ids(service, "¿Qué base de datos usa el proyecto?") == [sqlite]

    def test_as_of_answers_what_was_true_then(
        self, service: MemoryService, database_switch: tuple[str, str]
    ) -> None:
        postgres, sqlite = database_switch
        query = "base de datos del proyecto"
        assert recall_ids(service, query, as_of=T0 + timedelta(days=30)) == [postgres]
        assert recall_ids(service, query, as_of=T0 + timedelta(days=60)) == [sqlite]
        assert recall_ids(service, query, as_of=T0 - timedelta(days=1)) == []

    def test_history_returns_every_version_marked(
        self, service: MemoryService, database_switch: tuple[str, str]
    ) -> None:
        postgres, sqlite = database_switch
        result = service.recall(RecallQuery(query="base de datos", include_history=True))
        statuses = {hit.memory.id: hit.memory.status for hit in result.results}
        assert statuses == {postgres: Status.SUPERSEDED, sqlite: Status.ACTIVE}

    def test_history_and_as_of_cannot_be_combined(self) -> None:
        with pytest.raises(ValidationError):
            RecallQuery(query="x", include_history=True, as_of=T0)

    def test_future_facts_appear_once_they_start(
        self, service: MemoryService, clock: FakeClock
    ) -> None:
        start = clock.now + timedelta(days=30)
        marta = remember(service, "Marta será la responsable de pagos", valid_from=start)
        assert recall_ids(service, "responsable de pagos") == []
        clock.advance(days=31)
        assert recall_ids(service, "responsable de pagos") == [marta.id]

    def test_matches_without_accents(self, service: MemoryService) -> None:
        memory = remember(service, "La migración terminó en marzo")
        assert recall_ids(service, "migracion") == [memory.id]
        assert recall_ids(service, "MIGRACIÓN") == [memory.id]

    @pytest.mark.parametrize(
        "query", ['"', "*", "NEAR(", "') OR 1=1 --", "a" * 1000, "migración*", "content:x"]
    )
    def test_hostile_queries_are_harmless(self, service: MemoryService, query: str) -> None:
        memory = remember(service, "La migración terminó en marzo")
        service.recall(RecallQuery(query=query))
        assert service.get(memory.id) == memory

    def test_filters_by_kind(self, service: MemoryService) -> None:
        fact = remember(service, "Ana vive en Madrid")
        episode = remember(service, "Ana visitó Madrid en mayo", kind="episode")
        assert recall_ids(service, "Ana Madrid", kind=Kind.FACT) == [fact.id]
        assert recall_ids(service, "Ana Madrid", kind=Kind.EPISODE) == [episode.id]

    def test_respects_k_and_orders_by_score(self, service: MemoryService) -> None:
        for number in range(8):
            remember(service, f"nota {number} sobre pagos")
        remember(service, "pagos pagos pagos: todo sobre pagos")
        result = service.recall(RecallQuery(query="pagos", k=3))
        scores = [hit.score for hit in result.results]
        assert len(scores) == 3
        assert scores == sorted(scores, reverse=True)
        assert result.results[0].memory.content.startswith("pagos pagos")
        assert result.mode == "fts_only"
        assert all(hit.matched_by == ["fts"] for hit in result.results)
