"""Finishing at start a signup window's close off the queue that a stop cut off (#439, slice 5).

A close off the queue (`/signup close`, its timer, a restart past the close time, turning signup
off) returns each driver still filling in the wizard in a save of their own, marking them owed
their closing notice by a close off the queue, then tells and locks each one in turn and clears
the marks of those it reached in its audit save. A stop in between leaves the later drivers
returned, untold, with a writable channel nobody deletes. When the bot starts, it asks the change
queue, as the bot, for `signup.close.finish`: each marked driver whose channel's deletion is not
already armed is told once, then their channel closed, every marked account's mark taken, and
one line written. A notice Discord refuses stops the queue, as on any change.

Every test drives the real queue through `tests/support/season_league.py`: the ongoing season with
the signup window open, the real signup module and driver services, and the support's wizard
double recording each notice and lock. Whether a channel's deletion stands is read of the
scheduler (`has_job`), which the test answers from the channels the double locked.
"""
from __future__ import annotations

import inspect
import re
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord

from leaguebot.core.db.database import get_connection
from tests.support.change_queue import (
    change_rows,
    discard_job,
    http_error,
    retry_job,
    run_queue,
    stopped_job,
)
from tests.support.season_league import (
    SIGNING_UP,
    SIGNUP_CHANNEL,
    driver_state,
    ongoing_league,
    signing_up,
    window_open,
)

#: The change that finishes a close a stop cut off.
CLOSE_FINISH_KIND = "signup.close.finish"
#: A second driver part-way through signing up, beside `SIGNING_UP` (105).
SECOND = 107
#: What a signup channel is told where the window closes under it.
SIGNUPS_CLOSED = "🔒 Signups have closed. This channel will be automatically deleted in 24 hours."
#: The head of the line the finish writes.
FINISHED = "System | Signups closed | the close a stop cut off was finished"
#: The Sign Up button of a window opened and closed before the one now open.
OLDER_WINDOW = 6100
#: The head of the line the window's close writes where the finish closes it.
CLOSED_AT_START = "🔒 Signups closed at start-up, finishing a close a stop cut off"


class _Killed(BaseException):
    """The bot's process killed: no handler of the bot's sees it, nothing more is saved."""


async def _league(tmp_path: Any) -> Any:
    """The ongoing season with the signup window open, drivers 105 and 107 part-way through the
    wizard, and the scheduler answering that a channel's deletion stands for each driver whose
    channel the wizard double locked."""
    league = await ongoing_league(tmp_path, signups_open=True)
    for user_id in (SIGNING_UP, SECOND):
        await signing_up(league, user_id, state="PENDING_SIGNUP_COMPLETION")
    league.bot.scheduler_service.has_job = MagicMock(
        side_effect=lambda job_id: job_id in {
            f"wizard_channel_delete_{user_id}" for user_id in league.locked
        }
    )
    # No channel was told and locked with its deletion lost, unless a test says so.
    league.bot.wizard_service.rearm_deletion_if_held = AsyncMock(return_value=False)
    return league


async def _marked_by_a_close_off_the_queue(league: Any, user_id: int) -> None:
    """Driver *user_id* returned to Not Signed Up by a close off the queue that never reached
    them: their wizard marked over and owed its notice (2), by the close of an older window
    (`OLDER_WINDOW`) than the one now open, which the finish therefore leaves open."""
    await league.write(
        "UPDATE driver_profiles SET current_state = 'NOT_SIGNED_UP' WHERE discord_user_id = ?",
        str(user_id),
    )
    async with get_connection(league.db_path) as db:
        await league.bot.signup_module_service.end_wizard_on(
            db, str(user_id), by_off_queue_close=True
        )
        await db.commit()
    await league.write(
        "UPDATE signup_wizard_records SET closing_window_message_id = ? WHERE discord_user_id = ?",
        OLDER_WINDOW, str(user_id),
    )


async def _recover(league: Any) -> None:
    from leaguebot.__main__ import _recover_off_queue_closing_notices

    await _recover_off_queue_closing_notices(league.bot)


async def _finishes(league: Any) -> list[dict[str, Any]]:
    return [row for row in await change_rows(league.db_path) if row["kind"] == CLOSE_FINISH_KIND]


def _lines(league: Any) -> list[str]:
    """The finish's lines in the log channel, without the rule the channel sets beneath each."""
    return [re.sub(r"\n―+$", "", line) for line in league.bot.log_channel.sent if FINISHED in line]


