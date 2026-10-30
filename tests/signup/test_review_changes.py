"""A signup's review and correction cycle on the change queue (#439, slice 6b, C84–C87).

Today the review panel's Approve, a reason typed after Request Changes, the choice of what to
correct and the correction's five-minute lapse each change the driver's status and post in their
signup channel at once, off the queue, and the five minutes are an asyncio task from the reason.
On the queue Approve asks `signup.approve` on the league lane — the status, the lap sum written to
the signup record by signup itself (C84), the driver role, granted where it can be and named where
it cannot — which asks back `signup.closing` on the driver's lane for the notice (Q-B). The reason
after Request Changes asks `signup.request_changes` on the league lane, which asks back
`signup.choices` on the driver's lane: the reason deleted, the choices posted, their time saved as
`correction_offered_at`, and the five minutes armed by a job of their own after it (Q23). A choice
asks `signup.correction.choose` on the league lane, judged by when it was pressed, which asks back
`signup.recollect` on the lane. The lapse, asked by its timer or the restart, is the league's whole
(decision 31), and not due where a choice pressed in time is queued.

The league: `setup_league` at Signups, the signup module on and its window open. Kimi (105) has
signed up and his signup awaits approval unless a test says otherwise: his signup record holds
two lap times (1:30.000 and 1:31.500), his signup channel (6405) stands and records what it is
sent, and his wizard's row names it. Manager 77 holds the league's interaction role; the league's
driver role is 820.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

import discord
import pytest

from tests.support.change_queue import (
    MANAGER_ROLE_ID,
    acknowledgement,
    change_rows,
    discard_job,
    http_error,
    member_interaction,
    refuse_the_job,
    retry_job,
    run_queue,
    step_rows,
    stopped_job,
    typed_message,
    updated_reply,
)
from tests.support.review_league import channel
from tests.support.season_league import (
    DRIVER_ROLE,
    SEASON_ID,
    SIGNING_UP,
    SIGNUP_CHANNEL_BASE,
    SeasonLeague,
    setup_league,
    signing_up,
)

#: The changes the review and correction cycle asks, on the league lane and back on the lane.
APPROVE_KIND = "signup.approve"
CLOSING_KIND = "signup.closing"
REQUEST_KIND = "signup.request_changes"
CHOICES_KIND = "signup.choices"
CHOOSE_KIND = "signup.correction.choose"
RECOLLECT_KIND = "signup.recollect"
LAPSE_KIND = "signup.correction.lapse"
#: The timers: the correction's five minutes, the held channel's deletion, the wizard's 24 hours.
CORRECTION_TIMER = "correction_lapse"
DELETE_TIMER = "signup_channel_delete"
INACTIVITY_TIMER = "signup_inactivity"
KIMI_CHANNEL = SIGNUP_CHANNEL_BASE + SIGNING_UP
KIMI_LANE = f"channel:{KIMI_CHANNEL}"
#: A second driver awaiting a correction choice, with a channel of his own.
OTHER = 115
#: The manager reviewing Kimi's signup, and the reason they type after Request Changes.
REVIEWER = 77
REASON = "Lap times look mistyped"
#: Kimi's two lap times, and their sum in milliseconds.
LAP_TIMES = {"1": "1:30.000", "2": "1:31.500"}
LAP_SUM_MS = 181_500


async def _league(tmp_path: Any, *, state: str = "PENDING_ADMIN_APPROVAL",
                  offered_ago: timedelta | None = None) -> SeasonLeague:
    """`setup_league` at Signups, the window open, the driver role set; Kimi at *state*, his
    signup record saved, his channel standing; manager 77 holding the interaction role. Where
    *offered_ago* is given the correction choices were posted that long ago, asked for by 77.
    Today's handlers off the queue are stand-ins that do nothing, so that only what the presses
    ask of the queue shows."""
    os.makedirs(str(tmp_path), exist_ok=True)
    league = await setup_league(tmp_path, stage="SIGNUPS", signups_open=True)
    await league.write("UPDATE server_configs SET driver_role_id = ?", DRIVER_ROLE)
    league.roles_held[REVIEWER] = {MANAGER_ROLE_ID}
    await _signed_up(league, SIGNING_UP, state=state, offered_ago=offered_ago)
    wizards = league.bot.wizard_service
    wizards.approve_signup = AsyncMock(return_value=None)
    wizards.request_changes = AsyncMock(return_value=None)
    wizards.select_correction_parameter = AsyncMock(return_value=None)
    return league


async def _signed_up(league: SeasonLeague, user_id: int, *, state: str,
                     offered_ago: timedelta | None = None) -> None:
    """Driver *user_id* at *state*, their wizard unengaged in their signup channel, their signup
    record saved with Kimi's two lap times; the correction choices posted *offered_ago*, asked
    for by 77, where given."""
    await signing_up(league, user_id, state=state)
    cid = SIGNUP_CHANNEL_BASE + user_id
    draft = {"_correction_requested_by": str(REVIEWER)} if offered_ago is not None else {}
    await league.write(
        "UPDATE signup_wizard_records SET wizard_state = 'UNENGAGED', draft_answers_json = ? "
        "WHERE discord_user_id = ?",
        json.dumps(draft), str(user_id),
    )
    await league.write(
        "INSERT INTO signup_records (season_id, discord_user_id, server_display_name, "
        "nationality, platform, platform_id, driver_type, lap_times_json, signup_channel_id) "
        "VALUES (?, ?, ?, 'British', 'Steam', 'kimi7', 'Full-Time Driver', ?, ?)",
        SEASON_ID, str(user_id), str(user_id), json.dumps(LAP_TIMES), cid,
    )
    if offered_ago is not None:
        await league.write(
            "UPDATE signup_wizard_records SET correction_offered_at = ? WHERE discord_user_id = ?",
            (league.clock.now - offered_ago).isoformat(), str(user_id),
        )
    made = league._recording(channel(cid, league.events))
    made.guild = league.guild
    made.posted = []
    made.set_permissions = AsyncMock()
    league.channels[cid] = made


def _manager(league: SeasonLeague) -> Any:
    manager = league._member(REVIEWER)
    manager.bot = False
    return manager


def _press(league: SeasonLeague, *, at: datetime | None = None) -> Any:
    """Manager 77's press in Kimi's signup channel, made at *at* (now unless given)."""
    press = member_interaction(league.bot, user=_manager(league),
                               created_at=at or league.clock.now)
    press.guild = league.guild
    press.channel_id = KIMI_CHANNEL
    press.channel = league.channel(KIMI_CHANNEL)
    return press


async def _press_approve(league: SeasonLeague) -> Any:
    """Manager 77 presses Approve on Kimi's review panel. The panel is handed the change queue
    when it is built (A23), never reaching it through the bot."""
    from leaguebot.signup.services.signup_views import AdminReviewView

    press = _press(league)
    view = AdminReviewView(str(SIGNING_UP), change_queue=league.bot.change_queue)
    await view.approve_button.callback(press)
    return press


async def _press_choice(league: SeasonLeague, parameter: str = "nationality") -> Any:
    """Manager 77 presses *parameter* among the correction choices in Kimi's channel, now. The
    choices are handed the change queue when they are built (A23)."""
    from leaguebot.signup.services.signup_views import CorrectionParameterView

    press = _press(league)
    view = CorrectionParameterView(str(SIGNING_UP), change_queue=league.bot.change_queue)
    [button] = [item for item in view.children
                if getattr(item, "custom_id", None) == f"correct_{parameter}"]
    await button.callback(press)
    return press


async def _type_reason(league: SeasonLeague) -> tuple[Any, Any]:
    """Manager 77 has pressed Request Changes on Kimi's review panel (the press deferred and
    parked as the button parks it) and types the reason in Kimi's channel, which the review cog
    hears. Gives the stored press and the reason's message."""
    from leaguebot.signup.cogs.admin_review_cog import _PENDING_REASONS, AdminReviewCog

    manager = _manager(league)
    press = _press(league)
    await press.response.defer(ephemeral=True)
    _PENDING_REASONS[(KIMI_CHANNEL, REVIEWER)] = {
        "action": "request_changes",
        "discord_user_id": str(SIGNING_UP),
        "actor": manager,
        "guild": league.guild,
        "interaction": press,
        "followup": press.followup,
    }
    reason = typed_message(league.channel(KIMI_CHANNEL), manager, REASON,
                           created_at=league.clock.now)
    reason.guild = league.guild
    try:
        await AdminReviewCog(league.bot).on_message(reason)
    finally:
        _PENDING_REASONS.clear()
    return press, reason


