# Gramps-ID Resolution — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every MCP tool that accepts a `handle` also accept the equivalent `gramps_id` parameter, transparently resolved to a handle before tool logic runs.

**Architecture:** A new `gramps_id.py` module provides `resolve_handle(gramps_id, obj_type, client)` and `resolve_handles(arguments, handle_map, client)`. Tools call `resolve_handles` at their entry point; the function routes to `db.get_by_id()` for SQLite/Direct backends or a list-endpoint API call for the web backend. Parameter models gain optional `gramps_id` fields so the AI schema exposes them.

**Tech Stack:** Python, Pydantic, pytest (asyncio_mode=auto), existing SQLite in-memory fixture. Working dir: `C:\Users\dachner\Documents\Privat\dev\gramps-mcp`.

---

## File Map

| Action | Path | Purpose |
|---|---|---|
| Create | `src/gramps_mcp/gramps_id.py` | Core resolution utility |
| Create | `tests/test_gramps_id.py` | Unit + integration tests |
| Modify | `src/gramps_mcp/models/parameters/delete_params.py` | Add `gramps_id` field |
| Modify | `src/gramps_mcp/models/parameters/family_params.py` | Add `father_gramps_id`, `mother_gramps_id`, `child_gramps_ids`, `gramps_id` on `FamilyTimelineParams` |
| Modify | `src/gramps_mcp/models/parameters/link_edit_params.py` | Add gramps_id fields for all handle params |
| Modify | `src/gramps_mcp/tools/search_details.py` | Hook get_person, get_family, get_event, get_place |
| Modify | `src/gramps_mcp/tools/delete.py` | Hook delete_object_tool |
| Modify | `src/gramps_mcp/tools/data_management.py` | Hook create_family, create_citation (replace existing fix) |
| Modify | `src/gramps_mcp/server.py` | Replace link_edit lambdas with async handlers that call resolve_handles |

**Not changed** (already gramps_id-based): merge tools, split_person, get_ancestors, get_descendants, prepare_biography, DNA tools.

---

## Task 1: Create gramps_id.py and tests

**Files:**
- Create: `src/gramps_mcp/gramps_id.py`
- Create: `tests/test_gramps_id.py`

- [ ] **Step 1: Write failing tests**

  Create `tests/test_gramps_id.py`:

  ```python
  """Tests for gramps_id resolution utilities. Uses SQLite in-memory fixture."""
  import pytest

  from gramps_mcp.client import GrampsAPIError
  from gramps_mcp.gramps_id import _gramps_id_field, resolve_handle, resolve_handles


  class TestGramsIdFieldNaming:
      def test_bare_handle(self):
          assert _gramps_id_field("handle") == "gramps_id"

      def test_prefixed_handle(self):
          assert _gramps_id_field("father_handle") == "father_gramps_id"

      def test_plural_handles(self):
          assert _gramps_id_field("child_handles") == "child_gramps_ids"

      def test_numbered_handle1(self):
          assert _gramps_id_field("handle1") == "gramps_id1"

      def test_numbered_handle2(self):
          assert _gramps_id_field("handle2") == "gramps_id2"

      def test_source_handle(self):
          assert _gramps_id_field("source_handle") == "source_gramps_id"

      def test_from_handle(self):
          assert _gramps_id_field("from_handle") == "from_gramps_id"


  class TestResolveHandle:
      async def test_resolves_person_gramps_id(self, sqlite_client):
          handle = await resolve_handle("I0002", "person", sqlite_client)
          assert handle == "h_pe_jane"

      async def test_resolves_family_gramps_id(self, sqlite_client):
          handle = await resolve_handle("F0001", "family", sqlite_client)
          assert handle == "h_fa_smith"

      async def test_resolves_event_gramps_id(self, sqlite_client):
          handle = await resolve_handle("E0001", "event", sqlite_client)
          assert handle == "h_ev_birth_john"

      async def test_raises_if_not_found(self, sqlite_client):
          with pytest.raises(GrampsAPIError, match="not found"):
              await resolve_handle("I9999", "person", sqlite_client)


  class TestResolveHandles:
      async def test_single_gramps_id_resolved(self, sqlite_client):
          args = {"gramps_id": "I0002"}
          result = await resolve_handles(args, {"handle": "person"}, sqlite_client)
          assert result["handle"] == "h_pe_jane"

      async def test_handle_takes_precedence_over_gramps_id(self, sqlite_client):
          args = {"handle": "h_pe_john", "gramps_id": "I0002"}
          result = await resolve_handles(args, {"handle": "person"}, sqlite_client)
          assert result["handle"] == "h_pe_john"

      async def test_no_gramps_id_no_change(self, sqlite_client):
          args = {"handle": "h_pe_john"}
          result = await resolve_handles(args, {"handle": "person"}, sqlite_client)
          assert result["handle"] == "h_pe_john"

      async def test_list_resolved_element_wise(self, sqlite_client):
          args = {"child_gramps_ids": ["I0003"]}
          result = await resolve_handles(
              args, {"child_handles": "person"}, sqlite_client
          )
          assert result["child_handles"] == ["h_pe_child"]

      async def test_multi_field_map(self, sqlite_client):
          args = {"father_gramps_id": "I0001", "mother_gramps_id": "I0002"}
          result = await resolve_handles(
              args,
              {"father_handle": "person", "mother_handle": "person"},
              sqlite_client,
          )
          assert result["father_handle"] == "h_pe_john"
          assert result["mother_handle"] == "h_pe_jane"

      async def test_returns_new_dict(self, sqlite_client):
          args = {"gramps_id": "I0002"}
          result = await resolve_handles(args, {"handle": "person"}, sqlite_client)
          assert result is not args
  ```

