"""The league's two roles, and what changing one has to move with it (issue #276).

`/bot base-role` and `/bot driver-role` look like a pair and are not: one governs who can *see*
the signup channel, the other is granted to a driver when their signup is approved. Both were
the signup module's until a second module needed the base role; they are core's now, stored on
the server configuration, and survive the signup module being disabled.

**The base role is a channel permission, so changing it has to move the overwrite.** The signup
channel is visible to the base role and to nobody else; setting a new role without granting it
the overwrite would leave a league whose members cannot see the channel they are told to sign up
in, with nothing in the bot's replies to suggest why. And the *old* role's overwrite is removed
in the same breath, or the previous role keeps access it is no longer entitled to —
`test_the_previous_role_loses_its_access` is the half most easily forgotten, because nothing
visibly breaks when it is.

**Re-setting the same role does not remove its own overwrite.** The removal and the grant are
two calls, and a naive "remove the old, add the new" on the same role would briefly revoke and
restore it — or, if the grant failed in between, revoke it outright.

**The driver role touches no channel at all.** It is granted to a person, not applied to a
place, so it writes the configuration and stops. Tested explicitly because the two commands
share a body and the obvious tidy-up is to let the channel work run for both.

**A permissions failure does not fail the command.** Discord refuses an overwrite for reasons
that have nothing to do with the setting — a role above the bot's own, a channel it was removed
from — and the configured role is still the right one to store. The channel is repairable by
hand; a half-written configuration is not obviously repairable at all.

**Both are audited with the role they replaced.** A league that finds its members locked out
needs to know which role used to hold the access, and it is not recoverable from anywhere else
once the configuration is overwritten.

**Both are fixed once a season's configuration is confirmed.** A driver role changed mid-season
would leave every current driver holding the old one for good, the season's end revoking only
the new.

**Save a role deleted from the server** (#374), which nobody holds any longer. It may be replaced
while fixed, and the replacement is given to every driver — the base role too, the one time the
bot grants it — for without it a season could open no signup window until it ended.

**Both are core `Setting`s on the change queue** (#283, S13b), as the four `/bot` settings beside
them are (`test_bot_cog.py`): the command answers with the queue's acknowledgement, and the queue
writes the role, its audit entry and its line in one save, stamped with the queue's clock. The
signup channel's overwrite is signup's, moved by its subscriber to the "base role changed" hook the
builder signs it up to; the grants to every driver, where the old role was gone, follow the save
as its own follow-on. The tests run the bot the entry point builds, with the real queue and "now"
pinned, and run the queue.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from leaguebot.core.cogs.bot_cog import BotCog
from leaguebot.core.db.database import get_connection, run_migrations
from tests.support.undecorate import undecorate

SERVER_ID = 27601
SIGNUP_CHANNEL = 700
LOG_CHANNEL = 101
OLD_ROLE = 3001
NEW_ROLE = 3002
ACTOR_ID = 77

#: "Now", as the change queue's clock gives it.
NOW = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)

#: The manager running the commands, as a log line names them, the mention wrapped so it
#: notifies nobody.
NAMED = "Manager (`<@77>`)"

#: Passed as *signup_channel* for a league whose signup module is not enabled at all.
NO_SIGNUP = object()

#: The command as its line names it, and the label its reply gives the role, by column.
_WORDS = {
    "base_role_id": ("/bot base-role", "Base role"),
    "driver_role_id": ("/bot driver-role", "Driver role"),
}

#: What a reply says of a role replaced while Season 4's configuration is fixed.
_REPLACED = (
    "The role it replaces is no longer on the server, so it could be replaced although "
    "Season 4's configuration is fixed."
)

#: What a replaced base role adds: the bot knows its drivers and nobody else.
_OTHER_MEMBERS = (
    " The league's other members need it too: give it to them by hand, the bot knowing only "
    "its drivers."
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _make_db(
    tmp_path,
    *,
    base_role: int | None = OLD_ROLE,
    driver_role: int | None = OLD_ROLE,
    signup_channel=SIGNUP_CHANNEL,
    stage: str | None = None,
    claimed: bool = True,
    drivers: tuple[tuple[str, str], ...] = (),
) -> str:
    db_path = os.path.join(str(tmp_path), "league_roles.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id, base_role_id, driver_role_id) "
            "VALUES (?, 900, 100, ?, ?, ?)",
            (SERVER_ID if claimed else None, LOG_CHANNEL, base_role, driver_role),
        )
        if signup_channel is not NO_SIGNUP:
            await db.execute(
                "INSERT INTO signup_module_config (id, signup_channel_id) VALUES (1, ?)",
                (signup_channel,),
            )
        if stage is not None:
            # A trigger holds the stage to its status: Configuration is a SETUP season.
            await db.execute(
                "INSERT INTO seasons (start_date, status, season_number, stage) "
                "VALUES ('2026-01-01', ?, 4, ?)",
                ("SETUP" if stage == "CONFIGURATION" else "ACTIVE", stage),
            )
        for user_id, state in drivers:
            await db.execute(
                "INSERT INTO driver_profiles (discord_user_id, current_state) VALUES (?, ?)",
                (user_id, state),
            )
        await db.commit()
    return db_path


def _role(role_id: int = NEW_ROLE, name: str = "Drivers", *, above_the_bot: bool = False):
    """A role the bot can grant, unless *above_the_bot* says otherwise."""
    role = MagicMock(spec=discord.Role)
    role.id = role_id
    role.name = name
    role.mention = f"<@&{role_id}>"
    role.is_default.return_value = False
    role.managed = False
    role.guild.me.guild_permissions.manage_roles = True
    role.guild.me.top_role.__gt__ = lambda _self, _other: not above_the_bot
    return role


def _server(
    role: Any,
    *,
    old_role_gone: bool = False,
    channel_missing: bool = False,
    set_permissions_fails: bool = False,
    refusing: tuple[int, ...] = (),
) -> MagicMock:
    """The league's server: it finds *role*, the stored role 3001 (unless *old_role_gone*), the
    signup channel 700 (unless *channel_missing*), whose `set_permissions` is recorded in
    `signup_channel` and raises where *set_permissions_fails*, and the drivers 5001 and 5002, kept
    in `drivers`, each refusing the role where named in *refusing*."""
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = SIGNUP_CHANNEL
    channel.set_permissions = AsyncMock(
        side_effect=RuntimeError("missing permissions") if set_permissions_fails else None
    )
    roles = {role.id: role}
    if not old_role_gone and role.id != OLD_ROLE:
        roles[OLD_ROLE] = _role(OLD_ROLE, "Old members")
    drivers = {
        uid: MagicMock(
            add_roles=AsyncMock(
                side_effect=discord.Forbidden(MagicMock(status=403), "no")
                if uid in refusing
                else None
            )
        )
        for uid in (5001, 5002)
    }
    guild = MagicMock()
    guild.id = SERVER_ID
    guild.get_channel = MagicMock(
        side_effect=lambda cid: channel if cid == SIGNUP_CHANNEL and not channel_missing else None
    )
    guild.get_role = MagicMock(side_effect=roles.get)
    guild.get_member = MagicMock(side_effect=drivers.get)
    guild.signup_channel = channel
    guild.drivers = drivers
    return guild


async def _league(db_path: str, guild: MagicMock) -> Any:
    """The bot the entry point builds on *db_path*, its configuration read from the database, the
    league's server *guild*, the log channel 101 recording what it is sent, and its real change
    queue, its clock `NOW`."""
    from tests.support.change_queue import league_bot, league_queue

    bot = await league_bot(db_path, real_config=True)
    channels = {LOG_CHANNEL: bot.log_channel}
    bot.get_channel = MagicMock(side_effect=channels.get)
    bot.fetch_channel = AsyncMock(side_effect=lambda cid: channels[cid])
    bot.get_guild = MagicMock(return_value=guild)
    league_queue(bot, db_path, now=NOW)
    return bot


async def _set(db_path: str, column: str, role: Any = None, **server: Any) -> tuple[Any, Any, Any]:
    """Run the command for *column* with *role* (`_role()` if None) by the manager on the league's
    server (`_server(role, **server)`), then run the queue: the bot, the interaction and the
    server."""
    from tests.support.change_queue import run_queue

    role = role or _role()
    guild = _server(role, **server)
    bot = await _league(db_path, guild)
    asked = _asked(bot, column, guild)
    await _command(column)(BotCog(bot), asked, role)
    await run_queue(bot)
    return bot, asked, guild


def _asked(bot: Any, column: str, guild: Any) -> Any:
    """The command for *column* run on *guild* by the manager (77), its client *bot*."""
    from tests.support.change_queue import member
    from tests.support.settings_league import interaction

    asked = interaction(bot, _WORDS[column][0][1:], user=member(ACTOR_ID, "Manager", "Manager#0001"))
    asked.guild_id = SERVER_ID
    asked.guild = guild
    return asked


def _command(column: str) -> Any:
    return undecorate(
        BotCog.handle_base_role if column == "base_role_id" else BotCog.handle_driver_role
    )


def _reply(asked: Any) -> str:
    """What the manager's reply reads now."""
    from tests.support.settings_league import last_said

    return last_said(asked)


