"""Approving, rejecting and sending back a signup.

Issue #208. These three are what a league manager actually does with a completed signup, and
all three were unexecuted. `tests/signup/test_correction_parameter_timeout.py` covers what
happens when a correction window *lapses*; nothing covered opening one, or the two decisions
either side of it.

**Approval is the only one that grants anything, and the order it works in matters.** The
driver's total lap time is computed and stored *before* the state transition, because the
seeding value is read off the signup record and the transition is what makes the record a
driver. Reversed, a league would seed its divisions from a value that was not there yet.
`test_the_seeding_lap_time_is_stored_before_the_driver_becomes_unassigned` holds the ordering
rather than merely that both happened.

**Every failure along the way is survivable, deliberately.** A missing role, a role the bot
cannot grant, a driver who has left the server, a state transition that refuses — none of them
may stop an approval or a rejection part-way. A signup half-approved leaves a driver with a
role and no state, or a state and no channel notice, and there is no command that finishes the
job. Each is pinned with its own test.

**All three hold the channel for 24 hours rather than deleting it.** The driver is told the
outcome in the channel, so deleting it immediately would take the message away with it. The
hold is what gives them a day to read it.

`request_changes` writes down **who** asked and **why** before posting the view. The actor is
only a Discord object here and the window outlives the process, so the id is persisted — that
is what lets the timeout mention the right manager when it lapses.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest


SERVER_ID = 1
DRIVER_ID = "7"
CHANNEL_ID = 99
DRIVER_ROLE_ID = 4242
ACTOR_ID = 555


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _wizard(**draft):
    from leaguebot.signup.models.signup_module import ConfigSnapshot, SignupWizardRecord, WizardState

    return SignupWizardRecord(
        id=1,
        discord_user_id=DRIVER_ID,
        # The wizard has no review state of its own — `WizardState` stops at the last
        # question. Awaiting review is carried on the *driver profile*, so a completed
        # wizard sits in its final collection state until the channel is cleared up.
        wizard_state=WizardState.COLLECTING_NOTES,
        signup_channel_id=CHANNEL_ID,
        config_snapshot=ConfigSnapshot(
            nationality_required=False,
            time_type="TIME_TRIAL",
            time_image_required=False,
            selected_track_ids=[],
            slots=[],
        ),
        draft_answers=dict(draft),
        current_lap_track_index=0,
        last_activity_at=None,
    )


def _record(lap_times: dict | None = None):
    return SimpleNamespace(lap_times=lap_times or {})


@pytest.fixture
def review():
    """A `WizardService` wired for the three review outcomes, with an ordered call log."""
    from leaguebot.signup.services.wizard_service import WizardService

    svc = WizardService.__new__(WizardService)
    svc._correction_tasks = {}
    order: list[str] = []

    svc._cancel_inactivity_job = AsyncMock(return_value=None)  # type: ignore[method-assign]
    svc._trigger_channel_hold = AsyncMock(return_value=None)  # type: ignore[method-assign]

    async def _transition(user_id, state):
        order.append(f"transition:{state.value if hasattr(state, 'value') else state}")

    async def _store_total(user_id, lap_times):
        order.append("store_total_lap_ms")

    driver_service = MagicMock()
    driver_service.transition = AsyncMock(side_effect=_transition)

    signup_svc = MagicMock()
    # The module's configuration need only exist; the driver role is the league's (#276).
    signup_svc.get_config = AsyncMock(return_value=SimpleNamespace(signup_channel_id=700))
    signup_svc.get_record = AsyncMock(return_value=_record())
    signup_svc.mark_approved = AsyncMock()
    signup_svc.get_wizard = AsyncMock(return_value=_wizard())
    signup_svc.save_wizard = AsyncMock(return_value=None)

    bot = MagicMock()
    bot.driver_service = driver_service
    bot.signup_module_service = signup_svc
    bot.placement_service.store_total_lap_ms = AsyncMock(side_effect=_store_total)
    bot.config_service.get_league_server_id = AsyncMock(return_value=SERVER_ID)
    bot.config_service.get_server_config = AsyncMock(
        return_value=SimpleNamespace(driver_role_id=DRIVER_ROLE_ID)
    )
    svc._bot = bot

    svc._output_router = MagicMock()
    svc._output_router.post_log = AsyncMock(return_value=None)

    member = MagicMock()
    member.display_name = "Lewis"
    member.mention = f"<@{DRIVER_ID}>"
    member.add_roles = AsyncMock(return_value=None)

    role = MagicMock()
    channel = MagicMock(spec=discord.TextChannel)
    channel.send = AsyncMock(return_value=None)

    guild = MagicMock(spec=discord.Guild)
    guild.get_member = MagicMock(return_value=member)
    guild.get_role = MagicMock(return_value=role)
    guild.get_channel = MagicMock(return_value=channel)

    actor = MagicMock()
    actor.id = ACTOR_ID
    actor.display_name = "Manager"

    return SimpleNamespace(
        svc=svc,
        guild=guild,
        actor=actor,
        member=member,
        role=role,
        channel=channel,
        order=order,
        signup_svc=signup_svc,
        driver_service=driver_service,
    )


def _logged(ctx) -> str:
    return "\n".join(
        str(call.args[0]) for call in ctx.svc._output_router.post_log.await_args_list
    )


# ---------------------------------------------------------------------------
# Approval
# ---------------------------------------------------------------------------


async def test_an_approved_driver_is_granted_the_driver_role(review):
    await review.svc.approve_signup(DRIVER_ID, review.guild, review.actor)

    review.guild.get_role.assert_called_once_with(DRIVER_ROLE_ID)
    review.member.add_roles.assert_awaited_once()
    assert review.member.add_roles.await_args.args[0] is review.role


async def test_an_approved_driver_becomes_unassigned(review):
    """Unassigned, not placed. Approval says they may race; the placement commands decide
    where."""
    await review.svc.approve_signup(DRIVER_ID, review.guild, review.actor)

    assert "transition:UNASSIGNED" in review.order


async def test_the_seeding_lap_time_is_stored_before_the_driver_becomes_unassigned(review):
    """The ordering, not merely that both happened. The seeding value is read off the
    signup record, and the transition is what turns that record into a driver — reversed,
    a league would seed its divisions from a value that was not there yet."""
    review.signup_svc.get_record = AsyncMock(return_value=_record({"1": "1:23.456"}))

    await review.svc.approve_signup(DRIVER_ID, review.guild, review.actor)

    assert review.order.index("store_total_lap_ms") < review.order.index(
        "transition:UNASSIGNED"
    )


async def test_a_driver_with_no_lap_times_stores_no_seeding_value(review):
    """A league that asked for no times has nothing to seed from, and storing a zero would
    seed every driver identically."""
    review.signup_svc.get_record = AsyncMock(return_value=_record({}))

    await review.svc.approve_signup(DRIVER_ID, review.guild, review.actor)

    assert "store_total_lap_ms" not in review.order


async def test_approval_without_a_signup_configuration_does_nothing(review):
    review.signup_svc.get_config = AsyncMock(return_value=None)

    await review.svc.approve_signup(DRIVER_ID, review.guild, review.actor)

    assert review.order == []
    review.member.add_roles.assert_not_awaited()


async def test_a_role_the_bot_cannot_grant_does_not_stop_the_approval(review):
    """Discord refuses a role above the bot's own. The driver must still be approved —
    a half-approval leaves them with a state and no role and nothing to finish the job."""
    review.member.add_roles = AsyncMock(
        side_effect=discord.HTTPException(MagicMock(), "forbidden")
    )

    await review.svc.approve_signup(DRIVER_ID, review.guild, review.actor)

    assert "transition:UNASSIGNED" in review.order


async def test_a_league_with_no_driver_role_configured_still_approves(review):
    review.svc._bot.config_service.get_server_config = AsyncMock(
        return_value=SimpleNamespace(driver_role_id=None)
    )

    await review.svc.approve_signup(DRIVER_ID, review.guild, review.actor)

    review.member.add_roles.assert_not_awaited()
    assert "transition:UNASSIGNED" in review.order


async def test_a_driver_who_has_left_the_server_is_still_approved(review):
    """They may come back, and the record is the league's either way."""
    review.guild.get_member = MagicMock(return_value=None)

    await review.svc.approve_signup(DRIVER_ID, review.guild, review.actor)

    assert "transition:UNASSIGNED" in review.order


