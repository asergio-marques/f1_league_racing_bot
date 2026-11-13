"""`/signup config view` and the three signup settings toggles.

Issue #208. These are the commands a league manager uses to set up signups before opening them,
and none was executed by any test.

**The three toggles all write an audit entry, and that is what these tests are chiefly about.**
Each flips a boolean or swaps a string, which is barely worth pinning on its own — but each
also records *what it was and what it became* in `audit_entries`, and that record is the
league's only account of who changed a signup rule mid-season. The three insert statements are
copied between the commands rather than shared, so each is tested for both halves of its own
entry: a toggle recording the same value as old and new would look right in every other
respect.

They run against a real migrated database for exactly that reason. The audit insert is raw SQL
with eight bound parameters and a JSON payload; a double would accept any of them.

**`config view` is a report, and the thing that makes it hard is that every setting has three
states, not two.** A channel or role can be unset, set-and-present, or set-and-since-deleted —
and the third is the one a league needs told about, because a signup channel pointing at a
deleted channel looks configured and works like nothing. `test_a_deleted_channel_is_reported_
as_not_found` and its role counterpart sit on that distinction; reporting "not configured" for
a deleted channel would send a manager looking for a setting they had already made.
"""
from __future__ import annotations

import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from leaguebot.signup.cogs.signup_cog import SignupCog
from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.services.settings.config_service import ConfigService
from leaguebot.signup.services.signup_module_service import SignupModuleService
from discord import app_commands

from leaguebot.core.services.queue.setting_change import NOTHING_SAVED
from leaguebot.core.utils.interaction_errors import failure_reply
from tests.support.change_queue import MEMBER_ID, run_queue
from tests.support.settings_league import (
    fault_in,
    interaction,
    last_said,
    log_lines,
    said,
    settings_league,
)
from tests.support.undecorate import undecorate

SERVER_ID = 8908
ACTOR_ID = MEMBER_ID
#: The member as a log line names them.
NAMED = "Admin"
CHANNEL_ID = 777001
BASE_ROLE_ID = 555001
DRIVER_ROLE_ID = 555002


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _make_db(tmp_path, *, config: bool = True) -> str:
    db_path = os.path.join(str(tmp_path), "signup_config.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        # The two roles are the league's, on the server configuration (issue #276).
        await db.execute(
            "INSERT INTO server_configs "
            "(server_id, interaction_role_id, interaction_channel_id, log_channel_id, "
            " base_role_id, driver_role_id) "
            "VALUES (?, 100, 200, 300, ?, ?)",
            (SERVER_ID, BASE_ROLE_ID, DRIVER_ROLE_ID),
        )
        if config:
            await db.execute(
                "INSERT INTO signup_module_config "
                "(id, signup_channel_id, signups_open) VALUES (?, ?, 0)",
                (1, CHANNEL_ID),
            )
        await db.commit()
    return db_path


def _make_cog(db_path: str) -> SignupCog:
    bot = MagicMock()
    bot.db_path = db_path
    bot.module_service.is_signup_enabled = AsyncMock(return_value=True)
    bot.signup_module_service = SignupModuleService(db_path)
    bot.config_service = ConfigService(db_path)
    bot.output_router = MagicMock()
    bot.output_router.post_log = AsyncMock(return_value=None)
    return SignupCog(bot)


def _interaction(*, channel=..., role=...):
    """An interaction whose guild resolves the configured channel and roles.

    `...` means "present"; `None` means the object was deleted after being configured,
    which is the third state `config view` has to distinguish.
    """
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.user = MagicMock()
    interaction.user.id = ACTOR_ID
    interaction.user.display_name = "Manager"
    interaction.user.__str__ = lambda self: "Manager#0001"  # type: ignore[assignment]

    resolved_channel = MagicMock() if channel is ... else channel
    if resolved_channel is not None:
        resolved_channel.mention = "#signups"
    resolved_role = MagicMock() if role is ... else role
    if resolved_role is not None:
        resolved_role.mention = "@role"

    guild = MagicMock()
    guild.get_channel = MagicMock(return_value=resolved_channel)
    guild.get_role = MagicMock(return_value=resolved_role)
    interaction.guild = guild

    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    return interaction


async def _audit(db_path: str) -> list[dict]:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT change_type, old_value, new_value, actor_id FROM audit_entries "
            " ORDER BY id",
        )
        return [dict(r) for r in await cursor.fetchall()]


def _fields(interaction) -> dict[str, str]:
    embed = interaction.response.send_message.await_args.kwargs["embed"]
    return {f.name: f.value for f in embed.fields}


