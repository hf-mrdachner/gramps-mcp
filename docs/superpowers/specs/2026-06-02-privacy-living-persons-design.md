# Privacy / Living Persons Feature — Design Spec

**Date:** 2026-06-02
**Status:** Approved

## Problem

Gramps databases contain data about living persons. When gramps-mcp is used as an
MCP server for an AI assistant, all tool responses are forwarded to the AI provider.
This means personal data of living persons (names, birth dates, addresses, notes,
photos) can leave the local system without any data-protection controls.

## Goal

A global, opt-in privacy mode that redacts sensitive fields of living persons before
any data leaves the system via MCP tool responses. Write operations are unaffected —
the filter is an outbound data barrier only.

## Scope

- Read operations only (tool responses)
- All three backends: Web API, SQLite, XML/gpkg
- Activated globally via environment variable — no per-call opt-in/opt-out

## Out of Scope

- Write/edit operations (data comes *in*, not out)
- Non-person objects (places, sources, citations, etc.)
- Upstream PR — this is a fork-only feature for now

---

## Configuration

New field in `Settings` (`config.py`):

```python
gramps_privacy_mode: bool = Field(
    False,
    description="When True, redact sensitive data for living persons in all tool responses.",
)
```

Loaded from environment variable `GRAMPS_PRIVACY_MODE` (default: `false`).

A helper `is_privacy_mode() -> bool` in `privacy.py` reads the settings so handlers
do not need to import config directly.

---

## Architecture

New module: `src/gramps_mcp/privacy.py`

Three public functions:

### `is_living(person_data: dict, client, tree_id: str) -> bool`

Determines whether a person should be treated as living.

**Web backend** (`use_direct_backend == False`):
- Calls existing `ApiCalls.GET_LIVING` endpoint — delegates to native Gramps logic.

**SQLite / XML backend** (`use_direct_backend == True`):
- Has death date → `False` (not living)
- No death date + birth year present + birth year > (current year − 120) → `True` (born within last 120 years, could still be alive)
- No death date + birth year present + birth year ≤ (current year − 120) → `False` (too old to realistically be alive)
- No death date + no birth year → `True` (conservative: protect when uncertain)

### `redact_person(person_data: dict) -> dict`

Returns a new dict with sensitive fields replaced:

| Field | Redacted value |
|---|---|
| `primary_name.first_name` | `"[Living]"` |
| `primary_name.surname_list` | `[{"surname": "[Living]"}]` |
| `event_ref_list` | `[]` |
| `note_list` | `[]` |
| `media_list` | `[]` |
| `living` | `True` (marker) |

Fields preserved: `handle`, `gramps_id`, `gender`, `family_list`,
`parent_family_list`.

### `redact_if_living(person_data: dict, client, tree_id: str) -> dict`

Combines the above:
1. If `is_privacy_mode()` is `False` → return `person_data` unchanged
2. If `is_living(person_data, client, tree_id)` is `False` → return `person_data` unchanged
3. Otherwise → return `redact_person(person_data)`

---

## Integration Points

The following handlers call `make_api_call(GET_PERSON, ...)` and format the result.
Each must call `redact_if_living()` immediately after fetching person data, before
any formatting:

| File | Location |
|---|---|
| `handlers/person_handler.py` | `format_person()` after GET_PERSON call |
| `handlers/person_detail_handler.py` | after GET_PERSON call |
| `handlers/family_detail_handler.py` | after each GET_PERSON call for family members |

No changes needed in tools, server, or client layers.

---

## Data Flow

```
Tool call → make_api_call(GET_PERSON) → raw person_data
    → redact_if_living(person_data, client, tree_id)
        → is_privacy_mode()? No → return raw
        → is_living()? No → return raw
        → redact_person() → redacted dict
    → format_person(redacted dict) → MCP response
```

---

## Testing

File: `tests/test_privacy.py`

All tests use the existing SQLite in-memory fixture (`conftest_sqlite.py`).
No mocks.

| Test | Scenario | Expected |
|---|---|---|
| `test_living_person_is_redacted` | No death date, birth year within 120 years, privacy mode on | Name and events redacted |
| `test_deceased_person_not_redacted` | Has death date, privacy mode on | Full data returned |
| `test_old_person_no_death_date_not_redacted` | Birth year ≤ (current year − 120), no death date | Not redacted |
| `test_privacy_mode_disabled_no_redaction` | Living person, `GRAMPS_PRIVACY_MODE=false` | Full data returned |
| `test_no_birth_year_is_redacted` | No birth date, no death date, privacy mode on | Redacted (conservative) |
