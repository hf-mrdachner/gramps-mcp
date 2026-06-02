# Link-Edit Tools & SQLite-Layer-Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add two silent SQLite-layer fixes (birth/death ref indices, parent_family_list) and three new MCP tools (add_event_to_person, remove_child_from_family, move_attachment) so that agents never need to write raw SQL.

**Architecture:** Silent invariants are enforced inside `GrampsSqliteDB.put()` with two new module-level helper functions in `_gramps_sqlite.py`. Three SQLite-only MCP tools in a new `tools/link_edit.py` file manipulate Gramps raw JSON directly (no normalization round-trip) and use injectable `db` parameters for testability. Server registration follows the exact same pattern as `delete_object`.

**Tech Stack:** Python, SQLite (stdlib sqlite3), Pydantic v2, MCP Python SDK.

---

## File Map

| Action | File | What changes |
|--------|------|-------------|
| Modify | `src/gramps_mcp/_gramps_sqlite.py` | Add `_compute_birth_death_indices`, `_update_parent_family_list`; call both from `GrampsSqliteDB.put()` |
| Create | `src/gramps_mcp/models/parameters/link_edit_params.py` | Pydantic models for 3 new tools |
| Create | `src/gramps_mcp/tools/link_edit.py` | 3 tool functions + private helpers |
| Modify | `src/gramps_mcp/server.py` | Import + TOOL_REGISTRY entries |
| Create | `tests/test_sqlite_layer_fixes.py` | Tests for Fix A + Fix B |
| Create | `tests/test_link_edit_tools.py` | Tests for the 3 new tools |

---

## Task 1: Fix A — `birth_ref_index` / `death_ref_index` auto-update

**Files:**
- Modify: `src/gramps_mcp/_gramps_sqlite.py`
- Create: `tests/test_sqlite_layer_fixes.py`

### Step 1.1 — Write the failing tests

Create `tests/test_sqlite_layer_fixes.py`:

```python
"""
Tests for SQLite-layer silent fixes:
  Fix A: birth_ref_index / death_ref_index auto-update on put("person", ...)
  Fix B: parent_family_list update after put("family", ...) with child_handles
"""

import json
import sqlite3

import pytest

from gramps_mcp._gramps_sqlite import GrampsSqliteDB

# ---------------------------------------------------------------------------
# Minimal schema — only the tables needed for these tests
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE person (
    handle VARCHAR(50) PRIMARY KEY NOT NULL,
    given_name TEXT, surname TEXT,
    json_data TEXT, gramps_id TEXT, gender INTEGER DEFAULT 2,
    death_ref_index INTEGER DEFAULT -1,
    birth_ref_index INTEGER DEFAULT -1,
    change INTEGER DEFAULT 0, private INTEGER DEFAULT 0
);
CREATE TABLE family (
    handle VARCHAR(50) PRIMARY KEY NOT NULL,
    json_data TEXT, gramps_id TEXT,
    father_handle VARCHAR(50), mother_handle VARCHAR(50),
    change INTEGER DEFAULT 0, private INTEGER DEFAULT 0
);
CREATE TABLE event (
    handle VARCHAR(50) PRIMARY KEY NOT NULL,
    json_data TEXT, gramps_id TEXT,
    description TEXT, place VARCHAR(50),
    change INTEGER DEFAULT 0, private INTEGER DEFAULT 0
);
CREATE TABLE metadata (
    setting VARCHAR(50) PRIMARY KEY NOT NULL,
    json_data TEXT, value BLOB
);
"""

# Gramps-JSON event type dicts (raw, as stored in the DB)
_BIRTH_TYPE = {"_class": "EventType", "value": 12, "string": ""}
_DEATH_TYPE = {"_class": "EventType", "value": 13, "string": ""}
_MARRIAGE_TYPE = {"_class": "EventType", "value": 1, "string": ""}

_EMPTY_DATE = {
    "_class": "Date", "calendar": 0, "modifier": 0, "quality": 0,
    "dateval": [0, 0, 0, False], "text": "", "sortval": 0, "newyear": 0, "format": None,
}


def _eref(handle: str, role_val: int = 1) -> dict:
    return {
        "_class": "EventRef", "ref": handle,
        "role": {"_class": "EventRoleType", "value": role_val, "string": ""},
        "note_list": [], "attribute_list": [], "private": False,
    }


@pytest.fixture()
def fresh_db():
    """Fresh in-memory DB with person/event/family tables for each test."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    conn.commit()
    db = GrampsSqliteDB(conn=conn, db_path=":memory:", read_only=False)
    return db, conn


def _insert_event(conn, handle: str, gramps_id: str, event_type: dict) -> None:
    """Insert a minimal event row directly into the DB."""
    data = {
        "_class": "Event", "handle": handle, "gramps_id": gramps_id,
        "type": event_type, "date": _EMPTY_DATE,
        "description": "", "place": None,
        "citation_list": [], "note_list": [], "media_list": [],
        "attribute_list": [], "tag_list": [], "change": 0, "private": False,
    }
    conn.execute(
        "INSERT INTO event (handle, gramps_id, json_data, change, private) "
        "VALUES (?, ?, ?, 0, 0)",
        (handle, gramps_id, json.dumps(data)),
    )
    conn.commit()


# ===========================================================================
# Fix A — birth_ref_index / death_ref_index
# ===========================================================================

class TestBirthDeathRefIndexAutoUpdate:
    def test_birth_ref_index_set_when_birth_event_linked(self, fresh_db):
        db, conn = fresh_db
        _insert_event(conn, "h_ev_birth", "E0001", _BIRTH_TYPE)

        # Create person with a Birth event in event_ref_list
        person = db.put("person", {
            "given_name": "John", "surname": "Smith",
            "event_ref_list": [{"ref": "h_ev_birth", "role": "Primary"}],
        })
        handle = person["handle"]

        row = conn.execute(
            "SELECT birth_ref_index, json_data FROM person WHERE handle = ?", (handle,)
        ).fetchone()
        assert row["birth_ref_index"] == 0
        data = json.loads(row["json_data"])
        assert data["birth_ref_index"] == 0

    def test_death_ref_index_set_when_death_event_linked(self, fresh_db):
        db, conn = fresh_db
        _insert_event(conn, "h_ev_birth", "E0001", _BIRTH_TYPE)
        _insert_event(conn, "h_ev_death", "E0002", _DEATH_TYPE)

        person = db.put("person", {
            "given_name": "John", "surname": "Smith",
            "event_ref_list": [
                {"ref": "h_ev_birth", "role": "Primary"},
                {"ref": "h_ev_death", "role": "Primary"},
            ],
        })
        handle = person["handle"]

        row = conn.execute(
            "SELECT birth_ref_index, death_ref_index FROM person WHERE handle = ?", (handle,)
        ).fetchone()
        assert row["birth_ref_index"] == 0
        assert row["death_ref_index"] == 1

    def test_indices_remain_minus_one_when_no_birth_death_events(self, fresh_db):
        db, conn = fresh_db
        _insert_event(conn, "h_ev_marriage", "E0001", _MARRIAGE_TYPE)

        person = db.put("person", {
            "given_name": "John", "surname": "Smith",
            "event_ref_list": [{"ref": "h_ev_marriage", "role": "Primary"}],
        })
        handle = person["handle"]

        row = conn.execute(
            "SELECT birth_ref_index, death_ref_index FROM person WHERE handle = ?", (handle,)
        ).fetchone()
        assert row["birth_ref_index"] == -1
        assert row["death_ref_index"] == -1

    def test_indices_minus_one_when_no_events(self, fresh_db):
        db, conn = fresh_db
        person = db.put("person", {"given_name": "Jane", "surname": "Doe"})
        handle = person["handle"]

        row = conn.execute(
            "SELECT birth_ref_index, death_ref_index FROM person WHERE handle = ?", (handle,)
        ).fetchone()
        assert row["birth_ref_index"] == -1
        assert row["death_ref_index"] == -1

    def test_first_birth_event_is_used_when_multiple_exist(self, fresh_db):
        db, conn = fresh_db
        _insert_event(conn, "h_ev_birth1", "E0001", _BIRTH_TYPE)
        _insert_event(conn, "h_ev_birth2", "E0002", _BIRTH_TYPE)

        person = db.put("person", {
            "given_name": "John", "surname": "Smith",
            "event_ref_list": [
                {"ref": "h_ev_birth1", "role": "Primary"},
                {"ref": "h_ev_birth2", "role": "Primary"},
            ],
        })
        handle = person["handle"]

        row = conn.execute(
            "SELECT birth_ref_index FROM person WHERE handle = ?", (handle,)
        ).fetchone()
        assert row["birth_ref_index"] == 0  # first birth, not second


# ===========================================================================
# Fix B — parent_family_list
# ===========================================================================

class TestParentFamilyListAutoUpdate:
    def test_parent_family_list_updated_when_family_written_with_child_handles(
        self, fresh_db
    ):
        db, conn = fresh_db

        # Create child person first (no parent family yet)
        child = db.put("person", {"given_name": "James", "surname": "Smith"})
        child_handle = child["handle"]

        # Create family with child_handles
        family = db.put("family", {
            "child_handles": [child_handle],
        })
        family_handle = family["handle"]

        # Check child's parent_family_list was updated
        row = conn.execute(
            "SELECT json_data FROM person WHERE handle = ?", (child_handle,)
        ).fetchone()
        child_data = json.loads(row["json_data"])
        assert family_handle in child_data.get("parent_family_list", [])

    def test_parent_family_list_not_duplicated_on_double_write(self, fresh_db):
        db, conn = fresh_db

        child = db.put("person", {"given_name": "James", "surname": "Smith"})
        child_handle = child["handle"]

        family = db.put("family", {"child_handles": [child_handle]})
        family_handle = family["handle"]

        # Write again — must not duplicate
        db.put("family", {"handle": family_handle, "child_handles": [child_handle]})

        row = conn.execute(
            "SELECT json_data FROM person WHERE handle = ?", (child_handle,)
        ).fetchone()
        child_data = json.loads(row["json_data"])
        pfl = child_data.get("parent_family_list", [])
        assert pfl.count(family_handle) == 1

    def test_no_side_effect_when_patch_has_no_child_fields(self, fresh_db):
        db, conn = fresh_db

        child = db.put("person", {"given_name": "James", "surname": "Smith"})
        child_handle = child["handle"]

        family = db.put("family", {"father_handle": None, "mother_handle": None})
        family_handle = family["handle"]

        # Patch that does NOT include child_handles or child_ref_list
        db.put("family", {
            "handle": family_handle,
            "type": "Married",  # unrelated field
        })

        # child's parent_family_list must NOT have family_handle
        row = conn.execute(
            "SELECT json_data FROM person WHERE handle = ?", (child_handle,)
        ).fetchone()
        child_data = json.loads(row["json_data"])
        assert family_handle not in child_data.get("parent_family_list", [])
```