# ---------------------------------------------------------------------------
# The toggles
# ---------------------------------------------------------------------------


TOGGLES = [
    ("nationality", "nationality_required"),
    ("time_image", "time_image_required"),
]

#: Each toggle's reply once run from the defaults, as the manager reads it.
_TOGGLED = {
    "nationality": "✅ Nationality requirement: **OFF**.",
    "time_type": "✅ Time type: **Short Qualification**.",
    "time_image": "✅ Time image requirement: **OFF**.",
}

#: Why each test pinning the settings on the queue fails until they are (#286, item 11).
_ON_THE_QUEUE = "#286: the signup settings are not yet asked of the change queue"


async def _league(tmp_path, *, config: bool = True, slots=()):
    """The settings league with signup alone on (`settings_league`), holding the module's
    configuration where *config*, and each of *slots* (day, "HH:MM") saved."""
    league = await settings_league(tmp_path, modules=("signup",))
    async with get_connection(league.db_path) as db:
        if config:
            await db.execute(
                "INSERT INTO signup_module_config (id, signup_channel_id, signups_open) "
                "VALUES (1, ?, 0)",
                (CHANNEL_ID,),
            )
        for day, time_hhmm in slots:
            await db.execute(
                "INSERT INTO signup_availability_slots (day_of_week, time_hhmm) VALUES (?, ?)",
                (day, time_hhmm),
            )
        await db.commit()
    return league


async def _ask(league, command: str, *args):
    """Run `/signup <command>` (`SignupCog.<command>`) as the member, the queue not yet run."""
    name = {"time_slot_add": "time-slot add", "time_slot_remove": "time-slot remove"}.get(
        command, command.replace("_", "-")
    )
    asked = interaction(league.bot, f"signup {name}")
    await undecorate(getattr(SignupCog, command))(SignupCog(league.bot), asked, *args)
    return asked


async def _run(league, command: str, *args):
    """Run `/signup <command>` as the member, then the queue."""
    asked = await _ask(league, command, *args)
    await run_queue(league.bot)
    return asked


async def _settings(league):
    return await league.bot.signup_module_service.get_settings()


async def _rows(db_path: str, sql: str) -> list[tuple]:
    async with get_connection(db_path) as db:
        return [tuple(row) for row in await (await db.execute(sql)).fetchall()]


@pytest.mark.parametrize("command,field", TOGGLES, ids=[c for c, _ in TOGGLES])
async def test_a_toggle_flips_the_setting(tmp_path, command, field):
    league = await _league(tmp_path)
    before = getattr(await _settings(league), field)

    await _run(league, command)

    after = getattr(await _settings(league), field)
    assert after is not before


@pytest.mark.parametrize("command,field", TOGGLES, ids=[c for c, _ in TOGGLES])
async def test_a_toggle_flips_back(tmp_path, command, field):
    """A toggle that only ever turned something on would leave a league unable to undo it."""
    league = await _league(tmp_path)
    before = getattr(await _settings(league), field)

    await _run(league, command)
    await _run(league, command)

    after = getattr(await _settings(league), field)
    assert after is before


@pytest.mark.parametrize("command,field", TOGGLES, ids=[c for c, _ in TOGGLES])
async def test_a_toggle_records_what_it_was_and_what_it_became(tmp_path, command, field):
    """The league's only account of who changed a signup rule mid-season. An entry naming
    the same value twice would pass any test that merely counted the rows."""
    league = await _league(tmp_path)

    await _run(league, command)

    entries = await _audit(league.db_path)
    assert len(entries) == 1
    entry = entries[0]
    assert entry["change_type"] == "SIGNUP_SETTINGS_CHANGE"
    assert entry["actor_id"] == ACTOR_ID
    old = json.loads(entry["old_value"])
    new = json.loads(entry["new_value"])
    assert old["field"] == field
    assert new["field"] == field
    assert old["value"] is not new["value"]


async def test_the_time_type_command_swaps_between_the_two_kinds(tmp_path):
    """Not a boolean — the two values are the two ways a league can ask for a lap time,
    and they are set in different places in the game."""
    league = await _league(tmp_path)
    before = (await _settings(league)).time_type

    await _run(league, "time_type")
    after = (await _settings(league)).time_type

    assert {before, after} == {"TIME_TRIAL", "SHORT_QUALIFICATION"}


async def test_the_time_type_command_records_both_values(tmp_path):
    league = await _league(tmp_path)

    await _run(league, "time_type")

    entry = (await _audit(league.db_path))[0]
    assert json.loads(entry["old_value"])["field"] == "time_type"
    assert json.loads(entry["old_value"])["value"] != json.loads(entry["new_value"])["value"]


