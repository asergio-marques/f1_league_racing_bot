"""An attendance sheet asked of the change queue (#439, slice 6): `/attendance sync` makes its own
change, then asks the queue, as the bot's and repeatably, to post the division's sheet
(`attendance.sheet.post`), and its sanctions ask likewise for the sheet of each division a sack
reached; the sync's outcome is unchanged and the sheet follows on the queue. A sheet Discord
refuses stops the queue rather than being put on the old retry queue.

The league is `ongoing_league`'s with attendance on: season 7 being raced, Pro (21) with its
attendance channel 604, its round 1 final. Everything of the change is imported inside a test, so
this file collects while it is unbuilt.
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
    http_error,
    queued_log_lines,
    retry_job,
    run_queue,
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

UNBUILT = "#439: an attendance sheet is not yet asked of the change queue"

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


@pytest.mark.xfail(strict=True, reason=UNBUILT)
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


@pytest.mark.xfail(strict=True, reason=UNBUILT)
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


@pytest.mark.xfail(strict=True, reason=UNBUILT)
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
                  return_value=None), \
            patch("leaguebot.results.services.verdict_announcement_service."
                  "post_autosanction_announcement", new=AsyncMock(return_value=[])):
        await attendance_service.enforce_attendance_sanctions(
            bot, MagicMock(), db_path, 102, COMPOSED_PRO, 1, queue=queue,
        )

    asked = [call.args[1] for call in queue.ask.await_args_list if call.args[0] == KIND]
    assert [(p["division_id"], p["round_id"], sorted(p["sanctioned"])) for p in asked] == [
        (COMPOSED_PRO, 102, [2]), (AM, 201, [2]),
    ]
    posted.assert_not_awaited()


@pytest.mark.xfail(strict=True, reason=UNBUILT)
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
