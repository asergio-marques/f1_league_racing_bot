"""Core server configuration — `/bot init` and the four settings beside it.

Four properties are pinned here, each of which a plausible tidy-up would undo.

**The four setting commands are not bound to the interaction channel.** The tier guards
admit a command only in the configured interaction channel and only to a holder of one of
the league's two roles. These commands exist to repair those very settings, so guarding them
would lock a league out of the failure they are for — a deleted interaction channel, or an
admin role removed from the server, would be unrecoverable short of wiping the
configuration. The tests below invoke them from the wrong channel, by a user holding no
role, and require them to work anyway.

**Each writes exactly one column.** `save_server_config` once carried a whole `ServerConfig`
into an upsert, which is how `/bot init force:True` came to switch test mode off: the model
it was handed had never read the stored row, so `test_mode_active` defaulted to False and
overwrote a live setting. The setters write a single column so no such carry-over is
possible.

**`/bot init` runs once.** A second run is refused rather than overwriting, which is what
makes the clobber above unreachable rather than merely corrected.

**Each is audited as well as logged** (issue #371). The log line is all these once wrote, which
left the database no record of who changed who may command and govern the bot, nor of what the
setting had been. The audit keeps the value each command replaced.
"""
from __future__ import annotations

import json
import logging
import os
import re
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from leaguebot.core.cogs.bot_cog import BotCog
from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.services.config_service import ConfigService
from tests.support.undecorate import undecorate

SERVER_ID = 4242

#: The configured interaction channel and role. Every setting-command test below deliberately
#: acts from somewhere else, to prove the guard is absent.
CONFIGURED_CHANNEL = 111
CONFIGURED_ROLE = 222
CONFIGURED_LOG = 333
CONFIGURED_ADMIN_ROLE = 444


def _unwrap(cmd):
    """Strip `bot_setup_only` and return the command body.

    Unwrapping cannot by itself notice a guard being changed — it would simply strip the new
    layer instead — so the tier these commands sit at, and their exemption from the
    interaction channel, are pinned separately below.
    """
    return undecorate(cmd)


async def _make_db(tmp_path) -> str:
    db_path = os.path.join(str(tmp_path), "test.db")
    await run_migrations(db_path)
    return db_path


async def _seed_config(db_path: str, *, test_mode: int = 1) -> None:
    """A fully configured server, with test mode and both module flags on.

    They are on so that a command writing more than its own column shows up as a change to
    one of them.
    """
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id, league_admin_role_id, "
            "test_mode_active, weather_module_enabled, signup_module_enabled) "
            "VALUES (?, ?, ?, ?, ?, ?, 1, 1)",
            (SERVER_ID, CONFIGURED_ROLE, CONFIGURED_CHANNEL, CONFIGURED_LOG,
             CONFIGURED_ADMIN_ROLE, test_mode),
        )
        await db.commit()


async def _row(db_path: str) -> dict:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT * FROM server_configs WHERE server_id = ?", (SERVER_ID,)
        )
        row = await cursor.fetchone()
    return dict(row) if row is not None else {}


async def _audit_rows(db_path: str) -> list[dict]:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT actor_id, division_id, change_type, old_value, new_value "
            "FROM audit_entries"
        )
        return [dict(r) for r in await cursor.fetchall()]


def _bot(db_path: str) -> MagicMock:
    bot = MagicMock()
    # The two channel settings check the channel is not already doing another job, which
    # reads the database directly rather than through a service.
    bot.db_path = db_path
    bot.config_service = ConfigService(db_path)
    bot.output_router.post_log = AsyncMock()
    return bot


def _interaction(*, channel_id: int = 999) -> MagicMock:
    """An interaction from the *wrong* channel by default, by a user holding no role."""
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.channel_id = channel_id
    interaction.user = MagicMock(spec=discord.Member)
    interaction.user.id = 7
    interaction.user.display_name = "admin"
    interaction.user.roles = []
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    # Answered once replied to or deferred, as Discord's is: a bare mock would say it always
    # was, and a refusal or a failure would answer by the wrong route.
    interaction.response.is_done = MagicMock(
        side_effect=lambda: bool(
            interaction.response.send_message.await_count
            or interaction.response.defer.await_count
        )
    )
    return interaction


def _channel(channel_id: int) -> MagicMock:
    channel = MagicMock()
    channel.id = channel_id
    channel.mention = f"<#{channel_id}>"
    return channel


def _role(role_id: int) -> MagicMock:
    role = MagicMock()
    role.id = role_id
    role.name = "Stewards"
    role.mention = f"<@&{role_id}>"
    return role


def _refusable(bot: MagicMock, interaction: MagicMock, command: str) -> MagicMock:
    """*interaction* as `/<command>` from the bot, answering as Discord's does (#482).

    Its client is the bot, whose log channel a refusal is recorded in, and its response knows
    whether it has been used, so that a refusal answers by `response` or `followup` as the
    command left it.
    """
    state = {"done": False}

    async def _answer(*_args, **_kwargs):
        state["done"] = True

    interaction.client = bot
    interaction.command.qualified_name = command
    interaction.response.is_done = MagicMock(side_effect=lambda: state["done"])
    interaction.response.send_message = AsyncMock(side_effect=_answer)
    interaction.response.defer = AsyncMock(side_effect=_answer)
    interaction.followup.send = AsyncMock()
    return interaction


#: The marks a reply opens with, which a refusal's log line leaves out of its reason.
_REPLY_MARKS = ("❌", "⛔", "⚠️", "⚠", "ℹ️", "ℹ", "⏳")


def _reason(reply: str) -> str:
    """The reason a refusal's log line gives: the reply's first line, without its mark."""
    first = reply.strip().splitlines()[0].strip()
    for mark in _REPLY_MARKS:
        if first.startswith(mark):
            return first[len(mark):].lstrip(chr(0xFE0F)).strip()
    return first


def _log_lines(bot: MagicMock) -> list[str]:
    """Every line written to the league's log channel."""
    return [str(call.args[0]) for call in bot.output_router.post_log.await_args_list]


# ── /bot init runs once ───────────────────────────────────────────────────


async def test_bot_init_configures_an_unconfigured_server(tmp_path):
    db_path = await _make_db(tmp_path)
    bot = _bot(db_path)
    cog = BotCog(bot)
    interaction = _interaction()

    await _unwrap(cog.handle_bot_init)(
        cog,
        interaction,
        _role(CONFIGURED_ROLE),
        _role(CONFIGURED_ADMIN_ROLE),
        _channel(CONFIGURED_CHANNEL),
        _channel(CONFIGURED_LOG),
    )

    row = await _row(db_path)
    assert row["interaction_role_id"] == CONFIGURED_ROLE
    assert row["league_admin_role_id"] == CONFIGURED_ADMIN_ROLE
    assert row["interaction_channel_id"] == CONFIGURED_CHANNEL
    assert row["log_channel_id"] == CONFIGURED_LOG