@pytest.mark.xfail(strict=True, reason=_ON_THE_QUEUE)
@pytest.mark.parametrize(
    "command", ["nationality", "time_type", "time_image"],
)
async def test_every_toggle_tells_the_manager_and_the_log(tmp_path, command):
    """The toggle is acknowledged at once as under way, and once run the manager reads what it
    became; one Success line names the member and the command."""
    league = await _league(tmp_path)

    asked = await _run(league, command)

    assert said(asked)[0].startswith("⏳ ")
    assert last_said(asked) == _TOGGLED[command]
    [line] = await log_lines(league)
    assert line.startswith(f"{NAMED} (")
    assert f" | /signup {command.replace('_', '-')} | Success" in line.splitlines()[0]


@pytest.mark.parametrize(
    "command", ["nationality", "time_type", "time_image"],
)
async def test_a_server_with_no_settings_row_starts_from_the_defaults(tmp_path, command):
    """`get_settings` invents a default row rather than returning None, so the first
    toggle a league ever runs has something to flip."""
    league = await _league(tmp_path, config=False)

    await _run(league, command)

    assert len(await _audit(league.db_path)) == 1


_SLOTS = "SELECT day_of_week, time_hhmm FROM signup_availability_slots ORDER BY id"
_SETTINGS = ("SELECT nationality_required, time_type, time_image_required "
             "FROM signup_module_settings")
_MONDAY = app_commands.Choice(name="Monday", value="1")

# (command, its arguments, the slots saved first, the rows' query, the rows once saved, the kind
# of its audit entry)
_ONE_SAVE = [
    ("nationality", (), (), _SETTINGS, [(0, "TIME_TRIAL", 1)], "SIGNUP_SETTINGS_CHANGE"),
    ("time_type", (), (), _SETTINGS, [(1, "SHORT_QUALIFICATION", 1)], "SIGNUP_SETTINGS_CHANGE"),
    ("time_image", (), (), _SETTINGS, [(1, "TIME_TRIAL", 0)], "SIGNUP_SETTINGS_CHANGE"),
    ("time_slot_add", (_MONDAY, "20:00"), (), _SLOTS, [(1, "20:00")], "SIGNUP_SLOT_ADD"),
    ("time_slot_remove", (1,), ((1, "20:00"),), _SLOTS, [], "SIGNUP_SLOT_REMOVE"),
]


@pytest.mark.xfail(strict=True, reason=_ON_THE_QUEUE)
@pytest.mark.parametrize(
    "command, args, slots, query, saved, kind", _ONE_SAVE, ids=[case[0] for case in _ONE_SAVE]
)
async def test_a_signup_setting_saves_its_value_and_audit_in_one_save(
    tmp_path, command, args, slots, query, saved, kind
):
    """`/signup nationality`, `time-type`, `time-image`, `time-slot add Monday 20:00` and
    `time-slot remove 1` (Monday 20:00 saved): asked while the audit entry's write faults, the
    value is left as it was, the manager is told nothing was saved and to run it again, and no
    Success line is written; asked again, the value, one audit entry of its kind by the manager
    and one Success line are saved."""
    league = await _league(tmp_path, slots=slots)
    before = await _rows(league.db_path, query)
    name = {"time_slot_add": "time-slot add", "time_slot_remove": "time-slot remove"}.get(
        command, command.replace("_", "-")
    )

    faulted = await _ask(league, command, *args)
    with fault_in("leaguebot.core.services.queue.change_queue.record_change_on"):
        await run_queue(league.bot)

    assert last_said(faulted) == failure_reply(
        f"`/signup {name}`", NOTHING_SAVED.format(command=f"/signup {name}")
    )
    assert await _rows(league.db_path, query) == before
    assert await _audit(league.db_path) == []
    assert not any(" | Success" in line for line in await log_lines(league))

    await _run(league, command, *args)

    assert await _rows(league.db_path, query) == saved
    assert [(e["change_type"], e["actor_id"]) for e in await _audit(league.db_path)] == [
        (kind, ACTOR_ID)
    ]
    assert [line for line in await log_lines(league) if " | Success" in line] != []