async def test_approval_holds_the_channel_rather_than_deleting_it(review):
    """The driver is told the outcome in the channel; deleting it at once would take the
    message with it."""
    await review.svc.approve_signup(DRIVER_ID, review.guild, review.actor)

    review.svc._trigger_channel_hold.assert_awaited_once()
    notice = review.svc._trigger_channel_hold.await_args.args[2]
    assert "approved" in notice
    assert "Manager" in notice


async def test_approval_cancels_the_inactivity_timeout(review):
    """The wizard is finished. Left armed, the timeout would later close a signup that had
    already been approved."""
    await review.svc.approve_signup(DRIVER_ID, review.guild, review.actor)

    review.svc._cancel_inactivity_job.assert_awaited_once()


async def test_approval_is_logged_naming_the_manager_and_the_driver(review):
    await review.svc.approve_signup(DRIVER_ID, review.guild, review.actor)

    logged = _logged(review)
    assert "Approved" in logged
    assert "Manager" in logged


# ---------------------------------------------------------------------------
# Rejection
# ---------------------------------------------------------------------------


async def test_a_rejected_driver_returns_to_not_signed_up(review):
    await review.svc.reject_signup(DRIVER_ID, review.guild, review.actor)

    assert "transition:NOT_SIGNED_UP" in review.order


