"""Opening a round's results submission on the change queue (#439, slice 6b, C45).

At a round's start its timer asks the queue for `results.submission.open`, as the bot. The change's
jobs move the round off NOT_RUN in a save of their own (`start_round`), create the round's
submission channel in the interaction channel's category and record it (`create_channel`, results
spec :244), post
the opening that pings the league managers' role (`post_opening`) and the first session's prompt
(`post_prompt`). The channel's creation cannot be discarded: without it the round would never have
its results submitted, so Discard on it is refused and only Retry clears it (core spec :130,
decision 12 of the slice). A round whose start falls while the queue is stopped stays not run, and
so cancellable, until the stop clears and the queue reaches it (Q17).

The league is `ongoing_league`'s, results on: Pro's round 3 is NOT_RUN, its results channel 606; the
league's interaction channel stands in a category of the server's; the server creates each text
channel asked of it as a channel double of the league's. The change is asked as its timer asks it.
"""
from __future__ import annotations

import inspect
import itertools
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from leaguebot.core.models.round import RoundFormat
from leaguebot.results.services.result_submission_service import get_sessions_for_format
from leaguebot.results.utils import results_formatter
from tests.support.change_queue import (
    acknowledgement,
    change_rows,
    discard_job,
    http_error,
    retry_job,
    run_queue,
    step_rows,
    stopped_job,
)
from tests.support.review_league import block_queue, channel
from tests.support.season_league import (
    DIVISIONS,
    PRO,
    SeasonLeague,
    ongoing_league,
    round_id,
)

KIND = "results.submission.open"
ROUND_3 = round_id(PRO, 3)
RESULTS = DIVISIONS[PRO][3].results

_new_ids = itertools.count(7600)


async def _league(tmp_path: Any, **options: Any) -> SeasonLeague:
    """The league of the module docstring, its server creating text channels. Gives the league;
    the ids of the channels created are kept in `league.created`."""
    league = await ongoing_league(tmp_path, **options)
    league.created = []
    league.bot.interaction_channel.category = MagicMock(name="the interaction channel's category")
    league.create_fails = None

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


async def _ask(league: SeasonLeague, round_: int = ROUND_3) -> None:
    """Ask the queue to open *round_*'s submission as its round-start timer does, as the bot; the
    queue is not run."""
    from leaguebot.core.models.change import ChangeOrigin

    await league.bot.change_queue.ask(
        KIND, {"round_id": round_}, what="opening round 3's results submission",
        origin=ChangeOrigin.BOT,
    )


async def _status(league: SeasonLeague, round_: int = ROUND_3) -> str:
    [row] = await league.rows("SELECT status FROM rounds WHERE id = ?", round_)
    return row["status"]


async def _submission(league: SeasonLeague, round_: int = ROUND_3) -> list[dict[str, Any]]:
    return await league.rows(
        "SELECT channel_id, closed FROM round_submission_channels WHERE round_id = ?", round_
    )


async def _first_session_label(league: SeasonLeague) -> str:
    [row] = await league.rows("SELECT format FROM rounds WHERE id = ?", ROUND_3)
    fmt = RoundFormat(row["format"])
    first = get_sessions_for_format(fmt)[0]
    return results_formatter.format_session_label(first, is_sprint=fmt is RoundFormat.SPRINT)


async def _open_changes(league: SeasonLeague) -> list[dict[str, Any]]:
    return [row for row in await change_rows(league.db_path) if row["kind"] == KIND]


async def test_the_round_start_asks_and_the_queue_opens_the_channel_with_its_opening_and_first_prompt(
    tmp_path,
):
    """Round 3's start asks the queue to open its submission: nothing is created as it is asked.
    The queue then creates one channel in the interaction channel's category (results spec :244),
    records it as round 3's open submission, and posts in it the opening ("Results submission open for
    **Round 3** (Pro)", naming its sessions, Qualifying and Race) and then the first session's
    prompt. The round-start timer's callback asks
    the queue rather than running the submission itself."""
    import leaguebot.__main__ as bot_module

    league = await _league(tmp_path)

    await _ask(league)
    assert league.created == []
    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    [made] = league.created
    assert league.channel(made).category is league.bot.interaction_channel.category
    assert await _submission(league) == [{"channel_id": made, "closed": 0}]
    opening, prompt = league.texts(made)
    assert "Results submission open for **Round 3** (Pro)" in opening
    assert "Qualifying" in opening and "Race" in opening
    assert f"📋 Submit **{await _first_session_label(league)}** results" in prompt
    assert [row["kind"] for row in await _open_changes(league)] == [KIND]
    assert "run_result_submission_job" not in inspect.getsource(bot_module.main)


