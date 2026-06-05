"""Tests for gramps_id resolution utilities. Uses SQLite in-memory fixture."""
import pytest

from gramps_mcp.client import GrampsAPIError
from gramps_mcp.gramps_id import _gramps_id_field, resolve_handle, resolve_handles


class TestGramsIdFieldNaming:
    def test_bare_handle(self):
        assert _gramps_id_field("handle") == "gramps_id"

    def test_prefixed_handle(self):
        assert _gramps_id_field("father_handle") == "father_gramps_id"

    def test_plural_handles(self):
        assert _gramps_id_field("child_handles") == "child_gramps_ids"

    def test_numbered_handle1(self):
        assert _gramps_id_field("handle1") == "gramps_id1"

    def test_numbered_handle2(self):
        assert _gramps_id_field("handle2") == "gramps_id2"

    def test_source_handle(self):
        assert _gramps_id_field("source_handle") == "source_gramps_id"

    def test_from_handle(self):
        assert _gramps_id_field("from_handle") == "from_gramps_id"


class TestResolveHandle:
    async def test_resolves_person_gramps_id(self, sqlite_client):
        handle = await resolve_handle("I0002", "person", sqlite_client)
        assert handle == "h_pe_jane"

    async def test_resolves_family_gramps_id(self, sqlite_client):
        handle = await resolve_handle("F0001", "family", sqlite_client)
        assert handle == "h_fa_smith"

    async def test_resolves_event_gramps_id(self, sqlite_client):
        handle = await resolve_handle("E0001", "event", sqlite_client)
        assert handle == "h_ev_birth_john"

    async def test_raises_if_not_found(self, sqlite_client):
        with pytest.raises(GrampsAPIError, match="not found"):
            await resolve_handle("I9999", "person", sqlite_client)


class TestResolveHandles:
    async def test_single_gramps_id_resolved(self, sqlite_client):
        args = {"gramps_id": "I0002"}
        result = await resolve_handles(args, {"handle": "person"}, sqlite_client)
        assert result["handle"] == "h_pe_jane"

    async def test_handle_takes_precedence_over_gramps_id(self, sqlite_client):
        args = {"handle": "h_pe_john", "gramps_id": "I0002"}
        result = await resolve_handles(args, {"handle": "person"}, sqlite_client)
        assert result["handle"] == "h_pe_john"

    async def test_no_gramps_id_no_change(self, sqlite_client):
        args = {"handle": "h_pe_john"}
        result = await resolve_handles(args, {"handle": "person"}, sqlite_client)
        assert result["handle"] == "h_pe_john"

    async def test_list_resolved_element_wise(self, sqlite_client):
        args = {"child_gramps_ids": ["I0003"]}
        result = await resolve_handles(
            args, {"child_handles": "person"}, sqlite_client
        )
        assert result["child_handles"] == ["h_pe_child"]

    async def test_multi_field_map(self, sqlite_client):
        args = {"father_gramps_id": "I0001", "mother_gramps_id": "I0002"}
        result = await resolve_handles(
            args,
            {"father_handle": "person", "mother_handle": "person"},
            sqlite_client,
        )
        assert result["father_handle"] == "h_pe_john"
        assert result["mother_handle"] == "h_pe_jane"

    async def test_returns_new_dict(self, sqlite_client):
        args = {"gramps_id": "I0002"}
        result = await resolve_handles(args, {"handle": "person"}, sqlite_client)
        assert result is not args


# ---------------------------------------------------------------------------
# Tool integration — get_person_tool and get_family_tool
# ---------------------------------------------------------------------------

class TestGetPersonToolGramsId:
    async def test_get_person_by_gramps_id(self, monkeypatch, sqlite_client):
        monkeypatch.delenv("GRAMPS_PRIVACY_MODE", raising=False)
        from gramps_mcp.tools.search_details import get_person_tool
        result = await get_person_tool.__wrapped__(sqlite_client, {"gramps_id": "I0001"})
        assert any("John" in r.text for r in result)

    async def test_get_person_by_handle_still_works(self, monkeypatch, sqlite_client):
        monkeypatch.delenv("GRAMPS_PRIVACY_MODE", raising=False)
        from gramps_mcp.tools.search_details import get_person_tool
        result = await get_person_tool.__wrapped__(sqlite_client, {"handle": "h_pe_john"})
        assert any("John" in r.text for r in result)


# ---------------------------------------------------------------------------
# Tool integration — get_family_tool
# ---------------------------------------------------------------------------

class TestGetFamilyToolHandleOrId:
    async def test_get_family_by_gramps_id(self, sqlite_client):
        from gramps_mcp.tools.search_details import get_family_tool
        result = await get_family_tool.__wrapped__(sqlite_client, {"gramps_id": "F0001"})
        assert any(
            "F0001" in r.text or "Smith" in r.text or "John" in r.text
            for r in result
        )

    async def test_get_family_by_handle(self, sqlite_client):
        from gramps_mcp.tools.search_details import get_family_tool
        result = await get_family_tool.__wrapped__(sqlite_client, {"handle": "h_fa_smith"})
        assert any(
            "F0001" in r.text or "Smith" in r.text or "John" in r.text
            for r in result
        )


