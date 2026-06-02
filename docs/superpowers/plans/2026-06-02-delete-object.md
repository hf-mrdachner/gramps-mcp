# delete_object Tool — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a single MCP tool `delete_object` that deletes any Gramps object by type/handle, cascades to orphaned events, and unlinks back-references in related objects — with a dry-run mode that returns a human-readable summary before committing.

**Architecture:** A new module `src/gramps_mcp/tools/delete.py` contains all logic: `_label()` builds human-readable labels from raw DB JSON, `_find_references()` scans tables via LIKE query to find back-references, `_cascade_plan()` assembles a dry-run result, `_execute_cascade()` runs everything in one SQLite transaction. The MCP tool `delete_object_tool()` ties it together and is registered in `server.py`. SQLite-only; Web backend raises `GrampsAPIError`.

**Tech Stack:** Python 3.11+, sqlite3, dataclasses, json, pytest, existing `_gramps_sqlite._TABLE` / `GrampsSqliteDB`, `GrampsAPIError` from `client.py`.

---

## File Map

| Action | File |
|--------|------|
| Create | `src/gramps_mcp/tools/delete.py` |
| Create | `tests/test_delete_tool.py` |
| Modify | `src/gramps_mcp/server.py` (1 import + 1 registry entry) |

---

## Task 1: Data structures and `_label()` helper

**Files:**
- Create: `src/gramps_mcp/tools/delete.py`
- Create: `tests/test_delete_tool.py`

- [ ] **Step 1: Write the failing tests for `_label()`**

Add `tests/test_delete_tool.py`:

```python
"""
Tests for delete_object tool — cascade delete with dry-run support.
Uses fresh in-memory SQLite DBs (session-scoped fixture would be mutated).
"""

import json
import sqlite3

import pytest

from gramps_mcp._gramps_sqlite import GrampsSqliteDB

# ---------------------------------------------------------------------------
# Minimal schema (same as conftest_sqlite._SCHEMA)
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE person (
    handle VARCHAR(50) PRIMARY KEY NOT NULL,
    given_name TEXT, surname TEXT,
    json_data TEXT, gramps_id TEXT, gender INTEGER,
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
CREATE TABLE place (
    handle VARCHAR(50) PRIMARY KEY NOT NULL,
    json_data TEXT, gramps_id TEXT,
    title TEXT, long TEXT, lat TEXT, code TEXT, enclosed_by VARCHAR(50),
    change INTEGER DEFAULT 0, private INTEGER DEFAULT 0
);
CREATE TABLE source (
    handle VARCHAR(50) PRIMARY KEY NOT NULL,
    json_data TEXT, gramps_id TEXT, title TEXT, author TEXT,
    pubinfo TEXT, abbrev TEXT, change INTEGER DEFAULT 0, private INTEGER DEFAULT 0
);
CREATE TABLE citation (
    handle VARCHAR(50) PRIMARY KEY NOT NULL,
    json_data TEXT, gramps_id TEXT, page TEXT, confidence INTEGER DEFAULT 2,
    source_handle VARCHAR(50), change INTEGER DEFAULT 0, private INTEGER DEFAULT 0
);
CREATE TABLE note (
    handle VARCHAR(50) PRIMARY KEY NOT NULL,
    json_data TEXT, gramps_id TEXT, format INTEGER DEFAULT 0,
    change INTEGER DEFAULT 0, private INTEGER DEFAULT 0
);
CREATE TABLE media (
    handle VARCHAR(50) PRIMARY KEY NOT NULL,
    json_data TEXT, gramps_id TEXT, path TEXT, mime TEXT, desc TEXT, checksum TEXT,
    change INTEGER DEFAULT 0, private INTEGER DEFAULT 0
);
CREATE TABLE repository (
    handle VARCHAR(50) PRIMARY KEY NOT NULL,
    json_data TEXT, gramps_id TEXT, name TEXT,
    change INTEGER DEFAULT 0, private INTEGER DEFAULT 0
);
CREATE TABLE tag (
    handle VARCHAR(50) PRIMARY KEY NOT NULL,
    json_data TEXT, name TEXT, color VARCHAR(13),
    priority INTEGER DEFAULT 0, change INTEGER DEFAULT 0
);
CREATE TABLE reference (
    obj_handle VARCHAR(50), obj_class TEXT,
    ref_handle VARCHAR(50), ref_class TEXT
);
CREATE TABLE metadata (
    setting VARCHAR(50) PRIMARY KEY NOT NULL,
    json_data TEXT, value BLOB
);
"""


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def _insert(conn, table, handle, gramps_id, data, **extra):
    data = {"handle": handle, "gramps_id": gramps_id, **data}
    cols = ["handle", "gramps_id", "json_data"] + list(extra.keys())
    vals = [handle, gramps_id, json.dumps(data)] + list(extra.values())
    ph = ",".join("?" * len(cols))
    conn.execute(f"INSERT INTO {table} ({','.join(cols)}) VALUES ({ph})", vals)
    conn.commit()


# ---------------------------------------------------------------------------
# Label tests
# ---------------------------------------------------------------------------


class TestLabel:
    def test_person_label(self):
        from gramps_mcp.tools.delete import _label
        conn = _make_conn()
        _insert(conn, "person", "h_john", "I0001", {
            "_class": "Person",
            "primary_name": {
                "_class": "Name",
                "first_name": "John",
                "surname_list": [{"_class": "Surname", "surname": "Smith"}],
            },
        })
        assert _label(conn, "person", "h_john") == "John Smith (I0001)"

    def test_event_label_with_year(self):
        from gramps_mcp.tools.delete import _label
        conn = _make_conn()
        _insert(conn, "event", "h_ev", "E0001", {
            "_class": "Event",
            "type": {"_class": "EventType", "value": 12, "string": ""},
            "date": {"_class": "Date", "dateval": [15, 6, 1950, False]},
        })
        label = _label(conn, "event", "h_ev")
        assert "1950" in label
        assert "Birth" in label

    def test_place_label(self):
        from gramps_mcp.tools.delete import _label
        conn = _make_conn()
        _insert(conn, "place", "h_pl", "P0001", {
            "_class": "Place", "title": "Berlin, Germany",
        })
        assert _label(conn, "place", "h_pl") == "Berlin, Germany (P0001)"

    def test_unknown_handle_returns_handle(self):
        from gramps_mcp.tools.delete import _label
        conn = _make_conn()
        assert _label(conn, "person", "nonexistent") == "nonexistent"
```

