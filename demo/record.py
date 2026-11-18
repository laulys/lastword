"""Graba la demo del README ejecutando la CLI de verdad: nada está simulado salvo el tecleo.

Genera demo/vigente.cast (formato asciinema) y demo/vigente.gif.
Requisitos: Ollama con embeddinggemma y `agg` (brew install agg).

Uso:
    uv run python demo/record.py
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
CAST = HERE / "vigente.cast"
GIF = HERE / "vigente.gif"
COLS, ROWS = 96, 30
TYPING = 0.03  # segundos por tecla
CLEAR = "\x1b[2J\x1b[H"
DIM, RESET = "\x1b[2m", "\x1b[0m"


class Cast:
    """Grabación asciinema v2: líneas [tiempo, "o", texto]."""

    def __init__(self) -> None:
        self.time = 0.0
        self.events: list[tuple[float, str, str]] = []

    def out(self, text: str, delay: float = 0.0) -> None:
        self.time += delay
        self.events.append((round(self.time, 3), "o", text.replace("\n", "\r\n")))

    def type(self, text: str) -> None:
        for char in text:
            self.out(char, TYPING)

    def wait(self, seconds: float) -> None:
        self.time += seconds

    def save(self, path: Path) -> None:
        header = {"version": 2, "width": COLS, "height": ROWS, "env": {"TERM": "xterm-256color"}}
        lines = [json.dumps(header)] + [
            json.dumps(event, ensure_ascii=False) for event in self.events
        ]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    cast = Cast()
    with tempfile.TemporaryDirectory(prefix="vigente-demo-") as tmp:
        # Configuración fija: la demo muestra los valores por defecto, no los de quien la graba.
        inherited = {k: v for k, v in os.environ.items() if not k.startswith("VIGENTE_")}
        env = inherited | {
            "VIGENTE_DB": str(Path(tmp) / "demo.db"),
            "VIGENTE_WORKSPACE": "demo",
            "COLUMNS": str(COLS),
            "FORCE_COLOR": "1",
            "TERM": "xterm-256color",
        }
        env.pop("NO_COLOR", None)

        def comment(text: str) -> None:
            cast.out(f"{DIM}# {text}{RESET}\n", 0.4)

        def command(*args: str) -> str:
            cast.out("$ ", 0.3)
            cast.type("vigente " + " ".join(shlex.quote(a) for a in args))
            cast.out("\n")
            result = subprocess.run(
                [sys.executable, "-m", "vigente", *args],
                env=env, capture_output=True, text=True, check=True,
            )  # fmt: skip
            output = result.stdout + result.stderr
            cast.out(output, 0.25)
            cast.wait(2.2)
            return output

        def saved_id(output: str) -> str:
            match = re.search(r"Guardada\S*\s+(?:\x1b\[[0-9;]*m)*([0-9A-Z]{26})", output)
            assert match, output
            return match.group(1)

        cast.out(CLEAR)
        comment("Day 1: store a fact")
        old = saved_id(
            command(
                "add",
                "El proyecto usa PostgreSQL 15 como base de datos",
                "--valid-from",
                "2026-03-01",
            )
        )
        comment("Months later, a change that shares no words with it")
        new = saved_id(command("add", "Migramos a SQLite para el MVP"))
        cast.wait(1.5)

        cast.out(CLEAR, 0.2)
        comment("Link them: the old fact closes, but stays in history")
        command("supersede", old, "--by", new)
        comment("Search returns only what is still true")
        command("search", "¿qué base de datos usamos?", "-k", "2")
        cast.wait(1.0)

        cast.out(CLEAR, 0.2)
        comment("...and what was true at any past date")
        command("search", "¿qué base de datos usamos?", "-k", "2", "--as-of", "2026-06-01")
        comment("The full story of the fact")
        command("history", new)
        cast.wait(2.0)

    cast.save(CAST)
    subprocess.run(
        ["agg", "--theme", "github-dark", "--font-size", "15", "--last-frame-duration", "5",
         str(CAST), str(GIF)],
        check=True,
    )  # fmt: skip
    print(f"{CAST} y {GIF}: {GIF.stat().st_size / 1e6:.1f} MB, {cast.time:.0f} s")


if __name__ == "__main__":
    main()
