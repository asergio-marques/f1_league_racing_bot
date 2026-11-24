"""`/test-mode roster sign-up-bulk` on the change queue (#600).

A league admin's helper for building a test season quickly: it opens a box taking a pasted list,
`ID,Driver name,Nationality`, the nationality blank where none is wanted; submitting it is one
change on the league's lane, `core.test_roster_sign_up_bulk`, and one save, all or nothing, which
creates each fake driver at Not Signed Up as `add-bulk` does, and through signup's subscriber
records a submitted signup with the default answers to the open window and approves it, the driver
ending Unassigned with no seat. It obeys the signup flow: it is refused unless signups are open,
the signup module on and configured with at least one availability slot. No channel, wizard,
review panel, role, post or timer is made. Seating stays `/driver assign`.

These run on `setup_league`'s league at Signups with the window open (`SIGNUP_CHANNEL`), test
mode on and its nationality switch on, two availability slots (Monday 19:00 and Wednesday 20:00)
and one open window of season 3. Admin (77) holds the league admin role.
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock

import discord
import pytest
from discord import app_commands

# Imported as a module: pytest tries to collect a bare `TestModeCog` name as a test class.
from leaguebot.core.cogs import test_mode_cog
from tests.support.change_queue import change_rows, member, run_queue, stopped_job
from tests.support.season_league import SEASON_ID, setup_league
from tests.support.settings_league import audit_rows, last_said, said, unruled
from tests.support.settings_league import interaction as member_asks

#: Why each test here fails until the helper is built.
_NOT_BUILT = "#600: /test-mode roster sign-up-bulk is not yet built"

ACTOR_ID = 77
_NAMED = f"Admin (`<@{ACTOR_ID}>`)"
_COMMAND = "test-mode roster sign-up-bulk"

ALPHA, BETA = "9000000000000000001", "9000000000000000002"

#: Mock Alpha, British, and Mock Beta, its nationality blank.
SIGN_UPS = (
    "ID,Driver name,Nationality\n"
    f"{ALPHA},Mock Alpha,British\n"
    f"{BETA},Mock Beta,\n"
)
#: The same two with no nationality given.
NO_NATIONALITY = (
    "ID,Driver name,Nationality\n"
    f"{ALPHA},Mock Alpha,\n"
    f"{BETA},Mock Beta,\n"
)

_SIGNED_UP = "✅ Signed up and approved **2** fake driver(s). Seat each with `/driver assign`."
_DISCARDED = (
    "Nothing was added: no fake driver was created. Run `/test-mode roster sign-up-bulk` again."
)
_NOT_IN_TEST_MODE = "⛔ This command is only available when test mode is enabled."
_ALPHA_TAKEN = f"Line 2: a driver with ID {ALPHA} is already on this server."


@pytest.fixture
async def league(tmp_path):
    """The league of the module docstring; `cog` is the test-mode cog on its bot and `window` the
    id of the open window."""
    built = await setup_league(tmp_path, stage="SIGNUPS", signups_open=True)
    await built.set_test_mode(True)
    await built.write("UPDATE server_configs SET test_mode_nationality_required = 1")
    for day, time in ((0, "19:00"), (2, "20:00")):
        await built.write(
            "INSERT OR IGNORE INTO signup_availability_slots (day_of_week, time_hhmm) "
            "VALUES (?, ?)", day, time,
        )
    if not await _open_window(built):
        await built.write(
            "INSERT INTO signup_windows (season_id, opened_at) VALUES (?, ?)",
            SEASON_ID, built.clock.now.isoformat(),
        )
    [built.window] = await _open_window(built)
    cog = test_mode_cog.TestModeCog.__new__(test_mode_cog.TestModeCog)
    cog.bot = built.bot
    built.cog = cog
    return built


async def _open_window(league) -> list[int]:
    rows = await league.rows(
        "SELECT id FROM signup_windows WHERE season_id = ? AND closed_at IS NULL", SEASON_ID
    )
    return [row["id"] for row in rows]


def _walk(objects):
    for obj in objects:
        if isinstance(obj, app_commands.Group):
            yield from _walk(obj.commands)
        else:
            yield obj


def _command() -> Any:
    """The `/test-mode roster sign-up-bulk` command, off the test-mode cog."""
    from tests.support.undecorate import undecorate

    found = [
        command for command in _walk(test_mode_cog.TestModeCog.__cog_app_commands__)
        if command.qualified_name == _COMMAND
    ]
    assert len(found) == 1, f"/{_COMMAND} is not declared"
    return undecorate(found[0])


def _asks(league):
    """Admin's (77) interaction, recording every word they are told."""
    asked = member_asks(league.bot, user=member(ACTOR_ID, "Admin", "Admin#0001"))
    asked.response.send_modal = AsyncMock()
    return asked


