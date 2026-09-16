"""What happens when a member leaves the server, from the signup cog's listener.

Issue #208. `SignupCog.on_member_remove` was untested; the wizard clean-up it calls,
`handle_member_remove`, is tested in `test_wizard_lifecycle.py`. The listener adds one thing of
its own, and it is the part a league notices.

**Every departure goes through the wizard clean-up first.** A driver halfway through signing up
has a channel, jobs and a driver state to put right, and that is the wizard service's job — the
listener hands it over for every member, whoever they are.

**A placed or approved driver leaving is announced.** A driver who is Unassigned or Assigned has
no wizard to clean up, so the wizard path says nothing about them, yet a league has just lost a
driver it may have seated. The listener logs it, by the name they signed up under — their Discord
name may have changed, or they may already be unresolvable now they have gone.

**Anyone else leaving is not.** A member who never signed up, or whose signup the wizard path
already reported, is not a league event, and logging every departure from a large server would
bury the ones that are.

**The announcement cannot fail the clean-up.** The wizard clean-up runs first and the log is
best effort: a missing signup record falls back to the member's own name, and a log failure is
swallowed rather than raised out of a Discord event handler.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from cogs.signup_cog import SignupCog  # noqa: E402
from db.database import get_connection, run_migrations  # noqa: E402

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
                "INSERT INTO driver_profiles (server_id, discord_user_id, current_state) "
                "VALUES (?, ?, ?)",
                (SERVER_ID, str(DRIVER), state),
            )
        if record:
            await db.execute(
                "INSERT INTO signup_records (server_id, discord_user_id, discord_username, "
                "server_display_name) VALUES (?, ?, 'racer', 'Racer One')",
                (SERVER_ID, str(DRIVER)),
            )
        await db.commit()
    return db_path


def _cog(db_path):
    bot = MagicMock()
    bot.db_path = db_path
    bot.wizard_service = MagicMock()
    bot.wizard_service.handle_member_remove = AsyncMock()
    bot.output_router = MagicMock()
    bot.output_router.post_log = AsyncMock()
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


def _logged(cog) -> str:
    return "\n".join(str(c.args[1]) for c in cog.bot.output_router.post_log.await_args_list)


@pytest.mark.parametrize("state", [None, "NOT_SIGNED_UP", "PENDING_ADMIN_APPROVAL", "ASSIGNED"])
async def test_every_departure_goes_through_the_wizard_clean_up(tmp_path, state):
    cog = _cog(await _make_db(tmp_path, name=f"mr_{state}", state=state))

    await _leave(cog)

    cog.bot.wizard_service.handle_member_remove.assert_awaited_once()
    args = cog.bot.wizard_service.handle_member_remove.await_args.args
    assert args[:2] == (SERVER_ID, str(DRIVER))


@pytest.mark.parametrize("state", ["UNASSIGNED", "ASSIGNED"])
async def test_a_placed_or_approved_driver_leaving_is_announced(tmp_path, state):
    """The wizard path says nothing about them, yet the league has just lost a driver."""
    cog = _cog(await _make_db(tmp_path, name=f"mr_announce_{state}", state=state))

    await _leave(cog)

    logged = _logged(cog)
    assert "Driver left server" in logged
    assert f"state: {state}" in logged


async def test_the_announcement_uses_the_name_they_signed_up_under(tmp_path):
    cog = _cog(await _make_db(tmp_path, name="mr_name", state="ASSIGNED"))

    await _leave(cog)

    assert "**Racer One**" in _logged(cog)


async def test_a_driver_with_no_signup_record_is_named_by_their_discord_name(tmp_path):
    cog = _cog(await _make_db(tmp_path, name="mr_norecord", state="ASSIGNED", record=False))

    await _leave(cog)

    assert "**DiscordName**" in _logged(cog)


@pytest.mark.parametrize(
    "state", [None, "NOT_SIGNED_UP", "PENDING_SIGNUP_COMPLETION", "PENDING_ADMIN_APPROVAL", "LEAGUE_BANNED"]
)
async def test_anyone_else_leaving_is_not_announced(tmp_path, state):
    """Logging every departure from a large server would bury the ones that matter."""
    cog = _cog(await _make_db(tmp_path, name=f"mr_quiet_{state}", state=state))

    await _leave(cog)

    cog.bot.output_router.post_log.assert_not_awaited()


async def test_a_failing_announcement_is_not_raised_out_of_the_event(tmp_path):
    cog = _cog(await _make_db(tmp_path, name="mr_logfail", state="ASSIGNED"))
    cog.bot.output_router.post_log = AsyncMock(side_effect=RuntimeError("no log channel"))

    await _leave(cog)  # must not raise

    cog.bot.wizard_service.handle_member_remove.assert_awaited_once()
