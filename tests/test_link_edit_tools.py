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
from mcp.types import TextContent

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
        assert isinstance(result, list) and isinstance(result[0], TextContent)
        data = json.loads(result[0].text)
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
        data = json.loads(result[0].text)
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
        assert isinstance(result, list) and isinstance(result[0], TextContent)
        data = json.loads(result[0].text)
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
# add_child_to_family
# ===========================================================================

class TestAddChildToFamily:
    def test_child_appended_to_family_child_ref_list(self, fresh_db):
        from gramps_mcp.tools.link_edit import add_child_to_family_tool

        db, conn = fresh_db
        _insert_person(conn, "h_child", "I0001")
        _insert_family(conn, "h_fam", "F0001")

        result = asyncio.run(
            add_child_to_family_tool(
                family_handle="h_fam", child_handle="h_child", db=db
            )
        )
        assert isinstance(result, list) and isinstance(result[0], TextContent)
        data = json.loads(result[0].text)
        assert data["result"] == "ok"
        assert data["child_count"] == 1

        row = conn.execute(
            "SELECT json_data FROM family WHERE handle = 'h_fam'"
        ).fetchone()
        family_data = json.loads(row["json_data"])
        refs = [cr["ref"] for cr in family_data.get("child_ref_list", [])]
        assert "h_child" in refs

    def test_existing_children_not_replaced(self, fresh_db):
        # The core bug this tool fixes: create_family(child_gramps_ids=[...])
        # replaces child_ref_list wholesale, silently dropping every existing
        # child not re-listed. Adding a 2nd child must leave the 1st intact.
        from gramps_mcp.tools.link_edit import add_child_to_family_tool

        db, conn = fresh_db
        _insert_person(conn, "h_child1", "I0001")
        _insert_person(conn, "h_child2", "I0002")
        _insert_family(conn, "h_fam", "F0001", child_ref_list=[
            {"_class": "ChildRef", "ref": "h_child1",
             "frel": {"_class": "ChildRefType", "value": 1, "string": ""},
             "mrel": {"_class": "ChildRefType", "value": 1, "string": ""},
             "private": False, "citation_list": [], "note_list": []}
        ])

        result = asyncio.run(
            add_child_to_family_tool(
                family_handle="h_fam", child_handle="h_child2", db=db
            )
        )
        data = json.loads(result[0].text)
        assert data["child_count"] == 2

        row = conn.execute(
            "SELECT json_data FROM family WHERE handle = 'h_fam'"
        ).fetchone()
        family_data = json.loads(row["json_data"])
        refs = [cr["ref"] for cr in family_data.get("child_ref_list", [])]
        assert "h_child1" in refs
        assert "h_child2" in refs

    def test_parent_family_list_updated_for_child(self, fresh_db):
        from gramps_mcp.tools.link_edit import add_child_to_family_tool

        db, conn = fresh_db
        _insert_person(conn, "h_child", "I0001")
        _insert_family(conn, "h_fam", "F0001")

        asyncio.run(
            add_child_to_family_tool(
                family_handle="h_fam", child_handle="h_child", db=db
            )
        )

        row = conn.execute(
            "SELECT json_data FROM person WHERE handle = 'h_child'"
        ).fetchone()
        child_data = json.loads(row["json_data"])
        assert "h_fam" in child_data.get("parent_family_list", [])

    def test_no_duplicate_on_double_call(self, fresh_db):
        from gramps_mcp.tools.link_edit import add_child_to_family_tool

        db, conn = fresh_db
        _insert_person(conn, "h_child", "I0001")
        _insert_family(conn, "h_fam", "F0001")

        asyncio.run(
            add_child_to_family_tool(
                family_handle="h_fam", child_handle="h_child", db=db
            )
        )
        result = asyncio.run(
            add_child_to_family_tool(
                family_handle="h_fam", child_handle="h_child", db=db
            )
        )
        data = json.loads(result[0].text)
        assert data["result"] == "no_change"

        row = conn.execute(
            "SELECT json_data FROM family WHERE handle = 'h_fam'"
        ).fetchone()
        family_data = json.loads(row["json_data"])
        assert len(family_data.get("child_ref_list", [])) == 1

    def test_error_on_unknown_family_handle(self, fresh_db):
        from gramps_mcp.tools.link_edit import add_child_to_family_tool

        db, conn = fresh_db
        _insert_person(conn, "h_child", "I0001")

        with pytest.raises(GrampsAPIError, match="Family.*not found"):
            asyncio.run(
                add_child_to_family_tool(
                    family_handle="nonexistent", child_handle="h_child", db=db
                )
            )

    def test_error_on_unknown_child_handle(self, fresh_db):
        from gramps_mcp.tools.link_edit import add_child_to_family_tool

        db, conn = fresh_db
        _insert_family(conn, "h_fam", "F0001")

        with pytest.raises(GrampsAPIError, match="not found"):
            asyncio.run(
                add_child_to_family_tool(
                    family_handle="h_fam", child_handle="nonexistent", db=db
                )
            )

    def test_frel_mrel_default_to_birth(self, fresh_db):
        from gramps_mcp.tools.link_edit import add_child_to_family_tool

        db, conn = fresh_db
        _insert_person(conn, "h_child", "I0001")
        _insert_family(conn, "h_fam", "F0001")

        asyncio.run(
            add_child_to_family_tool(
                family_handle="h_fam", child_handle="h_child", db=db
            )
        )

        row = conn.execute(
            "SELECT json_data FROM family WHERE handle = 'h_fam'"
        ).fetchone()
        family_data = json.loads(row["json_data"])
        cref = family_data["child_ref_list"][0]
        # "Birth" is a mapped ChildRefType, not a custom one, so _denorm_type
        # stores it as value=1 with an empty string (string is only populated
        # for custom/unmapped values) — assert the actual encoding, not a
        # string that never gets set.
        assert cref["frel"]["value"] == 1
        assert cref["mrel"]["value"] == 1


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
        assert isinstance(result, list) and isinstance(result[0], TextContent)
        data = json.loads(result[0].text)
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


