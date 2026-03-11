"""Unit tests for DriverService state transitions added in T007 — T032."""

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
    import aiosqlite

    path = str(tmp_path / "driver_test.db")
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        await db.executescript(
            """
            CREATE TABLE server_configs (
                server_id              INTEGER PRIMARY KEY,
                interaction_role_id    INTEGER NOT NULL DEFAULT 0,
                interaction_channel_id INTEGER NOT NULL DEFAULT 0,
                log_channel_id         INTEGER NOT NULL DEFAULT 0,
                test_mode_active       INTEGER NOT NULL DEFAULT 0,
                previous_season_number INTEGER NOT NULL DEFAULT 0,
                weather_module_enabled INTEGER NOT NULL DEFAULT 0,
                signup_module_enabled  INTEGER NOT NULL DEFAULT 0
            );
            INSERT INTO server_configs (server_id) VALUES (1);

            CREATE TABLE driver_profiles (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id        INTEGER NOT NULL,
                discord_user_id  TEXT NOT NULL,
                current_state    TEXT NOT NULL DEFAULT 'NOT_SIGNED_UP',
                former_driver    INTEGER NOT NULL DEFAULT 0,
                race_ban_count   INTEGER NOT NULL DEFAULT 0,
                season_ban_count INTEGER NOT NULL DEFAULT 0,
                league_ban_count INTEGER NOT NULL DEFAULT 0,
                ban_races_remaining INTEGER NOT NULL DEFAULT 0,
                UNIQUE(server_id, discord_user_id)
            );

            CREATE TABLE audit_entries (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id   INTEGER NOT NULL,
                actor_id    INTEGER NOT NULL,
                actor_name  TEXT    NOT NULL,
                division_id INTEGER,
                change_type TEXT    NOT NULL,
                old_value   TEXT    NOT NULL,
                new_value   TEXT    NOT NULL,
                timestamp   TEXT    NOT NULL
            );

            CREATE TABLE seasons (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'SETUP'
            );
            CREATE TABLE driver_season_assignments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                driver_profile_id INTEGER NOT NULL,
                season_id INTEGER NOT NULL,
                division_id INTEGER NOT NULL DEFAULT 0,
                current_position INTEGER NOT NULL DEFAULT 0,
                current_points INTEGER NOT NULL DEFAULT 0,
                points_gap_to_first INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE team_seats (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                team_id          INTEGER NOT NULL,
                seat_number      INTEGER NOT NULL DEFAULT 1,
                driver_profile_id INTEGER
            );

            CREATE TABLE signup_records (
                id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id            INTEGER NOT NULL,
                discord_user_id      TEXT NOT NULL,
                discord_username     TEXT,
                server_display_name  TEXT,
                nationality          TEXT,
                platform             TEXT,
                platform_id          TEXT,
                availability_slot_ids TEXT,
                driver_type          TEXT,
                preferred_teams      TEXT,
                preferred_teammate   TEXT,
                lap_times_json       TEXT,
                notes                TEXT,
                signup_channel_id    INTEGER,
                total_lap_ms         INTEGER,
                created_at           TEXT,
                updated_at           TEXT,
                UNIQUE(server_id, discord_user_id)
            );
            """
        )
    return path


def _make_svc(db_path):
    from services.driver_service import DriverService
    return DriverService(db_path)


async def _seed_driver(db_path, user_id: str, state: str) -> None:
    import aiosqlite
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        await db.execute(
            "INSERT INTO driver_profiles (server_id, discord_user_id, current_state) VALUES (1, ?, ?)",
            (user_id, state),
        )
        await db.commit()


# ---------------------------------------------------------------------------
# PENDING_SIGNUP_COMPLETION → NOT_SIGNED_UP (T007 new transition)
# ---------------------------------------------------------------------------


