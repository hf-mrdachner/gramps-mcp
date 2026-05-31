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
Integration tests for the DNA parser module.

Tests cover:
  - parse_dna_note — full note, header-only, segments-only, empty input
  - _build_note_text — round-trip serialisation
  - write_dna_match — SQLite insert and round-trip
"""

import json
import sqlite3

from gramps_mcp.dna import DnaMatch, DnaSegment, parse_dna_note, write_dna_match
from gramps_mcp.dna.writer import _build_note_text

# ---------------------------------------------------------------------------
# Minimal schema (mirrors production DB)
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
CREATE TABLE note (
    handle VARCHAR(50) PRIMARY KEY NOT NULL,
    gramps_id TEXT,
    json_data TEXT,
    change INTEGER DEFAULT 0,
    private INTEGER DEFAULT 0
);
CREATE TABLE reference (
    obj_handle VARCHAR(50), obj_class TEXT,
    ref_handle VARCHAR(50), ref_class TEXT
);
"""

# ---------------------------------------------------------------------------
# Test-data builders
# ---------------------------------------------------------------------------


def _make_db() -> sqlite3.Connection:
    """Create an in-memory SQLite DB with the minimal required schema."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def _insert_person(conn: sqlite3.Connection, handle: str, gramps_id: str) -> None:
    """Insert a minimal person row into the test DB."""
    data = {
        "_class": "Person",
        "handle": handle,
        "gramps_id": gramps_id,
        "gender": 1,
        "primary_name": {"_class": "Name", "first_name": "Test", "surname_list": []},
        "event_ref_list": [],
        "family_list": [],
        "parent_family_list": [],
        "person_ref_list": [],
        "birth_ref_index": -1,
        "death_ref_index": -1,
        "change": 0,
        "private": False,
    }
    sql = (
        "INSERT INTO person (handle, gramps_id, json_data, given_name, surname,"
        " gender, birth_ref_index, death_ref_index, change, private)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)"
    )
    conn.execute(
        sql,
        [handle, gramps_id, json.dumps(data), "Test", "Person", 1, -1, -1, 0, 0],
    )


# ---------------------------------------------------------------------------
# parse_dna_note tests
# ---------------------------------------------------------------------------


_FULL_NOTE = """\
# AncestryDNA | 45.2 cM | Largest: 32.1 cM | 3rd Cousin | Side: maternal
Chromosome\tStart\tEnd\tcM\tSNPs
1\t1000000\t50000000\t32.1\t8234
X\t2500000\t8000000\t12.8\t3210
"""

_HEADER_ONLY_NOTE = "# GEDmatch | 18.5 cM | Side: paternal"

_NO_HEADER_NOTE = """\
Chromosome\tStart\tEnd\tcM\tSNPs
2\t500000\t30000000\t14.0\t5000
"""

_EMPTY_NOTE = ""

_NO_SNPS_NOTE = """\
# 23andMe | 22.0 cM | 2nd Cousin
Chromosome\tStart\tEnd\tcM\tSNPs
3\t100000\t20000000\t22.0\t
"""


class TestParseDnaNote:
    """Tests for parse_dna_note."""

    def test_full_note_header_fields(self):
        """All header fields are parsed correctly from a complete note."""
        match = parse_dna_note(_FULL_NOTE)
        assert match.source == "AncestryDNA"
        assert match.shared_cm == 45.2
        assert match.largest_segment == 32.1
        assert match.relationship == "3rd Cousin"
        assert match.side == "maternal"

    def test_full_note_match_handle_empty(self):
        """match_handle is empty string — set by caller, not by parser."""
        match = parse_dna_note(_FULL_NOTE)
        assert match.match_handle == ""

    def test_full_note_segments_count(self):
        """Two segment lines yield two DnaSegment objects."""
        match = parse_dna_note(_FULL_NOTE)
        assert len(match.segments) == 2

    def test_full_note_first_segment(self):
        """First segment has correct chromosome, coordinates, cM, and SNPs."""
        seg = parse_dna_note(_FULL_NOTE).segments[0]
        assert seg.chromosome == "1"
        assert seg.start == 1000000
        assert seg.end == 50000000
        assert seg.cm == 32.1
        assert seg.snps == 8234

    def test_full_note_x_chromosome_segment(self):
        """X chromosome is parsed as string 'X', not converted to int."""
        seg = parse_dna_note(_FULL_NOTE).segments[1]
        assert seg.chromosome == "X"
        assert seg.start == 2500000
        assert seg.end == 8000000
        assert seg.cm == 12.8
        assert seg.snps == 3210

    def test_header_only_note(self):
        """Header-only note yields no segments but correct header fields."""
        match = parse_dna_note(_HEADER_ONLY_NOTE)
        assert match.source == "GEDmatch"
        assert match.shared_cm == 18.5
        assert match.side == "paternal"
        assert match.relationship is None
        assert match.segments == []

    def test_no_header_note_shared_cm_zero(self):
        """Note without # line results in shared_cm=0.0."""
        match = parse_dna_note(_NO_HEADER_NOTE)
        assert match.shared_cm == 0.0

    def test_no_header_note_segments_parsed(self):
        """Segments below a missing header are still parsed."""
        match = parse_dna_note(_NO_HEADER_NOTE)
        assert len(match.segments) == 1
        assert match.segments[0].chromosome == "2"

    def test_empty_note(self):
        """Empty string returns DnaMatch with defaults and no segments."""
        match = parse_dna_note(_EMPTY_NOTE)
        assert match.shared_cm == 0.0
        assert match.source is None
        assert match.segments == []

    def test_missing_snps_is_none(self):
        """SNPs field that is empty string parses to None."""
        match = parse_dna_note(_NO_SNPS_NOTE)
        assert len(match.segments) == 1
        assert match.segments[0].snps is None

    def test_relationship_no_largest(self):
        """Relationship field is extracted even without a Largest: field."""
        match = parse_dna_note(_NO_SNPS_NOTE)
        assert match.relationship == "2nd Cousin"
        assert match.largest_segment is None