def _timer_callback(league: SeasonLeague, timer: str) -> Any:
    """The callback the builder registered for *timer*."""
    [callback] = [call.args[1]
                  for call in league.bot.scheduler_service.register_timer.call_args_list
                  if call.args and call.args[0] == timer]
    return callback


def _armed(league: SeasonLeague, timer: str) -> list[tuple[Any, ...]]:
    return [call.args for call in league.bot.scheduler_service.arm_once.call_args_list
            if call.args and call.args[0] == timer]


async def _changes_of(league: SeasonLeague, kind: str) -> list[dict[str, Any]]:
    return [row for row in await change_rows(league.db_path) if row["kind"] == kind]


async def _job_names(league: SeasonLeague, kind: str) -> list[str]:
    [change] = await _changes_of(league, kind)
    return [job["name"] for job in await step_rows(league.db_path, change["id"])]


def _lane(change: dict[str, Any]) -> str:
    return change.get("lane") or "league"


def _in_order(seen: list[str], wanted: list[str]) -> bool:
    """Whether *wanted* appear in *seen* in that order, others between them allowed."""
    rest = iter(seen)
    return all(name in rest for name in wanted)


async def _driver_state(league: SeasonLeague, user_id: int = SIGNING_UP) -> str | None:
    rows = await league.rows(
        "SELECT current_state FROM driver_profiles WHERE discord_user_id = ?", str(user_id)
    )
    return str(rows[0]["current_state"]) if rows else None


