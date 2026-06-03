"""
Gramps-ID to handle resolution utilities.

All tools that accept a handle parameter can also accept the equivalent
gramps_id parameter. This module resolves gramps_ids to internal handles
for all three backends (SQLite, DirectClient, WebAPI).
"""
import logging
from typing import Dict, List

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
        handle        -> gramps_id
        father_handle -> father_gramps_id
        child_handles -> child_gramps_ids
        handle1       -> gramps_id1

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
            {"father_handle": "person", "source_handle": "source"}.
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
