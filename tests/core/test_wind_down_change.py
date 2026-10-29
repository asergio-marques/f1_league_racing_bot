"""Winding a season down on the change queue (#439, slice 5).

A season in an ongoing stage whose every division is done is wound down by a change of its own:
`wind_down` closes the signup window first; `turn_down` then turns the season's pending
placements down, moves the season on to Pending completion and writes its line, all in one save;
then each driver turned down has a job of their own: a signup in review is told (`signup_notice`)
before their channel is locked (`close_signup`), and an approved driver loses the driver role
(`take_driver_role`). A driver the window's close returns to Not Signed Up is told and locked
the same way. Every failure stops the queue.

Every test drives the real queue through `tests/support/season_league.py`, the season built by the
migrations with "now" pinned: every round final and both divisions finished, the members holding
their roles, Lewis's placement committed and Max's and Charles's not.
"""
from __future__ import annotations

import json
import re
from typing import Any
from unittest.mock import MagicMock, patch

import discord
import pytest

from leaguebot.core.models.change import ChangeOrigin
from tests.support.change_queue import (
    change_rows,
    discard_job,
    http_error,
    queued_log_lines,
    retry_job,
    run_queue,
    step_rows,
    stopped_job,
)
from tests.support.season_league import (
    CHARLES,
    DRIVER_ROLE,
    LEWIS,
    MAX,
    SEASON_ID,
    SIGNING_UP,
    SIGNUP_CHANNEL_BASE,
    WIND_DOWN_KIND,
    driver_state,
    pending_completion_league,
    signing_up,
    window_open,
    wizard_state,
    wizards_recovered,
)





# ── Helpers ─────────────────────────────────────────────────────────────────────────


async def _league(tmp_path: Any, *, stage: str = "ONGOING_PLACEMENTS",
                  signups_open: bool = False) -> Any:
    """The season at *stage*, every division done, Max's and Charles's placements pending and a
    driver (105) in review."""
    league = await pending_completion_league(tmp_path, stage=stage, signups_open=signups_open)
    await league.write(
        "UPDATE driver_season_assignments SET committed = 0 WHERE driver_profile_id IN "
        "(SELECT id FROM driver_profiles WHERE discord_user_id IN (?, ?))",
        str(MAX), str(CHARLES),
    )
    return league


async def _ask(league: Any) -> int:
    change_id = await league.bot.change_queue.ask(
        WIND_DOWN_KIND, {}, origin=ChangeOrigin.BOT, what="Winding the season down",
    )
    assert change_id is not None
    return change_id


async def _jobs(league: Any) -> list[tuple[str, int | None]]:
    """The wind-down's jobs: each one's name, and the driver it is for where it is for one."""
    rows = [row for row in await change_rows(league.db_path) if row["kind"] == WIND_DOWN_KIND]
    assert len(rows) == 1, rows
    jobs = []
    for step in await step_rows(league.db_path, rows[0]["id"]):
        payload = json.loads(step["payload"] or "{}")
        user = payload.get("user_id")
        jobs.append((step["name"], int(user) if user is not None else None))
    return jobs


async def _stage(league: Any) -> str:
    return (await league.season())["stage"]


async def _pending(league: Any) -> int:
    rows = await league.rows(
        "SELECT COUNT(*) AS n FROM driver_season_assignments WHERE season_id = ? AND committed = 0",
        SEASON_ID,
    )
    return rows[0]["n"]


async def _stopped_at(league: Any) -> tuple[str, int | None] | None:
    job = await stopped_job(league.db_path)
    if job is None:
        return None
    user = json.loads(job["payload"] or "{}").get("user_id")
    return job["name"], int(user) if user is not None else None


# ── Defects ─────────────────────────────────────────────────────────────────────────


