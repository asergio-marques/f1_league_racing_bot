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
    """A migrated database with the league's server_configs row."""
    from db.database import get_connection, run_migrations

    path = str(tmp_path / "driver_test.db")
    await run_migrations(path)
    async with get_connection(path) as db:
        await db.execute("INSERT INTO server_configs (server_id) VALUES (1)")
        await db.commit()
    return path


def _make_svc(db_path):
    from services.driver_service import DriverService
    return DriverService(db_path)


async def _seed_driver(db_path, user_id: str, state: str) -> None:
    from db.database import get_connection
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO driver_profiles (discord_user_id, current_state) VALUES (?, ?)",
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
        result = await svc.transition("u1", DriverState.NOT_SIGNED_UP)
        # Nothing is deleted: the driver is pending deletion (issue #220)
        assert result is not None and result.current_state == DriverState.NOT_SIGNED_UP

    async def test_former_driver_retains_profile(self, db_path):
        from db.database import get_connection
        from models.driver_profile import DriverState
        await _seed_driver(db_path, "u2", "PENDING_SIGNUP_COMPLETION")
        async with get_connection(db_path) as db:
            await db.execute(
                "UPDATE driver_profiles SET former_driver = 1 WHERE discord_user_id = 'u2'"
            )
            await db.commit()
        svc = _make_svc(db_path)
        result = await svc.transition("u2", DriverState.NOT_SIGNED_UP)
        assert result is not None
        assert result.current_state == DriverState.NOT_SIGNED_UP

    async def test_transition_to_pending_admin_still_works(self, db_path):
        from models.driver_profile import DriverState
        await _seed_driver(db_path, "u3", "PENDING_SIGNUP_COMPLETION")
        svc = _make_svc(db_path)
        result = await svc.transition("u3", DriverState.PENDING_ADMIN_APPROVAL)
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
        result = await svc.transition("u4", DriverState.NOT_SIGNED_UP)
        assert result is not None and result.current_state == DriverState.NOT_SIGNED_UP  # pending deletion (#220)

    async def test_existing_transitions_still_work(self, db_path):
        from models.driver_profile import DriverState
        await _seed_driver(db_path, "u5", "PENDING_DRIVER_CORRECTION")
        svc = _make_svc(db_path)
        result = await svc.transition("u5", DriverState.PENDING_ADMIN_APPROVAL)
        assert result is not None
        assert result.current_state == DriverState.PENDING_ADMIN_APPROVAL

    async def test_invalid_transition_still_raises(self, db_path):
        from models.driver_profile import DriverState
        await _seed_driver(db_path, "u6", "PENDING_DRIVER_CORRECTION")
        svc = _make_svc(db_path)
        with pytest.raises(ValueError):
            # Cannot go directly to ASSIGNED from PENDING_DRIVER_CORRECTION
            await svc.transition("u6", DriverState.ASSIGNED)


# ---------------------------------------------------------------------------
# PENDING_ADMIN_APPROVAL → NOT_SIGNED_UP (pre-existing from 012, verify present)
# ---------------------------------------------------------------------------


class TestPendingAdminApprovalToNotSignedUp:
    async def test_transition_succeeds(self, db_path):
        from models.driver_profile import DriverState
        await _seed_driver(db_path, "u7", "PENDING_ADMIN_APPROVAL")
        svc = _make_svc(db_path)
        result = await svc.transition("u7", DriverState.NOT_SIGNED_UP)
        assert result is not None and result.current_state == DriverState.NOT_SIGNED_UP  # pending deletion (#220)


# ---------------------------------------------------------------------------
# T052 — AWAITING_CORRECTION_PARAMETER (ACP) transition paths
# ---------------------------------------------------------------------------


class TestAwaitingCorrectionParameterTransitions:
    """T052: New ACP state added in spec 014."""

    async def test_paa_to_acp(self, db_path):
        from models.driver_profile import DriverState
        await _seed_driver(db_path, "acp1", "PENDING_ADMIN_APPROVAL")
        svc = _make_svc(db_path)
        result = await svc.transition("acp1", DriverState.AWAITING_CORRECTION_PARAMETER)
        assert result is not None
        assert result.current_state == DriverState.AWAITING_CORRECTION_PARAMETER

    async def test_acp_to_pdc(self, db_path):
        from models.driver_profile import DriverState
        await _seed_driver(db_path, "acp2", "AWAITING_CORRECTION_PARAMETER")
        svc = _make_svc(db_path)
        result = await svc.transition("acp2", DriverState.PENDING_DRIVER_CORRECTION)
        assert result is not None
        assert result.current_state == DriverState.PENDING_DRIVER_CORRECTION

    async def test_acp_to_paa_timeout_revert(self, db_path):
        from models.driver_profile import DriverState
        await _seed_driver(db_path, "acp3", "AWAITING_CORRECTION_PARAMETER")
        svc = _make_svc(db_path)
        result = await svc.transition("acp3", DriverState.PENDING_ADMIN_APPROVAL)
        assert result is not None
        assert result.current_state == DriverState.PENDING_ADMIN_APPROVAL

    async def test_pdc_to_paa(self, db_path):
        from models.driver_profile import DriverState
        await _seed_driver(db_path, "acp4", "PENDING_DRIVER_CORRECTION")
        svc = _make_svc(db_path)
        result = await svc.transition("acp4", DriverState.PENDING_ADMIN_APPROVAL)
        assert result is not None
        assert result.current_state == DriverState.PENDING_ADMIN_APPROVAL

    async def test_psc_cannot_go_to_acp_directly(self, db_path):
        """ACP is only reachable from PAA, not from PSC."""
        from models.driver_profile import DriverState
        await _seed_driver(db_path, "acp5", "PENDING_SIGNUP_COMPLETION")
        svc = _make_svc(db_path)
        with pytest.raises(ValueError):
            await svc.transition("acp5", DriverState.AWAITING_CORRECTION_PARAMETER)

    async def test_acp_to_not_signed_up(self, db_path):
        """ACP can transition to NOT_SIGNED_UP (withdrawal/forced-close)."""
        from models.driver_profile import DriverState
        await _seed_driver(db_path, "acp6", "AWAITING_CORRECTION_PARAMETER")
        svc = _make_svc(db_path)
        result = await svc.transition("acp6", DriverState.NOT_SIGNED_UP)
        assert result is not None and result.current_state == DriverState.NOT_SIGNED_UP  # pending deletion (#220)


