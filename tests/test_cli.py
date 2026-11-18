from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner, Result

from vigente.cli import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def _without_embeddings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Los tests de la CLI no dependen de que Ollama esté corriendo."""
    monkeypatch.setenv("VIGENTE_EMBED_MODEL", "none")


def run(db_path: Path, *args: str, input: str | None = None, as_json: bool = True) -> Result:
    options = ["--db", str(db_path), "--workspace", "cli"]
    if as_json:
        options.append("--json")
    return runner.invoke(app, [*options, *args], input=input)


def ok(result: Result) -> Any:
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def test_full_lifecycle(db_path: Path) -> None:
    added = ok(run(db_path, "add", "El proyecto usa PostgreSQL", "--tag", "stack"))
    old_id = added["memory"]["id"]

    replaced = ok(run(db_path, "supersede", old_id, "El proyecto usa SQLite", "--reason", "MVP"))
    new_id = replaced["new"]["id"]
    assert replaced["old"]["valid_to"] == replaced["new"]["valid_from"]

    assert [m["id"] for m in ok(run(db_path, "history", new_id))] == [old_id, new_id]

    found = ok(run(db_path, "search", "proyecto usa"))
    assert [hit["memory"]["id"] for hit in found["results"]] == [new_id]

    assert ok(run(db_path, "forget", new_id, "--yes")) == {"deleted_ids": [old_id, new_id]}

    missing = run(db_path, "show", old_id)
    assert missing.exit_code == 1
    assert json.loads(missing.stderr)["code"] == "not_found"


def test_invalid_input_exits_with_code_2(db_path: Path) -> None:
    result = run(db_path, "add", "   ")
    assert result.exit_code == 2
    assert json.loads(result.stderr)["code"] == "invalid_input"


def test_superseding_twice_reports_the_current_version(db_path: Path) -> None:
    old_id = ok(run(db_path, "add", "Ana vive en Madrid"))["memory"]["id"]
    new_id = ok(run(db_path, "supersede", old_id, "Ana vive en Lisboa"))["new"]["id"]
    again = run(db_path, "supersede", old_id, "Ana vive en Roma")
    assert again.exit_code == 1
    assert json.loads(again.stderr) | {"message": ""} == {
        "code": "already_superseded",
        "message": "",
        "current_id": new_id,
    }


def test_forget_asks_for_confirmation(db_path: Path) -> None:
    memory_id = ok(run(db_path, "add", "Ana vive en Madrid"))["memory"]["id"]
    declined = run(db_path, "forget", memory_id, input="n\n")
    assert declined.exit_code != 0
    assert ok(run(db_path, "show", memory_id))["id"] == memory_id


def test_human_output(db_path: Path) -> None:
    added = run(db_path, "add", "Ana vive en [Madrid]", as_json=False)
    assert added.exit_code == 0, added.output
    assert "Guardada" in added.stdout
    listed = run(db_path, "list", as_json=False)
    assert "[Madrid]" in listed.stdout


def test_doctor_and_stats(db_path: Path) -> None:
    run(db_path, "add", "Ana vive en Madrid")
    assert ok(run(db_path, "doctor")) == {"ok": True, "problems": []}
    assert ok(run(db_path, "stats"))["total"] == 1


def test_workspace_is_isolated_from_the_cli(db_path: Path) -> None:
    memory_id = ok(run(db_path, "add", "Ana vive en Madrid"))["memory"]["id"]
    other = runner.invoke(app, ["--db", str(db_path), "-w", "otro", "--json", "show", memory_id])
    assert other.exit_code == 1


def test_refuses_remote_embedding_services_without_permission(db_path: Path) -> None:
    result = run(
        db_path, "--embed-model", "m", "--embed-url", "https://api.example.com/v1", "stats"
    )
    assert result.exit_code == 1, result.output
    assert "VIGENTE_ALLOW_REMOTE" in result.output


def test_reindex_without_a_model_is_a_config_error(db_path: Path) -> None:
    result = run(db_path, "reindex")
    assert result.exit_code == 1
    assert json.loads(result.stderr)["code"] == "config"
