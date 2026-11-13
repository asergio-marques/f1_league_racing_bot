"""`GroupCommands`: the commands on `/attendance` itself, `sync` and `post-check-in`.

They join the group after its subgroups, which is the order Discord is sent them in, so this file
is imported after the subgroups' (`attendance_cog.py`).
"""
from __future__ import annotations

import discord
from discord import app_commands

from leaguebot.attendance.cogs.attendance_group import (
    AFTER_THE_CATCH_UP,
    ATTENDANCE_GROUP,
    AttendanceSubgroup,
)
from leaguebot.attendance.services.scoring.attendance_recompute import recalculation_faults
from leaguebot.attendance.services.attendance_sync_change import (
    NOT_ONGOING,
    SYNC,
    amendment_verdict,
    faults_verdict,
    round_verdict,
)
from leaguebot.attendance.services.check_in.check_in_call import ask_check_in_call, judge_hand_post
from leaguebot.core.models.change import ChangeOrigin
from leaguebot.core.models.season import ONGOING_STAGES
from leaguebot.core.services.seasons.season_service import round_by_number
from leaguebot.core.utils.channel_guard import league_manager_only
from leaguebot.core.utils.interaction_errors import describe
from leaguebot.core.utils.log_lines import refuse
from leaguebot.results.services.round_amendment.amendment_writes import open_amendment_in_division


class GroupCommands(AttendanceSubgroup):
    # ── /attendance sync ───────────────────────────────────────────────────

    @ATTENDANCE_GROUP.command(
        name="sync",
        description="Recalculate a division's attendance from a round on, and apply any sanction still owed.",
    )
    @app_commands.describe(
        division="Division name",
        round="Round number to recalculate from; every finalised round after it follows.",
    )
    @league_manager_only
    @AFTER_THE_CATCH_UP
    async def sync(
        self, interaction: discord.Interaction, division: str, round: int
    ) -> None:
        """Finish a run of attendance sanctions that did not all apply (decided 2026-09-18, #239).

        A league manager's, like the thresholds it enforces: it makes no judgement of its own,
        only applies what the league configured. Refused, with nothing changed, outside the
        ongoing stages — a sanction then has no seat to take — for a round not yet finalised,
        and where a channel the recalculation posts to cannot be reached, the gate an
        amendment's recalculation holds to (#187).

        **It is one change on the queue** (`attendance_sync_change`): what can be judged is judged
        here, to answer at once, and again by the change as it starts. The manager is answered
        with the queue's acknowledgement, and then with what the sync did; nothing is deferred,
        the acknowledgement being the response.
        """
        if not await self._module_gate(interaction):
            return

        async def refused(reply: str, reason: str | None = None) -> None:
            # Answered by a follow-up, as the command's refusals always were; an interaction
            # the queue acknowledges is not deferred, so the response is taken only here.
            await interaction.response.defer(ephemeral=True)
            await refuse(interaction, reply, what=describe(interaction), reason=reason)

        season = await self.bot.season_service.get_confirmed_season()
        if season is None or season.stage not in ONGOING_STAGES:
            await refused(NOT_ONGOING)
            return

        divisions = await self.bot.season_service.get_divisions(season.id)
        div = next((d for d in divisions if d.name.lower() == division.lower()), None)
        if div is None:
            await refused(f"\u274c Division '{division}' not found.")
            return

        db_path = self.bot.db_path
        verdict = amendment_verdict(await open_amendment_in_division(db_path, div.id), div.name)
        found = None
        if verdict is None:
            found = await round_by_number(db_path, div.id, round)
            verdict = round_verdict(None if found is None else str(found.status), div.name, round)
        if verdict is None:
            verdict = faults_verdict(
                await recalculation_faults(db_path, season.id, interaction.guild, self.bot)
            )
        if verdict is not None:
            await refused(verdict.reply, verdict.reason or None)
            return

        # One change: the recalculation and the sanctions it owes are saved together, and the
        # cards, the lineups and the sheets follow as jobs of it (`attendance_sync_change`).
        assert found is not None
        await self.bot.change_queue.ask(
            SYNC,
            {
                "division_id": div.id, "division": div.name, "round_id": found.id,
                "round_number": round, "season_id": season.id,
            },
            interaction=interaction, what=describe(interaction),
        )

    # ── /attendance post-check-in ──────────────────────────────────────────

    @ATTENDANCE_GROUP.command(
        name="post-check-in",
        description="Post a round's check-in call by hand, where the scheduled one never went out.",
    )
    @app_commands.describe(
        division="Division name",
        round="Round number whose check-in call is missing.",
    )
    @league_manager_only
    async def post_check_in(
        self, interaction: discord.Interaction, division: str, round: int
    ) -> None:
        """Post a check-in call a league never received (decided 2026-09-20, #123).

        **A last resort, not a routine.** A call reaches a division from its scheduled job and
        from the recovery that re-arms it after a restart; this is the repair for when both
        have failed, and the advice a failed call's line gives in the log channel — post the
        call again once the cause is cleared — had no command behind it until now. A league
        reaching for this regularly has a cause nobody has fixed.

        It is confined to the window in which a call *should* be standing and is not. Before
        the call is due the scheduled one is still coming, and posting early would override the
        lead time the league configured; after the deadline the buttons lock on arrival, so the
        call would be unanswerable. A deadline of zero disables the *closing* of a check-in and
        not the race, so the round's own moment is the boundary then. A call already standing is **not** replaced — an amendment
        is the path that takes one down and carries the answers over, and a second call beside
        the first would split a division's answers across two messages.

        **It is a change on the queue** (#439): the call is `attendance.check_in.call`, the change
        the check-in timer asks, asked with the manager's interaction. The manager is told it is
        under way and then what became of it, never a success for a post Discord refused. What
        the window allows is judged here to answer at once and held again when the change runs, so
        a scheduled call racing this one is met by the queue running one change at a time, and the
        reply names it.
        """
        if not await self._module_gate(interaction):
            return
        await interaction.response.defer(ephemeral=True)

        season = await self.bot.season_service.get_confirmed_season()
        if season is None or season.stage not in ONGOING_STAGES:
            await refuse(
                interaction,
                "⛔ `/attendance post-check-in` is available only while the season is ongoing.",
                what=describe(interaction),
            )
            return

        divisions = await self.bot.season_service.get_divisions(season.id)
        div = next((d for d in divisions if d.name.lower() == division.lower()), None)
        if div is None:
            await refuse(
                interaction,
                f"❌ Division '{division}' not found.",
                what=describe(interaction),
            )
            return

        # What the window allows is judged here to answer at once, and again by the change when
        # its turn comes (`hand_post_refusal`): a queue stopped for a while can bring it up to
        # run after its deadline, or beside a call the timer has posted meanwhile.
        round_id, refusal = await judge_hand_post(
            self.bot.db_path,
            await self.bot.attendance_service.get_config(),
            div.id,
            div.name,
            round,
            now=self.bot.change_queue.now(),
        )
        if refusal is not None or round_id is None:
            await refuse(interaction, refusal or "", what=describe(interaction))
            return

        await ask_check_in_call(
            self.bot.change_queue,
            self.bot.db_path,
            round_id,
            origin=ChangeOrigin.MEMBER,
            interaction=interaction,
            refusal_what=describe(interaction),
        )
