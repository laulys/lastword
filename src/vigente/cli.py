"""CLI de Vigente: administración, depuración y demo."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, NoReturn, cast

import typer
from pydantic import BaseModel, ValidationError
from rich import box
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from vigente import safety
from vigente.config import (
    DEFAULT_DB_PATH,
    DEFAULT_EMBED_MODEL,
    DEFAULT_WORKSPACE,
    EmbeddingSettings,
    parse_search_mode,
)
from vigente.embeddings import DEFAULT_BASE_URL
from vigente.errors import SecretDetectedError, VigenteError
from vigente.models import (
    Hit,
    Kind,
    ListQuery,
    Memory,
    NewMemory,
    RecallQuery,
    RecallResult,
    RememberResult,
    SearchMode,
    Status,
    SupersedeRequest,
    SupersedeResult,
    describe_validation_error,
)
from vigente.service import MemoryService

app = typer.Typer(
    help="Vigente: memoria para agentes de IA que sabe qué sigue siendo verdad.",
    no_args_is_help=True,
    add_completion=False,
)
out = Console()
err = Console(stderr=True)

_KIND = {Kind.FACT: "hecho", Kind.EPISODE: "episodio"}
_SOURCES = {("fts",): "texto", ("vector",): "vector", ("fts", "vector"): "ambos"}


@dataclass(frozen=True)
class Options:
    db: Path
    workspace: str
    as_json: bool
    embedding: EmbeddingSettings
    search_mode: SearchMode


@app.callback()
def main(
    ctx: typer.Context,
    db: Annotated[
        Path, typer.Option("--db", envvar="VIGENTE_DB", help="Archivo SQLite de la memoria.")
    ] = DEFAULT_DB_PATH,
    workspace: Annotated[
        str,
        typer.Option(
            "--workspace", "-w", envvar="VIGENTE_WORKSPACE", help="Espacio de memoria aislado."
        ),
    ] = DEFAULT_WORKSPACE,
    as_json: Annotated[bool, typer.Option("--json", help="Salida en JSON.")] = False,
    embed_model: Annotated[
        str,
        typer.Option(
            "--embed-model",
            envvar="VIGENTE_EMBED_MODEL",
            help="Modelo de embeddings (Ollama u otro servicio compatible con OpenAI). "
            "'none' desactiva la búsqueda semántica.",
        ),
    ] = DEFAULT_EMBED_MODEL,
    embed_url: Annotated[
        str,
        typer.Option(
            "--embed-url",
            envvar="VIGENTE_EMBED_BASE_URL",
            help="URL del servicio de embeddings. Si no es local, exige VIGENTE_ALLOW_REMOTE=1.",
        ),
    ] = DEFAULT_BASE_URL,
    search_mode: Annotated[
        str,
        typer.Option(
            "--search-mode",
            envvar="VIGENTE_SEARCH_MODE",
            help="vector (por defecto), hybrid (vector + texto) o text.",
        ),
    ] = SearchMode.VECTOR.value,
) -> None:
    # La clave de API y el permiso de envío remoto solo se leen del entorno, nunca de
    # argumentos: no deben quedar en el historial de la shell.
    ctx.obj = Options(
        db=db.expanduser(),
        workspace=workspace,
        as_json=as_json,
        embedding=EmbeddingSettings.from_env(model=embed_model, base_url=embed_url),
        search_mode=_parse_search_mode(search_mode),
    )


def _parse_search_mode(raw: str) -> SearchMode:
    try:
        return parse_search_mode(raw)  # mismo criterio que el servidor MCP
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="--search-mode") from None


# --- infraestructura


def _options(ctx: typer.Context) -> Options:
    return cast(Options, ctx.obj)


def _fail(
    options: Options,
    code: str,
    message: str,
    *,
    exit_code: int = 1,
    current_id: str | None = None,
) -> NoReturn:
    if options.as_json:
        payload = {"code": code, "message": message}
        if current_id is not None:
            payload["current_id"] = current_id
        typer.echo(json.dumps(payload, ensure_ascii=False), err=True)
    else:
        err.print(f"[bold red]Error[/] [dim]({code})[/] {escape(message)}")
    raise typer.Exit(exit_code)


@contextmanager
def _service(ctx: typer.Context) -> Iterator[MemoryService]:
    options = _options(ctx)
    service: MemoryService | None = None
    embedder = None
    try:
        embedder = options.embedding.build()
        service = MemoryService.open(
            options.db, options.workspace, embedder=embedder, search_mode=options.search_mode
        )
        yield service
    except VigenteError as exc:
        message = exc.message
        if isinstance(exc, SecretDetectedError):
            message += " Si de verdad quieres guardarlo, repite el comando con --allow-secret."
        _fail(options, exc.code, message, current_id=exc.current_id)
    except ValidationError as exc:
        _fail(options, "invalid_input", describe_validation_error(exc), exit_code=2)
    finally:
        if service is not None:
            service.close()
        if embedder is not None:
            embedder.close()


def _warn(ctx: typer.Context, warnings: list[str]) -> None:
    """En modo humano, los avisos van a stderr; en JSON ya viajan dentro de la respuesta."""
    if not _options(ctx).as_json:
        for warning in warnings:
            err.print(f"[yellow]Aviso:[/] {escape(warning)}")


def _emit(
    ctx: typer.Context, data: BaseModel | dict[str, Any] | list[Any], render: Callable[[], None]
) -> None:
    if _options(ctx).as_json:
        payload = data.model_dump(mode="json") if isinstance(data, BaseModel) else data
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        render()


# --- presentación


def _fmt(value: datetime) -> str:
    """Fecha corta; la hora solo si no es medianoche (UTC)."""
    if (value.hour, value.minute, value.second) == (0, 0, 0):
        return value.strftime("%Y-%m-%d")
    return value.strftime("%Y-%m-%d %H:%M")


def _validity(memory: Memory) -> str:
    if memory.valid_to is None:
        return f"desde {_fmt(memory.valid_from)}"
    return f"{_fmt(memory.valid_from)} → {_fmt(memory.valid_to)}"


def _state(memory: Memory) -> str:
    """Estado según las fechas, no según `status`: un hecho con sucesora sigue vigente
    hasta que la sucesora empieza (por ejemplo, un cambio anunciado para la semana próxima)."""
    if memory.kind is Kind.EPISODE:
        return _KIND[memory.kind]
    now = datetime.now(UTC)
    if memory.valid_from > now:
        return "futura"
    if memory.valid_to is not None and memory.valid_to <= now:
        return "reemplazada"
    return "vigente"


def _content(memory: Memory) -> str:
    """El contenido, con un ⚠ si está marcado (ADR-0007): `vigente review` explica por qué."""
    text = escape(memory.content)
    return f"[yellow]⚠[/] {text}" if memory.flags else text


def _table(title: str | None = None) -> Table:
    # Sin bordes verticales: cabe en 80 columnas con ids completos.
    return Table(title=title, box=box.SIMPLE_HEAD, title_justify="left")


def _memory_table(memories: list[Memory], title: str | None = None) -> Table:
    table = _table(title)
    table.add_column("id", style="dim", no_wrap=True)
    table.add_column("contenido", overflow="fold", min_width=20)
    table.add_column("vigencia (UTC)", max_width=12)
    table.add_column("estado", no_wrap=True, min_width=11)
    for memory in memories:
        table.add_row(memory.id, _content(memory), _validity(memory), _state(memory))
    return table


def _hits_table(hits: list[Hit], title: str | None = None) -> Table:
    """Muestra la posición, no el score: los scores no son comparables entre consultas.

    El score y la similitud siguen disponibles con --json.
    """
    table = _table(title)
    table.add_column("#", justify="right")
    table.add_column("id", style="dim", no_wrap=True)
    table.add_column("contenido", overflow="fold", min_width=20)
    table.add_column("vigencia (UTC)", max_width=12)
    table.add_column("vía", no_wrap=True)
    for position, hit in enumerate(hits, start=1):
        content = _content(hit.memory)
        if hit.memory.status is Status.SUPERSEDED:
            content = f"[dim]{content}[/]"
        source = _SOURCES.get(tuple(hit.matched_by), "+".join(hit.matched_by))
        table.add_row(str(position), hit.memory.id, content, _validity(hit.memory), source)
    return table


def _render_recall(result: RecallResult) -> None:
    mode = {"vector": "por significado", "hybrid": "significado + texto"}.get(
        result.mode, "solo texto"
    )
    out.print(_hits_table(result.results, title=f"búsqueda: {mode}"))


def _render_remember(result: RememberResult) -> None:
    memory = result.memory
    out.print(f"[green]Guardada[/] {memory.id} ({_KIND[memory.kind]}, {_validity(memory)})")
    if result.possible_supersedes:
        out.print(
            "\nPosibles reemplazos. Si lo que acabas de guardar deja obsoleto alguno, usa "
            f"[bold]vigente supersede <id> --by {memory.id}[/]:"
        )
        out.print(_hits_table(result.possible_supersedes))


def _render_supersede(result: SupersedeResult) -> None:
    olds = [result.old, *result.also_old]
    out.print(f"[green]Reemplazada[/] {', '.join(old.id for old in olds)} → {result.new.id}")
    out.print(_memory_table([*olds, result.new]))


# --- comandos


@app.command()
def init(ctx: typer.Context) -> None:
    """Crea la base de datos y el workspace si no existen."""
    options = _options(ctx)
    with _service(ctx) as service:
        data = {"db": str(options.db), "workspace": service.workspace_id}
        _emit(
            ctx,
            data,
            lambda: out.print(
                f"Memoria lista en [bold]{escape(str(options.db))}[/], "
                f"workspace [bold]{service.workspace_id}[/]"
            ),
        )


@app.command()
def add(
    ctx: typer.Context,
    content: Annotated[str, typer.Argument(help="Texto de la memoria.")],
    kind: Annotated[
        Kind, typer.Option(help="fact: puede cambiar. episode: algo que pasó.")
    ] = Kind.FACT,
    tag: Annotated[
        list[str] | None, typer.Option("--tag", "-t", help="Etiqueta (se puede repetir).")
    ] = None,
    valid_from: Annotated[
        str | None, typer.Option(help="Desde cuándo es verdad (ISO 8601). Por defecto, ahora.")
    ] = None,
    allow_secret: Annotated[
        bool,
        typer.Option(
            "--allow-secret",
            help="Guardar aunque parezca una clave o contraseña. El servidor MCP no lo permite.",
        ),
    ] = False,
) -> None:
    """Guarda una memoria y sugiere hechos vigentes que podría reemplazar."""
    with _service(ctx) as service:
        request = NewMemory.model_validate(
            {
                "content": content,
                "kind": kind,
                "tags": tag or [],
                "valid_from": valid_from,
                "source": "cli",
            }
        )
        result = service.remember(request, allow_secrets=allow_secret)
        _emit(ctx, result, lambda: _render_remember(result))
        _warn(ctx, result.warnings)


@app.command()
def supersede(
    ctx: typer.Context,
    old_id: Annotated[str, typer.Argument(help="Id de la versión vigente.")],
    new_content: Annotated[
        str | None, typer.Argument(help="Texto de la versión nueva (o usa --by).")
    ] = None,
    by: Annotated[
        str | None,
        typer.Option("--by", help="Id de una memoria ya guardada que pasa a ser la versión nueva."),
    ] = None,
    also: Annotated[
        list[str] | None,
        typer.Option(
            "--also", help="Otro hecho que la versión nueva también reemplaza (se puede repetir)."
        ),
    ] = None,
    valid_from: Annotated[
        str | None, typer.Option(help="Desde cuándo es verdad lo nuevo (ISO 8601).")
    ] = None,
    reason: Annotated[str | None, typer.Option(help="Por qué cambió.")] = None,
    allow_secret: Annotated[
        bool,
        typer.Option(
            "--allow-secret",
            help="Guardar aunque parezca una clave o contraseña. El servidor MCP no lo permite.",
        ),
    ] = False,
) -> None:
    """Reemplaza uno o varios hechos por su versión nueva, conservando el historial.

    Con texto, crea la versión nueva. Con --by, enlaza una memoria que ya guardaste, por
    ejemplo la que `add` acaba de proponer como reemplazo. Con --also, la versión nueva
    reemplaza además otros hechos.
    """
    with _service(ctx) as service:
        fields: dict[str, object] = {"old_id": old_id, "reason": reason, "also_old_ids": also or []}
        if by is not None:
            fields |= {"new_id": by, "new_content": new_content, "valid_from": valid_from}
        else:
            fields |= {"new_content": new_content, "valid_from": valid_from, "source": "cli"}
        request = SupersedeRequest.model_validate(fields)
        result = service.supersede(request, allow_secrets=allow_secret)
        _emit(ctx, result, lambda: _render_supersede(result))
        _warn(ctx, result.warnings)


@app.command()
def search(
    ctx: typer.Context,
    query: Annotated[str, typer.Argument(help="Qué buscar, en lenguaje natural.")],
    top: Annotated[int, typer.Option("--top", "-k", help="Número de resultados.")] = 5,
    as_of: Annotated[
        str | None, typer.Option(help="Qué era verdad en esta fecha (ISO 8601).")
    ] = None,
    include_history: Annotated[
        bool, typer.Option("--history", help="Incluir versiones reemplazadas.")
    ] = False,
    kind: Annotated[Kind | None, typer.Option(help="Solo fact o solo episode.")] = None,
) -> None:
    """Busca memorias. Por defecto, solo lo que es verdad ahora."""
    with _service(ctx) as service:
        request = RecallQuery.model_validate(
            {
                "query": query,
                "k": top,
                "as_of": as_of,
                "include_history": include_history,
                "kind": kind,
            }
        )
        result = service.recall(request)
        _emit(ctx, result, lambda: _render_recall(result))
        _warn(ctx, result.warnings)


@app.command()
def reindex(ctx: typer.Context) -> None:
    """Calcula los vectores que falten con el modelo de embeddings configurado."""
    with _service(ctx) as service:
        result = service.reindex()
        _emit(
            ctx,
            result,
            lambda: out.print(
                f"[green]Vectores calculados:[/] {result.embedded} "
                f"(modelo {escape(result.model)}, {result.dim or '?'} dimensiones)"
            ),
        )


@app.command()
def show(
    ctx: typer.Context,
    memory_id: Annotated[str, typer.Argument(help="Id de la memoria.")],
) -> None:
    """Muestra una memoria."""
    with _service(ctx) as service:
        memory = service.get(memory_id)
        _emit(ctx, memory, lambda: out.print(_memory_table([memory])))


@app.command()
def history(
    ctx: typer.Context,
    memory_id: Annotated[str, typer.Argument(help="Id de cualquier versión del hecho.")],
) -> None:
    """Muestra todas las versiones de un hecho, de la más antigua a la vigente."""
    with _service(ctx) as service:
        chain = service.history(memory_id)
        _emit(
            ctx,
            [memory.model_dump(mode="json") for memory in chain],
            lambda: out.print(_memory_table(chain)),
        )


@app.command()
def forget(
    ctx: typer.Context,
    memory_id: Annotated[str, typer.Argument(help="Id de cualquier versión del hecho.")],
    yes: Annotated[bool, typer.Option("--yes", "-y", help="No pedir confirmación.")] = False,
) -> None:
    """Borra para siempre un hecho y todas sus versiones."""
    with _service(ctx) as service:
        if not yes:
            typer.confirm(f"¿Borrar para siempre {memory_id} y todas sus versiones?", abort=True)
        deleted = service.forget(memory_id)
        _emit(
            ctx,
            {"deleted_ids": deleted},
            lambda: out.print(f"[green]Borradas[/] {len(deleted)}: {', '.join(deleted)}"),
        )


@app.command("list")
def list_memories(
    ctx: typer.Context,
    kind: Annotated[Kind | None, typer.Option(help="Solo fact o solo episode.")] = None,
    status: Annotated[Status | None, typer.Option(help="active o superseded.")] = None,
    tag: Annotated[str | None, typer.Option(help="Solo con esta etiqueta.")] = None,
    limit: Annotated[int, typer.Option(help="Tamaño de página.")] = 20,
    cursor: Annotated[str | None, typer.Option(help="Cursor de la página siguiente.")] = None,
) -> None:
    """Lista memorias, las más recientes primero."""
    with _service(ctx) as service:
        query = ListQuery.model_validate(
            {"kind": kind, "status": status, "tag": tag, "limit": limit, "cursor": cursor}
        )
        page = service.list_memories(query)

        def render() -> None:
            out.print(_memory_table(page.items))
            if page.next_cursor is not None:
                out.print(f"Siguiente página: --cursor {page.next_cursor}")

        _emit(ctx, page, render)


@app.command()
def review(ctx: typer.Context) -> None:
    """Lista las memorias marcadas: posibles órdenes al agente, caracteres ocultos o secretos."""
    with _service(ctx) as service:
        flagged = service.review()

        def render() -> None:
            if not flagged:
                out.print(
                    "[green]Nada marcado.[/] Ninguna memoria parece contener órdenes al agente, "
                    "caracteres ocultos ni secretos."
                )
                return
            table = _table(f"memorias marcadas: {len(flagged)}")
            table.add_column("id", style="dim", no_wrap=True)
            table.add_column("contenido", overflow="fold", min_width=20)
            table.add_column("motivo", overflow="fold", min_width=16)
            for memory in flagged:
                reasons = "; ".join(safety.describe(flag) for flag in memory.flags)
                table.add_row(memory.id, escape(memory.content), escape(reasons))
            out.print(table)
            out.print("Si alguna no debería estar en la memoria, bórrala con vigente forget <id>.")

        data = [
            {
                "memory": memory.model_dump(mode="json"),
                "reasons": [safety.describe(flag) for flag in memory.flags],
            }
            for memory in flagged
        ]
        _emit(ctx, data, render)


@app.command()
def doctor(ctx: typer.Context) -> None:
    """Comprueba la consistencia de la base de datos y de sus índices."""
    with _service(ctx) as service:
        problems = service.doctor()
        _emit(
            ctx,
            {"ok": not problems, "problems": problems},
            lambda: out.print(
                "[green]Todo en orden.[/]"
                if not problems
                else "\n".join(f"[red]✗[/] {escape(problem)}" for problem in problems)
            ),
        )
        if problems:
            raise typer.Exit(1)


@app.command()
def stats(ctx: typer.Context) -> None:
    """Cuenta memorias del workspace."""
    with _service(ctx) as service:
        counts = service.stats()

        def render() -> None:
            table = Table(title=f"workspace: {service.workspace_id}")
            table.add_column("tipo")
            table.add_column("cantidad", justify="right")
            for key, value in counts.items():
                table.add_row(key, str(value))
            out.print(table)

        _emit(ctx, counts, render)
