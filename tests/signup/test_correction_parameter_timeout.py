"""A driver awaiting a correction parameter is never left there (issue #129), on the queue.

Pressing **Request Changes** parks the driver in ``AWAITING_CORRECTION_PARAMETER`` while a
manager picks which of the nine answers to re-collect. Five minutes after the choices appear the
driver goes back to the approval queue if nobody chose (Q23). Before #439 slice 6b the five
minutes were an in-memory ``asyncio`` task, and a restart released the driver itself, off the
queue. On the queue (C85, C87) the five minutes are a one-off timer whose callback, registered by
the builder, asks ``signup.correction.lapse`` as the bot on the league lane, and the restart asks
the same change of each driver still awaiting a choice before the queue starts.

Three decisions taken while fixing #129, each still pinned by a test below:

- a restart returns *every* such driver to Pending Admin Approval **unconditionally**, rather
  than re-arming the remainder of their five minutes;
- the manager who asked for the correction is mentioned when the window lapses, on a restart
  *and* on the ordinary five-minute lapse;
- the mention goes in the driver's signup channel, which is the only place a ping works —
  ``post_log`` renders mentions as plain text by design.

The league: ``setup_league`` at Signups, the signup module on and its window open. Kimi (105) has
signed up: his signup record is saved under the server name "Kimi", his signup channel (6405)
stands and records what it is sent, and his wizard's row names it. Unless a test says otherwise
he awaits a correction parameter, the choices posted at manager 77's ("Admin") request. Manager 77
holds the league's interaction role.

Every test here is ``async def`` because the lapse posts ``AdminReviewView``, and discord.py
calls ``get_running_loop()`` in ``View.__init__``.
"""
from __future__ import annotations

import json
import os
from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.support.change_queue import (
    MANAGER_ROLE_ID,
    acknowledgement,
    change_rows,
    discard_job,
    member_interaction,
    refuse_the_job,
    retry_job,
    run_queue,
    stopped_job,
    tier_member,
    typed_message,
)
from tests.support.review_league import channel
from tests.support.season_league import (
    SEASON_ID,
    SIGNING_UP,
    SIGNUP_CHANNEL_BASE,
    SeasonLeague,
    setup_league,
    signing_up,
)

LAPSE_KIND = "signup.correction.lapse"
CORRECTION_TIMER = "correction_lapse"
KIMI_CHANNEL = SIGNUP_CHANNEL_BASE + SIGNING_UP
REVIEWER = 77
AWAITING = "AWAITING_CORRECTION_PARAMETER"

#: The lapse the correction window records, naming the manager who asked (owner, 2026-09-30,
#: "Log both as lapses").
CORRECTION_LAPSE = (
    "⌛ the correction request for Kimi's signup lapsed unconfirmed "
    f"(started by Admin (`<@{REVIEWER}>`))\n"
    "  Kimi is back in the approval queue; a fresh review panel is in their channel."
)


# ── Harness ───────────────────────────────────────────────────────────────


async def _league(tmp_path: Any, *, state: str = AWAITING,
                  offered_ago: timedelta | None = timedelta(minutes=5),
                  requested_by: int | None = REVIEWER) -> SeasonLeague:
    """``setup_league`` at Signups, the window open; Kimi at *state*, his wizard unengaged in
    his channel and his signup record saved; the correction choices posted *offered_ago*, his
    wizard naming *requested_by* as the manager who asked. Today's Request Changes handler off
    the queue is a stand-in that does nothing, so that only what the reason asks shows."""
    os.makedirs(str(tmp_path), exist_ok=True)
    league = await setup_league(tmp_path, stage="SIGNUPS", signups_open=True)
    league.roles_held[REVIEWER] = {MANAGER_ROLE_ID}
    await signing_up(league, SIGNING_UP, state=state)
    draft = {} if requested_by is None else {"_correction_requested_by": str(requested_by)}
    await league.write(
        "UPDATE signup_wizard_records SET wizard_state = 'UNENGAGED', draft_answers_json = ? "
        "WHERE discord_user_id = ?",
        json.dumps(draft), str(SIGNING_UP),
    )
    await league.write(
        "INSERT INTO signup_records (season_id, discord_user_id, server_display_name, "
        "nationality, platform, platform_id, driver_type, signup_channel_id) "
        "VALUES (?, ?, 'Kimi', 'Finnish', 'Steam', 'kimi7', 'Full-Time Driver', ?)",
        SEASON_ID, str(SIGNING_UP), KIMI_CHANNEL,
    )
    if offered_ago is not None:
        await league.write(
            "UPDATE signup_wizard_records SET correction_offered_at = ? "
            "WHERE discord_user_id = ?",
            (league.clock.now - offered_ago).isoformat(), str(SIGNING_UP),
        )
    made = league._recording(channel(KIMI_CHANNEL, league.events))
    made.guild = league.guild
    made.posted = []
    made.set_permissions = AsyncMock()
    league.channels[KIMI_CHANNEL] = made
    league.bot.wizard_service.request_changes = AsyncMock(return_value=None)
    return league


