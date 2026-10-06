"""Unit tests for amendment_service (T034) — points-amendment workflow."""
from __future__ import annotations

import itertools
from datetime import datetime, timezone

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


#: Who staged each change, and when: the modification store records each change it stages (#442).
ACTOR = {
    "actor_id": 99,
    "actor_name": "Admin#0001",
    "now": datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc),
}


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
    await modify_session_points(
        path, season_id, "STD", "FEATURE_RACE", [(1, 30)], **ACTOR
    )

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
        await modify_session_points(
            path, season_id, "STD", "FEATURE_RACE", [(1, 30)], **ACTOR
        )


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
# One line for a confirmed /round amend, written straight after the save (#482)
# ---------------------------------------------------------------------------
#
# `/round amend` wrote two success lines for one amendment: this service's "/round amend (field)"
# line, naming the fields by their columns, and the confirmation's own. The one line now stays
# here, in the cogs' success form, and is written as soon as the amendment is saved, so it stands
# whatever fails after the save: the core specification's "The record of what changed".

#: The moment these amendments are made at, pinned so that the round's phases are judged alike
#: on every run.
_AMEND_NOW = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)


async def _round_to_amend(tmp_path, *, scheduled_at: datetime) -> str:
    """Round 1 of division Div A in season 1, raced, at Bahrain International Circuit in the
    NORMAL format, at *scheduled_at*, with no forecast drawn yet."""
    path = str(tmp_path / "amend_line.db")
    await run_migrations(path)
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
            "INSERT INTO divisions (id, season_id, name, tier, forecast_channel_id, "
            "mention_role_id) VALUES (1, 1, 'Div A', 1, 999, 555)"
        )
        await db.execute(
            "INSERT INTO rounds (id, division_id, round_number, format, track_name, scheduled_at) "
            "VALUES (1, 1, 1, 'NORMAL', 'Bahrain International Circuit', ?)",
            (scheduled_at.isoformat(),),
        )
        await db.commit()
    return path


def _amending_bot(path: str, *, weather: bool = False):
    """The bot an amendment is made through: attendance off, weather as asked, every line the
    amendment writes kept."""
    from unittest.mock import AsyncMock, MagicMock

    bot = MagicMock()
    bot.db_path = path
    bot.config_service.get_league_server_id = AsyncMock(return_value=1)
    bot.module_service.is_weather_enabled = AsyncMock(return_value=weather)
    bot.module_service.is_attendance_enabled = AsyncMock(return_value=False)
    bot.output_router.post_forecast = AsyncMock(return_value=None)
    bot.output_router.post_log = AsyncMock(return_value=None)
    bot.scheduler_service.cancel_round = MagicMock()
    bot.scheduler_service.schedule_round = MagicMock()
    return bot


def _race_control():
    from unittest.mock import MagicMock

    actor = MagicMock()
    actor.id = 4242
    actor.display_name = "Race Control"
    return actor


@pytest.mark.asyncio
async def test_a_round_amendment_writes_one_line_naming_the_member_the_round_and_each_change(
    tmp_path,
):
    """Race Control amends round 1 from Bahrain International Circuit in the NORMAL format to
    Silverstone Circuit in the SPRINT format. One line is written, in the success form, naming
    Race Control and `/round amend`, the round, and each field from what to what, named as the
    command's parameter (`track`) rather than by its column."""
    from datetime import timedelta

    from leaguebot.core.models.round import RoundFormat
    from leaguebot.core.services.amendment_service import AmendmentService

    path = await _round_to_amend(tmp_path, scheduled_at=_AMEND_NOW + timedelta(days=30))
    bot = _amending_bot(path)

    await AmendmentService(path).amend_round(
        1,
        _race_control(),
        [("track_name", "Silverstone Circuit"), ("format", RoundFormat.SPRINT)],
        bot,
        now=_AMEND_NOW,
    )

    [line] = [str(c.args[0]) for c in bot.output_router.post_log.await_args_list]
    head, _, body = line.partition("\n")
    assert head == "Race Control (<@4242>) | /round amend | Success"
    assert "round 1" in body.lower()
    changes = body.splitlines()
    assert "  track: Bahrain International Circuit → Silverstone Circuit" in changes
    assert "  format: NORMAL → SPRINT" in changes
    assert "track_name" not in body


@pytest.mark.asyncio
async def test_a_round_amendment_line_leaves_out_a_field_given_at_the_value_it_held(tmp_path):
    """Race Control amends round 1, which stands at Bahrain International Circuit in the NORMAL
    format, giving the track it already has and the SPRINT format. The one line names the format
    from what to what, and says nothing of the track, which did not change: a line reading
    'track: Bahrain International Circuit → Bahrain International Circuit' would record a change
    that was never made."""
    from datetime import timedelta

    from leaguebot.core.models.round import RoundFormat
    from leaguebot.core.services.amendment_service import AmendmentService

    path = await _round_to_amend(tmp_path, scheduled_at=_AMEND_NOW + timedelta(days=30))
    bot = _amending_bot(path)

    await AmendmentService(path).amend_round(
        1,
        _race_control(),
        [("track_name", "Bahrain International Circuit"), ("format", RoundFormat.SPRINT)],
        bot,
        now=_AMEND_NOW,
    )

    [line] = [str(c.args[0]) for c in bot.output_router.post_log.await_args_list]
    head, _, body = line.partition("\n")
    assert head == "Race Control (<@4242>) | /round amend | Success"
    assert "  format: NORMAL → SPRINT" in body.splitlines()
    assert "track:" not in body


@pytest.mark.parametrize("fault_in", ["cancelling the round's jobs", "re-running a phase"])
@pytest.mark.asyncio
async def test_a_round_amendment_that_fails_after_the_save_still_leaves_its_line(
    tmp_path, fault_in
):
    """Race Control moves round 1, raced a day ago with no forecast drawn, from Bahrain
    International Circuit to Silverstone Circuit, with weather on. The amendment is saved, then
    fails: the scheduler cannot cancel the round's jobs, or the first overdue forecast cannot be
    drawn again. The round stands amended, and its one success line has been written before the
    fault, so the record holds what changed."""
    from datetime import timedelta
    from unittest.mock import AsyncMock, MagicMock, patch

    from leaguebot.core.services.amendment_service import AmendmentService

    path = await _round_to_amend(tmp_path, scheduled_at=_AMEND_NOW - timedelta(days=1))
    bot = _amending_bot(path, weather=True)
    fault = RuntimeError("the fault after the save")
    if fault_in == "cancelling the round's jobs":
        bot.scheduler_service.cancel_round = MagicMock(side_effect=fault)

    with patch(
        "leaguebot.weather.services.phase1_service.run_phase1",
        new=AsyncMock(side_effect=fault),
    ), patch(
        "leaguebot.weather.services.phase2_service.run_phase2", new=AsyncMock()
    ), patch(
        "leaguebot.weather.services.phase3_service.run_phase3", new=AsyncMock()
    ):
        with pytest.raises(RuntimeError, match="the fault after the save"):
            await AmendmentService(path).amend_round(
                1, _race_control(), [("track_name", "Silverstone Circuit")], bot, now=_AMEND_NOW
            )

    async with get_connection(path) as db:
        cursor = await db.execute("SELECT track_name FROM rounds WHERE id = 1")
        assert (await cursor.fetchone())["track_name"] == "Silverstone Circuit"
    [line] = [str(c.args[0]) for c in bot.output_router.post_log.await_args_list]
    assert line.startswith("Race Control (<@4242>) | /round amend | Success\n")
    assert "  track: Bahrain International Circuit → Silverstone Circuit" in line.splitlines()


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
# Approving a points amendment reposts what it rescored (#130)
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

