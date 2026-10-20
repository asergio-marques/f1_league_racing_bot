"""`results.amend.open`: opening an amendment of a round's results on the change queue (#439, 6b).

`/results rounds amend` asks the queue for this change with its interaction, acknowledged
privately as under way. The change is the league's, whole (a command's jobs go on one lane): it
deletes a finished amendment's channel that survived (`delete_stale`), creates the amendment
channel beside the interaction channel and records its row against the round, the opener
included (`create_channel`), posts the opening with its **Cancel Amendment** button
(`post_opening`) and the first session's prompt, recording when it appeared (`post_prompt`,
`prompt_posted_at`), and arms the five minutes the first paste has from then, in a job of its
own (`arm_lapse`; Q18). The five minutes run from when the bot actually showed the prompt, not
from when the command was typed, so a prompt held behind a stopped queue costs the manager
nothing.

**The command's gates are the change's check**, judged when it is asked and again when it comes
up to run: the module, the live season's stage, the round being FINAL, no job of the division's
on the queue, the sessions chosen being results the round holds, and no other amendment open in
the division (results specification, one amendment is open in a division at a time). A request
refused as it runs tells the manager why in the acknowledgement, and creates nothing.

**A finished amendment's row gives way to the new one.** A row that carries ``closed_at`` has
ended and could not delete its channel; it holds nothing open, but its channel is taken down
first and its row replaced by the new amendment's.
"""
from __future__ import annotations

import dataclasses
import json
import re
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

import aiosqlite
import discord

from leaguebot.core.db.database import get_connection
from leaguebot.core.models.change import PlannedStep, StepKind, StepResult, Verdict
from leaguebot.core.models.round import RoundStatus
from leaguebot.core.services.change_queue import (
    ChangeType,
    CheckContext,
    OutcomeContext,
    Step,
    StepContext,
)
from leaguebot.core.services.season_service import live_season
from leaguebot.core.utils.season_gate import LIVE_STAGES, stage_refusal
from leaguebot.results.models.points_config import SessionType
from leaguebot.results.services.amend_views import AmendCancelView
from leaguebot.results.services.review_changes import division_job_in_hand
from leaguebot.results.services.review_posting import DELETE_CHANNEL, posting_steps
from leaguebot.results.services.result_submission_service import open_amendment_in_division
from leaguebot.results.services.submission_steps import (
    CREATE_CHANNEL,
    POST_PROMPT,
    create_channel_step,
    post_choice_step,
    post_prompt_step,
)

if TYPE_CHECKING:
    from leaguebot.core.services.module_service import ModuleService

KIND = "results.amend.open"
PLAN_OPEN = "plan_open"
DELETE_STALE = "delete_stale"
POST_OPENING = "post_opening"
ARM_LAPSE = "arm_lapse"
#: The one-off timer an amendment's five minutes are armed under, keyed by the amendment's row.
TIMER = "amend_lapse"
#: How long a paste or a choice of configuration may take, from when its prompt appeared.
WAIT = timedelta(minutes=5)

#: The command's name as a refusal quotes it back.
COMMAND = "results rounds amend"

ServerConfig = Callable[[], Awaitable[Any]]

_SESSION_LABEL = {
    SessionType.SPRINT_QUALIFYING: "Sprint Qualifying",
    SessionType.SPRINT_RACE: "Sprint Race",
    SessionType.FEATURE_QUALIFYING: "Feature Qualifying",
    SessionType.FEATURE_RACE: "Feature Race",
}

__all__ = [
    "ARM_LAPSE", "COMMAND", "DELETE_STALE", "KIND", "PLAN_OPEN", "POST_OPENING", "TIMER", "WAIT",
    "amend_open_change", "amendable_sessions", "amendment_open_refusal", "label_of",
]


def label_of(session: SessionType) -> str:
    """The session as an amendment's prompts name it."""
    return _SESSION_LABEL.get(session, session.value)


async def amendable_sessions(db_path: str, round_id: int) -> list[SessionType]:
    """The sessions of *round_id* an amendment may re-enter, in running order: those whose
    results stand. A superseded session is not what the round is any more."""
    async with get_connection(db_path) as db:
        rows = await (
            await db.execute(
                "SELECT session_type FROM session_results WHERE round_id = ? AND status = 'ACTIVE'",
                (round_id,),
            )
        ).fetchall()
    order = list(SessionType)
    return sorted((SessionType(each["session_type"]) for each in rows), key=order.index)


