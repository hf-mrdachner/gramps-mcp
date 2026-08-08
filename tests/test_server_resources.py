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
Tests for the resource registry wired into run_stdio_server() (issue #19):
resources decorated onto the FastMCP `app` object (HTTP transport) were
never reachable over the stdio transport, since run_stdio_server() builds a
separate low-level Server that never learned about them. list_resources()/
read_resource() are the shared source of truth used by both.
"""

from gramps_mcp.server import RESOURCE_REGISTRY, list_resources, read_resource


class TestResourceRegistry:
    def test_gql_documentation_is_registered(self):
        assert "gql://documentation" in RESOURCE_REGISTRY

    def test_all_app_resources_are_registered(self):
        # Every resource previously only reachable via @app.resource(...)
        # (HTTP transport) must also be in the shared registry used by the
        # stdio transport.
        expected = {
            "gql://documentation",
            "gramps://usage-guide",
            "gramps://tools/person",
            "gramps://tools/event",
            "gramps://tools/citation",
            "gramps://tools/family",
            "gramps://tools/search",
            "gramps://tools/admin",
        }
        assert expected == set(RESOURCE_REGISTRY.keys())


class TestListResources:
    def test_returns_one_resource_per_registry_entry(self):
        resources = list_resources()
        assert len(resources) == len(RESOURCE_REGISTRY)

    def test_each_resource_has_matching_uri(self):
        resources = list_resources()
        uris = {str(r.uri) for r in resources}
        assert uris == set(RESOURCE_REGISTRY.keys())


class TestReadResource:
    def test_reads_gql_documentation_content(self):
        content = read_resource("gql://documentation")
        assert "GQL" in content

    def test_reads_usage_guide_content(self):
        content = read_resource("gramps://usage-guide")
        assert len(content) > 0

    def test_unknown_uri_raises(self):
        import pytest

        with pytest.raises(ValueError, match="Unknown resource"):
            read_resource("gramps://nonexistent")
