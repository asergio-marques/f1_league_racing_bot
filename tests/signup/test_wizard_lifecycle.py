"""Ending a signup wizard — withdrawal, timeout, a driver leaving, and a restart.

Issue #208. Four ways a wizard ends other than being completed, none of them executed by any
test. Between them they decide what happens to a driver who changes their mind, one who goes
quiet, one who leaves the server, and every open wizard when the bot is restarted.

**Three of the four hold the channel; one deletes it outright.** Withdrawal and the inactivity
timeout both leave the channel standing for 24 hours, because the driver is still there and is
being told why their signup ended. `handle_member_remove` deletes it at once — the driver is
gone, nobody will read it, and a private channel for a departed member is clutter a league has
to clear by hand. `test_a_departing_driver_s_channel_is_deleted_rather_than_held` is what keeps
the two apart.

**Every path cancels every job.** A wizard leaves an inactivity job, possibly a channel-delete
job, and possibly an in-memory correction task. Any left armed fires later against a driver
whose signup has already ended — the inactivity timeout would transition a driver who had since
been approved. Each ending is tested for the cancellations it owes.

**`handle_member_remove` has to look in two places.** A driver part-way through the questions
has an active wizard; a driver waiting on a manager has an `UNENGAGED` wizard and an active
*driver state*. Both need cleaning up and the second is the one easily missed, so both are
pinned — as is the case that must do nothing at all, a member leaving who was never signing up.

**Recovery is where a restart is survived.** The inactivity deadline lives in the database as
`last_activity_at`, and the job that enforces it does not survive the process. On restart every
open wizard is re-armed from its own last activity, and one whose deadline has already gone by
is expired immediately rather than given a fresh 24 hours — which is what a naive re-arm would
do, and would let a driver hold a wizard open indefinitely by restarting the bot.

The recovery tests compute their times from the real clock rather than pinning a date, because
`recover_wizards` reads `datetime.now` itself and takes no `now` parameter.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest


SERVER_ID = 1
DRIVER_ID = "7"
CHANNEL_ID = 99


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _wizard(state=None, *, channel_id: int | None = CHANNEL_ID, last_activity: str | None = None):
    from leaguebot.signup.models.signup_module import ConfigSnapshot, SignupWizardRecord, WizardState

    return SignupWizardRecord(
        id=1,
        discord_user_id=DRIVER_ID,
        wizard_state=state or WizardState.COLLECTING_PLATFORM,
        signup_channel_id=channel_id,
        config_snapshot=ConfigSnapshot(
            nationality_required=False,
            time_type="TIME_TRIAL",
            time_image_required=False,
            selected_track_ids=[],
            slots=[],
        ),
        draft_answers={},
        current_lap_track_index=0,
        last_activity_at=last_activity,
    )


@pytest.fixture
def lifecycle():
    from leaguebot.signup.services.wizard_service import WizardService

    svc = WizardService.__new__(WizardService)
    svc._correction_tasks = {}

    svc._cancel_inactivity_job = AsyncMock(return_value=None)  # type: ignore[method-assign]
    svc._cancel_channel_delete_job = AsyncMock(return_value=None)  # type: ignore[method-assign]
    svc._arm_inactivity_job = AsyncMock(return_value=None)  # type: ignore[method-assign]
    svc.trigger_channel_hold = AsyncMock(return_value=None)  # type: ignore[method-assign]
    svc.recover_correction_timeouts = AsyncMock(return_value=None)  # type: ignore[method-assign]

    signup_svc = MagicMock()
    signup_svc.get_wizard = AsyncMock(return_value=_wizard())
    signup_svc.delete_wizard = AsyncMock(return_value=None)
    signup_svc.get_record = AsyncMock(
        return_value=SimpleNamespace(
            server_display_name="Lewis Hamilton", discord_username="lewis"
        )
    )
    signup_svc.get_all_active_wizards_all_servers = AsyncMock(return_value=[])

    driver_service = MagicMock()
    driver_service.transition = AsyncMock(return_value=None)
    driver_service.get_profile = AsyncMock(return_value=None)

    channel = MagicMock()
    channel.delete = AsyncMock(return_value=None)
    guild = MagicMock(spec=discord.Guild)
    guild.get_channel = MagicMock(return_value=channel)

    bot = MagicMock()
    bot.signup_module_service = signup_svc
    bot.driver_service = driver_service
    bot.get_guild = MagicMock(return_value=guild)
    bot.config_service.get_league_server_id = AsyncMock(return_value=SERVER_ID)
    svc._bot = bot

    svc._output_router = MagicMock()
    svc._output_router.post_log = AsyncMock(return_value=None)
    # One log channel, reached through the service's router or the bot's alike.
    bot.output_router = svc._output_router

    return SimpleNamespace(
        svc=svc,
        guild=guild,
        channel=channel,
        signup_svc=signup_svc,
        driver_service=driver_service,
    )


def _transitioned_to(ctx) -> list[str]:
    return [
        call.args[1].value if hasattr(call.args[1], "value") else str(call.args[1])
        for call in ctx.driver_service.transition.await_args_list
    ]


# ---------------------------------------------------------------------------
# Withdrawal
# ---------------------------------------------------------------------------


async def test_withdrawing_returns_the_driver_to_not_signed_up(lifecycle):
    await lifecycle.svc.withdraw(DRIVER_ID, lifecycle.guild)

    assert "NOT_SIGNED_UP" in _transitioned_to(lifecycle)


async def test_withdrawing_holds_the_channel_rather_than_deleting_it(lifecycle):
    """The driver is still here and is being told their signup has ended."""
    await lifecycle.svc.withdraw(DRIVER_ID, lifecycle.guild)

    lifecycle.svc.trigger_channel_hold.assert_awaited_once()
    assert "cancelled" in lifecycle.svc.trigger_channel_hold.await_args.args[2]
    lifecycle.channel.delete.assert_not_awaited()


async def test_withdrawing_cancels_the_inactivity_job(lifecycle):
    """Left armed it would fire against a driver who has already withdrawn."""
    await lifecycle.svc.withdraw(DRIVER_ID, lifecycle.guild)

    lifecycle.svc._cancel_inactivity_job.assert_awaited_once()


async def test_withdrawing_cancels_a_pending_correction_window(lifecycle):
    task = MagicMock()
    lifecycle.svc._correction_tasks[DRIVER_ID] = task

    await lifecycle.svc.withdraw(DRIVER_ID, lifecycle.guild)

    task.cancel.assert_called_once()
    assert DRIVER_ID not in lifecycle.svc._correction_tasks


#: The reason `withdraw` gives where the signup has already ended (S5-A6).
_ALREADY_ENDED = "This signup has already ended. Nothing was changed."


def _alex_on_the_server(lifecycle) -> None:
    lifecycle.guild.get_member = MagicMock(
        return_value=SimpleNamespace(id=int(DRIVER_ID), display_name="Alex", mention="<@7>")
    )


async def test_withdrawing_writes_one_withdrawn_line(lifecycle):
    """A withdrawal is the driver's own act through the Cancel Signup button, so it writes one
    line in the wizard's family (S5-A1)."""
    _alex_on_the_server(lifecycle)

    refused = await lifecycle.svc.withdraw(DRIVER_ID, lifecycle.guild)

    assert not refused
    lines = [c.args[0] for c in lifecycle.svc._output_router.post_log.await_args_list]
    assert lines == [f"Alex (<@{DRIVER_ID}>) | Signup | Withdrawn"]


