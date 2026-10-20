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
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from leaguebot.core.cogs.bot_cog import BotCog
from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.services import hub_service
from leaguebot.core.services.config_service import ConfigService
from tests.support.change_queue import (
    Clock,
    attach_queue,
    http_error,
    league_double,
    run_queue,
    stopped_job,
)
from tests.support.undecorate import undecorate

SERVER_ID = 27902
LOG_CHANNEL = 101
HUB, OLD_HUB = 7920, 7921
OLD_PANEL = 7930
NEW_PANEL = 7931
NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)



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
    # A message's link is text: a refused deletion keeps it on the stopped job, as JSON.
    partial.jump_url = f"https://discord.test/channels/{SERVER_ID}/{channel_id}/message"
    channel.get_partial_message = MagicMock(return_value=partial)
    channel._partial = partial
    return channel


def _setup(db_path, *channels):
    """The command's cog on a bot carrying the league's change queue, which posts and deletes
    the hub's panels when it is run; the guild and the bot find *channels* and the log channel."""
    from leaguebot.core.services.output_router import OutputRouter

    bot = league_double(db_path)
    by_id = {c.id: c for c in channels}
    by_id[LOG_CHANNEL] = bot.log_channel
    guild = MagicMock()
    guild.get_channel = MagicMock(side_effect=lambda cid: by_id.get(cid))
    guild.get_role = MagicMock(return_value=MagicMock(spec=discord.Role))
    guild.default_role = MagicMock(spec=discord.Role)
    guild.me = MagicMock()

    bot.config_service = ConfigService(db_path)
    bot.output_router = OutputRouter(bot, bot.config_service, retry_db_path=db_path)
    bot.get_guild = MagicMock(return_value=guild)
    bot.get_channel = MagicMock(side_effect=lambda cid: by_id.get(cid))
    bot.fetch_channel = AsyncMock(side_effect=lambda cid: by_id[cid])
    attach_queue(bot, db_path, now=Clock(NOW))
    cog = BotCog(bot)

    interaction = MagicMock()
    interaction.client = bot
    interaction.guild = guild
    interaction.guild_id = SERVER_ID
    interaction.user.id = 77
    interaction.user.display_name = "Manager"
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()
    return cog, interaction


def _log(cog) -> str:
    return "\n".join(cog.bot.log_channel.sent)


def _replied(interaction) -> str:
    return "\n".join(
        str(call.args[0])
        for call in interaction.response.send_message.await_args_list
        + interaction.followup.send.await_args_list
    )


async def _run(cog, interaction, channel):
    """The manager runs `/bot hub-channel` for *channel*, and the queue is run."""
    await undecorate(BotCog.handle_hub_channel)(cog, interaction, channel)
    await run_queue(cog.bot)


# ── Setting the hub ───────────────────────────────────────────────────────


async def test_the_hub_is_set_permissioned_and_given_its_panel(db_path):
    channel = _channel(HUB)
    cog, interaction = _setup(db_path, channel)

    await _run(cog, interaction, channel)

    assert await _stored(db_path) == {"hub_channel_id": HUB, "hub_message_id": NEW_PANEL}
    assert "overwrites" in channel.edit.await_args.kwargs
    assert "Nothing is offered here yet." in channel.send.await_args.args[0]
    assert f"**Hub channel** set to <#{HUB}>" in _replied(interaction)


async def test_the_reply_says_the_hub_was_set_and_its_panel_follows_on_the_queue(db_path):
    """Core spec :135: a command that makes its own change and then asks for a post replies
    with what it did, and the post follows on the queue. The manager points the hub at 7920:
    before the queue runs, the reply names the new hub channel and says its panel follows on
    the queue, and no panel has been posted; once the queue runs, the panel is posted."""
    channel = _channel(HUB)
    cog, interaction = _setup(db_path, channel)

    await undecorate(BotCog.handle_hub_channel)(cog, interaction, channel)

    replied = _replied(interaction)
    assert f"**Hub channel** set to <#{HUB}>" in replied
    assert "queue" in replied
    channel.send.assert_not_awaited()

    await run_queue(cog.bot)

    channel.send.assert_awaited_once()
    assert await _stored(db_path) == {"hub_channel_id": HUB, "hub_message_id": NEW_PANEL}


async def test_the_change_is_audited_and_logged(db_path):
    await _configure(db_path, hub_channel_id=OLD_HUB)
    old, new = _channel(OLD_HUB), _channel(HUB)
    cog, interaction = _setup(db_path, old, new)

    await _run(cog, interaction, new)

    (row,) = await _audit(db_path)
    assert row["change_type"] == "HUB_CHANNEL_SET"
    assert json.loads(row["old_value"]) == {"channel_id": OLD_HUB}
    assert json.loads(row["new_value"]) == {"channel_id": HUB}
    assert f"<#{HUB}>" in _log(cog)


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


