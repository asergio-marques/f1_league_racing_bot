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
    http_error,
    retry_job,
    run_queue,
    stopped_job,
)
from tests.support.season_league import (
    SIGNING_UP,
    driver_state,
    ongoing_league,
    signing_up,
)

#: The change that finishes a close a stop cut off.
CLOSE_FINISH_KIND = "signup.close.finish"
#: A second driver part-way through signing up, beside `SIGNING_UP` (105).
SECOND = 107
#: What a signup channel is told where the window closes under it.
SIGNUPS_CLOSED = "🔒 Signups have closed. This channel will be automatically deleted in 24 hours."
#: The head of the line the finish writes.
FINISHED = "System | Signups closed | the close a stop cut off was finished"


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
    return league


async def _marked_by_a_close_off_the_queue(league: Any, user_id: int) -> None:
    """Driver *user_id* returned to Not Signed Up by a close off the queue that never reached
    them: their wizard marked over and owed its notice (2)."""
    await league.write(
        "UPDATE driver_profiles SET current_state = 'NOT_SIGNED_UP' WHERE discord_user_id = ?",
        str(user_id),
    )
    async with get_connection(league.db_path) as db:
        await league.bot.signup_module_service.end_wizard_on(
            db, str(user_id), by_off_queue_close=True
        )
        await db.commit()


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
