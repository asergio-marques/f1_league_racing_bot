"""Unit tests for test_mode_service — toggle, queue ordering, and review summary."""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone, timedelta

import pytest
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.services.scheduler_service import SchedulerService
from leaguebot.core.services.season_service import SeasonService
from leaguebot.core.services.test_mode_service import (
    toggle_test_mode,
    toggle_test_mode_nationality,
    count_live_real_drivers,
    get_next_pending_phase,
    build_review_summary,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _seed(db_path: str, rounds: list[dict]) -> None:
    """Seed a server_config, active season, and the supplied rounds list.

    Each round dict may contain:
        division_id  (default 1)
        format       (default 'NORMAL')
        track_name   (default 'Bahrain')
        scheduled_at (default now + 7 days)
        phase1_done  (default 0)
        phase2_done  (default 0)
        phase3_done  (default 0)
    """
    async with get_connection(db_path) as db:
        # Server config (test_mode_active starts at 0 via migration default)
        await db.execute(
            "INSERT INTO server_configs "
            "(server_id, interaction_role_id, interaction_channel_id, log_channel_id) "
            "VALUES (1, 100, 200, 300)"
        )
        # Season
        await db.execute(
            "INSERT INTO seasons (id, start_date, status) "
            "VALUES (1, '2026-01-01', 'ACTIVE')"
        )
        # Two divisions with deterministic ids
        await db.execute(
            "INSERT INTO divisions "
            "(id, season_id, name, mention_role_id, forecast_channel_id) "
            "VALUES (1, 1, 'Division A', 11, 21)"
        )
        await db.execute(
            "INSERT INTO divisions "
            "(id, season_id, name, mention_role_id, forecast_channel_id) "
            "VALUES (2, 1, 'Division B', 12, 22)"
        )

        default_sched = (
            datetime.now(timezone.utc) + timedelta(days=7)
        ).strftime("%Y-%m-%dT%H:%M:%S")

        for i, r in enumerate(rounds, start=1):
            await db.execute(
                "INSERT INTO rounds "
                "(id, division_id, round_number, format, track_name, scheduled_at, "
                " phase1_done, phase2_done, phase3_done) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    i,
                    r.get("division_id", 1),
                    i,  # round_number matches insertion index
                    r.get("format", "NORMAL"),
                    r.get("track_name", "Bahrain"),
                    r.get("scheduled_at", default_sched),
                    r.get("phase1_done", 0),
                    r.get("phase2_done", 0),
                    r.get("phase3_done", 0),
                ),
            )

        await db.commit()


# ---------------------------------------------------------------------------
# toggle_test_mode
# ---------------------------------------------------------------------------

async def test_toggle_enables_test_mode() -> None:
    """First toggle flips flag from 0 → 1 and returns True."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name
    try:
        await run_migrations(db_path)
        await _seed(db_path, [])
        result = await toggle_test_mode(db_path)
        assert result is True
    finally:
        os.unlink(db_path)


async def test_toggle_disables_test_mode() -> None:
    """Second toggle flips flag back from 1 → 0 and returns False."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name
    try:
        await run_migrations(db_path)
        await _seed(db_path, [])
        await toggle_test_mode(db_path)   # enable
        result = await toggle_test_mode(db_path)  # disable
        assert result is False
    finally:
        os.unlink(db_path)


async def test_toggle_missing_config_returns_false() -> None:
    """toggle_test_mode returns False when there is no config row."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name
    try:
        await run_migrations(db_path)  # no seed — no server_config row
        result = await toggle_test_mode(db_path)
        assert result is False
    finally:
        os.unlink(db_path)


# ---------------------------------------------------------------------------
# count_live_real_drivers
# ---------------------------------------------------------------------------

async def _add_driver(db_path: str, user_id: str, state: str, *, test: bool = False) -> None:
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO driver_profiles "
            "(discord_user_id, current_state, is_test_driver) VALUES (?, ?, ?)",
            (user_id, state, 1 if test else 0),
        )
        await db.commit()


async def test_an_empty_server_holds_no_real_drivers() -> None:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name
    try:
        await run_migrations(db_path)
        await _seed(db_path, [])

        assert await count_live_real_drivers(db_path) == 0
    finally:
        os.unlink(db_path)


@pytest.mark.parametrize(
    "state",
    ["PENDING_SIGNUP_COMPLETION", "PENDING_ADMIN_APPROVAL", "UNASSIGNED", "ASSIGNED"],
)
async def test_every_live_state_counts(state: str) -> None:
    """Anyone the league is holding — mid-signup, approved or placed — is a real driver."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name
    try:
        await run_migrations(db_path)
        await _seed(db_path, [])
        await _add_driver(db_path, "5001", state)

        assert await count_live_real_drivers(db_path) == 1
    finally:
        os.unlink(db_path)


async def test_a_former_driver_does_not_count() -> None:
    """A retained NOT_SIGNED_UP row is someone who has left, not a driver in the league."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name
    try:
        await run_migrations(db_path)
        await _seed(db_path, [])
        await _add_driver(db_path, "5002", "NOT_SIGNED_UP")

        assert await count_live_real_drivers(db_path) == 0
    finally:
        os.unlink(db_path)


async def test_fake_drivers_do_not_count() -> None:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name
    try:
        await run_migrations(db_path)
        await _seed(db_path, [])
        await _add_driver(db_path, "9000000000000000001", "ASSIGNED", test=True)
        await _add_driver(db_path, "5003", "ASSIGNED")

        assert await count_live_real_drivers(db_path) == 1
    finally:
        os.unlink(db_path)


# ---------------------------------------------------------------------------
# toggle_test_mode_nationality
# ---------------------------------------------------------------------------

async def test_nationality_toggle_starts_on() -> None:
    """Migration 042 defaults it on, as the signup setting it parallels defaults on."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name
    try:
        await run_migrations(db_path)
        await _seed(db_path, [])
        async with get_connection(db_path) as db:
            row = await (
                await db.execute(
                    "SELECT test_mode_nationality_required FROM server_configs "
                    "WHERE server_id = 1"
                )
            ).fetchone()
        assert row["test_mode_nationality_required"] == 1
    finally:
        os.unlink(db_path)


async def test_nationality_toggle_disables_then_enables() -> None:
    """First toggle flips 1 → 0, the second back again."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name
    try:
        await run_migrations(db_path)
        await _seed(db_path, [])
        assert await toggle_test_mode_nationality(db_path) is False
        assert await toggle_test_mode_nationality(db_path) is True
    finally:
        os.unlink(db_path)


async def test_nationality_toggle_leaves_test_mode_itself_alone() -> None:
    """Two switches, not one: flipping nationality must not disturb test mode."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name
    try:
        await run_migrations(db_path)
        await _seed(db_path, [])
        await toggle_test_mode(db_path)  # enable test mode
        await toggle_test_mode_nationality(db_path)
        async with get_connection(db_path) as db:
            row = await (
                await db.execute(
                    "SELECT test_mode_active FROM server_configs WHERE server_id = 1"
                )
            ).fetchone()
        assert row["test_mode_active"] == 1
    finally:
        os.unlink(db_path)


async def test_nationality_toggle_missing_config_returns_false() -> None:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name
    try:
        await run_migrations(db_path)  # no seed — no server_config row
        assert await toggle_test_mode_nationality(db_path) is False
    finally:
        os.unlink(db_path)


