"""Which `/driver` the server shows: the member group, or the test-mode group while test mode is on.

Discord's member field cannot carry a fake driver, so while test mode is on `/driver` is the
test-mode group, whose driver fields take text (`DriverTestCog`). The member group is global, as
it always was; the test-mode group is on the league's server alone. `swap_driver_commands` puts
each where the flag calls for and syncs both trees: with test mode on, the member group comes off
the global tree and the test-mode group goes onto the server's; off, the reverse.

**It is handed the two groups** by the entry point, which gets them from the cogs it makes: a
service never imports a cog, and never finds one on the bot. **It reads the flag itself**, off the
database, as the step that runs it follows the save that set the flag.

**It reports nothing and refuses nothing.** Discord's refusal of a sync is raised as it came, and
a league's server missing from the cache is raised as `GuildUnavailable`, as a role move's is, for
the step that runs it (`Step.on_fault`) or the start-up to report. A swap that fails may leave the
wrong `/driver` in place until the next one, which is accepted: the remedy is a restart.
"""

from __future__ import annotations

from discord import app_commands

from leaguebot.framework.db.database import get_connection
from leaguebot.framework.models.change import GuildUnavailable
from leaguebot.framework.utils.league_bot import LeagueBot
from leaguebot.framework.utils.league_server import league_guild

#: The name both groups go by.
DRIVER_GROUP = "driver"


async def swap_driver_commands(
    bot: LeagueBot, *, member: app_commands.Group, variant: app_commands.Group
) -> None:
    """Show the `/driver` the flag calls for and sync both trees: with test mode on, no `/driver`
    on the global tree and the *variant* on the league's server; off, the *member* group on the
    global tree and none on the server. Raises `GuildUnavailable` where the league's server is not
    in the cache, and Discord's refusal of a sync as it came."""
    async with get_connection(bot.db_path) as db:
        cursor = await db.execute("SELECT test_mode_active FROM server_configs")
        row = await cursor.fetchone()
    test_mode = row is not None and bool(row["test_mode_active"])
    guild = await league_guild(bot)
    if guild is None:
        raise GuildUnavailable("the league's server is not in the cache")
    if test_mode:
        bot.tree.remove_command(DRIVER_GROUP)
        bot.tree.add_command(variant, guild=guild, override=True)
    else:
        bot.tree.add_command(member, override=True)
        bot.tree.remove_command(DRIVER_GROUP, guild=guild)
    await bot.tree.sync()
    await bot.tree.sync(guild=guild)