def _lines(bot: Any) -> list[str]:
    """Every line the log channel took, without the rule drawn beneath each."""
    from tests.support.settings_league import unruled

    return [unruled(str(line)) for line in bot.log_channel.sent]


async def _stored(db_path) -> dict:
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT * FROM server_configs")
        return dict(await cursor.fetchone())


async def _audit_rows(db_path) -> list[dict]:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT actor_id, change_type, old_value, new_value, timestamp FROM audit_entries",
        )
        return [dict(r) for r in await cursor.fetchall()]


def _permission_calls(guild) -> list:
    return guild.signup_channel.set_permissions.await_args_list


_BOTH = [
    pytest.param("base_role_id", "BASE_ROLE_SET", id="base"),
    pytest.param("driver_role_id", "DRIVER_ROLE_SET", id="driver"),
]

_TWO_DRIVERS = (("5001", "ASSIGNED"), ("5002", "UNASSIGNED"), ("5003", "NOT_SIGNED_UP"))


# ---------------------------------------------------------------------------
# Both commands
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("column,change_type", _BOTH)
async def test_the_role_is_stored_as_the_league_s(tmp_path, column, change_type):
    db_path = await _make_db(tmp_path)
    before = await _stored(db_path)

    await _set(db_path, column)

    after = await _stored(db_path)
    assert after[column] == NEW_ROLE
    assert {k: v for k, v in after.items() if k != column} == {
        k: v for k, v in before.items() if k != column
    }


