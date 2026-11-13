"""A division's attendance sheet posted by `/attendance sync` (#439, slice 6; #285): the sync is
one change on the queue, and the sheet is its `post_sheet` job, which posts the new sheet and plans
the deletion of the one it replaces as a job after it. A sheet Discord refuses stops the queue
rather than being put on the old retry queue.

The league is `ongoing_league`'s with attendance on: season 7 being raced, Pro (21) with its
attendance channel 604, its round 1 final. No attendance threshold is set, so the sync owes no
sanction and its one post is Pro's sheet.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord

from leaguebot.attendance.services import attendance_sheet
from leaguebot.attendance.services.attendance_sync_change import SYNC_SAVE
from tests.attendance.test_attendance_sheet_composition import (  # noqa: F401 — the fixture
    db_path,
)
from tests.support.change_queue import (
    acknowledgement,
    change_rows,
    discard_job,
    http_error,
    member_interaction,
    queued_log_lines,
    retry_job,
    run_queue,
    step_rows,
    stopped_job,
    updated_reply,
)
from tests.support.season_league import (
    DIVISIONS,
    LEWIS,
    PRO,
    ongoing_league,
    profile_id,
    round_id,
    run_attendance_channel_command,
)
from tests.support.undecorate import undecorate

KIND = "attendance.sync"
SHEET = DIVISIONS[PRO][3].attendance
ROUND_1 = round_id(PRO, 1)


async def _scored_league(tmp_path: Any, **options: Any) -> Any:
    """`ongoing_league` with attendance on, Lewis holding 3 attendance points after round 1."""
    league = await ongoing_league(tmp_path, attendance=True, **options)
    await league.write(
        "INSERT INTO driver_round_attendance (round_id, division_id, driver_profile_id, "
        "rsvp_status, attended, total_points_after) VALUES (?, ?, ?, 'NO_RSVP', 1, 3)",
        ROUND_1, PRO, profile_id(LEWIS),
    )
    return league


async def _sync_pro(league: Any) -> Any:
    """Alex, a league manager, runs `/attendance sync division:Pro round:1`; the queue is not
    run. Gives his interaction."""
    from leaguebot.attendance.cogs.attendance_cog import AttendanceCog

    cog = AttendanceCog.__new__(AttendanceCog)
    cog.bot = league.bot
    interaction = member_interaction(league.bot)
    interaction.guild = league.guild
    interaction.command = AttendanceCog.sync
    interaction.user.display_name = "Alex"
    await undecorate(AttendanceCog.sync)(cog, interaction, "Pro", 1)
    return interaction


async def _syncs(league: Any) -> list[dict[str, Any]]:
    return [row for row in await change_rows(league.db_path) if row["kind"] == KIND]


async def _said(league: Any) -> str:
    """Every log line, sent or still waiting on the old retry queue."""
    return "\n".join([league.log(), *await queued_log_lines(league.db_path)])


async def _sync_jobs(league: Any) -> list[dict[str, Any]]:
    """The jobs of the last `/attendance sync` asked, in order."""
    change = (await _syncs(league))[-1]
    return await step_rows(league.db_path, change["id"])


async def _run_sync_through(league: Any, name: str) -> None:
    """Run the queue a job at a time until the sync's job called *name* is done."""
    for _ in range(50):
        if any(job["name"] == name and job["done_at"] for job in await _sync_jobs(league)):
            return
        await run_queue(league.bot, steps=1)
    raise AssertionError(f"the job {name} never finished: {await _sync_jobs(league)}")


def _sheet_events(league: Any) -> list[tuple[str, int]]:
    """What Pro's attendance channel saw, in order: each send and delete, with its message."""
    return [(kind, mid) for kind, cid, mid in league.events if cid == SHEET]


def _sends(league: Any) -> int:
    return len([kind for kind, _ in _sheet_events(league) if kind == "send"])


async def test_a_sheet_discord_refuses_stops_the_queue_and_queues_nothing_on_the_old_retry_queue(
    tmp_path,
):
    """Alex runs `/attendance sync` for Pro from round 1, and Discord refuses Pro's sheet
    (Missing Access). The queue stops on the sheet, and nothing is put on the old retry queue;
    once the channel takes posts again, Retry posts the sheet once."""
    league = await _scored_league(tmp_path)
    league.channel(SHEET).send_fails = http_error(discord.Forbidden, status=403,
                                                  text="Missing Access")

    await _sync_pro(league)
    await run_queue(league.bot)

    assert (await stopped_job(league.db_path))["name"] == "post_sheet"
    assert await league.rows("SELECT id FROM pending_messages WHERE channel_id = ?", SHEET) == []

    league.channel(SHEET).send_fails = None
    await retry_job(league.bot)

    assert await stopped_job(league.db_path) is None
    assert len(league.texts(SHEET)) == 1


