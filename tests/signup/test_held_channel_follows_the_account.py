"""A held signup channel follows the driver to their new current account (issue #243).

After an approval or a rejection a driver's signup channel stays open, read-only, for 24 hours
before it is deleted. `/driver reassign` moves it — the wizard record, the channel's permission and
the deletion timer — to the new current account, the deletion kept at the moment it was already
due (E23). Where the new account holds a held channel of its own, the replaced account's is
deleted (E24). A deletion timer already gone still leaves the channel one (E42).

The channel is signup's, and core reaches it through the hook a driver's account changed declares
(#283): signup's part re-keys the wizard record on the connection of the reassign's own save, so
that it moves with the reassign or not at all, and hands back the jobs that follow it — the
deletion timer moved and the channel's permissions, or the replaced account's channel deleted.
While the signup module is off it does nothing, the hook being called whatever the switches say.

Each runs `/driver reassign` through the change queue on `season_league`'s season, in Placements,
Valtteri (106) Unassigned (`tests/core/test_driver_reassign.py`). The scheduler is the league's
double: what it was asked to arm and disarm is read from it.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock

import pytest

from tests.core.test_driver_reassign import (
    _NEW,
    _ask_reassign,
    _portraits_discarded,
    _queue_league,
    _reassigned,
)
from tests.support.season_league import SIGNUP_CHANNEL_BASE, UNASSIGNED, signup_channel

_THROUGH_THE_HOOK = (
    "#283: the held signup channel is not yet moved through the hook a driver's account changed"
)

DELETE_TIMER = "signup_channel_delete"
#: Valtteri's held channel, and the one 9001 holds of its own.
HELD, THEIRS = SIGNUP_CHANNEL_BASE + UNASSIGNED, SIGNUP_CHANNEL_BASE + _NEW


async def _held(league, user_id: int = UNASSIGNED, *, channel: bool = True) -> Any:
    """*user_id*'s signup has ended, its channel (`SIGNUP_CHANNEL_BASE` + their id) held, read-only,
    its wizard record naming it; gives the channel double, its permission edits and its deletion
    recorded. Without *channel* the record names none: a draft, a signup never begun."""
    cid = SIGNUP_CHANNEL_BASE + user_id
    await league.write(
        "INSERT INTO signup_wizard_records (discord_user_id, wizard_state, signup_channel_id) "
        "VALUES (?, 'UNENGAGED', ?)",
        str(user_id), cid if channel else None,
    )
    if not channel:
        return None
    held = signup_channel(league, user_id)
    held.set_permissions = AsyncMock()
    held.delete = AsyncMock()
    return held


def _due(league, due: dict[str, Any]) -> None:
    """The deletion timer of each account in *due* is due then; no other timer stands."""
    league.bot.scheduler_service.timer_due.side_effect = (
        lambda kind, key: due.get(str(key)) if kind == DELETE_TIMER else None
    )


def _armed(league) -> list[tuple[str, Any]]:
    """Each deletion timer the scheduler was asked to arm: the account, and when it is due."""
    return [
        (str(call.args[1]), call.args[2])
        for call in league.bot.scheduler_service.arm_once.call_args_list
        if call.args[0] == DELETE_TIMER
    ]


def _disarmed(league) -> list[str]:
    return [
        str(call.args[1])
        for call in league.bot.scheduler_service.disarm.call_args_list
        if call.args[0] == DELETE_TIMER
    ]


def _edits(held) -> list[tuple[int, dict[str, Any]]]:
    """Each permission edit of *held*: the member's id, and what it set."""
    return [(call.args[0].id, call.kwargs) for call in held.set_permissions.await_args_list]


async def _channel_of(league, user_id: int) -> int | None | str:
    """The channel *user_id*'s wizard record names, or "no record"."""
    rows = await league.rows(
        "SELECT signup_channel_id FROM signup_wizard_records WHERE discord_user_id = ?",
        str(user_id),
    )
    if not rows:
        return "no record"
    cid = rows[0]["signup_channel_id"]
    return None if cid is None else int(cid)


async def _reassign(tmp_path, monkeypatch, *, held=True, theirs=False, draft=False):
    """The league, Valtteri holding his held channel where *held*, 9001 one of its own where
    *theirs* or a draft where *draft*, no deletion timer standing; `/driver reassign` of Valtteri
    onto 9001 asked, not yet run. Gives the league, the interaction and the two channel
    doubles."""
    league = await _queue_league(tmp_path)
    _portraits_discarded(league, monkeypatch)
    valtteri = await _held(league) if held else None
    other = await _held(league, _NEW) if theirs else None
    if draft:
        await _held(league, _NEW, channel=False)
    _due(league, {})
    asked = await _ask_reassign(league, UNASSIGNED, _NEW)
    return league, asked, valtteri, other


