"""
Registration tests for the note-link tools: confirms server.py wiring
(TOOL_REGISTRY entries, TOOL_GROUPS membership, param model shape) without
needing a live database.
"""


class TestNoteLinkRegistration:
    def test_tools_in_registry(self):
        from gramps_mcp.server import TOOL_REGISTRY

        for name in (
            "add_note_to_person",
            "add_note_to_family",
            "remove_note_from_person",
            "remove_note_from_family",
        ):
            assert name in TOOL_REGISTRY, f"{name} missing from TOOL_REGISTRY"
            assert "schema" in TOOL_REGISTRY[name]
            assert "handler" in TOOL_REGISTRY[name]
            assert "description" in TOOL_REGISTRY[name]

    def test_tools_in_groups(self):
        from gramps_mcp.server import TOOL_GROUPS

        assert "add_note_to_person" in TOOL_GROUPS["person"]
        assert "remove_note_from_person" in TOOL_GROUPS["person"]
        assert "add_note_to_family" in TOOL_GROUPS["family"]
        assert "remove_note_from_family" in TOOL_GROUPS["family"]

    def test_add_note_to_person_params_accepts_link_only(self):
        from gramps_mcp.models.parameters.link_edit_params import AddNoteToPersonParams

        params = AddNoteToPersonParams(person_handle="h1", note_handle="h2")
        assert params.text is None
        assert params.type is None

    def test_add_note_to_person_params_accepts_create(self):
        from gramps_mcp.models.parameters.link_edit_params import AddNoteToPersonParams

        params = AddNoteToPersonParams(person_handle="h1", text="hi", type="Research")
        assert params.note_handle is None

    def test_remove_note_params_shape(self):
        from gramps_mcp.models.parameters.link_edit_params import (
            RemoveNoteFromFamilyParams,
            RemoveNoteFromPersonParams,
        )

        p = RemoveNoteFromPersonParams(person_handle="h1", note_handle="h2")
        assert p.person_handle == "h1"
        f = RemoveNoteFromFamilyParams(family_handle="h1", note_handle="h2")
        assert f.family_handle == "h1"
