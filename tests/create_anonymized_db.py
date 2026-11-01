"""
Create an anonymized Gramps SQLite test fixture from a real database.

Reads the database named by GRAMPS_TEST_DB_PATH and writes an anonymized copy to
tests/fixtures/test_gramps.sqlite, which is committed to a public repository.

Anonymization is default-deny: every string is dropped unless its key is listed
in STRUCTURAL_KEYS, and fictional values are then filled in where tests need
them. A field this script has never heard of therefore cannot leak.

  - Names, places, sources, repositories: fictional values from word lists
  - Citation pages: a running number
  - Notes: Lorem Ipsum
  - Dates: years shifted by a secret offset, months and days randomized
  - Attributes, URLs, addresses, alternate names: removed
  - Metadata (researcher, media path, custom-type lists): removed
  - gender_stats: rebuilt from the fictional names
  - Handles, IDs, relationships: unchanged

Reason for no fixed seed: the seed would be public, which would make the year
shift and the name mapping reversible.

tests/test_fixture_anonymized.py fails if anything else ends up in the fixture.

Usage:
    GRAMPS_TEST_DB_PATH=/path/to/sqlite.db uv run python tests/create_anonymized_db.py
"""

import json
import os
import random
import re
import shutil
import sqlite3
import sys
import tempfile
from collections import Counter
from datetime import date as calendar_date
from pathlib import Path

TARGET_DB = Path(__file__).parent / "fixtures" / "test_gramps.sqlite"

# Reason: drawn once per run and never stored, so the offset cannot be recovered.
_random = random.SystemRandom()
YEAR_SHIFT = -_random.randint(30, 80)

# Offset between a proleptic Gregorian ordinal and a Julian day number.
_JULIAN_DAY_OFFSET = 1721425

# ---------------------------------------------------------------------------
# Fictional vocabulary
# ---------------------------------------------------------------------------

_FIRST_NAMES_F = [
    "Anna", "Maria", "Elise", "Klara", "Helene", "Emma", "Ida", "Rosa",
    "Martha", "Frieda", "Greta", "Lotte", "Hedwig", "Paula", "Berta",
    "Hildegard", "Erna", "Else", "Agnes", "Wilhelmine",
]
_FIRST_NAMES_M = [
    "Karl", "Hans", "Wilhelm", "Friedrich", "Ernst", "Otto", "Franz",
    "Heinrich", "Gustav", "Hermann", "Georg", "Paul", "Johann", "Walter",
    "Albert", "Rudolf", "Erich", "Max", "Alfred", "Emil",
]
_SURNAMES = [
    "Bauer", "Fischer", "Hoffmann", "Müller", "Schmidt", "Schneider",
    "Weber", "Wagner", "Meyer", "Koch", "Richter", "Becker", "Schäfer",
    "Zimmermann", "Braun", "Krause", "Wolf", "Schröder", "Neumann", "Schwarz",
    "Hartmann", "Lange", "Schmitt", "Werner", "Schmitz", "Kramer", "Lehmann",
    "König", "Walter", "Mayer", "Huber", "Kaiser", "Fuchs", "Peters",
]
_PLACES = [
    "Musterdorf", "Beispielhausen", "Testburg", "Musterstadt", "Probedorf",
    "Neuheim", "Altfeld", "Waldkirch", "Bergheim", "Seebach",
    "Thalheim", "Steinbach", "Kirchdorf", "Lindenau", "Fichtenwald",
    "Eichenbach", "Buchenheim", "Ahorntal", "Rosenfeld", "Blumental",
]
_NOTE_TEXT = (
    "Lorem ipsum dolor sit amet, consectetur adipiscing elit. "
    "Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua. "
    "Ut enim ad minim veniam, quis nostrud exercitation ullamco laboris."
)
_SOURCE_TITLES = [
    "Kirchenbuch St. Marien", "Standesamt Musterdorf", "Taufregister 1800-1900",
    "Heiratsregister Kreis Muster", "Sterberegister Pfarrei Beispiel",
    "Volkszählung 1900", "Militärregister 1914", "Auswandererliste Hamburg",
    "Grundbuch Mustergemeinde", "Schulregister Altfeld",
]
_SOURCE_AUTHOR = "Musterarchiv"
_SOURCE_PUBINFO = "Musterdorf, 1900"
_MEDIA_DESC = "Anonymized media"
_MEDIA_MIME = "image/jpeg"
_CUSTOM_TYPE = "Custom"