async def test_the_turn_down_and_the_move_to_pending_completion_are_one_save(tmp_path):
    league = await _league(tmp_path)
    await _ask(league)
    from leaguebot.core.services import season_lifecycle_service

    with patch.object(season_lifecycle_service, "advance_to_pending_completion_on",
                      side_effect=RuntimeError("the move failed")):
        await run_queue(league.bot)
    assert await _pending(league) == 2
    assert await _stage(league) == "ONGOING_PLACEMENTS"
    assert await stopped_job(league.db_path) is not None

    await retry_job(league.bot)
    assert await _pending(league) == 0
    assert await _stage(league) == "PENDING_COMPLETION"


async def test_a_signup_channel_that_cannot_be_held_stops_the_queue_at_its_driver(tmp_path):
    league = await _league(tmp_path)
    await signing_up(league)
    league.hold_fails[SIGNING_UP] = http_error(discord.Forbidden, status=403,
                                               text="Missing Permissions")
    await _ask(league)
    await run_queue(league.bot)
    assert await _stopped_at(league) == ("signup_notice", SIGNING_UP)
    assert SIGNING_UP not in league.locked
    assert await _stage(league) == "PENDING_COMPLETION"


async def test_a_driver_role_discord_will_not_take_back_stops_the_queue(tmp_path):
    league = await _league(tmp_path)
    league.revoke_fails[MAX] = http_error(discord.Forbidden, status=403,
                                          text="Missing Permissions")
    await _ask(league)
    await run_queue(league.bot)
    assert await _stopped_at(league) == ("take_driver_role", MAX)

    del league.revoke_fails[MAX]
    await retry_job(league.bot)
    assert DRIVER_ROLE in league.revoked[MAX]


async def test_the_turned_down_line_is_written_with_the_save(tmp_path):
    league = await _league(tmp_path)
    league.bot.log_channel.send.side_effect = http_error(discord.Forbidden, status=403,
                                                         text="Missing Access")
    await _ask(league)
    await run_queue(league.bot)
    assert await _pending(league) == 0
    assert any("System | Every division is done | Pending placements turned down: 2" in line
               for line in await queued_log_lines(league.db_path))


# ── What it does ────────────────────────────────────────────────────────────────────


async def test_each_turned_down_driver_has_a_job_of_their_own(tmp_path):
    """Every division is done with Max's and Charles's placements pending, a driver (105) in
    review, and signups already shut: the driver in review is told and has their channel locked,
    the two approved drivers turned down lose the driver role while Lewis keeps it, and the line
    says only that the pending placements were turned down, with no claim that signups
    closed."""
    league = await _league(tmp_path)
    await signing_up(league)
    await _ask(league)
    await run_queue(league.bot)
    jobs = await _jobs(league)
    assert jobs[:2] == [("wind_down", None), ("turn_down", None)]
    assert sorted(jobs[2:]) == sorted([
        ("signup_notice", SIGNING_UP), ("close_signup", SIGNING_UP),
        ("take_driver_role", MAX), ("take_driver_role", CHARLES),
    ])
    assert jobs.index(("signup_notice", SIGNING_UP)) < jobs.index(("close_signup", SIGNING_UP))
    assert [uid for uid, _notice in league.notices] == [SIGNING_UP]
    assert league.locked == [SIGNING_UP]
    assert league.revoked == {MAX: [DRIVER_ROLE], CHARLES: [DRIVER_ROLE]}
    assert LEWIS not in league.revoked
    for user_id in (SIGNING_UP, MAX, CHARLES):
        assert await driver_state(league, user_id) == "NOT_SIGNED_UP"
    assert await _stage(league) == "PENDING_COMPLETION"
    assert not league.errors, league.errors
    lines = list(league.bot.log_channel.sent) + await queued_log_lines(league.db_path)
    assert any("System | Every division is done | Pending placements turned down: 3" in line
               for line in lines), lines
    assert not any("Signups closed" in line for line in lines)


async def test_a_plain_ongoing_season_is_moved_on_with_no_driver_job(tmp_path):
    league = await pending_completion_league(tmp_path, stage="ONGOING")
    await _ask(league)
    await run_queue(league.bot)
    assert [name for name, _user in await _jobs(league)] == ["wind_down", "turn_down"]
    assert await _stage(league) == "PENDING_COMPLETION"
    assert league.revoked == {}