- [ ] **Step 1.2 — Run tests to verify they fail**

```
uv run pytest tests/test_sqlite_layer_fixes.py -v
```

Expected: all tests FAIL (functions not yet implemented).

- [ ] **Step 1.3 — Add `_compute_birth_death_indices` to `_gramps_sqlite.py`**

Add this function after `_denorm_event_ref` (after line ~697, before `_denorm_child_ref`):

```python
def _compute_birth_death_indices(conn: Any, event_ref_list: List[Dict]) -> Tuple[int, int]:
    """
    Scan event_ref_list and return the first Birth (12) and Death (13) indices.

    Queries the event table for each ref handle to determine event type.
    Returns -1 for types not found.

    Args:
        conn: Open sqlite3.Connection.
        event_ref_list: List of EventRef dicts in Gramps JSON format.

    Returns:
        Tuple (birth_ref_index, death_ref_index).
    """
    birth_idx = -1
    death_idx = -1
    for i, eref in enumerate(event_ref_list):
        if not isinstance(eref, dict):
            continue
        handle = eref.get("ref")
        if not handle:
            continue
        row = conn.execute(
            "SELECT json_data FROM event WHERE handle = ?",  # noqa: S608
            (handle,),
        ).fetchone()
        if not row:
            continue
        try:
            event_data = json.loads(row[0])
        except Exception:
            continue
        etype = event_data.get("type", {})
        val = etype.get("value") if isinstance(etype, dict) else None
        if val == 12 and birth_idx == -1:
            birth_idx = i
        elif val == 13 and death_idx == -1:
            death_idx = i
    return birth_idx, death_idx
```

- [ ] **Step 1.4 — Call `_compute_birth_death_indices` in `GrampsSqliteDB.put()`**

In `put()`, the line `gramps_json = _build_gramps_json(obj_type, obj, existing_raw)` is followed immediately by `json_str = json.dumps(...)`. Insert the Fix A block between them:

```python
        gramps_json = _build_gramps_json(obj_type, obj, existing_raw)

        # Fix A: auto-update birth/death ref indices so they always match event_ref_list
        if obj_type == "person":
            birth_idx, death_idx = _compute_birth_death_indices(
                self._conn, gramps_json.get("event_ref_list", [])
            )
            gramps_json["birth_ref_index"] = birth_idx
            gramps_json["death_ref_index"] = death_idx
            obj = {**obj, "birth_ref_index": birth_idx, "death_ref_index": death_idx}

        json_str = json.dumps(gramps_json, ensure_ascii=False)
```

- [ ] **Step 1.5 — Run tests to verify Fix A passes**

```
uv run pytest tests/test_sqlite_layer_fixes.py::TestBirthDeathRefIndexAutoUpdate -v
```

Expected: all 5 `TestBirthDeathRefIndexAutoUpdate` tests PASS.

- [ ] **Step 1.6 — Run full test suite to check for regressions**

```
uv run pytest -x -q
```

Expected: no new failures.

- [ ] **Step 1.7 — Commit**