_FICTIONAL_TEXT = frozenset(
    _FIRST_NAMES_F + _FIRST_NAMES_M + _SURNAMES + _PLACES + _SOURCE_TITLES
    + [
        _NOTE_TEXT, _SOURCE_AUTHOR, _SOURCE_PUBINFO, _MEDIA_DESC, _MEDIA_MIME,
        _CUSTOM_TYPE,
    ]
)
_FICTIONAL_PATTERN = re.compile(r"(SRC|Seite |Archiv |Tag )\d+")

# ---------------------------------------------------------------------------
# What may stay
# ---------------------------------------------------------------------------

# JSON keys whose strings are structure (handles, IDs, class names), never content.
STRUCTURAL_KEYS = frozenset({
    "_class", "handle", "gramps_id", "ref", "father_handle", "mother_handle",
    "source_handle", "place", "citation_list", "note_list", "tag_list",
    "family_list", "parent_family_list", "enclosed_by",
})

# Table columns holding structure or numbers instead of text.
HANDLE_COLUMNS = frozenset({
    "handle", "gramps_id", "father_handle", "mother_handle", "source_handle",
    "place", "enclosed_by", "change", "private", "gender", "death_ref_index",
    "birth_ref_index", "confidence", "format", "priority", "color",
})

# Lists whose entries carry free-form details and are not needed by any test.
_REMOVED_LISTS = frozenset({
    "urls", "attribute_list", "address_list", "alternate_names", "alt_names",
    "alt_loc", "person_ref_list", "lds_ord_list", "data_map",
})

# Metadata settings that describe the database, not the person maintaining it.
_KEPT_METADATA = frozenset({"version", "default-person-handle"})


def is_fictional_text(text: str) -> bool:
    """
    Tell whether a string comes from this script's fictional vocabulary.

    Args:
        text (str): The string to check.

    Returns:
        bool: True if the string was generated by this script.
    """
    return text in _FICTIONAL_TEXT or bool(_FICTIONAL_PATTERN.fullmatch(text))


# ---------------------------------------------------------------------------
# Generic scrubbing
# ---------------------------------------------------------------------------

def _anon_date(date: dict) -> dict:
    """Shift years, randomize month and day, and clear the original text."""
    result = dict(date)
    result["text"] = ""
    dateval = list(result.get("dateval") or [])
    if len(dateval) < 3:
        return result

    # Reason: a range keeps one month and day, so its end stays after its start.
    month = _random.randint(1, 12)
    day = _random.randint(1, 28)
    for start in range(0, len(dateval) - 2, 4):
        if not dateval[start + 2]:
            continue
        dateval[start + 2] = max(1, dateval[start + 2] + YEAR_SHIFT)
        if dateval[start + 1]:
            dateval[start + 1] = month
        if dateval[start]:
            dateval[start] = day
    result["dateval"] = dateval

    # Reason: sortval is a Julian day number and would reveal the real date.
    if result.get("sortval") and dateval[2]:
        day_of_year = calendar_date(dateval[2], dateval[1] or 1, dateval[0] or 1)
        result["sortval"] = day_of_year.toordinal() + _JULIAN_DAY_OFFSET
    return result


def _scrub(obj, key=None):
    """Recursively remove every string that is not structural."""
    if isinstance(obj, dict):
        if obj.get("_class") == "Date":
            return _anon_date(obj)
        # Custom types hold free text in "string"; standard types have it empty.
        if "string" in obj and "value" in obj and obj.get("string"):
            return {**obj, "string": _CUSTOM_TYPE}
        return {
            child_key: [] if child_key in _REMOVED_LISTS else _scrub(value, child_key)
            for child_key, value in obj.items()
        }
    if isinstance(obj, list):
        return [_scrub(value, key) for value in obj]
    if isinstance(obj, str):
        return obj if key in STRUCTURAL_KEYS else ""
    return obj


