"""Configuración desde variables de entorno. Los secretos solo llegan por entorno."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from vigente.embeddings import DEFAULT_BASE_URL, OpenAICompatibleEmbedder

DEFAULT_EMBED_MODEL = "embeddinggemma"  # ver ADR-0004
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
        """Lee VIGENTE_EMBED_*; `model` y `base_url`, si se pasan, tienen prioridad."""
        raw_model = (
            model if model is not None else env.get("VIGENTE_EMBED_MODEL", DEFAULT_EMBED_MODEL)
        )
        return cls(
            model=None if raw_model.strip().lower() in _DISABLED else raw_model.strip(),
            base_url=base_url or env.get("VIGENTE_EMBED_BASE_URL") or DEFAULT_BASE_URL,
            api_key=env.get("VIGENTE_EMBED_API_KEY") or None,
            allow_remote=env.get("VIGENTE_ALLOW_REMOTE", "").strip().lower() in _ENABLED,
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
