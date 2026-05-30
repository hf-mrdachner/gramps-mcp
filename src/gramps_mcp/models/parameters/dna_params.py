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

"""Pydantic parameter models for DNA match tools."""

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class DnaSegmentParam(BaseModel):
    """DNA segment data for chromosome-level matching."""

    chromosome: str = Field(
        ...,
        description="Chromosome identifier: '1'-'22' or 'X'",
    )
    start: int = Field(
        ...,
        description="Base-pair start position",
        ge=1,
    )
    end: int = Field(
        ...,
        description="Base-pair end position",
        ge=1,
    )
    cm: float = Field(
        ...,
        description="Segment size in centiMorgans",
        gt=0,
    )
    snps: Optional[int] = Field(
        None,
        description="Number of SNPs in segment",
        ge=1,
    )


class AddDnaMatchParams(BaseModel):
    """Parameters for adding a DNA match to a test-taker."""

    person_id: str = Field(
        ...,
        description="Gramps ID of the test-taker (e.g. 'I0001')",
    )
    match_person_id: str = Field(
        ...,
        description="Gramps ID of the DNA match person (must already exist)",
    )
    shared_cm: float = Field(
        ...,
        description="Total shared centiMorgans",
        gt=0,
    )
    largest_segment: Optional[float] = Field(
        None,
        description="Largest segment in cM",
        gt=0,
    )
    relationship: Optional[str] = Field(
        None,
        description="Predicted relationship, e.g. '3rd Cousin'",
    )
    side: Optional[Literal["maternal", "paternal", "unknown"]] = Field(
        None,
        description="Which side of family",
    )
    source: Optional[str] = Field(
        None,
        description="Testing company: 'AncestryDNA', 'GEDmatch', '23andMe', 'FTDNA'",
    )
    segments: Optional[List[DnaSegmentParam]] = Field(
        None,
        description="Chromosome-level segment data",
    )


class GetDnaMatchesParams(BaseModel):
    """Parameters for querying DNA matches for a person."""

    person_id: str = Field(
        ...,
        description="Gramps ID of the person to query (e.g. 'I0001')",
    )


class UpdateDnaMatchParams(BaseModel):
    """Parameters for updating an existing DNA match."""

    person_id: str = Field(
        ...,
        description="Gramps ID of the test-taker",
    )
    match_person_id: str = Field(
        ...,
        description="Gramps ID of the DNA match to update",
    )
    shared_cm: Optional[float] = Field(
        None,
        description="Updated total shared centiMorgans",
        gt=0,
    )
    largest_segment: Optional[float] = Field(
        None,
        description="Updated largest segment in cM",
        gt=0,
    )
    relationship: Optional[str] = Field(
        None,
        description="Updated predicted relationship",
    )
    side: Optional[Literal["maternal", "paternal", "unknown"]] = Field(
        None,
        description="Updated family side",
    )
    source: Optional[str] = Field(
        None,
        description="Updated testing company",
    )
    segments: Optional[List[DnaSegmentParam]] = Field(
        None,
        description="Chromosome-level segment data (replaces existing)",
    )
