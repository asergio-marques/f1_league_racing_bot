"""Every `/attendance config` setter reaches a service method that exists — issue #119.

`/attendance config rsvp-absent-penalty` called `update_rsvp_absent_penalty`, a name that
lived only between migrations 034 and 038. Migration 038 renamed the column back to
`no_show_penalty` and carried the service with it; the cog was left behind. The command
acknowledged the interaction and then raised `AttributeError` on every invocation, so the
penalty a driver pays for accepting a check-in and then not appearing was pinned at its
enable-time default of 1 for every league, unreachable by any command.

Five months of a green suite did not catch it, because nothing here exercised an
`/attendance config` callback at all: the service end was covered
(`test_attendance_service.py`), the cog end was not, and the `# type: ignore[attr-defined]`
on the call kept a type checker quiet too.

**`spec=` is the whole mechanism of these tests.** Every service double is built with
`AsyncMock(spec=AttendanceService)`, so calling a method the real service does not define
raises `AttributeError` instead of being silently recorded. A bare `AsyncMock` would accept
`update_rsvp_absent_penalty` and pass happily — which is precisely the hole being closed.
Do not relax the `spec=` to make a future test easier to write.

The coverage is deliberately wider than the one broken command: all eight setters are driven,
so the next rename that stops halfway is caught on whichever command it lands.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from cogs.attendance_cog import AttendanceCog  # noqa: E402
from models.attendance import AttendanceConfig  # noqa: E402
from services.attendance_service import AttendanceService  # noqa: E402

SERVER_ID = 9119


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _config(**overrides) -> AttendanceConfig:
    """A default attendance configuration, with the timing invariant satisfied."""
    values = dict(
        module_enabled=True,
        rsvp_notice_days=5,
        rsvp_last_notice_hours=24,
        rsvp_deadline_hours=2,
        no_rsvp_penalty=1,
        absent_penalty=1,
        no_show_penalty=1,
        autoreserve_threshold=None,
        autosack_threshold=None,
    )
    values.update(overrides)
    return AttendanceConfig(**values)


def _make_cog(*, cfg: AttendanceConfig | None = None) -> AttendanceCog:
    """An `AttendanceCog` whose bot carries a spec-bound attendance service.

    The module reads as enabled and no season is active, so every command under test runs
    past its guards and on to the service call the test is actually about.
    """
    bot = MagicMock()
    bot.attendance_service = AsyncMock(spec=AttendanceService)
    bot.attendance_service.get_config.return_value = cfg if cfg is not None else _config()
    bot.module_service = MagicMock()
    bot.module_service.is_attendance_enabled = AsyncMock(return_value=True)
    bot.season_service = MagicMock()
    bot.season_service.get_confirmed_season = AsyncMock(return_value=None)
    return AttendanceCog(bot)


def _interaction() -> MagicMock:
    """A Discord interaction stub. `response` is sync-attribute, async-method, as discord.py's is."""
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    return interaction


async def _invoke(command, cog: AttendanceCog, interaction, *args) -> None:
    """Call a slash command's body, stepping past the `@league_manager_only` guard.

    `_tier_guard` wraps with `functools.wraps`, so `__wrapped__` is the undecorated function.
    Permission is covered by the channel-guard tests; these tests are about what the body does.
    """
    await command.callback.__wrapped__(cog, interaction, *args)


#: Every `/attendance config` setter: the command, the value to pass, and the service method
#: the cog must call with it. `config show` is excluded — it writes nothing.
CONFIG_SETTERS = [
    (AttendanceCog.config_rsvp_notice, 7, "update_rsvp_notice_days", 7),
    (AttendanceCog.config_rsvp_last_notice, 12, "update_rsvp_last_notice_hours", 12),
    (AttendanceCog.config_rsvp_deadline, 3, "update_rsvp_deadline_hours", 3),
    (AttendanceCog.config_no_rsvp_penalty, 2, "update_no_rsvp_penalty", 2),
    (AttendanceCog.config_absent_penalty, 3, "update_absent_penalty", 3),
    (AttendanceCog.config_no_show_penalty, 4, "update_no_show_penalty", 4),
    (AttendanceCog.config_autosack, 8, "update_autosack_threshold", 8),
    (AttendanceCog.config_autoreserve, 5, "update_autoreserve_threshold", 5),
]

