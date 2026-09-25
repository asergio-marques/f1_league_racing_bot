"""Unit tests for amendment_service (T034) — points-amendment workflow."""
from __future__ import annotations

import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.services.amendment_service import (
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
            "INSERT INTO seasons (start_date, status, season_number) "
            "VALUES ('2026-01-01', 'ACTIVE', 1)"
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
    from unittest.mock import AsyncMock, MagicMock, patch

    from leaguebot.core.services.amendment_service import AmendmentService

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
            "INSERT INTO seasons (id, start_date, status, season_number) "
            "VALUES (1, '2026-01-01', 'ACTIVE', 1)"
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
    bot.config_service.get_league_server_id = AsyncMock(return_value=1)
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
            "SELECT change_type, old_value, new_value FROM audit_entries"
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
    from unittest.mock import AsyncMock, MagicMock, patch

    from leaguebot.core.services.amendment_service import AmendmentService

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
            "INSERT INTO seasons (id, start_date, status, season_number) "
            "VALUES (1, '2026-01-01', 'ACTIVE', 1)"
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
    bot.config_service.get_league_server_id = AsyncMock(return_value=1)
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
            "SELECT change_type FROM audit_entries ORDER BY change_type"
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
    from unittest.mock import AsyncMock, MagicMock, patch

    from leaguebot.core.services.amendment_service import AmendmentService

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
            "INSERT INTO seasons (id, start_date, status, season_number) "
            "VALUES (1, '2026-01-01', 'ACTIVE', 1)"
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
    bot.config_service.get_league_server_id = AsyncMock(return_value=1)
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
    from unittest.mock import AsyncMock, MagicMock, patch

    from leaguebot.core.services.amendment_service import AmendmentService

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
            "INSERT INTO seasons (id, start_date, status, season_number) "
            "VALUES (1, '2026-01-01', 'ACTIVE', 1)"
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
            "INSERT INTO weather_pipeline_config (id, phase_1_days, phase_2_days, phase_3_hours) "
            "VALUES (1, 7, 3, 4)"
        )
        await db.commit()

    actor = MagicMock()
    actor.id = 4242
    actor.display_name = "Race Control"

    bot = MagicMock()
    bot.config_service.get_league_server_id = AsyncMock(return_value=1)
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
    from unittest.mock import AsyncMock, MagicMock, patch

    bot = MagicMock()
    bot.config_service.get_league_server_id = AsyncMock(return_value=1)
    bot.db_path = path
    bot.module_service.is_weather_enabled = AsyncMock(return_value=weather)
    bot.module_service.is_attendance_enabled = AsyncMock(return_value=attendance)
    bot.attendance_service.get_or_create_config = AsyncMock(
        return_value=SimpleNamespace(
            rsvp_notice_days=5, rsvp_last_notice_hours=24, rsvp_deadline_hours=2
        )
    )
    # No call has gone out for these rounds, so there is nothing to take down or repost.
    bot.attendance_service.get_embed_message = AsyncMock(return_value=None)
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
            "INSERT INTO seasons (id, start_date, status, season_number) "
            "VALUES (1, '2026-01-01', 'ACTIVE', 3)"
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
    `schedule_attendance_round` is called from the confirmation of placements and nowhere else, and that
    cannot be run again on an active season. So the round asked nobody whether they were racing,
    opened no attendance records, charged nobody, and read afterwards as perfect attendance for
    the entire division, with nothing anywhere reporting it.
    """
    from datetime import datetime, timedelta, timezone
    from unittest.mock import MagicMock

    from leaguebot.core.services.amendment_service import AmendmentService

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

    from leaguebot.core.services.amendment_service import AmendmentService

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

    from leaguebot.core.services.amendment_service import AmendmentService

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

    from leaguebot.core.services.amendment_service import AmendmentService

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


# ---------------------------------------------------------------------------
# What becomes of a check-in call that has already gone out
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_standing_call_is_taken_down_when_the_round_moves_out_of_its_window(tmp_path):
    """Moved far enough that the call would not have gone out, so the one standing is withdrawn.

    The armed job posts a fresh one at the new time. Leaving the old one up would have the
    division reading a call for a circuit, a date or a set of sessions the round no longer has.
    """
    from datetime import datetime, timedelta, timezone
    from unittest.mock import AsyncMock, MagicMock, patch

    from leaguebot.core.services.amendment_service import AmendmentService

    path = str(tmp_path / "amend_withdraw.db")
    await run_migrations(path)
    now = datetime.now(timezone.utc)
    await _seed_one_round(path, now + timedelta(days=2))

    actor = MagicMock()
    actor.id = 4242
    actor.display_name = "Race Control"
    bot = _amend_bot_with_attendance(path, attendance=True)
    _withdrawn = AsyncMock(return_value=True)
    _reposted = AsyncMock(return_value=None)

    with patch("leaguebot.attendance.services.rsvp_service.withdraw_rsvp_call", _withdrawn), patch(
        "leaguebot.attendance.services.rsvp_service.repost_rsvp_call", _reposted
    ):
        await AmendmentService(path).amend_round(
            1, actor, [("scheduled_at", now + timedelta(days=40))], bot, now=now
        )

    _withdrawn.assert_awaited_once()
    _reposted.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_standing_call_is_posted_again_when_its_window_has_passed(tmp_path):
    """The call was due and is still open, so it goes out again carrying what changed."""
    from datetime import datetime, timedelta, timezone
    from unittest.mock import AsyncMock, MagicMock, patch

    from leaguebot.core.services.amendment_service import AmendmentService

    path = str(tmp_path / "amend_repost.db")
    await run_migrations(path)
    now = datetime.now(timezone.utc)
    # Two days out: the call (5 days before) is behind us, the deadline (2 hours) is not.
    await _seed_one_round(path, now + timedelta(days=2))

    actor = MagicMock()
    actor.id = 4242
    actor.display_name = "Race Control"
    bot = _amend_bot_with_attendance(path, attendance=True)
    _withdrawn = AsyncMock(return_value=True)
    _reposted = AsyncMock(return_value=None)

    with patch("leaguebot.attendance.services.rsvp_service.withdraw_rsvp_call", _withdrawn), patch(
        "leaguebot.attendance.services.rsvp_service.repost_rsvp_call", _reposted
    ):
        await AmendmentService(path).amend_round(
            1, actor, [("track_name", "Silverstone Circuit")], bot, now=now
        )

    _reposted.assert_awaited_once()
    _withdrawn.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_closed_check_in_is_left_alone(tmp_path):
    """Past its deadline the check-in is settled and the reserves are distributed against it.

    Reopening it would unsettle a grid already told who is racing, so nothing is posted and
    nothing is taken down — the amendment touches the forecasts and the schedule alone.
    """
    from datetime import datetime, timedelta, timezone
    from unittest.mock import AsyncMock, MagicMock, patch

    from leaguebot.core.services.amendment_service import AmendmentService

    path = str(tmp_path / "amend_closed.db")
    await run_migrations(path)
    now = datetime.now(timezone.utc)
    # One hour out, so the deadline two hours before it has gone by.
    await _seed_one_round(path, now + timedelta(hours=1))

    actor = MagicMock()
    actor.id = 4242
    actor.display_name = "Race Control"
    bot = _amend_bot_with_attendance(path, attendance=True)
    _withdrawn = AsyncMock(return_value=True)
    _reposted = AsyncMock(return_value=None)

    with patch("leaguebot.attendance.services.rsvp_service.withdraw_rsvp_call", _withdrawn), patch(
        "leaguebot.attendance.services.rsvp_service.repost_rsvp_call", _reposted
    ):
        await AmendmentService(path).amend_round(
            1, actor, [("track_name", "Silverstone Circuit")], bot, now=now
        )

    _reposted.assert_not_awaited()
    _withdrawn.assert_not_awaited()


async def _cleared(path: str) -> bool:
    async with get_connection(path) as db:
        cursor = await db.execute("SELECT checkin_cleared FROM rounds WHERE id = 1")
        return bool((await cursor.fetchone())["checkin_cleared"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "days_out,changes,cleared",
    [
        (2, "moved-out", False),
        (2, "track", False),
        (1 / 24, "track", True),
    ],
    ids=["call-withdrawn", "call-reposted", "check-in-closed"],
)
async def test_reopening_a_check_in_clears_the_mark_of_one_taken_down(
    tmp_path, days_out, changes, cleared
):
    """A round's check-in marked taken down, then reopened by an amendment, is no longer taken
    down (#425): test mode reads the mark, and would otherwise never offer the new call. A
    check-in the amendment leaves closed keeps it."""
    from datetime import datetime, timedelta, timezone
    from unittest.mock import AsyncMock, MagicMock, patch

    from leaguebot.core.services.amendment_service import AmendmentService

    path = str(tmp_path / "amend_cleared.db")
    await run_migrations(path)
    now = datetime.now(timezone.utc)
    await _seed_one_round(path, now + timedelta(days=days_out))
    async with get_connection(path) as db:
        await db.execute("UPDATE rounds SET checkin_cleared = 1 WHERE id = 1")
        await db.commit()

    actor = MagicMock()
    actor.id = 4242
    actor.display_name = "Race Control"
    bot = _amend_bot_with_attendance(path, attendance=True)
    change = (
        ("scheduled_at", now + timedelta(days=40))
        if changes == "moved-out"
        else ("track_name", "Silverstone Circuit")
    )

    with patch("leaguebot.attendance.services.rsvp_service.withdraw_rsvp_call", AsyncMock(return_value=True)), patch(
        "leaguebot.attendance.services.rsvp_service.repost_rsvp_call", AsyncMock(return_value=None)
    ):
        await AmendmentService(path).amend_round(1, actor, [change], bot, now=now)

    assert await _cleared(path) is cleared


async def _placement(path: str) -> tuple[int | None, int]:
    async with get_connection(path) as db:
        cursor = await db.execute(
            "SELECT assigned_team_id, is_standby FROM driver_round_attendance WHERE round_id = 1"
        )
        row = await cursor.fetchone()
    return row["assigned_team_id"], row["is_standby"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "days_out,changes,forgotten",
    [
        (2, "moved-out", True),
        (2, "track", True),
        (1 / 24, "track", False),
    ],
    ids=["call-withdrawn", "call-reposted", "check-in-closed"],
)
async def test_reopening_a_check_in_forgets_the_distribution_of_the_call_it_replaces(
    tmp_path, days_out, changes, forgotten
):
    """A check-in reopened by an amendment carries its answers over, not its distribution
    (#429). The reserves were placed against the call being withdrawn, and the call that
    replaces it has its own deadline to place them; a reserve keeping the old team was charged
    as a no-show after the new deadline put them on standby. A check-in the amendment leaves
    closed keeps its distribution: it is the one the division was told about."""
    from datetime import datetime, timedelta, timezone
    from unittest.mock import AsyncMock, MagicMock, patch

    from leaguebot.core.services.amendment_service import AmendmentService

    path = str(tmp_path / "amend_placements.db")
    await run_migrations(path)
    now = datetime.now(timezone.utc)
    await _seed_one_round(path, now + timedelta(days=days_out))
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO driver_profiles (id, discord_user_id, current_state) "
            "VALUES (1, '4242', 'ASSIGNED')"
        )
        await db.execute(
            "INSERT INTO team_instances (id, division_id, name, full_name, max_seats, is_reserve) "
            "VALUES (10, 1, 'Alpha', 'Alpha', 2, 0)"
        )
        await db.execute(
            "INSERT INTO driver_round_attendance "
            "(round_id, division_id, driver_profile_id, rsvp_status, assigned_team_id) "
            "VALUES (1, 1, 1, 'ACCEPTED', 10)"
        )
        await db.commit()

    actor = MagicMock()
    actor.id = 4242
    actor.display_name = "Race Control"
    bot = _amend_bot_with_attendance(path, attendance=True)
    change = (
        ("scheduled_at", now + timedelta(days=40))
        if changes == "moved-out"
        else ("track_name", "Silverstone Circuit")
    )

    with patch("leaguebot.attendance.services.rsvp_service.withdraw_rsvp_call", AsyncMock(return_value=True)), patch(
        "leaguebot.attendance.services.rsvp_service.repost_rsvp_call", AsyncMock(return_value=None)
    ):
        await AmendmentService(path).amend_round(1, actor, [change], bot, now=now)

    assert await _placement(path) == ((None, 0) if forgotten else (10, 0))


# ---------------------------------------------------------------------------
# approve_amendment reposts what it rescored (#130)
# ---------------------------------------------------------------------------


async def _seed_division_with_rounds(path: str, season_id: int):
    """One division with two raced rounds and a third not yet run.

    Returns ``(division_id, raced_round_ids, unraced_round_id)``.
    """
    async with get_connection(path) as db:
        cursor = await db.execute(
            "INSERT INTO divisions (season_id, name, mention_role_id) VALUES (?, 'Alpha', 777)",
            (season_id,),
        )
        division_id = cursor.lastrowid
        await db.execute(
            "INSERT INTO division_results_config "
            "(division_id, results_channel_id, standings_channel_id) VALUES (?, 501, 502)",
            (division_id,),
        )
        raced: list[int] = []
        for round_number in (1, 2):
            cursor = await db.execute(
                "INSERT INTO rounds (division_id, round_number, format, status, scheduled_at) "
                "VALUES (?, ?, 'STANDARD', 'FINAL', '2026-06-01T18:00:00')",
                (division_id, round_number),
            )
            round_id = cursor.lastrowid
            raced.append(round_id)
            await db.execute(
                "INSERT INTO session_results (round_id, division_id, session_type, status) "
                "VALUES (?, ?, 'FEATURE_RACE', 'ACTIVE')",
                (round_id, division_id),
            )
        cursor = await db.execute(
            "INSERT INTO rounds (division_id, round_number, format, status, scheduled_at) "
            "VALUES (?, 3, 'STANDARD', 'NOT_RUN', '2026-09-01T18:00:00')",
            (division_id,),
        )
        unraced_round_id = cursor.lastrowid
        await db.commit()

    return division_id, raced, unraced_round_id


#: The stub bot's own Discord user id. The pre-flight looks its member up by it (#187).
_BOT_USER_ID = 4242


def _bot_recording_reposts(reposted: list[tuple], *, missing: tuple[int, ...] = ()):
    """A bot stub whose guild is real enough for the repost path to run.

    *missing* names channels the guild no longer holds, for the refusal tests: a deleted
    channel is what ``guild.get_channel`` answers None to.

    The channels are specced as ``discord.TextChannel`` because the approval now checks
    that what a division points at is something that can be posted in (#187). A bare
    ``AsyncMock`` is not, and would be refused before any reposting was attempted.

    **A bare ``MagicMock`` grants every permission.** The validation reads permissions with
    ``getattr(permissions, name, False)``, and a ``MagicMock`` answers any attribute with a
    truthy child mock — so the object handed back by ``permissions_for`` below means
    "everything allowed", which is what these tests want: they are about the cascade, not
    about permissions.

    The trap is in the other direction. A test that means to *deny* a permission must set
    that attribute to ``False`` by name, and a **misspelt** attribute silently grants
    instead of denying — leaving a test that reads as though it proves a refusal while
    actually exercising the success path. Set the attribute, then assert on the fault text,
    so the test fails if the denial never took.
    """
    import discord
    from unittest.mock import AsyncMock, MagicMock

    guild = MagicMock()
    guild.id = 1
    # The bot's own member resolves — the pre-flight reads its permissions through it — and
    # nobody else's does, which is what makes the postings below fall back to plain ids for
    # the drivers. Two different questions asked of one cache (#187).
    guild.get_member = lambda user_id: MagicMock() if user_id == _BOT_USER_ID else None
    guild.fetch_member = AsyncMock(side_effect=Exception("not found"))

    def get_channel(channel_id):
        if channel_id in missing:
            return None
        channel = MagicMock(spec=discord.TextChannel)

        async def fake_send(content=None, **kwargs):
            reposted.append((channel_id, content or ""))
            msg = MagicMock()
            msg.id = 4242
            return msg

        channel.send = fake_send
        channel.id = channel_id
        # Everything granted: these tests are about the cascade, not about permissions.
        channel.permissions_for.return_value = MagicMock()
        return channel

    guild.get_channel = get_channel

    bot = MagicMock()
    bot.config_service.get_league_server_id = AsyncMock(return_value=1)
    bot.user.id = _BOT_USER_ID
    bot.get_guild.return_value = guild
    bot.output_router.post_log = AsyncMock()
    bot.module_service.is_attendance_enabled = AsyncMock(return_value=False)
    bot.module_service.is_images_enabled = AsyncMock(return_value=False)
    bot.image_config_service.get_toggles = AsyncMock(return_value={})
    return bot


@pytest.mark.asyncio
async def test_approve_amendment_reposts_every_raced_round(db_path):
    """Approving an amendment must repost what it rescored, not only write it (#130).

    The reply tells the manager "All standings recomputed and reposted". Before the fix
    the repost raised ``TypeError`` on every round, was swallowed by the per-round
    ``try/except``, and the league's channels kept the old points for the rest of the
    season. No test called ``approve_amendment`` at all, which is why the suite passed.
    """
    from leaguebot.core.services.amendment_service import approve_amendment

    path, season_id = db_path
    await _seed_season_points(path, season_id)
    division_id, raced, _unraced = await _seed_division_with_rounds(path, season_id)

    await enable_amendment_mode(path, season_id)
    await modify_session_points(path, season_id, "STD", "FEATURE_RACE", 1, 30)

    reposted: list[tuple] = []
    await approve_amendment(path, season_id, 99, _bot_recording_reposts(reposted))

    assert reposted, "approving an amendment reposted nothing"
    # Both raced rounds reach the standings channel, each headed by its own round number.
    standings = [content for channel_id, content in reposted if channel_id == 502]
    assert len(standings) >= len(raced)
    for round_number in (1, 2):
        assert any(f"Round {round_number}" in content for content in standings), (
            f"round {round_number} was not reposted: {standings}"
        )


@pytest.mark.asyncio
async def test_approve_amendment_does_not_post_for_unraced_rounds(db_path):
    """The cascade walks every non-cancelled round; only the raced ones are reposted (#130)."""
    from leaguebot.core.services.amendment_service import approve_amendment

    path, season_id = db_path
    await _seed_season_points(path, season_id)
    await _seed_division_with_rounds(path, season_id)

    await enable_amendment_mode(path, season_id)
    await modify_session_points(path, season_id, "STD", "FEATURE_RACE", 1, 30)

    reposted: list[tuple] = []
    await approve_amendment(path, season_id, 99, _bot_recording_reposts(reposted))

    assert not any("Round 3" in content for _channel_id, content in reposted), (
        "standings were posted for a round that has not been raced"
    )


@pytest.mark.asyncio
async def test_approve_amendment_still_overwrites_the_points(db_path):
    """The rescore and the repost are one operation — calling it for real proves both."""
    from leaguebot.core.services.amendment_service import approve_amendment

    path, season_id = db_path
    await _seed_season_points(path, season_id)
    await _seed_division_with_rounds(path, season_id)

    await enable_amendment_mode(path, season_id)
    await modify_session_points(path, season_id, "STD", "FEATURE_RACE", 1, 30)

    await approve_amendment(path, season_id, 99, _bot_recording_reposts([]))

    async with get_connection(path) as db:
        row = await (
            await db.execute(
                "SELECT points FROM season_points_entries WHERE season_id = ? AND position = 1",
                (season_id,),
            )
        ).fetchone()
    assert row is not None and row["points"] == 30

    state = await get_amendment_state(path, season_id)
    assert state is not None
    assert not state.amendment_active
    assert not state.modified_flag


@pytest.mark.asyncio
async def test_approve_amendment_overwrites_the_fastest_lap_points(db_path):
    """The fastest lap bonus is overwritten by the same transaction as the points.

    Issue #185. The test that claimed to cover this transaction simulated it by hand and
    knew only about `season_points_entries` — so the whole `season_points_fl` half could
    have been dropped from `approve_amendment` and nothing would have failed. A league
    amending the bonus would have seen the panel accept the change and the old bonus keep
    being awarded.
    """
    from leaguebot.core.services.amendment_service import approve_amendment, modify_fl_bonus

    path, season_id = db_path
    await _seed_season_points(path, season_id)
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO season_points_fl "
            "(season_id, config_name, session_type, fl_points, fl_position_limit) "
            "VALUES (?, 'STD', 'FEATURE_RACE', 1, 10)",
            (season_id,),
        )
        await db.commit()

    await enable_amendment_mode(path, season_id)
    await modify_fl_bonus(path, season_id, "STD", "FEATURE_RACE", 3)

    await approve_amendment(path, season_id, 99, _bot_recording_reposts([]))

    async with get_connection(path) as db:
        row = await (
            await db.execute(
                "SELECT fl_points FROM season_points_fl WHERE season_id = ?",
                (season_id,),
            )
        ).fetchone()
    assert row is not None and row["fl_points"] == 3


@pytest.mark.asyncio
async def test_an_approved_amendment_empties_the_modification_store(db_path):
    """The staging tables are cleared, so the next amendment starts from the season.

    A store left behind is what a second amendment would build on: reopening amendment
    mode would show the previous amendment's figures as though they were pending changes,
    and approving it would rewrite the season with them.
    """
    from leaguebot.core.services.amendment_service import approve_amendment, modify_fl_bonus

    path, season_id = db_path
    await _seed_season_points(path, season_id)
    await enable_amendment_mode(path, season_id)
    await modify_session_points(path, season_id, "STD", "FEATURE_RACE", 1, 30)
    await modify_fl_bonus(path, season_id, "STD", "FEATURE_RACE", 3)

    await approve_amendment(path, season_id, 99, _bot_recording_reposts([]))

    async with get_connection(path) as db:
        for table in ("season_modification_entries", "season_modification_fl"):
            cursor = await db.execute(
                f"SELECT COUNT(*) AS n FROM {table} WHERE season_id = ?",  # noqa: S608
                (season_id,),
            )
            assert (await cursor.fetchone())["n"] == 0, f"{table} was left behind"


# ---------------------------------------------------------------------------
# An amendment that could not be published is refused entire (#187)
#
# Either everything succeeds or everything fails. The approval used to overwrite the
# season's points first and discover afterwards that it could not repost them, swallow
# that, and tell the league the championship had been republished.
# ---------------------------------------------------------------------------


async def _staged_amendment(path: str, season_id: int) -> None:
    """Amendment mode on, with one sound change staged and ready to approve."""
    await enable_amendment_mode(path, season_id)
    await modify_session_points(path, season_id, "STD", "FEATURE_RACE", 1, 30)


async def _season_state(path: str, season_id: int):
    """The three things a refusal must leave exactly as it found them."""
    async with get_connection(path) as db:
        points = await (
            await db.execute(
                "SELECT position, points FROM season_points_entries WHERE season_id = ? "
                "ORDER BY position",
                (season_id,),
            )
        ).fetchall()
        staged = await (
            await db.execute(
                "SELECT position, points FROM season_modification_entries WHERE season_id = ? "
                "ORDER BY position",
                (season_id,),
            )
        ).fetchall()
    state = await get_amendment_state(path, season_id)
    return (
        [(r["position"], r["points"]) for r in points],
        [(r["position"], r["points"]) for r in staged],
        state,
    )


@pytest.mark.asyncio
async def test_an_amendment_is_refused_when_a_division_channel_is_gone(db_path):
    """The headline of #187: a deleted standings channel refuses the approval outright.

    Before the fix this returned cleanly, having deleted the season's points, refilled
    them from the modification store, cleared the store, switched amendment mode off and
    posted ``AMENDMENT_APPROVED | Success`` — then reposted nothing at all, because
    ``guild.get_channel`` answers None for a deleted channel and nothing raises.
    """
    from leaguebot.core.services.amendment_service import AmendmentNotDeliverableError, approve_amendment

    path, season_id = db_path
    await _seed_season_points(path, season_id)
    await _seed_division_with_rounds(path, season_id)
    await _staged_amendment(path, season_id)
    before = await _season_state(path, season_id)

    reposted: list[tuple] = []
    with pytest.raises(AmendmentNotDeliverableError) as excinfo:
        await approve_amendment(
            path, season_id, 99, _bot_recording_reposts(reposted, missing=(502,))
        )

    assert "standings channel" in "; ".join(excinfo.value.faults)
    assert "Alpha" in "; ".join(excinfo.value.faults)
    assert reposted == [], "a refused amendment posted to the league's channels"
    assert await _season_state(path, season_id) == before, (
        "a refused amendment changed the season"
    )


@pytest.mark.asyncio
async def test_a_refused_amendment_keeps_the_season_points(db_path):
    """The points are what the refusal exists to protect: after the DELETE nothing
    could put them back."""
    from leaguebot.core.services.amendment_service import AmendmentNotDeliverableError, approve_amendment

    path, season_id = db_path
    await _seed_season_points(path, season_id)
    await _seed_division_with_rounds(path, season_id)
    await _staged_amendment(path, season_id)

    with pytest.raises(AmendmentNotDeliverableError):
        await approve_amendment(
            path, season_id, 99, _bot_recording_reposts([], missing=(501, 502))
        )

    async with get_connection(path) as db:
        row = await (
            await db.execute(
                "SELECT points FROM season_points_entries WHERE season_id = ? AND position = 1",
                (season_id,),
            )
        ).fetchone()
    assert row is not None and row["points"] == 25, "the season's own points were lost"


@pytest.mark.asyncio
async def test_a_refused_amendment_leaves_the_staged_changes_to_repair(db_path):
    """A manager repairs the channel and approves again; the work must still be there."""
    from leaguebot.core.services.amendment_service import AmendmentNotDeliverableError, approve_amendment

    path, season_id = db_path
    await _seed_season_points(path, season_id)
    await _seed_division_with_rounds(path, season_id)
    await _staged_amendment(path, season_id)

    with pytest.raises(AmendmentNotDeliverableError):
        await approve_amendment(
            path, season_id, 99, _bot_recording_reposts([], missing=(502,))
        )

    state = await get_amendment_state(path, season_id)
    assert state is not None
    assert state.amendment_active, "amendment mode was switched off by a refusal"
    async with get_connection(path) as db:
        row = await (
            await db.execute(
                "SELECT points FROM season_modification_entries WHERE season_id = ? "
                "AND position = 1",
                (season_id,),
            )
        ).fetchone()
    assert row is not None and row["points"] == 30


@pytest.mark.asyncio
async def test_a_refused_amendment_is_not_logged_as_a_success(db_path):
    """Nothing happened, so the log must not say anything did (#187)."""
    from leaguebot.core.services.amendment_service import AmendmentNotDeliverableError, approve_amendment

    path, season_id = db_path
    await _seed_season_points(path, season_id)
    await _seed_division_with_rounds(path, season_id)
    await _staged_amendment(path, season_id)

    bot = _bot_recording_reposts([], missing=(502,))
    with pytest.raises(AmendmentNotDeliverableError):
        await approve_amendment(path, season_id, 99, bot)

    logged = "\n".join(
        str(call.args[0]) for call in bot.output_router.post_log.await_args_list
    )
    assert "AMENDMENT_APPROVED" not in logged, logged


@pytest.mark.asyncio
async def test_an_amendment_is_refused_when_the_bot_cannot_post(db_path):
    """The issue's other reproduction path: Send Messages revoked on a live channel."""
    from unittest.mock import MagicMock

    from leaguebot.core.services.amendment_service import AmendmentNotDeliverableError, approve_amendment

    path, season_id = db_path
    await _seed_season_points(path, season_id)
    await _seed_division_with_rounds(path, season_id)
    await _staged_amendment(path, season_id)

    bot = _bot_recording_reposts([])
    guild = bot.get_guild.return_value
    working = guild.get_channel

    def get_channel(channel_id):
        channel = working(channel_id)
        permissions = MagicMock()
        permissions.send_messages = False
        channel.permissions_for.return_value = permissions
        return channel

    guild.get_channel = get_channel

    with pytest.raises(AmendmentNotDeliverableError) as excinfo:
        await approve_amendment(path, season_id, 99, bot)

    assert "Send Messages" in "; ".join(excinfo.value.faults)


@pytest.mark.asyncio
async def test_an_amendment_is_refused_when_the_guild_is_not_in_cache(db_path):
    """Today this overwrites the points and then silently reposts nothing at all (#187)."""
    from leaguebot.core.services.amendment_service import AmendmentNotDeliverableError, approve_amendment

    path, season_id = db_path
    await _seed_season_points(path, season_id)
    await _seed_division_with_rounds(path, season_id)
    await _staged_amendment(path, season_id)

    bot = _bot_recording_reposts([])
    bot.get_guild.return_value = None

    with pytest.raises(AmendmentNotDeliverableError) as excinfo:
        await approve_amendment(path, season_id, 99, bot)

    assert "not in this server" in "; ".join(excinfo.value.faults)
    async with get_connection(path) as db:
        row = await (
            await db.execute(
                "SELECT points FROM season_points_entries WHERE season_id = ? AND position = 1",
                (season_id,),
            )
        ).fetchone()
    assert row is not None and row["points"] == 25


@pytest.mark.asyncio
async def test_a_division_with_no_channels_does_not_refuse_the_amendment(db_path):
    """The guard against over-refusing: an unconfigured channel is ordinary (#187)."""
    from leaguebot.core.services.amendment_service import approve_amendment

    path, season_id = db_path
    await _seed_season_points(path, season_id)
    async with get_connection(path) as db:
        cursor = await db.execute(
            "INSERT INTO divisions (season_id, name, mention_role_id) VALUES (?, 'Alpha', 777)",
            (season_id,),
        )
        await db.execute(
            "INSERT INTO division_results_config "
            "(division_id, results_channel_id, standings_channel_id) VALUES (?, NULL, NULL)",
            (cursor.lastrowid,),
        )
        await db.commit()
    await _staged_amendment(path, season_id)

    await approve_amendment(path, season_id, 99, _bot_recording_reposts([]))

    async with get_connection(path) as db:
        row = await (
            await db.execute(
                "SELECT points FROM season_points_entries WHERE season_id = ? AND position = 1",
                (season_id,),
            )
        ).fetchone()
    assert row is not None and row["points"] == 30, "a sound amendment was refused"


@pytest.mark.asyncio
async def test_an_amendment_is_refused_when_the_attendance_channel_is_gone(db_path):
    """The approval recalculates attendance too, so its channels are part of the gate."""
    from unittest.mock import AsyncMock

    from leaguebot.core.services.amendment_service import AmendmentNotDeliverableError, approve_amendment

    path, season_id = db_path
    await _seed_season_points(path, season_id)
    division_id, _raced, _unraced = await _seed_division_with_rounds(path, season_id)
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO attendance_config (id, autosack_threshold) VALUES (1, 3)"
        )
        await db.execute(
            "INSERT INTO attendance_division_config (division_id, "
            "attendance_channel_id) VALUES (?, 601)",
            (division_id,),
        )
        await db.commit()
    await _staged_amendment(path, season_id)

    bot = _bot_recording_reposts([], missing=(601,))
    bot.module_service.is_attendance_enabled = AsyncMock(return_value=True)

    with pytest.raises(AmendmentNotDeliverableError) as excinfo:
        await approve_amendment(path, season_id, 99, bot)

    assert "attendance channel" in "; ".join(excinfo.value.faults)


async def _approve_with_attendance(db_path, recalc):
    """Approve a sound amendment with attendance on, the recalculation replaced by *recalc*."""
    from unittest.mock import AsyncMock, patch

    from leaguebot.core.services.amendment_service import approve_amendment

    path, season_id = db_path
    await _seed_season_points(path, season_id)
    division_id, raced, _unraced = await _seed_division_with_rounds(path, season_id)
    await _staged_amendment(path, season_id)
    bot = _bot_recording_reposts([])
    bot.module_service.is_attendance_enabled = AsyncMock(return_value=True)
    with patch(
        "leaguebot.attendance.services.attendance_service.recalculate_attendance_for_round", new=recalc
    ):
        failures = await approve_amendment(path, season_id, 99, bot)
    return failures, bot


@pytest.mark.asyncio
async def test_approval_reports_sanctions_that_did_not_apply(db_path):
    """#239. A sanction the recalculation could not apply used to vanish into the host's
    log; the approval stands, and the failure comes back with the command that finishes it."""
    from unittest.mock import AsyncMock

    from leaguebot.attendance.services.attendance_service import SanctionOutcome

    outcome = SanctionOutcome(failed=[("<@5> (Five)", "autosack", "discord down")])
    failures, _bot = await _approve_with_attendance(
        db_path, AsyncMock(return_value=outcome)
    )

    assert failures[0] == "<@5> (Five) — autosack: discord down"
    assert failures[1].startswith("Repair the cause, then run `/attendance sync division:")
    assert failures[1].endswith("round:2`.")


@pytest.mark.asyncio
async def test_a_recalculation_that_raises_is_reported_and_logged(db_path):
    from unittest.mock import AsyncMock

    failures, bot = await _approve_with_attendance(
        db_path, AsyncMock(side_effect=RuntimeError("database is locked"))
    )

    assert failures[0] == "the attendance could not be recalculated: database is locked"
    logged = "\n".join(str(c.args[0]) for c in bot.output_router.post_log.await_args_list)
    assert "ATTENDANCE_SANCTIONS | Incomplete" in logged


@pytest.mark.asyncio
async def test_a_clean_recalculation_reports_nothing(db_path):
    from unittest.mock import AsyncMock

    from leaguebot.attendance.services.attendance_service import SanctionOutcome

    failures, _bot = await _approve_with_attendance(
        db_path, AsyncMock(return_value=SanctionOutcome())
    )

    assert failures == []


@pytest.mark.asyncio
async def test_the_attendance_channels_are_not_checked_while_the_module_is_off(db_path):
    """A league without the attendance module must not be refused for a channel it has
    never configured — the same gate the cascade's own recalculation holds to."""
    from leaguebot.core.services.amendment_service import approve_amendment

    path, season_id = db_path
    await _seed_season_points(path, season_id)
    division_id, _raced, _unraced = await _seed_division_with_rounds(path, season_id)
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO attendance_config (id, autosack_threshold) VALUES (1, 3)"
        )
        await db.execute(
            "INSERT INTO attendance_division_config (division_id, "
            "attendance_channel_id) VALUES (?, 601)",
            (division_id,),
        )
        await db.commit()
    await _staged_amendment(path, season_id)

    # 601 is absent, but the module is off, so nothing asks after it.
    await approve_amendment(path, season_id, 99, _bot_recording_reposts([], missing=(601,)))

    async with get_connection(path) as db:
        row = await (
            await db.execute(
                "SELECT points FROM season_points_entries WHERE season_id = ? AND position = 1",
                (season_id,),
            )
        ).fetchone()
    assert row is not None and row["points"] == 30, "a sound amendment was refused"


@pytest.mark.asyncio
async def test_the_approval_is_logged_after_the_cascade_not_before(db_path):
    """The log records the approval once the reposting it claims has been done (#187).

    It used to be posted the moment the points were committed, before a single message had
    been attempted — so ``AMENDMENT_APPROVED | Success`` stood in the log whatever became
    of the cascade, and a manager reading it had no reason to check the channels.
    """
    from unittest.mock import AsyncMock

    from leaguebot.core.services.amendment_service import approve_amendment

    path, season_id = db_path
    await _seed_season_points(path, season_id)
    await _seed_division_with_rounds(path, season_id)
    await _staged_amendment(path, season_id)

    order: list[str] = []
    reposted: list[tuple] = []
    bot = _bot_recording_reposts(reposted)
    guild = bot.get_guild.return_value
    working = guild.get_channel

    def get_channel(channel_id):
        channel = working(channel_id)
        sent = channel.send

        async def recording_send(content=None, **kwargs):
            order.append("repost")
            return await sent(content, **kwargs)

        channel.send = recording_send
        return channel

    guild.get_channel = get_channel

    async def recording_log(content):
        order.append("log" if "AMENDMENT_APPROVED" in str(content) else "other-log")

    bot.output_router.post_log = AsyncMock(side_effect=recording_log)

    await approve_amendment(path, season_id, 99, bot)

    assert "repost" in order, "nothing was reposted, so the ordering proves nothing"
    assert order.index("log") > order.index("repost"), order
    assert order[-1] == "log", f"the approval was not the last thing logged: {order}"


@pytest.mark.asyncio
async def test_the_ordering_refusal_still_comes_first(db_path):
    """A table out of order is refused as such, not as an undeliverable one — the two
    refusals name different repairs and must not be confused."""
    from leaguebot.core.services.amendment_service import NonMonotonicAmendmentError, approve_amendment

    path, season_id = db_path
    await _seed_season_points(path, season_id)
    await _seed_division_with_rounds(path, season_id)
    await enable_amendment_mode(path, season_id)
    await modify_session_points(path, season_id, "STD", "FEATURE_RACE", 1, 25)
    await modify_session_points(path, season_id, "STD", "FEATURE_RACE", 2, 25)

    with pytest.raises(NonMonotonicAmendmentError):
        await approve_amendment(
            path, season_id, 99, _bot_recording_reposts([], missing=(501, 502))
        )


# ---------------------------------------------------------------------------
# The ordering rule holds at the mid-season end too
#
# The confirmation of placements refuses a points table that is out of order. Approving an amendment
# installed one without a word — deleting the season's points and refilling them from
# the modification store, then rescoring and reposting every round of every division
# against the new numbers. A rule that bound only the approval was a rule a league could
# step around by approving a good table and amending it afterwards.
# ---------------------------------------------------------------------------


async def _seed_two_position_table(db_path: str, season_id: int) -> None:
    """A season scoring 25 for a win and 18 for second, which is where a league starts."""
    async with get_connection(db_path) as db:
        for position, points in [(1, 25), (2, 18)]:
            await db.execute(
                "INSERT INTO season_points_entries "
                "(season_id, config_name, session_type, position, points) "
                "VALUES (?, 'STD', 'FEATURE_RACE', ?, ?)",
                (season_id, position, points),
            )
        await db.commit()


async def _season_points(db_path: str, season_id: int) -> dict[int, int]:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT position, points FROM season_points_entries WHERE season_id = ?",
            (season_id,),
        )
        return {r["position"]: r["points"] for r in await cursor.fetchall()}


@pytest.mark.asyncio
async def test_validate_modification_ordering_passes_a_table_running_down(db_path):
    path, season_id = db_path
    await _seed_two_position_table(path, season_id)
    await enable_amendment_mode(path, season_id)
    await modify_session_points(path, season_id, "STD", "FEATURE_RACE", 1, 30)

    from leaguebot.core.services.amendment_service import validate_modification_ordering

    assert await validate_modification_ordering(path, season_id) == []


@pytest.mark.asyncio
async def test_validate_modification_ordering_names_a_staged_inversion(db_path):
    path, season_id = db_path
    await _seed_two_position_table(path, season_id)
    await enable_amendment_mode(path, season_id)
    await modify_session_points(path, season_id, "STD", "FEATURE_RACE", 2, 30)

    from leaguebot.core.services.amendment_service import validate_modification_ordering

    errors = await validate_modification_ordering(path, season_id)

    assert len(errors) == 1
    assert "STD" in errors[0]
    assert "FEATURE_RACE" in errors[0]


@pytest.mark.asyncio
async def test_validate_modification_ordering_judges_each_session_on_its_own(db_path):
    """A qualifying table worth 1, 2, 3 is wrong; it does not make the race table wrong."""
    path, season_id = db_path
    await _seed_two_position_table(path, season_id)
    await enable_amendment_mode(path, season_id)
    await modify_session_points(path, season_id, "STD", "FEATURE_QUALIFYING", 1, 1)
    await modify_session_points(path, season_id, "STD", "FEATURE_QUALIFYING", 2, 3)

    from leaguebot.core.services.amendment_service import validate_modification_ordering

    errors = await validate_modification_ordering(path, season_id)

    assert len(errors) == 1
    assert "FEATURE_QUALIFYING" in errors[0]


@pytest.mark.asyncio
async def test_approve_amendment_refuses_a_table_out_of_order(db_path):
    """The regression. Before the fix this amendment was applied without a word."""
    from leaguebot.core.services.amendment_service import NonMonotonicAmendmentError, approve_amendment

    path, season_id = db_path
    await _seed_two_position_table(path, season_id)
    await _seed_division_with_rounds(path, season_id)
    await enable_amendment_mode(path, season_id)
    await modify_session_points(path, season_id, "STD", "FEATURE_RACE", 2, 30)

    with pytest.raises(NonMonotonicAmendmentError) as raised:
        await approve_amendment(path, season_id, 99, _bot_recording_reposts([]))

    assert raised.value.errors, "the refusal must carry what is wrong with it"
    assert "STD" in raised.value.errors[0]


@pytest.mark.asyncio
async def test_a_table_both_out_of_order_and_undeliverable_refuses_on_the_ordering(db_path):
    """Both checks apply; the ordering one is reached first, and nothing is written (#187).

    They are two independent refusals and an exception carries only one, so which is
    raised is a real decision rather than an accident of control flow. The ordering goes
    first because it is the staged table's own fault — the manager can repair it from the
    panel — where an unreachable channel is the server's and may right itself. Pinned
    because the outcome is what actually matters and is the same either way: refused
    entire, nothing changed. The panel is the surface that names **both**, which
    ``test_the_panel_names_both_faults_when_both_apply`` holds.
    """
    from leaguebot.core.services.amendment_service import NonMonotonicAmendmentError, approve_amendment

    path, season_id = db_path
    await _seed_two_position_table(path, season_id)
    await _seed_division_with_rounds(path, season_id)
    await enable_amendment_mode(path, season_id)
    await modify_session_points(path, season_id, "STD", "FEATURE_RACE", 2, 30)

    before = await _season_state(path, season_id)
    reposted: list[tuple] = []
    with pytest.raises(NonMonotonicAmendmentError):
        await approve_amendment(
            path, season_id, 99, _bot_recording_reposts(reposted, missing=(501, 502))
        )

    assert not reposted
    assert await _season_state(path, season_id) == before, (
        "a refusal for either reason must leave the season exactly as it stood"
    )


@pytest.mark.asyncio
async def test_a_refused_amendment_leaves_the_season_exactly_as_it_stood(db_path):
    """Nothing written: not the points, not the store, not the mode, and nothing reposted.

    This function's first act is to delete the season's points. A guard placed even one
    statement late would leave a running championship with no points table at all.
    """
    from leaguebot.core.services.amendment_service import NonMonotonicAmendmentError, approve_amendment

    path, season_id = db_path
    await _seed_two_position_table(path, season_id)
    await _seed_division_with_rounds(path, season_id)
    await enable_amendment_mode(path, season_id)
    await modify_session_points(path, season_id, "STD", "FEATURE_RACE", 2, 30)

    reposted: list[tuple] = []
    with pytest.raises(NonMonotonicAmendmentError):
        await approve_amendment(path, season_id, 99, _bot_recording_reposts(reposted))

    assert await _season_points(path, season_id) == {1: 25, 2: 18}, "the season's points moved"
    assert not reposted, "a refused amendment reposted a standings table"

    state = await get_amendment_state(path, season_id)
    assert state is not None
    assert state.amendment_active, "amendment mode was switched off by a refusal"
    assert state.modified_flag, "the staged change was thrown away"

    async with get_connection(path) as db:
        cursor = await db.execute(
            "SELECT points FROM season_modification_entries "
            "WHERE season_id = ? AND position = 2",
            (season_id,),
        )
        row = await cursor.fetchone()
    assert row is not None and row["points"] == 30, (
        "the working copy must survive so the manager can repair it"
    )


@pytest.mark.asyncio
async def test_a_well_ordered_amendment_still_applies(db_path):
    """The other half: a guard that refuses everything is no better than none at all."""
    from leaguebot.core.services.amendment_service import approve_amendment

    path, season_id = db_path
    await _seed_two_position_table(path, season_id)
    await _seed_division_with_rounds(path, season_id)
    await enable_amendment_mode(path, season_id)
    await modify_session_points(path, season_id, "STD", "FEATURE_RACE", 1, 30)

    await approve_amendment(path, season_id, 99, _bot_recording_reposts([]))

    assert await _season_points(path, season_id) == {1: 30, 2: 18}


@pytest.mark.asyncio
async def test_an_amendment_paying_nothing_below_the_points_still_applies(db_path):
    """Trailing zeros are the ordinary shape of a table, mid-season as at the start."""
    from leaguebot.core.services.amendment_service import approve_amendment

    path, season_id = db_path
    await _seed_two_position_table(path, season_id)
    await _seed_division_with_rounds(path, season_id)
    await enable_amendment_mode(path, season_id)
    await modify_session_points(path, season_id, "STD", "FEATURE_RACE", 3, 0)
    await modify_session_points(path, season_id, "STD", "FEATURE_RACE", 4, 0)

    await approve_amendment(path, season_id, 99, _bot_recording_reposts([]))

    assert await _season_points(path, season_id) == {1: 25, 2: 18, 3: 0, 4: 0}
