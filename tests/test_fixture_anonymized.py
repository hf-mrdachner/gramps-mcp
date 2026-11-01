"""
Privacy guard for the committed test fixture tests/fixtures/test_gramps.sqlite.

The fixture is generated from a real family tree and is public. These tests are
default-deny: every string in the file must be structural or come from the
generator's fictional vocabulary, so a new kind of leak fails without anyone
having to name the leaked value here.
"""

import json
import re
import shutil
import sqlite3
from pathlib import Path

import pytest
from create_anonymized_db import (
    HANDLE_COLUMNS,
    STRUCTURAL_KEYS,
    is_fictional_text,
)

FIXTURE = Path(__file__).parent / "fixtures" / "test_gramps.sqlite"

OBJECT_TABLES = (
    "person",
    "family",
    "event",
    "place",
    "source",
    "citation",
    "note",
    "media",
    "repository",
    "tag",
)

# Everything a real machine or person could have left behind in the raw bytes.
RAW_LEAK_MARKERS = re.compile(
    rb"users[\\/]|documents[\\/]|privat[\\/]|https?://|\.jpg|\.gpkg|@[a-z0-9-]+\.",
    re.IGNORECASE,
)


@pytest.fixture(scope="module")
def fixture_db(tmp_path_factory):
    """Open a copy of the fixture, so that reading it leaves no sidecar files."""
    copy = tmp_path_factory.mktemp("fixture_audit") / "copy.sqlite"
    shutil.copy(FIXTURE, copy)
    connection = sqlite3.connect(copy)
    yield connection
    connection.close()


def _leaks(obj, key=None):
    """Yield (key, value) for every string in a JSON value that is not allowed."""
    if isinstance(obj, dict):
        for child_key, value in obj.items():
            yield from _leaks(value, child_key)
    elif isinstance(obj, list):
        for value in obj:
            yield from _leaks(value, key)
    elif isinstance(obj, str) and obj:
        if key not in STRUCTURAL_KEYS and not is_fictional_text(obj):
            yield key, obj


@pytest.mark.parametrize("table", OBJECT_TABLES)
def test_object_json_holds_only_fictional_text(fixture_db, table):
    """No free text in an object's JSON may come from the real tree."""
    leaks = []
    for (raw,) in fixture_db.execute(f"SELECT json_data FROM {table}"):
        leaks.extend(_leaks(json.loads(raw)))
    assert not leaks, f"{len(leaks)} real strings in {table}.json_data"


@pytest.mark.parametrize("table", OBJECT_TABLES)
def test_secondary_columns_hold_only_fictional_text(fixture_db, table):
    """Columns duplicating JSON fields must be anonymized as well."""
    columns = [row[1] for row in fixture_db.execute(f"PRAGMA table_info({table})")]
    text_columns = [
        column
        for column in columns
        if column not in HANDLE_COLUMNS and column != "json_data"
    ]
    if not text_columns:
        return
    leaks = []
    for row in fixture_db.execute(f"SELECT {','.join(text_columns)} FROM {table}"):
        for column, value in zip(text_columns, row):
            if isinstance(value, str) and value and not is_fictional_text(value):
                leaks.append(column)
    assert not leaks, f"{len(leaks)} real values in {table} columns {set(leaks)}"


def test_gender_stats_hold_only_fictional_names(fixture_db):
    """gender_stats lists every given name of the source tree."""
    rows = fixture_db.execute("SELECT given_name FROM gender_stats")
    names = [row[0] for row in rows]
    real = [name for name in names if name and not is_fictional_text(name)]
    assert not real, f"{len(real)} real given names in gender_stats"


def test_name_group_is_empty(fixture_db):
    """name_group would list real surnames."""
    assert fixture_db.execute("SELECT COUNT(*) FROM name_group").fetchone()[0] == 0


def test_metadata_holds_no_personal_values(fixture_db):
    """Researcher details, media path and custom-name lists must be gone."""
    leaks = []
    for setting, raw in fixture_db.execute("SELECT setting, json_data FROM metadata"):
        value = json.loads(raw)["value"] if raw else None
        if setting in ("version", "default-person-handle"):
            continue
        if isinstance(value, dict):
            filled = {
                key: item
                for key, item in value.items()
                if key not in ("_class", "type") and item
            }
            if filled:
                leaks.append(setting)
        elif value and not isinstance(value, int):
            leaks.append(setting)
    assert not leaks, f"personal values in metadata settings: {leaks}"


def test_metadata_value_blobs_are_empty(fixture_db):
    """The legacy value column could hold the same data in binary form."""
    filled = fixture_db.execute(
        "SELECT COUNT(*) FROM metadata WHERE value IS NOT NULL"
    ).fetchone()[0]
    assert filled == 0


def test_dates_carry_no_exact_real_value(fixture_db):
    """Date text is cleared, and sortval agrees with the anonymized dateval."""
    for (raw,) in fixture_db.execute("SELECT json_data FROM event"):
        date = json.loads(raw).get("date") or {}
        assert not date.get("text")
        dateval = date.get("dateval") or []
        if date.get("sortval") and len(dateval) >= 3 and dateval[2]:
            # Reason: sortval is a Julian day number, so an untouched one would
            # give away the real date no matter how dateval was changed.
            approx_year = (date["sortval"] - 1721425) / 365.25
            assert abs(approx_year - dateval[2]) < 2


def test_raw_file_has_no_path_url_or_mail_traces():
    """Checks the bytes too, which covers free pages and unlisted tables."""
    matches = RAW_LEAK_MARKERS.findall(FIXTURE.read_bytes())
    assert not matches, f"{len(matches)} leak markers in raw fixture bytes"


def test_fixture_leaves_no_sidecar_files(tmp_path):
    """The fixture must not be in WAL mode, or any reader creates -shm/-wal files."""
    copy = tmp_path / "copy.sqlite"
    shutil.copy(FIXTURE, copy)
    connection = sqlite3.connect(copy)
    mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
    connection.close()
    assert mode.lower() != "wal"
