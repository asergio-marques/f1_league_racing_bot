"""Signup's own closing jobs: the closing notice in a driver's signup channel, the lock, and the
channel's deletion a day on, as the `signup.closing` change runs them on the driver's lane (#439,
slice 6b, C66).

Today the notice, the lock and the deletion's arming are `WizardService.trigger_channel_hold`,
called off the queue: a refused notice leaves the channel locked and kept for deletion by hand.
On the queue they are three jobs, `signup_notice`, `close_signup` and `arm_deletion`, which a
league change that ended a driver's signup (Withdraw, Approve, a Reject reason) asks back of the
driver's lane; a refused notice stops that lane alone, with Retry and Discard.

The league is `tests.support.season_league`'s `ongoing_league` with signups open: season 3
ongoing, Kimi (105) part-way through signing up with his signup channel (6405) standing. The
change is asked here as the bot, as the league change that hands it back would ask it.
"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock

import discord

from tests.support.change_queue import (
    change_rows,
    http_error,
    retry_job,
    run_queue,
    step_rows,
    stopped_job,
)
from tests.support.review_league import channel
from tests.support.season_league import (
    SIGNING_UP,
    SIGNUP_CHANNEL_BASE,
    SeasonLeague,
    ongoing_league,
    signing_up,
)

#: The lane change that closes a driver's signup in their channel.
CLOSING_KIND = "signup.closing"
#: The timer that deletes a held signup channel a day on.
TIMER = "signup_channel_delete"
#: A second driver part-way through signing up, with a channel of his own.
OTHER = 107
#: Each driver's signup channel and its lane.
KIMI_CHANNEL = SIGNUP_CHANNEL_BASE + SIGNING_UP
OTHER_CHANNEL = SIGNUP_CHANNEL_BASE + OTHER
KIMI_LANE = f"channel:{KIMI_CHANNEL}"
OTHER_LANE = f"channel:{OTHER_CHANNEL}"
#: The closing notice the league change hands over.
NOTICE = "Your signup has been withdrawn. This channel will be deleted in 24 hours."


async def _league(tmp_path: Any) -> SeasonLeague:
    """Season 3 ongoing with signups open; Kimi (105) and a second driver (107) part-way through
    signing up, each with their signup channel standing, Discord's permission edits recorded."""
    league = await ongoing_league(tmp_path, signups_open=True)
    for user_id in (SIGNING_UP, OTHER):
        await signing_up(league, user_id)
        cid = SIGNUP_CHANNEL_BASE + user_id
        held = league._recording(channel(cid, league.events))
        held.guild = league.guild
        held.set_permissions = AsyncMock()
        league.channels[cid] = held
    return league


async def _ask_closing(league: SeasonLeague, user_id: int = SIGNING_UP) -> int | None:
    from leaguebot.core.models.change import ChangeOrigin

    return await league.bot.change_queue.ask(
        CLOSING_KIND,
        {"user_id": str(user_id), "channel_id": SIGNUP_CHANNEL_BASE + user_id,
         "notice": NOTICE},
        what=f"closing the signup of <@{user_id}>",
        origin=ChangeOrigin.BOT,
    )


async def _closing(league: SeasonLeague, user_id: int = SIGNING_UP) -> dict[str, Any]:
    [change] = [row for row in await change_rows(league.db_path)
                if row["kind"] == CLOSING_KIND and _user_of(row) == str(user_id)]
    return change


def _user_of(change: dict[str, Any]) -> str | None:
    """The account a closing change's payload names."""
    payload = change.get("payload")
    data = json.loads(payload) if isinstance(payload, str) else (payload or {})
    return str(data["user_id"]) if data.get("user_id") is not None else None


def _lane(change: dict[str, Any]) -> str:
    return change.get("lane") or "league"


def _armed(league: SeasonLeague) -> list[tuple[Any, ...]]:
    return [call.args for call in league.bot.scheduler_service.arm_once.call_args_list
            if call.args and call.args[0] == TIMER]


