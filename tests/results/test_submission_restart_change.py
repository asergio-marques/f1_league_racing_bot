"""A results submission a restart cut short, cleared and reopened on the change queue (#439,
slice 6b, C50).

When the bot starts, `_recover_orphaned_submission_channels` finds each round whose submission
channel stands open and is in no review: a first paste the restart cut short. It no longer clears
it itself. Before the queue starts it asks, as the bot, for `results.submission.restart` of the
round, on the league lane (a start-up step's work runs whole there, R2(a)): `clear` saves the
round's sessions and its submission row away, which ends the old conversation (R4), then
`delete_channel` deletes the old channel, the change's line tells the league to re-submit, and a
follow-on `results.submission.open` opens the round's submission afresh. That opening's channel
creation cannot be discarded, as at the round's start (core spec :130, decision 12). A refused
deletion of the old channel stops the league lane, the lane that asked (R5). A submission whose
turn is still in hand on the queue is left alone.

The league is `ongoing_league`'s, results on: Pro's round 3 awaits results, its submission open in
channel `SUBMISSION_CHANNEL` (690) with its first session accepted; the admin (77) holds the league
managers' role; the server creates each text channel asked of it as a channel double of the
league's. Everything of the change is reached through the restart and the queue, or imported
inside a test, so this file collects while it is unbuilt.
"""
from __future__ import annotations

import itertools
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from leaguebot.results.services import result_submission_service
from tests.support.change_queue import (
    MANAGER_ROLE_ID,
    acknowledgement,
    change_rows,
    discard_job,
    http_error,
    restart_queue,
    retry_job,
    run_queue,
    stopped_job,
    typed_message,
)
from tests.support.review_league import channel
from tests.support.season_league import (
    ADMIN_ID,
    PRO,
    SUBMISSION_CHANNEL,
    SeasonLeague,
    accept_session,
    ongoing_league,
    open_submission,
    round_id,
)

KIND = "results.submission.restart"
OPEN_KIND = "results.submission.open"
TURN_KIND = "results.submission.turn"
ROUND_3 = round_id(PRO, 3)
LANE = f"channel:{SUBMISSION_CHANNEL}"

NO_SUBMISSION_RESTART = (
    "#439: a restart clears a cut-off submission, deletes its channel and starts the submission "
    "job itself, off the change queue, rather than asking for `results.submission.restart`"
)

_new_ids = itertools.count(7700)


async def _league(tmp_path: Any) -> SeasonLeague:
    """The league of the module docstring. The ids of the channels the server creates are kept in
    `league.created`; set `league.create_fails` to an exception to refuse a creation."""
    league = await ongoing_league(tmp_path)
    league.roles_held[ADMIN_ID] = {MANAGER_ROLE_ID}
    await league.write("UPDATE rounds SET status = 'AWAITING_RESULTS' WHERE id = ?", ROUND_3)
    await open_submission(league, ROUND_3)
    await accept_session(league, ROUND_3)
    sub = league.channel(SUBMISSION_CHANNEL)
    if not isinstance(getattr(sub, "posted", None), list):
        sub.posted = []
    league.created = []
    league.create_fails = None
    league.bot.interaction_channel.category = MagicMock(name="the interaction channel's category")

    async def _create(name: str, **kwargs: Any) -> Any:
        if league.create_fails is not None:
            raise league.create_fails
        made = league._recording(channel(next(_new_ids), league.events))
        made.name = name
        made.category = kwargs.get("category")
        made.guild = league.guild
        league.channels[made.id] = made
        league.created.append(made.id)
        return made

    league.guild.create_text_channel = AsyncMock(side_effect=_create)
    return league


async def _restart(league: SeasonLeague, *, run: bool = True) -> None:
    """The bot restarts: a fresh queue on the same database, the start-up's recovery of the
    submission channels left open, and then, where *run*, the queue run. The submission job the
    results module has today, where it still has one, is stubbed, so that what it would open
    off the queue does not run behind the test."""
    import leaguebot.__main__ as bot_module

    await restart_queue(league.bot)
    stub = AsyncMock(name="run_result_submission_job")
    had = hasattr(result_submission_service, "run_result_submission_job")
    if had:
        real = result_submission_service.run_result_submission_job
        result_submission_service.run_result_submission_job = stub
    try:
        await bot_module._recover_orphaned_submission_channels(league.bot)
    finally:
        if had:
            result_submission_service.run_result_submission_job = real
    if run:
        await run_queue(league.bot)


async def _sessions(league: SeasonLeague) -> int:
    [row] = await league.rows(
        "SELECT COUNT(*) AS n FROM session_results WHERE round_id = ?", ROUND_3
    )
    return row["n"]


async def _submission(league: SeasonLeague) -> list[dict[str, Any]]:
    return await league.rows(
        "SELECT channel_id, closed FROM round_submission_channels WHERE round_id = ?", ROUND_3
    )


def _deleted(league: SeasonLeague, cid: int = SUBMISSION_CHANNEL) -> bool:
    return any(event[0] == "delete_channel" and event[1] == cid for event in league.events)


async def _kinds(league: SeasonLeague) -> list[str]:
    return [row["kind"] for row in await change_rows(league.db_path)
            if row["kind"] in (KIND, OPEN_KIND, TURN_KIND)]