# ---------------------------------------------------------------------------
# get_next_pending_phase — queue ordering
# ---------------------------------------------------------------------------

async def test_empty_queue_returns_none() -> None:
    """All phases done → get_next_pending_phase returns None."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name
    try:
        await run_migrations(db_path)
        await _seed(db_path, [
            {"phase1_done": 1, "phase2_done": 1, "phase3_done": 1},
        ])
        result = await get_next_pending_phase(db_path)
        assert result is None
    finally:
        os.unlink(db_path)


async def test_mystery_round_notice_pending_returns_entry() -> None:
    """Mystery round with notice unsent (phase1_done=0) returns a phase_number=0 entry."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name
    try:
        await run_migrations(db_path)
        await _seed(db_path, [
            {"format": "MYSTERY", "phase1_done": 0, "phase2_done": 0, "phase3_done": 0},
        ])
        result = await get_next_pending_phase(db_path)
        assert result is not None
        assert result["phase_number"] == 0
    finally:
        os.unlink(db_path)


async def test_mystery_round_notice_done_excluded() -> None:
    """Mystery round with notice already sent (phase1_done=1) must not appear in queue."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name
    try:
        await run_migrations(db_path)
        await _seed(db_path, [
            {"format": "MYSTERY", "phase1_done": 1, "phase2_done": 0, "phase3_done": 0},
        ])
        result = await get_next_pending_phase(db_path)
        assert result is None
    finally:
        os.unlink(db_path)


async def test_phase_number_ordering_within_round() -> None:
    """Phase 1 done, Phase 2 not done → returns phase_number 2."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name
    try:
        await run_migrations(db_path)
        await _seed(db_path, [
            {"phase1_done": 1, "phase2_done": 0, "phase3_done": 0},
        ])
        result = await get_next_pending_phase(db_path)
        assert result is not None
        assert result["phase_number"] == 2
    finally:
        os.unlink(db_path)


async def test_earliest_scheduled_round_comes_first() -> None:
    """The round with the earlier scheduled_at is returned first."""
    earlier = (datetime.now(timezone.utc) + timedelta(days=5)).strftime(
        "%Y-%m-%dT%H:%M:%S"
    )
    later = (datetime.now(timezone.utc) + timedelta(days=10)).strftime(
        "%Y-%m-%dT%H:%M:%S"
    )
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name
    try:
        await run_migrations(db_path)
        await _seed(db_path, [
            {"track_name": "Monza",   "scheduled_at": later,   "phase1_done": 0},
            {"track_name": "Bahrain", "scheduled_at": earlier, "phase1_done": 0},
        ])
        result = await get_next_pending_phase(db_path)
        assert result is not None
        assert result["track_name"] == "Bahrain"
        assert result["phase_number"] == 1
    finally:
        os.unlink(db_path)


async def test_division_id_tiebreak_same_scheduled_at() -> None:
    """When two rounds have the same scheduled_at, lower division id comes first."""
    shared_sched = (datetime.now(timezone.utc) + timedelta(days=7)).strftime(
        "%Y-%m-%dT%H:%M:%S"
    )
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name
    try:
        await run_migrations(db_path)
        await _seed(db_path, [
            # division_id=2 listed first in rounds to ensure ordering is by d.id not insert order
            {
                "division_id": 2,
                "track_name": "Imola",
                "scheduled_at": shared_sched,
                "phase1_done": 0,
            },
            {
                "division_id": 1,
                "track_name": "Bahrain",
                "scheduled_at": shared_sched,
                "phase1_done": 0,
            },
        ])
        result = await get_next_pending_phase(db_path)
        assert result is not None
        assert result["division_id"] == 1
        assert result["track_name"] == "Bahrain"
    finally:
        os.unlink(db_path)


async def test_no_active_season_returns_none() -> None:
    """Returns None when there is no season in ACTIVE status."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name
    try:
        await run_migrations(db_path)
        async with get_connection(db_path) as db:
            await db.execute(
                "INSERT INTO server_configs "
                "(server_id, interaction_role_id, interaction_channel_id, log_channel_id) "
                "VALUES (1, 100, 200, 300)"
            )
            await db.execute(
                "INSERT INTO seasons (id, start_date, status) "
                "VALUES (1, '2026-01-01', 'SETUP')"  # SETUP, not ACTIVE
            )
            await db.commit()

        result = await get_next_pending_phase(db_path)
        assert result is None
    finally:
        os.unlink(db_path)


async def test_returns_phase1_for_fresh_round() -> None:
    """A round with all phases pending returns phase_number=1."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name
    try:
        await run_migrations(db_path)
        await _seed(db_path, [
            {"track_name": "Japan", "phase1_done": 0, "phase2_done": 0, "phase3_done": 0},
        ])
        result = await get_next_pending_phase(db_path)
        assert result is not None
        assert result["phase_number"] == 1
        assert result["track_name"] == "Japan"
    finally:
        os.unlink(db_path)


# ---------------------------------------------------------------------------
# build_review_summary
# ---------------------------------------------------------------------------

async def test_review_no_active_season() -> None:
    """Returns informative string when no active season exists."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name
    try:
        await run_migrations(db_path)
        # No server_config or season seeded
        summary = await build_review_summary(db_path)
        assert "No active season" in summary
    finally:
        os.unlink(db_path)


async def test_review_shows_phase_status() -> None:
    """Summary includes P1/P2/P3 completion indicators for non-Mystery rounds."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name
    try:
        await run_migrations(db_path)
        await _seed_with_weather(db_path, [
            {"phase1_done": 1, "phase2_done": 0, "phase3_done": 0, "track_name": "Monaco"},
        ])
        summary = await build_review_summary(db_path)
        assert "Monaco" in summary
        assert "P1: ✅" in summary
        assert "P2: ⏳" in summary
        assert "P3: ⏳" in summary
    finally:
        os.unlink(db_path)


async def test_review_mystery_round_shows_notice_not_phases() -> None:
    """Mystery rounds show 'Notice' status, not P1/P2/P3."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name
    try:
        await run_migrations(db_path)
        await _seed_with_weather(db_path, [
            {
                "format": "MYSTERY",
                "track_name": "Silverstone",
                "phase1_done": 0,
                "phase2_done": 0,
                "phase3_done": 0,
            },
        ])
        summary = await build_review_summary(db_path)
        assert "Silverstone" in summary
        assert "Notice: ⏳" in summary
        assert "P1:" not in summary
        assert "P2:" not in summary
        assert "P3:" not in summary
    finally:
        os.unlink(db_path)


# ---------------------------------------------------------------------------
# Scheduler path — misfired-job / empty-store fallback
# ---------------------------------------------------------------------------
# A stub scheduler that returns a configurable set of pending jobs, used to
# exercise the `scheduler_service is not None` code path.  Returning an empty
# list simulates all APScheduler jobs having misfired or been evicted.
# ---------------------------------------------------------------------------

class _StubScheduler:
    def __init__(self, pending_jobs: list[dict] | None = None) -> None:
        self._jobs = pending_jobs or []

    def get_pending_advance_jobs(self, round_ids: set[int]) -> list[dict]:  # noqa: ARG002
        return [j for j in self._jobs if j["round_id"] in round_ids]

    def get_queued_events_for_rounds(
        self, round_ids: set[int]  # noqa: ARG002
    ) -> set[tuple[int, str]]:
        return set()


async def _seed_with_weather(db_path: str, rounds: list[dict]) -> None:
    """Seed like _seed but also enable the weather module."""
    await _seed(db_path, rounds)
    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE server_configs SET weather_module_enabled = 1 WHERE server_id = 1"
        )
        await db.commit()


async def _seed_with_attendance(db_path: str, rounds: list[dict]) -> None:
    """Seed like _seed_with_weather but also enable the attendance module."""
    await _seed_with_weather(db_path, rounds)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO attendance_config "
            "(id, module_enabled) VALUES (1, 1)"
        )
        await db.commit()


async def test_misfired_fallback_returns_phase1() -> None:
    """Empty scheduler + weather enabled → phase 1 returned via DB flag fallback."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name
    try:
        await run_migrations(db_path)
        await _seed_with_weather(db_path, [
            {"track_name": "Monza", "phase1_done": 0, "phase2_done": 0, "phase3_done": 0},
        ])
        result = await get_next_pending_phase(db_path, _StubScheduler())
        assert result is not None
        assert result["phase_number"] == 1
        assert result["track_name"] == "Monza"
    finally:
        os.unlink(db_path)


