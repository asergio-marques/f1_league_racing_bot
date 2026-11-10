"""`/test-mode set-former-driver` on the change queue (#283).

The command is a change on the league's lane: the driver named at the ask by the account the
league admin picked, the value carried with it, the queue's acknowledgement its first reply, and
one save setting the driver's former-driver flag and writing its `TEST_FORMER_DRIVER_FLAG_SET`
audit entry, stamped with the queue's clock, then the reply and the line in the words the command
used before the queue. Test mode, and a profile for the account named, are judged at the ask and
again as the change starts. Nothing follows the save, so a fault in it ends the change with
nothing saved rather than stopping the lane.

These run on the bot the entry point builds, its configuration read from the database, with the
real queue and "now" pinned: the server in test mode, and Racer (4242) a driver holding a profile.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

# Imported as a module: pytest tries to collect a bare `TestModeCog` name as a test class.
from leaguebot.core.cogs import test_mode_cog
from leaguebot.core.db.database import get_connection, run_migrations
from tests.support.change_queue import (
    Clock,
    change_rows,
    league_bot,
    league_queue,
    member,
    run_queue,
    seed_server,
    step_rows,
    stopped_job,
)
from tests.support.driver_transitions import reassigned
from tests.support.settings_league import audit_rows, last_said, said, unruled
from tests.support.settings_league import interaction as member_asks
from tests.support.setup_rounds import acknowledged
from tests.support.undecorate import undecorate

#: What is not yet true of the command.
ON_THE_QUEUE = "#283: /test-mode set-former-driver is not yet a change on the queue"

ACTOR_ID = 77
_NAMED = f"Admin (`<@{ACTOR_ID}>`)"
NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)

RACER = "4242"
#: The account Racer moved to, in the case naming them by the one they left.
RACER_NOW = "5353"

_AUDIT_TYPE = "TEST_FORMER_DRIVER_FLAG_SET"
_NOT_IN_TEST_MODE = "⛔ This command is only available when test mode is enabled."
_NO_PROFILE = f"⛔ No driver profile found for user {RACER} on this server."


@pytest.fixture
async def league(tmp_path):
    """The league's server in test mode, on the bot the entry point builds, its real change
    queue's "now" pinned to `NOW`; `cog` is the test-mode cog on it."""
    db_path = str(tmp_path / "former_driver.db")
    await run_migrations(db_path)
    await seed_server(db_path)
    async with get_connection(db_path) as db:
        await db.execute("UPDATE server_configs SET test_mode_active = 1")
        await db.commit()
    bot = await league_bot(db_path, real_config=True)
    league_queue(bot, db_path, now=Clock(NOW))
    cog = test_mode_cog.TestModeCog.__new__(test_mode_cog.TestModeCog)
    cog.bot = bot
    return SimpleNamespace(bot=bot, cog=cog, db_path=db_path)


async def _seed_racer(db_path: str, *, former: bool) -> None:
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO driver_profiles (discord_user_id, current_state, former_driver) "
            "VALUES (?, 'UNASSIGNED', ?)",
            (RACER, int(former)),
        )
        await db.commit()


def _racer():
    """Racer as the command's `user` option gives them: the member the league admin picked."""
    return SimpleNamespace(id=int(RACER), display_name="Racer", mention=f"<@{RACER}>")


def _asks(league):
    """Admin's (77) interaction for `/test-mode set-former-driver`, recording every word they are
    told."""
    return member_asks(
        league.bot, "test-mode set-former-driver",
        user=member(ACTOR_ID, "Admin", "Admin#0001"),
    )


async def _ask(league, interaction, value: bool) -> None:
    """Admin runs the command for Racer with *value*, and nothing more: the queue is not run."""
    await undecorate(test_mode_cog.TestModeCog.set_former_driver)(
        league.cog, interaction, _racer(), value
    )


async def _flags(db_path: str) -> list[bool]:
    """The former-driver flag of every profile, by id."""
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT former_driver FROM driver_profiles ORDER BY id")
        return [bool(row["former_driver"]) for row in await cursor.fetchall()]


def _lines(league) -> list[str]:
    """Every line the log channel took, without the rule drawn beneath each."""
    return [unruled(str(line)) for line in league.bot.log_channel.sent]


def _doing(value: bool) -> str:
    return f"Setting **Racer**'s former-driver flag to **{value}**"


