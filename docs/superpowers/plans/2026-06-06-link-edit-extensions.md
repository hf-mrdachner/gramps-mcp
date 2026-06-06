# Link-Edit Extensions & MCP Discoverability — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `remove_event_from_person`, `add_citation_to_event`, and `remove_citation_from_event` tools; retrofit `add_event_to_person` with gramps_id; and add MCP `instructions` + tool-group resources for discoverability.

**Architecture:** Extract shared SQLite helpers (including new `_resolve_handle`) into `tools/_sqlite_helpers.py`. Tools resolve gramps_id internally via the injected conn — no server-level `resolve_handles` needed for new tools. Citation tools live in a new `tools/citation_link.py`. The FastMCP `instructions` field and six `gramps://tools/<group>` resources provide session-start discoverability.

**Tech Stack:** Python, MCP Python SDK (`FastMCP`), `sqlite3`, `pydantic`, `pytest`.

**Spec:** `docs/superpowers/specs/2026-06-06-link-edit-extensions-design.md`

---

## File Map

| Action | File | Responsibility |
|---|---|---|
| Create | `src/gramps_mcp/tools/_sqlite_helpers.py` | Shared helpers + `_resolve_handle` |
| Modify | `src/gramps_mcp/tools/link_edit.py` | Remove helpers, add `remove_event_from_person`, retrofit `add_event_to_person` |
| Create | `src/gramps_mcp/tools/citation_link.py` | `add_citation_to_event` + `remove_citation_from_event` |
| Modify | `src/gramps_mcp/models/parameters/link_edit_params.py` | Three new Params classes |
| Modify | `src/gramps_mcp/tools/__init__.py` | Export three new tools |
| Modify | `src/gramps_mcp/server.py` | New handlers, registry entries, `instructions`, resources |
| Modify | `.claude/CLAUDE.md` | MCP tool discovery rule |
| Modify | `tests/test_link_edit_tools.py` | gramps_id tests + `remove_event_from_person` tests |
| Create | `tests/test_citation_link_tools.py` | Full test suite for citation tools |

---

## Task 1 — Extract helpers into `_sqlite_helpers.py`

**Files:**
- Create: `src/gramps_mcp/tools/_sqlite_helpers.py`
- Modify: `src/gramps_mcp/tools/link_edit.py`

- [ ] **Step 1.1: Write the new helpers module**

Create `src/gramps_mcp/tools/_sqlite_helpers.py` with this exact content:

```python
"""
Shared SQLite helpers for link-edit tools.

All helpers require an open sqlite3.Connection and operate directly on JSON data.
"""

import json
import time
from typing import Any, Dict, List, Optional, Tuple

from gramps_mcp._gramps_sqlite import GrampsSqliteDB, _compute_birth_death_indices, _denorm_type
from gramps_mcp.client import GrampsAPIError


def _read_object(conn: Any, table: str, handle: str, label: str) -> Dict:
    """
    Read and JSON-parse an object from the DB.

    Args:
        conn: Open sqlite3.Connection.
        table: Table name (e.g. 'person', 'event').
        handle: Object handle.
        label: Human-readable label for error messages.

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
        (
            json.dumps(person_data, ensure_ascii=False),
            birth_idx,
            death_idx,
            person_data["change"],
            handle,
        ),
    )


def _write_object(conn: Any, table: str, handle: str, data: Dict) -> None:
    """
    Write any non-person object JSON back to DB, updating change timestamp.

    Args:
        conn: Open sqlite3.Connection within an active transaction.
        table: Table name (e.g. 'event', 'family').
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


def _resolve_handle(
    conn: Any,
    table: str,
    handle: Optional[str],
    gramps_id: Optional[str],
    label: str,
) -> str:
    """
    Return handle directly or resolve via gramps_id SQLite lookup.

    Args:
        conn: Open sqlite3.Connection.
        table: Table name (e.g. 'person', 'event', 'citation').
        handle: Object handle (returned as-is if set).
        gramps_id: Gramps ID (e.g. 'I0042') used if handle is None.
        label: Human-readable label for error messages.

    Returns:
        Resolved handle string.

    Raises:
        GrampsAPIError: If neither handle nor gramps_id given, or gramps_id not found.
    """
    if handle:
        return handle
    if gramps_id:
        row = conn.execute(
            f"SELECT handle FROM {table} WHERE gramps_id = ?",  # noqa: S608
            (gramps_id,),
        ).fetchone()
        if not row:
            raise GrampsAPIError(f"{label}: gramps_id '{gramps_id}' not found")
        return row[0]
    raise GrampsAPIError(f"{label}: handle or gramps_id required")
```

- [ ] **Step 1.2: Replace helpers in `link_edit.py` with imports**

In `src/gramps_mcp/tools/link_edit.py`, replace lines 1–114 (the module docstring + four helper functions) with:

```python
"""
Link-edit MCP tools — SQLite-only tools for manipulating links between existing
Gramps objects without replacing entire lists.

All tools require the SQLite backend (GrampsSqliteClient). They manipulate raw
Gramps JSON directly to avoid the normalization round-trip and use injectable
db= parameters for testability (same pattern as delete_object_tool).
"""

import json
from typing import Any, Dict, List, Optional, Tuple

from mcp.types import TextContent

from gramps_mcp._gramps_sqlite import _denorm_type
from gramps_mcp.client import GrampsAPIError
from gramps_mcp.tools._sqlite_helpers import (
    _read_object,
    _require_sqlite_db,
    _resolve_handle,
    _write_object,
    _write_person,
)
```

Remove the `import time` (now inside `_sqlite_helpers`). Keep everything below line 114 unchanged.

- [ ] **Step 1.3: Run existing tests to verify no regression**

```
uv run pytest tests/test_link_edit_tools.py -xvs
```

