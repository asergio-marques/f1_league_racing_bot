"""A preview names the grand prix, not the circuit (2026-09-01).

Every posting path reads ``race_name`` from the track registry — ``tracks.gp_name`` — and
never from the round, which records the circuit and not the event run on it.
``_race_name`` read the round instead, so all six previews that draw a race name drew the
circuit name under a heading naming the grand prix. On the check-in call, which names both
the grand prix and the circuit, it drew the circuit name twice.

The fault was invisible in the suite because nothing compared the preview's value against
the registry the postings read.
"""
from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest

from leaguebot.image.services import image_preview_service
from leaguebot.image.services.image_preview_service import (
    PreviewContext,
    _race_name,
)
from leaguebot.image.services.image_rsvp_service import MYSTERY_RACE_NAME

pytestmark = pytest.mark.asyncio


_REGISTRY = {
    "Silverstone Circuit": SimpleNamespace(
        name="Silverstone Circuit",
        gp_name="British Grand Prix",
        country="United Kingdom",
    ),
}


@pytest.fixture(autouse=True)
def _registry(monkeypatch):
    """The track registry the postings read, stubbed at its one source."""
    from leaguebot.core.services import calendar_post_service

    async def _tracks_by_name(_db_path):
        return dict(_REGISTRY)

    monkeypatch.setattr(calendar_post_service, "tracks_by_name", _tracks_by_name)


def _context(round_obj):
    return PreviewContext(
        season_number=1,
        division_id=1,
        division_name="Elite",
        division_tier=1,
        round=round_obj,
    )


def _bot():
    return SimpleNamespace(db_path=":memory:")


def _round(track_name="Silverstone Circuit", fmt="NORMAL"):
    return SimpleNamespace(round_number=1, track_name=track_name, format=fmt)


@pytest.mark.parametrize(
    ("round_kwargs", "expected"),
    [
        ({}, "British Grand Prix"),
        # ``race_name`` is mandatory on the check-in call; a preview does not empty it.
        ({"track_name": "Circuit Nowhere"}, "Circuit Nowhere"),
        ({"fmt": "MYSTERY"}, MYSTERY_RACE_NAME),
        ({"track_name": None}, ""),
    ],
    ids=["grand-prix", "unregistered-track", "mystery", "no-track"],
)
async def test_a_preview_draws_the_race_name_the_postings_read(round_kwargs, expected):
    """A round is named by its grand prix and not its circuit; a track the registry does
    not hold falls back to the circuit, a mystery round is named by the mystery literal,
    and a round with no track at all names nothing."""
    assert await _race_name(_bot(), _context(_round(**round_kwargs))) == expected


async def test_no_preview_is_left_reading_the_round_for_its_race_name():
    """The guard that would have caught this: no builder may pass the circuit through.

    A builder resolving ``race_name`` from anything but ``_race_name`` is reading the
    round again, which is the mistake this file exists to pin.
    """
    builders = [
        name
        for name in dir(image_preview_service)
        if name.startswith("build_") and name.endswith("_preview")
    ]
    offenders = []
    for name in sorted(builders):
        source = inspect.getsource(getattr(image_preview_service, name))
        for line in source.splitlines():
            stripped = line.strip()
            if stripped.startswith("race_name=") and "_race_name(" not in stripped:
                offenders.append(f"{name}: {stripped}")
    assert offenders == []
