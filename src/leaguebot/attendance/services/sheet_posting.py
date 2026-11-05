"""A division's attendance sheet, asked of the change queue (#439, slice 6).

A command that makes its own change and wants the sheet posted after it (`/attendance sync`, and
the sanctions it enforces) asks the queue, as the bot's and repeatably, to post it
(`attendance.sheet.post`): the command's reply says what it did, and the post follows. A sheet
Discord refuses stops the queue at the `post_sheet` job, to be tried again as text (Constitution
XIV, rule 8) or discarded, instead of being put on the old retry queue. A division with no
attendance channel set stops it too, naming the command that sets one.

**The opening and final sheets, posted when a season is approved and when it completes
(#283),** are two further occasions this module posts, under the same rules: the attendance
sheet a league already reads after every round, under the occasion's heading and with no
message text (see
:class:`leaguebot.core.models.classification_occasion.ClassificationOccasion`). Attendance posts
its own, one division at a time, each a job of the season's approval or of its completion on the
change queue, signed up to the hooks core declares for placements confirmed and for a season
completed, after results' own standings (architecture.md, "How modules and core fit together").
The opening sheet is drawn against the division's first round, read through core's
`division_first_round`; the final sheet is drawn against the round the completion hands its
step, the division's last round with results, which results reads for itself and hands on.
Neither reads `rounds` itself, and each raises where it cannot post, for the queue to stop on.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import aiosqlite

from leaguebot.attendance.services import attendance_service
from leaguebot.core.db.database import get_connection
from leaguebot.core.models.change import (
    ChangeOrigin,
    GuildUnavailable,
    PlannedStep,
    StepKind,
    StepResult,
    Verdict,
)
from leaguebot.core.models.classification_occasion import ClassificationOccasion
from leaguebot.core.services.change_queue import (
    ChangeQueue,
    ChangeType,
    CheckContext,
    OutcomeContext,
    Step,
    StepContext,
)
from leaguebot.core.services import standing_posts
from leaguebot.core.services.module_service import ModuleService
from leaguebot.core.services.season_lifecycle_service import require_guild
from leaguebot.core.services.season_service import division_first_round
from leaguebot.core.utils.league_server import league_guild

POST_SHEET = "post_sheet"
SHEET_POST = "attendance.sheet.post"
OPENING_SHEET = "opening_sheet"
FINAL_SHEET = "final_sheet"


def sheet_step() -> Step:
    """The job that posts a division's attendance sheet for a round.

    The payload is `{"division_id", "division_name", "round_id", "sanctioned"}`, `sanctioned`
    being the ids of the driver profiles the sheet marks as having reached the limit. The first
    try posts as configured (the picture where the image module has one); a try again posts the
    text. The job returns the sheet posted, saves its id as the division's live sheet with its
    mark (`record_sheet_on`) and plans, where the sheet takes the slot, the deletion of the one it
    replaces; a sheet discarded plans none, so the earlier one stands.
    """

    async def run(ctx: StepContext) -> StepResult:
        await standing_posts.remove_kept(ctx.bot, ctx.kept)
        guild = await league_guild(ctx.bot)
        if guild is None:
            raise GuildUnavailable("the league's server is not in the cache")
        payload = ctx.step_payload or ctx.payload
        sanctioned = {int(each) for each in payload.get("sanctioned") or []}
        posted = await attendance_service.post_attendance_sheet(
            ctx.bot, guild, ctx.db_path, int(payload["round_id"]), int(payload["division_id"]),
            sanctioned_profile_ids=sanctioned or None, as_text=ctx.tries > 0,
        )
        result = attendance_service.sheet_result(posted)
        then: tuple[PlannedStep, ...] = ()
        if posted is not None and posted.takes_slot:
            then = standing_posts.replaced(
                posted.channel_id, posted.previous, posted.message_id,
                what="attendance sheet",
                division_name=str(payload.get("division_name", "the division")),
            )
        return StepResult(result=result, then=then)

    async def record(db: aiosqlite.Connection, ctx: StepContext, result: StepResult) -> None:
        payload = ctx.step_payload or ctx.payload
        await attendance_service.record_sheet_on(db, int(payload["division_id"]), result.result)

    async def still_due(ctx: StepContext) -> bool:
        return await ctx.bot.module_service.is_attendance_enabled()

    async def describe(ctx: StepContext) -> str:
        payload = ctx.step_payload or ctx.payload
        return f"posting the attendance sheet of **{payload.get('division_name', 'the division')}**"

    return Step(
        POST_SHEET, StepKind.ACT, run, still_due=still_due, describe=describe, record=record,
    )


def sheet_post_change() -> ChangeType:
    """`attendance.sheet.post`: the bot's change, repeatable, that posts one division's sheet.

    The check drops the request where attendance is off, the division is gone or the round
    named is cancelled, and refuses where the division has no attendance channel set: the bot's
    change so refused stops the queue with the reason, to be put right and retried.
    """

    async def check(ctx: CheckContext) -> Verdict:
        async with get_connection(ctx.db_path) as db:
            enabled = await (await db.execute(
                "SELECT module_enabled FROM attendance_config"
            )).fetchone()
            division = await (await db.execute(
                "SELECT d.name, c.attendance_channel_id FROM divisions d "
                "LEFT JOIN attendance_division_config c ON c.division_id = d.id WHERE d.id = ?",
                (int(ctx.payload["division_id"]),),
            )).fetchone()
            round_row = await (await db.execute(
                "SELECT status FROM rounds WHERE id = ?", (int(ctx.payload["round_id"]),)
            )).fetchone()
        if not (enabled and enabled["module_enabled"]):
            return Verdict.not_due("attendance is off")
        if division is None:
            return Verdict.not_due("the division is gone")
        if round_row is not None and round_row["status"] == "CANCELLED":
            return Verdict.not_due("the round was cancelled")
        if not division["attendance_channel_id"]:
            return Verdict.refuse(
                f"**{division['name']}** has no attendance channel set: set one with "
                f"`/attendance channel attendance`, then press Retry."
            )
        return Verdict.go()

    def outcome(_ctx: OutcomeContext) -> str:
        return ""

    return ChangeType(
        kind=SHEET_POST,
        opening=(PlannedStep(POST_SHEET),),
        steps={POST_SHEET: sheet_step(), standing_posts.DELETE_REPLACED: standing_posts.delete_replaced_step()},
        check=check,
        key=lambda payload: f"{SHEET_POST}:{payload['division_id']}",
        doing=lambda payload: (
            f"posting the attendance sheet of **{payload.get('division_name', 'the division')}**"
        ),
        outcome=outcome,
        repeatable=True,
    )


async def ask_sheet_post(
    queue: ChangeQueue,
    db_path: str,
    division_id: int,
    round_id: int,
    sanctioned: Iterable[int] = (),
) -> None:
    """Ask the queue to post the attendance sheet of *division_id* for *round_id*, marking the
    driver profiles in *sanctioned* as having reached the limit.

    A command which makes its own change and wants a standing post after it asks the queue for
    that post as a change of the bot's, of a kind that may be repeated (slice 6's decision 2); the
    command's reply is unchanged and the post follows. The division's name is read here, on a
    connection of its own.
    """
    async with get_connection(db_path) as db:
        row = await (await db.execute(
            "SELECT name FROM divisions WHERE id = ?", (division_id,)
        )).fetchone()
    name = row["name"] if row is not None else str(division_id)
    payload: dict[str, Any] = {
        "division_id": division_id, "division_name": name, "round_id": round_id,
        "sanctioned": sorted(int(each) for each in sanctioned),
    }
    await queue.ask(
        SHEET_POST, payload, what=f"posting the attendance sheet of **{name}**",
        origin=ChangeOrigin.BOT,
    )


async def _opening_round(db_path: str, division_id: int):
    """The division's first round, read through core's `division_first_round`, or None where it
    has none.

    The sheet is drawn against it: not because it stands after it, it stands after nothing, but
    because the grid of rounds down its side is read from the season's calendar and the heading
    context the posting resolves is keyed by a round. Every cell in that grid is empty, which is
    exactly what an opening sheet should show. Approval refuses a division with no rounds, so
    None cannot normally happen.
    """
    return await division_first_round(db_path, division_id)


async def post_opening_sheet(
    bot: Any, guild: Any, db_path: str, division_id: int, *, as_text: bool = False
) -> dict[str, Any]:
    """Post one division's opening attendance sheet, raising where it cannot be posted, and
    give the job its result (`attendance_service.sheet_result`).

    This always raises and saves nothing: a division with no attendance channel stops the queue,
    its failure naming `/attendance channel attendance`, and so does one given a channel the
    guild no longer holds. The opening sheet takes the division's live slot, which the job saves
    with its mark (`attendance_service.record_sheet_on`) and replaces by a job of its own.
    *as_text* leaves the graphic out, which is how a job retries.
    """
    opener = await _opening_round(db_path, division_id)
    if opener is None:
        return attendance_service.sheet_result(None)
    return attendance_service.sheet_result(await attendance_service.post_attendance_sheet(
        bot,
        guild,
        db_path,
        opener.id,
        division_id,
        occasion=ClassificationOccasion.SEASON_OPENING,
        as_text=as_text,
    ))


async def placements_confirmed_on(
    db: Any, division_id: int, division_name: str
) -> tuple[PlannedStep, ...]:
    """Attendance's own job of placements confirmed (the season's approval): its opening sheet
    for *division_id*, planned whatever attendance's switch says — :func:`opening_steps`'
    `still_due` drops it as it runs where attendance is off."""
    return (
        PlannedStep(OPENING_SHEET, {"division_id": division_id, "division_name": division_name}),
    )


def opening_steps(modules: ModuleService) -> dict[str, Step]:
    """Attendance's step definition for the opening sheet, signed up to
    *placements_confirmed_steps* beside results' `season_standings_posts.opening_steps`. The
    builder hands in *modules*, as it hands cancellation's steps theirs, so that `still_due`
    reads attendance's switch without looking the service up on the bot (architecture.md, "How
    modules and core fit together")."""

    async def run(ctx: StepContext) -> StepResult:
        await standing_posts.remove_kept(ctx.bot, ctx.kept)
        guild = await require_guild(ctx.bot)
        result = await post_opening_sheet(
            ctx.bot, guild, ctx.db_path, int(ctx.step_payload["division_id"]),
            as_text=ctx.tries > 0,
        )
        then: tuple[PlannedStep, ...] = ()
        if result.get("takes_slot"):
            then = standing_posts.replaced(
                result.get("channel_id"), result.get("old"), result.get("new"),
                what="attendance sheet",
                division_name=str(ctx.step_payload.get("division_name", "the division")),
            )
        return StepResult(result=result, then=then)

    async def record(db: aiosqlite.Connection, ctx: StepContext, result: StepResult) -> None:
        await attendance_service.record_sheet_on(db, int(ctx.step_payload["division_id"]),
                                                  result.result)

    async def still_due(_ctx: StepContext) -> bool:
        return await modules.is_attendance_enabled()

    async def describe(ctx: StepContext) -> str:
        return f"posting the opening attendance sheet of **{ctx.step_payload['division_name']}**"

    def not_done(view: Any) -> str:
        return (
            f"**{view.payload['division_name']}** — its opening attendance sheet could not be "
            f"posted. No command posts it again; the first round's attendance posts the sheet "
            f"as usual."
        )

    return {
        OPENING_SHEET: Step(
            OPENING_SHEET, StepKind.ACT, run, still_due=still_due, describe=describe,
            record=record, not_done=not_done,
        ),
    }


