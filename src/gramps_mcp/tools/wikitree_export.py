# gramps-mcp - AI-Powered Genealogy Research & Management
# Copyright (C) 2025 cabout.me
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

"""
WikiTree biography export tool.

Reads a person's events and citations from the Gramps SQLite database and
generates WikiTree-compliant biography markup following standardised quality
rules:

  - Date status mapping: only "certain" when a primary source exists.
  - Structured <ref> citations with known-source formatting.
  - Consistent == Biography == / == Sources == structure.

SQLite backend only.
"""

import json
import logging
import re
from typing import Dict, List, Optional, Tuple

from mcp.types import TextContent

from ..client import GrampsAPIError, get_client
from .._gramps_sqlite import EVENT_TYPE as _EVENT_TYPE

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Month name translation (Gramps stores dates in various languages)
# ---------------------------------------------------------------------------

_DE_MONTHS = {
    # German + English abbreviated month names → full English
    "jan": "January",  "feb": "February", "mär": "March",   "mar": "March",
    "apr": "April",    "mai": "May",       "may": "May",     "jun": "June",
    "jul": "July",     "aug": "August",    "sep": "September",
    "okt": "October",  "oct": "October",   "nov": "November",
    "dez": "December", "dec": "December",
}

def _localise_date(date_str: str) -> str:
    """Convert a Gramps date string to English WikiTree format."""
    if not date_str:
        return ""
    s = date_str.strip()
    # Expand month names/abbreviations (German + English)
    for de, en in _DE_MONTHS.items():
        s = re.sub(rf'\b{de}\b', en, s, flags=re.IGNORECASE)
    # "04.April.1933" or "04. Januar 1900" → "4 April 1933"
    s = re.sub(r'(\d+)\.\s*', r'\1 ', s)
    s = s.strip()
    return s


# ---------------------------------------------------------------------------
# Source citation formatting
# ---------------------------------------------------------------------------

def _format_citation(source_title: str, page: str) -> str:
    """
    Format a Gramps citation into a WikiTree <ref> body.

    Recognised source types get structured formatting; others fall back to
    "Source title; page".
    """
    title = source_title.strip()
    page = page.strip()

    # Archion church books — page already contains readable text + URL
    if "archion" in title.lower() or re.search(r'archion\.de', page):
        return page  # already formatted: "Taufen 1817-1825, Eggesin [URL]"

    # KB Hessen (our own migrated citations)
    if re.match(r'(Katholische|Evangelische) Kirchenbücher', title):
        return f"{title}, {page}" if page else title

    # Standesamt / civil registry — page is "Nr/Jahr" or "263/1990"
    if "standesamt" in title.lower():
        return f"{title}, {page}" if page else title

    # Ancestry collections — page contains structured detail
    if "ancestry" in title.lower() and page and page != "Ancestry Family Tree":
        return f"{title}; {page}"

    # Generic fallback
    if page:
        return f"{title}; {page}"
    return title


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _load_maps(conn) -> Tuple[dict, dict, dict]:
    sources = {h: json.loads(j) for h, j in conn.execute("SELECT handle, json_data FROM source")}
    citations = {h: json.loads(j) for h, j in conn.execute("SELECT handle, json_data FROM citation")}
    places = {h: json.loads(j) for h, j in conn.execute("SELECT handle, json_data FROM place")}
    return sources, citations, places


def _place_name(place_handle: str, places: dict) -> str:
    if not place_handle:
        return ""
    p = places.get(place_handle, {})
    names = p.get("name", {})
    if isinstance(names, dict):
        return names.get("value", "")
    return ""


