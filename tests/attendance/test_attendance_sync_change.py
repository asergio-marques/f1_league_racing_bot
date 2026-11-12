"""`/attendance sync` as one change on the queue (#285).

The sync recovers a run of sanctions that did not all apply: every finalised round from the one
named on is recalculated from its results, the sanctions the latest of them owes are applied, and
their cards, the lineup and the sheet are posted. It ran outside the queue: the recalculation and
each sanction were committed one by one, then the cards were asked of the queue as a change of
their own, so a bot stopping between the two left sanctions applied and never announced, with no
record of what was owed.

It is now one change. The recalculation and the sanctions it owes are one save; the cards, the
lineup and the sheet are later jobs of the change, with its record, so a stop between them
finishes once the bot is back, and a second sync finds nothing owed and announces nothing again.
The manager is answered with the queue's acknowledgement, and what the sync did is the change's
line in the log channel.

Each test runs the command as a manager types it, on the bot the entry point builds, with the
real queue, attendance's real share of a round's review and a real placement service, "now"
pinned. The league: Division 1 of season 1, ongoing, its full-time driver (profile 101, a test
driver, so no role is touched) seated in Alpha; each finalised round has the driver as accepted
and not raced, so the recalculation gives them the no-show penalty; auto-sack is set at 3 points.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from leaguebot.attendance.cogs.attendance_cog import AttendanceCog
from leaguebot.attendance.services.attendance_after_review import AttendanceAfterReview
from leaguebot.core.services.drivers.placement_service import PlacementService
from leaguebot.core.db.database import get_connection, run_migrations
from tests.support.change_queue import (
    SERVER_ID,
    Clock,
    change_rows,
    league_bot,
    league_queue,
    member_interaction,
    restart_queue,
    run_queue,
    seed_server,
    set_guilds,
    stopped_job,
    text_channel,
    tier_member,
)
from tests.support.season_league import set_module_flag
from tests.support.settings_league import unruled
from tests.support.undecorate import undecorate

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
SEASON_ID = 1
DIVISION_ID = 1
ALPHA, RESERVE = 10, 11
DRIVER_PROFILE = 101
ATTENDANCE_CHANNEL = 710
VERDICTS_CHANNEL = 711
MANAGER_ID = 77

#: The change `/attendance sync` asks of the queue.
SYNC_KIND = "attendance.sync"

#: Why each test fails until the sync is one change.
NOT_A_CHANGE = (
    "#285: /attendance sync is not yet one change on the queue: the command recalculates and "
    "applies the sanctions itself, committing each, and asks for their cards apart"
)


class _League:
    """The bot, its server and the channels the sync posts to."""

    def __init__(self, bot: Any, guild: Any, db_path: str, channels: dict[int, Any]) -> None:
        self.bot, self.guild, self.db_path, self.channels = bot, guild, db_path, channels

    def cards(self) -> list[str]:
        """What the division's verdicts channel was sent: the sanctions' cards."""
        return list(self.channels[VERDICTS_CHANNEL].sent)

    def sheets(self) -> list[str]:
        """What the division's attendance channel was sent: the sheet."""
        return list(self.channels[ATTENDANCE_CHANNEL].sent)

    def sync_lines(self) -> list[str]:
        """Every line of the log channel's that records a sync."""
        return [unruled(str(line)) for line in self.bot.log_channel.sent
                if "| /attendance sync |" in str(line)]


