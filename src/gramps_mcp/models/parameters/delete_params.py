"""Parameters for the delete_object MCP tool."""

from typing import Literal, Optional

from pydantic import BaseModel, Field


class DeleteObjectParams(BaseModel):
    """Parameters for delete_object tool."""

    obj_type: Literal[
        "person",
        "family",
        "event",
        "place",
        "citation",
        "source",
        "note",
        "media",
        "repository",
        "tag",
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
