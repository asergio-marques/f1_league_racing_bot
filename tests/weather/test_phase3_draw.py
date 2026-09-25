"""Phase 3 decides how many weather slots each session gets, and draws each one.

Issue #161: the three phase draws had no automated cover. `run_phase3` was referenced only by
`test_weather_module_gate.py` (#113) and `test_weather_restart_recovery_horizons.py` (#111),
both of which exercise the module gate and the recovery horizon rather than the draw.

`weather_module_specification.md`, Phase 3:

- "The number of weather slots in-game, <Nslots>, is to be decided randomly, with the maximum
  number dictated by the number of available weather slots for each of the session types, and
  the minimum number being 1. However, if a session is determined to be mixed weather, it will
  obligatorily have a minimum of 2 slots, **save where the session type permits fewer**";
- "A session whose type was not determined in Phase 2 shall be treated as sunny";
- the per-slot-type weight formulas, of which the rain and sunny maps are zero for the
  labels belonging to the other end of the scale;
- and, from Generation of weather, "Each phase shall be performed at most once per round."

**The `LONG_SPRINT_RACE` case is the one worth having.** That session type caps at a single
slot, so a mixed sprint race is the sole configuration where the "minimum of 2" and the
session cap collide. `min_s = min(min_s, max_s)` in `phase3_service` exists for it alone, and
`test_a_mixed_session_capped_at_one_slot_takes_one` is what stops that line being tidied away
into a `random.randint(2, 1)` — which raises rather than degrading quietly.

`random.randint` is patched so the count is deterministic and its *bounds* can be asserted
directly, which is the actual rule. `draw_weighted` is replaced by a deterministic stand-in
that takes the highest-weighted label, chosen with `sorted()` so the tie-break cannot depend
on dictionary ordering; the weights it is handed are recorded and asserted, so the slot type
Phase 2 decided is proved to reach the draw.

**Nothing here asserts on the forecast wording**, since issue #112 is open at the time of
writing and a test pinning the current text would cement it.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.models.session import MAX_SLOTS, SessionType
from leaguebot.weather.services.phase3_service import run_phase3

SERVER_ID = 1
ROUND_ID = 1
SEEDED_TRACK = "Bahrain International Circuit"

DRY_LABELS = {"Clear", "Light Cloud", "Overcast"}
WET_LABELS = {"Wet", "Very Wet"}


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _make_db(tmp_path) -> str:
    db_path = os.path.join(str(tmp_path), "phase3.db")
    await run_migrations(db_path)
    return db_path


async def _seed(
    db_path: str,
    *,
    sessions: list[tuple[str, str | None]],
    rpc: float = 0.4,
    phase2_done: int = 1,
    phase3_done: int = 0,
    seed_phase_1: bool = True,
) -> None:
    """Seed a round past its Phase 3 horizon, with *sessions* as ``(type, slot_type)`` pairs.

    The moment is computed from the real clock rather than pinned to a date, so the test
    cannot rot into passing.
    """
    scheduled_at = datetime.now(timezone.utc) - timedelta(hours=3)
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
            " phase1_done, phase2_done, phase3_done) "
            "VALUES (?, 1, 1, 'NORMAL', ?, ?, 1, ?, ?)",
            (ROUND_ID, SEEDED_TRACK, scheduled_at.isoformat(), phase2_done, phase3_done),
        )
        for session_type, slot_type in sessions:
            await db.execute(
                "INSERT INTO sessions (round_id, session_type, phase2_slot_type) "
                "VALUES (?, ?, ?)",
                (ROUND_ID, session_type, slot_type),
            )
        if seed_phase_1:
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


def _highest(weights: dict[str, float]) -> str:
    """The highest-weighted label, ties broken by name.

    Deterministic by `sorted()` rather than by dictionary order, so the result cannot depend
    on the host's iteration order.
    """
    return sorted(weights.items(), key=lambda item: (-item[1], item[0]))[0][0]


async def _persisted_slots(db_path: str) -> list[tuple[str, list[str] | None]]:
    """Every session's ``(session_type, phase3_slots)``, ordered by id as Phase 3 reads them."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT session_type, phase3_slots FROM sessions WHERE round_id = ? ORDER BY id",
            (ROUND_ID,),
        )
        rows = await cursor.fetchall()
    return [
        (row["session_type"], json.loads(row["phase3_slots"]) if row["phase3_slots"] else None)
        for row in rows
    ]


