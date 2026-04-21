"""Unit tests for test_mode_service — toggle, queue ordering, and review summary."""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timezone, timedelta

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations
from services.test_mode_service import (
    toggle_test_mode,
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
            "INSERT INTO seasons (id, server_id, start_date, status) "
            "VALUES (1, 1, '2026-01-01', 'ACTIVE')"
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
        result = await toggle_test_mode(1, db_path)
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
        await toggle_test_mode(1, db_path)   # enable
        result = await toggle_test_mode(1, db_path)  # disable
        assert result is False
    finally:
        os.unlink(db_path)


async def test_toggle_missing_config_returns_false() -> None:
    """toggle_test_mode returns False when there is no config row."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name
    try:
        await run_migrations(db_path)  # no seed — no server_config row
        result = await toggle_test_mode(999, db_path)
        assert result is False
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
        result = await get_next_pending_phase(1, db_path)
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
        result = await get_next_pending_phase(1, db_path)
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
        result = await get_next_pending_phase(1, db_path)
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
        result = await get_next_pending_phase(1, db_path)
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
        result = await get_next_pending_phase(1, db_path)
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
        result = await get_next_pending_phase(1, db_path)
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
                "INSERT INTO seasons (id, server_id, start_date, status) "
                "VALUES (1, 1, '2026-01-01', 'SETUP')"  # SETUP, not ACTIVE
            )
            await db.commit()

        result = await get_next_pending_phase(1, db_path)
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
        result = await get_next_pending_phase(1, db_path)
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
        summary = await build_review_summary(1, db_path)
        assert "No active season" in summary
    finally:
        os.unlink(db_path)


async def test_review_shows_phase_status() -> None:
    """Summary includes P1/P2/P3 completion indicators for non-Mystery rounds."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name
    try:
        await run_migrations(db_path)
        await _seed(db_path, [
            {"phase1_done": 1, "phase2_done": 0, "phase3_done": 0, "track_name": "Monaco"},
        ])
        summary = await build_review_summary(1, db_path)
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
        await _seed(db_path, [
            {
                "format": "MYSTERY",
                "track_name": "Silverstone",
                "phase1_done": 0,
                "phase2_done": 0,
                "phase3_done": 0,
            },
        ])
        summary = await build_review_summary(1, db_path)
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

    def get_job_ids_for_rounds(self, round_ids: set[int]) -> set[str]:  # noqa: ARG002
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
            "(server_id, module_enabled) VALUES (1, 1)"
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
        result = await get_next_pending_phase(1, db_path, _StubScheduler())
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
        result = await get_next_pending_phase(1, db_path, _StubScheduler())
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
        result = await get_next_pending_phase(1, db_path, _StubScheduler())
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
        r = await get_next_pending_phase(1, db_path, stub)
        assert r is not None and r["phase_number"] == 1
        async with get_connection(db_path) as db:
            await db.execute("UPDATE rounds SET phase1_done = 1 WHERE id = 1")
            await db.commit()

        # Step 2 — RSVP notice (phase 5)
        r = await get_next_pending_phase(1, db_path, stub)
        assert r is not None and r["phase_number"] == 5
        async with get_connection(db_path) as db:
            await db.execute(
                "INSERT INTO rsvp_embed_messages "
                "(round_id, division_id, message_id, channel_id, posted_at) "
                "VALUES (1, 1, 'msg1', 'ch1', '2026-01-01T00:00:00')"
            )
            await db.commit()

        # Step 3 — phase 2
        r = await get_next_pending_phase(1, db_path, stub)
        assert r is not None and r["phase_number"] == 2
        async with get_connection(db_path) as db:
            await db.execute("UPDATE rounds SET phase2_done = 1 WHERE id = 1")
            await db.commit()

        # Step 4 — RSVP last-notice (phase 6)
        r = await get_next_pending_phase(1, db_path, stub)
        assert r is not None and r["phase_number"] == 6
        async with get_connection(db_path) as db:
            await db.execute(
                "UPDATE rsvp_embed_messages SET last_notice_msg_id = 'msg2' "
                "WHERE round_id = 1 AND division_id = 1"
            )
            await db.commit()

        # Step 5 — phase 3
        r = await get_next_pending_phase(1, db_path, stub)
        assert r is not None and r["phase_number"] == 3
        async with get_connection(db_path) as db:
            await db.execute("UPDATE rounds SET phase3_done = 1 WHERE id = 1")
            await db.commit()

        # Step 6 — RSVP deadline (phase 7)
        r = await get_next_pending_phase(1, db_path, stub)
        assert r is not None and r["phase_number"] == 7
    finally:
        os.unlink(db_path)


async def test_earlier_misfired_round_beats_later_scheduler_job() -> None:
    """Earlier round with misfired phases must be advanced before a later
    round whose scheduler job is still pending."""
    earlier = (datetime.now(timezone.utc) - timedelta(days=10)).strftime(
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
        result = await get_next_pending_phase(1, db_path, stub)
        assert result is not None
        assert result["track_name"] == "Bahrain"   # earlier round wins
        assert result["phase_number"] == 1
    finally:
        os.unlink(db_path)
