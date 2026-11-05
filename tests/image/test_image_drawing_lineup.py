"""The image module's lineup drawing, its answer to core's drawing protocol (#283).

Core asks whether the league draws its lineup and has one division's lineup drawn
(`core/services/image_drawing.py`); the image module answers in `image/services/image_drawing.py`.
These tests hold its answers: the three gates read, a gate that cannot be read answering text with
the error in the host's log alone, the lineup drawn from the division's committed seats with the
portraits refreshed at the league's own trigger, the picture, the problem and the notices handed
back with nothing posted, and the notices reported to the log channel in one message naming the
division. Core's posting of the lineup is tested against a stand-in for this drawing, under
`tests/core/`.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.attendance.test_uncommitted_drivers_outside_attendance import (
    DIVISION_ID,
    db_path,  # noqa: F401 — the fixture
)

TEMPLATES = Path(__file__).resolve().parents[2] / "resources" / "defaults" / "templates"
#: The display names the server gives the fixture's three drivers: 1001 committed to Pro's Alpha,
#: 1002 placed there mid-season and not yet confirmed, 1003 seated with no placement at all.
NAMES = {1001: "Zara Quill", 1002: "Yuri Vexley", 1003: "Cal Cole"}

#: Why the commanded draw's cases fail until the build.
_NO_COMMANDED_DRAW = (
    "#283: the image drawing does not yet draw a lineup as /team lineup's output "
    "(draw_lineup_for_command)"
)
#: The lineup drawn as the lineup of record (`draw_lineup`), and as `/team lineup`'s output
#: (`draw_lineup_for_command`); both draw the same seats, at the same trigger, and report their
#: notices the same way.
DRAWS = [
    pytest.param("draw_lineup", id="of_record"),
    pytest.param(
        "draw_lineup_for_command", id="for_command",
        marks=pytest.mark.xfail(strict=True, reason=_NO_COMMANDED_DRAW),
    ),
]


def _guild():
    """The league's server: each driver's member by id, and a lineup channel that would take any
    send, so a test can see that none is made."""
    members = {uid: MagicMock(id=uid, display_name=name) for uid, name in NAMES.items()}
    channel = MagicMock()
    channel.send = AsyncMock()
    guild = MagicMock()
    guild.get_member.side_effect = members.get
    guild.get_channel.return_value = channel
    guild.fetch_channel = AsyncMock(return_value=channel)
    return guild, channel, members


def _drawing(db_path=":memory:", *, images_on=True, aspect_on=True, valid=True, outcome=None):
    """Image's drawing over doubles of its services: the module, the `lineup` aspect and the
    lineup template's validity as given, the packaged directories configured, and a renderer
    giving *outcome* (nothing drawn by default), keeping the spec builder it was handed in
    `captured`. Handed the database path, as the builder hands it."""
    from leaguebot.image.services.image_drawing import ImageDrawing

    modules = MagicMock()
    modules.is_images_enabled = AsyncMock(return_value=images_on)
    config = MagicMock()
    config.is_aspect_enabled = AsyncMock(return_value=aspect_on)
    config.get_toggles = AsyncMock(return_value={"lineup": aspect_on})
    config.get_config = AsyncMock(
        return_value=NS(
            team_image_directory="resources/defaults/teams",
            flag_directory="resources/defaults/flags",
            driver_image_directory="resources/defaults/drivers",
            use_pfp=False,
            pfp_prerender=False,
        )
    )
    validity = MagicMock()
    validity.template_reports = AsyncMock(
        return_value={
            "lineup_template": NS(valid=valid, resolved_path=TEMPLATES / "lineup_template.svg")
        }
    )
    captured = {}

    async def _render(image_type, spec_builder, **kwargs):
        captured["image_type"] = image_type
        captured["builder"] = spec_builder
        return outcome or NS(problem=None, notices=[], png_paths=[])

    renderer = MagicMock()
    renderer.render = AsyncMock(side_effect=_render)
    post_log = AsyncMock()
    drawing = ImageDrawing(modules, config, validity, renderer=renderer, post_log=post_log,
                           db_path=db_path)
    return drawing, captured, post_log, modules


def _portraits_spied(monkeypatch):
    """Stand a spy in for the portrait refresh before a render, wherever the drawing reaches it;
    gives the spy."""
    import leaguebot.image.services.driver_portrait_service as portraits
    import leaguebot.image.services.image_drawing as image_drawing

    spy = AsyncMock(return_value=0)
    monkeypatch.setattr(portraits, "refresh_before_render", spy)
    monkeypatch.setattr(image_drawing, "refresh_before_render", spy, raising=False)
    return spy


@pytest.mark.parametrize(
    "images_on,aspect_on,valid,wanted",
    [
        (False, True, True, False),
        (True, False, True, False),
        (True, True, False, False),
        (True, True, True, True),
    ],
    ids=["images_off", "aspect_off", "no_valid_template", "all_open"],
)
async def test_the_lineup_is_drawn_only_with_every_gate_open(images_on, aspect_on, valid, wanted):
    """The images module off, the `lineup` aspect off, or no valid lineup template answers that
    the league posts its lineup as text; all three open answers that it draws it."""
    drawing, *_ = _drawing(images_on=images_on, aspect_on=aspect_on, valid=valid)
    assert await drawing.lineup_wanted() is wanted


async def test_a_lineup_gate_that_cannot_be_read_answers_text(caplog):
    """The images module's switch cannot be read (the database down): the drawing answers that the
    lineup is not drawn, so the text goes out and a league never loses its lineup; the error is in
    the host's log, and nothing is said in the log channel."""
    drawing, _, post_log, modules = _drawing()
    modules.is_images_enabled = AsyncMock(side_effect=RuntimeError("db down"))

    assert await drawing.lineup_wanted() is False
    assert "db down" in caplog.text
    post_log.assert_not_awaited()


