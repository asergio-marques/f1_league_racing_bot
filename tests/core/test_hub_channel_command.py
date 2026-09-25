"""`/bot hub-channel`: pointing the hub at a channel, and moving it (issue #279).

**A channel does one job.** The hub is held to the rule every channel command is: a channel
already set as anything else is refused, naming what holds it, and nothing is written.

**Setting it is the whole of standing it up.** The permissions are set and the panel posted in
the same command, so a league never has a hub channel without a panel, or a panel members
cannot see.

**Moving it stands the old one down.** The old panel is deleted and the permissions the bot set
there cleared, as moving the signup channel does; otherwise the old channel keeps a live panel
and a read-only lock nobody asked it to keep.

**What fails after the write is reported, not undone.** The channel chosen is still the right
one; a permission Discord refuses is repaired in Discord.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from leaguebot.core.cogs.bot_cog import BotCog
from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.services import hub_service
from leaguebot.core.services.config_service import ConfigService
from tests.support.undecorate import undecorate

SERVER_ID = 27902
LOG_CHANNEL = 101
HUB, OLD_HUB = 7920, 7921
OLD_PANEL = 7930
NEW_PANEL = 7931


@pytest.fixture(autouse=True)
def empty_registry(monkeypatch):
    monkeypatch.setattr(hub_service, "_OPTIONS", {})


@pytest.fixture
async def db_path(tmp_path):
    path = str(tmp_path / "hub_command.db")
    await run_migrations(path)
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id, league_admin_role_id) "
            "VALUES (?, 900, 100, ?, 902)",
            (SERVER_ID, LOG_CHANNEL),
        )
        await db.commit()
    return path


async def _configure(db_path, **columns):
    async with get_connection(db_path) as db:
        for column, value in columns.items():
            await db.execute(f"UPDATE server_configs SET {column} = ?", (value,))
        await db.commit()


async def _stored(db_path) -> dict:
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT hub_channel_id, hub_message_id FROM server_configs")
        return dict(await cursor.fetchone())


async def _audit(db_path) -> list[dict]:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT change_type, old_value, new_value FROM audit_entries"
        )
        return [dict(r) for r in await cursor.fetchall()]


def _channel(channel_id, *, manage=True, send_raises=None):
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = channel_id
    channel.mention = f"<#{channel_id}>"
    channel.permissions_for = MagicMock(
        return_value=MagicMock(manage_channels=manage, manage_roles=manage)
    )
    channel.edit = AsyncMock()
    channel.send = AsyncMock(side_effect=send_raises, return_value=MagicMock(id=NEW_PANEL))
    partial = MagicMock()
    partial.edit = AsyncMock()
    partial.delete = AsyncMock()
    channel.get_partial_message = MagicMock(return_value=partial)
    channel._partial = partial
    return channel


def _setup(db_path, *channels):
    by_id = {c.id: c for c in channels}
    guild = MagicMock()
    guild.get_channel = MagicMock(side_effect=lambda cid: by_id.get(cid))
    guild.get_role = MagicMock(return_value=MagicMock(spec=discord.Role))
    guild.default_role = MagicMock(spec=discord.Role)
    guild.me = MagicMock()

    bot = MagicMock()
    bot.db_path = db_path
    bot.config_service = ConfigService(db_path)
    bot.get_guild = MagicMock(return_value=guild)
    bot.output_router.post_log = AsyncMock()
    cog = BotCog(bot)

    interaction = MagicMock()
    interaction.guild = guild
    interaction.guild_id = SERVER_ID
    interaction.user.id = 77
    interaction.user.display_name = "Manager"
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()
    return cog, interaction


def _replied(interaction) -> str:
    return "\n".join(
        str(call.args[0])
        for call in interaction.response.send_message.await_args_list
        + interaction.followup.send.await_args_list
    )


async def _run(cog, interaction, channel):
    await undecorate(BotCog.handle_hub_channel)(cog, interaction, channel)


# ── Setting the hub ───────────────────────────────────────────────────────


async def test_the_hub_is_set_permissioned_and_given_its_panel(db_path):
    channel = _channel(HUB)
    cog, interaction = _setup(db_path, channel)

    await _run(cog, interaction, channel)

    assert await _stored(db_path) == {"hub_channel_id": HUB, "hub_message_id": NEW_PANEL}
    assert "overwrites" in channel.edit.await_args.kwargs
    assert "Nothing is offered here yet." in channel.send.await_args.args[0]
    assert f"**Hub channel** set to <#{HUB}>" in _replied(interaction)


async def test_the_change_is_audited_and_logged(db_path):
    await _configure(db_path, hub_channel_id=OLD_HUB)
    old, new = _channel(OLD_HUB), _channel(HUB)
    cog, interaction = _setup(db_path, old, new)

    await _run(cog, interaction, new)

    (row,) = await _audit(db_path)
    assert row["change_type"] == "HUB_CHANNEL_SET"
    assert json.loads(row["old_value"]) == {"channel_id": OLD_HUB}
    assert json.loads(row["new_value"]) == {"channel_id": HUB}
    assert f"<#{HUB}>" in cog.bot.output_router.post_log.await_args.args[0]


async def test_a_channel_doing_another_job_is_refused(db_path):
    """The log channel is the bot's; a panel there would sink under its lines."""
    log_channel = _channel(LOG_CHANNEL)
    cog, interaction = _setup(db_path, log_channel)

    await _run(cog, interaction, log_channel)

    assert "already the bot log channel" in _replied(interaction)
    assert await _stored(db_path) == {"hub_channel_id": None, "hub_message_id": None}
    log_channel.edit.assert_not_awaited()
    log_channel.send.assert_not_awaited()


