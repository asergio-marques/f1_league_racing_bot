"""Pacing a whole-division rebuild so it does not spend its time in rate-limit backoff (#345).

The amendment replay reposts every round of a division across four channels — some tens of
messages where the ordinary posting path sends two or three. Discord's practical limit is around
five messages to a channel in five seconds.

**The throttle is not what makes the replay correct.** discord.py blocks on a 429 and retries
after the interval Discord names, so the messages arrive either way. What the throttle buys is
predictability: a rebuild that paces itself finishes in a time somebody can wait out, where one
that sprints into a rate limit stalls in bursts of unknown length, and does so in the middle of
a sequence that is holding two copies of every message until it completes.

It is a named constant so a test can stand it down. A suite that actually slept a second per
post would take longer than the whole of the rest of the run.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from leaguebot.results.services import results_post_service  # noqa: E402


#: The interval the module declares, captured at import — before `conftest`'s autouse fixture
#: sets it to zero for every other test in the suite. These are the tests that have to see the
#: real value, so they put it back.
DECLARED_INTERVAL = results_post_service.POSTING_THROTTLE_SECONDS


@pytest.fixture()
def real_interval(monkeypatch):
    """Undo the suite-wide stand-down, so the declared pacing is what is under test."""
    monkeypatch.setattr(
        results_post_service, "POSTING_THROTTLE_SECONDS", DECLARED_INTERVAL
    )


@pytest.fixture()
def no_sleep(monkeypatch):
    """Stand the wait down and record what it was asked for."""
    slept: list[float] = []

    async def _sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(results_post_service.asyncio, "sleep", _sleep)
    return slept


async def test_the_throttle_waits_the_configured_interval(real_interval, no_sleep):
    await results_post_service.throttle()

    assert no_sleep == [DECLARED_INTERVAL]


async def test_the_interval_is_inside_discords_practical_limit():
    """Five messages in five seconds is the ceiling; a second a message sits under it.

    Pinned as a range rather than a value so the constant can be tuned without a test edit,
    while a change that would put the replay back into rate-limit territory still fails here.
    """
    assert 0.5 <= DECLARED_INTERVAL <= 2.0


async def test_a_zero_interval_does_not_wait_at_all(monkeypatch):
    """Standing it down has to actually stand it down, or every test pays for it."""
    slept: list[float] = []

    async def _sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(results_post_service.asyncio, "sleep", _sleep)
    monkeypatch.setattr(results_post_service, "POSTING_THROTTLE_SECONDS", 0)

    await results_post_service.throttle()

    assert slept == []


async def test_the_constant_is_read_at_call_time(monkeypatch, no_sleep):
    """Read from the module, not captured at import, so patching it works from anywhere."""
    monkeypatch.setattr(results_post_service, "POSTING_THROTTLE_SECONDS", 0.25)

    await results_post_service.throttle()

    assert no_sleep == [0.25]


async def test_the_suite_runs_with_the_throttle_stood_down():
    """`conftest`'s autouse fixture is what keeps the suite fast, so it is pinned here.

    Without it every test that reposts a division waits a real second per posting;
    `test_repost_for_division` alone took eighteen seconds. A future edit that removes the
    fixture would not fail anything — it would only make the suite quietly slower, which is
    exactly the kind of regression nobody attributes to the commit that caused it.
    """
    assert results_post_service.POSTING_THROTTLE_SECONDS == 0
