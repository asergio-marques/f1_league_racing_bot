"""`/attendance post-check-in` — the check-in call a league can post by hand (issue #123).

A check-in call used to reach a division from its scheduled job, from the recovery that re-arms
it after a restart, and from `/test-mode advance` — and from nowhere a league could reach. So a
call that failed to post stayed missed, and a round with no call records **perfect attendance
for the whole division**: no attendance rows are opened, the penalty pass iterates none, and
the sheet draws every cell empty. `_report_call_failure` told the manager to post the call
again and no command existed to do it.

Three rules decided with the user on 2026-09-20 shape what this command will and will not do,
and each has a test here that fails the moment somebody relaxes it:

**A call already standing is never replaced** — `test_a_call_already_standing_is_refused`. A
second call beside the first would split a division's answers across two messages, and the path
that legitimately replaces one is an amendment, which carries every answer across.

**It cannot post a call early** — `test_a_call_not_yet_due_is_refused`. Before the call is due
the scheduled one is still coming, and posting now would quietly override the lead time the
league configured with `/attendance config rsvp-notice`.

**It cannot post a call nobody could answer** — `test_a_round_past_its_deadline_is_refused`.
The buttons lock at the deadline, so a call posted after it arrives dead.

Together the last two confine the command to exactly the window in which a call *should* be
standing and is not, which is the whole of its purpose.

Every test that turns on a moment builds the round's `scheduled_at` from a fixed `now` it also
controls, so none of them can pass today and fail next month.

**On the change queue (#439, slice 6b, C27).** The command asks the queue for the round's call
(`attendance.check_in.call`, the change the timer asks) with the interaction, rather than posting
it at once. It still answers its refusals before it asks, as `/results rounds sync` does, and the
change's check holds them again when it runs, so the second standing-call check the command made
against a scheduled call racing it falls away: the queue runs one change at a time. The
manager's reply is the queue's acknowledgement, updated with the outcome. The refusal tests run
the command against a stand-in queue and assert nothing was asked and nothing posted; the tests of
the call it asks run it against `ongoing_league`'s real queue, where Pro's round 4 is three days
off, its call due and no call posted for it.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import discord

from leaguebot.attendance.cogs.attendance_cog import AttendanceCog
from leaguebot.attendance.services.attendance_service import AttendanceService
from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.models.season import SeasonStage
from leaguebot.core.utils.channel_guard import LEAGUE_MANAGER, TIER_ATTRIBUTE
from tests.support.change_queue import (
    attach_queue,
    change_rows,
    http_error,
    member_interaction,
    retry_job,
    run_queue,
    stopped_job,
)
from tests.support.review_league import block_queue
from tests.support.season_league import DIVISIONS, PRO, SeasonLeague, ongoing_league, round_id
from tests.support.undecorate import undecorate

SERVER_ID = 4242
SEASON_ID = 1
DIVISION_ID = 1
ROUND_ID = 1

#: The round sits five days and a little ahead of this, so the call is due and the deadline is
#: not. Fixed rather than taken from the clock: these tests are *about* the moment.
NOW = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)

NOTICE_DAYS = 5
DEADLINE_HOURS = 2

#: The change the command asks of the queue: the one the check-in timer asks.
KIND = "attendance.check_in.call"
#: Pro's check-in channel and round 4, in `ongoing_league`.
CHECK_IN = DIVISIONS[PRO][3].checkin
ROUND_4 = round_id(PRO, 4)


async def _make_db(tmp_path, *, scheduled_at: datetime, status: str = "NOT_RUN") -> str:
    """A migrated database with one division and one round at *scheduled_at*."""
    db_path = os.path.join(str(tmp_path), "post_check_in.db")
    await run_migrations(db_path)

    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs "
            "(server_id, interaction_role_id, interaction_channel_id, log_channel_id) "
            "VALUES (?, 100, 200, 300)",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO attendance_config (id, rsvp_notice_days, rsvp_deadline_hours) "
            "VALUES (1, ?, ?)",
            (NOTICE_DAYS, DEADLINE_HOURS),
        )
        await db.execute(
            "INSERT INTO seasons (id, season_number, start_date, status) "
            "VALUES (?, 1, '2026-01-01', 'ACTIVE')",
            (SEASON_ID,),
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
            "VALUES (?, ?, 'Division 1', 1, 555)",
            (DIVISION_ID, SEASON_ID),
        )
        await db.execute(
            "INSERT INTO rounds "
            "(id, division_id, round_number, format, track_name, scheduled_at, status) "
            "VALUES (?, ?, 1, 'NORMAL', 'Silverstone Circuit', ?, ?)",
            (ROUND_ID, DIVISION_ID, scheduled_at.isoformat(), status),
        )
        await db.commit()
    return db_path


def _cog(db_path: str, *, stage=SeasonStage.ONGOING) -> AttendanceCog:
    bot = MagicMock()
    bot.db_path = db_path
    bot.module_service.is_attendance_enabled = AsyncMock(return_value=True)
    season = None if stage is None else SimpleNamespace(id=SEASON_ID, stage=stage)
    bot.season_service.get_confirmed_season = AsyncMock(return_value=season)
    bot.season_service.get_divisions = AsyncMock(
        return_value=[SimpleNamespace(id=DIVISION_ID, name="Division 1")]
    )
    bot.attendance_service.get_config = AsyncMock(
        return_value=SimpleNamespace(
            rsvp_notice_days=NOTICE_DAYS, rsvp_deadline_hours=DEADLINE_HOURS
        )
    )
    bot.output_router.post_log = AsyncMock()
    bot.change_queue.ask = AsyncMock(return_value=1)
    return AttendanceCog(bot)


def _interaction() -> MagicMock:
    interaction = MagicMock()
    interaction.user.id = 77
    interaction.user.display_name = "Manager"
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.response.is_done = MagicMock(
        side_effect=lambda: bool(
            interaction.response.defer.await_count
            or interaction.response.send_message.await_count
        )
    )
    interaction.followup.send = AsyncMock()
    return interaction


async def _record_call(db_path: str) -> None:
    """Record a check-in call as standing for the round."""
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO rsvp_embed_messages "
            "(round_id, division_id, message_id, channel_id, posted_at) "
            "VALUES (?, ?, '9001', '7001', ?)",
            (ROUND_ID, DIVISION_ID, NOW.isoformat()),
        )
        await db.commit()


class _Posts:
    """What the command did to post a call: *asked* is the queue's `ask`, *notice* the stand-in
    for `run_rsvp_notice`, which it called before 6b."""

    def __init__(self, asked: AsyncMock, notice: AsyncMock) -> None:
        self.asked = asked
        self.notice = notice

    def assert_nothing(self) -> None:
        """Nothing was asked of the queue and nothing posted."""
        self.asked.assert_not_awaited()
        self.notice.assert_not_awaited()

    def assert_asked_once(self, interaction) -> None:
        """The round's call was asked of the queue once, with the manager's interaction, and
        nothing posted at once."""
        self.notice.assert_not_awaited()
        self.asked.assert_awaited_once()
        assert self.asked.await_args.args[0] == KIND
        assert ROUND_ID in self.asked.await_args.args[1].values()
        assert self.asked.await_args.kwargs["interaction"] is interaction


async def _invoke(cog, interaction, *, division="division 1", round=1, now=NOW,
                  posts=False) -> _Posts:
    """Run the command with the clock pinned, against the stand-in queue of `_cog`.

    `run_rsvp_notice`, which the command called before it asked the queue, is stood in for too,
    created where it is gone; *posts* says whether the stand-in records a call.
    """
    db_path = cog.bot.db_path
    interaction.command = AttendanceCog.post_check_in
    interaction.client = cog.bot

    async def _fake_notice(round_id, bot):
        if posts:
            await _record_call(db_path)

    clock = MagicMock(wraps=datetime)
    clock.now = MagicMock(return_value=now)
    with patch("leaguebot.attendance.cogs.attendance_cog.datetime", clock), patch(
        "leaguebot.attendance.services.rsvp_service.run_rsvp_notice",
        new=AsyncMock(side_effect=_fake_notice), create=True,
    ) as notice:
        await undecorate(AttendanceCog.post_check_in)(cog, interaction, division, round)
    return _Posts(cog.bot.change_queue.ask, notice)


async def _queue_league(tmp_path) -> SeasonLeague:
    """`ongoing_league` with attendance on and a real `AttendanceService`, its queue rebuilt on
    it: Pro's round 4 three days off, its call due and none posted."""
    league = await ongoing_league(tmp_path, attendance=True)
    league.bot.attendance_service = AttendanceService(league.db_path)
    attach_queue(league.bot, league.db_path, now=league.clock)
    await _round_4_in(league, timedelta(days=3))
    return league


