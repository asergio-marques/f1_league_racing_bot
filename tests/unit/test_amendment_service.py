"""Unit tests for amendment_service (T034) — points-amendment workflow."""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations
from services.amendment_service import (
    AmendmentModifiedError,
    AmendmentNotActiveError,
    disable_amendment_mode,
    enable_amendment_mode,
    get_amendment_state,
    modify_session_points,
    revert_modification_store,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
async def db_path(tmp_path):
    path = str(tmp_path / "amend_test.db")
    await run_migrations(path)
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO server_configs "
            "(server_id, interaction_role_id, interaction_channel_id, log_channel_id) "
            "VALUES (1, 10, 20, 30)"
        )
        cursor = await db.execute(
            "INSERT INTO seasons (server_id, start_date, status, season_number) "
            "VALUES (1, '2026-01-01', 'ACTIVE', 1)"
        )
        season_id = cursor.lastrowid
        await db.commit()
    return path, season_id


async def _get_season_id(db_path: str) -> int:
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT id FROM seasons LIMIT 1")
        row = await cursor.fetchone()
    return row["id"]  # type: ignore[index]


async def _seed_season_points(db_path: str, season_id: int) -> None:
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO season_points_entries (season_id, config_name, session_type, position, points) "
            "VALUES (?, 'STD', 'FEATURE_RACE', 1, 25)",
            (season_id,),
        )
        await db.commit()


# ---------------------------------------------------------------------------
# disable_amendment_mode raises AmendmentModifiedError when modified_flag=1
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_disable_raises_when_modified_flag(db_path):
    path, season_id = db_path
    await _seed_season_points(path, season_id)
    await enable_amendment_mode(path, season_id)
    # Manually set modified_flag=1
    async with get_connection(path) as db:
        await db.execute(
            "UPDATE season_amendment_state SET modified_flag = 1 WHERE season_id = ?",
            (season_id,),
        )
        await db.commit()

    with pytest.raises(AmendmentModifiedError):
        await disable_amendment_mode(path, season_id)


@pytest.mark.asyncio
async def test_disable_succeeds_when_not_modified(db_path):
    path, season_id = db_path
    await _seed_season_points(path, season_id)
    await enable_amendment_mode(path, season_id)
    # modified_flag is 0 after enable — should succeed
    await disable_amendment_mode(path, season_id)
    state = await get_amendment_state(path, season_id)
    assert state is not None
    assert not state.amendment_active


# ---------------------------------------------------------------------------
# revert_modification_store — resets entries and clears modified_flag
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_revert_modification_store(db_path):
    path, season_id = db_path
    await _seed_season_points(path, season_id)
    await enable_amendment_mode(path, season_id)

    # Modify something in the modification store
    await modify_session_points(path, season_id, "STD", "FEATURE_RACE", 1, 30)

    # Confirm flag is set
    state = await get_amendment_state(path, season_id)
    assert state is not None and state.modified_flag

    # Revert
    await revert_modification_store(path, season_id)

    # Flag should be cleared
    state = await get_amendment_state(path, season_id)
    assert state is not None
    assert not state.modified_flag

    # Modification store should match the original season points (25 pts)
    async with get_connection(path) as db:
        cursor = await db.execute(
            "SELECT points FROM season_modification_entries WHERE season_id = ? AND position = 1",
            (season_id,),
        )
        row = await cursor.fetchone()
    assert row is not None
    assert row["points"] == 25


# ---------------------------------------------------------------------------
# modify_session_points raises AmendmentNotActiveError when not in amendment mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_modify_raises_when_not_active(db_path):
    path, season_id = db_path
    with pytest.raises(AmendmentNotActiveError):
        await modify_session_points(path, season_id, "STD", "FEATURE_RACE", 1, 30)


