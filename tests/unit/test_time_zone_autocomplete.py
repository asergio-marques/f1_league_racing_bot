"""The time-zone autocomplete and the zone list behind it.

Previously it rebuilt `sorted(available_timezones())` on every keystroke — a walk of the
whole `/usr/share/zoneinfo` tree, measured at 325 ms cold on the Raspberry Pi the bot runs
on — and it carried no error guard at all. Both are covered here.
"""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

import cogs.image_cog as image_cog  # noqa: E402
import utils.timezones as timezones  # noqa: E402
from cogs.image_cog import ImageCog, clear_zone_cache  # noqa: E402


class _Interaction:
    def __init__(self, guild_id: int = 1) -> None:
        self.guild_id = guild_id


@pytest.fixture
def cog():
    """A cog with a fresh zone cache, dropped again afterwards.

    The cache lives for the life of the process, so a test that fills it would otherwise
    leak into the next one.
    """
    clear_zone_cache()
    yield ImageCog(SimpleNamespace())
    clear_zone_cache()


async def test_the_zone_list_is_built_once(cog, monkeypatch):
    """The scan is the expensive part, and it must not happen per keystroke."""
    calls = {"n": 0}

    def _counted():
        calls["n"] += 1
        return {"Europe/Lisbon", "America/New_York", "Asia/Tokyo"}

    monkeypatch.setattr(timezones, "available_timezones", _counted)
    clear_zone_cache()

    await cog._time_zone_autocomplete(_Interaction(), "eur")
    await cog._time_zone_autocomplete(_Interaction(), "ame")
    await cog._time_zone_autocomplete(_Interaction(), "asi")

    assert calls["n"] == 1, f"scanned the zone tree {calls['n']} times, expected once"


async def test_the_cache_can_be_dropped(cog, monkeypatch):
    calls = {"n": 0}

    def _counted():
        calls["n"] += 1
        return {"Europe/Lisbon"}

    monkeypatch.setattr(timezones, "available_timezones", _counted)
    clear_zone_cache()

    await cog._time_zone_autocomplete(_Interaction(), "")
    clear_zone_cache()
    await cog._time_zone_autocomplete(_Interaction(), "")

    assert calls["n"] == 2


async def test_a_substring_matches_whatever_the_case(cog):
    lower = await cog._time_zone_autocomplete(_Interaction(), "lisbon")
    upper = await cog._time_zone_autocomplete(_Interaction(), "LISBON")

    assert [c.value for c in lower] == [c.value for c in upper]
    assert "Europe/Lisbon" in [c.value for c in lower]


async def test_at_most_twenty_five_zones_are_offered(cog):
    """There are several hundred zones and Discord accepts 25."""
    choices = await cog._time_zone_autocomplete(_Interaction(), "")

    assert len(choices) == 25


async def test_the_offer_is_deterministic(cog):
    """Sorted, not whatever order the filesystem yielded.

    `available_timezones()` returns a set, so without sorting the 25 offered would differ
    between hosts and between runs — and the suite runs on three different platforms.
    """
    first = [c.value for c in await cog._time_zone_autocomplete(_Interaction(), "")]
    clear_zone_cache()
    second = [c.value for c in await cog._time_zone_autocomplete(_Interaction(), "")]

    assert first == second
    assert first == sorted(first)


async def test_a_failure_building_the_list_offers_no_choices(cog, monkeypatch):
    """It had no error guard before; the decorator supplies one."""

    def _raise():
        raise OSError("zoneinfo is unreadable")

    monkeypatch.setattr(timezones, "available_timezones", _raise)
    clear_zone_cache()

    assert await cog._time_zone_autocomplete(_Interaction(), "") == []
