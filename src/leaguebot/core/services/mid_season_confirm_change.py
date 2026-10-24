"""Confirming a season's placements mid-season, carried out on the change queue (#439, slice 8).

✅ Confirm placements on a mid-season `/season placements-review` gates the press in the season
cog, acknowledges it at once and asks the queue for this change, the review taken down ahead of
it. It used to commit the placements, grant the roles, ask for the lineups and move the season back
to Ongoing in separate steps off the queue, with Discord's refusals swallowed, so a stop part-way
left drivers without roles and nobody told. This module holds the change; the cog holds the press.

**The check, at the press and again when the change runs** (`check`), refuses in this order, the
first that fails being the refusal: another confirmation of the season in hand, queued, running or
stopped, **naming no job**; the season no longer in Ongoing, placements; every division of the
season done, there being nothing left to place drivers into (the wind-down that moves such a season
on discards the placements not yet committed, so confirming them would be undone); the season
changed since the review (the review's own fingerprint); then the faults the review withholds its
button on: an unsettled signup, a channel a division posts to that is gone, and the host's drawing
program missing where images is on. The press keeps the same refusals in its own words and order,
so a refusal there answers the member at once; the check is what stands between a season changed
while the confirmation waited on the queue and its commitment. It imports no module and no cog, and
reaches a service only through what the builder hands it; `ctx.bot` is used for Discord alone.

**The placements and the season's return to Ongoing are one job** (`apply`, architecture.md, "All
or nothing in one step"): saved together or not at all, reading nothing but the connection it is
handed, so a fault leaves the season in Ongoing, placements with nothing written and the queue
stops at it to retry. A retry judges the confirmation again before it saves (`judge`), the setup
being open meanwhile. **Everything else follows the save as a job of its own** and stops the queue
where Discord refuses it: each new driver's roles (`grant_roles`, a member Discord reports absent
being passed over, one it cannot fetch for any other reason stopping the queue), each division's
lineup (`post_lineup`), the notice to the review's channel where the member's reply can no longer
be updated, and the one line that records the confirmation and what a league admin discarded.

**No job is undiscardable.** A discarded `apply` leaves the season in Ongoing, placements, and the
manager confirms again; the wind-down and `/season complete` still move a finished season on. Roles
and lineups are put right by hand, and nothing here can stop a season from being started or
completed.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

import aiosqlite

from leaguebot.core.db.database import get_connection
from leaguebot.core.models.change import (
    AuditRecord,
    ChangeOrigin,
    FollowOn,
    PlannedStep,
    StepKind,
    StepResult,
    Verdict,
    VerdictKind,
)
from leaguebot.core.services import approval_checks, lineup_posting, standing_posts
from leaguebot.core.services.change_queue import (
    ChangeType,
    CheckContext,
    DiscardedContext,
    OutcomeContext,
    RefusedContext,
    Step,
    StepContext,
    StepView,
    unfinished,
)
from leaguebot.core.services.channel_tell_change import KIND as CHANNEL_TELL
from leaguebot.core.services.season_approval_change import (
    GRANT_ROLES,
    driver_roles_step,
    tell_channel,
    ungranted_line,
    unposted_lineup_line,
)
from leaguebot.core.services.season_fingerprint_service import SeasonFingerprint, take_fingerprint
from leaguebot.core.services.season_lifecycle_service import confirm_placements_on
from leaguebot.core.services.placement_service import uncommitted_placements_on
from leaguebot.core.utils.league_server import league_guild
from leaguebot.core.utils.log_lines import refusal_line, reply_reason

__all__ = [
    "ALREADY_BEING_CONFIRMED",
    "KIND",
    "NOT_CONFIRMED",
    "STAGE_REFUSAL",
    "confirmation_in_hand",
    "every_division_done",
    "every_division_done_refusal",
    "mid_season_confirm_change",
]

KIND = "season.placements.confirm"

ALREADY_BEING_CONFIRMED = (
    "⏳ This season's placements are already being confirmed, so this press confirms nothing. "
    "If that confirmation has stopped, a league manager or admin can press Retry, or a league "
    "admin Discard, on its notice in the log channel."
)
STAGE_REFUSAL = "⛔ The season is no longer placing drivers. **Nothing has been confirmed.**"
NOT_CONFIRMED = (
    "Nothing has been confirmed: the season is still placing drivers. Run `/season "
    "placements-review` again."
)

#: The job names, as the stop notice, the tests and `StepView` know them.
APPLY = "apply"
JUDGE = "judge"
POST_LINEUP = lineup_posting.POST_LINEUP
TELL_REVIEW_CHANNEL = "tell_review_channel"
CLOSE = "close"

_BUTTON = "the ✅ Confirm placements button of `/season placements-review`"


def every_division_done_refusal(season_number: Any) -> str:
    """What a confirmation is refused with where every division of the season is done."""
    return (
        f"⛔ Every division of season {season_number} is done, so there is nothing left to place "
        "drivers into. Run `/season complete`, which winds the season down. **Nothing has been "
        "confirmed.**"
    )


def channel_confirmed(actor_id: int | None) -> str:
    """What the review's channel is told of a confirmation its member could not be told of."""
    return (
        f"✅ <@{actor_id}> — the placements are confirmed and the season is ongoing again. Your "
        f"confirmation could not be sent to you privately; the log channel has what it said."
    )


