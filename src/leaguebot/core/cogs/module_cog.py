"""ModuleCog — /module enable and /module disable commands.

Manages the league's modules.
"""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from leaguebot.core.models.change import module_off, module_on
from leaguebot.core.models.module_dependencies import needs, taken_down_with
from leaguebot.core.utils.channel_guard import league_admin_only
from leaguebot.core.utils.league_bot import LeagueBot
from leaguebot.core.utils.interaction_errors import describe
from leaguebot.core.utils.league_server import LeagueView
from leaguebot.core.utils.log_lines import record_abandoned, refuse


_MODULE_CHOICES = [
    app_commands.Choice(name="weather", value="weather"),
    app_commands.Choice(name="signup", value="signup"),
    app_commands.Choice(name="results", value="results"),
    app_commands.Choice(name="attendance", value="attendance"),
    app_commands.Choice(name="images", value="images"),
]

#: Every module `/module` offers, as `/module` and `ModuleService`'s getters name them
#: (`is_<name>_enabled`) — the same names the dependency table's entries use, except images,
#: which the table calls "image" (`module_dependencies.py`'s own docstring). Read generally so a
#: second table entry (stewarding's) needs no change here.
_MODULE_NAMES = tuple(choice.value for choice in _MODULE_CHOICES)

#: How each module reads in a refusal, keyed as `_MODULE_NAMES` is.
_MODULE_LABEL = {
    "weather": "Weather module",
    "signup": "Signup module",
    "results": "Results & Standings module",
    "attendance": "Attendance module",
    "images": "Image module",
}

#: How the change that turns results off names itself in its lines.
_RESULTS_OFF = "`/module disable results`"


# ---------------------------------------------------------------------------
# Confirmation for the results → attendance cascade
# ---------------------------------------------------------------------------


def _results_disable_warning(*, season_active: bool, attendance: bool) -> str:
    """The warning shown before results & standings is switched off.

    Built from what is actually at stake rather than fixed, because the two costs are
    independent: a running season loses its results, attendance goes wherever it is on, and a
    league can face either, both, or — between seasons with attendance off — neither.
    """
    lines: list[str] = []

    if season_active:
        lines.append(
            "⚠️ **Disabling Results & Standings destroys this season's results.**\n"
            "The season is running, and switching the module off does not pause it. "
            "If you continue:\n"
            "• every classification recorded this season is deleted, and every standing "
            "computed from them;\n"
            "• every results and standings message already posted is removed from its "
            "channel;\n"
            "• every penalty and appeal verdict already announced is removed from the "
            "verdicts channel, with the banner heading it;\n"
            "• every round still waiting on results, report verdicts or appeal verdicts is "
            "closed as final with no results;\n"
            "• no further results are collected for the rest of the season.\n"
            "**None of this can be undone**, and the module cannot be switched back on until "
            "the season ends.\n"
            "Your points configurations, the season's copy of them, and every division's "
            "channels are kept, and so are any auto-sack and auto-reserve announcements in "
            "the verdicts channel."
        )

    if attendance:
        lines.append(
            "⚠️ **Attendance goes with it.** Attendance depends on Results & Standings and "
            "cannot run alone. If you continue:\n"
            "• every check-in call, reminder and deadline still to come will stop;\n"
            "• every division's check-in and attendance channels will be cleared, and you "
            "will have to set them again;\n"
            "• Attendance cannot be switched back on once a season's placements are confirmed.\n"
            "Timings, penalties and thresholds are kept either way."
        )

    return "\n\n".join(lines)


