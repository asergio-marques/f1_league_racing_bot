"""The image module's answers to core's attendance questions at season review: can the
attendance sheet draw the longest calendar, and can the check-in call name a round's sessions?
(#208, #283)

Core counts what the season holds and asks: the division holding the most rounds for the sheet,
and the most sessions any round is run over for the call — four if any round is a sprint, two
otherwise. It words each answer as a ⚠️ line of the review, follows the lines with a blank one,
and lets the review through where a check cannot run (`tests/core/test_season_review_capacity.py`).
Here the image module measures the configured templates and answers the problem and its remedy,
or nothing.

**Warnings, never refusals** (Constitution XIV.9). Which division's sheet and which round's call
are actually drawn is decided later, so a season whose drawn division fits could otherwise be
refused over one that never is.

**A template that draws no grid is a choice, not a fault.** A column capacity of nought means
the sheet has no round grid at all (XIV.3), and a warning about it would tell a league its
deliberate design is broken.

**The sheet's rows are not compared here.** They are guarded where they could overflow — a
driver assignment — which is the earlier moment XIV.12 requires. A reader adding a row check to
the review would report the same overflow twice, at the later of the two moments.

**An aspect that is off is not checked,** and a template that is missing or invalid is Layer 2's
to report. An uncountable template is let out as `CapacityError`, for core's one catch.

Nothing here rasterises: the templates are built in memory and read as SVG.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from lxml import etree

SVG_NS = "http://www.w3.org/2000/svg"
SHEET = "attendance_template"
CALL = "rsvp_template"
REMEDY = "Enlarge the template to draw it."


def _template_file(tmp_path, key, ids):
    """A template for *key* declaring the fields *ids*."""
    root = etree.Element(f"{{{SVG_NS}}}svg")
    root.set("width", "800")
    root.set("height", "600")
    for field_id in ids:
        etree.SubElement(root, f"{{{SVG_NS}}}text").set("id", field_id)
    path = tmp_path / f"{key}.svg"
    path.write_bytes(etree.tostring(root))
    return path


def _report(path, key, *, valid=True):
    return SimpleNamespace(valid=valid, resolved_path=path if valid else None, template_key=key)


def _drawing(tmp_path, *, columns=(1, 2, 3), sessions=(1, 2), aspects=("attendance", "rsvp"),
             reports=None, images=True):
    """The image module's implementation of core's protocol, over stand-ins for its own
    services: the *aspects* switched on, and an attendance sheet drawing the round *columns*
    and a check-in call naming the *sessions*, both valid, unless other *reports* are given."""
    from leaguebot.image.services.image_drawing import ImageDrawing

    if reports is None:
        sheet = _template_file(tmp_path, SHEET, [f"round_{n}_number" for n in columns])
        call = _template_file(tmp_path, CALL, [f"session_{n}_name" for n in sessions])
        reports = {SHEET: _report(sheet, SHEET), CALL: _report(call, CALL)}
    modules = MagicMock()
    modules.is_images_enabled = AsyncMock(return_value=images)
    config = MagicMock()
    config.get_config = AsyncMock(return_value=MagicMock())
    config.get_toggles = AsyncMock(return_value={aspect: True for aspect in aspects})
    config.is_aspect_enabled = AsyncMock(side_effect=lambda aspect: aspect in aspects)
    validity = MagicMock()
    validity.template_reports = AsyncMock(return_value=reports)
    return ImageDrawing(
        modules=modules, config=config, validity=validity,
        renderer=MagicMock(), post_log=AsyncMock(), db_path="",
    )


def _held(name, rounds):
    from leaguebot.core.services.image_drawing import Held

    return Held(name, rounds)


# ---------------------------------------------------------------------------
# The sheet's round columns
# ---------------------------------------------------------------------------


async def test_a_sheet_that_fits_the_longest_calendar_earns_no_warning(tmp_path):
    drawing = _drawing(tmp_path, columns=(1, 2, 3))

    assert await drawing.sheet_review_problem(_held("Pro", 3)) is None


@pytest.mark.parametrize("images", [True, False], ids=["module-on", "module-off"])
async def test_a_sheet_too_narrow_for_the_longest_calendar_is_warned_about(tmp_path, images):
    """It names the division handed, whose sheet will fall back to text: the one a manager
    needs to know about. The review warns on the aspect alone, whether or not the images
    module is on."""
    drawing = _drawing(tmp_path, columns=(1, 2, 3, 4), images=images)

    answer = await drawing.sheet_review_problem(_held("Am", 6))

    assert (answer.problem, answer.remedy) == (
        "Attendance sheet template draws 4 round column(s); `Am` holds 6. That division's "
        "sheet cannot be drawn as an image and will be posted as text",
        REMEDY,
    )


async def test_a_template_with_no_grid_is_not_a_divergence(tmp_path):
    """XIV.3: nought means the sheet draws no round grid, which is a legitimate design."""
    drawing = _drawing(tmp_path, columns=(), aspects=("attendance",))

    assert await drawing.sheet_review_problem(_held("Pro", 30)) is None


async def test_the_sheet_is_not_checked_with_its_aspect_off(tmp_path):
    drawing = _drawing(tmp_path, columns=(1,), aspects=())

    assert await drawing.sheet_review_problem(_held("Pro", 30)) is None


@pytest.mark.parametrize("valid", [None, False], ids=["missing", "invalid"])
async def test_a_missing_or_invalid_template_is_not_this_checks_to_report(tmp_path, valid):
    """Layer 2 reports it, and reporting it here too would say it twice."""
    sheet = _template_file(tmp_path, SHEET, ["round_1_number"])
    report = None if valid is None else _report(sheet, SHEET, valid=False)
    drawing = _drawing(tmp_path, aspects=("attendance",), reports={SHEET: report})

    assert await drawing.sheet_review_problem(_held("Pro", 30)) is None


# ---------------------------------------------------------------------------
# The check-in call's sessions
# ---------------------------------------------------------------------------


async def test_a_call_naming_two_sessions_fits_a_season_with_no_sprint(tmp_path):
    """No round is a sprint, so core asks of two sessions."""
    drawing = _drawing(tmp_path, sessions=(1, 2), aspects=("rsvp",))

    assert await drawing.checkin_review_problem(2) is None


async def test_a_sprint_round_needs_four_sessions(tmp_path):
    """A sprint round is run over four sessions, so core asks of four; the call has to name
    every one."""
    drawing = _drawing(tmp_path, sessions=(1, 2), aspects=("rsvp",))

    answer = await drawing.checkin_review_problem(4)

    assert (answer.problem, answer.remedy) == (
        "Check-in template names 2 session(s); this season holds a round run over 4. That "
        "call cannot be drawn as an image and will be posted without one",
        REMEDY,
    )


async def test_a_call_naming_four_sessions_fits_a_sprint_season(tmp_path):
    drawing = _drawing(tmp_path, sessions=(1, 2, 3, 4), aspects=("rsvp",))

    assert await drawing.checkin_review_problem(4) is None


@pytest.mark.parametrize("images", [True, False], ids=["module-on", "module-off"])
async def test_a_call_too_small_for_any_round_is_warned_about(tmp_path, images):
    """The review warns on the `rsvp` aspect alone, whether or not the images module is on."""
    drawing = _drawing(tmp_path, sessions=(1,), aspects=("rsvp",), images=images)

    answer = await drawing.checkin_review_problem(2)

    assert answer.problem == (
        "Check-in template names 1 session(s); this season holds a round run over 2. That "
        "call cannot be drawn as an image and will be posted without one"
    )


async def test_the_call_is_not_checked_with_its_aspect_off(tmp_path):
    drawing = _drawing(tmp_path, sessions=(1,), aspects=())

    assert await drawing.checkin_review_problem(4) is None


# ---------------------------------------------------------------------------
# A template that cannot be counted is core's to catch
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("question", ["sheet", "call"])
async def test_an_uncountable_template_is_let_out_for_core_s_catch(tmp_path, question):
    """A gap in the numbering cannot be counted. The image module lets the `CapacityError`
    out, and core's one catch drops the review's attendance, standings and check-in warnings
    together and lets the review through."""
    from leaguebot.image.models.image_catalogues import CapacityError

    drawing = _drawing(tmp_path, columns=(1, 2, 4), sessions=(1, 3))

    with pytest.raises(CapacityError):
        if question == "sheet":
            await drawing.sheet_review_problem(_held("Pro", 3))
        else:
            await drawing.checkin_review_problem(2)
