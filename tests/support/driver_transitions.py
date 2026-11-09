"""A driver's state moved through the transition table, in a save of its own.

`DriverService.transition_on` moves a driver on the connection a change's save holds, committing
nothing; a test of the transition table alone has no save around it, so `transitioned` opens one,
moves the driver, and commits.
"""
from __future__ import annotations

from leaguebot.core.db.database import get_connection
from leaguebot.core.models.driver_profile import DriverProfile, DriverState
from leaguebot.core.services.driver_service import DriverService


async def transitioned(
    db_path: str, discord_user_id: str, new_state: DriverState, *, test_mode_active: bool = False,
) -> DriverProfile:
    """Move the driver holding *discord_user_id* to *new_state* through `transition_on`, in a save
    that commits; gives the profile as moved. Raises ValueError where the table refuses."""
    async with get_connection(db_path) as db:
        profile = await DriverService(db_path).transition_on(
            db, discord_user_id, new_state, test_mode_active=test_mode_active
        )
        await db.commit()
    return profile
