"""`results.submission.open`: opening a round's results submission on the change queue (#439, 6b).

A round's start timer asks the queue, as the bot, for this change. Its jobs move the round off
NOT_RUN (`start_round`, written through core's season service so that results writes no `rounds`
row itself), create the round's submission channel beside the interaction channel and record it
(`create_channel`, results spec :244), post the opening that pings the league managers' role and
the first session's prompt. Where results are off, the round has nothing to wait for: it ends
FINAL and the change asks the season's wind-down, creating no channel.

**The round's start and the channel's creation cannot be discarded.** Without the start the round
would never open for results and its season could never be completed; without the creation it would
never have its results submitted. Discard on either is refused and only Retry clears it (core spec
:137). A start dropped by Retry, because its round was cancelled meanwhile, leaves nothing owed
(`still_due`). The channel's posts are planned by the creation itself, which knows the channel they
go in.

**A round whose start falls while the queue is stopped** is asked for all the same and waits its
turn: it stays NOT_RUN, and so cancellable, until the queue reaches the opening (Q17). A round
cancelled before then is found no longer due and the opening is dropped.

**No results channel stops the queue**, the stop naming `/results channel results`: the creation
job finds, as it runs, that the division's results channel is unset or gone from the server, and
stops rather than passing over the round. It reads the channel then, from the database, so a Retry
after the channel is set goes through. The round has moved off NOT_RUN by then, as for any refusal
of the creation.

The change runs on the league lane, whatever asked: a timer, the restart or test mode's advance.

**`results.submission.restart`** is the start-up's own: a first paste a restart cut short is
cleared (`clear`: the round's sessions and its submission row, which ends the old conversation), its
channel deleted (`delete_channel`, whose refusal stops the league lane, the lane that asked) and
`results.submission.open` asked afresh as a follow-on, whose channel creation cannot be discarded.
The log line telling the league to re-submit is written in the clearing's save. The clearing cannot
be discarded either: without it the round would take no results until the bot next restarts. It
completes, doing nothing, where its round's row is gone.

**`results.submission.all_cancelled`** is what a submission whose every session was entered as
cancelled hands the league (results spec :363, :379). **It ends the round cancelled** (`end_round`,
its first job, which cannot be discarded: without it the round would never finish and its season
could never be completed): the status, its audit, the division's finishing and the season's move on
are one save of core's, and the round is marked as not held, which sets it apart from a round called
off. There are no Provisional Results to carry the sessions' notes, so it then posts each session's
"This session was cancelled." line in the division's results channel, in running order, each
recorded as that session's results message (the posting job the review's tables use), and the
round's calendar again, struck through, where one was posted; and asks
**`results.submission.close_channel`** on the round's own lane. That says "⏭️" in the submission
channel and deletes it, closing the round's row with the deletion: the conversation ending itself,
so a refusal stops the round's lane alone and the channel keeps its row for the retry. One line in
the log channel records the ending, with whatever a league admin discarded beneath it.
"""
from __future__ import annotations

import dataclasses
from collections.abc import Callable
from datetime import datetime
from typing import TYPE_CHECKING, Any

import aiosqlite

from leaguebot.core.db.database import get_connection
from leaguebot.core.models.change import (
    ChangeOrigin,
    FollowOn,
    GuildUnavailable,
    PlannedStep,
    StepFailedOnDiscord,
    StepKind,
    StepResult,
    Verdict,
)
from leaguebot.core.models.round import RoundFormat, RoundStatus
from leaguebot.core.services.change_queue import (
    ChangeQueue,
    ChangeType,
    CheckContext,
    OutcomeContext,
    Step,
    StepContext,
)
from leaguebot.core.services import season_service
from leaguebot.core.services.calendar_post_service import POST_CALENDAR, calendar_steps
from leaguebot.core.services.cancellation_changes import not_notified
from leaguebot.core.services.cancellation_notice_service import failure_log_lines
from leaguebot.core.services.season_lifecycle_service import WIND_DOWN
from leaguebot.core.services.season_service import set_round_status_on
from leaguebot.core.utils.league_server import league_guild
from leaguebot.results.services import review_posting
from leaguebot.results.services.review_posting import (
    DELETE_CHANNEL,
    POST_SESSION_RESULTS,
    posting_steps,
)
from leaguebot.results.services.result_submission_service import (
    _make_slug,
    expected_session_on,
    get_sessions_for_format,
)
from leaguebot.results.services.submission_steps import (
    CREATE_CHANNEL,
    POST,
    POST_PROMPT,
    create_channel_step,
    post_prompt_step,
    post_step,
)
from leaguebot.results.utils import results_formatter

