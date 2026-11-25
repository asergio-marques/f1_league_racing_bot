"""Which `/driver` and `/signup` the server shows follows test mode, swapped as it changes (#600).

Discord's member field cannot carry a fake driver, so while test mode is on `/driver` and
`/signup` are each the test-mode group, whose driver fields take text (D12). The member groups
stay global; turning test mode on takes them off the global tree and puts the test-mode groups on
the league's server, and turning it off reverses both, each tree synced once (D7). The swap is
handed the (member, test-mode) pairs and names no group of its own. The swap reads the flag itself, and runs after the
save of every change that switches test mode on or off: the toggle, and `/season complete`,
`/season abort` and a cancellation of a test season (D6). A swap Discord refuses ends its job on a
fault: the change stands, the lane carries on, and the reply and the log line say the `/driver`
and `/signup` commands will be put right when the bot next restarts (D8).

The swap's own tests run on a bare bot carrying the four cogs, the league's server `SERVER`, and the
flag in the database. The toggle's run on the bot the entry point builds, season 3 in
Configuration, Admin (77) running `/test-mode toggle`; the season's end on
`tests.support.season_league`'s leagues. Every sync is recorded, none reaching Discord.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, call

import discord
import pytest

from leaguebot.framework.db.database import get_connection, run_migrations
from tests.support.change_queue import (
    Clock,
    http_error,
    league_bot,
    league_queue,
    member,
    run_queue,
    seed_server,
    stopped_job,
)

#: The queue's "now", and the league admin who runs the toggle.
NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
ACTOR_ID = 77

#: The groups the swap moves, by name.
_NAMES = ("driver", "signup")


async def _flag_db(tmp_path, *, on: bool) -> str:
    path = str(tmp_path / "swap.db")
    await run_migrations(path)
    await seed_server(path)
    async with get_connection(path) as db:
        await db.execute("UPDATE server_configs SET test_mode_active = ?", (int(on),))
        await db.commit()
    return path


def _signup_group(cog) -> Any:
    """The `/signup` group *cog* declares."""
    [group] = [command for command in cog.get_app_commands() if command.name == "signup"]
    return group


async def _bare_bot(db_path: str):
    """A bot carrying both `/driver` cogs and both `/signup` cogs, its syncs recorded, the
    league's server found by id; with the (member, test-mode) pairs of `/driver` and `/signup`."""
    from discord.ext import commands

    from leaguebot.core.cogs.driver_cog import DriverCog
    from leaguebot.core.cogs.driver_test_cog import DriverTestCog
    from leaguebot.signup.cogs.signup_cog import SignupCog
    from leaguebot.signup.cogs.signup_test_cog import SignupTestCog

    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    member_cog, test_cog = DriverCog(bot), DriverTestCog(bot)
    signup_cog, signup_test_cog = SignupCog(bot), SignupTestCog(bot)
    for cog in (member_cog, test_cog, signup_cog, signup_test_cog):
        await bot.add_cog(cog)
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT server_id FROM server_configs")
        server_id = (await cursor.fetchone())["server_id"]
    guild = discord.Object(id=server_id)
    bot.db_path = db_path
    bot.league_settings = MagicMock(get_league_server_id=AsyncMock(return_value=server_id))
    bot.get_guild = MagicMock(return_value=guild)
    bot.tree.sync = AsyncMock(return_value=[])
    pairs = [
        (member_cog.driver, test_cog.driver),
        (signup_cog.signup, _signup_group(signup_test_cog)),
    ]
    return bot, guild, pairs


async def _swap(bot, pairs) -> Any:
    """The swap, handed *pairs* and the bot's league settings."""
    from leaguebot.core.services.test_mode.driver_commands import swap_test_mode_commands

    return await swap_test_mode_commands(bot, groups=pairs, league_settings=bot.league_settings)


async def _set_flag(db_path: str, on: bool) -> None:
    async with get_connection(db_path) as db:
        await db.execute("UPDATE server_configs SET test_mode_active = ?", (int(on),))
        await db.commit()


def _shown(bot, guild) -> dict[str, tuple[Any, Any]]:
    """For `/driver` and `/signup`, the group the global tree carries, and the one the league's
    server carries."""
    return {
        name: (bot.tree.get_command(name), bot.tree.get_command(name, guild=guild))
        for name in _NAMES
    }


def _called_for(pairs, on: bool) -> dict[str, tuple[Any, Any]]:
    """What `_shown` gives where the flag is *on*: on, no group on the global tree and the
    test-mode group on the server; off, the member group on the global tree and none on the
    server."""
    return {member.name: ((None, variant) if on else (member, None)) for member, variant in pairs}


