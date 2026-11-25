"""Demo con un agente real: cuatro sesiones independientes de Claude Code usando Lastword.

Cada sesión es un proceso nuevo, sin contexto de las anteriores: lo único que comparten es
Lastword. El agente solo tiene las herramientas de Lastword (`--tools ""` quita las propias de
Claude Code, incluida su memoria de archivos) y una configuración MCP propia
(`--strict-mcp-config`), así que no se toca la configuración del usuario.

Requisitos: la CLI `claude` con sesión iniciada y Ollama con el modelo de embeddings.

Uso:
    uv run python eval/agent_demo.py

Nota: Claude Code guarda el registro de cada sesión en ~/.claude/projects/, como siempre.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from lastword.config import EmbeddingSettings
from lastword.models import ListQuery, RecallQuery
from lastword.service import MemoryService

REPO = Path(__file__).resolve().parent.parent
RESULTS = REPO / "eval" / "results" / "agent_demo.md"
WORKSPACE = "demo"
PROMPTS = [
    "Para que lo recuerdes en futuras conversaciones: el proyecto usa PostgreSQL 15 como base "
    "de datos, y la reunión diaria del equipo es a las 9:30.",
    "Novedad: migramos a SQLite para el MVP.",
    "¿Qué base de datos usamos en el proyecto y a qué hora es el daily?",
    "Apunta esto: desde la semana que viene el daily pasa a las 10.",
]
LASTWORD_TOOLS = ["remember", "recall", "supersede", "history", "list_memories"]  # sin forget


@dataclass
class Session:
    prompt: str
    tool_calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    answer: str = ""
    model: str = "?"
    turns: int = 0
    error: bool = False


def run_session(workdir: Path, prompt: str) -> Session:
    command = [
        "claude", "-p", prompt,
        "--mcp-config", str(workdir / "mcp.json"), "--strict-mcp-config",
        "--allowedTools", ",".join(f"mcp__lastword__{name}" for name in LASTWORD_TOOLS),
        "--tools", "",
        "--output-format", "stream-json", "--verbose",
    ]  # fmt: skip
    completed = subprocess.run(
        command, cwd=workdir, capture_output=True, text=True, timeout=600, stdin=subprocess.DEVNULL
    )
    session = Session(prompt=prompt)
    for line in completed.stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("type") == "system" and event.get("subtype") == "init":
            session.model = event.get("model", "?")
        elif event.get("type") == "assistant":
            for block in event["message"].get("content", []):
                if block.get("type") == "tool_use":
                    name = block["name"].removeprefix("mcp__lastword__")
                    session.tool_calls.append((name, block["input"]))
        elif event.get("type") == "result":
            session.answer = (event.get("result") or "").strip()
            session.turns = event.get("num_turns", 0)
            session.error = bool(event.get("is_error"))
    if completed.returncode != 0 and not session.answer:
        session.error = True
        session.answer = completed.stderr.strip()[-500:]
    return session


def claude_version() -> str:
    result = subprocess.run(["claude", "--version"], capture_output=True, text=True)
    return result.stdout.strip() or "?"


def report(sessions: list[Session], db_path: Path) -> str:
    lines = [
        "# Demo con un agente real",
        "",
        "Generado por `eval/agent_demo.py`. Cuatro sesiones **independientes** de Claude Code "
        "(cada una es un proceso nuevo, sin memoria de las anteriores) que solo comparten "
        "Lastword. El agente no tiene más herramientas que las de Lastword.",
        "",
        f"- Fecha: {datetime.now(UTC):%Y-%m-%d}",
        f"- {claude_version()}, modelo `{sessions[0].model}`",
        f"- Embeddings: `{EmbeddingSettings.from_env().model or 'ninguno (solo texto)'}` en Ollama",
        "",
    ]
    for number, session in enumerate(sessions, start=1):
        lines += [f"## Sesión {number}", "", f"> {session.prompt}", "", "Herramientas usadas:", ""]
        if not session.tool_calls:
            lines.append("- (ninguna)")
        for name, arguments in session.tool_calls:
            lines.append(f"- `{name}` `{json.dumps(arguments, ensure_ascii=False)}`")
        status = " (error)" if session.error else ""
        lines += ["", f"Respuesta{status} ({session.turns} turnos):", ""]
        lines += [f"> {line}" if line else ">" for line in session.answer.splitlines()]
        lines.append("")

    # Misma configuración que hereda el servidor MCP que lanzan las sesiones.
    embedder = EmbeddingSettings.from_env().build()
    service = MemoryService.open(db_path, WORKSPACE, embedder=embedder)
    try:
        memories = service.list_memories(ListQuery(limit=100)).items
        lines += [
            "## Estado final de la memoria",
            "",
            "| Contenido | Vigente desde | Hasta | Reemplaza a |",
            "|---|---|---|---|",
        ]
        contents = {memory.id: memory.content for memory in memories}
        for memory in reversed(memories):
            until = "sin fin" if memory.valid_to is None else f"{memory.valid_to:%Y-%m-%d %H:%M}"
            replaces = contents.get(memory.supersedes_id or "", "")
            lines.append(
                f"| {memory.content} | {memory.valid_from:%Y-%m-%d %H:%M} | {until} | {replaces} |"
            )
        next_week = datetime.now(UTC) + timedelta(days=7)
        checks = [
            ("Hoy", RecallQuery(query="¿A qué hora es el daily?", k=1)),
            (
                "Dentro de 7 días",
                RecallQuery(query="¿A qué hora es el daily?", k=1, as_of=next_week),
            ),
        ]
        lines += ["", "Búsqueda «¿A qué hora es el daily?» (resultado principal):", ""]
        for label, query in checks:
            results = service.recall(query).results
            lines.append(f"- {label}: {results[0].memory.content if results else '(nada)'}")
        problems = service.doctor()
        lines += ["", f"`lastword doctor`: {problems if problems else 'sin problemas'}", ""]
    finally:
        service.close()
        if embedder is not None:
            embedder.close()
    return "\n".join(lines)


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="lastword-demo-") as tmp:
        workdir = Path(tmp)
        db_path = workdir / "demo.db"
        config = {
            "mcpServers": {
                "lastword": {
                    "command": "uv",
                    "args": ["--directory", str(REPO), "run", "lastword-mcp"],
                    "env": {"LASTWORD_DB": str(db_path), "LASTWORD_WORKSPACE": WORKSPACE},
                }
            }
        }
        (workdir / "mcp.json").write_text(json.dumps(config), encoding="utf-8")
        sessions: list[Session] = []
        for prompt in PROMPTS:
            print(f"Sesión {len(sessions) + 1}: {prompt}", flush=True)
            sessions.append(run_session(workdir, prompt))
        text = report(sessions, db_path)
    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    RESULTS.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
