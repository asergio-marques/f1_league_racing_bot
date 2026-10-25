"""`results.amend.turn`: each results paste typed into an amendment's channel, a turn on the
amendment's lane (#439, 6b, C59).

The opener's message in the channel of an amendment still open is marked ⏳ at once (`take_amended_paste`) and
`results.amend.turn` is asked for it, as the member's, on the amendment's own lane
(``channel:<amendment channel>``). Nothing waits in memory for the next paste. When its turn comes
the change judges the paste against the session the amendment then expects (`judge`), deletes the
paste (`delete_message`), and either
holds it as a draft of the amendment (`save_draft`: the round's own results stand, nothing is
written yet), the next session's prompt following with the five minutes armed again from when
it appears, or the points configuration offered where the season has several to choose from
(`post_config_choice`).

**A refused paste ends the whole amendment** (results specification :459), the pastes already
accepted included, and nothing is written: the refusal is said in the channel, logged, and
`end_amendment` drops the drafts and closes the amendment's row, which is what ends the
conversation. It asks, as a follow-on of its own save, `results.amend.close`, which deletes the
channel on the amendment's lane: the amendment ending itself, a deletion Discord refuses stops
its own lane and not the league's. The row stays, closed, as it does where a channel could not be
deleted: restart recovery and the next amendment of the round find it so.

A message typed while a turn waits in the channel is turned away at once, with a note and a line
in the log (results spec :257). Only the member who opened the amendment is heard.

**A conversation that has ended drops its turns.** The conversation is the amendment's row while
it carries no ``closed_at``; the league's cancelling or lapsing it, or a refusal, closes it.
"""
from __future__ import annotations

import dataclasses
import json
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

import aiosqlite
import discord

from leaguebot.core.db.database import get_connection
from leaguebot.core.models.change import FollowOn, PlannedStep, StepKind, StepResult, Verdict
from leaguebot.core.services import typed_turns
from leaguebot.core.services.change_queue import (
    ChangeQueue,
    ChangeType,
    CheckContext,
    OutcomeContext,
    Step,
    StepContext,
)
from leaguebot.core.utils.league_server import league_guild
from leaguebot.core.utils.log_lines import refusal_line
from leaguebot.core.utils.member_names import member_named
from leaguebot.results.models.points_config import SessionType
from leaguebot.results.services import result_submission_service as submissions
from leaguebot.results.services.amend_changes import (
    ARM_LAPSE,
    COMMAND,
    WAIT,
    label_of,
    prompt_steps,
    record_prompt_posted,
)
from leaguebot.results.services.amendment_stage_changes import PROMPT_KIND
from leaguebot.results.services.review_posting import (
    DELETE_CHANNEL,
    DELETE_MESSAGE,
    NAMES,
    delete_message_step,
    display_names,
    posting_steps,
)
from leaguebot.results.services.submission_steps import (
    POST,
    POST_PROMPT,
    post_choice_step,
    post_step,
)

KIND = "results.amend.turn"
CONFIG_KIND = "results.amend.config"
STAGE_ONE_KIND = "results.amend.stage_one"
CLOSE_KIND = "results.amend.close"
JUDGE = "judge"
SAVE_DRAFT = "save_draft"
END_AMENDMENT = "end_amendment"
POST_CONFIG_CHOICE = "post_config_choice"
RECORD_STAGE_ONE = "record_stage_one"

#: The configuration name a draft keeps for a season with none attached. A draft whose
#: configuration is unset is still awaiting its choice (`expected_amended_session_on`), so a
#: season with nothing to choose stores this.
NO_CONFIG = ""

__all__ = [
    "CLOSE_KIND", "CONFIG_KIND", "KIND", "NO_CONFIG", "amend_close_change",
    "STAGE_ONE_KIND", "amend_config_change", "amend_stage_one_change", "amend_turn_change",
    "take_amended_paste", "turn_steps",
]

#: The amendment held in a channel, with what its turns need to know of it.
_AMENDMENT = (
    "SELECT rac.id AS amend_id, rac.round_id, rac.channel_id, rac.session_types, rac.started_by, "
    "r.division_id, r.round_number, r.format, d.name AS division_name, s.id AS season_id, "
    "s.season_number "
    "FROM round_amend_channels rac JOIN rounds r ON r.id = rac.round_id "
    "JOIN divisions d ON d.id = r.division_id JOIN seasons s ON s.id = d.season_id "
    "WHERE rac.channel_id = ? AND rac.closed_at IS NULL"
)