# ── The swap ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("on", [True, False], ids=["test-mode-on", "test-mode-off"])
async def test_the_swap_shows_the_driver_group_the_flag_calls_for(tmp_path, on):
    """Both `/driver` cogs and both `/signup` cogs are loaded, the member groups on the global
    tree, and the swap is handed the two pairs. With test mode on, the swap leaves no `/driver`
    and no `/signup` on the global tree and both test-mode groups on the league's server. With it
    off, after a swap under test mode, the swap puts both member groups back on the global tree
    and leaves neither on the server. Each time each tree is synced once, and the groups are the
    cogs' own."""
    db_path = await _flag_db(tmp_path, on=True)
    bot, guild, pairs = await _bare_bot(db_path)
    if not on:
        await _swap(bot, pairs)
        await _set_flag(db_path, False)
        bot.tree.sync.reset_mock()

    await _swap(bot, pairs)

    assert _shown(bot, guild) == _called_for(pairs, on)
    assert sorted(map(str, bot.tree.sync.await_args_list)) == sorted(
        map(str, [call(), call(guild=guild)])
    )


async def test_a_sync_discord_refuses_is_raised(tmp_path):
    """Test mode is on and Discord refuses the sync: the swap of `/driver` and `/signup` raises
    Discord's refusal, for the step that runs it to report."""
    db_path = await _flag_db(tmp_path, on=True)
    bot, _guild, pairs = await _bare_bot(db_path)
    bot.tree.sync = AsyncMock(side_effect=http_error())

    with pytest.raises(discord.HTTPException):
        await _swap(bot, pairs)


@pytest.mark.parametrize(
    ("claimed", "on"),
    [(False, True), (False, False), (True, False)],
    ids=["unclaimed-test-mode-on", "unclaimed-test-mode-off", "claimed-server-not-in-cache"],
)
async def test_an_unclaimed_bot_syncs_the_global_tree_with_the_member_driver(
    tmp_path, claimed, on
):
    """A swap under test mode has left no `/driver` and no `/signup` on the global tree. Then
    either no server is claimed (as after a factory reset), with test mode on or off, or the
    league's server is claimed but missing from the bot's cache, with test mode off. The swap puts
    both member groups back on the global tree and syncs it. With no server claimed it raises
    nothing; with the claimed server missing it raises `GuildUnavailable`, having synced the
    global tree first."""
    from leaguebot.framework.models.change import GuildUnavailable

    db_path = await _flag_db(tmp_path, on=True)
    bot, _guild, pairs = await _bare_bot(db_path)
    await _swap(bot, pairs)
    await _set_flag(db_path, on)
    bot.tree.sync.reset_mock()
    if claimed:
        bot.get_guild = MagicMock(return_value=None)
        with pytest.raises(GuildUnavailable):
            await _swap(bot, pairs)
    else:
        bot.league_settings.get_league_server_id = AsyncMock(return_value=None)
        await _swap(bot, pairs)

    assert [bot.tree.get_command(member.name) for member, _ in pairs] == [
        member for member, _ in pairs
    ]
    assert call() in bot.tree.sync.await_args_list


async def test_an_unclaimed_bot_takes_driver_off_every_server_it_is_on(tmp_path):
    """No server is claimed and test mode is on, and the bot is on two servers, each still
    carrying the test-mode `/driver` and the test-mode `/signup`. The swap takes both off both
    servers and syncs each once, besides syncing the global tree once, which carries both member
    groups."""
    from tests.support.change_queue import set_guilds

    db_path = await _flag_db(tmp_path, on=True)
    bot, _guild, pairs = await _bare_bot(db_path)
    servers = [discord.Object(id=5001), discord.Object(id=5002)]
    set_guilds(bot, servers)
    for server in servers:
        for _member, variant in pairs:
            bot.tree.add_command(variant, guild=server)
    bot.league_settings.get_league_server_id = AsyncMock(return_value=None)

    await _swap(bot, pairs)

    for name in _NAMES:
        assert [bot.tree.get_command(name, guild=server) for server in servers] == [None, None]
    assert [bot.tree.get_command(member.name) for member, _ in pairs] == [
        member for member, _ in pairs
    ]
    assert sorted(map(str, bot.tree.sync.await_args_list)) == sorted(
        map(str, [call(), *(call(guild=server) for server in servers)])
    )


# ── After the toggle ──────────────────────────────────────────────────────────