@pytest.mark.parametrize("column,change_type", _BOTH)
async def test_a_league_with_signup_disabled_still_sets_the_role(tmp_path, column, change_type):
    """The roles are the league's, not the signup module's: a league that runs without
    signups still has members, and the stewarding module and the hub read the base role."""
    db_path = await _make_db(tmp_path, signup_channel=NO_SIGNUP)

    _bot, _asked_, guild = await _set(db_path, column)

    assert (await _stored(db_path))[column] == NEW_ROLE
    assert _permission_calls(guild) == []


@pytest.mark.parametrize("column,change_type", _BOTH)
async def test_the_change_records_the_role_it_replaced(tmp_path, column, change_type):
    """A league that finds its members locked out needs to know which role used to hold the
    access, and it is not recoverable from anywhere else once the configuration is
    overwritten. The entry names the manager and is stamped with the queue's clock, never the
    host's."""
    db_path = await _make_db(tmp_path)

    await _set(db_path, column)

    rows = await _audit_rows(db_path)
    assert [r["change_type"] for r in rows] == [change_type]
    assert rows[0]["actor_id"] == ACTOR_ID
    assert json.loads(rows[0]["old_value"]) == {"role_id": OLD_ROLE}
    assert json.loads(rows[0]["new_value"]) == {"role_id": NEW_ROLE}
    assert rows[0]["timestamp"] == NOW.isoformat()


