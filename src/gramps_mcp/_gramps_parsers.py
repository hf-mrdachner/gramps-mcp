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
XML helper functions and Gramps object parsers.

Pure functions: each converts an xml.etree.ElementTree.Element to a plain
dict matching the structure expected by Gramps Web API responses.
No IO, no side effects, no external dependencies beyond the stdlib.
"""

from typing import Dict

# ---------------------------------------------------------------------------
# XML helpers
# ---------------------------------------------------------------------------


def _tag(el) -> str:
    """Strip namespace from an element tag."""
    t = el.tag
    return t.split("}", 1)[1] if "}" in t else t


def _text(el, child_tag: str, default: str = "") -> str:
    """Get stripped text of a named child element."""
    for c in el:
        if _tag(c) == child_tag:
            return c.text.strip() if c.text else default
    return default


# ---------------------------------------------------------------------------
# Date parsing
# ---------------------------------------------------------------------------

_QUALITY = {"estimated": 1, "calculated": 2}
_MODIFIER = {"before": 1, "after": 2, "about": 3}


def _split_date(val: str):
    """Parse 'YYYY-MM-DD', 'YYYY-MM', or 'YYYY' → (year, month, day)."""
    parts = val.split("-") if val else []
    try:
        y = int(parts[0]) if len(parts) > 0 else 0
        m = int(parts[1]) if len(parts) > 1 else 0
        d = int(parts[2]) if len(parts) > 2 else 0
        return y, m, d
    except (ValueError, IndexError):
        return 0, 0, 0


def _parse_date(el) -> Dict:
    """Convert a dateval/daterange/datespan/datestr element to a date dict.

    The dict matches what format_date() in date_handler.py expects:
      dateval: [day, month, year, False]   (month is 1-indexed)
      modifier: int (0=regular, 1=before, 2=after, 3=about, 4=between, 5=span)
      quality: int (0=regular, 1=estimated, 2=calculated)
      string: str (pre-formatted fallback or empty)
    """
    t = _tag(el)

    if t == "dateval":
        y, m, d = _split_date(el.get("val", ""))
        return {
            "dateval": [d, m, y, False],
            "modifier": _MODIFIER.get(el.get("type", ""), 0),
            "quality": _QUALITY.get(el.get("quality", ""), 0),
            "string": "",
        }

    if t == "daterange":
        start, stop = el.get("start", ""), el.get("stop", "")
        y, m, d = _split_date(start)
        return {
            "dateval": [d, m, y, False],
            "modifier": 4,  # between
            "quality": _QUALITY.get(el.get("quality", ""), 0),
            "string": f"between {start} and {stop}" if start else "",
        }

    if t == "datespan":
        start, stop = el.get("start", ""), el.get("stop", "")
        y, m, d = _split_date(start)
        return {
            "dateval": [d, m, y, False],
            "modifier": 5,  # from...to
            "quality": _QUALITY.get(el.get("quality", ""), 0),
            "string": f"from {start} to {stop}" if start else "",
        }

    if t == "datestr":
        return {
            "dateval": [],
            "modifier": 6,  # text only
            "quality": 0,
            "string": el.get("val", ""),
        }

    return {}


def _find_date(el) -> Dict:
    """Return the first date child of el, or {}."""
    for c in el:
        if _tag(c) in ("dateval", "daterange", "datespan", "datestr"):
            return _parse_date(c)
    return {}


# ---------------------------------------------------------------------------
# Object parsers  (XML element → plain dict matching Web API format)
# ---------------------------------------------------------------------------


def _parse_name(name_el) -> Dict:
    """Parse a <name> element into a primary_name dict."""
    children = {_tag(c): c for c in name_el}

    # Surname: <surname> (1.7+) or <last> (older)
    # Note: must use 'is not None' — xml.etree elements are falsy when childless
    sn_el = children.get("surname")
    if sn_el is None:
        sn_el = children.get("last")
    surname = sn_el.text.strip() if sn_el is not None and sn_el.text else ""

    fn_el = children.get("first")
    first = fn_el.text.strip() if fn_el is not None and fn_el.text else ""

    suffix_el = children.get("suffix")
    suffix = suffix_el.text.strip() if suffix_el is not None and suffix_el.text else ""

    call_el = children.get("call")
    call = call_el.text.strip() if call_el is not None and call_el.text else ""

    nick_el = children.get("nick")
    nick = nick_el.text.strip() if nick_el is not None and nick_el.text else ""

    title_el = children.get("title")
    title = title_el.text.strip() if title_el is not None and title_el.text else ""

    return {
        "first_name": first,
        "surname_list": [
            {"surname": surname, "primary": True, "prefix": "", "connector": ""}
        ],
        "suffix": suffix,
        "title": title,
        "call": call,
        "nick": nick,
        "type": name_el.get("type", "Birth Name"),
    }


def _parse_address(el) -> Dict:
    """Parse an <address> element to an address dict."""
    return {
        "street": _text(el, "street"),
        "locality": _text(el, "locality"),
        "city": _text(el, "city"),
        "county": _text(el, "county"),
        "state": _text(el, "state"),
        "country": _text(el, "country"),
        "postal": _text(el, "postal"),
        "phone": _text(el, "phone"),
        "date": _find_date(el),
        "note_list": [c.get("hlink", "") for c in el if _tag(c) == "noteref"],
    }


def _parse_person(el, events_by_handle: Dict) -> Dict:
    """Convert a <person> element to a dict matching the Web API format."""
    handle = el.get("handle", "")
    gramps_id = el.get("id", "")

    # Gender (M/F/U → 1/0/2)
    gender_map = {"M": 1, "F": 0, "U": 2}
    gender = gender_map.get(_text(el, "gender"), 2)

    # Primary name: prefer Birth Name, then first available
    name_els = [c for c in el if _tag(c) == "name"]
    birth_name_types = ("Birth Name", "Geburtsname", "")
    birth_names = [n for n in name_els if n.get("type", "") in birth_name_types]
    name_el = birth_names[0] if birth_names else (name_els[0] if name_els else None)
    primary_name = _parse_name(name_el) if name_el is not None else {
        "first_name": "",
        "surname_list": [
            {"surname": "", "primary": True, "prefix": "", "connector": ""}
        ],
    }

    # Event refs (preserve order — used for birth/death index)
    event_refs = [c for c in el if _tag(c) == "eventref"]
    event_ref_list = [
        {"ref": c.get("hlink", ""), "role": c.get("role", "")} for c in event_refs
    ]

    # Birth and death ref indices — look up event types from the pre-built index
    birth_ref_index = -1
    death_ref_index = -1
    for i, eref in enumerate(event_ref_list):
        etype = events_by_handle.get(eref["ref"], {}).get("type", "")
        if birth_ref_index == -1 and etype == "Birth":
            birth_ref_index = i
        if death_ref_index == -1 and etype in ("Death", "Burial", "Cremation"):
            death_ref_index = i

    # Family links
    parentin = [c.get("hlink", "") for c in el if _tag(c) == "parentin"]
    childof = [c.get("hlink", "") for c in el if _tag(c) == "childof"]

    # Attached objects
    note_list = [c.get("hlink", "") for c in el if _tag(c) == "noteref"]
    citation_list = [c.get("hlink", "") for c in el if _tag(c) == "citationref"]
    # DTD: objref* — media object references (tag is <objref>, not <mediaref>)
    media_list = [{"ref": c.get("hlink", "")} for c in el if _tag(c) == "objref"]

    urls = []
    for u in [c for c in el if _tag(c) == "url"]:
        urls.append(
            {
                "path": u.get("href", ""),
                "description": u.get("description", ""),
                "type": u.get("type", ""),
            }
        )

    address_list = [_parse_address(c) for c in el if _tag(c) == "address"]

    return {
        "gramps_id": gramps_id,
        "handle": handle,
        "change": int(el.get("change", "0") or "0"),
        "gender": gender,
        "primary_name": primary_name,
        "birth_ref_index": birth_ref_index,
        "death_ref_index": death_ref_index,
        "event_ref_list": event_ref_list,
        "family_list": parentin,        # families where this person is spouse/parent
        "parent_family_list": childof,  # families where this person is a child
        "note_list": note_list,
        "citation_list": citation_list,
        "media_list": media_list,
        "urls": urls,
        "address_list": address_list,
    }


def _parse_event(el) -> Dict:
    """Convert an <event> element to dict."""
    handle = el.get("handle", "")
    gramps_id = el.get("id", "")

    event_type = _text(el, "type")
    description = _text(el, "description")
    date = _find_date(el)

    place_el = next((c for c in el if _tag(c) == "place"), None)
    place = place_el.get("hlink", "") if place_el is not None else ""

    return {
        "gramps_id": gramps_id,
        "handle": handle,
        "change": int(el.get("change", "0") or "0"),
        "type": event_type,
        "date": date,
        "place": place,
        "description": description,
        "note_list": [c.get("hlink", "") for c in el if _tag(c) == "noteref"],
        "citation_list": [c.get("hlink", "") for c in el if _tag(c) == "citationref"],
    }


def _parse_family(el) -> Dict:
    """Convert a <family> element to dict."""
    handle = el.get("handle", "")
    gramps_id = el.get("id", "")

    rel_el = next((c for c in el if _tag(c) == "rel"), None)
    relationship = rel_el.get("type", "Married") if rel_el is not None else "Married"

    father_el = next((c for c in el if _tag(c) == "father"), None)
    mother_el = next((c for c in el if _tag(c) == "mother"), None)
    father_handle = father_el.get("hlink", "") if father_el is not None else ""
    mother_handle = mother_el.get("hlink", "") if mother_el is not None else ""

    child_ref_list = [
        {
            "ref": c.get("hlink", ""),
            "frel": c.get("frel", "Birth"),
            "mrel": c.get("mrel", "Birth"),
        }
        for c in el
        if _tag(c) == "childref"
    ]

    event_ref_list = [
        {"ref": c.get("hlink", ""), "role": c.get("role", "")}
        for c in el
        if _tag(c) == "eventref"
    ]

    return {
        "gramps_id": gramps_id,
        "handle": handle,
        "change": int(el.get("change", "0") or "0"),
        "father_handle": father_handle,
        "mother_handle": mother_handle,
        "child_ref_list": child_ref_list,
        "event_ref_list": event_ref_list,
        "relationship": relationship,
        "note_list": [c.get("hlink", "") for c in el if _tag(c) == "noteref"],
        "citation_list": [c.get("hlink", "") for c in el if _tag(c) == "citationref"],
        "media_list": [{"ref": c.get("hlink", "")} for c in el if _tag(c) == "objref"],
    }


def _parse_place(el) -> Dict:
    """Convert a <placeobj> element to dict."""
    handle = el.get("handle", "")
    gramps_id = el.get("id", "")

    ptitle_el = next((c for c in el if _tag(c) == "ptitle"), None)
    pname_el = next((c for c in el if _tag(c) == "pname"), None)

    title = ptitle_el.text.strip() if ptitle_el is not None and ptitle_el.text else ""
    pname_value = pname_el.get("value", "") if pname_el is not None else ""
    display_name = pname_value or title

    placeref_list = [
        {"ref": c.get("hlink", "")} for c in el if _tag(c) == "placeref"
    ]

    return {
        "gramps_id": gramps_id,
        "handle": handle,
        "change": int(el.get("change", "0") or "0"),
        "title": title,
        "name": {"value": display_name},
        "place_type": el.get("type", ""),
        "placeref_list": placeref_list,
        "urls": [
            {
                "path": u.get("href", ""),
                "description": u.get("description", ""),
                "type": u.get("type", ""),
            }
            for u in el
            if _tag(u) == "url"
        ],
    }


def _parse_source(el) -> Dict:
    """Convert a <source> element to dict."""
    handle = el.get("handle", "")
    gramps_id = el.get("id", "")

    return {
        "gramps_id": gramps_id,
        "handle": handle,
        "change": int(el.get("change", "0") or "0"),
        "title": _text(el, "stitle"),
        "author": _text(el, "sauthor"),
        "pubinfo": _text(el, "spubinfo"),
        "abbrev": _text(el, "sabbrev"),
        "note_list": [c.get("hlink", "") for c in el if _tag(c) == "noteref"],
        "media_list": [
            {"ref": c.get("hlink", "")} for c in el if _tag(c) == "objref"
        ],
        "reporef_list": [
            {
                "ref": c.get("hlink", ""),
                "callno": c.get("callno", ""),
                "medium": c.get("medium", ""),
            }
            for c in el
            if _tag(c) == "reporef"
        ],
        "attribute_list": [
            {"key": c.get("key", ""), "value": c.get("value", "")}
            for c in el
            if _tag(c) == "data_item"
        ],
    }


def _parse_citation(el) -> Dict:
    """Convert a <citation> element to dict."""
    handle = el.get("handle", "")
    gramps_id = el.get("id", "")

    page = _text(el, "page")
    confidence_str = _text(el, "confidence")
    confidence = int(confidence_str) if confidence_str.isdigit() else 0

    sourceref_el = next((c for c in el if _tag(c) == "sourceref"), None)
    source_handle = sourceref_el.get("hlink", "") if sourceref_el is not None else ""

    return {
        "gramps_id": gramps_id,
        "handle": handle,
        "change": int(el.get("change", "0") or "0"),
        "page": page,
        "confidence": confidence,
        "source_handle": source_handle,
        "date": _find_date(el),
        "note_list": [c.get("hlink", "") for c in el if _tag(c) == "noteref"],
    }


def _parse_note(el) -> Dict:
    """Convert a <note> element to dict."""
    handle = el.get("handle", "")
    gramps_id = el.get("id", "")

    text_el = next((c for c in el if _tag(c) == "text"), None)
    text = text_el.text.strip() if text_el is not None and text_el.text else ""

    return {
        "gramps_id": gramps_id,
        "handle": handle,
        "change": int(el.get("change", "0") or "0"),
        "type": el.get("type", ""),
        "text": {"string": text},
    }


def _parse_media(el) -> Dict:
    """Convert an <object> element to dict."""
    handle = el.get("handle", "")
    gramps_id = el.get("id", "")

    file_el = next((c for c in el if _tag(c) == "file"), None)
    src = file_el.get("src", "") if file_el is not None else ""
    mime = file_el.get("mime", "") if file_el is not None else ""
    description = file_el.get("description", "") if file_el is not None else ""
    checksum = file_el.get("checksum", "") if file_el is not None else ""

    return {
        "gramps_id": gramps_id,
        "handle": handle,
        "change": int(el.get("change", "0") or "0"),
        "path": src,
        "mime": mime,
        "desc": description,
        "checksum": checksum,
        "date": _find_date(el),
        "note_list": [c.get("hlink", "") for c in el if _tag(c) == "noteref"],
    }


def _parse_repository(el) -> Dict:
    """Convert a <repository> element to dict."""
    handle = el.get("handle", "")
    gramps_id = el.get("id", "")

    name = _text(el, "rname") or gramps_id
    repo_type = _text(el, "type")

    return {
        "gramps_id": gramps_id,
        "handle": handle,
        "change": int(el.get("change", "0") or "0"),
        "name": name,
        "type": repo_type,
        "urls": [
            {
                "path": u.get("href", ""),
                "description": u.get("description", ""),
                "type": u.get("type", ""),
            }
            for u in el
            if _tag(u) == "url"
        ],
        "note_list": [c.get("hlink", "") for c in el if _tag(c) == "noteref"],
    }
