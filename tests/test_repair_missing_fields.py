"""
Tests for scripts/repair_missing_fields.py.

Fix: repair_family_secondary_columns backfills stale family.father_handle /
family.mother_handle secondary columns from json_data (the source of truth),
mirroring repair_person_secondary_columns for person.surname/given_name.

repair_event_secondary_columns / repair_citation_secondary_columns extend the
same fix to event.place and citation.source_handle, which turned out to have
the identical divergence at much larger scale on the live "Konsolidiert" DB
(event.place: ~63% of rows) — same root cause (_secondaries() previously read
the raw patch instead of the merged JSON), just triggered far more often by
merge/citation-linking tools that only touch one field at a time.
"""

import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from repair_missing_fields import (  # noqa: E402
    repair_citation_secondary_columns,
    repair_event_secondary_columns,
    repair_family_secondary_columns,
)

_SCHEMA = """
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
CREATE TABLE citation (
    handle VARCHAR(50) PRIMARY KEY NOT NULL,
    json_data TEXT, gramps_id TEXT,
    page TEXT, confidence INTEGER DEFAULT 2,
    source_handle VARCHAR(50),
    change INTEGER DEFAULT 0, private INTEGER DEFAULT 0
);
"""


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(_SCHEMA)
    c.commit()
    return c


def _insert_family(conn, handle, gramps_id, father_handle_col, mother_handle_col,
                    json_father_handle, json_mother_handle):
    data = {
        "_class": "Family", "handle": handle, "gramps_id": gramps_id,
        "father_handle": json_father_handle, "mother_handle": json_mother_handle,
        "child_ref_list": [], "event_ref_list": [], "media_list": [],
        "attribute_list": [], "lds_ord_list": [], "citation_list": [],
        "note_list": [], "tag_list": [],
        "type": {"_class": "FamilyRelType", "value": 0, "string": ""},
        "change": 0, "private": False,
    }
    conn.execute(
        "INSERT INTO family "
        "(handle,gramps_id,json_data,father_handle,mother_handle,change,private) "
        "VALUES (?,?,?,?,?,0,0)",
        (handle, gramps_id, json.dumps(data), father_handle_col, mother_handle_col),
    )
    conn.commit()


class TestRepairFamilySecondaryColumns:
    def test_stale_father_handle_column_fixed_from_json(self, conn):
        _insert_family(
            conn, "h_fam1", "F0001",
            father_handle_col="h_stale_father", mother_handle_col=None,
            json_father_handle="h_real_father", json_mother_handle=None,
        )

        scanned, fixed = repair_family_secondary_columns(conn, dry_run=False)

        row = conn.execute(
            "SELECT father_handle FROM family WHERE handle = 'h_fam1'"
        ).fetchone()
        assert row["father_handle"] == "h_real_father"
        assert scanned == 1
        assert fixed == 1

    def test_native_empty_string_column_not_reported_as_fixed(self, conn):
        # Defense in depth: normalize the column side too, matching
        # _repair_single_ref_column's event/citation normalization, in case
        # any writer ever stores "" instead of NULL for an unset parent.
        _insert_family(
            conn, "h_fam1", "F0001",
            father_handle_col="", mother_handle_col=None,
            json_father_handle="", json_mother_handle=None,
        )

        scanned, fixed = repair_family_secondary_columns(conn, dry_run=False)

        assert fixed == 0

    def test_stale_mother_handle_column_fixed_from_json(self, conn):
        _insert_family(
            conn, "h_fam1", "F0001",
            father_handle_col=None, mother_handle_col="h_stale_mother",
            json_father_handle=None, json_mother_handle="h_real_mother",
        )

        repair_family_secondary_columns(conn, dry_run=False)

        row = conn.execute(
            "SELECT mother_handle FROM family WHERE handle = 'h_fam1'"
        ).fetchone()
        assert row["mother_handle"] == "h_real_mother"

    def test_matching_columns_are_not_reported_as_fixed(self, conn):
        _insert_family(
            conn, "h_fam1", "F0001",
            father_handle_col="h_father", mother_handle_col="h_mother",
            json_father_handle="h_father", json_mother_handle="h_mother",
        )

        scanned, fixed = repair_family_secondary_columns(conn, dry_run=False)

        assert scanned == 1
        assert fixed == 0

    def test_dry_run_does_not_write(self, conn):
        _insert_family(
            conn, "h_fam1", "F0001",
            father_handle_col="h_stale_father", mother_handle_col=None,
            json_father_handle="h_real_father", json_mother_handle=None,
        )

        scanned, fixed = repair_family_secondary_columns(conn, dry_run=True)

        row = conn.execute(
            "SELECT father_handle FROM family WHERE handle = 'h_fam1'"
        ).fetchone()
        assert row["father_handle"] == "h_stale_father"
        assert fixed == 1


