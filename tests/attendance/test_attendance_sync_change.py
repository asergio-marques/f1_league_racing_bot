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
and not raced, so the recalculation gives them the no-show penalty; auto-sack is set at 3 points
and auto-reserve off, and the division has a Reserve team and a lineup channel, unless a test
says otherwise.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from leaguebot.attendance.cogs.attendance_cog import AttendanceCog
from leaguebot.attendance.services.hooks.attendance_after_review import AttendanceAfterReview
from leaguebot.core.services.drivers.placement_service import PlacementService
from leaguebot.core.db.database import get_connection, run_migrations
from tests.support.change_queue import (
    SERVER_ID,
    Clock,
    change_rows,
    discard_job,
    http_error,
    league_bot,
    league_queue,
    member_interaction,
    restart_queue,
    run_queue,
    seed_server,
    set_guilds,
    set_user,
    stopped_job,
    text_channel,
    tier_member,
    updated_reply,
)
from tests.results.test_verdict_announcement_jobs import drawn  # noqa: F401 — the fixture
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
LINEUP_CHANNEL = 712
MANAGER_ID = 77

#: The heading over round 1's sanctions in the verdicts channel, in words, images being off.
HEADING = "**Season 1 Division 1 Round 1**"

#: The change `/attendance sync` asks of the queue.
SYNC_KIND = "attendance.sync"

class _League:
    """The bot, its server and the channels the sync posts to."""

    def __init__(self, bot: Any, guild: Any, db_path: str, channels: dict[int, Any]) -> None:
        self.bot, self.guild, self.db_path, self.channels = bot, guild, db_path, channels

    def verdicts(self) -> list[str]:
        """What the division's verdicts channel was sent, in order: the heading over the round's
        sanctions, then their cards."""
        return list(self.channels[VERDICTS_CHANNEL].sent)

    def sheets(self) -> list[str]:
        """What the division's attendance channel was sent: the sheet. Nothing, once the channel
        is gone from the server."""
        channel = self.channels.get(ATTENDANCE_CHANNEL)
        return [] if channel is None else list(channel.sent)

    def log_lines(self) -> list[str]:
        """Every line the log channel was sent."""
        return [unruled(str(line)) for line in self.bot.log_channel.sent]

    def sync_lines(self) -> list[str]:
        """Every line of the log channel's that records a sync."""
        return [unruled(str(line)) for line in self.bot.log_channel.sent
                if "| /attendance sync |" in str(line)]


