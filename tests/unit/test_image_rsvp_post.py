"""The check-in call's attachment path (041, US3).

Covers FR-049–FR-055 and the contract in
``specs/041-attendance-image-generation/contracts/attendance-posting.md`` Part 2.

**The call-graph test in this file is the strongest guard the module has on XIV.17.** The rule
says plainly that nothing can detect a stale static graphic: a picture drawn once, riding a
message edited in place, simply goes quietly wrong and reports nothing. What *can* be asserted
is that no code path reachable from a button press can redraw it — so a future session adding a
redraw must add an import to a module that has none, which review will see.
"""
from __future__ import annotations

import ast
import inspect
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from services import rsvp_service  # noqa: E402
from services.image_rsvp_post import rsvp_enabled, try_attach  # noqa: E402

_SRC = Path(__file__).resolve().parents[2] / "src"

#: Every image module the check-in graphic could be redrawn through.
_IMAGE_MODULES = ("image_rsvp_post", "image_rsvp_service", "image_render_service")

#: The functions reached when a driver presses a button, when the reserves are distributed, and
#: when the deadline closes the call. Each edits the embed **in place**; the attachment must
#: ride through every one of them untouched.
_EDIT_IN_PLACE = (
    "run_reserve_distribution",
    "run_rsvp_deadline",
    "_rebuild_embed_for_round",
    "_post_distribution_announcement",
    "_post_no_reserve_notice",
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
    assert await rsvp_enabled(_bot(), 1) is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs",
    [{"module_on": False}, {"aspect_on": False}, {"template_valid": False}],
)
async def test_the_call_posts_without_an_attachment_when_anything_is_off(kwargs):
    assert await rsvp_enabled(_bot(**kwargs), 1) is False


@pytest.mark.asyncio
async def test_a_reader_that_raises_never_breaks_the_call():
    bot = _bot()
    bot.image_config_service.get_toggles = AsyncMock(side_effect=RuntimeError("boom"))
    assert await rsvp_enabled(bot, 1) is False


@pytest.mark.asyncio
async def test_a_render_that_raises_yields_no_attachment_rather_than_an_error():
    """FR-055: the graphic gates neither the call nor the round's attendance rows."""
    bot = MagicMock()
    bot.module_service.is_images_enabled = AsyncMock(side_effect=RuntimeError("boom"))

    result = await try_attach(
        bot,
        1,
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
            None, 1, division_name="D", round_number=1, round_format="NORMAL",
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


def test_the_generation_has_exactly_one_call_site_in_the_whole_module():
    source = _SRC.joinpath("services", "rsvp_service.py").read_text(encoding="utf-8")
    assert source.count("try_attach(") == 1
    assert source.count("_checkin_attachment(") == 2  # its definition and its one call


def test_the_one_call_site_is_the_initial_post():
    source = inspect.getsource(rsvp_service.run_rsvp_notice)
    assert "_checkin_attachment(" in source
    # It is reached before the send, and the send is what posts the call.
    assert source.index("_checkin_attachment(") < source.index("channel.send(")


def test_the_call_is_never_deleted_and_reposted_while_it_stands():
    """Reposting would orphan the view re-armed against the stored message id."""
    source = inspect.getsource(rsvp_service.run_rsvp_notice)
    after_send = source.split("channel.send(", 1)[1]
    assert "delete()" not in after_send


def test_the_attachment_helper_never_raises():
    import dis

    opcodes = {
        instruction.opname
        for instruction in dis.get_instructions(rsvp_service._checkin_attachment)
    }
    assert "RAISE_VARARGS" not in opcodes


# ── The toggle changes nothing else (FR-052) ──────────────────────────────


def test_the_embed_and_the_view_are_built_before_and_regardless_of_the_graphic():
    """With the toggle on or off, the call is composed identically (FR-052)."""
    source = inspect.getsource(rsvp_service.run_rsvp_notice)
    assert source.index("build_rsvp_embed(") < source.index("_checkin_attachment(")
    assert source.index("RsvpView(") < source.index("_checkin_attachment(")


def test_the_attendance_rows_are_opened_after_the_post_either_way():
    """FR-055 — the graphic must not extend the existing post-then-open dependency."""
    source = inspect.getsource(rsvp_service.run_rsvp_notice)
    assert source.index("channel.send(") < source.index("bulk_insert_attendance_rows")


def test_the_notices_that_stay_text_carry_no_graphic(  # FR-053
):
    for function_name in (
        "_post_distribution_announcement",
        "_post_no_reserve_notice",
    ):
        if not hasattr(rsvp_service, function_name):
            continue
        source = inspect.getsource(getattr(rsvp_service, function_name))
        assert "file=" not in source
        assert "try_attach" not in source
