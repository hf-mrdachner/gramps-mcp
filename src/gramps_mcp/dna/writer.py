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

"""Writer for DNA match data into Gramps SQLite databases.

Provides _build_note_text (serialisation) and write_dna_match (persistence).
"""

import json
import sqlite3
import time
import uuid

from .models import DnaMatch


def _build_note_text(match: DnaMatch) -> str:
    """Serialise a DnaMatch to the plain-text note format.

    Args:
        match (DnaMatch): The match to serialise.

    Returns:
        str: Note text suitable for storing in a Gramps note.  Empty string
            when the match has no meaningful content.
    """
    parts = []
    if match.source:
        parts.append(match.source)
    if match.shared_cm:
        parts.append(f"{match.shared_cm} cM")
    if match.largest_segment:
        parts.append(f"Largest: {match.largest_segment} cM")
    if match.relationship:
        parts.append(match.relationship)
    if match.side:
        parts.append(f"Side: {match.side}")

    header = "# " + " | ".join(parts) if parts else ""

    if not match.segments:
        return header

    lines = [header, "Chromosome\tStart\tEnd\tcM\tSNPs"]
    for s in match.segments:
        lines.append(f"{s.chromosome}\t{s.start}\t{s.end}\t{s.cm}\t{s.snps or ''}")
    return "\n".join(lines)


def write_dna_match(
    conn: sqlite3.Connection,
    person_handle: str,
    match_handle: str,
    match: DnaMatch,
) -> str:
    """Write a DNA match to the SQLite database and return the new note handle.

    Creates a note row containing the serialised match text, appends a
    PersonRef entry to the owner person's person_ref_list, and inserts a
    reference row linking the person to the note.

    Args:
        conn (sqlite3.Connection): Open connection to a Gramps SQLite database.
        person_handle (str): Gramps handle of the person who owns the match.
        match_handle (str): Gramps handle of the matched person.
        match (DnaMatch): The DNA match data to persist.

    Returns:
        str: The handle of the newly created note row.
    """
    note_text = _build_note_text(match)
    # Reason: UUID without dashes gives a compact 32-char string; slice to 20
    # keeps it within the VARCHAR(50) handle column while remaining unique.
    note_handle = str(uuid.uuid4()).replace("-", "")[:20]

    row = conn.execute(
        "SELECT MAX(CAST(SUBSTR(gramps_id,2) AS INTEGER)) FROM note"
    ).fetchone()
    next_num = (row[0] or 0) + 1
    gramps_id = f"N{next_num:04d}"

    note_json = {
        "_class": "Note",
        "handle": note_handle,
        "gramps_id": gramps_id,
        "format": 0,
        "text": {"_class": "StyledText", "string": note_text, "tags": []},
        "type": {"_class": "NoteType", "value": 1, "string": "General"},
        "tag_list": [],
        "change": int(time.time()),
        "private": False,
    }

    person_row = conn.execute(
        "SELECT json_data FROM person WHERE handle=?", (person_handle,)
    ).fetchone()
    person = json.loads(person_row[0])
    person_ref = {
        "_class": "PersonRef",
        "ref": match_handle,
        "rel": "DNA",
        "note_list": [note_handle],
        "citation_list": [],
        "attribute_list": [],
        "private": False,
    }
    if "person_ref_list" not in person:
        person["person_ref_list"] = []
    person["person_ref_list"].append(person_ref)

    with conn:
        conn.execute(
            "INSERT INTO note (handle, gramps_id, json_data) VALUES (?,?,?)",
            (note_handle, gramps_id, json.dumps(note_json)),
        )
        conn.execute(
            "UPDATE person SET json_data=? WHERE handle=?",
            (json.dumps(person), person_handle),
        )
        conn.execute(
            "INSERT INTO reference (obj_handle, obj_class, ref_handle, ref_class) "
            "VALUES (?,?,?,?)",
            (person_handle, "Person", note_handle, "Note"),
        )

    return note_handle