@pytest.mark.parametrize("column,change_type", _BOTH)
async def test_the_change_is_logged_with_the_roles_name(tmp_path, column, change_type):
    """An id in the log tells a manager reading it nothing. The line is today's, word for word."""
    db_path = await _make_db(tmp_path)

    bot, _asked_, _guild = await _set(db_path, column, _role(NEW_ROLE, "Verified members"))

    assert _lines(bot) == [
        f"{NAMED} | {_WORDS[column][0]} | Success\n  role: Verified members (`<@&{NEW_ROLE}>`)"
    ]


@pytest.mark.parametrize("column,change_type", _BOTH)
async def test_the_manager_is_told_which_role_was_set(tmp_path, column, change_type):
    """The manager is answered at once with the queue's acknowledgement, which answers inside
    Discord's three seconds, and nothing is written until the queue runs the change; then the
    acknowledgement is updated to today's reply."""
    from tests.support.change_queue import acknowledgement, run_queue

    db_path = await _make_db(tmp_path)
    command, label = _WORDS[column]
    role = _role()
    guild = _server(role)
    bot = await _league(db_path, guild)
    asked = _asked(bot, column, guild)

    await _command(column)(BotCog(bot), asked, role)

    assert acknowledgement(asked).startswith(
        f"⏳ Setting **{label}** to <@&{NEW_ROLE}>. This message will be updated when it is "
        f"done; if it takes longer, the log channel will say so."
    )
    assert (await _stored(db_path))[column] == OLD_ROLE
    await run_queue(bot)

    assert _reply(asked) == f"✅ **{label}** set to <@&{NEW_ROLE}>."


@pytest.mark.parametrize("column,change_type", _BOTH)
async def test_a_league_role_saves_its_value_and_audit_together(
    tmp_path, monkeypatch, column, change_type
):
    """The audit entry's write fails on the database as the queue runs the change. The role is
    not saved either, and no audit entry stands; the signup channel is not touched. The manager
    is told in `setting_change`'s words that nothing was saved and to run it again; the log
    channel gets the one failure line and no success line; the queue is not stopped."""
    import sqlite3

    from leaguebot.core.services import change_queue
    from tests.support.change_queue import stopped_job

    db_path = await _make_db(tmp_path)
    command, _label = _WORDS[column]
    monkeypatch.setattr(
        change_queue, "record_change_on",
        AsyncMock(side_effect=sqlite3.OperationalError("disk I/O error")),
    )

    bot, asked, guild = await _set(db_path, column)

    assert (await _stored(db_path))[column] == OLD_ROLE
    assert await _audit_rows(db_path) == []
    assert _permission_calls(guild) == []
    assert _reply(asked) == (
        f"❌ `{command}` stopped on a fault in the bot, not on anything you entered, and did not "
        f"finish. Nothing was saved. Run `{command}` again to retry. The fault is recorded in the "
        f"log channel."
    )
    assert _lines(bot) == [
        f"❌ `{command}` failed for {NAMED} — OperationalError. The details are in the host's log."
    ]
    assert await stopped_job(db_path) is None


@pytest.mark.parametrize("column,change_type", _BOTH)
async def test_a_role_is_refused_once_the_configuration_is_confirmed(
    tmp_path, column, change_type
):
    """A driver role changed mid-season would leave every current driver holding the old one
    for good, the season's end revoking only the new. Refused at the ask in today's words."""
    db_path = await _make_db(tmp_path, stage="ONGOING")
    command, _label = _WORDS[column]

    _bot, asked, guild = await _set(db_path, column)

    assert _reply(asked) == (
        f"❌ The league's roles are fixed for Season 4 now that its configuration has been "
        f"confirmed. `{command}` is available again once the season has ended, or while a new "
        f"season is in configuration — or at once, should the role be deleted from the server."
    )
    assert (await _stored(db_path))[column] == OLD_ROLE
    assert await _audit_rows(db_path) == []
    assert _permission_calls(guild) == []


