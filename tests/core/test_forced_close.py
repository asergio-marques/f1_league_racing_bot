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
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from leaguebot.core.cogs.module_cog import execute_forced_close  # noqa: E402
from leaguebot.core.db.database import get_connection, run_migrations  # noqa: E402
from leaguebot.core.models.driver_profile import DriverState  # noqa: E402

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
    """Left armed they would fire against a signup that has already ended."""
    db_path = await _make_db(
        tmp_path, name="fc_jobs", drivers=[("101", DriverState.PENDING_SIGNUP_COMPLETION)]
    )
    bot = _bot(db_path)

    await execute_forced_close(bot, audit_action="X")

    removed = {c.args[0] for c in bot.scheduler_service._scheduler.remove_job.call_args_list}
    assert removed == {
        f"wizard_inactivity_101",
        f"wizard_channel_delete_101",
    }


async def test_a_job_already_gone_is_stepped_over(tmp_path):
    db_path = await _make_db(
        tmp_path, name="fc_nojob", drivers=[("101", DriverState.PENDING_SIGNUP_COMPLETION)]
    )
    bot = _bot(db_path)
    bot.scheduler_service._scheduler.remove_job = MagicMock(side_effect=Exception("no job"))

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


async def test_a_failing_transition_does_not_stop_the_close(tmp_path):
    db_path = await _make_db(
        tmp_path, name="fc_transfail", drivers=[("101", DriverState.PENDING_SIGNUP_COMPLETION)]
    )
    bot = _bot(db_path, transition_error=RuntimeError("db locked"))

    await execute_forced_close(bot, audit_action="X")

    bot.signup_module_service.set_window_closed.assert_awaited_once()


async def test_a_failing_channel_hold_does_not_stop_the_close(tmp_path):
    db_path = await _make_db(
        tmp_path, name="fc_holdfail", drivers=[("101", DriverState.PENDING_SIGNUP_COMPLETION)]
    )
    bot = _bot(db_path)
    bot.wizard_service._trigger_channel_hold = AsyncMock(side_effect=RuntimeError("gone"))

    await execute_forced_close(bot, audit_action="X")

    bot.signup_module_service.set_window_closed.assert_awaited_once()


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

    assert await execute_forced_close(_bot(db_path), audit_action="X") == 2


async def test_a_driver_whose_transition_failed_is_not_counted(tmp_path):
    """They are still mid-signup, so saying they were turned away would be false."""
    db_path = await _make_db(
        tmp_path,
        name="fc_count_fail",
        drivers=[("101", DriverState.PENDING_SIGNUP_COMPLETION)],
    )
    bot = _bot(db_path, transition_error=RuntimeError("db locked"))

    assert await execute_forced_close(bot, audit_action="X") == 0


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
    "error",
    [discord.NotFound(MagicMock(status=404), "gone"), RuntimeError("forbidden")],
)
async def test_a_button_that_cannot_be_deleted_does_not_stop_the_close(tmp_path, error):
    db_path = await _make_db(tmp_path, name=f"fc_buttonfail_{type(error).__name__}")
    bot = _bot(db_path, channel=_channel(fetch_error=error))

    await execute_forced_close(bot, audit_action="X")

    bot.signup_module_service.set_window_closed.assert_awaited_once()


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


async def test_a_notice_that_cannot_be_posted_still_closes_the_window(tmp_path):
    db_path = await _make_db(tmp_path, name="fc_noticefail")
    bot = _bot(db_path, channel=_channel(send_error=RuntimeError("forbidden")))

    await execute_forced_close(bot, audit_action="X")

    bot.signup_module_service.set_window_closed.assert_awaited_once_with(
        closed_msg_id=None
    )


async def test_a_guild_the_bot_has_left_still_closes_the_window(tmp_path):
    db_path = await _make_db(tmp_path, name="fc_noguild")
    bot = _bot(db_path, guild=False)

    await execute_forced_close(bot, audit_action="X")

    bot.signup_module_service.set_window_closed.assert_awaited_once_with(
        closed_msg_id=None
    )


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
