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

"""Data models for DNA match information stored in Gramps notes."""

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class DnaSegment:
    """A single shared DNA segment on one chromosome.

    Attributes:
        chromosome (str): Chromosome identifier, "1".."22" or "X".
        start (int): Start position in base pairs.
        end (int): End position in base pairs.
        cm (float): Length of the segment in centiMorgans.
        snps (Optional[int]): Number of SNPs covering the segment, if known.
    """

    chromosome: str
    start: int
    end: int
    cm: float
    snps: Optional[int] = None


@dataclass
class DnaMatch:
    """Summary of a DNA match between two individuals.

    Attributes:
        match_handle (str): Gramps internal handle of the matched person.
            Set by the caller after parsing; the parser always returns "".
        shared_cm (float): Total shared centimorgans.
        largest_segment (Optional[float]): Largest single shared segment in cM.
        relationship (Optional[str]): Predicted relationship label, e.g. "3rd Cousin".
        side (Optional[str]): Which side of the family: "maternal", "paternal",
            or "unknown".
        source (Optional[str]): Testing company, e.g. "AncestryDNA", "GEDmatch",
            "23andMe", "FTDNA".
        segments (List[DnaSegment]): Individual chromosome segment data.
        note_handle (Optional[str]): Gramps handle of the associated note, once
            persisted.
    """

    match_handle: str
    shared_cm: float
    largest_segment: Optional[float] = None
    relationship: Optional[str] = None
    side: Optional[str] = None
    source: Optional[str] = None
    segments: List[DnaSegment] = field(default_factory=list)
    note_handle: Optional[str] = None
