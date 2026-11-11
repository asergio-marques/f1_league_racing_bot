"""A weather forecast on the change queue: its draw, its post and the deletion of the phase it
supersedes, three jobs of one change (#439, slice 6).

Weather's timers, its start-up recovery and test mode's advance each ask the queue for a
forecast (`ask_forecast`); the mystery rule is decided here and nowhere else (architecture.md,
"Timed work and restarts"): the scheduler hands each phase's timer straight to this module's
handler without reading the round's format, and a mystery round's phase 1 is its notice (phase 0),
and its phases 2 and 3 nothing. The change's jobs are:

- `draw_forecast` (a save): the phase is drawn and recorded, its flag set and its calculation line
  written to the log channel, all in the one save (weather spec :165). A phase whose earlier
  phase is not done plans the earlier phase first.
- `post_forecast`: the forecast is posted, text composed when it is sent (`forecast_text`), the
  picture on the first try alone. Its record saves the message's id with the job's mark, and it
  plans the deletion of the forecast it supersedes (phase 2 supersedes 1, 3 supersedes 2). A
  forecast Discord refuses, or whose forecast channel is unset, stops the queue (XIV.8).
- `delete_forecast`: the deletion of one recorded forecast, whose record carries the forgetting
  of the row; a refusal stops the queue and keeps the row. It deletes the part the row names, and
  plans a shared `delete_replaced` job for each earlier part of a forecast posted in more than one.
- `plan_forecast_deletes`: finds the forecasts a clean-up (whichever of a round's Phase 1 to 3
  forecasts still stands, a day after it) or a flush (every forecast, when test mode is switched off) deletes, and plans a
  `delete_forecast` for each, so that each deletion is a job of its own.

A forecast whose turn comes after its round's race is dropped, at the draw and again at the post
(its draw is kept). The draws are reached through their modules as they run, so that a test sees
them. A forecast is not repeatable: a phase is done once.
"""

from __future__ import annotations

import dataclasses
import json
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

import aiosqlite
import discord

from leaguebot.core.db.database import get_connection
from leaguebot.core.models.change import (
    FORECAST,
    FORECAST_CLEAN_UP,
    ChangeOrigin,
    GuildUnavailable,
    PlannedStep,
    StepFailedOnDiscord,
    StepKind,
    StepResult,
    Verdict,
    forecasts_named,
)
from leaguebot.core.models.round import Round, RoundStatus, WeatherWindows, read_scheduled_at
from leaguebot.core.services.posting import standing_posts
from leaguebot.core.services.queue.change_queue import (
    ChangeQueue,
)
from leaguebot.core.services.queue.change_types import (
    ChangeType,
    CheckContext,
    OutcomeContext,
    Step,
    StepContext,
    StepView,
)
from leaguebot.core.services.posting.notice_handler import send_notice
from leaguebot.core.services.rounds.round_moments import clean_up_moment, phases_due
from leaguebot.core.services.rounds.round_steps import judged_windows, named_now, round_number_now
from leaguebot.core.services.test_mode.test_mode_service import (
    AdvanceOutcome,
    AdvanceStep,
    AdvanceStepFailed,
    PhaseEntry,
)
from leaguebot.weather.models.forecast import Drawn
from leaguebot.weather.services import (
    forecast_cleanup_service,
    mystery_notice_service,
    phase1_service,
    phase2_service,
    phase3_service,
)
from leaguebot.weather.services.draw_guard import weather_enabled_on
from leaguebot.weather.services.forecast_channel import forecast_channels_on
from leaguebot.weather.services.round_phases import phases_drawn_on
from leaguebot.core.utils.league_server import league_guild
from leaguebot.weather.utils.message_builder import forecast_text, invalidation_message

if TYPE_CHECKING:
    from leaguebot.core.services.modules.module_service import ModuleService
    from leaguebot.core.services.seasons.season_service import SeasonService

log = logging.getLogger(__name__)

DRAW_FORECAST = "draw_forecast"
POST_FORECAST = "post_forecast"
DELETE_FORECAST = forecast_cleanup_service.DELETE_FORECAST
PLAN_FORECAST_DELETES = "plan_forecast_deletes"
#: The jobs an amendment plans through weather, under the names the amendment gives them.
CLEAN_UP_FORECAST = "clean_up_forecast"
NOTIFY_INVALIDATION = "notify_invalidation"
RERUN_PHASE = "rerun_phase"

_DONE_COLUMN = {0: "phase1_done", 1: "phase1_done", 2: "phase2_done", 3: "phase3_done"}


def _value(ctx: StepContext, key: str, default: Any = None) -> Any:
    """The job's own value, else the change's."""
    return ctx.step_payload.get(key, ctx.payload.get(key, default))


def _occasion(phase: int) -> str:
    return "mystery round notice" if phase == 0 else f"Phase {phase} forecast"


async def _round_row(db: aiosqlite.Connection, round_id: int) -> aiosqlite.Row | None:
    cursor = await db.execute(
        "SELECT r.id, r.round_number, r.format, r.scheduled_at, r.phase1_done, r.phase2_done, "
        "       r.phase3_done, r.division_id, r.status AS round_status, r.not_held, "
        "       d.status AS division_status, d.name AS division_name, "
        "       d.forecast_channel_id, d.mention_role_id "
        "FROM rounds r JOIN divisions d ON d.id = r.division_id WHERE r.id = ?",
        (round_id,),
    )
    return await cursor.fetchone()


