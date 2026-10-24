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
league's.
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
    off the queue does not run behind the test. Where the queue is not to run, its worker is
    stopped before it ever runs: left running, it would carry out what the test means to find
    still in hand, as soon as the recovery waits on the database, on a slow host."""
    import leaguebot.__main__ as bot_module

    queue = await restart_queue(league.bot)
    if not run:
        await queue.stop()
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


async def test_a_round_whose_closing_is_stopped_is_not_taken_for_a_first_paste_at_a_restart(
    tmp_path,
):
    """Every session of round 3 was cancelled and the round's lane stopped on the deletion of its
    channel: `results.submission.close_channel` is in hand, the submission row still open. At the
    restart the recovery leaves round 3 alone, as one whose submission a change is closing: it
    asks no restart of it, which would delete the cancelled sessions and open the submission
    afresh."""
    from leaguebot.core.models.change import ChangeOrigin
    from leaguebot.results.services.submission_changes import CLOSE_KIND

    league = await _league(tmp_path)
    league.channel(SUBMISSION_CHANNEL).delete_fails = http_error(
        discord.Forbidden, status=403, text="Missing Permissions"
    )
    await league.bot.change_queue.ask(
        CLOSE_KIND, {"round_id": ROUND_3, "channel_id": SUBMISSION_CHANNEL},
        origin=ChangeOrigin.BOT, what="closing round 3's submission channel",
    )
    await run_queue(league.bot)
    job = await stopped_job(league.db_path)
    assert job is not None and job["name"] == "delete_channel"

    await _restart(league, run=False)

    assert [row["kind"] for row in await change_rows(league.db_path)] == [CLOSE_KIND]
    assert await _sessions(league) == 1
    assert await _submission(league) == [{"channel_id": SUBMISSION_CHANNEL, "closed": 0}]
    assert not _deleted(league)


async def test_a_round_whose_opening_is_stopped_is_not_taken_for_a_first_paste_at_a_restart(
    tmp_path,
):
    """Round 3's opening created its channel and recorded its row, then Discord refused the
    opening message and the league lane stopped there: `results.submission.open` is in hand, the
    submission row open. At the restart the recovery leaves round 3 alone: it asks no restart of
    it, which would delete the new channel and its row, say the sessions were lost and open the
    submission afresh behind the stopped opening."""
    from leaguebot.core.models.change import ChangeOrigin

    league = await _league(tmp_path)
    await league.write("DELETE FROM round_submission_channels WHERE round_id = ?", ROUND_3)
    making = league.guild.create_text_channel.side_effect

    async def refusing(name: str, **kwargs: Any) -> Any:
        made = await making(name, **kwargs)
        made.send_fails = http_error(status=403, text="Missing Access")
        return made

    league.guild.create_text_channel.side_effect = refusing
    await league.bot.change_queue.ask(
        OPEN_KIND, {"round_id": ROUND_3}, origin=ChangeOrigin.BOT,
        what="opening round 3's results submission",
    )
    await run_queue(league.bot)
    [made] = league.created
    assert (await stopped_job(league.db_path, "league"))["name"] == "post_opening"

    await _restart(league, run=False)

    assert await _kinds(league) == [OPEN_KIND]
    assert await _submission(league) == [{"channel_id": made, "closed": 0}]
    assert not _deleted(league, made)
    assert await _sessions(league) == 1


async def test_a_resubmission_whose_swap_is_in_hand_is_not_ended_at_a_restart(tmp_path):
    """Round 3 is being resubmitted, its review open, when the last resubmitted paste hands the
    league the swap and the league lane stops before it runs. At the restart the recovery leaves
    round 3 alone: it asks no review opening to end the resubmission, which would queue behind
    the swap, say the earlier results still stand and record the resubmission as lapsed after the
    swap had replaced them."""
    from leaguebot.core.models.change import ChangeOrigin
    from leaguebot.results.services.resubmission_changes import SWAP_KIND

    league = await _league(tmp_path)
    await league.write(
        "UPDATE round_submission_channels SET in_penalty_review = 1, resubmitting = 1 "
        "WHERE round_id = ?", ROUND_3,
    )
    await league.bot.change_queue.ask(
        SWAP_KIND,
        {
            "round_id": ROUND_3, "channel_id": SUBMISSION_CHANNEL, "division_id": 1,
            "season_id": 1, "round_number": 3, "division_name": "Pro",
            "cancel_message_id": None, "all_cancelled": False, "mark": None,
        },
        origin=ChangeOrigin.BOT, what="swapping round 3's resubmitted results in",
    )

    await _restart(league, run=False)

    assert [row["kind"] for row in await change_rows(league.db_path)] == [SWAP_KIND]
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


CANNOT_DISCARD_CLEAR = (
    "⛔ This job can't be discarded: without it round 3 in **Pro** would take no results until "
    "the bot next restarts. Fix what stopped it and press **Retry**."
)


async def test_the_restart_s_clearing_cannot_be_discarded(tmp_path):
    """At the restart the save clearing round 3's cut-short submission fails (the database refuses
    to delete its sessions), stopping the queue at `clear`. A league admin presses Discard: it is
    refused, saying round 3 in Pro would take no results until the bot next restarts; the queue
    stays stopped there, the accepted session and the submission row still stand and channel 690
    is not deleted. Once the fault is lifted, a Retry clears them, deletes channel 690 and opens
    round 3's submission afresh in a new channel."""
    league = await _league(tmp_path)
    await league.write(
        "CREATE TRIGGER refuse_clearing BEFORE DELETE ON session_results "
        "BEGIN SELECT RAISE(ABORT, 'the results are locked'); END"
    )

    await _restart(league)
    stopped = await stopped_job(league.db_path)
    assert stopped is not None and stopped["name"] == "clear"

    refused = await discard_job(league.bot)

    assert acknowledgement(refused) == CANNOT_DISCARD_CLEAR
    assert (await stopped_job(league.db_path))["id"] == stopped["id"]
    assert await _sessions(league) == 1
    assert await _submission(league) == [{"channel_id": SUBMISSION_CHANNEL, "closed": 0}]
    assert not _deleted(league)

    await league.write("DROP TRIGGER refuse_clearing")
    await retry_job(league.bot)

    assert await stopped_job(league.db_path) is None
    assert await _sessions(league) == 0
    assert _deleted(league)
    [made] = league.created
    assert await _submission(league) == [{"channel_id": made, "closed": 0}]


