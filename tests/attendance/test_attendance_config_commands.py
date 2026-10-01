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

from unittest.mock import AsyncMock, MagicMock

import pytest

from leaguebot.attendance.cogs.attendance_cog import AttendanceCog
from leaguebot.attendance.models.attendance import AttendanceConfig
from leaguebot.attendance.services.attendance_service import AttendanceService

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
    bot.db_path = "unused.db"
    bot.output_router = MagicMock()
    bot.output_router.post_log = AsyncMock(return_value=None)
    return AttendanceCog(bot)


def _interaction() -> MagicMock:
    """A Discord interaction stub. `response` is sync-attribute, async-method, as discord.py's is."""
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.user.display_name = "Manager"
    interaction.user.id = 42
    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.response.is_done = MagicMock(
        side_effect=lambda: bool(
            interaction.response.defer.await_count
            or interaction.response.send_message.await_count
        )
    )
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    return interaction


async def _invoke(command, cog: AttendanceCog, interaction, *args) -> None:
    """Call a slash command's body, stepping past the `@league_manager_only` guard.

    `_tier_guard` wraps with `functools.wraps`, so `__wrapped__` is the undecorated function.
    Permission is covered by the channel-guard tests; these tests are about what the body does.
    """
    interaction.command = command
    interaction.client = cog.bot
    await command.callback.__wrapped__(cog, interaction, *args)


@pytest.fixture(autouse=True)
def audit(monkeypatch) -> AsyncMock:
    """The audit entry a setter writes, caught rather than written: these tests have no database."""
    record = AsyncMock(return_value=None)
    monkeypatch.setattr(
        "leaguebot.attendance.cogs.attendance_cog.audit_service.record_change", record
    )
    return record


def _lines(cog: AttendanceCog) -> list[str]:
    """Every line the command wrote to the log channel."""
    return [str(c.args[0]) for c in cog.bot.output_router.post_log.await_args_list]


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
    """0 is the disable sentinel for both thresholds, stored as NULL.

    The threshold starts set, so that 0 changes it: one already disabled changes nothing.
    """
    column = method.removeprefix("update_")
    cog = _make_cog(cfg=_config(**{column: 6}))
    interaction = _interaction()

    await _invoke(command, cog, interaction, 0)

    getattr(cog.bot.attendance_service, method).assert_awaited_once_with(None)


# ---------------------------------------------------------------------------
# The "not configured yet" refusal — issue #208
# ---------------------------------------------------------------------------
#
# Every setter reads the current configuration before writing: the three timing setters to
# validate against the other two, and all eight to tell a value already held from a change and
# to audit the value replaced (#482). A league that has not enabled the module has no row, and
# each must say so rather than reaching `validate_timing_invariant`, or the comparison, with
# `None` and raising `AttributeError` at the league — the same shape of fault as issue #119.


@pytest.mark.parametrize(
    "command,value,method,expected", CONFIG_SETTERS, ids=CONFIG_SETTER_IDS
)
async def test_a_config_command_refuses_a_server_with_no_configuration(
    command, value, method, expected
):
    cog = _make_cog()
    cog.bot.attendance_service.get_config.return_value = None
    interaction = _interaction()

    await _invoke(command, cog, interaction, value)

    assert "No attendance configuration found" in interaction.response.send_message.await_args.args[0]


@pytest.mark.parametrize(
    "command,value,method,expected", CONFIG_SETTERS, ids=CONFIG_SETTER_IDS
)
async def test_nothing_is_written_for_a_server_with_no_configuration(
    command, value, method, expected
):
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


# ---------------------------------------------------------------------------
# Every outcome is recorded in the log channel (#482)
# ---------------------------------------------------------------------------
#
# The core specification's "The record of what changed": every command that acts records its
# success, its refusal and its failure, and every change to a league's configuration is
# recorded twice — an audit entry and a log line. A value already held records that nothing
# changed. `config show` changes nothing and records nothing.

S = AttendanceCog

