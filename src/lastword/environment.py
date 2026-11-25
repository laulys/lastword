"""Variables de entorno y archivo por defecto, también con el nombre anterior.

Hasta la 1.4 el proyecto se llamaba Vigente. Lo que ya está configurado sigue valiendo: las
variables `VIGENTE_*`, la memoria de `~/.vigente` y los comandos `vigente` y `vigente-mcp`.
Si están los dos nombres, manda el nuevo.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

DEFAULT_DB_PATH = Path("~/.lastword/lastword.db")
OLD_DB_PATH = Path("~/.vigente/vigente.db")


def names(name: str) -> list[str]:
    """Cómo se puede llamar una variable, por orden: `LASTWORD_DB`, `VIGENTE_DB`."""
    return [f"LASTWORD_{name}", f"VIGENTE_{name}"]


def value(name: str, env: Mapping[str, str] = os.environ) -> str | None:
    """El valor de una variable, o None si no está con ninguno de sus nombres."""
    return next((env[candidate] for candidate in names(name) if candidate in env), None)


def default_db_path() -> Path:
    """El archivo de siempre. La memoria no se mueve: si solo existe la de antes, se usa esa."""
    new, old = DEFAULT_DB_PATH.expanduser(), OLD_DB_PATH.expanduser()
    return old if old.exists() and not new.exists() else new
