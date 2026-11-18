# Vigente — Arquitectura (Hito 0)

Estado: **aprobada** (18-11-2026). Hito 1 implementado. Lo marcado con *(Hito 1)* recoge lo que se aprendió al implementarlo.

## 1. Resumen

Vigente es un servidor MCP local que da memoria persistente a agentes de IA y separa lo que **sigue siendo verdad** de lo que **fue verdad**. Los hechos no se editan ni se borran cuando cambian: se reemplazan con una cadena enlazada y fechas de validez. Por defecto la búsqueda solo devuelve lo vigente, y con `as_of` devuelve lo que era verdad en una fecha.

Todo vive en un único archivo SQLite: filas, índice de texto (FTS5) e índice vectorial (sqlite-vec). Así cualquier escritura o borrado es **una sola transacción** sobre los tres.

## 2. Entorno verificado (18-11-2026)

| Componente | Resultado |
|---|---|
| uv | 0.13.9 (instalado con Homebrew) |
| Python 3.12.16 / 3.13.18 (uv) y 3.15.2 (Homebrew) | Los tres cargan extensiones de SQLite |
| SQLite | 3.53–3.54, FTS5 disponible |
| sqlite-vec | v0.1.9 carga correctamente |
| Ollama | Instalado. Solo tiene `qwen3:8b` (chat); **falta un modelo de embeddings** |

Comportamiento de sqlite-vec comprobado con scripts de prueba:

1. **Filtros dentro del KNN.** Con `workspace_id` como *partition key* y columnas de metadatos (`status`, `valid_from`, `valid_to`), el filtro se aplica *antes* de elegir los `k` vecinos. Prueba: un vecino idéntico de otro workspace y uno reemplazado se excluyeron, y el resultado fue el siguiente más cercano válido. No hace falta sobremuestrear.
2. **`as_of` funciona dentro del KNN** con `valid_from <= t AND valid_to > t`.
3. **`UPDATE` de metadatos funciona.** Cerrar `valid_to` al reemplazar saca la fila de las búsquedas vigentes.
4. **Las columnas de metadatos no aceptan `NULL`.** Hace falta un centinela para «vigente».
5. ⚠️ **Un `INSERT` que falla deja una fila fantasma a medias** en la tabla vectorial si no hay transacción explícita. Con `BEGIN … ROLLBACK` desaparece por completo (verificado).

Los puntos 4 y 5 determinan reglas del diseño (ver §4 y ADR-0001).

## 3. Capas

```
            ┌───────────────┐   ┌──────────┐
            │ mcp_server.py │   │  cli.py  │      interfaces: validan entrada y formatean salida
            └───────┬───────┘   └────┬─────┘
                    └───────┬────────┘
                     ┌──────▼──────┐
                     │ service.py  │             casos de uso, reglas de reemplazo, transacciones
                     └──┬───────┬──┘
           ┌────────────▼┐     ┌▼──────────────┐
           │ retrieval.py│     │ embeddings.py │  FTS, vector, RRF, filtro temporal / Embedder
           └──────┬──────┘     └───────────────┘
           ┌──────▼──────┐
           │  store.py   │                       SQL, migraciones, conexión
           └─────────────┘
   models.py y config.py: compartidos por todos
```

- Las dependencias van en un solo sentido, de arriba abajo.
- Solo `store.py` escribe SQL.
- Solo `service.py` abre transacciones. Una operación del usuario es una transacción.
- `Embedder` es la única interfaz abstracta. Tiene dos implementaciones reales: la compatible con OpenAI y `FakeEmbedder` para tests.

## 4. Esquema SQL

Convenciones:

- Fechas en **milisegundos epoch UTC** (`INTEGER`).
- «Vigente» se guarda como `valid_to = 253402300799999` (31-12-9999). Las columnas de metadatos de sqlite-vec no aceptan `NULL`, y usar el mismo centinela en todas las tablas evita dos formas de decir lo mismo. La API pública expone `valid_to: null`; la conversión ocurre solo en `store.py`.
- IDs ULID: ordenables por tiempo y legibles.
- Migraciones con `PRAGMA user_version`.
- Conexión con `autocommit=True` (Python ≥ 3.12) y `BEGIN IMMEDIATE` explícito en cada escritura.
- `journal_mode=WAL` y `busy_timeout`, porque el servidor MCP y la CLI pueden abrir el mismo archivo a la vez.
- `secure_delete=ON`: SQLite sobrescribe con ceros lo que borra, en vez de dejarlo en páginas libres. *(Hito 1)*