async def test_bot_init_is_audited_with_the_four_settings(tmp_path):
    """Issue #371. Nothing stood before the claim, so every value it replaced is null."""
    db_path = await _make_db(tmp_path)
    cog = BotCog(_bot(db_path))

    await _unwrap(cog.handle_bot_init)(
        cog,
        _interaction(),
        _role(CONFIGURED_ROLE),
        _role(CONFIGURED_ADMIN_ROLE),
        _channel(CONFIGURED_CHANNEL),
        _channel(CONFIGURED_LOG),
    )

    (row,) = await _audit_rows(db_path)
    assert row["change_type"] == "BOT_INITIALISED"
    assert row["actor_id"] == 7
    assert row["division_id"] is None
    new = {
        "server_id": SERVER_ID,
        "interaction_role_id": CONFIGURED_ROLE,
        "league_admin_role_id": CONFIGURED_ADMIN_ROLE,
        "interaction_channel_id": CONFIGURED_CHANNEL,
        "log_channel_id": CONFIGURED_LOG,
    }
    assert json.loads(row["new_value"]) == new
    assert json.loads(row["old_value"]) == dict.fromkeys(new)


async def _init_configured(tmp_path) -> tuple[MagicMock, MagicMock]:
    """Run a first `/bot init` with the four configured settings: the bot and the interaction."""
    db_path = await _make_db(tmp_path)
    bot = _bot(db_path)
    cog = BotCog(bot)
    interaction = _interaction()
    admin_role = _role(CONFIGURED_ADMIN_ROLE)
    admin_role.name = "League Admins"

    await _unwrap(cog.handle_bot_init)(
        cog,
        interaction,
        _role(CONFIGURED_ROLE),
        admin_role,
        _channel(CONFIGURED_CHANNEL),
        _channel(CONFIGURED_LOG),
    )
    return bot, interaction


async def test_bot_init_s_line_names_the_league_admin_role(tmp_path):
    """The line lists all four settings it saved, the league admin role among them (#482)."""
    bot, _interaction_ = await _init_configured(tmp_path)

    [line] = _log_lines(bot)
    assert line.startswith("admin (<@7>) | /bot init | Success")
    assert f"League Admins (<@&{CONFIGURED_ADMIN_ROLE}>)" in line
    assert f"(<@&{CONFIGURED_ROLE}>)" in line
    assert f"<#{CONFIGURED_CHANNEL}>" in line and f"<#{CONFIGURED_LOG}>" in line


async def test_bot_init_s_reply_names_the_league_admin_role(tmp_path):
    """The confirmation lists the league admin role beside the other three settings (#482, F3)."""
    _bot_, interaction = await _init_configured(tmp_path)

    reply = interaction.response.send_message.call_args.args[0]
    assert f"<@&{CONFIGURED_ADMIN_ROLE}>" in reply
    assert f"<@&{CONFIGURED_ROLE}>" in reply
    assert f"<#{CONFIGURED_CHANNEL}>" in reply and f"<#{CONFIGURED_LOG}>" in reply


@pytest.mark.xfail(strict=True, reason="#482: /bot init is not refused while a clean-up runs")
async def test_bot_init_is_refused_while_a_factory_reset_is_cleaning_up(tmp_path, caplog):
    """A new `/bot init` would claim the server while the last reset is still deleting the bot's
    messages and roles there (#482, F4). It is refused, with a reply to run it again once the
    clean-up has finished. No server is claimed, so there is no log channel to record it in:
    the refusal goes to the host's log alone.
    """
    import asyncio

    caplog.set_level(logging.INFO, logger="leaguebot.core.cogs.bot_cog")
    db_path = await _make_db(tmp_path)
    bot = _bot(db_path)
    cog = BotCog(bot)
    cog._clean_up = asyncio.get_running_loop().create_future()
    interaction = _refusable(bot, _interaction(), "bot init")

    try:
        await _unwrap(cog.handle_bot_init)(
            cog, interaction, _role(900), _role(903), _channel(901), _channel(902)
        )
    finally:
        cog._clean_up.cancel()

    reply = interaction.response.send_message.call_args.args[0]
    assert "clean" in reply.lower() and "again" in reply.lower()
    assert await bot.config_service.get_league_server_id() is None
    assert await _audit_rows(db_path) == []
    bot.output_router.post_log.assert_not_awaited()
    assert any(
        record.name == "leaguebot.core.cogs.bot_cog"
        and record.levelno >= logging.INFO
        and "bot init" in record.getMessage()
        and re.search(r"(?<!\d)7(?!\d)", record.getMessage())
        for record in caplog.records
    ), [record.getMessage() for record in caplog.records]


async def test_bot_init_refuses_a_second_run_and_names_the_four_commands(tmp_path):
    db_path = await _make_db(tmp_path)
    await _seed_config(db_path)
    bot = _bot(db_path)
    cog = BotCog(bot)
    interaction = _interaction()

    await _unwrap(cog.handle_bot_init)(
        cog, interaction, _role(900), _role(903), _channel(901), _channel(902)
    )

    reply = interaction.response.send_message.call_args.args[0]
    assert "runs once" in reply
    assert "/bot log-channel" in reply
    assert "/bot interaction-channel" in reply
    assert "/bot interaction-role" in reply
    assert "/bot admin-role" in reply

    row = await _row(db_path)
    assert row["interaction_role_id"] == CONFIGURED_ROLE
    assert row["league_admin_role_id"] == CONFIGURED_ADMIN_ROLE
    assert row["interaction_channel_id"] == CONFIGURED_CHANNEL
    assert row["log_channel_id"] == CONFIGURED_LOG
    assert await _audit_rows(db_path) == []


async def test_a_second_bot_init_does_not_switch_test_mode_off(tmp_path):
    """The regression this change exists to make unreachable.

    `/bot init force:True` built a `ServerConfig` without reading the stored row, so
    `test_mode_active` defaulted to False and the upsert wrote it — leaving test mode off
    with the test drivers still seated, and nothing said so.
    """
    db_path = await _make_db(tmp_path)
    await _seed_config(db_path, test_mode=1)
    cog = BotCog(_bot(db_path))

    await _unwrap(cog.handle_bot_init)(
        cog, _interaction(), _role(900), _role(903), _channel(901), _channel(902)
    )

    assert (await _row(db_path))["test_mode_active"] == 1


async def test_bot_init_on_a_second_server_is_refused_and_writes_nothing(tmp_path):
    """Issue #244: one bot serves one league."""
    db_path = await _make_db(tmp_path)
    await _seed_config(db_path)
    bot = _bot(db_path)
    cog = BotCog(bot)
    interaction = _interaction()
    interaction.guild_id = SERVER_ID + 1

    await _unwrap(cog.handle_bot_init)(
        cog, interaction, _role(900), _role(903), _channel(901), _channel(902)
    )

    reply = interaction.response.send_message.call_args.args[0]
    assert "another server" in reply
    async with get_connection(db_path) as db:
        rows = await (await db.execute("SELECT server_id FROM server_configs")).fetchall()
    assert [r["server_id"] for r in rows] == [SERVER_ID]
    assert await _audit_rows(db_path) == []


