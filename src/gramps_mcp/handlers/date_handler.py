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
Date data handler for Gramps MCP operations.

Provides clean, consistent date formatting from Gramps date objects.
"""

import logging
from datetime import datetime

logger = logging.getLogger(__name__)


def format_date(date_obj: dict) -> str:
    """
    Format Gramps date object into human-readable string with fallback.

    Args:
        date_obj (dict): Gramps date object with dateval array

    Returns:
        str: Formatted date string or "date unknown" if invalid
    """
    if not date_obj:
        return "date unknown"

    # Try formatted string first
    formatted_date = date_obj.get("string", "")
    if formatted_date:
        return formatted_date

    # Try to extract from dateval
    dateval = date_obj.get("dateval")
    if not dateval or len(dateval) < 3:
        return "date unknown"

    # dateval format is [day, month, year, False]
    day, month, year = dateval[0], dateval[1], dateval[2]
    if year <= 0:
        return "date unknown"

    # Get quality and modifier
    quality = date_obj.get("quality", 0)
    modifier = date_obj.get("modifier", 0)

    base_date = _format_single_date(day, month, year)

    # Add modifier prefix
    modifier_prefixes = {
        0: "",  # regular
        1: "before ",
        2: "after ",
        3: "about ",
        4: "between ",  # range
        5: "from ",  # span
        6: "",  # textonly
        7: "from ",
        8: "to ",
    }

    # Add quality suffix
    quality_suffixes = {
        0: "",  # regular
        1: " (estimated)",
        2: " (calculated)",
    }

    prefix = modifier_prefixes.get(modifier, "")
    suffix = quality_suffixes.get(quality, "")

    # Range (4, "between X and Y") and span (5, "from X to Y") carry a second
    # date in dateval[4:7]; without it the display silently dropped the end date.
    if modifier in (4, 5) and len(dateval) >= 7:
        day2, month2, year2 = dateval[4], dateval[5], dateval[6]
        if year2 > 0:
            second_date = _format_single_date(day2, month2, year2)
            joiner = "and " if modifier == 4 else "to "
            return f"{prefix}{base_date} {joiner}{second_date}{suffix}"

    return f"{prefix}{base_date}{suffix}"


def _format_single_date(day: int, month: int, year: int) -> str:
    """
    Format a single day/month/year triple into a human-readable string.

    Args:
        day (int): Day of month, or 0 if unknown.
        month (int): Month, or 0 if unknown.
        year (int): Year.

    Returns:
        str: Formatted date string, falling back to just the year on error.
    """
    try:
        if day > 0 and month > 0:
            return datetime(year, month, day).strftime("%d %B %Y")
        elif month > 0:
            return datetime(year, month, 1).strftime("%B %Y")
        return str(year)
    except (ValueError, TypeError):
        return str(year) if year > 0 else "date unknown"
