"""A calendar's post and the deletion of the calendar it replaces, as two jobs (#439, slice 6).

Wherever the bot posts a division's calendar on the change queue (a round's, a division's or a
season's cancellation, a season's approval, `/division calendar-sync`), the `post_calendar` job
posts the new calendar and saves its id in the save that marks it done, and plans after itself a
`delete_replaced` job for the calendar it replaces, read from the division as the post runs. A
refused post leaves the old calendar standing and plans no deletion; a refused deletion stops the
queue, and once a league admin discards it the reply names the old calendar, with its link, for
deletion by hand.

The league is `ongoing_league`'s (season 3 being raced, Pro's calendar standing as message 6001 in
its calendar channel, 601), or `season_league`'s for an approval (no calendar posted yet). Each test
asks for the change through the real cog, runs the queue and reads the channel, the jobs and the
database.
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock

import discord
import pytest

from tests.support.change_queue import (
    change_rows,
    discard_job,
    http_error,
    retry_job,
    run_queue,
    step_rows,
    stopped_job,
)
from tests.support.season_league import (
    CALENDAR_MESSAGE,
    DIVISIONS,
    PRO,
    ROUND_CANCEL_KIND,
    cancel_round,
    ongoing_league,
    press_approve,
    reply,
    season_league,
)

PRO_CALENDAR = DIVISIONS[PRO][3].calendar
#: The link Discord gives Pro's old calendar, which a discarded deletion names.
OLD_LINK = f"https://discord.test/{PRO_CALENDAR}/{CALENDAR_MESSAGE}"


async def _change(league: Any, kind: str) -> dict[str, Any]:
    rows = [row for row in await change_rows(league.db_path) if row["kind"] == kind]
    assert len(rows) == 1, rows
    return rows[0]


async def _jobs(league: Any, kind: str = ROUND_CANCEL_KIND) -> list[dict[str, Any]]:
    """The change's jobs, in order, each with its payload read back."""
    jobs = await step_rows(league.db_path, (await _change(league, kind))["id"])
    for job in jobs:
        job["payload"] = json.loads(job["payload"] or "{}")
    return jobs


async def _run_through(league: Any, name: str, kind: str = ROUND_CANCEL_KIND) -> None:
    """Run the queue a job at a time until the change's job called *name* is done."""
    for _ in range(100):
        if any(job["name"] == name and job["done_at"] for job in await _jobs(league, kind)):
            return
        await run_queue(league.bot, steps=1)
    raise AssertionError(f"the job {name} never finished: {await _jobs(league, kind)}")


async def _cancel(league: Any) -> Any:
    """Cancel Pro's round 3, as admin 77; the queue is not run."""
    interaction = await cancel_round(league, "Pro", 3)
    assert not league.errors, league.errors
    return interaction


def _calendar_events(league: Any) -> list[tuple[str, int]]:
    """What the calendar channel saw, in order: each send and delete, with its message."""
    return [(kind, mid) for kind, cid, mid in league.events if cid == PRO_CALENDAR]


async def _calendar_id(league: Any) -> str | None:
    [row] = await league.rows("SELECT calendar_message_id FROM divisions WHERE id = ?", PRO)
    return row["calendar_message_id"]


def _undeletable(league: Any, message_id: int) -> None:
    """Discord refuses to delete *message_id* in Pro's calendar channel."""
    league.channel(PRO_CALENDAR).messages[message_id].delete = AsyncMock(
        side_effect=http_error(discord.Forbidden, status=403, text="Missing Permissions")
    )


async def test_the_new_calendar_is_posted_before_the_old_one_is_deleted_by_a_job_of_its_own(
    tmp_path,
):
    league = await ongoing_league(tmp_path)
    await _cancel(league)

    await _run_through(league, "post_calendar")
    new = _calendar_events(league)[0][1]
    assert _calendar_events(league) == [("send", new)]
    assert await _calendar_id(league) == str(new)
    assert CALENDAR_MESSAGE in league.channel(PRO_CALENDAR).messages

    await run_queue(league.bot)

    assert _calendar_events(league) == [("send", new), ("delete", CALENDAR_MESSAGE)]
    names = [job["name"] for job in await _jobs(league)]
    assert names.index("delete_replaced") == names.index("post_calendar") + 1
    [delete] = [job for job in await _jobs(league) if job["name"] == "delete_replaced"]
    assert delete["payload"]["message_id"] == CALENDAR_MESSAGE


async def test_a_calendar_post_refused_leaves_the_old_calendar_and_plans_no_delete(tmp_path):
    league = await ongoing_league(tmp_path)
    league.channel(PRO_CALENDAR).send_fails = http_error(
        discord.Forbidden, status=403, text="Missing Access"
    )
    await _cancel(league)

    await run_queue(league.bot)
    assert (await stopped_job(league.db_path))["name"] == "post_calendar"
    assert CALENDAR_MESSAGE in league.channel(PRO_CALENDAR).messages
    assert await _calendar_id(league) == str(CALENDAR_MESSAGE)

    await discard_job(league.bot)

    assert "delete_replaced" not in [job["name"] for job in await _jobs(league)]
    assert CALENDAR_MESSAGE in league.channel(PRO_CALENDAR).messages
    assert await _calendar_id(league) == str(CALENDAR_MESSAGE)


