"""The attendance sheet's posting path (041, US2).

Covers FR-043 and FR-046–FR-048, and the contract in
``specs/041-attendance-image-generation/contracts/attendance-posting.md``.

The two tests that matter most are the last two. **A graphic must never gate a sanction** and
must never gate the posting it rides on (Constitution XIV.7, v4.6.0): the natural way to write
a posting is to build the message and send it whole, which quietly makes a rasteriser the gate
on a league's autoreserve and autosack enforcement.
"""
from __future__ import annotations

import inspect
import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from services import attendance_service  # noqa: E402
from services.image_attendance_post import (  # noqa: E402
    SheetRender,
    attendance_enabled,
)


def _bot(*, module_on=True, aspect_on=True, template_valid=True):
    bot = MagicMock()
    bot.module_service.is_images_enabled = AsyncMock(return_value=module_on)
    bot.image_config_service.get_toggles = AsyncMock(
        return_value={"attendance": aspect_on}
    )
    report = MagicMock()
    report.valid = template_valid
    bot.image_validity_service.template_reports = AsyncMock(
        return_value={"attendance_template": report}
    )
    return bot


# ── Enablement ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_graphic_draws_when_module_aspect_and_template_are_all_sound():
    assert await attendance_enabled(_bot(), 1) is True


@pytest.mark.asyncio
async def test_the_module_being_off_falls_back_to_text():
    assert await attendance_enabled(_bot(module_on=False), 1) is False


@pytest.mark.asyncio
async def test_the_aspect_being_off_falls_back_to_text():
    assert await attendance_enabled(_bot(aspect_on=False), 1) is False


@pytest.mark.asyncio
async def test_an_invalid_template_falls_back_to_text():
    assert await attendance_enabled(_bot(template_valid=False), 1) is False


@pytest.mark.asyncio
async def test_a_reader_that_raises_never_breaks_the_posting():
    bot = _bot()
    bot.image_config_service.get_toggles = AsyncMock(side_effect=RuntimeError("boom"))
    assert await attendance_enabled(bot, 1) is False


# ── The render outcome ────────────────────────────────────────────────────


def test_a_render_with_no_png_does_not_draw():
    assert SheetRender().draws is False
    assert SheetRender(problem="template is missing a field").draws is False


def test_a_render_with_a_png_draws():
    assert SheetRender(png="x.png").draws is True


# ── The posting lifecycle it inherits ─────────────────────────────────────


def test_the_image_path_has_no_send_site_of_its_own():
    """FR-045: the ordering belongs to ``post_attendance_sheet`` and is inherited.

    A second send here would be a second implementation of produce-before-destroy, and the
    half left deleting first would be the fallback — the path reached because something has
    already gone wrong.
    """
    import services.image_attendance_post as post

    source = inspect.getsource(post)
    assert "channel.send" not in source
    assert ".delete()" not in source


def test_the_sheet_flow_still_has_exactly_one_send_site_and_one_delete_site():
    source = inspect.getsource(attendance_service.post_attendance_sheet)
    assert source.count("channel.send(") == 2  # the file branch and the text branch
    assert source.count(".delete()") == 1


def test_the_graphic_is_rendered_before_anything_is_sent_or_destroyed():
    source = inspect.getsource(attendance_service.post_attendance_sheet)
    render_at = source.index("_sheet_attachment(")
    send_at = source.index("channel.send(")
    delete_at = source.index(".delete()")
    assert render_at < send_at < delete_at


# ── The graphic adds no precondition (XIV.7) ──────────────────────────────


def test_the_attachment_helper_swallows_every_failure():
    """Nothing raised while drawing may reach the posting, still less the sanctions."""
    source = inspect.getsource(attendance_service._sheet_attachment)
    assert "except Exception" in source
    assert "return None" in source


@pytest.mark.asyncio
async def test_a_render_that_raises_yields_no_attachment_rather_than_an_error():
    guild = MagicMock()
    guild.id = 1
    bot = MagicMock()
    bot.module_service.is_images_enabled = AsyncMock(side_effect=RuntimeError("boom"))

    result = await attendance_service._sheet_attachment(
        bot,
        guild,
        "unused.db",
        round_id=1,
        division_id=7,
        sorted_drivers=[],
        cfg_row=None,
        sanctioned_profile_ids=None,
    )
    assert result is None


@pytest.mark.asyncio
async def test_no_bot_and_no_guild_yield_no_attachment():
    assert (
        await attendance_service._sheet_attachment(
            None, None, "unused.db", round_id=1, division_id=7,
            sorted_drivers=[], cfg_row=None, sanctioned_profile_ids=None,
        )
        is None
    )


def test_the_sanctions_are_enforced_by_a_flow_the_graphic_cannot_reach():
    """``enforce_attendance_sanctions`` imports no image module and calls none.

    XIV.7's precondition clause: the enforcement must complete exactly as it would with the
    images module disabled, and a render that fails must find that work already done.
    """
    source = inspect.getsource(attendance_service.enforce_attendance_sanctions)
    for forbidden in ("image_attendance_post", "image_attendance_service", "render_sheet"):
        assert forbidden not in source


def test_the_drawing_helper_contains_no_raise_at_all():
    """Read from the bytecode: the docstring says "raised", which a text match would catch.

    A graphic that could raise would be a graphic that can stop a sheet posting, and through
    it a sanction being announced.
    """
    import dis

    opcodes = {
        instruction.opname
        for instruction in dis.get_instructions(attendance_service._sheet_attachment)
    }
    assert "RAISE_VARARGS" not in opcodes