#: Ids that name the command as a league types it, so a failure reads as the command.
CONFIG_SETTER_IDS = [
    "rsvp-notice",
    "rsvp-last-notice",
    "rsvp-deadline",
    "no-rsvp-penalty",
    "absent-penalty",
    "no-show-penalty",
    "autosack",
    "autoreserve",
]


# ---------------------------------------------------------------------------
# The defect itself
# ---------------------------------------------------------------------------


async def test_the_no_show_penalty_command_writes_the_penalty():
    """Issue #119: the command called a service method that does not exist.

    Before the fix this raises `AttributeError: Mock object has no attribute
    'update_rsvp_absent_penalty'` — the same failure a league saw as an interaction that
    never responded.
    """
    cog = _make_cog()
    interaction = _interaction()

    await _invoke(AttendanceCog.config_no_show_penalty, cog, interaction, 4)

    cog.bot.attendance_service.update_no_show_penalty.assert_awaited_once_with(4)
    interaction.followup.send.assert_awaited_once()
    assert "4" in interaction.followup.send.await_args.args[0]


async def test_the_no_show_penalty_command_is_named_for_the_column_it_writes():
    """The command, the service method and the column all say "no-show" (decided 2026-09-15).

    It shipped as `rsvp-absent-penalty`, the name of a column that existed only between
    migrations 034 and 038, and nothing else in the module used that vocabulary.
    """
    assert AttendanceCog.config_no_show_penalty.name == "no-show-penalty"


# ---------------------------------------------------------------------------
# The general form — the hole #119 fell through
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "command,value,method,expected", CONFIG_SETTERS, ids=CONFIG_SETTER_IDS
)
async def test_every_config_command_calls_a_method_the_service_defines(
    command, value, method, expected
):
    """Each setter reaches its service method, and that method exists on the real service.

    The `spec=AttendanceService` double is what makes this an assertion rather than a
    formality: a call to a name the service dropped in a rename raises here.
    """
    cog = _make_cog()
    interaction = _interaction()

    await _invoke(command, cog, interaction, value)

    getattr(cog.bot.attendance_service, method).assert_awaited_once_with(expected)


@pytest.mark.parametrize(
    "command,value,method,expected", CONFIG_SETTERS, ids=CONFIG_SETTER_IDS
)
async def test_every_config_command_is_refused_while_the_module_is_disabled(
    command, value, method, expected
):
    """A disabled module writes nothing, whichever setter is used."""
    cog = _make_cog()
    cog.bot.module_service.is_attendance_enabled = AsyncMock(return_value=False)
    interaction = _interaction()

    await _invoke(command, cog, interaction, value)

    getattr(cog.bot.attendance_service, method).assert_not_awaited()
    interaction.response.send_message.assert_awaited_once()
    assert "not enabled" in interaction.response.send_message.await_args.args[0]


# ---------------------------------------------------------------------------
# Rejections — nothing is written when the value is refused
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "command,method",
    [
        (AttendanceCog.config_no_rsvp_penalty, "update_no_rsvp_penalty"),
        (AttendanceCog.config_absent_penalty, "update_absent_penalty"),
        (AttendanceCog.config_no_show_penalty, "update_no_show_penalty"),
    ],
    ids=["no-rsvp-penalty", "absent-penalty", "no-show-penalty"],
)
async def test_a_negative_penalty_is_refused_and_nothing_is_written(command, method):
    cog = _make_cog()
    interaction = _interaction()

    await _invoke(command, cog, interaction, -1)

    getattr(cog.bot.attendance_service, method).assert_not_awaited()
    interaction.response.send_message.assert_awaited_once()
    assert "negative" in interaction.response.send_message.await_args.args[0]


async def test_a_penalty_of_zero_is_written():
    """Zero stops the charge for that case entirely — it is a value, not a disable sentinel."""
    cog = _make_cog()
    interaction = _interaction()

    await _invoke(AttendanceCog.config_no_show_penalty, cog, interaction, 0)

    cog.bot.attendance_service.update_no_show_penalty.assert_awaited_once_with(0)