async def test_a_pack_frees_the_server_for_another(tmp_path):
    """The claim is the configuration row's `server_id`, and `/bot pack` clears it (#247)."""
    db_path = await _make_db(tmp_path)
    await _seed_config(db_path)
    bot = _packing_bot(db_path)
    cog = BotCog(bot)
    await _unwrap(cog.handle_pack)(
        cog, _deferred(_interaction(channel_id=CONFIGURED_CHANNEL)), "CONFIRM"
    )
    interaction = _interaction()
    interaction.guild_id = SERVER_ID + 1

    await _unwrap(cog.handle_bot_init)(
        cog, interaction, _role(900), _role(903), _channel(901), _channel(902)
    )

    assert await bot.config_service.get_league_server_id() == SERVER_ID + 1


async def test_a_lost_race_to_another_server_names_the_other_server(tmp_path):
    db_path = await _make_db(tmp_path)
    bot = _bot(db_path)
    real = bot.config_service

    async def _lose(cfg):
        await _seed_config(db_path)  # the other server wins in between
        return await ConfigService.save_server_config(real, cfg)

    bot.config_service = MagicMock(wraps=real)
    bot.config_service.get_league_server_id = real.get_league_server_id
    bot.config_service.get_server_config = real.get_server_config
    bot.config_service.save_server_config = _lose
    cog = BotCog(bot)
    interaction = _interaction()
    interaction.guild_id = SERVER_ID + 1

    await _unwrap(cog.handle_bot_init)(
        cog, interaction, _role(900), _role(903), _channel(901), _channel(902)
    )

    assert "another server" in interaction.response.send_message.call_args.args[0]
    assert await real.get_league_server_id() == SERVER_ID


async def test_save_server_config_will_not_overwrite_an_existing_row(tmp_path):
    """The insert-only contract, at the layer that enforces it."""
    from leaguebot.core.models.server_config import ServerConfig

    db_path = await _make_db(tmp_path)
    await _seed_config(db_path)
    service = ConfigService(db_path)

    created = await service.save_server_config(
        ServerConfig(
            server_id=SERVER_ID,
            interaction_role_id=1,
            interaction_channel_id=2,
            log_channel_id=3,
        )
    )

    assert created is False
    assert (await _row(db_path))["log_channel_id"] == CONFIGURED_LOG


async def test_save_server_config_will_not_claim_a_second_server(tmp_path):
    """Issue #244: one bot serves one league, and the first server set up is the league's."""
    from leaguebot.core.models.server_config import ServerConfig

    db_path = await _make_db(tmp_path)
    await _seed_config(db_path)
    service = ConfigService(db_path)

    created = await service.save_server_config(
        ServerConfig(
            server_id=SERVER_ID + 1,
            interaction_role_id=1,
            interaction_channel_id=2,
            log_channel_id=3,
        )
    )

    assert created is False
    assert await service.get_league_server_id() == SERVER_ID


async def test_no_server_is_the_league_s_before_bot_init(tmp_path):
    assert await ConfigService(await _make_db(tmp_path)).get_league_server_id() is None


# ── The four settings ─────────────────────────────────────────────────────

#: (command attribute, column, factory, the id it sets)
_SETTINGS = [
    ("handle_log_channel", "log_channel_id", _channel, 555),
    ("handle_interaction_channel", "interaction_channel_id", _channel, 556),
    ("handle_interaction_role", "interaction_role_id", _role, 557),
    ("handle_admin_role", "league_admin_role_id", _role, 558),
]

#: column → (the audit's change type, its value key, the value the seed holds)
_AUDITED = {
    "log_channel_id": ("LOG_CHANNEL_SET", "channel_id", CONFIGURED_LOG),
    "interaction_channel_id": ("INTERACTION_CHANNEL_SET", "channel_id", CONFIGURED_CHANNEL),
    "interaction_role_id": ("INTERACTION_ROLE_SET", "role_id", CONFIGURED_ROLE),
    "league_admin_role_id": ("LEAGUE_ADMIN_ROLE_SET", "role_id", CONFIGURED_ADMIN_ROLE),
}


@pytest.mark.parametrize(
    "attribute,column,factory,new_id", _SETTINGS, ids=[s[1] for s in _SETTINGS]
)
async def test_a_setting_command_writes_only_its_own_column(
    tmp_path, attribute, column, factory, new_id
):
    db_path = await _make_db(tmp_path)
    await _seed_config(db_path)
    cog = BotCog(_bot(db_path))
    before = await _row(db_path)

    await _unwrap(getattr(cog, attribute))(cog, _interaction(), factory(new_id))

    after = await _row(db_path)
    assert after[column] == new_id
    assert {k: v for k, v in after.items() if k != column} == {
        k: v for k, v in before.items() if k != column
    }


@pytest.mark.parametrize(
    "attribute,column,factory,new_id", _SETTINGS, ids=[s[1] for s in _SETTINGS]
)
async def test_a_setting_command_is_audited_with_the_value_it_replaced(
    tmp_path, attribute, column, factory, new_id
):
    """Issue #371: the log line alone left no record of who changed who governs the bot."""
    db_path = await _make_db(tmp_path)
    await _seed_config(db_path)
    cog = BotCog(_bot(db_path))
    change_type, key, old_id = _AUDITED[column]

    await _unwrap(getattr(cog, attribute))(cog, _interaction(), factory(new_id))

    (row,) = await _audit_rows(db_path)
    assert row["change_type"] == change_type
    assert row["actor_id"] == 7
    assert row["division_id"] is None
    assert json.loads(row["old_value"]) == {key: old_id}
    assert json.loads(row["new_value"]) == {key: new_id}


@pytest.mark.parametrize(
    "attribute,column,factory,new_id", _SETTINGS, ids=[s[1] for s in _SETTINGS]
)
async def test_a_setting_command_works_outside_the_interaction_channel(
    tmp_path, attribute, column, factory, new_id
):
    """Guarding these on the settings they repair would make the failure unrecoverable."""
    db_path = await _make_db(tmp_path)
    await _seed_config(db_path)
    cog = BotCog(_bot(db_path))

    # The whole decorator chain, not the unwrapped body: a channel-bound guard added later
    # would refuse this interaction, and refusing it is the failure this test is for.
    interaction = _interaction(channel_id=CONFIGURED_CHANNEL + 12345)
    await getattr(cog, attribute).callback(cog, interaction, factory(new_id))

    assert (await _row(db_path))[column] == new_id
    assert "✅" in interaction.response.send_message.call_args.args[0]


@pytest.mark.parametrize(
    "attribute,column,factory,new_id", _SETTINGS, ids=[s[1] for s in _SETTINGS]
)
async def test_a_setting_command_refuses_an_unconfigured_server(
    tmp_path, attribute, column, factory, new_id
):
    db_path = await _make_db(tmp_path)
    cog = BotCog(_bot(db_path))
    interaction = _interaction()

    await _unwrap(getattr(cog, attribute))(cog, interaction, factory(new_id))

    assert "/bot init" in interaction.response.send_message.call_args.args[0]
    assert await _row(db_path) == {}
    assert await _audit_rows(db_path) == []