@pytest.mark.xfail(strict=True, reason=_ON_THE_QUEUE)
async def test_a_fault_after_a_slot_is_saved_is_not_read_as_a_duplicate(tmp_path):
    """`/signup time-slot add Monday 20:00`, no slot saved, whose audit entry's write raises a
    `ValueError` after the slot's insert: the manager is told nothing was saved and to run it
    again, never that the slot already exists, and no slot is saved."""
    league = await _league(tmp_path)

    asked = await _ask(league, "time_slot_add", _MONDAY, "20:00")
    with fault_in("leaguebot.core.services.queue.change_queue.record_change_on",
                  ValueError("a fault after the slot's insert")):
        await run_queue(league.bot)

    assert not any("already exists" in word for word in said(asked))
    assert last_said(asked) == failure_reply(
        "`/signup time-slot add`", NOTHING_SAVED.format(command="/signup time-slot add")
    )
    assert await _rows(league.db_path, _SLOTS) == []


@pytest.mark.xfail(strict=True, reason=_ON_THE_QUEUE)
async def test_removing_slot_n_twice_removes_one_slot(tmp_path):
    """Monday 19:00 (#1), Wednesday 20:00 (#2) and Friday 21:00 (#3) saved;
    `/signup time-slot remove 1` is asked twice before either runs: Monday alone is removed,
    Wednesday and Friday stand, and one removal is audited."""
    league = await _league(tmp_path, slots=((1, "19:00"), (3, "20:00"), (5, "21:00")))

    await _ask(league, "time_slot_remove", 1)
    await _ask(league, "time_slot_remove", 1)
    await run_queue(league.bot)

    assert await _rows(league.db_path, _SLOTS) == [(3, "20:00"), (5, "21:00")]
    assert [e["change_type"] for e in await _audit(league.db_path)] == ["SIGNUP_SLOT_REMOVE"]


# ---------------------------------------------------------------------------
# config view
# ---------------------------------------------------------------------------


async def test_the_configuration_is_reported(tmp_path):
    """The report names the three settings as well as the channels: the toggles have no
    other read-back, so this report is the only way a league can confirm what it set."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    interaction = _interaction()

    await undecorate(SignupCog.config_view)(cog, interaction)

    fields = _fields(interaction)
    assert fields["Channel"] == "#signups"
    assert fields["Base Role"] == "@role"
    assert fields["Driver Role"] == "@role"
    assert fields["Signups Open"] == "No"
    assert fields["Nationality Required"] in ("ON", "OFF")
    assert fields["Time Type"] in ("Time Trial", "Short Qualification")
    assert fields["Time Image Required"] in ("ON", "OFF")


async def test_a_deleted_channel_is_reported_as_not_found(tmp_path):
    """The third state. A channel that was configured and has since been deleted looks
    configured and works like nothing — reporting it as "not configured" would send a
    manager looking for a setting they had already made."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    interaction = _interaction(channel=None)

    await undecorate(SignupCog.config_view)(cog, interaction)

    assert "not found" in _fields(interaction)["Channel"]


async def test_a_deleted_role_is_reported_as_not_found(tmp_path):
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    interaction = _interaction(role=None)

    await undecorate(SignupCog.config_view)(cog, interaction)

    fields = _fields(interaction)
    assert "not found" in fields["Base Role"]
    assert "not found" in fields["Driver Role"]


async def test_an_unconfigured_channel_is_reported_as_not_configured(tmp_path):
    """Distinct from "not found" — nothing was ever set here."""
    db_path = await _make_db(tmp_path)
    async with get_connection(db_path) as db:
        await db.execute("UPDATE signup_module_config SET signup_channel_id = NULL")
        await db.execute("UPDATE server_configs SET base_role_id = NULL")
        await db.commit()
    cog = _make_cog(db_path)
    interaction = _interaction()

    await undecorate(SignupCog.config_view)(cog, interaction)

    fields = _fields(interaction)
    assert "not configured" in fields["Channel"]
    assert "not configured" in fields["Base Role"]
    assert "/bot base-role" in fields["Base Role"]


async def test_a_server_that_has_configured_nothing_reports_not_set(tmp_path):
    """No configuration row at all — the state a league is in before `/signup channel`."""
    db_path = await _make_db(tmp_path, config=False)
    cog = _make_cog(db_path)
    interaction = _interaction()

    await undecorate(SignupCog.config_view)(cog, interaction)

    fields = _fields(interaction)
    assert fields["Channel"] == "Not set"
    assert fields["Signups Open"] == "No"
    # The league's roles are shown whether or not the module has a configuration.
    assert fields["Base Role"] == "@role"
    assert fields["Driver Role"] == "@role"


async def test_the_report_reflects_a_toggle_that_has_been_flipped(tmp_path):
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    before = _interaction()
    await undecorate(SignupCog.config_view)(cog, before)
    was = _fields(before)["Nationality Required"]

    await undecorate(SignupCog.nationality)(cog, _interaction())
    after = _interaction()
    await undecorate(SignupCog.config_view)(cog, after)

    assert _fields(after)["Nationality Required"] != was


