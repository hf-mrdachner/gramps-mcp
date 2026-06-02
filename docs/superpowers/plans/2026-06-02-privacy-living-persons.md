# Privacy / Living Persons Feature — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a global opt-in privacy filter (`GRAMPS_PRIVACY_MODE=true`) that redacts living persons' sensitive data from all MCP tool responses before it reaches an AI provider.

**Architecture:** A new `privacy.py` module exposes `redact_if_living(person_data, client, tree_id)` which checks the config flag and person's living status, then either returns the original dict or a redacted copy. Handlers call this function right after fetching person data, before any formatting. Backend detection uses `isinstance` to choose between native SQLite heuristics and the web API's `GET_LIVING` endpoint.

**Tech Stack:** Python, Pydantic (already in use), pytest (asyncio_mode=auto so no `@pytest.mark.asyncio` decorators needed), existing SQLite in-memory fixture.

**Spec:** `docs/superpowers/specs/2026-06-02-privacy-living-persons-design.md`

---

## File Map

| Action | Path | Purpose |
|---|---|---|
| Modify | `src/gramps_mcp/config.py` | Add `gramps_privacy_mode: bool` field |
| Create | `src/gramps_mcp/privacy.py` | All privacy logic |
| Modify | `src/gramps_mcp/handlers/person_handler.py` | Redact after GET_PERSON |
| Modify | `src/gramps_mcp/handlers/person_detail_handler.py` | Redact after GET_PERSON |
| Modify | `src/gramps_mcp/handlers/family_detail_handler.py` | Redact father/mother/children/`_get_birth_death_dates` |
| Create | `tests/test_privacy.py` | Unit + integration tests |

---

## Task 1: Add gramps_privacy_mode to Settings

**Files:**
- Modify: `src/gramps_mcp/config.py`

- [ ] **Step 1: Add field to Settings class**

  In `src/gramps_mcp/config.py`, add to the `Settings` class after the `gramps_db_path` field:

  ```python
  gramps_privacy_mode: bool = Field(
      False,
      description=(
          "When True, redact sensitive data for living persons in all tool responses."
      ),
  )
  ```

- [ ] **Step 2: Load from environment in get_settings()**

  In `get_settings()`, add to the `Settings(...)` call:

  ```python
  gramps_privacy_mode=os.environ.get("GRAMPS_PRIVACY_MODE", "false").lower() == "true",
  ```

- [ ] **Step 3: Verify settings load**

  Run:
  ```
  uv run python -c "import os; os.environ['GRAMPS_PRIVACY_MODE']='true'; from gramps_mcp.config import get_settings; s=get_settings(); print(s.gramps_privacy_mode)"
  ```
  Expected output: `True`

- [ ] **Step 4: Commit**

  ```bash
  git add src/gramps_mcp/config.py
  git commit -m "feat: add gramps_privacy_mode setting"
  ```

---

## Task 2: Create privacy.py with all core functions

**Files:**
- Create: `src/gramps_mcp/privacy.py`
- Create: `tests/test_privacy.py`