async def test_misfired_fallback_respects_phase_flags() -> None:
    """phase1_done=1, phase2_done=0 → fallback returns phase 2, not phase 1."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name
    try:
        await run_migrations(db_path)
        await _seed_with_weather(db_path, [
            {"track_name": "Spa", "phase1_done": 1, "phase2_done": 0, "phase3_done": 0},
        ])
        result = await get_next_pending_phase(db_path, _StubScheduler())
        assert result is not None
        assert result["phase_number"] == 2
    finally:
        os.unlink(db_path)


async def test_misfired_fallback_weather_disabled_skips_phases() -> None:
    """Weather module disabled → weather phases never returned even with empty scheduler."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name
    try:
        await run_migrations(db_path)
        # _seed leaves weather_module_enabled=0 (default)
        await _seed(db_path, [
            {"track_name": "Monza", "phase1_done": 0, "phase2_done": 0, "phase3_done": 0},
        ])
        result = await get_next_pending_phase(db_path, _StubScheduler())
        assert result is None
    finally:
        os.unlink(db_path)


async def test_misfired_fallback_canonical_order() -> None:
    """Canonical order: P1 → RSVP-notice → P2 → RSVP-last → P3 → RSVP-deadline.

    Steps through one full round by calling get_next_pending_phase and manually
    advancing the relevant DB flag / RSVP row between each call.
    """
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name
    try:
        await run_migrations(db_path)
        await _seed_with_attendance(db_path, [
            {"track_name": "Monaco", "phase1_done": 0, "phase2_done": 0, "phase3_done": 0},
        ])
        stub = _StubScheduler()

        # Step 1 — phase 1
        r = await get_next_pending_phase(db_path, stub)
        assert r is not None and r["phase_number"] == 1
        async with get_connection(db_path) as db:
            await db.execute("UPDATE rounds SET phase1_done = 1 WHERE id = 1")
            await db.commit()

        # Step 2 — RSVP notice (phase 5)
        r = await get_next_pending_phase(db_path, stub)
        assert r is not None and r["phase_number"] == 5
        async with get_connection(db_path) as db:
            await db.execute(
                "INSERT INTO rsvp_embed_messages "
                "(round_id, division_id, message_id, channel_id, posted_at) "
                "VALUES (1, 1, 'msg1', 'ch1', '2026-01-01T00:00:00')"
            )
            await db.commit()

        # Step 3 — phase 2
        r = await get_next_pending_phase(db_path, stub)
        assert r is not None and r["phase_number"] == 2
        async with get_connection(db_path) as db:
            await db.execute("UPDATE rounds SET phase2_done = 1 WHERE id = 1")
            await db.commit()

        # Step 4 — RSVP last-notice (phase 6)
        r = await get_next_pending_phase(db_path, stub)
        assert r is not None and r["phase_number"] == 6
        async with get_connection(db_path) as db:
            await db.execute(
                "UPDATE rsvp_embed_messages SET last_notice_msg_id = 'msg2' "
                "WHERE round_id = 1 AND division_id = 1"
            )
            await db.commit()

        # Step 5 — phase 3
        r = await get_next_pending_phase(db_path, stub)
        assert r is not None and r["phase_number"] == 3
        async with get_connection(db_path) as db:
            await db.execute("UPDATE rounds SET phase3_done = 1 WHERE id = 1")
            await db.commit()

        # Step 6 — RSVP deadline (phase 7)
        r = await get_next_pending_phase(db_path, stub)
        assert r is not None and r["phase_number"] == 7
    finally:
        os.unlink(db_path)


async def test_earlier_misfired_round_beats_later_scheduler_job() -> None:
    """Earlier round with misfired phases must be advanced before a later
    round whose scheduler job is still pending. The earlier round is still to be raced: a
    raced round's weather steps are passed over (#439)."""
    earlier = (datetime.now(timezone.utc) + timedelta(days=2)).strftime(
        "%Y-%m-%dT%H:%M:%S"
    )
    later = (datetime.now(timezone.utc) + timedelta(days=5)).strftime(
        "%Y-%m-%dT%H:%M:%S"
    )
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name
    try:
        await run_migrations(db_path)
        await _seed_with_weather(db_path, [
            {"track_name": "Bahrain",  "scheduled_at": earlier, "phase1_done": 0, "division_id": 1},
            {"track_name": "Monza",    "scheduled_at": later,   "phase1_done": 0, "division_id": 1},
        ])
        # Scheduler only knows about round 2 (round 1 jobs evicted after misfire)
        stub = _StubScheduler([{
            "job_id": "phase1_r2",
            "round_id": 2,
            "phase_number": 1,
            "next_run_time": datetime.now(timezone.utc) + timedelta(days=5),
        }])
        result = await get_next_pending_phase(db_path, stub)
        assert result is not None
        assert result["track_name"] == "Bahrain"   # earlier round wins
        assert result["phase_number"] == 1
    finally:
        os.unlink(db_path)


# ---------------------------------------------------------------------------
# The two cleanups a day after the round (#425)
# ---------------------------------------------------------------------------
# A round's Phase 3 forecast and its check-in come down a day after it, and advance fires each
# as a step of its own (decided 2026-09-24): 8 for the forecast, 9 for the check-in.


async def _seed_all_done(db_path: str, rounds: list[dict], *, results: bool = False) -> None:
    """Seed with weather and attendance on, every round's forecasts published."""
    await _seed_with_attendance(
        db_path, [{"phase1_done": 1, "phase2_done": 1, "phase3_done": 1, **r} for r in rounds]
    )
    if results:
        async with get_connection(db_path) as db:
            await db.execute("INSERT INTO results_module_config (id, module_enabled) VALUES (1, 1)")
            await db.commit()


async def _stand(
    db_path: str,
    round_id: int,
    *,
    forecast: bool = False,
    call: bool = False,
    distributed: bool = True,
    status: str | None = None,
    forecast_phase: int = 3,
) -> None:
    """Record what is standing for *round_id*, and the state it is in: its forecast is of
    *forecast_phase*, Phase 3 unless given (0 a mystery round's notice)."""
    async with get_connection(db_path) as db:
        if forecast:
            await db.execute(
                "INSERT INTO forecast_messages "
                "(round_id, division_id, phase_number, message_id, posted_at) "
                "VALUES (?, 1, ?, ?, '2026-01-01T00:00:00')",
                (round_id, forecast_phase, 800 + round_id),
            )
        if call:
            await db.execute(
                "INSERT INTO rsvp_embed_messages "
                "(round_id, division_id, message_id, channel_id, posted_at, "
                " last_notice_msg_id, distribution_msg_id) "
                "VALUES (?, 1, ?, 'ch1', '2026-01-01T00:00:00', 'last', ?)",
                (round_id, f"call{round_id}", "dist" if distributed else None),
            )
        if status is not None:
            await db.execute("UPDATE rounds SET status = ? WHERE id = ?", (status, round_id))
        await db.commit()


