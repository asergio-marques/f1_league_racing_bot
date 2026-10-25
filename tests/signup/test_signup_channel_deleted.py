"""A driver's signup channel deleted while their signup is under way (#439, slice 8, G2).

Nothing sees a signup channel deleted by hand while its signup is under way, so a driver awaiting
approval stays so for good, blocking the season's placements (core :307), and a driver mid-signup
or correcting waits out their 24 hours. On the queue the signup cog's `on_guild_channel_delete`
asks, as the bot, the ending of the signup whose wizard names the channel (`signup.end`, reason
`CHANNEL_GONE`, found "deleted"), and the restart asks the same for every in-progress signup whose
channel it finds gone (found "restart"). The ending returns the driver to Not Signed Up in one
save that may not be discarded, posts nothing and writes one line in the log channel (signup
:112, decided 2026-10-24). A channel deleted after its signup ended changes nothing.

The league: `_league` from the wizard's tests, at Signups with the window open. Kimi (105) signs
up in his signup channel 6405.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock

import discord
import pytest

from tests.signup.test_wizard_changes import (
    DELETE_TIMER,
    END_KIND,
    INACTIVITY_TIMER,
    KIMI_CHANNEL,
    NEW_CHANNEL,
    START_KIND,
    _armed,
    _changes_of,
    _driver_state,
    _held_after_signup,
    _job_names,
    _league,
    _press_sign_up,
    _timer_callback,
    _wizard,
    _wizards,
)
from tests.support.change_queue import (
    acknowledgement,
    discard_job,
    http_error,
    refuse_the_job,
    retry_job,
    run_queue,
    stopped_job,
)
from tests.support.season_league import SIGNING_UP, SeasonLeague, signing_up

#: The correction's five-minute window, and the change its lapse asks.
CORRECTION_TIMER = "correction_lapse"
LAPSE_KIND = "signup.correction.lapse"
#: Kimi as the log channel names him.
KIMI_NAMED = f"Kimi (`<@{SIGNING_UP}>`)"
_HEAD = f"{KIMI_NAMED} | Signup | Ended\n  channel: <#{KIMI_CHANNEL}>\n  "
#: The line the ending writes where the bot saw the channel go, and where the restart found it.
DELETED_LINE = (
    _HEAD + "Their signup channel was deleted, so their signup ended. They may press Sign Up "
    "again while signups are open."
)
RESTART_LINE = (
    _HEAD + "Their signup channel was found deleted when the bot restarted, so their signup "
    "ended. They may press Sign Up again while signups are open."
)
#: The four states of a signup under way: the driver's, and the wizard's beside it.
IN_PROGRESS = [
    ("PENDING_SIGNUP_COMPLETION", "COLLECTING_NATIONALITY"),
    ("PENDING_ADMIN_APPROVAL", "UNENGAGED"),
    ("AWAITING_CORRECTION_PARAMETER", "UNENGAGED"),
    ("PENDING_DRIVER_CORRECTION", "COLLECTING_LAP_TIME"),
]
IN_PROGRESS_IDS = ["pending signup completion", "pending admin approval",
                   "awaiting correction parameter", "pending driver correction"]


async def _under_way(league: SeasonLeague, state: str, wizard_state: str, *,
                     idle_hours: int = 1) -> None:
    """Kimi's signup is under way at *state*, his wizard at *wizard_state*, last active
    *idle_hours* ago (by the host's clock, which the restart reads), his channel 6405 standing."""
    await signing_up(league, SIGNING_UP, state=state)
    since = datetime.now(timezone.utc) - timedelta(hours=idle_hours)
    await league.write(
        "UPDATE signup_wizard_records SET wizard_state = ?, last_activity_at = ? "
        "WHERE discord_user_id = ?",
        wizard_state, since.isoformat(), str(SIGNING_UP),
    )


async def _channel_deleted(league: SeasonLeague, cid: int = KIMI_CHANNEL, *,
                           guild: Any = None) -> Any:
    """Channel *cid* is deleted from the server, in *guild* where one is given (else the
    league's), and the signup cog's listener hears it; gives the channel."""
    from leaguebot.signup.cogs.signup_cog import SignupCog

    gone = league.channels[cid]
    league.remove_channel(cid)
    if guild is not None:
        gone.guild = guild
    cog = SignupCog.__new__(SignupCog)
    cog.bot = league.bot
    await cog.on_guild_channel_delete(gone)
    return gone


async def _endings(league: SeasonLeague) -> list[dict[str, Any]]:
    return await _changes_of(league, END_KIND)


def _disarmed(league: SeasonLeague) -> list[tuple[Any, ...]]:
    return [call.args[:2] for call in league.bot.scheduler_service.disarm.call_args_list]


# ---------------------------------------------------------------------------
# The bot sees the channel go
# ---------------------------------------------------------------------------


async def test_a_channel_deleted_while_its_signup_awaits_approval_ends_the_signup(tmp_path):
    """Kimi awaits approval, and his signup channel 6405 is deleted by hand. The bot hears it and
    the queue runs: one `signup.end`, asked as the bot on the league lane, runs its single job
    `end`. Kimi is Not Signed Up, his wizard ended (still naming 6405), nothing is sent to the
    gone channel, and the log channel is told once, naming him and the channel, that his signup
    channel was deleted and that he may press Sign Up again while signups are open."""
    league = await _league(tmp_path)
    await _under_way(league, "PENDING_ADMIN_APPROVAL", "UNENGAGED")

    gone = await _channel_deleted(league)
    await run_queue(league.bot)

    [end] = await _endings(league)
    assert (end["lane"] or "league", end["origin"], end["state"]) == ("league", "BOT", "DONE")
    assert await _job_names(league, END_KIND) == ["end"]
    assert await _driver_state(league) == "NOT_SIGNED_UP"
    wizard = await _wizard(league)
    assert wizard is not None
    assert (wizard["wizard_state"], wizard["signup_channel_id"]) == ("UNENGAGED", KIMI_CHANNEL)
    assert gone.texts == []
    assert league.log().count(DELETED_LINE) == 1


@pytest.mark.parametrize(
    ("state", "wizard_state"),
    [IN_PROGRESS[0], IN_PROGRESS[2], IN_PROGRESS[3]],
    ids=[IN_PROGRESS_IDS[0], IN_PROGRESS_IDS[2], IN_PROGRESS_IDS[3]],
)
async def test_a_channel_deleted_mid_signup_ends_it(tmp_path, state, wizard_state):
    """Kimi is part-way through signing up, or awaiting the field a manager asks him to correct,
    or correcting it, and his signup channel 6405 is deleted. Once the queue runs he is Not
    Signed Up, his wizard ended, his 24 hours, his channel's deletion and his correction's five
    minutes disarmed, no timer armed, and the deleted line written once."""
    league = await _league(tmp_path)
    await _under_way(league, state, wizard_state)

    await _channel_deleted(league)
    await run_queue(league.bot)

    assert await _driver_state(league) == "NOT_SIGNED_UP"
    assert (await _wizard(league))["wizard_state"] == "UNENGAGED"
    user = str(SIGNING_UP)
    assert {(INACTIVITY_TIMER, user), (DELETE_TIMER, user), (CORRECTION_TIMER, user)} <= set(
        _disarmed(league)
    )
    league.bot.scheduler_service.arm_once.assert_not_called()
    assert league.log().count(DELETED_LINE) == 1


@pytest.mark.parametrize(
    "state", ["NOT_SIGNED_UP", "UNASSIGNED"],
    ids=["withdrawn", "approved"],
)
async def test_a_channel_deleted_after_its_signup_ended_changes_nothing(tmp_path, state):
    """Kimi's signup has ended: he withdrew or was rejected (Not Signed Up) or was approved
    (Unassigned), his wizard ended but still naming his held channel 6405. The channel is deleted
    by hand and the bot hears it. Nothing is ended: no `signup.end` is taken up, he stays where he
    is, his wizard is untouched, and nothing is written to the log channel."""
    league = await _league(tmp_path)
    await _under_way(league, "PENDING_ADMIN_APPROVAL", "UNENGAGED")
    await league.write(
        "UPDATE driver_profiles SET current_state = ? WHERE discord_user_id = ?",
        state, str(SIGNING_UP),
    )
    wizard = await _wizard(league)

    await _channel_deleted(league)
    await run_queue(league.bot)

    assert await _endings(league) == []
    assert await _driver_state(league) == state
    assert await _wizard(league) == wizard
    assert "| Signup | Ended" not in league.log()


async def test_the_bot_deleting_a_held_channel_ends_nothing(tmp_path):
    """Kimi's signup ended a day ago and his channel 6405 is held. Its 24-hour timer fires and the
    bot deletes the channel; Discord then tells the bot the channel was deleted. Nothing is
    ended: no `signup.end` is taken up, Kimi stays Not Signed Up, no lane is stopped and nothing
    is written to the log channel."""
    league = await _league(tmp_path)
    held = await _held_after_signup(league)

    await _timer_callback(league, DELETE_TIMER)(str(SIGNING_UP))
    await run_queue(league.bot)
    held.delete.assert_awaited_once()
    league.channels[KIMI_CHANNEL] = held
    await _channel_deleted(league)
    await run_queue(league.bot)

    assert await _endings(league) == []
    assert await _driver_state(league) == "NOT_SIGNED_UP"
    assert await stopped_job(league.db_path) is None
    assert "| Signup | Ended" not in league.log()


async def test_a_channel_no_wizard_names_is_ignored(tmp_path):
    """Kimi awaits approval in channel 6405, and another channel of the server, 6999, which no
    wizard names, is deleted. Nothing is asked of the queue: no `signup.end`, Kimi still awaits
    approval and nothing is written to the log channel."""
    from tests.support.review_league import channel

    league = await _league(tmp_path)
    await _under_way(league, "PENDING_ADMIN_APPROVAL", "UNENGAGED")
    other = channel(6999, league.events)
    other.guild = league.guild
    league.channels[6999] = other

    await _channel_deleted(league, 6999)
    await run_queue(league.bot)

    assert await _endings(league) == []
    assert await _driver_state(league) == "PENDING_ADMIN_APPROVAL"
    assert "| Signup | Ended" not in league.log()


async def test_a_channel_deleted_in_another_server_is_ignored(tmp_path):
    """Kimi awaits approval in channel 6405, and a channel of the same id is deleted in another
    server the bot sits in. Nothing is asked of the queue: Kimi still awaits approval, his wizard
    is untouched and nothing is written to the log channel."""
    league = await _league(tmp_path)
    await _under_way(league, "PENDING_ADMIN_APPROVAL", "UNENGAGED")
    wizard = await _wizard(league)
    elsewhere = MagicMock(spec=discord.Guild)
    elsewhere.id = 99999

    await _channel_deleted(league, guild=elsewhere)
    await run_queue(league.bot)

    assert await _endings(league) == []
    assert await _driver_state(league) == "PENDING_ADMIN_APPROVAL"
    assert await _wizard(league) == wizard
    assert "| Signup | Ended" not in league.log()


async def test_the_old_channel_of_a_driver_who_signed_up_again_ends_nothing(tmp_path):
    """Kimi signed up again and awaits approval in his new channel 6500; his old channel 6405
    still stands, and is then deleted. Nothing is ended: Kimi still awaits approval, his wizard
    still names 6500, and nothing is written to the log channel."""
    league = await _league(tmp_path)
    await _under_way(league, "PENDING_ADMIN_APPROVAL", "UNENGAGED")
    await league.write(
        "UPDATE signup_wizard_records SET signup_channel_id = ? WHERE discord_user_id = ?",
        NEW_CHANNEL, str(SIGNING_UP),
    )

    await _channel_deleted(league)
    await run_queue(league.bot)

    assert await _endings(league) == []
    assert await _driver_state(league) == "PENDING_ADMIN_APPROVAL"
    assert (await _wizard(league))["signup_channel_id"] == NEW_CHANNEL
    assert "| Signup | Ended" not in league.log()


async def test_ending_a_deleted_channel_s_signup_cannot_be_discarded(tmp_path):
    """Kimi awaits approval and his signup channel 6405 is deleted; the ending's save fails,
    stopping the league lane at `end`. A league admin's Discard on it is refused, saying Kimi's
    signup would stay under way with no signup channel, so he could neither finish it nor sign
    up again, and while he awaits review or a correction the season's placements could not be
    confirmed; the queue stays stopped and he still awaits approval. Once the fault is lifted, a
    Retry ends his signup and writes the deleted line once."""
    league = await _league(tmp_path)
    await _under_way(league, "PENDING_ADMIN_APPROVAL", "UNENGAGED")
    await refuse_the_job(league.db_path, END_KIND, "end", True)

    await _channel_deleted(league)
    await run_queue(league.bot)
    stopped = await stopped_job(league.db_path, "league")
    assert stopped is not None and stopped["name"] == "end"

    refused = await discard_job(league.bot, lane="league")

    assert acknowledgement(refused) == (
        f"⛔ This job can't be discarded: without it <@{SIGNING_UP}>'s signup would stay under "
        "way with no signup channel, so they could neither finish it nor sign up again, and "
        "while they await review or a correction the season's placements could not be "
        "confirmed. Fix what stopped it and press **Retry**."
    )
    assert (await stopped_job(league.db_path, "league"))["id"] == stopped["id"]
    assert await _driver_state(league) == "PENDING_ADMIN_APPROVAL"

    await refuse_the_job(league.db_path, END_KIND, "end", False)
    await retry_job(league.bot, lane="league")

    assert await stopped_job(league.db_path) is None
    assert await _driver_state(league) == "NOT_SIGNED_UP"
    assert league.log().count(DELETED_LINE) == 1


async def test_a_driver_whose_channel_was_deleted_may_sign_up_again(tmp_path):
    """Kimi's signup ended because his signup channel 6405 was deleted. He presses Sign Up and the
    queue runs: a new channel 6500 is created, he is part-way through signing up in it, his
    wizard names it, and the league lane is not stopped."""
    league = await _league(tmp_path)
    await _under_way(league, "PENDING_ADMIN_APPROVAL", "UNENGAGED")
    await _channel_deleted(league)
    await run_queue(league.bot)
    assert await _driver_state(league) == "NOT_SIGNED_UP"

    await _press_sign_up(league)
    await run_queue(league.bot)

    assert league.created == [NEW_CHANNEL]
    [start] = await _changes_of(league, START_KIND)
    assert start["state"] == "DONE"
    assert await stopped_job(league.db_path, "league") is None
    assert await _driver_state(league) == "PENDING_SIGNUP_COMPLETION"
    assert (await _wizard(league))["signup_channel_id"] == NEW_CHANNEL


# ---------------------------------------------------------------------------
# The restart finds the channel gone
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("state", "wizard_state"), IN_PROGRESS, ids=IN_PROGRESS_IDS)
async def test_a_restart_ends_a_signup_whose_channel_was_deleted_while_the_bot_was_down(
    tmp_path, state, wizard_state,
):
    """Kimi's signup is under way, in any of its four states, last active 25 hours ago, and his
    signup channel 6405 was deleted while the bot was down: neither the cache nor Discord's lookup
    finds it. The bot starts again and recovers the wizards, then the queue runs. One `signup.end`
    for the gone channel is taken up and no other: no expiry is asked, no correction lapse is
    asked and no 24-hour timer is armed. Kimi is Not Signed Up, and the log channel is told once
    that his signup channel was found deleted when the bot restarted."""
    league = await _league(tmp_path)
    await _under_way(league, state, wizard_state, idle_hours=25)
    league.remove_channel(KIMI_CHANNEL)

    await _wizards(league).recover_wizards()
    await run_queue(league.bot)

    [end] = await _endings(league)
    assert '"channel_gone"' in str(end["payload"]).replace("'", '"')
    assert await _changes_of(league, LAPSE_KIND) == []
    assert _armed(league, INACTIVITY_TIMER) == []
    assert await _driver_state(league) == "NOT_SIGNED_UP"
    assert league.log().count(RESTART_LINE) == 1


