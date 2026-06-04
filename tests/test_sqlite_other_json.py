"""
Unit tests for JSON denormalization of reporef_list (source), text (note),
and urls for non-place entities (repository, person).

Regression tests for the bug where _merge_into stored these without the
_class key required by Gramps's JSON deserializer.
"""

from gramps_mcp._gramps_sqlite import _build_gramps_json


def _build(obj_type, obj):
    return _build_gramps_json(
        obj_type, {"handle": "h_test", "gramps_id": "X0001", **obj}, None
    )


class TestRepoRefJson:
    def test_reporef_has_class(self):
        result = _build("source", {"reporef_list": [{"ref": "h_repo"}]})
        ref = result["reporef_list"][0]
        assert ref.get("_class") == "RepoRef", f"RepoRef missing _class: {ref}"

    def test_reporef_ref_preserved(self):
        result = _build("source", {"reporef_list": [{"ref": "abc123"}]})
        assert result["reporef_list"][0]["ref"] == "abc123"

    def test_reporef_media_type_has_class(self):
        result = _build("source", {"reporef_list": [{"ref": "h_repo"}]})
        mtype = result["reporef_list"][0].get("media_type", {})
        assert mtype.get("_class") == "SourceMediaType", f"media_type missing _class: {mtype}"

    def test_reporef_callno_mapped_to_call_number(self):
        result = _build("source", {"reporef_list": [{"ref": "h_repo", "callno": "ABC123"}]})
        ref = result["reporef_list"][0]
        assert ref.get("call_number") == "ABC123"
        assert "callno" not in ref

    def test_reporef_type_string_becomes_source_media_type(self):
        result = _build("source", {"reporef_list": [{"ref": "h_repo", "type": "Unknown"}]})
        mtype = result["reporef_list"][0].get("media_type", {})
        assert mtype.get("_class") == "SourceMediaType"
        assert "type" not in result["reporef_list"][0]

    def test_reporef_defaults_present(self):
        result = _build("source", {"reporef_list": [{"ref": "h_repo"}]})
        ref = result["reporef_list"][0]
        assert "call_number" in ref
        assert "note_list" in ref
        assert "private" in ref

    def test_empty_reporef_list(self):
        result = _build("source", {"reporef_list": []})
        assert result["reporef_list"] == []

    def test_multiple_reporefs_all_have_class(self):
        result = _build("source", {"reporef_list": [{"ref": "h1"}, {"ref": "h2"}]})
        for ref in result["reporef_list"]:
            assert ref.get("_class") == "RepoRef"
            assert ref.get("media_type", {}).get("_class") == "SourceMediaType"


class TestNoteTextJson:
    def test_note_text_has_class(self):
        result = _build("note", {"text": {"string": "Hello world", "tags": []}})
        assert result["text"].get("_class") == "StyledText", (
            f"StyledText missing _class: {result['text']}"
        )

    def test_note_text_string_preserved(self):
        result = _build("note", {"text": {"string": "Hello world"}})
        assert result["text"]["string"] == "Hello world"

    def test_note_text_tags_default_empty(self):
        result = _build("note", {"text": {"string": "Hello"}})
        assert result["text"]["tags"] == []

    def test_note_template_text_has_class(self):
        result = _build_gramps_json("note", {"handle": "h_t", "gramps_id": "N0"}, None)
        assert result["text"].get("_class") == "StyledText"


class TestUrlAllEntitiesJson:
    def test_repository_url_has_class(self):
        result = _build("repository", {"urls": [{"path": "https://example.com", "desc": "Test"}]})
        url = result["urls"][0]
        assert url.get("_class") == "Url", f"Url missing _class for repository: {url}"

    def test_repository_url_type_has_class(self):
        result = _build("repository", {"urls": [{"path": "https://example.com", "desc": "Test"}]})
        assert result["urls"][0]["type"]["_class"] == "UrlType"

    def test_repository_url_description_normalized(self):
        result = _build("repository", {"urls": [
            {"path": "https://x.com", "description": "Desc", "type": "Unknown"}
        ]})
        url = result["urls"][0]
        assert url["desc"] == "Desc"
        assert "description" not in url
        assert url["type"]["_class"] == "UrlType"

    def test_person_url_has_class(self):
        result = _build("person", {"urls": [{"path": "https://example.com", "desc": "Test"}]})
        url = result["urls"][0]
        assert url.get("_class") == "Url", f"Url missing _class for person: {url}"

    def test_person_url_href_normalized(self):
        result = _build("person", {"urls": [{"href": "https://example.com", "description": "Me"}]})
        url = result["urls"][0]
        assert url["path"] == "https://example.com"
        assert "href" not in url

    def test_empty_urls_all_entities(self):
        for obj_type in ("repository", "person"):
            result = _build(obj_type, {"urls": []})
            assert result["urls"] == []
