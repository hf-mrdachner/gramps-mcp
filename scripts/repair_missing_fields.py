"""
Repair script: backfill missing required fields in Gramps SQLite objects.

Objects stored without fields that Gramps always expects (e.g. ``attribute_list``,
``date``) cause Gramps' DB-check tool and the Citations view to crash with a
``KeyError`` in ``cleanup_empty_objects``.

Strategy
--------
* **Top-level fields** — compared against ``_empty_gramps_json`` templates (our
  canonical Gramps JSON format).  Any key present in the template but absent
  from the stored object is back-filled with the template's default value.
  ``handle``, ``gramps_id``, ``change``, and ``_class`` are never overwritten.

* **Sub-object fields** — EventRef, MediaRef, ChildRef, PersonRef, Address,
  LdsOrd, RepoRef, Attribute — ensured to have their required keys.

Run while Gramps Desktop is **closed**.

Usage:
    uv run python scripts/repair_missing_fields.py <path/to/sqlite.db>
    uv run python scripts/repair_missing_fields.py <path/to/sqlite.db> --dry-run
"""

import argparse
import json
import sqlite3
from typing import Any, Dict, List, Tuple

# Import canonical templates and sub-object denormalisers from the package.
from gramps_mcp._gramps_sqlite import _denorm_date, _empty_gramps_json


# ---------------------------------------------------------------------------
# Top-level repair via template comparison
# ---------------------------------------------------------------------------

# Fields that are managed by the DB row itself; never add via template.
_SKIP_KEYS = {"handle", "gramps_id", "change", "_class"}

# Pre-build templates once (they contain _denorm_date calls etc.)
# NOTE: place is intentionally excluded — Gramps Desktop omits most empty
# fields from place json_data; only targeted fixes are applied to places.
_TEMPLATES: Dict[str, Dict] = {
    t: _empty_gramps_json(t)
    for t in ("person", "family", "event", "citation", "source", "media")
}

_EMPTY_DATE = _denorm_date({})


def repair_top_level(obj_type: str, data: Dict) -> bool:
    """
    Backfill any top-level field that is in the template but absent in data.

    Returns True if any field was added.
    """
    template = _TEMPLATES.get(obj_type)
    if not template:
        return False
    changed = False
    for key, default in template.items():
        if key in _SKIP_KEYS:
            continue
        if key not in data:
            data[key] = default
            changed = True
    return changed


# ---------------------------------------------------------------------------
# Sub-object repair  (setdefault only — never overwrites existing values)
# ---------------------------------------------------------------------------

def _fix_event_ref(eref: Any) -> bool:
    if not isinstance(eref, dict):
        return False
    changed = False
    for key, default in [
        ("_class", "EventRef"),
        ("private", False),
        ("note_list", []),
        ("citation_list", []),
        ("attribute_list", []),
    ]:
        if key not in eref:
            eref[key] = default
            changed = True
    return changed


def _fix_media_ref(mref: Any) -> bool:
    if not isinstance(mref, dict):
        return False
    changed = False
    for key, default in [
        ("_class", "MediaRef"),
        ("private", False),
        ("note_list", []),
        ("attribute_list", []),
        ("citation_list", []),
        ("rect", None),
    ]:
        if key not in mref:
            mref[key] = default
            changed = True
    return changed


def _fix_child_ref(cref: Any) -> bool:
    if not isinstance(cref, dict):
        return False
    changed = False
    for key, default in [
        ("_class", "ChildRef"),
        ("private", False),
        ("note_list", []),
        ("citation_list", []),
    ]:
        if key not in cref:
            cref[key] = default
            changed = True
    return changed


def _fix_person_ref(pref: Any) -> bool:
    if not isinstance(pref, dict):
        return False
    changed = False
    for key, default in [
        ("_class", "PersonRef"),
        ("private", False),
        ("rel", ""),
        ("note_list", []),
        ("attribute_list", []),
        ("citation_list", []),
    ]:
        if key not in pref:
            pref[key] = default
            changed = True
    return changed


def _fix_address(addr: Any) -> bool:
    if not isinstance(addr, dict):
        return False
    changed = False
    for key, default in [
        ("_class", "Address"),
        ("private", False),
        ("note_list", []),
        ("citation_list", []),
    ]:
        if key not in addr:
            addr[key] = default
            changed = True
    return changed