- [ ] **Step 1: Write failing tests**

  Create `tests/test_privacy.py`:

  ```python
  """
  Tests for the privacy/living persons filter.

  Uses SQLite in-memory fixture from conftest_sqlite.py (auto-discovered via conftest.py).
  No @pytest.mark.asyncio needed — asyncio_mode=auto is set in pytest.ini.
  """
  import datetime
  import pytest

  from gramps_mcp.privacy import (
      _extract_birth_year,
      _is_living_local,
      is_privacy_mode,
      is_living,
      redact_person,
      redact_if_living,
  )


  # ---------------------------------------------------------------------------
  # Helpers
  # ---------------------------------------------------------------------------

  def _person(birth_year=None, death_ref_index=-1):
      """Build a minimal person_data dict for testing _is_living_local."""
      data: dict = {"death_ref_index": death_ref_index, "birth_ref_index": -1}
      if birth_year is not None:
          data["birth_ref_index"] = 0
          data["extended"] = {
              "events": [{"date": {"dateval": [1, 1, birth_year, False]}}]
          }
      return data


  # ---------------------------------------------------------------------------
  # _extract_birth_year
  # ---------------------------------------------------------------------------

  class TestExtractBirthYear:
      def test_extracts_year_from_extended_events(self):
          person = _person(birth_year=1952)
          assert _extract_birth_year(person) == 1952

      def test_returns_none_when_no_birth_ref(self):
          person = {"birth_ref_index": -1}
          assert _extract_birth_year(person) is None

      def test_returns_none_when_year_is_zero(self):
          person = {
              "birth_ref_index": 0,
              "extended": {"events": [{"date": {"dateval": [0, 0, 0, False]}}]},
          }
          assert _extract_birth_year(person) is None

      def test_returns_none_when_no_extended_events(self):
          person = {"birth_ref_index": 0}
          assert _extract_birth_year(person) is None


  # ---------------------------------------------------------------------------
  # _is_living_local
  # ---------------------------------------------------------------------------

  class TestIsLivingLocal:
      def test_death_ref_index_set_returns_false(self):
          assert _is_living_local(_person(birth_year=1980, death_ref_index=1)) is False

      def test_recent_birth_no_death_returns_true(self):
          assert _is_living_local(_person(birth_year=1980)) is True

      def test_old_birth_no_death_returns_false(self):
          old_year = datetime.date.today().year - 121
          assert _is_living_local(_person(birth_year=old_year)) is False

      def test_boundary_exactly_120_years_returns_false(self):
          boundary_year = datetime.date.today().year - 120
          assert _is_living_local(_person(birth_year=boundary_year)) is False

      def test_no_birth_no_death_returns_true(self):
          assert _is_living_local({"death_ref_index": -1, "birth_ref_index": -1}) is True

      def test_empty_birth_date_returns_true(self):
          person = {
              "death_ref_index": -1,
              "birth_ref_index": 0,
              "extended": {"events": [{"date": {"dateval": [0, 0, 0, False]}}]},
          }
          assert _is_living_local(person) is True


  # ---------------------------------------------------------------------------
  # is_privacy_mode
  # ---------------------------------------------------------------------------

  class TestIsPrivacyMode:
      def test_returns_false_when_not_set(self, monkeypatch):
          monkeypatch.delenv("GRAMPS_PRIVACY_MODE", raising=False)
          assert is_privacy_mode() is False

      def test_returns_true_when_set_to_true(self, monkeypatch):
          monkeypatch.setenv("GRAMPS_PRIVACY_MODE", "true")
          assert is_privacy_mode() is True

      def test_case_insensitive_true(self, monkeypatch):
          monkeypatch.setenv("GRAMPS_PRIVACY_MODE", "TRUE")
          assert is_privacy_mode() is True

      def test_returns_false_when_set_to_false(self, monkeypatch):
          monkeypatch.setenv("GRAMPS_PRIVACY_MODE", "false")
          assert is_privacy_mode() is False


  # ---------------------------------------------------------------------------
  # redact_person
  # ---------------------------------------------------------------------------

  class TestRedactPerson:
      def _jane(self):
          return {
              "handle": "h_pe_jane", "gramps_id": "I0002", "gender": 0,
              "primary_name": {
                  "first_name": "Jane",
                  "surname_list": [{"surname": "Doe"}],
              },
              "birth_ref_index": 0,
              "death_ref_index": -1,
              "event_ref_list": [{"ref": "h_ev_birth_jane"}],
              "note_list": ["h_no_1"],
              "media_list": ["h_me_1"],
              "family_list": ["h_fa_smith"],
              "parent_family_list": [],
          }

      def test_name_replaced(self):
          result = redact_person(self._jane())
          assert result["primary_name"]["first_name"] == "[Living]"
          assert result["primary_name"]["surname_list"][0]["surname"] == "[Living]"

      def test_event_ref_list_cleared(self):
          assert redact_person(self._jane())["event_ref_list"] == []

      def test_note_list_cleared(self):
          assert redact_person(self._jane())["note_list"] == []

      def test_media_list_cleared(self):
          assert redact_person(self._jane())["media_list"] == []

      def test_birth_death_indices_cleared(self):
          result = redact_person(self._jane())
          assert result["birth_ref_index"] == -1
          assert result["death_ref_index"] == -1

      def test_living_marker_set(self):
          assert redact_person(self._jane())["living"] is True

      def test_handle_gramps_id_gender_preserved(self):
          result = redact_person(self._jane())
          assert result["handle"] == "h_pe_jane"
          assert result["gramps_id"] == "I0002"
          assert result["gender"] == 0

      def test_family_lists_preserved(self):
          result = redact_person(self._jane())
          assert result["family_list"] == ["h_fa_smith"]
          assert result["parent_family_list"] == []

      def test_returns_new_dict_original_unchanged(self):
          jane = self._jane()
          result = redact_person(jane)
          assert result is not jane
          assert jane["event_ref_list"] == [{"ref": "h_ev_birth_jane"}]


  # ---------------------------------------------------------------------------
  # redact_if_living (integration, uses sqlite_client fixture)
  # ---------------------------------------------------------------------------

  class TestRedactIfLiving:
      async def test_privacy_off_living_person_not_redacted(
          self, monkeypatch, sqlite_client
      ):
          monkeypatch.delenv("GRAMPS_PRIVACY_MODE", raising=False)
          person = _person(birth_year=1980)
          person.update({"handle": "x", "primary_name": {"first_name": "A",
              "surname_list": [{"surname": "B"}]}, "event_ref_list": [],
              "note_list": [], "media_list": []})
          result = await redact_if_living(person, sqlite_client, "default")
          assert result is person

      async def test_privacy_on_living_person_redacted(
          self, monkeypatch, sqlite_client
      ):
          monkeypatch.setenv("GRAMPS_PRIVACY_MODE", "true")
          person = _person(birth_year=1980)
          person.update({"handle": "x", "primary_name": {"first_name": "A",
              "surname_list": [{"surname": "B"}]}, "event_ref_list": [],
              "note_list": [], "media_list": []})
          result = await redact_if_living(person, sqlite_client, "default")
          assert result["primary_name"]["first_name"] == "[Living]"

      async def test_privacy_on_deceased_not_redacted(
          self, monkeypatch, sqlite_client
      ):
          monkeypatch.setenv("GRAMPS_PRIVACY_MODE", "true")
          person = _person(birth_year=1950, death_ref_index=1)
          person.update({"handle": "x", "primary_name": {"first_name": "John",
              "surname_list": [{"surname": "Smith"}]}, "event_ref_list": [],
              "note_list": [], "media_list": []})
          result = await redact_if_living(person, sqlite_client, "default")
          assert result["primary_name"]["first_name"] == "John"
  ```

