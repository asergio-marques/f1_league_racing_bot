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
import os
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

SERVER_ID = 1
DRIVER_ID = "7"
CHANNEL_ID = 99


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _wizard(state=None, *, channel_id: int | None = CHANNEL_ID, last_activity: str | None = None):
    from models.signup_module import ConfigSnapshot, SignupWizardRecord, WizardState

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
    from services.wizard_service import WizardService

    svc = WizardService.__new__(WizardService)
    svc._correction_tasks = {}

    svc._cancel_inactivity_job = AsyncMock(return_value=None)  # type: ignore[method-assign]
    svc._cancel_channel_delete_job = AsyncMock(return_value=None)  # type: ignore[method-assign]
    svc._arm_inactivity_job = AsyncMock(return_value=None)  # type: ignore[method-assign]
    svc._trigger_channel_hold = AsyncMock(return_value=None)  # type: ignore[method-assign]
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

    return SimpleNamespace(
        svc=svc,
        guild=guild,
        channel=channel,
        signup_svc=signup_svc,
        driver_service=driver_service,
    )


def _transitioned_to(ctx) -> list[str]:
    return [
        call.args[2].value if hasattr(call.args[2], "value") else str(call.args[2])
        for call in ctx.driver_service.transition.await_args_list
    ]


# ---------------------------------------------------------------------------
# Withdrawal
# ---------------------------------------------------------------------------


async def test_withdrawing_returns_the_driver_to_not_signed_up(lifecycle):
    await lifecycle.svc.withdraw(SERVER_ID, DRIVER_ID, lifecycle.guild)

    assert "NOT_SIGNED_UP" in _transitioned_to(lifecycle)


async def test_withdrawing_holds_the_channel_rather_than_deleting_it(lifecycle):
    """The driver is still here and is being told their signup has ended."""
    await lifecycle.svc.withdraw(SERVER_ID, DRIVER_ID, lifecycle.guild)

    lifecycle.svc._trigger_channel_hold.assert_awaited_once()
    assert "cancelled" in lifecycle.svc._trigger_channel_hold.await_args.args[3]
    lifecycle.channel.delete.assert_not_awaited()


async def test_withdrawing_cancels_the_inactivity_job(lifecycle):
    """Left armed it would fire against a driver who has already withdrawn."""
    await lifecycle.svc.withdraw(SERVER_ID, DRIVER_ID, lifecycle.guild)

    lifecycle.svc._cancel_inactivity_job.assert_awaited_once()


async def test_withdrawing_cancels_a_pending_correction_window(lifecycle):
    task = MagicMock()
    lifecycle.svc._correction_tasks[(SERVER_ID, DRIVER_ID)] = task

    await lifecycle.svc.withdraw(SERVER_ID, DRIVER_ID, lifecycle.guild)

    task.cancel.assert_called_once()
    assert (SERVER_ID, DRIVER_ID) not in lifecycle.svc._correction_tasks


async def test_a_failed_transition_does_not_stop_the_withdrawal(lifecycle):
    """The driver may already be NOT_SIGNED_UP. They must still be told."""
    lifecycle.driver_service.transition = AsyncMock(side_effect=ValueError("already"))

    await lifecycle.svc.withdraw(SERVER_ID, DRIVER_ID, lifecycle.guild)

    lifecycle.svc._trigger_channel_hold.assert_awaited_once()


# ---------------------------------------------------------------------------
# The inactivity timeout
# ---------------------------------------------------------------------------


async def test_a_timed_out_driver_returns_to_not_signed_up(lifecycle):
    await lifecycle.svc.handle_inactivity_timeout(SERVER_ID, DRIVER_ID)

    assert "NOT_SIGNED_UP" in _transitioned_to(lifecycle)


async def test_a_timed_out_driver_is_told_why_in_their_channel(lifecycle):
    await lifecycle.svc.handle_inactivity_timeout(SERVER_ID, DRIVER_ID)

    assert "expired" in lifecycle.svc._trigger_channel_hold.await_args.args[3]


