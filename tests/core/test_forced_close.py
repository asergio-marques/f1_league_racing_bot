"""Force-closing the signup window, as the module disable and the close timer both do.

Issue #208. `execute_forced_close` is patched out everywhere it is called from, and was
therefore never itself run. It is the shared sub-flow behind `/module disable signup`, the
scheduled close and `/signup close`, and it decides what happens to drivers caught mid-signup.

**Only a driver still filling in the wizard is turned away.** `PENDING_SIGNUP_COMPLETION` is the
one state transitioned back to Not Signed Up. A driver waiting on a manager's approval, or
correcting an answer a manager asked about, has finished signing up as far as they are
concerned, and closing the window must not undo that (FR-002, FR-003).
`test_a_driver_awaiting_approval_is_left_alone` is the one that holds it. The close returns how
many it turned away, counting only transitions that succeeded, so the confirm button can report
what actually happened (issue #128).

**A turned-away driver is told, and their jobs go with them.** Their wizard's inactivity and
channel-delete jobs are removed — left armed they would fire against a signup that has ended —
and their channel is held for 24 hours with a notice, the same shape as a withdrawal.

**The button goes, and a notice replaces it.** The closed message's id is stored with the
window, so re-opening signups can delete it rather than leave "Signups are now closed" standing
under a working button.

**Each Discord step is independent of the others.** A button already deleted, a channel that
refuses a post, a transition that fails — each is logged and stepped over, because the window
has to end up closed and audited whatever Discord makes of it.
"""
from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from leaguebot.core.cogs.module_cog import execute_forced_close
from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.models.driver_profile import DriverState

SERVER_ID = 13308
SIGNUP_CHANNEL = 700
BUTTON_MESSAGE = 8800
CLOSED_MESSAGE = 9900


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _make_db(tmp_path, *, name="forced_close", drivers=()):
    """*drivers* are ``(discord_user_id, state)``."""
    db_path = os.path.join(str(tmp_path), f"{name}.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 900, 100, 101)",
            (SERVER_ID,),
        )
        for uid, state in drivers:
            await db.execute(
                "INSERT INTO driver_profiles (discord_user_id, current_state) "
                "VALUES (?, ?)",
                (uid, state.value),
            )
        await db.commit()
    return db_path


def _channel(*, fetch_error=None, send_error=None):
    channel = MagicMock()
    button = MagicMock()
    button.delete = AsyncMock()
    channel.fetch_message = AsyncMock(return_value=button, side_effect=fetch_error)
    closed = MagicMock()
    closed.id = CLOSED_MESSAGE
    channel.send = AsyncMock(return_value=closed, side_effect=send_error)
    channel._button = button
    return channel


_UNSET = object()


def _bot(db_path, *, config=_UNSET, channel=None, guild=True, transition_error=None):
    bot = MagicMock()
    bot.config_service.get_league_server_id = AsyncMock(return_value=SERVER_ID)
    bot.db_path = db_path
    bot.signup_module_service = MagicMock()
    bot.signup_module_service.get_config = AsyncMock(
        return_value=SimpleNamespace(
            signup_channel_id=SIGNUP_CHANNEL, signup_button_message_id=BUTTON_MESSAGE
        )
        if config is _UNSET
        else config
    )
    bot.signup_module_service.set_window_closed = AsyncMock()
    bot.driver_service = MagicMock()
    bot.driver_service.transition = AsyncMock(side_effect=transition_error)
    bot.scheduler_service = MagicMock()
    bot.scheduler_service._scheduler = MagicMock()
    bot.wizard_service = MagicMock()
    bot.wizard_service._trigger_channel_hold = AsyncMock()
    channel = channel if channel is not None else _channel()
    g = MagicMock()
    g.get_channel = MagicMock(return_value=channel)
    bot.get_guild = MagicMock(return_value=g if guild else None)
    bot._channel = channel
    return bot


async def _audit(db_path):
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT change_type, old_value, new_value, actor_name FROM audit_entries"
        )
        return [tuple(r) for r in await cursor.fetchall()]


