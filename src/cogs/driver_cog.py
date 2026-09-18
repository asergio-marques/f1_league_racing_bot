"""DriverCog — /driver command group."""
from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from models.season import ONGOING_STAGES, SeasonStage
from utils.channel_guard import league_admin_only, league_manager_only
from services.season_service import SeasonImmutableError

log = logging.getLogger(__name__)

#: The stages `/driver assign` and `/driver unassign` are available in (issue #220): while the
#: season is built, and mid-season for the drivers of the window just closed.
_PLACING_STAGES = frozenset({SeasonStage.PLACEMENTS, SeasonStage.ONGOING_PLACEMENTS})

_NOT_PLACING_REFUSAL = (
    "⛔ `{command}` is available only while the season is in placements, or mid-season "
    "while the drivers of a closed signup window are being placed."
)


class DriverCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
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
        """The member behind the current account of the driver *user* names (issue #243).

        Any account a driver has held names them, so a manager may give a command one the
        driver has since left. Everything the command does — the roles, the lineup, the
        log — belongs to the account the driver uses now, so it is swapped in here, before
        anything reads it. An account no driver holds is returned as it came.

        Where the current account is no longer in the server there is no member to act on,
        and the command is refused with the manager told so; returns None, having replied.
        """
        current = await self.bot.driver_service.current_account(  # type: ignore[attr-defined]
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
            await interaction.followup.send(
                f"⛔ <@{user.id}> is a past account of a driver whose current account, "
                f"<@{current}>, is not in the server.",
                ephemeral=True,
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
        """Make *new_user* the current account of the driver the old account names (#243).

        The account it replaces joins the driver's past accounts, and every account the driver
        has held goes on identifying them. Nothing the league holds is rewritten.
        """
        # Resolve old user ID — accept Member mention or raw snowflake string
        if old_user is not None:
            resolved_old_id = str(old_user.id)
        elif old_user_id is not None:
            resolved_old_id = old_user_id.strip()
        else:
            await interaction.response.send_message(
                "⛔ You must supply either `old_user` (mention) or `old_user_id` (raw snowflake).",
                ephemeral=True,
            )
            return

        server_id = interaction.guild_id
        new_user_id = str(new_user.id)
        actor_id = interaction.user.id
        actor_name = str(interaction.user)

        # Deferred: moving roles and a signup channel talks to Discord several times over.
        await interaction.response.defer(ephemeral=True)
        try:
            outcome = await self.bot.driver_service.reassign_user_id(  # type: ignore[attr-defined]
                server_id, resolved_old_id, new_user_id, actor_id, actor_name
            )
        except ValueError as exc:
            await interaction.followup.send(f"⛔ {exc}", ephemeral=True)
            return

        profile = outcome.profile
        replaced = outcome.replaced_account

        # The account change is committed. What follows is Discord's side of it, and a failure
        # there is reported rather than undoing it.
        problems: list[str] = []
        if interaction.guild is not None:
            try:
                problems += await self.bot.placement_service.move_driver_roles(  # type: ignore[attr-defined]
                    interaction.guild, server_id, profile.id, replaced, new_user_id
                )
            except Exception as exc:  # noqa: BLE001 — the reassign stands whatever Discord says
                log.exception("reassign: could not move the roles of driver %s", profile.id)
                problems.append(f"the roles could not be moved: {exc}")
            try:
                problems += await self.bot.wizard_service.move_held_channel(  # type: ignore[attr-defined]
                    server_id, replaced, new_user_id, interaction.guild
                )
            except Exception as exc:  # noqa: BLE001 — as the roles
                log.exception("reassign: could not move the signup channel of %s", replaced)
                problems.append(f"the signup channel could not be moved: {exc}")

        former = "Yes" if profile.former_driver else "No"
        past = [a for a in outcome.accounts if a != new_user_id]
        how = "switched back to a past account" if outcome.switched_back else "given a new account"
        if outcome.merged_accounts:
            how = (
                "merged with the driver on "
                + ", ".join(f"<@{a}>" for a in outcome.merged_accounts)
                + ", and given that account"
            )
        reply = (
            f"✅ Driver {how}.\n"
            f"   Current account : <@{new_user_id}>\n"
            f"   Past accounts   : {', '.join(f'<@{a}>' for a in past) or '—'}\n"
            f"   State           : {profile.current_state.value}\n"
            f"   Former driver   : {former}"
        )
        if problems:
            reply += "\n⚠️ Done, but on Discord:\n" + "\n".join(f"• {p}" for p in problems)
        await interaction.followup.send(reply, ephemeral=True)
        # After the reply, so that reading the image configuration and touching the league's
        # directory can never eat into Discord's three seconds.
        await self._remove_old_portrait(server_id, replaced)
        await self.bot.output_router.post_log(
            server_id,
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /driver reassign | Success\n"
            f"  replaced: <@{replaced}>\n"
            f"  current: {new_user.display_name} (<@{new_user_id}>)\n"
            f"  accounts: {', '.join(outcome.accounts)}"
            + "".join(f"\n  not done: {p}" for p in problems),
        )
        log.info(
            "Driver account changed on server %s: %s → %s by %s",
            server_id, replaced, new_user_id, actor_name,
        )

    async def _remove_old_portrait(self, server_id: int, discord_user_id: str) -> None:
        """Delete the portrait the bot obtained for the account a driver has just replaced.

        A portrait is a cache of one Discord account's own profile picture. Everything is
        drawn under the driver's current account (issue #243), so the replaced account's file
        would sit in the league's driver directory drawn by nothing — so it goes (issue #222).
        Switching back to that account later obtains its picture afresh, as for any driver.

        Where the league names no image configuration, or a directory that cannot be
        resolved, the file and its ownership row are **both** left alone. See
        `driver_portrait_service.remove_portrait` for why the row must never go on its own.

        Never raises. The reassign is committed by the time this runs, and a portrait is not
        worth reporting a successful command as a failure.
        """
        try:
            from services.driver_portrait_service import remove_portrait
            from services.image_render_service import resolve_configured_directories

            config = await self.bot.image_config_service.get_config()  # type: ignore[attr-defined]
            if config is None:
                return
            directories, _faults = resolve_configured_directories(
                config,
                (("driver", "driver_image_directory"),),
                image_type="driver_portraits",
            )
            directory = directories.get("driver")
            if directory is None:
                return
            await remove_portrait(
                self.bot.db_path, discord_user_id, directory  # type: ignore[attr-defined]
            )
        except Exception:  # noqa: BLE001 — a portrait never fails a command
            log.warning(
                "/driver reassign: could not remove the portrait of %s on server %s",
                discord_user_id, server_id, exc_info=True,
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
        team="Exact team name as it appears in the division.",
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
        server_id: int = interaction.guild_id  # type: ignore[assignment]
        user = await self._current_member(interaction, user)
        if user is None:
            return
        actor_id = interaction.user.id
        actor_name = str(interaction.user)

        # Resolve season, and the stage placements may be made in (issue #220)
        season = await self.bot.season_service.get_setup_or_active_season(server_id)  # type: ignore[attr-defined]
        if season is None or season.stage not in _PLACING_STAGES:
            await interaction.followup.send(
                _NOT_PLACING_REFUSAL.format(command="/driver assign"), ephemeral=True
            )
            return

        try:
            await self.bot.season_service.assert_season_mutable(season)  # type: ignore[attr-defined]
        except SeasonImmutableError:
            await interaction.followup.send(
                "❌ This season is archived (COMPLETED) and cannot be modified.",
                ephemeral=True,
            )
            return

        # Resolve division
        resolved = await self.bot.placement_service.resolve_division(  # type: ignore[attr-defined]
            season.id, division
        )
        if resolved is None:
            await interaction.followup.send(
                f"⛔ Division **{division}** not found in the active season.", ephemeral=True
            )
            return
        division_id, division_name = resolved

        # Fetch the driver profile
        profile = await self.bot.driver_service.get_profile(  # type: ignore[attr-defined]
            str(user.id)
        )
        if profile is None:
            await interaction.followup.send(
                f"⛓ No driver profile found for **{user.display_name}**.", ephemeral=True
            )
            return

        try:
            result = await self.bot.placement_service.assign_driver(  # type: ignore[attr-defined]
                server_id=server_id,
                driver_profile_id=profile.id,
                division_id=division_id,
                team_name=team,
                season_id=season.id,
                acting_user_id=actor_id,
                acting_user_name=actor_name,
                guild=interaction.guild,
                discord_user_id=str(user.id),
                committed=False,
                uncommitted_only=season.stage is SeasonStage.ONGOING_PLACEMENTS,
            )
        except ValueError as exc:
            await interaction.followup.send(f"⛔ {exc}", ephemeral=True)
            return

        verb = "Assigned"
        await interaction.followup.send(
            f"✅ {verb} **{user.display_name}** to **{result['team_name']}** "
            f"in **{result['division_name']}**.",
            ephemeral=True,
        )
        await self.bot.output_router.post_log(
            server_id,
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /driver assign | Success\n"
            f"  user: {user.display_name} (<@{user.id}>)\n"
            f"  team: {result['team_name']}\n"
            f"  division: {result['division_name']}",
        )
        log.info(
            "assign: server=%s user=%s → team=%s division=%s by %s",
            server_id, user.id, team, division_name, actor_name,
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
        server_id: int = interaction.guild_id  # type: ignore[assignment]
        user = await self._current_member(interaction, user)
        if user is None:
            return
        actor_id = interaction.user.id
        actor_name = str(interaction.user)

        season = await self.bot.season_service.get_setup_or_active_season(server_id)  # type: ignore[attr-defined]
        if season is None or season.stage not in _PLACING_STAGES:
            await interaction.followup.send(
                _NOT_PLACING_REFUSAL.format(command="/driver unassign"), ephemeral=True
            )
            return

        try:
            await self.bot.season_service.assert_season_mutable(season)  # type: ignore[attr-defined]
        except SeasonImmutableError:
            await interaction.followup.send(
                "❌ This season is archived (COMPLETED) and cannot be modified.",
                ephemeral=True,
            )
            return

        resolved = await self.bot.placement_service.resolve_division(  # type: ignore[attr-defined]
            season.id, division
        )
        if resolved is None:
            await interaction.followup.send(
                f"⛔ Division **{division}** not found in the active season.", ephemeral=True
            )
            return
        division_id, _division_name = resolved

        profile = await self.bot.driver_service.get_profile(  # type: ignore[attr-defined]
            str(user.id)
        )
        if profile is None:
            await interaction.followup.send(
                f"⛓ No driver profile found for **{user.display_name}**.", ephemeral=True
            )
            return

        try:
            result = await self.bot.placement_service.unassign_driver(  # type: ignore[attr-defined]
                server_id=server_id,
                driver_profile_id=profile.id,
                division_id=division_id,
                season_id=season.id,
                acting_user_id=actor_id,
                acting_user_name=actor_name,
                guild=interaction.guild,
                discord_user_id=str(user.id),
                uncommitted_only=season.stage is SeasonStage.ONGOING_PLACEMENTS,
            )
        except ValueError as exc:
            await interaction.followup.send(f"⛔ {exc}", ephemeral=True)
            return

        team_part = f"from **{result['team_name']}** " if result.get("team_name") else ""
        await interaction.followup.send(
            f"✅ Removed **{user.display_name}** {team_part}in **{result['division_name']}**.",
            ephemeral=True,
        )
        await self.bot.output_router.post_log(
            server_id,
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /driver unassign | Success\n"
            f"  user: {user.display_name} (<@{user.id}>)\n"
            f"  division: {result['division_name']}",
        )
        log.info(
            "unassign: server=%s user=%s from division=%s by %s",
            server_id, user.id, division, actor_name,
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
        team="Exact team name the driver is moved into.",
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
        """Move a committed driver in one step (issue #220).

        Full-time to Reserve, team to team, or a promotion or relegation between divisions,
        with the roles swapped and each lineup touched posted once. Available in the three
        ongoing stages; before placements are confirmed a seat is changed by unassigning and
        assigning again.
        """
        await interaction.response.defer(ephemeral=True)
        server_id: int = interaction.guild_id  # type: ignore[assignment]
        user = await self._current_member(interaction, user)
        if user is None:
            return

        season = await self.bot.season_service.get_confirmed_season(server_id)  # type: ignore[attr-defined]
        if season is None or season.stage not in ONGOING_STAGES:
            await interaction.followup.send(
                "⛔ `/driver move` is available only while the season is ongoing.",
                ephemeral=True,
            )
            return

        resolved_from = await self.bot.placement_service.resolve_division(  # type: ignore[attr-defined]
            season.id, from_division
        )
        if resolved_from is None:
            await interaction.followup.send(
                f"⛔ Division **{from_division}** not found in the active season.", ephemeral=True
            )
            return
        resolved_to = resolved_from
        if to_division is not None:
            resolved_to = await self.bot.placement_service.resolve_division(  # type: ignore[attr-defined]
                season.id, to_division
            )
            if resolved_to is None:
                await interaction.followup.send(
                    f"⛔ Division **{to_division}** not found in the active season.",
                    ephemeral=True,
                )
                return

        profile = await self.bot.driver_service.get_profile(str(user.id))  # type: ignore[attr-defined]
        if profile is None:
            await interaction.followup.send(
                f"⛓ No driver profile found for **{user.display_name}**.", ephemeral=True
            )
            return

        try:
            result = await self.bot.placement_service.move_driver(  # type: ignore[attr-defined]
                server_id=server_id,
                driver_profile_id=profile.id,
                season_id=season.id,
                from_division_id=resolved_from[0],
                to_division_id=resolved_to[0],
                team_name=team,
                acting_user_id=interaction.user.id,
                acting_user_name=str(interaction.user),
                guild=interaction.guild,
                discord_user_id=str(user.id),
            )
        except ValueError as exc:
            await interaction.followup.send(f"⛔ {exc}", ephemeral=True)
            return

        await interaction.followup.send(
            f"✅ Moved **{user.display_name}** from **{result['from_team']}** in "
            f"**{result['from_division']}** to **{result['to_team']}** in "
            f"**{result['to_division']}**.",
            ephemeral=True,
        )
        await self.bot.output_router.post_log(  # type: ignore[attr-defined]
            server_id,
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /driver move | Success\n"
            f"  user: {user.display_name} (<@{user.id}>)\n"
            f"  from: {result['from_team']}, {result['from_division']}\n"
            f"  to: {result['to_team']}, {result['to_division']}",
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
        """Release a committed driver from one division (issue #220), in the ongoing stages."""
        await interaction.response.defer(ephemeral=True)
        server_id: int = interaction.guild_id  # type: ignore[assignment]
        user = await self._current_member(interaction, user)
        if user is None:
            return

        season = await self.bot.season_service.get_confirmed_season(server_id)  # type: ignore[attr-defined]
        if season is None or season.stage not in ONGOING_STAGES:
            await interaction.followup.send(
                "⛔ `/driver release` is available only while the season is ongoing.",
                ephemeral=True,
            )
            return

        resolved = await self.bot.placement_service.resolve_division(  # type: ignore[attr-defined]
            season.id, division
        )
        if resolved is None:
            await interaction.followup.send(
                f"⛔ Division **{division}** not found in the active season.", ephemeral=True
            )
            return

        profile = await self.bot.driver_service.get_profile(str(user.id))  # type: ignore[attr-defined]
        if profile is None:
            await interaction.followup.send(
                f"⛓ No driver profile found for **{user.display_name}**.", ephemeral=True
            )
            return

        try:
            result = await self.bot.placement_service.release_driver(  # type: ignore[attr-defined]
                server_id=server_id,
                driver_profile_id=profile.id,
                division_id=resolved[0],
                season_id=season.id,
                acting_user_id=interaction.user.id,
                acting_user_name=str(interaction.user),
                guild=interaction.guild,
                discord_user_id=str(user.id),
            )
        except ValueError as exc:
            await interaction.followup.send(f"⛔ {exc}", ephemeral=True)
            return

        await interaction.followup.send(
            f"✅ Released **{user.display_name}** from **{result['division_name']}**.",
            ephemeral=True,
        )
        await self.bot.output_router.post_log(  # type: ignore[attr-defined]
            server_id,
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /driver release | Success\n"
            f"  user: {user.display_name} (<@{user.id}>)\n"
            f"  division: {result['division_name']}",
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
        """Turn down an Unassigned driver (issue #220).

        Available while the season is in Placements or Ongoing, placements — where the
        drivers of a window are settled. The driver returns to Not Signed Up and loses the
        signed-up role; their signup is kept with the season.
        """
        from models.driver_profile import DriverState

        await interaction.response.defer(ephemeral=True)
        server_id: int = interaction.guild_id  # type: ignore[assignment]
        user = await self._current_member(interaction, user)
        if user is None:
            return

        season = await self.bot.season_service.get_setup_or_active_season(server_id)  # type: ignore[attr-defined]
        if season is None or season.stage not in _PLACING_STAGES:
            await interaction.followup.send(
                _NOT_PLACING_REFUSAL.format(command="/driver reject"), ephemeral=True
            )
            return

        profile = await self.bot.driver_service.get_profile(str(user.id))  # type: ignore[attr-defined]
        if profile is None or profile.current_state is not DriverState.UNASSIGNED:
            await interaction.followup.send(
                f"⛔ **{user.display_name}** is not an Unassigned driver. A placed driver is "
                "unassigned first; a signup still in review is rejected from its panel.",
                ephemeral=True,
            )
            return

        await self.bot.driver_service.transition(  # type: ignore[attr-defined]
            str(user.id), DriverState.NOT_SIGNED_UP
        )
        await self.bot.signup_module_service.withdraw_approval(  # type: ignore[attr-defined]
            profile.id
        )

        signup_cfg = await self.bot.signup_module_service.get_config()  # type: ignore[attr-defined]
        role_id = getattr(signup_cfg, "signed_up_role_id", None)
        if role_id and interaction.guild is not None:
            role = interaction.guild.get_role(role_id)
            if role is not None:
                try:
                    await user.remove_roles(role, reason="Driver rejected")
                except discord.HTTPException as exc:
                    log.warning("reject: could not remove the signed-up role: %s", exc)

        await interaction.followup.send(
            f"✅ Turned down **{user.display_name}**. They are no longer signed up.",
            ephemeral=True,
        )
        await self.bot.output_router.post_log(  # type: ignore[attr-defined]
            server_id,
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /driver reject | Success\n"
            f"  user: {user.display_name} (<@{user.id}>)",
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
        server_id: int = interaction.guild_id  # type: ignore[assignment]
        user = await self._current_member(interaction, user)
        if user is None:
            return
        actor_id = interaction.user.id
        actor_name = str(interaction.user)

        # Sacking is available only while the season is ongoing (issue #220). Between seasons
        # every driver has already been returned to Not Signed Up by the season's end, and a
        # season still being built has no confirmed placement to sack anyone from.
        season = await self.bot.season_service.get_confirmed_season(server_id)  # type: ignore[attr-defined]
        if season is None or season.stage not in ONGOING_STAGES:
            await interaction.followup.send(
                "⛔ `/driver sack` is available only while the season is ongoing.",
                ephemeral=True,
            )
            return

        profile = await self.bot.driver_service.get_profile(  # type: ignore[attr-defined]
            str(user.id)
        )
        if profile is None:
            await interaction.followup.send(
                f"⛓ No driver profile found for **{user.display_name}**.", ephemeral=True
            )
            return

        try:
            await self.bot.placement_service.sack_driver(  # type: ignore[attr-defined]
                server_id=server_id,
                driver_profile_id=profile.id,
                season_id=season.id,
                acting_user_id=actor_id,
                acting_user_name=actor_name,
                guild=interaction.guild,
                discord_user_id=str(user.id),
            )
        except ValueError as exc:
            await interaction.followup.send(f"⛔ {exc}", ephemeral=True)
            return

        await interaction.followup.send(
            f"✅ **{user.display_name}** has been sacked. All roles and season assignments removed.",
            ephemeral=True,
        )
        await self.bot.output_router.post_log(
            server_id,
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /driver sack | Success\n"
            f"  user: {user.display_name} (<@{user.id}>)",
        )
        log.info(
            "sack: server=%s user=%s by %s",
            server_id, user.id, actor_name,
        )
