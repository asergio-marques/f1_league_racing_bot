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
    """Return the id of the driver who holds *discord_user_id* on *server_id*, or None.

    Any account the driver has held identifies them, not only the current one (issue #243).
    Accepts an open aiosqlite connection so callers can reuse an existing transaction.
    """
    cursor = await db.execute(
        "SELECT driver_profile_id FROM driver_accounts "
        "WHERE server_id = ? AND discord_user_id = ?",
        (server_id, str(discord_user_id)),
    )
    row = await cursor.fetchone()
    return row[0] if row else None


# ---------------------------------------------------------------------------
# A driver's accounts (issue #243)
# ---------------------------------------------------------------------------
#
# A driver owns every Discord account they have raced under; `driver_accounts` lists them
# and `driver_profiles.discord_user_id` names the current one. A stored record keeps the
# account it was written under, so the rest of the bot applies three rules, through the
# helpers below:
#
# - an account arriving from Discord — a command, a pasted result, a button — is mapped to
#   the current account *first* (`current_account_of`), and everything after runs as before;
# - finding a stored record from an account matches every account of the driver
#   (`accounts_of`);
# - reading stored records for counting or drawing maps each to the current account
#   (`current_account_map`), so a driver is counted once and named by the account in use.
#
# An account no driver holds maps to itself throughout: a record of a driver since deleted
# still reads exactly as it was written.
#
# Accounts bind as TEXT, matching the column. The results tables hold them as INTEGER, and
# the maps are keyed by int for that reason; an account that is not a number cannot appear
# in those tables and is left out of the maps.


#: Every account of the driver row aliased ``dp``, for matching a record written under any of
#: them — `discord_user_id IN {ACCOUNTS_OF_DP_SQL}`. A driver's signups are kept under the
#: account each was made from, so "the driver's latest signup" is the latest across all.
ACCOUNTS_OF_DP_SQL = (
    "(SELECT discord_user_id FROM driver_accounts WHERE driver_profile_id = dp.id)"
)


async def accounts_of(db, server_id: int, discord_user_id) -> list[str]:
    """Every account of the driver holding *discord_user_id*, or just that account.

    Sorted, so a caller building SQL from it binds in a stable order.
    """
    cursor = await db.execute(
        "SELECT discord_user_id FROM driver_accounts WHERE driver_profile_id = ("
        "  SELECT driver_profile_id FROM driver_accounts"
        "  WHERE server_id = ? AND discord_user_id = ?"
        ") ORDER BY discord_user_id",
        (server_id, str(discord_user_id)),
    )
    rows = await cursor.fetchall()
    return [str(r[0]) for r in rows] or [str(discord_user_id)]


async def accounts_of_profile(db, profile_id: int) -> list[str]:
    """Every account *profile_id* has held, current included, sorted."""
    cursor = await db.execute(
        "SELECT discord_user_id FROM driver_accounts WHERE driver_profile_id = ? "
        "ORDER BY discord_user_id",
        (profile_id,),
    )
    return [str(r[0]) for r in await cursor.fetchall()]


async def current_account_of(db, server_id: int, discord_user_id) -> str:
    """The current account of the driver holding *discord_user_id*; the account itself if none."""
    cursor = await db.execute(
        "SELECT dp.discord_user_id FROM driver_accounts da "
        "JOIN driver_profiles dp ON dp.id = da.driver_profile_id "
        "WHERE da.server_id = ? AND da.discord_user_id = ?",
        (server_id, str(discord_user_id)),
    )
    row = await cursor.fetchone()
    return str(row[0]) if row else str(discord_user_id)


async def current_account_map(db, server_id: int) -> dict[int, int]:
    """Every *past* account on *server_id* → its driver's current account, as ints.

    A current account is absent, as is any account no driver holds: look up with
    ``mapping.get(uid, uid)``. Carrying only the accounts that differ keeps the map to the
    handful of drivers who ever changed account.
    """
    cursor = await db.execute(
        "SELECT da.discord_user_id AS account, dp.discord_user_id AS current "
        "FROM driver_accounts da JOIN driver_profiles dp ON dp.id = da.driver_profile_id "
        "WHERE da.server_id = ? AND da.discord_user_id != dp.discord_user_id",
        (server_id,),
    )
    mapping: dict[int, int] = {}
    for row in await cursor.fetchall():
        try:
            mapping[int(row["account"])] = int(row["current"])
        except (TypeError, ValueError):
            continue
    return mapping


