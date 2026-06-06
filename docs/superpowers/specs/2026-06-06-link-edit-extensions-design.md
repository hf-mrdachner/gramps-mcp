# Design: Link-Edit Extensions & MCP Discoverability

**Date:** 2026-06-06  
**Status:** Approved  
**Branch:** `feature/link-edit-extensions`

---

## Problem

Two independent gaps were identified during active genealogy work:

1. **Missing tools**: Claude had to use raw SQLite SQL for operations that should be
   first-class MCP tools — removing an event from a person, and adding/removing
   citations on existing events.

2. **Tool discoverability**: MCP tools start as "deferred" in Claude Code (only names
   visible, no schema). Claude bypassed `add_event_to_person` for weeks via SQL because
   it had never loaded the schema. A new session always starts blind.

---

## Scope

### Part A — Three new link-edit tools + one retrofit

| Tool | File | Status |
|---|---|---|
| `remove_event_from_person` | `tools/link_edit.py` | New |
| `add_citation_to_event` | `tools/citation_link.py` | New |
| `remove_citation_from_event` | `tools/citation_link.py` | New |
| `add_event_to_person` | `tools/link_edit.py` | Retrofit: add gramps_id support |

### Part B — Refactor: extract shared helpers

`link_edit.py` would exceed 500 lines after Part A. Extract the four helper functions
into a new private module.

### Part C — MCP discoverability

Server-side fix so Claude loads the right tools from the first prompt.

---

## Part A: Tool Designs

### `remove_event_from_person`

**Parameters:**
- `person_handle` (str, optional) — handle of the person
- `person_gramps_id` (str, optional) — gramps_id of the person (e.g. `I0042`)
- `event_handle` (str, optional) — handle of the event to unlink
- `event_gramps_id` (str, optional) — gramps_id of the event
- `db` (Any, optional) — injected `GrampsSqliteDB` for tests

At least one of handle/gramps_id must be provided for each object.

**Behavior:**
- Reads person from DB, filters matching `EventRef` from `event_ref_list`
- Calls `_write_person` → `birth_ref_index` / `death_ref_index` auto-recomputed
- Does **not** delete the event object itself (use `delete_object` for that)
- Error if event is not linked to this person (no silent skip)
- Idempotency not needed — caller should know if the link exists

**Response:**
```json
{
  "result": "ok",
  "person_handle": "...",
  "event_handle": "...",
  "event_ref_count": 3,
  "birth_ref_index": 0,
  "death_ref_index": 2
}
```

---

### `add_citation_to_event`

**Parameters:**
- `event_handle` / `event_gramps_id`
- `citation_handle` / `citation_gramps_id`
- `db`

**Behavior:**
- Appends `citation_handle` to event's `citation_list`
- If already present → `result: no_change` (idempotent, no error)
- Calls `_write_object(conn, "event", ...)` to persist + update change timestamp

**Response:**
```json
{
  "result": "ok",
  "event_handle": "...",
  "citation_handle": "...",
  "citation_count": 2
}
```

---

### `remove_citation_from_event`

**Parameters:**
- `event_handle` / `event_gramps_id`
- `citation_handle` / `citation_gramps_id`
- `db`

**Behavior:**
- Filters `citation_handle` from event's `citation_list`
- Error if citation not in list (no silent skip)

**Response:**
```json
{
  "result": "ok",
  "event_handle": "...",
  "citation_handle": "...",
  "citation_count": 1
}
```

---

### `add_event_to_person` — Retrofit

Add optional `person_gramps_id` and `event_gramps_id` parameters.
Uses the new `_resolve_handle` helper. No behavior change for existing handle-based calls.

---

### gramps_id resolution in link-edit tools

All four tools use a new helper `_resolve_handle`:

```python
def _resolve_handle(
    conn: Any,
    table: str,
    handle: Optional[str],
    gramps_id: Optional[str],
    label: str,
) -> str:
    """Return handle; resolve via gramps_id if handle is None."""
```

- If `handle` is set → return as-is (no DB lookup)
- If `gramps_id` is set → `SELECT handle FROM {table} WHERE gramps_id = ?`
- If both `None` → `GrampsAPIError(f"{label}: handle or gramps_id required")`
- If gramps_id not found → `GrampsAPIError(f"{label}: gramps_id '{gramps_id}' not found")`

All four tools are **SQLite-only** (`_require_sqlite_db` guard).

---

## Part B: File Refactor

### New file: `tools/_sqlite_helpers.py`

Moves the four shared helpers out of `link_edit.py`:

```
_read_object(conn, table, handle, label) -> Dict
_write_person(conn, handle, person_data) -> None
_write_object(conn, table, handle, data) -> None
_require_sqlite_db(db, tool_name) -> GrampsSqliteDB
_resolve_handle(conn, table, handle, gramps_id, label) -> str   ← new
```

`link_edit.py` imports from `._sqlite_helpers`. No behavior change.

### New file: `tools/citation_link.py`

Contains `add_citation_to_event_tool` and `remove_citation_from_event_tool`.
Imports helpers from `._sqlite_helpers`.

### Updated `tools/link_edit.py`

- Helpers removed (now in `_sqlite_helpers`)
- `remove_event_from_person_tool` added (~50 lines)
- `add_event_to_person_tool` retrofit with gramps_id params

