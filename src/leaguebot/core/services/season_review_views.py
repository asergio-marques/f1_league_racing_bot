"""The review's buttons: the Approve of `/season placements-review`, the Confirm placements of a
mid-season review and the Confirm configuration of `/season config-review`.

A review's question carries one of these views, and a press is the whole of what it does: who
may press, whether the review still stands and has its five minutes, the claim on the button,
whether the season has changed since the report, and then the workflow of
`season_review_workflows`. Each view is handed the services it reads, by keyword and named as the
bot carries them, with the queue's clock (`now`) that the five minutes are judged on; it holds no
cog and looks nothing up on the bot. The entry point's factory builds them for the review's post
change and for the restart sweep (`recover_expired_review_prompts`), as `queue_stop_view` is built
for the stop notice.

The views are persistent with fixed custom ids, so that a question posted before a restart is
routed again by the id of its message: the review itself is its row in `season_review_prompts`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime

import discord

from leaguebot.core.services import season_fingerprint_service, season_review_workflows
from leaguebot.core.services.approval_checks import SignupModuleSettings
from leaguebot.core.services.change_queue import ChangeQueue
from leaguebot.core.services.config_service import ConfigService
from leaguebot.core.services.configuration_confirm_change import (
    ALREADY_BEING_CONFIRMED as ALREADY_CONFIGURATION_BEING_CONFIRMED,
)
from leaguebot.core.services.configuration_confirm_change import (
    confirmation_in_hand as configuration_confirmation_in_hand,
)
from leaguebot.core.services.image_drawing import ImageDrawing
from leaguebot.core.services.mid_season_confirm_change import (
    ALREADY_BEING_CONFIRMED,
    confirmation_in_hand,
)
from leaguebot.core.services.module_service import ModuleService
from leaguebot.core.services.output_router import OutputRouter
from leaguebot.core.services.scheduler_service import SchedulerService
from leaguebot.core.services.season_approval_change import (
    ALREADY_BEING_APPROVED,
    approval_in_hand,
)
from leaguebot.core.services.season_review_changes import (
    EXPIRED,
    FAILED,
    REFUSED,
    TAKING_DOWN,
    TIMER_FIRED,
    UNDER_WAY,
    StandingReview,
    mark_press,
    standing_review,
    take_review_down,
)
from leaguebot.core.services.season_review_workflows import review_button
from leaguebot.core.services.season_service import SeasonService
from leaguebot.core.services.team_service import TeamService
from leaguebot.core.utils.channel_guard import is_league_admin
from leaguebot.core.utils.hooks import AwaitedHook
from leaguebot.core.utils.league_server import LeagueView
from leaguebot.core.utils.log_lines import refuse

__all__ = ["ApproveView", "ConfirmConfigurationView", "ConfirmMidSeasonPlacementsView"]


class ApproveView(LeagueView):
    """The standing question at the end of a review, and the button that answers it.

    **Who may press.** The member who ran the review, or a server administrator. A league
    manager holding only the interaction role may run `/season placements-review` — that is what the
    report is for — but approving somebody else's review is not theirs to do. The message
    is public, so this check is what stops a bystander answering it; the refusal is
    ephemeral, so only the presser reads it.

    **The review is its row.** The view holds nothing of the review in memory: a press reads the
    question's row in `season_review_prompts` (when it was posted, the evidence its report was
    computed from, what became of the last press), by the id of the message it is on. So the
    button is the same after a restart, or behind a stopped queue, as on the minute it was
    posted: it is routed again by id (``timeout=None`` and fixed custom ids), and a press made
    once the five minutes have run is refused and recorded whether or not the take-down has yet
    run. The view deletes and posts nothing itself: a review that ends is taken down by the
    queue (`season.review.take_down`), which deletes it, tells the reviewer and records the lapse.

    **How it ends.** The five minutes are a timer armed when the question appeared
    (`review_expiry`), whose callback asks the take-down. A press that goes through asks it
    first, ahead of the approval's own work, so the review is gone before the approval's first
    job (the core specification's "Confirming placements"); one refused for a changed season, or
    found after the five minutes, asks it too.

    **Five minutes from posting.** The button stands five minutes from the posting of the review,
    whatever is pressed meanwhile: the row's ``posted_at`` is the deadline's start, and no press,
    a refused one included, moves it.

    **A press under way is not expired under it.** A press claims the row (``UNDER_WAY``). The
    timer firing meanwhile marks it and waits: a press that finishes has taken the review down,
    or, refused or cancelled, ends it as expired once the five minutes have run; one that raised
    leaves the review to expire then, its lapse saying an earlier press failed and may have been
    partly done. A second press while one is being worked is refused, so a double-click cannot
    approve twice.
    """

    #: The command whose report this button answers, named when the review expires.
    _review_command = "/season placements-review"
    #: The button's label without its mark, as a refusal of a press names it.
    _button_label = "Approve"
    #: What a review's reply says has not happened, when it lapses unanswered.
    _verb = "approved"

    @property
    def _button(self) -> str:
        """This view's button, named for the log channel."""
        return review_button(self._button_label, self._review_command)

    def __init__(
        self,
        reviewer_id: int,
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
        confirmation_refused: AwaitedHook,
        driver_names_read: AwaitedHook,
        now: Callable[[], datetime],
    ) -> None:
        super().__init__(timeout=None)
        self._reviewer_id = reviewer_id
        self._season_service = season_service
        self._module_service = module_service
        self._signup_module_service = signup_module_service
        self._config_service = config_service
        self._team_service = team_service
        self._db_path = db_path
        self._image_drawing = image_drawing
        self._change_queue = change_queue
        self._scheduler_service = scheduler_service
        self._output_router = output_router
        self._round_windows_read = round_windows_read
        self._confirmation_refused = confirmation_refused
        self._driver_names_read = driver_names_read
        self._now = now
        # The review the last press read from its row. A view serves one question, and a second
        # press is refused (`_refuse_if_under_way`) before it reads, so this is the row of the one
        # press being worked; nothing of the review is kept between presses.
        self._review: StandingReview | None = None

    async def _standing(self, interaction: discord.Interaction) -> StandingReview | None:
        """The review the pressed question belongs to, read from its row; refused, and recorded,
        where it stands no longer."""
        message = interaction.message
        review = (
            await standing_review(self._db_path, message.id) if message is not None else None
        )
        if review is None:
            await refuse(
                interaction,
                "\u26d4 This review no longer stands, so it can no longer be answered. "
                f"**Nothing has been {self._verb}.** Run `{self._review_command}` again.",
                what=self._button,
                reason="the review no longer stands",
            )
        self._review = review
        return review

    async def _refuse_if_expired(
        self, interaction: discord.Interaction, review: StandingReview
    ) -> bool:
        """Refuse a press made once the review's five minutes are up, and take the review down,
        unless an earlier press is under way (or the timer waits for it), which ends it.

        The timer normally takes it down first; a press can still reach the button before the
        take-down has run, or behind a stopped queue. It confirms nothing, and the refusal is what
        the log records.
        """
        if self._now() < review.deadline:
            return False
        await refuse(
            interaction,
            "\u26d4 This review's five minutes are up, so it can no longer be answered. "
            f"**Nothing has been {self._verb}.** Run `{self._review_command}` again.",
            what=self._button,
            reason="the review's five minutes were up",
        )
        if review.press in (UNDER_WAY, TIMER_FIRED):
            # An earlier press is being worked, or the timer has fired and waits for it: that
            # press settles how the review ends (`_press_worked`). Taking it down as refused here
            # would let its approval through with the review gone as expired.
            return True
        await take_review_down(self._change_queue, self._db_path,
                               review.question_id, REFUSED)
        return True

    async def _refuse_if_under_way(
        self, interaction: discord.Interaction, review: StandingReview
    ) -> bool:
        """Claim the button for this press, or refuse it where another press of the review is
        being worked (or the review is being taken down).

        A double-click would otherwise run a second approval (or confirmation) beside the
        first until the first commits. The second confirms nothing, and is recorded.
        """
        if mark_press(review.question_id, UNDER_WAY, only_from=(None, FAILED)):
            return False
        await refuse(
            interaction,
            "\u26d4 This review is already being answered by another press. "
            f"**Nothing has been {self._verb} by this one.**",
            what=self._button,
            reason="another press of the review was being worked",
        )
        return True

    @asynccontextmanager
    async def _press_worked(self, review: StandingReview) -> AsyncIterator["_Press"]:
        """Around a press whose button it has claimed: what the press leaves is settled on the review's mark.

        A press that went through has taken the review down (its own work asks that first). One
        refused or cancelled leaves it standing, to be pressed again within its five minutes,
        unless those ran out meanwhile or the season has changed since the review, for which it
        ends as an expired one does, the press having recorded its own outcome. A press refused
        because an approval is in hand says nothing of the season, but where the review's timer
        fired while it was worked, the review is taken down as expired all the same: the timer
        left that to the press, and nothing else would take it down. One that raised
        leaves the review up, marked, and, where the five minutes ran out meanwhile, takes it down
        now, its lapse saying an earlier press failed and may have been partly done.
        """
        press = _Press()
        try:
            yield press
        except Exception:
            current = await standing_review(self._db_path, review.question_id)
            if current is not None and current.press != TAKING_DOWN:
                waiting = current.press == TIMER_FIRED
                mark_press(review.question_id, FAILED)
                if waiting:
                    await take_review_down(self._change_queue, self._db_path, review.question_id, EXPIRED)
            raise
        current = await standing_review(self._db_path, review.question_id)
        if current is None or current.press == TAKING_DOWN:
            return
        if press.went_through:
            # The confirmation asked the review's take-down itself, ahead of its own work.
            return
        if press.leave_standing and current.press != TIMER_FIRED:
            mark_press(review.question_id, None)
        elif current.press == TIMER_FIRED or self._now() >= current.deadline:
            await take_review_down(self._change_queue, self._db_path, review.question_id, EXPIRED)
        elif await self._changed_since_review():
            await take_review_down(self._change_queue, self._db_path, review.question_id, REFUSED)
        else:
            mark_press(review.question_id, None)

    async def _changed_since_review(self) -> list[str]:
        """The areas of the season changed since the review the press read described it, named as a
        league reads them; none where the review recorded no evidence."""
        review = self._review
        if review is None or review.evidence is None:
            return []
        current = await season_fingerprint_service.take_fingerprint(
            self._db_path, review.season_id, drawing=self._image_drawing
        )
        return season_fingerprint_service.SeasonFingerprint(review.evidence).differs_from(current)

    async def _may_approve(self, interaction: discord.Interaction) -> bool:
        """The reviewer, or a league admin.

        Widening who may review and narrowing who may approve is the whole point of the
        split: the review is a league manager's, the approval a league admin's.

        The tier is a property of the league, so the role is what it reads — and reading it
        through `is_league_admin` is what keeps this button and the league admin commands
        answering the same question.
        """
        member = interaction.user
        if getattr(member, "id", None) == self._reviewer_id:
            return True
        if not isinstance(member, discord.Member):
            return False
        config = await self._config_service.get_server_config()
        if config is None:
            return False
        return is_league_admin(config, member)

    @discord.ui.button(
        label="✅ Approve", style=discord.ButtonStyle.success, custom_id="season_review:approve"
    )
    async def approve(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        """Ask the change queue to approve the season, the review taken down ahead of it.

        A press refused at the press by the approval's own gates (a date gone by, a channel, the
        rasteriser and the rest) or by the queue's check of it, or cancelled at the test-mode
        backup question, leaves the review and its button standing, to be pressed again within
        its five minutes, as a press that fails on a fault does (the core specification's
        "Confirming placements"). One refused because the season has changed since the review
        ends it as an expired one does. Only a press whose approval the queue accepts takes the
        review down, which the approval itself asks for, just before it asks for the approval.
        """
        # Checked first, the fingerprint included: the message is public, so anyone who can
        # read the channel can press this. Nothing is read and nothing is approved for a
        # member who may not approve.
        if not await self._may_approve(interaction):
            await refuse(
                interaction,
                "⛔ Only the person who ran this review, or a league admin, "
                "can approve it. **Nothing has been approved.**",
                what=self._button,
            )
            return

        review = await self._standing(interaction)
        if review is None:
            return
        if await self._refuse_if_expired(interaction, review):
            return
        if await self._refuse_if_under_way(interaction, review):
            return
        async with self._press_worked(review) as press:
            # A second approval of the season, from this review or another, is refused at once,
            # naming no job, before the evidence is read: a season whose first approval has
            # saved and is stopped at a post no longer fingerprints as it did, and the press is
            # told what is true of it.
            if await approval_in_hand(self._db_path, review.season_id):
                await refuse(interaction, ALREADY_BEING_APPROVED, what=self._button)
                press.leave_standing = True
                return

            # The report you read is the report you approve. The five-minute life makes a change
            # unlikely; this makes one detectable — and it is what lets the approval trust the
            # review's own render rather than drawing everything a second time.
            changed = await self._changed_since_review()
            if changed:
                bullets = "\n".join(f"• {area}" for area in changed)
                await refuse(
                    interaction,
                    f"⛔ Your season has changed since this review, so the report above "
                    f"no longer describes it:\n{bullets}\n"
                    f"Run `/season placements-review` again and approve from the fresh report. "
                    f"**Nothing has been approved.**",
                    what=self._button,
                    reason=f"the season has changed since this review:\n{bullets}",
                )
                await take_review_down(self._change_queue, self._db_path,
                                       review.question_id, REFUSED)
                return

            press.went_through = await season_review_workflows.approve_season(
                interaction,
                season_service=self._season_service,
                module_service=self._module_service,
                signup_module_service=self._signup_module_service,
                config_service=self._config_service,
                team_service=self._team_service,
                db_path=self._db_path,
                image_drawing=self._image_drawing,
                change_queue=self._change_queue,
                scheduler_service=self._scheduler_service,
                output_router=self._output_router,
                round_windows_read=self._round_windows_read,
                confirmation_refused=self._confirmation_refused,
                driver_names_read=self._driver_names_read,
                now=self._now,
                deadline=review.deadline,
                what=self._button,
                fingerprint=review.evidence,
            )

    async def _confirm_press(
        self,
        interaction: discord.Interaction,
        confirm: Callable[..., Awaitable[bool]],
        *,
        in_hand: tuple[Callable[[str, int], Awaitable[bool]], str] | None = None,
        evidence_to_confirm: bool = False,
    ) -> None:
        """A confirming button's press, every one but the placements approval's alike: who may
        press, the five minutes, the claim, the evidence, then *confirm*, the confirmation's own
        work, which asks the queue for the review's take-down ahead of the confirmation.

        *in_hand* is a reader of whether a confirmation of the season is already waiting,
        running or stopped on the change queue, and the refusal it is answered with: such a
        press is refused at once, naming no job, before the evidence is read, and leaves the
        review standing, as the approval's does. *evidence_to_confirm* hands the review's
        own evidence to *confirm* as its `fingerprint`, for a confirmation the queue judges
        again when it runs."""
        if not await self._may_approve(interaction):
            await refuse(
                interaction,
                "⛔ Only the person who ran this review, or a league admin, can confirm "
                "it. **Nothing has been confirmed.**",
                what=self._button,
            )
            return
        review = await self._standing(interaction)
        if review is None:
            return
        if await self._refuse_if_expired(interaction, review):
            return
        if await self._refuse_if_under_way(interaction, review):
            return
        async with self._press_worked(review) as press:
            if in_hand is not None:
                reader, refusal = in_hand
                if await reader(self._db_path, review.season_id):
                    await refuse(interaction, refusal, what=self._button)
                    press.leave_standing = True
                    return
            changed = await self._changed_since_review()
            if changed:
                bullets = "\n".join(f"• {area}" for area in changed)
                await refuse(
                    interaction,
                    f"⛔ The season has changed since this review:\n{bullets}\n"
                    f"Run `{self._review_command}` again and confirm from the fresh "
                    f"report. **Nothing has been confirmed.**",
                    what=self._button,
                    reason=f"the season has changed since this review:\n{bullets}",
                )
                await take_review_down(self._change_queue, self._db_path,
                                       review.question_id, REFUSED)
                return
            if evidence_to_confirm:
                press.went_through = await confirm(
                    interaction, what=self._button, fingerprint=review.evidence
                )
            else:
                press.went_through = await confirm(interaction, what=self._button)


@dataclass
class _Press:
    """What one press of a review's button left, for `ApproveView._press_worked` to settle."""

    #: The approval (or confirmation) went through: the review is taken down.
    went_through: bool = False
    #: The press was refused before the season was judged, and says nothing of the review's end.
    leave_standing: bool = False



class ConfirmMidSeasonPlacementsView(ApproveView):
    """The confirm button of `/season placements-review` in Ongoing, placements.

    Governed as every review button is; pressing it commits the new placements.
    """

    _review_command = "/season placements-review"
    _button_label = "Confirm placements"
    _verb = "confirmed"

    async def _confirm_placements(
        self, interaction: discord.Interaction, *, what: str, fingerprint: dict | None = None
    ) -> bool:
        """The mid-season placements' confirmation, handed the services the view holds."""
        return await season_review_workflows.confirm_mid_season_placements(
            interaction,
            season_service=self._season_service,
            module_service=self._module_service,
            db_path=self._db_path,
            image_drawing=self._image_drawing,
            change_queue=self._change_queue,
            driver_names_read=self._driver_names_read,
            what=what,
            fingerprint=fingerprint,
        )

    @discord.ui.button(
        label="✅ Confirm placements", style=discord.ButtonStyle.success,
        custom_id="season_review:confirm_placements",
    )
    async def approve(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._confirm_press(
            interaction,
            self._confirm_placements,
            in_hand=(confirmation_in_hand, ALREADY_BEING_CONFIRMED),
            evidence_to_confirm=True,
        )


class ConfirmConfigurationView(ApproveView):
    """The confirm button of `/season config-review`.

    Governed exactly as the placements review's approve button is — who may press it, how
    long it stands, the evidence it is pressed upon — and differing only in what pressing
    it does and what it says.
    """

    _review_command = "/season config-review"
    _button_label = "Confirm configuration"
    _verb = "confirmed"

    async def _confirm_configuration(
        self, interaction: discord.Interaction, *, what: str, fingerprint: dict | None = None
    ) -> bool:
        """The configuration's confirmation, handed the services the view holds."""
        return await season_review_workflows.confirm_configuration(
            interaction,
            season_service=self._season_service,
            module_service=self._module_service,
            signup_module_service=self._signup_module_service,
            config_service=self._config_service,
            team_service=self._team_service,
            db_path=self._db_path,
            image_drawing=self._image_drawing,
            change_queue=self._change_queue,
            confirmation_refused=self._confirmation_refused,
            what=what,
            fingerprint=fingerprint,
        )

    @discord.ui.button(
        label="✅ Confirm configuration", style=discord.ButtonStyle.success,
        custom_id="season_review:confirm_configuration",
    )
    async def approve(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._confirm_press(
            interaction,
            self._confirm_configuration,
            in_hand=(configuration_confirmation_in_hand, ALREADY_CONFIGURATION_BEING_CONFIRMED),
            evidence_to_confirm=True,
        )
