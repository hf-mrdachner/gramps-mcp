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

"""
MCP server main entry point with HTTP transport.

This module provides the FastAPI application and MCP server setup with
all 23 genealogy tools for Gramps Web API integration.
"""

import asyncio
import logging
import os
import sys
from typing import Any, Dict, Optional

from mcp.server import Server
from mcp.server.fastmcp import FastMCP
from mcp.server.stdio import stdio_server
from mcp.types import Tool
from pydantic import BaseModel, Field

# Import all parameter models
from .models.parameters.citation_params import CitationData
from .models.parameters.delete_params import DeleteObjectParams
from .models.parameters.link_edit_params import (
    AddChildToFamilyParams,
    AddCitationToEventParams,
    AddEventToFamilyParams,
    AddEventToPersonParams,
    AddNoteToFamilyParams,
    AddNoteToPersonParams,
    MoveAttachmentParams,
    RemoveChildFromFamilyParams,
    RemoveCitationFromEventParams,
    RemoveEventFromFamilyParams,
    RemoveEventFromPersonParams,
    RemoveNoteFromFamilyParams,
    RemoveNoteFromPersonParams,
)
from .models.parameters.dna_params import (
    AddDnaMatchParams,
    GetDnaMatchesParams,
    UpdateDnaMatchParams,
)
from .models.parameters.event_params import EventSaveParams
from .models.parameters.family_params import FamilySaveParams
from .models.parameters.media_params import MediaSaveParams
from .models.parameters.merge_params import (
    FindDuplicatesParams,
    MergePersonsParams,
    SplitPersonParams,
)
from .models.parameters.note_params import NoteSaveParams
from .models.parameters.people_params import PersonData
from .models.parameters.place_params import PlaceSaveParams
from .models.parameters.repository_params import RepositoryData
from .models.parameters.simple_params import (
    SimpleFindParams,
    SimpleGetParams,
    SimpleSearchParams,
)
from .models.parameters.source_params import SourceSaveParams
from .models.parameters.transactions_params import TransactionHistoryParams

# Import all tool functions
from .tools import (
    close_database_tool,
    create_citation_tool,
    create_event_tool,
    create_family_tool,
    create_media_tool,
    create_note_tool,
    create_person_tool,
    create_place_tool,
    create_repository_tool,
    create_source_tool,
    find_anything_tool,
    find_duplicate_persons_tool,
    get_ancestors_tool,
    get_descendants_tool,
    get_recent_changes_tool,
    get_tree_info_tool,
    list_databases_tool,
    merge_persons_tool,
    open_database_tool,
    split_person_tool,
)
from .tools.delete import delete_object_tool
from .tools.link_edit import (
    add_child_to_family_tool,
    add_event_to_family_tool,
    add_event_to_person_tool,
    move_attachment_tool,
    remove_child_from_family_tool,
    remove_event_from_family_tool,
    remove_event_from_person_tool,
)
from .tools.citation_link import (
    add_citation_to_event_tool,
    remove_citation_from_event_tool,
)
from .tools.note_link import (
    add_note_to_family_tool,
    add_note_to_person_tool,
    remove_note_from_family_tool,
    remove_note_from_person_tool,
)
from .tools.dna import (
    add_dna_match_tool,
    get_dna_matches_tool,
    update_dna_match_tool,
)
from .tools.search_basic import find_type_tool
from .tools.search_details import (
    find_duplicate_citations_tool,
    find_duplicate_events_tool,
    get_citation_tool,
    get_event_tool,
    get_family_tool,
    get_note_tool,
    get_person_tool,
    get_place_tool,
    get_type_tool,
    merge_citations_tool,
    merge_events_tool,
    merge_families_tool,
    merge_places_tool,
)
from .tools.wikitree_export import prepare_biography_tool
from .client import get_client
from .gramps_id import resolve_handles


# Simple analysis models for tools that use direct dict access
class TreeInfoParams(BaseModel):
    include_statistics: bool = Field(True, description="Include statistics")


class OpenDatabaseParams(BaseModel):
    path: str = Field(
        description=(
            "Absolute path to the Gramps database. "
            "Accepted formats: "
            ".sqlite or .db file (read/write, live DB), "
            "directory containing sqlite.db (read/write), "
            ".gpkg or .gramps file (read-only)."
        )
    )


class EmptyParams(BaseModel):
    """No parameters required."""


