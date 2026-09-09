"""The `/images test verdict-banner` preview (052).

Two things distinguish it from every other preview and are pinned here:

* it **fabricates nothing** — a banner draws the season, the division and the round, and
  the server already holds all three;
* it **names nobody**, so it draws on a server that has signed up not one driver, where the
  verdict card beside it opens on ``context.drivers[0]`` and returns nothing at all.

The grand prix and the country are read through the shared helpers, so a preview cannot
disagree with what the bot will post.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from services.image_preview_service import (  # noqa: E402
    PreviewContext,
    build_verdict_banner_preview,
    build_verdict_preview,
)
from utils.svg_document import parse_svg_bytes  # noqa: E402

pytestmark = pytest.mark.asyncio

_REGISTRY = {
    "Silverstone Circuit": SimpleNamespace(
        name="Silverstone Circuit",
        gp_name="British Grand Prix",
        country="United Kingdom",
    ),
}

TEMPLATE = (
    b'<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="304">'
    b'<text id="division_name">D</text>'
    b'<text id="round_number">1</text>'
    b'<g id="season_number_group"><text id="season_number">1</text></g>'
    b'<g id="race_name_group"><text id="race_name">R</text></g>'
    b'<g id="track_flag_group"><image id="track_flag" width="150" height="100"/></g>'
    b"</svg>"
)


@pytest.fixture(autouse=True)
def _registry(monkeypatch):
    from services import calendar_post_service

    async def _tracks_by_name(_db_path):
        return dict(_REGISTRY)

    monkeypatch.setattr(calendar_post_service, "tracks_by_name", _tracks_by_name)


def _bot():
    return SimpleNamespace(db_path=":memory:")


def _round(track_name="Silverstone Circuit", fmt="NORMAL"):
    return SimpleNamespace(round_number=8, track_name=track_name, format=fmt)


def _context(round_obj=None, **overrides):
    values = dict(
        server_id=1,
        season_number=5,
        division_id=1,
        division_name="Elite",
        division_tier=1,
        round=round_obj if round_obj is not None else _round(),
    )
    values.update(overrides)
    return PreviewContext(**values)


async def _spec(context):
    (_title, _key, builder), = await build_verdict_banner_preview(_bot(), context)
    return builder(parse_svg_bytes(TEMPLATE))


async def test_one_picture_is_drawn_and_it_names_its_own_template():
    requests = await build_verdict_banner_preview(_bot(), _context())
    assert len(requests) == 1
    title, key, _builder = requests[0]
    assert title == "Verdict banner"
    assert key == "verdict_banner_template"


async def test_the_preview_draws_the_grand_prix_and_not_the_circuit():
    spec = await _spec(_context())
    assert spec.text["race_name"] == "British Grand Prix"
    assert spec.image_data["track_flag"] == ("flag", "United Kingdom")


async def test_the_preview_draws_the_leagues_own_season_division_and_round():
    """Nothing here is fabricated: every value is one the server holds."""
    spec = await _spec(_context())
    assert spec.text["division_name"] == "Elite"
    assert spec.text["round_number"] == "8"
    assert spec.text["season_number"] == "5"


async def test_it_draws_on_a_server_with_no_driver_signed_up():
    """The card beside it cannot, which is why the banner is roster-free."""
    context = _context(drivers=[])
    assert await build_verdict_banner_preview(_bot(), context) != []
    assert await build_verdict_preview(_bot(), context) == []


async def test_a_mystery_round_keeps_the_phrase_and_drops_the_flag():
    from services.image_rsvp_service import MYSTERY_RACE_NAME

    spec = await _spec(_context(_round(fmt="MYSTERY")))
    assert spec.text["race_name"] == MYSTERY_RACE_NAME
    assert "track_flag_group" in spec.remove


async def test_no_round_draws_nothing_rather_than_raising():
    assert await build_verdict_banner_preview(_bot(), _context(round=None)) == []
