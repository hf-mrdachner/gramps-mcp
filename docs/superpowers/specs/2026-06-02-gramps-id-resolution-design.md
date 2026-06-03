# Gramps-ID Resolution — Design Spec

**Date:** 2026-06-02
**Status:** Approved

## Problem

All MCP tools that operate on a specific object require an internal `handle` (an opaque
20-char string). Tool responses show human-readable Gramps IDs (e.g. `I0001`, `S0042`).
An AI agent cannot reference an object it just read without a second lookup step to obtain
the handle. Several tools already implement their own inline gramps_id→handle resolution,
but inconsistently and without sharing code.

## Goal

Every tool that accepts a `handle` parameter also accepts the equivalent `gramps_id`
parameter. Resolution is transparent: the tool resolves the gramps_id to a handle
internally before processing. No new MCP tools are added.

## Out of Scope

- Search/filter tools (`find_person`, `find_anything`, etc.) — they already work by name/text
- `list_databases`, `open_database`, `close_database` — no object handles
- `find_duplicate_*` tools — no handle input

---

## Architecture

### New module: `src/gramps_mcp/gramps_id.py`

**`resolve_handle(gramps_id, obj_type, client) -> str`**

Resolves a single Gramps ID to a handle. Raises `GrampsAPIError` if not found.

Backend routing:

| Backend | Resolution method |
|---|---|
| `GrampsSqliteClient` | `client._db.get_by_id(obj_type, gramps_id)["handle"]` |
| `GrampsDirectClient` | Same — `GrampsXmlDB.get_by_id()` exists with identical signature |
| `GrampsWebAPIClient` | `make_api_call(OBJ_TYPE_TO_API[obj_type], params={"gramps_id": gramps_id, "pagesize": 1})` → first result's handle |

Object type → web endpoint mapping:

```python
OBJ_TYPE_TO_API = {
    "person":     ApiCalls.GET_PEOPLE,
    "family":     ApiCalls.GET_FAMILIES,
    "event":      ApiCalls.GET_EVENTS,
    "place":      ApiCalls.GET_PLACES,
    "source":     ApiCalls.GET_SOURCES,
    "citation":   ApiCalls.GET_CITATIONS,
    "note":       ApiCalls.GET_NOTES,
    "media":      ApiCalls.GET_MEDIA,
    "repository": ApiCalls.GET_REPOSITORIES,
}
```

**`resolve_handles(arguments, handle_map, client) -> Dict`**

Preprocesses an arguments dict, resolving any gramps_id values to handles before
the tool logic runs.

`handle_map` maps each handle field name to its object type:
```python
{"handle": "person", "father_handle": "person", "source_handle": "source"}
```

For each entry `(handle_field, obj_type)`:
1. Derive the corresponding gramps_id field name by convention (see below)
2. If `gramps_id_field` is present in `arguments` and `handle_field` is absent/None:
   - Call `resolve_handle(gramps_id, obj_type, client)`
   - Set `arguments[handle_field]` to the result
3. Return the updated arguments dict (new dict, original unchanged)

Lists (e.g. `child_handles` / `child_gramps_ids`) are resolved element-wise.

### Naming convention

| Handle field | gramps_id field |
|---|---|
| `handle` | `gramps_id` |
| `handle1` | `gramps_id1` |
| `handle2` | `gramps_id2` |
| `{prefix}_handle` | `{prefix}_gramps_id` |
| `{prefix}_handles` | `{prefix}_gramps_ids` |

---

## Parameter model changes

Models gain optional gramps_id fields (no validators — resolution is async, happens in tools).

### Already present (no change needed)

- `BaseDataModel.gramps_id` — already exists; description updated to clarify it can be used
  for update lookups
- `SimpleGetParams.gramps_id` — already exists

### New fields to add