#: Approving a points amendment is a change on the queue (#439, slice 3): until it is built, a
#: test that approves through it fails on the change type's import.

#: "Now", for the queue that carries the approval.
_APPROVE_NOW = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)

_message_ids = itertools.count(4300)


def _bot_recording_reposts(path: str, reposted: list[tuple], *, missing: tuple[int, ...] = ()):
    """A bot double whose guild is real enough for the approval's posting jobs to run.

    Built on the change queue's `league_double` (#439, slice 3): a real output router, so every
    line the approval writes lands in `bot.log_channel`, whose `sent` records it. Its guild's
    channels record each send in *reposted* as ``(channel_id, content)``, and each message they
    send can be fetched and deleted again, as the jobs replacing an old message do.

    *missing* names channels the guild no longer holds, for the refusal tests: a deleted
    channel is what ``guild.get_channel`` answers None to.

    The channels are specced as ``discord.TextChannel`` because the approval checks that what a
    division points at is something that can be posted in (#187). A bare ``AsyncMock`` is not,
    and would be refused before any reposting was attempted.

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

    from tests.support.change_queue import SERVER_ID, league_double

    bot = league_double(path)
    guild = MagicMock()
    guild.id = SERVER_ID
    # The bot's own member resolves — the pre-flight reads its permissions through it — and
    # nobody else's does, which is what makes the postings below fall back to plain ids for
    # the drivers. Two different questions asked of one cache (#187).
    guild.get_member = lambda user_id: MagicMock() if user_id == _BOT_USER_ID else None
    guild.fetch_member = AsyncMock(
        side_effect=discord.NotFound(MagicMock(status=404, reason="Not Found"), "Unknown Member")
    )
    channels: dict[int, object] = {}

    def _channel(channel_id):
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = channel_id
        channel.mention = f"<#{channel_id}>"
        channel.guild = guild
        messages: dict[int, object] = {}

        def _message(message_id):
            message = MagicMock()
            message.id = message_id
            message.channel = channel
            message.jump_url = f"https://discord.test/{channel_id}/{message_id}"

            async def _delete(*_args, **_kwargs):
                messages.pop(message_id, None)

            message.delete = AsyncMock(side_effect=_delete)
            message.edit = AsyncMock(return_value=message)
            return message

        async def fake_send(content=None, **kwargs):
            reposted.append((channel_id, content or ""))
            message = _message(next(_message_ids))
            message.content = content or ""
            messages[message.id] = message
            return message

        async def fetch_message(message_id):
            if message_id not in messages:
                raise discord.NotFound(MagicMock(status=404, reason="Not Found"), "Unknown Message")
            return messages[message_id]

        channel.send = AsyncMock(side_effect=fake_send)
        channel.fetch_message = AsyncMock(side_effect=fetch_message)
        channel.get_partial_message = MagicMock(
            side_effect=lambda message_id: messages.get(message_id) or _message(message_id)
        )
        # Everything granted: these tests are about the cascade, not about permissions.
        channel.permissions_for.return_value = MagicMock()
        return channel

    def get_channel(channel_id):
        if channel_id in missing:
            return None
        if channel_id not in channels:
            channels[channel_id] = _channel(channel_id)
        return channels[channel_id]

    guild.get_channel = get_channel

    bot.user.id = _BOT_USER_ID
    bot.get_guild = MagicMock(return_value=guild)
    bot.module_service.is_attendance_enabled = AsyncMock(return_value=False)
    bot.module_service.is_images_enabled = AsyncMock(return_value=False)
    bot.image_config_service.get_toggles = AsyncMock(return_value={})
    return bot


async def _approve(path: str, season_id: int, bot):
    """Press Approve on the season's points amendment, as user 99, and run the queue.

    A queue is attached on the test's database, results turned on and the season ongoing, as
    a league using amendments has them, and the approval asked for through a league admin's press, as
    `/results amend review`'s Approve button asks it (#439, slice 3). Returns the press, whose
    reply is the queue's acknowledgement, or the refusal the change's check gave at once.
    """
    from leaguebot.results.services.points_amendment_change import KIND
    from tests.support.change_queue import attach_queue, member_interaction, run_queue, tier_member

    async with get_connection(path) as db:
        await db.execute(
            "INSERT OR IGNORE INTO results_module_config (id, module_enabled) VALUES (1, 1)"
        )
        # The season is being raced: the approval's check refuses a season with no stage.
        await db.execute(
            "UPDATE seasons SET stage = 'ONGOING' WHERE id = ? AND stage IS NULL", (season_id,)
        )
        cursor = await db.execute(
            "SELECT season_number FROM seasons WHERE id = ?", (season_id,)
        )
        season_number = (await cursor.fetchone())["season_number"]
        await db.commit()
    attach_queue(bot, path, now=_APPROVE_NOW)
    press = member_interaction(bot, user=tier_member("admin", member_id=99))
    await bot.change_queue.ask(
        KIND, {"season_id": season_id, "season_number": season_number},
        interaction=press, what="`/results amend review`",
    )
    await run_queue(bot)
    return press


def _reply(press) -> str:
    """What the press was answered with."""
    from tests.support.change_queue import acknowledgement

    return acknowledgement(press)


async def _nothing_queued(path: str) -> bool:
    from tests.support.change_queue import change_rows

    return await change_rows(path) == []


@pytest.mark.asyncio
async def test_approve_amendment_reposts_every_raced_round(db_path):
    """Approving an amendment must repost what it rescored, not only write it (#130).

    The reply tells the manager "All standings recomputed and reposted". Before the fix
    the repost raised ``TypeError`` on every round, was swallowed by the per-round
    ``try/except``, and the league's channels kept the old points for the rest of the
    season. No test called ``approve_amendment`` at all, which is why the suite passed. The
    approval is now a change on the queue (#439, slice 3), its reposts jobs of its own.
    """
    path, season_id = db_path
    await _seed_season_points(path, season_id)
    division_id, raced, _unraced = await _seed_division_with_rounds(path, season_id)

    await enable_amendment_mode(path, season_id)
    await modify_session_points(
        path, season_id, "STD", "FEATURE_RACE", [(1, 30)], **ACTOR
    )

    reposted: list[tuple] = []
    await _approve(path, season_id, _bot_recording_reposts(path, reposted))

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
    path, season_id = db_path
    await _seed_season_points(path, season_id)
    await _seed_division_with_rounds(path, season_id)

    await enable_amendment_mode(path, season_id)
    await modify_session_points(
        path, season_id, "STD", "FEATURE_RACE", [(1, 30)], **ACTOR
    )

    reposted: list[tuple] = []
    await _approve(path, season_id, _bot_recording_reposts(path, reposted))

    assert not any("Round 3" in content for _channel_id, content in reposted), (
        "standings were posted for a round that has not been raced"
    )


@pytest.mark.asyncio
async def test_approve_amendment_still_overwrites_the_points(db_path):
    """The rescore and the repost are one operation — calling it for real proves both."""
    path, season_id = db_path
    await _seed_season_points(path, season_id)
    await _seed_division_with_rounds(path, season_id)

    await enable_amendment_mode(path, season_id)
    await modify_session_points(
        path, season_id, "STD", "FEATURE_RACE", [(1, 30)], **ACTOR
    )

    await _approve(path, season_id, _bot_recording_reposts(path, []))

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
    from leaguebot.core.services.amendment_service import modify_fl_bonus

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

    await _approve(path, season_id, _bot_recording_reposts(path, []))

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
    from leaguebot.core.services.amendment_service import modify_fl_bonus

    path, season_id = db_path
    await _seed_season_points(path, season_id)
    await enable_amendment_mode(path, season_id)
    await modify_session_points(
        path, season_id, "STD", "FEATURE_RACE", [(1, 30)], **ACTOR
    )
    await modify_fl_bonus(path, season_id, "STD", "FEATURE_RACE", 3)

    await _approve(path, season_id, _bot_recording_reposts(path, []))

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
    await modify_session_points(
        path, season_id, "STD", "FEATURE_RACE", [(1, 30)], **ACTOR
    )


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
    path, season_id = db_path
    await _seed_season_points(path, season_id)
    await _seed_division_with_rounds(path, season_id)
    await _staged_amendment(path, season_id)
    before = await _season_state(path, season_id)

    reposted: list[tuple] = []
    press = await _approve(
        path, season_id, _bot_recording_reposts(path, reposted, missing=(502,))
    )

    assert "standings channel" in _reply(press)
    assert "Alpha" in _reply(press)
    assert await _nothing_queued(path), "a refused approval was queued"
    assert reposted == [], "a refused amendment posted to the league's channels"
    assert await _season_state(path, season_id) == before, (
        "a refused amendment changed the season"
    )


@pytest.mark.asyncio
async def test_a_refused_amendment_keeps_the_season_points(db_path):
    """The points are what the refusal exists to protect: after the DELETE nothing
    could put them back."""
    path, season_id = db_path
    await _seed_season_points(path, season_id)
    await _seed_division_with_rounds(path, season_id)
    await _staged_amendment(path, season_id)

    press = await _approve(
        path, season_id, _bot_recording_reposts(path, [], missing=(501, 502))
    )

    assert "could not be published" in _reply(press)
    assert await _nothing_queued(path), "a refused approval was queued"
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
    path, season_id = db_path
    await _seed_season_points(path, season_id)
    await _seed_division_with_rounds(path, season_id)
    await _staged_amendment(path, season_id)

    press = await _approve(
        path, season_id, _bot_recording_reposts(path, [], missing=(502,))
    )

    assert "could not be published" in _reply(press)
    assert await _nothing_queued(path), "a refused approval was queued"
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
    path, season_id = db_path
    await _seed_season_points(path, season_id)
    await _seed_division_with_rounds(path, season_id)
    await _staged_amendment(path, season_id)

    bot = _bot_recording_reposts(path, [], missing=(502,))
    press = await _approve(path, season_id, bot)

    assert "could not be published" in _reply(press)
    assert await _nothing_queued(path), "a refused approval was queued"
    logged = "\n".join(bot.log_channel.sent)
    assert "| Success" not in logged, logged


@pytest.mark.asyncio
async def test_an_amendment_is_refused_when_the_bot_cannot_post(db_path):
    """The issue's other reproduction path: Send Messages revoked on a live channel."""
    from unittest.mock import MagicMock

    path, season_id = db_path
    await _seed_season_points(path, season_id)
    await _seed_division_with_rounds(path, season_id)
    await _staged_amendment(path, season_id)

    bot = _bot_recording_reposts(path, [])
    guild = bot.get_guild.return_value
    working = guild.get_channel

    def get_channel(channel_id):
        channel = working(channel_id)
        permissions = MagicMock()
        permissions.send_messages = False
        channel.permissions_for.return_value = permissions
        return channel

    guild.get_channel = get_channel

    press = await _approve(path, season_id, bot)

    assert "Send Messages" in _reply(press)
    assert await _nothing_queued(path), "a refused approval was queued"


@pytest.mark.asyncio
async def test_an_amendment_is_refused_when_the_guild_is_not_in_cache(db_path):
    """Today this overwrites the points and then silently reposts nothing at all (#187)."""
    path, season_id = db_path
    await _seed_season_points(path, season_id)
    await _seed_division_with_rounds(path, season_id)
    await _staged_amendment(path, season_id)

    bot = _bot_recording_reposts(path, [])
    bot.get_guild.return_value = None

    press = await _approve(path, season_id, bot)

    assert "not in this server" in _reply(press)
    assert await _nothing_queued(path), "a refused approval was queued"
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

    await _approve(path, season_id, _bot_recording_reposts(path, []))

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

    bot = _bot_recording_reposts(path, [], missing=(601,))
    bot.module_service.is_attendance_enabled = AsyncMock(return_value=True)

    press = await _approve(path, season_id, bot)

    assert "attendance channel" in _reply(press)
    assert await _nothing_queued(path), "a refused approval was queued"


@pytest.mark.asyncio
async def test_approval_faults_names_a_cancelled_division_s_deleted_attendance_channel(db_path):
    """A division cancelled mid-season, attendance on, whose attendance channel (601) has been
    deleted: the approval reposts and recalculates its raced rounds too, so the check names the
    channel, as the results specification's "every division's" channels require (owner,
    2026-10-06, "Refuse at the press")."""
    from unittest.mock import AsyncMock

    from leaguebot.core.services.amendment_service import approval_faults

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
        await db.execute(
            "UPDATE divisions SET status = 'CANCELLED' WHERE id = ?", (division_id,)
        )
        await db.commit()
    await _staged_amendment(path, season_id)

    bot = _bot_recording_reposts(path, [], missing=(601,))
    bot.module_service.is_attendance_enabled = AsyncMock(return_value=True)

    faults = await approval_faults(path, season_id, bot)

    assert len(faults) == 1, faults
    assert "attendance channel (id 601)" in faults[0]


@pytest.mark.asyncio
async def test_the_attendance_channels_are_not_checked_while_the_module_is_off(db_path):
    """A league without the attendance module must not be refused for a channel it has
    never configured — the same gate the cascade's own recalculation holds to."""
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
    await _approve(path, season_id, _bot_recording_reposts(path, [], missing=(601,)))

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
    path, season_id = db_path
    await _seed_season_points(path, season_id)
    await _seed_division_with_rounds(path, season_id)
    await _staged_amendment(path, season_id)

    order: list[str] = []

    class _Reposts(list):
        def append(self, item):
            order.append("repost")
            super().append(item)

    bot = _bot_recording_reposts(path, _Reposts())
    # The fixture's server names its log channel 30, which the queue's close line is sent to.
    get_channel, fetch_channel = bot.get_channel.side_effect, bot.fetch_channel.side_effect
    bot.get_channel.side_effect = lambda cid: bot.log_channel if cid == 30 else get_channel(cid)
    bot.fetch_channel.side_effect = (
        lambda cid: bot.log_channel if cid == 30 else fetch_channel(cid)
    )
    logged = bot.log_channel.send.side_effect

    async def recording_log(content="", **kwargs):
        order.append("log" if "| Success" in str(content) else "other-log")
        return await logged(content, **kwargs)

    bot.log_channel.send.side_effect = recording_log

    await _approve(path, season_id, bot)

    assert "repost" in order, "nothing was reposted, so the ordering proves nothing"
    last_repost = len(order) - 1 - order[::-1].index("repost")
    assert order.index("log") > last_repost, order
    assert order[-1] == "log", f"the approval was not the last thing logged: {order}"


@pytest.mark.asyncio
async def test_the_ordering_refusal_still_comes_first(db_path):
    """A table out of order is refused as such, not as an undeliverable one — the two
    refusals name different repairs and must not be confused."""
    path, season_id = db_path
    await _seed_season_points(path, season_id)
    await _seed_division_with_rounds(path, season_id)
    await enable_amendment_mode(path, season_id)
    await modify_session_points(
        path, season_id, "STD", "FEATURE_RACE", [(1, 25)], **ACTOR
    )
    await modify_session_points(
        path, season_id, "STD", "FEATURE_RACE", [(2, 25)], **ACTOR
    )

    press = await _approve(
        path, season_id, _bot_recording_reposts(path, [], missing=(501, 502))
    )

    assert _reply(press).startswith(
        "❌ Amendment not approved — the points would be out of order:"
    ), _reply(press)
    assert "could not be published" not in _reply(press)
    assert await _nothing_queued(path), "a refused approval was queued"


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
    await modify_session_points(
        path, season_id, "STD", "FEATURE_RACE", [(1, 30)], **ACTOR
    )

    from leaguebot.core.services.amendment_service import validate_modification_ordering

    assert await validate_modification_ordering(path, season_id) == []


@pytest.mark.asyncio
async def test_validate_modification_ordering_names_a_staged_inversion(db_path):
    path, season_id = db_path
    await _seed_two_position_table(path, season_id)
    await enable_amendment_mode(path, season_id)
    await modify_session_points(
        path, season_id, "STD", "FEATURE_RACE", [(2, 30)], **ACTOR
    )

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
    await modify_session_points(
        path, season_id, "STD", "FEATURE_QUALIFYING", [(1, 1)], **ACTOR
    )
    await modify_session_points(
        path, season_id, "STD", "FEATURE_QUALIFYING", [(2, 3)], **ACTOR
    )

    from leaguebot.core.services.amendment_service import validate_modification_ordering

    errors = await validate_modification_ordering(path, season_id)

    assert len(errors) == 1
    assert "FEATURE_QUALIFYING" in errors[0]


@pytest.mark.asyncio
async def test_approve_amendment_refuses_a_table_out_of_order(db_path):
    """The regression. Before the fix this amendment was applied without a word."""
    path, season_id = db_path
    await _seed_two_position_table(path, season_id)
    await _seed_division_with_rounds(path, season_id)
    await enable_amendment_mode(path, season_id)
    await modify_session_points(
        path, season_id, "STD", "FEATURE_RACE", [(2, 30)], **ACTOR
    )

    press = await _approve(path, season_id, _bot_recording_reposts(path, []))

    assert _reply(press).startswith(
        "❌ Amendment not approved — the points would be out of order:"
    ), _reply(press)
    assert "STD" in _reply(press), "the refusal must carry what is wrong with it"
    assert await _nothing_queued(path), "a refused approval was queued"


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
    path, season_id = db_path
    await _seed_two_position_table(path, season_id)
    await _seed_division_with_rounds(path, season_id)
    await enable_amendment_mode(path, season_id)
    await modify_session_points(
        path, season_id, "STD", "FEATURE_RACE", [(2, 30)], **ACTOR
    )

    before = await _season_state(path, season_id)
    reposted: list[tuple] = []
    press = await _approve(
        path, season_id, _bot_recording_reposts(path, reposted, missing=(501, 502))
    )

    assert _reply(press).startswith(
        "❌ Amendment not approved — the points would be out of order:"
    ), _reply(press)
    assert await _nothing_queued(path), "a refused approval was queued"
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
    path, season_id = db_path
    await _seed_two_position_table(path, season_id)
    await _seed_division_with_rounds(path, season_id)
    await enable_amendment_mode(path, season_id)
    await modify_session_points(
        path, season_id, "STD", "FEATURE_RACE", [(2, 30)], **ACTOR
    )

    reposted: list[tuple] = []
    press = await _approve(path, season_id, _bot_recording_reposts(path, reposted))

    assert "out of order" in _reply(press), _reply(press)
    assert await _nothing_queued(path), "a refused approval was queued"
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
    path, season_id = db_path
    await _seed_two_position_table(path, season_id)
    await _seed_division_with_rounds(path, season_id)
    await enable_amendment_mode(path, season_id)
    await modify_session_points(
        path, season_id, "STD", "FEATURE_RACE", [(1, 30)], **ACTOR
    )

    await _approve(path, season_id, _bot_recording_reposts(path, []))

    assert await _season_points(path, season_id) == {1: 30, 2: 18}


@pytest.mark.asyncio
async def test_an_amendment_paying_nothing_below_the_points_still_applies(db_path):
    """Trailing zeros are the ordinary shape of a table, mid-season as at the start."""
    path, season_id = db_path
    await _seed_two_position_table(path, season_id)
    await _seed_division_with_rounds(path, season_id)
    await enable_amendment_mode(path, season_id)
    await modify_session_points(
        path, season_id, "STD", "FEATURE_RACE", [(3, 0)], **ACTOR
    )
    await modify_session_points(
        path, season_id, "STD", "FEATURE_RACE", [(4, 0)], **ACTOR
    )

    await _approve(path, season_id, _bot_recording_reposts(path, []))

    assert await _season_points(path, season_id) == {1: 25, 2: 18, 3: 0, 4: 0}


# ---------------------------------------------------------------------------
# An approved amendment scores every raced session again (#443)
#
# The approval replaced the season's points tables and reposted every round, but scored no
# session again: every repost and every standings carried the points the rounds were first
# scored under. The #130 tests above seed sessions with no points configuration and no
# drivers, which is why they never saw it. Each session below was scored under the old
# table, 25-18-15 with one point for the fastest lap inside the top ten, before the
# amendment is approved.
# ---------------------------------------------------------------------------

_WINNER, _RUNNER_UP, _THIRD = 1001, 1002, 1003

#: The teams every seeded division fields, two drivers to a team as a league runs them:
#: the winner and the runner-up drive for the first, third and the fourth driver for the
#: second, and a fifth driver for the third. ``(shorthand, full name)``.
_TEAMS = (("RBR", "Red Bull Racing"), ("FER", "Ferrari"), ("MCL", "McLaren"))


def _team_of(driver: int) -> int:
    """The index in ``_TEAMS`` of the team *driver* (before any offset) drives for."""
    return (driver - _WINNER) // 2

#: The classification every raced round below records unless a test gives its own: the
#: winner, the runner-up, and third with the quickest lap, each holding what the old table
#: gave them. ``(driver, position, outcome, fastest lap, points, fastest-lap bonus,
#: post-race time penalty in ms)``.
_OLD_CLASSIFICATION = (
    (_WINNER, 1, "CLASSIFIED", "1:31.000", 25, 0, 0),
    (_RUNNER_UP, 2, "CLASSIFIED", "1:31.500", 18, 0, 0),
    (_THIRD, 3, "CLASSIFIED", "1:30.000", 15, 1, 0),
)


async def _seed_points_config(
    path: str,
    season_id: int,
    config: str = "STD",
    *,
    race: dict[int, int] | None = None,
    fastest_lap: tuple[int, int] | None = (1, 10),
    qualifying: dict[int, int] | None = None,
) -> None:
    """Give the season *config*: 25-18-15 for the feature race unless *race* says otherwise,
    ``(points, position limit)`` for its fastest lap, and *qualifying* for feature qualifying."""
    race = {1: 25, 2: 18, 3: 15} if race is None else race
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO season_points_links (season_id, config_name) VALUES (?, ?)",
            (season_id, config),
        )
        for session_type, table in (("FEATURE_RACE", race), ("FEATURE_QUALIFYING", qualifying or {})):
            for position, points in table.items():
                await db.execute(
                    "INSERT INTO season_points_entries "
                    "(season_id, config_name, session_type, position, points) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (season_id, config, session_type, position, points),
                )
        if fastest_lap is not None:
            await db.execute(
                "INSERT INTO season_points_fl "
                "(season_id, config_name, session_type, fl_points, fl_position_limit) "
                "VALUES (?, ?, 'FEATURE_RACE', ?, ?)",
                (season_id, config, *fastest_lap),
            )
        await db.commit()


async def _seed_raced_division(
    path: str,
    season_id: int,
    name: str,
    *,
    channels: tuple[int, int],
    round_statuses: tuple[str, ...] = ("FINAL", "FINAL"),
    division_status: str = "ACTIVE",
    unraced_status: str = "NOT_RUN",
    config: str = "STD",
    round_formats: tuple[str, ...] | None = None,
    round_configs: tuple[str, ...] | None = None,
    classification: tuple = _OLD_CLASSIFICATION,
    qualifying: tuple = (),
    fastest_lap_override: int | None = None,
    driver_offset: int = 0,
):
    """A division whose raced rounds were scored under the old table, and one round to come.

    It fields the teams of ``_TEAMS``, each driver in the team ``_team_of`` names, so that
    no team has more than two drivers in a session.

    *channels* are its ``(results, standings)`` channels. Each status in *round_statuses* is
    a raced round, in order, carrying one feature race under *config* with *classification*,
    and a feature qualifying with *qualifying* ``(driver, position, points)`` where given. The
    round after them has not been raced. *driver_offset* is added to every driver id, so that
    two divisions do not share a driver.

    Every raced round is a Normal one scored under *config*, unless *round_formats* and
    *round_configs* give each raced round, in order, a format and a configuration of its own.

    Returns ``(division_id, raced round ids, race session ids, qualifying session ids,
    unraced round id)``.
    """
    async with get_connection(path) as db:
        cursor = await db.execute(
            "INSERT INTO divisions (season_id, name, mention_role_id, status) "
            "VALUES (?, ?, 777, ?)",
            (season_id, name, division_status),
        )
        division_id = cursor.lastrowid
        await db.execute(
            "INSERT INTO division_results_config "
            "(division_id, results_channel_id, standings_channel_id) VALUES (?, ?, ?)",
            (division_id, *channels),
        )
        team_ids: list[int] = []
        for shorthand, full_name in _TEAMS:
            cursor = await db.execute(
                "INSERT INTO team_instances (division_id, name, full_name) VALUES (?, ?, ?)",
                (division_id, shorthand, full_name),
            )
            team_ids.append(cursor.lastrowid)

        raced: list[int] = []
        race_sessions: list[int] = []
        qualifying_sessions: list[int] = []
        formats = round_formats or ("NORMAL",) * len(round_statuses)
        configs = round_configs or (config,) * len(round_statuses)
        for round_number, (status, round_format, round_config) in enumerate(
            zip(round_statuses, formats, configs, strict=True), start=1
        ):
            cursor = await db.execute(
                "INSERT INTO rounds (division_id, round_number, format, track_name, status, "
                "scheduled_at) VALUES (?, ?, ?, 'Monza', ?, '2026-06-01T18:00:00')",
                (division_id, round_number, round_format, status),
            )
            round_id = cursor.lastrowid
            raced.append(round_id)
            if qualifying:
                cursor = await db.execute(
                    "INSERT INTO session_results "
                    "(round_id, division_id, session_type, status, config_name) "
                    "VALUES (?, ?, 'FEATURE_QUALIFYING', 'ACTIVE', ?)",
                    (round_id, division_id, round_config),
                )
                qualifying_sessions.append(cursor.lastrowid)
                for driver, position, points in qualifying:
                    await db.execute(
                        "INSERT INTO qualifying_session_results (session_result_id, "
                        "driver_user_id, team_instance_id, finishing_position, outcome, "
                        "best_lap, points_awarded) VALUES (?, ?, ?, ?, 'CLASSIFIED', "
                        "'1:29.000', ?)",
                        (
                            cursor.lastrowid, driver + driver_offset,
                            team_ids[_team_of(driver)], position, points,
                        ),
                    )
            cursor = await db.execute(
                "INSERT INTO session_results "
                "(round_id, division_id, session_type, status, config_name, fl_driver_override) "
                "VALUES (?, ?, 'FEATURE_RACE', 'ACTIVE', ?, ?)",
                (
                    round_id,
                    division_id,
                    round_config,
                    None if fastest_lap_override is None else fastest_lap_override + driver_offset,
                ),
            )
            session_id = cursor.lastrowid
            race_sessions.append(session_id)
            for driver, position, outcome, lap, points, bonus, penalty_ms in classification:
                await db.execute(
                    "INSERT INTO race_session_results (session_result_id, driver_user_id, "
                    "team_instance_id, finishing_position, outcome, fastest_lap, "
                    "points_awarded, fastest_lap_bonus, postrace_time_penalties_ms) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        session_id, driver + driver_offset, team_ids[_team_of(driver)],
                        position, outcome, lap, points, bonus, penalty_ms,
                    ),
                )
        cursor = await db.execute(
            "INSERT INTO rounds (division_id, round_number, format, track_name, status, "
            "scheduled_at) VALUES (?, ?, 'NORMAL', 'Spa', ?, '2026-09-01T18:00:00')",
            (division_id, len(round_statuses) + 1, unraced_status),
        )
        unraced_round_id = cursor.lastrowid
        await db.commit()
    return division_id, raced, race_sessions, qualifying_sessions, unraced_round_id


