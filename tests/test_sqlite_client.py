"""
Tests for GrampsSqliteClient — full read/write Gramps SQLite backend.

All tests use the synthetic in-memory fixture from conftest_sqlite.py.
No real Gramps database, no GTK, no external server required.

Run with:
    uv run pytest tests/test_sqlite_client.py -v
"""

import json

import pytest

from gramps_mcp.client import GrampsAPIError
from gramps_mcp.models.api_calls import ApiCalls

# Fixtures from conftest_sqlite.py are auto-discovered by pytest


# ===========================================================================
# Normalisation: types become plain strings
# ===========================================================================


class TestNormalisation:
    def test_event_type_is_string(self, sqlite_db):
        ev = sqlite_db.get("event", "h_ev_birth_john")
        assert ev["type"] == "Birth"

    def test_death_type_is_string(self, sqlite_db):
        ev = sqlite_db.get("event", "h_ev_death_john")
        assert ev["type"] == "Death"

    def test_marriage_type_is_string(self, sqlite_db):
        ev = sqlite_db.get("event", "h_ev_marriage")
        assert ev["type"] == "Marriage"

    def test_family_has_relationship_not_type(self, sqlite_db):
        fam = sqlite_db.get("family", "h_fa_smith")
        assert "relationship" in fam
        assert fam["relationship"] == "Married"
        assert "type" not in fam

    def test_date_has_string_not_text(self, sqlite_db):
        ev = sqlite_db.get("event", "h_ev_birth_john")
        date = ev["date"]
        assert "string" in date
        assert "text" not in date

    def test_date_dateval(self, sqlite_db):
        ev = sqlite_db.get("event", "h_ev_birth_john")
        assert ev["date"]["dateval"] == [15, 6, 1950, False]

    def test_no_class_keys_in_person(self, sqlite_db):
        person = sqlite_db.get("person", "h_pe_john")
        assert "_class" not in person
        assert "_class" not in person["primary_name"]

    def test_event_ref_role_is_string(self, sqlite_db):
        person = sqlite_db.get("person", "h_pe_john")
        role = person["event_ref_list"][0]["role"]
        assert isinstance(role, str)
        assert role == "Primary"

    def test_child_ref_frel_is_string(self, sqlite_db):
        fam = sqlite_db.get("family", "h_fa_smith")
        frel = fam["child_ref_list"][0]["frel"]
        assert isinstance(frel, str)
        assert frel == "Birth"


# ===========================================================================
# Read: persons
# ===========================================================================


class TestReadPerson:
    def test_get_by_handle(self, sqlite_db):
        p = sqlite_db.get("person", "h_pe_john")
        assert p["gramps_id"] == "I0001"
        assert p["handle"] == "h_pe_john"

    def test_get_by_gramps_id(self, sqlite_db):
        p = sqlite_db.get_by_id("person", "I0001")
        assert p is not None
        assert p["handle"] == "h_pe_john"

    def test_missing_returns_none(self, sqlite_db):
        assert sqlite_db.get("person", "no_such_handle") is None

    def test_person_name(self, sqlite_db):
        pn = sqlite_db.get("person", "h_pe_john")["primary_name"]
        assert pn["first_name"] == "John Robert"
        assert pn["surname_list"][0]["surname"] == "Smith"

    def test_person_gender(self, sqlite_db):
        assert sqlite_db.get("person", "h_pe_john")["gender"] == 1
        assert sqlite_db.get("person", "h_pe_jane")["gender"] == 0

    def test_birth_ref_index(self, sqlite_db):
        p = sqlite_db.get("person", "h_pe_john")
        assert p["birth_ref_index"] == 0
        assert p["event_ref_list"][0]["ref"] == "h_ev_birth_john"

    def test_death_ref_index(self, sqlite_db):
        assert sqlite_db.get("person", "h_pe_john")["death_ref_index"] == 1

    def test_no_death_for_jane(self, sqlite_db):
        assert sqlite_db.get("person", "h_pe_jane")["death_ref_index"] == -1

    def test_family_list(self, sqlite_db):
        p = sqlite_db.get("person", "h_pe_john")
        assert "h_fa_smith" in p["family_list"]

    def test_parent_family_list(self, sqlite_db):
        child = sqlite_db.get("person", "h_pe_child")
        assert "h_fa_smith" in child["parent_family_list"]

    def test_media_list(self, sqlite_db):
        media = sqlite_db.get("person", "h_pe_john")["media_list"]
        assert len(media) == 1
        assert media[0]["ref"] == "h_me_photo"

    def test_count(self, sqlite_db):
        assert sqlite_db.count("person") == 3

    def test_all(self, sqlite_db):
        assert len(sqlite_db.all("person")) == 3