# ── A signup channel's notice, before its lock (F2) ─────────────────────────────────


async def test_a_driver_returned_by_the_wind_down_s_close_is_told_before_their_channel_is_locked(
    tmp_path,
):
    league = await _league(tmp_path, stage="ONGOING_SIGNUPS", signups_open=True)
    await signing_up(league, state="PENDING_SIGNUP_COMPLETION")
    await _ask(league)
    await run_queue(league.bot)
    jobs = await _jobs(league)
    assert jobs.index(("signup_notice", SIGNING_UP)) < jobs.index(("close_signup", SIGNING_UP))
    assert [(kind, uid) for kind, uid, _n in league.events if kind in ("notice", "lock")] == [
        ("notice", SIGNING_UP), ("lock", SIGNING_UP),
    ]
    assert league.notices == [(SIGNING_UP, "🔒 Signups have closed. This channel will be "
                                           "automatically deleted in 24 hours.")]
    assert await driver_state(league, SIGNING_UP) == "NOT_SIGNED_UP"


async def test_a_discarded_signup_notice_still_closes_the_channel(tmp_path):
    league = await _league(tmp_path, stage="ONGOING_SIGNUPS", signups_open=True)
    await signing_up(league, state="PENDING_SIGNUP_COMPLETION")
    league.hold_fails[SIGNING_UP] = http_error(discord.Forbidden, status=403,
                                               text="Missing Permissions")
    await _ask(league)
    await run_queue(league.bot)
    assert await _stopped_at(league) == ("signup_notice", SIGNING_UP)
    assert league.locked == []

    await discard_job(league.bot)
    assert league.locked == [SIGNING_UP]
    assert league.notices == []
    discard_lines = [line for line in league.bot.log_channel.sent if "Discard" in line]
    assert discard_lines and f"<@{SIGNING_UP}>" in discard_lines[-1]


def _close_recording(service: Any) -> str:
    """The name of the form through which the signup service records the window closed:
    `set_window_closed_on`, which `post_closed`'s record calls on its save's connection from
    C66 on, or until then the committing `set_window_closed`."""
    return ("set_window_closed_on" if hasattr(service, "set_window_closed_on")
            else "set_window_closed")


def _closes_then_fails_once(league: Any) -> None:
    """Recording the window closed goes through, then fails once, after the close has returned
    its drivers: as an audit that cannot be written would make it."""
    service = league.bot.signup_module_service
    name = _close_recording(service)
    closing = getattr(service, name)
    failing = {"now": True}

    async def _set_window_closed(*args: Any, **kwargs: Any) -> Any:
        closed = await closing(*args, **kwargs)
        if failing["now"]:
            failing["now"] = False
            raise RuntimeError("disk I/O error")
        return closed

    setattr(service, name, _set_window_closed)


async def test_a_window_close_that_stopped_after_returning_its_drivers_still_tells_and_closes_them(
    tmp_path,
):
    """The wind-down's close returns a driver still filling in the wizard; `post_closed`'s record
    then writes the window closed and fails, so its save is undone: the queue stops at
    `post_closed` with the window still open and the driver Not Signed Up, told nothing.
    Retried, the window is recorded closed, the driver's notice is posted before their
    channel's lock, and the season is moved on to Pending completion."""
    league = await _league(tmp_path, stage="ONGOING_SIGNUPS", signups_open=True)
    await signing_up(league, state="PENDING_SIGNUP_COMPLETION")
    _closes_then_fails_once(league)
    await _ask(league)

    await run_queue(league.bot)
    assert await _stopped_at(league) == ("post_closed", None)
    assert await driver_state(league, SIGNING_UP) == "NOT_SIGNED_UP"
    assert await window_open(league)
    assert league.notices == [] and league.locked == []

    await retry_job(league.bot)

    jobs = await _jobs(league)
    assert jobs.index(("signup_notice", SIGNING_UP)) < jobs.index(("close_signup", SIGNING_UP))
    assert [(kind, uid) for kind, uid, _n in league.events if kind in ("notice", "lock")] == [
        ("notice", SIGNING_UP), ("lock", SIGNING_UP),
    ]
    assert league.notices == [(SIGNING_UP, "🔒 Signups have closed. This channel will be "
                                           "automatically deleted in 24 hours.")]
    assert league.locked == [SIGNING_UP]
    assert not await window_open(league)
    assert await _stage(league) == "PENDING_COMPLETION"


