"""
Unit tests for Place JSON denormalization in _build_gramps_json.

Regression tests for the bug where create_place stored PlaceRef, Url, and
PlaceName without the _class key required by Gramps's JSON deserializer,
causing KeyError: '_class' when opening the Pedigree/Diagram view.
"""

import json

import pytest
from pydantic import ValidationError

from conftest_sqlite import _make_in_memory_db
from gramps_mcp._gramps_sqlite import GrampsSqliteDB, _build_gramps_json, _denorm_date
from gramps_mcp.models.api_calls import ApiCalls
from gramps_mcp.models.parameters.place_params import PlaceSaveParams
from gramps_mcp.sqlite_client import GrampsSqliteClient


def _build_place(obj):
    return _build_gramps_json(
        "place", {"handle": "h_test", "gramps_id": "P9999", **obj}, None
    )


class TestPlaceRefJson:
    def test_placeref_has_class(self):
        result = _build_place({"placeref_list": [{"ref": "parent_handle"}]})
        ref = result["placeref_list"][0]
        assert ref.get("_class") == "PlaceRef", f"PlaceRef missing _class: {ref}"

    def test_placeref_date_has_class(self):
        result = _build_place({"placeref_list": [{"ref": "parent_handle"}]})
        date = result["placeref_list"][0].get("date", {})
        assert date.get("_class") == "Date", f"PlaceRef.date missing _class: {date}"

    def test_placeref_ref_preserved(self):
        result = _build_place({"placeref_list": [{"ref": "abc123"}]})
        assert result["placeref_list"][0]["ref"] == "abc123"

    def test_placeref_with_existing_date_preserves_dateval(self):
        result = _build_place({"placeref_list": [{"ref": "abc123", "date": {
            "dateval": [1, 1, 2000, False], "modifier": 0, "quality": 0, "string": ""
        }}]})
        ref = result["placeref_list"][0]
        assert ref["_class"] == "PlaceRef"
        assert ref["date"]["_class"] == "Date"
        assert ref["date"]["dateval"] == [1, 1, 2000, False]

    def test_empty_placeref_list(self):
        result = _build_place({"placeref_list": []})
        assert result["placeref_list"] == []

    def test_multiple_placerefs_all_have_class(self):
        result = _build_place({"placeref_list": [{"ref": "h1"}, {"ref": "h2"}]})
        for ref in result["placeref_list"]:
            assert ref.get("_class") == "PlaceRef"
            assert ref.get("date", {}).get("_class") == "Date"


class TestUrlJson:
    def test_url_has_class(self):
        result = _build_place({"urls": [{"path": "https://example.com", "desc": "Test"}]})
        url = result["urls"][0]
        assert url.get("_class") == "Url", f"Url missing _class: {url}"

    def test_url_type_is_url_type_object(self):
        result = _build_place({"urls": [{"path": "https://example.com", "desc": "Test"}]})
        url_type = result["urls"][0].get("type", {})
        assert isinstance(url_type, dict), f"Url.type must be dict, got {url_type!r}"
        assert url_type.get("_class") == "UrlType", f"Url.type missing _class: {url_type}"

    def test_url_path_preserved(self):
        result = _build_place({"urls": [{"path": "https://example.com", "desc": "Test"}]})
        assert result["urls"][0]["path"] == "https://example.com"

    def test_url_href_normalized_to_path(self):
        result = _build_place({"urls": [{"href": "https://example.com", "description": "Test"}]})
        url = result["urls"][0]
        assert url["path"] == "https://example.com"
        assert "href" not in url

    def test_url_description_normalized_to_desc(self):
        result = _build_place({"urls": [{"path": "https://x.com", "description": "My Desc"}]})
        assert result["urls"][0]["desc"] == "My Desc"
        assert "description" not in result["urls"][0]

    def test_url_int_type_becomes_url_type_obj(self):
        result = _build_place({"urls": [{"path": "https://x.com", "desc": "X", "type": 0}]})
        url_type = result["urls"][0]["type"]
        assert url_type["_class"] == "UrlType"
        assert url_type["value"] == 0

    def test_url_string_type_becomes_url_type_obj(self):
        result = _build_place({"urls": [{"path": "https://x.com", "desc": "X", "type": "Unknown"}]})
        url_type = result["urls"][0]["type"]
        assert url_type["_class"] == "UrlType"

    def test_empty_url_list(self):
        result = _build_place({"urls": []})
        assert result["urls"] == []

    def test_multiple_urls_all_have_class(self):
        result = _build_place({"urls": [
            {"path": "https://a.com", "desc": "A"},
            {"href": "https://b.com", "description": "B"},
        ]})
        for url in result["urls"]:
            assert url.get("_class") == "Url"
            assert url.get("type", {}).get("_class") == "UrlType"