- [ ] **Step 2: Run tests to verify they all fail**

  ```
  uv run pytest tests/test_privacy.py -v
  ```
  Expected: all tests fail with `ModuleNotFoundError: No module named 'gramps_mcp.privacy'`

- [ ] **Step 3: Create privacy.py**

  Create `src/gramps_mcp/privacy.py`:

  ```python
  """
  Privacy filter for living persons.

  Redacts sensitive fields from person data when GRAMPS_PRIVACY_MODE is enabled,
  preventing living persons' data from leaking to AI providers via MCP responses.
  """
  import datetime
  import logging
  from typing import Optional

  from .config import get_settings
  from .models.api_calls import ApiCalls

  logger = logging.getLogger(__name__)


  def is_privacy_mode() -> bool:
      """
      Return True when GRAMPS_PRIVACY_MODE=true is set.

      Returns:
          bool: True if privacy mode is active.
      """
      return get_settings().gramps_privacy_mode


  def _extract_birth_year(person_data: dict) -> Optional[int]:
      """
      Extract birth year from person data using extended events.

      Args:
          person_data (dict): Person data dict, ideally fetched with extend=all.

      Returns:
          Optional[int]: Birth year, or None if not determinable.
      """
      birth_ref_index = person_data.get("birth_ref_index", -1)
      if birth_ref_index < 0:
          return None
      events = person_data.get("extended", {}).get("events", [])
      if birth_ref_index >= len(events):
          return None
      dateval = events[birth_ref_index].get("date", {}).get("dateval", [])
      if len(dateval) >= 3 and isinstance(dateval[2], int) and dateval[2] > 0:
          return dateval[2]
      return None


  def _is_living_local(person_data: dict) -> bool:
      """
      Determine living status from person data without API calls.

      Rule: deceased if death_ref_index >= 0. Potentially alive if birth year is
      within the last 120 years. Conservative (living) when birth year is unknown.

      Args:
          person_data (dict): Person data dict.

      Returns:
          bool: True if the person should be treated as living.
      """
      if person_data.get("death_ref_index", -1) >= 0:
          return False
      birth_year = _extract_birth_year(person_data)
      if birth_year is not None:
          return birth_year > (datetime.date.today().year - 120)
      return True  # Reason: no death and no usable birth year — conservative


  async def is_living(person_data: dict, client, tree_id: str) -> bool:
      """
      Determine whether a person should be treated as living.

      Uses the native SQLite heuristic for direct backends, and the
      GET_LIVING web endpoint for the web backend.

      Args:
          person_data (dict): Person data dict.
          client: Any Gramps backend client instance.
          tree_id (str): Family tree identifier.

      Returns:
          bool: True if the person is (or may be) living.
      """
      from .sqlite_client import GrampsSqliteClient
      from .direct_client import GrampsDirectClient

      if isinstance(client, (GrampsSqliteClient, GrampsDirectClient)):
          return _is_living_local(person_data)

      handle = person_data.get("handle", "")
      if not handle:
          return True
      try:
          result = await client.make_api_call(
              ApiCalls.GET_LIVING, tree_id=tree_id, handle=handle
          )
          if isinstance(result, bool):
              return result
          if isinstance(result, dict):
              return result.get("is_alive", True)
      except Exception:
          logger.debug("GET_LIVING failed for %s, defaulting to living", handle)
      return True


  def redact_person(person_data: dict) -> dict:
      """
      Return a shallow copy of person_data with sensitive fields replaced.

      Preserved: handle, gramps_id, gender, family_list, parent_family_list.
      Cleared: primary_name, event_ref_list, note_list, media_list,
               birth_ref_index, death_ref_index.

      Args:
          person_data (dict): Original person data dict.

      Returns:
          dict: New dict with sensitive fields redacted.
      """
      redacted = dict(person_data)
      redacted["primary_name"] = {
          "first_name": "[Living]",
          "surname_list": [{"surname": "[Living]"}],
      }
      redacted["event_ref_list"] = []
      redacted["note_list"] = []
      redacted["media_list"] = []
      redacted["birth_ref_index"] = -1
      redacted["death_ref_index"] = -1
      redacted["living"] = True
      return redacted


  async def redact_if_living(person_data: dict, client, tree_id: str) -> dict:
      """
      Return redacted person data if privacy mode is on and person is living.

      Args:
          person_data (dict): Person data dict from make_api_call.
          client: Any Gramps backend client instance.
          tree_id (str): Family tree identifier.

      Returns:
          dict: Original or redacted person data dict.
      """
      if not is_privacy_mode():
          return person_data
      if not await is_living(person_data, client, tree_id):
          return person_data
      return redact_person(person_data)
  ```

