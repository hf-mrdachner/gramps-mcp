# Implementation Plan: DNA Match Tools

Tracking issue: https://github.com/hf-mrdachner/gramps-mcp/issues/1

## Goal

Three MCP tools for recording and querying DNA match data in a Gramps SQLite database.
Storage layer only — data entry is manual or driven by a future AncestryMCP project.

## Data model (Gramps native format)

DNA matches are stored exactly as the Gramps DNA addons (DNA Segment Map, DNAMatches)
expect them — no custom schema, full compatibility:

```
Person.person_ref_list
  └─ PersonRef {
       "ref": <match_person_handle>,
       "rel": "DNA",
       "note_list": [<note_handle>],
       "citation_list": [],
       "private": false,
       "_class": "PersonRef"
     }

Note.text (plain text, tab-separated):
  # AncestryDNA | 45.2 cM | Largest: 32.1 cM | 3rd Cousin | Side: maternal
  Chromosome\tStart\tEnd\tcM\tSNPs
  1\t1000000\t50000000\t32.1\t8234
  X\t2500000\t8000000\t12.8\t3210
```

Header line format (all fields optional except shared_cm):
  `# {source} | {shared_cm} cM | Largest: {largest_segment} cM | {relationship} | Side: {side}`

Segment lines: tab-separated, header row present only when segments exist.

## Tools

### `add_dna_match`

Creates PersonRef + Note on the test-taker person.
If a DNA association between the two persons already exists, raise an error
(use `update_dna_match` instead).

Parameters (Pydantic model `AddDnaMatchParams`):
- `person_id: str` — Gramps ID of test-taker (e.g. "I0001")
- `match_person_id: str` — Gramps ID of DNA match (must already exist)
- `shared_cm: float` — total shared centiMorgans
- `largest_segment: Optional[float]` — largest segment in cM
- `relationship: Optional[str]` — e.g. "3rd Cousin"
- `side: Optional[Literal["maternal", "paternal", "unknown"]]`
- `source: Optional[str]` — "AncestryDNA" | "GEDmatch" | "23andMe" | "FTDNA"
- `segments: Optional[List[DnaSegment]]` — chromosome-level data

`DnaSegment` model:
- `chromosome: str` — "1".."22" or "X"
- `start: int` — base-pair start position
- `end: int` — base-pair end position
- `cm: float` — centiMorgans
- `snps: Optional[int]`

### `get_dna_matches`

Returns all DNA associations for a person with parsed summary + segments.

Parameters (`GetDnaMatchesParams`):
- `person_id: str` — Gramps ID

Returns list of matches, each with: match_person_id, match_name, shared_cm,
largest_segment, relationship, side, source, segments[].

### `update_dna_match`

Updates an existing DNA association (adds/replaces segments or summary fields).
Finds the association by (person_id, match_person_id) pair.

Parameters (`UpdateDnaMatchParams`):
- `person_id: str`
- `match_person_id: str`
- any subset of `add_dna_match` fields (all optional)

## File structure

```
src/gramps_mcp/
  dna/
    __init__.py          — exports DnaSegment, DnaMatch, add_dna_match_to_db, get_dna_matches_from_db
    models.py            — DnaSegment dataclass, DnaMatch dataclass
    parser.py            — parse_dna_note(text) -> DnaMatch
    writer.py            — write_dna_match(conn, person_handle, match_handle, match) -> note_handle
  models/parameters/
    dna_params.py        — AddDnaMatchParams, GetDnaMatchesParams, UpdateDnaMatchParams, DnaSegmentParam
  tools/
    dna.py               — add_dna_match_tool, get_dna_matches_tool, update_dna_match_tool
tests/
  test_dna_tools.py      — integration tests against in-memory SQLite
```

## Key implementation notes

### SQLite access pattern
Same as `merge_persons.py` — SQLite-only, extract conn via:
```python
from ..sqlite_client import GrampsSqliteClient
if not isinstance(client, GrampsSqliteClient):
    raise GrampsAPIError("DNA tools require the SQLite backend.")
conn = client._db._conn
```
Use `get_client()` (sync, not `await get_client()`).

