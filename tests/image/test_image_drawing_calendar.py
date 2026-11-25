"""The image module's calendar drawing, its answer to core's drawing protocol (#283).

Core asks whether the league draws its calendar and has one division's calendar drawn
(`core/services/modules/image_drawing.py`); the image module answers in `image/services/render/image_drawing.py`.
These tests hold its answers: both gates read, a gate that cannot be read answering no, the two
asset directories made absolute, a rejected directory carried as a fault, and the notices reported
to the log channel in one message. Core's posting of the calendar is tested against a stand-in for
this drawing, under `tests/core/`.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock

import pytest
from leaguebot.image.services.render.image_render_service import grouped_notice_lines
from tests.support.switches import answer_switch

TRACKS = {
    "Silverstone Circuit": NS(
        name="Silverstone Circuit", gp_name="British Grand Prix", country="United Kingdom"
    )
}
TEMPLATES = Path(__file__).resolve().parents[2] / "resources" / "defaults" / "templates"

#: Why a drawing's notices fail until they come back grouped by kind, repeats counted.
NOT_YET_GROUPED = "#635: a drawing hands back each notice's words, one apiece"


def _rounds(count=2):
    return [
        NS(
            round_number=index,
            format="NORMAL",
            track_name="Silverstone Circuit",
            scheduled_at=datetime(2026, 6, 4 + 7 * (index - 1), 20, 0, tzinfo=timezone.utc),
        )
        for index in range(1, count + 1)
    ]


def _division():
    return NS(id=7, name="Elite", tier=1, calendar_channel_id=999, calendar_message_id=None)


def _drawing(*, images_on=True, aspect_on=True, flag_directory="resources/defaults/flags",
             outcome=None):
    """Image's drawing over doubles of its services: the module and the `calendar` aspect as
    given, the configured directories, and a renderer giving *outcome* (nothing drawn by
    default), keeping the spec builder it was handed in `captured`."""
    from leaguebot.image.services.render.image_drawing import ImageDrawing

    modules = MagicMock()
    answer_switch(modules, "images", images_on)
    config = MagicMock()
    config.is_aspect_enabled = AsyncMock(return_value=aspect_on)
    config.get_config = AsyncMock(
        return_value=NS(
            date_format="DDD_DD_MON_YYYY",
            time_format="24H",
            time_zone="UTC",
            track_image_directory="resources/defaults/tracks",
            flag_directory=flag_directory,
        )
    )
    captured = {}

    async def _render(image_type, spec_builder, **kwargs):
        captured["builder"] = spec_builder
        captured["kwargs"] = kwargs
        return outcome or NS(problem=None, notices=[], png_paths=[])

    renderer = MagicMock()
    renderer.render = AsyncMock(side_effect=_render)
    post_log = AsyncMock()
    drawing = ImageDrawing(
        modules, config, MagicMock(), renderer=renderer, post_log=post_log, db_path="",
    )
    return drawing, captured, post_log, modules


@pytest.mark.parametrize(
    "images_on,aspect_on,wanted",
    [
        (False, True, False),
        (True, False, False),
        (False, False, False),
        (True, True, True),
    ],
    ids=["images_off", "aspect_off", "both_off", "both_open"],
)
async def test_the_calendar_is_drawn_only_with_both_gates_open(images_on, aspect_on, wanted):
    """Either gate closed, the module or the `calendar` aspect, answers that the league posts its
    calendar as text; both open answers that it draws it."""
    drawing, *_ = _drawing(images_on=images_on, aspect_on=aspect_on)
    assert await drawing.calendar_wanted() is wanted


async def test_a_gate_that_cannot_be_read_answers_text(caplog):
    """The images module's switch cannot be read (the database down): the drawing answers that the
    calendar is not drawn, so the text goes out and a league never loses its calendar, and the
    error is in the host's log."""
    drawing, _, _, modules = _drawing()
    answer_switch(modules, "images", RuntimeError("db down"))

    assert await drawing.calendar_wanted() is False
    assert "db down" in caplog.text


async def test_the_asset_directories_are_absolute():
    """Regression: the calendar resolved its own directories and produced relative paths.

    A relative path passes `resolve_asset` (the bot's working directory is the project root) and
    is written into the SVG as a relative href. The rasteriser reads that SVG out of a temporary
    directory and resolves the href against *that*, so every circuit map and country flag a league
    supplied was silently absent from the picture. Only the packaged `mystery.svg`, resolved
    against the project root and therefore absolute, appeared.

    Every other posting path goes through `resolve_configured_directories`. This pins the calendar
    to it.
    """
    from leaguebot.image.utils.svg_document import load_svg

    drawing, captured, *_ = _drawing()

    await drawing.draw_calendar(_division(), _rounds(), TRACKS)

    spec = captured["builder"](load_svg(TEMPLATES / "calendar_template.svg"))
    assert set(spec.asset_directories) == {"track", "flag"}
    for asset_class, directory in spec.asset_directories.items():
        assert directory.is_absolute(), asset_class
    assert spec.asset_directories["track"].name == "tracks"
    assert spec.asset_directories["flag"].name == "flags"


async def test_a_rejected_directory_is_carried_through_as_a_fault():
    """The reason is the point: `not configured` and `rejected` are different faults.

    The hand-rolled resolution reported neither: a directory that escaped the project root simply
    became `None`, and the filler then said the class was never configured.
    """
    from leaguebot.image.utils.svg_document import load_svg

    drawing, captured, *_ = _drawing(flag_directory="../../elsewhere")

    await drawing.draw_calendar(_division(), _rounds(), TRACKS)

    spec = captured["builder"](load_svg(TEMPLATES / "calendar_template.svg"))
    assert "flag" not in spec.asset_directories
    assert "flag" in spec.asset_directory_faults


