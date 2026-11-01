"""
Tests that the fictional integration-test database has the variety it claims,
and that no database file can end up in the repository.

Without the variety checks, tests like TestDateVariety in
test_sqlite_integration.py would pass without checking anything.
"""

import json
import re
import sqlite3
import subprocess
from collections import Counter
from pathlib import Path

import pytest
from synthetic_tree import write_synthetic_tree

REPO_ROOT = Path(__file__).parent.parent

# Formats that hold family-tree data. Binary sample images are not covered:
# tests/sample holds a historical register page from the upstream project.
DATA_FILE = re.compile(
    r"\.(sqlite3?|db|gpkg|gramps|ged|gedcom)(-shm|-wal)?$", re.IGNORECASE
)


@pytest.fixture(scope="module")
def tree(tmp_path_factory):
    """Open a freshly generated fictional database."""
    path = tmp_path_factory.mktemp("synthetic") / "sqlite.db"
    write_synthetic_tree(path)
    connection = sqlite3.connect(path)
    yield connection
    connection.close()


def _objects(tree, table):
    """Return the parsed JSON of every object in a table."""
    rows = tree.execute(f"SELECT json_data FROM {table}")
    return [json.loads(row[0]) for row in rows]


def test_is_large_enough_to_cover_several_generations(tree):
    """The tree has more than a couple of hand-written records."""
    assert tree.execute("SELECT COUNT(*) FROM person").fetchone()[0] >= 30
    assert tree.execute("SELECT COUNT(*) FROM family").fetchone()[0] >= 8


def test_generation_is_reproducible(tmp_path):
    """Same content on every run, so that a failing test can be reproduced."""
    contents = []
    for name in ("one.db", "two.db"):
        write_synthetic_tree(tmp_path / name)
        connection = sqlite3.connect(tmp_path / name)
        rows = connection.execute("SELECT json_data FROM person ORDER BY handle")
        contents.append(rows.fetchall())
        connection.close()
    assert contents[0] == contents[1]


def test_all_date_forms_occur(tree):
    """Exact, about, before, after, between, from-to and text dates all exist."""
    modifiers = {event["date"]["modifier"] for event in _objects(tree, "event")}
    assert {0, 1, 2, 3, 4, 5, 6} <= modifiers


def test_range_dates_have_both_ends(tree):
    """Ranges and spans use the eight-element dateval."""
    lengths = {
        event["date"]["modifier"]: len(event["date"]["dateval"])
        for event in _objects(tree, "event")
    }
    assert lengths[4] == 8
    assert lengths[5] == 8


def test_custom_types_occur(tree):
    """Custom event and note types carry their label in ``string``."""
    assert any(event["type"]["string"] for event in _objects(tree, "event"))
    assert any(note["type"]["string"] for note in _objects(tree, "note"))


def test_all_genders_occur(tree):
    """Female, male and unknown persons exist."""
    genders = Counter(person["gender"] for person in _objects(tree, "person"))
    assert {0, 1, 2} <= set(genders)


def test_name_variety_occurs(tree):
    """Compound surnames and alternate names exist."""
    people = _objects(tree, "person")
    assert any(len(p["primary_name"]["surname_list"]) > 1 for p in people)
    assert any(p["alternate_names"] for p in people)


def test_incomplete_families_occur(tree):
    """Single-parent and childless families exist."""
    families = _objects(tree, "family")
    assert any(not f["father_handle"] and f["mother_handle"] for f in families)
    assert any(f["father_handle"] and not f["mother_handle"] for f in families)
    assert any(not f["child_ref_list"] for f in families)


def test_relationships_are_consistent(tree):
    """Every family member's record points back to the family."""
    people = {p["handle"]: p for p in _objects(tree, "person")}
    for family in _objects(tree, "family"):
        for parent in (family["father_handle"], family["mother_handle"]):
            if parent:
                assert family["handle"] in people[parent]["family_list"]
        for child in family["child_ref_list"]:
            assert family["handle"] in people[child["ref"]]["parent_family_list"]


def test_place_hierarchy_has_several_levels(tree):
    """A town sits inside a region inside a country."""
    places = {p["handle"]: p for p in _objects(tree, "place")}
    depths = []
    for place in places.values():
        depth = 0
        refs = place["placeref_list"]
        while refs and refs[0]["ref"] in places:
            depth += 1
            refs = places[refs[0]["ref"]]["placeref_list"]
        depths.append(depth)
    assert max(depths) >= 2


def test_repository_has_no_tracked_data_files():
    """No database or tree export may be committed, however well anonymized."""
    tracked = subprocess.run(
        ["git", "ls-files"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    data_files = [path for path in tracked if DATA_FILE.search(path)]
    assert not data_files, f"Data files in the repository: {data_files}"
