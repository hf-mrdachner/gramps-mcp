"""
Generate a fictional Gramps SQLite database for integration tests.

Everything is invented: no real family tree is read at any point, and no database
file is committed. The tree is built at test time, with a fixed seed so that test
failures are reproducible. It deliberately contains the variety a grown tree has,
such as all date forms, custom types and incomplete families.
"""

import sqlite3

from conftest_sqlite import write_fixture_db
from synthetic_builder import FEMALE, MALE, UNKNOWN, TreeBuilder

_GENERATIONS = 4
_SURNAMES = ["Beispiel", "Muster", "Probe", "Fiktiv", "Erfunden", "Testmann"]


def _build_tree(builder: TreeBuilder) -> None:
    """Fill the database with a fictional multi-generation tree."""
    country = builder.place("Fiktivland", 1)
    states = [builder.place(f"Region {n}", 2, country) for n in range(1, 3)]
    full_title = "Musterstadt, Region 1, Fiktivland"
    towns = [
        builder.place("Musterstadt", 4, states[0], title=full_title),
        builder.place("Beispielhausen", 4, states[0]),
        builder.place("Probedorf", 4, states[1]),
    ]

    repositories = [
        builder.repository("Fiktives Staatsarchiv", True),
        builder.repository("Erfundenes Pfarrarchiv", False),
    ]
    sources = [
        builder.source("Erfundenes Kirchenbuch", repositories[0]),
        builder.source("Fiktives Standesamtsregister", repositories[1]),
        builder.source("Ausgedachte Familienchronik", None),
    ]
    citations = [builder.citation(sources[n % 3], n) for n in range(1, 13)]
    notes = [
        builder.note("Eine erfundene Forschungsnotiz.", 1),
        builder.note("Noch eine ausgedachte Notiz.", 0, custom_type="Eigene Notizart"),
    ]
    media = builder.media("fiktiv/dokument.jpg", "image/jpeg")
    builder.media("fiktiv/ohne-typ.bin", "")

    rng = builder.rng

    def new_person(gender, surname, year):
        """Build a person with randomly chosen sources and birthplace."""
        return builder.person(
            gender,
            surname,
            year,
            rng.choice(towns),
            rng.sample(citations, 2),
            notes,
            media,
        )

    couples = [
        (new_person(MALE, surname, 1780), new_person(FEMALE, "Geborene", 1782))
        for surname in _SURNAMES[:3]
    ]
    people = [person for couple in couples for person in couple]

    for generation in range(1, _GENERATIONS):
        year = 1780 + generation * 28
        next_couples = []
        for father, mother in couples:
            surname = father["primary_name"]["surname_list"][0]["surname"]
            children = []
            for _ in range(rng.randint(1, 3)):
                gender = rng.choice([FEMALE, MALE, MALE, UNKNOWN])
                children.append(new_person(gender, surname, year))
            builder.family(father, mother, children, year - 2, rng.choice(towns), 0)
            people.extend(children)
            # Each son starts the next generation with an invented spouse.
            for child in children:
                if child["gender"] == MALE:
                    spouse = new_person(FEMALE, rng.choice(_SURNAMES), year + 2)
                    people.append(spouse)
                    next_couples.append((child, spouse))
        couples = next_couples or couples

    # Incomplete families, which every real tree contains.
    single_mother = new_person(FEMALE, "Alleinerziehend", 1850)
    single_father = new_person(MALE, "Alleinerziehend", 1850)
    only_child = new_person(MALE, "Alleinerziehend", 1875)
    childless_husband = new_person(MALE, "Kinderlos", 1850)
    childless_wife = new_person(FEMALE, "Kinderlos", 1852)
    people += [
        single_mother,
        single_father,
        only_child,
        childless_husband,
        childless_wife,
    ]
    builder.family(None, single_mother, [only_child], 1875, towns[0], 3)
    builder.family(single_father, None, [], 1875, None, 3)
    builder.family(childless_husband, childless_wife, [], 1875, towns[1], 1)

    for person in people:
        builder.save_person(person)


def write_synthetic_tree(path) -> None:
    """
    Write a fictional Gramps database to a file.

    Args:
        path: Target file path. The parent directory must already exist.
    """
    write_fixture_db(path)
    conn = sqlite3.connect(path)
    try:
        # Tables a real Gramps database has beyond those the tests read directly.
        conn.execute(
            "CREATE TABLE gender_stats (given_name TEXT, female INTEGER, "
            "male INTEGER, unknown INTEGER)"
        )
        conn.execute(
            "CREATE TABLE name_group (name VARCHAR(50) PRIMARY KEY, grouping TEXT)"
        )
        _build_tree(TreeBuilder(conn))
        conn.commit()
    finally:
        conn.close()
