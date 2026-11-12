"""`RoundsCommands`: the `/results reserves`, `/results standings` and `/results rounds` subgroups.

The three act on a division's rounds or standings, and are small, so they share one file: the
reserves toggle; the standings repost; the rounds repost and the round amendment, with the
session picker the amendment posts (`_AmendSessionsView`).
"""
from __future__ import annotations

import logging

import discord
from discord import app_commands

from leaguebot.results.cogs.results_group import RESULTS_GROUP, ResultsSubgroup
from leaguebot.results.models.points_config import SessionType
from leaguebot.results.services.points.points_settings import RESERVES, reserves_toggle_request
from leaguebot.core.utils.channel_guard import league_admin_only, league_manager_only
from leaguebot.core.utils.interaction_errors import describe
from leaguebot.core.utils.league_server import (
    CallbackButton,
    CallbackSelect,
    LeagueView,
    guild_of,
)
from leaguebot.core.utils.log_lines import record_abandoned, refuse
from leaguebot.core.utils.season_gate import season_for_command

log = logging.getLogger(__name__)

#: What a `/results rounds amend` left at its session picker leaves beneath its line.
_PICKER_ENDED = "Nothing was amended. Run `/results rounds amend` again to amend a round."

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


class RoundsCommands(ResultsSubgroup):
    # ------------------------------------------------------------------
    # /results reserves group — T026
    # ------------------------------------------------------------------

    reserves_group = app_commands.Group(
        name="reserves", description="Reserve driver visibility in standings", parent=RESULTS_GROUP
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
        name="standings", description="Standings commands", parent=RESULTS_GROUP
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
        name="rounds", description="Round results commands", parent=RESULTS_GROUP
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