```sql
CREATE TABLE workspaces (
  id          TEXT PRIMARY KEY,          -- slug: "portfolio", "trabajo"
  created_at  INTEGER NOT NULL
);

CREATE TABLE memories (
  seq              INTEGER PRIMARY KEY,  -- alias estable de rowid (ver nota)
  id               TEXT NOT NULL UNIQUE, -- ULID, identificador público
  workspace_id     TEXT NOT NULL REFERENCES workspaces(id),
  kind             TEXT NOT NULL CHECK (kind IN ('fact', 'episode')),
  content          TEXT NOT NULL CHECK (length(content) BETWEEN 1 AND 4000),
  tags             TEXT NOT NULL DEFAULT '[]',          -- array JSON
  source           TEXT,                                -- "mcp", "cli", ...
  confidence       REAL CHECK (confidence BETWEEN 0 AND 1),
  created_at       INTEGER NOT NULL,     -- cuándo se registró (tiempo de registro)
  valid_from       INTEGER NOT NULL,     -- desde cuándo es verdad (tiempo de validez)
  valid_to         INTEGER NOT NULL DEFAULT 253402300799999,
  status           TEXT NOT NULL CHECK (status IN ('active', 'superseded')),
  supersedes_id    TEXT REFERENCES memories(id),
  superseded_by_id TEXT REFERENCES memories(id),
  supersede_reason TEXT,
  embedding_model  TEXT,                 -- NULL si no tiene vector
  CHECK (valid_from < valid_to),
  CHECK (kind = 'fact' OR (supersedes_id IS NULL AND superseded_by_id IS NULL))
);
CREATE INDEX memories_ws_status ON memories(workspace_id, status);
CREATE INDEX memories_ws_validity ON memories(workspace_id, valid_from, valid_to);

-- Texto: tabla FTS5 propia (no external-content), con rowid = memories.seq.
-- Duplica el texto, pero el borrado es un DELETE normal y no depende de triggers.
CREATE VIRTUAL TABLE memories_fts USING fts5(
  content,
  tags,
  tokenize = 'unicode61 remove_diacritics 2'   -- "migración" = "migracion"; sin stemming (ES + EN)
);

-- Registro de espacios vectoriales: un índice por (modelo, dimensión).
CREATE TABLE embedding_spaces (
  id          INTEGER PRIMARY KEY,
  model       TEXT NOT NULL,
  dim         INTEGER NOT NULL,
  table_name  TEXT NOT NULL UNIQUE,      -- "vec_1", "vec_2", ...
  is_active   INTEGER NOT NULL DEFAULT 0,
  created_at  INTEGER NOT NULL,
  UNIQUE (model, dim)
);

-- Creada en tiempo de ejecución para cada embedding_space (ejemplo con dim = 1024):
CREATE VIRTUAL TABLE vec_1 USING vec0(
  memory_id     TEXT PRIMARY KEY,
  workspace_id  TEXT PARTITION KEY,
  status        TEXT,
  valid_from    INTEGER,
  valid_to      INTEGER,
  embedding     FLOAT[1024] distance_metric=cosine
);
```

**Por qué `seq` además del ULID** *(Hito 1)*: el índice FTS se enlaza por número de fila. En tablas sin `INTEGER PRIMARY KEY`, `VACUUM` puede renumerar el rowid implícito, y eso desincronizaría el índice en silencio. `seq` es un alias explícito y estable; `id` sigue siendo el identificador público.

**Por qué hay columnas duplicadas en `vec_N`:** `status`, `valid_from` y `valid_to` se repiten ahí para que el filtro ocurra dentro del KNN (§2.1). Se mantienen sincronizadas en la misma transacción que `memories`, y `vigente doctor` comprueba que coincidan.

**Invariante comprobada por tests y por `vigente doctor`:** el conjunto de ids de `memories`, `memories_fts` y el `vec_N` activo es el mismo (salvo memorias sin embedding, que no están en `vec_N`).

## 5. Reglas de dominio

- **Reemplazar** (`supersede(old, new_content, valid_from=t)`), en una sola transacción:
  1. Inserta `new` con `valid_from = t` y `supersedes_id = old`. Si no se indica `t`, se usa `max(ahora, old.valid_from + 1 ms)`, para que un reemplazo en el mismo milisegundo no falle. El motivo (`reason`) se guarda en `new`. *(Hito 1)*
  2. Actualiza `old` con `valid_to = t`, `status = superseded` y `superseded_by_id = new`.
  3. Actualiza los mismos campos en `vec_N` y añade `new` a FTS y a `vec_N`.