@pytest.mark.parametrize(
    "attribute,column,factory,new_id", _SETTINGS, ids=[s[1] for s in _SETTINGS]
)
async def test_a_setting_command_refuses_a_member_of_neither_tier(
    tmp_path, attribute, column, factory, new_id
):
    """`bot_setup_only` is the whole of the gate on these, so it had better be on."""
    db_path = await _make_db(tmp_path)
    await _seed_config(db_path)
    cog = BotCog(_bot(db_path))

    interaction = _interaction()
    interaction.user.guild_permissions.administrator = False
    # The guard answers before anything else has: the interaction is still unanswered, and
    # its client is the bot, whose log channel a refusal is recorded in.
    interaction.response.is_done = MagicMock(return_value=False)
    interaction.client = cog.bot

    # The decorated command, not the unwrapped body.
    await getattr(cog, attribute).callback(cog, interaction, factory(new_id))

    reply = interaction.response.send_message.call_args.args[0]
    assert "Administrator" in reply
    assert (await _row(db_path))[column] != new_id
    assert await _audit_rows(db_path) == []


@pytest.mark.parametrize(
    "attribute,column,factory,new_id", _SETTINGS, ids=[s[1] for s in _SETTINGS]
)
async def test_a_setting_command_admits_the_league_admin_role(
    tmp_path, attribute, column, factory, new_id
):
    """The role is the tier; Administrator is only the way back when the role is gone."""
    db_path = await _make_db(tmp_path)
    await _seed_config(db_path)
    cog = BotCog(_bot(db_path))

    interaction = _interaction()
    interaction.user.guild_permissions.administrator = False
    interaction.user.roles = [_role(CONFIGURED_ADMIN_ROLE)]

    await getattr(cog, attribute).callback(cog, interaction, factory(new_id))

    assert (await _row(db_path))[column] == new_id


async def test_a_setting_command_admits_an_administrator_before_the_bot_is_configured(
    tmp_path,
):
    """The bootstrap. `/bot init` has no configuration to read a role out of."""
    db_path = await _make_db(tmp_path)
    cog = BotCog(_bot(db_path))

    interaction = _interaction()
    interaction.user.guild_permissions.administrator = True
    interaction.user.roles = []

    await cog.handle_bot_init.callback(
        cog,
        interaction,
        _role(CONFIGURED_ROLE),
        _role(CONFIGURED_ADMIN_ROLE),
        _channel(CONFIGURED_CHANNEL),
        _channel(CONFIGURED_LOG),
    )

    assert (await _row(db_path))["league_admin_role_id"] == CONFIGURED_ADMIN_ROLE


async def test_set_core_setting_refuses_a_column_of_the_callers_choosing(tmp_path):
    """The column is interpolated into the UPDATE, so the allow-list is load-bearing."""
    db_path = await _make_db(tmp_path)
    await _seed_config(db_path)
    service = ConfigService(db_path)

    with pytest.raises(ValueError):
        await service.set_core_setting("test_mode_active", 0)

    assert (await _row(db_path))["test_mode_active"] == 1


# ── The league's two roles (issue #276) ───────────────────────────────────


@pytest.mark.parametrize("column", ["base_role_id", "driver_role_id"])
async def test_a_league_role_is_written_alone_and_read_back(tmp_path, column):
    """The league's roles are core settings, written a column at a time like the four beside
    them, so setting one cannot carry a stale value over the other or over anything else."""
    db_path = await _make_db(tmp_path)
    await _seed_config(db_path)
    service = ConfigService(db_path)
    before = await _row(db_path)

    assert await service.set_core_setting(column, 5150) is True

    after = await _row(db_path)
    assert after[column] == 5150
    assert {k: v for k, v in after.items() if k != column} == {
        k: v for k, v in before.items() if k != column
    }
    assert getattr(await service.get_server_config(), column) == 5150


@pytest.mark.parametrize("column", ["hub_channel_id", "hub_message_id"])
async def test_the_hub_is_written_alone_and_read_back(tmp_path, column):
    """The hub's channel and its panel (issue #279) are written a column at a time too."""
    db_path = await _make_db(tmp_path)
    await _seed_config(db_path)
    service = ConfigService(db_path)
    before = await _row(db_path)

    assert await service.set_core_setting(column, 6060) is True

    after = await _row(db_path)
    assert after[column] == 6060
    assert {k: v for k, v in after.items() if k != column} == {
        k: v for k, v in before.items() if k != column
    }
    assert getattr(await service.get_server_config(), column) == 6060


async def test_a_league_that_has_set_neither_role_reads_none(tmp_path):
    """Both are optional until the signup module asks for them."""
    db_path = await _make_db(tmp_path)
    await _seed_config(db_path)

    config = await ConfigService(db_path).get_server_config()

    assert (config.base_role_id, config.driver_role_id) == (None, None)


@pytest.mark.parametrize(
    "attribute", [s[0] for s in _SETTINGS] + ["handle_bot_init"]
)
def test_no_core_command_is_bound_to_the_interaction_channel(attribute):
    """Stated structurally as well as behaviourally, because the reason is easy to lose.

    These commands repair the interaction channel and the two roles, so a guard that read
    any of them would refuse precisely the person who needs them. The tier the guard records
    is asserted rather than the number of decorators: counting them said the same thing only
    for as long as the guards came in pairs, and said nothing about which guards they were.
    """
    from leaguebot.core.cogs.bot_cog import BotCog as Cog
    from leaguebot.core.utils.channel_guard import CHANNEL_EXEMPT_ATTRIBUTE, LEAGUE_ADMIN, TIER_ATTRIBUTE

    callback = getattr(Cog, attribute).callback
    assert getattr(callback, TIER_ATTRIBUTE) == LEAGUE_ADMIN
    assert getattr(callback, CHANNEL_EXEMPT_ATTRIBUTE) is True, (
        f"{attribute} is bound to the interaction channel — a league whose interaction "
        f"channel was deleted can no longer repair it"
    )


# ── The league admin role ─────────────────────────────────────────────────


def test_bot_init_requires_the_league_admin_role():
    """A required parameter, not an optional one.

    The role is the whole of the league admin tier, so a server initialised without one has
    nobody who may cancel its season — every league admin command would be refused until
    somebody noticed. Asking for it up front is what stops that, and making the parameter
    optional again would quietly undo it.
    """
    from leaguebot.core.cogs.bot_cog import BotCog as Cog

    parameter = next(
        p for p in Cog.handle_bot_init.parameters if p.name == "league_admin_role"
    )
    assert parameter.required


async def test_save_server_config_persists_the_league_admin_role(tmp_path):
    from leaguebot.core.models.server_config import ServerConfig

    db_path = await _make_db(tmp_path)
    service = ConfigService(db_path)

    created = await service.save_server_config(
        ServerConfig(
            server_id=SERVER_ID,
            interaction_role_id=CONFIGURED_ROLE,
            league_admin_role_id=CONFIGURED_ADMIN_ROLE,
            interaction_channel_id=CONFIGURED_CHANNEL,
            log_channel_id=CONFIGURED_LOG,
        )
    )

    assert created is True
    assert (await _row(db_path))["league_admin_role_id"] == CONFIGURED_ADMIN_ROLE
    stored = await service.get_server_config()
    assert stored is not None
    assert stored.league_admin_role_id == CONFIGURED_ADMIN_ROLE