async def _fire_the_timer(league: SeasonLeague) -> None:
    """The correction's five minutes run out: the callback the builder registered for the
    ``correction_lapse`` timer is handed Kimi's account."""
    [callback] = [call.args[1]
                  for call in league.bot.scheduler_service.register_timer.call_args_list
                  if call.args and call.args[0] == CORRECTION_TIMER]
    await callback(str(SIGNING_UP))


async def _restart(league: SeasonLeague) -> None:
    """The bot starts again: the wizard service, built with the change queue, recovers the
    correction requests before the queue starts."""
    from leaguebot.signup.services.wizard_service import WizardService

    wizards = WizardService(
        league.db_path, league.bot.scheduler_service, league.bot.output_router,
        change_queue=league.bot.change_queue,
    )
    wizards.set_bot(league.bot)
    await wizards.recover_correction_timeouts()


async def _lapses(league: SeasonLeague) -> list[dict[str, Any]]:
    return [row for row in await change_rows(league.db_path) if row["kind"] == LAPSE_KIND]


async def _state(league: SeasonLeague) -> str:
    [row] = await league.rows(
        "SELECT current_state FROM driver_profiles WHERE discord_user_id = ?", str(SIGNING_UP)
    )
    return str(row["current_state"])


async def _draft(league: SeasonLeague) -> dict[str, Any]:
    [row] = await league.rows(
        "SELECT draft_answers_json FROM signup_wizard_records WHERE discord_user_id = ?",
        str(SIGNING_UP),
    )
    return dict(json.loads(row["draft_answers_json"] or "{}"))


def _posted(league: SeasonLeague) -> str:
    """Everything Kimi's channel was sent, joined."""
    return "\n".join(league.channel(KIMI_CHANNEL).texts)


def _panels(league: SeasonLeague) -> list[Any]:
    return [call for call in league.channel(KIMI_CHANNEL).send.call_args_list
            if type(call.kwargs.get("view")).__name__ == "AdminReviewView"]


# ── Remembering who asked ─────────────────────────────────────────────────


async def test_request_changes_records_the_requesting_admin(tmp_path):
    """Kimi awaits approval. Manager 77 presses Request Changes and types the reason in Kimi's
    channel. When the queue reaches the request, Kimi's wizard names 77 as the manager who
    asked, so the lapse can mention him later."""
    from leaguebot.signup.cogs.admin_review_cog import _PENDING_REASONS, AdminReviewCog

    league = await _league(tmp_path, state="PENDING_ADMIN_APPROVAL", offered_ago=None,
                           requested_by=None)
    manager = league._member(REVIEWER)
    manager.bot = False
    press = member_interaction(league.bot, user=manager, created_at=league.clock.now)
    press.guild = league.guild
    press.channel_id = KIMI_CHANNEL
    await press.response.defer(ephemeral=True)
    _PENDING_REASONS[(KIMI_CHANNEL, REVIEWER)] = {
        "action": "request_changes", "discord_user_id": str(SIGNING_UP), "actor": manager,
        "guild": league.guild, "interaction": press, "followup": press.followup,
    }
    reason = typed_message(league.channel(KIMI_CHANNEL), manager, "Lap time looks wrong",
                           created_at=league.clock.now)
    reason.guild = league.guild
    try:
        await AdminReviewCog(league.bot).on_message(reason)
    finally:
        _PENDING_REASONS.clear()
    await run_queue(league.bot)

    assert (await _draft(league))["_correction_requested_by"] == str(REVIEWER)


# ── The restart ───────────────────────────────────────────────────────────


