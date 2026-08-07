"""
Tests for SQLite-layer silent fixes:
  Fix A: birth_ref_index / death_ref_index auto-update on put("person", ...)
  Fix B: parent_family_list update after put("family", ...) with child_handles
"""

import json
import sqlite3

import pytest

from gramps_mcp._gramps_sqlite import GrampsSqliteDB
from gramps_mcp.client import GrampsAPIError

# ---------------------------------------------------------------------------
# Minimal schema — only the tables needed for these tests
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
CREATE TABLE metadata (
    setting VARCHAR(50) PRIMARY KEY NOT NULL,
    json_data TEXT, value BLOB
);
"""

# Gramps-JSON event type dicts (raw, as stored in the DB)
_BIRTH_TYPE = {"_class": "EventType", "value": 12, "string": ""}
_DEATH_TYPE = {"_class": "EventType", "value": 13, "string": ""}
_MARRIAGE_TYPE = {"_class": "EventType", "value": 1, "string": ""}

_EMPTY_DATE = {
    "_class": "Date", "calendar": 0, "modifier": 0, "quality": 0,
    "dateval": [0, 0, 0, False], "text": "", "sortval": 0, "newyear": 0, "format": None,
}


def _eref(handle: str, role_val: int = 1) -> dict:
    return {
        "_class": "EventRef", "ref": handle,
        "role": {"_class": "EventRoleType", "value": role_val, "string": ""},
        "note_list": [], "attribute_list": [], "private": False,
    }


@pytest.fixture()
def fresh_db():
    """Fresh in-memory DB with person/event/family tables for each test."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    conn.commit()
    db = GrampsSqliteDB(conn=conn, db_path=":memory:", read_only=False)
    return db, conn


def _insert_event(conn, handle: str, gramps_id: str, event_type: dict) -> None:
    """Insert a minimal event row directly into the DB."""
    data = {
        "_class": "Event", "handle": handle, "gramps_id": gramps_id,
        "type": event_type, "date": _EMPTY_DATE,
        "description": "", "place": None,
        "citation_list": [], "note_list": [], "media_list": [],
        "attribute_list": [], "tag_list": [], "change": 0, "private": False,
    }
    conn.execute(
        "INSERT INTO event (handle, gramps_id, json_data, change, private) "
        "VALUES (?, ?, ?, 0, 0)",
        (handle, gramps_id, json.dumps(data)),
    )
    conn.commit()


# ===========================================================================
# Fix A — birth_ref_index / death_ref_index
# ===========================================================================

