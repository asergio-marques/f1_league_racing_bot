"""Weather's readers of its own columns on core's tables (#283).

`rounds.phase1_done`–`phase3_done` and `divisions.forecast_channel_id` are the weather module's
own (architecture.md, "Each table is read and written by its owner alone"): weather reads them
through readers of its own, and core asks weather for them through a hook rather than carrying
them on its models.
"""
from __future__ import annotations

import os

import pytest

from leaguebot.core.db.database import get_connection, run_migrations

SERVER_ID = 9810


async def _make_db(tmp_path) -> str:
    """A season in setup with three divisions: 11 posting its forecast to channel 706, 12 and 13
    with none set. Division 11 holds round 101, with Phase 1 drawn, and round 102, with Phases 1
    and 2 drawn; division 12 holds round 103, with nothing drawn."""
    db_path = os.path.join(str(tmp_path), "weather_columns.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 900, 100, 101)",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO seasons (id, season_number, start_date, status) "
            "VALUES (1, 1, '2026-01-01', 'SETUP')",
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id, "
            "forecast_channel_id) VALUES (11, 1, 'Pro', 1, 555, 706), "
            "(12, 1, 'Am', 2, 556, NULL), (13, 1, 'Club', 3, 557, NULL)"
        )
        await db.execute(
            "INSERT INTO rounds (id, division_id, round_number, format, track_name, "
            "scheduled_at, phase1_done, phase2_done, phase3_done) VALUES "
            "(101, 11, 1, 'NORMAL', 'Monza', '2026-03-01T18:00:00+00:00', 1, 0, 0), "
            "(102, 11, 2, 'NORMAL', 'Spa', '2026-03-08T18:00:00+00:00', 1, 1, 0), "
            "(103, 12, 1, 'NORMAL', 'Imola', '2026-03-01T18:00:00+00:00', 0, 0, 0)"
        )
        await db.commit()
    return db_path


async def test_weather_reads_which_phases_each_round_has_had_drawn(tmp_path):
    """Rounds 101 (Phase 1 drawn), 102 (Phases 1 and 2) and 103 (nothing) asked for, with round
    999, which does not exist: each round's three phases come back, drawn or not, and the round
    that does not exist is left out."""
    from leaguebot.weather.services.round_phases import phases_drawn_on

    db_path = await _make_db(tmp_path)
    async with get_connection(db_path) as db:
        drawn = await phases_drawn_on(db, [101, 102, 103, 999])

    assert drawn == {
        101: {1: True, 2: False, 3: False},
        102: {1: True, 2: True, 3: False},
        103: {1: False, 2: False, 3: False},
    }


async def test_weather_reads_the_phases_drawn_on_the_connection_it_is_handed(tmp_path):
    """Round 103's Phase 1 marked drawn on a connection and not yet committed, as an amendment's
    save holds it: weather's reader, handed that connection, sees the phase drawn."""
    from leaguebot.weather.services.round_phases import phases_drawn_on

    db_path = await _make_db(tmp_path)
    async with get_connection(db_path) as db:
        await db.execute("UPDATE rounds SET phase1_done = 1 WHERE id = 103")

        drawn = await phases_drawn_on(db, [103])

    assert drawn == {103: {1: True, 2: False, 3: False}}


async def test_weather_reads_each_division_s_forecast_channel(tmp_path):
    """Divisions 11 (channel 706), 12 (none set) and 999 (no such division) asked for: 11's
    channel and 12's absence come back, and the division that does not exist is left out."""
    from leaguebot.weather.services.forecast_channel import forecast_channels_on

    db_path = await _make_db(tmp_path)
    async with get_connection(db_path) as db:
        channels = await forecast_channels_on(db, [11, 12, 999])

    assert channels == {11: 706, 12: None}


async def test_weather_gives_the_placements_review_each_division_s_weather_line(tmp_path):
    """The placements review asks weather for divisions 11 (channel 706) and 12 (none set):
    each is given its weather channel line in the review's words, the channel named or shown as
    none, with the weather module's switch never asked, as the review always showed it."""
    from leaguebot.weather.services.forecast_channel import division_lines

    db_path = await _make_db(tmp_path)

    lines = await division_lines(db_path, season_id=1, division_ids=[11, 12])

    assert lines == {
        11: ["  Weather channel: <#706>"],
        12: ["  Weather channel: *(none)*"],
    }
