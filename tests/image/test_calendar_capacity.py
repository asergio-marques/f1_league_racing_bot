"""The image module's answers to core's two calendar questions — the round a division would
hold, and the review's longest calendar (#208, #283).

The same overflow is reported two ways at two moments, and the difference is the whole point
of having both. **Adding a round that outgrows the template is refused outright**, with the
round not added: `/round add` knows exactly which division it is touching, so the comparison is
certain and Constitution XIV.12 requires overflow to be rejected at the earliest moment it can
be detected. **Season review can only warn** (Constitution XIV.9): it compares against the
greatest round count any division holds, but which division is actually drawn is decided later.
Core asks the one question or the other, and words the refusal or the warning around the
answer; `tests/core/test_season_review_capacity.py` pins core's side, its counting, and that a
check which cannot run lets the season and the round through.

Here the image module measures the configured template and answers the problem and its remedy,
or nothing. It answers nothing where the template is not drawn or cannot be trusted: the module
off (for the round), the `calendar` aspect off, no valid template. A fault reading the template
or the reports, a template declaring no rounds among them, is let out, for core's one catch.

**It counts rounds, not drivers.** The placement questions count seated drivers against the
lineup and attendance templates; a calendar's collection is rounds. The two guard different
commands and must not be merged (research.md § R3).

Nothing here rasterises: the templates are built in memory and read as SVG.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from lxml import etree

SVG_NS = "http://www.w3.org/2000/svg"
CALENDAR = "calendar_template"
ROUND_REMEDY = (
    "Enlarge the template, or turn the `calendar` image aspect off with `/images config toggle`."
)
REVIEW_REMEDY = "Enlarge the template to draw it."


def _template_file(tmp_path, *, rounds: int):
    """A calendar template declaring *rounds* rounds."""
    root = etree.Element(f"{{{SVG_NS}}}svg")
    root.set("width", "600")
    root.set("height", "400")
    etree.SubElement(root, f"{{{SVG_NS}}}text").set("id", "division_name")
    for number in range(1, rounds + 1):
        etree.SubElement(root, f"{{{SVG_NS}}}text").set("id", f"round_{number}_number")
    path = tmp_path / f"{CALENDAR}.svg"
    path.write_bytes(etree.tostring(root))
    return path


def _drawing(
    tmp_path, *, rounds=24, images=True, aspect=True, report=..., reports_error=None
):
    """The image module's implementation of core's protocol, over stand-ins for its own
    services: the images module *images*, the `calendar` aspect *aspect*, and a valid calendar
    template drawing *rounds* rounds unless another *report* is given, or *reports_error*
    raised reading the reports."""
    from leaguebot.image.services.image_drawing import ImageDrawing

    if report is ...:
        report = SimpleNamespace(
            valid=True, resolved_path=_template_file(tmp_path, rounds=rounds),
            template_key=CALENDAR,
        )
    modules = MagicMock()
    modules.is_images_enabled = AsyncMock(return_value=images)
    config = MagicMock()
    config.get_config = AsyncMock(return_value=MagicMock())
    config.get_toggles = AsyncMock(return_value={"calendar": aspect})
    config.is_aspect_enabled = AsyncMock(side_effect=lambda name: aspect and name == "calendar")
    validity = MagicMock()
    validity.template_reports = AsyncMock(
        return_value={CALENDAR: report}, side_effect=reports_error
    )
    return ImageDrawing(
        modules=modules, config=config, validity=validity,
        renderer=MagicMock(), post_log=AsyncMock(), db_path="",
    )


async def _ask(drawing, question: str, held: int):
    """Ask *question* of *held* rounds: ``round`` for the round a division would hold,
    ``review`` for the review's longest calendar, held by Premier."""
    if question == "round":
        return await drawing.round_capacity_problem(held)
    from leaguebot.core.services.image_drawing import Held

    return await drawing.calendar_review_problem(Held("Premier", held))


QUESTIONS = pytest.mark.parametrize("question", ["round", "review"])


# ── The round a division would hold (XIV.12) ──────────────────────────────


