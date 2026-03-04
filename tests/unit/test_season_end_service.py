"""Unit tests for season_end_service and the new SeasonService helper methods."""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations
from services.season_service import SeasonService
from services.season_end_service import check_and_schedule_season_end, execute_season_end


# ---------------------------------------------------------------------------
# Stub helpers
# ---------------------------------------------------------------------------

class _FakeScheduler:
    def __init__(self) -> None:
        self.season_end_scheduled: list[tuple] = []
        self.season_end_cancelled: list[int] = []
        self.cancelled_rounds: list[int] = []

    def schedule_season_end(self, server_id, fire_at, season_id) -> None:
        self.season_end_scheduled.append((server_id, fire_at, season_id))

    def cancel_season_end(self, server_id: int) -> None:
        self.season_end_cancelled.append(server_id)

    def cancel_round(self, round_id: int) -> None:
        self.cancelled_rounds.append(round_id)


class _FakeRouter:
    def __init__(self) -> None:
        self.log_messages: list[tuple[int, str]] = []

    async def post_log(self, server_id: int, content: str) -> None:
        self.log_messages.append((server_id, content))


class _FakeBot:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self.season_service = SeasonService(db_path)
        self.scheduler_service = _FakeScheduler()
        self.output_router = _FakeRouter()


async def _seed_server(db_path: str, server_id: int = 1) -> tuple[int, list[int]]:
    """Seed a fully active season with two rounds. Returns (season_id, round_ids)."""
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs "
            "(server_id, interaction_role_id, interaction_channel_id, log_channel_id) "
            "VALUES (?, 100, 200, 300)",
            (server_id,),
        )
        await db.execute(
            "INSERT INTO seasons (server_id, start_date, status) "
            "VALUES (?, '2026-01-01', 'ACTIVE')",
            (server_id,),
        )
        cur = await db.execute("SELECT last_insert_rowid()")
        (season_id,) = await cur.fetchone()

        await db.execute(
            "INSERT INTO divisions (season_id, name, mention_role_id, forecast_channel_id) "
            "VALUES (?, 'Div A', 11, 21)",
            (season_id,),
        )
        cur = await db.execute("SELECT last_insert_rowid()")
        (div_id,) = await cur.fetchone()

        round_ids = []
        for i, scheduled_at in enumerate(
            ["2026-04-01T12:00:00", "2026-05-01T12:00:00"], start=1
        ):
            await db.execute(
                "INSERT INTO rounds "
                "(division_id, round_number, track_name, scheduled_at, format, "
                " phase1_done, phase2_done, phase3_done) "
                "VALUES (?, ?, 'Bahrain', ?, 'NORMAL', 0, 0, 0)",
                (div_id, i, scheduled_at),
            )
            cur = await db.execute("SELECT last_insert_rowid()")
            (rid,) = await cur.fetchone()
            round_ids.append(rid)

        await db.commit()

    return season_id, round_ids


async def _mark_all_phases_done(db_path: str, round_ids: list[int]) -> None:
    async with get_connection(db_path) as db:
        for rid in round_ids:
            await db.execute(
                "UPDATE rounds SET phase1_done = 1, phase2_done = 1, phase3_done = 1 WHERE id = ?",
                (rid,),
            )
        await db.commit()


# ---------------------------------------------------------------------------
# SeasonService helper tests
# ---------------------------------------------------------------------------

async def test_has_existing_season_true() -> None:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name
    try:
        await run_migrations(db_path)
        await _seed_server(db_path, server_id=1)
        svc = SeasonService(db_path)
        assert await svc.has_existing_season(1) is True
    finally:
        os.unlink(db_path)


async def test_has_existing_season_false() -> None:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name
    try:
        await run_migrations(db_path)
        svc = SeasonService(db_path)
        assert await svc.has_existing_season(999) is False
    finally:
        os.unlink(db_path)


async def test_all_phases_complete_false_when_pending() -> None:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name
    try:
        await run_migrations(db_path)
        await _seed_server(db_path, server_id=1)
        svc = SeasonService(db_path)
        assert await svc.all_phases_complete(1) is False
    finally:
        os.unlink(db_path)