def _fix_lds_ord(ord_: Any) -> bool:
    if not isinstance(ord_, dict):
        return False
    changed = False
    for key, default in [
        ("_class", "LdsOrd"),
        ("private", False),
        ("note_list", []),
        ("citation_list", []),
    ]:
        if key not in ord_:
            ord_[key] = default
            changed = True
    return changed


def _fix_repo_ref(rref: Any) -> bool:
    if not isinstance(rref, dict):
        return False
    changed = False
    for key, default in [
        ("_class", "RepoRef"),
        ("private", False),
        ("note_list", []),
    ]:
        if key not in rref:
            rref[key] = default
            changed = True
    return changed


def _fix_place_name(name: Any) -> Tuple[bool, Any]:
    """
    Ensure a PlaceName entry is a proper dict, not a plain string.

    Returns (changed, corrected_value).
    """
    if isinstance(name, str):
        return True, {
            "_class": "PlaceName", "value": name,
            "date": _EMPTY_DATE, "lang": "",
        }
    if isinstance(name, dict):
        changed = False
        for key, default in [
            ("_class", "PlaceName"), ("value", ""), ("lang", ""), ("date", _EMPTY_DATE),
        ]:
            if key not in name:
                name[key] = default
                changed = True
        return changed, name
    return False, name


def _fix_attribute(attr: Any) -> bool:
    if not isinstance(attr, dict):
        return False
    changed = False
    for key, default in [
        ("_class", "Attribute"),
        ("private", False),
        ("note_list", []),
        ("citation_list", []),
    ]:
        if key not in attr:
            attr[key] = default
            changed = True
    return changed


def _fix_list(lst: List, fix_fn) -> bool:
    changed = False
    for item in (lst or []):
        if fix_fn(item):
            changed = True
    return changed


def _fix_date_dict(d: Any) -> bool:
    """
    Ensure a Gramps Date dict's dateval has at least 4 elements.

    Gramps _display_gregorian accesses dateval[2] (year) and dateval[3]
    (slash_year), causing IndexError if the list is shorter.
    """
    if not isinstance(d, dict):
        return False
    dv = d.get("dateval")
    if not isinstance(dv, list) or len(dv) >= 4:
        return False
    while len(dv) < 3:
        dv.append(0)
    dv.append(False)  # slot 3 is slash_year (bool)
    d["dateval"] = dv
    return True


def _fix_all_dates(obj: Any) -> bool:
    """
    Recursively walk any JSON structure and fix Date dicts with short dateval.

    Covers date fields at any nesting depth (top-level, inside address_list,
    lds_ord_list, primary_name, etc.).
    """
    if isinstance(obj, dict):
        changed = False
        if obj.get("_class") == "Date":
            changed |= _fix_date_dict(obj)
        for v in obj.values():
            changed |= _fix_all_dates(v)
        return changed
    if isinstance(obj, list):
        changed = False
        for item in obj:
            changed |= _fix_all_dates(item)
        return changed
    return False


# ---------------------------------------------------------------------------
# Per-table repair
# ---------------------------------------------------------------------------