async def _league(tmp_path, *, rounds: dict[int, str] | None = None,
                  no_show_penalty: int = 5, autosack: int | None = 3,
                  autoreserve: int | None = None, reserve: bool = True) -> _League:
    """The league of the module docstring. *rounds* gives each round of Division 1 by number
    (its id the same) and its status, round 1 final where none is given; the driver accepted each
    finalised round and did not race it. The no-show penalty is *no_show_penalty*, the auto-sack
    and auto-reserve thresholds *autosack* and *autoreserve*; without *reserve* the division has
    no Reserve team."""
    rounds = rounds or {1: "FINAL"}
    db_path = os.path.join(str(tmp_path), "attendance_sync.db")
    await run_migrations(db_path)
    await seed_server(db_path)
    await set_module_flag(db_path, "attendance", True)
    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE attendance_config SET autosack_threshold = ?, autoreserve_threshold = ?, "
            "no_show_penalty = ?",
            (autosack, autoreserve, no_show_penalty),
        )
        await db.execute(
            "INSERT INTO seasons (id, season_number, start_date, status, stage) "
            "VALUES (?, 1, '2026-01-01', 'ACTIVE', 'ONGOING')",
            (SEASON_ID,),
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id, "
            "lineup_channel_id) VALUES (?, ?, 'Division 1', 1, 555, ?)",
            (DIVISION_ID, SEASON_ID, LINEUP_CHANNEL),
        )
        for number, status in rounds.items():
            await db.execute(
                "INSERT INTO rounds (id, division_id, round_number, format, track_name, "
                "scheduled_at, status) VALUES (?, ?, ?, 'NORMAL', 'Silverstone', ?, ?)",
                (number, DIVISION_ID, number, f"2026-09-{number:02d}T18:00:00+00:00", status),
            )
        teams = [(ALPHA, "Alpha", 2, 0)] + ([(RESERVE, "Reserve", 6, 1)] if reserve else [])
        for team_id, name, seats, is_reserve in teams:
            await db.execute(
                "INSERT INTO team_instances (id, division_id, name, full_name, max_seats, "
                "is_reserve) VALUES (?, ?, ?, ?, ?, ?)",
                (team_id, DIVISION_ID, name, name, seats, is_reserve),
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
        LINEUP_CHANNEL: text_channel(LINEUP_CHANNEL),
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
    # The bot's own user, logged in, which a sanction it applies is recorded as.
    bot_user = MagicMock(id=900)
    bot_user.__str__.return_value = "League Bot"
    set_user(bot, bot_user)
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


@pytest.mark.parametrize(
    "no_show_penalty,applied,told",
    [(5, 1, ["Sanctions applied:", "• <@5101> (Full Timer) — autosack"]),
     (0, 0, ["No sanction was owed."])],
    ids=["a-sanction-owed", "nothing-owed"],
)
async def test_a_sync_is_one_change_acknowledged_at_once_and_logged_once_done(
    tmp_path, no_show_penalty, applied, told
):
    """Round 1 of Division 1 is final; its driver accepted and did not race, with a no-show
    penalty of *no_show_penalty* against an auto-sack of 3. Manager 77 runs `/attendance sync`
    from round 1. One change is asked of the queue and the manager is answered with the queue's
    acknowledgement; nothing is written until the queue runs it: the driver is still seated and
    the round not recalculated. The queue is run: it finishes, the driver is sacked only where
    owed, the acknowledgement is updated with the reply the command always gave (the sanction
    applied, or that none was owed), and the log channel's one line for the sync is its Success
    line, naming the division, the round it ran from and the number of sanctions applied."""
    league = await _league(tmp_path, no_show_penalty=no_show_penalty)

    interaction = await _sync(league)

    assert len(await _sync_changes(league.db_path)) == 1
    assert _first_said(interaction).startswith("⏳ ")
    assert await _seated(league.db_path) == [DRIVER_PROFILE]
    assert (await _attendance(league.db_path, 1))["attended"] is None

    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    assert await _seated(league.db_path) == ([] if applied else [DRIVER_PROFILE])
    reply = updated_reply(interaction)
    assert "Attendance of **Division 1** recalculated from round 1 on." in reply
    for words in told:
        assert words in reply
    assert "Still not applied" not in reply
    [line] = league.sync_lines()
    assert "| /attendance sync | Success" in line
    assert "division: Division 1" in line
    assert "from round: 1" in line
    assert f"sanctions applied: {applied}" in line


async def test_a_sync_recalculates_from_the_named_round_on_and_leaves_earlier_rounds_alone(
    tmp_path,
):
    """Rounds 1, 2 and 3 of Division 1 are final and round 4 is not yet run; the driver accepted
    rounds 1 to 3 and raced none, and none has been recalculated. The no-show penalty is 2 and
    auto-sack is at 3, so the sack is owed only once round 3 counts: round 2 alone gives 2 points.
    Manager 77 runs `/attendance sync` from round 2, as one change, and the queue is run. Rounds 2
    and 3, every approved round from the one named on, are recalculated, each with the no-show
    penalty; round 1, before it, is left as it was, and round 4, not run, has no row and is given
    none. The driver is sacked against round 3, the latest of them, and the one sheet posted is
    round 3's: it gives the driver's total after round 3, 4 points, not round 2's 2."""
    league = await _league(
        tmp_path, rounds={1: "FINAL", 2: "FINAL", 3: "FINAL", 4: "NOT_RUN"}, no_show_penalty=2,
    )

    await _sync(league, round_number=2)
    await run_queue(league.bot)

    assert len(await _sync_changes(league.db_path)) == 1
    assert await stopped_job(league.db_path) is None
    assert await _attendance(league.db_path, 2) == {"attended": 0, "points_awarded": 2}
    assert await _attendance(league.db_path, 3) == {"attended": 0, "points_awarded": 2}
    assert await _attendance(league.db_path, 1) == {"attended": None, "points_awarded": None}
    async with get_connection(league.db_path) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM driver_round_attendance WHERE round_id = 4")
        assert (await cursor.fetchone())[0] == 0
    assert await _seated(league.db_path) == []
    [sheet] = league.sheets()
    assert "4 attendance points" in sheet
    assert "— 2 attendance points" not in sheet


async def test_a_stop_between_the_sanctions_and_their_cards_finishes_on_restart(tmp_path):
    """Round 1 is final and its driver owes an auto-sack. Manager 77 runs `/attendance sync`
    from round 1, and the queue runs one job at a time: at no point is the round seen
    recalculated without the sack or the sack without the round, the two being one save. The bot
    stops just after that save, before any card is posted; it comes back and its queue runs on.
    The change finishes: the verdicts channel, which had no heading for round 1, is sent the
    round's heading and then the sack's one card, the sheet is posted once in the attendance
    channel, the driver stays sacked, and the sync's one line is written."""
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
    assert league.verdicts() == []

    await restart_queue(league.bot)
    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    heading, _card = league.verdicts()
    assert heading == HEADING
    assert len(league.sheets()) == 1
    assert await _seated(league.db_path) == []
    assert len(league.sync_lines()) == 1


async def test_a_second_sync_announces_nothing_already_announced(tmp_path):
    """Round 1 is final and its driver owes an auto-sack. Manager 77 runs `/attendance sync`
    from round 1 and the queue is run: the driver is sacked, and the verdicts channel is sent the
    round's heading and the sack's card. They run it again, and the queue is run: the second sync
    finishes too, finds nothing owed, posts neither a heading nor a card, and its line says no
    sanction was applied."""
    league = await _league(tmp_path)

    await _sync(league)
    await run_queue(league.bot)
    heading, _card = league.verdicts()
    assert heading == HEADING
    await _sync(league)
    await run_queue(league.bot)

    assert len(await _sync_changes(league.db_path)) == 2
    assert await stopped_job(league.db_path) is None
    assert len(league.verdicts()) == 2
    first, second = league.sync_lines()
    assert "sanctions applied: 1" in first
    assert "sanctions applied: 0" in second


async def test_a_sanction_the_sync_cannot_apply_is_named_and_the_sync_still_finishes(tmp_path):
    """Round 1 is final; Division 1 has no Reserve team, and its driver, given the no-show penalty
    of 5 against an auto-reserve of 3 with auto-sack off, is owed an auto-reserve that cannot be
    applied. Manager 77 runs `/attendance sync` from round 1 and the queue is run. The queue does
    not stop and the round's recalculation is saved, the driver staying in Alpha. The updated
    acknowledgement lists the auto-reserve as still not applied, with its reason. The sync's one
    line in the log channel reads Incomplete and names the driver (their mention wrapped so that
    it notifies no one), the sanction and the reason,
    ending with the `/attendance sync` command that finishes the job; no other entry repeats
    it."""
    league = await _league(tmp_path, autosack=None, autoreserve=3, reserve=False)

    interaction = await _sync(league)
    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    assert await _attendance(league.db_path, 1) == {"attended": 0, "points_awarded": 5}
    assert await _seated(league.db_path) == [DRIVER_PROFILE]
    failure = "(Full Timer) — autoreserve: the division has no Reserve team"
    reply = updated_reply(interaction)
    assert "Still not applied" in reply
    assert f"• <@5101> {failure}" in reply
    [line] = league.sync_lines()
    assert "| /attendance sync | Incomplete" in line
    # The log channel names the driver without notifying them.
    assert f"`<@5101>` {failure}" in line
    assert line.rstrip().endswith("`/attendance sync division:Division 1 round:1`.")
    assert [each for each in league.log_lines() if "no Reserve team" in each] == [line]
    assert league.verdicts() == []


@pytest.mark.parametrize("other_profile", [100, 102],
                         ids=["refused-driver-read-first", "refused-driver-read-second"])
async def test_a_sanction_the_sync_cannot_apply_never_stops_the_others(tmp_path, other_profile):
    """Round 1 is final and its driver owes an auto-sack. A second driver of Division 1 (profile
    *other_profile*, a test driver, Discord user 5102), seated in Alpha but with their placement
    not yet confirmed, accepted round 1 and did not race it either, so is owed an auto-sack too,
    which cannot be applied: only a confirmed placement can be sacked. The refused driver is read
    before the other, and after them. Manager 77 runs `/attendance sync` from round 1 and the
    queue is run. The queue does not stop. The first driver is sacked and carded, whichever was
    read first; the second stays seated. The updated acknowledgement lists the first under
    "Sanctions applied" and the second under "Still not applied", with the reason; the sync's one
    line reads Incomplete with one sanction applied; and the log channel is told of no no-op."""
    league = await _league(tmp_path)
    async with get_connection(league.db_path) as db:
        await db.execute(
            "INSERT INTO driver_profiles (id, discord_user_id, current_state, is_test_driver, "
            "test_display_name) VALUES (?, '5102', 'ASSIGNED', 1, 'Second Timer')",
            (other_profile,),
        )
        await db.execute(
            "INSERT INTO team_seats (id, team_instance_id, seat_number, driver_profile_id) "
            "VALUES (21, ?, 2, ?)",
            (ALPHA, other_profile),
        )
        await db.execute(
            "INSERT INTO driver_season_assignments (driver_profile_id, season_id, division_id, "
            "team_seat_id, committed) VALUES (?, ?, ?, 21, 0)",
            (other_profile, SEASON_ID, DIVISION_ID),
        )
        await db.execute(
            "INSERT INTO driver_round_attendance (round_id, division_id, driver_profile_id, "
            "rsvp_status) VALUES (1, ?, ?, 'ACCEPTED')",
            (DIVISION_ID, other_profile),
        )
        await db.commit()

    interaction = await _sync(league)
    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    assert await _seated(league.db_path) == [other_profile]
    heading, card = league.verdicts()
    assert heading == HEADING
    reply = updated_reply(interaction)
    applied, _, not_applied = reply.partition("Still not applied")
    assert "Sanctions applied:" in applied
    assert "• <@5101> (Full Timer) — autosack" in applied
    assert "<@5102>" not in applied
    assert "• <@5102> (Second Timer) — autosack: Only a driver whose placement is confirmed" \
        in not_applied
    assert "<@5101>" not in not_applied
    [line] = league.sync_lines()
    assert "| /attendance sync | Incomplete" in line
    assert "sanctions applied: 1" in line
    assert not any("No-op" in each for each in league.log_lines())


async def _amend_round_1(league: _League) -> str:
    async with get_connection(league.db_path) as db:
        await db.execute(
            "INSERT INTO round_amend_channels (round_id, channel_id, session_types, created_at) "
            "VALUES (1, 8200, '[\"FEATURE_RACE\"]', '2026-10-01T11:00:00+00:00')",
        )
        await db.commit()
    return "is being amended"


async def _move_to_pending_completion(league: _League) -> str:
    async with get_connection(league.db_path) as db:
        await db.execute(
            "UPDATE seasons SET stage = 'PENDING_COMPLETION' WHERE id = ?", (SEASON_ID,)
        )
        await db.commit()
    return "only while the season is ongoing"


async def _lose_the_attendance_channel(league: _League) -> str:
    league.channels.pop(ATTENDANCE_CHANNEL)
    return f"the attendance channel (id {ATTENDANCE_CHANNEL}) is not in the server"


#: What can change between a sync being asked and the queue taking it up, each changing it and
#: returning the words its refusal carries.
CHANGED_SINCE = {
    "amended": _amend_round_1,
    "no-longer-ongoing": _move_to_pending_completion,
    "channel-gone": _lose_the_attendance_channel,
}


@pytest.mark.parametrize("changed", list(CHANGED_SINCE), ids=list(CHANGED_SINCE))
async def test_a_sync_is_refused_as_it_starts_where_the_league_changed_since_it_was_asked(
    tmp_path, changed
):
    """Round 1 is final and its driver owes an auto-sack; the bot can see and post in both of
    Division 1's channels. Manager 77 runs `/attendance sync` from round 1 and it is asked of the
    queue. Before the queue takes it up, the league changes (*changed*): an amendment of round 1
    is opened, the season moves on to Pending completion, or the attendance channel is deleted.
    The queue is run: the sync is refused, judged again as it starts, and ends REFUSED with
    nothing written: the driver is still seated, round 1 not recalculated, no card or sheet
    posted. The manager's reply and the one refusal line in the log channel give the reason, the
    line naming the manager by a mention wrapped so that it notifies no one.
    The queue does not stop."""
    league = await _league(tmp_path)
    # The bot's own member in the server, so that its channels are judged.
    league.guild.get_member = MagicMock(return_value=MagicMock())

    interaction = await _sync(league)
    assert len(await _sync_changes(league.db_path)) == 1
    reason = await CHANGED_SINCE[changed](league)
    await run_queue(league.bot)

    [change] = await _sync_changes(league.db_path)
    assert change["state"] == "REFUSED"
    assert await stopped_job(league.db_path) is None
    assert await _seated(league.db_path) == [DRIVER_PROFILE]
    assert await _attendance(league.db_path, 1) == {"attended": None, "points_awarded": None}
    assert league.verdicts() == []
    assert league.sheets() == []
    assert reason in updated_reply(interaction)
    refused = [line for line in league.log_lines() if "`/attendance sync` refused" in line]
    assert len(refused) == 1
    assert refused[0].startswith("⛔ `/attendance sync` refused for Manager (`<@77>`) — ")
    assert reason in refused[0]
    assert league.sync_lines() == []


async def test_a_sync_s_card_discord_refuses_stops_the_queue_and_a_discard_names_it(tmp_path):
    """Round 1 is final and its driver owes an auto-sack. Manager 77 runs `/attendance sync`
    from round 1 and the queue is run: the round's heading is posted in the verdicts channel, but
    Discord refuses the sack's card (Missing Access). The queue stops at the card, the driver
    already sacked. A league admin discards it: the queue goes on, the driver stays sacked, and
    the log channel names the driver's auto-sack as applied but not announced."""
    league = await _league(tmp_path)
    verdicts = league.channels[VERDICTS_CHANNEL]
    post = verdicts.send.side_effect

    async def _refuse_the_card(content: str = "", **kwargs: Any) -> Any:
        if verdicts.sent:  # the heading stands, so this is the card
            raise http_error(discord.Forbidden, status=403, text="Missing Access")
        return await post(content, **kwargs)

    verdicts.send.side_effect = _refuse_the_card

    await _sync(league)
    await run_queue(league.bot)

    assert len(await _sync_changes(league.db_path)) == 1
    assert await stopped_job(league.db_path) is not None
    assert league.verdicts() == [HEADING]
    assert await _seated(league.db_path) == []

    await discard_job(league.bot)

    assert await stopped_job(league.db_path) is None
    assert await _seated(league.db_path) == []
    named = [line for line in league.log_lines() if "5101" in line and "autosack" in line]
    assert any("not announced" in line for line in named)


#: The roles a sack takes from the driver: the division's, Alpha's and the driver role.
DIVISION_ROLE, ALPHA_ROLE, DRIVER_ROLE = 555, 9010, 9020


async def test_a_role_change_discord_refuses_leaves_the_sync_s_sack_applied_and_the_queue_running(
    tmp_path, caplog
):
    """Round 1 is final and its driver owes an auto-sack. The driver is a real one (Discord user
    5101), not a test driver, holding the division's role, Alpha's team role and the driver role,
    so a sack takes their roles back; Discord refuses to take any of them (Missing Permissions).
    Manager 77 runs `/attendance sync` from round 1 and the queue is run. The sack is saved all
    the same, the driver no longer seated; the queue does not stop; the verdicts channel is sent
    the round's heading and the sack's card; the sync's line reads Success with one sanction
    applied; and the refusal is noted in the host's log, naming the member."""
    league = await _league(tmp_path)
    async with get_connection(league.db_path) as db:
        await db.execute(
            "UPDATE driver_profiles SET is_test_driver = 0 WHERE id = ?", (DRIVER_PROFILE,)
        )
        await db.execute("UPDATE server_configs SET driver_role_id = ?", (DRIVER_ROLE,))
        await db.execute(
            "INSERT INTO team_role_configs (team_name, role_id, updated_at) VALUES ('Alpha', ?, ?)",
            (ALPHA_ROLE, NOW.isoformat()),
        )
        await db.commit()
    roles = {role_id: MagicMock(id=role_id) for role_id in (DIVISION_ROLE, ALPHA_ROLE, DRIVER_ROLE)}
    league.guild.get_role = MagicMock(side_effect=roles.get)
    member = MagicMock()
    member.id = 5101
    member.guild = league.guild
    member.roles = list(roles.values())
    member.remove_roles = AsyncMock(
        side_effect=http_error(discord.Forbidden, status=403, text="Missing Permissions")
    )
    # The bot's own member, as the server gives it, beside the driver.
    bot_member = MagicMock()
    league.guild.get_member = MagicMock(
        side_effect=lambda user_id: member if int(user_id) == 5101 else bot_member
    )
    league.guild.fetch_member = AsyncMock(return_value=member)

    with caplog.at_level(logging.WARNING):
        await _sync(league)
        await run_queue(league.bot)

    assert len(await _sync_changes(league.db_path)) == 1
    member.remove_roles.assert_awaited()
    assert await _seated(league.db_path) == []
    assert await stopped_job(league.db_path) is None
    heading, _card = league.verdicts()
    assert heading == HEADING
    [line] = league.sync_lines()
    assert "| /attendance sync | Success" in line
    assert "sanctions applied: 1" in line
    assert any(record.levelno >= logging.WARNING and "5101" in record.getMessage()
               for record in caplog.records)


async def test_a_driver_already_signed_off_is_passed_over_with_a_no_op_line(tmp_path):
    """Round 1 is final; its driver, over the auto-sack threshold of 3 with the no-show penalty
    of 5, has already been signed off (Not Signed Up) though still seated in Alpha. Manager 77 runs
    `/attendance sync` from round 1 and the queue is run. The queue does not stop; the driver is
    not sacked again, so stays seated as they were; no heading or card is posted; the sync's line
    reads Success, an expected no-op being no failure, and says no sanction was applied; the
    updated acknowledgement lists nothing as still not applied; and the log channel is told of
    the no-op."""
    league = await _league(tmp_path)
    async with get_connection(league.db_path) as db:
        await db.execute(
            "UPDATE driver_profiles SET current_state = 'NOT_SIGNED_UP' WHERE id = ?",
            (DRIVER_PROFILE,),
        )
        await db.commit()

    interaction = await _sync(league)
    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    assert await _seated(league.db_path) == [DRIVER_PROFILE]
    assert league.verdicts() == []
    assert "Still not applied" not in updated_reply(interaction)
    [line] = league.sync_lines()
    assert "| /attendance sync | Success" in line
    assert "sanctions applied: 0" in line
    assert any("ATTENDANCE_AUTOSACK | No-op" in each for each in league.log_lines())


#: Division 2 of the season, its lineup and attendance channels and its team, where the driver
#: also races.
OTHER_DIVISION, OTHER_LINEUP_CHANNEL, OTHER_ATTENDANCE_CHANNEL, OTHER_TEAM = 2, 713, 714, 12


@pytest.mark.parametrize("other_has_a_sheet", [True, False],
                         ids=["division-2-has-a-sheet", "division-2-has-no-sheet"])
async def test_a_sync_s_sack_of_a_driver_in_two_divisions_posts_both_lineups(
    tmp_path, other_has_a_sheet,
):
    """Round 1 of Division 1 is final and its driver owes an auto-sack; the driver also holds a
    confirmed seat in Division 2's Alpha, and Division 2 has an attendance channel. Where
    Division 2 has a sheet (its round 201 scored), and where it has none yet. Manager 77 runs
    `/attendance sync` from round 1 and the queue is run. The sack takes the driver out of both
    divisions, and the lineup of each is posted once: Division 1's and Division 2's. Division 2's
    sheet is posted again once where it had one, to correct it, and not at all where it had none;
    Division 1's is posted once."""
    league = await _league(tmp_path)
    async with get_connection(league.db_path) as db:
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id, "
            "lineup_channel_id) VALUES (?, ?, 'Division 2', 2, 556, ?)",
            (OTHER_DIVISION, SEASON_ID, OTHER_LINEUP_CHANNEL),
        )
        await db.execute(
            "INSERT INTO team_instances (id, division_id, name, full_name, max_seats, "
            "is_reserve) VALUES (?, ?, 'Alpha', 'Alpha', 2, 0)",
            (OTHER_TEAM, OTHER_DIVISION),
        )
        await db.execute(
            "INSERT INTO team_seats (id, team_instance_id, seat_number, driver_profile_id) "
            "VALUES (30, ?, 1, ?)",
            (OTHER_TEAM, DRIVER_PROFILE),
        )
        await db.execute(
            "INSERT INTO driver_season_assignments (driver_profile_id, season_id, division_id, "
            "team_seat_id, committed) VALUES (?, ?, ?, 30, 1)",
            (DRIVER_PROFILE, SEASON_ID, OTHER_DIVISION),
        )
        await db.execute(
            "INSERT INTO attendance_division_config (division_id, attendance_channel_id) "
            "VALUES (?, ?)",
            (OTHER_DIVISION, str(OTHER_ATTENDANCE_CHANNEL)),
        )
        if other_has_a_sheet:
            await db.execute(
                "INSERT INTO rounds (id, division_id, round_number, format, track_name, "
                "scheduled_at, status) VALUES (201, ?, 1, 'NORMAL', 'Monza', "
                "'2026-09-01T18:00:00+00:00', 'FINAL')",
                (OTHER_DIVISION,),
            )
            await db.execute(
                "INSERT INTO driver_round_attendance (round_id, division_id, driver_profile_id, "
                "rsvp_status, attended, points_awarded, total_points_after) "
                "VALUES (201, ?, ?, 'ACCEPTED', 1, 0, 0)",
                (OTHER_DIVISION, DRIVER_PROFILE),
            )
        await db.commit()
    for channel_id in (OTHER_LINEUP_CHANNEL, OTHER_ATTENDANCE_CHANNEL):
        league.channels[channel_id] = text_channel(channel_id)
        league.channels[channel_id].guild = league.guild
    league.bot.lineup_poster.refresh_lineup = AsyncMock(return_value=None)

    await _sync(league)
    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    async with get_connection(league.db_path) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM team_seats WHERE driver_profile_id = ?", (DRIVER_PROFILE,)
        )
        assert (await cursor.fetchone())[0] == 0
    asked = sorted(call.args[1] for call in
                   league.bot.lineup_poster.refresh_lineup.await_args_list)
    assert asked == [DIVISION_ID, OTHER_DIVISION]
    assert len(league.sheets()) == 1
    assert len(league.channels[OTHER_ATTENDANCE_CHANNEL].sent) == (1 if other_has_a_sheet else 0)


