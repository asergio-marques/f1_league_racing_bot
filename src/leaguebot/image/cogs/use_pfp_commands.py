"""`UsePfpCommands`: the `/images use-pfp` subgroup, and the form and confirmation it opens.

`/images use-pfp daily-toggle` opens `PortraitTimeModal`, which names the time of day, and its
answer is confirmed by `PortraitTimeConfirm`; both are here with it.
"""
from __future__ import annotations

import logging

import discord

from leaguebot.core.utils.channel_guard import league_manager_only
from leaguebot.framework.utils.interaction_errors import (
    describe as describe_command,
    describe_form,
)
from leaguebot.framework.utils.league_server import LeagueModal, LeagueView
from leaguebot.framework.utils.log_lines import record_abandoned, refuse
from leaguebot.utils.time_parsing import parse_time_of_day
from leaguebot.image.cogs.image_group import USE_PFP_GROUP, ImageSubgroup
from leaguebot.image.services.config.image_settings import (
    DAILY_PORTRAITS,
    DAILY_TOGGLE,
    PORTRAITS,
    daily_portraits_refusal,
)

log = logging.getLogger(__name__)


class PortraitTimeModal(LeagueModal, title="Daily portrait updates"):
    """Names the time of day the daily driver-portrait refresh runs.

    A modal must be the *initial* response to an interaction, so the command that opens this
    cannot defer first. Submitting it does not commit anything: per the specification the
    setting is committed only on a confirmation given after the modal, which
    `PortraitTimeConfirm` collects.
    """

    time_of_day: discord.ui.TextInput = discord.ui.TextInput(
        label="Time of day in UTC",
        placeholder="03:00 — also accepts 3am, 3:30 pm, 1530",
        required=True,
        max_length=16,
    )

    def __init__(self, cog: ImageSubgroup, current: str) -> None:
        super().__init__()
        self._cog = cog
        # Prefilled with what is stored, so re-running the command shows the standing value
        # rather than making a manager remember it.
        self.time_of_day.default = current

    async def on_submit(self, interaction: discord.Interaction) -> None:
        what = describe_form(self)
        normalised = parse_time_of_day(str(self.time_of_day.value))
        if normalised is None:
            await refuse(
                interaction,
                f"❌ Could not read `{self.time_of_day.value}` as a time of day. "
                f"Try `03:00`, `3am` or `1530`. Nothing was changed.",
                what=what,
            )
            return

        # The form may have stood open while another manager changed the module or the setting,
        # and a confirmation offered for a change that can no longer be made would only be
        # refused at the end of it.
        stale = await daily_portraits_refusal(
            modules=self._cog.bot.module_service, config=self._cog._config_service, form=True
        )
        if stale is not None:
            await refuse(interaction, stale, what=what)
            return

        await interaction.response.send_message(
            f"Daily driver-portrait updates will run at **{normalised} UTC**.\n"
            f"The bot runs on UTC, so this is not your local time unless you are on it.\n"
            f"Confirm to enable them.",
            view=PortraitTimeConfirm(self._cog, normalised, interaction),
            ephemeral=True,
        )


class PortraitTimeConfirm(LeagueView):
    """The confirmation the specification requires after the time-of-day modal.

    Not persistent, and deliberately: it is a step within one command rather than a control
    a league comes back to. Every ending is recorded in the log channel: a Confirm as the
    command's success, its refusal or its failure; a Cancel as cancelled; and an unanswered
    one, which changes nothing, as lapsed, naming the member who opened it. Each takes the
    buttons down, so that none can be pressed again, through the interaction alone.

    It answers once: a second press, which can arrive before the first has finished, is
    refused rather than saved again.
    """

    def __init__(
        self, cog: ImageSubgroup, normalised: str, opened: discord.Interaction
    ) -> None:
        super().__init__(timeout=120)
        self._cog = cog
        self._normalised = normalised
        #: The form's submission, whose reply carries these buttons and whose member opened them.
        self._opened = opened
        self._answered = False

    @staticmethod
    async def _take_down(edit) -> None:
        """Take the buttons down with *edit*; a message Discord will not edit is left as it is."""
        try:
            await edit(view=None)
        except discord.HTTPException as exc:
            log.warning("could not take the portrait confirmation's buttons down: %s", exc)

    async def _already_answered(self, interaction: discord.Interaction) -> bool:
        """Refuse a press once the confirmation has been answered; otherwise mark it answered."""
        if self._answered:
            await refuse(
                interaction,
                "ℹ️ This confirmation has already been answered.",
                what=f"`{DAILY_TOGGLE}`",
            )
            return True
        self._answered = True
        return False

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.success)
    async def confirm(
        self, interaction: discord.Interaction, _button: discord.ui.Button
    ) -> None:
        if await self._already_answered(interaction):
            return
        try:
            await self._take_down(interaction.response.edit_message)
            await self._cog.bot.change_queue.ask(
                DAILY_PORTRAITS,
                {"time": self._normalised},
                interaction=interaction,
                what=f"`{DAILY_TOGGLE}`",
            )
        finally:
            self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(
        self, interaction: discord.Interaction, _button: discord.ui.Button
    ) -> None:
        if await self._already_answered(interaction):
            return
        try:
            await record_abandoned(
                self._cog.bot,
                interaction.user,
                what=f"`{DAILY_TOGGLE}`",
                lapsed=False,
                detail=_UNCHANGED_DETAIL,
            )
            await self._take_down(interaction.response.edit_message)
            await self._cog._reply(
                interaction, "❌ Cancelled. Daily driver-portrait updates are unchanged."
            )
        finally:
            self.stop()

    async def on_timeout(self) -> None:
        """Record the lapse, naming who opened the confirmation, and take its buttons down."""
        if self._answered:
            return
        self._answered = True
        await record_abandoned(
            self._cog.bot,
            self._opened.user,
            what=f"`{DAILY_TOGGLE}`",
            lapsed=True,
            detail=_UNCHANGED_DETAIL,
        )
        await self._take_down(self._opened.edit_original_response)