async def test_the_round_moves_off_not_run_in_the_open_s_save(tmp_path):
    """Discord refuses to create round 3's channel (Missing Permissions). The queue stops at the
    channel's creation, and round 3 is already awaiting results: its move off NOT_RUN was saved
    with the mark of the job before it, `start_round`. No submission is recorded, and no penalty
    review is asked."""
    league = await _league(tmp_path)
    league.create_fails = http_error(discord.Forbidden, status=403, text="Missing Permissions")

    await _ask(league)
    await run_queue(league.bot)

    job = await stopped_job(league.db_path)
    assert job is not None and job["name"] == "create_channel"
    assert await _status(league) == "AWAITING_RESULTS"
    [change] = await _open_changes(league)
    [start] = [step for step in await step_rows(league.db_path, change["id"])
               if step["name"] == "start_round"]
    assert start["done_at"] is not None
    assert league.created == []
    assert await league.rows("SELECT round_id FROM round_submission_channels") == []
    assert [row for row in await change_rows(league.db_path)
            if row["kind"] == "results.review.open"] == []


async def test_a_round_starting_while_the_queue_is_stopped_opens_when_it_clears_and_is_cancellable_till_then(
    tmp_path,
):
    """The queue is stopped at another job when round 3's start asks for its submission. The
    round stays NOT_RUN, so a league manager may still cancel it, and no channel is created. Once
    the stopped job is retried and goes through, the queue opens round 3's submission: the round
    awaits results and its channel is created and recorded."""
    league = await _league(tmp_path)
    holder = await block_queue(league)

    await _ask(league)
    await run_queue(league.bot)

    assert await _status(league) == "NOT_RUN"
    assert league.created == []
    assert await _submission(league) == []

    holder["fail"] = False
    await retry_job(league.bot)

    assert await stopped_job(league.db_path) is None
    assert await _status(league) == "AWAITING_RESULTS"
    [made] = league.created
    assert await _submission(league) == [{"channel_id": made, "closed": 0}]


async def test_a_round_with_results_off_asks_the_wind_down(tmp_path):
    """The results module is off when round 3 starts. The queue makes round 3 FINAL, creates no
    channel, and asks for the season's wind-down, as the bot."""
    from leaguebot.core.services.season_lifecycle_service import WIND_DOWN

    league = await _league(tmp_path)
    await league.switch("results", False)

    await _ask(league)
    await run_queue(league.bot)

    assert await _status(league) == "FINAL"
    assert league.created == []
    assert await _submission(league) == []
    assert WIND_DOWN in [row["kind"] for row in await change_rows(league.db_path)]


async def test_a_cancelled_round_s_open_is_dropped(tmp_path):
    """Round 3 was cancelled before the queue reached its opening. The opening is dropped as no
    longer due: the queue does not stop, no channel is created, and round 3 stays CANCELLED."""
    league = await _league(tmp_path)
    await _ask(league)
    await league.write("UPDATE rounds SET status = 'CANCELLED' WHERE id = ?", ROUND_3)

    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    assert league.created == []
    assert await _submission(league) == []
    assert await _status(league) == "CANCELLED"


async def test_a_second_open_asked_while_the_first_is_stopped_is_dropped_once_the_channel_stands(
    tmp_path,
):
    """Test mode's advance asks round 3's opening twice: the first stops at the channel's
    creation (Discord refuses it), and the second is asked behind it. Once the refusal clears and
    the first is retried, the second finds the submission open and is dropped as no longer due:
    one channel is made, and the queue does not stop."""
    league = await _league(tmp_path)
    league.create_fails = http_error(discord.Forbidden, status=403, text="Missing Permissions")
    await _ask(league)
    await run_queue(league.bot)
    assert (await stopped_job(league.db_path))["name"] == "create_channel"
    await _ask(league)

    league.create_fails = None
    await retry_job(league.bot)
    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    [made] = league.created
    assert await _submission(league) == [{"channel_id": made, "closed": 0}]


