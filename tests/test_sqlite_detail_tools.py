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
Integration tests for the new detail/merge/duplicate-citation tools.

Uses an in-memory SQLite DB (derived from conftest_sqlite._make_in_memory_db)
with function scope so write operations don't affect subsequent tests.

Tools covered:
  get_event_tool, get_place_tool,
  merge_places_tool, merge_events_tool, merge_citations_tool,
  find_duplicate_citations_tool, merge_families_tool
"""

import json
import os

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_write_client():
    """
    Build a fresh in-memory GrampsSqliteClient for each test.

    We import _make_in_memory_db from the shared conftest so the base data
    is identical, then wrap it in a writable client.
    """
    from tests.conftest_sqlite import _make_in_memory_db
    from gramps_mcp._gramps_sqlite import GrampsSqliteDB
    from gramps_mcp.sqlite_client import GrampsSqliteClient

    conn = _make_in_memory_db()
    db = GrampsSqliteDB(conn=conn, db_path=":memory:", read_only=False)
    client = object.__new__(GrampsSqliteClient)
    client._db = db
    client._db_path = ":memory:"
    client._report_cache = {}
    return client


@pytest.fixture
def write_client():
    """Fresh writable in-memory client per test."""
    return _make_write_client()


def _add_citation(conn, handle: str, gramps_id: str, source_handle: str, page: str):
    """Insert an extra citation into the DB."""
    from gramps_mcp._gramps_sqlite import _denorm_date
    data = {
        "_class": "Citation", "handle": handle, "gramps_id": gramps_id,
        "page": page, "confidence": 2, "source_handle": source_handle,
        "date": _denorm_date({}),
        "note_list": [], "media_list": [], "attribute_list": [],
        "tag_list": [], "change": 0, "private": False,
    }
    conn.execute(
        "INSERT INTO citation (handle,gramps_id,json_data,page,confidence,source_handle,change,private) "
        "VALUES (?,?,?,?,?,?,?,?)",
        [handle, gramps_id, json.dumps(data), page, 2, source_handle, 0, 0],
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _result_text(result) -> str:
    return result[0].text


def _set_env_sqlite(monkeypatch, path=":memory:"):
    monkeypatch.setenv("GRAMPS_DB_PATH", path)
    monkeypatch.delenv("GRAMPS_API_URL", raising=False)


# ---------------------------------------------------------------------------
# get_event_tool
# ---------------------------------------------------------------------------

class TestGetEventTool:
    @pytest.mark.asyncio
    async def test_returns_event_details(self, write_client, monkeypatch):
        _set_env_sqlite(monkeypatch)
        monkeypatch.setattr(
            "gramps_mcp.tools.search_details._get_client_for_tool",
            lambda: write_client,
            raising=False,
        )
        from gramps_mcp.tools.search_details import get_event_tool
        from unittest.mock import AsyncMock, patch

        # Inject client directly via the with_client decorator path
        result = await get_event_tool.__wrapped__(write_client, {"gramps_id": "E0001"})
        text = _result_text(result)
        assert "E0001" in text

    @pytest.mark.asyncio
    async def test_not_found(self, write_client):
        from gramps_mcp.tools.search_details import get_event_tool
        result = await get_event_tool.__wrapped__(write_client, {"gramps_id": "E9999"})
        assert "not found" in _result_text(result)

    @pytest.mark.asyncio
    async def test_missing_gramps_id(self, write_client):
        from gramps_mcp.tools.search_details import get_event_tool
        result = await get_event_tool.__wrapped__(write_client, {})
        assert "Error" in _result_text(result)

    @pytest.mark.asyncio
    async def test_persons_listed(self, write_client):
        from gramps_mcp.tools.search_details import get_event_tool
        # E0001 = birth of John Robert Smith (h_pe_john)
        result = await get_event_tool.__wrapped__(write_client, {"gramps_id": "E0001"})
        text = _result_text(result)
        assert "John" in text or "I0001" in text

    @pytest.mark.asyncio
    async def test_family_event_listed(self, write_client):
        from gramps_mcp.tools.search_details import get_event_tool
        # E0004 = marriage event — attached to family h_fa_smith, not a person
        result = await get_event_tool.__wrapped__(write_client, {"gramps_id": "E0004"})
        text = _result_text(result)
        assert "E0004" in text


# ---------------------------------------------------------------------------
# get_place_tool
# ---------------------------------------------------------------------------

class TestGetPlaceTool:
    @pytest.mark.asyncio
    async def test_returns_place_details(self, write_client):
        from gramps_mcp.tools.search_details import get_place_tool
        result = await get_place_tool.__wrapped__(write_client, {"gramps_id": "P0001"})
        text = _result_text(result)
        assert "P0001" in text or "Berlin" in text

    @pytest.mark.asyncio
    async def test_not_found(self, write_client):
        from gramps_mcp.tools.search_details import get_place_tool
        result = await get_place_tool.__wrapped__(write_client, {"gramps_id": "P9999"})
        assert "not found" in _result_text(result)

    @pytest.mark.asyncio
    async def test_events_at_place_listed(self, write_client):
        from gramps_mcp.tools.search_details import get_place_tool
        # P0001 = Berlin: E0001 (birth_john) and E0004 (marriage) are here
        result = await get_place_tool.__wrapped__(write_client, {"gramps_id": "P0001"})
        text = _result_text(result)
        assert "Events" in text
        assert "E0001" in text or "E0004" in text


# ---------------------------------------------------------------------------
# get_note_tool
# ---------------------------------------------------------------------------

class TestGetNoteTool:
    @pytest.mark.asyncio
    async def test_returns_full_note_text(self, write_client):
        from gramps_mcp.tools.search_details import get_note_tool
        # N0001 = "John Smith was a notable person in his community." (h_no_john)
        result = await get_note_tool.__wrapped__(write_client, {"gramps_id": "N0001"})
        text = _result_text(result)
        assert "N0001" in text
        assert "John Smith was a notable person in his community." in text

    @pytest.mark.asyncio
    async def test_not_found(self, write_client):
        from gramps_mcp.tools.search_details import get_note_tool
        result = await get_note_tool.__wrapped__(write_client, {"gramps_id": "N9999"})
        assert "not found" in _result_text(result)

    @pytest.mark.asyncio
    async def test_missing_gramps_id(self, write_client):
        from gramps_mcp.tools.search_details import get_note_tool
        result = await get_note_tool.__wrapped__(write_client, {})
        assert "Error" in _result_text(result)

    @pytest.mark.asyncio
    async def test_backlinks_person_and_family(self, write_client):
        from gramps_mcp.tools.search_details import get_note_tool
        # N0001 (h_no_john) is linked to person I0001 and family F0001 in conftest_sqlite
        result = await get_note_tool.__wrapped__(write_client, {"gramps_id": "N0001"})
        text = _result_text(result)
        assert "I0001" in text
        assert "F0001" in text

    @pytest.mark.asyncio
    async def test_no_backlinks_found(self, write_client):
        from gramps_mcp.tools.search_details import get_note_tool

        conn = write_client._db._conn
        data = {
            "_class": "Note", "handle": "h_no_orphan", "gramps_id": "N0099", "format": 0,
            "text": {"_class": "StyledText", "string": "Orphan note", "tags": []},
            "type": {"_class": "NoteType", "value": 1, "string": "General"},
            "tag_list": [], "change": 0, "private": False,
        }
        conn.execute(
            "INSERT INTO note (handle, gramps_id, json_data, format, change, private) "
            "VALUES (?,?,?,0,0,0)",
            ("h_no_orphan", "N0099", json.dumps(data)),
        )
        conn.commit()

        result = await get_note_tool.__wrapped__(write_client, {"gramps_id": "N0099"})
        text = _result_text(result)
        assert "Orphan note" in text
        assert "Keine Verkn" in text  # "Keine Verknüpfungen gefunden"


# ---------------------------------------------------------------------------
# get_citation_tool
# ---------------------------------------------------------------------------

class TestGetCitationTool:
    @pytest.mark.asyncio
    async def test_returns_page_confidence_and_source(self, write_client):
        from gramps_mcp.tools.search_details import get_citation_tool
        # C0001 = h_ci_birth: page "Certificate No. 12345", confidence 2,
        # source_handle h_so_civil ("Civil Records Office")
        result = await get_citation_tool.__wrapped__(write_client, {"gramps_id": "C0001"})
        text = _result_text(result)
        assert "C0001" in text
        assert "Certificate No. 12345" in text
        assert "Normal" in text  # confidence 2
        assert "Civil Records Office" in text

    @pytest.mark.asyncio
    async def test_not_found(self, write_client):
        from gramps_mcp.tools.search_details import get_citation_tool
        result = await get_citation_tool.__wrapped__(write_client, {"gramps_id": "C9999"})
        assert "not found" in _result_text(result)

    @pytest.mark.asyncio
    async def test_missing_gramps_id(self, write_client):
        from gramps_mcp.tools.search_details import get_citation_tool
        result = await get_citation_tool.__wrapped__(write_client, {})
        assert "Error" in _result_text(result)

    @pytest.mark.asyncio
    async def test_backlink_person_found(self, write_client):
        from gramps_mcp.tools.search_details import get_citation_tool
        # h_ci_birth (C0001) is in h_pe_john's (I0001) citation_list in conftest_sqlite
        result = await get_citation_tool.__wrapped__(write_client, {"gramps_id": "C0001"})
        text = _result_text(result)
        assert "I0001" in text

    @pytest.mark.asyncio
    async def test_shows_apid_attribute(self, write_client):
        from gramps_mcp.tools.search_details import get_citation_tool

        conn = write_client._db._conn
        data = {
            "_class": "Citation", "handle": "h_ci_ancestry", "gramps_id": "C0099",
            "page": "", "confidence": 2, "source_handle": "h_so_civil",
            "date": {"_class": "Date", "calendar": 0, "modifier": 0, "quality": 0,
                     "dateval": [0, 0, 0, False], "text": "", "sortval": 0,
                     "newyear": 0, "format": None},
            "note_list": [], "media_list": [],
            "attribute_list": [{
                "_class": "Attribute",
                "type": {"_class": "AttributeType", "value": 0, "string": "_APID"},
                "value": "1,6482::12345", "private": False,
                "citation_list": [], "note_list": [],
            }],
            "tag_list": [], "change": 0, "private": False,
        }
        conn.execute(
            "INSERT INTO citation (handle,gramps_id,json_data,page,confidence,source_handle,change,private) "
            "VALUES (?,?,?,?,?,?,0,0)",
            ("h_ci_ancestry", "C0099", json.dumps(data), "", 2, "h_so_civil"),
        )
        conn.commit()

        result = await get_citation_tool.__wrapped__(write_client, {"gramps_id": "C0099"})
        text = _result_text(result)
        assert "_APID" in text
        assert "1,6482::12345" in text

    @pytest.mark.asyncio
    async def test_no_backlinks_found(self, write_client):
        from gramps_mcp.tools.search_details import get_citation_tool
        _add_citation(
            write_client._db._conn, "h_ci_orphan", "C0098", "h_so_civil", "Orphan page"
        )

        result = await get_citation_tool.__wrapped__(write_client, {"gramps_id": "C0098"})
        text = _result_text(result)
        assert "Keine Verkn" in text  # "Keine Verknüpfungen gefunden"


# ---------------------------------------------------------------------------
# merge_places_tool (dry_run + write)
# ---------------------------------------------------------------------------

class TestMergePlacesTool:
    @pytest.mark.asyncio
    async def test_dry_run_shows_planned(self, write_client):
        from gramps_mcp.tools.search_details import merge_places_tool
        result = await merge_places_tool.__wrapped__(
            write_client, {"winner_id": "P0001", "loser_id": "P0002", "dry_run": True}
        )
        text = _result_text(result)
        assert "[DRY RUN]" in text

    @pytest.mark.asyncio
    async def test_dry_run_does_not_delete(self, write_client):
        from gramps_mcp.tools.search_details import merge_places_tool
        await merge_places_tool.__wrapped__(
            write_client, {"winner_id": "P0001", "loser_id": "P0002", "dry_run": True}
        )
        row = write_client._db._conn.execute(
            "SELECT 1 FROM place WHERE handle='h_pl_hamburg'"
        ).fetchone()
        assert row is not None

    @pytest.mark.asyncio
    async def test_write_deletes_loser(self, write_client):
        from gramps_mcp.tools.search_details import merge_places_tool
        result = await merge_places_tool.__wrapped__(
            write_client, {"winner_id": "P0001", "loser_id": "P0002", "dry_run": False}
        )
        text = _result_text(result)
        assert "Fertig" in text
        row = write_client._db._conn.execute(
            "SELECT 1 FROM place WHERE handle='h_pl_hamburg'"
        ).fetchone()
        assert row is None

    @pytest.mark.asyncio
    async def test_write_redirects_events(self, write_client):
        from gramps_mcp.tools.search_details import merge_places_tool
        # E0002 (death_john) is at h_pl_hamburg; after merge it should point to h_pl_berlin
        await merge_places_tool.__wrapped__(
            write_client, {"winner_id": "P0001", "loser_id": "P0002", "dry_run": False}
        )
        row = write_client._db._conn.execute(
            "SELECT json_data FROM event WHERE handle='h_ev_death_john'"
        ).fetchone()
        data = json.loads(row["json_data"])
        assert data["place"] == "h_pl_berlin"

    @pytest.mark.asyncio
    async def test_identical_winner_loser(self, write_client):
        from gramps_mcp.tools.search_details import merge_places_tool
        result = await merge_places_tool.__wrapped__(
            write_client, {"winner_id": "P0001", "loser_id": "P0001", "dry_run": False}
        )
        assert "identisch" in _result_text(result)

    @pytest.mark.asyncio
    async def test_loser_not_found(self, write_client):
        from gramps_mcp.tools.search_details import merge_places_tool
        result = await merge_places_tool.__wrapped__(
            write_client, {"winner_id": "P0001", "loser_id": "P9999", "dry_run": True}
        )
        assert "nicht gefunden" in _result_text(result)


# ---------------------------------------------------------------------------
# merge_events_tool (dry_run + write, including family event_ref_list)
# ---------------------------------------------------------------------------

class TestMergeEventsTool:
    @pytest.mark.asyncio
    async def test_dry_run_shows_planned(self, write_client):
        from gramps_mcp.tools.search_details import merge_events_tool
        result = await merge_events_tool.__wrapped__(
            write_client, {"winner_id": "E0001", "loser_id": "E0005", "dry_run": True}
        )
        assert "[DRY RUN]" in _result_text(result)

    @pytest.mark.asyncio
    async def test_dry_run_does_not_delete(self, write_client):
        from gramps_mcp.tools.search_details import merge_events_tool
        await merge_events_tool.__wrapped__(
            write_client, {"winner_id": "E0001", "loser_id": "E0005", "dry_run": True}
        )
        row = write_client._db._conn.execute(
            "SELECT 1 FROM event WHERE handle='h_ev_birth_child'"
        ).fetchone()
        assert row is not None

    @pytest.mark.asyncio
    async def test_write_deletes_loser(self, write_client):
        from gramps_mcp.tools.search_details import merge_events_tool
        await merge_events_tool.__wrapped__(
            write_client, {"winner_id": "E0001", "loser_id": "E0005", "dry_run": False}
        )
        row = write_client._db._conn.execute(
            "SELECT 1 FROM event WHERE handle='h_ev_birth_child'"
        ).fetchone()
        assert row is None

    @pytest.mark.asyncio
    async def test_write_removes_loser_from_person(self, write_client):
        from gramps_mcp.tools.search_details import merge_events_tool
        # h_pe_child has event_ref_list = [h_ev_birth_child]
        await merge_events_tool.__wrapped__(
            write_client, {"winner_id": "E0001", "loser_id": "E0005", "dry_run": False}
        )
        row = write_client._db._conn.execute(
            "SELECT json_data FROM person WHERE handle='h_pe_child'"
        ).fetchone()
        data = json.loads(row["json_data"])
        refs = [r.get("ref") for r in data.get("event_ref_list", []) if isinstance(r, dict)]
        assert "h_ev_birth_child" not in refs

    @pytest.mark.asyncio
    async def test_write_removes_loser_from_family(self, write_client):
        from gramps_mcp.tools.search_details import merge_events_tool
        # h_fa_smith has event_ref_list = [h_ev_marriage]; add a duplicate to test family handling
        # Use h_ev_marriage (E0004) as loser, merge into E0001
        # First verify family references h_ev_marriage
        row = write_client._db._conn.execute(
            "SELECT json_data FROM family WHERE handle='h_fa_smith'"
        ).fetchone()
        fam_before = json.loads(row["json_data"])
        fam_refs_before = [r.get("ref") for r in fam_before.get("event_ref_list", []) if isinstance(r, dict)]
        assert "h_ev_marriage" in fam_refs_before

        await merge_events_tool.__wrapped__(
            write_client, {"winner_id": "E0001", "loser_id": "E0004", "dry_run": False}
        )
        row = write_client._db._conn.execute(
            "SELECT json_data FROM family WHERE handle='h_fa_smith'"
        ).fetchone()
        fam_after = json.loads(row["json_data"])
        fam_refs_after = [r.get("ref") for r in fam_after.get("event_ref_list", []) if isinstance(r, dict)]
        assert "h_ev_marriage" not in fam_refs_after

    @pytest.mark.asyncio
    async def test_identical_winner_loser(self, write_client):
        from gramps_mcp.tools.search_details import merge_events_tool
        result = await merge_events_tool.__wrapped__(
            write_client, {"winner_id": "E0001", "loser_id": "E0001", "dry_run": False}
        )
        assert "identisch" in _result_text(result)

    @pytest.mark.asyncio
    async def test_merge_events_with_note_handles_on_event(self, write_client):
        """Events whose note_list contains plain handle strings must not crash.

        Gramps stores note_list as a list of plain handle strings (not dicts).
        _merge_unique must handle both str and dict items.
        Regression test for: 'str' object has no attribute 'get'
        """
        conn = write_client._db._conn
        # Add a note row so the handle is valid
        note_json = {
            "_class": "Note", "handle": "h_no_ev_test", "gramps_id": "N9001",
            "format": 0, "text": {"_class": "StyledText", "string": "Test note", "tags": []},
            "type": {"_class": "NoteType", "value": 1, "string": "General"},
            "tag_list": [], "change": 0, "private": False,
        }
        conn.execute(
            "INSERT INTO note (handle, gramps_id, json_data) VALUES (?,?,?)",
            ("h_no_ev_test", "N9001", json.dumps(note_json))
        )
        # Patch E0001's note_list to contain a plain string handle
        row = conn.execute("SELECT json_data FROM event WHERE gramps_id='E0001'").fetchone()
        ev = json.loads(row["json_data"])
        ev["note_list"] = ["h_no_ev_test"]
        conn.execute(
            "UPDATE event SET json_data=? WHERE gramps_id='E0001'",
            (json.dumps(ev),)
        )
        conn.commit()

        from gramps_mcp.tools.search_details import merge_events_tool
        # Should not crash with 'str' object has no attribute 'get'
        result = await merge_events_tool.__wrapped__(
            write_client, {"winner_id": "E0001", "loser_id": "E0005", "dry_run": True}
        )
        assert "[DRY RUN]" in _result_text(result)


# ---------------------------------------------------------------------------
# merge_citations_tool (dry_run + write, including person citation_list)
# ---------------------------------------------------------------------------

class TestMergeCitationsTool:
    @pytest.mark.asyncio
    async def test_dry_run_shows_planned(self, write_client):
        # Add a second citation with same source to merge
        _add_citation(write_client._db._conn, "h_ci_dup", "C0002", "h_so_civil", "Certificate No. 99")
        from gramps_mcp.tools.search_details import merge_citations_tool
        result = await merge_citations_tool.__wrapped__(
            write_client, {"winner_id": "C0001", "loser_id": "C0002", "dry_run": True}
        )
        assert "[DRY RUN]" in _result_text(result)

    @pytest.mark.asyncio
    async def test_dry_run_does_not_delete(self, write_client):
        _add_citation(write_client._db._conn, "h_ci_dup", "C0002", "h_so_civil", "p99")
        from gramps_mcp.tools.search_details import merge_citations_tool
        await merge_citations_tool.__wrapped__(
            write_client, {"winner_id": "C0001", "loser_id": "C0002", "dry_run": True}
        )
        row = write_client._db._conn.execute(
            "SELECT 1 FROM citation WHERE handle='h_ci_dup'"
        ).fetchone()
        assert row is not None

    @pytest.mark.asyncio
    async def test_write_deletes_loser(self, write_client):
        _add_citation(write_client._db._conn, "h_ci_dup", "C0002", "h_so_civil", "p99")
        from gramps_mcp.tools.search_details import merge_citations_tool
        await merge_citations_tool.__wrapped__(
            write_client, {"winner_id": "C0001", "loser_id": "C0002", "dry_run": False}
        )
        row = write_client._db._conn.execute(
            "SELECT 1 FROM citation WHERE handle='h_ci_dup'"
        ).fetchone()
        assert row is None

    @pytest.mark.asyncio
    async def test_write_redirects_person_citation(self, write_client):
        # I0001 (h_pe_john) has citation_list = ["h_ci_birth"]
        # Add a duplicate citation and put it on John's citation_list
        _add_citation(write_client._db._conn, "h_ci_dup", "C0002", "h_so_civil", "p99")
        # Update John to also reference the loser citation
        row = write_client._db._conn.execute(
            "SELECT json_data FROM person WHERE handle='h_pe_john'"
        ).fetchone()
        john = json.loads(row["json_data"])
        john["citation_list"] = ["h_ci_dup"]
        write_client._db._conn.execute(
            "UPDATE person SET json_data=? WHERE handle='h_pe_john'",
            [json.dumps(john)]
        )
        write_client._db._conn.commit()

        from gramps_mcp.tools.search_details import merge_citations_tool
        await merge_citations_tool.__wrapped__(
            write_client, {"winner_id": "C0001", "loser_id": "C0002", "dry_run": False}
        )
        row = write_client._db._conn.execute(
            "SELECT json_data FROM person WHERE handle='h_pe_john'"
        ).fetchone()
        john_after = json.loads(row["json_data"])
        assert "h_ci_dup" not in john_after["citation_list"]
        assert "h_ci_birth" in john_after["citation_list"]

    @pytest.mark.asyncio
    async def test_identical_winner_loser(self, write_client):
        from gramps_mcp.tools.search_details import merge_citations_tool
        result = await merge_citations_tool.__wrapped__(
            write_client, {"winner_id": "C0001", "loser_id": "C0001", "dry_run": False}
        )
        assert "identisch" in _result_text(result)


# ---------------------------------------------------------------------------
# find_duplicate_citations_tool
# ---------------------------------------------------------------------------

class TestFindDuplicateCitationsTool:
    @pytest.mark.asyncio
    async def test_no_duplicates(self, write_client):
        from gramps_mcp.tools.search_details import find_duplicate_citations_tool
        result = await find_duplicate_citations_tool.__wrapped__(write_client, {})
        assert "Keine Duplikate" in _result_text(result)

    @pytest.mark.asyncio
    async def test_detects_duplicate_pair(self, write_client):
        # Add a second citation with same source + page as C0001
        _add_citation(
            write_client._db._conn, "h_ci_dup", "C0002",
            "h_so_civil", "Certificate No. 12345"
        )
        from gramps_mcp.tools.search_details import find_duplicate_citations_tool
        result = await find_duplicate_citations_tool.__wrapped__(write_client, {})
        text = _result_text(result)
        assert "C0001" in text or "C0002" in text
        assert "2" in text  # 2 citations in the duplicate group

    @pytest.mark.asyncio
    async def test_source_filter(self, write_client):
        _add_citation(
            write_client._db._conn, "h_ci_dup", "C0002",
            "h_so_civil", "Certificate No. 12345"
        )
        from gramps_mcp.tools.search_details import find_duplicate_citations_tool
        # Filter matches "Civil Records Office" (source title)
        result_match = await find_duplicate_citations_tool.__wrapped__(
            write_client, {"source_filter": "civil"}
        )
        result_no_match = await find_duplicate_citations_tool.__wrapped__(
            write_client, {"source_filter": "nonexistent_source_xyz"}
        )
        assert "C0001" in _result_text(result_match) or "C0002" in _result_text(result_match)
        assert "Keine Duplikate" in _result_text(result_no_match)


# ---------------------------------------------------------------------------
# merge_families_tool (dry_run + write, child_ref_list + event_ref_list transfer)
# ---------------------------------------------------------------------------

def _add_loser_family(conn, handle: str, gramps_id: str,
                      father_handle: str = "", mother_handle: str = "",
                      child_handles: list = None, event_handles: list = None):
    """Insert a second (loser) family into the DB for merge tests."""
    from gramps_mcp._gramps_sqlite import _denorm_date

    def _cref(h):
        return {"_class": "ChildRef", "ref": h,
                "frel": {"_class": "ChildRefType", "value": 1, "string": ""},
                "mrel": {"_class": "ChildRefType", "value": 1, "string": ""},
                "private": False, "citation_list": [], "note_list": []}

    def _eref(h):
        return {"_class": "EventRef", "ref": h,
                "role": {"_class": "EventRoleType", "value": 8, "string": ""},
                "note_list": [], "attribute_list": [], "private": False}

    data = {
        "_class": "Family", "handle": handle, "gramps_id": gramps_id,
        "father_handle": father_handle, "mother_handle": mother_handle,
        "child_ref_list": [_cref(h) for h in (child_handles or [])],
        "event_ref_list": [_eref(h) for h in (event_handles or [])],
        "type": {"_class": "FamilyRelType", "value": 0, "string": ""},
        "media_list": [], "attribute_list": [], "lds_ord_list": [],
        "citation_list": [], "note_list": [], "tag_list": [],
        "change": 0, "private": False,
    }
    conn.execute(
        "INSERT INTO family (handle,gramps_id,json_data,father_handle,mother_handle,change,private) "
        "VALUES (?,?,?,?,?,?,?)",
        [handle, gramps_id, json.dumps(data), father_handle, mother_handle, 0, 0],
    )
    conn.commit()


class TestMergeFamiliesTool:
    @pytest.mark.asyncio
    async def test_dry_run_shows_planned(self, write_client):
        _add_loser_family(write_client._db._conn, "h_fa_loser", "F0002",
                          father_handle="h_pe_john")
        from gramps_mcp.tools.search_details import merge_families_tool
        result = await merge_families_tool.__wrapped__(
            write_client, {"winner_id": "F0001", "loser_id": "F0002", "dry_run": True}
        )
        assert "[DRY RUN]" in _result_text(result)

    @pytest.mark.asyncio
    async def test_dry_run_does_not_delete(self, write_client):
        _add_loser_family(write_client._db._conn, "h_fa_loser", "F0002")
        from gramps_mcp.tools.search_details import merge_families_tool
        await merge_families_tool.__wrapped__(
            write_client, {"winner_id": "F0001", "loser_id": "F0002", "dry_run": True}
        )
        row = write_client._db._conn.execute(
            "SELECT 1 FROM family WHERE handle='h_fa_loser'"
        ).fetchone()
        assert row is not None

    @pytest.mark.asyncio
    async def test_write_deletes_loser(self, write_client):
        _add_loser_family(write_client._db._conn, "h_fa_loser", "F0002")
        from gramps_mcp.tools.search_details import merge_families_tool
        result = await merge_families_tool.__wrapped__(
            write_client, {"winner_id": "F0001", "loser_id": "F0002", "dry_run": False}
        )
        assert "Fertig" in _result_text(result)
        row = write_client._db._conn.execute(
            "SELECT 1 FROM family WHERE handle='h_fa_loser'"
        ).fetchone()
        assert row is None

    @pytest.mark.asyncio
    async def test_write_redirects_family_list(self, write_client):
        # John (h_pe_john) has family_list = ["h_fa_smith"]
        # Add loser family and put John in its family_list too
        _add_loser_family(write_client._db._conn, "h_fa_loser", "F0002",
                          father_handle="h_pe_john")
        row = write_client._db._conn.execute(
            "SELECT json_data FROM person WHERE handle='h_pe_john'"
        ).fetchone()
        john = json.loads(row["json_data"])
        john["family_list"] = ["h_fa_loser"]
        write_client._db._conn.execute(
            "UPDATE person SET json_data=? WHERE handle='h_pe_john'", [json.dumps(john)]
        )
        write_client._db._conn.commit()

        from gramps_mcp.tools.search_details import merge_families_tool
        await merge_families_tool.__wrapped__(
            write_client, {"winner_id": "F0001", "loser_id": "F0002", "dry_run": False}
        )
        row = write_client._db._conn.execute(
            "SELECT json_data FROM person WHERE handle='h_pe_john'"
        ).fetchone()
        john_after = json.loads(row["json_data"])
        assert "h_fa_loser" not in john_after["family_list"]
        assert "h_fa_smith" in john_after["family_list"]

    @pytest.mark.asyncio
    async def test_write_redirects_parent_family_list(self, write_client):
        # James (h_pe_child) has parent_family_list = ["h_fa_smith"]
        # Add loser with James as child and set his parent_family_list to loser
        _add_loser_family(write_client._db._conn, "h_fa_loser", "F0002",
                          child_handles=["h_pe_child"])
        row = write_client._db._conn.execute(
            "SELECT json_data FROM person WHERE handle='h_pe_child'"
        ).fetchone()
        james = json.loads(row["json_data"])
        james["parent_family_list"] = ["h_fa_loser"]
        write_client._db._conn.execute(
            "UPDATE person SET json_data=? WHERE handle='h_pe_child'", [json.dumps(james)]
        )
        write_client._db._conn.commit()

        from gramps_mcp.tools.search_details import merge_families_tool
        await merge_families_tool.__wrapped__(
            write_client, {"winner_id": "F0001", "loser_id": "F0002", "dry_run": False}
        )
        row = write_client._db._conn.execute(
            "SELECT json_data FROM person WHERE handle='h_pe_child'"
        ).fetchone()
        james_after = json.loads(row["json_data"])
        assert "h_fa_loser" not in james_after["parent_family_list"]
        assert "h_fa_smith" in james_after["parent_family_list"]

    @pytest.mark.asyncio
    async def test_write_redirects_family_list_dedupes(self, write_client):
        # John already has both h_fa_smith (winner) and h_fa_loser in family_list
        # (e.g. after a prior person merge). Redirecting loser->winner must not
        # produce a duplicate h_fa_smith entry.
        _add_loser_family(write_client._db._conn, "h_fa_loser", "F0002",
                          father_handle="h_pe_john")
        row = write_client._db._conn.execute(
            "SELECT json_data FROM person WHERE handle='h_pe_john'"
        ).fetchone()
        john = json.loads(row["json_data"])
        john["family_list"] = ["h_fa_smith", "h_fa_loser"]
        write_client._db._conn.execute(
            "UPDATE person SET json_data=? WHERE handle='h_pe_john'", [json.dumps(john)]
        )
        write_client._db._conn.commit()

        from gramps_mcp.tools.search_details import merge_families_tool
        await merge_families_tool.__wrapped__(
            write_client, {"winner_id": "F0001", "loser_id": "F0002", "dry_run": False}
        )
        row = write_client._db._conn.execute(
            "SELECT json_data FROM person WHERE handle='h_pe_john'"
        ).fetchone()
        john_after = json.loads(row["json_data"])
        assert john_after["family_list"].count("h_fa_smith") == 1

    @pytest.mark.asyncio
    async def test_write_redirects_parent_family_list_dedupes(self, write_client):
        # James already has both h_fa_smith (winner) and h_fa_loser in
        # parent_family_list. Redirecting must not produce a duplicate entry.
        _add_loser_family(write_client._db._conn, "h_fa_loser", "F0002",
                          child_handles=["h_pe_child"])
        row = write_client._db._conn.execute(
            "SELECT json_data FROM person WHERE handle='h_pe_child'"
        ).fetchone()
        james = json.loads(row["json_data"])
        james["parent_family_list"] = ["h_fa_smith", "h_fa_loser"]
        write_client._db._conn.execute(
            "UPDATE person SET json_data=? WHERE handle='h_pe_child'", [json.dumps(james)]
        )
        write_client._db._conn.commit()

        from gramps_mcp.tools.search_details import merge_families_tool
        await merge_families_tool.__wrapped__(
            write_client, {"winner_id": "F0001", "loser_id": "F0002", "dry_run": False}
        )
        row = write_client._db._conn.execute(
            "SELECT json_data FROM person WHERE handle='h_pe_child'"
        ).fetchone()
        james_after = json.loads(row["json_data"])
        assert james_after["parent_family_list"].count("h_fa_smith") == 1

    @pytest.mark.asyncio
    async def test_write_merges_child_ref_list(self, write_client):
        # Loser has James (h_pe_child) as an extra child not yet in winner
        _add_loser_family(write_client._db._conn, "h_fa_loser", "F0002",
                          child_handles=["h_pe_child"])
        # Remove James from winner's child_ref_list first
        row = write_client._db._conn.execute(
            "SELECT json_data FROM family WHERE handle='h_fa_smith'"
        ).fetchone()
        winner = json.loads(row["json_data"])
        winner["child_ref_list"] = []
        write_client._db._conn.execute(
            "UPDATE family SET json_data=? WHERE handle='h_fa_smith'", [json.dumps(winner)]
        )
        write_client._db._conn.commit()

        from gramps_mcp.tools.search_details import merge_families_tool
        await merge_families_tool.__wrapped__(
            write_client, {"winner_id": "F0001", "loser_id": "F0002", "dry_run": False}
        )
        row = write_client._db._conn.execute(
            "SELECT json_data FROM family WHERE handle='h_fa_smith'"
        ).fetchone()
        winner_after = json.loads(row["json_data"])
        child_refs = [r.get("ref") for r in winner_after.get("child_ref_list", [])
                      if isinstance(r, dict)]
        assert "h_pe_child" in child_refs

    @pytest.mark.asyncio
    async def test_write_merges_event_ref_list(self, write_client):
        # Loser has E0003 (birth_jane) as an event; winner has E0004 (marriage)
        # After merge, winner should have both
        _add_loser_family(write_client._db._conn, "h_fa_loser", "F0002",
                          event_handles=["h_ev_birth_jane"])
        from gramps_mcp.tools.search_details import merge_families_tool
        await merge_families_tool.__wrapped__(
            write_client, {"winner_id": "F0001", "loser_id": "F0002", "dry_run": False}
        )
        row = write_client._db._conn.execute(
            "SELECT json_data FROM family WHERE handle='h_fa_smith'"
        ).fetchone()
        winner_after = json.loads(row["json_data"])
        event_refs = [r.get("ref") for r in winner_after.get("event_ref_list", [])
                      if isinstance(r, dict)]
        assert "h_ev_birth_jane" in event_refs
        assert "h_ev_marriage" in event_refs  # original winner event preserved

    @pytest.mark.asyncio
    async def test_identical_winner_loser(self, write_client):
        from gramps_mcp.tools.search_details import merge_families_tool
        result = await merge_families_tool.__wrapped__(
            write_client, {"winner_id": "F0001", "loser_id": "F0001", "dry_run": False}
        )
        assert "identisch" in _result_text(result)

    @pytest.mark.asyncio
    async def test_loser_not_found(self, write_client):
        from gramps_mcp.tools.search_details import merge_families_tool
        result = await merge_families_tool.__wrapped__(
            write_client, {"winner_id": "F0001", "loser_id": "F9999", "dry_run": True}
        )
        assert "nicht gefunden" in _result_text(result)
