# Design: Link-Edit Tools & SQLite-Layer-Fixes

**Date:** 2026-06-02  
**Branch:** dev  
**Status:** Approved

## Problem

During genealogy research, four gaps were identified where agents had to resort to raw SQL scripts:

1. `birth_ref_index`/`death_ref_index` not set automatically when Birth/Death events are linked to a person
2. `parent_family_list` of children not updated when a family is created with `child_handles`
3. No way to append a single event to a person without replacing the entire `event_ref_list`
4. No way to remove a child from a family (keeping both sides consistent)
5. No way to atomically move a note or media reference from one object to another

`delete_object` (added in the previous session) already covers event deletion.

## Approach: Option C — SQLite-Layer Fixes + 3 New MCP Tools

### Part 1: Silent SQLite-Layer Fixes (`_gramps_sqlite.py`)

Two invariants maintained automatically — no agent awareness required.

**Fix A — `birth_ref_index`/`death_ref_index` auto-update**

Location: `_build_gramps_json()` in `_gramps_sqlite.py`, person branch only.

After the `event_ref_list` is assembled, scan it for the first entry whose event type value is `12` (Birth) and `13` (Death). Set `birth_ref_index` and `death_ref_index` to those positions, or `-1` if not found.

Trigger: every `put("person", ...)` — same object being written, no side effects.

**Fix B — `parent_family_list` update after `create_family` with children**

Location: `GrampsSqliteDB.put()`, family branch.

Condition: fires **only** when `child_handles` or `child_ref_list` is explicitly present in the patch dict. If the patch does not mention children, nothing happens (avoids unexpected side effects on unrelated updates).

When triggered: for each child handle in the resulting `child_ref_list`, read the person's current JSON and add the family handle to `parent_family_list` if not already present. Write the updated person back. All within the same SQLite transaction as the family write.

**Why "only when children in patch":** A patch that only changes e.g. the relationship type would otherwise silently touch all child person records, updating their `change` timestamps and appearing in `recent_changes`. This would be surprising and incorrect.

### Part 2: Three New MCP Tools (`tools/link_edit.py`)

New module: `src/gramps_mcp/tools/link_edit.py`

**Rationale for new module:** `data_management.py` is at ~445 lines (limit: 500). These tools manipulate links between existing objects, which is conceptually distinct from creating objects.

All three tools are **SQLite-only** (like `delete_object`). They check that the active client is a `GrampsSqliteClient` and raise `GrampsAPIError` otherwise.

---

#### Tool 1: `add_event_to_person`

Appends an event reference to a person's `event_ref_list` without replacing it.

**Parameters (Pydantic model `AddEventToPersonParams`):**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `person_handle` | `str` | required | Handle of the person |
| `event_handle` | `str` | required | Handle of the event to link (must exist) |
| `role` | `str` | `"Primary"` | Event role (e.g. Primary, Witness) |

**Behaviour:**
1. Verify both handles exist in the DB; raise `GrampsAPIError` if not.
2. Read person's current `event_ref_list`.
3. Check for duplicate (`event_handle` already in list) — if duplicate, return info message, no write.
4. Append new `EventRef` entry with the given role.
5. Write person back via `db.put("person", ...)`.
6. SQLite-Layer Fix A fires automatically → `birth_ref_index`/`death_ref_index` updated.

**Returns:** JSON summary with updated person handle, gramps_id, and new event_ref_list length.

---

#### Tool 2: `remove_child_from_family`

Removes a child from a family and cleans up the child's `parent_family_list`.

**Parameters (Pydantic model `RemoveChildFromFamilyParams`):**

| Field | Type | Description |
|-------|------|-------------|
| `family_handle` | `str` | Handle of the family |
| `child_handle` | `str` | Handle of the child person to remove |

**Behaviour:**
1. Verify both handles exist; raise `GrampsAPIError` if not.
2. Read family's current `child_ref_list`; raise `GrampsAPIError` if child not in list.
3. Remove the child's entry from `child_ref_list`.
4. Write family back.
5. Read child person's `parent_family_list`; remove `family_handle` if present.
6. Write child person back.
7. Steps 4–6 in a single SQLite transaction.

**Returns:** JSON summary with what was removed.

---

#### Tool 3: `move_attachment`

Atomically moves a note or media reference from one object to another.

**Parameters (Pydantic model `MoveAttachmentParams`):**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `attachment_type` | `"note" \| "media"` | required | Type of attachment |
| `handle` | `str` | required | Handle of the note or media object |
| `from_handle` | `str` | required | Source object handle |
| `from_type` | `"person" \| "family"` | `"person"` | Source object type |
| `to_handle` | `str` | required | Target object handle |
| `to_type` | `"person" \| "family"` | `"person"` | Target object type |

**Behaviour:**
1. Verify `handle`, `from_handle`, `to_handle` all exist; raise `GrampsAPIError` if not.
2. Read source object; verify `handle` is in its `note_list`/`media_list`; raise `GrampsAPIError` if not.
3. Remove handle from source's list.
4. Read target object; add handle to its `note_list`/`media_list` (no-op if already present).
5. Write both objects back in a single SQLite transaction.

**Returns:** JSON summary confirming move (from → to).

---

### Part 3: Server Registration

In `server.py`: three new entries in `TOOL_REGISTRY`, three new Pydantic parameter schemas in `models/parameters/link_edit_params.py`.

Imports added to `tools/__init__.py`.

## Testing Strategy (TDD)

**`tests/test_sqlite_layer_fixes.py`** — unit/integration tests for the SQLite-layer changes:

- `birth_ref_index` set correctly when Birth event in `event_ref_list`
- `death_ref_index` set correctly when Death event present
- Both remain `-1` when no Birth/Death events
- `parent_family_list` of child updated when family written with `child_handles`
- No side effect when patch contains no `child_handles`/`child_ref_list`

**`tests/test_link_edit_tools.py`** — integration tests for the three tools:

- `add_event_to_person`: event appended, `birth_ref_index` set correctly for Birth event
- `add_event_to_person`: no duplicate on double call
- `add_event_to_person`: error on unknown person/event handle
- `remove_child_from_family`: child removed from family + `parent_family_list` cleaned
- `remove_child_from_family`: error when child not in family
- `move_attachment`: note moved person → person
- `move_attachment`: note moved person → family
- `move_attachment`: media moved person → person
- `move_attachment`: error when handle not in source's list

All tests use in-memory SQLite (pattern from `conftest_sqlite.py`) — no Gramps Web server needed.