# ---------------------------------------------------------------------------
# Per-object fictional values
# ---------------------------------------------------------------------------

def _anon_person(data: dict) -> dict:
    """Give a scrubbed person fictional names."""
    obj = dict(data)
    pool = _FIRST_NAMES_M if obj.get("gender") == 1 else _FIRST_NAMES_F
    name = obj.get("primary_name")
    if isinstance(name, dict):
        name = {**name, "first_name": _random.choice(pool)}
        surname = _random.choice(_SURNAMES)
        name["surname_list"] = [
            {**entry, "surname": surname} for entry in name.get("surname_list", [])
        ]
        obj["primary_name"] = name
    obj["note_list"] = []
    return obj


def _anon_place(data: dict) -> dict:
    """Give a scrubbed place a fictional name."""
    obj = dict(data)
    place_name = _random.choice(_PLACES)
    obj["title"] = place_name
    if isinstance(obj.get("name"), dict):
        obj["name"] = {**obj["name"], "value": place_name}
    return obj


def _anon_source(data: dict, index: int) -> dict:
    """Give a scrubbed source fictional details."""
    return {
        **data,
        "title": _SOURCE_TITLES[index % len(_SOURCE_TITLES)],
        "author": _SOURCE_AUTHOR,
        "pubinfo": _SOURCE_PUBINFO,
        "abbrev": f"SRC{index}",
    }


def _anon_citation(data: dict, index: int) -> dict:
    """Give a scrubbed citation a fictional page."""
    return {**data, "page": f"Seite {index}"}


def _anon_note(data: dict) -> dict:
    """Replace a scrubbed note's text with Lorem Ipsum."""
    obj = dict(data)
    if isinstance(obj.get("text"), dict):
        obj["text"] = {**obj["text"], "string": _NOTE_TEXT, "tags": []}
    return obj


def _anon_media(data: dict) -> dict:
    """Give a scrubbed media object fictional details."""
    return {**data, "desc": _MEDIA_DESC, "mime": _MEDIA_MIME, "path": ""}


def _anon_repository(data: dict, index: int) -> dict:
    """Give a scrubbed repository a fictional name."""
    return {**data, "name": f"Archiv {index}"}


def _anon_tag(data: dict, index: int) -> dict:
    """Give a scrubbed tag a fictional name."""
    return {**data, "name": f"Tag {index}"}


_OBJECT_TRANSFORMS = {
    "person": lambda data, index: _anon_person(data),
    "family": lambda data, index: data,
    "event": lambda data, index: data,
    "place": lambda data, index: _anon_place(data),
    "source": _anon_source,
    "citation": _anon_citation,
    "note": lambda data, index: _anon_note(data),
    "media": lambda data, index: _anon_media(data),
    "repository": _anon_repository,
    "tag": _anon_tag,
}


# ---------------------------------------------------------------------------
# Table-level processing
# ---------------------------------------------------------------------------

def _secondary_value(table: str, column: str, data: dict):
    """Return the value of a text column duplicating a field of the JSON."""
    if table == "person":
        name = data.get("primary_name") or {}
        surnames = name.get("surname_list") or []
        if column == "given_name":
            return name.get("first_name", "")
        if column == "surname":
            return surnames[0].get("surname", "") if surnames else ""
    value = data.get(column)
    return value if isinstance(value, str) else ""


