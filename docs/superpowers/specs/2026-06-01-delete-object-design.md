# delete_object Tool — Design Spec

**Date:** 2026-06-01
**Status:** Approved

## Overview

A single generic MCP tool `delete_object` that deletes any Gramps object by type and handle, cascades to orphaned dependents, and cleans up back-references in related objects. SQLite backend only in this iteration; the Web backend raises `NotImplementedError`.

The tool is designed for AI-first usability: a dry-run mode returns a human-readable `summary` string the AI can present to the user before committing.

---

## Tool Interface

### Parameters

| Field | Type | Description |
|-------|------|-------------|
| `obj_type` | str | One of: `person`, `family`, `event`, `place`, `citation`, `source`, `note`, `media`, `repository`, `tag` |
| `handle` | str | Gramps handle of the object to delete |
| `confirmed` | bool | `False` = dry-run only; `True` = execute deletion |

### Response — dry-run (`confirmed=False`)

```json
{
  "summary": "Löscht John Smith (I0001) samt 2 Events (Geburt, Tod). Entfernt ihn als Vater aus Familie F0001.",
  "would_delete": [
    {"obj_type": "person", "handle": "h_pe_john",       "label": "John Smith (I0001)"},
    {"obj_type": "event",  "handle": "h_ev_birth_john", "label": "Birth 1950"},
    {"obj_type": "event",  "handle": "h_ev_death_john", "label": "Death 2020"}
  ],
  "would_unlink": [
    {"obj_type": "family", "handle": "h_fa_smith", "label": "Smith/Doe (F0001)", "field": "father_handle"}
  ]
}
```

### Response — confirmed (`confirmed=True`)

Same structure, keys renamed `deleted` / `unlinked` (list of `{obj_type, handle, label}` entries).

### Errors

- Handle not found → `GrampsAPIError` with clear message
- Web backend → `GrampsAPIError("delete_object is SQLite-only; Web backend not yet supported")`
- SQLite write error → transaction rolled back, `GrampsAPIError`

---

## Cascade Logic

Implemented in `_cascade_delete(conn, obj_type, handle) -> CascadeResult` in a new module `src/gramps_mcp/tools/delete.py`. Runs entirely in a single SQLite transaction (all-or-nothing).

### Step 1 — Find back-references

```sql
SELECT obj_handle, obj_class FROM reference WHERE ref_handle = ?
```

For each referring object: load its `json_data`, remove all occurrences of the handle (from lists, nullable fields), write back. This is generic — no per-type code needed.

### Step 2 — Orphan detection

A small ownership map defines which object types "own" dependents:

```python
_OWNS = {
    "person": ["event"],
    "family": ["event"],
}
```

For each event referenced by the deleted person/family: after removing the reference, check whether any other owner still points to it:

```sql
SELECT COUNT(*) FROM reference WHERE ref_handle = ? AND obj_class IN ('person','family')
```

If count drops to 0 → recursively delete the event (same cascade logic, depth-first).

### Step 3 — Delete the object

```sql
DELETE FROM {table} WHERE handle = ?
DELETE FROM reference WHERE obj_handle = ? OR ref_handle = ?
```

### Step 4 — gramps_id counters

Not reset. Gramps convention: IDs are never recycled.

---

## Label Generation

A helper `_label(conn, obj_type, handle) -> str` produces the human-readable label for each object included in the response:

| Type | Label format |
|------|-------------|
| person | `"Firstname Surname (I0001)"` |
| family | `"Surname1/Surname2 (F0001)"` |
| event | `"EventType YYYY"` (year from date if available) |
| place | `"title (P0001)"` |
| tag | `"name (T0001)"` |
| others | `"gramps_id"` |

---

## New Files

| File | Purpose |
|------|---------|
| `src/gramps_mcp/tools/delete.py` | `_cascade_delete()`, `_label()`, `delete_object_tool()` |
| `tests/test_delete_tool.py` | Unit + integration tests (in-memory SQLite) |

`server.py` gets one new import and one TOOL_REGISTRY entry.

---

## Tests

Three test classes in `tests/test_delete_tool.py`, all against in-memory SQLite:

**1. Unit — `_cascade_delete()` directly**
- Delete a person → owned events with no other owner are cascade-deleted
- Delete an event shared by two persons → only the reference is removed, event survives
- Delete a place → unlinked from events, place row gone

**2. Dry-run via tool**
- `confirmed=False` → returns `summary` + `would_delete` + `would_unlink`, DB unchanged
- Unknown handle → `GrampsAPIError`

**3. Confirmed delete via tool**
- `confirmed=True` → rows gone from DB, `reference` table cleaned
- Rollback: simulate mid-transaction failure → nothing deleted

---

## Out of Scope (this iteration)

- Web backend support
- Undo / soft-delete / recycle bin
- Batch delete
- UI confirmation beyond the `confirmed` flag