```
git add src/gramps_mcp/_gramps_sqlite.py tests/test_sqlite_layer_fixes.py
git commit -m "feat: auto-update birth/death ref indices in GrampsSqliteDB.put()"
```

---

## Task 2: Fix B — `parent_family_list` auto-update

**Files:**
- Modify: `src/gramps_mcp/_gramps_sqlite.py`
- Modify: `tests/test_sqlite_layer_fixes.py` (tests already written in Task 1)

- [ ] **Step 2.1 — Run Fix B tests to verify they fail**

```
uv run pytest tests/test_sqlite_layer_fixes.py::TestParentFamilyListAutoUpdate -v
```

Expected: all 3 `TestParentFamilyListAutoUpdate` tests FAIL.

- [ ] **Step 2.2 — Add `_update_parent_family_list` to `_gramps_sqlite.py`**

Add immediately after `_compute_birth_death_indices`:

```python
def _update_parent_family_list(conn: Any, family_handle: str, child_handles: List[str]) -> None:
    """
    Add family_handle to parent_family_list of each child person in the DB.

    Called within the same SQLite transaction as the family write. Only adds;
    never removes (removal is handled by the remove_child_from_family tool).

    Args:
        conn: Open sqlite3.Connection within an active transaction.
        family_handle: The family handle to add to each child's parent_family_list.
        child_handles: List of child person handles to update.
    """
    for child_handle in child_handles:
        row = conn.execute(
            "SELECT json_data FROM person WHERE handle = ?",  # noqa: S608
            (child_handle,),
        ).fetchone()
        if not row:
            continue
        try:
            person_data = json.loads(row[0])
        except Exception:
            continue
        pfl = person_data.get("parent_family_list", [])
        if family_handle not in pfl:
            pfl.append(family_handle)
            person_data["parent_family_list"] = pfl
            conn.execute(
                "UPDATE person SET json_data = ? WHERE handle = ?",  # noqa: S608
                (json.dumps(person_data, ensure_ascii=False), child_handle),
            )
```

- [ ] **Step 2.3 — Call `_update_parent_family_list` inside `GrampsSqliteDB.put()`**

Inside the `with self._conn:` block, AFTER the `conn.execute()` for INSERT or UPDATE, add Fix B. The block looks like this (find it by the `if existing_raw is None:` branch):

```python
        try:
            with self._conn:
                secondaries = _secondaries(obj_type, obj)
                if existing_raw is None:
                    cols = ["handle", "json_data"] + list(secondaries.keys())
                    # ... INSERT ...
                else:
                    # ... UPDATE ...

                # Fix B: update parent_family_list of children when family written with child data
                if obj_type == "family" and (
                    "child_handles" in obj or "child_ref_list" in obj
                ):
                    child_handles = [
                        cr["ref"]
                        for cr in gramps_json.get("child_ref_list", [])
                        if isinstance(cr, dict) and cr.get("ref")
                    ]
                    _update_parent_family_list(self._conn, handle, child_handles)

        except sqlite3.Error as exc:
```

- [ ] **Step 2.4 — Run Fix B tests**

```
uv run pytest tests/test_sqlite_layer_fixes.py::TestParentFamilyListAutoUpdate -v
```

Expected: all 3 tests PASS.

- [ ] **Step 2.5 — Run full test suite**

```
uv run pytest -x -q
```

Expected: no new failures.

- [ ] **Step 2.6 — Commit**

```
git add src/gramps_mcp/_gramps_sqlite.py
git commit -m "feat: auto-update parent_family_list in GrampsSqliteDB.put() when child_handles written"
```

---

## Task 3: Parameter models for new tools

**Files:**
- Create: `src/gramps_mcp/models/parameters/link_edit_params.py`

- [ ] **Step 3.1 — Create the parameter models file**

```python
# src/gramps_mcp/models/parameters/link_edit_params.py
"""Pydantic parameter models for the three link-edit MCP tools."""

from typing import Literal

from pydantic import BaseModel, Field


class AddEventToPersonParams(BaseModel):
    """Parameters for add_event_to_person tool."""

    person_handle: str = Field(..., description="Handle of the person to link the event to")
    event_handle: str = Field(
        ..., description="Handle of the event to link (must already exist in the DB)"
    )
    role: str = Field(
        "Primary",
        description=(
            "Role of the person in the event. Common values: Primary, Witness, "
            "Godparent, Informant. Default: Primary."
        ),
    )


class RemoveChildFromFamilyParams(BaseModel):
    """Parameters for remove_child_from_family tool."""

    family_handle: str = Field(..., description="Handle of the family")
    child_handle: str = Field(..., description="Handle of the child person to remove")


class MoveAttachmentParams(BaseModel):
    """Parameters for move_attachment tool."""

    attachment_type: Literal["note", "media"] = Field(
        ..., description="Type of attachment to move: 'note' or 'media'"
    )
    handle: str = Field(..., description="Handle of the note or media object to move")
    from_handle: str = Field(..., description="Handle of the source object")
    from_type: Literal["person", "family"] = Field(
        "person", description="Type of the source object: 'person' or 'family'"
    )
    to_handle: str = Field(..., description="Handle of the target object")
    to_type: Literal["person", "family"] = Field(
        "person", description="Type of the target object: 'person' or 'family'"
    )
```

- [ ] **Step 3.2 — Verify import works**

```
uv run python -c "from gramps_mcp.models.parameters.link_edit_params import AddEventToPersonParams, RemoveChildFromFamilyParams, MoveAttachmentParams; print('OK')"
```

Expected output: `OK`

- [ ] **Step 3.3 — Commit**

```
git add src/gramps_mcp/models/parameters/link_edit_params.py
git commit -m "feat: add Pydantic parameter models for link-edit tools"
```

---

## Task 4: `add_event_to_person` tool

**Files:**
- Create: `src/gramps_mcp/tools/link_edit.py`
- Create: `tests/test_link_edit_tools.py`

- [ ] **Step 4.1 — Write the failing tests**

Create `tests/test_link_edit_tools.py`:

```python
"""
Tests for link-edit MCP tools:
  - add_event_to_person
  - remove_child_from_family
  - move_attachment

All tests use fresh per-test in-memory SQLite DBs. Tool functions are called
directly with the db= parameter injected (same pattern as test_delete_tool.py).
"""

import asyncio
import json
import sqlite3

import pytest

from gramps_mcp._gramps_sqlite import GrampsSqliteDB
from gramps_mcp.client import GrampsAPIError

# ---------------------------------------------------------------------------
# Minimal schema (same as conftest_sqlite._SCHEMA minus unused tables)
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE person (
    handle VARCHAR(50) PRIMARY KEY NOT NULL,
    given_name TEXT, surname TEXT,
    json_data TEXT, gramps_id TEXT, gender INTEGER DEFAULT 2,
    death_ref_index INTEGER DEFAULT -1,
    birth_ref_index INTEGER DEFAULT -1,
    change INTEGER DEFAULT 0, private INTEGER DEFAULT 0
);
CREATE TABLE family (
    handle VARCHAR(50) PRIMARY KEY NOT NULL,
    json_data TEXT, gramps_id TEXT,
    father_handle VARCHAR(50), mother_handle VARCHAR(50),
    change INTEGER DEFAULT 0, private INTEGER DEFAULT 0
);
CREATE TABLE event (
    handle VARCHAR(50) PRIMARY KEY NOT NULL,
    json_data TEXT, gramps_id TEXT,
    description TEXT, place VARCHAR(50),
    change INTEGER DEFAULT 0, private INTEGER DEFAULT 0
);
CREATE TABLE note (
    handle VARCHAR(50) PRIMARY KEY NOT NULL,
    json_data TEXT, gramps_id TEXT, format INTEGER DEFAULT 0,
    change INTEGER DEFAULT 0, private INTEGER DEFAULT 0
);
CREATE TABLE media (
    handle VARCHAR(50) PRIMARY KEY NOT NULL,
    json_data TEXT, gramps_id TEXT, path TEXT, mime TEXT,
    desc TEXT, checksum TEXT, change INTEGER DEFAULT 0, private INTEGER DEFAULT 0
);
CREATE TABLE metadata (
    setting VARCHAR(50) PRIMARY KEY NOT NULL,
    json_data TEXT, value BLOB
);
"""

_EMPTY_DATE = {
    "_class": "Date", "calendar": 0, "modifier": 0, "quality": 0,
    "dateval": [0, 0, 0, False], "text": "", "sortval": 0, "newyear": 0, "format": None,
}


@pytest.fixture()
def fresh_db():
    """Fresh in-memory GrampsSqliteDB for each test."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    conn.commit()
    db = GrampsSqliteDB(conn=conn, db_path=":memory:", read_only=False)
    return db, conn


def _insert_event(conn, handle: str, gramps_id: str, type_value: int) -> None:
    data = {
        "_class": "Event", "handle": handle, "gramps_id": gramps_id,
        "type": {"_class": "EventType", "value": type_value, "string": ""},
        "date": _EMPTY_DATE, "description": "", "place": None,
        "citation_list": [], "note_list": [], "media_list": [],
        "attribute_list": [], "tag_list": [], "change": 0, "private": False,
    }
    conn.execute(
        "INSERT INTO event (handle, gramps_id, json_data, change, private) VALUES (?,?,?,0,0)",
        (handle, gramps_id, json.dumps(data)),
    )
    conn.commit()


def _insert_person(conn, handle: str, gramps_id: str,
                   note_list=None, media_list=None, event_ref_list=None,
                   parent_family_list=None) -> None:
    data = {
        "_class": "Person", "handle": handle, "gramps_id": gramps_id,
        "gender": 2,
        "primary_name": {
            "_class": "Name", "first_name": "Test", "suffix": "", "title": "",
            "call": "", "nick": "", "famnick": "", "group_as": "",
            "sort_as": 0, "display_as": 0, "private": False,
            "surname_list": [{"_class": "Surname", "surname": "Person",
                              "prefix": "", "primary": True, "connector": "",
                              "origintype": {"_class": "NameOriginType", "value": 1, "string": ""}}],
            "citation_list": [], "note_list": [],
            "type": {"_class": "NameType", "value": 2, "string": ""},
            "date": _EMPTY_DATE,
        },
        "alternate_names": [], "death_ref_index": -1, "birth_ref_index": -1,
        "event_ref_list": event_ref_list or [],
        "family_list": [], "parent_family_list": parent_family_list or [],
        "media_list": media_list or [], "address_list": [], "attribute_list": [],
        "urls": [], "lds_ord_list": [], "citation_list": [],
        "note_list": note_list or [], "tag_list": [], "person_ref_list": [],
        "change": 0, "private": False,
    }
    conn.execute(
        "INSERT INTO person (handle, gramps_id, json_data, change, private, "
        "birth_ref_index, death_ref_index) VALUES (?,?,?,0,0,-1,-1)",
        (handle, gramps_id, json.dumps(data)),
    )
    conn.commit()


def _insert_family(conn, handle: str, gramps_id: str,
                   child_ref_list=None, note_list=None, media_list=None) -> None:
    data = {
        "_class": "Family", "handle": handle, "gramps_id": gramps_id,
        "father_handle": None, "mother_handle": None,
        "child_ref_list": child_ref_list or [],
        "type": {"_class": "FamilyRelType", "value": 0, "string": ""},
        "event_ref_list": [], "media_list": media_list or [],
        "attribute_list": [], "lds_ord_list": [], "citation_list": [],
        "note_list": note_list or [], "tag_list": [], "change": 0, "private": False,
    }
    conn.execute(
        "INSERT INTO family (handle, gramps_id, json_data, change, private) VALUES (?,?,?,0,0)",
        (handle, gramps_id, json.dumps(data)),
    )
    conn.commit()


def _insert_note(conn, handle: str, gramps_id: str) -> None:
    data = {
        "_class": "Note", "handle": handle, "gramps_id": gramps_id,
        "format": 0, "text": {"_class": "StyledText", "string": "Test note", "tags": []},
        "type": {"_class": "NoteType", "value": 1, "string": "General"},
        "tag_list": [], "change": 0, "private": False,
    }
    conn.execute(
        "INSERT INTO note (handle, gramps_id, json_data, format, change, private) "
        "VALUES (?,?,?,0,0,0)",
        (handle, gramps_id, json.dumps(data)),
    )
    conn.commit()


def _insert_media(conn, handle: str, gramps_id: str) -> None:
    data = {
        "_class": "MediaObject", "handle": handle, "gramps_id": gramps_id,
        "path": "/test.jpg", "mime": "image/jpeg", "desc": "Test", "checksum": "abc",
        "date": _EMPTY_DATE, "note_list": [], "citation_list": [],
        "attribute_list": [], "tag_list": [], "media_list": [], "change": 0, "private": False,
    }
    conn.execute(
        "INSERT INTO media (handle, gramps_id, json_data, path, mime, desc, checksum, change, private) "
        "VALUES (?,?,?,'/test.jpg','image/jpeg','Test','abc',0,0)",
        (handle, gramps_id, json.dumps(data)),
    )
    conn.commit()


# ===========================================================================
# add_event_to_person
# ===========================================================================

class TestAddEventToPerson:
    def test_event_appended_to_person(self, fresh_db):
        from gramps_mcp.tools.link_edit import add_event_to_person_tool

        db, conn = fresh_db
        _insert_person(conn, "h_pe", "I0001")
        _insert_event(conn, "h_ev", "E0001", 42)  # custom event type

        result = asyncio.get_event_loop().run_until_complete(
            add_event_to_person_tool(
                person_handle="h_pe", event_handle="h_ev", role="Witness", db=db
            )
        )
        data = json.loads(result)
        assert data["result"] == "ok"
        assert data["event_ref_count"] == 1

        row = conn.execute(
            "SELECT json_data FROM person WHERE handle = 'h_pe'"
        ).fetchone()
        person_data = json.loads(row["json_data"])
        refs = [e["ref"] for e in person_data.get("event_ref_list", [])]
        assert "h_ev" in refs

    def test_birth_ref_index_set_when_birth_event_added(self, fresh_db):
        from gramps_mcp.tools.link_edit import add_event_to_person_tool

        db, conn = fresh_db
        _insert_person(conn, "h_pe", "I0001")
        _insert_event(conn, "h_ev_birth", "E0001", 12)  # Birth = 12

        asyncio.get_event_loop().run_until_complete(
            add_event_to_person_tool(
                person_handle="h_pe", event_handle="h_ev_birth", role="Primary", db=db
            )
        )

        row = conn.execute(
            "SELECT birth_ref_index FROM person WHERE handle = 'h_pe'"
        ).fetchone()
        assert row["birth_ref_index"] == 0

    def test_no_duplicate_on_double_call(self, fresh_db):
        from gramps_mcp.tools.link_edit import add_event_to_person_tool

        db, conn = fresh_db
        _insert_person(conn, "h_pe", "I0001")
        _insert_event(conn, "h_ev", "E0001", 42)

        loop = asyncio.get_event_loop()
        loop.run_until_complete(
            add_event_to_person_tool(person_handle="h_pe", event_handle="h_ev", db=db)
        )
        result = loop.run_until_complete(
            add_event_to_person_tool(person_handle="h_pe", event_handle="h_ev", db=db)
        )
        data = json.loads(result)
        assert data["result"] == "no_change"

        row = conn.execute(
            "SELECT json_data FROM person WHERE handle = 'h_pe'"
        ).fetchone()
        person_data = json.loads(row["json_data"])
        assert len(person_data.get("event_ref_list", [])) == 1  # still just one

    def test_error_on_unknown_person_handle(self, fresh_db):
        from gramps_mcp.tools.link_edit import add_event_to_person_tool

        db, conn = fresh_db
        _insert_event(conn, "h_ev", "E0001", 42)

        with pytest.raises(GrampsAPIError, match="Person.*not found"):
            asyncio.get_event_loop().run_until_complete(
                add_event_to_person_tool(
                    person_handle="nonexistent", event_handle="h_ev", db=db
                )
            )

    def test_error_on_unknown_event_handle(self, fresh_db):
        from gramps_mcp.tools.link_edit import add_event_to_person_tool

        db, conn = fresh_db
        _insert_person(conn, "h_pe", "I0001")

        with pytest.raises(GrampsAPIError, match="Event.*not found"):
            asyncio.get_event_loop().run_until_complete(
                add_event_to_person_tool(
                    person_handle="h_pe", event_handle="nonexistent", db=db
                )
            )
```