# ---------------------------------------------------------------------------
# _build_note_text tests
# ---------------------------------------------------------------------------


class TestBuildNoteText:
    """Tests for the private _build_note_text helper in writer.py."""

    def test_round_trip_full(self):
        """A DnaMatch with all fields serialises and re-parses identically."""
        original = DnaMatch(
            match_handle="abc123",
            shared_cm=45.2,
            largest_segment=32.1,
            relationship="3rd Cousin",
            side="maternal",
            source="AncestryDNA",
            segments=[
                DnaSegment(
                    chromosome="1", start=1000000, end=50000000, cm=32.1, snps=8234
                ),
                DnaSegment(
                    chromosome="X", start=2500000, end=8000000, cm=12.8, snps=3210
                ),
            ],
        )
        text = _build_note_text(original)
        parsed = parse_dna_note(text)
        assert parsed.source == original.source
        assert parsed.shared_cm == original.shared_cm
        assert parsed.largest_segment == original.largest_segment
        assert parsed.relationship == original.relationship
        assert parsed.side == original.side
        assert len(parsed.segments) == 2
        assert parsed.segments[0].chromosome == "1"
        assert parsed.segments[0].snps == 8234

    def test_no_segments_no_table_header(self):
        """Note without segments omits the column-header row."""
        match = DnaMatch(match_handle="x", shared_cm=10.0, source="FTDNA")
        text = _build_note_text(match)
        assert "Chromosome" not in text

    def test_empty_match_empty_string(self):
        """DnaMatch with only match_handle and shared_cm=0.0 gives empty header."""
        match = DnaMatch(match_handle="x", shared_cm=0.0)
        text = _build_note_text(match)
        # No pipe-separated content — header is empty string (no parts, no prefix)
        assert text == ""

    def test_segments_present_includes_table_header(self):
        """When segments exist the column-header row appears in output."""
        match = DnaMatch(
            match_handle="x",
            shared_cm=5.0,
            segments=[DnaSegment(chromosome="1", start=1, end=2, cm=5.0, snps=100)],
        )
        text = _build_note_text(match)
        assert "Chromosome\tStart\tEnd\tcM\tSNPs" in text

    def test_snps_none_produces_empty_column(self):
        """A segment with snps=None renders as empty string in the last column."""
        match = DnaMatch(
            match_handle="x",
            shared_cm=5.0,
            segments=[DnaSegment(chromosome="2", start=1, end=2, cm=5.0, snps=None)],
        )
        text = _build_note_text(match)
        last_line = [ln for ln in text.splitlines() if ln.startswith("2")][0]
        assert last_line.endswith("\t")

    def test_round_trip_relationship_only(self):
        """A match with relationship but no source round-trips correctly."""
        original = DnaMatch(
            match_handle="abc",
            shared_cm=45.2,
            relationship="3rd Cousin",
        )
        text = _build_note_text(original)
        parsed = parse_dna_note(text)
        assert parsed.relationship == "3rd Cousin"
        assert parsed.source is None
        assert parsed.shared_cm == 45.2

    def test_round_trip_source_only(self):
        """A match with source but no relationship round-trips correctly."""
        original = DnaMatch(
            match_handle="abc",
            shared_cm=45.2,
            source="AncestryDNA",
        )
        text = _build_note_text(original)
        parsed = parse_dna_note(text)
        assert parsed.source == "AncestryDNA"
        assert parsed.relationship is None