async def _round_4_in(league: SeasonLeague, delta: timedelta) -> None:
    when = league.clock.now + delta
    await league.write("UPDATE rounds SET scheduled_at = ? WHERE id = ?",
                       when.replace(tzinfo=None).isoformat(), ROUND_4)


async def _post_check_in(league: SeasonLeague, *, round: int = 4):
    """Run `/attendance post-check-in` for Pro's *round* as manager 77; the queue is not run.
    Gives the interaction, its acknowledgement's message able to be edited."""
    cog = AttendanceCog.__new__(AttendanceCog)
    cog.bot = league.bot
    interaction = member_interaction(league.bot)
    interaction.guild = league.guild
    interaction.command = SimpleNamespace(qualified_name="attendance post-check-in")
    interaction.followup.send.return_value = MagicMock(edit=AsyncMock())
    await undecorate(AttendanceCog.post_check_in)(cog, interaction, "Pro", round)
    return interaction


def _content(call) -> str:
    return str(call.args[0]) if call.args else str(call.kwargs.get("content", ""))


def _acknowledged(interaction) -> str:
    """What the deferred command was first answered with: its first follow-up."""
    calls = interaction.followup.send.await_args_list
    return _content(calls[0]) if calls else ""


def _outcome(interaction) -> str:
    """What the acknowledgement was last updated to say."""
    edits = interaction.followup.send.return_value.edit.await_args_list
    return _content(edits[-1]) if edits else ""


