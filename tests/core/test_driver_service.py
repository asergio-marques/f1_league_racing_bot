"""`DriverService` — listing the drivers who stand in given states.

Issue #283. The signup window's close returns the drivers still filling in the wizard, and reads
which they are through core's driver service, the owner of `driver_profiles`, rather than
selecting from the table itself (`docs/design/architecture.md`, "Each table is read and written
by its owner alone").
"""
from __future__ import annotations

import os


from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.models.driver_profile import DriverState
from leaguebot.core.services.driver_service import DriverService


async def test_drivers_in_states_are_listed(tmp_path):
    """Drivers 101 and 103 are part-way through signing up (Pending Signup Completion), 102 is
    approved and waiting for a seat (Unassigned), and 104 is Not Signed Up. Asked for the drivers
    in Pending Signup Completion, the driver service gives 101 and 103 alone."""
    db_path = os.path.join(str(tmp_path), "driver_service.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        for user_id, state in (
            ("101", DriverState.PENDING_SIGNUP_COMPLETION),
            ("102", DriverState.UNASSIGNED),
            ("103", DriverState.PENDING_SIGNUP_COMPLETION),
            ("104", DriverState.NOT_SIGNED_UP),
        ):
            await db.execute(
                "INSERT INTO driver_profiles (discord_user_id, current_state) VALUES (?, ?)",
                (user_id, state.value),
            )
        await db.commit()

    ids = await DriverService(db_path).ids_in_states({DriverState.PENDING_SIGNUP_COMPLETION})

    assert sorted(ids) == ["101", "103"]