class _ConfirmDisableResultsView(LeagueView):
    """Confirm disabling results & standings before anything is written.

    Shown wherever the command costs the league something it cannot get back: a running
    season, whose results the disable destroys, or an enabled attendance module, which the disable
    takes with it. With neither at stake — between seasons, with attendance already off — the
    command disables results straight away.
    """

    def __init__(
        self,
        cog: "ModuleCog",
        actor_id: int,
        *,
        cascade_attendance: bool = True,
    ) -> None:
        super().__init__(timeout=120)
        self._cog = cog
        self._actor_id = actor_id
        self._cascade_attendance = cascade_attendance
        self.confirm.label = (
            "✅ Disable both" if cascade_attendance else "✅ Disable and delete the results"
        )

    @discord.ui.button(label="✅ Disable both", style=discord.ButtonStyle.danger)
    async def confirm(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if interaction.user.id != self._actor_id:
            await refuse(interaction, "⛔ Not your action.", what=describe(interaction, button))
            return
        self.stop()
        await self._cog.bot.change_queue.ask(
            module_off("results"),
            {"cascade_attendance": self._cascade_attendance},
            interaction=interaction,
            what=_RESULTS_OFF,
            refusal_what=describe(interaction, button),
        )

    @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if interaction.user.id != self._actor_id:
            await refuse(interaction, "⛔ Not your action.", what=describe(interaction, button))
            return
        self.stop()
        await interaction.response.send_message(
            f"Cancelled. {self._standing}",
            ephemeral=True,
        )
        await record_abandoned(
            interaction.client,
            interaction.user,
            what="`/module disable`",
            lapsed=False,
            detail=f"{self._standing} Run `/module disable` again to disable results.",
        )

    async def on_timeout(self) -> None:
        """Record that the confirmation lapsed unanswered, naming who started it.

        Nothing was changed, so the line says what stands and to run the command again.
        """
        await record_abandoned(
            self._cog.bot,
            self._actor_id,
            what="`/module disable`",
            lapsed=True,
            detail=f"{self._standing} Run `/module disable` again to disable results.",
        )

    @property
    def _standing(self) -> str:
        """What stands once the confirmation is not given."""
        return (
            "Both modules remain enabled."
            if self._cascade_attendance
            else "Results & Standings remains enabled and nothing was deleted."
        )


# ---------------------------------------------------------------------------
# ModuleCog
# ---------------------------------------------------------------------------


class ModuleCog(commands.Cog):
    def __init__(self, bot: LeagueBot) -> None:
        self.bot = bot

    module = app_commands.Group(
        name="module",
        description="Enable or disable bot modules for this server.",
        default_permissions=None,
    )

    # ── /module enable ─────────────────────────────────────────────────

    @module.command(
        name="enable",
        description="Enable a bot module for this server.",
    )
    @app_commands.describe(
        module_name="Module to enable",
    )
    @app_commands.choices(module_name=_MODULE_CHOICES)
    @league_admin_only
    async def enable(
        self,
        interaction: discord.Interaction,
        module_name: app_commands.Choice[str],
    ) -> None:

        if await self._refuse_module_change(interaction, module_name.value, "enable"):
            return

        if module_name.value == "weather":
            await self._enable_weather(interaction)
        elif module_name.value == "results":
            await self._enable_results(interaction)
        elif module_name.value == "attendance":
            await self._enable_attendance(interaction)
        elif module_name.value == "images":
            await self._enable_images(interaction)
        else:
            await self._enable_signup(interaction)

    # ── /module disable ────────────────────────────────────────────────

    @module.command(
        name="disable",
        description="Disable a bot module for this server.",
    )
    @app_commands.describe(module_name="Module to disable")
    @app_commands.choices(module_name=_MODULE_CHOICES)
    @league_admin_only
    async def disable(
        self,
        interaction: discord.Interaction,
        module_name: app_commands.Choice[str],
    ) -> None:
        if await self._refuse_module_change(interaction, module_name.value, "disable"):
            return

        if module_name.value == "weather":
            await self._disable_weather(interaction)
        elif module_name.value == "results":
            await self._disable_results(interaction)
        elif module_name.value == "attendance":
            await self._disable_attendance(interaction)
        elif module_name.value == "images":
            await self._disable_images(interaction)
        else:
            await self._disable_signup(interaction)

    async def _refuse_module_change(
        self,
        interaction: discord.Interaction,
        module: str,
        action: str,
        *,
        record: bool = True,
    ) -> bool:
        """Refuse enabling or disabling a module where the season's stage forbids it.

        In the order a league meets the rules:

        - the signup module is enabled and disabled only with no active season, or while its
          season is in Configuration;
        - no other module is enabled once the season's placements have been confirmed;
        - no module is disabled while the season is in Pending completion.

        Checked here, before the module's own handler, so every module answers alike. Returns
        True where the command was refused, having answered the interaction and, unless
        *record* is False, recorded the refusal in the log channel.
        """
        from leaguebot.core.services.season_lifecycle_service import (
            FROZEN_FOR_COMPLETION_REFUSAL,
            PLACEMENTS_CONFIRMED_REFUSAL,
            configuration_fixed,
            configuration_fixed_refusal,
            modules_frozen_for_completion,
        )

        async def _refused(reply: str) -> bool:
            if record:
                await refuse(interaction, reply, what=describe(interaction))
            else:
                await interaction.response.send_message(reply, ephemeral=True)
            return True

        if module == "signup":
            season_number = await configuration_fixed(self.bot.db_path)
            if season_number is not None:
                return await _refused(configuration_fixed_refusal(season_number, action))
        elif action == "enable":
            if await self.bot.season_service.get_confirmed_season() is not None:
                return await _refused(PLACEMENTS_CONFIRMED_REFUSAL)

        if action == "disable" and await modules_frozen_for_completion(
            self.bot.db_path
        ):
            return await _refused(FROZEN_FOR_COMPLETION_REFUSAL)
        return False

    async def _enabled_modules(self) -> frozenset[str]:
        """Every module `/module` offers that is switched on, read generally over the table's
        names rather than naming one by its flag, so a second table entry needs no change
        here."""
        enabled = set()
        for module in _MODULE_NAMES:
            is_enabled = getattr(self.bot.module_service, f"is_{module}_enabled")
            if await is_enabled():
                enabled.add(module)
        return frozenset(enabled)

    async def _refuse_missing_dependency(
        self, interaction: discord.Interaction, module: str
    ) -> bool:
        """Refuse enabling *module* while a module it `needs` is off, reading the dependency
        table for every module rather than a rule of *module*'s own, so a second table entry
        needs no change here. Empty for a module with no entry, as `needs` already is."""
        for required in needs(module):
            is_enabled = getattr(self.bot.module_service, f"is_{required}_enabled")
            if not await is_enabled():
                await refuse(
                    interaction,
                    f"❌ The {_MODULE_LABEL[module]} requires the {_MODULE_LABEL[required]} "
                    "to be enabled first.",
                    what=describe(interaction),
                )
                return True
        return False

    # ── Weather enable ──────────────────────────────────────────────────

    async def _enable_weather(
        self, interaction: discord.Interaction
    ) -> None:
        # A season whose placements are confirmed refuses the enable before this handler is
        # reached, so there is never a running season to catch up on: its forecast channels are
        # checked, and its phases armed, when placements are confirmed.
        if await self._refuse_missing_dependency(interaction, "weather"):
            return
        # The switch is weather's own change on the queue: this asks for it and does nothing
        # else.
        await self.bot.change_queue.ask(
            module_on("weather"),
            {},
            interaction=interaction,
            what=describe(interaction),
        )

    # ── Weather disable ─────────────────────────────────────────────────

    async def _disable_weather(
        self, interaction: discord.Interaction
    ) -> None:
        # The switch-off is weather's own change on the queue: it lowers the flag
        # and writes its audit entry in one save, then removes the season's weather jobs in a
        # step of its own. This asks for it and does nothing else.
        await self.bot.change_queue.ask(
            module_off("weather"),
            {},
            interaction=interaction,
            what=describe(interaction),
        )

    # ── Results & Standings enable ─────────────────────────────────────

    async def _enable_results(
        self, interaction: discord.Interaction
    ) -> None:
        if await self._refuse_missing_dependency(interaction, "results"):
            return

        # Block if ACTIVE season exists (FR-003)
        active_season = await self.bot.season_service.get_confirmed_season()
        if active_season is not None:
            await refuse(
                interaction,
                "❌ Results & Standings module cannot be enabled once a season's placements are confirmed.",
                what=describe(interaction),
            )
            return

        # The switch is results' own change on the queue, whose check refuses "already enabled":
        # this asks for it and does nothing else.
        await self.bot.change_queue.ask(
            module_on("results"),
            {},
            interaction=interaction,
            what=describe(interaction),
        )

    # ── Results & Standings disable ────────────────────────────────────

    async def _disable_results(self, interaction: discord.Interaction) -> None:
        """Disable results & standings, warning first wherever it costs the league something.

        The warning names the cascade: attendance goes with results, its check-in and
        attendance channels are cleared, and it cannot come back while the season runs.

        A running season is owed the confirmation in its own right, whatever attendance is
        doing: disabling mid-season destroys that season's results entire and closes every
        round still waiting on them.
        """
        if not await self.bot.module_service.is_results_enabled():
            await refuse(
                interaction, "⚠️ Results & Standings module is already disabled.", what=describe(interaction)
            )
            return

        # Warn before anything irreversible, and write nothing until the league confirms it.
        active_season = await self.bot.season_service.get_confirmed_season()
        enabled = await self._enabled_modules()
        cascade_attendance = "attendance" in taken_down_with("results", enabled)

        if active_season is not None or cascade_attendance:
            await interaction.response.send_message(
                _results_disable_warning(
                    season_active=active_season is not None, attendance=cascade_attendance
                ),
                view=_ConfirmDisableResultsView(
                    self,
                    interaction.user.id,
                    cascade_attendance=cascade_attendance,
                ),
                ephemeral=True,
            )
            return

        await self.bot.change_queue.ask(
            module_off("results"),
            {"cascade_attendance": False},
            interaction=interaction,
            what=_RESULTS_OFF,
            refusal_what=describe(interaction),
        )

    # ── Attendance enable ──────────────────────────────────────────────

    async def _enable_attendance(
        self, interaction: discord.Interaction
    ) -> None:
        # Guard: the dependency table's needs must be enabled first
        if await self._refuse_missing_dependency(interaction, "attendance"):
            return

        # Guard: no ACTIVE season
        active_season = await self.bot.season_service.get_confirmed_season()
        if active_season is not None:
            await refuse(
                interaction,
                "❌ Attendance module cannot be enabled once a season's placements are confirmed.",
                what=describe(interaction),
            )
            return

        # The switch is attendance's own change on the queue, whose check refuses "already
        # enabled": this asks for it and does nothing else.
        await self.bot.change_queue.ask(
            module_on("attendance"),
            {},
            interaction=interaction,
            what=describe(interaction),
        )

    # ── Images enable ────────────────────────────────────────────────

    async def _enable_images(
        self, interaction: discord.Interaction
    ) -> None:
        if await self._refuse_missing_dependency(interaction, "images"):
            return

        # The switch is image's own change on the queue: this asks for it and does nothing
        # else.
        await self.bot.change_queue.ask(
            module_on("images"),
            {},
            interaction=interaction,
            what=describe(interaction),
        )

    # ── Images disable ──────────────────────────────────────────────────

    async def _disable_images(
        self, interaction: discord.Interaction
    ) -> None:
        # The switch-off is image's own change on the queue: it lowers the flag and writes
        # its audit entry in one save, deleting nothing and removing nothing (Principle X.6's
        # exception, which image claims). This asks for it and does nothing else.
        await self.bot.change_queue.ask(
            module_off("images"),
            {},
            interaction=interaction,
            what=describe(interaction),
        )

    # ── Attendance disable ─────────────────────────────────────────────

    async def _disable_attendance(self, interaction: discord.Interaction) -> None:
        # The switch-off is attendance's own change on the queue, whose check refuses "already
        # disabled": it calls `AttendanceService.switch_off_on` and writes its audit entry in
        # one save. This asks for it and does nothing else.
        await self.bot.change_queue.ask(
            module_off("attendance"),
            {},
            interaction=interaction,
            what=describe(interaction),
        )

    # ── Signup enable ───────────────────────────────────────────────────

    async def _enable_signup(
        self,
        interaction: discord.Interaction,
    ) -> None:
        if await self._refuse_missing_dependency(interaction, "signup"):
            return

        # The switch is signup's own change on the queue, whose check refuses "already enabled"
        # and the season's stage being fixed: this asks for it and does nothing else.
        await self.bot.change_queue.ask(
            module_on("signup"),
            {},
            interaction=interaction,
            what=describe(interaction),
        )

    # ── Signup disable ──────────────────────────────────────────────────

    async def _disable_signup(
        self, interaction: discord.Interaction
    ) -> None:
        # The switch-off is signup's own change on the queue. It fills its own payload at the ask
        # with the window standing then, and its check refuses "already disabled", a season whose
        # configuration is fixed and a window changed since the ask. Where the window is open it
        # closes it first, as jobs; its switch-off turns the module off, forgets the channel and
        # audits it, then clears the channel's overwrites in a job of its own, so that they go only
        # with the module. This asks for it and does nothing else.
        await self.bot.change_queue.ask(
            module_off("signup"),
            {},
            interaction=interaction,
            what=describe(interaction),
        )