async def test_a_restart_returns_the_driver_to_pending_admin_approval(tmp_path):
    """Kimi's choices were posted five minutes ago and the bot restarts. The restart asks the
    lapse as the bot on the league lane, posting nothing; when the queue runs Kimi is back
    awaiting approval."""
    league = await _league(tmp_path)

    await _restart(league)

    [lapse] = await _lapses(league)
    assert ((lapse.get("lane") or "league"), lapse["origin"]) == ("league", "BOT")
    assert _posted(league) == ""

    await run_queue(league.bot)

    assert await _state(league) == "PENDING_ADMIN_APPROVAL"


async def test_a_restart_reverts_even_within_the_five_minutes(tmp_path):
    """Kimi's choices were posted a minute ago and the bot restarts. The release is
    unconditional, not re-armed for what was left: when the queue runs Kimi is back awaiting
    approval."""
    league = await _league(tmp_path, offered_ago=timedelta(minutes=1))

    await _restart(league)
    await run_queue(league.bot)

    assert await _state(league) == "PENDING_ADMIN_APPROVAL"


async def test_a_restart_mentions_the_admin_who_requested_the_correction(tmp_path):
    """Manager 77 asked for a correction of Kimi's signup and the bot restarted before a
    parameter was chosen. When the queue runs the lapse, 77 is mentioned in Kimi's channel and
    the lapse naming 77 is recorded in the log channel."""
    league = await _league(tmp_path)

    await _restart(league)
    await run_queue(league.bot)

    assert f"<@{REVIEWER}>" in _posted(league)
    assert CORRECTION_LAPSE in league.log()


async def test_a_restart_says_that_the_bot_restarted(tmp_path):
    """The two lapse reasons read differently, so a manager knows which happened: the notice a
    restart's lapse posts in Kimi's channel says the bot restarted, and the log records it as
    the five minutes' lapse is recorded (owner, 2026-09-30)."""
    league = await _league(tmp_path)

    await _restart(league)
    await run_queue(league.bot)

    assert "restart" in _posted(league).lower()
    assert CORRECTION_LAPSE in league.log()


async def test_a_driver_stranded_before_the_fix_is_released_without_a_mention(tmp_path):
    """Kimi's wizard names no manager who asked (written before the key existed). The restart's
    lapse still releases him and posts in his channel, and mentions no one as `<@None>`."""
    league = await _league(tmp_path, requested_by=None)

    await _restart(league)
    await run_queue(league.bot)

    assert await _state(league) == "PENDING_ADMIN_APPROVAL"
    assert _posted(league) != ""
    assert "<@None>" not in _posted(league)


async def test_a_driver_not_awaiting_a_parameter_is_left_alone(tmp_path):
    """Kimi awaits approval, not a correction parameter, and the bot restarts. No lapse is
    asked and nothing is posted in his channel."""
    league = await _league(tmp_path, state="PENDING_ADMIN_APPROVAL", offered_ago=None)

    await _restart(league)
    await run_queue(league.bot)

    assert await _lapses(league) == []
    assert _posted(league) == ""


async def test_the_reposted_review_panel_notifies_no_group(tmp_path):
    """Without the panel the manager has the driver back but no buttons to act on: the
    restart's lapse posts one fresh review panel in Kimi's channel. The reposted panel quotes
    the same answers the first did (#362): it may notify neither everyone nor any role."""
    league = await _league(tmp_path)

    await _restart(league)
    await run_queue(league.bot)

    assert len(_panels(league)) == 1
    [panel] = _panels(league)
    assert panel.kwargs["allowed_mentions"].everyone is False
    assert panel.kwargs["allowed_mentions"].roles is False


# ── The ordinary five-minute lapse ────────────────────────────────────────


async def test_the_five_minute_timeout_mentions_the_admin(tmp_path):
    """Manager 77 asked for a correction of Kimi's signup and chose no parameter within five
    minutes of the choices appearing. When the timer's lapse runs Kimi is back awaiting review,
    77 is mentioned in Kimi's channel, and the lapse naming 77 is recorded in the log channel."""
    league = await _league(tmp_path)

    await _fire_the_timer(league)
    await run_queue(league.bot)

    assert f"<@{REVIEWER}>" in _posted(league)
    assert await _state(league) == "PENDING_ADMIN_APPROVAL"
    assert CORRECTION_LAPSE in league.log()