async def _wizard(league: SeasonLeague) -> dict[str, Any]:
    [row] = await league.rows(
        "SELECT * FROM signup_wizard_records WHERE discord_user_id = ?", str(SIGNING_UP)
    )
    return row


async def _record(league: SeasonLeague) -> dict[str, Any]:
    [row] = await league.rows(
        "SELECT * FROM signup_records WHERE discord_user_id = ?", str(SIGNING_UP)
    )
    return row


def _texts(league: SeasonLeague, user_id: int = SIGNING_UP) -> list[str]:
    return list(league.channel(SIGNUP_CHANNEL_BASE + user_id).texts)


def _sent_with(league: SeasonLeague, view: str) -> list[Any]:
    """The messages posted in Kimi's channel with a view of the class named *view*."""
    return [call for call in league.channel(KIMI_CHANNEL).send.call_args_list
            if type(call.kwargs.get("view")).__name__ == view]


def _locked(league: SeasonLeague) -> bool:
    return any(call.kwargs.get("send_messages") is False
               for call in league.channel(KIMI_CHANNEL).set_permissions.call_args_list)


def _answered(press: Any) -> str:
    """All the manager was told: the acknowledgement, then the reply as updated."""
    return "\n".join(part for part in (acknowledgement(press), updated_reply(press)) if part)


# ---------------------------------------------------------------------------
# Approve (C84)
# ---------------------------------------------------------------------------


async def test_approve_saves_grants_the_role_and_tells_the_driver_as_jobs(tmp_path):
    """Manager 77 presses Approve on Kimi's review panel. At once they are answered privately
    that the approval is under way; Kimi is still awaiting approval and holds no role. When the
    queue runs, `signup.approve` runs on the league lane, `approve` before `grant_role`: Kimi is
    Unassigned, not placed (approval says he may race; the placement commands decide where),
    his signup record is marked approved (an approved signup outranks a later one of the
    driver's that was not, #243), and he is given the driver role (820), once. It asks back
    `signup.closing` on Kimi's lane, whose `signup_notice` tells his channel he was approved,
    once; his typing there is revoked and the channel's deletion armed a day on, the channel
    itself still standing, since deleting it at once would take the message with it. The
    manager's reply is updated to say the signup was approved."""
    league = await _league(tmp_path)

    press = await _press_approve(league)

    assert acknowledgement(press).startswith("⏳")
    assert await _driver_state(league) == "PENDING_ADMIN_APPROVAL"
    assert league.granted.get(SIGNING_UP) is None

    await run_queue(league.bot)

    [approve] = await _changes_of(league, APPROVE_KIND)
    assert _lane(approve) == "league"
    assert _in_order(await _job_names(league, APPROVE_KIND), ["approve", "grant_role"])
    assert await _driver_state(league) == "UNASSIGNED"
    assert (await _record(league))["approved"] == 1
    assert league.granted.get(SIGNING_UP) == [DRIVER_ROLE]
    [closing] = await _changes_of(league, CLOSING_KIND)
    assert _lane(closing) == KIMI_LANE
    assert "signup_notice" in await _job_names(league, CLOSING_KIND)
    assert len([text for text in _texts(league) if "approved" in text.lower()]) == 1
    assert _locked(league)
    assert _armed(league, DELETE_TIMER) == [
        (DELETE_TIMER, str(SIGNING_UP), league.clock.now + timedelta(hours=24))
    ]
    assert KIMI_CHANNEL in league.channels
    league.channel(KIMI_CHANNEL).delete.assert_not_awaited()
    assert "approved" in updated_reply(press).lower()