@pytest.mark.parametrize("column,change_type", _BOTH)
async def test_a_role_gone_from_the_server_may_be_replaced_while_fixed(
    tmp_path, column, change_type
):
    """Nobody holds a deleted role, so the reason for fixing it is spent — and refusing would
    leave the season unable to open a window until it ended."""
    db_path = await _make_db(tmp_path, stage="ONGOING")

    bot, asked, _guild = await _set(db_path, column, old_role_gone=True)

    assert (await _stored(db_path))[column] == NEW_ROLE
    assert "no longer on the server" in _reply(asked)
    assert [r["change_type"] for r in await _audit_rows(db_path)] == [change_type]
    assert "replaced: a role no longer on the server" in _lines(bot)[-1]


@pytest.mark.parametrize("column,change_type", _BOTH)
async def test_a_role_never_set_stays_fixed(tmp_path, column, change_type):
    """Not set is not gone: a league that ran its season with no signup module chose no role."""
    db_path = await _make_db(tmp_path, stage="ONGOING", base_role=None, driver_role=None)

    _bot, asked, _guild = await _set(db_path, column, old_role_gone=True)

    assert "fixed for Season 4" in _reply(asked)
    assert (await _stored(db_path))[column] is None


@pytest.mark.parametrize("column,change_type", _BOTH)
async def test_the_replacement_is_given_to_every_driver(tmp_path, column, change_type):
    """Every driver lost the old role with it. Whoever the driver role belongs to is given the
    replacement — the base role too, the one time the bot grants it — once the queue has saved
    it, as the setting's follow-on: nobody is given it while the change waits. The reply and the
    line are today's, word for word, the line the only one the log channel takes."""
    from tests.support.change_queue import run_queue

    db_path = await _make_db(tmp_path, stage="ONGOING", drivers=_TWO_DRIVERS)
    command, label = _WORDS[column]
    role = _role()
    guild = _server(role, old_role_gone=True)
    bot = await _league(db_path, guild)
    asked = _asked(bot, column, guild)

    await _command(column)(BotCog(bot), asked, role)
    for driver in guild.drivers.values():
        driver.add_roles.assert_not_awaited()
    await run_queue(bot)

    for driver in guild.drivers.values():
        driver.add_roles.assert_awaited_once_with(role, reason="League role replaced")
    others = _OTHER_MEMBERS if column == "base_role_id" else ""
    assert _reply(asked) == (
        f"✅ **{label}** set to <@&{NEW_ROLE}>.\n{_REPLACED}\n"
        f"It has been given to 2 driver(s).{others}"
    )
    assert _lines(bot) == [
        f"{NAMED} | {command} | Success\n  role: Drivers (`<@&{NEW_ROLE}>`)\n"
        f"  replaced: a role no longer on the server\n  given to: 2 driver(s)"
    ]


@pytest.mark.parametrize("column,change_type", _BOTH)
async def test_a_driver_who_could_not_be_given_it_is_named(tmp_path, column, change_type):
    """The reply and the line are today's, word for word, the line the only one the log channel
    takes."""
    db_path = await _make_db(tmp_path, stage="ONGOING", drivers=_TWO_DRIVERS)
    command, label = _WORDS[column]

    bot, asked, _guild = await _set(db_path, column, old_role_gone=True, refusing=(5002,))

    others = _OTHER_MEMBERS if column == "base_role_id" else ""
    assert _reply(asked) == (
        f"✅ **{label}** set to <@&{NEW_ROLE}>.\n{_REPLACED}\n"
        f"It has been given to 1 driver(s). It could not be given to <@5002> — give it to them "
        f"by hand.{others}"
    )
    assert _lines(bot) == [
        f"{NAMED} | {command} | Success\n  role: Drivers (`<@&{NEW_ROLE}>`)\n"
        f"  replaced: a role no longer on the server\n  given to: 1 driver(s)\n"
        f"  not given to: `<@5002>`"
    ]


