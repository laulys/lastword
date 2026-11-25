"""CLI de Lastword: administración, depuración y demo."""

from __future__ import annotations

import json
import re
import sqlite3
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
from rich.text import Text

from lastword import environment, safety, store
from lastword.config import (
    DEFAULT_EMBED_MODEL,
    DEFAULT_WORKSPACE,
    EmbeddingSettings,
    parse_search_mode,
)
from lastword.embeddings import DEFAULT_BASE_URL
from lastword.environment import DEFAULT_DB_PATH
from lastword.errors import LastwordError, SecretDetectedError
from lastword.i18n import tr
from lastword.models import (
    Hit,
    Kind,
    ListQuery,
    Memory,
    NewMemory,
    Origin,
    PendingChange,
    PendingPreview,
    RecallQuery,
    RecallResult,
    RememberResult,
    SearchMode,
    Status,
    SupersedeRequest,
    SupersedeResult,
    describe_validation_error,
)
from lastword.service import MemoryService

app = typer.Typer(
    help=tr("Lastword: memoria para agentes de IA que sabe qué sigue siendo verdad."),
    no_args_is_help=True,
    add_completion=False,
)
# Sin emojis por nombre: «:cross_mark:» se vería como otra cosa que lo guardado.
out = Console(emoji=False)
err = Console(stderr=True, emoji=False)