- [ ] **Step 2: Run to verify they fail**

  ```
  uv run pytest tests/test_gramps_id.py -v
  ```
  Expected: all fail with `ModuleNotFoundError: No module named 'gramps_mcp.gramps_id'`

- [ ] **Step 3: Create src/gramps_mcp/gramps_id.py**

  ```python
  """
  Gramps-ID to handle resolution utilities.

  All tools that accept a handle parameter can also accept the equivalent
  gramps_id parameter. This module resolves gramps_ids to internal handles
  for all three backends (SQLite, DirectClient, WebAPI).
  """
  import logging
  from typing import Dict, List, Optional

  from .client import GrampsAPIError
  from .config import get_settings
  from .models.api_calls import ApiCalls

  logger = logging.getLogger(__name__)

  _OBJ_TYPE_TO_API: Dict[str, ApiCalls] = {
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


  def _gramps_id_field(handle_field: str) -> str:
      """
      Derive the gramps_id field name for a handle field by naming convention.

      Examples:
          handle       -> gramps_id
          father_handle -> father_gramps_id
          child_handles -> child_gramps_ids
          handle1      -> gramps_id1

      Args:
          handle_field (str): Name of the handle field.

      Returns:
          str: Name of the corresponding gramps_id field.
      """
      if handle_field == "handle":
          return "gramps_id"
      if handle_field.endswith("_handles"):
          return handle_field[: -len("_handles")] + "_gramps_ids"
      if handle_field.endswith("_handle"):
          return handle_field[: -len("_handle")] + "_gramps_id"
      suffix = handle_field[len("handle"):]
      if handle_field.startswith("handle") and suffix.isdigit():
          return "gramps_id" + suffix
      return handle_field + "_gramps_id"


  async def resolve_handle(gramps_id: str, obj_type: str, client) -> str:
      """
      Resolve a Gramps ID (e.g. 'I0001') to an internal handle.

      Args:
          gramps_id (str): Gramps ID to look up.
          obj_type (str): Object type: 'person', 'family', 'event', 'place',
              'source', 'citation', 'note', 'media', or 'repository'.
          client: Any Gramps backend client instance.

      Returns:
          str: Internal handle for the object.

      Raises:
          GrampsAPIError: If the gramps_id is not found.
      """
      from .sqlite_client import GrampsSqliteClient
      from .direct_client import GrampsDirectClient

      if isinstance(client, (GrampsSqliteClient, GrampsDirectClient)):
          # Reason: db.get_by_id is the correct abstraction — no MCP API for gramps_id lookup
          obj = client._db.get_by_id(obj_type, gramps_id)
          if not obj:
              raise GrampsAPIError(f"{obj_type} '{gramps_id}' not found")
          return obj["handle"]

      api_call = _OBJ_TYPE_TO_API.get(obj_type)
      if not api_call:
          raise GrampsAPIError(f"Unknown obj_type '{obj_type}'")
      tree_id = get_settings().gramps_tree_id
      results = await client.make_api_call(
          api_call, tree_id=tree_id,
          params={"gramps_id": gramps_id, "pagesize": 1},
      )
      if not results:
          raise GrampsAPIError(f"{obj_type} '{gramps_id}' not found")
      return results[0]["handle"]


  async def resolve_handles(
      arguments: Dict, handle_map: Dict[str, str], client
  ) -> Dict:
      """
      Resolve all gramps_id values in an arguments dict to handles.

      For each (handle_field, obj_type) in handle_map, checks the
      corresponding gramps_id field (derived by naming convention). If the
      gramps_id field is present and the handle field is absent or None,
      resolves and fills in the handle. Lists are resolved element-wise.

      Args:
          arguments (Dict): Tool arguments dict (not mutated).
          handle_map (Dict[str, str]): {handle_field: obj_type}, e.g.
              ``{"father_handle": "person", "source_handle": "source"}``.
          client: Any Gramps backend client instance.

      Returns:
          Dict: New arguments dict with gramps_ids resolved to handles.
      """
      result = dict(arguments)
      for handle_field, obj_type in handle_map.items():
          id_field = _gramps_id_field(handle_field)
          gramps_id_value = result.get(id_field)
          if not gramps_id_value:
              continue
          if result.get(handle_field):
              continue  # handle already provided — takes precedence

          if handle_field.endswith("_handles"):
              ids: List[str] = (
                  gramps_id_value
                  if isinstance(gramps_id_value, list)
                  else [gramps_id_value]
              )
              result[handle_field] = [
                  await resolve_handle(gid, obj_type, client) for gid in ids
              ]
          else:
              result[handle_field] = await resolve_handle(
                  gramps_id_value, obj_type, client
              )
      return result
  ```