async def test_a_role_that_cannot_be_granted_is_named_and_does_not_stop_the_queue(tmp_path):
    """Discord refuses to give Kimi the driver role. Manager 77 presses Approve and the queue
    runs: the league lane is not stopped, Kimi is Unassigned all the same, and his channel is
    told he was approved. The manager's updated reply says both that the signup was approved
    and that the driver role could not be granted, and the log channel says the role could not
    be granted."""
    league = await _league(tmp_path)
    league.grant_fails[SIGNING_UP] = http_error(discord.Forbidden, status=403)

    press = await _press_approve(league)
    await run_queue(league.bot)

    assert await stopped_job(league.db_path, "league") is None
    assert await _driver_state(league) == "UNASSIGNED"
    assert any("approved" in text.lower() for text in _texts(league))
    assert "approved" in updated_reply(press).lower()
    assert "driver role could not be granted" in updated_reply(press).lower()
    assert "driver role could not be granted" in league.log().lower()


async def test_approve_saves_the_lap_sum_on_the_signup_record_itself(tmp_path):
    """Kimi's signup record holds lap times of 1:30.000 and 1:31.500. Manager 77 presses Approve
    and the queue runs: the record's total lap time is 181,500 ms, written by the approval
    itself."""
    league = await _league(tmp_path)

    await _press_approve(league)
    await run_queue(league.bot)

    assert (await _record(league))["total_lap_ms"] == LAP_SUM_MS


# ---------------------------------------------------------------------------
# Request Changes (C85)
# ---------------------------------------------------------------------------


async def test_request_changes_deletes_the_reason_and_posts_the_choices_as_jobs(tmp_path):
    """Manager 77 pressed Request Changes on Kimi's review panel and types "Lap times look
    mistyped" in his channel. The reason is not deleted at once, and a `signup.request_changes`
    change is asked on the league lane through the stored press. When the queue runs Kimi awaits
    a correction parameter, and `signup.choices` runs on his lane, `delete_reason` before
    `post_choices`: the reason is deleted and the correction choices posted in his channel."""
    league = await _league(tmp_path)

    _press_made, reason = await _type_reason(league)

    assert not reason.gone
    [request] = await _changes_of(league, REQUEST_KIND)
    assert _lane(request) == "league"

    await run_queue(league.bot)

    assert await _driver_state(league) == "AWAITING_CORRECTION_PARAMETER"
    [choices] = await _changes_of(league, CHOICES_KIND)
    assert _lane(choices) == KIMI_LANE
    assert _in_order(await _job_names(league, CHOICES_KIND), ["delete_reason", "post_choices"])
    assert reason.gone
    assert len(_sent_with(league, "CorrectionParameterView")) == 1


async def test_the_five_minutes_start_when_the_choices_appear(tmp_path):
    """Manager 77 types the reason after Request Changes; the queue does not reach it until
    three minutes later. When it runs, the choices' time saved on Kimi's wizard is the time they
    were posted, and the correction's lapse is armed five minutes after that — eight minutes
    after the reason was typed, not five."""
    league = await _league(tmp_path)

    await _type_reason(league)
    league.clock.advance(minutes=3)
    await run_queue(league.bot)

    offered = datetime.fromisoformat((await _wizard(league))["correction_offered_at"])
    assert offered == league.clock.now
    assert _armed(league, CORRECTION_TIMER) == [
        (CORRECTION_TIMER, str(SIGNING_UP), league.clock.now + timedelta(minutes=5))
    ]