async def test_a_restart_with_no_league_server_ends_nothing(tmp_path, caplog):
    """Kimi awaits approval and his signup channel 6405 is gone, but when the bot starts again the
    league's server is missing from its cache. Recovering the wizards ends nothing: no `signup.end`
    is asked, Kimi still awaits approval, and a warning is logged on the host."""
    league = await _league(tmp_path)
    await _under_way(league, "PENDING_ADMIN_APPROVAL", "UNENGAGED")
    league.remove_channel(KIMI_CHANNEL)
    league.bot.get_guild.return_value = None

    with caplog.at_level(logging.WARNING, logger="leaguebot.signup"):
        await _wizards(league).recover_wizards()
    await run_queue(league.bot)

    assert await _endings(league) == []
    assert await _driver_state(league) == "PENDING_ADMIN_APPROVAL"
    assert any(record.levelno >= logging.WARNING and record.name.startswith("leaguebot.signup")
               for record in caplog.records)


@pytest.mark.parametrize(
    "refusal",
    [http_error(), http_error(discord.Forbidden, status=403, text="Missing Access")],
    ids=["server error", "forbidden"],
)
async def test_a_restart_that_cannot_confirm_a_channel_gone_ends_nothing(
    tmp_path, caplog, refusal,
):
    """Kimi awaits approval, and when the bot starts again his signup channel 6405 is missing from
    its cache and Discord's lookup of it fails with a server error, or is forbidden. The restart
    asked Discord for the channel, treats it as standing and ends nothing: no `signup.end` is
    asked, no lane is stopped, Kimi still awaits approval, and the failure is logged on the
    host."""
    league = await _league(tmp_path)
    await _under_way(league, "PENDING_ADMIN_APPROVAL", "UNENGAGED")
    cached = league.guild.get_channel.side_effect
    league.guild.get_channel.side_effect = (
        lambda cid: None if int(cid) == KIMI_CHANNEL else cached(cid)
    )
    league.guild.fetch_channel.side_effect = refusal

    with caplog.at_level(logging.WARNING, logger="leaguebot.signup"):
        await _wizards(league).recover_wizards()
    await run_queue(league.bot)

    league.guild.fetch_channel.assert_any_await(KIMI_CHANNEL)
    assert await _endings(league) == []
    assert await stopped_job(league.db_path) is None
    assert await _driver_state(league) == "PENDING_ADMIN_APPROVAL"
    assert any(record.levelno >= logging.WARNING and record.name.startswith("leaguebot.signup")
               for record in caplog.records)


async def test_a_channel_missing_from_the_cache_but_found_at_the_restart_is_left_alone(tmp_path):
    """Kimi awaits approval, and when the bot starts again his signup channel 6405 is missing from
    its cache but Discord's lookup finds it standing. Recovering the wizards ends nothing: no
    `signup.end` is asked, Kimi still awaits approval and nothing is written to the log
    channel."""
    league = await _league(tmp_path)
    await _under_way(league, "PENDING_ADMIN_APPROVAL", "UNENGAGED")
    cached = league.guild.get_channel.side_effect
    league.guild.get_channel.side_effect = (
        lambda cid: None if int(cid) == KIMI_CHANNEL else cached(cid)
    )

    await _wizards(league).recover_wizards()
    await run_queue(league.bot)

    assert await _endings(league) == []
    assert await _driver_state(league) == "PENDING_ADMIN_APPROVAL"
    assert "| Signup | Ended" not in league.log()