def _sent(league: SeasonLeague) -> list[int]:
    """The ids of the messages posted in Pro's check-in channel, in order."""
    return [mid for kind, cid, mid in league.events if kind == "send" and cid == CHECK_IN]


async def _call_record(league: SeasonLeague) -> list[dict]:
    return await league.rows("SELECT * FROM rsvp_embed_messages WHERE round_id = ?", ROUND_4)


async def _call_states(league: SeasonLeague) -> list[str]:
    return [row["state"] for row in await change_rows(league.db_path) if row["kind"] == KIND]


def _replied(interaction) -> str:
    return "\n".join(str(c.args[0]) for c in interaction.followup.send.await_args_list)


def _logged(cog) -> str:
    return "\n".join(str(c.args[0]) for c in cog.bot.output_router.post_log.await_args_list)


async def _rows(db_path: str) -> int:
    async with get_connection(db_path) as db:
        cur = await db.execute(
            "SELECT COUNT(*) AS n FROM rsvp_embed_messages WHERE round_id = ?", (ROUND_ID,)
        )
        return (await cur.fetchone())["n"]


# ---------------------------------------------------------------------------
# The tier
# ---------------------------------------------------------------------------


async def test_the_command_is_a_league_manager_s():
    """A manager's, like `/attendance sync` — the other repair command in the group."""
    assert getattr(AttendanceCog.post_check_in.callback, TIER_ATTRIBUTE) == LEAGUE_MANAGER


# ---------------------------------------------------------------------------
# The refusals
# ---------------------------------------------------------------------------


