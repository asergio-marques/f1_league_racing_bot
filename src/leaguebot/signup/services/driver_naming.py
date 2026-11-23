"""How a signup names its driver in the log channel and in the posts of its channel.

A real driver is a member of the server and is named as any member is. A fake driver is no
member, so a mention of its account reaches nobody: it is named by its test name, with no
mention, wherever a signup names it (the core specification, "Fake drivers and rosters"). The
league admin who answered for it is the actor of every line, never the driver.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from leaguebot.core.services.drivers.driver_reference import resolve_driver
from leaguebot.framework.utils.log_lines import name_of_member

if TYPE_CHECKING:
    from leaguebot.core.services.drivers.driver_service import DriverService
    from leaguebot.framework.utils.league_bot import LeagueBot


async def fake_name_of(db_path: str, user_id: int | str) -> str | None:
    """The test name of the fake driver holding *user_id*, or None for a real driver or no driver,
    for a job whose change type is handed no driver service."""
    reference = await resolve_driver(db_path, str(user_id))
    return reference.test_name if reference is not None and reference.is_test_driver else None


async def name_of_driver(
    bot: "LeagueBot", drivers: "DriverService", user_id: int | str
) -> tuple[str, bool]:
    """The driver holding *user_id*, named as the log channel names them, and whether it is a fake
    driver: its test name for one, `name_of_member` for any other."""
    fake = await drivers.fake_driver_name(str(user_id))
    if fake is not None:
        return fake, True
    return await name_of_member(bot, int(user_id)), False
