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
Gramps SQLite database access layer.

Reads and writes the Gramps SQLite database (grampsdb/*/sqlite.db) directly
using only Python's standard library.  No GTK, no gramps Python package needed.

The Gramps SQLite backend stores each object as JSON in a ``json_data`` column
plus indexed secondary columns.  Typed sub-objects use ``_class`` keys
(e.g. ``{"_class": "EventType", "value": 12, "string": ""}``).

This module normalises that format to the flat-string dict our handlers expect,
and denormalises on write.
"""

import json
import logging
import sqlite3
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from ._gramps_db import GrampsXmlDB
from .client import GrampsAPIError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Type maps  (value → display string)
# ---------------------------------------------------------------------------

EVENT_TYPE: Dict[int, str] = {
    -1: "Unknown", 0: "Custom",
    1: "Marriage", 2: "Marriage Settlement", 3: "Marriage License",
    4: "Marriage Contract", 5: "Marriage Banns", 6: "Engagement",
    7: "Divorce", 8: "Divorce Filing", 9: "Annulment",
    10: "Alternate Marriage", 11: "Adopted", 12: "Birth", 13: "Death",
    14: "Adult Christening", 15: "Baptism", 16: "Bar Mitzvah",
    17: "Bat Mitzvah", 18: "Blessing", 19: "Burial", 20: "Cause Of Death",
    21: "Census", 22: "Christening", 23: "Confirmation", 24: "Cremation",
    25: "Degree", 26: "Education", 27: "Elected", 28: "Emigration",
    29: "First Communion", 30: "Immigration", 31: "Graduation",
    32: "Medical Information", 33: "Military Service", 34: "Naturalization",
    35: "Nobility Title", 36: "Number of Marriages", 37: "Occupation",
    38: "Ordination", 39: "Probate", 40: "Property", 41: "Religion",
    42: "Residence", 43: "Retirement", 44: "Will", 45: "Stillbirth",
}
FAMILY_REL_TYPE: Dict[int, str] = {
    0: "Married", 1: "Unmarried", 2: "Civil Union", 3: "Unknown", 4: "Custom",
}
EVENT_ROLE_TYPE: Dict[int, str] = {
    -1: "Unknown", 0: "Custom", 1: "Primary", 2: "Clergy", 3: "Celebrant",
    4: "Aide", 5: "Bride", 6: "Groom", 7: "Witness", 8: "Family",
    9: "Informant", 10: "Godparent", 11: "Father", 12: "Mother",
    13: "Parent", 14: "Child", 15: "Multiple birth", 16: "Friend",
    17: "Neighbor", 18: "Officiator",
}
CHILD_REF_TYPE: Dict[int, str] = {
    0: "None", 1: "Birth", 2: "Adopted", 3: "Stepchild",
    4: "Sponsored", 5: "Foster", 6: "Unknown", 7: "Custom",
}
NAME_TYPE: Dict[int, str] = {
    -1: "Unknown", 0: "Custom", 1: "Also Known As",
    2: "Birth Name", 3: "Married Name",
}
PLACE_TYPE: Dict[int, str] = {
    -1: "Unknown", 0: "Custom", 1: "Country", 2: "State", 3: "County",
    4: "City", 5: "Parish", 6: "Locality", 7: "Street", 8: "Province",
    9: "Region", 10: "Department", 11: "Neighborhood", 12: "District",
    13: "Borough", 14: "Municipality", 15: "Town", 16: "Village",
    17: "Hamlet", 18: "Farm", 19: "Building", 20: "Number",
}
NOTE_TYPE: Dict[int, str] = {
    -1: "Unknown", 0: "Custom", 1: "General", 2: "Research", 3: "Transcript",
    4: "Person Note", 5: "Attribute Note", 6: "Address Note",
    7: "Association Note", 8: "LDS Note", 9: "Source Note",
    10: "Source Reference Note", 11: "Citation Note", 12: "Event Note",
    13: "Event Reference Note", 14: "Place Note", 15: "Repository Note",
    16: "Repository Reference Note", 17: "Media Note",
    18: "Media Reference Note", 19: "Child Reference Note",
    20: "Family Note", 21: "HTML code",
}
REPOSITORY_TYPE: Dict[int, str] = {
    -1: "Unknown", 0: "Custom", 1: "Library", 2: "Cemetery", 3: "Church",
    4: "National Archive", 5: "Regional Archive", 6: "Institutional Archive",
    7: "Specialty Archive", 8: "Published Work", 9: "Personal Collection",
    10: "Computer Format", 11: "Audio/Video", 12: "Collection",
    13: "Miscellaneous",
}

_TYPE_MAPS: Dict[str, Dict[int, str]] = {
    "EventType": EVENT_TYPE,
    "FamilyRelType": FAMILY_REL_TYPE,
    "EventRoleType": EVENT_ROLE_TYPE,
    "ChildRefType": CHILD_REF_TYPE,
    "NameType": NAME_TYPE,
    "PlaceType": PLACE_TYPE,
    "NoteType": NOTE_TYPE,
    "RepositoryType": REPOSITORY_TYPE,
    "NameOriginType": {},
}
# Custom sentinel value per type class (used when string field is non-empty)
_CUSTOM: Dict[str, int] = {
    "EventType": 0, "FamilyRelType": 4, "EventRoleType": 0,
    "ChildRefType": 7, "NameType": 0, "PlaceType": 0,
    "NoteType": 0, "RepositoryType": 0, "NameOriginType": 0,
}
# Reverse maps: display string → int value
_REV_TYPE_MAPS: Dict[str, Dict[str, int]] = {
    cls: {v: k for k, v in m.items()} for cls, m in _TYPE_MAPS.items()
}

# Table name and ID prefix per object type
_TABLE: Dict[str, str] = {
    "person": "person", "family": "family", "event": "event",
    "place": "place", "source": "source", "citation": "citation",
    "note": "note", "media": "media", "repository": "repository",
    "tag": "tag",
}
_ID_PREFIX: Dict[str, str] = {
    "person": "I", "family": "F", "event": "E", "place": "P",
    "source": "S", "citation": "C", "note": "N", "media": "O",
    "repository": "R", "tag": "T",
}

# ---------------------------------------------------------------------------
# Normalisation  (Gramps JSON → our dict format)
# ---------------------------------------------------------------------------


def _type_str(obj: Any) -> str:
    """Convert a typed Gramps JSON object to a plain string."""
    if not isinstance(obj, dict):
        return str(obj) if obj is not None else ""
    cls = obj.get("_class", "")
    string = obj.get("string", "")
    if string:
        return string
    value = obj.get("value", -1)
    return _TYPE_MAPS.get(cls, {}).get(value, f"Unknown({value})")


def _normalize(obj: Any) -> Any:
    """
    Recursively convert Gramps internal JSON to our flat dict format.

    - Typed sub-objects (EventType, etc.) become plain strings.
    - Date objects are reshaped to ``{dateval, modifier, quality, string}``.
    - ``_class`` keys are removed from all other dicts.

    Args:
        obj: Any JSON value from the Gramps sqlite json_data column.

    Returns:
        Normalised value compatible with the Gramps Web API dict shape.
    """
    if isinstance(obj, list):
        return [_normalize(item) for item in obj]
    if not isinstance(obj, dict):
        return obj

    cls = obj.get("_class", "")

    if cls in _TYPE_MAPS:
        return _type_str(obj)

    if cls == "Date":
        return {
            "dateval": obj.get("dateval", []),
            "modifier": obj.get("modifier", 0),
            "quality": obj.get("quality", 0),
            "string": obj.get("text", ""),
        }

    return {k: _normalize(v) for k, v in obj.items() if k != "_class"}


def _normalize_person(raw: Dict) -> Dict:
    """Normalise a raw Gramps person JSON dict."""
    obj = _normalize(raw)
    # birth_ref_index / death_ref_index are already ints in JSON
    return obj


def _normalize_family(raw: Dict) -> Dict:
    """Normalise a raw Gramps family JSON dict (renames type → relationship)."""
    obj = _normalize(raw)
    obj["relationship"] = obj.pop("type", "Married")
    return obj


def _normalize_place(raw: Dict) -> Dict:
    """Normalise a raw Gramps place JSON dict."""
    obj = _normalize(raw)
    # Gramps stores place_type as typed obj; after _normalize it's a string.
    # Re-key to match XML parser output.
    obj["place_type"] = obj.pop("place_type", obj.pop("type", ""))
    return obj


# Generic normaliser for types that need no special treatment
def _normalize_generic(raw: Dict) -> Dict:
    """Normalise a raw Gramps JSON dict."""
    return _normalize(raw)


_NORMALISE: Dict[str, Any] = {
    "person": _normalize_person,
    "family": _normalize_family,
    "place": _normalize_place,
}

# ---------------------------------------------------------------------------
# Denormalisation  (our dict format → Gramps JSON)
# ---------------------------------------------------------------------------


def _denorm_type(value: str, cls: str) -> Dict:
    """
    Convert a plain type string back to a Gramps typed object.

    Args:
        value: Display string, e.g. ``"Birth"``.
        cls:   Gramps class name, e.g. ``"EventType"``.

    Returns:
        Dict like ``{"_class": "EventType", "value": 12, "string": ""}``.
    """
    rev = _REV_TYPE_MAPS.get(cls, {})
    custom = _CUSTOM.get(cls, 0)
    num = rev.get(value, custom)
    return {"_class": cls, "value": num, "string": value if num == custom else ""}


def _denorm_date(d: Any) -> Dict:
    """
    Convert our date dict back to Gramps JSON date format.

    Args:
        d: Our date dict with ``dateval``, ``modifier``, ``quality``, ``string``.

    Returns:
        Gramps JSON date dict with ``_class`` and ``text`` key.
    """
    _empty_dateval = [0, 0, 0, False]

    def _safe_dateval(raw: Any) -> list:
        # Gramps _display_gregorian accesses dateval[2] and dateval[3].
        # Pad any short list to the minimum 4 elements [day, month, year, slash].
        dv = list(raw) if isinstance(raw, (list, tuple)) and raw else list(_empty_dateval)
        while len(dv) < 3:
            dv.append(0)
        if len(dv) == 3:
            dv.append(False)
        return dv

    if not isinstance(d, dict):
        return {"_class": "Date", "calendar": 0, "modifier": 0, "quality": 0,
                "dateval": list(_empty_dateval), "text": "", "sortval": 0, "newyear": 0, "format": None}
    return {
        "_class": "Date",
        "calendar": d.get("calendar", 0),
        "modifier": d.get("modifier", 0),
        "quality": d.get("quality", 0),
        "dateval": _safe_dateval(d.get("dateval")),
        "text": d.get("string", ""),
        "sortval": d.get("sortval", 0),
        "newyear": d.get("newyear", 0),
        "format": d.get("format"),
    }


# ---------------------------------------------------------------------------
# LazyDict — on-demand SQLite proxy replacing the full in-memory dicts
# ---------------------------------------------------------------------------


class LazyDict:
    """
    Dict-like proxy that queries SQLite on every access.

    Implements the subset of the dict interface used by
    :class:`GrampsXmlDB` and its subclasses:
    ``get``, ``__getitem__``, ``__contains__``, ``__len__``,
    ``values``, ``__iter__``, ``__setitem__`` (no-op — data lives in DB).

    Every read goes directly to SQLite so the agent always sees the
    current state of the database without needing a reload.
    """

    def __init__(self, conn: sqlite3.Connection, table: str, normalise):
        """
        Args:
            conn:      Open SQLite connection (row_factory must be sqlite3.Row).
            table:     Table name (e.g. ``"person"``).
            normalise: Callable that converts a raw Gramps JSON dict to our
                       normalised format.
        """
        self._conn = conn
        self._table = table
        self._normalise = normalise

    def _fetch_one(self, handle: str) -> Optional[Dict]:
        """Fetch and normalise a single row by handle, or None."""
        try:
            row = self._conn.execute(  # noqa: S608
                f"SELECT json_data FROM {self._table} WHERE handle=?",
                (handle,),
            ).fetchone()
        except sqlite3.OperationalError:
            return None
        if row is None:
            return None
        try:
            return self._normalise(json.loads(row["json_data"]))
        except Exception:
            return None

    def get(self, handle: Optional[str], default=None):
        """Return the normalised object for *handle*, or *default*."""
        if not handle:
            return default
        result = self._fetch_one(handle)
        return result if result is not None else default

    def __getitem__(self, handle: str) -> Dict:
        result = self._fetch_one(handle)
        if result is None:
            raise KeyError(handle)
        return result

    def __contains__(self, handle: object) -> bool:
        if not isinstance(handle, str):
            return False
        try:
            row = self._conn.execute(  # noqa: S608
                f"SELECT 1 FROM {self._table} WHERE handle=?", (handle,)
            ).fetchone()
            return row is not None
        except sqlite3.OperationalError:
            return False

    def __len__(self) -> int:
        try:
            row = self._conn.execute(  # noqa: S608
                f"SELECT COUNT(*) FROM {self._table}"
            ).fetchone()
            return row[0] if row else 0
        except sqlite3.OperationalError:
            return 0

    def values(self) -> List[Dict]:
        """Return all normalised objects from this table."""
        try:
            rows = self._conn.execute(  # noqa: S608
                f"SELECT json_data FROM {self._table}"
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        result = []
        for row in rows:
            try:
                result.append(self._normalise(json.loads(row["json_data"])))
            except Exception:
                pass
        return result

    def __iter__(self):
        try:
            rows = self._conn.execute(  # noqa: S608
                f"SELECT handle FROM {self._table}"
            ).fetchall()
            return iter(row["handle"] for row in rows)
        except sqlite3.OperationalError:
            return iter([])

    def __setitem__(self, handle: str, value: Dict) -> None:
        """No-op — data is persisted to SQLite by put(), not via dict assignment."""


# ---------------------------------------------------------------------------
# Database loader
# ---------------------------------------------------------------------------


def _load_sqlite(db_path: str, read_only: bool = False) -> "GrampsSqliteDB":
    """
    Open a Gramps SQLite database and return a lazy-loading DB instance.

    No data is loaded eagerly — every read goes directly to SQLite so
    the agent always sees the current state without a reload.

    Args:
        db_path:   Absolute path to the Gramps ``sqlite.db`` file.
        read_only: If True, open in read-only mode (``PRAGMA query_only``).
                   Write attempts will raise :class:`GrampsAPIError`.

    Returns:
        A :class:`GrampsSqliteDB` instance ready for lazy reads and writes.

    Raises:
        GrampsAPIError: If the file cannot be opened or is not a Gramps DB.
    """
    try:
        conn = sqlite3.connect(db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        if read_only:
            conn.execute("PRAGMA query_only=ON")
        else:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
    except Exception as exc:
        raise GrampsAPIError(
            f"Cannot open Gramps SQLite DB '{db_path}': {exc}"
        ) from exc

    logger.info("Opened Gramps SQLite '%s' (read_only=%s)", db_path, read_only)
    return GrampsSqliteDB(conn=conn, db_path=db_path, read_only=read_only)


# ---------------------------------------------------------------------------
# GrampsSqliteDB
# ---------------------------------------------------------------------------


class GrampsSqliteDB(GrampsXmlDB):
    """
    Lazy-loading Gramps database backed by a live SQLite file.

    Inherits all read / traversal / timeline methods from
    :class:`GrampsXmlDB` — they all use ``self.people``,
    ``self.families``, etc. which are now :class:`LazyDict` instances
    that query SQLite on every access.

    This means:
    - No startup delay — nothing is loaded eagerly.
    - Always fresh data — changes made by Gramps Desktop are visible
      immediately on the next read without calling ``reload_database``.
    - Write-through — :meth:`put` writes to SQLite; the next read
      returns the updated data from the DB.
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        db_path: str,
        read_only: bool = False,
        source_name: str = "",
    ):
        """
        Initialise with an open SQLite connection.

        No data is loaded at construction time — all reads go to SQLite
        on demand via :class:`LazyDict` proxies.

        Args:
            conn:        Open ``sqlite3.Connection`` (row_factory=sqlite3.Row).
            db_path:     Path to the SQLite file (used for logging).
            read_only:   If True, :meth:`put` raises :class:`GrampsAPIError`.
            source_name: Display name shown in log messages.
        """
        # Initialise parent with empty dicts; we replace them immediately.
        super().__init__(
            source_name=source_name or db_path,
            people={}, families={}, events={}, places={},
            sources={}, citations={}, notes={}, media={}, repositories={},
        )
        self._conn = conn
        self._db_path = db_path
        self._read_only = read_only
        self._normalise_map = {
            "person":     _normalize_person,
            "family":     _normalize_family,
            "event":      _normalize_generic,
            "place":      _normalize_place,
            "source":     _normalize_generic,
            "citation":   _normalize_generic,
            "note":       _normalize_generic,
            "media":      _normalize_generic,
            "repository": _normalize_generic,
        }
        # Replace static dicts with live SQLite proxies.
        self.people       = LazyDict(conn, "person",     _normalize_person)
        self.families     = LazyDict(conn, "family",     _normalize_family)
        self.events       = LazyDict(conn, "event",      _normalize_generic)
        self.places       = LazyDict(conn, "place",      _normalize_place)
        self.sources      = LazyDict(conn, "source",     _normalize_generic)
        self.citations    = LazyDict(conn, "citation",   _normalize_generic)
        self.notes        = LazyDict(conn, "note",       _normalize_generic)
        self.media        = LazyDict(conn, "media",      _normalize_generic)
        self.repositories = LazyDict(conn, "repository", _normalize_generic)

    def get_by_id(self, obj_type: str, gramps_id: str) -> Optional[Dict]:
        """
        Look up an object by Gramps ID using the indexed SQL column.

        Faster than the parent's linear scan through ``values()``.

        Args:
            obj_type:  One of ``person``, ``family``, ``event``, etc.
            gramps_id: Gramps ID string, e.g. ``"I0001"``.

        Returns:
            Normalised dict or ``None`` if not found.
        """
        table = _TABLE.get(obj_type)
        normalise = self._normalise_map.get(obj_type)
        if not table or not normalise:
            return None
        try:
            row = self._conn.execute(  # noqa: S608
                f"SELECT json_data FROM {table} WHERE gramps_id=?",
                (gramps_id,),
            ).fetchone()
        except sqlite3.OperationalError:
            return None
        if row is None:
            return None
        try:
            return normalise(json.loads(row["json_data"]))
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Handle / ID generation
    # ------------------------------------------------------------------

    def new_handle(self) -> str:
        """Generate a unique Gramps object handle."""
        return uuid.uuid4().hex

    def _next_id(self, obj_type: str) -> str:
        """
        Return the next available Gramps ID for an object type.

        Args:
            obj_type: One of ``person``, ``family``, ``event``, etc.

        Returns:
            Next ID string, e.g. ``"I0043"``.
        """
        prefix = _ID_PREFIX[obj_type]
        table = _TABLE[obj_type]
        cur = self._conn.execute(
            f"SELECT gramps_id FROM {table} WHERE gramps_id LIKE ?",  # noqa: S608
            (f"{prefix}%",),
        )
        max_num = 0
        for row in cur.fetchall():
            gid = row[0] or ""
            try:
                max_num = max(max_num, int(gid[len(prefix):]))
            except ValueError:
                pass
        return f"{prefix}{max_num + 1:04d}"

    # ------------------------------------------------------------------
    # Write  (INSERT / UPDATE)
    # ------------------------------------------------------------------

    def put(self, obj_type: str, obj: Dict) -> Dict:
        """
        Persist an object to SQLite.

        If ``obj`` has no ``handle``, a new one is generated.
        If ``obj`` has no ``gramps_id``, the next sequential ID is assigned.
        The ``change`` timestamp is always set to now.

        For updates, the existing Gramps JSON is read from the DB, the
        supplied fields are merged in, and the result is written back so
        that fields we do not explicitly touch are preserved.

        Args:
            obj_type: One of ``person``, ``family``, ``event``, etc.
            obj:      Our normalised dict (Gramps Web API shape).

        Returns:
            The stored dict (with handle and gramps_id filled in).

        Raises:
            GrampsAPIError: If the database is read-only or on write errors.
        """
        if self._read_only:
            raise GrampsAPIError(
                "Cannot write: database is open in read-only mode because "
                "another process (Gramps Desktop?) holds the lock file. "
                "Close Gramps Desktop first, then call reload_database."
            )
        handle = obj.get("handle") or self.new_handle()
        obj = {**obj, "handle": handle}

        table = _TABLE[obj_type]
        existing_raw: Optional[Dict] = None

        cur = self._conn.execute(
            f"SELECT json_data FROM {table} WHERE handle = ?",  # noqa: S608
            (handle,),
        )
        row = cur.fetchone()
        if row:
            try:
                existing_raw = json.loads(row[0])
            except Exception:
                pass

        if not obj.get("gramps_id"):
            if existing_raw:
                existing_id = existing_raw.get("gramps_id")
                obj["gramps_id"] = existing_id or self._next_id(obj_type)
            else:
                obj["gramps_id"] = self._next_id(obj_type)

        obj["change"] = int(time.time())

        gramps_json = _build_gramps_json(obj_type, obj, existing_raw)

        # Fix A: auto-update birth/death ref indices so they always match event_ref_list
        if obj_type == "person":
            birth_idx, death_idx = _compute_birth_death_indices(
                self._conn, gramps_json.get("event_ref_list", [])
            )
            gramps_json["birth_ref_index"] = birth_idx
            gramps_json["death_ref_index"] = death_idx
            obj = {**obj, "birth_ref_index": birth_idx, "death_ref_index": death_idx}

        json_str = json.dumps(gramps_json, ensure_ascii=False)

        try:
            with self._conn:
                secondaries = _secondaries(obj_type, obj)
                if existing_raw is None:
                    cols = ["handle", "json_data"] + list(secondaries.keys())
                    placeholders = ",".join("?" * len(cols))
                    vals: List = [handle, json_str] + list(secondaries.values())
                    col_str = ",".join(cols)
                    sql = (  # noqa: S608
                        f"INSERT INTO {table} ({col_str}) VALUES ({placeholders})"
                    )
                    self._conn.execute(sql, vals)
                else:
                    update_cols = ["json_data"] + list(secondaries.keys())
                    set_clause = ", ".join(f"{c}=?" for c in update_cols)
                    vals = [json_str] + list(secondaries.values()) + [handle]
                    self._conn.execute(
                        f"UPDATE {table} SET {set_clause} WHERE handle=?",  # noqa: S608
                        vals,
                    )
                # Fix B: update parent_family_list of children when family written with child data
                if obj_type == "family" and (
                    "child_handles" in obj or "child_ref_list" in obj
                ):
                    child_handles = [
                        cr["ref"]
                        for cr in gramps_json.get("child_ref_list", [])
                        if isinstance(cr, dict) and cr.get("ref")
                    ]
                    _update_parent_family_list(self._conn, handle, child_handles)
                # Fix C: update family_list of father/mother when family written with parent handles
                if obj_type == "family":
                    for parent_key in ("father_handle", "mother_handle"):
                        if parent_key not in obj:
                            continue
                        parent_handle = gramps_json.get(parent_key)
                        if parent_handle:
                            _update_person_family_list(self._conn, handle, parent_handle)
                # Fix D: update child_ref_list of families when person written with parent_family_list
                if obj_type == "person" and "parent_family_list" in obj:
                    parent_family_handles = gramps_json.get("parent_family_list", [])
                    _update_family_child_ref_list(self._conn, handle, parent_family_handles)
        except sqlite3.Error as exc:
            raise GrampsAPIError(
                f"SQLite write error for {obj_type}/{handle}: {exc}"
            ) from exc

        # No cache to update — next read fetches fresh from SQLite.
        return obj

    def delete(self, obj_type: str, handle: str) -> None:
        """Delete an object from SQLite by handle."""
        if self._read_only:
            raise GrampsAPIError(
                "Cannot write: database is open in read-only mode."
            )
        table = _TABLE.get(obj_type)
        if table is None:
            raise GrampsAPIError(f"Unknown object type for delete: {obj_type}")
        try:
            with self._conn:
                cursor = self._conn.execute(
                    f"DELETE FROM {table} WHERE handle = ?",  # noqa: S608
                    (handle,),
                )
        except sqlite3.Error as exc:
            raise GrampsAPIError(
                f"SQLite delete error for {obj_type}/{handle}: {exc}"
            ) from exc
        if cursor.rowcount == 0:
            raise GrampsAPIError(f"No {obj_type} found with handle {handle!r}")

    def close(self):
        """Close the SQLite connection."""
        self._conn.close()


# ---------------------------------------------------------------------------
# JSON builder  (our dict → Gramps internal JSON)
# ---------------------------------------------------------------------------


def _build_gramps_json(obj_type: str, obj: Dict, existing: Optional[Dict]) -> Dict:
    """
    Build the Gramps-format JSON dict for storage.

    Merges *obj* into *existing* (if present) so that fields we don't
    touch are preserved.  Typed fields are denormalised back to ``_class``
    objects.

    Args:
        obj_type: One of ``person``, ``family``, ``event``, etc.
        obj:      Our normalised dict.
        existing: Current Gramps JSON from the database (may be None).

    Returns:
        Gramps-format JSON dict ready for ``json.dumps``.
    """
    base = existing.copy() if existing else _empty_gramps_json(obj_type)
    _merge_into(base, obj, obj_type)
    base["handle"] = obj["handle"]
    base["gramps_id"] = obj.get("gramps_id", base.get("gramps_id", ""))
    base["change"] = obj.get("change", int(time.time()))
    base["private"] = bool(obj.get("private", False))
    return base


def _denorm_event_ref(eref: Any) -> Any:
    """Denormalise a single event-ref dict back to Gramps JSON format."""
    if not isinstance(eref, dict):
        return eref
    result = dict(eref)
    result.setdefault("_class", "EventRef")
    if isinstance(result.get("role"), str):
        result["role"] = _denorm_type(result["role"], "EventRoleType")
    result.setdefault("note_list", [])
    result.setdefault("citation_list", [])
    result.setdefault("attribute_list", [])
    result.setdefault("private", False)
    return result


def _compute_birth_death_indices(conn: Any, event_ref_list: List[Dict]) -> Tuple[int, int]:
    """
    Scan event_ref_list and return the first Birth (12) and Death (13) indices.

    Queries the event table for each ref handle to determine event type.
    Returns -1 for types not found.

    Args:
        conn: Open sqlite3.Connection.
        event_ref_list: List of EventRef dicts in Gramps JSON format.

    Returns:
        Tuple (birth_ref_index, death_ref_index).
    """
    birth_idx = -1
    death_idx = -1
    for i, eref in enumerate(event_ref_list):
        if not isinstance(eref, dict):
            continue
        handle = eref.get("ref")
        if not handle:
            continue
        row = conn.execute(
            "SELECT json_data FROM event WHERE handle = ?",  # noqa: S608
            (handle,),
        ).fetchone()
        if not row:
            continue
        try:
            event_data = json.loads(row[0])
        except Exception:
            continue
        etype = event_data.get("type", {})
        val = etype.get("value") if isinstance(etype, dict) else None
        if val == 12 and birth_idx == -1:
            birth_idx = i
        if val == 13 and death_idx == -1:
            death_idx = i
    return birth_idx, death_idx


def _update_parent_family_list(conn: Any, family_handle: str, child_handles: List[str]) -> None:
    """
    Add family_handle to parent_family_list of each child person in the DB.

    Called within the same SQLite transaction as the family write. Only adds;
    never removes (removal is handled by the remove_child_from_family tool).

    Args:
        conn: Open sqlite3.Connection within an active transaction.
        family_handle: The family handle to add to each child's parent_family_list.
        child_handles: List of child person handles to update.
    """
    for child_handle in child_handles:
        row = conn.execute(
            "SELECT json_data FROM person WHERE handle = ?",  # noqa: S608
            (child_handle,),
        ).fetchone()
        if not row:
            continue
        try:
            person_data = json.loads(row[0])
        except Exception:
            continue
        pfl = person_data.get("parent_family_list", [])
        if family_handle not in pfl:
            pfl.append(family_handle)
            person_data["parent_family_list"] = pfl
            person_data["change"] = int(time.time())
            conn.execute(
                "UPDATE person SET json_data = ?, change = ? WHERE handle = ?",  # noqa: S608
                (json.dumps(person_data, ensure_ascii=False), person_data["change"], child_handle),
            )


def _update_person_family_list(conn: Any, family_handle: str, person_handle: str) -> None:
    """
    Add family_handle to family_list of a spouse/parent person in the DB.

    Called within the same SQLite transaction as the family write. Only adds;
    never removes (removal is handled by dedicated tools).

    Args:
        conn: Open sqlite3.Connection within an active transaction.
        family_handle: The family handle to add to the person's family_list.
        person_handle: Handle of the father or mother to update.
    """
    row = conn.execute(
        "SELECT json_data FROM person WHERE handle = ?",  # noqa: S608
        (person_handle,),
    ).fetchone()
    if not row:
        return
    try:
        person_data = json.loads(row[0])
    except Exception:
        return
    fl = person_data.get("family_list", [])
    if family_handle not in fl:
        fl.append(family_handle)
        person_data["family_list"] = fl
        person_data["change"] = int(time.time())
        conn.execute(
            "UPDATE person SET json_data = ?, change = ? WHERE handle = ?",  # noqa: S608
            (json.dumps(person_data, ensure_ascii=False), person_data["change"], person_handle),
        )


def _update_family_child_ref_list(
    conn: Any, person_handle: str, family_handles: List[str]
) -> None:
    """
    Add person_handle to child_ref_list of each family in family_handles.

    Called within the same SQLite transaction as the person write. Only adds;
    never removes. Default relationship type is Birth (ChildRefType value 1).

    Args:
        conn: Open sqlite3.Connection within an active transaction.
        person_handle: The person handle to add as a child.
        family_handles: Family handles from the person's parent_family_list.
    """
    for family_handle in family_handles:
        row = conn.execute(
            "SELECT json_data FROM family WHERE handle = ?",  # noqa: S608
            (family_handle,),
        ).fetchone()
        if not row:
            continue
        try:
            family_data = json.loads(row[0])
        except Exception:
            continue
        child_ref_list = family_data.get("child_ref_list", [])
        existing = [
            cr["ref"] if isinstance(cr, dict) else cr
            for cr in child_ref_list
        ]
        if person_handle not in existing:
            child_ref_list.append({
                "_class": "ChildRef",
                "ref": person_handle,
                "frel": {"_class": "ChildRefType", "value": 1, "string": ""},
                "mrel": {"_class": "ChildRefType", "value": 1, "string": ""},
                "private": False,
                "citation_list": [],
                "note_list": [],
            })
            family_data["child_ref_list"] = child_ref_list
            family_data["change"] = int(time.time())
            conn.execute(
                "UPDATE family SET json_data = ?, change = ? WHERE handle = ?",  # noqa: S608
                (
                    json.dumps(family_data, ensure_ascii=False),
                    family_data["change"],
                    family_handle,
                ),
            )


def _denorm_child_ref(cref: Any) -> Any:
    """Denormalise a single child-ref dict back to Gramps JSON format."""
    if not isinstance(cref, dict):
        return cref
    result = dict(cref)
    result.setdefault("_class", "ChildRef")
    if isinstance(result.get("frel"), str):
        result["frel"] = _denorm_type(result["frel"], "ChildRefType")
    result.setdefault("frel", {"_class": "ChildRefType", "value": 1, "string": ""})
    if isinstance(result.get("mrel"), str):
        result["mrel"] = _denorm_type(result["mrel"], "ChildRefType")
    result.setdefault("mrel", {"_class": "ChildRefType", "value": 1, "string": ""})
    result.setdefault("private", False)
    result.setdefault("citation_list", [])
    result.setdefault("note_list", [])
    return result


def _denorm_place_ref(ref: Any) -> Any:
    """Denormalise a single place-ref dict back to Gramps JSON format."""
    if not isinstance(ref, dict):
        return ref
    result = dict(ref)
    result["_class"] = "PlaceRef"
    result["date"] = _denorm_date(result.get("date", {}))
    return result


def _denorm_url(url: Any) -> Any:
    """Denormalise a single URL dict back to Gramps JSON format."""
    if not isinstance(url, dict):
        return url
    path = url.get("path") or url.get("href", "")
    desc = url.get("desc") or url.get("description", "")
    url_type = url.get("type", {})
    if not isinstance(url_type, dict):
        url_type = {"_class": "UrlType", "value": 0, "string": ""}
    elif "_class" not in url_type:
        url_type = {"_class": "UrlType", **url_type}
    return {
        "_class": "Url",
        "path": path,
        "desc": desc,
        "type": url_type,
        "private": bool(url.get("private", False)),
    }


def _denorm_place_name(name: Any) -> Any:
    """Denormalise a place name dict back to Gramps JSON PlaceName format."""
    if not isinstance(name, dict):
        return name
    return {
        "_class": "PlaceName",
        "value": name.get("value", ""),
        "date": _denorm_date(name.get("date", {})),
        "lang": name.get("lang", ""),
    }


def _denorm_media_ref(ref: Any) -> Any:
    """Denormalise a single media-ref dict back to Gramps JSON MediaRef format."""
    if not isinstance(ref, dict):
        return ref
    result = dict(ref)
    result["_class"] = "MediaRef"
    result.setdefault("rect", None)
    result.setdefault("private", False)
    result.setdefault("note_list", [])
    result.setdefault("attribute_list", [])
    result.setdefault("citation_list", [])
    return result


def _denorm_attribute(attr: Any) -> Any:
    """Denormalise a single attribute dict back to Gramps JSON Attribute format."""
    if not isinstance(attr, dict):
        return attr
    result = dict(attr)
    result["_class"] = "Attribute"
    atype = result.pop("type", {})
    if isinstance(atype, str):
        atype = {"_class": "AttributeType", "value": 0, "string": atype}
    elif not isinstance(atype, dict):
        atype = {"_class": "AttributeType", "value": 0, "string": ""}
    elif "_class" not in atype:
        atype["_class"] = "AttributeType"
    result["type"] = atype
    result.setdefault("value", "")
    result.setdefault("private", False)
    result.setdefault("citation_list", [])
    result.setdefault("note_list", [])
    return result


def _denorm_address(addr: Any) -> Any:
    """Denormalise a single address dict back to Gramps JSON Address format."""
    if not isinstance(addr, dict):
        return addr
    result = dict(addr)
    result["_class"] = "Address"
    result["date"] = _denorm_date(result.get("date", {}))
    for field in ("street", "locality", "city", "county", "state",
                  "country", "postal", "phone"):
        result.setdefault(field, "")
    result.setdefault("private", False)
    result.setdefault("citation_list", [])
    result.setdefault("note_list", [])
    return result


def _denorm_person_ref(ref: Any) -> Any:
    """Denormalise a single person-ref dict back to Gramps JSON PersonRef format."""
    if not isinstance(ref, dict):
        return ref
    result = dict(ref)
    result["_class"] = "PersonRef"
    result.setdefault("rel", "")
    result.setdefault("private", False)
    result.setdefault("note_list", [])
    result.setdefault("citation_list", [])
    result.setdefault("attribute_list", [])
    return result


def _denorm_lds_ord(ord_: Any) -> Any:
    """Denormalise a single LDS ordinance dict back to Gramps JSON LdsOrd format."""
    if not isinstance(ord_, dict):
        return ord_
    result = dict(ord_)
    result["_class"] = "LdsOrd"
    result["date"] = _denorm_date(result.get("date", {}))
    result.setdefault("temple", "")
    result.setdefault("place", None)
    result.setdefault("family_handle", None)
    result.setdefault("status", {"_class": "LdsOrdStatus", "value": 0, "string": ""})
    result.setdefault("private", False)
    result.setdefault("citation_list", [])
    result.setdefault("note_list", [])
    return result


def _denorm_repo_ref(ref: Any) -> Any:
    """Denormalise a single repo-ref dict back to Gramps JSON RepoRef format."""
    if not isinstance(ref, dict):
        return ref
    result = dict(ref)
    result["_class"] = "RepoRef"
    if "callno" in result and "call_number" not in result:
        result["call_number"] = result.pop("callno")
    result.setdefault("call_number", "")
    # type (string or missing) → media_type SourceMediaType object
    mtype = result.pop("type", result.pop("media_type", {}))
    if not isinstance(mtype, dict):
        mtype = {"_class": "SourceMediaType", "value": 0, "string": ""}
    elif "_class" not in mtype:
        mtype["_class"] = "SourceMediaType"
    result["media_type"] = mtype
    result.setdefault("note_list", [])
    result.setdefault("private", False)
    return result


def _denorm_note_text(text: Any) -> Any:
    """Denormalise note text to Gramps JSON StyledText format."""
    if not isinstance(text, dict):
        return {"_class": "StyledText", "string": str(text) if text else "", "tags": []}
    result = dict(text)
    result["_class"] = "StyledText"
    result.setdefault("string", "")
    result.setdefault("tags", [])
    return result


def _merge_into(base: Dict, patch: Dict, obj_type: str) -> None:
    """Apply normalised patch fields onto an existing Gramps JSON base."""
    field_map = _FIELD_PATCH_MAP.get(obj_type, {})
    for key, val in patch.items():
        if key in ("handle", "gramps_id", "change", "private"):
            continue
        gramps_key = field_map.get(key, key)
        converter = _FIELD_CONVERTERS.get((obj_type, key))
        if converter:
            base[gramps_key] = converter(val)
        elif key == "date":
            base[gramps_key] = _denorm_date(val)
        elif key in ("primary_name",):
            base[gramps_key] = _denorm_name(val)
        elif key == "event_ref_list" and isinstance(val, list):
            base[gramps_key] = [_denorm_event_ref(e) for e in val]
        elif key == "child_ref_list" and isinstance(val, list):
            base[gramps_key] = [_denorm_child_ref(c) for c in val]
        elif key == "child_handles" and isinstance(val, list):
            # FamilySaveParams convenience: flat handle list → child_ref_list
            base["child_ref_list"] = [_denorm_child_ref({"ref": h}) for h in val]
        elif obj_type == "place" and key == "name" and isinstance(val, dict):
            base["name"] = _denorm_place_name(val)
            new_val = val.get("value", "")
            if new_val:
                existing_title = base.get("title", "")
                if existing_title and "," in existing_title:
                    suffix = existing_title.split(",", 1)[1]
                    base["title"] = f"{new_val},{suffix}"
                else:
                    base["title"] = new_val
        elif obj_type == "place" and key == "placeref_list" and isinstance(val, list):
            base[gramps_key] = [_denorm_place_ref(r) for r in val]
        elif key == "urls" and isinstance(val, list):
            base[gramps_key] = [_denorm_url(u) for u in val]
        elif key == "reporef_list" and isinstance(val, list):
            base[gramps_key] = [_denorm_repo_ref(r) for r in val]
        elif key == "media_list" and isinstance(val, list):
            base[gramps_key] = [_denorm_media_ref(m) for m in val]
        elif key == "attribute_list" and isinstance(val, list):
            base[gramps_key] = [_denorm_attribute(a) for a in val]
        elif key == "address_list" and isinstance(val, list):
            base[gramps_key] = [_denorm_address(a) for a in val]
        elif key == "person_ref_list" and isinstance(val, list):
            base[gramps_key] = [_denorm_person_ref(r) for r in val]
        elif key == "lds_ord_list" and isinstance(val, list):
            base[gramps_key] = [_denorm_lds_ord(o) for o in val]
        elif obj_type == "note" and key == "text" and isinstance(val, dict):
            base[gramps_key] = _denorm_note_text(val)
        elif isinstance(val, list):
            base[gramps_key] = val  # plain handle lists: note_list, citation_list, …
        elif val is not None:
            base[gramps_key] = val


def _denorm_name(name: Dict) -> Dict:
    """Convert our name dict back to Gramps JSON name format."""
    if not isinstance(name, dict):
        return name
    result = {"_class": "Name"}
    result["first_name"] = name.get("first_name") or ""
    result["suffix"] = name.get("suffix") or ""
    result["title"] = name.get("title") or ""
    result["call"] = name.get("call") or ""
    result["nick"] = name.get("nick") or ""
    result["famnick"] = name.get("famnick") or ""
    result["group_as"] = name.get("group_as") or ""
    result["sort_as"] = name.get("sort_as", 0)
    result["display_as"] = name.get("display_as", 0)
    result["private"] = bool(name.get("private", False))
    name_type_str = name.get("type", "Birth Name")
    result["type"] = _denorm_type(name_type_str, "NameType")
    result["date"] = _denorm_date(name.get("date", {}))
    result["citation_list"] = name.get("citation_list", [])
    result["note_list"] = name.get("note_list", [])
    surname_list = []
    for sn in name.get("surname_list", []):
        surname_list.append({
            "_class": "Surname",
            "surname": sn.get("surname") or "",
            "prefix": sn.get("prefix") or "",
            "primary": bool(sn.get("primary", True)),
            "connector": sn.get("connector") or "",
            "origintype": {"_class": "NameOriginType", "value": 1, "string": ""},
        })
    result["surname_list"] = surname_list
    return result


# Field renames: our key → Gramps JSON key (only where they differ)
_FIELD_PATCH_MAP: Dict[str, Dict[str, str]] = {
    "family": {"relationship": "type"},
}

# Per-(obj_type, field) converters
_FIELD_CONVERTERS: Dict[Tuple[str, str], Any] = {
    ("event", "type"): lambda v: _denorm_type(v, "EventType"),
    ("family", "relationship"): lambda v: _denorm_type(v, "FamilyRelType"),
    ("place", "place_type"): lambda v: _denorm_type(v, "PlaceType"),
    ("note", "type"): lambda v: _denorm_type(v, "NoteType"),
    ("repository", "type"): lambda v: _denorm_type(v, "RepositoryType"),
}


def _empty_gramps_json(obj_type: str) -> Dict:
    """Return a minimal empty Gramps JSON dict for a new object."""
    templates: Dict[str, Dict] = {
        "person": {
            "_class": "Person", "gender": 2,
            "primary_name": {
                "_class": "Name", "first_name": "", "surname_list": [],
                "suffix": "", "title": "", "call": "", "nick": "",
                "famnick": "", "group_as": "", "sort_as": 0, "display_as": 0,
                "private": False, "citation_list": [], "note_list": [],
                "type": {"_class": "NameType", "value": 2, "string": ""},
                "date": _denorm_date({})
            },
            "alternate_names": [], "death_ref_index": -1, "birth_ref_index": -1,
            "event_ref_list": [], "family_list": [], "parent_family_list": [],
            "media_list": [], "address_list": [], "attribute_list": [],
            "urls": [], "lds_ord_list": [], "citation_list": [],
            "note_list": [], "tag_list": [], "person_ref_list": [],
        },
        "family": {
            "_class": "Family",
            "father_handle": None, "mother_handle": None,
            "child_ref_list": [], "event_ref_list": [], "media_list": [],
            "attribute_list": [], "lds_ord_list": [], "citation_list": [],
            "note_list": [], "tag_list": [],
            "type": {"_class": "FamilyRelType", "value": 0, "string": ""},
        },
        "event": {
            "_class": "Event",
            "type": {"_class": "EventType", "value": 12, "string": ""},
            "date": _denorm_date({}), "description": "", "place": None,
            "citation_list": [], "note_list": [], "media_list": [],
            "attribute_list": [], "tag_list": [],
        },
        "place": {
            "_class": "Place", "title": "", "long": "", "lat": "", "code": "",
            "place_type": {"_class": "PlaceType", "value": -1, "string": ""},
            "alt_names": [], "placeref_list": [],
            "name": {"_class": "PlaceName", "value": "", "date": _denorm_date({}), "lang": ""},
            "alt_loc": [], "urls": [], "media_list": [], "citation_list": [],
            "note_list": [], "tag_list": [],
        },
        "source": {
            "_class": "Source", "title": "", "author": "", "pubinfo": "",
            "abbrev": "", "note_list": [], "media_list": [], "reporef_list": [],
            "attribute_list": [], "tag_list": [], "data_maps": [],
        },
        "citation": {
            "_class": "Citation", "page": "", "confidence": 2,
            "source_handle": None, "date": _denorm_date({}),
            "note_list": [], "media_list": [], "attribute_list": [], "tag_list": [],
        },
        "note": {
            "_class": "Note", "format": 0,
            "text": {"_class": "StyledText", "string": "", "tags": []},
            "type": {"_class": "NoteType", "value": 1, "string": ""}, "tag_list": [],
        },
        "media": {
            "_class": "MediaObject", "path": "", "mime": "", "desc": "",
            "checksum": "", "date": _denorm_date({}),
            "note_list": [], "citation_list": [], "attribute_list": [],
            "tag_list": [], "media_list": [],
        },
        "repository": {
            "_class": "Repository", "name": "",
            "type": {"_class": "RepositoryType", "value": 0, "string": ""},
            "address_list": [], "urls": [], "note_list": [], "tag_list": [],
        },
    }
    return templates.get(obj_type, {"_class": obj_type.title()})


# ---------------------------------------------------------------------------
# Secondary column builders
# ---------------------------------------------------------------------------


def _secondaries(obj_type: str, obj: Dict) -> Dict[str, Any]:
    """
    Build the secondary (indexed) column values for a SQLite row.

    Args:
        obj_type: Object type string.
        obj:      Normalised dict with at least ``gramps_id`` and ``change``.

    Returns:
        Dict of column name → value for all secondary columns.
    """
    gid = obj.get("gramps_id", "")
    change = obj.get("change", int(time.time()))
    private = 1 if obj.get("private") else 0

    if obj_type == "person":
        pn = obj.get("primary_name", {})
        sl = pn.get("surname_list", [])
        return {
            "gramps_id": gid, "gender": obj.get("gender", 2),
            "given_name": pn.get("first_name") or "",
            "surname": sl[0].get("surname") or "" if sl else "",
            "birth_ref_index": obj.get("birth_ref_index", -1),
            "death_ref_index": obj.get("death_ref_index", -1),
            "change": change, "private": private,
        }
    if obj_type == "family":
        return {
            "gramps_id": gid,
            "father_handle": obj.get("father_handle") or None,
            "mother_handle": obj.get("mother_handle") or None,
            "change": change, "private": private,
        }
    if obj_type == "event":
        return {
            "gramps_id": gid, "description": obj.get("description", ""),
            "place": obj.get("place") or None,
            "change": change, "private": private,
        }
    if obj_type == "place":
        return {
            "gramps_id": gid, "title": obj.get("title", ""),
            "long": obj.get("long", ""), "lat": obj.get("lat", ""),
            "code": obj.get("code", ""),
            "enclosed_by": (obj.get("placeref_list") or [{}])[0].get("ref") or None
            if obj.get("placeref_list") else None,
            "change": change, "private": private,
        }
    if obj_type == "source":
        return {
            "gramps_id": gid, "title": obj.get("title", ""),
            "author": obj.get("author", ""), "pubinfo": obj.get("pubinfo", ""),
            "abbrev": obj.get("abbrev", ""),
            "change": change, "private": private,
        }
    if obj_type == "citation":
        return {
            "gramps_id": gid, "page": obj.get("page", ""),
            "confidence": obj.get("confidence", 2),
            "source_handle": obj.get("source_handle") or None,
            "change": change, "private": private,
        }
    if obj_type == "note":
        return {
            "gramps_id": gid,
            "format": 1 if obj.get("format") else 0,
            "change": change, "private": private,
        }
    if obj_type == "media":
        return {
            "gramps_id": gid, "path": obj.get("path", ""),
            "mime": obj.get("mime", ""), "desc": obj.get("desc", ""),
            "checksum": obj.get("checksum", ""),
            "change": change, "private": private,
        }
    if obj_type == "repository":
        return {
            "gramps_id": gid, "name": obj.get("name", ""),
            "change": change, "private": private,
        }
    return {"gramps_id": gid, "change": change}
