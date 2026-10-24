"""A message typed in a round's submission channel while its penalty review is open, deleted and
warned on the round's lane (#439, slice 6b, C57).

Results' listener (`ResultsCog.on_message`) asks `results.review.lock` as the bot's, on the round's
lane: a job deletes the member's message, a job posts the "locked during penalty review" warning
(mentioning the member alone, with no `delete_after`) and saves the warning's row
(`review_lock_warnings`), and a job of its own after that save arms the eight-second timer
(`arm_once("review_lock_clear", <warning id>, now + 8 s)`). When the timer fires, the callback the
builder registered reads the row again and asks `results.review.lock.clear`, also on the round's
lane (a conversation's own timer, R2(d)), whose job deletes the warning and the row; with the row
gone it asks nothing. While the round's lane is stopped, typed messages stand until it clears
(Q20).

The league is `review_league`'s: round 3 of Pro in its report stage in submission channel 702, its
scheduler service a double recording what is armed and registered. The timer's callback is found
where the builder registers it, on `bot.scheduler_service.register_timer`.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord

from leaguebot.core.db.database import get_connection
from tests.support.change_queue import (
    http_error,
    retry_job,
    run_queue,
    stopped_job,
)
from tests.support.review_league import (
    NOW,
    SUBMISSION_CHANNEL,
    ReviewLeague,
    block_queue,
    changes_of,
    review_league,
)

KIND = "results.review.lock"
CLEAR_KIND = "results.review.lock.clear"
TIMER = "review_lock_clear"
LANE = f"channel:{SUBMISSION_CHANNEL}"
TYPED = 8970
ALEX = 4242
WARNING = "locked during penalty review"



def _typed(league: ReviewLeague, content: str = "can I add a penalty?") -> Any:
    """Alex (4242), a member of the league, types *content* in round 3's submission channel;
    the message stands there as 8970."""
    channel = league.channel(SUBMISSION_CHANNEL)
    channel.seed(TYPED, content)
    author = MagicMock(spec=discord.Member)
    author.id = ALEX
    author.bot = False
    author.display_name = "Alex"
    author.mention = f"<@{ALEX}>"
    message = MagicMock(spec=discord.Message)
    message.id = TYPED
    message.author = author
    message.content = content
    message.channel = channel
    message.guild = league.guild
    message.created_at = NOW
    message.attachments = []

    async def _delete(*_args: Any, **_kwargs: Any) -> None:
        await channel.messages[TYPED].delete()

    message.delete = AsyncMock(side_effect=_delete)
    message.add_reaction = AsyncMock()
    return message


async def _listen(league: ReviewLeague, message: Any) -> None:
    from leaguebot.results.cogs.results_cog import ResultsCog

    cog = ResultsCog.__new__(ResultsCog)
    cog.bot = league.bot
    await cog.on_message(message)


def _warnings(league: ReviewLeague) -> list[int]:
    return [mid for mid, message in league.channel(SUBMISSION_CHANNEL).messages.items()
            if WARNING in (message.content or "")]


async def _warning_rows(db_path: str) -> list[dict[str, Any]]:
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT * FROM review_lock_warnings")
        return [dict(row) for row in await cursor.fetchall()]


def _armed(league: ReviewLeague) -> list[tuple[Any, ...]]:
    return [call.args for call in league.bot.scheduler_service.arm_once.call_args_list
            if call.args and call.args[0] == TIMER]


def _timer_callback(league: ReviewLeague) -> Any:
    """The callback the builder registered for the lock warning's timer."""
    [callback] = [call.args[1] for call in league.bot.scheduler_service.register_timer.call_args_list
                  if call.args and call.args[0] == TIMER]
    return callback


def _lane(change: dict[str, Any]) -> str:
    return change.get("lane") or "league"


async def test_a_message_in_a_locked_channel_is_deleted_and_warned_as_jobs(tmp_path):
    """Alex types in round 3's submission channel while its penalty review is open. Nothing is
    deleted or posted at once; a lock change is asked on round 3's lane. When it runs, his
    message is deleted and the warning posted, mentioning him alone and not set to delete itself,
    and the warning's row is saved."""
    league = await review_league(tmp_path)
    message = _typed(league)

    await _listen(league, message)

    assert TYPED in league.channel(SUBMISSION_CHANNEL).messages
    assert _warnings(league) == []
    [change] = await changes_of(league.db_path, KIND)
    assert _lane(change) == LANE
    assert change["origin"] == "BOT"

    await run_queue(league.bot)

    assert TYPED not in league.channel(SUBMISSION_CHANNEL).messages
    [warning] = _warnings(league)
    [sent] = [call for call in league.channel(SUBMISSION_CHANNEL).send.await_args_list
              if WARNING in (call.args[0] if call.args else call.kwargs.get("content", ""))]
    assert "delete_after" not in sent.kwargs
    mentions = sent.kwargs["allowed_mentions"]
    assert [user.id for user in mentions.users] == [ALEX]
    assert mentions.roles is False and mentions.everyone is False
    [row] = await _warning_rows(league.db_path)
    assert row["message_id"] == warning
    assert row["channel_id"] == SUBMISSION_CHANNEL


async def test_the_warning_s_clear_is_armed_by_a_job_of_its_own_after_its_row_is_saved(tmp_path):
    """Alex's message in the locked channel is deleted and warned. The warning's eight seconds
    are armed by a job of their own, after the job that saved the warning's row: a timer for
    that warning, eight seconds from the queue's now."""
    league = await review_league(tmp_path)

    await _listen(league, _typed(league))
    await run_queue(league.bot)

    [warning] = _warnings(league)
    assert _armed(league) == [(TIMER, warning, NOW + timedelta(seconds=8))]
    [change] = await changes_of(league.db_path, KIND)
    from tests.support.change_queue import step_rows

    names = [row["name"] for row in await step_rows(league.db_path)
             if row["change_id"] == change["id"]]
    assert names.index("arm_clear") > names.index("post_warning")