def _job(round_id: int, phase: int, *, days: float) -> dict:
    return {
        "job_id": f"job_{phase}_r{round_id}",
        "round_id": round_id,
        "phase_number": phase,
        "next_run_time": datetime.now(timezone.utc) + timedelta(days=days),
    }


async def test_a_round_ends_with_its_forecast_then_its_check_in_coming_down(tmp_path) -> None:
    """With nothing else pending, what is left standing is what remains to take down — the
    forecast first, as the two fall together and it is the lower step."""
    db_path = str(tmp_path / "cleanups_last.db")
    await run_migrations(db_path)
    await _seed_all_done(db_path, [{"track_name": "Monaco"}])
    await _stand(db_path, 1, forecast=True, call=True, status="FINAL")
    stub = _StubScheduler()

    first = await get_next_pending_phase(db_path, stub)
    assert first is not None and first["phase_number"] == 8
    async with get_connection(db_path) as db:
        await db.execute("DELETE FROM forecast_messages")
        await db.commit()

    second = await get_next_pending_phase(db_path, stub)
    assert second is not None and second["phase_number"] == 9


async def test_a_cleared_round_is_not_offered_its_call_again(tmp_path) -> None:
    """Its call is gone and so is the row recording it — which is also what a round whose call
    is still to come looks like. The mark is what tells them apart, and without it advance
    would post the call of a round a day past (#425)."""
    db_path = str(tmp_path / "cleared.db")
    await run_migrations(db_path)
    await _seed_all_done(db_path, [{"track_name": "Monaco"}])
    async with get_connection(db_path) as db:
        await db.execute("UPDATE rounds SET checkin_cleared = 1 WHERE id = 1")
        await db.commit()

    assert await get_next_pending_phase(db_path, _StubScheduler()) is None


async def test_a_round_start_already_asked_of_the_queue_is_not_offered_again(tmp_path) -> None:
    """Round 1's result submission is the step advance would offer, ahead of the round's cleanup
    job: result submission never comes from the job store, and falls a day before the cleanup.
    An advance has already asked the queue to open it, and the opening is not done: the next
    advance passes over the round start, since offering it again would ask for a second
    channel."""
    import json

    db_path = str(tmp_path / "round_start_in_hand.db")
    await run_migrations(db_path)
    await _seed_all_done(db_path, [{"track_name": "Monaco"}], results=True)
    await _stand(db_path, 1, call=True)
    scheduler = _StubScheduler([_job(1, 9, days=1)])
    offered = await get_next_pending_phase(db_path, scheduler)
    assert offered is not None and offered["phase_number"] == 4

    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO queued_changes (kind, dedup_key, payload, origin, state, what) "
            "VALUES ('results.submission.open', 'results.submission.open:1', ?, 'BOT', 'QUEUED', "
            "'opening the results submission')",
            (json.dumps({"round_id": 1}),),
        )
        await db.commit()

    again = await get_next_pending_phase(db_path, scheduler)

    assert again is None or again["phase_number"] != 4


async def test_a_finished_round_s_cleanup_job_is_not_stale(tmp_path) -> None:
    """A finished round offers no other step, which once meant any job of its was thrown away
    as stale. Its cleanups are still to run."""
    db_path = str(tmp_path / "final_cleanup.db")
    await run_migrations(db_path)
    await _seed_all_done(db_path, [{"track_name": "Monaco"}], results=True)
    await _stand(db_path, 1, call=True, status="FINAL")

    result = await get_next_pending_phase(db_path, _StubScheduler([_job(1, 9, days=1)]))

    assert result is not None
    assert (result["phase_number"], result["job_id"]) == (9, "job_9_r1")


async def test_a_forecast_cleanup_with_no_forecast_standing_is_stale(tmp_path) -> None:
    """A round with no forecast recorded leaves nothing to take down, so its cleanup job is
    stale."""
    db_path = str(tmp_path / "nothing_to_clean.db")
    await run_migrations(db_path)
    await _seed_all_done(db_path, [{"track_name": "Monaco"}])
    await _stand(db_path, 1, status="FINAL")
    async with get_connection(db_path) as db:
        await db.execute("UPDATE rounds SET checkin_cleared = 1 WHERE id = 1")
        await db.commit()

    assert await get_next_pending_phase(db_path, _StubScheduler([_job(1, 8, days=1)])) is None


