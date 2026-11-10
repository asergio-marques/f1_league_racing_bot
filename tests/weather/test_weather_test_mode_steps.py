"""Weather's steps for `/test-mode advance`: what of each round it still has to post or take down,
and what advance passes over, read from its own state alone and handed to core through the
`pending_steps_read` hook."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from leaguebot.core.db.database import get_connection, run_migrations

#: Noon on 12 October 2026: the moment each step is judged at.
NOW = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)

#: A week ahead, and an hour ago: a round still to race, and one raced.
AHEAD = "2026-10-19T12:00:00+00:00"
RACED = "2026-10-12T11:00:00+00:00"


async def _seed(
    db_path: str,
    *,
    weather_on: bool = True,
    format: str = "NORMAL",
    scheduled_at: str = AHEAD,
    done: tuple[int, ...] = (),
    standing: tuple[int, ...] = (),
    forecasts_in_hand: tuple[int, ...] = (),
    clean_up_in_hand: bool = False,
) -> None:
    """Round 1 (Bahrain, Division A) of the active season, with weather on or off: the phases in
    *done* marked done, a forecast of each phase in *standing* still posted, and a forecast of each
    phase in *forecasts_in_hand*, or its clean-up, asked of the change queue and not yet run."""
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs "
            "(server_id, interaction_role_id, interaction_channel_id, log_channel_id, "
            " weather_module_enabled) VALUES (1, 100, 200, 300, ?)",
            (int(weather_on),),
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
            " phase1_done, phase2_done, phase3_done) VALUES (1, 1, 1, ?, 'Bahrain', ?, ?, ?, ?)",
            (format, scheduled_at, *(int(n in done) for n in (1, 2, 3))),
        )
        for phase in standing:
            await db.execute(
                "INSERT INTO forecast_messages "
                "(round_id, division_id, phase_number, message_id, posted_at) "
                "VALUES (1, 1, ?, ?, '2026-10-01T00:00:00+00:00')",
                (phase, 800 + phase),
            )
        queued = [
            ("weather.forecast", f"weather.forecast:1:{phase}",
             {"round_id": 1, "phase": phase, "division_name": "Division A", "round_number": 1})
            for phase in forecasts_in_hand
        ]
        if clean_up_in_hand:
            queued.append(
                ("weather.forecast.clean_up", "weather.forecast.clean_up:1", {"round_id": 1})
            )
        for kind, key, payload in queued:
            await db.execute(
                "INSERT INTO queued_changes (kind, dedup_key, payload, origin, state, what) "
                "VALUES (?, ?, ?, 'BOT', 'QUEUED', 'a weather step')",
                (kind, key, json.dumps(payload)),
            )
        await db.commit()


def _round(*, format: str, scheduled_at: str):
    """Round 1 as core reads it and hands it to each module."""
    from leaguebot.core.models.round import read_scheduled_at
    from leaguebot.core.services.test_mode_service import ActiveRound

    return ActiveRound(
        round_id=1, round_number=1, division_id=1, division_name="Division A", format=format,
        track_name="Bahrain", scheduled_at=read_scheduled_at(scheduled_at), status="NOT_RUN",
    )


@pytest.mark.parametrize("state, pending, passed_over", [
    pytest.param({}, [(1, "weather_p1"), (2, "weather_p2"), (3, "weather_p3")], [],
                 id="a round still to race with nothing posted"),
    pytest.param({"done": (1,), "standing": (1,)}, [(2, "weather_p2"), (3, "weather_p3"),
                 (8, "cleanup")], [], id="phase 1 posted"),
    pytest.param({"format": "MYSTERY"}, [(0, "weather_p1")], [],
                 id="a mystery round's notice still to post"),
    pytest.param({"format": "MYSTERY", "done": (1,), "standing": (0,)}, [], [],
                 id="a mystery round's notice posted, with no forecast to take down"),
    pytest.param({"forecasts_in_hand": (1,)}, [(2, "weather_p2"), (3, "weather_p3")], [],
                 id="phase 1 in hand on the change queue"),
    pytest.param({"scheduled_at": RACED}, [],
                 [(1, "weather_p1"), (2, "weather_p2"), (3, "weather_p3")],
                 id="a raced round with nothing posted"),
    pytest.param({"scheduled_at": RACED, "format": "MYSTERY"}, [], [(0, "weather_p1")],
                 id="a raced mystery round with no notice"),
    pytest.param({"scheduled_at": RACED, "forecasts_in_hand": (1,)}, [],
                 [(2, "weather_p2"), (3, "weather_p3")],
                 id="a raced round with phase 1 in hand on the change queue"),
    pytest.param({"scheduled_at": RACED, "done": (1,), "standing": (1,)}, [(8, "cleanup")],
                 [(2, "weather_p2"), (3, "weather_p3")],
                 id="a raced round with phase 1 still posted"),
    pytest.param({"done": (1, 2, 3), "standing": (3,)}, [(8, "cleanup")], [],
                 id="every phase posted, phase 3 still up"),
    pytest.param({"done": (1, 2, 3), "standing": (3,), "clean_up_in_hand": True}, [], [],
                 id="the clean-up in hand on the change queue"),
    pytest.param({"done": (1, 2, 3)}, [], [], id="every phase posted and taken down"),
    pytest.param({"weather_on": False}, [], [], id="weather off"),
    pytest.param({"weather_on": False, "scheduled_at": RACED, "done": (1,), "standing": (1,)},
                 [], [], id="weather off, with a raced round's forecast still up"),
])
async def test_weather_hands_back_its_own_pending_steps_and_those_passed_over(
    tmp_path, state, pending, passed_over,
) -> None:
    """It is noon on 12 October 2026, and round 1 (Bahrain) races in a week, or raced an hour ago.
    Weather hands back each of Phases 1 to 3 not yet posted (a mystery round's notice in their
    place, under `weather_p1`), and the forecast clean-up while a forecast of Phases 1 to 3 is
    still up; never a step already asked of the change queue. Of a raced round, the phases not
    posted are passed over instead, as the change queue would drop them as not due, though its
    clean-up is still pending. While weather is off, it hands back nothing."""
    from leaguebot.weather.services.test_mode_steps import pending_steps

    db_path = str(tmp_path / "weather_steps.db")
    await _seed(db_path, **state)
    handed = _round(
        format=state.get("format", "NORMAL"), scheduled_at=state.get("scheduled_at", AHEAD)
    )

    steps = await pending_steps(db_path, [handed], now=NOW)

    assert sorted((s.round_id, s.phase_number, s.kind) for s in steps.pending) == [
        (1, phase, kind) for phase, kind in pending
    ]
    assert sorted((s.round_id, s.phase_number, s.kind) for s in steps.passed_over) == [
        (1, phase, kind) for phase, kind in passed_over
    ]
