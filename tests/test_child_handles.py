"""
Regression tests for _merge_into() child_handles bug.

BUG: child_handles (List[str]) was stored as an unknown key instead of being
mapped to child_ref_list via _denorm_child_ref.

FIX in _gramps_sqlite.py ~line 734:
    elif key == "child_handles" and isinstance(val, list):
        base["child_ref_list"] = [_denorm_child_ref({"ref": h}) for h in val]
"""

import json
import sqlite3

import pytest

from gramps_mcp._gramps_sqlite import GrampsSqliteDB, _merge_into

# ---------------------------------------------------------------------------
# Minimal schema — only the family table is needed here
# ---------------------------------------------------------------------------

_FAMILY_SCHEMA = """
CREATE TABLE family (
    handle VARCHAR(50) PRIMARY KEY NOT NULL,
    json_data TEXT, gramps_id TEXT,
    father_handle VARCHAR(50), mother_handle VARCHAR(50),
    change INTEGER DEFAULT 0, private INTEGER DEFAULT 0
);
CREATE TABLE person (
    handle VARCHAR(50) PRIMARY KEY NOT NULL,
    given_name TEXT, surname TEXT,
    json_data TEXT, gramps_id TEXT, gender INTEGER DEFAULT 2,
    death_ref_index INTEGER DEFAULT -1,
    birth_ref_index INTEGER DEFAULT -1,
    change INTEGER DEFAULT 0, private INTEGER DEFAULT 0
);
CREATE TABLE metadata (
    setting VARCHAR(50) PRIMARY KEY NOT NULL,
    json_data TEXT, value BLOB
);
"""


# ---------------------------------------------------------------------------
# Unit tests: _merge_into()
# ---------------------------------------------------------------------------


class TestMergeIntoChildHandles:
    def test_child_handles_stored_in_child_ref_list(self):
        base = {"_class": "Family", "child_ref_list": []}
        patch = {"child_handles": ["handle_a", "handle_b"]}

        _merge_into(base, patch, "family")

        assert "child_handles" not in base, (
            "_merge_into must not pass child_handles through as a raw key"
        )
        assert "child_ref_list" in base
        assert len(base["child_ref_list"]) == 2

    def test_child_ref_list_has_correct_refs(self):
        base = {"_class": "Family", "child_ref_list": []}
        patch = {"child_handles": ["handle_a", "handle_b"]}

        _merge_into(base, patch, "family")

        refs = [cr["ref"] for cr in base["child_ref_list"]]
        assert refs == ["handle_a", "handle_b"]

    def test_child_ref_entries_have_class_and_private(self):
        base = {"_class": "Family", "child_ref_list": []}
        patch = {"child_handles": ["handle_a", "handle_b"]}

        _merge_into(base, patch, "family")

        for cr in base["child_ref_list"]:
            assert cr["_class"] == "ChildRef"
            assert cr["private"] is False

    def test_empty_child_handles_clears_child_ref_list(self):
        base = {
            "_class": "Family",
            "child_ref_list": [{"_class": "ChildRef", "ref": "old_handle", "private": False}],
        }
        patch = {"child_handles": []}

        _merge_into(base, patch, "family")

        assert base["child_ref_list"] == []


# ---------------------------------------------------------------------------
# Integration test: GrampsSqliteDB.put() with child_handles
# ---------------------------------------------------------------------------


@pytest.fixture()
def fresh_db(tmp_path):
    """GrampsSqliteDB backed by a temporary SQLite file with empty schema."""
    db_file = tmp_path / "sqlite.db"
    conn = sqlite3.connect(str(db_file))
    conn.row_factory = sqlite3.Row
    conn.executescript(_FAMILY_SCHEMA)
    conn.commit()
    db = GrampsSqliteDB(conn=conn, db_path=str(db_file), read_only=False)
    return db, conn


class TestPutFamilyWithChildHandles:
    def test_child_handles_persisted_as_child_ref_list(self, fresh_db):
        db, conn = fresh_db

        # Step 1: create a family (no handle → auto-assigned)
        created = db.put("family", {"father_handle": None, "mother_handle": None})
        handle = created["handle"]
        assert handle

        # Step 2: update the same family with child_handles convenience key
        db.put("family", {"handle": handle, "child_handles": ["fake_handle_1"]})

        # Step 3: read raw json_data back from SQLite
        row = conn.execute(
            "SELECT json_data FROM family WHERE handle = ?", (handle,)
        ).fetchone()
        assert row is not None

        raw = json.loads(row[0])

        assert "child_handles" not in raw, (
            "child_handles must not be stored verbatim in the DB"
        )
        assert "child_ref_list" in raw
        assert len(raw["child_ref_list"]) >= 1
        assert raw["child_ref_list"][0]["ref"] == "fake_handle_1"
        assert raw["child_ref_list"][0]["_class"] == "ChildRef"