@pytest.mark.parametrize(
    "would_hold",
    [20, 24],
    ids=["within_capacity", "exactly_at_capacity"],
)
async def test_a_round_within_capacity_is_not_refused(tmp_path, would_hold):
    """The comparison is `>`, so a template drawing twenty-four rounds takes twenty-four."""
    assert await _drawing(tmp_path).round_capacity_problem(would_hold) is None


async def test_a_round_past_capacity_is_refused(tmp_path):
    """The remedy names both ways out: enlarging the template and turning the aspect off are
    genuinely different decisions, and a league may reasonably take either."""
    answer = await _drawing(tmp_path).round_capacity_problem(25)

    assert (answer.problem, answer.remedy) == (
        "That would give the division 25 rounds, but the configured calendar template draws 24",
        ROUND_REMEDY,
    )


# ── The review's longest calendar (XIV.9) ─────────────────────────────────


@pytest.mark.parametrize("images", [True, False], ids=["module-on", "module-off"])
async def test_the_warning_says_what_will_happen_instead(tmp_path, images):
    """The calendar is posted as text rather than not at all, which is the difference between
    a degraded season and a broken one. The division holding the longest calendar is named, so
    a season of eight divisions knows which one to shorten. The review warns on the `calendar`
    aspect alone, whether or not the images module is on."""
    from leaguebot.core.services.image_drawing import Held

    answer = await _drawing(tmp_path, images=images).calendar_review_problem(Held("Premier", 25))

    assert (answer.problem, answer.remedy) == (
        "Calendar template draws 24 round(s); `Premier` holds 25. That division's calendar "
        "cannot be drawn as an image and will be posted as text",
        REVIEW_REMEDY,
    )


async def test_a_season_within_capacity_earns_no_warning(tmp_path):
    from leaguebot.core.services.image_drawing import Held

    assert await _drawing(tmp_path).calendar_review_problem(Held("Division 1", 20)) is None


# ── Where the questions stand aside ───────────────────────────────────────


async def test_the_guard_is_inert_while_the_image_module_is_off(tmp_path):
    """No graphic will be drawn, so there is no capacity to outgrow."""
    assert await _drawing(tmp_path, images=False).round_capacity_problem(100) is None


@QUESTIONS
async def test_the_guard_is_inert_while_the_calendar_aspect_is_off(tmp_path, question):
    """A league can run the image module and draw no calendar — the aspect is what decides
    whether this template is used at all."""
    assert await _ask(_drawing(tmp_path, aspect=False), question, 100) is None


@QUESTIONS
@pytest.mark.parametrize(
    "report",
    [None, SimpleNamespace(valid=False, resolved_path="/tmp/bad.svg", template_key=CALENDAR)],
    ids=["no_calendar_template", "invalid_template"],
)
async def test_a_missing_or_invalid_template_is_not_used_to_refuse(tmp_path, report, question):
    """A league with no calendar template has no capacity to outgrow. An invalid one's
    capacity cannot be trusted, and refusing a round on an unreadable number would block a
    league for a fault in their artwork rather than their calendar; the review's own template
    problems already name it."""
    assert await _ask(_drawing(tmp_path, report=report), question, 100) is None


# ── A fault is core's to catch ────────────────────────────────────────────


@QUESTIONS
@pytest.mark.parametrize("fault", ["unreadable template", "no rounds declared", "reports down"])
async def test_a_fault_reading_the_template_is_let_out_for_core_s_catch(
    tmp_path, fault, question
):
    """The image module does not decide that a fault lets the round through: it lets the
    fault out, and core's one catch logs it and lets the season and the round through. A
    template declaring no rounds cannot be counted, which is a fault like any other: a template
    that genuinely drew no rounds would be invalid."""
    from leaguebot.image.models.image_catalogues import CapacityError
    from leaguebot.image.utils.svg_document import SvgError

    expected: type[Exception]
    if fault == "unreadable template":
        drawing = _drawing(tmp_path, report=SimpleNamespace(
            valid=True, resolved_path=tmp_path / "missing.svg", template_key=CALENDAR,
        ))
        expected = SvgError
    elif fault == "no rounds declared":
        drawing = _drawing(tmp_path, rounds=0)
        expected = CapacityError
    else:
        drawing = _drawing(tmp_path, reports_error=RuntimeError("template service down"))
        expected = RuntimeError

    with pytest.raises(expected):
        await _ask(drawing, question, 100)