async def test_a_withdrawal_after_the_signup_ended_is_refused_and_changes_nothing(lifecycle):
    """The driver's transition is refused (`ValueError`) because the signup has already ended:
    withdrawn, rejected, expired or closed. `withdraw` returns why, for the button to answer and
    record, and neither posts the notice again nor pushes the channel's deletion back."""
    _alex_on_the_server(lifecycle)
    lifecycle.driver_service.transition = AsyncMock(side_effect=ValueError("already"))

    refused = await lifecycle.svc.withdraw(DRIVER_ID, lifecycle.guild)

    assert refused == _ALREADY_ENDED
    lifecycle.svc.trigger_channel_hold.assert_not_awaited()
    lifecycle.svc._output_router.post_log.assert_not_awaited()


async def test_a_withdrawal_whose_transition_fails_otherwise_is_not_swallowed(lifecycle):
    """Only the expected refusal (`ValueError`) is caught by name; any other error reaches the
    Cancel Signup button's failure handler, and nothing claims the signup was withdrawn."""
    _alex_on_the_server(lifecycle)
    lifecycle.driver_service.transition = AsyncMock(side_effect=RuntimeError("database is locked"))

    with pytest.raises(RuntimeError):
        await lifecycle.svc.withdraw(DRIVER_ID, lifecycle.guild)

    lifecycle.svc.trigger_channel_hold.assert_not_awaited()
    lifecycle.svc._output_router.post_log.assert_not_awaited()


#: The detail a signup's own line gains where its closing notice was refused (#439, F2, P3).
_CHANNEL_KEPT = (
    f"  not done: the closing notice could not be posted in <#{CHANNEL_ID}> (Forbidden); "
    "the channel is kept, readable but locked, and will not delete itself: delete it by hand"
)