# ---------------------------------------------------------------------------
# Drivers mid-signup
# ---------------------------------------------------------------------------


async def test_a_driver_still_filling_in_the_wizard_is_turned_away(tmp_path):
    db_path = await _make_db(
        tmp_path, drivers=[("101", DriverState.PENDING_SIGNUP_COMPLETION)]
    )
    bot = _bot(db_path)

    await execute_forced_close(bot, audit_action="SIGNUP_FORCE_CLOSE")

    bot.driver_service.transition.assert_awaited_once_with(
        "101", DriverState.NOT_SIGNED_UP
    )


@pytest.mark.parametrize(
    "state",
    [
        DriverState.PENDING_ADMIN_APPROVAL,
        DriverState.AWAITING_CORRECTION_PARAMETER,
        DriverState.PENDING_DRIVER_CORRECTION,
        DriverState.UNASSIGNED,
    ],
)
async def test_a_driver_awaiting_approval_is_left_alone(tmp_path, state):
    """FR-002/FR-003: they have finished signing up as far as they are concerned, and
    closing the window must not undo that."""
    db_path = await _make_db(tmp_path, name=f"fc_{state.value}", drivers=[("101", state)])
    bot = _bot(db_path)

    await execute_forced_close(bot, audit_action="SIGNUP_FORCE_CLOSE")

    bot.driver_service.transition.assert_not_awaited()
    bot.wizard_service._trigger_channel_hold.assert_not_awaited()


async def test_a_turned_away_drivers_jobs_are_removed(tmp_path):
    """Left armed they would fire against a signup that has already ended. Removed through the
    scheduler service's `cancel_job`: nothing but the scheduler service removes a job."""
    db_path = await _make_db(
        tmp_path, name="fc_jobs", drivers=[("101", DriverState.PENDING_SIGNUP_COMPLETION)]
    )
    bot = _bot(db_path)

    await execute_forced_close(bot, audit_action="X")

    removed = {c.args[0] for c in bot.scheduler_service.cancel_job.call_args_list}
    assert removed == {
        f"wizard_inactivity_101",
        f"wizard_channel_delete_101",
    }


async def test_a_job_already_gone_is_stepped_over(tmp_path):
    from apscheduler.jobstores.base import JobLookupError

    from leaguebot.core.services.scheduler_service import SchedulerService

    # The real scheduler service, over an APScheduler that no longer holds the job.
    scheduler = SchedulerService.__new__(SchedulerService)
    scheduler._scheduler = MagicMock()
    scheduler._scheduler.remove_job = MagicMock(side_effect=JobLookupError("no job"))

    db_path = await _make_db(
        tmp_path, name="fc_nojob", drivers=[("101", DriverState.PENDING_SIGNUP_COMPLETION)]
    )
    bot = _bot(db_path)
    bot.scheduler_service = scheduler

    await execute_forced_close(bot, audit_action="X")

    bot.signup_module_service.set_window_closed.assert_awaited_once()


async def test_a_turned_away_driver_is_told_and_their_channel_held(tmp_path):
    """The same shape as a withdrawal: a notice, and the channel deleted in 24 hours."""
    db_path = await _make_db(
        tmp_path, name="fc_hold", drivers=[("101", DriverState.PENDING_SIGNUP_COMPLETION)]
    )
    bot = _bot(db_path)

    await execute_forced_close(bot, audit_action="X")

    hold = bot.wizard_service._trigger_channel_hold.await_args
    assert hold.args[0] == "101"
    assert "Signups have closed" in hold.args[2]


_OUTCOME = "#482: the forced close returns a bare count and swallows every failed step"


@pytest.mark.xfail(strict=True, reason=_OUTCOME)
async def test_a_failing_transition_does_not_stop_the_close(tmp_path):
    """Driver 101 is still filling in the wizard and their transition fails with an error other
    than the expected refusal: the window still closes, and the outcome names driver 101 as a
    failed step, not returned (#457)."""
    db_path = await _make_db(
        tmp_path, name="fc_transfail", drivers=[("101", DriverState.PENDING_SIGNUP_COMPLETION)]
    )
    bot = _bot(db_path, transition_error=RuntimeError("db locked"))

    outcome = await execute_forced_close(bot, audit_action="X")

    bot.signup_module_service.set_window_closed.assert_awaited_once()
    assert outcome.returned == 0
    [failed] = outcome.failed
    assert "101" in failed


