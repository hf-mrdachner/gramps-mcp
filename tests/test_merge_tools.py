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
Integration tests for the duplicate-person merge module.

Uses an in-memory SQLite DB with the real Gramps schema (same structure
as conftest_sqlite.py) populated with controlled duplicate test data.

Tests cover:
  - detect.py  — normalize, similarity_score, find_duplicate_persons
  - operations.py — merge_persons (dry-run + real), event dedup, split_person
"""

import json
import sqlite3

import pytest

from gramps_mcp.merge.detect import (
    DuplicateCandidate,
    find_duplicate_persons,
    normalize,
    similarity_score,
)
from gramps_mcp.merge.operations import (
    _events_match,
    load_backups,
    merge_persons,
    split_person,
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
CREATE TABLE reference (
    obj_handle VARCHAR(50), obj_class TEXT,
    ref_handle VARCHAR(50), ref_class TEXT
);
"""


# ---------------------------------------------------------------------------
# Test-data builders
# ---------------------------------------------------------------------------

def _name(first: str, surname: str, suffix: str = "") -> dict:
    return {
        "_class": "Name",
        "first_name": first,
        "surname_list": [{"_class": "Surname", "surname": surname}],
        "suffix": suffix, "title": "", "call": "", "nick": "",
        "type": {"_class": "NameType", "value": 2, "string": ""},
        "citation_list": [], "note_list": [],
    }


def _eref(handle: str, role_val: int = 1) -> dict:
    return {
        "_class": "EventRef", "ref": handle,
        "role": {"_class": "EventRoleType", "value": role_val, "string": ""},
        "note_list": [], "attribute_list": [], "private": False,
    }


def _event(handle: str, gramps_id: str, ev_type: int, year: int,
           place=None, citations=None) -> dict:
    return {
        "_class": "Event", "handle": handle, "gramps_id": gramps_id,
        "type": {"_class": "EventType", "value": ev_type, "string": ""},
        "date": {"_class": "Date", "dateval": [0, 0, year, False]},
        "place": place,
        "citation_list": citations or [],
        "note_list": [], "media_list": [], "attribute_list": [], "tag_list": [],
        "change": 0, "private": False, "description": "",
    }


def _person(handle: str, gramps_id: str, given: str, surname: str,
            gender: int, erefs: list, birth_idx: int = -1) -> dict:
    return {
        "_class": "Person", "handle": handle, "gramps_id": gramps_id,
        "gender": gender,
        "primary_name": _name(given, surname),
        "alternate_names": [],
        "birth_ref_index": birth_idx, "death_ref_index": -1,
        "event_ref_list": erefs,
        "family_list": [], "parent_family_list": [],
        "media_list": [], "address_list": [], "attribute_list": [], "urls": [],
        "lds_ord_list": [], "citation_list": [], "note_list": [], "tag_list": [],
        "person_ref_list": [], "change": 0, "private": False,
    }


def _insert_event(conn: sqlite3.Connection, data: dict) -> None:
    conn.execute(
        "INSERT INTO event (handle,gramps_id,json_data,description,place,change,private) "
        "VALUES (?,?,?,?,?,?,?)",
        [data["handle"], data["gramps_id"], json.dumps(data),
         data.get("description", ""), data.get("place"), 0, 0],
    )


