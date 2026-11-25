"""Results' opening and final standings, posted when a season is approved and when it
completes (#283).

The opening and final classifications bracket a season: the standings a league already reads
after every round, under the occasion's heading and with no message text (see
:class:`leaguebot.core.models.classification_occasion.ClassificationOccasion`). Results posts
its own, one division at a time, each a job of the season's approval or of its completion on the
change queue, signed up to the hooks core declares for placements confirmed and for a season
completed (:func:`placements_confirmed_on`, :func:`season_completed_on`, architecture.md, "How
modules and core fit together"). The final classification is drawn against the division's last
round with results, which results reads for itself (:func:`final_round_on`) from its own
`session_results`, exactly as core used to read it.

**Each posting reads the division's round as it runs** (never the table itself: the opening
through core's `division_first_round`, the final through the round the completion hands its
step, architecture.md, "A module reads core's tables only through core's services"), **and
raises where it cannot post**, for the queue to stop on — except that it returns without posting
where no standings channel is set, or where no driver is ranked, neither of which is a fault the
queue should stop on. The standings channel is read from `division_results_config`, where
`/results channel standings` keeps it. **Each posting's message ids are recorded** against its
division and occasion in `classification_posts`, with the job's done mark (`record_posted`), so
that switching results off can take the opening classification down; the final classification's
are recorded the same way, but its season is complete and the purge never reads one. A table
already sent when a later one fails is not recorded: it is named on the failure (`result`), and
the job keeps it: the next try is handed it as *kept* and removes that copy before it posts both
again.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterable, Sequence
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, NoReturn

import discord

from leaguebot.results.models.switch import SWITCH_KEY
from leaguebot.framework.models.change import (
    PlannedStep,
    StepFailedOnDiscord,
    StepKind,
    StepResult,
    drawn_kept,
    drawn_part,
)
from leaguebot.core.models.classification_occasion import ClassificationOccasion
from leaguebot.core.models.fault_source import source_of
from leaguebot.core.services.modules.image_drawing import picture_remedy, with_host_remedy
from leaguebot.framework.services.queue.change_types import Step, StepContext
from leaguebot.framework.services.switches.module_service import ModuleService
from leaguebot.core.services.seasons.season_lifecycle_service import require_guild
from leaguebot.core.services.seasons.season_service import (
    division_first_round,
    division_rounds_on,
    round_by_id,
)
from leaguebot.framework.utils.hooks import AwaitedHook
from leaguebot.framework.utils.log_lines import framed_picture_lines

if TYPE_CHECKING:
    import aiosqlite

    from leaguebot.image.services.render.image_drawing import ImageDrawing

    from leaguebot.results.services.publishing.results_post_service import PostedTable

OPENING_STANDINGS = "opening_standings"
FINAL_STANDINGS = "final_standings"


def _refused_picture_line(
    ctx: StepContext, championship: str | None, fault: str, *, subject: str
) -> str:
    """The line a job writes to the log for a *championship* picture of its *subject* (the
    opening standings, the final classification) that went out as text: the member and the
    change it ran for, then the division, the championship, what failed (Discord's refusal and
    its reason, or the drawing's fault) and what puts it right, as the round's standings job
    says it (image specification, "Errors"). No command posts these again, so none is named.

    Where Discord refused the picture (an `UploadFault`) nothing of a template is at fault;
    otherwise the remedy reads by where the fault lies (`picture_remedy`): the template to fix,
    the bot's fault, or the converter's absence."""
    from leaguebot.results.services.publishing.results_post_service import UploadFault

    tail = f"no command posts the {subject} again, and the text stands"
    put_right = tail if isinstance(fault, UploadFault) else picture_remedy(
        source_of(fault), template=f"fix the standings template; {tail}", otherwise=tail
    )
    return (
        f"{ctx.named} | {ctx.what} | Standings posted as text\n"
        f"  division: {ctx.step_payload['division_name']}\n"
        f"  problem: the {championship} picture of the {subject} could not be "
        f"posted: {fault}\n"
        f"  to put it right: {put_right}"
    )


def _with_host_remedy(lines: Iterable[str], *, subject: str) -> tuple[str, ...]:
    """*lines*, each of a picture of the *subject* not drawn for want of Inkscape given what puts
    it right: the host must install it, and no command then posts the picture again
    (`with_host_remedy`)."""
    tail = f"no command posts the {subject} again, and the text stands"
    return tuple(with_host_remedy(line, otherwise=tail) for line in lines)


def _refused_picture_lines(
    ctx: StepContext, tables: "Sequence[PostedTable]", *, subject: str
) -> tuple[str, ...]:
    """The lines for each table of the *subject* whose picture went out as text
    (:func:`_refused_picture_line`)."""
    return tuple(
        _refused_picture_line(ctx, table.championship, table.fault, subject=subject)
        for table in tables if table.fault is not None
    )


def _raise_keeping_refused_pictures(
    ctx: StepContext, failure: StepFailedOnDiscord | discord.HTTPException, *, subject: str
) -> NoReturn:
    """Raise what a job raises where the *subject*'s post failed: *failure*, but where a picture
    was refused or would not draw and the text that went out in its place failed too, a stop
    carrying that picture's line beside the lines it already kept, for the try that posts the
    text to hand back once.

    A stop of the posting carries the earlier tries' lines first and then those of this try,
    still marked as the drawing handed them back (`NoticeBlock`, which only a save to the
    database loses). They are framed here, with the member and the change, before the stop that
    keeps them is raised, with each picture's label and notices and the line holding them."""
    from leaguebot.results.services.publishing.results_post_service import (
        PICTURE_FAULTS_ATTRIBUTE,
    )

    cause: BaseException | None = failure
    while cause is not None and not getattr(cause, PICTURE_FAULTS_ATTRIBUTE, None):
        cause = cause.__cause__
    faults: dict[str, str] = getattr(cause, PICTURE_FAULTS_ATTRIBUTE) if cause is not None else {}
    refused = tuple(
        _refused_picture_line(ctx, name, fault, subject=subject) for name, fault in faults.items()
    )
    if isinstance(failure, StepFailedOnDiscord):
        earlier = drawn_kept(ctx.kept)
        new = drawn_kept(failure.result).lines[len(earlier.lines):]
        raise framed_picture_lines(
            ctx.named, ctx.what, new, kept=ctx.kept, label=subject
        ).plus(*refused).carried_by(failure)
    if isinstance(failure, discord.HTTPException) and refused:
        raise StepFailedOnDiscord(f"the {subject} were refused").carrying(refused) from failure
    raise failure


async def _opening_round(db_path: str, division_id: int):
    """The division's first round, read through core's `division_first_round`, or None where it
    has none.

    The standings are drawn against it: not because they stand after it, they stand after
    nothing, but because the grid of rounds down their side is read from the season's calendar
    and the heading context the posting resolves is keyed by a round. Every cell in that grid is
    empty, which is exactly what the opening standings should show. Approval refuses a division
    with no rounds, so None cannot normally happen.
    """
    return await division_first_round(db_path, division_id)


async def remove_earlier_try(guild, kept: dict[str, Any] | None) -> dict[str, Any] | None:
    """Take down the standings a try before this one left posted in part, or None where it left none.

    The ids of an opening or final standings are recorded only once its posting is done, so a table
    already sent when a later one failed is put on the failure for the job to keep (`kept`), and the
    next try removes it here, from the channel it was posted in, which the standings channel may
    since have left: a channel the guild no longer holds holds none of them. A message that will not
    delete raises, with what is still to remove. Once the copies are down, what is returned names
    none left, so that a failure of this try names what it left, even nothing, and the job no longer
    keeps the copies removed. The earlier try's picture lines stay in it, as a stop's result
    replaces what the job kept and a failure that is not Discord's carries none of its own.
    """
    from leaguebot.results.services.publishing.results_post_service import remove_part_posted_messages

    earlier = [int(each) for each in (kept or {}).get("new") or []]
    if not earlier:
        return None
    posted_in = int((kept or {}).get("channel_id") or 0)
    earlier_channel = guild.get_channel(posted_in) if posted_in else None
    if earlier_channel is not None:
        left, failures = await remove_part_posted_messages(earlier_channel, earlier)
        if left:
            # The earlier try's picture lines go on the stop, as a stop's result replaces what
            # the job kept: the retry that does remove the tables must still hand them back.
            raise drawn_kept(kept).carried_by(StepFailedOnDiscord(
                f"{len(left)} message(s) of the earlier try could not be removed",
                result={"new": left, "channel_id": posted_in},
            )) from (failures[0] if failures else None)
    return {"new": [], "channel_id": posted_in, **drawn_part(kept)}


@asynccontextmanager
async def keeping(removed: dict[str, Any] | None, what: str) -> AsyncIterator[None]:
    """Carry what the earlier try's removal left on any failure of this try.

    Where the earlier try's copies are down (*removed* is not None), a failure of this one that
    names no result is given *removed*, and a failure that is not a Discord one becomes one that
    does, so that the job keeps what is true: nothing left standing.
    """
    from leaguebot.framework.models.change import StepFailedOnDiscord

    try:
        yield
    except StepFailedOnDiscord as failure:
        if removed is not None and failure.result is None:
            failure.result = removed
        raise
    except Exception as failure:
        if removed is None:
            raise
        raise StepFailedOnDiscord(
            f"the {what} standings could not be posted once the earlier try's were removed",
            result=removed,
        ) from failure


#: What each stranding's *what* calls the standings a line of its picture speaks of.
_SUBJECTS = {"opening": "opening standings", "final": "final classification"}


async def produce_stranding(
    what: str, channel_id, posting, kept: dict[str, Any] | None = None
) -> "list[PostedTable]":
    """Await *posting*, results' ``produce_standings``, and hand back the tables it put up, naming
    on its failure a table already sent.

    The ids are recorded only once the posting is done, so a table already sent when a later one
    failed is put on the failure for the job to keep, and the next try removes it. What the
    pictures drawn for it said, on the failure or kept from an earlier try (*kept*), is kept
    beside them, for the try that posts to hand back.
    """

    from leaguebot.framework.models.change import StepFailedOnDiscord, drawn_lines_kept
    from leaguebot.results.services.publishing.results_post_service import DRAWN_LINES_ATTRIBUTE

    try:
        return await posting
    except Exception as failure:
        stranded = [int(each) for each in getattr(failure, "left_standing", None) or []]
        lines = drawn_lines_kept(kept) + _with_host_remedy(
            getattr(failure, DRAWN_LINES_ATTRIBUTE, ()), subject=_SUBJECTS[what]
        )
        if stranded:
            raise StepFailedOnDiscord(
                f"the {what} standings were posted in part and the rest was refused",
                result={"new": stranded, "channel_id": int(channel_id)},
            ).carrying(lines) from failure
        if lines and isinstance(failure, discord.HTTPException):
            raise StepFailedOnDiscord(f"the {what} standings were refused").carrying(
                lines
            ) from failure
        raise


async def standings_channel_of(db_path: str, division_id: int) -> int | None:
    """The standings channel *division_id* was given in `division_results_config`, or None where
    it never was."""
    from leaguebot.framework.db.database import get_connection

    async with get_connection(db_path) as db:
        config = await (
            await db.execute(
                "SELECT standings_channel_id FROM division_results_config WHERE division_id = ?",
                (division_id,),
            )
        ).fetchone()
    channel_id = config["standings_channel_id"] if config is not None else None
    return int(channel_id) if channel_id else None


async def post_opening_standings(
    driver_names_read: AwaitedHook,
    guild,
    db_path: str,
    division_id: int,
    *,
    drawing: "ImageDrawing | None" = None,
    as_text: bool = False,
    kept: dict[str, Any] | None = None,
) -> "list[PostedTable]":
    """Post one division's opening standings, both championships, raising where it cannot, and
    return the tables put up (none where nothing is posted).

    **The standings channel is read here, from `division_results_config`,** where results' own
    settings keep it, as the posting runs. **It returns without posting where no standings
    channel is set, or where no driver is ranked** (a division never given a channel, or one with
    nobody seated): a channel the guild no longer holds raises `StepFailedOnDiscord`, for the
    change queue to stop on. The ids are recorded by the step with its done mark
    (`record_posted`), not here, so a table already sent when a later one fails is named on the
    failure (`result`), and the job keeps it: the next try is handed it as *kept* and removes
    that copy, from the channel it was posted in, before it posts both again, failing again, with
    what it still has to remove, where a message will not delete. Once that copy is down, any
    failure of the try names what the try left, even nothing, so that the job no longer keeps the
    copy removed. *drawing* is the image module's, which draws the pictures (none is drawn where it is None); *as_text* leaves the graphic out, which is how a job retries (Constitution XIV
    rule 8).
    """
    from leaguebot.framework.models.change import StepFailedOnDiscord
    from leaguebot.core.services.drivers.driver_names import read_driver_names
    from leaguebot.results.services.standings import standings_service
    from leaguebot.results.services.publishing.results_post_service import (
        produce_standings,
        reserves_in_standings,
    )

    opener = await _opening_round(db_path, division_id)
    if opener is None:
        return []
    removed = await remove_earlier_try(guild, kept)
    async with keeping(removed, "opening"):
        channel_id = await standings_channel_of(db_path, division_id)
        if not channel_id:
            return []
        channel = guild.get_channel(int(channel_id))
        if channel is None:
            raise StepFailedOnDiscord(
                f"the standings channel (id {channel_id}) is not in the server"
            )

        # Twice, deliberately. The order is taken on the name the sheet will actually draw, and the
        # names are resolved from Discord by user id, so the roster has to be known before it can be
        # ordered. The first pass is read only for who is in it; the second is the one that counts.
        roster = await standings_service.opening_driver_standings(db_path, division_id)
        names = await read_driver_names(
            db_path, driver_names_read, guild, [snapshot.driver_user_id for snapshot in roster],
            division_id=division_id,
        )
        driver_snaps = await standings_service.opening_driver_standings(db_path, division_id, names)
        team_snaps = await standings_service.opening_team_standings(db_path, division_id)
        if not driver_snaps:
            return []
        return await produce_stranding(
            "opening",
            channel_id,
            produce_standings(
                db_path,
                division_id,
                opener.id,
                opener.round_number,
                opener.track_name or "",
                channel,
                driver_snaps,
                team_snaps,
                guild,
                await reserves_in_standings(db_path, division_id),
                "",
                drawing=None if as_text else drawing,
                driver_names_read=driver_names_read,
                occasion=ClassificationOccasion.SEASON_OPENING,
            ),
            kept,
        )


async def final_round_on(db: Any, division_id: int) -> int | None:
    """*division_id*'s last round with results, or None where it has none, read on the
    completion's save's connection, signed up to core's `final_round_read`.

    The final classification is drawn against that round, whose classification it *is*: results'
    own read of its `session_results`, exactly as core's read used to be — except that the round
    ids themselves come from core's `division_rounds_on`, read on this same connection, rather
    than from a join of `rounds` into this query (architecture.md, "The database": a query that
    would join another owner's table is split at that line, each half asked of its owner).
    """
    for round_id in await division_rounds_on(db, division_id):
        cursor = await db.execute(
            "SELECT 1 FROM session_results WHERE round_id = ? AND status = 'ACTIVE' LIMIT 1",
            (round_id,),
        )
        if await cursor.fetchone() is not None:
            return round_id
    return None


async def season_standing_on(
    db: Any, division_id: int, accounts: Sequence[str]
) -> tuple[tuple[int, int] | None, int | None]:
    """A driver's last standing in *division_id* and the division's winner's points, read on the
    save's connection, signed up to core's `season_standing_read`.

    Core hands the accounts the driver has held, and writes the season's history entry from what
    this hands back: `((position, points) or None, winner's points or None)`. The standing is the
    driver's under whichever of *accounts* it stands in the latest round that holds one: a driver
    who changed account after the last round finished it under the old one. The winner's points
    are the most any driver holds in the latest round with a standing at all. The round ids come
    from core's `division_rounds_on`, newest first, read on this same connection, rather than from
    a join of `rounds` into these queries (architecture.md, "The database"). No standing is None,
    and so is a division with no standings.
    """
    rounds = await division_rounds_on(db, division_id)
    cursor = await db.execute(
        "SELECT round_id, MAX(total_points) FROM driver_standings_snapshots "
        "WHERE division_id = ? GROUP BY round_id",
        (division_id,),
    )
    winners = {int(row[0]): int(row[1]) for row in await cursor.fetchall()}
    standings: dict[int, tuple[int, int]] = {}
    if accounts:
        marks = ",".join("?" for _ in accounts)
        cursor = await db.execute(
            "SELECT round_id, standing_position, total_points FROM driver_standings_snapshots "
            f"WHERE division_id = ? AND driver_user_id IN ({marks}) ORDER BY id",  # noqa: S608
            (division_id, *accounts),
        )
        standings = {int(row[0]): (int(row[1]), int(row[2])) for row in await cursor.fetchall()}
    standing = next((standings[each] for each in rounds if each in standings), None)
    winner = next((winners[each] for each in rounds if each in winners), None)
    return standing, winner


async def _final_round(db_path: str, division_id: int, round_id: int):
    """The round the final classification is drawn against, read through core's `round_by_id`
    (never `rounds` itself, architecture.md, "A module reads core's tables only through core's
    services") as the posting runs."""
    return await round_by_id(db_path, division_id, round_id)


async def post_final_standings(
    driver_names_read: AwaitedHook,
    guild,
    db_path: str,
    division_id: int,
    round_id: int,
    *,
    drawing: "ImageDrawing | None" = None,
    as_text: bool = False,
    kept: dict[str, Any] | None = None,
) -> "list[PostedTable]":
    """Post one division's final standings, both championships, raising where it cannot, and
    return the tables put up (none where nothing is posted).

    Drawn against *round_id*, the division's last round that has results, whose classification
    the final standings *are*: the season's last word, restated under its own heading rather than
    recomputed into something the round's own posting would disagree with. **They are posted
    beside the round's own standings, replacing nothing** (results and image specifications): the
    text is a new message, never an edit of the round's, and its ids are recorded against the
    division as its final classification (`record_posted`), not on the round's. The standings
    channel is read here, from `division_results_config`, as the posting runs. A division never
    given one posts nothing; one given a channel the guild no longer holds raises
    `StepFailedOnDiscord`, for the change queue to stop on. A division with no driver to rank
    posts nothing. A table already sent when a later one failed is named on the failure
    (`result`), and the job keeps it: the next try is handed it as *kept* and removes it before it
    posts both again, as the opening standings do. *as_text* leaves the graphic out, which is
    how a job retries (Constitution XIV rule 8).
    """
    from leaguebot.framework.models.change import StepFailedOnDiscord
    from leaguebot.results.services.standings import standings_service
    from leaguebot.results.services.publishing.results_post_service import (
        driver_standings_for_display,
        produce_standings,
        reserves_in_standings,
    )

    last = await _final_round(db_path, division_id, round_id)
    if last is None:
        return []
    removed = await remove_earlier_try(guild, kept)
    async with keeping(removed, "final"):
        channel_id = await standings_channel_of(db_path, division_id)
        if not channel_id:
            return []
        channel = guild.get_channel(int(channel_id))
        if channel is None:
            raise StepFailedOnDiscord(
                f"the standings channel (id {channel_id}) is not in the server"
            )

        driver_snaps = await driver_standings_for_display(
            db_path, division_id, round_id, guild, driver_names_read
        )
        team_snaps = await standings_service.compute_team_standings(
            db_path, division_id, round_id
        )
        if not driver_snaps:
            return []
        return await produce_stranding(
            "final",
            channel_id,
            produce_standings(
                db_path,
                division_id,
                round_id,
                last.round_number,
                last.track_name or "",
                channel,
                driver_snaps,
                team_snaps,
                guild,
                await reserves_in_standings(db_path, division_id),
                "",
                drawing=None if as_text else drawing,
                driver_names_read=driver_names_read,
                occasion=ClassificationOccasion.SEASON_FINAL,
                fresh=True,
            ),
            kept,
        )


def posted_result(
    division_id: int,
    occasion: ClassificationOccasion,
    tables: "Sequence[PostedTable]",
    channel_id: int | None,
) -> dict[str, Any]:
    """What a classification step's job carries for `record_posted`: the division and occasion,
    and for each table put up the championship it carries and the messages it went out as, the
    anchor first.

    Beside them go every table's new ids, flattened, and the channel posted to (`new` and
    `channel_id`, as a round's standings carry them), so that where the save marking the step
    done fails, the job keeps what was sent and the next try, or `not_done` on a discard, reaches
    it, the ids not yet being recorded.
    """
    return {
        "division_id": division_id,
        "occasion": occasion.value,
        "new": [each_id for each in tables for each_id in each.new],
        "channel_id": channel_id,
        "tables": [
            {"championship": each.championship, "new": list(each.new)} for each in tables
        ],
    }


async def record_posted(db: "aiosqlite.Connection", _ctx: StepContext, result: StepResult) -> None:
    """Save a division's classification ids in the save that marks the posting done, committing
    nothing, so that the ids and the mark stand or fall together (architecture.md, "Every post
    keeps three things"). A table with no message is a championship a text table replaced: its id
    is forgotten."""
    from leaguebot.results.services.publishing.results_post_service import (
        set_classification_message_id_on,
    )

    for table in result.result.get("tables", []):
        if table["championship"] is None:
            continue
        new = table["new"]
        await set_classification_message_id_on(
            db, int(result.result["division_id"]),
            ClassificationOccasion(result.result["occasion"]),
            new[0] if new else None, table["championship"],
            message_ids=json.dumps(new) if new else None,
            channel_id=result.result["channel_id"],
        )


async def placements_confirmed_on(
    db: Any, division_id: int, division_name: str
) -> tuple[PlannedStep, ...]:
    """Results' own job of placements confirmed (the season's approval): its opening standings
    for *division_id*, planned whatever results' switch says — :func:`opening_steps`'
    `still_due` drops it as it runs where results is off."""
    return (PlannedStep(OPENING_STANDINGS, {"division_id": division_id, "division_name": division_name}),)


async def season_completed_on(
    _db: Any, division_id: int, division_name: str, round_id: int
) -> tuple[PlannedStep, ...]:
    """Results' own job of a season completed: its final standings for *division_id*, drawn
    against *round_id*, planned whatever results' switch says — :func:`final_steps`' `still_due`
    drops it as it runs where results is off."""
    return (
        PlannedStep(
            FINAL_STANDINGS,
            {"division_id": division_id, "division_name": division_name, "round_id": round_id},
        ),
    )


def opening_steps(
    modules: ModuleService, drawing: "ImageDrawing", driver_names_read: AwaitedHook
) -> dict[str, Step]:
    """Results' step definition for the opening standings, signed up to
    *placements_confirmed_steps* beside :func:`placements_confirmed_on`. The builder hands in
    *modules*, as it hands cancellation's steps theirs, so that `still_due` reads results'
    switch without looking the service up on the bot (architecture.md, "How modules and core
    fit together")."""

    async def run(ctx: StepContext) -> StepResult:
        guild = await require_guild(ctx.bot)
        division_id = int(ctx.step_payload["division_id"])
        channel_id = await standings_channel_of(ctx.db_path, division_id)
        try:
            tables = await post_opening_standings(
                driver_names_read, guild, ctx.db_path, division_id, drawing=drawing,
                as_text=ctx.tries > 0, kept=ctx.kept,
            )
        except (StepFailedOnDiscord, discord.HTTPException) as failure:
            _raise_keeping_refused_pictures(ctx, failure, subject="opening standings")
        drawn = framed_picture_lines(
            ctx.named, ctx.what,
            _with_host_remedy(
                (line for table in tables for line in table.lines), subject="opening standings"
            ),
            kept=ctx.kept, label="opening standings",
        )
        return StepResult(
            result={
                **posted_result(
                    division_id, ClassificationOccasion.SEASON_OPENING, tables, channel_id
                ),
                **drawn.result(),
            },
            lines=drawn.lines + _refused_picture_lines(ctx, tables, subject="opening standings"),
            linked=drawn.linked,
        )

    async def still_due(_ctx: StepContext) -> bool:
        return await modules.is_enabled(SWITCH_KEY)

    async def describe(ctx: StepContext) -> str:
        return f"posting the opening standings of **{ctx.step_payload['division_name']}**"

    def not_done(view: Any) -> str:
        """The lines a discarded opening standings job leaves for doing by hand, joined by a
        plain newline: `kept_line`'s caller (`_left`, architecture.md, "How modules and core fit
        together") splits them apart again, so that a table left standing in part gets its own
        "not done:" line in the approval's log and its own bullet in the reply."""
        line = (
            f"**{view.payload['division_name']}** — its opening standings could not be "
            f"posted. No command posts them again; the first round's results post the "
            f"standings as usual."
        )
        stranded = (view.result or {}).get("new")
        if stranded:
            ids = ", ".join(str(each_id) for each_id in stranded)
            line += (
                f"\n**{view.payload['division_name']}** — a part of its opening standings "
                f"was posted before the job stopped and stands in "
                f"<#{(view.result or {}).get('channel_id')}> (message {ids}): delete it by hand."
            )
        return line

    return {
        OPENING_STANDINGS: Step(
            OPENING_STANDINGS, StepKind.ACT, run, still_due=still_due, describe=describe,
            record=record_posted, not_done=not_done,
        ),
    }


def final_steps(
    modules: ModuleService, drawing: "ImageDrawing", driver_names_read: AwaitedHook
) -> dict[str, Step]:
    """Results' step definition for the final standings, signed up to
    *season_completed_steps* beside :func:`season_completed_on`. The builder hands in *modules*,
    as it hands the opening standings' theirs, so that `still_due` reads results' switch
    without looking the service up on the bot (architecture.md, "How modules and core fit
    together")."""

    async def run(ctx: StepContext) -> StepResult:
        guild = await require_guild(ctx.bot)
        each = ctx.step_payload
        division_id = int(each["division_id"])
        channel_id = await standings_channel_of(ctx.db_path, division_id)
        try:
            tables = await post_final_standings(
                driver_names_read, guild, ctx.db_path, division_id, int(each["round_id"]),
                drawing=drawing, as_text=ctx.tries > 0, kept=ctx.kept,
            )
        except (StepFailedOnDiscord, discord.HTTPException) as failure:
            _raise_keeping_refused_pictures(ctx, failure, subject="final classification")
        drawn = framed_picture_lines(
            ctx.named, ctx.what,
            _with_host_remedy(
                (line for table in tables for line in table.lines), subject="final classification"
            ),
            kept=ctx.kept, label="final classification",
        )
        return StepResult(
            result={
                **posted_result(
                    division_id, ClassificationOccasion.SEASON_FINAL, tables, channel_id
                ),
                **drawn.result(),
            },
            lines=drawn.lines + _refused_picture_lines(ctx, tables, subject="final classification"),
            linked=drawn.linked,
        )

    async def still_due(_ctx: StepContext) -> bool:
        return await modules.is_enabled(SWITCH_KEY)

    async def describe(ctx: StepContext) -> str:
        return f"posting the final standings of **{ctx.step_payload['division_name']}**"

    def not_done(view: Any) -> str:
        """The lines a discarded final standings job leaves for doing by hand, joined by a
        plain newline: `kept_line`'s caller splits them apart again, so that a table left
        standing in part gets its own "not done:" line in the completion's log and its own
        bullet in the reply, word for word as the completion used to word it."""
        line = (
            f"**{view.payload['division_name']}** — its final classification could not be "
            "posted. No command posts it again."
        )
        stranded = (view.result or {}).get("new")
        if stranded:
            ids = ", ".join(str(each_id) for each_id in stranded)
            line += (
                f"\n**{view.payload['division_name']}** — a part of its final classification "
                f"was posted before the job stopped and stands in "
                f"<#{(view.result or {}).get('channel_id')}> (message {ids}): delete it by hand."
            )
        return line

    return {
        FINAL_STANDINGS: Step(
            FINAL_STANDINGS, StepKind.ACT, run, still_due=still_due, describe=describe,
            record=record_posted, not_done=not_done,
        ),
    }
