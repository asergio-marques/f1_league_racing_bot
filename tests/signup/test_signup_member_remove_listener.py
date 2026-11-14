"""What happens when a member leaves the server, from the signup cog's listener.

Issue #208. `SignupCog.on_member_remove` was untested; the clean-up it asks for,
`signup.member_left`, is tested in `test_wizard_changes.py`.

**Every departure is handed to the change queue, and the listener does nothing else.** A driver
halfway through signing up has a channel, jobs and a driver state to put right, and a placed or
approved driver leaving is a line in the log channel; both are the `signup.member_left` change's
work (#439, slice 6b, C80; #286) — the listener asks the queue for it, as the bot, for every
member, whoever they are, and the change's own check decides what there is to do. The line a
placed or approved driver's leaving writes is pinned in `test_wizard_changes.py`.

**The listener announces nobody itself.** A member who never signed up is not a league event,
and logging every departure from a large server would bury the ones that are.
"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock

import pytest

from leaguebot.signup.cogs.signup_cog import SignupCog
from leaguebot.core.db.database import get_connection, run_migrations

SERVER_ID = 14308
DRIVER = 4242


async def _make_db(tmp_path, *, name="member_remove", state=None, record=True):
    db_path = os.path.join(str(tmp_path), f"{name}.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 900, 100, 101)",
            (SERVER_ID,),
        )
        if state is not None:
            await db.execute(
                "INSERT INTO driver_profiles (discord_user_id, current_state) "
                "VALUES (?, ?)",
                (str(DRIVER), state),
            )
        if record:
            await db.execute(
                "INSERT INTO signup_records (created_at, discord_user_id, discord_username, "
                "server_display_name) VALUES ('2026-01-01T00:00:00+00:00', ?, 'racer', 'Racer One')",
                (str(DRIVER),),
            )
        await db.commit()
    return db_path


def _cog(db_path):
    bot = MagicMock()
    bot.db_path = db_path
    bot.wizard_service = MagicMock()
    bot.output_router = MagicMock()
    bot.output_router.post_log = AsyncMock()
    bot.change_queue = MagicMock()
    bot.change_queue.ask = AsyncMock(return_value=1)
    bot.config_service.get_league_server_id = AsyncMock(return_value=SERVER_ID)
    cog = SignupCog.__new__(SignupCog)
    cog.bot = bot
    return cog


def _member():
    member = MagicMock()
    member.id = DRIVER
    member.display_name = "DiscordName"
    member.guild = MagicMock()
    member.guild.id = SERVER_ID
    return member


async def _leave(cog):
    await SignupCog.on_member_remove(cog, _member())


MEMBER_LEFT_KIND = "signup.member_left"


def _asked_member_left(cog) -> list:
    """The `signup.member_left` changes the listener asked of the queue."""
    return [call for call in cog.bot.change_queue.ask.await_args_list
            if call.args and call.args[0] == MEMBER_LEFT_KIND]


def _logged(cog) -> str:
    return "\n".join(str(c.args[0]) for c in cog.bot.output_router.post_log.await_args_list)


@pytest.mark.parametrize("state", [None, "NOT_SIGNED_UP", "PENDING_ADMIN_APPROVAL", "ASSIGNED"])
async def test_every_departure_goes_through_the_wizard_clean_up(tmp_path, state):
    """Whatever the member's state, the listener asks the queue, as the bot, for one
    `signup.member_left` change naming them, and cleans nothing up itself: their driver state
    is as it was."""
    from leaguebot.core.models.change import ChangeOrigin

    db_path = await _make_db(tmp_path, name=f"mr_{state}", state=state)
    cog = _cog(db_path)

    await _leave(cog)

    [ask] = _asked_member_left(cog)
    assert str(DRIVER) in str(ask.args[1])
    assert ask.kwargs.get("origin") == ChangeOrigin.BOT
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT current_state FROM driver_profiles WHERE discord_user_id = ?", (str(DRIVER),)
        )
        row = await cursor.fetchone()
    assert (row[0] if row else None) == state


@pytest.mark.parametrize(
    "state", [None, "NOT_SIGNED_UP", "PENDING_SIGNUP_COMPLETION", "PENDING_ADMIN_APPROVAL",
              "PENDING_DRIVER_CORRECTION"]
)
async def test_anyone_else_leaving_is_not_announced(tmp_path, state):
    """Logging every departure from a large server would bury the ones that matter."""
    cog = _cog(await _make_db(tmp_path, name=f"mr_quiet_{state}", state=state))

    await _leave(cog)

    cog.bot.output_router.post_log.assert_not_awaited()