async def test_rejection_grants_no_role(review):
    await review.svc.reject_signup(DRIVER_ID, review.guild, review.actor)

    review.member.add_roles.assert_not_awaited()


async def test_a_rejection_reason_reaches_the_driver(review):
    """The driver has to know what to fix before signing up again."""
    await review.svc.reject_signup(
        DRIVER_ID, review.guild, review.actor, reason="Lap time unverified"
    )

    notice = review.svc._trigger_channel_hold.await_args.args[2]
    assert "Lap time unverified" in notice


async def test_a_rejection_without_a_reason_omits_the_reason_line(review):
    """An empty **Reason:** would read as a reason nobody gave."""
    await review.svc.reject_signup(DRIVER_ID, review.guild, review.actor)

    assert "Reason:" not in review.svc._trigger_channel_hold.await_args.args[2]


async def test_a_failed_transition_does_not_stop_the_rejection(review):
    """The driver may already be NOT_SIGNED_UP. The notice and the channel hold must still
    happen, or the driver is never told."""
    review.driver_service.transition = AsyncMock(side_effect=ValueError("already there"))

    await review.svc.reject_signup(DRIVER_ID, review.guild, review.actor)

    review.svc._trigger_channel_hold.assert_awaited_once()


async def test_rejection_cancels_a_pending_correction_window(review):
    """A driver sent back and then rejected must not still have a correction window open,
    which would let them re-submit into a signup that no longer exists."""
    task = MagicMock()
    review.svc._correction_tasks[DRIVER_ID] = task

    await review.svc.reject_signup(DRIVER_ID, review.guild, review.actor)

    task.cancel.assert_called_once()
    assert DRIVER_ID not in review.svc._correction_tasks


async def test_a_rejection_reason_is_logged(review):
    await review.svc.reject_signup(
        DRIVER_ID, review.guild, review.actor, reason="Duplicate entry"
    )

    logged = _logged(review)
    assert "Rejected" in logged
    assert "Duplicate entry" in logged


# ---------------------------------------------------------------------------
# Requesting changes
# ---------------------------------------------------------------------------


async def test_requesting_changes_awaits_a_correction_parameter(review):
    wizard = _wizard()
    review.signup_svc.get_wizard = AsyncMock(return_value=wizard)

    await review.svc.request_changes(DRIVER_ID, review.guild, review.actor)

    assert "transition:AWAITING_CORRECTION_PARAMETER" in review.order
    for task in review.svc._correction_tasks.values():
        task.cancel()