class TestPendingSignupCompletionToNotSignedUp:
    async def test_transition_succeeds(self, db_path):
        from models.driver_profile import DriverState
        await _seed_driver(db_path, "u1", "PENDING_SIGNUP_COMPLETION")
        svc = _make_svc(db_path)
        result = await svc.transition(1, "u1", DriverState.NOT_SIGNED_UP)
        # Non-former-driver transitions to NOT_SIGNED_UP deletes the profile
        assert result is None

    async def test_former_driver_retains_profile(self, db_path):
        import aiosqlite
        from models.driver_profile import DriverState
        await _seed_driver(db_path, "u2", "PENDING_SIGNUP_COMPLETION")
        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                "UPDATE driver_profiles SET former_driver = 1 WHERE discord_user_id = 'u2'"
            )
            await db.commit()
        svc = _make_svc(db_path)
        result = await svc.transition(1, "u2", DriverState.NOT_SIGNED_UP)
        assert result is not None
        assert result.current_state == DriverState.NOT_SIGNED_UP

    async def test_transition_to_pending_admin_still_works(self, db_path):
        from models.driver_profile import DriverState
        await _seed_driver(db_path, "u3", "PENDING_SIGNUP_COMPLETION")
        svc = _make_svc(db_path)
        result = await svc.transition(1, "u3", DriverState.PENDING_ADMIN_APPROVAL)
        assert result is not None
        assert result.current_state == DriverState.PENDING_ADMIN_APPROVAL


# ---------------------------------------------------------------------------
# PENDING_DRIVER_CORRECTION → NOT_SIGNED_UP (T007 new transition)
# ---------------------------------------------------------------------------


class TestPendingDriverCorrectionToNotSignedUp:
    async def test_transition_succeeds(self, db_path):
        from models.driver_profile import DriverState
        await _seed_driver(db_path, "u4", "PENDING_DRIVER_CORRECTION")
        svc = _make_svc(db_path)
        result = await svc.transition(1, "u4", DriverState.NOT_SIGNED_UP)
        assert result is None  # non-former-driver deleted

    async def test_existing_transitions_still_work(self, db_path):
        from models.driver_profile import DriverState
        await _seed_driver(db_path, "u5", "PENDING_DRIVER_CORRECTION")
        svc = _make_svc(db_path)
        result = await svc.transition(1, "u5", DriverState.PENDING_ADMIN_APPROVAL)
        assert result is not None
        assert result.current_state == DriverState.PENDING_ADMIN_APPROVAL

    async def test_invalid_transition_still_raises(self, db_path):
        from models.driver_profile import DriverState
        await _seed_driver(db_path, "u6", "PENDING_DRIVER_CORRECTION")
        svc = _make_svc(db_path)
        with pytest.raises(ValueError):
            # Cannot go directly to ASSIGNED from PENDING_DRIVER_CORRECTION
            await svc.transition(1, "u6", DriverState.ASSIGNED)


# ---------------------------------------------------------------------------
# PENDING_ADMIN_APPROVAL → NOT_SIGNED_UP (pre-existing from 012, verify present)
# ---------------------------------------------------------------------------


class TestPendingAdminApprovalToNotSignedUp:
    async def test_transition_succeeds(self, db_path):
        from models.driver_profile import DriverState
        await _seed_driver(db_path, "u7", "PENDING_ADMIN_APPROVAL")
        svc = _make_svc(db_path)
        result = await svc.transition(1, "u7", DriverState.NOT_SIGNED_UP)
        assert result is None  # non-former-driver deleted


# ---------------------------------------------------------------------------
# T052 — AWAITING_CORRECTION_PARAMETER (ACP) transition paths
# ---------------------------------------------------------------------------


