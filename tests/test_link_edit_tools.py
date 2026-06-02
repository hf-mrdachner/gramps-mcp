"""
Tests for link-edit MCP tools:
  - add_event_to_person
  - remove_child_from_family
  - move_attachment

All tests use fresh per-test in-memory SQLite DBs. Tool functions are called
directly with the db= parameter injected (same pattern as test_delete_tool.py).
"""

import asyncio
import json
import sqlite3

import pytest

from gramps_mcp._gramps_sqlite import GrampsSqliteDB
from gramps_mcp.client import GrampsAPIError

# ---------------------------------------------------------------------------
# Minimal schema
# ---------------------------------------------------------------------------

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
CREATE TABLE event (
    handle VARCHAR(50) PRIMARY KEY NOT NULL,
    json_data TEXT, gramps_id TEXT,
    description TEXT, place VARCHAR(50),
    change INTEGER DEFAULT 0, private INTEGER DEFAULT 0
);
CREATE TABLE note (
    handle VARCHAR(50) PRIMARY KEY NOT NULL,
    json_data TEXT, gramps_id TEXT, format INTEGER DEFAULT 0,
    change INTEGER DEFAULT 0, private INTEGER DEFAULT 0
);
CREATE TABLE media (
    handle VARCHAR(50) PRIMARY KEY NOT NULL,
    json_data TEXT, gramps_id TEXT, path TEXT, mime TEXT,
    desc TEXT, checksum TEXT, change INTEGER DEFAULT 0, private INTEGER DEFAULT 0
);
CREATE TABLE metadata (
    setting VARCHAR(50) PRIMARY KEY NOT NULL,
    json_data TEXT, value BLOB
);
"""

_EMPTY_DATE = {
    "_class": "Date", "calendar": 0, "modifier": 0, "quality": 0,
    "dateval": [0, 0, 0, False], "text": "", "sortval": 0, "newyear": 0, "format": None,
}


@pytest.fixture()
def fresh_db():
    """Fresh in-memory GrampsSqliteDB for each test."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    conn.commit()
    db = GrampsSqliteDB(conn=conn, db_path=":memory:", read_only=False)
    return db, conn


def _insert_event(conn, handle: str, gramps_id: str, type_value: int) -> None:
    data = {
        "_class": "Event", "handle": handle, "gramps_id": gramps_id,
        "type": {"_class": "EventType", "value": type_value, "string": ""},
        "date": _EMPTY_DATE, "description": "", "place": None,
        "citation_list": [], "note_list": [], "media_list": [],
        "attribute_list": [], "tag_list": [], "change": 0, "private": False,
    }
    conn.execute(
        "INSERT INTO event (handle, gramps_id, json_data, change, private) VALUES (?,?,?,0,0)",
        (handle, gramps_id, json.dumps(data)),
    )
    conn.commit()


def _insert_person(conn, handle: str, gramps_id: str,
                   note_list=None, media_list=None, event_ref_list=None,
                   parent_family_list=None) -> None:
    data = {
        "_class": "Person", "handle": handle, "gramps_id": gramps_id,
        "gender": 2,
        "primary_name": {
            "_class": "Name", "first_name": "Test", "suffix": "", "title": "",
            "call": "", "nick": "", "famnick": "", "group_as": "",
            "sort_as": 0, "display_as": 0, "private": False,
            "surname_list": [{"_class": "Surname", "surname": "Person",
                              "prefix": "", "primary": True, "connector": "",
                              "origintype": {"_class": "NameOriginType", "value": 1, "string": ""}}],
            "citation_list": [], "note_list": [],
            "type": {"_class": "NameType", "value": 2, "string": ""},
            "date": _EMPTY_DATE,
        },
        "alternate_names": [], "death_ref_index": -1, "birth_ref_index": -1,
        "event_ref_list": event_ref_list or [],
        "family_list": [], "parent_family_list": parent_family_list or [],
        "media_list": media_list or [], "address_list": [], "attribute_list": [],
        "urls": [], "lds_ord_list": [], "citation_list": [],
        "note_list": note_list or [], "tag_list": [], "person_ref_list": [],
        "change": 0, "private": False,
    }
    conn.execute(
        "INSERT INTO person (handle, gramps_id, json_data, change, private, "
        "birth_ref_index, death_ref_index) VALUES (?,?,?,0,0,-1,-1)",
        (handle, gramps_id, json.dumps(data)),
    )
    conn.commit()