async def _submit(league, interaction, text: str = SIGN_UPS) -> None:
    """Admin runs `/test-mode roster sign-up-bulk`, and submits the box it opens with *text*
    pasted in, and nothing more: the queue is not run."""
    opening = _asks(league)
    await _command()(league.cog, opening)
    [form] = opening.response.send_modal.await_args.args
    [box] = [child for child in form.children if isinstance(child, discord.ui.TextInput)]
    box._value = text
    await form.on_submit(interaction)


async def _drivers(league) -> list[dict]:
    """Every fake driver: its id, name and state, and the team it is seated in, if any."""
    return await league.rows(
        "SELECT p.discord_user_id, p.test_display_name, p.current_state, s.team_instance_id "
        "FROM driver_profiles p LEFT JOIN team_seats s ON s.driver_profile_id = p.id "
        "WHERE p.is_test_driver = 1 AND p.discord_user_id IN (?, ?) ORDER BY p.id",
        ALPHA, BETA,
    )


async def _signups(league) -> list[dict]:
    return await league.rows(
        "SELECT * FROM signup_records WHERE discord_user_id IN (?, ?) ORDER BY id", ALPHA, BETA
    )


def _lines(league) -> list[str]:
    """Every line the log channel took, without the rule drawn beneath each."""
    return [unruled(str(line)) for line in league.bot.log_channel.sent]


async def _nothing_written(league) -> None:
    assert await _drivers(league) == []
    assert await _signups(league) == []
    assert await audit_rows(league.db_path) == []


@pytest.mark.xfail(strict=True, reason=_NOT_BUILT)
async def test_a_pasted_list_signs_up_and_approves_every_driver_in_one_save(league):
    """The core specification's test mode: a fake driver signs up as any driver, and the helper
    is a second way through the signup. Signups are open. Admin (77) runs `/test-mode roster
    sign-up-bulk` and submits Mock Alpha, British, and Mock Beta, its nationality blank, and the
    queue runs one change, `core.test_roster_sign_up_bulk`. Both are fake drivers with the IDs the
    list names, at Unassigned, seated nowhere. Each holds one approved signup to season 3 and the
    open window, made at the queue's clock, with the default answers: its nationality (British, or
    none), platform Steam, its name as platform ID, every availability slot, Full-Time Driver, no
    preferred teams, teammate, lap times or notes, and its test name as username and display name.
    No wizard row is made for either. Each driver has three audit entries in turn, by Admin at the
    queue's clock: its addition, its signup's start (Not Signed Up to Pending Signup Completion)
    and its approval (Pending Admin Approval to Unassigned). The reply reads "✅ Signed up and
    approved **2** fake driver(s). Seat each with `/driver assign`.", and the log channel holds one
    line, counting the two drivers signed up."""
    from leaguebot.core.utils.input_validator import parse_nationality
    from leaguebot.signup.services.signup_module_service import slots_on
    from leaguebot.framework.db.database import get_connection

    interaction = _asks(league)

    await _submit(league, interaction)
    await run_queue(league.bot)

    assert [row["kind"] for row in await change_rows(league.db_path)] == [
        "core.test_roster_sign_up_bulk"
    ]
    assert await _drivers(league) == [
        {"discord_user_id": uid, "test_display_name": name, "current_state": "UNASSIGNED",
         "team_instance_id": None}
        for uid, name in ((ALPHA, "Mock Alpha"), (BETA, "Mock Beta"))
    ]
    async with get_connection(league.db_path) as db:
        slots = [slot.slot_id for slot in await slots_on(db)]
    assert len(slots) >= 2
    signups = await _signups(league)
    assert [
        (row["discord_user_id"], row["season_id"], row["window_id"], row["approved"],
         row["created_at"], row["discord_username"], row["server_display_name"],
         row["nationality"], row["platform"], row["platform_id"],
         json.loads(row["availability_slot_ids"]), row["driver_type"],
         json.loads(row["preferred_teams"] or "[]"), row["preferred_teammate"] or None,
         json.loads(row["lap_times_json"] or "{}"), row["notes"] or None)
        for row in signups
    ] == [
        (uid, SEASON_ID, league.window, 1, league.clock.now.isoformat(), name, name,
         nationality, "Steam", name, slots, "Full-Time Driver", [], None, {}, None)
        for uid, name, nationality in (
            (ALPHA, "Mock Alpha", parse_nationality("British")), (BETA, "Mock Beta", None),
        )
    ]
    assert await league.rows(
        "SELECT 1 FROM signup_wizard_records WHERE discord_user_id IN (?, ?)", ALPHA, BETA
    ) == []
    entries = await audit_rows(league.db_path)
    assert {(row["actor_id"], row["timestamp"]) for row in entries} == {
        (ACTOR_ID, league.clock.now.isoformat())
    }
    for uid, name in ((ALPHA, "Mock Alpha"), (BETA, "Mock Beta")):
        mine = [
            (row["change_type"], row["old_value"], row["new_value"]) for row in entries
            if uid in (str((row["old_value"] or {}).get("discord_user_id")),
                       str((row["new_value"] or {}).get("user_id")))
        ]
        assert mine == [
            ("TEST_DRIVER_ADDED", {"driver": None, "user_id": None},
             {"driver": name, "user_id": uid}),
            ("SIGNUP_START", {"state": "NOT_SIGNED_UP", "discord_user_id": uid},
             {"new_state": "PENDING_SIGNUP_COMPLETION"}),
            ("SIGNUP_APPROVE", {"state": "PENDING_ADMIN_APPROVAL", "discord_user_id": uid},
             {"new_state": "UNASSIGNED"}),
        ]
    assert len(entries) == 6
    assert last_said(interaction) == _SIGNED_UP
    assert _lines(league) == [
        f"{_NAMED} | /test-mode roster sign-up-bulk | Success\n"
        "  drivers_signed_up: 2"
    ]


