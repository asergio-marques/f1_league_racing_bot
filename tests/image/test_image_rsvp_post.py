"""The check-in call's attachment path (041, US3).

Covers FR-049–FR-055 and the contract in
``specs/041-attendance-image-generation/contracts/attendance-posting.md`` Part 2.

**The call-graph test in this file is the strongest guard the module has on XIV.17.** The rule
says plainly that nothing can detect a stale static graphic: a picture drawn once, riding a
message edited in place, simply goes quietly wrong and reports nothing. What *can* be asserted
is that no code path reachable from a button press can redraw it — so a future session adding a
redraw must add an import to a module that has none, which review will see.

Since #439 (slice 6b) the call is posted by its own job on the change queue
(`attendance.check_in.call`), so the call's own cases drive it there: Pro's round 4 of
`ongoing_league`, three days off with no call posted, asked of the queue as its timer asks it,
the check-in graphic stood in for by a double of `try_attach`.
"""
from __future__ import annotations

import inspect
from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from leaguebot.attendance.services import rsvp_service
from leaguebot.attendance.services.attendance_service import AttendanceService
from leaguebot.image.services import image_rsvp_post
from leaguebot.image.services.image_rsvp_post import rsvp_enabled, try_attach
from tests.support.change_queue import attach_queue, http_error, run_queue, stopped_job
from tests.support.season_league import (
    CALL_MESSAGES,
    DIVISIONS,
    PRO,
    SeasonLeague,
    ongoing_league,
    round_id,
)

CHECK_IN = DIVISIONS[PRO][3].checkin
ROUND_4 = round_id(PRO, 4)
ROUND_3 = round_id(PRO, 3)

NO_CALL_CHANGE = (
    "#439: the check-in call is posted by `run_rsvp_notice` at once, off the change queue, "
    "rather than by its own job"
)

#: Every image module the check-in graphic could be redrawn through.
_IMAGE_MODULES = ("image_rsvp_post", "image_rsvp_service", "image_render_service")

#: The functions reached when a driver presses a button and when the reserves are distributed.
#: Each edits the embed **in place**; the attachment must ride through every one of them
#: untouched. The deadline's own jobs, which lock the call and announce the placements, are held
#: by `test_the_deadline_s_jobs_never_redraw_the_graphic` below.
_EDIT_IN_PLACE = (
    "run_reserve_distribution",
    "_rebuild_embed_for_round",
)


def _bot(*, module_on=True, aspect_on=True, template_valid=True):
    bot = MagicMock()
    bot.module_service.is_images_enabled = AsyncMock(return_value=module_on)
    bot.image_config_service.get_toggles = AsyncMock(return_value={"rsvp": aspect_on})
    report = MagicMock()
    report.valid = template_valid
    bot.image_validity_service.template_reports = AsyncMock(
        return_value={"rsvp_template": report}
    )
    return bot


# ── Enablement ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_graphic_draws_when_module_aspect_and_template_are_all_sound():
    assert await rsvp_enabled(_bot()) is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs",
    [{"module_on": False}, {"aspect_on": False}, {"template_valid": False}],
)
async def test_the_call_posts_without_an_attachment_when_anything_is_off(kwargs):
    assert await rsvp_enabled(_bot(**kwargs)) is False


@pytest.mark.asyncio
async def test_a_reader_that_raises_never_breaks_the_call():
    bot = _bot()
    bot.image_config_service.get_toggles = AsyncMock(side_effect=RuntimeError("boom"))
    assert await rsvp_enabled(bot) is False


@pytest.mark.asyncio
async def test_a_render_that_raises_yields_no_attachment_rather_than_an_error():
    """FR-055: the graphic gates neither the call nor the round's attendance rows."""
    bot = MagicMock()
    bot.module_service.is_images_enabled = AsyncMock(side_effect=RuntimeError("boom"))

    result = await try_attach(
        bot,
        division_name="Division 1",
        round_number=1,
        round_format="NORMAL",
        scheduled_at=None,
        track_name="Silverstone Circuit",
    )
    assert result is None


@pytest.mark.asyncio
async def test_no_bot_yields_no_attachment():
    assert (
        await try_attach(
            None, division_name="D", round_number=1, round_format="NORMAL",
            scheduled_at=None, track_name=None,
        )
        is None
    )


# ── The static call graph (XIV.17) ────────────────────────────────────────


def _imports_of(function_name: str) -> set[str]:
    """Every module imported anywhere inside *function_name*, read from its bytecode.

    Bytecode rather than source text: a docstring explaining *why* this function must not
    reach the image module would trip a text match, and the source of a nested function does
    not dedent cleanly for the parser either.
    """
    import dis
    import types

    found: set[str] = set()

    def walk(code) -> None:
        for instruction in dis.get_instructions(code):
            if instruction.opname == "IMPORT_NAME" and instruction.argval:
                found.add(str(instruction.argval))
            elif instruction.opname == "IMPORT_FROM" and instruction.argval:
                found.add(str(instruction.argval))
        for const in code.co_consts:
            if isinstance(const, types.CodeType):
                walk(const)

    walk(getattr(rsvp_service, function_name).__code__)
    return found