@pytest.mark.parametrize("draw", DRAWS)
async def test_a_drawn_lineup_leaves_out_a_driver_not_yet_confirmed(db_path, monkeypatch, draw):
    """Pro, in a season being raced, seats Zara (placement confirmed), Yuri (placed mid-season and
    not yet confirmed) and a third seat with no placement. Pro's lineup is drawn, as the lineup of
    record and as `/team lineup`'s output alike: Zara is drawn, Yuri is not (image spec, "A driver
    whose placement is not yet committed shall not be drawn")."""
    from leaguebot.image.utils.svg_document import load_svg

    _portraits_spied(monkeypatch)
    guild, *_ = _guild()
    drawing, captured, *_ = _drawing(db_path)

    await getattr(drawing, draw)(guild, DIVISION_ID)

    assert captured["image_type"] == "lineup_template"
    spec = captured["builder"](load_svg(TEMPLATES / "lineup_template.svg"))
    filled = " ".join(spec.text.values()).lower()
    assert "quill" in filled, spec.text
    assert "vexley" not in filled, spec.text


@pytest.mark.parametrize("draw", DRAWS)
async def test_a_drawn_lineup_refreshes_portraits_only_at_the_league_s_trigger(
    db_path, monkeypatch, draw,
):
    """Pro's lineup drawn for a posting, or as `/team lineup`'s output: the portraits of the
    drivers it draws are brought up to date as the league's own trigger says, and none missing is
    fetched beyond it, which only the placements review may ask for. Zara alone is drawn, so hers
    alone is asked for."""
    spy = _portraits_spied(monkeypatch)
    guild, _, members = _guild()
    drawing, *_ = _drawing(db_path)

    await getattr(drawing, draw)(guild, DIVISION_ID)

    spy.assert_awaited_once()
    assert spy.await_args.args[1] == [members[1001]]
    assert spy.await_args.kwargs["obtain_missing"] is False


