"""Configuración desde variables de entorno. Los secretos solo llegan por entorno."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from lastword import environment
from lastword.embeddings import DEFAULT_BASE_URL, OpenAICompatibleEmbedder
from lastword.environment import DEFAULT_DB_PATH
from lastword.i18n import tr
from lastword.models import SearchMode

DEFAULT_EMBED_MODEL = "embeddinggemma"  # ver ADR-0004
DEFAULT_WORKSPACE = "default"
_DISABLED = {"", "none", "off", "false", "0"}
_ENABLED = {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class EmbeddingSettings:
    model: str | None  # None = sin embeddings (solo búsqueda de texto)
    base_url: str = DEFAULT_BASE_URL
    api_key: str | None = None
    allow_remote: bool = False

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str] = os.environ,
        *,
        model: str | None = None,
        base_url: str | None = None,
    ) -> EmbeddingSettings:
        """Lee LASTWORD_EMBED_*; `model` y `base_url`, si se pasan, tienen prioridad."""
        raw_model = environment.value("EMBED_MODEL", env) if model is None else model
        if raw_model is None:
            raw_model = DEFAULT_EMBED_MODEL
        return cls(
            model=None if raw_model.strip().lower() in _DISABLED else raw_model.strip(),
            base_url=base_url or environment.value("EMBED_BASE_URL", env) or DEFAULT_BASE_URL,
            api_key=environment.value("EMBED_API_KEY", env) or None,
            allow_remote=(environment.value("ALLOW_REMOTE", env) or "").strip().lower() in _ENABLED,
        )

    def build(self) -> OpenAICompatibleEmbedder | None:
        if self.model is None:
            return None
        return OpenAICompatibleEmbedder(
            self.model,
            base_url=self.base_url,
            api_key=self.api_key,
            allow_remote=self.allow_remote,
        )


@dataclass(frozen=True)
class Settings:
    """Configuración completa: dónde está la memoria, qué workspace y qué embeddings."""

    db_path: Path = DEFAULT_DB_PATH
    workspace: str = DEFAULT_WORKSPACE
    embedding: EmbeddingSettings = field(default_factory=lambda: EmbeddingSettings(model=None))
    search_mode: SearchMode = SearchMode.VECTOR

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> Settings:
        return cls(
            db_path=Path(
                environment.value("DB", env) or environment.default_db_path()
            ).expanduser(),
            workspace=environment.value("WORKSPACE", env) or DEFAULT_WORKSPACE,
            embedding=EmbeddingSettings.from_env(env),
            search_mode=parse_search_mode(environment.value("SEARCH_MODE", env) or ""),
        )


def parse_search_mode(raw: str) -> SearchMode:
    """Un único criterio para la CLI y el servidor MCP: sin distinguir mayúsculas ni espacios."""
    value = raw.strip().lower()
    if not value:
        return SearchMode.VECTOR
    try:
        return SearchMode(value)
    except ValueError:
        options = ", ".join(mode.value for mode in SearchMode)
        raise ValueError(
            tr(
                "el modo de búsqueda {mode} no existe; usa {options}",
                mode=repr(raw),
                options=options,
            )
        ) from None
