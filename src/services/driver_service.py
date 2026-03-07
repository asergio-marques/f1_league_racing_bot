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
        DriverState.NOT_SIGNED_UP,  # signup window force-close
    },
    DriverState.PENDING_ADMIN_APPROVAL: {
        DriverState.UNASSIGNED,
        DriverState.PENDING_DRIVER_CORRECTION,
        DriverState.NOT_SIGNED_UP,
        DriverState.SEASON_BANNED,
        DriverState.LEAGUE_BANNED,
    },
    DriverState.PENDING_DRIVER_CORRECTION: {
        DriverState.PENDING_ADMIN_APPROVAL,
        DriverState.SEASON_BANNED,
        DriverState.LEAGUE_BANNED,
        DriverState.NOT_SIGNED_UP,  # signup window force-close
    },
    DriverState.UNASSIGNED: {
        DriverState.ASSIGNED,
        DriverState.SEASON_BANNED,
        DriverState.LEAGUE_BANNED,
    },
    DriverState.ASSIGNED: {
        DriverState.SEASON_BANNED,
        DriverState.LEAGUE_BANNED,
    },
    DriverState.SEASON_BANNED: {
        DriverState.NOT_SIGNED_UP,
        DriverState.LEAGUE_BANNED,
    },
    DriverState.LEAGUE_BANNED: {
        DriverState.NOT_SIGNED_UP,
    },
}

# Test-mode additional transitions from NOT_SIGNED_UP
_TEST_MODE_EXTRA_FROM_NOT_SIGNED_UP: set[DriverState] = {
    DriverState.UNASSIGNED,
    DriverState.ASSIGNED,
}


def _row_to_profile(row) -> DriverProfile:
    """Convert an aiosqlite Row from driver_profiles to a DriverProfile."""
    return DriverProfile(
        id=row["id"],
        server_id=row["server_id"],
        discord_user_id=row["discord_user_id"],
        current_state=DriverState(row["current_state"]),
        former_driver=bool(row["former_driver"]),
        race_ban_count=row["race_ban_count"],
        season_ban_count=row["season_ban_count"],
        league_ban_count=row["league_ban_count"],
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
                "SELECT id, server_id, discord_user_id, current_state, former_driver, "
                "       race_ban_count, season_ban_count, league_ban_count "
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
                "(server_id, discord_user_id, current_state, former_driver, "
                " race_ban_count, season_ban_count, league_ban_count) "
                "VALUES (?, ?, ?, 0, 0, 0, 0)",
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
            race_ban_count=0,
            season_ban_count=0,
            league_ban_count=0,
        )

    async def _update_state(self, profile_id: int, new_state: DriverState) -> None:
        """Persist a state change."""
        async with get_connection(self._db_path) as db:
            await db.execute(
                "UPDATE driver_profiles SET current_state = ? WHERE id = ?",
                (new_state.value, profile_id),
            )
            await db.commit()

    async def _clear_seat_references(self, profile_id: int) -> None:
        """NULL-out any team_seats rows referencing this profile."""
        async with get_connection(self._db_path) as db:
            await db.execute(
                "UPDATE team_seats SET driver_profile_id = NULL WHERE driver_profile_id = ?",
                (profile_id,),
            )
            await db.commit()

    async def _delete_profile(self, profile_id: int) -> None:
        """Hard-delete the driver profile row."""
        async with get_connection(self._db_path) as db:
            await db.execute(
                "DELETE FROM driver_profiles WHERE id = ?",
                (profile_id,),
            )
            await db.commit()

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

        Returns the updated DriverProfile, or None if the profile was deleted
        (NOT_SIGNED_UP transition for a non-former-driver).

        Raises ValueError for disallowed transitions.
        """
        profile = await self.get_profile(server_id, discord_user_id)

        if profile is None:
            # Only valid when test_mode allows direct creation from NOT_SIGNED_UP
            if not test_mode_active or new_state not in _TEST_MODE_EXTRA_FROM_NOT_SIGNED_UP:
                raise ValueError(
                    f"No driver profile found for user {discord_user_id} on this server."
                )
            return await self._create_profile(server_id, discord_user_id, new_state)

        current = profile.current_state
        allowed = set(ALLOWED_TRANSITIONS.get(current, set()))
        if test_mode_active and current == DriverState.NOT_SIGNED_UP:
            allowed |= _TEST_MODE_EXTRA_FROM_NOT_SIGNED_UP

        if new_state not in allowed:
            raise ValueError(
                f"Transition from {current.value} to {new_state.value} is not allowed. "
                f"Allowed targets: {sorted(s.value for s in allowed) or 'none'}."
            )

        if new_state == DriverState.NOT_SIGNED_UP and not profile.former_driver:
            await self._clear_seat_references(profile.id)
            await self._delete_profile(profile.id)
            return None

        await self._update_state(profile.id, new_state)
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
        """Re-key an existing driver profile from old_user_id to new_user_id."""
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
