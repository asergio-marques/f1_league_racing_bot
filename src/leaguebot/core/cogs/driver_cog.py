"""DriverCog — /driver command group."""
from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from leaguebot.core.models.season import ONGOING_STAGES, SeasonStage
from leaguebot.core.services.drivers.driver_changes import (
    DRIVER_ASSIGN,
    DRIVER_MOVE,
    DRIVER_REASSIGN,
    DRIVER_REJECT,
    DRIVER_RELEASE,
    DRIVER_SACK,
    DRIVER_UNASSIGN,
    NOT_PLACING_REFUSAL,
    NOT_UNASSIGNED_REFUSAL,
    PAST_ACCOUNT_REFUSAL,
)
from leaguebot.core.utils.autocomplete import bounded_autocomplete, team_autocomplete
from leaguebot.core.utils.channel_guard import league_admin_only, league_manager_only
from leaguebot.core.utils.input_validator import parse_user_id
from leaguebot.core.utils.interaction_errors import describe
from leaguebot.core.utils.league_bot import LeagueBot
from leaguebot.core.utils.log_lines import refuse
from leaguebot.core.services.seasons.season_service import SeasonImmutableError

log = logging.getLogger(__name__)

#: The stages `/driver assign` and `/driver unassign` are available in: while the
#: season is built, and mid-season for the drivers of the window just closed.
_PLACING_STAGES = frozenset({SeasonStage.PLACEMENTS, SeasonStage.ONGOING_PLACEMENTS})


