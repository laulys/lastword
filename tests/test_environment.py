"""Lo configurado con el nombre anterior, Vigente, sigue valiendo."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from lastword import environment
from lastword.config import Settings
from lastword.i18n import language


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    return tmp_path


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    return path


def test_old_variables_still_work(home: Path) -> None:
    old = {
        "VIGENTE_DB": "/datos/memoria.db",
        "VIGENTE_WORKSPACE": "equipo",
        "VIGENTE_EMBED_MODEL": "none",
        "VIGENTE_SEARCH_MODE": "text",
        "VIGENTE_ALLOW_REMOTE": "1",
        "VIGENTE_EMBED_BASE_URL": "https://embeddings.example/v1",
        "VIGENTE_EMBED_API_KEY": "una-clave",
    }
    settings = Settings.from_env(old)
    assert (str(settings.db_path), settings.workspace) == ("/datos/memoria.db", "equipo")
    assert settings.search_mode == "text"
    assert settings.embedding.model is None and settings.embedding.allow_remote
    assert settings.embedding.base_url == "https://embeddings.example/v1"
    assert settings.embedding.api_key == "una-clave"
    assert settings == Settings.from_env(
        {name.replace("VIGENTE_", "LASTWORD_"): value for name, value in old.items()}
    )


def test_the_new_name_wins(home: Path) -> None:
    both = {"LASTWORD_WORKSPACE": "nuevo", "VIGENTE_WORKSPACE": "viejo"}
    assert Settings.from_env(both).workspace == "nuevo"
    # También vacía: es la forma de desactivar los embeddings.
    both = {"LASTWORD_EMBED_MODEL": "", "VIGENTE_EMBED_MODEL": "embeddinggemma"}
    assert Settings.from_env(both).embedding.model is None


def test_the_language_variable_too(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LASTWORD_LANG", raising=False)
    monkeypatch.setenv("LANG", "C.UTF-8")
    monkeypatch.setenv("VIGENTE_LANG", "es")
    assert language() == "es"


def test_an_existing_memory_is_not_left_behind(home: Path) -> None:
    new, old = home / ".lastword" / "lastword.db", home / ".vigente" / "vigente.db"
    assert environment.default_db_path() == new  # nadie tenía nada
    _touch(old)
    assert environment.default_db_path() == old
    assert Settings.from_env({}).db_path == old
    _touch(new)
    assert environment.default_db_path() == new


def test_the_cli_finds_the_old_memory(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LASTWORD_EMBED_MODEL", "none")
    monkeypatch.delenv("LASTWORD_DB", raising=False)
    monkeypatch.delenv("VIGENTE_DB", raising=False)
    _touch(home / ".vigente" / "vigente.db")
    command = [sys.executable, "-m", "lastword", "add", "El proyecto usa SQLite"]
    assert subprocess.run(command, capture_output=True, timeout=60).returncode == 0
    assert (home / ".vigente" / "vigente.db").stat().st_size > 0
    assert not (home / ".lastword").exists()


def test_the_old_commands_are_still_installed() -> None:
    from importlib.metadata import entry_points

    scripts = {entry.name: entry.value for entry in entry_points(group="console_scripts")}
    assert scripts["vigente"] == scripts["lastword"]
    assert scripts["vigente-mcp"] == scripts["lastword-mcp"]
