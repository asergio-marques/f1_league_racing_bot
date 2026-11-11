"""The image module's answer to core's standings question at season review: can the two standings
templates draw the season? (040 US4, FR-043, FR-045, #283)

Both standings catalogues declare ``capacity=None``: their rows are counted from the template
file, not from a number in the catalogue, so ``declared_capacities()`` cannot see them.

Two ceilings, caught at two moments, as Constitution XIV.12 requires:

* **A driver assignment** that would take a division past the drivers template's rows is
  refused with the change unapplied. Core counts and refuses
  (`tests/core/test_placement_capacity_guards.py`); the image module measures the template
  (`tests/image/test_standings_drivers_capacity.py`).
* **`/season placements-review`** reports what either template could not draw for the season as a
  whole: the drivers rows, the constructors rows, and each template's round columns against
  the longest calendar. It is the only moment the *constructors* ceiling can be caught at
  all, because seating a driver adds no team. That half is this file's.

Core counts what the season holds and asks: the division seating the most drivers, the one
fielding the most race teams (the reserve team is no constructor, and is not counted) and the one
holding the most rounds. It words each answer as a ⚠️ line of the review and lets the review
through where the check cannot run (`tests/core/test_season_review_capacity.py`). Here the image
module measures the configured templates and answers the problems and their remedies, in the
order drivers rows, drivers round columns, constructors rows, constructors round columns.

**An aspect that is off is not checked,** and a template that is missing or invalid is Layer 2's
to report. A template that cannot be counted is skipped on its own, and the other is still
answered. Nothing here rasterises.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from lxml import etree


pytestmark = pytest.mark.asyncio

SVG_NS = "http://www.w3.org/2000/svg"
DRIVERS = "standings_drivers_template"
CONSTRUCTORS = "standings_constructors_template"
REMEDY = "Enlarge the template to draw it."

_ROW_SUFFIXES = ("position", "driver_name", "team_name", "points")


def _template_file(tmp_path, name, *, rows, rounds: int = 0):
    """A standings template declaring *rows* rows, each carrying *rounds* round cells. *rows* is
    a count, or the row numbers themselves where a gap is wanted."""
    numbers = range(1, rows + 1) if isinstance(rows, int) else rows
    root = etree.Element(f"{{{SVG_NS}}}svg")
    root.set("width", "1200")
    root.set("height", "675")
    for field in ("division_name", "round_number", "result_status"):
        etree.SubElement(root, f"{{{SVG_NS}}}text").set("id", field)
    for ordinal in range(1, rounds + 1):
        etree.SubElement(root, f"{{{SVG_NS}}}text").set("id", f"round_{ordinal}_number")
    for index in numbers:
        group = etree.SubElement(root, f"{{{SVG_NS}}}g")
        group.set("id", f"row_{index}_group")
        for suffix in _ROW_SUFFIXES:
            etree.SubElement(group, f"{{{SVG_NS}}}text").set(
                "id", f"row_{index}_{suffix}"
            )
        for ordinal in range(1, rounds + 1):
            cell = etree.SubElement(group, f"{{{SVG_NS}}}g")
            cell.set("id", f"row_{index}_round_{ordinal}_group")
            etree.SubElement(cell, f"{{{SVG_NS}}}text").set(
                "id", f"row_{index}_round_{ordinal}_feature_race_result"
            )

    path = tmp_path / f"{name}.svg"
    path.write_bytes(etree.tostring(root))
    return path


def _report(path, key, *, valid=True):
    return MagicMock(
        valid=valid, resolved_path=str(path) if valid else None, template_key=key
    )


def _drawing(reports, *, toggle=True):
    """The image module's implementation of core's protocol, over stand-ins for its own
    services: the `standings` aspect *toggle*, and *reports* as the templates in force."""
    from leaguebot.image.services.image_drawing import ImageDrawing

    modules = MagicMock()
    modules.is_images_enabled = AsyncMock(return_value=True)
    config = MagicMock()
    config.get_config = AsyncMock(return_value=MagicMock())
    config.get_toggles = AsyncMock(return_value={"standings": toggle})
    config.is_aspect_enabled = AsyncMock(side_effect=lambda aspect: toggle and aspect == "standings")
    validity = MagicMock()
    validity.template_reports = AsyncMock(return_value=reports)
    return ImageDrawing(
        modules=modules, config=config, validity=validity,
        renderer=MagicMock(), post_log=AsyncMock(), db_path="",
    )


def _held(name, count):
    from leaguebot.core.services.modules.image_drawing import Held

    return Held(name, count)


def _answers(problems):
    return [(answer.problem, answer.remedy) for answer in problems]


def _rows(label, name, rows, held):
    return (
        f"**{label}** draws {rows} row(s); `{name}` holds {held}. That division's standings "
        f"cannot be drawn as an image and will be posted as text",
        REMEDY,
    )


def _columns(label, name, columns, held):
    return (
        f"**{label}** draws {columns} round column(s); `{name}` holds {held}. That division's "
        f"standings cannot be drawn as an image and will be posted as text",
        REMEDY,
    )


# ── At the season review (FR-043, FR-045) ─────────────────────────────────


async def test_the_review_names_the_two_templates_apart_never_as_one_pair(tmp_path):
    """FR-045 — a manager told 'the standings template' would not know which to enlarge. Each
    ceiling reports its own template, its own count and the division it was counted in: the
    drivers rows against the division seating the most drivers, the constructors rows against
    the one fielding the most race teams, and each template's round columns against the one
    holding the most rounds."""
    drawing = _drawing({
        DRIVERS: _report(_template_file(tmp_path, DRIVERS, rows=2, rounds=2), DRIVERS),
        CONSTRUCTORS: _report(
            _template_file(tmp_path, CONSTRUCTORS, rows=3, rounds=3), CONSTRUCTORS
        ),
    })

    problems = await drawing.standings_review_problems(
        _held("Elite", 6), _held("Pro", 4), _held("Am", 5)
    )

    assert _answers(problems) == [
        _rows("Standings — drivers", "Elite", 2, 6),
        _columns("Standings — drivers", "Am", 2, 5),
        _rows("Standings — constructors", "Pro", 3, 4),
        _columns("Standings — constructors", "Am", 3, 5),
    ]


async def test_counts_the_templates_hold_exactly_are_no_problem(tmp_path):
    """A template drawing as many rows and round columns as the season holds draws it whole."""
    drawing = _drawing({
        DRIVERS: _report(_template_file(tmp_path, DRIVERS, rows=2, rounds=3), DRIVERS),
        CONSTRUCTORS: _report(
            _template_file(tmp_path, CONSTRUCTORS, rows=2, rounds=3), CONSTRUCTORS
        ),
    })

    assert await drawing.standings_review_problems(
        _held("Alpha", 2), _held("Alpha", 2), _held("Alpha", 3)
    ) == []


async def test_a_calendar_longer_than_the_round_columns_is_reported(tmp_path):
    drawing = _drawing(
        {DRIVERS: _report(_template_file(tmp_path, DRIVERS, rows=5, rounds=2), DRIVERS)}
    )

    problems = await drawing.standings_review_problems(
        _held("Alpha", 1), _held("Alpha", 1), _held("Alpha", 5)
    )

    assert _answers(problems) == [_columns("Standings — drivers", "Alpha", 2, 5)]


async def test_a_template_drawing_no_grid_at_all_is_not_an_overflow(tmp_path):
    """XIV.3 — a template that draws the classification alone is a legitimate choice."""
    drawing = _drawing(
        {DRIVERS: _report(_template_file(tmp_path, DRIVERS, rows=5, rounds=0), DRIVERS)}
    )

    assert await drawing.standings_review_problems(
        _held("Alpha", 1), _held("Alpha", 1), _held("Alpha", 20)
    ) == []


@pytest.mark.parametrize("valid", [None, False], ids=["missing", "invalid"])
async def test_an_invalid_template_is_left_to_the_layer_that_reports_it(tmp_path, valid):
    """Layer 2 reports a template missing or invalid, and reporting it here too would say it
    twice."""
    template = _template_file(tmp_path, DRIVERS, rows=1)
    report = None if valid is None else _report(template, DRIVERS, valid=False)
    drawing = _drawing({DRIVERS: report})

    assert await drawing.standings_review_problems(
        _held("Alpha", 99), _held("Alpha", 1), _held("Alpha", 1)
    ) == []


async def test_the_standings_are_not_checked_with_their_aspect_off(tmp_path):
    """No standings are drawn as images, so there is nothing for the templates to fall short
    of."""
    drawing = _drawing(
        {
            DRIVERS: _report(_template_file(tmp_path, DRIVERS, rows=1, rounds=1), DRIVERS),
            CONSTRUCTORS: _report(
                _template_file(tmp_path, CONSTRUCTORS, rows=1, rounds=1), CONSTRUCTORS
            ),
        },
        toggle=False,
    )

    assert await drawing.standings_review_problems(
        _held("Alpha", 9), _held("Alpha", 9), _held("Alpha", 9)
    ) == []


async def test_an_uncountable_template_is_skipped_and_the_other_still_answered(tmp_path):
    """A gap in the drivers template's row numbering cannot be counted: Layer 2 reports it, and
    the constructors template is still measured."""
    drawing = _drawing({
        DRIVERS: _report(_template_file(tmp_path, DRIVERS, rows=(1, 3)), DRIVERS),
        CONSTRUCTORS: _report(_template_file(tmp_path, CONSTRUCTORS, rows=2), CONSTRUCTORS),
    })

    problems = await drawing.standings_review_problems(
        _held("Alpha", 9), _held("Alpha", 4), _held("Alpha", 1)
    )

    assert _answers(problems) == [_rows("Standings — constructors", "Alpha", 2, 4)]