async def _league(tmp_path, *, rounds: dict[int, str] | None = None,
                  no_show_penalty: int = 5) -> _League:
    """The league of the module docstring. *rounds* gives each round of Division 1 by number
    (its id the same) and its status, round 1 final where none is given; the driver accepted each
    finalised round and did not race it. The no-show penalty is *no_show_penalty*."""
    rounds = rounds or {1: "FINAL"}
    db_path = os.path.join(str(tmp_path), "attendance_sync.db")
    await run_migrations(db_path)
    await seed_server(db_path)
    await set_module_flag(db_path, "attendance", True)
    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE attendance_config SET autosack_threshold = 3, no_show_penalty = ?",
            (no_show_penalty,),
        )
        await db.execute(
            "INSERT INTO seasons (id, season_number, start_date, status, stage) "
            "VALUES (?, 1, '2026-01-01', 'ACTIVE', 'ONGOING')",
            (SEASON_ID,),
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
            "VALUES (?, ?, 'Division 1', 1, 555)",
            (DIVISION_ID, SEASON_ID),
        )
        for number, status in rounds.items():
            await db.execute(
                "INSERT INTO rounds (id, division_id, round_number, format, track_name, "
                "scheduled_at, status) VALUES (?, ?, ?, 'NORMAL', 'Silverstone', ?, ?)",
                (number, DIVISION_ID, number, f"2026-09-{number:02d}T18:00:00+00:00", status),
            )
        for team_id, name, seats, reserve in ((ALPHA, "Alpha", 2, 0), (RESERVE, "Reserve", 6, 1)):
            await db.execute(
                "INSERT INTO team_instances (id, division_id, name, full_name, max_seats, "
                "is_reserve) VALUES (?, ?, ?, ?, ?, ?)",
                (team_id, DIVISION_ID, name, name, seats, reserve),
            )
        await db.execute(
            "INSERT INTO driver_profiles (id, discord_user_id, current_state, is_test_driver, "
            "test_display_name) VALUES (?, '5101', 'ASSIGNED', 1, 'Full Timer')",
            (DRIVER_PROFILE,),
        )
        await db.execute(
            "INSERT INTO team_seats (id, team_instance_id, seat_number, driver_profile_id) "
            "VALUES (20, ?, 1, ?)",
            (ALPHA, DRIVER_PROFILE),
        )
        await db.execute(
            "INSERT INTO driver_season_assignments (driver_profile_id, season_id, division_id, "
            "team_seat_id, committed) VALUES (?, ?, ?, 20, 1)",
            (DRIVER_PROFILE, SEASON_ID, DIVISION_ID),
        )
        for number, status in rounds.items():
            if status == "FINAL":
                await db.execute(
                    "INSERT INTO driver_round_attendance (round_id, division_id, "
                    "driver_profile_id, rsvp_status) VALUES (?, ?, ?, 'ACCEPTED')",
                    (number, DIVISION_ID, DRIVER_PROFILE),
                )
        await db.execute(
            "INSERT INTO division_results_config (division_id, reserves_in_standings, "
            "penalty_channel_id) VALUES (?, 1, ?)",
            (DIVISION_ID, str(VERDICTS_CHANNEL)),
        )
        await db.execute(
            "INSERT INTO attendance_division_config (division_id, attendance_channel_id) "
            "VALUES (?, ?)",
            (DIVISION_ID, str(ATTENDANCE_CHANNEL)),
        )
        await db.commit()

    def _hook(bot: Any, path: str) -> Any:
        lineups = MagicMock()
        lineups.refresh_lineup = AsyncMock(return_value=None)
        return AttendanceAfterReview(bot, PlacementService(path), lineups=lineups)

    bot = await league_bot(db_path, attendance_hook_of=_hook)
    # The lineup a sack owes is not what these tests are about.
    bot.lineup_poster._refresh_lineup_post = AsyncMock(return_value=None)
    bot.module_service.is_images_enabled = AsyncMock(return_value=False)
    channels = {
        bot.log_channel.id: bot.log_channel,
        bot.interaction_channel.id: bot.interaction_channel,
        ATTENDANCE_CHANNEL: text_channel(ATTENDANCE_CHANNEL),
        VERDICTS_CHANNEL: text_channel(VERDICTS_CHANNEL),
    }
    guild = MagicMock(spec=discord.Guild)
    guild.id = SERVER_ID
    guild.get_channel = MagicMock(side_effect=channels.get)
    guild.get_member = MagicMock(return_value=None)
    guild.fetch_member = AsyncMock(return_value=None)
    for each in channels.values():
        each.guild = guild
    bot.get_channel = MagicMock(side_effect=channels.get)
    bot.fetch_channel = AsyncMock(side_effect=lambda cid: channels[cid])
    bot.get_guild = MagicMock(return_value=guild)
    set_guilds(bot, [guild])
    league_queue(bot, db_path, now=Clock(NOW))
    return _League(bot, guild, db_path, channels)


async def _sync(league: _League, round_number: int = 1) -> Any:
    """Manager 77 runs `/attendance sync division:Division 1 round:<round_number>`; the queue is
    not run. The interaction is returned."""
    cog = AttendanceCog.__new__(AttendanceCog)
    cog.bot = league.bot
    interaction = member_interaction(
        league.bot,
        user=tier_member("manager", member_id=MANAGER_ID, display_name="Manager",
                         name="Manager#0001"),
    )
    interaction.guild = league.guild
    interaction.command = AttendanceCog.sync
    await undecorate(AttendanceCog.sync)(cog, interaction, "Division 1", round_number)
    return interaction


def _first_said(interaction: Any) -> str:
    """The first thing the manager was told, by the response or, after a deferral, a
    follow-up."""
    calls = (interaction.response.send_message.await_args_list
             + interaction.followup.send.await_args_list)
    if not calls:
        return ""
    call = calls[0]
    return str(call.args[0] if call.args else call.kwargs.get("content", ""))


async def _sync_changes(db_path: str) -> list[dict[str, Any]]:
    return [row for row in await change_rows(db_path) if row["kind"] == SYNC_KIND]


async def _seated(db_path: str) -> list[int]:
    """The drivers seated in Alpha."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT driver_profile_id FROM team_seats WHERE team_instance_id = ? "
            "AND driver_profile_id IS NOT NULL",
            (ALPHA,),
        )
        return [row[0] for row in await cursor.fetchall()]


async def _attendance(db_path: str, round_id: int) -> dict[str, Any]:
    """The driver's attendance row of the round: whether they attended and the points given."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT attended, points_awarded FROM driver_round_attendance "
            "WHERE round_id = ? AND driver_profile_id = ?",
            (round_id, DRIVER_PROFILE),
        )
        return dict(await cursor.fetchone())


