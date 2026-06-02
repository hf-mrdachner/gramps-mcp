"""
delete_object MCP tool — cascade-safe deletion for Gramps SQLite databases.

Supports dry-run mode: confirmed=False returns a human-readable summary of
what would be deleted/unlinked without touching the database.
SQLite backend only; raises GrampsAPIError for Web backend.
"""

import copy
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Tuple

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

    # Tags have no gramps_id column — query only json_data and name
    if obj_type == "tag":
        row = conn.execute(
            f"SELECT json_data, name FROM {table} WHERE handle = ?",  # noqa: S608
            (handle,),
        ).fetchone()
        if not row:
            return handle
        try:
            data = json.loads(row[0])
        except Exception:
            return handle
        name = data.get("name") or (row[1] if row[1] else handle)
        return name

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

    return gid


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
    "father_handle",
    "mother_handle",
    "place",
    "source_handle",
)

# List fields containing plain handle strings
_PLAIN_LIST_FIELDS = (
    "citation_list",
    "note_list",
    "tag_list",
    "family_list",
    "parent_family_list",
)

# List fields containing dicts with a "ref" key
_REF_LIST_FIELDS = (
    "event_ref_list",
    "child_ref_list",
    "media_list",
    "placeref_list",
    "reporef_list",
    "person_ref_list",
)


def _remove_handle_from_json(data: Dict, handle: str) -> Tuple[Dict, List[str]]:
    """
    Remove all occurrences of handle from a Gramps JSON dict.

    Does not mutate the input — returns a shallow copy with changes applied.

    Args:
        data: Raw Gramps JSON dict.
        handle: Handle to remove.

    Returns:
        Tuple of (updated_dict, list_of_affected_field_names).
    """
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
                item
                for item in lst
                if not (isinstance(item, dict) and item.get("ref") == handle)
            ]
            if len(new) < len(lst):
                data[f] = new
                affected.append(f)

    return data, affected


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
        DeleteEntry(
            obj_type=obj_type, handle=handle, label=_label(conn, obj_type, handle)
        )
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
        targets = ", ".join(f"{e.label} ({e.field})" for e in plan.to_unlink)
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
                    f"DELETE FROM {table} WHERE handle = ?",
                    (entry.handle,),  # noqa: S608
                )
                conn.execute(
                    "DELETE FROM reference WHERE obj_handle = ? OR ref_handle = ?",
                    (entry.handle, entry.handle),
                )
    except _sqlite3.Error as exc:
        raise GrampsAPIError(
            f"SQLite error during delete of {plan.to_delete[0].handle}: {exc}"
        ) from exc


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
            f"Unknown object type: '{obj_type}'. " f"Valid types: {', '.join(_TABLE)}"
        )

    conn = db._conn
    row = conn.execute(
        f"SELECT handle FROM {table} WHERE handle = ?",  # noqa: S608
        (handle,),
    ).fetchone()
    if not row:
        raise GrampsAPIError(f"{obj_type} with handle '{handle}' not found")

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