Expected: all tests pass (same behaviour, just moved helpers).

- [ ] **Step 1.4: Commit**

```
git add src/gramps_mcp/tools/_sqlite_helpers.py src/gramps_mcp/tools/link_edit.py
git commit -m "refactor: extract SQLite helpers into _sqlite_helpers.py"
```

---

## Task 2 — Retrofit `add_event_to_person` with gramps_id

**Files:**
- Modify: `src/gramps_mcp/tools/link_edit.py` (tool function signature)
- Modify: `src/gramps_mcp/server.py` (handler)
- Modify: `tests/test_link_edit_tools.py` (new gramps_id tests)

- [ ] **Step 2.1: Write the failing gramps_id tests first**

Append to `tests/test_link_edit_tools.py` (inside the file, after the existing `TestAddEventToPerson` class):

```python
class TestAddEventToPersonGrampsId:
    def test_add_via_person_gramps_id(self, fresh_db):
        from gramps_mcp.tools.link_edit import add_event_to_person_tool

        db, conn = fresh_db
        _insert_person(conn, "h_pe", "I0001")
        _insert_event(conn, "h_ev", "E0001", 42)

        result = asyncio.run(
            add_event_to_person_tool(
                person_gramps_id="I0001", event_handle="h_ev", db=db
            )
        )
        data = json.loads(result[0].text)
        assert data["result"] == "ok"
        assert data["event_ref_count"] == 1

    def test_add_via_event_gramps_id(self, fresh_db):
        from gramps_mcp.tools.link_edit import add_event_to_person_tool

        db, conn = fresh_db
        _insert_person(conn, "h_pe", "I0001")
        _insert_event(conn, "h_ev", "E0001", 42)

        result = asyncio.run(
            add_event_to_person_tool(
                person_handle="h_pe", event_gramps_id="E0001", db=db
            )
        )
        data = json.loads(result[0].text)
        assert data["result"] == "ok"

    def test_error_on_unknown_person_gramps_id(self, fresh_db):
        from gramps_mcp.tools.link_edit import add_event_to_person_tool

        db, conn = fresh_db
        _insert_event(conn, "h_ev", "E0001", 42)

        with pytest.raises(GrampsAPIError, match="gramps_id 'I9999' not found"):
            asyncio.run(
                add_event_to_person_tool(
                    person_gramps_id="I9999", event_handle="h_ev", db=db
                )
            )

    def test_error_when_neither_handle_nor_gramps_id(self, fresh_db):
        from gramps_mcp.tools.link_edit import add_event_to_person_tool

        db, conn = fresh_db
        _insert_person(conn, "h_pe", "I0001")
        _insert_event(conn, "h_ev", "E0001", 42)

        with pytest.raises(GrampsAPIError, match="handle or gramps_id required"):
            asyncio.run(
                add_event_to_person_tool(event_handle="h_ev", db=db)
            )
```

- [ ] **Step 2.2: Run to verify tests fail**

```
uv run pytest tests/test_link_edit_tools.py::TestAddEventToPersonGrampsId -xvs
```

Expected: FAIL — `add_event_to_person_tool` doesn't accept gramps_id params yet.

- [ ] **Step 2.3: Update `add_event_to_person_tool` signature in `link_edit.py`**

Replace the existing `async def add_event_to_person_tool(...)` function with:

```python
async def add_event_to_person_tool(
    person_handle: Optional[str] = None,
    event_handle: Optional[str] = None,
    person_gramps_id: Optional[str] = None,
    event_gramps_id: Optional[str] = None,
    role: str = "Primary",
    db: Any = None,
) -> List[TextContent]:
    """
    Append an event reference to a person's event_ref_list without replacing it.

    Automatically updates birth_ref_index and death_ref_index if a Birth or
    Death event is added. SQLite backend only.

    Args:
        person_handle: Handle of the person.
        event_handle: Handle of the event to link (must already exist).
        person_gramps_id: Gramps ID of the person (alternative to person_handle).
        event_gramps_id: Gramps ID of the event (alternative to event_handle).
        role: Role of the person in the event (default: 'Primary').
        db: GrampsSqliteDB instance (injected for tests; None uses get_client()).

    Returns:
        List[TextContent] with JSON result.

    Raises:
        GrampsAPIError: If backend is not SQLite or objects not found.
    """
    db = _require_sqlite_db(db, "add_event_to_person")
    conn = db._conn

    person_handle = _resolve_handle(conn, "person", person_handle, person_gramps_id, "Person")
    event_handle = _resolve_handle(conn, "event", event_handle, event_gramps_id, "Event")

    person_data = _read_object(conn, "person", person_handle, "Person")

    if not conn.execute(
        "SELECT handle FROM event WHERE handle = ?",  # noqa: S608
        (event_handle,),
    ).fetchone():
        raise GrampsAPIError(f"Event with handle '{event_handle}' not found")

    event_ref_list = person_data.get("event_ref_list", [])

    existing_handles = [
        e.get("ref") for e in event_ref_list if isinstance(e, dict)
    ]
    if event_handle in existing_handles:
        return [TextContent(type="text", text=json.dumps(
            {
                "result": "no_change",
                "message": f"Event '{event_handle}' is already linked to this person.",
            },
            ensure_ascii=False,
        ))]

    new_ref = {
        "_class": "EventRef",
        "ref": event_handle,
        "role": _denorm_type(role, "EventRoleType"),
        "private": False,
        "note_list": [],
        "attribute_list": [],
    }
    event_ref_list.append(new_ref)
    person_data["event_ref_list"] = event_ref_list

    with conn:
        _write_person(conn, person_handle, person_data)

    return [TextContent(type="text", text=json.dumps(
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
    ))]
```

