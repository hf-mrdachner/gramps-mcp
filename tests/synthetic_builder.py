"""
Build fictional Gramps objects in the JSON format of a Gramps SQLite database.

Used by tests/synthetic_tree.py, which decides what the fictional tree contains.
"""

import json
import random
import sqlite3

from conftest_sqlite import _EMPTY_DATE, _cref, _eref, _name

_SEED = 20240101

_FIRST_NAMES = {
    0: ["Anna", "Klara", "Helene", "Ida", "Rosa", "Greta", "Paula", "Berta"],
    1: ["Karl", "Hans", "Ernst", "Franz", "Georg", "Paul", "Emil", "Rudolf"],
    2: ["Kim", "Sascha"],
}

# Gramps event type codes
MARRIAGE, BIRTH, DEATH = 1, 12, 13
# Gramps gender codes
FEMALE, MALE, UNKNOWN = 0, 1, 2


def _date(dateval, modifier=0, quality=0, text=""):
    """Build a Gramps Date object."""
    return {
        "_class": "Date",
        "calendar": 0,
        "modifier": modifier,
        "quality": quality,
        "dateval": dateval,
        "text": text,
        "sortval": 0,
        "newyear": 0,
        "format": None,
    }


def _date_variants(year):
    """Return one Date of every form Gramps stores, based around a year."""
    return [
        _date([15, 6, year, False]),
        _date([0, 0, year, False], modifier=3),  # about
        _date([0, 5, year, False], modifier=1),  # before
        _date([3, 0, year, False], modifier=2),  # after
        _date([1, 1, year, False, 31, 12, year + 1, False], modifier=4),  # between
        _date([1, 1, year, False, 1, 1, year + 5, False], modifier=5),  # from-to
        _date([0, 0, 0, False], modifier=6, text=f"Sommer {year}"),  # text only
        _date([2, 2, year, True], quality=1),  # dual-dated, estimated
        _date([20, 9, year, False], quality=2),  # calculated
        dict(_EMPTY_DATE),
    ]


def _typed(cls, value, string=""):
    """Build a Gramps typed value, such as an EventType."""
    return {"_class": cls, "value": value, "string": string}


def _record(cls, handle, gramps_id, **fields):
    """Build a Gramps object with the fields all object types have in common."""
    return {
        "_class": cls,
        "handle": handle,
        "gramps_id": gramps_id,
        "tag_list": [],
        "change": 0,
        "private": False,
        **fields,
    }


