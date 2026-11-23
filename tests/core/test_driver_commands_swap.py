"""Which `/driver` the server shows follows test mode, swapped as it changes (#600).

Discord's member field cannot carry a fake driver, so while test mode is on `/driver` is the
test-mode group, whose driver fields take text. The member group stays global; turning test mode
on takes it off the global tree and puts the test-mode group on the league's server, and turning
it off reverses both, each tree synced (D7). The swap reads the flag itself, and runs after the
save of every change that switches test mode on or off: the toggle, and `/season complete`,
`/season abort` and a cancellation of a test season (D6). A swap Discord refuses ends its job on a
fault: the change stands, the lane carries on, and the reply and the log line say `/driver` will
be put right when the bot next restarts (D8).

The swap's own tests run on a bare bot carrying both cogs, the league's server `SERVER`, and the
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

PENDING = "#600: no `/driver` swap follows test mode"

#: The queue's "now", and the league admin who runs the toggle.
NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
ACTOR_ID = 77


async def _flag_db(tmp_path, *, on: bool) -> str:
    path = str(tmp_path / "swap.db")
    await run_migrations(path)
    await seed_server(path)
    async with get_connection(path) as db:
        await db.execute("UPDATE server_configs SET test_mode_active = ?", (int(on),))
        await db.commit()
    return path


async def _bare_bot(db_path: str):
    """A bot carrying both `/driver` cogs, its syncs recorded, the league's server found by id."""
    from discord.ext import commands

    from leaguebot.core.cogs.driver_cog import DriverCog
    from leaguebot.core.cogs.driver_test_cog import DriverTestCog

    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    member_cog, test_cog = DriverCog(bot), DriverTestCog(bot)
    await bot.add_cog(member_cog)
    await bot.add_cog(test_cog)
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT server_id FROM server_configs")
        server_id = (await cursor.fetchone())["server_id"]
    guild = discord.Object(id=server_id)
    bot.db_path = db_path
    bot.league_settings = MagicMock(get_league_server_id=AsyncMock(return_value=server_id))
    bot.get_guild = MagicMock(return_value=guild)
    bot.tree.sync = AsyncMock(return_value=[])
    return bot, guild, member_cog.driver, test_cog.driver


async def _set_flag(db_path: str, on: bool) -> None:
    async with get_connection(db_path) as db:
        await db.execute("UPDATE server_configs SET test_mode_active = ?", (int(on),))
        await db.commit()


def _shown(bot, guild) -> tuple[Any, Any]:
    """The `/driver` the global tree carries, and the one the league's server carries."""
    return bot.tree.get_command("driver"), bot.tree.get_command("driver", guild=guild)


# ── The swap ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("on", [True, False], ids=["test-mode-on", "test-mode-off"])
async def test_the_swap_shows_the_driver_group_the_flag_calls_for(tmp_path, on):
    """Both `/driver` cogs are loaded, the member group on the global tree. With test mode on, the
    swap leaves no `/driver` on the global tree and the test-mode group on the league's server.
    With it off, after a swap under test mode, the swap puts the member group back on the global
    tree and leaves none on the server. Each time both trees are synced, and the groups are the
    cogs' own."""
    from leaguebot.core.services.test_mode.driver_commands import swap_driver_commands

    db_path = await _flag_db(tmp_path, on=True)
    bot, guild, member_group, test_group = await _bare_bot(db_path)
    if not on:
        await swap_driver_commands(bot, member=member_group, variant=test_group)
        await _set_flag(db_path, False)
        bot.tree.sync.reset_mock()

    await swap_driver_commands(bot, member=member_group, variant=test_group)

    assert _shown(bot, guild) == ((None, test_group) if on else (member_group, None))
    assert sorted(map(str, bot.tree.sync.await_args_list)) == sorted(
        map(str, [call(), call(guild=guild)])
    )


async def test_a_sync_discord_refuses_is_raised(tmp_path):
    """Test mode is on and Discord refuses the sync: the swap raises Discord's refusal, for the
    step that runs it to report."""
    from leaguebot.core.services.test_mode.driver_commands import swap_driver_commands

    db_path = await _flag_db(tmp_path, on=True)
    bot, _guild, member_group, test_group = await _bare_bot(db_path)
    bot.tree.sync = AsyncMock(side_effect=http_error())

    with pytest.raises(discord.HTTPException):
        await swap_driver_commands(bot, member=member_group, variant=test_group)


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


def _groups(bot) -> tuple[Any, Any]:
    """The member group and the test-mode group, as the bot's two `/driver` cogs hold them."""
    return bot.get_cog("DriverCog").driver, bot.get_cog("DriverTestCog").driver


def _log_lines(bot) -> list[str]:
    from tests.support.settings_league import unruled

    return [unruled(str(line)) for line in bot.log_channel.sent]


