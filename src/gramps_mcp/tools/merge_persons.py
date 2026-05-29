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
MCP tools for duplicate-person detection and merge operations.

These tools require the SQLite backend (GRAMPS_DB_PATH pointing to a
live Gramps sqlite.db).  They are not available with the Web API or
XML/gpkg read-only backends.
"""

import logging
import os
from typing import Dict, List

from mcp.types import TextContent

from ..client import GrampsAPIError, get_client
from ..merge.detect import find_duplicate_persons
from ..merge.operations import merge_persons as _merge_persons
from ..merge.operations import split_person as _split_person
from ..models.parameters.merge_params import (
    FindDuplicatesParams,
    MergePersonsParams,
    SplitPersonParams,
)

logger = logging.getLogger(__name__)


def _get_sqlite_conn(client):
    """
    Extract the raw SQLite connection from a GrampsSqliteClient.

    Raises GrampsAPIError if the active backend is not SQLite.

    Args:
        client: Active Gramps client from get_client().

    Returns:
        sqlite3.Connection ready for merge operations.
    """
    from ..sqlite_client import GrampsSqliteClient

    if not isinstance(client, GrampsSqliteClient):
        raise GrampsAPIError(
            "Merge tools require the SQLite backend. "
            "Set GRAMPS_DB_PATH to a Gramps sqlite.db file or directory."
        )
    return client._db._conn


def _backup_file(client) -> str:
    """
    Derive the sidecar backup file path from the client's DB path.

    Args:
        client: Active GrampsSqliteClient.

    Returns:
        Absolute path to merge_backups.json next to the DB file.
    """
    return os.path.join(os.path.dirname(client._db_path), "merge_backups.json")


async def find_duplicate_persons_tool(arguments: Dict) -> List[TextContent]:
    """
    Scan the genealogy database for duplicate person records.

    Compares all persons by name (with German umlaut normalisation),
    birth year, suffix (Roman numerals), gender, and shared relatives.
    Returns ranked candidate pairs — review before merging.

    Only available with the SQLite backend.
    """
    params = FindDuplicatesParams(**arguments)
    client = await get_client()
    try:
        conn = _get_sqlite_conn(client)
        candidates = find_duplicate_persons(
            conn, limit=params.limit, min_score=params.min_score
        )
        if not candidates:
            return [TextContent(
                type="text",
                text=f"No duplicate candidates found (min_score={params.min_score}).",
            )]

        high = [c for c in candidates if c.score >= 8]
        med  = [c for c in candidates if 5 <= c.score < 8]
        low  = [c for c in candidates if c.score < 5]

        lines = [
            f"Found {len(candidates)} duplicate candidate(s):\n",
            f"  High confidence (score >= 8): {len(high)}",
            f"  Medium (5-7):                 {len(med)}",
            f"  Low (< 5):                    {len(low)}",
            "",
        ]
        for c in candidates:
            marker = "***" if c.score >= 8 else " * " if c.score >= 5 else "   "
            lines.append(
                f"{marker} [{c.score:4.1f}]  {c.winner_name!r} ({c.winner_id})"
                f"  <--  {c.loser_name!r} ({c.loser_id})"
            )
            lines.append(f"         Reasons: {', '.join(c.reasons)}")

        lines.append(
            "\nTo merge a pair: merge_persons(winner_id=..., loser_id=..., dry_run=True)"
        )
        return [TextContent(type="text", text="\n".join(lines))]

    except GrampsAPIError as e:
        return [TextContent(type="text", text=f"Error: {e}")]
    finally:
        await client.close()


async def merge_persons_tool(arguments: Dict) -> List[TextContent]:
    """
    Merge a duplicate person (loser) into another (winner).

    Combines event references, citations, notes, family links and other
    data from the loser into the winner.  Duplicate events (same type,
    date and place) are merged rather than duplicated — their citations
    are combined.  A backup is saved so the merge can be undone with
    split_person.

    Set dry_run=True (default) to preview changes without writing.

    Only available with the SQLite backend.
    """
    params = MergePersonsParams(**arguments)
    client = await get_client()
    try:
        conn = _get_sqlite_conn(client)

        wrow = conn.execute(
            "SELECT handle FROM person WHERE gramps_id=?", (params.winner_id,)
        ).fetchone()
        lrow = conn.execute(
            "SELECT handle FROM person WHERE gramps_id=?", (params.loser_id,)
        ).fetchone()

        if wrow is None:
            return [TextContent(type="text", text=f"Person not found: {params.winner_id}")]
        if lrow is None:
            return [TextContent(type="text", text=f"Person not found: {params.loser_id}")]

        backup = _backup_file(client) if not params.dry_run else None
        changes = _merge_persons(
            conn,
            winner_handle=wrow["handle"],
            loser_handle=lrow["handle"],
            dry_run=params.dry_run,
            backup_file=backup,
        )

        action = "Would merge (dry run)" if params.dry_run else "Merged"
        lines = [
            f"{action}: {params.winner_id} <-- {params.loser_id}",
            f"Changes: {', '.join(changes) if changes else 'none'}",
        ]
        if params.dry_run:
            lines.append(
                "\nRe-run with dry_run=False to apply."
            )
        else:
            lines.append(
                f"\nBackup saved to: {backup}\n"
                "To undo: split_person(gramps_id='" + params.winner_id + "')"
            )
        return [TextContent(type="text", text="\n".join(lines))]

    except GrampsAPIError as e:
        return [TextContent(type="text", text=f"Error: {e}")]
    finally:
        await client.close()


async def split_person_tool(arguments: Dict) -> List[TextContent]:
    """
    Undo a previous merge by recreating the absorbed person records.

    Reads the merge backup file written by merge_persons.  The winner
    person is NOT modified automatically — review and clean up its merged
    data in Gramps after splitting.

    Only available with the SQLite backend.  Requires a prior merge_persons
    call with dry_run=False.
    """
    params = SplitPersonParams(**arguments)
    client = await get_client()
    try:
        conn = _get_sqlite_conn(client)
        backup = _backup_file(client)

        if not os.path.exists(backup):
            return [TextContent(
                type="text",
                text=(
                    f"No merge backup file found at: {backup}\n"
                    "split_person only works if merge_persons was previously run "
                    "with dry_run=False."
                ),
            )]

        messages = _split_person(conn, params.gramps_id, backup)
        return [TextContent(
            type="text",
            text="\n".join(messages) or f"Split completed for {params.gramps_id}.",
        )]

    except GrampsAPIError as e:
        return [TextContent(type="text", text=f"Error: {e}")]
    finally:
        await client.close()
