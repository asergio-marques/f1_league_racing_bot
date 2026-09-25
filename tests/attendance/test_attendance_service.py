"""Unit tests for AttendanceService — T006, T012, T018, T024."""
from __future__ import annotations

import sys
import os

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def db_path(tmp_path):
    """A migrated database: the league's server_configs row, one season, and its divisions
    10 and 11. No attendance_config row, which is how a league that has never enabled the
    module stands."""
    from leaguebot.core.db.database import get_connection, run_migrations

    path = str(tmp_path / "test.db")
    await run_migrations(path)
    async with get_connection(path) as db:
        await db.execute("INSERT INTO server_configs (server_id) VALUES (1)")
        await db.execute(
            "INSERT INTO seasons (id, start_date, status) VALUES (1, '2026-01-01', 'SETUP')"
        )
        await db.executemany(
            "INSERT INTO divisions (id, season_id, name, mention_role_id) VALUES (?, 1, ?, ?)",
            [(10, "Pro", 3010), (11, "Am", 3011)],
        )
        await db.commit()
    return path


# ---------------------------------------------------------------------------
# T006 — Lifecycle tests (enable/disable)
#
# Enabling and disabling are driven through the cog in test_module_enable_disable.py and
# test_module_disable_paths.py; what is left here is the service's own part.
# ---------------------------------------------------------------------------


class TestIsAttendanceEnabledFalseByDefault:
    async def test_returns_false_when_no_row(self, db_path):
        from leaguebot.attendance.services.attendance_service import AttendanceService
        svc = AttendanceService(db_path)
        result = await svc.get_config()
        assert result is None

    async def test_module_enabled_false_after_get_or_create(self, db_path):
        from leaguebot.attendance.services.attendance_service import AttendanceService
        svc = AttendanceService(db_path)
        cfg = await svc.get_or_create_config()
        assert cfg.module_enabled is False


class TestDisableDeletesDivisionConfigs:
    async def test_disable_deletes_division_configs(self, db_path):
        import aiosqlite as _aio
        async with _aio.connect(db_path) as db:
            db.row_factory = _aio.Row
            await db.execute(
                "INSERT OR REPLACE INTO attendance_config (id, module_enabled) VALUES (?, 1)",
                (1,),
            )
            await db.execute(
                "INSERT INTO attendance_division_config (division_id) VALUES (10)"
            )
            await db.commit()

        from leaguebot.attendance.services.attendance_service import AttendanceService
        svc = AttendanceService(db_path)
        await svc.delete_division_configs()

        div_cfg = await svc.get_division_config(10)
        assert div_cfg is None


# ---------------------------------------------------------------------------
# T012 — Division config tests
# ---------------------------------------------------------------------------


class TestGetDivisionConfigNoneBeforeCreate:
    async def test_get_config_none_before_create(self, db_path):
        from leaguebot.attendance.services.attendance_service import AttendanceService
        svc = AttendanceService(db_path)
        result = await svc.get_division_config(10)
        assert result is None


class TestSetRsvpChannel:
    async def test_set_rsvp_channel(self, db_path):
        from leaguebot.attendance.services.attendance_service import AttendanceService
        svc = AttendanceService(db_path)
        await svc.set_rsvp_channel(10, 999)
        cfg = await svc.get_division_config(10)
        assert cfg is not None
        assert cfg.rsvp_channel_id == "999"
        assert cfg.attendance_channel_id is None


class TestSetAttendanceChannel:
    async def test_set_attendance_channel(self, db_path):
        from leaguebot.attendance.services.attendance_service import AttendanceService
        svc = AttendanceService(db_path)
        await svc.set_attendance_channel(10, 888)
        cfg = await svc.get_division_config(10)
        assert cfg is not None
        assert cfg.attendance_channel_id == "888"
        assert cfg.rsvp_channel_id is None


