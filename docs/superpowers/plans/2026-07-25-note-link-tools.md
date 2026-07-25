# Note-Link Tools Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `add_note_to_person`/`add_note_to_family` (with upsert semantics), their `remove_note_from_*` counterparts, and a `get_note` tool to the Gramps MCP server.

**Architecture:** Four new SQLite-only tools in a new `tools/note_link.py` module, following the exact pattern of `tools/citation_link.py` (raw JSON manipulation via `_sqlite_helpers.py`, `db=` injection for tests). A fifth tool, `get_note`, is added to `tools/search_details.py` alongside `get_event`/`get_place` since it must work across all three backends (Web/XML/SQLite), not just SQLite.

**Tech Stack:** Python, pydantic, MCP Python SDK, pytest (`uv run pytest`), sqlite3 (stdlib), no mocks — all tests use fresh in-memory SQLite DBs.

## Global Constraints

- No file may exceed 500 lines (CLAUDE.md). `search_details.py` is already at 961 lines (pre-existing violation, not in scope to fix); adding `get_note_tool` there follows established file placement and is not a unilateral restructure.
- Use `uv run pytest` / `uv run git commit` for all commands (CLAUDE.md).
- No mocks in tests — real in-memory SQLite DBs only (CLAUDE.md TDD rules).
- Docstrings: Google style (CLAUDE.md).
- No emojis anywhere in code (CLAUDE.md).
- Every new tool is `SQLite backend only` except `get_note`, which is cross-backend.
- Branch: `feature/note-link-tools` (already checked out). All commits go here; open the PR to `dev` only after the final task passes (see closing note).
- Design source of truth: `docs/superpowers/specs/2026-07-25-note-link-tools-design.md`.

---

### Task 1: `add_note_to_person` tool

**Files:**
- Create: `src/gramps_mcp/tools/note_link.py`
- Create: `tests/test_note_link_tools.py`

**Interfaces:**
- Consumes: `_read_object`, `_require_sqlite_db`, `_resolve_handle`, `_write_object`, `_write_person` from `src/gramps_mcp/tools/_sqlite_helpers.py` (all already exist, unchanged); `GrampsAPIError` from `src/gramps_mcp/client.py`; `GrampsSqliteDB` from `src/gramps_mcp/_gramps_sqlite.py` (its `put(obj_type: str, obj: Dict) -> Dict` method, which upserts an object and returns the stored dict with `handle`/`gramps_id` filled in).
- Produces: `add_note_to_person_tool(person_handle=None, person_gramps_id=None, note_handle=None, note_gramps_id=None, text=None, type=None, db=None) -> List[TextContent]` and a private helper `_upsert_note(conn, db, note_handle, note_gramps_id, text, type) -> tuple[str, Optional[str], Optional[str]]` (returns `note_handle, note_gramps_id, result` where `result` is `"created"`, `"updated"`, or `None` for link-only mode) — both used again by Task 2.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_note_link_tools.py`:

```python
"""
Tests for note-link MCP tools:
  - add_note_to_person / add_note_to_family
  - remove_note_from_person / remove_note_from_family

All tests use fresh per-test in-memory SQLite DBs with db= injection.
"""

import asyncio
import json
import sqlite3

import pytest
from mcp.types import TextContent

from gramps_mcp._gramps_sqlite import GrampsSqliteDB
from gramps_mcp.client import GrampsAPIError

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
CREATE TABLE note (
    handle VARCHAR(50) PRIMARY KEY NOT NULL,
    json_data TEXT, gramps_id TEXT, format INTEGER DEFAULT 0,
    change INTEGER DEFAULT 0, private INTEGER DEFAULT 0
);
CREATE TABLE metadata (
    setting VARCHAR(50) PRIMARY KEY NOT NULL,
    json_data TEXT, value BLOB
);
"""


@pytest.fixture()
def fresh_db():
    """Fresh in-memory GrampsSqliteDB for each test."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    conn.commit()
    db = GrampsSqliteDB(conn=conn, db_path=":memory:", read_only=False)
    return db, conn


def _insert_person(conn, handle: str, gramps_id: str, note_list=None) -> None:
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
            "date": {"_class": "Date", "calendar": 0, "modifier": 0, "quality": 0,
                     "dateval": [0, 0, 0, False], "text": "", "sortval": 0,
                     "newyear": 0, "format": None},
        },
        "alternate_names": [], "death_ref_index": -1, "birth_ref_index": -1,
        "event_ref_list": [], "family_list": [], "parent_family_list": [],
        "media_list": [], "address_list": [], "attribute_list": [],
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


def _insert_note(conn, handle: str, gramps_id: str,
                  text: str = "Original text", note_type: str = "General") -> None:
    data = {
        "_class": "Note", "handle": handle, "gramps_id": gramps_id, "format": 0,
        "text": {"_class": "StyledText", "string": text, "tags": []},
        "type": {"_class": "NoteType", "value": 1, "string": note_type},
        "tag_list": [], "change": 0, "private": False,
    }
    conn.execute(
        "INSERT INTO note (handle, gramps_id, json_data, format, change, private) "
        "VALUES (?,?,?,0,0,0)",
        (handle, gramps_id, json.dumps(data)),
    )
    conn.commit()


# ===========================================================================
# add_note_to_person
# ===========================================================================

