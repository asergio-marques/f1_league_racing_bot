"""Phase 1 draws the rain probability, records it and blocks cleanly when it cannot.

Issue #161: the three phase draws had no automated cover. `run_phase1` was referenced by two
test files — `test_weather_module_gate.py` (#113) and `test_weather_restart_recovery_horizons.py`
(#111) — but both exercise only the module gate and the recovery horizon. Neither touches the
draw itself, which is what this file covers.

`weather_module_specification.md`, Phase 1:

- "the bot shall draw the rain probability <Rpc> for the round from a Beta distribution
  parameterised by the μ and σ of the round's circuit";
- "The distribution parameters shall be derived as ν = μ(1 − μ)/σ² − 1, α = μν and β = (1 − μ)ν";
- "The draw shall be clamped to the interval [0, 1] and rounded to two decimal places";
- "σ shall satisfy 0 < σ < √(μ(1 − μ)). Where it does not ... Phase 1 shall be blocked for that
  round, and the reason written to the log channel";
- "Where the round's circuit cannot be resolved, Phase 1 shall likewise be blocked";
- and, from Generation of weather, "Each phase shall be performed at most once per round."

**`random.betavariate` is patched rather than seeded**, so the draw is deterministic while the
derivation of α and β, the clamp and the rounding all still run for real. Patching
`compute_rpc_beta` instead would stub out the very arithmetic the specification pins.

The clamp is tested with draws of 1.5 and −0.5, which `betavariate` cannot itself produce.
That is deliberate: the clamp exists as a guard against exactly the impossible value, so the
only way to prove it works is to hand it one.

**Nothing here asserts on the forecast wording.** Issue #112 — the posts saying "5 days out"
whatever the league configured — is open at the time of writing, and a test pinning the
current text would cement the defect. These tests assert on what is *persisted* and on the
fact that a post was made.
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
from leaguebot.weather.services.phase1_service import run_phase1  # noqa: E402

SERVER_ID = 1
ROUND_ID = 1

#: A circuit seeded by migration 029, with feasible parameters.
#: √(0.05 × 0.95) ≈ 0.218, so σ = 0.02 sits comfortably inside the limit.
SEEDED_TRACK = "Bahrain International Circuit"
SEEDED_MU = 0.05
SEEDED_SIGMA = 0.02

#: A circuit inserted by the tests whose σ breaks the feasibility rule:
#: √(0.5 × 0.5) = 0.5, and 0.9 is well beyond it.
INFEASIBLE_TRACK = "Impossible Circuit"


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _make_db(tmp_path) -> str:
    db_path = os.path.join(str(tmp_path), "phase1.db")
    await run_migrations(db_path)
    return db_path


async def _seed(
    db_path: str,
    *,
    track_name: str | None = SEEDED_TRACK,
    phase1_done: int = 0,
) -> None:
    """Insert server_config, season, division and one round whose Phase 1 horizon has passed.

    The moment is computed from the real clock rather than pinned to a date, so the test
    cannot rot into passing.
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
            "(id, division_id, round_number, format, track_name, scheduled_at, phase1_done) "
            "VALUES (?, 1, 1, 'NORMAL', ?, ?, ?)",
            (ROUND_ID, track_name, scheduled_at.isoformat(), phase1_done),
        )
        await db.commit()


async def _add_infeasible_track(db_path: str) -> None:
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO tracks (name, gp_name, location, country, mu, sigma) "
            "VALUES (?, 'Impossible Grand Prix', 'Nowhere', 'Nowhere', 0.5, 0.9)",
            (INFEASIBLE_TRACK,),
        )
        await db.commit()


def _make_bot(db_path: str) -> MagicMock:
    bot = MagicMock()
    bot.db_path = db_path
    bot.module_service.is_weather_enabled = AsyncMock(return_value=True)
    bot.output_router.post_log = AsyncMock(return_value=None)
    return bot


async def _phase1_state(db_path: str) -> tuple[int, int]:
    """Return ``(phase1_done, phase_results_row_count)``."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT phase1_done FROM rounds WHERE id = ?", (ROUND_ID,)
        )
        row = await cursor.fetchone()
        counter = await db.execute(
            "SELECT COUNT(*) AS n FROM phase_results WHERE round_id = ?", (ROUND_ID,)
        )
        count = await counter.fetchone()
    return row["phase1_done"], count["n"]


async def _payload(db_path: str) -> dict:
    """The recorded Phase 1 payload, as stored."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT payload FROM phase_results "
            "WHERE round_id = ? AND phase_number = 1 ORDER BY id DESC LIMIT 1",
            (ROUND_ID,),
        )
        row = await cursor.fetchone()
    return json.loads(row["payload"])


