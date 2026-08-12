"""Unit tests for team-name validation — T026, T027.

A lineup template addresses a team's block by the **normalised** team name
(Constitution XIV.11), and that form must serve as an XML `@id`. Principle IX therefore
constrains the name at the moment it is set — and does so **whether or not the image
module is enabled** (FR-012), which is the point most of these tests exist to pin.

Covers the four rejection rules, the two deliberate exemptions, and the ten shipped
default team names surviving all of it.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from services.team_service import validate_team_name


# ── Rule 1: non-empty, trimmed and normalised ─────────────────────────────


@pytest.mark.parametrize("name", ["", "   ", "\t\n"])
def test_an_empty_name_is_refused(name):
    assert validate_team_name(name) is not None


@pytest.mark.parametrize("name", ["!!!", "---", "()", "  @#$  "])
def test_a_name_normalising_to_nothing_is_refused(name):
    problem = validate_team_name(name)
    assert problem is not None
    assert "letter or digit" in problem


# ── Rule 2: begins with a letter ──────────────────────────────────────────


@pytest.mark.parametrize("name", ["2 Fast", "9ers", "1000 Miles", "  7 Racing"])
def test_a_name_not_beginning_with_a_letter_is_refused(name):
    problem = validate_team_name(name)
    assert problem is not None
    assert "begin with a letter" in problem


def test_leading_punctuation_is_stripped_before_the_first_letter_is_judged():
    """`(Alpha)` normalises to `alpha`, which begins with a letter and is fine."""
    assert validate_team_name("(Alpha)") is None


# ── Rule 3: unique within scope ───────────────────────────────────────────


def test_a_name_colliding_after_normalisation_is_refused():
    problem = validate_team_name("Red  Bull!", {"red_bull": "Red Bull"})
    assert problem is not None
    assert "Red Bull" in problem
    assert "red_bull" in problem


def test_a_distinct_name_is_accepted_against_the_same_scope():
    assert validate_team_name("Mercedes", {"red_bull": "Red Bull"}) is None


def test_uniqueness_is_only_judged_against_the_scope_supplied():
    """Rules 1, 2 and 4 are properties of the name; rule 3 needs the sibling set."""
    assert validate_team_name("Red Bull") is None
    assert validate_team_name("Red Bull", {"red_bull": "Red Bull"}) is not None


# ── Rule 4: the reserved word ─────────────────────────────────────────────


@pytest.mark.parametrize("name", ["Reserve", "reserve", "  RESERVE  ", "Reserve!"])
def test_a_name_reducing_to_the_reserved_word_is_refused(name):
    problem = validate_team_name(name)
    assert problem is not None
    assert "reserved" in problem


def test_a_name_merely_containing_reserve_is_accepted():
    assert validate_team_name("Reserve Racing") is None


# ── The shipped defaults all survive ──────────────────────────────────────


DEFAULT_TEAMS = [
    "Alpine",
    "Aston Martin",
    "Ferrari",
    "Haas",
    "McLaren",
    "Mercedes",
    "Racing Bulls",
    "Red Bull",
    "Sauber",
    "Williams",
]


def test_every_shipped_default_team_name_passes():
    """No existing server is broken by this rule, so no migration is owed."""
    seen: dict[str, str] = {}
    from utils.asset_resolver import normalise

    for name in DEFAULT_TEAMS:
        assert validate_team_name(name, seen) is None, name
        seen[normalise(name)] = name


def test_the_shipped_defaults_normalise_uniquely():
    from utils.asset_resolver import normalise

    keys = [normalise(name) for name in DEFAULT_TEAMS]
    assert len(set(keys)) == len(keys)


# ── Names a league might reasonably choose ────────────────────────────────


@pytest.mark.parametrize(
    "name,key",
    [
        ("Force India (B)", "force_india_b"),
        ("Scuderia Ferrari", "scuderia_ferrari"),
        ("Sauber-Alfa", "sauber_alfa"),
        ("Åland Racing", "aland_racing"),
        ("Team 42", "team_42"),
    ],
)
def test_reasonable_names_are_accepted_and_key_as_expected(name, key):
    from utils.asset_resolver import normalise

    assert validate_team_name(name) is None
    assert normalise(name) == key


def test_the_key_and_the_asset_filename_come_from_one_rule():
    """Constitution XIV.13, v4.3.0 — one datum, one spelling in id and filename alike."""
    from utils.asset_resolver import filename_for, normalise

    assert normalise("Red Bull") == "red_bull"
    assert filename_for("Red Bull") == "red_bull.svg"