class TestAddNoteToPerson:
    def test_link_existing_note(self, fresh_db):
        from gramps_mcp.tools.note_link import add_note_to_person_tool

        db, conn = fresh_db
        _insert_person(conn, "h_pe", "I0001")
        _insert_note(conn, "h_no", "N0001")

        result = asyncio.run(
            add_note_to_person_tool(person_handle="h_pe", note_handle="h_no", db=db)
        )
        assert isinstance(result, list) and isinstance(result[0], TextContent)
        data = json.loads(result[0].text)
        assert data["result"] == "linked"
        assert data["note_handle"] == "h_no"
        assert data["note_gramps_id"] == "N0001"
        assert data["note_count"] == 1

        row = conn.execute("SELECT json_data FROM person WHERE handle = 'h_pe'").fetchone()
        person_data = json.loads(row["json_data"])
        assert "h_no" in person_data["note_list"]

    def test_link_idempotent(self, fresh_db):
        from gramps_mcp.tools.note_link import add_note_to_person_tool

        db, conn = fresh_db
        _insert_person(conn, "h_pe", "I0001")
        _insert_note(conn, "h_no", "N0001")

        asyncio.run(add_note_to_person_tool(person_handle="h_pe", note_handle="h_no", db=db))
        result = asyncio.run(
            add_note_to_person_tool(person_handle="h_pe", note_handle="h_no", db=db)
        )
        data = json.loads(result[0].text)
        assert data["result"] == "no_change"

        row = conn.execute("SELECT json_data FROM person WHERE handle = 'h_pe'").fetchone()
        person_data = json.loads(row["json_data"])
        assert person_data["note_list"].count("h_no") == 1

    def test_link_via_gramps_ids(self, fresh_db):
        from gramps_mcp.tools.note_link import add_note_to_person_tool

        db, conn = fresh_db
        _insert_person(conn, "h_pe", "I0001")
        _insert_note(conn, "h_no", "N0001")

        result = asyncio.run(
            add_note_to_person_tool(
                person_gramps_id="I0001", note_gramps_id="N0001", db=db
            )
        )
        data = json.loads(result[0].text)
        assert data["result"] == "linked"

    def test_link_error_unknown_note(self, fresh_db):
        from gramps_mcp.tools.note_link import add_note_to_person_tool

        db, conn = fresh_db
        _insert_person(conn, "h_pe", "I0001")

        with pytest.raises(GrampsAPIError, match="not found"):
            asyncio.run(
                add_note_to_person_tool(person_handle="h_pe", note_handle="missing", db=db)
            )

    def test_create_new_note(self, fresh_db):
        from gramps_mcp.tools.note_link import add_note_to_person_tool

        db, conn = fresh_db
        _insert_person(conn, "h_pe", "I0001")

        result = asyncio.run(
            add_note_to_person_tool(
                person_handle="h_pe", text="Brand new note", type="Research", db=db
            )
        )
        data = json.loads(result[0].text)
        assert data["result"] == "created"
        assert data["note_gramps_id"] == "N0001"
        new_handle = data["note_handle"]

        row = conn.execute(
            "SELECT json_data FROM note WHERE handle = ?", (new_handle,)
        ).fetchone()
        note_data = json.loads(row["json_data"])
        assert note_data["text"]["string"] == "Brand new note"
        assert note_data["type"]["string"] == "Research"

        prow = conn.execute("SELECT json_data FROM person WHERE handle = 'h_pe'").fetchone()
        person_data = json.loads(prow["json_data"])
        assert new_handle in person_data["note_list"]

    def test_update_existing_note_text(self, fresh_db):
        from gramps_mcp.tools.note_link import add_note_to_person_tool

        db, conn = fresh_db
        _insert_person(conn, "h_pe", "I0001", note_list=["h_no"])
        _insert_note(conn, "h_no", "N0001", text="Old text", note_type="General")

        result = asyncio.run(
            add_note_to_person_tool(person_handle="h_pe", note_handle="h_no", text="New text", db=db)
        )
        data = json.loads(result[0].text)
        assert data["result"] == "updated"
        assert data["note_count"] == 1  # already linked, unchanged

        row = conn.execute("SELECT json_data FROM note WHERE handle = 'h_no'").fetchone()
        note_data = json.loads(row["json_data"])
        assert note_data["text"]["string"] == "New text"
        assert note_data["type"]["string"] == "General"  # untouched

    def test_update_and_link_not_yet_linked(self, fresh_db):
        from gramps_mcp.tools.note_link import add_note_to_person_tool

        db, conn = fresh_db
        _insert_person(conn, "h_pe", "I0001")
        _insert_note(conn, "h_no", "N0001", text="Old text")

        result = asyncio.run(
            add_note_to_person_tool(person_handle="h_pe", note_handle="h_no", text="New text", db=db)
        )
        data = json.loads(result[0].text)
        assert data["result"] == "updated"
        assert data["note_count"] == 1  # newly linked as part of the update

        row = conn.execute("SELECT json_data FROM note WHERE handle = 'h_no'").fetchone()
        assert json.loads(row["json_data"])["text"]["string"] == "New text"

    def test_error_no_identifier_no_content(self, fresh_db):
        from gramps_mcp.tools.note_link import add_note_to_person_tool

        db, conn = fresh_db
        _insert_person(conn, "h_pe", "I0001")

        with pytest.raises(GrampsAPIError, match="text/type"):
            asyncio.run(add_note_to_person_tool(person_handle="h_pe", db=db))

    def test_error_unknown_person(self, fresh_db):
        from gramps_mcp.tools.note_link import add_note_to_person_tool

        db, conn = fresh_db
        _insert_note(conn, "h_no", "N0001")

        with pytest.raises(GrampsAPIError, match="not found"):
            asyncio.run(
                add_note_to_person_tool(person_handle="missing", note_handle="h_no", db=db)
            )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_note_link_tools.py -v`
Expected: FAIL/ERROR on every test with `ModuleNotFoundError: No module named 'gramps_mcp.tools.note_link'`

- [ ] **Step 3: Write the minimal implementation**

Create `src/gramps_mcp/tools/note_link.py`:

```python
"""
Note-link MCP tools — SQLite-only tools for linking, creating, and updating
notes on persons and families without replacing note_list wholesale.

add_note_to_person / add_note_to_family behave as an upsert:
  - note_handle/note_gramps_id only          -> link an existing note
  - text/type only                           -> create a new note and link it
  - note_handle/note_gramps_id + text/type   -> update the existing note's
                                                 content and ensure it's linked

All tools require the SQLite backend (GrampsSqliteClient) and use injectable
db= parameters for testability (same pattern as link_edit.py/citation_link.py).
"""

import json
from typing import Any, Dict, List, Optional, Tuple

from mcp.types import TextContent

from gramps_mcp.client import GrampsAPIError
from gramps_mcp.tools._sqlite_helpers import (
    _read_object,
    _require_sqlite_db,
    _resolve_handle,
    _write_object,
    _write_person,
)


def _upsert_note(
    conn: Any,
    db: Any,
    note_handle: Optional[str],
    note_gramps_id: Optional[str],
    text: Optional[str],
    type: Optional[str],
) -> Tuple[str, Optional[str], Optional[str]]:
    """
    Resolve, create, or update a note per add_note_to_person/family's mode rules.

    Args:
        conn: Open sqlite3.Connection.
        db: GrampsSqliteDB (used for db.put on create/update).
        note_handle: Handle of an existing note, or None.
        note_gramps_id: Gramps ID of an existing note, or None.
        text: New note text, or None to leave content untouched (link-only mode).
        type: New note type, or None to leave content untouched (link-only mode).

    Returns:
        Tuple of (note_handle, note_gramps_id, result) where result is
        'created', 'updated', or None (link-only mode; the caller determines
        'linked'/'no_change' from whether the note was already in the list).

    Raises:
        GrampsAPIError: If an identifier is given but the note doesn't exist,
                        or neither an identifier nor text/type is given.
    """
    has_identifier = bool(note_handle or note_gramps_id)
    has_content = text is not None or type is not None

    if not has_identifier and not has_content:
        raise GrampsAPIError("note_handle/note_gramps_id or text/type is required")

    resolved_handle: Optional[str] = None
    if has_identifier:
        resolved_handle = _resolve_handle(conn, "note", note_handle, note_gramps_id, "Note")
        # _resolve_handle only confirms existence when gramps_id is given; when a
        # raw handle is passed directly it returns it without a DB lookup, so we
        # must verify the note exists here in both code paths.
        if not conn.execute(
            "SELECT handle FROM note WHERE handle = ?",  # noqa: S608
            (resolved_handle,),
        ).fetchone():
            raise GrampsAPIError(f"Note with handle '{resolved_handle}' not found")

    if not has_content:
        row = conn.execute(
            "SELECT gramps_id FROM note WHERE handle = ?",  # noqa: S608
            (resolved_handle,),
        ).fetchone()
        return resolved_handle, (row[0] if row else None), None

    note_obj: Dict[str, Any] = {}
    if resolved_handle:
        note_obj["handle"] = resolved_handle
    if text is not None:
        note_obj["text"] = {"string": text}
    if type is not None:
        note_obj["type"] = type

    stored_note = db.put("note", note_obj)
    result = "created" if resolved_handle is None else "updated"
    return stored_note["handle"], stored_note.get("gramps_id"), result