class _Killed(BaseException):
    """The bot's process killed: no handler of the bot's sees it, nothing more is saved."""


def _killed_before_the_window_is_recorded_closed(league: Any) -> None:
    """The process is killed as the window's close comes to record the window closed: once,
    after the close has returned its drivers, their transitions committed, and before the job's
    mark or any result of its is saved. The bot started again closes as it should."""
    service = league.bot.signup_module_service
    name = _close_recording(service)
    closing = getattr(service, name)
    killing = {"now": True}

    async def _set_window_closed(*args: Any, **kwargs: Any) -> Any:
        if killing["now"]:
            killing["now"] = False
            raise _Killed()
        return await closing(*args, **kwargs)

    setattr(service, name, _set_window_closed)


async def test_a_window_close_cut_off_by_a_kill_after_returning_its_drivers_still_tells_and_closes_them_at_start(
    tmp_path,
):
    """The bot is killed after the wind-down's close has returned a driver still filling in the
    wizard to Not Signed Up, as `post_closed` comes to record the window closed and before its
    mark is saved. When the bot starts again, the driver it had returned is told, then their
    channel closed, and the season is moved on to Pending completion."""
    league = await _league(tmp_path, stage="ONGOING_SIGNUPS", signups_open=True)
    await signing_up(league, state="PENDING_SIGNUP_COMPLETION")
    _killed_before_the_window_is_recorded_closed(league)
    await _ask(league)

    with pytest.raises(_Killed):
        await run_queue(league.bot)
    [closed] = [row for row in await step_rows(league.db_path) if row["name"] == "post_closed"]
    assert closed["done_at"] is None and closed["result"] is None
    assert await driver_state(league, SIGNING_UP) == "NOT_SIGNED_UP"
    assert await window_open(league)

    await league.restart()
    await run_queue(league.bot)

    assert league.notices == [(SIGNING_UP, "🔒 Signups have closed. This channel will be "
                                           "automatically deleted in 24 hours.")]
    assert league.locked == [SIGNING_UP]
    jobs = await _jobs(league)
    assert jobs.index(("signup_notice", SIGNING_UP)) < jobs.index(("close_signup", SIGNING_UP))
    assert [(kind, uid) for kind, uid, _n in league.events if kind in ("notice", "lock")] == [
        ("notice", SIGNING_UP), ("lock", SIGNING_UP),
    ]
    assert not await window_open(league)
    assert await _stage(league) == "PENDING_COMPLETION"


@pytest.mark.parametrize(("signing", "failures"), [(False, 1), (True, 1), (True, 2)],
                         ids=["nobody signing up", "fails once", "fails again on the retry"])