# ===========================================================================
# Read: events, families, places
# ===========================================================================


class TestReadEvent:
    def test_event_place_handle(self, sqlite_db):
        ev = sqlite_db.get("event", "h_ev_birth_john")
        assert ev["place"] == "h_pl_berlin"

    def test_event_no_place(self, sqlite_db):
        ev = sqlite_db.get("event", "h_ev_birth_jane")
        assert not ev["place"]

    def test_event_count(self, sqlite_db):
        assert sqlite_db.count("event") == 5


class TestReadFamily:
    def test_parents(self, sqlite_db):
        fam = sqlite_db.get("family", "h_fa_smith")
        assert fam["father_handle"] == "h_pe_john"
        assert fam["mother_handle"] == "h_pe_jane"

    def test_children(self, sqlite_db):
        refs = sqlite_db.get("family", "h_fa_smith")["child_ref_list"]
        assert len(refs) == 1
        assert refs[0]["ref"] == "h_pe_child"


class TestReadPlace:
    def test_place_title(self, sqlite_db):
        p = sqlite_db.get("place", "h_pl_berlin")
        assert p["title"] == "Berlin, Germany"

    def test_place_type(self, sqlite_db):
        p = sqlite_db.get("place", "h_pl_berlin")
        assert p["place_type"] == "City"


# ===========================================================================
# API: make_api_call  (read)
# ===========================================================================


