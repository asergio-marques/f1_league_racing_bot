"""The cancelled-round overlay of the packaged calendar, verified on the PNG (#175).

A round called off keeps its place on the calendar and is veiled by its
``round_<x>_cancelled`` overlay; a round still to be raced loses the overlay and is drawn
as it always was. Read from the rasterised picture rather than the SVG, which is where a
veil drawn beneath the text instead of over it would show (XIV.14).
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from lxml import etree

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from services.image_calendar_service import build_fill_spec, resolve_drawing
from services.image_render_service import rasterise
from utils.svg_document import canvas_of
from utils.svg_fill import fill

DEFAULTS = Path(__file__).resolve().parents[2] / "resources" / "defaults"
TEMPLATE = DEFAULTS / "templates" / "calendar_template.svg"
#: The packaged asset folders, tracked and identical on every host; the league's own are
#: gitignored and hold whatever this machine carries.
DIRECTORIES = {"flag": DEFAULTS / "flags", "track": DEFAULTS / "tracks"}
TRACKS = {
    "Silverstone Circuit": NS(
        name="Silverstone Circuit", gp_name="British Grand Prix", country="United Kingdom"
    )
}

#: The head of the grand prix name in the packaged template's first row of cards: rounds 1
#: and 2 stand abreast, their cards spanning x 48–724 and 772–1448, the name set at y 320.
#: Kept left of and above the overlay's own word, which is red and centred on the card.
RACE_NAME_BAND = {1: (148, 298, 250, 316), 2: (872, 298, 974, 316)}


def _round(number: int, status: str = "NOT_RUN"):
    return NS(
        round_number=number,
        format="NORMAL",
        track_name="Silverstone Circuit",
        scheduled_at=datetime(2026, 6, 4 + 7 * (number - 1), 20, 0, tzinfo=timezone.utc),
        status=status,
    )


def _brightest(image, box) -> int:
    return max(max(pixel[:3]) for pixel in image.crop(box).getdata())


@pytest.mark.rasteriser
def test_a_cancelled_round_is_veiled_and_a_live_one_is_not(tmp_path):
    from PIL import Image

    root = etree.parse(str(TEMPLATE)).getroot()
    drawing = resolve_drawing(
        division_name="Elite",
        division_tier=1,
        season_number=1,
        rounds=[_round(1), _round(2, "CANCELLED")],
        tracks=TRACKS,
    )
    result = fill(build_fill_spec(drawing, root, asset_directories=DIRECTORIES))
    assert not result.unresolved, result.unresolved

    destination = tmp_path / "calendar_cancelled.png"
    rasterise(result.svg, destination, canvas_of(etree.fromstring(result.svg)))
    image = Image.open(destination).convert("RGB")

    # The live round's name is set in near-white; the veiled one's is dimmed to a shadow.
    assert _brightest(image, RACE_NAME_BAND[1]) > 200
    assert _brightest(image, RACE_NAME_BAND[2]) < 90