async def test_a_round_whose_check_in_is_over_is_refused(tmp_path):
    """A round whose check-in is over, closed by its deadline with no call standing or given up
    at a restart, has nothing left to answer: the command refuses it up front, in one line, and
    posts nothing, rather than reporting a failure the log channel does not explain."""
    db_path = await _make_db(tmp_path, scheduled_at=NOW + timedelta(days=1))
    async with get_connection(db_path) as db:
        await db.execute("UPDATE rounds SET checkin_cleared = 1 WHERE id = ?", (ROUND_ID,))
        await db.commit()
    cog = _cog(db_path)
    interaction = _interaction()

    posts = await _invoke(cog, interaction)

    posts.assert_nothing()
    replies = [str(c.args[0]) for c in interaction.followup.send.await_args_list]
    assert len(replies) == 1
    assert replies[0].startswith("⛔") and "check-in is over" in replies[0]
    assert "The log channel says why" not in replies[0]
    assert "refused" in _logged(cog) and "check-in is over" in _logged(cog)


async def test_a_call_already_standing_is_refused(tmp_path):
    """Decision 1: a standing call is never replaced, and the refusal says what to do instead.

    A second call beside the first splits a division's answers across two messages. An
    amendment is the path that takes one down and carries the answers over.
    """
    db_path = await _make_db(tmp_path, scheduled_at=NOW + timedelta(days=1))
    await _record_call(db_path)
    cog = _cog(db_path)
    interaction = _interaction()

    posts = await _invoke(cog, interaction)

    posts.assert_nothing()
    reply = _replied(interaction)
    assert "already standing" in reply
    assert "Amend the round" in reply


async def test_a_call_not_yet_due_is_refused(tmp_path):
    """Decision 3: the command cannot post a call ahead of its configured lead time.

    The round is six days out and the notice is five, so the scheduled call is still coming.
    Posting now would silently override `/attendance config rsvp-notice`.
    """
    db_path = await _make_db(tmp_path, scheduled_at=NOW + timedelta(days=NOTICE_DAYS + 1))
    cog = _cog(db_path)
    interaction = _interaction()

    posts = await _invoke(cog, interaction)

    posts.assert_nothing()
    assert "not due" in _replied(interaction)
    assert await _rows(db_path) == 0


async def test_a_round_past_its_deadline_is_refused(tmp_path):
    """A call posted after the deadline arrives with its buttons already locked."""
    db_path = await _make_db(
        tmp_path, scheduled_at=NOW + timedelta(hours=DEADLINE_HOURS - 1)
    )
    cog = _cog(db_path)
    interaction = _interaction()

    posts = await _invoke(cog, interaction)

    posts.assert_nothing()
    assert "closed at" in _replied(interaction)
    assert await _rows(db_path) == 0


@pytest.mark.parametrize(
    "offset, what",
    [
        (timedelta(0), "at the moment the race starts"),
        (timedelta(hours=-3), "three hours after it"),
        (timedelta(days=-6), "six days after it"),
    ],
)
async def test_a_disabled_deadline_still_stops_at_the_race_itself(tmp_path, offset, what):
    """A deadline of zero disables the closing of the check-in, not the race (#123).

    `/attendance config rsvp-deadline 0` is a supported setting — only a negative value is
    refused — and the guard used to skip the whole window check when it was set, so the command
    would post a check-in call for a race already run, asking a division whether it is racing in
    something that is already in the results. The round's own moment is the boundary instead.
    """
    db_path = await _make_db(tmp_path, scheduled_at=NOW + offset)
    cog = _cog(db_path)
    cog.bot.attendance_service.get_config = AsyncMock(
        return_value=SimpleNamespace(rsvp_notice_days=NOTICE_DAYS, rsvp_deadline_hours=0)
    )
    interaction = _interaction()

    posts = await _invoke(cog, interaction)

    posts.assert_nothing()
    assert "started at" in _replied(interaction)
    assert await _rows(db_path) == 0