async def _race_points(path: str, session_id: int) -> dict[int, tuple[int, int]]:
    """Each driver's stored ``(points, fastest-lap bonus)`` in a race session."""
    async with get_connection(path) as db:
        cursor = await db.execute(
            "SELECT driver_user_id, points_awarded, fastest_lap_bonus "
            "FROM race_session_results WHERE session_result_id = ?",
            (session_id,),
        )
        return {
            r["driver_user_id"]: (r["points_awarded"], r["fastest_lap_bonus"])
            for r in await cursor.fetchall()
        }


def _posts(reposted: list[tuple], channel_id: int, round_number: int) -> list[str]:
    """What was posted to *channel_id* under the heading of *round_number*."""
    return [
        content
        for posted_to, content in reposted
        if posted_to == channel_id and f"Round {round_number} " in content
    ]


def _scoring_bot(path: str, reposted: list[tuple]):
    """The repost double above, for sessions that carry drivers.

    These sessions carry drivers, which the #130 double never had to name. Naming one reads
    the database through the bot, and asks the server for them, which answers as Discord
    does for somebody who is not a member.
    """
    return _bot_recording_reposts(path, reposted)


async def _approve_raised_win(path: str, season_id: int, reposted: list[tuple] | None = None):
    """Stage a win worth 30 rather than 25, and approve it."""
    await enable_amendment_mode(path, season_id)
    await modify_session_points(
        path, season_id, "STD", "FEATURE_RACE", [(1, 30)], **ACTOR
    )
    await _approve(path, season_id, _scoring_bot(path, [] if reposted is None else reposted))


