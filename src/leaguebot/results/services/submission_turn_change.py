"""`results.submission.turn` and `.config`: each results paste a turn on the round's lane (#439, 6b).

**A resubmission's pastes are turns of the same shape** (`results.resubmission.turn` and
`.config`, built by `resubmission_changes`, which share these jobs): a paste is judged against the
session the resubmission then expects, and an accepted one is held as a draft (`save_draft`, in
place of `save_session`) while the round's own results stand. The last draft hands the swap to the
league (see `resubmission_changes`).

A league manager's message in a round's open submission channel is marked ⏳ at once (`take_paste`)
and `results.submission.turn` is asked for it, as the member's, on the round's own lane
(``channel:<submission channel>``). Nothing waits in memory for the next paste. When its turn comes
the change judges the paste against the session the round then expects (`judge`): a refused one is
answered with its errors, logged as rejected (results spec :257) and the session asked for again;
so is one whose round was cancelled or whose fastest-lap override is not among the finishers, and
every paste held back or refused at its save is logged the same way, with a `reason:` line
(results spec :261). An accepted one is held back while another round of the division is being amended, or the
submission has been closed by a cancellation (`settle`), else saved scored by the season's points
configuration (`save_session`), the channel told so and asked for the next session. The save reads
again, inside itself, whether a cancellation's save has come since the turn was settled; where it
has, it saves nothing and the channel is told the round has been cancelled (#439, slice 8). A message typed
while a turn waits in the channel is turned away at once, with a note and a line in the log.

**The points configuration is a second change.** Where the season has several attached, the turn
posts the picker (`post_config_choice`) and ends, saving nothing; the press asks
`results.submission.config` on the same lane, carrying everything the paste was judged to be, and
that change settles and saves it. The picker lives in memory, as it did when a coroutine waited on
it: a restart leaves it dead, and the restart's own recovery clears a first paste cut short.

**The last paste hands the review to the league** (`results.review.open`, a follow-on of the save),
the channel told with the number of its first job (results spec :382). Its ⏳ stays: the review's
prompt change clears it once the prompt appears, the member seeing nothing else meanwhile.

**Every session cancelled** hands `results.submission.all_cancelled` to the league (a follow-on of
the save), which posts each session's cancelled line in the results channel and asks the channel's
closing back of the round's lane (see `submission_changes`).

**A conversation that has ended drops its turns.** The conversation is the round's submission row;
a restart's clearing deletes it. A row closed by a cancellation still stands, so that the paste
typed before the cancellation is answered, and nothing saved, when its turn comes.
"""
from __future__ import annotations

import dataclasses
import json
from collections.abc import Awaitable, Callable
from typing import Any

import aiosqlite
import discord

from leaguebot.core.db.database import get_connection
from leaguebot.core.models.change import FollowOn, PlannedStep, StepKind, StepResult, Verdict
from leaguebot.core.models.round import RoundFormat
from leaguebot.core.services import typed_turns
from leaguebot.core.services.change_queue import (
    ChangeQueue,
    ChangeType,
    CheckContext,
    OutcomeContext,
    Step,
    StepContext,
)
from leaguebot.core.utils.channel_guard import is_league_manager
from leaguebot.core.utils.log_lines import refusal_line, reply_reason
from leaguebot.core.utils.member_names import member_named
from leaguebot.results.models.points_config import SessionType
from leaguebot.results.services import result_submission_service as submissions
from leaguebot.results.services import review_open_change, season_points_service
from leaguebot.results.services.submission_changes import ALL_CANCELLED_KIND
from leaguebot.results.services.submission_steps import (
    POST,
    POST_PROMPT,
    post_choice_step,
    post_prompt_step,
    post_step,
)
from leaguebot.results.utils import results_formatter

KIND = "results.submission.turn"
CONFIG_KIND = "results.submission.config"
RESUBMISSION_KIND = "results.resubmission.turn"
RESUBMISSION_CONFIG_KIND = "results.resubmission.config"
SWAP_KIND = "results.resubmission.swap"
JUDGE = "judge"
SETTLE = "settle"
SAVE_SESSION = "save_session"
SAVE_DRAFT = "save_draft"
POST_CONFIG_CHOICE = "post_config_choice"
POST_OPENING_REVIEW = "post_opening_review"

