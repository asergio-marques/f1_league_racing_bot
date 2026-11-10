"""What a season review's buttons do once they are let through: the review's workflows.

A review's button is pressed on a review that is standing; the view in front of it settles who may
press, whether the review has expired and whether the season has changed since. What is then left
is a workflow, run here: it runs every gate the confirmation rests on, in its order, and answers
the member through the interaction it is handed where one refuses, **in the words the change's own
check uses** (the change judges them again when it runs). Where every gate passes, it asks the
change queue for the confirmation, the review taken down ahead of it (`take_review_down_before`),
and the queue's acknowledgement is the press's own response.

Each workflow takes the interaction pressed, the services it reads as keywords, named as the bot
carries them, and the press's own particulars (*what*, the button as the log channel names it, and
the review's *fingerprint*). It answers `True` where the queue accepted the request, and `False`
where the press was refused, which leaves the button's review standing. The gates it runs are
`approval_checks`', called through that module.

**The approval's backup question** (`offer_backup_before_approving`, its view
`BackupBeforeApprovalView`) is put under test mode, after every gate and before every write, and
is given what is left of the review's window. The time left, and the dates the approval's calendar
gate judges, are read from the *now* the caller hands in, the change queue's clock, as the change
types are handed it.

The check of the change a workflow asks is the same gates again, judged as the change runs, so the
refusal at the press and the refusal at the run cannot disagree.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

import discord

from leaguebot.core.models.season import SeasonStage
from leaguebot.core.services import approval_checks, backup_service
from leaguebot.core.services.approval_checks import SignupModuleSettings
from leaguebot.core.services.change_queue import ChangeQueue
from leaguebot.core.services.config_service import ConfigService
from leaguebot.core.services.configuration_confirm_change import KIND as CONFIGURATION_CONFIRM_KIND
from leaguebot.core.services.image_drawing import ImageDrawing
from leaguebot.core.services.mid_season_confirm_change import (
    ALREADY_BEING_CONFIRMED,
    KIND as MID_SEASON_CONFIRM_KIND,
    STAGE_REFUSAL as CONFIRM_STAGE_REFUSAL,
    confirmation_in_hand,
    every_division_done_in,
    every_division_done_refusal,
)
from leaguebot.core.services.module_service import ModuleService
from leaguebot.core.services.output_router import OutputRouter
from leaguebot.core.services.round_moments import read_round_windows
from leaguebot.core.services.scheduler_service import SchedulerService
from leaguebot.core.services.season_approval_change import (
    ALREADY_BEING_APPROVED,
    KIND as APPROVAL_KIND,
    approval_in_hand,
)
from leaguebot.core.services.season_fingerprint_service import take_fingerprint
from leaguebot.core.services.season_review_changes import take_review_down_before
from leaguebot.core.services.season_service import SeasonService
from leaguebot.core.services.team_service import TeamService
from leaguebot.core.utils.formatting import discord_ts
from leaguebot.core.utils.hooks import AwaitedHook
from leaguebot.core.utils.interaction_errors import report_failure
from leaguebot.core.utils.league_bot import bot_of
from leaguebot.core.utils.league_server import LeagueView
from leaguebot.core.utils.log_lines import record_abandoned, refuse
from leaguebot.core.utils.member_names import interaction_member

__all__ = [
    "BackupBeforeApprovalView",
    "approve_season",
    "confirm_configuration",
    "confirm_mid_season_placements",
    "offer_backup_before_approving",
    "review_button",
]


def review_button(label: str, review: str) -> str:
    """A review's button as the log channel names it: which button, of which review."""
    return f"the ✅ {label} button of `{review}`"