async def test_the_timing_commands_are_refused_while_a_season_is_active():
    """Timing cannot move mid-season; the penalties can, and are covered above."""
    cog = _make_cog()
    cog.bot.season_service.get_confirmed_season = AsyncMock(return_value=MagicMock())
    interaction = _interaction()

    await _invoke(AttendanceCog.config_rsvp_deadline, cog, interaction, 3)

    cog.bot.attendance_service.update_rsvp_deadline_hours.assert_not_awaited()
    assert "placements are confirmed" in interaction.response.send_message.await_args.args[0]


async def test_a_timing_value_breaking_the_invariant_is_not_written():
    """Notice × 24 must exceed the last notice; a value that breaks it is refused."""
    cog = _make_cog()
    interaction = _interaction()

    await _invoke(AttendanceCog.config_rsvp_notice, cog, interaction, 1)

    cog.bot.attendance_service.update_rsvp_notice_days.assert_not_awaited()
    interaction.response.send_message.assert_awaited_once()


async def test_autosack_is_refused_while_autoreserve_is_set():
    """The two thresholds are mutually exclusive — one must be cleared before the other is set."""
    cog = _make_cog(cfg=_config(autoreserve_threshold=5))
    interaction = _interaction()

    await _invoke(AttendanceCog.config_autosack, cog, interaction, 8)

    cog.bot.attendance_service.update_autosack_threshold.assert_not_awaited()
    assert "auto-reserve" in interaction.response.send_message.await_args.args[0]


@pytest.mark.parametrize(
    "command,method",
    [
        (AttendanceCog.config_autosack, "update_autosack_threshold"),
        (AttendanceCog.config_autoreserve, "update_autoreserve_threshold"),
    ],
    ids=["autosack", "autoreserve"],
)
async def test_a_threshold_of_zero_disables_it(command, method):
    """0 is the disable sentinel for both thresholds, stored as NULL."""
    cog = _make_cog()
    interaction = _interaction()

    await _invoke(command, cog, interaction, 0)

    getattr(cog.bot.attendance_service, method).assert_awaited_once_with(None)


# ---------------------------------------------------------------------------
# The "not configured yet" refusal — issue #208
# ---------------------------------------------------------------------------
#
# The three *timing* setters read the current configuration before writing, because each
# validates against the other two. A league that has not enabled the module has no row, and
# each must say so rather than reaching `validate_timing_invariant` with `None` and raising
# `AttributeError` at the league — the same shape of fault as issue #119 above.
#
# The five penalty and threshold setters deliberately do **not**: they write a single column
# that depends on nothing else, and the two threshold commands read the configuration only to
# consult the *other* threshold, tolerating its absence with `if cfg and ...`. Parametrising
# all eight here would assert a refusal five of them have no reason to make.

#: The setters that validate against the rest of the configuration, and so need to read it.
TIMING_SETTERS = [
    (AttendanceCog.config_rsvp_notice, 7, "update_rsvp_notice_days"),
    (AttendanceCog.config_rsvp_last_notice, 12, "update_rsvp_last_notice_hours"),
    (AttendanceCog.config_rsvp_deadline, 3, "update_rsvp_deadline_hours"),
]
TIMING_SETTER_IDS = ["rsvp-notice", "rsvp-last-notice", "rsvp-deadline"]


@pytest.mark.parametrize(
    "command,value,method", TIMING_SETTERS, ids=TIMING_SETTER_IDS
)
async def test_a_timing_command_refuses_a_server_with_no_configuration(command, value, method):
    cog = _make_cog()
    cog.bot.attendance_service.get_config.return_value = None
    interaction = _interaction()

    await _invoke(command, cog, interaction, value)

    assert "No attendance configuration found" in interaction.response.send_message.await_args.args[0]


@pytest.mark.parametrize(
    "command,value,method", TIMING_SETTERS, ids=TIMING_SETTER_IDS
)
async def test_nothing_is_written_for_a_server_with_no_configuration(command, value, method):
    """The refusal must also be a refusal to write, not merely a message beside a write."""
    cog = _make_cog()
    cog.bot.attendance_service.get_config.return_value = None
    interaction = _interaction()

    await _invoke(command, cog, interaction, value)

    getattr(cog.bot.attendance_service, method).assert_not_awaited()


# ---------------------------------------------------------------------------
# The lower bounds on the timing commands
# ---------------------------------------------------------------------------


