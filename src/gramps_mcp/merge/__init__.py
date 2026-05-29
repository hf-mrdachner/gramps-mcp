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

"""Duplicate-person detection and merge operations for Gramps SQLite databases."""

from .detect import DuplicateCandidate, find_duplicate_persons, normalize
from .operations import load_backups, merge_persons, split_person

__all__ = [
    "DuplicateCandidate",
    "find_duplicate_persons",
    "normalize",
    "merge_persons",
    "split_person",
    "load_backups",
]
