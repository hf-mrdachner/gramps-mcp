"""
Link-edit MCP tools — SQLite-only tools for manipulating links between existing
Gramps objects without replacing entire lists.

All tools require the SQLite backend (GrampsSqliteClient). They manipulate raw
Gramps JSON directly to avoid the normalization round-trip and use injectable
db= parameters for testability (same pattern as delete_object_tool).
"""

import json
from typing import Any, List, Optional

from mcp.types import TextContent

from gramps_mcp.client import GrampsAPIError
from gramps_mcp.tools._sqlite_helpers import (
    _make_child_ref,
    _make_event_ref,
    _make_name,
    _read_object,
    _require_sqlite_db,
    _resolve_handle,
    _write_object,
    _write_person,
)


# ---------------------------------------------------------------------------
# Tool 1: add_event_to_person
# ---------------------------------------------------------------------------


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

    # _resolve_handle only confirms existence when gramps_id is given; when a
    # raw handle is passed directly it returns it without a DB lookup, so we
    # must verify the event exists here in both code paths.
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

    event_ref_list.append(_make_event_ref(event_handle, role))
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


# ---------------------------------------------------------------------------
# Tool 2: add_event_to_family
# ---------------------------------------------------------------------------


async def add_event_to_family_tool(
    family_handle: Optional[str] = None,
    event_handle: Optional[str] = None,
    family_gramps_id: Optional[str] = None,
    event_gramps_id: Optional[str] = None,
    role: str = "Family",
    db: Any = None,
) -> List[TextContent]:
    """
    Append an event reference to a family's event_ref_list without replacing it.

    SQLite backend only.

    Args:
        family_handle: Handle of the family.
        event_handle: Handle of the event to link (must already exist).
        family_gramps_id: Gramps ID of the family (alternative to family_handle).
        event_gramps_id: Gramps ID of the event (alternative to event_handle).
        role: Role of the family in the event (default: 'Family').
        db: GrampsSqliteDB instance (injected for tests; None uses get_client()).

    Returns:
        List[TextContent] with JSON result.

    Raises:
        GrampsAPIError: If backend is not SQLite or objects not found.
    """
    db = _require_sqlite_db(db, "add_event_to_family")
    conn = db._conn

    family_handle = _resolve_handle(conn, "family", family_handle, family_gramps_id, "Family")
    event_handle = _resolve_handle(conn, "event", event_handle, event_gramps_id, "Event")

    family_data = _read_object(conn, "family", family_handle, "Family")

    # Same rationale as add_event_to_person: _resolve_handle skips the DB lookup
    # when a raw handle is given, so we verify event existence explicitly here.
    if not conn.execute(
        "SELECT handle FROM event WHERE handle = ?",  # noqa: S608
        (event_handle,),
    ).fetchone():
        raise GrampsAPIError(f"Event with handle '{event_handle}' not found")

    event_ref_list = family_data.get("event_ref_list", [])

    existing_handles = {
        e.get("ref") for e in event_ref_list if isinstance(e, dict) and e.get("ref")
    }
    if event_handle in existing_handles:
        return [TextContent(type="text", text=json.dumps(
            {
                "result": "no_change",
                "message": f"Event '{event_handle}' is already linked to this family.",
            },
            ensure_ascii=False,
        ))]

    event_ref_list.append(_make_event_ref(event_handle, role))
    family_data["event_ref_list"] = event_ref_list

    with conn:
        _write_object(conn, "family", family_handle, family_data)

    valid_ref_count = sum(1 for e in event_ref_list if isinstance(e, dict) and e.get("ref"))
    return [TextContent(type="text", text=json.dumps(
        {
            "result": "ok",
            "family_handle": family_handle,
            "event_handle": event_handle,
            "role": role,
            "event_ref_count": valid_ref_count,
        },
        ensure_ascii=False,
    ))]


# ---------------------------------------------------------------------------
# Tool 3: remove_event_from_family
# ---------------------------------------------------------------------------