- [ ] **Step 4: Run tests to verify they all pass**

  ```
  uv run pytest tests/test_privacy.py -v
  ```
  Expected: all tests pass.

- [ ] **Step 5: Commit**

  ```bash
  git add src/gramps_mcp/privacy.py tests/test_privacy.py
  git commit -m "feat: add privacy module with living-person redaction logic"
  ```

---

## Task 3: Hook person_handler.py

**Files:**
- Modify: `src/gramps_mcp/handlers/person_handler.py`
- Test: `tests/test_privacy.py` (extend existing file)

- [ ] **Step 1: Write failing integration test**

  Append to `tests/test_privacy.py`:

  ```python
  # ---------------------------------------------------------------------------
  # Handler integration — format_person
  # ---------------------------------------------------------------------------

  class TestFormatPersonPrivacy:
      async def test_living_person_name_redacted(self, monkeypatch, sqlite_client):
          """Jane (h_pe_jane, born 1952, no death) must show [Living] with privacy on."""
          monkeypatch.setenv("GRAMPS_PRIVACY_MODE", "true")
          from gramps_mcp.handlers.person_handler import format_person
          result = await format_person(sqlite_client, "default", "h_pe_jane")
          assert "[Living]" in result

      async def test_living_person_birth_date_not_shown(self, monkeypatch, sqlite_client):
          monkeypatch.setenv("GRAMPS_PRIVACY_MODE", "true")
          from gramps_mcp.handlers.person_handler import format_person
          result = await format_person(sqlite_client, "default", "h_pe_jane")
          assert "Born:" not in result

      async def test_deceased_person_not_redacted(self, monkeypatch, sqlite_client):
          """John (h_pe_john, born 1950, died 2020) must not be redacted."""
          monkeypatch.setenv("GRAMPS_PRIVACY_MODE", "true")
          from gramps_mcp.handlers.person_handler import format_person
          result = await format_person(sqlite_client, "default", "h_pe_john")
          assert "John" in result

      async def test_privacy_off_person_shown_normally(self, monkeypatch, sqlite_client):
          monkeypatch.delenv("GRAMPS_PRIVACY_MODE", raising=False)
          from gramps_mcp.handlers.person_handler import format_person
          result = await format_person(sqlite_client, "default", "h_pe_jane")
          assert "Jane" in result
  ```