# ---------------------------------------------------------------------------
# approve_amendment — atomically overwrites season_points_entries
# (tested at DB level without bot dependency)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approve_amendment_overwrites_season_points(db_path):
    """Validate the transactional overwrite by inspecting DB state after manual simulate."""
    path, season_id = db_path
    await _seed_season_points(path, season_id)
    await enable_amendment_mode(path, season_id)

    # Modify P1 from 25 to 30
    await modify_session_points(path, season_id, "STD", "FEATURE_RACE", 1, 30)

    # Manually do the atomic overwrite (mirrors approve_amendment transaction)
    async with get_connection(path) as db:
        await db.execute(
            "DELETE FROM season_points_entries WHERE season_id = ?", (season_id,)
        )
        await db.execute(
            """
            INSERT INTO season_points_entries (season_id, config_name, session_type, position, points)
            SELECT season_id, config_name, session_type, position, points
            FROM season_modification_entries WHERE season_id = ?
            """,
            (season_id,),
        )
        await db.execute(
            "DELETE FROM season_modification_entries WHERE season_id = ?", (season_id,)
        )
        await db.execute(
            "UPDATE season_amendment_state SET amendment_active = 0, modified_flag = 0 WHERE season_id = ?",
            (season_id,),
        )
        await db.commit()

    # Verify season_points_entries now has 30 pts
    async with get_connection(path) as db:
        cursor = await db.execute(
            "SELECT points FROM season_points_entries WHERE season_id = ? AND position = 1",
            (season_id,),
        )
        row = await cursor.fetchone()
    assert row is not None
    assert row["points"] == 30

    # Verify amendment mode is off
    state = await get_amendment_state(path, season_id)
    assert state is not None
    assert not state.amendment_active
    assert not state.modified_flag


# ---------------------------------------------------------------------------
# amend_round actually amends — the query it opens with must name real columns
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_amend_round_changes_the_field(tmp_path):
    """`/round amend` was dead on arrival: its opening SELECT asked `divisions` for a
    `division_id` column, which that table has never had, so every amendment of an active
    season's round raised `no such column: d.division_id` and the league was told the
    amendment failed. Nothing else in the suite executed this path.
    """
    from datetime import datetime, timedelta, timezone
    from unittest.mock import AsyncMock, MagicMock

    from services.amendment_service import AmendmentService

    path = str(tmp_path / "amend_round.db")
    await run_migrations(path)
    scheduled_at = datetime.now(timezone.utc) + timedelta(days=30)
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO server_configs "
            "(server_id, interaction_role_id, interaction_channel_id, log_channel_id) "
            "VALUES (1, 10, 20, 30)"
        )
        await db.execute(
            "INSERT INTO seasons (id, server_id, start_date, status, season_number) "
            "VALUES (1, 1, '2026-01-01', 'ACTIVE', 1)"
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, forecast_channel_id, mention_role_id) "
            "VALUES (1, 1, 'Div A', 1, 999, 555)"
        )
        await db.execute(
            "INSERT INTO rounds (id, division_id, round_number, format, track_name, scheduled_at) "
            "VALUES (1, 1, 1, 'NORMAL', 'Bahrain International Circuit', ?)",
            (scheduled_at.isoformat(),),
        )
        await db.commit()

    actor = MagicMock()
    actor.id = 4242
    actor.display_name = "Race Control"

    bot = MagicMock()
    bot.db_path = path
    bot.module_service.is_weather_enabled = AsyncMock(return_value=False)
    bot.module_service.is_attendance_enabled = AsyncMock(return_value=False)
    bot.output_router.post_forecast = AsyncMock(return_value=None)
    bot.output_router.post_log = AsyncMock(return_value=None)
    bot.scheduler_service.cancel_round = MagicMock()
    bot.scheduler_service.schedule_round = MagicMock()

    await AmendmentService(path).amend_round(
        1, actor, [("track_name", "Silverstone Circuit")], bot
    )

    async with get_connection(path) as db:
        cursor = await db.execute("SELECT track_name FROM rounds WHERE id = 1")
        row = await cursor.fetchone()
        audit = await db.execute(
            "SELECT change_type, old_value, new_value FROM audit_entries WHERE server_id = 1"
        )
        entry = await audit.fetchone()

    assert row["track_name"] == "Silverstone Circuit"
    assert entry["change_type"] == "round.track_name"
    assert entry["old_value"] == "Bahrain International Circuit"
    assert entry["new_value"] == "Silverstone Circuit"


