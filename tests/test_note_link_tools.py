"""
Tests for note-link MCP tools:
  - add_note_to_person / add_note_to_family
  - remove_note_from_person / remove_note_from_family

All tests use fresh per-test in-memory SQLite DBs with db= injection.
"""

import asyncio
import json
import sqlite3

import pytest
from mcp.types import TextContent

from gramps_mcp._gramps_sqlite import NOTE_TYPE, GrampsSqliteDB
from gramps_mcp.client import GrampsAPIError

_SCHEMA = """
CREATE TABLE person (
    handle VARCHAR(50) PRIMARY KEY NOT NULL,
    given_name TEXT, surname TEXT,
    json_data TEXT, gramps_id TEXT, gender INTEGER DEFAULT 2,
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
CREATE TABLE note (
    handle VARCHAR(50) PRIMARY KEY NOT NULL,
    json_data TEXT, gramps_id TEXT, format INTEGER DEFAULT 0,
    change INTEGER DEFAULT 0, private INTEGER DEFAULT 0
);
CREATE TABLE metadata (
    setting VARCHAR(50) PRIMARY KEY NOT NULL,
    json_data TEXT, value BLOB
);
"""


@pytest.fixture()
def fresh_db():
    """Fresh in-memory GrampsSqliteDB for each test."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    conn.commit()
    db = GrampsSqliteDB(conn=conn, db_path=":memory:", read_only=False)
    return db, conn


def _insert_person(conn, handle: str, gramps_id: str, note_list=None) -> None:
    data = {
        "_class": "Person",
        "handle": handle,
        "gramps_id": gramps_id,
        "gender": 2,
        "primary_name": {
            "_class": "Name",
            "first_name": "Test",
            "suffix": "",
            "title": "",
            "call": "",
            "nick": "",
            "famnick": "",
            "group_as": "",
            "sort_as": 0,
            "display_as": 0,
            "private": False,
            "surname_list": [
                {
                    "_class": "Surname",
                    "surname": "Person",
                    "prefix": "",
                    "primary": True,
                    "connector": "",
                    "origintype": {
                        "_class": "NameOriginType",
                        "value": 1,
                        "string": "",
                    },
                }
            ],
            "citation_list": [],
            "note_list": [],
            "type": {"_class": "NameType", "value": 2, "string": ""},
            "date": {
                "_class": "Date",
                "calendar": 0,
                "modifier": 0,
                "quality": 0,
                "dateval": [0, 0, 0, False],
                "text": "",
                "sortval": 0,
                "newyear": 0,
                "format": None,
            },
        },
        "alternate_names": [],
        "death_ref_index": -1,
        "birth_ref_index": -1,
        "event_ref_list": [],
        "family_list": [],
        "parent_family_list": [],
        "media_list": [],
        "address_list": [],
        "attribute_list": [],
        "urls": [],
        "lds_ord_list": [],
        "citation_list": [],
        "note_list": note_list or [],
        "tag_list": [],
        "person_ref_list": [],
        "change": 0,
        "private": False,
    }
    conn.execute(
        "INSERT INTO person (handle, gramps_id, json_data, change, private, "
        "birth_ref_index, death_ref_index) VALUES (?,?,?,0,0,-1,-1)",
        (handle, gramps_id, json.dumps(data)),
    )
    conn.commit()


def _insert_note(
    conn,
    handle: str,
    gramps_id: str,
    text: str = "Original text",
    note_type: str = "General",
    private: bool = False,
    format: int = 0,
) -> None:
    data = {
        "_class": "Note",
        "handle": handle,
        "gramps_id": gramps_id,
        "format": format,
        "text": {"_class": "StyledText", "string": text, "tags": []},
        "type": {"_class": "NoteType", "value": 1, "string": note_type},
        "tag_list": [],
        "change": 0,
        "private": private,
    }
    conn.execute(
        "INSERT INTO note (handle, gramps_id, json_data, format, change, private) "
        "VALUES (?,?,?,?,0,?)",
        (handle, gramps_id, json.dumps(data), format, int(private)),
    )
    conn.commit()


def _insert_family(conn, handle: str, gramps_id: str, note_list=None) -> None:
    data = {
        "_class": "Family",
        "handle": handle,
        "gramps_id": gramps_id,
        "father_handle": None,
        "mother_handle": None,
        "child_ref_list": [],
        "type": {"_class": "FamilyRelType", "value": 0, "string": ""},
        "event_ref_list": [],
        "media_list": [],
        "attribute_list": [],
        "lds_ord_list": [],
        "citation_list": [],
        "note_list": note_list or [],
        "tag_list": [],
        "change": 0,
        "private": False,
    }
    conn.execute(
        "INSERT INTO family (handle, gramps_id, json_data, change, private) "
        "VALUES (?,?,?,0,0)",
        (handle, gramps_id, json.dumps(data)),
    )
    conn.commit()


# ===========================================================================
# add_note_to_person
# ===========================================================================


class TestAddNoteToPerson:
    def test_link_existing_note(self, fresh_db):
        from gramps_mcp.tools.note_link import add_note_to_person_tool

        db, conn = fresh_db
        _insert_person(conn, "h_pe", "I0001")
        _insert_note(conn, "h_no", "N0001")

        result = asyncio.run(
            add_note_to_person_tool(person_handle="h_pe", note_handle="h_no", db=db)
        )
        assert isinstance(result, list) and isinstance(result[0], TextContent)
        data = json.loads(result[0].text)
        assert data["result"] == "linked"
        assert data["note_handle"] == "h_no"
        assert data["note_gramps_id"] == "N0001"
        assert data["note_count"] == 1

        row = conn.execute(
            "SELECT json_data FROM person WHERE handle = 'h_pe'"
        ).fetchone()
        person_data = json.loads(row["json_data"])
        assert "h_no" in person_data["note_list"]

    def test_link_idempotent(self, fresh_db):
        from gramps_mcp.tools.note_link import add_note_to_person_tool

        db, conn = fresh_db
        _insert_person(conn, "h_pe", "I0001")
        _insert_note(conn, "h_no", "N0001")

        asyncio.run(
            add_note_to_person_tool(person_handle="h_pe", note_handle="h_no", db=db)
        )
        result = asyncio.run(
            add_note_to_person_tool(person_handle="h_pe", note_handle="h_no", db=db)
        )
        data = json.loads(result[0].text)
        assert data["result"] == "no_change"

        row = conn.execute(
            "SELECT json_data FROM person WHERE handle = 'h_pe'"
        ).fetchone()
        person_data = json.loads(row["json_data"])
        assert person_data["note_list"].count("h_no") == 1

    def test_link_via_gramps_ids(self, fresh_db):
        from gramps_mcp.tools.note_link import add_note_to_person_tool

        db, conn = fresh_db
        _insert_person(conn, "h_pe", "I0001")
        _insert_note(conn, "h_no", "N0001")

        result = asyncio.run(
            add_note_to_person_tool(
                person_gramps_id="I0001", note_gramps_id="N0001", db=db
            )
        )
        data = json.loads(result[0].text)
        assert data["result"] == "linked"

    def test_link_error_unknown_note(self, fresh_db):
        from gramps_mcp.tools.note_link import add_note_to_person_tool

        db, conn = fresh_db
        _insert_person(conn, "h_pe", "I0001")

        with pytest.raises(GrampsAPIError, match="not found"):
            asyncio.run(
                add_note_to_person_tool(
                    person_handle="h_pe", note_handle="missing", db=db
                )
            )

    def test_create_new_note(self, fresh_db):
        from gramps_mcp.tools.note_link import add_note_to_person_tool

        db, conn = fresh_db
        _insert_person(conn, "h_pe", "I0001")

        result = asyncio.run(
            add_note_to_person_tool(
                person_handle="h_pe", text="Brand new note", type="Research", db=db
            )
        )
        data = json.loads(result[0].text)
        assert data["result"] == "created"
        assert data["note_gramps_id"] == "N0001"
        new_handle = data["note_handle"]

        row = conn.execute(
            "SELECT json_data FROM note WHERE handle = ?", (new_handle,)
        ).fetchone()
        note_data = json.loads(row["json_data"])
        assert note_data["text"]["string"] == "Brand new note"
        # "Research" is a recognized NoteType, so db.put's denormalization (via
        # _denorm_type) stores it as {value: 2, string: ""} — value is the source
        # of truth for known types, string is only populated for custom/unrecognized
        # ones. Look the value back up in NOTE_TYPE rather than checking "string".
        assert NOTE_TYPE[note_data["type"]["value"]] == "Research"

        prow = conn.execute(
            "SELECT json_data FROM person WHERE handle = 'h_pe'"
        ).fetchone()
        person_data = json.loads(prow["json_data"])
        assert new_handle in person_data["note_list"]

    def test_update_existing_note_text(self, fresh_db):
        from gramps_mcp.tools.note_link import add_note_to_person_tool

        db, conn = fresh_db
        _insert_person(conn, "h_pe", "I0001", note_list=["h_no"])
        _insert_note(conn, "h_no", "N0001", text="Old text", note_type="General")

        result = asyncio.run(
            add_note_to_person_tool(
                person_handle="h_pe", note_handle="h_no", text="New text", db=db
            )
        )
        data = json.loads(result[0].text)
        assert data["result"] == "updated"
        assert data["note_count"] == 1  # already linked, unchanged

        row = conn.execute(
            "SELECT json_data FROM note WHERE handle = 'h_no'"
        ).fetchone()
        note_data = json.loads(row["json_data"])
        assert note_data["text"]["string"] == "New text"
        assert note_data["type"]["string"] == "General"  # untouched

    def test_update_and_link_not_yet_linked(self, fresh_db):
        from gramps_mcp.tools.note_link import add_note_to_person_tool

        db, conn = fresh_db
        _insert_person(conn, "h_pe", "I0001")
        _insert_note(conn, "h_no", "N0001", text="Old text")

        result = asyncio.run(
            add_note_to_person_tool(
                person_handle="h_pe", note_handle="h_no", text="New text", db=db
            )
        )
        data = json.loads(result[0].text)
        assert data["result"] == "updated"
        assert data["note_count"] == 1  # newly linked as part of the update

        row = conn.execute(
            "SELECT json_data FROM note WHERE handle = 'h_no'"
        ).fetchone()
        assert json.loads(row["json_data"])["text"]["string"] == "New text"

    def test_error_no_identifier_no_content(self, fresh_db):
        from gramps_mcp.tools.note_link import add_note_to_person_tool

        db, conn = fresh_db
        _insert_person(conn, "h_pe", "I0001")

        with pytest.raises(GrampsAPIError, match="text/type"):
            asyncio.run(add_note_to_person_tool(person_handle="h_pe", db=db))

    def test_error_unknown_person(self, fresh_db):
        from gramps_mcp.tools.note_link import add_note_to_person_tool

        db, conn = fresh_db
        _insert_note(conn, "h_no", "N0001")

        with pytest.raises(GrampsAPIError, match="not found"):
            asyncio.run(
                add_note_to_person_tool(
                    person_handle="missing", note_handle="h_no", db=db
                )
            )

    def test_update_preserves_private_and_format(self, fresh_db):
        """Updating a note's text must not silently reset private/format.

        Regression test: db.put() computes the stored `private` field and the
        `format` secondary column directly from the patch dict it is given, so
        a partial patch (only handle/text/type) would previously reset an
        existing note's private=True flag and non-default format to
        False/0 on every update.
        """
        from gramps_mcp.tools.note_link import add_note_to_person_tool

        db, conn = fresh_db
        _insert_person(conn, "h_pe", "I0001", note_list=["h_no"])
        _insert_note(
            conn,
            "h_no",
            "N0001",
            text="Old text",
            private=True,
            format=1,
        )

        result = asyncio.run(
            add_note_to_person_tool(
                person_handle="h_pe", note_handle="h_no", text="New text", db=db
            )
        )
        data = json.loads(result[0].text)
        assert data["result"] == "updated"

        row = conn.execute(
            "SELECT json_data, private, format FROM note WHERE handle = 'h_no'"
        ).fetchone()
        note_data = json.loads(row["json_data"])
        assert note_data["text"]["string"] == "New text"
        assert note_data["private"] is True
        assert note_data["format"] == 1
        assert row["private"] == 1
        assert row["format"] == 1


# ===========================================================================
# add_note_to_family
# ===========================================================================


class TestAddNoteToFamily:
    def test_link_existing_note(self, fresh_db):
        from gramps_mcp.tools.note_link import add_note_to_family_tool

        db, conn = fresh_db
        _insert_family(conn, "h_fa", "F0001")
        _insert_note(conn, "h_no", "N0001")

        result = asyncio.run(
            add_note_to_family_tool(family_handle="h_fa", note_handle="h_no", db=db)
        )
        data = json.loads(result[0].text)
        assert data["result"] == "linked"
        assert data["family_handle"] == "h_fa"
        assert data["note_count"] == 1

        row = conn.execute(
            "SELECT json_data FROM family WHERE handle = 'h_fa'"
        ).fetchone()
        assert "h_no" in json.loads(row["json_data"])["note_list"]

    def test_link_idempotent(self, fresh_db):
        from gramps_mcp.tools.note_link import add_note_to_family_tool

        db, conn = fresh_db
        _insert_family(conn, "h_fa", "F0001")
        _insert_note(conn, "h_no", "N0001")

        asyncio.run(
            add_note_to_family_tool(family_handle="h_fa", note_handle="h_no", db=db)
        )
        result = asyncio.run(
            add_note_to_family_tool(family_handle="h_fa", note_handle="h_no", db=db)
        )
        assert json.loads(result[0].text)["result"] == "no_change"

    def test_create_new_note(self, fresh_db):
        from gramps_mcp.tools.note_link import add_note_to_family_tool

        db, conn = fresh_db
        _insert_family(conn, "h_fa", "F0001")

        result = asyncio.run(
            add_note_to_family_tool(
                family_handle="h_fa",
                text="Family research note",
                type="Research",
                db=db,
            )
        )
        data = json.loads(result[0].text)
        assert data["result"] == "created"
        new_handle = data["note_handle"]

        row = conn.execute(
            "SELECT json_data FROM family WHERE handle = 'h_fa'"
        ).fetchone()
        assert new_handle in json.loads(row["json_data"])["note_list"]

    def test_update_existing_note(self, fresh_db):
        from gramps_mcp.tools.note_link import add_note_to_family_tool

        db, conn = fresh_db
        _insert_family(conn, "h_fa", "F0001", note_list=["h_no"])
        _insert_note(conn, "h_no", "N0001", text="Old")

        result = asyncio.run(
            add_note_to_family_tool(
                family_handle="h_fa", note_handle="h_no", text="New", db=db
            )
        )
        assert json.loads(result[0].text)["result"] == "updated"

        row = conn.execute(
            "SELECT json_data FROM note WHERE handle = 'h_no'"
        ).fetchone()
        assert json.loads(row["json_data"])["text"]["string"] == "New"

    def test_error_unknown_family(self, fresh_db):
        from gramps_mcp.tools.note_link import add_note_to_family_tool

        db, conn = fresh_db
        _insert_note(conn, "h_no", "N0001")

        with pytest.raises(GrampsAPIError, match="not found"):
            asyncio.run(
                add_note_to_family_tool(
                    family_handle="missing", note_handle="h_no", db=db
                )
            )


# ===========================================================================
# remove_note_from_person
# ===========================================================================


class TestRemoveNoteFromPerson:
    def test_note_removed(self, fresh_db):
        from gramps_mcp.tools.note_link import remove_note_from_person_tool

        db, conn = fresh_db
        _insert_note(conn, "h_no", "N0001")
        _insert_person(conn, "h_pe", "I0001", note_list=["h_no"])

        result = asyncio.run(
            remove_note_from_person_tool(
                person_handle="h_pe", note_handle="h_no", db=db
            )
        )
        data = json.loads(result[0].text)
        assert data["result"] == "ok"
        assert data["note_count"] == 0

        row = conn.execute(
            "SELECT json_data FROM person WHERE handle = 'h_pe'"
        ).fetchone()
        assert "h_no" not in json.loads(row["json_data"])["note_list"]

    def test_second_note_survives(self, fresh_db):
        from gramps_mcp.tools.note_link import remove_note_from_person_tool

        db, conn = fresh_db
        _insert_note(conn, "h_no1", "N0001")
        _insert_note(conn, "h_no2", "N0002")
        _insert_person(conn, "h_pe", "I0001", note_list=["h_no1", "h_no2"])

        asyncio.run(
            remove_note_from_person_tool(
                person_handle="h_pe", note_handle="h_no1", db=db
            )
        )

        row = conn.execute(
            "SELECT json_data FROM person WHERE handle = 'h_pe'"
        ).fetchone()
        note_list = json.loads(row["json_data"])["note_list"]
        assert "h_no1" not in note_list
        assert "h_no2" in note_list

    def test_error_when_not_linked(self, fresh_db):
        from gramps_mcp.tools.note_link import remove_note_from_person_tool

        db, conn = fresh_db
        _insert_note(conn, "h_no", "N0001")
        _insert_person(conn, "h_pe", "I0001")

        with pytest.raises(GrampsAPIError, match="not linked"):
            asyncio.run(
                remove_note_from_person_tool(
                    person_handle="h_pe", note_handle="h_no", db=db
                )
            )

    def test_remove_via_gramps_ids(self, fresh_db):
        from gramps_mcp.tools.note_link import remove_note_from_person_tool

        db, conn = fresh_db
        _insert_note(conn, "h_no", "N0001")
        _insert_person(conn, "h_pe", "I0001", note_list=["h_no"])

        result = asyncio.run(
            remove_note_from_person_tool(
                person_gramps_id="I0001", note_gramps_id="N0001", db=db
            )
        )
        assert json.loads(result[0].text)["result"] == "ok"

    def test_error_unknown_person(self, fresh_db):
        from gramps_mcp.tools.note_link import remove_note_from_person_tool

        db, conn = fresh_db
        _insert_note(conn, "h_no", "N0001")

        with pytest.raises(GrampsAPIError, match="not found"):
            asyncio.run(
                remove_note_from_person_tool(
                    person_gramps_id="missing", note_handle="h_no", db=db
                )
            )


# ===========================================================================
# remove_note_from_family
# ===========================================================================


class TestRemoveNoteFromFamily:
    def test_note_removed(self, fresh_db):
        from gramps_mcp.tools.note_link import remove_note_from_family_tool

        db, conn = fresh_db
        _insert_note(conn, "h_no", "N0001")
        _insert_family(conn, "h_fa", "F0001", note_list=["h_no"])

        result = asyncio.run(
            remove_note_from_family_tool(
                family_handle="h_fa", note_handle="h_no", db=db
            )
        )
        data = json.loads(result[0].text)
        assert data["result"] == "ok"
        assert data["note_count"] == 0

    def test_error_when_not_linked(self, fresh_db):
        from gramps_mcp.tools.note_link import remove_note_from_family_tool

        db, conn = fresh_db
        _insert_note(conn, "h_no", "N0001")
        _insert_family(conn, "h_fa", "F0001")

        with pytest.raises(GrampsAPIError, match="not linked"):
            asyncio.run(
                remove_note_from_family_tool(
                    family_handle="h_fa", note_handle="h_no", db=db
                )
            )

    def test_error_unknown_family(self, fresh_db):
        from gramps_mcp.tools.note_link import remove_note_from_family_tool

        db, conn = fresh_db
        _insert_note(conn, "h_no", "N0001")

        with pytest.raises(GrampsAPIError, match="not found"):
            asyncio.run(
                remove_note_from_family_tool(
                    family_gramps_id="missing", note_handle="h_no", db=db
                )
            )