async def test_a_window_close_whose_closing_record_fails_keeps_its_drivers_for_the_retry(
    tmp_path, signing, failures,
):
    """The wind-down's close returns driver 105, still filling in the wizard, then recording the
    window closed fails: the queue stops at `post_closed`, with the window still open, the season
    still in Ongoing, signups open, both pending placements standing, and 105 Not Signed Up, told
    nothing, and does so again where the first Retry fails the same way. Once the
    record goes through, 105 is told, then their channel closed, each once, and the season is
    moved on to Pending completion. With nobody filling in the wizard, the close returns nobody:
    the queue stops at `post_closed` all the same, and once retried, the window closes and the
    season moves on with nobody told and no channel closed."""
    league = await _league(tmp_path, stage="ONGOING_SIGNUPS", signups_open=True)
    if signing:
        await signing_up(league, state="PENDING_SIGNUP_COMPLETION")
    league.close_fails = RuntimeError("the window could not be recorded closed")
    await _ask(league)

    await run_queue(league.bot)
    for attempt in range(failures):
        if attempt:
            await retry_job(league.bot)
        assert await _stopped_at(league) == ("post_closed", None)
        assert await window_open(league)
        if signing:
            assert await driver_state(league, SIGNING_UP) == "NOT_SIGNED_UP"
        assert league.notices == [] and league.locked == []
        assert await _stage(league) == "ONGOING_SIGNUPS"
        assert await _pending(league) == 2

    league.close_fails = None
    await retry_job(league.bot)

    jobs = await _jobs(league)
    if signing:
        assert league.notices == [(SIGNING_UP, "🔒 Signups have closed. This channel will be "
                                               "automatically deleted in 24 hours.")]
        assert league.locked == [SIGNING_UP]
        assert jobs.count(("signup_notice", SIGNING_UP)) == 1
        assert jobs.count(("close_signup", SIGNING_UP)) == 1
        assert jobs.index(("signup_notice", SIGNING_UP)) < jobs.index(("close_signup", SIGNING_UP))
        assert [(kind, uid) for kind, uid, _n in league.events if kind in ("notice", "lock")] == [
            ("notice", SIGNING_UP), ("lock", SIGNING_UP),
        ]
    else:
        assert league.notices == [] and league.locked == []
        assert not [name for name, _uid in jobs if name in ("signup_notice", "close_signup")]
        [closed] = [row for row in await step_rows(league.db_path) if row["name"] == "post_closed"]
        assert json.loads(closed["payload"])["returned"] == 0
    assert not await window_open(league)
    assert await _stage(league) == "PENDING_COMPLETION"


# ── A signup's end, marked in the save that ends it (amendment K2) ───────────────────

SIGNUPS_CLOSED = "🔒 Signups have closed. This channel will be automatically deleted in 24 hours."
#: How a discarded closed notice is named: the window was not closed.
WINDOW_OPEN = (
    "The signup window could not be closed. Close it with `/signup close`. "
    "Where a close time is set, run `/signup close-time cancel` first, and where a season's end "
    "or wind-down is still on the queue, let it finish first, as `/signup close` is refused "
    "until then."
)
#: How a driver returned and left untold would be named: never for a discarded closed notice,
#: which drops only that post (b15-2).
RETURNED_NOT_TOLD = (
    f"<@{SIGNING_UP}> was returned to Not Signed Up when signups closed, but was not told and "
    "their channel was not closed: tell them and delete it by hand."
)


def _logged(text: str) -> str:
    """*text* as the log channel carries it: every mention in backticks, naming without
    notifying."""
    return re.sub(r"(<@&?\d+>)", r"`\1`", text)


async def _owed(league: Any) -> list[str]:
    """The accounts still owed their closing notice, as signup keeps them."""
    return await league.bot.signup_module_service.owed_closing_notices()


def _killed_after_the_window_is_recorded_closed(league: Any) -> None:
    """The process is killed straight after the window's close has recorded the window closed:
    once, before the close's audit is written and before the job's mark or any result of its is
    saved. The bot started again closes as it should."""
    service = league.bot.signup_module_service
    name = _close_recording(service)
    closing = getattr(service, name)
    killing = {"now": True}

    async def _set_window_closed(*args: Any, **kwargs: Any) -> Any:
        closed = await closing(*args, **kwargs)
        if killing["now"]:
            killing["now"] = False
            raise _Killed()
        return closed

    setattr(service, name, _set_window_closed)


