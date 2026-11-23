"""How a signup names its driver wherever it names one: its log lines, the posts of its channel,
the queue's job descriptions and its refusals.

A real driver is a member of the server and is named as any member is. A fake driver is no
member, so a mention of its account reaches nobody: it is named by its test name, with no
mention, wherever a signup names it (the core specification, "Fake drivers and rosters"). The
league admin who answered for it is the actor of every line, never the driver.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from leaguebot.core.services.drivers.driver_reference import resolve_driver
from leaguebot.framework.utils.log_lines import name_of_member

if TYPE_CHECKING:
    from leaguebot.core.services.drivers.driver_service import DriverService
    from leaguebot.framework.utils.league_bot import LeagueBot


def named_in_progress(payload: dict[str, Any]) -> str:
    """How a change's in-progress text names its driver: by the ``fake_name`` its payload carries
    where they are a fake driver, as that text is built from the payload alone, and by a mention
    of their account otherwise."""
    return payload.get("fake_name") or f"<@{payload['user_id']}>"


async def fake_name_of(db_path: str, user_id: int | str) -> str | None:
    """The test name of the fake driver holding *user_id*, or None for a real driver or no driver,
    for a job whose change type is handed no driver service."""
    reference = await resolve_driver(db_path, str(user_id))
    return reference.test_name if reference is not None and reference.is_test_driver else None


async def mention_or_name(db_path: str, user_id: int | str) -> str:
    """How a job's description or a refusal names the driver holding *user_id*: a mention of the
    account for a real driver, the test name for a fake driver, whose account is no member's and
    a mention of it reaches nobody."""
    fake = await fake_name_of(db_path, user_id)
    return f"<@{user_id}>" if fake is None else fake


async def name_of_driver(
    bot: "LeagueBot", drivers: "DriverService", user_id: int | str
) -> tuple[str, bool]:
    """The driver holding *user_id*, named as the log channel names them, and whether it is a fake
    driver: its test name for one, `name_of_member` for any other."""
    fake = await drivers.fake_driver_name(str(user_id))
    if fake is not None:
        return fake, True
    return await name_of_member(bot, int(user_id)), False