- [ ] **Step 4: Run to verify all pass**

  ```
  uv run pytest tests/test_gramps_id.py -v
  ```
  Expected: all 14 tests pass.

- [ ] **Step 5: Commit**

  ```bash
  git add src/gramps_mcp/gramps_id.py tests/test_gramps_id.py
  git commit -m "feat: add gramps_id resolution utility module"
  ```

---

## Task 2: Update parameter models

**Files:**
- Modify: `src/gramps_mcp/models/parameters/delete_params.py`
- Modify: `src/gramps_mcp/models/parameters/family_params.py`
- Modify: `src/gramps_mcp/models/parameters/link_edit_params.py`

- [ ] **Step 1: Update delete_params.py**

  Replace the current `handle` field (required `str`) with optional + add `gramps_id`:

  ```python
  """Parameters for the delete_object MCP tool."""

  from typing import Literal, Optional

  from pydantic import BaseModel, Field


  class DeleteObjectParams(BaseModel):
      """Parameters for delete_object tool."""

      obj_type: Literal[
          "person", "family", "event", "place", "citation",
          "source", "note", "media", "repository", "tag",
      ] = Field(..., description="Type of Gramps object to delete")
      handle: Optional[str] = Field(
          None, description="Internal handle of the object to delete."
      )
      gramps_id: Optional[str] = Field(
          None,
          description=(
              "Gramps ID of the object to delete (e.g. 'I0001'). "
              "Alternative to handle — resolved automatically."
          ),
      )
      confirmed: bool = Field(
          False,
          description=(
              "False (default) = dry-run, returns summary without deleting. "
              "True = execute deletion. Always call with False first."
          ),
      )
  ```

- [ ] **Step 2: Update family_params.py**

  Add `gramps_id` alternatives for reference handles in `FamilySaveParams` and `FamilyTimelineParams`:

  ```python
  class FamilySaveParams(BaseModel):
      """Parameters for creating or updating a family."""

      handle: Optional[str] = Field(
          None, description="Family's handle (for updates; omit for new family)"
      )
      gramps_id: Optional[str] = Field(
          None, description="Family's Gramps ID for updates (e.g. 'F0001'). Alternative to handle."
      )
      father_handle: Optional[str] = Field(None, description="Father's handle")
      father_gramps_id: Optional[str] = Field(
          None, description="Father's Gramps ID (e.g. 'I0001'). Alternative to father_handle."
      )
      mother_handle: Optional[str] = Field(None, description="Mother's handle")
      mother_gramps_id: Optional[str] = Field(
          None, description="Mother's Gramps ID (e.g. 'I0002'). Alternative to mother_handle."
      )
      child_handles: Optional[List[str]] = Field(
          None, description="List of child handles"
      )
      child_gramps_ids: Optional[List[str]] = Field(
          None, description="List of child Gramps IDs (e.g. ['I0003']). Alternative to child_handles."
      )
      event_ref_list: Optional[List[dict]] = Field(
          None, description="List of event references"
      )
      note_list: Optional[List[str]] = Field(None, description="List of note handles")
      urls: Optional[List[dict]] = Field(
          None, description="List of URLs associated with the family"
      )
      media_list: Optional[List[dict]] = Field(
          None, description="List of media references"
      )


  class FamilyTimelineParams(BaseModel):
      """Parameters for getting family timeline information."""

      handle: Optional[str] = Field(
          None, min_length=8, description="The unique identifier for a family"
      )
      gramps_id: Optional[str] = Field(
          None, description="Family Gramps ID (e.g. 'F0001'). Alternative to handle."
      )
      dates: Optional[str] = Field(None, description="Date range to bound the timeline")
      events: Optional[str] = Field(
          None, description="Comma delimited list of specific events"
      )
      event_classes: Optional[str] = Field(
          None, description="Comma delimited list of event classes"
      )
      ratings: Optional[bool] = Field(
          None, description="Include citation count and highest confidence score"
      )
      discard_empty: Optional[bool] = Field(None, description="Discard undated events")
      page: Optional[int] = Field(None, ge=0, description="Page number for pagination")
      pagesize: Optional[int] = Field(None, ge=1, description="Number of items per page")
  ```