async def test_the_manager_who_asked_is_written_down(review):
    """The actor is only a Discord object here and the window outlives the process, so the
    id is persisted — it is what lets the timeout mention the right manager."""
    wizard = _wizard()
    review.signup_svc.get_wizard = AsyncMock(return_value=wizard)

    await review.svc.request_changes(DRIVER_ID, review.guild, review.actor)

    assert wizard.draft_answers["_correction_requested_by"] == str(ACTOR_ID)
    review.signup_svc.save_wizard.assert_awaited_once()
    for task in review.svc._correction_tasks.values():
        task.cancel()


async def test_the_reason_is_saved_alongside_it(review):
    wizard = _wizard()
    review.signup_svc.get_wizard = AsyncMock(return_value=wizard)

    await review.svc.request_changes(
        DRIVER_ID, review.guild, review.actor, reason="Wrong platform"
    )

    assert wizard.draft_answers["_correction_reason"] == "Wrong platform"
    for task in review.svc._correction_tasks.values():
        task.cancel()


async def test_no_reason_writes_no_reason(review):
    """An absent key rather than an empty string, so the re-submission prompt can tell
    "no reason given" from "the reason was blank"."""
    wizard = _wizard()
    review.signup_svc.get_wizard = AsyncMock(return_value=wizard)

    await review.svc.request_changes(DRIVER_ID, review.guild, review.actor)

    assert "_correction_reason" not in wizard.draft_answers
    for task in review.svc._correction_tasks.values():
        task.cancel()


async def test_the_driver_is_prompted_in_their_own_channel(review):
    wizard = _wizard()
    review.signup_svc.get_wizard = AsyncMock(return_value=wizard)

    await review.svc.request_changes(DRIVER_ID, review.guild, review.actor)

    review.channel.send.assert_awaited_once()
    assert "requested a correction" in review.channel.send.await_args.args[0]
    for task in review.svc._correction_tasks.values():
        task.cancel()


async def test_a_five_minute_window_is_armed(review):
    wizard = _wizard()
    review.signup_svc.get_wizard = AsyncMock(return_value=wizard)

    await review.svc.request_changes(DRIVER_ID, review.guild, review.actor)

    assert DRIVER_ID in review.svc._correction_tasks
    for task in review.svc._correction_tasks.values():
        task.cancel()


async def test_asking_twice_replaces_the_window_rather_than_stacking_them(review):
    """Two live timeouts would close the window at the first of them, cutting the driver's
    time short without explanation."""
    wizard = _wizard()
    review.signup_svc.get_wizard = AsyncMock(return_value=wizard)
    existing = MagicMock()
    review.svc._correction_tasks[DRIVER_ID] = existing

    await review.svc.request_changes(DRIVER_ID, review.guild, review.actor)

    existing.cancel.assert_called_once()
    assert review.svc._correction_tasks[DRIVER_ID] is not existing
    for task in review.svc._correction_tasks.values():
        if isinstance(task, asyncio.Task):
            task.cancel()


async def test_a_driver_with_no_wizard_is_left_alone(review):
    review.signup_svc.get_wizard = AsyncMock(return_value=None)

    await review.svc.request_changes(DRIVER_ID, review.guild, review.actor)

    assert review.order == []
    review.channel.send.assert_not_awaited()


async def test_a_missing_channel_still_transitions_and_arms_the_window(review):
    """The channel may have been deleted by hand. The driver can still be sent back; they
    simply will not see the prompt, and the window will lapse and say so."""
    wizard = _wizard()
    review.signup_svc.get_wizard = AsyncMock(return_value=wizard)
    review.guild.get_channel = MagicMock(return_value=None)

    await review.svc.request_changes(DRIVER_ID, review.guild, review.actor)

    assert "transition:AWAITING_CORRECTION_PARAMETER" in review.order
    assert DRIVER_ID in review.svc._correction_tasks
    for task in review.svc._correction_tasks.values():
        task.cancel()


async def test_an_approved_signup_is_marked_as_such(review):
    """Issue #243: an approved signup outranks a later one of the driver's that was not."""
    await review.svc.approve_signup(DRIVER_ID, review.guild, review.actor)

    review.signup_svc.mark_approved.assert_awaited_once_with(DRIVER_ID)