@pytest.mark.xfail(strict=True, reason=_OUTCOME)
async def test_a_driver_already_moved_on_is_neither_counted_nor_a_failure(tmp_path):
    """Driver 101's transition is refused as the state machine refuses a driver who has moved
    on since the close read them (`ValueError`): the window closes, they are not counted as
    returned, and nothing is reported as failed (#457)."""
    db_path = await _make_db(
        tmp_path, name="fc_movedon", drivers=[("101", DriverState.PENDING_SIGNUP_COMPLETION)]
    )
    bot = _bot(db_path, transition_error=ValueError("not in PENDING_SIGNUP_COMPLETION"))

    outcome = await execute_forced_close(bot, audit_action="X")

    bot.signup_module_service.set_window_closed.assert_awaited_once()
    assert outcome.returned == 0
    assert list(outcome.failed) == []


@pytest.mark.xfail(strict=True, reason=_OUTCOME)
async def test_a_failing_channel_hold_does_not_stop_the_close(tmp_path):
    """Driver 101 is returned, but their channel cannot be given its notice: the window still
    closes, 101 is counted, and the outcome names 101 as a driver not told."""
    db_path = await _make_db(
        tmp_path, name="fc_holdfail", drivers=[("101", DriverState.PENDING_SIGNUP_COMPLETION)]
    )
    bot = _bot(db_path)
    bot.wizard_service._trigger_channel_hold = AsyncMock(side_effect=RuntimeError("gone"))

    outcome = await execute_forced_close(bot, audit_action="X")

    bot.signup_module_service.set_window_closed.assert_awaited_once()
    assert outcome.returned == 1
    [failed] = outcome.failed
    assert "101" in failed


@pytest.mark.xfail(strict=True, reason=_OUTCOME)
async def test_the_close_returns_how_many_drivers_it_turned_away(tmp_path):
    """The confirm button reports this number (issue #128). A driver awaiting approval is not
    turned away, so is not counted."""
    db_path = await _make_db(
        tmp_path,
        name="fc_count",
        drivers=[
            ("101", DriverState.PENDING_SIGNUP_COMPLETION),
            ("102", DriverState.PENDING_SIGNUP_COMPLETION),
            ("103", DriverState.PENDING_ADMIN_APPROVAL),
        ],
    )

    outcome = await execute_forced_close(_bot(db_path), audit_action="X")

    assert outcome.returned == 2
    assert list(outcome.failed) == []


@pytest.mark.xfail(strict=True, reason=_OUTCOME)
async def test_a_driver_whose_transition_failed_is_not_counted(tmp_path):
    """They are still mid-signup, so saying they were turned away would be false."""
    db_path = await _make_db(
        tmp_path,
        name="fc_count_fail",
        drivers=[("101", DriverState.PENDING_SIGNUP_COMPLETION)],
    )
    bot = _bot(db_path, transition_error=RuntimeError("db locked"))

    assert (await execute_forced_close(bot, audit_action="X")).returned == 0


# ---------------------------------------------------------------------------
# The window itself
# ---------------------------------------------------------------------------


async def test_the_signup_button_is_deleted(tmp_path):
    db_path = await _make_db(tmp_path, name="fc_button")
    bot = _bot(db_path)

    await execute_forced_close(bot, audit_action="X")

    bot._channel.fetch_message.assert_awaited_once_with(BUTTON_MESSAGE)
    bot._channel._button.delete.assert_awaited_once()


