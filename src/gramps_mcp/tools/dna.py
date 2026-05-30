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
MCP tools for DNA match management.

Stores and retrieves DNA match data as structured notes attached to
PersonRef entries in a Gramps SQLite database.  All three tools require
the SQLite backend (GRAMPS_DB_PATH pointing to a live Gramps sqlite.db).
"""

import json
from typing import Dict, List

from mcp.types import TextContent

from ..client import GrampsAPIError, get_client
from ..dna import DnaMatch, DnaSegment, parse_dna_note, write_dna_match
from ..models.parameters.dna_params import (
    AddDnaMatchParams,
    GetDnaMatchesParams,
    UpdateDnaMatchParams,
)


def _get_sqlite_conn(client):
    """
    Extract the raw SQLite connection from a GrampsSqliteClient.

    Args:
        client: Active Gramps client from get_client().

    Returns:
        sqlite3.Connection ready for DNA operations.

    Raises:
        GrampsAPIError: If the active backend is not SQLite.
    """
    from ..sqlite_client import GrampsSqliteClient

    if not isinstance(client, GrampsSqliteClient):
        raise GrampsAPIError(
            "DNA tools require the SQLite backend. "
            "Set GRAMPS_DB_PATH to a Gramps sqlite.db file or directory."
        )
    return client._db._conn


async def add_dna_match_tool(arguments: Dict) -> List[TextContent]:
    """
    Add a DNA match between two persons.

    Creates a structured note with the match summary and optional segment
    data, then appends a PersonRef with rel='DNA' to the test-taker's
    person_ref_list.  Returns an error if the association already exists;
    use update_dna_match to modify an existing record.

    Only available with the SQLite backend.

    Args:
        arguments (dict): Must satisfy AddDnaMatchParams schema.

    Returns:
        list[TextContent]: Single item describing success or error.
    """
    params = AddDnaMatchParams(**arguments)
    client = get_client()
    try:
        conn = _get_sqlite_conn(client)

        taker_row = conn.execute(
            "SELECT handle FROM person WHERE gramps_id=?", (params.person_id,)
        ).fetchone()
        match_row = conn.execute(
            "SELECT handle FROM person WHERE gramps_id=?", (params.match_person_id,)
        ).fetchone()

        if taker_row is None:
            return [TextContent(
                type="text", text=f"Error: Person not found: {params.person_id}"
            )]
        if match_row is None:
            return [TextContent(
                type="text",
                text=f"Error: Match person not found: {params.match_person_id}",
            )]

        taker_handle = taker_row["handle"]
        match_handle = match_row["handle"]

        person_row = conn.execute(
            "SELECT json_data FROM person WHERE handle=?", (taker_handle,)
        ).fetchone()
        person = json.loads(person_row["json_data"])

        existing = [
            r
            for r in person.get("person_ref_list", [])
            if r.get("rel") == "DNA" and r.get("ref") == match_handle
        ]
        if existing:
            raise GrampsAPIError(
                f"DNA association already exists between {params.person_id} and "
                f"{params.match_person_id}. Use update_dna_match to modify it."
            )

        segments = []
        if params.segments:
            segments = [
                DnaSegment(
                    chromosome=s.chromosome,
                    start=s.start,
                    end=s.end,
                    cm=s.cm,
                    snps=s.snps,
                )
                for s in params.segments
            ]

        match = DnaMatch(
            match_handle=match_handle,
            shared_cm=params.shared_cm,
            largest_segment=params.largest_segment,
            relationship=params.relationship,
            side=params.side,
            source=params.source,
            segments=segments,
        )

        note_handle = write_dna_match(conn, taker_handle, match_handle, match)
        return [
            TextContent(
                type="text",
                text=(
                    f"DNA match added: {params.person_id} <-> {params.match_person_id} "
                    f"({params.shared_cm} cM). Note: {note_handle}"
                ),
            )
        ]

    except GrampsAPIError as e:
        return [TextContent(type="text", text=f"Error: {e}")]


async def get_dna_matches_tool(arguments: Dict) -> List[TextContent]:
    """
    Return all DNA matches recorded for a person.

    Reads PersonRef entries with rel='DNA', loads and parses their
    associated notes, and returns a JSON array of match summaries.

    Only available with the SQLite backend.

    Args:
        arguments (dict): Must satisfy GetDnaMatchesParams schema.

    Returns:
        list[TextContent]: Single item with JSON array of match dicts,
            or an error/informational message.
    """
    params = GetDnaMatchesParams(**arguments)
    client = get_client()
    try:
        conn = _get_sqlite_conn(client)

        person_row = conn.execute(
            "SELECT handle, json_data FROM person WHERE gramps_id=?",
            (params.person_id,),
        ).fetchone()
        if person_row is None:
            return [TextContent(
                type="text", text=f"Error: Person not found: {params.person_id}"
            )]

        person = json.loads(person_row["json_data"])
        dna_refs = [
            r for r in person.get("person_ref_list", []) if r.get("rel") == "DNA"
        ]

        if not dna_refs:
            return [TextContent(
                type="text",
                text=f"No DNA matches found for {params.person_id}.",
            )]

        results = []
        for ref in dna_refs:
            match_handle = ref["ref"]
            note_handles = ref.get("note_list", [])

            match_person_row = conn.execute(
                "SELECT json_data, gramps_id FROM person WHERE handle=?",
                (match_handle,),
            ).fetchone()
            if match_person_row is None:
                match_gramps_id = "unknown"
                match_name = "Unknown"
            else:
                match_gramps_id = match_person_row["gramps_id"]
                mp = json.loads(match_person_row["json_data"])
                pname = mp.get("primary_name", {})
                given = pname.get("first_name", "")
                slist = pname.get("surname_list", [])
                surname = slist[0].get("surname", "") if slist else ""
                match_name = f"{given} {surname}".strip()

            dna_match = DnaMatch(match_handle=match_handle)
            if note_handles:
                note_row = conn.execute(
                    "SELECT json_data FROM note WHERE handle=?", (note_handles[0],)
                ).fetchone()
                if note_row:
                    note_json = json.loads(note_row["json_data"])
                    note_text = note_json.get("text", {}).get("string", "")
                    dna_match = parse_dna_note(note_text)
                    dna_match.match_handle = match_handle
                    dna_match.note_handle = note_handles[0]

            results.append(
                {
                    "match_person_id": match_gramps_id,
                    "match_name": match_name,
                    "shared_cm": dna_match.shared_cm,
                    "largest_segment": dna_match.largest_segment,
                    "relationship": dna_match.relationship,
                    "side": dna_match.side,
                    "source": dna_match.source,
                    "segments": [
                        {
                            "chromosome": s.chromosome,
                            "start": s.start,
                            "end": s.end,
                            "cm": s.cm,
                            "snps": s.snps,
                        }
                        for s in dna_match.segments
                    ],
                }
            )

        return [TextContent(type="text", text=json.dumps(results, indent=2))]

    except GrampsAPIError as e:
        return [TextContent(type="text", text=f"Error: {e}")]


async def update_dna_match_tool(arguments: Dict) -> List[TextContent]:
    """
    Update fields on an existing DNA match.

    Loads the current note, parses it, applies the supplied field
    overrides, deletes the old note, and writes a fresh one.  Supplied
    segment data replaces all existing segments; omitting segments leaves
    them unchanged.

    Only available with the SQLite backend.

    Args:
        arguments (dict): Must satisfy UpdateDnaMatchParams schema.

    Returns:
        list[TextContent]: Single item describing success or error.
    """
    params = UpdateDnaMatchParams(**arguments)
    client = get_client()
    try:
        conn = _get_sqlite_conn(client)

        taker_row = conn.execute(
            "SELECT handle, json_data FROM person WHERE gramps_id=?",
            (params.person_id,),
        ).fetchone()
        match_row = conn.execute(
            "SELECT handle FROM person WHERE gramps_id=?", (params.match_person_id,)
        ).fetchone()

        if taker_row is None:
            return [TextContent(
                type="text", text=f"Error: Person not found: {params.person_id}"
            )]
        if match_row is None:
            return [TextContent(
                type="text",
                text=f"Error: Match person not found: {params.match_person_id}",
            )]

        taker_handle = taker_row["handle"]
        match_handle = match_row["handle"]

        person = json.loads(taker_row["json_data"])
        dna_refs = [
            r
            for r in person.get("person_ref_list", [])
            if r.get("rel") == "DNA" and r.get("ref") == match_handle
        ]
        if not dna_refs:
            raise GrampsAPIError(
                f"No DNA association found between {params.person_id} and "
                f"{params.match_person_id}."
            )

        ref = dna_refs[0]
        old_note_handles = ref.get("note_list", [])

        existing_match = DnaMatch(match_handle=match_handle)
        if old_note_handles:
            note_row = conn.execute(
                "SELECT json_data FROM note WHERE handle=?", (old_note_handles[0],)
            ).fetchone()
            if note_row:
                note_json = json.loads(note_row["json_data"])
                note_text = note_json.get("text", {}).get("string", "")
                existing_match = parse_dna_note(note_text)
                existing_match.match_handle = match_handle

        if params.shared_cm is not None:
            existing_match.shared_cm = params.shared_cm
        if params.largest_segment is not None:
            existing_match.largest_segment = params.largest_segment
        if params.relationship is not None:
            existing_match.relationship = params.relationship
        if params.side is not None:
            existing_match.side = params.side
        if params.source is not None:
            existing_match.source = params.source
        if params.segments is not None:
            existing_match.segments = [
                DnaSegment(
                    chromosome=s.chromosome,
                    start=s.start,
                    end=s.end,
                    cm=s.cm,
                    snps=s.snps,
                )
                for s in params.segments
            ]

        # Reason: write_dna_match manages its own transaction; we delete
        # separately so we don't nest incompatible transaction contexts.
        for old_h in old_note_handles:
            conn.execute("DELETE FROM note WHERE handle=?", (old_h,))
            conn.execute("DELETE FROM reference WHERE ref_handle=?", (old_h,))
        conn.commit()

        new_note_handle = write_dna_match(
            conn, taker_handle, match_handle, existing_match
        )

        # Update the PersonRef note_list in the person JSON to point at the new note.
        for r in person.get("person_ref_list", []):
            if r.get("rel") == "DNA" and r.get("ref") == match_handle:
                r["note_list"] = [new_note_handle]
        conn.execute(
            "UPDATE person SET json_data=? WHERE handle=?",
            (json.dumps(person), taker_handle),
        )
        conn.commit()

        return [
            TextContent(
                type="text",
                text=(
                    f"DNA match updated: {params.person_id} <-> "
                    f"{params.match_person_id}. New note: {new_note_handle}"
                ),
            )
        ]

    except GrampsAPIError as e:
        return [TextContent(type="text", text=f"Error: {e}")]