def channel_not_confirmed(actor_id: int | None, season_number: Any) -> str:
    """What the review's channel is told of a confirmation that did not go through, its member
    having no reply left to be told in."""
    return (
        f"⛔ <@{actor_id}> — Season #{season_number}'s placements were not confirmed. Your reply "
        f"could not be updated; the log channel has why. Run `/season placements-review` again."
    )


def channel_refused(actor_id: int | None, season_number: Any, reply: str) -> str:
    """What the review's channel is told of a confirmation refused as it came up to run, its
    member having no reply left to be told in: the refusal's own reply beneath a line naming
    them."""
    return (
        f"⛔ <@{actor_id}> — Season #{season_number}'s placements were not confirmed when their "
        f"turn came on the queue, and your reply could no longer be updated:\n{reply}"
    )


async def confirmation_in_hand(
    db_path: str, season_id: int, *, excluding: int | None = None
) -> bool:
    """Whether a confirmation of *season_id*'s placements is queued, running or stopped.

    No job number is wanted: a second press is refused without naming the job. *excluding*
    leaves out the change with that id, so that the confirmation's own check does not find
    itself. Read through core's `unfinished`.
    """
    return any(
        payload.get("season_id") == season_id
        for payload in await unfinished(db_path, [KIND], excluding=excluding)
    )


async def every_division_done(db: aiosqlite.Connection, season_id: int) -> bool:
    """Whether the season has divisions and every one is finished or cancelled."""
    cursor = await db.execute(
        "SELECT COUNT(*) AS divisions, "
        "COALESCE(SUM(status NOT IN ('FINISHED', 'CANCELLED')), 0) AS outstanding "
        "FROM divisions WHERE season_id = ?",
        (season_id,),
    )
    row = await cursor.fetchone()
    return row is not None and row["divisions"] > 0 and row["outstanding"] == 0


