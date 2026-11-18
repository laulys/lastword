# Vigente

Memoria para agentes de IA que sabe qué sigue siendo verdad.

Cuando un hecho cambia, Vigente no lo borra ni lo mezcla con el nuevo: lo marca como reemplazado y guarda la cadena de cambios. Por defecto solo devuelve lo vigente, y también puede responder qué era verdad en una fecha concreta.

> **En construcción.** Hito 1 de 5: núcleo local con SQLite, búsqueda de texto (FTS5) y validez temporal. Próximo: búsqueda semántica y servidor MCP.

## Probarlo

```bash
uv sync
uv run vigente add "El proyecto usa PostgreSQL 15" --valid-from 2026-03-01
uv run vigente supersede <id> "El proyecto usa SQLite" --valid-from 2026-09-15 --reason "MVP más simple"
uv run vigente search "qué base de datos usa el proyecto"
uv run vigente search "base de datos del proyecto" --as-of 2026-06-01
uv run vigente history <id>
```

La base de datos vive en `~/.vigente/vigente.db` (cámbiala con `--db` o `VIGENTE_DB`).

## Desarrollo

```bash
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy src tests
```

## Diseño

- [Arquitectura](docs/architecture.md)
- [Decisiones (ADRs)](docs/decisions/)
