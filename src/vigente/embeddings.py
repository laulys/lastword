"""Proveedores de embeddings.

Un `Embedder` convierte textos en vectores. Cada modelo espera un formato de entrada distinto
según el uso (documento guardado, pregunta, o comparación entre afirmaciones). Si no se
respeta, la calidad cae sin dar ningún error. `ModelProfile` encapsula ese formato para que
el resto del código no tenga que conocerlo.
"""

from __future__ import annotations

import ipaddress
import math
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol
from urllib.parse import urlparse

import httpx

from vigente.errors import ConfigError, EmbeddingError
from vigente.i18n import tr

Purpose = Literal["document", "query", "similarity"]

DEFAULT_BASE_URL = "http://localhost:11434/v1"  # Ollama, API compatible con OpenAI


@dataclass(frozen=True)
class ModelProfile:
    """Cómo hablarle a un modelo concreto y con qué umbrales usarlo."""

    document: str = "{text}"
    query: str = "{text}"
    similarity: str = "{text}"
    # Formato con el que se compara una memoria nueva contra las guardadas para proponer
    # posibles reemplazos, y similitud coseno mínima para proponerlas.
    candidate_purpose: Purpose = "document"
    candidate_threshold: float = 0.75
    # Similitud mínima para que un vecino cuente como resultado de una búsqueda.
    min_recall_similarity: float = 0.0

    def format(self, purpose: Purpose, text: str) -> str:
        template = {
            "document": self.document,
            "query": self.query,
            "similarity": self.similarity,
        }[purpose]
        return template.replace("{text}", text)


PROFILES: dict[str, ModelProfile] = {
    # Qwen recomienda escribir las instrucciones en inglés, también con contenido multilingüe.
    # Los documentos van sin instrucción.
    "qwen3-embedding": ModelProfile(
        query="Instruct: Given a question, retrieve stored memories that answer it\nQuery:{text}",
        candidate_threshold=0.45,  # mejor F1 en eval/results/embedding_choice.md
        similarity=(
            "Instruct: Given a new statement, retrieve stored statements about the same "
            "subject that it may update or contradict\nQuery:{text}"
        ),
    ),
    "embeddinggemma": ModelProfile(
        document="title: none | text: {text}",
        query="task: search result | query: {text}",
        similarity="task: sentence similarity | query: {text}",
        candidate_threshold=0.51,  # mejor F1 en eval/results/embedding_choice.md (ADR-0004)
    ),
}


def profile_for(model: str) -> ModelProfile:
    """Perfil por familia: «qwen3-embedding:0.6b» → «qwen3-embedding». Desconocido → neutro."""
    family = model.split(":", 1)[0].rsplit("/", 1)[-1]
    return PROFILES.get(family, ModelProfile())


class Embedder(Protocol):
    @property
    def model(self) -> str: ...

    @property
    def profile(self) -> ModelProfile: ...

    def embed(self, texts: Sequence[str], purpose: Purpose) -> list[list[float]]: ...


