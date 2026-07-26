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
    # Read (and thus validate existence of) the person BEFORE touching the note.
    # _resolve_handle only confirms existence when gramps_id is given — a raw
    # handle is returned unchecked — so without this, _upsert_note could create
    # or mutate a Note and commit it before we discover the person doesn't exist.
    person_data = _read_object(conn, "person", person_handle, "Person")

    note_handle_final, note_gramps_id_final, note_result = _upsert_note(
        conn, db, note_handle, note_gramps_id, text, type
    )

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