def _insert_family(conn, handle: str, gramps_id: str,
                   child_ref_list=None, note_list=None, media_list=None) -> None:
    data = {
        "_class": "Family", "handle": handle, "gramps_id": gramps_id,
        "father_handle": None, "mother_handle": None,
        "child_ref_list": child_ref_list or [],
        "type": {"_class": "FamilyRelType", "value": 0, "string": ""},
        "event_ref_list": [], "media_list": media_list or [],
        "attribute_list": [], "lds_ord_list": [], "citation_list": [],
        "note_list": note_list or [], "tag_list": [], "change": 0, "private": False,
    }
    conn.execute(
        "INSERT INTO family (handle, gramps_id, json_data, change, private) VALUES (?,?,?,0,0)",
        (handle, gramps_id, json.dumps(data)),
    )
    conn.commit()


def _insert_note(conn, handle: str, gramps_id: str) -> None:
    data = {
        "_class": "Note", "handle": handle, "gramps_id": gramps_id,
        "format": 0, "text": {"_class": "StyledText", "string": "Test note", "tags": []},
        "type": {"_class": "NoteType", "value": 1, "string": "General"},
        "tag_list": [], "change": 0, "private": False,
    }
    conn.execute(
        "INSERT INTO note (handle, gramps_id, json_data, format, change, private) "
        "VALUES (?,?,?,0,0,0)",
        (handle, gramps_id, json.dumps(data)),
    )
    conn.commit()


def _insert_media(conn, handle: str, gramps_id: str) -> None:
    data = {
        "_class": "MediaObject", "handle": handle, "gramps_id": gramps_id,
        "path": "/test.jpg", "mime": "image/jpeg", "desc": "Test", "checksum": "abc",
        "date": _EMPTY_DATE, "note_list": [], "citation_list": [],
        "attribute_list": [], "tag_list": [], "media_list": [], "change": 0, "private": False,
    }
    conn.execute(
        "INSERT INTO media (handle, gramps_id, json_data, path, mime, desc, checksum, change, private) "
        "VALUES (?,?,?,'/test.jpg','image/jpeg','Test','abc',0,0)",
        (handle, gramps_id, json.dumps(data)),
    )
    conn.commit()


# ===========================================================================
# add_event_to_person
# ===========================================================================

class TestAddEventToPerson:
    def test_event_appended_to_person(self, fresh_db):
        from gramps_mcp.tools.link_edit import add_event_to_person_tool

        db, conn = fresh_db
        _insert_person(conn, "h_pe", "I0001")
        _insert_event(conn, "h_ev", "E0001", 42)  # custom event type

        result = asyncio.run(
            add_event_to_person_tool(
                person_handle="h_pe", event_handle="h_ev", role="Witness", db=db
            )
        )
        data = json.loads(result)
        assert data["result"] == "ok"
        assert data["event_ref_count"] == 1

        row = conn.execute(
            "SELECT json_data FROM person WHERE handle = 'h_pe'"
        ).fetchone()
        person_data = json.loads(row["json_data"])
        refs = [e["ref"] for e in person_data.get("event_ref_list", [])]
        assert "h_ev" in refs

    def test_birth_ref_index_set_when_birth_event_added(self, fresh_db):
        from gramps_mcp.tools.link_edit import add_event_to_person_tool

        db, conn = fresh_db
        _insert_person(conn, "h_pe", "I0001")
        _insert_event(conn, "h_ev_birth", "E0001", 12)  # Birth = 12

        asyncio.run(
            add_event_to_person_tool(
                person_handle="h_pe", event_handle="h_ev_birth", role="Primary", db=db
            )
        )

        row = conn.execute(
            "SELECT birth_ref_index FROM person WHERE handle = 'h_pe'"
        ).fetchone()
        assert row["birth_ref_index"] == 0

    def test_no_duplicate_on_double_call(self, fresh_db):
        from gramps_mcp.tools.link_edit import add_event_to_person_tool

        db, conn = fresh_db
        _insert_person(conn, "h_pe", "I0001")
        _insert_event(conn, "h_ev", "E0001", 42)

        asyncio.run(
            add_event_to_person_tool(person_handle="h_pe", event_handle="h_ev", db=db)
        )
        result = asyncio.run(
            add_event_to_person_tool(person_handle="h_pe", event_handle="h_ev", db=db)
        )
        data = json.loads(result)
        assert data["result"] == "no_change"

        row = conn.execute(
            "SELECT json_data FROM person WHERE handle = 'h_pe'"
        ).fetchone()
        person_data = json.loads(row["json_data"])
        assert len(person_data.get("event_ref_list", [])) == 1  # still just one

    def test_error_on_unknown_person_handle(self, fresh_db):
        from gramps_mcp.tools.link_edit import add_event_to_person_tool

        db, conn = fresh_db
        _insert_event(conn, "h_ev", "E0001", 42)

        with pytest.raises(GrampsAPIError, match="Person.*not found"):
            asyncio.run(
                add_event_to_person_tool(
                    person_handle="nonexistent", event_handle="h_ev", db=db
                )
            )

    def test_error_on_unknown_event_handle(self, fresh_db):
        from gramps_mcp.tools.link_edit import add_event_to_person_tool

        db, conn = fresh_db
        _insert_person(conn, "h_pe", "I0001")

        with pytest.raises(GrampsAPIError, match="Event.*not found"):
            asyncio.run(
                add_event_to_person_tool(
                    person_handle="h_pe", event_handle="nonexistent", db=db
                )
            )