def _notice_refused(lifecycle):
    """The real channel hold, over a signup channel whose notice Discord refuses (403)."""
    del lifecycle.svc.trigger_channel_hold  # the fixture's double; the service's own hold runs
    lifecycle.svc._scheduler = MagicMock()
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = CHANNEL_ID
    channel.mention = f"<#{CHANNEL_ID}>"
    channel.send = AsyncMock(side_effect=discord.Forbidden(MagicMock(status=403), "no"))
    channel.set_permissions = AsyncMock(return_value=None)
    lifecycle.guild.get_channel = MagicMock(return_value=channel)
    return channel


async def test_a_withdrawal_whose_notice_is_refused_keeps_the_channel_and_names_it_in_its_line(
    lifecycle,
):
    """A withdrawal whose closing notice Discord refuses keeps the channel readable with the
    driver's typing locked and no deletion armed, and its "Withdrawn" line names the channel for
    a league manager to delete by hand (#439, F2, P3)."""
    _alex_on_the_server(lifecycle)
    channel = _notice_refused(lifecycle)

    await lifecycle.svc.withdraw(DRIVER_ID, lifecycle.guild)

    assert channel.set_permissions.await_args.kwargs["send_messages"] is False
    lifecycle.svc._scheduler._scheduler.add_job.assert_not_called()
    lines = [c.args[0] for c in lifecycle.svc._output_router.post_log.await_args_list]
    assert lines == [f"Alex (<@{DRIVER_ID}>) | Signup | Withdrawn\n{_CHANNEL_KEPT}"]


# ---------------------------------------------------------------------------
# The inactivity timeout
# ---------------------------------------------------------------------------


async def test_a_timed_out_driver_returns_to_not_signed_up(lifecycle):
    await lifecycle.svc.handle_inactivity_timeout(DRIVER_ID)

    assert "NOT_SIGNED_UP" in _transitioned_to(lifecycle)


async def test_a_timed_out_driver_is_told_why_in_their_channel(lifecycle):
    await lifecycle.svc.handle_inactivity_timeout(DRIVER_ID)

    assert "expired" in lifecycle.svc.trigger_channel_hold.await_args.args[2]


async def test_a_timeout_for_a_guild_the_bot_has_left_still_ends_the_signup(lifecycle):
    """The job outlives the bot's membership of the server. The driver state is the
    league's record and must be put right even where no channel can be held."""
    lifecycle.svc._bot.get_guild = MagicMock(return_value=None)

    await lifecycle.svc.handle_inactivity_timeout(DRIVER_ID)

    assert "NOT_SIGNED_UP" in _transitioned_to(lifecycle)
    lifecycle.svc.trigger_channel_hold.assert_not_awaited()


async def test_a_timeout_cancels_a_pending_correction_window(lifecycle):
    task = MagicMock()
    lifecycle.svc._correction_tasks[DRIVER_ID] = task

    await lifecycle.svc.handle_inactivity_timeout(DRIVER_ID)

    task.cancel.assert_called_once()


#: The lapse the wizard's expiry records, naming the driver who started it (S5-A4).
_EXPIRY_LAPSE = (
    f"⌛ the signup wizard lapsed unconfirmed (started by Alex (<@{DRIVER_ID}>))\n"
    "  The signup was cancelled after 24 hours without an answer. "
    "They may press Sign Up again while signups are open."
)


async def test_an_expired_wizard_records_one_lapse_naming_the_driver(lifecycle):
    """The 24-hour expiry ends a flow the driver started, so it is recorded as a lapse naming
    them, saying what became of it and what they may do next (owner, 2026-09-30, "Log both as
    lapses")."""
    _alex_on_the_server(lifecycle)

    await lifecycle.svc.handle_inactivity_timeout(DRIVER_ID)

    lines = [c.args[0] for c in lifecycle.svc._output_router.post_log.await_args_list]
    assert lines == [_EXPIRY_LAPSE]


async def test_an_expiry_whose_notice_is_refused_keeps_the_channel_and_names_it(lifecycle):
    """An expiry whose closing notice Discord refuses keeps the channel readable with the
    driver's typing locked and no deletion armed, and after the lapse line a "Channel kept" line
    of the wizard's family names the channel for a league manager to delete by hand (#439, F2,
    P3)."""
    _alex_on_the_server(lifecycle)
    channel = _notice_refused(lifecycle)

    await lifecycle.svc.handle_inactivity_timeout(DRIVER_ID)

    assert channel.set_permissions.await_args.kwargs["send_messages"] is False
    lifecycle.svc._scheduler._scheduler.add_job.assert_not_called()
    lines = [c.args[0] for c in lifecycle.svc._output_router.post_log.await_args_list]
    assert lines == [
        _EXPIRY_LAPSE,
        f"Alex (<@{DRIVER_ID}>) | Signup | Channel kept\n{_CHANNEL_KEPT}",
    ]