class DescendantsParams(BaseModel):
    gramps_id: str = Field(..., description="Person ID")
    max_generations: Optional[int] = Field(
        5,
        description=(
            "Max generations to retrieve (default: 5, use higher values "
            "carefully as they can overflow context)"
        ),
    )


class AncestorsParams(BaseModel):
    gramps_id: str = Field(..., description="Person ID")
    max_generations: Optional[int] = Field(
        5,
        description=(
            "Max generations to retrieve (default: 5, use higher values "
            "carefully as they can overflow context)"
        ),
    )


# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class GetPersonParams(BaseModel):
    gramps_id: str = Field(..., description="Gramps person ID (e.g. 'I0001')")


class GetEventParams(BaseModel):
    gramps_id: str = Field(..., description="Gramps event ID (e.g. 'E0001')")


class GetFamilyParams(BaseModel):
    gramps_id: str = Field(..., description="Gramps family ID (e.g. 'F0001')")


class GetPlaceParams(BaseModel):
    gramps_id: str = Field(..., description="Gramps place ID (e.g. 'P0001')")


class GetNoteParams(BaseModel):
    gramps_id: str = Field(..., description="Gramps note ID (e.g. 'N0001')")


class GetCitationParams(BaseModel):
    gramps_id: str = Field(..., description="Gramps citation ID (e.g. 'C0001')")


class MergePlacesParams(BaseModel):
    winner_id: str = Field(..., description="Gramps place ID to keep (e.g. 'P0001')")
    loser_id: str = Field(..., description="Gramps place ID to absorb into winner")
    dry_run: bool = Field(
        True, description="If True (default), show planned changes without writing"
    )


class MergeFamiliesParams(BaseModel):
    winner_id: str = Field(..., description="Gramps family ID to keep (e.g. 'F0001')")
    loser_id: str = Field(..., description="Gramps family ID to absorb into winner")
    dry_run: bool = Field(
        True, description="If True (default), show planned changes without writing"
    )


class MergeEventsParams(BaseModel):
    winner_id: str = Field(..., description="Gramps event ID to keep (e.g. 'E0001')")
    loser_id: str = Field(
        ...,
        description="Gramps event ID to absorb; its citations are transferred to winner",
    )
    dry_run: bool = Field(
        True, description="If True (default), show planned changes without writing"
    )


class MergeCitationsParams(BaseModel):
    winner_id: str = Field(..., description="Gramps citation ID to keep (e.g. 'C0001')")
    loser_id: str = Field(..., description="Gramps citation ID to absorb into winner")
    dry_run: bool = Field(
        True, description="If True (default), show planned changes without writing"
    )


class FindDuplicateCitationsParams(BaseModel):
    max_results: int = Field(
        50, description="Maximum number of duplicate groups to show"
    )
    source_filter: Optional[str] = Field(
        None, description="Filter by source title substring (case-insensitive)"
    )


class FindDuplicateEventsParams(BaseModel):
    max_results: int = Field(
        50, description="Maximum number of duplicate groups to show", ge=1, le=500
    )
    gramps_id: Optional[str] = Field(
        None, description="Limit scan to one person by Gramps ID (e.g. 'I0001')"
    )


class PrepareBiographyParams(BaseModel):
    gramps_id: str = Field(..., description="Gramps person ID (e.g. 'I0001')")
    flavor: str = Field("wikitree", description="Target platform format: 'wikitree'")
    language: str = Field("en", description="Output language: 'en'")
    include_events: Optional[list] = Field(
        None,
        description="Event types to include, e.g. ['Birth','Death']. Default: all standard events.",
    )


async def _handle_remove_event_from_family(args: Dict) -> Any:
    """Handler for remove_event_from_family."""
    return await remove_event_from_family_tool(
        family_handle=args.get("family_handle"),
        event_handle=args.get("event_handle"),
        family_gramps_id=args.get("family_gramps_id"),
        event_gramps_id=args.get("event_gramps_id"),
    )


async def _handle_add_event_to_family(args: Dict) -> Any:
    """Handler for add_event_to_family."""
    return await add_event_to_family_tool(
        family_handle=args.get("family_handle"),
        event_handle=args.get("event_handle"),
        family_gramps_id=args.get("family_gramps_id"),
        event_gramps_id=args.get("event_gramps_id"),
        role=args.get("role", "Family"),
    )