async def test_setting_the_hub_it_already_is_changes_nothing(db_path):
    await _configure(db_path, hub_channel_id=HUB, hub_message_id=OLD_PANEL)
    channel = _channel(HUB)
    cog, interaction = _setup(db_path, channel)

    await _run(cog, interaction, channel)

    assert "already the hub channel" in _replied(interaction)
    assert "Nothing was changed" in _replied(interaction)
    channel.send.assert_not_awaited()


async def test_a_channel_the_bot_cannot_manage_is_refused_before_anything_is_written(db_path):
    channel = _channel(HUB, manage=False)
    cog, interaction = _setup(db_path, channel)

    await _run(cog, interaction, channel)

    assert "Manage Channel" in _replied(interaction)
    assert "Manage Permissions" in _replied(interaction)
    assert await _stored(db_path) == {"hub_channel_id": None, "hub_message_id": None}


async def test_a_panel_that_cannot_be_posted_is_reported_and_the_hub_kept(db_path):
    """The channel is the right one; the repair is in Discord's settings."""
    forbidden = discord.HTTPException(MagicMock(status=403, reason="Forbidden"), "Missing Access")
    channel = _channel(HUB, send_raises=forbidden)
    cog, interaction = _setup(db_path, channel)

    await _run(cog, interaction, channel)

    assert (await _stored(db_path))["hub_channel_id"] == HUB
    assert "could not be posted" in _replied(interaction)
    assert "with faults" in cog.bot.output_router.post_log.await_args.args[0]


# ── Moving the hub ────────────────────────────────────────────────────────


async def test_moving_the_hub_deletes_the_old_panel_and_clears_the_old_channel(db_path):
    await _configure(db_path, hub_channel_id=OLD_HUB, hub_message_id=OLD_PANEL)
    old, new = _channel(OLD_HUB), _channel(HUB)
    cog, interaction = _setup(db_path, old, new)

    await _run(cog, interaction, new)

    old.get_partial_message.assert_called_once_with(OLD_PANEL)
    old._partial.delete.assert_awaited_once()
    old.edit.assert_awaited_once_with(overwrites={})
    new.send.assert_awaited_once()
    assert await _stored(db_path) == {"hub_channel_id": HUB, "hub_message_id": NEW_PANEL}


async def test_an_old_panel_already_deleted_does_not_stop_the_move(db_path):
    await _configure(db_path, hub_channel_id=OLD_HUB, hub_message_id=OLD_PANEL)
    old, new = _channel(OLD_HUB), _channel(HUB)
    old._partial.delete = AsyncMock(
        side_effect=discord.NotFound(MagicMock(status=404, reason="Not Found"), "gone")
    )
    cog, interaction = _setup(db_path, old, new)

    await _run(cog, interaction, new)

    assert "⚠️" not in _replied(interaction)
    assert (await _stored(db_path))["hub_channel_id"] == HUB


async def test_an_old_hub_channel_deleted_from_the_server_is_stepped_over(db_path):
    await _configure(db_path, hub_channel_id=OLD_HUB, hub_message_id=OLD_PANEL)
    new = _channel(HUB)
    cog, interaction = _setup(db_path, new)

    await _run(cog, interaction, new)

    assert "⚠️" not in _replied(interaction)
    new.send.assert_awaited_once()


# ── Its tier ──────────────────────────────────────────────────────────────


def test_the_command_is_a_league_manager_s_in_the_interaction_channel():
    """Like every other channel command; it repairs nothing the guards read."""
    from leaguebot.core.utils.channel_guard import CHANNEL_EXEMPT_ATTRIBUTE, LEAGUE_MANAGER, TIER_ATTRIBUTE

    callback = BotCog.handle_hub_channel.callback
    assert getattr(callback, TIER_ATTRIBUTE) == LEAGUE_MANAGER
    assert not getattr(callback, CHANNEL_EXEMPT_ATTRIBUTE, False)