async def test_an_earlier_round_s_cleanup_does_not_jump_a_later_round_s_step(tmp_path) -> None:
    """A double-header: Sunday's deadline falls before Saturday's cleanup. The check of the
    rounds before a job must not offer Saturday's cleanup ahead of it."""
    saturday = (datetime.now(timezone.utc) + timedelta(days=6)).strftime("%Y-%m-%dT%H:%M:%S")
    sunday = (datetime.now(timezone.utc) + timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S")
    db_path = str(tmp_path / "double_header.db")
    await run_migrations(db_path)
    await _seed_all_done(
        db_path,
        [
            {"track_name": "Imola", "scheduled_at": saturday},
            {"track_name": "Monza", "scheduled_at": sunday},
        ],
    )
    await _stand(db_path, 1, call=True, status="FINAL")
    await _stand(db_path, 2, call=True, distributed=False)
    stub = _StubScheduler([_job(2, 7, days=6.9), _job(1, 9, days=7)])

    result = await get_next_pending_phase(db_path, stub)

    assert result is not None
    assert (result["round_id"], result["phase_number"]) == (2, 7)


async def test_the_database_fallback_offers_the_cleanups_last(tmp_path) -> None:
    """Round-by-round the fallback would reach a round's cleanup before the next round's first
    phase, though the cleanup falls days later; so every other step goes first."""
    db_path = str(tmp_path / "fallback_order.db")
    await run_migrations(db_path)
    await _seed_all_done(
        db_path, [{"track_name": "Imola"}, {"track_name": "Monza", "phase1_done": 0}]
    )
    await _stand(db_path, 1, call=True, status="FINAL")
    await _stand(db_path, 2, call=True)

    result = await get_next_pending_phase(db_path, _StubScheduler())

    assert result is not None
    assert (result["round_id"], result["phase_number"]) == (2, 1)


@pytest.mark.parametrize("found", ["job in the store", "database fallback"])
async def test_a_forecast_cleanup_is_offered_for_an_earlier_phase_still_standing(
    tmp_path, found
) -> None:
    """Round 1 is finished, its check-in cleared, and its Phase 3 never posted, so its Phase 2
    forecast stands. A day after the race the clean-up deletes whichever of Phases 1 to 3 is
    still recorded (owner, 2026-10-12), so its cleanup (8) is offered: from its job in the store,
    or from the database once the store is empty."""
    db_path = str(tmp_path / "phase_2_standing.db")
    await run_migrations(db_path)
    await _seed_all_done(db_path, [{"track_name": "Monaco"}])
    await _stand(db_path, 1, forecast=True, forecast_phase=2, status="FINAL")
    async with get_connection(db_path) as db:
        await db.execute("UPDATE rounds SET checkin_cleared = 1 WHERE id = 1")
        await db.commit()
    stub = _StubScheduler([_job(1, 8, days=1)] if found == "job in the store" else [])

    result = await get_next_pending_phase(db_path, stub)

    assert result is not None
    assert (result["round_id"], result["phase_number"]) == (1, 8)


async def test_a_mystery_round_s_notice_is_offered_no_forecast_cleanup(tmp_path) -> None:
    """A finished mystery round, its check-in cleared, with its notice (phase 0) standing: the
    notice is not a forecast the clean-up deletes, so nothing is offered."""
    db_path = str(tmp_path / "notice_standing.db")
    await run_migrations(db_path)
    await _seed_all_done(db_path, [{"track_name": None, "format": "MYSTERY"}])
    await _stand(db_path, 1, forecast=True, forecast_phase=0, status="FINAL")
    async with get_connection(db_path) as db:
        await db.execute("UPDATE rounds SET checkin_cleared = 1 WHERE id = 1")
        await db.commit()

    assert await get_next_pending_phase(db_path, _StubScheduler([_job(1, 8, days=1)])) is None
    assert await get_next_pending_phase(db_path, _StubScheduler()) is None


# ---------------------------------------------------------------------------
# The review reports the cleanups (#425)
# ---------------------------------------------------------------------------
# Test mode shall report, for every round, which of its scheduled work has run and which
# remains — the cleanups a day after the round among it.


@pytest.mark.parametrize(
    "phase3_done,forecast_phase,shown",
    [(0, None, "Cleanup: ⏳"), (1, 3, "Cleanup: ⏳"), (1, 2, "Cleanup: ⏳"), (1, None, "Cleanup: ✅")],
    ids=["phase-3-to-come", "forecast-standing", "earlier-phase-standing", "taken-down"],
)
async def test_the_review_shows_the_forecast_cleanup(
    tmp_path, phase3_done, forecast_phase, shown
) -> None:
    """Where a forecast stands, of the phase given, the clean-up is still to come. Where Round
    1's Phase 3 was drawn but never posted, its Phase 2 forecast stands: the clean-up deletes
    whichever of Phases 1 to 3 is still recorded (owner, 2026-10-12), so the review shows it
    still to come."""
    db_path = str(tmp_path / "review_forecast_cleanup.db")
    await run_migrations(db_path)
    await _seed_with_weather(
        db_path, [{"phase1_done": 1, "phase2_done": 1, "phase3_done": phase3_done}]
    )
    if forecast_phase is None:
        await _stand(db_path, 1)
    else:
        await _stand(db_path, 1, forecast=True, forecast_phase=forecast_phase)

    assert shown in await build_review_summary(db_path)


async def test_the_review_shows_a_standing_check_in_as_still_to_clear(tmp_path) -> None:
    db_path = str(tmp_path / "review_standing_call.db")
    await run_migrations(db_path)
    await _seed_with_attendance(db_path, [{"track_name": "Monaco"}])
    await _stand(db_path, 1, call=True)

    summary = await build_review_summary(db_path)

    assert "RSVP: ✅  Last: ✅  Deadline: ✅  Cleared: ⏳" in summary


async def test_the_review_shows_a_cleared_check_in_as_done_throughout(tmp_path) -> None:
    """Its record went with its messages, which would otherwise read as a call still to come."""
    db_path = str(tmp_path / "review_cleared.db")
    await run_migrations(db_path)
    await _seed_with_attendance(db_path, [{"track_name": "Monaco"}])
    async with get_connection(db_path) as db:
        await db.execute("UPDATE rounds SET checkin_cleared = 1 WHERE id = 1")
        await db.commit()

    summary = await build_review_summary(db_path)

    assert "RSVP: ✅  Last: ✅  Deadline: ✅  Cleared: ✅" in summary


# ---------------------------------------------------------------------------
# The review finds the scheduler's own jobs (#426)
# ---------------------------------------------------------------------------
# The review once looked for jobs under IDs of its own making — `phase1_r{id}`, `results_r{id}` —
# a shape no job has ever had, so every pending step read ⚠️ whether its job was queued or not.
# These tests arm their jobs with the production writers, never with hand-written IDs, so the
# review and the scheduler cannot drift apart unseen again.


@pytest.fixture
async def paused_scheduler():
    """A `SchedulerService` over a real APScheduler, started paused so nothing it holds can fire.

    In memory rather than on SQLite: the store is not the subject, and a job store left holding a
    file fails only on Windows. Started, because a stopped scheduler keeps its jobs pending with
    no fire time at all.
    """
    service = SchedulerService.__new__(SchedulerService)
    service._scheduler = AsyncIOScheduler(jobstores={"default": MemoryJobStore()}, timezone="UTC")
    service._scheduler.start(paused=True)
    yield service
    service._scheduler.shutdown(wait=False)


async def _arm(
    db_path: str,
    service: SchedulerService,
    round_ids: list[int],
    *,
    weather: bool = True,
    attendance: bool = True,
    last_notice_hours: int = 24,
) -> None:
    """Arm *round_ids* as approval arms them, with the production `schedule_*` writers."""
    season_service = SeasonService(db_path)
    for round_id in round_ids:
        rnd = await season_service.get_round(round_id)
        assert rnd is not None
        if weather:
            service.schedule_round(rnd, season_number=1, division_tier=rnd.division_id)
        if attendance:
            service.schedule_attendance_round(
                rnd,
                season_number=1,
                division_tier=rnd.division_id,
                notice_days=5,
                last_notice_hours=last_notice_hours,
                deadline_hours=2,
            )


async def _season_armed_as_approval_arms_it(tmp_path, service: SchedulerService) -> str:
    """Weather, the check-in and results on; a normal and a mystery round in Division A and a
    normal round in Division B, all a week ahead; every one of their jobs queued."""
    db_path = str(tmp_path / "review_jobs.db")
    await run_migrations(db_path)
    await _seed_with_attendance(
        db_path,
        [
            {"track_name": "Monaco"},
            {"format": "MYSTERY", "track_name": None},
            {"track_name": "Spa", "division_id": 2},
        ],
    )
    async with get_connection(db_path) as db:
        await db.execute("INSERT INTO results_module_config (id, module_enabled) VALUES (1, 1)")
        await db.commit()
    await _arm(db_path, service, [1, 2, 3])
    return db_path


def _round_row(summary: str, round_number: int) -> str:
    rows = [line for line in summary.splitlines() if line.startswith(f"  Round {round_number} ")]
    assert len(rows) == 1, summary
    return rows[0]


async def test_the_review_marks_every_queued_job_as_queued(tmp_path, paused_scheduler) -> None:
    """Every step of a season armed as approval arms it has its job queued, and reads ⏳."""
    db_path = await _season_armed_as_approval_arms_it(tmp_path, paused_scheduler)

    summary = await build_review_summary(db_path, paused_scheduler)

    check_in = "Results: ⏳  |  RSVP: ⏳  Last: ⏳  Deadline: ⏳  Cleared: ⏳"
    assert _round_row(summary, 1).endswith(f"P1: ⏳  P2: ⏳  P3: ⏳  Cleanup: ⏳  |  {check_in}")
    assert _round_row(summary, 2).endswith(f"Notice: ⏳  |  {check_in}")
    assert _round_row(summary, 3).endswith(f"P1: ⏳  P2: ⏳  P3: ⏳  Cleanup: ⏳  |  {check_in}")


@pytest.mark.parametrize(
    "round_id,event_type,cell",
    [
        (1, "weather_p1", "P1"),
        (1, "weather_p2", "P2"),
        (1, "weather_p3", "P3"),
        (1, "cleanup", "Cleanup"),
        (1, "results", "Results"),
        (1, "rsvp_notice", "RSVP"),
        (1, "rsvp_last_notice", "Last"),
        (1, "rsvp_deadline", "Deadline"),
        (1, "rsvp_cleanup", "Cleared"),
        (2, "weather_p1", "Notice"),
    ],
    ids=[
        "p1", "p2", "p3", "forecast-cleanup", "results", "call", "last-notice", "deadline",
        "check-in-cleanup", "mystery-notice",
    ],
)
async def test_a_job_taken_from_the_store_reads_as_missing(
    tmp_path, paused_scheduler, round_id, event_type, cell
) -> None:
    """Each cell reads its own job and no other. A mystery round's notice is armed as
    `weather_p1`, so taking that job away is what leaves its `Notice` without one."""
    db_path = await _season_armed_as_approval_arms_it(tmp_path, paused_scheduler)
    paused_scheduler.cancel_round(round_id, only=frozenset({event_type}))

    row = _round_row(await build_review_summary(db_path, paused_scheduler), round_id)

    assert f"{cell}: ⚠️" in row
    assert row.count("⚠️") == 1, row


async def test_the_review_shows_no_weather_step_while_weather_is_off(tmp_path) -> None:
    """Nothing of the weather module is armed while it is off, and advance runs none of it, so
    the review shows none of it — as it shows no result submission or check-in while theirs are
    off (decided 2026-09-24, #426). A mystery round's notice is a weather posting too."""
    db_path = str(tmp_path / "review_weather_off.db")
    await run_migrations(db_path)
    await _seed(db_path, [{"track_name": "Monaco"}, {"format": "MYSTERY", "track_name": None}])

    summary = await build_review_summary(db_path)

    assert _round_row(summary, 1) and _round_row(summary, 2)
    for cell in ("P1:", "P2:", "P3:", "Cleanup:", "Notice:"):
        assert cell not in summary, summary


# ---------------------------------------------------------------------------
# A mystery round's weather jobs in advance (#426)
# ---------------------------------------------------------------------------
# A mystery round's notice is armed as `weather_p1`, with `weather_p2` and `weather_p3` beside it
# that do nothing when they fire. The job store knows nothing of formats, and advance once ran
# the three as weather Phases 1 to 3 — posting nothing and reporting success, or, where the round
# names its hidden track, forecasting it.


async def _mystery_round_armed(
    tmp_path, service: SchedulerService, *, notice_posted: bool
) -> str:
    """A mystery round a week ahead, weather and results on, its jobs armed as approval arms
    them."""
    db_path = str(tmp_path / "mystery_jobs.db")
    await run_migrations(db_path)
    await _seed_with_weather(
        db_path,
        [{"format": "MYSTERY", "track_name": None, "phase1_done": 1 if notice_posted else 0}],
    )
    async with get_connection(db_path) as db:
        await db.execute("INSERT INTO results_module_config (id, module_enabled) VALUES (1, 1)")
        await db.commit()
    await _arm(db_path, service, [1], attendance=False)
    return db_path


async def test_a_mystery_round_s_queued_notice_is_advanced_as_the_notice(
    tmp_path, paused_scheduler
) -> None:
    db_path = await _mystery_round_armed(tmp_path, paused_scheduler, notice_posted=False)

    result = await get_next_pending_phase(db_path, paused_scheduler)

    assert result is not None
    assert result["phase_number"] == 0
    assert str(result["job_id"]).startswith("weather_p1_")


async def test_a_mystery_round_s_notice_job_left_after_its_notice_is_stale(
    tmp_path, paused_scheduler
) -> None:
    """A notice posted from database state can leave its job queued. Advancing that job would
    post the notice a second time, so the round's next step — its result submission — comes
    instead."""
    db_path = await _mystery_round_armed(tmp_path, paused_scheduler, notice_posted=True)
    paused_scheduler.cancel_round(1, only=frozenset({"weather_p2", "weather_p3"}))

    result = await get_next_pending_phase(db_path, paused_scheduler)

    assert result is not None
    assert result["phase_number"] == 4


async def test_a_mystery_round_s_phase_2_and_3_jobs_are_passed_over(
    tmp_path, paused_scheduler
) -> None:
    """They do nothing when they fire in a live season, so advance has nothing to fire for them:
    handed on as Phases 2 and 3, they reached the weather runners, which read no format."""
    db_path = await _mystery_round_armed(tmp_path, paused_scheduler, notice_posted=True)
    paused_scheduler.cancel_round(1, only=frozenset({"weather_p1"}))

    result = await get_next_pending_phase(db_path, paused_scheduler)

    assert result is not None
    assert result["phase_number"] == 4


# ---------------------------------------------------------------------------
# A last notice switched off (#426)
# ---------------------------------------------------------------------------
# `/attendance config rsvp-last-notice 0` means no last notice is sent, and a live season arms no
# job for one. Advance once found one owing from database state regardless, and posted it.


async def _last_notice_switched_off(db_path: str) -> None:
    """The check-in on with its last notice set to 0, and round 1's call and distribution posted
    — so no last notice, the one a live season would never have sent."""
    fortnight = (datetime.now(timezone.utc) + timedelta(days=14)).strftime("%Y-%m-%dT%H:%M:%S")
    await run_migrations(db_path)
    await _seed(db_path, [{"track_name": "Monaco"}, {"track_name": "Spa", "scheduled_at": fortnight}])
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO attendance_config (id, module_enabled, rsvp_last_notice_hours) "
            "VALUES (1, 1, 0)"
        )
        await db.execute(
            "INSERT INTO rsvp_embed_messages "
            "(round_id, division_id, message_id, channel_id, posted_at, distribution_msg_id) "
            "VALUES (1, 1, 'call1', 'ch1', '2026-01-01T00:00:00', 'dist1')"
        )
        await db.commit()