#: The tables a turn's saves may write.
_WRITES = ("amend_drafts", "round_amend_channels")

ServerConfig = Callable[[], Awaitable[Any]]


def _turn_channel(payload: dict[str, Any]) -> int:
    return int(payload["channel_id"])


def _lane(payload: dict[str, Any]) -> str:
    return f"channel:{int(payload['channel_id'])}"


async def _conversation_open(db: aiosqlite.Connection, payload: dict[str, Any]) -> bool:
    row = await (
        await db.execute(
            "SELECT 1 FROM round_amend_channels WHERE channel_id = ? AND closed_at IS NULL",
            (int(payload["channel_id"]),),
        )
    ).fetchone()
    return row is not None


def _covered(row: aiosqlite.Row) -> list[SessionType]:
    """The sessions the amendment covers, in the order its record lists them."""
    return [SessionType(each) for each in json.loads(row["session_types"])]


async def take_amended_paste(bot: Any, message: discord.Message, *, queue: ChangeQueue) -> bool:
    """What the results cog does with a message typed in a channel that holds an open amendment:
    nothing unless it is the opener's; then it is turned away where a turn waits in the channel
    (the refusal logged), else marked ⏳ and asked as a turn. Gives whether the channel is an
    amendment's, so that the cog hears it no further."""
    async with get_connection(bot.db_path) as db:
        row = await (await db.execute(_AMENDMENT, (message.channel.id,))).fetchone()
        if row is None:
            return False
        if message.author.id != row["started_by"]:
            return True
        expected = await submissions.expected_amended_session_on(db, int(row["amend_id"]))
    if await typed_turns.turn_waiting(bot.db_path, queue.types, message.channel.id):
        await typed_turns.turn_away(message, typed_turns.TURN_AWAY_NOTE)
        waiting = "its session" if expected is None else label_of(expected)
        await bot.output_router.post_log(
            refusal_line(
                member_named(message.author.display_name, message.author.id),
                "A results paste",
                f"Round {row['round_number']} ({row['division_name']}) is still waiting on an "
                f"earlier paste for {waiting}",
            )
        )
        return True
    await typed_turns.mark(message)
    await queue.ask(
        KIND,
        {
            "channel_id": message.channel.id,
            "message_id": message.id,
            "author_id": message.author.id,
            "content": message.content,
            "attachments": [attachment.url for attachment in message.attachments],
            "typed_at": message.created_at.isoformat(),
        },
        actor=message.author,
        what=f"an amended results paste for round {row['round_number']}",
    )
    return True


