"""Phase 2 builds the 1000-slot pool and draws one slot for each session of the round.

Issue #161: the three phase draws had no automated cover. `run_phase2` was referenced only by
`test_weather_module_gate.py` (#113) and `test_weather_restart_recovery_horizons.py` (#111),
both of which exercise the module gate and the recovery horizon rather than the draw.

`weather_module_specification.md`, Phase 2:

- "A 1000-entry map is to be filled with these three slots for a randomized drawing";
- "<Ir> shall be equal to ((1000 * <Rpc>) * (1 + <Rpc>) ^ 2) / 5, rounded down";
- "<Im> shall be equal to (1000 * <Rpc>) - <Ir>, clamped to a minimum value of 0";
- "<Is> shall be equal to 1000 - <Im> - <Ir>";
- "From these 1000 slots, 1 shall be taken at random for each of the sessions configured to
  take place in the round, which shall be remembered for later use in Phase 3";
- "Where Phase 1 has not been performed for the round, it shall be performed first";
- and, from Generation of weather, "Each phase shall be performed at most once per round."

`random.choice` is patched so the draw is deterministic, and the pool it was handed is
inspected through the call arguments — which lets the composition be asserted for real rather
than recomputed by the test from the same formula it is meant to be checking.

**Nothing here asserts on the forecast wording**, since issue #112 — the posts saying "2 days
out" whatever the league configured — is open at the time of writing and a test pinning the
current text would cement it. These tests assert on what is persisted and on the supersession.
"""
from __future__ import annotations

import json
import math
import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from leaguebot.core.db.database import get_connection, run_migrations  # noqa: E402
from leaguebot.weather.services.phase2_service import run_phase2  # noqa: E402

SERVER_ID = 1
ROUND_ID = 1
SEEDED_TRACK = "Bahrain International Circuit"

#: The sessions of a full round, in the order Phase 2 reads them (`ORDER BY id`).
SESSION_TYPES = ["FULL_QUALIFYING", "FULL_RACE"]


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _make_db(tmp_path) -> str:
    db_path = os.path.join(str(tmp_path), "phase2.db")
    await run_migrations(db_path)
    return db_path


async def _seed(
    db_path: str,
    *,
    phase1_done: int = 1,
    phase2_done: int = 0,
    rpc: float | None = 0.2,
    session_types: list[str] | None = None,
) -> None:
    """Seed a round whose Phase 2 horizon has passed, with an ACTIVE Phase 1 result.

    Pass ``rpc=None`` to seed no Phase 1 result at all. The moment is computed from the real
    clock rather than pinned to a date, so the test cannot rot into passing.
    """
    scheduled_at = datetime.now(timezone.utc) - timedelta(days=1)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs "
            "(server_id, interaction_role_id, interaction_channel_id, log_channel_id, "
            " weather_module_enabled) VALUES (?, 100, 200, 300, 1)",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO seasons (id, season_number, start_date, status) "
            "VALUES (1, 1, '2026-01-01', 'ACTIVE')"
        )
        await db.execute(
            "INSERT INTO divisions "
            "(id, season_id, name, tier, forecast_channel_id, mention_role_id) "
            "VALUES (1, 1, 'Div A', 1, 999, 555)"
        )
        await db.execute(
            "INSERT INTO rounds "
            "(id, division_id, round_number, format, track_name, scheduled_at, "
            " phase1_done, phase2_done) "
            "VALUES (?, 1, 1, 'NORMAL', ?, ?, ?, ?)",
            (ROUND_ID, SEEDED_TRACK, scheduled_at.isoformat(), phase1_done, phase2_done),
        )
        for session_type in (session_types or SESSION_TYPES):
            await db.execute(
                "INSERT INTO sessions (round_id, session_type) VALUES (?, ?)",
                (ROUND_ID, session_type),
            )
        if rpc is not None:
            await db.execute(
                "INSERT INTO phase_results "
                "(round_id, phase_number, payload, status, created_at) "
                "VALUES (?, 1, ?, 'ACTIVE', ?)",
                (
                    ROUND_ID,
                    json.dumps({"phase": 1, "rpc": rpc, "track": SEEDED_TRACK}),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
        await db.commit()


def _make_bot(db_path: str) -> MagicMock:
    bot = MagicMock()
    bot.db_path = db_path
    bot.module_service.is_weather_enabled = AsyncMock(return_value=True)
    bot.output_router.post_log = AsyncMock(return_value=None)
    return bot


async def _phase2_state(db_path: str) -> tuple[int, int]:
    """Return ``(phase2_done, phase_2_result_row_count)``."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT phase2_done FROM rounds WHERE id = ?", (ROUND_ID,)
        )
        row = await cursor.fetchone()
        counter = await db.execute(
            "SELECT COUNT(*) AS n FROM phase_results "
            "WHERE round_id = ? AND phase_number = 2",
            (ROUND_ID,),
        )
        count = await counter.fetchone()
    return row["phase2_done"], count["n"]


async def _payload(db_path: str) -> dict:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT payload FROM phase_results "
            "WHERE round_id = ? AND phase_number = 2 ORDER BY id DESC LIMIT 1",
            (ROUND_ID,),
        )
        row = await cursor.fetchone()
    return json.loads(row["payload"])


async def _slot_types(db_path: str) -> list[tuple[str, str | None]]:
    """Every session's ``(session_type, phase2_slot_type)``, ordered by id as Phase 2 reads them."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT session_type, phase2_slot_type FROM sessions "
            "WHERE round_id = ? ORDER BY id",
            (ROUND_ID,),
        )
        rows = await cursor.fetchall()
    return [(row["session_type"], row["phase2_slot_type"]) for row in rows]


def _expected_pool(rpc: float) -> tuple[int, int, int]:
    """Ir, Im, Is computed straight from the specification's formulas."""
    ir = math.floor((1000 * rpc * (1 + rpc) ** 2) / 5)
    im = max(0, math.floor(1000 * rpc) - ir)
    return ir, im, 1000 - im - ir


def _run(db_path: str, *, drawn: str = "mixed"):
    """Run Phase 2 with the slot draw pinned, returning the doubles the test inspects."""
    bot = _make_bot(db_path)
    posted = AsyncMock()

    async def _invoke():
        with patch(
            "leaguebot.weather.services.phase2_service.random.choice", return_value=drawn
        ) as choice, patch(
            "leaguebot.weather.services.forecast_cleanup_service.post_phase_message", new=posted
        ), patch(
            "leaguebot.image.services.image_weather_post.attach_forecast",
            new=AsyncMock(return_value=None),
        ):
            await run_phase2(ROUND_ID, bot)
        return choice

    return bot, posted, _invoke


# ---------------------------------------------------------------------------
# The slot pool
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "rpc", [0.0, 0.05, 0.2, 0.5, 0.75, 1.0], ids=lambda v: f"rpc_{v}"
)
async def test_pool_composition_follows_the_formulas(rpc, tmp_path):
    """Ir, Im and Is are recorded as the specification derives them."""
    db_path = await _make_db(tmp_path)
    await _seed(db_path, rpc=rpc)
    _, _, invoke = _run(db_path)

    await invoke()

    payload = await _payload(db_path)
    ir, im, is_ = _expected_pool(rpc)
    assert (payload["ir"], payload["im"], payload["is"]) == (ir, im, is_)
    assert payload["rpc"] == rpc


