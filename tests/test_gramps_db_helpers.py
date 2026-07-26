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
Unit tests for report-formatting helpers in _gramps_db.py.
"""

from gramps_mcp._gramps_db import _year_from_date


class TestYearFromDate:
    def test_dateval_year_used_when_present(self):
        assert _year_from_date({"dateval": [0, 0, 1830, False]}) == "1830"

    def test_falls_back_to_free_text_date(self):
        # Gramps stores unparsed dates (e.g. "7 Mai 1604") under date.text,
        # not date.string -- dateval stays [0, 0, 0, False] in that case.
        date = {"dateval": [0, 0, 0, False], "text": "7 Mai 1604"}
        assert _year_from_date(date) == "1604"

    def test_no_year_returns_empty_string(self):
        date = {"dateval": [0, 0, 0, False], "text": "unknown"}
        assert _year_from_date(date) == ""

    def test_missing_date_returns_empty_string(self):
        assert _year_from_date({}) == ""