@pytest.mark.xfail(strict=True, reason=_THROUGH_THE_HOOK)
async def test_a_held_channel_moves_with_its_deletion_still_due_when_it_was(
    tmp_path, monkeypatch,
):
    """E23. Valtteri (106) holds his held channel, its deletion due in six hours. The league
    manager runs `/driver reassign` of Valtteri onto 9001, which holds no channel. Until the queue
    runs nothing moves: the record names 106, no timer is armed or disarmed and no permission
    edited. Once it has, the record is 9001's, naming the channel, and 106 has none; one deletion
    timer is armed, naming 9001, due at the same moment, and 106's is disarmed; the channel is
    made read-only to 9001 (seen, not written) and 106's own permission on it removed; nothing
    else is deleted, and the reply is today's alone. Was `move_held_channel` called directly on
    the real scheduler, asserting the same."""
    from tests.support.change_queue import run_queue
    from tests.support.settings_league import last_said

    league, asked, held, _theirs = await _reassign(tmp_path, monkeypatch)
    soon = league.clock.now + timedelta(hours=6)
    _due(league, {str(UNASSIGNED): soon})

    assert await _channel_of(league, UNASSIGNED) == HELD
    assert _armed(league) == [] and _disarmed(league) == [] and _edits(held) == []
    await run_queue(league.bot)

    assert await _channel_of(league, _NEW) == HELD
    assert await _channel_of(league, UNASSIGNED) == "no record"
    assert _armed(league) == [(str(_NEW), soon)]
    assert _disarmed(league) == [str(UNASSIGNED)]
    [(to_new, set_new), (to_old, set_old)] = _edits(held)
    assert (to_new, set_new["view_channel"], set_new["send_messages"]) == (_NEW, True, False)
    assert (to_old, set_old["overwrite"]) == (UNASSIGNED, None)
    held.delete.assert_not_awaited()
    assert last_said(asked) == _reassigned("given a new account", _NEW, "<@106>")


async def test_a_held_channel_on_the_new_account_is_kept_and_the_other_deleted(
    tmp_path, monkeypatch,
):
    """E24. Valtteri (106) and 9001 each hold a held channel, each due for deletion in six hours.
    The league manager runs `/driver reassign` of Valtteri onto 9001. Nothing is deleted until the
    queue runs. Once it has, Valtteri's channel is deleted and its record with it; 9001 keeps its
    channel and its record, no timer is armed for it, and neither channel's permissions are
    edited. Was `move_held_channel` called directly, asserting that `signup.channel.delete` was
    asked of the queue and the old record stood until it ran; `test_wizard_changes.py`'s "driver
    reassign asks the held channel's deletion" ran that change to the channel's deletion, and is
    folded in here."""
    from tests.support.change_queue import run_queue

    league, _asked, held, theirs = await _reassign(tmp_path, monkeypatch, theirs=True)
    soon = league.clock.now + timedelta(hours=6)
    _due(league, {str(UNASSIGNED): soon, str(_NEW): soon})

    held.delete.assert_not_awaited()
    await run_queue(league.bot)

    assert held.delete.await_count == 1
    theirs.delete.assert_not_awaited()
    assert await _channel_of(league, UNASSIGNED) == "no record"
    assert await _channel_of(league, _NEW) == THEIRS
    assert [account for account, _due_at in _armed(league)] == []
    assert _edits(held) == [] and _edits(theirs) == []


@pytest.mark.xfail(strict=True, reason=_THROUGH_THE_HOOK)
async def test_a_deletion_job_already_gone_is_armed_again(tmp_path, monkeypatch):
    """E42: the channel is never left with nothing to delete it. Valtteri (106) holds his held
    channel, its deletion timer gone. After `/driver reassign` of Valtteri onto 9001 has run, one
    deletion timer names 9001, due 24 hours after the queue's clock. Was the same, called
    directly, due between 23 and 24 hours after the host's clock."""
    from tests.support.change_queue import run_queue

    league, _asked, _held_channel, _theirs = await _reassign(tmp_path, monkeypatch)
    await run_queue(league.bot)

    assert _armed(league) == [(str(_NEW), league.clock.now + timedelta(hours=24))]


async def test_an_account_holding_no_channel_moves_nothing(tmp_path, monkeypatch):
    """Neither Valtteri (106) nor 9001 holds a signup channel. After `/driver reassign` of Valtteri
    onto 9001 has run, neither has a wizard record, no deletion timer is armed or disarmed, and the
    reply is today's alone. Was the same, `move_held_channel` called directly."""
    from tests.support.change_queue import run_queue
    from tests.support.settings_league import last_said

    league, asked, _held_channel, _theirs = await _reassign(tmp_path, monkeypatch, held=False)
    await run_queue(league.bot)

    assert await _channel_of(league, UNASSIGNED) == "no record"
    assert await _channel_of(league, _NEW) == "no record"
    assert _armed(league) == [] and _disarmed(league) == []
    assert last_said(asked) == _reassigned("given a new account", _NEW, "<@106>")