- [ ] **Step 3: Update link_edit_params.py**

  Replace with this full file content:

  ```python
  """Pydantic parameter models for the three link-edit MCP tools."""

  from typing import Literal, Optional

  from pydantic import BaseModel, Field


  class AddEventToPersonParams(BaseModel):
      """Parameters for add_event_to_person tool."""

      person_handle: Optional[str] = Field(
          None, description="Handle of the person to link the event to"
      )
      person_gramps_id: Optional[str] = Field(
          None, description="Gramps ID of the person (e.g. 'I0001'). Alternative to person_handle."
      )
      event_handle: Optional[str] = Field(
          None, description="Handle of the event to link (must already exist in the DB)"
      )
      event_gramps_id: Optional[str] = Field(
          None, description="Gramps ID of the event (e.g. 'E0001'). Alternative to event_handle."
      )
      role: str = Field(
          "Primary",
          description=(
              "Role of the person in the event. Common values: Primary, Witness, "
              "Godparent, Informant. Default: Primary."
          ),
      )


  class RemoveChildFromFamilyParams(BaseModel):
      """Parameters for remove_child_from_family tool."""

      family_handle: Optional[str] = Field(None, description="Handle of the family")
      family_gramps_id: Optional[str] = Field(
          None, description="Gramps ID of the family (e.g. 'F0001'). Alternative to family_handle."
      )
      child_handle: Optional[str] = Field(
          None, description="Handle of the child person to remove"
      )
      child_gramps_id: Optional[str] = Field(
          None, description="Gramps ID of the child (e.g. 'I0003'). Alternative to child_handle."
      )


  class MoveAttachmentParams(BaseModel):
      """Parameters for move_attachment tool."""

      attachment_type: Literal["note", "media"] = Field(
          ..., description="Type of attachment to move: 'note' or 'media'"
      )
      handle: Optional[str] = Field(
          None, description="Handle of the note or media object to move"
      )
      gramps_id: Optional[str] = Field(
          None, description="Gramps ID of the note or media object. Alternative to handle."
      )
      from_handle: Optional[str] = Field(None, description="Handle of the source object")
      from_gramps_id: Optional[str] = Field(
          None, description="Gramps ID of the source object. Alternative to from_handle."
      )
      from_type: Literal["person", "family"] = Field(
          "person", description="Type of the source object: 'person' or 'family'"
      )
      to_handle: Optional[str] = Field(None, description="Handle of the target object")
      to_gramps_id: Optional[str] = Field(
          None, description="Gramps ID of the target object. Alternative to to_handle."
      )
      to_type: Literal["person", "family"] = Field(
          "person", description="Type of the target object: 'person' or 'family'"
      )
  ```

- [ ] **Step 4: Run existing tests to verify no regressions**

  ```
  uv run pytest tests/test_link_edit_tools.py tests/test_sqlite_client.py -v
  ```
  Expected: all pass.

- [ ] **Step 5: Commit**

  ```bash
  git add src/gramps_mcp/models/parameters/delete_params.py \
          src/gramps_mcp/models/parameters/family_params.py \
          src/gramps_mcp/models/parameters/link_edit_params.py
  git commit -m "feat: add gramps_id fields to delete, family, link_edit param models"
  ```

---

## Task 3: Hook get_person_tool and get_family_tool

**Files:**
- Modify: `src/gramps_mcp/tools/search_details.py:53-82` (get_person_tool)
- Modify: `src/gramps_mcp/tools/search_details.py:86-106` (get_family_tool)
- Test: `tests/test_gramps_id.py` (append)

- [ ] **Step 1: Append integration tests**

  Append to `tests/test_gramps_id.py`:

  ```python
  # ---------------------------------------------------------------------------
  # Tool integration — get_person_tool
  # ---------------------------------------------------------------------------

  class TestGetPersonToolGramsId:
      async def test_get_person_by_gramps_id(self, monkeypatch, sqlite_client):
          monkeypatch.delenv("GRAMPS_PRIVACY_MODE", raising=False)
          from gramps_mcp.tools.search_details import get_person_tool
          result = await get_person_tool(sqlite_client, {"gramps_id": "I0001"})
          assert any("John" in r.text for r in result)

      async def test_get_person_by_handle_still_works(self, monkeypatch, sqlite_client):
          monkeypatch.delenv("GRAMPS_PRIVACY_MODE", raising=False)
          from gramps_mcp.tools.search_details import get_person_tool
          result = await get_person_tool(sqlite_client, {"handle": "h_pe_john"})
          assert any("John" in r.text for r in result)

      async def test_get_family_by_gramps_id(self, sqlite_client):
          from gramps_mcp.tools.search_details import get_family_tool
          result = await get_family_tool(sqlite_client, {"gramps_id": "F0001"})
          assert any("F0001" in r.text or "Smith" in r.text or "John" in r.text for r in result)
  ```

