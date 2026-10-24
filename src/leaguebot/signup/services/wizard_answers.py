"""A driver's answers in their signup channel on the change queue (#439, slice 6b, C81-C83).

A typed answer is a turn on the lane of the driver's signup channel (``channel:<id>``). The signup
cog marks the message ⏳ at once and asks `signup.answer`; a wizard button asks the same change
with the press, acknowledged privately and updated. A message typed while an earlier answer still
waits is turned away (`typed_turns`). Nothing waits in memory for the next answer: the wizard's
row, the 24 hours' timer and the queue's records are the state.

**The judging is pure.** `judge_answer` and `judge_button` read the wizard and what was typed or
pressed and give the answers to keep (`Accepted`) or the refusal and the step its log line names
(`Refused`); they save and post nothing. The jobs do: `judge` reads the wizard as it stands when
the turn comes and plans, `save_answer` keeps the answer and moves the wizard on (a save of the
wizard's row alone, R3), `arm_inactivity` sets the 24 hours again from that moment, `show_next`
posts the next question (taking typing away or giving it back in the same job) and `clear_mark`
takes the ⏳ off. An answer to a question the wizard has moved past is refused when its turn
comes, naming the step it was typed at. A button is judged by the change's check, so a refusal is
the queue's: the press's acknowledgement is updated and one line records it.

**The last answer hands over.** It saves the complete draft on the lane and asks, as a follow-on
of its own save, `signup.submit` of the league: the signup is saved, the driver moved to Pending
Admin Approval and the wizard parked in one save, the driver's name read first by a job of its
own (a save waits on no second connection) and their timers taken away after. That asks back
`signup.panel` on the driver's lane, which locks typing, posts the review panel and takes the ⏳
off, so the mark outlives the whole hand-over (R10). The panel's post cannot be discarded (#439,
slice 8): dropped, the driver would wait for approval with no panel, an unsettled signup that
refuses the season's placements, so a Discard on it is refused and only a Retry moves it. Nor can
`arm_inactivity` or a correction's `commit` (#439, slice 8): dropped, a signup would never time
out, or a correction would never be submitted, while the driver corrects it, either an unsettled
signup that refuses the season's placements. A driver whose signup a close of the window
ended meanwhile is no longer due: the submit is dropped, nothing saved.

**Rejected:** keeping the draft in memory until the last answer, which a restart would lose; one
change for the whole signup, which would hold a driver's lane until the league had approved them.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import aiosqlite
import discord

from leaguebot.core.db.database import get_connection
from leaguebot.core.models.change import (
    FollowOn,
    PlannedStep,
    StepFailedOnDiscord,
    StepKind,
    StepResult,
    Verdict,
)
from leaguebot.core.models.driver_profile import DriverState
from leaguebot.core.services import track_service, typed_turns
from leaguebot.core.services.change_queue import (
    ChangeType,
    CheckContext,
    OutcomeContext,
    Step,
    StepContext,
)
from leaguebot.core.utils.in_a_job import posts_in_a_job
from leaguebot.core.utils.input_validator import SIGNUP_ANSWER, parse_nationality
from leaguebot.core.utils.league_server import league_guild
from leaguebot.core.utils.log_lines import refusal_line
from leaguebot.core.utils.member_names import member_named
from leaguebot.signup.models.signup_module import SignupRecord, SignupWizardRecord, WizardState
from leaguebot.signup.services.closing_steps import INACTIVITY_TIMER
from leaguebot.signup.services.signup_posting import post
from leaguebot.signup.services.wizard_service import (
    _CORRECTION_LABELS,
    _CORRECTION_PARAMETER_STATES,
    _REVIEW_PANEL_MENTIONS,
    _TEAM_PICKED,
    WizardService,
    _wizard_gone_or_answered,
    build_step_view,
)

if TYPE_CHECKING:
    from leaguebot.core.services.change_queue import ChangeQueue
    from leaguebot.core.services.driver_service import DriverService
    from leaguebot.core.utils.league_bot import LeagueBot
    from leaguebot.signup.services.signup_module_service import SignupModuleService

ANSWER = "signup.answer"
SUBMIT = "signup.submit"
PANEL = "signup.panel"
JUDGE = "judge"
REFUSE = "refuse"
SAVE_ANSWER = "save_answer"
ARM_INACTIVITY = "arm_inactivity"
SHOW_NEXT = "show_next"
NAME_DRIVER = "name_driver"
COMMIT = "commit"
DISARM = "disarm_timers"
POST_PANEL = "post_panel"

#: How long a driver has to answer before their signup lapses.
INACTIVITY = timedelta(hours=24)

#: The step each state asks, as a refusal's log line names it (the review's buttons name them so).
STEP_NAMES: dict[WizardState, str] = {
    WizardState.COLLECTING_NATIONALITY: "Nationality",
    WizardState.COLLECTING_PLATFORM: "Platform",
    WizardState.COLLECTING_PLATFORM_ID: "Platform ID",
    WizardState.COLLECTING_AVAILABILITY: "Availability",
    WizardState.COLLECTING_DRIVER_TYPE: "Driver Type",
    WizardState.COLLECTING_PREFERRED_TEAMS: "Preferred Teams",
    WizardState.COLLECTING_PREFERRED_TEAMMATE: "Preferred Teammate",
    WizardState.COLLECTING_LAP_TIME: "Lap Times",
    WizardState.COLLECTING_NOTES: "Notes",
}

_SEQUENCE = list(STEP_NAMES)
#: The states a button answers alone, so that typing is taken away while they stand.
_BUTTON_ONLY = WizardService._BUTTON_ONLY_STATES
#: The platforms and driver types a typed or pressed answer may name.
_PLATFORMS = ["Steam", "EA", "Xbox", "PlayStation"]
_DRIVER_TYPES = ["Full-Time Driver", "Reserve Driver"]
#: The button of each step, by the state it answers.
BUTTON_STATES: dict[str, WizardState] = {
    "platform": WizardState.COLLECTING_PLATFORM,
    "driver_type": WizardState.COLLECTING_DRIVER_TYPE,
    "team": WizardState.COLLECTING_PREFERRED_TEAMS,
    "teammate": WizardState.COLLECTING_PREFERRED_TEAMMATE,
    "notes": WizardState.COLLECTING_NOTES,
}
#: Where a wizard still stands that a driver is mid-signup or waiting on review.
_OPEN_DRIVER_STATES = (
    DriverState.PENDING_SIGNUP_COMPLETION,
    DriverState.PENDING_DRIVER_CORRECTION,
    DriverState.PENDING_ADMIN_APPROVAL,
    DriverState.AWAITING_CORRECTION_PARAMETER,
)

__all__ = [
    "ANSWER", "Accepted", "PANEL", "Refused", "STEP_NAMES", "SUBMIT", "answer_change",
    "judge_answer", "judge_button", "ordinal_prompt", "panel_change", "submit_change",
    "take_typed_answer",
]


@dataclass(frozen=True)
class Accepted:
    """An answer the wizard keeps. *answers* are the keys to merge into the draft and *drop* the
    bookkeeping keys to take out of it; *lap_advanced* says a lap time was given for the track
    the wizard asks, and *sub_step* that the team step has more picks to take. *line* is what a
    button's answer leaves in the log channel, *ends_step* whether it ends its step (a correction
    ended by it writes "Correction submitted" instead)."""

    answers: dict[str, Any]
    drop: tuple[str, ...] = ()
    lap_advanced: bool = False
    sub_step: bool = False
    line: str | None = None
    ends_step: bool = True


@dataclass(frozen=True)
class Refused:
    """An answer the wizard turns away: *reply* is said in the driver's channel (or to the
    presser), and *step* is the step the log line names."""

    step: str
    reply: str


def _refused(step: str, reply: str) -> Refused:
    return Refused(step, reply)


def _teams_of(snapshot: Any) -> tuple[list[str], dict[str, str]]:
    """The teams the wizard offers, by full name, and each typed name that stands for one."""
    names = list(snapshot.team_names)
    typed = {name.casefold(): name for name in names}
    typed.update({
        short.casefold(): full for short, full in snapshot.team_shorthands.items() if full in names
    })
    return names, typed


def judge_answer(wizard: SignupWizardRecord, message: Any) -> Accepted | Refused:
    """Judge what the driver typed at the question their wizard asks.

    *message* gives ``content`` and ``attachments``. Pure: the wizard is read and nothing is
    saved or posted, so that the check and the job that saves it share one set of rules.
    """
    state = wizard.wizard_state
    snapshot = wizard.config_snapshot
    step = STEP_NAMES.get(state, "")
    if snapshot is None or state not in STEP_NAMES:
        return _refused(step, _wizard_gone_or_answered(wizard))
    content = str(message.content)
    raw = content.strip()

    if state is WizardState.COLLECTING_NATIONALITY:
        value = parse_nationality(content)
        if value is None:
            return _refused(step, (
                "❌ Invalid nationality. Please enter your full nationality (e.g. `British`) "
                "or country name (e.g. `United Kingdom`), or type `other`."
            ))
        return Accepted({"nationality": value})

    if state is WizardState.COLLECTING_PLATFORM:
        match = next((p for p in _PLATFORMS if p.lower() == raw.lower()), None)
        if match is None:
            return _refused(step, f"❌ Invalid platform. Please choose one of: {' / '.join(_PLATFORMS)}")
        return Accepted({"platform": match})

    if state is WizardState.COLLECTING_PLATFORM_ID:
        if not raw:
            return _refused(step, "❌ Platform ID cannot be empty.")
        refusal = SIGNUP_ANSWER.check("platform ID", raw).refusal
        if refusal is not None:
            return _refused(step, f"❌ {refusal}")
        return Accepted({"platform_id": raw})

    if state is WizardState.COLLECTING_AVAILABILITY:
        # The driver types the display numbers they were shown; the durable slot ids are kept
        # (issue #126), and every reply quotes the numbers typed.
        try:
            selected = [int(part) for part in re.split(r"[,\s]+", raw) if part]
        except ValueError:
            return _refused(step, "❌ Please enter slot IDs as numbers (e.g. `1 3`).")
        by_number = {slot.slot_sequence_id: slot for slot in snapshot.slots}
        bad = [str(number) for number in selected if number not in by_number]
        if bad:
            return _refused(step, (
                f"❌ Unknown slot ID(s): {', '.join(bad)}. "
                f"Valid IDs: {', '.join(str(number) for number in sorted(by_number))}"
            ))
        if not selected:
            return _refused(step, "❌ Please select at least one time slot.")
        return Accepted({"availability_slot_ids": [by_number[number].slot_id for number in selected]})

    if state is WizardState.COLLECTING_DRIVER_TYPE:
        match = next((t for t in _DRIVER_TYPES if t.lower() == raw.lower()), None)
        if match is None:
            return _refused(step, f"❌ Please choose one of: {' / '.join(_DRIVER_TYPES)}")
        return Accepted({"driver_type": match})

    if state is WizardState.COLLECTING_PREFERRED_TEAMS:
        if raw.lower() == "no preference":
            return Accepted({"preferred_teams": []})
        names, typed = _teams_of(snapshot)
        parts = [part.strip() for part in re.split(r"[,\n]+", raw) if part.strip()]
        if len(parts) > 3:
            return _refused(step, "❌ Please select up to 3 teams.")
        bad_teams = [part for part in parts if part.casefold() not in typed]
        if bad_teams:
            listed = ", ".join(f"`{each}`" for each in bad_teams)
            valid = ", ".join(f"`{name}`" for name in names)
            return _refused(step, f"❌ Unknown team(s): {listed}.\nValid teams: {valid}")
        return Accepted({"preferred_teams": [typed[part.casefold()] for part in parts]})

    if state is WizardState.COLLECTING_PREFERRED_TEAMMATE:
        refusal = SIGNUP_ANSWER.check("preferred teammate", raw).refusal
        if refusal is not None:
            return _refused(step, f"❌ {refusal}")
        return Accepted({"preferred_teammate": None if raw.lower() == "no preference" else raw})

    if state is WizardState.COLLECTING_LAP_TIME:
        index = wizard.current_lap_track_index
        if index >= len(snapshot.selected_track_ids):
            # Should not happen: accepted so that the wizard moves on, with no time stored.
            return Accepted({})
        if snapshot.time_image_required and not message.attachments:
            return _refused(step, (
                "❌ A screenshot of your lap time is required. "
                "Please attach an image along with your time."
            ))
        normalised = WizardService._normalise_lap_time(content)
        if normalised is None:
            label = "Time Trial" if snapshot.time_type == "TIME_TRIAL" else "Short Qualification"
            return _refused(step, f"❌ Invalid {label} time. Use format `M:ss.mmm` (e.g. `1:23.456`).")
        lap_times = dict(wizard.draft_answers.get("lap_times", {}))
        lap_times[snapshot.selected_track_ids[index]] = normalised
        return Accepted({"lap_times": lap_times}, lap_advanced=True)

    # COLLECTING_NOTES
    if raw.lower() == "no notes":
        return Accepted({"notes": None})
    if len(raw) > 50:
        return _refused(step, "❌ Notes must be 50 characters or fewer.")
    refusal = SIGNUP_ANSWER.check("notes", raw).refusal
    if refusal is not None:
        return _refused(step, f"❌ {refusal}")
    return Accepted({"notes": raw})


def judge_button(
    wizard: SignupWizardRecord | None, button: str, value: str | None
) -> Accepted | Refused:
    """Judge a press of a wizard button: *button* is its kind (`BUTTON_STATES`) and *value* what
    it names (the platform, the driver type, a team; None for No Preference and No Notes).

    A press on a step the wizard has moved past, or with no wizard, is refused; so is a team the
    driver has already picked (a button on an earlier sub-step's message still offers it). Pure.
    """
    if wizard is None or wizard.config_snapshot is None:
        return _refused("", _wizard_gone_or_answered(None))
    if wizard.wizard_state is not BUTTON_STATES[button]:
        return _refused("", _wizard_gone_or_answered(wizard))
    if button == "platform":
        return Accepted({"platform": value}, line=f"Platform: {value}")
    if button == "driver_type":
        return Accepted({"driver_type": value}, line=f"Driver type: {value}")
    if button == "teammate":
        return Accepted({"preferred_teammate": None}, line="Preferred teammate: no preference")
    if button == "notes":
        return Accepted({"notes": None})
    draft = wizard.draft_answers
    step = int(draft.get("_pref_teams_step", 0))
    picks = list(draft.get("preferred_teams") or [])
    if value is None:
        return Accepted(
            {"preferred_teams": picks}, drop=("_pref_teams_step",),
            line="Preferred team: no preference",
        )
    if value in picks:
        return _refused("", _TEAM_PICKED)
    picks.append(value)
    remaining = [name for name in wizard.config_snapshot.team_names if name not in picks]
    done = step + 1 >= 3 or not remaining
    if done:
        return Accepted(
            {"preferred_teams": picks}, drop=("_pref_teams_step",),
            line=f"Preferred team: {value}",
        )
    return Accepted(
        {"preferred_teams": picks, "_pref_teams_step": step + 1}, sub_step=True,
        line=f"Preferred team: {value}", ends_step=False,
    )


def ordinal_prompt(step: int, picks: list[str]) -> str:
    """The question for the team step's *step*-th sub-step (1 or 2), after *picks*."""
    ordinal = ["1st", "2nd", "3rd"][step]
    return (
        f"**Step 6 — {ordinal} Preferred Team** *(so far: {', '.join(picks)})*\n"
        f"Select your {ordinal} preferred team, or press **No Preference** to finish."
    )


def _next(wizard: SignupWizardRecord, accepted: Accepted) -> tuple[str, WizardState | None]:
    """Where the wizard goes once *accepted* is kept: ("move", state), ("stay", state) for the
    team sub-step and the lap time of the next track, or ("done", None) once the signup is
    complete or a correction ends. The order of the questions and the steps skipped are the
    wizard's own: nationality only where required, no teams for a reserve, lap times once per
    selected track."""
    snapshot = wizard.config_snapshot
    assert snapshot is not None
    state = wizard.wizard_state
    draft = {**wizard.draft_answers, **accepted.answers}
    for key in accepted.drop:
        draft.pop(key, None)
    lap_index = wizard.current_lap_track_index + (1 if accepted.lap_advanced else 0)
    if accepted.sub_step:
        return "stay", state
    if state is WizardState.COLLECTING_LAP_TIME and lap_index < len(snapshot.selected_track_ids):
        return "stay", state
    if draft.get("_is_correction"):
        return "done", None
    for follower in _SEQUENCE[_SEQUENCE.index(state) + 1:]:
        if follower is WizardState.COLLECTING_NATIONALITY and not snapshot.nationality_required:
            continue
        if follower is WizardState.COLLECTING_PREFERRED_TEAMS and draft.get("driver_type") == "Reserve Driver":
            continue
        if follower is WizardState.COLLECTING_LAP_TIME and lap_index >= len(snapshot.selected_track_ids):
            continue
        return "move", follower
    return "done", None


def _owner(display_name: str | None, user_id: int | str) -> str:
    """Whose wizard a line names: "Alex's", or "<@id>'s" where no name is to be had."""
    return f"{display_name}'s" if isinstance(display_name, str) and display_name else f"<@{user_id}>'s"


def _what(step: str, owner: str) -> str:
    return f"the {step} step of {owner} signup wizard"


async def take_typed_answer(
    bot: "LeagueBot", message: discord.Message, wizard: SignupWizardRecord, *, queue: "ChangeQueue"
) -> None:
    """What the signup cog does with a message the wizard's driver typed in their channel:
    nothing where the wizard asks nothing; else turned away where an earlier answer still waits
    (a note, the message deleted, a line in the log), else marked ⏳ at once and asked as a turn
    of the driver's lane. The answer is judged when its turn comes."""
    state = wizard.wizard_state
    if state not in STEP_NAMES:
        return
    author = message.author
    owner = _owner(getattr(author, "display_name", None), author.id)
    what = _what(STEP_NAMES[state], owner)
    if await typed_turns.turn_waiting(bot.db_path, queue.types, message.channel.id):
        await typed_turns.turn_away(message, typed_turns.TURN_AWAY_NOTE)
        await bot.output_router.post_log(refusal_line(
            member_named(getattr(author, "display_name", None), author.id), what,
            "an earlier answer is still waiting its turn",
        ))
        return
    await typed_turns.mark(message)
    await queue.ask(
        ANSWER,
        {
            "user_id": str(author.id),
            "channel_id": message.channel.id,
            "state": state.value,
            "message_id": message.id,
            "content": message.content,
            "attachments": [attachment.url for attachment in message.attachments],
            "typed_at": message.created_at.isoformat(),
            "owner": owner,
        },
        actor=author,
        what=what,
    )


def _channel_of(payload: dict[str, Any]) -> int:
    return int(payload["channel_id"])


async def _driver_open(db: aiosqlite.Connection, payload: dict[str, Any]) -> bool:
    """Whether the driver of the channel still has a signup in hand: their wizard stands and they
    are mid-signup or waiting on review. A close, a withdrawal or an expiry ends it."""
    marks = ", ".join("?" for _ in _OPEN_DRIVER_STATES)
    row = await (
        await db.execute(
            "SELECT 1 FROM signup_wizard_records w JOIN driver_profiles d "
            "ON d.discord_user_id = w.discord_user_id "
            f"WHERE w.signup_channel_id = ? AND d.current_state IN ({marks})",
            (_channel_of(payload), *(each.value for each in _OPEN_DRIVER_STATES)),
        )
    ).fetchone()
    return row is not None


def _saved(ctx: StepContext) -> bool:
    return any(
        view.name == SAVE_ANSWER and view.done and (view.result or {}).get("saved")
        for view in ctx.steps
    )


async def _set_typing(
    channel: discord.TextChannel, member: discord.Member, *, allowed: bool, reason: str
) -> None:
    try:
        await channel.set_permissions(
            member, view_channel=True, send_messages=allowed, reason=reason
        )
    except discord.HTTPException as error:
        raise StepFailedOnDiscord(
            f"typing in the signup channel {channel.id} could not be {'given back' if allowed else 'taken away'}"
        ) from error


async def _text_channel(ctx: StepContext, channel_id: int) -> discord.TextChannel | None:
    guild = await league_guild(ctx.bot)
    found = None if guild is None else guild.get_channel(channel_id)
    return found if isinstance(found, discord.TextChannel) else None


def answer_change(
    *,
    signup: Callable[[], "SignupModuleService"],
    queue_of: Callable[[], "ChangeQueue"],
    now: Callable[[], datetime],
    arm_once: Callable[[str, str, datetime], None],
) -> ChangeType:
    """The `signup.answer` change type, for the builder to register, on the lane of the driver's
    signup channel.

    The payload is ``user_id`` (the driver's account, as text), ``channel_id`` and, of a typed
    answer, ``state`` (the question it answers), ``message_id``, ``content``, ``attachments`` (the
    urls), ``typed_at`` and ``owner`` (whose wizard, for the line), or, of a button's, ``button``
    and ``value`` (`judge_button`). *signup* is the module service, *queue_of* the change queue
    the questions' buttons are handed, *now* the queue's clock and *arm_once* the scheduler
    service's one-off timer, handed so that no job looks the scheduler up on the bot.

    A button is judged by the check, so its refusal is the queue's. A typed answer is judged by
    its `judge` job, which plans what follows. The change writes only the wizard's row.
    """

    async def check(ctx: CheckContext) -> Verdict:
        payload = ctx.payload
        if payload.get("button") is None:
            return Verdict.go()
        wizard = await signup().get_wizard(str(payload["user_id"]))
        judged = judge_button(wizard, str(payload["button"]), payload.get("value"))
        if isinstance(judged, Refused):
            return Verdict.refuse(f"⛔ {judged.reply}")
        return Verdict.go()

    async def judge(ctx: StepContext) -> StepResult:
        payload = ctx.payload
        user_id = str(payload["user_id"])
        channel_id = _channel_of(payload)
        mark = (
            {"channel_id": channel_id, "message_id": int(payload["message_id"])}
            if payload.get("message_id") is not None else None
        )
        clear = (PlannedStep(typed_turns.CLEAR_MARK, mark),) if mark is not None else ()
        wizard = await signup().get_wizard(user_id)
        if wizard is None or wizard.config_snapshot is None:
            return StepResult(result={"judged": False}, then=clear)
        button = payload.get("button")
        if button is not None:
            judged: Accepted | Refused = judge_button(wizard, str(button), payload.get("value"))
        else:
            asked = WizardState(payload["state"])
            if wizard.wizard_state is not asked:
                judged = _refused(STEP_NAMES[asked], "⛔ That step has already been answered.")
            else:
                judged = judge_answer(wizard, SimpleNamespace(
                    content=payload["content"], attachments=list(payload.get("attachments") or []),
                ))
        if isinstance(judged, Refused):
            reason = judged.reply.splitlines()[0].removeprefix("❌").removeprefix("⛔").strip()
            what = _what(judged.step, str(payload.get("owner") or _owner(None, user_id)))
            return StepResult(
                result={"refused": reason},
                lines=(refusal_line(ctx.named, what, reason),),
                then=(PlannedStep(REFUSE, {"channel_id": channel_id, "text": judged.reply}), *clear)
                if mark is not None else (),
            )
        kind, to_state = _next(wizard, judged)
        line = None
        if judged.line is not None and not (
            judged.ends_step and wizard.draft_answers.get("_is_correction")
        ):
            line = f"{ctx.named} | Signup | {judged.line}"
        saving = {
            "user_id": user_id, "channel_id": channel_id,
            "state": wizard.wizard_state.value, "answers": judged.answers,
            "drop": list(judged.drop), "lap_advanced": judged.lap_advanced,
            "kind": kind, "to_state": None if to_state is None else to_state.value,
            "line": line, "mark": mark,
        }
        planned = [PlannedStep(SAVE_ANSWER, saving)]
        if kind != "done":
            planned.append(PlannedStep(ARM_INACTIVITY, {"user_id": user_id}))
            planned.append(PlannedStep(SHOW_NEXT, {
                "user_id": user_id, "channel_id": channel_id, "from_state": wizard.wizard_state.value,
                "kind": kind,
            }))
            planned.extend(clear)
        return StepResult(result={"judged": kind}, then=tuple(planned))

    async def refuse(ctx: StepContext) -> StepResult:
        channel = await _text_channel(ctx, int(ctx.step_payload["channel_id"]))
        if channel is None:
            return StepResult(result={"no_channel": True})
        await post(
            channel, str(ctx.step_payload["text"]), mentions=discord.AllowedMentions.none(),
            refused=f"the refusal could not be posted in channel {channel.id}",
        )
        return StepResult(result={})

    async def save_answer(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        pl = ctx.step_payload
        wizard = await signup().get_wizard_on(db, str(pl["user_id"]))
        if wizard is None or wizard.wizard_state.value != pl["state"]:
            # The wizard moved on, or ended, since the turn was judged.
            return StepResult(result={"saved": False})
        wizard.draft_answers.update(pl["answers"])
        for key in pl["drop"]:
            wizard.draft_answers.pop(key, None)
        if pl["lap_advanced"]:
            wizard.current_lap_track_index += 1
        if pl["kind"] == "move":
            wizard.wizard_state = WizardState(pl["to_state"])
        wizard.last_activity_at = now().isoformat()
        await signup().save_wizard_on(db, wizard)
        lines = (str(pl["line"]),) if pl.get("line") else ()
        if pl["kind"] != "done":
            return StepResult(result={"saved": True}, lines=lines)
        return StepResult(
            result={"saved": True}, lines=lines,
            follow_ons=(FollowOn(
                SUBMIT,
                {"user_id": pl["user_id"], "channel_id": pl["channel_id"], "mark": pl.get("mark")},
                "submitting a signup",
            ),),
        )

    async def arm_inactivity(ctx: StepContext) -> StepResult:
        arm_once(INACTIVITY_TIMER, str(ctx.step_payload["user_id"]), now() + INACTIVITY)
        return StepResult(result={})

    @posts_in_a_job
    async def show_next(ctx: StepContext) -> StepResult:
        pl = ctx.step_payload
        user_id = str(pl["user_id"])
        channel = await _text_channel(ctx, int(pl["channel_id"]))
        if channel is None:
            raise StepFailedOnDiscord("the signup channel could not be found to post in")
        wizard = await signup().get_wizard(user_id)
        if wizard is None or wizard.config_snapshot is None:
            return StepResult(result={"no_wizard": True})
        snapshot = wizard.config_snapshot
        member = channel.guild.get_member(int(user_id))
        moved = pl["kind"] == "move"
        if member is not None and moved:
            if WizardState(pl["from_state"]) in _BUTTON_ONLY:
                await _set_typing(channel, member, allowed=True, reason="Signup wizard button step answered")
            if wizard.wizard_state in _BUTTON_ONLY:
                await _set_typing(channel, member, allowed=False, reason="Signup wizard channel hold")
        queue = queue_of()
        state = wizard.wizard_state
        if not moved and state is WizardState.COLLECTING_PREFERRED_TEAMS:
            from leaguebot.signup.services.signup_views import PreferredTeamsButtonView

            picks = list(wizard.draft_answers.get("preferred_teams") or [])
            text = ordinal_prompt(int(wizard.draft_answers.get("_pref_teams_step", 1)), picks)
            view: discord.ui.View = PreferredTeamsButtonView(
                user_id, queue, snapshot.team_names, excluded=picks
            )
        else:
            async with get_connection(ctx.db_path) as db:
                track_map = await track_service.get_track_name_map(db)
            text = WizardService._prompt_for_state(state, snapshot, wizard, track_name_map=track_map)
            view = build_step_view(queue, state, user_id, snapshot.team_names)
        await post(
            channel, text, view=view, mentions=discord.AllowedMentions.none(),
            refused=f"the next question could not be posted in channel {channel.id}",
        )
        return StepResult(result={})

    async def describe_judge(_ctx: StepContext) -> str:
        return "reading a signup answer"

    async def describe_refuse(_ctx: StepContext) -> str:
        return "telling the driver their answer was not taken"

    async def describe_save(_ctx: StepContext) -> str:
        return "keeping a signup answer"

    async def describe_arm(_ctx: StepContext) -> str:
        return "arming the signup's 24 hours again"

    async def would_never_time_out(ctx: StepContext) -> str:
        return (
            f"<@{ctx.step_payload['user_id']}>'s signup would never time out if they stopped "
            "answering, and while they correct it the season's placements could not be "
            "confirmed until the bot restarts"
        )

    async def describe_show(ctx: StepContext) -> str:
        return f"asking the next question in <#{ctx.step_payload['channel_id']}>"

    def outcome(ctx: OutcomeContext) -> str:
        for view in ctx.steps:
            if view.name == JUDGE and (view.result or {}).get("refused"):
                return f"⛔ {(view.result or {})['refused']}"
        return "✅ Your answer has been taken."

    steps = {
        JUDGE: Step(JUDGE, StepKind.ACT, judge, describe=describe_judge),
        REFUSE: Step(REFUSE, StepKind.ACT, refuse, describe=describe_refuse),
        SAVE_ANSWER: Step(SAVE_ANSWER, StepKind.SAVE, save_answer, describe=describe_save),
        ARM_INACTIVITY: Step(
            ARM_INACTIVITY, StepKind.ACT, arm_inactivity, still_due=_saved_due,
            describe=describe_arm, undiscardable=would_never_time_out,
        ),
        SHOW_NEXT: Step(
            SHOW_NEXT, StepKind.ACT, show_next, still_due=_saved_due, describe=describe_show
        ),
        typed_turns.CLEAR_MARK: typed_turns.clear_mark_step(),
    }
    return ChangeType(
        kind=ANSWER,
        opening=(PlannedStep(JUDGE),),
        steps=steps,
        check=check,
        key=lambda p: f"{ANSWER}:{p['user_id']}:{p.get('state') or p.get('button')}:"
                      f"{p.get('message_id') or p.get('value')}",
        doing=lambda _p: "Taking a signup answer",
        outcome=outcome,
        lane=lambda p: f"channel:{_channel_of(p)}",
        conversation_open=_driver_open,
        writes=("signup_wizard_records",),
        turn_channel=_channel_of,
    )


async def _saved_due(ctx: StepContext) -> bool:
    """Whether the answer was kept: a job after the save is not due where the save found the
    wizard moved on."""
    return _saved(ctx)


def submit_change(
    *,
    signup: Callable[[], "SignupModuleService"],
    drivers: Callable[[], "DriverService"],
    cancel_timers: Callable[[str], None],
) -> ChangeType:
    """The `signup.submit` change type, for the builder to register, on the league lane: the last
    answer's hand-over (decision 22, Q-A, "every change of a driver's status is the league's").

    The payload is ``user_id``, ``channel_id`` and the ⏳ ``mark`` of the last typed answer, if
    any. `name_driver` reads the driver's name and state before anything is written (a save waits
    on no second connection, and a correction is told from a first submission by the state they
    stood in); `commit` is one save: the signup record, the driver at Pending Admin Approval, the
    wizard parked with its draft cleared, and the line ("Submitted", or "Correction submitted"
    naming what was corrected); `disarm_timers` takes the 24 hours away. It asks back
    `signup.panel` on the driver's lane. Behind a close of the window, or any end of the signup,
    it is no longer due and nothing is saved.
    """

    async def check(ctx: CheckContext) -> Verdict:
        wizard = await signup().get_wizard(str(ctx.payload["user_id"]))
        if wizard is None or wizard.wizard_state is WizardState.UNENGAGED:
            return Verdict.not_due("the signup has ended")
        return Verdict.go()

    async def name_driver(ctx: StepContext) -> StepResult:
        user_id = str(ctx.payload["user_id"])
        guild = await league_guild(ctx.bot)
        member = None if guild is None else guild.get_member(int(user_id))
        profile = await drivers().get_profile(user_id)
        return StepResult(result={
            "named": member_named(getattr(member, "display_name", None), int(user_id)),
            "prior_state": None if profile is None else profile.current_state.value,
        })

    def _named(ctx: StepContext) -> tuple[str, str | None]:
        for view in ctx.steps:
            if view.name == NAME_DRIVER and view.done:
                found = view.result or {}
                return str(found["named"]), found.get("prior_state")
        return f"<@{ctx.payload['user_id']}>", None

    async def commit(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        user_id = str(ctx.payload["user_id"])
        named, prior = _named(ctx)
        wizard = await signup().get_wizard_on(db, user_id)
        if wizard is None or wizard.wizard_state is WizardState.UNENGAGED:
            return StepResult(result={"committed": False})
        draft = wizard.draft_answers
        correcting = bool(draft.get("_is_correction"))
        existing = await signup().get_record_on(db, user_id)
        if correcting:
            # A correction amends the signup it was asked of, and makes no new one (#220).
            if existing is None:
                return StepResult(result={"committed": False})
            record = existing
            for key in (
                "nationality", "platform", "platform_id", "availability_slot_ids", "driver_type",
                "preferred_teams", "preferred_teammate", "lap_times", "notes",
            ):
                if key in draft:
                    setattr(record, key, draft[key])
        else:
            record = SignupRecord(
                id=-1, discord_user_id=user_id,
                discord_username=draft.get("discord_username"),
                server_display_name=draft.get("server_display_name"),
                nationality=draft.get("nationality"), platform=draft.get("platform"),
                platform_id=draft.get("platform_id"),
                availability_slot_ids=draft.get("availability_slot_ids", []),
                driver_type=draft.get("driver_type"),
                preferred_teams=draft.get("preferred_teams", []),
                preferred_teammate=draft.get("preferred_teammate"),
                lap_times=draft.get("lap_times", {}), notes=draft.get("notes"),
                signup_channel_id=wizard.signup_channel_id,
            )
            if prior == DriverState.PENDING_DRIVER_CORRECTION.value and existing is not None:
                record.id = existing.id
        corrected = next(
            (key for key, state in _CORRECTION_PARAMETER_STATES.items() if state == wizard.wizard_state),
            None,
        )
        await signup().save_record_on(db, record)
        await drivers().transition_on(db, user_id, DriverState.PENDING_ADMIN_APPROVAL)
        wizard.wizard_state = WizardState.UNENGAGED
        wizard.draft_answers = {}
        await signup().save_wizard_on(db, wizard)
        if not correcting:
            text = "Submitted"
        elif corrected is None:
            text = "Correction submitted"
        else:
            text = f"Correction submitted: {_CORRECTION_LABELS[corrected]}"
        return StepResult(
            result={"committed": True},
            lines=(f"{named} | Signup | {text}",),
            follow_ons=(FollowOn(
                PANEL,
                {"user_id": user_id, "channel_id": _channel_of(ctx.payload),
                 "mark": ctx.payload.get("mark")},
                "posting a signup's review panel",
            ),),
        )

    async def disarm(ctx: StepContext) -> StepResult:
        cancel_timers(str(ctx.payload["user_id"]))
        return StepResult(result={})

    async def describe_name(_ctx: StepContext) -> str:
        return "reading the driver's name for their signup"

    async def describe_commit(_ctx: StepContext) -> str:
        return "saving the driver's signup"

    async def describe_disarm(_ctx: StepContext) -> str:
        return "taking the signup's 24 hours away"

    async def would_never_be_submitted(ctx: StepContext) -> str:
        return (
            f"<@{ctx.payload['user_id']}>'s signup would never be submitted, and while they "
            "correct it the season's placements could not be confirmed until the bot restarts"
        )

    return ChangeType(
        kind=SUBMIT,
        opening=(PlannedStep(NAME_DRIVER), PlannedStep(COMMIT), PlannedStep(DISARM)),
        steps={
            NAME_DRIVER: Step(NAME_DRIVER, StepKind.ACT, name_driver, describe=describe_name),
            COMMIT: Step(
                COMMIT, StepKind.SAVE, commit, describe=describe_commit,
                undiscardable=would_never_be_submitted,
            ),
            DISARM: Step(DISARM, StepKind.ACT, disarm, describe=describe_disarm),
        },
        check=check,
        key=lambda p: f"{SUBMIT}:{p['user_id']}",
        doing=lambda _p: "Submitting a signup",
        outcome=lambda _ctx: "",
        turn_channel=_channel_of,
    )


def panel_change(
    *, signup: Callable[[], "SignupModuleService"], queue_of: Callable[[], "ChangeQueue"]
) -> ChangeType:
    """The `signup.panel` change type, for the builder to register, on the driver's lane: the
    league asked it back once the signup was saved. Its one job `post_panel` takes the driver's
    typing away, now that the channel holds a review rather than a question, and posts the
    review panel (users may be notified, never a group, #362); then `clear_mark` takes the ⏳ off
    the last answer, the mark outliving the whole hand-over. The payload is ``user_id``,
    ``channel_id`` and the ``mark``. A channel gone from the server is nothing to post in. *queue_of*
    gives the change queue the panel's buttons are handed."""

    def plan(payload: dict[str, Any]) -> tuple[PlannedStep, ...]:
        shared = {"user_id": str(payload["user_id"]), "channel_id": _channel_of(payload)}
        planned = [PlannedStep(POST_PANEL, shared)]
        if payload.get("mark") is not None:
            planned.append(PlannedStep(typed_turns.CLEAR_MARK, dict(payload["mark"])))
        return tuple(planned)

    async def standing(_ctx: CheckContext) -> Verdict:
        return Verdict.go()

    async def open_(db: aiosqlite.Connection, payload: dict[str, Any]) -> bool:
        row = await (
            await db.execute(
                "SELECT 1 FROM signup_wizard_records WHERE signup_channel_id = ?",
                (_channel_of(payload),),
            )
        ).fetchone()
        return row is not None

    @posts_in_a_job
    async def post_panel(ctx: StepContext) -> StepResult:
        user_id = str(ctx.step_payload["user_id"])
        channel = await _text_channel(ctx, int(ctx.step_payload["channel_id"]))
        if channel is None:
            return StepResult(result={"no_channel": True})
        member = channel.guild.get_member(int(user_id))
        if member is not None:
            await _set_typing(channel, member, allowed=False, reason="Signup wizard channel hold")
        record = await signup().get_record(user_id)
        wizard = await signup().get_wizard(user_id)
        if record is None:
            return StepResult(result={"no_record": True})
        slot_labels = {
            slot.slot_id: slot.display_label
            for slot in (wizard.config_snapshot.slots if wizard and wizard.config_snapshot else [])
        }
        async with get_connection(ctx.db_path) as db:
            track_map = await track_service.get_track_name_map(db)
        from leaguebot.signup.services.signup_views import AdminReviewView

        await post(
            channel,
            WizardService._format_review_panel(record, slot_labels, track_name_map=track_map),
            view=AdminReviewView(user_id, change_queue=queue_of()), mentions=_REVIEW_PANEL_MENTIONS,
            refused=f"the review panel could not be posted in channel {channel.id}",
        )
        return StepResult(result={})

    async def describe_panel(ctx: StepContext) -> str:
        return f"posting the review panel in <#{ctx.step_payload['channel_id']}>"

    async def would_wait_unreviewed(ctx: StepContext) -> str:
        return (
            f"<@{ctx.step_payload['user_id']}> would wait for approval with no review panel, "
            "and the season's placements could not be confirmed"
        )

    return ChangeType(
        kind=PANEL,
        opening=(),
        plan=plan,
        steps={
            POST_PANEL: Step(
                POST_PANEL, StepKind.ACT, post_panel, describe=describe_panel,
                undiscardable=would_wait_unreviewed,
            ),
            typed_turns.CLEAR_MARK: typed_turns.clear_mark_step(),
        },
        check=standing,
        key=lambda p: f"{PANEL}:{p['user_id']}",
        doing=lambda _p: "Posting a signup's review panel",
        outcome=lambda _ctx: "",
        lane=lambda p: f"channel:{_channel_of(p)}",
        conversation_open=open_,
        writes=(),
        turn_channel=_channel_of,
    )