class TestAwaitingCorrectionParameterTransitions:
    """T052: New ACP state added in spec 014."""

    async def test_paa_to_acp(self, db_path):
        from models.driver_profile import DriverState
        await _seed_driver(db_path, "acp1", "PENDING_ADMIN_APPROVAL")
        svc = _make_svc(db_path)
        result = await svc.transition(1, "acp1", DriverState.AWAITING_CORRECTION_PARAMETER)
        assert result is not None
        assert result.current_state == DriverState.AWAITING_CORRECTION_PARAMETER

    async def test_acp_to_pdc(self, db_path):
        from models.driver_profile import DriverState
        await _seed_driver(db_path, "acp2", "AWAITING_CORRECTION_PARAMETER")
        svc = _make_svc(db_path)
        result = await svc.transition(1, "acp2", DriverState.PENDING_DRIVER_CORRECTION)
        assert result is not None
        assert result.current_state == DriverState.PENDING_DRIVER_CORRECTION

    async def test_acp_to_paa_timeout_revert(self, db_path):
        from models.driver_profile import DriverState
        await _seed_driver(db_path, "acp3", "AWAITING_CORRECTION_PARAMETER")
        svc = _make_svc(db_path)
        result = await svc.transition(1, "acp3", DriverState.PENDING_ADMIN_APPROVAL)
        assert result is not None
        assert result.current_state == DriverState.PENDING_ADMIN_APPROVAL

    async def test_pdc_to_paa(self, db_path):
        from models.driver_profile import DriverState
        await _seed_driver(db_path, "acp4", "PENDING_DRIVER_CORRECTION")
        svc = _make_svc(db_path)
        result = await svc.transition(1, "acp4", DriverState.PENDING_ADMIN_APPROVAL)
        assert result is not None
        assert result.current_state == DriverState.PENDING_ADMIN_APPROVAL

    async def test_psc_cannot_go_to_acp_directly(self, db_path):
        """ACP is only reachable from PAA, not from PSC."""
        from models.driver_profile import DriverState
        await _seed_driver(db_path, "acp5", "PENDING_SIGNUP_COMPLETION")
        svc = _make_svc(db_path)
        with pytest.raises(ValueError):
            await svc.transition(1, "acp5", DriverState.AWAITING_CORRECTION_PARAMETER)

    async def test_acp_to_not_signed_up(self, db_path):
        """ACP can transition to NOT_SIGNED_UP (withdrawal/forced-close)."""
        from models.driver_profile import DriverState
        await _seed_driver(db_path, "acp6", "AWAITING_CORRECTION_PARAMETER")
        svc = _make_svc(db_path)
        result = await svc.transition(1, "acp6", DriverState.NOT_SIGNED_UP)
        assert result is None  # non-former-driver deleted


# ---------------------------------------------------------------------------
# T052 — NOT_SIGNED_UP signup-data clearing (former_driver paths)
# ---------------------------------------------------------------------------


@pytest.fixture
async def db_with_signup(tmp_path):
    """DB fixture that includes signup_records table for clearing tests."""
    import aiosqlite
    path = str(tmp_path / "driver_signup_test.db")
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        await db.executescript(
            """
            CREATE TABLE server_configs (
                server_id              INTEGER PRIMARY KEY,
                interaction_role_id    INTEGER NOT NULL DEFAULT 0,
                interaction_channel_id INTEGER NOT NULL DEFAULT 0,
                log_channel_id         INTEGER NOT NULL DEFAULT 0,
                test_mode_active       INTEGER NOT NULL DEFAULT 0,
                previous_season_number INTEGER NOT NULL DEFAULT 0,
                weather_module_enabled INTEGER NOT NULL DEFAULT 0,
                signup_module_enabled  INTEGER NOT NULL DEFAULT 0
            );
            INSERT INTO server_configs (server_id) VALUES (1);

            CREATE TABLE driver_profiles (
                id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id          INTEGER NOT NULL,
                discord_user_id    TEXT NOT NULL,
                current_state      TEXT NOT NULL DEFAULT 'NOT_SIGNED_UP',
                former_driver      INTEGER NOT NULL DEFAULT 0,
                race_ban_count     INTEGER NOT NULL DEFAULT 0,
                season_ban_count   INTEGER NOT NULL DEFAULT 0,
                league_ban_count   INTEGER NOT NULL DEFAULT 0,
                ban_races_remaining INTEGER NOT NULL DEFAULT 0,
                UNIQUE(server_id, discord_user_id)
            );

            CREATE TABLE audit_entries (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id   INTEGER NOT NULL,
                actor_id    INTEGER NOT NULL,
                actor_name  TEXT    NOT NULL,
                division_id INTEGER,
                change_type TEXT    NOT NULL,
                old_value   TEXT    NOT NULL,
                new_value   TEXT    NOT NULL,
                timestamp   TEXT    NOT NULL
            );

            CREATE TABLE seasons (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'SETUP'
            );
            CREATE TABLE driver_season_assignments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                driver_profile_id INTEGER NOT NULL,
                season_id         INTEGER NOT NULL,
                division_id       INTEGER NOT NULL DEFAULT 0,
                current_position  INTEGER NOT NULL DEFAULT 0,
                current_points    INTEGER NOT NULL DEFAULT 0,
                points_gap_to_first INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE team_seats (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                team_id           INTEGER NOT NULL,
                seat_number       INTEGER NOT NULL DEFAULT 1,
                driver_profile_id INTEGER
            );

            CREATE TABLE signup_records (
                id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id            INTEGER NOT NULL,
                discord_user_id      TEXT NOT NULL,
                discord_username     TEXT,
                server_display_name  TEXT,
                nationality          TEXT,
                platform             TEXT,
                platform_id          TEXT,
                availability_slot_ids TEXT,
                driver_type          TEXT,
                preferred_teams      TEXT,
                preferred_teammate   TEXT,
                lap_times_json       TEXT,
                notes                TEXT,
                signup_channel_id    INTEGER,
                total_lap_ms         INTEGER,
                created_at           TEXT,
                updated_at           TEXT,
                UNIQUE(server_id, discord_user_id)
            );
            """
        )
        await db.commit()
    return path


