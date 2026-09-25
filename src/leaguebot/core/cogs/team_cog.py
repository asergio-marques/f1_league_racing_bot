"""TeamCog — /team command group."""
from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from models.season import SeasonStage
from services.team_service import FULL_NAME_MAX, SHORTHAND_MAX
from utils.asset_resolver import normalise
from utils.autocomplete import bounded_autocomplete, team_autocomplete
from utils.channel_guard import league_admin_only, league_manager_only, role_grant_refusal
from utils.league_bot import LeagueBot
from utils.league_server import LeagueModal

log = logging.getLogger(__name__)

_MAX_MSG_LEN = 1900  # leave headroom below Discord's 2000 char limit


class TeamCog(commands.Cog):
    def __init__(self, bot: LeagueBot) -> None:
        self.bot = bot

    async def _team_list_is_open(self) -> bool:
        """Whether the team list may still be changed: no live season, or one in Configuration.

        The question apart from the refusal, because `/team modify` asks it to decide which
        fields its form offers, and asks it again when the form comes back (#381).
        """
        season = await self.bot.season_service.get_setup_or_active_season()
        return season is None or season.stage is SeasonStage.CONFIGURATION

    async def _team_list_lock(self, interaction: discord.Interaction, command: str) -> bool:
        """Refuse a change to the team list once a season's configuration is confirmed.

        The team list is settled while the active season stands in Configuration, and is
        fixed for the rest of that season from the moment its configuration is confirmed:
        the signup wizard offers it as the preferred teams, and every division is created
        from it. With no active season the list is free. Returns True where the command was
        refused, having answered the interaction.
        """
        season = await self.bot.season_service.get_setup_or_active_season(

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

        season = await self.bot.season_service.get_setup_or_active_season(

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
    @league_manager_only
    async def team_add(self, interaction: discord.Interaction) -> None:
        """Open the form a team is added on (#381).

        A team carries three things, and the form takes all three at once: the shorthand a
        league types, the full name every post shows, and the role its drivers are granted.
        The two names are bounded as they are typed, which is the one thing a modal does that
        a command parameter cannot.

        **No deferral.** `send_modal` has to be the interaction's first response, so every
        check before it is a quick read — and every one of them runs again on submit, because
        a season can move on while the form is open.
        """
        if await self._team_list_lock(interaction, "add"):
            return
        await interaction.response.send_modal(_TeamAddModal(self))

    async def add_team(
        self,
        interaction: discord.Interaction,
        *,
        shorthand: str,
        full_name: str,
        role: discord.Role,
    ) -> None:
        """Add the team the form describes, or say why not. Every check runs here."""
        if await self._team_list_lock(interaction, "add"):
            return
        # A team's role is granted to its drivers, so one the bot cannot grant is refused here,
        # where a league can still choose another (#381).
        refusal = role_grant_refusal(role)
        if refusal is not None:
            await interaction.response.send_message(f"⛔ {refusal}", ephemeral=True)
            return
        # A role belongs to one team only, and the team is not added where its role is taken.
        holder = await self.bot.placement_service.team_holding_role(
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
            await self.bot.team_service.add_default_team(
                shorthand, full_name=full_name
            )
        except ValueError as exc:
            await interaction.response.send_message(f"⛔ {exc}", ephemeral=True)
            return

        try:
            await self.bot.placement_service.set_team_role_config(
                shorthand, role.id,
                actor_id=interaction.user.id, actor_name=str(interaction.user),
            )
        except ValueError as exc:
            # Taken between the check and the write: the team goes again, so nothing stands.
            await self.bot.team_service.remove_default_team(shorthand)
            await interaction.response.send_message(f"⛔ {exc}", ephemeral=True)
            return

        await interaction.response.send_message(
            f'✅ Team "{full_name}" added as `{shorthand}`, with role {role.mention}.',
            ephemeral=True,
        )
        await self.bot.output_router.post_log(
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /team add | Success\n"
            f"  team: {full_name}\n"
            f"  shorthand: {shorthand}\n"
            f"  role: {role.name} (<@&{role.id}>)",
        )

    # ------------------------------------------------------------------
    # /team modify  (#381) — replaces /team rename and /team role
    # ------------------------------------------------------------------

    @team.command(
        name="modify",
        description="Change a team's names or its role.",
    )
    @app_commands.describe(team="The team's shorthand.")
    @league_manager_only
    async def team_modify(self, interaction: discord.Interaction, team: str) -> None:
        """Open the form a team is changed on (#381).

        The form offers what may change **now**: the two names only while the team list is
        open, and the role at every stage but Pending completion, where nothing may change at
        all and the command is refused before any form opens. Every field comes pre-filled, so
        submitting it unchanged changes nothing.

        The Reserve team is sent to `/team reserve-role`, which is the command that owns it.
        """
        if await self._refuse_once_the_season_is_done(interaction, "modify"):
            return
        reference = await self.bot.team_service.resolve_server_team(team)
        if reference.team is None:
            await interaction.response.send_message(f"⛔ {reference.refusal}", ephemeral=True)
            return
        if reference.team["is_reserve"]:
            await interaction.response.send_message(
                "⛔ The Reserve team's role is set with `/team reserve-role`, and its names "
                "are not a league's to change.",
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(
            _TeamModifyModal(
                self, team=reference.team, names_offered=await self._team_list_is_open()
            )
        )

    async def modify_team(
        self,
        interaction: discord.Interaction,
        *,
        was: dict,
        shorthand: str | None,
        full_name: str | None,
        role: discord.Role | None,
    ) -> None:
        """Apply what the form changed, or refuse the whole submission (#381).

        *was* is the team as the form opened on it. Only a field whose value **changed**
        counts, so a form submitted untouched changes nothing and says so. Each changed field
        is held to its own window, checked again here because the season may have moved on
        while the form stood open: a change that no longer stands refuses the submission as a
        whole, naming the field, and nothing at all is written.
        """
        await interaction.response.defer(ephemeral=True)
        reference = await self.bot.team_service.resolve_server_team(was["name"])
        if reference.team is None:
            await interaction.followup.send(
                f'⛔ "{was["full_name"]}" is no longer in the server\'s team list.',
                ephemeral=True,
            )
            return
        current = reference.team

        new_shorthand = (shorthand or "").strip() or current["name"]
        new_full_name = (full_name or "").strip() or current["full_name"]
        names_changed = (new_shorthand, new_full_name) != (current["name"], current["full_name"])
        role_changed = role is not None and role.id != current["role_id"]
        new_role = role if role_changed else None

        if not names_changed and not role_changed:
            await interaction.followup.send(
                f'Nothing changed: "{current["full_name"]}" stands as it was.', ephemeral=True
            )
            return

        if names_changed and not await self._team_list_is_open():
            await interaction.followup.send(
                "⛔ The team list is fixed now that the season's configuration is confirmed, "
                "so a team's shorthand and full name cannot change. Only its role can, and "
                "nothing was written.",
                ephemeral=True,
            )
            return

        if new_role is not None:
            refusal = role_grant_refusal(new_role)
            if refusal is not None:
                await interaction.followup.send(
                    f"⛔ {refusal} Nothing was written.", ephemeral=True
                )
                return
            try:
                await self.bot.placement_service.set_team_role_config(
                    current["name"], new_role.id,
                    actor_id=interaction.user.id, actor_name=str(interaction.user),
                )
            except ValueError as exc:
                # Another team holds the role (#375): nothing is changed and no driver moves.
                await interaction.followup.send(
                    f"⛔ {exc} Nothing was written.", ephemeral=True
                )
                return

        moved = 0
        if new_role is not None:
            # The drivers already seated in the team follow its role (issue #220).
            moved = await self.bot.placement_service.swap_team_role(
                current["name"], current["role_id"], new_role.id, interaction.guild,
            )

        named = dict(current)
        if names_changed:
            try:
                named = await self.bot.team_service.modify_default_team(
                    current["name"],
                    shorthand=new_shorthand,
                    full_name=new_full_name,
                    actor_id=interaction.user.id,
                    actor_name=str(interaction.user),
                )
            except ValueError as exc:
                await interaction.followup.send(f"⛔ {exc}", ephemeral=True)
                return
            if new_shorthand != current["name"]:
                await self.bot.placement_service.rename_team_role_config(
                    current["name"], new_shorthand,
                    actor_id=interaction.user.id, actor_name=str(interaction.user),
                )

        lines = [f'✅ Team "{named["full_name"]}" updated.']
        if new_full_name != current["full_name"]:
            lines.append(f'  Full name: "{current["full_name"]}" → "{new_full_name}"')
        if new_shorthand != current["name"]:
            lines.append(f"  Shorthand: `{current['name']}` → `{new_shorthand}`")
            old_file, new_file = normalise(current["name"]), normalise(new_shorthand)
            if old_file != new_file:
                lines.append(
                    f"  Its artwork is now looked for as `{new_file}`, not `{old_file}` — "
                    "rename the file in the team image directory."
                )
        if new_role is not None:
            lines.append(f"  Role: {new_role.mention}")
            if moved:
                lines.append(f"  {moved} seated driver(s) moved to the new role.")
        await interaction.followup.send("\n".join(lines), ephemeral=True)
        await self.bot.output_router.post_log(
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /team modify | Success\n"
            f"  team: {named['full_name']}\n"
            f"  shorthand: {named['name']}\n"
            + (f"  role: {new_role.name} (<@&{new_role.id}>)\n" if new_role is not None else "")
            + f"  seated drivers moved: {moved}",
        )

    @team_modify.autocomplete("team")
    @bounded_autocomplete()
    async def _modify_team_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """The teams `/team modify` acts on: the server's list, the Reserve team excepted."""
        return await team_autocomplete(self.bot, current, include_reserve=False)

    # ------------------------------------------------------------------
    # /team remove  (FR-004, FR-005, FR-006)
    # ------------------------------------------------------------------

    @team.command(
        name="remove",
        description="Remove a team from the server list, while no season's configuration is confirmed.",
    )
    @app_commands.describe(name="The team's shorthand.")
    @league_admin_only
    async def team_remove(
        self,
        interaction: discord.Interaction,
        name: str,
    ) -> None:
        if await self._team_list_lock(interaction, "remove"):
            return
        # A team is named by its shorthand (#381), offered by the autocomplete below.
        reference = await self.bot.team_service.resolve_server_team(name)
        if reference.team is None:
            await interaction.response.send_message(f"⛔ {reference.refusal}", ephemeral=True)
            return
        shorthand, full_name = reference.team["name"], reference.team["full_name"]
        try:
            await self.bot.team_service.remove_default_team(
                shorthand
            )
        except ValueError as exc:
            await interaction.response.send_message(f"⛔ {exc}", ephemeral=True)
            return

        await self.bot.placement_service.delete_team_role_config(
            shorthand,
            actor_id=interaction.user.id, actor_name=str(interaction.user),
        )

        await interaction.response.send_message(
            f'✅ Team "{full_name}" removed from the server list.', ephemeral=True
        )
        await self.bot.output_router.post_log(
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /team remove | Success\n"
            f"  team: {full_name}\n"
            f"  shorthand: {shorthand}",
        )

    @team_remove.autocomplete("name")
    @bounded_autocomplete()
    async def _remove_team_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """The teams `/team remove` acts on: the server's list, the Reserve team excepted."""
        return await team_autocomplete(self.bot, current, include_reserve=False)

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

        server_teams = await self.bot.team_service.get_teams_with_roles(

        )
        non_reserve = [t for t in server_teams if not t["is_reserve"]]

        if not non_reserve:
            await interaction.followup.send(
                "No teams configured. Use `/team add` to create one.", ephemeral=True
            )
            return

        def _fmt_team(t: dict) -> str:
            # All three names a team carries (#381): what is shown, what is typed, and the
            # role its drivers are given.
            role_part = f"<@&{t['role_id']}>" if t["role_id"] else "no role"
            return f"  {t['full_name']} — `{t['name']}` → {role_part}"

        server_lines = [_fmt_team(t) for t in non_reserve]
        reserve = next((t for t in server_teams if t["is_reserve"]), None)
        if reserve:
            server_lines.append(_fmt_team(reserve))

        setup_season = await self.bot.season_service.get_setup_season(

        )

        if setup_season is None:
            header = "**Server team list:**"
            content = header + "\n" + "\n".join(server_lines)
            await _send_long(interaction, content)
            return

        season_names = await self.bot.team_service.get_setup_season_team_names(
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

        season = await self.bot.season_service.get_confirmed_season(

        )
        if season is None:
            await interaction.followup.send("⛔ No season is being raced, so no lineup is confirmed.", ephemeral=True)
            return

        all_divisions = await self.bot.season_service.get_divisions(season.id)
        all_divisions = sorted(all_divisions, key=lambda d: d.tier)

        if division is not None:
            result = await self.bot.placement_service.resolve_division(
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
            teams = await self.bot.team_service.get_division_teams(
                div.id, committed_only=True
            )
            if not teams:
                lines.append("  *(no teams)*")
            else:
                for team in teams:
                    lines.append(f"  **{team['full_name']}**")
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
        """Set or clear the Reserve team's role, on the same terms `/team modify` sets a team's.

        Refused once the season is pending completion, for the reason given there, and refuses
        a role the bot cannot grant as every team command does (#381).
        """
        if await self._refuse_once_the_season_is_done(interaction, "reserve-role"):
            return
        if role is not None:
            refusal = role_grant_refusal(role)
            if refusal is not None:
                await interaction.response.send_message(f"⛔ {refusal}", ephemeral=True)
                return
        await interaction.response.defer(ephemeral=True)
        teams = await self.bot.team_service.get_teams_with_roles()
        old_role_id = next((t["role_id"] for t in teams if t["is_reserve"]), None)
        if role is not None:
            try:
                await self.bot.placement_service.set_team_role_config(
                    "Reserve", role.id,
                    actor_id=interaction.user.id, actor_name=str(interaction.user),
                )
            except ValueError as exc:
                # Another team holds the role (#375): nothing is changed and no driver moves.
                await interaction.followup.send(f"⛔ {exc}", ephemeral=True)
                return
            msg = f"✅ Reserve team role set to {role.mention}."
        else:
            await self.bot.placement_service.delete_team_role_config(
                "Reserve",
                actor_id=interaction.user.id, actor_name=str(interaction.user),
            )
            msg = "✅ Reserve team role cleared."

        # The drivers already seated in Reserve follow its role (issue #220).
        moved = await self.bot.placement_service.swap_team_role(
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


# ---------------------------------------------------------------------------
# The forms a team is added and changed on (#381)
# ---------------------------------------------------------------------------


class _TeamAddModal(LeagueModal, title="Add a team"):
    """The three things a team carries, taken together.

    The lengths are enforced as the manager types — Discord will not let a field exceed its
    ``max_length`` — and again by `TeamService`, which is where the rules live: a modal bounds
    the typing, it does not decide what is acceptable.
    """

    def __init__(self, cog: "TeamCog") -> None:
        super().__init__()
        self._cog = cog
        self.shorthand: discord.ui.TextInput = discord.ui.TextInput(
            placeholder="RBR", min_length=1, max_length=SHORTHAND_MAX,
        )
        self.full_name: discord.ui.TextInput = discord.ui.TextInput(
            placeholder="Oracle Red Bull Racing", min_length=1, max_length=FULL_NAME_MAX,
        )
        self.role: discord.ui.RoleSelect = discord.ui.RoleSelect(
            placeholder="The team's role", required=True,
        )
        self.add_item(discord.ui.Label(
            text="Shorthand",
            description="Typed to name the team, and the filename of its artwork.",
            component=self.shorthand,
        ))
        self.add_item(discord.ui.Label(
            text="Full name",
            description="What every post and graphic shows.",
            component=self.full_name,
        ))
        self.add_item(discord.ui.Label(
            text="Role",
            description="Granted to every driver placed in the team.",
            component=self.role,
        ))

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self._cog.add_team(
            interaction,
            shorthand=self.shorthand.value.strip(),
            full_name=self.full_name.value.strip(),
            role=self.role.values[0],
        )


class _TeamModifyModal(LeagueModal, title="Modify a team"):
    """A team's fields, pre-filled, and only those that may change now (#381).

    While the team list is open the form holds all three; once it is fixed it holds the role
    alone, because that is the only thing a league may still change. The windows are checked
    again on submit — a form can stand open across a stage change.
    """

    def __init__(self, cog: "TeamCog", *, team: Mapping[str, Any], names_offered: bool) -> None:
        super().__init__()
        self._cog = cog
        self._team = dict(team)
        self.shorthand: discord.ui.TextInput | None = None
        self.full_name: discord.ui.TextInput | None = None

        if names_offered:
            self.shorthand = discord.ui.TextInput(
                default=team["name"], min_length=1, max_length=SHORTHAND_MAX,
            )
            self.full_name = discord.ui.TextInput(
                default=team["full_name"], min_length=1, max_length=FULL_NAME_MAX,
            )
            self.add_item(discord.ui.Label(
                text="Shorthand",
                description="Typed to name the team, and the filename of its artwork.",
                component=self.shorthand,
            ))
            self.add_item(discord.ui.Label(
                text="Full name",
                description="What every post and graphic shows.",
                component=self.full_name,
            ))

        self.role: discord.ui.RoleSelect = discord.ui.RoleSelect(
            placeholder="The team's role",
            required=True,
            default_values=(
                [discord.Object(id=int(team["role_id"]))] if team.get("role_id") else []
            ),
        )
        self.add_item(discord.ui.Label(
            text="Role",
            description="Granted to every driver placed in the team.",
            component=self.role,
        ))

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self._cog.modify_team(
            interaction,
            was=self._team,
            shorthand=self.shorthand.value if self.shorthand is not None else None,
            full_name=self.full_name.value if self.full_name is not None else None,
            role=self.role.values[0] if self.role.values else None,
        )
