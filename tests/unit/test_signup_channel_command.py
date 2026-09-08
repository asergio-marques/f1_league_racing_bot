"""`/signup channel` — setting the channel, and the permissions that go with it.

The command had no test of its own, and that is how a `NameError` shipped: the guard
against reusing the bot's command channel was replaced by the server-wide
`find_channel_use` check (775443d), and the `server_cfg` that guard had fetched was left
referenced fifty lines below, where the interaction role's overwrite is built. Nothing
exercised the command past the guard, so nothing noticed.

These drive the body to its end. The permission overwrites are the part that was broken
and the part most easily broken again, since they are built from three roles read from
three different places.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from cogs.signup_cog import SignupCog  # noqa: E402
from db.database import get_connection, run_migrations  # noqa: E402

SERVER_ID = 5511
INTERACTION_ROLE = 900
BASE_ROLE = 901


@pytest.fixture
async def db_path(tmp_path):
    path = str(tmp_path / "signup.db")
    await run_migrations(path)
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, ?, 100, 101)",
            (SERVER_ID, INTERACTION_ROLE),
        )
        await db.execute(
            "INSERT INTO signup_module_config (server_id, base_role_id) VALUES (?, ?)",
            (SERVER_ID, BASE_ROLE),
        )
        await db.commit()
    return path


def _channel(channel_id: int = 700):
    channel = MagicMock()
    channel.id = channel_id
    channel.mention = f"<#{channel_id}>"
    channel.edit = AsyncMock()
    return channel


def _interaction(guild):
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.guild = guild
    interaction.user.id = 42
    interaction.user.display_name = "Manager"
    interaction.response.defer = AsyncMock()
    interaction.response.is_done = MagicMock(return_value=False)
    interaction.response.send_message = AsyncMock()
    interaction.followup.send = AsyncMock()
    return interaction


def _guild():
    guild = MagicMock()
    guild.default_role = MagicMock()
    guild.me = MagicMock()
    guild.get_role = MagicMock(side_effect=lambda rid: MagicMock(name=f"role{rid}"))
    guild.get_channel = MagicMock(return_value=None)
    return guild


def _cog(db_path):
    from services.config_service import ConfigService
    from services.signup_module_service import SignupModuleService

    bot = MagicMock()
    bot.db_path = db_path
    bot.config_service = ConfigService(db_path)
    bot.signup_module_service = SignupModuleService(db_path)
    bot.output_router.post_log = AsyncMock()

    cog = SignupCog.__new__(SignupCog)
    cog.bot = bot
    return cog


async def _run(cog, interaction, channel):
    """The command body, past `channel_guard` and `server_admin_only`."""
    body = SignupCog.signup_channel.callback.__wrapped__.__wrapped__
    await body(cog, interaction, channel)


async def test_setting_a_free_channel_stores_it(db_path):
    """The regression: this raised `NameError` before reaching the store."""
    cog = _cog(db_path)
    guild = _guild()
    channel = _channel()

    await _run(cog, _interaction(guild), channel)

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT signup_channel_id FROM signup_module_config WHERE server_id = ?",
            (SERVER_ID,),
        )
        assert (await cursor.fetchone())[0] == channel.id


async def test_the_interaction_role_is_read_for_the_overwrites(db_path):
    """What the orphaned `server_cfg` was for.

    The role is fetched from the guild by the id on the server config; losing that read
    is what raised, and a silent `None` here would leave the stewards unable to see the
    channel they had just configured.
    """
    cog = _cog(db_path)
    guild = _guild()

    await _run(cog, _interaction(guild), _channel())

    asked = [call.args[0] for call in guild.get_role.call_args_list]
    assert INTERACTION_ROLE in asked, "the interaction role was never looked up"
    assert BASE_ROLE in asked, "the base role was never looked up"


async def test_a_channel_already_in_use_is_refused(db_path):
    """The guard that replaced the old one: 100 is the bot's command channel."""
    cog = _cog(db_path)
    interaction = _interaction(_guild())

    await _run(cog, interaction, _channel(100))

    reply = interaction.response.send_message.await_args.args[0]
    assert "already the" in reply

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT signup_channel_id FROM signup_module_config WHERE server_id = ?",
            (SERVER_ID,),
        )
        assert (await cursor.fetchone())[0] is None, "a refused channel was stored"


async def test_the_command_runs_to_the_end_without_an_unbound_name(db_path):
    """A blunt guard against the class of fault, not the instance.

    The body reads three roles and two configurations from four places, and an editing
    slip that drops one of the reads raises only when the command is run all the way
    through — which no test did before this file.
    """
    cog = _cog(db_path)
    interaction = _interaction(_guild())

    await _run(cog, interaction, _channel())

    # Reached the end: the command defers, so its confirmation is a followup, and no
    # refusal was sent through the response.
    interaction.response.send_message.assert_not_awaited()
    interaction.followup.send.assert_awaited()