class TestSetChannelPreservesOtherChannel:
    async def test_set_channel_preserves_other_channel(self, db_path):
        from leaguebot.attendance.services.attendance_service import AttendanceService
        svc = AttendanceService(db_path)
        await svc.set_rsvp_channel(10, 111)
        await svc.set_attendance_channel(10, 222)
        cfg = await svc.get_division_config(10)
        assert cfg is not None
        assert cfg.rsvp_channel_id == "111"
        assert cfg.attendance_channel_id == "222"


# ---------------------------------------------------------------------------
# T018 — Timing invariant tests
# ---------------------------------------------------------------------------


class TestTimingInvariantValid:
    async def test_timing_invariant_valid(self):
        from leaguebot.attendance.services.attendance_service import validate_timing_invariant
        # notice_days=5, 5*24=120 > last_notice=24 > deadline=2 → valid
        result = validate_timing_invariant(5, 24, 2)
        assert result is None


class TestTimingInvariantNoticeTooSmall:
    async def test_timing_invariant_notice_too_small(self):
        from leaguebot.attendance.services.attendance_service import validate_timing_invariant
        # 1*24=24, last_notice_hours=24 → 24 <= 24 → violation
        result = validate_timing_invariant(1, 24, 2)
        assert result is not None
        assert "rsvp_notice_days" in result


class TestTimingInvariantDeadlineExceedsLast:
    async def test_timing_invariant_deadline_exceeds_last(self):
        from leaguebot.attendance.services.attendance_service import validate_timing_invariant
        # notice_days=5, 120>6, but last=6 <= deadline=6 → violation
        result = validate_timing_invariant(5, 6, 6)
        assert result is not None
        assert "rsvp_last_notice_hours" in result


class TestTimingInvariantLastZeroSentinelValid:
    async def test_timing_invariant_last_zero_sentinel_valid(self):
        from leaguebot.attendance.services.attendance_service import validate_timing_invariant
        # last_notice_hours=0 is sentinel (no last-notice ping); deadline check skipped
        result = validate_timing_invariant(5, 0, 2)
        assert result is None


class TestTimingInvariantLastEqualsDeadlineRejected:
    async def test_timing_invariant_last_equals_deadline_rejected(self):
        from leaguebot.attendance.services.attendance_service import validate_timing_invariant
        # notice_days=5 (120h), last=4, deadline=4 → last <= deadline → rejected
        result = validate_timing_invariant(5, 4, 4)
        assert result is not None
        assert "rsvp_last_notice_hours" in result


# ---------------------------------------------------------------------------
# T024 — Penalty / threshold config tests
# ---------------------------------------------------------------------------


class TestConfigPenaltyFieldsUpdate:
    async def test_config_penalty_fields_update(self, db_path):
        import aiosqlite as _aio
        async with _aio.connect(db_path) as db:
            db.row_factory = _aio.Row
            await db.execute(
                "INSERT OR REPLACE INTO attendance_config "
                "(id, module_enabled, rsvp_notice_days, rsvp_last_notice_hours, "
                "rsvp_deadline_hours, no_rsvp_penalty, absent_penalty, no_show_penalty, "
                "autoreserve_threshold, autosack_threshold) "
                "VALUES (?, 1, 5, 24, 2, 1, 1, 1, NULL, NULL)",
                (1,),
            )
            await db.commit()

        from leaguebot.attendance.services.attendance_service import AttendanceService
        svc = AttendanceService(db_path)
        await svc.update_no_rsvp_penalty(3)
        await svc.update_absent_penalty(2)
        await svc.update_no_show_penalty(4)
        cfg = await svc.get_config()
        assert cfg is not None
        assert cfg.no_rsvp_penalty == 3
        assert cfg.absent_penalty == 2
        assert cfg.no_show_penalty == 4