# ===========================================================================
# add_event_to_person — gramps_id support
# ===========================================================================

class TestAddEventToPersonGrampsId:
    def test_add_via_person_gramps_id(self, fresh_db):
        from gramps_mcp.tools.link_edit import add_event_to_person_tool

        db, conn = fresh_db
        _insert_person(conn, "h_pe", "I0001")
        _insert_event(conn, "h_ev", "E0001", 42)

        result = asyncio.run(
            add_event_to_person_tool(
                person_gramps_id="I0001", event_handle="h_ev", db=db
            )
        )
        data = json.loads(result[0].text)
        assert data["result"] == "ok"
        assert data["event_ref_count"] == 1

    def test_add_via_event_gramps_id(self, fresh_db):
        from gramps_mcp.tools.link_edit import add_event_to_person_tool

        db, conn = fresh_db
        _insert_person(conn, "h_pe", "I0001")
        _insert_event(conn, "h_ev", "E0001", 42)

        result = asyncio.run(
            add_event_to_person_tool(
                person_handle="h_pe", event_gramps_id="E0001", db=db
            )
        )
        data = json.loads(result[0].text)
        assert data["result"] == "ok"

    def test_error_on_unknown_person_gramps_id(self, fresh_db):
        from gramps_mcp.tools.link_edit import add_event_to_person_tool

        db, conn = fresh_db
        _insert_event(conn, "h_ev", "E0001", 42)

        with pytest.raises(GrampsAPIError, match="gramps_id 'I9999' not found"):
            asyncio.run(
                add_event_to_person_tool(
                    person_gramps_id="I9999", event_handle="h_ev", db=db
                )
            )

    def test_error_when_neither_handle_nor_gramps_id(self, fresh_db):
        from gramps_mcp.tools.link_edit import add_event_to_person_tool

        db, conn = fresh_db
        _insert_person(conn, "h_pe", "I0001")
        _insert_event(conn, "h_ev", "E0001", 42)

        with pytest.raises(GrampsAPIError, match="handle or gramps_id required"):
            asyncio.run(
                add_event_to_person_tool(event_handle="h_ev", db=db)
            )