async def add_note_to_person_tool(
    person_handle: Optional[str] = None,
    person_gramps_id: Optional[str] = None,
    note_handle: Optional[str] = None,
    note_gramps_id: Optional[str] = None,
    text: Optional[str] = None,
    type: Optional[str] = None,
    db: Any = None,
) -> List[TextContent]:
    """
    Link a note to a person, creating or updating the note in the same call.

    Three modes, selected by which parameters are given:
      - note_handle/note_gramps_id only: link an existing note (idempotent,
        result='no_change' if already linked).
      - text/type only: create a new note and link it (result='created').
      - note_handle/note_gramps_id + text and/or type: overwrite the
        existing note's content, then ensure it's linked (result='updated').

    Args:
        person_handle: Handle of the person.
        person_gramps_id: Gramps ID of the person (alternative to person_handle).
        note_handle: Handle of an existing note to link or update.
        note_gramps_id: Gramps ID of an existing note (alternative to note_handle).
        text: Note text. Required when note_handle/note_gramps_id are both omitted.
        type: Note type (e.g. 'Research'). Required when note_handle/note_gramps_id
            are both omitted.
        db: GrampsSqliteDB instance (injected for tests; None uses get_client()).

    Returns:
        List[TextContent] with JSON result.

    Raises:
        GrampsAPIError: If backend is not SQLite, person/note not found, or
                        neither an identifier nor text/type is given.
    """
    db = _require_sqlite_db(db, "add_note_to_person")
    conn = db._conn

    person_handle = _resolve_handle(conn, "person", person_handle, person_gramps_id, "Person")
    note_handle_final, note_gramps_id_final, note_result = _upsert_note(
        conn, db, note_handle, note_gramps_id, text, type
    )

    person_data = _read_object(conn, "person", person_handle, "Person")
    note_list = person_data.get("note_list", [])

    if note_handle_final in note_list:
        result = note_result or "no_change"
    else:
        note_list.append(note_handle_final)
        person_data["note_list"] = note_list
        with conn:
            _write_person(conn, person_handle, person_data)
        result = note_result or "linked"

    return [TextContent(type="text", text=json.dumps(
        {
            "result": result,
            "note_handle": note_handle_final,
            "note_gramps_id": note_gramps_id_final,
            "person_handle": person_handle,
            "note_count": len(note_list),
        },
        ensure_ascii=False,
    ))]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_note_link_tools.py -v`
Expected: All `TestAddNoteToPerson` tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/gramps_mcp/tools/note_link.py tests/test_note_link_tools.py
uv run git commit -m "feat: add add_note_to_person tool with link/create/update upsert modes"
```

---

### Task 2: `add_note_to_family` tool

**Files:**
- Modify: `src/gramps_mcp/tools/note_link.py` (append `add_note_to_family_tool`)
- Modify: `tests/test_note_link_tools.py` (append `TestAddNoteToFamily`)

**Interfaces:**
- Consumes: `_upsert_note` from Task 1 (same file, same signature); `_write_object` from `_sqlite_helpers.py`.
- Produces: `add_note_to_family_tool(family_handle=None, family_gramps_id=None, note_handle=None, note_gramps_id=None, text=None, type=None, db=None) -> List[TextContent]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_note_link_tools.py`, after the `_insert_note` helper and before `TestAddNoteToPerson` (helper) or anywhere after the fixtures — add this helper next to `_insert_person`:

```python
def _insert_family(conn, handle: str, gramps_id: str, note_list=None) -> None:
    data = {
        "_class": "Family", "handle": handle, "gramps_id": gramps_id,
        "father_handle": None, "mother_handle": None,
        "child_ref_list": [],
        "type": {"_class": "FamilyRelType", "value": 0, "string": ""},
        "event_ref_list": [], "media_list": [],
        "attribute_list": [], "lds_ord_list": [], "citation_list": [],
        "note_list": note_list or [], "tag_list": [], "change": 0, "private": False,
    }
    conn.execute(
        "INSERT INTO family (handle, gramps_id, json_data, change, private) VALUES (?,?,?,0,0)",
        (handle, gramps_id, json.dumps(data)),
    )
    conn.commit()
```

Then append this test class at the end of the file:

```python
# ===========================================================================
# add_note_to_family
# ===========================================================================

class TestAddNoteToFamily:
    def test_link_existing_note(self, fresh_db):
        from gramps_mcp.tools.note_link import add_note_to_family_tool

        db, conn = fresh_db
        _insert_family(conn, "h_fa", "F0001")
        _insert_note(conn, "h_no", "N0001")

        result = asyncio.run(
            add_note_to_family_tool(family_handle="h_fa", note_handle="h_no", db=db)
        )
        data = json.loads(result[0].text)
        assert data["result"] == "linked"
        assert data["family_handle"] == "h_fa"
        assert data["note_count"] == 1

        row = conn.execute("SELECT json_data FROM family WHERE handle = 'h_fa'").fetchone()
        assert "h_no" in json.loads(row["json_data"])["note_list"]

    def test_link_idempotent(self, fresh_db):
        from gramps_mcp.tools.note_link import add_note_to_family_tool

        db, conn = fresh_db
        _insert_family(conn, "h_fa", "F0001")
        _insert_note(conn, "h_no", "N0001")

        asyncio.run(add_note_to_family_tool(family_handle="h_fa", note_handle="h_no", db=db))
        result = asyncio.run(
            add_note_to_family_tool(family_handle="h_fa", note_handle="h_no", db=db)
        )
        assert json.loads(result[0].text)["result"] == "no_change"

    def test_create_new_note(self, fresh_db):
        from gramps_mcp.tools.note_link import add_note_to_family_tool

        db, conn = fresh_db
        _insert_family(conn, "h_fa", "F0001")

        result = asyncio.run(
            add_note_to_family_tool(
                family_handle="h_fa", text="Family research note", type="Research", db=db
            )
        )
        data = json.loads(result[0].text)
        assert data["result"] == "created"
        new_handle = data["note_handle"]

        row = conn.execute("SELECT json_data FROM family WHERE handle = 'h_fa'").fetchone()
        assert new_handle in json.loads(row["json_data"])["note_list"]

    def test_update_existing_note(self, fresh_db):
        from gramps_mcp.tools.note_link import add_note_to_family_tool

        db, conn = fresh_db
        _insert_family(conn, "h_fa", "F0001", note_list=["h_no"])
        _insert_note(conn, "h_no", "N0001", text="Old")

        result = asyncio.run(
            add_note_to_family_tool(family_handle="h_fa", note_handle="h_no", text="New", db=db)
        )
        assert json.loads(result[0].text)["result"] == "updated"

        row = conn.execute("SELECT json_data FROM note WHERE handle = 'h_no'").fetchone()
        assert json.loads(row["json_data"])["text"]["string"] == "New"

    def test_error_unknown_family(self, fresh_db):
        from gramps_mcp.tools.note_link import add_note_to_family_tool

        db, conn = fresh_db
        _insert_note(conn, "h_no", "N0001")

        with pytest.raises(GrampsAPIError, match="not found"):
            asyncio.run(
                add_note_to_family_tool(family_handle="missing", note_handle="h_no", db=db)
            )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_note_link_tools.py::TestAddNoteToFamily -v`
Expected: FAIL/ERROR with `ImportError: cannot import name 'add_note_to_family_tool'`

- [ ] **Step 3: Write the minimal implementation**

Append to `src/gramps_mcp/tools/note_link.py`, after `add_note_to_person_tool`:

```python
async def add_note_to_family_tool(
    family_handle: Optional[str] = None,
    family_gramps_id: Optional[str] = None,
    note_handle: Optional[str] = None,
    note_gramps_id: Optional[str] = None,
    text: Optional[str] = None,
    type: Optional[str] = None,
    db: Any = None,
) -> List[TextContent]:
    """
    Link a note to a family, creating or updating the note in the same call.

    Same three modes as add_note_to_person_tool.

    Args:
        family_handle: Handle of the family.
        family_gramps_id: Gramps ID of the family (alternative to family_handle).
        note_handle: Handle of an existing note to link or update.
        note_gramps_id: Gramps ID of an existing note (alternative to note_handle).
        text: Note text. Required when note_handle/note_gramps_id are both omitted.
        type: Note type (e.g. 'Research'). Required when note_handle/note_gramps_id
            are both omitted.
        db: GrampsSqliteDB instance (injected for tests; None uses get_client()).

    Returns:
        List[TextContent] with JSON result.

    Raises:
        GrampsAPIError: If backend is not SQLite, family/note not found, or
                        neither an identifier nor text/type is given.
    """
    db = _require_sqlite_db(db, "add_note_to_family")
    conn = db._conn

    family_handle = _resolve_handle(conn, "family", family_handle, family_gramps_id, "Family")
    note_handle_final, note_gramps_id_final, note_result = _upsert_note(
        conn, db, note_handle, note_gramps_id, text, type
    )

    family_data = _read_object(conn, "family", family_handle, "Family")
    note_list = family_data.get("note_list", [])

    if note_handle_final in note_list:
        result = note_result or "no_change"
    else:
        note_list.append(note_handle_final)
        family_data["note_list"] = note_list
        with conn:
            _write_object(conn, "family", family_handle, family_data)
        result = note_result or "linked"

    return [TextContent(type="text", text=json.dumps(
        {
            "result": result,
            "note_handle": note_handle_final,
            "note_gramps_id": note_gramps_id_final,
            "family_handle": family_handle,
            "note_count": len(note_list),
        },
        ensure_ascii=False,
    ))]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_note_link_tools.py -v`
Expected: All tests PASS (both `TestAddNoteToPerson` and `TestAddNoteToFamily`)

- [ ] **Step 5: Commit**

```bash
git add src/gramps_mcp/tools/note_link.py tests/test_note_link_tools.py
uv run git commit -m "feat: add add_note_to_family tool"
```

---

### Task 3: `remove_note_from_person` / `remove_note_from_family` tools

**Files:**
- Modify: `src/gramps_mcp/tools/note_link.py` (append both remove tools)
- Modify: `tests/test_note_link_tools.py` (append both test classes)

**Interfaces:**
- Consumes: `_read_object`, `_resolve_handle`, `_require_sqlite_db`, `_write_object`, `_write_person` (unchanged, already imported in Task 1).
- Produces: `remove_note_from_person_tool(person_handle=None, person_gramps_id=None, note_handle=None, note_gramps_id=None, db=None) -> List[TextContent]` and `remove_note_from_family_tool(family_handle=None, family_gramps_id=None, note_handle=None, note_gramps_id=None, db=None) -> List[TextContent]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_note_link_tools.py`:

```python
# ===========================================================================
# remove_note_from_person
# ===========================================================================

class TestRemoveNoteFromPerson:
    def test_note_removed(self, fresh_db):
        from gramps_mcp.tools.note_link import remove_note_from_person_tool

        db, conn = fresh_db
        _insert_note(conn, "h_no", "N0001")
        _insert_person(conn, "h_pe", "I0001", note_list=["h_no"])

        result = asyncio.run(
            remove_note_from_person_tool(person_handle="h_pe", note_handle="h_no", db=db)
        )
        data = json.loads(result[0].text)
        assert data["result"] == "ok"
        assert data["note_count"] == 0

        row = conn.execute("SELECT json_data FROM person WHERE handle = 'h_pe'").fetchone()
        assert "h_no" not in json.loads(row["json_data"])["note_list"]

    def test_second_note_survives(self, fresh_db):
        from gramps_mcp.tools.note_link import remove_note_from_person_tool

        db, conn = fresh_db
        _insert_note(conn, "h_no1", "N0001")
        _insert_note(conn, "h_no2", "N0002")
        _insert_person(conn, "h_pe", "I0001", note_list=["h_no1", "h_no2"])

        asyncio.run(
            remove_note_from_person_tool(person_handle="h_pe", note_handle="h_no1", db=db)
        )

        row = conn.execute("SELECT json_data FROM person WHERE handle = 'h_pe'").fetchone()
        note_list = json.loads(row["json_data"])["note_list"]
        assert "h_no1" not in note_list
        assert "h_no2" in note_list

    def test_error_when_not_linked(self, fresh_db):
        from gramps_mcp.tools.note_link import remove_note_from_person_tool

        db, conn = fresh_db
        _insert_note(conn, "h_no", "N0001")
        _insert_person(conn, "h_pe", "I0001")

        with pytest.raises(GrampsAPIError, match="not linked"):
            asyncio.run(
                remove_note_from_person_tool(person_handle="h_pe", note_handle="h_no", db=db)
            )

    def test_remove_via_gramps_ids(self, fresh_db):
        from gramps_mcp.tools.note_link import remove_note_from_person_tool

        db, conn = fresh_db
        _insert_note(conn, "h_no", "N0001")
        _insert_person(conn, "h_pe", "I0001", note_list=["h_no"])

        result = asyncio.run(
            remove_note_from_person_tool(
                person_gramps_id="I0001", note_gramps_id="N0001", db=db
            )
        )
        assert json.loads(result[0].text)["result"] == "ok"


# ===========================================================================
# remove_note_from_family
# ===========================================================================

class TestRemoveNoteFromFamily:
    def test_note_removed(self, fresh_db):
        from gramps_mcp.tools.note_link import remove_note_from_family_tool

        db, conn = fresh_db
        _insert_note(conn, "h_no", "N0001")
        _insert_family(conn, "h_fa", "F0001", note_list=["h_no"])

        result = asyncio.run(
            remove_note_from_family_tool(family_handle="h_fa", note_handle="h_no", db=db)
        )
        data = json.loads(result[0].text)
        assert data["result"] == "ok"
        assert data["note_count"] == 0

    def test_error_when_not_linked(self, fresh_db):
        from gramps_mcp.tools.note_link import remove_note_from_family_tool

        db, conn = fresh_db
        _insert_note(conn, "h_no", "N0001")
        _insert_family(conn, "h_fa", "F0001")

        with pytest.raises(GrampsAPIError, match="not linked"):
            asyncio.run(
                remove_note_from_family_tool(family_handle="h_fa", note_handle="h_no", db=db)
            )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_note_link_tools.py::TestRemoveNoteFromPerson tests/test_note_link_tools.py::TestRemoveNoteFromFamily -v`
Expected: FAIL/ERROR with `ImportError: cannot import name 'remove_note_from_person_tool'`

- [ ] **Step 3: Write the minimal implementation**

Append to `src/gramps_mcp/tools/note_link.py`, after `add_note_to_family_tool`:

```python
async def remove_note_from_person_tool(
    person_handle: Optional[str] = None,
    person_gramps_id: Optional[str] = None,
    note_handle: Optional[str] = None,
    note_gramps_id: Optional[str] = None,
    db: Any = None,
) -> List[TextContent]:
    """
    Remove a note from a person's note_list.

    Does not delete the Note object itself. SQLite backend only.

    Args:
        person_handle: Handle of the person.
        person_gramps_id: Gramps ID of the person (alternative to person_handle).
        note_handle: Handle of the note to unlink.
        note_gramps_id: Gramps ID of the note (alternative to note_handle).
        db: GrampsSqliteDB instance (injected for tests; None uses get_client()).

    Returns:
        List[TextContent] with JSON result, person_handle, note_handle, note_count.

    Raises:
        GrampsAPIError: If backend is not SQLite, handles not found, or note
                        not linked to this person.
    """
    db = _require_sqlite_db(db, "remove_note_from_person")
    conn = db._conn

    person_handle = _resolve_handle(conn, "person", person_handle, person_gramps_id, "Person")
    note_handle = _resolve_handle(conn, "note", note_handle, note_gramps_id, "Note")

    person_data = _read_object(conn, "person", person_handle, "Person")
    note_list = person_data.get("note_list", [])

    new_list = [h for h in note_list if h != note_handle]
    if len(new_list) == len(note_list):
        raise GrampsAPIError(
            f"Note '{note_handle}' is not linked to person '{person_handle}'"
        )

    person_data["note_list"] = new_list

    with conn:
        _write_person(conn, person_handle, person_data)

    return [TextContent(type="text", text=json.dumps(
        {
            "result": "ok",
            "note_handle": note_handle,
            "person_handle": person_handle,
            "note_count": len(new_list),
        },
        ensure_ascii=False,
    ))]


async def remove_note_from_family_tool(
    family_handle: Optional[str] = None,
    family_gramps_id: Optional[str] = None,
    note_handle: Optional[str] = None,
    note_gramps_id: Optional[str] = None,
    db: Any = None,
) -> List[TextContent]:
    """
    Remove a note from a family's note_list.

    Does not delete the Note object itself. SQLite backend only.

    Args:
        family_handle: Handle of the family.
        family_gramps_id: Gramps ID of the family (alternative to family_handle).
        note_handle: Handle of the note to unlink.
        note_gramps_id: Gramps ID of the note (alternative to note_handle).
        db: GrampsSqliteDB instance (injected for tests; None uses get_client()).

    Returns:
        List[TextContent] with JSON result, family_handle, note_handle, note_count.

    Raises:
        GrampsAPIError: If backend is not SQLite, handles not found, or note
                        not linked to this family.
    """
    db = _require_sqlite_db(db, "remove_note_from_family")
    conn = db._conn

    family_handle = _resolve_handle(conn, "family", family_handle, family_gramps_id, "Family")
    note_handle = _resolve_handle(conn, "note", note_handle, note_gramps_id, "Note")

    family_data = _read_object(conn, "family", family_handle, "Family")
    note_list = family_data.get("note_list", [])

    new_list = [h for h in note_list if h != note_handle]
    if len(new_list) == len(note_list):
        raise GrampsAPIError(
            f"Note '{note_handle}' is not linked to family '{family_handle}'"
        )

    family_data["note_list"] = new_list

    with conn:
        _write_object(conn, "family", family_handle, family_data)

    return [TextContent(type="text", text=json.dumps(
        {
            "result": "ok",
            "note_handle": note_handle,
            "family_handle": family_handle,
            "note_count": len(new_list),
        },
        ensure_ascii=False,
    ))]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_note_link_tools.py -v`
Expected: All tests in the file PASS (20 tests across all 4 test classes: 9 in `TestAddNoteToPerson`, 5 in `TestAddNoteToFamily`, 4 in `TestRemoveNoteFromPerson`, 2 in `TestRemoveNoteFromFamily`)

- [ ] **Step 5: Commit**

```bash
git add src/gramps_mcp/tools/note_link.py tests/test_note_link_tools.py
uv run git commit -m "feat: add remove_note_from_person and remove_note_from_family tools"
```

---

### Task 4: Wire the 4 note-link tools into the server

**Files:**
- Modify: `src/gramps_mcp/models/parameters/link_edit_params.py` (append 4 param models)
- Modify: `src/gramps_mcp/tools/__init__.py` (export the 4 tools, update docstring)
- Modify: `src/gramps_mcp/server.py` (imports, 4 handlers, 4 `TOOL_REGISTRY` entries, `TOOL_GROUPS` updates)
- Create: `tests/test_note_link_registration.py`

**Interfaces:**
- Consumes: `add_note_to_person_tool`, `add_note_to_family_tool`, `remove_note_from_person_tool`, `remove_note_from_family_tool` from `src/gramps_mcp/tools/note_link.py` (Tasks 1-3).
- Produces: `AddNoteToPersonParams`, `AddNoteToFamilyParams`, `RemoveNoteFromPersonParams`, `RemoveNoteFromFamilyParams` pydantic models; 4 new `TOOL_REGISTRY` keys `"add_note_to_person"`, `"add_note_to_family"`, `"remove_note_from_person"`, `"remove_note_from_family"`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_note_link_registration.py`:

```python
"""
Registration tests for the note-link tools: confirms server.py wiring
(TOOL_REGISTRY entries, TOOL_GROUPS membership, param model shape) without
needing a live database.
"""


class TestNoteLinkRegistration:
    def test_tools_in_registry(self):
        from gramps_mcp.server import TOOL_REGISTRY

        for name in (
            "add_note_to_person",
            "add_note_to_family",
            "remove_note_from_person",
            "remove_note_from_family",
        ):
            assert name in TOOL_REGISTRY, f"{name} missing from TOOL_REGISTRY"
            assert "schema" in TOOL_REGISTRY[name]
            assert "handler" in TOOL_REGISTRY[name]
            assert "description" in TOOL_REGISTRY[name]

    def test_tools_in_groups(self):
        from gramps_mcp.server import TOOL_GROUPS

        assert "add_note_to_person" in TOOL_GROUPS["person"]
        assert "remove_note_from_person" in TOOL_GROUPS["person"]
        assert "add_note_to_family" in TOOL_GROUPS["family"]
        assert "remove_note_from_family" in TOOL_GROUPS["family"]

    def test_add_note_to_person_params_accepts_link_only(self):
        from gramps_mcp.models.parameters.link_edit_params import AddNoteToPersonParams

        params = AddNoteToPersonParams(person_handle="h1", note_handle="h2")
        assert params.text is None
        assert params.type is None

    def test_add_note_to_person_params_accepts_create(self):
        from gramps_mcp.models.parameters.link_edit_params import AddNoteToPersonParams

        params = AddNoteToPersonParams(person_handle="h1", text="hi", type="Research")
        assert params.note_handle is None

    def test_remove_note_params_shape(self):
        from gramps_mcp.models.parameters.link_edit_params import (
            RemoveNoteFromFamilyParams,
            RemoveNoteFromPersonParams,
        )

        p = RemoveNoteFromPersonParams(person_handle="h1", note_handle="h2")
        assert p.person_handle == "h1"
        f = RemoveNoteFromFamilyParams(family_handle="h1", note_handle="h2")
        assert f.family_handle == "h1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_note_link_registration.py -v`
Expected: FAIL with `ImportError: cannot import name 'AddNoteToPersonParams'`

- [ ] **Step 3: Add the 4 param models**

In `src/gramps_mcp/models/parameters/link_edit_params.py`, append at the end of the file (after `RemoveCitationFromEventParams`):