- [ ] **Step 2: Run tests to verify they fail**

```
uv run pytest tests/test_delete_tool.py::TestLabel -v
```

Expected: `ImportError: cannot import name '_label' from 'gramps_mcp.tools.delete'`

- [ ] **Step 3: Create `src/gramps_mcp/tools/delete.py` with data structures and `_label()`**

```python
"""
delete_object MCP tool — cascade-safe deletion for Gramps SQLite databases.

Supports dry-run mode: confirmed=False returns a human-readable summary of
what would be deleted/unlinked without touching the database.
SQLite backend only; raises GrampsAPIError for Web backend.
"""

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List

from gramps_mcp._gramps_sqlite import _TABLE, _TYPE_MAPS
from gramps_mcp.client import GrampsAPIError

# ---------------------------------------------------------------------------
# EventType int → display string (for labels)
# ---------------------------------------------------------------------------

_EVENT_TYPE_STR: Dict[int, str] = _TYPE_MAPS.get("EventType", {})

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class DeleteEntry:
    """One object that will be (or was) deleted."""

    obj_type: str
    handle: str
    label: str


@dataclass
class UnlinkEntry:
    """One field in one object that will have the deleted handle removed."""

    obj_type: str
    handle: str
    label: str
    field: str


@dataclass
class CascadeResult:
    """Full dry-run plan: what to delete and what to unlink."""

    to_delete: List[DeleteEntry] = field(default_factory=list)
    to_unlink: List[UnlinkEntry] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Label helper
# ---------------------------------------------------------------------------


def _label(conn: Any, obj_type: str, handle: str) -> str:
    """
    Return a human-readable label for any Gramps object.

    Reads raw Gramps JSON directly (not normalised) so it works without
    a GrampsSqliteDB instance.

    Args:
        conn: Open sqlite3.Connection.
        obj_type: One of the keys in _TABLE.
        handle: Gramps handle.

    Returns:
        str: Human-readable label, e.g. "John Smith (I0001)".
             Falls back to handle if object not found.
    """
    table = _TABLE.get(obj_type)
    if not table:
        return handle
    row = conn.execute(
        f"SELECT json_data, gramps_id FROM {table} WHERE handle = ?",  # noqa: S608
        (handle,),
    ).fetchone()
    if not row:
        return handle
    try:
        data = json.loads(row[0])
    except Exception:
        return handle
    gid = data.get("gramps_id") or (row[1] if row[1] else handle)

    if obj_type == "person":
        name = data.get("primary_name") or {}
        first = name.get("first_name", "")
        surnames = [
            s.get("surname", "")
            for s in name.get("surname_list", [])
            if isinstance(s, dict)
        ]
        surname = " ".join(s for s in surnames if s)
        full = f"{first} {surname}".strip()
        return f"{full} ({gid})" if full else gid

    if obj_type == "family":
        return f"Family {gid}"

    if obj_type == "event":
        raw_type = data.get("type") or {}
        if isinstance(raw_type, dict):
            val = raw_type.get("value", 0)
            type_str = _EVENT_TYPE_STR.get(val, raw_type.get("string", "Event"))
        else:
            type_str = str(raw_type)
        date = data.get("date") or {}
        dateval = date.get("dateval", []) if isinstance(date, dict) else []
        year = str(dateval[2]) if len(dateval) >= 3 and dateval[2] else ""
        return f"{type_str} {year}".strip() if year else type_str

    if obj_type == "place":
        title = data.get("title", gid)
        return f"{title} ({gid})"

    if obj_type == "tag":
        name = data.get("name", gid)
        return f"{name} ({gid})"

    return gid
```

- [ ] **Step 4: Run tests to verify they pass**

```
uv run pytest tests/test_delete_tool.py::TestLabel -v
```

Expected: 4 passed

