"""An attendance sheet asked of the change queue (#439, slice 6): `/attendance sync` makes its own
change, then asks the queue, as the bot's and repeatably, to post the division's sheet
(`attendance.sheet.post`), and its sanctions ask likewise for the sheet of each division a sack
reached; the sync's outcome is unchanged and the sheet follows on the queue. A sheet Discord
refuses stops the queue rather than being put on the old retry queue.

The league is `ongoing_league`'s with attendance on: season 7 being raced, Pro (21) with its
attendance channel 604, its round 1 final.
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from leaguebot.attendance.services import attendance_service
from tests.attendance.test_attendance_sheet_composition import (  # noqa: F401 — the fixture
    AM,
    PRO as COMPOSED_PRO,
    _seat_driver_two_in_pro_past_the_threshold,
    db_path,
)
from tests.support.change_queue import (
    change_rows,
    discard_job,
    http_error,
    queued_log_lines,
    retry_job,
    run_queue,
    step_rows,
    stopped_job,
)
from tests.support.season_league import (
    DIVISIONS,
    LEWIS,
    PRO,
    SEASON_ID,
    ongoing_league,
    profile_id,
    round_id,
)

KIND = "attendance.sheet.post"
SHEET = DIVISIONS[PRO][3].attendance
ROUND_1 = round_id(PRO, 1)


async def _scored_league(tmp_path: Any) -> Any:
    """`ongoing_league` with attendance on, Lewis holding 3 attendance points after round 1."""
    league = await ongoing_league(tmp_path, attendance=True)
    await league.write(
        "INSERT INTO driver_round_attendance (round_id, division_id, driver_profile_id, "
        "rsvp_status, attended, total_points_after) VALUES (?, ?, ?, 'NO_RSVP', 1, 3)",
        ROUND_1, PRO, profile_id(LEWIS),
    )
    return league


async def _sync(league: Any) -> Any:
    """`/attendance sync` of Pro from round 1, its recalculation stubbed (the totals are the
    test's), handed the league's change queue; the queue is not run."""
    with patch.object(attendance_service, "record_attendance_from_results_full_recompute",
                      new=AsyncMock(return_value=None)), \
            patch.object(attendance_service, "distribute_attendance_points",
                         new=AsyncMock(return_value=None)):
        return await attendance_service.sync_attendance(
            league.bot, league.guild, league.db_path, PRO, ROUND_1, SEASON_ID,
            queue=league.bot.change_queue,
        )


async def _sheet_asks(league: Any) -> list[dict[str, Any]]:
    return [row for row in await change_rows(league.db_path) if row["kind"] == KIND]


async def _said(league: Any) -> str:
    """Every log line, sent or still waiting on the old retry queue."""
    return "\n".join([league.log(), *await queued_log_lines(league.db_path)])


async def test_a_sync_asks_the_queue_for_the_sheet_and_replies_as_before(tmp_path):
    league = await _scored_league(tmp_path)

    outcome = await _sync(league)

    [asked] = await _sheet_asks(league)
    assert asked["origin"] == "BOT"
    payload = json.loads(asked["payload"])
    assert (payload["division_id"], payload["round_id"]) == (PRO, ROUND_1)
    assert league.texts(SHEET) == [], "the sync posted the sheet itself"
    assert outcome.complete and outcome.posting_faults == []

    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    assert len(league.texts(SHEET)) == 1


async def test_a_sheet_a_sync_asked_for_that_discord_refuses_stops_the_queue_and_queues_nothing_on_the_old_retry_queue(
    tmp_path,
):
    league = await _scored_league(tmp_path)
    league.channel(SHEET).send_fails = http_error(discord.Forbidden, status=403,
                                                  text="Missing Access")

    outcome = await _sync(league)
    await run_queue(league.bot)

    assert outcome.posting_faults == []
    assert (await stopped_job(league.db_path))["name"] == "post_sheet"
    assert await league.rows("SELECT id FROM pending_messages WHERE channel_id = ?", SHEET) == []

    league.channel(SHEET).send_fails = None
    await retry_job(league.bot)

    assert await stopped_job(league.db_path) is None
    assert len(league.texts(SHEET)) == 1


async def test_a_sync_s_sanctions_ask_for_the_sheet_of_each_division_a_sack_reached(db_path):
    """Driver 2 is past Pro's autosack threshold at round 102 and has a sheet in Am, whose latest
    scored round is 201. The sanctions ask the queue for Pro's sheet at round 102 marking driver
    2, and for Am's at round 201 marking driver 2, and post neither themselves."""
    await _seat_driver_two_in_pro_past_the_threshold(db_path)
    bot = MagicMock()
    bot.user.id = 1
    bot.output_router.post_log = AsyncMock()
    bot.placement_service.sack_driver = AsyncMock(return_value=[COMPOSED_PRO, AM])
    queue = MagicMock()
    queue.ask = AsyncMock()

    with patch.object(attendance_service, "post_attendance_sheet", new=AsyncMock()) as posted, \
            patch("leaguebot.results.services.verdict_announcement_service.banner_for_round",
                  return_value=None, create=True), \
            patch("leaguebot.results.services.verdict_announcement_service."
                  "post_autosanction_announcement", new=AsyncMock(return_value=[]),
                  create=True):
        await attendance_service.enforce_attendance_sanctions(
            bot, MagicMock(), db_path, 102, COMPOSED_PRO, 1, queue=queue,
        )

    asked = [call.args[1] for call in queue.ask.await_args_list if call.args[0] == KIND]
    assert [(p["division_id"], p["round_id"], sorted(p["sanctioned"])) for p in asked] == [
        (COMPOSED_PRO, 102, [2]), (AM, 201, [2]),
    ]
    posted.assert_not_awaited()


async def test_a_sheet_for_a_division_with_no_attendance_channel_stops_the_queue_naming_the_command(
    tmp_path,
):
    from leaguebot.attendance.services.sheet_posting import ask_sheet_post

    league = await _scored_league(tmp_path)
    await league.write(
        "UPDATE attendance_division_config SET attendance_channel_id = NULL WHERE division_id = ?",
        PRO,
    )

    await ask_sheet_post(league.bot.change_queue, league.db_path, PRO, ROUND_1)
    await run_queue(league.bot)

    assert (await stopped_job(league.db_path))["name"] == "post_sheet"
    assert ("**Pro** has no attendance channel set: set one with `/attendance channel "
            "attendance`, then press Retry.") in await _said(league)

    await league.write(
        "UPDATE attendance_division_config SET attendance_channel_id = ? WHERE division_id = ?",
        str(SHEET), PRO,
    )
    await retry_job(league.bot)

    assert await stopped_job(league.db_path) is None
    assert len(league.texts(SHEET)) == 1

    # Attendance switched off while a sheet waits: the sheet is no longer due, and is dropped.
    await ask_sheet_post(league.bot.change_queue, league.db_path, PRO, ROUND_1)
    await league.switch("attendance", False)
    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    assert len(league.texts(SHEET)) == 1


# ── The post and the deletion of the sheet it replaces, as two jobs ──────────────────

#: Pro's sheet standing in its attendance channel before the ask.
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


async def _sheet_jobs(league: Any) -> list[dict[str, Any]]:
    """The jobs of the one `attendance.sheet.post` asked, in order."""
    [change] = await _sheet_asks(league)
    return await step_rows(league.db_path, change["id"])


async def _run_sheet_through(league: Any, name: str) -> None:
    """Run the queue a job at a time until the sheet's job called *name* is done."""
    for _ in range(50):
        if any(job["name"] == name and job["done_at"] for job in await _sheet_jobs(league)):
            return
        await run_queue(league.bot, steps=1)
    raise AssertionError(f"the job {name} never finished: {await _sheet_jobs(league)}")


def _sheet_events(league: Any) -> list[tuple[str, int]]:
    """What Pro's attendance channel saw, in order: each send and delete, with its message."""
    return [(kind, mid) for kind, cid, mid in league.events if cid == SHEET]


async def _sheet_id(league: Any) -> Any:
    [row] = await league.rows(
        "SELECT attendance_message_id FROM attendance_division_config WHERE division_id = ?", PRO,
    )
    return row["attendance_message_id"]


async def _ask_pro_s_sheet(league: Any) -> None:
    from leaguebot.attendance.services.sheet_posting import ask_sheet_post

    await ask_sheet_post(league.bot.change_queue, league.db_path, PRO, ROUND_1)


async def test_the_new_sheet_is_posted_before_the_old_one_is_deleted_by_a_job(tmp_path):
    league = await _scored_league(tmp_path)
    await _old_sheet_standing(league)

    await _ask_pro_s_sheet(league)
    await _run_sheet_through(league, "post_sheet")

    [(sent, new)] = _sheet_events(league)
    assert sent == "send"
    assert OLD_SHEET in league.channel(SHEET).messages

    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    assert _sheet_events(league) == [("send", new), ("delete", OLD_SHEET)]
    assert [job["name"] for job in await _sheet_jobs(league)] == ["post_sheet", "delete_replaced"]
    assert str(await _sheet_id(league)) == str(new)


async def test_a_sheet_s_id_is_saved_with_its_post_and_the_final_sheet_saves_none(tmp_path):
    """Once the post job is done, and before the deletion after it runs, the division records
    the new sheet as its live one. A sheet that takes no live slot, as the final sheet of a
    season, is saved nowhere: its record leaves the live sheet as it was."""
    from leaguebot.core.db.database import get_connection

    league = await _scored_league(tmp_path)
    await _old_sheet_standing(league)

    await _ask_pro_s_sheet(league)
    await _run_sheet_through(league, "post_sheet")

    [(_sent, new)] = _sheet_events(league)
    assert str(await _sheet_id(league)) == str(new)
    assert OLD_SHEET in league.channel(SHEET).messages, "the old sheet went too soon"

    async with get_connection(league.db_path) as db:
        await attendance_service.record_sheet_on(
            db, PRO, {"new": 6300, "old": str(new), "channel_id": SHEET, "takes_slot": False},
        )
        await db.commit()
    assert str(await _sheet_id(league)) == str(new)


async def test_an_old_sheet_discord_will_not_delete_stops_the_queue_and_once_discarded_is_named(
    tmp_path,
):
    """The new sheet is posted; Discord refuses to delete the old one: the queue stops at the
    deletion, keeping the old sheet's link. Once a league admin discards it, the log channel's
    Discard line names the deletion not done, and both sheets stand."""
    league = await _scored_league(tmp_path)
    await _old_sheet_standing(league)
    league.channel(SHEET).messages[OLD_SHEET].delete = AsyncMock(
        side_effect=http_error(discord.Forbidden, status=403, text="Missing Permissions")
    )

    await _ask_pro_s_sheet(league)
    await run_queue(league.bot)

    stopped = await stopped_job(league.db_path)
    assert stopped is not None and stopped["name"] == "delete_replaced"
    assert OLD_SHEET_LINK in str(stopped["result"])

    await discard_job(league.bot)

    assert await stopped_job(league.db_path) is None
    assert "deleting the earlier attendance sheet of **Pro**" in await _said(league)
    assert OLD_SHEET in league.channel(SHEET).messages
    assert len([kind for kind, _ in _sheet_events(league) if kind == "send"]) == 1
