"""A standing post's two jobs: the post, then the deletion of the message it replaces (#439, slice 6).

`core/services/standing_posts.py` holds what every standing post shares: the job that deletes the
message a post replaced (`delete_replaced`), which the post plans after itself, and the removal of
a copy a post's earlier try sent where its record then failed (`remove_kept`). A replaced message
already gone, or its channel, completes the deletion; one Discord refuses to delete stops the queue,
keeping its link, and a league admin's Discard names it for deletion by hand.

The change types are the dummy ones of `test_change_queue.py`, on its league; the league's server
holds Pro's calendar channel (601) with its old calendar (6001) standing.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from tests.core.test_change_queue import (  # noqa: F401 — `env` is the fixture
    _ask,
    _queue,
    _type,
    env,
)
from tests.support.change_queue import http_error, in_a_job, run_queue, step_rows, stopped_job
from tests.support.review_league import channel

CALENDAR_CHANNEL = 601
OLD, NEW = 6001, 7001


def _server(env, *, channel_standing: bool = True) -> Any:
    """The league's server, holding the calendar channel with its old calendar (6001) and the
    copy a failed record left (7001), unless *channel_standing* is False."""
    events: list = []
    calendar = channel(CALENDAR_CHANNEL, events)
    calendar.seed(OLD, "the old calendar")
    calendar.seed(NEW, "the copy a failed record left")
    channels = {CALENDAR_CHANNEL: calendar} if channel_standing else {}
    guild = MagicMock(spec=discord.Guild)
    guild.get_channel = MagicMock(side_effect=channels.get)
    env.bot.get_guild = MagicMock(return_value=guild)
    return calendar


def _deleting(env) -> Any:
    """A dummy change of one job, the deletion of the old calendar as a post plans it."""
    from leaguebot.core.services import standing_posts

    planned = standing_posts.replaced(CALENDAR_CHANNEL, OLD, NEW, what="calendar",
                                      division_name="Pro")
    return _queue(env, _type(steps=[standing_posts.delete_replaced_step()], opening=planned))


# ---------------------------------------------------------------------------
# What a post plans
# ---------------------------------------------------------------------------


def test_replaced_plans_one_delete_of_the_old_message():
    from leaguebot.core.models.change import PlannedStep
    from leaguebot.core.services import standing_posts

    planned = standing_posts.replaced(CALENDAR_CHANNEL, OLD, NEW, what="calendar",
                                      division_name="Pro")

    assert planned == (PlannedStep(standing_posts.DELETE_REPLACED, {
        "channel_id": CALENDAR_CHANNEL, "message_id": OLD, "what": "calendar",
        "division_name": "Pro",
    }),)
    assert standing_posts.DELETE_REPLACED == "delete_replaced"


@pytest.mark.parametrize("channel_id, old, new", [
    pytest.param(CALENDAR_CHANNEL, None, NEW, id="no old message"),
    pytest.param(CALENDAR_CHANNEL, NEW, NEW, id="the old is the new"),
    pytest.param(None, OLD, NEW, id="no channel"),
])
def test_replaced_plans_nothing(channel_id, old, new):
    from leaguebot.core.services import standing_posts

    assert standing_posts.replaced(channel_id, old, new, what="calendar",
                                   division_name="Pro") == ()


# ---------------------------------------------------------------------------
# The delete job
# ---------------------------------------------------------------------------


async def test_a_replaced_message_already_gone_completes_its_delete(env):
    """The old calendar deleted by hand meanwhile: Discord answers NotFound, and the job is done."""
    calendar = _server(env)
    calendar.messages[OLD].delete = AsyncMock(
        side_effect=http_error(discord.NotFound, status=404, text="Unknown Message")
    )
    _deleting(env)

    await _ask(env)
    await run_queue(env.bot)

    assert await stopped_job(env.db_path) is None
    [job] = await step_rows(env.db_path)
    assert job["name"] == "delete_replaced"
    assert job["done_at"] is not None
    assert job["result"] == {"gone": True}


async def test_a_replaced_message_discord_refuses_stops_the_queue_keeping_its_link(env):
    """Discord refuses the deletion: the queue stops at the job, which keeps the old calendar's
    link for a Discard to name."""
    calendar = _server(env)
    calendar.messages[OLD].delete = AsyncMock(
        side_effect=http_error(discord.Forbidden, status=403, text="Missing Permissions")
    )
    _deleting(env)

    await _ask(env)
    await run_queue(env.bot)

    job = await stopped_job(env.db_path)
    assert job is not None and job["name"] == "delete_replaced"
    assert job["result"]["link"] == calendar.messages[OLD].jump_url
    assert OLD in calendar.messages


async def test_a_replaced_message_whose_channel_is_gone_completes_its_delete(env):
    """The calendar channel deleted meanwhile: the message went with it, and the job is done."""
    _server(env, channel_standing=False)
    _deleting(env)

    await _ask(env)
    await run_queue(env.bot)

    assert await stopped_job(env.db_path) is None
    [job] = await step_rows(env.db_path)
    assert job["done_at"] is not None
    assert job["result"] == {"gone": True}


def test_a_discarded_delete_is_named_with_its_link_for_deletion_by_hand():
    from leaguebot.core.services import standing_posts
    from leaguebot.core.services.change_queue import StepView

    link = f"https://discord.test/{CALENDAR_CHANNEL}/{OLD}"
    view = StepView(
        name=standing_posts.DELETE_REPLACED,
        payload={"channel_id": CALENDAR_CHANNEL, "message_id": OLD, "what": "calendar",
                 "division_name": "Pro"},
        result={"link": link, "discarded": {"by": 77, "at": "2026-10-01T12:00:00+00:00"}},
        done=True,
    )

    assert standing_posts.not_deleted(view) == (
        f"**Pro** — its earlier calendar could not be deleted ({link}): delete it by hand."
    )


# ---------------------------------------------------------------------------
# The copy a failed record left
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("refused", [
    pytest.param(False, id="removed"),
    pytest.param(True, id="refused"),
])
async def test_a_copy_a_failed_record_left_is_removed_before_the_next_try(env, refused):
    """A post's record failed after it sent 7001: before the next try posts again, 7001 is
    deleted and the old calendar (6001) is left for the delete job; where Discord refuses, the
    job fails again keeping 7001 to remove."""
    from leaguebot.core.models.change import StepFailedOnDiscord
    from leaguebot.core.services import standing_posts

    calendar = _server(env)
    if refused:
        calendar.messages[NEW].delete = AsyncMock(
            side_effect=http_error(discord.Forbidden, status=403, text="Missing Permissions")
        )
    kept = {"new": NEW, "old": OLD, "channel_id": CALENDAR_CHANNEL}

    if refused:
        with pytest.raises(StepFailedOnDiscord) as failed:
            with in_a_job():
                await standing_posts.remove_kept(env.bot, kept)
        assert failed.value.result["new"] in (NEW, [NEW])
        assert failed.value.result["channel_id"] == CALENDAR_CHANNEL
        assert NEW in calendar.messages
    else:
        with in_a_job():
            await standing_posts.remove_kept(env.bot, kept)
        assert NEW not in calendar.messages
    assert OLD in calendar.messages