async def _phase3_state(db_path: str) -> tuple[int, int]:
    """Return ``(phase3_done, phase_3_result_row_count)``."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT phase3_done FROM rounds WHERE id = ?", (ROUND_ID,)
        )
        row = await cursor.fetchone()
        counter = await db.execute(
            "SELECT COUNT(*) AS n FROM phase_results "
            "WHERE round_id = ? AND phase_number = 3",
            (ROUND_ID,),
        )
        count = await counter.fetchone()
    return row["phase3_done"], count["n"]


async def _payload(db_path: str) -> dict:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT payload FROM phase_results "
            "WHERE round_id = ? AND phase_number = 3 ORDER BY id DESC LIMIT 1",
            (ROUND_ID,),
        )
        row = await cursor.fetchone()
    return json.loads(row["payload"])


def _run(db_path: str, *, pick: str = "max"):
    """Run Phase 3 deterministically.

    *pick* chooses which end of the permitted slot-count range `randint` returns, so a test
    can drive either bound. Returns the bot, the posting double, and an invoker that yields
    the `randint` double and the list of weight maps `draw_weighted` was handed.
    """
    bot = _make_bot(db_path)
    posted = AsyncMock()
    seen_weights: list[dict[str, float]] = []

    def _draw(weights):
        seen_weights.append(dict(weights))
        return _highest(weights)

    async def _invoke():
        with patch(
            "leaguebot.weather.services.phase3_service.random.randint",
            side_effect=lambda lo, hi: hi if pick == "max" else lo,
        ) as randint, patch(
            "leaguebot.weather.services.phase3_service.draw_weighted", side_effect=_draw
        ), patch(
            "leaguebot.weather.services.forecast_cleanup_service.post_phase_message", new=posted
        ), patch(
            "leaguebot.image.services.image_weather_post.attach_forecast",
            new=AsyncMock(return_value=None),
        ):
            await run_phase3(ROUND_ID, bot)
        return randint, seen_weights

    return bot, posted, _invoke


# ---------------------------------------------------------------------------
# How many slots a session gets
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "session_type, slot_type, expected_bounds",
    [
        ("FULL_QUALIFYING", "sunny", (1, 3)),
        ("FULL_QUALIFYING", "rain", (1, 3)),
        ("FULL_QUALIFYING", "mixed", (2, 3)),
        ("FULL_RACE", "sunny", (1, 4)),
        ("FULL_RACE", "mixed", (2, 4)),
        ("SHORT_QUALIFYING", "sunny", (1, 2)),
        ("SHORT_QUALIFYING", "mixed", (2, 2)),
        ("LONG_RACE", "mixed", (2, 3)),
    ],
    ids=lambda v: str(v),
)
async def test_slot_count_bounds(session_type, slot_type, expected_bounds, tmp_path):
    """The minimum is 1, or 2 for a mixed session; the maximum is the session type's cap."""
    db_path = await _make_db(tmp_path)
    await _seed(db_path, sessions=[(session_type, slot_type)])
    _, _, invoke = _run(db_path)

    randint, _ = await invoke()

    assert randint.call_args.args == expected_bounds
    # The cap is the one the model declares, not a number repeated in the test.
    assert expected_bounds[1] == MAX_SLOTS[SessionType(session_type)]


async def test_a_mixed_session_capped_at_one_slot_takes_one(tmp_path):
    """"... save where the session type permits fewer."

    `LONG_SPRINT_RACE` caps at one slot, so the mixed minimum of 2 has to give way. Without
    `min_s = min(min_s, max_s)` this would call `randint(2, 1)`, which raises — taking the
    whole forecast down two hours before the race rather than degrading.
    """
    assert MAX_SLOTS[SessionType.LONG_SPRINT_RACE] == 1  # the premise of this test
    db_path = await _make_db(tmp_path)
    await _seed(db_path, sessions=[("LONG_SPRINT_RACE", "mixed")])
    _, _, invoke = _run(db_path)

    randint, _ = await invoke()

    assert randint.call_args.args == (1, 1)
    # Exactly one slot is recorded — which label it holds is the weighting's business.
    slots = (await _persisted_slots(db_path))[0][1]
    assert len(slots) == 1
    assert set(slots) <= DRY_LABELS | WET_LABELS


