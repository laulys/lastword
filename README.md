# Vigente

[![CI](https://github.com/laulys/vigente/actions/workflows/ci.yml/badge.svg)](https://github.com/laulys/vigente/actions/workflows/ci.yml)

Memoria para agentes de IA que sabe qué sigue siendo verdad.

Cuando un hecho cambia, Vigente no lo borra ni lo mezcla con el nuevo: lo marca como reemplazado y guarda la cadena de cambios. Por defecto solo devuelve lo vigente, y también puede responder qué era verdad en una fecha concreta.

> **En construcción.** Hito 3 de 5: servidor MCP sobre un núcleo local con SQLite, búsqueda híbrida y validez temporal. Próximo: evaluación.

## Hoja de ruta

- [x] **Hito 0. Diseño:** arquitectura, esquema y plan de evaluación, con sqlite-vec probado antes de escribir código.
- [x] **Hito 1. Núcleo:** SQLite + FTS5, reemplazos con validez temporal, borrado verificado en disco y CLI.
- [x] **Hito 2. Búsqueda semántica:** embeddings locales, índice vectorial, búsqueda híbrida y elección del modelo con un benchmark propio.
- [x] **Hito 3. Servidor MCP:** seis herramientas, probadas con el cliente oficial por stdio y con un agente real.
- [ ] **Hito 4. Evaluación:** datos sintéticos y LongMemEval, con resultados publicados tal como salgan.
- [ ] **Hito 5. Cierre:** README final y demo.

## Probarlo

```bash
uv sync
ollama pull embeddinggemma        # opcional: sin él, Vigente busca solo por texto
uv run vigente add "El proyecto usa PostgreSQL 15 como base de datos" --valid-from 2026-03-01
uv run vigente add "Migramos a SQLite para el MVP"     # propone reemplazar el hecho anterior
uv run vigente supersede <id> "El proyecto usa SQLite" --reason "MVP más simple"
uv run vigente search "¿qué base de datos usamos?"
uv run vigente search "base de datos" --as-of 2026-06-01
uv run vigente history <id>
```

## Usarlo desde un agente (MCP)

Vigente expone seis herramientas por MCP: `remember`, `recall`, `supersede`, `forget`, `history` y `list_memories`. El workspace lo fija la configuración, nunca el agente.

**Claude Code:**

```bash
claude mcp add vigente -e VIGENTE_WORKSPACE=mi-proyecto -- uv --directory /ruta/a/vigente run vigente-mcp
```

**Cursor** (`.cursor/mcp.json`) o **Claude Desktop** (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "vigente": {
      "command": "uv",
      "args": ["--directory", "/ruta/a/vigente", "run", "vigente-mcp"],
      "env": { "VIGENTE_WORKSPACE": "mi-proyecto" }
    }
  }
}
```

Cuando el agente guarda algo que deja obsoleto un hecho anterior, `remember` se lo propone y el agente lo enlaza con `supersede`. Desde entonces el hecho anterior no se devuelve, aunque queda en el historial.

## Configuración

| Variable | Por defecto | Para qué |
|---|---|---|
| `VIGENTE_DB` | `~/.vigente/vigente.db` | Archivo de la memoria |
| `VIGENTE_WORKSPACE` | `default` | Espacio aislado de memorias |
| `VIGENTE_EMBED_MODEL` | `embeddinggemma` | Modelo de embeddings; `none` para usar solo texto |
| `VIGENTE_EMBED_BASE_URL` | `http://localhost:11434/v1` | Cualquier servicio compatible con la API de OpenAI |
| `VIGENTE_EMBED_API_KEY` | | Solo si el servicio la pide |
| `VIGENTE_ALLOW_REMOTE` | desactivado | Necesario para enviar memorias a un servicio no local |

Si el servicio de embeddings no responde, Vigente sigue funcionando con búsqueda de texto y lo avisa. `vigente reindex` calcula después los vectores pendientes.

## Resultados

- [Demo con un agente real](eval/results/agent_demo.md): cuatro sesiones independientes de Claude Code que solo comparten Vigente. El agente guarda, detecta y enlaza cambios sin duplicar hechos (incluido uno anunciado para la semana siguiente) y en una sesión nueva responde con lo vigente.
- [Elección del modelo de embeddings](eval/results/embedding_choice.md): con embeddings, la detección de hechos reemplazados sube de F1 0.43 a 0.76 en un conjunto pequeño en español.

## Desarrollo

```bash
uv run pytest                                          # sin red ni modelos
VIGENTE_TEST_OLLAMA=1 uv run pytest tests/test_ollama.py   # con Ollama local
uv run ruff check . && uv run ruff format --check .
uv run mypy src tests eval
uv run python eval/embedding_choice.py                 # repite el benchmark
uv run python eval/agent_demo.py                       # demo con un agente (requiere la CLI de Claude)
```

## Diseño

- [Arquitectura](docs/architecture.md)
- [Decisiones (ADRs)](docs/decisions/)

## Licencia

[MIT](LICENSE)
