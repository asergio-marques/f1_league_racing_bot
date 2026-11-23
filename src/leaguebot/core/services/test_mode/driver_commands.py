"""Which `/driver` and `/signup` the server shows: the member groups, or the test-mode groups while test mode is on.

Discord's member field cannot carry a fake driver, so while test mode is on `/driver` and `/signup`
are each a test-mode group, whose driver fields take text (`DriverTestCog`, `SignupTestCog`). The
member groups are global, as they always were; the test-mode groups are on the league's server
alone. `swap_test_mode_commands` puts each where the flag calls for and syncs both trees: with test
mode on, the member groups come off the global tree and the test-mode groups go onto the server's;
off, the reverse.

**It is handed the (member, test-mode) group pairs** by the entry point, which gets them from the
cogs it makes: a service never imports a cog, never finds one on the bot, and the swap names no
group of its own: each is removed by its own group's name. **It is handed the league's settings**,
the very ones the bot carries, to tell a bot with no server claimed from one with a claim; the
claimed server itself is reached through `league_guild`, the one route to it, which reads the same
settings off the bot. **It reads the flag itself**, off the database, as the step that runs it
follows the save that set the flag.

**It reports nothing and refuses nothing.** Discord's refusal of a sync is raised as it came, and
a league's server missing from the cache is raised as `GuildUnavailable`, as a role move's is, for
the step that runs it (`Step.on_fault`) or the start-up to report. A swap that fails may leave the
wrong commands in place until the next one, which is accepted: the remedy is a restart.

**The notices name the groups in words** (`COMMANDS_NAMED`), for the notice a factory reset or
`/bot init` adds is built where no swap is: it is the one place core's text names `/signup`, and
the swap never reads it.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence

from discord import app_commands

from leaguebot.core.services.test_mode.test_mode_service import test_mode_on
from leaguebot.framework.db.database import get_connection
from leaguebot.framework.models.change import GuildUnavailable, StepKind, StepResult
from leaguebot.framework.services.posting.league_settings import LeagueSettings
from leaguebot.framework.services.queue.change_types import Step, StepContext, StepView
from leaguebot.framework.utils.league_bot import LeagueBot
from leaguebot.framework.utils.league_server import league_guild

#: The name of the step that swaps them, as the changes that plan it and their outcomes know it.
SWAP_TEST_MODE_COMMANDS = "swap_test_mode_commands"

#: The commands the swap moves, as a notice names them.
COMMANDS_NAMED = "the `/driver` and `/signup` commands"

#: What a swap Discord refused leaves undone, as a change's reply and line say it: the remedy is a
#: restart, never the change run again.
SWAP_PROBLEM = (
    f"{COMMANDS_NAMED} could not be changed; they will be put right when the bot next restarts"
)

#: The pair of groups a command name is shown as: the member group and the test-mode group.
GroupPair = tuple[app_commands.Group, app_commands.Group]


async def swap_test_mode_commands(
    bot: LeagueBot,
    *,
    groups: Sequence[GroupPair],
    league_settings: LeagueSettings,
) -> list[app_commands.AppCommand]:
    """Show the commands the flag calls for and sync both trees. *groups* are the (member,
    test-mode) pairs, each pair's two groups sharing a name. With test mode on, no member group on
    the global tree and each test-mode group on the league's server; off, each member group on the
    global tree and none on the server. Where no server is claimed, the member groups on the global
    tree, synced, and none of the pairs' names on any server the bot is on: the global commands,
    `/bot init` among them, must reach Discord before a league can claim a server, and a factory
    reset under test mode leaves the old league's server showing the test-mode groups, which only
    a restart can take off, there being no claim to find it by. Returns the global commands
    synced. Raises `GuildUnavailable` where a claimed server is not in the cache (the global tree
    synced first where test mode is off), and Discord's refusal of a sync as it came."""
    async with get_connection(bot.db_path) as db:
        test_mode = await test_mode_on(db)
    server_id = await league_settings.get_league_server_id()
    if server_id is None:
        for member, _variant in groups:
            bot.tree.add_command(member, override=True)
        synced = await bot.tree.sync()
        for server in bot.guilds:
            for member, _variant in groups:
                bot.tree.remove_command(member.name, guild=server)
            await bot.tree.sync(guild=server)
        return synced
    guild = await league_guild(bot)
    if guild is None:
        if not test_mode:
            for member, _variant in groups:
                bot.tree.add_command(member, override=True)
            await bot.tree.sync()
        raise GuildUnavailable("the league's server is not in the cache")
    for member, variant in groups:
        if test_mode:
            bot.tree.remove_command(member.name)
            bot.tree.add_command(variant, guild=guild, override=True)
        else:
            bot.tree.add_command(member, override=True)
            bot.tree.remove_command(member.name, guild=guild)
    synced = await bot.tree.sync()
    await bot.tree.sync(guild=guild)
    return synced


def swap_test_mode_commands_step(swap: Callable[[LeagueBot], Awaitable[object]]) -> Step:
    """The job that runs *swap*, the bot's `swap_test_mode_commands` with its group pairs handed it
    (the entry point's), after the save of a change that switches test mode on or off,
    ended by a fault, not stopping the lane (architecture.md, "A job that fails stops its lane"):
    the seat, the season and the flag are the league's record, and the swap is Discord work the
    save does not need, so nothing waits on it. The remedy it names is a restart, not the change
    run again, which would flip test mode or, after a season's end, be refused. A refusal ends the
    job with `{"problem": SWAP_PROBLEM}`, for the change's reply and line to carry
    (`swap_problem`)."""

    async def run(ctx: StepContext) -> StepResult:
        await swap(ctx.bot)
        return StepResult(result={})

    def on_fault(_ctx: StepContext, _error: BaseException) -> StepResult:
        return StepResult(result={"problem": SWAP_PROBLEM})

    async def describe(_ctx: StepContext) -> str:
        return "swapping the `/driver` and `/signup` commands"

    return Step(SWAP_TEST_MODE_COMMANDS, StepKind.ACT, run, describe=describe, on_fault=on_fault)


def swap_problem(views: Sequence[StepView]) -> str | None:
    """What the swap among *views* left undone, or None where it ran well or ran not at all."""
    for view in views:
        if view.name == SWAP_TEST_MODE_COMMANDS and (view.result or {}).get("problem"):
            return SWAP_PROBLEM
    return None


def swap_not_done_line(views: Sequence[StepView]) -> str:
    """The line a change's log entry carries for a swap Discord refused, none where it did not."""
    problem = swap_problem(views)
    return f"\n  not done: {problem}" if problem else ""


def swap_not_done_reply(views: Sequence[StepView]) -> str:
    """The part of a change's reply naming a swap Discord refused, none where it did not."""
    problem = swap_problem(views)
    return f"\n⚠️ Done, but on Discord:\n• {problem}" if problem else ""


__all__ = [
    "COMMANDS_NAMED", "GroupPair", "SWAP_PROBLEM", "SWAP_TEST_MODE_COMMANDS",
    "swap_not_done_line", "swap_not_done_reply", "swap_problem", "swap_test_mode_commands",
    "swap_test_mode_commands_step",
]
