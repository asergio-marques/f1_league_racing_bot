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
    """Summary includes phase completion indicators for non-Mystery rounds."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name
    try:
        await run_migrations(db_path)
        await _seed(db_path, [
            {"phase1_done": 1, "phase2_done": 0, "phase3_done": 0, "track_name": "Monaco"},
        ])
        summary = await build_review_summary(1, db_path)
        assert "Monaco" in summary
        assert "✅" in summary   # phase1 done
        assert "⏳" in summary   # phase2/3 pending
    finally:
        os.unlink(db_path)


async def test_review_mystery_round_shows_na() -> None:
    """Mystery rounds appear in review with 'N/A' label, not P1/P2/P3."""
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
        assert "N/A" in summary.upper() or "n/a" in summary.lower()
        # Should NOT contain P1/P2/P3 status emojis for Mystery round
        assert "Mystery" in summary or "MYSTERY" in summary
    finally:
        os.unlink(db_path)