- [ ] **Step 2: Run test to verify it fails**

  ```
  uv run pytest tests/test_privacy.py::TestFormatPersonPrivacy -v
  ```
  Expected: FAIL — Jane's name still shows normally.

- [ ] **Step 3: Add import and redact call to person_handler.py**

  In `src/gramps_mcp/handlers/person_handler.py`, add import after the existing imports:

  ```python
  from ..privacy import redact_if_living
  ```

  In `format_person()`, after the `person_data = await client.make_api_call(...)` call (around line 46), add:

  ```python
  person_data = await redact_if_living(person_data, client, tree_id)
  ```

  The block should look like:

  ```python
  person_data = await client.make_api_call(
      ApiCalls.GET_PERSON,
      tree_id=tree_id,
      handle=handle,
      params={"extend": "all"},
  )
  person_data = await redact_if_living(person_data, client, tree_id)
  if not person_data:
      return f"• **Unknown Person** (Handle: {handle})\n  No data available\n\n"
  ```

- [ ] **Step 4: Run tests to verify they pass**

  ```
  uv run pytest tests/test_privacy.py::TestFormatPersonPrivacy -v
  ```
  Expected: all 4 tests pass.

- [ ] **Step 5: Run full test suite to check for regressions**

  ```
  uv run pytest tests/test_search_basic.py tests/test_sqlite_client.py tests/test_sqlite_integration.py -v
  ```
  Expected: all pass.

- [ ] **Step 6: Commit**

  ```bash
  git add src/gramps_mcp/handlers/person_handler.py tests/test_privacy.py
  git commit -m "feat: redact living persons in format_person"
  ```

---

## Task 4: Hook person_detail_handler.py

**Files:**
- Modify: `src/gramps_mcp/handlers/person_detail_handler.py`
- Test: `tests/test_privacy.py` (extend existing file)