- **Solo se reemplazan `fact`.** Los `episode` («el 3 de mayo decidimos X») son registros de algo que pasó y no dejan de ser verdad. Por eso un `episode` tampoco puede tener `valid_from` en el futuro. *(Hito 1)*
- **«Vigente en t»** significa `valid_from <= t < valid_to`, igual para hechos y episodios. `status` solo indica si la memoria tiene sucesora; la búsqueda filtra por fechas. Así, un hecho con `valid_from` futuro («desde enero el responsable será Luis») no aparece hasta que empieza. *(Hito 1)*
- **Reemplazar algo ya reemplazado** devuelve un error con el id de su sucesor actual, para que el agente reintente sobre la versión vigente. Evita bifurcar la cadena.
- **Olvidar** (`forget(id)`) borra **toda la cadena** del hecho (todas sus versiones) de `memories`, FTS y vector, y devuelve los ids borrados. Si alguien pide olvidar dónde vive, también debe desaparecer dónde vivía.
  - *(Hito 1)* Para que desaparezca también del disco:
    - `secure_delete=ON` sobrescribe las filas borradas.
    - Un `optimize` del índice FTS: FTS5 registra los borrados como marcas que solo aplica al fusionar segmentos, así que sin esto los términos sobreviven en segmentos antiguos.
    - Un checkpoint del WAL.
  - Un test lo verifica buscando el texto en los bytes del archivo, con control previo de que estaba ahí.
  - Coste: `optimize` reescribe el índice entero. Se medirá en el Hito 4.
- **Tiempo de registro y tiempo de validez son distintos.** `created_at` dice cuándo lo supimos; `valid_from`/`valid_to` dicen cuándo fue verdad. `as_of` consulta el tiempo de validez. Ejemplo: «me mudé a Lisboa en marzo», dicho en mayo, tiene `valid_from = marzo` y `created_at = mayo`.
- **Aislamiento.** El workspace lo fija la configuración del servidor (`VIGENTE_WORKSPACE`). No es un parámetro de las herramientas MCP. Todas las consultas de `store.py` lo exigen como argumento obligatorio.

## 6. Recuperación

1. **Saneamiento de la consulta FTS.** El texto del usuario no va directo a `MATCH`, porque comillas, `*`, `NEAR` o `-` romperían la sintaxis o cambiarían su significado. Se tokeniza y cada token se cita: `"token1" OR "token2"`. Se quitan palabras vacías (lista mínima en español e inglés) salvo que no quede ninguna otra. El ranking lo da BM25.
2. **FTS:** `memories_fts` unida a `memories` con los filtros de workspace, vigencia o `as_of`, y `kind`. Se piden `k × 4` candidatos.
3. **Vector:** se calcula el embedding de la consulta y se hace KNN en el `vec_N` activo con los mismos filtros dentro del KNN. También `k × 4` candidatos.
4. **Fusión RRF:** `score(d) = Σ 1 / (60 + rank_i(d))`. Se ordena y se corta en `k`.
5. **Modos:** sin `Embedder` configurado, el modo es `fts_only` y se informa en la respuesta. Nada falla.
6. **`include_history=true`** quita el filtro de vigencia y marca cada resultado con su `status`.

Cada resultado devuelve `id`, `content`, `kind`, `valid_from`, `valid_to`, `status`, `score` y `matched_by` (`["fts"]`, `["vector"]` o ambos).

## 7. Contratos MCP

Las fechas de entrada y salida van en ISO 8601. Los errores son estructurados (`code`, `message` y, si aplica, `current_id`).

| Herramienta | Entrada | Salida |
|---|---|---|
| `remember` | `content`, `kind="fact"`, `tags=[]`, `valid_from?`, `confidence?` | `memory`, `possible_supersedes: [{memory, score, matched_by}]` |
| `recall` | `query`, `k=5` (máx. 50), `as_of?`, `include_history=false`, `kind?` | `mode`, `results: [...]` (§6) |
| `supersede` | `old_id`, `new_content`, `valid_from?`, `reason?` | `new_id`, `old_id`, `old_valid_to` |
| `forget` | `id` | `deleted_ids: [...]` |
| `history` | `id` | cadena ordenada de la versión más antigua a la más nueva |
| `list_memories` | `kind?`, `status?`, `tag?`, `limit=20`, `cursor?` | `items`, `next_cursor` |

**Cómo se detectan los «posibles reemplazos» en `remember`:**