async def test_a_lapse_whose_driver_has_already_moved_on_records_nothing(tmp_path):
    """The five minutes run out and the timer asks the lapse, but before the queue reaches it
    Kimi's signup moves on (to Pending Driver Correction). When the queue runs the lapse is no
    longer due: Kimi stays where he is, nothing is posted in his channel and no lapse is
    recorded."""
    league = await _league(tmp_path)

    await _fire_the_timer(league)
    await league.write(
        "UPDATE driver_profiles SET current_state = 'PENDING_DRIVER_CORRECTION' "
        "WHERE discord_user_id = ?", str(SIGNING_UP),
    )
    await run_queue(league.bot)

    assert await _state(league) == "PENDING_DRIVER_CORRECTION"
    assert _posted(league) == ""
    assert "lapsed" not in league.log()


async def test_a_lapsed_window_clears_the_correction_state(tmp_path):
    """Kimi's wizard holds the correction's reason, the manager who asked and the correction
    flag. Once the timer's lapse runs none of them is left, so a stale reason cannot resurface
    on the manager's next correction."""
    league = await _league(tmp_path)
    await league.write(
        "UPDATE signup_wizard_records SET draft_answers_json = ? WHERE discord_user_id = ?",
        json.dumps({"_correction_requested_by": str(REVIEWER),
                    "_correction_reason": "Lap time looks wrong", "_is_correction": True}),
        str(SIGNING_UP),
    )

    await _fire_the_timer(league)
    await run_queue(league.bot)

    draft = await _draft(league)
    assert "_correction_reason" not in draft
    assert "_correction_requested_by" not in draft
    assert "_is_correction" not in draft


# ── The disabled module ───────────────────────────────────────────────────


async def test_the_timeout_does_nothing_while_the_signup_module_is_disabled(tmp_path):
    """The signup module is turned off and Kimi's five minutes run out. The lapse posts no
    review panel into a module the league turned off: Kimi stays awaiting a parameter, nothing
    is posted in his channel and no lapse line is written (core specification, Modules)."""
    league = await _league(tmp_path)
    await league.switch("signup", False)

    await _fire_the_timer(league)
    await run_queue(league.bot)

    assert await _state(league) == AWAITING
    assert _posted(league) == ""
    assert "lapsed" not in league.log()


# ── Closing the window ────────────────────────────────────────────────────


async def _signup_close(league: SeasonLeague) -> Any:
    """Manager 77 runs `/signup close`; gives his interaction."""
    from leaguebot.signup.cogs.signup_cog import SignupCog
    from tests.support.undecorate import undecorate

    cog = SignupCog.__new__(SignupCog)
    cog.bot = league.bot
    interaction = member_interaction(league.bot, user=tier_member("manager", member_id=REVIEWER))
    interaction.guild = league.guild
    interaction.command = MagicMock(qualified_name="signup close")
    await undecorate(SignupCog.signup_close)(cog, interaction)
    return interaction


async def _signup_close_press(league: SeasonLeague) -> Any:
    """Manager 77's press of Confirm Close."""
    press = member_interaction(league.bot, user=tier_member("manager", member_id=REVIEWER))
    press.guild = league.guild
    press.command = MagicMock(qualified_name="signup close")
    return press


async def test_the_close_confirmation_counts_a_driver_awaiting_a_correction_parameter(tmp_path):
    """Kimi awaits a correction parameter and manager 77 runs `/signup close`. Parked alone, he
    used to let the window shut with no confirmation at all: the confirmation counts him among
    the drivers who keep their place, naming him and his channel."""
    league = await _league(tmp_path, offered_ago=None)

    asked = await _signup_close(league)

    reply = asked.response.send_message.await_args.args[0]
    assert "1 driver(s) awaiting approval or a correction will keep their place" in reply
    assert f"Kimi — <#{KIMI_CHANNEL}>" in reply


async def test_a_close_leaves_a_driver_awaiting_a_correction_parameter_alone(tmp_path):
    """Kimi awaits a correction parameter; manager 77 runs `/signup close`, presses Confirm
    Close, and the queue runs the close. Kimi is listed but untouched, like the other two
    review-cycle states beside it: a manager may still press Request Changes after the window
    has shut, so a close-time release would rescue nobody the restart does not (decided
    2026-09-15)."""
    league = await _league(tmp_path, offered_ago=None)

    asked = await _signup_close(league)
    view = asked.response.send_message.await_args.kwargs["view"]
    await view.confirm.callback(await _signup_close_press(league))
    await run_queue(league.bot)

    assert await _state(league) == AWAITING


