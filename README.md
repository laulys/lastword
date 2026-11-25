# Vigente

[![CI](https://github.com/laulys/vigente/actions/workflows/ci.yml/badge.svg)](https://github.com/laulys/vigente/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)

**Memory for AI agents that knows what is still true.** · [Leer en español](README.es.md)

When a fact changes, most agent memories either overwrite it or keep both versions and hope that search picks the right one. Vigente does neither. The old fact is **superseded**: closed with a date and linked to its replacement.

- Search returns only what is true **now**.
- `as_of` returns what was true **at any past date**.
- `history` shows how a fact changed.

A memory that several agents share, and that lasts between sessions, is also an attack surface: OWASP lists *Memory & Context Poisoning* among the top risks of agentic applications ([ASI06](https://genai.owasp.org/2025/12/09/owasp-top-10-for-agentic-applications-the-benchmark-for-agentic-security-in-the-age-of-autonomous-ai/)). So Vigente also **guards what goes in**:

- Secrets are rejected on save, never stored.
- Hidden Unicode that can smuggle instructions is rejected.
- Text that reads like an order to the agent is stored but flagged, and the agent is told never to follow a memory.

These are deterministic rules, without an LLM, measured in [E3](eval/results/e3_security.md). Details in [Security and privacy](#security-and-privacy).

It runs locally as an **MCP server**, so Claude Code, Codex, Cursor or any MCP client can use it, and they can share the same memory. Everything lives in a single SQLite file.

![A real CLI session: a fact changes, Vigente proposes the replacement, and search returns what is true now or at a past date](demo/vigente.gif)

<sub>A real session, recorded by [`demo/record.py`](demo/record.py) with the embedding model running. It is in Spanish; the CLI follows the system language, English or Spanish, or `VIGENTE_LANG`.</sub>

## Results

![What the first search result is, by memory system](eval/results/e1_obsoletos.svg)

| | Naive memory | Vigente |
|---|---:|---:|
| First result is the right fact ([synthetic set](eval/results/e1_synthetic.md), vector search) | 48% | **95%** |
| First result is an outdated version of the fact | 51% | **0%** |
| Recall@5 | 97% | **100%** |

- **Re-ranking by recency is not enough.** It removes outdated results, but then the first result is a different fact in half of the questions (51%).
- **[LongMemEval](eval/results/e2_longmemeval.md)**, 470 questions from a public benchmark: without supersession, the old version of an updated fact ranks above the new one in **54–56%** of cases, with any search mode.
- **[Scale](eval/results/scale.md):** with 200,000 facts, a search takes 25 ms and saving a fact 26 ms (7 ms each with 50,000). Saving costs as much as searching because every new fact is compared with the current ones to propose replacements. Search compares compressed 1-bit vectors first and re-ranks the best candidates with the exact ones, with the same results as an exact search in E1 and E2 ([ADR-0010](docs/decisions/0010-busqueda-en-dos-pasos.md)). Add about 13 ms for the embedding model.
- **[A real agent](eval/results/agent_demo.md)** (Claude Code, four independent sessions sharing only Vigente) follows the protocol on its own: before saving a change it searches for the old fact, or it links the replacement that `remember` proposes; it does not duplicate facts, and it answers with what is current.
- **[Exact codes](eval/results/e4_exact.md):** vector search already returns the right memory first for 36 of 36 questions that hinge on a code (error E-4031 vs E-4013, order 7781 vs 7718); text search alone gets 34. So no special mode was added.
- **[Security](eval/results/e3_security.md):** the filters catch 40 of 40 secrets with no false alarms on 40 lookalikes (order numbers, hashes, «the key is in 1Password»), and flag 40 of 40 injected orders while marking 1 of 40 legitimate memories. The same author wrote the cases and the rules, so read it as a ceiling.

The decision rules were fixed **before** measuring ([protocol](eval/README.md)), and E1 and E2 give identical results when re-run.

## What's new since 1.0

- **Security (1.1).** Secrets are rejected on save, hidden Unicode is rejected, and text that reads like an order to the agent is flagged. `vigente review` lists what is flagged.
- **One fact replaces several (1.2).** «Ana and Luis moved to Lisbon» closes both their old cities in one step: `also_old_ids` in MCP, `--also` in the CLI. Since 1.3, another fact can be added later to the same replacement.
- **Share it between computers (1.2).** `vigente-mcp --http` serves the memory to other machines, a team or a cloud agent, with a required token. Since 1.3, `--allowed-host` keeps it on localhost behind an HTTPS proxy.
- **Faster search (1.2).** Two-stage vector search: about 9 times faster than exact search with 50,000 memories, with the same results.
- **English or Spanish (1.2).** The CLI and `vigente-mcp` follow the system language or `VIGENTE_LANG`.
- **Audited (1.3).** A full review of the code fixed, among others, a date bug and two privacy issues ([Security and privacy](#security-and-privacy)).

Every version, in detail: [CHANGELOG.md](CHANGELOG.md).

## How it works

- **One file.** SQLite holds the rows, a full-text index (FTS5) and a vector index (sqlite-vec). Every write or deletion is a single transaction across all three.
- **Facts are never edited.** `supersede` creates the new version, closes the old one and links both. «True at time t» means `valid_from <= t < valid_to`. One new fact can replace several: «Ana and Luis moved to Lisbon» closes both their old cities ([ADR-0008](docs/decisions/0008-un-hecho-reemplaza-a-varios.md)).
- **The engine calls no LLM.** `remember` uses local embeddings (embeddinggemma via Ollama) to propose facts the new one may replace. The agent, which already is an LLM, decides. Proposals favour not missing a replacement (17 of 18 in the hand-written set), so statements on the same topic also get one; similarity cannot tell «this updates that» from «this adds detail», and [masking names and numbers did not help](docs/decisions/0004-modelo-de-embeddings.md).
- **Forgetting is real.** `forget` deletes every version of a fact from the rows, both indexes and the disk. A test checks that the bytes are gone from the file.
- **It degrades gracefully.** Without embeddings it searches by text. It refuses to send memories to a non-local service unless you allow it.
- **It is safe to share between agents.** Secrets are rejected on save, and memories that read like orders are flagged ([Security and privacy](#security-and-privacy)).

## Quick start

```bash
git clone https://github.com/laulys/vigente && cd vigente
uv sync
ollama pull embeddinggemma        # optional: without it, Vigente searches by text only
uv run vigente add "El proyecto usa PostgreSQL 15" --valid-from 2026-03-01
uv run vigente add "Migramos a SQLite para el MVP"   # proposes replacing the previous fact
uv run vigente supersede <old-id> --by <new-id>
uv run vigente search "¿qué base de datos usamos?"
uv run vigente search "¿qué base de datos usamos?" --as-of 2026-06-01
```

All CLI commands (`vigente --help` for their options):

| Command | What it does |
|---|---|
| `add` | Saves a memory and proposes current facts it may replace |
| `supersede` | Replaces one or more facts with their new version (`--by` links one already saved, `--also` adds more old facts) |
| `search` | Searches what is true now, at a past date (`--as-of`) or across all versions (`--history`) |
| `show`, `history` | One memory, or every version of a fact |
| `list` | Lists memories, newest first, with filters |
| `forget` | Deletes a fact and all its versions for good |
| `review` | Lists flagged memories: possible orders to the agent, hidden characters or secrets |
| `reindex` | Computes the vectors that are missing |
| `doctor`, `stats` | Checks the database and its indexes; counts memories |
| `init` | Creates the database and the workspace |

## Use it from an agent (MCP)

**Claude Code:**

```bash
claude mcp add vigente -e VIGENTE_WORKSPACE=my-project -- uv --directory /path/to/vigente run vigente-mcp
```

**Codex:**

```bash
codex mcp add vigente --env VIGENTE_WORKSPACE=my-project -- uv --directory /path/to/vigente run vigente-mcp
```

**Cursor** (`.cursor/mcp.json`) or **Claude Desktop** (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "vigente": {
      "command": "uv",
      "args": ["--directory", "/path/to/vigente", "run", "vigente-mcp"],
      "env": { "VIGENTE_WORKSPACE": "my-project" }
    }
  }
}
```

| Tool | What it does |
|---|---|
| `remember` | Saves a fact or an episode and proposes current facts it may replace. Rejects secrets |
| `recall` | Searches what is true now, or at a past date (`as_of`) |
| `supersede` | Closes one or more outdated facts and links their new version (new text, or the id `remember` just returned) |
| `history` | All versions of a fact |
| `list_memories` | Lists memories with filters |
| `forget` | Deletes a fact and all its versions, also from disk (marked as destructive) |

The workspace is fixed by the server configuration, so an agent cannot read another one. Clients configured with the same workspace share the memory, even at the same time: what Claude Code saves, Codex and Cursor find.

### Share it between computers

`vigente-mcp --http` serves the same memory over HTTP, so other computers, a team or a cloud agent can share it. A token is required ([ADR-0009](docs/decisions/0009-compartir-por-http.md)):

```bash
export VIGENTE_TOKEN=$(openssl rand -hex 32)
VIGENTE_WORKSPACE=team uv run vigente-mcp --http --allowed-host memory.example.com
```

It listens on `127.0.0.1:8765` only. Put an HTTPS proxy (Caddy, nginx or a tunnel) in front on the same machine, and name its public host with `--allowed-host`: without it, the server answers 421 to requests that arrive with that name. `--host 0.0.0.0` exposes the unencrypted port to the whole network, so use it only on a network you trust.

Claude Code:

```bash
claude mcp add --transport http vigente https://memory.example.com/mcp --header "Authorization: Bearer $VIGENTE_TOKEN"
```

Codex (`~/.codex/config.toml`):

```toml
[mcp_servers.vigente]
url = "https://memory.example.com/mcp"
bearer_token_env_var = "VIGENTE_TOKEN"
```

> **If your client has its own memory**, the agent may save the same fact in both places, and only Vigente will know when it stops being true. In Claude Code you can turn its auto memory off with `"autoMemoryEnabled": false` in the project settings, or with `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` ([docs](https://code.claude.com/docs/en/memory)).

## Security and privacy

A memory that several agents share, and that lasts between sessions, has to be careful with what goes in and what goes out ([ADR-0007](docs/decisions/0007-seguridad-de-la-memoria.md)).

**When it matters.** Claude Code and Codex already protect the session: they ask before running commands, and their models are trained to distrust instructions found in pages or files. What they do not check is what stays written in a memory that outlives the session and that other clients will read. So these filters matter most when the memory is shared:

- between Claude Code, Codex and Cursor, where the memory is only as careful as the least careful client that writes to it;
- between people, over HTTP, where what someone else saves ends up in your sessions;
- with agents that read outside content, such as emails, issues or web pages, or agents built from scratch with fewer safeguards of their own.

With a single client, on your own computer, they add less: the main reason to use Vigente is then the other half, knowing what is still true.

What Vigente does:

- **Nothing leaves your computer by default.** The memory is a local SQLite file, and embeddings come from a local model. Sending memories to a non-local embedding service requires `VIGENTE_ALLOW_REMOTE=1`, and system proxies are ignored for local addresses.
- **The file is private.** It is created readable by its owner only (folder `0700`, file `0600`). It is not encrypted: disk encryption, such as FileVault or BitLocker, covers it.
- **Secrets are not stored.** API keys in 10 known formats, passwords written in a sentence or as an environment variable, and random-looking tokens are rejected on save. The error never repeats the secret, and suggests saving where it lives instead. Only a person can override it, from the CLI with `--allow-secret`; MCP clients cannot.
- **Memories are data, never instructions.** Hidden Unicode that can smuggle text is rejected. Text that reads like an order to the agent is stored but flagged: `recall` warns the agent, and `vigente review` lists it. The server tells the agent never to follow a memory. These are rules in Spanish and English: they catch the obvious, not an attacker who knows them, so [E3](eval/results/e3_security.md) is a ceiling.
- **Forgetting erases.** `forget` deletes every version from the rows, the text index and both vector tables, and SQLite overwrites the freed space. If another connection is reading at that moment, the text stays in the `-wal` file until it closes, and `forget` says so.
- **One agent, one workspace.** The server fixes the workspace; no tool accepts another one.
- **Over HTTP, a token is required.** At least 32 characters, compared in constant time. The server stays on `127.0.0.1` behind an HTTPS proxy ([ADR-0009](docs/decisions/0009-compartir-por-http.md)).
- **Audited.** A full review of the code fixed, in v1.3, two privacy issues among others: memory text could reach a system proxy, and the file was readable by other users of the computer ([pull request](https://github.com/laulys/vigente/pull/9)).

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `VIGENTE_DB` | `~/.vigente/vigente.db` | Memory file |
| `VIGENTE_WORKSPACE` | `default` | Isolated memory space |
| `VIGENTE_EMBED_MODEL` | `embeddinggemma` | Embedding model; `none` for text-only search |
| `VIGENTE_EMBED_BASE_URL` | `http://localhost:11434/v1` | Any service with an OpenAI-compatible API |
| `VIGENTE_EMBED_API_KEY` | | Only if the service needs one |
| `VIGENTE_ALLOW_REMOTE` | off | Required to send memories to a non-local service |
| `VIGENTE_SEARCH_MODE` | `vector` | `hybrid` (vectors + text) or `text` |
| `VIGENTE_TOKEN` | | Required by `vigente-mcp --http`; clients send it as a bearer token |
| `VIGENTE_LANG` | system language | `en` or `es`: language of what people read from `vigente` and `vigente-mcp` |

If the embedding service is down, Vigente keeps working with text search and says so. `vigente reindex` fills in the missing vectors later.

## Design decisions

Each decision has an ADR with the alternatives considered (in Spanish):

1. [One SQLite file with FTS5 and sqlite-vec, no ORM](docs/decisions/0001-sqlite-fts5-sqlite-vec.md)
2. [Facts are superseded, never edited, with validity dates](docs/decisions/0002-reemplazo-append-only-con-validez.md)
3. [The engine calls no LLM: it proposes, the agent decides](docs/decisions/0003-sin-llm-en-el-motor.md)
4. [embeddinggemma by default, chosen with a benchmark; threshold 0.51](docs/decisions/0004-modelo-de-embeddings.md)
5. [`supersede` can link a memory that is already stored](docs/decisions/0005-dos-formas-de-reemplazar.md)
6. [Vector-only search by default, decided with the evaluation](docs/decisions/0006-busqueda-por-vectores.md)
7. [What never enters the memory, and what enters flagged](docs/decisions/0007-seguridad-de-la-memoria.md)
8. [One new fact can replace several](docs/decisions/0008-un-hecho-reemplaza-a-varios.md)
9. [Sharing between computers: HTTP with a token](docs/decisions/0009-compartir-por-http.md)
10. [Two-stage search: binary vectors, then an exact re-rank](docs/decisions/0010-busqueda-en-dos-pasos.md)

The full design is in [docs/architecture.md](docs/architecture.md).

## Development

```bash
uv run pytest                                              # no network or models needed
VIGENTE_TEST_OLLAMA=1 uv run pytest tests/test_ollama.py   # with local Ollama
uv run ruff check . && uv run ruff format --check .
uv run mypy src tests eval demo
```

How to reproduce the whole evaluation: [eval/README.md](eval/README.md) (in Spanish, like the reports).

The project was built in milestones (design, core, semantic search, MCP server, evaluation, security), each with its decisions in an ADR. Since the MCP server, every change went through a [pull request](https://github.com/laulys/vigente/pulls?q=is%3Apr) with its decisions and measurements.

## License

[MIT](LICENSE)