if TYPE_CHECKING:
    from leaguebot.core.services.season_service import SeasonService

KIND = "results.submission.open"
START_ROUND = "start_round"
POST_OPENING = "post_opening"
#: Where the opening's mention goes, until the creation knows the role is on the server.
_MENTION = "<<mention>>"

#: The restart's clearing of a submission a restart cut short.
RESTART_KIND = "results.submission.restart"
CLEAR = "clear"

#: A submission whose every session was cancelled: the league's notes, then the lane's closing.
ALL_CANCELLED_KIND = "results.submission.all_cancelled"
CLOSE_KIND = "results.submission.close_channel"
ASK_CLOSE = "ask_close"
#: The first job of that change: the round ends cancelled.
END_ROUND = "end_round"

__all__ = [
    "ALL_CANCELLED_KIND", "CLOSE_KIND", "KIND", "RESTART_KIND", "ask_submission_open",
    "ask_submission_restart", "submission_all_cancelled_change", "submission_close_change",
    "submission_open_change", "submission_restart_change",
]

_ROUND = (
    "SELECT r.status, r.division_id, r.round_number, r.format, d.name AS division_name, "
    "s.season_number, c.results_channel_id "
    "FROM rounds r JOIN divisions d ON d.id = r.division_id JOIN seasons s ON s.id = d.season_id "
    "LEFT JOIN division_results_config c ON c.division_id = d.id WHERE r.id = ?"
)


async def _results_enabled(db: aiosqlite.Connection) -> bool:
    row = await (await db.execute("SELECT module_enabled FROM results_module_config")).fetchone()
    return bool(row is not None and row["module_enabled"])