class TestAutosackZeroStoresNull:
    async def test_autosack_zero_stores_null(self, db_path):
        import aiosqlite as _aio
        async with _aio.connect(db_path) as db:
            db.row_factory = _aio.Row
            await db.execute(
                "INSERT OR REPLACE INTO attendance_config (id, module_enabled) VALUES (?, 1)",
                (1,),
            )
            await db.commit()

        from leaguebot.attendance.services.attendance_service import AttendanceService
        svc = AttendanceService(db_path)
        await svc.update_autosack_threshold(None)
        cfg = await svc.get_config()
        assert cfg is not None
        assert cfg.autosack_threshold is None


class TestAutoreserveZeroStoresNull:
    async def test_autoreserve_zero_stores_null(self, db_path):
        import aiosqlite as _aio
        async with _aio.connect(db_path) as db:
            db.row_factory = _aio.Row
            await db.execute(
                "INSERT OR REPLACE INTO attendance_config (id, module_enabled) VALUES (?, 1)",
                (1,),
            )
            await db.commit()

        from leaguebot.attendance.services.attendance_service import AttendanceService
        svc = AttendanceService(db_path)
        await svc.update_autoreserve_threshold(None)
        cfg = await svc.get_config()
        assert cfg is not None
        assert cfg.autoreserve_threshold is None


# ---------------------------------------------------------------------------
# recalculation_faults — the attendance half of an amendment's pre-flight check (#187)
#
# The approval of a mid-season amendment recalculates attendance as part of its cascade.
# A recalculation that could not be posted used to be swallowed under the same false
# success as the reposting, so the channels it needs are established before anything is
# written and the approval is refused entire if they are not there.
# ---------------------------------------------------------------------------

import discord  # noqa: E402
from unittest.mock import AsyncMock, MagicMock  # noqa: E402


async def _seed_attendance_season(
    tmp_path,
    *,
    attendance_channel_id=601,
    penalty_channel_id=602,
    autosack=3,
    autoreserve=None,
):
    """A season with one division, seeded against the real schema.

    Returns ``(db_path, season_id)``.
    """
    from leaguebot.core.db.database import run_migrations, get_connection

    path = str(tmp_path / "recalc_faults.db")
    await run_migrations(path)
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (1, 10, 20, 30)"
        )
        await db.execute(
            "INSERT INTO attendance_config (id, autoreserve_threshold, "
            "autosack_threshold) VALUES (1, ?, ?)",
            (autoreserve, autosack),
        )
        cursor = await db.execute(
            "INSERT INTO seasons (start_date, status, season_number) "
            "VALUES ('2026-01-01', 'ACTIVE', 1)"
        )
        season_id = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO divisions (season_id, name, mention_role_id) "
            "VALUES (?, 'Alpha', 777)",
            (season_id,),
        )
        division_id = cursor.lastrowid
        await db.execute(
            "INSERT INTO attendance_division_config (division_id, "
            "attendance_channel_id) VALUES (?, ?)",
            (division_id, attendance_channel_id),
        )
        await db.execute(
            "INSERT INTO division_results_config (division_id, penalty_channel_id) "
            "VALUES (?, ?)",
            (division_id, penalty_channel_id),
        )
        await db.commit()
    return path, season_id


def _attendance_guild(present=(601, 602)):
    """A guild holding *present* as text channels the bot may post in.

    The bot's own member comes from ``get_member`` rather than ``guild.me``, which is what
    the pre-flight reads (#187).
    """
    guild = MagicMock()
    guild.id = 1
    guild.get_member = lambda _user_id: object()

    def get_channel(channel_id):
        if channel_id not in present:
            return None
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = channel_id
        channel.permissions_for.return_value = MagicMock()
        return channel

    guild.get_channel = get_channel
    return guild


def _plain_bot():
    bot = MagicMock()
    bot.user.id = 4242
    bot.module_service.is_images_enabled = AsyncMock(return_value=False)
    bot.image_config_service.get_toggles = AsyncMock(return_value={})
    return bot


