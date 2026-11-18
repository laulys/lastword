from __future__ import annotations

import ast
import json
import re
import string
from pathlib import Path

import pytest
from typer.testing import CliRunner

from vigente import cli, safety
from vigente.i18n import EN, language, tr

SOURCE = Path(__file__).resolve().parent.parent / "src" / "vigente"
MARKUP = re.compile(r"\[/?[a-z ]*\]")


def _tr_calls() -> list[tuple[str, ast.expr]]:
    calls = []
    for path in sorted(SOURCE.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "tr"
                and node.args
            ):
                calls.append((f"{path.name}:{node.lineno}", node.args[0]))
    return calls


def _literals() -> set[str]:
    return {
        arg.value
        for _, arg in _tr_calls()
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str)
    }


def _chosen_at_runtime() -> set[str]:
    """Textos que llegan a `tr` desde una tabla, no como literal."""
    return {
        *cli._KIND.values(),
        *cli._SOURCES.values(),
        *cli._MODES.values(),
        *safety.labels(),
    }


def _fields(text: str) -> list[str]:
    return sorted(name for _, name, _, _ in string.Formatter().parse(text) if name is not None)


def test_every_text_has_an_english_version() -> None:
    missing = (_literals() | _chosen_at_runtime()) - EN.keys()
    assert not missing, f"Falta la traducción de: {sorted(missing)}"


def test_the_catalog_has_no_leftover_entries() -> None:
    unused = EN.keys() - _literals() - _chosen_at_runtime()
    assert not unused, f"Traducciones que ya no se usan: {sorted(unused)}"


def test_no_text_is_built_before_translating_it() -> None:
    """Un f-string dentro de `tr` nunca coincidiría con el catálogo: los huecos van aparte."""
    built = [where for where, arg in _tr_calls() if isinstance(arg, ast.JoinedStr)]
    assert not built


@pytest.mark.parametrize("spanish", sorted(EN))
def test_translations_keep_placeholders_and_markup(spanish: str) -> None:
    english = EN[spanish]
    assert _fields(english) == _fields(spanish)
    assert MARKUP.findall(english) == MARKUP.findall(spanish)


@pytest.mark.parametrize(
    ("environment", "expected"),
    [
        ({"VIGENTE_LANG": "en", "LANG": "es_ES.UTF-8"}, "en"),
        ({"VIGENTE_LANG": "ES"}, "es"),
        ({"LANG": "es_ES.UTF-8"}, "es"),
        ({"LANG": "en_US.UTF-8"}, "en"),
        ({"LANG": "C.UTF-8"}, "en"),
        ({"LC_ALL": "es_MX.UTF-8", "LANG": "en_US.UTF-8"}, "es"),
        ({"VIGENTE_LANG": "fr", "LANG": "es_ES.UTF-8"}, "es"),
        ({}, "en"),
    ],
)
def test_language_comes_from_vigente_lang_or_the_system(
    monkeypatch: pytest.MonkeyPatch, environment: dict[str, str], expected: str
) -> None:
    for variable in ("VIGENTE_LANG", "LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.delenv(variable, raising=False)
    for variable, value in environment.items():
        monkeypatch.setenv(variable, value)
    assert language() == expected


def test_spanish_is_the_source_and_english_fills_the_same_placeholders(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert tr("{n} resultados", n=3) == "3 resultados"
    monkeypatch.setenv("VIGENTE_LANG", "en")
    assert tr("{n} resultados", n=3) == "3 results"
    assert tr("un texto sin traducir") == "un texto sin traducir"


def test_the_cli_speaks_english(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("VIGENTE_LANG", "en")
    monkeypatch.setenv("VIGENTE_EMBED_MODEL", "none")
    runner = CliRunner()
    options = ["--db", str(tmp_path / "vigente.db"), "--workspace", "en"]

    assert "Save a memory" in runner.invoke(cli.app, ["--help"]).stdout

    added = runner.invoke(cli.app, [*options, "add", "The project uses SQLite"])
    assert added.exit_code == 0, added.output
    assert "Saved" in added.stdout
    assert "fact" in added.stdout

    missing = runner.invoke(cli.app, [*options, "--json", "show", "01J0000000000000000000000Z"])
    assert missing.exit_code == 1
    assert "does not exist in this workspace" in json.loads(missing.stderr)["message"]
