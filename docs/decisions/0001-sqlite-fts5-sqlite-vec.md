# ADR-0001: Un solo archivo SQLite con FTS5 y sqlite-vec, sin ORM

- Estado: aceptado
- Fecha: 18-11-2026

## Contexto

Necesitamos filas, búsqueda de texto y búsqueda vectorial. Además, «olvidar» tiene que ser un borrado real en los tres sitios, y el filtro por workspace y por vigencia debe aplicarse dentro de la búsqueda vectorial, no después. El proyecto es local-first y no debe exigir servidores.

## Decisión

- SQLite con FTS5 para texto y sqlite-vec (v0.1.9, versión fijada) para vectores, en el mismo archivo.
- Acceso con `sqlite3` de la biblioteca estándar y SQL escrito a mano en `store.py`.
- Migraciones con `PRAGMA user_version`.
- Toda escritura en una transacción explícita (`BEGIN IMMEDIATE … COMMIT/ROLLBACK`).

## Alternativas consideradas

- **Chroma o LanceDB embebidos.** Son maduros para vectores, pero las filas y el texto vivirían en otro almacenamiento con su propia persistencia. Perderíamos la transacción única que garantiza que borrar es borrar en todas partes.
- **Qdrant o PostgreSQL + pgvector.** Exigen un servidor y contradicen el objetivo de un solo archivo local. Quedan como posibles backends futuros, fuera de v1.
- **SQLAlchemy + Alembic.** Las partes importantes (`MATCH` de FTS5, KNN de vec0, tablas virtuales) se escribirían igualmente en SQL crudo. Para unas pocas migraciones, Alembic añade más maquinaria de la que ahorra.

## Consecuencias

- ✅ Borrado y reemplazo atómicos sobre filas, texto y vectores.
- ✅ Filtro por workspace y vigencia dentro del KNN (verificado: partition key + columnas de metadatos).
- ✅ Cero infraestructura: el usuario instala un paquete y tiene un archivo.
- ⚠️ sqlite-vec es pre-1.0. Mitigación: versión fijada y tests de integración contra la extensión real.
- ⚠️ Verificado: un insert que falla sin transacción deja una fila fantasma en vec0. La transacción explícita es obligatoria, no opcional. `vigente doctor` comprueba la consistencia.
- ⚠️ Las columnas de metadatos no aceptan `NULL`: «vigente» se representa con el centinela 31-12-9999.
- ⚠️ El KNN de sqlite-vec es exacto (fuerza bruta). Su coste se mide en el Hito 4 en vez de suponerlo.
