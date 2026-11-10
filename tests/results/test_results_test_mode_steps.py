"""Results' step for `/test-mode advance`: whether a round's result submission is still to open,
read from its own state alone and handed to core through the `pending_steps_read` hook."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from leaguebot.core.db.database import get_connection, run_migrations

#: Noon on 12 October 2026: the moment each step is judged at; round 1 raced an hour before.
NOW = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)
RACED = "2026-10-12T11:00:00+00:00"


async def _seed(
    db_path: str,
    *,
    results_on: bool = True,
    status: str = "NOT_RUN",
    submitted: bool = False,
    in_hand: bool = False,
) -> None:
    """Round 1 (Bahrain, Division A) of the active season, in *status*, with results on or off:
    a session's results submitted where *submitted* is set, and its submission asked of the change
    queue and not yet opened where *in_hand* is."""
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs "
            "(server_id, interaction_role_id, interaction_channel_id, log_channel_id) "
            "VALUES (1, 100, 200, 300)"
        )
        await db.execute(
            "INSERT INTO results_module_config (id, module_enabled) VALUES (1, ?)",
            (int(results_on),),
        )
        await db.execute(
            "INSERT INTO seasons (id, start_date, status) VALUES (1, '2026-01-01', 'ACTIVE')"
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, mention_role_id, forecast_channel_id) "
            "VALUES (1, 1, 'Division A', 11, 21)"
        )
        await db.execute(
            "INSERT INTO rounds (id, division_id, round_number, format, track_name, scheduled_at, "
            " status) VALUES (1, 1, 1, 'NORMAL', 'Bahrain', ?, ?)",
            (RACED, status),
        )
        if submitted:
            await db.execute(
                "INSERT INTO session_results (round_id, division_id, session_type, status) "
                "VALUES (1, 1, 'FEATURE_RACE', 'ACTIVE')"
            )
        if in_hand:
            await db.execute(
                "INSERT INTO queued_changes (kind, dedup_key, payload, origin, state, what) "
                "VALUES ('results.submission.open', 'results.submission.open:1', ?, 'BOT', "
                "'QUEUED', 'opening a submission')",
                (json.dumps({"round_id": 1}),),
            )
        await db.commit()


def _round(status: str):
    """Round 1 as core reads it and hands it to each module."""
    from leaguebot.core.models.round import read_scheduled_at
    from leaguebot.core.services.test_mode_service import ActiveRound

    return ActiveRound(
        round_id=1, round_number=1, division_id=1, division_name="Division A", format="NORMAL",
        track_name="Bahrain", scheduled_at=read_scheduled_at(RACED), status=status,
    )


@pytest.mark.parametrize("state, pending", [
    pytest.param({}, [(4, "results")], id="a round not yet run"),
    pytest.param({"status": "AWAITING_RESULTS"}, [(4, "results")], id="awaiting its results"),
    pytest.param({"submitted": True}, [], id="a session's results submitted"),
    pytest.param({"in_hand": True}, [], id="the submission in hand on the change queue"),
    pytest.param({"status": "AWAITING_REPORT_VERDICTS"}, [], id="awaiting report verdicts"),
    pytest.param({"status": "FINAL"}, [], id="final"),
    pytest.param({"results_on": False}, [], id="results off"),
])
async def test_results_hands_back_its_own_pending_step(tmp_path, state, pending) -> None:
    """It is noon on 12 October 2026, and round 1 (Bahrain) raced an hour ago. Results hands back
    the round's result submission while the round is not yet run or awaiting its results, no
    session's results stand and no submission is already asked of the change queue. It passes
    nothing over, and while it is off it hands back nothing."""
    from leaguebot.results.services.test_mode_steps import pending_steps

    db_path = str(tmp_path / "results_steps.db")
    await _seed(db_path, **state)

    steps = await pending_steps(db_path, [_round(state.get("status", "NOT_RUN"))], now=NOW)

    assert [(s.round_id, s.phase_number, s.kind) for s in steps.pending] == [
        (1, phase, kind) for phase, kind in pending
    ]
    assert steps.passed_over == []