- [ ] **Step 5: Commit**

```
git add src/gramps_mcp/tools/delete.py tests/test_delete_tool.py
git commit -m "feat(delete): add data structures and _label() helper"
```

---

## Task 2: `_find_references()` and `_remove_handle_from_json()`

**Files:**
- Modify: `src/gramps_mcp/tools/delete.py`
- Modify: `tests/test_delete_tool.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_delete_tool.py`:

```python
# ---------------------------------------------------------------------------
# _find_references tests
# ---------------------------------------------------------------------------


class TestFindReferences:
    def test_finds_father_handle_in_family(self):
        from gramps_mcp.tools.delete import _find_references
        conn = _make_conn()
        _insert(conn, "family", "h_fam", "F0001", {
            "_class": "Family",
            "father_handle": "h_john",
            "mother_handle": None,
            "child_ref_list": [],
        })
        refs = _find_references(conn, "h_john")
        assert any(obj_type == "family" and obj_handle == "h_fam"
                   for obj_type, obj_handle, _ in refs)

    def test_does_not_return_self(self):
        from gramps_mcp.tools.delete import _find_references
        conn = _make_conn()
        _insert(conn, "person", "h_john", "I0001", {
            "_class": "Person",
            "primary_name": {"first_name": "John", "surname_list": []},
            "event_ref_list": [{"_class": "EventRef", "ref": "h_ev"}],
        })
        refs = _find_references(conn, "h_john")
        assert not any(h == "h_john" for _, h, _ in refs)

    def test_no_references_returns_empty(self):
        from gramps_mcp.tools.delete import _find_references
        conn = _make_conn()
        assert _find_references(conn, "handle_nobody_knows") == []


# ---------------------------------------------------------------------------
# _remove_handle_from_json tests
# ---------------------------------------------------------------------------


class TestRemoveHandleFromJson:
    def test_removes_father_handle(self):
        from gramps_mcp.tools.delete import _remove_handle_from_json
        data = {"_class": "Family", "father_handle": "h_john", "mother_handle": "h_jane"}
        updated, affected = _remove_handle_from_json(data, "h_john")
        assert updated["father_handle"] is None
        assert updated["mother_handle"] == "h_jane"
        assert "father_handle" in affected

    def test_removes_from_event_ref_list(self):
        from gramps_mcp.tools.delete import _remove_handle_from_json
        data = {
            "_class": "Person",
            "event_ref_list": [
                {"_class": "EventRef", "ref": "h_ev1"},
                {"_class": "EventRef", "ref": "h_ev2"},
            ],
        }
        updated, affected = _remove_handle_from_json(data, "h_ev1")
        assert len(updated["event_ref_list"]) == 1
        assert updated["event_ref_list"][0]["ref"] == "h_ev2"
        assert "event_ref_list" in affected

    def test_removes_from_plain_note_list(self):
        from gramps_mcp.tools.delete import _remove_handle_from_json
        data = {"note_list": ["h_note1", "h_note2"]}
        updated, affected = _remove_handle_from_json(data, "h_note1")
        assert updated["note_list"] == ["h_note2"]
        assert "note_list" in affected

    def test_no_match_returns_empty_affected(self):
        from gramps_mcp.tools.delete import _remove_handle_from_json
        data = {"father_handle": "someone_else", "note_list": ["h_other"]}
        updated, affected = _remove_handle_from_json(data, "h_john")
        assert affected == []
        assert updated == data
```

- [ ] **Step 2: Run tests to verify they fail**

```
uv run pytest tests/test_delete_tool.py::TestFindReferences tests/test_delete_tool.py::TestRemoveHandleFromJson -v
```

Expected: `ImportError: cannot import name '_find_references'`

- [ ] **Step 3: Implement `_find_references()` and `_remove_handle_from_json()` in `delete.py`**

Append after `_label()`:

```python
# ---------------------------------------------------------------------------
# Reference scanning
# ---------------------------------------------------------------------------

# Object types to scan for back-references (all except the deleted type itself)
_ALL_TYPES = list(_TABLE.keys())


def _find_references(conn: Any, handle: str) -> List[tuple]:
    """
    Find all objects in the DB that contain handle in their json_data.

    Uses a LIKE query for speed (sufficient for genealogy-scale DBs), then
    returns raw rows for the caller to inspect. Does not return the object
    with this handle itself.

    Args:
        conn: Open sqlite3.Connection.
        handle: The handle to search for.

    Returns:
        List of (obj_type, obj_handle, json_data_str) tuples.
    """
    results = []
    for obj_type in _ALL_TYPES:
        table = _TABLE[obj_type]
        rows = conn.execute(
            f"SELECT handle, json_data FROM {table} WHERE json_data LIKE ?",  # noqa: S608
            (f"%{handle}%",),
        ).fetchall()
        for row_handle, json_data in rows:
            if row_handle != handle:
                results.append((obj_type, row_handle, json_data))
    return results


# ---------------------------------------------------------------------------
# JSON unlinking
# ---------------------------------------------------------------------------

# Fields that hold a single nullable handle
_NULLABLE_HANDLE_FIELDS = (
    "father_handle", "mother_handle", "place", "source_handle", "enclosed_by",
)

# List fields containing plain handle strings
_PLAIN_LIST_FIELDS = ("citation_list", "note_list", "tag_list")

# List fields containing dicts with a "ref" key
_REF_LIST_FIELDS = (
    "event_ref_list", "child_ref_list", "media_list", "placeref_list",
    "reporef_list", "person_ref_list",
)


def _remove_handle_from_json(
    data: Dict, handle: str
) -> tuple:
    """
    Remove all occurrences of handle from a Gramps JSON dict.

    Does not mutate the input — returns a shallow copy with changes applied.

    Args:
        data: Raw Gramps JSON dict.
        handle: Handle to remove.

    Returns:
        Tuple of (updated_dict, list_of_affected_field_names).
    """
    import copy

    data = copy.deepcopy(data)
    affected: List[str] = []

    for f in _NULLABLE_HANDLE_FIELDS:
        if data.get(f) == handle:
            data[f] = None
            affected.append(f)

    for f in _PLAIN_LIST_FIELDS:
        lst = data.get(f)
        if isinstance(lst, list):
            new = [h for h in lst if h != handle]
            if len(new) < len(lst):
                data[f] = new
                affected.append(f)

    for f in _REF_LIST_FIELDS:
        lst = data.get(f)
        if isinstance(lst, list):
            new = [
                item for item in lst
                if not (isinstance(item, dict) and item.get("ref") == handle)
            ]
            if len(new) < len(lst):
                data[f] = new
                affected.append(f)

    return data, affected
```

- [ ] **Step 4: Run tests to verify they pass**

```
uv run pytest tests/test_delete_tool.py::TestFindReferences tests/test_delete_tool.py::TestRemoveHandleFromJson -v
```

Expected: 7 passed

- [ ] **Step 5: Commit**

```
git add src/gramps_mcp/tools/delete.py tests/test_delete_tool.py
git commit -m "feat(delete): add _find_references and _remove_handle_from_json"
```

---

## Task 3: `_cascade_plan()` — dry-run logic

**Files:**
- Modify: `src/gramps_mcp/tools/delete.py`
- Modify: `tests/test_delete_tool.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_delete_tool.py`:

```python
# ---------------------------------------------------------------------------
# _cascade_plan tests
# ---------------------------------------------------------------------------


class TestCascadePlan:
    def _setup_person_with_events(self):
        """Person with two events, each owned exclusively by that person."""
        conn = _make_conn()
        _insert(conn, "event", "h_ev_birth", "E0001", {
            "_class": "Event",
            "type": {"_class": "EventType", "value": 12, "string": ""},
            "date": {"_class": "Date", "dateval": [1, 1, 1950, False]},
        })
        _insert(conn, "event", "h_ev_death", "E0002", {
            "_class": "Event",
            "type": {"_class": "EventType", "value": 13, "string": ""},
            "date": {"_class": "Date", "dateval": [1, 1, 2020, False]},
        })
        _insert(conn, "person", "h_john", "I0001", {
            "_class": "Person",
            "primary_name": {
                "first_name": "John",
                "surname_list": [{"surname": "Smith"}],
            },
            "event_ref_list": [
                {"_class": "EventRef", "ref": "h_ev_birth"},
                {"_class": "EventRef", "ref": "h_ev_death"},
            ],
        })
        return conn

    def test_plan_includes_primary_object(self):
        from gramps_mcp.tools.delete import _cascade_plan
        conn = self._setup_person_with_events()
        plan = _cascade_plan(conn, "person", "h_john")
        handles = [e.handle for e in plan.to_delete]
        assert "h_john" in handles

    def test_plan_cascades_orphaned_events(self):
        from gramps_mcp.tools.delete import _cascade_plan
        conn = self._setup_person_with_events()
        plan = _cascade_plan(conn, "person", "h_john")
        handles = [e.handle for e in plan.to_delete]
        assert "h_ev_birth" in handles
        assert "h_ev_death" in handles

    def test_shared_event_not_cascaded(self):
        """Event referenced by two persons must NOT appear in to_delete."""
        from gramps_mcp.tools.delete import _cascade_plan
        conn = _make_conn()
        _insert(conn, "event", "h_ev_shared", "E0001", {
            "_class": "Event",
            "type": {"_class": "EventType", "value": 1, "string": ""},
            "date": {"_class": "Date", "dateval": [1, 1, 1975, False]},
        })
        _insert(conn, "person", "h_john", "I0001", {
            "_class": "Person",
            "primary_name": {"first_name": "John", "surname_list": []},
            "event_ref_list": [{"_class": "EventRef", "ref": "h_ev_shared"}],
        })
        _insert(conn, "person", "h_jane", "I0002", {
            "_class": "Person",
            "primary_name": {"first_name": "Jane", "surname_list": []},
            "event_ref_list": [{"_class": "EventRef", "ref": "h_ev_shared"}],
        })
        plan = _cascade_plan(conn, "person", "h_john")
        handles = [e.handle for e in plan.to_delete]
        assert "h_ev_shared" not in handles

    def test_plan_records_family_unlink(self):
        """Person who is father in a family: family appears in to_unlink."""
        from gramps_mcp.tools.delete import _cascade_plan
        conn = _make_conn()
        _insert(conn, "person", "h_john", "I0001", {
            "_class": "Person",
            "primary_name": {"first_name": "John", "surname_list": []},
            "event_ref_list": [],
        })
        _insert(conn, "family", "h_fam", "F0001", {
            "_class": "Family",
            "father_handle": "h_john",
            "mother_handle": None,
            "child_ref_list": [],
            "event_ref_list": [],
        }, father_handle="h_john")
        plan = _cascade_plan(conn, "person", "h_john")
        unlink_handles = [e.handle for e in plan.to_unlink]
        assert "h_fam" in unlink_handles

    def test_dry_run_does_not_modify_db(self):
        from gramps_mcp.tools.delete import _cascade_plan
        conn = self._setup_person_with_events()
        _cascade_plan(conn, "person", "h_john")
        row = conn.execute("SELECT handle FROM person WHERE handle='h_john'").fetchone()
        assert row is not None
```

