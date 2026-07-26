# Design: Note-Link Tools (`add_note_to_person` / `add_note_to_family`) + `get_note`

**Date:** 2026-07-25
**Status:** Approved
**Branch:** `feature/note-link-tools`

---

## Problem

`create_person` / `create_family` replace `note_list` wholesale, so attaching one note
without touching everything else requires either raw SQLite or a fragile read-modify-write
via `create_person`/`create_family`. The existing link-edit tools (`add_event_to_person`,
`add_citation_to_event`, ...) solved this for events and citations but not notes, and none
of them support create-or-update in one call — they only link objects that already exist.

The requested tools should also behave as an **upsert**: if the caller already knows which
note to attach (by handle/gramps_id) and passes new text/type, the note's content gets
updated in the same call, instead of requiring a separate `create_note` call first.

Separately: there is no way to read a single note's full details back. `find_note_tool`
exists in `search_basic.py` but is dead code — never registered in `TOOL_REGISTRY` or
exported from `tools/__init__.py`. There is also no `get_note`, unlike `get_person`,
`get_family`, `get_event`, `get_place`. This matters directly for Part A: without it there
is no way to verify what `add_note_to_person`'s upsert actually wrote.

---

## Scope

**Part A** — four new SQLite-only tools in a new module `tools/note_link.py`:

| Tool | Behavior |
|---|---|
| `add_note_to_person` | link existing note / create new note / update existing note, then link, to a person |
| `add_note_to_family` | same, for a family |
| `remove_note_from_person` | unlink a note from a person (does not delete the Note object) |
| `remove_note_from_family` | same, for a family |

**Part B** — one new cross-backend tool `get_note`, added to `tools/search_details.py`
alongside `get_event`/`get_place` (same architecture: goes through the abstracted
`client.make_api_call`, so it works on Web, XML, and SQLite backends — unlike Part A,
which is SQLite-only).

---

## Part A: Tool Design

### `add_note_to_person` / `add_note_to_family`

**Parameters:**
- `person_handle` / `person_gramps_id` (family: `family_handle` / `family_gramps_id`)
- `note_handle` (optional) — handle of an existing note
- `note_gramps_id` (optional) — gramps_id of an existing note (e.g. `N0012`), alternative to `note_handle`
- `text` (optional) — note text; required when `note_handle`/`note_gramps_id` are both omitted
- `type` (optional) — note type (e.g. `Research`); required when `note_handle`/`note_gramps_id` are both omitted
- `db` (injected for tests)

**Behavior — three modes selected by which parameters are given:**

1. **Link only** — `note_handle` or `note_gramps_id` given, `text`/`type` omitted.
   The note must already exist (`GrampsAPIError` if not found). Appends the note's handle
   to `note_list` if not already present; `result: "no_change"` if already linked
   (idempotent, matches the pattern of `add_citation_to_event`).

2. **Create + link** — no `note_handle`/`note_gramps_id`, `text` and `type` both given.
   Creates a new Note via `GrampsSqliteDB.put("note", {...})` — the same upsert path
   `create_note_tool` uses on the SQLite backend, so handle generation, `gramps_id`
   assignment (next `N####`), and JSON denormalization (`text` → `StyledText`, `type` →
   `NoteType`) are not reimplemented. Links the new note.

3. **Update + link** — `note_handle`/`note_gramps_id` given **and** at least one of
   `text`/`type` given. Calls `GrampsSqliteDB.put("note", {"handle": ..., ...})` with only
   the provided field(s) in the patch dict — `db.put`'s merge-on-update behavior leaves any
   field not present in the patch untouched, so `text`-only or `type`-only updates work
   independently. Then ensures the note is linked (append to `note_list` if missing).

4. **Error** — no identifier and no `text`/`type`: nothing to create or link
   (`GrampsAPIError`).

**Note object write** goes through `db.put("note", obj)` (not the raw
`_write_object`/`_read_object` pair used elsewhere in this module) because it needs
create-or-update + ID generation, which `db.put` already implements and
`create_note_tool` already relies on for the SQLite backend. The `note_list` append on
the person/family is a second, separate write using the existing
`_read_object`/`_write_person`/`_write_object` helpers — same as
`add_citation_to_event`. These are two sequential SQLite transactions (not one combined
transaction); if the second write fails the note object still exists and can be linked
with a follow-up call.

**Response:**
```json
{
  "result": "created" | "updated" | "linked" | "no_change",
  "note_handle": "...",
  "note_gramps_id": "N0012",
  "person_handle": "...",
  "note_count": 2
}
```
(`family_handle` instead of `person_handle` for `add_note_to_family`.)

---

### `remove_note_from_person` / `remove_note_from_family`

**Parameters:**
- `person_handle` / `person_gramps_id` (family: `family_handle` / `family_gramps_id`)
- `note_handle` / `note_gramps_id`
- `db`

**Behavior:**
- Filters the note handle out of `note_list`.
- Error if the note is not currently linked to this person/family (no silent skip —
  consistent with `remove_citation_from_event`/`remove_event_from_family`).
- Does **not** delete the Note object itself — use `delete_object` for that.

**Response:**
```json
{
  "result": "ok",
  "note_handle": "...",
  "person_handle": "...",
  "note_count": 1
}
```

---

## Part B: `get_note`

Follows the exact shape of `get_event_tool`/`get_place_tool` in `search_details.py`:
resolve `handle` from `gramps_id` via `resolve_handles`, fetch the object via
`client.make_api_call(ApiCalls.GET_NOTE, ...)`, then scan for backlinks.