def _raced(row: aiosqlite.Row, now: datetime) -> bool:
    """Whether the round's race time has passed by *now*."""
    return read_scheduled_at(row["scheduled_at"]) <= now


def _not_due(
    row: aiosqlite.Row | None, phase: int, weather_on: bool, now: datetime, *, posting: bool
) -> str | None:
    """Why *phase* of the round is no longer to be drawn (or, *posting*, posted), else None.

    These are the guards each runner held (`draw_guard`), judged again as the change starts and as
    the post comes up, with the race time added (A25): a forecast for a race already run is
    dropped.
    """
    if row is None:
        return "the round is gone"
    if row["round_status"] == RoundStatus.CANCELLED.value or row["division_status"] == "CANCELLED":
        return "the round or its division is cancelled"
    if not weather_on:
        return "the weather module is off"
    if (phase == 0) != (row["format"] == "MYSTERY"):
        return "the round's format is no longer the one this forecast is for"
    if _raced(row, now):
        return "the round's race time has passed"
    done = bool(row[_DONE_COLUMN[phase]])
    if posting and not done:
        return "the phase is not recorded as drawn"
    if not posting and done:
        return f"the {_occasion(phase)} is already done"
    return None


def _forecast_mentions(phase: int, role_id: int | None) -> discord.AllowedMentions:
    """Who a forecast's post may notify: its division's role alone, whether it is posted as text
    or as a picture and whether it is a first try or a retry, and nobody for the mystery notice
    (phase 0) or a division with no role set. No member and not everyone, ever."""
    if phase == 0 or not role_id:
        return discord.AllowedMentions.none()
    return discord.AllowedMentions(
        everyone=False, users=False, roles=[discord.Object(id=int(role_id))]
    )


def _drawers() -> dict[int, Callable[..., Any]]:
    """Each phase's draw, resolved through its module as it is called."""
    return {
        1: phase1_service.draw_phase1_on,
        2: phase2_service.draw_phase2_on,
        3: phase3_service.draw_phase3_on,
    }


