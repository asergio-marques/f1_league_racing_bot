"""`PointsAmendCommands`: the `/results amend` subgroup, the season's points mid-season.

The commands that switch amendment mode on and off, stage a change to a session's points, the
fastest-lap bonus or its position limit, revert the staged changes and review them for approval,
with the form and the review panel they post (`BulkAmendSessionModal`, the amend review).
"""
from __future__ import annotations

import logging

import discord
from discord import app_commands

from leaguebot.results.cogs.results_group import (
    MODULE_OFF,
    RACE_SESSION_CHOICES,
    RESULTS_GROUP,
    SESSION_CHOICES,
    ResultsSubgroup,
)
from leaguebot.results.services.points.amendment_settings import (
    AMEND_FASTEST_LAP,
    AMEND_MODE,
    AMEND_POINTS,
    AMEND_REVERT,
)
from leaguebot.results.services.points.amendment_mode_service import review_panel
from leaguebot.results.services.points.points_amendment_change import (
    KIND as POINTS_AMENDMENT,
    refuse_while_approval_in_hand,
)
from leaguebot.results.services.points.points_paste import bulk_refusal, parse_bulk_lines
from leaguebot.core.utils.channel_guard import league_admin_only, league_manager_only
from leaguebot.core.utils.interaction_errors import describe
from leaguebot.core.utils.league_bot import bot_of
from leaguebot.core.utils.league_server import LeagueModal, LeagueView
from leaguebot.core.utils.log_lines import record_abandoned, refuse
from leaguebot.core.utils.member_names import interaction_member
from leaguebot.core.utils.season_gate import season_for_command

log = logging.getLogger(__name__)

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


class PointsAmendCommands(ResultsSubgroup):
    # ------------------------------------------------------------------
    # /results amend group — T025
    # ------------------------------------------------------------------
    #
    # Every command of this group, and of the reserves, standings and rounds groups,
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
        name="amend", description="Mid-season points amendment management", parent=RESULTS_GROUP
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