__all__ = [
    "CONFIG_KIND", "KIND", "NO_CONFIG", "RESUBMISSION_CONFIG_KIND", "RESUBMISSION_KIND",
    "SWAP_KIND", "submission_config_change", "submission_turn_change", "take_paste", "turn_steps",
]

#: The configuration name a draft keeps for a season with none attached. A draft whose
#: configuration is unset is still awaiting its choice (`expected_resubmitted_session_on`), so a
#: season with nothing to choose stores this, and the swap saves the session with none.
NO_CONFIG = ""

#: The round whose submission is held in a channel, with what its turns need to know of it.
_ROUND = (
    "SELECT rsc.round_id, rsc.channel_id, rsc.closed, rsc.resubmitting, r.division_id, "
    "r.round_number, r.format, d.name AS division_name, s.id AS season_id, s.season_number "
    "FROM round_submission_channels rsc JOIN rounds r ON r.id = rsc.round_id "
    "JOIN divisions d ON d.id = r.division_id JOIN seasons s ON s.id = d.season_id "
    "WHERE rsc.channel_id = ?"
)

#: The tables a turn's saves may write.
_WRITES = (
    "session_results", "qualifying_session_results", "race_session_results",
    "round_submission_channels", "resubmission_drafts",
)

ServerConfig = Callable[[], Awaitable[Any]]


def _label(session: SessionType, row: aiosqlite.Row) -> str:
    return results_formatter.format_session_label(
        session, is_sprint=RoundFormat(row["format"]) is RoundFormat.SPRINT
    )


def _turn_channel(payload: dict[str, Any]) -> int:
    return int(payload["channel_id"])


def _lane(payload: dict[str, Any]) -> str:
    return f"channel:{int(payload['channel_id'])}"


async def _conversation_open(db: aiosqlite.Connection, payload: dict[str, Any]) -> bool:
    row = await (
        await db.execute(
            "SELECT 1 FROM round_submission_channels WHERE channel_id = ?",
            (int(payload["channel_id"]),),
        )
    ).fetchone()
    return row is not None


async def take_paste(
    bot: Any, message: discord.Message, *, queue: ChangeQueue, config: Any
) -> None:
    """What the results cog does with a message typed in a channel: nothing, unless it is a league
    manager's in a round's open first submission; then it is turned away where a turn waits in the
    channel (the refusal logged), else marked ⏳ and asked as a turn.

    *config* is the server configuration, read by the cog, which holds the league managers' role.
    """
    async with get_connection(bot.db_path) as db:
        row = await (await db.execute(_ROUND, (message.channel.id,))).fetchone()
        if row is None or row["closed"]:
            return
        if not isinstance(message.author, discord.Member) or config is None or not (
            is_league_manager(config, message.author)
        ):
            return
        resubmitting = bool(row["resubmitting"])
        expected = await (
            submissions.expected_resubmitted_session_on if resubmitting
            else submissions.expected_session_on
        )(db, int(row["round_id"]))
    if await typed_turns.turn_waiting(bot.db_path, queue.types, message.channel.id):
        await typed_turns.turn_away(message, typed_turns.TURN_AWAY_NOTE)
        waiting = "its session" if expected is None else _label(expected, row)
        await bot.output_router.post_log(
            refusal_line(
                member_named(message.author.display_name, message.author.id),
                "A results paste",
                f"Round {row['round_number']} of season {row['season_number']} "
                f"({row['division_name']}) is still waiting on an "
                f"earlier paste for {waiting}",
            )
        )
        return
    await typed_turns.mark(message)
    await queue.ask(
        RESUBMISSION_KIND if resubmitting else KIND,
        {
            "channel_id": message.channel.id,
            "message_id": message.id,
            "author_id": message.author.id,
            "content": message.content,
            "attachments": [attachment.url for attachment in message.attachments],
            "typed_at": message.created_at.isoformat(),
        },
        actor=message.author,
        what=(
            f"a resubmitted results paste for round {row['round_number']}" if resubmitting
            else f"a results paste for round {row['round_number']}"
        ),
    )


