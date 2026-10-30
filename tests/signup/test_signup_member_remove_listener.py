"""What happens when a member leaves the server, from the signup cog's listener.

Issue #208. `SignupCog.on_member_remove` was untested; the clean-up it asks for,
`signup.member_left`, is tested in `test_wizard_changes.py`. The listener adds one thing of its
own, and it is the part a league notices.

**Every departure is handed to the change queue first.** A driver halfway through signing up
has a channel, jobs and a driver state to put right, and that is the `signup.member_left` change's
work (#439, slice 6b, C80) — the listener asks the queue for it, as the bot, for every member,
whoever they are, and the change's own check decides what there is to clean up.

**A placed or approved driver leaving is announced.** A driver who is Unassigned or Assigned has
no wizard to clean up, so the wizard path says nothing about them, yet a league has just lost a
driver it may have seated. The listener logs it, by the name they signed up under — their Discord
name may have changed, or they may already be unresolvable now they have gone.

**Anyone else leaving is not.** A member who never signed up, or whose signup the wizard path
already reported, is not a league event, and logging every departure from a large server would
bury the ones that are.

**The announcement cannot fail the clean-up.** The clean-up is asked first and the log is
best effort: a missing signup record falls back to the member's own name, and a log failure is
swallowed rather than raised out of a Discord event handler.
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
                "INSERT INTO signup_records (discord_user_id, discord_username, "
                "server_display_name) VALUES (?, 'racer', 'Racer One')",
                (str(DRIVER),),
            )
        await db.commit()
    return db_path


def _cog(db_path):
    bot = MagicMock()
    bot.db_path = db_path
    bot.wizard_service = MagicMock()
    # Today's clean-up off the queue, a stand-in that does nothing, so that only what the
    # listener asks of the queue shows.
    bot.wizard_service.handle_member_remove = AsyncMock()
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


@pytest.mark.parametrize("state", ["UNASSIGNED", "ASSIGNED"])
async def test_a_placed_or_approved_driver_leaving_is_announced(tmp_path, state):
    """The wizard path says nothing about them, yet the league has just lost a driver. They
    are named by the name they signed up under."""
    cog = _cog(await _make_db(tmp_path, name=f"mr_announce_{state}", state=state))

    await _leave(cog)

    logged = _logged(cog)
    assert "Driver left server" in logged
    assert f"state: {state}" in logged
    assert "**Racer One**" in logged


async def test_a_driver_with_no_signup_record_is_named_by_their_discord_name(tmp_path):
    cog = _cog(await _make_db(tmp_path, name="mr_norecord", state="ASSIGNED", record=False))

    await _leave(cog)

    assert "**DiscordName**" in _logged(cog)


@pytest.mark.parametrize(
    "state", [None, "NOT_SIGNED_UP", "PENDING_SIGNUP_COMPLETION", "PENDING_ADMIN_APPROVAL",
              "PENDING_DRIVER_CORRECTION"]
)
async def test_anyone_else_leaving_is_not_announced(tmp_path, state):
    """Logging every departure from a large server would bury the ones that matter."""
    cog = _cog(await _make_db(tmp_path, name=f"mr_quiet_{state}", state=state))

    await _leave(cog)

    cog.bot.output_router.post_log.assert_not_awaited()


async def test_a_failing_announcement_is_not_raised_out_of_the_event(tmp_path):
    """An Assigned driver leaves and the log channel refuses the announcement: the listener
    raises nothing, and the clean-up was still asked of the queue."""
    cog = _cog(await _make_db(tmp_path, name="mr_logfail", state="ASSIGNED"))
    cog.bot.output_router.post_log = AsyncMock(side_effect=RuntimeError("no log channel"))

    await _leave(cog)  # must not raise

    assert len(_asked_member_left(cog)) == 1