@pytest.mark.parametrize("column,change_type", _BOTH)
async def test_a_fault_giving_the_replacement_leaves_the_role_saved(
    tmp_path, monkeypatch, column, change_type
):
    """Season 4's configuration is fixed and the stored role is gone from the server; the manager
    sets a new one, and giving it to the drivers raises once the queue has saved it. The role is
    saved and audited, and the queue is not stopped. The reply says the role is set and replaced,
    then that it could not be given to the drivers, for the league to give it by hand, a base role
    still reminding the league that its other members need it too; the log channel gets the
    queue's failure line, and no success line, the grants carrying it (decision 169)."""
    from leaguebot.core.services.placement_service import PlacementService
    from tests.support.change_queue import stopped_job

    db_path = await _make_db(tmp_path, stage="ONGOING", drivers=_TWO_DRIVERS)
    command, label = _WORDS[column]
    monkeypatch.setattr(
        PlacementService, "grant_to_every_driver", AsyncMock(side_effect=RuntimeError("x"))
    )

    bot, asked, _guild = await _set(db_path, column, old_role_gone=True)

    assert (await _stored(db_path))[column] == NEW_ROLE
    assert [r["change_type"] for r in await _audit_rows(db_path)] == [change_type]
    others = _OTHER_MEMBERS if column == "base_role_id" else ""
    assert _reply(asked) == (
        f"✅ **{label}** set to <@&{NEW_ROLE}>.\n{_REPLACED}\n"
        f"⚠️ The role is saved, but it could not be given to the drivers: give it to them by "
        f"hand.{others}"
    )
    assert _lines(bot) == [
        f"❌ `{command}` failed for {NAMED} — RuntimeError. The details are in the host's log."
    ]
    assert await stopped_job(db_path) is None


@pytest.mark.parametrize(
    ("stored", "stage", "said"),
    [
        pytest.param(
            OLD_ROLE, "ONGOING",
            f"✅ **Base role** set to @everyone.\n{_REPLACED}\nEverybody holds it already.",
            id="replacing-a-gone-role",
        ),
        pytest.param(
            SERVER_ID, None, "ℹ️ Nothing changed: **Base role** is already @everyone.",
            id="already-held",
        ),
    ],
)
async def test_a_base_role_replaced_by_everyone_is_given_to_nobody(tmp_path, stored, stage, said):
    """Everybody holds @everyone already. Replacing a base role gone from the server while Season
    4's configuration is fixed, or setting it while @everyone is already the base role, the reply
    names it "@everyone", as Discord renders the server's default role, never as a role mention;
    it carries no reminder that the league's other members need it, and no driver is given it."""
    db_path = await _make_db(tmp_path, stage=stage, base_role=stored, drivers=_TWO_DRIVERS)
    role = _role(SERVER_ID, "@everyone")
    role.mention = "@everyone"
    role.is_default.return_value = True

    _bot, asked, guild = await _set(db_path, "base_role_id", role, old_role_gone=True)

    assert _reply(asked) == said
    for driver in guild.drivers.values():
        driver.add_roles.assert_not_awaited()


@pytest.mark.parametrize("column,change_type", _BOTH)
async def test_a_role_may_be_set_while_the_season_is_in_configuration(
    tmp_path, column, change_type
):
    db_path = await _make_db(tmp_path, stage="CONFIGURATION")

    await _set(db_path, column)

    assert (await _stored(db_path))[column] == NEW_ROLE


@pytest.mark.parametrize("column,change_type", _BOTH)
async def test_a_server_not_yet_set_up_is_refused(tmp_path, column, change_type):
    """The tier guard refuses first in practice; the body refuses too rather than writing a
    role into a row that serves no server. Refused in today's words, to the host's log alone."""
    db_path = await _make_db(tmp_path, claimed=False)

    bot, asked, _guild = await _set(db_path, column)

    assert _reply(asked) == "⛔ This server is not configured yet — run `/bot init` first."
    assert (await _stored(db_path))[column] == OLD_ROLE
    assert await _audit_rows(db_path) == []
    assert _lines(bot) == []


def test_both_commands_are_a_league_manager_s_in_the_interaction_channel():
    """They repair nothing the guards read, so nothing exempts them from the channel rule —
    unlike the four `/bot` settings beside them."""
    from leaguebot.core.utils.channel_guard import CHANNEL_EXEMPT_ATTRIBUTE, LEAGUE_MANAGER, TIER_ATTRIBUTE

    for command in (BotCog.handle_base_role, BotCog.handle_driver_role):
        assert getattr(command.callback, TIER_ATTRIBUTE) == LEAGUE_MANAGER
        assert not getattr(command.callback, CHANNEL_EXEMPT_ATTRIBUTE, False)