async def test_an_expiry_whose_transition_fails_otherwise_records_no_lapse_and_tells_nobody(
    lifecycle, caplog,
):
    """Only the expected refusal (`ValueError`) is caught by name. Any other error goes to the
    host log with its traceback, whether logged here or raised to the job runner that logs it:
    the driver is not told their session expired, and no lapse is recorded for a signup that
    did not end."""
    _alex_on_the_server(lifecycle)
    lifecycle.driver_service.transition = AsyncMock(side_effect=RuntimeError("database is locked"))

    raised = False
    with caplog.at_level(logging.WARNING):
        try:
            await lifecycle.svc.handle_inactivity_timeout(DRIVER_ID)
        except RuntimeError:
            raised = True

    logged = any(
        record.exc_info and isinstance(record.exc_info[1], RuntimeError)
        for record in caplog.records
    )
    assert raised or logged, "the error reached neither the job runner nor the host log"
    lifecycle.svc.trigger_channel_hold.assert_not_awaited()
    lifecycle.svc._output_router.post_log.assert_not_awaited()


async def test_an_expiry_for_a_driver_already_moved_on_still_holds_the_channel(lifecycle):
    """The 24-hour job can fire for a driver who has already moved on, the transition refusing
    with `ValueError`. The channel is still held, with the expiry notice and its deletion 24 hours
    on, but no lapse is recorded: the signup did not lapse (owner, 2026-10-01, Gate 3, "Still hold
    the channel")."""
    _alex_on_the_server(lifecycle)
    lifecycle.driver_service.transition = AsyncMock(
        side_effect=ValueError("illegal transition")
    )

    await lifecycle.svc.handle_inactivity_timeout(DRIVER_ID)

    lifecycle.svc.trigger_channel_hold.assert_awaited_once()
    held = lifecycle.svc.trigger_channel_hold.await_args.args
    assert held[0] == DRIVER_ID
    assert held[1] is lifecycle.guild
    assert "expired" in held[2]
    lifecycle.svc._output_router.post_log.assert_not_awaited()


# ---------------------------------------------------------------------------
# A driver leaving the server
# ---------------------------------------------------------------------------


async def test_a_driver_part_way_through_the_questions_is_cleaned_up(lifecycle):
    await lifecycle.svc.handle_member_remove(DRIVER_ID, lifecycle.guild)

    assert "NOT_SIGNED_UP" in _transitioned_to(lifecycle)
    lifecycle.signup_svc.delete_wizard.assert_awaited_once()


async def test_a_driver_waiting_on_a_manager_is_cleaned_up_too(lifecycle):
    """Their wizard is UNENGAGED, so the wizard state says nothing — the *driver* state is
    where the open signup lives. This is the half easily missed."""
    from leaguebot.core.models.driver_profile import DriverState
    from leaguebot.signup.models.signup_module import WizardState

    lifecycle.signup_svc.get_wizard = AsyncMock(
        return_value=_wizard(WizardState.UNENGAGED)
    )
    lifecycle.driver_service.get_profile = AsyncMock(
        return_value=SimpleNamespace(current_state=DriverState.PENDING_ADMIN_APPROVAL)
    )

    await lifecycle.svc.handle_member_remove(DRIVER_ID, lifecycle.guild)

    assert "NOT_SIGNED_UP" in _transitioned_to(lifecycle)


async def test_a_driver_awaiting_a_correction_parameter_is_cleaned_up(lifecycle):
    from leaguebot.core.models.driver_profile import DriverState
    from leaguebot.signup.models.signup_module import WizardState

    lifecycle.signup_svc.get_wizard = AsyncMock(
        return_value=_wizard(WizardState.UNENGAGED)
    )
    lifecycle.driver_service.get_profile = AsyncMock(
        return_value=SimpleNamespace(
            current_state=DriverState.AWAITING_CORRECTION_PARAMETER
        )
    )

    await lifecycle.svc.handle_member_remove(DRIVER_ID, lifecycle.guild)

    assert "NOT_SIGNED_UP" in _transitioned_to(lifecycle)


async def test_a_member_who_was_never_signing_up_is_left_alone(lifecycle):
    """Most people leaving a server have no signup at all. Transitioning them would write
    a driver state for somebody who never had one."""
    from leaguebot.signup.models.signup_module import WizardState

    lifecycle.signup_svc.get_wizard = AsyncMock(
        return_value=_wizard(WizardState.UNENGAGED)
    )
    lifecycle.driver_service.get_profile = AsyncMock(return_value=None)

    await lifecycle.svc.handle_member_remove(DRIVER_ID, lifecycle.guild)

    lifecycle.driver_service.transition.assert_not_awaited()
    lifecycle.signup_svc.delete_wizard.assert_not_awaited()


