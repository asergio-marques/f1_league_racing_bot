"""ResultsCog — /results config, /results amend, /results reserves, /round results commands."""
from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from models.points_config import SessionType
from services import points_config_service, season_points_service
from services.points_config_service import (
    ConfigAlreadyExistsError,
    ConfigNotFoundError,
    InvalidSessionTypeError,
)
from services.season_points_service import (
    ConfigNotAttachedError,
    SeasonNotInSetupError,
)
from utils.channel_guard import admin_only, channel_guard

log = logging.getLogger(__name__)

_SESSION_CHOICES = [
    app_commands.Choice(name="Sprint Qualifying", value="SPRINT_QUALIFYING"),
    app_commands.Choice(name="Sprint Race", value="SPRINT_RACE"),
    app_commands.Choice(name="Feature Qualifying", value="FEATURE_QUALIFYING"),
    app_commands.Choice(name="Feature Race", value="FEATURE_RACE"),
]

_RACE_SESSION_CHOICES = [
    app_commands.Choice(name="Sprint Race", value="SPRINT_RACE"),
    app_commands.Choice(name="Feature Race", value="FEATURE_RACE"),
]


class ResultsCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ------------------------------------------------------------------
    # Gate helpers
    # ------------------------------------------------------------------

    async def _module_gate(self, interaction: discord.Interaction) -> bool:
        if not await self.bot.module_service.is_results_enabled(interaction.guild_id):
            await interaction.response.send_message(
                "\u274c The Results & Standings module is not enabled on this server.",
                ephemeral=True,
            )
            return False
        return True

    # ------------------------------------------------------------------
    # /results config group
    # ------------------------------------------------------------------

    results_group = app_commands.Group(name="results", description="Results & Standings commands")
    config_group = app_commands.Group(
        name="config", description="Points configuration management", parent=results_group
    )

    @config_group.command(name="add", description="Add a named points configuration to this server.")
    @app_commands.describe(name="Unique config name, e.g. '100%'")
    @channel_guard
    @admin_only
    async def config_add(self, interaction: discord.Interaction, name: str) -> None:
        if not await self._module_gate(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        try:
            await points_config_service.create_config(self.bot.db_path, interaction.guild_id, name)
        except ConfigAlreadyExistsError:
            await interaction.followup.send(
                f"\u274c A config named **{name}** already exists on this server.", ephemeral=True
            )
            return
        await interaction.followup.send(
            f"\u2705 Config **{name}** created. All positions default to 0 points.", ephemeral=True
        )

    @config_group.command(name="remove", description="Remove a named points configuration.")
    @app_commands.describe(name="Config name to remove")
    @channel_guard
    @admin_only
    async def config_remove(self, interaction: discord.Interaction, name: str) -> None:
        if not await self._module_gate(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        try:
            await points_config_service.remove_config(self.bot.db_path, interaction.guild_id, name)
        except ConfigNotFoundError:
            await interaction.followup.send(
                f"\u274c Config **{name}** not found.", ephemeral=True
            )
            return
        await interaction.followup.send(f"\u2705 Config **{name}** removed.", ephemeral=True)

    @config_group.command(name="session", description="Set points for a finishing position in a session type.")
    @app_commands.describe(
        name="Config name",
        session="Session type",
        position="Finishing position (1-indexed)",
        points="Points awarded",
    )
    @app_commands.choices(session=_SESSION_CHOICES)
    @channel_guard
    @admin_only
    async def config_session(
        self,
        interaction: discord.Interaction,
        name: str,
        session: app_commands.Choice[str],
        position: int,
        points: int,
    ) -> None:
        if not await self._module_gate(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        try:
            await points_config_service.set_session_points(
                self.bot.db_path,
                interaction.guild_id,
                name,
                SessionType(session.value),
                position,
                points,
            )
        except ConfigNotFoundError:
            await interaction.followup.send(f"\u274c Config **{name}** not found.", ephemeral=True)
            return
        await interaction.followup.send(
            f"\u2705 Set **{session.name}** position {position} \u2192 {points} pts in config **{name}**.",
            ephemeral=True,
        )

    @config_group.command(name="fl", description="Set the fastest-lap bonus for a race session type.")
    @app_commands.describe(
        name="Config name",
        session="Race session type (Sprint Race or Feature Race)",
        points="Bonus points for fastest lap",
    )
    @app_commands.choices(session=_RACE_SESSION_CHOICES)
    @channel_guard
    @admin_only
    async def config_fl(
        self,
        interaction: discord.Interaction,
        name: str,
        session: app_commands.Choice[str],
        points: int,
    ) -> None:
        if not await self._module_gate(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        try:
            await points_config_service.set_fl_bonus(
                self.bot.db_path, interaction.guild_id, name, SessionType(session.value), points
            )
        except ConfigNotFoundError:
            await interaction.followup.send(f"\u274c Config **{name}** not found.", ephemeral=True)
            return
        except InvalidSessionTypeError:
            await interaction.followup.send(
                "\u274c Fastest-lap bonus cannot be set for qualifying sessions.", ephemeral=True
            )
            return
        await interaction.followup.send(
            f"\u2705 Set fastest-lap bonus for **{session.name}** \u2192 {points} pts in config **{name}**.",
            ephemeral=True,
        )

    @config_group.command(name="fl-plimit", description="Set the position eligibility limit for fastest-lap bonus.")
    @app_commands.describe(
        name="Config name",
        session="Race session type (Sprint Race or Feature Race)",
        limit="Highest eligible position (e.g. 10 → positions 1–10 eligible)",
    )
    @app_commands.choices(session=_RACE_SESSION_CHOICES)
    @channel_guard
    @admin_only
    async def config_fl_plimit(
        self,
        interaction: discord.Interaction,
        name: str,
        session: app_commands.Choice[str],
        limit: int,
    ) -> None:
        if not await self._module_gate(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        try:
            await points_config_service.set_fl_position_limit(
                self.bot.db_path, interaction.guild_id, name, SessionType(session.value), limit
            )
        except ConfigNotFoundError:
            await interaction.followup.send(f"\u274c Config **{name}** not found.", ephemeral=True)
            return
        except InvalidSessionTypeError:
            await interaction.followup.send(
                "\u274c Position limit cannot be set for qualifying sessions.", ephemeral=True
            )
            return
        await interaction.followup.send(
            f"\u2705 Set fastest-lap position limit for **{session.name}** \u2192 top {limit} eligible in config **{name}**.",
            ephemeral=True,
        )

    @config_group.command(name="append", description="Attach a server config to the current season in SETUP.")
    @app_commands.describe(name="Config name to attach")
    @channel_guard
    @admin_only
    async def config_append(self, interaction: discord.Interaction, name: str) -> None:
        if not await self._module_gate(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        season = await self.bot.season_service.get_season_for_server(interaction.guild_id)
        if season is None:
            await interaction.followup.send("\u274c No season found for this server.", ephemeral=True)
            return
        try:
            await season_points_service.attach_config(
                self.bot.db_path, season.id, name, season.status
            )
        except SeasonNotInSetupError:
            await interaction.followup.send(
                "\u274c Config attachment is only allowed for seasons in SETUP.", ephemeral=True
            )
            return
        await interaction.followup.send(
            f"\u2705 Config **{name}** attached to the current season.", ephemeral=True
        )

    @config_group.command(name="detach", description="Detach a config from the current season in SETUP.")
    @app_commands.describe(name="Config name to detach")
    @channel_guard
    @admin_only
    async def config_detach(self, interaction: discord.Interaction, name: str) -> None:
        if not await self._module_gate(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        season = await self.bot.season_service.get_season_for_server(interaction.guild_id)
        if season is None:
            await interaction.followup.send("\u274c No season found for this server.", ephemeral=True)
            return
        try:
            await season_points_service.detach_config(
                self.bot.db_path, season.id, name, season.status
            )
        except SeasonNotInSetupError:
            await interaction.followup.send(
                "\u274c Config detachment is only allowed for seasons in SETUP.", ephemeral=True
            )
            return
        except ConfigNotAttachedError:
            await interaction.followup.send(
                f"\u2139\ufe0f Config **{name}** is not attached to this season.", ephemeral=True
            )
            return
        await interaction.followup.send(
            f"\u2705 Config **{name}** detached from the current season.", ephemeral=True
        )

    # ------------------------------------------------------------------
    # /results config view
    # ------------------------------------------------------------------

    @config_group.command(name="view", description="View a points config for the current season.")
    @app_commands.describe(
        name="Config name",
        session="Optional: filter to a specific session type",
    )
    @app_commands.choices(session=_SESSION_CHOICES)
    @channel_guard
    @admin_only
    async def config_view(
        self,
        interaction: discord.Interaction,
        name: str,
        session: app_commands.Choice[str] | None = None,
    ) -> None:
        if not await self._module_gate(interaction):
            return
        await interaction.response.defer(ephemeral=True)

        season = await self.bot.season_service.get_season_for_server(interaction.guild_id)
        if season is None:
            await interaction.followup.send(
                "\u274c No active or setup season found.", ephemeral=True
            )
            return

        session_type_filter: SessionType | None = (
            SessionType(session.value) if session is not None else None
        )

        _LABEL_MAP = {
            "SPRINT_QUALIFYING": "Sprint Qualifying",
            "SPRINT_RACE": "Sprint Race",
            "FEATURE_QUALIFYING": "Feature Qualifying",
            "FEATURE_RACE": "Feature Race",
        }

        entries_by_session: dict[str, list[tuple[str, int]]] = {}
        fl_by_session: dict[str, tuple[int, int | None]] = {}

        if season.status == "ACTIVE":
            view_data = await season_points_service.get_season_points_view(
                self.bot.db_path, season.id, name, session_type_filter
            )
            if not view_data:
                await interaction.followup.send(
                    f"\u274c Config **{name}** not found in the current season.",
                    ephemeral=True,
                )
                return
            for st_key, data in view_data.items():
                label = _LABEL_MAP.get(st_key, st_key)
                entries_by_session[label] = data["entries"]
                if data["fl"] is not None:
                    fl_by_session[label] = data["fl"]
        else:
            # SETUP — read from server-level config store
            try:
                raw_entries, raw_fl = await points_config_service.get_config_entries(
                    self.bot.db_path, interaction.guild_id, name
                )
            except ConfigNotFoundError:
                await interaction.followup.send(
                    f"\u274c Config **{name}** not found.", ephemeral=True
                )
                return

            if session_type_filter is not None:
                raw_entries = [e for e in raw_entries if e.session_type == session_type_filter]
                raw_fl = [f for f in raw_fl if f.session_type == session_type_filter]

            from utils.results_formatter import _collapse_trailing_zeros

            for st in SessionType:
                session_entries = sorted(
                    [e for e in raw_entries if e.session_type == st],
                    key=lambda e: e.position,
                )
                if not session_entries:
                    continue
                label = _LABEL_MAP.get(st.value, st.value)
                entries_by_session[label] = _collapse_trailing_zeros(
                    [(e.position, e.points) for e in session_entries]
                )
            for fl_entry in raw_fl:
                label = _LABEL_MAP.get(fl_entry.session_type.value, fl_entry.session_type.value)
                fl_by_session[label] = (fl_entry.fl_points, fl_entry.fl_position_limit)

        from utils import results_formatter

        formatted = results_formatter.format_config_view(name, entries_by_session, fl_by_session)
        await interaction.followup.send(formatted, ephemeral=True)

    # ------------------------------------------------------------------
    # /results amend group — T025
    # ------------------------------------------------------------------

    amend_group = app_commands.Group(
        name="amend", description="Mid-season points amendment management", parent=results_group
    )

    @amend_group.command(name="toggle", description="Enable or disable amendment mode for the current season.")
    @channel_guard
    @admin_only
    async def amend_toggle(self, interaction: discord.Interaction) -> None:
        if not await self._module_gate(interaction):
            return
        await interaction.response.defer(ephemeral=True)

        from services.amendment_service import (
            AmendmentModifiedError,
            disable_amendment_mode,
            enable_amendment_mode,
            get_amendment_state,
        )

        season = await self.bot.season_service.get_season_for_server(interaction.guild_id)
        if season is None:
            await interaction.followup.send("\u274c No active season.", ephemeral=True)
            return

        state = await get_amendment_state(self.bot.db_path, season.id)
        currently_on = state is not None and state.amendment_active

        if not currently_on:
            await enable_amendment_mode(self.bot.db_path, season.id)
            await interaction.followup.send(
                "\u2705 Amendment mode enabled. Modification store initialised.", ephemeral=True
            )
        else:
            try:
                await disable_amendment_mode(self.bot.db_path, season.id)
                await interaction.followup.send("\u2705 Amendment mode disabled.", ephemeral=True)
            except AmendmentModifiedError:
                await interaction.followup.send(
                    "\u274c Cannot disable amendment mode \u2014 uncommitted changes exist. "
                    "Use `/results amend revert` to discard or `/results amend review` to apply.",
                    ephemeral=True,
                )

    @amend_group.command(name="revert", description="Revert all modification store changes to the season points.")
    @channel_guard
    @admin_only
    async def amend_revert(self, interaction: discord.Interaction) -> None:
        if not await self._module_gate(interaction):
            return
        await interaction.response.defer(ephemeral=True)

        from services.amendment_service import (
            AmendmentNotActiveError,
            get_amendment_state,
            revert_modification_store,
        )

        season = await self.bot.season_service.get_season_for_server(interaction.guild_id)
        if season is None:
            await interaction.followup.send("\u274c No active season.", ephemeral=True)
            return

        state = await get_amendment_state(self.bot.db_path, season.id)
        if state is None or not state.amendment_active:
            await interaction.followup.send("\u274c Amendment mode is not active.", ephemeral=True)
            return

        await revert_modification_store(self.bot.db_path, season.id)
        await interaction.followup.send(
            "\u2705 Modification store reverted to current season points.", ephemeral=True
        )

    @amend_group.command(name="session", description="Set points in the modification store for a session position.")
    @app_commands.describe(
        name="Config name",
        session="Session type",
        position="Finishing position",
        points="Points to award",
    )
    @app_commands.choices(session=_SESSION_CHOICES)
    @channel_guard
    @admin_only
    async def amend_session(
        self,
        interaction: discord.Interaction,
        name: str,
        session: app_commands.Choice[str],
        position: int,
        points: int,
    ) -> None:
        if not await self._module_gate(interaction):
            return
        await interaction.response.defer(ephemeral=True)

        from services.amendment_service import AmendmentNotActiveError, modify_session_points

        season = await self.bot.season_service.get_season_for_server(interaction.guild_id)
        if season is None:
            await interaction.followup.send("\u274c No active season.", ephemeral=True)
            return

        try:
            await modify_session_points(
                self.bot.db_path, season.id, name, session.value, position, points
            )
        except AmendmentNotActiveError:
            await interaction.followup.send("\u274c Amendment mode is not active.", ephemeral=True)
            return
        await interaction.followup.send(
            f"\u2705 Updated in modification store: **{name}** {session.name} P{position} \u2192 {points} pts.",
            ephemeral=True,
        )

    @amend_group.command(name="fl", description="Set fastest-lap bonus in the modification store.")
    @app_commands.describe(
        name="Config name",
        session="Race session type",
        points="FL bonus points",
    )
    @app_commands.choices(session=_RACE_SESSION_CHOICES)
    @channel_guard
    @admin_only
    async def amend_fl(
        self,
        interaction: discord.Interaction,
        name: str,
        session: app_commands.Choice[str],
        points: int,
    ) -> None:
        if not await self._module_gate(interaction):
            return
        await interaction.response.defer(ephemeral=True)

        from services.amendment_service import AmendmentNotActiveError, modify_fl_bonus

        season = await self.bot.season_service.get_season_for_server(interaction.guild_id)
        if season is None:
            await interaction.followup.send("\u274c No active season.", ephemeral=True)
            return

        try:
            await modify_fl_bonus(self.bot.db_path, season.id, name, session.value, points)
        except AmendmentNotActiveError:
            await interaction.followup.send("\u274c Amendment mode is not active.", ephemeral=True)
            return
        await interaction.followup.send(
            f"\u2705 Updated in modification store: **{name}** {session.name} FL bonus \u2192 {points} pts.",
            ephemeral=True,
        )

    @amend_group.command(name="fl-plimit", description="Set fastest-lap position limit in the modification store.")
    @app_commands.describe(
        name="Config name",
        session="Race session type",
        limit="Highest eligible position",
    )
    @app_commands.choices(session=_RACE_SESSION_CHOICES)
    @channel_guard
    @admin_only
    async def amend_fl_plimit(
        self,
        interaction: discord.Interaction,
        name: str,
        session: app_commands.Choice[str],
        limit: int,
    ) -> None:
        if not await self._module_gate(interaction):
            return
        await interaction.response.defer(ephemeral=True)

        from services.amendment_service import AmendmentNotActiveError, modify_fl_position_limit

        season = await self.bot.season_service.get_season_for_server(interaction.guild_id)
        if season is None:
            await interaction.followup.send("\u274c No active season.", ephemeral=True)
            return

        try:
            await modify_fl_position_limit(self.bot.db_path, season.id, name, session.value, limit)
        except AmendmentNotActiveError:
            await interaction.followup.send("\u274c Amendment mode is not active.", ephemeral=True)
            return
        await interaction.followup.send(
            f"\u2705 Updated in modification store: **{name}** {session.name} FL position limit \u2192 top {limit}.",
            ephemeral=True,
        )

    @amend_group.command(name="review", description="Review modification store changes and approve or reject.")
    @channel_guard
    @admin_only
    async def amend_review(self, interaction: discord.Interaction) -> None:
        if not await self._module_gate(interaction):
            return
        await interaction.response.defer(ephemeral=True)

        from services.amendment_service import (
            approve_amendment,
            get_amendment_state,
            get_modification_store_diff,
        )

        season = await self.bot.season_service.get_season_for_server(interaction.guild_id)
        if season is None:
            await interaction.followup.send("\u274c No active season.", ephemeral=True)
            return

        state = await get_amendment_state(self.bot.db_path, season.id)
        if state is None or not state.amendment_active:
            await interaction.followup.send("\u274c Amendment mode is not active.", ephemeral=True)
            return

        diff = await get_modification_store_diff(self.bot.db_path, season.id)

        class _ReviewView(discord.ui.View):
            def __init__(self_v) -> None:
                super().__init__(timeout=None)
                self_v.approved = False
                self_v.rejected = False

            @discord.ui.button(label="\u2705 Approve", style=discord.ButtonStyle.success)
            async def approve(
                self_v, btn_inter: discord.Interaction, _: discord.ui.Button
            ) -> None:
                self_v.approved = True
                self_v.stop()
                await btn_inter.response.defer()

            @discord.ui.button(label="\u274c Reject", style=discord.ButtonStyle.danger)
            async def reject(
                self_v, btn_inter: discord.Interaction, _: discord.ui.Button
            ) -> None:
                self_v.rejected = True
                self_v.stop()
                await btn_inter.response.defer()

        view = _ReviewView()
        await interaction.followup.send(
            f"{diff}\n\nApprove or reject these changes?", view=view, ephemeral=True
        )
        await view.wait()

        if view.rejected:
            await interaction.followup.send(
                "\u2139\ufe0f Amendment rejected. Modification store and amendment mode remain active.",
                ephemeral=True,
            )
            return

        if view.approved:
            await approve_amendment(
                self.bot.db_path, season.id, interaction.user.id, interaction.client
            )
            await interaction.followup.send(
                "\u2705 Amendment approved. All standings recomputed and reposted.", ephemeral=True
            )

    # ------------------------------------------------------------------
    # /results reserves group — T026
    # ------------------------------------------------------------------

    reserves_group = app_commands.Group(
        name="reserves", description="Reserve driver visibility in standings", parent=results_group
    )

    @reserves_group.command(name="toggle", description="Toggle reserve driver visibility in division standings.")
    @app_commands.describe(division="Division name")
    @channel_guard
    @admin_only
    async def reserves_toggle(self, interaction: discord.Interaction, division: str) -> None:
        if not await self._module_gate(interaction):
            return
        await interaction.response.defer(ephemeral=True)

        season = await self.bot.season_service.get_season_for_server(interaction.guild_id)
        if season is None:
            await interaction.followup.send("\u274c No active season.", ephemeral=True)
            return

        divisions = await self.bot.season_service.get_divisions(season.id)
        div = next((d for d in divisions if d.name.lower() == division.lower()), None)
        if div is None:
            await interaction.followup.send(f"\u274c Division '{division}' not found.", ephemeral=True)
            return

        from db.database import get_connection
        async with get_connection(self.bot.db_path) as db:
            cursor = await db.execute(
                "SELECT reserves_in_standings FROM division_results_config WHERE division_id = ?",
                (div.id,),
            )
            row = await cursor.fetchone()

        current = (
            bool(row["reserves_in_standings"])
            if row and row["reserves_in_standings"] is not None
            else True
        )
        new_value = not current

        async with get_connection(self.bot.db_path) as db:
            await db.execute(
                """
                INSERT INTO division_results_config (division_id, reserves_in_standings)
                VALUES (?, ?)
                ON CONFLICT (division_id) DO UPDATE
                    SET reserves_in_standings = excluded.reserves_in_standings
                """,
                (div.id, 1 if new_value else 0),
            )
            await db.commit()

        state_str = "visible" if new_value else "hidden"
        await interaction.followup.send(
            f"\u2705 Reserve visibility for **{division}** set to **{state_str}**.", ephemeral=True
        )

    # ------------------------------------------------------------------
    # /results standings group — T013 (US3)
    # ------------------------------------------------------------------

    standings_group = app_commands.Group(
        name="standings", description="Standings commands", parent=results_group
    )

    @standings_group.command(name="sync", description="Force a standings repost for a division.")
    @app_commands.describe(division="Division name")
    @channel_guard
    @admin_only
    async def standings_sync(self, interaction: discord.Interaction, division: str) -> None:
        if not await self._module_gate(interaction):
            return
        await interaction.response.defer(ephemeral=True)

        season = await self.bot.season_service.get_season_for_server(interaction.guild_id)
        if season is None:
            await interaction.followup.send("\u274c No active season.", ephemeral=True)
            return

        divisions = await self.bot.season_service.get_divisions(season.id)
        div = next((d for d in divisions if d.name.lower() == division.lower()), None)
        if div is None:
            await interaction.followup.send(f"\u274c Division '{division}' not found.", ephemeral=True)
            return

        from services.results_post_service import repost_standings_for_division
        status = await repost_standings_for_division(self.bot.db_path, div.id, interaction.guild)

        if status == "ok":
            await interaction.followup.send(
                f"\u2705 Standings for **{division}** synced to the standings channel.",
                ephemeral=True,
            )
        elif status == "no_rounds":
            await interaction.followup.send(
                f"\u2139\ufe0f No completed rounds found for **{division}**. No standings to post.",
                ephemeral=True,
            )
        else:  # no_channel
            await interaction.followup.send(
                f"\u274c Division '{division}' has no standings channel configured.",
                ephemeral=True,
            )
