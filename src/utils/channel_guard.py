"""channel_guard decorator — enforces interaction channel and role access control.

Constitution Principle I: Two-tier access
  - Interaction channel: commands silently ignored outside it.
  - Interaction role: ephemeral permission error for role-lacking users.

Constitution Principle VII: Output channel discipline
  - This guard never posts to non-configured channels.
"""

from __future__ import annotations

import functools
import logging
from typing import Callable, Any

import discord
from discord import app_commands, Interaction

log = logging.getLogger(__name__)


def channel_guard(func: Callable) -> Callable:
    """Decorator for app_commands callback methods.

    Usage:
        @app_commands.command(...)
        @channel_guard
        async def my_command(self, interaction: Interaction, ...) -> None:
            ...

    The decorated command must be a method of a Cog; `self.bot` must have a
    `config_service` attribute with a `get_server_config(server_id)` method.

    Behaviour:
        1. Fetch ServerConfig for the guild.
        2. If command is not in interaction_channel_id → silently ignore.
        3. If invoker does not have interaction_role_id → ephemeral error.
        4. Otherwise run the original command.
    """

    @functools.wraps(func)
    async def wrapper(self: Any, interaction: Interaction, *args: Any, **kwargs: Any) -> None:
        config = await self.bot.config_service.get_server_config(interaction.guild_id)

        if config is None:
            # Bot not initialised; silently pass through (init_cog is exempt)
            await func(self, interaction, *args, **kwargs)
            return

        # 1. Channel check — silent ignore
        if interaction.channel_id != config.interaction_channel_id:
            # Do not respond at all; just silently drop the interaction
            return

        # 2. Role check — ephemeral error
        member = interaction.user
        if not isinstance(member, discord.Member):
            await interaction.response.send_message(
                "⛔ This command can only be used inside a server.",
                ephemeral=True,
            )
            return

        role_ids = {role.id for role in member.roles}
        if config.interaction_role_id not in role_ids:
            await interaction.response.send_message(
                "⛔ You don't have permission to use this command.",
                ephemeral=True,
            )
            return

        await func(self, interaction, *args, **kwargs)

    return wrapper


def admin_only(func: Callable) -> Callable:
    """Additional decorator for commands that require `MANAGE_GUILD` permission.

    Apply *after* channel_guard when both are needed:
        @channel_guard
        @admin_only
        async def my_command(...)

    This is separate from channel_guard so /bot init can use admin_only without
    channel_guard (chicken-and-egg: no config exists yet).
    """

    @functools.wraps(func)
    async def wrapper(self: Any, interaction: Interaction, *args: Any, **kwargs: Any) -> None:
        member = interaction.user
        if not isinstance(member, discord.Member):
            await interaction.response.send_message(
                "⛔ This command can only be used inside a server.",
                ephemeral=True,
            )
            return

        if not member.guild_permissions.manage_guild:
            await interaction.response.send_message(
                "⛔ You need the **Manage Server** permission to use this command.",
                ephemeral=True,
            )
            return

        await func(self, interaction, *args, **kwargs)

    return wrapper