def repair_object(obj_type: str, data: Dict) -> bool:
    """
    Apply all repairs to a single raw Gramps JSON dict.

    Returns True if any field was added.
    """
    changed = repair_top_level(obj_type, data)

    # Fix any Date dict with dateval shorter than the required 4 elements.
    changed |= _fix_all_dates(data)

    # Sub-object repairs (fields must exist after top-level repair populates lists)
    changed |= _fix_list(data.get("event_ref_list", []), _fix_event_ref)
    changed |= _fix_list(data.get("media_list", []), _fix_media_ref)
    changed |= _fix_list(data.get("child_ref_list", []), _fix_child_ref)
    changed |= _fix_list(data.get("person_ref_list", []), _fix_person_ref)
    changed |= _fix_list(data.get("address_list", []), _fix_address)
    changed |= _fix_list(data.get("lds_ord_list", []), _fix_lds_ord)
    changed |= _fix_list(data.get("reporef_list", []), _fix_repo_ref)
    changed |= _fix_list(data.get("attribute_list", []), _fix_attribute)

    # Repository: type must be RepositoryType dict, not a plain string
    if obj_type == "repository":
        repo_type = data.get("type")
        if isinstance(repo_type, str):
            data["type"] = {"_class": "RepositoryType", "value": 0, "string": repo_type}
            changed = True

    # Note: StyledText.tags required by Gramps deserializer; type must be NoteType dict
    if obj_type == "note":
        text = data.get("text")
        if isinstance(text, dict):
            note_changed = False
            if "tags" not in text:
                text["tags"] = []
                note_changed = True
            if "_class" not in text:
                text["_class"] = "StyledText"
                note_changed = True
            if note_changed:
                changed = True
        # type stored as plain string by old code → convert to NoteType dict
        note_type = data.get("type")
        if isinstance(note_type, str):
            data["type"] = {"_class": "NoteType", "value": 1, "string": note_type}
            changed = True

    # Place: primary name and alt_names must be full PlaceName dicts with date
    if obj_type == "place":
        primary = data.get("name")
        if primary is not None:
            name_changed, fixed_name = _fix_place_name(primary)
            if name_changed:
                data["name"] = fixed_name
                changed = True
        if data.get("alt_names"):
            new_alt = []
            for entry in data["alt_names"]:
                item_changed, fixed = _fix_place_name(entry)
                new_alt.append(fixed)
                if item_changed:
                    changed = True
            data["alt_names"] = new_alt

    return changed


_TABLES = ["person", "family", "event", "citation", "source", "media", "place", "note", "repository"]


def repair_table(
    conn: sqlite3.Connection,
    table: str,
    dry_run: bool,
) -> Tuple[int, int]:
    """Scan all rows in *table*, repair, write back if changed."""
    rows = conn.execute(
        f"SELECT handle, json_data FROM {table}"  # noqa: S608
    ).fetchall()
    fixed = 0
    for row in rows:
        handle = row[0]
        try:
            data = json.loads(row[1])
        except Exception as exc:
            print(f"  WARN: {table}/{handle}: JSON parse error: {exc}")
            continue
        if repair_object(table, data):
            fixed += 1
            if not dry_run:
                conn.execute(
                    f"UPDATE {table} SET json_data=? WHERE handle=?",  # noqa: S608
                    (json.dumps(data, ensure_ascii=False), handle),
                )
    return len(rows), fixed


def repair_person_secondary_columns(
    conn: sqlite3.Connection,
    dry_run: bool,
) -> Tuple[int, int]:
    """
    Fix person.surname and person.given_name secondary columns that are NULL.

    Gramps' get_surname_list() reads the surname column directly; NULL causes
    changenames.py to crash on name.strip().
    """
    rows = conn.execute(
        "SELECT handle, json_data FROM person WHERE surname IS NULL OR given_name IS NULL"  # noqa: S608
    ).fetchall()
    fixed = 0
    for handle, jdata in rows:
        try:
            data = json.loads(jdata)
        except Exception as exc:
            print(f"  WARN: person/{handle}: JSON parse error: {exc}")
            continue
        pn = data.get("primary_name", {})
        sl = pn.get("surname_list", [])
        surname = sl[0].get("surname") or "" if sl else ""
        given_name = pn.get("first_name") or ""
        fixed += 1
        if not dry_run:
            conn.execute(
                "UPDATE person SET surname=?, given_name=? WHERE handle=?",  # noqa: S608
                (surname, given_name, handle),
            )
    return len(rows), fixed


def repair_family_secondary_columns(
    conn: sqlite3.Connection,
    dry_run: bool,
) -> Tuple[int, int]:
    """
    Fix family.father_handle / family.mother_handle columns that drifted from json_data.

    A partial put("family", ...) that omitted father_handle/mother_handle used
    to blank or stale these secondary columns while json_data (the merged,
    authoritative record) kept the correct value — see _secondaries() in
    _gramps_sqlite.py, now fixed to read from the merged JSON on write. This
    repairs rows written before that fix.
    """
    rows = conn.execute(
        "SELECT handle, json_data, father_handle, mother_handle FROM family"  # noqa: S608
    ).fetchall()
    fixed = 0
    for handle, jdata, col_father, col_mother in rows:
        try:
            data = json.loads(jdata)
        except Exception as exc:
            print(f"  WARN: family/{handle}: JSON parse error: {exc}")
            continue
        json_father = data.get("father_handle") or None
        json_mother = data.get("mother_handle") or None
        if col_father == json_father and col_mother == json_mother:
            continue
        fixed += 1
        if not dry_run:
            conn.execute(
                "UPDATE family SET father_handle=?, mother_handle=? WHERE handle=?",  # noqa: S608
                (json_father, json_mother, handle),
            )
    return len(rows), fixed