async def test_an_approved_amendment_rescores_every_raced_session(db_path):
    path, season_id = db_path
    await _seed_points_config(path, season_id)
    _division, _raced, sessions, _q, _unraced = await _seed_raced_division(
        path, season_id, "Alpha", channels=(501, 502)
    )

    await _approve_raised_win(path, season_id)

    for session_id in sessions:
        points = await _race_points(path, session_id)
        assert points[_WINNER] == (30, 0), points
        assert points[_RUNNER_UP] == (18, 0), points


async def test_an_approved_amendment_moves_the_standings(db_path):
    path, season_id = db_path
    await _seed_points_config(path, season_id)
    division_id, raced, _s, _q, _unraced = await _seed_raced_division(
        path, season_id, "Alpha", channels=(501, 502)
    )

    await _approve_raised_win(path, season_id)

    async with get_connection(path) as db:
        cursor = await db.execute(
            "SELECT total_points FROM driver_standings_snapshots "
            "WHERE round_id = ? AND division_id = ? AND driver_user_id = ?",
            (raced[-1], division_id, _WINNER),
        )
        row = await cursor.fetchone()
    assert row is not None and row["total_points"] == 60, "the standings kept the old points"


async def test_an_approved_amendment_reposts_every_standings_with_the_new_points(db_path):
    """What the owner asked for at Gate 1: every standings, of every division, reposted with
    the new totals, and none for a round not yet raced.

    Each post carries both championships. The winner and the runner-up drive for Red Bull
    Racing, which the old table gave 43 a round and the new one 48.
    """
    path, season_id = db_path
    await _seed_points_config(path, season_id)
    await _seed_raced_division(path, season_id, "Alpha", channels=(501, 502))
    await _seed_raced_division(
        path, season_id, "Beta", channels=(511, 512), driver_offset=1000
    )

    reposted: list[tuple] = []
    await _approve_raised_win(path, season_id, reposted)

    for standings_channel, winner in ((502, _WINNER), (512, _WINNER + 1000)):
        for round_number, total, team_total in ((1, 30, 48), (2, 60, 96)):
            posts = _posts(reposted, standings_channel, round_number)
            assert posts, f"round {round_number} standings were not reposted to {standings_channel}"
            assert f"<@{winner}> — **{total} pts**" in posts[-1], posts[-1]
            assert f"1. Red Bull Racing — **{team_total} pts**" in posts[-1], posts[-1]
        assert not _posts(reposted, standings_channel, 3), (
            "standings were posted for a round that has not been raced"
        )


