"""
Tests for delete_object tool — cascade delete with dry-run support.
Uses fresh in-memory SQLite DBs (session-scoped fixture would be mutated).
"""

import json
import sqlite3

import pytest

from gramps_mcp._gramps_sqlite import GrampsSqliteDB

# ---------------------------------------------------------------------------
# Minimal schema (same as conftest_sqlite._SCHEMA)
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE person (
    handle VARCHAR(50) PRIMARY KEY NOT NULL,
    given_name TEXT, surname TEXT,
    json_data TEXT, gramps_id TEXT, gender INTEGER,
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
CREATE TABLE place (
    handle VARCHAR(50) PRIMARY KEY NOT NULL,
    json_data TEXT, gramps_id TEXT,
    title TEXT, long TEXT, lat TEXT, code TEXT, enclosed_by VARCHAR(50),
    change INTEGER DEFAULT 0, private INTEGER DEFAULT 0
);
CREATE TABLE source (
    handle VARCHAR(50) PRIMARY KEY NOT NULL,
    json_data TEXT, gramps_id TEXT, title TEXT, author TEXT,
    pubinfo TEXT, abbrev TEXT, change INTEGER DEFAULT 0, private INTEGER DEFAULT 0
);
CREATE TABLE citation (
    handle VARCHAR(50) PRIMARY KEY NOT NULL,
    json_data TEXT, gramps_id TEXT, page TEXT, confidence INTEGER DEFAULT 2,
    source_handle VARCHAR(50), change INTEGER DEFAULT 0, private INTEGER DEFAULT 0
);
CREATE TABLE note (
    handle VARCHAR(50) PRIMARY KEY NOT NULL,
    json_data TEXT, gramps_id TEXT, format INTEGER DEFAULT 0,
    change INTEGER DEFAULT 0, private INTEGER DEFAULT 0
);
CREATE TABLE media (
    handle VARCHAR(50) PRIMARY KEY NOT NULL,
    json_data TEXT, gramps_id TEXT, path TEXT, mime TEXT, desc TEXT, checksum TEXT,
    change INTEGER DEFAULT 0, private INTEGER DEFAULT 0
);
CREATE TABLE repository (
    handle VARCHAR(50) PRIMARY KEY NOT NULL,
    json_data TEXT, gramps_id TEXT, name TEXT,
    change INTEGER DEFAULT 0, private INTEGER DEFAULT 0
);
CREATE TABLE tag (
    handle VARCHAR(50) PRIMARY KEY NOT NULL,
    json_data TEXT, name TEXT, color VARCHAR(13),
    priority INTEGER DEFAULT 0, change INTEGER DEFAULT 0
);
CREATE TABLE reference (
    obj_handle VARCHAR(50), obj_class TEXT,
    ref_handle VARCHAR(50), ref_class TEXT
);
CREATE TABLE metadata (
    setting VARCHAR(50) PRIMARY KEY NOT NULL,
    json_data TEXT, value BLOB
);
"""


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def _insert(conn, table, handle, gramps_id, data, **extra):
    data = {"handle": handle, "gramps_id": gramps_id, **data}
    cols = ["handle", "gramps_id", "json_data"] + list(extra.keys())
    vals = [handle, gramps_id, json.dumps(data)] + list(extra.values())
    ph = ",".join("?" * len(cols))
    conn.execute(f"INSERT INTO {table} ({','.join(cols)}) VALUES ({ph})", vals)
    conn.commit()


# ---------------------------------------------------------------------------
# Label tests
# ---------------------------------------------------------------------------


class TestLabel:
    def test_person_label(self):
        from gramps_mcp.tools.delete import _label

        conn = _make_conn()
        _insert(
            conn,
            "person",
            "h_john",
            "I0001",
            {
                "_class": "Person",
                "primary_name": {
                    "_class": "Name",
                    "first_name": "John",
                    "surname_list": [{"_class": "Surname", "surname": "Smith"}],
                },
            },
        )
        assert _label(conn, "person", "h_john") == "John Smith (I0001)"

    def test_event_label_with_year(self):
        from gramps_mcp.tools.delete import _label

        conn = _make_conn()
        _insert(
            conn,
            "event",
            "h_ev",
            "E0001",
            {
                "_class": "Event",
                "type": {"_class": "EventType", "value": 12, "string": ""},
                "date": {"_class": "Date", "dateval": [15, 6, 1950, False]},
            },
        )
        label = _label(conn, "event", "h_ev")
        assert "1950" in label
        assert "Birth" in label

    def test_place_label(self):
        from gramps_mcp.tools.delete import _label

        conn = _make_conn()
        _insert(
            conn,
            "place",
            "h_pl",
            "P0001",
            {
                "_class": "Place",
                "title": "Berlin, Germany",
            },
        )
        assert _label(conn, "place", "h_pl") == "Berlin, Germany (P0001)"

    def test_unknown_handle_returns_handle(self):
        from gramps_mcp.tools.delete import _label

        conn = _make_conn()
        assert _label(conn, "person", "nonexistent") == "nonexistent"

    def test_tag_label(self):
        from gramps_mcp.tools.delete import _label

        conn = _make_conn()
        conn.execute(
            "INSERT INTO tag (handle, json_data, name) VALUES (?, ?, ?)",
            ("h_tag", json.dumps({"handle": "h_tag", "name": "Bookmark"}), "Bookmark"),
        )
        conn.commit()
        assert _label(conn, "tag", "h_tag") == "Bookmark"


# ---------------------------------------------------------------------------
# _find_references tests
# ---------------------------------------------------------------------------


class TestFindReferences:
    def test_finds_father_handle_in_family(self):
        from gramps_mcp.tools.delete import _find_references

        conn = _make_conn()
        _insert(
            conn,
            "family",
            "h_fam",
            "F0001",
            {
                "_class": "Family",
                "father_handle": "h_john",
                "mother_handle": None,
                "child_ref_list": [],
            },
        )
        refs = _find_references(conn, "h_john")
        assert any(
            obj_type == "family" and obj_handle == "h_fam"
            for obj_type, obj_handle, _ in refs
        )

    def test_does_not_return_self(self):
        from gramps_mcp.tools.delete import _find_references

        conn = _make_conn()
        _insert(
            conn,
            "person",
            "h_john",
            "I0001",
            {
                "_class": "Person",
                "primary_name": {"first_name": "John", "surname_list": []},
                "event_ref_list": [{"_class": "EventRef", "ref": "h_ev"}],
            },
        )
        refs = _find_references(conn, "h_john")
        assert not any(h == "h_john" for _, h, _ in refs)

    def test_no_references_returns_empty(self):
        from gramps_mcp.tools.delete import _find_references

        conn = _make_conn()
        assert _find_references(conn, "handle_nobody_knows") == []


# ---------------------------------------------------------------------------
# _remove_handle_from_json tests
# ---------------------------------------------------------------------------


class TestRemoveHandleFromJson:
    def test_removes_father_handle(self):
        from gramps_mcp.tools.delete import _remove_handle_from_json

        data = {
            "_class": "Family",
            "father_handle": "h_john",
            "mother_handle": "h_jane",
        }
        updated, affected = _remove_handle_from_json(data, "h_john")
        assert updated["father_handle"] is None
        assert updated["mother_handle"] == "h_jane"
        assert "father_handle" in affected

    def test_removes_from_event_ref_list(self):
        from gramps_mcp.tools.delete import _remove_handle_from_json

        data = {
            "_class": "Person",
            "event_ref_list": [
                {"_class": "EventRef", "ref": "h_ev1"},
                {"_class": "EventRef", "ref": "h_ev2"},
            ],
        }
        updated, affected = _remove_handle_from_json(data, "h_ev1")
        assert len(updated["event_ref_list"]) == 1
        assert updated["event_ref_list"][0]["ref"] == "h_ev2"
        assert "event_ref_list" in affected

    def test_removes_from_plain_note_list(self):
        from gramps_mcp.tools.delete import _remove_handle_from_json

        data = {"note_list": ["h_note1", "h_note2"]}
        updated, affected = _remove_handle_from_json(data, "h_note1")
        assert updated["note_list"] == ["h_note2"]
        assert "note_list" in affected

    def test_no_match_returns_empty_affected(self):
        from gramps_mcp.tools.delete import _remove_handle_from_json

        data = {"father_handle": "someone_else", "note_list": ["h_other"]}
        updated, affected = _remove_handle_from_json(data, "h_john")
        assert affected == []
        assert updated == data

    def test_removes_family_handle_from_person(self):
        from gramps_mcp.tools.delete import _remove_handle_from_json

        data = {
            "_class": "Person",
            "family_list": ["h_fam1", "h_fam2"],
            "parent_family_list": ["h_fam_parent"],
        }
        updated, affected = _remove_handle_from_json(data, "h_fam1")
        assert updated["family_list"] == ["h_fam2"]
        assert "family_list" in affected
        assert updated["parent_family_list"] == ["h_fam_parent"]


# ---------------------------------------------------------------------------
# _cascade_plan tests
# ---------------------------------------------------------------------------


class TestCascadePlan:
    def _setup_person_with_events(self):
        """Person with two events, each owned exclusively by that person."""
        conn = _make_conn()
        _insert(
            conn,
            "event",
            "h_ev_birth",
            "E0001",
            {
                "_class": "Event",
                "type": {"_class": "EventType", "value": 12, "string": ""},
                "date": {"_class": "Date", "dateval": [1, 1, 1950, False]},
            },
        )
        _insert(
            conn,
            "event",
            "h_ev_death",
            "E0002",
            {
                "_class": "Event",
                "type": {"_class": "EventType", "value": 13, "string": ""},
                "date": {"_class": "Date", "dateval": [1, 1, 2020, False]},
            },
        )
        _insert(
            conn,
            "person",
            "h_john",
            "I0001",
            {
                "_class": "Person",
                "primary_name": {
                    "first_name": "John",
                    "surname_list": [{"surname": "Smith"}],
                },
                "event_ref_list": [
                    {"_class": "EventRef", "ref": "h_ev_birth"},
                    {"_class": "EventRef", "ref": "h_ev_death"},
                ],
            },
        )
        return conn

    def test_plan_includes_primary_object(self):
        from gramps_mcp.tools.delete import _cascade_plan

        conn = self._setup_person_with_events()
        plan = _cascade_plan(conn, "person", "h_john")
        handles = [e.handle for e in plan.to_delete]
        assert "h_john" in handles

    def test_plan_cascades_orphaned_events(self):
        from gramps_mcp.tools.delete import _cascade_plan

        conn = self._setup_person_with_events()
        plan = _cascade_plan(conn, "person", "h_john")
        handles = [e.handle for e in plan.to_delete]
        assert "h_ev_birth" in handles
        assert "h_ev_death" in handles

    def test_shared_event_not_cascaded(self):
        """Event referenced by two persons must NOT appear in to_delete."""
        from gramps_mcp.tools.delete import _cascade_plan

        conn = _make_conn()
        _insert(
            conn,
            "event",
            "h_ev_shared",
            "E0001",
            {
                "_class": "Event",
                "type": {"_class": "EventType", "value": 1, "string": ""},
                "date": {"_class": "Date", "dateval": [1, 1, 1975, False]},
            },
        )
        _insert(
            conn,
            "person",
            "h_john",
            "I0001",
            {
                "_class": "Person",
                "primary_name": {"first_name": "John", "surname_list": []},
                "event_ref_list": [{"_class": "EventRef", "ref": "h_ev_shared"}],
            },
        )
        _insert(
            conn,
            "person",
            "h_jane",
            "I0002",
            {
                "_class": "Person",
                "primary_name": {"first_name": "Jane", "surname_list": []},
                "event_ref_list": [{"_class": "EventRef", "ref": "h_ev_shared"}],
            },
        )
        plan = _cascade_plan(conn, "person", "h_john")
        handles = [e.handle for e in plan.to_delete]
        assert "h_ev_shared" not in handles

    def test_plan_records_family_unlink(self):
        """Person who is father in a family: family appears in to_unlink."""
        from gramps_mcp.tools.delete import _cascade_plan

        conn = _make_conn()
        _insert(
            conn,
            "person",
            "h_john",
            "I0001",
            {
                "_class": "Person",
                "primary_name": {"first_name": "John", "surname_list": []},
                "event_ref_list": [],
            },
        )
        _insert(
            conn,
            "family",
            "h_fam",
            "F0001",
            {
                "_class": "Family",
                "father_handle": "h_john",
                "mother_handle": None,
                "child_ref_list": [],
                "event_ref_list": [],
            },
            father_handle="h_john",
        )
        plan = _cascade_plan(conn, "person", "h_john")
        unlink_handles = [e.handle for e in plan.to_unlink]
        assert "h_fam" in unlink_handles

    def test_dry_run_does_not_modify_db(self):
        from gramps_mcp.tools.delete import _cascade_plan

        conn = self._setup_person_with_events()
        _cascade_plan(conn, "person", "h_john")
        row = conn.execute("SELECT handle FROM person WHERE handle='h_john'").fetchone()
        assert row is not None


# ---------------------------------------------------------------------------
# _execute_cascade tests
# ---------------------------------------------------------------------------


class TestExecuteCascade:
    def _setup(self):
        conn = _make_conn()
        _insert(
            conn,
            "event",
            "h_ev_birth",
            "E0001",
            {
                "_class": "Event",
                "type": {"_class": "EventType", "value": 12, "string": ""},
                "date": {"_class": "Date", "dateval": [1, 1, 1950, False]},
            },
        )
        _insert(
            conn,
            "person",
            "h_john",
            "I0001",
            {
                "_class": "Person",
                "primary_name": {"first_name": "John", "surname_list": []},
                "event_ref_list": [{"_class": "EventRef", "ref": "h_ev_birth"}],
            },
        )
        _insert(
            conn,
            "family",
            "h_fam",
            "F0001",
            {
                "_class": "Family",
                "father_handle": "h_john",
                "mother_handle": None,
                "child_ref_list": [],
                "event_ref_list": [],
            },
            father_handle="h_john",
        )
        return conn

    def test_person_row_deleted(self):
        from gramps_mcp.tools.delete import _cascade_plan, _execute_cascade

        conn = self._setup()
        plan = _cascade_plan(conn, "person", "h_john")
        _execute_cascade(conn, plan)
        row = conn.execute("SELECT handle FROM person WHERE handle='h_john'").fetchone()
        assert row is None

    def test_orphaned_event_deleted(self):
        from gramps_mcp.tools.delete import _cascade_plan, _execute_cascade

        conn = self._setup()
        plan = _cascade_plan(conn, "person", "h_john")
        _execute_cascade(conn, plan)
        row = conn.execute(
            "SELECT handle FROM event WHERE handle='h_ev_birth'"
        ).fetchone()
        assert row is None

    def test_family_father_handle_nulled(self):
        from gramps_mcp.tools.delete import _cascade_plan, _execute_cascade

        conn = self._setup()
        plan = _cascade_plan(conn, "person", "h_john")
        _execute_cascade(conn, plan)
        row = conn.execute(
            "SELECT json_data FROM family WHERE handle='h_fam'"
        ).fetchone()
        fam_data = json.loads(row[0])
        assert fam_data.get("father_handle") is None

    def test_rollback_on_error_leaves_db_unchanged(self):
        """If a write fails mid-transaction, no partial changes should persist."""
        from gramps_mcp.tools.delete import _cascade_plan, _execute_cascade

        conn = self._setup()
        plan = _cascade_plan(conn, "person", "h_john")

        # Install a trigger that raises on DELETE from the event table
        conn.execute(
            "CREATE TRIGGER no_event_delete BEFORE DELETE ON event "
            "BEGIN SELECT RAISE(ABORT, 'blocked for test'); END"
        )
        with pytest.raises(Exception):
            _execute_cascade(conn, plan)

        conn.execute("DROP TRIGGER no_event_delete")
        # Person must still exist — transaction was rolled back
        row = conn.execute("SELECT handle FROM person WHERE handle='h_john'").fetchone()
        assert row is not None

    def test_shared_event_survives(self):
        from gramps_mcp.tools.delete import _cascade_plan, _execute_cascade

        conn = _make_conn()
        _insert(
            conn,
            "event",
            "h_ev_shared",
            "E0001",
            {
                "_class": "Event",
                "type": {"_class": "EventType", "value": 1, "string": ""},
                "date": {},
            },
        )
        _insert(
            conn,
            "person",
            "h_john",
            "I0001",
            {
                "_class": "Person",
                "primary_name": {"first_name": "John", "surname_list": []},
                "event_ref_list": [{"_class": "EventRef", "ref": "h_ev_shared"}],
            },
        )
        _insert(
            conn,
            "person",
            "h_jane",
            "I0002",
            {
                "_class": "Person",
                "primary_name": {"first_name": "Jane", "surname_list": []},
                "event_ref_list": [{"_class": "EventRef", "ref": "h_ev_shared"}],
            },
        )
        plan = _cascade_plan(conn, "person", "h_john")
        _execute_cascade(conn, plan)
        row = conn.execute(
            "SELECT handle FROM event WHERE handle='h_ev_shared'"
        ).fetchone()
        assert row is not None


# ---------------------------------------------------------------------------
# Full tool tests
# ---------------------------------------------------------------------------


class TestDeleteObjectTool:
    def _make_db(self):
        conn = _make_conn()
        _insert(
            conn,
            "event",
            "h_ev_birth",
            "E0001",
            {
                "_class": "Event",
                "type": {"_class": "EventType", "value": 12, "string": ""},
                "date": {"_class": "Date", "dateval": [1, 1, 1950, False]},
            },
        )
        _insert(
            conn,
            "person",
            "h_john",
            "I0001",
            {
                "_class": "Person",
                "primary_name": {
                    "first_name": "John",
                    "surname_list": [{"surname": "Smith"}],
                },
                "event_ref_list": [{"_class": "EventRef", "ref": "h_ev_birth"}],
            },
        )
        db = GrampsSqliteDB(conn=conn, db_path=":memory:", read_only=False)
        return db, conn

    def test_dry_run_returns_would_delete(self):
        from gramps_mcp.tools.delete import _delete_object_core as delete_object_tool
        import asyncio

        db, conn = self._make_db()
        result = asyncio.run(
            delete_object_tool("person", "h_john", confirmed=False, db=db)
        )
        data = json.loads(result)
        assert "would_delete" in data
        assert "summary" in data
        handles = [e["handle"] for e in data["would_delete"]]
        assert "h_john" in handles

    def test_dry_run_does_not_delete(self):
        from gramps_mcp.tools.delete import _delete_object_core as delete_object_tool
        import asyncio

        db, conn = self._make_db()
        asyncio.run(delete_object_tool("person", "h_john", confirmed=False, db=db))
        row = conn.execute("SELECT handle FROM person WHERE handle='h_john'").fetchone()
        assert row is not None

    def test_confirmed_deletes_person(self):
        from gramps_mcp.tools.delete import _delete_object_core as delete_object_tool
        import asyncio

        db, conn = self._make_db()
        result = asyncio.run(
            delete_object_tool("person", "h_john", confirmed=True, db=db)
        )
        data = json.loads(result)
        assert "deleted" in data
        row = conn.execute("SELECT handle FROM person WHERE handle='h_john'").fetchone()
        assert row is None

    def test_unknown_handle_raises(self):
        from gramps_mcp.tools.delete import _delete_object_core as delete_object_tool
        from gramps_mcp.client import GrampsAPIError
        import asyncio

        db, _ = self._make_db()
        with pytest.raises(GrampsAPIError, match="not found"):
            asyncio.run(
                delete_object_tool("person", "no_such_handle", confirmed=False, db=db)
            )