@pytest.mark.xfail(strict=True, reason=NOT_A_CHANGE)
@pytest.mark.parametrize(
    "no_show_penalty,applied",
    [(5, 1), (0, 0)],
    ids=["a-sanction-owed", "nothing-owed"],
)
async def test_a_sync_is_one_change_acknowledged_at_once_and_logged_once_done(
    tmp_path, no_show_penalty, applied
):
    """Round 1 of Division 1 is final; its driver accepted and did not race, with a no-show
    penalty of *no_show_penalty* against an auto-sack of 3. Manager 77 runs `/attendance sync`
    from round 1. One change is asked of the queue and the manager is answered with the queue's
    acknowledgement; nothing is written until the queue runs it: the driver is still seated and
    the round not recalculated. The queue is run: it finishes, the driver is sacked only where
    owed, and the log channel's one line for the sync is its Success line, naming the division,
    the round it ran from and the number of sanctions applied."""
    league = await _league(tmp_path, no_show_penalty=no_show_penalty)

    interaction = await _sync(league)

    assert len(await _sync_changes(league.db_path)) == 1
    assert _first_said(interaction).startswith("⏳ ")
    assert await _seated(league.db_path) == [DRIVER_PROFILE]
    assert (await _attendance(league.db_path, 1))["attended"] is None

    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    assert await _seated(league.db_path) == ([] if applied else [DRIVER_PROFILE])
    [line] = league.sync_lines()
    assert "| /attendance sync | Success" in line
    assert "division: Division 1" in line
    assert "from round: 1" in line
    assert f"sanctions applied: {applied}" in line


@pytest.mark.xfail(strict=True, reason=NOT_A_CHANGE)
async def test_a_sync_recalculates_from_the_named_round_on_and_leaves_earlier_rounds_alone(
    tmp_path,
):
    """Rounds 1 and 2 of Division 1 are final and round 3 is not yet run; the driver accepted
    rounds 1 and 2 and raced neither, and neither has been recalculated. Manager 77 runs
    `/attendance sync` from round 2, as one change, and the queue is run. Round 2 is recalculated:
    the driver did not attend and is given the no-show penalty of 5; round 1 is left as it was;
    and the driver, over the auto-sack threshold at round 2, is sacked."""
    league = await _league(tmp_path, rounds={1: "FINAL", 2: "FINAL", 3: "NOT_RUN"})

    await _sync(league, round_number=2)
    await run_queue(league.bot)

    assert len(await _sync_changes(league.db_path)) == 1
    assert await stopped_job(league.db_path) is None
    assert await _attendance(league.db_path, 2) == {"attended": 0, "points_awarded": 5}
    assert await _attendance(league.db_path, 1) == {"attended": None, "points_awarded": None}
    assert await _seated(league.db_path) == []


@pytest.mark.xfail(strict=True, reason=NOT_A_CHANGE)
async def test_a_stop_between_the_sanctions_and_their_cards_finishes_on_restart(tmp_path):
    """Round 1 is final and its driver owes an auto-sack. Manager 77 runs `/attendance sync`
    from round 1, and the queue runs one job at a time: at no point is the round seen
    recalculated without the sack or the sack without the round, the two being one save. The bot
    stops just after that save, before any card is posted; it comes back and its queue runs on.
    The change finishes: the sack's one card is posted in the verdicts channel, the sheet once in
    the attendance channel, the driver stays sacked, and the sync's one line is written."""
    league = await _league(tmp_path)

    await _sync(league)

    assert len(await _sync_changes(league.db_path)) == 1
    assert await _seated(league.db_path) == [DRIVER_PROFILE]
    for _ in range(40):
        await run_queue(league.bot, steps=1)
        sacked = await _seated(league.db_path) == []
        recalculated = (await _attendance(league.db_path, 1))["attended"] is not None
        assert sacked == recalculated, "the recalculation and the sack were saved apart"
        if sacked:
            break
    else:
        raise AssertionError("the driver was never sacked")
    assert league.cards() == []

    await restart_queue(league.bot)
    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    assert len(league.cards()) == 1
    assert len(league.sheets()) == 1
    assert await _seated(league.db_path) == []
    assert len(league.sync_lines()) == 1


@pytest.mark.xfail(strict=True, reason=NOT_A_CHANGE)
async def test_a_second_sync_announces_nothing_already_announced(tmp_path):
    """Round 1 is final and its driver owes an auto-sack. Manager 77 runs `/attendance sync`
    from round 1 and the queue is run: the driver is sacked and the sack's card posted. They run
    it again, and the queue is run: the second sync finishes too, finds nothing owed, posts no
    second card, and its line says no sanction was applied."""
    league = await _league(tmp_path)

    await _sync(league)
    await run_queue(league.bot)
    await _sync(league)
    await run_queue(league.bot)

    assert len(await _sync_changes(league.db_path)) == 2
    assert await stopped_job(league.db_path) is None
    assert len(league.cards()) == 1
    first, second = league.sync_lines()
    assert "sanctions applied: 1" in first
    assert "sanctions applied: 0" in second
