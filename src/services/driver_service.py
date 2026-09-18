"""DriverService — driver profile CRUD and state machine."""
from __future__ import annotations

import logging
from dataclasses import dataclass

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
        discord_user_id=row["discord_user_id"],
        current_state=DriverState(row["current_state"]),
        former_driver=bool(row["former_driver"]),
    )


async def resolve_driver_profile_id(discord_user_id: int, db) -> int | None:
    """Return the id of the driver who holds *discord_user_id*, or None.

    Any account the driver has held identifies them, not only the current one (issue #243).
    Accepts an open aiosqlite connection so callers can reuse an existing transaction.
    """
    cursor = await db.execute(
        "SELECT driver_profile_id FROM driver_accounts "
        "WHERE discord_user_id = ?",
        (str(discord_user_id),),
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

#: The order "the driver's signup" is chosen in: the latest season first, and within a season
#: an approved signup before any other, then the latest. A seated driver's approved signup
#: therefore outranks a later one the league rejected on another of their accounts (decided
#: 2026-09-18). A driver cannot sign up again while approved, so within one account the rule
#: changes nothing.
SIGNUP_PRECEDENCE_SQL = "season_id DESC, approved DESC, id DESC"

#: The id of the signup record that is the driver's — the driver row aliased ``dp``.
DRIVERS_SIGNUP_OF_DP_SQL = (
    "(SELECT id FROM signup_records "
    f"WHERE discord_user_id IN {ACCOUNTS_OF_DP_SQL} "
    f"ORDER BY {SIGNUP_PRECEDENCE_SQL} LIMIT 1)"
)


async def accounts_of(db, discord_user_id) -> list[str]:
    """Every account of the driver holding *discord_user_id*, or just that account.

    Sorted, so a caller building SQL from it binds in a stable order.
    """
    cursor = await db.execute(
        "SELECT discord_user_id FROM driver_accounts WHERE driver_profile_id = ("
        "  SELECT driver_profile_id FROM driver_accounts"
        "  WHERE discord_user_id = ?"
        ") ORDER BY discord_user_id",
        (str(discord_user_id),),
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


async def current_account_of(db, discord_user_id) -> str:
    """The current account of the driver holding *discord_user_id*; the account itself if none."""
    cursor = await db.execute(
        "SELECT dp.discord_user_id FROM driver_accounts da "
        "JOIN driver_profiles dp ON dp.id = da.driver_profile_id "
        "WHERE da.discord_user_id = ?",
        (str(discord_user_id),),
    )
    row = await cursor.fetchone()
    return str(row[0]) if row else str(discord_user_id)


async def current_account_map(db) -> dict[int, int]:
    """Every *past* account → its driver's current account, as ints.

    A current account is absent, as is any account no driver holds: look up with
    ``mapping.get(uid, uid)``. Carrying only the accounts that differ keeps the map to the
    handful of drivers who ever changed account.
    """
    cursor = await db.execute(
        "SELECT da.discord_user_id AS account, dp.discord_user_id AS current "
        "FROM driver_accounts da JOIN driver_profiles dp ON dp.id = da.driver_profile_id "
        "WHERE da.discord_user_id != dp.discord_user_id",
    )
    mapping: dict[int, int] = {}
    for row in await cursor.fetchall():
        try:
            mapping[int(row["account"])] = int(row["current"])
        except (TypeError, ValueError):
            continue
    return mapping


async def _division_exists(db, division_id: int) -> bool:
    cursor = await db.execute("SELECT 1 FROM divisions WHERE id = ?", (division_id,))
    return await cursor.fetchone() is not None


async def accounts_of_in_division(db, division_id: int, discord_user_id) -> list[str]:
    """`accounts_of`, for a reader holding a division. An unknown division knows only the
    account it was given."""
    if not await _division_exists(db, division_id):
        return [str(discord_user_id)]
    return await accounts_of(db, discord_user_id)


async def current_account_map_for_division(db, division_id: int) -> dict[int, int]:
    """`current_account_map`, for the readers of the results and standings tables, which
    hold a division. An unknown division maps nothing.
    """
    if not await _division_exists(db, division_id):
        return {}
    return await current_account_map(db)


async def divisions_taken_part_in(
    db, profile_id: int | None, accounts: list[str]
) -> set[int]:
    """Every division the identity took part in: by a confirmed seat, or by a
    result or a standing under any of *accounts*.

    A division belongs to one season, so two identities sharing one is one person in one
    season's standings twice — which is what a merge must not produce (decided 2026-09-18).
    *profile_id* is None for an account no driver holds, whose leftover records are all there
    is of it.
    """
    divisions: set[int] = set()
    if profile_id is not None:
        cursor = await db.execute(
            "SELECT division_id FROM driver_division_memberships WHERE driver_profile_id = ?",
            (profile_id,),
        )
        divisions |= {r[0] for r in await cursor.fetchall()}
    if accounts:
        marks = ",".join("?" for _ in accounts)
        cursor = await db.execute(
            f"""
            SELECT sr.division_id FROM race_session_results x
            JOIN session_results sr ON sr.id = x.session_result_id
            WHERE x.driver_user_id IN ({marks})
            UNION
            SELECT sr.division_id FROM qualifying_session_results x
            JOIN session_results sr ON sr.id = x.session_result_id
            WHERE x.driver_user_id IN ({marks})
            UNION
            SELECT division_id FROM driver_standings_snapshots
            WHERE driver_user_id IN ({marks})
            """,
            (*accounts, *accounts, *accounts),
        )
        divisions |= {r[0] for r in await cursor.fetchall()}
    return divisions


async def _describe_division(db, division_id: int) -> str:
    cursor = await db.execute(
        "SELECT s.season_number, d.name FROM divisions d JOIN seasons s ON s.id = d.season_id "
        "WHERE d.id = ?",
        (division_id,),
    )
    row = await cursor.fetchone()
    return f"Season {row[0]} {row[1]}" if row else f"division {division_id}"


#: Every column naming a driver by their profile, which a merge moves onto the driver kept.
#: The division memberships come first: moving a placement fires migration 057's trigger,
#: which would otherwise add the membership a second time.
_PROFILE_COLUMNS = (
    "driver_division_memberships",
    "team_seats",
    "driver_season_assignments",
    "driver_history_entries",
    "driver_round_attendance",
    "driver_standings_snapshots",
    "race_session_results",
    "qualifying_session_results",
    "driver_accounts",
)


#: The states of a driver whose signup is under way: collecting their answers, in review, or
#: in correction. A reassign waits for it to be finished or withdrawn.
SIGNUP_IN_PROGRESS = frozenset({
    DriverState.PENDING_SIGNUP_COMPLETION.value,
    DriverState.PENDING_ADMIN_APPROVAL.value,
    DriverState.AWAITING_CORRECTION_PARAMETER.value,
    DriverState.PENDING_DRIVER_CORRECTION.value,
})


async def _profile_holding(db, discord_user_id):
    """The driver_profiles row of the driver holding *discord_user_id*, by any account."""
    cursor = await db.execute(
        "SELECT dp.id, dp.discord_user_id, dp.current_state, dp.former_driver, "
        "dp.is_test_driver FROM driver_accounts da "
        "JOIN driver_profiles dp ON dp.id = da.driver_profile_id "
        "WHERE da.discord_user_id = ?",
        (str(discord_user_id),),
    )
    return await cursor.fetchone()


#: The states in which a driver holds a signup or a seat in the live season. Two profiles both
#: in one of these cannot become one driver (issue #243).
_HOLDS_THE_LIVE_SEASON = frozenset({DriverState.UNASSIGNED.value, DriverState.ASSIGNED.value})


async def _refuse_a_shared_division(
    db, ours: set[int], theirs: set[int], who: str
) -> None:
    shared = sorted(ours & theirs)
    if shared:
        raise ValueError(
            f"This driver and {who} both took part in {await _describe_division(db, shared[0])}. "
            "One person cannot stand twice in one season's standings, so the accounts cannot "
            "be joined."
        )


async def _merge(db, kept, absorbed, actor_id: int, actor_name: str) -> None:
    """Fold the *absorbed* profile into the *kept* one, within the caller's transaction.

    Every column naming the absorbed driver by profile is moved onto the kept one, its
    accounts with them; the former-driver flag is kept if either held it; and the absorbed
    profile is deleted. The refusals before this guarantee no two rows collide on a key.
    Nothing naming a driver by account is touched.
    """
    for table in _PROFILE_COLUMNS:
        await db.execute(
            f"UPDATE {table} SET driver_profile_id = ? WHERE driver_profile_id = ?",
            (kept["id"], absorbed["id"]),
        )
    await db.execute(
        "UPDATE driver_profiles SET former_driver = 1 WHERE id = ? AND ? = 1",
        (kept["id"], int(bool(absorbed["former_driver"]))),
    )
    await db.execute("DELETE FROM driver_profiles WHERE id = ?", (absorbed["id"],))
    await db.execute(
        "INSERT INTO audit_entries "
        "(actor_id, actor_name, division_id, change_type, old_value, new_value, "
        "timestamp) VALUES (?, ?, NULL, "
        "'DRIVER_PROFILES_MERGED', ?, ?, datetime('now'))",
        (actor_id, actor_name, str(absorbed["id"]), str(kept["id"])),
    )


def _refuse_a_signup_in_progress(row, who: str) -> None:
    if row["current_state"] in SIGNUP_IN_PROGRESS:
        raise ValueError(
            f"{who[0].upper()}{who[1:]} has a signup in progress. Finish it — approve or reject "
            "it — or have it withdrawn first."
        )


@dataclass
class ReassignOutcome:
    """What a reassign did, for the command to act on and report."""

    #: The driver, standing on their new current account.
    profile: DriverProfile
    #: The account that was current before, whose roles and signup channel move to the new one.
    replaced_account: str
    #: Every account the driver now holds, current included, sorted.
    accounts: list[str]
    #: Whether the new account was one of the driver's own past accounts.
    switched_back: bool = False
    #: The accounts of a second profile merged into this one, where the new account had one.
    merged_accounts: list[str] | None = None


class DriverService:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    # ------------------------------------------------------------------
    # DB helpers
    # ------------------------------------------------------------------

    async def get_profile(self, discord_user_id: str) -> DriverProfile | None:
        """Return the DriverProfile for this server/user, or None."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT id, discord_user_id, current_state, former_driver "
                "FROM driver_profiles WHERE discord_user_id = ?",
                (discord_user_id,),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return _row_to_profile(row)

    async def current_account(self, discord_user_id) -> str:
        """The current account of the driver holding *discord_user_id*; see `current_account_of`."""
        async with get_connection(self._db_path) as db:
            return await current_account_of(db, discord_user_id)

    async def _create_profile(
        self,
        discord_user_id: str,
        initial_state: DriverState,
    ) -> DriverProfile:
        """Insert a new driver profile and return it."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "INSERT INTO driver_profiles "
                "(discord_user_id, current_state, former_driver) "
                "VALUES (?, ?, 0)",
                (discord_user_id, initial_state.value),
            )
            await db.commit()
            profile_id = cursor.lastrowid
        return DriverProfile(
            id=profile_id,
            discord_user_id=discord_user_id,
            current_state=initial_state,
            former_driver=False,
        )

    # ------------------------------------------------------------------
    # State machine
    # ------------------------------------------------------------------

    async def transition(
        self,
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
        profile = await self.get_profile(discord_user_id)

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
            return await self._create_profile(discord_user_id, new_state)

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
        old_user_id: str,
        new_user_id: str,
        actor_id: int,
        actor_name: str,
    ) -> ReassignOutcome:
        """Make *new_user_id* the current account of the driver *old_user_id* names.

        A person changing Discord account keeps their history because a driver owns every
        account they have held (issue #243): the profile's current account changes, the
        migration 059 triggers list the new one beside the old, and **no record is
        rewritten**. A result, a standing, a signup or a history entry keeps the account it
        was written under, and an archived season stays exactly as it was. Everything read
        from then on maps the old account to the new one. This replaced the re-key of issue
        #222, which rewrote every such record onto the new account, completed seasons
        included, against the rule that one is immutable.

        *old_user_id* may be any account of the driver's, current or past. Naming one of the
        driver's own past accounts as *new_user_id* makes it current again.

        Where *new_user_id* is another profile's current account, the two are **merged** into
        one driver owning both lists of accounts. The profile holding the live season's seat
        or signup is the one kept, the driver named otherwise; the other's links — seats,
        placements, attendance, history, results — move onto it, as does its former-driver
        flag, and it is deleted.

        Refused, with nothing changed, when: no driver holds *old_user_id*; *new_user_id* is
        already the driver's current account; it is a past account of another driver; either
        side is a test-mode driver; either side has a signup in progress; both sides hold a
        seat or a signup in the live season; or both took part in one division — one
        season's division, by a confirmed seat or by results. That last applies as much to an
        account no driver holds whose leftover results sit in such a division: one person
        would stand twice in one season's standings (decided 2026-09-18).

        The checks and the write share one `BEGIN IMMEDIATE` transaction, so a signup begun or
        a second reassign given in between cannot slip past a check that has already passed.
        The Discord side — roles, a held signup channel, a portrait — is the caller's, after
        this returns; nothing there can undo it.
        """
        async with get_connection(self._db_path) as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                driver = await _profile_holding(db, old_user_id)
                if driver is None:
                    raise ValueError(
                        f"No driver profile found for user {old_user_id} on this server."
                    )
                if driver["is_test_driver"]:
                    raise ValueError(
                        "That driver was created by test mode. A test-mode driver cannot be "
                        "given a real account, nor a real driver a test-mode one."
                    )
                replaced = str(driver["discord_user_id"])
                if replaced == str(new_user_id):
                    raise ValueError(f"<@{new_user_id}> is already this driver's current account.")

                owner = await _profile_holding(db, new_user_id)
                switched_back = owner is not None and owner["id"] == driver["id"]
                if owner is not None and not switched_back:
                    if str(owner["discord_user_id"]) != str(new_user_id):
                        raise ValueError(
                            f"<@{new_user_id}> is a past account of another driver in this "
                            "league, and an account belongs to one driver."
                        )
                    if owner["is_test_driver"]:
                        raise ValueError(
                            f"<@{new_user_id}> is a test-mode driver. A test-mode driver cannot "
                            "be merged with a real one."
                        )
                _refuse_a_signup_in_progress(driver, "this driver")
                driver_accounts = await accounts_of_profile(db, driver["id"])
                merged_accounts: list[str] | None = None
                kept = driver
                if owner is not None and not switched_back:
                    _refuse_a_signup_in_progress(owner, f"<@{new_user_id}>")
                    if driver["current_state"] in _HOLDS_THE_LIVE_SEASON and (
                        owner["current_state"] in _HOLDS_THE_LIVE_SEASON
                    ):
                        raise ValueError(
                            f"This driver and <@{new_user_id}> both hold a seat or a signup in "
                            "the live season, so they cannot be merged into one driver."
                        )
                    owner_accounts = await accounts_of_profile(db, owner["id"])
                    await _refuse_a_shared_division(
                        db, await divisions_taken_part_in(db, driver["id"], driver_accounts),
                        await divisions_taken_part_in(db, owner["id"], owner_accounts),
                        f"<@{new_user_id}>",
                    )
                    kept, absorbed = (
                        (owner, driver)
                        if owner["current_state"] in _HOLDS_THE_LIVE_SEASON
                        else (driver, owner)
                    )
                    await _merge(db, kept, absorbed, actor_id, actor_name)
                    merged_accounts = owner_accounts
                elif owner is None:
                    await _refuse_a_shared_division(
                        db, await divisions_taken_part_in(db, driver["id"], driver_accounts),
                        await divisions_taken_part_in(db, None, [str(new_user_id)]),
                        f"<@{new_user_id}>",
                    )

                await db.execute(
                    "UPDATE driver_profiles SET discord_user_id = ? WHERE id = ?",
                    (str(new_user_id), kept["id"]),
                )
                await db.execute(
                    "INSERT INTO audit_entries "
                    "(actor_id, actor_name, division_id, change_type, old_value, "
                    "new_value, timestamp) "
                    "VALUES (?, ?, NULL, 'DRIVER_USER_ID_REASSIGN', ?, ?, datetime('now'))",
                    (actor_id, actor_name, replaced, str(new_user_id)),
                )
                accounts = await accounts_of_profile(db, kept["id"])
                cursor = await db.execute(
                    "SELECT id, discord_user_id, current_state, former_driver "
                    "FROM driver_profiles WHERE id = ?",
                    (kept["id"],),
                )
                profile = _row_to_profile(await cursor.fetchone())
                await db.commit()
            except BaseException:
                await db.rollback()
                raise
        return ReassignOutcome(
            profile=profile,
            replaced_account=replaced,
            accounts=accounts,
            switched_back=switched_back,
            merged_accounts=merged_accounts,
        )

    # ------------------------------------------------------------------
    # Former-driver flag override (US3)
    # ------------------------------------------------------------------

    async def set_former_driver(
        self,
        discord_user_id: str,
        value: bool,
        actor_id: int,
        actor_name: str,
    ) -> tuple[bool, bool]:
        """Set the former_driver flag.  Returns (old_value, new_value).

        Any account the driver has held names them (issue #243).
        """
        profile = await self.get_profile(
            await self.current_account(discord_user_id)
        )
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
                "(actor_id, actor_name, division_id, change_type, old_value, new_value, timestamp) "
                "VALUES (?, ?, NULL, 'TEST_FORMER_DRIVER_FLAG_SET', ?, ?, datetime('now'))",
                (actor_id, actor_name, str(old_value), str(value)),
            )
            await db.commit()
        return old_value, value