#: Every way a setter is refused: the command, its name, the value, how the bot stands, and
#: the reason the line must carry.
REFUSALS = [
    *[
        (command, name, value, {"module": False}, "The Attendance module is not enabled.")
        for (command, value, _m, _e), name in zip(CONFIG_SETTERS, CONFIG_SETTER_IDS)
    ],
    *[
        (command, name, value, {"season": True}, "Attendance configuration cannot be changed")
        for command, name, value in [
            (S.config_rsvp_notice, "rsvp-notice", 7),
            (S.config_rsvp_last_notice, "rsvp-last-notice", 12),
            (S.config_rsvp_deadline, "rsvp-deadline", 3),
        ]
    ],
    (S.config_rsvp_notice, "rsvp-notice", 0, {}, "`rsvp_notice_days` must be at least 1."),
    (S.config_rsvp_last_notice, "rsvp-last-notice", -1, {}, "cannot be negative"),
    (S.config_rsvp_deadline, "rsvp-deadline", -1, {}, "cannot be negative"),
    (S.config_no_rsvp_penalty, "no-rsvp-penalty", -1, {}, "Penalty points cannot be negative."),
    (S.config_absent_penalty, "absent-penalty", -1, {}, "Penalty points cannot be negative."),
    (S.config_no_show_penalty, "no-show-penalty", -1, {}, "Penalty points cannot be negative."),
    (S.config_autosack, "autosack", -1, {}, "Threshold cannot be negative."),
    (S.config_autoreserve, "autoreserve", -1, {}, "Threshold cannot be negative."),
    *[
        (command, name, value, {"cfg": None}, "No attendance configuration found.")
        for (command, value, _m, _e), name in zip(CONFIG_SETTERS, CONFIG_SETTER_IDS)
    ],
    (S.config_rsvp_notice, "rsvp-notice", 1, {}, "must be greater than `rsvp_last_notice_hours`"),
    (S.config_rsvp_last_notice, "rsvp-last-notice", 200, {},
     "must be greater than `rsvp_last_notice_hours` (200h)"),
    (S.config_rsvp_deadline, "rsvp-deadline", 30, {},
     "must be greater than `rsvp_deadline_hours` (30h)"),
    (
        S.config_autosack, "autosack", 8, {"cfg": {"autoreserve_threshold": 5}},
        "Cannot set auto-sack while auto-reserve is active.",
    ),
    (
        S.config_autoreserve, "autoreserve", 5, {"cfg": {"autosack_threshold": 8}},
        "Cannot set auto-reserve while auto-sack is active.",
    ),
]


def _refusal_id(case) -> str:
    _command, name, value, state, _reason = case
    return f"{name}-{value}-{'-'.join(state) or 'value'}"


@pytest.mark.parametrize("case", REFUSALS, ids=[_refusal_id(c) for c in REFUSALS])
async def test_every_refusal_of_a_setter_is_recorded(case):
    """A1: each refusal keeps its reply and writes one "⛔ … refused for Name (<@id>) — …" line."""
    command, name, value, state, reason = case
    if "cfg" in state and state["cfg"] is None:
        cog = _make_cog()
        cog.bot.attendance_service.get_config.return_value = None
    else:
        cog = _make_cog(cfg=_config(**state.get("cfg", {})))
    if state.get("module") is False:
        cog.bot.module_service.is_attendance_enabled = AsyncMock(return_value=False)
    if state.get("season"):
        cog.bot.season_service.get_confirmed_season = AsyncMock(return_value=MagicMock())
    interaction = _interaction()

    await _invoke(command, cog, interaction, value)

    interaction.response.send_message.assert_awaited_once()
    reply = interaction.response.send_message.await_args.args[0]
    assert reply.startswith("❌")
    lines = _lines(cog)
    assert len(lines) == 1
    assert lines[0].startswith(
        f"⛔ `/attendance config {name}` refused for Manager (<@42>) — "
    )
    assert reason in lines[0]


async def test_show_records_nothing_while_the_module_is_disabled():
    """A2: `config show` changes nothing, so its refusal is answered and not recorded."""
    cog = _make_cog()
    cog.bot.module_service.is_attendance_enabled = AsyncMock(return_value=False)
    interaction = _interaction()

    await _invoke(AttendanceCog.config_show, cog, interaction)

    assert "not enabled" in interaction.response.send_message.await_args.args[0]
    cog.bot.output_router.post_log.assert_not_awaited()


