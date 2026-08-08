"""
Tests for scripts/repair_missing_fields.py.

Fix: repair_family_secondary_columns backfills stale family.father_handle /
family.mother_handle secondary columns from json_data (the source of truth),
mirroring repair_person_secondary_columns for person.surname/given_name.
"""

import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from repair_missing_fields import repair_family_secondary_columns  # noqa: E402

_SCHEMA = """
CREATE TABLE family (
    handle VARCHAR(50) PRIMARY KEY NOT NULL,
    json_data TEXT, gramps_id TEXT,
    father_handle VARCHAR(50), mother_handle VARCHAR(50),
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
