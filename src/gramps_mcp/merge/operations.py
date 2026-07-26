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
Merge and split operations for Gramps SQLite persons.

merge_persons  — absorb a loser person into a winner, deduplicating events.
split_person   — undo a prior merge using a sidecar backup file.

All functions operate on a raw sqlite3.Connection so they can be used
both from MCP tools and standalone scripts.
"""

import json
import os
import sqlite3
import time
from copy import deepcopy
from typing import Dict, List, Optional, Set, Tuple


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _primary_surname(primary_name: dict) -> str:
    """
    Extract the first surname from a Gramps primary_name dict.

    Args:
        primary_name: Gramps Name dict with surname_list.

    Returns:
        Surname string, or empty string if absent.
    """
    surname_list = primary_name.get("surname_list", [])
    return surname_list[0].get("surname", "") if surname_list else ""


# ---------------------------------------------------------------------------
# Backup helpers
# ---------------------------------------------------------------------------

def load_backups(backup_file: str) -> List[dict]:
    """
    Load merge backup records from a sidecar JSON file.

    Args:
        backup_file: Absolute path to the backup JSON file.

    Returns:
        List of backup dicts (empty list if file does not exist).
    """
    if os.path.exists(backup_file):
        with open(backup_file, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def save_backup(
    backup_file: str,
    loser_handle: str,
    loser_json: dict,
    winner_handle: str,
    winner_id: str,
    loser_id: str,
) -> None:
    """
    Append one merge record to the sidecar backup file.

    Args:
        backup_file: Absolute path to the backup JSON file.
        loser_handle: DB handle of the deleted loser.
        loser_json: Full Gramps JSON of the loser before deletion.
        winner_handle: DB handle of the surviving winner.
        winner_id: Gramps ID of the winner.
        loser_id: Gramps ID of the loser.
    """
    records = load_backups(backup_file)
    records.append({
        "timestamp": int(time.time()),
        "winner_id": winner_id,
        "loser_id": loser_id,
        "winner_handle": winner_handle,
        "loser_handle": loser_handle,
        "loser_json": loser_json,
    })
    with open(backup_file, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Event deduplication helpers
# ---------------------------------------------------------------------------

def _date_key(ev: dict) -> Tuple[int, int, int]:
    """
    Return (year, month, day) from a Gramps event — (0, 0, 0) if absent.

    Args:
        ev: Gramps event JSON dict.

    Returns:
        Tuple of (year, month, day).
    """
    dv = ev.get("date", {}).get("dateval", [0, 0, 0, False])
    return (int(dv[2]), int(dv[1]), int(dv[0]))


def _events_match(ev1: dict, ev2: dict) -> bool:
    """
    Return True if two events represent the same occurrence.

    Matches on type + date (year/month/day) + place handle.

    Args:
        ev1: Gramps event JSON dict.
        ev2: Gramps event JSON dict.

    Returns:
        True if events are considered duplicates.
    """
    if ev1.get("type", {}).get("value") != ev2.get("type", {}).get("value"):
        return False
    if _date_key(ev1) != _date_key(ev2):
        return False
    if ev1.get("place") != ev2.get("place"):
        return False
    return True


def _find_duplicate_events(
    conn: sqlite3.Connection,
    wp: dict,
    lp: dict,
) -> Tuple[Set[str], Dict[str, dict], Set[str], Dict[str, str]]:
    """
    Identify loser events that duplicate a winner event.

    When a match is found, the loser event's citations are merged into the
    winner event rather than creating a second event entry.

    Shared event handles (same handle in both persons' event_ref_list) are
    skipped from deletion — only the loser ref is excluded from the merge,
    the shared event row is preserved.

    Args:
        conn: SQLite connection.
        wp: Winner person JSON.
        lp: Loser person JSON.

    Returns:
        Tuple of:
          events_to_skip   — loser event handles to exclude from event_ref_list merge
          event_updates    — winner_event_handle -> updated event JSON
          events_to_delete — loser event handles to remove from the event table
          events_to_replace — loser_event_handle -> winner_event_handle (for family fixup)
    """
    events_to_skip: Set[str] = set()
    event_updates: Dict[str, dict] = {}
    events_to_delete: Set[str] = set()
    events_to_replace: Dict[str, str] = {}

    winner_events: Dict[str, dict] = {}
    for eref in wp.get("event_ref_list", []):
        row = conn.execute(
            "SELECT json_data FROM event WHERE handle=?", (eref["ref"],)
        ).fetchone()
        if row is not None:
            winner_events[eref["ref"]] = json.loads(row["json_data"])

    for eref in lp.get("event_ref_list", []):
        lh = eref["ref"]
        lrow = conn.execute(
            "SELECT json_data FROM event WHERE handle=?", (lh,)
        ).fetchone()
        if lrow is None:
            continue
        lev = json.loads(lrow["json_data"])

        for wh, wev in winner_events.items():
            if not _events_match(wev, lev):
                continue
            events_to_skip.add(lh)
            if lh == wh:
                # Same DB row shared by winner and loser — skip the ref
                # but do NOT delete the event (winner still needs it).
                break
            merged = deepcopy(wev)
            w_cits: set = set(merged.get("citation_list", []))
            for c in lev.get("citation_list", []):
                if c not in w_cits:
                    merged.setdefault("citation_list", []).append(c)
                    w_cits.add(c)
            if merged != wev:
                event_updates[wh] = merged
            events_to_delete.add(lh)
            events_to_replace[lh] = wh
            break

    return events_to_skip, event_updates, events_to_delete, events_to_replace


# ---------------------------------------------------------------------------
# Core merge
# ---------------------------------------------------------------------------

def merge_persons(
    conn: sqlite3.Connection,
    winner_handle: str,
    loser_handle: str,
    dry_run: bool = True,
    backup_file: Optional[str] = None,
) -> List[str]:
    """
    Merge the loser person into the winner.

    In dry_run mode the changes are computed and returned but nothing is
    written to the database.  When dry_run=False a full transaction is
    executed and (if backup_file is given) the loser JSON is persisted so
    split_person can undo the merge later.

    Args:
        conn: SQLite connection to a Gramps database.
        winner_handle: DB handle of the person to keep.
        loser_handle: DB handle of the person to absorb.
        dry_run: If True, compute changes without writing.
        backup_file: Path to the sidecar backup JSON file.

    Returns:
        Human-readable list of changes made (or that would be made).
    """
    wp_row = conn.execute(
        "SELECT gramps_id, json_data FROM person WHERE handle=?", (winner_handle,)
    ).fetchone()
    lp_row = conn.execute(
        "SELECT gramps_id, json_data FROM person WHERE handle=?", (loser_handle,)
    ).fetchone()
    wp = json.loads(wp_row["json_data"])
    lp = json.loads(lp_row["json_data"])
    winner_id = wp_row["gramps_id"]
    loser_id = lp_row["gramps_id"]

    changes: List[str] = []

    events_to_skip, event_updates, events_to_delete, events_to_replace = _find_duplicate_events(conn, wp, lp)
    if events_to_delete:
        type_labels = []
        for h in events_to_delete:
            row = conn.execute("SELECT json_data FROM event WHERE handle=?", (h,)).fetchone()
            if row is not None:
                ev = json.loads(row["json_data"])
                type_labels.append(ev.get("type", {}).get("string", "?") or "?")
        changes.append(f"dedupliziert {len(events_to_delete)} Event(s): {', '.join(type_labels)}")

    # Single merge function handles both dict-ref fields and plain handle lists
    def _merge_field(f: str) -> None:
        w_refs = {x["ref"] if isinstance(x, dict) else x for x in wp.get(f, [])}
        added = []
        for item in lp.get(f, []):
            ref = item["ref"] if isinstance(item, dict) else item
            if ref not in w_refs:
                if f == "event_ref_list" and ref in events_to_skip:
                    continue
                wp.setdefault(f, []).append(item)
                w_refs.add(ref)
                added.append(ref)
        if added:
            changes.append(f"+{len(added)} {f}")

    for f in ("event_ref_list", "citation_list", "note_list", "media_list",
              "attribute_list", "address_list", "urls", "lds_ord_list",
              "person_ref_list", "tag_list", "family_list", "parent_family_list"):
        _merge_field(f)

    loser_name = lp["primary_name"]
    winner_name = wp["primary_name"]
    if loser_name["first_name"].strip().lower() != winner_name["first_name"].strip().lower():
        alt = deepcopy(loser_name)
        alt["type"] = {"_class": "NameType", "value": 2, "string": ""}
        wp.setdefault("alternate_names", []).append(alt)
        changes.append("added alternate name")

    # Adopt loser's birth/death ref indices, finding the new position in the
    # merged event_ref_list rather than copying the raw loser index verbatim.
    def _adopt_event_ref_index(field: str, label: str) -> None:
        if wp[field] != -1 or lp[field] == -1:
            return
        loser_erefs = lp.get("event_ref_list", [])
        idx = lp[field]
        if not (0 <= idx < len(loser_erefs)):
            return
        event_h = loser_erefs[idx]["ref"]
        if event_h in events_to_delete:
            return
        new_idx = next(
            (i for i, e in enumerate(wp.get("event_ref_list", []))
             if e["ref"] == event_h),
            -1,
        )
        if new_idx >= 0:
            wp[field] = new_idx
            changes.append(f"adopted {label}")

    _adopt_event_ref_index("birth_ref_index", "birth_ref_index")
    _adopt_event_ref_index("death_ref_index", "death_ref_index")

    wp["change"] = int(time.time())

    if dry_run:
        return changes

    loser_family_handles = set(
        lp.get("family_list", []) + lp.get("parent_family_list", [])
    )
    with conn:
        _commit_merge(
            conn, wp, winner_handle, loser_handle,
            event_updates, events_to_delete, events_to_replace, loser_family_handles,
        )

    if backup_file:
        save_backup(backup_file, loser_handle, lp, winner_handle, winner_id, loser_id)

    return changes


def _commit_merge(
    conn: sqlite3.Connection,
    wp: dict,
    winner_handle: str,
    loser_handle: str,
    event_updates: Dict[str, dict],
    events_to_delete: Set[str],
    events_to_replace: Dict[str, str],
    loser_family_handles: Set[str],
) -> None:
    """
    Execute all merge writes inside an open transaction.

    Args:
        conn: SQLite connection (transaction managed by caller via ``with conn``).
        wp: Updated winner person JSON.
        winner_handle: Winner handle.
        loser_handle: Loser handle (will be deleted).
        event_updates: Mapping of winner event handles to updated event JSON.
        events_to_delete: Loser event handles to remove.
        events_to_replace: Loser event handle -> winner event handle; used to fix
            family event_ref_lists so no dangling refs are left after deletion.
        loser_family_handles: Family handles from loser's family/parent_family lists;
            used for a targeted fetch instead of a full table scan.
    """
    conn.execute(
        "UPDATE person SET json_data=?, given_name=?, surname=? WHERE handle=?",
        (json.dumps(wp, ensure_ascii=False),
         wp["primary_name"]["first_name"],
         _primary_surname(wp["primary_name"]),
         winner_handle),
    )

    for ev_handle, ev_data in event_updates.items():
        conn.execute(
            "UPDATE event SET json_data=? WHERE handle=?",
            (json.dumps(ev_data, ensure_ascii=False), ev_handle),
        )

    # Only fetch families that actually reference the loser — O(k) not O(N)
    if loser_family_handles:
        placeholders = ",".join("?" * len(loser_family_handles))
        fam_rows = conn.execute(
            f"SELECT handle, json_data FROM family WHERE handle IN ({placeholders})",
            list(loser_family_handles),
        ).fetchall()
        for row in fam_rows:
            fam = json.loads(row["json_data"])
            changed = False
            if fam.get("father_handle") == loser_handle:
                fam["father_handle"] = winner_handle
                changed = True
            if fam.get("mother_handle") == loser_handle:
                fam["mother_handle"] = winner_handle
                changed = True
            child_refs = fam.get("child_ref_list", [])
            for c in child_refs:
                if c["ref"] == loser_handle:
                    c["ref"] = winner_handle
                    changed = True
            if child_refs:
                # Two ChildRef entries can end up pointing at the same ref
                # (winner already listed + loser's rewritten entry). Keep the
                # first entry's frel/mrel, but union citation_list/note_list
                # from the discarded duplicate(s) so nothing is silently lost.
                kept_by_ref: Dict[str, dict] = {}
                order: List[str] = []
                for c in child_refs:
                    ref = c["ref"]
                    if ref not in kept_by_ref:
                        kept_by_ref[ref] = c
                        order.append(ref)
                        continue
                    kept = kept_by_ref[ref]
                    for field in ("citation_list", "note_list"):
                        for item in c.get(field, []):
                            if item not in kept.get(field, []):
                                kept.setdefault(field, []).append(item)
                deduped_refs = [kept_by_ref[ref] for ref in order]
                if len(deduped_refs) != len(child_refs):
                    fam["child_ref_list"] = deduped_refs
                    changed = True
            if changed:
                conn.execute(
                    "UPDATE family SET json_data=? WHERE handle=?",
                    (json.dumps(fam, ensure_ascii=False), row["handle"]),
                )

    # Fix family event_ref_lists: loser events being deleted may still be
    # referenced by families (e.g. the marriage event appears in both the
    # person's and the family's event_ref_list). Replace or remove each
    # deleted loser event handle across ALL families that reference it.
    for loser_ev, winner_ev in events_to_replace.items():
        fam_rows_ev = conn.execute(
            "SELECT handle, json_data FROM family WHERE json_data LIKE ?",
            (f"%{loser_ev}%",),
        ).fetchall()
        for row in fam_rows_ev:
            fam = json.loads(row["json_data"])
            erefs = fam.get("event_ref_list", [])
            winner_already = any(
                e.get("ref") == winner_ev for e in erefs if isinstance(e, dict)
            )
            new_erefs = []
            for e in erefs:
                if not (isinstance(e, dict) and e.get("ref") == loser_ev):
                    new_erefs.append(e)
                elif not winner_already:
                    # Replace loser ref with winner ref in-place
                    replaced = dict(e)
                    replaced["ref"] = winner_ev
                    new_erefs.append(replaced)
                    winner_already = True
                # else: winner already present — just drop the loser ref
            if new_erefs != erefs:
                fam["event_ref_list"] = new_erefs
                conn.execute(
                    "UPDATE family SET json_data=? WHERE handle=?",
                    (json.dumps(fam, ensure_ascii=False), row["handle"]),
                )

    conn.execute(
        "UPDATE reference SET obj_handle=? WHERE obj_handle=?",
        (winner_handle, loser_handle),
    )
    conn.execute(
        "UPDATE reference SET ref_handle=? WHERE ref_handle=?",
        (winner_handle, loser_handle),
    )
    conn.execute("DELETE FROM reference WHERE obj_handle=ref_handle")
    conn.execute("""
        DELETE FROM reference WHERE rowid NOT IN (
            SELECT MIN(rowid) FROM reference
            GROUP BY obj_handle, obj_class, ref_handle, ref_class
        )
    """)

    conn.execute("DELETE FROM person WHERE handle=?", (loser_handle,))

    for ev_handle in events_to_delete:
        conn.execute("DELETE FROM event WHERE handle=?", (ev_handle,))
        conn.execute("DELETE FROM reference WHERE obj_handle=?", (ev_handle,))
        conn.execute("DELETE FROM reference WHERE ref_handle=?", (ev_handle,))


# ---------------------------------------------------------------------------
# Split / undo
# ---------------------------------------------------------------------------

def _restore_person(conn: sqlite3.Connection, backup: dict) -> Tuple[bool, str]:
    """
    Re-insert a backed-up person into the person table.

    Does not restore family links (too risky to automate — user should
    review in Gramps afterwards).

    Args:
        conn: SQLite connection (transaction managed by caller).
        backup: Backup record as written by save_backup().

    Returns:
        Tuple of (success, message).
    """
    lp = backup["loser_json"]
    loser_handle = backup["loser_handle"]
    loser_id = backup["loser_id"]

    if conn.execute(
        "SELECT 1 FROM person WHERE handle=?", (loser_handle,)
    ).fetchone() is not None:
        return False, f"Handle {loser_handle!r} already taken — {loser_id} skipped."

    given = lp.get("primary_name", {}).get("first_name", "")
    surname = _primary_surname(lp.get("primary_name", {}))

    conn.execute(
        "INSERT INTO person (handle, gramps_id, given_name, surname, gender, json_data) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (loser_handle, loser_id, given, surname,
         lp.get("gender", 2), json.dumps(lp, ensure_ascii=False)),
    )
    return True, f"Restored: {loser_id} ({given} {surname})"


def split_person(
    conn: sqlite3.Connection,
    gramps_id: str,
    backup_file: str,
) -> List[str]:
    """
    Undo a prior merge by recreating all persons merged into gramps_id.

    Reads the sidecar backup file written by merge_persons.  The winner
    person is NOT modified — its merged data must be cleaned up manually
    in Gramps afterwards.

    Args:
        conn: SQLite connection.
        gramps_id: Gramps ID of the merged winner (e.g. "I0001").
        backup_file: Path to the sidecar backup JSON file.

    Returns:
        List of human-readable result messages.
    """
    messages: List[str] = []
    records = load_backups(backup_file)
    matching = [r for r in records if r["winner_id"] == gramps_id]

    if not matching:
        messages.append(f"No merge backup found for {gramps_id}.")
        row = conn.execute(
            "SELECT json_data FROM person WHERE gramps_id=?", (gramps_id,)
        ).fetchone()
        if row is None:
            messages.append(f"Person {gramps_id} not found in database.")
            return messages
        wp = json.loads(row["json_data"])
        alt_names = [
            n for n in wp.get("alternate_names", [])
            if n.get("type", {}).get("value") == 2
        ]
        if alt_names:
            messages.append(
                f"Found {len(alt_names)} alternate name(s) — no full restore possible."
            )
            messages.append("Please review and split manually in Gramps.")
        else:
            messages.append("No alternate names found — nothing to split.")
        return messages

    messages.append(f"Splitting {len(matching)} person(s) from {gramps_id}...")
    restored = 0
    with conn:
        for rec in matching:
            ok, msg = _restore_person(conn, rec)
            messages.append(msg)
            if ok:
                restored += 1

    messages.append(f"Restored: {restored}/{len(matching)} person(s)")
    messages.append(
        "NOTE: Family links and event assignments need manual review in Gramps."
    )
    messages.append(
        "The winner record still contains all merged data — remove duplicates there."
    )
    return messages
