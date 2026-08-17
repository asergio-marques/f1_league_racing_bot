"""The nationality-to-country map (044, data-model V-1..V-4).

The flag class is keyed on a country so that one directory serves a driver and a
round alike. These tests are what make that safe: the map must be total over the
signup wizard's vocabulary, must agree with ``NATIONALITY_LOOKUP``, and must cover
every country the track registry can present.

V-3 is the important one. It is what stops a British driver drawing
``great_britain.svg`` while the British Grand Prix draws ``united_kingdom.svg`` --
two files for one country, which is the duplication the rekey exists to remove.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from utils.asset_resolver import normalise
from utils.country_data import NATIONALITY_COUNTRIES
from utils.nationality_data import NATIONALITY_LOOKUP

#: Recorded for a driver who stated no nationality. Not a country.
NATIONALITY_OTHER = "Other"

MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "src" / "db" / "migrations" / "029_track_data_expansion.sql"
)


def seeded_track_countries() -> set[str]:
    """The distinct ``country`` values migration 029 seeds into ``tracks``.

    Read from the migration rather than from a live database: these tests run
    offline, and the seed is the authority for what a league can be presented with
    out of the box.
    """
    sql = MIGRATION.read_text(encoding="utf-8")
    block = sql.split("INSERT OR IGNORE INTO tracks", 1)[1]
    block = block.split(";", 1)[0]
    # Each row ends: ..., 'Country', mu, sigma)
    rows = re.findall(r"'([^']+)',\s*[\d.]+,\s*[\d.]+\)", block)
    assert rows, "no seeded track rows parsed from migration 029"
    return set(rows)


# --------------------------------------------------------------------------
# V-1 Totality
# --------------------------------------------------------------------------

def test_v1_every_canonical_nationality_has_a_country():
    """FR-003. A nationality with no country is a defect of the module.

    It must be caught here and never by a fallback drawn at generation, which
    would hide the bug behind a plausible-looking placeholder flag.
    """
    canonical = set(NATIONALITY_LOOKUP.values())
    missing = canonical - set(NATIONALITY_COUNTRIES)
    assert not missing, f"nationalities with no country: {sorted(missing)}"


def test_v1_map_introduces_no_nationality_of_its_own():
    extra = set(NATIONALITY_COUNTRIES) - set(NATIONALITY_LOOKUP.values())
    assert not extra, f"countries mapped for unknown nationalities: {sorted(extra)}"


# --------------------------------------------------------------------------
# V-2 Consistency with the nationality vocabulary
# --------------------------------------------------------------------------

def test_v2_every_country_is_a_known_nationality_key():
    """Keeps the two vocabularies from drifting apart.

    ``Other`` is excepted: it is not a country, and is carried through unchanged.
    """
    keys = set(NATIONALITY_LOOKUP)
    unknown = {
        nationality: country
        for nationality, country in NATIONALITY_COUNTRIES.items()
        if country != NATIONALITY_OTHER and country.lower() not in keys
    }
    assert not unknown, f"countries unknown to NATIONALITY_LOOKUP: {unknown}"


def test_v2_other_is_carried_through_and_is_not_a_country():
    assert NATIONALITY_COUNTRIES[NATIONALITY_OTHER] == NATIONALITY_OTHER
    assert normalise(NATIONALITY_COUNTRIES[NATIONALITY_OTHER]) == "other"


# --------------------------------------------------------------------------
# V-3 Track coverage -- the R-001 fault class
# --------------------------------------------------------------------------

def test_v3_every_seeded_track_country_is_reachable_from_the_map():
    """Every country the track registry holds must be one a driver could hold too.

    A seeded circuit whose country no nationality yields means the round and the
    driver resolve different files for the same place.
    """
    reachable = set(NATIONALITY_COUNTRIES.values())
    unreachable = seeded_track_countries() - reachable
    assert not unreachable, (
        "seeded track countries no nationality maps to: "
        f"{sorted(unreachable)} -- a round and a driver of the same country "
        "would resolve different flag files"
    )


@pytest.mark.parametrize(
    ("nationality", "country"),
    [
        ("British", "United Kingdom"),
        ("American", "United States of America"),
        ("Dutch", "Netherlands"),
        ("Saudi", "Saudi Arabia"),
        ("Emirati", "United Arab Emirates"),
        ("Monegasque", "Monaco"),
        ("Brazilian", "Brazil"),
    ],
)
def test_v3_seed_spellings_are_the_ones_the_map_yields(nationality, country):
    """The spellings are the seed's, not the shorter conversational forms.

    ``United Kingdom`` and ``United States of America`` are what migration 029
    carries; research R-001 settled that the map bends to the data rather than the
    data to the prose.
    """
    assert NATIONALITY_COUNTRIES[nationality] == country
    assert country in seeded_track_countries()


# --------------------------------------------------------------------------
# V-4 Slug stability
# --------------------------------------------------------------------------

def test_v4_one_country_yields_one_slug_whichever_path_asked():
    """The driver path and the round path must produce the same filename."""
    for country in seeded_track_countries() & set(NATIONALITY_COUNTRIES.values()):
        from_round = normalise(country)
        from_driver = {
            normalise(mapped)
            for mapped in NATIONALITY_COUNTRIES.values()
            if mapped == country
        }
        assert from_driver == {from_round}, (
            f"{country!r} normalises differently by path: "
            f"round={from_round!r} driver={from_driver!r}"
        )


def test_v4_slugs_are_non_empty_and_stable():
    for nationality, country in NATIONALITY_COUNTRIES.items():
        slug = normalise(country)
        assert slug, f"{nationality!r} -> {country!r} normalises to nothing"
        assert normalise(slug) == slug, f"{slug!r} is not normalisation-stable"


def test_v4_circuits_sharing_a_country_share_one_slug():
    """Miami, Las Vegas and the Circuit of the Americas draw one file.

    Intended and ruled explicitly; asserted so it is not "fixed" later.
    """
    usa = NATIONALITY_COUNTRIES["American"]
    assert normalise(usa) == "united_states_of_america"


# --------------------------------------------------------------------------
# country_for_nationality -- the single resolution point (044, US1)
# --------------------------------------------------------------------------

def test_helper_maps_a_nationality_to_its_country():
    from utils.country_data import country_for_nationality

    assert country_for_nationality("British") == "United Kingdom"
    assert country_for_nationality("Brazilian") == "Brazil"


def test_helper_carries_other_through_unchanged():
    from utils.country_data import country_for_nationality

    assert country_for_nationality(NATIONALITY_OTHER) == NATIONALITY_OTHER


def test_helper_treats_an_absent_nationality_as_no_country():
    """An absent datum seeks no asset; the caller's rules govern the field."""
    from utils.country_data import country_for_nationality

    assert country_for_nationality(None) is None
    assert country_for_nationality("") is None
    assert country_for_nationality("   ") is None


def test_helper_strips_surrounding_whitespace():
    from utils.country_data import country_for_nationality

    assert country_for_nationality("  British  ") == "United Kingdom"


def test_helper_passes_an_unmapped_value_through_rather_than_raising():
    """Defence against a corrupt record, not a supported path.

    Totality is guaranteed by test, so this cannot arise from the wizard. If it
    somehow does, the value reaches ordinary asset resolution and degrades to the
    class fallback with a notice, rather than killing the graphic mid-render.
    """
    from utils.country_data import country_for_nationality

    assert country_for_nationality("Atlantean") == "Atlantean"
