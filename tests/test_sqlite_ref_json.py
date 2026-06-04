"""
Unit tests for JSON denormalization of remaining reference/object list fields:
media_list (MediaRef), attribute_list (Attribute), address_list (Address),
person_ref_list (PersonRef), lds_ord_list (LdsOrd).

Regression tests to ensure _merge_into never stores typed Gramps objects
without the _class key required by Gramps's JSON deserializer.
"""

from gramps_mcp._gramps_sqlite import _build_gramps_json


def _build(obj_type, obj):
    return _build_gramps_json(
        obj_type, {"handle": "h_test", "gramps_id": "X0001", **obj}, None
    )


class TestMediaRefJson:
    def test_media_list_item_has_class(self):
        result = _build("person", {"media_list": [{"ref": "h_media"}]})
        item = result["media_list"][0]
        assert item.get("_class") == "MediaRef", f"MediaRef missing _class: {item}"

    def test_media_ref_preserved(self):
        result = _build("person", {"media_list": [{"ref": "abc123"}]})
        assert result["media_list"][0]["ref"] == "abc123"

    def test_media_list_defaults_present(self):
        result = _build("person", {"media_list": [{"ref": "h_media"}]})
        item = result["media_list"][0]
        assert "rect" in item
        assert "private" in item
        assert "note_list" in item
        assert "citation_list" in item

    def test_media_list_on_event(self):
        result = _build("event", {"media_list": [{"ref": "h_media"}]})
        assert result["media_list"][0].get("_class") == "MediaRef"

    def test_media_list_on_source(self):
        result = _build("source", {"media_list": [{"ref": "h_media"}]})
        assert result["media_list"][0].get("_class") == "MediaRef"

    def test_empty_media_list(self):
        result = _build("person", {"media_list": []})
        assert result["media_list"] == []

    def test_multiple_media_refs_all_have_class(self):
        result = _build("person", {"media_list": [{"ref": "h1"}, {"ref": "h2"}]})
        for item in result["media_list"]:
            assert item.get("_class") == "MediaRef"


class TestAttributeJson:
    def test_attribute_has_class(self):
        result = _build("person", {"attribute_list": [{"type": "Height", "value": "180cm"}]})
        item = result["attribute_list"][0]
        assert item.get("_class") == "Attribute", f"Attribute missing _class: {item}"

    def test_attribute_value_preserved(self):
        result = _build("person", {"attribute_list": [{"type": "Height", "value": "180cm"}]})
        assert result["attribute_list"][0]["value"] == "180cm"

    def test_attribute_type_string_becomes_attribute_type_obj(self):
        result = _build("person", {"attribute_list": [{"type": "Height", "value": "x"}]})
        atype = result["attribute_list"][0]["type"]
        assert isinstance(atype, dict)
        assert atype.get("_class") == "AttributeType"
        assert atype.get("string") == "Height"

    def test_attribute_type_dict_gets_class(self):
        result = _build("person", {"attribute_list": [
            {"type": {"value": 0, "string": "Custom"}, "value": "x"}
        ]})
        atype = result["attribute_list"][0]["type"]
        assert atype.get("_class") == "AttributeType"

    def test_attribute_defaults_present(self):
        result = _build("person", {"attribute_list": [{"type": "X", "value": "y"}]})
        item = result["attribute_list"][0]
        assert "private" in item
        assert "citation_list" in item
        assert "note_list" in item

    def test_attribute_on_event(self):
        result = _build("event", {"attribute_list": [{"type": "Role", "value": "Witness"}]})
        assert result["attribute_list"][0].get("_class") == "Attribute"

    def test_attribute_on_media(self):
        result = _build("media", {"attribute_list": [{"type": "Medienart", "value": "image"}]})
        assert result["attribute_list"][0].get("_class") == "Attribute"

    def test_empty_attribute_list(self):
        result = _build("person", {"attribute_list": []})
        assert result["attribute_list"] == []


class TestAddressJson:
    def test_address_has_class(self):
        result = _build("person", {"address_list": [{"city": "Berlin"}]})
        item = result["address_list"][0]
        assert item.get("_class") == "Address", f"Address missing _class: {item}"

    def test_address_city_preserved(self):
        result = _build("person", {"address_list": [{"city": "Berlin"}]})
        assert result["address_list"][0]["city"] == "Berlin"

    def test_address_date_has_class(self):
        result = _build("person", {"address_list": [{"city": "Berlin"}]})
        date = result["address_list"][0].get("date", {})
        assert date.get("_class") == "Date", f"Address.date missing _class: {date}"

    def test_address_defaults_present(self):
        result = _build("person", {"address_list": [{"city": "Berlin"}]})
        item = result["address_list"][0]
        for field in ("street", "locality", "city", "county", "state",
                      "country", "postal", "phone", "private",
                      "citation_list", "note_list"):
            assert field in item, f"Address missing field: {field}"

    def test_address_on_repository(self):
        result = _build("repository", {"address_list": [{"city": "Rostock"}]})
        assert result["address_list"][0].get("_class") == "Address"

    def test_empty_address_list(self):
        result = _build("person", {"address_list": []})
        assert result["address_list"] == []


class TestPersonRefJson:
    def test_person_ref_has_class(self):
        result = _build("person", {"person_ref_list": [{"ref": "h_person", "rel": "DNA"}]})
        item = result["person_ref_list"][0]
        assert item.get("_class") == "PersonRef", f"PersonRef missing _class: {item}"

    def test_person_ref_ref_preserved(self):
        result = _build("person", {"person_ref_list": [{"ref": "abc123", "rel": "Sibling"}]})
        assert result["person_ref_list"][0]["ref"] == "abc123"

    def test_person_ref_rel_preserved(self):
        result = _build("person", {"person_ref_list": [{"ref": "h", "rel": "DNA"}]})
        assert result["person_ref_list"][0]["rel"] == "DNA"

    def test_person_ref_defaults_present(self):
        result = _build("person", {"person_ref_list": [{"ref": "h", "rel": "X"}]})
        item = result["person_ref_list"][0]
        assert "private" in item
        assert "note_list" in item
        assert "citation_list" in item

    def test_empty_person_ref_list(self):
        result = _build("person", {"person_ref_list": []})
        assert result["person_ref_list"] == []

    def test_multiple_person_refs_all_have_class(self):
        result = _build("person", {"person_ref_list": [
            {"ref": "h1", "rel": "DNA"}, {"ref": "h2", "rel": "Sibling"}
        ]})
        for item in result["person_ref_list"]:
            assert item.get("_class") == "PersonRef"


class TestLdsOrdJson:
    def test_lds_ord_has_class(self):
        result = _build("person", {"lds_ord_list": [{"type": "Baptism"}]})
        item = result["lds_ord_list"][0]
        assert item.get("_class") == "LdsOrd", f"LdsOrd missing _class: {item}"

    def test_lds_ord_date_has_class(self):
        result = _build("person", {"lds_ord_list": [{"type": "Baptism"}]})
        date = result["lds_ord_list"][0].get("date", {})
        assert date.get("_class") == "Date"

    def test_empty_lds_ord_list(self):
        result = _build("person", {"lds_ord_list": []})
        assert result["lds_ord_list"] == []
