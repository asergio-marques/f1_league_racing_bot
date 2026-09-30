"""Every `/signup …` command a manager is turned away from is recorded in the log channel (#482).

The core specification's "The record of what changed": a refusal is one line naming the member,
what was refused and why, and the member is answered exactly as before. A view or a list changes
nothing and records nothing, a refusal included.

The module-off gate used to be `SignupCog.interaction_check`, which answered the member and then
raised `CheckFailure`, so the error handler answered a second time and wrote a failure line for
what was only a refusal. It now runs in each command's body, after the tier guard, as the results
cog's does: one reply, one refusal line for a command that acts, none for a list.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.signup.cogs.signup_cog import SignupCog
from tests.support.undecorate import undecorate

SERVER_ID = 7731
MANAGER = 42
NOT_ENABLED = "Signup module is not enabled"

_GATE = "#482: the signup module gate is still the cog's interaction_check, not in the body"
_REFUSAL_LINE = "#482: the refusal is answered but not recorded in the log channel"


def _interaction(bot, command: str):
    """An interaction whose response knows whether it has been used, as Discord's does, and
    whose client is *bot*, so a refusal's line lands where the test reads it."""
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.user.id = MANAGER
    interaction.user.display_name = "Manager"
    interaction.client = bot
    interaction.command.qualified_name = command
    state = {"done": False}

    async def _answer(*_args, **_kwargs):
        state["done"] = True

    interaction.response.is_done = MagicMock(side_effect=lambda: state["done"])
    interaction.response.send_message = AsyncMock(side_effect=_answer)
    interaction.response.defer = AsyncMock(side_effect=_answer)
    interaction.followup.send = AsyncMock()
    return interaction


def _replies(interaction) -> list[str]:
    return [
        str(call.args[0])
        for call in interaction.response.send_message.await_args_list
        + interaction.followup.send.await_args_list
        if call.args
    ]


def _lines(bot) -> list[str]:
    return [str(call.args[0]) for call in bot.output_router.post_log.await_args_list]


# ── The module-off gate ─────────────────────────────────────────────────


def _module_off_bot(tmp_path):
    """Signups disabled. Its database is an empty file under *tmp_path*, so a command that runs
    past a missing gate fails there rather than leaving a file in the working directory."""
    bot = MagicMock()
    bot.db_path = str(tmp_path / "untouched.db")
    bot.module_service.is_signup_enabled = AsyncMock(return_value=False)
    bot.output_router.post_log = AsyncMock()
    return bot


def _day():
    day = MagicMock()
    day.value = "0"
    day.name = "Monday"
    return day


# (method, qualified name, the arguments after the interaction)
_ACTING = [
    ("signup_channel", "signup channel", lambda: [MagicMock()]),
    ("nationality", "signup nationality", list),
    ("time_type", "signup time-type", list),
    ("time_image", "signup time-image", list),
    ("time_slot_add", "signup time-slot add", lambda: [_day(), "20:00"]),
    ("time_slot_remove", "signup time-slot remove", lambda: [1]),
    ("close_time_add", "signup close-time add", lambda: ["2099-06-15T20:00:00"]),
    ("close_time_cancel", "signup close-time cancel", list),
    ("close_time_modify", "signup close-time modify", lambda: ["2099-06-15T20:00:00"]),
    ("signup_open", "signup open", list),
    ("signup_close", "signup close", list),
]

_LISTS = [
    ("time_slot_list", "signup time-slot list"),
    ("signup_unassigned_list", "signup unassigned list"),
    ("signup_unassigned_export", "signup unassigned export"),
]


@pytest.mark.xfail(strict=True, reason=_GATE)
@pytest.mark.parametrize("method, name, args", _ACTING, ids=[n for _, n, _ in _ACTING])
async def test_a_command_refused_while_the_module_is_off_is_answered_once_and_recorded(
    tmp_path, method, name, args
):
    """Signups disabled, a manager runs a `/signup` command that would change something: they
    are told once that the module is not enabled, and one refusal line names the command.
    Nothing else runs, and no failure line is written."""
    bot = _module_off_bot(tmp_path)
    cog = SignupCog.__new__(SignupCog)
    cog.bot = bot
    interaction = _interaction(bot, name)

    await undecorate(getattr(SignupCog, method))(cog, interaction, *args())

    [reply] = _replies(interaction)
    assert NOT_ENABLED in reply
    [line] = _lines(bot)
    assert line.startswith(f"⛔ `/{name}` refused for Manager (<@{MANAGER}>) — ")
    assert NOT_ENABLED in line