@pytest.mark.parametrize("function_name", _EDIT_IN_PLACE)
def test_no_path_that_edits_the_embed_can_reach_the_image_module(function_name):
    """The heart of XIV.17: nothing that edits the embed may redraw the picture."""
    if not hasattr(rsvp_service, function_name):
        pytest.skip(f"{function_name} is not defined in rsvp_service")

    imported = _imports_of(function_name)
    for image_module in _IMAGE_MODULES:
        assert not any(image_module in name for name in imported), (
            f"{function_name} imports {image_module}: a static graphic must never be "
            f"redrawn while its call stands (XIV.17)"
        )


def test_the_button_view_reaches_no_image_module():
    source = inspect.getsource(rsvp_service.RsvpView)
    for image_module in _IMAGE_MODULES:
        assert image_module not in source


# ── The call's own job (#439, slice 6b) ───────────────────────────────────


async def _call_league(tmp_path: Any, *, images: bool = True) -> SeasonLeague:
    """Pro's round 4 three days off, its call due and no call posted, the queue attached."""
    league = await ongoing_league(tmp_path, attendance=True, images=images)
    league.bot.attendance_service = AttendanceService(league.db_path)
    attach_queue(league.bot, league.db_path, now=league.clock)
    when = league.clock.now + timedelta(days=3)
    await league.write("UPDATE rounds SET scheduled_at = ? WHERE id = ?",
                       when.replace(tzinfo=None).isoformat(), ROUND_4)
    return league


def _drawing(league: SeasonLeague, tmp_path: Any, monkeypatch: Any) -> AsyncMock:
    """Stand in for the check-in graphic: each draw is recorded in the league's events and gives
    a picture to attach."""
    picture = tmp_path / "checkin.png"

    async def _attach(*_args: Any, **_kwargs: Any) -> discord.File:
        league.events.append(("draw", CHECK_IN, 0))
        picture.write_bytes(b"png")
        return discord.File(str(picture), filename="checkin.png")

    drawing = AsyncMock(side_effect=_attach)
    monkeypatch.setattr(image_rsvp_post, "try_attach", drawing)
    return drawing


async def _post_the_call(league: SeasonLeague) -> list[int]:
    """Ask the queue for round 4's call as its timer does and run it; the ids sent in Pro's
    check-in channel."""
    from leaguebot.attendance.services.check_in_changes import ask_check_in_call
    from leaguebot.core.models.change import ChangeOrigin

    await ask_check_in_call(league.bot.change_queue, league.db_path, ROUND_4,
                            origin=ChangeOrigin.BOT)
    await run_queue(league.bot)
    return [mid for kind, cid, mid in league.events if kind == "send" and cid == CHECK_IN]


async def _rows_opened(league: SeasonLeague) -> int:
    return len(await league.rows(
        "SELECT 1 FROM driver_round_attendance WHERE round_id = ?", ROUND_4))


async def test_the_generation_has_exactly_one_call_site_in_the_whole_module(
    tmp_path, monkeypatch,
):
    """The call's job draws the check-in graphic once, and the call it posts carries it."""
    league = await _call_league(tmp_path)
    drawing = _drawing(league, tmp_path, monkeypatch)

    [_posted] = await _post_the_call(league)

    assert drawing.await_count == 1
    assert league.channel(CHECK_IN).files[-1] is not None


async def test_the_one_call_site_is_the_initial_post(tmp_path, monkeypatch):
    """The graphic is drawn before the call is sent, and the send is what posts the call."""
    league = await _call_league(tmp_path)
    _drawing(league, tmp_path, monkeypatch)

    [posted] = await _post_the_call(league)

    assert league.events.index(("draw", CHECK_IN, 0)) < league.events.index(
        ("send", CHECK_IN, posted))


async def test_the_call_is_never_deleted_and_reposted_while_it_stands(tmp_path, monkeypatch):
    """Reposting would orphan the view re-armed against the stored message id: once posted, the
    call stands, nothing deleted in the check-in channel and nothing posted after it."""
    league = await _call_league(tmp_path)
    _drawing(league, tmp_path, monkeypatch)

    [posted] = await _post_the_call(league)

    assert posted in league.channel(CHECK_IN).messages
    assert [event for event in league.events
            if event[0] == "delete" and event[1] == CHECK_IN] == []


async def test_the_attachment_helper_never_raises(tmp_path, monkeypatch):
    """FR-055: a drawing that raises gates neither the call nor the round's attendance rows: the
    call is posted without a picture, its rows opened, and the queue does not stop."""
    league = await _call_league(tmp_path)
    monkeypatch.setattr(image_rsvp_post, "try_attach",
                        AsyncMock(side_effect=RuntimeError("boom")))

    [_posted] = await _post_the_call(league)

    assert league.channel(CHECK_IN).files[-1] is None
    assert await _rows_opened(league) > 0
    assert await stopped_job(league.db_path) is None


# ── The toggle changes nothing else (FR-052) ──────────────────────────────