- [ ] **Step 2: Run tests to verify they fail**

```
uv run pytest tests/test_delete_tool.py::TestCascadePlan -v
```

Expected: `ImportError: cannot import name '_cascade_plan'`

- [ ] **Step 3: Implement `_cascade_plan()` in `delete.py`**

Append after `_remove_handle_from_json()`:

```python
# ---------------------------------------------------------------------------
# Cascade plan (dry-run)
# ---------------------------------------------------------------------------

# Object types whose events are "owned" — orphaned events get cascade-deleted
_OWNS_EVENTS: frozenset = frozenset({"person", "family"})


def _cascade_plan(conn: Any, obj_type: str, handle: str) -> CascadeResult:
    """
    Build a deletion plan without touching the database.

    Finds all back-references (objects to unlink) and orphaned owned
    dependents (events to cascade-delete) for the given object.

    Args:
        conn: Open sqlite3.Connection.
        obj_type: Type of the object to delete.
        handle: Handle of the object to delete.

    Returns:
        CascadeResult with to_delete and to_unlink populated.
    """
    result = CascadeResult()
    result.to_delete.append(
        DeleteEntry(obj_type=obj_type, handle=handle, label=_label(conn, obj_type, handle))
    )

    # --- Back-references: objects that reference this handle ---
    refs = _find_references(conn, handle)
    seen_unlink: set = set()
    for ref_obj_type, ref_handle, json_str in refs:
        try:
            json_data = json.loads(json_str)
        except Exception:
            continue
        _, affected = _remove_handle_from_json(json_data, handle)
        if not affected:
            continue
        lbl = _label(conn, ref_obj_type, ref_handle)
        for f in affected:
            key = (ref_handle, f)
            if key not in seen_unlink:
                seen_unlink.add(key)
                result.to_unlink.append(
                    UnlinkEntry(
                        obj_type=ref_obj_type, handle=ref_handle, label=lbl, field=f
                    )
                )

    # --- Orphan detection: events exclusively owned by this object ---
    if obj_type in _OWNS_EVENTS:
        table = _TABLE[obj_type]
        row = conn.execute(
            f"SELECT json_data FROM {table} WHERE handle = ?",  # noqa: S608
            (handle,),
        ).fetchone()
        if row:
            try:
                own_data = json.loads(row[0])
            except Exception:
                own_data = {}
            event_handles = [
                e["ref"]
                for e in own_data.get("event_ref_list", [])
                if isinstance(e, dict) and e.get("ref")
            ]
            for ev_handle in event_handles:
                # Check whether any other person/family references this event
                other_owners = [
                    (t, h)
                    for t, h, _ in _find_references(conn, ev_handle)
                    if t in _OWNS_EVENTS and h != handle
                ]
                if not other_owners:
                    result.to_delete.append(
                        DeleteEntry(
                            obj_type="event",
                            handle=ev_handle,
                            label=_label(conn, "event", ev_handle),
                        )
                    )

    return result
```

- [ ] **Step 4: Run tests to verify they pass**

```
uv run pytest tests/test_delete_tool.py::TestCascadePlan -v
```

Expected: 5 passed

- [ ] **Step 5: Commit**

```
git add src/gramps_mcp/tools/delete.py tests/test_delete_tool.py
git commit -m "feat(delete): add _cascade_plan dry-run logic"
```

---

## Task 4: `_build_summary()` and `_execute_cascade()`

**Files:**
- Modify: `src/gramps_mcp/tools/delete.py`
- Modify: `tests/test_delete_tool.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_delete_tool.py`:

```python
# ---------------------------------------------------------------------------
# _execute_cascade tests
# ---------------------------------------------------------------------------


class TestExecuteCascade:
    def _setup(self):
        conn = _make_conn()
        _insert(conn, "event", "h_ev_birth", "E0001", {
            "_class": "Event",
            "type": {"_class": "EventType", "value": 12, "string": ""},
            "date": {"_class": "Date", "dateval": [1, 1, 1950, False]},
        })
        _insert(conn, "person", "h_john", "I0001", {
            "_class": "Person",
            "primary_name": {"first_name": "John", "surname_list": []},
            "event_ref_list": [{"_class": "EventRef", "ref": "h_ev_birth"}],
        })
        _insert(conn, "family", "h_fam", "F0001", {
            "_class": "Family",
            "father_handle": "h_john",
            "mother_handle": None,
            "child_ref_list": [],
            "event_ref_list": [],
        }, father_handle="h_john")
        return conn

    def test_person_row_deleted(self):
        from gramps_mcp.tools.delete import _cascade_plan, _execute_cascade
        conn = self._setup()
        plan = _cascade_plan(conn, "person", "h_john")
        _execute_cascade(conn, plan)
        row = conn.execute("SELECT handle FROM person WHERE handle='h_john'").fetchone()
        assert row is None

    def test_orphaned_event_deleted(self):
        from gramps_mcp.tools.delete import _cascade_plan, _execute_cascade
        conn = self._setup()
        plan = _cascade_plan(conn, "person", "h_john")
        _execute_cascade(conn, plan)
        row = conn.execute("SELECT handle FROM event WHERE handle='h_ev_birth'").fetchone()
        assert row is None

    def test_family_father_handle_nulled(self):
        from gramps_mcp.tools.delete import _cascade_plan, _execute_cascade
        conn = self._setup()
        plan = _cascade_plan(conn, "person", "h_john")
        _execute_cascade(conn, plan)
        row = conn.execute("SELECT json_data FROM family WHERE handle='h_fam'").fetchone()
        fam_data = json.loads(row[0])
        assert fam_data.get("father_handle") is None

    def test_rollback_on_error_leaves_db_unchanged(self):
        """If a write fails mid-transaction, no partial changes should persist."""
        from gramps_mcp.tools.delete import _cascade_plan, _execute_cascade
        conn = self._setup()
        plan = _cascade_plan(conn, "person", "h_john")

        # Install a trigger that raises on the second DELETE (events table)
        conn.execute(
            "CREATE TRIGGER no_event_delete BEFORE DELETE ON event "
            "BEGIN SELECT RAISE(ABORT, 'blocked for test'); END"
        )
        with pytest.raises(Exception):
            _execute_cascade(conn, plan)

        conn.execute("DROP TRIGGER no_event_delete")
        # Person must still exist — transaction was rolled back
        row = conn.execute("SELECT handle FROM person WHERE handle='h_john'").fetchone()
        assert row is not None

    def test_shared_event_survives(self):
        from gramps_mcp.tools.delete import _cascade_plan, _execute_cascade
        conn = _make_conn()
        _insert(conn, "event", "h_ev_shared", "E0001", {
            "_class": "Event",
            "type": {"_class": "EventType", "value": 1, "string": ""},
            "date": {},
        })
        _insert(conn, "person", "h_john", "I0001", {
            "_class": "Person",
            "primary_name": {"first_name": "John", "surname_list": []},
            "event_ref_list": [{"_class": "EventRef", "ref": "h_ev_shared"}],
        })
        _insert(conn, "person", "h_jane", "I0002", {
            "_class": "Person",
            "primary_name": {"first_name": "Jane", "surname_list": []},
            "event_ref_list": [{"_class": "EventRef", "ref": "h_ev_shared"}],
        })
        plan = _cascade_plan(conn, "person", "h_john")
        _execute_cascade(conn, plan)
        row = conn.execute("SELECT handle FROM event WHERE handle='h_ev_shared'").fetchone()
        assert row is not None
```

- [ ] **Step 2: Run tests to verify they fail**

```
uv run pytest tests/test_delete_tool.py::TestExecuteCascade -v
```

Expected: `ImportError: cannot import name '_execute_cascade'`

- [ ] **Step 3: Implement `_build_summary()` and `_execute_cascade()` in `delete.py`**

Append after `_cascade_plan()`:

```python
# ---------------------------------------------------------------------------
# Summary builder
# ---------------------------------------------------------------------------


def _build_summary(plan: CascadeResult) -> str:
    """
    Build a one-sentence human-readable summary of a CascadeResult.

    Args:
        plan: CascadeResult from _cascade_plan().

    Returns:
        str: English summary suitable for presenting to the user.
    """
    if not plan.to_delete:
        return "Nothing to delete."
    primary = plan.to_delete[0]
    parts = [f"Deletes {primary.label}"]
    cascade = plan.to_delete[1:]
    if cascade:
        labels = ", ".join(e.label for e in cascade)
        parts.append(f"including {len(cascade)} dependent object(s): {labels}")
    if plan.to_unlink:
        targets = ", ".join(
            f"{e.label} ({e.field})" for e in plan.to_unlink
        )
        parts.append(f"removes references in: {targets}")
    return ". ".join(parts) + "."


# ---------------------------------------------------------------------------
# Execute cascade (transactional)
# ---------------------------------------------------------------------------


def _execute_cascade(conn: Any, plan: CascadeResult) -> None:
    """
    Execute a CascadeResult plan in a single SQLite transaction.

    Applies JSON unlinks first, then deletes all objects in plan.to_delete,
    then cleans up the reference table. Rolls back on any error.

    Args:
        conn: Open sqlite3.Connection (write mode).
        plan: CascadeResult from _cascade_plan().

    Raises:
        GrampsAPIError: On SQLite write errors.
    """
    import sqlite3 as _sqlite3
    from collections import defaultdict

    # Group unlinks by target object so we apply all removals in one JSON write
    by_obj: Dict = defaultdict(list)
    for entry in plan.to_unlink:
        by_obj[(entry.obj_type, entry.handle)].append(entry)

    # Collect all handles being deleted (for batch unlink pass)
    deleting_handles = {e.handle for e in plan.to_delete}

    try:
        with conn:
            # 1. Apply JSON unlinks
            for (obj_type, obj_handle), _ in by_obj.items():
                table = _TABLE[obj_type]
                row = conn.execute(
                    f"SELECT json_data FROM {table} WHERE handle = ?",  # noqa: S608
                    (obj_handle,),
                ).fetchone()
                if not row:
                    continue
                try:
                    json_data = json.loads(row[0])
                except Exception:
                    continue
                for del_handle in deleting_handles:
                    json_data, _ = _remove_handle_from_json(json_data, del_handle)
                conn.execute(
                    f"UPDATE {table} SET json_data = ? WHERE handle = ?",  # noqa: S608
                    (json.dumps(json_data, ensure_ascii=False), obj_handle),
                )

            # 2. Delete objects and clean reference table
            for entry in plan.to_delete:
                table = _TABLE[entry.obj_type]
                conn.execute(
                    f"DELETE FROM {table} WHERE handle = ?", (entry.handle,)  # noqa: S608
                )
                conn.execute(
                    "DELETE FROM reference WHERE obj_handle = ? OR ref_handle = ?",
                    (entry.handle, entry.handle),
                )
    except _sqlite3.Error as exc:
        raise GrampsAPIError(
            f"SQLite error during delete of {plan.to_delete[0].handle}: {exc}"
        ) from exc
```

- [ ] **Step 4: Run tests to verify they pass**

```
uv run pytest tests/test_delete_tool.py::TestExecuteCascade -v
```

Expected: 4 passed

- [ ] **Step 5: Commit**

```
git add src/gramps_mcp/tools/delete.py tests/test_delete_tool.py
git commit -m "feat(delete): add _build_summary and _execute_cascade"
```

---

## Task 5: `delete_object_tool()` and server registration

**Files:**
- Modify: `src/gramps_mcp/tools/delete.py`
- Modify: `src/gramps_mcp/server.py`
- Modify: `tests/test_delete_tool.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_delete_tool.py`:

```python
# ---------------------------------------------------------------------------
# Full tool tests
# ---------------------------------------------------------------------------


class TestDeleteObjectTool:
    def _make_db(self):
        conn = _make_conn()
        _insert(conn, "event", "h_ev_birth", "E0001", {
            "_class": "Event",
            "type": {"_class": "EventType", "value": 12, "string": ""},
            "date": {"_class": "Date", "dateval": [1, 1, 1950, False]},
        })
        _insert(conn, "person", "h_john", "I0001", {
            "_class": "Person",
            "primary_name": {"first_name": "John", "surname_list": [{"surname": "Smith"}]},
            "event_ref_list": [{"_class": "EventRef", "ref": "h_ev_birth"}],
        })
        db = GrampsSqliteDB(conn=conn, db_path=":memory:", read_only=False)
        return db, conn

    def test_dry_run_returns_would_delete(self):
        from gramps_mcp.tools.delete import delete_object_tool
        import asyncio
        db, conn = self._make_db()
        result = asyncio.run(delete_object_tool("person", "h_john", confirmed=False, db=db))
        data = json.loads(result)
        assert "would_delete" in data
        assert "summary" in data
        handles = [e["handle"] for e in data["would_delete"]]
        assert "h_john" in handles

    def test_dry_run_does_not_delete(self):
        from gramps_mcp.tools.delete import delete_object_tool
        import asyncio
        db, conn = self._make_db()
        asyncio.run(delete_object_tool("person", "h_john", confirmed=False, db=db))
        row = conn.execute("SELECT handle FROM person WHERE handle='h_john'").fetchone()
        assert row is not None

    def test_confirmed_deletes_person(self):
        from gramps_mcp.tools.delete import delete_object_tool
        import asyncio
        db, conn = self._make_db()
        result = asyncio.run(delete_object_tool("person", "h_john", confirmed=True, db=db))
        data = json.loads(result)
        assert "deleted" in data
        row = conn.execute("SELECT handle FROM person WHERE handle='h_john'").fetchone()
        assert row is None

    def test_unknown_handle_raises(self):
        from gramps_mcp.tools.delete import delete_object_tool
        from gramps_mcp.client import GrampsAPIError
        import asyncio
        db, _ = self._make_db()
        with pytest.raises(GrampsAPIError, match="not found"):
            asyncio.run(delete_object_tool("person", "no_such_handle", confirmed=False, db=db))
```

- [ ] **Step 2: Run tests to verify they fail**

```
uv run pytest tests/test_delete_tool.py::TestDeleteObjectTool -v
```

Expected: `ImportError: cannot import name 'delete_object_tool'`