def _review_panels_standing(league: SeasonLeague) -> list[Any]:
    """The review panels standing in Kimi's channel, refused posts left out."""
    return [message for message in league.channel(KIMI_CHANNEL).messages.values()
            if type(message.view).__name__ == "AdminReviewView"]


async def test_a_lapse_s_panel_cannot_be_discarded_and_a_retry_records_the_lapse(tmp_path):
    """Kimi's correction lapses and Discord refuses the fresh review panel in his channel, so the
    queue stops at `post_panel`. A league admin's Discard on it is refused, saying Kimi would
    wait for approval with no review panel and the season's placements could not be confirmed.
    Kimi is back awaiting approval, the queue stays stopped there and no lapse is recorded yet.
    Once Discord takes posts again, a Retry posts one review panel, and the log channel records
    the lapse naming manager 77 and Kimi and saying a fresh review panel is in his channel."""
    league = await _league(tmp_path)
    league.channel(KIMI_CHANNEL).fail_when = lambda _content, kwargs: kwargs.get("view") is not None

    await _fire_the_timer(league)
    await run_queue(league.bot)
    stopped = await stopped_job(league.db_path)
    assert stopped is not None and stopped["name"] == "post_panel"

    refused = await discard_job(league.bot)

    assert acknowledgement(refused) == (
        f"⛔ This job can't be discarded: without it <@{SIGNING_UP}> would wait for approval with "
        "no review panel, and the season's placements could not be confirmed. Fix what stopped "
        "it and press **Retry**."
    )
    assert (await stopped_job(league.db_path))["id"] == stopped["id"]
    assert await _state(league) == "PENDING_ADMIN_APPROVAL"
    assert CORRECTION_LAPSE.split("\n")[0] not in league.log()

    league.channel(KIMI_CHANNEL).fail_when = None
    await retry_job(league.bot)

    assert await stopped_job(league.db_path) is None
    assert len(_review_panels_standing(league)) == 1
    assert CORRECTION_LAPSE in league.log(), league.log()


async def test_the_lapse_itself_cannot_be_discarded(tmp_path):
    """Kimi's correction lapses, but the save returning him to the approval queue fails, so the
    queue stops at `lapse`. A league admin's Discard on it is refused, saying Kimi would be left
    awaiting a correction nobody can choose and the season's placements could not be confirmed
    until the bot restarts. Kimi still awaits a correction parameter, nothing is posted in his
    channel and the queue stays stopped there. Once the fault is lifted, a Retry returns him to
    the approval queue, posts one review panel and records the lapse."""
    league = await _league(tmp_path)
    await refuse_the_job(league.db_path, LAPSE_KIND, "lapse", True)

    await _fire_the_timer(league)
    await run_queue(league.bot)
    stopped = await stopped_job(league.db_path)
    assert stopped is not None and stopped["name"] == "lapse"

    refused = await discard_job(league.bot)

    assert acknowledgement(refused) == (
        f"⛔ This job can't be discarded: without it <@{SIGNING_UP}> would be left awaiting a "
        "correction nobody can choose, and the season's placements could not be confirmed until "
        "the bot restarts. Fix what stopped it and press **Retry**."
    )
    assert (await stopped_job(league.db_path))["id"] == stopped["id"]
    assert await _state(league) == AWAITING
    assert _posted(league) == ""

    await refuse_the_job(league.db_path, LAPSE_KIND, "lapse", False)
    await retry_job(league.bot)

    assert await stopped_job(league.db_path) is None
    assert await _state(league) == "PENDING_ADMIN_APPROVAL"
    assert len(_review_panels_standing(league)) == 1
    assert CORRECTION_LAPSE in league.log(), league.log()


# ── A lapse whose signup channel is gone (#439, slice 8, F1) ───────────────

#: The line the ending writes in the log channel, naming Kimi and his gone channel.
CHANNEL_GONE_LINE = (
    f"Kimi (`<@{SIGNING_UP}>`) | Signup | Ended\n  channel: <#{KIMI_CHANNEL}>\n  Their signup "
    "channel was gone when their review panel was due, so their signup ended. They may press "
    "Sign Up again while signups are open."
)
#: What the lapse's own record says of a gone channel.
LAPSE_CHANNEL_GONE = (
    "Kimi's signup channel was gone, so no fresh review panel could be posted and their signup "
    "is ended."
)


