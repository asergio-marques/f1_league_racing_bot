"""Core server configuration — `/bot-init` and the three settings beside it.

Three properties are pinned here, each of which a plausible tidy-up would undo.

**The three setting commands are not `channel_guard`-ed.** That guard admits a command only
in the configured interaction channel and only to a holder of the configured interaction
role. These commands exist to repair those very settings, so guarding them would lock an
administrator out of the failure they are for — a deleted interaction channel would be
unrecoverable short of wiping the configuration. The tests below invoke them from the wrong
channel, by a user without the role, and require them to work anyway.

**Each writes exactly one column.** `save_server_config` once carried a whole `ServerConfig`
into an upsert, which is how `/bot-init force:True` came to switch test mode off: the model
it was handed had never read the stored row, so `test_mode_active` defaulted to False and
overwrote a live setting. The setters write a single column so no such carry-over is
possible.

**`/bot-init` runs once.** A second run is refused rather than overwriting, which is what
makes the clobber above unreachable rather than merely corrected.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from cogs.init_cog import InitCog  # noqa: E402
from db.database import get_connection, run_migrations  # noqa: E402
from services.config_service import ConfigService  # noqa: E402

SERVER_ID = 4242

#: The configured interaction channel and role. Every setting-command test below deliberately
#: acts from somewhere else, to prove the guard is absent.
CONFIGURED_CHANNEL = 111
CONFIGURED_ROLE = 222
CONFIGURED_LOG = 333


def _unwrap(cmd):
    """Strip `admin_only` and return the command body.

    One layer, because these commands carry no `channel_guard`. Unwrapping cannot by itself
    notice a guard being added — it would simply strip the new outer layer instead — so the
    absence of one is pinned separately, behaviourally and structurally, below.
    """
    return cmd.callback.__wrapped__


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
            "interaction_channel_id, log_channel_id, test_mode_active, "
            "weather_module_enabled, signup_module_enabled) VALUES (?, ?, ?, ?, ?, 1, 1)",
            (SERVER_ID, CONFIGURED_ROLE, CONFIGURED_CHANNEL, CONFIGURED_LOG, test_mode),
        )
        await db.commit()


async def _row(db_path: str) -> dict:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT * FROM server_configs WHERE server_id = ?", (SERVER_ID,)
        )
        row = await cursor.fetchone()
    return dict(row) if row is not None else {}


def _bot(db_path: str) -> MagicMock:
    bot = MagicMock()
    # The two channel settings check the channel is not already doing another job, which
    # reads the database directly rather than through a service.
    bot.db_path = db_path
    bot.config_service = ConfigService(db_path)
    bot.output_router.post_log = AsyncMock()
    bot.team_service.seed_default_teams_if_empty = AsyncMock()
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


# ── /bot-init runs once ───────────────────────────────────────────────────


async def test_bot_init_configures_an_unconfigured_server(tmp_path):
    db_path = await _make_db(tmp_path)
    bot = _bot(db_path)
    cog = InitCog(bot)
    interaction = _interaction()

    await _unwrap(cog.handle_bot_init)(
        cog,
        interaction,
        _role(CONFIGURED_ROLE),
        _channel(CONFIGURED_CHANNEL),
        _channel(CONFIGURED_LOG),
    )

    row = await _row(db_path)
    assert row["interaction_role_id"] == CONFIGURED_ROLE
    assert row["interaction_channel_id"] == CONFIGURED_CHANNEL
    assert row["log_channel_id"] == CONFIGURED_LOG
    bot.team_service.seed_default_teams_if_empty.assert_awaited_once_with(SERVER_ID)


async def test_bot_init_refuses_a_second_run_and_names_the_three_commands(tmp_path):
    db_path = await _make_db(tmp_path)
    await _seed_config(db_path)
    bot = _bot(db_path)
    cog = InitCog(bot)
    interaction = _interaction()

    await _unwrap(cog.handle_bot_init)(
        cog, interaction, _role(900), _channel(901), _channel(902)
    )

    reply = interaction.response.send_message.call_args.args[0]
    assert "runs once" in reply
    assert "/bot-log-channel" in reply
    assert "/bot-interaction-channel" in reply
    assert "/bot-interaction-role" in reply

    row = await _row(db_path)
    assert row["interaction_role_id"] == CONFIGURED_ROLE
    assert row["interaction_channel_id"] == CONFIGURED_CHANNEL
    assert row["log_channel_id"] == CONFIGURED_LOG
    bot.team_service.seed_default_teams_if_empty.assert_not_awaited()


async def test_a_second_bot_init_does_not_switch_test_mode_off(tmp_path):
    """The regression this change exists to make unreachable.

    `/bot-init force:True` built a `ServerConfig` without reading the stored row, so
    `test_mode_active` defaulted to False and the upsert wrote it — leaving test mode off
    with the test drivers still seated, and nothing said so.
    """
    db_path = await _make_db(tmp_path)
    await _seed_config(db_path, test_mode=1)
    cog = InitCog(_bot(db_path))

    await _unwrap(cog.handle_bot_init)(
        cog, _interaction(), _role(900), _channel(901), _channel(902)
    )

    assert (await _row(db_path))["test_mode_active"] == 1


async def test_save_server_config_will_not_overwrite_an_existing_row(tmp_path):
    """The insert-only contract, at the layer that enforces it."""
    from models.server_config import ServerConfig

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


# ── The three settings ────────────────────────────────────────────────────

#: (command attribute, column, factory, the id it sets)
_SETTINGS = [
    ("handle_log_channel", "log_channel_id", _channel, 555),
    ("handle_interaction_channel", "interaction_channel_id", _channel, 556),
    ("handle_interaction_role", "interaction_role_id", _role, 557),
]


@pytest.mark.parametrize(
    "attribute,column,factory,new_id", _SETTINGS, ids=[s[1] for s in _SETTINGS]
)
async def test_a_setting_command_writes_only_its_own_column(
    tmp_path, attribute, column, factory, new_id
):
    db_path = await _make_db(tmp_path)
    await _seed_config(db_path)
    cog = InitCog(_bot(db_path))
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
async def test_a_setting_command_works_outside_the_interaction_channel(
    tmp_path, attribute, column, factory, new_id
):
    """Guarding these on the settings they repair would make the failure unrecoverable."""
    db_path = await _make_db(tmp_path)
    await _seed_config(db_path)
    cog = InitCog(_bot(db_path))

    # The whole decorator chain, not the unwrapped body: a `channel_guard` added later
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
    cog = InitCog(_bot(db_path))
    interaction = _interaction()

    await _unwrap(getattr(cog, attribute))(cog, interaction, factory(new_id))

    assert "/bot-init" in interaction.response.send_message.call_args.args[0]
    assert await _row(db_path) == {}


@pytest.mark.parametrize(
    "attribute,column,factory,new_id", _SETTINGS, ids=[s[1] for s in _SETTINGS]
)
async def test_a_setting_command_requires_manage_server(
    tmp_path, attribute, column, factory, new_id
):
    """`admin_only` is the whole of the gate on these, so it had better be on."""
    db_path = await _make_db(tmp_path)
    await _seed_config(db_path)
    cog = InitCog(_bot(db_path))

    interaction = _interaction()
    interaction.user.guild_permissions.manage_guild = False

    # The decorated command, not the unwrapped body.
    await getattr(cog, attribute).callback(cog, interaction, factory(new_id))

    assert "Manage Server" in interaction.response.send_message.call_args.args[0]
    assert (await _row(db_path))[column] != new_id


async def test_set_core_setting_refuses_a_column_of_the_callers_choosing(tmp_path):
    """The column is interpolated into the UPDATE, so the allow-list is load-bearing."""
    db_path = await _make_db(tmp_path)
    await _seed_config(db_path)
    service = ConfigService(db_path)

    with pytest.raises(ValueError):
        await service.set_core_setting(SERVER_ID, "test_mode_active", 0)

    assert (await _row(db_path))["test_mode_active"] == 1


@pytest.mark.parametrize(
    "attribute", [s[0] for s in _SETTINGS] + ["handle_bot_init"]
)
def test_no_core_command_is_channel_guarded(attribute):
    """Exactly one decorator — `admin_only` — stands between the command and its body.

    Stated structurally as well as behaviourally because the reason is easy to lose: these
    commands repair the interaction channel and the interaction role, so a guard that reads
    either would refuse precisely the administrator who needs them.
    """
    from cogs.init_cog import InitCog as Cog

    body = getattr(Cog, attribute).callback.__wrapped__
    assert not hasattr(body, "__wrapped__"), (
        f"{attribute} carries more than one decorator — if that is a channel_guard, an "
        f"admin whose interaction channel was deleted can no longer repair it"
    )