#: The driver's display name on the server, which the card's picture names them by.
SERVER_NAME = "Racer"


async def _a_member_owed_an_autoreserve(league: _League) -> None:
    """Make the driver a real one (Discord user 5101, known to the server as "Racer"), not a test
    driver, owed an auto-reserve rather than an auto-sack: the no-show penalty of 5 against an
    auto-reserve of 3, auto-sack off."""
    async with get_connection(league.db_path) as db:
        await db.execute(
            "UPDATE driver_profiles SET is_test_driver = 0 WHERE id = ?", (DRIVER_PROFILE,)
        )
        await db.commit()
    member = MagicMock(spec=discord.Member)
    member.id = 5101
    member.display_name = SERVER_NAME
    member.mention = "<@5101>"
    member.guild = league.guild
    member.roles = []
    member.add_roles = AsyncMock()
    member.remove_roles = AsyncMock()
    bot_member = MagicMock()
    league.guild.get_member = MagicMock(
        side_effect=lambda user_id: member if int(user_id) == 5101 else bot_member
    )
    league.guild.fetch_member = AsyncMock(return_value=member)


@pytest.mark.parametrize("picture", [False, True], ids=["as-text", "drawn"])
async def test_a_sync_s_sanction_card_names_the_driver(tmp_path, request, picture):
    """Round 1 is final; its driver, a member of the server known as "Racer" (Discord user
    5101), is owed an auto-reserve. With the verdict graphic off, and with it on. Manager 77 runs
    `/attendance sync` from round 1 and the queue is run whole. As text, the card in the
    verdicts channel, after the round's heading, mentions the driver as <@5101> and says they
    were moved to reserve. As a picture, the card is drawn under "Racer", never their user id."""
    drawn = request.getfixturevalue("drawn") if picture else None
    league = await _league(tmp_path, autosack=None, autoreserve=3)
    await _a_member_owed_an_autoreserve(league)

    await _sync(league)
    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    if drawn is None:
        heading, card = league.verdicts()
        assert heading == HEADING
        assert "<@5101>" in card
        assert "Moved to Reserve" in card
    else:
        assert [item["driver_name"] for item in drawn] == [SERVER_NAME]
        assert "5101" not in drawn[0]["driver_name"]