- [ ] **Step 2.4: Simplify the server handler for `add_event_to_person`**

In `src/gramps_mcp/server.py`, replace `_handle_add_event_to_person`:

```python
async def _handle_add_event_to_person(args: Dict) -> Any:
    """Handler for add_event_to_person."""
    return await add_event_to_person_tool(
        person_handle=args.get("person_handle"),
        event_handle=args.get("event_handle"),
        person_gramps_id=args.get("person_gramps_id"),
        event_gramps_id=args.get("event_gramps_id"),
        role=args.get("role", "Primary"),
    )
```

- [ ] **Step 2.5: Run all tests**

```
uv run pytest tests/test_link_edit_tools.py -xvs
```

Expected: all pass including new `TestAddEventToPersonGrampsId`.

- [ ] **Step 2.6: Commit**

```
git add src/gramps_mcp/tools/link_edit.py src/gramps_mcp/server.py tests/test_link_edit_tools.py
git commit -m "feat: add gramps_id support to add_event_to_person"
```

---

## Task 3 — Add `remove_event_from_person`

**Files:**
- Modify: `src/gramps_mcp/tools/link_edit.py`
- Modify: `src/gramps_mcp/models/parameters/link_edit_params.py`
- Modify: `src/gramps_mcp/tools/__init__.py`
- Modify: `src/gramps_mcp/server.py`
- Modify: `tests/test_link_edit_tools.py`

- [ ] **Step 3.1: Write failing tests**

Append to `tests/test_link_edit_tools.py`:

```python
class TestRemoveEventFromPerson:
    def test_event_removed_from_person(self, fresh_db):
        from gramps_mcp.tools.link_edit import remove_event_from_person_tool

        db, conn = fresh_db
        _insert_event(conn, "h_ev", "E0001", 42)
        _insert_person(conn, "h_pe", "I0001", event_ref_list=[
            {"_class": "EventRef", "ref": "h_ev",
             "role": {"_class": "EventRoleType", "value": 1, "string": ""},
             "private": False, "note_list": [], "attribute_list": []}
        ])

        result = asyncio.run(
            remove_event_from_person_tool(
                person_handle="h_pe", event_handle="h_ev", db=db
            )
        )
        data = json.loads(result[0].text)
        assert data["result"] == "ok"
        assert data["event_ref_count"] == 0

        row = conn.execute(
            "SELECT json_data FROM person WHERE handle = 'h_pe'"
        ).fetchone()
        person_data = json.loads(row["json_data"])
        refs = [e["ref"] for e in person_data.get("event_ref_list", [])]
        assert "h_ev" not in refs

    def test_birth_ref_index_reset_after_birth_event_removed(self, fresh_db):
        from gramps_mcp.tools.link_edit import remove_event_from_person_tool

        db, conn = fresh_db
        _insert_event(conn, "h_birth", "E0001", 12)  # Birth = 12
        _insert_event(conn, "h_other", "E0002", 42)
        _insert_person(conn, "h_pe", "I0001", event_ref_list=[
            {"_class": "EventRef", "ref": "h_birth",
             "role": {"_class": "EventRoleType", "value": 1, "string": ""},
             "private": False, "note_list": [], "attribute_list": []},
            {"_class": "EventRef", "ref": "h_other",
             "role": {"_class": "EventRoleType", "value": 1, "string": ""},
             "private": False, "note_list": [], "attribute_list": []},
        ])
        conn.execute("UPDATE person SET birth_ref_index = 0 WHERE handle = 'h_pe'")
        conn.commit()

        asyncio.run(
            remove_event_from_person_tool(
                person_handle="h_pe", event_handle="h_birth", db=db
            )
        )

        row = conn.execute(
            "SELECT birth_ref_index FROM person WHERE handle = 'h_pe'"
        ).fetchone()
        assert row["birth_ref_index"] == -1

    def test_remove_via_gramps_ids(self, fresh_db):
        from gramps_mcp.tools.link_edit import remove_event_from_person_tool

        db, conn = fresh_db
        _insert_event(conn, "h_ev", "E0001", 42)
        _insert_person(conn, "h_pe", "I0001", event_ref_list=[
            {"_class": "EventRef", "ref": "h_ev",
             "role": {"_class": "EventRoleType", "value": 1, "string": ""},
             "private": False, "note_list": [], "attribute_list": []}
        ])

        result = asyncio.run(
            remove_event_from_person_tool(
                person_gramps_id="I0001", event_gramps_id="E0001", db=db
            )
        )
        data = json.loads(result[0].text)
        assert data["result"] == "ok"

    def test_error_when_event_not_linked(self, fresh_db):
        from gramps_mcp.tools.link_edit import remove_event_from_person_tool

        db, conn = fresh_db
        _insert_event(conn, "h_ev", "E0001", 42)
        _insert_person(conn, "h_pe", "I0001")

        with pytest.raises(GrampsAPIError, match="not linked"):
            asyncio.run(
                remove_event_from_person_tool(
                    person_handle="h_pe", event_handle="h_ev", db=db
                )
            )
```

- [ ] **Step 3.2: Run to verify tests fail**

```
uv run pytest tests/test_link_edit_tools.py::TestRemoveEventFromPerson -xvs
```

Expected: FAIL — `remove_event_from_person_tool` not defined.

- [ ] **Step 3.3: Implement `remove_event_from_person_tool` in `link_edit.py`**

Add after the `add_event_to_person_tool` function:

```python
# ---------------------------------------------------------------------------
# Tool 4: remove_event_from_person
# ---------------------------------------------------------------------------


async def remove_event_from_person_tool(
    person_handle: Optional[str] = None,
    event_handle: Optional[str] = None,
    person_gramps_id: Optional[str] = None,
    event_gramps_id: Optional[str] = None,
    db: Any = None,
) -> List[TextContent]:
    """
    Remove an event reference from a person's event_ref_list.

    Automatically recalculates birth_ref_index and death_ref_index after
    removal. Does not delete the event object itself. SQLite backend only.

    Args:
        person_handle: Handle of the person.
        event_handle: Handle of the event to unlink.
        person_gramps_id: Gramps ID of the person (alternative to person_handle).
        event_gramps_id: Gramps ID of the event (alternative to event_handle).
        db: GrampsSqliteDB instance (injected for tests; None uses get_client()).

    Returns:
        List[TextContent] with JSON result, person_handle, event_handle,
        event_ref_count, birth_ref_index, death_ref_index.

    Raises:
        GrampsAPIError: If backend is not SQLite, handles not found, or event
                        not linked to this person.
    """
    db = _require_sqlite_db(db, "remove_event_from_person")
    conn = db._conn

    person_handle = _resolve_handle(conn, "person", person_handle, person_gramps_id, "Person")
    event_handle = _resolve_handle(conn, "event", event_handle, event_gramps_id, "Event")

    person_data = _read_object(conn, "person", person_handle, "Person")
    event_ref_list = person_data.get("event_ref_list", [])

    new_refs = [
        e for e in event_ref_list
        if not (isinstance(e, dict) and e.get("ref") == event_handle)
    ]
    if len(new_refs) == len(event_ref_list):
        raise GrampsAPIError(
            f"Event '{event_handle}' is not linked to person '{person_handle}'"
        )

    person_data["event_ref_list"] = new_refs

    with conn:
        _write_person(conn, person_handle, person_data)

    return [TextContent(type="text", text=json.dumps(
        {
            "result": "ok",
            "person_handle": person_handle,
            "event_handle": event_handle,
            "event_ref_count": len(new_refs),
            "birth_ref_index": person_data.get("birth_ref_index", -1),
            "death_ref_index": person_data.get("death_ref_index", -1),
        },
        ensure_ascii=False,
    ))]
```

- [ ] **Step 3.4: Run tests**

```
uv run pytest tests/test_link_edit_tools.py::TestRemoveEventFromPerson -xvs
```

Expected: all 4 tests pass.

- [ ] **Step 3.5: Add `RemoveEventFromPersonParams` to `link_edit_params.py`**

Append to `src/gramps_mcp/models/parameters/link_edit_params.py`:

```python
class RemoveEventFromPersonParams(BaseModel):
    """Parameters for remove_event_from_person tool."""

    person_handle: Optional[str] = Field(
        None, description="Handle of the person"
    )
    person_gramps_id: Optional[str] = Field(
        None, description="Gramps ID of the person (e.g. 'I0042'). Alternative to person_handle."
    )
    event_handle: Optional[str] = Field(
        None, description="Handle of the event to unlink"
    )
    event_gramps_id: Optional[str] = Field(
        None, description="Gramps ID of the event (e.g. 'E0007'). Alternative to event_handle."
    )
```

- [ ] **Step 3.6: Register in `server.py`**

**a) Add import** — extend the `link_edit` import block in `server.py`:

```python
from .tools.link_edit import (
    add_event_to_person_tool,
    move_attachment_tool,
    remove_child_from_family_tool,
    remove_event_from_person_tool,
)
```

**b) Add import for Params** — extend the `link_edit_params` import:

```python
from .models.parameters.link_edit_params import (
    AddEventToPersonParams,
    MoveAttachmentParams,
    RemoveChildFromFamilyParams,
    RemoveEventFromPersonParams,
)
```

**c) Add handler function** — after `_handle_add_event_to_person`:

```python
async def _handle_remove_event_from_person(args: Dict) -> Any:
    """Handler for remove_event_from_person."""
    return await remove_event_from_person_tool(
        person_handle=args.get("person_handle"),
        event_handle=args.get("event_handle"),
        person_gramps_id=args.get("person_gramps_id"),
        event_gramps_id=args.get("event_gramps_id"),
    )
```

**d) Add to `TOOL_REGISTRY`** — after the `add_event_to_person` entry:

```python
"remove_event_from_person": {
    "description": (
        "Remove an event reference from a person's event_ref_list. "
        "Automatically recalculates birth_ref_index and death_ref_index after removal. "
        "Does not delete the event object itself — use delete_object for that. "
        "SQLite backend only."
    ),
    "schema": RemoveEventFromPersonParams,
    "handler": _handle_remove_event_from_person,
},
```

- [ ] **Step 3.7: Export from `tools/__init__.py`**

The existing `link_edit` tools are NOT currently exported from `__init__.py` — the
server imports them directly. Add a new import block for all link_edit tools (the
existing ones plus the new one) so `__init__.py` becomes the single source of truth:

```python
from .link_edit import (
    add_event_to_person_tool,
    move_attachment_tool,
    remove_child_from_family_tool,
    remove_event_from_person_tool,
)
```

And add to `__all__` (four entries, all new to `__all__`):
```python
"add_event_to_person_tool",
"move_attachment_tool",
"remove_child_from_family_tool",
"remove_event_from_person_tool",
```

- [ ] **Step 3.8: Run full test suite**

```
uv run pytest tests/test_link_edit_tools.py -xvs
```

Expected: all tests pass.

- [ ] **Step 3.9: Commit**

```
git add src/gramps_mcp/tools/link_edit.py \
        src/gramps_mcp/models/parameters/link_edit_params.py \
        src/gramps_mcp/tools/__init__.py \
        src/gramps_mcp/server.py \
        tests/test_link_edit_tools.py
git commit -m "feat: add remove_event_from_person tool with gramps_id support"
```

---

## Task 4 — Create `citation_link.py` with add/remove citation tools