class TestBirthDeathRefIndexAutoUpdate:
    def test_birth_ref_index_set_when_birth_event_linked(self, fresh_db):
        db, conn = fresh_db
        _insert_event(conn, "h_ev_birth", "E0001", _BIRTH_TYPE)

        # Create person with a Birth event in event_ref_list
        person = db.put("person", {
            "given_name": "John", "surname": "Smith",
            "event_ref_list": [{"ref": "h_ev_birth", "role": "Primary"}],
        })
        handle = person["handle"]

        row = conn.execute(
            "SELECT birth_ref_index, json_data FROM person WHERE handle = ?", (handle,)
        ).fetchone()
        assert row["birth_ref_index"] == 0
        data = json.loads(row["json_data"])
        assert data["birth_ref_index"] == 0

    def test_death_ref_index_set_when_death_event_linked(self, fresh_db):
        db, conn = fresh_db
        _insert_event(conn, "h_ev_birth", "E0001", _BIRTH_TYPE)
        _insert_event(conn, "h_ev_death", "E0002", _DEATH_TYPE)

        person = db.put("person", {
            "given_name": "John", "surname": "Smith",
            "event_ref_list": [
                {"ref": "h_ev_birth", "role": "Primary"},
                {"ref": "h_ev_death", "role": "Primary"},
            ],
        })
        handle = person["handle"]

        row = conn.execute(
            "SELECT birth_ref_index, death_ref_index FROM person WHERE handle = ?", (handle,)
        ).fetchone()
        assert row["birth_ref_index"] == 0
        assert row["death_ref_index"] == 1

    def test_indices_remain_minus_one_when_no_birth_death_events(self, fresh_db):
        db, conn = fresh_db
        _insert_event(conn, "h_ev_marriage", "E0001", _MARRIAGE_TYPE)

        person = db.put("person", {
            "given_name": "John", "surname": "Smith",
            "event_ref_list": [{"ref": "h_ev_marriage", "role": "Primary"}],
        })
        handle = person["handle"]

        row = conn.execute(
            "SELECT birth_ref_index, death_ref_index FROM person WHERE handle = ?", (handle,)
        ).fetchone()
        assert row["birth_ref_index"] == -1
        assert row["death_ref_index"] == -1

    def test_indices_minus_one_when_no_events(self, fresh_db):
        db, conn = fresh_db
        person = db.put("person", {"given_name": "Jane", "surname": "Doe"})
        handle = person["handle"]

        row = conn.execute(
            "SELECT birth_ref_index, death_ref_index FROM person WHERE handle = ?", (handle,)
        ).fetchone()
        assert row["birth_ref_index"] == -1
        assert row["death_ref_index"] == -1

    def test_first_birth_event_is_used_when_multiple_exist(self, fresh_db):
        db, conn = fresh_db
        _insert_event(conn, "h_ev_birth1", "E0001", _BIRTH_TYPE)
        _insert_event(conn, "h_ev_birth2", "E0002", _BIRTH_TYPE)

        person = db.put("person", {
            "given_name": "John", "surname": "Smith",
            "event_ref_list": [
                {"ref": "h_ev_birth1", "role": "Primary"},
                {"ref": "h_ev_birth2", "role": "Primary"},
            ],
        })
        handle = person["handle"]

        row = conn.execute(
            "SELECT birth_ref_index FROM person WHERE handle = ?", (handle,)
        ).fetchone()
        assert row["birth_ref_index"] == 0  # first birth, not second


# ===========================================================================
# Fix B — parent_family_list  (tests only, implementation in Task 2)
# ===========================================================================

class TestParentFamilyListAutoUpdate:
    def test_parent_family_list_updated_when_family_written_with_child_handles(
        self, fresh_db
    ):
        db, conn = fresh_db

        # Create child person first (no parent family yet)
        child = db.put("person", {"given_name": "James", "surname": "Smith"})
        child_handle = child["handle"]

        # Create family with child_handles
        family = db.put("family", {
            "child_handles": [child_handle],
        })
        family_handle = family["handle"]

        # Check child's parent_family_list was updated
        row = conn.execute(
            "SELECT json_data FROM person WHERE handle = ?", (child_handle,)
        ).fetchone()
        child_data = json.loads(row["json_data"])
        assert family_handle in child_data.get("parent_family_list", [])

        # change timestamp must be bumped (not remain at the initial 0)
        row2 = conn.execute(
            "SELECT change FROM person WHERE handle = ?", (child_handle,)
        ).fetchone()
        assert row2["change"] > 0

    def test_parent_family_list_not_duplicated_on_double_write(self, fresh_db):
        db, conn = fresh_db

        child = db.put("person", {"given_name": "James", "surname": "Smith"})
        child_handle = child["handle"]

        family = db.put("family", {"child_handles": [child_handle]})
        family_handle = family["handle"]

        # Write again — must not duplicate
        db.put("family", {"handle": family_handle, "child_handles": [child_handle]})

        row = conn.execute(
            "SELECT json_data FROM person WHERE handle = ?", (child_handle,)
        ).fetchone()
        child_data = json.loads(row["json_data"])
        pfl = child_data.get("parent_family_list", [])
        assert pfl.count(family_handle) == 1

    def test_no_side_effect_when_patch_has_no_child_fields(self, fresh_db):
        db, conn = fresh_db

        child = db.put("person", {"given_name": "James", "surname": "Smith"})
        child_handle = child["handle"]

        family = db.put("family", {"father_handle": None, "mother_handle": None})
        family_handle = family["handle"]

        # Patch that does NOT include child_handles or child_ref_list
        db.put("family", {
            "handle": family_handle,
            "type": "Married",  # unrelated field
        })

        # child's parent_family_list must NOT have family_handle
        row = conn.execute(
            "SELECT json_data FROM person WHERE handle = ?", (child_handle,)
        ).fetchone()
        child_data = json.loads(row["json_data"])
        assert family_handle not in child_data.get("parent_family_list", [])