async def test_a_switched_off_last_notice_is_never_offered(tmp_path, paused_scheduler) -> None:
    db_path = str(tmp_path / "last_notice_off.db")
    await _last_notice_switched_off(db_path)
    await _arm(db_path, paused_scheduler, [2], weather=False, last_notice_hours=0)

    result = await get_next_pending_phase(db_path, paused_scheduler)

    assert result is not None
    assert (result["round_id"], result["phase_number"]) == (2, 5)


async def test_the_review_leaves_out_a_switched_off_last_notice(tmp_path) -> None:
    """A step the league has switched off is left out rather than marked, as a module's steps are
    while it is off (decided 2026-09-24, #426) — here, and where the check-in has come down."""
    db_path = str(tmp_path / "review_last_notice_off.db")
    await _last_notice_switched_off(db_path)
    async with get_connection(db_path) as db:
        await db.execute("UPDATE rounds SET checkin_cleared = 1 WHERE id = 2")
        await db.commit()

    summary = await build_review_summary(db_path)

    assert "Last:" not in summary, summary
    assert _round_row(summary, 1).endswith("RSVP: ✅  Deadline: ✅  Cleared: ⏳")
    assert _round_row(summary, 2).endswith("RSVP: ✅  Deadline: ✅  Cleared: ✅")


