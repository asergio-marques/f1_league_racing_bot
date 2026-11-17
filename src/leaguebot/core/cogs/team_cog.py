"""TeamCog — /team command group."""
from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from leaguebot.core.models.season import SeasonStage
from leaguebot.core.services.drivers.lineup_command import lineups_for_command
from leaguebot.core.services.drivers.team_changes import (
    TEAM_ADD,
    TEAM_MODIFY,
    TEAM_REMOVE,
    TEAM_RESERVE_ROLE,
    gone_reply,
    list_lock_reply,
    season_done_reply,
)
from leaguebot.core.services.drivers.team_service import FULL_NAME_MAX, SHORTHAND_MAX
from leaguebot.framework.utils.autocomplete import bounded_autocomplete
from leaguebot.core.utils.team_autocomplete import team_autocomplete
from leaguebot.core.utils.channel_guard import league_admin_only, league_manager_only, changes_nothing
from leaguebot.framework.utils.interaction_errors import describe, describe_form
from leaguebot.framework.utils.league_bot import LeagueBot
from leaguebot.framework.utils.league_server import LeagueModal
from leaguebot.framework.utils.log_lines import drawn_notices_section, refuse
from leaguebot.utils.messages import chunk_message

log = logging.getLogger(__name__)

_MAX_MSG_LEN = 1900  # leave headroom below Discord's 2000 char limit


