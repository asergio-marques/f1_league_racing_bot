"""A check-in call whose graphic falls back is told as a line of its job (architecture.md, "Errors
and failures", the sixth place: the drawing of a graphic riding on a post).

The league is `ongoing_league`'s with attendance and images on: Pro's round 4 is three days off,
past the moment its call falls due and before its deadline, with no call posted for it yet. The
call is asked of the queue as its timer asks it, and the queue is run. Where the graphic cannot be
drawn, or is drawn without the deadline, the call still posts and is recorded, and the log channel
is told what was left out, naming the season, the division and the round; the full error stays in
the host's log.
"""
from __future__ import annotations

from datetime import timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from tests.support.change_queue import http_error, retry_job, run_queue, stopped_job
from tests.support.season_league import (
    DIVISIONS,
    PRO,
    SeasonLeague,
    attendance_from_the_database,
    ongoing_league,
    round_id,
)
from tests.support.switches import answer_switch

CHECK_IN = DIVISIONS[PRO][3].checkin
ROUND_4 = round_id(PRO, 4)
#: What the drawing below raises: a fault in the bot, whose words never reach the log channel.
RAISED = "template unreadable"
#: The clause the log channel is given for a graphic that could not be drawn at all: the fault in
#: the bot named by its kind alone.
NOT_DRAWN = (
    "the graphic could not be drawn, so the call is posted without it: "
    "the bot hit an internal fault"
)
#: The clause the log channel is given for a graphic drawn without its deadline.
NO_DEADLINE = "the deadline could not be read, so the check-in graphic is drawn without it"


async def _league(tmp_path: Any) -> SeasonLeague:
    league = await ongoing_league(tmp_path, attendance=True, images=True)
    attendance_from_the_database(league)
    when = league.clock.now + timedelta(days=3)
    await league.write("UPDATE rounds SET scheduled_at = ? WHERE id = ?",
                       when.astimezone(timezone.utc).isoformat(), ROUND_4)
    return league


async def _ask_and_run(league: SeasonLeague) -> None:
    from leaguebot.attendance.services.check_in.check_in_call import ask_check_in_call
    from leaguebot.framework.models.change import ChangeOrigin

    await ask_check_in_call(league.bot.change_queue, league.db_path, ROUND_4,
                            origin=ChangeOrigin.BOT)
    await run_queue(league.bot)


def _sent(league: SeasonLeague) -> list[int]:
    return [mid for kind, cid, mid in league.events if kind == "send" and cid == CHECK_IN]


@pytest.mark.parametrize("fault", [
    "not drawn",
    "no deadline",
    "none",
])
async def test_a_call_whose_graphic_cannot_be_drawn_is_told_as_a_line_of_its_job(
    tmp_path, monkeypatch, fault
):
    """(not drawn) the drawing raises, a fault in the bot: the call posts without a picture and
    the log channel is told so, naming the fault by its kind alone and never in the words it was
    raised with. (no deadline) the configuration read for the deadline raises: the graphic is drawn
    without it, the call posts with it, and the log channel is told so. (none) the graphic is drawn
    whole: no such line is written. Each line written names season 3, Pro and round 4."""
    from leaguebot.attendance.services.check_in.attendance_config_service import AttendanceService

    league = await _league(tmp_path)
    picture = tmp_path / "checkin.png"

    async def _attach(*_args: Any, **_kwargs: Any) -> tuple[discord.File | None, tuple[str, ...]]:
        if fault == "not drawn":
            raise RuntimeError(RAISED)
        picture.write_bytes(b"png")
        return discord.File(str(picture), filename="checkin.png"), ()

    monkeypatch.setattr(
        league.bot.image_drawing, "attach_check_in_call", AsyncMock(side_effect=_attach)
    )
    if fault == "no deadline":
        monkeypatch.setattr(AttendanceService, "get_config",
                            AsyncMock(side_effect=RuntimeError("config unreadable")))

    await _ask_and_run(league)

    assert await stopped_job(league.db_path) is None
    assert len(_sent(league)) == 1
    assert (league.channel(CHECK_IN).files[-1] is None) == (fault == "not drawn")
    [record] = await league.rows("SELECT * FROM rsvp_embed_messages WHERE round_id = ?", ROUND_4)
    assert record["message_id"] == str(_sent(league)[0])
    lines = [line for line in league.log().splitlines() if "check-in call of round 4:" in line]
    expected = {"not drawn": [NOT_DRAWN], "no deadline": [NO_DEADLINE], "none": []}[fault]
    # Each line names the season, the division and the round.
    heading = f"Season 3 **{DIVISIONS[PRO][0]}** — check-in call of round 4: "
    assert lines == [heading + clause for clause in expected]
    assert RAISED not in league.log()