def _run(db_path: str, *, raw_draw: float = 0.05):
    """Run Phase 1 with the Beta draw pinned, returning the `post_phase_message` double."""
    bot = _make_bot(db_path)
    posted = AsyncMock()

    async def _invoke():
        with patch("random.betavariate", return_value=raw_draw), patch(
            "leaguebot.weather.services.forecast_cleanup_service.post_phase_message", new=posted
        ), patch(
            "leaguebot.image.services.image_weather_post.attach_forecast",
            new=AsyncMock(return_value=None),
        ):
            await run_phase1(ROUND_ID, bot)

    return bot, posted, _invoke


# ---------------------------------------------------------------------------
# The successful draw
# ---------------------------------------------------------------------------


async def test_successful_draw_records_posts_and_logs(tmp_path):
    db_path = await _make_db(tmp_path)
    await _seed(db_path)
    bot, posted, invoke = _run(db_path, raw_draw=0.3456)

    await invoke()

    assert await _phase1_state(db_path) == (1, 1)
    posted.assert_awaited_once()
    bot.output_router.post_log.assert_awaited_once()

    # The forecast went to the division's own channel, for the round it was drawn for.
    kwargs = posted.await_args.kwargs
    assert kwargs["round_id"] == ROUND_ID
    assert kwargs["channel_id"] == 999
    assert kwargs["phase_number"] == 1


async def test_payload_records_the_distribution_it_drew_from(tmp_path):
    """ν = μ(1 − μ)/σ² − 1, α = μν, β = (1 − μ)ν, recorded alongside the draw.

    The payload is the league's audit of the forecast, so it has to carry the parameters the
    draw was actually made with — not merely the outcome.
    """
    db_path = await _make_db(tmp_path)
    await _seed(db_path)
    _, _, invoke = _run(db_path, raw_draw=0.3456)

    await invoke()

    payload = await _payload(db_path)
    nu = SEEDED_MU * (1.0 - SEEDED_MU) / SEEDED_SIGMA ** 2 - 1.0

    assert payload["phase"] == 1
    assert payload["round_id"] == ROUND_ID
    assert payload["track"] == SEEDED_TRACK
    assert payload["distribution"] == "beta"
    assert payload["mu"] == pytest.approx(SEEDED_MU)
    assert payload["sigma"] == pytest.approx(SEEDED_SIGMA)
    assert payload["alpha"] == pytest.approx(SEEDED_MU * nu)
    assert payload["beta_param"] == pytest.approx((1.0 - SEEDED_MU) * nu)
    assert payload["raw_draw"] == pytest.approx(0.3456)


async def test_the_draw_uses_the_circuit_parameters(tmp_path):
    """`betavariate` is called with the α and β derived from the round's own circuit."""
    db_path = await _make_db(tmp_path)
    await _seed(db_path)
    bot = _make_bot(db_path)
    nu = SEEDED_MU * (1.0 - SEEDED_MU) / SEEDED_SIGMA ** 2 - 1.0

    with patch("random.betavariate", return_value=0.05) as betavariate, patch(
        "leaguebot.weather.services.forecast_cleanup_service.post_phase_message", new=AsyncMock()
    ), patch(
        "leaguebot.image.services.image_weather_post.attach_forecast", new=AsyncMock(return_value=None)
    ):
        await run_phase1(ROUND_ID, bot)

    alpha, beta_param = betavariate.call_args.args
    assert alpha == pytest.approx(SEEDED_MU * nu)
    assert beta_param == pytest.approx((1.0 - SEEDED_MU) * nu)
    # Both must be positive, or `betavariate` itself would raise.
    assert alpha > 0 and beta_param > 0


@pytest.mark.parametrize(
    "raw_draw, expected_rpc",
    [
        (0.3456, 0.35),
        (0.344, 0.34),
        (0.125, 0.12),   # round-half-to-even, as Python's `round` does
        (0.0, 0.0),
        (1.0, 1.0),
    ],
    ids=["rounds_up", "rounds_down", "half_even", "zero", "one"],
)
async def test_rpc_is_rounded_to_two_decimal_places(raw_draw, expected_rpc, tmp_path):
    db_path = await _make_db(tmp_path)
    await _seed(db_path)
    _, _, invoke = _run(db_path, raw_draw=raw_draw)

    await invoke()

    assert (await _payload(db_path))["rpc"] == expected_rpc


