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

from pathlib import Path  # noqa: E402

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


def test_the_sheet_resolves_the_folder_its_marks_are_drawn_from():
    """The mark beneath a total is artwork, so its class must reach the resolver.

    Asserted against the source because ``render_sheet`` builds the pairs inline: a class the
    sheet projects but never resolves draws nothing at all, silently, and no fixture would
    say so.
    """
    from services import image_attendance_post
    from services.image_attendance_service import MARK_ASSET_CLASS

    source = inspect.getsource(image_attendance_post.render_sheet)
    assert f'("{MARK_ASSET_CLASS}", "marker_directory")' in source


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


# ── The marks, as pixels ──────────────────────────────────────────────────


def _rendered_sheet(tmp_path):
    """The sample sheet, filled out of the packaged assets and rasterised."""
    from lxml import etree

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
    from tests.support.image_sample_data import build_attendance_drawing

    from services.image_attendance_service import build_fill_spec
    from services.image_render_service import rasterise
    from utils.paths import PROJECT_ROOT
    from utils.svg_document import canvas_of
    from utils.svg_fill import fill as fill_spec_onto

    class _Team:
        def __init__(self, name, reserve=False):
            self.name, self.is_reserve = name, reserve

    packaged = Path(PROJECT_ROOT) / "resources" / "defaults"
    root = etree.parse(
        str(packaged / "templates" / "attendance_template.svg")
    ).getroot()
    drawing = build_attendance_drawing(
        root, [_Team("Alpha"), _Team("Bravo"), _Team("Charlie"), _Team("Reserve", True)]
    )
    spec = build_fill_spec(drawing, root)
    spec.asset_directories = {
        "marker": packaged / "markers",
        "flag": packaged / "flags",
        "team": packaged / "teams",
    }
    result = fill_spec_onto(spec)
    png = rasterise(
        result.svg, tmp_path / "attendance.png", result.canvas or canvas_of(root)
    )
    return root, drawing, png


def _lightness(rgb):
    """CIE L* of an sRGB triple.

    Used here to prove each mark is *present* — a wash over a near-black band raises the
    lightness of the box it fills, and nothing else on the row does. It is deliberately **not**
    what tells the two marks apart: they are drawn at one weight, so their lightness is
    near-identical by design and the hue is what carries the difference.
    """
    def _linear(channel):
        channel /= 255.0
        return (
            channel / 12.92
            if channel <= 0.04045
            else ((channel + 0.055) / 1.055) ** 2.4
        )

    red, green, blue = (_linear(float(c)) for c in rgb)
    y = 0.2126 * red + 0.7152 * green + 0.0722 * blue
    return 116 * (y ** (1 / 3)) - 16 if y > 0.008856 else 903.3 * y


@pytest.mark.rasteriser
def test_the_two_marks_reach_the_raster_and_are_told_apart_by_hue(tmp_path):
    """Rule XIV.14 — the marks are verified as pixels, never as markup.

    Only the raster proves the mark was drawn *where and how* the artwork says: an href the
    rasteriser cannot follow, a slot authored after its text rather than before it, or a
    `preserveAspectRatio` that letterboxes instead of stretching all leave correct-looking
    markup and a wrong picture.

    Every coordinate is read out of the template rather than assumed, and the samples are
    taken in the corners, well clear of the glyph — whose width depends on which font the
    host resolved.
    """
    from utils.svg_document import FieldIndex

    from PIL import Image  # noqa: PLC0415

    root, drawing, png = _rendered_sheet(tmp_path)
    image = Image.open(png).convert("RGB")
    index = FieldIndex(root)

    def corners(ordinal):
        slot = index.resolve(f"row_{ordinal}_points_background")
        left, top = float(slot.get("x")), float(slot.get("y"))
        width, height = float(slot.get("width")), float(slot.get("height"))
        return (
            image.getpixel((int(left + width) - 2, int(top) + 2)),      # opaque corner
            image.getpixel((int(left) + 2, int(top + height) - 2)),     # clear corner
        )

    # The sample is pitched at exactly this: the first row on the limit, the second one point
    # short of it, the third earning nothing.
    marks = [entry.mark for entry in drawing.entries[:3]]
    assert marks == ["attendance_limit_reached", "attendance_limit_near", None]

    reached_top, reached_bottom = corners(1)
    near_top, near_bottom = corners(2)
    bare_top, bare_bottom = corners(3)

    band = _lightness(bare_top)
    assert _lightness(bare_bottom) == pytest.approx(band, abs=1.0), (
        "an unmarked row is not the bare band, so something was drawn into it"
    )

    # Both marks are plainly present against the band.
    for opaque in (near_top, reached_top):
        assert _lightness(opaque) - band > 15

    # **And they are told apart by hue, not by weight.** The two stand at one lightness on
    # purpose: a warning drawn as a fainter sanction reads as a weaker sanction, which is not
    # what it means. Pinned because it is the kind of intent a later eye tidies away — dropping
    # one of them a step would look like an improvement and would silently undo it.
    assert _lightness(reached_top) == pytest.approx(_lightness(near_top), abs=4.0)

    # Amber for the warning, red for the sanction. Both are warm, so red leads in each; what
    # separates them is the green channel, which the amber carries and the red does not.
    for opaque in (near_top, reached_top):
        assert opaque[0] > opaque[2], "a mark that is not warm at all"
    assert near_top[1] > reached_top[1] * 2, (
        f"the near mark {near_top} is not plainly amber beside the reached one {reached_top}"
    )

    # The gradient runs to full transparency in the opposite corner: the bottom-left of a
    # marked cell is the row band again, whichever mark it carries.
    for clear in (reached_bottom, near_bottom):
        assert _lightness(clear) - band < 2