@pytest.mark.xfail(strict=True, reason=_GATE)
@pytest.mark.parametrize("method, name", _LISTS, ids=[n for _, n in _LISTS])
async def test_a_list_refused_while_the_module_is_off_is_answered_and_not_recorded(
    tmp_path, method, name
):
    """A list changes nothing, so the module-off refusal is answered and writes no line."""
    bot = _module_off_bot(tmp_path)
    cog = SignupCog.__new__(SignupCog)
    cog.bot = bot
    interaction = _interaction(bot, name)

    await undecorate(getattr(SignupCog, method))(cog, interaction)

    [reply] = _replies(interaction)
    assert NOT_ENABLED in reply
    bot.output_router.post_log.assert_not_awaited()


async def test_the_configuration_view_still_answers_while_the_module_is_off(tmp_path):
    """Signups disabled, a manager runs `/signup config view`: it is left outside the module
    gate, as it always was, so the manager sees the configuration rather than a refusal, and
    a view writes no line."""
    from leaguebot.core.services.config_service import ConfigService
    from leaguebot.signup.services.signup_module_service import SignupModuleService

    db_path = await _seed(tmp_path, signups_open=False, close_at=None, stage="CONFIGURATION")
    bot = _module_off_bot(tmp_path)
    bot.db_path = db_path
    bot.signup_module_service = SignupModuleService(db_path)
    bot.config_service = ConfigService(db_path)
    cog = SignupCog.__new__(SignupCog)
    cog.bot = bot
    interaction = _interaction(bot, "signup config view")

    await undecorate(SignupCog.config_view)(cog, interaction)

    [call] = interaction.response.send_message.await_args_list
    assert call.kwargs["embed"].title == "Signup Module Configuration"
    assert not any(NOT_ENABLED in reply for reply in _replies(interaction))
    bot.output_router.post_log.assert_not_awaited()


@pytest.mark.xfail(strict=True, reason=_GATE)
async def test_the_cog_no_longer_gates_through_interaction_check():
    """A cog-wide `interaction_check` that returns False raises `CheckFailure`, which answers a
    second time and writes a failure line; the gate is in each command's body instead."""
    assert "interaction_check" not in SignupCog.__dict__


async def test_a_command_run_in_a_dm_writes_no_league_line(tmp_path):
    """A DM reaches the tier guard before the module gate, and the guard sends it to the host
    log: the member is answered and the league's log channel is not written."""
    bot = _module_off_bot(tmp_path)
    bot.config_service.get_server_config = AsyncMock(
        return_value=MagicMock(interaction_channel_id=100)
    )
    cog = SignupCog.__new__(SignupCog)
    cog.bot = bot
    interaction = _interaction(bot, "signup close")
    interaction.guild_id = None
    interaction.guild = None
    interaction.channel_id = 555

    await SignupCog.signup_close.callback(cog, interaction)

    assert _replies(interaction), "the member was not answered"
    bot.output_router.post_log.assert_not_awaited()


# ── The commands' own refusals ──────────────────────────────────────────


async def _seed(tmp_path, *, signups_open: bool, close_at: str | None, stage: str):
    path = str(tmp_path / "refusals.db")
    await run_migrations(path)
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 900, 100, 101)",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO signup_module_config (id, signups_open, close_at) VALUES (?, ?, ?)",
            (1, 1 if signups_open else 0, close_at),
        )
        await db.execute(
            "INSERT INTO seasons (start_date, status, season_number, stage) "
            "VALUES ('2026-09-17', 'SETUP', 1, ?)",
            (stage,),
        )
        await db.commit()
    return path


def _cog(db_path):
    from leaguebot.signup.services.signup_module_service import SignupModuleService

    bot = MagicMock()
    bot.db_path = db_path
    bot.signup_module_service = SignupModuleService(db_path)
    bot.module_service.is_signup_enabled = AsyncMock(return_value=True)
    bot.scheduler_service = MagicMock()
    bot.output_router.post_log = AsyncMock()
    cog = SignupCog.__new__(SignupCog)
    cog.bot = bot
    return cog


