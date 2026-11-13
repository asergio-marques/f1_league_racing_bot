"""The close nobody ran: the signup window closed at its set time by the timer, or at start-up by
a restart past it.

Issue #208, #439 slice 6b, #283. Both ask the change queue's own close (`signup.close`, pinned in
`tests/signup/test_window_changes.py`) through `close_signups_unattended` with their own cause.
A season's end and every division done close the window through signup's own step on the
season's end change, pinned in the core season end tests; they no longer come through here.
"""
from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from leaguebot.core.db.database import get_connection, run_migrations

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


def _bot(db_path, *, config=_UNSET, channel=None, guild=True):
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
    bot.scheduler_service = MagicMock()
    bot.scheduler_service._scheduler = MagicMock()
    bot.wizard_service = MagicMock()
    channel = channel if channel is not None else _channel()
    g = MagicMock()
    g.get_channel = MagicMock(return_value=channel)
    bot.get_guild = MagicMock(return_value=g if guild else None)
    bot._channel = channel
    return bot


def _services_of(bot) -> dict:
    """The bot's services a close nobody ran is handed, by keyword."""
    return {
        "change_queue": bot.change_queue,
        "module_service": bot.module_service,
        "signup_module_service": bot.signup_module_service,
    }


def _window(*, signups_open=True, button=BUTTON_MESSAGE, close_at=None):
    """The signup configuration as the close re-reads it."""
    return SimpleNamespace(
        signup_channel_id=SIGNUP_CHANNEL,
        signup_button_message_id=button,
        signups_open=signups_open,
        close_at=close_at,
    )


# ---------------------------------------------------------------------------
# A close nobody ran: the timer and the restart sweep
# ---------------------------------------------------------------------------

#: The set time a timer closed at, already past.
SET_TIME = "2026-09-30T18:00:00+00:00"


def _unattended_bot(db_path, *, signups_open=True, enabled=True, channel=None):
    """A bot whose signup module is *enabled* or not, and whose window is open or not, with
    its close time set at `SET_TIME`, and a log channel that keeps what is posted to it."""
    bot = _bot(db_path, config=_window(signups_open=signups_open, close_at=SET_TIME), channel=channel)
    bot.module_service.is_signup_enabled = AsyncMock(return_value=enabled)
    bot.output_router.post_log = AsyncMock(return_value=None)
    return bot


@pytest.mark.parametrize("cause", ["timer", "restart"])
@pytest.mark.parametrize(
    "signups_open, enabled",
    [pytest.param(False, True, id="no window open"), pytest.param(True, False, id="module off")],
)
async def test_a_close_nobody_ran_that_closes_nothing_writes_no_line(
    tmp_path, cause, signups_open, enabled
):
    """A close nobody ran finds no window open, or the signup module disabled: it writes no
    line, since only a window actually closed is recorded and a disabled module produces
    nothing."""
    from leaguebot.signup.services.window_close import close_signups_unattended

    db_path = await _make_db(tmp_path)
    bot = _unattended_bot(db_path, signups_open=signups_open, enabled=enabled)

    await close_signups_unattended(**_services_of(bot), cause=cause)

    bot.output_router.post_log.assert_not_awaited()