- [ ] **Step 4.2 — Run the add_event_to_person tests to confirm they fail**

```
uv run pytest tests/test_link_edit_tools.py::TestAddEventToPerson -v
```

Expected: ImportError or AttributeError (module not yet created).

- [ ] **Step 4.3 — Create `tools/link_edit.py` with shared helpers and `add_event_to_person_tool`**

```python
# src/gramps_mcp/tools/link_edit.py
"""
Link-edit MCP tools — SQLite-only tools for manipulating links between existing
Gramps objects without replacing entire lists.

All tools require the SQLite backend (GrampsSqliteClient). They manipulate raw
Gramps JSON directly to avoid the normalization round-trip and use injectable
db= parameters for testability (same pattern as delete_object_tool).
"""

import json
import time
from typing import Any, Dict, List, Optional, Tuple

from gramps_mcp._gramps_sqlite import GrampsSqliteDB, _compute_birth_death_indices
from gramps_mcp.client import GrampsAPIError

# Maps role name strings to EventRoleType int values
_EVENT_ROLE_MAP: Dict[str, int] = {
    "Unknown": -1, "Custom": 0, "Primary": 1, "Clergy": 2, "Celebrant": 3,
    "Aide": 4, "Bride": 5, "Groom": 6, "Witness": 7, "Family": 8,
    "Informant": 9, "Godparent": 10, "Father": 11, "Mother": 12,
    "Parent": 13, "Child": 14, "Multiple birth": 15, "Friend": 16,
    "Neighbor": 17, "Officiator": 18,
}

_OBJECT_TABLE: Dict[str, str] = {
    "person": "person",
    "family": "family",
}


def _read_object(conn: Any, table: str, handle: str, label: str) -> Dict:
    """
    Read and JSON-parse an object from the DB.

    Args:
        conn: Open sqlite3.Connection.
        table: Table name (e.g. 'person', 'family', 'event').
        handle: Object handle.
        label: Human-readable object label for error messages.

    Returns:
        Parsed Gramps JSON dict.

    Raises:
        GrampsAPIError: If handle not found.
    """
    row = conn.execute(
        f"SELECT json_data FROM {table} WHERE handle = ?",  # noqa: S608
        (handle,),
    ).fetchone()
    if not row:
        raise GrampsAPIError(f"{label} with handle '{handle}' not found")
    return json.loads(row[0])


def _write_person(conn: Any, handle: str, person_data: Dict) -> None:
    """
    Write person JSON back to DB, auto-computing birth/death ref indices.

    Args:
        conn: Open sqlite3.Connection within an active transaction.
        handle: Person handle.
        person_data: Gramps JSON dict (mutated in place: sets change, indices).
    """
    person_data["change"] = int(time.time())
    birth_idx, death_idx = _compute_birth_death_indices(
        conn, person_data.get("event_ref_list", [])
    )
    person_data["birth_ref_index"] = birth_idx
    person_data["death_ref_index"] = death_idx
    conn.execute(
        "UPDATE person SET json_data=?, birth_ref_index=?, death_ref_index=?, change=? "
        "WHERE handle=?",  # noqa: S608
        (json.dumps(person_data, ensure_ascii=False), birth_idx, death_idx,
         person_data["change"], handle),
    )


def _write_object(conn: Any, table: str, handle: str, data: Dict) -> None:
    """
    Write any non-person object JSON back to DB, updating change timestamp.

    Args:
        conn: Open sqlite3.Connection within an active transaction.
        table: Table name (e.g. 'family').
        handle: Object handle.
        data: Gramps JSON dict (mutated in place: sets change).
    """
    data["change"] = int(time.time())
    conn.execute(
        f"UPDATE {table} SET json_data=?, change=? WHERE handle=?",  # noqa: S608
        (json.dumps(data, ensure_ascii=False), data["change"], handle),
    )


def _require_sqlite_db(db: Any, tool_name: str) -> GrampsSqliteDB:
    """
    Return a validated GrampsSqliteDB, fetching from get_client() if db is None.

    Args:
        db: Injected GrampsSqliteDB instance (None in production).
        tool_name: Tool name for error messages.

    Returns:
        GrampsSqliteDB instance.

    Raises:
        GrampsAPIError: If backend is not SQLite.
    """
    if db is None:
        from gramps_mcp.client import get_client
        from gramps_mcp.sqlite_client import GrampsSqliteClient

        client = get_client()
        if not isinstance(client, GrampsSqliteClient):
            raise GrampsAPIError(
                f"{tool_name} is SQLite-only; Web backend is not supported"
            )
        db = client._db

    if not isinstance(db, GrampsSqliteDB):
        raise GrampsAPIError(
            f"{tool_name} is SQLite-only; Web backend is not supported"
        )
    return db


# ---------------------------------------------------------------------------
# Tool 1: add_event_to_person
# ---------------------------------------------------------------------------


async def add_event_to_person_tool(
    person_handle: str,
    event_handle: str,
    role: str = "Primary",
    db: Any = None,
) -> str:
    """
    Append an event reference to a person's event_ref_list without replacing it.

    Automatically updates birth_ref_index and death_ref_index if a Birth or
    Death event is added. SQLite backend only.

    Args:
        person_handle: Handle of the person.
        event_handle: Handle of the event to link (must already exist).
        role: Role of the person in the event (default: 'Primary').
        db: GrampsSqliteDB instance (injected for tests; None uses get_client()).

    Returns:
        JSON string with result, person_handle, event_handle, event_ref_count,
        birth_ref_index, death_ref_index.

    Raises:
        GrampsAPIError: If backend is not SQLite or handles not found.
    """
    db = _require_sqlite_db(db, "add_event_to_person")
    conn = db._conn

    person_data = _read_object(conn, "person", person_handle, "Person")

    if not conn.execute(
        "SELECT handle FROM event WHERE handle = ?", (event_handle,)  # noqa: S608
    ).fetchone():
        raise GrampsAPIError(f"Event with handle '{event_handle}' not found")

    event_ref_list = person_data.get("event_ref_list", [])

    # Check for duplicate
    existing_handles = [
        e.get("ref") for e in event_ref_list if isinstance(e, dict)
    ]
    if event_handle in existing_handles:
        return json.dumps(
            {
                "result": "no_change",
                "message": f"Event '{event_handle}' is already linked to this person.",
            },
            ensure_ascii=False,
        )

    role_value = _EVENT_ROLE_MAP.get(role, 1)
    new_ref = {
        "_class": "EventRef",
        "ref": event_handle,
        "role": {"_class": "EventRoleType", "value": role_value, "string": ""},
        "private": False,
        "note_list": [],
        "attribute_list": [],
    }
    event_ref_list.append(new_ref)
    person_data["event_ref_list"] = event_ref_list

    with conn:
        _write_person(conn, person_handle, person_data)

    return json.dumps(
        {
            "result": "ok",
            "person_handle": person_handle,
            "event_handle": event_handle,
            "role": role,
            "event_ref_count": len(event_ref_list),
            "birth_ref_index": person_data.get("birth_ref_index", -1),
            "death_ref_index": person_data.get("death_ref_index", -1),
        },
        ensure_ascii=False,
    )
```

