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
Integration tests for DNA match MCP tools.

Uses an in-memory SQLite DB with the full Gramps schema.  All tool functions
are tested directly, with get_client() mocked to return a fake
GrampsSqliteClient backed by the in-memory connection.
"""

import json
import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from gramps_mcp.tools.dna import (
    add_dna_match_tool,
    get_dna_matches_tool,
    update_dna_match_tool,
)

# ---------------------------------------------------------------------------
# Minimal schema (mirrors conftest_sqlite.py / production DB)
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE person (
    handle VARCHAR(50) PRIMARY KEY NOT NULL,
    given_name TEXT, surname TEXT, json_data TEXT,
    gramps_id TEXT, gender INTEGER,
    death_ref_index INTEGER DEFAULT -1,
    birth_ref_index INTEGER DEFAULT -1,
    change INTEGER DEFAULT 0, private INTEGER DEFAULT 0
);
CREATE TABLE family (
    handle VARCHAR(50) PRIMARY KEY NOT NULL,
    json_data TEXT, gramps_id TEXT,
    father_handle VARCHAR(50), mother_handle VARCHAR(50),
    change INTEGER DEFAULT 0, private INTEGER DEFAULT 0
);
CREATE TABLE event (
    handle VARCHAR(50) PRIMARY KEY NOT NULL,
    json_data TEXT, gramps_id TEXT,
    description TEXT, place VARCHAR(50),
    change INTEGER DEFAULT 0, private INTEGER DEFAULT 0
);
CREATE TABLE note (
    handle VARCHAR(50) PRIMARY KEY NOT NULL,
    json_data TEXT, gramps_id TEXT,
    format INTEGER DEFAULT 0,
    change INTEGER DEFAULT 0, private INTEGER DEFAULT 0
);
CREATE TABLE reference (
    obj_handle VARCHAR(50), obj_class TEXT,
    ref_handle VARCHAR(50), ref_class TEXT
);
"""


# ---------------------------------------------------------------------------
# Test-data helpers
# ---------------------------------------------------------------------------