def _say(channel_id: int, text: str) -> PlannedStep:
    return PlannedStep(POST, {"channel_id": channel_id, "text": text})


def _refused_paste_line(
    named: str, *, season_number: object, division_name: object, round_number: object,
    session: str, content: str, reason: str | None = None,
) -> str:
    """The log channel's line for a results paste refused at its turn (results spec :261): who
    pasted it, the season, division, round and session, the paste as typed (to 500 characters)
    and, where *reason* is given, a `reason:` line beneath the round's.

    A paste refused for its errors gives no reason, since the errors are its reason and the
    channel was told them; every other refusal gives the first line of what the channel was told.
    """
    why = "" if reason is None else f"  reason: {reason}\n"
    return (
        f"{named} | RESULT_SUBMISSION_REJECTED | \n"
        f"  season: {season_number}, division: {division_name!r}\n"
        f"  round: {round_number}, session: {session}\n"
        f"{why}"
        f"  input:\n```\n{content[:500]}\n```"
    )


def _refusal_of(named: str, payload: dict[str, Any], told: str) -> str:
    """The line for the paste a settled turn's *payload* carries, refused with what the channel
    was *told*."""
    return _refused_paste_line(
        named, season_number=payload["season_number"], division_name=payload["division_name"],
        round_number=payload["round_number"], session=str(payload["session_type"]),
        content=str(payload["input"]), reason=reply_reason(told),
    )


