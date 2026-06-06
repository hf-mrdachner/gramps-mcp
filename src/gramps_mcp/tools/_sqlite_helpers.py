"""
Shared SQLite helpers for link-edit tools.

All helpers require an open sqlite3.Connection and operate directly on JSON data.
"""

import json
import time
from typing import Any, Dict, Optional

from gramps_mcp._gramps_sqlite import GrampsSqliteDB, _compute_birth_death_indices, _denorm_type
from gramps_mcp.client import GrampsAPIError

_VALID_TABLES: frozenset[str] = frozenset({
    "person", "family", "event", "place", "source", "citation",
    "note", "media", "repository", "tag",
})


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
    if table not in _VALID_TABLES:
        raise GrampsAPIError(f"Invalid table name: '{table}'")
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
    cursor = conn.execute(
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
    if cursor.rowcount == 0:
        raise GrampsAPIError(f"Person with handle '{handle}' not found")


def _write_object(conn: Any, table: str, handle: str, data: Dict) -> None:
    """
    Write any non-person object JSON back to DB, updating change timestamp.

    Args:
        conn: Open sqlite3.Connection within an active transaction.
        table: Table name (e.g. 'event', 'family').
        handle: Object handle.
        data: Gramps JSON dict (mutated in place: sets change).
    """
    if table not in _VALID_TABLES:
        raise GrampsAPIError(f"Invalid table name: '{table}'")
    data["change"] = int(time.time())
    cursor = conn.execute(
        f"UPDATE {table} SET json_data=?, change=? WHERE handle=?",  # noqa: S608
        (json.dumps(data, ensure_ascii=False), data["change"], handle),
    )
    if cursor.rowcount == 0:
        raise GrampsAPIError(f"{table.title()} with handle '{handle}' not found")


def _make_event_ref(event_handle: str, role: str) -> dict:
    """
    Build a Gramps EventRef dict for insertion into an event_ref_list.

    Args:
        event_handle: Handle of the event to reference.
        role: Human-readable role string (e.g. 'Primary', 'Family').

    Returns:
        Dict conforming to the Gramps EventRef JSON schema.
    """
    return {
        "_class": "EventRef",
        "ref": event_handle,
        "role": _denorm_type(role, "EventRoleType"),
        "private": False,
        "note_list": [],
        "attribute_list": [],
    }


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
    if table not in _VALID_TABLES:
        raise GrampsAPIError(f"Invalid table name: '{table}'")
    if handle is not None:
        if handle == "":
            raise GrampsAPIError(f"{label}: handle must not be empty")
        return handle
    if gramps_id is not None:
        row = conn.execute(
            f"SELECT handle FROM {table} WHERE gramps_id = ?",  # noqa: S608
            (gramps_id,),
        ).fetchone()
        if not row:
            raise GrampsAPIError(f"{label}: gramps_id '{gramps_id}' not found")
        return row[0]
    raise GrampsAPIError(f"{label}: handle or gramps_id required")
