"""TeamCog — /team command group."""
from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from models.season import SeasonStage
from utils.channel_guard import league_admin_only, league_manager_only

log = logging.getLogger(__name__)

_MAX_MSG_LEN = 1900  # leave headroom below Discord's 2000 char limit


class TeamCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def _team_list_lock(self, interaction: discord.Interaction, command: str) -> bool:
        """Refuse a change to the team list once a season's configuration is confirmed.

        The team list is settled while the active season stands in Configuration, and is
        fixed for the rest of that season from the moment its configuration is confirmed:
        the signup wizard offers it as the preferred teams, and every division is created
        from it. With no active season the list is free. Returns True where the command was
        refused, having answered the interaction.
        """
        season = await self.bot.season_service.get_setup_or_active_season(  # type: ignore[attr-defined]

        )
        if season is None or season.stage is SeasonStage.CONFIGURATION:
            return False
        await interaction.response.send_message(
            f"⛔ The team list is fixed for Season {season.season_number} now that its "
            f"configuration has been confirmed. `/team {command}` is available again once "
            "the season has ended, or while a new season is in configuration.",
            ephemeral=True,
        )
        return True

    async def _refuse_once_the_season_is_done(
        self, interaction: discord.Interaction, command: str
    ) -> bool:
        """Refuse a role mapping once every division of the season is finished or cancelled.

        A team's role may be repaired in every stage of a live season but Pending completion
        (issue #224), and with no live season at all the mapping is the server's own and free
        to change — the team list itself is settled on the same terms. So this refuses only
        where a season is live and has run its course, and lets the no-season case through,
        which the shared gate would otherwise turn away.

        Returns True where the command was refused, having answered the interaction.
        """
        from models.season import SeasonStage

        season = await self.bot.season_service.get_setup_or_active_season(  # type: ignore[attr-defined]

        )
        if season is None or season.stage is not SeasonStage.PENDING_COMPLETION:
            return False
        await interaction.response.send_message(
            f"⛔ Every division of Season {season.season_number} is done, so `/team "
            f"{command}` no longer has anything to act on — completing the season "
            "revokes the team roles. Repair the mapping once the season has ended.",
            ephemeral=True,
        )
        return True

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
        description="Add a team to the server list, while no season's configuration is confirmed.",
    )
    @app_commands.describe(
        name="Name of the new team (max 50 chars).",
        role="Discord role to associate with this team.",
    )
    @league_manager_only
    async def team_add(
        self,
        interaction: discord.Interaction,
        name: str,
        role: discord.Role,
    ) -> None:
        if await self._team_list_lock(interaction, "add"):
            return
        # A role belongs to one team only, and the team is not added where its role is taken.
        holder = await self.bot.placement_service.team_holding_role(  # type: ignore[attr-defined]
            role.id
        )
        if holder is not None:
            await interaction.response.send_message(
                f'⛔ {role.mention} is already the role of "{holder}". A role belongs to one '
                "team only.",
                ephemeral=True,
            )
            return
        try:
            await self.bot.team_service.add_default_team(  # type: ignore[attr-defined]
                name
            )
        except ValueError as exc:
            await interaction.response.send_message(f"⛔ {exc}", ephemeral=True)
            return

        try:
            await self.bot.placement_service.set_team_role_config(  # type: ignore[attr-defined]
                name, role.id,
                actor_id=interaction.user.id, actor_name=str(interaction.user),
            )
        except ValueError as exc:
            # Taken between the check and the write: the team goes again, so nothing stands.
            await self.bot.team_service.remove_default_team(name)  # type: ignore[attr-defined]
            await interaction.response.send_message(f"⛔ {exc}", ephemeral=True)
            return

        await interaction.response.send_message(
            f'✅ Team "{name}" added with role {role.mention}.', ephemeral=True
        )
        await self.bot.output_router.post_log(
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /team add | Success\n"
            f"  team: {name}",
        )

    # ------------------------------------------------------------------
    # /team remove  (FR-004, FR-005, FR-006)
    # ------------------------------------------------------------------

    @team.command(
        name="remove",
        description="Remove a team from the server list, while no season's configuration is confirmed.",
    )
    @app_commands.describe(name="Exact team name to remove.")
    @league_admin_only
    async def team_remove(
        self,
        interaction: discord.Interaction,
        name: str,
    ) -> None:
        if await self._team_list_lock(interaction, "remove"):
            return
        try:
            await self.bot.team_service.remove_default_team(  # type: ignore[attr-defined]
                name
            )
        except ValueError as exc:
            await interaction.response.send_message(f"⛔ {exc}", ephemeral=True)
            return

        await self.bot.placement_service.delete_team_role_config(  # type: ignore[attr-defined]
            name,
            actor_id=interaction.user.id, actor_name=str(interaction.user),
        )

        await interaction.response.send_message(
            f'✅ Team "{name}" removed from the server list.', ephemeral=True
        )
        await self.bot.output_router.post_log(
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /team remove | Success\n"
            f"  team: {name}",
        )

    # ------------------------------------------------------------------
    # /team rename  (FR-007, FR-008, FR-009)
    # ------------------------------------------------------------------

    @team.command(
        name="rename",
        description="Rename a team in the server list, while no season's configuration is confirmed.",
    )
    @app_commands.describe(
        current_name="Exact current name of the team.",
        new_name="Replacement name (max 50 chars).",
    )
    @league_manager_only
    async def team_rename(
        self,
        interaction: discord.Interaction,
        current_name: str,
        new_name: str,
    ) -> None:
        if await self._team_list_lock(interaction, "rename"):
            return
        try:
            await self.bot.team_service.rename_default_team(  # type: ignore[attr-defined]
                current_name, new_name
            )
        except ValueError as exc:
            await interaction.response.send_message(f"⛔ {exc}", ephemeral=True)
            return

        await self.bot.placement_service.rename_team_role_config(  # type: ignore[attr-defined]
            current_name, new_name,
            actor_id=interaction.user.id, actor_name=str(interaction.user),
        )

        await interaction.response.send_message(
            f'✅ Team "{current_name}" renamed to "{new_name}".', ephemeral=True
        )
        await self.bot.output_router.post_log(
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /team rename | Success\n"
            f"  old_name: {current_name}\n"
            f"  new_name: {new_name}",
        )

    # ------------------------------------------------------------------
    # /team role — set the role of a team, in any state
    # ------------------------------------------------------------------

    @team.command(
        name="role",
        description="Set the Discord role of a team, while the season is being built or raced.",
    )
    @app_commands.describe(
        name="Exact name of the team.",
        role="Discord role to associate with this team.",
    )
    @league_manager_only
    async def team_role(
        self,
        interaction: discord.Interaction,
        name: str,
        role: discord.Role,
    ) -> None:
        """Map a team of the server list to a role, in any stage but Pending completion.

        Unlike the team list, a team's role is never fixed: nothing stops a role being
        deleted from the server mid-season, and a league must be able to point the team
        at its replacement. The Reserve team keeps its own command.

        **Except once every division is done** (issue #224). Nothing is raced in Pending
        completion, and completing the season revokes every team role a few steps later, so
        a mapping repaired there would be undone before anyone wore it. The repair is made
        once the season has ended, for the season that follows.
        """
        if await self._refuse_once_the_season_is_done(interaction, "role"):
            return
        teams = await self.bot.team_service.get_teams_with_roles(  # type: ignore[attr-defined]

        )
        match = next(
            (t for t in teams if t["name"].casefold() == name.casefold()), None
        )
        if match is None:
            await interaction.response.send_message(
                f'⛔ No team named "{name}" is in the server list.', ephemeral=True
            )
            return
        if match["is_reserve"]:
            await interaction.response.send_message(
                "⛔ The Reserve team's role is set with `/team reserve-role`.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)
        try:
            await self.bot.placement_service.set_team_role_config(  # type: ignore[attr-defined]
                match["name"], role.id,
                actor_id=interaction.user.id, actor_name=str(interaction.user),
            )
        except ValueError as exc:
            # Another team holds the role (#375): nothing is changed and no driver moves.
            await interaction.followup.send(f"⛔ {exc}", ephemeral=True)
            return
        # The drivers already seated in the team follow its role (issue #220).
        moved = await self.bot.placement_service.swap_team_role(  # type: ignore[attr-defined]
            match["name"], match["role_id"], role.id, interaction.guild
        )
        await interaction.followup.send(
            f'✅ Team "{match["name"]}" now maps to {role.mention}.'
            + (f" {moved} seated driver(s) moved to the new role." if moved else ""),
            ephemeral=True,
        )
        await self.bot.output_router.post_log(
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /team role | Success\n"
            f"  team: {match['name']}\n"
            f"  role: {role.name} (<@&{role.id}>)\n"
            f"  seated drivers moved: {moved}",
        )

    # ------------------------------------------------------------------
    # /team list  (FR-010, FR-011)
    # ------------------------------------------------------------------

    @team.command(
        name="list",
        description="List all teams in the server list with their mapped roles.",
    )
    @league_manager_only
    async def team_list(
        self,
        interaction: discord.Interaction,
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        server_teams = await self.bot.team_service.get_teams_with_roles(  # type: ignore[attr-defined]

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

        )

        if setup_season is None:
            header = "**Server team list:**"
            content = header + "\n" + "\n".join(server_lines)
            await _send_long(interaction, content)
            return

        season_names = await self.bot.team_service.get_setup_season_team_names(  # type: ignore[attr-defined]
            setup_season.id
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
        description="Show the confirmed team lineups of the season being raced.",
    )
    @app_commands.describe(
        division="Division name or tier number. Omit to show all divisions.",
        public="Post the lineup visibly in the channel (default: only visible to you).",
    )
    @league_manager_only
    async def team_lineup(
        self,
        interaction: discord.Interaction,
        division: str | None = None,
        public: bool = False,
    ) -> None:
        await interaction.response.defer(ephemeral=not public)

        season = await self.bot.season_service.get_confirmed_season(  # type: ignore[attr-defined]

        )
        if season is None:
            await interaction.followup.send("⛔ No season is being raced, so no lineup is confirmed.", ephemeral=True)
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

        # ── The graphic replaces the textual output where configured (FR-026) ──
        #
        # One image per division, honouring `public`. These are command output and **not**
        # the lineup of record: nothing is written to `lineup_message_id` and the lineup
        # channel is untouched (FR-028). A fatal error rejects rather than falling back —
        # the caller is the one person able to fix the template (Constitution XIV.7).
        from services.image_lineup_post import lineup_enabled, render_for_command

        if await lineup_enabled(self.bot):
            from services.image_render_service import discard_attachment

            files: list[discord.File] = []
            notices: list = []
            # One `finally` around the whole batch, because a rejection part way through
            # abandons every picture drawn before it — the divisions already rendered are
            # never sent, and without this their files would be the one leak no posting
            # path accounts for.
            try:
                for div in all_divisions:
                    outcome = await render_for_command(self.bot, interaction.guild, div.id)
                    if outcome.action == "REJECTED":
                        await interaction.followup.send(
                            f"{outcome.message}\n_Lineup for **{div.name}** was not drawn._",
                            ephemeral=True,
                        )
                        return
                    if outcome.png_path is not None:
                        files.append(
                            discord.File(
                                str(outcome.png_path), filename=outcome.png_path.name
                            )
                        )
                    notices.extend(outcome.notices)

                if files:
                    text = "\n".join(f"**{div.name}**" for div in all_divisions)
                    if notices:
                        from services.image_render_service import ImageRenderService

                        await ImageRenderService.report_notices(
                            self.bot, notices
                        )
                    await interaction.followup.send(
                        text, files=files, ephemeral=not public
                    )
                    return
            finally:
                discard_attachment(*files)

        lines: list[str] = []
        for div in all_divisions:
            lines.append(f"**{div.name}**")
            teams = await self.bot.team_service.get_division_teams(  # type: ignore[attr-defined]
                div.id, committed_only=True
            )
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
                        if uid:
                            member = interaction.guild.get_member(int(uid)) if interaction.guild else None
                            driver_str = member.display_name if member else uid
                        else:
                            driver_str = "*(empty)*"
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
    @league_manager_only
    async def team_reserve_role(
        self,
        interaction: discord.Interaction,
        role: discord.Role | None = None,
    ) -> None:
        """Set or clear the Reserve team's role, on the same terms as `/team role`.

        Refused once the season is pending completion, for the reason given there.
        """
        if await self._refuse_once_the_season_is_done(interaction, "reserve-role"):
            return
        await interaction.response.defer(ephemeral=True)
        teams = await self.bot.team_service.get_teams_with_roles()  # type: ignore[attr-defined]
        old_role_id = next((t["role_id"] for t in teams if t["is_reserve"]), None)
        if role is not None:
            try:
                await self.bot.placement_service.set_team_role_config(  # type: ignore[attr-defined]
                    "Reserve", role.id,
                    actor_id=interaction.user.id, actor_name=str(interaction.user),
                )
            except ValueError as exc:
                # Another team holds the role (#375): nothing is changed and no driver moves.
                await interaction.followup.send(f"⛔ {exc}", ephemeral=True)
                return
            msg = f"✅ Reserve team role set to {role.mention}."
        else:
            await self.bot.placement_service.delete_team_role_config(  # type: ignore[attr-defined]
                "Reserve",
                actor_id=interaction.user.id, actor_name=str(interaction.user),
            )
            msg = "✅ Reserve team role cleared."

        # The drivers already seated in Reserve follow its role (issue #220).
        moved = await self.bot.placement_service.swap_team_role(  # type: ignore[attr-defined]
            "Reserve", old_role_id, role.id if role else None,
            interaction.guild,
        )
        if moved:
            msg += f" {moved} seated driver(s) moved to the new role."
        await interaction.followup.send(msg, ephemeral=True)
        await self.bot.output_router.post_log(
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /team reserve-role | Success\n"
            + (f"  role: {role.name} (<@&{role.id}>)" if role else "  role: cleared"),
        )


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