async def test_an_unknown_session_type_falls_back_to_four_slots(tmp_path):
    """A session type outside the enum must not take the phase down."""
    db_path = await _make_db(tmp_path)
    await _seed(db_path, sessions=[("NOT_A_SESSION_TYPE", "sunny")])
    _, _, invoke = _run(db_path)

    randint, _ = await invoke()

    assert randint.call_args.args == (1, 4)


@pytest.mark.parametrize("pick", ["min", "max"], ids=["lower_bound", "upper_bound"])
async def test_the_drawn_count_is_the_number_of_slots_persisted(pick, tmp_path):
    db_path = await _make_db(tmp_path)
    await _seed(db_path, sessions=[("FULL_RACE", "mixed")])
    _, _, invoke = _run(db_path, pick=pick)

    await invoke()

    expected = 2 if pick == "min" else 4
    assert [len(slots) for _, slots in await _persisted_slots(db_path)] == [expected]


# ---------------------------------------------------------------------------
# What each slot may be
# ---------------------------------------------------------------------------


async def test_a_rain_session_draws_only_wet_weather(tmp_path):
    """The rain weight map is zero for every dry label, so none can be drawn."""
    db_path = await _make_db(tmp_path)
    await _seed(db_path, sessions=[("FULL_RACE", "rain")])
    _, _, invoke = _run(db_path)

    _, weights = await invoke()

    assert weights, "draw_weighted was never reached"
    for weight_map in weights:
        assert all(weight_map[label] == 0.0 for label in DRY_LABELS)
        assert any(weight_map[label] > 0.0 for label in WET_LABELS)

    slots = (await _persisted_slots(db_path))[0][1]
    assert set(slots) <= WET_LABELS


async def test_a_sunny_session_draws_only_dry_weather(tmp_path):
    db_path = await _make_db(tmp_path)
    await _seed(db_path, sessions=[("FULL_RACE", "sunny")])
    _, _, invoke = _run(db_path)

    _, weights = await invoke()

    assert weights, "draw_weighted was never reached"
    for weight_map in weights:
        assert all(weight_map[label] == 0.0 for label in WET_LABELS)

    slots = (await _persisted_slots(db_path))[0][1]
    assert set(slots) <= DRY_LABELS


async def test_a_mixed_session_may_draw_either_end(tmp_path):
    """Mixed is the only slot type whose map offers both wet and dry outcomes."""
    db_path = await _make_db(tmp_path)
    await _seed(db_path, sessions=[("FULL_RACE", "mixed")], rpc=0.5)
    _, _, invoke = _run(db_path)

    _, weights = await invoke()

    assert weights, "draw_weighted was never reached"
    for weight_map in weights:
        assert any(weight_map[label] > 0.0 for label in DRY_LABELS)
        assert any(weight_map[label] > 0.0 for label in WET_LABELS)


async def test_a_session_without_a_phase_2_slot_type_is_treated_as_sunny(tmp_path):
    """"A session whose type was not determined in Phase 2 shall be treated as sunny." """
    db_path = await _make_db(tmp_path)
    await _seed(db_path, sessions=[("FULL_RACE", None)])
    _, _, invoke = _run(db_path)

    randint, weights = await invoke()

    # Sunny, so the mixed minimum of 2 does not apply...
    assert randint.call_args.args == (1, 4)
    # ...and no wet outcome is on offer.
    for weight_map in weights:
        assert all(weight_map[label] == 0.0 for label in WET_LABELS)
    assert (await _payload(db_path))["session_draws"][0]["slot_type"] == "sunny"


async def test_the_weights_follow_the_rain_probability(tmp_path):
    """Rpc reaches the weight map, so a wetter round really does skew wetter.

    Two runs at opposite ends of the scale, compared against each other rather than against
    a number copied out of the formula.
    """
    (tmp_path / "dry").mkdir(exist_ok=True)
    (tmp_path / "wet").mkdir(exist_ok=True)

    async def _weights_for(directory, rpc):
        db_path = await _make_db(directory)
        await _seed(db_path, sessions=[("FULL_RACE", "mixed")], rpc=rpc)
        _, _, invoke = _run(db_path)
        _, weights = await invoke()
        return weights[0]

    dry = await _weights_for(tmp_path / "dry", 0.1)
    wet = await _weights_for(tmp_path / "wet", 0.9)

    assert wet["Very Wet"] > dry["Very Wet"]
    assert wet["Clear"] < dry["Clear"]