- [ ] **Step 2: Run to verify they fail**

  ```
  uv run pytest tests/test_gramps_id.py::TestGetPersonToolGramsId -v
  ```
  Expected: FAIL — get_family_tool gramps_id test fails (family_handle required).

- [ ] **Step 3: Update get_person_tool**

  In `src/gramps_mcp/tools/search_details.py`, replace `get_person_tool` (lines 53-82) with:

  ```python
  @with_client
  async def get_person_tool(client, arguments: Dict) -> List[TextContent]:
      """
      Get comprehensive person information including parents, siblings, spouse and children.
      Accepts handle or gramps_id (e.g. 'I0001').
      """
      try:
          from ..gramps_id import resolve_handles
          arguments = await resolve_handles(arguments, {"handle": "person"}, client)
          handle = arguments.get("handle") or arguments.get("person_handle")
          if not handle:
              raise ValueError("handle or gramps_id is required")
          settings = get_settings()
          tree_id = settings.gramps_tree_id
          formatted_person = await format_person_detail(client, tree_id, handle)
          return [TextContent(type="text", text=formatted_person)]
      except Exception as e:
          return _format_error_response(e, "person details retrieval")
  ```

- [ ] **Step 4: Update get_family_tool**

  Replace `get_family_tool` (lines 85-106) with:

  ```python
  @with_client
  async def get_family_tool(client, arguments: Dict) -> List[TextContent]:
      """
      Get detailed family information.
      Accepts family_handle or gramps_id (e.g. 'F0001').
      """
      try:
          from ..gramps_id import resolve_handles
          arguments = await resolve_handles(arguments, {"handle": "family"}, client)
          handle = arguments.get("handle") or arguments.get("family_handle")
          if not handle:
              raise ValueError("handle or gramps_id is required")
          settings = get_settings()
          tree_id = settings.gramps_tree_id
          formatted_family = await format_family_detail(client, tree_id, handle)
          return [TextContent(type="text", text=formatted_family)]
      except Exception as e:
          return _format_error_response(e, "family details retrieval")
  ```

- [ ] **Step 5: Run tests**

  ```
  uv run pytest tests/test_gramps_id.py::TestGetPersonToolGramsId -v
  ```
  Expected: all 3 pass.

- [ ] **Step 6: Commit**

  ```bash
  git add src/gramps_mcp/tools/search_details.py tests/test_gramps_id.py
  git commit -m "feat: add gramps_id support to get_person_tool and get_family_tool"
  ```

---

## Task 4: Hook get_event_tool and get_place_tool

These tools currently accept only `gramps_id`. This task adds `handle` as an alternative.

**Files:**
- Modify: `src/gramps_mcp/tools/search_details.py:110-190` (get_event_tool)
- Modify: `src/gramps_mcp/tools/search_details.py:192-272` (get_place_tool)
- Test: `tests/test_gramps_id.py` (append)

- [ ] **Step 1: Append tests**

  Append to `tests/test_gramps_id.py`:

  ```python
  class TestGetEventToolHandleOrId:
      async def test_get_event_by_gramps_id(self, sqlite_client):
          from gramps_mcp.tools.search_details import get_event_tool
          result = await get_event_tool(sqlite_client, {"gramps_id": "E0001"})
          assert any("E0001" in r.text or "Birth" in r.text for r in result)

      async def test_get_event_by_handle(self, sqlite_client):
          from gramps_mcp.tools.search_details import get_event_tool
          result = await get_event_tool(sqlite_client, {"handle": "h_ev_birth_john"})
          assert any("E0001" in r.text or "Birth" in r.text for r in result)

  class TestGetPlaceToolHandleOrId:
      async def test_get_place_by_gramps_id(self, sqlite_client):
          from gramps_mcp.tools.search_details import get_place_tool
          result = await get_place_tool(sqlite_client, {"gramps_id": "P0001"})
          assert any("Berlin" in r.text or "P0001" in r.text for r in result)

      async def test_get_place_by_handle(self, sqlite_client):
          from gramps_mcp.tools.search_details import get_place_tool
          result = await get_place_tool(sqlite_client, {"handle": "h_pl_berlin"})
          assert any("Berlin" in r.text or "P0001" in r.text for r in result)
  ```

- [ ] **Step 2: Run to verify handle tests fail**

  ```
  uv run pytest tests/test_gramps_id.py::TestGetEventToolHandleOrId tests/test_gramps_id.py::TestGetPlaceToolHandleOrId -v
  ```
  Expected: handle-based tests fail.