@pytest.mark.xfail(strict=True, reason=NO_CALL_CHANGE)
async def test_the_embed_and_the_view_are_built_before_and_regardless_of_the_graphic(
    tmp_path, monkeypatch,
):
    """With the graphic drawn or images off, the call is composed identically (FR-052): the same
    embed, and its buttons, either way."""
    (tmp_path / "drawn").mkdir()
    (tmp_path / "plain").mkdir()
    drawn = await _call_league(tmp_path / "drawn")
    _drawing(drawn, tmp_path, monkeypatch)
    await _post_the_call(drawn)
    plain = await _call_league(tmp_path / "plain", images=False)
    await _post_the_call(plain)

    sends = [league.channel(CHECK_IN).send.await_args_list[-1].kwargs for league in (drawn, plain)]
    assert sends[0]["embed"].to_dict() == sends[1]["embed"].to_dict()
    assert all(sent.get("view") is not None for sent in sends)
    assert drawn.channel(CHECK_IN).files[-1] is not None
    assert plain.channel(CHECK_IN).files[-1] is None


@pytest.mark.parametrize("images", [True, False], ids=["graphic drawn", "images off"])
async def test_the_attendance_rows_are_opened_after_the_post_either_way(
    tmp_path, monkeypatch, images,
):
    """FR-055: the graphic must not extend the post-then-open dependency. Discord refuses the
    call: no rows are opened. Once it takes the call, its rows are."""
    league = await _call_league(tmp_path, images=images)
    if images:
        _drawing(league, tmp_path, monkeypatch)
    league.channel(CHECK_IN).send_fails = http_error(discord.Forbidden, status=403,
                                                     text="Missing Permissions")

    assert await _post_the_call(league) == []
    assert await _rows_opened(league) == 0

    league.channel(CHECK_IN).send_fails = None
    league.clock.advance(minutes=1)
    await run_queue(league.bot)

    assert await _rows_opened(league) > 0


# ── The deadline's jobs (#439, slice 6b) ───────────────────────────────────

NO_DEADLINE_CHANGE = (
    "#439: the check-in deadline is run by `run_rsvp_deadline` at once, off the change queue, "
    "rather than by its own jobs"
)


async def _deadline_league(tmp_path: Any, monkeypatch: Any) -> tuple[SeasonLeague, AsyncMock]:
    """Pro's round 3 an hour off, so its two-hour check-in deadline passed an hour ago, its call
    (7001) standing in Pro's check-in channel and nothing announced; the check-in graphic stood
    in for by a double that records each draw."""
    league = await ongoing_league(tmp_path, attendance=True, images=True)
    league.bot.attendance_service = AttendanceService(league.db_path)
    attach_queue(league.bot, league.db_path, now=league.clock)
    when = league.clock.now + timedelta(hours=1)
    await league.write("UPDATE rounds SET scheduled_at = ? WHERE id = ?",
                       when.replace(tzinfo=None).isoformat(), ROUND_3)
    del league.channel(CHECK_IN).messages[CALL_MESSAGES[2]]
    await league.write("UPDATE rsvp_embed_messages SET distribution_msg_id = NULL "
                       "WHERE round_id = ?", ROUND_3)
    return league, _drawing(league, tmp_path, monkeypatch)


async def _run_the_deadline(league: SeasonLeague) -> None:
    from leaguebot.attendance.services.check_in_changes import ask_check_in_deadline
    from leaguebot.core.models.change import ChangeOrigin

    await ask_check_in_deadline(league.bot.change_queue, league.db_path, ROUND_3,
                                origin=ChangeOrigin.BOT)
    await run_queue(league.bot)


@pytest.mark.xfail(strict=True, reason=NO_DEADLINE_CHANGE)
async def test_the_deadline_s_jobs_never_redraw_the_graphic(tmp_path, monkeypatch):
    """XIV.17: the deadline locks the call by editing it in place. Its jobs draw nothing, and
    the edit that takes the buttons off carries no picture of its own, so the graphic the call
    was posted with rides through untouched."""
    league, drawing = await _deadline_league(tmp_path, monkeypatch)

    await _run_the_deadline(league)

    assert drawing.await_count == 0
    call = league.channel(CHECK_IN).messages[CALL_MESSAGES[0]]
    call.edit.assert_awaited()
    for name in ("file", "files", "attachments"):
        assert name not in call.edit.await_args.kwargs


@pytest.mark.xfail(strict=True, reason=NO_DEADLINE_CHANGE)
async def test_the_notices_that_stay_text_carry_no_graphic(tmp_path, monkeypatch):  # FR-053
    """The deadline's announcement, or its notice that no reserves were placed, is text: posted
    with no file, and nothing drawn for it."""
    league, drawing = await _deadline_league(tmp_path, monkeypatch)

    await _run_the_deadline(league)

    sent = [mid for kind, cid, mid in league.events if kind == "send" and cid == CHECK_IN]
    assert len(sent) == 1
    assert league.channel(CHECK_IN).files[-1] is None
    assert drawing.await_count == 0