@pytest.mark.asyncio
async def test_recalculation_faults_passes_a_healthy_division(tmp_path):
    from leaguebot.attendance.services.attendance_service import recalculation_faults

    path, season_id = await _seed_attendance_season(tmp_path)

    assert await recalculation_faults(
        path, season_id, _attendance_guild(), _plain_bot()
    ) == []


@pytest.mark.asyncio
async def test_recalculation_faults_names_a_deleted_attendance_channel(tmp_path):
    from leaguebot.attendance.services.attendance_service import recalculation_faults

    path, season_id = await _seed_attendance_season(tmp_path)

    faults = await recalculation_faults(
        path, season_id, _attendance_guild(present=(602,)), _plain_bot()
    )

    assert len(faults) == 1, faults
    assert "attendance channel" in faults[0]
    assert "Alpha" in faults[0]


@pytest.mark.asyncio
async def test_recalculation_faults_names_a_deleted_verdicts_channel(tmp_path):
    """Sanctions are announced as verdicts, so that channel is part of the cascade."""
    from leaguebot.attendance.services.attendance_service import recalculation_faults

    path, season_id = await _seed_attendance_season(tmp_path)

    faults = await recalculation_faults(
        path, season_id, _attendance_guild(present=(601,)), _plain_bot()
    )

    assert len(faults) == 1, faults
    assert "verdicts channel" in faults[0]


@pytest.mark.asyncio
async def test_recalculation_faults_ignores_the_verdicts_channel_without_thresholds(tmp_path):
    """`enforce_attendance_sanctions` returns at once when both are unset, so a league
    using neither must not be refused for a channel it will never post to (#187)."""
    from leaguebot.attendance.services.attendance_service import recalculation_faults

    path, season_id = await _seed_attendance_season(
        tmp_path, autosack=None, autoreserve=None
    )

    faults = await recalculation_faults(
        path, season_id, _attendance_guild(present=(601,)), _plain_bot()
    )

    assert faults == []


@pytest.mark.asyncio
async def test_recalculation_faults_ignores_an_unconfigured_attendance_channel(tmp_path):
    from leaguebot.attendance.services.attendance_service import recalculation_faults

    path, season_id = await _seed_attendance_season(
        tmp_path, attendance_channel_id=None, autosack=None, autoreserve=None
    )

    faults = await recalculation_faults(
        path, season_id, _attendance_guild(present=()), _plain_bot()
    )

    assert faults == []


@pytest.mark.asyncio
async def test_recalculation_faults_says_nothing_about_an_absent_guild(tmp_path):
    """The results half reports that already; saying it twice would have a manager
    repairing one thing from two lines."""
    from leaguebot.attendance.services.attendance_service import recalculation_faults

    path, season_id = await _seed_attendance_season(tmp_path)

    assert await recalculation_faults(path, season_id, None, _plain_bot()) == []


@pytest.mark.asyncio
async def test_recalculation_faults_wants_attach_files_only_with_graphics(tmp_path):
    from leaguebot.attendance.services.attendance_service import recalculation_faults

    path, season_id = await _seed_attendance_season(
        tmp_path, autosack=None, autoreserve=None
    )
    guild = _attendance_guild()
    denied = MagicMock()
    denied.attach_files = False
    guild.get_channel(601).permissions_for.return_value = denied

    def get_channel(channel_id):
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = channel_id
        channel.permissions_for.return_value = denied
        return channel

    guild.get_channel = get_channel

    assert await recalculation_faults(path, season_id, guild, _plain_bot()) == []

    with_graphics = _plain_bot()
    with_graphics.module_service.is_images_enabled = AsyncMock(return_value=True)
    with_graphics.image_config_service.get_toggles = AsyncMock(
        return_value={"attendance": True}
    )
    faults = await recalculation_faults(path, season_id, guild, with_graphics)

    assert len(faults) == 1, faults
    assert "Attach Files" in faults[0]