# ---------------------------------------------------------------------------
# A compound amendment is one amendment — issue #115
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_amending_two_fields_amends_once(tmp_path):
    """Amending a round's track and its date together is one amendment, not two.

    `/round amend` called the service once per field the manager gave, so changing both ran the
    whole amendment twice: the league was told twice that its forecasts had been thrown away,
    and the round was cancelled, re-armed and had its overdue phases re-run twice over. Two
    audit entries are right — one per field — and everything else happens exactly once.
    """
    from datetime import datetime, timedelta, timezone
    from unittest.mock import AsyncMock, MagicMock

    from services.amendment_service import AmendmentService

    path = str(tmp_path / "amend_two.db")
    await run_migrations(path)
    scheduled_at = datetime.now(timezone.utc) + timedelta(days=30)
    new_moment = scheduled_at + timedelta(days=7)
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO server_configs "
            "(server_id, interaction_role_id, interaction_channel_id, log_channel_id) "
            "VALUES (1, 10, 20, 30)"
        )
        await db.execute(
            "INSERT INTO seasons (id, server_id, start_date, status, season_number) "
            "VALUES (1, 1, '2026-01-01', 'ACTIVE', 1)"
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, forecast_channel_id, mention_role_id) "
            "VALUES (1, 1, 'Div A', 1, 999, 555)"
        )
        # phase1_done = 1 so the invalidation notice path is reached at all.
        await db.execute(
            "INSERT INTO rounds "
            "(id, division_id, round_number, format, track_name, scheduled_at, phase1_done) "
            "VALUES (1, 1, 1, 'NORMAL', 'Bahrain International Circuit', ?, 1)",
            (scheduled_at.isoformat(),),
        )
        await db.commit()

    actor = MagicMock()
    actor.id = 4242
    actor.display_name = "Race Control"

    bot = MagicMock()
    bot.db_path = path
    bot.module_service.is_weather_enabled = AsyncMock(return_value=True)
    bot.module_service.is_attendance_enabled = AsyncMock(return_value=False)
    bot.output_router.post_forecast = AsyncMock(return_value=None)
    bot.output_router.post_log = AsyncMock(return_value=None)
    bot.scheduler_service.cancel_round = MagicMock()
    bot.scheduler_service.schedule_round = MagicMock()

    # Both horizons stay in the future, so no overdue phase re-runs and the count below is
    # measuring the amendment itself rather than the catch-up.
    await AmendmentService(path).amend_round(
        1,
        actor,
        [("track_name", "Silverstone Circuit"), ("scheduled_at", new_moment)],
        bot,
        now=datetime.now(timezone.utc),
    )

    async with get_connection(path) as db:
        cursor = await db.execute("SELECT track_name, scheduled_at FROM rounds WHERE id = 1")
        rnd = await cursor.fetchone()
        cursor = await db.execute(
            "SELECT change_type FROM audit_entries WHERE server_id = 1 ORDER BY change_type"
        )
        audit = [r["change_type"] for r in await cursor.fetchall()]

    # Both fields landed, in one amendment.
    assert rnd["track_name"] == "Silverstone Circuit"
    assert rnd["scheduled_at"] == new_moment.isoformat()

    # One audit entry per field — the one thing that is right to do twice.
    assert audit == ["round.scheduled_at", "round.track_name"]

    # Everything else exactly once.
    assert bot.output_router.post_forecast.await_count == 1
    assert bot.output_router.post_log.await_count == 1
    assert bot.scheduler_service.cancel_round.call_count == 1
    assert bot.scheduler_service.schedule_round.call_count == 1


