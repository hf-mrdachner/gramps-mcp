# gramps-mcp - AI-Powered Genealogy Research & Management
# Copyright (C) 2025 cabout.me
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""
Database lifecycle MCP tools.

Provides three tools for managing the active Gramps database connection:

- ``open_database``   — open (or switch to) a database file
- ``close_database``  — release the connection so Gramps Desktop can use it
- ``reload_database`` — re-read the database after Gramps Desktop made changes
"""

import logging
from typing import Dict, List

from mcp.types import TextContent

from ..client import GrampsAPIError, close_database, open_database, reload_database

logger = logging.getLogger(__name__)


def _db_summary(client) -> str:
    """Return a short stats summary for the active database."""
    db = client._db
    return (
        f"  People:       {db.count('person')}\n"
        f"  Families:     {db.count('family')}\n"
        f"  Events:       {db.count('event')}\n"
        f"  Places:       {db.count('place')}\n"
        f"  Sources:      {db.count('source')}\n"
        f"  Citations:    {db.count('citation')}\n"
        f"  Notes:        {db.count('note')}\n"
        f"  Media:        {db.count('media')}\n"
        f"  Repositories: {db.count('repository')}"
    )


async def open_database_tool(arguments: Dict) -> List[TextContent]:
    """
    Open a Gramps database file, replacing any currently active connection.

    Accepts ``.sqlite`` / ``.db`` files (or directories containing
    ``sqlite.db``) for full read/write access, or ``.gpkg`` / ``.gramps``
    files for read-only access.

    Args:
        arguments: Dict with key ``path`` — absolute path to the database.

    Returns:
        Confirmation message with record counts.
    """
    path = (arguments.get("path") or "").strip()
    if not path:
        return [TextContent(type="text", text="Error: 'path' is required.")]
    try:
        client = open_database(path)
        is_sqlite = hasattr(client._db, "_conn")
        mode = "read/write (SQLite)" if is_sqlite else "read-only (XML)"
        msg = (
            f"Database opened in {mode} mode.\n"
            f"Path: {path}\n\n"
            f"Record counts:\n{_db_summary(client)}"
        )
        return [TextContent(type="text", text=msg)]
    except GrampsAPIError as exc:
        return [TextContent(type="text", text=f"Error: {exc}")]
    except Exception as exc:
        logger.exception("open_database_tool failed")
        return [TextContent(type="text", text=f"Unexpected error: {exc}")]


async def close_database_tool(arguments: Dict) -> List[TextContent]:
    """
    Close the current database connection and release any file locks.

    Call this before opening the database in Gramps Desktop to avoid
    conflicting SQLite locks.  The path is remembered so
    ``reload_database`` can reopen it afterwards.

    Args:
        arguments: Not used.

    Returns:
        Confirmation message.
    """
    path = close_database()
    if path:
        msg = (
            f"Database closed: {path}\n\n"
            "You can now safely open this database in Gramps Desktop.\n"
            "Call reload_database when you are done."
        )
    else:
        msg = "No database was open."
    return [TextContent(type="text", text=msg)]


async def reload_database_tool(arguments: Dict) -> List[TextContent]:
    """
    Close and immediately reopen the current database from disk.

    Use this after Gramps Desktop (or another tool) has written changes
    so the agent sees the updated data.

    Args:
        arguments: Not used.

    Returns:
        Confirmation message with refreshed record counts.
    """
    try:
        client = reload_database()
        is_sqlite = hasattr(client._db, "_conn")
        mode = "read/write (SQLite)" if is_sqlite else "read-only (XML)"
        msg = (
            f"Database reloaded in {mode} mode.\n\n"
            f"Record counts:\n{_db_summary(client)}"
        )
        return [TextContent(type="text", text=msg)]
    except GrampsAPIError as exc:
        return [TextContent(type="text", text=f"Error: {exc}")]
    except Exception as exc:
        logger.exception("reload_database_tool failed")
        return [TextContent(type="text", text=f"Unexpected error: {exc}")]