def _notices_and_locks(league: Any, user_id: int) -> list[str]:
    return [kind for kind, uid, _n in league.events if kind in ("notice", "lock") and uid == user_id]


async def test_a_close_cut_off_by_a_kill_is_finished_at_start_each_untold_driver_told_once_and_closed(
    tmp_path,
):
    """`/signup close` (off the queue) returns drivers 105 and 107, part-way through the wizard.
    105's channel is told and held, its deletion armed; the process is killed while 107's is
    being held, before 107 is told. When the bot starts, the finish is asked and run: 107 is told
    "🔒 Signups have closed …" once, then their channel locked, its deletion armed; 105 is not
    told again; neither is owed anything any longer; and one line says "System | Signups closed |
    the close a stop cut off was finished: `<@107>` told and their channel closed"."""
    from leaguebot.core.cogs.module_cog import execute_forced_close

    league = await _league(tmp_path)
    league.hold_fails[SECOND] = _Killed()
    try:
        await execute_forced_close(league.bot, audit_action="SIGNUP_FORCE_CLOSE")
    except _Killed:
        pass
    else:
        raise AssertionError("the close was not cut off")
    assert await driver_state(league, SECOND) == "NOT_SIGNED_UP"
    assert league.notices == [(SIGNING_UP, SIGNUPS_CLOSED)]
    league.hold_fails.clear()

    await _recover(league)
    await run_queue(league.bot)

    assert league.notices == [(SIGNING_UP, SIGNUPS_CLOSED), (SECOND, SIGNUPS_CLOSED)]
    assert league.locked == [SIGNING_UP, SECOND]
    assert _notices_and_locks(league, SECOND)[-2:] == ["notice", "lock"]
    assert await league.bot.signup_module_service.off_queue_closing_notices() == []
    assert _lines(league) == [f"{FINISHED}: `<@{SECOND}>` told and their channel closed"]
    [finish] = await _finishes(league)
    assert finish["state"] == "DONE"


async def test_the_queue_s_own_marks_are_left_to_the_queue(tmp_path):
    """Driver 107 is owed their closing notice by the change queue's close (1) and by nothing
    else. When the bot starts, nothing is asked of the queue for them, and their mark stands for
    the queue's own close to find."""
    league = await _league(tmp_path)
    async with get_connection(league.db_path) as db:
        await league.bot.signup_module_service.end_wizard_on(
            db, str(SECOND), closing_notice_owed=True
        )
        await db.commit()

    await _recover(league)

    assert await _finishes(league) == []
    assert await league.bot.signup_module_service.owed_closing_notices() == [str(SECOND)]


async def test_nothing_is_asked_at_start_where_no_mark_is_left(tmp_path):
    """No driver is owed a closing notice: when the bot starts, nothing is asked of the queue."""
    league = await _league(tmp_path)

    await _recover(league)

    assert await _finishes(league) == []


async def test_a_driver_left_with_no_signup_channel_is_named_at_start(tmp_path):
    """Driver 107 was returned by a close off the queue that never reached them, and their
    signup channel is gone. When the bot starts the finish finds nothing to tell: no lock is
    asked for them and no deletion armed, their mark is taken, and the line names them as having
    had no signup channel left to tell."""
    league = await _league(tmp_path)
    await _marked_by_a_close_off_the_queue(league, SECOND)
    wizard = league.bot.wizard_service
    holding = wizard.trigger_channel_hold.side_effect

    async def _hold(uid: Any, guild: Any, notice: str, **kwargs: Any) -> Any:
        if int(uid) == SECOND:
            from types import SimpleNamespace

            return SimpleNamespace(channel_id=None, posted=False, locked=False, reason=None)
        return await holding(uid, guild, notice, **kwargs)

    wizard.trigger_channel_hold = AsyncMock(side_effect=_hold)

    await _recover(league)
    await run_queue(league.bot)

    [line] = _lines(league)
    assert f"`<@{SECOND}>` had no signup channel left to tell" in line
    assert [call for call in wizard.lock_signup_channel.await_args_list
            if int(call.args[0]) == SECOND] == []
    assert SECOND not in league.locked
    assert await league.bot.signup_module_service.off_queue_closing_notices() == []


