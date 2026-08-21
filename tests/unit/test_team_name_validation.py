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


# ── Withdrawn: the name had to begin with a letter ────────────────────────


@pytest.mark.parametrize("name", ["2 Fast", "9ers", "1000 Miles", "  7 Racing"])
def test_a_name_beginning_with_a_digit_is_admitted(name):
    """047 FR-031. The rule held only while the normalised form was an XML `@id`.

    It names a **file** now — `2fast_motorsport.svg` — and a filename may begin with a
    digit. Nothing else about the name is relaxed.
    """
    assert validate_team_name(name) is None


def test_leading_punctuation_is_still_stripped():
    """`(Alpha)` normalises to `alpha`. Unchanged by the relaxation above."""
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


# ══════════════════════════════════════════════════════════════════════════
# 047 FR-032 — the same criteria at `season review`
#
# `validate_team_name` is exercised above as a function. This section covers the
# *review* path that applies it across a server's team list and every division of the
# season under setup — the half that had no test, in a method sitting beside one this
# feature rewrote.
# ══════════════════════════════════════════════════════════════════════════

import os as _os  # noqa: E402
import sys as _sys  # noqa: E402
from unittest.mock import MagicMock as _MagicMock  # noqa: E402

_sys.path.insert(0, _os.path.join(_os.path.dirname(__file__), "..", "..", "src"))

import aiosqlite  # noqa: E402

from cogs.season_cog import SeasonCog  # noqa: E402


async def _seed(path, server_teams, division_teams):
    async with aiosqlite.connect(path) as db:
        await db.execute(
            "CREATE TABLE default_teams (server_id INTEGER, name TEXT, is_reserve INTEGER)"
        )
        await db.execute("CREATE TABLE divisions (id INTEGER, season_id INTEGER, name TEXT, tier INTEGER)")
        await db.execute(
            "CREATE TABLE team_instances (division_id INTEGER, name TEXT, is_reserve INTEGER)"
        )
        for name in server_teams:
            await db.execute(
                "INSERT INTO default_teams VALUES (?, ?, 0)", (1, name)
            )
        await db.execute("INSERT INTO divisions VALUES (10, 1, 'Elite', 1)")
        for name in division_teams:
            await db.execute("INSERT INTO team_instances VALUES (10, ?, 0)", (name,))
        await db.commit()


async def _review_problems(tmp_path, server_teams, division_teams):
    path = tmp_path / "review.db"
    await _seed(path, server_teams, division_teams)
    cog = _MagicMock(bot=_MagicMock(db_path=str(path)))
    return await SeasonCog._team_name_problems(cog, server_id=1, season_id=1)


@pytest.mark.asyncio
async def test_season_review_passes_a_sound_team_list(tmp_path):
    problems = await _review_problems(tmp_path, ["Red Bull", "Ferrari"], ["Haas"])

    assert problems == []


@pytest.mark.asyncio
async def test_season_review_admits_a_name_beginning_with_a_digit(tmp_path):
    """FR-031 reaching the review, not the command alone."""
    problems = await _review_problems(tmp_path, ["2Fast Motorsport"], ["9ers"])

    assert problems == []


@pytest.mark.asyncio
async def test_season_review_names_every_offending_team_not_just_the_first(tmp_path):
    """A manager fixing them one review at a time is a manager the check is failing."""
    problems = await _review_problems(tmp_path, ["!!!", "???"], ["Reserve!"])

    assert len(problems) == 3
    assert any("!!!" in p for p in problems)
    assert any("???" in p for p in problems)
    assert any("Reserve!" in p for p in problems)


@pytest.mark.asyncio
async def test_season_review_reports_the_scope_a_fault_was_found_in(tmp_path):
    problems = await _review_problems(tmp_path, ["!!!"], ["???"])

    assert any("the server's team list" in p for p in problems)
    assert any("Elite" in p for p in problems)


@pytest.mark.asyncio
async def test_season_review_catches_two_teams_normalising_alike(tmp_path):
    """No longer fatal at render (047), so the review is where it must be caught."""
    problems = await _review_problems(tmp_path, [], ["Red Bull", "Red  Bull!"])

    assert len(problems) == 1
    assert "red_bull" in problems[0]