@pytest.mark.parametrize(
    "raw_draw, expected_rpc",
    [(1.5, 1.0), (-0.5, 0.0)],
    ids=["above_one", "below_zero"],
)
async def test_rpc_is_clamped_to_the_unit_interval(raw_draw, expected_rpc, tmp_path):
    """A draw outside [0, 1] is clamped, and the raw value still recorded for audit.

    `betavariate` cannot return these, which is the point: the clamp is a guard against the
    impossible, so it can only be proved by handing it the impossible.
    """
    db_path = await _make_db(tmp_path)
    await _seed(db_path)
    _, _, invoke = _run(db_path, raw_draw=raw_draw)

    await invoke()

    payload = await _payload(db_path)
    assert payload["rpc"] == expected_rpc
    assert payload["raw_draw"] == pytest.approx(raw_draw)


# ---------------------------------------------------------------------------
# Blocking, and the reasons for it
# ---------------------------------------------------------------------------


async def test_unresolvable_circuit_blocks_the_phase(tmp_path):
    """"Where the round's circuit cannot be resolved, Phase 1 shall ... be blocked."""
    db_path = await _make_db(tmp_path)
    await _seed(db_path, track_name="Nürburgring Nordschleife")  # not a seeded circuit
    bot, posted, invoke = _run(db_path)

    await invoke()

    assert await _phase1_state(db_path) == (0, 0)
    posted.assert_not_awaited()
    # The reason reaches the league's log channel rather than only the bot's own log.
    bot.output_router.post_log.assert_awaited_once()
    (text,) = bot.output_router.post_log.await_args.args
    assert "BLOCKED" in text


async def test_infeasible_sigma_blocks_the_phase(tmp_path):
    """"σ shall satisfy 0 < σ < √(μ(1 − μ)). Where it does not ... Phase 1 shall be blocked."

    The draw is left unpatched here — the guard has to fire before `betavariate` is reached,
    since the derived α and β would be non-positive and `betavariate` would raise.
    """
    db_path = await _make_db(tmp_path)
    await _add_infeasible_track(db_path)
    await _seed(db_path, track_name=INFEASIBLE_TRACK)
    bot = _make_bot(db_path)
    posted = AsyncMock()

    with patch("leaguebot.weather.services.forecast_cleanup_service.post_phase_message", new=posted), patch(
        "leaguebot.image.services.image_weather_post.attach_forecast", new=AsyncMock(return_value=None)
    ):
        await run_phase1(ROUND_ID, bot)

    assert await _phase1_state(db_path) == (0, 0)
    posted.assert_not_awaited()
    bot.output_router.post_log.assert_awaited_once()
    (text,) = bot.output_router.post_log.await_args.args
    assert "BLOCKED" in text
    # σ = 0.9 against a limit of √(0.5 × 0.5) = 0.5.
    assert 0.9 >= math.sqrt(0.5 * 0.5)


async def test_round_without_a_circuit_blocks_the_phase(tmp_path):
    db_path = await _make_db(tmp_path)
    await _seed(db_path, track_name=None)
    _, posted, invoke = _run(db_path)

    await invoke()

    assert await _phase1_state(db_path) == (0, 0)
    posted.assert_not_awaited()


async def test_unknown_round_produces_nothing(tmp_path):
    db_path = await _make_db(tmp_path)
    await _seed(db_path)
    bot, posted, _ = _run(db_path)

    with patch("leaguebot.weather.services.forecast_cleanup_service.post_phase_message", new=posted):
        await run_phase1(999, bot)

    assert await _phase1_state(db_path) == (0, 0)
    posted.assert_not_awaited()
    bot.output_router.post_log.assert_not_awaited()


# ---------------------------------------------------------------------------
# At most once per round
# ---------------------------------------------------------------------------


async def test_a_phase_already_performed_is_skipped(tmp_path):
    """"Each phase shall be performed at most once per round." """
    db_path = await _make_db(tmp_path)
    await _seed(db_path, phase1_done=1)
    bot, posted, invoke = _run(db_path)

    await invoke()

    # Still marked done, and no result recorded by this run.
    assert await _phase1_state(db_path) == (1, 0)
    posted.assert_not_awaited()
    bot.output_router.post_log.assert_not_awaited()


async def test_running_twice_records_one_result(tmp_path):
    """The guard holds on the second call, not merely on a round seeded as done."""
    db_path = await _make_db(tmp_path)
    await _seed(db_path)
    _, posted, invoke = _run(db_path)

    await invoke()
    await invoke()

    assert await _phase1_state(db_path) == (1, 1)
    posted.assert_awaited_once()