async def test_a_timeout_for_a_guild_the_bot_has_left_still_ends_the_signup(lifecycle):
    """The job outlives the bot's membership of the server. The driver state is the
    league's record and must be put right even where no channel can be held."""
    lifecycle.svc._bot.get_guild = MagicMock(return_value=None)

    await lifecycle.svc.handle_inactivity_timeout(SERVER_ID, DRIVER_ID)

    assert "NOT_SIGNED_UP" in _transitioned_to(lifecycle)
    lifecycle.svc._trigger_channel_hold.assert_not_awaited()


async def test_a_timeout_cancels_a_pending_correction_window(lifecycle):
    task = MagicMock()
    lifecycle.svc._correction_tasks[(SERVER_ID, DRIVER_ID)] = task

    await lifecycle.svc.handle_inactivity_timeout(SERVER_ID, DRIVER_ID)

    task.cancel.assert_called_once()


# ---------------------------------------------------------------------------
# A driver leaving the server
# ---------------------------------------------------------------------------


async def test_a_driver_part_way_through_the_questions_is_cleaned_up(lifecycle):
    await lifecycle.svc.handle_member_remove(SERVER_ID, DRIVER_ID, lifecycle.guild)

    assert "NOT_SIGNED_UP" in _transitioned_to(lifecycle)
    lifecycle.signup_svc.delete_wizard.assert_awaited_once()


async def test_a_driver_waiting_on_a_manager_is_cleaned_up_too(lifecycle):
    """Their wizard is UNENGAGED, so the wizard state says nothing — the *driver* state is
    where the open signup lives. This is the half easily missed."""
    from models.driver_profile import DriverState
    from models.signup_module import WizardState

    lifecycle.signup_svc.get_wizard = AsyncMock(
        return_value=_wizard(WizardState.UNENGAGED)
    )
    lifecycle.driver_service.get_profile = AsyncMock(
        return_value=SimpleNamespace(current_state=DriverState.PENDING_ADMIN_APPROVAL)
    )

    await lifecycle.svc.handle_member_remove(SERVER_ID, DRIVER_ID, lifecycle.guild)

    assert "NOT_SIGNED_UP" in _transitioned_to(lifecycle)


async def test_a_driver_awaiting_a_correction_parameter_is_cleaned_up(lifecycle):
    from models.driver_profile import DriverState
    from models.signup_module import WizardState

    lifecycle.signup_svc.get_wizard = AsyncMock(
        return_value=_wizard(WizardState.UNENGAGED)
    )
    lifecycle.driver_service.get_profile = AsyncMock(
        return_value=SimpleNamespace(
            current_state=DriverState.AWAITING_CORRECTION_PARAMETER
        )
    )

    await lifecycle.svc.handle_member_remove(SERVER_ID, DRIVER_ID, lifecycle.guild)

    assert "NOT_SIGNED_UP" in _transitioned_to(lifecycle)


async def test_a_member_who_was_never_signing_up_is_left_alone(lifecycle):
    """Most people leaving a server have no signup at all. Transitioning them would write
    a driver state for somebody who never had one."""
    from models.signup_module import WizardState

    lifecycle.signup_svc.get_wizard = AsyncMock(
        return_value=_wizard(WizardState.UNENGAGED)
    )
    lifecycle.driver_service.get_profile = AsyncMock(return_value=None)

    await lifecycle.svc.handle_member_remove(SERVER_ID, DRIVER_ID, lifecycle.guild)

    lifecycle.driver_service.transition.assert_not_awaited()
    lifecycle.signup_svc.delete_wizard.assert_not_awaited()


async def test_an_approved_driver_leaving_is_left_alone(lifecycle):
    """An UNASSIGNED driver has finished signing up; there is no wizard to clean up and
    their record is the league's to keep."""
    from models.driver_profile import DriverState
    from models.signup_module import WizardState

    lifecycle.signup_svc.get_wizard = AsyncMock(
        return_value=_wizard(WizardState.UNENGAGED)
    )
    lifecycle.driver_service.get_profile = AsyncMock(
        return_value=SimpleNamespace(current_state=DriverState.UNASSIGNED)
    )

    await lifecycle.svc.handle_member_remove(SERVER_ID, DRIVER_ID, lifecycle.guild)

    lifecycle.driver_service.transition.assert_not_awaited()