async def test_the_warning_is_deleted_by_a_job_its_timer_asks_eight_seconds_on(tmp_path):
    """Alex's message in the locked channel has been deleted and warned. When the warning's
    timer fires, its callback asks a clear change; when that runs, the warning is deleted from
    the channel and its row with it."""
    league = await review_league(tmp_path)
    await _listen(league, _typed(league))
    await run_queue(league.bot)
    [warning] = _warnings(league)

    await _timer_callback(league)(warning)

    assert warning in league.channel(SUBMISSION_CHANNEL).messages
    [clear] = await changes_of(league.db_path, CLEAR_KIND)
    assert clear["origin"] == "BOT"

    await run_queue(league.bot)

    assert warning not in league.channel(SUBMISSION_CHANNEL).messages
    assert await _warning_rows(league.db_path) == []


async def test_the_warning_s_clear_runs_on_the_round_s_lane(tmp_path):
    """The league lane is stopped at a job of its own when the warning's timer fires: the clear
    is asked on round 3's lane, and the warning is deleted while the league stays stopped."""
    league = await review_league(tmp_path)
    await _listen(league, _typed(league))
    await run_queue(league.bot)
    [warning] = _warnings(league)
    await block_queue(league)

    await _timer_callback(league)(warning)
    await run_queue(league.bot)

    [clear] = await changes_of(league.db_path, CLEAR_KIND)
    assert _lane(clear) == LANE
    assert clear["state"] == "DONE"
    assert warning not in league.channel(SUBMISSION_CHANNEL).messages
    assert await stopped_job(league.db_path, "league") is not None


async def test_a_warning_clear_whose_row_is_gone_asks_nothing(tmp_path):
    """The warning's row is gone by the time its timer fires (deleted by hand, or by a clear
    already run): the callback asks no clear change, and nothing is deleted."""
    league = await review_league(tmp_path)
    await _listen(league, _typed(league))
    await run_queue(league.bot)
    [warning] = _warnings(league)
    async with get_connection(league.db_path) as db:
        await db.execute("DELETE FROM review_lock_warnings")
        await db.commit()

    await _timer_callback(league)(warning)
    await run_queue(league.bot)

    assert await changes_of(league.db_path, CLEAR_KIND) == []
    assert warning in league.channel(SUBMISSION_CHANNEL).messages


async def test_a_refused_deletion_stops_the_queue(tmp_path):
    """Discord refuses to delete Alex's message in the locked channel. Round 3's lane stops at
    that deletion, with no warning posted, and the league lane runs on; Retry deletes the message
    and posts the warning."""
    league = await review_league(tmp_path)
    message = _typed(league)
    standing = league.channel(SUBMISSION_CHANNEL).messages[TYPED]
    working = standing.delete
    standing.delete = AsyncMock(side_effect=http_error(status=403, text="Missing Access"))

    await _listen(league, message)
    await run_queue(league.bot)

    stopped = await stopped_job(league.db_path, LANE)
    assert stopped is not None and stopped["name"] == "delete_message"
    assert await stopped_job(league.db_path, "league") is None
    assert _warnings(league) == []

    standing.delete = working
    await retry_job(league.bot, lane=LANE)

    assert await stopped_job(league.db_path) is None
    assert TYPED not in league.channel(SUBMISSION_CHANNEL).messages
    assert len(_warnings(league)) == 1


async def test_messages_typed_while_the_queue_is_stopped_stand_until_it_clears(tmp_path):
    """Round 3's lane is stopped at another job when Alex types in the locked channel. His
    message stands and no warning is posted; once the stop clears, it is deleted and warned."""
    league = await review_league(tmp_path)
    holder = await block_queue(league, lane=LANE)

    await _listen(league, _typed(league))
    await run_queue(league.bot)

    assert TYPED in league.channel(SUBMISSION_CHANNEL).messages
    assert _warnings(league) == []

    holder["fail"] = False
    await retry_job(league.bot, lane=LANE)

    assert await stopped_job(league.db_path) is None
    assert TYPED not in league.channel(SUBMISSION_CHANNEL).messages
    assert len(_warnings(league)) == 1


async def test_a_warning_stopped_in_a_channel_the_appeals_closed_is_found_not_due_on_retry(tmp_path):
    """Discord refuses the warning, and round 3's lane stops there. Meanwhile the appeals are
    approved on the league lane, which closes the submission (its row stays, closed). Retry finds
    the lock no longer due and clears the stop, posting nothing in the closed channel."""
    league = await review_league(tmp_path)
    channel = league.channel(SUBMISSION_CHANNEL)
    channel.send_fails = http_error(status=403, text="Missing Access")

    await _listen(league, _typed(league))
    await run_queue(league.bot)

    stopped = await stopped_job(league.db_path, LANE)
    assert stopped is not None and stopped["name"] == "post_warning"

    async with get_connection(league.db_path) as db:
        await db.execute(
            "UPDATE round_submission_channels SET closed = 1 WHERE channel_id = ?",
            (SUBMISSION_CHANNEL,),
        )
        await db.commit()
    channel.send_fails = None
    await retry_job(league.bot, lane=LANE)

    assert await stopped_job(league.db_path) is None
    assert _warnings(league) == []
    assert await _warning_rows(league.db_path) == []
