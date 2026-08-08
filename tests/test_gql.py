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
Tests for _gql.py's minimal GQL filter, including the issue #19 fix:
an unrecognized top-level property name must raise a clear error instead
of silently matching nothing.
"""

import pytest

from gramps_mcp.client import GrampsAPIError
from gramps_mcp._gql import gql_match


_FAMILY = {
    "handle": "h_fam", "gramps_id": "F0001",
    "father_handle": "h_father", "mother_handle": "h_mother",
    "child_ref_list": [{"ref": "h_child"}],
    "media_list": [], "note_list": [],
}


class TestExistingSupportedSyntax:
    """Regression coverage — must keep working exactly as before."""

    def test_equality(self):
        assert gql_match(_FAMILY, "father_handle = 'h_father'") is True
        assert gql_match(_FAMILY, "father_handle = 'someone_else'") is False

    def test_inequality(self):
        assert gql_match(_FAMILY, "father_handle != 'someone_else'") is True

    def test_contains(self):
        assert gql_match(_FAMILY, "gramps_id ~ '000'") is True
        assert gql_match(_FAMILY, "gramps_id !~ '999'") is True

    def test_length_pseudo_property(self):
        assert gql_match(_FAMILY, "child_ref_list.length > 0") is True
        assert gql_match(_FAMILY, "media_list.length > 0") is False

    def test_and_or(self):
        assert gql_match(_FAMILY, "father_handle = 'h_father' and mother_handle = 'h_mother'") is True
        assert gql_match(_FAMILY, "father_handle = 'nope' or mother_handle = 'h_mother'") is True

    def test_boolean_truthy(self):
        assert gql_match(_FAMILY, "father_handle") is True
        assert gql_match(_FAMILY, "media_list") is False

    def test_array_index(self):
        assert gql_match(_FAMILY, "child_ref_list[0].ref = 'h_child'") is True

    def test_empty_expression_matches_everything(self):
        assert gql_match(_FAMILY, "") is True

    def test_valid_field_with_none_value_does_not_raise(self):
        # A recognized field whose value happens to be None/missing at a
        # nested step is a legitimate non-match, not a syntax error.
        obj = {"handle": "h", "father_handle": None}
        assert gql_match(obj, "father_handle = 'x'") is False


class TestUnrecognizedFieldRaises:
    """Issue #19: unknown property names must raise, not silently return False."""

    def test_unrecognized_top_level_field_raises(self):
        # The exact repro from the issue: "father" isn't a real field on a
        # Family object (the real field is "father_handle") — this must be
        # reported as an error, not silently evaluated as "no match".
        with pytest.raises(GrampsAPIError, match="father"):
            gql_match(_FAMILY, "father.gramps_id = 'I0001'")

    def test_correct_field_name_does_not_raise(self):
        # The corrected query from the same issue must work without error.
        assert gql_match(_FAMILY, "father_handle = 'h_father'") is True

    def test_unrecognized_field_raises_even_in_and_expression(self):
        with pytest.raises(GrampsAPIError):
            gql_match(_FAMILY, "gramps_id = 'F0001' and nonexistent_field = 'x'")

    def test_unrecognized_field_raises_even_in_or_expression(self):
        with pytest.raises(GrampsAPIError):
            gql_match(_FAMILY, "nonexistent_field = 'x' or gramps_id = 'F0001'")