async def confirm_configuration(
    interaction: discord.Interaction,
    *,
    season_service: SeasonService,
    module_service: ModuleService,
    signup_module_service: SignupModuleSettings,
    config_service: ConfigService,
    team_service: TeamService,
    db_path: str,
    image_drawing: ImageDrawing,
    change_queue: ChangeQueue,
    what: str | None = None,
    fingerprint: dict | None = None,
) -> bool:
    """Ask the change queue to confirm the configuration, the review taken down ahead of it.
    Returns whether the queue accepted the request: False where the press was refused, which
    leaves the button's review standing.

    A refusal is recorded under *what*, the button pressed, which the button passes in;
    the review's own Confirm configuration button where the caller names none. *fingerprint*
    is the review's own, the areas of the season as the report described it, which the
    confirmation is judged against again when it runs; where it is not given the season is
    fingerprinted as it stands.

    **The press is not deferred:** the queue's acknowledgement is the press's own response
    and the confirmation's outcome updates it. The refusals below answer the press at once
    in the words the confirmation's own check uses (`configuration_confirm_change`), which
    judges them again when the confirmation runs, and the season no longer in Configuration
    with them: a season changed while it waited must not be confirmed. Everything after the
    request, the stage, signup's settings, the audit entry and the line, is the change's one
    save, to Waiting where the signup module is enabled and test mode is off, or to Placements
    otherwise.

    The review comes down ahead of the confirmation, but only for a request the queue
    accepts: one it refuses at once leaves the review standing.
    """
    if what is None:
        what = review_button("Confirm configuration", "/season config-review")
    season = await season_service.get_setup_season()
    if season is None:
        await refuse(interaction, "⛔ There is no season in configuration.", what=what)
        return False

    faults = await approval_checks.configuration_faults(
        season.id,
        interaction.guild,
        module_service=module_service,
        signup_module_service=signup_module_service,
        config_service=config_service,
        team_service=team_service,
        db_path=db_path,
        image_drawing=image_drawing,
    )
    if faults:
        body = "\n".join(f"• {fault}" for fault in faults)
        await refuse(
            interaction,
            f"⛔ The configuration cannot be confirmed:\n{body}\n"
            "**Nothing has been confirmed.**",
            what=what,
            reason="the configuration cannot be confirmed:\n" + "\n".join(faults),
        )
        return False

    if fingerprint is None:
        fingerprint = (
            await take_fingerprint(db_path, season.id, drawing=image_drawing)
        ).areas
    confirmation = {
        "season_id": season.id,
        "season_number": season.season_number,
        "fingerprint": dict(fingerprint),
    }
    question = getattr(interaction.message, "id", None)
    if isinstance(question, int):
        change_id = await take_review_down_before(
            change_queue, db_path, question, CONFIGURATION_CONFIRM_KIND,
            confirmation, interaction=interaction, what=what,
        )
    else:
        change_id = await change_queue.ask(
            CONFIGURATION_CONFIRM_KIND, confirmation, interaction=interaction, what=what
        )
    # None where the queue's own check refused the request and saved nothing.
    return change_id is not None