async def test_a_server_configured_before_the_role_existed_reads_none(tmp_path):
    """The migration adds the column nullable, and NULL means "not yet chosen".

    No value is invented for a league that predates the role. Reading it back as ``None``
    is what lets the guards refuse a league admin command and name `/bot admin-role`,
    rather than falling back to a Discord permission the league never picked.
    """
    db_path = await _make_db(tmp_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, ?, ?, ?)",
            (SERVER_ID, CONFIGURED_ROLE, CONFIGURED_CHANNEL, CONFIGURED_LOG),
        )
        await db.commit()

    stored = await ConfigService(db_path).get_server_config()
    assert stored is not None
    assert stored.league_admin_role_id is None


# ── /bot pack ─────────────────────────────────────────────────────────────


def _packing_bot(db_path: str) -> MagicMock:
    bot = _bot(db_path)
    bot.scheduler_service.cancel_all = MagicMock(return_value=0)
    bot.get_cog = MagicMock(return_value=None)
    return bot


def _deferred(interaction: MagicMock) -> MagicMock:
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()
    return interaction


def test_bot_pack_is_a_league_admin_s_command_in_the_interaction_channel():
    """It releases the settings rather than repairing them, so it takes no setup exemption."""
    from leaguebot.core.cogs.bot_cog import BotCog as Cog
    from leaguebot.core.utils.channel_guard import CHANNEL_EXEMPT_ATTRIBUTE, LEAGUE_ADMIN, TIER_ATTRIBUTE

    callback = Cog.handle_pack.callback
    assert getattr(callback, TIER_ATTRIBUTE) == LEAGUE_ADMIN
    assert getattr(callback, CHANNEL_EXEMPT_ATTRIBUTE) is False


async def test_bot_pack_without_the_word_changes_nothing(tmp_path):
    """Refused, and the refusal recorded in the log channel (#482)."""
    db_path = await _make_db(tmp_path)
    await _seed_config(db_path)
    bot = _packing_bot(db_path)
    cog = BotCog(bot)
    interaction = _refusable(bot, _interaction(channel_id=CONFIGURED_CHANNEL), "bot pack")

    await _unwrap(cog.handle_pack)(cog, interaction, "confirm")

    reply = interaction.response.send_message.call_args.args[0]
    assert "CONFIRM" in reply
    assert await bot.config_service.get_league_server_id() == SERVER_ID
    assert _log_lines(bot) == [f"⛔ `/bot pack` refused for admin (<@7>) — {_reason(reply)}"]


async def test_bot_pack_is_refused_while_a_season_is_current_and_is_recorded(tmp_path):
    """Refused before anything is cleared, and the refusal recorded in the log channel (#482)."""
    db_path = await _make_db(tmp_path)
    await _seed_config(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO seasons (start_date, status, season_number, stage) "
            "VALUES ('2026-01-01', 'SETUP', 3, 'WAITING')"
        )
        await db.commit()
    bot = _packing_bot(db_path)
    cog = BotCog(bot)
    interaction = _refusable(bot, _interaction(channel_id=CONFIGURED_CHANNEL), "bot pack")

    await _unwrap(cog.handle_pack)(cog, interaction, "CONFIRM")

    reply = interaction.response.send_message.call_args.args[0]
    assert "Season 3 is current" in reply
    assert "waiting" in reply
    assert await bot.config_service.get_league_server_id() == SERVER_ID
    assert _log_lines(bot) == [f"⛔ `/bot pack` refused for admin (<@7>) — {_reason(reply)}"]


async def test_bot_pack_logs_while_the_log_channel_still_exists(tmp_path):
    """The one line a pack writes goes before it, while the log channel is still the league's.

    Once the pack has run there is no log channel to write to, so the line cannot report the
    pack as done (#482): it says the pack is under way and what it will clear.
    """
    db_path = await _make_db(tmp_path)
    await _seed_config(db_path)
    bot = _packing_bot(db_path)
    claimed_when_logged = []
    lines = []

    async def post_log(content):
        claimed_when_logged.append(await bot.config_service.get_league_server_id())
        lines.append(content)

    bot.output_router.post_log = AsyncMock(side_effect=post_log)
    cog = BotCog(bot)
    interaction = _deferred(_interaction(channel_id=CONFIGURED_CHANNEL))

    await _unwrap(cog.handle_pack)(cog, interaction, "CONFIRM")

    assert claimed_when_logged == [SERVER_ID]
    [line] = lines
    assert line.startswith("admin (<@7>) | /bot pack | ")
    assert "Success" not in line, "the line is written before the pack has succeeded"
    assert re.search(r"under ?way", line), line
    assert "settings" in line and "roles" in line, "the line says what the pack will clear"
    assert await bot.config_service.get_league_server_id() is None
    reply = interaction.followup.send.call_args.args[0]
    assert "/bot init" in reply
    assert "buttons now refuse" in reply


async def test_bot_pack_losing_a_race_to_a_new_season_says_so(tmp_path, monkeypatch):
    from leaguebot.core.services import pack_service

    db_path = await _make_db(tmp_path)
    await _seed_config(db_path)

    async def refuse(*_args, **_kwargs):
        raise pack_service.PackRefused(4, "CONFIGURATION")

    monkeypatch.setattr(pack_service, "pack", refuse)
    bot = _packing_bot(db_path)
    cog = BotCog(bot)
    interaction = _refusable(bot, _interaction(channel_id=CONFIGURED_CHANNEL), "bot pack")

    await _unwrap(cog.handle_pack)(cog, interaction, "CONFIRM")

    reply = interaction.followup.send.call_args.args[0]
    assert "Season 4 is current" in reply
    # The line the pack wrote as it began, then the standard refusal (#482).
    lines = _log_lines(bot)
    assert len(lines) == 2
    assert lines[1] == f"⛔ `/bot pack` refused for admin (<@7>) — {_reason(reply)}"


async def test_bot_pack_is_audited_as_the_member_who_ran_it(tmp_path):
    """Issue #383. What the entry holds is the service's to pin; who it names is the cog's."""
    db_path = await _make_db(tmp_path)
    await _seed_config(db_path)
    cog = BotCog(_packing_bot(db_path))

    await _unwrap(cog.handle_pack)(
        cog, _deferred(_interaction(channel_id=CONFIGURED_CHANNEL)), "CONFIRM"
    )

    (row,) = await _audit_rows(db_path)
    assert (row["change_type"], row["actor_id"]) == ("BOT_PACKED", 7)


# ── /bot factory-reset ─────────────────────────────────────────────────────

OWNER_ID = 77


def _owner_interaction(user_id: int = OWNER_ID) -> MagicMock:
    interaction = _deferred(_interaction())
    interaction.user.id = user_id
    interaction.user.send = AsyncMock(return_value=MagicMock(edit=AsyncMock()))
    interaction.guild = MagicMock()
    interaction.guild.owner_id = OWNER_ID
    interaction.guild.get_channel = MagicMock(return_value=None)
    return interaction