- [ ] **Step 1: Write failing integration test**

  Append to `tests/test_privacy.py`:

  ```python
  # ---------------------------------------------------------------------------
  # Handler integration — format_person_detail
  # ---------------------------------------------------------------------------

  class TestFormatPersonDetailPrivacy:
      async def test_living_person_name_redacted(self, monkeypatch, sqlite_client):
          monkeypatch.setenv("GRAMPS_PRIVACY_MODE", "true")
          from gramps_mcp.handlers.person_detail_handler import format_person_detail
          result = await format_person_detail(sqlite_client, "default", "h_pe_jane")
          assert "[Living]" in result

      async def test_living_person_birth_date_not_shown(self, monkeypatch, sqlite_client):
          monkeypatch.setenv("GRAMPS_PRIVACY_MODE", "true")
          from gramps_mcp.handlers.person_detail_handler import format_person_detail
          result = await format_person_detail(sqlite_client, "default", "h_pe_jane")
          assert "Born:" not in result

      async def test_deceased_person_not_redacted(self, monkeypatch, sqlite_client):
          monkeypatch.setenv("GRAMPS_PRIVACY_MODE", "true")
          from gramps_mcp.handlers.person_detail_handler import format_person_detail
          result = await format_person_detail(sqlite_client, "default", "h_pe_john")
          assert "John" in result
  ```

- [ ] **Step 2: Run test to verify it fails**

  ```
  uv run pytest tests/test_privacy.py::TestFormatPersonDetailPrivacy -v
  ```
  Expected: FAIL.

- [ ] **Step 3: Add import and redact call to person_detail_handler.py**

  In `src/gramps_mcp/handlers/person_detail_handler.py`, add import after existing imports:

  ```python
  from ..privacy import redact_if_living
  ```

  In `format_person_detail()`, after the `person_data = await client.make_api_call(...)` call (line 29), add:

  ```python
  person_data = await redact_if_living(person_data, client, tree_id)
  ```

  The block should look like:

  ```python
  person_data = await client.make_api_call(
      ApiCalls.GET_PERSON, tree_id=tree_id, handle=handle, params={"extend": "all"}
  )
  person_data = await redact_if_living(person_data, client, tree_id)
  ```

- [ ] **Step 4: Run tests to verify they pass**

  ```
  uv run pytest tests/test_privacy.py::TestFormatPersonDetailPrivacy -v
  ```
  Expected: all 3 tests pass.

- [ ] **Step 5: Run broader test suite**

  ```
  uv run pytest tests/test_sqlite_detail_tools.py tests/test_search_details.py -v
  ```
  Expected: all pass.

- [ ] **Step 6: Commit**

  ```bash
  git add src/gramps_mcp/handlers/person_detail_handler.py tests/test_privacy.py
  git commit -m "feat: redact living persons in format_person_detail"
  ```

---

## Task 5: Hook family_detail_handler.py

This handler has three places where person data is formatted: father, mother, and inside
`_get_birth_death_dates()` (called for father, mother, and each child).

**Files:**
- Modify: `src/gramps_mcp/handlers/family_detail_handler.py`
- Test: `tests/test_privacy.py` (extend existing file)

- [ ] **Step 1: Write failing integration test**

  Append to `tests/test_privacy.py`:

  ```python
  # ---------------------------------------------------------------------------
  # Handler integration — format_family_detail
  # ---------------------------------------------------------------------------

  class TestFormatFamilyDetailPrivacy:
      async def test_living_mother_name_redacted(self, monkeypatch, sqlite_client):
          """Smith family (h_fa_smith): mother Jane (living) must show [Living]."""
          monkeypatch.setenv("GRAMPS_PRIVACY_MODE", "true")
          from gramps_mcp.handlers.family_detail_handler import format_family_detail
          result = await format_family_detail(sqlite_client, "default", "h_fa_smith")
          assert "[Living]" in result

      async def test_deceased_father_not_redacted(self, monkeypatch, sqlite_client):
          """Father John (deceased) must still show his name."""
          monkeypatch.setenv("GRAMPS_PRIVACY_MODE", "true")
          from gramps_mcp.handlers.family_detail_handler import format_family_detail
          result = await format_family_detail(sqlite_client, "default", "h_fa_smith")
          assert "John" in result

      async def test_privacy_off_shows_all_names(self, monkeypatch, sqlite_client):
          monkeypatch.delenv("GRAMPS_PRIVACY_MODE", raising=False)
          from gramps_mcp.handlers.family_detail_handler import format_family_detail
          result = await format_family_detail(sqlite_client, "default", "h_fa_smith")
          assert "Jane" in result
  ```