### Estimated post-refactor line counts

| File | LOC |
|---|---|
| `_sqlite_helpers.py` | ~130 |
| `link_edit.py` | ~320 |
| `citation_link.py` | ~120 |

All under the 500-line limit.

### Registration

`tools/__init__.py` and `server.py` get three new tool registrations:
`remove_event_from_person`, `add_citation_to_event`, `remove_citation_from_event`.

---

## Part C: MCP Discoverability

### Problem

MCP tools are deferred in Claude Code: only names are visible at session start. Claude
can know `mcp__gramps-genealogy__add_event_to_person` exists but cannot call it without
first running `ToolSearch select:mcp__gramps-genealogy__add_event_to_person`. Without
that step, Claude falls back to raw SQL — which is slower, error-prone, and bypasses
transaction safety.

### Solution 1: Server `instructions` field

The MCP `initialize` handshake includes an `instructions` field (sent automatically on
connect). Claude Code receives it before any user prompt.

```python
mcp = FastMCP(
    "gramps-genealogy",
    instructions="""
Gramps genealogy database. Tool groups — load before use:

  gramps://tools/person    → create/get/merge/split persons, DNA
  gramps://tools/event     → create/get events, add/remove event↔person links
  gramps://tools/citation  → create citations, add/remove citation↔event links
  gramps://tools/family    → create/get/merge families, child links
  gramps://tools/search    → find_anything, ancestors, descendants, stats
  gramps://tools/admin     → open/close/list databases

Read a group resource before working in that domain.
Before writing raw SQL: check if an MCP tool covers the operation.
""",
)
```

### Solution 2: Tool group resources

Six MCP resources, dynamically generated from the server's registered tool list:

| URI | Content |
|---|---|
| `gramps://tools/person` | All person tools: name, description, required params |
| `gramps://tools/event` | All event tools |
| `gramps://tools/citation` | All citation/source tools |
| `gramps://tools/family` | All family tools |
| `gramps://tools/search` | All search/analysis tools |
| `gramps://tools/admin` | Database management tools |

Each resource returns Markdown. Example entry:
```
### add_event_to_person
Link an existing event to a person's event_ref_list.
Required: person_handle OR person_gramps_id, event_handle OR event_gramps_id
Optional: role (default: Primary)
```

Resources are generated at request time from a static mapping in `server.py`:

```python
TOOL_GROUPS: dict[str, list[str]] = {
    "person": ["create_person", "get_person", "merge_persons", "split_person", ...],
    "event": ["create_event", "get_event", "add_event_to_person", "remove_event_from_person"],
    "citation": ["create_citation", "create_source", "add_citation_to_event", "remove_citation_from_event"],
    ...
}
```

New tools are added to this mapping when registered — one-line maintenance cost per tool.

### CLAUDE.md addition (`.claude/CLAUDE.md`)

```markdown
### MCP Tool Discovery
Before attempting raw SQLite for any genealogy operation, read the relevant
tool group resource to check if an MCP tool covers it:
- Persons: read resource `gramps://tools/person`
- Events: read resource `gramps://tools/event`
- Citations/Sources: read resource `gramps://tools/citation`
- Families: read resource `gramps://tools/family`
```

### Together

`instructions` tells Claude the groups exist and how to use them.
Resources deliver the details on demand, per domain, without bloating context.
CLAUDE.md reinforces the "check before SQL" behavior for new sessions.

---

## Error Handling Summary

| Scenario | Behavior |
|---|---|
| Neither handle nor gramps_id provided | `GrampsAPIError` |
| gramps_id not found in DB | `GrampsAPIError` with gramps_id in message |
| Handle not found in DB | `GrampsAPIError` |
| Event not in person's event_ref_list | `GrampsAPIError` |
| Citation not in event's citation_list | `GrampsAPIError` |
| Event already in event_ref_list (add) | `result: no_change` (idempotent) |
| Citation already in citation_list (add) | `result: no_change` (idempotent) |
| Non-SQLite backend | `GrampsAPIError` via `_require_sqlite_db` |

---

## Tests

All tests use the `conftest_sqlite.py` in-memory SQLite fixture. No mocks.

| File | Covers |
|---|---|
| `tests/test_remove_event_from_person.py` | remove via handle, via gramps_id, error if not linked, birth/death index recomputed after remove |
| `tests/test_citation_link.py` | add + remove via handle and gramps_id, idempotent add, error on missing remove target |
| `tests/test_link_edit.py` (extend) | gramps_id variants for `add_event_to_person` |

---

## Deliverables Checklist

- [ ] `tools/_sqlite_helpers.py` — extract + add `_resolve_handle`
- [ ] `tools/link_edit.py` — remove helpers, add `remove_event_from_person`, retrofit `add_event_to_person`
- [ ] `tools/citation_link.py` — `add_citation_to_event` + `remove_citation_from_event`
- [ ] `tools/__init__.py` + `server.py` — register 3 new tools
- [ ] `server.py` — add `instructions` to `FastMCP()`
- [ ] `server.py` — implement tool group resources (`gramps://tools/*`)
- [ ] `.claude/CLAUDE.md` — add MCP tool discovery rule
- [ ] Tests for all new tools and gramps_id retrofits