# ---------------------------------------------------------------------------
# Posting it
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "offset, deadline_hours",
    [
        (timedelta(days=1), DEADLINE_HOURS),
        (timedelta(days=NOTICE_DAYS), DEADLINE_HOURS),
        (timedelta(minutes=1), 0),
    ],
    ids=["a-day-out", "exactly-due", "deadline-disabled-a-minute-out"],
)
async def test_it_posts_the_call_for_the_named_round(tmp_path, offset, deadline_hours):
    """The defect itself: a league can reach the round's call, asked of the queue with the
    manager's interaction rather than posted at once.

    The boundary belongs to the window: at the due moment the call is late, not early, and is
    asked of the queue. With the deadline disabled the boundary is the race, not some margin
    before it: a minute out is still answerable."""
    db_path = await _make_db(tmp_path, scheduled_at=NOW + offset)
    cog = _cog(db_path)
    cog.bot.attendance_service.get_config = AsyncMock(
        return_value=SimpleNamespace(
            rsvp_notice_days=NOTICE_DAYS, rsvp_deadline_hours=deadline_hours
        )
    )
    interaction = _interaction()

    posts = await _invoke(cog, interaction, posts=True)

    posts.assert_asked_once(interaction)


async def test_a_call_that_fails_to_post_is_reported_as_failed(tmp_path):
    """Discord refuses the call: the queue stops at it, and the manager's acknowledgement is
    updated to say so, never with a tick. A failed post is not reported as a success, which is
    the silent wrong record the whole issue is about."""
    league = await _queue_league(tmp_path)
    league.channel(CHECK_IN).send_fails = http_error(discord.Forbidden, status=403,
                                                     text="Missing Permissions")

    interaction = await _post_check_in(league)
    await run_queue(league.bot)

    stopped = await stopped_job(league.db_path)
    assert stopped is not None
    outcome = _outcome(interaction)
    assert outcome.startswith("❌") and f"stopped at job #{stopped['id']}" in outcome
    assert "✅" not in outcome
    assert await _call_record(league) == []


async def test_the_run_is_written_to_the_log_channel(tmp_path):
    """Once the queue has posted the call, the log channel has one success line for the command,
    naming the division and the round."""
    league = await _queue_league(tmp_path)

    await _post_check_in(league)
    await run_queue(league.bot)

    [line] = [line for line in league.bot.log_channel.sent
              if "| /attendance post-check-in |" in line]
    assert "| /attendance post-check-in | Success" in line
    assert "division: Pro" in line
    assert "round: 4" in line


# ---------------------------------------------------------------------------
# On the change queue (#439, slice 6b, C27)
# ---------------------------------------------------------------------------


async def test_post_check_in_is_acknowledged_and_updated_with_the_call(tmp_path):
    """The manager is answered that the call is under way, capitalised as every member-facing
    acknowledgement is, and nothing is posted yet; once the queue has run, one call stands in
    Pro's check-in channel, recorded, and the acknowledgement is updated to say it was posted."""
    league = await _queue_league(tmp_path)

    interaction = await _post_check_in(league)
    acknowledged = _acknowledged(interaction)
    assert _sent(league) == []
    await run_queue(league.bot)

    assert acknowledged.startswith("⏳ Posting the check-in call of round 4 in **Pro**.")
    assert _outcome(interaction) == "✅ Check-in call posted for round 4 of **Pro**."
    [posted] = _sent(league)
    [row] = await _call_record(league)
    assert row["message_id"] == str(posted)
    assert await _call_states(league) == ["DONE"]
    assert await stopped_job(league.db_path) is None


async def test_post_check_in_whose_deadline_passed_while_queued_is_refused_when_it_runs(tmp_path):
    """Round 4 starts in 2 hours 10 minutes, so its check-in closes in ten. The queue is stopped
    ahead of the command, which is acknowledged; eleven minutes on the stop is cleared and the
    call is refused in the command's own words, as its deadline has passed. Nothing is posted
    or recorded."""
    league = await _queue_league(tmp_path)
    await _round_4_in(league, timedelta(hours=2, minutes=10))
    holder = await block_queue(league)

    interaction = await _post_check_in(league)
    league.clock.advance(minutes=11)
    holder["fail"] = False
    await retry_job(league.bot)

    assert "could not be answered" in _outcome(interaction)
    assert _sent(league) == []
    assert await _call_record(league) == []
    assert await _call_states(league) == ["REFUSED"]