# ---------------------------------------------------------------------------
# What the render itself reports reaches the log channel once, under the check-in call's name
# ---------------------------------------------------------------------------

#: The detail of the one render notice the drawing below comes back with.
NOTICE_DETAIL = "no `flag` image for “Silverstone”"
#: The detail of the fault the drawing below fails on.
RENDER_FAULT = "the template declares no `round_number` field"
#: The words the render gives where Inkscape is not installed, which the line keeps.
CONVERTER_ABSENT_WORDS = "Inkscape is not installed on this host."
#: What the check-in call's line says puts its picture right where Inkscape is not installed.
CONVERTER_ABSENT_REMEDY = (
    "Inkscape is not installed on the machine running the bot, and the host must install it; "
    "once it has, the next check-in call is posted with its picture"
)


def _draw_with(league: SeasonLeague, tmp_path: Any, outcome: str) -> None:
    """Image generation on for the check-in call, its template valid; its render, the image
    module's own, comes back drawn with one notice (*outcome* "notice"), failed on a template
    fault (*outcome* "fault") or failed for want of Inkscape (*outcome* "converter absent")."""
    from leaguebot.image.models.image_module import Problem, RenderNotice, RenderOutcome

    league.bot.image_config_service.get_toggles = AsyncMock(return_value={"rsvp": True})
    league.bot.image_validity_service.template_reports = AsyncMock(
        return_value={"rsvp_template": MagicMock(valid=True)}
    )
    picture = tmp_path / "checkin.png"
    picture.write_bytes(b"\x89PNG")
    if outcome == "notice":
        drawn = RenderOutcome(png_paths=[picture], notices=[RenderNotice(
            image_type="rsvp_template", notice_kind="ASSET_FALLBACK_USED",
            detail=NOTICE_DETAIL, field_id="flag",
        )])
    elif outcome == "converter absent":
        drawn = RenderOutcome(problem=Problem(
            kind="CONVERTER_ABSENT", detail=CONVERTER_ABSENT_WORDS, template_key="rsvp_template",
        ))
    else:
        drawn = RenderOutcome(problem=Problem(
            kind="TEMPLATE_INVALID", detail=RENDER_FAULT, template_key="rsvp_template",
        ))
    league.bot.image_render_service.render = AsyncMock(return_value=drawn)


async def test_a_check_in_call_s_render_notices_reach_the_log_once_naming_the_round(tmp_path):
    """Pro's round 4's check-in call is drawn, its flag falling back (one render notice). The
    call posts with its picture, and the log channel carries the notice once, in a block whose
    heading names round 4, season 3 and Pro; no block without a heading carries it a second
    time."""
    league = await _league(tmp_path)
    _draw_with(league, tmp_path, "notice")

    await _ask_and_run(league)

    assert await stopped_job(league.db_path) is None
    assert league.channel(CHECK_IN).files[-1] is not None
    log = league.log()
    assert log.count(NOTICE_DETAIL) == 1, log
    [block] = [message for message in league.bot.log_channel.sent if NOTICE_DETAIL in message]
    heading = block.splitlines()[0]
    assert f"check-in call of round 4 in Season 3 **{DIVISIONS[PRO][0]}**:" in heading, block


