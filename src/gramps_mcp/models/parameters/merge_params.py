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

"""Pydantic parameter models for duplicate-person merge tools."""

from typing import Optional

from pydantic import BaseModel, Field


class FindDuplicatesParams(BaseModel):
    """Parameters for the find_duplicate_persons tool."""

    limit: Optional[int] = Field(
        50,
        description="Maximum number of candidate pairs to return.",
        ge=1,
        le=500,
    )
    min_score: Optional[float] = Field(
        4.0,
        description="Minimum similarity score (higher = stricter). Suggested: 4 low, 8 high-confidence.",
        ge=1.0,
    )


class MergePersonsParams(BaseModel):
    """Parameters for the merge_persons tool."""

    winner_id: str = Field(
        ...,
        description="Gramps ID of the person to keep (e.g. 'I0001').",
    )
    loser_id: str = Field(
        ...,
        description="Gramps ID of the person to absorb into the winner.",
    )
    dry_run: Optional[bool] = Field(
        True,
        description="If True (default), show planned changes without writing to the database.",
    )


class SplitPersonParams(BaseModel):
    """Parameters for the split_person tool."""

    gramps_id: str = Field(
        ...,
        description="Gramps ID of the merged winner to split (e.g. 'I0001').",
    )