class TestPlaceNameJson:
    def test_name_has_class(self):
        result = _build_place({"name": {"value": "Naugard"}})
        assert result["name"].get("_class") == "PlaceName"

    def test_name_date_has_class(self):
        result = _build_place({"name": {"value": "Naugard"}})
        date = result["name"].get("date", {})
        assert date.get("_class") == "Date", f"PlaceName.date missing _class: {date}"

    def test_name_value_preserved(self):
        result = _build_place({"name": {"value": "Naugard"}})
        assert result["name"]["value"] == "Naugard"

    def test_name_lang_present(self):
        result = _build_place({"name": {"value": "Naugard"}})
        assert "lang" in result["name"]

    def test_default_template_name_has_valid_structure(self):
        """Even without passing name, the template must have valid PlaceName."""
        result = _build_gramps_json("place", {"handle": "h_t", "gramps_id": "P0"}, None)
        name = result["name"]
        assert name.get("_class") == "PlaceName"
        date = name.get("date", {})
        assert date.get("_class") == "Date", f"Template PlaceName.date missing _class: {date}"


class TestAltNamesJson:
    """Regression tests for issue #57: alt_names stored as raw strings."""

    def test_strings_become_place_names(self):
        result = _build_place({"alt_names": ["Foo", "Bar"]})
        assert [n["value"] for n in result["alt_names"]] == ["Foo", "Bar"]
        for name in result["alt_names"]:
            assert name["_class"] == "PlaceName"
            assert name["date"]["_class"] == "Date"
            assert name["lang"] == ""

    def test_dict_entry_keeps_lang_and_date(self):
        result = _build_place({"alt_names": [{
            "value": "Neuencamp",
            "lang": "de",
            "date": {"dateval": [1, 1, 1900, False], "modifier": 0, "quality": 0, "string": ""},
        }]})
        name = result["alt_names"][0]
        assert name["_class"] == "PlaceName"
        assert name["value"] == "Neuencamp"
        assert name["lang"] == "de"
        assert name["date"]["dateval"] == [1, 1, 1900, False]

    def test_mixed_strings_and_dicts(self):
        result = _build_place({"alt_names": ["Foo", {"value": "Bar", "lang": "pl"}]})
        assert [n["_class"] for n in result["alt_names"]] == ["PlaceName", "PlaceName"]
        assert [n["lang"] for n in result["alt_names"]] == ["", "pl"]

    def test_empty_list_clears_alt_names(self):
        existing = _build_place({"alt_names": ["Foo"]})
        result = _build_gramps_json("place", {"handle": "h_test", "alt_names": []}, existing)
        assert result["alt_names"] == []

    def test_update_without_alt_names_keeps_existing(self):
        existing = _build_place({"alt_names": [{"value": "Foo", "lang": "de"}]})
        result = _build_gramps_json("place", {"handle": "h_test", "lat": "1.0"}, existing)
        assert result["alt_names"] == existing["alt_names"]


