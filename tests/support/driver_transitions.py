"""A driver's state moved through the transition table, or their account reassigned, in a save
of its own.

`DriverService.transition_on` moves a driver on the connection a change's save holds, committing
nothing; a test of the transition table alone has no save around it, so `transitioned` opens one,
moves the driver, and commits. `DriverService.reassign_user_id_on` is `/driver reassign`'s save
in the same way, and `reassigned` opens one around it for a test of the reassign without the
queue.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from leaguebot.core.db.database import get_connection
from leaguebot.core.models.driver_profile import DriverProfile, DriverState
from leaguebot.core.services.driver_service import DriverService, ReassignOutcome
from tests.support.delete_hooks import MERGE_HOOKS, delete_hooks

#: The clock a reassign through `reassigned` is handed, unless the test hands its own.
REASSIGNED_AT = datetime(2026, 9, 17, 12, 0, tzinfo=timezone.utc)


async def transitioned(
    db_path: str, discord_user_id: str, new_state: DriverState, *, test_mode_active: bool = False,
) -> DriverProfile:
    """Move the driver holding *discord_user_id* to *new_state* through `transition_on`, in a save
    that commits; gives the profile as moved. Raises the state machine's refusal where the
    table refuses."""
    async with get_connection(db_path) as db:
        profile = await DriverService(db_path).transition_on(
            db, discord_user_id, new_state, test_mode_active=test_mode_active
        )
        await db.commit()
    return profile


async def reassigned(
    db_path: str, old_user_id: str, new_user_id: str, *, hooks: dict[str, Any] | None = None,
    now: datetime = REASSIGNED_AT,
) -> ReassignOutcome:
    """Make *new_user_id* the current account of the driver *old_user_id* names, through
    `reassign_user_id_on` in a save of its own, by the league manager Manager (77) at *now*, handed
    the merge hook the builder makes, or *hooks*. Gives the outcome.

    The save ends as a change's save on the queue ends: a refusal (ValueError) is committed as it
    stands, the change's save turning it into its reply, so that a test sees the refusal itself
    wrote nothing; any other fault is rolled back, as the queue rolls back a save that fails."""
    async with get_connection(db_path) as db:
        await db.execute("BEGIN IMMEDIATE")
        try:
            outcome = await DriverService(db_path).reassign_user_id_on(
                db, old_user_id, new_user_id, 77, "Manager",
                **(hooks or delete_hooks(*MERGE_HOOKS)), now=now,
            )
        except ValueError:
            await db.commit()
            raise
        except BaseException:
            await db.rollback()
            raise
        await db.commit()
    return outcome
