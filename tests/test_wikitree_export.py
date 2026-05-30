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
Integration tests for wikitree_export.prepare_biography.

Uses an in-memory SQLite DB with the real Gramps schema. Tests call
prepare_biography(conn=...) directly — no client singleton involved.
"""

import json
import sqlite3

import pytest

from gramps_mcp.tools.wikitree_export import (
    _format_citation,
    _localise_date,
    prepare_biography,
)

# ---------------------------------------------------------------------------
# Minimal schema (mirrors conftest_sqlite.py)
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
CREATE TABLE place (
    handle VARCHAR(50) PRIMARY KEY NOT NULL,
    json_data TEXT, gramps_id TEXT,
    title TEXT, change INTEGER DEFAULT 0, private INTEGER DEFAULT 0
);
CREATE TABLE source (
    handle VARCHAR(50) PRIMARY KEY NOT NULL,
    json_data TEXT, gramps_id TEXT, title TEXT,
    change INTEGER DEFAULT 0, private INTEGER DEFAULT 0
);
CREATE TABLE citation (
    handle VARCHAR(50) PRIMARY KEY NOT NULL,
    json_data TEXT, gramps_id TEXT, page TEXT,
    source_handle VARCHAR(50),
    change INTEGER DEFAULT 0, private INTEGER DEFAULT 0
);
CREATE TABLE note (
    handle VARCHAR(50) PRIMARY KEY NOT NULL,
    json_data TEXT, gramps_id TEXT,
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

def _make_person(handle, gramps_id, first, surname, gender=1,
                 event_refs=None, birth_idx=-1):
    return {
        "_class": "Person", "handle": handle, "gramps_id": gramps_id,
        "gender": gender,
        "primary_name": {
            "_class": "Name", "first_name": first,
            "surname_list": [{"_class": "Surname", "surname": surname}],
            "suffix": "", "title": "",
        },
        "alternate_names": [],
        "birth_ref_index": birth_idx, "death_ref_index": -1,
        "event_ref_list": event_refs or [],
        "family_list": [], "parent_family_list": [],
        "media_list": [], "citation_list": [], "note_list": [],
        "person_ref_list": [], "tag_list": [], "change": 0, "private": False,
    }


def _make_event(handle, gramps_id, ev_type_val, ev_type_str,
                year=None, month=None, day=None, place=None, citations=None):
    dateval = [day or 0, month or 0, year or 0, False]
    return {
        "_class": "Event", "handle": handle, "gramps_id": gramps_id,
        "type": {"_class": "EventType", "value": ev_type_val, "string": ev_type_str},
        "date": {"_class": "Date", "dateval": dateval, "text": "", "string": ""},
        "place": place or "",
        "citation_list": citations or [],
        "note_list": [], "media_list": [], "attribute_list": [],
        "tag_list": [], "change": 0, "private": False, "description": "",
    }


def _eref(handle, role_val=1):
    return {"_class": "EventRef", "ref": handle,
            "role": {"_class": "EventRoleType", "value": role_val, "string": ""},
            "note_list": [], "attribute_list": [], "private": False}


def _make_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def _insert(conn, table, data):
    cols = ", ".join(data.keys())
    placeholders = ", ".join("?" * len(data))
    conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({placeholders})",
                 list(data.values()))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def plain_db():
    """Person with birth + death events, no citations."""
    conn = _make_db()
    birth = _make_event("h_ev_birth", "E001", 12, "Birth", year=1830, month=4, day=14, place="h_pl_1")
    death = _make_event("h_ev_death", "E002", 13, "Death", year=1900, month=1, day=3)
    person = _make_person("h_pe_1", "I001", "Hans", "Mueller", gender=1,
                          event_refs=[_eref("h_ev_birth"), _eref("h_ev_death")],
                          birth_idx=0)
    place = {
        "_class": "Place", "handle": "h_pl_1", "gramps_id": "P001",
        "name": {"value": "Berlin"}, "title": "Berlin",
    }
    _insert(conn, "event", {"handle": "h_ev_birth", "gramps_id": "E001",
                             "json_data": json.dumps(birth), "description": "", "place": "h_pl_1"})
    _insert(conn, "event", {"handle": "h_ev_death", "gramps_id": "E002",
                             "json_data": json.dumps(death), "description": "", "place": ""})
    _insert(conn, "person", {"handle": "h_pe_1", "gramps_id": "I001",
                              "json_data": json.dumps(person), "given_name": "Hans",
                              "surname": "Mueller", "gender": 1,
                              "birth_ref_index": 0, "death_ref_index": 1})
    _insert(conn, "place", {"handle": "h_pl_1", "gramps_id": "P001",
                             "json_data": json.dumps(place), "title": "Berlin"})
    conn.commit()
    return conn


@pytest.fixture
def cited_db():
    """Person with birth event that has a citation."""
    conn = _make_db()
    source = {"_class": "Source", "handle": "h_so_1", "gramps_id": "S001",
              "title": "Evangelische Kirchenbücher Mecklenburg",
              "note_list": [], "media_list": [], "reporef_list": [],
              "attribute_list": [], "tag_list": [], "change": 0, "private": False}
    citation = {"_class": "Citation", "handle": "h_ci_1", "gramps_id": "C001",
                "page": "Taufen 1830, Nr. 42", "confidence": 2,
                "source_handle": "h_so_1",
                "note_list": [], "media_list": [], "attribute_list": [],
                "tag_list": [], "change": 0, "private": False,
                "date": {"_class": "Date", "dateval": [0, 0, 0, False]}}
    birth = _make_event("h_ev_birth2", "E003", 12, "Birth",
                        year=1845, month=6, day=20, citations=["h_ci_1"])
    person = _make_person("h_pe_2", "I002", "Anna", "Schmidt", gender=0,
                          event_refs=[_eref("h_ev_birth2")], birth_idx=0)
    _insert(conn, "source", {"handle": "h_so_1", "gramps_id": "S001",
                              "json_data": json.dumps(source), "title": "Evangelische Kirchenbücher Mecklenburg"})
    _insert(conn, "citation", {"handle": "h_ci_1", "gramps_id": "C001",
                                "json_data": json.dumps(citation), "page": "Taufen 1830, Nr. 42",
                                "source_handle": "h_so_1"})
    _insert(conn, "event", {"handle": "h_ev_birth2", "gramps_id": "E003",
                             "json_data": json.dumps(birth), "description": "", "place": ""})
    _insert(conn, "person", {"handle": "h_pe_2", "gramps_id": "I002",
                              "json_data": json.dumps(person), "given_name": "Anna",
                              "surname": "Schmidt", "gender": 0,
                              "birth_ref_index": 0, "death_ref_index": -1})
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Unit tests — pure helper functions
# ---------------------------------------------------------------------------

class TestLocaliseDate:
    def test_abbreviated_german_october(self):
        # _localise_date handles 3-letter abbreviations, not full month names
        assert "October" in _localise_date("04 Okt 1933")

    def test_abbreviated_german_jan(self):
        assert "January" in _localise_date("15 Jan 1820")

    def test_english_passthrough(self):
        assert _localise_date("14 January 1820") == "14 January 1820"

    def test_dot_separator_removed(self):
        # "04." → "04 " (leading zero preserved, only dot removed)
        result = _localise_date("04. April 1933")
        assert "." not in result
        assert "04 April 1933" == result

    def test_empty_string(self):
        assert _localise_date("") == ""


class TestFormatCitation:
    def test_archion_returns_page_content(self):
        # Archion sources return the page text directly (contains the URL)
        page = "Taufen 1817 [https://archion.de/p/abc]"
        result = _format_citation("Kirchenbuch via Archion", page)
        assert result == page

    def test_kb_hessen_title_plus_page(self):
        result = _format_citation("Evangelische Kirchenbücher Mecklenburg", "Taufen 1830, Nr. 42")
        assert "Evangelische" in result
        assert "Nr. 42" in result

    def test_standesamt_format(self):
        result = _format_citation("Standesamt Berlin", "263/1990")
        assert "Standesamt" in result
        assert "263/1990" in result

    def test_generic_with_page(self):
        result = _format_citation("Some Source", "p. 12")
        assert "Some Source" in result
        assert "p. 12" in result

    def test_empty_title_returns_empty(self):
        assert _format_citation("", "") == ""

    def test_ancestry_family_trees_not_filtered_by_format_citation(self):
        # Filtering of "Ancestry Family Trees" happens in _citations_for_event,
        # not in _format_citation itself
        result = _format_citation("Ancestry Family Trees", "")
        assert "Ancestry Family Trees" in result


# ---------------------------------------------------------------------------
# Integration tests — prepare_biography with real in-memory DB
# ---------------------------------------------------------------------------

class TestPrepareBiography:
    def test_wikitree_structure(self, plain_db):
        bio = prepare_biography("I001", conn=plain_db)
        assert "== Biography ==" in bio
        assert "== Sources ==" in bio
        assert "<references />" in bio

    def test_birth_sentence_present(self, plain_db):
        bio = prepare_biography("I001", conn=plain_db)
        assert "was born" in bio

    def test_death_sentence_present(self, plain_db):
        bio = prepare_biography("I001", conn=plain_db)
        assert "died" in bio

    def test_birth_place_in_output(self, plain_db):
        bio = prepare_biography("I001", conn=plain_db)
        assert "Berlin" in bio

    def test_birth_date_in_output(self, plain_db):
        bio = prepare_biography("I001", conn=plain_db)
        assert "1830" in bio

    def test_unsourced_when_no_citations(self, plain_db):
        bio = prepare_biography("I001", conn=plain_db)
        assert "{{Unsourced}}" in bio

    def test_no_unsourced_when_cited(self, cited_db):
        bio = prepare_biography("I002", conn=cited_db)
        assert "{{Unsourced}}" not in bio

    def test_ref_tag_present_when_cited(self, cited_db):
        bio = prepare_biography("I002", conn=cited_db)
        assert "<ref>" in bio
        assert "</ref>" in bio

    def test_female_pronoun(self, cited_db):
        """Second sentence for female person uses 'She'."""
        # Add death event to get a second sentence
        death = _make_event("h_ev_death3", "E004", 13, "Death", year=1920)
        cited_db.execute(
            "INSERT INTO event (handle,gramps_id,json_data,description,place) VALUES (?,?,?,?,?)",
            ["h_ev_death3", "E004", json.dumps(death), "", ""]
        )
        person_row = cited_db.execute("SELECT json_data FROM person WHERE gramps_id='I002'").fetchone()
        person = json.loads(person_row["json_data"])
        person["event_ref_list"].append(_eref("h_ev_death3"))
        cited_db.execute("UPDATE person SET json_data=? WHERE gramps_id='I002'",
                         [json.dumps(person)])
        cited_db.commit()
        bio = prepare_biography("I002", conn=cited_db)
        assert "She died" in bio

    def test_person_not_found(self, plain_db):
        from gramps_mcp.client import GrampsAPIError
        with pytest.raises(GrampsAPIError):
            prepare_biography("I_NONEXISTENT", conn=plain_db)

    def test_unsupported_flavor_raises(self, plain_db):
        with pytest.raises(NotImplementedError):
            prepare_biography("I001", flavor="gedcom", conn=plain_db)

    def test_include_events_filter(self, plain_db):
        """When include_events=['Birth'], death sentence should not appear."""
        bio = prepare_biography("I001", include_events=["Birth"], conn=plain_db)
        assert "was born" in bio
        assert "died" not in bio