async def test_all_phases_complete_true_when_done() -> None:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name
    try:
        await run_migrations(db_path)
        _, round_ids = await _seed_server(db_path, server_id=1)
        await _mark_all_phases_done(db_path, round_ids)
        svc = SeasonService(db_path)
        assert await svc.all_phases_complete(1) is True
    finally:
        os.unlink(db_path)


async def test_get_last_scheduled_at_returns_latest() -> None:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name
    try:
        await run_migrations(db_path)
        await _seed_server(db_path, server_id=1)
        svc = SeasonService(db_path)
        last_at = await svc.get_last_scheduled_at(1)
        assert last_at is not None
        # The seeded rounds have scheduled_at '2026-04-01' and '2026-05-01'
        assert last_at.year == 2026
        assert last_at.month == 5
    finally:
        os.unlink(db_path)


async def test_get_last_scheduled_at_returns_none_for_unknown_server() -> None:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name
    try:
        await run_migrations(db_path)
        svc = SeasonService(db_path)
        assert await svc.get_last_scheduled_at(999) is None
    finally:
        os.unlink(db_path)


# ---------------------------------------------------------------------------
# check_and_schedule_season_end tests
# ---------------------------------------------------------------------------

async def test_check_does_not_schedule_when_phases_incomplete() -> None:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name
    try:
        await run_migrations(db_path)
        await _seed_server(db_path, server_id=1)  # phases NOT done
        bot = _FakeBot(db_path)
        await check_and_schedule_season_end(1, bot)
        assert bot.scheduler_service.season_end_scheduled == []
    finally:
        os.unlink(db_path)


async def test_check_schedules_when_all_phases_complete() -> None:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name
    try:
        await run_migrations(db_path)
        _, round_ids = await _seed_server(db_path, server_id=1)
        await _mark_all_phases_done(db_path, round_ids)
        bot = _FakeBot(db_path)
        await check_and_schedule_season_end(1, bot)
        assert len(bot.scheduler_service.season_end_scheduled) == 1
        server_id, fire_at, season_id = bot.scheduler_service.season_end_scheduled[0]
        assert server_id == 1
        # fire_at should be last round scheduled_at (2026-05-01) + 7 days
        assert fire_at.year == 2026
        assert fire_at.month == 5
        assert fire_at.day == 8
    finally:
        os.unlink(db_path)


async def test_check_is_noop_when_no_active_season() -> None:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name
    try:
        await run_migrations(db_path)
        bot = _FakeBot(db_path)
        await check_and_schedule_season_end(999, bot)
        assert bot.scheduler_service.season_end_scheduled == []
    finally:
        os.unlink(db_path)


# ---------------------------------------------------------------------------
# execute_season_end tests
# ---------------------------------------------------------------------------

async def test_execute_season_end_deletes_season() -> None:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name
    try:
        await run_migrations(db_path)
        season_id, _ = await _seed_server(db_path, server_id=1)
        bot = _FakeBot(db_path)
        await execute_season_end(1, season_id, bot)
        # Season row must now be gone
        async with get_connection(db_path) as db:
            cur = await db.execute("SELECT COUNT(*) FROM seasons WHERE server_id = 1")
            (n,) = await cur.fetchone()
        assert n == 0
    finally:
        os.unlink(db_path)


async def test_execute_season_end_posts_log_message() -> None:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name
    try:
        await run_migrations(db_path)
        season_id, _ = await _seed_server(db_path, server_id=1)
        bot = _FakeBot(db_path)
        await execute_season_end(1, season_id, bot)
        assert len(bot.output_router.log_messages) == 1
        server_id, msg = bot.output_router.log_messages[0]
        assert server_id == 1
        assert "Season Complete" in msg or "season" in msg.lower()
    finally:
        os.unlink(db_path)