async def test_a_panel_that_cannot_be_posted_stops_the_queue_and_the_hub_is_kept(db_path):
    """The channel is the right one; the repair is in Discord's settings. Discord refuses the
    new panel: the hub stays set to the channel chosen, and the queue stops at the panel's
    refresh, for Retry once the bot may post there, rather than the fault being reported."""
    channel = _channel(HUB, send_raises=http_error(discord.Forbidden, status=403,
                                                   text="Missing Access"))
    cog, interaction = _setup(db_path, channel)

    await _run(cog, interaction, channel)

    assert (await _stored(db_path))["hub_channel_id"] == HUB
    assert await stopped_job(db_path) is not None


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
    assert await stopped_job(db_path) is None


async def test_an_old_hub_channel_deleted_from_the_server_is_stepped_over(db_path):
    await _configure(db_path, hub_channel_id=OLD_HUB, hub_message_id=OLD_PANEL)
    new = _channel(HUB)
    cog, interaction = _setup(db_path, new)

    await _run(cog, interaction, new)

    assert "⚠️" not in _replied(interaction)
    new.send.assert_awaited_once()


async def test_moving_the_hub_posts_the_new_panel_before_deleting_the_old(db_path):
    """The hub moves from 7921, whose panel stands, to 7920. The new panel is posted first and
    the old one deleted after, as a job of its own, so the league is never left without one."""
    await _configure(db_path, hub_channel_id=OLD_HUB, hub_message_id=OLD_PANEL)
    old, new = _channel(OLD_HUB), _channel(HUB)
    order: list[str] = []
    new.send.side_effect = lambda *a, **k: order.append("post new") or MagicMock(id=NEW_PANEL)
    old._partial.delete.side_effect = lambda *a, **k: order.append("delete old")
    cog, interaction = _setup(db_path, old, new)

    await _run(cog, interaction, new)

    assert order == ["post new", "delete old"]
    assert await _stored(db_path) == {"hub_channel_id": HUB, "hub_message_id": NEW_PANEL}


async def test_the_old_panel_comes_down_when_an_earlier_refresh_already_posted_the_new(db_path):
    """A module toggle's refresh is waiting when the hub moves from 7921 to 7920. It runs first and
    posts the new panel, so the move's own refresh only edits it; the old panel still comes
    down, as the new one is up either way, and the new is posted once."""
    from leaguebot.core.models.change import ChangeOrigin

    await _configure(db_path, hub_channel_id=OLD_HUB, hub_message_id=OLD_PANEL)
    old, new = _channel(OLD_HUB), _channel(HUB)
    cog, interaction = _setup(db_path, old, new)
    await cog.bot.change_queue.ask(
        hub_service.HUB_REFRESH, {"command": "/module enable weather"},
        what="refreshing the hub panel", origin=ChangeOrigin.BOT,
    )

    await _run(cog, interaction, new)

    new.send.assert_awaited_once()
    old._partial.delete.assert_awaited_once()
    assert await _stored(db_path) == {"hub_channel_id": HUB, "hub_message_id": NEW_PANEL}


async def test_a_refused_deletion_of_the_old_panel_stops_the_queue(db_path):
    """Discord refuses to delete the panel in the old hub 7921: the new panel stands in 7920
    and is kept, and the queue stops at the old panel's deletion rather than reporting it."""
    await _configure(db_path, hub_channel_id=OLD_HUB, hub_message_id=OLD_PANEL)
    old, new = _channel(OLD_HUB), _channel(HUB)
    old._partial.delete = AsyncMock(
        side_effect=http_error(discord.Forbidden, status=403, text="Missing Permissions")
    )
    cog, interaction = _setup(db_path, old, new)

    await _run(cog, interaction, new)

    new.send.assert_awaited_once()
    assert await _stored(db_path) == {"hub_channel_id": HUB, "hub_message_id": NEW_PANEL}
    assert await stopped_job(db_path) is not None


# ── Its tier ──────────────────────────────────────────────────────────────


def test_the_command_is_a_league_manager_s_in_the_interaction_channel():
    """Like every other channel command; it repairs nothing the guards read."""
    from leaguebot.core.utils.channel_guard import CHANNEL_EXEMPT_ATTRIBUTE, LEAGUE_MANAGER, TIER_ATTRIBUTE

    callback = BotCog.handle_hub_channel.callback
    assert getattr(callback, TIER_ATTRIBUTE) == LEAGUE_MANAGER
    assert not getattr(callback, CHANNEL_EXEMPT_ATTRIBUTE, False)