class TreeBuilder:
    """Collects fictional objects and writes them to an open database."""

    def __init__(self, conn: sqlite3.Connection):
        """
        Set up the builder.

        Args:
            conn (sqlite3.Connection): Database that already has the schema.
        """
        self.conn = conn
        self.rng = random.Random(_SEED)
        self.counters = {}
        self.variant = 0

    def _new_ids(self, prefix):
        """Return a new (handle, gramps_id) pair for a type prefix."""
        # Reason: start at 1000, above the IDs of the base fixture data.
        number = self.counters.get(prefix, 1000)
        self.counters[prefix] = number + 1
        return f"{self.rng.getrandbits(128):032x}", f"{prefix}{number}"

    def _insert(self, table, **columns):
        """Insert one row, serialising the json_data column."""
        columns["json_data"] = json.dumps(columns["json_data"], ensure_ascii=False)
        names = ",".join(columns)
        marks = ",".join("?" * len(columns))
        self.conn.execute(
            f"INSERT INTO {table} ({names}) VALUES ({marks})", list(columns.values())
        )

    def _reference(self, obj_handle, obj_class, ref_handle, ref_class):
        """Record a link in the reference table, as Gramps does."""
        self.conn.execute(
            "INSERT INTO reference VALUES (?, ?, ?, ?)",
            (obj_handle, obj_class, ref_handle, ref_class),
        )

    def next_date(self, year):
        """Return the next date form in rotation, so that all forms get used."""
        variants = _date_variants(year)
        date = variants[self.variant % len(variants)]
        self.variant += 1
        return date

    def place(self, name, type_code, parent=None, title=None):
        """Add a place, optionally enclosed by a parent place."""
        handle, gramps_id = self._new_ids("P")
        title = title or name

        def place_name(value, lang):
            """Build a PlaceName."""
            return {
                "_class": "PlaceName",
                "value": value,
                "date": dict(_EMPTY_DATE),
                "lang": lang,
            }

        refs = []
        if parent:
            refs = [{"_class": "PlaceRef", "ref": parent, "date": dict(_EMPTY_DATE)}]
        data = _record(
            "Place",
            handle,
            gramps_id,
            title=title,
            place_type=_typed("PlaceType", type_code),
            name=place_name(name, ""),
            alt_names=[place_name(f"Alt-{name}", "de")],
            placeref_list=refs,
            long="",
            lat="",
            code="",
            alt_loc=[],
            urls=[],
            media_list=[],
            citation_list=[],
            note_list=[],
        )
        self._insert(
            "place", handle=handle, gramps_id=gramps_id, title=title, json_data=data
        )
        return handle

    def repository(self, name, with_url):
        """Add a repository."""
        handle, gramps_id = self._new_ids("R")
        urls = []
        if with_url:
            urls = [
                {
                    "_class": "Url",
                    "path": "https://example.org/archiv",
                    "desc": "Fiktives Archiv",
                    "type": _typed("UrlType", 2),
                    "private": False,
                }
            ]
        data = _record(
            "Repository",
            handle,
            gramps_id,
            name=name,
            type=_typed("RepositoryType", 4),
            address_list=[],
            urls=urls,
            note_list=[],
        )
        self._insert(
            "repository", handle=handle, gramps_id=gramps_id, name=name, json_data=data
        )
        return handle

    def source(self, title, repository):
        """Add a source, optionally held by a repository."""
        handle, gramps_id = self._new_ids("S")
        details = {
            "title": title,
            "author": "Fiktiver Autor",
            "pubinfo": "Fiktivstadt, 1900",
            "abbrev": gramps_id,
        }
        refs = []
        if repository:
            refs = [
                {
                    "_class": "RepoRef",
                    "ref": repository,
                    "call_number": "Band 1",
                    "media_type": _typed("SourceMediaType", 2),
                    "note_list": [],
                    "private": False,
                }
            ]
        data = _record(
            "Source",
            handle,
            gramps_id,
            **details,
            note_list=[],
            media_list=[],
            reporef_list=refs,
            attribute_list=[],
        )
        self._insert(
            "source", handle=handle, gramps_id=gramps_id, **details, json_data=data
        )
        return handle

    def citation(self, source, number):
        """Add a citation of a source, with a varying confidence and date."""
        handle, gramps_id = self._new_ids("C")
        page = "" if number % 5 == 0 else f"Seite {number}"
        confidence = number % 5
        data = _record(
            "Citation",
            handle,
            gramps_id,
            page=page,
            confidence=confidence,
            source_handle=source,
            date=self.next_date(1880),
            note_list=[],
            media_list=[],
            attribute_list=[],
        )
        self._insert(
            "citation",
            handle=handle,
            gramps_id=gramps_id,
            page=page,
            confidence=confidence,
            source_handle=source,
            json_data=data,
        )
        self._reference(handle, "Citation", source, "Source")
        return handle

    def note(self, text, type_code, custom_type=""):
        """Add a note."""
        handle, gramps_id = self._new_ids("N")
        data = _record(
            "Note",
            handle,
            gramps_id,
            format=0,
            text={"_class": "StyledText", "string": text, "tags": []},
            type=_typed("NoteType", type_code, custom_type),
        )
        self._insert("note", handle=handle, gramps_id=gramps_id, json_data=data)
        return handle

    def media(self, path, mime):
        """Add a media object."""
        handle, gramps_id = self._new_ids("O")
        details = {
            "path": path,
            "mime": mime,
            "desc": "Fiktives Dokument",
            "checksum": "",
        }
        data = _record(
            "Media",
            handle,
            gramps_id,
            **details,
            date=self.next_date(1900),
            note_list=[],
            citation_list=[],
            attribute_list=[],
        )
        self._insert(
            "media", handle=handle, gramps_id=gramps_id, **details, json_data=data
        )
        return handle

    def event(self, type_code, year, place, citations, custom_type=""):
        """Add an event and return its handle."""
        handle, gramps_id = self._new_ids("E")
        description = "Fiktives Ereignis" if custom_type else ""
        data = _record(
            "Event",
            handle,
            gramps_id,
            type=_typed("EventType", type_code, custom_type),
            date=self.next_date(year),
            description=description,
            place=place,
            citation_list=citations,
            note_list=[],
            media_list=[],
            attribute_list=[],
        )
        self._insert(
            "event",
            handle=handle,
            gramps_id=gramps_id,
            description=description,
            place=place,
            json_data=data,
        )
        for citation in citations:
            self._reference(handle, "Event", citation, "Citation")
        return handle

    def person(self, gender, surname, birth_year, place, citations, notes, media):
        """Build a person with a birth and usually a death event, and return them."""
        handle, gramps_id = self._new_ids("I")
        number = int(gramps_id[1:])
        first_name = self.rng.choice(_FIRST_NAMES[gender])

        birth = self.event(BIRTH, birth_year, place, citations[:1])
        event_refs = [_eref(birth)]
        death_index = -1
        if number % 3:
            death = self.event(DEATH, birth_year + 60, None, [])
            event_refs.append(_eref(death))
            death_index = 1
        if number % 7 == 0:
            custom = self.event(0, birth_year + 30, place, [], custom_type="Hausbau")
            event_refs.append(_eref(custom))

        name = _name(first_name, surname)
        if number % 4 == 0:
            # A compound surname: two entries, only the first one is primary.
            second = dict(name["surname_list"][0], surname="Doppelname", primary=False)
            name["surname_list"] = name["surname_list"] + [second]

        media_refs = []
        if number % 6 == 0:
            media_refs = [
                {
                    "_class": "MediaRef",
                    "ref": media,
                    "rect": None,
                    "private": False,
                    "note_list": [],
                    "attribute_list": [],
                    "citation_list": [],
                }
            ]
        alternate = [_name(first_name, "Ehename", 3)] if number % 5 == 0 else []
        attributes = []
        if number % 9 == 0:
            attributes = [
                {
                    "_class": "Attribute",
                    "type": _typed("AttributeType", 0, "Beruf"),
                    "value": "Fiktiver Beruf",
                    "private": False,
                    "citation_list": [],
                    "note_list": [],
                }
            ]
        return _record(
            "Person",
            handle,
            gramps_id,
            gender=gender,
            primary_name=name,
            alternate_names=alternate,
            death_ref_index=death_index,
            birth_ref_index=0,
            event_ref_list=event_refs,
            family_list=[],
            parent_family_list=[],
            media_list=media_refs,
            address_list=[],
            attribute_list=attributes,
            urls=[],
            lds_ord_list=[],
            citation_list=citations[1:2],
            note_list=notes[:1] if number % 2 else [],
            person_ref_list=[],
            private=number % 11 == 0,
        )

    def save_person(self, data):
        """Write a person, once their family links are complete."""
        name = data["primary_name"]
        self._insert(
            "person",
            handle=data["handle"],
            gramps_id=data["gramps_id"],
            given_name=name["first_name"],
            surname=name["surname_list"][0]["surname"],
            gender=data["gender"],
            birth_ref_index=data["birth_ref_index"],
            death_ref_index=data["death_ref_index"],
            private=int(data["private"]),
            json_data=data,
        )
        for ref in data["event_ref_list"]:
            self._reference(data["handle"], "Person", ref["ref"], "Event")

    def family(self, father, mother, children, year, place, rel_type):
        """Add a family, linking its members in their person records."""
        handle, gramps_id = self._new_ids("F")
        event_refs = []
        if father and mother:
            marriage = self.event(MARRIAGE, year, place, [])
            event_refs = [_eref(marriage, role_val=8)]
        for parent in (father, mother):
            if parent:
                parent["family_list"].append(handle)
        for child in children:
            child["parent_family_list"].append(handle)
        father_handle = father["handle"] if father else None
        mother_handle = mother["handle"] if mother else None
        data = _record(
            "Family",
            handle,
            gramps_id,
            father_handle=father_handle,
            mother_handle=mother_handle,
            child_ref_list=[_cref(child["handle"]) for child in children],
            type=_typed("FamilyRelType", rel_type),
            event_ref_list=event_refs,
            media_list=[],
            attribute_list=[],
            lds_ord_list=[],
            citation_list=[],
            note_list=[],
        )
        self._insert(
            "family",
            handle=handle,
            gramps_id=gramps_id,
            father_handle=father_handle,
            mother_handle=mother_handle,
            json_data=data,
        )
        return handle
