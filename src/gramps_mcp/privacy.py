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