def turn_steps(
    queue: ChangeQueue, server_config: ServerConfig, *, resubmission: bool = False
) -> dict[str, Step]:
    """The jobs a dialogue's turn and its configuration change share, keyed by their names.

    With *resubmission* the turn judges against the session a resubmission expects and holds an
    accepted one as a draft (`save_draft`), in place of a first submission's `save_session`.
    """
    held_verb = "received" if resubmission else "saved"

    async def judge(ctx: StepContext) -> StepResult:
        payload = ctx.payload
        channel_id = int(payload["channel_id"])
        mark = {"channel_id": channel_id, "message_id": int(payload["message_id"])}
        clear = (PlannedStep(typed_turns.CLEAR_MARK, mark),)
        async with get_connection(ctx.db_path) as db:
            row = await (await db.execute(_ROUND, (channel_id,))).fetchone()
            if row is not None and bool(row["resubmitting"]) != resubmission:
                row = None
            expected = None if row is None else await (
                submissions.expected_resubmitted_session_on if resubmission
                else submissions.expected_session_on
            )(db, int(row["round_id"]))
        if row is None or expected is None:
            return StepResult(result={"judged": False}, then=clear)
        round_id = int(row["round_id"])
        label = _label(expected, row)
        content = str(payload["content"]).strip()
        session_value = expected.value
        season_number, division_name = row["season_number"], row["division_name"]
        round_number = row["round_number"]

        def refused(reason: str | None = None) -> str:
            return _refused_paste_line(
                ctx.named, season_number=season_number, division_name=division_name,
                round_number=round_number, session=session_value, content=content, reason=reason,
            )

        closed = await submissions.closed_by_cancellation(ctx.db_path, round_id)
        if closed:
            return StepResult(
                result={"refused": "closed"}, lines=(refused(reply_reason(closed)),),
                then=(_say(channel_id, closed), *clear),
            )
        base: dict[str, Any] = {
            "channel_id": channel_id,
            "round_id": round_id,
            "division_id": int(row["division_id"]),
            "season_id": int(row["season_id"]),
            "season_number": int(row["season_number"]),
            "round_number": int(row["round_number"]),
            "division_name": row["division_name"],
            "session_type": expected.value,
            "input": content[:500],
            "label": label,
            "submitted_by": int(payload["author_id"]),
            "mark": mark,
        }
        if content.upper() == "CANCELLED":
            settle_cancelled = {
                **base, "status": "CANCELLED", "rows": [], "fl_override": None,
                "config_name": None,
                "held_then": f"Type `CANCELLED` for **{label}** again then.",
                "saved_text": f"✅ **{label}** marked as CANCELLED.",
            }
            return StepResult(
                result={"judged": "cancelled"},
                then=(PlannedStep(SETTLE, settle_cancelled), *clear),
            )

        lines = content.splitlines()
        current_of = await submissions.current_accounts(ctx.db_path)
        fl_override, lines = submissions.extract_current_fl_override(lines, expected, current_of)
        if resubmission:
            # Checked against the resubmission's own drafts, not the stored results being
            # replaced: those may be wrong in exactly the way that made the manager resubmit.
            async with get_connection(ctx.db_path) as db:
                drafted = await submissions.resubmission_drafts_on(db, round_id)
            other = submissions.collected_team_assignments(drafted, expected)
        else:
            other = await submissions.other_active_team_assignments(ctx.db_path, round_id, expected)
        data = await submissions._build_division_validation_data(int(row["division_id"]), ctx.bot)
        errors, parsed = submissions.split_validation(
            submissions.validate_submission_block(
                lines, expected, data.division_driver_ids, data.team_of_role,
                data.reserve_team_role_id, data.driver_team_map, data.reserve_driver_ids,
                other_active_assignments=other, current_of=current_of,
                team_names=data.team_names, team_of_shorthand=data.team_of_shorthand,
            )
        )
        again = PlannedStep(
            POST_PROMPT,
            {"channel_id": channel_id, "round_id": round_id, "session_type": expected.value},
        )
        if errors:
            error_list = "\n".join(f"• {e}" for e in errors)
            return StepResult(
                result={"refused": "validation"}, lines=(refused(),),
                then=(
                    _say(channel_id, f"❌ Validation failed:\n{error_list}\nPlease correct and resubmit."),
                    again, *clear,
                ),
            )
        if fl_override is not None and fl_override not in {r.driver_user_id for r in parsed}:
            return StepResult(
                result={"refused": "fastest lap"},
                lines=(refused(f"FL override <@{fl_override}> is not in the submitted results"),),
                then=(
                    _say(
                        channel_id,
                        f"❌ FL override <@{fl_override}> is not in the submitted results. "
                        "Please correct and resubmit.",
                    ),
                    again, *clear,
                ),
            )

        if expected.is_qualifying:
            driver_rows = [
                submissions._row_dict_from_qualifying(r)
                for r in submissions._rows_of_kind(parsed, submissions.ParsedQualifyingRow)
            ]
        else:
            driver_rows = [
                submissions._row_dict_from_race(r)
                for r in submissions._rows_of_kind(parsed, submissions.ParsedRaceRow)
            ]
        accepted = (
            f"{ctx.named} | RESULT_SUBMISSION_ACCEPTED | Success\n"
            f"  season: {row['season_number']}, division: {row['division_name']!r}\n"
            f"  round: {row['round_number']}, session: {expected.value}\n"
            f"  input:\n```\n{content[:500]}\n```"
        )
        settle_paste = {
            **base, "status": "ACTIVE", "rows": driver_rows, "fl_override": fl_override,
            "held_then": f"Paste **{label}** again then.",
            "saved_text": f"✅ **{label}** results {held_verb}.",
            "accepted_line": accepted,
        }
        # What the configuration's choice has to say is said with the save, in one post.
        said = f"✅ **{label}** results {held_verb}."
        config_names = await season_points_service.get_attached_config_names(
            ctx.db_path, int(row["season_id"])
        )
        if len(config_names) == 1:
            return StepResult(
                result={"judged": "single configuration"},
                then=(
                    PlannedStep(SETTLE, {
                        **settle_paste, "config_name": config_names[0],
                        "saved_text": f"✅ Auto-selected config **{config_names[0]}** "
                                      f"(only one attached).\n{said}",
                    }),
                    *clear,
                ),
            )
        if config_names:
            return StepResult(
                result={"judged": "configuration to choose"},
                then=(
                    PlannedStep(POST_CONFIG_CHOICE, {
                        "channel_id": channel_id,
                        "text": "🔧 Select the points configuration for this session:",
                        "config_names": config_names,
                        "settle": {**settle_paste, "mark": None},
                        "session": f"the {label} of round {row['round_number']} "
                                   f"({row['division_name']})",
                    }),
                    *clear,
                ),
            )
        return StepResult(
            result={"judged": "no configuration"},
            then=(
                PlannedStep(SETTLE, {
                    **settle_paste, "config_name": NO_CONFIG if resubmission else None,
                    "saved_text": said if resubmission else (
                        "⚠️ No points configuration attached to this season. "
                        f"Results will be saved without a config.\n{said}"
                    ),
                }),
                *clear,
            ),
        )

    async def settle(ctx: StepContext) -> StepResult:
        """Hold the session back where its round's submission was closed by a cancellation or
        another round of the division is being amended, else save it. Read as it is about to be
        written, so an amendment opened while the manager chose the configuration still holds. A
        cancellation saved after this, before the save, is read again by `save_session`."""
        payload = ctx.step_payload
        channel_id = int(payload["channel_id"])
        round_id = int(payload["round_id"])
        held = await submissions.closed_by_cancellation(ctx.db_path, round_id) or (
            await submissions.held_by_amendment(
                ctx.db_path, round_id, int(payload["division_id"]), then=str(payload["held_then"])
            )
        )
        if held:
            return StepResult(
                result={"held": True}, lines=(_refusal_of(ctx.named, payload, held),),
                then=(_say(channel_id, held),),
            )
        return StepResult(
            result={"held": False},
            then=(PlannedStep(SAVE_DRAFT if resubmission else SAVE_SESSION, dict(payload)),),
        )

    async def save_session(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        payload = ctx.step_payload
        round_id = int(payload["round_id"])
        session = SessionType(payload["session_type"])
        channel_id = int(payload["channel_id"])
        closed = await submissions.closed_by_cancellation_on(db, round_id)
        if closed:
            # A cancellation's save came between the settling and this save: both hold the write
            # lock in turn, so it is read here and the session is not saved against its round.
            return StepResult(
                result={"saved": False, "closed": True},
                lines=(_refusal_of(ctx.named, payload, closed),),
                then=(_say(channel_id, closed),),
            )
        already = await (
            await db.execute(
                "SELECT 1 FROM session_results WHERE round_id = ? AND session_type = ?",
                (round_id, session.value),
            )
        ).fetchone()
        if already is not None:
            # Two presses of one picker reach the queue; the first saved it.
            return StepResult(result={"saved": False})
        saved_id = await submissions._save_session_result_in_tx(
            db, round_id, int(payload["division_id"]), session, str(payload["status"]),
            payload["config_name"], int(payload["submitted_by"]), list(payload["rows"]),
            payload["fl_override"],
        )
        if payload["config_name"] is not None:
            await submissions._apply_points_in_tx(
                db, saved_id, int(payload["season_id"]), str(payload["config_name"]), session
            )
        lines = tuple(
            line for line in (payload.get("accepted_line"), payload.get("choice_line")) if line
        )
        then: list[PlannedStep] = [_say(channel_id, str(payload["saved_text"]))]
        upcoming = await submissions.expected_session_on(db, round_id)
        if upcoming is not None:
            then.append(PlannedStep(
                POST_PROMPT,
                {"channel_id": channel_id, "round_id": round_id, "session_type": upcoming.value},
            ))
            return StepResult(result={"saved": True}, lines=lines, then=tuple(then))
        entered = await (
            await db.execute(
                "SELECT id, session_type, status FROM session_results WHERE round_id = ?",
                (round_id,),
            )
        ).fetchall()
        if all(found["status"] == "CANCELLED" for found in entered):
            # No tables to carry the sessions' notes and no review: the league posts them in the
            # results channel and asks the channel's closing back of this lane (results :363, :379).
            order = list(SessionType)
            cancelled = sorted(
                ((found["session_type"], int(found["id"])) for found in entered),
                key=lambda pair: order.index(SessionType(pair[0])),
            )
            return StepResult(
                result={"saved": True, "all_cancelled": True}, lines=lines, then=tuple(then),
                follow_ons=(FollowOn(
                    ALL_CANCELLED_KIND,
                    {
                        "round_id": round_id, "channel_id": channel_id,
                        "round_number": int(payload["round_number"]),
                        "sessions": [
                            {"session_id": session_id, "session_type": session_type}
                            for session_type, session_id in cancelled
                        ],
                    },
                    f"posting round {payload['round_number']}'s cancelled sessions",
                ),),
            )
        then.append(PlannedStep(POST_OPENING_REVIEW, {"channel_id": channel_id, "round_id": round_id}))
        asked: dict[str, Any] = {
            "round_id": round_id,
            "round_number": int(payload["round_number"]),
            "division_name": payload["division_name"],
            "label": "Provisional Results",
            "publish": True,
        }
        if payload.get("mark") is not None:
            asked["mark"] = payload["mark"]
        return StepResult(
            result={"saved": True, "review_asked": True}, lines=lines, then=tuple(then),
            follow_ons=(FollowOn(
                review_open_change.KIND, asked,
                f"the penalty review of round {payload['round_number']}",
            ),),
        )

    async def save_draft(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        """Hold the session as a draft of the resubmission, then ask for the next session, or, with
        every session drafted, hand the swap of the round's results to the league."""
        payload = ctx.step_payload
        round_id = int(payload["round_id"])
        channel_id = int(payload["channel_id"])
        session = SessionType(payload["session_type"])
        already = await (
            await db.execute(
                "SELECT 1 FROM resubmission_drafts WHERE round_id = ? AND session_type = ? "
                "AND (config_name IS NOT NULL OR status = 'CANCELLED')",
                (round_id, session.value),
            )
        ).fetchone()
        if already is not None:
            # Two presses of one picker reach the queue; the first held it.
            return StepResult(result={"saved": False})
        await db.execute(
            "INSERT OR REPLACE INTO resubmission_drafts "
            "(round_id, session_type, status, rows_json, config_name, submitted_by) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                round_id, session.value, str(payload["status"]),
                json.dumps({"rows": payload["rows"], "fl_override": payload["fl_override"]}),
                payload["config_name"], int(payload["submitted_by"]),
            ),
        )
        lines = tuple(
            line for line in (payload.get("accepted_line"), payload.get("choice_line")) if line
        )
        then: list[PlannedStep] = [_say(channel_id, str(payload["saved_text"]))]
        upcoming = await submissions.expected_resubmitted_session_on(db, round_id)
        if upcoming is not None:
            then.append(PlannedStep(
                POST_PROMPT,
                {
                    "channel_id": channel_id, "round_id": round_id,
                    "session_type": upcoming.value, "override_fl": True,
                },
            ))
            return StepResult(result={"saved": True}, lines=lines, then=tuple(then))
        drafted = await submissions.resubmission_drafts_on(db, round_id)
        announcement = await (
            await db.execute(
                "SELECT resubmit_prompt_message_id FROM round_submission_channels "
                "WHERE round_id = ?",
                (round_id,),
            )
        ).fetchone()
        everything_cancelled = all(each.status == "CANCELLED" for each in drafted)
        asked: dict[str, Any] = {
            "round_id": round_id,
            "channel_id": channel_id,
            "division_id": int(payload["division_id"]),
            "season_id": int(payload["season_id"]),
            "round_number": int(payload["round_number"]),
            "division_name": payload["division_name"],
            "cancel_message_id": (
                None if announcement is None else announcement["resubmit_prompt_message_id"]
            ),
            "all_cancelled": everything_cancelled,
        }
        if payload.get("mark") is not None and not everything_cancelled:
            asked["mark"] = payload["mark"]
        return StepResult(
            result={"saved": True, "review_asked": not everything_cancelled}, lines=lines,
            then=tuple(then),
            follow_ons=(FollowOn(
                SWAP_KIND, asked,
                f"replacing round {payload['round_number']}'s results with the resubmission",
            ),),
        )

    async def post_opening_review(ctx: StepContext) -> StepResult:
        """Say in the channel that the review is being opened, with its first job's number and,
        where the league's queue is stopped, the job it is stopped at (results spec :382)."""
        payload = ctx.step_payload
        async with get_connection(ctx.db_path) as db:
            change = await (
                await db.execute(
                    "SELECT id FROM queued_changes WHERE kind = ? "
                    "AND json_extract(payload, '$.round_id') = ? ORDER BY id DESC LIMIT 1",
                    (review_open_change.KIND, int(payload["round_id"])),
                )
            ).fetchone()
            first_row = None if change is None else await (
                await db.execute(
                    "SELECT MIN(id) AS first FROM queued_change_steps WHERE change_id = ?",
                    (change["id"],),
                )
            ).fetchone()
        first = None if first_row is None else first_row["first"]
        if change is None or first is None:
            return StepResult(result={"line": None})
        _, stopped_at = await queue.job_numbers(int(change["id"]))
        line = f"⏳ The penalty review is being opened (job #{first})."
        if stopped_at is not None:
            line += (
                f" The queue is stopped at job #{stopped_at}, so it opens once that job is cleared."
            )
        return StepResult(
            result={"line": line}, then=(_say(int(payload["channel_id"]), line),)
        )

    async def choice_view(ctx: StepContext) -> discord.ui.View:
        payload = ctx.step_payload
        return submissions._ConfigSelectView(
            list(payload["config_names"]), await server_config(),
            session=str(payload["session"]),
            ask=(
                RESUBMISSION_CONFIG_KIND if resubmission else CONFIG_KIND,
                dict(payload["settle"]),
            ),
        )

    async def describe_judge(_ctx: StepContext) -> str:
        return "reading a results paste"

    async def describe_settle(_ctx: StepContext) -> str:
        return "checking that the session can be entered"

    async def describe_save(ctx: StepContext) -> str:
        payload = ctx.step_payload
        return f"saving the {payload['label']} of round {payload['round_number']}"

    async def describe_draft(ctx: StepContext) -> str:
        payload = ctx.step_payload
        return f"holding the {payload['label']} of round {payload['round_number']}'s resubmission"

    async def describe_opening_review(_ctx: StepContext) -> str:
        return "working out the number of the penalty review's first job"

    async def still_marked(ctx: StepContext) -> bool:
        """The last paste's ⏳ stays until the review's prompt appears and clears it."""
        return not any(
            view.name in (SAVE_SESSION, SAVE_DRAFT) and view.result
            and view.result.get("review_asked")
            for view in ctx.steps
        )

    return {
        JUDGE: Step(JUDGE, StepKind.ACT, judge, describe=describe_judge),
        SETTLE: Step(SETTLE, StepKind.ACT, settle, describe=describe_settle),
        SAVE_SESSION: Step(SAVE_SESSION, StepKind.SAVE, save_session, describe=describe_save),
        SAVE_DRAFT: Step(SAVE_DRAFT, StepKind.SAVE, save_draft, describe=describe_draft),
        POST: post_step(describing="posting a line in the submission channel"),
        POST_PROMPT: post_prompt_step(),
        POST_CONFIG_CHOICE: post_choice_step(
            POST_CONFIG_CHOICE, choice_view, describing="posting the points configuration choice"
        ),
        POST_OPENING_REVIEW: Step(
            POST_OPENING_REVIEW, StepKind.ACT, post_opening_review,
            describe=describe_opening_review,
        ),
        typed_turns.CLEAR_MARK: dataclasses.replace(
            typed_turns.clear_mark_step(), still_due=still_marked
        ),
    }


def _checking(
    resubmission: bool, *, turn: bool = False
) -> Callable[[CheckContext], Awaitable[Verdict]]:
    """The check of a turn or its configuration: due while the round's submission stands, and,
    for a resubmission's configuration, while the round is still being resubmitted.

    A resubmission's *turn* is due while the submission stands: its judge finds a round no longer
    resubmitting and clears the paste's mark, which a check that dropped the change would leave
    on the message with no one to answer it.
    """

    async def check(ctx: CheckContext) -> Verdict:
        async with get_connection(ctx.db_path) as db:
            row = await (
                await db.execute(
                    "SELECT resubmitting FROM round_submission_channels WHERE channel_id = ?",
                    (int(ctx.payload["channel_id"]),),
                )
            ).fetchone()
        if row is None:
            return Verdict.not_due("the round's submission is gone")
        if resubmission and not turn and not row["resubmitting"]:
            return Verdict.not_due("the resubmission has ended")
        return Verdict.go()

    return check


def submission_turn_change(
    *, queue: ChangeQueue, server_config: ServerConfig, resubmission: bool = False
) -> ChangeType:
    """The `results.submission.turn` change type, for the builder to register; with
    *resubmission*, `results.resubmission.turn`, a resubmission's paste held as a draft.

    *queue* is the change queue, which tells the review's stopped job; *server_config* reads the
    server configuration the points-configuration picker checks its presser against. The payload
    is ``channel_id``, ``message_id``, ``author_id``, ``content``, ``attachments`` and
    ``typed_at`` of the pasted message.
    """

    def outcome(_ctx: OutcomeContext) -> str:
        return ""

    kind = RESUBMISSION_KIND if resubmission else KIND
    return ChangeType(
        kind=kind,
        opening=(PlannedStep(JUDGE),),
        steps=turn_steps(queue, server_config, resubmission=resubmission),
        check=_checking(resubmission, turn=True),
        key=lambda payload: f"{kind}:{payload['message_id']}",
        doing=lambda _payload: (
            "Taking a resubmitted results paste" if resubmission else "Taking a results paste"
        ),
        outcome=outcome,
        lane=_lane,
        conversation_open=_conversation_open,
        writes=_WRITES,
        turn_channel=_turn_channel,
    )


def submission_config_change(
    *, queue: ChangeQueue, server_config: ServerConfig, resubmission: bool = False
) -> ChangeType:
    """The `results.submission.config` change type, for the builder to register: a session pasted
    and judged, saved with the configuration its press chose; with *resubmission*,
    `results.resubmission.config`, held as a draft.

    The payload is the session's (`judge`'s ``settle``) with ``config_name`` and the press's
    ``choice_line``.
    """

    def plan(payload: dict[str, Any]) -> tuple[PlannedStep, ...]:
        return (PlannedStep(SETTLE, dict(payload)),)

    def outcome(ctx: OutcomeContext) -> str:
        payload = ctx.payload
        if any(
            view.name in (SAVE_SESSION, SAVE_DRAFT) and view.done
            and (view.result or {}).get("saved")
            for view in ctx.steps
        ):
            return (
                f"✅ The {payload['label']} of round {payload['round_number']} is "
                f"{'held' if resubmission else 'saved'}, scored by **{payload['config_name']}**."
            )
        held = next(
            (
                str(view.payload["text"]) for view in ctx.steps
                if view.name == POST and view.payload.get("text")
            ),
            None,
        )
        return held or "ℹ️ Nothing was saved."

    kind = RESUBMISSION_CONFIG_KIND if resubmission else CONFIG_KIND
    return ChangeType(
        kind=kind,
        opening=(),
        plan=plan,
        steps=turn_steps(queue, server_config, resubmission=resubmission),
        check=_checking(resubmission),
        key=lambda payload: f"{kind}:{payload['channel_id']}:{payload['session_type']}",
        doing=lambda payload: (
            f"{'Holding' if resubmission else 'Saving'} the {payload['label']} of round "
            f"{payload['round_number']} with the {payload['config_name']} configuration"
        ),
        outcome=outcome,
        lane=_lane,
        conversation_open=_conversation_open,
        writes=_WRITES,
        turn_channel=_turn_channel,
    )