#: What a cancelled or lapsed confirmation leaves, and what to do next, as the log line says.
_UNCHANGED_DETAIL = (
    "Daily driver-portrait updates are unchanged.\n"
    f"Run `{DAILY_TOGGLE}` again to enable them."
)


class UsePfpCommands(ImageSubgroup):
    use_pfp = USE_PFP_GROUP

    # ── Driver portraits obtained from Discord ────────────────────────────

    async def _guard_portraits_enabled(self, interaction: discord.Interaction):
        """The configuration, or None having replied why the command may not proceed.

        The two sub-toggles govern *how* portraits are kept up to date, which is not a
        question while the bot is not obtaining them at all.
        """
        if not await self._module_gate(interaction):
            return None
        config = await self._config_service.get_config()
        if config is None or not config.use_pfp:
            await refuse(
                interaction,
                "❌ Driver portraits are not being obtained from Discord. "
                "Use `/images use-pfp toggle` first.",
                what=describe_command(interaction),
            )
            return None
        return config

    @use_pfp.command(
        name="toggle",
        description="Obtain driver portraits from their Discord profile pictures.",
    )
    @league_manager_only
    async def use_pfp_toggle(self, interaction: discord.Interaction) -> None:
        if not await self._module_gate(interaction):
            return

        config = await self._config_service.get_config()
        if config is None:
            await refuse(
                interaction,
                "❌ The image module has no configuration yet.",
                what=describe_command(interaction),
            )
            return

        # The flip is resolved here, as the member saw the state; the daily job follows the
        # save as a job of its own (`image_settings`), so a job that cannot be scheduled is a
        # failure with the setting stored, and not a success followed by an apology.
        await self.bot.change_queue.ask(
            PORTRAITS,
            {"column": "use_pfp", "enabled": not config.use_pfp},
            interaction=interaction,
            what=describe_command(interaction),
        )

    @use_pfp.command(
        name="prerender-toggle",
        description="Update the portraits a graphic needs just before drawing it.",
    )
    @league_manager_only
    async def use_pfp_prerender_toggle(self, interaction: discord.Interaction) -> None:
        config = await self._guard_portraits_enabled(interaction)
        if config is None:
            return
        await self.bot.change_queue.ask(
            PORTRAITS,
            {"column": "pfp_prerender", "enabled": not config.pfp_prerender},
            interaction=interaction,
            what=describe_command(interaction),
        )

    @use_pfp.command(
        name="daily-toggle",
        description="Update every driver's portrait once a day, at a time you choose.",
    )
    @league_manager_only
    async def use_pfp_daily_toggle(self, interaction: discord.Interaction) -> None:
        config = await self._guard_portraits_enabled(interaction)
        if config is None:
            return

        if config.pfp_daily:
            # Turning it off needs no time and therefore no modal.
            await self.bot.change_queue.ask(
                PORTRAITS,
                {"column": "pfp_daily", "enabled": False},
                interaction=interaction,
                what=describe_command(interaction),
            )
            return

        # A modal must be the initial response, so nothing above this may defer.
        await interaction.response.send_modal(
            PortraitTimeModal(self, config.pfp_daily_time)
        )
