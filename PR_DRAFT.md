# PR Draft — feat: add direct backend

**Title:** `feat: add direct backend — read local .gpkg files without Gramps Web`

---

## What this does

First off — great project. The handler architecture is clean, the GQL
integration is thoughtful, and the MCP tool design maps nicely onto what
an AI assistant actually needs from a genealogy database. It was a pleasure
to build on top of.

This PR adds a **direct backend** that reads `.gpkg` / `.gramps` XML files
from disk using only Python's standard library — no Gramps Web server, no
Docker required.

**Why I needed this:** I wanted a lighter setup. Standing up a Gramps Web
stack works fine, but for read-only AI-assisted research it felt like a lot
of infrastructure. A local `.gpkg` export covers the use case with zero
moving parts.

The direct backend is intentionally **read-only** — creating or updating
records still requires a Gramps Web server.

### How it works

| | Direct backend | Web backend |
|---|---|---|
| Reads | `.gpkg` / `.gramps` from disk | Gramps Web API |
| Write operations | ❌ read-only | ✓ |
| Ancestors / descendants | ✓ (BFS traversal) | ✓ (async report) |
| Person + family timelines | ✓ | ✓ |
| GQL filtering | ✓ (common subset) | ✓ (full) |
| Setup | `GRAMPS_DB_PATH=/path/to/tree.gpkg` | `GRAMPS_API_URL=…` |

The backend is a drop-in replacement for `GrampsWebAPIClient` — all
existing tools work unchanged via the `get_client()` factory in `client.py`.

### New modules

| File | Purpose |
|---|---|
| `_gramps_parsers.py` | XML parsers: person, event, family (with compound-surname support) |
| `_gramps_parsers_ext.py` | XML parsers: place, source, citation, note, media, repository |
| `_gramps_db.py` | In-memory DB, BFS traversal, timeline building |
| `_gql.py` | GQL filter engine (subset) |
| `direct_client.py` | Dispatch layer, public API |

### Tests

146 tests, all passing. A synthetic `.gpkg` is built in memory in
`conftest.py` — no Gramps Web server, no private data, no mocks.
CI matrix covers Python 3.11 / 3.12 / 3.13.

```
_gramps_parsers.py   97 %
_gramps_db.py        92 %
direct_client.py     90 %
_gql.py              78 %
```

---

## A note on issue #8

Issue #8 asks whether this project should be renamed to "Gramps Web MCP"
since it currently only works with Gramps Web, not with desktop Gramps.

This PR offers a different angle: with a direct backend, "Gramps MCP" can
mean exactly what it says — an MCP server for Gramps, regardless of whether
you use the web stack or a local file.

That said, whether this fits the direction you have in mind for the project
is entirely your call. If the scope here is intentionally Gramps Web, I'm
happy to spin this off as a separate repo rather than add complexity to
yours.