async def test_post_check_in_while_the_queue_is_stopped_says_so(tmp_path):
    """The queue is stopped at another job: the manager's acknowledgement says the queue is
    stopped at that job and the call runs once it is cleared, and nothing is posted meanwhile."""
    league = await _queue_league(tmp_path)
    await block_queue(league)
    blocker = await stopped_job(league.db_path)
    assert blocker is not None

    interaction = await _post_check_in(league)
    await run_queue(league.bot)

    assert f"The queue is stopped at job #{blocker['id']}" in _acknowledged(interaction)
    assert _sent(league) == []
    assert await _call_record(league) == []
    assert await _call_states(league) == ["QUEUED"]


# ---------------------------------------------------------------------------
# Every refusal is recorded in the log channel (#482)
# ---------------------------------------------------------------------------


async def _arrange(tmp_path, case):
    """The cog and the keyword arguments that bring about refusal *case*."""
    scheduled_at = {
        "not-due": NOW + timedelta(days=NOTICE_DAYS + 1),
        "past-deadline": NOW + timedelta(hours=DEADLINE_HOURS - 1),
    }.get(case, NOW + timedelta(days=1))
    status = "CANCELLED" if case == "cancelled" else "NOT_RUN"
    db_path = await _make_db(tmp_path, scheduled_at=scheduled_at, status=status)
    stage = {
        "not-ongoing": None,
        "not-ongoing-placements": SeasonStage.PLACEMENTS,
        "not-ongoing-pending-completion": SeasonStage.PENDING_COMPLETION,
    }.get(case, SeasonStage.ONGOING)
    cog = _cog(db_path, stage=stage)
    if case == "module-disabled":
        cog.bot.module_service.is_attendance_enabled = AsyncMock(return_value=False)
    if case == "standing":
        await _record_call(db_path)
    if case == "no-configuration":
        cog.bot.attendance_service.get_config = AsyncMock(return_value=None)
    return cog, {
        "unknown-division": {"division": "Division 9"},
        "unknown-round": {"round": 7},
    }.get(case, {})


# *via* is the reply that carries the refusal: the module gate answers before the command
# defers, by `response.send_message`; every later refusal follows the defer, by `followup.send`.
@pytest.mark.parametrize(
    "case,reason,via",
    [
        ("module-disabled", "The Attendance module is not enabled.", "response"),
        ("not-ongoing", "only while the season is ongoing", "followup"),
        ("not-ongoing-placements", "only while the season is ongoing", "followup"),
        ("not-ongoing-pending-completion", "only while the season is ongoing", "followup"),
        ("unknown-division", "Division 'Division 9' not found.", "followup"),
        ("unknown-round", "has no round 7", "followup"),
        # A round that is off has no check-in to answer, so there is nothing to post.
        ("cancelled", "is cancelled", "followup"),
        ("standing", "already standing", "followup"),
        # The timings come from the configuration, so without one there is no window to
        # judge; reading `rsvp_notice_days` off the None `get_config` gives would hand the
        # manager a traceback in the middle of a recovery command instead of an answer.
        ("no-configuration", "No attendance configuration found.", "followup"),
        ("not-due", "is not due until", "followup"),
        ("past-deadline", "so a call posted now could not be answered", "followup"),
    ],
)
async def test_every_refusal_of_a_hand_posted_call_is_recorded(tmp_path, case, reason, via):
    """A6: each refusal is answered once, by the reply *via* names, carrying its reason, and
    writes one "⛔ `/attendance post-check-in` refused" line with its reason, and nothing is
    posted."""
    cog, kwargs = await _arrange(tmp_path, case)
    interaction = _interaction()

    posts = await _invoke(cog, interaction, **kwargs)

    posts.assert_nothing()
    replies = (
        interaction.response.send_message.await_args_list
        + interaction.followup.send.await_args_list
    )
    assert len(replies) == 1
    assert reason in str(replies[0].args[0])
    carried = {
        "response": interaction.response.send_message,
        "followup": interaction.followup.send,
    }[via]
    assert carried.await_count == 1
    logged = [str(c.args[0]) for c in cog.bot.output_router.post_log.await_args_list]
    assert len(logged) == 1
    assert logged[0].startswith(
        "\u26d4 `/attendance post-check-in` refused for Manager (<@77>) \u2014 "
    )
    assert reason in logged[0]