def _copy_objects(src, dst, table: str, transform) -> None:
    """Copy one object table, anonymizing its JSON and every text column."""
    columns = [row[1] for row in src.execute(f"PRAGMA table_info({table})")]
    placeholders = ",".join("?" * len(columns))
    insert = f"INSERT INTO {table} ({','.join(columns)}) VALUES ({placeholders})"
    for index, row in enumerate(src.execute(f"SELECT * FROM {table}").fetchall()):
        values = dict(zip(columns, row))
        data = transform(_scrub(json.loads(values["json_data"])), index)
        values["json_data"] = json.dumps(data, ensure_ascii=False)
        for column in columns:
            if column not in HANDLE_COLUMNS and column != "json_data":
                values[column] = _secondary_value(table, column, data)
        dst.execute(insert, [values[column] for column in columns])


def _copy_metadata(src, dst) -> None:
    """Copy metadata, emptying everything that is not database structure."""
    rows = src.execute("SELECT setting, json_data FROM metadata").fetchall()
    for setting, raw in rows:
        envelope = json.loads(raw) if raw else {}
        value = envelope.get("value")
        if setting not in _KEPT_METADATA:
            if isinstance(value, dict):
                value = {
                    key: item if key in ("_class", "type") else ""
                    for key, item in value.items()
                }
            elif isinstance(value, list):
                value = []
            elif isinstance(value, str):
                value = ""
        envelope["value"] = value
        dst.execute(
            "INSERT INTO metadata (setting, json_data, value) VALUES (?, ?, NULL)",
            (setting, json.dumps(envelope, ensure_ascii=False)),
        )


def _rebuild_gender_stats(dst) -> None:
    """Fill gender_stats from the fictional names; the source lists real ones."""
    counts: Counter = Counter()
    for given_name, gender in dst.execute("SELECT given_name, gender FROM person"):
        counts[(given_name, gender)] += 1
    for name in {name for name, _gender in counts}:
        dst.execute(
            "INSERT INTO gender_stats (given_name, female, male, unknown) "
            "VALUES (?, ?, ?, ?)",
            (name, counts[(name, 0)], counts[(name, 1)], counts[(name, 2)]),
        )


def _anonymize(source: Path, target: Path) -> None:
    """Write an anonymized copy of source to target, which must not exist."""
    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    dst = sqlite3.connect(target)
    tables = [
        row[0]
        for row in src.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND sql IS NOT NULL"
        )
    ]
    for (sql,) in src.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND sql IS NOT NULL"
    ).fetchall():
        dst.execute(sql)

    for table, transform in _OBJECT_TRANSFORMS.items():
        if table in tables:
            print(f"  {table}")
            _copy_objects(src, dst, table, transform)
    if "reference" in tables:
        dst.executemany(
            "INSERT INTO reference VALUES (?, ?, ?, ?)",
            src.execute("SELECT * FROM reference").fetchall(),
        )
    if "metadata" in tables:
        _copy_metadata(src, dst)
    if "gender_stats" in tables:
        _rebuild_gender_stats(dst)
    # Any other table, such as name_group, is deliberately left empty.

    dst.commit()
    # Reason: no WAL file next to the fixture, and no free pages with old content.
    dst.execute("PRAGMA journal_mode = DELETE")
    dst.execute("VACUUM")
    dst.close()
    src.close()


def main() -> None:
    """Generate the fixture from the database named by GRAMPS_TEST_DB_PATH."""
    source_path = os.environ.get("GRAMPS_TEST_DB_PATH")
    if not source_path or not os.path.exists(source_path):
        print("ERROR: set GRAMPS_TEST_DB_PATH to an existing Gramps sqlite.db.")
        sys.exit(1)

    with tempfile.TemporaryDirectory() as work_dir:
        # Reason: works on copies, so the source can be the fixture itself and a
        # real database is neither locked nor given sidecar files.
        source = Path(work_dir) / "source.sqlite"
        shutil.copy(source_path, source)
        target = Path(work_dir) / "target.sqlite"
        print(f"Target: {TARGET_DB}")
        _anonymize(source, target)
        TARGET_DB.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(target, TARGET_DB)

    print(f"Done. {TARGET_DB.name} ({TARGET_DB.stat().st_size // 1024} KB)")
    print("Verify with: uv run pytest tests/test_fixture_anonymized.py")


if __name__ == "__main__":
    main()