def forecast_steps(*, now: Callable[[], datetime]) -> dict[str, Step]:
    """The three jobs of a forecast, *now* being the queue's clock."""

    async def draw(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        phase = int(_value(ctx, "phase"))
        round_id = int(_value(ctx, "round_id"))
        if phase == 0:
            drawn: Drawn = await mystery_notice_service.record_mystery_notice_on(db, round_id)
        else:
            drawn = await _drawers()[phase](db, round_id, now=now())
        if drawn.blocked:
            return StepResult(result={"blocked": True}, lines=(drawn.line,) if drawn.line else ())
        if drawn.earlier is not None:
            earlier = drawn.earlier
            if any(
                view.name == DRAW_FORECAST and view.done
                and view.payload.get("phase") == earlier
                for view in ctx.steps
            ):
                log.error("Phase %s: no active Phase %s result for round %s", phase, earlier,
                          round_id)
                return StepResult(result={"no_earlier": True})
            base = {**ctx.payload, **ctx.step_payload}
            return StepResult(
                result={"earlier": earlier},
                then=(
                    PlannedStep(DRAW_FORECAST, {**base, "phase": earlier}),
                    PlannedStep(DRAW_FORECAST, {**base, "phase": phase}),
                ),
            )
        if not drawn.written:
            return StepResult(result={"dropped": True})
        return StepResult(
            result={"phase": phase, "drawn": True},
            lines=(drawn.line,) if drawn.line else (),
            then=(PlannedStep(POST_FORECAST, {**ctx.payload, **ctx.step_payload,
                                              "phase": phase}),),
        )

    async def describe_draw(ctx: StepContext) -> str:
        phase = int(_value(ctx, "phase"))
        where = f"round {_value(ctx, 'round_number', '?')} in **{_value(ctx, 'division_name', '?')}**"
        if phase == 0:
            return f"recording the mystery round notice of {where}"
        return f"drawing the Phase {phase} forecast of {where}"

    async def post_still_due(ctx: StepContext) -> bool:
        phase = int(_value(ctx, "phase"))
        async with get_connection(ctx.db_path) as db:
            row = await _round_row(db, int(_value(ctx, "round_id")))
            weather_on = await weather_enabled_on(db)
            drawn = phase == 0 or await _active_payload(db, int(_value(ctx, "round_id")),
                                                        phase) is not None
        reason = _not_due(row, phase, weather_on, now(), posting=True)
        if reason is None and not drawn:
            reason = "the phase's draw no longer stands"
        if reason is not None:
            log.info("The %s of round %s is no longer to be posted: %s.", _occasion(phase),
                     _value(ctx, "round_id"), reason)
        return reason is None

    async def post(ctx: StepContext) -> StepResult:
        await standing_posts.remove_kept(ctx.bot, ctx.kept)
        phase = int(_value(ctx, "phase"))
        round_id = int(_value(ctx, "round_id"))
        async with get_connection(ctx.db_path) as db:
            row = await _round_row(db, round_id)
            if row is None:
                raise LookupError(f"round {round_id} is no longer there")
            payload = None if phase == 0 else await _active_payload(db, round_id, phase)
            earlier = None
            if phase >= 2:
                cursor = await db.execute(
                    "SELECT message_id FROM forecast_messages "
                    "WHERE round_id = ? AND division_id = ? AND phase_number = ?",
                    (round_id, row["division_id"], phase - 1),
                )
                found = await cursor.fetchone()
                earlier = None if found is None else int(found["message_id"])
        if not row["forecast_channel_id"]:
            raise StepFailedOnDiscord(
                f"**{row['division_name']}** has no forecast channel set: set one with "
                f"`/weather channel`, then press Retry."
            )
        mention = row["mention_role_id"]
        text = forecast_text(phase, mention, payload)
        attachment = None
        if ctx.tries == 0:
            from leaguebot.image.services import image_weather_post

            attachment = await image_weather_post.attach_forecast(
                ctx.bot, round_id, 1 if phase == 0 else phase
            )
        parts = await forecast_cleanup_service.post_phase_message(
            ctx.bot,
            division_id=row["division_id"],
            channel_id=int(row["forecast_channel_id"]),
            phase_number=phase,
            text=text,
            attachment=attachment,
            attachment_text="" if phase == 0 else f"<@&{mention}>",
            mentions=_forecast_mentions(phase, mention),
        )
        result = {
            "new": list(parts), "channel_id": int(row["forecast_channel_id"]),
            "phase": phase,
            "old": earlier, "division_id": row["division_id"],
        }
        then: tuple[PlannedStep, ...] = ()
        if earlier is not None:
            then = (PlannedStep(DELETE_FORECAST, {
                "round_id": round_id, "division_id": row["division_id"], "phase": phase - 1,
                "message_id": earlier, "superseded": True,
            }),)
        return StepResult(result=result, then=then)

    async def record_post(db: aiosqlite.Connection, ctx: StepContext, result: StepResult) -> None:
        parts = [int(each) for each in result.result["new"]]
        await forecast_cleanup_service.store_forecast_message_on(
            db, int(_value(ctx, "round_id")), int(result.result["division_id"]),
            int(result.result["phase"]), parts[-1], now=now(),
            channel_id=int(result.result["channel_id"]), message_ids=parts,
        )

    async def describe_post(ctx: StepContext) -> str:
        phase = int(_value(ctx, "phase"))
        async with get_connection(ctx.db_path) as db:
            row = await _round_row(db, int(_value(ctx, "round_id")))
        where = f"round {_value(ctx, 'round_number', '?')} in "
        where += f"<#{row['forecast_channel_id']}>" if row is not None and row[
            "forecast_channel_id"] else f"**{_value(ctx, 'division_name', '?')}**"
        if phase == 0:
            return f"posting the mystery round notice of {where}"
        return f"posting the Phase {phase} forecast of {where}"

    async def delete(ctx: StepContext) -> StepResult:
        phase = int(_value(ctx, "phase"))
        round_id = int(_value(ctx, "round_id"))
        wanted = _value(ctx, "message_id")
        async with get_connection(ctx.db_path) as db:
            division_id = _value(ctx, "division_id")
            if division_id is None:
                row = await _round_row(db, round_id)
                division_id = None if row is None else row["division_id"]
            cursor = await db.execute(
                "SELECT fm.message_id, fm.message_ids, d.name AS division_name, "
                "COALESCE(fm.channel_id, d.forecast_channel_id) "
                "AS forecast_channel_id FROM forecast_messages fm "
                "JOIN divisions d ON d.id = fm.division_id "
                "WHERE fm.round_id = ? AND fm.division_id = ? AND fm.phase_number = ?",
                (round_id, division_id, phase),
            )
            found = await cursor.fetchone()
        if found is None or (wanted is not None and int(found["message_id"]) != int(wanted)):
            return StepResult(result={"none": True})
        channel_id = found["forecast_channel_id"]
        message_id = int(found["message_id"])
        if channel_id is None:
            return StepResult(result={"none": True, "message_id": message_id})
        # A forecast posted in parts comes down whole: this job deletes the part the row names (the
        # last), and plans a `delete_replaced` job for each part before it, so that a refusal of
        # one part stops the queue naming that part and a Discard names it for deletion by hand.
        # A part already gone counts as deleted.
        parts = json.loads(found["message_ids"]) if found["message_ids"] else [message_id]
        try:
            await forecast_cleanup_service._discord_delete(ctx.bot, int(channel_id), message_id)
        except discord.NotFound:
            pass
        except discord.HTTPException as exc:
            from leaguebot.core.utils.league_server import league_guild

            kept: dict[str, Any] = {
                "phase": phase, "channel_id": int(channel_id), "message_id": message_id,
            }
            guild = await league_guild(ctx.bot)
            if guild is not None:
                kept["guild_id"] = guild.id
            raise StepFailedOnDiscord(
                f"the Phase {phase} forecast message {message_id} in channel {channel_id}"
                f" could not be deleted",
                result=kept,
            ) from exc
        then = tuple(
            step
            for part in parts
            if int(part) != message_id
            for step in standing_posts.replaced(
                int(channel_id), int(part), None, what=f"Phase {phase} forecast",
                division_name=found["division_name"],
            )
        )
        return StepResult(
            result={"phase": phase, "message_id": message_id, "channel_id": int(channel_id)},
            then=then,
        )

    async def record_delete(
        db: aiosqlite.Connection, ctx: StepContext, result: StepResult
    ) -> None:
        wanted = _value(ctx, "message_id")
        message_id = wanted if wanted is not None else result.result.get("message_id")
        division_id = _value(ctx, "division_id")
        round_id = int(_value(ctx, "round_id"))
        if division_id is None:
            cursor = await db.execute("SELECT division_id FROM rounds WHERE id = ?", (round_id,))
            found = await cursor.fetchone()
            if found is None:
                return
            division_id = found["division_id"]
        await forecast_cleanup_service.forget_forecast_on(
            db, round_id, int(division_id), int(_value(ctx, "phase")),
            None if message_id is None else int(message_id),
        )

    async def describe_delete(ctx: StepContext) -> str:
        phase = int(_value(ctx, "phase"))
        async with get_connection(ctx.db_path) as db:
            row = await _round_row(db, int(_value(ctx, "round_id")))
            recorded = None
            if _value(ctx, "message_id") is not None:
                cursor = await db.execute(
                    "SELECT channel_id FROM forecast_messages WHERE message_id = ?",
                    (int(_value(ctx, "message_id")),),
                )
                found = await cursor.fetchone()
                recorded = None if found is None else found["channel_id"]
        channel = recorded or (None if row is None else row["forecast_channel_id"])
        where = "the forecast channel" if not channel else f"<#{channel}>"
        number = _value(ctx, "round_number", None) or (None if row is None
                                                       else row["round_number"])
        later = " a day after it" if _value(ctx, "clean_up", False) else ""
        return f"deleting the Phase {phase} forecast of round {number} in {where}{later}"

    async def plan_deletes(ctx: StepContext) -> StepResult:
        planned = await forecast_cleanup_service.forecast_deletes(
            ctx.db_path, round_id=_value(ctx, "round_id"), phases=_value(ctx, "phases"),
            clean_up=bool(_value(ctx, "clean_up", False)),
        )
        return StepResult(result={"planned": len(planned)}, then=planned)

    async def describe_plan(_ctx: StepContext) -> str:
        return "finding the forecasts to delete"

    return {
        DRAW_FORECAST: Step(DRAW_FORECAST, StepKind.SAVE, draw, describe=describe_draw),
        POST_FORECAST: Step(
            POST_FORECAST, StepKind.ACT, post, still_due=post_still_due, describe=describe_post,
            record=record_post,
        ),
        DELETE_FORECAST: Step(
            DELETE_FORECAST, StepKind.DELETE, delete, describe=describe_delete,
            record=record_delete,
        ),
        standing_posts.DELETE_REPLACED: standing_posts.delete_replaced_step(),
        PLAN_FORECAST_DELETES: Step(
            PLAN_FORECAST_DELETES, StepKind.ACT, plan_deletes, describe=describe_plan,
        ),
    }


async def _active_payload(
    db: aiosqlite.Connection, round_id: int, phase: int
) -> dict[str, Any] | None:
    """The payload of the phase's active draw, or None where there is none."""
    cursor = await db.execute(
        "SELECT payload FROM phase_results "
        "WHERE round_id = ? AND phase_number = ? AND status = 'ACTIVE' ORDER BY id DESC LIMIT 1",
        (round_id, phase),
    )
    found = await cursor.fetchone()
    return None if found is None else json.loads(found["payload"])


def _doing(payload: dict[str, Any]) -> str:
    phase = int(payload["phase"])
    where = f"round {payload.get('round_number', '?')} in **{payload.get('division_name', '?')}**"
    if phase == 0:
        return f"posting the mystery round notice of {where}"
    return f"drawing the Phase {phase} forecast of {where}"


def forecast_change(*, now: Callable[[], datetime]) -> ChangeType:
    """`weather.forecast`: the bot's change that draws, posts and supersedes one phase's forecast.

    Not repeatable: a phase is done once, and its check drops a second request once it is.
    """

    async def check(ctx: CheckContext) -> Verdict:
        phase = int(ctx.payload["phase"])
        async with get_connection(ctx.db_path) as db:
            row = await _round_row(db, int(ctx.payload["round_id"]))
            weather_on = await weather_enabled_on(db)
        reason = _not_due(row, phase, weather_on, now(), posting=False)
        return Verdict.go() if reason is None else Verdict.not_due(reason)

    def outcome(_ctx: OutcomeContext) -> str:
        return ""

    return ChangeType(
        kind=FORECAST,
        opening=(PlannedStep(DRAW_FORECAST),),
        steps=forecast_steps(now=now),
        check=check,
        key=lambda payload: f"{FORECAST}:{payload['round_id']}:{payload['phase']}",
        doing=_doing,
        outcome=outcome,
    )


@dataclass(frozen=True)
class ForecastAsked:
    """What `ask_forecast` asked: the *phase* asked (0 where a mystery round's phase 1 became its
    notice) and the queue's *change* id, None where it dropped the ask as not due."""

    phase: int
    change: int | None


async def ask_forecast(
    queue: ChangeQueue, db_path: str, round_id: int, phase: int
) -> ForecastAsked | None:
    """Ask the queue to draw, post and supersede *phase* of *round_id*, as its timer does, and
    return what was asked; None where nothing was.

    The mystery rule is weather's: a mystery round's phase 1 is asked as its notice (phase 0),
    and its phases 2 and 3 asked of nothing. A round that is not there asks nothing either. The
    change's check drops a request that is no longer due (a format amended meanwhile, a phase
    done, a race run).
    """
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT r.round_number, r.format, d.name FROM rounds r "
            "JOIN divisions d ON d.id = r.division_id WHERE r.id = ?",
            (round_id,),
        )
        row = await cursor.fetchone()
    if row is None:
        log.error("Forecast: round_id=%s not found — nothing asked of the queue.", round_id)
        return None
    if row["format"] == "MYSTERY":
        if phase not in (0, 1):
            log.info(
                "Phase %s of mystery round %s has no forecast — nothing asked of the queue.",
                phase, round_id,
            )
            return None
        phase = 0
    payload = {
        "round_id": round_id, "phase": phase, "division_name": row["name"],
        "round_number": row["round_number"],
    }
    change = await queue.ask(FORECAST, payload, what=_doing(payload), origin=ChangeOrigin.BOT)
    return ForecastAsked(phase, change)


