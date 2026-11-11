"""Confirming a season's configuration, carried out on the change queue.

✅ Confirm configuration on `/season config-review` gates the press
(`season_review_workflows.confirm_configuration`) and asks the queue for this change, the review
taken down ahead of it, as ✅ Confirm placements does mid-season (`mid_season_confirm_change`).
The stage, the modules' kept settings and the confirmation's own record are one save, so a fault
leaves the season in Configuration with nothing kept and nothing recorded. This module holds the change; the workflow holds the press.

**The check, at the press and again when the change runs** (`check`), refuses in this order, the
first that fails being the refusal: another confirmation of the season in hand, queued, running or
stopped, **naming no job**; the season no longer in Configuration; the season changed since the
review (the review's own fingerprint); then every fault the review withholds its button on
(`approval_checks.configuration_faults`, the one reading of them, which the review command and the
confirmation's workflow call too). The press keeps the same
refusals in its own words, so a refusal there answers the member at once; the check is what stands
between a season changed while the confirmation waited on the queue and its confirmation. It
imports no module and no cog; the builder hands it the services the faults read, and `ctx.bot` is
used for the server alone.

**The stage, signup's settings, the audit entry and the line are one job** (`apply`,
architecture.md, "All or nothing in one step"): saved together or not at all, reading nothing but
the connection it is handed, so a fault leaves the season in Configuration with nothing kept and
the queue stops at it to retry. The season moves to Waiting where the signup module is on and test
mode is off, test mode never opening a signup window, and to Placements otherwise; the
"configuration confirmed" hook then hands each subscriber the save's connection, the season and the
queue's clock, each checking its own module's switch, so that signup keeps its settings for the
season with the stage that fixes them. A retry judges the confirmation again before it saves
(`judge`), the configuration being open meanwhile.

**A discarded save leaves the season in Configuration**, and the manager reviews it again.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

import aiosqlite

from leaguebot.core.db.database import get_connection
from leaguebot.core.models.change import (
    AuditRecord,
    ChangeOrigin,
    PlannedStep,
    StepKind,
    StepResult,
    Verdict,
    VerdictKind,
)
from leaguebot.core.models.season import SeasonStage
from leaguebot.core.services.seasons import approval_checks
from leaguebot.core.services.seasons.approval_checks import SignupModuleSettings
from leaguebot.core.services.queue.queue_records import (
    unfinished,
)
from leaguebot.core.services.queue.change_types import (
    ChangeType,
    CheckContext,
    OutcomeContext,
    Step,
    StepContext,
    StepView,
)
from leaguebot.core.services.config_service import ConfigService
from leaguebot.core.services.modules.image_drawing import ImageDrawing
from leaguebot.core.services.modules.module_service import ModuleService
from leaguebot.core.services.seasons.season_fingerprint_service import SeasonFingerprint, take_fingerprint
from leaguebot.core.services.seasons.season_lifecycle_service import confirm_configuration_on
from leaguebot.core.services.drivers.team_service import TeamService
from leaguebot.core.utils.hooks import AwaitedHook
from leaguebot.core.utils.league_server import league_guild
from leaguebot.core.utils.log_lines import refusal_line, reply_reason

__all__ = [
    "ALREADY_BEING_CONFIRMED",
    "BUTTON",
    "KIND",
    "NOT_CONFIRMED",
    "STAGE_REFUSAL",
    "confirmation_in_hand",
    "configuration_confirm_change",
]

KIND = "season.configuration.confirm"

ALREADY_BEING_CONFIRMED = (
    "⏳ This season's configuration is already being confirmed, so this press confirms nothing. "
    "If that confirmation has stopped, a league manager or admin can press Retry, or a league "
    "admin Discard, on its notice in the log channel."
)
STAGE_REFUSAL = "⛔ The season is no longer in configuration. **Nothing has been confirmed.**"
NOT_CONFIRMED = (
    "Nothing has been confirmed: the season is still in configuration. Run `/season "
    "config-review` again."
)

#: The job names, as the stop notice, the tests and `StepView` know them.
APPLY = "apply"
JUDGE = "judge"

#: The button as the log channel names it.
BUTTON = "the ✅ Confirm configuration button of `/season config-review`"


async def confirmation_in_hand(
    db_path: str, season_id: int, *, excluding: int | None = None
) -> bool:
    """Whether a confirmation of *season_id*'s configuration is queued, running or stopped.

    No job number is wanted: a second press is refused without naming the job. *excluding*
    leaves out the change with that id, so that the confirmation's own check does not find
    itself. Read through core's `unfinished`.
    """
    return any(
        payload.get("season_id") == season_id
        for payload in await unfinished(db_path, [KIND], excluding=excluding)
    )


def configuration_confirm_change(
    *,
    drawing: ImageDrawing,
    modules: ModuleService,
    signup_module: SignupModuleSettings,
    configs: ConfigService,
    teams: TeamService,
    configuration_confirmed: AwaitedHook,
    confirmation_refused: AwaitedHook,
    fingerprint_areas_read: AwaitedHook,
    now: Callable[[], datetime],
) -> ChangeType:
    """The change that confirms a season's configuration; see the module.

    The builder hands in the image module's own drawing (*drawing*, which the fingerprint reads and
    the faults read), the services the faults read (*modules*, *signup_module*, *configs* and
    *teams*), the *configuration_confirmed* hook each module's part is kept through, called inside
    the save on its connection, the *confirmation_refused* hook the modules' own faults are
    read through, the *fingerprint_areas_read* hook each module's own areas of the fingerprint
    are asked through, and *now* (the queue's clock).
    """

    async def check(ctx: CheckContext) -> Verdict:
        season_id = int(ctx.payload["season_id"])
        if await confirmation_in_hand(ctx.db_path, season_id, excluding=ctx.change_id):
            return Verdict.refuse(ALREADY_BEING_CONFIRMED)

        async with get_connection(ctx.db_path) as db:
            cursor = await db.execute("SELECT stage FROM seasons WHERE id = ?", (season_id,))
            season = await cursor.fetchone()
        if season is None or season["stage"] != SeasonStage.CONFIGURATION.value:
            return Verdict.refuse(STAGE_REFUSAL)

        # The report read is the report confirmed: the press compares the review's fingerprint,
        # and so does the run, since the season may have changed while it waited.
        current = await take_fingerprint(
            ctx.db_path,
            season_id,
            drawing=drawing,
            module_service=modules,
            fingerprint_areas_read=fingerprint_areas_read,
        )
        changed = SeasonFingerprint(areas=dict(ctx.payload["fingerprint"])).differs_from(current)
        if changed:
            bullets = "\n".join(f"• {area}" for area in changed)
            return Verdict.refuse(
                f"⛔ The season has changed since this review:\n{bullets}\n"
                f"Run `/season config-review` again and confirm from the fresh report. "
                f"**Nothing has been confirmed.**",
                f"the season has changed since this review:\n{bullets}",
            )

        found = await approval_checks.configuration_faults(
            season_id,
            await league_guild(ctx.bot),
            module_service=modules,
            signup_module_service=signup_module,
            config_service=configs,
            team_service=teams,
            confirmation_refused=confirmation_refused,
            image_drawing=drawing,
        )
        if found:
            bullets = "\n".join(f"• {fault}" for fault in found)
            return Verdict.refuse(
                f"⛔ The configuration cannot be confirmed:\n{bullets}\n"
                "**Nothing has been confirmed.**",
                "the configuration cannot be confirmed:\n" + "\n".join(found),
            )
        return Verdict.go()

    async def judge(ctx: StepContext) -> StepResult:
        """Judge the confirmation again, as `check` does, and say whether it still stands."""
        verdict = await check(
            CheckContext(ctx.payload, ctx.bot, ctx.db_path, ChangeOrigin.MEMBER, ctx.change_id)
        )
        if verdict.kind is VerdictKind.GO:
            return StepResult(result={"judged": True})
        return StepResult(result={"judged": False, "refused": verdict.reply})

    async def apply(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        """The one save; see the module.

        The queue judges the confirmation (`check`) only before it starts, and the save reads
        nothing but its own connection, so a save stopped and tried again would otherwise confirm
        the season as it then stands. A try after a failure therefore saves nothing: it plans
        `judge`, then this save once more, and the new save reads the judgement, refusing where it
        found the confirmation no longer stands, and saving nothing where a league admin discarded
        the judgement.
        """
        if ctx.tries > 0:
            return StepResult(
                result={"rejudge": True}, then=(PlannedStep(JUDGE), PlannedStep(APPLY))
            )
        judged = next(
            (view for view in reversed(ctx.steps) if view.name == JUDGE and view.done), None
        )
        judgement = (judged.result or {}) if judged is not None else {}
        if judgement.get("refused"):
            return _refused(ctx, str(judgement["refused"]))
        if judged is not None and not judgement.get("judged"):
            # The judgement was discarded, so nothing found the confirmation standing: it is
            # saved as nothing, the season staying in Configuration.
            return StepResult(result={"unjudged": True})
        season_id = int(ctx.payload["season_id"])
        season_number = int(ctx.payload["season_number"])
        cursor = await db.execute("SELECT stage FROM seasons WHERE id = ?", (season_id,))
        season = await cursor.fetchone()
        if season is None or season["stage"] != SeasonStage.CONFIGURATION.value:
            # A backstop: the check passed, and something wrote the database after it.
            return _refused(ctx, STAGE_REFUSAL)

        cursor = await db.execute("SELECT test_mode_active FROM server_configs")
        server = await cursor.fetchone()
        signup_on = await modules.is_enabled_on(db, "signup")
        test_mode = server is not None and bool(server["test_mode_active"])
        target = SeasonStage.WAITING if signup_on and not test_mode else SeasonStage.PLACEMENTS
        await confirm_configuration_on(db, season_id, target)
        # Every subscriber is called, each checking its own module's switch on *db*.
        await configuration_confirmed.call(db, season_id, now())

        audit = AuditRecord(
            "CONFIGURATION_CONFIRMED",
            {"season_id": season_id, "stage": SeasonStage.CONFIGURATION.value},
            {"season_id": season_id, "season_number": season_number, "stage": target.value},
        )
        line = (
            f"{ctx.named} | /season config-review | Confirmed\n"
            f"  season: Season #{season_number}\n"
            f"  stage: {target.value}"
        )
        return StepResult(
            result={"season_id": season_id, "season_number": season_number, "stage": target.value},
            audits=(audit,),
            lines=(line,),
        )

    def _refused(ctx: StepContext, reply: str) -> StepResult:
        """The save's result where the confirmation was found no longer to stand, and the line
        that records the refusal."""
        return StepResult(
            result={"refused": reply},
            lines=(refusal_line(ctx.named, BUTTON, reply_reason(reply)),),
        )

    async def describe_apply(ctx: StepContext) -> str:
        return f"confirming the configuration of season {ctx.payload['season_number']}"

    async def describe_judge(ctx: StepContext) -> str:
        return (
            f"judging again whether season {ctx.payload['season_number']}'s configuration can "
            "still be confirmed"
        )

    def outcome(ctx: OutcomeContext) -> str:
        view = _view(ctx, APPLY)
        result = (view.result or {}) if view is not None else {}
        if result.get("refused"):
            return str(result["refused"])
        if view is None or not view.done or "discarded" in result or result.get("unjudged"):
            return NOT_CONFIRMED
        if result["stage"] == SeasonStage.WAITING.value:
            next_step = "The season now waits for its signup window — open it with `/signup open`."
        else:
            next_step = (
                "The season is now in placements — build its divisions and calendar, place "
                "its drivers, then run `/season placements-review`."
            )
        return f"✅ Season #{result['season_number']}'s configuration is confirmed. {next_step}"

    def doing(payload: dict[str, Any]) -> str:
        return f"Confirming season {payload['season_number']}'s configuration"

    return ChangeType(
        kind=KIND,
        opening=(PlannedStep(APPLY),),
        steps={
            APPLY: Step(APPLY, StepKind.SAVE, apply, describe=describe_apply),
            JUDGE: Step(JUDGE, StepKind.ACT, judge, describe=describe_judge),
        },
        check=check,
        key=lambda payload: f"{KIND}:{payload['season_id']}",
        doing=doing,
        outcome=outcome,
    )


def _view(ctx: OutcomeContext, name: str) -> StepView | None:
    """The last job named *name*: the save, tried again, is planned afresh, and the last stands."""
    return next((view for view in reversed(ctx.steps) if view.name == name), None)