# ===========================================================================
# remove_child_from_family
# ===========================================================================

class TestRemoveChildFromFamily:
    def test_child_removed_from_family_child_ref_list(self, fresh_db):
        from gramps_mcp.tools.link_edit import remove_child_from_family_tool

        db, conn = fresh_db
        _insert_person(conn, "h_child", "I0001", parent_family_list=["h_fam"])
        _insert_family(conn, "h_fam", "F0001", child_ref_list=[
            {"_class": "ChildRef", "ref": "h_child",
             "frel": {"_class": "ChildRefType", "value": 1, "string": ""},
             "mrel": {"_class": "ChildRefType", "value": 1, "string": ""},
             "private": False, "citation_list": [], "note_list": []}
        ])

        result = asyncio.run(
            remove_child_from_family_tool(
                family_handle="h_fam", child_handle="h_child", db=db
            )
        )
        data = json.loads(result)
        assert data["result"] == "ok"
        assert data["remaining_children"] == 0

        row = conn.execute(
            "SELECT json_data FROM family WHERE handle = 'h_fam'"
        ).fetchone()
        family_data = json.loads(row["json_data"])
        refs = [cr["ref"] for cr in family_data.get("child_ref_list", [])]
        assert "h_child" not in refs

    def test_parent_family_list_cleaned_from_child(self, fresh_db):
        from gramps_mcp.tools.link_edit import remove_child_from_family_tool

        db, conn = fresh_db
        _insert_person(conn, "h_child", "I0001", parent_family_list=["h_fam"])
        _insert_family(conn, "h_fam", "F0001", child_ref_list=[
            {"_class": "ChildRef", "ref": "h_child",
             "frel": {"_class": "ChildRefType", "value": 1, "string": ""},
             "mrel": {"_class": "ChildRefType", "value": 1, "string": ""},
             "private": False, "citation_list": [], "note_list": []}
        ])

        asyncio.run(
            remove_child_from_family_tool(
                family_handle="h_fam", child_handle="h_child", db=db
            )
        )

        row = conn.execute(
            "SELECT json_data FROM person WHERE handle = 'h_child'"
        ).fetchone()
        child_data = json.loads(row["json_data"])
        assert "h_fam" not in child_data.get("parent_family_list", [])

    def test_error_when_child_not_in_family(self, fresh_db):
        from gramps_mcp.tools.link_edit import remove_child_from_family_tool

        db, conn = fresh_db
        _insert_person(conn, "h_child", "I0001")
        _insert_family(conn, "h_fam", "F0001")  # empty child_ref_list

        with pytest.raises(GrampsAPIError, match="not in family"):
            asyncio.run(
                remove_child_from_family_tool(
                    family_handle="h_fam", child_handle="h_child", db=db
                )
            )