async def test_an_approved_amendment_reposts_every_results_table_with_the_new_points(db_path):
    path, season_id = db_path
    await _seed_points_config(path, season_id)
    await _seed_raced_division(path, season_id, "Alpha", channels=(501, 502))
    await _seed_raced_division(
        path, season_id, "Beta", channels=(511, 512), driver_offset=1000
    )

    reposted: list[tuple] = []
    await _approve_raised_win(path, season_id, reposted)

    for results_channel, winner in ((501, _WINNER), (511, _WINNER + 1000)):
        for round_number in (1, 2):
            posts = _posts(reposted, results_channel, round_number)
            assert posts, f"round {round_number} results were not reposted to {results_channel}"
            winner_line = next(
                line for line in posts[-1].splitlines() if line.startswith(f"**1.** <@{winner}>")
            )
            assert winner_line.endswith("**30 pts**"), winner_line


async def test_an_approved_fastest_lap_amendment_rescores_the_bonus(db_path):
    from leaguebot.core.services.amendment_service import modify_fl_bonus

    path, season_id = db_path
    await _seed_points_config(path, season_id)
    _division, _raced, sessions, _q, _unraced = await _seed_raced_division(
        path, season_id, "Alpha", channels=(501, 502)
    )
    await enable_amendment_mode(path, season_id)
    await modify_fl_bonus(path, season_id, "STD", "FEATURE_RACE", 3)

    await _approve(path, season_id, _scoring_bot(path, []))

    for session_id in sessions:
        assert (await _race_points(path, session_id))[_THIRD] == (15, 3)