@pytest.mark.xfail(strict=True, reason=ON_THE_QUEUE)
@pytest.mark.parametrize(
    "former, value, past_account",
    [
        pytest.param(False, True, False, id="set"),
        pytest.param(True, False, False, id="cleared"),
        pytest.param(False, True, True, id="through an account Racer has left"),
    ],
)
async def test_the_flag_and_its_audit_entry_and_line_are_one_save(
    league, former, value, past_account
):
    """The core specification's former-driver flag set by hand under test mode, and its record of
    what changed. The server is in test mode; Racer holds a profile whose flag is *former*, and in
    the third case has since moved to another account, the one Admin picks being the account
    Racer left. Admin (77) runs `/test-mode set-former-driver` for Racer with *value*. They are
    answered at once with the queue's acknowledgement, "Setting **Racer**'s former-driver flag to
    **<value>**", naming its first job, and nothing is written until the queue runs the change, of
    kind `core.test_mode_former_driver`. Once it has, Racer's flag reads *value*; one audit entry,
    `TEST_FORMER_DRIVER_FLAG_SET`, by Admin, on no division, stamped with the queue's clock,
    records the flag before and after; the acknowledgement reads today's reply, naming Racer and
    both values; and the log channel holds today's success line once."""
    await _seed_racer(league.db_path, former=former)
    if past_account:
        await reassigned(league.db_path, RACER, RACER_NOW)
    interaction = _asks(league)

    await _ask(league, interaction, value)

    first_job = (await step_rows(league.db_path))[0]["id"]
    assert said(interaction)[0] == f"{acknowledged(_doing(value))} It begins with job #{first_job}."
    assert [row["kind"] for row in await change_rows(league.db_path)] == [
        "core.test_mode_former_driver"
    ]
    assert await _flags(league.db_path) == [former]
    assert await audit_rows(league.db_path) == []
    await run_queue(league.bot)

    assert await _flags(league.db_path) == [value]
    assert [
        (row["change_type"], row["old_value"], row["new_value"],
         row["actor_id"], row["division_id"], row["timestamp"])
        for row in await audit_rows(league.db_path)
    ] == [(
        _AUDIT_TYPE, {"former_driver": former}, {"former_driver": value},
        ACTOR_ID, None, NOW.isoformat(),
    )]
    assert last_said(interaction) == (
        "✅ former_driver flag updated.\n"
        "   User     : Racer\n"
        f"   Old value: {former}\n"
        f"   New value: {value}"
    )
    assert _lines(league) == [
        f"{_NAMED} | /test-mode set-former-driver | Success\n"
        f"  user: Racer (`<@{RACER}>`)\n"
        f"  former_driver: {former} -> {value}"
    ]


async def test_a_member_with_no_profile_is_refused_at_the_ask(league):
    """The server is in test mode and Racer holds no profile. Admin (77) runs the command for
    Racer: they are refused at once, seen by them alone, in the words the command has always
    used, naming the account; nothing is queued or audited, and the log channel takes the refusal
    line."""
    interaction = _asks(league)

    await _ask(league, interaction, True)

    assert said(interaction) == [_NO_PROFILE]
    [reply] = interaction.response.send_message.await_args_list
    assert reply.kwargs.get("ephemeral") is True
    assert await change_rows(league.db_path) == []
    assert await audit_rows(league.db_path) == []
    assert _lines(league) == [
        f"⛔ `/test-mode set-former-driver` refused for {_NAMED} — {_NO_PROFILE[2:]}"
    ]


@pytest.mark.xfail(strict=True, reason=ON_THE_QUEUE)
@pytest.mark.parametrize("meanwhile", ["test mode turned off", "profile deleted"])
async def test_it_is_judged_again_as_it_starts(league, meanwhile):
    """The server is in test mode; Racer holds a profile whose flag is false. Admin (77) runs the
    command for Racer with true and is acknowledged; before the queue runs it, test mode is
    switched off, or Racer's profile is deleted. The change judges again as it starts and is
    refused: nothing is saved or audited, Admin's reply becomes the refusal the command gives at
    the ask, word for word, the log channel takes that refusal as its only line, and the queue is
    not stopped."""
    await _seed_racer(league.db_path, former=False)
    interaction = _asks(league)

    await _ask(league, interaction, True)
    assert said(interaction)[0].startswith("⏳ ")
    async with get_connection(league.db_path) as db:
        if meanwhile == "test mode turned off":
            await db.execute("UPDATE server_configs SET test_mode_active = 0")
        else:
            await db.execute("DELETE FROM driver_profiles")
        await db.commit()
    await run_queue(league.bot)

    refusal = _NOT_IN_TEST_MODE if meanwhile == "test mode turned off" else _NO_PROFILE
    assert last_said(interaction) == refusal
    assert await _flags(league.db_path) == ([False] if meanwhile == "test mode turned off" else [])
    assert await audit_rows(league.db_path) == []
    assert _lines(league) == [
        f"⛔ `/test-mode set-former-driver` refused for {_NAMED} — {refusal[2:]}"
    ]
    assert await stopped_job(league.db_path) is None


@pytest.mark.xfail(strict=True, reason=ON_THE_QUEUE)
async def test_a_fault_in_the_save_saves_neither_the_flag_nor_its_audit(league, monkeypatch):
    """The server is in test mode; Racer holds a profile whose flag is false. Admin (77) runs the
    command for Racer with true, and the audit entry's write fails on the database as the queue
    runs the change. The flag is not saved either: it is still false and no audit entry stands.
    Admin is told the command stopped on a fault in the bot and to run it again; the log channel
    gets the one failure line and no success line; the queue is not stopped, nothing waiting on
    the save."""
    from leaguebot.core.services import change_queue

    await _seed_racer(league.db_path, former=False)
    monkeypatch.setattr(
        change_queue, "record_change_on",
        AsyncMock(side_effect=sqlite3.OperationalError("disk I/O error")),
    )
    interaction = _asks(league)

    await _ask(league, interaction, True)
    await run_queue(league.bot)

    assert await _flags(league.db_path) == [False]
    assert await audit_rows(league.db_path) == []
    reply = last_said(interaction)
    assert reply.startswith(
        "❌ `/test-mode set-former-driver` stopped on a fault in the bot, not on anything you "
        "entered, and did not finish."
    )
    assert "Run `/test-mode set-former-driver` again" in reply
    assert _lines(league) == [
        f"❌ `/test-mode set-former-driver` failed for {_NAMED} — OperationalError. The details "
        f"are in the host's log."
    ]
    assert await stopped_job(league.db_path) is None