```python
class AddNoteToPersonParams(BaseModel):
    """Parameters for add_note_to_person tool."""

    person_handle: Optional[str] = Field(None, description="Handle of the person")
    person_gramps_id: Optional[str] = Field(
        None, description="Gramps ID of the person (e.g. 'I0001'). Alternative to person_handle."
    )
    note_handle: Optional[str] = Field(
        None, description="Handle of an existing note to link or update"
    )
    note_gramps_id: Optional[str] = Field(
        None,
        description="Gramps ID of an existing note (e.g. 'N0012'). Alternative to note_handle.",
    )
    text: Optional[str] = Field(
        None,
        description=(
            "Note text. Required when note_handle/note_gramps_id are both omitted "
            "(creates a new note). If given together with note_handle/note_gramps_id, "
            "overwrites the existing note's text."
        ),
    )
    type: Optional[str] = Field(
        None,
        description=(
            "Note type (e.g. 'Research'). Required when note_handle/note_gramps_id are "
            "both omitted (creates a new note). If given together with note_handle/"
            "note_gramps_id, overwrites the existing note's type."
        ),
    )


class AddNoteToFamilyParams(BaseModel):
    """Parameters for add_note_to_family tool."""

    family_handle: Optional[str] = Field(None, description="Handle of the family")
    family_gramps_id: Optional[str] = Field(
        None, description="Gramps ID of the family (e.g. 'F0001'). Alternative to family_handle."
    )
    note_handle: Optional[str] = Field(
        None, description="Handle of an existing note to link or update"
    )
    note_gramps_id: Optional[str] = Field(
        None,
        description="Gramps ID of an existing note (e.g. 'N0012'). Alternative to note_handle.",
    )
    text: Optional[str] = Field(
        None,
        description=(
            "Note text. Required when note_handle/note_gramps_id are both omitted "
            "(creates a new note). If given together with note_handle/note_gramps_id, "
            "overwrites the existing note's text."
        ),
    )
    type: Optional[str] = Field(
        None,
        description=(
            "Note type (e.g. 'Research'). Required when note_handle/note_gramps_id are "
            "both omitted (creates a new note). If given together with note_handle/"
            "note_gramps_id, overwrites the existing note's type."
        ),
    )


class RemoveNoteFromPersonParams(BaseModel):
    """Parameters for remove_note_from_person tool."""

    person_handle: Optional[str] = Field(None, description="Handle of the person")
    person_gramps_id: Optional[str] = Field(
        None, description="Gramps ID of the person (e.g. 'I0001'). Alternative to person_handle."
    )
    note_handle: Optional[str] = Field(None, description="Handle of the note to unlink")
    note_gramps_id: Optional[str] = Field(
        None, description="Gramps ID of the note (e.g. 'N0012'). Alternative to note_handle."
    )


class RemoveNoteFromFamilyParams(BaseModel):
    """Parameters for remove_note_from_family tool."""

    family_handle: Optional[str] = Field(None, description="Handle of the family")
    family_gramps_id: Optional[str] = Field(
        None, description="Gramps ID of the family (e.g. 'F0001'). Alternative to family_handle."
    )
    note_handle: Optional[str] = Field(None, description="Handle of the note to unlink")
    note_gramps_id: Optional[str] = Field(
        None, description="Gramps ID of the note (e.g. 'N0012'). Alternative to note_handle."
    )
```

- [ ] **Step 4: Export the 4 tools from `tools/__init__.py`**

In `src/gramps_mcp/tools/__init__.py`, change the module docstring at the top from:

```python
"""
Unified interface for all MCP tools.

This module exports 38 genealogy tools organized by category:
- Search & Discovery Tools (11): find_*, get_person, get_family
- Data Management Tools (9): create_*
- Analysis Tools (4): tree_stats, ancestors, descendants, recent_changes
- Database Lifecycle Tools (3): list/open/close_database
- Merge Tools (3): find_duplicate_persons, merge_persons, split_person
- Link Edit Tools (6): add/remove_event_to/from_person, add/remove_event_to/from_family, remove_child_from_family, move_attachment
- Citation Link Tools (2): add/remove_citation_to/from_event
"""
```

to:

```python
"""
Unified interface for all MCP tools.

This module exports 42 genealogy tools organized by category:
- Search & Discovery Tools (11): find_*, get_person, get_family
- Data Management Tools (9): create_*
- Analysis Tools (4): tree_stats, ancestors, descendants, recent_changes
- Database Lifecycle Tools (3): list/open/close_database
- Merge Tools (3): find_duplicate_persons, merge_persons, split_person
- Link Edit Tools (6): add/remove_event_to/from_person, add/remove_event_to/from_family, remove_child_from_family, move_attachment
- Citation Link Tools (2): add/remove_citation_to/from_event
- Note Link Tools (4): add/remove_note_to/from_person, add/remove_note_to/from_family
"""
```

Then add a new import block right after the existing `from .citation_link import (...)` block:

```python
from .citation_link import (
    add_citation_to_event_tool,
    remove_citation_from_event_tool,
)
from .note_link import (
    add_note_to_family_tool,
    add_note_to_person_tool,
    remove_note_from_family_tool,
    remove_note_from_person_tool,
)
```

Then add these 4 names to the `__all__` list, right after `"remove_citation_from_event_tool",`:

```python
    "remove_citation_from_event_tool",
    # Note Link Tools (SQLite backend only)
    "add_note_to_person_tool",
    "add_note_to_family_tool",
    "remove_note_from_person_tool",
    "remove_note_from_family_tool",
]
```

- [ ] **Step 5: Wire into `server.py`**

In `src/gramps_mcp/server.py`:

1. In the `from .models.parameters.link_edit_params import (...)` block (currently lines 39-48), add the 4 new params in alphabetical order:

```python
from .models.parameters.link_edit_params import (
    AddCitationToEventParams,
    AddEventToFamilyParams,
    AddEventToPersonParams,
    AddNoteToFamilyParams,
    AddNoteToPersonParams,
    MoveAttachmentParams,
    RemoveChildFromFamilyParams,
    RemoveCitationFromEventParams,
    RemoveEventFromFamilyParams,
    RemoveEventFromPersonParams,
    RemoveNoteFromFamilyParams,
    RemoveNoteFromPersonParams,
)
```

2. Right after the existing `from .tools.citation_link import (...)` block, add:

```python
from .tools.citation_link import (
    add_citation_to_event_tool,
    remove_citation_from_event_tool,
)
from .tools.note_link import (
    add_note_to_family_tool,
    add_note_to_person_tool,
    remove_note_from_family_tool,
    remove_note_from_person_tool,
)
```

3. After the `_handle_remove_citation_from_event` function, add 4 new handler functions:

```python
async def _handle_add_note_to_person(args: Dict) -> Any:
    """Handler for add_note_to_person."""
    return await add_note_to_person_tool(
        person_handle=args.get("person_handle"),
        person_gramps_id=args.get("person_gramps_id"),
        note_handle=args.get("note_handle"),
        note_gramps_id=args.get("note_gramps_id"),
        text=args.get("text"),
        type=args.get("type"),
    )


async def _handle_add_note_to_family(args: Dict) -> Any:
    """Handler for add_note_to_family."""
    return await add_note_to_family_tool(
        family_handle=args.get("family_handle"),
        family_gramps_id=args.get("family_gramps_id"),
        note_handle=args.get("note_handle"),
        note_gramps_id=args.get("note_gramps_id"),
        text=args.get("text"),
        type=args.get("type"),
    )


async def _handle_remove_note_from_person(args: Dict) -> Any:
    """Handler for remove_note_from_person."""
    return await remove_note_from_person_tool(
        person_handle=args.get("person_handle"),
        person_gramps_id=args.get("person_gramps_id"),
        note_handle=args.get("note_handle"),
        note_gramps_id=args.get("note_gramps_id"),
    )


async def _handle_remove_note_from_family(args: Dict) -> Any:
    """Handler for remove_note_from_family."""
    return await remove_note_from_family_tool(
        family_handle=args.get("family_handle"),
        family_gramps_id=args.get("family_gramps_id"),
        note_handle=args.get("note_handle"),
        note_gramps_id=args.get("note_gramps_id"),
    )
```

