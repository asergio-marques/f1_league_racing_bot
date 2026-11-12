"""The league's server, read on a handed connection (#284, S7).

`league_server_id_on` is the core half of what a module's query of `server_configs` asked when it
needed the league's server to link a message by.
"""
from __future__ import annotations

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.services.settings.config_service import league_server_id_on


async def test_the_server_the_row_holds_is_read(tmp_path):
    path = str(tmp_path / "league.db")
    await run_migrations(path)
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, interaction_channel_id, "
            "log_channel_id) VALUES (4242, 1, 2, 3)"
        )
        await db.commit()
        assert await league_server_id_on(db) == 4242


async def test_no_server_is_read_while_none_is_claimed(tmp_path):
    path = str(tmp_path / "league.db")
    await run_migrations(path)
    async with get_connection(path) as db:
        assert await league_server_id_on(db) is None
        await db.execute("INSERT INTO server_configs (server_id) VALUES (NULL)")
        await db.commit()
        assert await league_server_id_on(db) is None
