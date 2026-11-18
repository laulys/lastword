"""El servidor MCP por HTTP, para compartir la memoria entre ordenadores (ADR-0009)."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator, MutableMapping
from pathlib import Path
from typing import Any

import httpx
import httpx2  # el cliente HTTP que usa el SDK de MCP
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from vigente.mcp_server import RequireToken

TOKEN = "t" * 40


async def _ok_app(scope: MutableMapping[str, Any], receive: Any, send: Any) -> None:
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("headers", "status"),
    [
        ({}, 401),
        ({"Authorization": "Bearer otro-token-de-cuarenta-caracteres-xxxxx"}, 401),
        ({"Authorization": TOKEN}, 401),  # sin «Bearer»
        ({"Authorization": f"Bearer {TOKEN}"}, 200),
    ],
)
async def test_only_requests_with_the_token_get_through(
    headers: dict[str, str], status: int
) -> None:
    transport = httpx.ASGITransport(app=RequireToken(_ok_app, TOKEN))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/mcp", headers=headers)
    assert response.status_code == status


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _env(tmp_path: Path, **overrides: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("VIGENTE_")}
    env |= {"VIGENTE_DB": str(tmp_path / "http.db"), "VIGENTE_EMBED_MODEL": "none"}
    return env | {"VIGENTE_WORKSPACE": "equipo", "VIGENTE_TOKEN": TOKEN} | overrides


@pytest.fixture
def http_server(tmp_path: Path) -> Iterator[str]:
    port = _free_port()
    process = subprocess.Popen(
        [sys.executable, "-m", "vigente.mcp_server", "--http", "--port", str(port)],
        env=_env(tmp_path),
        stderr=subprocess.PIPE,
    )
    url = f"http://127.0.0.1:{port}/mcp"
    try:
        for _ in range(100):
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                    break
            except OSError:
                time.sleep(0.05)
        else:
            raise RuntimeError("el servidor HTTP no arrancó")
        yield url
    finally:
        process.terminate()
        process.wait(timeout=10)


@pytest.mark.anyio
async def test_two_clients_share_one_memory_over_http(http_server: str) -> None:
    def connect() -> Any:
        auth = {"Authorization": f"Bearer {TOKEN}"}
        return streamable_http_client(http_server, http_client=httpx2.AsyncClient(headers=auth))

    async with Client(connect()) as first:
        saved = await first.call_tool("remember", {"content": "La entrega es el 25 de noviembre"})
        assert not saved.is_error
    async with Client(connect()) as second:  # otro cliente, otra sesión
        found = await second.call_tool("recall", {"query": "entrega"})
    assert found.structured_content is not None
    contents = [hit["memory"]["content"] for hit in found.structured_content["results"]]
    assert contents == ["La entrega es el 25 de noviembre"]


def test_requests_without_the_token_are_refused(http_server: str) -> None:
    response = httpx.post(http_server, json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert response.status_code == 401


@pytest.mark.parametrize(
    ("lang", "expected"),
    [("es", "hace falta VIGENTE_TOKEN"), ("en", "needs VIGENTE_TOKEN")],
)
def test_http_refuses_to_start_without_a_long_token(
    tmp_path: Path, lang: str, expected: str
) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "vigente.mcp_server", "--http", "--port", str(_free_port())],
        env=_env(tmp_path, VIGENTE_TOKEN="corto", VIGENTE_LANG=lang),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode != 0
    assert expected in result.stderr


def test_help_follows_the_language(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "vigente.mcp_server", "--help"],
        env=_env(tmp_path, VIGENTE_LANG="en"),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0
    assert "Serve over HTTP" in result.stdout
