"""Parameters for the delete_object MCP tool."""

from typing import Literal

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
    handle: str = Field(..., description="Gramps handle of the object to delete")
    confirmed: bool = Field(
        False,
        description=(
            "False (default) = dry-run, returns summary without deleting. "
            "True = execute deletion. Always call with False first."
        ),
    )