async def remove_event_from_family_tool(
    family_handle: Optional[str] = None,
    event_handle: Optional[str] = None,
    family_gramps_id: Optional[str] = None,
    event_gramps_id: Optional[str] = None,
    db: Any = None,
) -> List[TextContent]:
    """
    Remove an event reference from a family's event_ref_list.

    Does not delete the event object itself. SQLite backend only.

    Args:
        family_handle: Handle of the family.
        event_handle: Handle of the event to unlink.
        family_gramps_id: Gramps ID of the family (alternative to family_handle).
        event_gramps_id: Gramps ID of the event (alternative to event_handle).
        db: GrampsSqliteDB instance (injected for tests; None uses get_client()).

    Returns:
        List[TextContent] with JSON result, family_handle, event_handle,
        event_ref_count.

    Raises:
        GrampsAPIError: If backend is not SQLite, handles not found, or event
                        not linked to this family.
    """
    db = _require_sqlite_db(db, "remove_event_from_family")
    conn = db._conn

    family_handle = _resolve_handle(conn, "family", family_handle, family_gramps_id, "Family")
    event_handle = _resolve_handle(conn, "event", event_handle, event_gramps_id, "Event")

    family_data = _read_object(conn, "family", family_handle, "Family")
    event_ref_list = family_data.get("event_ref_list", [])

    new_refs = [
        e for e in event_ref_list
        if not (isinstance(e, dict) and e.get("ref") == event_handle)
    ]
    if len(new_refs) == len(event_ref_list):
        raise GrampsAPIError(
            f"Event '{event_handle}' is not linked to family '{family_handle}'"
        )

    family_data["event_ref_list"] = new_refs

    with conn:
        _write_object(conn, "family", family_handle, family_data)

    return [TextContent(type="text", text=json.dumps(
        {
            "result": "ok",
            "family_handle": family_handle,
            "event_handle": event_handle,
            "event_ref_count": len(new_refs),
        },
        ensure_ascii=False,
    ))]


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


# ---------------------------------------------------------------------------
# Tool 3: remove_child_from_family
# ---------------------------------------------------------------------------


async def remove_child_from_family_tool(
    family_handle: str,
    child_handle: str,
    db: Any = None,
) -> List[TextContent]:
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
        List[TextContent] with JSON result, family_handle, child_handle,
        remaining_children.

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

    return [TextContent(type="text", text=json.dumps(
        {
            "result": "ok",
            "family_handle": family_handle,
            "child_handle": child_handle,
            "remaining_children": len(new_child_refs),
        },
        ensure_ascii=False,
    ))]


# ---------------------------------------------------------------------------
# Tool 3b: add_child_to_family
# ---------------------------------------------------------------------------


async def add_child_to_family_tool(
    family_handle: str,
    child_handle: str,
    frel: str = "Birth",
    mrel: str = "Birth",
    db: Any = None,
) -> List[TextContent]:
    """
    Append a child to a family's child_ref_list without replacing it.

    Adds the child to the family's child_ref_list, then adds family_handle to
    the child person's parent_family_list. Both writes happen in a single
    SQLite transaction — mirrors remove_child_from_family_tool. Unlike
    create_family(child_gramps_ids=[...]), which replaces child_ref_list
    wholesale, this only appends, so existing children are never at risk of
    being silently dropped. SQLite backend only.

    If the child is already in the family, this is a no-op — it does not
    update frel/mrel on an already-linked child (matches add_event_to_person/
    add_event_to_family's equivalent no-op behavior for role on a repeat call).

    Args:
        family_handle: Handle of the family.
        child_handle: Handle of the child person to add.
        frel: Relationship to father (default: 'Birth').
        mrel: Relationship to mother (default: 'Birth').
        db: GrampsSqliteDB instance (injected for tests; None uses get_client()).

    Returns:
        List[TextContent] with JSON result, family_handle, child_handle,
        child_count.

    Raises:
        GrampsAPIError: If backend is not SQLite or handles not found.
    """
    db = _require_sqlite_db(db, "add_child_to_family")
    conn = db._conn

    family_data = _read_object(conn, "family", family_handle, "Family")
    child_ref_list = family_data.get("child_ref_list", [])

    existing_handles = [
        cr.get("ref") for cr in child_ref_list if isinstance(cr, dict)
    ]
    if child_handle in existing_handles:
        return [TextContent(type="text", text=json.dumps(
            {
                "result": "no_change",
                "message": f"Child '{child_handle}' is already in this family.",
            },
            ensure_ascii=False,
        ))]

    # Confirms the child exists before writing either side.
    child_data = _read_object(conn, "person", child_handle, "Child person")

    child_ref_list.append(_make_child_ref(child_handle, frel, mrel))
    family_data["child_ref_list"] = child_ref_list

    pfl = child_data.get("parent_family_list", [])
    if family_handle not in pfl:
        pfl.append(family_handle)
        child_data["parent_family_list"] = pfl

    with conn:
        _write_object(conn, "family", family_handle, family_data)
        _write_person(conn, child_handle, child_data)

    return [TextContent(type="text", text=json.dumps(
        {
            "result": "ok",
            "family_handle": family_handle,
            "child_handle": child_handle,
            "child_count": len(child_ref_list),
        },
        ensure_ascii=False,
    ))]


# ---------------------------------------------------------------------------
# Tool 4: move_attachment
# ---------------------------------------------------------------------------