class TestSignupDataClearing:
    """T052: NOT_SIGNED_UP transition clears signup data for former drivers."""

    async def _seed_former_driver_with_record(self, db_path: str, user_id: str) -> None:
        import aiosqlite
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute(
                "INSERT INTO driver_profiles "
                "(server_id, discord_user_id, current_state, former_driver) "
                "VALUES (1, ?, 'PENDING_ADMIN_APPROVAL', 1)",
                (user_id,),
            )
            await db.execute(
                "INSERT INTO signup_records "
                "(server_id, discord_user_id, discord_username, platform, platform_id) "
                "VALUES (1, ?, 'TestUser', 'Steam', 'SteamUser123')",
                (user_id,),
            )
            await db.commit()

    async def test_former_driver_nsu_nulls_signup_fields(self, db_with_signup):
        """former_driver=True: NOT_SIGNED_UP nulls all signup data but retains channel."""
        import aiosqlite
        from models.driver_profile import DriverState
        await self._seed_former_driver_with_record(db_with_signup, "fd1")
        svc = _make_svc(db_with_signup)
        result = await svc.transition(1, "fd1", DriverState.NOT_SIGNED_UP)
        assert result is not None  # former driver profile retained
        assert result.current_state == DriverState.NOT_SIGNED_UP
        # Verify signup_records fields were nulled
        async with aiosqlite.connect(db_with_signup) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT discord_username, platform, platform_id FROM signup_records "
                "WHERE server_id = 1 AND discord_user_id = 'fd1'"
            )
            row = await cur.fetchone()
        assert row is not None  # record still exists
        assert row["discord_username"] is None
        assert row["platform"] is None
        assert row["platform_id"] is None

    async def test_non_former_driver_nsu_deletes_profile(self, db_with_signup):
        """former_driver=False: NOT_SIGNED_UP deletes the driver profile."""
        from models.driver_profile import DriverState
        import aiosqlite
        async with aiosqlite.connect(db_with_signup) as db:
            db.row_factory = aiosqlite.Row
            await db.execute(
                "INSERT INTO driver_profiles "
                "(server_id, discord_user_id, current_state, former_driver) "
                "VALUES (1, 'nfd1', 'PENDING_ADMIN_APPROVAL', 0)"
            )
            await db.commit()
        svc = _make_svc(db_with_signup)
        result = await svc.transition(1, "nfd1", DriverState.NOT_SIGNED_UP)
        assert result is None  # profile deleted