async def test_a_first_paste_cut_short_is_cleared_and_reopened_as_jobs(tmp_path):
    """Round 3's submission was open in channel 690, one session accepted, when the bot stopped.
    At the restart the recovery asks the queue for the round's restart and does nothing itself:
    when it returns the accepted session, the submission row and channel 690 all still stand.
    The queue then clears the session and the row, deletes channel 690, tells the log channel to
    re-submit round 3 ("R3"), and opens round 3's submission afresh: a new channel is created and
    recorded open, its opening posted."""
    league = await _league(tmp_path)

    await _restart(league, run=False)

    assert await _kinds(league) == [KIND]
    assert await _sessions(league) == 1
    assert await _submission(league) == [{"channel_id": SUBMISSION_CHANNEL, "closed": 0}]
    assert not _deleted(league)

    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    assert await _sessions(league) == 0
    assert _deleted(league)
    [made] = league.created
    assert await _submission(league) == [{"channel_id": made, "closed": 0}]
    assert "Results submission open for **Round 3** (Pro)" in league.texts(made)[0]
    assert await _kinds(league) == [KIND, OPEN_KIND]
    assert "re-submit" in league.log() and "R3" in league.log()


@pytest.mark.xfail(strict=True, reason=NO_SUBMISSION_RESTART)
async def test_a_submission_whose_turn_is_in_hand_is_left_alone_at_a_restart(tmp_path):
    """The admin typed a paste in channel 690 and its turn was asked, but had not run, when the
    bot stopped. At the restart the recovery leaves round 3 alone: it asks no restart of it, and
    the accepted session, the submission row and channel 690 all stand."""
    from leaguebot.results.cogs.results_cog import ResultsCog

    league = await _league(tmp_path)
    message = typed_message(
        league.channel(SUBMISSION_CHANNEL), league._member(ADMIN_ID), "1, <@101>, FER",
        created_at=league.clock.now,
    )
    await ResultsCog(league.bot).on_message(message)
    assert await _kinds(league) == [TURN_KIND]

    await _restart(league, run=False)

    assert await _kinds(league) == [TURN_KIND]
    assert await _sessions(league) == 1
    assert await _submission(league) == [{"channel_id": SUBMISSION_CHANNEL, "closed": 0}]
    assert not _deleted(league)


async def test_a_refused_channel_deletion_at_a_restart_stops_the_queue(tmp_path):
    """Discord refuses to delete channel 690 (Missing Permissions). The queue stops at the
    restart's deletion, on the league lane, with round 3's sessions already cleared and no new
    channel created. Once Discord allows it, a Retry deletes channel 690 and round 3's submission
    is opened afresh."""
    league = await _league(tmp_path)
    league.channel(SUBMISSION_CHANNEL).delete_fails = http_error(
        discord.Forbidden, status=403, text="Missing Permissions"
    )

    await _restart(league)

    job = await stopped_job(league.db_path, "league")
    assert job is not None and job["name"] == "delete_channel"
    assert await _sessions(league) == 0
    assert league.created == []

    league.channel(SUBMISSION_CHANNEL).delete_fails = None
    await retry_job(league.bot)

    assert await stopped_job(league.db_path) is None
    assert _deleted(league)
    [made] = league.created
    assert await _submission(league) == [{"channel_id": made, "closed": 0}]


async def test_a_restart_s_reopening_of_a_submission_cannot_be_discarded(tmp_path):
    """Discord refuses to create round 3's new channel after the restart cleared the old one, so
    the queue stops at the reopening's channel creation. A league admin presses Discard: it is
    refused, saying round 3 in Pro would never have its results submitted, and the job still
    stops the queue. Once Discord allows it, a Retry creates and records the channel."""
    league = await _league(tmp_path)
    league.create_fails = http_error(discord.Forbidden, status=403, text="Missing Permissions")

    await _restart(league)
    stopped = await stopped_job(league.db_path)
    assert stopped is not None and stopped["name"] == "create_channel"

    refused = await discard_job(league.bot)

    assert "would never have its results submitted" in acknowledgement(refused)
    assert (await stopped_job(league.db_path))["id"] == stopped["id"]
    assert league.created == []

    league.create_fails = None
    await retry_job(league.bot)

    assert await stopped_job(league.db_path) is None
    [made] = league.created
    assert await _submission(league) == [{"channel_id": made, "closed": 0}]


async def test_a_restart_s_clearing_ends_the_old_conversation_and_its_refused_deletion_stops_the_league(
    tmp_path,
):
    """Discord refuses to delete channel 690 at the restart. The stop is the league lane's: no
    job of channel 690's own lane stops. The old submission is ended by the clearing, so a paste
    the admin types in channel 690 while it still stands is taken as no turn of round 3."""
    from leaguebot.results.cogs.results_cog import ResultsCog

    league = await _league(tmp_path)
    league.channel(SUBMISSION_CHANNEL).delete_fails = http_error(
        discord.Forbidden, status=403, text="Missing Permissions"
    )

    await _restart(league)

    assert await stopped_job(league.db_path, "league") is not None
    assert await stopped_job(league.db_path, LANE) is None
    assert await _submission(league) == []

    message = typed_message(
        league.channel(SUBMISSION_CHANNEL), league._member(ADMIN_ID), "1, <@101>, FER",
        created_at=league.clock.now,
    )
    await ResultsCog(league.bot).on_message(message)

    assert TURN_KIND not in await _kinds(league)