- [ ] **Step 3: Update get_event_tool**

  In `get_event_tool`, replace the opening block that currently requires `gramps_id`:

  ```python
  gramps_id = arguments.get("gramps_id")
  if not gramps_id:
      raise ValueError("gramps_id is required")
  settings = get_settings()
  tree_id = settings.gramps_tree_id
  events = await client.make_api_call(
      ApiCalls.GET_EVENTS, tree_id=tree_id,
      params={"gramps_id": gramps_id, "pagesize": 1},
  )
  if not events:
      return [TextContent(type="text", text=f"Event {gramps_id} not found")]
  event = events[0]
  handle = event.get("handle", "")
  ```

  Replace with:

  ```python
  from ..gramps_id import resolve_handles
  arguments = await resolve_handles(arguments, {"handle": "event"}, client)
  handle = arguments.get("handle")
  settings = get_settings()
  tree_id = settings.gramps_tree_id
  if not handle:
      raise ValueError("handle or gramps_id is required")
  event = await client.make_api_call(ApiCalls.GET_EVENT, tree_id=tree_id, handle=handle)
  if not event:
      return [TextContent(type="text", text=f"Event {handle} not found")]
  gramps_id = event.get("gramps_id", handle)
  ```

  Also update the lines below that use `gramps_id` for display — they continue to work since `gramps_id = event.get("gramps_id", handle)`.

- [ ] **Step 4: Update get_place_tool similarly**

  In `get_place_tool`, apply the same pattern — replace the gramps_id-only opening with:

  ```python
  from ..gramps_id import resolve_handles
  arguments = await resolve_handles(arguments, {"handle": "place"}, client)
  handle = arguments.get("handle")
  settings = get_settings()
  tree_id = settings.gramps_tree_id
  if not handle:
      raise ValueError("handle or gramps_id is required")
  place = await client.make_api_call(ApiCalls.GET_PLACE, tree_id=tree_id, handle=handle)
  if not place:
      return [TextContent(type="text", text=f"Place {handle} not found")]
  gramps_id = place.get("gramps_id", handle)
  ```

- [ ] **Step 5: Run tests**

  ```
  uv run pytest tests/test_gramps_id.py::TestGetEventToolHandleOrId tests/test_gramps_id.py::TestGetPlaceToolHandleOrId -v
  ```
  Expected: all 4 pass.

- [ ] **Step 6: Run broader regression**

  ```
  uv run pytest tests/test_sqlite_detail_tools.py -v
  ```
  Expected: all pass.

- [ ] **Step 7: Commit**

  ```bash
  git add src/gramps_mcp/tools/search_details.py tests/test_gramps_id.py
  git commit -m "feat: add handle option to get_event_tool and get_place_tool"
  ```

---

## Task 5: Hook delete_object_tool, create_family_tool, create_citation_tool

**Files:**
- Modify: `src/gramps_mcp/tools/delete.py` (delete_object_tool entry)
- Modify: `src/gramps_mcp/tools/data_management.py` (create_family_tool, create_citation_tool)
- Test: `tests/test_gramps_id.py` (append)

- [ ] **Step 1: Append tests**

  Append to `tests/test_gramps_id.py`:

  ```python
  class TestDeleteObjectToolGramsId:
      async def test_delete_dry_run_by_gramps_id(self, sqlite_client):
          from gramps_mcp.tools.delete import delete_object_tool
          result = await delete_object_tool(
              sqlite_client,
              {"obj_type": "event", "gramps_id": "E0001", "confirmed": False},
          )
          text = result[0].text if result else ""
          assert "E0001" in text or "dry" in text.lower() or "Birth" in text

  class TestCreateCitationToolGramsId:
      async def test_citation_created_with_source_gramps_id(self, sqlite_client):
          from gramps_mcp.tools.data_management import create_citation_tool
          result = await create_citation_tool(
              {"source_gramps_id": "S0001", "page": "p.42"}
          )
          text = result[0].text if result else ""
          assert "error" not in text.lower() or "S0001" in text
  ```

- [ ] **Step 2: Run to verify delete test fails**

  ```
  uv run pytest tests/test_gramps_id.py::TestDeleteObjectToolGramsId -v
  ```
  Expected: FAIL — `delete_object_tool` does not accept `gramps_id`.