#: The phases whose forecasts a clean-up deletes: whichever of them still stands a day after the
#: race. A mystery round's notice (phase 0) is no forecast, and stands.
CLEAN_UP_PHASES = [1, 2, 3]


def _clean_up_doing(payload: dict[str, Any]) -> str:
    """How the clean-up is named: by the forecasts standing when it was asked (`standing`)."""
    return (
        f"deleting {forecasts_named(payload.get('standing', ()))} of round "
        f"{payload.get('round_number', '?')} in **{payload.get('division_name', '?')}** a day "
        f"after it"
    )


def forecast_clean_up_change(*, now: Callable[[], datetime]) -> ChangeType:
    """`weather.forecast.clean_up`: the bot's change that deletes a round's forecast a day after its
    race, whichever of Phases 1 to 3 still stands (a Phase 3 never posted leaves Phase 2 standing),
    one `delete_forecast` job for each, planned by `plan_forecast_deletes`. A round called off, or
    of a division called off, is owed none; a round that ended cancelled because every session was
    entered as not held (`rounds.not_held`) is owed it as any round is, its forecasts having
    stood until then.

    Not repeatable: a second request for a round still waiting is dropped by the queue's own
    rule, and one after the forecast is deleted finds nothing recorded and is not due.
    """

    async def check(ctx: CheckContext) -> Verdict:
        async with get_connection(ctx.db_path) as db:
            row = await _round_row(db, int(ctx.payload["round_id"]))
            weather_on = await weather_enabled_on(db)
            recorded = row is not None and any([
                await _recorded(db, int(ctx.payload["round_id"]), phase)
                for phase in CLEAN_UP_PHASES
            ])
        if row is None:
            return Verdict.not_due("the round is gone")
        if (
            row["round_status"] == RoundStatus.CANCELLED.value and not row["not_held"]
        ) or row["division_status"] == "CANCELLED":
            return Verdict.not_due("the round or its division is cancelled")
        if not weather_on:
            return Verdict.not_due("the weather module is off")
        if not recorded:
            return Verdict.not_due("no Phase 1 to 3 forecast is recorded")
        return Verdict.go()

    def outcome(_ctx: OutcomeContext) -> str:
        return ""

    return ChangeType(
        kind=FORECAST_CLEAN_UP,
        opening=(PlannedStep(PLAN_FORECAST_DELETES),),
        steps=forecast_steps(now=now),
        check=check,
        key=lambda payload: f"{FORECAST_CLEAN_UP}:{payload['round_id']}",
        doing=_clean_up_doing,
        outcome=outcome,
    )


