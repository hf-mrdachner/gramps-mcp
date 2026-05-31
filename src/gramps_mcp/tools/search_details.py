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
Detail retrieval MCP tools for genealogy operations.

This module contains detail retrieval tools for getting comprehensive
person, family, and event information using direct API calls.
"""

import logging
from typing import Dict, List

from mcp.types import TextContent

from ..client import GrampsAPIError
from ..config import get_settings
from ..handlers.date_handler import format_date
from ..handlers.family_detail_handler import format_family_detail
from ..handlers.person_detail_handler import format_person_detail
from ..handlers.place_handler import format_place
from ..models.api_calls import ApiCalls
from .search_basic import with_client

logger = logging.getLogger(__name__)


def _format_error_response(error: Exception, operation: str) -> List[TextContent]:
    """Format error into user-friendly MCP response."""
    if isinstance(error, GrampsAPIError):
        error_msg = str(error)
    else:
        error_msg = f"Unexpected error during {operation}: {str(error)}"

    logger.error(f"Tool error in {operation}: {error_msg}")
    return [TextContent(type="text", text=f"Error: {error_msg}")]


@with_client
async def get_person_tool(client, arguments: Dict) -> List[TextContent]:
    """
    Get comprehensive person information including parents, siblings, spouse and children.
    Accepts gramps_id (e.g. 'I0001') or person_handle.
    """
    try:
        handle = arguments.get("person_handle") or arguments.get("handle")
        gramps_id = arguments.get("gramps_id")

        settings = get_settings()
        tree_id = settings.gramps_tree_id

        # Resolve gramps_id to handle if needed
        if not handle and gramps_id:
            people = await client.make_api_call(
                ApiCalls.GET_PEOPLE, tree_id=tree_id,
                params={"gramps_id": gramps_id, "pagesize": 1},
            )
            if not people:
                return [TextContent(type="text", text=f"Person {gramps_id} not found")]
            handle = people[0].get("handle", "")

        if not handle:
            raise ValueError("gramps_id or person_handle required")

        formatted_person = await format_person_detail(client, tree_id, handle)
        return [TextContent(type="text", text=formatted_person)]

    except Exception as e:
        return _format_error_response(e, "person details retrieval")


@with_client
async def get_family_tool(client, arguments: Dict) -> List[TextContent]:
    """
    Get detailed family information using direct API calls.
    """
    try:
        # Extract handle from arguments
        handle = arguments.get("family_handle")
        if not handle:
            raise ValueError("family_handle is required")

        # Get tree_id from settings
        settings = get_settings()
        tree_id = settings.gramps_tree_id

        # Use the detailed family handler to get comprehensive formatted data
        formatted_family = await format_family_detail(client, tree_id, handle)

        return [TextContent(type="text", text=formatted_family)]

    except Exception as e:
        return _format_error_response(e, "family details retrieval")


@with_client
async def get_event_tool(client, arguments: Dict) -> List[TextContent]:
    """
    Get event details by gramps_id and find which persons have this event.
    Scans all persons in the database — fact-based, no guessing.
    """
    try:
        gramps_id = arguments.get("gramps_id")
        if not gramps_id:
            raise ValueError("gramps_id is required")

        settings = get_settings()
        tree_id = settings.gramps_tree_id

        # Resolve event by gramps_id
        events = await client.make_api_call(
            ApiCalls.GET_EVENTS, tree_id=tree_id,
            params={"gramps_id": gramps_id, "pagesize": 1},
        )
        if not events:
            return [TextContent(type="text", text=f"Event {gramps_id} not found")]

        event = events[0]
        handle = event.get("handle", "")

        # Format event details
        event_type = event.get("type", "Unknown")
        date = format_date(event.get("date", {}))
        place_handle = event.get("place", "")
        place = await format_place(client, tree_id, place_handle, inline=True)
        description = event.get("description", "")

        lines = [f"## {event_type} — {gramps_id} [{handle}]", ""]
        lines.append(f"* Datum: {date}")
        if place:
            lines.append(f"* Ort: {place}")
        if description:
            lines.append(f"* Beschreibung: {description}")

        # Citations
        for ch in event.get("citation_list", []):
            try:
                cit = await client.make_api_call(
                    ApiCalls.GET_CITATION, tree_id=tree_id, handle=ch
                )
                lines.append(f"* Citation: {cit.get('gramps_id', '?')}")
            except Exception:
                lines.append(f"* Citation: [broken ref {ch[:16]}]")

        # Find persons — scan all, GQL cannot search inside arrays
        all_persons = await client.make_api_call(
            ApiCalls.GET_PEOPLE, tree_id=tree_id, params={"pagesize": 99999}
        )

        lines.append("")
        lines.append("**Personen mit diesem Event:**")
        found = []
        for person in all_persons:
            for ref in person.get("event_ref_list", []):
                ref_handle = ref.get("ref", "") if isinstance(ref, dict) else ref
                if ref_handle == handle:
                    role = ref.get("role", "Primary") if isinstance(ref, dict) else "Primary"
                    pn = person.get("primary_name", {})
                    given = pn.get("first_name", "")
                    sl = pn.get("surname_list", [])
                    surname = sl[0].get("surname", "") if sl else ""
                    name = f"{given} {surname}".strip() or "?"
                    pid = person.get("gramps_id", "")
                    found.append(f"* {name} ({pid}) — {role}")
                    break

        if found:
            lines.extend(found)
        else:
            lines.append("* Keine Person gefunden")

        return [TextContent(type="text", text="\n".join(lines))]

    except Exception as e:
        return _format_error_response(e, "event details retrieval")


@with_client
async def get_place_tool(client, arguments: Dict) -> List[TextContent]:
    """
    Get place details and all events/persons at this place.
    Scans all events — fact-based, no guessing.
    """
    try:
        gramps_id = arguments.get("gramps_id")
        if not gramps_id:
            raise ValueError("gramps_id is required")

        settings = get_settings()
        tree_id = settings.gramps_tree_id

        # Resolve place by gramps_id
        places = await client.make_api_call(
            ApiCalls.GET_PLACES, tree_id=tree_id,
            params={"gramps_id": gramps_id, "pagesize": 1},
        )
        if not places:
            return [TextContent(type="text", text=f"Place {gramps_id} not found")]

        place = places[0]
        handle = place.get("handle", "")
        name = place.get("name", {}).get("value", "") or gramps_id
        place_type = place.get("type", "")
        lat = place.get("lat", "")
        lon = place.get("long", "")
        urls = place.get("urls", [])

        lines = [f"## {name} ({place_type}) — {gramps_id} [{handle}]", ""]
        if lat and lon:
            lines.append(f"* Koordinaten: {lat}°N {lon}°E")
        for url in urls:
            if isinstance(url, dict):
                lines.append(f"* {url.get('description', 'URL')}: {url.get('path', '')}")

        # Scan all events for this place handle
        all_events = await client.make_api_call(
            ApiCalls.GET_EVENTS, tree_id=tree_id, params={"pagesize": 99999}
        )

        # Build event handle → person name lookup via all persons
        all_persons = await client.make_api_call(
            ApiCalls.GET_PEOPLE, tree_id=tree_id, params={"pagesize": 99999}
        )
        event_to_persons: dict = {}
        for person in all_persons:
            pn = person.get("primary_name", {})
            given = pn.get("first_name", "")
            sl = pn.get("surname_list", [])
            surname = sl[0].get("surname", "") if sl else ""
            name_str = f"{given} {surname}".strip() or "?"
            pid = person.get("gramps_id", "")
            for ref in person.get("event_ref_list", []):
                eh = ref.get("ref", "") if isinstance(ref, dict) else ref
                if eh:
                    event_to_persons.setdefault(eh, []).append(f"{name_str} ({pid})")

        # Collect events at this place
        hits = []
        for event in all_events:
            if event.get("place", "") == handle:
                etype = event.get("type", "?")
                date = format_date(event.get("date", {}))
                eid = event.get("gramps_id", "")
                ehandle = event.get("handle", "")
                persons = event_to_persons.get(ehandle, [])
                persons_str = ", ".join(persons) if persons else "—"
                hits.append((date, f"* {date} — {etype} ({eid}): {persons_str}"))

        lines.append(f"**Events an diesem Ort ({len(hits)}):**")
        if hits:
            for _, line in sorted(hits):
                lines.append(line)
        else:
            lines.append("* Keine Events gefunden")

        return [TextContent(type="text", text="\n".join(lines))]

    except Exception as e:
        return _format_error_response(e, "place details retrieval")


@with_client
async def merge_places_tool(client, arguments: Dict) -> List[TextContent]:
    """
    Merge a duplicate place (loser) into a canonical place (winner).
    Redirects all events from loser to winner. dry_run=True by default.
    """
    try:
        winner_id = arguments.get("winner_id")
        loser_id = arguments.get("loser_id")
        dry_run = arguments.get("dry_run", True)

        if not winner_id or not loser_id:
            raise ValueError("winner_id and loser_id are required")

        settings = get_settings()
        tree_id = settings.gramps_tree_id

        # Resolve both places
        winners = await client.make_api_call(
            ApiCalls.GET_PLACES, tree_id=tree_id,
            params={"gramps_id": winner_id, "pagesize": 1}
        )
        losers = await client.make_api_call(
            ApiCalls.GET_PLACES, tree_id=tree_id,
            params={"gramps_id": loser_id, "pagesize": 1}
        )

        if not winners:
            return [TextContent(type="text", text=f"Winner {winner_id} nicht gefunden")]
        if not losers:
            return [TextContent(type="text", text=f"Loser {loser_id} nicht gefunden")]

        winner = winners[0]
        loser = losers[0]
        winner_handle = winner["handle"]
        loser_handle = loser["handle"]
        winner_name = winner.get("name", {}).get("value", winner_id)
        loser_name = loser.get("name", {}).get("value", loser_id)

        if winner_handle == loser_handle:
            return [TextContent(type="text", text="Winner und Loser sind identisch — nichts zu tun.")]

        # Scan all events for loser place
        all_events = await client.make_api_call(
            ApiCalls.GET_EVENTS, tree_id=tree_id, params={"pagesize": 99999}
        )
        affected_events = [e for e in all_events if e.get("place", "") == loser_handle]

        # Scan all places for child-places that reference the loser as parent
        all_places = await client.make_api_call(
            ApiCalls.GET_PLACES, tree_id=tree_id, params={"pagesize": 99999}
        )
        affected_child_places = [
            p for p in all_places
            if any(
                (r.get("ref", "") if isinstance(r, dict) else r) == loser_handle
                for r in p.get("placeref_list", [])
            )
        ]

        # Transfer loser's placeref_list to winner if winner has none
        loser_placrefs = loser.get("placeref_list", [])
        winner_placrefs = winner.get("placeref_list", [])
        transfer_placrefs = loser_placrefs if loser_placrefs and not winner_placrefs else []

        prefix = "[DRY RUN] " if dry_run else ""
        lines = [
            f"{prefix}merge_places: {loser_name} ({loser_id}) → {winner_name} ({winner_id})",
            f"{len(affected_events)} Events, {len(affected_child_places)} untergeordnete Orte betroffen",
        ]
        if transfer_placrefs:
            planned = "[PLANNED] " if dry_run else ""
            lines.append(f"{planned}Übergeordnete Orte vom Loser übernommen: {len(transfer_placrefs)}")
        lines.append("")

        for event in affected_events:
            eid = event.get("gramps_id", "?")
            etype = event.get("type", "?")
            date_str = format_date(event.get("date", {}))
            lines.append(f"* Event: {etype} {date_str} ({eid})")
        for place in affected_child_places:
            lines.append(f"* Ort: {place.get('name', {}).get('value', '?')} ({place.get('gramps_id', '?')})")

        if not dry_run:
            # Redirect events
            for event in affected_events:
                await client.make_api_call(
                    ApiCalls.PUT_EVENT, tree_id=tree_id, handle=event["handle"],
                    params={"handle": event["handle"], "place": winner_handle},
                )
            # Redirect child places: replace loser ref with winner ref
            for place in affected_child_places:
                new_refs = [
                    {**r, "ref": winner_handle} if (r.get("ref") if isinstance(r, dict) else r) == loser_handle else r
                    for r in place.get("placeref_list", [])
                ]
                await client.make_api_call(
                    ApiCalls.PUT_PLACE, tree_id=tree_id, handle=place["handle"],
                    params={"handle": place["handle"], "placeref_list": new_refs},
                )
            # Transfer parent hierarchy if winner has none
            if transfer_placrefs:
                await client.make_api_call(
                    ApiCalls.PUT_PLACE, tree_id=tree_id, handle=winner_handle,
                    params={"handle": winner_handle, "placeref_list": transfer_placrefs},
                )
            # Delete loser
            await client.make_api_call(
                ApiCalls.DELETE_PLACE, tree_id=tree_id, handle=loser_handle
            )
            lines.append(f"\nFertig. {loser_id} ({loser_name}) gelöscht.")

        if dry_run:
            lines.append("\nRe-run mit dry_run=False um anzuwenden.")

        return [TextContent(type="text", text="\n".join(lines))]

    except Exception as e:
        return _format_error_response(e, "merge places")


@with_client
async def merge_events_tool(client, arguments: Dict) -> List[TextContent]:
    """
    Merge a duplicate event (loser) into a canonical event (winner).
    Transfers citations, notes, media and attributes from loser to winner.
    Removes loser from all person and family event_ref_lists, then deletes loser.
    dry_run=True by default.
    """
    try:
        winner_id = arguments.get("winner_id")
        loser_id = arguments.get("loser_id")
        dry_run = arguments.get("dry_run", True)

        if not winner_id or not loser_id:
            raise ValueError("winner_id and loser_id are required")

        settings = get_settings()
        tree_id = settings.gramps_tree_id

        winners = await client.make_api_call(
            ApiCalls.GET_EVENTS, tree_id=tree_id,
            params={"gramps_id": winner_id, "pagesize": 1}
        )
        losers = await client.make_api_call(
            ApiCalls.GET_EVENTS, tree_id=tree_id,
            params={"gramps_id": loser_id, "pagesize": 1}
        )
        if not winners:
            return [TextContent(type="text", text=f"Winner {winner_id} nicht gefunden")]
        if not losers:
            return [TextContent(type="text", text=f"Loser {loser_id} nicht gefunden")]

        winner = winners[0]
        loser = losers[0]
        winner_handle = winner["handle"]
        loser_handle = loser["handle"]

        if winner_handle == loser_handle:
            return [TextContent(type="text", text="Winner und Loser sind identisch.")]

        def _merge_unique(winner_lst: list, loser_lst: list) -> list:
            """Append loser items not already in winner (by handle/identity)."""
            def _key(i):
                return (i.get("ref") if isinstance(i, dict) else i) or str(i)

            existing = {_key(i) for i in winner_lst}
            return winner_lst + [i for i in loser_lst if _key(i) not in existing]

        # Merge citations, notes, media, attributes from loser (no duplicates)
        merged_cits = winner.get("citation_list", []) + [
            c for c in loser.get("citation_list", [])
            if c not in winner.get("citation_list", [])
        ]
        merged_notes = _merge_unique(winner.get("note_list", []), loser.get("note_list", []))
        merged_media = _merge_unique(winner.get("media_list", []), loser.get("media_list", []))
        merged_attrs = _merge_unique(winner.get("attribute_list", []), loser.get("attribute_list", []))

        def _has_event_ref(obj: dict) -> bool:
            return any(
                (r.get("ref", "") if isinstance(r, dict) else r) == loser_handle
                for r in obj.get("event_ref_list", [])
            )

        # Find persons and families with loser event
        all_persons = await client.make_api_call(
            ApiCalls.GET_PEOPLE, tree_id=tree_id, params={"pagesize": 99999}
        )
        all_families = await client.make_api_call(
            ApiCalls.GET_FAMILIES, tree_id=tree_id, params={"pagesize": 99999}
        )
        affected_persons = [p for p in all_persons if _has_event_ref(p)]
        affected_families = [f for f in all_families if _has_event_ref(f)]

        prefix = "[DRY RUN] " if dry_run else ""
        winner_date = format_date(winner.get("date", {}))
        loser_date = format_date(loser.get("date", {}))
        loser_cits = loser.get("citation_list", [])
        winner_cits = winner.get("citation_list", [])
        lines = [
            f"{prefix}merge_events: {loser_id} ({loser_date}) → {winner_id} ({winner_date})",
            f"Citations: {len(winner_cits)} + {len(loser_cits)} → {len(merged_cits)}",
            f"Personen betroffen: {len(affected_persons)}, Familien betroffen: {len(affected_families)}",
            "",
        ]
        for p in affected_persons:
            pn = p.get("primary_name", {})
            given = pn.get("first_name", "")
            sl = pn.get("surname_list", [])
            surname = sl[0].get("surname", "") if sl else ""
            lines.append(f"* Person: {given} {surname}".strip() + f" ({p.get('gramps_id', '?')})")
        for f in affected_families:
            lines.append(f"* Familie ({f.get('gramps_id', '?')})")

        if not dry_run:
            # Update winner: merge citations, notes, media, attributes
            await client.make_api_call(
                ApiCalls.PUT_EVENT, tree_id=tree_id, handle=winner_handle,
                params={
                    "handle": winner_handle,
                    "citation_list": merged_cits,
                    "note_list": merged_notes,
                    "media_list": merged_media,
                    "attribute_list": merged_attrs,
                }
            )
            # Remove loser from each person's event_ref_list
            for person in affected_persons:
                new_refs = [
                    r for r in person.get("event_ref_list", [])
                    if (r.get("ref", "") if isinstance(r, dict) else r) != loser_handle
                ]
                await client.make_api_call(
                    ApiCalls.PUT_PERSON, tree_id=tree_id, handle=person["handle"],
                    params={"handle": person["handle"], "event_ref_list": new_refs}
                )
            # Remove loser from each family's event_ref_list
            for family in affected_families:
                new_refs = [
                    r for r in family.get("event_ref_list", [])
                    if (r.get("ref", "") if isinstance(r, dict) else r) != loser_handle
                ]
                await client.make_api_call(
                    ApiCalls.PUT_FAMILY, tree_id=tree_id, handle=family["handle"],
                    params={"handle": family["handle"], "event_ref_list": new_refs}
                )
            # Delete loser event
            await client.make_api_call(
                ApiCalls.DELETE_EVENT, tree_id=tree_id, handle=loser_handle
            )
            lines.append(f"\nFertig. {loser_id} gelöscht, Citations/Notes/Media gesichert.")
        else:
            lines.append("\nRe-run mit dry_run=False um anzuwenden.")

        return [TextContent(type="text", text="\n".join(lines))]

    except Exception as e:
        return _format_error_response(e, "merge events")


@with_client
async def merge_citations_tool(client, arguments: Dict) -> List[TextContent]:
    """
    Merge a duplicate citation (loser) into a canonical citation (winner).
    Redirects citation references from loser to winner across events, persons,
    families, places, and media, then deletes loser.
    dry_run=True by default.
    """
    try:
        winner_id = arguments.get("winner_id")
        loser_id = arguments.get("loser_id")
        dry_run = arguments.get("dry_run", True)

        if not winner_id or not loser_id:
            raise ValueError("winner_id and loser_id are required")

        settings = get_settings()
        tree_id = settings.gramps_tree_id

        winners = await client.make_api_call(
            ApiCalls.GET_CITATIONS, tree_id=tree_id,
            params={"gramps_id": winner_id, "pagesize": 1}
        )
        losers = await client.make_api_call(
            ApiCalls.GET_CITATIONS, tree_id=tree_id,
            params={"gramps_id": loser_id, "pagesize": 1}
        )
        if not winners:
            return [TextContent(type="text", text=f"Winner {winner_id} nicht gefunden")]
        if not losers:
            return [TextContent(type="text", text=f"Loser {loser_id} nicht gefunden")]

        winner_handle = winners[0]["handle"]
        loser_handle = losers[0]["handle"]

        if winner_handle == loser_handle:
            return [TextContent(type="text", text="Winner und Loser sind identisch.")]

        def _has_citation(obj: dict) -> bool:
            return loser_handle in obj.get("citation_list", [])

        def _replace_citation(cits: list) -> list:
            """Replace loser with winner, deduplicate."""
            new_cits: list = []
            seen: set = set()
            for c in cits:
                target = winner_handle if c == loser_handle else c
                if target not in seen:
                    new_cits.append(target)
                    seen.add(target)
            return new_cits

        # Scan all object types that carry citation_list
        all_events = await client.make_api_call(
            ApiCalls.GET_EVENTS, tree_id=tree_id, params={"pagesize": 99999}
        )
        all_persons = await client.make_api_call(
            ApiCalls.GET_PEOPLE, tree_id=tree_id, params={"pagesize": 99999}
        )
        all_families = await client.make_api_call(
            ApiCalls.GET_FAMILIES, tree_id=tree_id, params={"pagesize": 99999}
        )
        all_places = await client.make_api_call(
            ApiCalls.GET_PLACES, tree_id=tree_id, params={"pagesize": 99999}
        )
        all_media = await client.make_api_call(
            ApiCalls.GET_MEDIA, tree_id=tree_id, params={"pagesize": 99999}
        )

        affected_events = [e for e in all_events if _has_citation(e)]
        affected_persons = [p for p in all_persons if _has_citation(p)]
        affected_families = [f for f in all_families if _has_citation(f)]
        affected_places = [p for p in all_places if _has_citation(p)]
        affected_media = [m for m in all_media if _has_citation(m)]

        total = (len(affected_events) + len(affected_persons) + len(affected_families)
                 + len(affected_places) + len(affected_media))
        prefix = "[DRY RUN] " if dry_run else ""
        lines = [
            f"{prefix}merge_citations: {loser_id} → {winner_id}",
            (f"{total} Objekte betroffen ({len(affected_events)} Events, "
             f"{len(affected_persons)} Personen, {len(affected_families)} Familien, "
             f"{len(affected_places)} Orte, {len(affected_media)} Medien)"),
            "",
        ]
        for e in affected_events:
            lines.append(f"* Event: {e.get('type', '?')} ({e.get('gramps_id', '?')})")
        for p in affected_persons:
            pn = p.get("primary_name", {})
            given = pn.get("first_name", "")
            sl = pn.get("surname_list", [])
            surname = sl[0].get("surname", "") if sl else ""
            lines.append(f"* Person: {given} {surname}".strip() + f" ({p.get('gramps_id', '?')})")
        for f in affected_families:
            lines.append(f"* Familie ({f.get('gramps_id', '?')})")
        for p in affected_places:
            lines.append(f"* Ort: {p.get('name', {}).get('value', '?')} ({p.get('gramps_id', '?')})")
        for m in affected_media:
            lines.append(f"* Medium ({m.get('gramps_id', '?')})")

        if not dry_run:
            for event in affected_events:
                await client.make_api_call(
                    ApiCalls.PUT_EVENT, tree_id=tree_id, handle=event["handle"],
                    params={"handle": event["handle"],
                            "citation_list": _replace_citation(event.get("citation_list", []))}
                )
            for person in affected_persons:
                await client.make_api_call(
                    ApiCalls.PUT_PERSON, tree_id=tree_id, handle=person["handle"],
                    params={"handle": person["handle"],
                            "citation_list": _replace_citation(person.get("citation_list", []))}
                )
            for family in affected_families:
                await client.make_api_call(
                    ApiCalls.PUT_FAMILY, tree_id=tree_id, handle=family["handle"],
                    params={"handle": family["handle"],
                            "citation_list": _replace_citation(family.get("citation_list", []))}
                )
            for place in affected_places:
                await client.make_api_call(
                    ApiCalls.PUT_PLACE, tree_id=tree_id, handle=place["handle"],
                    params={"handle": place["handle"],
                            "citation_list": _replace_citation(place.get("citation_list", []))}
                )
            for media in affected_media:
                await client.make_api_call(
                    ApiCalls.PUT_MEDIA_ITEM, tree_id=tree_id, handle=media["handle"],
                    params={"handle": media["handle"],
                            "citation_list": _replace_citation(media.get("citation_list", []))}
                )
            await client.make_api_call(
                ApiCalls.DELETE_CITATION, tree_id=tree_id, handle=loser_handle
            )
            lines.append(f"\nFertig. {loser_id} gelöscht.")
        else:
            lines.append("\nRe-run mit dry_run=False um anzuwenden.")

        return [TextContent(type="text", text="\n".join(lines))]

    except Exception as e:
        return _format_error_response(e, "merge citations")


@with_client
async def find_duplicate_citations_tool(client, arguments: Dict) -> List[TextContent]:
    """
    Find duplicate citations grouped by (source, page).
    Two citations are duplicates when they reference the same source at the same page.
    Returns groups sorted by count (most duplicates first).
    """
    try:
        settings = get_settings()
        tree_id = settings.gramps_tree_id
        max_results = arguments.get("max_results", 50)
        source_filter = (arguments.get("source_filter") or "").lower()

        all_citations = await client.make_api_call(
            ApiCalls.GET_CITATIONS, tree_id=tree_id, params={"pagesize": 99999}
        )

        # Group by (source_handle, page)
        groups: dict = {}
        for cit in all_citations:
            source_handle = cit.get("source_handle", "")
            page = (cit.get("page") or "").strip()
            key = (source_handle, page)
            groups.setdefault(key, []).append(cit)

        duplicates = {k: v for k, v in groups.items() if len(v) > 1}

        if not duplicates:
            return [TextContent(type="text", text="Keine Duplikate gefunden.")]

        # Resolve source names (cached)
        source_cache: dict = {}
        unique_sources = {sh for sh, _ in duplicates}
        for sh in unique_sources:
            try:
                src = await client.make_api_call(
                    ApiCalls.GET_SOURCE, tree_id=tree_id, handle=sh
                )
                source_cache[sh] = src.get("title", sh[:16])
            except Exception:
                source_cache[sh] = sh[:16]

        # Filter by source name if requested
        if source_filter:
            duplicates = {
                k: v for k, v in duplicates.items()
                if source_filter in source_cache.get(k[0], "").lower()
            }
            if not duplicates:
                return [TextContent(type="text", text="Keine Duplikate gefunden.")]

        # Sort by count descending, limit output
        sorted_groups = sorted(duplicates.items(), key=lambda x: -len(x[1]))[:max_results]

        total = sum(len(v) - 1 for v in duplicates.values())
        lines = [
            f"Citation-Duplikate: {len(duplicates)} Gruppen, {total} überflüssige Citations",
            f"(Zeige {len(sorted_groups)} von {len(duplicates)}, sortiert nach Häufigkeit)",
            "",
        ]
        for (source_handle, page), cits in sorted_groups:
            source_name = source_cache.get(source_handle, "?")
            page_str = f'"{page}"' if page else "(keine Seite)"
            ids = ", ".join(c.get("gramps_id", "?") for c in cits)
            lines.append(f"**{source_name[:70]}**")
            lines.append(f"  Seite: {page_str} → {len(cits)}×: {ids}")

        return [TextContent(type="text", text="\n".join(lines))]

    except Exception as e:
        return _format_error_response(e, "find duplicate citations")


@with_client
async def merge_families_tool(client, arguments: Dict) -> List[TextContent]:
    """
    Merge a duplicate family (loser) into a canonical family (winner).
    - Redirects person family_list / parent_family_list references.
    - Merges loser's child_ref_list, event_ref_list, citation_list,
      note_list, and media_list into winner (no data lost).
    - Deletes the loser family.
    dry_run=True by default.
    """
    try:
        winner_id = arguments.get("winner_id")
        loser_id = arguments.get("loser_id")
        dry_run = arguments.get("dry_run", True)

        if not winner_id or not loser_id:
            raise ValueError("winner_id and loser_id are required")

        settings = get_settings()
        tree_id = settings.gramps_tree_id

        winners = await client.make_api_call(
            ApiCalls.GET_FAMILIES, tree_id=tree_id,
            params={"gramps_id": winner_id, "pagesize": 1}
        )
        losers = await client.make_api_call(
            ApiCalls.GET_FAMILIES, tree_id=tree_id,
            params={"gramps_id": loser_id, "pagesize": 1}
        )
        if not winners:
            return [TextContent(type="text", text=f"Winner {winner_id} nicht gefunden")]
        if not losers:
            return [TextContent(type="text", text=f"Loser {loser_id} nicht gefunden")]

        winner = winners[0]
        loser = losers[0]
        winner_handle = winner["handle"]
        loser_handle = loser["handle"]

        if winner_handle == loser_handle:
            return [TextContent(type="text", text="Winner und Loser sind identisch.")]

        def _merge_ref_list(winner_lst: list, loser_lst: list) -> list:
            """Append loser items not already present in winner.

            Items are either dicts (with a 'ref' key) or plain handle strings.
            """
            def _key(i):
                return i.get("ref") if isinstance(i, dict) else i

            existing = {_key(i) for i in winner_lst}
            return winner_lst + [i for i in loser_lst if _key(i) not in existing]

        # Merge loser's lists into winner
        merged_children = _merge_ref_list(
            winner.get("child_ref_list", []), loser.get("child_ref_list", [])
        )
        merged_events = _merge_ref_list(
            winner.get("event_ref_list", []), loser.get("event_ref_list", [])
        )
        merged_cits = winner.get("citation_list", []) + [
            c for c in loser.get("citation_list", [])
            if c not in winner.get("citation_list", [])
        ]
        merged_notes = _merge_ref_list(winner.get("note_list", []), loser.get("note_list", []))
        merged_media = _merge_ref_list(winner.get("media_list", []), loser.get("media_list", []))

        new_children = len(merged_children) - len(winner.get("child_ref_list", []))
        new_events = len(merged_events) - len(winner.get("event_ref_list", []))

        # Find persons with loser in family_list or parent_family_list
        all_persons = await client.make_api_call(
            ApiCalls.GET_PEOPLE, tree_id=tree_id, params={"pagesize": 99999}
        )
        affected_as_parent = [p for p in all_persons if loser_handle in p.get("family_list", [])]
        affected_as_child = [p for p in all_persons if loser_handle in p.get("parent_family_list", [])]

        prefix = "[DRY RUN] " if dry_run else ""
        lines = [
            f"{prefix}merge_families: {loser_id} → {winner_id}",
            f"{len(affected_as_parent)} Elternteile, {len(affected_as_child)} Kinder betroffen",
            f"+{new_children} Kinder, +{new_events} Events vom Loser übernommen",
            "",
        ]
        for p in affected_as_parent + affected_as_child:
            pn = p.get("primary_name", {})
            given = pn.get("first_name", "")
            sl = pn.get("surname_list", [])
            surname = sl[0].get("surname", "") if sl else ""
            role = "Elternteil" if p in affected_as_parent else "Kind"
            lines.append(f"* {given} {surname} ({p.get('gramps_id', '?')}) — {role}")

        if not dry_run:
            # Update winner: merge children, events, citations, notes, media
            await client.make_api_call(
                ApiCalls.PUT_FAMILY, tree_id=tree_id, handle=winner_handle,
                params={
                    "handle": winner_handle,
                    "child_ref_list": merged_children,
                    "event_ref_list": merged_events,
                    "citation_list": merged_cits,
                    "note_list": merged_notes,
                    "media_list": merged_media,
                }
            )
            # Redirect person family_list references
            for p in affected_as_parent:
                new_fl = [winner_handle if h == loser_handle else h
                          for h in p.get("family_list", [])]
                await client.make_api_call(
                    ApiCalls.PUT_PERSON, tree_id=tree_id, handle=p["handle"],
                    params={"handle": p["handle"], "family_list": new_fl}
                )
            # Redirect person parent_family_list references
            for p in affected_as_child:
                new_pfl = [winner_handle if h == loser_handle else h
                           for h in p.get("parent_family_list", [])]
                await client.make_api_call(
                    ApiCalls.PUT_PERSON, tree_id=tree_id, handle=p["handle"],
                    params={"handle": p["handle"], "parent_family_list": new_pfl}
                )
            await client.make_api_call(
                ApiCalls.DELETE_FAMILY, tree_id=tree_id, handle=loser_handle
            )
            lines.append(f"\nFertig. {loser_id} gelöscht.")
        else:
            lines.append("\nRe-run mit dry_run=False um anzuwenden.")

        return [TextContent(type="text", text="\n".join(lines))]

    except Exception as e:
        return _format_error_response(e, "merge families")


@with_client
async def find_duplicate_events_tool(client, arguments: Dict) -> List[TextContent]:
    """
    Find duplicate events per person: same type + same date on the same person.
    Returns candidate pairs sorted by count. Use merge_events to fix them.
    """
    try:
        from ..models.api_calls import ApiCalls
        from ..handlers.date_handler import format_date

        settings = get_settings()
        tree_id = settings.gramps_tree_id
        max_results = arguments.get("max_results", 50)
        gramps_id_filter = arguments.get("gramps_id")

        all_persons = await client.make_api_call(
            ApiCalls.GET_PEOPLE, tree_id=tree_id, params={"pagesize": 99999}
        )

        if gramps_id_filter:
            all_persons = [p for p in all_persons if p.get("gramps_id") == gramps_id_filter]

        duplicates = []
        for person in all_persons:
            pid = person.get("gramps_id", "")
            pn = person.get("primary_name", {})
            given = pn.get("first_name", "")
            sl = pn.get("surname_list", [])
            surname = sl[0].get("surname", "") if sl else ""
            name = f"{given} {surname}".strip()

            erefs = person.get("event_ref_list", [])
            events_data = []
            for ref in erefs:
                eh = ref.get("ref", "") if isinstance(ref, dict) else ref
                if not eh:
                    continue
                try:
                    ev = await client.make_api_call(
                        ApiCalls.GET_EVENT, tree_id=tree_id, handle=eh
                    )
                    etype = ev.get("type", "?")
                    date = ev.get("date", {})
                    dateval = tuple(date.get("dateval", [])) if isinstance(date, dict) else ()
                    modifier = date.get("modifier", 0) if isinstance(date, dict) else 0
                    key = (etype, dateval, modifier)
                    events_data.append((key, etype, ev.get("gramps_id", "?"), eh,
                                        format_date(date)))
                except Exception:
                    continue

            groups: dict = {}
            for key, etype, eid, eh, date_str in events_data:
                groups.setdefault(key, []).append((eid, eh, date_str, etype))

            for key, items in groups.items():
                if len(items) > 1:
                    duplicates.append((name, pid, items))

        total = sum(len(v) - 1 for _, _, v in duplicates)
        shown = duplicates[:max_results]
        lines = [
            f"Duplikat-Events: {len(duplicates)} Gruppen, {total} überflüssige Events",
            f"(Zeige {len(shown)} von {len(duplicates)})", "",
        ]
        for name, pid, items in shown:
            etype = items[0][3]
            date_str = items[0][2]
            eids = ", ".join(i[0] for i in items)
            lines.append(f"**{name} ({pid})** — {etype} {date_str}: {eids}")

        return [TextContent(type="text", text="\n".join(lines))]

    except Exception as e:
        return _format_error_response(e, "find duplicate events")


async def get_type_tool(arguments: Dict) -> List[TextContent]:
    """Universal get tool for person and family details."""
    entity_type = arguments.get("type")
    handle = arguments.get("handle")
    gramps_id = arguments.get("gramps_id")

    # If gramps_id provided but no handle, find the handle first
    if gramps_id and not handle:
        from .search_basic import find_type_tool

        search_result = await find_type_tool(
            {"type": entity_type, "gql": f'gramps_id="{gramps_id}"', "max_results": 1}
        )

        # Extract handle from search result
        search_text = search_result[0].text
        import re

        handle_match = re.search(r"\[([^\]]+)\]", search_text)
        if handle_match:
            handle = handle_match.group(1)

    if entity_type == "person" and handle:
        return await get_person_tool({"person_handle": handle})
    elif entity_type == "family" and handle:
        return await get_family_tool({"family_handle": handle})

    return [TextContent(type="text", text="get_type_tool not yet implemented")]