def _repair_single_ref_column(
    conn: sqlite3.Connection,
    table: str,
    column: str,
    dry_run: bool,
) -> Tuple[int, int]:
    """
    Fix a single reference-type secondary column that drifted from json_data.

    Normalizes both sides with ``or None`` before comparing, matching the
    convention ``_secondaries()`` uses on write — this treats Gramps
    Desktop's native "" placeholder and a genuinely missing value as
    equivalent, so rows never touched by our tool are never flagged.
    """
    rows = conn.execute(
        f"SELECT handle, json_data, {column} FROM {table}"  # noqa: S608
    ).fetchall()
    fixed = 0
    for handle, jdata, col_val in rows:
        try:
            data = json.loads(jdata)
        except Exception as exc:
            print(f"  WARN: {table}/{handle}: JSON parse error: {exc}")
            continue
        json_val = data.get(column) or None
        col_val = col_val or None
        if col_val == json_val:
            continue
        fixed += 1
        if not dry_run:
            conn.execute(
                f"UPDATE {table} SET {column}=? WHERE handle=?",  # noqa: S608
                (json_val, handle),
            )
    return len(rows), fixed


def repair_event_secondary_columns(
    conn: sqlite3.Connection,
    dry_run: bool,
) -> Tuple[int, int]:
    """
    Fix event.place columns that drifted from json_data.

    Same root cause as repair_family_secondary_columns, but triggered far
    more often in practice: merge/citation-linking tools frequently patch a
    single unrelated field on an event, which used to blank/stale `place`.
    """
    return _repair_single_ref_column(conn, "event", "place", dry_run)


def repair_citation_secondary_columns(
    conn: sqlite3.Connection,
    dry_run: bool,
) -> Tuple[int, int]:
    """Fix citation.source_handle columns that drifted from json_data."""
    return _repair_single_ref_column(conn, "citation", "source_handle", dry_run)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Run the repair against the given Gramps SQLite database."""
    parser = argparse.ArgumentParser(
        description="Backfill missing required fields in a Gramps SQLite database."
    )
    parser.add_argument("db_path", help="Path to the Gramps sqlite.db file")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Scan and report without writing anything",
    )
    args = parser.parse_args()

    print(f"{'[DRY RUN] ' if args.dry_run else ''}Repairing: {args.db_path}")

    conn = sqlite3.connect(args.db_path)
    conn.row_factory = sqlite3.Row

    total_fixed = 0
    try:
        with conn:
            for table in _TABLES:
                scanned, fixed = repair_table(conn, table, args.dry_run)
                status = "would fix" if args.dry_run else "fixed"
                print(f"  {table:12s}: scanned {scanned:5d}  {status} {fixed}")
                total_fixed += fixed
            scanned, fixed = repair_person_secondary_columns(conn, args.dry_run)
            status = "would fix" if args.dry_run else "fixed"
            print(f"  {'person.cols':12s}: scanned {scanned:5d}  {status} {fixed}")
            total_fixed += fixed
            scanned, fixed = repair_family_secondary_columns(conn, args.dry_run)
            status = "would fix" if args.dry_run else "fixed"
            print(f"  {'family.cols':12s}: scanned {scanned:5d}  {status} {fixed}")
            total_fixed += fixed
            scanned, fixed = repair_event_secondary_columns(conn, args.dry_run)
            status = "would fix" if args.dry_run else "fixed"
            print(f"  {'event.cols':12s}: scanned {scanned:5d}  {status} {fixed}")
            total_fixed += fixed
            scanned, fixed = repair_citation_secondary_columns(conn, args.dry_run)
            status = "would fix" if args.dry_run else "fixed"
            print(f"  {'citation.cols':12s}: scanned {scanned:5d}  {status} {fixed}")
            total_fixed += fixed
    finally:
        conn.close()

    verb = "Would fix" if args.dry_run else "Fixed"
    print(f"\n{verb} {total_fixed} object(s) total.")
    if args.dry_run and total_fixed > 0:
        print("Re-run without --dry-run to apply changes.")


if __name__ == "__main__":
    main()