async def _test_mode_off(league) -> None:
    await league.set_test_mode(False)


async def _signups_closed(league) -> None:
    await league.write("UPDATE signup_module_config SET signups_open = 0")
    await league.write(
        "UPDATE signup_windows SET closed_at = ? WHERE closed_at IS NULL",
        league.clock.now.isoformat(),
    )


async def _signup_module_off(league) -> None:
    await league.switch("signup", False)


async def _signup_unconfigured(league) -> None:
    await league.write("DELETE FROM signup_module_config")


async def _no_slot(league) -> None:
    await league.write("DELETE FROM signup_availability_slots")


async def _alpha_taken(league) -> None:
    """A fake driver, Mock Zero, holds Mock Alpha's ID."""
    await league.write(
        "INSERT INTO driver_profiles (discord_user_id, current_state, is_test_driver, "
        "test_display_name) VALUES (?, 'NOT_SIGNED_UP', 1, 'Mock Zero')",
        ALPHA,
    )


async def _nationality_switch_off(league) -> None:
    await league.write("UPDATE server_configs SET test_mode_nationality_required = 0")


#: Each thing that refuses the helper, by what sets it up, the list pasted and a part of the
#: refusal's words, where the plan fixes them ("⛔" where it fixes only that one is given).
_REFUSALS = [
    pytest.param(_test_mode_off, SIGN_UPS, _NOT_IN_TEST_MODE, id="test mode off"),
    pytest.param(_signups_closed, SIGN_UPS, "⛔", id="signups not open"),
    pytest.param(_signup_module_off, SIGN_UPS, "⛔", id="the signup module off"),
    pytest.param(_signup_unconfigured, SIGN_UPS, "⛔", id="the signup module unconfigured"),
    pytest.param(_no_slot, SIGN_UPS, "⛔", id="no availability slot"),
    pytest.param(_alpha_taken, SIGN_UPS, _ALPHA_TAKEN, id="an ID already held"),
    pytest.param(
        _nationality_switch_off, SIGN_UPS, "/test-mode nationality",
        id="a nationality while the switch is off",
    ),
]


