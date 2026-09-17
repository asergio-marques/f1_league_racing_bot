"""DriverService — driver profile CRUD and state machine."""
from __future__ import annotations

import logging

from db.database import get_connection
from models.driver_profile import DriverProfile, DriverState

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# State machine transition map
# ---------------------------------------------------------------------------

ALLOWED_TRANSITIONS: dict[DriverState, set[DriverState]] = {
    DriverState.NOT_SIGNED_UP: {
        DriverState.PENDING_SIGNUP_COMPLETION,
    },
    DriverState.PENDING_SIGNUP_COMPLETION: {
        DriverState.PENDING_ADMIN_APPROVAL,
        DriverState.NOT_SIGNED_UP,  # withdrawal, inactivity, or force-close
    },
    DriverState.PENDING_ADMIN_APPROVAL: {
        DriverState.AWAITING_CORRECTION_PARAMETER,
        DriverState.UNASSIGNED,
        DriverState.NOT_SIGNED_UP,  # rejection or withdrawal
    },
    DriverState.AWAITING_CORRECTION_PARAMETER: {
        DriverState.PENDING_DRIVER_CORRECTION,
        DriverState.PENDING_ADMIN_APPROVAL,  # 5-minute timeout
        DriverState.NOT_SIGNED_UP,           # withdrawal or force-close
    },
    DriverState.PENDING_DRIVER_CORRECTION: {
        DriverState.PENDING_ADMIN_APPROVAL,
        DriverState.NOT_SIGNED_UP,  # withdrawal, inactivity, or force-close
    },
    DriverState.UNASSIGNED: {
        DriverState.ASSIGNED,
        DriverState.NOT_SIGNED_UP,  # /driver sack
    },
    DriverState.ASSIGNED: {
        DriverState.UNASSIGNED,
        DriverState.NOT_SIGNED_UP,  # /driver sack
    },
}

# Test-mode additional transitions from NOT_SIGNED_UP
_TEST_MODE_EXTRA_FROM_NOT_SIGNED_UP: set[DriverState] = {
    DriverState.UNASSIGNED,
    DriverState.ASSIGNED,
}


async def write_transition(
    db,
    profile_id: int,
    current: DriverState,
    new_state: DriverState,
    *,
    test_mode_active: bool = False,
) -> None:
    """Write *profile_id*'s move from *current* to *new_state*, within the caller's transaction.

    The one place a driver's state is written (Constitution VIII: no code path sets a state
    directly). A caller changing a state as part of a larger write — a sack freeing seats in
    the same transaction, the driver pass ending a season — calls this rather than issuing
    the UPDATE itself, so the transition table governs every route alike. Raises ValueError
    for a transition the table does not allow, having written nothing. Does not commit.
    """
    allowed = set(ALLOWED_TRANSITIONS.get(current, set()))
    if test_mode_active and current == DriverState.NOT_SIGNED_UP:
        allowed |= _TEST_MODE_EXTRA_FROM_NOT_SIGNED_UP
    if new_state not in allowed:
        raise ValueError(
            f"Transition from {current.value} to {new_state.value} is not allowed. "
            f"Allowed targets: {sorted(s.value for s in allowed) or 'none'}."
        )
    await db.execute(
        "UPDATE driver_profiles SET current_state = ? WHERE id = ?",
        (new_state.value, profile_id),
    )


def _row_to_profile(row) -> DriverProfile:
    """Convert an aiosqlite Row from driver_profiles to a DriverProfile."""
    return DriverProfile(
        id=row["id"],
        server_id=row["server_id"],
        discord_user_id=row["discord_user_id"],
        current_state=DriverState(row["current_state"]),
        former_driver=bool(row["former_driver"]),
    )


async def resolve_driver_profile_id(server_id: int, discord_user_id: int, db) -> int | None:
    """Return driver_profiles.id for the given discord_user_id and server_id.

    Accepts an open aiosqlite connection so callers can reuse an existing transaction.
    Returns None if no matching profile is found.
    """
    cursor = await db.execute(
        "SELECT id FROM driver_profiles "
        "WHERE server_id = ? AND CAST(discord_user_id AS INTEGER) = ?",
        (server_id, discord_user_id),
    )
    row = await cursor.fetchone()
    return row[0] if row else None