@pytest.mark.xfail(strict=True, reason=PENDING)
@pytest.mark.parametrize("on", [True, False], ids=["on", "off"])
async def test_the_toggle_swaps_driver_after_its_save(toggling, on):
    """Admin turns test mode on, and then, for the second case, off again. Once the queue has run
    the toggle, the server shows the `/driver` the new flag calls for, the bot's own cogs' groups:
    on, none on the global tree and the test-mode group on the server; off, the member group on
    the global tree and none on the server. Both trees were synced, the reply says nothing of
    `/driver`, and the queue is not stopped."""
    from tests.support.settings_league import last_said

    bot, path, guild = toggling
    asked = await _toggle(bot)
    if not on:
        bot.tree.sync.reset_mock()
        asked = await _toggle(bot)

    member_group, test_group = _groups(bot)
    assert _shown(bot, guild) == ((None, test_group) if on else (member_group, None))
    assert call() in bot.tree.sync.await_args_list
    assert call(guild=guild) in bot.tree.sync.await_args_list
    assert "/driver" not in last_said(asked)
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


@pytest.mark.xfail(strict=True, reason=PENDING)
async def test_a_swap_discord_refuses_ends_on_a_fault_and_the_toggle_stands(toggling):
    """Discord refuses every sync. Admin turns test mode on: test mode is on all the same, the
    queue is not stopped, and both Admin's reply and a line in the log channel say the `/driver`
    commands could not be changed and will be put right when the bot next restarts, never asking
    for the toggle again. A second toggle, turning it off, then runs."""
    from tests.support.settings_league import last_said

    bot, path, _guild = toggling
    bot.tree.sync = AsyncMock(side_effect=http_error())

    asked = await _toggle(bot)

    async with get_connection(path) as db:
        cursor = await db.execute("SELECT test_mode_active FROM server_configs")
        assert (await cursor.fetchone())["test_mode_active"] == 1
    assert await stopped_job(path) is None
    reply = last_said(asked)
    tail = reply.split("/driver", 1)[1]
    assert "restart" in tail and "toggle" not in tail
    assert any("/driver" in line and "restart" in line for line in _log_lines(bot))

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
        *(pytest.param(end, True, marks=pytest.mark.xfail(strict=True, reason=PENDING),
                       id=f"{end}-test-season") for end in sorted(_ENDS)),
        *(pytest.param(end, False, id=f"{end}-real-season") for end in sorted(_ENDS)),
    ],
)
async def test_a_test_seasons_end_swaps_driver_back_and_a_real_ones_does_not(
    tmp_path, end, test_mode
):
    """The season ends through `/season complete`, `/season abort` or `/season cancel`, run by
    admin 77 and carried out by the queue. A test season's end, which switches test mode off,
    leaves the member group on the global tree and no `/driver` on the server, where the
    test-mode group stood, both trees synced. A real season's end syncs nothing."""
    league, ending = await _ENDS[end](tmp_path, test_mode)
    bot, guild = league.bot, league.guild
    if test_mode:
        bot.tree.remove_command("driver")
        bot.tree.add_command(bot.get_cog("DriverTestCog").driver, guild=guild)

    await ending(league)
    await run_queue(bot)

    if test_mode:
        assert _shown(bot, guild) == (bot.get_cog("DriverCog").driver, None)
        assert call() in bot.tree.sync.await_args_list
        assert call(guild=guild) in bot.tree.sync.await_args_list
    else:
        bot.tree.sync.assert_not_awaited()


#: What each end leaves of season 7: completed, cancelled, or deleted by its abort.
_ENDED = {"complete": [{"status": "COMPLETED"}], "cancel": [{"status": "CANCELLED"}], "abort": []}


@pytest.mark.xfail(strict=True, reason=PENDING)
@pytest.mark.parametrize("end", sorted(_ENDS))
async def test_a_swap_discord_refuses_at_a_test_seasons_end_ends_on_a_fault_and_the_season_ends(
    tmp_path, end
):
    """Season 7 is a test season, the test-mode group on the league's server, and Discord refuses
    every sync. Admin 77 runs `/season complete`, `/season abort` or `/season cancel`, and the
    queue carries it out: the season has ended all the same (completed, deleted, or cancelled),
    test mode is off, the queue is not stopped, and both the reply and a line in the log channel
    name `/driver` and a restart, never asking for anything to be run again."""
    from tests.support.season_league import SEASON_ID, reply

    league, ending = await _ENDS[end](tmp_path, True)
    bot, guild = league.bot, league.guild
    bot.tree.remove_command("driver")
    bot.tree.add_command(bot.get_cog("DriverTestCog").driver, guild=guild)
    bot.tree.sync = AsyncMock(side_effect=http_error())

    asked = await ending(league)
    await run_queue(bot)

    assert await league.rows("SELECT status FROM seasons WHERE id = ?", SEASON_ID) == _ENDED[end]
    assert (await league.rows("SELECT test_mode_active FROM server_configs"))[0][
        "test_mode_active"
    ] == 0
    assert await stopped_job(league.db_path) is None
    logged = [line for posted in _log_lines(bot) for line in posted.splitlines()]
    for told in (reply(asked).splitlines(), logged):
        named = [line for line in told if "/driver" in line]
        assert named and all("restart" in line and "again" not in line for line in named)