**Files:**
- Create: `src/gramps_mcp/tools/citation_link.py`
- Modify: `src/gramps_mcp/models/parameters/link_edit_params.py`
- Modify: `src/gramps_mcp/tools/__init__.py`
- Modify: `src/gramps_mcp/server.py`
- Create: `tests/test_citation_link_tools.py`

- [ ] **Step 4.1: Write the test file**

Create `tests/test_citation_link_tools.py`:

```python
"""
Tests for citation link MCP tools:
  - add_citation_to_event
  - remove_citation_from_event

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
CREATE TABLE event (
    handle VARCHAR(50) PRIMARY KEY NOT NULL,
    json_data TEXT, gramps_id TEXT,
    description TEXT, place VARCHAR(50),
    change INTEGER DEFAULT 0, private INTEGER DEFAULT 0
);
CREATE TABLE citation (
    handle VARCHAR(50) PRIMARY KEY NOT NULL,
    json_data TEXT, gramps_id TEXT,
    source_handle VARCHAR(50), page TEXT,
    change INTEGER DEFAULT 0, private INTEGER DEFAULT 0
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


def _insert_event(conn, handle: str, gramps_id: str, citation_list=None) -> None:
    data = {
        "_class": "Event", "handle": handle, "gramps_id": gramps_id,
        "type": {"_class": "EventType", "value": 42, "string": ""},
        "date": _EMPTY_DATE, "description": "", "place": None,
        "citation_list": citation_list or [],
        "note_list": [], "media_list": [], "attribute_list": [],
        "tag_list": [], "change": 0, "private": False,
    }
    conn.execute(
        "INSERT INTO event (handle, gramps_id, json_data, change, private) VALUES (?,?,?,0,0)",
        (handle, gramps_id, json.dumps(data)),
    )
    conn.commit()


def _insert_citation(conn, handle: str, gramps_id: str) -> None:
    data = {
        "_class": "Citation", "handle": handle, "gramps_id": gramps_id,
        "source_handle": None, "page": "", "confidence": 2,
        "date": _EMPTY_DATE, "note_list": [], "media_list": [],
        "attribute_list": [], "tag_list": [], "change": 0, "private": False,
    }
    conn.execute(
        "INSERT INTO citation (handle, gramps_id, json_data, change, private) VALUES (?,?,?,0,0)",
        (handle, gramps_id, json.dumps(data)),
    )
    conn.commit()


# ===========================================================================
# add_citation_to_event
# ===========================================================================

class TestAddCitationToEvent:
    def test_citation_appended_to_event(self, fresh_db):
        from gramps_mcp.tools.citation_link import add_citation_to_event_tool

        db, conn = fresh_db
        _insert_event(conn, "h_ev", "E0001")
        _insert_citation(conn, "h_cit", "C0001")

        result = asyncio.run(
            add_citation_to_event_tool(
                event_handle="h_ev", citation_handle="h_cit", db=db
            )
        )
        assert isinstance(result, list) and isinstance(result[0], TextContent)
        data = json.loads(result[0].text)
        assert data["result"] == "ok"
        assert data["citation_count"] == 1

        row = conn.execute("SELECT json_data FROM event WHERE handle = 'h_ev'").fetchone()
        event_data = json.loads(row["json_data"])
        assert "h_cit" in event_data["citation_list"]

    def test_idempotent_on_double_add(self, fresh_db):
        from gramps_mcp.tools.citation_link import add_citation_to_event_tool

        db, conn = fresh_db
        _insert_event(conn, "h_ev", "E0001")
        _insert_citation(conn, "h_cit", "C0001")

        asyncio.run(
            add_citation_to_event_tool(event_handle="h_ev", citation_handle="h_cit", db=db)
        )
        result = asyncio.run(
            add_citation_to_event_tool(event_handle="h_ev", citation_handle="h_cit", db=db)
        )
        data = json.loads(result[0].text)
        assert data["result"] == "no_change"

        row = conn.execute("SELECT json_data FROM event WHERE handle = 'h_ev'").fetchone()
        event_data = json.loads(row["json_data"])
        assert event_data["citation_list"].count("h_cit") == 1

    def test_add_via_gramps_ids(self, fresh_db):
        from gramps_mcp.tools.citation_link import add_citation_to_event_tool

        db, conn = fresh_db
        _insert_event(conn, "h_ev", "E0001")
        _insert_citation(conn, "h_cit", "C0001")

        result = asyncio.run(
            add_citation_to_event_tool(
                event_gramps_id="E0001", citation_gramps_id="C0001", db=db
            )
        )
        data = json.loads(result[0].text)
        assert data["result"] == "ok"

    def test_error_on_unknown_event(self, fresh_db):
        from gramps_mcp.tools.citation_link import add_citation_to_event_tool

        db, conn = fresh_db
        _insert_citation(conn, "h_cit", "C0001")

        with pytest.raises(GrampsAPIError, match="not found"):
            asyncio.run(
                add_citation_to_event_tool(
                    event_handle="nonexistent", citation_handle="h_cit", db=db
                )
            )

    def test_error_when_neither_handle_nor_gramps_id_for_event(self, fresh_db):
        from gramps_mcp.tools.citation_link import add_citation_to_event_tool

        db, conn = fresh_db
        _insert_citation(conn, "h_cit", "C0001")

        with pytest.raises(GrampsAPIError, match="handle or gramps_id required"):
            asyncio.run(
                add_citation_to_event_tool(citation_handle="h_cit", db=db)
            )


# ===========================================================================
# remove_citation_from_event
# ===========================================================================

class TestRemoveCitationFromEvent:
    def test_citation_removed_from_event(self, fresh_db):
        from gramps_mcp.tools.citation_link import remove_citation_from_event_tool

        db, conn = fresh_db
        _insert_citation(conn, "h_cit", "C0001")
        _insert_event(conn, "h_ev", "E0001", citation_list=["h_cit"])

        result = asyncio.run(
            remove_citation_from_event_tool(
                event_handle="h_ev", citation_handle="h_cit", db=db
            )
        )
        assert isinstance(result, list) and isinstance(result[0], TextContent)
        data = json.loads(result[0].text)
        assert data["result"] == "ok"
        assert data["citation_count"] == 0

        row = conn.execute("SELECT json_data FROM event WHERE handle = 'h_ev'").fetchone()
        event_data = json.loads(row["json_data"])
        assert "h_cit" not in event_data["citation_list"]

    def test_remove_via_gramps_ids(self, fresh_db):
        from gramps_mcp.tools.citation_link import remove_citation_from_event_tool

        db, conn = fresh_db
        _insert_citation(conn, "h_cit", "C0001")
        _insert_event(conn, "h_ev", "E0001", citation_list=["h_cit"])

        result = asyncio.run(
            remove_citation_from_event_tool(
                event_gramps_id="E0001", citation_gramps_id="C0001", db=db
            )
        )
        data = json.loads(result[0].text)
        assert data["result"] == "ok"

    def test_second_citation_survives_after_remove(self, fresh_db):
        from gramps_mcp.tools.citation_link import remove_citation_from_event_tool

        db, conn = fresh_db
        _insert_citation(conn, "h_cit1", "C0001")
        _insert_citation(conn, "h_cit2", "C0002")
        _insert_event(conn, "h_ev", "E0001", citation_list=["h_cit1", "h_cit2"])

        asyncio.run(
            remove_citation_from_event_tool(
                event_handle="h_ev", citation_handle="h_cit1", db=db
            )
        )

        row = conn.execute("SELECT json_data FROM event WHERE handle = 'h_ev'").fetchone()
        event_data = json.loads(row["json_data"])
        assert "h_cit1" not in event_data["citation_list"]
        assert "h_cit2" in event_data["citation_list"]

    def test_error_when_citation_not_in_event(self, fresh_db):
        from gramps_mcp.tools.citation_link import remove_citation_from_event_tool

        db, conn = fresh_db
        _insert_citation(conn, "h_cit", "C0001")
        _insert_event(conn, "h_ev", "E0001")

        with pytest.raises(GrampsAPIError, match="not in event"):
            asyncio.run(
                remove_citation_from_event_tool(
                    event_handle="h_ev", citation_handle="h_cit", db=db
                )
            )
```