def _insert_person(conn: sqlite3.Connection, given: str, surname: str,
                   gender: int, data: dict) -> None:
    conn.execute(
        "INSERT INTO person "
        "(handle,gramps_id,json_data,given_name,surname,gender,"
        "birth_ref_index,death_ref_index,change,private) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        [data["handle"], data["gramps_id"], json.dumps(data),
         given, surname, gender,
         data["birth_ref_index"], data["death_ref_index"], 0, 0],
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_merge_db() -> sqlite3.Connection:
    """
    In-memory DB with two duplicate persons (Hans Mueller / Hans Müller)
    sharing a birth event type+date+place, plus one unrelated person.

    Duplicate pair:
      winner  I_W  h_w  — birth h_ev_w (type=12, year=1830, place=p1), citation h_ci_w
      loser   I_L  h_l  — birth h_ev_l (type=12, year=1830, place=p1), citation h_ci_l
    Unrelated:
      I_O  h_o  — birth year 1800 (different)
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)

    ev_w = _event("h_ev_w", "E01", 12, 1830, "p1", ["h_ci_w"])
    ev_l = _event("h_ev_l", "E02", 12, 1830, "p1", ["h_ci_l"])
    ev_o = _event("h_ev_o", "E03", 12, 1800, "p1")
    for ev in [ev_w, ev_l, ev_o]:
        _insert_event(conn, ev)

    p_w = _person("h_w", "I_W", "Hans", "Mueller", 1, [_eref("h_ev_w")], birth_idx=0)
    p_l = _person("h_l", "I_L", "Hans", "Müller",  1, [_eref("h_ev_l")], birth_idx=0)
    p_o = _person("h_o", "I_O", "Fritz", "Mueller", 1, [_eref("h_ev_o")], birth_idx=0)
    _insert_person(conn, "Hans",  "Mueller", 1, p_w)
    _insert_person(conn, "Hans",  "Müller",  1, p_l)
    _insert_person(conn, "Fritz", "Mueller", 1, p_o)

    conn.commit()
    return conn


@pytest.fixture
def db():
    """Fresh in-memory DB for each test."""
    return _make_merge_db()


@pytest.fixture
def merged_db(db, tmp_path, monkeypatch):
    """DB with winner/loser already merged."""
    backup = str(tmp_path / "backups.json")
    merge_persons(db, "h_w", "h_l", dry_run=False, backup_file=backup)
    return db, backup


# ---------------------------------------------------------------------------
# 1. normalize
# ---------------------------------------------------------------------------

class TestNormalize:
    def test_oe(self):
        assert normalize("Görlitz") == normalize("Goerlitz")

    def test_ue(self):
        assert normalize("Müller") == normalize("Mueller")

    def test_ae(self):
        assert normalize("Mäder") == normalize("Maeder")

    def test_ss(self):
        assert normalize("Groß") == normalize("Gross")

    def test_lowercase_and_strip(self):
        assert normalize("  MUELLER  ") == "mueller"

    def test_hyphen_becomes_space(self):
        assert normalize("Hans-Karl") == "hans karl"


# ---------------------------------------------------------------------------
# 2. similarity_score
# ---------------------------------------------------------------------------

class TestSimilarityScore:
    def _make(self, given: str, surname: str, gender: int = 1, suffix: str = "") -> dict:
        return {
            "_given": given, "_surname": surname,
            "_gender": gender, "_suffix": suffix,
        }

    def test_exact_match_scores_high(self):
        p = self._make("Hans", "Mueller")
        score, _ = similarity_score(p, p, 1830, 1830)
        assert score >= 8

    def test_umlaut_variant_matches(self):
        p1 = self._make("Hans", "Mueller")
        p2 = self._make("Hans", "Müller")
        score, _ = similarity_score(p1, p2, 1830, 1830)
        assert score >= 8

    def test_gender_mismatch_excluded(self):
        p1 = self._make("Hans", "Mueller", gender=1)
        p2 = self._make("Hans", "Mueller", gender=0)
        score, _ = similarity_score(p1, p2, 1830, 1830)
        assert score == 0

    def test_surname_mismatch_excluded(self):
        p1 = self._make("Hans", "Mueller")
        p2 = self._make("Hans", "Schmidt")
        score, _ = similarity_score(p1, p2, 1830, 1830)
        assert score == 0

    def test_birth_year_mismatch_excluded(self):
        p = self._make("Hans", "Mueller")
        score, _ = similarity_score(p, p, 1830, 1835)
        assert score == 0

    def test_suffix_mismatch_excluded(self):
        p1 = self._make("Hans", "Mueller", suffix="i")
        p2 = self._make("Hans", "Mueller", suffix="ii")
        score, _ = similarity_score(p1, p2, 1830, 1830)
        assert score == 0


# ---------------------------------------------------------------------------
# 3. find_duplicate_persons
# ---------------------------------------------------------------------------

class TestFindDuplicatePersons:
    def test_umlaut_pair_found(self, db):
        candidates = find_duplicate_persons(db)
        ids = {frozenset([c.winner_id, c.loser_id]) for c in candidates}
        assert frozenset(["I_W", "I_L"]) in ids

    def test_unrelated_not_found(self, db):
        candidates = find_duplicate_persons(db)
        for c in candidates:
            assert "I_O" not in (c.winner_id, c.loser_id)

    def test_candidate_has_reasons(self, db):
        candidates = find_duplicate_persons(db)
        pair = next(c for c in candidates
                    if frozenset([c.winner_id, c.loser_id]) == frozenset(["I_W", "I_L"]))
        assert len(pair.reasons) > 0

    def test_limit_respected(self, db):
        candidates = find_duplicate_persons(db, limit=1)
        assert len(candidates) <= 1


# ---------------------------------------------------------------------------
# 4. _events_match
# ---------------------------------------------------------------------------

class TestEventsMatch:
    def _ev(self, ev_type: int, year: int, place=None) -> dict:
        return _event("x", "E0", ev_type, year, place)

    def test_identical_events_match(self):
        ev = self._ev(12, 1830, "p1")
        assert _events_match(ev, ev) is True

    def test_different_year_no_match(self):
        assert _events_match(self._ev(12, 1830, "p1"), self._ev(12, 1831, "p1")) is False

    def test_different_place_no_match(self):
        assert _events_match(self._ev(12, 1830, "p1"), self._ev(12, 1830, "p2")) is False

    def test_different_type_no_match(self):
        assert _events_match(self._ev(12, 1830, "p1"), self._ev(13, 1830, "p1")) is False

    def test_both_no_place_match(self):
        assert _events_match(self._ev(12, 1830, None), self._ev(12, 1830, None)) is True


# ---------------------------------------------------------------------------
# 5. merge_persons — dry run
# ---------------------------------------------------------------------------

class TestMergePersonsDryRun:
    def test_db_unchanged(self, db):
        before = db.execute("SELECT COUNT(*) FROM person").fetchone()[0]
        merge_persons(db, "h_w", "h_l", dry_run=True)
        assert db.execute("SELECT COUNT(*) FROM person").fetchone()[0] == before

    def test_reports_event_dedup(self, db):
        changes = merge_persons(db, "h_w", "h_l", dry_run=True)
        assert any("dedup" in c.lower() for c in changes)

    def test_no_backup_file_written(self, db, tmp_path):
        backup = str(tmp_path / "bak.json")
        merge_persons(db, "h_w", "h_l", dry_run=True, backup_file=backup)
        import os
        assert not os.path.exists(backup)


# ---------------------------------------------------------------------------
# 6. merge_persons — real write
# ---------------------------------------------------------------------------

class TestMergePersonsWrite:
    def test_loser_deleted(self, merged_db):
        db, _ = merged_db
        assert db.execute("SELECT 1 FROM person WHERE handle='h_l'").fetchone() is None

    def test_winner_survives(self, merged_db):
        db, _ = merged_db
        assert db.execute("SELECT 1 FROM person WHERE handle='h_w'").fetchone() is not None

    def test_duplicate_event_deleted(self, merged_db):
        db, _ = merged_db
        assert db.execute("SELECT 1 FROM event WHERE handle='h_ev_l'").fetchone() is None

    def test_winner_event_preserved(self, merged_db):
        db, _ = merged_db
        assert db.execute("SELECT 1 FROM event WHERE handle='h_ev_w'").fetchone() is not None

    def test_loser_citation_merged_into_winner_event(self, merged_db):
        db, _ = merged_db
        row = db.execute("SELECT json_data FROM event WHERE handle='h_ev_w'").fetchone()
        ev = json.loads(row["json_data"])
        assert "h_ci_w" in ev["citation_list"]
        assert "h_ci_l" in ev["citation_list"]

    def test_backup_written(self, merged_db):
        _, backup = merged_db
        backups = load_backups(backup)
        assert len(backups) == 1
        assert backups[0]["loser_id"] == "I_L"
        assert backups[0]["winner_id"] == "I_W"


# ---------------------------------------------------------------------------
# 7. split_person
# ---------------------------------------------------------------------------

class TestSplitPerson:
    def test_restores_loser(self, merged_db):
        db, backup = merged_db
        split_person(db, "I_W", backup)
        row = db.execute("SELECT gramps_id FROM person WHERE handle='h_l'").fetchone()
        assert row is not None
        assert row["gramps_id"] == "I_L"

    def test_no_backup_is_graceful(self, db, tmp_path):
        backup = str(tmp_path / "nonexistent.json")
        messages = split_person(db, "I_W", backup)
        assert messages  # some message returned

    def test_double_split_idempotent(self, merged_db):
        db, backup = merged_db
        split_person(db, "I_W", backup)
        split_person(db, "I_W", backup)  # second call must not crash


# ---------------------------------------------------------------------------
# 8. merge_persons — family event_ref_list fixup (regression)
#
# When a marriage event appears in BOTH a person's event_ref_list AND the
# family's event_ref_list, merging the loser person used to leave the family
# with a dangling ref to the deleted loser event.
# ---------------------------------------------------------------------------

def _make_marriage_merge_db() -> sqlite3.Connection:
    """
    DB with winner/loser sharing a marriage event that is also in a family.

    Family F1 has father=winner, mother=loser (for simplicity), and the
    marriage event in its event_ref_list.  Both spouses also carry the ref
    in their own event_ref_list.

    Winner person  h_w — event_ref_list: [marriage h_ev_m_w (winner's copy)]
    Loser person   h_l — event_ref_list: [marriage h_ev_m_l (loser's copy, same type/date)]
    Family F1 — event_ref_list: [h_ev_m_l] (points to LOSER event)

    After merge the loser marriage event h_ev_m_l should be gone, the family
    event_ref_list should point to the winner event h_ev_m_w (not be dangling).
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)

    # Marriage event type = 1, year 1855
    ev_mw = _event("h_ev_m_w", "E10", 1, 1855, "p_church")
    ev_ml = _event("h_ev_m_l", "E11", 1, 1855, "p_church")
    _insert_event(conn, ev_mw)
    _insert_event(conn, ev_ml)

    p_w = _person("h_w", "I_W2", "Johann", "Bauer", 1, [_eref("h_ev_m_w")])
    p_l = _person("h_l", "I_L2", "Johann", "Bauer", 1, [_eref("h_ev_m_l")])
    _insert_person(conn, "Johann", "Bauer", 1, p_w)
    _insert_person(conn, "Johann", "Bauer", 1, p_l)

    # Family references the LOSER marriage event
    fam = {
        "_class": "Family", "handle": "h_f1", "gramps_id": "F0001",
        "father_handle": "h_w", "mother_handle": "h_l",
        "child_ref_list": [],
        "event_ref_list": [_eref("h_ev_m_l", role_val=1)],
        "citation_list": [], "note_list": [], "media_list": [], "tag_list": [],
        "change": 0, "private": False,
    }
    conn.execute(
        "INSERT INTO family (handle, gramps_id, json_data, father_handle, mother_handle) "
        "VALUES (?,?,?,?,?)",
        ["h_f1", "F0001", json.dumps(fam), "h_w", "h_l"],
    )
    conn.commit()
    return conn


class TestMergePersonsFamilyEventFixup:
    def test_family_event_ref_replaced_not_dangling(self):
        """Family event_ref_list must not point to the deleted loser event."""
        conn = _make_marriage_merge_db()
        merge_persons(conn, "h_w", "h_l", dry_run=False)

        # Loser event must be gone
        assert conn.execute(
            "SELECT 1 FROM event WHERE handle='h_ev_m_l'"
        ).fetchone() is None

        # Family must no longer reference the deleted event
        row = conn.execute(
            "SELECT json_data FROM family WHERE handle='h_f1'"
        ).fetchone()
        fam = json.loads(row["json_data"])
        refs = [e.get("ref") for e in fam.get("event_ref_list", []) if isinstance(e, dict)]
        assert "h_ev_m_l" not in refs, f"Dangling ref found: {refs}"

    def test_family_event_ref_points_to_winner_event(self):
        """After merge the family should reference the winner event."""
        conn = _make_marriage_merge_db()
        merge_persons(conn, "h_w", "h_l", dry_run=False)

        row = conn.execute(
            "SELECT json_data FROM family WHERE handle='h_f1'"
        ).fetchone()
        fam = json.loads(row["json_data"])
        refs = [e.get("ref") for e in fam.get("event_ref_list", []) if isinstance(e, dict)]
        assert "h_ev_m_w" in refs, f"Winner event ref missing: {refs}"

    def test_winner_event_not_duplicated_in_family(self):
        """If winner event was already in the family, it must not appear twice."""
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(_SCHEMA)

        ev_mw = _event("h_ev_m_w", "E10", 1, 1855, "p_church")
        ev_ml = _event("h_ev_m_l", "E11", 1, 1855, "p_church")
        _insert_event(conn, ev_mw)
        _insert_event(conn, ev_ml)

        p_w = _person("h_w", "I_W2", "Johann", "Bauer", 1, [_eref("h_ev_m_w")])
        p_l = _person("h_l", "I_L2", "Johann", "Bauer", 1, [_eref("h_ev_m_l")])
        _insert_person(conn, "Johann", "Bauer", 1, p_w)
        _insert_person(conn, "Johann", "Bauer", 1, p_l)

        # Family already has BOTH events
        fam = {
            "_class": "Family", "handle": "h_f1", "gramps_id": "F0001",
            "father_handle": "h_w", "mother_handle": "h_l",
            "child_ref_list": [],
            "event_ref_list": [_eref("h_ev_m_w", role_val=1), _eref("h_ev_m_l", role_val=1)],
            "citation_list": [], "note_list": [], "media_list": [], "tag_list": [],
            "change": 0, "private": False,
        }
        conn.execute(
            "INSERT INTO family (handle, gramps_id, json_data, father_handle, mother_handle) "
            "VALUES (?,?,?,?,?)",
            ["h_f1", "F0001", json.dumps(fam), "h_w", "h_l"],
        )
        conn.commit()

        merge_persons(conn, "h_w", "h_l", dry_run=False)

        row = conn.execute(
            "SELECT json_data FROM family WHERE handle='h_f1'"
        ).fetchone()
        fam_after = json.loads(row["json_data"])
        refs = [e.get("ref") for e in fam_after.get("event_ref_list", []) if isinstance(e, dict)]
        assert refs.count("h_ev_m_w") == 1, f"Duplicate winner ref: {refs}"
        assert "h_ev_m_l" not in refs, f"Dangling loser ref: {refs}"


# ---------------------------------------------------------------------------
# 9. merge_persons — family child_ref_list dedupe (regression)
#
# If winner and loser were both (mistakenly) entered as separate children of
# the same family — exactly the scenario merge_persons exists to fix —
# rewriting the loser's ChildRef.ref to the winner handle must not leave two
# ChildRef entries pointing at the winner.
# ---------------------------------------------------------------------------

def _cref(handle: str, citation_list=None, note_list=None) -> dict:
    return {
        "_class": "ChildRef", "ref": handle,
        "frel": {"_class": "ChildRefType", "value": 1, "string": ""},
        "mrel": {"_class": "ChildRefType", "value": 1, "string": ""},
        "private": False,
        "citation_list": citation_list or [],
        "note_list": note_list or [],
    }


class TestMergePersonsChildRefListDedupe:
    def test_winner_not_duplicated_in_child_ref_list(self):
        """If winner and loser both appear as children, merge must dedupe them."""
        conn = _make_merge_db()

        fam = {
            "_class": "Family", "handle": "h_fa", "gramps_id": "F0001",
            "father_handle": "h_o", "mother_handle": None,
            "child_ref_list": [_cref("h_w"), _cref("h_l")],
            "event_ref_list": [],
            "citation_list": [], "note_list": [], "media_list": [], "tag_list": [],
            "change": 0, "private": False,
        }
        conn.execute(
            "INSERT INTO family (handle, gramps_id, json_data, father_handle, mother_handle) "
            "VALUES (?,?,?,?,?)",
            ["h_fa", "F0001", json.dumps(fam), "h_o", None],
        )
        for h in ("h_w", "h_l"):
            row = conn.execute(
                "SELECT json_data FROM person WHERE handle=?", (h,)
            ).fetchone()
            p = json.loads(row["json_data"])
            p["parent_family_list"] = ["h_fa"]
            conn.execute(
                "UPDATE person SET json_data=? WHERE handle=?", (json.dumps(p), h)
            )
        conn.commit()

        merge_persons(conn, "h_w", "h_l", dry_run=False)

        row = conn.execute(
            "SELECT json_data FROM family WHERE handle='h_fa'"
        ).fetchone()
        fam_after = json.loads(row["json_data"])
        refs = [c["ref"] for c in fam_after.get("child_ref_list", [])]
        assert refs.count("h_w") == 1, f"Duplicate winner child ref: {refs}"

    def test_citations_and_notes_preserved_from_discarded_duplicate(self):
        """Citations/notes on the discarded duplicate ChildRef must not be lost."""
        conn = _make_merge_db()

        fam = {
            "_class": "Family", "handle": "h_fa", "gramps_id": "F0001",
            "father_handle": "h_o", "mother_handle": None,
            "child_ref_list": [
                _cref("h_w", citation_list=["h_ci_w"]),
                _cref("h_l", citation_list=["h_ci_l"], note_list=["h_no_l"]),
            ],
            "event_ref_list": [],
            "citation_list": [], "note_list": [], "media_list": [], "tag_list": [],
            "change": 0, "private": False,
        }
        conn.execute(
            "INSERT INTO family (handle, gramps_id, json_data, father_handle, mother_handle) "
            "VALUES (?,?,?,?,?)",
            ["h_fa", "F0001", json.dumps(fam), "h_o", None],
        )
        for h in ("h_w", "h_l"):
            row = conn.execute(
                "SELECT json_data FROM person WHERE handle=?", (h,)
            ).fetchone()
            p = json.loads(row["json_data"])
            p["parent_family_list"] = ["h_fa"]
            conn.execute(
                "UPDATE person SET json_data=? WHERE handle=?", (json.dumps(p), h)
            )
        conn.commit()

        merge_persons(conn, "h_w", "h_l", dry_run=False)

        row = conn.execute(
            "SELECT json_data FROM family WHERE handle='h_fa'"
        ).fetchone()
        fam_after = json.loads(row["json_data"])
        refs = fam_after.get("child_ref_list", [])
        assert len(refs) == 1, f"Expected single deduped entry, got: {refs}"
        kept = refs[0]
        assert kept["ref"] == "h_w"
        assert "h_ci_w" in kept["citation_list"]
        assert "h_ci_l" in kept["citation_list"], (
            f"Citation from discarded duplicate was lost: {kept['citation_list']}"
        )
        assert "h_no_l" in kept["note_list"], (
            f"Note from discarded duplicate was lost: {kept['note_list']}"
        )