# ===========================================================================
# add_event_to_family
# ===========================================================================

class TestAddEventToFamily:
    def test_event_appended_to_family(self, fresh_db):
        from gramps_mcp.tools.link_edit import add_event_to_family_tool

        db, conn = fresh_db
        _insert_family(conn, "h_fam", "F0001")
        _insert_event(conn, "h_ev", "E0001", 42)

        result = asyncio.run(
            add_event_to_family_tool(
                family_handle="h_fam", event_handle="h_ev", role="Family", db=db
            )
        )
        assert isinstance(result, list) and isinstance(result[0], TextContent)
        data = json.loads(result[0].text)
        assert data["result"] == "ok"
        assert data["event_ref_count"] == 1

        row = conn.execute(
            "SELECT json_data FROM family WHERE handle = 'h_fam'"
        ).fetchone()
        family_data = json.loads(row["json_data"])
        refs = [e["ref"] for e in family_data.get("event_ref_list", [])]
        assert "h_ev" in refs

    def test_no_duplicate_on_double_call(self, fresh_db):
        from gramps_mcp.tools.link_edit import add_event_to_family_tool

        db, conn = fresh_db
        _insert_family(conn, "h_fam", "F0001")
        _insert_event(conn, "h_ev", "E0001", 42)

        asyncio.run(
            add_event_to_family_tool(family_handle="h_fam", event_handle="h_ev", db=db)
        )
        result = asyncio.run(
            add_event_to_family_tool(family_handle="h_fam", event_handle="h_ev", db=db)
        )
        data = json.loads(result[0].text)
        assert data["result"] == "no_change"

        row = conn.execute(
            "SELECT json_data FROM family WHERE handle = 'h_fam'"
        ).fetchone()
        family_data = json.loads(row["json_data"])
        assert len(family_data.get("event_ref_list", [])) == 1

    def test_error_on_unknown_family_handle(self, fresh_db):
        from gramps_mcp.tools.link_edit import add_event_to_family_tool

        db, conn = fresh_db
        _insert_event(conn, "h_ev", "E0001", 42)

        with pytest.raises(GrampsAPIError, match="Family.*not found"):
            asyncio.run(
                add_event_to_family_tool(
                    family_handle="nonexistent", event_handle="h_ev", db=db
                )
            )

    def test_error_on_unknown_event_handle(self, fresh_db):
        from gramps_mcp.tools.link_edit import add_event_to_family_tool

        db, conn = fresh_db
        _insert_family(conn, "h_fam", "F0001")

        with pytest.raises(GrampsAPIError, match="Event.*not found"):
            asyncio.run(
                add_event_to_family_tool(
                    family_handle="h_fam", event_handle="nonexistent", db=db
                )
            )

    def test_add_via_family_gramps_id(self, fresh_db):
        from gramps_mcp.tools.link_edit import add_event_to_family_tool

        db, conn = fresh_db
        _insert_family(conn, "h_fam", "F0001")
        _insert_event(conn, "h_ev", "E0001", 42)

        result = asyncio.run(
            add_event_to_family_tool(
                family_gramps_id="F0001", event_handle="h_ev", db=db
            )
        )
        data = json.loads(result[0].text)
        assert data["result"] == "ok"
        assert data["event_ref_count"] == 1

    def test_add_via_event_gramps_id(self, fresh_db):
        from gramps_mcp.tools.link_edit import add_event_to_family_tool

        db, conn = fresh_db
        _insert_family(conn, "h_fam", "F0001")
        _insert_event(conn, "h_ev", "E0001", 42)

        result = asyncio.run(
            add_event_to_family_tool(
                family_handle="h_fam", event_gramps_id="E0001", db=db
            )
        )
        data = json.loads(result[0].text)
        assert data["result"] == "ok"

    def test_error_when_neither_handle_nor_gramps_id(self, fresh_db):
        from gramps_mcp.tools.link_edit import add_event_to_family_tool

        db, conn = fresh_db
        _insert_event(conn, "h_ev", "E0001", 42)

        with pytest.raises(GrampsAPIError, match="handle or gramps_id required"):
            asyncio.run(
                add_event_to_family_tool(event_handle="h_ev", db=db)
            )

    def test_event_ref_count_excludes_malformed_entries(self, fresh_db):
        from gramps_mcp.tools.link_edit import add_event_to_family_tool

        db, conn = fresh_db
        # Pre-populate family with a malformed EventRef (no "ref" key)
        malformed_family_data = {
            "_class": "Family", "handle": "h_fam", "gramps_id": "F0001",
            "father_handle": None, "mother_handle": None,
            "child_ref_list": [],
            "type": {"_class": "FamilyRelType", "value": 0, "string": ""},
            "event_ref_list": [{"_class": "EventRef", "private": False}],  # missing "ref"
            "media_list": [], "attribute_list": [], "lds_ord_list": [],
            "citation_list": [], "note_list": [], "tag_list": [],
            "change": 0, "private": False,
        }
        conn.execute(
            "INSERT INTO family (handle, gramps_id, json_data, change, private) VALUES (?,?,?,0,0)",
            ("h_fam", "F0001", json.dumps(malformed_family_data)),
        )
        conn.commit()
        _insert_event(conn, "h_ev", "E0001", 42)

        result = asyncio.run(
            add_event_to_family_tool(family_handle="h_fam", event_handle="h_ev", db=db)
        )
        data = json.loads(result[0].text)
        assert data["result"] == "ok"
        # Count should reflect only valid (non-malformed) refs plus the new one
        assert data["event_ref_count"] == 1