class TestApiRead:
    @pytest.mark.asyncio
    async def test_get_person(self, sqlite_client):
        p = await sqlite_client.make_api_call(ApiCalls.GET_PERSON, handle="h_pe_john")
        assert p["gramps_id"] == "I0001"

    @pytest.mark.asyncio
    async def test_gql_filter_matches_correct_field(self, sqlite_client):
        # End-to-end repro from issue #19: the corrected query (father_handle,
        # not father.gramps_id) must work through the full make_api_call path.
        families = await sqlite_client.make_api_call(
            ApiCalls.GET_FAMILIES, params={"gql": "father_handle = 'h_pe_john'"}
        )
        assert any(f["handle"] == "h_fa_smith" for f in families)

    @pytest.mark.asyncio
    async def test_gql_filter_unrecognized_field_raises(self, sqlite_client):
        # End-to-end repro from issue #19: an unrecognized property name
        # (father.gramps_id — the real field is father_handle) must raise a
        # clear error through the full make_api_call path, not silently
        # return zero results.
        with pytest.raises(GrampsAPIError, match="father"):
            await sqlite_client.make_api_call(
                ApiCalls.GET_FAMILIES, params={"gql": "father.gramps_id = 'I0001'"}
            )

    @pytest.mark.asyncio
    async def test_get_person_extended(self, sqlite_client):
        p = await sqlite_client.make_api_call(
            ApiCalls.GET_PERSON, params={"extend": "all"}, handle="h_pe_john"
        )
        ext = p["extended"]
        assert len(ext["events"]) == 2
        assert len(ext["families"]) == 1

    @pytest.mark.asyncio
    async def test_get_family_extended(self, sqlite_client):
        fam = await sqlite_client.make_api_call(
            ApiCalls.GET_FAMILY, params={"extend": "all"}, handle="h_fa_smith"
        )
        ext = fam["extended"]
        assert ext["father"]["gramps_id"] == "I0001"
        assert ext["mother"]["gramps_id"] == "I0002"
        assert len(ext["children"]) == 1

    @pytest.mark.asyncio
    async def test_missing_handle_raises(self, sqlite_client):
        with pytest.raises(GrampsAPIError, match="not found"):
            await sqlite_client.make_api_call(ApiCalls.GET_PERSON, handle="nope")

    @pytest.mark.asyncio
    async def test_list_people(self, sqlite_client):
        people = await sqlite_client.make_api_call(
            ApiCalls.GET_PEOPLE, params={"pagesize": 100}
        )
        assert len(people) == 3

    @pytest.mark.asyncio
    async def test_filter_by_gramps_id(self, sqlite_client):
        result = await sqlite_client.make_api_call(
            ApiCalls.GET_PEOPLE, params={"gramps_id": "I0001"}
        )
        assert len(result) == 1
        assert result[0]["handle"] == "h_pe_john"

    @pytest.mark.asyncio
    async def test_name_search(self, sqlite_client):
        result = await sqlite_client.make_api_call(
            ApiCalls.GET_PEOPLE, params={"query": "smith", "pagesize": 20}
        )
        ids = {p["gramps_id"] for p in result}
        assert "I0001" in ids
        assert "I0003" in ids

    @pytest.mark.asyncio
    async def test_gql_filter(self, sqlite_client):
        result = await sqlite_client.make_api_call(
            ApiCalls.GET_PEOPLE, params={"gql": "gender = 1", "pagesize": 100}
        )
        assert all(p["gender"] == 1 for p in result)

    @pytest.mark.asyncio
    async def test_tree_info(self, sqlite_client):
        info = await sqlite_client.make_api_call(ApiCalls.GET_TREE)
        assert info["usage_people"] == 3
        assert info["usage_events"] == 5
        assert info["usage_families"] == 1

    @pytest.mark.asyncio
    async def test_with_headers(self, sqlite_client):
        result, headers = await sqlite_client.make_api_call(
            ApiCalls.GET_PEOPLE, params={"pagesize": 100}, with_headers=True
        )
        assert len(result) == 3
        assert headers["x-total-count"] == "3"

    @pytest.mark.asyncio
    async def test_with_headers_total_count_reflects_all_matches_not_just_page(
        self, sqlite_client
    ):
        """issue #39: x-total-count must be the true match count, not len(page)."""
        result, headers = await sqlite_client.make_api_call(
            ApiCalls.GET_PEOPLE, params={"pagesize": 1}, with_headers=True
        )
        assert len(result) == 1
        assert headers["x-total-count"] == "3"


# ===========================================================================
# API: full-text search (GET_SEARCH)  — issue #21
# ===========================================================================


class TestSearch:
    """
    "0001" matches three fixture records that fall through to the default
    gramps_id text match in _search(): family F0001, citation C0001, and
    media O0001 — one match in each of three different object types, which
    are scanned in the order person/family/event/place/source/citation/
    note/media/repository (see conftest_sqlite.py).
    """

    @pytest.mark.asyncio
    async def test_search_total_count_reflects_all_types_not_just_page(
        self, sqlite_client
    ):
        _, headers = await sqlite_client.make_api_call(
            ApiCalls.GET_SEARCH,
            params={"query": "0001", "pagesize": 1},
            with_headers=True,
        )
        assert headers["x-total-count"] == "3"

    @pytest.mark.asyncio
    async def test_search_does_not_stop_at_first_matching_type(self, sqlite_client):
        result = await sqlite_client.make_api_call(
            ApiCalls.GET_SEARCH, params={"query": "0001", "pagesize": 3}
        )
        object_types = {r["object_type"] for r in result}
        assert object_types == {"family", "citation", "media"}


# ===========================================================================
# API: search perf prefilter (issue #38) — SQL-level candidate prescreen
# must not silently drop matches the old full-Python-scan found.
# ===========================================================================


