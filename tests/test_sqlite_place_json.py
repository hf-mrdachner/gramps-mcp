"""
Unit tests for Place JSON denormalization in _build_gramps_json.

Regression tests for the bug where create_place stored PlaceRef, Url, and
PlaceName without the _class key required by Gramps's JSON deserializer,
causing KeyError: '_class' when opening the Pedigree/Diagram view.
"""

from gramps_mcp._gramps_sqlite import _build_gramps_json


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