async def test_a_lapse_whose_channel_is_gone_ends_the_signup(tmp_path):
    """Manager 77 asked for a correction of Kimi's signup, and Kimi's signup channel 6405 is then
    deleted. The five minutes run out and the queue runs: the lapse returns Kimi to the approval
    queue, finds his channel gone, posts nothing and asks the ending of his signup, so he is Not
    Signed Up, his wizard Unengaged. The lapse's record in the log channel says his signup
    channel was gone, so no fresh review panel could be posted and his signup is ended, and the
    ending is written once, naming Kimi and the channel. The queue is not stopped."""
    league = await _league(tmp_path)
    gone = league.channel(KIMI_CHANNEL)
    league.remove_channel(KIMI_CHANNEL)

    await _fire_the_timer(league)
    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    assert await _state(league) == "NOT_SIGNED_UP"
    [wizard] = await league.rows(
        "SELECT wizard_state FROM signup_wizard_records WHERE discord_user_id = ?",
        str(SIGNING_UP),
    )
    assert wizard["wizard_state"] == "UNENGAGED"
    gone.send.assert_not_awaited()
    log = league.log()
    assert LAPSE_CHANNEL_GONE in log, log
    assert "a fresh review panel is in their channel" not in log
    assert log.count(CHANNEL_GONE_LINE) == 1, log


async def test_a_lapse_whose_server_is_out_of_cache_stops_rather_than_passing_the_driver_over(
    tmp_path,
):
    """Kimi's correction lapses and the lapse returns him to the approval queue, but when its
    notice is due the league's server is missing from the bot's cache, so his channel cannot be
    looked for. The queue stops at `post_lapse`: Kimi awaits approval, his signup is not ended,
    nothing is posted in his channel and no lapse is recorded yet. Once the server is back, a
    Retry tells his channel the correction lapsed, posts one review panel, and records the lapse
    naming manager 77 and saying a fresh review panel is in his channel."""
    league = await _league(tmp_path)

    await _fire_the_timer(league)
    await run_queue(league.bot, steps=2)
    league.bot.get_guild.return_value = None
    await run_queue(league.bot)

    stopped = await stopped_job(league.db_path)
    assert stopped is not None and stopped["name"] == "post_lapse"
    assert await _state(league) == "PENDING_ADMIN_APPROVAL"
    assert not [row for row in await change_rows(league.db_path) if row["kind"] == "signup.end"]
    assert _posted(league) == ""
    assert CORRECTION_LAPSE not in league.log()

    league.bot.get_guild.return_value = league.guild
    await retry_job(league.bot)

    assert await stopped_job(league.db_path) is None
    assert f"<@{REVIEWER}>" in _posted(league)
    assert len(_review_panels_standing(league)) == 1
    assert CORRECTION_LAPSE in league.log(), league.log()


async def test_a_lapse_whose_wizard_names_no_channel_ends_the_signup(tmp_path):
    """Manager 77 asked for a correction of Kimi's signup, but Kimi's wizard names no signup
    channel. The five minutes run out and the queue runs: with nowhere to post a fresh review
    panel, the lapse ends his signup as it does for a gone channel, so he is Not Signed Up and
    not left in Pending Admin Approval, his wizard Unengaged. Nothing is posted in his channel,
    no record says he is back in the approval queue, and the ending is written once, naming Kimi
    with no channel and saying his signup channel was gone. The queue is not stopped."""
    league = await _league(tmp_path)
    await league.write(
        "UPDATE signup_wizard_records SET signup_channel_id = NULL WHERE discord_user_id = ?",
        str(SIGNING_UP),
    )

    await _fire_the_timer(league)
    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    assert await _state(league) == "NOT_SIGNED_UP"
    [wizard] = await league.rows(
        "SELECT wizard_state FROM signup_wizard_records WHERE discord_user_id = ?",
        str(SIGNING_UP),
    )
    assert wizard["wizard_state"] == "UNENGAGED"
    assert _panels(league) == []
    assert _posted(league) == ""
    log = league.log()
    assert "back in the approval queue" not in log, log
    assert log.count("| Signup | Ended") == 1, log
    assert log.count(
        f"Kimi (`<@{SIGNING_UP}>`) | Signup | Ended\n  Their signup channel was gone when their "
        "review panel was due, so their signup ended. They may press Sign Up again while signups "
        "are open."
    ) == 1, log
    assert "channel:" not in log, log
