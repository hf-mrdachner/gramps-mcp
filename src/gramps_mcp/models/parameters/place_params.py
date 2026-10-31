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
Pydantic models for place-related operations.

API calls supported in this category:
- GET_PLACES: Get information about multiple places
- POST_PLACES: Add a new place to the database
- GET_PLACE: Get information about a specific place
- PUT_PLACE: Update the place
- DELETE_PLACE: Delete the place
"""

from typing import List, Optional, Union

from pydantic import BaseModel, Field, field_validator

from .base_params import BaseGetMultipleParams, BaseGetSingleParams


class PlaceSearchParams(BaseGetMultipleParams):
    """Parameters for searching places."""

    pass


class PlaceDetailsParams(BaseGetSingleParams):
    """Parameters for getting specific place details."""

    pass


class PlaceSaveParams(BaseModel):
    """Parameters for creating or updating a place."""

    handle: Optional[str] = Field(
        None, min_length=8, description="Place handle (for updates; omit for new place)"
    )
    gramps_id: Optional[str] = Field(
        None, description="Alternate user managed identifier"
    )
    name: Optional[dict] = Field(
        None, description="Place name object with 'value' field"
    )
    code: Optional[str] = Field(None, description="Place code")
    alt_loc: Optional[List[dict]] = Field(None, description="Alternative locations")
    place_type: str = Field(..., description="Place type")
    placeref_list: Optional[List[dict]] = Field(
        None, description="List of place references"
    )
    alt_names: Optional[List[Union[str, dict]]] = Field(
        None,
        description=(
            "Alternative names; replaces the existing list. Each entry is a plain "
            "string or a place name object with 'value' and optional 'lang' and "
            "'date'. 'date' is an object with dateval array [day, month, year, "
            "False], quality and modifier, as on an event date"
        ),
    )
    lat: Optional[str] = Field(None, description="Latitude coordinate")
    long: Optional[str] = Field(None, description="Longitude coordinate")
    urls: Optional[List[dict]] = Field(None, description="Associated URLs")
    media_list: Optional[List[str]] = Field(None, description="List of media handles")
    citation_list: Optional[List[str]] = Field(
        None, description="List of citation handles"
    )
    note_list: Optional[List[str]] = Field(None, description="List of note handles")
    tag_list: Optional[List[str]] = Field(None, description="List of tag handles")
    private: Optional[bool] = Field(None, description="Mark as private")

    @field_validator("alt_names")
    @classmethod
    def _wrap_alt_names(
        cls, value: Optional[List[Union[str, dict]]]
    ) -> Optional[List[dict]]:
        """
        Convert alternate names to PlaceName objects.

        Args:
            value (Optional[List[Union[str, dict]]]): Names as given by the caller.

        Returns:
            Optional[List[dict]]: One PlaceName dict per name, or None if omitted.

        Raises:
            ValueError: If a name has no non-empty string value, or its date is
                not an object.
        """
        # Reason: the Web backend sends model_dump() unchanged, so this is the only
        # place that normalises for it. The SQLite backend completes date/lang later.
        if value is None:
            return None
        names = []
        for entry in value:
            name = {"value": entry} if isinstance(entry, str) else entry
            text = name.get("value")
            if not isinstance(text, str) or not text.strip():
                raise ValueError(f"needs a non-empty string value: {entry!r}")
            # Reason: _denorm_date turns a non-dict into an empty date, so a string
            # date would be lost while the tool still reported success.
            if "date" in name and not isinstance(name["date"], dict):
                raise ValueError(f"date must be an object: {entry!r}")
            names.append({**name, "_class": "PlaceName"})
        return names