- [ ] **Step 3: Hook delete_object_tool**

  In `src/gramps_mcp/tools/delete.py`, read the function signature at the top. The tool receives `client` and `arguments` dict. Add the resolve call at the entry point.

  Find `async def delete_object_tool(` and add after the `try:` opening:

  ```python
  from ..gramps_id import resolve_handles
  arguments = await resolve_handles(
      arguments, {"handle": arguments.get("obj_type", "person")}, client
  )
  ```

  The `obj_type` field is always present (it's required), so `arguments.get("obj_type", "person")` is safe.

- [ ] **Step 4: Hook create_citation_tool**

  In `src/gramps_mcp/tools/data_management.py`, replace the entire existing `source_gramps_id` block in `create_citation_tool`:

  ```python
  async def create_citation_tool(arguments: Dict) -> List[TextContent]:
      """
      Create or update citation including object associations.
      """
      client = get_client()
      arguments = await resolve_handles(arguments, {"source_handle": "source"}, client)
      return await _handle_crud_operation(
          arguments,
          "citation",
          ApiCalls.POST_CITATIONS,
          ApiCalls.PUT_CITATION,
          CitationData,
      )
  ```

  Also add the import at the top of `data_management.py` (alongside the `GrampsSqliteClient` import already there):

  ```python
  from ..gramps_id import resolve_handles
  ```

  And remove the now-unused `GrampsSqliteClient` import (since the `isinstance` check is gone).

- [ ] **Step 5: Hook create_family_tool**

  In `src/gramps_mcp/tools/data_management.py`, find `create_family_tool` and add the resolve call:

  ```python
  async def create_family_tool(arguments: Dict) -> List[TextContent]:
      """
      Create or update family record.
      """
      client = get_client()
      arguments = await resolve_handles(
          arguments,
          {
              "handle": "family",
              "father_handle": "person",
              "mother_handle": "person",
              "child_handles": "person",
          },
          client,
      )
      return await _handle_crud_operation(
          arguments, "family", ApiCalls.POST_FAMILIES, ApiCalls.PUT_FAMILY, FamilySaveParams
      )
  ```

- [ ] **Step 6: Run tests**

  ```
  uv run pytest tests/test_gramps_id.py::TestDeleteObjectToolGramsId tests/test_gramps_id.py::TestCreateCitationToolGramsId -v
  ```
  Expected: all pass.

- [ ] **Step 7: Run regression**

  ```
  uv run pytest tests/test_sqlite_integration.py tests/test_privacy.py -v
  ```
  Expected: all pass.

- [ ] **Step 8: Commit**

  ```bash
  git add src/gramps_mcp/tools/delete.py \
          src/gramps_mcp/tools/data_management.py \
          tests/test_gramps_id.py
  git commit -m "feat: add gramps_id support to delete, create_family, create_citation tools"
  ```

---

## Task 6: Hook link_edit tools via server.py

The link_edit tools (`add_event_to_person`, `remove_child_from_family`, `move_attachment`) have non-Dict signatures. Resolution happens in `server.py` by replacing their lambdas with async handler functions.

**Files:**
- Modify: `src/gramps_mcp/server.py:553-597`
- Test: `tests/test_gramps_id.py` (append)

- [ ] **Step 1: Append tests**

  Append to `tests/test_gramps_id.py`:

  ```python
  class TestLinkEditToolsGramsId:
      async def test_add_event_to_person_by_gramps_id(self, sqlite_client):
          from gramps_mcp.gramps_id import resolve_handles
          from gramps_mcp.tools.link_edit import add_event_to_person_tool
          args = {"person_gramps_id": "I0001", "event_gramps_id": "E0003"}
          args = await resolve_handles(
              args,
              {"person_handle": "person", "event_handle": "event"},
              sqlite_client,
          )
          result = await add_event_to_person_tool(
              person_handle=args["person_handle"],
              event_handle=args["event_handle"],
              role="Primary",
              db=sqlite_client._db,
          )
          import json
          data = json.loads(result)
          assert data.get("result") in ("ok", "no_change")

      async def test_remove_child_by_gramps_id(self, sqlite_client):
          from gramps_mcp.gramps_id import resolve_handles
          from gramps_mcp.tools.link_edit import remove_child_from_family_tool
          args = {"family_gramps_id": "F0001", "child_gramps_id": "I0003"}
          args = await resolve_handles(
              args,
              {"family_handle": "family", "child_handle": "person"},
              sqlite_client,
          )
          # dry check — just confirm resolution worked
          assert args["family_handle"] == "h_fa_smith"
          assert args["child_handle"] == "h_pe_child"
  ```

- [ ] **Step 2: Run to verify they pass (resolution tested independently)**

  ```
  uv run pytest tests/test_gramps_id.py::TestLinkEditToolsGramsId -v
  ```
  Expected: both pass (they test `resolve_handles` directly, not server.py dispatch).

- [ ] **Step 3: Update server.py link_edit handlers**

  In `src/gramps_mcp/server.py`, add imports near the top (after existing imports):

  ```python
  from .gramps_id import resolve_handles
  ```

  Replace the three lambda handlers in `TOOL_REGISTRY` with async functions. Find the entries for `"add_event_to_person"`, `"remove_child_from_family"`, and `"move_attachment"` and replace:

  ```python
  "add_event_to_person": {
      "description": (
          "Append an event reference to a person's event_ref_list without replacing it. "
          "Automatically updates birth_ref_index and death_ref_index when a Birth or "
          "Death event is added. Use this instead of create_person when you want to add "
          "a single event and preserve existing event links. SQLite backend only."
      ),
      "schema": AddEventToPersonParams,
      "handler": _handle_add_event_to_person,
  },
  "remove_child_from_family": {
      "description": (
          "Remove a child from a family and clean up the child's parent_family_list. "
          "Both the family's child_ref_list and the child person's parent_family_list "
          "are updated in a single transaction. SQLite backend only."
      ),
      "schema": RemoveChildFromFamilyParams,
      "handler": _handle_remove_child_from_family,
  },
  "move_attachment": {
      "description": (
          "Move a note or media reference from one object to another atomically. "
          "Removes the handle from the source's note_list/media_list and adds it "
          "to the target's list in a single transaction. Use for correcting GEDCOM "
          "import errors where notes/media landed on the wrong person. "
          "Supports person->person and person->family moves. SQLite backend only. "
          "Supports person and family objects as source and target only."
      ),
      "schema": MoveAttachmentParams,
      "handler": _handle_move_attachment,
  },
  ```

  And add the three handler functions just before `TOOL_REGISTRY`:

  ```python
  async def _handle_add_event_to_person(args: Dict) -> Any:
      args = await resolve_handles(
          args, {"person_handle": "person", "event_handle": "event"}, get_client()
      )
      return await add_event_to_person_tool(
          person_handle=args["person_handle"],
          event_handle=args["event_handle"],
          role=args.get("role", "Primary"),
      )


  async def _handle_remove_child_from_family(args: Dict) -> Any:
      args = await resolve_handles(
          args, {"family_handle": "family", "child_handle": "person"}, get_client()
      )
      return await remove_child_from_family_tool(
          family_handle=args["family_handle"],
          child_handle=args["child_handle"],
      )


  async def _handle_move_attachment(args: Dict) -> Any:
      args = await resolve_handles(
          args,
          {
              "handle": args.get("attachment_type", "note"),
              "from_handle": args.get("from_type", "person"),
              "to_handle": args.get("to_type", "person"),
          },
          get_client(),
      )
      return await move_attachment_tool(
          attachment_type=args["attachment_type"],
          handle=args["handle"],
          from_handle=args["from_handle"],
          from_type=args.get("from_type", "person"),
          to_handle=args["to_handle"],
          to_type=args.get("to_type", "person"),
      )
  ```

  **Note on `_handle_move_attachment`:** The `handle` field refers to the note/media object itself, not a person/family. Its obj_type is `attachment_type` ("note" or "media"). The `from_handle`/`to_handle` obj_types come from `from_type`/`to_type` fields.

- [ ] **Step 4: Run full test suite**

  ```
  uv run pytest tests/test_gramps_id.py tests/test_link_edit_tools.py tests/test_privacy.py -v
  ```
  Expected: all pass.

- [ ] **Step 5: Commit**

  ```bash
  git add src/gramps_mcp/server.py tests/test_gramps_id.py
  git commit -m "feat: add gramps_id support to link_edit tools via server.py handlers"
  ```

---

## Task 7: Final regression and cleanup

**Files:**
- Test only

- [ ] **Step 1: Run all SQLite tests**

  ```
  uv run pytest tests/test_sqlite_client.py tests/test_sqlite_integration.py tests/test_sqlite_detail_tools.py tests/test_sqlite_layer_fixes.py tests/test_link_edit_tools.py tests/test_privacy.py tests/test_gramps_id.py -v
  ```
  Expected: all pass.

- [ ] **Step 2: Verify gramps_id test count**

  ```
  uv run pytest tests/test_gramps_id.py --collect-only -q
  ```
  Expected: at least 25 tests collected.

- [ ] **Step 3: Commit if any leftover changes**

  ```bash
  git status
  # If clean, nothing to do. If changes, commit them.
  ```

---

## Self-Review

**Spec coverage:**
- ✅ New `gramps_id.py` with `resolve_handle` + `resolve_handles` — Task 1
- ✅ SQLite/DirectClient backend via `db.get_by_id` — Task 1
- ✅ Web backend via list-endpoint API call — Task 1
- ✅ Naming convention (`_gramps_id_field`) — Task 1 tested
- ✅ `DeleteObjectParams` — Task 2
- ✅ `FamilySaveParams`, `FamilyTimelineParams` — Task 2
- ✅ `AddEventToPersonParams`, `RemoveChildFromFamilyParams`, `MoveAttachmentParams` — Task 2
- ✅ get_person, get_family — Task 3
- ✅ get_event, get_place — Task 4
- ✅ delete_object, create_family, create_citation — Task 5
- ✅ link_edit tools — Task 6
- ✅ Error: gramps_id not found → GrampsAPIError — Task 1 tested

**Not in scope** (already gramps_id-based): merge_persons, split_person, merge_places, merge_events, merge_citations, merge_families, get_ancestors, get_descendants, prepare_biography, DNA tools.

**Note on `move_attachment` handle types:** The `handle` field is for the note/media object itself (`attachment_type` = "note" or "media"). The `from_handle`/`to_handle` types come from `from_type`/`to_type`. The handler maps these correctly using `args.get("attachment_type")` and `args.get("from_type")`.