def turn_steps(
    server_config: ServerConfig,
    *,
    now: Callable[[], datetime],
    arm_once: Callable[[str, int | str, datetime], None],
) -> dict[str, Step]:
    """The jobs an amendment's turn and its configuration change share, keyed by their names."""

    async def judge(ctx: StepContext) -> StepResult:
        payload = ctx.payload
        channel_id = int(payload["channel_id"])
        mark = {"channel_id": channel_id, "message_id": int(payload["message_id"])}
        clear = (PlannedStep(typed_turns.CLEAR_MARK, mark),)
        delete_paste = PlannedStep(DELETE_MESSAGE, {**mark, "what": "an amended results paste"})
        async with get_connection(ctx.db_path) as db:
            row = await (await db.execute(_AMENDMENT, (channel_id,))).fetchone()
            expected = None if row is None else await submissions.expected_amended_session_on(
                db, int(row["amend_id"])
            )
            drafted = [] if row is None else await submissions.amend_drafts_on(
                db, int(row["amend_id"])
            )
        if row is None or expected is None:
            return StepResult(result={"judged": False}, then=clear)
        covered = _covered(row)
        round_id = int(row["round_id"])
        what = f"`{COMMAND}` of round {row['round_number']} ({row['division_name']})"
        label = label_of(expected)

        lines_raw = [
            each.strip() for each in str(payload["content"]).strip().splitlines() if each.strip()
        ]
        current_of = await submissions.current_accounts(ctx.db_path)
        fl_override, lines_raw = submissions.extract_current_fl_override(
            lines_raw, expected, current_of
        )
        # A driver's team must agree across the round's sessions. The sessions being replaced are
        # read from their new pastes, never their old rows: the correction would otherwise be held
        # against the very classification it corrects.
        others = submissions.collected_team_assignments(drafted, expected)
        others.update(await submissions.other_active_team_assignments(
            ctx.db_path, round_id, expected,
            also_exclude=[each for each in covered if each is not expected],
        ))
        data = await submissions._build_division_validation_data(int(row["division_id"]), ctx.bot)
        errors, parsed = submissions.split_validation(
            submissions.validate_submission_block(
                lines_raw, expected, data.division_driver_ids, data.team_of_role,
                data.reserve_team_role_id, data.driver_team_map, data.reserve_driver_ids,
                other_active_assignments=others, current_of=current_of,
                team_names=data.team_names, team_of_shorthand=data.team_of_shorthand,
            )
        )

        def refused(reply: str, reason: str) -> StepResult:
            """The whole amendment ends, earlier pastes and all (decided 2026-09-21): the paste
            is deleted, the refusal said and logged, and the amendment ended and its channel
            taken down."""
            return StepResult(
                result={"refused": reason},
                lines=(refusal_line(ctx.named, what, reason),),
                then=(
                    delete_paste,
                    PlannedStep(POST, {"channel_id": channel_id, "text": reply}),
                    PlannedStep(END_AMENDMENT, {
                        "channel_id": channel_id, "amend_id": int(row["amend_id"]),
                        "round_id": round_id,
                    }),
                    *clear,
                ),
            )

        if errors:
            return refused(
                "❌ Amendment rejected — validation errors were found. Check the log channel "
                "for details, then re-run `/results rounds amend`.",
                f"session {expected.value} failed validation: {'; '.join(errors[:10])}",
            )
        if fl_override is not None and fl_override not in {r.driver_user_id for r in parsed}:
            guild = await league_guild(ctx.bot)
            member = None if guild is None else guild.get_member(int(fl_override))
            name = member.display_name if member is not None else str(fl_override)
            return refused(
                f"❌ Amendment rejected — FL override **{name}** is not in the submitted "
                "results. Re-run `/results rounds amend` to try again.",
                f"session {expected.value}: FL override {name} not in submitted results",
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
        draft = {
            "channel_id": channel_id, "amend_id": int(row["amend_id"]), "round_id": round_id,
            "division_id": int(row["division_id"]), "session_type": expected.value, "label": label,
            "round_number": int(row["round_number"]),
            "rows": driver_rows, "fl_override": fl_override, "mark": mark,
            "several": len(covered) > 1,
        }
        config_names = await _attached_configs(ctx.db_path, int(row["season_id"]))
        existing = await _existing_config(ctx.db_path, round_id, expected)
        if len(config_names) == 1:
            config_name: str | None = config_names[0]
        elif not config_names:
            config_name = NO_CONFIG
        elif existing is not None and existing in config_names:
            config_name = existing
        else:
            config_name = None
        if config_name is None:
            return StepResult(
                result={"judged": "configuration to choose"},
                then=(
                    delete_paste,
                    PlannedStep(POST_CONFIG_CHOICE, {
                        "channel_id": channel_id,
                        "text": f"Select the points configuration for {label}:",
                        "config_names": config_names,
                        "draft": {**draft, "mark": None},
                        "session": f"the {label} of round {row['round_number']} "
                                   f"({row['division_name']})",
                    }),
                    PlannedStep(ARM_LAPSE, {"channel_id": channel_id}),
                    *clear,
                ),
            )
        return StepResult(
            result={"judged": "accepted"},
            then=(delete_paste, PlannedStep(SAVE_DRAFT, {**draft, "config_name": config_name}),
                  *clear),
        )

    async def save_draft(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        """Hold the session as a draft of the amendment, then ask for the next session: its prompt
        and the five minutes armed again. With every session drafted nothing more is asked here."""
        payload = ctx.step_payload
        amend_id = int(payload["amend_id"])
        session = SessionType(payload["session_type"])
        already = await (
            await db.execute(
                "SELECT 1 FROM amend_drafts WHERE amend_id = ? AND session_type = ? "
                "AND config_name IS NOT NULL",
                (amend_id, session.value),
            )
        ).fetchone()
        if already is not None:
            # A backstop: two pickers for one session reach the queue, and the first held
            # it. A second press of one picker never gets here, the view refusing it.
            return StepResult(result={"saved": False})
        await db.execute(
            "INSERT OR REPLACE INTO amend_drafts (amend_id, session_type, rows_json, config_name) "
            "VALUES (?, ?, ?, ?)",
            (
                amend_id, session.value,
                json.dumps({"rows": payload["rows"], "fl_override": payload["fl_override"]}),
                payload["config_name"],
            ),
        )
        lines = (str(payload["choice_line"]),) if payload.get("choice_line") else ()
        upcoming = await submissions.expected_amended_session_on(db, amend_id)
        if upcoming is None:
            # Every session is in: stage one is the league's, handed over in this save.
            return StepResult(
                result={"saved": True}, lines=lines,
                follow_ons=(FollowOn(
                    STAGE_ONE_KIND,
                    {
                        "amend_id": amend_id, "round_id": int(payload["round_id"]),
                        "division_id": int(payload["division_id"]), "channel_id": int(payload["channel_id"]),
                    },
                    f"recording round {payload['round_number']}'s amended results",
                ),),
            )
        channel_id = int(payload["channel_id"])
        asking: list[PlannedStep] = [PlannedStep(POST_PROMPT, {
            "channel_id": channel_id, "round_id": int(payload["round_id"]),
            "session_type": upcoming.value, "override_fl": True,
        })]
        if payload.get("several"):
            # Which of the amendment's sessions is asked for, as the pastes follow one another.
            asking.append(PlannedStep(POST, {
                "channel_id": channel_id,
                "text": f"**{label_of(upcoming)}** — paste the corrected results for this session.",
            }))
        asking.append(PlannedStep(ARM_LAPSE, {"channel_id": channel_id}))
        return StepResult(result={"saved": True}, lines=lines, then=tuple(asking))

    async def end_amendment(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        """Drop the drafts and close the amendment's row, which ends the conversation, and ask
        the channel's deletion on the amendment's lane. Nothing was written to the round."""
        payload = ctx.step_payload
        amend_id = int(payload["amend_id"])
        channel_id = int(payload["channel_id"])
        closing: dict[str, Any] = {"channel_id": channel_id, "round_id": payload.get("round_id")}
        if payload.get("farewell"):
            closing["farewell"] = payload["farewell"]
        await db.execute("DELETE FROM amend_drafts WHERE amend_id = ?", (amend_id,))
        await db.execute(
            "UPDATE round_amend_channels SET closed_at = ? WHERE id = ? AND closed_at IS NULL",
            (now().isoformat(), amend_id),
        )
        return StepResult(
            result={"ended": True},
            follow_ons=(FollowOn(CLOSE_KIND, closing, "deleting a refused amendment's channel"),),
        )

    async def choice_view(ctx: StepContext) -> discord.ui.View:
        payload = ctx.step_payload
        return submissions._ConfigSelectView(
            list(payload["config_names"]), await server_config(),
            session=str(payload["session"]),
            ask=(CONFIG_KIND, dict(payload["draft"])),
            text=str(payload["text"]),
        )

    async def describe_judge(_ctx: StepContext) -> str:
        return "reading an amended results paste"

    async def describe_save(ctx: StepContext) -> str:
        payload = ctx.step_payload
        return f"holding the {payload['label']} of round {payload['round_number']}'s amendment"

    async def describe_end(_ctx: StepContext) -> str:
        return "ending the amendment"

    return {
        JUDGE: Step(JUDGE, StepKind.ACT, judge, describe=describe_judge),
        DELETE_MESSAGE: delete_message_step(),
        POST: post_step(describing="posting a line in the amendment channel"),
        SAVE_DRAFT: Step(SAVE_DRAFT, StepKind.SAVE, save_draft, describe=describe_save),
        END_AMENDMENT: Step(END_AMENDMENT, StepKind.SAVE, end_amendment, describe=describe_end),
        POST_CONFIG_CHOICE: dataclasses.replace(
            post_choice_step(
                POST_CONFIG_CHOICE, choice_view,
                describing="posting the points configuration choice",
            ),
            record=record_prompt_posted(now),
        ),
        typed_turns.CLEAR_MARK: typed_turns.clear_mark_step(),
        **prompt_steps(now=now, arm_once=arm_once),
    }


async def _attached_configs(db_path: str, season_id: int) -> list[str]:
    from leaguebot.results.services.season_points_service import get_season_config_names

    return await get_season_config_names(db_path, season_id)


async def _existing_config(db_path: str, round_id: int, session: SessionType) -> str | None:
    """The configuration the session being replaced was scored by, kept where still attached."""
    async with get_connection(db_path) as db:
        row = await (
            await db.execute(
                "SELECT config_name FROM session_results "
                "WHERE round_id = ? AND session_type = ? AND status = 'ACTIVE'",
                (round_id, session.value),
            )
        ).fetchone()
    return None if row is None else row["config_name"]


async def _amendment_standing(ctx: CheckContext) -> Verdict:
    async with get_connection(ctx.db_path) as db:
        row = await (
            await db.execute(
                "SELECT closed_at, prompt_posted_at FROM round_amend_channels "
                "WHERE channel_id = ?",
                (int(ctx.payload["channel_id"]),),
            )
        ).fetchone()
    if row is None or row["closed_at"] is not None:
        return Verdict.not_due("the amendment has ended")
    typed, shown = ctx.payload.get("typed_at"), row["prompt_posted_at"]
    if typed is not None and shown is not None and (
        datetime.fromisoformat(typed) > datetime.fromisoformat(shown) + WAIT
    ):
        # Results :460, Q18: an answer counts only if typed within the five minutes of the prompt
        # it answers, however long its turn waited. The lapse has the amendment.
        return Verdict.not_due("it was typed after the prompt's five minutes ran out")
    return Verdict.go()


def amend_turn_change(
    *,
    server_config: ServerConfig,
    now: Callable[[], datetime],
    arm_once: Callable[[str, int | str, datetime], None],
) -> ChangeType:
    """The `results.amend.turn` change type, for the builder to register.

    *server_config* reads the server configuration the points-configuration picker checks its
    presser against, *now* is the queue's clock and *arm_once* the scheduler service's, handed
    here so that no job looks the scheduler up on the bot. The payload is ``channel_id``,
    ``message_id``, ``author_id``, ``content``, ``attachments`` and ``typed_at`` of the pasted
    message, which counts only if typed within the five minutes of the prompt it answers.
    """
    return ChangeType(
        kind=KIND,
        opening=(PlannedStep(JUDGE),),
        steps=turn_steps(server_config, now=now, arm_once=arm_once),
        check=_amendment_standing,
        key=lambda payload: f"{KIND}:{payload['message_id']}",
        doing=lambda _payload: "Taking an amended results paste",
        outcome=lambda _ctx: "",
        lane=_lane,
        conversation_open=_conversation_open,
        writes=_WRITES,
        turn_channel=_turn_channel,
    )


def amend_config_change(
    *,
    server_config: ServerConfig,
    now: Callable[[], datetime],
    arm_once: Callable[[str, int | str, datetime], None],
) -> ChangeType:
    """The `results.amend.config` change type, for the builder to register: a session pasted and
    judged, held as a draft of the amendment with the configuration its press chose, on the
    amendment's lane.

    The payload is the session's draft (what `judge` plans for the configuration picker) with the
    press's ``config_name`` and ``choice_line``, the one line the choice leaves in the log
    channel. The arguments are `amend_turn_change`'s.
    """

    def plan(payload: dict[str, Any]) -> tuple[PlannedStep, ...]:
        return (PlannedStep(SAVE_DRAFT, dict(payload)),)

    def outcome(ctx: OutcomeContext) -> str:
        payload = ctx.payload
        saved = any(
            view.name == SAVE_DRAFT and view.done and (view.result or {}).get("saved")
            for view in ctx.steps
        )
        if saved:
            return (
                f"✅ The {payload['label']} of round {payload['round_number']} is held, "
                f"scored by **{payload['config_name']}**."
            )
        return "ℹ️ Nothing was saved."

    return ChangeType(
        kind=CONFIG_KIND,
        opening=(),
        plan=plan,
        steps=turn_steps(server_config, now=now, arm_once=arm_once),
        check=_amendment_standing,
        key=lambda payload: f"{CONFIG_KIND}:{payload['channel_id']}:{payload['session_type']}",
        doing=lambda payload: (
            f"Holding the {payload['label']} of round {payload['round_number']}'s amendment "
            f"with the {payload['config_name']} configuration"
        ),
        outcome=outcome,
        lane=_lane,
        conversation_open=_conversation_open,
        writes=_WRITES,
        turn_channel=_turn_channel,
    )


def amend_stage_one_change(
    *,
    server_config: ServerConfig,
    now: Callable[[], datetime],
    arm_once: Callable[[str, int | str, datetime], None],
) -> ChangeType:
    """The `results.amend.stage_one` change type, for the builder to register, on the league lane:
    the amendment's pastes, all in, recorded.

    The last paste's turn hands it over in its own save. The payload is ``amend_id``,
    ``round_id``, ``division_id`` and ``channel_id``; the pastes are read from the amendment's
    drafts when it runs. Its jobs are `names` (the standings names, resolved before the save, which
    waits on nothing but its connection) and `record_stage_one`, one save: the round snapshotted,
    each corrected classification written over it, the points applied and the standings recomputed
    (`amend_round_results_on`), the stage's line written, and the amendment's lane asked for the
    stage-two prompt (`results.amend.prompt`). Nothing is published (#345).

    A driver carrying a verdict missing from a corrected classification refuses the amendment
    (`AmendmentWouldOrphanVerdictError`): the save is undone to where it began, the refusal is logged and
    said in the channel (by the amendment's lane, ahead of the channel's deletion), and the whole
    amendment ends as a refused paste ends it. Any other
    fault leaves the save unwritten and stops the league's queue; the drafts stay, so a Retry
    records them. The drafts are kept once recorded: the amendment expects its next session by
    them.
    """
    steps = turn_steps(server_config, now=now, arm_once=arm_once)

    def plan(payload: dict[str, Any]) -> tuple[PlannedStep, ...]:
        return (
            PlannedStep(NAMES, {"division_id": int(payload["division_id"])}),
            PlannedStep(RECORD_STAGE_ONE),
        )

    async def standing(ctx: CheckContext) -> Verdict:
        async with get_connection(ctx.db_path) as db:
            row = await (
                await db.execute(
                    "SELECT closed_at, pre_amendment_state FROM round_amend_channels WHERE id = ?",
                    (int(ctx.payload["amend_id"]),),
                )
            ).fetchone()
        if row is None or row["closed_at"] is not None:
            return Verdict.not_due("the amendment has ended")
        if row["pre_amendment_state"] is not None:
            return Verdict.not_due("the amendment's stage one is already recorded")
        return Verdict.go()

    async def record(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        payload = ctx.payload
        amend_id, round_id = int(payload["amend_id"]), int(payload["round_id"])
        division_id, channel_id = int(payload["division_id"]), int(payload["channel_id"])
        row = await (
            await db.execute(
                "SELECT rac.started_by, rac.session_types, r.round_number, "
                "d.name AS division_name, s.season_number "
                "FROM round_amend_channels rac JOIN rounds r ON r.id = rac.round_id "
                "JOIN divisions d ON d.id = r.division_id JOIN seasons s ON s.id = d.season_id "
                "WHERE rac.id = ?",
                (amend_id,),
            )
        ).fetchone()
        assert row is not None  # the check found the amendment standing
        drafted = await submissions.amend_drafts_on(db, amend_id)
        sessions = [
            submissions.AmendedSession(
                each.session_type, each.driver_rows, each.config_name, each.fl_driver_override
            )
            for each in drafted
        ]
        what = f"`{COMMAND}` of round {row['round_number']} ({row['division_name']})"
        await db.execute("SAVEPOINT stage_one")
        try:
            await submissions.amend_round_results_on(
                db, round_id, division_id, sessions, int(row["started_by"]),
                display_names(ctx, division_id), now=now(),
            )
        except submissions.AmendmentWouldOrphanVerdictError as exc:
            await db.execute("ROLLBACK TO SAVEPOINT stage_one")
            await db.execute("RELEASE SAVEPOINT stage_one")
            return StepResult(
                result={"refused": str(exc)},
                lines=(refusal_line(ctx.named, what, str(exc)),),
                then=(
                    PlannedStep(END_AMENDMENT, {
                        "channel_id": channel_id, "amend_id": amend_id, "round_id": round_id,
                        "farewell": f"❌ {exc}",
                    }),
                ),
            )
        await db.execute("RELEASE SAVEPOINT stage_one")
        covered = _covered(row)
        line = (
            f"{ctx.named} | AMEND_STAGE_1 | Recorded\n"
            f"  season: {row['season_number']}, division: {row['division_name']!r}\n"
            f"  round: {row['round_number']}, sessions: "
            + ", ".join(f"{each.session_type.value}={each.config_name}" for each in sessions)
            + "\n"
            "  Corrected classification recorded. Nothing is published until the report and "
            "appeal stages are approved."
        )
        return StepResult(
            result={"recorded": True},
            lines=(line,),
            follow_ons=(FollowOn(
                PROMPT_KIND,
                {
                    "stage": "reports", "round_id": round_id, "division_id": division_id,
                    "channel_id": channel_id, "session_types": [each.value for each in covered],
                    "round_number": int(row["round_number"]),
                    "division_name": row["division_name"],
                },
                f"the stage-two prompt of round {row['round_number']}'s amendment",
            ),),
        )

    async def describe_record(_ctx: StepContext) -> str:
        return "recording the amended results"

    def outcome(ctx: OutcomeContext) -> str:
        refused = next(
            (str(view.result["refused"]) for view in ctx.steps
             if view.name == RECORD_STAGE_ONE and view.result and view.result.get("refused")),
            None,
        )
        return f"❌ {refused}" if refused else ""

    return ChangeType(
        kind=STAGE_ONE_KIND,
        opening=(),
        plan=plan,
        steps={
            **steps,
            NAMES: posting_steps()[NAMES],
            RECORD_STAGE_ONE: Step(
                RECORD_STAGE_ONE, StepKind.SAVE, record, describe=describe_record
            ),
        },
        check=standing,
        key=lambda payload: f"{STAGE_ONE_KIND}:{payload['amend_id']}",
        doing=lambda payload: f"Recording round {payload['round_id']}'s amended results",
        outcome=outcome,
        turn_channel=_turn_channel,
    )


def amend_close_change() -> ChangeType:
    """The `results.amend.close` change type, for the builder to register, on the amendment's own
    lane (``channel:<amendment channel>``): the deletion of the channel of an amendment that ended
    itself.

    The payload is ``channel_id`` and ``round_id``, and a ``farewell`` where the ending has a
    line to say first (stage one's refusal): posted ahead of the deletion, so a refusal of that post
    stops this lane and not the league's. The amendment's row stays, closed, which is
    what restart recovery and the next amendment of the round look for. The conversation stands
    while the row names the channel, closed or not: the deletion is the ending's own, and needs
    only its channel to stand.
    """

    async def conversation_open(db: aiosqlite.Connection, payload: dict[str, Any]) -> bool:
        row = await (
            await db.execute(
                "SELECT 1 FROM round_amend_channels WHERE channel_id = ?",
                (int(payload["channel_id"]),),
            )
        ).fetchone()
        return row is not None

    def plan(payload: dict[str, Any]) -> tuple[PlannedStep, ...]:
        farewell = (
            (PlannedStep(POST, {
                "channel_id": int(payload["channel_id"]), "text": str(payload["farewell"]),
            }),)
            if payload.get("farewell") else ()
        )
        return (*farewell, PlannedStep(DELETE_CHANNEL, {
            "channel_id": int(payload["channel_id"]), "round_id": payload.get("round_id"),
            "what": "amendment channel", "reason": "Results amend complete",
        }))

    async def standing(ctx: CheckContext) -> Verdict:
        async with get_connection(ctx.db_path) as db:
            row = await (
                await db.execute(
                    "SELECT 1 FROM round_amend_channels WHERE channel_id = ?",
                    (int(ctx.payload["channel_id"]),),
                )
            ).fetchone()
        return Verdict.go() if row is not None else Verdict.not_due("the amendment is gone")

    def outcome(_ctx: OutcomeContext) -> str:
        return ""

    return ChangeType(
        kind=CLOSE_KIND,
        opening=(),
        plan=plan,
        steps={
            POST: post_step(describing="posting a line in the amendment channel"),
            DELETE_CHANNEL: dataclasses.replace(posting_steps()[DELETE_CHANNEL], record=None),
        },
        check=standing,
        key=lambda payload: f"{CLOSE_KIND}:{payload['channel_id']}",
        doing=lambda _payload: "deleting an ended amendment's channel",
        outcome=outcome,
        lane=_lane,
        conversation_open=conversation_open,
        writes=(),
    )