@pytest.mark.xfail(strict=True, reason=_NOT_BUILT)
@pytest.mark.parametrize("setup, text, words", _REFUSALS)
async def test_a_list_it_cannot_sign_up_is_refused_at_the_ask_writing_nothing(
    league, setup, text, words
):
    """Admin (77) opens `/test-mode roster sign-up-bulk`'s box while signups are open, then, before
    submitting it: test mode is turned off; the window closes; the signup module is switched off;
    its configuration is gone; its availability slots are gone; a fake driver Mock Zero comes to
    hold Mock Alpha's ID; or the test-mode nationality switch is turned off, Mock Alpha's line
    naming British. Admin submits Mock Alpha and Mock Beta. They are refused at once, seen by them
    alone, the fault named; nothing is queued, no driver, signup or audit entry is written."""
    interaction = _asks(league)
    opening = _asks(league)
    await _command()(league.cog, opening)
    [form] = opening.response.send_modal.await_args.args
    [box] = [child for child in form.children if isinstance(child, discord.ui.TextInput)]
    box._value = text
    await setup(league)

    await form.on_submit(interaction)

    [refusal] = said(interaction)
    assert refusal.startswith("⛔") and words in refusal
    assert interaction.followup.send.await_args.kwargs.get("ephemeral") is True
    assert await change_rows(league.db_path) == []
    if setup is not _alpha_taken:
        await _nothing_written(league)
    else:
        assert [row["test_display_name"] for row in await _drivers(league)] == ["Mock Zero"]
        assert await _signups(league) == []


@pytest.mark.xfail(strict=True, reason=_NOT_BUILT)
async def test_a_list_without_nationalities_is_taken_while_the_switch_is_off(league):
    """The test-mode nationality switch is off and signups are open. Admin (77) submits Mock Alpha
    and Mock Beta with their nationality column blank, and the queue runs: both end Unassigned,
    holding approved signups with no nationality."""
    await _nationality_switch_off(league)
    interaction = _asks(league)

    await _submit(league, interaction, NO_NATIONALITY)
    await run_queue(league.bot)

    assert [row["current_state"] for row in await _drivers(league)] == ["UNASSIGNED"] * 2
    assert [(row["approved"], row["nationality"]) for row in await _signups(league)] == [
        (1, None), (1, None),
    ]
    assert last_said(interaction) == _SIGNED_UP


@pytest.mark.xfail(strict=True, reason=_NOT_BUILT)
@pytest.mark.parametrize(
    "meanwhile, words",
    [
        pytest.param(_test_mode_off, _NOT_IN_TEST_MODE, id="test mode turned off"),
        pytest.param(_signups_closed, "⛔", id="signups closed"),
    ],
)
async def test_it_is_judged_again_as_it_starts(league, meanwhile, words):
    """Signups are open. Admin (77) submits Mock Alpha and Mock Beta and is acknowledged; before
    the queue runs it, test mode is switched off, or the window closes. The change judges again as
    it starts and is refused: Admin's reply becomes the refusal, naming the fault; no driver,
    signup or audit entry is written, the log channel takes only the refusal line, and the queue
    is not stopped."""
    interaction = _asks(league)

    await _submit(league, interaction)
    assert said(interaction)[0].startswith("⏳ ")
    await meanwhile(league)
    await run_queue(league.bot)

    refusal = last_said(interaction)
    assert refusal.startswith("⛔") and words in refusal
    await _nothing_written(league)
    assert not any("| Success" in line for line in _lines(league))
    assert len(_lines(league)) == 1
    assert await stopped_job(league.db_path) is None


@pytest.mark.xfail(strict=True, reason=_NOT_BUILT)
async def test_a_fault_in_the_save_creates_and_signs_up_nobody(league):
    """Signups are open. Admin (77) submits Mock Alpha and Mock Beta, and an audit entry's write
    fails on the database as the queue runs the change. Nothing is saved: no fake driver, no
    signup, no audit entry, no success line; the league's lane stops at the job. Once a league
    admin discards it, Admin's reply says "Nothing was added: no fake driver was created. Run
    `/test-mode roster sign-up-bulk` again.", the error's text reaching nobody, and the lane is
    clear."""
    from tests.support.change_queue import discard_job
    from tests.support.settings_league import fault_in

    interaction = _asks(league)

    await _submit(league, interaction)
    with fault_in("leaguebot.framework.services.queue.change_queue.record_change_on"):
        await run_queue(league.bot)

    await _nothing_written(league)
    assert not any("| Success" in line for line in _lines(league))
    assert await stopped_job(league.db_path) is not None

    await discard_job(league.bot)

    assert last_said(interaction) == _DISCARDED
    assert not any("database or disk is full" in text for text in said(interaction))
    assert await stopped_job(league.db_path) is None