@pytest.fixture
async def toggling(tmp_path):
    """The bot the entry point builds, on the league's server, found by id, with season 3 in
    Configuration and no driver; its queue's "now" pinned."""
    path = str(tmp_path / "toggle.db")
    await run_migrations(path)
    await seed_server(path)
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO seasons (start_date, status, season_number, stage) "
            "VALUES ('2026-09-17', 'SETUP', 3, 'CONFIGURATION')"
        )
        cursor = await db.execute("SELECT server_id FROM server_configs")
        server_id = (await cursor.fetchone())["server_id"]
        await db.commit()
    bot = await league_bot(path, real_config=True)
    guild = discord.Object(id=server_id)
    bot.get_guild = MagicMock(return_value=guild)
    league_queue(bot, path, now=Clock(NOW))
    return bot, path, guild


async def _toggle(bot) -> Any:
    """Admin runs `/test-mode toggle`, and the queue runs what it asked."""
    from leaguebot.core.cogs.test_mode_cog import TestModeCog
    from tests.support.settings_league import interaction
    from tests.support.undecorate import undecorate

    cog = TestModeCog.__new__(TestModeCog)
    cog.bot = bot
    asked = interaction(bot, "test-mode toggle", user=member(ACTOR_ID, "Admin", "Admin#0001"))
    await undecorate(TestModeCog.toggle)(cog, asked)
    await run_queue(bot)
    return asked


def _groups(bot) -> list[tuple[Any, Any]]:
    """The (member, test-mode) pairs of `/driver` and `/signup`, as the bot's own cogs hold them."""
    return [
        (bot.get_cog("DriverCog").driver, bot.get_cog("DriverTestCog").driver),
        (bot.get_cog("SignupCog").signup, _signup_group(bot.get_cog("SignupTestCog"))),
    ]


def _test_mode_groups_shown(bot, guild) -> None:
    """Leave the server as a swap under test mode left it: no member group on the global tree,
    each test-mode group on the league's server."""
    for member, variant in _groups(bot):
        bot.tree.remove_command(member.name)
        bot.tree.add_command(variant, guild=guild)


def _log_lines(bot) -> list[str]:
    from tests.support.settings_league import unruled

    return [unruled(str(line)) for line in bot.log_channel.sent]


@pytest.mark.parametrize("on", [True, False], ids=["on", "off"])
async def test_the_toggle_swaps_driver_after_its_save(toggling, on):
    """Admin turns test mode on, and then, for the second case, off again. Once the queue has run
    the toggle, the server shows the `/driver` and the `/signup` the new flag calls for, the bot's
    own cogs' groups: on, neither on the global tree and each test-mode group on the server; off,
    each member group on the global tree and neither on the server. Both trees were synced, the
    reply ends with the notice to reload Discord, and the queue is not stopped."""
    from leaguebot.core.services.test_mode.driver_commands import RELOAD
    from tests.support.settings_league import last_said

    bot, path, guild = toggling
    asked = await _toggle(bot)
    if not on:
        bot.tree.sync.reset_mock()
        asked = await _toggle(bot)

    assert _shown(bot, guild) == _called_for(_groups(bot), on)
    assert call() in bot.tree.sync.await_args_list
    assert call(guild=guild) in bot.tree.sync.await_args_list
    assert last_said(asked).endswith(f"\n\n{RELOAD}")
    assert await stopped_job(path) is None


async def test_a_refused_toggle_swaps_nothing(toggling):
    """A real driver is on the server, so Admin's toggle to turn test mode on is refused: test
    mode stays off, and nothing is synced."""
    bot, path, _guild = toggling
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO driver_profiles (discord_user_id, current_state) "
            "VALUES ('4242', 'UNASSIGNED')"
        )
        await db.commit()

    await _toggle(bot)

    bot.tree.sync.assert_not_awaited()


async def test_a_swap_discord_refuses_ends_on_a_fault_and_the_toggle_stands(toggling):
    """Discord refuses every sync. Admin turns test mode on: test mode is on all the same, the
    queue is not stopped, and both Admin's reply and a line in the log channel say the `/driver`
    and `/signup` commands could not be changed and will be put right when the bot next restarts, never asking
    for the toggle again, and it does not tell the member to reload Discord, nothing having changed.
    A second toggle, turning it off, then runs."""
    from leaguebot.core.services.test_mode.driver_commands import RELOAD
    from tests.support.settings_league import last_said

    bot, path, _guild = toggling
    bot.tree.sync = AsyncMock(side_effect=http_error())

    asked = await _toggle(bot)

    async with get_connection(path) as db:
        cursor = await db.execute("SELECT test_mode_active FROM server_configs")
        assert (await cursor.fetchone())["test_mode_active"] == 1
    assert await stopped_job(path) is None
    reply = last_said(asked)
    assert RELOAD not in reply
    tail = reply.split("/driver", 1)[1]
    assert "/signup" in tail and "restart" in tail and "toggle" not in tail
    assert any(
        "/driver" in line and "/signup" in line and "restart" in line for line in _log_lines(bot)
    )

    await _toggle(bot)
    async with get_connection(path) as db:
        cursor = await db.execute("SELECT test_mode_active FROM server_configs")
        assert (await cursor.fetchone())["test_mode_active"] == 0