def submission_open_change(*, now: Callable[[], datetime]) -> ChangeType:
    """The `results.submission.open` change type, for the builder to register.

    *now* is the queue's clock, read when the channel's row is written. The payload is
    ``{"round_id": int}``.
    """

    async def check(ctx: CheckContext) -> Verdict:
        async with get_connection(ctx.db_path) as db:
            row = await (await db.execute(_ROUND, (int(ctx.payload["round_id"]),))).fetchone()
        if row is None:
            return Verdict.not_due("the round is gone")
        if row["status"] == RoundStatus.CANCELLED.value:
            return Verdict.not_due("the round was cancelled")
        async with get_connection(ctx.db_path) as db:
            standing = await (
                await db.execute(
                    "SELECT 1 FROM round_submission_channels WHERE round_id = ? AND closed = 0",
                    (int(ctx.payload["round_id"]),),
                )
            ).fetchone()
        if standing is not None:
            # An earlier opening already made the channel (this one waited behind it, as test
            # mode's advance can ask twice): a second would orphan the first's channel.
            return Verdict.not_due("the round's submission is already open")
        return Verdict.go()

    async def not_cancelled(ctx: StepContext) -> bool:
        async with get_connection(ctx.db_path) as db:
            row = await (
                await db.execute("SELECT status FROM rounds WHERE id = ?", (int(ctx.payload["round_id"]),))
            ).fetchone()
        return row is not None and row["status"] != RoundStatus.CANCELLED.value

    async def start_round(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        round_id = int(ctx.payload["round_id"])
        row = await (await db.execute(_ROUND, (round_id,))).fetchone()
        if row is None:
            raise LookupError(f"round {round_id} is not there to start")
        if not await _results_enabled(db):
            # Nobody will ever enter a result, so the round ends here; the division finishing may
            # have been the season's last, which the wind-down judges (the queue drops it if not).
            await set_round_status_on(
                db, round_id, RoundStatus.FINAL, only_from=(RoundStatus.NOT_RUN.value,)
            )
            await season_service.refresh_division_status_on(db, int(row["division_id"]))
            return StepResult(
                result={"results": False},
                follow_ons=(FollowOn(WIND_DOWN, {}, f"Winding the season down after {ctx.what}"),),
            )
        await set_round_status_on(
            db, round_id, RoundStatus.AWAITING_RESULTS, only_from=(RoundStatus.NOT_RUN.value,)
        )
        server = await (
            await db.execute(
                "SELECT interaction_role_id, interaction_channel_id, league_admin_role_id "
                "FROM server_configs WHERE server_id IS NOT NULL"
            )
        ).fetchone()
        interaction_role = None if server is None else server["interaction_role_id"]
        admin_role = None if server is None else server["league_admin_role_id"]
        fmt = RoundFormat(row["format"])
        is_sprint = fmt is RoundFormat.SPRINT
        sessions = ", ".join(
            results_formatter.format_session_label(each, is_sprint=is_sprint)
            for each in get_sessions_for_format(fmt)
        )
        opening = (
            f"✅ Results submission open for **Round {row['round_number']}** "
            f"({row['division_name']}) - {fmt.label}. Sessions: {sessions}.{_MENTION}\n\n"
            "Submit results one driver per line (comma-separated), or type `CANCELLED` to skip a "
            "session."
        )
        first = await expected_session_on(db, round_id)
        slug = _make_slug(row["division_name"])
        create: dict[str, Any] = {
            "name": f"S{row['season_number']}-{slug}-R{row['round_number']}-results",
            "beside_channel_id": None if server is None else server["interaction_channel_id"],
            "role_ids": [each for each in (interaction_role, admin_role) if each],
            "round_number": row["round_number"],
            "division_id": row["division_id"],
            "division_name": row["division_name"],
            "opening": opening,
            "interaction_role_id": interaction_role,
            "first_session": None if first is None else first.value,
        }
        return StepResult(result={"results": True}, then=(PlannedStep(CREATE_CHANNEL, create),))

    async def record_channel(db: aiosqlite.Connection, ctx: StepContext, result: StepResult) -> None:
        """Replace the round's row (a prior closed submission or an orphan of a restart would hit
        the UNIQUE constraint) with the channel just made, in the save that marks the job done."""
        round_id = int(ctx.payload["round_id"])
        await db.execute("DELETE FROM round_submission_channels WHERE round_id = ?", (round_id,))
        await db.execute(
            "INSERT INTO round_submission_channels (round_id, channel_id, created_at, closed) "
            "VALUES (?, ?, ?, 0)",
            (round_id, int(result.result["channel_id"]), now().isoformat()),
        )

    shared_create = create_channel_step(record_channel)

    async def create(ctx: StepContext) -> StepResult:
        """Create the channel, and plan its opening and first prompt, which know where they go.

        The division's results channel must be set and on the server, as the results are posted
        there once they are in: else the queue stops here, naming the command that sets it. It is
        read as the job runs, so a Retry after it is set goes through.
        """
        payload = ctx.step_payload
        guild = await league_guild(ctx.bot)
        if guild is None:
            raise GuildUnavailable("the league's server is not in the cache")
        async with get_connection(ctx.db_path) as db:
            configured = await (
                await db.execute(
                    "SELECT results_channel_id FROM division_results_config WHERE division_id = ?",
                    (int(payload["division_id"]),),
                )
            ).fetchone()
        results_channel = None if configured is None else configured["results_channel_id"]
        if results_channel is None or guild.get_channel(int(results_channel)) is None:
            raise StepFailedOnDiscord(
                f"**{payload['division_name']}** has no results channel on the server, so round "
                f"{payload['round_number']}'s results submission cannot open: set one with "
                "`/results channel results`, then press Retry"
            )
        made = await shared_create.run(ctx)
        channel_id = made.result["channel_id"]
        # The league managers enter the results, so they are the ones pinged, where their role is
        # on the server; the division's role cannot see this channel (#136).
        role_id = payload.get("interaction_role_id")
        pinged = [int(role_id)] if role_id and guild.get_role(int(role_id)) is not None else []
        planned = [
            PlannedStep(
                POST_OPENING,
                {
                    "channel_id": channel_id,
                    "text": payload["opening"].replace(
                        _MENTION, f" <@&{pinged[0]}>" if pinged else ""
                    ),
                    "mention_role_ids": pinged,
                },
            )
        ]
        if payload.get("first_session") is not None:
            planned.append(
                PlannedStep(
                    POST_PROMPT,
                    {
                        "channel_id": channel_id,
                        "round_id": int(ctx.payload["round_id"]),
                        "session_type": payload["first_session"],
                    },
                )
            )
        return dataclasses.replace(made, then=tuple(planned))

    async def would_never_have_results(ctx: StepContext) -> str:
        payload = ctx.step_payload
        return (
            f"round {payload['round_number']} in **{payload['division_name']}** "
            "would never have its results submitted"
        )

    async def describe_create(ctx: StepContext) -> str:
        payload = ctx.step_payload
        return (
            f"creating the results submission channel of round {payload['round_number']} in "
            f"**{payload['division_name']}**"
        )

    async def would_never_open(ctx: StepContext) -> str:
        called = await season_service.round_called(ctx.db_path, int(ctx.payload["round_id"]))
        return f"{called} would never open for results, and its season could never be completed"

    async def describe_start(_ctx: StepContext) -> str:
        return "starting the round"

    def opened(_ctx: OutcomeContext) -> str:
        return ""

    return ChangeType(
        kind=KIND,
        opening=(PlannedStep(START_ROUND),),
        steps={
            START_ROUND: Step(
                START_ROUND, StepKind.SAVE, start_round, still_due=not_cancelled,
                describe=describe_start, undiscardable=would_never_open,
            ),
            CREATE_CHANNEL: dataclasses.replace(
                shared_create, run=create, still_due=not_cancelled, describe=describe_create,
                undiscardable=would_never_have_results,
            ),
            POST_OPENING: dataclasses.replace(
                post_step(POST_OPENING, describing="posting the results submission's opening"),
                still_due=not_cancelled,
            ),
            POST_PROMPT: dataclasses.replace(post_prompt_step(), still_due=not_cancelled),
        },
        check=check,
        key=lambda payload: f"{KIND}:{payload['round_id']}",
        doing=lambda _payload: "opening a round's results submission",
        outcome=opened,
    )


async def ask_submission_open(queue: ChangeQueue, db_path: str, round_id: int) -> None:
    """Ask the queue, as the bot, to open *round_id*'s results submission.

    The round's number is read here, on a connection of its own, to name the change.
    """
    async with get_connection(db_path) as db:
        row = await (
            await db.execute("SELECT round_number FROM rounds WHERE id = ?", (round_id,))
        ).fetchone()
    number = "a round" if row is None else f"round {row['round_number']}"
    await queue.ask(
        KIND, {"round_id": round_id}, what=f"opening {number}'s results submission",
        origin=ChangeOrigin.BOT,
    )


def submission_restart_change() -> ChangeType:
    """The `results.submission.restart` change type, for the builder to register.

    The payload is ``{"round_id": int, "channel_id": int}``, the channel the restart found the
    round's submission open in. It is due while the round's submission row stands open in that
    channel and in no review; a round cancelled, one whose row is gone and one whose submission
    has since been opened in another channel are dropped. A discarded deletion leaves the channel
    standing with no row naming it, so the log channel is told, once, to delete it by hand.
    """

    async def check(ctx: CheckContext) -> Verdict:
        async with get_connection(ctx.db_path) as db:
            row = await (
                await db.execute(
                    "SELECT closed, in_penalty_review FROM round_submission_channels "
                    "WHERE round_id = ? AND channel_id = ?",
                    (int(ctx.payload["round_id"]), int(ctx.payload["channel_id"])),
                )
            ).fetchone()
        if row is None or row["closed"] or row["in_penalty_review"]:
            return Verdict.not_due("the round has no submission left open")
        return Verdict.go()

    async def clear(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        round_id = int(ctx.payload["round_id"])
        row = await (
            await db.execute(
                "SELECT rsc.channel_id, r.round_number FROM round_submission_channels rsc "
                "JOIN rounds r ON r.id = rsc.round_id WHERE rsc.round_id = ?",
                (round_id,),
            )
        ).fetchone()
        if row is None:
            return StepResult(result={"cleared": False})
        await db.execute("DELETE FROM session_results WHERE round_id = ?", (round_id,))
        await db.execute("DELETE FROM round_submission_channels WHERE round_id = ?", (round_id,))
        line = (
            "System | Bot restarted mid-result-submission | Notice\n"
            f"  round: R{row['round_number']}\n"
            "  Sessions submitted before restart have been cleared. "
            "Submission wizard re-opened — please re-submit all sessions."
        )
        return StepResult(
            result={"cleared": True},
            lines=(line,),
            then=(
                PlannedStep(
                    DELETE_CHANNEL,
                    {
                        "round_id": round_id,
                        "channel_id": int(row["channel_id"]),
                        "reason": "Orphaned submission channel cleanup on restart",
                        "what": "cut-short submission channel",
                        "round_number": int(row["round_number"]),
                    },
                ),
            ),
            follow_ons=(
                FollowOn(
                    KIND, {"round_id": round_id},
                    f"Opening round {row['round_number']}'s results submission afresh",
                ),
            ),
        )

    async def describe_clear(_ctx: StepContext) -> str:
        return "clearing the results submission a restart cut short"

    async def would_take_no_results(ctx: StepContext) -> str:
        called = await season_service.round_called(ctx.db_path, int(ctx.payload["round_id"]))
        return f"{called} would take no results until the bot next restarts"

    def cleared(_ctx: OutcomeContext) -> str:
        return ""

    def left_standing(ctx: OutcomeContext) -> tuple[str, ...]:
        """One line for the channel a league admin discarded the deletion of: no row names it any
        more, so the log channel is the only record of it."""
        lines: list[str] = []
        for view in ctx.steps:
            if view.name == DELETE_CHANNEL and "discarded" in (view.result or {}):
                lines.append(
                    f"⚠️ The {view.payload['what']} <#{view.payload['channel_id']}> of round "
                    f"{view.payload['round_number']} was not deleted, and no record of it is "
                    "kept: delete it by hand."
                )
        return tuple(lines)

    return ChangeType(
        kind=RESTART_KIND,
        opening=(PlannedStep(CLEAR),),
        steps={
            CLEAR: Step(
                CLEAR, StepKind.SAVE, clear, describe=describe_clear,
                undiscardable=would_take_no_results,
            ),
            DELETE_CHANNEL: posting_steps()[DELETE_CHANNEL],
        },
        check=check,
        key=lambda payload: f"{RESTART_KIND}:{payload['round_id']}",
        doing=lambda _payload: "clearing a results submission a restart cut short",
        outcome=cleared,
        finished_lines=left_standing,
    )


async def ask_submission_restart(
    queue: ChangeQueue, db_path: str, round_id: int, channel_id: int,
) -> None:
    """Ask the queue, as the bot, to clear *round_id*'s submission a restart cut short, found open
    in *channel_id*, and open it afresh. The round's number is read here to name the change."""
    async with get_connection(db_path) as db:
        row = await (
            await db.execute("SELECT round_number FROM rounds WHERE id = ?", (round_id,))
        ).fetchone()
    number = "a round" if row is None else f"round {row['round_number']}"
    await queue.ask(
        RESTART_KIND, {"round_id": round_id, "channel_id": channel_id},
        what=f"clearing {number}'s results submission a restart cut short",
        origin=ChangeOrigin.BOT,
    )


async def _submission_standing(ctx: CheckContext) -> Verdict:
    async with get_connection(ctx.db_path) as db:
        row = await (
            await db.execute(
                "SELECT 1 FROM round_submission_channels WHERE round_id = ? AND channel_id = ?",
                (int(ctx.payload["round_id"]), int(ctx.payload["channel_id"])),
            )
        ).fetchone()
    return Verdict.go() if row is not None else Verdict.not_due("the round's submission is gone")


def submission_all_cancelled_change(
    *, seasons: "SeasonService", now: Callable[[], datetime],
) -> ChangeType:
    """The `results.submission.all_cancelled` change type, for the builder to register, on the
    league lane.

    The payload is the last turn's: ``round_id``, ``channel_id``, ``round_number`` and
    ``sessions``, each ``{"session_id", "session_type"}`` of the round's cancelled sessions in
    running order. *seasons* posts the calendar and *now* is the queue's clock, read when the
    round's ending is audited.

    Its first job, ``end_round`` (a save, **never to be discarded**: without it the round would
    never finish and its season could never be completed), ends the round CANCELLED through core's
    ``end_round_not_held_on``: the status and its audit, the division's refresh and the season's
    move on, in one save. It then plans one posting job for each session (the line, in the results
    channel, saved as the session's results message; a division with no results channel has none
    to post) and the round's calendar again, struck through, where one stands; its follow-on is the
    season's wind-down. Where the save finds a session not cancelled after all, or the round past
    the point it can end from, it writes and plans nothing. ``ask_close`` asks the closing of the
    channel of the round's lane in its own save either way. A discarded line leaves the others and
    the closing to go on.
    """

    async def end_round(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        payload = ctx.payload
        round_id = int(payload["round_id"])
        statuses = [
            each["status"]
            for each in await (
                await db.execute(
                    "SELECT status FROM session_results WHERE round_id = ?", (round_id,)
                )
            ).fetchall()
        ]
        if (
            not statuses
            or any(status != "CANCELLED" for status in statuses)
            or await expected_session_on(db, round_id) is not None
        ):
            return StepResult(result={"ended": False})
        previous = await season_service.end_round_not_held_on(
            db, round_id, actor_id=ctx.actor_id or 0, actor_name=ctx.actor_name or "System",
            now=now(),
        )
        if previous is None:
            return StepResult(result={"ended": False})
        row = await (await db.execute(
            "SELECT r.division_id, r.round_number, r.track_name, d.name AS division_name, "
            "d.season_id, s.season_number FROM rounds r JOIN divisions d ON d.id = r.division_id "
            "JOIN seasons s ON s.id = d.season_id WHERE r.id = ?",
            (round_id,),
        )).fetchone()
        assert row is not None
        posts = tuple(
            PlannedStep(POST_SESSION_RESULTS, {
                "session_id": int(session["session_id"]), "round_id": round_id,
                "label": "Provisional Results", "round_number": payload["round_number"],
                "session": f"{str(session['session_type']).replace('_', ' ').title()} results",
                "remedy": "/results rounds sync",
            })
            for session in payload["sessions"]
        )
        target = {
            "division_id": int(row["division_id"]), "division_name": str(row["division_name"]),
            "season_id": int(row["season_id"]), "season_number": int(row["season_number"]),
            "round_number": int(row["round_number"]), "track_name": row["track_name"],
        }
        return StepResult(
            result={"ended": True, "previous": previous, **target},
            then=(
                *posts,
                PlannedStep(POST_CALENDAR, {**target, "round_ids": [round_id], "only_if_posted": True}),
            ),
            follow_ons=(FollowOn(WIND_DOWN, {}, f"Winding the season down after {ctx.what}"),),
        )

    async def describe_end_round(_ctx: StepContext) -> str:
        return "ending the round cancelled, every session being entered as not held"

    async def would_never_finish(ctx: StepContext) -> str:
        round_id = int(ctx.payload["round_id"])
        called = await season_service.round_called(ctx.db_path, round_id)
        async with get_connection(ctx.db_path) as db:
            row = await (await db.execute(
                "SELECT s.season_number FROM rounds r JOIN divisions d ON d.id = r.division_id "
                "JOIN seasons s ON s.id = d.season_id WHERE r.id = ?", (round_id,),
            )).fetchone()
        season = "its season" if row is None else f"season {row['season_number']}"
        return f"{called} would never finish, and {season} could not be completed"

    def plan(payload: dict[str, Any]) -> tuple[PlannedStep, ...]:
        return (PlannedStep(END_ROUND), PlannedStep(ASK_CLOSE))

    async def ask_close(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        payload = ctx.payload
        asked = {"round_id": int(payload["round_id"]), "channel_id": int(payload["channel_id"])}
        return StepResult(
            result={"asked": True},
            follow_ons=(FollowOn(
                CLOSE_KIND, asked,
                f"closing round {payload['round_number']}'s submission channel",
            ),),
        )

    async def describe_ask_close(_ctx: StepContext) -> str:
        return "asking for the submission channel to be closed"

    def ended_lines(ctx: OutcomeContext) -> tuple[str, ...]:
        """The one line recording the ending, with what a league admin discarded beneath it; none
        where the ending found nothing to end."""
        ended = next(
            (view.result or {} for view in ctx.steps if view.name == END_ROUND), {}
        )
        if not ended.get("ended"):
            return ()
        line = (
            f"{ctx.named} | ROUND_CANCELLED | Success\n"
            f"  division: {ended['division_name']}\n"
            f"  round: {ended['round_number']}\n"
            "  reason: every session was entered as not held"
            + failure_log_lines(not_notified(ctx))
            + "".join(f"\n  {text}" for text in review_posting.not_done(ctx))
        )
        return (line,)

    return ChangeType(
        kind=ALL_CANCELLED_KIND,
        opening=(),
        plan=plan,
        steps={
            END_ROUND: Step(
                END_ROUND, StepKind.SAVE, end_round, describe=describe_end_round,
                undiscardable=would_never_finish,
            ),
            POST_SESSION_RESULTS: posting_steps()[POST_SESSION_RESULTS],
            **calendar_steps(seasons),
            ASK_CLOSE: Step(ASK_CLOSE, StepKind.SAVE, ask_close, describe=describe_ask_close),
        },
        check=_submission_standing,
        key=lambda payload: f"{ALL_CANCELLED_KIND}:{payload['round_id']}",
        doing=lambda payload: f"posting round {payload['round_number']}'s cancelled sessions",
        outcome=lambda _ctx: "",
        turn_channel=lambda payload: int(payload["channel_id"]),
        finished_lines=ended_lines,
    )


def submission_close_change() -> ChangeType:
    """The `results.submission.close_channel` change type, for the builder to register, on the
    round's own lane (``channel:<submission channel>``).

    The payload is ``round_id`` and ``channel_id``. It says "⏭️" in the channel and deletes it;
    the deletion's record closes the round's row, so a channel that could not be deleted keeps it
    for the retry. The conversation stands while the row names the channel.
    """

    def lane(payload: dict[str, Any]) -> str:
        return f"channel:{int(payload['channel_id'])}"

    async def conversation_open(db: aiosqlite.Connection, payload: dict[str, Any]) -> bool:
        row = await (
            await db.execute(
                "SELECT 1 FROM round_submission_channels WHERE round_id = ? AND channel_id = ?",
                (int(payload["round_id"]), int(payload["channel_id"])),
            )
        ).fetchone()
        return row is not None

    def plan(payload: dict[str, Any]) -> tuple[PlannedStep, ...]:
        channel_id = int(payload["channel_id"])
        return (
            PlannedStep(POST, {
                "channel_id": channel_id,
                "text": "⏭️ All sessions for this round were cancelled — no penalty review required.",
            }),
            PlannedStep(DELETE_CHANNEL, {
                "channel_id": channel_id, "round_id": int(payload["round_id"]),
                "what": "submission channel", "reason": "Results submission complete",
            }),
        )

    async def record_closed(
        db: aiosqlite.Connection, ctx: StepContext, _result: StepResult
    ) -> None:
        await db.execute(
            "UPDATE round_submission_channels SET closed = 1 WHERE round_id = ?",
            (int(ctx.step_payload["round_id"]),),
        )

    return ChangeType(
        kind=CLOSE_KIND,
        opening=(),
        plan=plan,
        steps={
            POST: post_step(describing="posting the closing line in the submission channel"),
            DELETE_CHANNEL: dataclasses.replace(
                posting_steps()[DELETE_CHANNEL], record=record_closed
            ),
        },
        check=_submission_standing,
        key=lambda payload: f"{CLOSE_KIND}:{payload['round_id']}",
        doing=lambda _payload: "closing a round's submission channel",
        outcome=lambda _ctx: "",
        lane=lane,
        conversation_open=conversation_open,
        writes=("round_submission_channels",),
    )