# ---------------------------------------------------------------------------
# The base role, and the channel it opens
# ---------------------------------------------------------------------------


async def test_the_new_role_is_granted_access_to_the_signup_channel(tmp_path):
    """Without it a league's members cannot see the channel they are told to sign up in,
    and nothing in the bot's replies suggests why."""
    db_path = await _make_db(tmp_path)
    role = _role()

    _bot, _asked_, guild = await _set(db_path, "base_role_id", role)

    grant = [c for c in _permission_calls(guild) if c.args[0] is role]
    assert len(grant) == 1
    assert grant[0].kwargs == {
        "view_channel": True,
        "send_messages": False,
        "use_application_commands": True,
    }


async def test_the_previous_role_loses_its_access(tmp_path):
    """The half most easily forgotten, because nothing visibly breaks when it is: the old
    role simply keeps access it is no longer entitled to."""
    db_path = await _make_db(tmp_path)

    _bot, _asked_, guild = await _set(db_path, "base_role_id")

    revoked = [c for c in _permission_calls(guild) if "overwrite" in c.kwargs]
    assert len(revoked) == 1
    assert revoked[0].args[0].id == OLD_ROLE


@pytest.mark.parametrize("column,change_type", _BOTH)
async def test_re_setting_the_same_role_does_not_revoke_it(tmp_path, column, change_type):
    """The removal and the grant are two calls; on one role a naive "remove then add" would
    briefly revoke it, and outright if the grant failed in between. A role set to the role it
    holds is answered "Nothing changed" in `setting_change`'s way (decision 163): the reply says
    the role already holds the setting, the log channel gets a "Nothing changed" line, no audit
    entry is written, and nothing follows — the signup channel is not touched at all."""
    db_path = await _make_db(tmp_path)
    command, label = _WORDS[column]

    bot, asked, guild = await _set(db_path, column, _role(OLD_ROLE, "Old members"))

    assert (await _stored(db_path))[column] == OLD_ROLE
    assert _reply(asked) == f"ℹ️ Nothing changed: **{label}** is already <@&{OLD_ROLE}>."
    assert _lines(bot) == [
        f"{NAMED} | {command} | Nothing changed\n  role: Old members (`<@&{OLD_ROLE}>`)"
    ]
    assert await _audit_rows(db_path) == []
    assert _permission_calls(guild) == []


async def test_a_league_setting_its_first_base_role_revokes_nothing(tmp_path):
    db_path = await _make_db(tmp_path, base_role=None)

    _bot, _asked_, guild = await _set(db_path, "base_role_id")

    assert len(_permission_calls(guild)) == 1


async def test_a_previous_role_that_has_been_deleted_is_stepped_over(tmp_path):
    """Its overwrite went with it, and the new role still needs granting."""
    db_path = await _make_db(tmp_path)
    role = _role()

    _bot, _asked_, guild = await _set(db_path, "base_role_id", role, old_role_gone=True)

    assert [c for c in _permission_calls(guild) if c.args[0] is role]
    assert (await _stored(db_path))["base_role_id"] == NEW_ROLE


async def test_a_league_with_no_signup_channel_yet_still_sets_the_role(tmp_path):
    db_path = await _make_db(tmp_path, signup_channel=None)

    _bot, _asked_, guild = await _set(db_path, "base_role_id")

    assert (await _stored(db_path))["base_role_id"] == NEW_ROLE
    assert len(await _audit_rows(db_path)) == 1
    assert _permission_calls(guild) == []


async def test_a_deleted_signup_channel_still_sets_the_role(tmp_path):
    db_path = await _make_db(tmp_path)

    await _set(db_path, "base_role_id", channel_missing=True)

    assert (await _stored(db_path))["base_role_id"] == NEW_ROLE