async def test_rescoring_keeps_the_fastest_lap_override(db_path):
    """The runner-up was given the fastest lap by hand; the rescoring must not hand it back
    to the quickest time."""
    from leaguebot.core.services.amendment_service import modify_fl_bonus

    path, season_id = db_path
    await _seed_points_config(path, season_id)
    _division, _raced, sessions, _q, _unraced = await _seed_raced_division(
        path, season_id, "Alpha", channels=(501, 502),
        classification=(
            (_WINNER, 1, "CLASSIFIED", "1:31.000", 25, 0, 0),
            (_RUNNER_UP, 2, "CLASSIFIED", "1:31.500", 18, 1, 0),
            (_THIRD, 3, "CLASSIFIED", "1:30.000", 15, 0, 0),
        ),
        fastest_lap_override=_RUNNER_UP,
    )
    await enable_amendment_mode(path, season_id)
    await modify_fl_bonus(path, season_id, "STD", "FEATURE_RACE", 3)

    await _approve(path, season_id, _scoring_bot(path, []))

    for session_id in sessions:
        points = await _race_points(path, session_id)
        assert points[_RUNNER_UP] == (18, 3), points
        assert points[_THIRD] == (15, 0), points


async def test_an_approved_position_limit_moves_the_fastest_lap_bonus(db_path):
    """Third set the quickest lap; with the bonus now limited to the top two, nobody holds it."""
    from leaguebot.core.services.amendment_service import modify_fl_position_limit

    path, season_id = db_path
    await _seed_points_config(path, season_id)
    _division, _raced, sessions, _q, _unraced = await _seed_raced_division(
        path, season_id, "Alpha", channels=(501, 502)
    )
    await enable_amendment_mode(path, season_id)
    await modify_fl_position_limit(path, season_id, "STD", "FEATURE_RACE", 2)

    await _approve(path, season_id, _scoring_bot(path, []))

    for session_id in sessions:
        points = await _race_points(path, session_id)
        assert points[_THIRD] == (15, 0), points
        assert all(bonus == 0 for _points, bonus in points.values()), points


