"""The one wording every picture's remedy reuses, chosen by where its fault lies (#584).

A line or reply that says what puts a picture right says it by the picture's `FaultSource`:
for the league's own fault, the template remedy the line already gives; for a fault in the bot,
that the fault is the bot's, that its detail is in the host's log, and what posts the picture
once it is cleared; for the converter's absence, that the host must install Inkscape, and what
posts the picture once it has. A line with nothing to run ends with its own tail instead.
"""
from __future__ import annotations

import pytest

TEMPLATE = "fix the results template, then run `/results sync`"
THEN = "run `/results sync`"
OTHERWISE = "no command posts the picture again, and the text stands"
BOT = "the fault is in the bot, and its detail is in the host's log"
HOST = "Inkscape is not installed on the machine running the bot, and the host must install it"


@pytest.mark.xfail(strict=True, reason="#584: core has no picture_remedy yet")
@pytest.mark.parametrize(
    ("source", "then", "otherwise", "expected"),
    [
        pytest.param("LEAGUE", THEN, None, TEMPLATE, id="league-then"),
        pytest.param("LEAGUE", None, OTHERWISE, TEMPLATE, id="league-otherwise"),
        pytest.param("BOT", THEN, None, f"{BOT}; once it is cleared, {THEN}", id="bot-then"),
        pytest.param("BOT", None, OTHERWISE, f"{BOT}; {OTHERWISE}", id="bot-otherwise"),
        pytest.param("HOST", THEN, None, f"{HOST}; once it has, {THEN}", id="host-then"),
        pytest.param("HOST", None, OTHERWISE, f"{HOST}; {OTHERWISE}", id="host-otherwise"),
    ],
)
def test_a_picture_remedy_reads_by_where_the_fault_lies(source, then, otherwise, expected):
    from leaguebot.core.models.fault_source import FaultSource
    from leaguebot.core.services.modules.image_drawing import picture_remedy

    remedy = picture_remedy(
        FaultSource[source], template=TEMPLATE, then=then, otherwise=otherwise
    )

    assert remedy == expected


@pytest.mark.xfail(strict=True, reason="#584: core has no CONVERTER_ABSENT yet")
def test_the_converter_core_names_is_the_one_the_image_module_runs():
    """Core cannot import the image module, so it carries the converter's name as a copy."""
    from leaguebot.core.services.modules.image_drawing import CONVERTER_ABSENT
    from leaguebot.image.services.render.image_render_service import CONVERTER_NAME

    assert CONVERTER_ABSENT == HOST
    assert CONVERTER_ABSENT.startswith(f"{CONVERTER_NAME} ")