# ===========================================================================
# Fix C — family_list of father/mother  (implementation in _gramps_sqlite.py)
# ===========================================================================

class TestFamilyListAutoUpdate:
    def test_family_list_updated_for_father_when_family_written(self, fresh_db):
        db, conn = fresh_db
        father = db.put("person", {"given_name": "John", "surname": "Smith"})
        father_handle = father["handle"]

        family = db.put("family", {"father_handle": father_handle})
        family_handle = family["handle"]

        row = conn.execute(
            "SELECT json_data FROM person WHERE handle = ?", (father_handle,)
        ).fetchone()
        father_data = json.loads(row["json_data"])
        assert family_handle in father_data.get("family_list", [])

    def test_family_list_updated_for_mother_when_family_written(self, fresh_db):
        db, conn = fresh_db
        mother = db.put("person", {"given_name": "Jane", "surname": "Doe"})
        mother_handle = mother["handle"]

        family = db.put("family", {"mother_handle": mother_handle})
        family_handle = family["handle"]

        row = conn.execute(
            "SELECT json_data FROM person WHERE handle = ?", (mother_handle,)
        ).fetchone()
        mother_data = json.loads(row["json_data"])
        assert family_handle in mother_data.get("family_list", [])

    def test_family_list_updated_for_both_parents(self, fresh_db):
        db, conn = fresh_db
        father = db.put("person", {"given_name": "John", "surname": "Smith"})
        mother = db.put("person", {"given_name": "Jane", "surname": "Doe"})
        father_handle = father["handle"]
        mother_handle = mother["handle"]

        family = db.put("family", {
            "father_handle": father_handle,
            "mother_handle": mother_handle,
        })
        family_handle = family["handle"]

        father_row = conn.execute(
            "SELECT json_data FROM person WHERE handle = ?", (father_handle,)
        ).fetchone()
        mother_row = conn.execute(
            "SELECT json_data FROM person WHERE handle = ?", (mother_handle,)
        ).fetchone()
        father_data = json.loads(father_row["json_data"])
        mother_data = json.loads(mother_row["json_data"])
        assert family_handle in father_data.get("family_list", [])
        assert family_handle in mother_data.get("family_list", [])

    def test_family_list_not_duplicated_on_double_write(self, fresh_db):
        db, conn = fresh_db
        father = db.put("person", {"given_name": "John", "surname": "Smith"})
        father_handle = father["handle"]

        family = db.put("family", {"father_handle": father_handle})
        family_handle = family["handle"]

        # Write again — must not duplicate
        db.put("family", {"handle": family_handle, "father_handle": father_handle})

        row = conn.execute(
            "SELECT json_data FROM person WHERE handle = ?", (father_handle,)
        ).fetchone()
        father_data = json.loads(row["json_data"])
        fl = father_data.get("family_list", [])
        assert fl.count(family_handle) == 1

    def test_change_timestamp_bumped_for_parent(self, fresh_db):
        db, conn = fresh_db
        father = db.put("person", {"given_name": "John", "surname": "Smith"})
        father_handle = father["handle"]

        db.put("family", {"father_handle": father_handle})

        row = conn.execute(
            "SELECT change FROM person WHERE handle = ?", (father_handle,)
        ).fetchone()
        assert row["change"] > 0

    def test_no_side_effect_when_family_written_without_parents(self, fresh_db):
        db, conn = fresh_db
        person = db.put("person", {"given_name": "John", "surname": "Smith"})
        person_handle = person["handle"]

        family = db.put("family", {})

        row = conn.execute(
            "SELECT json_data FROM person WHERE handle = ?", (person_handle,)
        ).fetchone()
        person_data = json.loads(row["json_data"])
        assert family["handle"] not in person_data.get("family_list", [])

    def test_family_list_removed_from_old_father_on_reassignment(self, fresh_db):
        db, conn = fresh_db
        old_father = db.put("person", {"given_name": "John", "surname": "Smith"})
        new_father = db.put("person", {"given_name": "Charlie", "surname": "Jones"})
        family = db.put("family", {"father_handle": old_father["handle"]})
        family_handle = family["handle"]

        db.put("family", {"handle": family_handle, "father_handle": new_father["handle"]})

        old_row = conn.execute(
            "SELECT json_data FROM person WHERE handle = ?", (old_father["handle"],)
        ).fetchone()
        old_data = json.loads(old_row["json_data"])
        assert family_handle not in old_data.get("family_list", [])

        new_row = conn.execute(
            "SELECT json_data FROM person WHERE handle = ?", (new_father["handle"],)
        ).fetchone()
        new_data = json.loads(new_row["json_data"])
        assert family_handle in new_data.get("family_list", [])

    def test_family_list_removed_from_old_mother_on_reassignment(self, fresh_db):
        db, conn = fresh_db
        old_mother = db.put("person", {"given_name": "Jane", "surname": "Doe"})
        new_mother = db.put("person", {"given_name": "Alice", "surname": "Roe"})
        family = db.put("family", {"mother_handle": old_mother["handle"]})
        family_handle = family["handle"]

        db.put("family", {"handle": family_handle, "mother_handle": new_mother["handle"]})

        old_row = conn.execute(
            "SELECT json_data FROM person WHERE handle = ?", (old_mother["handle"],)
        ).fetchone()
        old_data = json.loads(old_row["json_data"])
        assert family_handle not in old_data.get("family_list", [])

    def test_family_list_removed_from_old_father_when_cleared(self, fresh_db):
        db, conn = fresh_db
        old_father = db.put("person", {"given_name": "John", "surname": "Smith"})
        family = db.put("family", {"father_handle": old_father["handle"]})
        family_handle = family["handle"]

        db.put("family", {"handle": family_handle, "father_handle": None})

        old_row = conn.execute(
            "SELECT json_data FROM person WHERE handle = ?", (old_father["handle"],)
        ).fetchone()
        old_data = json.loads(old_row["json_data"])
        assert family_handle not in old_data.get("family_list", [])

    def test_family_list_unchanged_when_father_rewritten_same_value(self, fresh_db):
        db, conn = fresh_db
        father = db.put("person", {"given_name": "John", "surname": "Smith"})
        family = db.put("family", {"father_handle": father["handle"]})
        family_handle = family["handle"]

        db.put("family", {"handle": family_handle, "father_handle": father["handle"]})

        row = conn.execute(
            "SELECT json_data FROM person WHERE handle = ?", (father["handle"],)
        ).fetchone()
        data = json.loads(row["json_data"])
        assert data.get("family_list", []).count(family_handle) == 1


