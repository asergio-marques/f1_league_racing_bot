"""The bounded_autocomplete decorator — Discord's three-second budget, enforced.

Every test here drives the decorator with `asyncio.sleep` rather than a real query, and
keeps the deadline and the sleep an order of magnitude apart. The suite runs on a Raspberry
Pi, on `ubuntu-latest` and on `windows-latest`, and a tight timing assertion is precisely
the kind that passes where it was written and fails everywhere else.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from utils.autocomplete import bounded_autocomplete


async def test_a_fast_callback_returns_its_choices_unchanged():
    @bounded_autocomplete(deadline=1.0)
    async def fast(self, interaction, current):
        return ["Division 1", "Division 2"]

    assert await fast(None, None, "") == ["Division 1", "Division 2"]


async def test_a_callback_past_the_deadline_returns_no_choices():
    @bounded_autocomplete(deadline=0.05)
    async def slow(self, interaction, current):
        await asyncio.sleep(5.0)
        return ["never seen"]

    started = time.perf_counter()
    result = await slow(None, None, "")
    elapsed = time.perf_counter() - started

    assert result == []
    # Nearer the deadline than the sleep. Generous, because a loaded CI runner is not a
    # quiet development machine.
    assert elapsed < 2.0, f"gave up after {elapsed:.2f}s — the deadline was not enforced"


async def test_a_callback_past_the_deadline_is_left_to_finish():
    """The task is abandoned, never cancelled — and that is deliberate.

    aiosqlite queues its close() behind the running statement, so cancelling the coroutine
    would not stop the worker thread and would leave a half-closed connection. Letting the
    task run to completion lets aiosqlite tear itself down properly.
    """
    finished = asyncio.Event()

    @bounded_autocomplete(deadline=0.05)
    async def slow(self, interaction, current):
        await asyncio.sleep(0.2)
        finished.set()
        return ["late"]

    assert await slow(None, None, "") == []

    await asyncio.wait_for(finished.wait(), timeout=5.0)
    assert finished.is_set(), "the abandoned callback should have been allowed to finish"


async def test_a_raising_callback_returns_no_choices(caplog):
    @bounded_autocomplete(deadline=1.0)
    async def broken(self, interaction, current):
        raise RuntimeError("database is away")

    with caplog.at_level(logging.ERROR, logger="utils.autocomplete"):
        assert await broken(None, None, "") == []

    assert any("broken" in record.message for record in caplog.records)


async def test_a_slow_but_answered_callback_is_logged(caplog):
    @bounded_autocomplete(deadline=5.0, slow_after=0.01)
    async def sluggish(self, interaction, current):
        await asyncio.sleep(0.05)
        return ["ok"]

    with caplog.at_level(logging.WARNING, logger="utils.autocomplete"):
        assert await sluggish(None, None, "") == ["ok"]

    assert any(
        "within the" in record.message for record in caplog.records
    ), "a slow-but-successful call should warn, so a regression is visible before it fails"


async def test_an_overrun_is_logged(caplog):
    @bounded_autocomplete(deadline=0.05)
    async def slow(self, interaction, current):
        await asyncio.sleep(0.3)
        return []

    with caplog.at_level(logging.WARNING, logger="utils.autocomplete"):
        await slow(None, None, "")

    assert any("exceeded" in record.message for record in caplog.records)


async def test_cancellation_is_not_swallowed():
    """A cancelled autocomplete must propagate, not turn into an empty list."""
    started = asyncio.Event()

    @bounded_autocomplete(deadline=10.0)
    async def slow(self, interaction, current):
        started.set()
        await asyncio.sleep(10.0)
        return []

    task = asyncio.ensure_future(slow(None, None, ""))
    await asyncio.wait_for(started.wait(), timeout=5.0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


def test_the_wrapper_keeps_the_shape_discord_inspects():
    """discord.py reads __qualname__ and the signature to register a callback.

    It decides whether a callback is a bound method from `__qualname__`, and reads its
    parameters with `inspect.signature`. If a future refactor drops `functools.wraps`,
    registration breaks silently — this is the test that catches it.
    """

    async def _division_autocomplete(self, interaction, current):
        return []

    original_qualname = _division_autocomplete.__qualname__
    wrapped = bounded_autocomplete()(_division_autocomplete)

    assert wrapped.__qualname__ == original_qualname
    assert list(inspect.signature(wrapped).parameters) == [
        "self",
        "interaction",
        "current",
    ]
