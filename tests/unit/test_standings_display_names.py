"""Resolving the names the standings are ordered and posted under.

Issue #208. `results_post_service.py`'s name resolution is small, easy to read as redundant, and
carries a decision that a later reader would almost certainly try to optimise away.

**The standings are computed twice, deliberately.** The final tiebreak orders two entries level
on everything else alphabetically by driver; the names are resolved from Discord by user id; so
the roster has to be known before it can be ordered. The first pass is read only for *who is in
it*, the second is the one that counts. `test_the_standings_are_computed_twice_to_order_by_name`
is the test that stops the second call being removed as a duplicate — and removing it would not
fail anything else, because the first pass returns a perfectly plausible order.

**With nothing to resolve a name from, a full tie falls back to user id.** That is not a
degradation to be fixed: it is deterministic, which is what the ordering needs above all, and
it is what happens in every context with no guild in scope.

**A name is whatever resolves at the moment of computing, and no earlier one is kept** (decided
2026-09-15). A driver renamed mid-season therefore moves among the entries they are tied with,
and a round reposted after the rename can order such a pair the other way about than when it
was first published. That is accepted rather than worked around — preserving the old order
would mean the standings reading a name nobody is called any more.

**`standings_display_names` resolves across the division, not per round**, so a cascade rewriting
every round orders them all on one resolution. The roster only grows as a season runs, so the
division's last round holds every driver an earlier one could — which is why the query takes the
highest round number and why it skips cancelled rounds, whose roster is not the season's.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations  # noqa: E402
from services.results_post_service import (  # noqa: E402
    _build_member_display,
    _build_test_driver_display,
    driver_standings_for_display,
    standings_display_names,
)

SERVER_ID = 11608
SEASON_ID = 1
DIVISION_ID = 11
DRIVER_A = 4001
DRIVER_B = 4002


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _make_db(tmp_path, *, rounds=((1, "FINAL"),)) -> str:
    db_path = os.path.join(str(tmp_path), "display_names.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 900, 100, 101)",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO seasons (id, server_id, season_number, start_date, status) "
            "VALUES (?, ?, 1, '2026-01-01', 'ACTIVE')",
            (SEASON_ID, SERVER_ID),
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
            "VALUES (?, ?, 'Division 1', 1, 555)",
            (DIVISION_ID, SEASON_ID),
        )
        for number, status in rounds:
            await db.execute(
                "INSERT INTO rounds (id, division_id, round_number, format, track_name, "
                "scheduled_at, status) "
                "VALUES (?, ?, ?, 'NORMAL', 'Silverstone Circuit', '2026-06-01', ?)",
                (number, DIVISION_ID, number, status),
            )
        await db.commit()
    return db_path


async def _add_test_driver(db_path: str, user_id: int, name: str | None) -> None:
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO driver_profiles "
            "(server_id, discord_user_id, current_state, is_test_driver, test_display_name) "
            "VALUES (?, ?, 'ACTIVE', 1, ?)",
            (SERVER_ID, str(user_id), name),
        )
        await db.commit()


async def _add_real_driver(db_path: str, user_id: int) -> None:
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO driver_profiles "
            "(server_id, discord_user_id, current_state, is_test_driver) "
            "VALUES (?, ?, 'ACTIVE', 0)",
            (SERVER_ID, str(user_id)),
        )
        await db.commit()


def _snap(user_id: int):
    return SimpleNamespace(driver_user_id=user_id)


def _guild(members: dict[int, str] | None = None, *, fetchable: dict | None = None):
    guild = MagicMock()
    resolved = members or {}

    def _get(uid):
        name = resolved.get(uid)
        if name is None:
            return None
        member = MagicMock()
        member.display_name = name
        return member

    guild.get_member = MagicMock(side_effect=_get)

    async def _fetch(uid):
        name = (fetchable or {}).get(uid)
        if name is None:
            import discord

            raise discord.NotFound(MagicMock(), "unknown member")
        member = MagicMock()
        member.display_name = name
        return member

    guild.fetch_member = AsyncMock(side_effect=_fetch)
    return guild


# ---------------------------------------------------------------------------
# Test driver names
# ---------------------------------------------------------------------------


async def test_a_test_driver_is_shown_with_their_display_name(tmp_path):
    """Test drivers share one Discord account, so the mention alone cannot tell them
    apart — the name is the only thing distinguishing a grid of them."""
    db_path = await _make_db(tmp_path)
    await _add_test_driver(db_path, DRIVER_A, "Test Lewis")

    result = await _build_test_driver_display(db_path, [DRIVER_A])

    assert result[DRIVER_A] == f"<@{DRIVER_A}> (Test Lewis)"


async def test_a_real_driver_is_not_in_the_test_driver_map(tmp_path):
    """The map is consulted first and falls through for anybody absent, so including a
    real driver would give them a test driver's rendering."""
    db_path = await _make_db(tmp_path)
    await _add_real_driver(db_path, DRIVER_A)

    assert await _build_test_driver_display(db_path, [DRIVER_A]) == {}