async def test_a_departing_driver_s_channel_is_deleted_rather_than_held(lifecycle):
    """The distinction from every other ending. Nobody will read it, and a private channel
    for a departed member is clutter the league clears by hand."""
    await lifecycle.svc.handle_member_remove(SERVER_ID, DRIVER_ID, lifecycle.guild)

    lifecycle.channel.delete.assert_awaited_once()
    lifecycle.svc._trigger_channel_hold.assert_not_awaited()


async def test_a_channel_that_cannot_be_deleted_does_not_stop_the_cleanup(lifecycle):
    lifecycle.channel.delete = AsyncMock(
        side_effect=discord.HTTPException(MagicMock(), "forbidden")
    )

    await lifecycle.svc.handle_member_remove(SERVER_ID, DRIVER_ID, lifecycle.guild)

    lifecycle.signup_svc.delete_wizard.assert_awaited_once()


async def test_a_departure_cancels_both_scheduled_jobs(lifecycle):
    """A wizard may have left an inactivity job *and* a channel-delete job."""
    await lifecycle.svc.handle_member_remove(SERVER_ID, DRIVER_ID, lifecycle.guild)

    lifecycle.svc._cancel_inactivity_job.assert_awaited_once()
    lifecycle.svc._cancel_channel_delete_job.assert_awaited_once()


async def test_the_departure_log_names_the_driver_and_the_state_they_were_in(lifecycle):
    """The state is captured before the cleanup, so the log says what the driver was doing
    rather than the NOT_SIGNED_UP they were left in."""
    await lifecycle.svc.handle_member_remove(SERVER_ID, DRIVER_ID, lifecycle.guild)

    logged = lifecycle.svc._output_router.post_log.await_args.args[1]
    assert "Lewis Hamilton" in logged
    assert "COLLECTING_PLATFORM" in logged


async def test_a_driver_with_no_record_is_logged_by_id(lifecycle):
    lifecycle.signup_svc.get_record = AsyncMock(return_value=None)

    await lifecycle.svc.handle_member_remove(SERVER_ID, DRIVER_ID, lifecycle.guild)

    assert DRIVER_ID in lifecycle.svc._output_router.post_log.await_args.args[1]


async def test_a_failing_record_lookup_does_not_stop_the_log(lifecycle):
    """The log is the only trace a departure leaves; losing it to a lookup failure would
    leave the league with no record of why a driver vanished."""
    lifecycle.signup_svc.get_record = AsyncMock(side_effect=RuntimeError("db gone"))

    await lifecycle.svc.handle_member_remove(SERVER_ID, DRIVER_ID, lifecycle.guild)

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
    fire_at = lifecycle.svc._arm_inactivity_job.await_args.args[2]
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
        side_effect=lambda s, u: expired.append((s, u))
    )

    await lifecycle.svc.recover_wizards()
    await asyncio.sleep(0)  # let the background task run

    lifecycle.svc._arm_inactivity_job.assert_not_awaited()
    assert expired == [(SERVER_ID, DRIVER_ID)]


async def test_a_wizard_with_no_recorded_activity_expires_at_once(lifecycle):
    """There is no deadline to compute, and giving it a fresh one would reward the gap."""
    lifecycle.signup_svc.get_all_active_wizards_all_servers = AsyncMock(
        return_value=[_wizard(last_activity=None)]
    )
    expired: list = []
    lifecycle.svc.handle_inactivity_timeout = AsyncMock(  # type: ignore[method-assign]
        side_effect=lambda s, u: expired.append((s, u))
    )

    await lifecycle.svc.recover_wizards()
    await asyncio.sleep(0)

    assert expired == [(SERVER_ID, DRIVER_ID)]


async def test_a_parked_wizard_is_not_re_armed(lifecycle):
    """An UNENGAGED wizard belongs to a driver waiting on a manager, who cannot be timed
    out for the manager's delay."""
    from models.signup_module import WizardState

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

    fire_at = lifecycle.svc._arm_inactivity_job.await_args.args[2]
    assert fire_at.tzinfo is not None
    assert fire_at == last.replace(tzinfo=timezone.utc) + timedelta(hours=24)


async def test_recovery_releases_pending_correction_windows_first(lifecycle):
    """The five-minute window is an in-memory task and cannot survive a restart, so the
    drivers holding one are released before anything else is considered."""
    await lifecycle.svc.recover_wizards()

    lifecycle.svc.recover_correction_timeouts.assert_awaited_once()