class TeamCog(commands.Cog):
    def __init__(self, bot: LeagueBot) -> None:
        self.bot = bot

    async def _team_list_is_open(self) -> bool:
        """Whether the team list may still be changed: no live season, or one in Configuration.

        The question apart from the refusal, because `/team modify` asks it to decide which
        fields its form offers, and asks it again when the form comes back.
        """
        season = await self.bot.season_service.get_setup_or_active_season()
        return season is None or season.stage is SeasonStage.CONFIGURATION

    async def _team_list_lock(
        self, interaction: discord.Interaction, command: str, *, what: str
    ) -> bool:
        """Refuse a change to the team list once a season's configuration is confirmed.

        The team list is settled while the active season stands in Configuration, and is
        fixed for the rest of that season from the moment its configuration is confirmed:
        the signup wizard offers it as the preferred teams, and every division is created
        from it. With no active season the list is free. Returns True where the command was
        refused, having answered the interaction and recorded the refusal of *what* — the
        command, or the form it was submitted on. The refusal is the change's own
        (`team_changes.list_lock_reply`), which the queue judges again as the change starts.
        """
        reply = list_lock_reply(
            await self.bot.season_service.get_setup_or_active_season(), command
        )
        if reply is None:
            return False
        await refuse(interaction, reply, what=what)
        return True

    async def _refuse_once_the_season_is_done(
        self, interaction: discord.Interaction, command: str, *, what: str
    ) -> bool:
        """Refuse a role mapping once every division of the season is finished or cancelled.

        A team's role may be repaired in every stage of a live season but Pending completion,
        and with no live season at all the mapping is the server's own and free to change — the
        team list itself is settled on the same terms. So this refuses only where a season is live
        and has run its course, and lets the no-season case through, which the shared gate would
        otherwise turn away.

        Returns True where the command was refused, having answered the interaction and
        recorded the refusal of *what*. The refusal is the change's own
        (`team_changes.season_done_reply`), which the queue judges again as the change starts.
        """
        reply = season_done_reply(
            await self.bot.season_service.get_setup_or_active_season(), command
        )
        if reply is None:
            return False
        await refuse(interaction, reply, what=what)
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
        """Open the form a team is added on.

        A team carries three things, and the form takes all three at once: the shorthand a
        league types, the full name every post shows, and the role its drivers are granted.
        The two names are bounded as they are typed, which is the one thing a modal does that
        a command parameter cannot.

        **No deferral.** `send_modal` has to be the interaction's first response, so every
        check before it is a quick read — and every one of them runs again on submit, because
        a season can move on while the form is open.
        """
        if await self._team_list_lock(interaction, "add", what=describe(interaction)):
            return
        await interaction.response.send_modal(_TeamAddModal(self))

    async def add_team(
        self,
        interaction: discord.Interaction,
        *,
        shorthand: str,
        full_name: str,
        role: discord.Role,
        what: str | None = None,
    ) -> None:
        """Ask for the team the form describes to be added, or say why not.

        The team's addition is a change on the queue (`core/services/drivers/team_changes.py`), which
        refuses at once, and again as the change starts, whatever the list, the role or the
        team's names do not allow. *what* names what a refusal is recorded against: the form the
        team was submitted on, which the form passes; a call without one names the command.
        """
        await self.bot.change_queue.ask(
            TEAM_ADD,
            {
                "shorthand": shorthand,
                "full_name": full_name,
                "role_id": role.id,
                "role_name": role.name,
            },
            interaction=interaction, what=what or describe(interaction),
        )

    # ------------------------------------------------------------------
    # /team modify
    # ------------------------------------------------------------------

    @team.command(
        name="modify",
        description="Change a team's names or its role.",
    )
    @app_commands.describe(team="The team's shorthand.")
    @league_manager_only
    async def team_modify(self, interaction: discord.Interaction, team: str) -> None:
        """Open the form a team is changed on.

        The form offers what may change **now**: the two names only while the team list is
        open, and the role at every stage but Pending completion, where nothing may change at
        all and the command is refused before any form opens. Every field comes pre-filled, so
        submitting it unchanged changes nothing.

        The Reserve team is sent to `/team reserve-role`, which is the command that owns it.
        """
        if await self._refuse_once_the_season_is_done(
            interaction, "modify", what=describe(interaction)
        ):
            return
        reference = await self.bot.team_service.resolve_server_team(team)
        if reference.team is None:
            await refuse(interaction, f"⛔ {reference.refusal}", what=describe(interaction))
            return
        if reference.team["is_reserve"]:
            await refuse(
                interaction,
                "⛔ The Reserve team's role is set with `/team reserve-role`, and its names "
                "are not a league's to change.",
                what=describe(interaction),
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
        what: str | None = None,
    ) -> None:
        """Ask for what the form changed, or refuse the whole submission.

        *was* is the team as the form opened on it. Only a field whose value **changed**
        counts, so a form submitted untouched changes nothing and says so. The change is one on
        the queue (`core/services/drivers/team_changes.py`), which holds each changed field to its own
        window at the ask, because the season may have moved on while the form stood open, and
        again as the change starts: a change that no longer stands refuses the submission as a
        whole, naming the field, and nothing at all is written. *what* names what a refusal is
        recorded against: the form, which passes it; a call without one names the command.
        """
        what = what or describe(interaction)
        await interaction.response.defer(ephemeral=True)
        reference = await self.bot.team_service.resolve_server_team(was["name"])
        if reference.team is None:
            await refuse(interaction, gone_reply(was["full_name"]), what=what)
            return
        current = reference.team

        new_shorthand = (shorthand or "").strip() or current["name"]
        new_full_name = (full_name or "").strip() or current["full_name"]
        names_changed = (new_shorthand, new_full_name) != (current["name"], current["full_name"])
        new_role = role if role is not None and role.id != current["role_id"] else None

        if not names_changed and new_role is None:
            await interaction.followup.send(
                f'Nothing changed: "{current["full_name"]}" stands as it was.', ephemeral=True
            )
            await self.bot.output_router.post_log(
                f"{interaction.user.display_name} (<@{interaction.user.id}>) | /team modify | "
                "Nothing changed\n"
                f"  team: {current['full_name']}\n"
                "  the form was submitted as it stood"
            )
            return

        await self.bot.change_queue.ask(
            TEAM_MODIFY,
            {
                "was_shorthand": current["name"],
                "was_full_name": current["full_name"],
                "shorthand": new_shorthand,
                "full_name": new_full_name,
                "role_id": None if new_role is None else new_role.id,
                "role_name": None if new_role is None else new_role.name,
            },
            interaction=interaction, what=what,
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
        if await self._team_list_lock(interaction, "remove", what=describe(interaction)):
            return
        # A team is named by its shorthand, offered by the autocomplete below.
        reference = await self.bot.team_service.resolve_server_team(name)
        if reference.team is None:
            await refuse(interaction, f"⛔ {reference.refusal}", what=describe(interaction))
            return
        await self.bot.change_queue.ask(
            TEAM_REMOVE,
            {"shorthand": reference.team["name"], "full_name": reference.team["full_name"]},
            interaction=interaction, what=describe(interaction),
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
    @changes_nothing
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
            # All three names a team carries: what is shown, what is typed, and the role its drivers
            # are given.
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

    @staticmethod
    async def _tell_notices(interaction: discord.Interaction, section: str) -> None:
        """Tell the manager alone the notices of the pictures drawn, one follow-up for each part
        Discord's message limit leaves: a lineup of several divisions can note more than one
        message holds, and a failed send would cost the manager the notices and their link."""
        if not section:
            return
        for part in chunk_message(section):
            await interaction.followup.send(part, ephemeral=True)

    @team.command(
        name="lineup",
        description="Show the confirmed team lineups of the season being raced.",
    )
    @app_commands.describe(
        division="Division name or tier number. Omit to show all divisions.",
        public="Post the lineup visibly in the channel (default: only visible to you).",
    )
    @league_manager_only
    @changes_nothing
    async def team_lineup(
        self,
        interaction: discord.Interaction,
        division: str | None = None,
        public: bool = False,
    ) -> None:
        await interaction.response.defer(ephemeral=not public)
        acknowledgement = _PublicAcknowledgement(interaction, standing=public)
        try:
            await self._show_lineup(interaction, division, public, acknowledgement)
        except Exception:
            # The command tree answers a fault with a reply the manager alone should see, which
            # a standing public acknowledgement would show in the channel instead.
            await acknowledgement.take_down()
            raise

    async def _show_lineup(
        self,
        interaction: discord.Interaction,
        division: str | None,
        public: bool,
        acknowledgement: _PublicAcknowledgement,
    ) -> None:
        """Answer `/team lineup` once it is acknowledged.

        Every refusal is sent to the manager alone, and so first takes down the public
        acknowledgement (`_PublicAcknowledgement`), which would otherwise give the first
        follow-up its own visibility and show the refusal in the channel (Constitution XIV.4).
        """
        season = await self.bot.season_service.get_confirmed_season(

        )
        if season is None:
            await acknowledgement.take_down()
            await interaction.followup.send("⛔ No season is being raced, so no lineup is confirmed.", ephemeral=True)
            return

        all_divisions = await self.bot.season_service.get_divisions(season.id)
        all_divisions = sorted(all_divisions, key=lambda d: d.tier)

        if division is not None:
            result = await self.bot.placement_service.resolve_division(
                season.id, division
            )
            if result is None:
                await acknowledgement.take_down()
                await interaction.followup.send(f"⛔ Division `{division}` not found.", ephemeral=True)
                return
            div_id, _ = result
            all_divisions = [d for d in all_divisions if d.id == div_id]

        if not all_divisions:
            await acknowledgement.take_down()
            await interaction.followup.send("No divisions found.", ephemeral=True)
            return

        # ── The graphic replaces the textual output where configured (FR-026) ──
        #
        # One image per division, honouring `public`. Which divisions are drawn, the refusal
        # rather than a fall back to text, and the release of every picture drawn are
        # `lineups_for_command`'s; the replies are the command's. `discord.File` is opened only
        # once every division has drawn clean, and closed again before the block hands the paths
        # back to be released, so no render is unlinked while its handle is still open — the
        # model `calendar_post_service.replace_calendar_message` and
        # `LineupPoster._refresh_lineup_post` both set.
        async with lineups_for_command(
            self.bot.image_drawing, interaction.guild, all_divisions
        ) as lineups:
            if lineups.drawn:
                # The notices are told to the manager alone, whatever `public` says: a channel
                # the drivers read carries the picture and nothing else (Constitution XIV.4).
                section = drawn_notices_section(lineups.notices)
                if lineups.problem is not None:
                    refusal = (
                        f"❌ {lineups.problem}\n"
                        f"_Lineup for **{lineups.refused}** was not drawn._"
                    )
                    # The section is always a follow-up of its own, never carried in the refusal.
                    await acknowledgement.take_down()
                    await interaction.followup.send(refusal, ephemeral=True)
                    await self._tell_notices(interaction, section)
                    return

                if lineups.paths:
                    files = [
                        discord.File(str(path), filename=path.name)
                        for path in lineups.paths
                    ]
                    try:
                        text = "\n".join(f"**{div.name}**" for div in all_divisions)
                        await interaction.followup.send(
                            text, files=files, ephemeral=not public
                        )
                        acknowledgement.sent()
                        await self._tell_notices(interaction, section)
                        return
                    finally:
                        for attachment in files:
                            attachment.close()

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

        # The acknowledgement is gone only once the first message has gone out: where Discord
        # refuses that one, the failure reply still has to be taken down first.
        await _send_long(
            interaction,
            "\n".join(lines).rstrip(),
            ephemeral=not public,
            on_first_sent=acknowledgement.sent,
        )

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
        """Ask for the Reserve team's role to be set or cleared, on the same terms `/team modify`
        sets a team's.

        The change is one on the queue (`core/services/drivers/team_changes.py`), which refuses at once,
        and again as the change starts, once the season is pending completion, and a role the bot
        cannot grant or another team holds, as every team command does.
        """
        await self.bot.change_queue.ask(
            TEAM_RESERVE_ROLE,
            {
                "role_id": None if role is None else role.id,
                "role_name": None if role is None else role.name,
            },
            interaction=interaction, what=describe(interaction),
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOT_SHOWN = "⛔ The lineup could not be shown."


class _PublicAcknowledgement:
    """The "thinking…" a public `/team lineup` shows until its first follow-up.

    Discord gives the first follow-up after a deferral the deferral's visibility, whatever the
    follow-up asks, so a refusal sent first would be shown in the channel the drivers read
    (Constitution XIV.4). `take_down` deletes the acknowledgement while it stands, so that the next
    follow-up is seen as it asks, by the manager alone. Where Discord refuses the deletion, the
    channel is given one neutral line, naming no problem, as that first follow-up, and the refusal
    follows privately all the same. `standing` is cleared once anything has been sent, after which
    there is nothing to take down: deleting the original then would delete what was sent.
    """

    def __init__(self, interaction: discord.Interaction, *, standing: bool) -> None:
        self._interaction = interaction
        self.standing = standing

    def sent(self) -> None:
        """Note that a follow-up has gone out, so the acknowledgement is no longer standing."""
        self.standing = False

    async def take_down(self) -> None:
        if not self.standing:
            return
        self.standing = False
        try:
            await self._interaction.delete_original_response()
        except discord.HTTPException:
            log.warning("could not delete the public acknowledgement of /team lineup", exc_info=True)
            try:
                await self._interaction.followup.send(_NOT_SHOWN, ephemeral=False)
            except discord.HTTPException:
                log.warning("could not show the channel that the lineup was not shown", exc_info=True)


async def _send_long(
    interaction: discord.Interaction,
    text: str,
    *,
    ephemeral: bool = True,
    on_first_sent: Callable[[], None] | None = None,
) -> None:
    """Send potentially-long text, splitting into followup chunks if needed.

    `on_first_sent` is called once the first chunk has gone out, and not before.
    """
    chunks = [text]
    if len(text) > _MAX_MSG_LEN:
        chunks = [text[i:i + _MAX_MSG_LEN] for i in range(0, len(text), _MAX_MSG_LEN)]
    for number, chunk in enumerate(chunks):
        await interaction.followup.send(chunk, ephemeral=ephemeral)
        if number == 0 and on_first_sent is not None:
            on_first_sent()


# ---------------------------------------------------------------------------
# The forms a team is added and changed on
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
            what=describe_form(self),
        )


class _TeamModifyModal(LeagueModal, title="Modify a team"):
    """A team's fields, pre-filled, and only those that may change now.

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
            what=describe_form(self),
        )
