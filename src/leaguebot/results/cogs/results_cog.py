"""ResultsCog — /results config, /results amend, /results reserves, /results standings,
/results rounds and /results channel commands.

`/results rounds amend` sits beside `/results rounds sync`: it sat under core's `/round` until
#462 moved it, and only its name changed.
"""
from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from leaguebot.results.cogs.config_commands import ConfigCommands
from leaguebot.results.cogs.results_group import (
    MODULE_OFF,
    RACE_SESSION_CHOICES,
    RESULTS_GROUP,
    SESSION_CHOICES,
)
from leaguebot.results.models.points_config import SessionType
from leaguebot.results.services.points.amendment_settings import (
    AMEND_FASTEST_LAP,
    AMEND_MODE,
    AMEND_POINTS,
    AMEND_REVERT,
)
from leaguebot.results.services.points.points_settings import RESERVES, reserves_toggle_request
from leaguebot.results.services.points.amendment_mode_service import review_panel
from leaguebot.results.services.points.points_amendment_change import (
    KIND as POINTS_AMENDMENT,
    refuse_while_approval_in_hand,
)
from leaguebot.results.services.points.points_paste import bulk_refusal, parse_bulk_lines
from leaguebot.results.services.module.channel_settings import (
    NO_ACCESS,
    NOT_ENABLED,
    RESULTS_CHANNEL,
    STANDINGS_CHANNEL,
    VERDICTS_CHANNEL,
    verdicts_division_not_found,
)
from leaguebot.core.services.settings.division_settings import NO_SEASON_REFUSAL, division_not_found
from leaguebot.core.utils.channel_guard import league_admin_only, league_manager_only
from leaguebot.core.utils.interaction_errors import describe
from leaguebot.core.utils.league_bot import LeagueBot, bot_of
from leaguebot.core.utils.league_server import (
    CallbackButton,
    CallbackSelect,
    LeagueModal,
    LeagueView,
    guild_of,
    is_foreign_guild,
)
from leaguebot.core.utils.log_lines import record_abandoned, refuse
from leaguebot.core.utils.member_names import interaction_member
from leaguebot.core.utils.season_gate import season_for_command

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Bulk modal classes (T013 / T014)
# ---------------------------------------------------------------------------

#: What a Reject, and the amend review panel's lapse, leave beneath their line in the log channel.
_NOTHING_APPROVED = (
    "Nothing has been approved. The staged changes and amendment mode remain. "
    "Run /results amend review again."
)

#: How long the amend review panel waits for Approve or Reject before it lapses.
_AMEND_REVIEW_LAPSE_SECONDS = 300
#: What the lapsed amend review panel tells the admin who ran it.
_AMEND_REVIEW_LAPSED = (
    "\u231b This review lapsed after five minutes. Nothing has been approved. The staged "
    "changes and amendment mode remain. Run `/results amend review` again."
)

#: What a `/results rounds amend` left at its session picker leaves beneath its line.
_PICKER_ENDED = "Nothing was amended. Run `/results rounds amend` again to amend a round."

#: The kind of change each `/results channel` command asks of the queue.
_CHANNEL_KINDS = {
    "results": RESULTS_CHANNEL,
    "standings": STANDINGS_CHANNEL,
    "verdicts": VERDICTS_CHANNEL,
}


class BulkAmendSessionModal(LeagueModal, title="Bulk Amend Session Points"):
    """Modal for bulk-amending session points in the modification store.

    All or nothing, on the same terms as `BulkConfigSessionModal`: every line is parsed, and
    the module, the season and amendment mode checked, before anything is staged, and the
    paste is staged in one transaction.
    """

    entries: discord.ui.TextInput = discord.ui.TextInput(
        label="position, points — one per line",
        style=discord.TextStyle.paragraph,
        placeholder="1, 25\n2, 18\n3, 15",
        required=True,
        max_length=2000,
    )

    def __init__(
        self,
        config_name: str,
        session: app_commands.Choice,
        db_path: str,
    ) -> None:
        super().__init__()
        self._config_name = config_name
        self._session = session
        self._db_path = db_path

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        bot = bot_of(interaction)
        what = "`/results amend bulk-session`"

        if not await bot.module_service.is_results_enabled():
            await refuse(interaction, MODULE_OFF, what=what)
            return

        # Gated here as well as on the command that opened it (issue #224). A modal can be
        # submitted long after it was shown, and the season can reach Pending completion in
        # between; the change's check judges it again as it starts.
        season = await season_for_command(
            interaction, bot.season_service, "results amend bulk-session"
        )
        if season is None:
            return

        valid, errors, overrides = parse_bulk_lines(self.entries.value)
        if not valid and not errors:
            await refuse(interaction, "\u274c No entries provided.", what=what)
            return
        if errors:
            reply, reason = bulk_refusal(errors)
            await refuse(interaction, reply, what=what, reason=reason)
            return

        # The mode, the configuration and any approval in hand are the change's to judge.
        await bot.change_queue.ask(
            AMEND_POINTS,
            {
                "season_id": season.id,
                "config": self._config_name,
                "session": self._session.value,
                "pairs": [[position, points] for position, points in valid],
                "bulk": True,
                "notes": overrides,
            },
            interaction=interaction,
            what=what,
        )