def mid_season_confirm_change(
    *,
    modules: Any,
    placement: Any,
    rasteriser_fault: Callable[[], str | None],
) -> ChangeType:
    """The change that confirms a season's placements mid-season; see the module.

    The builder hands in *modules* (which modules are on), the *placement* service (a driver's
    roles, a division's lineup) and *rasteriser_fault* (what is wrong with the host's drawing
    program, None where nothing is).
    """

    async def check(ctx: CheckContext) -> Verdict:
        season_id = int(ctx.payload["season_id"])
        if await confirmation_in_hand(ctx.db_path, season_id, excluding=ctx.change_id):
            return Verdict.refuse(ALREADY_BEING_CONFIRMED)

        async with get_connection(ctx.db_path) as db:
            cursor = await db.execute("SELECT stage FROM seasons WHERE id = ?", (season_id,))
            season = await cursor.fetchone()
            done = await every_division_done(db, season_id)
        if season is None or season["stage"] != "ONGOING_PLACEMENTS":
            return Verdict.refuse(STAGE_REFUSAL)
        if done:
            return Verdict.refuse(every_division_done_refusal(ctx.payload["season_number"]))

        # The report read is the report confirmed: the press compares the review's fingerprint,
        # and so does the run, since the season may have changed while it waited.
        current = await take_fingerprint(ctx.bot, season_id)
        changed = SeasonFingerprint(areas=dict(ctx.payload["fingerprint"])).differs_from(current)
        if changed:
            bullets = "\n".join(f"• {area}" for area in changed)
            return Verdict.refuse(
                f"⛔ The season has changed since this review:\n{bullets}\n"
                f"Run `/season placements-review` again and confirm from the fresh report. "
                f"**Nothing has been confirmed.**",
                f"the season has changed since this review:\n{bullets}",
            )

        unsettled = await approval_checks.unsettled_signups(ctx.db_path)
        guild = await league_guild(ctx.bot)
        channels = await approval_checks.division_channel_faults(
            modules, ctx.db_path, season_id, guild
        )
        faults = [*(f"Unsettled signup: {line}" for line in unsettled), *channels]
        if await modules.is_images_enabled():
            fault = rasteriser_fault()
            if fault:
                faults.append(fault)
        if faults:
            bullets = "\n".join(f"• {line}" for line in faults)
            return Verdict.refuse(
                f"⛔ Placements cannot be confirmed:\n{bullets}\n**Nothing has been confirmed.**",
                "placements cannot be confirmed:\n" + "\n".join(faults),
            )
        return Verdict.go()

    # ── The jobs ────────────────────────────────────────────────────────────────

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
        nothing but its own connection, so a save stopped and tried again would otherwise
        commit the season as it then stands. A try after a failure therefore saves nothing: it
        plans `judge`, then this save once more, and the new save reads the judgement, refusing
        where it found the confirmation no longer stands, and saving nothing where a league
        admin discarded the judgement.
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
            return StepResult(result={"refused": str(judgement["refused"])})
        if judged is not None and not judgement.get("judged"):
            # The judgement was discarded, so nothing found the confirmation standing: it is
            # saved as nothing, the season staying in placements.
            return StepResult(result={"unjudged": True})
        season_id = int(ctx.payload["season_id"])
        season_number = int(ctx.payload["season_number"])
        cursor = await db.execute("SELECT stage FROM seasons WHERE id = ?", (season_id,))
        season = await cursor.fetchone()
        if season is None or season["stage"] != "ONGOING_PLACEMENTS":
            # A backstop: the check passed, and something wrote the database after it.
            return StepResult(result={"refused": STAGE_REFUSAL})

        placements = await uncommitted_placements_on(db, season_id)
        confirmed = await confirm_placements_on(db, season_id)

        cursor = await db.execute(
            "SELECT id, name FROM divisions WHERE season_id = ? ORDER BY tier, id", (season_id,)
        )
        divisions = [(int(row["id"]), str(row["name"])) for row in await cursor.fetchall()]
        steps: list[PlannedStep] = []
        for division_id, division_name in divisions:
            drivers = sorted(
                (p for p in placements
                 if int(p["division_id"]) == division_id and not p["is_test_driver"]),
                key=lambda p: int(p["discord_user_id"]),
            )
            steps.extend(
                PlannedStep(
                    GRANT_ROLES,
                    {
                        "user_id": int(driver["discord_user_id"]),
                        "division_id": division_id,
                        "division_name": division_name,
                        "team_name": driver["team_name"],
                    },
                )
                for driver in drivers
            )
        touched = {int(p["division_id"]) for p in placements}
        steps.extend(
            PlannedStep(POST_LINEUP, {"division_id": division_id, "division_name": division_name})
            for division_id, division_name in divisions
            if division_id in touched
        )

        audit = AuditRecord(
            "PLACEMENTS_CONFIRMED",
            {"season_id": season_id, "stage": "ONGOING_PLACEMENTS"},
            {
                "season_id": season_id,
                "season_number": season_number,
                "stage": "ONGOING",
                "placements_confirmed": confirmed.committed,
            },
        )
        return StepResult(
            result={
                "season_id": season_id,
                "season_number": season_number,
                "placements": confirmed.committed,
            },
            audits=(audit,),
            then=tuple(steps),
        )

    async def tell_review_channel(ctx: StepContext) -> StepResult:
        """Tell the channel the review was read in how the confirmation ended, where the
        member's reply can no longer be updated: fourteen minutes on, or after a restart."""
        number = ctx.payload["season_number"]
        refused = ((_view(ctx, APPLY) or _EMPTY).result or {}).get("refused")
        if _applied(ctx):
            text = channel_confirmed(ctx.actor_id)
        elif refused:
            # Refused as the save was tried again, after the reply had gone: the reason is
            # given, as for a refusal as the confirmation first came up to run.
            text = channel_refused(ctx.actor_id, number, str(refused))
        else:
            text = channel_not_confirmed(ctx.actor_id, number)
        await tell_channel(ctx.bot, int(ctx.payload["channel_id"]), text)
        return StepResult(result={"told": True})

    async def reply_lost(ctx: StepContext) -> bool:
        return not ctx.reply_updatable

    async def close(_db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        if not _applied(ctx):
            refused = (_view(ctx, APPLY) or _EMPTY).result
            if refused and refused.get("refused"):
                reply = str(refused["refused"])
                return StepResult(
                    result={"closed": True},
                    lines=(refusal_line(ctx.named, _BUTTON, reply_reason(reply)),),
                )
            return StepResult(result={"closed": True})
        applied = (_view(ctx, APPLY) or _EMPTY).result or {}
        line = (
            f"{ctx.named} | /season placements-review | Confirmed\n"
            f"  season: Season #{applied.get('season_number', ctx.payload['season_number'])}\n"
            f"  placements: {applied.get('placements', 0)}"
            + "".join(f"\n  not done: {each}" for each in _left(ctx))
        )
        return StepResult(result={"closed": True}, lines=(line,))

    # ── How each job is named, in the lines that say it stopped the queue ──────

    async def describe_apply(ctx: StepContext) -> str:
        return f"confirming the placements of season {ctx.payload['season_number']}"

    async def describe_judge(ctx: StepContext) -> str:
        return (
            f"judging again whether season {ctx.payload['season_number']}'s placements can "
            "still be confirmed"
        )

    async def describe_tell(ctx: StepContext) -> str:
        return (
            f"telling <#{ctx.payload['channel_id']}> how the confirmation of season "
            f"{ctx.payload['season_number']}'s placements ended"
        )

    async def describe_close(ctx: StepContext) -> str:
        return f"recording the confirmation of season {ctx.payload['season_number']}'s placements"

    def tell_of_refusal(context: RefusedContext) -> tuple[FollowOn, ...]:
        """Where the member can no longer be told of a refusal at run, ask the change that tells
        the review's channel, saved with the refusal."""
        if context.reply_updatable:
            return ()
        return _tell(
            context.payload, context.actor_id,
            channel_refused(context.actor_id, context.payload["season_number"], context.reply),
        )

    def tell_of_discard(context: DiscardedContext) -> tuple[FollowOn, ...]:
        """Where a league admin discards the confirmation stopped at its check, before anything
        of it began, and the member can no longer be told, tell the review's channel."""
        if context.reply_updatable:
            return ()
        return _tell(
            context.payload, context.actor_id,
            channel_not_confirmed(context.actor_id, context.payload["season_number"]),
        )

    def outcome(ctx: OutcomeContext) -> str:
        refusal = (_view(ctx, APPLY) or _EMPTY).result
        if refusal and refusal.get("refused"):
            return str(refusal["refused"])
        if not _applied(ctx):
            return NOT_CONFIRMED
        applied = (_view(ctx, APPLY) or _EMPTY).result or {}
        return (
            f"✅ {applied.get('placements', 0)} placement(s) confirmed. The season is ongoing "
            "again." + approval_checks.not_done_section(_left(ctx, told=False))
        )

    def doing(payload: dict[str, Any]) -> str:
        return f"Confirming season {payload['season_number']}'s placements"

    steps: dict[str, Step] = {
        APPLY: Step(APPLY, StepKind.SAVE, apply, describe=describe_apply),
        JUDGE: Step(JUDGE, StepKind.ACT, judge, describe=describe_judge),
        GRANT_ROLES: driver_roles_step(placement),
        POST_LINEUP: lineup_posting.lineup_step(placement),
        standing_posts.DELETE_REPLACED: standing_posts.delete_replaced_step(),
        TELL_REVIEW_CHANNEL: Step(
            TELL_REVIEW_CHANNEL, StepKind.ACT, tell_review_channel, still_due=reply_lost,
            describe=describe_tell,
        ),
        CLOSE: Step(CLOSE, StepKind.SAVE, close, describe=describe_close),
    }
    return ChangeType(
        kind=KIND,
        opening=(PlannedStep(APPLY), PlannedStep(TELL_REVIEW_CHANNEL), PlannedStep(CLOSE)),
        steps=steps,
        check=check,
        key=lambda payload: f"{KIND}:{payload['season_id']}",
        doing=doing,
        outcome=outcome,
        on_refused=tell_of_refusal,
        on_discarded=tell_of_discard,
    )


# ── What the jobs left, read by the outcome and the closing line ────────────────────

_EMPTY = StepView(name="", payload={}, result=None, done=False)


def _tell(payload: dict[str, Any], actor_id: int | None, text: str) -> tuple[FollowOn, ...]:
    """The `channel.tell` change that tells the review's channel *text*, as the bot's, for the
    member's actor."""
    channel_id = payload["channel_id"]
    return (
        FollowOn(
            CHANNEL_TELL,
            {"channel_id": channel_id, "text": text, "mention": actor_id},
            f"Telling <#{channel_id}> that season {payload['season_number']}'s placements were "
            "not confirmed",
        ),
    )


def _view(ctx: OutcomeContext, name: str) -> StepView | None:
    """The last job named *name*: the save, tried again, is planned afresh, and the last stands."""
    return next((view for view in reversed(ctx.steps) if view.name == name), None)


def _discarded(view: StepView) -> bool:
    return "discarded" in (view.result or {})


def _applied(ctx: OutcomeContext) -> bool:
    """Whether the save went through: it is done, and neither refused, unjudged nor discarded."""
    view = _view(ctx, APPLY)
    if view is None or not view.done:
        return False
    result = view.result or {}
    return not (_discarded(view) or result.get("refused") or result.get("unjudged"))


def _left(ctx: OutcomeContext, *, told: bool = True) -> list[str]:
    """What was not done, one line for each job a league admin discarded, in the order of the
    jobs, the drivers whose roles were not granted gathered into one line. *told* adds the review's
    channel not told, which the member's reply cannot carry."""
    lines: list[str] = []
    ungranted: list[str] = []
    ungranted_at: int | None = None
    for view in ctx.steps:
        if not _discarded(view):
            continue
        each = view.payload
        if view.name == GRANT_ROLES:
            if ungranted_at is None:
                ungranted_at = len(lines)
                lines.append("")
            ungranted.append(str(each["user_id"]))
        elif view.name == POST_LINEUP:
            lines.append(unposted_lineup_line(each["division_name"]))
        elif view.name == standing_posts.DELETE_REPLACED:
            lines.append(standing_posts.not_deleted(view) or "")
        elif told and view.name == TELL_REVIEW_CHANNEL:
            lines.append(f"<#{ctx.payload['channel_id']}> was not told how the confirmation ended")
    if ungranted_at is not None:
        lines[ungranted_at] = ungranted_line(ungranted)
    return lines