- [ ] **Step 4.2: Run to verify tests fail**

```
uv run pytest tests/test_citation_link_tools.py -xvs
```

Expected: FAIL — `gramps_mcp.tools.citation_link` does not exist yet.

- [ ] **Step 4.3: Create `citation_link.py`**

Create `src/gramps_mcp/tools/citation_link.py`:

```python
"""
Citation link MCP tools — SQLite-only tools for adding/removing citations
on existing event objects without replacing the entire citation_list.
"""

import json
from typing import Any, List, Optional

from mcp.types import TextContent

from gramps_mcp.client import GrampsAPIError
from gramps_mcp.tools._sqlite_helpers import (
    _read_object,
    _require_sqlite_db,
    _resolve_handle,
    _write_object,
)


async def add_citation_to_event_tool(
    event_handle: Optional[str] = None,
    event_gramps_id: Optional[str] = None,
    citation_handle: Optional[str] = None,
    citation_gramps_id: Optional[str] = None,
    db: Any = None,
) -> List[TextContent]:
    """
    Append a citation to an existing event's citation_list without replacing it.

    Idempotent: if the citation is already linked, returns result='no_change'.
    SQLite backend only.

    Args:
        event_handle: Handle of the event.
        event_gramps_id: Gramps ID of the event (alternative to event_handle).
        citation_handle: Handle of the citation to add.
        citation_gramps_id: Gramps ID of the citation (alternative to citation_handle).
        db: GrampsSqliteDB instance (injected for tests; None uses get_client()).

    Returns:
        List[TextContent] with JSON result, event_handle, citation_handle,
        citation_count.

    Raises:
        GrampsAPIError: If backend is not SQLite or objects not found.
    """
    db = _require_sqlite_db(db, "add_citation_to_event")
    conn = db._conn

    event_handle = _resolve_handle(conn, "event", event_handle, event_gramps_id, "Event")
    citation_handle = _resolve_handle(
        conn, "citation", citation_handle, citation_gramps_id, "Citation"
    )

    event_data = _read_object(conn, "event", event_handle, "Event")
    citation_list = event_data.get("citation_list", [])

    if citation_handle in citation_list:
        return [TextContent(type="text", text=json.dumps(
            {
                "result": "no_change",
                "message": f"Citation '{citation_handle}' is already in this event.",
            },
            ensure_ascii=False,
        ))]

    citation_list.append(citation_handle)
    event_data["citation_list"] = citation_list

    with conn:
        _write_object(conn, "event", event_handle, event_data)

    return [TextContent(type="text", text=json.dumps(
        {
            "result": "ok",
            "event_handle": event_handle,
            "citation_handle": citation_handle,
            "citation_count": len(citation_list),
        },
        ensure_ascii=False,
    ))]


async def remove_citation_from_event_tool(
    event_handle: Optional[str] = None,
    event_gramps_id: Optional[str] = None,
    citation_handle: Optional[str] = None,
    citation_gramps_id: Optional[str] = None,
    db: Any = None,
) -> List[TextContent]:
    """
    Remove a citation from an existing event's citation_list.

    SQLite backend only.

    Args:
        event_handle: Handle of the event.
        event_gramps_id: Gramps ID of the event (alternative to event_handle).
        citation_handle: Handle of the citation to remove.
        citation_gramps_id: Gramps ID of the citation (alternative to citation_handle).
        db: GrampsSqliteDB instance (injected for tests; None uses get_client()).

    Returns:
        List[TextContent] with JSON result, event_handle, citation_handle,
        citation_count.

    Raises:
        GrampsAPIError: If backend is not SQLite, objects not found, or
                        citation not in event's citation_list.
    """
    db = _require_sqlite_db(db, "remove_citation_from_event")
    conn = db._conn

    event_handle = _resolve_handle(conn, "event", event_handle, event_gramps_id, "Event")
    citation_handle = _resolve_handle(
        conn, "citation", citation_handle, citation_gramps_id, "Citation"
    )

    event_data = _read_object(conn, "event", event_handle, "Event")
    citation_list = event_data.get("citation_list", [])

    new_list = [h for h in citation_list if h != citation_handle]
    if len(new_list) == len(citation_list):
        raise GrampsAPIError(
            f"Citation '{citation_handle}' is not in event '{event_handle}'"
        )

    event_data["citation_list"] = new_list

    with conn:
        _write_object(conn, "event", event_handle, event_data)

    return [TextContent(type="text", text=json.dumps(
        {
            "result": "ok",
            "event_handle": event_handle,
            "citation_handle": citation_handle,
            "citation_count": len(new_list),
        },
        ensure_ascii=False,
    ))]
```