async def test_a_draft_the_new_account_abandoned_goes_in_the_same_save_as_the_move(
    tmp_path, monkeypatch,
):
    """9001 holds a wizard record that never got a channel (a signup never begun); Valtteri (106)
    holds his held channel. After `/driver reassign` of Valtteri onto 9001 has run, the draft is
    replaced, not collided with: 9001's one record names Valtteri's channel, and 106 has none. Was
    the same, `move_held_channel` called directly."""
    from tests.support.change_queue import run_queue

    league, _asked, _held_channel, _theirs = await _reassign(tmp_path, monkeypatch, draft=True)
    await run_queue(league.bot)

    assert await _channel_of(league, _NEW) == HELD
    assert await _channel_of(league, UNASSIGNED) == "no record"


@pytest.mark.xfail(strict=True, reason=_THROUGH_THE_HOOK)
async def test_the_held_channel_s_record_moves_in_the_reassign_s_own_save(tmp_path, monkeypatch):
    """Takes the place of E43's crash order: the record is re-keyed in the reassign's save, so it
    moves with the reassign or not at all. Valtteri (106) holds his held channel. The league
    manager runs `/driver reassign` of Valtteri onto 9001, and the database fails as the queue
    writes its audit entry. Valtteri's record still names his channel, 9001 has none, no deletion
    timer is armed or disarmed and no permission edited. Was the order, the real scheduler's new
    timer armed, then the record re-keyed in a save of its own, then the old timer disarmed."""
    from tests.support.change_queue import run_queue
    from tests.support.settings_league import fault_in

    league, _asked, held, _theirs = await _reassign(tmp_path, monkeypatch)
    with fault_in("leaguebot.core.services.change_queue.record_change_on"):
        await run_queue(league.bot)

    assert await _channel_of(league, UNASSIGNED) == HELD
    assert await _channel_of(league, _NEW) == "no record"
    assert _armed(league) == [] and _disarmed(league) == [] and _edits(held) == []


@pytest.mark.xfail(strict=True, reason=_THROUGH_THE_HOOK)
async def test_a_held_channel_stays_while_the_signup_module_is_off(tmp_path, monkeypatch):
    """Signup's part does nothing while the module is off, the hook being called whatever the
    switches say. Valtteri (106) holds his held channel and the signup module is turned off. After
    `/driver reassign` of Valtteri onto 9001 has run, 9001 is Valtteri's current account, but his
    record still names his channel, no deletion timer is armed or disarmed and no permission
    edited."""
    from tests.support.change_queue import run_queue

    league = await _queue_league(tmp_path)
    _portraits_discarded(league, monkeypatch)
    held = await _held(league)
    _due(league, {})
    await league.switch("signup", False)
    await _ask_reassign(league, UNASSIGNED, _NEW)
    await run_queue(league.bot)

    [row] = await league.rows(
        "SELECT discord_user_id FROM driver_profiles WHERE discord_user_id = ?", str(_NEW)
    )
    assert row is not None
    assert await _channel_of(league, UNASSIGNED) == HELD
    assert _armed(league) == [] and _disarmed(league) == [] and _edits(held) == []


@pytest.mark.xfail(strict=True, reason=_THROUGH_THE_HOOK)
async def test_permissions_discord_refuses_are_reported_and_the_reassign_stands(
    tmp_path, monkeypatch,
):
    """Valtteri (106) holds his held channel, and Discord refuses to edit its permissions (403
    Missing Permissions). After `/driver reassign` of Valtteri onto 9001 has run, the reassign and
    the record's move stand and the deletion timer names 9001; the reply is today's, then "⚠️
    Done, but on Discord:" and a bullet, "the signup channel's permissions could not be moved:
    Discord refused or failed a request from the bot", and the success line carries a "not done:"
    line in the same words; the queue is not stopped. Until now the cog reported it with Discord's own
    error text after the colon."""
    import discord

    from tests.core.test_driver_move import _as_logged
    from tests.core.test_driver_reassign import _reassign_line, _reassign_lines
    from tests.support.change_queue import http_error, run_queue, stopped_job
    from tests.support.settings_league import last_said, log_lines

    league, asked, held, _theirs = await _reassign(tmp_path, monkeypatch)
    held.set_permissions.side_effect = http_error(
        discord.Forbidden, status=403, text="Missing Permissions"
    )
    await run_queue(league.bot)

    problem = (
        "the signup channel's permissions could not be moved: Discord refused or failed a "
        "request from the bot"
    )
    assert await _channel_of(league, _NEW) == HELD
    assert [account for account, _due_at in _armed(league)] == [str(_NEW)]
    assert last_said(asked) == (
        f"{_reassigned('given a new account', _NEW, '<@106>')}\n⚠️ Done, but on Discord:\n"
        f"• {problem}"
    )
    assert _reassign_lines(await log_lines(league)) == [
        f"{_reassign_line(UNASSIGNED, _NEW, f'106, {_NEW}')}\n  not done: {_as_logged(problem)}"
    ]
    assert await stopped_job(league.db_path) is None