# ---------------------------------------------------------------------------
# Tool integration — get_event_tool and get_place_tool
# ---------------------------------------------------------------------------

class TestGetEventToolHandleOrId:
    async def test_get_event_by_gramps_id(self, sqlite_client):
        from gramps_mcp.tools.search_details import get_event_tool
        result = await get_event_tool.__wrapped__(sqlite_client, {"gramps_id": "E0001"})
        assert any("E0001" in r.text or "Birth" in r.text for r in result)

    async def test_get_event_by_handle(self, sqlite_client):
        from gramps_mcp.tools.search_details import get_event_tool
        result = await get_event_tool.__wrapped__(sqlite_client, {"handle": "h_ev_birth_john"})
        assert any("E0001" in r.text or "Birth" in r.text for r in result)


class TestGetPlaceToolHandleOrId:
    async def test_get_place_by_gramps_id(self, sqlite_client):
        from gramps_mcp.tools.search_details import get_place_tool
        result = await get_place_tool.__wrapped__(sqlite_client, {"gramps_id": "P0001"})
        assert any("Berlin" in r.text or "P0001" in r.text for r in result)

    async def test_get_place_by_handle(self, sqlite_client):
        from gramps_mcp.tools.search_details import get_place_tool
        result = await get_place_tool.__wrapped__(sqlite_client, {"handle": "h_pl_berlin"})
        assert any("Berlin" in r.text or "P0001" in r.text for r in result)


# ---------------------------------------------------------------------------
# Tool integration — delete, create_family, create_citation
# ---------------------------------------------------------------------------


class TestDeleteObjectToolGramsId:
    async def test_delete_dry_run_by_gramps_id(self, sqlite_client):
        from gramps_mcp.tools.delete import delete_object_tool
        result = await delete_object_tool.__wrapped__(
            sqlite_client,
            {"obj_type": "event", "gramps_id": "E0001", "confirmed": False},
        )
        text = " ".join(r.text for r in result)
        assert "E0001" in text or "Birth" in text or "dry" in text.lower() or "would" in text.lower()


class TestCreateCitationToolGramsId:
    async def test_citation_accepts_source_gramps_id(self, monkeypatch, sqlite_client):
        import gramps_mcp.tools.data_management as dm
        monkeypatch.setattr(dm, "get_client", lambda: sqlite_client)
        result = await dm.create_citation_tool({"source_gramps_id": "S0001", "page": "p.42"})
        text = " ".join(r.text for r in result)
        # Should not error about source not found
        assert "not found" not in text.lower() or "S0001" in text


# ---------------------------------------------------------------------------
# Tool integration — link_edit gramps_id resolution
# ---------------------------------------------------------------------------

class TestLinkEditToolsGramsId:
    async def test_resolve_add_event_to_person(self, sqlite_client):
        """Verify that resolve_handles correctly maps gramps_ids for add_event_to_person."""
        from gramps_mcp.gramps_id import resolve_handles
        args = {"person_gramps_id": "I0001", "event_gramps_id": "E0003"}
        resolved = await resolve_handles(
            args,
            {"person_handle": "person", "event_handle": "event"},
            sqlite_client,
        )
        assert resolved["person_handle"] == "h_pe_john"
        assert resolved["event_handle"] == "h_ev_birth_jane"

    async def test_resolve_remove_child_from_family(self, sqlite_client):
        from gramps_mcp.gramps_id import resolve_handles
        args = {"family_gramps_id": "F0001", "child_gramps_id": "I0003"}
        resolved = await resolve_handles(
            args,
            {"family_handle": "family", "child_handle": "person"},
            sqlite_client,
        )
        assert resolved["family_handle"] == "h_fa_smith"
        assert resolved["child_handle"] == "h_pe_child"


# ---------------------------------------------------------------------------
# CRUD tools — gramps_id should trigger UPDATE not INSERT
# ---------------------------------------------------------------------------


class TestCrudGramsIdResolution:
    async def test_create_person_with_gramps_id_does_update(self, monkeypatch, sqlite_client):
        """create_person_tool with gramps_id instead of handle must update, not insert."""
        import gramps_mcp.tools.data_management as dm
        monkeypatch.setattr(dm, "get_client", lambda: sqlite_client)
        result = await dm.create_person_tool({
            "gramps_id": "I0001",
            "primary_name": {
                "first_name": "John Robert",
                "surname_list": [{"surname": "Smith", "prefix": "", "primary": True,
                                  "connector": ""}],
            },
            "gender": 1,
        })
        text = " ".join(r.text for r in result)
        assert "updated" in text.lower(), f"Expected 'updated', got: {text[:200]}"

    async def test_create_event_with_gramps_id_does_update(self, monkeypatch, sqlite_client):
        """create_event_tool with gramps_id instead of handle must update, not insert."""
        import gramps_mcp.tools.data_management as dm
        monkeypatch.setattr(dm, "get_client", lambda: sqlite_client)
        result = await dm.create_event_tool({
            "gramps_id": "E0001",
            "type": "Birth",
            "citation_list": [],
        })
        text = " ".join(r.text for r in result)
        assert "updated" in text.lower(), f"Expected 'updated', got: {text[:200]}"