# ---------------------------------------------------------------------------
# A forecast that would still have run is kept
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_phase_that_would_still_have_run_is_kept(tmp_path):
    """Each phase is judged on its own, by whether it would have run under the new moment.

    Every amendment used to invalidate all three phases, clear every slot and wipe all three
    done flags, whatever had actually been drawn. So a round nudged an hour threw away a
    forecast that was still perfectly good, and — because the flags went with it — the next
    amendment had no record that any forecast had ever been posted, which is what the track and
    format rules read.

    Round one day out, delayed to four. Phase 1 falls five days before it and is behind us
    either way, so it stands. Phases 2 and 3 have not come round under the new moment, so they
    are withdrawn and armed again.
    """
    from datetime import datetime, timedelta, timezone
    from unittest.mock import AsyncMock, MagicMock

    from services.amendment_service import AmendmentService

    path = str(tmp_path / "amend_phases.db")
    await run_migrations(path)
    now = datetime.now(timezone.utc)
    scheduled_at = now + timedelta(days=1)
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO server_configs "
            "(server_id, interaction_role_id, interaction_channel_id, log_channel_id) "
            "VALUES (1, 10, 20, 30)"
        )
        await db.execute(
            "INSERT INTO seasons (id, server_id, start_date, status, season_number) "
            "VALUES (1, 1, '2026-01-01', 'ACTIVE', 1)"
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, forecast_channel_id, mention_role_id) "
            "VALUES (1, 1, 'Div A', 1, 999, 555)"
        )
        # Phases 1 and 2 have run (their horizons, 5 and 2 days out, are behind us); 3 has not.
        await db.execute(
            "INSERT INTO rounds "
            "(id, division_id, round_number, format, track_name, scheduled_at, "
            " phase1_done, phase2_done, phase3_done) "
            "VALUES (1, 1, 1, 'NORMAL', 'Bahrain International Circuit', ?, 1, 1, 0)",
            (scheduled_at.isoformat(),),
        )
        for phase_number in (1, 2):
            await db.execute(
                "INSERT INTO phase_results (round_id, phase_number, payload, status, created_at) "
                "VALUES (1, ?, '{}', 'ACTIVE', ?)",
                (phase_number, now.isoformat()),
            )
        await db.commit()

    actor = MagicMock()
    actor.id = 4242
    actor.display_name = "Race Control"

    bot = MagicMock()
    bot.db_path = path
    bot.module_service.is_weather_enabled = AsyncMock(return_value=False)
    bot.module_service.is_attendance_enabled = AsyncMock(return_value=False)
    bot.output_router.post_forecast = AsyncMock(return_value=None)
    bot.output_router.post_log = AsyncMock(return_value=None)
    bot.scheduler_service.cancel_round = MagicMock()
    bot.scheduler_service.schedule_round = MagicMock()

    await AmendmentService(path).amend_round(
        1, actor, [("scheduled_at", now + timedelta(days=4))], bot, now=now
    )

    async with get_connection(path) as db:
        cursor = await db.execute(
            "SELECT phase1_done, phase2_done, phase3_done FROM rounds WHERE id = 1"
        )
        flags = await cursor.fetchone()
        cursor = await db.execute(
            "SELECT phase_number, status FROM phase_results WHERE round_id = 1 "
            "ORDER BY phase_number"
        )
        results = {r["phase_number"]: r["status"] for r in await cursor.fetchall()}

    # Phase 1 would still have run, so its forecast and its flag both stand.
    assert flags["phase1_done"] == 1
    assert results[1] == "ACTIVE"

    # Phase 2 would not, so it is withdrawn and will be drawn again.
    assert flags["phase2_done"] == 0
    assert results[2] == "INVALIDATED"

    # Phase 3 never ran and is still ahead either way.
    assert flags["phase3_done"] == 0


# ---------------------------------------------------------------------------
# An amended round is re-armed at the league's own horizons — issue #110
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_amending_a_round_rearms_it_at_the_configured_horizons(tmp_path):
    """A league that configured its own forecast horizons keeps them across an amendment.

    `schedule_round` falls back to the packaged 5 / 2 / 2 when it is not told otherwise, and the
    amendment never told it — so a league running 7 / 3 / 4 had the defaults quietly restored
    every time it moved a round, and the phases fired at moments nobody had chosen. It also put
    the amendment at odds with itself once the rules began reading the configured horizons: a
    phase would be judged at seven days and armed at five.
    """
    from datetime import datetime, timedelta, timezone
    from unittest.mock import AsyncMock, MagicMock

    from services.amendment_service import AmendmentService

    path = str(tmp_path / "amend_horizons.db")
    await run_migrations(path)
    now = datetime.now(timezone.utc)
    scheduled_at = now + timedelta(days=30)
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO server_configs "
            "(server_id, interaction_role_id, interaction_channel_id, log_channel_id) "
            "VALUES (1, 10, 20, 30)"
        )
        await db.execute(
            "INSERT INTO seasons (id, server_id, start_date, status, season_number) "
            "VALUES (1, 1, '2026-01-01', 'ACTIVE', 1)"
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, forecast_channel_id, mention_role_id) "
            "VALUES (1, 1, 'Div A', 1, 999, 555)"
        )
        await db.execute(
            "INSERT INTO rounds "
            "(id, division_id, round_number, format, track_name, scheduled_at) "
            "VALUES (1, 1, 1, 'NORMAL', 'Bahrain International Circuit', ?)",
            (scheduled_at.isoformat(),),
        )
        # Anything but the packaged 5 / 2 / 2, so a fallback cannot pass by coincidence.
        await db.execute(
            "INSERT INTO weather_pipeline_config (server_id, phase_1_days, phase_2_days, phase_3_hours) "
            "VALUES (1, 7, 3, 4)"
        )
        await db.commit()

    actor = MagicMock()
    actor.id = 4242
    actor.display_name = "Race Control"

    bot = MagicMock()
    bot.db_path = path
    bot.module_service.is_weather_enabled = AsyncMock(return_value=True)
    bot.module_service.is_attendance_enabled = AsyncMock(return_value=False)
    bot.output_router.post_forecast = AsyncMock(return_value=None)
    bot.output_router.post_log = AsyncMock(return_value=None)
    bot.scheduler_service.cancel_round = MagicMock()
    bot.scheduler_service.schedule_round = MagicMock()

    await AmendmentService(path).amend_round(
        1, actor, [("track_name", "Silverstone Circuit")], bot, now=now
    )

    bot.scheduler_service.schedule_round.assert_called_once()
    kwargs = bot.scheduler_service.schedule_round.call_args.kwargs
    assert kwargs["phase_1_days"] == 7
    assert kwargs["phase_2_days"] == 3
    assert kwargs["phase_3_hours"] == 4