# ---------------------------------------------------------------------------
# write_dna_match tests
# ---------------------------------------------------------------------------


class TestWriteDnaMatch:
    """Tests for write_dna_match (SQLite write path)."""

    def test_returns_note_handle(self):
        """write_dna_match returns a non-empty string handle."""
        conn = _make_db()
        _insert_person(conn, "p_owner", "I0001")
        _insert_person(conn, "p_match", "I0002")
        match = DnaMatch(match_handle="p_match", shared_cm=30.0, source="AncestryDNA")
        handle = write_dna_match(conn, "p_owner", "p_match", match)
        assert isinstance(handle, str)
        assert len(handle) > 0

    def test_note_row_inserted(self):
        """A row is created in the note table after write_dna_match."""
        conn = _make_db()
        _insert_person(conn, "p_owner", "I0001")
        _insert_person(conn, "p_match", "I0002")
        match = DnaMatch(match_handle="p_match", shared_cm=30.0, source="AncestryDNA")
        handle = write_dna_match(conn, "p_owner", "p_match", match)
        row = conn.execute("SELECT * FROM note WHERE handle=?", (handle,)).fetchone()
        assert row is not None

    def test_note_json_contains_text(self):
        """The note JSON stores the serialised note text."""
        conn = _make_db()
        _insert_person(conn, "p_owner", "I0001")
        _insert_person(conn, "p_match", "I0002")
        match = DnaMatch(match_handle="p_match", shared_cm=30.0, source="AncestryDNA")
        handle = write_dna_match(conn, "p_owner", "p_match", match)
        row = conn.execute(
            "SELECT json_data FROM note WHERE handle=?", (handle,)
        ).fetchone()
        note_data = json.loads(row["json_data"])
        assert "AncestryDNA" in note_data["text"]["string"]

    def test_person_ref_appended(self):
        """person_ref_list gains one entry referencing match_handle."""
        conn = _make_db()
        _insert_person(conn, "p_owner", "I0001")
        _insert_person(conn, "p_match", "I0002")
        match = DnaMatch(match_handle="p_match", shared_cm=30.0)
        write_dna_match(conn, "p_owner", "p_match", match)
        row = conn.execute(
            "SELECT json_data FROM person WHERE handle='p_owner'"
        ).fetchone()
        person = json.loads(row["json_data"])
        refs = person["person_ref_list"]
        assert len(refs) == 1
        assert refs[0]["ref"] == "p_match"

    def test_reference_row_inserted(self):
        """A reference row linking person to note is inserted in the reference table."""
        conn = _make_db()
        _insert_person(conn, "p_owner", "I0001")
        _insert_person(conn, "p_match", "I0002")
        match = DnaMatch(match_handle="p_match", shared_cm=30.0)
        handle = write_dna_match(conn, "p_owner", "p_match", match)
        row = conn.execute(
            "SELECT * FROM reference WHERE obj_handle='p_owner' AND ref_handle=?",
            (handle,),
        ).fetchone()
        assert row is not None
        assert row["ref_class"] == "Note"

    def test_gramps_id_sequential(self):
        """Two successive calls produce notes with distinct sequential gramps_ids."""
        conn = _make_db()
        _insert_person(conn, "p_owner", "I0001")
        _insert_person(conn, "p_match1", "I0002")
        _insert_person(conn, "p_match2", "I0003")
        h1 = write_dna_match(
            conn,
            "p_owner",
            "p_match1",
            DnaMatch(match_handle="p_match1", shared_cm=10.0),
        )
        h2 = write_dna_match(
            conn,
            "p_owner",
            "p_match2",
            DnaMatch(match_handle="p_match2", shared_cm=20.0),
        )
        row1 = conn.execute(
            "SELECT gramps_id FROM note WHERE handle=?", (h1,)
        ).fetchone()
        row2 = conn.execute(
            "SELECT gramps_id FROM note WHERE handle=?", (h2,)
        ).fetchone()
        assert row1["gramps_id"] != row2["gramps_id"]