def _insert_event(conn, handle, gramps_id, place_col, json_place):
    data = {
        "_class": "Event", "handle": handle, "gramps_id": gramps_id,
        "type": {"_class": "EventType", "value": 12, "string": ""},
        "date": {"_class": "Date", "calendar": 0, "modifier": 0, "quality": 0,
                 "dateval": [0, 0, 0, False], "text": "", "sortval": 0,
                 "newyear": 0, "format": None},
        "description": "", "place": json_place,
        "citation_list": [], "note_list": [], "media_list": [],
        "attribute_list": [], "tag_list": [], "change": 0, "private": False,
    }
    conn.execute(
        "INSERT INTO event (handle,gramps_id,json_data,place,change,private) "
        "VALUES (?,?,?,?,0,0)",
        (handle, gramps_id, json.dumps(data), place_col),
    )
    conn.commit()


class TestRepairEventSecondaryColumns:
    def test_stale_place_column_fixed_from_json(self, conn):
        _insert_event(
            conn, "h_ev1", "E0001",
            place_col="h_stale_place", json_place="h_real_place",
        )

        scanned, fixed = repair_event_secondary_columns(conn, dry_run=False)

        row = conn.execute(
            "SELECT place FROM event WHERE handle = 'h_ev1'"
        ).fetchone()
        assert row["place"] == "h_real_place"
        assert scanned == 1
        assert fixed == 1

    def test_matching_place_column_not_reported_as_fixed(self, conn):
        _insert_event(
            conn, "h_ev1", "E0001",
            place_col="h_place", json_place="h_place",
        )

        scanned, fixed = repair_event_secondary_columns(conn, dry_run=False)

        assert fixed == 0

    def test_native_empty_string_column_not_reported_as_fixed(self, conn):
        # Gramps Desktop's own native writes use "" for "no place", never
        # touched by our tool — must not be flagged as divergence.
        _insert_event(
            conn, "h_ev1", "E0001",
            place_col="", json_place="",
        )

        scanned, fixed = repair_event_secondary_columns(conn, dry_run=False)

        assert fixed == 0


def _insert_citation(conn, handle, gramps_id, source_handle_col, json_source_handle):
    data = {
        "_class": "Citation", "handle": handle, "gramps_id": gramps_id,
        "page": "", "confidence": 2, "source_handle": json_source_handle,
        "date": {"_class": "Date", "calendar": 0, "modifier": 0, "quality": 0,
                 "dateval": [0, 0, 0, False], "text": "", "sortval": 0,
                 "newyear": 0, "format": None},
        "note_list": [], "media_list": [], "attribute_list": [], "tag_list": [],
        "change": 0, "private": False,
    }
    conn.execute(
        "INSERT INTO citation "
        "(handle,gramps_id,json_data,source_handle,change,private) "
        "VALUES (?,?,?,?,0,0)",
        (handle, gramps_id, json.dumps(data), source_handle_col),
    )
    conn.commit()


class TestRepairCitationSecondaryColumns:
    def test_stale_source_handle_column_fixed_from_json(self, conn):
        _insert_citation(
            conn, "h_cit1", "C0001",
            source_handle_col="h_stale_source", json_source_handle="h_real_source",
        )

        scanned, fixed = repair_citation_secondary_columns(conn, dry_run=False)

        row = conn.execute(
            "SELECT source_handle FROM citation WHERE handle = 'h_cit1'"
        ).fetchone()
        assert row["source_handle"] == "h_real_source"
        assert scanned == 1
        assert fixed == 1

    def test_dry_run_does_not_write(self, conn):
        _insert_citation(
            conn, "h_cit1", "C0001",
            source_handle_col="h_stale_source", json_source_handle="h_real_source",
        )

        scanned, fixed = repair_citation_secondary_columns(conn, dry_run=True)

        row = conn.execute(
            "SELECT source_handle FROM citation WHERE handle = 'h_cit1'"
        ).fetchone()
        assert row["source_handle"] == "h_stale_source"
        assert fixed == 1