# ---------------------------------------------------------------------------
# /results rounds amend — what one amendment has opened, and the choice of its sessions
# ---------------------------------------------------------------------------


class _AmendSessionsView(LeagueView):
    """Choose which of a round's sessions an amendment re-enters (#345, decided 2026-09-21).

    Several at once, because a round's reports and appeals are reviewed together: amending the
    sessions one at a time would mean a review, and a division-wide repost, for each.

    Posted ephemerally to the member who ran the command, so nobody else can answer it. Times
    out with the paste, rather than holding the command open for ever.

    *what* is the amendment as the log names it; a refused Continue is recorded against it. The
    command records the Cancel and the lapse, where it reads how the picker ended, and
    *cancelled_by* is who pressed Cancel.
    """

    def __init__(
        self,
        sessions: list[tuple[str, str]],
        what: str = "`/results rounds amend`",
    ) -> None:
        super().__init__(timeout=300)
        self._what = what
        #: The session-type values chosen, in the order the select reports them.
        self.selected: list[str] = []
        self.cancelled = False
        self.cancelled_by: discord.abc.User | None = None
        self._select = CallbackSelect(
            placeholder="Sessions to amend",
            min_values=1,
            max_values=len(sessions),
            options=[discord.SelectOption(label=label, value=value) for value, label in sessions],
            row=0,
            on_choose=self._chosen,
        )
        self.add_item(self._select)
        go = CallbackButton(
            label="Continue",
            style=discord.ButtonStyle.success,
            row=1,
            on_press=self._continue,
        )
        self.add_item(go)
        cancel = CallbackButton(
            label="\u274c Cancel",
            style=discord.ButtonStyle.secondary,
            row=1,
            on_press=self._cancel,
        )
        self.add_item(cancel)

    async def _chosen(self, interaction: discord.Interaction) -> None:
        self.selected = list(self._select.values)
        await interaction.response.defer()

    async def _continue(self, interaction: discord.Interaction) -> None:
        if not self.selected:
            await refuse(
                interaction,
                "Choose at least one session first.",
                what=f"the \u201cContinue\u201d button of {self._what}",
            )
            return
        self.stop()
        await interaction.response.defer()

    async def _cancel(self, interaction: discord.Interaction) -> None:
        self.cancelled = True
        self.cancelled_by = interaction.user
        self.stop()
        await interaction.response.defer()