# ---------------------------------------------------------------------------
# Recording and posting
# ---------------------------------------------------------------------------


async def test_every_session_is_drawn_for_and_recorded(tmp_path):
    sessions = [("FULL_QUALIFYING", "sunny"), ("FULL_RACE", "rain")]
    db_path = await _make_db(tmp_path)
    await _seed(db_path, sessions=sessions)
    _, _, invoke = _run(db_path)

    await invoke()

    persisted = await _persisted_slots(db_path)
    assert [session_type for session_type, _ in persisted] == [t for t, _ in sessions]
    assert all(slots for _, slots in persisted)

    payload = await _payload(db_path)
    assert len(payload["session_draws"]) == len(sessions)
    assert [draw["slot_type"] for draw in payload["session_draws"]] == ["sunny", "rain"]
    for draw in payload["session_draws"]:
        assert draw["n_slots"] == len(draw["slots"])
        assert draw["slots_display"]


async def test_the_phase_3_post_supersedes_the_phase_2_post(tmp_path):
    db_path = await _make_db(tmp_path)
    await _seed(db_path, sessions=[("FULL_RACE", "mixed")])
    bot, posted, invoke = _run(db_path)

    await invoke()

    posted.assert_awaited_once()
    kwargs = posted.await_args.kwargs
    assert kwargs["supersedes"] == 2
    assert kwargs["phase_number"] == 3
    assert kwargs["channel_id"] == 999
    bot.output_router.post_log.assert_awaited_once()
    assert await _phase3_state(db_path) == (1, 1)


# ---------------------------------------------------------------------------
# Depending on Phase 2, and at most once per round
# ---------------------------------------------------------------------------


async def test_phase_2_is_performed_first_when_it_has_not_been(tmp_path):
    """Phase 3 runs Phase 2 first rather than drawing against no slot type at all."""
    db_path = await _make_db(tmp_path)
    await _seed(db_path, sessions=[("FULL_RACE", None)], phase2_done=0)
    bot = _make_bot(db_path)

    with patch("leaguebot.weather.services.phase2_service.random.choice", return_value="rain"), patch(
        "leaguebot.weather.services.phase3_service.random.randint", side_effect=lambda lo, hi: hi
    ), patch(
        "leaguebot.weather.services.phase3_service.draw_weighted", side_effect=_highest
    ), patch(
        "leaguebot.weather.services.forecast_cleanup_service.post_phase_message", new=AsyncMock()
    ), patch(
        "leaguebot.image.services.image_weather_post.attach_forecast", new=AsyncMock(return_value=None)
    ):
        await run_phase3(ROUND_ID, bot)

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT phase2_done, phase3_done FROM rounds WHERE id = ?", (ROUND_ID,)
        )
        row = await cursor.fetchone()
    assert (row["phase2_done"], row["phase3_done"]) == (1, 1)
    # Phase 3 read the slot type Phase 2 had just drawn, not the "sunny" fallback.
    assert (await _payload(db_path))["session_draws"][0]["slot_type"] == "rain"


async def test_missing_phase_1_result_stops_phase_3(tmp_path):
    db_path = await _make_db(tmp_path)
    await _seed(db_path, sessions=[("FULL_RACE", "mixed")], seed_phase_1=False)
    _, posted, invoke = _run(db_path)

    await invoke()

    assert await _phase3_state(db_path) == (0, 0)
    posted.assert_not_awaited()
    assert [slots for _, slots in await _persisted_slots(db_path)] == [None]


async def test_a_phase_already_performed_is_skipped(tmp_path):
    """"Each phase shall be performed at most once per round." """
    db_path = await _make_db(tmp_path)
    await _seed(db_path, sessions=[("FULL_RACE", "mixed")], phase3_done=1)
    bot, posted, invoke = _run(db_path)

    await invoke()

    assert await _phase3_state(db_path) == (1, 0)
    posted.assert_not_awaited()
    bot.output_router.post_log.assert_not_awaited()
    assert [slots for _, slots in await _persisted_slots(db_path)] == [None]


async def test_running_twice_records_one_result(tmp_path):
    db_path = await _make_db(tmp_path)
    await _seed(db_path, sessions=[("FULL_RACE", "mixed")])
    _, posted, invoke = _run(db_path)

    await invoke()
    await invoke()

    assert await _phase3_state(db_path) == (1, 1)
    posted.assert_awaited_once()