class DriverCog(commands.Cog):
    def __init__(self, bot: LeagueBot) -> None:
        self.bot = bot

    driver = app_commands.Group(
        name="driver",
        description="Driver profile management commands",
        guild_only=True,
        default_permissions=None,
    )

    async def _current_member(
        self, interaction: discord.Interaction, user: discord.Member
    ) -> discord.Member | None:
        """The member behind the current account of the driver *user* names.

        Any account a driver has held names them, so a manager may give a command one the
        driver has since left. Everything the command does — the roles, the lineup, the
        log — belongs to the account the driver uses now, so it is swapped in here, before
        anything reads it. An account no driver holds is returned as it came.

        Where the current account is no longer in the server there is no member to act on,
        and the command is refused with the manager told so; returns None, having replied.
        """
        current = await self.bot.driver_service.current_account(
            user.id
        )
        if current == str(user.id):
            return user
        guild = interaction.guild
        member = guild.get_member(int(current)) if guild is not None else None
        if member is None and guild is not None:
            try:
                member = await guild.fetch_member(int(current))
            except discord.HTTPException:
                member = None
        if member is None:
            await refuse(
                interaction,
                PAST_ACCOUNT_REFUSAL.format(account=user.id, current=current),
                what=describe(interaction),
            )
        return member

    # ------------------------------------------------------------------
    # /driver reassign
    # ------------------------------------------------------------------

    @driver.command(
        name="reassign",
        description="Make another Discord account a driver's current one.",
    )
    @app_commands.describe(
        old_user="Any account of the driver's (mention; use old_user_id for an account no longer in the server).",
        old_user_id="Raw Discord snowflake ID of any account of the driver's.",
        new_user="The account to make current: a new one, or one of the driver's past accounts.",
    )
    @league_manager_only
    async def reassign(
        self,
        interaction: discord.Interaction,
        new_user: discord.Member,
        old_user: discord.Member | None = None,
        old_user_id: str | None = None,
    ) -> None:
        """Make *new_user* the current account of the driver the old account names.

        The account it replaces joins the driver's past accounts, and every account the driver
        has held goes on identifying them. Nothing the league holds is rewritten.

        **Only while drivers are being placed** — Placements, or Ongoing, placements.
        Re-keying a profile onto another Discord account is part of settling who sits where;
        everywhere else `/driver move` is the command for changing where a driver sits. The
        consequence is accepted: a person who changes account between seasons is re-keyed
        once the next season reaches placements, which is the moment they would be placed
        anyway.
        """
        from leaguebot.core.utils.season_gate import PLACEMENT_STAGES, season_for_command

        # Checked before the parameters, so that a manager running it in the wrong stage is
        # told the rule rather than told they mis-typed a snowflake.
        if await season_for_command(
            interaction,
            self.bot.season_service,
            "driver reassign",
            stages=PLACEMENT_STAGES,
            refusal=(
                "⛔ A driver's account is changed only while drivers are being placed "
                "— while the season is in **Placements**, or in **Ongoing, placements** "
                "with the drivers of a closed signup window still to place. Elsewhere, "
                "`/driver move` is the command for changing where a driver sits."
            ),
        ) is None:
            return

        # Resolve old user ID — accept Member mention or raw snowflake string
        if old_user is not None:
            resolved_old_id = str(old_user.id)
        elif old_user_id is not None:
            parsed_old_id = parse_user_id(old_user_id)
            if parsed_old_id is None:
                await refuse(
                    interaction,
                    "⛔ `old_user_id` must be a numeric Discord user ID.",
                    what=describe(interaction),
                )
                return
            resolved_old_id = str(parsed_old_id)
        else:
            await refuse(
                interaction,
                "⛔ You must supply either `old_user` (mention) or `old_user_id` (raw snowflake).",
                what=describe(interaction),
            )
            return

        # The reassignment, its roles, the held signup channel and the portrait are a change on
        # the queue, which refuses again as it starts what the service refuses
        # (`core/services/drivers/driver_changes.py`).
        await self.bot.change_queue.ask(
            DRIVER_REASSIGN,
            {
                "old_user_id": resolved_old_id,
                "new_user_id": str(new_user.id),
                "new_name": new_user.display_name,
            },
            interaction=interaction, what=describe(interaction),
        )

    # ------------------------------------------------------------------
    # /driver assign
    # ------------------------------------------------------------------

    @driver.command(
        name="assign",
        description="Assign an Unassigned driver to a team and division.",
    )
    @app_commands.describe(
        user="The Discord member to assign.",
        division="Division tier number or name.",
        team="The team's shorthand.",
    )
    @league_manager_only
    async def assign(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        division: str,
        team: str,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        current = await self._current_member(interaction, user)
        if current is None:
            return
        user = current

        # Resolve season, and the stage placements may be made in
        season = await self.bot.season_service.get_setup_or_active_season()
        if season is None or season.stage not in _PLACING_STAGES:
            await refuse(
                interaction,
                NOT_PLACING_REFUSAL.format(command="/driver assign"),
                what=describe(interaction),
            )
            return

        try:
            await self.bot.season_service.assert_season_mutable(season)
        except SeasonImmutableError:
            await refuse(
                interaction,
                "❌ This season is archived (COMPLETED) and cannot be modified.",
                what=describe(interaction),
            )
            return

        # Resolve division
        resolved = await self.bot.placement_service.resolve_division(
            season.id, division
        )
        if resolved is None:
            await refuse(
                interaction,
                f"⛔ Division **{division}** not found in the active season.",
                what=describe(interaction),
            )
            return
        division_id, division_name = resolved

        # A team is typed by its shorthand.
        reference = await self.bot.team_service.resolve_division_team(
            division_id, team
        )
        if reference.team is None:
            await refuse(interaction, f"⛔ {reference.refusal}", what=describe(interaction))
            return

        # Fetch the driver profile
        profile = await self.bot.driver_service.get_profile(
            str(user.id)
        )
        if profile is None:
            await refuse(
                interaction,
                f"⛓ No driver profile found for **{user.display_name}**.",
                what=describe(interaction),
            )
            return

        # The assignment is a change on the queue, which refuses again as it starts what is
        # refused here (`core/services/drivers/driver_changes.py`).
        await self.bot.change_queue.ask(
            DRIVER_ASSIGN,
            {
                "driver_profile_id": profile.id,
                "discord_user_id": str(user.id),
                "driver_name": user.display_name,
                "season_id": season.id,
                "division_id": division_id,
                "division": division_name,
                "team_name": reference.team["name"],
                "team_full_name": reference.team["full_name"],
            },
            interaction=interaction, what=describe(interaction),
        )

    # ------------------------------------------------------------------
    # /driver unassign
    # ------------------------------------------------------------------

    @driver.command(
        name="unassign",
        description="Remove a driver's placement from a specific division.",
    )
    @app_commands.describe(
        user="The Discord member to unassign.",
        division="Division tier number or name.",
    )
    @league_manager_only
    async def unassign(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        division: str,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        current = await self._current_member(interaction, user)
        if current is None:
            return
        user = current

        season = await self.bot.season_service.get_setup_or_active_season()
        if season is None or season.stage not in _PLACING_STAGES:
            await refuse(
                interaction,
                NOT_PLACING_REFUSAL.format(command="/driver unassign"),
                what=describe(interaction),
            )
            return

        try:
            await self.bot.season_service.assert_season_mutable(season)
        except SeasonImmutableError:
            await refuse(
                interaction,
                "❌ This season is archived (COMPLETED) and cannot be modified.",
                what=describe(interaction),
            )
            return

        resolved = await self.bot.placement_service.resolve_division(
            season.id, division
        )
        if resolved is None:
            await refuse(
                interaction,
                f"⛔ Division **{division}** not found in the active season.",
                what=describe(interaction),
            )
            return
        division_id, division_name = resolved

        profile = await self.bot.driver_service.get_profile(
            str(user.id)
        )
        if profile is None:
            await refuse(
                interaction,
                f"⛓ No driver profile found for **{user.display_name}**.",
                what=describe(interaction),
            )
            return

        # The removal is a change on the queue, which refuses again as it starts what is
        # refused here (`core/services/drivers/driver_changes.py`).
        await self.bot.change_queue.ask(
            DRIVER_UNASSIGN,
            {
                "driver_profile_id": profile.id,
                "discord_user_id": str(user.id),
                "driver_name": user.display_name,
                "season_id": season.id,
                "division_id": division_id,
                "division": division_name,
            },
            interaction=interaction, what=describe(interaction),
        )

    # ------------------------------------------------------------------
    # /driver move
    # ------------------------------------------------------------------

    @driver.command(
        name="move",
        description="Move a confirmed driver to another team, in the same division or another.",
    )
    @app_commands.describe(
        user="The Discord member to move.",
        from_division="Division tier number or name the driver is moved from.",
        team="The shorthand of the team the driver is moved into.",
        to_division="Division tier number or name moved into. Omit for the same division.",
    )
    @league_manager_only
    async def move(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        from_division: str,
        team: str,
        to_division: str | None = None,
    ) -> None:
        """Move a committed driver in one step.

        Full-time to Reserve, team to team, or a promotion or relegation between divisions,
        with the roles swapped and each lineup touched posted once. Available in the three
        ongoing stages; before placements are confirmed a seat is changed by unassigning and
        assigning again.
        """
        await interaction.response.defer(ephemeral=True)
        current = await self._current_member(interaction, user)
        if current is None:
            return
        user = current

        season = await self.bot.season_service.get_confirmed_season()
        if season is None or season.stage not in ONGOING_STAGES:
            await refuse(
                interaction,
                "⛔ `/driver move` is available only while the season is ongoing.",
                what=describe(interaction),
            )
            return

        resolved_from = await self.bot.placement_service.resolve_division(
            season.id, from_division
        )
        if resolved_from is None:
            await refuse(
                interaction,
                f"⛔ Division **{from_division}** not found in the active season.",
                what=describe(interaction),
            )
            return
        resolved_to = resolved_from
        if to_division is not None:
            found_to = await self.bot.placement_service.resolve_division(
                season.id, to_division
            )
            if found_to is None:
                await refuse(
                    interaction,
                    f"⛔ Division **{to_division}** not found in the active season.",
                    what=describe(interaction),
                )
                return
            resolved_to = found_to

        # A team is typed by its shorthand, in the division moved into.
        reference = await self.bot.team_service.resolve_division_team(
            resolved_to[0], team
        )
        if reference.team is None:
            await refuse(interaction, f"⛔ {reference.refusal}", what=describe(interaction))
            return

        profile = await self.bot.driver_service.get_profile(str(user.id))
        if profile is None:
            await refuse(
                interaction,
                f"⛓ No driver profile found for **{user.display_name}**.",
                what=describe(interaction),
            )
            return

        # The move, its roles and its lineups are a change on the queue, which refuses again
        # as it starts what is refused here (`core/services/drivers/driver_changes.py`).
        await self.bot.change_queue.ask(
            DRIVER_MOVE,
            {
                "driver_profile_id": profile.id,
                "discord_user_id": str(user.id),
                "driver_name": user.display_name,
                "season_id": season.id,
                "from_division_id": resolved_from[0],
                "from_division": resolved_from[1],
                "to_division_id": resolved_to[0],
                "to_division": resolved_to[1],
                "team_name": reference.team["name"],
                "team_full_name": reference.team["full_name"],
            },
            interaction=interaction, what=describe(interaction),
        )

    # ------------------------------------------------------------------
    # /driver release
    # ------------------------------------------------------------------

    @driver.command(
        name="release",
        description="Release a confirmed driver from one division, keeping their other seats.",
    )
    @app_commands.describe(
        user="The Discord member to release.",
        division="Division tier number or name to release them from.",
    )
    @league_manager_only
    async def release(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        division: str,
    ) -> None:
        """Release a committed driver from one division, in the ongoing stages."""
        await interaction.response.defer(ephemeral=True)
        current = await self._current_member(interaction, user)
        if current is None:
            return
        user = current

        season = await self.bot.season_service.get_confirmed_season()
        if season is None or season.stage not in ONGOING_STAGES:
            await refuse(
                interaction,
                "⛔ `/driver release` is available only while the season is ongoing.",
                what=describe(interaction),
            )
            return

        resolved = await self.bot.placement_service.resolve_division(
            season.id, division
        )
        if resolved is None:
            await refuse(
                interaction,
                f"⛔ Division **{division}** not found in the active season.",
                what=describe(interaction),
            )
            return

        profile = await self.bot.driver_service.get_profile(str(user.id))
        if profile is None:
            await refuse(
                interaction,
                f"⛓ No driver profile found for **{user.display_name}**.",
                what=describe(interaction),
            )
            return

        # The release, its roles and its lineup are a change on the queue, which refuses again as
        # it starts what is refused here (`core/services/drivers/driver_changes.py`).
        await self.bot.change_queue.ask(
            DRIVER_RELEASE,
            {
                "driver_profile_id": profile.id,
                "discord_user_id": str(user.id),
                "driver_name": user.display_name,
                "season_id": season.id,
                "division_id": resolved[0],
                "division": resolved[1],
            },
            interaction=interaction, what=describe(interaction),
        )

    # ------------------------------------------------------------------
    # /driver reject
    # ------------------------------------------------------------------

    @driver.command(
        name="reject",
        description="Turn down an approved driver who has not been placed.",
    )
    @app_commands.describe(user="The Unassigned driver to turn down.")
    @league_manager_only
    async def reject(self, interaction: discord.Interaction, user: discord.Member) -> None:
        """Turn down an Unassigned driver.

        Available while the season is in Placements or Ongoing, placements — where the
        drivers of a window are settled. The driver returns to Not Signed Up and loses the
        driver role; their signup is kept with the season.
        """
        from leaguebot.core.models.driver_profile import DriverState

        await interaction.response.defer(ephemeral=True)
        current = await self._current_member(interaction, user)
        if current is None:
            return
        user = current

        season = await self.bot.season_service.get_setup_or_active_season()
        if season is None or season.stage not in _PLACING_STAGES:
            await refuse(
                interaction,
                NOT_PLACING_REFUSAL.format(command="/driver reject"),
                what=describe(interaction),
            )
            return

        profile = await self.bot.driver_service.get_profile(str(user.id))
        if profile is None or profile.current_state is not DriverState.UNASSIGNED:
            await refuse(
                interaction,
                NOT_UNASSIGNED_REFUSAL.format(driver_name=user.display_name),
                what=describe(interaction),
            )
            return

        # The turn-down, its approval's withdrawal, its audit entry and the driver role are a
        # change on the queue, which refuses again as it starts what is refused here
        # (`core/services/drivers/driver_changes.py`).
        await self.bot.change_queue.ask(
            DRIVER_REJECT,
            {
                "driver_profile_id": profile.id,
                "discord_user_id": str(user.id),
                "driver_name": user.display_name,
                "season_id": season.id,
            },
            interaction=interaction, what=describe(interaction),
        )

    # ------------------------------------------------------------------
    # /driver sack
    # ------------------------------------------------------------------

    @driver.command(
        name="sack",
        description="Sack a driver: revoke all roles, clear assignments, revert to Not Signed Up.",
    )
    @app_commands.describe(
        user="The Discord member to sack.",
    )
    @league_admin_only
    async def sack(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        current = await self._current_member(interaction, user)
        if current is None:
            return
        user = current

        # Sacking is available only while the season is ongoing. Between seasons every driver has
        # already been returned to Not Signed Up by the season's end, and a season still being built
        # has no confirmed placement to sack anyone from.
        season = await self.bot.season_service.get_confirmed_season()
        if season is None or season.stage not in ONGOING_STAGES:
            await refuse(
                interaction,
                "⛔ `/driver sack` is available only while the season is ongoing.",
                what=describe(interaction),
            )
            return

        profile = await self.bot.driver_service.get_profile(
            str(user.id)
        )
        if profile is None:
            await refuse(
                interaction,
                f"⛓ No driver profile found for **{user.display_name}**.",
                what=describe(interaction),
            )
            return

        # The sack, its roles and its lineups are a change on the queue, which refuses again as
        # it starts what is refused here (`core/services/drivers/driver_changes.py`).
        await self.bot.change_queue.ask(
            DRIVER_SACK,
            {
                "driver_profile_id": profile.id,
                "discord_user_id": str(user.id),
                "driver_name": user.display_name,
                "season_id": season.id,
            },
            interaction=interaction, what=describe(interaction),
        )

    # ------------------------------------------------------------------
    # The team parameter's autocomplete
    # ------------------------------------------------------------------

    @bounded_autocomplete()
    async def _team_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """A team is typed by its shorthand; offered under both its names, Reserve included."""
        return await team_autocomplete(self.bot, current, include_reserve=True)

    assign.autocomplete("team")(_team_autocomplete)
    move.autocomplete("team")(_team_autocomplete)
