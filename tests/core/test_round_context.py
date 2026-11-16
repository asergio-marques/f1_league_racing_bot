"""Core's readers of a round's and a division's context: `round_context`, `division_context` and
`division_rounds`.

The weather, verdict, lineup, standings and preview pictures head themselves through these readers
and join none of core's tables; these tests pin the readers directly, which those pictures' tests
reach only through their callers.
"""
from __future__ import annotations

import os

from leaguebot.framework.db.database import get_connection, run_migrations
from leaguebot.core.services.rounds.round_context import (
    division_context,
    division_rounds,
    round_context,
)

DIVISION = 1
#: Round 2 runs at Albert Park, which the track registry holds; round 1 at a track it does not.
REGISTERED_ROUND, UNREGISTERED_ROUND = 20, 10


async def _league(tmp_path) -> str:
    """Season 3 with division Pro (tier 2), seeded parents first, and three rounds of Pro made out
    of order: round 3 (no track named, SPRINT), round 1 at "Nowhere Ring" (NORMAL, not in the
    registry) and round 2 at "Albert Park Circuit" (NORMAL, in the registry)."""
    db_path = os.path.join(str(tmp_path), "context.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO seasons (id, start_date, status, season_number) "
            "VALUES (7, '2026-01-01', 'ACTIVE', 3)"
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, mention_role_id, tier) "
            "VALUES (?, 7, 'Pro', 1, 2)",
            (DIVISION,),
        )
        for round_id, number, fmt, track in (
            (30, 3, "SPRINT", None),
            (UNREGISTERED_ROUND, 1, "NORMAL", "Nowhere Ring"),
            (REGISTERED_ROUND, 2, "NORMAL", "Albert Park Circuit"),
        ):
            await db.execute(
                "INSERT INTO rounds (id, division_id, round_number, format, track_name, "
                "scheduled_at) VALUES (?, ?, ?, ?, ?, '2026-03-01T18:00:00+00:00')",
                (round_id, DIVISION, number, fmt, track),
            )
        await db.commit()
    return db_path


async def test_a_round_s_context_carries_its_division_season_and_grand_prix(tmp_path):
    """Round 2 of Pro runs at Albert Park: its context carries the round's number, format and
    track, Pro with its tier, season 7 numbered 3, and the registry's grand prix and country."""
    db_path = await _league(tmp_path)

    context = await round_context(db_path, REGISTERED_ROUND)

    assert context is not None
    assert (context.round_id, context.round_number, context.format, context.track_name) == (
        REGISTERED_ROUND, 2, "NORMAL", "Albert Park Circuit")
    assert (context.gp_name, context.country) == ("Australian Grand Prix", "Australia")
    division = context.division
    assert (division.division_id, division.name, division.tier) == (DIVISION, "Pro", 2)
    assert (division.season_id, division.season_number) == (7, 3)


async def test_a_round_whose_track_is_not_registered_has_no_grand_prix_or_country(tmp_path):
    """Round 1 runs at "Nowhere Ring", which the registry does not hold: the round still reads,
    with its track name, and no grand prix or country."""
    db_path = await _league(tmp_path)

    context = await round_context(db_path, UNREGISTERED_ROUND)

    assert context is not None
    assert context.track_name == "Nowhere Ring"
    assert (context.gp_name, context.country) == (None, None)


async def test_a_round_or_division_that_is_gone_reads_as_none(tmp_path):
    """Asked for round 999 and division 999, which do not exist, each reader answers None."""
    db_path = await _league(tmp_path)

    assert await round_context(db_path, 999) is None
    assert await division_context(db_path, 999) is None


async def test_a_division_s_context_carries_its_season(tmp_path):
    """Asked for Pro, the reader hands back Pro, its tier 2, and season 7 numbered 3."""
    db_path = await _league(tmp_path)

    division = await division_context(db_path, DIVISION)

    assert division is not None
    assert (division.division_id, division.name, division.tier) == (DIVISION, "Pro", 2)
    assert (division.season_id, division.season_number) == (7, 3)


async def test_a_division_s_rounds_come_in_round_order(tmp_path):
    """Pro's three rounds, made 3, 1, 2, come back 1, 2, 3, each with its id, format and track,
    the round with no track named as None; a division with no rounds hands back none."""
    db_path = await _league(tmp_path)

    rounds = await division_rounds(db_path, DIVISION)

    assert [(r.round_number, r.round_id, r.format, r.track_name) for r in rounds] == [
        (1, UNREGISTERED_ROUND, "NORMAL", "Nowhere Ring"),
        (2, REGISTERED_ROUND, "NORMAL", "Albert Park Circuit"),
        (3, 30, "SPRINT", None),
    ]
    assert await division_rounds(db_path, 999) == []