@pytest.mark.parametrize("draw", DRAWS)
async def test_a_drawn_lineup_reports_its_notices_to_the_log_channel_in_one_message(
    db_path, monkeypatch, draw,
):
    """Pro's lineup drawn with two notices, as the lineup of record or as `/team lineup`'s output:
    the drawing itself reports them to the log channel, in one message headed with the lineup and
    the division, so they reach the log whichever change or command drew the lineup, and never
    the lineup channel (Constitution XIV.4, "reported in one message"; the owner's decision on
    #283)."""
    from leaguebot.image.models.image_module import RenderNotice

    _portraits_spied(monkeypatch)
    notices = [
        RenderNotice(image_type="lineup", notice_kind="ASSET_FALLBACK_USED",
                     detail="a fallback flag stood in for Zara's"),
        RenderNotice(image_type="lineup", notice_kind="ASSET_FALLBACK_USED",
                     detail="a fallback portrait stood in for Zara's"),
    ]
    guild, channel, _ = _guild()
    drawing, _, post_log, _ = _drawing(
        db_path, outcome=NS(problem=None, notices=notices, png_paths=[])
    )

    await getattr(drawing, draw)(guild, DIVISION_ID)

    [call] = post_log.await_args_list
    assert call.args[0].startswith("Image render notices — lineup — Pro:\n"), call.args[0]
    assert "a fallback flag stood in for Zara's" in call.args[0]
    assert "a fallback portrait stood in for Zara's" in call.args[0]
    channel.send.assert_not_awaited()


#: The problem's words /team lineup's refusal carries, as `Problem.message("the lineup")` gives
#: them: a field the template is at fault in named with it, and an internal fault never detailed.
_INTERNAL_WORDS = (
    "this image could not be produced — the fault has been recorded for the bot's operator."
)


@pytest.mark.parametrize(
    "draw,drawn,problem",
    [
        pytest.param("draw_lineup", "a picture", None, id="of_record-a_picture"),
        pytest.param("draw_lineup", "a field problem", "the template has no rows",
                     id="of_record-a_problem"),
        pytest.param("draw_lineup_for_command", "a picture", None, id="for_command-a_picture",
                     marks=pytest.mark.xfail(strict=True, reason=_NO_COMMANDED_DRAW)),
        pytest.param("draw_lineup_for_command", "a field problem",
                     "the lineup: `team_1` — the template has no rows",
                     id="for_command-a_field_problem",
                     marks=pytest.mark.xfail(strict=True, reason=_NO_COMMANDED_DRAW)),
        pytest.param("draw_lineup_for_command", "an internal problem", _INTERNAL_WORDS,
                     id="for_command-an_internal_problem",
                     marks=pytest.mark.xfail(strict=True, reason=_NO_COMMANDED_DRAW)),
    ],
)
async def test_the_lineup_drawing_hands_back_the_picture_the_problem_and_the_notices(
    db_path, monkeypatch, tmp_path, draw, drawn, problem,
):
    """Pro's lineup drawn, with one notice: the drawing hands core the picture's path where it was
    drawn, or the problem's words where it could not be, and the notices' words either way. It
    posts nothing to Pro's lineup channel. Drawn as the lineup of record, the problem is its bare
    detail, which the text lineup's fallback line carries. Drawn as `/team lineup`'s output, it
    is the words the command's refusal reads today: the lineup and the field at fault named before
    the detail, and an internal fault, which no league can act on, never detailed."""
    from leaguebot.image.models.image_module import (
        PROBLEM_UNKNOWN_IMAGE_TYPE,
        PROBLEM_UNRESOLVED_VALUE,
        Problem,
        RenderNotice,
    )

    _portraits_spied(monkeypatch)
    directory = tmp_path / "f1bot_render_lineup"
    directory.mkdir()
    png = directory / "lineup_template.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n")
    notice = RenderNotice(image_type="lineup", notice_kind="ASSET_FALLBACK_USED",
                          detail="a fallback flag stood in for Zara's")
    fault = {
        "a field problem": Problem(kind=PROBLEM_UNRESOLVED_VALUE,
                                   detail="the template has no rows", field_id="team_1"),
        "an internal problem": Problem(kind=PROBLEM_UNKNOWN_IMAGE_TYPE,
                                       detail="no renderer for lineup_template"),
    }.get(drawn)
    outcome = (
        NS(problem=None, notices=[notice], png_paths=[png]) if fault is None
        else NS(problem=fault, notices=[notice], png_paths=[])
    )
    guild, channel, _ = _guild()
    drawing, *_ = _drawing(db_path, outcome=outcome)

    picture = await getattr(drawing, draw)(guild, DIVISION_ID)

    assert picture.path == (png if fault is None else None)
    assert picture.problem == problem
    assert tuple(picture.notices) == ("a fallback flag stood in for Zara's",)
    channel.send.assert_not_awaited()