class ResultsCog(ConfigCommands, commands.Cog):
    results_group = RESULTS_GROUP

    def __init__(self, bot: LeagueBot) -> None:
        self.bot = bot

    # ------------------------------------------------------------------
    # Guard: block messages while a round is in penalty review (T014)
    # ------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or not message.guild:
            return
        if await is_foreign_guild(self.bot, message.guild.id):
            return
        from leaguebot.results.services.review.review_prompts import is_channel_in_penalty_review

        if await is_channel_in_penalty_review(self.bot.db_path, message.channel.id):
            # Deleted and warned by jobs on the round's lane, not at once (#439).
            from leaguebot.results.services.review.review_lock_change import ask_lock

            await ask_lock(self.bot.change_queue, message)
            return
        # The opener's message in an open amendment's channel is a paste too: a turn on the
        # amendment's lane (#439).
        from leaguebot.results.services.round_amendment.amend_turn_change import take_amended_paste

        if await take_amended_paste(self.bot, message, queue=self.bot.change_queue):
            return
        # A league manager's message in a round's open submission is a paste: marked at once and
        # taken as a turn on the round's lane, or turned away while an earlier one waits (#439).
        from leaguebot.results.services.submission.submission_turn_change import take_paste

        await take_paste(
            self.bot, message, queue=self.bot.change_queue,
            config=await self.bot.config_service.get_server_config(),
        )

    # ------------------------------------------------------------------
    # Gate helpers
    # ------------------------------------------------------------------

    async def _module_gate(
        self, interaction: discord.Interaction, *, record: bool = True
    ) -> bool:
        """Whether the results module is on; where it is not, refuse and return False.

        The refusal is recorded in the log channel, as every refusal of a command that acts
        is, unless *record* is False — which the views and lists pass, since they change
        nothing and record nothing.
        """
        if not await self.bot.module_service.is_results_enabled():
            reply = "\u274c The Results & Standings module is not enabled on this server."
            if record:
                await refuse(interaction, reply, what=describe(interaction))
            else:
                await interaction.response.send_message(reply, ephemeral=True)
            return False
        return True

    async def _answer_nothing_changed(
        self,
        interaction: discord.Interaction,
        command: str,
        reply: str,
        *details: str,
    ) -> None:
        """Answer a request that could have been carried out but changes nothing, and record it.

        The reply is *reply* under an information mark, and the log gets one line in the success
        form, ``Name (<@id>) | /<command> | Nothing changed``, with *details* beneath it, as
        "The record of what changed" asks of a no-op (#482). No audit entry is written: the
        caller has changed nothing to audit. A request that could not be carried out is a
        refusal and never comes here.
        """
        await interaction.followup.send(f"\u2139\ufe0f {reply}", ephemeral=True)
        await self.bot.output_router.post_log(
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /{command} | "
            "Nothing changed" + "".join(f"\n  {detail}" for detail in details),
        )

    async def _sync_gate(
        self, interaction: discord.Interaction, div, *, what: str
    ) -> bool:
        """Refuse a sync of a division with an amendment open (#345, decided 2026-09-21).

        An amendment's first stage writes its corrections and recalculates the division,
        publishing nothing until its last stage is approved. A sync reposts the division from
        the same database, so it would publish them unapproved — and leave them published if
        the amendment were then cancelled or lapsed, its revert posting nothing. It waits, as a
        submission of another of the division's rounds does.

        The wait is a refusal and is recorded as one (#482), *what* being the caller's own name
        of the command as the log channel should read it.
        """
        from leaguebot.results.services.round_amendment.amendment_writes import sync_held_by_amendment

        reply = await sync_held_by_amendment(self.bot.db_path, div.id)
        if reply is None:
            return True
        await refuse(interaction, reply, what=what)
        return False

    # ------------------------------------------------------------------
    # /results amend group — T025
    # ------------------------------------------------------------------
    #
    # Every command of this group, and of the reserves, standings and rounds groups below,
    # asks `season_for_command` for its season rather than reading the most recent one
    # (issue #224). Two rules follow from that one call, and both were broken before it:
    #
    #   * A **completed or cancelled** season is an archive and is never changed. These
    #     commands read `get_season_for_server` — the highest season id, whatever its status
    #     — so amendment mode could be switched on against a finished season and its points
    #     rewritten, and the reserves toggle wrote to one outright.
    #   * A season **pending completion** has had every division finish or be cancelled.
    #     Nothing is raced, so a standings repost, a results repost and the reserves display
    #     describe nothing anyone will drive under, and the season's points are settled.
    #     What remains open there is repairing a division's channels, amending the results of
    #     a round already final, and completing the season (decided 2026-09-20).
    #
    # Amendment mode left switched on when a season reaches Pending completion therefore
    # cannot be switched off again. That is accepted: nothing blocks `/season complete` on
    # it, so the season still ends and the modification store is simply never applied.

    amend_group = app_commands.Group(
        name="amend", description="Mid-season points amendment management", parent=results_group
    )

    @amend_group.command(name="toggle", description="Enable or disable amendment mode for the current season.")
    @league_manager_only
    async def amend_toggle(self, interaction: discord.Interaction) -> None:
        if not await self._module_gate(interaction):
            return

        from leaguebot.results.services.points.amendment_mode_service import (
            get_amendment_state,
        )

        season = await season_for_command(
            interaction, self.bot.season_service, "results amend toggle"
        )
        if season is None:
            return

        # The flip is resolved here, from the state the member saw: a second press while the first
        # waits asks the same thing, and is refused as a repeat. The approval in hand, and whether
        # staged changes forbid turning it off, are the change's to judge.
        state = await get_amendment_state(self.bot.db_path, season.id)
        currently_on = state is not None and state.amendment_active
        await self.bot.change_queue.ask(
            AMEND_MODE,
            {"season_id": season.id, "active": not currently_on},
            interaction=interaction,
            what=describe(interaction),
        )

    @amend_group.command(name="revert", description="Revert all modification store changes to the season points.")
    @league_manager_only
    async def amend_revert(self, interaction: discord.Interaction) -> None:
        if not await self._module_gate(interaction):
            return

        season = await season_for_command(
            interaction, self.bot.season_service, "results amend revert"
        )
        if season is None:
            return
        # The mode being on, and the approval in hand, are the change's to judge.
        await self.bot.change_queue.ask(
            AMEND_REVERT,
            {"season_id": season.id},
            interaction=interaction,
            what=describe(interaction),
        )

    @amend_group.command(name="session", description="Set points in the modification store for a session position.")
    @app_commands.describe(
        name="Config name",
        session="Session type",
        position="Finishing position",
        points="Points to award",
    )
    @app_commands.choices(session=SESSION_CHOICES)
    @league_manager_only
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
        season = await season_for_command(
            interaction, self.bot.season_service, "results amend session"
        )
        if season is None:
            return
        # The mode, the configuration and any approval in hand are the change's to judge.
        await self.bot.change_queue.ask(
            AMEND_POINTS,
            {
                "season_id": season.id,
                "config": name,
                "session": session.value,
                "pairs": [[position, points]],
                "bulk": False,
            },
            interaction=interaction,
            what=describe(interaction),
        )

    @amend_group.command(name="fl", description="Set fastest-lap bonus in the modification store.")
    @app_commands.describe(
        name="Config name",
        session="Race session type",
        points="FL bonus points",
    )
    @app_commands.choices(session=RACE_SESSION_CHOICES)
    @league_manager_only
    async def amend_fl(
        self,
        interaction: discord.Interaction,
        name: str,
        session: app_commands.Choice[str],
        points: int,
    ) -> None:
        if not await self._module_gate(interaction):
            return
        season = await season_for_command(
            interaction, self.bot.season_service, "results amend fl"
        )
        if season is None:
            return
        await self.bot.change_queue.ask(
            AMEND_FASTEST_LAP,
            {
                "season_id": season.id,
                "config": name,
                "session": session.value,
                "fl_points": points,
            },
            interaction=interaction,
            what=describe(interaction),
        )

    @amend_group.command(name="fl-plimit", description="Set fastest-lap position limit in the modification store.")
    @app_commands.describe(
        name="Config name",
        session="Race session type",
        limit="Highest eligible position",
    )
    @app_commands.choices(session=RACE_SESSION_CHOICES)
    @league_manager_only
    async def amend_fl_plimit(
        self,
        interaction: discord.Interaction,
        name: str,
        session: app_commands.Choice[str],
        limit: int,
    ) -> None:
        if not await self._module_gate(interaction):
            return
        season = await season_for_command(
            interaction, self.bot.season_service, "results amend fl-plimit"
        )
        if season is None:
            return
        await self.bot.change_queue.ask(
            AMEND_FASTEST_LAP,
            {
                "season_id": season.id,
                "config": name,
                "session": session.value,
                "fl_position_limit": limit,
            },
            interaction=interaction,
            what=describe(interaction),
        )

    @amend_group.command(
        name="bulk-session",
        description="Bulk-update points in the modification store for multiple positions via a modal.",
    )
    @app_commands.describe(name="Config name", session="Session type")
    @app_commands.choices(session=SESSION_CHOICES)
    @league_manager_only
    async def bulk_amend_session(
        self,
        interaction: discord.Interaction,
        name: str,
        session: app_commands.Choice[str],
    ) -> None:
        if not await self._module_gate(interaction):
            return
        # Before the modal, not after: showing one and refusing its submission would have a
        # manager type a screenful of positions to no purpose. The submission asks the change,
        # whose check judges it again.
        season = await season_for_command(
            interaction, self.bot.season_service, "results amend bulk-session"
        )
        if season is None:
            return
        if await refuse_while_approval_in_hand(
            interaction, self.bot.db_path, season, what=describe(interaction)
        ):
            return
        await interaction.response.send_modal(
            BulkAmendSessionModal(name, session, self.bot.db_path)
        )

    @amend_group.command(name="review", description="Review modification store changes and approve or reject.")
    @league_admin_only
    async def amend_review(self, interaction: discord.Interaction) -> None:
        """Review the modification store and approve or reject it.

        **A league admin's**, and the only command of `/results amend` that is. Approval
        overwrites the season's points entire and nothing undoes it; everything else in the
        group writes the modification store, which `/results amend revert` discards.

        The tier is asked of the whole command rather than of the ✅ Approve button alone,
        which is where it naturally belongs. The panel below is sent `ephemeral=True`, so
        only the member who ran the command can see or press it — a button asking a higher
        tier than the command that posted it would show a league manager a button they
        cannot press, with no league admin able to see it either. Splitting the two would
        need the panel made public, on the model of the season-approval question, and that
        is a larger change than this one.
        """
        if not await self._module_gate(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        season = await season_for_command(
            interaction, self.bot.season_service, "results amend review"
        )
        if season is None:
            return

        diff = await review_panel(
            self.bot.db_path, season, bot=self.bot, module_service=self.bot.module_service
        )
        if diff is None:
            await refuse(
                interaction, "\u274c Amendment mode is not active.", what=describe(interaction)
            )
            return

        # The season the panel was drawn for, bound where it is known not to be None.
        staged_for = season

        class _ReviewView(LeagueView):
            def __init__(self_v) -> None:
                super().__init__(timeout=_AMEND_REVIEW_LAPSE_SECONDS)
                self_v.approved = False
                self_v.rejected = False
                # The interaction of the press, answered through from here on: the
                # command's own token lasts fifteen minutes from the command.
                self_v.pressed_by: discord.Interaction | None = None

            async def on_timeout(self_v) -> None:
                """Nobody answered in five minutes: nothing was approved, and that is recorded.

                The buttons come down through the command's own interaction, whose token lasts
                fifteen minutes, well beyond the five; the admin is told nothing was approved
                and one lapse line names who ran the review. A panel that could not be edited
                still records the lapse.
                """
                try:
                    await interaction.edit_original_response(
                        content=_AMEND_REVIEW_LAPSED, view=None
                    )
                except discord.HTTPException as exc:
                    log.warning("could not take down the lapsed amend review panel: %s", exc)
                await record_abandoned(
                    self.bot,
                    interaction.user,
                    what=describe(interaction),
                    lapsed=True,
                    detail=_NOTHING_APPROVED,
                )

            @discord.ui.button(label="\u2705 Approve", style=discord.ButtonStyle.success)
            async def approve(
                self_v, btn_inter: discord.Interaction, _: discord.ui.Button
            ) -> None:
                self_v.approved = True
                self_v.pressed_by = btn_inter
                self_v.stop()
                # The queue's acknowledgement is the press's response, so it is not deferred
                # (#439). Every check, the approval and its replies are the change's.
                await bot_of(btn_inter).change_queue.ask(
                    POINTS_AMENDMENT,
                    {"season_id": staged_for.id, "season_number": staged_for.season_number},
                    interaction=btn_inter,
                    what=describe(interaction),
                )

            @discord.ui.button(label="\u274c Reject", style=discord.ButtonStyle.danger)
            async def reject(
                self_v, btn_inter: discord.Interaction, _: discord.ui.Button
            ) -> None:
                self_v.rejected = True
                self_v.pressed_by = btn_inter
                self_v.stop()
                await btn_inter.response.defer()

        view = _ReviewView()
        await interaction.followup.send(
            f"{diff}\n\nApprove or reject these changes?", view=view, ephemeral=True
        )
        await self.bot.output_router.post_log(
            f"{interaction_member(interaction)} | /results amend review | Review posted"
        )
        await view.wait()

        # Every reply after the press goes through the press's own interaction (its token is
        # fresh), the refusals through `refuse` with the command's name.
        pressed = view.pressed_by or interaction
        what = describe(interaction)

        if view.rejected:
            await pressed.followup.send(
                "\u2139\ufe0f Amendment rejected. Modification store and amendment mode remain active.",
                ephemeral=True,
            )
            await record_abandoned(
                self.bot, pressed.user, what=what, lapsed=False, detail=_NOTHING_APPROVED
            )
            return

    # ------------------------------------------------------------------
    # /results reserves group — T026
    # ------------------------------------------------------------------

    reserves_group = app_commands.Group(
        name="reserves", description="Reserve driver visibility in standings", parent=results_group
    )

    @reserves_group.command(name="toggle", description="Toggle reserve driver visibility in division standings.")
    @app_commands.describe(division="Division name")
    @league_manager_only
    async def reserves_toggle(self, interaction: discord.Interaction, division: str) -> None:
        if not await self._module_gate(interaction):
            return
        season = await season_for_command(
            interaction, self.bot.season_service, "results reserves toggle"
        )
        if season is None:
            return

        divisions = await self.bot.season_service.get_divisions(season.id)
        div = next((d for d in divisions if d.name.lower() == division.lower()), None)
        if div is None:
            await refuse(
                interaction, f"\u274c Division '{division}' not found.", what=describe(interaction)
            )
            return

        await self.bot.change_queue.ask(
            RESERVES,
            await reserves_toggle_request(self.bot.db_path, div.id, division),
            interaction=interaction,
            what=describe(interaction),
        )

    # ------------------------------------------------------------------
    # /results standings group — T013 (US3)
    # ------------------------------------------------------------------

    standings_group = app_commands.Group(
        name="standings", description="Standings commands", parent=results_group
    )

    @standings_group.command(name="sync", description="Force a standings repost for a division.")
    @app_commands.describe(division="Division name")
    @league_manager_only
    async def standings_sync(self, interaction: discord.Interaction, division: str) -> None:
        if not await self._module_gate(interaction):
            return
        await interaction.response.defer(ephemeral=True)

        season = await season_for_command(
            interaction, self.bot.season_service, "results standings sync"
        )
        if season is None:
            return

        divisions = await self.bot.season_service.get_divisions(season.id)
        div = next((d for d in divisions if d.name.lower() == division.lower()), None)
        if div is None:
            await refuse(
                interaction, f"\u274c Division '{division}' not found.", what=describe(interaction)
            )
            return
        if not await self._sync_gate(interaction, div, what=describe(interaction)):
            return

        from leaguebot.results.services.publishing.results_post_service import standings_repost_status
        from leaguebot.results.services.standings.standings_sync_change import STANDINGS_SYNC

        # The channel and the rounds are read here to answer at once, as the command always has;
        # the repost itself is a change on the queue, which checks them again when it runs.
        status = await standings_repost_status(self.bot.db_path, div.id, guild_of(interaction))

        if status == "ok":
            await self.bot.change_queue.ask(
                STANDINGS_SYNC, {"division_id": div.id, "division_name": div.name},
                interaction=interaction, what="`/results standings sync`",
                refusal_what=describe(interaction),
            )
        elif status == "no_rounds":
            await self._answer_nothing_changed(
                interaction,
                "results standings sync",
                f"No completed rounds found for **{division}**. No standings to post.",
                f"division: {div.name}",
                "reason: no completed rounds, so nothing was posted",
            )
        else:  # no_channel
            await refuse(
                interaction,
                f"\u274c Division '{division}' has no standings channel configured.",
                what=describe(interaction),
            )

    # ------------------------------------------------------------------
    # /results rounds group
    # ------------------------------------------------------------------

    rounds_group = app_commands.Group(
        name="rounds", description="Round results commands", parent=results_group
    )

    @rounds_group.command(name="sync", description="Force a results repost for all rounds in a division.")
    @app_commands.describe(division="Division name")
    @league_manager_only
    async def rounds_sync(self, interaction: discord.Interaction, division: str) -> None:
        if not await self._module_gate(interaction):
            return
        await interaction.response.defer(ephemeral=True)

        season = await season_for_command(
            interaction, self.bot.season_service, "results rounds sync"
        )
        if season is None:
            return

        divisions = await self.bot.season_service.get_divisions(season.id)
        div = next((d for d in divisions if d.name.lower() == division.lower()), None)
        if div is None:
            await refuse(
                interaction, f"\u274c Division '{division}' not found.", what=describe(interaction)
            )
            return
        if not await self._sync_gate(interaction, div, what=describe(interaction)):
            return

        from leaguebot.results.services.publishing.results_post_service import division_repost_status
        from leaguebot.results.services.publishing.rounds_sync_change import ROUNDS_SYNC

        # The channel and the rounds are read here to answer at once, as the command always has;
        # the repost itself is a change on the queue, which checks them again when it runs.
        status = await division_repost_status(self.bot.db_path, div.id, guild_of(interaction))

        if status == "ok":
            await self.bot.change_queue.ask(
                ROUNDS_SYNC, {"division_id": div.id, "division_name": div.name},
                interaction=interaction, what="`/results rounds sync`",
                refusal_what=describe(interaction),
            )
        elif status == "no_rounds":
            await self._answer_nothing_changed(
                interaction,
                "results rounds sync",
                f"No completed rounds found for **{division}**. No results to post.",
                f"division: {div.name}",
                "reason: no completed rounds, so nothing was posted",
            )
        else:  # no_channel
            await refuse(
                interaction,
                f"\u274c Division '{division}' has no results channel configured.",
                what=describe(interaction),
            )

    # ------------------------------------------------------------------
    # /results rounds amend
    # ------------------------------------------------------------------

    @rounds_group.command(
        name="amend",
        description="Re-submit results for one session of a completed round.",
    )
    @app_commands.describe(
        division_name="Division name",
        round_number="Round number",
        session="Session to amend (if omitted, bot will ask)",
    )
    @app_commands.choices(session=[
        app_commands.Choice(name="Sprint Qualifying", value="SPRINT_QUALIFYING"),
        app_commands.Choice(name="Sprint Race", value="SPRINT_RACE"),
        app_commands.Choice(name="Feature Qualifying", value="FEATURE_QUALIFYING"),
        app_commands.Choice(name="Feature Race", value="FEATURE_RACE"),
    ])
    @league_admin_only
    async def rounds_amend(
        self,
        interaction: discord.Interaction,
        division_name: str,
        round_number: int,
        session: app_commands.Choice[str] | None = None,
    ) -> None:
        """Amend the results of a round that has reached FINAL.

        **A league admin's**, unlike `/results rounds sync` beside it, and unlike what a
        reading of the name suggests. Once the last corrected paste is in, `results.amend.stage_one`
        supersedes what the league raced: `amend_round_results_on` snapshots the chosen sessions,
        then writes their corrected classifications over them, re-points their verdicts and
        rescores the standings. It sits with the other commands that rewrite what a league is
        built from.

        Adding, amending and importing rounds stay a league manager's; cancelling and
        deleting one are a league admin's for the same reason this is.

        **An amendment that ends before stage one lets the division go** (#345). From the moment
        its record is in, an amendment holds its division — no other amendment, paste, approval
        or sync — and until stage one writes, it carries no deadline for the sweep to act on.
        Nothing has been written by then, so no ending has anything to put back: each drops
        the drafts and closes the row. A refused paste does so through `end_amendment` and
        asks `results.amend.close` to delete the channel. The league's cancel and the lapse
        are `results.amend.cancel` and `results.amend.lapse`, which resolve the names, revert
        and delete the channel. The closed row stays until the round's next amendment
        replaces it, or until a restart forgets it, first deleting its channel where an
        earlier deletion failed.
        """
        from leaguebot.core.models.change import VerdictKind
        from leaguebot.core.utils.season_gate import LIVE_STAGES, season_for_command
        from leaguebot.results.models.points_config import SessionType
        from leaguebot.results.services.round_amendment.amend_changes import (
            COMMAND,
            KIND,
            amendable_sessions,
            label_of,
        )

        if not await self.bot.module_service.is_results_enabled():
            await refuse(
                interaction,
                "\u274c The Results & Standings module is not enabled.",
                what=describe(interaction),
            )
            return

        # The live season, not the most recent one (issue #224): amending a completed or
        # cancelled season's results is what the archive rule forbids. Pending completion is
        # named deliberately: amending a final round's results is one of the three things still
        # open there (decided 2026-09-20).
        season = await season_for_command(
            interaction, self.bot.season_service, COMMAND, stages=LIVE_STAGES
        )
        if season is None:
            return
        divisions = await self.bot.season_service.get_divisions(season.id)
        div = next((d for d in divisions if d.name.lower() == division_name.lower()), None)
        if div is None:
            await refuse(
                interaction, f"\u274c Division `{division_name}` not found.",
                what=describe(interaction),
            )
            return
        rounds = await self.bot.season_service.get_division_rounds(div.id)
        rnd = next((r for r in rounds if r.round_number == round_number), None)
        if rnd is None:
            await refuse(
                interaction, f"\u274c Round {round_number} not found.", what=describe(interaction)
            )
            return

        # Everything else the command refuses is the change's check, judged here and again when
        # the change runs, so an amendment waiting behind a stopped queue is refused as it would
        # be now. A request asked with no sessions is refused for any reason but the choice.
        queue = self.bot.change_queue
        what = f"/results rounds amend {div.name} {rnd.round_number}"
        what_amended = f"`/results rounds amend` of round {rnd.round_number} ({div.name})"
        asked = {"round_id": rnd.id, "division_id": div.id}
        if session is not None:
            chosen = [session.value]
        else:
            probe = {**asked, "session_types": []}
            if (await queue.check(KIND, probe)).kind is not VerdictKind.GO:
                await queue.ask(
                    KIND, probe, interaction=interaction, what=what,
                    refusal_what=describe(interaction),
                )
                return
            await interaction.response.defer(ephemeral=True)
            order = list(SessionType)
            present = await amendable_sessions(self.bot.db_path, rnd.id)
            # **Several sessions in one amendment** (#345, decided 2026-09-21). A round's
            # reports and appeals are reviewed together, so the sessions being corrected are
            # re-entered one after another and their decisions reviewed in one pass, with the
            # division rebuilt once at the end.
            sv = _AmendSessionsView(
                [(each.value, label_of(each)) for each in present], what=what_amended
            )
            await interaction.followup.send(
                "\U0001f4cb Select the sessions to amend. Each is re-entered in turn, and their "
                "reports and appeals are then reviewed together.",
                view=sv,
                ephemeral=True,
            )
            if await sv.wait():
                await record_abandoned(
                    self.bot, interaction.user, what=what_amended, lapsed=True,
                    detail=_PICKER_ENDED,
                )
                await interaction.followup.send(
                    "\u231b The choice of sessions lapsed, so nothing was amended. Run "
                    "`/results rounds amend` again to amend a round.",
                    ephemeral=True,
                )
                return
            if sv.cancelled or not sv.selected:
                await record_abandoned(
                    self.bot, sv.cancelled_by or interaction.user, what=what_amended,
                    lapsed=False, detail=_PICKER_ENDED,
                )
                await interaction.followup.send("ℹ️ Amendment cancelled.", ephemeral=True)
                return
            chosen = [
                each.value
                for each in sorted((SessionType(v) for v in sv.selected), key=order.index)
            ]
        await queue.ask(
            KIND, {**asked, "session_types": chosen}, interaction=interaction, what=what,
            refusal_what=describe(interaction),
        )

    # ------------------------------------------------------------------
    # /results channel group
    # ------------------------------------------------------------------

    channel_group = app_commands.Group(
        name="channel",
        description="Set the channels a division's results are posted to",
        parent=results_group,
    )

    async def _set_division_channel(
        self,
        interaction: discord.Interaction,
        name: str,
        channel: discord.TextChannel,
        channel_type: str,  # "results" | "standings" | "verdicts"
    ) -> None:
        """Ask the change queue to set a division's results, standings or verdicts channel, for
        the three commands below.

        Results' own commands, under results' own group. Each checks its module first in its own
        words, not `_module_gate`'s.

        **The live season's division**: a division's channels belong to the season being built or
        raced, and an archived one's no longer matter. Pending completion is live, so a channel
        lost before the season completes may be repaired. The command finds the division from
        the name typed, since the change is for it by its id (`results_channel_settings`).

        **A channel does one job** (`channel_registry_service`, judged by the setting's `check`),
        as the queue is asked and again as the change starts, so a refusal leaves the
        configuration exactly as it stood — the value the setting already holds included. The
        queue's acknowledgement is the command's response, so nothing is deferred; the queue
        writes the channel and its audit entry in one save and answers and logs after it.

        **Verdicts' channel is one the bot must be able to post in**, refused before the season
        is read, and its division is refused in words of its own.

        Every refusal is recorded: `refuse` answers and writes one line naming the command,
        which is read from the interaction.
        """
        if not await self.bot.module_service.is_results_enabled():
            await refuse(interaction, NOT_ENABLED, what=describe(interaction))
            return

        if channel_type == "verdicts":
            guild = interaction.guild
            if guild is None or not channel.permissions_for(guild.me).send_messages:
                await refuse(interaction, NO_ACCESS, what=describe(interaction))
                return

        season = await self.bot.season_service.get_setup_or_active_season()
        if season is None:
            await refuse(interaction, NO_SEASON_REFUSAL, what=describe(interaction))
            return

        divisions = await self.bot.season_service.get_divisions(season.id)
        div = next((d for d in divisions if d.name.lower() == name.lower()), None)
        if div is None:
            not_found = (
                verdicts_division_not_found if channel_type == "verdicts" else division_not_found
            )
            await refuse(interaction, not_found(name), what=describe(interaction))
            return

        await self.bot.change_queue.ask(
            _CHANNEL_KINDS[channel_type],
            {
                "division_id": div.id,
                "division_name": name,
                "value": channel.id,
                "channel_name": channel.name,
            },
            interaction=interaction,
            what=describe(interaction),
        )

    @channel_group.command(
        name="results",
        description="Set the results posting channel for a division.",
    )
    @app_commands.describe(name="Division name", channel="Results channel")
    @league_manager_only
    async def channel_results(
        self,
        interaction: discord.Interaction,
        name: str,
        channel: discord.TextChannel,
    ) -> None:
        await self._set_division_channel(interaction, name, channel, "results")

    @channel_group.command(
        name="standings",
        description="Set the standings posting channel for a division.",
    )
    @app_commands.describe(name="Division name", channel="Standings channel")
    @league_manager_only
    async def channel_standings(
        self,
        interaction: discord.Interaction,
        name: str,
        channel: discord.TextChannel,
    ) -> None:
        await self._set_division_channel(interaction, name, channel, "standings")

    @channel_group.command(
        name="verdicts",
        description="Set the verdicts (penalty announcement) channel for a division.",
    )
    @app_commands.describe(name="Division name", channel="Verdicts announcement channel")
    @league_manager_only
    async def channel_verdicts(
        self,
        interaction: discord.Interaction,
        name: str,
        channel: discord.TextChannel,
    ) -> None:
        """Set the channel a division's verdicts are announced in, by `_set_division_channel`."""
        await self._set_division_channel(interaction, name, channel, "verdicts")