async def test_a_permissions_failure_does_not_fail_the_command(tmp_path):
    """Discord refuses an overwrite for reasons that have nothing to do with the setting.
    The channel is repairable by hand; a half-written configuration is not obviously
    repairable at all."""
    db_path = await _make_db(tmp_path)

    _bot, asked, _guild = await _set(db_path, "base_role_id", set_permissions_fails=True)

    assert (await _stored(db_path))["base_role_id"] == NEW_ROLE
    assert _reply(asked) == f"✅ **Base role** set to <@&{NEW_ROLE}>."


async def test_a_changed_base_role_is_handed_to_the_base_role_changed_hook(tmp_path):
    """The league's base role is 3001 and its signup channel 700; the manager sets it to 3002 and
    the queue runs the change. Core hands the change to the "base role changed" hook the builder
    makes (architecture.md, "How modules and core fit together"), never reaching signup itself:
    a subscriber signed up after the builder's is awaited once, after the save, with the league's
    server, the role replaced and the role now set, and signup's subscriber, which the builder
    signs up, has moved the signup channel's overwrite from the one to the other."""
    from tests.support.change_queue import run_queue

    db_path = await _make_db(tmp_path)
    role = _role()
    guild = _server(role)
    bot = await _league(db_path, guild)
    handed: list[tuple[Any, ...]] = []

    async def subscriber(*args: Any) -> None:
        handed.append(((await _stored(db_path))["base_role_id"], *args))

    bot.base_role_changed.subscribe(subscriber)

    await _command("base_role_id")(BotCog(bot), _asked(bot, "base_role_id", guild), role)
    assert handed == []
    await run_queue(bot)

    assert handed == [(NEW_ROLE, guild, OLD_ROLE, role)]
    assert [c.args[0].id for c in _permission_calls(guild)] == [OLD_ROLE, NEW_ROLE]



# ---------------------------------------------------------------------------
# The driver role, which is not a channel permission
# ---------------------------------------------------------------------------


async def test_the_driver_role_touches_no_channel(tmp_path):
    """It is granted to a person, not applied to a place. Tested explicitly because the two
    commands share a body, and the obvious tidy-up is to let the channel work run for both."""
    db_path = await _make_db(tmp_path)

    _bot, _asked_, guild = await _set(db_path, "driver_role_id")

    assert _permission_calls(guild) == []


async def test_a_driver_role_the_bot_cannot_grant_is_refused(tmp_path):
    """It is granted at every approval, and `wizard_service.approve_signup` only logs a grant
    Discord refuses — so a role the bot cannot grant costs every driver of the window their
    role with nobody told (#374). Setting it is the moment a league is there to choose another,
    as `/team role` holds since #381."""
    db_path = await _make_db(tmp_path)

    _bot, asked, _guild = await _set(db_path, "driver_role_id", _role(above_the_bot=True))

    assert "Move my role above it" in _reply(asked)
    assert (await _stored(db_path))["driver_role_id"] == OLD_ROLE
    assert await _audit_rows(db_path) == []


async def test_a_base_role_the_bot_cannot_grant_is_still_set(tmp_path):
    """The league grants the base role, not the bot, so where it sits in the role list is not
    the bot's concern."""
    db_path = await _make_db(tmp_path)

    await _set(db_path, "base_role_id", _role(above_the_bot=True))

    assert (await _stored(db_path))["base_role_id"] == NEW_ROLE


def test_nothing_current_names_a_withdrawn_signup_role_command():
    """`/signup base-role`, `/signup complete-role` and `/signup config roles` were withdrawn
    when the roles became the league's (issue #276). A reply or a guide naming one sends a
    league to a command Discord no longer offers. `specs/` and the constitution's sync
    reports are historical records and are not read."""
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[2]
    withdrawn = re.compile(r"/signup (?:base-role|complete-role|config roles)\b")
    paths = [
        *sorted((root / "src").rglob("*.py")),
        root / "README.md",
        *sorted((root / "docs" / "how-to").glob("*.md")),
        *sorted((root / "docs" / "wip-specs").glob("*.md")),
    ]
    offenders = sorted(
        str(path.relative_to(root))
        for path in paths
        if withdrawn.search(path.read_text(encoding="utf-8"))
    )
    assert offenders == []
