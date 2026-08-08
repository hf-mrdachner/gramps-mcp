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
    _year_from_event,
    find_duplicate_persons,
    normalize,
    similarity_score,
)
from gramps_mcp.merge.operations import (
    _events_match,
    _primary_surname,
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

    def test_both_birth_years_missing_penalized_more_than_one_missing(self):
        # Base (gender+surname+given, no birth info at all) = 1 + 3 + 4 = 8
        p = self._make("Hans", "Mueller")
        score_one_missing, _ = similarity_score(p, p, 1830, None)
        score_both_missing, _ = similarity_score(p, p, None, None)
        assert score_both_missing == 6.0  # base(8) - 2 penalty
        assert score_one_missing == 7.0  # base(8) - 1 penalty (unchanged)
        assert score_both_missing < score_one_missing


# ---------------------------------------------------------------------------
# 2b. _year_from_event — free-text date fallback
# ---------------------------------------------------------------------------

class TestYearFromEvent:
    def test_dateval_year_used_when_present(self):
        ev = {"date": {"dateval": [0, 0, 1830, False]}}
        assert _year_from_event(ev) == 1830

    def test_falls_back_to_free_text_date(self):
        # Gramps stores unparsed dates (e.g. "7 Mai 1604") under date.text,
        # not date.string -- dateval stays [0, 0, 0, False] in that case.
        ev = {"date": {"dateval": [0, 0, 0, False], "text": "7 Mai 1604"}}
        assert _year_from_event(ev) == 1604

    def test_no_year_returns_none(self):
        ev = {"date": {"dateval": [0, 0, 0, False], "text": "unknown"}}
        assert _year_from_event(ev) is None

    def test_missing_date_returns_none(self):
        assert _year_from_event({}) is None


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
# 3b. find_duplicate_persons — direct relatives must not be proposed
#
# Regression: a father and son sharing the same given+surname (common in
# historical records) with no recorded birth years used to score 8
# (gender+surname+given) and pass min_score, since the missing-birth-year
# case wasn't penalized and no check excluded already-connected relatives.
# ---------------------------------------------------------------------------

def _make_same_name_family_db(relation: str) -> sqlite3.Connection:
    """
    In-memory DB with two same-named "Hans Schmidt" persons and no birth
    events, connected either as parent/child or as spouses via one family.
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)

    # Same gender on both sides so the pre-existing gender check can't mask
    # whether the new family-relation exclusion actually fired.
    p_a = _person("h_a", "I_A", "Hans", "Schmidt", 1, [])
    p_b = _person("h_b", "I_B", "Hans", "Schmidt", 1, [])

    if relation == "parent_child":
        p_a["family_list"] = ["h_fam"]
        p_b["parent_family_list"] = ["h_fam"]
        fam = {
            "_class": "Family", "handle": "h_fam", "gramps_id": "F0001",
            "father_handle": "h_a", "mother_handle": None,
            "child_ref_list": [_cref("h_b")],
            "event_ref_list": [],
            "citation_list": [], "note_list": [], "media_list": [], "tag_list": [],
            "change": 0, "private": False,
        }
    else:  # spouses
        p_a["family_list"] = ["h_fam"]
        p_b["family_list"] = ["h_fam"]
        fam = {
            "_class": "Family", "handle": "h_fam", "gramps_id": "F0001",
            "father_handle": "h_a", "mother_handle": "h_b",
            "child_ref_list": [],
            "event_ref_list": [],
            "citation_list": [], "note_list": [], "media_list": [], "tag_list": [],
            "change": 0, "private": False,
        }

    conn.execute(
        "INSERT INTO family (handle, gramps_id, json_data, father_handle, mother_handle) "
        "VALUES (?,?,?,?,?)",
        [fam["handle"], fam["gramps_id"], json.dumps(fam),
         fam["father_handle"], fam["mother_handle"]],
    )
    _insert_person(conn, "Hans", "Schmidt", 1, p_a)
    _insert_person(conn, "Hans", "Schmidt", 1, p_b)
    conn.commit()
    return conn


class TestFindDuplicatePersonsExcludesDirectRelatives:
    def test_parent_child_same_name_not_proposed(self):
        conn = _make_same_name_family_db("parent_child")
        candidates = find_duplicate_persons(conn)
        ids = {frozenset([c.winner_id, c.loser_id]) for c in candidates}
        assert frozenset(["I_A", "I_B"]) not in ids

    def test_spouses_same_surname_not_proposed(self):
        conn = _make_same_name_family_db("spouses")
        candidates = find_duplicate_persons(conn)
        ids = {frozenset([c.winner_id, c.loser_id]) for c in candidates}
        assert frozenset(["I_A", "I_B"]) not in ids


# ---------------------------------------------------------------------------
# 3b2. find_duplicate_persons — two-hop relatives (grandparent/grandchild,
# uncle/nephew) must also be excluded, not just direct (one-hop) relatives.
#
# Naming-after-a-relative is a common historical pattern, so a same-named
# grandparent/grandchild pair otherwise scores high enough on name alone
# (no birth years needed) to clear the default min_score - the one-hop
# exclusion above doesn't catch this since they aren't direct neighbors.
# ---------------------------------------------------------------------------

class TestFindDuplicatePersonsExcludesTwoHopRelatives:
    def test_grandparent_grandchild_same_name_not_proposed(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(_SCHEMA)

        p_gp = _person("h_gp", "I_GP", "Hans", "Schmidt", 1, [])
        p_gp["family_list"] = ["h_fam1"]
        p_gm = _person("h_gm", "I_GM", "Anna", "Schmidt", 0, [])
        p_gm["family_list"] = ["h_fam1"]
        p_f = _person("h_f", "I_F", "Peter", "Schmidt", 1, [])
        p_f["parent_family_list"] = ["h_fam1"]
        p_f["family_list"] = ["h_fam2"]
        p_m = _person("h_m", "I_M", "Maria", "Mueller", 0, [])
        p_m["family_list"] = ["h_fam2"]
        p_gc = _person("h_gc", "I_GC", "Hans", "Schmidt", 1, [])
        p_gc["parent_family_list"] = ["h_fam2"]

        fam1 = {
            "_class": "Family", "handle": "h_fam1", "gramps_id": "F0001",
            "father_handle": "h_gp", "mother_handle": "h_gm",
            "child_ref_list": [_cref("h_f")],
            "event_ref_list": [],
            "citation_list": [], "note_list": [], "media_list": [], "tag_list": [],
            "change": 0, "private": False,
        }
        fam2 = {
            "_class": "Family", "handle": "h_fam2", "gramps_id": "F0002",
            "father_handle": "h_f", "mother_handle": "h_m",
            "child_ref_list": [_cref("h_gc")],
            "event_ref_list": [],
            "citation_list": [], "note_list": [], "media_list": [], "tag_list": [],
            "change": 0, "private": False,
        }
        for fam in (fam1, fam2):
            conn.execute(
                "INSERT INTO family (handle, gramps_id, json_data, father_handle, mother_handle) "
                "VALUES (?,?,?,?,?)",
                [fam["handle"], fam["gramps_id"], json.dumps(fam),
                 fam["father_handle"], fam["mother_handle"]],
            )
        _insert_person(conn, "Hans", "Schmidt", 1, p_gp)
        _insert_person(conn, "Anna", "Schmidt", 0, p_gm)
        _insert_person(conn, "Peter", "Schmidt", 1, p_f)
        _insert_person(conn, "Maria", "Mueller", 0, p_m)
        _insert_person(conn, "Hans", "Schmidt", 1, p_gc)
        conn.commit()

        # Default min_score - name-only match (score 6) would otherwise
        # clear it on its own, with no family bonus needed at all.
        candidates = find_duplicate_persons(conn)
        ids = {frozenset([c.winner_id, c.loser_id]) for c in candidates}
        assert frozenset(["I_GP", "I_GC"]) not in ids

    def test_uncle_nephew_same_name_not_proposed(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(_SCHEMA)

        # Grandparents -> two children: uncle (h_u) and nephew's parent (h_p)
        p_gp = _person("h_gp2", "I_GP2", "Karl", "Weber", 1, [])
        p_gp["family_list"] = ["h_famG"]
        p_gm = _person("h_gm2", "I_GM2", "Erna", "Weber", 0, [])
        p_gm["family_list"] = ["h_famG"]
        p_u = _person("h_u", "I_U", "Hans", "Weber", 1, [])
        p_u["parent_family_list"] = ["h_famG"]
        p_p = _person("h_p", "I_P", "Fritz", "Weber", 1, [])
        p_p["parent_family_list"] = ["h_famG"]
        p_p["family_list"] = ["h_famN"]
        p_pm = _person("h_pm", "I_PM", "Klara", "Bauer", 0, [])
        p_pm["family_list"] = ["h_famN"]
        p_n = _person("h_n", "I_N", "Hans", "Weber", 1, [])
        p_n["parent_family_list"] = ["h_famN"]

        famG = {
            "_class": "Family", "handle": "h_famG", "gramps_id": "F0010",
            "father_handle": "h_gp2", "mother_handle": "h_gm2",
            "child_ref_list": [_cref("h_u"), _cref("h_p")],
            "event_ref_list": [],
            "citation_list": [], "note_list": [], "media_list": [], "tag_list": [],
            "change": 0, "private": False,
        }
        famN = {
            "_class": "Family", "handle": "h_famN", "gramps_id": "F0011",
            "father_handle": "h_p", "mother_handle": "h_pm",
            "child_ref_list": [_cref("h_n")],
            "event_ref_list": [],
            "citation_list": [], "note_list": [], "media_list": [], "tag_list": [],
            "change": 0, "private": False,
        }
        for fam in (famG, famN):
            conn.execute(
                "INSERT INTO family (handle, gramps_id, json_data, father_handle, mother_handle) "
                "VALUES (?,?,?,?,?)",
                [fam["handle"], fam["gramps_id"], json.dumps(fam),
                 fam["father_handle"], fam["mother_handle"]],
            )
        _insert_person(conn, "Karl", "Weber", 1, p_gp)
        _insert_person(conn, "Erna", "Weber", 0, p_gm)
        _insert_person(conn, "Hans", "Weber", 1, p_u)
        _insert_person(conn, "Fritz", "Weber", 1, p_p)
        _insert_person(conn, "Klara", "Bauer", 0, p_pm)
        _insert_person(conn, "Hans", "Weber", 1, p_n)
        conn.commit()

        candidates = find_duplicate_persons(conn)
        ids = {frozenset([c.winner_id, c.loser_id]) for c in candidates}
        assert frozenset(["I_U", "I_N"]) not in ids

    def test_three_hop_relatives_still_proposed(self):
        """Boundary check: great-grandparent/great-grandchild (3 hops) is
        deliberately NOT excluded - the cutoff is 2 hops."""
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(_SCHEMA)

        p_ggp = _person("h_ggp", "I_GGP", "Hans", "Schmidt", 1, [])
        p_ggp["family_list"] = ["h_fam1"]
        p_f1 = _person("h_f1", "I_F1", "Peter", "Schmidt", 1, [])
        p_f1["parent_family_list"] = ["h_fam1"]
        p_f1["family_list"] = ["h_fam2"]
        p_f2 = _person("h_f2", "I_F2", "Paul", "Schmidt", 1, [])
        p_f2["parent_family_list"] = ["h_fam2"]
        p_f2["family_list"] = ["h_fam3"]
        p_ggc = _person("h_ggc", "I_GGC", "Hans", "Schmidt", 1, [])
        p_ggc["parent_family_list"] = ["h_fam3"]

        fam1 = {
            "_class": "Family", "handle": "h_fam1", "gramps_id": "F0020",
            "father_handle": "h_ggp", "mother_handle": None,
            "child_ref_list": [_cref("h_f1")], "event_ref_list": [],
            "citation_list": [], "note_list": [], "media_list": [], "tag_list": [],
            "change": 0, "private": False,
        }
        fam2 = {
            "_class": "Family", "handle": "h_fam2", "gramps_id": "F0021",
            "father_handle": "h_f1", "mother_handle": None,
            "child_ref_list": [_cref("h_f2")], "event_ref_list": [],
            "citation_list": [], "note_list": [], "media_list": [], "tag_list": [],
            "change": 0, "private": False,
        }
        fam3 = {
            "_class": "Family", "handle": "h_fam3", "gramps_id": "F0022",
            "father_handle": "h_f2", "mother_handle": None,
            "child_ref_list": [_cref("h_ggc")], "event_ref_list": [],
            "citation_list": [], "note_list": [], "media_list": [], "tag_list": [],
            "change": 0, "private": False,
        }
        for fam in (fam1, fam2, fam3):
            conn.execute(
                "INSERT INTO family (handle, gramps_id, json_data, father_handle, mother_handle) "
                "VALUES (?,?,?,?,?)",
                [fam["handle"], fam["gramps_id"], json.dumps(fam),
                 fam["father_handle"], fam["mother_handle"]],
            )
        _insert_person(conn, "Hans", "Schmidt", 1, p_ggp)
        _insert_person(conn, "Peter", "Schmidt", 1, p_f1)
        _insert_person(conn, "Paul", "Schmidt", 1, p_f2)
        _insert_person(conn, "Hans", "Schmidt", 1, p_ggc)
        conn.commit()

        candidates = find_duplicate_persons(conn)
        ids = {frozenset([c.winner_id, c.loser_id]) for c in candidates}
        assert frozenset(["I_GGP", "I_GGC"]) in ids


# ---------------------------------------------------------------------------
# 3c. find_duplicate_persons — family bonus must be role-aware
#
# A shared neighbor is not by itself duplicate evidence: a grandparent and
# grandchild both touch the connecting parent, but in mismatched roles
# (child vs. parent). Only a *same-role* overlap (both list the identical
# handle as PARENT, as SPOUSE, or as CHILD) is real duplicate evidence -
# e.g. a family entered twice, each copy holding one of the two child
# records that should be merged.
# ---------------------------------------------------------------------------

class TestFamilyBonusIsRoleAware:
    def test_grandparent_grandchild_connector_not_boosted(self):
        """GP and GC share connector F (GP's child, GC's parent) - mismatched
        roles must not count toward the family bonus."""
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(_SCHEMA)

        p_gp = _person("h_gp", "I_GP", "Hans", "Schmidt", 1, [])
        p_gp["family_list"] = ["h_fam1"]
        p_gm = _person("h_gm", "I_GM", "Anna", "Schmidt", 0, [])
        p_gm["family_list"] = ["h_fam1"]
        p_f = _person("h_f", "I_F", "Peter", "Schmidt", 1, [])
        p_f["parent_family_list"] = ["h_fam1"]
        p_f["family_list"] = ["h_fam2"]
        p_m = _person("h_m", "I_M", "Maria", "Mueller", 0, [])
        p_m["family_list"] = ["h_fam2"]
        p_gc = _person("h_gc", "I_GC", "Hans", "Schmidt", 1, [])
        p_gc["parent_family_list"] = ["h_fam2"]

        fam1 = {
            "_class": "Family", "handle": "h_fam1", "gramps_id": "F0001",
            "father_handle": "h_gp", "mother_handle": "h_gm",
            "child_ref_list": [_cref("h_f")],
            "event_ref_list": [],
            "citation_list": [], "note_list": [], "media_list": [], "tag_list": [],
            "change": 0, "private": False,
        }
        fam2 = {
            "_class": "Family", "handle": "h_fam2", "gramps_id": "F0002",
            "father_handle": "h_f", "mother_handle": "h_m",
            "child_ref_list": [_cref("h_gc")],
            "event_ref_list": [],
            "citation_list": [], "note_list": [], "media_list": [], "tag_list": [],
            "change": 0, "private": False,
        }
        for fam in (fam1, fam2):
            conn.execute(
                "INSERT INTO family (handle, gramps_id, json_data, father_handle, mother_handle) "
                "VALUES (?,?,?,?,?)",
                [fam["handle"], fam["gramps_id"], json.dumps(fam),
                 fam["father_handle"], fam["mother_handle"]],
            )
        _insert_person(conn, "Hans", "Schmidt", 1, p_gp)
        _insert_person(conn, "Anna", "Schmidt", 0, p_gm)
        _insert_person(conn, "Peter", "Schmidt", 1, p_f)
        _insert_person(conn, "Maria", "Mueller", 0, p_m)
        _insert_person(conn, "Hans", "Schmidt", 1, p_gc)
        conn.commit()

        # Base score (gender+surname+given match, no birth years) = 6 < 7.
        # A buggy role-blind bonus (+3 for the shared connector F) would
        # push this to 9 and wrongly propose the pair.
        candidates = find_duplicate_persons(conn, min_score=7.0)
        ids = {frozenset([c.winner_id, c.loser_id]) for c in candidates}
        assert frozenset(["I_GP", "I_GC"]) not in ids

    def test_shared_parents_still_boosted(self):
        """Two same-named children of the SAME father+mother, entered under
        two separate family records, share both parents in matching role -
        that is real duplicate-family evidence and must still be boosted."""
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(_SCHEMA)

        p_x = _person("h_x", "I_X", "Karl", "Schmidt", 1, [])
        p_y = _person("h_y", "I_Y", "Erna", "Meier", 0, [])
        p_1 = _person("h_p1", "I_P1", "Hans", "Schmidt", 1, [])
        p_1["parent_family_list"] = ["h_famA"]
        p_2 = _person("h_p2", "I_P2", "Hans", "Schmidt", 1, [])
        p_2["parent_family_list"] = ["h_famB"]

        famA = {
            "_class": "Family", "handle": "h_famA", "gramps_id": "F0001",
            "father_handle": "h_x", "mother_handle": "h_y",
            "child_ref_list": [_cref("h_p1")],
            "event_ref_list": [],
            "citation_list": [], "note_list": [], "media_list": [], "tag_list": [],
            "change": 0, "private": False,
        }
        famB = {
            "_class": "Family", "handle": "h_famB", "gramps_id": "F0002",
            "father_handle": "h_x", "mother_handle": "h_y",
            "child_ref_list": [_cref("h_p2")],
            "event_ref_list": [],
            "citation_list": [], "note_list": [], "media_list": [], "tag_list": [],
            "change": 0, "private": False,
        }
        for fam in (famA, famB):
            conn.execute(
                "INSERT INTO family (handle, gramps_id, json_data, father_handle, mother_handle) "
                "VALUES (?,?,?,?,?)",
                [fam["handle"], fam["gramps_id"], json.dumps(fam),
                 fam["father_handle"], fam["mother_handle"]],
            )
        _insert_person(conn, "Karl", "Schmidt", 1, p_x)
        _insert_person(conn, "Erna", "Meier", 0, p_y)
        _insert_person(conn, "Hans", "Schmidt", 1, p_1)
        _insert_person(conn, "Hans", "Schmidt", 1, p_2)
        conn.commit()

        candidates = find_duplicate_persons(conn, min_score=7.0)
        ids = {frozenset([c.winner_id, c.loser_id]) for c in candidates}
        assert frozenset(["I_P1", "I_P2"]) in ids


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
# 7b. merge_persons — alternate name preservation (regression #36)
#
# The name-preservation check used to compare first_name only, so a loser
# with the same first name but a different surname (e.g. maiden vs. married
# name) was dropped silently: no alternate name added, no mention in the
# reported changes.
# ---------------------------------------------------------------------------

def _make_named_pair_db(
    winner_first: str, winner_surname: str,
    loser_first: str, loser_surname: str,
) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    p_w = _person("h_w", "I_W", winner_first, winner_surname, 1, [])
    p_l = _person("h_l", "I_L", loser_first, loser_surname, 1, [])
    _insert_person(conn, winner_first, winner_surname, 1, p_w)
    _insert_person(conn, loser_first, loser_surname, 1, p_l)
    conn.commit()
    return conn


def _winner_json(conn: sqlite3.Connection) -> dict:
    row = conn.execute(
        "SELECT json_data FROM person WHERE handle='h_w'"
    ).fetchone()
    return json.loads(row["json_data"])


class TestMergePersonsNamePreservation:
    def test_same_first_name_different_surname_preserves_alternate_name(self):
        # Regression #36: "Helen Gibson" (winner) absorbing "Helen Wilke"
        # (loser) must not silently drop the "Wilke" surname.
        conn = _make_named_pair_db("Helen", "Gibson", "Helen", "Wilke")
        changes = merge_persons(conn, "h_w", "h_l", dry_run=False)
        wp = _winner_json(conn)
        alt_surnames = [_primary_surname(n) for n in wp.get("alternate_names", [])]
        assert "Wilke" in alt_surnames
        assert any("alternate name" in c.lower() for c in changes)

    def test_different_first_name_same_surname_preserves_alternate_name(self):
        conn = _make_named_pair_db("Barbara", "Wilke", "Barbara Kay", "Wilke")
        merge_persons(conn, "h_w", "h_l", dry_run=False)
        wp = _winner_json(conn)
        alt_first_names = [n["first_name"] for n in wp.get("alternate_names", [])]
        assert "Barbara Kay" in alt_first_names

    def test_identical_full_name_no_alternate_added(self):
        conn = _make_named_pair_db("Hans", "Mueller", "Hans", "Mueller")
        changes = merge_persons(conn, "h_w", "h_l", dry_run=False)
        wp = _winner_json(conn)
        assert wp.get("alternate_names", []) == []
        assert not any("alternate name" in c.lower() for c in changes)


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
