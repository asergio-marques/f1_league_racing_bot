"""A mystery round draws the mystery flag on the grid headings (044 FR-012).

The rule is not new. 044 moved the flag's datum from `RoundHeading.track` to
`RoundHeading.country` when the column heading became a country flag rather than a circuit
map, and states that a mystery round resolves **both** classes from the literal `Mystery`.
The two grid builders were never updated, and so:

* the standings set `country=None` for a mystery round, which `build_fill_spec` reads as
  "no flag to draw" and answers by **deleting the slot** — the round drew nothing at all;
* the attendance sheet never set `country` at all, so *every* heading lost its flag, while
  `/images test attendance` drew them from the very same rounds.

Both survived the suite because the tests still asserted on `track`, the field that had
stopped mattering. These assert on `country`, which is what the fill reads.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from leaguebot.core.db.database import get_connection, run_migrations  # noqa: E402

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def db_path(tmp_path):
    """Division 5's three rounds, the middle one a mystery. The two circuits are the seeded
    registry's, which places them in the United Kingdom and the Netherlands."""
    path = str(tmp_path / "grid.db")
    await run_migrations(path)
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO seasons (id, start_date, status) VALUES (1, '2026-01-01', 'ACTIVE')"
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, mention_role_id) "
            "VALUES (5, 1, 'Elite', 3001)"
        )
        await db.executemany(
            "INSERT INTO rounds (id, division_id, round_number, format, track_name, "
            "scheduled_at) VALUES (?, 5, ?, ?, ?, ?)",
            [
                (1, 1, "NORMAL", "Silverstone Circuit", "2026-06-07T18:00:00"),
                (2, 2, "MYSTERY", None, "2026-06-14T18:00:00"),
                (3, 3, "NORMAL", "Circuit Zandvoort", "2026-06-21T18:00:00"),
            ],
        )
        await db.commit()
    return path


async def test_the_standings_headings_name_the_mystery_datum(db_path):
    from types import SimpleNamespace

    from leaguebot.image.services.image_standings_post import _calendar

    headings, ordinals = await _calendar(SimpleNamespace(db_path=db_path), 5)

    assert [h.country for h in headings] == [
        "United Kingdom",
        "Mystery",
        "Netherlands",
    ]
    assert ordinals == {1: 1, 2: 2, 3: 3}


async def test_the_mystery_heading_still_names_no_circuit(db_path):
    """The concealment is the point: only the flag is substituted, never the track."""
    from types import SimpleNamespace

    from leaguebot.image.services.image_standings_post import _calendar

    headings, _ = await _calendar(SimpleNamespace(db_path=db_path), 5)
    assert headings[1].track is None


async def test_the_mystery_datum_is_the_calendar_service_s_literal():
    """One literal, in one place — the calendar has owned it since 037."""
    from leaguebot.image.services.image_calendar_service import MYSTERY_COUNTRY, MYSTERY_DATUM

    assert MYSTERY_DATUM == MYSTERY_COUNTRY == "Mystery"


async def test_the_mystery_flag_resolves_to_the_module_s_own_file(tmp_path):
    """The closed-set rule (XIV.13): a league's flag directory need not carry one.

    This is what makes substituting the datum sufficient — no league is asked to draw a
    mystery flag of its own, and one that has not still gets the right picture.
    """
    from leaguebot.image.models.image_constants import is_closed_set_datum
    from leaguebot.image.utils.asset_resolver import normalise, resolve_asset

    league = tmp_path / "flags"
    league.mkdir()
    packaged = tmp_path / "packaged"
    packaged.mkdir()
    (packaged / "mystery.svg").write_bytes(b"<svg/>")

    resolution = resolve_asset(
        league,
        "Mystery",
        packaged=packaged,
        closed_set=is_closed_set_datum("flag", normalise("Mystery")),
    )
    assert resolution.path == packaged / "mystery.svg"
    assert resolution.drew_own_file is True


async def test_a_mystery_round_keeps_its_flag_through_the_fill(db_path):
    """End to end over the shipped template: the slot survives rather than being removed."""
    from types import SimpleNamespace

    from lxml import etree

    from leaguebot.image.services.image_standings_post import _calendar
    from leaguebot.image.services.image_standings_service import (
        DRIVERS_TEMPLATE_KEY,
        StandingsDrawing,
        build_fill_spec,
    )

    headings, _ = await _calendar(SimpleNamespace(db_path=db_path), 5)
    root = etree.parse(
        str(
            __import__("pathlib").Path(__file__).resolve().parents[2]
            / "resources"
            / "defaults"
            / "templates"
            / "standings_drivers_template.svg"
        )
    ).getroot()

    spec = build_fill_spec(
        StandingsDrawing(
            template_key=DRIVERS_TEMPLATE_KEY,
            division_name="Elite",
            classification_label="After Round 3",
            result_status_label="PROVISIONAL",
            rounds=headings,
        ),
        root,
    )

    assert spec.image_data["round_2_flag"] == ("flag", "Mystery")
    assert "round_2_flag" not in spec.remove