async def test_a_refused_notice_at_start_stops_the_queue_before_anything_is_locked(tmp_path):
    """Driver 107 was returned by a close off the queue that never reached them. When the bot
    starts, Discord refuses 107's notice: the queue stops at `signup_notice`, nothing is locked
    and no line is written. A Retry, Discord answering again, tells 107, then locks their
    channel, and the line names them told."""
    league = await _league(tmp_path)
    await _marked_by_a_close_off_the_queue(league, SECOND)
    league.hold_fails[SECOND] = http_error(discord.Forbidden, status=403, text="Missing Access")

    await _recover(league)
    await run_queue(league.bot)

    stopped = await stopped_job(league.db_path)
    assert stopped is not None and stopped["name"] == "signup_notice"
    assert league.locked == [] and league.notices == []
    assert _lines(league) == []

    league.hold_fails.clear()
    await retry_job(league.bot)

    assert league.notices == [(SECOND, SIGNUPS_CLOSED)]
    assert league.locked == [SECOND]
    assert _lines(league) == [f"{FINISHED}: `<@{SECOND}>` told and their channel closed"]
    assert await league.bot.signup_module_service.off_queue_closing_notices() == []


async def test_a_marked_driver_who_has_signed_up_again_is_passed_over_and_their_mark_cleared(
    tmp_path,
):
    """Driver 107 was marked owed their notice by a close off the queue, and the mark outlived
    it: 107 has since begun a new signup and stands Pending Signup Completion. When the bot
    starts, the finish passes 107 over: nothing is posted in their channel, nothing locked, and
    the mark is cleared."""
    league = await _league(tmp_path)
    await _marked_by_a_close_off_the_queue(league, SECOND)
    await league.write(
        "UPDATE driver_profiles SET current_state = 'PENDING_SIGNUP_COMPLETION' "
        "WHERE discord_user_id = ?",
        str(SECOND),
    )

    await _recover(league)
    await run_queue(league.bot)

    assert league.notices == []
    assert league.locked == []
    assert await league.bot.signup_module_service.off_queue_closing_notices() == []
    assert await driver_state(league, SECOND) == "PENDING_SIGNUP_COMPLETION"


async def test_a_held_driver_whose_deletion_was_lost_is_not_told_again(tmp_path):
    """Driver 107 was told and their channel locked by a close off the queue that a stop then
    cut off, and the long stop lost the channel's deletion job. When the bot starts, the finish
    asks the wizard service whether 107's channel was held; it was, and its deletion is armed
    again: 107 is not told a second time, nor locked again, and their mark is cleared."""
    league = await _league(tmp_path)
    await _marked_by_a_close_off_the_queue(league, SECOND)
    league.bot.wizard_service.rearm_deletion_if_held = AsyncMock(
        side_effect=lambda user_id, _guild, notice: int(user_id) == SECOND
        and notice == SIGNUPS_CLOSED
    )

    await _recover(league)
    await run_queue(league.bot)

    assert league.notices == [] and league.locked == []
    asked = league.bot.wizard_service.rearm_deletion_if_held.await_args_list
    assert [int(call.args[0]) for call in asked] == [SECOND]
    assert await league.bot.signup_module_service.off_queue_closing_notices() == []


async def test_a_close_cut_off_before_the_window_was_recorded_closed_closes_it_at_start_first(
    tmp_path,
):
    """`/signup close` (off the queue) returns drivers 105 and 107 and tells and locks 105; the
    process is killed while 107's channel is being held, so the window was never recorded
    closed. When the bot starts, the finish closes the window first, as the close would have:
    "🔒 Signups are now closed." is posted in the signup channel, the window is recorded closed
    and a line says signups closed at start-up; only then is 107 told, and locked."""
    from leaguebot.core.cogs.module_cog import execute_forced_close

    league = await _league(tmp_path)
    league.hold_fails[SECOND] = _Killed()
    try:
        await execute_forced_close(league.bot, audit_action="SIGNUP_FORCE_CLOSE")
    except _Killed:
        pass
    assert await window_open(league)
    league.hold_fails.clear()
    holding = league.bot.wizard_service.trigger_channel_hold.side_effect
    open_when_told: list[tuple[int, bool]] = []

    async def _hold(uid: Any, guild: Any, notice: str, **kwargs: Any) -> Any:
        open_when_told.append((int(uid), await window_open(league)))
        return await holding(uid, guild, notice, **kwargs)

    league.bot.wizard_service.trigger_channel_hold = AsyncMock(side_effect=_hold)

    await _recover(league)
    await run_queue(league.bot)

    assert not await window_open(league)
    assert "🔒 Signups are now closed." in league.texts(SIGNUP_CHANNEL)
    assert any(CLOSED_AT_START in line for line in league.bot.log_channel.sent)
    assert open_when_told == [(SECOND, False)]
    assert league.notices == [(SIGNING_UP, SIGNUPS_CLOSED), (SECOND, SIGNUPS_CLOSED)]
    assert league.locked == [SIGNING_UP, SECOND]
    assert _lines(league) == [f"{FINISHED}: `<@{SECOND}>` told and their channel closed"]