# ---------------------------------------------------------------------------
# An amended round keeps its check-in — issue #120
# ---------------------------------------------------------------------------


def _amend_bot_with_attendance(path, *, attendance: bool, weather: bool = False):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    bot = MagicMock()
    bot.db_path = path
    bot.module_service.is_weather_enabled = AsyncMock(return_value=weather)
    bot.module_service.is_attendance_enabled = AsyncMock(return_value=attendance)
    bot.attendance_service.get_or_create_config = AsyncMock(
        return_value=SimpleNamespace(
            rsvp_notice_days=5, rsvp_last_notice_hours=24, rsvp_deadline_hours=2
        )
    )
    bot.output_router.post_forecast = AsyncMock(return_value=None)
    bot.output_router.post_log = AsyncMock(return_value=None)
    bot.scheduler_service.cancel_round = MagicMock()
    bot.scheduler_service.schedule_round = MagicMock()
    bot.scheduler_service.schedule_attendance_round = MagicMock()
    return bot


async def _seed_one_round(path, scheduled_at):
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO server_configs "
            "(server_id, interaction_role_id, interaction_channel_id, log_channel_id) "
            "VALUES (1, 10, 20, 30)"
        )
        await db.execute(
            "INSERT INTO seasons (id, server_id, start_date, status, season_number) "
            "VALUES (1, 1, '2026-01-01', 'ACTIVE', 3)"
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, forecast_channel_id, mention_role_id) "
            "VALUES (1, 1, 'Div A', 2, 999, 555)"
        )
        await db.execute(
            "INSERT INTO rounds "
            "(id, division_id, round_number, format, track_name, scheduled_at) "
            "VALUES (1, 1, 1, 'NORMAL', 'Bahrain International Circuit', ?)",
            (scheduled_at.isoformat(),),
        )
        await db.commit()


@pytest.mark.asyncio
async def test_amending_a_round_rearms_its_check_in(tmp_path):
    """The reported defect: amending a round destroyed its check-in for good.

    Cancelling the round takes all eight of its jobs, the check-in call, its reminder and its
    deadline included, and only the weather ones were ever put back. Nothing else arms them —
    `schedule_attendance_round` is called from `/season approve` and nowhere else, and that
    cannot be run again on an active season. So the round asked nobody whether they were racing,
    opened no attendance records, charged nobody, and read afterwards as perfect attendance for
    the entire division, with nothing anywhere reporting it.
    """
    from datetime import datetime, timedelta, timezone
    from unittest.mock import MagicMock

    from services.amendment_service import AmendmentService

    path = str(tmp_path / "amend_checkin.db")
    await run_migrations(path)
    now = datetime.now(timezone.utc)
    await _seed_one_round(path, now + timedelta(days=30))

    actor = MagicMock()
    actor.id = 4242
    actor.display_name = "Race Control"
    bot = _amend_bot_with_attendance(path, attendance=True)

    await AmendmentService(path).amend_round(
        1, actor, [("scheduled_at", now + timedelta(days=40))], bot, now=now
    )

    bot.scheduler_service.schedule_attendance_round.assert_called_once()
    kwargs = bot.scheduler_service.schedule_attendance_round.call_args.kwargs
    # Armed against the league's own timings, and named for the right season and tier.
    assert kwargs["notice_days"] == 5
    assert kwargs["last_notice_hours"] == 24
    assert kwargs["deadline_hours"] == 2
    assert kwargs["season_number"] == 3
    assert kwargs["division_tier"] == 2
    # And against the round as amended, not as it stood.
    armed_round = bot.scheduler_service.schedule_attendance_round.call_args.args[0]
    assert armed_round.scheduled_at.replace(tzinfo=timezone.utc) == now + timedelta(days=40)