async def test_a_stopped_sheet_lands_once_the_attendance_channel_is_set_and_retried(tmp_path):
    """Set the channel, then Retry: the remedy the stop notice leads a manager to, end to end.

    Alex runs `/attendance sync` for Pro from round 1; once its save is made, and before its sheet
    is posted, Pro's attendance channel is unset. The queue stops at the sheet, the stop naming
    the command that sets the channel. Alex runs `/attendance channel attendance` for Pro with
    channel 604 and is acknowledged; the queue runs. The setting does not wait behind the stopped
    sheet: it runs past it, saves 604 as Pro's attendance channel and tells Alex the channel was
    set, while the sheet still stops the league's lane. Alex presses Retry: the sheet lands once
    in 604, and nothing stops the queue. Alex then syncs again, and attendance is switched off
    once its save is made: the sheet is no longer due, and is dropped, posting nothing."""
    league = await _scored_league(tmp_path)
    await _sync_pro(league)
    await _run_sync_through(league, SYNC_SAVE)
    await league.write(
        "UPDATE attendance_division_config SET attendance_channel_id = NULL WHERE division_id = ?",
        PRO,
    )
    await run_queue(league.bot)

    assert (await stopped_job(league.db_path))["name"] == "post_sheet"
    assert "/attendance channel attendance" in await _said(league)

    manager = await run_attendance_channel_command(league, "attendance", SHEET)
    assert acknowledgement(manager).startswith("⏳ ")
    await run_queue(league.bot)

    assert "set to" in updated_reply(manager)
    [row] = await league.rows(
        "SELECT attendance_channel_id FROM attendance_division_config WHERE division_id = ?", PRO,
    )
    assert int(row["attendance_channel_id"]) == SHEET
    assert (await stopped_job(league.db_path))["name"] == "post_sheet", (
        "setting the channel ran the job"
    )
    await retry_job(league.bot)

    assert _sends(league) == 1
    assert await stopped_job(league.db_path) is None

    # Attendance switched off while a sheet waits: the sheet is no longer due, and is dropped.
    await _sync_pro(league)
    await _run_sync_through(league, SYNC_SAVE)
    await league.switch("attendance", False)
    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    assert _sends(league) == 1


# ── The post and the deletion of the sheet it replaces, as two jobs ──────────────────

#: Pro's sheet standing in its attendance channel before the sync.
OLD_SHEET = 6200
#: The link Discord gives Pro's old sheet, which a discarded deletion names.
OLD_SHEET_LINK = f"https://discord.test/{SHEET}/{OLD_SHEET}"


async def _old_sheet_standing(league: Any) -> None:
    """Pro's sheet stands as message 6200, recorded as the division's live sheet."""
    await league.write(
        "UPDATE attendance_division_config SET attendance_message_id = ? WHERE division_id = ?",
        str(OLD_SHEET), PRO,
    )
    league.channel(SHEET).seed(OLD_SHEET, "Pro's attendance sheet")


async def _sheet_id(league: Any) -> Any:
    [row] = await league.rows(
        "SELECT attendance_message_id FROM attendance_division_config WHERE division_id = ?", PRO,
    )
    return row["attendance_message_id"]


async def test_the_new_sheet_is_posted_before_the_old_one_is_deleted_by_a_job(tmp_path):
    """Pro's sheet stands as message 6200. Alex runs `/attendance sync` for Pro from round 1.
    Once the sync's sheet job is done, the new sheet is sent and 6200 still stands; the queue
    runs on and a job of its own deletes 6200 after it, and the new sheet is Pro's live one."""
    league = await _scored_league(tmp_path)
    await _old_sheet_standing(league)

    await _sync_pro(league)
    await _run_sync_through(league, "post_sheet")

    [(sent, new)] = _sheet_events(league)
    assert sent == "send"
    assert OLD_SHEET in league.channel(SHEET).messages

    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    assert _sheet_events(league) == [("send", new), ("delete", OLD_SHEET)]
    assert [job["name"] for job in await _sync_jobs(league)
            if job["name"] in ("post_sheet", "delete_replaced")] == ["post_sheet",
                                                                     "delete_replaced"]
    assert str(await _sheet_id(league)) == str(new)