async def test_an_approved_amendment_rescores_qualifying(db_path):
    path, season_id = db_path
    await _seed_points_config(path, season_id, qualifying={1: 3})
    _division, _raced, _s, qualifying_sessions, _unraced = await _seed_raced_division(
        path, season_id, "Alpha", channels=(501, 502),
        qualifying=((_WINNER, 1, 3), (_RUNNER_UP, 2, 0)),
    )
    await enable_amendment_mode(path, season_id)
    await modify_session_points(
        path, season_id, "STD", "FEATURE_QUALIFYING", [(1, 5)], **ACTOR
    )

    await _approve(path, season_id, _scoring_bot(path, []))

    async with get_connection(path) as db:
        for session_id in qualifying_sessions:
            cursor = await db.execute(
                "SELECT points_awarded FROM qualifying_session_results "
                "WHERE session_result_id = ? AND driver_user_id = ?",
                (session_id, _WINNER),
            )
            assert (await cursor.fetchone())["points_awarded"] == 5


async def test_a_session_under_another_configuration_keeps_its_points(db_path):
    """Beta races under ALT, 12-8-6 with no fastest-lap bonus; amending STD leaves it alone."""
    path, season_id = db_path
    await _seed_points_config(path, season_id)
    await _seed_points_config(path, season_id, "ALT", race={1: 12, 2: 8, 3: 6}, fastest_lap=None)
    await _seed_raced_division(path, season_id, "Alpha", channels=(501, 502))
    _division, _raced, alt_sessions, _q, _unraced = await _seed_raced_division(
        path, season_id, "Beta", channels=(511, 512), config="ALT", driver_offset=1000,
        classification=(
            (_WINNER, 1, "CLASSIFIED", "1:31.000", 12, 0, 0),
            (_RUNNER_UP, 2, "CLASSIFIED", "1:31.500", 8, 0, 0),
            (_THIRD, 3, "CLASSIFIED", "1:30.000", 6, 0, 0),
        ),
    )

    await _approve_raised_win(path, season_id)

    for session_id in alt_sessions:
        assert await _race_points(path, session_id) == {
            _WINNER + 1000: (12, 0),
            _RUNNER_UP + 1000: (8, 0),
            _THIRD + 1000: (6, 0),
        }


async def test_a_round_under_an_unchanged_configuration_keeps_its_points_and_is_reposted(db_path):
    """What the owner asked for at Gate 2: two rounds of different formats, each scored under
    a configuration of its own, both paying 25 for a win.

    Round 1 is a Normal round under STD, round 2 an Endurance round under ENDURO. Only STD's
    win is raised to 30: round 1 is scored again, round 2 keeps its points, and round 2 is
    reposted all the same, its results table and its standings both.
    """
    path, season_id = db_path
    await _seed_points_config(path, season_id)
    await _seed_points_config(path, season_id, "ENDURO")
    _division, _raced, sessions, _q, _unraced = await _seed_raced_division(
        path, season_id, "Alpha", channels=(501, 502),
        round_formats=("NORMAL", "ENDURANCE"), round_configs=("STD", "ENDURO"),
    )

    reposted: list[tuple] = []
    await _approve_raised_win(path, season_id, reposted)

    assert (await _race_points(path, sessions[0]))[_WINNER] == (30, 0), (
        "the round under the amended configuration kept its old points"
    )
    assert await _race_points(path, sessions[1]) == {
        _WINNER: (25, 0),
        _RUNNER_UP: (18, 0),
        _THIRD: (15, 1),
    }, "the round under the unchanged configuration was scored under the amended one"

    results = _posts(reposted, 501, 2)
    assert results, "round 2's results were not reposted"
    winner_line = next(
        line for line in results[-1].splitlines() if line.startswith(f"**1.** <@{_WINNER}>")
    )
    assert winner_line.endswith("**25 pts**"), winner_line
    standings = _posts(reposted, 502, 2)
    assert standings, "round 2's standings were not reposted"
    assert f"<@{_WINNER}> — **55 pts**" in standings[-1], standings[-1]


async def test_rescoring_keeps_the_sanctions(db_path):
    """The winner and the runner-up are paid the new table; the penalties stand.

    The runner-up was given a time penalty and is stored at the second place it left them
    in; third did not finish, fourth was disqualified having set the quickest lap, and
    fifth did not start.
    """
    path, season_id = db_path
    await _seed_points_config(path, season_id, race={1: 25, 2: 18, 3: 15, 4: 12, 5: 10})
    _division, _raced, sessions, _q, _unraced = await _seed_raced_division(
        path, season_id, "Alpha", channels=(501, 502), round_statuses=("FINAL",),
        classification=(
            (_WINNER, 1, "CLASSIFIED", "1:31.000", 25, 0, 0),
            (_RUNNER_UP, 2, "CLASSIFIED", "1:31.500", 18, 0, 5000),
            (_THIRD, 3, "DNF", "1:32.000", 0, 0, 0),
            (1004, 4, "DSQ", "1:29.000", 0, 0, 0),
            (1005, 5, "DNS", None, 0, 0, 0),
        ),
    )
    await enable_amendment_mode(path, season_id)
    await modify_session_points(
        path, season_id, "STD", "FEATURE_RACE", [(1, 30)], **ACTOR
    )
    await modify_session_points(
        path, season_id, "STD", "FEATURE_RACE", [(2, 20)], **ACTOR
    )

    await _approve(path, season_id, _scoring_bot(path, []))

    points = await _race_points(path, sessions[0])
    assert points[_WINNER][0] == 30, points
    assert points[_RUNNER_UP][0] == 20, "the time-penalised driver was not scored at their place"
    assert points[_THIRD][0] == 0, "a driver who did not finish was paid for their place"
    assert points[1004] == (0, 0), "a disqualified driver was paid"
    assert points[1005] == (0, 0), "a driver who did not start was paid"