async def test_an_open_with_no_results_channel_stops_the_queue_naming_the_command(tmp_path):
    """Pro has no results channel set when round 3 starts. The queue stops rather than passing
    over it, its stop notice in the log channel naming `/results channel results`, and no
    channel is created."""
    league = await _league(tmp_path)
    await league.write(
        "UPDATE division_results_config SET results_channel_id = NULL WHERE division_id = ?", PRO,
    )

    await _ask(league)
    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is not None
    assert "/results channel results" in league.log()
    assert league.created == []


async def test_the_opening_of_a_submission_cannot_be_discarded(tmp_path):
    """Discord refuses to create round 3's channel, so the queue stops at its creation. A league
    admin presses Discard: it is refused, privately, saying round 3 in Pro would never have its
    results submitted; the refusal is written to the log channel and the job still stops the
    queue. Once Discord allows it, a Retry creates and records the channel and posts the opening."""
    league = await _league(tmp_path)
    league.create_fails = http_error(discord.Forbidden, status=403, text="Missing Permissions")
    await _ask(league)
    await run_queue(league.bot)
    stopped = await stopped_job(league.db_path)
    assert stopped is not None and stopped["name"] == "create_channel"

    refused = await discard_job(league.bot)

    assert "would never have its results submitted" in acknowledgement(refused)
    assert "can't be discarded" in league.log()
    assert (await stopped_job(league.db_path))["id"] == stopped["id"]
    assert league.created == []

    league.create_fails = None
    await retry_job(league.bot)

    assert await stopped_job(league.db_path) is None
    [made] = league.created
    assert await _submission(league) == [{"channel_id": made, "closed": 0}]
    assert "Results submission open for **Round 3** (Pro)" in league.texts(made)[0]


CANNOT_DISCARD_START = (
    "⛔ This job can't be discarded: without it round 3 in **Pro** would never open for results, "
    "and its season could never be completed. Fix what stopped it and press **Retry**."
)


async def test_starting_the_round_cannot_be_discarded(tmp_path):
    """Round 3 starts, and the save that moves it off NOT_RUN fails, stopping the queue at
    `start_round`. A league admin presses Discard: it is refused, saying round 3 in Pro would never
    open for results and its season could never be completed; the queue stays stopped there and
    round 3 is still NOT_RUN, with no channel created. Once the fault is lifted, a Retry starts the
    round: it awaits results and its submission channel is created and recorded."""
    league = await _league(tmp_path)
    with patch(
        "leaguebot.results.services.submission_changes.set_round_status_on",
        side_effect=RuntimeError("the rounds table is locked"),
    ):
        await _ask(league)
        await run_queue(league.bot)
        stopped = await stopped_job(league.db_path)
        assert stopped is not None and stopped["name"] == "start_round"

        refused = await discard_job(league.bot)

        assert acknowledgement(refused) == CANNOT_DISCARD_START
        assert (await stopped_job(league.db_path))["id"] == stopped["id"]
        assert await _status(league) == "NOT_RUN"
        assert league.created == []

    await retry_job(league.bot)

    assert await stopped_job(league.db_path) is None
    assert await _status(league) == "AWAITING_RESULTS"
    [made] = league.created
    assert await _submission(league) == [{"channel_id": made, "closed": 0}]


async def test_a_round_cancelled_while_its_start_stood_stopped_is_dropped_on_retry(tmp_path):
    """Round 3 starts, and the save that moves it off NOT_RUN fails, stopping the queue at
    `start_round`. While it stands stopped a league manager cancels round 3. Once the fault is
    lifted, a Retry finds the round cancelled and drops the start as no longer due: the queue runs
    on, no channel is created and round 3 stays CANCELLED."""
    league = await _league(tmp_path)
    with patch(
        "leaguebot.results.services.submission_changes.set_round_status_on",
        side_effect=RuntimeError("the rounds table is locked"),
    ):
        await _ask(league)
        await run_queue(league.bot)
        stopped = await stopped_job(league.db_path)
        assert stopped is not None and stopped["name"] == "start_round"
    await league.write("UPDATE rounds SET status = 'CANCELLED' WHERE id = ?", ROUND_3)

    await retry_job(league.bot)

    assert await stopped_job(league.db_path) is None
    assert league.created == []
    assert await _submission(league) == []
    assert await _status(league) == "CANCELLED"
