# Changelog

What changed in each version of Lastword, called Vigente until 1.4. Each one has its [release notes](https://github.com/laulys/lastword/releases), with measurements and upgrade instructions.

## 2.0.0 · 2026-11-25

A new name ([pull request](https://github.com/laulys/lastword/pull/13)). The major version is for the Python package, which is imported with another name; memories and databases do not change.

**Changed**

- The project is now called **Lastword**. The package, the commands (`lastword`, `lastword-mcp`), the variables (`LASTWORD_*`) and the default file (`~/.lastword/lastword.db`) take the new name.
- What is already set up keeps working: the commands `vigente` and `vigente-mcp`, the `VIGENTE_*` variables, and the memory in `~/.vigente`, which is used where it is when there is no new one. If both names are set, the new one wins.
- To upgrade an installed Vigente, uninstall it first: `uv tool uninstall vigente`, then install. The other way round, uninstalling would remove the `vigente` commands that Lastword also provides.
- The Python package is imported as `lastword`. `import vigente` no longer works.
- The demo is in English.

## 1.4.0 · 2026-11-25

Who may change a fact, and the fixes from a security review ([pull request](https://github.com/laulys/lastword/pull/12)).

**Added**

- Who may change a fact ([ADR-0011](docs/decisions/0011-quien-puede-cambiar-un-hecho.md)). Each memory keeps its origin (a person, an agent or outside), and a less trusted origin cannot close or delete a more trusted fact: the change waits for a person. New commands: `pending`, `accept`, `reject` and `confirm`. New MCP field: `from_outside`.
- E5 measures it, with the rule fixed beforehand, after reproducing the attack: in 1.3.1, 12 of 12 false facts replaced true ones without a warning.
- The README says when the security filters matter most.
- `history` over MCP returns the latest 100 versions and says how many it omits.

**Fixed**

From a security review ([details](docs/decisions/0011-quien-puede-cambiar-un-hecho.md#vías-que-encontró-una-revisión-antes-de-publicar)). The bypasses of ADR-0011 were found and fixed before release; these also affected 1.3.1:

- Text with terminal control codes could make the CLI show something other than what was stored. The Escape character is rejected on save, and the CLI never prints control codes raw.
- A crafted 4,000-character memory took seconds on every read and blocked the server.
- Text hidden in variation selectors, and common ways to write a password (JSON, Markdown, camelCase), got past the filters.
- The default embedding address was `localhost`, which may resolve to `::1`, where another local user could listen. It is now `127.0.0.1`.
- The body of an embedding service's error, and a password in its address, reached the agent. Malformed replies stopped a memory from being saved.
- In a narrow terminal, the CLI cropped text without saying so.
- A date written as `.5` or `1.7e9` was stored as 1970 or 2023.
- Databases created before 1.3 stayed readable by other users: they are made private on upgrade, and `doctor` reports it.
- `forget` shows every version before asking.

**Changed**

- Database schema v6 → v7. What was saved from the CLI becomes a person's. Databases migrate on first open; older versions cannot open them afterwards.
- Over MCP, `supersede` on a person's fact returns `new: null` and a warning, and `forget` is refused.

## 1.3.1 · 2026-11-18

Documentation only; the code does not change ([pull request](https://github.com/laulys/lastword/pull/10)).

- The README presents memory security next to supersession: why it matters (OWASP ASI06) and what Vigente does on save, plus a new security and privacy section.
- The README lists what is new since 1.0, with the version of each change, and every CLI command.
- This changelog.

## 1.3.0 · 2026-11-18

Fixes from a full audit of the code ([pull request](https://github.com/laulys/lastword/pull/9)).

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
