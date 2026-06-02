"""Pydantic parameter models for the three link-edit MCP tools."""

from typing import Literal

from pydantic import BaseModel, Field


class AddEventToPersonParams(BaseModel):
    """Parameters for add_event_to_person tool."""

    person_handle: str = Field(..., description="Handle of the person to link the event to")
    event_handle: str = Field(
        ..., description="Handle of the event to link (must already exist in the DB)"
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

    family_handle: str = Field(..., description="Handle of the family")
    child_handle: str = Field(..., description="Handle of the child person to remove")


class MoveAttachmentParams(BaseModel):
    """Parameters for move_attachment tool."""

    attachment_type: Literal["note", "media"] = Field(
        ..., description="Type of attachment to move: 'note' or 'media'"
    )
    handle: str = Field(..., description="Handle of the note or media object to move")
    from_handle: str = Field(..., description="Handle of the source object")
    from_type: Literal["person", "family"] = Field(
        "person", description="Type of the source object: 'person' or 'family'"
    )
    to_handle: str = Field(..., description="Handle of the target object")
    to_type: Literal["person", "family"] = Field(
        "person", description="Type of the target object: 'person' or 'family'"
    )