# ===========================================================================
# move_attachment
# ===========================================================================

class TestMoveAttachment:
    def test_note_moved_person_to_person(self, fresh_db):
        from gramps_mcp.tools.link_edit import move_attachment_tool

        db, conn = fresh_db
        _insert_note(conn, "h_note", "N0001")
        _insert_person(conn, "h_src", "I0001", note_list=["h_note"])
        _insert_person(conn, "h_dst", "I0002")

        result = asyncio.run(
            move_attachment_tool(
                attachment_type="note", handle="h_note",
                from_handle="h_src", from_type="person",
                to_handle="h_dst", to_type="person",
                db=db,
            )
        )
        data = json.loads(result)
        assert data["result"] == "ok"

        src_row = conn.execute(
            "SELECT json_data FROM person WHERE handle = 'h_src'"
        ).fetchone()
        dst_row = conn.execute(
            "SELECT json_data FROM person WHERE handle = 'h_dst'"
        ).fetchone()
        src_data = json.loads(src_row["json_data"])
        dst_data = json.loads(dst_row["json_data"])
        assert "h_note" not in src_data.get("note_list", [])
        assert "h_note" in dst_data.get("note_list", [])

    def test_note_moved_person_to_family(self, fresh_db):
        from gramps_mcp.tools.link_edit import move_attachment_tool

        db, conn = fresh_db
        _insert_note(conn, "h_note", "N0001")
        _insert_person(conn, "h_src", "I0001", note_list=["h_note"])
        _insert_family(conn, "h_fam", "F0001")

        asyncio.run(
            move_attachment_tool(
                attachment_type="note", handle="h_note",
                from_handle="h_src", from_type="person",
                to_handle="h_fam", to_type="family",
                db=db,
            )
        )

        src_row = conn.execute(
            "SELECT json_data FROM person WHERE handle = 'h_src'"
        ).fetchone()
        dst_row = conn.execute(
            "SELECT json_data FROM family WHERE handle = 'h_fam'"
        ).fetchone()
        src_data = json.loads(src_row["json_data"])
        dst_data = json.loads(dst_row["json_data"])
        assert "h_note" not in src_data.get("note_list", [])
        assert "h_note" in dst_data.get("note_list", [])

    def test_media_moved_person_to_person(self, fresh_db):
        from gramps_mcp.tools.link_edit import move_attachment_tool

        db, conn = fresh_db
        _insert_media(conn, "h_media", "O0001")
        _insert_person(conn, "h_src", "I0001", media_list=[
            {"_class": "MediaRef", "ref": "h_media", "private": False,
             "rect": None, "citation_list": [], "note_list": [], "attribute_list": []}
        ])
        _insert_person(conn, "h_dst", "I0002")

        asyncio.run(
            move_attachment_tool(
                attachment_type="media", handle="h_media",
                from_handle="h_src", from_type="person",
                to_handle="h_dst", to_type="person",
                db=db,
            )
        )

        src_row = conn.execute(
            "SELECT json_data FROM person WHERE handle = 'h_src'"
        ).fetchone()
        dst_row = conn.execute(
            "SELECT json_data FROM person WHERE handle = 'h_dst'"
        ).fetchone()
        src_data = json.loads(src_row["json_data"])
        dst_data = json.loads(dst_row["json_data"])
        dst_refs = [e.get("ref") if isinstance(e, dict) else e for e in dst_data.get("media_list", [])]
        assert "h_media" in dst_refs
        src_refs = [e.get("ref") if isinstance(e, dict) else e for e in src_data.get("media_list", [])]
        assert "h_media" not in src_refs

    def test_error_when_attachment_not_in_source(self, fresh_db):
        from gramps_mcp.tools.link_edit import move_attachment_tool

        db, conn = fresh_db
        _insert_note(conn, "h_note", "N0001")
        _insert_person(conn, "h_src", "I0001")  # note_list is empty
        _insert_person(conn, "h_dst", "I0002")

        with pytest.raises(GrampsAPIError, match="not in"):
            asyncio.run(
                move_attachment_tool(
                    attachment_type="note", handle="h_note",
                    from_handle="h_src", from_type="person",
                    to_handle="h_dst", to_type="person",
                    db=db,
                )
            )