async def _handle_add_event_to_person(args: Dict) -> Any:
    """Handler for add_event_to_person."""
    return await add_event_to_person_tool(
        person_handle=args.get("person_handle"),
        event_handle=args.get("event_handle"),
        person_gramps_id=args.get("person_gramps_id"),
        event_gramps_id=args.get("event_gramps_id"),
        role=args.get("role", "Primary"),
    )


async def _handle_remove_event_from_person(args: Dict) -> Any:
    """Handler for remove_event_from_person."""
    return await remove_event_from_person_tool(
        person_handle=args.get("person_handle"),
        event_handle=args.get("event_handle"),
        person_gramps_id=args.get("person_gramps_id"),
        event_gramps_id=args.get("event_gramps_id"),
    )


async def _handle_add_citation_to_event(args: Dict) -> Any:
    """Handler for add_citation_to_event."""
    return await add_citation_to_event_tool(
        event_handle=args.get("event_handle"),
        event_gramps_id=args.get("event_gramps_id"),
        citation_handle=args.get("citation_handle"),
        citation_gramps_id=args.get("citation_gramps_id"),
    )


async def _handle_remove_citation_from_event(args: Dict) -> Any:
    """Handler for remove_citation_from_event."""
    return await remove_citation_from_event_tool(
        event_handle=args.get("event_handle"),
        event_gramps_id=args.get("event_gramps_id"),
        citation_handle=args.get("citation_handle"),
        citation_gramps_id=args.get("citation_gramps_id"),
    )


async def _handle_add_note_to_person(args: Dict) -> Any:
    """Handler for add_note_to_person."""
    return await add_note_to_person_tool(
        person_handle=args.get("person_handle"),
        person_gramps_id=args.get("person_gramps_id"),
        note_handle=args.get("note_handle"),
        note_gramps_id=args.get("note_gramps_id"),
        text=args.get("text"),
        type=args.get("type"),
    )


async def _handle_add_note_to_family(args: Dict) -> Any:
    """Handler for add_note_to_family."""
    return await add_note_to_family_tool(
        family_handle=args.get("family_handle"),
        family_gramps_id=args.get("family_gramps_id"),
        note_handle=args.get("note_handle"),
        note_gramps_id=args.get("note_gramps_id"),
        text=args.get("text"),
        type=args.get("type"),
    )


async def _handle_remove_note_from_person(args: Dict) -> Any:
    """Handler for remove_note_from_person."""
    return await remove_note_from_person_tool(
        person_handle=args.get("person_handle"),
        person_gramps_id=args.get("person_gramps_id"),
        note_handle=args.get("note_handle"),
        note_gramps_id=args.get("note_gramps_id"),
    )


async def _handle_remove_note_from_family(args: Dict) -> Any:
    """Handler for remove_note_from_family."""
    return await remove_note_from_family_tool(
        family_handle=args.get("family_handle"),
        family_gramps_id=args.get("family_gramps_id"),
        note_handle=args.get("note_handle"),
        note_gramps_id=args.get("note_gramps_id"),
    )


async def _handle_remove_child_from_family(args: Dict) -> Any:
    """Handler for remove_child_from_family with gramps_id resolution."""
    args = await resolve_handles(
        args, {"family_handle": "family", "child_handle": "person"}, get_client()
    )
    return await remove_child_from_family_tool(
        family_handle=args["family_handle"],
        child_handle=args["child_handle"],
    )


async def _handle_add_child_to_family(args: Dict) -> Any:
    """Handler for add_child_to_family with gramps_id resolution."""
    args = await resolve_handles(
        args, {"family_handle": "family", "child_handle": "person"}, get_client()
    )
    return await add_child_to_family_tool(
        family_handle=args["family_handle"],
        child_handle=args["child_handle"],
        frel=args.get("frel", "Birth"),
        mrel=args.get("mrel", "Birth"),
    )


async def _handle_move_attachment(args: Dict) -> Any:
    """Handler for move_attachment with gramps_id resolution."""
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