@pytest.mark.parametrize(
    "error, failed",
    [
        pytest.param(
            discord.NotFound(MagicMock(status=404), "gone"),
            False,
            marks=pytest.mark.xfail(strict=True, reason=_OUTCOME),
            id="already gone",
        ),
        pytest.param(
            RuntimeError("forbidden"),
            True,
            marks=pytest.mark.xfail(strict=True, reason=_OUTCOME),
            id="not removed",
        ),
    ],
)
async def test_a_button_that_cannot_be_deleted_does_not_stop_the_close(tmp_path, error, failed):
    """The Sign Up button message cannot be deleted: the window still closes. A button already
    gone is what the close wanted and fails nothing; one that stays is a failed step naming the
    Sign Up button."""
    db_path = await _make_db(tmp_path, name=f"fc_buttonfail_{type(error).__name__}")
    bot = _bot(db_path, channel=_channel(fetch_error=error))

    outcome = await execute_forced_close(bot, audit_action="X")

    bot.signup_module_service.set_window_closed.assert_awaited_once()
    if failed:
        [step] = outcome.failed
        assert "Sign Up button" in step
    else:
        assert list(outcome.failed) == []


async def test_a_window_with_no_button_deletes_nothing(tmp_path):
    db_path = await _make_db(tmp_path, name="fc_nobutton")
    bot = _bot(
        db_path,
        config=SimpleNamespace(signup_channel_id=SIGNUP_CHANNEL, signup_button_message_id=None),
    )

    await execute_forced_close(bot, audit_action="X")

    bot._channel.fetch_message.assert_not_awaited()


async def test_a_closed_notice_is_posted_and_its_id_kept(tmp_path):
    """So re-opening can delete it rather than leave it standing under a working button."""
    db_path = await _make_db(tmp_path, name="fc_notice")
    bot = _bot(db_path)

    await execute_forced_close(bot, audit_action="X")

    assert "Signups are now closed" in str(bot._channel.send.await_args.args[0])
    bot.signup_module_service.set_window_closed.assert_awaited_once_with(
        closed_msg_id=CLOSED_MESSAGE
    )


@pytest.mark.xfail(strict=True, reason=_OUTCOME)
async def test_a_notice_that_cannot_be_posted_still_closes_the_window(tmp_path):
    """The signup channel refuses the "Signups are now closed" notice: the window still closes,
    with no notice id kept, and the outcome names the notice as a failed step."""
    db_path = await _make_db(tmp_path, name="fc_noticefail")
    bot = _bot(db_path, channel=_channel(send_error=RuntimeError("forbidden")))

    outcome = await execute_forced_close(bot, audit_action="X")

    bot.signup_module_service.set_window_closed.assert_awaited_once_with(
        closed_msg_id=None
    )
    [step] = outcome.failed
    assert "notice" in step


@pytest.mark.xfail(strict=True, reason=_OUTCOME)
async def test_a_guild_the_bot_has_left_still_closes_the_window(tmp_path):
    """The league's server cannot be reached: the window still closes, and the outcome names
    both the Sign Up button left standing and the notice not posted as failed steps."""
    db_path = await _make_db(tmp_path, name="fc_noguild")
    bot = _bot(db_path, guild=False)

    outcome = await execute_forced_close(bot, audit_action="X")

    bot.signup_module_service.set_window_closed.assert_awaited_once_with(
        closed_msg_id=None
    )
    assert any("Sign Up button" in step for step in outcome.failed)
    assert any("notice" in step for step in outcome.failed)


@pytest.mark.xfail(strict=True, reason=_OUTCOME)
async def test_a_season_that_cannot_be_moved_on_is_a_failed_step(tmp_path, monkeypatch):
    """Moving the season on after the close fails: the window still closes and is audited, and
    the outcome names the season as a failed step."""
    from leaguebot.core.services import season_lifecycle_service

    monkeypatch.setattr(
        season_lifecycle_service,
        "advance_on_window_close",
        AsyncMock(side_effect=RuntimeError("database is locked")),
    )
    db_path = await _make_db(tmp_path, name="fc_season")
    bot = _bot(db_path)

    outcome = await execute_forced_close(bot, audit_action="X")

    bot.signup_module_service.set_window_closed.assert_awaited_once()
    assert await _audit(db_path) != []
    [step] = outcome.failed
    assert "season" in step


