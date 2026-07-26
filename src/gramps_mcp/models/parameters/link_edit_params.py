"""Pydantic parameter models for the three link-edit MCP tools."""

from typing import Literal, Optional

from pydantic import BaseModel, Field


class AddEventToPersonParams(BaseModel):
    """Parameters for add_event_to_person tool."""

    person_handle: Optional[str] = Field(
        None, description="Handle of the person to link the event to"
    )
    person_gramps_id: Optional[str] = Field(
        None,
        description="Gramps ID of the person (e.g. 'I0001'). Alternative to person_handle.",
    )
    event_handle: Optional[str] = Field(
        None, description="Handle of the event to link (must already exist in the DB)"
    )
    event_gramps_id: Optional[str] = Field(
        None,
        description="Gramps ID of the event (e.g. 'E0001'). Alternative to event_handle.",
    )
    role: str = Field(
        "Primary",
        description=(
            "Role of the person in the event. Common values: Primary, Witness, "
            "Godparent, Informant. Default: Primary."
        ),
    )


class AddEventToFamilyParams(BaseModel):
    """Parameters for add_event_to_family tool."""

    family_handle: Optional[str] = Field(
        None, description="Handle of the family to link the event to"
    )
    family_gramps_id: Optional[str] = Field(
        None,
        description="Gramps ID of the family (e.g. 'F0001'). Alternative to family_handle.",
    )
    event_handle: Optional[str] = Field(
        None, description="Handle of the event to link (must already exist in the DB)"
    )
    event_gramps_id: Optional[str] = Field(
        None,
        description="Gramps ID of the event (e.g. 'E0001'). Alternative to event_handle.",
    )
    role: str = Field(
        "Family",
        description=(
            "Role of the family in the event. Common values: Family, Primary. Default: Family."
        ),
    )


class RemoveEventFromFamilyParams(BaseModel):
    """Parameters for remove_event_from_family tool."""

    family_handle: Optional[str] = Field(
        None, description="Handle of the family"
    )
    family_gramps_id: Optional[str] = Field(
        None, description="Gramps ID of the family (e.g. 'F0042'). Alternative to family_handle."
    )
    event_handle: Optional[str] = Field(
        None, description="Handle of the event to unlink"
    )
    event_gramps_id: Optional[str] = Field(
        None, description="Gramps ID of the event (e.g. 'E0007'). Alternative to event_handle."
    )


class RemoveChildFromFamilyParams(BaseModel):
    """Parameters for remove_child_from_family tool."""

    family_handle: Optional[str] = Field(None, description="Handle of the family")
    family_gramps_id: Optional[str] = Field(
        None,
        description="Gramps ID of the family (e.g. 'F0001'). Alternative to family_handle.",
    )
    child_handle: Optional[str] = Field(
        None, description="Handle of the child person to remove"
    )
    child_gramps_id: Optional[str] = Field(
        None,
        description="Gramps ID of the child (e.g. 'I0003'). Alternative to child_handle.",
    )


