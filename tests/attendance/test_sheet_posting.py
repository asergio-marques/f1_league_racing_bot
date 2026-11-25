"""A division's attendance sheet posted by `/attendance sync` (#439, slice 6; #285): the sync is
one change on the queue, and the sheet is its `post_sheet` job, which posts the new sheet and plans
the deletion of the one it replaces as a job after it. A sheet Discord refuses stops the queue
rather than being put on the old retry queue.

The league is `ongoing_league`'s with attendance on: season 3 being raced, Pro (21) with its
attendance channel 604, its round 1 final. No attendance threshold is set, so the sync owes no
sanction and its one post is Pro's sheet.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from leaguebot.attendance.services.sheet import attendance_sheet
from leaguebot.attendance.services.sanctions.attendance_sync_change import SYNC_SAVE
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
from tests.support.review_league import channel
from tests.support.season_league import (
    DIVISIONS,
    LEWIS,
    PRO,
    ongoing_league,
    profile_id,
    round_id,
    run_attendance_channel_command,
)
from tests.support.settings_league import unruled
from tests.support.undecorate import undecorate
from tests.support.switches import answer_switch

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


#: The channel Pro's sheet moves to: one the league's server holds and no division uses.
MOVED_SHEET = 608


async def test_a_sheet_is_deleted_from_the_channel_it_was_posted_in_after_the_channel_moves(
    tmp_path,
):
    """Pro's sheet (6200) was posted in 604, and its attendance channel then moved to 608. Alex
    runs `/attendance sync` for Pro from round 1: the new sheet goes to 608 and the old one is
    deleted from 604, where it stands; the channel recorded beside the sheet's id is now 608."""
    league = await _scored_league(tmp_path)
    await _old_sheet_standing(league)
    moved = league._recording(channel(MOVED_SHEET, league.events))
    moved.guild = league.guild
    league.channels[MOVED_SHEET] = moved
    await league.write(
        "UPDATE attendance_division_config SET attendance_channel_id = ?, "
        "attendance_message_channel_id = ? WHERE division_id = ?",
        str(MOVED_SHEET), str(SHEET), PRO,
    )

    await _sync_pro(league)
    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    [new] = [mid for kind, cid, mid in league.events if kind == "send" and cid == MOVED_SHEET]
    assert _sheet_events(league) == [("delete", OLD_SHEET)]
    assert OLD_SHEET not in league.channel(SHEET).messages
    [row] = await league.rows(
        "SELECT attendance_message_id, attendance_message_channel_id "
        "FROM attendance_division_config WHERE division_id = ?", PRO,
    )
    assert (str(row["attendance_message_id"]), int(row["attendance_message_channel_id"])) == (
        str(new), MOVED_SHEET)