_C1 = re.compile("[\x7f-\x9f]")
_KIND = {Kind.FACT: "hecho", Kind.EPISODE: "episodio"}
_SOURCES = {
    ("fts",): "texto",
    ("vector",): "vector",
    ("fts", "vector"): "ambos",
    ("trust",): "más fiable",
}
_MODES = {"vector": "por significado", "hybrid": "significado + texto", "text": "solo texto"}


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
        Path,
        typer.Option(
            "--db", envvar=environment.names("DB"), help=tr("Archivo SQLite de la memoria.")
        ),
    ] = DEFAULT_DB_PATH,
    workspace: Annotated[
        str,
        typer.Option(
            "--workspace",
            "-w",
            envvar=environment.names("WORKSPACE"),
            help=tr("Espacio de memoria aislado."),
        ),
    ] = DEFAULT_WORKSPACE,
    as_json: Annotated[bool, typer.Option("--json", help=tr("Salida en JSON."))] = False,
    embed_model: Annotated[
        str,
        typer.Option(
            "--embed-model",
            envvar=environment.names("EMBED_MODEL"),
            help=tr(
                "Modelo de embeddings (Ollama u otro servicio compatible con OpenAI). "
                "'none' desactiva la búsqueda semántica."
            ),
        ),
    ] = DEFAULT_EMBED_MODEL,
    embed_url: Annotated[
        str,
        typer.Option(
            "--embed-url",
            envvar=environment.names("EMBED_BASE_URL"),
            help=tr(
                "URL del servicio de embeddings. Si no es local, exige LASTWORD_ALLOW_REMOTE=1."
            ),
        ),
    ] = DEFAULT_BASE_URL,
    search_mode: Annotated[
        str,
        typer.Option(
            "--search-mode",
            envvar=environment.names("SEARCH_MODE"),
            help=tr("vector (por defecto), hybrid (vector + texto) o text."),
        ),
    ] = SearchMode.VECTOR.value,
) -> None:
    # La clave de API y el permiso de envío remoto solo se leen del entorno, nunca de
    # argumentos: no deben quedar en el historial de la shell.
    ctx.obj = Options(
        db=environment.default_db_path() if db == DEFAULT_DB_PATH else db.expanduser(),
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
        typer.echo(_json(payload), err=True)
    else:
        err.print(
            Text.assemble(("Error", "bold red"), " ", (f"({code})", "dim"), " ", _shown(message))
        )
    raise typer.Exit(exit_code)


def _json(payload: object, *, indent: int | None = None) -> str:
    """JSON legible. `json` no escapa los controles C1, y algunos terminales los obedecen."""
    text = json.dumps(payload, ensure_ascii=False, indent=indent)
    return _C1.sub(lambda match: f"\\u{ord(match.group()):04x}", text)


def _shown(text: str, *, flagged: bool = False) -> Text:
    """Un texto guardado, listo para el terminal. Como `Text`, y no como marcas de Rich: nada de
    lo que escribió quien lo guardó puede dar formato, ni convertirse en un emoji."""
    return Text.assemble(("⚠ ", "yellow") if flagged else "", safety.visible(text))


@contextmanager
def _service(ctx: typer.Context) -> Iterator[MemoryService]:
    options = _options(ctx)
    service: MemoryService | None = None
    embedder = None
    try:
        embedder = options.embedding.build()
        service = MemoryService.open(
            options.db,
            options.workspace,
            embedder=embedder,
            search_mode=options.search_mode,
            origin=Origin.PERSON,  # quien usa la CLI es una persona (ADR-0011)
        )
        yield service
    except LastwordError as exc:
        message = exc.message
        if isinstance(exc, SecretDetectedError):
            message += tr(" Si de verdad quieres guardarlo, repite el comando con --allow-secret.")
        _fail(options, exc.code, message, current_id=exc.current_id)
    except ValidationError as exc:
        _fail(options, "invalid_input", describe_validation_error(exc), exit_code=2)
    except sqlite3.Error as exc:  # archivo que no se puede abrir, base ocupada por otro proceso
        error = store.database_error(exc)
        _fail(options, error.code, error.message)
    finally:
        if service is not None:
            service.close()
        if embedder is not None:
            embedder.close()


def _warn(ctx: typer.Context, warnings: list[str]) -> None:
    """En modo humano, los avisos van a stderr; en JSON ya viajan dentro de la respuesta."""
    if not _options(ctx).as_json:
        for warning in warnings:
            err.print(Text.assemble((tr("Aviso:"), "yellow"), " ", _shown(warning)))


def _emit(
    ctx: typer.Context, data: BaseModel | dict[str, Any] | list[Any], render: Callable[[], None]
) -> None:
    if _options(ctx).as_json:
        payload = data.model_dump(mode="json") if isinstance(data, BaseModel) else data
        typer.echo(_json(payload, indent=2))
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
        return tr("desde {date}", date=_fmt(memory.valid_from))
    return f"{_fmt(memory.valid_from)} → {_fmt(memory.valid_to)}"


def _state(memory: Memory) -> str:
    """Estado según las fechas, no según `status`: un hecho con sucesora sigue vigente
    hasta que la sucesora empieza (por ejemplo, un cambio anunciado para la semana próxima)."""
    if memory.kind is Kind.EPISODE:
        return tr(_KIND[memory.kind])
    now = datetime.now(UTC)
    if memory.valid_from > now:
        return tr("futura")
    if memory.valid_to is not None and memory.valid_to <= now:
        return tr("reemplazada")
    return tr("vigente")


def _content(memory: Memory) -> Text:
    """El contenido, con un ⚠ si está marcado (ADR-0007): `lastword review` explica por qué."""
    return _shown(memory.content, flagged=bool(memory.flags))


def _table(title: str | None = None) -> Table:
    # Sin bordes verticales: cabe en 80 columnas con ids completos.
    return Table(title=title, box=box.SIMPLE_HEAD, title_justify="left")


def _wide() -> bool:
    """¿Caben las columnas sin recortar? Si no, todas pueden partir su texto en varias líneas:
    Rich recortaría por la derecha, sin avisar, lo que no cabe."""
    return out.width >= 72


def _at_least(columns: int) -> int | None:
    return columns if _wide() else None


def _memory_table(memories: list[Memory], title: str | None = None) -> Table:
    table = _table(title)
    table.add_column("id", style="dim", no_wrap=_wide(), overflow="fold")
    table.add_column(tr("contenido"), overflow="fold", min_width=_at_least(20))
    table.add_column(tr("vigencia (UTC)"), max_width=12, overflow="fold")
    table.add_column(tr("estado"), no_wrap=_wide(), overflow="fold", min_width=_at_least(11))
    for memory in memories:
        table.add_row(memory.id, _content(memory), _validity(memory), _state(memory))
    return table


def _hits_table(hits: list[Hit], title: str | None = None) -> Table:
    """Muestra la posición, no el score: los scores no son comparables entre consultas.

    El score y la similitud siguen disponibles con --json.
    """
    table = _table(title)
    table.add_column("#", justify="right")
    table.add_column("id", style="dim", no_wrap=_wide(), overflow="fold")
    table.add_column(tr("contenido"), overflow="fold", min_width=_at_least(20))
    table.add_column(tr("vigencia (UTC)"), max_width=12, overflow="fold")
    table.add_column(tr("vía"), no_wrap=_wide(), overflow="fold")
    for position, hit in enumerate(hits, start=1):
        content = _content(hit.memory)
        if hit.memory.status is Status.SUPERSEDED:
            content.stylize("dim")
        source = tr(_SOURCES.get(tuple(hit.matched_by), "+".join(hit.matched_by)))
        table.add_row(str(position), hit.memory.id, content, _validity(hit.memory), source)
    return table


def _render_recall(result: RecallResult) -> None:
    mode = tr(_MODES.get(result.mode, "solo texto"))
    out.print(_hits_table(result.results, title=tr("búsqueda: {mode}", mode=mode)))


def _render_remember(result: RememberResult) -> None:
    memory = result.memory
    saved = tr("Guardada")
    out.print(f"[green]{saved}[/] {memory.id} ({tr(_KIND[memory.kind])}, {_validity(memory)})")
    if result.possible_supersedes:
        out.print(
            tr(
                "\nPosibles reemplazos. Si lo que acabas de guardar deja obsoleto alguno, usa "
                "[bold]lastword supersede <id> --by {id}[/]:",
                id=memory.id,
            )
        )
        out.print(_hits_table(result.possible_supersedes))


def _render_supersede(result: SupersedeResult) -> None:
    olds = [result.old, *result.also_old]
    replaced = tr("Reemplazada")
    out.print(f"[green]{replaced}[/] {', '.join(old.id for old in olds)} → {result.new.id}")
    out.print(_memory_table([*olds, result.new]))


# --- comandos


@app.command(help=tr("Crea la base de datos y el workspace si no existen."))
def init(ctx: typer.Context) -> None:
    options = _options(ctx)
    with _service(ctx) as service:
        data = {"db": str(options.db), "workspace": service.workspace_id}
        _emit(
            ctx,
            data,
            lambda: out.print(
                tr(
                    "Memoria lista en [bold]{db}[/], workspace [bold]{workspace}[/]",
                    db=escape(str(options.db)),
                    workspace=service.workspace_id,
                )
            ),
        )


@app.command(help=tr("Guarda una memoria y sugiere hechos vigentes que podría reemplazar."))
def add(
    ctx: typer.Context,
    content: Annotated[str, typer.Argument(help=tr("Texto de la memoria."))],
    kind: Annotated[
        Kind, typer.Option(help=tr("fact: puede cambiar. episode: algo que pasó."))
    ] = Kind.FACT,
    tag: Annotated[
        list[str] | None, typer.Option("--tag", "-t", help=tr("Etiqueta (se puede repetir)."))
    ] = None,
    valid_from: Annotated[
        str | None, typer.Option(help=tr("Desde cuándo es verdad (ISO 8601). Por defecto, ahora."))
    ] = None,
    allow_secret: Annotated[
        bool,
        typer.Option(
            "--allow-secret",
            help=tr(
                "Guardar aunque parezca una clave o contraseña. El servidor MCP no lo permite."
            ),
        ),
    ] = False,
) -> None:
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


@app.command(
    help=tr(
        "Reemplaza uno o varios hechos por su versión nueva, conservando el historial.\n\n"
        "Con texto, crea la versión nueva. Con --by, enlaza una memoria que ya guardaste, por "
        "ejemplo la que `add` acaba de proponer como reemplazo. Con --also, la versión nueva "
        "reemplaza además otros hechos."
    )
)
def supersede(
    ctx: typer.Context,
    old_id: Annotated[str, typer.Argument(help=tr("Id de la versión vigente."))],
    new_content: Annotated[
        str | None, typer.Argument(help=tr("Texto de la versión nueva (o usa --by)."))
    ] = None,
    by: Annotated[
        str | None,
        typer.Option(
            "--by", help=tr("Id de una memoria ya guardada que pasa a ser la versión nueva.")
        ),
    ] = None,
    also: Annotated[
        list[str] | None,
        typer.Option(
            "--also",
            help=tr("Otro hecho que la versión nueva también reemplaza (se puede repetir)."),
        ),
    ] = None,
    valid_from: Annotated[
        str | None, typer.Option(help=tr("Desde cuándo es verdad lo nuevo (ISO 8601)."))
    ] = None,
    reason: Annotated[str | None, typer.Option(help=tr("Por qué cambió."))] = None,
    allow_secret: Annotated[
        bool,
        typer.Option(
            "--allow-secret",
            help=tr(
                "Guardar aunque parezca una clave o contraseña. El servidor MCP no lo permite."
            ),
        ),
    ] = False,
) -> None:
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


@app.command(help=tr("Busca memorias. Por defecto, solo lo que es verdad ahora."))
def search(
    ctx: typer.Context,
    query: Annotated[str, typer.Argument(help=tr("Qué buscar, en lenguaje natural."))],
    top: Annotated[int, typer.Option("--top", "-k", help=tr("Número de resultados."))] = 5,
    as_of: Annotated[
        str | None, typer.Option(help=tr("Qué era verdad en esta fecha (ISO 8601)."))
    ] = None,
    include_history: Annotated[
        bool, typer.Option("--history", help=tr("Incluir versiones reemplazadas."))
    ] = False,
    kind: Annotated[Kind | None, typer.Option(help=tr("Solo fact o solo episode."))] = None,
) -> None:
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


@app.command(help=tr("Calcula los vectores que falten con el modelo de embeddings configurado."))
def reindex(ctx: typer.Context) -> None:
    with _service(ctx) as service:
        result = service.reindex()
        _emit(
            ctx,
            result,
            lambda: out.print(
                tr(
                    "[green]Vectores calculados:[/] {n} (modelo {model}, {dim} dimensiones)",
                    n=result.embedded,
                    model=escape(result.model),
                    dim=result.dim or "?",
                )
            ),
        )


@app.command(help=tr("Muestra una memoria."))
def show(
    ctx: typer.Context,
    memory_id: Annotated[str, typer.Argument(help=tr("Id de la memoria."))],
) -> None:
    with _service(ctx) as service:
        memory = service.get(memory_id)
        _emit(ctx, memory, lambda: out.print(_memory_table([memory])))


@app.command(help=tr("Muestra todas las versiones de un hecho, de la más antigua a la vigente."))
def history(
    ctx: typer.Context,
    memory_id: Annotated[str, typer.Argument(help=tr("Id de cualquier versión del hecho."))],
) -> None:
    with _service(ctx) as service:
        chain = service.history(memory_id)
        _emit(
            ctx,
            [memory.model_dump(mode="json") for memory in chain],
            lambda: out.print(_memory_table(chain)),
        )


@app.command(help=tr("Borra para siempre un hecho y todas sus versiones."))
def forget(
    ctx: typer.Context,
    memory_id: Annotated[str, typer.Argument(help=tr("Id de cualquier versión del hecho."))],
    yes: Annotated[bool, typer.Option("--yes", "-y", help=tr("No pedir confirmación."))] = False,
) -> None:
    with _service(ctx) as service:
        memory = service.get(memory_id)  # antes de preguntar: que exista
        if not yes:
            if _options(ctx).as_json:  # la pregunta se mezclaría con el JSON de la salida
                message = tr("Con --json no se puede preguntar: confirma el borrado con --yes.")
                _fail(_options(ctx), "invalid_input", message, exit_code=2)
            # Con todas sus versiones delante: pueden venir de otro origen que la pedida.
            out.print(_memory_table(service.history(memory.id)))
            typer.confirm(
                tr("¿Borrar para siempre {id} y todas sus versiones?", id=memory.id), abort=True
            )
        result = service.forget(memory.id)
        deleted = result.deleted_ids
        _emit(
            ctx,
            result,
            lambda: out.print(
                tr("[green]Borradas[/] {n}: {ids}", n=len(deleted), ids=", ".join(deleted))
            ),
        )
        _warn(ctx, result.warnings)


@app.command("list", help=tr("Lista memorias, las más recientes primero."))
def list_memories(
    ctx: typer.Context,
    kind: Annotated[Kind | None, typer.Option(help=tr("Solo fact o solo episode."))] = None,
    status: Annotated[Status | None, typer.Option(help=tr("active o superseded."))] = None,
    tag: Annotated[str | None, typer.Option(help=tr("Solo con esta etiqueta."))] = None,
    limit: Annotated[int, typer.Option(help=tr("Tamaño de página."))] = 20,
    cursor: Annotated[str | None, typer.Option(help=tr("Cursor de la página siguiente."))] = None,
) -> None:
    with _service(ctx) as service:
        query = ListQuery.model_validate(
            {"kind": kind, "status": status, "tag": tag, "limit": limit, "cursor": cursor}
        )
        page = service.list_memories(query)

        def render() -> None:
            out.print(_memory_table(page.items))
            if page.next_cursor is not None:
                out.print(tr("Siguiente página: --cursor {cursor}", cursor=page.next_cursor))

        _emit(ctx, page, render)


@app.command(
    help=tr(
        "Lista las memorias marcadas: posibles órdenes al agente, caracteres ocultos o secretos."
    )
)
def review(ctx: typer.Context) -> None:
    with _service(ctx) as service:
        flagged = service.review()

        def render() -> None:
            if not flagged:
                out.print(
                    tr(
                        "[green]Nada marcado.[/] Ninguna memoria parece contener órdenes al "
                        "agente, caracteres ocultos ni secretos."
                    )
                )
                return
            table = _table(tr("memorias marcadas: {n}", n=len(flagged)))
            table.add_column("id", style="dim", no_wrap=_wide(), overflow="fold")
            table.add_column(tr("contenido"), overflow="fold", min_width=_at_least(20))
            table.add_column(tr("motivo"), overflow="fold", min_width=_at_least(16))
            for memory in flagged:
                reasons = "; ".join(safety.describe(flag) for flag in memory.flags)
                table.add_row(memory.id, _shown(memory.content), _shown(reasons))
            out.print(table)
            out.print(
                tr("Si alguna no debería estar en la memoria, bórrala con lastword forget <id>.")
            )

        data = [
            {
                "memory": memory.model_dump(mode="json"),
                "reasons": [safety.describe(flag) for flag in memory.flags],
            }
            for memory in flagged
        ]
        _emit(ctx, data, render)


@app.command(help=tr("Da por bueno un hecho que guardó un agente: solo una persona lo cambia."))
def confirm(
    ctx: typer.Context,
    memory_id: Annotated[str, typer.Argument(help=tr("Id de la memoria."))],
) -> None:
    with _service(ctx) as service:
        memory = service.confirm(memory_id)

        def render() -> None:
            out.print(tr("[green]Confirmada[/] {id}", id=memory.id))
            out.print(_memory_table([memory]))
            if memory.tags:
                out.print(Text.assemble(tr("etiquetas"), ": ", _shown(", ".join(memory.tags))))

        _emit(ctx, memory, render)


_ORIGIN = {Origin.PERSON: "persona", Origin.AGENT: "agente", Origin.EXTERNAL: "de fuera"}


def _pending_rows(preview: PendingPreview, change: PendingChange) -> list[tuple[str, Text]]:
    """Todo lo que se aplicaría al aceptar, cada dato con su etiqueta."""
    rows = [(tr("pedido por"), Text(tr(_ORIGIN[change.origin])))]
    rows += [(tr("sigue valiendo"), _content(old)) for old in preview.olds]
    rows.append((tr("pasaría a ser"), _shown(preview.content, flagged=bool(preview.flags))))
    rows += [(tr("ya reemplazó a"), _content(old)) for old in preview.already_replaced]
    start = tr("ahora") if preview.start is None else _fmt(preview.start)
    rows.append((tr("desde"), Text(start)))
    if preview.tags:
        rows.append((tr("etiquetas"), _shown(", ".join(preview.tags))))
    if change.reason:
        rows.append((tr("motivo"), _shown(change.reason)))
    if preview.stale:
        rows.append((tr("aviso"), Text(tr("ya no se puede aceptar: algo cambió después"))))
    return rows


def _render_pending(service: MemoryService, changes: list[PendingChange]) -> None:
    """Un bloque por cambio, y no una tabla: en un terminal estrecho, una tabla recorta el texto
    por la derecha. Las etiquetas van en su columna: quien pide el cambio escribió el texto y el
    motivo, y no debe poder imitar los demás datos."""
    out.print(tr("cambios pendientes: {n}", n=len(changes)))
    for change in changes:
        block = Table.grid(padding=(0, 1))
        block.add_column(style="dim", overflow="fold", max_width=14)
        block.add_column(overflow="fold")
        for label, value in _pending_rows(service.preview(change), change):
            block.add_row(label, value)
        out.print()
        out.print(Text(change.id, style="bold"), overflow="fold")
        out.print(block)
    out.print()


@app.command(help=tr("Lista los cambios que esperan a que una persona los acepte o rechace."))
def pending(ctx: typer.Context) -> None:
    with _service(ctx) as service:
        changes = service.pending()

        def render() -> None:
            if not changes:
                out.print(tr("[green]Ningún cambio pendiente.[/]"))
                return
            _render_pending(service, changes)
            out.print(tr("Decide con lastword accept <id> o lastword reject <id>."))

        _emit(ctx, [change.model_dump(mode="json") for change in changes], render)


@app.command(help=tr("Acepta un cambio pendiente: reemplaza el hecho y pasa a ser tuyo."))
def accept(
    ctx: typer.Context,
    pending_id: Annotated[str, typer.Argument(help=tr("Id del cambio pendiente."))],
) -> None:
    with _service(ctx) as service:
        result = service.accept(pending_id)
        _emit(ctx, result, lambda: _render_supersede(result))
        _warn(ctx, result.warnings)


@app.command(help=tr("Rechaza un cambio pendiente y borra su texto."))
def reject(
    ctx: typer.Context,
    pending_id: Annotated[str, typer.Argument(help=tr("Id del cambio pendiente."))],
) -> None:
    with _service(ctx) as service:
        result = service.reject(pending_id)

        def render() -> None:
            out.print(tr("[green]Rechazado.[/] El hecho anterior sigue vigente."))
            if result.kept_id is not None:
                out.print(
                    tr(
                        "La memoria que se pedía usar sigue guardada: bórrala con "
                        "lastword forget {id} si no es cierta.",
                        id=result.kept_id,
                    )
                )

        _emit(ctx, result, render)
        _warn(ctx, result.warnings)


@app.command(help=tr("Comprueba la consistencia de la base de datos y de sus índices."))
def doctor(ctx: typer.Context) -> None:
    with _service(ctx) as service:
        problems = service.doctor()
        _emit(
            ctx,
            {"ok": not problems, "problems": problems},
            lambda: out.print(
                tr("[green]Todo en orden.[/]")
                if not problems
                else "\n".join(f"[red]✗[/] {escape(problem)}" for problem in problems)
            ),
        )
        if problems:
            raise typer.Exit(1)


@app.command(help=tr("Cuenta memorias del workspace."))
def stats(ctx: typer.Context) -> None:
    with _service(ctx) as service:
        counts = service.stats()

        def render() -> None:
            table = Table(title=f"workspace: {service.workspace_id}")
            table.add_column(tr("tipo"))
            table.add_column(tr("cantidad"), justify="right")
            for key, value in counts.items():
                table.add_row(key, str(value))
            out.print(table)

        _emit(ctx, counts, render)
