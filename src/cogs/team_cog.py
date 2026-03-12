"""TeamCog — /team command group."""
from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from utils.channel_guard import channel_guard, admin_only

log = logging.getLogger(__name__)

_MAX_MSG_LEN = 1900  # leave headroom below Discord's 2000 char limit


class TeamCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    team = app_commands.Group(
        name="team",
        description="Team configuration commands",
        guild_only=True,
        default_permissions=None,
    )

    # ------------------------------------------------------------------
    # /team add  (FR-001, FR-002, FR-003)
    # ------------------------------------------------------------------

    @team.command(
        name="add",
        description="Add a team to the server list. Also applies to the current SETUP season if one is active.",
    )
    @app_commands.describe(
        name="Name of the new team (max 50 chars).",
        role="Discord role to associate with this team (optional).",
    )
    @channel_guard
    @admin_only
    async def team_add(
        self,
        interaction: discord.Interaction,
        name: str,
        role: discord.Role | None = None,
    ) -> None:
        try:
            await self.bot.team_service.add_default_team(  # type: ignore[attr-defined]
                interaction.guild_id, name
            )
        except ValueError as exc:
            await interaction.response.send_message(f"⛔ {exc}", ephemeral=True)
            return

        if role is not None:
            await self.bot.placement_service.set_team_role_config(  # type: ignore[attr-defined]
                interaction.guild_id, name, role.id,
                actor_id=interaction.user.id, actor_name=str(interaction.user),
            )

        setup_season = await self.bot.season_service.get_setup_season(  # type: ignore[attr-defined]
            interaction.guild_id
        )
        if setup_season is not None:
            try:
                div_count = await self.bot.team_service.season_team_add(  # type: ignore[attr-defined]
                    interaction.guild_id, setup_season.id, name, 2
                )
            except ValueError as exc:
                await interaction.response.send_message(f"⛔ {exc}", ephemeral=True)
                return

        if setup_season is not None and role is not None:
            msg = (
                f'✅ Team "{name}" added with role {role.mention} and inserted into all '
                f"{div_count} division(s) of Season {setup_season.season_number}."
            )
        elif setup_season is not None:
            msg = (
                f'✅ Team "{name}" added and inserted into all '
                f"{div_count} division(s) of Season {setup_season.season_number}."
            )
        elif role is not None:
            msg = f'✅ Team "{name}" added with role {role.mention}.'
        else:
            msg = f'✅ Team "{name}" added.'

        await interaction.response.send_message(msg, ephemeral=True)

    # ------------------------------------------------------------------
    # /team remove  (FR-004, FR-005, FR-006)
    # ------------------------------------------------------------------

    @team.command(
        name="remove",
        description="Remove a team from the server list. Also applies to the current SETUP season if one is active.",
    )
    @app_commands.describe(name="Exact team name to remove.")
    @channel_guard
    @admin_only
    async def team_remove(
        self,
        interaction: discord.Interaction,
        name: str,
    ) -> None:
        setup_season = await self.bot.season_service.get_setup_season(  # type: ignore[attr-defined]
            interaction.guild_id
        )

        try:
            await self.bot.team_service.remove_default_team(  # type: ignore[attr-defined]
                interaction.guild_id, name
            )
        except ValueError as exc:
            await interaction.response.send_message(f"⛔ {exc}", ephemeral=True)
            return

        await self.bot.placement_service.delete_team_role_config(  # type: ignore[attr-defined]
            interaction.guild_id, name,
            actor_id=interaction.user.id, actor_name=str(interaction.user),
        )

        if setup_season is not None:
            season_names = await self.bot.team_service.get_setup_season_team_names(  # type: ignore[attr-defined]
                interaction.guild_id, setup_season.id
            )
            team_in_season = name in season_names
            div_count = await self.bot.team_service.season_team_remove(  # type: ignore[attr-defined]
                interaction.guild_id, setup_season.id, name
            )

        if setup_season is not None and team_in_season:
            msg = (
                f'✅ Team "{name}" removed from the server list and all '
                f"{div_count} division(s) of Season {setup_season.season_number}."
            )
        elif setup_season is not None:
            msg = (
                f'✅ Team "{name}" removed from the server list. '
                f"(Not present in Season {setup_season.season_number} divisions.)"
            )
        else:
            msg = f'✅ Team "{name}" removed from the server list.'

        await interaction.response.send_message(msg, ephemeral=True)

    # ------------------------------------------------------------------
    # /team rename  (FR-007, FR-008, FR-009)
    # ------------------------------------------------------------------

    @team.command(
        name="rename",
        description="Rename a team in the server list. Also applies to the current SETUP season if one is active.",
    )
    @app_commands.describe(
        current_name="Exact current name of the team.",
        new_name="Replacement name (max 50 chars).",
    )
    @channel_guard
    @admin_only
    async def team_rename(
        self,
        interaction: discord.Interaction,
        current_name: str,
        new_name: str,
    ) -> None:
        setup_season = await self.bot.season_service.get_setup_season(  # type: ignore[attr-defined]
            interaction.guild_id
        )

        try:
            await self.bot.team_service.rename_default_team(  # type: ignore[attr-defined]
                interaction.guild_id, current_name, new_name
            )
        except ValueError as exc:
            await interaction.response.send_message(f"⛔ {exc}", ephemeral=True)
            return

        await self.bot.placement_service.rename_team_role_config(  # type: ignore[attr-defined]
            interaction.guild_id, current_name, new_name,
            actor_id=interaction.user.id, actor_name=str(interaction.user),
        )

        if setup_season is not None:
            div_count = await self.bot.team_service.season_team_rename(  # type: ignore[attr-defined]
                interaction.guild_id, setup_season.id, current_name, new_name
            )
            msg = (
                f'✅ Team "{current_name}" renamed to "{new_name}" across all '
                f"{div_count} division(s) of Season {setup_season.season_number}."
            )
        else:
            msg = f'✅ Team "{current_name}" renamed to "{new_name}".'

        await interaction.response.send_message(msg, ephemeral=True)

    # ------------------------------------------------------------------
    # /team list  (FR-010, FR-011)
    # ------------------------------------------------------------------

    @team.command(
        name="list",
        description="List all teams in the server list with their mapped roles.",
    )
    @channel_guard
    @admin_only
    async def team_list(
        self,
        interaction: discord.Interaction,
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        server_teams = await self.bot.team_service.get_teams_with_roles(  # type: ignore[attr-defined]
            interaction.guild_id
        )
        non_reserve = [t for t in server_teams if not t["is_reserve"]]

        if not non_reserve:
            await interaction.followup.send(
                "No teams configured. Use `/team add` to create one.", ephemeral=True
            )
            return

        def _fmt_team(t: dict) -> str:
            role_part = f"<@&{t['role_id']}>" if t["role_id"] else "no role"
            return f"  {t['name']} → {role_part}"

        server_lines = [_fmt_team(t) for t in non_reserve]
        reserve = next((t for t in server_teams if t["is_reserve"]), None)
        if reserve:
            server_lines.append(_fmt_team(reserve))

        setup_season = await self.bot.season_service.get_setup_season(  # type: ignore[attr-defined]
            interaction.guild_id
        )

        if setup_season is None:
            header = "**Server team list:**"
            content = header + "\n" + "\n".join(server_lines)
            await _send_long(interaction, content)
            return

        season_names = await self.bot.team_service.get_setup_season_team_names(  # type: ignore[attr-defined]
            interaction.guild_id, setup_season.id
        )
        server_names = {t["name"] for t in non_reserve}

        if server_names == season_names:
            header = f"**Server team list (Season {setup_season.season_number} will use this list):**"
            content = header + "\n" + "\n".join(server_lines)
        else:
            season_list = ", ".join(sorted(season_names)) if season_names else "*(empty)*"
            content = (
                f"⚠️ Season {setup_season.season_number} divisions differ from the server list.\n\n"
                f"**Server list:**\n" + "\n".join(server_lines) +
                f"\n\n**Season {setup_season.season_number} effective teams:**\n  {season_list}"
            )

        await _send_long(interaction, content)

    # ------------------------------------------------------------------
    # /team lineup  — show placed drivers per team for the active season
    # ------------------------------------------------------------------

    @team.command(
        name="lineup",
        description="Show team lineups for the active season.",
    )
    @app_commands.describe(
        division="Division name or tier number. Omit to show all divisions.",
        public="Post the lineup visibly in the channel (default: only visible to you).",
    )
    @channel_guard
    @admin_only
    async def team_lineup(
        self,
        interaction: discord.Interaction,
        division: str | None = None,
        public: bool = False,
    ) -> None:
        await interaction.response.defer(ephemeral=not public)

        season = await self.bot.season_service.get_active_season(  # type: ignore[attr-defined]
            interaction.guild_id
        )
        if season is None:
            await interaction.followup.send("⛔ No active season.", ephemeral=True)
            return

        all_divisions = await self.bot.season_service.get_divisions(season.id)  # type: ignore[attr-defined]
        all_divisions = sorted(all_divisions, key=lambda d: d.tier)

        if division is not None:
            result = await self.bot.placement_service.resolve_division(  # type: ignore[attr-defined]
                season.id, division
            )
            if result is None:
                await interaction.followup.send(f"⛔ Division `{division}` not found.", ephemeral=True)
                return
            div_id, _ = result
            all_divisions = [d for d in all_divisions if d.id == div_id]

        if not all_divisions:
            await interaction.followup.send("No divisions found.", ephemeral=True)
            return

        lines: list[str] = []
        for div in all_divisions:
            lines.append(f"**{div.name}**")
            teams = await self.bot.team_service.get_division_teams(div.id)  # type: ignore[attr-defined]
            if not teams:
                lines.append("  *(no teams)*")
            else:
                for team in teams:
                    lines.append(f"  **{team['name']}**")
                    filled = {
                        s["seat_number"]: s["discord_user_id"]
                        for s in team["seats"]
                        if s["driver_profile_id"] is not None
                    }
                    for seat_num in range(1, team["max_seats"] + 1):
                        uid = filled.get(seat_num)
                        driver_str = f"<@{uid}>" if uid else "*(empty)*"
                        lines.append(f"    Seat {seat_num}: {driver_str}")
            lines.append("")

        await _send_long(interaction, "\n".join(lines).rstrip(), ephemeral=not public)

    # ------------------------------------------------------------------
    # /team reserve-role  — set or clear the role for the Reserve team
    # ------------------------------------------------------------------

    @team.command(
        name="reserve-role",
        description="Set or clear the Discord role for the Reserve team.",
    )
    @app_commands.describe(
        role="Role to grant to Reserve drivers. Omit (or leave blank) to clear the current mapping.",
    )
    @channel_guard
    @admin_only
    async def team_reserve_role(
        self,
        interaction: discord.Interaction,
        role: discord.Role | None = None,
    ) -> None:
        if role is not None:
            await self.bot.placement_service.set_team_role_config(  # type: ignore[attr-defined]
                interaction.guild_id, "Reserve", role.id,
                actor_id=interaction.user.id, actor_name=str(interaction.user),
            )
            msg = f"✅ Reserve team role set to {role.mention}."
        else:
            await self.bot.placement_service.delete_team_role_config(  # type: ignore[attr-defined]
                interaction.guild_id, "Reserve",
                actor_id=interaction.user.id, actor_name=str(interaction.user),
            )
            msg = "✅ Reserve team role cleared."

        await interaction.response.send_message(msg, ephemeral=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _send_long(interaction: discord.Interaction, text: str, *, ephemeral: bool = True) -> None:
    """Send potentially-long text, splitting into followup chunks if needed."""
    if len(text) <= _MAX_MSG_LEN:
        await interaction.followup.send(text, ephemeral=ephemeral)
        return
    chunks = []
    while text:
        chunks.append(text[:_MAX_MSG_LEN])
        text = text[_MAX_MSG_LEN:]
    for chunk in chunks:
        await interaction.followup.send(chunk, ephemeral=ephemeral)