async def _server_of_division(db, division_id: int) -> int | None:
    cursor = await db.execute(
        "SELECT s.server_id FROM divisions d JOIN seasons s ON s.id = d.season_id "
        "WHERE d.id = ?",
        (division_id,),
    )
    row = await cursor.fetchone()
    return row[0] if row else None


async def accounts_of_in_division(db, division_id: int, discord_user_id) -> list[str]:
    """`accounts_of` for the server *division_id* belongs to."""
    server_id = await _server_of_division(db, division_id)
    if server_id is None:
        return [str(discord_user_id)]
    return await accounts_of(db, server_id, discord_user_id)


async def current_account_map_for_division(db, division_id: int) -> dict[int, int]:
    """`current_account_map` for the server *division_id* belongs to.

    For the readers of the results and standings tables, which reach their server only
    through the division. An unknown division maps nothing.
    """
    server_id = await _server_of_division(db, division_id)
    if server_id is None:
        return {}
    return await current_account_map(db, server_id)


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


async def account_holds_racing_records(db, server_id: int, discord_user_id: str) -> bool:
    """Whether *discord_user_id* holds results, standings or history of its own on *server_id*.

    Asked of the account a profile is about to be re-keyed onto, which by then is known to
    hold no profile — so any such rows belong to a driver whose profile was deleted, a driver
    who never raced a round in full but may well have been entered as a did-not-start. Moving
    a second person's racing onto them would merge two people's records into one history with
    nothing to separate them again, which is a worse outcome than refusing.
    """
    cursor = await db.execute(
        f"""
        SELECT EXISTS (
            SELECT 1 FROM driver_standings_snapshots
            WHERE driver_user_id = ? AND division_id IN ({_DIVISIONS_OF_SERVER_SQL})
        ) OR EXISTS (
            SELECT 1 FROM race_session_results
            WHERE driver_user_id = ? AND session_result_id IN ({_SESSIONS_OF_SERVER_SQL})
        ) OR EXISTS (
            SELECT 1 FROM qualifying_session_results
            WHERE driver_user_id = ? AND session_result_id IN ({_SESSIONS_OF_SERVER_SQL})
        ) OR EXISTS (
            SELECT 1 FROM driver_history_entries
            WHERE server_id = ? AND discord_user_id = ?
        )
        """,
        (
            discord_user_id, server_id,
            discord_user_id, server_id,
            discord_user_id, server_id,
            server_id, discord_user_id,
        ),
    )
    row = await cursor.fetchone()
    return bool(row[0])


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

    async def current_account(self, server_id: int, discord_user_id) -> str:
        """The current account of the driver holding *discord_user_id*; see `current_account_of`."""
        async with get_connection(self._db_path) as db:
            return await current_account_of(db, server_id, discord_user_id)

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

        Refused, with nothing changed, in three cases: no profile stands at the old account,
        a profile already stands at the new one, or the new account holds racing records of
        its own — see `account_holds_racing_records`.

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
            if await account_holds_racing_records(db, server_id, new_user_id):
                raise ValueError(
                    f"User {new_user_id} already holds results, standings or history of "
                    "their own in this league. Re-keying onto that account would merge two "
                    "drivers' records, and is not permitted."
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
            # Their history of every season that has ended, which names them by identifier so
            # that it outlives the profile (issue #220), and the signup they are part-way
            # through. An abandoned wizard record standing on the new account is dropped
            # first: a wizard record is the transient state of one signup in progress, the
            # new account holds no profile, and so nothing of a driver's can be lost with it.
            await db.execute(
                "UPDATE driver_history_entries SET discord_user_id = ? "
                "WHERE server_id = ? AND discord_user_id = ?",
                (new_user_id, server_id, old_user_id),
            )
            await db.execute(
                "DELETE FROM signup_wizard_records WHERE server_id = ? AND discord_user_id = ?",
                (server_id, new_user_id),
            )
            await db.execute(
                "UPDATE signup_wizard_records SET discord_user_id = ? "
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
            # A session's fastest-lap override names its driver by account too, and the
            # points are recomputed from it whenever a penalty, an appeal verdict or an
            # amendment lands. Left behind it would match nobody, and the bonus a driver was
            # awarded on the day would quietly go to no one at all.
            await db.execute(
                f"UPDATE session_results SET fl_driver_override = ? "
                f"WHERE fl_driver_override = ? AND division_id IN ({_DIVISIONS_OF_SERVER_SQL})",
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
