"""A driver's past account leaving the server is not the driver leaving (issue #243, E4).

Only the current account is the driver on Discord. `SignupCog.on_member_remove` reports a
placed driver who leaves; the report must follow the current account, or a driver who moved
account and then tidied the old one away would be logged as having left the league.
"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock

from leaguebot.signup.cogs.signup_cog import SignupCog
from leaguebot.core.db.database import get_connection, run_migrations

SERVER_ID = 2432
PAST, CURRENT = "6101", "6102"


async def _cog(tmp_path) -> SignupCog:
    db_path = os.path.join(str(tmp_path), "leave.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "INSERT INTO driver_profiles (discord_user_id, current_state) "
            "VALUES (?, 'ASSIGNED')",
            (PAST,),
        )
        await db.execute(
            "UPDATE driver_profiles SET discord_user_id = ? WHERE id = ?",
            (CURRENT, cursor.lastrowid),
        )
        await db.commit()
    cog = SignupCog.__new__(SignupCog)
    cog.bot = MagicMock()
    cog.bot.db_path = db_path
    cog.bot.wizard_service.handle_member_remove = AsyncMock()
    cog.bot.output_router.post_log = AsyncMock()
    cog.bot.config_service.get_league_server_id = AsyncMock(return_value=SERVER_ID)
    return cog


def _member(account: str) -> MagicMock:
    member = MagicMock()
    member.id = int(account)
    member.guild.id = SERVER_ID
    member.display_name = "Racer"
    return member


async def test_a_past_account_leaving_is_not_reported(tmp_path):
    cog = await _cog(tmp_path)
    await SignupCog.on_member_remove(cog, _member(PAST))
    cog.bot.output_router.post_log.assert_not_awaited()


async def test_the_current_account_leaving_is_reported(tmp_path):
    cog = await _cog(tmp_path)
    await SignupCog.on_member_remove(cog, _member(CURRENT))
    cog.bot.output_router.post_log.assert_awaited_once()
    assert "Driver left server" in cog.bot.output_router.post_log.await_args.args[0]