# ── After a season's end ──────────────────────────────────────────────────────


async def _completed(tmp_path, test_mode: bool):
    from tests.support.season_league import complete_season, pending_completion_league

    league = await pending_completion_league(tmp_path, test_mode=test_mode)
    return league, complete_season


async def _aborted(tmp_path, test_mode: bool):
    from tests.support.season_league import abort_season, setup_league

    league = await setup_league(tmp_path, test_mode=test_mode)
    return league, abort_season


async def _cancelled(tmp_path, test_mode: bool):
    from tests.support.season_league import cancel_season, ongoing_league

    league = await ongoing_league(tmp_path, test_mode=test_mode)
    return league, cancel_season


_ENDS = {"complete": _completed, "abort": _aborted, "cancel": _cancelled}


@pytest.mark.parametrize(
    ("end", "test_mode"),
    [
        *(pytest.param(end, True, id=f"{end}-test-season") for end in sorted(_ENDS)),
        *(pytest.param(end, False, id=f"{end}-real-season") for end in sorted(_ENDS)),
    ],
)
async def test_a_test_seasons_end_swaps_driver_back_and_a_real_ones_does_not(
    tmp_path, end, test_mode
):
    """The season ends through `/season complete`, `/season abort` or `/season cancel`, run by
    admin 77 and carried out by the queue. A test season's end, which switches test mode off,
    leaves both member groups on the global tree and no `/driver` or `/signup` on the server,
    where the test-mode groups stood, both trees synced, and its reply ends with the notice to
    reload Discord. A real season's end syncs nothing and its reply has no such notice."""
    from leaguebot.core.services.test_mode.driver_commands import RELOAD
    from tests.support.season_league import reply

    league, ending = await _ENDS[end](tmp_path, test_mode)
    bot, guild = league.bot, league.guild
    if test_mode:
        _test_mode_groups_shown(bot, guild)

    asked = await ending(league)
    await run_queue(bot)

    if test_mode:
        assert _shown(bot, guild) == _called_for(_groups(bot), False)
        assert call() in bot.tree.sync.await_args_list
        assert call(guild=guild) in bot.tree.sync.await_args_list
        assert reply(asked).endswith(f"\n\n{RELOAD}")
    else:
        bot.tree.sync.assert_not_awaited()
        assert RELOAD not in reply(asked)


#: What each end leaves of season 7: completed, cancelled, or deleted by its abort.
_ENDED = {"complete": [{"status": "COMPLETED"}], "cancel": [{"status": "CANCELLED"}], "abort": []}


@pytest.mark.parametrize("end", sorted(_ENDS))
async def test_a_swap_discord_refuses_at_a_test_seasons_end_ends_on_a_fault_and_the_season_ends(
    tmp_path, end
):
    """Season 7 is a test season, the test-mode `/driver` and `/signup` on the league's server,
    and Discord refuses every sync. Admin 77 runs `/season complete`, `/season abort` or
    `/season cancel`, and the queue carries it out: the season has ended all the same (completed,
    deleted, or cancelled), test mode is off, the queue is not stopped, and both the reply and a
    line in the log channel name `/driver`, `/signup` and a restart, never asking for anything to
    be run again; the reply does not tell the member to reload Discord, nothing having changed."""
    from leaguebot.core.services.test_mode.driver_commands import RELOAD
    from tests.support.season_league import SEASON_ID, reply

    league, ending = await _ENDS[end](tmp_path, True)
    bot, guild = league.bot, league.guild
    _test_mode_groups_shown(bot, guild)
    bot.tree.sync = AsyncMock(side_effect=http_error())

    asked = await ending(league)
    await run_queue(bot)

    assert await league.rows("SELECT status FROM seasons WHERE id = ?", SEASON_ID) == _ENDED[end]
    assert (await league.rows("SELECT test_mode_active FROM server_configs"))[0][
        "test_mode_active"
    ] == 0
    assert await stopped_job(league.db_path) is None
    assert RELOAD not in reply(asked)
    logged = [line for posted in _log_lines(bot) for line in posted.splitlines()]
    for told in (reply(asked).splitlines(), logged):
        named = [line for line in told if "/driver" in line]
        assert named and all(
            "/signup" in line and "restart" in line and "again" not in line for line in named
        )
