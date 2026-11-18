"""Modelos de dominio y de entrada.

Toda entrada externa (CLI, MCP) se valida con estos modelos antes de llegar a SQL.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    model_validator,
)

MAX_CONTENT_CHARS = 4000
MAX_TAGS = 20
MAX_K = 50
MAX_LIST_LIMIT = 100


class Kind(StrEnum):
    FACT = "fact"  # algo que es verdad durante un tiempo y puede cambiar
    EPISODE = "episode"  # algo que pasó; no se reemplaza


class SearchMode(StrEnum):
    """Cómo busca `recall`. Por defecto, vector: ganó en E1 y E2 (eval/results/decisions.md)."""

    VECTOR = "vector"  # por significado
    HYBRID = "hybrid"  # significado + palabras, fusionados con RRF
    TEXT = "text"  # solo palabras (BM25)


class Status(StrEnum):
    ACTIVE = "active"  # no tiene sucesor
    SUPERSEDED = "superseded"  # otra memoria la reemplazó


def _as_utc(value: datetime) -> datetime:
    """Las fechas sin zona horaria se interpretan como UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


UtcDatetime = Annotated[datetime, AfterValidator(_as_utc)]
WorkspaceId = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")]
# Pydantic comprueba `pattern` antes de `to_upper`, así que el patrón acepta minúsculas
# y la conversión se hace después, de forma explícita.
MemoryId = Annotated[
    str,
    StringConstraints(strip_whitespace=True, pattern=r"^[0-9A-HJKMNP-TV-Za-hjkmnp-tv-z]{26}$"),
    AfterValidator(str.upper),
]
Content = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_CONTENT_CHARS)
]
Tag = Annotated[
    str, StringConstraints(strip_whitespace=True, to_lower=True, pattern=r"^[\w.:/-]{1,40}$")
]
Source = Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)]


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class NewMemory(_Input):
    content: Content
    kind: Kind = Kind.FACT
    tags: list[Tag] = Field(default_factory=list, max_length=MAX_TAGS)
    source: Source | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    valid_from: UtcDatetime | None = None


class SupersedeRequest(_Input):
    """La versión nueva se indica de una de dos formas:

    - `new_content`: se crea ahora (el agente ya sabía qué hecho cambiaba).
    - `new_id`: ya se guardó con remember, que la propuso como reemplazo. Enlazarla evita
      duplicar el hecho; su vigencia y demás datos son los de esa memoria.
    """

    old_id: MemoryId
    new_content: Content | None = None
    new_id: MemoryId | None = None
    valid_from: UtcDatetime | None = None
    reason: str | None = Field(default=None, max_length=500)
    tags: list[Tag] | None = Field(default=None, max_length=MAX_TAGS)  # None = hereda
    source: Source | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _exactly_one_new_version(self) -> Self:
        if (self.new_content is None) == (self.new_id is None):
            raise ValueError("indica new_content o new_id (uno de los dos)")
        extras = (self.valid_from, self.tags, self.source, self.confidence)
        if self.new_id is not None and any(value is not None for value in extras):
            raise ValueError("con new_id, la vigencia y los demás datos son los de esa memoria")
        return self


class RecallQuery(_Input):
    query: str = Field(min_length=1, max_length=1000)
    k: int = Field(default=5, ge=1, le=MAX_K)
    as_of: UtcDatetime | None = None
    include_history: bool = False
    kind: Kind | None = None

    @model_validator(mode="after")
    def _history_excludes_as_of(self) -> Self:
        if self.include_history and self.as_of is not None:
            raise ValueError("include_history y as_of no se pueden combinar")
        return self


class ListQuery(_Input):
    kind: Kind | None = None
    status: Status | None = None
    tag: Tag | None = None
    limit: int = Field(default=20, ge=1, le=MAX_LIST_LIMIT)
    cursor: str | None = Field(default=None, pattern=r"^\d{1,18}$")


class Memory(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    workspace_id: str
    kind: Kind
    content: str
    tags: list[str]
    source: str | None = None
    confidence: float | None = None  # estimada al guardar; no es la relevancia de una búsqueda
    created_at: datetime  # cuándo se registró
    valid_from: datetime  # desde cuándo es verdad
    valid_to: datetime | None = None  # hasta cuándo; None = sin fecha de fin
    status: Status = Status.ACTIVE
    supersedes_id: str | None = None
    superseded_by_id: str | None = None
    supersede_reason: str | None = None  # por qué esta versión reemplazó a la anterior
    embedding_model: str | None = None  # modelo del último vector calculado; None = sin vector


class Hit(BaseModel):
    model_config = ConfigDict(frozen=True)

    memory: Memory
    score: float  # relevancia para esta consulta (mayor = mejor; no comparable entre consultas)
    matched_by: list[str]  # "fts", "vector" o ambos
    similarity: float | None = None  # similitud coseno, si la encontró la búsqueda vectorial


class RememberResult(BaseModel):
    memory: Memory
    possible_supersedes: list[Hit]
    warnings: list[str] = Field(default_factory=list)


class SupersedeResult(BaseModel):
    new: Memory
    old: Memory
    warnings: list[str] = Field(default_factory=list)


class RecallResult(BaseModel):
    mode: Literal["vector", "hybrid", "fts_only"]  # el que se usó de verdad
    results: list[Hit]
    warnings: list[str] = Field(default_factory=list)


class ReindexResult(BaseModel):
    model: str
    dim: int | None
    embedded: int


class Page(BaseModel):
    items: list[Memory]
    next_cursor: str | None


def describe_validation_error(exc: ValidationError) -> str:
    """Qué campo falló y por qué, sin repetir el valor enviado: puede ser el contenido de una
    memoria y acabar en logs."""
    parts = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"]) or "entrada"
        parts.append(f"{location}: {error['msg']}")
    return "; ".join(parts)