async def confirm_mid_season_placements(
    interaction: discord.Interaction,
    *,
    season_service: SeasonService,
    module_service: ModuleService,
    db_path: str,
    image_drawing: ImageDrawing,
    change_queue: ChangeQueue,
    what: str | None = None,
    fingerprint: dict | None = None,
) -> bool:
    """Ask the change queue to confirm the new placements, the review taken down ahead of it.
    Returns whether the queue accepted the request: False where the press was refused, which
    leaves the button's review standing.

    A refusal is recorded under *what*, the button pressed, which the button passes in;
    the review's own Confirm placements button where the caller names none. *fingerprint*
    is the review's own, the areas of the season as the report described it, which the
    confirmation is judged against again when it runs; where it is not given the season is
    fingerprinted as it stands.

    **The press is not deferred:** the queue's acknowledgement is the press's own response
    and the confirmation's outcome updates it. The refusals below answer the press at once
    in the words the confirmation's own check uses (`mid_season_confirm_change`), which
    judges them again when the confirmation runs: a season changed while it waited must
    not be committed. Everything after the request, the placements and the season's return
    to Ongoing in one save, each driver's roles, each lineup, the one line that records it,
    is the change's, a job each, so that a stop leaves nothing undone for good.
    """
    if what is None:
        what = review_button("Confirm placements", "/season placements-review")
    season = await season_service.get_confirmed_season()
    if season is not None and await confirmation_in_hand(db_path, season.id):
        await refuse(interaction, ALREADY_BEING_CONFIRMED, what=what)
        return False
    if season is None or season.stage is not SeasonStage.ONGOING_PLACEMENTS:
        await refuse(interaction, CONFIRM_STAGE_REFUSAL, what=what)
        return False
    if await every_division_done_in(db_path, season.id):
        await refuse(interaction, every_division_done_refusal(season.season_number), what=what)
        return False
    # Judged afresh, as the first confirmation judges its placements gate. The review withholds
    # its button on each of these, but it stands five minutes, and a channel deleted from
    # the server meanwhile changes nothing the fingerprint reads. The lineups are not
    # drawn again: the fingerprint proves the images configuration and the artwork
    # unchanged since the review drew them.
    unsettled, channel_faults = await approval_checks.placement_confirmation_faults(
        module_service, db_path, season.id, interaction.guild
    )
    faults = [
        *(f"Unsettled signup: {line}" for line in unsettled),
        *channel_faults,
        *await approval_checks.mid_season_configuration_faults(module_service, image_drawing),
    ]
    if faults:
        bullets = "\n".join(f"\u2022 {line}" for line in faults)
        await refuse(
            interaction,
            f"\u26d4 Placements cannot be confirmed:\n{bullets}\n"
            "**Nothing has been confirmed.**",
            what=what,
            reason="placements cannot be confirmed:\n" + "\n".join(faults),
        )
        return False

    if fingerprint is None:
        fingerprint = (await take_fingerprint(db_path, season.id, drawing=image_drawing)).areas
    # The review comes down ahead of the confirmation's own jobs, but only for a request
    # the queue accepts: one it refuses at once leaves the review standing.
    confirmation = {
        "season_id": season.id,
        "season_number": season.season_number,
        "fingerprint": dict(fingerprint),
        "channel_id": interaction.channel_id,
    }
    question = getattr(interaction.message, "id", None)
    if isinstance(question, int):
        change_id = await take_review_down_before(
            change_queue, db_path, question, MID_SEASON_CONFIRM_KIND,
            confirmation, interaction=interaction, what=what,
        )
    else:
        change_id = await change_queue.ask(
            MID_SEASON_CONFIRM_KIND, confirmation, interaction=interaction, what=what
        )
    # None where the queue's own check refused the request and saved nothing.
    return change_id is not None


#: What became of a backup that a fault stopped: the approval goes on without it.
_BACKUP_NOT_TAKEN = "The backup was not taken. The season is being approved anyway."