# Tool registry - single source of truth for all tools
TOOL_REGISTRY: Dict[str, Dict[str, Any]] = {
    # Search & Retrieval Tools
    "find_type": {
        "description": (
            "Search any entity type using GQL - read gql://documentation "
            "resource first to understand syntax"
        ),
        "schema": SimpleFindParams,
        "handler": find_type_tool,
    },
    "find_anything": {
        "description": (
            "Text search across all record types - matches literal text "
            "within records, not logical combinations"
        ),
        "schema": SimpleSearchParams,
        "handler": find_anything_tool,
    },
    "get_type": {
        "description": "Get full details for person or family by handle or gramps_id",
        "schema": SimpleGetParams,
        "handler": get_type_tool,
    },
    "get_person": {
        "description": (
            "Get full person details by gramps_id: name, birth, death, "
            "parents, siblings, spouse(s), and children with dates. "
            "Use this to navigate the family tree around a known person."
        ),
        "schema": GetPersonParams,
        "handler": get_person_tool,
    },
    "get_family": {
        "description": (
            "Get full family details by gramps_id: parents with birth/death dates, "
            "children with dates and relationship types, marriage event, timeline, "
            "notes, and media. Use this to inspect a specific family unit."
        ),
        "schema": GetFamilyParams,
        "handler": get_family_tool,
    },
    "get_event": {
        "description": (
            "Get event details by gramps_id (type, date, place, citations) "
            "and find which persons have this event attached. "
            "Use this to navigate from an event back to the person(s) involved."
        ),
        "schema": GetEventParams,
        "handler": get_event_tool,
    },
    "get_place": {
        "description": (
            "Get place details (type, coordinates, URLs) and all events with persons "
            "at this place, sorted by date. Use this to see everyone connected to a "
            "location and to identify duplicate place records."
        ),
        "schema": GetPlaceParams,
        "handler": get_place_tool,
    },
    "get_note": {
        "description": (
            "Get full note text by gramps_id and find which persons/families have this "
            "note linked. Scans all persons and families — fact-based, no guessing. "
            "Notes attached only to other object types (events, citations, sources, "
            "places, media) are not found by this scan."
        ),
        "schema": GetNoteParams,
        "handler": get_note_tool,
    },
    "get_citation": {
        "description": (
            "Get citation details: page, date, confidence, source title, and "
            "attributes (including _APID, used by Ancestry-style record resolution). "
            "Also finds which persons/families/events reference this citation. "
            "Scans all persons/families/events — fact-based, no guessing."
        ),
        "schema": GetCitationParams,
        "handler": get_citation_tool,
    },
    "merge_places": {
        "description": (
            "Merge a duplicate place (loser) into a canonical place (winner) by "
            "redirecting all events from loser to winner. "
            "dry_run=True (default) shows planned changes without writing. "
            "Set dry_run=False to apply. Winner place keeps its data; loser is deleted."
        ),
        "schema": MergePlacesParams,
        "handler": merge_places_tool,
    },
    "merge_families": {
        "description": (
            "Merge a duplicate family (loser) into a canonical family (winner). "
            "Updates all person family_list and parent_family_list references, "
            "then deletes the loser family. dry_run=True (default) shows planned changes."
        ),
        "schema": MergeFamiliesParams,
        "handler": merge_families_tool,
    },
    "merge_events": {
        "description": (
            "Merge a duplicate event (loser) into a canonical event (winner). "
            "Transfers all citations from loser to winner (no data loss), "
            "removes loser from all person event_ref_lists, then deletes loser. "
            "dry_run=True (default) shows planned changes without writing."
        ),
        "schema": MergeEventsParams,
        "handler": merge_events_tool,
    },
    "merge_citations": {
        "description": (
            "Merge a duplicate citation (loser) into a canonical citation (winner). "
            "Redirects all event citation references from loser to winner, then deletes loser. "
            "dry_run=True (default) shows planned changes without writing."
        ),
        "schema": MergeCitationsParams,
        "handler": merge_citations_tool,
    },
    "find_duplicate_citations": {
        "description": (
            "Find duplicate citations grouped by (source, page). "
            "Two citations are duplicates when they reference the same source at the same page. "
            "Use source_filter to narrow results to a specific collection. "
            "Returns candidate groups for merge_citations."
        ),
        "schema": FindDuplicateCitationsParams,
        "handler": find_duplicate_citations_tool,
    },
    "find_duplicate_events": {
        "description": (
            "Find persons who have duplicate events: same type and date recorded more than once. "
            "Use gramps_id to scan a single person, or omit to scan the whole tree. "
            "Returns grouped candidates for merge_events."
        ),
        "schema": FindDuplicateEventsParams,
        "handler": find_duplicate_events_tool,
    },
    # Data Management Tools
    "create_person": {
        "description": (
            "Create or update person information including family links "
            "and event associations"
        ),
        "schema": PersonData,
        "handler": create_person_tool,
    },
    "create_family": {
        "description": "Create or update family unit including member relationships",
        "schema": FamilySaveParams,
        "handler": create_family_tool,
    },
    "create_event": {
        "description": (
            "Create or update life event including person/place associations"
        ),
        "schema": EventSaveParams,
        "handler": create_event_tool,
    },
    "create_place": {
        "description": "Create or update geographic location",
        "schema": PlaceSaveParams,
        "handler": create_place_tool,
    },
    "create_source": {
        "description": "Create or update source document",
        "schema": SourceSaveParams,
        "handler": create_source_tool,
    },
    "create_citation": {
        "description": "Create or update citation including object associations",
        "schema": CitationData,
        "handler": create_citation_tool,
    },
    "create_note": {
        "description": "Create or update textual note including object associations",
        "schema": NoteSaveParams,
        "handler": create_note_tool,
    },
    "create_media": {
        "description": "Create or update media files including object associations",
        "schema": MediaSaveParams,
        "handler": create_media_tool,
    },
    "create_repository": {
        "description": "Create or update repository information",
        "schema": RepositoryData,
        "handler": create_repository_tool,
    },
    # Analysis Tools
    "tree_stats": {
        "description": (
            "Get information about a specific tree including statistics "
            "(counts of people, families, events, etc.)"
        ),
        "schema": TreeInfoParams,
        "handler": get_tree_info_tool,
    },
    "get_descendants": {
        "description": (
            "Find all descendants of a person - WARNING: Very token-heavy "
            "operation, minimize generations (default: 5)"
        ),
        "schema": DescendantsParams,
        "handler": get_descendants_tool,
    },
    "get_ancestors": {
        "description": (
            "Find all ancestors of a person - WARNING: Very token-heavy "
            "operation, minimize generations (default: 5)"
        ),
        "schema": AncestorsParams,
        "handler": get_ancestors_tool,
    },
    "recent_changes": {
        "description": "Get recent changes/modifications to the family tree",
        "schema": TransactionHistoryParams,
        "handler": get_recent_changes_tool,
    },
    # Database lifecycle
    "list_databases": {
        "description": (
            "List all Gramps databases (family trees) found on this machine. "
            "Shows name, backend type, path, and whether read/write access "
            "is available. Use open_database with the shown path to connect."
        ),
        "schema": EmptyParams,
        "handler": list_databases_tool,
    },
    "open_database": {
        "description": (
            "Open a Gramps database file or directory. "
            "SQLite databases support full read/write; "
            ".gpkg/.gramps files are read-only. "
            "Replaces any currently open database."
        ),
        "schema": OpenDatabaseParams,
        "handler": open_database_tool,
    },
    "close_database": {
        "description": (
            "Close the current database connection and release the lock file. "
            "Call this before opening the database in Gramps Desktop to avoid "
            "conflicts. Call open_database again when you want to reconnect."
        ),
        "schema": EmptyParams,
        "handler": close_database_tool,
    },
    # Merge tools (SQLite backend only)
    "find_duplicate_persons": {
        "description": (
            "Scan the genealogy database for duplicate person records. "
            "Compares all persons by name (with German umlaut normalisation), "
            "birth year, suffix (Roman numerals), gender, and shared relatives. "
            "Returns ranked candidate pairs — review before merging. "
            "SQLite backend only."
        ),
        "schema": FindDuplicatesParams,
        "handler": find_duplicate_persons_tool,
    },
    "merge_persons": {
        "description": (
            "Merge a duplicate person (loser) into another (winner). "
            "Combines events, citations, notes and family links. "
            "Duplicate events (same type, date, place) are deduplicated. "
            "Set dry_run=False to apply; a backup is saved for undo. "
            "SQLite backend only."
        ),
        "schema": MergePersonsParams,
        "handler": merge_persons_tool,
    },
    "split_person": {
        "description": (
            "Undo a previous merge_persons call by recreating the absorbed person. "
            "Reads the merge backup file. The winner record is not modified "
            "automatically. SQLite backend only."
        ),
        "schema": SplitPersonParams,
        "handler": split_person_tool,
    },
    # Export Tools
    "prepare_biography": {
        "description": (
            "Generate export biography markup for a person from their Gramps events and "
            "citations. flavor='wikitree' produces WikiTree-ready markup with structured "
            "<ref> citations. Events without citations are flagged {{Unsourced}}. "
            "SQLite backend only. "
            "IMPORTANT: Always present the generated biography to the user for review "
            "and explicit approval before passing it to wikitree_edit_person. "
            "Check for: factual accuracy (dates, places, names), natural language "
            "(no repeated full name, correct prepositions), and complete source coverage. "
            "Never post to WikiTree without user confirmation."
        ),
        "schema": PrepareBiographyParams,
        "handler": prepare_biography_tool,
    },
    # DNA tools (SQLite backend only)
    "add_dna_match": {
        "description": (
            "Record a DNA match between two persons. "
            "Creates a PersonRef with rel='DNA' and a Note in the Gramps DNA "
            "Segment Map format. Requires both persons to exist. "
            "SQLite backend only."
        ),
        "schema": AddDnaMatchParams,
        "handler": add_dna_match_tool,
    },
    "get_dna_matches": {
        "description": (
            "List all DNA matches recorded for a person. "
            "Returns shared cM, relationship, side, source, and "
            "chromosome-level segments. SQLite backend only."
        ),
        "schema": GetDnaMatchesParams,
        "handler": get_dna_matches_tool,
    },
    "update_dna_match": {
        "description": (
            "Update an existing DNA match. "
            "Merges provided fields into the existing match and replaces the note. "
            "Use to add or replace chromosome-level segments on a summary-only match. "
            "SQLite backend only."
        ),
        "schema": UpdateDnaMatchParams,
        "handler": update_dna_match_tool,
    },
    "delete_object": {
        "description": (
            "Delete a Gramps object and cascade-clean all references. "
            "Use confirmed=False first to see a dry-run summary of what "
            "will be deleted, then call again with confirmed=True to execute. "
            "Orphaned events (owned exclusively by the deleted person/family) "
            "are automatically deleted. SQLite backend only."
        ),
        "schema": DeleteObjectParams,
        "handler": delete_object_tool,
    },
    "add_event_to_family": {
        "description": (
            "Append an event reference to a family's event_ref_list without replacing it. "
            "Use this instead of create_family when you want to add a single event and "
            "preserve existing event links. SQLite backend only."
        ),
        "schema": AddEventToFamilyParams,
        "handler": _handle_add_event_to_family,
    },
    "remove_event_from_family": {
        "description": (
            "Remove an event reference from a family's event_ref_list. "
            "Does not delete the event object itself — use delete_object for that. "
            "SQLite backend only."
        ),
        "schema": RemoveEventFromFamilyParams,
        "handler": _handle_remove_event_from_family,
    },
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
    "remove_event_from_person": {
        "description": (
            "Remove an event reference from a person's event_ref_list. "
            "Automatically recalculates birth_ref_index and death_ref_index after removal. "
            "Does not delete the event object itself — use delete_object for that. "
            "SQLite backend only."
        ),
        "schema": RemoveEventFromPersonParams,
        "handler": _handle_remove_event_from_person,
    },
    "add_citation_to_event": {
        "description": (
            "Add a citation to an existing event's citation_list without replacing it. "
            "Idempotent: adding an already-linked citation returns result='no_change'. "
            "Use this instead of create_event when you only want to attach a citation. "
            "SQLite backend only."
        ),
        "schema": AddCitationToEventParams,
        "handler": _handle_add_citation_to_event,
    },
    "remove_citation_from_event": {
        "description": (
            "Remove a citation from an existing event's citation_list. "
            "Raises an error if the citation is not in the list. "
            "SQLite backend only."
        ),
        "schema": RemoveCitationFromEventParams,
        "handler": _handle_remove_citation_from_event,
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
    "add_child_to_family": {
        "description": (
            "Add a child to an existing family without replacing the existing "
            "child_ref_list. Unlike create_family(child_gramps_ids=[...]), which "
            "replaces the whole list, this only appends — use it to add one child "
            "to a family that already has other children/events without needing to "
            "re-supply everything else. Both the family's child_ref_list and the "
            "child person's parent_family_list are updated in a single transaction. "
            "SQLite backend only."
        ),
        "schema": AddChildToFamilyParams,
        "handler": _handle_add_child_to_family,
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
    "add_note_to_person": {
        "description": (
            "Link a note to a person. Three modes: pass note_handle/note_gramps_id alone "
            "to link an existing note; pass text+type alone to create a new note and link "
            "it; pass note_handle/note_gramps_id together with text and/or type to "
            "overwrite the existing note's content and ensure it's linked. Idempotent "
            "link-only calls return result='no_change'. SQLite backend only."
        ),
        "schema": AddNoteToPersonParams,
        "handler": _handle_add_note_to_person,
    },
    "remove_note_from_person": {
        "description": (
            "Remove a note from a person's note_list. Does not delete the Note object "
            "itself — use delete_object for that. SQLite backend only."
        ),
        "schema": RemoveNoteFromPersonParams,
        "handler": _handle_remove_note_from_person,
    },
    "add_note_to_family": {
        "description": (
            "Link a note to a family. Same three modes as add_note_to_person: link "
            "existing, create+link, or update+link. SQLite backend only."
        ),
        "schema": AddNoteToFamilyParams,
        "handler": _handle_add_note_to_family,
    },
    "remove_note_from_family": {
        "description": (
            "Remove a note from a family's note_list. Does not delete the Note object "
            "itself — use delete_object for that. SQLite backend only."
        ),
        "schema": RemoveNoteFromFamilyParams,
        "handler": _handle_remove_note_from_family,
    },
}


# Tool groups for gramps://tools/<group> resources
TOOL_GROUPS: dict[str, list[str]] = {
    "person": [
        "create_person",
        "get_person",
        "merge_persons",
        "split_person",
        "find_duplicate_persons",
        "add_dna_match",
        "get_dna_matches",
        "update_dna_match",
        "add_note_to_person",
        "remove_note_from_person",
        "get_note",
    ],
    "event": [
        "create_event",
        "get_event",
        "add_event_to_person",
        "remove_event_from_person",
        "add_event_to_family",
        "remove_event_from_family",
    ],
    "citation": [
        "create_citation",
        "get_citation",
        "create_source",
        "create_repository",
        "add_citation_to_event",
        "remove_citation_from_event",
    ],
    "family": [
        "create_family",
        "get_family",
        "merge_families",
        "add_child_to_family",
        "remove_child_from_family",
        "add_event_to_family",
        "remove_event_from_family",
        "add_note_to_family",
        "remove_note_from_family",
        "get_note",
    ],
    "search": [
        "find_anything",
        "find_type",
        "get_type",
        "get_ancestors",
        "get_descendants",
        "tree_stats",
        "recent_changes",
        "find_duplicate_events",
        "find_duplicate_citations",
    ],
    "admin": [
        "list_databases",
        "open_database",
        "close_database",
    ],
}


# Create FastMCP app with stateless HTTP (no SSE)
app = FastMCP(
    "gramps-genealogy",
    stateless_http=True,
    json_response=True,
    instructions=(
        "Gramps genealogy database — SQLite backend.\n\n"
        "Load a tool-group resource before working in a domain:\n\n"
        "  gramps://tools/person    — create/get/merge/split persons, DNA, notes\n"
        "  gramps://tools/event     — create/get events, add/remove event↔person links\n"
        "  gramps://tools/citation  — create citations/sources, add/remove citation↔event links\n"
        "  gramps://tools/family    — create/get/merge families, child links, notes\n"
        "  gramps://tools/search    — find_anything, ancestors, descendants, tree stats\n"
        "  gramps://tools/admin     — open/close/list databases\n\n"
        "Before writing raw SQLite: always check if an MCP tool covers the operation."
    ),
)


# ============================================================================
# Dynamic FastMCP Tool Registration
# ============================================================================


# Register all tools dynamically from the registry
def register_tools():
    """Register all tools from the registry with FastMCP."""
    for tool_name, tool_config in TOOL_REGISTRY.items():
        schema = tool_config["schema"]
        handler_func = tool_config["handler"]
        description = tool_config["description"]

        # Create the async handler function with proper schema annotation
        async def create_handler(arguments, handler=handler_func):
            return await handler(arguments.model_dump())

        # Set proper metadata
        create_handler.__name__ = tool_name
        create_handler.__doc__ = description
        create_handler.__annotations__ = {"arguments": schema}

        # Register with FastMCP
        app.tool(description=description)(create_handler)


register_tools()


# ============================================================================
# Resource Management
# ============================================================================


def load_resource(filename: str) -> str:
    """Load content from resources folder with error handling."""
    try:
        # Get the path to the resources directory relative to this file
        current_dir = os.path.dirname(os.path.abspath(__file__))
        resource_path = os.path.join(current_dir, "resources", filename)

        with open(resource_path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return f"Resource file '{filename}' not found."
    except Exception as e:
        return f"Error loading resource '{filename}': {str(e)}"


@app.resource("gql://documentation")
def get_gql_documentation() -> str:
    """
    Complete GQL documentation, syntax, examples, and property
    reference for Gramps queries.
    """
    return load_resource("gql-documentation.md")


@app.resource("gramps://usage-guide")
def get_usage_guide() -> str:
    """
    IMPORTANT: Read this first before using ANY creation tools -
    explains proper genealogy workflow and tool usage order.
    """
    return load_resource("gramps-usage-guide.md")


def _generate_tool_group_resource(group_name: str) -> str:
    """Generate Markdown documentation for a named tool group."""
    tool_names = TOOL_GROUPS.get(group_name, [])
    lines = [f"# Gramps Tools — {group_name.title()}\n"]
    for name in tool_names:
        config = TOOL_REGISTRY.get(name)
        if not config:
            continue
        lines.append(f"### {name}")
        lines.append(config["description"])
        schema = config["schema"]
        lines.append("\n**Parameters:**")
        for field_name, field_info in schema.model_fields.items():
            req = "(required)" if field_info.is_required() else "(optional)"
            desc = field_info.description or ""
            lines.append(f"  - `{field_name}` {req}: {desc}")
        lines.append("")
    return "\n".join(lines)


@app.resource("gramps://tools/person")
def get_person_tools() -> str:
    """Person-related tools: create, get, merge, split, DNA."""
    return _generate_tool_group_resource("person")


@app.resource("gramps://tools/event")
def get_event_tools() -> str:
    """Event tools: create, get, add/remove event↔person links."""
    return _generate_tool_group_resource("event")


@app.resource("gramps://tools/citation")
def get_citation_tools() -> str:
    """Citation and source tools: create, add/remove citation↔event links."""
    return _generate_tool_group_resource("citation")


@app.resource("gramps://tools/family")
def get_family_tools() -> str:
    """Family tools: create, get, merge, child management."""
    return _generate_tool_group_resource("family")


@app.resource("gramps://tools/search")
def get_search_tools() -> str:
    """Search and analysis tools: find_anything, ancestors, descendants, stats."""
    return _generate_tool_group_resource("search")


@app.resource("gramps://tools/admin")
def get_admin_tools() -> str:
    """Database management tools: open, close, list databases."""
    return _generate_tool_group_resource("admin")


# Add custom routes to the FastMCP app
@app.custom_route("/", ["GET"])
async def root(request):
    """Root endpoint with server information."""
    from starlette.responses import JSONResponse

    return JSONResponse(
        {
            "service": "Gramps MCP Server",
            "version": "1.0.0",
            "description": "MCP server for Gramps Web API genealogy operations",
            "mcp_endpoint": "/mcp",
            "tools_count": len(TOOL_REGISTRY),
        }
    )


@app.custom_route("/health", ["GET"])
async def health_check(request):
    """Health check endpoint."""
    from starlette.responses import JSONResponse

    return JSONResponse(
        {
            "status": "healthy",
            "service": "Gramps MCP Server",
            "tools": len(TOOL_REGISTRY),
        }
    )


async def run_stdio_server():
    """Run the MCP server with stdio transport."""
    # Create a standard MCP server for stdio transport
    server = Server("gramps")

    @server.list_tools()
    async def handle_list_tools():
        """List all available tools."""
        return [
            Tool(
                name=tool_name,
                description=tool_config["description"],
                inputSchema=tool_config["schema"].model_json_schema(),
            )
            for tool_name, tool_config in TOOL_REGISTRY.items()
        ]

    @server.call_tool()
    async def handle_call_tool(name: str, arguments: dict):
        """Handle tool calls."""
        if name in TOOL_REGISTRY:
            return await TOOL_REGISTRY[name]["handler"](arguments)
        else:
            raise ValueError(f"Unknown tool: {name}")

    # Run the server with stdio transport
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream, write_stream, server.create_initialization_options()
        )


def main():
    """
    CLI entry point for the gramps-mcp binary.

    Usage:
        gramps-mcp           # HTTP transport on port 8000
        gramps-mcp stdio     # stdio transport (for Claude Desktop, Claude Code)
    """
    transport_type = sys.argv[1] if len(sys.argv) > 1 else "streamable-http"

    if transport_type == "stdio":
        asyncio.run(run_stdio_server())
    else:
        app.settings.host = "0.0.0.0"
        app.settings.port = 8000
        app.run(transport="streamable-http")


if __name__ == "__main__":
    main()