- [ ] **Step 4.4 — Run `add_event_to_person` tests**

```
uv run pytest tests/test_link_edit_tools.py::TestAddEventToPerson -v
```

Expected: all 5 tests PASS.

- [ ] **Step 4.5 — Run full suite**

```
uv run pytest -x -q
```

Expected: no failures.

- [ ] **Step 4.6 — Commit**

```
git add src/gramps_mcp/tools/link_edit.py tests/test_link_edit_tools.py
git commit -m "feat: add add_event_to_person MCP tool (SQLite-only)"
```

---

## Task 5: `remove_child_from_family` tool

**Files:**
- Modify: `src/gramps_mcp/tools/link_edit.py`
- Modify: `tests/test_link_edit_tools.py`

- [ ] **Step 5.1 — Add tests to `tests/test_link_edit_tools.py`**

Append to the file:

```python
# ===========================================================================
# remove_child_from_family
# ===========================================================================

class TestRemoveChildFromFamily:
    def test_child_removed_from_family_child_ref_list(self, fresh_db):
        from gramps_mcp.tools.link_edit import remove_child_from_family_tool

        db, conn = fresh_db
        _insert_person(conn, "h_child", "I0001", parent_family_list=["h_fam"])
        _insert_family(conn, "h_fam", "F0001", child_ref_list=[
            {"_class": "ChildRef", "ref": "h_child",
             "frel": {"_class": "ChildRefType", "value": 1, "string": ""},
             "mrel": {"_class": "ChildRefType", "value": 1, "string": ""},
             "private": False, "citation_list": [], "note_list": []}
        ])

        result = asyncio.get_event_loop().run_until_complete(
            remove_child_from_family_tool(
                family_handle="h_fam", child_handle="h_child", db=db
            )
        )
        data = json.loads(result)
        assert data["result"] == "ok"
        assert data["remaining_children"] == 0

        row = conn.execute(
            "SELECT json_data FROM family WHERE handle = 'h_fam'"
        ).fetchone()
        family_data = json.loads(row["json_data"])
        refs = [cr["ref"] for cr in family_data.get("child_ref_list", [])]
        assert "h_child" not in refs

    def test_parent_family_list_cleaned_from_child(self, fresh_db):
        from gramps_mcp.tools.link_edit import remove_child_from_family_tool

        db, conn = fresh_db
        _insert_person(conn, "h_child", "I0001", parent_family_list=["h_fam"])
        _insert_family(conn, "h_fam", "F0001", child_ref_list=[
            {"_class": "ChildRef", "ref": "h_child",
             "frel": {"_class": "ChildRefType", "value": 1, "string": ""},
             "mrel": {"_class": "ChildRefType", "value": 1, "string": ""},
             "private": False, "citation_list": [], "note_list": []}
        ])

        asyncio.get_event_loop().run_until_complete(
            remove_child_from_family_tool(
                family_handle="h_fam", child_handle="h_child", db=db
            )
        )

        row = conn.execute(
            "SELECT json_data FROM person WHERE handle = 'h_child'"
        ).fetchone()
        child_data = json.loads(row["json_data"])
        assert "h_fam" not in child_data.get("parent_family_list", [])

    def test_error_when_child_not_in_family(self, fresh_db):
        from gramps_mcp.tools.link_edit import remove_child_from_family_tool

        db, conn = fresh_db
        _insert_person(conn, "h_child", "I0001")
        _insert_family(conn, "h_fam", "F0001")  # empty child_ref_list

        with pytest.raises(GrampsAPIError, match="not in family"):
            asyncio.get_event_loop().run_until_complete(
                remove_child_from_family_tool(
                    family_handle="h_fam", child_handle="h_child", db=db
                )
            )
```

- [ ] **Step 5.2 — Run remove tests to confirm they fail**

```
uv run pytest tests/test_link_edit_tools.py::TestRemoveChildFromFamily -v
```

Expected: FAIL (function not defined).

- [ ] **Step 5.3 — Add `remove_child_from_family_tool` to `tools/link_edit.py`**

Append to the file:

```python
# ---------------------------------------------------------------------------
# Tool 2: remove_child_from_family
# ---------------------------------------------------------------------------


async def remove_child_from_family_tool(
    family_handle: str,
    child_handle: str,
    db: Any = None,
) -> str:
    """
    Remove a child from a family and clean up the child's parent_family_list.

    Removes the child's entry from child_ref_list of the family, then removes
    family_handle from the child person's parent_family_list. Both writes happen
    in a single SQLite transaction. SQLite backend only.

    Args:
        family_handle: Handle of the family.
        child_handle: Handle of the child person to remove.
        db: GrampsSqliteDB instance (injected for tests; None uses get_client()).

    Returns:
        JSON string with result, family_handle, child_handle, remaining_children.

    Raises:
        GrampsAPIError: If backend is not SQLite, handles not found, or child
                        not in the family.
    """
    db = _require_sqlite_db(db, "remove_child_from_family")
    conn = db._conn

    family_data = _read_object(conn, "family", family_handle, "Family")
    child_ref_list = family_data.get("child_ref_list", [])

    new_child_refs = [
        cr for cr in child_ref_list
        if not (isinstance(cr, dict) and cr.get("ref") == child_handle)
    ]
    if len(new_child_refs) == len(child_ref_list):
        raise GrampsAPIError(
            f"Child '{child_handle}' is not in family '{family_handle}'"
        )

    family_data["child_ref_list"] = new_child_refs

    child_data = _read_object(conn, "person", child_handle, "Child person")
    pfl = child_data.get("parent_family_list", [])
    child_data["parent_family_list"] = [h for h in pfl if h != family_handle]

    with conn:
        _write_object(conn, "family", family_handle, family_data)
        _write_person(conn, child_handle, child_data)

    return json.dumps(
        {
            "result": "ok",
            "family_handle": family_handle,
            "child_handle": child_handle,
            "remaining_children": len(new_child_refs),
        },
        ensure_ascii=False,
    )
```

- [ ] **Step 5.4 — Run tests**

```
uv run pytest tests/test_link_edit_tools.py::TestRemoveChildFromFamily -v
```

Expected: all 3 tests PASS.

- [ ] **Step 5.5 — Run full suite**

```
uv run pytest -x -q
```

Expected: no failures.

- [ ] **Step 5.6 — Commit**

```
git add src/gramps_mcp/tools/link_edit.py tests/test_link_edit_tools.py
git commit -m "feat: add remove_child_from_family MCP tool (SQLite-only)"
```

---

## Task 6: `move_attachment` tool

**Files:**
- Modify: `src/gramps_mcp/tools/link_edit.py`
- Modify: `tests/test_link_edit_tools.py`

- [ ] **Step 6.1 — Add tests to `tests/test_link_edit_tools.py`**

Append to the file:

```python
# ===========================================================================
# move_attachment
# ===========================================================================

class TestMoveAttachment:
    def test_note_moved_person_to_person(self, fresh_db):
        from gramps_mcp.tools.link_edit import move_attachment_tool

        db, conn = fresh_db
        _insert_note(conn, "h_note", "N0001")
        _insert_person(conn, "h_src", "I0001", note_list=["h_note"])
        _insert_person(conn, "h_dst", "I0002")

        result = asyncio.get_event_loop().run_until_complete(
            move_attachment_tool(
                attachment_type="note", handle="h_note",
                from_handle="h_src", from_type="person",
                to_handle="h_dst", to_type="person",
                db=db,
            )
        )
        data = json.loads(result)
        assert data["result"] == "ok"

        src_row = conn.execute(
            "SELECT json_data FROM person WHERE handle = 'h_src'"
        ).fetchone()
        dst_row = conn.execute(
            "SELECT json_data FROM person WHERE handle = 'h_dst'"
        ).fetchone()
        src_data = json.loads(src_row["json_data"])
        dst_data = json.loads(dst_row["json_data"])
        assert "h_note" not in src_data.get("note_list", [])
        assert "h_note" in dst_data.get("note_list", [])

    def test_note_moved_person_to_family(self, fresh_db):
        from gramps_mcp.tools.link_edit import move_attachment_tool

        db, conn = fresh_db
        _insert_note(conn, "h_note", "N0001")
        _insert_person(conn, "h_src", "I0001", note_list=["h_note"])
        _insert_family(conn, "h_fam", "F0001")

        asyncio.get_event_loop().run_until_complete(
            move_attachment_tool(
                attachment_type="note", handle="h_note",
                from_handle="h_src", from_type="person",
                to_handle="h_fam", to_type="family",
                db=db,
            )
        )

        src_row = conn.execute(
            "SELECT json_data FROM person WHERE handle = 'h_src'"
        ).fetchone()
        dst_row = conn.execute(
            "SELECT json_data FROM family WHERE handle = 'h_fam'"
        ).fetchone()
        src_data = json.loads(src_row["json_data"])
        dst_data = json.loads(dst_row["json_data"])
        assert "h_note" not in src_data.get("note_list", [])
        assert "h_note" in dst_data.get("note_list", [])

    def test_media_moved_person_to_person(self, fresh_db):
        from gramps_mcp.tools.link_edit import move_attachment_tool

        db, conn = fresh_db
        _insert_media(conn, "h_media", "O0001")
        _insert_person(conn, "h_src", "I0001", media_list=["h_media"])
        _insert_person(conn, "h_dst", "I0002")

        asyncio.get_event_loop().run_until_complete(
            move_attachment_tool(
                attachment_type="media", handle="h_media",
                from_handle="h_src", from_type="person",
                to_handle="h_dst", to_type="person",
                db=db,
            )
        )

        src_row = conn.execute(
            "SELECT json_data FROM person WHERE handle = 'h_src'"
        ).fetchone()
        dst_row = conn.execute(
            "SELECT json_data FROM person WHERE handle = 'h_dst'"
        ).fetchone()
        src_data = json.loads(src_row["json_data"])
        dst_data = json.loads(dst_row["json_data"])
        assert "h_media" not in src_data.get("media_list", [])
        assert "h_media" in dst_data.get("media_list", [])

    def test_error_when_attachment_not_in_source(self, fresh_db):
        from gramps_mcp.tools.link_edit import move_attachment_tool

        db, conn = fresh_db
        _insert_note(conn, "h_note", "N0001")
        _insert_person(conn, "h_src", "I0001")  # note_list is empty
        _insert_person(conn, "h_dst", "I0002")

        with pytest.raises(GrampsAPIError, match="not in"):
            asyncio.get_event_loop().run_until_complete(
                move_attachment_tool(
                    attachment_type="note", handle="h_note",
                    from_handle="h_src", from_type="person",
                    to_handle="h_dst", to_type="person",
                    db=db,
                )
            )
```

- [ ] **Step 6.2 — Run move tests to confirm they fail**

```
uv run pytest tests/test_link_edit_tools.py::TestMoveAttachment -v
```

Expected: FAIL (function not defined).

- [ ] **Step 6.3 — Add `move_attachment_tool` to `tools/link_edit.py`**

Append to the file:

```python
# ---------------------------------------------------------------------------
# Tool 3: move_attachment
# ---------------------------------------------------------------------------


async def move_attachment_tool(
    attachment_type: str,
    handle: str,
    from_handle: str,
    from_type: str = "person",
    to_handle: str = "",
    to_type: str = "person",
    db: Any = None,
) -> str:
    """
    Atomically move a note or media reference from one object to another.

    Removes handle from source's note_list/media_list and adds it to target's
    list. Both writes happen in a single SQLite transaction. SQLite backend only.

    Args:
        attachment_type: 'note' or 'media'.
        handle: Handle of the note or media object.
        from_handle: Handle of the source object.
        from_type: 'person' or 'family' (default: 'person').
        to_handle: Handle of the target object.
        to_type: 'person' or 'family' (default: 'person').
        db: GrampsSqliteDB instance (injected for tests; None uses get_client()).

    Returns:
        JSON string with result and move details.

    Raises:
        GrampsAPIError: If backend is not SQLite, handles not found, or
                        attachment not in source object.
    """
    db = _require_sqlite_db(db, "move_attachment")
    conn = db._conn

    attachment_table = "note" if attachment_type == "note" else "media"
    list_key = "note_list" if attachment_type == "note" else "media_list"

    # Verify the attachment object exists
    if not conn.execute(
        f"SELECT handle FROM {attachment_table} WHERE handle = ?",  # noqa: S608
        (handle,),
    ).fetchone():
        raise GrampsAPIError(
            f"{attachment_type.title()} with handle '{handle}' not found"
        )

    from_data = _read_object(conn, from_type, from_handle, f"Source {from_type}")
    from_list = from_data.get(list_key, [])
    if handle not in from_list:
        raise GrampsAPIError(
            f"{attachment_type.title()} '{handle}' is not in "
            f"{from_type} '{from_handle}'"
        )
    from_data[list_key] = [h for h in from_list if h != handle]

    to_data = _read_object(conn, to_type, to_handle, f"Target {to_type}")
    to_list = to_data.get(list_key, [])
    if handle not in to_list:
        to_list.append(handle)
    to_data[list_key] = to_list

    with conn:
        if from_type == "person":
            _write_person(conn, from_handle, from_data)
        else:
            _write_object(conn, "family", from_handle, from_data)

        if to_type == "person":
            _write_person(conn, to_handle, to_data)
        else:
            _write_object(conn, "family", to_handle, to_data)

    return json.dumps(
        {
            "result": "ok",
            "attachment_type": attachment_type,
            "handle": handle,
            "from": {"type": from_type, "handle": from_handle},
            "to": {"type": to_type, "handle": to_handle},
        },
        ensure_ascii=False,
    )
```

- [ ] **Step 6.4 — Run all link_edit tests**

```
uv run pytest tests/test_link_edit_tools.py -v
```

Expected: all tests PASS.

- [ ] **Step 6.5 — Run full suite**

```
uv run pytest -x -q
```

Expected: no failures.

- [ ] **Step 6.6 — Commit**

```
git add src/gramps_mcp/tools/link_edit.py tests/test_link_edit_tools.py
git commit -m "feat: add move_attachment MCP tool (SQLite-only)"
```

---

## Task 7: Server registration

**Files:**
- Modify: `src/gramps_mcp/server.py`

- [ ] **Step 7.1 — Add import of the three tools in `server.py`**

After the existing `from .tools.delete import delete_object_tool` line, add:

```python
from .tools.link_edit import (
    add_event_to_person_tool,
    move_attachment_tool,
    remove_child_from_family_tool,
)
```

- [ ] **Step 7.2 — Add parameter model imports in `server.py`**

After the existing `from .models.parameters.delete_params import DeleteObjectParams` line (or grouped with the other model imports at the top), add:

```python
from .models.parameters.link_edit_params import (
    AddEventToPersonParams,
    MoveAttachmentParams,
    RemoveChildFromFamilyParams,
)
```

- [ ] **Step 7.3 — Add TOOL_REGISTRY entries in `server.py`**

Add these three entries at the end of `TOOL_REGISTRY` (before the closing `}`), after the `"delete_object"` entry:

```python
    "add_event_to_person": {
        "description": (
            "Append an event reference to a person's event_ref_list without replacing it. "
            "Automatically updates birth_ref_index and death_ref_index when a Birth or "
            "Death event is added. Use this instead of create_person when you want to add "
            "a single event and preserve existing event links. SQLite backend only."
        ),
        "schema": AddEventToPersonParams,
        "handler": lambda args: add_event_to_person_tool(
            person_handle=args["person_handle"],
            event_handle=args["event_handle"],
            role=args.get("role", "Primary"),
        ),
    },
    "remove_child_from_family": {
        "description": (
            "Remove a child from a family and clean up the child's parent_family_list. "
            "Both the family's child_ref_list and the child person's parent_family_list "
            "are updated in a single transaction. SQLite backend only."
        ),
        "schema": RemoveChildFromFamilyParams,
        "handler": lambda args: remove_child_from_family_tool(
            family_handle=args["family_handle"],
            child_handle=args["child_handle"],
        ),
    },
    "move_attachment": {
        "description": (
            "Move a note or media reference from one object to another atomically. "
            "Removes the handle from the source's note_list/media_list and adds it "
            "to the target's list in a single transaction. Use for correcting GEDCOM "
            "import errors where notes/media landed on the wrong person. "
            "Supports person→person and person→family moves. SQLite backend only."
        ),
        "schema": MoveAttachmentParams,
        "handler": lambda args: move_attachment_tool(
            attachment_type=args["attachment_type"],
            handle=args["handle"],
            from_handle=args["from_handle"],
            from_type=args.get("from_type", "person"),
            to_handle=args["to_handle"],
            to_type=args.get("to_type", "person"),
        ),
    },
```

- [ ] **Step 7.4 — Verify server starts without errors**

```
uv run python -c "from gramps_mcp.server import app, TOOL_REGISTRY; print(list(TOOL_REGISTRY.keys()))"
```

Expected: the output list includes `add_event_to_person`, `remove_child_from_family`, `move_attachment`.

- [ ] **Step 7.5 — Run full test suite one final time**

```
uv run pytest -x -q
```

Expected: all tests pass, no failures.

- [ ] **Step 7.6 — Final commit**

```
git add src/gramps_mcp/server.py
git commit -m "feat: register add_event_to_person, remove_child_from_family, move_attachment in MCP server"
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Task |
|-----------------|------|
| birth_ref_index/death_ref_index auto-update | Task 1 |
| Only fires when event_ref_list in patch | Task 1, Fix A applies to every person put (correct — recalculates from stored data) |
| parent_family_list update only when child_handles in patch | Task 2, Fix B condition `"child_handles" in obj or "child_ref_list" in obj` |
| add_event_to_person tool | Task 4 |
| remove_child_from_family tool | Task 5 |
| move_attachment tool (person→person, person→family) | Task 6 |
| SQLite-only enforcement | Tasks 4/5/6 via `_require_sqlite_db` |
| Tests for all error paths | Tasks 4/5/6 tests include unknown-handle and wrong-source tests |
| Server registration | Task 7 |

**Placeholder check:** None found.

**Type consistency check:** `_write_person` and `_write_object` are defined once in Task 4 and reused in Tasks 5 and 6 unchanged. `_require_sqlite_db` is defined once and reused in all three tools.