async def test_an_approved_driver_leaving_is_left_alone(lifecycle):
    """An UNASSIGNED driver has finished signing up; there is no wizard to clean up and
    their record is the league's to keep."""
    from leaguebot.core.models.driver_profile import DriverState
    from leaguebot.signup.models.signup_module import WizardState

    lifecycle.signup_svc.get_wizard = AsyncMock(
        return_value=_wizard(WizardState.UNENGAGED)
    )
    lifecycle.driver_service.get_profile = AsyncMock(
        return_value=SimpleNamespace(current_state=DriverState.UNASSIGNED)
    )

    await lifecycle.svc.handle_member_remove(DRIVER_ID, lifecycle.guild)

    lifecycle.driver_service.transition.assert_not_awaited()


async def test_a_departing_driver_s_channel_is_deleted_rather_than_held(lifecycle):
    """The distinction from every other ending. Nobody will read it, and a private channel
    for a departed member is clutter the league clears by hand."""
    await lifecycle.svc.handle_member_remove(DRIVER_ID, lifecycle.guild)

    lifecycle.channel.delete.assert_awaited_once()
    lifecycle.svc.trigger_channel_hold.assert_not_awaited()


async def test_a_channel_that_cannot_be_deleted_does_not_stop_the_cleanup(lifecycle):
    lifecycle.channel.delete = AsyncMock(
        side_effect=discord.HTTPException(MagicMock(), "forbidden")
    )

    await lifecycle.svc.handle_member_remove(DRIVER_ID, lifecycle.guild)

    lifecycle.signup_svc.delete_wizard.assert_awaited_once()


async def test_a_departure_cancels_both_scheduled_jobs(lifecycle):
    """A wizard may have left an inactivity job *and* a channel-delete job."""
    await lifecycle.svc.handle_member_remove(DRIVER_ID, lifecycle.guild)

    lifecycle.svc._cancel_inactivity_job.assert_awaited_once()
    lifecycle.svc._cancel_channel_delete_job.assert_awaited_once()


async def test_the_departure_log_names_the_driver_and_the_state_they_were_in(lifecycle):
    """The state is captured before the cleanup, so the log says what the driver was doing
    rather than the NOT_SIGNED_UP they were left in."""
    await lifecycle.svc.handle_member_remove(DRIVER_ID, lifecycle.guild)

    logged = lifecycle.svc._output_router.post_log.await_args.args[0]
    assert "Lewis Hamilton" in logged
    assert "COLLECTING_PLATFORM" in logged


async def test_a_driver_with_no_record_is_logged_by_id(lifecycle):
    lifecycle.signup_svc.get_record = AsyncMock(return_value=None)

    await lifecycle.svc.handle_member_remove(DRIVER_ID, lifecycle.guild)

    assert DRIVER_ID in lifecycle.svc._output_router.post_log.await_args.args[0]


async def test_a_failing_record_lookup_does_not_stop_the_log(lifecycle):
    """The log is the only trace a departure leaves; losing it to a lookup failure would
    leave the league with no record of why a driver vanished."""
    lifecycle.signup_svc.get_record = AsyncMock(side_effect=RuntimeError("db gone"))

    await lifecycle.svc.handle_member_remove(DRIVER_ID, lifecycle.guild)

    lifecycle.svc._output_router.post_log.assert_awaited_once()


async def test_a_departure_whose_transition_fails_otherwise_is_not_swallowed(lifecycle):
    """Only the expected refusal (`ValueError`, a driver already moved on) is caught by name; any
    other error reaches the listener rather than being passed over."""
    lifecycle.driver_service.transition = AsyncMock(side_effect=RuntimeError("database is locked"))

    with pytest.raises(RuntimeError):
        await lifecycle.svc.handle_member_remove(DRIVER_ID, lifecycle.guild)


async def test_a_departing_driver_already_moved_on_is_still_cleaned_up(lifecycle):
    """A driver whose transition is refused (`ValueError`) has already moved on; the rest of the
    cleanup still runs, as it does today."""
    lifecycle.driver_service.transition = AsyncMock(side_effect=ValueError("already"))

    await lifecycle.svc.handle_member_remove(DRIVER_ID, lifecycle.guild)

    lifecycle.channel.delete.assert_awaited_once()
    lifecycle.signup_svc.delete_wizard.assert_awaited_once()
    lifecycle.svc._output_router.post_log.assert_awaited_once()


# ---------------------------------------------------------------------------
# Recovery across a restart
# ---------------------------------------------------------------------------


