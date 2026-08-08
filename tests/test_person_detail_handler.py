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
Tests for format_person_detail — the Spouse:/Children: rendering used by get_person.

Uses a fresh in-memory SQLite backend per test (not the shared session-scoped
sqlite_client fixture) so family_list mutations don't leak between tests.
"""

import pytest

from gramps_mcp.handlers.person_detail_handler import format_person_detail
from gramps_mcp.models.api_calls import ApiCalls


def _make_write_client():
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
    return _make_write_client()


def _new_person(client_sync_db, first_name: str, surname: str) -> dict:
    return client_sync_db.put("person", {
        "given_name": first_name, "surname": surname,
        "primary_name": {
            "first_name": first_name,
            "surname_list": [{"surname": surname, "primary": True, "prefix": "", "connector": ""}],
            "suffix": "", "title": "", "call": "", "nick": "", "type": "Birth Name",
        },
        "event_ref_list": [], "family_list": [], "parent_family_list": [],
        "note_list": [], "citation_list": [], "media_list": [],
        "address_list": [], "urls": [],
    })


class TestSingleParentChildrenSection:
    @pytest.mark.asyncio
    async def test_children_shown_for_father_only_family(self, write_client):
        db = write_client._db
        father = _new_person(db, "Charlie", "Jones")
        child = _new_person(db, "Dana", "Jones")
        db.put("family", {
            "father_handle": father["handle"],
            "child_handles": [child["handle"]],
        })

        result = await format_person_detail(write_client, "default", father["handle"])

        assert "Children:" in result
        assert "Dana" in result

    @pytest.mark.asyncio
    async def test_children_shown_for_mother_only_family(self, write_client):
        db = write_client._db
        mother = _new_person(db, "Alice", "Roe")
        child = _new_person(db, "Ellis", "Roe")
        db.put("family", {
            "mother_handle": mother["handle"],
            "child_handles": [child["handle"]],
        })

        result = await format_person_detail(write_client, "default", mother["handle"])

        assert "Children:" in result
        assert "Ellis" in result

    @pytest.mark.asyncio
    async def test_spouse_and_children_both_shown_for_two_parent_family(self, write_client):
        db = write_client._db
        father = _new_person(db, "Charlie", "Jones")
        mother = _new_person(db, "Maria", "Jones")
        child = _new_person(db, "Dana", "Jones")
        db.put("family", {
            "father_handle": father["handle"], "mother_handle": mother["handle"],
            "child_handles": [child["handle"]],
        })

        result = await format_person_detail(write_client, "default", father["handle"])

        assert "Spouse:" in result
        assert "Maria" in result
        assert "Children:" in result
        assert "Dana" in result

    @pytest.mark.asyncio
    async def test_no_spouse_section_for_single_parent_family(self, write_client):
        db = write_client._db
        father = _new_person(db, "Charlie", "Jones")
        child = _new_person(db, "Dana", "Jones")
        db.put("family", {
            "father_handle": father["handle"],
            "child_handles": [child["handle"]],
        })

        result = await format_person_detail(write_client, "default", father["handle"])

        assert "Spouse:" not in result

    @pytest.mark.asyncio
    async def test_no_children_section_when_family_has_no_children(self, write_client):
        db = write_client._db
        father = _new_person(db, "Charlie", "Jones")
        db.put("family", {"father_handle": father["handle"]})

        result = await format_person_detail(write_client, "default", father["handle"])

        assert "Children:" not in result
