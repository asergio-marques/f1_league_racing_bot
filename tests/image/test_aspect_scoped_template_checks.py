"""A broken template blocks only where the output that draws it is switched on.

One rule, applied by every surface that judges a template:

  * aspect **on**, template broken → blocks. The output would post nothing.
  * aspect **off**, template broken → a warning, never a block. The output posts as
    text and reaches no drawing at all.
  * switching an aspect **on** with its template broken → refused, and the aspect stays
    off, so an unusable output is never stored.
  * naming a template folder → judged on the switched-on outputs alone.

Until this held, `check_all_templates` surveyed all fifteen regardless, and every surface
blocked on the lot: a league that had never switched verdicts on was refused approval over
a verdicts drawing it had no use for, and told its image module was "not correctly
configured". That is the reported fault this file pins.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from unittest.mock import AsyncMock, MagicMock

import pytest

from leaguebot.image.models.image_constants import ASPECTS, ASPECT_TEMPLATES, TEMPLATE_COLUMNS
from leaguebot.framework.services.switches.module_service import SwitchNotSignedUp
from leaguebot.image.services.render.image_drawing import ImageDrawing
from leaguebot.image.services.validity.image_validity_service import (
    blocking_template_problems,
    check_all_templates,
    templates_of_enabled_aspects,
)
from tests.support.switches import answer_switch

DEFAULT_TEMPLATES = os.path.join(
    os.path.dirname(__file__), "..", "..", "resources", "defaults", "templates"
)


# ── The map from aspects to templates ─────────────────────────────────────


def test_no_aspect_switched_on_wants_no_template():
    assert templates_of_enabled_aspects({aspect: False for aspect in ASPECTS}) == set()


def test_every_aspect_switched_on_wants_all_sixteen():
    wanted = templates_of_enabled_aspects({aspect: True for aspect in ASPECTS})

    assert wanted == set(TEMPLATE_COLUMNS)
    assert len(wanted) == 16


@pytest.mark.parametrize("aspect", ASPECTS)
def test_one_aspect_wants_exactly_its_own_templates(aspect):
    """Read from ASPECT_TEMPLATES, so a tenth aspect is covered the day it is added."""
    assert templates_of_enabled_aspects({aspect: True}) == set(ASPECT_TEMPLATES[aspect])


def test_an_absent_toggle_is_read_as_off():
    """A column a migration has not yet written must not be read as enabled."""
    assert templates_of_enabled_aspects({}) == set()


# ── Blocking, against a real template directory ───────────────────────────


@pytest.fixture
def config_missing_verdicts(tmp_path, monkeypatch):
    """A real configuration whose folder holds every template but the verdicts one.

    Built under the project root, because the directory check refuses a path outside it
    and would report fifteen faults rather than the one under test. The folder is named afresh
    for each use, so that parallel workers running the tests that use it do not build, fill and
    remove one folder together.
    """
    from leaguebot.image.services.validity.image_validity_service import evaluate_all_templates  # noqa: F401

    root = os.path.join(os.path.dirname(__file__), "..", "..")
    folder = tempfile.mkdtemp(
        prefix="_test_templates_missing_verdicts_", dir=os.path.join(root, "resources")
    )
    for name in os.listdir(DEFAULT_TEMPLATES):
        if "verdicts" in name:
            continue
        shutil.copy(os.path.join(DEFAULT_TEMPLATES, name), folder)

    from leaguebot.image.models.image_module import ImageConfig
    import dataclasses

    fields = {f.name for f in dataclasses.fields(ImageConfig)}
    values = {name: None for name in fields}
    values.update(template_directory=f"resources/{os.path.basename(folder)}")
    for column, default in TEMPLATE_COLUMNS.items():
        if column in fields:
            values[column] = default
    from leaguebot.image.models.image_constants import ASSET_DIRECTORIES

    for column, (_, default_dir, _packaged) in ASSET_DIRECTORIES.items():
        if column in fields:
            values[column] = default_dir

    yield ImageConfig(**values)

    shutil.rmtree(folder, ignore_errors=True)


def test_the_survey_still_finds_the_broken_template(config_missing_verdicts):
    """Nothing is hidden — the fault is found, and the surfaces report it as a warning."""
    problems = check_all_templates(config_missing_verdicts)

    assert [p.template_key for p in problems] == ["verdicts_template"]


def test_it_does_not_block_while_its_aspect_is_off(config_missing_verdicts):
    """The reported fault: a season refused over a drawing nothing would ever post."""
    toggles = {aspect: aspect != "verdicts" for aspect in ASPECTS}

    assert blocking_template_problems(config_missing_verdicts, toggles) == []


def test_it_blocks_the_moment_its_aspect_is_on(config_missing_verdicts):
    toggles = {aspect: True for aspect in ASPECTS}

    problems = blocking_template_problems(config_missing_verdicts, toggles)

    assert [p.template_key for p in problems] == ["verdicts_template"]


def test_an_unrelated_aspect_being_on_changes_nothing(config_missing_verdicts):
    """Only the aspect that draws the template decides, not how many others are on."""
    toggles = {aspect: False for aspect in ASPECTS}
    toggles["calendar"] = True

    assert blocking_template_problems(config_missing_verdicts, toggles) == []


def test_nothing_blocks_when_every_output_is_off(config_missing_verdicts):
    """A league posting everything as text is stopped by no drawing whatsoever."""
    toggles = {aspect: False for aspect in ASPECTS}

    assert blocking_template_problems(config_missing_verdicts, toggles) == []


# ── Whether an aspect could attach a file, asked at aspect grain (#187) ───
#
# The pre-flight of an amendment asks whether the bot will need Attach Files on a
# division's channels. That is a different question from "will this graphic draw", and the
# tests below exist to keep it different — see `aspect_attaches_files`' docstring.


def _drawing(*, module_on=True, toggles=None, reports=None):
    """The image drawing over stub services answering the two readers the question uses, and
    a third it must not."""
    toggles = toggles or {}
    modules = MagicMock()
    answer_switch(modules, "images", module_on)
    config = MagicMock()
    config.get_toggles = AsyncMock(return_value=toggles)
    config.is_aspect_enabled = AsyncMock(side_effect=lambda aspect: toggles.get(aspect, False))
    validity = MagicMock()
    # Every template broken. Nothing here may change the answer.
    validity.template_reports = AsyncMock(return_value=reports or {})
    return ImageDrawing(
        modules, config, validity, renderer=MagicMock(), post_log=AsyncMock(), db_path=":unused:",
    )


async def test_an_aspect_that_is_off_could_not():
    assert not await _drawing(toggles={"standings": False}).aspect_attaches_files("standings")


async def test_an_aspect_absent_from_the_toggles_could_not():
    """A server with no configuration row attaches nothing, rather than raising."""
    assert not await _drawing(toggles={}).aspect_attaches_files("standings")


async def test_the_module_being_off_settles_it_whatever_the_toggles_say():
    """A toggle set before the module was switched off still reads as off."""
    drawing = _drawing(module_on=False, toggles={"standings": True})

    assert not await drawing.aspect_attaches_files("standings")
    drawing._config.is_aspect_enabled.assert_not_awaited()


async def test_each_aspect_is_asked_for_separately():
    """Standings being on says nothing about the results channel needing Attach Files."""
    drawing = _drawing(toggles={"standings": True, "results": False})

    assert await drawing.aspect_attaches_files("standings")
    assert not await drawing.aspect_attaches_files("results")


async def test_calendar_and_lineup_attach_files_follow_their_aspect():
    """Core's channel checks ask after the calendar and lineup channels by name: each answer
    follows its own aspect, and the module being off settles both."""
    calendar_on = _drawing(toggles={"calendar": True, "lineup": False})
    assert await calendar_on.calendar_attaches_files()
    assert not await calendar_on.lineup_attaches_files()

    lineup_on = _drawing(toggles={"calendar": False, "lineup": True})
    assert not await lineup_on.calendar_attaches_files()
    assert await lineup_on.lineup_attaches_files()

    module_off = _drawing(module_on=False, toggles={"calendar": True, "lineup": True})
    assert not await module_off.calendar_attaches_files()
    assert not await module_off.lineup_attaches_files()


async def test_template_validity_is_deliberately_not_consulted():
    """**The guard on the decision, not an incidental assertion.**

    This question is one template check away from looking exactly like the gate every
    picture asks, ``aspect_wanted`` with a template, and a later reader who notices the
    difference will be tempted to close it. They must not: template validity moves whenever a
    league edits its own artwork, so a permission requirement built on it would appear and
    vanish with the SVGs and refuse the same amendment on one day and accept it the next for
    reasons having nothing to do with the permission.

    Asserting that ``template_reports`` is never awaited is what makes adding the check
    fail here rather than a year later in a league's server.
    """
    drawing = _drawing(toggles={"standings": True}, reports={})

    assert await drawing.aspect_attaches_files("standings"), (
        "a broken template must not stop the channel being asked for Attach Files"
    )
    drawing._validity.template_reports.assert_not_awaited()


async def test_a_switch_nobody_signed_up_for_answers_no_and_is_left_to_the_job():
    """A module switch nobody signed up for is a fault in the bot's assembly. The pre-flight is
    not where it is raised: it answers no, and the picture's own gate, asked when the job draws,
    raises it and stops the job (`test_a_sheet_whose_images_switch_nobody_signed_up_for_stops_its_job`)."""
    drawing = _drawing(toggles={"standings": True})
    answer_switch(drawing._modules, "images", SwitchNotSignedUp("images"))

    assert not await drawing.aspect_attaches_files("standings")


async def test_a_reader_that_raises_answers_no_rather_than_raising():
    """The callers are pre-flight checks that must refuse cleanly or not at all.

    An exception here would come out of a check whose whole purpose is to produce a
    readable refusal, and Attach Files is only ever an extra requirement on top of the
    three a posting needs anyway — so under-reporting is the safe way to fail.
    """
    drawing = _drawing(toggles={"standings": True})
    drawing._config.is_aspect_enabled = AsyncMock(side_effect=RuntimeError("no config"))

    assert not await drawing.aspect_attaches_files("standings")