CUT_SHORT_LEFT = (
    f"⚠️ The cut-short submission channel <#{SUBMISSION_CHANNEL}> of round 3 was not deleted, and "
    "no record of it is kept: delete it by hand."
)
CUT_SHORT_UNNAMED = "#439: a discarded deletion at a restart does not yet name the channel left"


@pytest.mark.xfail(strict=True, reason=CUT_SHORT_UNNAMED)
async def test_a_discarded_deletion_at_a_restart_names_the_channel_to_delete_by_hand(tmp_path):
    """Discord refuses to delete channel 690 at the restart, after round 3's cut-short submission
    was cleared, so the queue stops at the deletion. A league admin discards it. The log channel
    is told, once, that the cut-short submission channel 690 of round 3 was not deleted and that
    no record of it is kept, to delete it by hand; round 3's submission is still opened afresh in a
    new channel."""
    league = await _league(tmp_path)
    league.channel(SUBMISSION_CHANNEL).delete_fails = http_error(
        discord.Forbidden, status=403, text="Missing Permissions"
    )

    await _restart(league)
    stopped = await stopped_job(league.db_path, "league")
    assert stopped is not None and stopped["name"] == "delete_channel"

    await discard_job(league.bot)

    assert await stopped_job(league.db_path) is None
    assert league.log().count(CUT_SHORT_LEFT) == 1
    assert not _deleted(league)
    [made] = league.created
    assert await _submission(league) == [{"channel_id": made, "closed": 0}]


ENDED_CANCELLED_UNRECOVERED = (
    "#439: a restart takes a round that ended cancelled for a first paste cut short, or for a "
    "review, and does not close its channel"
)