async def test_an_rsvp_notice_of_less_than_a_day_is_refused():
    """`attendance_module_specification.md` gives the notice in whole days, so zero days
    would schedule the call for the round's own moment and there would be nothing to
    notice."""
    cog = _make_cog()
    interaction = _interaction()

    await _invoke(AttendanceCog.config_rsvp_notice, cog, interaction, 0)

    assert "at least 1" in interaction.response.send_message.await_args.args[0]
    cog.bot.attendance_service.update_rsvp_notice_days.assert_not_awaited()


@pytest.mark.parametrize(
    "command,method",
    [
        (AttendanceCog.config_rsvp_last_notice, "update_rsvp_last_notice_hours"),
        (AttendanceCog.config_rsvp_deadline, "update_rsvp_deadline_hours"),
    ],
    ids=["rsvp-last-notice", "rsvp-deadline"],
)
async def test_a_negative_number_of_hours_is_refused(command, method):
    """Zero is meaningful for both — it disables the last notice, and it makes the deadline
    the round start — so the bound is below zero, not at it."""
    cog = _make_cog()
    interaction = _interaction()

    await _invoke(command, cog, interaction, -1)

    getattr(cog.bot.attendance_service, method).assert_not_awaited()


# ---------------------------------------------------------------------------
# `/attendance config show`
# ---------------------------------------------------------------------------


async def test_the_configuration_is_shown_with_every_setting_a_command_can_write():
    """`show` is how a league checks what it has set, so a setting missing from it is a
    setting nobody can confirm. Each of the eight is named."""
    cog = _make_cog(
        cfg=_config(
            rsvp_notice_days=6,
            rsvp_last_notice_hours=12,
            rsvp_deadline_hours=3,
            no_rsvp_penalty=2,
            absent_penalty=4,
            no_show_penalty=6,
            autoreserve_threshold=10,
            autosack_threshold=20,
        )
    )
    interaction = _interaction()

    await _invoke(AttendanceCog.config_show, cog, interaction)

    shown = interaction.response.send_message.await_args.args[0]
    for value in ("6", "12", "3", "2", "4", "10", "20"):
        assert value in shown
    assert "Attendance Configuration" in shown


async def test_an_unset_threshold_is_shown_as_disabled_rather_than_none():
    """`None` reaching a league as the word "None" reads as a bug; the thresholds are the
    only two settings that can be unset."""
    cog = _make_cog(cfg=_config(autoreserve_threshold=None, autosack_threshold=None))
    interaction = _interaction()

    await _invoke(AttendanceCog.config_show, cog, interaction)

    shown = interaction.response.send_message.await_args.args[0]
    assert "disabled" in shown
    assert "None" not in shown


async def test_a_last_notice_of_zero_is_shown_as_disabled():
    """Zero hours is a legal value meaning "do not send one", and the league needs to be
    able to tell that from "zero hours before the race"."""
    cog = _make_cog(cfg=_config(rsvp_last_notice_hours=0))
    interaction = _interaction()

    await _invoke(AttendanceCog.config_show, cog, interaction)

    assert "*(disabled)*" in interaction.response.send_message.await_args.args[0]


async def test_show_refuses_a_server_with_no_configuration():
    cog = _make_cog()
    cog.bot.attendance_service.get_config.return_value = None
    interaction = _interaction()

    await _invoke(AttendanceCog.config_show, cog, interaction)

    assert "No attendance configuration found" in interaction.response.send_message.await_args.args[0]


async def test_show_is_refused_while_the_module_is_disabled():
    cog = _make_cog()
    cog.bot.module_service.is_attendance_enabled = AsyncMock(return_value=False)
    interaction = _interaction()

    await _invoke(AttendanceCog.config_show, cog, interaction)

    cog.bot.attendance_service.get_config.assert_not_awaited()


async def test_setting_autosack_says_it_reaches_every_division_and_names_autoreserve():
    """Issue #220: a league wanting one division alone must be pointed at autoreserve."""
    cog = _make_cog()
    interaction = _interaction()

    await _invoke(AttendanceCog.config_autosack, cog, interaction, 8)

    reply = interaction.followup.send.await_args.args[0]
    assert "every seat in every division" in reply
    assert "/attendance config autoreserve" in reply
