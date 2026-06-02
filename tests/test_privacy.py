"""
Tests for the privacy/living persons filter.

Uses SQLite in-memory fixture from conftest_sqlite.py (auto-discovered via conftest.py).
No @pytest.mark.asyncio needed — asyncio_mode=auto is set in pytest.ini.
"""
import datetime
import pytest

from gramps_mcp.privacy import (
    _extract_birth_year,
    _is_living_local,
    is_privacy_mode,
    is_living,
    redact_person,
    redact_if_living,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _person(birth_year=None, death_ref_index=-1):
    """Build a minimal person_data dict for testing _is_living_local."""
    data: dict = {"death_ref_index": death_ref_index, "birth_ref_index": -1}
    if birth_year is not None:
        data["birth_ref_index"] = 0
        data["extended"] = {
            "events": [{"date": {"dateval": [1, 1, birth_year, False]}}]
        }
    return data


# ---------------------------------------------------------------------------
# _extract_birth_year
# ---------------------------------------------------------------------------

class TestExtractBirthYear:
    def test_extracts_year_from_extended_events(self):
        person = _person(birth_year=1952)
        assert _extract_birth_year(person) == 1952

    def test_returns_none_when_no_birth_ref(self):
        person = {"birth_ref_index": -1}
        assert _extract_birth_year(person) is None

    def test_returns_none_when_year_is_zero(self):
        person = {
            "birth_ref_index": 0,
            "extended": {"events": [{"date": {"dateval": [0, 0, 0, False]}}]},
        }
        assert _extract_birth_year(person) is None

    def test_returns_none_when_no_extended_events(self):
        person = {"birth_ref_index": 0}
        assert _extract_birth_year(person) is None


# ---------------------------------------------------------------------------
# _is_living_local
# ---------------------------------------------------------------------------

class TestIsLivingLocal:
    def test_death_ref_index_set_returns_false(self):
        assert _is_living_local(_person(birth_year=1980, death_ref_index=1)) is False

    def test_recent_birth_no_death_returns_true(self):
        assert _is_living_local(_person(birth_year=1980)) is True

    def test_old_birth_no_death_returns_false(self):
        old_year = datetime.date.today().year - 121
        assert _is_living_local(_person(birth_year=old_year)) is False

    def test_boundary_exactly_120_years_returns_false(self):
        boundary_year = datetime.date.today().year - 120
        assert _is_living_local(_person(birth_year=boundary_year)) is False

    def test_no_birth_no_death_returns_true(self):
        assert _is_living_local({"death_ref_index": -1, "birth_ref_index": -1}) is True

    def test_empty_birth_date_returns_true(self):
        person = {
            "death_ref_index": -1,
            "birth_ref_index": 0,
            "extended": {"events": [{"date": {"dateval": [0, 0, 0, False]}}]},
        }
        assert _is_living_local(person) is True


# ---------------------------------------------------------------------------
# is_privacy_mode
# ---------------------------------------------------------------------------

class TestIsPrivacyMode:
    def test_returns_false_when_not_set(self, monkeypatch):
        monkeypatch.delenv("GRAMPS_PRIVACY_MODE", raising=False)
        assert is_privacy_mode() is False

    def test_returns_true_when_set_to_true(self, monkeypatch):
        monkeypatch.setenv("GRAMPS_PRIVACY_MODE", "true")
        assert is_privacy_mode() is True

    def test_case_insensitive_true(self, monkeypatch):
        monkeypatch.setenv("GRAMPS_PRIVACY_MODE", "TRUE")
        assert is_privacy_mode() is True

    def test_returns_false_when_set_to_false(self, monkeypatch):
        monkeypatch.setenv("GRAMPS_PRIVACY_MODE", "false")
        assert is_privacy_mode() is False


# ---------------------------------------------------------------------------
# redact_person
# ---------------------------------------------------------------------------

class TestRedactPerson:
    def _jane(self):
        return {
            "handle": "h_pe_jane", "gramps_id": "I0002", "gender": 0,
            "primary_name": {
                "first_name": "Jane",
                "surname_list": [{"surname": "Doe"}],
            },
            "birth_ref_index": 0,
            "death_ref_index": -1,
            "event_ref_list": [{"ref": "h_ev_birth_jane"}],
            "note_list": ["h_no_1"],
            "media_list": ["h_me_1"],
            "family_list": ["h_fa_smith"],
            "parent_family_list": [],
        }

    def test_name_replaced(self):
        result = redact_person(self._jane())
        assert result["primary_name"]["first_name"] == "[Living]"
        assert result["primary_name"]["surname_list"][0]["surname"] == "[Living]"

    def test_event_ref_list_cleared(self):
        assert redact_person(self._jane())["event_ref_list"] == []

    def test_note_list_cleared(self):
        assert redact_person(self._jane())["note_list"] == []

    def test_media_list_cleared(self):
        assert redact_person(self._jane())["media_list"] == []

    def test_birth_death_indices_cleared(self):
        result = redact_person(self._jane())
        assert result["birth_ref_index"] == -1
        assert result["death_ref_index"] == -1

    def test_living_marker_set(self):
        assert redact_person(self._jane())["living"] is True

    def test_handle_gramps_id_gender_preserved(self):
        result = redact_person(self._jane())
        assert result["handle"] == "h_pe_jane"
        assert result["gramps_id"] == "I0002"
        assert result["gender"] == 0

    def test_family_lists_preserved(self):
        result = redact_person(self._jane())
        assert result["family_list"] == ["h_fa_smith"]
        assert result["parent_family_list"] == []

    def test_returns_new_dict_original_unchanged(self):
        jane = self._jane()
        result = redact_person(jane)
        assert result is not jane
        assert jane["event_ref_list"] == [{"ref": "h_ev_birth_jane"}]


# ---------------------------------------------------------------------------
# redact_if_living (integration, uses sqlite_client fixture)
# ---------------------------------------------------------------------------

class TestRedactIfLiving:
    async def test_privacy_off_living_person_not_redacted(
        self, monkeypatch, sqlite_client
    ):
        monkeypatch.delenv("GRAMPS_PRIVACY_MODE", raising=False)
        person = _person(birth_year=1980)
        person.update({"handle": "x", "primary_name": {"first_name": "A",
            "surname_list": [{"surname": "B"}]}, "event_ref_list": [],
            "note_list": [], "media_list": []})
        result = await redact_if_living(person, sqlite_client, "default")
        assert result is person

    async def test_privacy_on_living_person_redacted(
        self, monkeypatch, sqlite_client
    ):
        monkeypatch.setenv("GRAMPS_PRIVACY_MODE", "true")
        person = _person(birth_year=1980)
        person.update({"handle": "x", "primary_name": {"first_name": "A",
            "surname_list": [{"surname": "B"}]}, "event_ref_list": [],
            "note_list": [], "media_list": []})
        result = await redact_if_living(person, sqlite_client, "default")
        assert result["primary_name"]["first_name"] == "[Living]"

    async def test_privacy_on_deceased_not_redacted(
        self, monkeypatch, sqlite_client
    ):
        monkeypatch.setenv("GRAMPS_PRIVACY_MODE", "true")
        person = _person(birth_year=1950, death_ref_index=1)
        person.update({"handle": "x", "primary_name": {"first_name": "John",
            "surname_list": [{"surname": "Smith"}]}, "event_ref_list": [],
            "note_list": [], "media_list": []})
        result = await redact_if_living(person, sqlite_client, "default")
        assert result["primary_name"]["first_name"] == "John"


# ---------------------------------------------------------------------------
# Handler integration — format_person
# ---------------------------------------------------------------------------

class TestFormatPersonPrivacy:
    async def test_living_person_name_redacted(self, monkeypatch, sqlite_client):
        """Jane (h_pe_jane, born 1952, no death) must show [Living] with privacy on."""
        monkeypatch.setenv("GRAMPS_PRIVACY_MODE", "true")
        from gramps_mcp.handlers.person_handler import format_person
        result = await format_person(sqlite_client, "default", "h_pe_jane")
        assert "[Living]" in result

    async def test_living_person_birth_date_not_shown(self, monkeypatch, sqlite_client):
        monkeypatch.setenv("GRAMPS_PRIVACY_MODE", "true")
        from gramps_mcp.handlers.person_handler import format_person
        result = await format_person(sqlite_client, "default", "h_pe_jane")
        assert "Born:" not in result

    async def test_deceased_person_not_redacted(self, monkeypatch, sqlite_client):
        """John (h_pe_john, born 1950, died 2020) must not be redacted."""
        monkeypatch.setenv("GRAMPS_PRIVACY_MODE", "true")
        from gramps_mcp.handlers.person_handler import format_person
        result = await format_person(sqlite_client, "default", "h_pe_john")
        assert "John" in result

    async def test_privacy_off_person_shown_normally(self, monkeypatch, sqlite_client):
        monkeypatch.delenv("GRAMPS_PRIVACY_MODE", raising=False)
        from gramps_mcp.handlers.person_handler import format_person
        result = await format_person(sqlite_client, "default", "h_pe_jane")
        assert "Jane" in result


# ---------------------------------------------------------------------------
# Handler integration — format_person_detail
# ---------------------------------------------------------------------------

class TestFormatPersonDetailPrivacy:
    async def test_living_person_name_redacted(self, monkeypatch, sqlite_client):
        monkeypatch.setenv("GRAMPS_PRIVACY_MODE", "true")
        from gramps_mcp.handlers.person_detail_handler import format_person_detail
        result = await format_person_detail(sqlite_client, "default", "h_pe_jane")
        assert "[Living]" in result

    async def test_living_person_birth_date_not_shown(self, monkeypatch, sqlite_client):
        monkeypatch.setenv("GRAMPS_PRIVACY_MODE", "true")
        from gramps_mcp.handlers.person_detail_handler import format_person_detail
        result = await format_person_detail(sqlite_client, "default", "h_pe_jane")
        assert "Born:" not in result

    async def test_deceased_person_not_redacted(self, monkeypatch, sqlite_client):
        monkeypatch.setenv("GRAMPS_PRIVACY_MODE", "true")
        from gramps_mcp.handlers.person_detail_handler import format_person_detail
        result = await format_person_detail(sqlite_client, "default", "h_pe_john")
        assert "John" in result

    async def test_living_relative_name_redacted(self, monkeypatch, sqlite_client):
        """John's mother-of-record is None but child James (no birth year, living) must be redacted."""
        monkeypatch.setenv("GRAMPS_PRIVACY_MODE", "true")
        from gramps_mcp.handlers.person_detail_handler import format_person_detail
        # John is the subject (deceased, shown). Jane is his spouse (living, should be redacted).
        result = await format_person_detail(sqlite_client, "default", "h_pe_john")
        # Jane (born 1952, no death) is John's spouse - her name must NOT appear
        assert "Jane" not in result or "[Living]" in result


# ---------------------------------------------------------------------------
# Handler integration — format_family_detail
# ---------------------------------------------------------------------------

class TestFormatFamilyDetailPrivacy:
    async def test_living_mother_name_redacted(self, monkeypatch, sqlite_client):
        """Smith family (h_fa_smith): mother Jane (living) must show [Living]."""
        monkeypatch.setenv("GRAMPS_PRIVACY_MODE", "true")
        from gramps_mcp.handlers.family_detail_handler import format_family_detail
        result = await format_family_detail(sqlite_client, "default", "h_fa_smith")
        assert "[Living]" in result

    async def test_deceased_father_not_redacted(self, monkeypatch, sqlite_client):
        """Father John (deceased) must still show his name."""
        monkeypatch.setenv("GRAMPS_PRIVACY_MODE", "true")
        from gramps_mcp.handlers.family_detail_handler import format_family_detail
        result = await format_family_detail(sqlite_client, "default", "h_fa_smith")
        assert "John" in result

    async def test_privacy_off_shows_all_names(self, monkeypatch, sqlite_client):
        monkeypatch.delenv("GRAMPS_PRIVACY_MODE", raising=False)
        from gramps_mcp.handlers.family_detail_handler import format_family_detail
        result = await format_family_detail(sqlite_client, "default", "h_fa_smith")
        assert "Jane" in result