async def _ended_cancelled(league: SeasonLeague, *, in_review: bool = False) -> None:
    """Round 3's every session was entered as not held and the round ended cancelled, but the
    bot stopped before its submission channel 690 was closed: the round is CANCELLED, its one
    session cancelled, its submission row still open, and, where *in_review*, the row still
    flagged in penalty review, as a resubmission leaves it."""
    await league.write(
        "UPDATE session_results SET status = 'CANCELLED' WHERE round_id = ?", ROUND_3
    )
    await league.write("UPDATE rounds SET status = 'CANCELLED' WHERE id = ?", ROUND_3)
    if in_review:
        await league.write(
            "UPDATE round_submission_channels SET in_penalty_review = 1 WHERE round_id = ?",
            ROUND_3,
        )


async def _kinds_asked(league: SeasonLeague) -> list[str]:
    return [row["kind"] for row in await change_rows(league.db_path)]


@pytest.mark.xfail(strict=True, reason=ENDED_CANCELLED_UNRECOVERED)
async def test_a_restart_closes_the_channel_of_a_round_that_ended_cancelled_and_keeps_its_sessions(
    tmp_path,
):
    """Round 3 ended cancelled, every session entered as not held, and the bot stopped before its
    submission channel 690 was closed. At the restart the recovery asks the queue, as the bot,
    only to close round 3's submission channel: no restart that would clear the cancelled
    sessions and open the submission afresh. The queue then deletes channel 690, posting no
    closing line in it, and closes the round's submission row; the cancelled session is kept, no
    new channel is created, and the round stays cancelled."""
    from leaguebot.results.services.submission_changes import CLOSE_KIND

    league = await _league(tmp_path)
    await _ended_cancelled(league)
    before = list(league.texts(SUBMISSION_CHANNEL))

    await _restart(league)

    assert await _kinds_asked(league) == [CLOSE_KIND]
    assert await stopped_job(league.db_path) is None
    assert _deleted(league)
    assert league.texts(SUBMISSION_CHANNEL) == before
    assert await _submission(league) == [{"channel_id": SUBMISSION_CHANNEL, "closed": 1}]
    assert await _sessions(league) == 1
    assert league.created == []
    [row] = await league.rows("SELECT status FROM rounds WHERE id = ?", ROUND_3)
    assert row["status"] == "CANCELLED"


@pytest.mark.xfail(strict=True, reason=ENDED_CANCELLED_UNRECOVERED)
async def test_a_restart_closes_the_channel_of_a_round_that_ended_cancelled_even_when_its_review_flag_stands(
    tmp_path,
):
    """Round 3 ended cancelled after a resubmission that entered every session as not held, so its
    submission row still carries the penalty review's flag, and the bot stopped before channel
    690 was closed. At the restart the recovery asks only the closing of the channel: no penalty
    review is put back for a round that has ended. The queue deletes channel 690 and closes the
    row; the cancelled session is kept."""
    from leaguebot.results.services.submission_changes import CLOSE_KIND

    league = await _league(tmp_path)
    await _ended_cancelled(league, in_review=True)

    await _restart(league)

    assert await _kinds_asked(league) == [CLOSE_KIND]
    assert await stopped_job(league.db_path) is None
    assert _deleted(league)
    assert await _submission(league) == [{"channel_id": SUBMISSION_CHANNEL, "closed": 1}]
    assert await _sessions(league) == 1


@pytest.mark.xfail(strict=True, reason=ENDED_CANCELLED_UNRECOVERED)
async def test_a_restart_closes_the_channel_of_a_round_that_ended_cancelled_whose_channel_is_gone(
    tmp_path,
):
    """Round 3 ended cancelled and its submission channel 690 was then deleted by hand while the
    bot was stopped, its row still open. At the restart the recovery asks only the closing of the
    channel; the queue finds the channel gone, completes without stopping, and closes the row. The
    cancelled session is kept and no new channel is created."""
    from leaguebot.results.services.submission_changes import CLOSE_KIND

    league = await _league(tmp_path)
    await _ended_cancelled(league)
    del league.channels[SUBMISSION_CHANNEL]

    await _restart(league)

    assert await _kinds_asked(league) == [CLOSE_KIND]
    assert await stopped_job(league.db_path) is None
    assert await _submission(league) == [{"channel_id": SUBMISSION_CHANNEL, "closed": 1}]
    assert await _sessions(league) == 1
    assert league.created == []
