# Changelog

What changed in each version of Vigente. Each one has its [release notes](https://github.com/laulys/vigente/releases), with measurements and upgrade instructions.

## 1.3.1 · 2026-11-18

Documentation only; the code does not change ([pull request](https://github.com/laulys/vigente/pull/10)).

- The README presents memory security next to supersession: why it matters (OWASP ASI06) and what Vigente does on save, plus a new security and privacy section.
- The README lists what is new since 1.0, with the version of each change, and every CLI command.
- This changelog.

## 1.3.0 · 2026-11-18

Fixes from a full audit of the code ([pull request](https://github.com/laulys/vigente/pull/9)).

**Added**

- `vigente-mcp --http --allowed-host <name>`: keeps the server on localhost behind an HTTPS proxy.
- Another fact can be linked later to a version that already replaced one: `supersede(old_id, new_id)`.
- `forget` returns `warnings`, and says so when another connection keeps the deleted text in the `-wal` file.

**Fixed**

- A date typed as a bare number, such as `2024`, was stored as a date in 1970. Dates must now be ISO 8601.
- Behind a system proxy, memory text left the computer even with a local embedding service.
- `reindex` could attach a forgotten memory's vector to another memory.
- The HTTP server answered 421 behind a proxy when it listened on localhost.
- Search could lose memories after changing the embedding model or its dimension, and `remember` did not propose replacing a fact saved without a vector.
- Secrets written as environment variables, keys split by an invisible character, and orders with extra spaces were not detected.
- The memory file was readable by other users of the computer. It is now created for its owner only.
- `history` and `forget` were slow on a fact with thousands of versions, and a fact with more than 10,000 versions could not be read or forgotten.
- A null vector, a busy database or a file that cannot be opened ended in a traceback.

**Changed**

- Database schema v4 → v6 (two indexes). Databases migrate on first open; older versions cannot open them afterwards.
- The scale benchmark now saves facts, not episodes. Saving a fact costs about as much as a search, more than the figure published for 1.2.

## 1.2.1 · 2026-11-18

**Fixed**

- `vigente-mcp --help` and its startup errors follow the language too.

## 1.2.0 · 2026-11-18

**Added**

- One new fact can replace several: `also_old_ids` in MCP, `--also` in the CLI ([ADR-0008](docs/decisions/0008-un-hecho-reemplaza-a-varios.md)).
- `vigente-mcp --http` shares one memory across computers, with a required token ([ADR-0009](docs/decisions/0009-compartir-por-http.md)).
- The CLI speaks English or Spanish, following `VIGENTE_LANG` or the system language.
- E4, an evaluation of searches that hinge on exact codes.

**Changed**

- Two-stage vector search: 1-bit vectors pick the candidates and exact vectors re-rank them, about 9 times faster with 50,000 memories ([ADR-0010](docs/decisions/0010-busqueda-en-dos-pasos.md)). Database schema v2 → v4.

## 1.1.0 · 2026-11-18

**Added**

- Secrets are rejected on save, and so is hidden Unicode that can smuggle instructions ([ADR-0007](docs/decisions/0007-seguridad-de-la-memoria.md)).
- Text that reads like an order to the agent is flagged: every memory carries `flags`, `recall` warns, and `vigente review` lists them.
- E3, an evaluation of the security filters.

## 1.0.1 · 2026-11-18

**Fixed**

- Several servers can create the same database at the same time.
- The essential MCP instructions fit in the first 512 characters, which some clients prioritize.

## 1.0.0 · 2026-11-18

First release: a local MCP server and CLI where facts are superseded, never overwritten, with validity dates and a single SQLite file.
