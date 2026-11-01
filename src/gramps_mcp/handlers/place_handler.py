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
Place data handler for Gramps MCP operations.

Provides clean, direct formatting of place data from handles.
"""

import logging

from ..client import GrampsAPIError
from ..models.api_calls import ApiCalls
from .url_utils import url_description

logger = logging.getLogger(__name__)


async def format_place(client, tree_id: str, handle: str, inline: bool = False) -> str:
    """
    Format place data with full hierarchy and details.

    Args:
        client: Gramps API client instance
        tree_id (str): Family tree identifier
        handle (str): Place handle
        inline (bool, optional): If True, return just the hierarchy for inline use

    Returns:
        str: Formatted place string with hierarchy (and details if not inline)
    """
    if not handle:
        return "" if inline else "• **Place**\n  No handle provided\n\n"

    try:
        place_data = await client.make_api_call(
            api_call=ApiCalls.GET_PLACE, tree_id=tree_id, handle=handle
        )
        if not place_data:
            return "" if inline else f"• **Place {handle}**\n  Place not found\n\n"

        # Build place hierarchy
        place_hierarchy = await _build_place_hierarchy(client, tree_id, place_data)

        # For inline use, just return the hierarchy
        if inline:
            return place_hierarchy if place_hierarchy else ""

        # For search results, return new format
        gramps_id = place_data.get("gramps_id", "")
        place_type = place_data.get("place_type", "")
        urls = place_data.get("urls", [])

        # First line: type: title (full hierarchy) - gramps_id - [handle]
        title = place_hierarchy if place_hierarchy else ""
        first_line = f"{place_type}: {title} - {gramps_id} - [{handle}]"
        result = first_line

        # Second line: url path - url desc (if URLs present)
        for url in urls:
            if isinstance(url, dict):
                url_path = url.get("path", "")
                url_desc = url_description(url)
                if url_path:
                    url_line = url_path
                    if url_desc:
                        url_line += f" - {url_desc}"
                    result += f"\n{url_line}"

        return result + "\n\n"

    except Exception as e:
        logger.debug(f"Failed to format place {handle}: {e}")
        if inline:
            return ""
        else:
            return f"• **Place {handle}**\n  Error formatting place: {str(e)}\n\n"


async def _build_place_hierarchy(client, tree_id: str, place_data: dict) -> str:
    """Build place hierarchy string from place data."""
    # A title containing more than the place's own name is the full hierarchy.
    # Reason: places created through this server get title == name, so such a
    # title says nothing about the parents and they have to be looked up.
    title = place_data.get("title", "")
    name_value = _place_name(place_data)
    if title and title != name_value:
        return title

    # Otherwise build from name and parent places
    place_names = []

    own_name = name_value or title
    if own_name:
        place_names.append(own_name)

    # Follow parent places
    placeref_list = place_data.get("placeref_list", [])
    # Reason: parent links are user data and can form a cycle.
    visited = {place_data.get("handle")}

    while placeref_list:
        parent_handle = placeref_list[0].get("ref")
        if not parent_handle or parent_handle in visited:
            break
        visited.add(parent_handle)

        # Reason: a broken parent link must not hide the names already known,
        # and this walk now runs for every place whose title is just its name.
        try:
            parent_data = await client.make_api_call(
                api_call=ApiCalls.GET_PLACE, tree_id=tree_id, handle=parent_handle
            )
        except GrampsAPIError as e:
            logger.debug(f"Stopped place hierarchy at {parent_handle}: {e}")
            break
        if not parent_data:
            break

        # A parent title beyond its own name already holds the rest of the hierarchy
        parent_title = parent_data.get("title", "")
        parent_name = _place_name(parent_data)
        if parent_title and parent_title != parent_name:
            place_names.append(parent_title)
            break

        # Otherwise use the parent's name
        if parent_name or parent_title:
            place_names.append(parent_name or parent_title)

        # Continue up the hierarchy
        placeref_list = parent_data.get("placeref_list", [])

    return ", ".join(place_names) if place_names else ""


def _place_name(place_data: dict) -> str:
    """
    Return a place's own name from either backend's representation.

    Args:
        place_data (dict): Place object as returned by a backend client.

    Returns:
        str: The place name, or an empty string if there is none.
    """
    name = place_data.get("name")
    if isinstance(name, dict):
        return name.get("value", "") or ""
    return name or ""
