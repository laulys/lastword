# Demo con un agente real

Generado por `eval/agent_demo.py`. Cuatro sesiones **independientes** de Claude Code (cada una es un proceso nuevo, sin memoria de las anteriores) que solo comparten Vigente. El agente no tiene más herramientas que las de Vigente.

- Fecha: 2026-11-18
- 2.1.236 (Claude Code), modelo `claude-fable-5`
- Embeddings: `embeddinggemma` en Ollama

## Sesión 1

> Para que lo recuerdes en futuras conversaciones: el proyecto usa PostgreSQL 15 como base de datos, y la reunión diaria del equipo es a las 9:30.

Herramientas usadas:

- `recall` `{"query": "base de datos del proyecto PostgreSQL"}`
- `recall` `{"query": "hora de la reunión diaria del equipo"}`
- `remember` `{"content": "El proyecto usa PostgreSQL 15 como base de datos.", "kind": "fact", "tags": ["proyecto", "base-de-datos"]}`
- `remember` `{"content": "La reunión diaria del equipo es a las 9:30.", "kind": "fact", "tags": ["equipo", "reuniones"]}`

Respuesta (5 turnos):

> Listo, he guardado los dos datos como hechos en mi memoria persistente:
>
> - **El proyecto usa PostgreSQL 15 como base de datos** (etiquetas: proyecto, base-de-datos).
> - **La reunión diaria del equipo es a las 9:30** (etiquetas: equipo, reuniones).
>
> Antes de guardarlos comprobé que no hubiera versiones anteriores de estos datos, y no había ninguna. Si en el futuro cambia alguno (por ejemplo, migráis a otra versión de PostgreSQL o movéis la daily de hora), dímelo y actualizaré el hecho conservando el historial en lugar de duplicarlo.

## Sesión 2

> Novedad: migramos a SQLite para el MVP.

Herramientas usadas:

- `recall` `{"query": "base de datos del MVP del proyecto"}`
- `supersede` `{"old_id": "01M7QDAAYHWZQTH0415G2ACA9R", "new_content": "El proyecto usa SQLite como base de datos para el MVP.", "reason": "Migración a SQLite para el MVP (18 nov 2026)."}`

Respuesta (3 turnos):

> Anotado: actualicé la memoria para registrar que el proyecto ahora usa **SQLite** como base de datos para el MVP. El dato anterior (PostgreSQL 15) quedó en el historial como reemplazado, así que si alguna vez necesitas saber qué era verdad antes de hoy, sigue consultable.

## Sesión 3

> ¿Qué base de datos usamos en el proyecto y a qué hora es el daily?

Herramientas usadas:

- `recall` `{"query": "base de datos del proyecto"}`
- `recall` `{"query": "hora del daily standup"}`

Respuesta (3 turnos):

> Según lo que tengo guardado:
>
> - **Base de datos:** SQLite para el MVP (este dato reemplazó una versión anterior por la migración decidida hoy, 18 de noviembre de 2026).
> - **Daily:** la reunión diaria del equipo es a las **9:30**.

## Sesión 4

> Apunta esto: desde la semana que viene el daily pasa a las 10.

Herramientas usadas:

- `remember` `{"content": "El daily del equipo es a las 10:00 (cambio efectivo desde el lunes 23 de noviembre de 2026).", "kind": "fact", "tags": ["equipo", "reuniones", "daily"], "valid_from": "2026-11-23"}`
- `supersede` `{"old_id": "01M7QDABNBVTN87YG6FA8J126Z", "new_id": "01M7QDBMGCMR7NMCW5X2Q7B2EG", "reason": "El equipo movió el daily de las 9:30 a las 10:00 a partir del 23 de noviembre de 2026."}`

Respuesta (3 turnos):

> Apuntado: el daily pasa a las 10:00 a partir del lunes 23 de noviembre de 2026. Tenía registrado que era a las 9:30, así que marqué esa versión como reemplazada — queda en el historial, pero a partir de esa fecha la hora vigente es las 10:00.

## Estado final de la memoria

| Contenido | Vigente desde | Hasta | Reemplaza a |
|---|---|---|---|
| El proyecto usa PostgreSQL 15 como base de datos. | 2026-11-18 13:25 | 2026-11-18 13:25 |  |
| La reunión diaria del equipo es a las 9:30. | 2026-11-18 13:25 | 2026-11-23 00:00 |  |
| El proyecto usa SQLite como base de datos para el MVP. | 2026-11-18 13:25 | sin fin | El proyecto usa PostgreSQL 15 como base de datos. |
| El daily del equipo es a las 10:00 (cambio efectivo desde el lunes 23 de noviembre de 2026). | 2026-11-23 00:00 | sin fin | La reunión diaria del equipo es a las 9:30. |

Búsqueda «¿A qué hora es el daily?» (resultado principal):

- Hoy: La reunión diaria del equipo es a las 9:30.
- Dentro de 7 días: El daily del equipo es a las 10:00 (cambio efectivo desde el lunes 23 de noviembre de 2026).

`vigente doctor`: sin problemas