async def test_a_window_close_cut_off_by_a_kill_after_the_window_is_recorded_closed_still_tells_and_closes_its_drivers_at_start(
    tmp_path,
):
    """The bot is killed in `post_closed`'s record straight after it writes the window closed,
    before its save commits: the window is still open. When the bot starts again, driver 105,
    whom the wind-down's close had returned, is told once, then their channel locked; the season
    is moved on to Pending completion, and 105 is owed nothing more."""
    league = await _league(tmp_path, stage="ONGOING_SIGNUPS", signups_open=True)
    await signing_up(league, state="PENDING_SIGNUP_COMPLETION")
    _killed_after_the_window_is_recorded_closed(league)
    await _ask(league)

    with pytest.raises(_Killed):
        await run_queue(league.bot)
    [closed] = [row for row in await step_rows(league.db_path) if row["name"] == "post_closed"]
    assert closed["done_at"] is None and closed["result"] is None
    assert await driver_state(league, SIGNING_UP) == "NOT_SIGNED_UP"
    assert await window_open(league)

    await league.restart()
    await run_queue(league.bot)

    assert league.notices == [(SIGNING_UP, SIGNUPS_CLOSED)]
    assert league.locked == [SIGNING_UP]
    assert [(kind, uid) for kind, uid, _n in league.events if kind in ("notice", "lock")] == [
        ("notice", SIGNING_UP), ("lock", SIGNING_UP),
    ]
    assert await _stage(league) == "PENDING_COMPLETION"
    assert await _owed(league) == []


async def test_a_discarded_closed_notice_still_tells_and_closes_the_drivers_the_wind_down_returned(
    tmp_path,
):
    """The signup window open with driver 105 still filling in the wizard. The wind-down's close
    returns 105, then `post_closed`, whose record writes the window closed, fails, and a league
    admin discards it. Only that post is dropped (b15-2): 105 is still told, then their channel
    locked, by their own jobs, and no line names them as returned but not told. One log line
    says the signup window could not be closed and to close it with `/signup close`, and none
    that signups closed, nor is the window's close audited; the window is still recorded open,
    the season moved on to Pending completion, and 105 is owed nothing more."""
    league = await _league(tmp_path, stage="ONGOING_SIGNUPS", signups_open=True)
    await signing_up(league, state="PENDING_SIGNUP_COMPLETION")
    league.close_fails = RuntimeError("the window could not be recorded closed")
    await _ask(league)
    await run_queue(league.bot)
    assert await _stopped_at(league) == ("post_closed", None)
    assert league.notices == [] and league.locked == []

    await discard_job(league.bot)

    assert league.notices == [(SIGNING_UP, SIGNUPS_CLOSED)]
    assert league.locked == [SIGNING_UP]
    assert [(kind, uid) for kind, uid, _n in league.events if kind in ("notice", "lock")] == [
        ("notice", SIGNING_UP), ("lock", SIGNING_UP),
    ]
    assert not any(_logged(RETURNED_NOT_TOLD) in line for line in league.bot.log_channel.sent)
    assert len([line for line in league.bot.log_channel.sent if WINDOW_OPEN in line]) == 1
    assert await league.rows(
        "SELECT * FROM audit_entries WHERE change_type = 'SIGNUP_DIVISIONS_DONE_CLOSE'"
    ) == []
    assert not any("🔒 Signups closed" in line
                   for line in league.bot.log_channel.sent + await queued_log_lines(league.db_path))
    assert await window_open(league)
    assert await _stage(league) == "PENDING_COMPLETION"
    assert await _owed(league) == []


