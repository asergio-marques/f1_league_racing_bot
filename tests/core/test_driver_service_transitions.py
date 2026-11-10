"""Unit tests for DriverService state transitions added in T007 — T032."""

from __future__ import annotations

import pytest

from tests.support.driver_transitions import transitioned

#: Why the state machine's refusal tests fail until #283's build names it.
STATE_MACHINE_REFUSAL = (
    "#283: the state machine still refuses a transition with a bare ValueError, "
    "not InvalidDriverTransition"
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def db_path(tmp_path):
    """A migrated database with the league's server_configs row."""
    from leaguebot.core.db.database import get_connection, run_migrations

    path = str(tmp_path / "driver_test.db")
    await run_migrations(path)
    async with get_connection(path) as db:
        await db.execute("INSERT INTO server_configs (server_id) VALUES (1)")
        await db.commit()
    return path


async def _seed_driver(db_path, user_id: str, state: str) -> None:
    from leaguebot.core.db.database import get_connection
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO driver_profiles (discord_user_id, current_state) VALUES (?, ?)",
            (user_id, state),
        )
        await db.commit()


# ---------------------------------------------------------------------------
# The transitions allowed and refused (T007, T052)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "from_state, to_state, former",
    [
        pytest.param("PENDING_SIGNUP_COMPLETION", "NOT_SIGNED_UP", False, id="PSC to NSU"),
        pytest.param("PENDING_SIGNUP_COMPLETION", "NOT_SIGNED_UP", True,
                     id="PSC to NSU, a former driver"),
        pytest.param("PENDING_SIGNUP_COMPLETION", "PENDING_ADMIN_APPROVAL", False,
                     id="PSC to PAA"),
        pytest.param("PENDING_DRIVER_CORRECTION", "NOT_SIGNED_UP", False, id="PDC to NSU"),
        pytest.param("PENDING_DRIVER_CORRECTION", "PENDING_ADMIN_APPROVAL", False,
                     id="PDC to PAA"),
        pytest.param("PENDING_ADMIN_APPROVAL", "AWAITING_CORRECTION_PARAMETER", False,
                     id="PAA to ACP"),
        pytest.param("AWAITING_CORRECTION_PARAMETER", "PENDING_DRIVER_CORRECTION", False,
                     id="ACP to PDC"),
        pytest.param("AWAITING_CORRECTION_PARAMETER", "PENDING_ADMIN_APPROVAL", False,
                     id="ACP to PAA, the timeout's revert"),
        pytest.param("AWAITING_CORRECTION_PARAMETER", "NOT_SIGNED_UP", False,
                     id="ACP to NSU, a withdrawal or forced close"),
        pytest.param("PENDING_ADMIN_APPROVAL", "NOT_SIGNED_UP", False, id="PAA to NSU"),
    ],
)
async def test_an_allowed_transition_lands_on_its_target(db_path, from_state, to_state, former):
    """A driver in *from_state*, a former driver or not, is moved to *to_state*. A return to
    Not Signed Up deletes nothing: the profile is kept, pending deletion (#220), a former
    driver's as anyone's. The awaiting-correction-parameter state (T052, spec 014) is reached
    from pending admin approval, and left for a driver correction, back to admin approval when
    it times out, or to Not Signed Up on a withdrawal or a forced close."""
    from leaguebot.core.db.database import get_connection
    from leaguebot.core.models.driver_profile import DriverState
    await _seed_driver(db_path, "u1", from_state)
    if former:
        async with get_connection(db_path) as db:
            await db.execute(
                "UPDATE driver_profiles SET former_driver = 1 WHERE discord_user_id = 'u1'"
            )
            await db.commit()
    result = await transitioned(db_path, "u1", DriverState(to_state))
    assert result is not None
    assert result.current_state == DriverState(to_state)


@pytest.mark.parametrize(
    "from_state, to_state",
    [
        pytest.param("PENDING_SIGNUP_COMPLETION", "AWAITING_CORRECTION_PARAMETER",
                     id="PSC to ACP"),
        pytest.param("PENDING_DRIVER_CORRECTION", "ASSIGNED", id="PDC to ASSIGNED"),
    ],
)
@pytest.mark.xfail(strict=True, reason=STATE_MACHINE_REFUSAL)
async def test_a_transition_the_table_does_not_allow_raises(db_path, from_state, to_state):
    """The awaiting-correction-parameter state is reachable only from pending admin approval,
    not from pending signup completion, and a driver awaiting their correction cannot go
    straight to Assigned. The state machine refuses by a name of its own, which is not a
    refusal a member reads: a caller judges the move first, and catches this by name only where
    a move raced past its judgement."""
    from leaguebot.core.models.driver_profile import DriverState, InvalidDriverTransition
    from leaguebot.core.models.refusal import Refused
    await _seed_driver(db_path, "u1", from_state)
    with pytest.raises(InvalidDriverTransition) as refused:
        await transitioned(db_path, "u1", DriverState(to_state))
    assert not isinstance(refused.value, Refused)


# ---------------------------------------------------------------------------
# T052 — NOT_SIGNED_UP signup-data clearing (former_driver paths)
# ---------------------------------------------------------------------------


class TestSignupDataClearing:
    """T052: NOT_SIGNED_UP keeps a former driver's signup data (issue #220 withdrew the clearing)."""

    async def _seed_former_driver_with_record(self, db_path: str, user_id: str) -> None:
        from leaguebot.core.db.database import get_connection
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
        from leaguebot.core.models.driver_profile import DriverState
        await self._seed_former_driver_with_record(db_path, "fd1")
        result = await transitioned(db_path, "fd1", DriverState.NOT_SIGNED_UP)
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


@pytest.mark.xfail(strict=True, reason=STATE_MACHINE_REFUSAL)
async def test_a_state_written_within_a_transaction_obeys_the_table(tmp_path):
    """`write_transition` is how a sack or the driver pass changes a state inside a larger
    write, and it refuses what the table refuses, writing nothing."""
    from leaguebot.core.db.database import get_connection, run_migrations
    from leaguebot.core.models.driver_profile import DriverState
    from leaguebot.core.models.driver_profile import InvalidDriverTransition
    from leaguebot.core.services.driver_service import write_transition

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

        with pytest.raises(InvalidDriverTransition, match="not allowed"):
            await write_transition(
                db, 9, DriverState.PENDING_SIGNUP_COMPLETION, DriverState.ASSIGNED
            )
        await write_transition(
            db, 9, DriverState.PENDING_SIGNUP_COMPLETION, DriverState.NOT_SIGNED_UP
        )
        await db.commit()
        cursor = await db.execute("SELECT current_state FROM driver_profiles WHERE id = 9")
        assert (await cursor.fetchone())[0] == "NOT_SIGNED_UP"