# ===========================================================================
# remove_event_from_family
# ===========================================================================

class TestRemoveEventFromFamily:
    def test_event_removed_from_family(self, fresh_db):
        from gramps_mcp.tools.link_edit import remove_event_from_family_tool

        db, conn = fresh_db
        _insert_event(conn, "h_ev", "E0001", 42)
        _insert_family(conn, "h_fam", "F0001")
        # Pre-link the event
        fam_data = json.loads(
            conn.execute("SELECT json_data FROM family WHERE handle='h_fam'").fetchone()[0]
        )
        fam_data["event_ref_list"] = [
            {"_class": "EventRef", "ref": "h_ev",
             "role": {"_class": "EventRoleType", "value": 0, "string": ""},
             "private": False, "note_list": [], "attribute_list": []}
        ]
        conn.execute(
            "UPDATE family SET json_data=? WHERE handle='h_fam'",
            (json.dumps(fam_data),),
        )
        conn.commit()

        result = asyncio.run(
            remove_event_from_family_tool(
                family_handle="h_fam", event_handle="h_ev", db=db
            )
        )
        data = json.loads(result[0].text)
        assert data["result"] == "ok"
        assert data["event_ref_count"] == 0

        row = conn.execute("SELECT json_data FROM family WHERE handle='h_fam'").fetchone()
        family_data = json.loads(row[0])
        refs = [e["ref"] for e in family_data.get("event_ref_list", [])]
        assert "h_ev" not in refs

    def test_remove_via_gramps_ids(self, fresh_db):
        from gramps_mcp.tools.link_edit import remove_event_from_family_tool

        db, conn = fresh_db
        _insert_event(conn, "h_ev", "E0001", 42)
        _insert_family(conn, "h_fam", "F0001")
        fam_data = json.loads(
            conn.execute("SELECT json_data FROM family WHERE handle='h_fam'").fetchone()[0]
        )
        fam_data["event_ref_list"] = [
            {"_class": "EventRef", "ref": "h_ev",
             "role": {"_class": "EventRoleType", "value": 0, "string": ""},
             "private": False, "note_list": [], "attribute_list": []}
        ]
        conn.execute(
            "UPDATE family SET json_data=? WHERE handle='h_fam'",
            (json.dumps(fam_data),),
        )
        conn.commit()

        result = asyncio.run(
            remove_event_from_family_tool(
                family_gramps_id="F0001", event_gramps_id="E0001", db=db
            )
        )
        data = json.loads(result[0].text)
        assert data["result"] == "ok"

    def test_error_when_event_not_linked(self, fresh_db):
        from gramps_mcp.tools.link_edit import remove_event_from_family_tool

        db, conn = fresh_db
        _insert_event(conn, "h_ev", "E0001", 42)
        _insert_family(conn, "h_fam", "F0001")

        with pytest.raises(GrampsAPIError, match="not linked"):
            asyncio.run(
                remove_event_from_family_tool(
                    family_handle="h_fam", event_handle="h_ev", db=db
                )
            )

    def test_error_on_unknown_family_handle(self, fresh_db):
        from gramps_mcp.tools.link_edit import remove_event_from_family_tool

        db, conn = fresh_db
        _insert_event(conn, "h_ev", "E0001", 42)

        with pytest.raises(GrampsAPIError, match="Family.*not found"):
            asyncio.run(
                remove_event_from_family_tool(
                    family_handle="nonexistent", event_handle="h_ev", db=db
                )
            )