ARMED = "2099-06-08T20:00:00+00:00"
LATER = "2099-06-15T20:00:00+00:00"

# (case id, command, how the window stands, the arguments, what the reply says)
_REFUSALS = [
    ("time-slot add, 25 slots", "signup time-slot add", "configuration", "full",
     lambda: [_day(), "20:00"], "Maximum of 25 time slots reached."),
    ("time-slot add, bad time", "signup time-slot add", "configuration", None,
     lambda: [_day(), "banana"], "Could not parse time 'banana'."),
    ("time-slot add, duplicate", "signup time-slot add", "configuration", "one slot",
     lambda: [_day(), "20:00"], "That time slot already exists."),
    ("time-slot remove, no slots", "signup time-slot remove", "configuration", None,
     lambda: [1], "No slots configured."),
    ("time-slot remove, no such slot", "signup time-slot remove", "configuration", "one slot",
     lambda: [9], "Slot #9 does not exist."),
    ("close-time add, window closed", "signup close-time add", "closed", None,
     lambda: [LATER], "Signups are not currently open"),
    ("close-time add, already armed", "signup close-time add", "armed", None,
     lambda: [LATER], "Signups already auto-close at"),
    ("close-time add, bad time", "signup close-time add", "open", None,
     lambda: ["next Tuesday-ish"], "not a valid ISO 8601 datetime"),
    ("close-time cancel, nothing armed", "signup close-time cancel", "open", None,
     list, "No auto-close time is set."),
    ("close-time modify, nothing armed", "signup close-time modify", "open", None,
     lambda: [LATER], "No auto-close time is set, so there is nothing to change."),
    ("close-time modify, bad time", "signup close-time modify", "armed", None,
     lambda: ["the Thursday after next"], "not a valid ISO 8601 datetime"),
    ("close, window closed", "signup close", "closed", None,
     list, "Signups are not currently open."),
    ("close, close time armed", "signup close", "armed", None,
     list, "Signups will auto-close at"),
]

_METHODS = {
    "signup time-slot add": "time_slot_add",
    "signup time-slot remove": "time_slot_remove",
    "signup close-time add": "close_time_add",
    "signup close-time cancel": "close_time_cancel",
    "signup close-time modify": "close_time_modify",
    "signup close": "signup_close",
}


@pytest.mark.xfail(strict=True, reason=_REFUSAL_LINE)
@pytest.mark.parametrize(
    "name, window, slots, args, said",
    [case[1:] for case in _REFUSALS],
    ids=[case[0] for case in _REFUSALS],
)
async def test_a_signup_command_refusal_is_answered_as_before_and_recorded(
    tmp_path, monkeypatch, name, window, slots, args, said
):
    """A manager's `/signup time-slot`, `/signup close-time` or `/signup close` command is turned
    away by its own check: the reply is today's, seen by them alone, and exactly one refusal line
    names the command, the manager and the reply's first line."""
    stage = "CONFIGURATION" if window == "configuration" else "SIGNUPS"
    db_path = await _seed(
        tmp_path,
        signups_open=window in ("open", "armed"),
        close_at=ARMED if window == "armed" else None,
        stage=stage,
    )
    cog = _cog(db_path)
    if slots == "one slot":
        await cog.bot.signup_module_service.add_slot(0, "20:00")
    elif slots == "full":
        monkeypatch.setattr(
            cog.bot.signup_module_service, "get_slots", AsyncMock(return_value=[MagicMock()] * 25)
        )
    interaction = _interaction(cog.bot, name)

    await undecorate(getattr(SignupCog, _METHODS[name]))(cog, interaction, *args())

    [reply] = _replies(interaction)
    assert said in reply
    for call in (
        interaction.response.send_message.await_args_list
        + interaction.followup.send.await_args_list
    ):
        assert call.kwargs.get("ephemeral") is True
    [line] = _lines(cog.bot)
    assert line.startswith(f"⛔ `/{name}` refused for Manager (<@{MANAGER}>) — ")
    assert said in line