async def test_a_mark_left_by_an_older_window_never_closes_the_window_now_open(tmp_path):
    """Driver 107 is still owed their notice by the close of an older window, and a new window
    stands open, driver 105 part-way through signing up in it. When the bot starts, the finish
    tells and closes 107 alone: the new window stays open and 105 is left signing up."""
    league = await _league(tmp_path)
    await _marked_by_a_close_off_the_queue(league, SECOND)

    await _recover(league)
    await run_queue(league.bot)

    assert await window_open(league)
    assert league.notices == [(SECOND, SIGNUPS_CLOSED)]
    assert await driver_state(league, SIGNING_UP) == "PENDING_SIGNUP_COMPLETION"
    assert not any(CLOSED_AT_START in line for line in league.bot.log_channel.sent)


async def test_a_finish_whose_every_driver_was_already_held_writes_its_line_bare(tmp_path):
    """Driver 107's channel was told and held before the stop, and only the close's audit was
    cut off. When the bot starts, the finish tells nobody and closes nothing, and still writes
    its line, with no driver named: the cut-off close wrote none."""
    league = await _league(tmp_path)
    await _marked_by_a_close_off_the_queue(league, SECOND)
    league.bot.wizard_service.rearm_deletion_if_held = AsyncMock(return_value=True)

    await _recover(league)
    await run_queue(league.bot)

    assert league.notices == []
    assert _lines(league) == [FINISHED]


async def test_the_finish_takes_only_the_marks_its_plan_read_unless_the_plan_was_discarded(
    tmp_path,
):
    """Driver 107 is owed their notice by a close off the queue cut off by a stop. The finish's
    first job reads 107; before its last save, driver 105 is marked by another close off the
    queue. The last save leaves 105's mark for a later finish, naming only 107 as told."""
    league = await _league(tmp_path)
    await _marked_by_a_close_off_the_queue(league, SECOND)
    await _recover(league)
    await run_queue(league.bot, steps=1)
    await _marked_by_a_close_off_the_queue(league, SIGNING_UP)

    await run_queue(league.bot)

    assert _lines(league) == [f"{FINISHED}: `<@{SECOND}>` told and their channel closed"]
    assert await league.bot.signup_module_service.off_queue_closing_notices() == [
        str(SIGNING_UP)
    ]


async def test_a_discarded_finish_names_every_driver_still_marked_and_takes_their_marks(tmp_path):
    """Driver 107 is owed their notice by a close off the queue cut off by a stop. When the bot
    starts, Discord refuses to show the finish 107's channel, which stops the queue at its first
    job, and a league admin discards it. Its last save takes 107's mark and names them as
    returned but not told, their channel to delete by hand; nothing is posted or locked."""
    league = await _league(tmp_path)
    await _marked_by_a_close_off_the_queue(league, SECOND)
    league.bot.wizard_service.rearm_deletion_if_held = AsyncMock(
        side_effect=http_error(discord.Forbidden, status=403, text="Missing Access")
    )
    await _recover(league)
    await run_queue(league.bot)
    stopped = await stopped_job(league.db_path)
    assert stopped is not None and stopped["name"] == "finish_close"

    await discard_job(league.bot)

    [line] = _lines(league)
    assert f"`<@{SECOND}>` was returned to Not Signed Up when signups closed, but was not told" in line
    assert league.notices == [] and league.locked == []
    assert await league.bot.signup_module_service.off_queue_closing_notices() == []


def test_the_recovery_is_asked_after_the_wizards_are_recovered_and_before_the_queue_starts():
    """The finish is asked once the signup wizards are recovered, which pass over these
    wizards, and after the close timers', whose restart close may leave marks of its own; and
    before the queue starts, so that it is saved and run behind any change the queue resumes.
    Read from the source, because all three run inside `on_ready` among a dozen start-up steps no
    test drives whole."""
    from leaguebot import __main__ as bot_module

    source = inspect.getsource(bot_module.main)
    timers = source.index("await _recover_signup_close_timers()")
    wizards = source.index("await bot.wizard_service.recover_wizards()")
    finish = source.index("await _recover_off_queue_closing_notices(bot)")
    start = source.index("await bot.change_queue.start()")
    assert timers < wizards < finish < start
