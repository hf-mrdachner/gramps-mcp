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