# ===========================================================================
# remove_event_from_person
# ===========================================================================

class TestRemoveEventFromPerson:
    def test_event_removed_from_person(self, fresh_db):
        from gramps_mcp.tools.link_edit import remove_event_from_person_tool

        db, conn = fresh_db
        _insert_event(conn, "h_ev", "E0001", 42)
        _insert_person(conn, "h_pe", "I0001", event_ref_list=[
            {"_class": "EventRef", "ref": "h_ev",
             "role": {"_class": "EventRoleType", "value": 1, "string": ""},
             "private": False, "note_list": [], "attribute_list": []}
        ])

        result = asyncio.run(
            remove_event_from_person_tool(
                person_handle="h_pe", event_handle="h_ev", db=db
            )
        )
        data = json.loads(result[0].text)
        assert data["result"] == "ok"
        assert data["event_ref_count"] == 0

        row = conn.execute(
            "SELECT json_data FROM person WHERE handle = 'h_pe'"
        ).fetchone()
        person_data = json.loads(row["json_data"])
        refs = [e["ref"] for e in person_data.get("event_ref_list", [])]
        assert "h_ev" not in refs

    def test_birth_ref_index_reset_after_birth_event_removed(self, fresh_db):
        from gramps_mcp.tools.link_edit import remove_event_from_person_tool

        db, conn = fresh_db
        _insert_event(conn, "h_birth", "E0001", 12)  # Birth = 12
        _insert_event(conn, "h_other", "E0002", 42)
        _insert_person(conn, "h_pe", "I0001", event_ref_list=[
            {"_class": "EventRef", "ref": "h_birth",
             "role": {"_class": "EventRoleType", "value": 1, "string": ""},
             "private": False, "note_list": [], "attribute_list": []},
            {"_class": "EventRef", "ref": "h_other",
             "role": {"_class": "EventRoleType", "value": 1, "string": ""},
             "private": False, "note_list": [], "attribute_list": []},
        ])
        conn.execute("UPDATE person SET birth_ref_index = 0 WHERE handle = 'h_pe'")
        conn.commit()

        asyncio.run(
            remove_event_from_person_tool(
                person_handle="h_pe", event_handle="h_birth", db=db
            )
        )

        row = conn.execute(
            "SELECT birth_ref_index FROM person WHERE handle = 'h_pe'"
        ).fetchone()
        assert row["birth_ref_index"] == -1

    def test_remove_via_gramps_ids(self, fresh_db):
        from gramps_mcp.tools.link_edit import remove_event_from_person_tool

        db, conn = fresh_db
        _insert_event(conn, "h_ev", "E0001", 42)
        _insert_person(conn, "h_pe", "I0001", event_ref_list=[
            {"_class": "EventRef", "ref": "h_ev",
             "role": {"_class": "EventRoleType", "value": 1, "string": ""},
             "private": False, "note_list": [], "attribute_list": []}
        ])

        result = asyncio.run(
            remove_event_from_person_tool(
                person_gramps_id="I0001", event_gramps_id="E0001", db=db
            )
        )
        data = json.loads(result[0].text)
        assert data["result"] == "ok"

    def test_error_when_event_not_linked(self, fresh_db):
        from gramps_mcp.tools.link_edit import remove_event_from_person_tool

        db, conn = fresh_db
        _insert_event(conn, "h_ev", "E0001", 42)
        _insert_person(conn, "h_pe", "I0001")

        with pytest.raises(GrampsAPIError, match="not linked"):
            asyncio.run(
                remove_event_from_person_tool(
                    person_handle="h_pe", event_handle="h_ev", db=db
                )
            )