async def test_a_sheet_s_id_is_saved_with_its_post_and_the_final_sheet_saves_none(tmp_path):
    """Pro's sheet stands as message 6200 and Alex runs `/attendance sync` for Pro from round 1.
    Once the sync's sheet job is done, and before the deletion after it runs, the division
    records the new sheet as its live one. A sheet that takes no live slot, as the final sheet of
    a season, is saved nowhere: its record leaves the live sheet as it was."""
    from leaguebot.core.db.database import get_connection

    league = await _scored_league(tmp_path)
    await _old_sheet_standing(league)

    await _sync_pro(league)
    await _run_sync_through(league, "post_sheet")

    [(_sent, new)] = _sheet_events(league)
    assert str(await _sheet_id(league)) == str(new)
    assert OLD_SHEET in league.channel(SHEET).messages, "the old sheet went too soon"

    async with get_connection(league.db_path) as db:
        await attendance_sheet.record_sheet_on(
            db, PRO, {"new": 6300, "old": str(new), "channel_id": SHEET, "takes_slot": False},
        )
        await db.commit()
    assert str(await _sheet_id(league)) == str(new)


async def test_a_sheet_whose_grid_cannot_be_read_stops_and_leaves_the_standing_one(tmp_path):
    """Pro's sheet stands as message 6200, and the image module is on with the attendance
    graphic switched on, so Pro's round 1 sheet is drawn with its grid of rounds. Alex runs
    `/attendance sync` for Pro from round 1; once its save is made, the points each round gave
    each driver cannot be read. The queue stops at the sheet, nothing is sent, and 6200 stays as
    Pro's live sheet, rather than a sheet with an empty grid replacing it. The points become
    readable again and a league admin presses Retry: the new sheet is posted and replaces
    6200."""
    from leaguebot.image.services.image_attendance_post import (
        ATTENDANCE_ASPECT,
        ATTENDANCE_TEMPLATE_KEY,
        attendance_enabled,
    )

    league = await _scored_league(tmp_path, images=True)
    await _old_sheet_standing(league)
    # The attendance graphic switched on, and its template sound.
    league.bot.image_config_service.get_toggles = AsyncMock(
        return_value={ATTENDANCE_ASPECT: True}
    )
    league.bot.image_validity_service.template_reports = AsyncMock(
        return_value={ATTENDANCE_TEMPLATE_KEY: MagicMock(valid=True)}
    )
    assert await attendance_enabled(league.bot), "the graphic is not drawn in this league"

    await _sync_pro(league)
    await _run_sync_through(league, SYNC_SAVE)
    # The grid alone reads the points each round gave; the sheet's own rows read the totals.
    await league.write(
        "ALTER TABLE driver_round_attendance RENAME COLUMN points_awarded TO points_unreadable"
    )
    await run_queue(league.bot)

    assert (await stopped_job(league.db_path))["name"] == "post_sheet"
    assert _sheet_events(league) == []
    assert OLD_SHEET in league.channel(SHEET).messages
    assert str(await _sheet_id(league)) == str(OLD_SHEET)

    await league.write(
        "ALTER TABLE driver_round_attendance RENAME COLUMN points_unreadable TO points_awarded"
    )
    await retry_job(league.bot)

    assert await stopped_job(league.db_path) is None
    [(sent, new)] = [event for event in _sheet_events(league) if event[0] == "send"]
    assert ("delete", OLD_SHEET) in _sheet_events(league)
    assert str(await _sheet_id(league)) == str(new)


async def test_an_old_sheet_discord_will_not_delete_stops_the_queue_and_once_discarded_is_named(
    tmp_path,
):
    """Pro's sheet stands as message 6200 and Alex runs `/attendance sync` for Pro from round 1.
    The new sheet is posted; Discord refuses to delete the old one: the queue stops at the
    deletion, keeping the old sheet's link. Once a league admin discards it, the log channel's
    Discard line names the deletion not done, and both sheets stand."""
    league = await _scored_league(tmp_path)
    await _old_sheet_standing(league)
    league.channel(SHEET).messages[OLD_SHEET].delete = AsyncMock(
        side_effect=http_error(discord.Forbidden, status=403, text="Missing Permissions")
    )

    await _sync_pro(league)
    await run_queue(league.bot)

    stopped = await stopped_job(league.db_path)
    assert stopped is not None and stopped["name"] == "delete_replaced"
    assert OLD_SHEET_LINK in str(stopped["result"])

    await discard_job(league.bot)

    assert await stopped_job(league.db_path) is None
    assert "deleting the earlier attendance sheet of **Pro**" in await _said(league)
    assert OLD_SHEET in league.channel(SHEET).messages
    assert _sends(league) == 1