def _resetting_bot(db_path: str, tmp_path) -> MagicMock:
    bot = _packing_bot(db_path)
    bot.scheduler_service._scheduler.running = False
    bot.scheduler_service._jobstore_path = str(tmp_path / "scheduler.db")
    bot.user.id = 1000
    # Asked before the wipe where the reset's closing line goes (#482): the configured log
    # channel, as the router reads it while the configuration is still there.
    bot.output_router.log_destination = AsyncMock(return_value=CONFIGURED_LOG)
    return bot


async def _run(cog, interaction, confirm="CONFIRM"):
    """The guard is kept: who may run this is the point of half these tests."""
    await BotCog.handle_factory_reset.callback(cog, interaction, confirm)
    if cog._clean_up is not None:
        await cog._clean_up


def test_bot_factory_reset_is_the_server_owner_s_from_any_channel():
    from leaguebot.core.utils.channel_guard import CHANNEL_EXEMPT_ATTRIBUTE, SERVER_OWNER, TIER_ATTRIBUTE

    callback = BotCog.handle_factory_reset.callback
    assert getattr(callback, TIER_ATTRIBUTE) == SERVER_OWNER
    assert getattr(callback, CHANNEL_EXEMPT_ATTRIBUTE) is True


async def test_bot_factory_reset_refuses_an_administrator_who_is_not_the_owner(tmp_path):
    """Whatever roles or permissions they hold: the owner is the only way in."""
    db_path = await _make_db(tmp_path)
    await _seed_config(db_path)
    cog = BotCog(_resetting_bot(db_path, tmp_path))
    interaction = _owner_interaction(user_id=OWNER_ID + 1)
    interaction.user.guild_permissions = discord.Permissions(administrator=True)
    interaction.user.roles = [_role(CONFIGURED_ADMIN_ROLE)]
    # The guard answers before the command defers: the interaction is still unanswered, and
    # its client is the bot, whose log channel a refusal is recorded in.
    interaction.response.is_done = MagicMock(return_value=False)
    interaction.client = cog.bot

    await _run(cog, interaction)

    assert "owner" in interaction.response.send_message.call_args.args[0]
    assert await ConfigService(db_path).get_league_server_id() == SERVER_ID


async def test_bot_factory_reset_without_the_word_changes_nothing(tmp_path):
    db_path = await _make_db(tmp_path)
    await _seed_config(db_path)
    cog = BotCog(_resetting_bot(db_path, tmp_path))
    interaction = _owner_interaction()

    await _run(cog, interaction, confirm="yes")

    assert "CONFIRM" in interaction.response.send_message.call_args.args[0]
    assert await ConfigService(db_path).get_league_server_id() == SERVER_ID


async def test_bot_factory_reset_erases_nothing_without_a_backup(tmp_path, monkeypatch):
    """A backup that cannot be taken is a fault in the bot, so the reset is a failure, not a
    refusal (owner, 2026-09-29): the standard failure reply stating that nothing was erased, with
    no raw error in Discord, and a failure line in the log channel, whose configuration is still
    there to find it (#482)."""
    from leaguebot.core.services import backup_service, factory_reset_service
    from leaguebot.core.utils.interaction_errors import failure_reply

    def fail(*_args, **_kwargs):
        raise backup_service.BackupFault("the disk is full")

    monkeypatch.setattr(factory_reset_service, "take_backup", fail)
    db_path = await _make_db(tmp_path)
    await _seed_config(db_path)
    bot = _resetting_bot(db_path, tmp_path)
    cog = BotCog(bot)
    interaction = _refusable(bot, _owner_interaction(), "bot factory-reset")

    await _run(cog, interaction)

    reply = interaction.followup.send.call_args.args[0]
    assert reply == failure_reply(
        "`/bot factory-reset`", "Nothing was erased: the backup could not be taken."
    )
    assert "the disk is full" not in reply
    [line] = _log_lines(bot)
    assert line.startswith(
        f"❌ `/bot factory-reset` failed for admin (<@{OWNER_ID}>) — BackupFault."
    )
    assert "the disk is full" not in line
    assert await ConfigService(db_path).get_league_server_id() == SERVER_ID
    assert cog._clean_up is None
    interaction.user.send.assert_not_awaited()


async def test_bot_factory_reset_backs_up_then_wipes_then_reports_by_dm(tmp_path):
    db_path = await _make_db(tmp_path)
    await _seed_config(db_path)
    bot = _resetting_bot(db_path, tmp_path)
    cog = BotCog(bot)
    interaction = _owner_interaction()

    await _run(cog, interaction)

    backups = sorted(p.name for p in tmp_path.iterdir() if ".factory-" in p.name)
    assert len(backups) == 1 and backups[0].startswith("test.factory-")
    assert await ConfigService(db_path).get_league_server_id() is None
    bot.scheduler_service.cancel_all.assert_called_once_with()
    reply = interaction.followup.send.call_args.args[0]
    assert backups[0] in reply
    assert "direct message" in reply
    progress = interaction.user.send.return_value
    assert progress.edit.await_args_list[-1].kwargs["content"].startswith("✅")


async def test_bot_factory_reset_reports_to_the_log_where_dms_are_closed(tmp_path):
    db_path = await _make_db(tmp_path)
    await _seed_config(db_path)
    cog = BotCog(_resetting_bot(db_path, tmp_path))
    interaction = _owner_interaction()
    interaction.user.send = AsyncMock(
        side_effect=discord.Forbidden(MagicMock(status=403, reason="Forbidden"), "closed")
    )

    await _run(cog, interaction)

    assert "host's log" in interaction.followup.send.call_args.args[0]
    assert await ConfigService(db_path).get_league_server_id() is None


async def test_a_clean_up_that_breaks_says_where_it_stopped(tmp_path, monkeypatch):
    from leaguebot.core.services import factory_reset_service

    async def broken(*_args, **_kwargs):
        raise RuntimeError("gateway lost")

    monkeypatch.setattr(factory_reset_service, "clean_discord", broken)
    db_path = await _make_db(tmp_path)
    await _seed_config(db_path)
    cog = BotCog(_resetting_bot(db_path, tmp_path))
    interaction = _owner_interaction()

    await _run(cog, interaction)

    last = interaction.user.send.return_value.edit.await_args_list[-1].kwargs["content"]
    assert "stopped" in last and "gateway lost" in last


async def test_a_second_factory_reset_waits_for_the_first_clean_up(tmp_path):
    import asyncio

    db_path = await _make_db(tmp_path)
    cog = BotCog(_resetting_bot(db_path, tmp_path))
    cog._clean_up = asyncio.get_running_loop().create_future()
    interaction = _owner_interaction()

    await BotCog.handle_factory_reset.callback(cog, interaction, "CONFIRM")

    assert "still cleaning" in interaction.response.send_message.call_args.args[0]
    cog._clean_up.cancel()


# ── /bot factory-reset's closing line (#482) ───────────────────────────────
#
# A reset that goes ahead leaves one line in the log channel, posted once its clean-up has
# ended, however it ended (owner, 2026-09-29: "One line after clean-up"). The wipe takes the
# configuration with it, so the cog asks the router where the log goes before the wipe and
# hands that back with the line.


