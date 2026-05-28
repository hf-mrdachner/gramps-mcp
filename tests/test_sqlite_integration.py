"""
Integration tests for GrampsSqliteClient against a real Gramps database.

These tests run against your actual Gramps SQLite database to verify that:
  1. _load_sqlite() correctly opens and parses a real database
  2. _normalize() produces the dict shape our handlers expect
  3. Type mappings are correct (EventType values, FamilyRelType, etc.)
  4. All structural invariants hold across every object in the database

Run with:
    uv run pytest tests/test_sqlite_integration.py -v -m integration

Skip in CI (no real DB available):
    uv run pytest -m "not integration"

Configure the database path:
    GRAMPS_TEST_DB_PATH=C:/path/to/sqlite.db uv run pytest -m integration
"""

import os

import pytest

# ---------------------------------------------------------------------------
# Real database path
# ---------------------------------------------------------------------------

_DEFAULT_DB = (
    r"C:\Users\dachner\AppData\Roaming\gramps\grampsdb\6a1764f8\sqlite.db"
)
REAL_DB = os.environ.get("GRAMPS_TEST_DB_PATH", _DEFAULT_DB)

pytestmark = pytest.mark.integration


def _db_available() -> bool:
    return os.path.exists(REAL_DB)


skip_if_no_db = pytest.mark.skipif(
    not _db_available(),
    reason=f"Real Gramps DB not found at {REAL_DB}. "
           "Set GRAMPS_TEST_DB_PATH to override.",
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def real_client():
    """GrampsSqliteClient connected to the real Gramps database."""
    if not _db_available():
        pytest.skip(f"Real DB not found: {REAL_DB}")
    from gramps_mcp.sqlite_client import GrampsSqliteClient
    return GrampsSqliteClient(REAL_DB)


@pytest.fixture(scope="module")
def real_db(real_client):
    return real_client._db


# ---------------------------------------------------------------------------
# Loader / init
# ---------------------------------------------------------------------------

class TestLoader:
    @skip_if_no_db
    def test_loads_without_error(self, real_db):
        assert real_db is not None

    @skip_if_no_db
    def test_has_people(self, real_db):
        assert real_db.count("person") > 0

    @skip_if_no_db
    def test_has_families(self, real_db):
        assert real_db.count("family") > 0

    @skip_if_no_db
    def test_has_events(self, real_db):
        assert real_db.count("event") > 0

    @skip_if_no_db
    def test_counts_logged(self, real_db, capsys):
        # Just verify counts are reasonable
        people = real_db.count("person")
        events = real_db.count("event")
        print(f"\nDB stats: {people} people, {events} events, "
              f"{real_db.count('family')} families, "
              f"{real_db.count('place')} places, "
              f"{real_db.count('source')} sources")
        assert people > 0


# ---------------------------------------------------------------------------
# Structural invariants: Person
# ---------------------------------------------------------------------------

class TestPersonInvariants:
    @skip_if_no_db
    def test_all_persons_have_handle(self, real_db):
        for p in real_db.all("person"):
            assert "handle" in p, f"Missing handle: {p}"
            assert isinstance(p["handle"], str) and p["handle"]

    @skip_if_no_db
    def test_all_persons_have_gramps_id(self, real_db):
        for p in real_db.all("person"):
            assert "gramps_id" in p, f"Missing gramps_id: {p.get('handle')}"

    @skip_if_no_db
    def test_all_persons_have_primary_name(self, real_db):
        for p in real_db.all("person"):
            pn = p.get("primary_name")
            assert isinstance(pn, dict), (
                f"primary_name is not a dict for {p.get('gramps_id')}: {type(pn)}"
            )

    @skip_if_no_db
    def test_first_name_is_string(self, real_db):
        for p in real_db.all("person"):
            first = p.get("primary_name", {}).get("first_name")
            assert isinstance(first, str), (
                f"first_name not str for {p.get('gramps_id')}: {type(first)}"
            )

    @skip_if_no_db
    def test_surname_list_is_list(self, real_db):
        for p in real_db.all("person"):
            sl = p.get("primary_name", {}).get("surname_list")
            assert isinstance(sl, list), (
                f"surname_list not list for {p.get('gramps_id')}: {type(sl)}"
            )

    @skip_if_no_db
    def test_surname_list_entries_have_surname_key(self, real_db):
        for p in real_db.all("person"):
            for sn in p.get("primary_name", {}).get("surname_list", []):
                assert "surname" in sn, (
                    f"surname key missing in {p.get('gramps_id')}: {sn}"
                )
                assert isinstance(sn["surname"], str)

    @skip_if_no_db
    def test_gender_is_int(self, real_db):
        for p in real_db.all("person"):
            g = p.get("gender")
            assert isinstance(g, int) and g in (0, 1, 2, 3), (
                f"gender invalid for {p.get('gramps_id')}: {g}"
            )

    @skip_if_no_db
    def test_event_ref_list_is_list(self, real_db):
        for p in real_db.all("person"):
            erl = p.get("event_ref_list")
            assert isinstance(erl, list), (
                f"event_ref_list not list for {p.get('gramps_id')}: {type(erl)}"
            )

    @skip_if_no_db
    def test_event_ref_role_is_string(self, real_db):
        for p in real_db.all("person"):
            for eref in p.get("event_ref_list", []):
                role = eref.get("role")
                assert isinstance(role, str), (
                    f"event_ref role not str for {p.get('gramps_id')}: "
                    f"{type(role)} = {role!r}"
                )

    @skip_if_no_db
    def test_no_class_keys_in_person(self, real_db):
        for p in real_db.all("person"):
            assert "_class" not in p, (
                f"_class key leaked into person {p.get('gramps_id')}"
            )
            assert "_class" not in p.get("primary_name", {}), (
                f"_class in primary_name of {p.get('gramps_id')}"
            )

    @skip_if_no_db
    def test_family_list_contains_strings(self, real_db):
        for p in real_db.all("person"):
            for h in p.get("family_list", []):
                assert isinstance(h, str), (
                    f"family_list entry not str for {p.get('gramps_id')}: {type(h)}"
                )


# ---------------------------------------------------------------------------
# Structural invariants: Event
# ---------------------------------------------------------------------------

class TestEventInvariants:
    @skip_if_no_db
    def test_event_type_is_string(self, real_db):
        for ev in real_db.all("event"):
            t = ev.get("type")
            assert isinstance(t, str), (
                f"event type not str for {ev.get('gramps_id')}: "
                f"{type(t)} = {t!r}"
            )

    @skip_if_no_db
    def test_event_type_not_empty(self, real_db):
        unknown_count = sum(
            1 for ev in real_db.all("event")
            if ev.get("type", "").startswith("Unknown(")
        )
        total = real_db.count("event")
        pct = unknown_count / total * 100 if total else 0
        print(f"\n{unknown_count}/{total} events have unmapped type ({pct:.1f}%)")
        # Allow up to 5% unmapped — flags real mapping gaps
        assert pct < 5, f"Too many unmapped event types: {unknown_count}/{total}"

    @skip_if_no_db
    def test_event_date_has_string_key(self, real_db):
        for ev in real_db.all("event"):
            date = ev.get("date", {})
            if date:
                assert "string" in date, (
                    f"date missing 'string' key for event {ev.get('gramps_id')}: "
                    f"{list(date.keys())}"
                )
                assert "text" not in date, (
                    f"date has 'text' key (not normalised) for {ev.get('gramps_id')}"
                )

    @skip_if_no_db
    def test_event_date_dateval_is_list(self, real_db):
        for ev in real_db.all("event"):
            date = ev.get("date", {})
            if date:
                dv = date.get("dateval")
                assert isinstance(dv, list), (
                    f"dateval not list for {ev.get('gramps_id')}: {type(dv)}"
                )


# ---------------------------------------------------------------------------
# Structural invariants: Family
# ---------------------------------------------------------------------------

class TestFamilyInvariants:
    @skip_if_no_db
    def test_family_has_relationship_key(self, real_db):
        for fam in real_db.all("family"):
            assert "relationship" in fam, (
                f"family {fam.get('gramps_id')} missing 'relationship'"
            )

    @skip_if_no_db
    def test_family_has_no_type_key(self, real_db):
        for fam in real_db.all("family"):
            assert "type" not in fam, (
                f"family {fam.get('gramps_id')} has raw 'type' key "
                "(normalization failed)"
            )

    @skip_if_no_db
    def test_relationship_is_string(self, real_db):
        for fam in real_db.all("family"):
            r = fam.get("relationship")
            assert isinstance(r, str), (
                f"relationship not str for {fam.get('gramps_id')}: {type(r)}"
            )

    @skip_if_no_db
    def test_child_ref_frel_is_string(self, real_db):
        for fam in real_db.all("family"):
            for cref in fam.get("child_ref_list", []):
                frel = cref.get("frel")
                assert isinstance(frel, str), (
                    f"child_ref frel not str in {fam.get('gramps_id')}: "
                    f"{type(frel)} = {frel!r}"
                )


# ---------------------------------------------------------------------------
# Structural invariants: Place
# ---------------------------------------------------------------------------

class TestPlaceInvariants:
    @skip_if_no_db
    def test_place_type_is_string(self, real_db):
        for pl in real_db.all("place"):
            pt = pl.get("place_type")
            assert isinstance(pt, str), (
                f"place_type not str for {pl.get('gramps_id')}: {type(pt)}"
            )


# ---------------------------------------------------------------------------
# Cross-reference consistency
# ---------------------------------------------------------------------------

class TestCrossReferences:
    @skip_if_no_db
    def test_event_refs_resolve(self, real_db):
        """Every eventref in a person must point to an existing event."""
        missing = []
        for p in real_db.all("person"):
            for eref in p.get("event_ref_list", []):
                h = eref.get("ref", "")
                if h and real_db.get("event", h) is None:
                    missing.append((p.get("gramps_id"), h))
        assert not missing, f"Dangling event refs: {missing[:5]}"

    @skip_if_no_db
    def test_family_refs_resolve(self, real_db):
        """Every family_list handle in a person must exist."""
        missing = []
        for p in real_db.all("person"):
            for fh in p.get("family_list", []):
                if real_db.get("family", fh) is None:
                    missing.append((p.get("gramps_id"), fh))
        assert not missing, f"Dangling family refs: {missing[:5]}"

    @skip_if_no_db
    def test_birth_ref_index_in_range(self, real_db):
        """birth_ref_index must be -1 or a valid index into event_ref_list."""
        bad = []
        for p in real_db.all("person"):
            bi = p.get("birth_ref_index", -1)
            erl = p.get("event_ref_list", [])
            if bi != -1 and not (0 <= bi < len(erl)):
                bad.append((p.get("gramps_id"), bi, len(erl)))
        assert not bad, f"birth_ref_index out of range: {bad[:5]}"


# ---------------------------------------------------------------------------
# Handler smoke tests (do our dicts pass through the handlers without crash?)
# ---------------------------------------------------------------------------

class TestHandlerSmoke:
    @skip_if_no_db
    @pytest.mark.asyncio
    async def test_format_person_runs(self, real_client, real_db):
        """format_person handler must not crash on the first person."""
        from gramps_mcp.handlers.person_handler import format_person
        person = next(iter(real_db.all("person")))
        result = await format_person(real_client, "default", person["handle"])
        assert isinstance(result, str)
        assert len(result) > 0

    @skip_if_no_db
    @pytest.mark.asyncio
    async def test_format_family_runs(self, real_client, real_db):
        """format_family handler must not crash on the first family."""
        from gramps_mcp.handlers.family_handler import format_family
        family = next(iter(real_db.all("family")))
        result = await format_family(real_client, "default", family["handle"])
        assert isinstance(result, str)
        assert len(result) > 0

    @skip_if_no_db
    @pytest.mark.asyncio
    async def test_format_note_handler(self, real_client, real_db):
        """format_note handler must not crash."""
        if real_db.count("note") == 0:
            pytest.skip("No notes in DB")
        from gramps_mcp.handlers.note_handler import format_note
        note = next(iter(real_db.all("note")))
        result = await format_note(real_client, "default", note["handle"])
        assert isinstance(result, str)

    @skip_if_no_db
    @pytest.mark.asyncio
    async def test_format_place_inline(self, real_client, real_db):
        """format_place handler must not crash."""
        if real_db.count("place") == 0:
            pytest.skip("No places in DB")
        from gramps_mcp.handlers.place_handler import format_place
        place = next(iter(real_db.all("place")))
        result = await format_place(real_client, "default", place["handle"], inline=True)
        assert isinstance(result, str)