# ===========================================================================
# add_alternate_name_to_person
# ===========================================================================

class TestAddAlternateNameToPerson:
    def test_name_appended_to_person_alternate_names(self, fresh_db):
        from gramps_mcp.tools.link_edit import add_alternate_name_to_person_tool

        db, conn = fresh_db
        _insert_person(conn, "h_pe", "I0001")

        result = asyncio.run(
            add_alternate_name_to_person_tool(
                person_handle="h_pe",
                name={
                    "first_name": "Johanne Auguste Henriette",
                    "surname_list": [{"surname": "Thamm"}],
                    "type": "Also Known As",
                },
                db=db,
            )
        )
        assert isinstance(result, list) and isinstance(result[0], TextContent)
        data = json.loads(result[0].text)
        assert data["result"] == "ok"
        assert data["alternate_name_count"] == 1

        row = conn.execute(
            "SELECT json_data FROM person WHERE handle = 'h_pe'"
        ).fetchone()
        person_data = json.loads(row["json_data"])
        alt_names = person_data.get("alternate_names", [])
        assert len(alt_names) == 1
        assert alt_names[0]["_class"] == "Name"
        assert alt_names[0]["first_name"] == "Johanne Auguste Henriette"
        assert alt_names[0]["surname_list"][0]["surname"] == "Thamm"
        assert alt_names[0]["type"]["value"] == 1  # Also Known As

    def test_primary_name_untouched(self, fresh_db):
        from gramps_mcp.tools.link_edit import add_alternate_name_to_person_tool

        db, conn = fresh_db
        _insert_person(conn, "h_pe", "I0001")

        asyncio.run(
            add_alternate_name_to_person_tool(
                person_handle="h_pe",
                name={"first_name": "Alt", "surname_list": [{"surname": "Name"}]},
                db=db,
            )
        )

        row = conn.execute(
            "SELECT json_data FROM person WHERE handle = 'h_pe'"
        ).fetchone()
        person_data = json.loads(row["json_data"])
        assert person_data["primary_name"]["first_name"] == "Test"

    def test_existing_alternate_names_not_replaced(self, fresh_db):
        from gramps_mcp.tools.link_edit import add_alternate_name_to_person_tool

        db, conn = fresh_db
        _insert_person(conn, "h_pe", "I0001")

        asyncio.run(
            add_alternate_name_to_person_tool(
                person_handle="h_pe",
                name={"first_name": "First", "surname_list": [{"surname": "Alt"}]},
                db=db,
            )
        )
        result = asyncio.run(
            add_alternate_name_to_person_tool(
                person_handle="h_pe",
                name={"first_name": "Second", "surname_list": [{"surname": "Alt"}]},
                db=db,
            )
        )
        data = json.loads(result[0].text)
        assert data["alternate_name_count"] == 2

        row = conn.execute(
            "SELECT json_data FROM person WHERE handle = 'h_pe'"
        ).fetchone()
        person_data = json.loads(row["json_data"])
        first_names = [n["first_name"] for n in person_data.get("alternate_names", [])]
        assert first_names == ["First", "Second"]

    def test_no_duplicate_on_double_call(self, fresh_db):
        from gramps_mcp.tools.link_edit import add_alternate_name_to_person_tool

        db, conn = fresh_db
        _insert_person(conn, "h_pe", "I0001")
        name = {"first_name": "Johanne", "surname_list": [{"surname": "Thamm"}]}

        asyncio.run(
            add_alternate_name_to_person_tool(person_handle="h_pe", name=name, db=db)
        )
        result = asyncio.run(
            add_alternate_name_to_person_tool(person_handle="h_pe", name=name, db=db)
        )
        data = json.loads(result[0].text)
        assert data["result"] == "no_change"

        row = conn.execute(
            "SELECT json_data FROM person WHERE handle = 'h_pe'"
        ).fetchone()
        person_data = json.loads(row["json_data"])
        assert len(person_data.get("alternate_names", [])) == 1

    def test_different_custom_types_not_treated_as_duplicate(self, fresh_db):
        # _denorm_type maps any non-standard type string to the same NameType
        # sentinel (value=0, "Custom"), with the actual label only in `string`.
        # Two different custom types (e.g. "Religious Name" vs "Stage Name")
        # must not collide in the duplicate check just because both have
        # value=0 — that would silently drop the second, distinct name.
        from gramps_mcp.tools.link_edit import add_alternate_name_to_person_tool

        db, conn = fresh_db
        _insert_person(conn, "h_pe", "I0001")

        asyncio.run(
            add_alternate_name_to_person_tool(
                person_handle="h_pe",
                name={
                    "first_name": "Anna",
                    "surname_list": [{"surname": "Muster"}],
                    "type": "Religious Name",
                },
                db=db,
            )
        )
        result = asyncio.run(
            add_alternate_name_to_person_tool(
                person_handle="h_pe",
                name={
                    "first_name": "Anna",
                    "surname_list": [{"surname": "Muster"}],
                    "type": "Stage Name",
                },
                db=db,
            )
        )
        data = json.loads(result[0].text)
        assert data["result"] == "ok"
        assert data["alternate_name_count"] == 2

        row = conn.execute(
            "SELECT json_data FROM person WHERE handle = 'h_pe'"
        ).fetchone()
        person_data = json.loads(row["json_data"])
        type_strings = [
            n["type"]["string"] for n in person_data.get("alternate_names", [])
        ]
        assert type_strings == ["Religious Name", "Stage Name"]

    def test_error_on_unknown_person_handle(self, fresh_db):
        from gramps_mcp.tools.link_edit import add_alternate_name_to_person_tool

        db, conn = fresh_db

        with pytest.raises(GrampsAPIError, match="Person.*not found"):
            asyncio.run(
                add_alternate_name_to_person_tool(
                    person_handle="nonexistent",
                    name={"first_name": "X", "surname_list": [{"surname": "Y"}]},
                    db=db,
                )
            )

    def test_error_on_missing_name(self, fresh_db):
        from gramps_mcp.tools.link_edit import add_alternate_name_to_person_tool

        db, conn = fresh_db
        _insert_person(conn, "h_pe", "I0001")

        with pytest.raises(GrampsAPIError, match="name is required"):
            asyncio.run(
                add_alternate_name_to_person_tool(person_handle="h_pe", db=db)
            )

    def test_add_via_person_gramps_id(self, fresh_db):
        from gramps_mcp.tools.link_edit import add_alternate_name_to_person_tool

        db, conn = fresh_db
        _insert_person(conn, "h_pe", "I0001")

        result = asyncio.run(
            add_alternate_name_to_person_tool(
                person_gramps_id="I0001",
                name={"first_name": "X", "surname_list": [{"surname": "Y"}]},
                db=db,
            )
        )
        data = json.loads(result[0].text)
        assert data["result"] == "ok"

    def test_error_when_neither_handle_nor_gramps_id(self, fresh_db):
        from gramps_mcp.tools.link_edit import add_alternate_name_to_person_tool

        db, conn = fresh_db
        _insert_person(conn, "h_pe", "I0001")

        with pytest.raises(GrampsAPIError, match="handle or gramps_id required"):
            asyncio.run(
                add_alternate_name_to_person_tool(
                    name={"first_name": "X", "surname_list": [{"surname": "Y"}]},
                    db=db,
                )
            )