_MONTH_NAMES = [
    "", "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


def _event_date(ev: dict) -> str:
    """Return a human-readable date string from a Gramps event dict."""
    date = ev.get("date", {})
    if not isinstance(date, dict):
        return ""
    # Prefer free-text representation (already localised by Gramps)
    text = date.get("text", "").strip()
    if text:
        return _localise_date(text)
    # Structured dateval: [day, month, year, slash]
    dv = date.get("dateval", [0, 0, 0, False])
    if not isinstance(dv, list) or len(dv) < 3 or not dv[2]:
        return ""
    day, month, year = int(dv[0]), int(dv[1]), int(dv[2])
    if day and 1 <= month <= 12:
        return f"{day} {_MONTH_NAMES[month]} {year}"
    if 1 <= month <= 12:
        return f"{_MONTH_NAMES[month]} {year}"
    return str(year)


def _citations_for_event(ev: dict, citations: dict, sources: dict) -> List[str]:
    refs = []
    for ch in ev.get("citation_list", []):
        cit = citations.get(ch, {})
        src = sources.get(cit.get("source_handle", ""), {})
        title = src.get("title", "")
        page = cit.get("page", "")
        # Skip empty / family-tree-only citations
        if not title or title in ("Ancestry Family Trees", "Public Member Trees"):
            continue
        formatted = _format_citation(title, page)
        if formatted:
            refs.append(formatted)
    return refs


# ---------------------------------------------------------------------------
# Biography sentence builders
# ---------------------------------------------------------------------------

_EVENT_TEMPLATES_EN = {
    "Birth":         "{name} was born{date_part}{place_part}.{refs}",
    "Baptism":       "{name} was baptised{date_part}{place_part}.{refs}",
    "Confirmation":  "{name} was confirmed{date_part}{place_part}.{refs}",
    "Marriage":      "{name} married{spouse_part}{date_part}{place_part}.{refs}",
    "Residence":     "{name} lived{place_part}{date_part}.{refs}",
    "Death":         "{name} died{date_part}{place_part}.{refs}",
    "Burial":        "{name} was buried{date_part}{place_part}.{refs}",
}

# For sentences after the first, use pronoun instead of full name
_PRONOUN = {"Male": "He", "Female": "She", "Unknown": "They"}

# Events we include by default (order matters for narrative flow)
DEFAULT_EVENT_ORDER = ["Birth", "Baptism", "Confirmation", "Marriage", "Residence", "Death", "Burial"]


def _date_preposition(date: str) -> str:
    """'on 14 January 1820' but 'in 1820' for year-only dates."""
    if re.search(r'\d{1,2}\s+\w', date):
        return "on"
    return "in"


def _build_sentence(template: str, name: str, date: str, place: str,
                    spouse: str, ref_texts: List[str]) -> str:
    prep = _date_preposition(date) if date else ""
    date_part = f" {prep} {date}" if date else ""
    place_part = f" in {place}" if place else ""
    spouse_part = f" {spouse}" if spouse else ""
    refs = "".join(f"<ref>{r}</ref>" for r in ref_texts)
    return template.format(
        name=name, date_part=date_part, place_part=place_part,
        spouse_part=spouse_part, refs=refs,
    )


# ---------------------------------------------------------------------------
# Main export function
# ---------------------------------------------------------------------------

SUPPORTED_FLAVORS = ("wikitree",)


def prepare_biography(
    gramps_id: str,
    flavor: str = "wikitree",
    language: str = "en",
    include_events: Optional[List[str]] = None,
    conn=None,
) -> str:
    """
    Generate export biography markup for a Gramps person.

    flavor  — target platform format, currently only "wikitree"
    language — output language, currently only "en"
    conn    — optional raw SQLite connection; if None, extracted from active client

    Returns the biography as a string ready to paste into the target platform.
    """
    if flavor not in SUPPORTED_FLAVORS:
        raise NotImplementedError(f"Unsupported flavor: {flavor!r}. Supported: {SUPPORTED_FLAVORS}")
    if language != "en":
        raise NotImplementedError("Only language='en' is currently supported.")

    if conn is None:
        client = get_client()
        from ..sqlite_client import GrampsSqliteClient
        if not isinstance(client, GrampsSqliteClient):
            raise GrampsAPIError("prepare_wikitree_biography requires the SQLite backend.")
        conn = client._db._conn

    sources, citations, places = _load_maps(conn)

    # Load person
    row = conn.execute(
        "SELECT json_data FROM person WHERE gramps_id=?", (gramps_id,)
    ).fetchone()
    if not row:
        raise GrampsAPIError(f"Person not found: {gramps_id}")
    person = json.loads(row[0])

    # Name
    pname = person.get("primary_name", {})
    first = pname.get("first_name", "")
    surnames = pname.get("surname_list", [])
    last = surnames[0].get("surname", "") if surnames else ""
    full_name = f"{first} {last}".strip()

    # Load all events for this person
    event_rows = {
        h: json.loads(j)
        for h, j in conn.execute("SELECT handle, json_data FROM event")
    }

    # Collect spouse handles for marriage events
    family_rows = {
        h: json.loads(j)
        for h, j in conn.execute("SELECT handle, json_data FROM family")
    }

    def _spouse_name(family_handle: str) -> str:
        fam = family_rows.get(family_handle, {})
        father_h = fam.get("father_handle", "")
        mother_h = fam.get("mother_handle", "")
        p_handle = person.get("handle", "")
        spouse_h = mother_h if father_h == p_handle else father_h
        if not spouse_h:
            return ""
        sr = conn.execute(
            "SELECT json_data FROM person WHERE handle=?", (spouse_h,)
        ).fetchone()
        if not sr:
            return ""
        sp = json.loads(sr[0])
        sn = sp.get("primary_name", {})
        sf = sn.get("first_name", "")
        ss = sn.get("surname_list", [{}])[0].get("surname", "")
        return f"{sf} {ss}".strip()

    def _ev_type_str(ev: dict) -> str:
        """Resolve Gramps event type to a human-readable string."""
        raw = ev.get("type", {})
        if isinstance(raw, dict):
            s = raw.get("string", "")
            if s:
                return s
            return _EVENT_TYPE.get(raw.get("value", -1), "Unknown")
        return str(raw)

    # Collect "floating" citations from _WLNK events — match to event types by keyword
    _WLNK_KEYWORDS: Dict[str, List[str]] = {
        "Birth":        ["taufen", "geburten", "birth", "baptis"],
        "Baptism":      ["taufen", "baptis"],
        "Confirmation": ["konfirmation", "confirmati"],
        "Marriage":     ["trauungen", "heirat", "marriage", "married"],
        "Death":        ["bestattungen", "sterbe", "death", "burial", "begrä"],
        "Burial":       ["bestattungen", "begrä", "burial"],
    }
    floating_cits: Dict[str, List[str]] = {t: [] for t in DEFAULT_EVENT_ORDER}

    for eref in person.get("event_ref_list", []):
        ev = event_rows.get(eref.get("ref", ""), {})
        if _ev_type_str(ev) != "_WLNK":
            continue
        for ch in ev.get("citation_list", []):
            cit = citations.get(ch, {})
            src = sources.get(cit.get("source_handle", ""), {})
            page = cit.get("page", "").lower()
            for ev_type, keywords in _WLNK_KEYWORDS.items():
                if any(kw in page for kw in keywords):
                    formatted = _format_citation(src.get("title", ""), cit.get("page", ""))
                    if formatted and formatted not in floating_cits[ev_type]:
                        floating_cits[ev_type].append(formatted)

    # Build event type → list of (event, spouse_name) tuples
    allowed = include_events or DEFAULT_EVENT_ORDER
    ev_by_type: Dict[str, list] = {t: [] for t in allowed}

    for eref in person.get("event_ref_list", []):
        ev = event_rows.get(eref.get("ref", ""), {})
        ev_type = _ev_type_str(ev)
        if ev_type not in ev_by_type:
            continue
        ev_by_type[ev_type].append(ev)

    # Also check family events for marriages
    for fref in person.get("family_list", []):
        fam = family_rows.get(fref, {})
        for eref in fam.get("event_ref_list", []):
            ev = event_rows.get(eref.get("ref", ""), {})
            ev_type = _ev_type_str(ev)
            if ev_type == "Marriage" and "Marriage" in ev_by_type:
                spouse = _spouse_name(fref)
                ev_by_type["Marriage"].append((ev, spouse))

    # Deduplicate events: keep most complete (has location > no, has day > just year)
    def _completeness(ev):
        date = _event_date(ev)
        place = _place_name(ev.get("place", ""), places)
        # Higher score = more complete
        score = 0
        if place:
            score += 2
        if re.search(r'\d{1,2}\s+\w+', date):  # has day
            score += 1
        score += len(ev.get("citation_list", []))
        return score

    def _dedup(evs):
        # Group by approximate date (year only) + type, keep best
        groups: Dict[str, list] = {}
        for item in evs:
            ev = item[0] if isinstance(item, tuple) else item
            date = _event_date(ev)
            year = re.search(r'\d{4}', date)
            key = year.group(0) if year else date
            groups.setdefault(key, []).append(item)
        out = []
        for key, group in groups.items():
            best = max(group, key=lambda x: _completeness(x[0] if isinstance(x, tuple) else x))
            out.append(best)
        return out

    # Determine pronoun for subsequent sentences
    gender_raw = person.get("gender", 2)  # 0=Female, 1=Male, 2=Unknown
    if gender_raw == 1:
        pronoun = _PRONOUN["Male"]
    elif gender_raw == 0:
        pronoun = _PRONOUN["Female"]
    else:
        pronoun = _PRONOUN["Unknown"]

    # Generate sentences
    sentences = []
    for ev_type in allowed:
        template = _EVENT_TEMPLATES_EN.get(ev_type)
        if not template:
            continue
        evs = _dedup(ev_by_type.get(ev_type, []))
        for item in evs:
            spouse = ""
            if isinstance(item, tuple):
                ev, spouse = item
            else:
                ev = item
            date = _event_date(ev)
            place_h = ev.get("place", "")
            place = _place_name(place_h, places)
            ref_texts = _citations_for_event(ev, citations, sources)
            # Supplement with floating _WLNK citations matched by keyword
            for fc in floating_cits.get(ev_type, []):
                if fc not in ref_texts:
                    ref_texts.append(fc)

            # First sentence uses full name, subsequent ones use pronoun
            name = full_name if not sentences else pronoun
            sentence = _build_sentence(template, name, date, place, spouse, ref_texts)
            sentences.append(sentence)

    if not sentences:
        bio_body = f"{full_name}'s biography is empty. What can you add?\n"
        unsourced = "{{Unsourced}}\n"
    else:
        bio_body = "\n\n".join(sentences) + "\n"
        # Mark unsourced if any sentence has no refs
        has_unsourced = any("<ref>" not in s for s in sentences if s.strip())
        unsourced = "{{Unsourced}}\n" if has_unsourced else ""

    biography = (
        f"{unsourced}"
        f"== Biography ==\n\n"
        f"{bio_body}\n"
        f"== Sources ==\n"
        f"<references />\n"
    )
    return biography


# ---------------------------------------------------------------------------
# MCP tool wrapper
# ---------------------------------------------------------------------------

async def prepare_biography_tool(arguments: Dict) -> List[TextContent]:
    """
    Generate export biography markup from a Gramps person record.

    Reads events and citations from the local Gramps database and formats them
    according to the target platform's quality standards.

    flavor="wikitree":
    - Archion, Standesamt, KB Hessen, and Ancestry citations get structured formatting.
    - Events without citations are flagged with {{Unsourced}}.
    - Output is ready to pass directly to wikitree_edit_person.

    SQLite backend only.
    """
    gramps_id = arguments.get("gramps_id", "")
    flavor = arguments.get("flavor", "wikitree")
    language = arguments.get("language", "en")
    include_events = arguments.get("include_events") or None

    if not gramps_id:
        return [TextContent(type="text", text="Error: gramps_id is required.")]

    try:
        text = prepare_biography(gramps_id, flavor=flavor, language=language,
                                 include_events=include_events)
        return [TextContent(type="text", text=text)]
    except (GrampsAPIError, NotImplementedError) as e:
        return [TextContent(type="text", text=f"Error: {e}")]