async def test_the_correction_timer_is_armed_by_a_job_of_its_own_after_the_choices(tmp_path):
    """Discord refuses the correction choices in Kimi's channel. Manager 77's reason is typed
    and the queue runs: Kimi's lane stops at `post_choices` and no correction lapse is armed.
    Once Discord takes the post, Retry posts the choices, and a job of its own, `arm_lapse`, is
    done after them, arming the lapse five minutes from the post."""
    from tests.support.change_queue import retry_job

    league = await _league(tmp_path)
    kimi_channel = league.channel(KIMI_CHANNEL)
    posting = kimi_channel.send.side_effect
    kimi_channel.send.side_effect = http_error(discord.Forbidden, status=403)

    await _type_reason(league)
    await run_queue(league.bot)

    job = await stopped_job(league.db_path, KIMI_LANE)
    assert job is not None and job["name"] == "post_choices"
    assert _armed(league, CORRECTION_TIMER) == []

    kimi_channel.send.side_effect = posting
    await retry_job(league.bot, lane=KIMI_LANE)

    arming = [job for job in await step_rows(league.db_path) if job["name"] == "arm_lapse"]
    assert len(arming) == 1 and arming[0]["done_at"]
    assert _armed(league, CORRECTION_TIMER) == [
        (CORRECTION_TIMER, str(SIGNING_UP), league.clock.now + timedelta(minutes=5))
    ]


def _refusing_to_arm(timer: str) -> Any:
    """A one-off timer that the scheduler refuses to arm for *timer*, and arms for any other."""
    def arm(name: str, *_args: Any, **_kwargs: Any) -> None:
        if name == timer:
            raise RuntimeError("the job store is unavailable")
    return arm


async def test_arming_the_five_minutes_cannot_be_discarded(tmp_path):
    """Manager 77 types the reason after Request Changes and the correction choices are posted
    in Kimi's channel, but the scheduler refuses to arm the five minutes, so Kimi's lane stops at
    `arm_lapse`. A league admin's Discard on it is refused, saying nothing would end Kimi's
    correction request once its five minutes ran out and the season's placements could not be
    confirmed until the bot restarts. The lane stays stopped there. Once the scheduler takes it,
    a Retry arms the lapse five minutes from the post."""
    league = await _league(tmp_path)
    scheduler = league.bot.scheduler_service
    scheduler.arm_once.side_effect = _refusing_to_arm(CORRECTION_TIMER)

    await _type_reason(league)
    await run_queue(league.bot)
    stopped = await stopped_job(league.db_path, KIMI_LANE)
    assert stopped is not None and stopped["name"] == "arm_lapse"

    refused = await discard_job(league.bot, lane=KIMI_LANE)

    assert acknowledgement(refused) == (
        f"⛔ This job can't be discarded: without it nothing would end <@{SIGNING_UP}>'s "
        "correction request once its five minutes ran out, and the season's placements could "
        "not be confirmed until the bot restarts. Fix what stopped it and press **Retry**."
    )
    assert (await stopped_job(league.db_path, KIMI_LANE))["id"] == stopped["id"]

    scheduler.arm_once.side_effect = None
    await retry_job(league.bot, lane=KIMI_LANE)

    assert await stopped_job(league.db_path) is None
    assert _armed(league, CORRECTION_TIMER)[-1] == (
        CORRECTION_TIMER, str(SIGNING_UP), league.clock.now + timedelta(minutes=5)
    )


@pytest.mark.parametrize("job", ["choose", "arm_inactivity"])
async def test_the_choice_and_its_24_hours_cannot_be_discarded(tmp_path, job):
    """Kimi awaits a correction parameter, the choices posted a minute ago, and manager 77
    presses Nationality. Either the save sending Kimi back to his nationality fails (`choose`), or
    the scheduler refuses to arm his 24 hours of inactivity (`arm_inactivity`), so the league
    lane stops at that job. A league admin's Discard on it is refused, saying, for `choose`, that
    Kimi would be left awaiting a correction nobody can choose, or, for `arm_inactivity`, that
    his signup would never time out if he stopped answering, and in both that the season's
    placements could not be confirmed until the bot restarts. The queue stays stopped there.
    Once the fault is lifted, a Retry leaves Kimi Pending Driver Correction with his 24 hours
    armed."""
    league = await _league(tmp_path, state="AWAITING_CORRECTION_PARAMETER",
                           offered_ago=timedelta(minutes=1))
    scheduler = league.bot.scheduler_service
    if job == "choose":
        await refuse_the_job(league.db_path, CHOOSE_KIND, "choose", True)
    else:
        scheduler.arm_once.side_effect = _refusing_to_arm(INACTIVITY_TIMER)

    await _press_choice(league)
    await run_queue(league.bot)
    stopped = await stopped_job(league.db_path, "league")
    assert stopped is not None and stopped["name"] == job

    refused = await discard_job(league.bot, lane="league")

    follows = (
        f"<@{SIGNING_UP}> would be left awaiting a correction nobody can choose"
        if job == "choose" else
        f"<@{SIGNING_UP}>'s signup would never time out if they stopped answering, and while "
        "they correct it"
    )
    assert acknowledgement(refused) == (
        f"⛔ This job can't be discarded: without it {follows}"
        + (", and" if job == "choose" else "")
        + " the season's placements could not be confirmed until the bot restarts. Fix what "
        "stopped it and press **Retry**."
    )
    assert (await stopped_job(league.db_path, "league"))["id"] == stopped["id"]
    assert await _driver_state(league) == (
        "AWAITING_CORRECTION_PARAMETER" if job == "choose" else "PENDING_DRIVER_CORRECTION"
    )

    if job == "choose":
        await refuse_the_job(league.db_path, CHOOSE_KIND, "choose", False)
    else:
        scheduler.arm_once.side_effect = None
    await retry_job(league.bot, lane="league")

    assert await stopped_job(league.db_path, "league") is None
    assert await _driver_state(league) == "PENDING_DRIVER_CORRECTION"
    assert _armed(league, INACTIVITY_TIMER)[-1] == (
        INACTIVITY_TIMER, str(SIGNING_UP), league.clock.now + timedelta(hours=24)
    )


