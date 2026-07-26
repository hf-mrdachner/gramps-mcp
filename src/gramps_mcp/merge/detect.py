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
Duplicate person detection for Gramps SQLite databases.

Scoring mirrors the logic in Gramps' own finddupes.py but is adapted for
direct SQLite access and extended with:
  - German umlaut normalisation (ö→oe etc.)
  - Family-bonus scoring (shared relatives increase confidence)
"""

import json
import re
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass
class DuplicateCandidate:
    """
    A pair of persons that may be duplicates.

    Attributes:
        score: Similarity score (higher = more likely duplicate).
        winner_handle: DB handle of the richer / preferred record.
        loser_handle: DB handle of the record to be absorbed.
        winner_id: Gramps ID of the winner (e.g. "I0001").
        loser_id: Gramps ID of the loser.
        winner_name: Display name of the winner.
        loser_name: Display name of the loser.
        reasons: Human-readable list of matching factors.
    """

    score: float
    winner_handle: str
    loser_handle: str
    winner_id: str
    loser_id: str
    winner_name: str
    loser_name: str
    reasons: List[str] = field(default_factory=list)


def normalize(s: str) -> str:
    """
    Normalise a name string for comparison.

    Lowercases, strips, converts German umlauts to ASCII digraphs, and
    collapses whitespace/hyphens so spelling variants land in the same
    comparison bucket.

    Args:
        s: Raw name string.

    Returns:
        Normalised form suitable for equality comparison.
    """
    s = s.lower().strip()
    for old, new in [("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")]:
        s = s.replace(old, new)
    s = re.sub(r"[-_\s]+", " ", s)
    return s


def _year_from_event(ev: dict) -> Optional[int]:
    """
    Extract a 4-digit year from a Gramps event dict.

    Tries dateval first (dv[2] = year), then falls back to parsing the
    date string for a 4-digit year — covering older .gpkg exports that
    store dates as free-text strings.

    Args:
        ev: Gramps event JSON dict.

    Returns:
        Year as int, or None if not determinable.
    """
    dv = ev.get("date", {}).get("dateval", [0, 0, 0, False])
    if dv[2]:
        return int(dv[2])
    date_str = ev.get("date", {}).get("string", "") or ""
    m = re.search(r"\b(\d{4})\b", date_str)
    return int(m.group(1)) if m else None


def _bulk_birth_years(
    persons: Dict[str, dict], conn: sqlite3.Connection
) -> Dict[str, Optional[int]]:
    """
    Return a person-handle → birth-year mapping using a single bulk query.

    Replaces the previous per-person, per-event SELECT loop with one
    WHERE handle IN (...) fetch, reducing N×M round-trips to one query.

    Args:
        persons: Dict of handle → augmented person JSON.
        conn: SQLite connection.

    Returns:
        Dict mapping each person handle to its birth year (or None).
    """
    # Collect all primary-role event handles referenced by any person
    all_handles: set = set()
    for p in persons.values():
        for eref in p.get("event_ref_list", []):
            if eref.get("role", {}).get("value") == 1:  # Primary role
                all_handles.add(eref["ref"])

    if not all_handles:
        return {h: None for h in persons}

    placeholders = ",".join("?" * len(all_handles))
    rows = conn.execute(
        f"SELECT handle, json_data FROM event WHERE handle IN ({placeholders})",
        list(all_handles),
    ).fetchall()

    # Build event handle → birth year (Birth = type 12 only)
    event_years: Dict[str, int] = {}
    for row in rows:
        ev = json.loads(row["json_data"])
        if ev.get("type", {}).get("value") == 12:
            year = _year_from_event(ev)
            if year is not None:
                event_years[row["handle"]] = year

    # Map person handle → first found birth year
    result: Dict[str, Optional[int]] = {}
    for ph, p in persons.items():
        year = None
        for eref in p.get("event_ref_list", []):
            if eref.get("role", {}).get("value") == 1:
                year = event_years.get(eref["ref"])
                if year is not None:
                    break
        result[ph] = year
    return result


def similarity_score(
    p1: dict,
    p2: dict,
    birth_year_1: Optional[int],
    birth_year_2: Optional[int],
) -> Tuple[float, List[str]]:
    """
    Compute a similarity score between two persons.

    Uses gender, surname, given name, birth year, and suffix (Roman numerals).
    Returns (0, reasons) on hard mismatches so callers can skip early.

    Args:
        p1: Person dict with _given, _surname, _gender, _suffix keys.
        p2: Person dict with same keys.
        birth_year_1: Birth year of p1, or None.
        birth_year_2: Birth year of p2, or None.

    Returns:
        Tuple of (score, reasons) where score is float and reasons is list of str.
    """
    score: float = 0.0
    reasons: List[str] = []

    if p1["_gender"] != p2["_gender"]:
        return 0.0, ["gender mismatch"]
    score += 1

    sx1 = p1.get("_suffix", "")
    sx2 = p2.get("_suffix", "")
    if sx1 and sx2:
        if sx1 == sx2:
            score += 2
            reasons.append(f"suffix='{sx1}'")
        else:
            return 0.0, [f"suffix mismatch: '{sx1}' vs '{sx2}'"]
    elif sx1 or sx2:
        score -= 1

    s1 = normalize(p1["_surname"])
    s2 = normalize(p2["_surname"])
    if s1 and s2:
        if s1 == s2:
            score += 3
            reasons.append(f"surname='{s1}'")
        else:
            return 0.0, [f"surname mismatch: '{s1}' vs '{s2}'"]
    elif not s1 and not s2:
        score += 1
    else:
        return 0.0, ["one surname empty"]

    g1 = normalize(p1["_given"])
    g2 = normalize(p2["_given"])
    if g1 and g2:
        if g1 == g2:
            score += 4
            reasons.append(f"given='{g1}'")
        elif g1 in g2 or g2 in g1:
            score += 2
            reasons.append(f"given partial: '{g1}' ~ '{g2}'")
        else:
            return 0.0, [f"given mismatch: '{g1}' vs '{g2}'"]
    elif not g1 and not g2:
        score += 1

    # Use explicit None checks so year 0 (1 BCE) is treated as a known year
    if birth_year_1 is not None and birth_year_2 is not None:
        diff = abs(birth_year_1 - birth_year_2)
        if diff == 0:
            score += 4
            reasons.append(f"birth_year={birth_year_1}")
        elif diff <= 1:
            score += 2
            reasons.append(f"birth_year~{birth_year_1}/{birth_year_2}")
        else:
            return 0.0, [f"birth year mismatch: {birth_year_1} vs {birth_year_2}"]
    elif birth_year_1 is not None or birth_year_2 is not None:
        score -= 1
    else:
        # No birth year on either side means no temporal evidence at all to
        # tell two same-named people apart (e.g. a father and son sharing a
        # name) - penalize harder than the single-missing case.
        score -= 2
        reasons.append("kein Geburtsjahr bekannt")

    return score, reasons


def _bulk_family_relations(
    persons: Dict[str, dict], conn: sqlite3.Connection
) -> Dict[str, Dict[str, set]]:
    """
    Return a person-handle -> role-separated relative-handles mapping.

    Roles are kept apart ("parents", "spouses", "children", "siblings")
    rather than merged into one neighbor set, resolved from a single bulk
    family fetch (mirrors _bulk_birth_years). This lets the family bonus
    require a matching role on both sides: a grandparent and grandchild
    also touch a common connector (the linking parent), but in mismatched
    roles (child vs. parent) - that is ordinary kinship, not evidence of a
    duplicated record, and must not score as one.

    Args:
        persons: Dict of handle -> augmented person JSON.
        conn: SQLite connection.

    Returns:
        Dict mapping each person handle to its role-separated relative sets.
    """
    all_fam_handles: set = set()
    for p in persons.values():
        all_fam_handles.update(p.get("parent_family_list", []))
        all_fam_handles.update(p.get("family_list", []))

    families: Dict[str, dict] = {}
    if all_fam_handles:
        placeholders = ",".join("?" * len(all_fam_handles))
        rows = conn.execute(
            f"SELECT handle, json_data FROM family WHERE handle IN ({placeholders})",
            list(all_fam_handles),
        ).fetchall()
        families = {r["handle"]: json.loads(r["json_data"]) for r in rows}

    relations: Dict[str, Dict[str, set]] = {
        h: {"parents": set(), "spouses": set(), "children": set(), "siblings": set()}
        for h in persons
    }
    for h, p in persons.items():
        rel = relations[h]
        for fam_h in p.get("parent_family_list", []):
            fam = families.get(fam_h)
            if fam is None:
                continue
            for parent in (fam.get("father_handle"), fam.get("mother_handle")):
                if parent:
                    rel["parents"].add(parent)
            for c in fam.get("child_ref_list", []):
                if c["ref"] != h:
                    rel["siblings"].add(c["ref"])
        for fam_h in p.get("family_list", []):
            fam = families.get(fam_h)
            if fam is None:
                continue
            for spouse in (fam.get("father_handle"), fam.get("mother_handle")):
                if spouse and spouse != h:
                    rel["spouses"].add(spouse)
            for c in fam.get("child_ref_list", []):
                rel["children"].add(c["ref"])
    return relations


def _all_relatives(rel: Dict[str, set]) -> set:
    """Union of every direct-relation category, for the hard exclusion check."""
    return rel["parents"] | rel["spouses"] | rel["children"] | rel["siblings"]


# Naming-after-a-relative (grandparent, uncle/aunt) is a common historical
# pattern, so a same-named pair up to this many hops apart in the direct-
# relation graph is treated as ordinary kinship, not a duplicate candidate.
# 2 hops covers grandparent/grandchild and uncle/aunt-nephew/niece; cousins
# and great-grandparent/great-grandchild (3+ hops) are left to normal scoring.
RELATION_EXCLUSION_HOPS = 2


def _relatives_within_hops(
    start: str, all_relatives: Dict[str, set], max_hops: int
) -> set:
    """
    BFS the direct-relation graph up to max_hops from start.

    Args:
        start: Handle to start from.
        all_relatives: Person-handle -> set-of-direct-relative-handles graph.
        max_hops: Maximum number of hops to traverse.

    Returns:
        Set of handles reachable within max_hops (excludes start itself).
    """
    visited = {start}
    frontier = {start}
    for _ in range(max_hops):
        next_frontier: set = set()
        for h in frontier:
            for neighbor in all_relatives.get(h, set()):
                if neighbor not in visited:
                    visited.add(neighbor)
                    next_frontier.add(neighbor)
        if not next_frontier:
            break
        frontier = next_frontier
    visited.discard(start)
    return visited


def _family_bonus(
    rel_1: Dict[str, set], rel_2: Dict[str, set], conn: sqlite3.Connection
) -> Tuple[float, List[str]]:
    """
    Extra score when both persons share relatives in the SAME role.

    Only same-role overlap counts: both listing the identical handle as
    PARENT, as SPOUSE, or as CHILD is evidence of a duplicated record (e.g.
    a family entered twice, each copy holding one of the two child records
    that should be merged). A connector shared only via mismatched roles
    (e.g. one person's child is the other's parent, as with a grandparent
    and grandchild) is ordinary kinship and must not contribute.

    Args:
        rel_1: Role-separated direct-relative sets of person 1.
        rel_2: Role-separated direct-relative sets of person 2.
        conn: SQLite connection (used only to resolve a sample name for the
            reason text).

    Returns:
        Tuple of (bonus, reasons).
    """
    shared = (
        (rel_1["parents"] & rel_2["parents"])
        | (rel_1["spouses"] & rel_2["spouses"])
        | (rel_1["children"] & rel_2["children"])
    )
    if not shared:
        return 0.0, []
    bonus = min(len(shared) * 3.0, 6.0)
    sample = next(iter(shared))
    row = conn.execute(
        "SELECT given_name, surname FROM person WHERE handle=?", (sample,)
    ).fetchone()
    name = f"{row['given_name']} {row['surname']}".strip() if row else "?"
    return bonus, [f"gemeinsame Verwandte: {len(shared)} (z.B. {name})"]


def _load_persons(conn: sqlite3.Connection) -> Dict[str, dict]:
    """Load all persons with augmented _given/_surname/_gender/_suffix/_gramps_id keys."""
    rows = conn.execute(
        "SELECT handle, given_name, surname, gramps_id, gender, json_data FROM person"
    ).fetchall()
    persons: Dict[str, dict] = {}
    for r in rows:
        p = json.loads(r["json_data"])
        p["_handle"] = r["handle"]
        p["_given"] = r["given_name"] or ""
        p["_surname"] = r["surname"] or ""
        p["_gramps_id"] = r["gramps_id"]
        p["_gender"] = r["gender"]
        p["_suffix"] = normalize(p["primary_name"].get("suffix", "") or "")
        persons[r["handle"]] = p
    return persons


def _richness(p: dict) -> int:
    """Count of data fields — used to choose winner (more data = keep)."""
    return (
        len(p.get("event_ref_list", []))
        + len(p.get("citation_list", []))
        + len(p.get("note_list", []))
        + len(p.get("family_list", []))
        + len(p.get("parent_family_list", []))
        + (1 if p["_given"] and p["_given"].upper() not in ("ICH",) else 0)
    )


def find_duplicate_persons(
    conn: sqlite3.Connection,
    limit: int = 100,
    min_score: float = 4.0,
) -> List[DuplicateCandidate]:
    """
    Scan all persons and return duplicate candidate pairs sorted by score.

    Args:
        conn: SQLite connection to a Gramps database.
        limit: Maximum number of candidates to return.
        min_score: Minimum score threshold to include a pair.

    Returns:
        Sorted list of DuplicateCandidate (highest score first).
    """
    persons = _load_persons(conn)
    birth_years = _bulk_birth_years(persons, conn)
    relations = _bulk_family_relations(persons, conn)
    all_relatives = {h: _all_relatives(rel) for h, rel in relations.items()}
    hop_relatives_cache: Dict[str, set] = {}

    def hop_relatives(h: str) -> set:
        if h not in hop_relatives_cache:
            hop_relatives_cache[h] = _relatives_within_hops(
                h, all_relatives, RELATION_EXCLUSION_HOPS
            )
        return hop_relatives_cache[h]

    # Group by normalised (surname, given) for O(n) pair enumeration
    groups: Dict[tuple, list] = defaultdict(list)
    for h, p in persons.items():
        key = (normalize(p["_surname"]), normalize(p["_given"]))
        groups[key].append(h)

    candidates: List[DuplicateCandidate] = []
    for handles in groups.values():
        if len(handles) < 2:
            continue
        for i in range(len(handles)):
            for j in range(i + 1, len(handles)):
                h1, h2 = handles[i], handles[j]

                # Already related within RELATION_EXCLUSION_HOPS (parent,
                # child, spouse, sibling, or grandparent/uncle-nephew etc.) -
                # a same-named relative is not a duplicate candidate.
                if h2 in hop_relatives(h1):
                    continue

                p1, p2 = persons[h1], persons[h2]
                score, reasons = similarity_score(
                    p1, p2, birth_years.get(h1), birth_years.get(h2)
                )
                if score <= 0:
                    continue  # hard mismatch - no family bonus can rescue this
                if score < 8:
                    bonus, fam_reasons = _family_bonus(
                        relations[h1], relations[h2], conn
                    )
                    score += bonus
                    reasons += fam_reasons
                if score < min_score:
                    continue

                if _richness(p1) >= _richness(p2):
                    winner, loser = p1, p2
                    wh, lh = h1, h2
                else:
                    winner, loser = p2, p1
                    wh, lh = h2, h1

                candidates.append(DuplicateCandidate(
                    score=score,
                    winner_handle=wh,
                    loser_handle=lh,
                    winner_id=winner["_gramps_id"],
                    loser_id=loser["_gramps_id"],
                    winner_name=f"{winner['_given']} {winner['_surname']}".strip(),
                    loser_name=f"{loser['_given']} {loser['_surname']}".strip(),
                    reasons=reasons,
                ))

    candidates.sort(key=lambda c: -c.score)
    return candidates[:limit]
