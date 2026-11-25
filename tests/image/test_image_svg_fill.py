"""How the fill engine's own notices name the template a league is to put right."""
from __future__ import annotations

import pytest

from leaguebot.image.models.image_constants import NOTICE_CROP_POINT_OFF_CANVAS
from leaguebot.image.utils.svg_document import parse_svg_bytes
from leaguebot.image.utils.svg_fill import FillSpec, fill


def _crop_notice_detail(image_type: str) -> str:
    """Fill a template whose last crop point sits above its declared height, for ``image_type``,
    and hand back the detail of the crop notice it raises."""
    root = parse_svg_bytes(
        b'<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="675">'
        b'<rect id="crop_last" x="0" y="600" width="10" height="10"/></svg>'
    )
    result = fill(
        FillSpec(root=root, image_type=image_type, crop="crop_last", crop_is_final=True)
    )
    (notice,) = [
        n for n in result.notices if n.notice_kind == NOTICE_CROP_POINT_OFF_CANVAS
    ]
    return notice.detail


@pytest.mark.parametrize(
    ("image_type", "words"),
    [
        ("standings_drivers_template", "the drivers' standings template"),
        ("standings_constructors_template", "the constructors' standings template"),
        ("results_race_template", "the race results template"),
        ("weather_p2_sprint_template", "the phase 2 (sprint) weather template"),
        ("calendar_template", "the calendar template"),
        ("no_such_template", "the template"),
    ],
)
def test_a_crop_notice_names_a_two_part_template_as_a_league_says_it(image_type, words):
    """A two-part label is said as a league says it, "the race results template", never "the
    results — race template"; a one-part label reads as it stands, and a key the bot does not
    know reads "the template". The internal key never appears."""
    detail = _crop_notice_detail(image_type)

    assert f"but {words} declares a height of" in detail, detail
    assert " — " not in detail, detail
    assert image_type not in detail, detail