class RemoveEventFromPersonParams(BaseModel):
    """Parameters for remove_event_from_person tool."""

    person_handle: Optional[str] = Field(
        None, description="Handle of the person"
    )
    person_gramps_id: Optional[str] = Field(
        None, description="Gramps ID of the person (e.g. 'I0042'). Alternative to person_handle."
    )
    event_handle: Optional[str] = Field(
        None, description="Handle of the event to unlink"
    )
    event_gramps_id: Optional[str] = Field(
        None, description="Gramps ID of the event (e.g. 'E0007'). Alternative to event_handle."
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


class AddCitationToEventParams(BaseModel):
    """Parameters for add_citation_to_event tool."""

    event_handle: Optional[str] = Field(
        None, description="Handle of the event"
    )
    event_gramps_id: Optional[str] = Field(
        None, description="Gramps ID of the event (e.g. 'E0007'). Alternative to event_handle."
    )
    citation_handle: Optional[str] = Field(
        None, description="Handle of the citation to add"
    )
    citation_gramps_id: Optional[str] = Field(
        None,
        description="Gramps ID of the citation (e.g. 'C0012'). Alternative to citation_handle.",
    )


class RemoveCitationFromEventParams(BaseModel):
    """Parameters for remove_citation_from_event tool."""

    event_handle: Optional[str] = Field(
        None, description="Handle of the event"
    )
    event_gramps_id: Optional[str] = Field(
        None, description="Gramps ID of the event (e.g. 'E0007'). Alternative to event_handle."
    )
    citation_handle: Optional[str] = Field(
        None, description="Handle of the citation to remove"
    )
    citation_gramps_id: Optional[str] = Field(
        None,
        description="Gramps ID of the citation (e.g. 'C0012'). Alternative to citation_handle.",
    )


class AddNoteToPersonParams(BaseModel):
    """Parameters for add_note_to_person tool."""

    person_handle: Optional[str] = Field(None, description="Handle of the person")
    person_gramps_id: Optional[str] = Field(
        None, description="Gramps ID of the person (e.g. 'I0001'). Alternative to person_handle."
    )
    note_handle: Optional[str] = Field(
        None, description="Handle of an existing note to link or update"
    )
    note_gramps_id: Optional[str] = Field(
        None,
        description="Gramps ID of an existing note (e.g. 'N0012'). Alternative to note_handle.",
    )
    text: Optional[str] = Field(
        None,
        description=(
            "Note text. Required when note_handle/note_gramps_id are both omitted "
            "(creates a new note). If given together with note_handle/note_gramps_id, "
            "overwrites the existing note's text."
        ),
    )
    type: Optional[str] = Field(
        None,
        description=(
            "Note type (e.g. 'Research'). Required when note_handle/note_gramps_id are "
            "both omitted (creates a new note). If given together with note_handle/"
            "note_gramps_id, overwrites the existing note's type."
        ),
    )


class AddNoteToFamilyParams(BaseModel):
    """Parameters for add_note_to_family tool."""

    family_handle: Optional[str] = Field(None, description="Handle of the family")
    family_gramps_id: Optional[str] = Field(
        None, description="Gramps ID of the family (e.g. 'F0001'). Alternative to family_handle."
    )
    note_handle: Optional[str] = Field(
        None, description="Handle of an existing note to link or update"
    )
    note_gramps_id: Optional[str] = Field(
        None,
        description="Gramps ID of an existing note (e.g. 'N0012'). Alternative to note_handle.",
    )
    text: Optional[str] = Field(
        None,
        description=(
            "Note text. Required when note_handle/note_gramps_id are both omitted "
            "(creates a new note). If given together with note_handle/note_gramps_id, "
            "overwrites the existing note's text."
        ),
    )
    type: Optional[str] = Field(
        None,
        description=(
            "Note type (e.g. 'Research'). Required when note_handle/note_gramps_id are "
            "both omitted (creates a new note). If given together with note_handle/"
            "note_gramps_id, overwrites the existing note's type."
        ),
    )


class RemoveNoteFromPersonParams(BaseModel):
    """Parameters for remove_note_from_person tool."""

    person_handle: Optional[str] = Field(None, description="Handle of the person")
    person_gramps_id: Optional[str] = Field(
        None, description="Gramps ID of the person (e.g. 'I0001'). Alternative to person_handle."
    )
    note_handle: Optional[str] = Field(None, description="Handle of the note to unlink")
    note_gramps_id: Optional[str] = Field(
        None, description="Gramps ID of the note (e.g. 'N0012'). Alternative to note_handle."
    )


class RemoveNoteFromFamilyParams(BaseModel):
    """Parameters for remove_note_from_family tool."""

    family_handle: Optional[str] = Field(None, description="Handle of the family")
    family_gramps_id: Optional[str] = Field(
        None, description="Gramps ID of the family (e.g. 'F0001'). Alternative to family_handle."
    )
    note_handle: Optional[str] = Field(None, description="Handle of the note to unlink")
    note_gramps_id: Optional[str] = Field(
        None, description="Gramps ID of the note (e.g. 'N0012'). Alternative to note_handle."
    )