async def test_a_calendar_drawing_hands_its_notices_back_and_logs_nothing():
    """Elite's calendar drawn with a notice. Every calendar is drawn inside a change's job, so the
    drawing writes nothing to the log channel itself and hands the notice back with the picture,
    grouped under its kind's heading, for the posting job to record once among its own lines (the
    owner's decision on #288: code inside a job never calls `post_log`)."""
    from leaguebot.image.models.image_module import RenderNotice

    notice = RenderNotice(image_type="calendar", notice_kind="ASSET_FALLBACK_USED",
                          detail="a fallback flag stood in for Monaco's")
    drawing, _, post_log, _ = _drawing(
        outcome=NS(problem=None, notices=[notice], png_paths=[])
    )

    picture = await drawing.draw_calendar(_division(), _rounds(), TRACKS)

    post_log.assert_not_awaited()
    assert tuple(picture.notices) == tuple(grouped_notice_lines([notice]))


#: The converter's absence, in the words the render gives it.
_ABSENT_WORDS = "Inkscape is not installed on this host."


@pytest.mark.parametrize(
    "drawn,problem,source",
    [
        pytest.param("a picture", None, "LEAGUE", id="a_picture"),
        pytest.param("a problem", "the template has no rows", "LEAGUE", id="a_problem"),
        pytest.param("an internal problem", "the bot hit an internal fault", "BOT",
                     id="an_internal_problem"),
        pytest.param("a converter absent", _ABSENT_WORDS, "HOST", id="a_converter_absent"),
    ],
)
async def test_the_drawing_hands_back_the_picture_the_problem_and_the_notices(
    tmp_path, drawn, problem, source,
):
    """Elite's calendar drawn, with one notice: the drawing hands core the picture's path where it
    was drawn, or the problem's words where it could not be, and the notices' words either way.
    The picture says where its fault lies: the league's for a field at fault or none, the bot's
    for an internal fault, which comes back named by its kind alone, and the host's for the
    converter's absence, which keeps its words. The notices come back grouped under their kind's
    heading."""
    from leaguebot.core.models.fault_source import FaultSource
    from leaguebot.image.models.image_module import (
        PROBLEM_CONVERTER_ABSENT,
        PROBLEM_DRAWING_FAULT,
        PROBLEM_UNRESOLVED_VALUE,
        Problem,
        RenderNotice,
    )

    png = tmp_path / "calendar.png"
    notice = RenderNotice(image_type="calendar", notice_kind="ASSET_FALLBACK_USED",
                          detail="a fallback flag stood in for Monaco's")
    fault = {
        "a problem": Problem(kind=PROBLEM_UNRESOLVED_VALUE, detail="the template has no rows"),
        "an internal problem": Problem(kind=PROBLEM_DRAWING_FAULT,
                                       detail="the bot hit an internal fault"),
        "a converter absent": Problem(kind=PROBLEM_CONVERTER_ABSENT, detail=_ABSENT_WORDS),
    }.get(drawn)
    outcome = (
        NS(problem=None, notices=[notice], png_paths=[png]) if fault is None
        else NS(problem=fault, notices=[notice], png_paths=[])
    )
    drawing, *_ = _drawing(outcome=outcome)

    picture = await drawing.draw_calendar(_division(), _rounds(), TRACKS)

    assert picture.path == (png if fault is None else None)
    assert picture.problem == problem
    assert picture.fault_source is FaultSource[source]
    assert tuple(picture.notices) == tuple(grouped_notice_lines([notice]))


async def test_a_calendar_whose_track_cannot_be_named_comes_back_as_its_problem():
    """Elite's calendar asked for with a round at Monza, which matches no track on this server:
    the drawing hands the refusal back as the picture's problem, in its own words, rather than
    raising it. Nothing is rendered, and the fault is the league's."""
    from leaguebot.core.models.fault_source import FaultSource

    rounds = _rounds()
    rounds[1].track_name = "Monza"
    drawing, captured, *_ = _drawing()

    picture = await drawing.draw_calendar(_division(), rounds, TRACKS)

    assert picture.path is None
    assert "matches no track" in picture.problem
    assert picture.fault_source is FaultSource.LEAGUE
    assert "builder" not in captured


def test_a_released_calendar_is_removed_with_its_render_directory(tmp_path):
    """`discard_picture` removes a drawn calendar and the render directory holding it, so no
    posting leaves its picture behind in the host's temporary space; handed no picture, it
    does nothing."""
    drawing, *_ = _drawing()
    directory = tmp_path / "f1bot_render_calendar"
    directory.mkdir()
    png = directory / "calendar_template.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n")

    drawing.discard_picture(png)
    drawing.discard_picture(None)

    assert not png.exists()
    assert not directory.exists()


async def test_a_switch_nobody_signed_up_for_is_raised_not_read_as_text():
    """The images module's switch reader was never signed up (`SwitchNotSignedUp`): that is a fault in
    the bot's assembly, not a gate that cannot be read, so it is raised rather than answered as
    text."""
    drawing, _, _, modules = _drawing()
    from leaguebot.framework.services.switches.module_service import SwitchNotSignedUp

    answer_switch(modules, "images", SwitchNotSignedUp("images"))

    with pytest.raises(SwitchNotSignedUp, match="images"):
        await drawing.calendar_wanted()