async def test_an_open_wizard_is_re_armed_from_its_own_last_activity(lifecycle):
    """The deadline is in the database; the job enforcing it is not. Re-arming from the
    recorded activity is what makes the timeout survive a restart."""
    last = datetime.now(timezone.utc) - timedelta(hours=1)
    lifecycle.signup_svc.get_all_active_wizards_all_servers = AsyncMock(
        return_value=[_wizard(last_activity=last.isoformat())]
    )

    await lifecycle.svc.recover_wizards()

    lifecycle.svc._arm_inactivity_job.assert_awaited_once()
    fire_at = lifecycle.svc._arm_inactivity_job.await_args.args[1]
    assert fire_at == last + timedelta(hours=24)


async def test_a_deadline_already_passed_expires_at_once(lifecycle):
    """Not re-armed for a fresh 24 hours, which a naive recovery would do — a driver could
    then hold a wizard open indefinitely by getting the bot restarted."""
    last = datetime.now(timezone.utc) - timedelta(hours=30)
    lifecycle.signup_svc.get_all_active_wizards_all_servers = AsyncMock(
        return_value=[_wizard(last_activity=last.isoformat())]
    )
    expired: list = []
    lifecycle.svc.handle_inactivity_timeout = AsyncMock(  # type: ignore[method-assign]
        side_effect=lambda u: expired.append(u)
    )

    await lifecycle.svc.recover_wizards()
    await asyncio.sleep(0)  # let the background task run

    lifecycle.svc._arm_inactivity_job.assert_not_awaited()
    assert expired == [DRIVER_ID]


async def test_a_wizard_with_no_recorded_activity_expires_at_once(lifecycle):
    """There is no deadline to compute, and giving it a fresh one would reward the gap."""
    lifecycle.signup_svc.get_all_active_wizards_all_servers = AsyncMock(
        return_value=[_wizard(last_activity=None)]
    )
    expired: list = []
    lifecycle.svc.handle_inactivity_timeout = AsyncMock(  # type: ignore[method-assign]
        side_effect=lambda u: expired.append(u)
    )

    await lifecycle.svc.recover_wizards()
    await asyncio.sleep(0)

    assert expired == [DRIVER_ID]


async def test_a_parked_wizard_is_not_re_armed(lifecycle):
    """An UNENGAGED wizard belongs to a driver waiting on a manager, who cannot be timed
    out for the manager's delay."""
    from leaguebot.signup.models.signup_module import WizardState

    last = datetime.now(timezone.utc) - timedelta(hours=1)
    lifecycle.signup_svc.get_all_active_wizards_all_servers = AsyncMock(
        return_value=[_wizard(WizardState.UNENGAGED, last_activity=last.isoformat())]
    )

    await lifecycle.svc.recover_wizards()

    lifecycle.svc._arm_inactivity_job.assert_not_awaited()


async def test_a_naive_timestamp_is_read_as_utc(lifecycle):
    """Rows written before the timestamps carried a zone still exist. Read as local time
    the deadline would move by the host's offset, so the same database would expire a
    wizard at a different moment on the Pi than on a developer's machine."""
    last = (datetime.now(timezone.utc) - timedelta(hours=1)).replace(tzinfo=None)
    lifecycle.signup_svc.get_all_active_wizards_all_servers = AsyncMock(
        return_value=[_wizard(last_activity=last.isoformat())]
    )

    await lifecycle.svc.recover_wizards()

    fire_at = lifecycle.svc._arm_inactivity_job.await_args.args[1]
    assert fire_at.tzinfo is not None
    assert fire_at == last.replace(tzinfo=timezone.utc) + timedelta(hours=24)


async def test_recovery_releases_pending_correction_windows_first(lifecycle):
    """The five-minute window is an in-memory task and cannot survive a restart, so the
    drivers holding one are released before anything else is considered."""
    await lifecycle.svc.recover_wizards()

    lifecycle.svc.recover_correction_timeouts.assert_awaited_once()


async def test_a_wizard_expired_at_restart_records_its_lapse(lifecycle):
    """A wizard whose deadline went by while the bot was down expires at once on restart, and
    records the same lapse as the 24-hour job would have."""
    _alex_on_the_server(lifecycle)
    last = datetime.now(timezone.utc) - timedelta(hours=30)
    lifecycle.signup_svc.get_all_active_wizards_all_servers = AsyncMock(
        return_value=[_wizard(last_activity=last.isoformat())]
    )
    before = asyncio.all_tasks()

    await lifecycle.svc.recover_wizards()
    await asyncio.gather(*(asyncio.all_tasks() - before - {asyncio.current_task()}))

    lines = [c.args[0] for c in lifecycle.svc._output_router.post_log.await_args_list]
    assert lines == [_EXPIRY_LAPSE]


# ---------------------------------------------------------------------------
# A restart after a signup has ended (#439, slice 5)
# ---------------------------------------------------------------------------
#
# The real signup module and driver services on a database built by the migrations: a restart
# reads the wizards a signup's end left behind, so the record each route writes is the subject.
# The Discord side is doubled. `recover_wizards` reads the host's clock, so the times are
# computed from it.