# ---------------------------------------------------------------------------
# T052 — NOT_SIGNED_UP signup-data clearing (former_driver paths)
# ---------------------------------------------------------------------------


class TestSignupDataClearing:
    """T052: NOT_SIGNED_UP keeps a former driver's signup data (issue #220 withdrew the clearing)."""

    async def _seed_former_driver_with_record(self, db_path: str, user_id: str) -> None:
        from db.database import get_connection
        async with get_connection(db_path) as db:
            await db.execute(
                "INSERT INTO driver_profiles "
                "(discord_user_id, current_state, former_driver) "
                "VALUES (?, 'PENDING_ADMIN_APPROVAL', 1)",
                (user_id,),
            )
            await db.execute(
                "INSERT INTO signup_records "
                "(discord_user_id, discord_username, platform, platform_id) "
                "VALUES (?, 'TestUser', 'Steam', 'SteamUser123')",
                (user_id,),
            )
            await db.commit()

    async def test_former_driver_nsu_keeps_signup_fields(self, db_path):
        """former_driver=True: NOT_SIGNED_UP keeps the signup, which is season history (#220)."""
        import aiosqlite
        from models.driver_profile import DriverState
        await self._seed_former_driver_with_record(db_path, "fd1")
        svc = _make_svc(db_path)
        result = await svc.transition("fd1", DriverState.NOT_SIGNED_UP)
        assert result is not None  # former driver profile retained
        assert result.current_state == DriverState.NOT_SIGNED_UP
        # The signup is kept whole
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT discord_username, platform, platform_id FROM signup_records "
                "WHERE discord_user_id = 'fd1'"
            )
            row = await cur.fetchone()
        assert row is not None
        assert row["discord_username"] == "TestUser"
        assert row["platform"] == "Steam"
        assert row["platform_id"] == "SteamUser123"

    async def test_non_former_driver_nsu_keeps_profile_pending_deletion(self, db_path):
        """former_driver=False: NOT_SIGNED_UP keeps the profile, pending deletion (#220)."""
        from db.database import get_connection
        from models.driver_profile import DriverState
        async with get_connection(db_path) as db:
            await db.execute(
                "INSERT INTO driver_profiles "
                "(discord_user_id, current_state, former_driver) "
                "VALUES ('nfd1', 'PENDING_ADMIN_APPROVAL', 0)"
            )
            await db.commit()
        svc = _make_svc(db_path)
        result = await svc.transition("nfd1", DriverState.NOT_SIGNED_UP)
        assert result is not None and result.current_state == DriverState.NOT_SIGNED_UP



async def test_a_state_written_within_a_transaction_obeys_the_table(tmp_path):
    """`write_transition` is how a sack or the driver pass changes a state inside a larger
    write, and it refuses what the table refuses, writing nothing."""
    from db.database import get_connection, run_migrations
    from models.driver_profile import DriverState
    from services.driver_service import write_transition

    path = str(tmp_path / "write_transition.db")
    await run_migrations(path)
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (1, 1, 2, 3)"
        )
        await db.execute(
            "INSERT INTO driver_profiles (id, discord_user_id, current_state) "
            "VALUES (9, '99', 'PENDING_SIGNUP_COMPLETION')"
        )
        await db.commit()

        with pytest.raises(ValueError, match="not allowed"):
            await write_transition(
                db, 9, DriverState.PENDING_SIGNUP_COMPLETION, DriverState.ASSIGNED
            )
        await write_transition(
            db, 9, DriverState.PENDING_SIGNUP_COMPLETION, DriverState.NOT_SIGNED_UP
        )
        await db.commit()
        cursor = await db.execute("SELECT current_state FROM driver_profiles WHERE id = 9")
        assert (await cursor.fetchone())[0] == "NOT_SIGNED_UP"
