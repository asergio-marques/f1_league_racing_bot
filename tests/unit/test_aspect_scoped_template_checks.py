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
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from models.image_constants import ASPECTS, ASPECT_TEMPLATES, TEMPLATE_COLUMNS  # noqa: E402
from services.image_validity_service import (  # noqa: E402
    blocking_template_problems,
    check_all_templates,
    templates_of_enabled_aspects,
)

DEFAULT_TEMPLATES = os.path.join(
    os.path.dirname(__file__), "..", "..", "resources", "defaults", "templates"
)


# ── The map from aspects to templates ─────────────────────────────────────


def test_no_aspect_switched_on_wants_no_template():
    assert templates_of_enabled_aspects({aspect: False for aspect in ASPECTS}) == set()


def test_every_aspect_switched_on_wants_all_fifteen():
    wanted = templates_of_enabled_aspects({aspect: True for aspect in ASPECTS})

    assert wanted == set(TEMPLATE_COLUMNS)
    assert len(wanted) == 15


@pytest.mark.parametrize("aspect", ASPECTS)
def test_one_aspect_wants_exactly_its_own_templates(aspect):
    """Read from ASPECT_TEMPLATES, so a ninth aspect is covered the day it is added."""
    assert templates_of_enabled_aspects({aspect: True}) == set(ASPECT_TEMPLATES[aspect])


def test_an_absent_toggle_is_read_as_off():
    """A column a migration has not yet written must not be read as enabled."""
    assert templates_of_enabled_aspects({}) == set()


# ── Blocking, against a real template directory ───────────────────────────


@pytest.fixture
def config_missing_verdicts(tmp_path, monkeypatch):
    """A real configuration whose folder holds every template but the verdicts one.

    Built under the project root, because the directory check refuses a path outside it
    and would report fifteen faults rather than the one under test.
    """
    from services.image_validity_service import evaluate_all_templates  # noqa: F401

    root = os.path.join(os.path.dirname(__file__), "..", "..")
    folder = os.path.join(root, "resources", "_test_templates_missing_verdicts")
    shutil.rmtree(folder, ignore_errors=True)
    os.makedirs(folder)
    for name in os.listdir(DEFAULT_TEMPLATES):
        if "verdicts" in name:
            continue
        shutil.copy(os.path.join(DEFAULT_TEMPLATES, name), folder)

    from models.image_module import ImageConfig
    import dataclasses

    fields = {f.name for f in dataclasses.fields(ImageConfig)}
    values = {name: None for name in fields}
    values.update(
        server_id=1,
        template_directory="resources/_test_templates_missing_verdicts",
    )
    for column, default in TEMPLATE_COLUMNS.items():
        if column in fields:
            values[column] = default
    from models.image_constants import ASSET_DIRECTORIES

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