# ===========================================================================
# Fix D — child_ref_list of families when person written with parent_family_list
# ===========================================================================

def _child_handles_in_family(conn, family_handle):
    """Helper: extract ref handles from family child_ref_list."""
    row = conn.execute(
        "SELECT json_data FROM family WHERE handle = ?", (family_handle,)
    ).fetchone()
    family_data = json.loads(row["json_data"])
    return [
        cr["ref"] if isinstance(cr, dict) else cr
        for cr in family_data.get("child_ref_list", [])
    ]


class TestChildRefListAutoUpdate:
    def test_child_ref_list_updated_when_person_written_with_parent_family_list(
        self, fresh_db
    ):
        db, conn = fresh_db
        family = db.put("family", {})
        family_handle = family["handle"]

        person = db.put("person", {
            "given_name": "James", "surname": "Smith",
            "parent_family_list": [family_handle],
        })
        person_handle = person["handle"]

        assert person_handle in _child_handles_in_family(conn, family_handle)

    def test_child_ref_has_correct_structure(self, fresh_db):
        db, conn = fresh_db
        family = db.put("family", {})
        family_handle = family["handle"]

        person = db.put("person", {
            "given_name": "James", "surname": "Smith",
            "parent_family_list": [family_handle],
        })
        person_handle = person["handle"]

        row = conn.execute(
            "SELECT json_data FROM family WHERE handle = ?", (family_handle,)
        ).fetchone()
        family_data = json.loads(row["json_data"])
        child_ref = next(
            cr for cr in family_data["child_ref_list"]
            if isinstance(cr, dict) and cr.get("ref") == person_handle
        )
        assert child_ref["_class"] == "ChildRef"
        assert child_ref["private"] is False

    def test_child_ref_not_duplicated_on_double_write(self, fresh_db):
        db, conn = fresh_db
        family = db.put("family", {})
        family_handle = family["handle"]

        person = db.put("person", {
            "given_name": "James", "surname": "Smith",
            "parent_family_list": [family_handle],
        })
        person_handle = person["handle"]

        # Write again — must not duplicate
        db.put("person", {
            "handle": person_handle,
            "parent_family_list": [family_handle],
        })

        child_handles = _child_handles_in_family(conn, family_handle)
        assert child_handles.count(person_handle) == 1

    def test_multiple_families_all_updated(self, fresh_db):
        db, conn = fresh_db
        fam1 = db.put("family", {})
        fam2 = db.put("family", {})

        person = db.put("person", {
            "given_name": "James", "surname": "Smith",
            "parent_family_list": [fam1["handle"], fam2["handle"]],
        })
        person_handle = person["handle"]

        assert person_handle in _child_handles_in_family(conn, fam1["handle"])
        assert person_handle in _child_handles_in_family(conn, fam2["handle"])

    def test_change_timestamp_bumped_for_family(self, fresh_db):
        db, conn = fresh_db
        family = db.put("family", {})
        family_handle = family["handle"]

        db.put("person", {
            "given_name": "James", "surname": "Smith",
            "parent_family_list": [family_handle],
        })

        row = conn.execute(
            "SELECT change FROM family WHERE handle = ?", (family_handle,)
        ).fetchone()
        assert row["change"] > 0

    def test_no_side_effect_when_person_written_without_parent_family_list(
        self, fresh_db
    ):
        db, conn = fresh_db
        family = db.put("family", {})
        family_handle = family["handle"]

        db.put("person", {"given_name": "James", "surname": "Smith"})

        assert _child_handles_in_family(conn, family_handle) == []

    def test_unknown_family_handle_silently_skipped(self, fresh_db):
        db, conn = fresh_db

        # Should not raise even when family doesn't exist
        db.put("person", {
            "given_name": "James", "surname": "Smith",
            "parent_family_list": ["nonexistent_family_handle"],
        })


# ===========================================================================
# Fix E — _write_object / _write_person raise on missing handle
# ===========================================================================

class TestWriteObjectRowcount:
    def test_write_object_raises_when_family_handle_not_found(self, fresh_db):
        from gramps_mcp.tools._sqlite_helpers import _write_object

        db, conn = fresh_db
        with pytest.raises(GrampsAPIError, match="not found"):
            with conn:
                _write_object(conn, "family", "nonexistent_handle", {
                    "_class": "Family", "handle": "nonexistent_handle",
                    "child_ref_list": [], "event_ref_list": [],
                })

    def test_write_person_raises_when_person_handle_not_found(self, fresh_db):
        from gramps_mcp.tools._sqlite_helpers import _write_person

        db, conn = fresh_db
        with pytest.raises(GrampsAPIError, match="not found"):
            with conn:
                _write_person(conn, "nonexistent_handle", {
                    "_class": "Person", "handle": "nonexistent_handle",
                    "event_ref_list": [],
                })