# ---------------------------------------------------------------------------
# A mystery round's notice while weather is off (#426)
# ---------------------------------------------------------------------------


async def test_no_mystery_notice_is_offered_while_weather_is_off(tmp_path) -> None:
    """The notice is a weather posting, and a disabled module produces nothing: a live season
    arms no job for it. Advance once found it owing from database state regardless."""
    db_path = str(tmp_path / "mystery_weather_off.db")
    await run_migrations(db_path)
    await _seed(db_path, [{"format": "MYSTERY", "track_name": None}])

    assert await get_next_pending_phase(db_path, _StubScheduler()) is None



# ---------------------------------------------------------------------------
# A weather step already in hand on the change queue (#439)
# ---------------------------------------------------------------------------


async def test_a_weather_step_in_hand_on_the_queue_is_not_offered_again(tmp_path) -> None:
    """Weather is on and round 1 (Monza) has none of its phases done; its scheduler job was
    cancelled by the advance that asked the change queue for its Phase 1, which waits there
    unrun. The next advance does not offer round 1's Phase 1 again. Before that change was
    asked, it was the step offered."""
    import json

    db_path = os.path.join(tmp_path, "test.db")
    await run_migrations(db_path)
    await _seed_with_weather(db_path, [{"track_name": "Monza"}])
    first = await get_next_pending_phase(db_path, _StubScheduler())
    assert first is not None
    assert (first["round_id"], first["phase_number"]) == (1, 1)

    payload = {"round_id": 1, "phase": 1, "division_name": "Division A", "round_number": 1}
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO queued_changes (kind, dedup_key, payload, origin, state, what) "
            "VALUES ('weather.forecast', 'weather.forecast:1:1', ?, 'BOT', 'QUEUED', "
            "'drawing the Phase 1 forecast of round 1 in **Division A**')",
            (json.dumps(payload),),
        )
        await db.commit()

    result = await get_next_pending_phase(db_path, _StubScheduler())
    assert result is None or (result["round_id"], result["phase_number"]) != (1, 1)


#: Each check-in step advance offers, by its phase: the change that asks for it, and the rows the
#: round holds when it is next (a call standing with or without its last notice, the deadline
#: marked or not, the round over).
_CHECK_IN_STEPS_IN_HAND = [
    pytest.param(5, "attendance.check_in.call", None, id="call"),
    pytest.param(6, "attendance.check_in.last_notice", (None, None), id="last notice"),
    pytest.param(7, "attendance.check_in.deadline", ("last", None), id="deadline"),
    pytest.param(9, "attendance.check_in.clean_up", ("last", "dist"), id="clean-up"),
]


@pytest.mark.parametrize("phase,kind,call", _CHECK_IN_STEPS_IN_HAND)
async def test_a_check_in_step_in_hand_on_the_queue_is_not_offered_again(
    tmp_path, phase, kind, call
) -> None:
    """Round 1's check-in call (5), last notice (6), deadline (7) or clean-up (9) is the step
    advance offers. An earlier advance has asked the change queue for it, cancelling its
    scheduler job, and it waits there unrun: the next advance does not offer the same step
    again, which would ask a second time."""
    import json

    db_path = str(tmp_path / "check_in_in_hand.db")
    await run_migrations(db_path)
    await _seed_all_done(db_path, [{"track_name": "Monaco"}])
    async with get_connection(db_path) as db:
        if call is not None:
            await db.execute(
                "INSERT INTO rsvp_embed_messages "
                "(round_id, division_id, message_id, channel_id, posted_at, "
                " last_notice_msg_id, distribution_msg_id) "
                "VALUES (1, 1, 'call1', 'ch1', '2026-01-01T00:00:00', ?, ?)",
                call,
            )
        if phase == 9:
            await db.execute("UPDATE rounds SET status = 'FINAL' WHERE id = 1")
        await db.commit()
    first = await get_next_pending_phase(db_path, _StubScheduler())
    assert first is not None and (first["round_id"], first["phase_number"]) == (1, phase)

    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO queued_changes (kind, dedup_key, payload, origin, state, what) "
            "VALUES (?, ?, ?, 'BOT', 'QUEUED', 'a check-in step')",
            (kind, f"{kind}:1", json.dumps({"round_id": 1})),
        )
        await db.commit()

    again = await get_next_pending_phase(db_path, _StubScheduler())
    assert again is None or (again["round_id"], again["phase_number"]) != (1, phase)


# ---------------------------------------------------------------------------
# A weather step of a round whose race has been run (#439)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("scheduler", ["job left in the store", "job store empty", "no scheduler"])
async def test_a_raced_round_s_weather_steps_are_passed_over(tmp_path, scheduler) -> None:
    """Weather is on. Round 1 (Bahrain) was raced an hour ago with none of its phases done (the
    module was switched on after it), and round 2 (Monza) is a week ahead. The change queue
    drops a forecast for a race already run (A25), so advance offers round 2's Phase 1, and not
    round 1's: whether round 1's Phase 1 job is still in the store, the store is empty, or no
    scheduler is given."""
    raced = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S")
    db_path = str(tmp_path / "raced.db")
    await run_migrations(db_path)
    await _seed_with_weather(db_path, [
        {"track_name": "Bahrain", "scheduled_at": raced},
        {"track_name": "Monza"},
    ])
    stub = {
        "job left in the store": _StubScheduler([_job(1, 1, days=-2)]),
        "job store empty": _StubScheduler(),
        "no scheduler": None,
    }[scheduler]

    result = await get_next_pending_phase(db_path, stub)

    assert result is not None
    assert (result["round_id"], result["phase_number"]) == (2, 1)


async def test_a_raced_round_s_result_submission_is_still_offered(tmp_path) -> None:
    """Weather and results are on. Round 1 (Bahrain) was raced an hour ago with none of its
    phases done and no results submitted: advance passes over its weather steps alone, and
    offers its result submission."""
    raced = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S")
    db_path = str(tmp_path / "raced_results.db")
    await run_migrations(db_path)
    await _seed_with_weather(db_path, [
        {"track_name": "Bahrain", "scheduled_at": raced},
        {"track_name": "Monza"},
    ])
    async with get_connection(db_path) as db:
        await db.execute("INSERT INTO results_module_config (id, module_enabled) VALUES (1, 1)")
        await db.commit()

    result = await get_next_pending_phase(db_path, _StubScheduler())

    assert result is not None
    assert (result["round_id"], result["phase_number"]) == (1, 4)


