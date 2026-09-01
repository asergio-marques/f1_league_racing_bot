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
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from services import image_preview_service  # noqa: E402
from services.image_preview_service import (  # noqa: E402
    PreviewContext,
    _race_name,
)
from services.image_rsvp_service import MYSTERY_RACE_NAME  # noqa: E402

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
    from services import calendar_post_service

    async def _tracks_by_name(_db_path):
        return dict(_REGISTRY)

    monkeypatch.setattr(calendar_post_service, "tracks_by_name", _tracks_by_name)


def _context(round_obj):
    return PreviewContext(
        server_id=1,
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


async def test_a_round_is_named_by_its_grand_prix_and_not_its_circuit():
    assert await _race_name(_bot(), _context(_round())) == "British Grand Prix"


async def test_a_track_the_registry_does_not_hold_falls_back_to_the_circuit():
    """``race_name`` is mandatory on the check-in call; a preview does not empty it."""
    name = await _race_name(_bot(), _context(_round(track_name="Circuit Nowhere")))
    assert name == "Circuit Nowhere"


async def test_a_mystery_round_is_named_by_the_mystery_literal():
    assert await _race_name(_bot(), _context(_round(fmt="MYSTERY"))) == MYSTERY_RACE_NAME


async def test_a_round_with_no_track_at_all_names_nothing():
    assert await _race_name(_bot(), _context(_round(track_name=None))) == ""


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
