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
Regression tests for issue #20: create_citation/format_citation must echo back
page/date/confidence explicitly so a silent write-drop (like #12 was) is visible
in the tool's own response instead of looking identical to a successful write.
"""

import json

import pytest

from tests.conftest_sqlite import _make_in_memory_db


def _make_write_client():
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


def _add_citation(
    conn,
    handle: str,
    gramps_id: str,
    source_handle: str,
    page: str,
    confidence: int = 2,
):
    from gramps_mcp._gramps_sqlite import _denorm_date

    data = {
        "_class": "Citation",
        "handle": handle,
        "gramps_id": gramps_id,
        "page": page,
        "confidence": confidence,
        "source_handle": source_handle,
        "date": _denorm_date({}),
        "note_list": [],
        "media_list": [],
        "attribute_list": [],
        "tag_list": [],
        "change": 0,
        "private": False,
    }
    conn.execute(
        "INSERT INTO citation "
        "(handle,gramps_id,json_data,page,confidence,source_handle,change,private) "
        "VALUES (?,?,?,?,?,?,?,?)",
        [handle, gramps_id, json.dumps(data), page, confidence, source_handle, 0, 0],
    )
    conn.commit()


class TestFormatCitationEchoesFields:
    @pytest.mark.asyncio
    async def test_shows_page_value(self, write_client):
        from gramps_mcp.handlers.citation_handler import format_citation

        # C0001 = h_ci_birth: page "Certificate No. 12345" (see conftest_sqlite)
        result = await format_citation(write_client, "default", "h_ci_birth")
        assert "Page: Certificate No. 12345" in result

    @pytest.mark.asyncio
    async def test_shows_none_for_empty_page(self, write_client):
        """
        Core regression case: a citation whose page silently failed to persist
        (e.g. issue #12) must not look identical to one that never had a page.
        """
        from gramps_mcp.handlers.citation_handler import format_citation

        conn = write_client._db._conn
        _add_citation(conn, "h_ci_nopage", "C0099", "h_so_civil", page="")

        result = await format_citation(write_client, "default", "h_ci_nopage")
        assert "Page: (none)" in result

    @pytest.mark.asyncio
    async def test_shows_confidence_label(self, write_client):
        from gramps_mcp.handlers.citation_handler import format_citation

        result = await format_citation(write_client, "default", "h_ci_birth")
        assert "Confidence: Normal" in result

    @pytest.mark.asyncio
    async def test_shows_low_confidence_label(self, write_client):
        from gramps_mcp.handlers.citation_handler import format_citation

        conn = write_client._db._conn
        _add_citation(
            conn, "h_ci_lowconf", "C0098", "h_so_civil", page="p1", confidence=0
        )

        result = await format_citation(write_client, "default", "h_ci_lowconf")
        assert "Confidence: Very Low" in result

    @pytest.mark.asyncio
    async def test_shows_date_line(self, write_client):
        from gramps_mcp.handlers.citation_handler import format_citation

        # C0001 date dateval [15, 1, 2024, False]
        result = await format_citation(write_client, "default", "h_ci_birth")
        assert "Date:" in result

    @pytest.mark.asyncio
    async def test_shows_date_unknown_when_no_date(self, write_client):
        from gramps_mcp.handlers.citation_handler import format_citation

        conn = write_client._db._conn
        _add_citation(conn, "h_ci_nodate", "C0097", "h_so_civil", page="p2")

        result = await format_citation(write_client, "default", "h_ci_nodate")
        assert "Date: date unknown" in result