- Con embeddings: hechos vigentes del mismo workspace con similitud coseno ≥ umbral. El umbral es configurable y su valor por defecto se calibra en el Hito 4.
- Sin embeddings: top-3 por BM25. Es una señal más débil y se documenta como limitación. *(Hito 1)* Ejemplo real: «Migramos a SQLite para el MVP» **no** propone «El proyecto usa PostgreSQL», porque no comparten palabras. Es justo el caso que deben resolver los embeddings del Hito 2. Con BM25, `score` no es comparable entre consultas.

Las descripciones de las herramientas indican cuándo usarlas, por ejemplo: «si `remember` devuelve un posible reemplazo que contradice lo nuevo, llama a `supersede` en vez de dejar ambos». También avisan de que el contenido devuelto son **datos guardados**, no instrucciones.

## 8. Plan de evaluación

**Experimento central:** memoria ingenua (todo queda `active`) frente a memoria con reemplazos, con la misma recuperación. La tesis se sostiene si la tasa de resultados obsoletos baja y Recall@5 no empeora.

### E1. Sintético (en el repo, semilla fija, en español)

- **Datos:** entidades con atributos que cambian 1–4 veces (ciudad de una persona, base de datos de un proyecto, responsable de un módulo, preferencias), más distractores.
- **Respuesta correcta conocida:** para cada atributo se conoce su cadena de versiones.
- **Ingesta oráculo:** se usan los reemplazos verdaderos. Así se evalúa el mecanismo aislado de las decisiones del agente.
- **Preguntas:** sobre el estado actual y con `as_of` en fechas pasadas.
- **Métricas:** Recall@5, MRR, tasa de obsoletos en top-1 y top-5.

### E2. LongMemEval-cleaned (licencia MIT; se descarga con un script, no se incluye en el repo)

- **Formato confirmado:** cada pregunta trae `question_type`, las sesiones del historial con fechas (`haystack_dates`), `answer_session_ids` y `has_answer` en los turnos que contienen la evidencia. Las preguntas `_abs` (abstención) se excluyen de la evaluación de recuperación, como en el propio benchmark.
- **E2a, recuperación:** con `longmemeval_s_cleaned`, cada turno del usuario se ingiere como `episode` con su fecha. Se mide Recall@5/10 por sesión y por turno, y MRR, comparando FTS, vector e híbrido.
- **E2b, actualización de conocimiento (opcional, se decide en el Hito 4):** con las preguntas `knowledge-update` del archivo *oracle* y `qwen3:8b` en Ollama como agente simulado. El agente procesa las sesiones en orden y decide `remember` o `supersede`, y se mide la tasa de obsoletos de extremo a extremo. Es la única forma honesta de probar en LongMemEval la parte que depende del agente. Hay que verificar antes cómo distinguir la evidencia vieja de la nueva en esa categoría.

### Común a ambos

- **Latencia:** p50 y p95 de `recall` y de escritura, y tiempo de indexación, con el hardware y las versiones de los modelos anotados.
- **Salida:** tabla en Markdown y un gráfico, generados por `vigente eval`. Los resultados se publican tal como salgan.

## 9. Decisiones (aprobadas el 18-11-2026)

1. **`forget` borra la cadena completa**, no solo una versión.
2. **Los `episode` son inmutables** y no se pueden reemplazar.
3. **E2b (agente simulado con qwen3:8b)** queda como opcional para el Hito 4.
4. **Modelo de embeddings para el Hito 2:** candidatos multilingües en Ollama: `qwen3-embedding:0.6b` y `embeddinggemma`. Son unos 600 MB de descarga, así que te preguntaré antes de bajarlo. Se elige con una mini-comparación sobre E1.
5. **Nombre «vigente».**
6. **Git:** propongo `git init` al empezar el Hito 1 y un commit por hito, con tu autorización en cada uno.

## 10. Riesgos

| Riesgo | Mitigación |
|---|---|
| sqlite-vec es pre-1.0 (v0.1.9) | Versión fijada; tests de integración contra la extensión real |
| Filas fantasma tras un insert fallido | Transacción explícita en toda escritura; validación con Pydantic antes de llegar a SQL; `vigente doctor` |
| `NULL` no permitido en metadatos | Centinela 31-12-9999, convertido solo en `store.py` |
| Sintaxis FTS5 en consultas del usuario | Tokenizar y citar cada token |
| Mezcla de español e inglés | `unicode61` sin stemming; modelo de embeddings multilingüe |
| El agente puede no llamar a `supersede` | Descripciones claras de las herramientas; se mide en E2b; se documenta como limitación |
| KNN de sqlite-vec es exacto (fuerza bruta) | Suficiente para la escala objetivo; se mide en el Hito 4 sin suponer cifras |
| CLI y servidor MCP sobre el mismo archivo | WAL + `busy_timeout` |
