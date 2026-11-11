"""The circuit autocomplete shared by `/round add` and `/round amend`.

It used to be two byte-identical callbacks with no test between them. They are now one,
registered against both parameters — so the pair cannot drift, and the behaviour is pinned
here for the first time.
"""

from __future__ import annotations

import asyncio
import os
import time

import pytest

from leaguebot.core.cogs.round_cog import RoundCog
from leaguebot.core.db.database import AUTOCOMPLETE_TIMEOUT_SECONDS, get_connection, run_migrations


class _Interaction:
    def __init__(self, guild_id: int = 1) -> None:
        self.guild_id = guild_id


@pytest.fixture
async def cog(tmp_path):
    """A RoundCog wired to a real, migrated database — tracks come from migration 029."""
    from unittest.mock import MagicMock

    db_path = str(tmp_path / "bot.db")
    await run_migrations(db_path)

    bot = MagicMock()
    bot.db_path = db_path
    return RoundCog(bot)


async def test_both_track_parameters_share_one_autocomplete():
    """One callback, registered twice — the guard against the two copies returning.

    Read off the commands themselves rather than the class, because what matters is what
    discord.py will actually invoke.
    """
    add = RoundCog.round_add
    amend = RoundCog.round_amend

    add_cb = add._params["track"].autocomplete
    amend_cb = amend._params["track"].autocomplete

    assert add_cb is not None, "/round add lost its track autocomplete"
    assert amend_cb is not None, "/round amend lost its track autocomplete"
    assert add_cb is amend_cb, "the two commands should share one callback, not copy it"


async def test_it_offers_the_seeded_circuits(cog):
    choices = await cog._track_autocomplete(_Interaction(), "")

    assert choices, "migration 029 seeds 28 circuits; none were offered"
    # Every choice submits a track name and shows an id-prefixed label.
    assert all(choice.value for choice in choices)
    assert all("–" in choice.name for choice in choices)


async def test_matching_ignores_case(cog):
    everything = await cog._track_autocomplete(_Interaction(), "")
    a_name = sorted(choice.value for choice in everything)[0]

    lower = await cog._track_autocomplete(_Interaction(), a_name.lower())
    upper = await cog._track_autocomplete(_Interaction(), a_name.upper())

    assert [c.value for c in lower] == [c.value for c in upper]
    assert a_name in [c.value for c in lower]


async def test_the_offer_is_capped_at_what_discord_accepts(cog):
    """28 circuits are seeded and Discord takes 25 — the cap is reachable, not theoretical."""
    choices = await cog._track_autocomplete(_Interaction(), "")

    assert len(choices) == 25


async def test_a_database_failure_offers_no_choices_rather_than_breaking_the_command(cog):
    """Previously unguarded: a locked or missing database propagated out of the callback."""
    cog.bot.db_path = os.path.join(os.sep, "no", "such", "directory", "bot.db")

    assert await cog._track_autocomplete(_Interaction(), "") == []


async def test_an_autocomplete_that_hangs_offers_nothing(cog, monkeypatch):
    """The deadline applies here too — a late answer is worse than an empty one.

    The hung read is let go once the answer is checked, and the callback left running past the
    deadline is waited for, so its connection closes within the test. Left hanging, it held a
    database thread open past the test's call, and the suite's teardown waited out its whole
    grace period for it (`tests/support/change_queue.py`, `THREAD_GRACE_SECONDS`)."""
    import leaguebot.core.services.rounds.track_service as track_service

    released = asyncio.Event()

    async def _hang(_db):
        await released.wait()
        return []

    monkeypatch.setattr(track_service, "get_all_tracks", _hang)

    started = time.perf_counter()
    result = await cog._track_autocomplete(_Interaction(), "")
    elapsed = time.perf_counter() - started

    assert result == []
    assert elapsed < 3.0, f"took {elapsed:.2f}s — past Discord's budget"

    released.set()
    left_running = asyncio.all_tasks() - {asyncio.current_task()}
    await asyncio.wait(left_running, timeout=5)


async def test_it_asks_the_service_with_a_lock_wait_short_enough_to_answer_in_time(
    cog, monkeypatch
):
    """The tracks are asked of the service by the database's path, with the autocomplete's
    shorter lock wait: waiting the ordinary five seconds on a contended database would
    overshoot Discord's three-second budget and answer into an expired token."""
    import leaguebot.core.services.rounds.track_service as track_service

    seen = {}

    async def _capture(db_path, *, timeout=None):
        seen["read"] = (db_path, timeout)
        return []

    monkeypatch.setattr(track_service, "list_tracks", _capture)

    await cog._track_autocomplete(_Interaction(), "")

    assert seen["read"] == (cog.bot.db_path, AUTOCOMPLETE_TIMEOUT_SECONDS)
