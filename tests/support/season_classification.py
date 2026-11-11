"""A season bracketed by its classifications, for the tests of the opening and final posts.

The opening classification is posted when a season is approved, the final one when it
completes: results' standings and attendance's sheet, each under its occasion's heading
(`leaguebot.core.models.classification_occasion.ClassificationOccasion`). The tests of each
module's posting seed the same season here, and stand results' and attendance's own posting
functions aside with `classification_posts`, so that what is pinned is which sheet is posted
for which division, against which round, and that a posting raises where it cannot be made.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from leaguebot.core.db.database import get_connection


def bot_on(path: str) -> Any:
    """A bot carrying a real db path — the name resolution reads it."""
    bot = MagicMock()
    bot.db_path = path
    return bot


def guild_double() -> Any:
    """The league's server, every channel it is asked for standing in it."""
    guild = MagicMock()
    guild.id = 1
    guild.get_channel.return_value = MagicMock()
    return guild


async def seed_season(path: str, *, server_id: int = 1, divisions: tuple[str, ...] = ("Div A",)):
    """A season, its divisions, one round each, and a seated driver per division.

    Each division's results channel is 800 and its standings channel 900, plus its index."""
    ids: list[int] = []
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO server_configs "
            "(server_id, interaction_role_id, interaction_channel_id, log_channel_id) "
            "VALUES (?, 1, 2, 3)",
            (server_id,),
        )
        cur = await db.execute(
            "INSERT INTO seasons (start_date, status, season_number) "
            "VALUES ('2026-01-01', 'ACTIVE', 1)"
        )
        season_id = cur.lastrowid
        for index, name in enumerate(divisions):
            cur = await db.execute(
                "INSERT INTO divisions (season_id, name, mention_role_id, "
                "forecast_channel_id) VALUES (?, ?, ?, ?)",
                (season_id, name, 10 + index, 20 + index),
            )
            division_id = cur.lastrowid
            ids.append(division_id)
            await db.execute(
                "INSERT INTO division_results_config "
                "(division_id, results_channel_id, standings_channel_id) "
                "VALUES (?, ?, ?)",
                (division_id, 800 + index, 900 + index),
            )
            await db.execute(
                "INSERT INTO rounds (division_id, round_number, format, scheduled_at) "
                "VALUES (?, 1, 'NORMAL', '2026-02-01T18:00:00+00:00')",
                (division_id,),
            )
            cur = await db.execute(
                "INSERT INTO driver_profiles (discord_user_id, current_state) "
                "VALUES (?, 'ACTIVE')",
                (500 + index,),
            )
            profile_id = cur.lastrowid
            cur = await db.execute(
                "INSERT INTO team_instances (division_id, name, full_name, max_seats, is_reserve) "
                "VALUES (?, 'Apex Racing', 'Apex Racing', 2, 0)",
                (division_id,),
            )
            await db.execute(
                "INSERT INTO team_seats (team_instance_id, seat_number, "
                "driver_profile_id) VALUES (?, 1, ?)",
                (cur.lastrowid, profile_id),
            )
        await db.commit()
    return season_id, ids


def classification_posts(standings: Any = None, attendance: Any = None) -> tuple[Any, Any, Any]:
    """The standings, through whichever of results' two posting functions they use, and the
    sheet. Both standings doubles are the one mock, so a test reads either; the recording
    `post_standings` is created where #439 6b has deleted it."""
    standings = standings or AsyncMock(return_value=[])
    return (
        patch(
            "leaguebot.results.services.results_post_service.post_standings", standings, create=True,
        ),
        patch("leaguebot.results.services.results_post_service.produce_standings", standings),
        patch(
            "leaguebot.attendance.services.attendance_service.post_attendance_sheet",
            attendance or AsyncMock(return_value=None),
        ),
    )


async def first_round_id(path: str, division_id: int) -> int:
    """The id of the division's round 1."""
    async with get_connection(path) as db:
        cursor = await db.execute(
            "SELECT id FROM rounds WHERE division_id = ? AND round_number = 1", (division_id,)
        )
        return (await cursor.fetchone())["id"]


async def second_round_with_results(path: str, division_id: int, *, message_id=None) -> int:
    """Round 2 of the division, with an accepted session and Lewis (500) leading its standings,
    the standings message *message_id* recorded against it: the round a division's final
    classification is drawn against."""
    async with get_connection(path) as db:
        cur = await db.execute(
            "INSERT INTO rounds (division_id, round_number, format, scheduled_at) "
            "VALUES (?, 2, 'NORMAL', '2026-03-01T18:00:00+00:00')",
            (division_id,),
        )
        round_id = cur.lastrowid
        await db.execute(
            "INSERT INTO session_results (round_id, division_id, session_type, status) "
            "VALUES (?, ?, 'FEATURE_RACE', 'ACTIVE')",
            (round_id, division_id),
        )
        await db.execute(
            "INSERT INTO driver_standings_snapshots (round_id, division_id, driver_user_id, "
            "standing_position, total_points, standings_message_id, standings_message_ids) "
            "VALUES (?, ?, 500, 1, 25, ?, ?)",
            (
                round_id,
                division_id,
                message_id,
                None if message_id is None else f"[{message_id}]",
            ),
        )
        await db.commit()
    return round_id