@pytest.mark.parametrize(
    "rpc", [0.0, 0.05, 0.2, 0.5, 0.75, 1.0], ids=lambda v: f"rpc_{v}"
)
async def test_the_pool_drawn_from_holds_exactly_one_thousand_slots(rpc, tmp_path):
    """"A 1000-entry map is to be filled with these three slots for a randomized drawing."

    Asserted on the pool actually handed to the draw, not on the recorded counts, so a pool
    built from the right numbers but assembled wrongly is still caught.
    """
    db_path = await _make_db(tmp_path)
    await _seed(db_path, rpc=rpc)
    _, _, invoke = _run(db_path)

    choice = await invoke()

    ir, im, is_ = _expected_pool(rpc)
    for call in choice.call_args_list:
        pool = call.args[0]
        assert len(pool) == 1000
        assert pool.count("rain") == ir
        assert pool.count("mixed") == im
        assert pool.count("sunny") == is_


async def test_a_certainty_of_rain_leaves_no_sunny_slots(tmp_path):
    """Rpc = 1.0 gives 800 rain and 200 mixed, and no sunny slot to draw."""
    db_path = await _make_db(tmp_path)
    await _seed(db_path, rpc=1.0)
    _, _, invoke = _run(db_path, drawn="rain")

    choice = await invoke()

    pool = choice.call_args_list[0].args[0]
    assert pool.count("sunny") == 0
    assert (pool.count("rain"), pool.count("mixed")) == (800, 200)


async def test_no_chance_of_rain_leaves_only_sunny_slots(tmp_path):
    db_path = await _make_db(tmp_path)
    await _seed(db_path, rpc=0.0)
    _, _, invoke = _run(db_path, drawn="sunny")

    choice = await invoke()

    pool = choice.call_args_list[0].args[0]
    assert pool.count("sunny") == 1000


# ---------------------------------------------------------------------------
# The draw, one slot per session
# ---------------------------------------------------------------------------


async def test_one_slot_is_drawn_for_each_session(tmp_path):
    """"1 shall be taken at random for each of the sessions configured to take place." """
    db_path = await _make_db(tmp_path)
    await _seed(db_path)
    _, _, invoke = _run(db_path, drawn="rain")

    choice = await invoke()

    assert choice.call_count == len(SESSION_TYPES)
    assert await _slot_types(db_path) == [(t, "rain") for t in SESSION_TYPES]