@pytest.mark.parametrize("outcome", [
    "fault",
    pytest.param("converter absent", marks=pytest.mark.xfail(
        strict=True,
        reason="#584: the check-in call's line says nothing of what puts the converter's absence "
        "right",
    )),
])
async def test_a_check_in_call_s_render_fault_is_named_as_the_check_in_call_image(
    tmp_path, outcome
):
    """Pro's round 4's check-in call is drawn, and its render fails on a template fault ("fault")
    or for want of Inkscape ("converter absent"). The call posts without a picture, and the log
    channel gives the fault on one line headed "⚠️ Check-in call image", the name `/images config
    toggle` offers, naming round 4, season 3 and Pro; nothing in the log calls it a results image.
    Where Inkscape is not installed, the line keeps the render's words and says that the host must
    install it, and that the next check-in call is posted with its picture once it has."""
    league = await _league(tmp_path)
    _draw_with(league, tmp_path, outcome)

    await _ask_and_run(league)

    assert await stopped_job(league.db_path) is None
    assert league.channel(CHECK_IN).files[-1] is None
    log = league.log()
    detail = RENDER_FAULT if outcome == "fault" else CONVERTER_ABSENT_WORDS
    [line] = [line for line in log.splitlines() if detail in line]
    named = (
        f"⚠️ Check-in call image — check-in call of round 4 in Season 3 **{DIVISIONS[PRO][0]}**: "
    )
    assert line.startswith(named), line
    assert "Results image" not in log, log
    if outcome == "converter absent":
        assert CONVERTER_ABSENT_REMEDY in line, line


@pytest.mark.parametrize("outcome", ["notice", "fault"])
async def test_a_refused_check_in_call_s_picture_line_reaches_the_log_once_when_it_is_retried(
    tmp_path, outcome
):
    """Pro's round 4's check-in call is drawn with one render notice (*outcome* "notice") or its
    render fails on a template fault ("fault"), and Discord refuses the post: the queue stops at
    the call. Discord then accepts, Retry is pressed and the call goes out as text. The notice, or
    the fault's "⚠️ Check-in call image" line, reaches the log channel once, as it did when the
    call was posted at the first try: never lost with the refused post, and never twice."""
    league = await _league(tmp_path)
    _draw_with(league, tmp_path, outcome)
    league.channel(CHECK_IN).send_fails = http_error(status=500, text="Internal Server Error")

    await _ask_and_run(league)
    assert await stopped_job(league.db_path) is not None

    league.channel(CHECK_IN).send_fails = None
    await retry_job(league.bot)

    assert await stopped_job(league.db_path) is None
    assert len(_sent(league)) == 1
    assert league.channel(CHECK_IN).files[-1] is None
    detail = NOTICE_DETAIL if outcome == "notice" else RENDER_FAULT
    log = league.log()
    assert log.count(detail) == 1, log
    if outcome == "fault":
        [line] = [line for line in log.splitlines() if RENDER_FAULT in line]
        assert line.startswith("⚠️ Check-in call image — "), line


async def test_a_check_in_call_whose_images_switch_nobody_signed_up_for_stops_its_job(tmp_path):
    """Pro's round 4's check-in call is due with image generation asked for, but the images
    module's switch reader was never signed up (`SwitchNotSignedUp`), a fault in the bot's own
    wiring. The fault reaches the call's job, which stops; the call is not posted as text with a
    "the graphic could not be drawn" line, as a picture that merely would not draw is."""
    from leaguebot.framework.services.switches.module_service import SwitchNotSignedUp

    league = await _league(tmp_path)
    _draw_with(league, tmp_path, "notice")
    answer_switch(league.bot.module_service, "images", SwitchNotSignedUp("images"))

    await _ask_and_run(league)

    assert await stopped_job(league.db_path) is not None
    assert _sent(league) == []
    assert "could not be drawn" not in league.log(), league.log()