- [ ] **Step 2: Run test to verify it fails**

  ```
  uv run pytest tests/test_privacy.py::TestFormatFamilyDetailPrivacy -v
  ```
  Expected: FAIL — Jane's name shows normally even in privacy mode.

- [ ] **Step 3: Add import to family_detail_handler.py**

  In `src/gramps_mcp/handlers/family_detail_handler.py`, add import after existing imports:

  ```python
  from ..privacy import redact_if_living
  ```

- [ ] **Step 4: Redact father and mother in format_family_detail()**

  Find the father block (around line 52):
  ```python
  father = extended.get("father", {})
  if father:
      father_name = _extract_person_name(father)
  ```

  Replace with:
  ```python
  father = await redact_if_living(extended.get("father", {}), client, tree_id)
  if father:
      father_name = _extract_person_name(father)
  ```

  Find the mother block (around line 63):
  ```python
  mother = extended.get("mother", {})
  if mother:
      mother_name = _extract_person_name(mother)
  ```

  Replace with:
  ```python
  mother = await redact_if_living(extended.get("mother", {}), client, tree_id)
  if mother:
      mother_name = _extract_person_name(mother)
  ```

  Find the children loop (around line 87):
  ```python
  for child in children:
      child_name = _extract_person_name(child)
  ```

  Replace with:
  ```python
  for child in children:
      child = await redact_if_living(child, client, tree_id)
      child_name = _extract_person_name(child)
  ```

- [ ] **Step 5: Redact inside _get_birth_death_dates()**

  Find the `full_person_data` assignment inside `_get_birth_death_dates()` (around line 277):
  ```python
  full_person_data = await client.make_api_call(
      ApiCalls.GET_PERSON,
      tree_id=tree_id,
      handle=person_handle,
      params={"extend": "all"},
  )
  ```

  Add one line after it:
  ```python
  full_person_data = await redact_if_living(full_person_data, client, tree_id)
  ```

- [ ] **Step 6: Run tests to verify they pass**

  ```
  uv run pytest tests/test_privacy.py::TestFormatFamilyDetailPrivacy -v
  ```
  Expected: all 3 tests pass.

- [ ] **Step 7: Run full privacy test suite**

  ```
  uv run pytest tests/test_privacy.py -v
  ```
  Expected: all tests pass.

- [ ] **Step 8: Run broader regression check**

  ```
  uv run pytest tests/test_sqlite_detail_tools.py tests/test_sqlite_integration.py tests/test_sqlite_client.py -v
  ```
  Expected: all pass.

- [ ] **Step 9: Commit**

  ```bash
  git add src/gramps_mcp/handlers/family_detail_handler.py tests/test_privacy.py
  git commit -m "feat: redact living persons in format_family_detail"
  ```

---

## Self-Review Notes

**Spec gap addressed in plan:** The spec lists `event_ref_list → []` for redaction.
However, `person_handler.py` uses `birth_ref_index` and `death_ref_index` (not
`event_ref_list`) to show Born/Died lines. `redact_person()` therefore also clears
these indices to prevent birth/death date leakage. This is reflected in Task 2's
implementation and tests (`test_birth_death_indices_cleared`).

**Not covered:** URLs on person records (not in approved spec scope; YAGNI).

**Web backend:** `is_living()` delegates to `GET_LIVING` for web clients. No automated
test exists for this path (requires a live web server). Manual verification needed
when using web backend.