def _make_db() -> sqlite3.Connection:
    """Create an in-memory SQLite DB with the minimal Gramps schema."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def _insert_person(
    conn: sqlite3.Connection,
    handle: str,
    gramps_id: str,
    given: str,
    surname: str,
    person_ref_list: list = None,
) -> dict:
    """
    Insert a minimal person row and return the JSON dict.

    Args:
        conn: Open SQLite connection.
        handle: Gramps internal handle.
        gramps_id: Gramps public ID (e.g. 'I0001').
        given: Given name.
        surname: Surname.
        person_ref_list: Optional list of PersonRef dicts.

    Returns:
        dict: The person JSON that was inserted.
    """
    person = {
        "_class": "Person",
        "handle": handle,
        "gramps_id": gramps_id,
        "gender": 0,
        "primary_name": {
            "_class": "Name",
            "first_name": given,
            "surname_list": [{"_class": "Surname", "surname": surname}],
            "suffix": "",
            "title": "",
            "call": "",
            "nick": "",
            "type": {"_class": "NameType", "value": 2, "string": ""},
            "citation_list": [],
            "note_list": [],
        },
        "alternate_names": [],
        "event_ref_list": [],
        "family_list": [],
        "parent_family_list": [],
        "media_list": [],
        "address_list": [],
        "attribute_list": [],
        "urls": [],
        "lds_ord_list": [],
        "citation_list": [],
        "note_list": [],
        "person_ref_list": person_ref_list or [],
        "tag_list": [],
        "change": 0,
        "private": False,
    }
    conn.execute(
        "INSERT INTO person "
        "(handle, gramps_id, given_name, surname, json_data, gender) "
        "VALUES (?,?,?,?,?,?)",
        (handle, gramps_id, given, surname, json.dumps(person), 0),
    )
    return person


# ---------------------------------------------------------------------------
# Mock client factory
# ---------------------------------------------------------------------------


def _make_mock_client(conn: sqlite3.Connection):
    """
    Build a MagicMock that passes isinstance(client, GrampsSqliteClient).

    Args:
        conn: The in-memory SQLite connection to expose.

    Returns:
        MagicMock with spec=GrampsSqliteClient and ._db._conn = conn.
    """
    from gramps_mcp.sqlite_client import GrampsSqliteClient

    client = MagicMock(spec=GrampsSqliteClient)
    client._db = MagicMock()
    client._db._conn = conn
    return client


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_dna_match_creates_person_ref():
    """
    add_dna_match_tool must append a PersonRef with rel='DNA' to the
    test-taker's person_ref_list, pointing at the match person.
    """
    conn = _make_db()
    _insert_person(conn, "ph1", "I0001", "Hans", "Mueller")
    _insert_person(conn, "ph2", "I0002", "Maria", "Schmidt")

    mock_client = _make_mock_client(conn)
    with patch(
        "gramps_mcp.tools.dna.get_client", return_value=mock_client
    ):
        result = await add_dna_match_tool(
            {
                "person_id": "I0001",
                "match_person_id": "I0002",
                "shared_cm": 45.2,
                "relationship": "3rd Cousin",
                "side": "maternal",
            }
        )

    assert result, "Expected non-empty result"
    assert "Error:" not in result[0].text, f"Unexpected error: {result[0].text}"

    row = conn.execute(
        "SELECT json_data FROM person WHERE handle='ph1'"
    ).fetchone()
    person = json.loads(row["json_data"])
    dna_refs = [r for r in person["person_ref_list"] if r.get("rel") == "DNA"]
    assert len(dna_refs) == 1
    assert dna_refs[0]["ref"] == "ph2"


@pytest.mark.asyncio
async def test_add_dna_match_creates_note():
    """
    add_dna_match_tool must create a note row in the note table whose
    JSON contains the shared_cm value in the text field.
    """
    conn = _make_db()
    _insert_person(conn, "ph1", "I0001", "Hans", "Mueller")
    _insert_person(conn, "ph2", "I0002", "Maria", "Schmidt")

    mock_client = _make_mock_client(conn)
    with patch(
        "gramps_mcp.tools.dna.get_client", return_value=mock_client
    ):
        result = await add_dna_match_tool(
            {
                "person_id": "I0001",
                "match_person_id": "I0002",
                "shared_cm": 45.2,
                "relationship": "3rd Cousin",
                "side": "maternal",
            }
        )

    assert "Error:" not in result[0].text

    note_row = conn.execute("SELECT json_data FROM note LIMIT 1").fetchone()
    assert note_row is not None, "No note was created"
    note_data = json.loads(note_row["json_data"])
    note_text = note_data["text"]["string"]
    assert "45.2" in note_text, f"shared_cm not found in note text: {note_text!r}"
    assert "cM" in note_text


@pytest.mark.asyncio
async def test_add_dna_match_with_segments():
    """
    add_dna_match_tool must include segment lines (Chromosome\\tStart\\tEnd...)
    in the note text when segments are passed.
    """
    conn = _make_db()
    _insert_person(conn, "ph1", "I0001", "Hans", "Mueller")
    _insert_person(conn, "ph2", "I0002", "Maria", "Schmidt")

    mock_client = _make_mock_client(conn)
    with patch(
        "gramps_mcp.tools.dna.get_client", return_value=mock_client
    ):
        result = await add_dna_match_tool(
            {
                "person_id": "I0001",
                "match_person_id": "I0002",
                "shared_cm": 60.0,
                "segments": [
                    {
                        "chromosome": "3",
                        "start": 1000000,
                        "end": 50000000,
                        "cm": 32.1,
                        "snps": 8234,
                    },
                    {
                        "chromosome": "X",
                        "start": 2500000,
                        "end": 8000000,
                        "cm": 12.8,
                    },
                ],
            }
        )

    assert "Error:" not in result[0].text

    note_row = conn.execute("SELECT json_data FROM note LIMIT 1").fetchone()
    note_text = json.loads(note_row["json_data"])["text"]["string"]
    assert "Chromosome" in note_text
    assert "32.1" in note_text
    assert "8234" in note_text
    assert "X" in note_text


@pytest.mark.asyncio
async def test_get_dna_matches_returns_parsed_data():
    """
    get_dna_matches_tool must return JSON containing the shared_cm and
    relationship of a previously added match.

    With the labeled-prefix header format, relationship is correctly parsed
    even without a source field.
    """
    conn = _make_db()
    _insert_person(conn, "ph1", "I0001", "Hans", "Mueller")
    _insert_person(conn, "ph2", "I0002", "Maria", "Schmidt")

    mock_client = _make_mock_client(conn)
    with patch(
        "gramps_mcp.tools.dna.get_client", return_value=mock_client
    ):
        await add_dna_match_tool(
            {
                "person_id": "I0001",
                "match_person_id": "I0002",
                "shared_cm": 45.2,
                "relationship": "3rd Cousin",
                "side": "maternal",
            }
        )
        result = await get_dna_matches_tool({"person_id": "I0001"})

    assert "Error:" not in result[0].text
    matches = json.loads(result[0].text)
    assert len(matches) == 1
    m = matches[0]
    assert m["match_person_id"] == "I0002"
    assert abs(m["shared_cm"] - 45.2) < 0.01
    assert m["relationship"] == "3rd Cousin"
    assert m["side"] == "maternal"
    assert m["source"] is None


@pytest.mark.asyncio
async def test_get_dna_matches_with_segments():
    """
    get_dna_matches_tool must return a non-empty segments list when the
    stored note contains segment data.
    """
    conn = _make_db()
    _insert_person(conn, "ph1", "I0001", "Hans", "Mueller")
    _insert_person(conn, "ph2", "I0002", "Maria", "Schmidt")

    mock_client = _make_mock_client(conn)
    with patch(
        "gramps_mcp.tools.dna.get_client", return_value=mock_client
    ):
        await add_dna_match_tool(
            {
                "person_id": "I0001",
                "match_person_id": "I0002",
                "shared_cm": 60.0,
                "segments": [
                    {
                        "chromosome": "3",
                        "start": 1000000,
                        "end": 50000000,
                        "cm": 32.1,
                        "snps": 8234,
                    },
                ],
            }
        )
        result = await get_dna_matches_tool({"person_id": "I0001"})

    matches = json.loads(result[0].text)
    assert len(matches) == 1
    segs = matches[0]["segments"]
    assert len(segs) == 1
    assert segs[0]["chromosome"] == "3"
    assert abs(segs[0]["cm"] - 32.1) < 0.01
    assert segs[0]["snps"] == 8234


@pytest.mark.asyncio
async def test_add_duplicate_raises_error_or_returns_error():
    """
    Adding the same (person_id, match_person_id) pair a second time must
    return a TextContent with 'Error:' in the text rather than creating a
    duplicate PersonRef.
    """
    conn = _make_db()
    _insert_person(conn, "ph1", "I0001", "Hans", "Mueller")
    _insert_person(conn, "ph2", "I0002", "Maria", "Schmidt")

    mock_client = _make_mock_client(conn)
    args = {
        "person_id": "I0001",
        "match_person_id": "I0002",
        "shared_cm": 45.2,
    }

    with patch(
        "gramps_mcp.tools.dna.get_client", return_value=mock_client
    ):
        first = await add_dna_match_tool(args)
        assert "Error:" not in first[0].text, f"First add failed: {first[0].text}"

        second = await add_dna_match_tool(args)

    assert "Error:" in second[0].text, (
        f"Expected error on duplicate, got: {second[0].text!r}"
    )


@pytest.mark.asyncio
async def test_update_adds_segments_to_existing():
    """
    update_dna_match_tool must overwrite the existing note with new segment
    data while preserving fields not supplied in the update call.
    """
    conn = _make_db()
    _insert_person(conn, "ph1", "I0001", "Hans", "Mueller")
    _insert_person(conn, "ph2", "I0002", "Maria", "Schmidt")

    mock_client = _make_mock_client(conn)
    with patch(
        "gramps_mcp.tools.dna.get_client", return_value=mock_client
    ):
        # Add a summary-only match (no segments)
        await add_dna_match_tool(
            {
                "person_id": "I0001",
                "match_person_id": "I0002",
                "shared_cm": 45.2,
                "relationship": "3rd Cousin",
            }
        )

        # Update: add segments
        result = await update_dna_match_tool(
            {
                "person_id": "I0001",
                "match_person_id": "I0002",
                "segments": [
                    {
                        "chromosome": "5",
                        "start": 500000,
                        "end": 20000000,
                        "cm": 20.0,
                    }
                ],
            }
        )

    assert "Error:" not in result[0].text

    # Extract the new note handle from the tool's return text ("New note: <handle>")
    new_note_handle = result[0].text.split("New note:")[-1].strip()
    assert new_note_handle, "Expected a note handle in the result text"

    # Verify the updated note by its specific handle
    note_row = conn.execute(
        "SELECT json_data FROM note WHERE handle=?", (new_note_handle,)
    ).fetchone()
    assert note_row is not None, f"Note {new_note_handle!r} not found in DB"
    note_text = json.loads(note_row["json_data"])["text"]["string"]
    assert "Chromosome" in note_text
    assert "20.0" in note_text
    # Original relationship should still be in the note
    assert "3rd Cousin" in note_text


@pytest.mark.asyncio
async def test_dna_requires_sqlite_backend():
    """
    All three DNA tools must return a TextContent with 'Error:' when the
    active client is not a GrampsSqliteClient instance.
    """
    # A plain MagicMock without spec does NOT pass isinstance(..., GrampsSqliteClient)
    non_sqlite_client = MagicMock()

    for tool_fn, args in [
        (
            add_dna_match_tool,
            {"person_id": "I0001", "match_person_id": "I0002", "shared_cm": 10.0},
        ),
        (get_dna_matches_tool, {"person_id": "I0001"}),
        (update_dna_match_tool, {"person_id": "I0001", "match_person_id": "I0002"}),
    ]:
        with patch(
            "gramps_mcp.tools.dna.get_client", return_value=non_sqlite_client
        ):
            result = await tool_fn(args)

        assert result, f"No result returned from {tool_fn.__name__}"
        assert "Error:" in result[0].text, (
            f"{tool_fn.__name__} did not return an error for non-SQLite client: "
            f"{result[0].text!r}"
        )


@pytest.mark.asyncio
async def test_update_nonexistent_association_returns_error():
    """
    update_dna_match_tool must return an error when no DNA association
    exists between the two persons.
    """
    conn = _make_db()
    _insert_person(conn, "ph1", "I0001", "Hans", "Müller")
    _insert_person(conn, "ph2", "I0002", "Maria", "Schmidt")
    mock_client = _make_mock_client(conn)
    with patch("gramps_mcp.tools.dna.get_client", return_value=mock_client):
        result = await update_dna_match_tool({
            "person_id": "I0001",
            "match_person_id": "I0002",
            "shared_cm": 30.0,
        })
    assert any("Error" in r.text for r in result)


@pytest.mark.asyncio
async def test_get_dna_matches_empty():
    """
    get_dna_matches_tool must return a 'No DNA matches' message when the
    person exists but has no DNA associations.
    """
    conn = _make_db()
    _insert_person(conn, "ph1", "I0001", "Hans", "Müller")
    mock_client = _make_mock_client(conn)
    with patch("gramps_mcp.tools.dna.get_client", return_value=mock_client):
        result = await get_dna_matches_tool({"person_id": "I0001"})
    assert any("No DNA matches" in r.text for r in result)