async def test_the_close_is_audited_under_the_callers_action(tmp_path):
    """The same sub-flow serves three callers, and the audit says which one closed it."""
    db_path = await _make_db(tmp_path, name="fc_audit")

    await execute_forced_close(_bot(db_path), audit_action="SIGNUP_TIMER_CLOSE")

    assert await _audit(db_path) == [("SIGNUP_TIMER_CLOSE", "open", "closed", "system")]


async def test_a_server_with_no_signup_configuration_does_nothing(tmp_path):
    db_path = await _make_db(tmp_path, name="fc_noconfig")
    bot = _bot(db_path, config=None)

    assert await execute_forced_close(bot, audit_action="X") == 0

    bot.signup_module_service.set_window_closed.assert_not_awaited()
    assert await _audit(db_path) == []


# ---------------------------------------------------------------------------
# A close given the window it was asked about (#491)
# ---------------------------------------------------------------------------

_STALE_WINDOW = "#482: execute_forced_close is given no window and re-checks nothing (#491)"

ARMED = "2099-06-15T20:00:00+00:00"


def _window(*, signups_open=True, button=BUTTON_MESSAGE, close_at=None):
    """The signup configuration as the close re-reads it."""
    return SimpleNamespace(
        signup_channel_id=SIGNUP_CHANNEL,
        signup_button_message_id=button,
        signups_open=signups_open,
        close_at=close_at,
    )


@pytest.mark.xfail(strict=True, reason=_STALE_WINDOW)
@pytest.mark.parametrize(
    "config, refusal",
    [
        pytest.param(
            _window(signups_open=False, button=None),
            ("Signups are no longer open. Nothing was closed.",),
            id="closed since",
        ),
        pytest.param(
            _window(button=BUTTON_MESSAGE + 1),
            ("Signups were reopened since this was asked. Nothing was closed.",),
            id="reopened since",
        ),
        pytest.param(
            _window(close_at=ARMED),
            ("auto-close", "<t:", "`/signup close-time cancel`"),
            id="close time armed since",
        ),
    ],
)
async def test_a_close_given_a_window_that_has_changed_refuses_and_touches_nothing(
    tmp_path, config, refusal
):
    """A driver is still filling in the wizard. The close is given the Sign Up button message of
    the window a manager was asked about, but the window has since been closed, reopened on a
    new button, or given a close time. The close returns the refusal's reason and touches
    nothing: no driver returned, no job removed, no channel held, no button deleted, no notice
    posted, the window not closed, and nothing audited."""
    db_path = await _make_db(
        tmp_path, drivers=[("101", DriverState.PENDING_SIGNUP_COMPLETION)]
    )
    bot = _bot(db_path, config=config)
    bot.wizard_service.trigger_channel_hold = AsyncMock()

    outcome = await execute_forced_close(
        bot, audit_action="SIGNUP_FORCE_CLOSE", window=BUTTON_MESSAGE
    )

    for text in refusal:
        assert text in outcome.refused
    bot.driver_service.transition.assert_not_awaited()
    bot.scheduler_service.cancel_job.assert_not_called()
    bot.wizard_service.trigger_channel_hold.assert_not_awaited()
    bot._channel.fetch_message.assert_not_awaited()
    bot._channel.send.assert_not_awaited()
    bot.signup_module_service.set_window_closed.assert_not_awaited()
    assert await _audit(db_path) == []


@pytest.mark.xfail(strict=True, reason=_STALE_WINDOW)
async def test_a_close_given_the_window_still_open_closes_it(tmp_path):
    """The window the manager was asked about is still open on the same button, with no close
    time armed: the close goes ahead, refuses nothing, and returns the driver it turned away."""
    db_path = await _make_db(
        tmp_path, drivers=[("101", DriverState.PENDING_SIGNUP_COMPLETION)]
    )
    bot = _bot(db_path, config=_window())
    bot.wizard_service.trigger_channel_hold = AsyncMock()

    outcome = await execute_forced_close(
        bot, audit_action="SIGNUP_FORCE_CLOSE", window=BUTTON_MESSAGE
    )

    assert outcome.refused is None
    assert outcome.returned == 1
    bot.signup_module_service.set_window_closed.assert_awaited_once()