async def test_open_signups_are_reported_as_open(tmp_path):
    db_path = await _make_db(tmp_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE signup_module_config SET signups_open = 1",
        )
        await db.commit()
    cog = _make_cog(db_path)
    interaction = _interaction()

    await undecorate(SignupCog.config_view)(cog, interaction)

    assert _fields(interaction)["Signups Open"] == "Yes"


#: The commands answered off the change queue that wait for the catch-up after a restart (#440):
#: the `/signup` commands that set something, the `/attendance config` setters, `/attendance sync`,
#: `/bot pack`, `/bot factory-reset` and the `/test-mode backup` commands that change something.
_HELD_FOR_THE_CATCH_UP = {
    "signup channel", "signup nationality", "signup time-type", "signup time-image",
    "signup time-slot add", "signup time-slot remove", "signup close-time add",
    "signup close-time cancel", "signup close-time modify",
    "attendance config rsvp-notice", "attendance config rsvp-last-notice",
    "attendance config rsvp-deadline", "attendance config no-rsvp-penalty",
    "attendance config absent-penalty", "attendance config no-show-penalty",
    "attendance config autosack", "attendance config autoreserve", "attendance sync",
    "bot pack", "bot factory-reset",
    "test-mode backup save", "test-mode backup lock", "test-mode backup restore",
}


def _the_wait_of(callback):
    """The wait for the catch-up *callback* wears beneath its guard, or None where it wears none."""
    from leaguebot.core.utils.catch_up_gate import after_the_catch_up

    async def body(self, interaction):
        return None

    waits = after_the_catch_up(lambda cog: None)(body).__code__
    while callback is not None:
        if getattr(callback, "__code__", None) is waits:
            return callback
        callback = getattr(callback, "__wrapped__", None)
    return None


async def test_the_off_queue_commands_wait_for_the_catch_up(monkeypatch):
    """The bot has restarted and holds its change queue for the catch-up. Of every command the
    signup, attendance, bot and test-mode cogs carry, exactly the twenty-three that change the league
    without asking the queue wear the wait, `/attendance sync` among them; `/attendance config show`
    and `/test-mode backup status`, which change nothing, do not. A league manager then invokes one held
    command of each cog (`/signup nationality`, `/attendance config no-rsvp-penalty`, `/bot pack`,
    `/test-mode backup lock`): none acts while the queue is held, and once the catch-up ends and the
    queue is released each acts, once."""
    import asyncio

    from leaguebot.attendance.cogs.attendance_cog import AttendanceCog
    from leaguebot.core.cogs import bot_cog
    from leaguebot.core.cogs.test_mode_cog import TestModeCog
    from leaguebot.core.services.queue.change_queue import ChangeQueue
    from tests.support.change_queue import member_interaction, response_in_its_slot

    bot = MagicMock()
    bot.change_queue = ChangeQueue("unused.db", MagicMock(), MagicMock(), MagicMock())
    attendance, bot_commands, test_mode, signup = cogs = [
        AttendanceCog(bot), bot_cog.BotCog(bot), TestModeCog(bot), SignupCog(bot)
    ]
    commands = {command.qualified_name: (cog, command) for cog in cogs
                for command in cog.walk_app_commands() if hasattr(command, "callback")}

    held = {name for name, (_, command) in commands.items() if _the_wait_of(command.callback)}
    assert held == _HELD_FOR_THE_CATCH_UP
    assert not held & {"attendance config show", "test-mode backup status"}

    acted: list[str] = []

    def acts(name, result=None):
        async def run(*args, **kwargs):
            acted.append(name)
            return result

        return run

    signup._module_gate = acts("signup nationality", False)
    attendance._ask_setting = acts("attendance config no-rsvp-penalty")
    test_mode._refuse_outside_test_mode = acts("test-mode backup lock", True)
    monkeypatch.setattr(bot_cog, "refuse", acts("bot pack"))
    invoked = {"signup nationality": (), "attendance config no-rsvp-penalty": (3,),
               "bot pack": ("not the word",), "test-mode backup lock": ()}

    bot.change_queue.hold()
    waiting = []
    for name, args in invoked.items():
        cog, command = commands[name]
        press = response_in_its_slot(member_interaction(bot))
        waiting.append(asyncio.create_task(_the_wait_of(command.callback)(cog, press, *args)))
    for _ in range(20):
        await asyncio.sleep(0)

    assert acted == []
    assert not any(task.done() for task in waiting)

    bot.change_queue.release()
    await asyncio.wait_for(asyncio.gather(*waiting), timeout=5)

    assert sorted(acted) == sorted(invoked)