async def test_a_test_driver_with_no_name_is_left_out(tmp_path):
    """An empty pair of brackets reads as a missing name rather than as a driver."""
    db_path = await _make_db(tmp_path)
    await _add_test_driver(db_path, DRIVER_A, None)

    assert await _build_test_driver_display(db_path, [DRIVER_A]) == {}


async def test_asking_about_nobody_queries_nothing(tmp_path):
    """The `IN ()` clause would be malformed, so the empty case returns before the query."""
    db_path = await _make_db(tmp_path)

    assert await _build_test_driver_display(db_path, []) == {}


# ---------------------------------------------------------------------------
# Member names
# ---------------------------------------------------------------------------


async def test_a_cached_member_is_used_without_a_fetch(tmp_path):
    """Every standings post resolves the whole grid; fetching each would be a request per
    driver per round."""
    guild = _guild({DRIVER_A: "Lewis"})

    result = await _build_member_display(guild, [DRIVER_A])

    assert result[DRIVER_A] == "Lewis"
    guild.fetch_member.assert_not_awaited()


async def test_a_member_not_in_the_cache_is_fetched(tmp_path):
    """A driver who has not spoken since the bot started is absent from the cache but is
    still of the server."""
    guild = _guild({}, fetchable={DRIVER_A: "Lewis"})

    result = await _build_member_display(guild, [DRIVER_A])

    assert result[DRIVER_A] == "Lewis"
    guild.fetch_member.assert_awaited_once()


async def test_a_member_who_has_left_is_named_by_id(tmp_path):
    """They raced, so they are in the standings — a blank would leave a row with points
    and nobody against them."""
    guild = _guild({})

    result = await _build_member_display(guild, [DRIVER_A])

    assert result[DRIVER_A] == f"User {DRIVER_A}"


async def test_every_driver_asked_about_is_answered(tmp_path):
    """The caller indexes into this by id, so a missing key would raise while drawing."""
    guild = _guild({DRIVER_A: "Lewis"})

    result = await _build_member_display(guild, [DRIVER_A, DRIVER_B])

    assert set(result) == {DRIVER_A, DRIVER_B}


# ---------------------------------------------------------------------------
# The double computation
# ---------------------------------------------------------------------------


def _standings(calls: list):
    """Record each `compute_driver_standings` call, returning one snapshot."""

    async def _compute(db_path, division_id, round_id, names=None):
        calls.append(names)
        return [_snap(DRIVER_A), _snap(DRIVER_B)]

    return patch(
        "services.standings_service.compute_driver_standings",
        new=AsyncMock(side_effect=_compute),
    )


async def test_the_standings_are_computed_twice_to_order_by_name(tmp_path):
    """The final tiebreak orders a full tie alphabetically by driver, and the names are
    resolved by user id — so the roster has to be known before it can be ordered. The
    first pass is read only for who is in it.

    Removing the second call would not fail anything else: the first pass returns a
    perfectly plausible order, just not the one the tiebreak asks for."""
    db_path = await _make_db(tmp_path)
    calls: list = []

    with _standings(calls), patch(
        "services.image_results_post._driver_names",
        new=AsyncMock(return_value={DRIVER_A: "Alonso", DRIVER_B: "Bottas"}),
    ):
        await driver_standings_for_display(db_path, DIVISION_ID, 1, _guild(), MagicMock())

    assert len(calls) == 2
    assert calls[0] is None  # the first pass knows no names
    assert calls[1] == {DRIVER_A: "Alonso", DRIVER_B: "Bottas"}