def _clean_up_ending(monkeypatch, bot: MagicMock, ending: str) -> dict:
    """Make the Discord clean-up end as *ending* says, noting how many log lines stood as it ran.

    "finished" cleans everything, "faults" finishes with a channel it could not clear, and
    "stopped" breaks part-way on something it did not expect.
    """
    from leaguebot.core.services import factory_reset_service

    seen: dict = {}

    async def clean(guild, bot_user_id, targets, report, **_kwargs):
        seen["lines_while_cleaning"] = bot.output_router.post_log.await_count
        if ending == "stopped":
            raise RuntimeError("gateway lost")
        outcome = factory_reset_service.CleanOutcome(
            total=2, done=2, channels_deleted=1, messages_deleted=5
        )
        if ending == "faults":
            outcome.faults.append("#race-log could not be cleared: 403 Forbidden")
        return outcome

    monkeypatch.setattr(factory_reset_service, "clean_discord", clean)
    return seen


def _asked_before_the_wipe(bot: MagicMock, db_path: str, answer: int | None) -> dict:
    """Answer where the log goes with *answer*, noting whether the league was still configured."""
    seen: dict = {}

    async def destination():
        seen["configured_when_asked"] = await ConfigService(db_path).get_league_server_id()
        return answer

    bot.output_router.log_destination = AsyncMock(side_effect=destination)
    return seen


async def _pending_rows(db_path: str) -> int:
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM pending_messages")
        (count,) = await cursor.fetchone()
    return count


@pytest.mark.parametrize(
    "ending",
    [
        pytest.param(
            "finished",
            id="clean-up-finished",
        ),
        pytest.param(
            "faults",
            id="clean-up-finished-with-faults",
        ),
        pytest.param(
            "stopped",
            id="clean-up-stopped-part-way",
        ),
    ],
)
async def test_a_factory_reset_that_goes_ahead_posts_one_line_once_its_clean_up_ends(
    tmp_path, monkeypatch, ending
):
    """One line, to the log channel found before the wipe, posted after the clean-up has ended
    and so left standing by it: it names the server owner, has what was erased beneath it, and
    says how the clean-up ended (#482)."""
    db_path = await _make_db(tmp_path)
    await _seed_config(db_path)
    bot = _resetting_bot(db_path, tmp_path)
    asked = _asked_before_the_wipe(bot, db_path, CONFIGURED_LOG)
    cleaning = _clean_up_ending(monkeypatch, bot, ending)
    cog = BotCog(bot)
    interaction = _owner_interaction()

    await _run(cog, interaction)

    assert asked["configured_when_asked"] == SERVER_ID
    assert cleaning["lines_while_cleaning"] == 0
    [call] = bot.output_router.post_log.await_args_list
    assert call.kwargs["channel"] == CONFIGURED_LOG
    line = str(call.args[0])
    first, *beneath = line.splitlines()
    assert f"admin (<@{OWNER_ID}>)" in first and "/bot factory-reset" in first
    assert beneath, "what was erased goes beneath the line"
    if ending == "finished":
        assert "stopped" not in line.lower() and "could not" not in line
    elif ending == "faults":
        assert "#race-log could not be cleared" in line
    else:
        assert "stopped" in line.lower()


@pytest.mark.parametrize(
    "where",
    [
        pytest.param(
            "not-found",
            id="log-channel-not-found",
        ),
        pytest.param(
            "not-written",
            id="log-channel-refuses-or-is-gone",
        ),
    ],
)
async def test_a_factory_reset_line_that_cannot_be_posted_goes_to_the_host_log(
    tmp_path, monkeypatch, caplog, where
):
    """Where the log channel cannot be found before the wipe, or cannot take the line after the
    clean-up, the line goes to the host's log, and nothing is queued to retry it: the fresh
    database belongs to a bot serving no server (#482)."""
    caplog.set_level(logging.INFO, logger="leaguebot.core.cogs.bot_cog")
    db_path = await _make_db(tmp_path)
    await _seed_config(db_path)
    bot = _resetting_bot(db_path, tmp_path)
    _asked_before_the_wipe(bot, db_path, None if where == "not-found" else CONFIGURED_LOG)
    bot.output_router.post_log = AsyncMock(return_value=None)
    _clean_up_ending(monkeypatch, bot, "finished")
    cog = BotCog(bot)
    interaction = _owner_interaction()

    await _run(cog, interaction)

    if where == "not-found":
        bot.output_router.post_log.assert_not_awaited()
    else:
        [call] = bot.output_router.post_log.await_args_list
        assert call.kwargs["channel"] == CONFIGURED_LOG
    assert any(
        record.name == "leaguebot.core.cogs.bot_cog"
        and record.levelno >= logging.INFO
        and f"admin (<@{OWNER_ID}>)" in record.getMessage()
        and "/bot factory-reset" in record.getMessage()
        for record in caplog.records
    ), [record.getMessage() for record in caplog.records]
    assert await _pending_rows(db_path) == 0


def test_nothing_current_names_a_withdrawn_bot_command():
    """The five setup commands became `/bot …` and `/bot-reset` was withdrawn (issue #247).

    A reply or a guide naming the old form sends a league to a command Discord no longer
    offers. `specs/` and the constitution's sync reports are historical records and are not
    read; everything a league or a maintainer reads as current is.
    """
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[2]
    withdrawn = re.compile(
        r"/bot-(?:init|log-channel|interaction-channel|interaction-role|admin-role|reset)\b"
    )
    paths = [
        *sorted((root / "src").rglob("*.py")),
        root / "README.md",
        *sorted((root / "docs" / "how-to").glob("*.md")),
        *sorted((root / "docs" / "wip-specs").glob("*.md")),
    ]
    offenders = sorted(
        str(path.relative_to(root))
        for path in paths
        if withdrawn.search(path.read_text(encoding="utf-8"))
    )
    assert offenders == []


# ── #482: every refusal of the bot cog's own checks is recorded ────────────


def _unconfigured_channel(channel_id: int = 905) -> MagicMock:
    """A channel the bot may manage, holding no job of the bot's."""
    channel = _channel(channel_id)
    perms = MagicMock(manage_channels=True, manage_roles=True)
    channel.permissions_for = MagicMock(return_value=perms)
    return channel


def _grantable_role(role_id: int = 903, *, managed: bool = False) -> MagicMock:
    """A role the bot can grant, unless it is *managed* by an integration."""
    role = MagicMock(spec=discord.Role)
    role.id = role_id
    role.name = "Drivers"
    role.mention = f"<@&{role_id}>"
    role.is_default.return_value = False
    role.managed = managed
    role.guild.me.guild_permissions.manage_roles = True
    role.guild.me.top_role.__gt__ = lambda _self, _other: True
    return role


async def _seed_ongoing_season(db_path: str) -> None:
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO seasons (start_date, status, season_number, stage) "
            "VALUES ('2026-01-01', 'ACTIVE', 3, 'ONGOING')"
        )
        await db.commit()