@pytest.mark.parametrize("occasion", [
    pytest.param("round cancel", id="round cancel"),
    pytest.param("season approval", id="season approval"),
])
async def test_an_old_calendar_discord_will_not_delete_stops_the_queue_and_once_discarded_is_named(
    tmp_path, monkeypatch, occasion,
):
    """The new calendar is posted; Discord refuses to delete the old one: the queue stops at the
    deletion, and once a league admin discards it the reply names the old calendar with its link,
    for deletion by hand. Both calendars stand."""
    if occasion == "round cancel":
        league = await ongoing_league(tmp_path)
        _undeletable(league, CALENDAR_MESSAGE)
        asked = await _cancel(league)
        named = (f"the earlier calendar could not be deleted ({OLD_LINK}); delete it by hand")
    else:
        league = await season_league(tmp_path, monkeypatch=monkeypatch)
        await league.write("UPDATE divisions SET calendar_message_id = ? WHERE id = ?",
                           str(CALENDAR_MESSAGE), PRO)
        league.channel(PRO_CALENDAR).seed(CALENDAR_MESSAGE, "Pro's calendar")
        _undeletable(league, CALENDAR_MESSAGE)
        asked = await press_approve(league)
        assert not league.errors, league.errors
        named = (f"**Pro** — its earlier calendar could not be deleted ({OLD_LINK}): delete it "
                 "by hand.")

    await run_queue(league.bot)
    stopped = await stopped_job(league.db_path)
    assert stopped is not None and stopped["name"] == "delete_replaced"
    assert OLD_LINK in str(stopped["result"])

    await discard_job(league.bot)

    assert named in reply(asked)
    assert CALENDAR_MESSAGE in league.channel(PRO_CALENDAR).messages
    assert len(league.texts(PRO_CALENDAR)) == 1


async def test_a_stop_between_the_calendar_post_and_its_delete_deletes_the_old_one_on_restart(
    tmp_path,
):
    league = await ongoing_league(tmp_path)
    await _cancel(league)
    await _run_through(league, "post_calendar")
    assert CALENDAR_MESSAGE in league.channel(PRO_CALENDAR).messages

    await league.restart()
    await run_queue(league.bot)

    assert CALENDAR_MESSAGE not in league.channel(PRO_CALENDAR).messages
    assert len(league.texts(PRO_CALENDAR)) == 1
    [new] = list(league.channel(PRO_CALENDAR).messages)
    assert await _calendar_id(league) == str(new)
    assert [job["name"] for job in await _jobs(league) if job["done_at"]].count(
        "delete_replaced") == 1


async def test_a_calendar_whose_id_could_not_be_saved_removes_the_copy_before_it_posts_again(
    tmp_path,
):
    """The new calendar is sent, and saving its id fails: the queue stops at the post, keeping
    the copy it sent; Retry deletes that copy first, posts once more and saves the new id."""
    league = await ongoing_league(tmp_path)
    await league.write(
        "CREATE TRIGGER refuse_calendar_id BEFORE UPDATE OF calendar_message_id ON divisions "
        "BEGIN SELECT RAISE(ABORT, 'the disk is full'); END"
    )
    await _cancel(league)

    await run_queue(league.bot)
    stopped = await stopped_job(league.db_path)
    assert stopped is not None and stopped["name"] == "post_calendar"
    copy = _calendar_events(league)[0][1]
    assert _calendar_events(league) == [("send", copy)]
    assert CALENDAR_MESSAGE in league.channel(PRO_CALENDAR).messages

    await league.write("DROP TRIGGER refuse_calendar_id")
    await retry_job(league.bot)

    events = _calendar_events(league)
    assert events[:2] == [("send", copy), ("delete", copy)]
    [new] = [mid for kind, mid in events[2:] if kind == "send"]
    assert events[2:] == [("send", new), ("delete", CALENDAR_MESSAGE)]
    assert list(league.channel(PRO_CALENDAR).messages) == [new]
    assert await _calendar_id(league) == str(new)


async def test_the_calendar_replaced_is_read_when_the_job_runs(tmp_path):
    """The division's calendar was posted again (6005) while the cancellation waited: its post
    deletes 6005, the calendar standing as it ran, and not the 6001 it held when asked."""
    league = await ongoing_league(tmp_path)
    await _cancel(league)
    await _run_through(league, "apply")
    await league.write("UPDATE divisions SET calendar_message_id = '6005' WHERE id = ?", PRO)
    league.channel(PRO_CALENDAR).seed(6005, "Pro's calendar, posted again")

    await run_queue(league.bot)

    [delete] = [job for job in await _jobs(league) if job["name"] == "delete_replaced"]
    assert delete["payload"]["message_id"] == 6005
    assert 6005 not in league.channel(PRO_CALENDAR).messages


async def test_a_first_calendar_plans_no_delete(tmp_path, monkeypatch):
    """A season approved with no calendar posted before: its calendar is posted and its id saved,
    and nothing is planned to delete."""
    from leaguebot.core.services.season_approval_change import KIND

    league = await season_league(tmp_path, monkeypatch=monkeypatch)
    await press_approve(league)
    assert not league.errors, league.errors

    await run_queue(league.bot)

    names = [job["name"] for job in await _jobs(league, KIND)]
    assert "post_calendar" in names
    assert "delete_replaced" not in names
    [new] = list(league.channel(PRO_CALENDAR).messages)
    assert await _calendar_id(league) == str(new)

