from __future__ import annotations

import json
import socket
from collections.abc import Callable

import httpx
import pytest

from vigente.config import DEFAULT_EMBED_MODEL, EmbeddingSettings
from vigente.embeddings import PROFILES, ModelProfile, OpenAICompatibleEmbedder, profile_for
from vigente.errors import ConfigError, EmbeddingError

Handler = Callable[[httpx.Request], httpx.Response]


def embedder(handler: Handler, **options: object) -> OpenAICompatibleEmbedder:
    return OpenAICompatibleEmbedder(
        "qwen3-embedding:0.6b",
        transport=httpx.MockTransport(handler),
        sleep=lambda _: None,
        **options,  # type: ignore[arg-type]
    )


def answer(request: httpx.Request, dim: int = 3) -> httpx.Response:
    inputs = json.loads(request.content)["input"]
    data = [{"index": i, "embedding": [float(i + 1)] * dim} for i in range(len(inputs))]
    return httpx.Response(200, json={"data": data})


class Recorder:
    def __init__(self, *responses: httpx.Response | Exception) -> None:
        self.requests: list[httpx.Request] = []
        self._responses = list(responses)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if not self._responses:
            return answer(request)
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class TestRequests:
    def test_sends_model_and_inputs_formatted_for_their_purpose(self) -> None:
        recorder = Recorder()
        client = embedder(recorder)
        client.embed(["Ana vive en Madrid"], "document")
        client.embed(["¿Dónde vive Ana?"], "query")
        documents, queries = (json.loads(r.content) for r in recorder.requests)
        assert recorder.requests[0].url.path == "/v1/embeddings"
        assert documents == {"model": "qwen3-embedding:0.6b", "input": ["Ana vive en Madrid"]}
        assert queries["input"][0].startswith("Instruct: ")
        assert queries["input"][0].endswith("Query:¿Dónde vive Ana?")

    def test_keeps_input_order_even_if_the_server_reorders(self) -> None:
        def reversed_answer(request: httpx.Request) -> httpx.Response:
            inputs = json.loads(request.content)["input"]
            data = [{"index": i, "embedding": [float(i)]} for i in range(len(inputs))]
            return httpx.Response(200, json={"data": data[::-1]})

        assert embedder(reversed_answer).embed(["a", "b", "c"], "document") == [[0.0], [1.0], [2.0]]

    def test_splits_large_inputs_into_batches(self) -> None:
        recorder = Recorder()
        vectors = embedder(recorder, batch_size=2).embed(list("abcde"), "document")
        assert len(vectors) == 5
        assert [len(json.loads(r.content)["input"]) for r in recorder.requests] == [2, 2, 1]

    def test_sends_the_api_key_as_a_header(self) -> None:
        recorder = Recorder()
        embedder(recorder, api_key="clave-secreta").embed(["x"], "document")
        assert recorder.requests[0].headers["authorization"] == "Bearer clave-secreta"


class TestErrors:
    def test_retries_transient_errors(self) -> None:
        recorder = Recorder(httpx.Response(503), httpx.Response(429))
        assert embedder(recorder).embed(["x"], "document") == [[1.0, 1.0, 1.0]]
        assert len(recorder.requests) == 3

    def test_gives_up_after_the_retries_without_leaking_the_key(self) -> None:
        recorder = Recorder(*[httpx.Response(503) for _ in range(5)])
        with pytest.raises(EmbeddingError) as error:
            embedder(recorder, api_key="clave-secreta", max_retries=2).embed(["x"], "document")
        assert len(recorder.requests) == 3
        assert "clave-secreta" not in str(error.value)

    def test_refused_connections_fail_fast(self) -> None:
        recorder = Recorder(httpx.ConnectError("refused"))
        with pytest.raises(EmbeddingError, match="No se pudo conectar"):
            embedder(recorder).embed(["x"], "document")
        assert len(recorder.requests) == 1  # sin servicio, reintentar solo hace esperar

    def test_other_network_errors_are_retried(self) -> None:
        recorder = Recorder(httpx.ReadTimeout("lento"), httpx.RemoteProtocolError("cortado"))
        assert embedder(recorder).embed(["x"], "document") == [[1.0, 1.0, 1.0]]
        assert len(recorder.requests) == 3

    def test_client_errors_are_not_retried(self) -> None:
        recorder = Recorder(httpx.Response(400, text="modelo desconocido"))
        with pytest.raises(EmbeddingError, match="HTTP 400"):
            embedder(recorder).embed(["x"], "document")
        assert len(recorder.requests) == 1

    @pytest.mark.parametrize(
        "body",
        [
            {"x": 1},
            {"data": [{"embedding": "abc"}]},
            {"data": []},
            {"data": [{"embedding": []}]},
            {"data": [{"embedding": [float("nan")]}]},
            {"data": [[0.1, 0.2]]},
            {"data": {"a": 1}},
        ],
    )
    def test_rejects_malformed_responses(self, body: object) -> None:
        recorder = Recorder(httpx.Response(200, content=json.dumps(body).encode()))
        with pytest.raises(EmbeddingError):
            embedder(recorder).embed(["x"], "document")

    def test_rejects_vectors_of_different_sizes(self) -> None:
        body = {"data": [{"embedding": [1.0, 2.0]}, {"embedding": [1.0]}]}
        recorder = Recorder(httpx.Response(200, json=body))
        with pytest.raises(EmbeddingError, match="dimensiones"):
            embedder(recorder).embed(["a", "b"], "document")


