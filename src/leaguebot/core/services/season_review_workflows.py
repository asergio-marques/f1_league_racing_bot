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

The check of the change a workflow asks is the same gates again, judged as the change runs, so the
refusal at the press and the refusal at the run cannot disagree.
"""
from __future__ import annotations

import discord

from leaguebot.core.models.season import SeasonStage
from leaguebot.core.services import approval_checks
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
from leaguebot.core.services.season_fingerprint_service import take_fingerprint
from leaguebot.core.services.season_review_changes import take_review_down_before
from leaguebot.core.services.season_service import SeasonService
from leaguebot.core.services.team_service import TeamService
from leaguebot.core.utils.log_lines import refuse

__all__ = ["confirm_configuration", "confirm_mid_season_placements", "review_button"]


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