- [ ] **Step 4.4: Run citation tests**

```
uv run pytest tests/test_citation_link_tools.py -xvs
```

Expected: all 9 tests pass.

- [ ] **Step 4.5: Add Params classes to `link_edit_params.py`**

Append to `src/gramps_mcp/models/parameters/link_edit_params.py`:

```python
class AddCitationToEventParams(BaseModel):
    """Parameters for add_citation_to_event tool."""

    event_handle: Optional[str] = Field(
        None, description="Handle of the event"
    )
    event_gramps_id: Optional[str] = Field(
        None, description="Gramps ID of the event (e.g. 'E0007'). Alternative to event_handle."
    )
    citation_handle: Optional[str] = Field(
        None, description="Handle of the citation to add"
    )
    citation_gramps_id: Optional[str] = Field(
        None,
        description="Gramps ID of the citation (e.g. 'C0012'). Alternative to citation_handle.",
    )


class RemoveCitationFromEventParams(BaseModel):
    """Parameters for remove_citation_from_event tool."""

    event_handle: Optional[str] = Field(
        None, description="Handle of the event"
    )
    event_gramps_id: Optional[str] = Field(
        None, description="Gramps ID of the event (e.g. 'E0007'). Alternative to event_handle."
    )
    citation_handle: Optional[str] = Field(
        None, description="Handle of the citation to remove"
    )
    citation_gramps_id: Optional[str] = Field(
        None,
        description="Gramps ID of the citation (e.g. 'C0012'). Alternative to citation_handle.",
    )
```

- [ ] **Step 4.6: Register in `server.py`**

**a) Add import for citation_link tools** — after the `link_edit` import block:

```python
from .tools.citation_link import (
    add_citation_to_event_tool,
    remove_citation_from_event_tool,
)
```

**b) Extend `link_edit_params` import:**

```python
from .models.parameters.link_edit_params import (
    AddCitationToEventParams,
    AddEventToPersonParams,
    MoveAttachmentParams,
    RemoveCitationFromEventParams,
    RemoveChildFromFamilyParams,
    RemoveEventFromPersonParams,
)
```

**c) Add handler functions** — after `_handle_remove_event_from_person`:

```python
async def _handle_add_citation_to_event(args: Dict) -> Any:
    """Handler for add_citation_to_event."""
    return await add_citation_to_event_tool(
        event_handle=args.get("event_handle"),
        event_gramps_id=args.get("event_gramps_id"),
        citation_handle=args.get("citation_handle"),
        citation_gramps_id=args.get("citation_gramps_id"),
    )


async def _handle_remove_citation_from_event(args: Dict) -> Any:
    """Handler for remove_citation_from_event."""
    return await remove_citation_from_event_tool(
        event_handle=args.get("event_handle"),
        event_gramps_id=args.get("event_gramps_id"),
        citation_handle=args.get("citation_handle"),
        citation_gramps_id=args.get("citation_gramps_id"),
    )
```

**d) Add to `TOOL_REGISTRY`** — after `remove_event_from_person` entry:

```python
"add_citation_to_event": {
    "description": (
        "Add a citation to an existing event's citation_list without replacing it. "
        "Idempotent: adding an already-linked citation returns result='no_change'. "
        "Use this instead of create_event when you only want to attach a citation. "
        "SQLite backend only."
    ),
    "schema": AddCitationToEventParams,
    "handler": _handle_add_citation_to_event,
},
"remove_citation_from_event": {
    "description": (
        "Remove a citation from an existing event's citation_list. "
        "Raises an error if the citation is not in the list. "
        "SQLite backend only."
    ),
    "schema": RemoveCitationFromEventParams,
    "handler": _handle_remove_citation_from_event,
},
```

- [ ] **Step 4.7: Export from `tools/__init__.py`**

Add to imports and `__all__`:

```python
from .citation_link import (
    add_citation_to_event_tool,
    remove_citation_from_event_tool,
)
```

In `__all__`:
```python
"add_citation_to_event_tool",
"remove_citation_from_event_tool",
```

- [ ] **Step 4.8: Run full test suite**

```
uv run pytest tests/ -x --ignore=tests/conftest_sqlite.py -q
```

Expected: all tests pass.

- [ ] **Step 4.9: Commit**

