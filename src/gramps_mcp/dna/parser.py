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

"""Parser for DNA match notes stored in Gramps.

Note text format (new, labeled)::

    # Src: AncestryDNA | 45.2 cM | Largest: 32.1 cM | Rel: 3rd Cousin | Side: maternal
    Chromosome\tStart\tEnd\tcM\tSNPs
    1\t1000000\t50000000\t32.1\t8234
    X\t2500000\t8000000\t12.8\t3210

Legacy format (positional, still supported for existing notes)::

    # AncestryDNA | 45.2 cM | Largest: 32.1 cM | 3rd Cousin | Side: maternal

The header line is optional.  Segment data is tab-separated and may appear
without a preceding header line.
"""

from typing import List, Optional

from .models import DnaMatch, DnaSegment


def _parse_header(line: str) -> dict:
    """Parse the pipe-separated header line into a dict of field values.

    Supports both the new labeled format (``Src: …``, ``Rel: …``) and the
    legacy positional format for backwards compatibility with existing notes.

    Args:
        line (str): The header line, starting with '#'.

    Returns:
        dict: Keys from {"source", "shared_cm", "largest_segment",
              "relationship", "side"}, present only when the field was found.
    """
    result: dict = {}
    # Strip leading '#' and surrounding whitespace before splitting.
    raw = line.lstrip("#").strip()
    if not raw:
        return result

    fields = [f.strip() for f in raw.split("|")]

    unmatched = []
    for field in fields:
        if field.startswith("Src: "):
            result["source"] = field[len("Src: "):].strip()
        elif field.startswith("Rel: "):
            result["relationship"] = field[len("Rel: "):].strip()
        elif field.startswith("Largest: "):
            value_str = field[len("Largest: "):].removesuffix(" cM").strip()
            try:
                result["largest_segment"] = float(value_str)
            except ValueError:
                continue
        elif field.startswith("Side: "):
            result["side"] = field[len("Side: "):].strip()
        elif field.endswith(" cM") and "cM" not in field[:-3]:
            # Reason: shared_cm is the only plain "X cM" field without a prefix.
            try:
                result["shared_cm"] = float(field.removesuffix(" cM").strip())
            except ValueError:
                continue
        else:
            unmatched.append(field)

    # Backward-compatible fallback for legacy positional format: if no labeled
    # source/relationship was found, treat the first unmatched non-cM field as
    # source and the next as relationship (old behaviour).
    if "source" not in result or "relationship" not in result:
        for item in unmatched:
            if "source" not in result and "cM" not in item:
                result["source"] = item
            elif "relationship" not in result:
                result["relationship"] = item

    return result


def _parse_segments(lines: List[str]) -> List[DnaSegment]:
    """Parse tab-separated segment lines into DnaSegment objects.

    Args:
        lines (list[str]): Lines that follow the optional column-header row.
            The column-header row (starting with "Chromosome") is skipped
            automatically.

    Returns:
        list[DnaSegment]: Parsed segments; empty list when no valid lines exist.
    """
    segments: List[DnaSegment] = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("Chromosome"):
            continue
        parts = line.split("\t")
        if len(parts) < 4:
            continue
        try:
            chromosome = parts[0]
            start = int(parts[1])
            end = int(parts[2])
            cm = float(parts[3])
            snps: Optional[int] = None
            if len(parts) >= 5 and parts[4].strip():
                snps = int(parts[4].strip())
        except ValueError:
            continue
        segments.append(
            DnaSegment(chromosome=chromosome, start=start, end=end, cm=cm, snps=snps)
        )
    return segments


def parse_dna_note(text: str) -> DnaMatch:
    """Parse a Gramps note text containing DNA match data.

    The caller is responsible for setting match_handle on the returned object
    once the Gramps handle of the matched person is known.

    Args:
        text (str): Raw note text, possibly containing a '#' header line
            followed by tab-separated segment rows.

    Returns:
        DnaMatch: Parsed match object with match_handle set to empty string.
    """
    if not text or not text.strip():
        return DnaMatch(match_handle="", shared_cm=0.0)

    lines = text.splitlines()
    header_fields: dict = {}
    segment_lines: List[str] = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            header_fields = _parse_header(stripped)
        else:
            segment_lines.append(line)

    shared_cm = header_fields.get("shared_cm", 0.0)
    segments = _parse_segments(segment_lines)

    return DnaMatch(
        match_handle="",
        shared_cm=shared_cm,
        largest_segment=header_fields.get("largest_segment"),
        relationship=header_fields.get("relationship"),
        side=header_fields.get("side"),
        source=header_fields.get("source"),
        segments=segments,
    )