def is_local_url(url: str) -> bool:
    host = urlparse(url).hostname or ""
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class OpenAICompatibleEmbedder:
    """Cliente de `POST /embeddings`. Sirve para Ollama, LM Studio, OpenRouter u OpenAI.

    Privacidad: si la URL no es local, se niega a enviar nada salvo con `allow_remote=True`.
    """

    def __init__(
        self,
        model: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        api_key: str | None = None,
        allow_remote: bool = False,
        timeout: float = 30.0,
        max_retries: int = 2,
        batch_size: int = 64,
        profile: ModelProfile | None = None,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not model.strip():
            raise ConfigError(tr("Falta el nombre del modelo de embeddings."))
        if not is_local_url(base_url) and not allow_remote:
            raise ConfigError(
                tr(
                    "{url} no es una dirección local. Para enviar el contenido de las memorias a "
                    "un servicio remoto, activa VIGENTE_ALLOW_REMOTE=1.",
                    url=base_url,
                )
            )
        self._model = model
        self._profile = profile or profile_for(model)
        self._base_url = base_url
        self._max_retries = max_retries
        self._batch_size = batch_size
        self._sleep = sleep
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        # Con un servicio local se ignoran HTTP_PROXY y ALL_PROXY: si no, httpx enviaría el
        # texto de las memorias al proxy aunque la dirección sea localhost.
        self._client = httpx.Client(
            base_url=base_url,
            timeout=timeout,
            headers=headers,
            transport=transport,
            trust_env=not is_local_url(base_url),
        )

    @property
    def model(self) -> str:
        return self._model

    @property
    def profile(self) -> ModelProfile:
        return self._profile

    def close(self) -> None:
        self._client.close()

    def embed(self, texts: Sequence[str], purpose: Purpose) -> list[list[float]]:
        inputs = [self._profile.format(purpose, text) for text in texts]
        vectors: list[list[float]] = []
        for start in range(0, len(inputs), self._batch_size):
            vectors.extend(self._request(inputs[start : start + self._batch_size]))
        if len({len(vector) for vector in vectors}) > 1:
            raise EmbeddingError(tr("El servicio devolvió vectores de dimensiones distintas."))
        return vectors

    def _request(self, batch: list[str]) -> list[list[float]]:
        problem = tr("sin respuesta")
        for attempt in range(self._max_retries + 1):
            if attempt:
                self._sleep(0.5 * 2 ** (attempt - 1))
            try:
                response = self._client.post(
                    "embeddings", json={"model": self._model, "input": batch}
                )
            except httpx.TimeoutException:
                problem = tr("tiempo de espera agotado")
                continue
            except httpx.ConnectError as exc:
                # Conexión rechazada: casi siempre el servicio no está corriendo. Reintentar
                # solo haría esperar al usuario en cada comando.
                raise EmbeddingError(
                    tr(
                        "No se pudo conectar con {url} ({error}). ¿Está corriendo el servicio de "
                        "embeddings?",
                        url=self._base_url,
                        error=type(exc).__name__,
                    )
                ) from exc
            except httpx.TransportError as exc:
                problem = tr("no se pudo conectar ({error})", error=type(exc).__name__)
                continue
            if response.status_code == 429 or response.status_code >= 500:
                problem = f"HTTP {response.status_code}"
                continue
            if response.status_code >= 400:
                raise EmbeddingError(
                    tr(
                        "El servicio de embeddings rechazó la petición (HTTP {status}): {body}",
                        status=response.status_code,
                        body=response.text[:200],
                    )
                )
            return _parse(response, expected=len(batch))
        raise EmbeddingError(
            tr(
                "No se pudieron calcular embeddings con {model} en {url}: {problem}.",
                model=self._model,
                url=self._base_url,
                problem=problem,
            )
        )


def _parse(response: httpx.Response, *, expected: int) -> list[list[float]]:
    try:
        items: list[dict[str, Any]] = response.json()["data"]
        ordered = sorted(enumerate(items), key=lambda pair: int(pair[1].get("index", pair[0])))
        vectors = [[float(value) for value in item["embedding"]] for _, item in ordered]
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise EmbeddingError(tr("Respuesta de embeddings con formato inesperado.")) from exc
    if len(vectors) != expected:
        raise EmbeddingError(
            tr(
                "Se esperaban {expected} vectores y llegaron {got}.",
                expected=expected,
                got=len(vectors),
            )
        )
    if any(not vector for vector in vectors):
        raise EmbeddingError(tr("El servicio devolvió un vector vacío."))
    if not all(math.isfinite(value) for vector in vectors for value in vector):
        raise EmbeddingError(tr("El servicio devolvió valores no numéricos (NaN o infinito)."))
    if any(not any(vector) for vector in vectors):
        # Un vector de ceros no tiene dirección: su distancia coseno a cualquier otro no existe.
        raise EmbeddingError(tr("El servicio devolvió un vector nulo (todo ceros)."))
    return vectors