async def _recorded(db: aiosqlite.Connection, round_id: int, phase: int) -> bool:
    """Whether a forecast of *phase* is recorded for *round_id*."""
    cursor = await db.execute(
        "SELECT 1 FROM forecast_messages WHERE round_id = ? AND phase_number = ? LIMIT 1",
        (round_id, phase),
    )
    return await cursor.fetchone() is not None


@dataclass(frozen=True)
class CleanUpAsked:
    """What `ask_forecast_clean_up` asked: the phases *standing* that it names, and the queue's
    *change* id, None where it dropped the ask as not due."""

    standing: list[int]
    change: int | None


async def ask_forecast_clean_up(
    queue: ChangeQueue, db_path: str, round_id: int
) -> CleanUpAsked | None:
    """Ask the queue to delete the forecast of *round_id* that still stands (Phase 1 to 3), as its
    timer does a day after the race, and return what was asked; None where nothing was. A round
    that is not there asks nothing; the change's check drops a request that is no longer due. The
    phases standing as it is asked (`standing`) name the change; the jobs delete what stands when
    they run."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT r.round_number, d.name FROM rounds r "
            "JOIN divisions d ON d.id = r.division_id WHERE r.id = ?",
            (round_id,),
        )
        row = await cursor.fetchone()
        standing = [
            phase for phase in CLEAN_UP_PHASES if await _recorded(db, round_id, phase)
        ]
    if row is None:
        log.error("Forecast clean-up: round_id=%s not found — nothing asked of the queue.",
                  round_id)
        return None
    payload = {
        "round_id": round_id, "phases": list(CLEAN_UP_PHASES), "clean_up": True,
        "standing": standing,
        "division_name": row["name"],
        "round_number": row["round_number"],
    }
    change = await queue.ask(FORECAST_CLEAN_UP, payload, what=_clean_up_doing(payload),
                             origin=ChangeOrigin.BOT)
    return CleanUpAsked(standing, change)


@dataclass(frozen=True)
class TimerCallbacks:
    """The handlers the scheduler fires for weather: each phase's and the clean-up's. Each takes a
    round id and asks the change queue."""

    phases: Mapping[int, Callable[[int], Awaitable[None]]]
    clean_up: Callable[[int], Awaitable[None]]


def timer_callbacks(queue: ChangeQueue, db_path: str) -> TimerCallbacks:
    """The callbacks the scheduler fires for weather, each asking *queue* as its timer does.

    Handed the queue rather than reading it off the bot (architecture.md, "How modules and core
    fit together"). A mystery round's phase 1 timer asks for its notice, by the mystery rule in
    `ask_forecast`; its phases 2 and 3 ask for nothing.
    """

    def _phase(phase: int) -> Callable[[int], Awaitable[None]]:
        async def fire(round_id: int) -> None:
            await ask_forecast(queue, db_path, round_id, phase)

        return fire

    async def clean_up(round_id: int) -> None:
        await ask_forecast_clean_up(queue, db_path, round_id)

    return TimerCallbacks(
        phases={1: _phase(1), 2: _phase(2), 3: _phase(3)},
        clean_up=clean_up,
    )


def advance_steps(queue: ChangeQueue, db_path: str) -> dict[str, AdvanceStep]:
    """The step `/test-mode advance` runs for each of weather's kinds of round job: the three
    phases and the clean-up, each asking *queue* as its timer does (`ask_forecast`,
    `ask_forecast_clean_up`), early.

    The mystery rule is `ask_forecast`'s: a round's `weather_p1` step is its notice where its
    format is mystery and Phase 1 otherwise, so that a notice found from the job store and one
    found from database state reach the same step. A fault is named as the entry names its step:
    the notice (phase number 0), or the phase and its track. Each step's words, and the change it
    asks, are what advance has always said and asked.
    """

    def phase_step(phase: int) -> AdvanceStep:
        async def step(entry: PhaseEntry) -> AdvanceOutcome:
            division, number = entry["division_name"], entry["round_number"]
            notice = entry["phase_number"] == 0
            try:
                asked = await ask_forecast(queue, db_path, entry["round_id"], phase)
            except Exception as exc:  # noqa: BLE001 — named by the command, which reports it
                if notice:
                    raise AdvanceStepFailed("the Mystery Round notice") from exc
                raise AdvanceStepFailed(f"Phase {phase}", track=entry["track_name"]) from exc
            if asked is not None and asked.phase == 0:
                if asked.change is None:
                    return AdvanceOutcome.not_due(
                        f"🔮 The **Mystery Round notice** for **{division}** — "
                        f"**Round {number}** was not due: its race has been run."
                    )
                return AdvanceOutcome.asked(
                    f"🔮 The **Mystery Round notice** for **{division}** — **Round {number}** "
                    f"is on the change queue: it goes to the division forecast channel as soon "
                    f"as the queue reaches it.",
                    f"  phase: Mystery Round notice\n  division: {division}\n"
                    f"  round: Round {number}",
                )
            if asked is None or asked.change is None:
                # The forecast's check dropped it (A25): its race has been run. Nothing is on the
                # queue.
                return AdvanceOutcome.not_due(
                    f"⏩ **Phase {phase}** for **{division}** — **Round {number}** was not due: "
                    f"its race has been run."
                )
            return AdvanceOutcome.asked(
                f"⏩ **Phase {phase}** for **{division}** — **{entry['track_name']}** is on the "
                f"change queue: its forecast goes to the forecast channel and its calculation "
                f"to the log channel as soon as the queue reaches it.",
                f"  phase: {phase}\n  division: {division}\n  track: {entry['track_name']}\n"
                f"  round: {number}",
            )

        return step

    async def clean_up(entry: PhaseEntry) -> AdvanceOutcome:
        division, number = entry["division_name"], entry["round_number"]
        try:
            asked = await ask_forecast_clean_up(queue, db_path, entry["round_id"])
        except Exception as exc:  # noqa: BLE001 — named by the command, which reports it
            raise AdvanceStepFailed("the forecast cleanup") from exc
        if asked is None or asked.change is None:
            return AdvanceOutcome.not_due(
                f"🧹 The **forecast cleanup** for **{division}** — **Round {number}** was not "
                f"due: no forecast stands."
            )
        return AdvanceOutcome.asked(
            f"🧹 The **forecast cleanup** for **{division}** — **Round {number}** is on the "
            f"change queue: it deletes {forecasts_named(asked.standing)} as soon as the queue "
            f"reaches it.",
            f"  phase: cleanup\n  division: {division}\n  round: {number}",
        )

    return {
        "weather_p1": phase_step(1), "weather_p2": phase_step(2), "weather_p3": phase_step(3),
        "cleanup": clean_up,
    }


def forecast_failure(view: StepView) -> tuple[str, str] | None:
    """What a league admin's discard of a forecast job leaves, as a core outcome names it: the
    thing and what to do, or None for a job that is not a forecast's.

    Handed to core's amendment through its hooks, so that it names a discarded weather job without
    importing weather. A discarded draw is made when the bot next starts, while its round is still
    to be run; a discarded post leaves the draw standing in the log channel and nothing to post it
    again; a discarded deletion of a part of any forecast posted in parts, which a `DELETE_FORECAST`
    plans for each part before the last, is left to delete by hand, by its link. A discarded `DELETE_FORECAST` is not named here: the amendment names it itself, as a
    forecast to delete by hand, before it asks this hook.
    """
    result = view.result or {}
    phase = view.payload.get("phase", result.get("phase"))
    occasion = f"Phase {phase} forecast" if phase else "mystery round notice"
    if view.name == DRAW_FORECAST:
        return (occasion, "not drawn, and a league admin discarded it; it is drawn when the bot "
                "next starts, while the round is still to be run")
    if view.name == POST_FORECAST:
        return (occasion, "drawn but not posted, and a league admin discarded it; the draw "
                "stands in the log channel, and nothing posts it again")
    if view.name == standing_posts.DELETE_REPLACED and "forecast" in str(
        view.payload.get("what", "")
    ):
        link = (view.result or {}).get("link") or f"in <#{view.payload['channel_id']}>"
        return ("forecast channel", f"a part of the {view.payload['what']} could not be "
                f"deleted, and a league admin discarded it; delete it by hand ({link})")
    return None


async def amendment_posts(
    _db: aiosqlite.Connection, _round_id: int, *, kept: Mapping[str, Any], draw: Sequence[int],
    **_context: Any,
) -> tuple[PlannedStep, ...]:
    """The posts an amendment owes of weather's, in order, planned as the round is armed again:
    the deletion of each withdrawn forecast that was posted, the notice that the forecasts no
    longer stand, and the draw of each phase in *draw*. *kept* is what weather's part of the
    save kept (`phase_withdrawal.round_amended_on`) with the track, and is empty where the save
    was not made. Subscribed to core's `round_armed`."""
    posted = kept.get("posted", [])
    planned = [PlannedStep(DELETE_FORECAST, {"phase": n}) for n in posted]
    if posted:
        planned.append(PlannedStep(NOTIFY_INVALIDATION, {"track": kept["track"]}))
    planned += [PlannedStep(RERUN_PHASE, {"phase": n}) for n in draw]
    return tuple(planned)


async def amendment_clean_ups(
    db: aiosqlite.Connection, round_id: int, *, rnd: Round, now: Callable[[], datetime],
    **_context: Any,
) -> tuple[PlannedStep, ...]:
    """The clean-up of the round's forecast a day after the round, planned where that day has
    come and a forecast stands (whichever of Phases 1 to 3), named by the phases standing. Read on
    *db*. Subscribed to core's `round_clean_ups_due`."""
    if clean_up_moment(rnd.scheduled_at) > now():
        return ()
    cursor = await db.execute(
        "SELECT DISTINCT phase_number FROM forecast_messages "
        "WHERE round_id = ? AND phase_number IN (1, 2, 3) ORDER BY phase_number",
        (round_id,),
    )
    standing = [int(row["phase_number"]) for row in await cursor.fetchall()]
    if not standing:
        return ()
    return (PlannedStep(CLEAN_UP_FORECAST, {"phases": [1, 2, 3], "clean_up": True,
                                            "standing": standing}),)


def season_end_steps(
    *, now: Callable[[], datetime], line_prefix: str | None = None
) -> dict[str, Step]:
    """The job weather defines for a season's end, by name: the deletion of a forecast
    (`DELETE_FORECAST`), which the season end's `flush_forecasts` plans for each forecast posted
    under test mode. *now* is the queue's clock; *line_prefix* is the closing line some changes
    write, which a forecast's deletion has no use for. Subscribed to core's `season_end_steps`."""
    return {DELETE_FORECAST: forecast_steps(now=now)[DELETE_FORECAST]}


def amendment_steps(
    *, now: Callable[[], datetime], seasons: SeasonService, modules: ModuleService
) -> dict[str, Step]:
    """The jobs weather defines for an amendment, by name: a forecast's own jobs, the draw and the
    deletion named for what the amendment does with them, and the three the amendment plans
    through weather: the notice that the forecasts no longer stand (`NOTIFY_INVALIDATION`), the
    draw of a phase now due (`RERUN_PHASE`) and the deletion of the forecast a day after the round
    (`CLEAN_UP_FORECAST`, a forecast's planning of its deletions under the amendment's name for
    it). *now* is the queue's clock, *seasons* names the round by the number it bears when a job
    is described, and *modules* says whether weather is on. Subscribed to core's
    `amendment_steps`."""
    defined = forecast_steps(now=now)

    def named(text: str) -> Callable[[StepContext], Awaitable[str]]:
        return named_now(seasons, text)

    async def weather_on(_ctx: StepContext) -> bool:
        return await modules.is_weather_enabled()

    async def describe_forecast_delete(ctx: StepContext) -> str:
        if ctx.step_payload.get("clean_up"):
            return (
                f"deleting the Phase {ctx.step_payload['phase']} forecast of round "
                f"{await round_number_now(seasons, ctx)} in **{ctx.payload['division_name']}** a "
                f"day after it"
            )
        if ctx.step_payload.get("superseded"):
            return (
                f"deleting the superseded Phase {ctx.step_payload['phase']} forecast of round "
                f"{await round_number_now(seasons, ctx)} in **{ctx.payload['division_name']}**"
            )
        return await named("deleting a withdrawn forecast of round {number} in **{division}**")(ctx)

    async def describe_clean_up(ctx: StepContext) -> str:
        """The clean-up a day after the round, named by the forecasts standing when it was planned
        (`standing`), never by a phase that is not."""
        return (
            f"deleting {forecasts_named(ctx.step_payload.get('standing', ()))} of round "
            f"{await round_number_now(seasons, ctx)} in **{ctx.payload['division_name']}** a day "
            f"after it"
        )

    async def describe_draw(ctx: StepContext) -> str:
        """The draw of a phase the amendment makes at once, and the job that plans it, each
        naming its phase."""
        phase = ctx.step_payload.get("phase", ctx.payload.get("phase"))
        return (
            f"drawing the Phase {phase} forecast of round {await round_number_now(seasons, ctx)} "
            f"in **{ctx.payload['division_name']}** now"
        )

    async def notify_invalidation(ctx: StepContext) -> StepResult:
        """Tell the division's forecast channel that its forecasts no longer stand. A channel never
        set is done and unnamed; one set and gone, or a send Discord refuses, stops the queue."""
        division_id = int(ctx.payload["division_id"])
        async with get_connection(ctx.db_path) as db:
            channels = await forecast_channels_on(db, [division_id])
        if division_id not in channels:
            raise LookupError(f"division {ctx.payload['division_id']} is no longer there")
        forecast_channel_id = channels[division_id]
        if not forecast_channel_id:
            return StepResult(result={"unset": True})
        guild = await league_guild(ctx.bot)
        if guild is None:
            raise GuildUnavailable("the league's server is not in the cache")
        channel = guild.get_channel(int(forecast_channel_id))
        if channel is None:
            raise StepFailedOnDiscord(
                f"the channel <#{forecast_channel_id}> is no longer on the server"
            )
        try:
            await send_notice(
                channel,
                invalidation_message(str(ctx.step_payload["track"])),
                mentions=discord.AllowedMentions.none(),
            )
        except discord.HTTPException as exc:
            raise StepFailedOnDiscord(
                f"the message could not be posted to <#{forecast_channel_id}>: {exc}"
            ) from exc
        return StepResult(result={"sent": True})

    async def phase_still_due(ctx: StepContext) -> bool:
        """A phase is drawn at once only while weather is on and the phase is still due as the
        round stands when this job runs (`phases_due`): not drawn yet, its horizon passed, the
        race still ahead and the round no mystery round. A job before it can stop and be retried
        after the race, and a forecast for a round already raced is no forecast (owner,
        2026-10-09)."""
        if not await modules.is_weather_enabled():
            return False
        rnd = await seasons.get_round(int(ctx.payload["round_id"]))
        _, weather = judged_windows(ctx)
        if rnd is None or weather is None:
            return False
        async with get_connection(ctx.db_path) as db:
            drawn = (await phases_drawn_on(db, [rnd.id])).get(rnd.id, {})
        return int(ctx.step_payload["phase"]) in phases_due(rnd, weather, now(), drawn=drawn)

    async def rerun_phase(ctx: StepContext) -> StepResult:
        """Plan the draw of a phase whose horizon has already passed, by the amended moment or while
        the amendment stood stopped, where it is still due when the job runs (`phase_still_due`).
        The draw, its post and the deletion of the forecast it supersedes are jobs of the
        amendment's own, in that order, before the next phase's: Discord refusing the post or the
        deletion stops the queue (owner, 2026-10-10)."""
        phase = int(ctx.step_payload["phase"])
        return StepResult(
            result={"phase": phase},
            then=(PlannedStep(DRAW_FORECAST, {"round_id": int(ctx.payload["round_id"]),
                                              "phase": phase}),),
        )

    return {
        **defined,
        DRAW_FORECAST: dataclasses.replace(defined[DRAW_FORECAST], describe=describe_draw),
        DELETE_FORECAST: dataclasses.replace(
            defined[DELETE_FORECAST], describe=describe_forecast_delete
        ),
        NOTIFY_INVALIDATION: Step(
            NOTIFY_INVALIDATION, StepKind.ACT, notify_invalidation, still_due=weather_on,
            describe=named("telling **{division}** its forecasts no longer stand"),
        ),
        RERUN_PHASE: Step(
            RERUN_PHASE, StepKind.ACT, rerun_phase, still_due=phase_still_due,
            describe=describe_draw,
        ),
        CLEAN_UP_FORECAST: dataclasses.replace(
            defined[PLAN_FORECAST_DELETES], name=CLEAN_UP_FORECAST, still_due=weather_on,
            describe=describe_clean_up,
        ),
    }