_WITHDRAWN = f"Alex (<@{DRIVER_ID}>) | Signup | Withdrawn"


async def _real_signup(tmp_path, *, hours_since_activity: float, refusing: bool = False):
    """Driver 7 part-way through the wizard (Pending Signup Completion, the wizard collecting
    their notes in channel 99), their last answer *hours_since_activity* hours ago, on the real
    services; a `WizardService` over them, recording each inactivity job armed, each expiry
    fired and each closing notice held. Where *refusing*, the real channel hold runs over a
    channel whose notice Discord refuses (403); otherwise the hold is a double that posts."""
    from leaguebot.core.db.database import get_connection, run_migrations
    from leaguebot.core.services.driver_service import DriverService
    from leaguebot.signup.services.signup_module_service import SignupModuleService
    from leaguebot.signup.services.wizard_service import HoldOutcome, WizardService

    db_path = str(tmp_path / "restart.db")
    await run_migrations(db_path)
    last = datetime.now(timezone.utc) - timedelta(hours=hours_since_activity)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 0, 0, 0)",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO driver_profiles (discord_user_id, current_state) "
            "VALUES (?, 'PENDING_SIGNUP_COMPLETION')",
            (DRIVER_ID,),
        )
        await db.execute(
            "INSERT INTO signup_wizard_records (discord_user_id, wizard_state, "
            "signup_channel_id, last_activity_at) VALUES (?, 'COLLECTING_NOTES', ?, ?)",
            (DRIVER_ID, CHANNEL_ID, last.isoformat()),
        )
        await db.commit()

    channel = MagicMock(spec=discord.TextChannel)
    channel.id = CHANNEL_ID
    channel.mention = f"<#{CHANNEL_ID}>"
    channel.send = AsyncMock(
        side_effect=discord.Forbidden(MagicMock(status=403), "no") if refusing else None
    )
    channel.set_permissions = AsyncMock(return_value=None)
    # The signup channel, where the window's Sign Up button stands and its close is posted.
    button = MagicMock()
    button.delete = AsyncMock(return_value=None)
    signups = MagicMock()
    signups.fetch_message = AsyncMock(return_value=button)
    signups.send = AsyncMock(return_value=SimpleNamespace(id=9900))
    guild = MagicMock(spec=discord.Guild)
    guild.get_channel = MagicMock(
        side_effect=lambda cid: channel if cid == CHANNEL_ID else signups
    )
    guild.get_member = MagicMock(
        return_value=SimpleNamespace(id=int(DRIVER_ID), display_name="Alex", mention="<@7>")
    )

    bot = MagicMock()
    bot.db_path = db_path
    bot.signup_module_service = SignupModuleService(db_path)
    bot.driver_service = DriverService(db_path)
    bot.get_guild = MagicMock(return_value=guild)
    bot.config_service.get_league_server_id = AsyncMock(return_value=SERVER_ID)
    router = MagicMock()
    router.post_log = AsyncMock(return_value=None)
    bot.output_router = router

    svc = WizardService.__new__(WizardService)
    svc._db_path = db_path
    svc._correction_tasks = {}
    svc._scheduler = MagicMock()
    svc._output_router = router
    svc._bot = bot
    svc.recover_correction_timeouts = AsyncMock(return_value=None)  # type: ignore[method-assign]
    armed: list[str] = []
    svc._arm_inactivity_job = AsyncMock(  # type: ignore[method-assign]
        side_effect=lambda uid, _at: armed.append(str(uid))
    )
    expired: list[str] = []
    expiring = svc.handle_inactivity_timeout

    async def _expire(uid: str) -> None:
        expired.append(str(uid))
        await expiring(uid)

    svc.handle_inactivity_timeout = _expire  # type: ignore[method-assign]
    held: list[str] = []
    if not refusing:
        async def _hold(_uid, _guild, notice, **_kwargs):
            held.append(notice)
            return HoldOutcome(CHANNEL_ID, posted=True, locked=True)

        svc.trigger_channel_hold = AsyncMock(side_effect=_hold)  # type: ignore[method-assign]
    return SimpleNamespace(
        svc=svc, bot=bot, guild=guild, channel=channel, router=router, db_path=db_path,
        armed=armed, expired=expired, held=held,
    )


async def _restarted(ctx) -> None:
    """The bot started again: its wizards recovered, and any expiry it fires run out."""
    before = asyncio.all_tasks()
    await ctx.svc.recover_wizards()
    await asyncio.gather(*(asyncio.all_tasks() - before - {asyncio.current_task()}))