async def test_execute_season_end_is_idempotent() -> None:
    """Calling execute_season_end twice must not raise and must only post one log."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name
    try:
        await run_migrations(db_path)
        season_id, _ = await _seed_server(db_path, server_id=1)
        bot = _FakeBot(db_path)
        await execute_season_end(1, season_id, bot)
        await execute_season_end(1, season_id, bot)  # second call: no-op
        assert len(bot.output_router.log_messages) == 1  # only posted once
    finally:
        os.unlink(db_path)


async def test_execute_season_end_preserves_server_config() -> None:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name
    try:
        await run_migrations(db_path)
        season_id, _ = await _seed_server(db_path, server_id=1)
        bot = _FakeBot(db_path)
        await execute_season_end(1, season_id, bot)
        async with get_connection(db_path) as db:
            cur = await db.execute(
                "SELECT COUNT(*) FROM server_configs WHERE server_id = 1"
            )
            (n,) = await cur.fetchone()
        assert n == 1
    finally:
        os.unlink(db_path)


async def test_execute_season_end_cancels_season_end_job() -> None:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name
    try:
        await run_migrations(db_path)
        season_id, _ = await _seed_server(db_path, server_id=1)
        bot = _FakeBot(db_path)
        await execute_season_end(1, season_id, bot)
        assert 1 in bot.scheduler_service.season_end_cancelled
    finally:
        os.unlink(db_path)


# ---------------------------------------------------------------------------
# Startup recovery tests (FR-025)
# ---------------------------------------------------------------------------

async def test_startup_recovery_schedules_future_job() -> None:
    """When all phases are complete and fire_at is in the future, job is scheduled."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name
    try:
        await run_migrations(db_path)
        _, round_ids = await _seed_server(db_path, server_id=1)
        await _mark_all_phases_done(db_path, round_ids)
        bot = _FakeBot(db_path)
        # now = day before fire_at (2026-05-08), so job should be scheduled
        now = datetime(2026, 5, 7, 12, 0, 0, tzinfo=timezone.utc)
        await check_and_schedule_season_end(1, bot, now=now)
        assert len(bot.scheduler_service.season_end_scheduled) == 1
        _, fire_at, _ = bot.scheduler_service.season_end_scheduled[0]
        # fire_at = last_round.scheduled_at (2026-05-01) + 7 days = 2026-05-08
        assert fire_at.year == 2026 and fire_at.month == 5 and fire_at.day == 8
    finally:
        os.unlink(db_path)


async def test_startup_recovery_fires_immediately_when_past() -> None:
    """When fire_at is already in the past, execute_season_end runs directly."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name
    try:
        await run_migrations(db_path)
        _, round_ids = await _seed_server(db_path, server_id=1)
        await _mark_all_phases_done(db_path, round_ids)
        bot = _FakeBot(db_path)
        # now = day after fire_at (2026-05-08), so season end fires immediately
        now = datetime(2026, 5, 9, 12, 0, 0, tzinfo=timezone.utc)
        await check_and_schedule_season_end(1, bot, now=now)
        # No job scheduled — executed directly instead
        assert len(bot.scheduler_service.season_end_scheduled) == 0
        # Season data deleted immediately
        svc = SeasonService(db_path)
        assert await svc.get_active_season(1) is None
    finally:
        os.unlink(db_path)


async def test_startup_recovery_noop_when_phases_incomplete() -> None:
    """When not all phases are done, no job is scheduled and no deletion occurs."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name
    try:
        await run_migrations(db_path)
        _, _round_ids = await _seed_server(db_path, server_id=1)
        # Deliberately do NOT mark phases done
        bot = _FakeBot(db_path)
        now = datetime(2026, 5, 9, 12, 0, 0, tzinfo=timezone.utc)
        await check_and_schedule_season_end(1, bot, now=now)
        assert len(bot.scheduler_service.season_end_scheduled) == 0
        svc = SeasonService(db_path)
        assert await svc.get_active_season(1) is not None
    finally:
        os.unlink(db_path)


async def test_get_all_server_ids_with_active_season() -> None:
    """Returns only server_ids with ACTIVE seasons; excludes servers with none."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name
    try:
        await run_migrations(db_path)
        # Seed two servers
        await _seed_server(db_path, server_id=1)
        await _seed_server(db_path, server_id=2)
        svc = SeasonService(db_path)
        ids = await svc.get_all_server_ids_with_active_season()
        assert set(ids) == {1, 2}
        # A server with no season row is not included
        ids_no_99 = [i for i in ids if i != 99]
        assert 99 not in ids_no_99
    finally:
        os.unlink(db_path)