async def test_a_sync_s_sanction_card_whose_picture_fails_is_posted_as_text_and_says_why(
    tmp_path, drawn, monkeypatch,
):
    """As above, the verdict graphic on, but the image module's step that builds the picture
    raises a fault it does not catch itself (a file it reads cannot be opened). Manager 77 runs
    `/attendance sync` from round 1 and the queue is run. The card is posted as text naming the
    driver and their move to reserve, the queue runs to the end, and the log channel gains one
    line naming round 1 of Division 1, the verdict picture, the fault, that the card went out as
    text, and that no command posts it again."""
    from leaguebot.image.services import image_verdict_post

    fault = "the verdict picture's file could not be opened"

    async def _fails(*_args: Any, **_kwargs: Any) -> Any:
        raise OSError(fault)

    monkeypatch.setattr(image_verdict_post, "build_drawing", _fails)
    league = await _league(tmp_path, autosack=None, autoreserve=3)
    await _a_member_owed_an_autoreserve(league)

    await _sync(league)
    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    [card] = [each for each in league.verdicts() if "<@5101>" in each]
    assert "Moved to Reserve" in card
    faults = [line.lower() for line in league.log_lines() if fault in line]
    assert len(faults) == 1, league.log_lines()
    [line] = faults
    assert "division 1" in line and "round 1" in line, line
    assert "verdict" in line and ("picture" in line or "image" in line), line
    assert "text" in line, line
    assert "to put it right: fix the verdict template; no command posts a sanction's card again" \
        in line, line