async def test_a_round_in_review_is_rescored_and_reposted_under_its_own_label(db_path):
    path, season_id = db_path
    await _seed_points_config(path, season_id)
    _division, _raced, sessions, _q, _unraced = await _seed_raced_division(
        path, season_id, "Alpha", channels=(501, 502),
        round_statuses=("FINAL", "AWAITING_REPORT_VERDICTS"),
    )

    reposted: list[tuple] = []
    await _approve_raised_win(path, season_id, reposted)

    assert (await _race_points(path, sessions[1]))[_WINNER] == (30, 0)
    posts = _posts(reposted, 501, 2)
    assert posts, "the round in review was not reposted"
    assert "Provisional Results" in posts[-1], posts[-1]
    assert "**30 pts**" in posts[-1], posts[-1]


async def test_a_cancelled_division_is_rescored(db_path):
    """Beta was cancelled after two rounds; what it raced is scored under the new table too
    (decided 2026-09-26)."""
    path, season_id = db_path
    await _seed_points_config(path, season_id)
    await _seed_raced_division(path, season_id, "Alpha", channels=(501, 502))
    _division, _raced, cancelled_sessions, _q, _unraced = await _seed_raced_division(
        path, season_id, "Beta", channels=(511, 512), driver_offset=1000,
        division_status="CANCELLED", unraced_status="CANCELLED",
    )

    await _approve_raised_win(path, season_id)

    for session_id in cancelled_sessions:
        assert (await _race_points(path, session_id))[_WINNER + 1000] == (30, 0)


async def test_a_finished_division_is_rescored_while_another_races(db_path):
    """Beta finished its season, its last round cancelled, while Alpha still has a round to
    race; what Beta raced is scored under the new table too."""
    path, season_id = db_path
    await _seed_points_config(path, season_id)
    await _seed_raced_division(path, season_id, "Alpha", channels=(501, 502))
    _division, _raced, finished_sessions, _q, _unraced = await _seed_raced_division(
        path, season_id, "Beta", channels=(511, 512), driver_offset=1000,
        division_status="FINISHED", unraced_status="CANCELLED",
    )

    await _approve_raised_win(path, season_id)

    for session_id in finished_sessions:
        assert (await _race_points(path, session_id))[_WINNER + 1000] == (30, 0)


async def test_rescoring_leaves_another_season_alone(db_path):
    """A completed season keeps the points it was scored with.

    Its own STD table now pays 20 for a win while its winners hold 25, so a rescoring that
    reaches that season at all moves them, whichever season's table it scores them under.
    """
    path, season_id = db_path
    async with get_connection(path) as db:
        cursor = await db.execute(
            "INSERT INTO seasons (start_date, status, season_number) "
            "VALUES ('2025-01-01', 'COMPLETED', 0)"
        )
        other_season_id = cursor.lastrowid
        await db.commit()
    await _seed_points_config(path, other_season_id, race={1: 20, 2: 18, 3: 15})
    _division, _raced, other_sessions, _q, _unraced = await _seed_raced_division(
        path, other_season_id, "Alpha", channels=(531, 532), driver_offset=2000
    )
    await _seed_points_config(path, season_id)
    await _seed_raced_division(path, season_id, "Alpha", channels=(501, 502))

    await _approve_raised_win(path, season_id)

    for session_id in other_sessions:
        assert (await _race_points(path, session_id))[_WINNER + 2000] == (25, 0)


async def test_a_rescore_that_fails_changes_nothing(db_path, monkeypatch):
    """Scoring the second session raises: the approval's save fails whole and the queue stops
    at it, and the season, the staged changes and the first session's points are exactly as
    they stood. Nothing is reposted, and the log records no success."""
    import sqlite3

    from leaguebot.results.services import result_submission_service

    path, season_id = db_path
    await _seed_points_config(path, season_id)
    _division, _raced, sessions, _q, _unraced = await _seed_raced_division(
        path, season_id, "Alpha", channels=(501, 502)
    )
    await _staged_amendment(path, season_id)

    real_scoring = result_submission_service._apply_points_in_tx
    calls: list[int] = []

    async def failing_on_the_second_session(db, session_result_id, *args, **kwargs):
        calls.append(session_result_id)
        if len(calls) == 2:
            raise sqlite3.OperationalError("database is locked")
        return await real_scoring(db, session_result_id, *args, **kwargs)

    monkeypatch.setattr(
        result_submission_service, "_apply_points_in_tx", failing_on_the_second_session
    )

    reposted: list[tuple] = []
    bot = _scoring_bot(path, reposted)
    await _approve(path, season_id, bot)

    from tests.support.change_queue import stopped_job

    stopped = await stopped_job(path)
    assert stopped is not None and stopped["name"] == "apply", (
        f"the queue did not stop at the approval's save: {stopped}"
    )
    assert reposted == [], "a failed approval reposted"
    logged = "\n".join(bot.log_channel.sent)
    assert "| Success" not in logged, "a failed approval was logged as a success"
    points, staged, state = await _season_state(path, season_id)
    assert points == [(1, 25), (2, 18), (3, 15)], "the season's table moved"
    assert staged == [(1, 30), (2, 18), (3, 15)], "the staged changes were lost"
    assert state is not None and state.amendment_active and state.modified_flag
    for session_id in sessions:
        assert (await _race_points(path, session_id))[_WINNER] == (25, 0), (
            "a failed approval kept part of its rescoring"
        )


# ---------------------------------------------------------------------------
# modify_session_points stages a whole paste at once (#442)
# ---------------------------------------------------------------------------


async def test_modify_session_points_stages_every_pair_in_one_transaction(db_path):
    """Every pair is staged, the store is marked modified, and each staged change is recorded
    as an audit entry, by whom and when."""
    path, season_id = db_path
    await _seed_season_points(path, season_id)
    await enable_amendment_mode(path, season_id)

    await modify_session_points(
        path, season_id, "STD", "FEATURE_RACE", [(1, 30), (2, 20), (3, 10)], **ACTOR
    )

    async with get_connection(path) as db:
        cursor = await db.execute(
            "SELECT position, points FROM season_modification_entries "
            "WHERE season_id = ? AND session_type = 'FEATURE_RACE' ORDER BY position",
            (season_id,),
        )
        staged = [tuple(r) for r in await cursor.fetchall()]
        cursor = await db.execute(
            "SELECT actor_id, actor_name, timestamp FROM audit_entries ORDER BY id"
        )
        audited = [tuple(r) for r in await cursor.fetchall()]
    assert staged == [(1, 30), (2, 20), (3, 10)]
    state = await get_amendment_state(path, season_id)
    assert state is not None and state.modified_flag
    assert audited == [(99, "Admin#0001", ACTOR["now"].isoformat())] * 3
