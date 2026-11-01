"""
Regression tests for tool output that only surfaced once the tool-level
integration tests ran against a real SQLite database.

Each test goes through the public tools against the ``live_backend`` database.
"""

import re

import pytest

from src.gramps_mcp.tools.data_management import create_person_tool, create_place_tool
from src.gramps_mcp.tools.search_details import get_type_tool

pytestmark = pytest.mark.usefixtures("live_backend")


def _handle(text: str) -> str:
    """Extract the 32-character object handle from a tool response."""
    match = re.search(r"\[([a-f0-9]{32})\]", text)
    assert match, f"No handle found in: {text}"
    return match.group(1)


async def _create_place(name: str, parent: str | None = None, **extra) -> str:
    """Create a place through the tool and return its full response text."""
    arguments = {"name": {"value": name}, "place_type": "City", **extra}
    if parent:
        arguments["placeref_list"] = [{"ref": parent}]
    result = await create_place_tool(arguments)
    return result[0].text


class TestPlaceHierarchyOutput:
    """A place whose title is only its own name must still show its parents."""

    async def test_shows_all_ancestor_levels(self):
        """Every level is shown, not just the first parent."""
        country = _handle(await _create_place("Testland"))
        state = _handle(await _create_place("Teststate", parent=country))

        text = await _create_place("Testcity", parent=state)

        assert "Testcity, Teststate, Testland" in text

    async def test_parent_cycle_terminates(self):
        """A cycle in the parent links must not hang the formatter."""
        first = _handle(await _create_place("Cycle One"))
        second = _handle(await _create_place("Cycle Two", parent=first))

        text = await _create_place(
            "Cycle One", parent=second, handle=first
        )

        assert "Cycle One, Cycle Two" in text
        assert text.count("Cycle One") == 1


class TestUrlDescriptionOutput:
    """URL descriptions are stored as ``desc`` in Gramps JSON and must be shown."""

    async def test_person_url_description_is_shown(self):
        """The person formatter shows the description next to the URL."""
        result = await create_person_tool(
            {
                "primary_name": {
                    "first_name": "Url",
                    "surname_list": [{"surname": "Tester"}],
                },
                "gender": 1,
                "urls": [
                    {
                        "path": "https://example.org/profile",
                        "type": "Web Home",
                        "description": "Profile page",
                    }
                ],
            }
        )

        assert "https://example.org/profile - Profile page" in result[0].text

    async def test_place_url_description_is_shown(self):
        """The place formatter shows the description next to the URL."""
        text = await _create_place(
            "Url Town",
            urls=[
                {
                    "path": "https://example.org/town",
                    "type": "Web Home",
                    "description": "Town website",
                }
            ],
        )

        assert "https://example.org/town - Town website" in text


class TestGetTypeErrors:
    """get_type must explain failures instead of claiming to be unimplemented."""

    async def test_unknown_gramps_id_is_reported_as_not_found(self):
        """An unknown ID names the ID that was not found."""
        result = await get_type_tool({"type": "person", "gramps_id": "I9999"})

        text = result[0].text
        assert "not yet implemented" not in text
        assert "I9999" in text
        assert "not found" in text.lower()

    async def test_missing_identifier_is_reported(self):
        """Calling without handle or gramps_id says what is missing."""
        result = await get_type_tool({"type": "person"})

        text = result[0].text
        assert "not yet implemented" not in text
        assert "handle or gramps_id" in text