class TestPrivacy:
    @pytest.mark.parametrize(
        "url",
        [
            "http://localhost:11434/v1",
            "http://127.0.0.1:1234/v1",
            "http://[::1]:8080/v1",
            "http://ollama.localhost/v1",
        ],
    )
    def test_local_services_are_allowed(self, url: str) -> None:
        OpenAICompatibleEmbedder("m", base_url=url).close()

    def test_a_system_proxy_never_sees_requests_to_a_local_service(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """httpx usa HTTP_PROXY también para localhost: el texto de la memoria saldría hacia
        el proxy aunque la dirección sea local."""
        proxy = socket.socket()
        proxy.bind(("127.0.0.1", 0))
        proxy.listen(1)
        proxy.settimeout(0.3)
        unused = socket.socket()
        unused.bind(("127.0.0.1", 0))
        port = unused.getsockname()[1]
        unused.close()
        for name in ("HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
            monkeypatch.setenv(name, f"http://127.0.0.1:{proxy.getsockname()[1]}")
        for name in ("NO_PROXY", "no_proxy"):
            monkeypatch.delenv(name, raising=False)
        local = OpenAICompatibleEmbedder("m", base_url=f"http://localhost:{port}/v1")
        try:
            with pytest.raises(EmbeddingError, match="No se pudo conectar"):
                local.embed(["una memoria privada"], "document")
            with pytest.raises(TimeoutError):
                proxy.accept()  # nadie llamó al proxy
        finally:
            local.close()
            proxy.close()

    def test_remote_services_need_explicit_permission(self) -> None:
        with pytest.raises(ConfigError, match="VIGENTE_ALLOW_REMOTE"):
            OpenAICompatibleEmbedder("m", base_url="https://api.example.com/v1")
        OpenAICompatibleEmbedder("m", base_url="https://api.example.com/v1", allow_remote=True)


class TestProfiles:
    def test_profiles_are_chosen_by_model_family(self) -> None:
        assert profile_for("qwen3-embedding:0.6b") is PROFILES["qwen3-embedding"]
        assert profile_for("library/embeddinggemma:latest") is PROFILES["embeddinggemma"]
        assert profile_for("modelo-desconocido") == ModelProfile()

    def test_formats_text_for_each_purpose(self) -> None:
        gemma = PROFILES["embeddinggemma"]
        assert gemma.format("document", "hola") == "title: none | text: hola"
        assert gemma.format("query", "hola") == "task: search result | query: hola"
        assert ModelProfile().format("query", "{text} literal") == "{text} literal"


class TestSettings:
    def test_defaults_to_the_local_model(self) -> None:
        settings = EmbeddingSettings.from_env({})
        assert settings.model == DEFAULT_EMBED_MODEL
        assert settings.base_url == "http://localhost:11434/v1"
        assert not settings.allow_remote
        assert settings.api_key is None

    @pytest.mark.parametrize("value", ["none", "OFF", "", "0"])
    def test_embeddings_can_be_disabled(self, value: str) -> None:
        settings = EmbeddingSettings.from_env({"VIGENTE_EMBED_MODEL": value})
        assert settings.model is None
        assert settings.build() is None

    def test_reads_secrets_and_permissions_from_the_environment(self) -> None:
        env = {
            "VIGENTE_EMBED_MODEL": "text-embedding-3-small",
            "VIGENTE_EMBED_BASE_URL": "https://api.example.com/v1",
            "VIGENTE_EMBED_API_KEY": "clave",
            "VIGENTE_ALLOW_REMOTE": "1",
        }
        settings = EmbeddingSettings.from_env(env)
        assert (settings.model, settings.api_key, settings.allow_remote) == (
            "text-embedding-3-small",
            "clave",
            True,
        )
        assert EmbeddingSettings.from_env(env, model="none").model is None


def test_search_mode_comes_from_the_environment() -> None:
    from vigente.config import Settings
    from vigente.models import SearchMode

    assert Settings.from_env({}).search_mode is SearchMode.VECTOR
    assert Settings.from_env({"VIGENTE_SEARCH_MODE": " Hybrid "}).search_mode is SearchMode.HYBRID
    with pytest.raises(ValueError, match="usa vector, hybrid, text"):
        Settings.from_env({"VIGENTE_SEARCH_MODE": "magia"})