| Model | New fields |
|---|---|
| `DeleteObjectParams` | `gramps_id: Optional[str]` |
| `LivingParams` | `gramps_id: Optional[str]` |
| `FamilySaveParams` | `father_gramps_id`, `mother_gramps_id`, `child_gramps_ids` |
| `FamilyTimelineParams` | `gramps_id: Optional[str]` |
| `EventMergeParams` (event_params) | `gramps_id1`, `gramps_id2` |
| `RelationParams` | `gramps_id1`, `gramps_id2` |
| `AddEventToPersonParams` | `person_gramps_id`, `event_gramps_id` |
| `RemoveChildFromFamilyParams` | `family_gramps_id`, `child_gramps_id` |
| `MoveAttachmentParams` | `from_gramps_id`, `to_gramps_id` |
| `MergePersonsParams` | `gramps_id1`, `gramps_id2` (replace existing raw SQL approach) |
| `SplitPersonParams` | already has `gramps_id` — no change |

---

## Tools to update

Each tool calls `resolve_handles(arguments, handle_map, client)` as its first step.
Tools that already have inline gramps_id logic replace it with `resolve_handles`.

| Tool | handle_map |
|---|---|
| `get_person_tool` | `{"handle": "person"}` — replace inline |
| `get_family_tool` | `{"handle": "family"}` |
| `get_event_tool` | `{"handle": "event"}` — replace inline |
| `get_place_tool` | `{"handle": "place"}` — replace inline |
| `delete_object_tool` | `{"handle": arguments["obj_type"]}` — dynamic type |
| `merge_persons_tool` | `{"handle1": "person", "handle2": "person"}` — replace inline SQL |
| `split_person_tool` | `{"handle": "person"}` — replace inline |
| `merge_events_tool` | `{"handle1": "event", "handle2": "event"}` |
| `merge_places_tool` | `{"handle1": "place", "handle2": "place"}` |
| `merge_citations_tool` | `{"handle1": "citation", "handle2": "citation"}` |
| `merge_families_tool` | `{"handle1": "family", "handle2": "family"}` |
| `create_family_tool` | `{"handle": "family", "father_handle": "person", "mother_handle": "person", "child_handles": "person"}` |
| `create_citation_tool` | `{"source_handle": "source"}` — replace existing fix |
| `add_dna_match_tool` | `{"handle": "person"}` |
| `get_dna_matches_tool` | `{"handle": "person"}` |
| `update_dna_match_tool` | `{"handle": "person"}` |
| `add_event_to_person_tool` | `{"person_handle": "person", "event_handle": "event"}` |
| `remove_child_from_family_tool` | `{"family_handle": "family", "child_handle": "person"}` |
| `move_attachment_tool` | `{"handle": arguments["obj_type"], "from_handle": arguments["from_type"], "to_handle": arguments["to_type"]}` — types come from existing `from_type`/`to_type` fields |
| `get_ancestors_tool` | `{"handle": "person"}` |
| `get_descendants_tool` | `{"handle": "person"}` |
| `prepare_biography_tool` | `{"handle": "person"}` |

---

## Error handling

- gramps_id not found → `GrampsAPIError`: `"<obj_type> '<gramps_id>' not found"`
- Neither handle nor gramps_id provided → existing validation catches (required field missing)
- Both provided → handle takes precedence, gramps_id ignored

---

## Testing

File: `tests/test_gramps_id.py`

All tests use the SQLite in-memory fixture.

| Test | Scenario |
|---|---|
| `test_resolve_handle_by_gramps_id` | Jane (`I0002`) resolves to `h_pe_jane` |
| `test_resolve_handle_passthrough` | `h_pe_jane` (already a handle) — returns as-is |
| `test_resolve_handle_not_found` | `I9999` → raises `GrampsAPIError` |
| `test_resolve_handles_single` | `{"gramps_id": "I0002"}` → `{"handle": "h_pe_jane", "gramps_id": "I0002"}` |
| `test_resolve_handles_multi` | father + mother gramps_ids both resolved |
| `test_resolve_handles_list` | `child_gramps_ids: ["I0003"]` → `child_handles: ["h_pe_child"]` |
| `test_handle_takes_precedence` | both handle + gramps_id provided → handle used unchanged |
| Integration tests for key tools | `get_person`, `merge_persons`, `delete_object` each called with gramps_id |

---

## Migration note

`create_citation_tool` already has a partial `source_gramps_id` fix (in the `feat: allow
source_gramps_id...` commit). That fix is replaced by `resolve_handles` for consistency.
The `MergePersonsParams` raw SQL gramps_id lookup is replaced by `resolve_handles`.