- [ ] **Step 3: Implement `delete_object_tool()` in `delete.py`**

Append at the end of `delete.py`:

```python
# ---------------------------------------------------------------------------
# MCP Tool entry point
# ---------------------------------------------------------------------------


async def delete_object_tool(
    obj_type: str,
    handle: str,
    confirmed: bool,
    db: Any = None,
) -> str:
    """
    Delete a Gramps object with cascade cleanup.

    When confirmed=False returns a dry-run summary without touching the DB.
    When confirmed=True executes the deletion in a single transaction.
    SQLite backend only.

    Args:
        obj_type: One of person/family/event/place/citation/source/
                  note/media/repository/tag.
        handle:   Gramps handle of the object to delete.
        confirmed: False for dry-run, True to execute.
        db:       GrampsSqliteDB instance (injected; uses get_client() if None).

    Returns:
        JSON string with summary, would_delete/deleted, would_unlink/unlinked.

    Raises:
        GrampsAPIError: On unknown type, missing handle, or write error.
    """
    if db is None:
        from gramps_mcp.client import get_client
        from gramps_mcp.sqlite_client import GrampsSqliteClient
        client = get_client()
        if not isinstance(client, GrampsSqliteClient):
            raise GrampsAPIError(
                "delete_object is SQLite-only; Web backend not yet supported"
            )
        db = client._db

    from gramps_mcp._gramps_sqlite import GrampsSqliteDB

    if not isinstance(db, GrampsSqliteDB):
        raise GrampsAPIError(
            "delete_object is SQLite-only; Web backend not yet supported"
        )

    table = _TABLE.get(obj_type)
    if not table:
        raise GrampsAPIError(
            f"Unknown object type: '{obj_type}'. "
            f"Valid types: {', '.join(_TABLE)}"
        )

    conn = db._conn
    row = conn.execute(
        f"SELECT handle FROM {table} WHERE handle = ?",  # noqa: S608
        (handle,),
    ).fetchone()
    if not row:
        raise GrampsAPIError(
            f"{obj_type} with handle '{handle}' not found"
        )

    plan = _cascade_plan(conn, obj_type, handle)

    if not confirmed:
        return json.dumps(
            {
                "summary": _build_summary(plan),
                "would_delete": [asdict(e) for e in plan.to_delete],
                "would_unlink": [asdict(e) for e in plan.to_unlink],
            },
            ensure_ascii=False,
        )

    _execute_cascade(conn, plan)

    return json.dumps(
        {
            "summary": _build_summary(plan),
            "deleted": [asdict(e) for e in plan.to_delete],
            "unlinked": [asdict(e) for e in plan.to_unlink],
        },
        ensure_ascii=False,
    )
```

- [ ] **Step 4: Run tool tests to verify they pass**

```
uv run pytest tests/test_delete_tool.py::TestDeleteObjectTool -v
```

Expected: 4 passed

- [ ] **Step 5: Register in `server.py`**

Find the import block near the top of `server.py` where other tools are imported (search for `from gramps_mcp.tools`). Add:

```python
from gramps_mcp.tools.delete import delete_object_tool
```

Find `TOOL_REGISTRY` in `server.py`. Add this entry (e.g., after the merge tools section):

```python
    "delete_object": {
        "description": (
            "Delete a Gramps object and cascade-clean all references. "
            "Use confirmed=False first to see a dry-run summary of what "
            "will be deleted, then call again with confirmed=True to execute. "
            "Orphaned events (owned exclusively by the deleted person/family) "
            "are automatically deleted. SQLite backend only."
        ),
        "schema": DeleteObjectParams,
        "handler": lambda args: delete_object_tool(
            obj_type=args["obj_type"],
            handle=args["handle"],
            confirmed=args.get("confirmed", False),
        ),
    },
```

Also add the `DeleteObjectParams` Pydantic model. Create
`src/gramps_mcp/models/parameters/delete_params.py`:

```python
"""Parameters for the delete_object MCP tool."""

from typing import Literal
from pydantic import BaseModel, Field


class DeleteObjectParams(BaseModel):
    """Parameters for delete_object tool."""

    obj_type: Literal[
        "person", "family", "event", "place", "citation",
        "source", "note", "media", "repository", "tag",
    ] = Field(..., description="Type of Gramps object to delete")
    handle: str = Field(..., description="Gramps handle of the object to delete")
    confirmed: bool = Field(
        False,
        description=(
            "False (default) = dry-run, returns summary without deleting. "
            "True = execute deletion. Always call with False first."
        ),
    )
```

Import it in `server.py`:

```python
from gramps_mcp.models.parameters.delete_params import DeleteObjectParams
```

- [ ] **Step 6: Run full test suite to verify no regressions**

```
uv run pytest tests/test_delete_tool.py tests/test_sqlite_client.py -v
```

Expected: all pass

- [ ] **Step 7: Commit**

```
git add src/gramps_mcp/tools/delete.py \
        src/gramps_mcp/models/parameters/delete_params.py \
        src/gramps_mcp/server.py \
        tests/test_delete_tool.py
git commit -m "feat: add delete_object MCP tool with cascade cleanup and dry-run mode"
```