```
git add src/gramps_mcp/tools/citation_link.py \
        src/gramps_mcp/models/parameters/link_edit_params.py \
        src/gramps_mcp/tools/__init__.py \
        src/gramps_mcp/server.py \
        tests/test_citation_link_tools.py
git commit -m "feat: add add_citation_to_event and remove_citation_from_event tools"
```

---

## Task 5 — MCP discoverability: `instructions` + tool group resources + CLAUDE.md

**Files:**
- Modify: `src/gramps_mcp/server.py`
- Modify: `.claude/CLAUDE.md`

- [ ] **Step 5.1: Add `TOOL_GROUPS` dict to `server.py`**

Add after the `TOOL_REGISTRY` dict (before `# Create FastMCP app`):

```python
# Tool groups for gramps://tools/<group> resources
TOOL_GROUPS: dict[str, list[str]] = {
    "person": [
        "create_person", "get_person", "find_person",
        "merge_persons", "split_person", "find_duplicate_persons",
        "add_dna_match", "get_dna_matches", "update_dna_match",
    ],
    "event": [
        "create_event", "get_event", "find_event",
        "add_event_to_person", "remove_event_from_person",
    ],
    "citation": [
        "create_citation", "create_source", "create_repository",
        "find_citation", "find_source", "find_repository",
        "add_citation_to_event", "remove_citation_from_event",
    ],
    "family": [
        "create_family", "get_family", "find_family",
        "merge_families", "remove_child_from_family",
    ],
    "search": [
        "find_anything", "find_type", "get_type",
        "get_ancestors", "get_descendants", "tree_stats",
        "recent_changes", "find_duplicate_events", "find_duplicate_citations",
    ],
    "admin": [
        "list_databases", "open_database", "close_database",
    ],
}
```

- [ ] **Step 5.2: Replace the `FastMCP` app creation line with `instructions`**

Replace:

```python
app = FastMCP("gramps", stateless_http=True, json_response=True)
```

With:

```python
app = FastMCP(
    "gramps-genealogy",
    stateless_http=True,
    json_response=True,
    instructions=(
        "Gramps genealogy database — SQLite backend.\n\n"
        "Load a tool-group resource before working in a domain:\n\n"
        "  gramps://tools/person    — create/get/merge/split persons, DNA\n"
        "  gramps://tools/event     — create/get events, add/remove event↔person links\n"
        "  gramps://tools/citation  — create citations/sources, add/remove citation↔event links\n"
        "  gramps://tools/family    — create/get/merge families, child links\n"
        "  gramps://tools/search    — find_anything, ancestors, descendants, tree stats\n"
        "  gramps://tools/admin     — open/close/list databases\n\n"
        "Before writing raw SQLite: always check if an MCP tool covers the operation."
    ),
)
```

- [ ] **Step 5.3: Add the helper and six resource endpoints to `server.py`**

Add after the existing `get_usage_guide` resource (before the `# Add custom routes` comment):

```python
def _generate_tool_group_resource(group_name: str) -> str:
    """Generate Markdown documentation for a named tool group."""
    tool_names = TOOL_GROUPS.get(group_name, [])
    lines = [f"# Gramps Tools — {group_name.title()}\n"]
    for name in tool_names:
        config = TOOL_REGISTRY.get(name)
        if not config:
            continue
        lines.append(f"### {name}")
        lines.append(config["description"])
        schema = config["schema"]
        lines.append("\n**Parameters:**")
        for field_name, field_info in schema.model_fields.items():
            req = "(required)" if field_info.is_required() else "(optional)"
            desc = field_info.description or ""
            lines.append(f"  - `{field_name}` {req}: {desc}")
        lines.append("")
    return "\n".join(lines)


@app.resource("gramps://tools/person")
def get_person_tools() -> str:
    """Person-related tools: create, get, merge, split, DNA."""
    return _generate_tool_group_resource("person")


@app.resource("gramps://tools/event")
def get_event_tools() -> str:
    """Event tools: create, get, add/remove event↔person links."""
    return _generate_tool_group_resource("event")


@app.resource("gramps://tools/citation")
def get_citation_tools() -> str:
    """Citation and source tools: create, add/remove citation↔event links."""
    return _generate_tool_group_resource("citation")


@app.resource("gramps://tools/family")
def get_family_tools() -> str:
    """Family tools: create, get, merge, child management."""
    return _generate_tool_group_resource("family")


@app.resource("gramps://tools/search")
def get_search_tools() -> str:
    """Search and analysis tools: find_anything, ancestors, descendants, stats."""
    return _generate_tool_group_resource("search")


@app.resource("gramps://tools/admin")
def get_admin_tools() -> str:
    """Database management tools: open, close, list databases."""
    return _generate_tool_group_resource("admin")
```

- [ ] **Step 5.4: Add the MCP tool discovery rule to `.claude/CLAUDE.md`**

Append to `.claude/CLAUDE.md`:

```markdown
### MCP Tool Discovery

Before attempting raw SQLite for any genealogy operation, read the relevant
tool-group resource to check if an MCP tool already covers it:

- Persons: read resource `gramps://tools/person`
- Events: read resource `gramps://tools/event`
- Citations / Sources: read resource `gramps://tools/citation`
- Families: read resource `gramps://tools/family`
- Search / Analysis: read resource `gramps://tools/search`
- Database management: read resource `gramps://tools/admin`
```

- [ ] **Step 5.5: Verify server starts without errors**

```
uv run python -c "from gramps_mcp.server import app; print('OK')"
```

Expected output: `OK`

- [ ] **Step 5.6: Run full test suite one last time**

```
uv run pytest tests/ -x --ignore=tests/conftest_sqlite.py -q
```

Expected: all tests pass.

- [ ] **Step 5.7: Commit**

```
git add src/gramps_mcp/server.py .claude/CLAUDE.md
git commit -m "feat: add MCP instructions and tool-group resources for discoverability"
```
