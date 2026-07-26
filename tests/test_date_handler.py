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

"""Tests for date_handler.format_date, in particular range/span dates."""

from gramps_mcp.handlers.date_handler import format_date


def test_format_date_regular_single_date():
    date_obj = {"dateval": [15, 3, 1650, False], "modifier": 0, "quality": 0}
    assert format_date(date_obj) == "15 March 1650"


def test_format_date_range_shows_both_dates():
    # Gramps range dateval: [day1, month1, year1, slash1, day2, month2, year2, slash2]
    date_obj = {
        "dateval": [1, 1, 1589, False, 7, 1, 1589, False],
        "modifier": 4,
        "quality": 0,
    }
    assert format_date(date_obj) == "between 01 January 1589 and 07 January 1589"


def test_format_date_span_shows_both_dates():
    date_obj = {
        "dateval": [1, 1, 1580, False, 31, 12, 1590, False],
        "modifier": 5,
        "quality": 0,
    }
    assert format_date(date_obj) == "from 01 January 1580 to 31 December 1590"


def test_format_date_range_without_second_date_falls_back():
    # Short dateval (no second date present) must not crash.
    date_obj = {"dateval": [1, 1, 1589, False], "modifier": 4, "quality": 0}
    assert format_date(date_obj) == "between 01 January 1589"


def test_format_date_textonly_uses_free_text():
    # Gramps stores unparsed dates (e.g. "7 Mai 1604") under date.text with
    # modifier 6 ("textonly") and an empty dateval -- not under date.string.
    date_obj = {
        "dateval": [0, 0, 0, False],
        "modifier": 6,
        "quality": 0,
        "text": "7 Mai 1604",
    }
    assert format_date(date_obj) == "7 Mai 1604"