async def test_the_review_marks_a_raced_round_s_weather_steps_as_passed_over(tmp_path) -> None:
    """Weather is on, and it is noon on 12 October 2026; no job is queued. Round 1 (Bahrain) was
    raced an hour ago with none of its phases done; round 2 (Monza), raced an hour ago too, had
    Phases 1 and 2 done, Phase 3 never, and its Phase 2 forecast stands; round 3 is a mystery
    round raced an hour ago whose notice never went out; round 4 (Spa) is a week ahead. Advance
    passes over the weather steps of a raced round (the change queue drops them, A25), so the
    review marks each undone one ⏭️, not ⚠️ ("use /test-mode advance"): round 1's clean-up has
    nothing standing to delete and is done, round 2's still has its Phase 2 forecast to delete.
    Round 4's steps are still to be advanced, ⚠️ with no job, and the legend explains ⏭️."""
    now = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)
    raced = "2026-10-12T11:00:00"
    db_path = str(tmp_path / "review_raced.db")
    await run_migrations(db_path)
    await _seed_with_weather(db_path, [
        {"track_name": "Bahrain", "scheduled_at": raced},
        {"track_name": "Monza", "scheduled_at": raced, "phase1_done": 1, "phase2_done": 1},
        {"track_name": "Mystery", "scheduled_at": raced, "format": "MYSTERY"},
        {"track_name": "Spa", "scheduled_at": "2026-10-19T12:00:00"},
    ])
    await _stand(db_path, 2, forecast=True, forecast_phase=2)

    summary = await build_review_summary(db_path, _StubScheduler(), now=now)

    assert "P1: ⏭️  P2: ⏭️  P3: ⏭️  Cleanup: ✅" in _round_row(summary, 1)
    assert "P1: ✅  P2: ✅  P3: ⏭️  Cleanup: ⚠️" in _round_row(summary, 2)
    assert "Notice: ⏭️" in _round_row(summary, 3)
    assert "P1: ⚠️  P2: ⚠️  P3: ⚠️  Cleanup: ⚠️" in _round_row(summary, 4)
    assert "⏭️ passed over (its race has been run)" in summary


@pytest.mark.parametrize("scheduler", ["job left in the store", "job store empty", "no scheduler"])
async def test_a_raced_mystery_round_s_notice_is_passed_over(tmp_path, scheduler) -> None:
    """Weather is on, and it is noon on 12 October 2026. Round 1 is a mystery round raced an hour
    ago whose notice never went out, and round 2 (Monza) is a week ahead. The change queue drops
    a notice for a race already run (A25), so advance offers round 2's Phase 1, and not round
    1's notice: whether round 1's `weather_p1` job is still in the store, the store is empty, or
    no scheduler is given."""
    now = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)
    db_path = str(tmp_path / "raced_mystery.db")
    await run_migrations(db_path)
    await _seed_with_weather(db_path, [
        {"track_name": "Mystery", "format": "MYSTERY", "scheduled_at": "2026-10-12T11:00:00"},
        {"track_name": "Monza", "scheduled_at": "2026-10-19T12:00:00"},
    ])
    stub = {
        "job left in the store": _StubScheduler([_job(1, 1, days=-2)]),
        "job store empty": _StubScheduler(),
        "no scheduler": None,
    }[scheduler]

    result = await get_next_pending_phase(db_path, stub, now=now)

    assert result is not None
    assert (result["round_id"], result["phase_number"]) == (2, 1)


# ---------------------------------------------------------------------------
# The weather steps advance passes over, which its reply names (#439)
# ---------------------------------------------------------------------------


async def test_the_steps_passed_over_are_a_raced_round_s_undone_weather_steps(tmp_path) -> None:
    """Weather is on, and it is noon on 12 October 2026. Round 1 (Bahrain) was raced an hour ago
    with none of its phases done; round 2 is a mystery round raced an hour ago whose notice never
    went out; round 3 (Monza), raced too, had all three phases done; round 4 (Spa) is a week
    ahead. Advance passes over the weather steps of a raced round (owner, 2026-10-12, "Advance
    names what it skipped"), so the steps it names as passed over are round 1's Phases 1, 2 and
    3 and round 2's notice, in that order, and nothing of rounds 3 or 4."""
    from leaguebot.core.services.test_mode_service import get_passed_over_steps

    now = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)
    raced = "2026-10-12T11:00:00"
    db_path = str(tmp_path / "passed_over.db")
    await run_migrations(db_path)
    await _seed_with_weather(db_path, [
        {"track_name": "Bahrain", "scheduled_at": raced},
        {"track_name": "Mystery", "scheduled_at": raced, "format": "MYSTERY"},
        {"track_name": "Monza", "scheduled_at": raced,
         "phase1_done": 1, "phase2_done": 1, "phase3_done": 1},
        {"track_name": "Spa", "scheduled_at": "2026-10-19T12:00:00"},
    ])

    steps = await get_passed_over_steps(db_path, now=now)

    assert [(step["round_number"], step["phase_number"]) for step in steps] == [
        (1, 1), (1, 2), (1, 3), (2, 0),
    ]
    assert {step["division_name"] for step in steps} == {"Division A"}


async def test_no_step_is_passed_over_while_the_weather_module_is_off(tmp_path) -> None:
    """Weather is off, so nothing of it was armed and advance offers none of it: a raced round's
    undone phases are not named as passed over."""
    from leaguebot.core.services.test_mode_service import get_passed_over_steps

    now = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)
    db_path = str(tmp_path / "passed_over_off.db")
    await run_migrations(db_path)
    await _seed(db_path, [{"track_name": "Bahrain", "scheduled_at": "2026-10-12T11:00:00"}])

    assert await get_passed_over_steps(db_path, now=now) == []


async def _queue_forecast(db_path: str, round_id: int, phase: int) -> None:
    """A `weather.forecast` change for *phase* of *round_id* asked of the queue and not yet run."""
    import json

    payload = {"round_id": round_id, "phase": phase, "division_name": "Division A",
               "round_number": round_id}
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO queued_changes (kind, dedup_key, payload, origin, state, what) "
            "VALUES ('weather.forecast', ?, ?, 'BOT', 'QUEUED', 'drawing a forecast')",
            (f"weather.forecast:{round_id}:{phase}", json.dumps(payload)),
        )
        await db.commit()


@pytest.mark.parametrize("case, left", [
    pytest.param("FINAL", [], id="round final"),
    pytest.param("AWAITING_APPEAL_VERDICTS", [], id="round awaiting appeal verdicts"),
    pytest.param("phase 1 in hand", [2, 3], id="phase 1 in hand on the queue"),
    pytest.param("division cancelled", [], id="division cancelled"),
    pytest.param("round cancelled", [], id="round cancelled"),
])
async def test_a_raced_step_advance_would_not_offer_anyway_is_not_named_as_passed_over(
    tmp_path, case, left,
) -> None:
    """Weather is on, and it is noon on 12 October 2026. Round 1 (Bahrain) was raced an hour ago
    with none of its phases done, so Phases 1, 2 and 3 would be named as passed over. But advance
    offers nothing of a round that has ended (final, or its appeals being judged), of a cancelled
    round or division, nor a step already in hand on the change queue: so none of those is named
    either, and with Phase 1 in hand only Phases 2 and 3 are."""
    from leaguebot.core.services.test_mode_service import get_passed_over_steps

    now = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)
    db_path = str(tmp_path / "passed_over_limits.db")
    await run_migrations(db_path)
    await _seed_with_weather(db_path, [
        {"track_name": "Bahrain", "scheduled_at": "2026-10-12T11:00:00"},
    ])
    if case == "phase 1 in hand":
        await _queue_forecast(db_path, 1, 1)
    else:
        sql = {
            "FINAL": "UPDATE rounds SET status = 'FINAL' WHERE id = 1",
            "AWAITING_APPEAL_VERDICTS":
                "UPDATE rounds SET status = 'AWAITING_APPEAL_VERDICTS' WHERE id = 1",
            "division cancelled": "UPDATE divisions SET status = 'CANCELLED' WHERE id = 1",
            "round cancelled": "UPDATE rounds SET status = 'CANCELLED' WHERE id = 1",
        }[case]
        async with get_connection(db_path) as db:
            await db.execute(sql)
            await db.commit()

    steps = await get_passed_over_steps(db_path, now=now)

    assert [step["phase_number"] for step in steps] == left