def amendment_open_refusal(division_name: str, open_row: Any) -> str:
    """Why `/results rounds amend` was refused: another amendment is open in the division."""
    channel_id = open_row["channel_id"] if open_row else None
    if channel_id is None:
        return f"❌ {division_name} already has an amendment open. Finish or cancel it first."
    try:
        sessions = ", ".join(
            str(each).replace("_", " ").title() for each in json.loads(open_row["session_types"])
        )
    except (TypeError, ValueError):
        sessions = "its sessions"
    return (
        f"❌ {division_name} already has an amendment open — round "
        f"{open_row['round_number']}, {sessions} — in <#{channel_id}>. Finish or cancel it "
        "there first: one amendment is open in a division at a time."
    )


def amend_open_change(
    *,
    modules: ModuleService,
    server_config: ServerConfig,
    now: Callable[[], datetime],
    arm_once: Callable[[str, int | str, datetime], None],
) -> ChangeType:
    """The `results.amend.open` change type, for the builder to register.

    *modules* tells whether results are on, *server_config* reads the server configuration the
    Cancel button checks its presser against, *now* is the queue's clock (read when the prompt's
    time is recorded) and *arm_once* the scheduler service's, handed here so that no job looks the
    scheduler up on the bot. The payload is ``{"round_id", "division_id", "session_types"}``, the
    sessions as session-type values in running order.
    """

    async def check(ctx: CheckContext) -> Verdict:
        payload = ctx.payload
        round_id = int(payload["round_id"])
        division_id = int(payload["division_id"])
        if not await modules.is_results_enabled():
            return Verdict.refuse("❌ The Results & Standings module is not enabled.")
        season = await live_season(ctx.db_path)
        refusal = stage_refusal(
            season.stage if season is not None else None, COMMAND, stages=LIVE_STAGES
        )
        if refusal is not None or season is None:
            return Verdict.refuse(refusal or "")
        async with get_connection(ctx.db_path) as db:
            row = await (
                await db.execute(
                    "SELECT r.status, r.round_number, d.name AS division_name "
                    "FROM rounds r JOIN divisions d ON d.id = r.division_id "
                    "WHERE r.id = ? AND d.id = ? AND d.season_id = ?",
                    (round_id, division_id, season.id),
                )
            ).fetchone()
            if row is None:
                return Verdict.refuse("❌ Round not found.")
            held = await (
                await db.execute(
                    "SELECT session_type FROM session_results "
                    "WHERE round_id = ? AND status = 'ACTIVE'",
                    (round_id,),
                )
            ).fetchall()
        if row["status"] != RoundStatus.FINAL.value:
            return Verdict.refuse(
                "❌ This round cannot be amended yet. Round results must reach **FINAL** status "
                "(approved through the full penalty review and appeals process) before they can "
                "be amended."
            )
        waiting_on = await division_job_in_hand(ctx.db_path, division_id)
        if waiting_on is not None:
            # 0 is a change with only its close left: it still holds the division, but has no
            # job a manager could Retry or Discard to name.
            naming = f" (job #{waiting_on})" if waiting_on else ""
            return Verdict.refuse(
                f"⏸️ A round of {row['division_name']} has a job on the change queue{naming}, so "
                "it cannot be amended until that is done. Let it finish, or "
                "press **Retry** or **Discard** on its notice if it has stopped, then amend "
                "again."
            )
        if not held:
            return Verdict.refuse("❌ No results found for this round.")
        present = {each["session_type"] for each in held}
        for chosen in payload["session_types"]:
            if chosen not in present:
                return Verdict.refuse(f"❌ No {chosen} session found for this round.")
        other = await open_amendment_in_division(ctx.db_path, division_id)
        if other is not None:
            return Verdict.refuse(amendment_open_refusal(row["division_name"], other))
        return Verdict.go()

    async def plan_open(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        payload = ctx.payload
        round_id = int(payload["round_id"])
        row = await (
            await db.execute(
                "SELECT r.round_number, r.format, d.name AS division_name, s.season_number "
                "FROM rounds r JOIN divisions d ON d.id = r.division_id "
                "JOIN seasons s ON s.id = d.season_id WHERE r.id = ?",
                (round_id,),
            )
        ).fetchone()
        if row is None:
            raise LookupError(f"round {round_id} is not there to amend")
        server = await (
            await db.execute(
                "SELECT interaction_role_id, interaction_channel_id, league_admin_role_id "
                "FROM server_configs WHERE server_id IS NOT NULL"
            )
        ).fetchone()
        stale = await (
            await db.execute(
                "SELECT channel_id FROM round_amend_channels "
                "WHERE round_id = ? AND closed_at IS NOT NULL",
                (round_id,),
            )
        ).fetchone()
        sessions = [SessionType(each) for each in payload["session_types"]]
        slug = re.sub(r"[^a-z0-9-]", "", str(row["division_name"]).lower().replace(" ", "-"))[:20]
        # The league admin role is opened to the amendment channel on the same terms as the
        # interaction role: a league admin holds the league manager tier within their own, and a
        # channel opened to one role and not the other would leave them able to cancel an
        # amendment they cannot see (issue #116).
        roles = [] if server is None else [
            each for each in (server["interaction_role_id"], server["league_admin_role_id"])
            if each
        ]
        planned: list[PlannedStep] = []
        if stale is not None:
            planned.append(PlannedStep(DELETE_STALE, {
                "channel_id": int(stale["channel_id"]), "round_id": round_id,
                "what": "finished amendment channel",
                "reason": "Finished amendment channel left behind",
            }))
        planned.append(PlannedStep(CREATE_CHANNEL, {
            "name": f"amend-S{row['season_number']}-{slug}-R{row['round_number']}",
            "beside_channel_id": None if server is None else server["interaction_channel_id"],
            "role_ids": roles,
            "round_id": round_id,
            "round_number": int(row["round_number"]),
            "division_name": row["division_name"],
            "session_types": [each.value for each in sessions],
            "opener_id": ctx.actor_id,
        }))
        return StepResult(result={"planned": len(planned)}, then=tuple(planned))

    async def record_channel(
        db: aiosqlite.Connection, ctx: StepContext, result: StepResult
    ) -> None:
        """Record the amendment against its round, in the save that marks the channel's creation
        done: the row of a finished amendment that stood in its place gives way."""
        payload = ctx.step_payload
        round_id = int(payload["round_id"])
        await db.execute(
            "DELETE FROM round_amend_channels WHERE round_id = ? AND closed_at IS NOT NULL",
            (round_id,),
        )
        await db.execute(
            "INSERT INTO round_amend_channels "
            "(round_id, channel_id, session_types, created_at, started_by) VALUES (?, ?, ?, ?, ?)",
            (
                round_id, int(result.result["channel_id"]), json.dumps(payload["session_types"]),
                now().isoformat(), ctx.actor_id,
            ),
        )

    shared_create = create_channel_step(record_channel)

    async def create(ctx: StepContext) -> StepResult:
        """Create the channel, and plan its opening, first prompt and timer, which know where
        they go."""
        payload = ctx.step_payload
        made = await shared_create.run(ctx)
        channel_id = int(made.result["channel_id"])
        sessions = [SessionType(each) for each in payload["session_types"]]
        labels = ", ".join(label_of(each) for each in sessions)
        opening = (
            f"📋 **Amend Results — Round {payload['round_number']} ({payload['division_name']})**\n"
            f"Sessions: {labels}.\n"
            "**Stage 1 of 3.** Paste each session's corrected results when asked, in the same "
            "format as a first submission — the two sanction columns amendments used to take "
            "have been withdrawn. The reports and appeals of these sessions are reviewed "
            "together in the two stages that follow.\n"
            "Click **❌ Cancel Amendment** to abort and delete this channel."
        )
        return dataclasses.replace(made, then=(
            PlannedStep(POST_OPENING, {
                "channel_id": channel_id, "text": opening,
                "opener_id": payload["opener_id"], "round_number": payload["round_number"],
                "division_name": payload["division_name"],
                "session_types": payload["session_types"],
            }),
            PlannedStep(POST_PROMPT, {
                "channel_id": channel_id, "round_id": int(payload["round_id"]),
                "session_type": sessions[0].value, "override_fl": True,
            }),
            PlannedStep(ARM_LAPSE, {"channel_id": channel_id}),
        ))

    async def opening_view(ctx: StepContext) -> discord.ui.View:
        payload = ctx.step_payload
        async with get_connection(ctx.db_path) as db:
            row = await (
                await db.execute(
                    "SELECT id FROM round_amend_channels WHERE channel_id = ?",
                    (int(payload["channel_id"]),),
                )
            ).fetchone()
        if row is None:
            raise LookupError(f"channel {payload['channel_id']} holds no amendment")
        return AmendCancelView(
            amend_id=int(row["id"]),
            opener_id=int(payload["opener_id"]),
            bot=ctx.bot,
            server_cfg=await server_config(),
            what=(
                f"`/results rounds amend` of round {payload['round_number']} "
                f"({payload['division_name']})"
            ),
            round_number=int(payload["round_number"]),
            sessions_text=", ".join(payload["session_types"]),
        )

    async def record_prompt(
        db: aiosqlite.Connection, ctx: StepContext, _result: StepResult
    ) -> None:
        """Record when the prompt appeared, in the save that marks it posted: the five minutes
        run from then."""
        await db.execute(
            "UPDATE round_amend_channels SET prompt_posted_at = ? WHERE channel_id = ?",
            (now().isoformat(), int(ctx.step_payload["channel_id"])),
        )

    async def arm_lapse(ctx: StepContext) -> StepResult:
        async with get_connection(ctx.db_path) as db:
            row = await (
                await db.execute(
                    "SELECT id, prompt_posted_at FROM round_amend_channels WHERE channel_id = ?",
                    (int(ctx.step_payload["channel_id"]),),
                )
            ).fetchone()
        if row is None or row["prompt_posted_at"] is None:
            return StepResult(result={"armed": False})
        arm_once(TIMER, int(row["id"]), datetime.fromisoformat(row["prompt_posted_at"]) + WAIT)
        return StepResult(result={"armed": True})

    async def describe_plan(_ctx: StepContext) -> str:
        return "opening the amendment"

    async def describe_create(ctx: StepContext) -> str:
        return f"creating the amendment channel of round {ctx.step_payload['round_number']}"

    async def describe_arm(_ctx: StepContext) -> str:
        return "arming the timer that ends an amendment nobody answers"

    def opened(ctx: OutcomeContext) -> str:
        """The manager's reply: the channel, or the channel that was not created, and a line for
        each job an admin discarded (a discarded job is done with a ``discarded`` result)."""
        made: dict[str, Any] | None = None
        lines: list[str] = []
        for view in ctx.steps:
            result = view.result or {}
            discarded = "discarded" in result
            channel = view.payload.get("channel_id")
            if view.name == CREATE_CHANNEL:
                if discarded:
                    lines.append(
                        "⚠️ The amendment channel was not created, so nothing was opened: "
                        "run `/results rounds amend` again."
                    )
                elif view.done and result:
                    made = result
            elif not discarded:
                continue
            elif view.name == DELETE_STALE:
                lines.append(
                    f"⚠️ The finished amendment's channel <#{channel}> was not deleted, and no "
                    "record of it is kept: delete it by hand."
                )
            elif view.name == POST_OPENING:
                lines.append(f"⚠️ The opening message was not posted in <#{channel}>.")
            elif view.name == POST_PROMPT:
                lines.append(
                    f"⚠️ The first session's prompt was not posted in <#{channel}>, so no time "
                    "limit runs on the amendment: cancel it there and run "
                    "`/results rounds amend` again."
                )
        if made is not None:
            lines.insert(0, f"✅ Amendment channel created: <#{made['channel_id']}>")
        return "\n".join(lines)

    return ChangeType(
        kind=KIND,
        opening=(PlannedStep(PLAN_OPEN),),
        steps={
            PLAN_OPEN: Step(PLAN_OPEN, StepKind.SAVE, plan_open, describe=describe_plan),
            DELETE_STALE: dataclasses.replace(
                posting_steps()[DELETE_CHANNEL], name=DELETE_STALE
            ),
            CREATE_CHANNEL: dataclasses.replace(
                shared_create, run=create, describe=describe_create
            ),
            POST_OPENING: post_choice_step(
                POST_OPENING, opening_view, describing="posting the amendment's opening"
            ),
            POST_PROMPT: dataclasses.replace(post_prompt_step(), record=record_prompt),
            ARM_LAPSE: Step(ARM_LAPSE, StepKind.ACT, arm_lapse, describe=describe_arm),
        },
        check=check,
        key=lambda payload: f"{KIND}:{payload['round_id']}",
        doing=lambda _payload: "Opening an amendment of a round's results",
        outcome=opened,
    )