class BackupBeforeApprovalView(LeagueView):
    """Save the databases, or don't, or stop — asked between the last gate and the commit.

    Its timeout is what remains of the approval window rather than a span of its own, so a
    review whose backup question goes unanswered expires exactly when it would have expired
    anyway. `answer` is None in that case, which is how the caller tells silence from a
    deliberate "no".

    **What the log holds.** A save that is taken writes a success line of its own, so the record
    holds it whatever the approval then does. A save refused (`BackupError`: locked, none saved)
    is a refusal with its reason; one a fault stopped (`BackupFault`, or any other error) takes
    the standard failure reply and line, its outcome saying the backup was not taken and the
    season is being approved anyway. Neither stops the approval.
    """

    #: The save button as the log channel names it.
    _save_button = "the \U0001f4be Save, then approve button of `/season placements-review`"

    def __init__(
        self,
        *,
        timeout: float,
        scheduler_service: SchedulerService,
        db_path: str,
        output_router: OutputRouter,
    ) -> None:
        super().__init__(timeout=timeout)
        self._scheduler_service = scheduler_service
        self._db_path = db_path
        self._output_router = output_router
        self.answer: str | None = None

    @discord.ui.button(label="\U0001f4be Save, then approve", style=discord.ButtonStyle.primary)
    async def save(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        scheduler = self._scheduler_service
        try:
            # Paused for the copy exactly as `/test-mode backup save` pauses it.
            with scheduler.paused():
                backup_service.save(self._db_path, scheduler.jobstore_path)
        except backup_service.BackupFault as exc:
            # A fault in the bot's own copy: the standard failure form, in place of the
            # fault's text, which a manager cannot act on. The details go to the host's log.
            await report_failure(
                interaction,
                exc,
                what=self._save_button,
                outcome=_BACKUP_NOT_TAKEN,
            )
            self.answer = "skip"
            self.stop()
            return
        except backup_service.BackupError as exc:
            # A backup that cannot be taken does not refuse the season. The manager asked
            # for a convenience and is told it was not available; approving is what they
            # actually came to do, and the alternative is making them run the review again
            # for a reason that has nothing to do with the season.
            await refuse(
                interaction,
                f"⚠️ The backup was not taken — {exc}\nApproving the season "
                f"anyway.",
                what=self._save_button,
                reason=str(exc),
            )
            self.answer = "skip"
            self.stop()
            return
        except Exception as exc:
            await report_failure(
                interaction,
                exc,
                what=self._save_button,
                outcome=_BACKUP_NOT_TAKEN,
            )
            self.answer = "skip"
            self.stop()
            return

        # Its own line, so the record holds the backup whatever the approval then does.
        await self._output_router.post_log(
            f"{interaction_member(interaction)} | /season placements-review backup | Success"
        )
        await interaction.followup.send(
            "✅ Saved. Restore it with `/test-mode backup restore`.", ephemeral=True
        )
        self.answer = "save"
        self.stop()

    @discord.ui.button(label="Approve without saving", style=discord.ButtonStyle.secondary)
    async def skip(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        self.answer = "skip"
        self.stop()

    @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.danger)
    async def cancel(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        self.answer = "cancel"
        self.stop()


async def offer_backup_before_approving(
    interaction: discord.Interaction,
    *,
    deadline: datetime,
    config_service: ConfigService,
    db_path: str,
    change_queue: ChangeQueue,
    scheduler_service: SchedulerService,
    output_router: OutputRouter,
    now: Callable[[], datetime],
) -> bool:
    """Under test mode, ask whether to save the databases. Returns whether to go on.

    Returns True where the approval should continue — the server is not in test mode
    and there was nothing to ask, the manager said no, or the backup was taken. False
    where the approval must stop: the question went unanswered until the review
    expired, or the manager cancelled.

    **The question is given what is left of the approval window, not a window of its
    own.** A review stops describing its season five minutes after it is posted, and
    that is as true of a season waiting on this question as of one waiting on the
    button. Leaving it unanswered therefore expires the approval rather than holding
    it open, which is the whole reason the deadline is carried in here.

    Each way the approval ends here is recorded in the log channel: a cancel as a cancel,
    and the question left unanswered, or the review expiring before it was asked, as a
    lapse, with the reply's own words beneath.
    """
    config = await config_service.get_server_config()
    if config is None or not getattr(config, "test_mode_active", False):
        return True
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)

    remaining = (deadline - now()).total_seconds()
    if remaining <= 0:
        reply = (
            "⏱️ This review expired before the season could be approved. Run "
            "`/season placements-review` again. **Nothing has been approved.**"
        )
        await interaction.followup.send(reply, ephemeral=True)
        await _record_backup_question_ended(interaction, reply, lapsed=True)
        return False

    state = backup_service.state(db_path)
    standing = (
        f"A backup taken {discord_ts(state.taken_at)} would be replaced."
        if state.exists and not state.locked and state.taken_at is not None
        else "The saved backup is locked and will not be replaced."
        if state.locked
        else "Nothing is saved yet."
    )

    view = BackupBeforeApprovalView(
        timeout=remaining,
        scheduler_service=scheduler_service,
        db_path=db_path,
        output_router=output_router,
    )
    await interaction.followup.send(
        f"\U0001f4be **Save the databases before approving?**\n"
        f"Approving arms the schedule, grants the roles and posts the lineups, which "
        f"is a laborious thing to build again. {standing}\n"
        f"This review expires {discord_ts(deadline)} whichever you choose, so answer "
        f"before then.",
        view=view,
        ephemeral=True,
    )
    await view.wait()

    if view.answer is None:
        reply = (
            "⏱️ This review expired while the backup question went "
            "unanswered. Run `/season placements-review` again. **Nothing has been approved.**"
        )
        await interaction.followup.send(reply, ephemeral=True)
        await _record_backup_question_ended(interaction, reply, lapsed=True)
        return False
    if view.answer == "cancel":
        await interaction.followup.send(
            "Nothing has been approved, and nothing has been saved.", ephemeral=True
        )
        await _record_backup_question_ended(
            interaction,
            "Nothing has been approved, and nothing has been saved. "
            "Run the review again.",
            lapsed=False,
        )
        return False
    return True



async def _record_backup_question_ended(
    interaction: discord.Interaction, detail: str, *, lapsed: bool
) -> None:
    """Record that the approval ended at the backup question: cancelled, or lapsed.

    The reply's own words go beneath the line, less the marks a message carries.
    """
    await record_abandoned(
        bot_of(interaction),
        interaction.user,
        what="`/season placements-review`",
        lapsed=lapsed,
        detail=detail.removeprefix("⏱️ ").replace("**", ""),
    )



async def approve_season(
    interaction: discord.Interaction,
    *,
    season_service: SeasonService,
    module_service: ModuleService,
    signup_module_service: SignupModuleSettings,
    config_service: ConfigService,
    team_service: TeamService,
    db_path: str,
    image_drawing: ImageDrawing,
    change_queue: ChangeQueue,
    scheduler_service: SchedulerService,
    output_router: OutputRouter,
    round_windows_read: AwaitedHook,
    now: Callable[[], datetime],
    deadline: datetime | None = None,
    what: str | None = None,
    fingerprint: dict | None = None,
) -> bool:
    """Approve the season: every gate, then the backup question where test mode is on, then
    the approval is asked of the change queue, which carries it out.

    Returns whether the approval was asked of the queue and saved. False where a gate
    refused the press, the backup question was cancelled or went unanswered, or the queue's
    own check refused the approval: the button's review then stands
    (`_ApproveView.approve`), and nothing has been approved.

    *deadline* is when the review stops being approvable, carried in from the view so
    the backup question below can be given what is left of that window rather than a
    fresh one. Omitted, the question is skipped — there is no window to divide.

    *fingerprint* is the review's own, the areas of the season as the report described
    it, which the approval is judged against again when it runs. The view always hands it
    in; where it is not given, as the gate tests call this, the season is fingerprinted as
    it stands, so that the check at run is never an empty one.

    **The press is not deferred here.** The queue's acknowledgement is the press's own response
    and its outcome updates it; only the backup question, which waits on a person, defers
    first. A refusal below answers the press itself. **The in-hand check comes after the gates
    and before the question**, so that a second approval of the season, from any review, is
    refused at once without being asked whether to save the databases, and without naming the
    job. Everything after it, the writes, the roles and the posts, is the change's
    (`season_approval_change`), a job each, so that a stop leaves nothing undone for good.
    """
    # Every gate below refuses through `refuse`, naming the button pressed (*what*, the
    # review's own Approve button where the caller names none) in the log channel.
    if what is None:
        what = review_button("Approve", "/season placements-review")

    season = await season_service.get_setup_season()
    if season is None:
        await refuse(interaction, "\u274c No pending season setup.", what=what)
        return False

    if await season_service.get_stage(season.id) is not SeasonStage.PLACEMENTS:
        await refuse(
            interaction,
            "\u26d4 The season is no longer in placements. **Nothing has been approved.**",
            what=what,
        )
        return False

    if not await approval_checks.season_has_divisions(
        season.id, season_service=season_service
    ):
        await refuse(
            interaction, approval_checks.NO_DIVISIONS_REFUSAL + " **Nothing has been approved.**", what=what
        )
        return False

    # ── Gate S: every signup settled, and every channel a division posts to set and on
    # the server ──
    unsettled, channel_faults = await approval_checks.placement_confirmation_faults(
        module_service, db_path, season.id, interaction.guild
    )
    if unsettled or channel_faults:
        bullets = "\n".join(f"\u2022 {line}" for line in [*unsettled, *channel_faults])
        await refuse(
            interaction,
            f"\u26d4 Season cannot be approved:\n{bullets}",
            what=what,
            reason="the season cannot be approved:\n" + "\n".join([*unsettled, *channel_faults]),
        )
        return False

    # Validate tier sequential integrity before committing
    try:
        await season_service.validate_division_tiers(season.id)
    except ValueError as exc:
        await refuse(interaction, f"\u26d4 Season cannot be approved. {exc}", what=what)
        return False

    divisions = await season_service.get_divisions(season.id)
    div_rounds: dict[int, list] = {}
    for div_db in divisions:
        div_rounds[div_db.id] = await season_service.get_division_rounds(div_db.id)

    # ── Gate 0: every division must have at least one round ────────────────
    empty_divs = [d.name for d in divisions if not div_rounds[d.id]]
    if empty_divs:
        names = ", ".join(f"**{n}**" for n in empty_divs)
        await refuse(
            interaction,
            f"\u274c Season cannot be approved \u2014 the following divisions have no rounds: "
            f"{names}. Add at least one round to each division first.",
            what=what,
        )
        return False

    # ── Gate 0b: no two rounds in the same division may share a datetime ──
    duplicate_errors: list[str] = []
    for div_db in divisions:
        seen: set = set()
        for rnd in div_rounds[div_db.id]:
            if rnd.scheduled_at in seen:
                duplicate_errors.append(
                    f"**{div_db.name}** has multiple rounds scheduled at {discord_ts(rnd.scheduled_at)}"
                )
                break
            seen.add(rnd.scheduled_at)
    if duplicate_errors:
        bullet_list = "\n\u2022 ".join(duplicate_errors)
        await refuse(
            interaction,
            f"\u274c Season cannot be approved \u2014 duplicate round times detected:\n\u2022 {bullet_list}\n"
            f"Reschedule rounds so each has a unique datetime within its division.",
            what=what,
            reason="duplicate round times detected:\n" + "\n".join(duplicate_errors),
        )
        return False

    # Every channel a division posts to — the weather, results, standings, verdicts, RSVP
    # and attendance channels among them — is judged at Gate S above, by the helper the
    # review withholds its button on.

    # ── Gate 2: points-config prerequisites (FR-013) ───────────────────────
    if await module_service.is_results_enabled():
        # Test mode is no exception: it attaches Standard and Half
        # Points when it is enabled, and a test season that has had them detached is
        # refused here as any other rather than having them attached again behind it.
        errors: list[str] = []
        if await approval_checks.no_points_config_attached(db_path, season.id):
            errors.append("no points configuration is attached to this season")

        # Counting the links is not the same as having the configurations they name.
        # A link is a bare name, so one that was mistyped — or one whose configuration
        # has since been removed — satisfies the count above and then breaks the
        # snapshot further down, which is exactly the silence a snapshot that cannot be taken makes.
        for phantom in await approval_checks.missing_points_config_problems(db_path, season.id):
            errors.append(
                f"points configuration **{phantom}** is attached to this season but "
                f"does not exist — create it with `/results config add`, or drop it "
                f"with `/results config detach {phantom}`"
            )

        if errors:
            bullet_list = "\n\u2022 ".join(errors)
            await refuse(
                interaction,
                f"\u274c Season cannot be approved \u2014 R&S prerequisites not met:\n\u2022 {bullet_list}",
                what=what,
                reason="R&S prerequisites not met:\n" + "\n".join(errors),
            )
            return False

        # ── Gate 2a: monotonic ordering check (FR-008) ───────────────────
        #
        # Through the same helper `/season placements-review` reports from, so the report a
        # manager was given and the refusal they then meet cannot disagree.
        mono_errors = await approval_checks.points_ordering_problems(db_path, season.id)
        if mono_errors:
            bullet_list = "\n\u2022 ".join(mono_errors)
            await refuse(
                interaction,
                f"\u274c Season cannot be approved \u2014 points configuration "
                f"violates monotonic ordering:\n\u2022 {bullet_list}",
                what=what,
                reason="points configuration violates monotonic ordering:\n"
                + "\n".join(mono_errors),
            )
            return False

    # ── Gate 2b: signup module config prerequisites ───────────────────────
    # The league's two roles are not checked again: confirming the configuration
    # required them while signup was enabled, and fixed them until the season ends.
    if await module_service.is_signup_enabled():
        signup_cfg = await signup_module_service.get_config()
        if signup_cfg:
            missing: list[str] = []
            if signup_cfg.signup_channel_id is None:
                missing.append("**Signup channel** (use `/signup channel`)")
            if missing:
                bullet_list = "\n\u2022 ".join(missing)
                await refuse(
                    interaction,
                    f"\u274c Season cannot be approved \u2014 signup module is enabled but "
                    f"missing required configuration:\n\u2022 {bullet_list}",
                    what=what,
                    reason="signup module is enabled but missing required configuration:\n"
                    + "\n".join(missing),
                )
                return False

    # ── Gate 2d: no round may already have run, nor be inside a window
    #
    # A season's scheduled work is armed here, and every piece of it fires at some
    # configured distance *before* the round. Approve late enough and those moments are
    # behind us, and unrefused that would be silent. An overdue check-in call is never
    # scheduled at all, so the round asks nobody whether they are racing, distributes
    # no reserves, and is recorded afterwards as perfect attendance for the whole
    # division, with nothing able to recover it. An overdue weather phase is armed
    # past-dated and thrown away for exceeding the scheduler's misfire grace, coming
    # back only if the host happens to restart before the race.
    #
    # **And the round's own moment is judged, whatever the modules**. The windows
    # above are contributed by the modules that configure them, so a league running
    # neither weather nor attendance has none, and a season every round of which is
    # already in the past would be approved in silence, every round of it stuck at *not
    # run* for good, because a result-submission job armed that far behind is thrown away
    # by the misfire grace and nothing in production opens a submission channel afterwards.
    # `/round amend` refuses a backwards move on the same reasoning, so the two
    # doors agree about a round.
    #
    # Refused rather than caught up: the remedy is to move the round or shorten the
    # window, and choosing between posting late, posting nothing, and losing the round's
    # records is the league's decision, not the bot's.
    #
    # Checked again here although `/season placements-review` reports it and withholds its button:
    # the review stands for five minutes, and a round on the right side of a window when
    # the report was drawn can be the wrong side of it by the time Approve is pressed.
    #
    # No exemption under test mode. Test mode relaxes approval
    # elsewhere — it seeds points configurations so a test season passes that
    # requirement — but a test season that loses its check-ins misreports attendance
    # exactly as a real one does, and is a worse thing to be testing against.
    _att_windows, _wx_windows = await read_round_windows(round_windows_read)
    _dates = approval_checks.date_refusal(
        divisions,
        div_rounds,
        now=now(),
        attendance=_att_windows,
        weather=_wx_windows,
    )
    if _dates is not None:
        await refuse(interaction, _dates.reply, what=what, reason=_dates.reason)
        return False

    # Everything above is a database read. What follows reads the image module's
    # templates, artwork and settings besides — still no rasterisation, the approval
    # drawing nothing (see below), but dearer than a query, so the cheap checks come
    # first and a league missing a channel is told so before any file is opened.

    # ── Gate 3a: team names can address a lineup template (038, FR-013) ───
    #
    # Not gated on the image module: a team name is a structural invariant of teams
    # (Principle IX), constrained at the one moment it is set so that a league
    # enabling the module later is not left with names it cannot correct.
    name_problems = await approval_checks.team_name_problems(team_service, season.id)
    if name_problems:
        bullet_list = "\n• ".join(name_problems)
        await refuse(
            interaction,
            f"❌ Season cannot be approved — these team names cannot become "
            f"lineup template fields:\n• {bullet_list}",
            what=what,
            reason="these team names cannot become lineup template fields:\n"
            + "\n".join(name_problems),
        )
        return False

    # ── Gate 4a: the lineup template against this season (038, FR-017/18) ─
    #
    # Season review holds the data that will actually be drawn, so a divergence here
    # refuses rather than warns (Constitution XIV.9). A warning would let a league
    # approve a season every lineup of which then falls back to text.
    lineup_problems = await approval_checks.lineup_problems(
        season.id,
        season_service=season_service,
        team_service=team_service,
        image_drawing=image_drawing,
    )
    if lineup_problems:
        bullet_list = "\n• ".join(lineup_problems)
        await refuse(
            interaction,
            f"❌ Season cannot be approved — the `lineup` image aspect is on but "
            f"the template cannot draw this season:\n• {bullet_list}",
            what=what,
            reason="the `lineup` image aspect is on but the template cannot draw this "
            "season:\n" + "\n".join(lineup_problems),
        )
        return False

    # ── Gate 4b: the image module's configuration ──────────────────
    #
    # Every check the configuration review makes of the image module, made again: the
    # rasteriser, every template a switched-on output draws, the per-tier colours and
    # the driver portrait settings. Read through `approval_checks.image_configuration_faults`, the
    # helper `/season placements-review` withholds its button on, so the report a
    # manager was given and the refusal they then meet cannot disagree.
    #
    # **The review's drawing is not evidence enough on its own.** It draws the lineup
    # and the calendar and nothing else, so a broken results or weather template, or a
    # rasteriser missing with both of those off, meets no render before the season
    # first tries to post it.
    if await module_service.is_images_enabled():
        image_faults = await approval_checks.image_configuration_faults(image_drawing)
        if image_faults:
            bullet_list = "\n• ".join(image_faults)
            await refuse(
                interaction,
                f"❌ Season cannot be approved — the image module is not correctly "
                f"configured:\n• {bullet_list}",
                what=what,
                reason="the image module is not correctly configured:\n"
                + "\n".join(image_faults),
            )
            return False

    # The graphics are **not** drawn here. `/season
    # placements-review` draws the lineup and the calendar of every division, and the
    # button that reaches this code refuses unless the season still fingerprints as the
    # one that review described, its template and artwork files among what that
    # fingerprint covers. The review's render is therefore evidence for this approval,
    # and repeating it would be one full rasterisation per division per aspect for an
    # answer already in hand.

    # A second approval of the season, from this review or another, while the first is
    # waiting, running or stopped on the queue: refused before the backup question, naming
    # no job.
    if await approval_in_hand(db_path, season.id):
        await refuse(interaction, ALREADY_BEING_APPROVED, what=what)
        return False

    # ── The last thing before anything is committed: a backup, under test mode ──
    #
    # Approving a season is the point a test run becomes hard to repeat — the schedule
    # is armed, the roles are granted, the lineups and calendars are posted — and
    # getting back to the moment before it meant building the season again. So under
    # test mode the approval pauses here and offers to save the databases first.
    #
    # Placed after every gate and before every write, which is the only moment a
    # backup is worth taking: earlier and it saves a season that turns out to be
    # unapprovable, later and it saves one already committed.
    if deadline is not None and not await offer_backup_before_approving(
        interaction,
        deadline=deadline,
        config_service=config_service,
        db_path=db_path,
        change_queue=change_queue,
        scheduler_service=scheduler_service,
        output_router=output_router,
        now=now,
    ):
        return False

    if fingerprint is None:
        fingerprint = (await take_fingerprint(db_path, season.id, drawing=image_drawing)).areas
    # The review comes down ahead of the approval's own jobs, but only for an approval the
    # queue accepts: a request it refuses at once leaves the review standing (core
    # "Confirming placements"). So the two are asked in one save, the take-down first, and a
    # refused approval leaves no take-down behind.
    question = getattr(interaction.message, "id", None)
    approval = {
        "season_id": season.id,
        "season_number": season.season_number,
        "fingerprint": dict(fingerprint),
        "channel_id": interaction.channel_id,
    }
    if isinstance(question, int):
        change_id = await take_review_down_before(
            change_queue, db_path, question, APPROVAL_KIND, approval,
            interaction=interaction, what=what,
        )
    else:
        change_id = await change_queue.ask(
            APPROVAL_KIND, approval, interaction=interaction, what=what
        )
    # None where the queue's own check refused the approval and saved nothing.
    return change_id is not None