**Parameters:**
- `gramps_id` (required) — e.g. `N0012` (matches `GetEventParams`/`GetPlaceParams` style:
  gramps_id-only, no raw handle param, since this is the top-level MCP-facing tool)

**Behavior:**
- Fetches the note directly (no `MAX_NOTE_LENGTH` truncation — unlike `format_note`,
  which is meant for inline embedding in other objects' detail views, this tool's whole
  purpose is showing the full text back to the caller).
- Reverse-lookup: scans all persons and all families for this note's handle in their
  `note_list`, same pattern `get_event_tool` uses (scan-all, no guessing). Notes attached
  only to other object types (events, citations, sources, places, media) are not found by
  this scan — documented as a known limitation in the tool description, same honesty
  `get_event_tool` already has about only scanning persons.

**Response** (plain text, matching the Markdown style of `get_event`/`get_place`):
```
## Research Note — N0012 [a1b2c3...]

<full note text>

**Verlinkt mit:**
* Person: Max Mustermann (I0001)
* Familie: F0003
```

---

## Files

- **New** `src/gramps_mcp/tools/note_link.py` — the 4 Part A tool functions. Imports
  `_read_object`, `_write_person`, `_write_object`, `_require_sqlite_db`,
  `_resolve_handle` from `_sqlite_helpers.py` (no new helpers needed there).
- **`src/gramps_mcp/models/parameters/link_edit_params.py`** — add
  `AddNoteToPersonParams`, `AddNoteToFamilyParams`, `RemoveNoteFromPersonParams`,
  `RemoveNoteFromFamilyParams` (same file the citation params already live in).
- **`src/gramps_mcp/tools/search_details.py`** — add `get_note_tool` (Part B), next to
  `get_event_tool`/`get_place_tool`.
- **`src/gramps_mcp/server.py`** — import the 5 tools (4 from Part A + `get_note_tool`),
  add a `GetNoteParams` model next to `GetEventParams`/`GetPlaceParams`, add 4 handler
  functions for Part A (`_handle_add_note_to_person`, etc. — `get_note_tool` needs no
  wrapper handler, same as `get_event`/`get_place`), 5 `TOOL_REGISTRY` entries, add the
  new tool names to `TOOL_GROUPS["person"]` and `TOOL_GROUPS["family"]`.
- **`src/gramps_mcp/tools/__init__.py`** — export the 5 new tool functions, update the
  module docstring's tool count/category list.
- **New** `tests/test_note_link_tools.py` — Part A tests, mirrors
  `test_citation_link_tools.py` structure (fresh in-memory DB per test,
  `_insert_person`/`_insert_family`/`_insert_note` helpers borrowed from
  `test_link_edit_tools.py`).
- **`tests/test_sqlite_detail_tools.py`** — this is where `get_event_tool`/
  `get_place_tool` are already covered against the SQLite backend; add `get_note_tool`
  coverage there in the same style.

`find_note_tool` (the existing dead code in `search_basic.py`) is left as-is — out of
scope, since `get_note` (single object by gramps_id) and `find_note` (text/GQL search)
serve different purposes and registering `find_note` was not requested.

No changes to `create_person`/`create_family` or `create_note_tool` — they keep their
existing behavior.

---

## Error Handling Summary

| Scenario | Behavior |
|---|---|
| Neither handle nor gramps_id for person/family | `GrampsAPIError` |
| gramps_id not found | `GrampsAPIError` |
| `note_handle`/`note_gramps_id` given but not found | `GrampsAPIError` |
| No note identifier and no `text`/`type` | `GrampsAPIError` |
| Note already linked (add, link-only mode) | `result: "no_change"` |
| Note not linked (remove) | `GrampsAPIError` |
| Non-SQLite backend (Part A only) | `GrampsAPIError` via `_require_sqlite_db` |
| `get_note`: gramps_id not found | Text response `"Note {handle} not found"` (matches `get_event_tool`/`get_place_tool` error style, not an exception) |

---

## Tests

All tests use fresh per-test in-memory SQLite DBs with `db=` injection, following
`test_citation_link_tools.py`. No mocks.

`tests/test_note_link_tools.py` (Part A) covers:
- Link-only mode: append existing note, idempotent double-add, resolve via `gramps_id`,
  error on unknown note
- Create+link mode: new note created with generated handle/gramps_id, linked, text/type
  persisted correctly
- Update+link mode: existing note's text/type overwritten, still linked exactly once
- Error when neither identifier nor text/type given
- Error on unknown person/family
- Remove: success, second note survives, error when note not linked
- Both person and family variants for all of the above

`get_note_tool` (Part B) coverage:
- Fetch by gramps_id returns full untruncated text
- Backlink scan finds a linking person, a linking family, and both at once
- No backlinks found → explicit "none found" line, not silent omission
- Unknown gramps_id → not-found text response, no exception

---

## Deliverables Checklist

- [ ] `tools/note_link.py` — 4 Part A tool functions
- [ ] `models/parameters/link_edit_params.py` — 4 new param models
- [ ] `tools/search_details.py` — `get_note_tool`
- [ ] `tools/__init__.py` — export 5 tools + docstring update
- [ ] `server.py` — imports, `GetNoteParams`, handlers, `TOOL_REGISTRY` entries, `TOOL_GROUPS` updates
- [ ] `tests/test_note_link_tools.py` — full Part A coverage per above
- [ ] `get_note_tool` tests — full Part B coverage per above