async def move_attachment_tool(
    attachment_type: str,
    handle: str,
    from_handle: str,
    from_type: str = "person",
    to_handle: str = "",
    to_type: str = "person",
    db: Any = None,
) -> List[TextContent]:
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
        List[TextContent] with JSON result and move details.

    Raises:
        GrampsAPIError: If backend is not SQLite, handles not found, or
                        attachment not in source object.
    """
    db = _require_sqlite_db(db, "move_attachment")
    conn = db._conn

    if not to_handle:
        raise GrampsAPIError("to_handle is required")

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

    if attachment_type == "media":
        # media_list contains MediaRef dicts: {"_class": "MediaRef", "ref": handle, ...}
        present = any(
            (e.get("ref") == handle if isinstance(e, dict) else e == handle)
            for e in from_list
        )
        if not present:
            raise GrampsAPIError(
                f"Media '{handle}' is not in {from_type} '{from_handle}'"
            )
        from_data[list_key] = [
            e for e in from_list
            if not ((isinstance(e, dict) and e.get("ref") == handle) or e == handle)
        ]
    else:
        # note_list contains plain handle strings
        if handle not in from_list:
            raise GrampsAPIError(
                f"Note '{handle}' is not in {from_type} '{from_handle}'"
            )
        from_data[list_key] = [h for h in from_list if h != handle]

    to_data = _read_object(conn, to_type, to_handle, f"Target {to_type}")
    to_list = to_data.get(list_key, [])

    if attachment_type == "media":
        existing_refs = [e.get("ref") if isinstance(e, dict) else e for e in to_list]
        if handle not in existing_refs:
            to_list.append({
                "_class": "MediaRef", "ref": handle, "private": False,
                "citation_list": [], "note_list": [], "attribute_list": [], "rect": None,
            })
    else:
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

    return [TextContent(type="text", text=json.dumps(
        {
            "result": "ok",
            "attachment_type": attachment_type,
            "handle": handle,
            "from": {"type": from_type, "handle": from_handle},
            "to": {"type": to_type, "handle": to_handle},
        },
        ensure_ascii=False,
    ))]


# ---------------------------------------------------------------------------
# Tool 5: add_alternate_name_to_person
# ---------------------------------------------------------------------------


def _name_identity(name: dict) -> tuple:
    """Build a comparison key (first_name, surnames, type) to detect duplicates."""
    surnames = tuple(
        sn.get("surname", "") for sn in name.get("surname_list", [])
    )
    name_type = name.get("type", {})
    # _denorm_type maps every non-standard type string to the same "Custom"
    # sentinel (value=0), with the actual label only in `string` — comparing
    # value alone would treat two distinct custom types as duplicates.
    return (
        name.get("first_name", ""),
        surnames,
        name_type.get("value"),
        name_type.get("string"),
    )


async def add_alternate_name_to_person_tool(
    person_handle: Optional[str] = None,
    person_gramps_id: Optional[str] = None,
    name: Optional[dict] = None,
    db: Any = None,
) -> List[TextContent]:
    """
    Append a Name object to a person's alternate_names without replacing it.

    Unlike smuggling a name into create_person's untyped primary_name field,
    this appends a properly structured Gramps Name to alternate_names only,
    leaving primary_name and every other field untouched. SQLite backend only.

    If a name with the same first_name, surname(s), and type is already
    present, this is a no-op (matches add_event_to_person/add_child_to_family's
    equivalent no-op behavior for repeat calls).

    Args:
        person_handle: Handle of the person.
        person_gramps_id: Gramps ID of the person (alternative to person_handle).
        name: Name object dict (e.g. first_name, surname_list, type, suffix).
        db: GrampsSqliteDB instance (injected for tests; None uses get_client()).

    Returns:
        List[TextContent] with JSON result, person_handle, alternate_name_count.

    Raises:
        GrampsAPIError: If backend is not SQLite, person not found, or name missing.
    """
    db = _require_sqlite_db(db, "add_alternate_name_to_person")
    conn = db._conn

    if not name or not isinstance(name, dict):
        raise GrampsAPIError("name is required and must be a Name object dict")

    person_handle = _resolve_handle(
        conn, "person", person_handle, person_gramps_id, "Person"
    )
    person_data = _read_object(conn, "person", person_handle, "Person")

    new_name = _make_name(name)
    alternate_names = person_data.get("alternate_names", [])

    existing_identities = {
        _name_identity(n) for n in alternate_names if isinstance(n, dict)
    }
    if _name_identity(new_name) in existing_identities:
        return [TextContent(type="text", text=json.dumps(
            {
                "result": "no_change",
                "message": (
                    "An identical alternate name is already present on this person."
                ),
            },
            ensure_ascii=False,
        ))]

    alternate_names.append(new_name)
    person_data["alternate_names"] = alternate_names

    with conn:
        _write_person(conn, person_handle, person_data)

    return [TextContent(type="text", text=json.dumps(
        {
            "result": "ok",
            "person_handle": person_handle,
            "alternate_name_count": len(alternate_names),
        },
        ensure_ascii=False,
    ))]