#: The divisions of one server, for re-keying rows that carry no server of their own.
#:
#: `driver_standings_snapshots` and the two session-result tables name their driver by Discord
#: account but hold no `server_id`, so an unscoped re-key would move the same person's results
#: in every other league this bot serves. They reach their server through their division.
_DIVISIONS_OF_SERVER_SQL = (
    "SELECT d.id FROM divisions d JOIN seasons s ON s.id = d.season_id WHERE s.server_id = ?"
)

#: The sessions of one server, scoped exactly as `_DIVISIONS_OF_SERVER_SQL` is.
_SESSIONS_OF_SERVER_SQL = (
    f"SELECT sr.id FROM session_results sr WHERE sr.division_id IN ({_DIVISIONS_OF_SERVER_SQL})"
)


class DriverService:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    # ------------------------------------------------------------------
    # DB helpers
    # ------------------------------------------------------------------

    async def get_profile(self, server_id: int, discord_user_id: str) -> DriverProfile | None:
        """Return the DriverProfile for this server/user, or None."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT id, server_id, discord_user_id, current_state, former_driver "
                "FROM driver_profiles WHERE server_id = ? AND discord_user_id = ?",
                (server_id, discord_user_id),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return _row_to_profile(row)

    async def _create_profile(
        self,
        server_id: int,
        discord_user_id: str,
        initial_state: DriverState,
    ) -> DriverProfile:
        """Insert a new driver profile and return it."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "INSERT INTO driver_profiles "
                "(server_id, discord_user_id, current_state, former_driver) "
                "VALUES (?, ?, ?, 0)",
                (server_id, discord_user_id, initial_state.value),
            )
            await db.commit()
            profile_id = cursor.lastrowid
        return DriverProfile(
            id=profile_id,
            server_id=server_id,
            discord_user_id=discord_user_id,
            current_state=initial_state,
            former_driver=False,
        )

    # ------------------------------------------------------------------
    # State machine
    # ------------------------------------------------------------------

    async def transition(
        self,
        server_id: int,
        discord_user_id: str,
        new_state: DriverState,
        *,
        test_mode_active: bool = False,
    ) -> DriverProfile | None:
        """Transition a driver to *new_state*.

        Returns the updated DriverProfile. No transition deletes a profile (issue #220): a
        driver without the former-driver flag who reaches NOT_SIGNED_UP is pending deletion,
        and is deleted by the driver pass that ends the season.

        Raises ValueError for disallowed transitions.
        """
        profile = await self.get_profile(server_id, discord_user_id)

        if profile is None:
            # Absent profile = implicitly NOT_SIGNED_UP (Principle VIII).
            # Allow any transition valid from NOT_SIGNED_UP; create a fresh profile.
            valid = set(ALLOWED_TRANSITIONS.get(DriverState.NOT_SIGNED_UP, set()))
            if test_mode_active:
                valid |= _TEST_MODE_EXTRA_FROM_NOT_SIGNED_UP
            if new_state not in valid:
                raise ValueError(
                    f"No driver profile found for user {discord_user_id}; "
                    f"transition from NOT_SIGNED_UP to {new_state.value} is not allowed. "
                    f"Allowed targets: {sorted(s.value for s in valid) or 'none'}."
                )
            return await self._create_profile(server_id, discord_user_id, new_state)

        # Reaching Not Signed Up deletes nothing and clears nothing (issue #220). A driver
        # without the former-driver flag is pending deletion, deleted by the season's end;
        # a former driver's signups are season history and are kept whole.
        async with get_connection(self._db_path) as db:
            await write_transition(
                db, profile.id, profile.current_state, new_state,
                test_mode_active=test_mode_active,
            )
            await db.commit()
        profile.current_state = new_state
        return profile

    # ------------------------------------------------------------------
    # Reassign user ID (US2)
    # ------------------------------------------------------------------

    async def reassign_user_id(
        self,
        server_id: int,
        old_user_id: str,
        new_user_id: str,
        actor_id: int,
        actor_name: str,
    ) -> DriverProfile:
        """Re-key a driver profile, and everything naming the driver, onto another account.

        A re-key exists so that a person changing Discord account keeps their history (core
        specification, *Changing the account behind a profile*), so every record naming the
        driver by their account is carried with the profile: their signups, their session
        results and their standings. Left on the old account, as they were until issue #222,
        the driver's standings line is drawn as a **raw snowflake** — a name is resolved by
        joining `driver_profiles` on the account, and the old one no longer holds a profile —
        and the season's end reads their final standing as zero points and no position.

        **The ids bind as TEXT against the INTEGER columns on purpose.** `driver_profiles`
        holds the account as TEXT where the results tables hold it as INTEGER, and SQLite's
        column affinity converts a TEXT parameter on both sides: `driver_user_id = '4242'`
        matches `4242`, and the `SET` stores an integer. Casting in Python instead would
        raise on an account id that is not a number, where affinity simply matches nothing.
        """
        existing_old = await self.get_profile(server_id, old_user_id)
        if existing_old is None:
            raise ValueError(
                f"No driver profile found for user {old_user_id} on this server."
            )
        existing_new = await self.get_profile(server_id, new_user_id)
        if existing_new is not None:
            raise ValueError(
                f"User {new_user_id} already has a driver profile on this server. "
                "Reassignment is not permitted."
            )
        async with get_connection(self._db_path) as db:
            await db.execute(
                "UPDATE driver_profiles SET discord_user_id = ? WHERE id = ?",
                (new_user_id, existing_old.id),
            )
            # Every signup the profile made goes with it (issue #220): they are keyed by the
            # account, and left on the old one they would belong to nobody's history.
            await db.execute(
                "UPDATE signup_records SET discord_user_id = ? "
                "WHERE server_id = ? AND discord_user_id = ?",
                (new_user_id, server_id, old_user_id),
            )
            # Their standings, and their results in every session they raced (issue #222).
            await db.execute(
                f"UPDATE driver_standings_snapshots SET driver_user_id = ? "
                f"WHERE driver_user_id = ? AND division_id IN ({_DIVISIONS_OF_SERVER_SQL})",
                (new_user_id, old_user_id, server_id),
            )
            for table in ("race_session_results", "qualifying_session_results"):
                await db.execute(
                    f"UPDATE {table} SET driver_user_id = ? WHERE driver_user_id = ? "
                    f"AND session_result_id IN ({_SESSIONS_OF_SERVER_SQL})",
                    (new_user_id, old_user_id, server_id),
                )
            await db.execute(
                "INSERT INTO audit_entries "
                "(server_id, actor_id, actor_name, division_id, change_type, old_value, new_value, timestamp) "
                "VALUES (?, ?, ?, NULL, 'DRIVER_USER_ID_REASSIGN', ?, ?, datetime('now'))",
                (server_id, actor_id, actor_name, old_user_id, new_user_id),
            )
            await db.commit()
        existing_old.discord_user_id = new_user_id
        return existing_old

    # ------------------------------------------------------------------
    # Former-driver flag override (US3)
    # ------------------------------------------------------------------

    async def set_former_driver(
        self,
        server_id: int,
        discord_user_id: str,
        value: bool,
        actor_id: int,
        actor_name: str,
    ) -> tuple[bool, bool]:
        """Set the former_driver flag.  Returns (old_value, new_value)."""
        profile = await self.get_profile(server_id, discord_user_id)
        if profile is None:
            raise ValueError(
                f"No driver profile found for user {discord_user_id} on this server."
            )
        old_value = profile.former_driver
        async with get_connection(self._db_path) as db:
            await db.execute(
                "UPDATE driver_profiles SET former_driver = ? WHERE id = ?",
                (int(value), profile.id),
            )
            await db.execute(
                "INSERT INTO audit_entries "
                "(server_id, actor_id, actor_name, division_id, change_type, old_value, new_value, timestamp) "
                "VALUES (?, ?, ?, NULL, 'TEST_FORMER_DRIVER_FLAG_SET', ?, ?, datetime('now'))",
                (server_id, actor_id, actor_name, str(old_value), str(value)),
            )
            await db.commit()
        return old_value, value