async def test_without_a_guild_the_standings_are_computed_once(tmp_path):
    """There is nothing to resolve a name from, so the second pass would be identical —
    and a full tie falls back to ordering by user id, which is deterministic."""
    db_path = await _make_db(tmp_path)
    calls: list = []

    with _standings(calls):
        await driver_standings_for_display(db_path, DIVISION_ID, 1, None, MagicMock())

    assert len(calls) == 1


async def test_without_a_bot_the_standings_are_computed_once(tmp_path):
    db_path = await _make_db(tmp_path)
    calls: list = []

    with _standings(calls):
        await driver_standings_for_display(db_path, DIVISION_ID, 1, _guild(), None)

    assert len(calls) == 1


async def test_an_empty_division_is_not_computed_twice(tmp_path):
    """No drivers, so no names to resolve and nothing to reorder."""
    db_path = await _make_db(tmp_path)
    calls: list = []

    async def _compute(db_path, division_id, round_id, names=None):
        calls.append(names)
        return []

    with patch(
        "services.standings_service.compute_driver_standings",
        new=AsyncMock(side_effect=_compute),
    ):
        await driver_standings_for_display(db_path, DIVISION_ID, 1, _guild(), MagicMock())

    assert len(calls) == 1


# ---------------------------------------------------------------------------
# Resolving across the division
# ---------------------------------------------------------------------------


async def test_the_names_are_resolved_from_the_division_s_last_round(tmp_path):
    """The roster only grows as a season runs, so the last round holds every driver an
    earlier one could — which is what lets a cascade order every round it rewrites on one
    resolution."""
    db_path = await _make_db(tmp_path, rounds=((1, "FINAL"), (2, "FINAL"), (3, "FINAL")))
    seen: list = []

    async def _compute(db_path, division_id, round_id, names=None):
        seen.append(round_id)
        return [_snap(DRIVER_A)]

    with patch(
        "services.standings_service.compute_driver_standings",
        new=AsyncMock(side_effect=_compute),
    ), patch(
        "services.image_results_post._driver_names",
        new=AsyncMock(return_value={DRIVER_A: "Alonso"}),
    ):
        await standings_display_names(db_path, DIVISION_ID, _guild(), MagicMock())

    assert seen == [3]


async def test_a_cancelled_round_is_not_used_to_resolve_names(tmp_path):
    """Its roster is not the season's — a round called off may have been cancelled
    precisely because the division changed."""
    db_path = await _make_db(tmp_path, rounds=((1, "FINAL"), (2, "CANCELLED")))
    seen: list = []

    async def _compute(db_path, division_id, round_id, names=None):
        seen.append(round_id)
        return [_snap(DRIVER_A)]

    with patch(
        "services.standings_service.compute_driver_standings",
        new=AsyncMock(side_effect=_compute),
    ), patch(
        "services.image_results_post._driver_names",
        new=AsyncMock(return_value={DRIVER_A: "Alonso"}),
    ):
        await standings_display_names(db_path, DIVISION_ID, _guild(), MagicMock())

    assert seen == [1]


async def test_a_division_with_no_rounds_resolves_nothing(tmp_path):
    db_path = await _make_db(tmp_path, rounds=())

    result = await standings_display_names(db_path, DIVISION_ID, _guild(), MagicMock())

    assert result is None


async def test_a_division_with_no_standings_resolves_nothing(tmp_path):
    """A round exists but nobody has scored — there is no roster to name."""
    db_path = await _make_db(tmp_path)

    with patch(
        "services.standings_service.compute_driver_standings",
        new=AsyncMock(return_value=[]),
    ):
        result = await standings_display_names(db_path, DIVISION_ID, _guild(), MagicMock())

    assert result is None


@pytest.mark.parametrize(
    "guild,bot", [(None, MagicMock()), (MagicMock(), None), (None, None)],
    ids=["no-guild", "no-bot", "neither"],
)
async def test_nothing_to_resolve_from_returns_none(tmp_path, guild, bot):
    """`None` rather than an empty dict, because the caller distinguishes "no names known"
    from "names known and empty" — the first orders a tie by user id."""
    db_path = await _make_db(tmp_path)

    assert await standings_display_names(db_path, DIVISION_ID, guild, bot) is None