@pytest.mark.asyncio
async def test_amending_a_round_arms_no_check_in_while_attendance_is_disabled(tmp_path):
    """A disabled module produces nothing, a scheduled job included."""
    from datetime import datetime, timedelta, timezone
    from unittest.mock import MagicMock

    from services.amendment_service import AmendmentService

    path = str(tmp_path / "amend_no_checkin.db")
    await run_migrations(path)
    now = datetime.now(timezone.utc)
    await _seed_one_round(path, now + timedelta(days=30))

    actor = MagicMock()
    actor.id = 4242
    actor.display_name = "Race Control"
    bot = _amend_bot_with_attendance(path, attendance=False)

    await AmendmentService(path).amend_round(
        1, actor, [("scheduled_at", now + timedelta(days=40))], bot, now=now
    )

    bot.scheduler_service.schedule_attendance_round.assert_not_called()


# ---------------------------------------------------------------------------
# An amended round keeps its result submission — issue #133
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_amending_a_round_rearms_its_results_job_with_weather_off(tmp_path):
    """`schedule_round` builds the weather jobs and the results job together.

    So with weather switched off it was never called, and the amendment's cancel took the
    round's results job with nothing putting it back. That job is the round's one clock-driven
    status transition — without it the round never leaves NOT_RUN, its division never finishes,
    and its season can never be completed.
    """
    from datetime import datetime, timedelta, timezone
    from unittest.mock import MagicMock

    from services.amendment_service import AmendmentService

    path = str(tmp_path / "amend_results.db")
    await run_migrations(path)
    now = datetime.now(timezone.utc)
    await _seed_one_round(path, now + timedelta(days=30))

    actor = MagicMock()
    actor.id = 4242
    actor.display_name = "Race Control"
    bot = _amend_bot_with_attendance(path, attendance=False, weather=False)
    bot.scheduler_service.schedule_result_submission_jobs = MagicMock()

    await AmendmentService(path).amend_round(
        1, actor, [("scheduled_at", now + timedelta(days=40))], bot, now=now
    )

    bot.scheduler_service.schedule_result_submission_jobs.assert_called_once()
    rounds = bot.scheduler_service.schedule_result_submission_jobs.call_args.args[0]
    assert [r.id for r in rounds] == [1]
    meta = bot.scheduler_service.schedule_result_submission_jobs.call_args.kwargs["division_meta"]
    assert meta == {1: (3, 2)}


@pytest.mark.asyncio
async def test_the_results_job_is_not_armed_twice_when_weather_is_on(tmp_path):
    """With weather on, `schedule_round` has already created it — arming again would duplicate."""
    from datetime import datetime, timedelta, timezone
    from unittest.mock import MagicMock

    from services.amendment_service import AmendmentService

    path = str(tmp_path / "amend_results_weather.db")
    await run_migrations(path)
    now = datetime.now(timezone.utc)
    await _seed_one_round(path, now + timedelta(days=30))

    actor = MagicMock()
    actor.id = 4242
    actor.display_name = "Race Control"
    bot = _amend_bot_with_attendance(path, attendance=False, weather=True)
    bot.scheduler_service.schedule_result_submission_jobs = MagicMock()

    await AmendmentService(path).amend_round(
        1, actor, [("scheduled_at", now + timedelta(days=40))], bot, now=now
    )

    bot.scheduler_service.schedule_round.assert_called_once()
    bot.scheduler_service.schedule_result_submission_jobs.assert_not_called()