async def test_the_draw_is_remembered_for_phase_3(tmp_path):
    """The drawn slot is persisted on the session, which is what Phase 3 reads."""
    db_path = await _make_db(tmp_path)
    await _seed(db_path)
    _, _, invoke = _run(db_path, drawn="mixed")

    await invoke()

    assert [slot for _, slot in await _slot_types(db_path)] == ["mixed", "mixed"]
    payload = await _payload(db_path)
    assert [draw["slot"] for draw in payload["session_draws"]] == ["mixed", "mixed"]
    assert [draw["session_type"] for draw in payload["session_draws"]] == SESSION_TYPES


async def test_a_round_with_more_sessions_draws_for_every_one(tmp_path):
    """A sprint round carries four sessions, and each takes its own draw."""
    sessions = [
        "SHORT_SPRINT_QUALIFYING",
        "LONG_SPRINT_RACE",
        "SHORT_FEATURE_QUALIFYING",
        "LONG_FEATURE_RACE",
    ]
    db_path = await _make_db(tmp_path)
    await _seed(db_path, session_types=sessions)
    _, _, invoke = _run(db_path, drawn="sunny")

    choice = await invoke()

    assert choice.call_count == len(sessions)
    assert await _slot_types(db_path) == [(t, "sunny") for t in sessions]


# ---------------------------------------------------------------------------
# Posting and supersession
# ---------------------------------------------------------------------------


async def test_the_phase_2_post_supersedes_the_phase_1_post(tmp_path):
    """Phase 2 replaces the Phase 1 forecast rather than standing beside it."""
    db_path = await _make_db(tmp_path)
    await _seed(db_path)
    bot, posted, invoke = _run(db_path)

    await invoke()

    posted.assert_awaited_once()
    kwargs = posted.await_args.kwargs
    assert kwargs["supersedes"] == 1
    assert kwargs["phase_number"] == 2
    assert kwargs["channel_id"] == 999
    bot.output_router.post_log.assert_awaited_once()


# ---------------------------------------------------------------------------
# Depending on Phase 1
# ---------------------------------------------------------------------------


async def test_phase_1_is_performed_first_when_it_has_not_been(tmp_path):
    """"Where Phase 1 has not been performed for the round, it shall be performed first." """
    db_path = await _make_db(tmp_path)
    await _seed(db_path, phase1_done=0, rpc=None)
    bot = _make_bot(db_path)

    with patch("random.betavariate", return_value=0.2), patch(
        "leaguebot.weather.services.phase2_service.random.choice", return_value="mixed"
    ), patch(
        "leaguebot.weather.services.forecast_cleanup_service.post_phase_message", new=AsyncMock()
    ), patch(
        "leaguebot.image.services.image_weather_post.attach_forecast", new=AsyncMock(return_value=None)
    ):
        await run_phase2(ROUND_ID, bot)

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT phase1_done, phase2_done FROM rounds WHERE id = ?", (ROUND_ID,)
        )
        row = await cursor.fetchone()
    assert (row["phase1_done"], row["phase2_done"]) == (1, 1)
    # Phase 2 used the Rpc Phase 1 had just drawn.
    assert (await _payload(db_path))["rpc"] == 0.2


async def test_missing_phase_1_result_stops_phase_2(tmp_path):
    """A round marked as done but holding no ACTIVE result must not be guessed at."""
    db_path = await _make_db(tmp_path)
    await _seed(db_path, phase1_done=1, rpc=None)
    _, posted, invoke = _run(db_path)

    await invoke()

    assert await _phase2_state(db_path) == (0, 0)
    posted.assert_not_awaited()
    assert [slot for _, slot in await _slot_types(db_path)] == [None, None]


async def test_an_invalidated_phase_1_result_is_not_read(tmp_path):
    """Only an ACTIVE Phase 1 result counts; a superseded one must not revive a forecast."""
    db_path = await _make_db(tmp_path)
    await _seed(db_path, phase1_done=1, rpc=None)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO phase_results "
            "(round_id, phase_number, payload, status, created_at) "
            "VALUES (?, 1, ?, 'INVALIDATED', ?)",
            (
                ROUND_ID,
                json.dumps({"phase": 1, "rpc": 0.9, "track": SEEDED_TRACK}),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        await db.commit()
    _, posted, invoke = _run(db_path)

    await invoke()

    assert await _phase2_state(db_path) == (0, 0)
    posted.assert_not_awaited()


# ---------------------------------------------------------------------------
# At most once per round
# ---------------------------------------------------------------------------


async def test_a_phase_already_performed_is_skipped(tmp_path):
    """"Each phase shall be performed at most once per round." """
    db_path = await _make_db(tmp_path)
    await _seed(db_path, phase2_done=1)
    bot, posted, invoke = _run(db_path)

    await invoke()

    assert await _phase2_state(db_path) == (1, 0)
    posted.assert_not_awaited()
    bot.output_router.post_log.assert_not_awaited()
    assert [slot for _, slot in await _slot_types(db_path)] == [None, None]


async def test_running_twice_records_one_result(tmp_path):
    db_path = await _make_db(tmp_path)
    await _seed(db_path)
    _, posted, invoke = _run(db_path)

    await invoke()
    await invoke()

    assert await _phase2_state(db_path) == (1, 1)
    posted.assert_awaited_once()