class TestSearchCandidatesPrefilter:
    """
    _search()'s SQL prefilter (issue #38) scans json_data with a fast SQL
    substring test before paying the JSON-parse + normalise cost, and must
    stay a strict superset of what the old full-Python-scan found. Two ways
    that can silently break:

    1. Standard EventType display names ("Birth", "Death", ...) are resolved
       from a numeric code during normalisation and are never literal text
       in json_data -- a naive raw-text prefilter would miss them.
    2. SQLite's built-in LIKE only case-folds ASCII; a prefilter must match
       Python's str.lower() (Unicode-aware) or German names lose matches.
    """

    def test_event_type_search_matches_via_type_code_not_literal_text(self):
        """
        Independent throwaway DB with a "clean" handle/description (no
        literal "birth" substring anywhere in json_data) -- the shared
        session fixture's event handles (e.g. h_ev_birth_john) would let
        this pass on the raw-text prefilter alone and prove nothing about
        the type-code special case this test exists to guard.
        """
        import sqlite3

        from gramps_mcp._gramps_sqlite import GrampsSqliteDB, _denorm_date

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE event (
                handle VARCHAR(50) PRIMARY KEY NOT NULL,
                json_data TEXT, gramps_id TEXT,
                description TEXT, place VARCHAR(50),
                change INTEGER DEFAULT 0, private INTEGER DEFAULT 0
            );
            """
        )
        data = {
            "_class": "Event", "handle": "h_clean_001", "gramps_id": "E0099",
            "type": {"_class": "EventType", "value": 12, "string": ""},
            "date": _denorm_date({}), "description": "", "place": None,
            "citation_list": [], "note_list": [], "media_list": [],
            "attribute_list": [], "tag_list": [], "change": 0, "private": False,
        }
        conn.execute(
            "INSERT INTO event (handle,gramps_id,json_data,description,change,private) "
            "VALUES (?,?,?,?,?,?)",
            ["h_clean_001", "E0099", json.dumps(data, ensure_ascii=False), "", 0, 0],
        )
        conn.commit()
        db = GrampsSqliteDB(conn=conn, db_path=":memory:", read_only=False)

        # Sanity check: the raw-text prefilter alone must NOT find this row,
        # otherwise this test wouldn't actually exercise the type-code path.
        assert db._store("event").search("birth") == []

        candidates = db.search_candidates("event", "birth")

        assert [c["gramps_id"] for c in candidates] == ["E0099"]

    def test_ci_contains_is_unicode_case_insensitive(self):
        from gramps_mcp._gramps_sqlite import _ci_contains

        assert _ci_contains('{"first_name": "Jürgen Müller"}', "müller")
        assert _ci_contains('{"first_name": "Jürgen MÜLLER"}', "müller")
        assert not _ci_contains('{"first_name": "Jürgen Müller"}', "schmidt")
        assert not _ci_contains(None, "muller")

    def test_search_candidates_finds_unicode_name(self):
        """
        Independent throwaway DB (not the shared session fixture), so this
        doesn't disturb the exact person-count assertions elsewhere.
        """
        import sqlite3

        from gramps_mcp._gramps_sqlite import GrampsSqliteDB, _denorm_date

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE person (
                handle VARCHAR(50) PRIMARY KEY NOT NULL,
                given_name TEXT, surname TEXT, json_data TEXT,
                gramps_id TEXT, gender INTEGER,
                death_ref_index INTEGER DEFAULT -1, birth_ref_index INTEGER DEFAULT -1,
                change INTEGER DEFAULT 0, private INTEGER DEFAULT 0
            );
            """
        )
        data = {
            "_class": "Person", "handle": "h1", "gramps_id": "I0099", "gender": 1,
            "primary_name": {
                "_class": "Name", "first_name": "Jürgen",
                "surname_list": [{
                    "_class": "Surname", "surname": "Müller", "prefix": "",
                    "primary": True, "connector": "",
                    "origintype": {
                        "_class": "NameOriginType", "value": 1, "string": "",
                    },
                }],
                "suffix": "", "title": "", "call": "", "nick": "", "famnick": "",
                "group_as": "", "sort_as": 0, "display_as": 0, "private": False,
                "citation_list": [], "note_list": [],
                "type": {"_class": "NameType", "value": 2, "string": ""},
                "date": _denorm_date({}),
            },
            "alternate_names": [], "death_ref_index": -1, "birth_ref_index": -1,
            "event_ref_list": [], "family_list": [], "parent_family_list": [],
            "media_list": [], "address_list": [], "attribute_list": [], "urls": [],
            "lds_ord_list": [], "citation_list": [], "note_list": [], "tag_list": [],
            "person_ref_list": [], "change": 0, "private": False,
        }
        conn.execute(
            "INSERT INTO person "
            "(handle,gramps_id,json_data,given_name,surname,gender,change,private) "
            "VALUES (?,?,?,?,?,?,?,?)",
            ["h1", "I0099", json.dumps(data, ensure_ascii=False),
             "Jürgen", "Müller", 1, 0, 0],
        )
        conn.commit()
        db = GrampsSqliteDB(conn=conn, db_path=":memory:", read_only=False)

        candidates = db.search_candidates("person", "müller")

        assert [c["gramps_id"] for c in candidates] == ["I0099"]