class TestAltNamesParams:
    """The Web backend sends model_dump() as-is, so the model must normalise too."""

    def test_string_dumped_as_place_name(self):
        params = PlaceSaveParams(place_type="Village", alt_names=["Foo"])
        assert params.model_dump(exclude_none=True)["alt_names"] == [
            {"_class": "PlaceName", "value": "Foo"}
        ]

    def test_dict_entry_accepted_and_keeps_fields(self):
        params = PlaceSaveParams(
            place_type="Village", alt_names=[{"value": "Foo", "lang": "de"}]
        )
        assert params.alt_names == [{"_class": "PlaceName", "value": "Foo", "lang": "de"}]

    def test_omitted_stays_none(self):
        assert PlaceSaveParams(place_type="Village").alt_names is None

    def test_caller_class_cannot_override_place_name(self):
        params = PlaceSaveParams(
            place_type="Village", alt_names=[{"_class": "Location", "value": "Foo"}]
        )
        assert params.alt_names[0]["_class"] == "PlaceName"

    @pytest.mark.parametrize(
        "bad_entry",
        ["", "   ", {"lang": "de"}, {"value": ""}, {"value": None}, {"value": 123}],
    )
    def test_invalid_value_rejected(self, bad_entry):
        with pytest.raises(ValidationError, match="alt_names"):
            PlaceSaveParams(place_type="Village", alt_names=[bad_entry])

    def test_non_dict_date_rejected(self):
        """A string date would otherwise be stored as an empty date, reporting success."""
        with pytest.raises(ValidationError, match="date"):
            PlaceSaveParams(
                place_type="Village", alt_names=[{"value": "Foo", "date": "1900"}]
            )

    def test_validation_is_idempotent(self):
        """FastMCP validates once and validate_api_call_params rebuilds the model."""
        first = PlaceSaveParams(place_type="Village", alt_names=["Foo"])
        second = PlaceSaveParams(**first.model_dump(exclude_none=True))
        assert second.alt_names == first.alt_names


class TestAltNameDateText:
    """Both date text spellings must survive: 'string' (input) and 'text' (Gramps JSON)."""

    def test_date_text_key_survives(self):
        result = _build_place({"alt_names": [{
            "value": "Foo", "date": {"dateval": [0, 0, 1700, False], "text": "um 1700"},
        }]})
        assert result["alt_names"][0]["date"]["text"] == "um 1700"

    def test_date_string_key_survives(self):
        result = _build_place({"alt_names": [{
            "value": "Foo", "date": {"dateval": [0, 0, 1700, False], "string": "um 1700"},
        }]})
        assert result["alt_names"][0]["date"]["text"] == "um 1700"

    def test_already_denormalised_date_is_unchanged(self):
        date = {"dateval": [0, 0, 1700, False], "text": "um 1700", "calendar": 1}
        once = _denorm_date(date)
        assert once["text"] == "um 1700"
        assert _denorm_date(once) == once


class TestAltNamesRoundTrip:
    @pytest.mark.asyncio
    async def test_put_place_persists_place_name_objects(self):
        """Issue #57 repro: the update call must store objects in json_data."""
        # Reason: the shared sqlite_client fixture is session-scoped, so a write
        # through it would leak into other tests. This DB is private to the test.
        conn = _make_in_memory_db()
        client = object.__new__(GrampsSqliteClient)
        client._db = GrampsSqliteDB(conn=conn, db_path=":memory:", read_only=False)
        client._db_path = ":memory:"
        client._report_cache = {}

        await client.make_api_call(
            ApiCalls.PUT_PLACE,
            params=PlaceSaveParams(
                handle="h_pl_berlin", place_type="City", alt_names=["Foo", "Bar"]
            ),
            handle="h_pl_berlin",
        )

        row = conn.execute(
            "SELECT json_data FROM place WHERE handle = ?", ("h_pl_berlin",)
        ).fetchone()
        stored = json.loads(row[0])["alt_names"]
        assert [n["value"] for n in stored] == ["Foo", "Bar"]
        assert all(n["_class"] == "PlaceName" for n in stored)
        assert all(n["date"]["_class"] == "Date" for n in stored)
