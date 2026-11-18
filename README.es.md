# Vigente

[![CI](https://github.com/laulys/vigente/actions/workflows/ci.yml/badge.svg)](https://github.com/laulys/vigente/actions/workflows/ci.yml)
[![Licencia: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)

**Memoria para agentes de IA que sabe qué sigue siendo verdad.** · [Read in English](README.md)

Cuando un hecho cambia, la mayoría de las memorias para agentes lo sobrescriben, o guardan las dos versiones y confían en que la búsqueda acierte. Vigente no hace ninguna de las dos cosas. El hecho anterior queda **reemplazado**: se cierra con una fecha y se enlaza con su sustituto.

- La búsqueda devuelve solo lo que es verdad **ahora**.
- `as_of` devuelve lo que era verdad **en cualquier fecha pasada**.
- `history` muestra cómo cambió un hecho.

Funciona en local como **servidor MCP**, así que Claude Code, Codex, Cursor o cualquier cliente MCP pueden usarlo y compartir la misma memoria. Todo vive en un único archivo SQLite.

![Una sesión real de la CLI: un hecho cambia, Vigente propone el reemplazo y la búsqueda devuelve lo vigente ahora o en una fecha pasada](demo/vigente.gif)

<sub>Sesión real, grabada por [`demo/record.py`](demo/record.py) con el modelo de embeddings en marcha.</sub>

## Resultados

![Qué es el primer resultado de la búsqueda según el sistema de memoria](eval/results/e1_obsoletos.svg)

| | Memoria ingenua | Vigente |
|---|---:|---:|
| El primer resultado es el dato correcto ([datos sintéticos](eval/results/e1_synthetic.md), búsqueda por vectores) | 48% | **95%** |
| El primer resultado es una versión obsoleta del dato | 51% | **0%** |
| Recall@5 | 97% | **100%** |

- **Reordenar por lo más reciente no basta.** Elimina los obsoletos, pero entonces el primer resultado es otro dato en la mitad de las preguntas (51%).
- **[LongMemEval](eval/results/e2_longmemeval.md)**, 470 preguntas de un benchmark público: sin reemplazos, la versión vieja de un dato actualizado sale antes que la nueva en el **54–56%** de los casos, con cualquier modo de búsqueda.
- **[Escala](eval/results/scale.md):** con 50.000 memorias, una búsqueda tarda 62 ms y un borrado completo 52 ms, más unos 15 ms del modelo de embeddings.
- **[Un agente real](eval/results/agent_demo.md)** (Claude Code, cuatro sesiones independientes que solo comparten Vigente) sigue el protocolo por su cuenta: busca antes de guardar, enlaza los cambios sin duplicar hechos y responde con lo vigente.

Las reglas de decisión se fijaron **antes** de medir ([protocolo](eval/README.md)), y E1 y E2 dan resultados idénticos al repetirlos.

## Cómo funciona

- **Un solo archivo.** SQLite guarda las filas, un índice de texto (FTS5) y un índice vectorial (sqlite-vec). Cada escritura o borrado es una sola transacción sobre los tres.
- **Los hechos nunca se editan.** `supersede` crea la versión nueva, cierra la anterior y las enlaza. «Verdad en el instante t» significa `valid_from <= t < valid_to`.
- **El motor no llama a ningún LLM.** `remember` usa embeddings locales (embeddinggemma con Ollama) para proponer hechos que lo nuevo podría reemplazar. Decide el agente, que ya es un LLM.
- **Olvidar es borrar de verdad.** `forget` borra todas las versiones de un hecho de las filas, de los dos índices y del disco. Un test comprueba que los bytes desaparecen del archivo.
- **Si algo falla, sigue funcionando.** Sin embeddings, busca por texto. Se niega a enviar memorias a un servicio que no sea local salvo que lo permitas.

## Probarlo

```bash
git clone https://github.com/laulys/vigente && cd vigente
uv sync
ollama pull embeddinggemma        # opcional: sin él, Vigente busca solo por texto
uv run vigente add "El proyecto usa PostgreSQL 15" --valid-from 2026-03-01
uv run vigente add "Migramos a SQLite para el MVP"   # propone reemplazar el hecho anterior
uv run vigente supersede <id-viejo> --by <id-nuevo>
uv run vigente search "¿qué base de datos usamos?"
uv run vigente search "¿qué base de datos usamos?" --as-of 2026-06-01
```

## Usarlo desde un agente (MCP)

**Claude Code:**

```bash
claude mcp add vigente -e VIGENTE_WORKSPACE=mi-proyecto -- uv --directory /ruta/a/vigente run vigente-mcp
```

**Codex:**

```bash
codex mcp add vigente --env VIGENTE_WORKSPACE=mi-proyecto -- uv --directory /ruta/a/vigente run vigente-mcp
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

| Herramienta | Qué hace |
|---|---|
| `remember` | Guarda un hecho o un episodio y propone hechos vigentes que podría reemplazar |
| `recall` | Busca lo que es verdad ahora, o en una fecha pasada (`as_of`) |
| `supersede` | Cierra un hecho obsoleto y enlaza su versión nueva (texto nuevo, o el id que acaba de devolver `remember`) |
| `history` | Todas las versiones de un hecho |
| `list_memories` | Lista memorias con filtros |
| `forget` | Borra un hecho y todas sus versiones, también del disco (marcada como destructiva) |

El workspace lo fija la configuración del servidor, así que un agente no puede leer otro. Los clientes configurados con el mismo workspace comparten la memoria, incluso a la vez: lo que guarda Claude Code lo encuentran Codex y Cursor.

> **Si tu cliente tiene memoria propia**, el agente puede guardar lo mismo en los dos sitios, y solo Vigente sabrá cuándo deja de ser verdad. En Claude Code puedes desactivar su memoria automática con `"autoMemoryEnabled": false` en la configuración del proyecto, o con `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` ([documentación](https://code.claude.com/docs/en/memory)).

## Configuración

| Variable | Por defecto | Para qué |
|---|---|---|
| `VIGENTE_DB` | `~/.vigente/vigente.db` | Archivo de la memoria |
| `VIGENTE_WORKSPACE` | `default` | Espacio aislado de memorias |
| `VIGENTE_EMBED_MODEL` | `embeddinggemma` | Modelo de embeddings; `none` para buscar solo por texto |
| `VIGENTE_EMBED_BASE_URL` | `http://localhost:11434/v1` | Cualquier servicio con la API de OpenAI |
| `VIGENTE_EMBED_API_KEY` | | Solo si el servicio la pide |
| `VIGENTE_ALLOW_REMOTE` | desactivado | Necesario para enviar memorias a un servicio no local |
| `VIGENTE_SEARCH_MODE` | `vector` | `hybrid` (vectores + texto) o `text` |

Si el servicio de embeddings no responde, Vigente sigue funcionando con búsqueda de texto y lo avisa. `vigente reindex` calcula después los vectores pendientes.

## Decisiones de diseño

Cada decisión tiene un ADR con las alternativas que se consideraron:

1. [Un solo archivo SQLite con FTS5 y sqlite-vec, sin ORM](docs/decisions/0001-sqlite-fts5-sqlite-vec.md)
2. [Los hechos se reemplazan sin editarlos, con fechas de validez](docs/decisions/0002-reemplazo-append-only-con-validez.md)
3. [El motor no llama a ningún LLM: propone y el agente decide](docs/decisions/0003-sin-llm-en-el-motor.md)
4. [embeddinggemma por defecto, elegido con un benchmark; umbral 0.51](docs/decisions/0004-modelo-de-embeddings.md)
5. [`supersede` puede enlazar una memoria ya guardada](docs/decisions/0005-dos-formas-de-reemplazar.md)
6. [Búsqueda por vectores por defecto, decidida con la evaluación](docs/decisions/0006-busqueda-por-vectores.md)

El diseño completo está en [docs/architecture.md](docs/architecture.md).

## Limitaciones

- **Las propuestas de reemplazo tienen ruido.** La similitud no distingue «esto actualiza aquello» de «esto añade información sobre lo mismo», así que las frases del mismo tema también reciben propuestas. Por eso decide el agente.
- **Las cadenas son lineales:** una versión nueva reemplaza a un solo hecho. «Ana y Luis se mudaron a Lisboa» no puede cerrar dos hechos a la vez.
- **La búsqueda es exacta (KNN por fuerza bruta).** Su coste crece de forma lineal. Con cientos de miles de memorias harían falta vectores cuantizados o un índice aproximado.
- **La memoria es local.** La comparten los clientes del mismo ordenador; los agentes en la nube y tus compañeros no llegan a ella.
- **La evaluación tiene puntos ciegos.** Hay pocas consultas de coincidencia exacta (códigos de error, identificadores), que es donde la búsqueda por texto suele aportar, y el conjunto sintético sale de plantillas.

## Trabajo relacionado

Las descripciones se basan en la documentación de cada proyecto (consultada en noviembre de 2026).

- **[Delfos](https://github.com/MiltMont/Delfos)** (Apache-2.0): servidor MCP de memoria en grafo para repositorios de código, con un recorrido guiado por un LLM sobre un grafo Cue → Tag → Content y un motor en C++. Fue la inspiración de este proyecto. Vigente se centra en hechos generales que cambian con el tiempo, no en código, y su motor no llama a ningún LLM.
- **[Graphiti](https://github.com/getzep/graphiti)** (Apache-2.0) y **Zep**, el servicio comercial construido sobre él: grafos de contexto temporales que invalidan los hechos obsoletos en vez de borrarlos, con aristas bitemporales. Usan un LLM para extraer entidades y una base de datos de grafos (Neo4j, FalkorDB o Neptune). Es la idea más cercana. Vigente cambia el grafo y el LLM por un único archivo SQLite y decisiones que toma el agente cliente.
- **[Mem0](https://github.com/mem0ai/mem0)** (Apache-2.0): capa de memoria que usa un LLM para extraer hechos al guardar. Desde su algoritmo de abril de 2026, la extracción solo añade memorias, y el estado actual se resuelve con una recuperación que tiene en cuenta el tiempo. Almacén vectorial (Qdrant por defecto).
- **[Letta](https://github.com/letta-ai/letta)**, antes MemGPT (Apache-2.0): plataforma de agentes con estado que editan sus propios bloques de memoria mediante herramientas, con una memoria de archivo en una base de datos vectorial.
- **[Servidor de memoria de referencia de MCP](https://github.com/modelcontextprotocol/servers/tree/main/src/memory)** (MIT): un grafo de conocimiento en un archivo JSONL, sin versiones ni fechas.
- **[LongMemEval](https://github.com/xiaowu0162/LongMemEval)** (ICLR 2025, MIT): benchmark de memoria a largo plazo para asistentes conversacionales, usado aquí en [E2](eval/results/e2_longmemeval.md).

## Desarrollo

```bash
uv run pytest                                              # sin red ni modelos
VIGENTE_TEST_OLLAMA=1 uv run pytest tests/test_ollama.py   # con Ollama local
uv run ruff check . && uv run ruff format --check .
uv run mypy src tests eval demo
```

Cómo repetir toda la evaluación: [eval/README.md](eval/README.md).

El proyecto se construyó en cinco hitos (diseño, núcleo, búsqueda semántica, servidor MCP y evaluación), cada uno con sus decisiones en un ADR. Desde el servidor MCP, cada hito entró por un [pull request](https://github.com/laulys/vigente/pulls?q=is%3Apr) con sus decisiones y mediciones.

## Licencia

[MIT](LICENSE)