async def post_final_sheet(
    bot: Any, guild: Any, db_path: str, division_id: int, round_id: int, *, as_text: bool = False
) -> dict[str, Any]:
    """Post one division's final attendance sheet, drawn against *round_id*, raising where it
    cannot be posted, and give the job its result (`attendance_service.sheet_result`).

    This always raises and saves nothing: a division with no attendance channel stops the queue,
    its failure naming `/attendance channel attendance`, and so does one given a channel the
    guild no longer holds. The final sheet stands beside the last round's and takes no live slot,
    so nothing is saved for it and nothing deleted. *as_text* leaves the graphic out, which is
    how a job retries.
    """
    return attendance_service.sheet_result(await attendance_service.post_attendance_sheet(
        bot,
        guild,
        db_path,
        round_id,
        division_id,
        occasion=ClassificationOccasion.SEASON_FINAL,
        as_text=as_text,
    ))


async def season_completed_on(
    _db: Any, division_id: int, division_name: str, round_id: int
) -> tuple[PlannedStep, ...]:
    """Attendance's own job of a season completed: its final sheet for *division_id*, drawn
    against *round_id*, planned whatever attendance's switch says — :func:`final_steps`'
    `still_due` drops it as it runs where attendance is off."""
    return (
        PlannedStep(
            FINAL_SHEET,
            {"division_id": division_id, "division_name": division_name, "round_id": round_id},
        ),
    )