# ===========================================================================
# API: _text_match must also scan alternate_names — issue #42
#
# search_candidates() (the SQL prefilter) scans raw json_data, so a query
# that only matches an alternate_names entry survives the prefilter. The
# exact _text_match() check ran afterwards, though, and only ever inspected
# primary_name — so the person was found by the prefilter, then silently
# dropped by the exact filter and never reached the caller.
# ===========================================================================


class TestTextMatchAlternateNames:
    def _make_client_with_alt_name_person(self):
        import sqlite3

        from gramps_mcp._gramps_sqlite import GrampsSqliteDB, _denorm_date
        from gramps_mcp.sqlite_client import GrampsSqliteClient

        def _name(first, surname):
            return {
                "_class": "Name", "first_name": first,
                "surname_list": [{
                    "_class": "Surname", "surname": surname, "prefix": "",
                    "primary": True, "connector": "",
                    "origintype": {
                        "_class": "NameOriginType", "value": 1, "string": "",
                    },
                }],
                "suffix": "", "title": "", "call": "", "nick": "", "famnick": "",
                "group_as": "", "sort_as": 0, "display_as": 0, "private": False,
                "citation_list": [], "note_list": [],
                "type": {"_class": "NameType", "value": 2, "string": ""},
                "date": _denorm_date({}),
            }

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE person (
                handle VARCHAR(50) PRIMARY KEY NOT NULL,
                given_name TEXT, surname TEXT, json_data TEXT,
                gramps_id TEXT, gender INTEGER,
                death_ref_index INTEGER DEFAULT -1, birth_ref_index INTEGER DEFAULT -1,
                change INTEGER DEFAULT 0, private INTEGER DEFAULT 0
            );
            """
        )
        data = {
            "_class": "Person", "handle": "h1", "gramps_id": "I0099", "gender": 1,
            "primary_name": _name("Robert", "Stevens"),
            "alternate_names": [_name("Bob", "Stevenson")],
            "death_ref_index": -1, "birth_ref_index": -1,
            "event_ref_list": [], "family_list": [], "parent_family_list": [],
            "media_list": [], "address_list": [], "attribute_list": [], "urls": [],
            "lds_ord_list": [], "citation_list": [], "note_list": [], "tag_list": [],
            "person_ref_list": [], "change": 0, "private": False,
        }
        conn.execute(
            "INSERT INTO person "
            "(handle,gramps_id,json_data,given_name,surname,gender,change,private) "
            "VALUES (?,?,?,?,?,?,?,?)",
            ["h1", "I0099", json.dumps(data, ensure_ascii=False),
             "Robert", "Stevens", 1, 0, 0],
        )
        conn.commit()
        db = GrampsSqliteDB(conn=conn, db_path=":memory:", read_only=False)
        client = object.__new__(GrampsSqliteClient)
        client._db = db
        client._db_path = ":memory:"
        client._report_cache = {}
        return client

    @pytest.mark.asyncio
    async def test_find_anything_matches_alternate_surname(self):
        client = self._make_client_with_alt_name_person()
        result = await client.make_api_call(
            ApiCalls.GET_SEARCH, params={"query": "stevenson", "pagesize": 20}
        )
        assert [r["object"]["gramps_id"] for r in result] == ["I0099"]

    @pytest.mark.asyncio
    async def test_find_anything_matches_alternate_given_name(self):
        client = self._make_client_with_alt_name_person()
        result = await client.make_api_call(
            ApiCalls.GET_SEARCH, params={"query": "bob", "pagesize": 20}
        )
        assert [r["object"]["gramps_id"] for r in result] == ["I0099"]

    @pytest.mark.asyncio
    async def test_person_list_name_filter_matches_alternate_name(self):
        client = self._make_client_with_alt_name_person()
        result = await client.make_api_call(
            ApiCalls.GET_PEOPLE, params={"name": "stevenson"}
        )
        assert [r["gramps_id"] for r in result] == ["I0099"]

    @pytest.mark.asyncio
    async def test_primary_name_still_matches(self):
        client = self._make_client_with_alt_name_person()
        result = await client.make_api_call(
            ApiCalls.GET_SEARCH, params={"query": "stevens", "pagesize": 20}
        )
        assert [r["object"]["gramps_id"] for r in result] == ["I0099"]


# ===========================================================================
# API: make_api_call  (write)
# ===========================================================================


class TestApiWrite:
    @pytest.mark.asyncio
    async def test_create_person(self, sqlite_client):
        new_person = {
            "gender": 0,
            "primary_name": {
                "first_name": "Anna",
                "surname_list": [{"surname": "Müller", "primary": True,
                                   "prefix": "", "connector": ""}],
                "suffix": "", "title": "", "call": "", "nick": "",
                "type": "Birth Name",
            },
            "event_ref_list": [],
            "family_list": [], "parent_family_list": [],
            "note_list": [], "citation_list": [], "media_list": [],
            "address_list": [], "urls": [],
        }
        result = await sqlite_client.make_api_call(ApiCalls.POST_PEOPLE, params=new_person)
        assert "handle" in result
        assert result["gramps_id"] != ""
        assert result["gender"] == 0
        # Verify it's retrievable
        fetched = await sqlite_client.make_api_call(
            ApiCalls.GET_PERSON, handle=result["handle"]
        )
        assert fetched["primary_name"]["first_name"] == "Anna"

    @pytest.mark.asyncio
    async def test_update_person_gender(self, sqlite_client):
        # Create a person to update
        new_p = await sqlite_client.make_api_call(ApiCalls.POST_PEOPLE, params={
            "gender": 1,
            "primary_name": {"first_name": "Karl", "surname_list": [
                {"surname": "Test", "primary": True, "prefix": "", "connector": ""}
            ], "suffix": "", "title": "", "call": "", "nick": "", "type": "Birth Name"},
            "event_ref_list": [], "family_list": [], "parent_family_list": [],
            "note_list": [], "citation_list": [], "media_list": [],
            "address_list": [], "urls": [],
        })
        handle = new_p["handle"]
        updated = await sqlite_client.make_api_call(
            ApiCalls.PUT_PERSON, params={"gender": 0}, handle=handle
        )
        assert updated["gender"] == 0
        fetched = await sqlite_client.make_api_call(ApiCalls.GET_PERSON, handle=handle)
        assert fetched["gender"] == 0

    @pytest.mark.asyncio
    async def test_create_event(self, sqlite_client):
        new_event = {
            "type": "Baptism",
            "date": {"dateval": [5, 3, 1951, False], "modifier": 0,
                     "quality": 0, "string": ""},
            "description": "Baptism at St. Mary's",
            "place": "h_pl_berlin",
            "note_list": [], "citation_list": [],
        }
        result = await sqlite_client.make_api_call(ApiCalls.POST_EVENTS, params=new_event)
        assert result["gramps_id"].startswith("E")
        fetched = await sqlite_client.make_api_call(
            ApiCalls.GET_EVENT, handle=result["handle"]
        )
        assert fetched["type"] == "Baptism"
        assert fetched["description"] == "Baptism at St. Mary's"

    @pytest.mark.asyncio
    async def test_create_note(self, sqlite_client):
        new_note = {
            "type": "General",
            "text": {"string": "Research note about John Smith's origins."},
        }
        result = await sqlite_client.make_api_call(ApiCalls.POST_NOTES, params=new_note)
        assert result["gramps_id"].startswith("N")
        fetched = await sqlite_client.make_api_call(
            ApiCalls.GET_NOTE, handle=result["handle"]
        )
        text = fetched.get("text", {})
        if isinstance(text, dict):
            assert "Smith" in text.get("string", "")
        else:
            assert "Smith" in str(text)

    @pytest.mark.asyncio
    async def test_create_citation_persists_page(self, sqlite_client):
        # Regression: "page" was in _dispatch's pagination-param strip set,
        # so Citation.page (a real data field) was silently dropped on write.
        result = await sqlite_client.make_api_call(ApiCalls.POST_CITATIONS, params={
            "source_handle": "h_so_civil",
            "page": "Sterberegister, Urkunde Nr. 90",
            "confidence": 2,
            "note_list": [], "media_list": [], "attribute_list": [],
        })
        assert result["page"] == "Sterberegister, Urkunde Nr. 90"
        fetched = await sqlite_client.make_api_call(
            ApiCalls.GET_CITATION, handle=result["handle"]
        )
        assert fetched["page"] == "Sterberegister, Urkunde Nr. 90"

    @pytest.mark.asyncio
    async def test_update_citation_page(self, sqlite_client):
        created = await sqlite_client.make_api_call(ApiCalls.POST_CITATIONS, params={
            "source_handle": "h_so_civil",
            "page": "old page",
            "confidence": 2,
            "note_list": [], "media_list": [], "attribute_list": [],
        })
        handle = created["handle"]
        updated = await sqlite_client.make_api_call(
            ApiCalls.PUT_CITATION, params={"page": "new page"}, handle=handle
        )
        assert updated["page"] == "new page"
        fetched = await sqlite_client.make_api_call(ApiCalls.GET_CITATION, handle=handle)
        assert fetched["page"] == "new page"

    @pytest.mark.asyncio
    async def test_new_handle_is_unique(self, sqlite_client):
        h1 = sqlite_client._db.new_handle()
        h2 = sqlite_client._db.new_handle()
        assert h1 != h2

    @pytest.mark.asyncio
    async def test_gramps_id_increments(self, sqlite_client):
        p1 = await sqlite_client.make_api_call(ApiCalls.POST_PEOPLE, params={
            "gender": 2,
            "primary_name": {"first_name": "X", "surname_list": [
                {"surname": "Y", "primary": True, "prefix": "", "connector": ""}
            ], "suffix": "", "title": "", "call": "", "nick": "", "type": "Birth Name"},
            "event_ref_list": [], "family_list": [], "parent_family_list": [],
            "note_list": [], "citation_list": [], "media_list": [],
            "address_list": [], "urls": [],
        })
        p2 = await sqlite_client.make_api_call(ApiCalls.POST_PEOPLE, params={
            "gender": 2,
            "primary_name": {"first_name": "A", "surname_list": [
                {"surname": "B", "primary": True, "prefix": "", "connector": ""}
            ], "suffix": "", "title": "", "call": "", "nick": "", "type": "Birth Name"},
            "event_ref_list": [], "family_list": [], "parent_family_list": [],
            "note_list": [], "citation_list": [], "media_list": [],
            "address_list": [], "urls": [],
        })
        n1 = int(p1["gramps_id"][1:])
        n2 = int(p2["gramps_id"][1:])
        assert n2 == n1 + 1


# ===========================================================================
# Timelines
# ===========================================================================


class TestTimeline:
    @pytest.mark.asyncio
    async def test_person_timeline_is_list(self, sqlite_client):
        result = await sqlite_client.make_api_call(
            ApiCalls.GET_PERSON_TIMELINE, handle="h_pe_john"
        )
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_person_timeline_has_birth(self, sqlite_client):
        result = await sqlite_client.make_api_call(
            ApiCalls.GET_PERSON_TIMELINE, handle="h_pe_john"
        )
        types = {e["type"] for e in result}
        assert "Birth" in types

    @pytest.mark.asyncio
    async def test_person_timeline_includes_marriage(self, sqlite_client):
        result = await sqlite_client.make_api_call(
            ApiCalls.GET_PERSON_TIMELINE, handle="h_pe_john"
        )
        types = {e["type"] for e in result}
        assert "Marriage" in types

    @pytest.mark.asyncio
    async def test_family_timeline(self, sqlite_client):
        result = await sqlite_client.make_api_call(
            ApiCalls.GET_FAMILY_TIMELINE, handle="h_fa_smith"
        )
        assert isinstance(result, list)
        assert len(result) >= 1


# ===========================================================================
# Client factory: auto-detection
# ===========================================================================


class TestReadOnlyMode:
    def test_read_only_flag_stored(self, sqlite_db):
        from gramps_mcp._gramps_sqlite import GrampsSqliteDB
        assert hasattr(sqlite_db, "_read_only")

    @pytest.mark.asyncio
    async def test_put_raises_when_read_only(self, tmp_path):
        """Writing to a read-only DB raises GrampsAPIError."""
        import sqlite3
        from gramps_mcp._gramps_sqlite import _load_sqlite
        # Create a minimal empty SQLite file
        db_path = tmp_path / "ro_test.sqlite"
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "CREATE TABLE person (handle TEXT PRIMARY KEY, gramps_id TEXT, "
            "json_data TEXT, given_name TEXT, surname TEXT, gender INTEGER, "
            "birth_ref_index INTEGER DEFAULT -1, death_ref_index INTEGER DEFAULT -1, "
            "change INTEGER DEFAULT 0, private INTEGER DEFAULT 0)"
        )
        conn.commit()
        conn.close()

        db = _load_sqlite(str(db_path), read_only=True)
        with pytest.raises(GrampsAPIError, match="read-only"):
            db.put("person", {"gender": 1, "primary_name": {
                "first_name": "Test",
                "surname_list": [{"surname": "X", "primary": True,
                                   "prefix": "", "connector": ""}],
            }})
        db.close()


class TestClientFactory:
    def test_sqlite_path_detected(self, tmp_path):
        from gramps_mcp.client import _is_sqlite_path
        db = tmp_path / "sqlite.db"
        db.write_bytes(b"")
        assert _is_sqlite_path(str(db))

    def test_gpkg_not_sqlite(self):
        from gramps_mcp.client import _is_sqlite_path
        assert not _is_sqlite_path("/some/path/tree.gpkg")

    def test_directory_with_sqlite_db(self, tmp_path):
        from gramps_mcp.client import _is_sqlite_path
        (tmp_path / "sqlite.db").write_bytes(b"")
        assert _is_sqlite_path(str(tmp_path))

    def test_gramps_file_not_sqlite(self):
        from gramps_mcp.client import _is_sqlite_path
        assert not _is_sqlite_path("/some/path/tree.gramps")


class TestSqliteCloseNoop:
    """Regression: with_client calls close() after every tool call.

    GrampsSqliteClient.close() must be a no-op so the singleton connection
    stays alive across multiple tool calls within the same session.
    """

    @pytest.mark.asyncio
    async def test_close_does_not_destroy_connection(self, sqlite_client):
        """close() must leave the connection usable for the next query."""
        from gramps_mcp.models.api_calls import ApiCalls
        await sqlite_client.close()
        # The connection must still work after close()
        result = await sqlite_client.make_api_call(ApiCalls.GET_PEOPLE)
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_multiple_queries_after_multiple_closes(self, sqlite_client):
        """Simulates with_client calling close() after each tool call."""
        from gramps_mcp.models.api_calls import ApiCalls
        for _ in range(3):
            await sqlite_client.close()
            result = await sqlite_client.make_api_call(ApiCalls.GET_PEOPLE)
            assert isinstance(result, list), "Connection must survive repeated close() calls"