def _lines(ctx) -> list[str]:
    return [c.args[0] for c in ctx.router.post_log.await_args_list]


@pytest.mark.xfail(
    strict=True,
    reason="#439 slice 5: a withdrawal leaves its wizard engaged, for a restart to expire",
)
@pytest.mark.parametrize("hours", [1, 30], ids=["the deadline still ahead", "the deadline passed"])
async def test_a_restart_within_a_day_after_a_withdrawal_tells_nobody_their_session_expired(
    tmp_path, hours,
):
    """Driver 7 withdraws from the wizard, their last answer *hours* ago, and the bot restarts.
    Their signup has ended, so the restart arms no inactivity job for them, fires no expiry and
    posts nothing: neither "⏰ … expired" in their channel nor a line in the log."""
    ctx = await _real_signup(tmp_path, hours_since_activity=hours)
    assert await ctx.svc.withdraw(DRIVER_ID, ctx.guild) is None
    assert _lines(ctx) == [_WITHDRAWN]
    ctx.held.clear()

    await _restarted(ctx)

    assert ctx.armed == []
    assert ctx.expired == []
    assert ctx.held == []
    assert _lines(ctx) == [_WITHDRAWN]


@pytest.mark.xfail(
    strict=True, reason="#439 slice 5: a lapse leaves its wizard engaged, for a restart to expire"
)
async def test_a_restart_after_a_lapse_tells_nobody_again(tmp_path):
    """Driver 7's wizard lapses: 25 hours after their last answer the inactivity job fires,
    returning them to Not Signed Up, telling them their session expired and recording the lapse.
    The bot then restarts: it fires no second expiry, arms nothing, and posts nothing more."""
    ctx = await _real_signup(tmp_path, hours_since_activity=25)
    await ctx.svc.handle_inactivity_timeout(DRIVER_ID)
    assert len(ctx.held) == 1 and "expired" in ctx.held[0]
    lapsed = _lines(ctx)
    ctx.held.clear()
    ctx.expired.clear()

    await _restarted(ctx)

    assert ctx.expired == []
    assert ctx.armed == []
    assert ctx.held == []
    assert _lines(ctx) == lapsed


@pytest.mark.xfail(
    strict=True,
    reason="#439 slice 5: a withdrawal leaves its wizard engaged, for a restart to expire",
)
async def test_a_restart_after_a_refused_withdrawal_notice_posts_nothing_and_writes_no_new_line(
    tmp_path,
):
    """Driver 7, their last answer 30 hours ago, withdraws; Discord refuses the closing notice,
    so the channel is kept, readable with their typing locked, and the "Withdrawn" line names it
    (P3). The bot then starts twice. Neither start tries an expiry notice in the kept channel,
    and neither writes a "Channel kept" line: the "Withdrawn" line stands alone in the log."""
    ctx = await _real_signup(tmp_path, hours_since_activity=30, refusing=True)
    assert await ctx.svc.withdraw(DRIVER_ID, ctx.guild) is None
    [withdrawn] = _lines(ctx)
    assert withdrawn.startswith(_WITHDRAWN)

    await _restarted(ctx)
    await _restarted(ctx)

    assert ctx.expired == []
    assert ctx.channel.send.await_count == 1
    assert "expired" not in str(ctx.channel.send.await_args_list)
    assert _lines(ctx) == [withdrawn]


@pytest.mark.xfail(
    strict=True,
    reason="#439 slice 5: a queue close leaves its returned drivers' wizards engaged",
)
async def test_a_driver_a_queue_close_returned_is_not_expired_at_restart_however_late(tmp_path):
    """The signup window open with driver 7 part-way through the wizard, their last answer two
    days ago. The change queue's close (`execute_forced_close(hold_channels=False)`) returns
    them to Not Signed Up, leaving their notice to the queue's own jobs. The bot then restarts:
    it fires no expiry for them and posts no "⏰" notice, so the queue's notice is the one they
    are told."""
    from leaguebot.core.cogs.module_cog import execute_forced_close
    from leaguebot.core.db.database import get_connection

    ctx = await _real_signup(tmp_path, hours_since_activity=48)
    async with get_connection(ctx.db_path) as db:
        await db.execute(
            "INSERT INTO signup_module_config (id, signup_channel_id, signups_open, "
            "signup_button_message_id) VALUES (1, 700, 1, 8800)"
        )
        await db.commit()
    ctx.bot.wizard_service = ctx.svc

    outcome = await execute_forced_close(ctx.bot, audit_action="X", hold_channels=False)
    assert [str(uid) for uid in outcome.returned_ids] == [DRIVER_ID]

    await _restarted(ctx)

    assert ctx.expired == []
    assert not any("⏰" in notice for notice in ctx.held)
    assert ctx.armed == []