def final_steps(modules: ModuleService) -> dict[str, Step]:
    """Attendance's step definition for the final sheet, signed up to *season_completed_steps*
    beside results' `season_standings_posts.final_steps`. The builder hands in *modules*, as it
    hands the opening sheet's theirs, so that `still_due` reads attendance's switch without
    looking the service up on the bot (architecture.md, "How modules and core fit together")."""

    async def run(ctx: StepContext) -> StepResult:
        guild = await require_guild(ctx.bot)
        each = ctx.step_payload
        result = await post_final_sheet(
            ctx.bot, guild, ctx.db_path, int(each["division_id"]), int(each["round_id"]),
            as_text=ctx.tries > 0,
        )
        # The final sheet stands beside the last round's: nothing is saved for it and nothing
        # deleted, so it plans no deletion and carries no record.
        return StepResult(result=result)

    async def still_due(_ctx: StepContext) -> bool:
        return await modules.is_attendance_enabled()

    async def describe(ctx: StepContext) -> str:
        return f"posting the final attendance sheet of **{ctx.step_payload['division_name']}**"

    def not_done(view: Any) -> str:
        name = view.payload["division_name"]
        return (
            f"**{name}** — its final classification (attendance sheet) could not be posted. "
            "No command posts it again."
        )

    return {
        FINAL_SHEET: Step(
            FINAL_SHEET, StepKind.ACT, run, still_due=still_due, describe=describe,
            not_done=not_done,
        ),
    }