async def test_the_turn_down_marks_a_turned_down_signup_over_in_its_save(tmp_path):
    """Driver 105 is correcting their signup (Pending Driver Correction, their wizard in a
    correction step) when the wind-down turns the season's pending placements down. Without
    the hooks the builder hands it, `turn_down` refuses to turn a signup down
    (`RuntimeError`) before it writes anything: what it wrote is committed, and the database,
    read afresh, holds no turn-down. With them, 105's wizard is unengaged in `turn_down`'s own
    save, before any job of theirs runs, and a restart then arms no inactivity job for them and
    expires nothing."""
    from leaguebot.core.db.database import get_connection
    from leaguebot.core.services.season_lifecycle_service import TURN_DOWN_STEP, wind_down_steps

    league = await _league(tmp_path)
    await signing_up(league, state="PENDING_DRIVER_CORRECTION")
    await league.write(
        "UPDATE signup_wizard_records SET wizard_state = 'COLLECTING_NATIONALITY' "
        "WHERE discord_user_id = ?",
        str(SIGNING_UP),
    )

    unhooked = wind_down_steps(None)[TURN_DOWN_STEP]
    async with get_connection(league.db_path) as db:
        with pytest.raises(RuntimeError, match="a wind-down is run with the hooks the builder"):
            try:
                await unhooked.run(db, MagicMock())
            finally:
                # Whatever the step wrote before it refused is saved, so that the reads below,
                # each on a connection of its own, would see it.
                await db.commit()
    assert await _pending(league) == 2
    assert await driver_state(league, SIGNING_UP) == "PENDING_DRIVER_CORRECTION"
    assert await wizard_state(league, SIGNING_UP) == "COLLECTING_NATIONALITY"

    await _ask(league)
    for _ in range(50):
        if any(row["name"] == "turn_down" and row["done_at"]
               for row in await step_rows(league.db_path)):
            break
        await run_queue(league.bot, steps=1)
    assert await driver_state(league, SIGNING_UP) == "NOT_SIGNED_UP"
    assert await wizard_state(league, SIGNING_UP) == "UNENGAGED"
    assert league.notices == []

    await run_queue(league.bot)
    assert await wizards_recovered(league) == {"armed": [], "expired": []}


def _channel_gone(league: Any, user_id: int = SIGNING_UP) -> None:
    """Driver *user_id*'s signup channel is gone from the server: deleted before their
    `signup_notice` ran, so there is nothing to tell or lock in it."""
    league.remove_channel(SIGNUP_CHANNEL_BASE + user_id)


def _locks_asked(league: Any, user_id: int = SIGNING_UP) -> list[Any]:
    """Each permission edit taking driver *user_id*'s typing away in their signup channel, the
    channel looked for on the server and among those removed from it."""
    cid = SIGNUP_CHANNEL_BASE + user_id
    held = league.channels.get(cid) or league._removed.get(cid)
    if held is None:
        return []
    return [call for call in held.set_permissions.call_args_list
            if call.kwargs.get("send_messages") is False]


def _deletion_armed_for(league: Any, user_id: int = SIGNING_UP) -> bool:
    """Whether a deletion of driver *user_id*'s signup channel was armed: through the scheduler
    service's `signup_channel_delete` timer, or the wizard service's own job on the scheduler."""
    scheduler = league.bot.scheduler_service
    return any(
        call.args[:2] == ("signup_channel_delete", str(user_id))
        for call in scheduler.arm_once.call_args_list
    ) or any(
        call.kwargs.get("id") == f"wizard_channel_delete_{user_id}"
        for call in scheduler._scheduler.add_job.call_args_list
    )


async def test_a_driver_with_no_signup_channel_left_is_named_in_a_line(tmp_path):
    """Driver 105, in review with no signup channel, is turned down by the wind-down. Their
    `signup_notice` finds no channel to tell, no lock is asked for them and no deletion armed,
    and a line of the job's own names them: "System | Every division is done | `<@105>` had no
    signup channel left to tell"."""
    league = await _league(tmp_path)
    await signing_up(league)
    await league.write(
        "UPDATE signup_wizard_records SET signup_channel_id = NULL WHERE discord_user_id = ?",
        str(SIGNING_UP),
    )
    _channel_gone(league)
    await _ask(league)

    await run_queue(league.bot)

    assert ("signup_notice", SIGNING_UP) in await _jobs(league)
    assert any(
        f"System | Every division is done | `<@{SIGNING_UP}>` had no signup channel left to tell"
        in line for line in league.bot.log_channel.sent
    )
    assert _locks_asked(league) == []
    assert SIGNING_UP not in league.locked
    assert not _deletion_armed_for(league)
    assert league.notices == []
    assert await _stage(league) == "PENDING_COMPLETION"