### PersonRef JSON structure
```python
{
    "_class": "PersonRef",
    "ref": match_handle,
    "rel": "DNA",
    "note_list": [note_handle],
    "citation_list": [],
    "attribute_list": [],
    "private": False,
}
```
Append to `person["person_ref_list"]`, then UPDATE person row + INSERT note row.

### Note JSON structure (Gramps format)
```python
{
    "_class": "Note",
    "handle": new_handle,          # uuid-based, e.g. str(uuid.uuid4())[:20]
    "gramps_id": next_gramps_id,   # N0001, N0002 etc — query MAX(gramps_id) from note
    "format": 0,
    "text": {"_class": "StyledText", "string": note_text, "tags": []},
    "type": {"_class": "NoteType", "value": 1, "string": "General"},
    "tag_list": [],
    "change": int(time.time()),
    "private": False,
}
```

### Note text format
```python
def _build_note_text(match: DnaMatch) -> str:
    parts = []
    if match.source:       parts.append(match.source)
    if match.shared_cm:    parts.append(f"{match.shared_cm} cM")
    if match.largest_segment: parts.append(f"Largest: {match.largest_segment} cM")
    if match.relationship: parts.append(match.relationship)
    if match.side:         parts.append(f"Side: {match.side}")
    header = "# " + " | ".join(parts) if parts else ""

    if not match.segments:
        return header

    lines = [header, "Chromosome\tStart\tEnd\tcM\tSNPs"]
    for s in match.segments:
        lines.append(f"{s.chromosome}\t{s.start}\t{s.end}\t{s.cm}\t{s.snps or ''}")
    return "\n".join(lines)
```

### Transaction pattern
Use `with conn:` (not explicit BEGIN/COMMIT) — matches operations.py pattern.

### Handle generation
```python
import uuid, time
new_handle = str(uuid.uuid4()).replace("-", "")[:20]
```

### gramps_id generation for notes
```python
row = conn.execute("SELECT MAX(CAST(SUBSTR(gramps_id,2) AS INTEGER)) FROM note").fetchone()
next_num = (row[0] or 0) + 1
gramps_id = f"N{next_num:04d}"
```

### reference table
After inserting note, add reference row:
```python
conn.execute(
    "INSERT INTO reference (obj_handle, obj_class, ref_handle, ref_class) VALUES (?,?,?,?)",
    (person_handle, "Person", note_handle, "Note"),
)
```

## Test cases (tests/test_dna_tools.py)

Use the same in-memory SQLite schema as `tests/test_merge_tools.py` —
reuse `_SCHEMA`, `_insert_person`, `_insert_event` helpers.

Key tests:
- `test_add_dna_match_creates_person_ref` — PersonRef with rel="DNA" in person JSON
- `test_add_dna_match_creates_note` — Note row exists with correct text
- `test_add_dna_match_with_segments` — segment lines in note text
- `test_get_dna_matches_returns_parsed_data` — shared_cm, relationship etc. parsed correctly
- `test_get_dna_matches_with_segments` — segment list populated
- `test_add_duplicate_raises_error` — adding same pair twice raises GrampsAPIError
- `test_update_adds_segments_to_existing` — update_dna_match adds segments to summary-only match
- `test_dna_requires_sqlite_backend` — GrampsAPIError when client is not GrampsSqliteClient

## server.py registration

Add to TOOL_REGISTRY (import from `.tools.dna`):
```python
"add_dna_match": {"schema": AddDnaMatchParams, "handler": add_dna_match_tool, ...},
"get_dna_matches": {"schema": GetDnaMatchesParams, "handler": get_dna_matches_tool, ...},
"update_dna_match": {"schema": UpdateDnaMatchParams, "handler": update_dna_match_tool, ...},
```

Add param classes to server.py imports from `models.parameters.dna_params`.