async def test_request_changes_changes_the_status_on_the_league_and_the_choices_come_back_on_the_lane(
    tmp_path,
):
    """Discord refuses the correction choices in Kimi's channel. Manager 77's reason is typed
    and the queue runs: `signup.request_changes` is done on the league lane, Kimi awaiting a
    correction parameter, and the league lane is not stopped; the choices, asked back on Kimi's
    lane, stop that lane alone."""
    league = await _league(tmp_path)
    league.channel(KIMI_CHANNEL).send.side_effect = http_error(discord.Forbidden, status=403)

    await _type_reason(league)
    await run_queue(league.bot)

    [request] = await _changes_of(league, REQUEST_KIND)
    assert (_lane(request), request["state"]) == ("league", "DONE")
    assert await _driver_state(league) == "AWAITING_CORRECTION_PARAMETER"
    assert await stopped_job(league.db_path, "league") is None
    job = await stopped_job(league.db_path, KIMI_LANE)
    assert job is not None and job["name"] == "post_choices"


# ---------------------------------------------------------------------------
# The choice of what to correct (C86)
# ---------------------------------------------------------------------------


async def test_a_choice_pressed_in_time_counts_though_it_runs_after(tmp_path):
    """Kimi awaits a correction parameter, the choices posted four minutes and 59 seconds ago.
    Manager 77 presses Nationality now; the queue reaches it two minutes later, past the five
    minutes. The choice counts: Kimi is Pending Driver Correction, his wizard asks his
    nationality again, `signup.recollect` on his lane posts the prompt to re-submit it, and his
    24 hours of inactivity are armed from when it ran. The manager is answered privately and
    the reply updated."""
    league = await _league(tmp_path, state="AWAITING_CORRECTION_PARAMETER",
                           offered_ago=timedelta(minutes=4, seconds=59))

    press = await _press_choice(league)

    assert acknowledgement(press).startswith("⏳")

    league.clock.advance(minutes=2)
    await run_queue(league.bot)

    assert await _driver_state(league) == "PENDING_DRIVER_CORRECTION"
    assert (await _wizard(league))["wizard_state"] == "COLLECTING_NATIONALITY"
    [recollect] = await _changes_of(league, RECOLLECT_KIND)
    assert _lane(recollect) == KIMI_LANE
    assert any("re-submit" in text.lower() for text in _texts(league))
    assert _armed(league, INACTIVITY_TIMER) == [
        (INACTIVITY_TIMER, str(SIGNING_UP), league.clock.now + timedelta(hours=24))
    ]
    assert updated_reply(press)


async def test_a_choice_pressed_after_the_five_minutes_is_refused(tmp_path):
    """Kimi awaits a correction parameter, the choices posted five minutes and one second ago,
    their lapse not yet run. Manager 77 presses Nationality and the queue runs: the press is
    refused (⛔), Kimi still awaits a correction parameter and no prompt to re-submit is
    posted."""
    league = await _league(tmp_path, state="AWAITING_CORRECTION_PARAMETER",
                           offered_ago=timedelta(minutes=5, seconds=1))

    press = await _press_choice(league)
    await run_queue(league.bot)

    assert "⛔" in _answered(press)
    assert await _driver_state(league) == "AWAITING_CORRECTION_PARAMETER"
    assert not any("re-submit" in text.lower() for text in _texts(league))