def _losing_init_race(bot: MagicMock, db_path: str, *, winner: int) -> None:
    """`/bot init` loses its claim to a run on server *winner* between its check and its write."""
    real = bot.config_service

    async def _lose(cfg):
        async with get_connection(db_path) as db:
            await db.execute(
                "INSERT INTO server_configs (server_id, interaction_role_id, "
                "interaction_channel_id, log_channel_id) VALUES (?, ?, ?, ?)",
                (winner, CONFIGURED_ROLE, CONFIGURED_CHANNEL, CONFIGURED_LOG),
            )
            await db.commit()
        return await ConfigService.save_server_config(real, cfg)

    bot.config_service = MagicMock(wraps=real)
    bot.config_service.get_league_server_id = real.get_league_server_id
    bot.config_service.get_server_config = real.get_server_config
    bot.config_service.save_server_config = _lose


async def _init(cog, interaction):
    await _unwrap(cog.handle_bot_init)(
        cog, interaction, _role(900), _role(903), _channel(901), _channel(902)
    )


async def _refused_site(case: str, tmp_path) -> tuple[MagicMock, MagicMock, str, str]:
    """Set up the refusal *case* and run it: the bot, the interaction, the command as the log
    names it, and a phrase of today's reply."""
    db_path = await _make_db(tmp_path)
    # Every case but these runs on the league's configured server.
    if not (case.endswith("before init") or case == "init lost race to the same server"):
        await _seed_config(db_path)
    bot = _resetting_bot(db_path, tmp_path)
    cog = BotCog(bot)
    command = {
        "init": "bot init", "log": "bot log-channel", "base": "bot base-role",
        "driver": "bot driver-role", "hub": "bot hub-channel", "factory": "bot factory-reset",
    }[case.split("-")[0].split(" ")[0]]
    interaction = _refusable(bot, _owner_interaction(), command)

    if case == "init already configured":
        await _init(cog, interaction)
        return bot, interaction, command, "runs once"
    if case == "init lost race to the same server":
        _losing_init_race(bot, db_path, winner=SERVER_ID)
        await _init(cog, interaction)
        return bot, interaction, command, "already configured"
    if case == "log-channel already another job":
        await _unwrap(cog.handle_log_channel)(cog, interaction, _channel(CONFIGURED_CHANNEL))
        return bot, interaction, command, "bot command channel"
    if case == "base-role fixed for the season":
        await _seed_ongoing_season(db_path)
        await _unwrap(cog.handle_base_role)(cog, interaction, _grantable_role())
        return bot, interaction, command, "fixed for Season 3"
    if case == "driver-role cannot be granted":
        await _unwrap(cog.handle_driver_role)(cog, interaction, _grantable_role(managed=True))
        return bot, interaction, command, "❌"
    if case == "hub-channel already another job":
        await _unwrap(cog.handle_hub_channel)(cog, interaction, _unconfigured_channel(CONFIGURED_LOG))
        return bot, interaction, command, "log channel"
    if case == "hub-channel lacks permissions":
        channel = _unconfigured_channel()
        channel.permissions_for.return_value = MagicMock(manage_channels=False, manage_roles=True)
        await _unwrap(cog.handle_hub_channel)(cog, interaction, channel)
        return bot, interaction, command, "Manage Channel"
    if case == "factory-reset without the word":
        await _unwrap(cog.handle_factory_reset)(cog, interaction, "yes")
        return bot, interaction, command, "CONFIRM"
    # The host-only cases.
    if case == "init on another server":
        interaction.guild_id = SERVER_ID + 1
        await _init(cog, interaction)
        return bot, interaction, command, "another server"
    if case == "init lost race to another server before init":
        _losing_init_race(bot, db_path, winner=SERVER_ID + 1)
        await _init(cog, interaction)
        return bot, interaction, command, "another server"
    if case == "log-channel before init":
        await _unwrap(cog.handle_log_channel)(cog, interaction, _channel(905))
        return bot, interaction, command, "not configured yet"
    if case == "base-role before init":
        await _unwrap(cog.handle_base_role)(cog, interaction, _grantable_role())
        return bot, interaction, command, "not configured yet"
    if case == "hub-channel before init":
        await _unwrap(cog.handle_hub_channel)(cog, interaction, _unconfigured_channel())
        return bot, interaction, command, "not configured yet"
    if case == "factory-reset without the word before init":
        await _unwrap(cog.handle_factory_reset)(cog, interaction, "yes")
        return bot, interaction, command, "CONFIRM"
    if case == "factory-reset while cleaning up before init":
        import asyncio

        cog._clean_up = asyncio.get_running_loop().create_future()
        try:
            await _unwrap(cog.handle_factory_reset)(cog, interaction, "CONFIRM")
        finally:
            cog._clean_up.cancel()
        return bot, interaction, command, "still cleaning"
    raise AssertionError(case)


def _replied(interaction: MagicMock) -> str:
    calls = (
        interaction.response.send_message.await_args_list
        + interaction.followup.send.await_args_list
    )
    return "\n".join(str(call.args[0]) for call in calls)


@pytest.mark.parametrize(
    "case",
    [
        "init already configured",
        "init lost race to the same server",
        "log-channel already another job",
        "base-role fixed for the season",
        "driver-role cannot be granted",
        "hub-channel already another job",
        "hub-channel lacks permissions",
        "factory-reset without the word",
    ],
)
async def test_a_refusal_of_the_bot_cog_is_recorded_in_the_log_channel(tmp_path, case):
    """A member the bot cog's own checks turn away on the league's configured server is
    answered as today, seen by them alone, and one standard line records it (#482).

    `/bot pack`'s two refusals are pinned by the pack tests above.
    """
    bot, interaction, command, phrase = await _refused_site(case, tmp_path)

    reply = _replied(interaction)
    assert phrase in reply
    sent = (
        interaction.response.send_message.await_args_list
        + interaction.followup.send.await_args_list
    )
    assert sent and all(call.kwargs.get("ephemeral") is True for call in sent)
    assert _log_lines(bot) == [
        f"⛔ `/{command}` refused for admin (<@{OWNER_ID}>) — {_reason(reply)}"
    ]


@pytest.mark.parametrize(
    "case",
    [
        "init on another server",
        "init lost race to another server before init",
        "log-channel before init",
        "base-role before init",
        "hub-channel before init",
        "factory-reset without the word before init",
        "factory-reset while cleaning up before init",
    ],
)
async def test_a_refusal_outside_the_league_goes_to_the_host_log_alone(tmp_path, caplog, case):
    """A refusal given on another server, or before the bot is set up, answers the member as
    today and writes nothing to a log channel: there is none of the league's to write to. The
    host's log records it, naming the command and the member (owner, #482: "Host log only").
    """
    caplog.set_level(logging.INFO, logger="leaguebot.core.cogs.bot_cog")

    bot, interaction, command, phrase = await _refused_site(case, tmp_path)

    assert phrase in _replied(interaction)
    bot.output_router.post_log.assert_not_awaited()
    member = re.compile(rf"(?<!\d){OWNER_ID}(?!\d)")
    assert any(
        record.name == "leaguebot.core.cogs.bot_cog"
        and record.levelno >= logging.INFO
        and command in record.getMessage()
        and member.search(record.getMessage())
        for record in caplog.records
    ), [record.getMessage() for record in caplog.records]