#: Each setter's success: the command, its name, the configuration it starts from, the value
#: typed, the column, the value stored and the line's detail.
SUCCESSES = [
    (S.config_rsvp_notice, "rsvp-notice", {}, 7, "rsvp_notice_days", 5, 7,
     "RSVP notice: 7 day(s) (was 5 day(s))"),
    (S.config_rsvp_last_notice, "rsvp-last-notice", {}, 12, "rsvp_last_notice_hours", 24, 12,
     "last RSVP reminder: 12 hour(s) (was 24 hour(s))"),
    (S.config_rsvp_last_notice, "rsvp-last-notice", {}, 0, "rsvp_last_notice_hours", 24, 0,
     "last RSVP reminder: disabled (was 24 hour(s))"),
    (S.config_rsvp_deadline, "rsvp-deadline", {}, 3, "rsvp_deadline_hours", 2, 3,
     "RSVP deadline: 3 hour(s) (was 2 hour(s))"),
    (S.config_no_rsvp_penalty, "no-rsvp-penalty", {}, 2, "no_rsvp_penalty", 1, 2,
     "no-RSVP penalty: 2 point(s) (was 1 point(s))"),
    (S.config_absent_penalty, "absent-penalty", {}, 3, "absent_penalty", 1, 3,
     "absent penalty: 3 point(s) (was 1 point(s))"),
    (S.config_no_show_penalty, "no-show-penalty", {}, 0, "no_show_penalty", 1, 0,
     "no-show penalty: 0 point(s) (was 1 point(s))"),
    (S.config_autosack, "autosack", {}, 8, "autosack_threshold", None, 8,
     "auto-sack threshold: 8 point(s) (was disabled)"),
    (S.config_autosack, "autosack", {"autosack_threshold": 8}, 0, "autosack_threshold", 8, None,
     "auto-sack threshold: disabled (was 8 point(s))"),
    (S.config_autoreserve, "autoreserve", {}, 5, "autoreserve_threshold", None, 5,
     "auto-reserve threshold: 5 point(s) (was disabled)"),
    (S.config_autoreserve, "autoreserve", {"autoreserve_threshold": 5}, 0,
     "autoreserve_threshold", 5, None, "auto-reserve threshold: disabled (was 5 point(s))"),
]


@pytest.mark.parametrize(
    "command,name,start,value,column,old,new,detail",
    SUCCESSES,
    ids=[f"{c[1]}-{c[3]}" for c in SUCCESSES],
)
async def test_a_setter_records_its_change_twice(
    audit, command, name, start, value, column, old, new, detail
):
    """A3: a change writes one audit entry, the column from and to, and one success line."""
    cog = _make_cog(cfg=_config(**start))
    interaction = _interaction()

    await _invoke(command, cog, interaction, value)

    audit.assert_awaited_once()
    entry = audit.await_args.kwargs
    assert entry["change_type"] == "ATTENDANCE_CONFIG_SET"
    assert entry["actor_id"] == 42
    assert entry["old_value"] == {column: old}
    assert entry["new_value"] == {column: new}
    assert _lines(cog) == [
        f"Manager (<@42>) | /attendance config {name} | Success\n  {detail}"
    ]


#: Each setter given the value the default configuration already holds, and the line's detail.
UNCHANGED = [
    (S.config_rsvp_notice, "rsvp-notice", 5, "update_rsvp_notice_days", "RSVP notice: 5 day(s)"),
    (S.config_rsvp_last_notice, "rsvp-last-notice", 24, "update_rsvp_last_notice_hours",
     "last RSVP reminder: 24 hour(s)"),
    (S.config_rsvp_deadline, "rsvp-deadline", 2, "update_rsvp_deadline_hours",
     "RSVP deadline: 2 hour(s)"),
    (S.config_no_rsvp_penalty, "no-rsvp-penalty", 1, "update_no_rsvp_penalty",
     "no-RSVP penalty: 1 point(s)"),
    (S.config_absent_penalty, "absent-penalty", 1, "update_absent_penalty",
     "absent penalty: 1 point(s)"),
    (S.config_no_show_penalty, "no-show-penalty", 1, "update_no_show_penalty",
     "no-show penalty: 1 point(s)"),
    (S.config_autosack, "autosack", 0, "update_autosack_threshold",
     "auto-sack threshold: disabled"),
    (S.config_autoreserve, "autoreserve", 0, "update_autoreserve_threshold",
     "auto-reserve threshold: disabled"),
]


@pytest.mark.parametrize(
    "command,name,value,method,detail", UNCHANGED, ids=[c[1] for c in UNCHANGED]
)
async def test_a_value_already_held_records_that_nothing_changed(
    audit, command, name, value, method, detail
):
    """A4: the value the setting holds writes nothing, audits nothing, and says so."""
    cog = _make_cog()
    interaction = _interaction()

    await _invoke(command, cog, interaction, value)

    getattr(cog.bot.attendance_service, method).assert_not_awaited()
    audit.assert_not_awaited()
    assert interaction.response.send_message.await_args.args[0].startswith(
        "ℹ️ Nothing changed: "
    )
    assert _lines(cog) == [
        f"Manager (<@42>) | /attendance config {name} | Nothing changed\n  {detail}"
    ]