4. In `TOOL_REGISTRY`, right after the `"move_attachment"` entry (the last entry before the closing `}`), add:

```python
    "add_note_to_person": {
        "description": (
            "Link a note to a person. Three modes: pass note_handle/note_gramps_id alone "
            "to link an existing note; pass text+type alone to create a new note and link "
            "it; pass note_handle/note_gramps_id together with text and/or type to "
            "overwrite the existing note's content and ensure it's linked. Idempotent "
            "link-only calls return result='no_change'. SQLite backend only."
        ),
        "schema": AddNoteToPersonParams,
        "handler": _handle_add_note_to_person,
    },
    "remove_note_from_person": {
        "description": (
            "Remove a note from a person's note_list. Does not delete the Note object "
            "itself — use delete_object for that. SQLite backend only."
        ),
        "schema": RemoveNoteFromPersonParams,
        "handler": _handle_remove_note_from_person,
    },
    "add_note_to_family": {
        "description": (
            "Link a note to a family. Same three modes as add_note_to_person: link "
            "existing, create+link, or update+link. SQLite backend only."
        ),
        "schema": AddNoteToFamilyParams,
        "handler": _handle_add_note_to_family,
    },
    "remove_note_from_family": {
        "description": (
            "Remove a note from a family's note_list. Does not delete the Note object "
            "itself — use delete_object for that. SQLite backend only."
        ),
        "schema": RemoveNoteFromFamilyParams,
        "handler": _handle_remove_note_from_family,
    },
```

5. In `TOOL_GROUPS`, update the `"person"` and `"family"` entries:

```python
    "person": [
        "create_person", "get_person",
        "merge_persons", "split_person", "find_duplicate_persons",
        "add_dna_match", "get_dna_matches", "update_dna_match",
        "add_note_to_person", "remove_note_from_person",
    ],
```

```python
    "family": [
        "create_family", "get_family",
        "merge_families", "remove_child_from_family",
        "add_event_to_family", "remove_event_from_family",
        "add_note_to_family", "remove_note_from_family",
    ],
```

- [ ] **Step 6: Run the registration test and the full note-link suite**

Run: `uv run pytest tests/test_note_link_registration.py tests/test_note_link_tools.py -v`
Expected: All tests PASS

- [ ] **Step 7: Run the full non-live-API test suite to check for regressions**

Run:
```bash
uv run pytest tests/ \
  --ignore=tests/test_server.py \
  --ignore=tests/test_data_management.py \
  --ignore=tests/test_search_basic.py \
  --ignore=tests/test_client.py \
  --ignore=tests/test_analysis.py \
  --ignore=tests/test_complete_workflow.py \
  --ignore=tests/test_auth_integration.py \
  --ignore=tests/test_search_details.py \
  -v --tb=short
```
Expected: All tests PASS (this mirrors the CI job in `.github/workflows/test.yml`)

- [ ] **Step 8: Commit**

```bash
git add src/gramps_mcp/models/parameters/link_edit_params.py \
        src/gramps_mcp/tools/__init__.py \
        src/gramps_mcp/server.py \
        tests/test_note_link_registration.py
uv run git commit -m "feat: register add/remove_note_to/from_person/family tools"
```

---

### Task 5: `get_note` tool

**Files:**
- Modify: `src/gramps_mcp/tools/search_details.py` (append `get_note_tool`)
- Modify: `src/gramps_mcp/server.py` (import, `GetNoteParams`, `TOOL_REGISTRY`/`TOOL_GROUPS` entries)
- Modify: `tests/test_sqlite_detail_tools.py` (append `TestGetNoteTool`)