async def test_a_sheet_s_id_is_saved_with_its_post_and_the_final_sheet_saves_none(tmp_path):
    """Pro's sheet stands as message 6200 and Alex runs `/attendance sync` for Pro from round 1.
    Once the sync's sheet job is done, and before the deletion after it runs, the division
    records the new sheet as its live one. A sheet that takes no live slot, as the final sheet of
    a season, is saved nowhere: its record leaves the live sheet as it was."""
    from leaguebot.framework.db.database import get_connection

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
    from leaguebot.image.services.aspects.image_attendance_post import (
        ATTENDANCE_ASPECT,
        ATTENDANCE_TEMPLATE_KEY,
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
    assert await league.bot.image_drawing.attendance_wanted(), (
        "the graphic is not drawn in this league"
    )

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


# ---------------------------------------------------------------------------
# A sheet's render fault is named in the log channel as the attendance sheet image
# ---------------------------------------------------------------------------

#: The detail of the template fault the sheet's render below fails on.
SHEET_RENDER_FAULT = "the template declares no `total_points` field"
#: The words the render gives where Inkscape is not installed, which the line keeps.
CONVERTER_ABSENT_WORDS = "Inkscape is not installed on this host."
#: What the sheet's line says puts its picture right where Inkscape is not installed.
CONVERTER_ABSENT_REMEDY = (
    "Inkscape is not installed on the machine running the bot, and the host must install it; "
    "once it has, run `/attendance sync` to post the sheet again"
)


@pytest.mark.parametrize("kind, detail", [
    ("TEMPLATE_INVALID", SHEET_RENDER_FAULT),
    pytest.param("CONVERTER_ABSENT", CONVERTER_ABSENT_WORDS),
], ids=["template fault", "converter absent"])
async def test_a_sheet_s_render_fault_is_named_as_the_attendance_sheet_image(
    tmp_path, kind, detail
):
    """The image module is on with the attendance graphic switched on and its template valid,
    and the image module's render of Pro's sheet fails on a template fault, or for want of
    Inkscape. Alex runs `/attendance sync` for Pro from round 1: the sheet posts once, as text
    with no picture, and the queue runs to the end; the log channel gives the fault on one line
    headed "⚠️ Attendance sheet image", the name `/images config toggle` offers, naming season 3,
    Pro and round 1, and nothing in the log calls it a results image. Where Inkscape is not
    installed, the line keeps the render's words and says that the host must install it, and to
    run `/attendance sync` to post the sheet again once it has."""
    from leaguebot.image.models.image_module import Problem, RenderOutcome
    from leaguebot.image.services.aspects.image_attendance_post import (
        ATTENDANCE_ASPECT,
        ATTENDANCE_TEMPLATE_KEY,
    )

    league = await _scored_league(tmp_path, images=True)
    league.bot.image_config_service.get_toggles = AsyncMock(
        return_value={ATTENDANCE_ASPECT: True}
    )
    league.bot.image_validity_service.template_reports = AsyncMock(
        return_value={ATTENDANCE_TEMPLATE_KEY: MagicMock(valid=True)}
    )
    league.bot.image_render_service.render = AsyncMock(return_value=RenderOutcome(
        problem=Problem(kind=kind, detail=detail, template_key=ATTENDANCE_TEMPLATE_KEY),
    ))

    await _sync_pro(league)
    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    assert _sends(league) == 1
    assert league.channel(SHEET).files[-1] is None
    log = await _said(league)
    [line] = [line for line in log.splitlines() if detail in line]
    named = "⚠️ Attendance sheet image — Season 3 Pro — attendance after round 1"
    assert line.startswith(named), line
    assert "Results image" not in log, log
    if kind == "CONVERTER_ABSENT":
        assert CONVERTER_ABSENT_REMEDY in line, line


#: The detail of the one render notice the sheet's render below comes back with.
SHEET_NOTICE = "no `flag` image for “Lewis”"

#: Why the two tests below fail until #635 is built.
NOT_YET_FRAMED = (
    "#635: a sheet's notices reach the log as a bare block headed by the kind's code, the member "
    "and the command unnamed, and its job keeps nothing of them for the reply"
)


async def _sheet_notice_is_framed_and_kept(league: Any, interaction: Any) -> None:
    """The log channel carries the sheet's one notice once, in a line opening with Alex and the
    command he ran, then "Image render notices — <sheet>:" naming Pro and round 1, and beneath it
    the notice under "Artwork not supplied by the league", naming its field `flag`, with no kind
    code and no template name; the sheet's job keeps the sheet's name and its grouped notices on
    its result, and the sync's reply to Alex's *interaction* says the attendance was recalculated
    and ends in one "Image notices" section listing the sheet under that name, linked to the log
    channel's message holding it, and the notice beneath, once."""
    log = await _said(league)
    assert log.count(SHEET_NOTICE) == 1, log
    [block] = [message for message in league.bot.log_channel.sent if SHEET_NOTICE in message]
    heading, *grouped = unruled(block).splitlines()
    assert heading.count(" | ") >= 2, block
    named, what, drawn = heading.split(" | ", 2)
    assert "<@4242>" in named and "/attendance sync" in what, block
    assert drawn.startswith("Image render notices — "), block
    assert "Pro" in drawn and "round 1" in drawn.lower(), block
    assert grouped == [
        "  Artwork not supplied by the league", f"    • `flag` — {SHEET_NOTICE}",
    ], block
    assert "ASSET_FALLBACK_USED" not in block and "_template" not in block, block
    from leaguebot.attendance.services.sheet.sheet_posting import POST_SHEET
    from leaguebot.framework.models.change import RESULT_DRAWN, RESULT_NOTICES, RESULT_PICTURES

    [kept] = [row["result"] for row in await step_rows(league.db_path)
              if row["name"] == POST_SHEET]
    assert kept[RESULT_PICTURES] == [{
        RESULT_DRAWN: drawn.removeprefix("Image render notices — ").removesuffix(":"),
        RESULT_NOTICES: grouped,
    }], kept
    told = updated_reply(interaction)
    assert told.startswith("✅ Attendance of **Pro** recalculated from round 1 on."), told
    assert told.count("⚠️ **Image notices**") == 1, told
    assert "\n\n⚠️ **Image notices**\n" in told, told
    n = league.bot.log_channel.sent.index(block) + 1
    label = drawn.removeprefix("Image render notices — ").removesuffix(":")
    assert f"  **{label}** ([see them in the log](https://discord.test/{n}))" in told, told
    assert told.count(SHEET_NOTICE) == 1, told


@pytest.mark.xfail(strict=True, reason=NOT_YET_FRAMED)
async def test_a_sheet_s_render_notices_reach_the_log_once_naming_the_round(tmp_path):
    """The image module is on with the attendance graphic switched on and its template valid,
    and the image module's render of Pro's sheet is drawn with one notice, a flag falling back.
    Alex runs `/attendance sync` for Pro from round 1: the sheet posts once, as a picture, and the
    queue runs to the end; the log channel carries the notice once, in a line opening with Alex
    and the command, then a heading naming season 3, Pro and round 1, and the notice grouped
    beneath under its kind in plain words, naming its field; the sheet's job keeps the sheet's
    name and grouped notices, and the sync's reply ends in one "Image notices" section listing the
    sheet with a link to that log message, and the notice beneath (issue #635; Constitution V;
    owner decision 2026-11-24)."""
    from leaguebot.image.models.image_module import RenderNotice, RenderOutcome
    from leaguebot.image.services.aspects.image_attendance_post import (
        ATTENDANCE_ASPECT,
        ATTENDANCE_TEMPLATE_KEY,
    )

    league = await _scored_league(tmp_path, images=True)
    league.bot.image_config_service.get_toggles = AsyncMock(
        return_value={ATTENDANCE_ASPECT: True}
    )
    league.bot.image_validity_service.template_reports = AsyncMock(
        return_value={ATTENDANCE_TEMPLATE_KEY: MagicMock(valid=True)}
    )
    picture = tmp_path / "sheet.png"
    picture.write_bytes(b"\x89PNG")
    league.bot.image_render_service.render = AsyncMock(return_value=RenderOutcome(
        png_paths=[picture], notices=[RenderNotice(
            image_type=ATTENDANCE_TEMPLATE_KEY, notice_kind="ASSET_FALLBACK_USED",
            detail=SHEET_NOTICE, field_id="flag",
        )],
    ))

    interaction = await _sync_pro(league)
    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    assert _sends(league) == 1
    assert league.channel(SHEET).files[-1] is not None
    await _sheet_notice_is_framed_and_kept(league, interaction)
    [block] = [message for message in league.bot.log_channel.sent if SHEET_NOTICE in message]
    assert "Season 3 Pro — attendance after round 1" in block.splitlines()[0], block


@pytest.mark.xfail(strict=True, reason=NOT_YET_FRAMED)
async def test_a_refused_sheet_s_render_notices_reach_the_log_once_when_it_is_retried(tmp_path):
    """The image module is on with the attendance graphic switched on and its template valid,
    and the image module's render of Pro's sheet is drawn with one notice, a flag falling back.
    Pro's attendance channel refuses every send. Alex runs `/attendance sync` for Pro from round 1
    and the queue stops at the sheet; the channel is let accept and a Retry posts the sheet as
    text. The queue runs to the end, and the log channel carries the notice once, in a line opening
    with Alex and the command, then a heading naming Pro and round 1, and the notice grouped
    beneath; the sheet's job keeps the sheet's name and grouped notices across the stop, never
    lost with the refused try, and the sync's reply lists the sheet under "Image notices" with a
    link to that log message and the notice beneath, once (issue #635; image spec, Errors and the
    rejection of input, #550; owner decision 2026-11-24)."""
    from leaguebot.image.models.image_module import RenderNotice, RenderOutcome
    from leaguebot.image.services.aspects.image_attendance_post import (
        ATTENDANCE_ASPECT,
        ATTENDANCE_TEMPLATE_KEY,
    )

    league = await _scored_league(tmp_path, images=True)
    league.bot.image_config_service.get_toggles = AsyncMock(
        return_value={ATTENDANCE_ASPECT: True}
    )
    league.bot.image_validity_service.template_reports = AsyncMock(
        return_value={ATTENDANCE_TEMPLATE_KEY: MagicMock(valid=True)}
    )
    picture = tmp_path / "sheet.png"

    async def _render(*_args: Any, **_kwargs: Any) -> Any:
        picture.write_bytes(b"\x89PNG")
        return RenderOutcome(png_paths=[picture], notices=[RenderNotice(
            image_type=ATTENDANCE_TEMPLATE_KEY, notice_kind="ASSET_FALLBACK_USED",
            detail=SHEET_NOTICE, field_id="flag",
        )])

    league.bot.image_render_service.render = AsyncMock(side_effect=_render)
    league.channel(SHEET).send_fails = http_error(discord.Forbidden, status=403,
                                                  text="Missing Access")

    interaction = await _sync_pro(league)
    await run_queue(league.bot)
    assert await stopped_job(league.db_path) is not None
    league.channel(SHEET).send_fails = None
    await retry_job(league.bot)

    assert await stopped_job(league.db_path) is None
    assert league.channel(SHEET).files[-1] is None
    await _sheet_notice_is_framed_and_kept(league, interaction)


#: What each season sheet's job lines name the change by: the approval's button, or the
#: completion; and the job that posts the sheet.
_SEASON_SHEETS = {
    "opening": ("the ✅ Approve button of `/season placements-review`", "opening_sheet"),
    "final": ("/season complete", "final_sheet"),
}


@pytest.mark.parametrize("occasion", ["opening", "final"])
async def test_a_season_sheet_s_notices_open_with_member_and_change_and_are_kept_across_a_stop(
    tmp_path, monkeypatch, occasion,
):
    """Images and attendance on, the attendance graphic asked for, and every sheet drawn as a PNG
    with one font substituted, a font of its own. Season 3 is approved (each division's opening
    sheet) or completed (each division's final sheet), and Pro's attendance channel refuses the
    first send: the queue stops at Pro's sheet. The channel is let accept and a Retry posts Pro's
    sheet as text, and Am's sheet is drawn and posted. Each sheet's notice reaches the log channel
    once, in a line opening with the member and the change ("| the ✅ Approve button of `/season
    placements-review` |" or "| /season complete |"), then "Image render notices — <sheet>:", and
    beneath it the notice grouped under "Font substituted", naming its field. Each division's job
    keeps its sheet's name and its grouped notices on its result, Pro's across the stop, for the
    approval's or the completion's reply to list (issue #635; Constitution V; image spec, Errors
    and the rejection of input, #550)."""
    from leaguebot.image.models.image_module import RenderNotice
    from leaguebot.image.services.aspects import image_attendance_post
    from leaguebot.image.services.aspects.image_attendance_post import SheetRender
    from tests.support.season_league import (
        complete_season,
        pending_completion_league,
        press_approve,
        season_league,
    )

    asked, job = _SEASON_SHEETS[occasion]
    if occasion == "opening":
        league = await season_league(
            tmp_path, monkeypatch=monkeypatch, attendance=True, images=True,
        )
    else:
        league = await pending_completion_league(tmp_path, attendance=True, images=True)
    monkeypatch.setattr(league.bot.image_drawing, "attendance_wanted",
                        AsyncMock(return_value=True))
    fonts: list[str] = []

    async def _sheet(*_args: Any, **_kwargs: Any) -> Any:
        fonts.append(f"Font{len(fonts) + 1}")
        png = tmp_path / f"sheet_{len(fonts)}.png"
        png.write_bytes(b"\x89PNG\r\n\x1a\n")
        return SheetRender(png=png, notices=[RenderNotice(
            image_type="attendance_sheet", notice_kind="FONT_SUBSTITUTED",
            field_id="driver_name_1",
            detail=f"{fonts[-1]} was unavailable, so a substitute was drawn",
        )])

    monkeypatch.setattr(image_attendance_post, "render_sheet", _sheet)
    pro_sheet = league.channel(DIVISIONS[PRO][3].attendance)
    pro_sheet.send_fails = http_error(status=500, text="Internal Server Error")
    if occasion == "opening":
        await press_approve(league)
    else:
        await complete_season(league)

    await run_queue(league.bot)
    assert await stopped_job(league.db_path) is not None
    pro_sheet.send_fails = None
    await retry_job(league.bot)

    assert await stopped_job(league.db_path) is None
    assert len(fonts) == 2, fonts
    log = await _said(league)
    told = []
    for font in fonts:
        assert log.count(font + " ") == 1, log
        [message] = [line for line in league.bot.log_channel.sent if font + " " in line]
        heading, *grouped = unruled(message).splitlines()
        assert heading.count(" | ") >= 2, message
        named, what, drawn = heading.split(" | ", 2)
        assert "<@77>" in named and what == asked, message
        label = drawn.removeprefix("Image render notices — ").removesuffix(":")
        assert drawn == f"Image render notices — {label}:", message
        assert grouped == [
            "  Font substituted",
            f"    • `driver_name_1` — {font} was unavailable, so a substitute was drawn",
        ], message
        told.append((label, grouped))
    from leaguebot.framework.models.change import RESULT_DRAWN, RESULT_NOTICES, RESULT_PICTURES

    kept = [row["result"] for row in await step_rows(league.db_path) if row["name"] == job]
    assert len(kept) == 2, kept
    pictures = [each for result in kept for each in result[RESULT_PICTURES]]
    assert sorted(pictures, key=str) == sorted(
        ({RESULT_DRAWN: label, RESULT_NOTICES: grouped} for label, grouped in told), key=str,
    ), pictures


async def test_a_sheet_whose_images_switch_nobody_signed_up_for_stops_its_job(tmp_path):
    """The image module is on with the attendance graphic switched on, but the images module's
    switch reader was never signed up (`SwitchNotSignedUp`), a fault in the bot's own wiring.
    Alex runs `/attendance sync` for Pro from round 1: the fault reaches the sheet's job, which
    stops; the sheet is not posted as text with a line saying its picture could not be drawn."""
    from leaguebot.framework.services.switches.module_service import SwitchNotSignedUp

    league = await _scored_league(tmp_path, images=True)
    answer_switch(league.bot.module_service, "images", SwitchNotSignedUp("images"))

    await _sync_pro(league)
    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is not None
    assert _sends(league) == 0
    log = await _said(league)
    assert "Attendance sheet image" not in log and "could not be drawn" not in log, log
