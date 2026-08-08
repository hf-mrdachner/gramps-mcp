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
Citation data handler for Gramps MCP operations.

Provides clean, direct formatting of citation data including source details,
URLs, and repository information.
"""

import logging

from ..models.api_calls import ApiCalls
from .date_handler import format_date

logger = logging.getLogger(__name__)

CONFIDENCE_LABELS = {0: "Very Low", 1: "Low", 2: "Normal", 3: "High", 4: "Very High"}


async def format_citation(client, tree_id: str, handle: str) -> str:
    """
    Format citation data with source details, URLs, and repository info.

    Args:
        client: Gramps API client instance
        tree_id: Family tree identifier
        handle: Citation handle

    Returns:
        Formatted citation string with source and URL details
    """
    if not handle:
        return "• **Unknown Citation**\n  No handle provided\n\n"

    try:
        citation_data = await client.make_api_call(
            api_call=ApiCalls.GET_CITATION,
            tree_id=tree_id,
            handle=handle,
            params={"extend": "backlinks", "backlinks": True},
        )
        if not citation_data:
            return f"• **Citation {handle}**\n  Citation not found\n\n"

        gramps_id = citation_data.get("gramps_id", "")
        page = citation_data.get("page", "").strip()
        confidence = citation_data.get("confidence", 2)
        source_handle = citation_data.get("source_handle", "")
        date = citation_data.get("date", {})
        media_list = citation_data.get("media_list", [])
        note_list = citation_data.get("note_list", [])
        citation_data.get("backlinks", {})

        # Get source title
        source_title = ""
        if source_handle:
            try:
                source_data = await client.make_api_call(
                    api_call=ApiCalls.GET_SOURCE, tree_id=tree_id, handle=source_handle
                )
                if source_data:
                    source_title = source_data.get("title", "").strip()
            except Exception:
                pass

        # First line: source title - gramps_id - [handle]
        if source_title:
            first_line = f"{source_title} - {gramps_id} - [{handle}]"
        else:
            first_line = f" - {gramps_id} - [{handle}]"
        result = first_line

        # Page/Date/Confidence: always shown as explicit labeled lines so a
        # silently-dropped write (e.g. issue #12) is visible in the response
        # instead of looking identical to a successful one.
        result += f"\nPage: {page if page else '(none)'}"
        result += f"\nDate: {format_date(date)}"
        confidence_label = CONFIDENCE_LABELS.get(confidence, f"Unknown({confidence})")
        result += f"\nConfidence: {confidence_label}"

        # Attached media: gramps_id(s)
        if media_list:
            media_ids = []
            for media_ref in media_list:
                if isinstance(media_ref, dict):
                    media_handle = media_ref.get("ref", "")
                    if media_handle:
                        try:
                            media_data = await client.make_api_call(
                                api_call=ApiCalls.GET_MEDIA_ITEM,
                                tree_id=tree_id,
                                handle=media_handle,
                            )
                            if media_data:
                                media_gramps_id = media_data.get("gramps_id", "")
                                if media_gramps_id:
                                    media_ids.append(media_gramps_id)
                        except Exception:
                            continue

            if media_ids:
                result += f"\nAttached media: {', '.join(media_ids)}"

        # Attached notes: gramps_id(s)
        if note_list:
            note_ids = []
            for note_handle in note_list:
                try:
                    note_data = await client.make_api_call(
                        api_call=ApiCalls.GET_NOTE, tree_id=tree_id, handle=note_handle
                    )
                    if note_data:
                        note_gramps_id = note_data.get("gramps_id", "")
                        if note_gramps_id:
                            note_ids.append(note_gramps_id)
                except Exception:
                    continue

            if note_ids:
                result += f"\nAttached notes: {', '.join(note_ids)}"

        # Attached to: gramps ids of backlinks (using extended data)
        extended = citation_data.get("extended", {})
        extended_backlinks = extended.get("backlinks", {})

        if isinstance(extended_backlinks, dict) and extended_backlinks:
            backlink_ids = []
            # Filter for entity types that reference citations (person, family, event)
            relevant_types = ["person", "family", "event"]

            for entity_type, entities in extended_backlinks.items():
                if entity_type in relevant_types and isinstance(entities, list):
                    for entity in entities:
                        if isinstance(entity, dict):
                            entity_gramps_id = entity.get("gramps_id", "")
                            if entity_gramps_id:
                                backlink_ids.append(entity_gramps_id)

            if backlink_ids:
                result += f"\nAttached to: {', '.join(backlink_ids)}"

        return result + "\n\n"

    except Exception as e:
        logger.debug(f"Failed to format citation {handle}: {e}")
        return f"• **Citation {handle}**\n  Error formatting citation: {str(e)}\n\n"