**Interfaces:**
- Consumes: `client.make_api_call(ApiCalls.GET_NOTE, tree_id=..., handle=...)`, `client.make_api_call(ApiCalls.GET_PEOPLE, ...)`, `client.make_api_call(ApiCalls.GET_FAMILIES, ...)` (all already exist on every backend); `resolve_handles` from `src/gramps_mcp/gramps_id.py` (already handles `"note"` via `_OBJ_TYPE_TO_API`); `with_client` decorator, `_format_error_response`, `get_settings` (all already in `search_details.py`).
- Produces: `get_note_tool(client, arguments: Dict) -> List[TextContent]` (decorated with `@with_client`, so the public call signature via the registry is `get_note_tool(arguments: Dict)`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_sqlite_detail_tools.py`, after the `TestGetPlaceTool` class:

```python
# ---------------------------------------------------------------------------
# get_note_tool
# ---------------------------------------------------------------------------

class TestGetNoteTool:
    @pytest.mark.asyncio
    async def test_returns_full_note_text(self, write_client):
        from gramps_mcp.tools.search_details import get_note_tool
        # N0001 = "John Smith was a notable person in his community." (h_no_john)
        result = await get_note_tool.__wrapped__(write_client, {"gramps_id": "N0001"})
        text = _result_text(result)
        assert "N0001" in text
        assert "John Smith was a notable person in his community." in text

    @pytest.mark.asyncio
    async def test_not_found(self, write_client):
        from gramps_mcp.tools.search_details import get_note_tool
        result = await get_note_tool.__wrapped__(write_client, {"gramps_id": "N9999"})
        assert "not found" in _result_text(result)

    @pytest.mark.asyncio
    async def test_missing_gramps_id(self, write_client):
        from gramps_mcp.tools.search_details import get_note_tool
        result = await get_note_tool.__wrapped__(write_client, {})
        assert "Error" in _result_text(result)

    @pytest.mark.asyncio
    async def test_backlinks_person_and_family(self, write_client):
        from gramps_mcp.tools.search_details import get_note_tool
        # N0001 (h_no_john) is linked to person I0001 and family F0001 in conftest_sqlite
        result = await get_note_tool.__wrapped__(write_client, {"gramps_id": "N0001"})
        text = _result_text(result)
        assert "I0001" in text
        assert "F0001" in text

    @pytest.mark.asyncio
    async def test_no_backlinks_found(self, write_client):
        from gramps_mcp.tools.search_details import get_note_tool

        conn = write_client._db._conn
        data = {
            "_class": "Note", "handle": "h_no_orphan", "gramps_id": "N0099", "format": 0,
            "text": {"_class": "StyledText", "string": "Orphan note", "tags": []},
            "type": {"_class": "NoteType", "value": 1, "string": "General"},
            "tag_list": [], "change": 0, "private": False,
        }
        conn.execute(
            "INSERT INTO note (handle, gramps_id, json_data, format, change, private) "
            "VALUES (?,?,?,0,0,0)",
            ("h_no_orphan", "N0099", json.dumps(data)),
        )
        conn.commit()

        result = await get_note_tool.__wrapped__(write_client, {"gramps_id": "N0099"})
        text = _result_text(result)
        assert "Orphan note" in text
        assert "Keine Verkn" in text  # "Keine Verknüpfungen gefunden"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_sqlite_detail_tools.py::TestGetNoteTool -v`
Expected: FAIL/ERROR with `ImportError: cannot import name 'get_note_tool'`

- [ ] **Step 3: Write the minimal implementation**

In `src/gramps_mcp/tools/search_details.py`, add `get_note_tool` right after `get_place_tool` (before `merge_places_tool`):

```python
@with_client
async def get_note_tool(client, arguments: Dict) -> List[TextContent]:
    """
    Get full note text by gramps_id and find which persons/families have it linked.
    Scans all persons and families — fact-based, no guessing. Notes attached only to
    other object types (events, citations, sources, places, media) are not found here.
    """
    try:
        from ..gramps_id import resolve_handles
        arguments = await resolve_handles(arguments, {"handle": "note"}, client)
        handle = arguments.get("handle")
        settings = get_settings()
        tree_id = settings.gramps_tree_id
        if not handle:
            raise ValueError("gramps_id is required")
        note = await client.make_api_call(ApiCalls.GET_NOTE, tree_id=tree_id, handle=handle)
        if not note:
            return [TextContent(type="text", text=f"Note {handle} not found")]
        gramps_id = note.get("gramps_id", handle)
        note_type = note.get("type", "Unknown")
        text = note.get("text", {}).get("string", "")

        lines = [f"## {note_type} Note — {gramps_id} [{handle}]", "", text, ""]

        # Find persons and families with this note linked — scan all, GQL cannot search inside arrays
        all_persons = await client.make_api_call(
            ApiCalls.GET_PEOPLE, tree_id=tree_id, params={"pagesize": 99999}
        )
        all_families = await client.make_api_call(
            ApiCalls.GET_FAMILIES, tree_id=tree_id, params={"pagesize": 99999}
        )

        found = []
        for person in all_persons:
            if handle in person.get("note_list", []):
                pn = person.get("primary_name", {})
                given = pn.get("first_name", "")
                sl = pn.get("surname_list", [])
                surname = sl[0].get("surname", "") if sl else ""
                name = f"{given} {surname}".strip() or "?"
                pid = person.get("gramps_id", "")
                found.append(f"* Person: {name} ({pid})")

        for family in all_families:
            if handle in family.get("note_list", []):
                fid = family.get("gramps_id", "")
                found.append(f"* Familie: {fid}")

        lines.append("**Verlinkt mit:**")
        if found:
            lines.extend(found)
        else:
            lines.append("* Keine Verknüpfungen gefunden")

        return [TextContent(type="text", text="\n".join(lines))]

    except Exception as e:
        return _format_error_response(e, "note details retrieval")
```

Then in `src/gramps_mcp/server.py`:

1. In the `from .tools.search_details import (...)` block (currently lines 116-128), add `get_note_tool` alphabetically between `get_family_tool` and `get_person_tool`:

```python
from .tools.search_details import (
    find_duplicate_citations_tool,
    find_duplicate_events_tool,
    get_event_tool,
    get_family_tool,
    get_note_tool,
    get_person_tool,
    get_place_tool,
    get_type_tool,
    merge_citations_tool,
    merge_events_tool,
    merge_families_tool,
    merge_places_tool,
)
```

2. Add a `GetNoteParams` class right after `GetPlaceParams` (currently around line 194-196):

```python
class GetNoteParams(BaseModel):
    gramps_id: str = Field(..., description="Gramps note ID (e.g. 'N0001')")
```

3. In `TOOL_REGISTRY`, add a `"get_note"` entry right after the `"get_place"` entry:

```python
    "get_note": {
        "description": (
            "Get full note text by gramps_id and find which persons/families have this "
            "note linked. Scans all persons and families — fact-based, no guessing. "
            "Notes attached only to other object types (events, citations, sources, "
            "places, media) are not found by this scan."
        ),
        "schema": GetNoteParams,
        "handler": get_note_tool,
    },
```

4. In `TOOL_GROUPS`, add `"get_note"` to both the `"person"` and `"family"` lists (from Task 4):

```python
    "person": [
        "create_person", "get_person",
        "merge_persons", "split_person", "find_duplicate_persons",
        "add_dna_match", "get_dna_matches", "update_dna_match",
        "add_note_to_person", "remove_note_from_person", "get_note",
    ],
```

```python
    "family": [
        "create_family", "get_family",
        "merge_families", "remove_child_from_family",
        "add_event_to_family", "remove_event_from_family",
        "add_note_to_family", "remove_note_from_family", "get_note",
    ],
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_sqlite_detail_tools.py::TestGetNoteTool -v`
Expected: All 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/gramps_mcp/tools/search_details.py src/gramps_mcp/server.py tests/test_sqlite_detail_tools.py
uv run git commit -m "feat: add get_note tool with person/family backlink scan"
```

---

### Task 6: Final verification

**Files:** None (verification only)

**Interfaces:** None — this task only runs commands.

- [ ] **Step 1: Run the full non-live-API suite (mirrors CI)**

```bash
uv run pytest tests/ \
  --ignore=tests/test_server.py \
  --ignore=tests/test_data_management.py \
  --ignore=tests/test_search_basic.py \
  --ignore=tests/test_client.py \
  --ignore=tests/test_analysis.py \
  --ignore=tests/test_complete_workflow.py \
  --ignore=tests/test_auth_integration.py \
  --ignore=tests/test_search_details.py \
  -v --tb=short
```
Expected: All tests PASS, including every test added in Tasks 1-5

- [ ] **Step 2: Run ruff on the changed files**

```bash
uv run ruff check src/gramps_mcp/tools/note_link.py \
  src/gramps_mcp/models/parameters/link_edit_params.py \
  src/gramps_mcp/tools/search_details.py \
  src/gramps_mcp/server.py \
  src/gramps_mcp/tools/__init__.py
```
Expected: No errors introduced by this feature's code. `server.py` and `search_details.py` are large pre-existing files — if ruff reports issues on lines you did not touch in this plan, they are out of scope; only fix issues on the lines added/modified by Tasks 1-5 (the new `note_link.py` content, the new param models, and the new imports/handlers/registry entries you just added).

- [ ] **Step 3: Run black on the new/changed files**

```bash
uv run black --check src/gramps_mcp/tools/note_link.py \
  src/gramps_mcp/models/parameters/link_edit_params.py \
  src/gramps_mcp/tools/search_details.py \
  src/gramps_mcp/server.py \
  src/gramps_mcp/tools/__init__.py \
  tests/test_note_link_tools.py \
  tests/test_note_link_registration.py
```
If it reports files that would be reformatted, run `uv run black <files>` (without `--check`) and re-run the full test suite from Step 1 to confirm nothing broke.

- [ ] **Step 4: Confirm file sizes stay under the 500-line CLAUDE.md limit**

```bash
wc -l src/gramps_mcp/tools/note_link.py
```
Expected: Under 500 lines (should be roughly 260-300 lines). `search_details.py` will remain over 500 (pre-existing, out of scope per the design spec).

- [ ] **Step 5: Commit any formatting fixes**

Only if Steps 2-3 required changes:

```bash
git add -u
uv run git commit -m "style: ruff/black fixes for note-link tools"
```

---

## Closing note

Once all 6 tasks pass, use **superpowers:finishing-a-development-branch** to decide how to integrate `feature/note-link-tools` into `dev` (per the project's branch workflow in `.claude/CLAUDE.md`: push the branch, open a PR with `gh pr create --base dev --repo hf-mrdachner/gramps-mcp`, squash-merge after CI passes, delete the feature branch).
