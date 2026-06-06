"""
Tests for citation link MCP tools:
  - add_citation_to_event
  - remove_citation_from_event

All tests use fresh per-test in-memory SQLite DBs with db= injection.
"""

import asyncio
import json
import sqlite3

import pytest
from mcp.types import TextContent

from gramps_mcp._gramps_sqlite import GrampsSqliteDB
from gramps_mcp.client import GrampsAPIError

_SCHEMA = """
CREATE TABLE event (
    handle VARCHAR(50) PRIMARY KEY NOT NULL,
    json_data TEXT, gramps_id TEXT,
    description TEXT, place VARCHAR(50),
    change INTEGER DEFAULT 0, private INTEGER DEFAULT 0
);
CREATE TABLE citation (
    handle VARCHAR(50) PRIMARY KEY NOT NULL,
    json_data TEXT, gramps_id TEXT,
    source_handle VARCHAR(50), page TEXT,
    change INTEGER DEFAULT 0, private INTEGER DEFAULT 0
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


def _insert_event(conn, handle: str, gramps_id: str, citation_list=None) -> None:
    data = {
        "_class": "Event", "handle": handle, "gramps_id": gramps_id,
        "type": {"_class": "EventType", "value": 42, "string": ""},
        "date": _EMPTY_DATE, "description": "", "place": None,
        "citation_list": citation_list or [],
        "note_list": [], "media_list": [], "attribute_list": [],
        "tag_list": [], "change": 0, "private": False,
    }
    conn.execute(
        "INSERT INTO event (handle, gramps_id, json_data, change, private) VALUES (?,?,?,0,0)",
        (handle, gramps_id, json.dumps(data)),
    )
    conn.commit()


def _insert_citation(conn, handle: str, gramps_id: str) -> None:
    data = {
        "_class": "Citation", "handle": handle, "gramps_id": gramps_id,
        "source_handle": None, "page": "", "confidence": 2,
        "date": _EMPTY_DATE, "note_list": [], "media_list": [],
        "attribute_list": [], "tag_list": [], "change": 0, "private": False,
    }
    conn.execute(
        "INSERT INTO citation (handle, gramps_id, json_data, change, private) VALUES (?,?,?,0,0)",
        (handle, gramps_id, json.dumps(data)),
    )
    conn.commit()


# ===========================================================================
# add_citation_to_event
# ===========================================================================

class TestAddCitationToEvent:
    def test_citation_appended_to_event(self, fresh_db):
        from gramps_mcp.tools.citation_link import add_citation_to_event_tool

        db, conn = fresh_db
        _insert_event(conn, "h_ev", "E0001")
        _insert_citation(conn, "h_cit", "C0001")

        result = asyncio.run(
            add_citation_to_event_tool(
                event_handle="h_ev", citation_handle="h_cit", db=db
            )
        )
        assert isinstance(result, list) and isinstance(result[0], TextContent)
        data = json.loads(result[0].text)
        assert data["result"] == "ok"
        assert data["citation_count"] == 1

        row = conn.execute("SELECT json_data FROM event WHERE handle = 'h_ev'").fetchone()
        event_data = json.loads(row["json_data"])
        assert "h_cit" in event_data["citation_list"]

    def test_idempotent_on_double_add(self, fresh_db):
        from gramps_mcp.tools.citation_link import add_citation_to_event_tool

        db, conn = fresh_db
        _insert_event(conn, "h_ev", "E0001")
        _insert_citation(conn, "h_cit", "C0001")

        asyncio.run(
            add_citation_to_event_tool(event_handle="h_ev", citation_handle="h_cit", db=db)
        )
        result = asyncio.run(
            add_citation_to_event_tool(event_handle="h_ev", citation_handle="h_cit", db=db)
        )
        data = json.loads(result[0].text)
        assert data["result"] == "no_change"

        row = conn.execute("SELECT json_data FROM event WHERE handle = 'h_ev'").fetchone()
        event_data = json.loads(row["json_data"])
        assert event_data["citation_list"].count("h_cit") == 1

    def test_add_via_gramps_ids(self, fresh_db):
        from gramps_mcp.tools.citation_link import add_citation_to_event_tool

        db, conn = fresh_db
        _insert_event(conn, "h_ev", "E0001")
        _insert_citation(conn, "h_cit", "C0001")

        result = asyncio.run(
            add_citation_to_event_tool(
                event_gramps_id="E0001", citation_gramps_id="C0001", db=db
            )
        )
        data = json.loads(result[0].text)
        assert data["result"] == "ok"

    def test_error_on_unknown_event(self, fresh_db):
        from gramps_mcp.tools.citation_link import add_citation_to_event_tool

        db, conn = fresh_db
        _insert_citation(conn, "h_cit", "C0001")

        with pytest.raises(GrampsAPIError, match="not found"):
            asyncio.run(
                add_citation_to_event_tool(
                    event_handle="nonexistent", citation_handle="h_cit", db=db
                )
            )

    def test_error_when_neither_handle_nor_gramps_id_for_event(self, fresh_db):
        from gramps_mcp.tools.citation_link import add_citation_to_event_tool

        db, conn = fresh_db
        _insert_citation(conn, "h_cit", "C0001")

        with pytest.raises(GrampsAPIError, match="handle or gramps_id required"):
            asyncio.run(
                add_citation_to_event_tool(citation_handle="h_cit", db=db)
            )


# ===========================================================================
# remove_citation_from_event
# ===========================================================================

class TestRemoveCitationFromEvent:
    def test_citation_removed_from_event(self, fresh_db):
        from gramps_mcp.tools.citation_link import remove_citation_from_event_tool

        db, conn = fresh_db
        _insert_citation(conn, "h_cit", "C0001")
        _insert_event(conn, "h_ev", "E0001", citation_list=["h_cit"])

        result = asyncio.run(
            remove_citation_from_event_tool(
                event_handle="h_ev", citation_handle="h_cit", db=db
            )
        )
        assert isinstance(result, list) and isinstance(result[0], TextContent)
        data = json.loads(result[0].text)
        assert data["result"] == "ok"
        assert data["citation_count"] == 0

        row = conn.execute("SELECT json_data FROM event WHERE handle = 'h_ev'").fetchone()
        event_data = json.loads(row["json_data"])
        assert "h_cit" not in event_data["citation_list"]

    def test_remove_via_gramps_ids(self, fresh_db):
        from gramps_mcp.tools.citation_link import remove_citation_from_event_tool

        db, conn = fresh_db
        _insert_citation(conn, "h_cit", "C0001")
        _insert_event(conn, "h_ev", "E0001", citation_list=["h_cit"])

        result = asyncio.run(
            remove_citation_from_event_tool(
                event_gramps_id="E0001", citation_gramps_id="C0001", db=db
            )
        )
        data = json.loads(result[0].text)
        assert data["result"] == "ok"

    def test_second_citation_survives_after_remove(self, fresh_db):
        from gramps_mcp.tools.citation_link import remove_citation_from_event_tool

        db, conn = fresh_db
        _insert_citation(conn, "h_cit1", "C0001")
        _insert_citation(conn, "h_cit2", "C0002")
        _insert_event(conn, "h_ev", "E0001", citation_list=["h_cit1", "h_cit2"])

        asyncio.run(
            remove_citation_from_event_tool(
                event_handle="h_ev", citation_handle="h_cit1", db=db
            )
        )

        row = conn.execute("SELECT json_data FROM event WHERE handle = 'h_ev'").fetchone()
        event_data = json.loads(row["json_data"])
        assert "h_cit1" not in event_data["citation_list"]
        assert "h_cit2" in event_data["citation_list"]

    def test_error_when_citation_not_in_event(self, fresh_db):
        from gramps_mcp.tools.citation_link import remove_citation_from_event_tool

        db, conn = fresh_db
        _insert_citation(conn, "h_cit", "C0001")
        _insert_event(conn, "h_ev", "E0001")

        with pytest.raises(GrampsAPIError, match="not in event"):
            asyncio.run(
                remove_citation_from_event_tool(
                    event_handle="h_ev", citation_handle="h_cit", db=db
                )
            )