def _locked(league: SeasonLeague, cid: int = KIMI_CHANNEL) -> bool:
    """Whether the driver's typing was revoked in *cid*."""
    return any(call.kwargs.get("send_messages") is False
               for call in league.channel(cid).set_permissions.call_args_list)


def _notices(league: SeasonLeague, cid: int = KIMI_CHANNEL) -> list[str]:
    return [text for text in league.channel(cid).texts if text == NOTICE]


async def test_a_closing_notice_refused_stops_the_queue(tmp_path):
    """Kimi's signup has ended and Discord refuses every post in his signup channel. The closing
    change is asked and the queue runs: it stops at `signup_notice`, with no notice posted, his
    typing not revoked and no deletion armed, so a Retry finds the channel as it was. Once the
    channel takes posts again, Retry posts the notice, locks the channel and arms its deletion."""
    league = await _league(tmp_path)
    league.channel(KIMI_CHANNEL).send_fails = http_error(
        discord.Forbidden, status=403, text="Missing Permissions"
    )

    await _ask_closing(league)
    await run_queue(league.bot)

    job = await stopped_job(league.db_path, KIMI_LANE)
    assert job is not None and job["name"] == "signup_notice"
    assert _notices(league) == []
    assert not _locked(league)
    assert _armed(league) == []

    league.channel(KIMI_CHANNEL).send_fails = None
    await retry_job(league.bot, lane=KIMI_LANE)

    assert await stopped_job(league.db_path, KIMI_LANE) is None
    assert len(_notices(league)) == 1
    assert _locked(league)
    assert len(_armed(league)) == 1


async def test_close_signup_locks_the_channel_and_asks_its_deletion_a_day_on(tmp_path):
    """Kimi's signup has ended. The closing change is asked and the queue runs: its jobs
    `signup_notice`, `close_signup` and `arm_deletion` are done in that order; the notice is
    posted in his signup channel, notifying neither everyone nor any role; his typing there is
    revoked; and the channel's deletion is armed, keyed to his account, a day after the job ran."""
    league = await _league(tmp_path)

    await _ask_closing(league)
    await run_queue(league.bot)

    change = await _closing(league)
    jobs = await step_rows(league.db_path, change["id"])
    assert [job["name"] for job in jobs] == ["signup_notice", "close_signup", "arm_deletion"]
    assert all(job["done_at"] for job in jobs)
    assert len(_notices(league)) == 1
    [send] = [call for call in league.channel(KIMI_CHANNEL).send.call_args_list
              if (call.args[0] if call.args else call.kwargs.get("content")) == NOTICE]
    mentions = send.kwargs.get("allowed_mentions")
    assert mentions is not None and mentions.everyone is False and mentions.roles is False
    assert _locked(league)
    assert _armed(league) == [(TIMER, str(SIGNING_UP), league.clock.now + timedelta(hours=24))]


async def test_a_closing_notice_asked_back_runs_on_the_driver_s_lane_and_a_refusal_stops_only_it(
    tmp_path,
):
    """Kimi's and another driver's signups have both ended; Discord refuses posts in Kimi's
    signup channel alone. Each closing change is asked and the queue runs: Kimi's runs on his
    channel's lane and stops there, while the league lane is not stopped and the other driver's
    closing, on his own channel's lane, is done, his notice posted and his channel locked."""
    league = await _league(tmp_path)
    league.channel(KIMI_CHANNEL).send_fails = http_error(
        discord.Forbidden, status=403, text="Missing Permissions"
    )

    await _ask_closing(league)
    await _ask_closing(league, OTHER)
    await run_queue(league.bot)

    kimi, other = await _closing(league), await _closing(league, OTHER)
    assert (_lane(kimi), _lane(other)) == (KIMI_LANE, OTHER_LANE)
    assert await stopped_job(league.db_path, KIMI_LANE) is not None
    assert await stopped_job(league.db_path, "league") is None
    assert await stopped_job(league.db_path, OTHER_LANE) is None
    assert other["state"] == "DONE"
    assert len(_notices(league, OTHER_CHANNEL)) == 1
    assert _locked(league, OTHER_CHANNEL)