# ---------------------------------------------------------------------------
# The correction's lapse (C87)
# ---------------------------------------------------------------------------


async def test_a_lapse_asked_by_its_timer_returns_the_driver_and_posts_the_notice_and_panel(
    tmp_path,
):
    """Kimi awaits a correction parameter, the choices posted five minutes ago at manager 77's
    request. The correction's timer fires: it asks `signup.correction.lapse` as the bot, on the
    league lane, and nothing is posted yet. When the queue runs Kimi is back awaiting approval;
    his channel is told, mentioning manager 77, that he is back in the approval queue, a fresh
    review panel is posted there, and the lapse is recorded in the log channel."""
    league = await _league(tmp_path, state="AWAITING_CORRECTION_PARAMETER",
                           offered_ago=timedelta(minutes=5))

    await _timer_callback(league, CORRECTION_TIMER)(str(SIGNING_UP))

    [lapse] = await _changes_of(league, LAPSE_KIND)
    assert (_lane(lapse), lapse["origin"]) == ("league", "BOT")
    assert _texts(league) == []

    await run_queue(league.bot)

    assert await _driver_state(league) == "PENDING_ADMIN_APPROVAL"
    told = [text for text in _texts(league) if "approval queue" in text.lower()]
    assert len(told) == 1 and f"<@{REVIEWER}>" in told[0]
    assert len(_sent_with(league, "AdminReviewView")) == 1
    assert "lapsed" in league.log().lower()


async def test_a_restart_asks_the_lapse_of_each_driver_awaiting_a_choice(tmp_path):
    """The bot starts again. Kimi's correction choices were posted ten minutes ago, a second
    driver's (115) one minute ago, both awaiting a correction parameter. Recovering the
    correction requests (the wizard service built with the change queue) asks the queue for a
    `signup.correction.lapse` of each, as the bot on the league lane, before the queue starts,
    posting nothing; a restart releases every driver awaiting a choice. When the queue runs both
    are back awaiting approval."""
    from leaguebot.signup.services.wizard_service import WizardService

    league = await _league(tmp_path, state="AWAITING_CORRECTION_PARAMETER",
                           offered_ago=timedelta(minutes=10))
    await _signed_up(league, OTHER, state="AWAITING_CORRECTION_PARAMETER",
                     offered_ago=timedelta(minutes=1))
    wizards = WizardService(
        league.db_path, league.bot.scheduler_service, league.bot.output_router,
        change_queue=league.bot.change_queue,
    )
    wizards.set_bot(league.bot)

    await wizards.recover_correction_timeouts()

    lapses = await _changes_of(league, LAPSE_KIND)
    assert sorted((_lane(change), change["origin"]) for change in lapses) == [
        ("league", "BOT"), ("league", "BOT")
    ]
    asked = " ".join(str(change["payload"]) for change in lapses)
    assert str(SIGNING_UP) in asked and str(OTHER) in asked
    assert _texts(league) == [] and _texts(league, OTHER) == []

    await run_queue(league.bot)

    assert await _driver_state(league) == "PENDING_ADMIN_APPROVAL"
    assert await _driver_state(league, OTHER) == "PENDING_ADMIN_APPROVAL"


async def test_a_lapse_is_not_due_where_a_choice_is_queued(tmp_path):
    """Kimi's correction choices were posted four minutes and 59 seconds ago. Manager 77 presses
    Nationality, which waits its turn; two minutes later the correction's timer fires and asks
    the lapse. When the queue runs the choice counts and the lapse does nothing: Kimi is Pending
    Driver Correction, nothing says he is back in the approval queue, and no fresh review panel
    is posted."""
    league = await _league(tmp_path, state="AWAITING_CORRECTION_PARAMETER",
                           offered_ago=timedelta(minutes=4, seconds=59))

    await _press_choice(league)
    league.clock.advance(minutes=2)
    await _timer_callback(league, CORRECTION_TIMER)(str(SIGNING_UP))
    await run_queue(league.bot)

    assert await _driver_state(league) == "PENDING_DRIVER_CORRECTION"
    assert not any("approval queue" in text.lower() for text in _texts(league))
    assert _sent_with(league, "AdminReviewView") == []
