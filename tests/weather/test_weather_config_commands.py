"""Every `/weather config` deadline command holds its guards and reaches its service.

Issue #161: nothing under `tests/` referenced `WeatherCog` at all. The service end of the
weather configuration was untested (closed by `test_weather_config_service.py`) and the cog
end — the guards a league actually meets — was untested too. This file covers the cog end.

`weather_module_specification.md` — "All three commands shall be rejected while the weather
module is disabled", "All three commands shall reject any value below 1", "If there is an
ongoing season (read: season approved/active), all three commands must be rejected", and
"Each successful command shall report the resulting values of all three deadlines, and shall
be written to the log channel."

On the active-season rule: there is no separate `APPROVED` status — `SeasonStatus` is
SETUP / ACTIVE / COMPLETED / CANCELLED and approval moves a season to ACTIVE — so the
specification's "approved/active" is `get_confirmed_season`, which is what the cog reads.

**`spec=` is load-bearing here**, as it is in `test_attendance_config_commands.py`, whose
shape this file follows. The bot's services are spec-bound doubles, so a cog calling a method
the real service does not define raises `AttributeError` rather than being silently recorded.
Issue #119 was exactly that failure on the attendance cog — a setter left behind by a rename,
acknowledged and then raising on every invocation, with a green suite throughout. Since #228
the type check catches the same mistake in `src/` too, but `spec=` is the suite's own guard
and does not lean on it. Do not relax the `spec=` to make a future test easier.

The coverage is deliberately driven across all three commands rather than one, so a change
that stops halfway is caught on whichever command it lands on.

`/weather config view` is covered at the end (issue #118). It shares the setters' module gate
and must not share their season gate: the deadlines were once readable only in the two season
reviews, each tied to one stage of one season.

**Every outcome is recorded in the log channel** (#482; the core specification's "The record
of what changed"): a refusal as one "⛔" line naming the command, a success as one line naming
the command with the three deadlines beneath, and a value the deadline already holds as one
"Nothing changed" line, after a reply saying so. Whether a value is already held is the
service's to say: a setter returns ``None`` for it, having written nothing.
"""
from __future__ import annotations

import contextlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from leaguebot.weather.cogs.weather_cog import WeatherCog
from leaguebot.core.models.season import Season, SeasonStatus
from leaguebot.weather.models.weather_config import WeatherPipelineConfig
from leaguebot.core.services.season_service import SeasonService

SERVER_ID = 7161
DB_PATH = "/nonexistent/weather.db"  # never opened: every service call is patched

#: Each command under test: its callback, the `weather_config_service` function it must
#: reach, a legal value to pass, and the deadline it sets, as its replies name it with that
#: value in its unit.
COMMANDS = [
    (WeatherCog.phase_1_deadline, "set_phase_1_days", 10, ("Phase 1", "10 day(s)")),
    (WeatherCog.phase_2_deadline, "set_phase_2_days", 3, ("Phase 2", "3 day(s)")),
    (WeatherCog.phase_3_deadline, "set_phase_3_hours", 6, ("Phase 3", "6 hour(s)")),
]

#: The member every interaction here belongs to, as the log channel names them.
MEMBER = "Race Control (<@4242>)"

_NOT_YET_RECORDED = "#482: a weather refusal is not yet recorded in the log channel"

COMMAND_IDS = ["phase_1", "phase_2", "phase_3"]

#: The three setters, for the tests that must prove *none* of them was reached.
ALL_SETTERS = [entry[1] for entry in COMMANDS]


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _config(**overrides) -> WeatherPipelineConfig:
    """A configuration carrying the packaged 5 / 2 / 2 horizons."""
    values = dict(phase_1_days=5, phase_2_days=2, phase_3_hours=2)
    values.update(overrides)
    return WeatherPipelineConfig(**values)


def _season() -> Season:
    """An ACTIVE season, which every deadline command must refuse to run against."""
    from datetime import date

    return Season(
        id=1,
        start_date=date(2026, 1, 1),
        status=SeasonStatus.ACTIVE,
        season_number=1,
    )


def _make_cog(*, weather_enabled: bool = True, season: Season | None = None) -> WeatherCog:
    """A `WeatherCog` whose bot carries spec-bound doubles.

    Defaults put the cog past both guards — module on, no season active — so a test that is
    about the body reaches it, and a test that is about a guard flips just the one it means.
    """
    bot = MagicMock()
    bot.db_path = DB_PATH
    bot.module_service = MagicMock()
    bot.module_service.is_weather_enabled = AsyncMock(return_value=weather_enabled)
    bot.season_service = AsyncMock(spec=SeasonService)
    bot.season_service.get_confirmed_season.return_value = season
    bot.output_router = MagicMock()
    bot.output_router.post_log = AsyncMock(return_value=None)
    return WeatherCog(bot)


def _interaction() -> MagicMock:
    """A Discord interaction stub. `response` is sync-attribute, async-method, as discord.py's is.

    `is_done` tracks whether the interaction has been answered or deferred, as discord.py's
    does, so a refusal is sent by whichever of the two a real interaction would take.
    """
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.user.display_name = "Race Control"
    interaction.user.id = 4242
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


async def _invoke(command, cog: WeatherCog, interaction, value) -> None:
    """Call a slash command's body, stepping past the `@league_manager_only` guard.

    `_tier_guard` wraps with `functools.wraps`, so `__wrapped__` is the undecorated function.
    Permission is covered by the channel-guard tests; these tests are about what the body does.
    The interaction carries the command and the bot, as a real one does, so a line in the log
    channel names the command and reaches the bot's router.
    """
    interaction.command = command
    interaction.client = cog.bot
    await command.callback.__wrapped__(cog, interaction, value)


@contextlib.contextmanager
def _patched_setters(return_value=None, *, held: bool = False):
    """Patch all three `weather_config_service` setters, yielding them by name.

    *held* has each return ``None``, the service's answer for a value already held.

    The cog imports each setter *inside* the command body, so the patch has to land on the
    service module rather than on a name bound into the cog at import time.

    `patch.multiple` is deliberately not used: given an explicit `new=`, it does not return
    the replacement in its dictionary, which leaves a test with no handle to assert on.
    """
    value = None if held else _config() if return_value is None else return_value
    with contextlib.ExitStack() as stack:
        yield {
            name: stack.enter_context(
                patch(
                    f"leaguebot.weather.services.weather_config_service.{name}",
                    new=AsyncMock(return_value=value),
                )
            )
            for name in ALL_SETTERS
        }


def _sent_text(interaction) -> str:
    """Everything the command said to the user, across both response and followup."""
    parts = []
    for mock in (interaction.response.send_message, interaction.followup.send):
        for call in mock.await_args_list:
            if call.args:
                parts.append(str(call.args[0]))
    return "\n".join(parts)


def _reason(reply: str) -> str:
    """A reply's first line without the mark it opens with: the reason a refusal records."""
    first = reply.strip().splitlines()[0].strip()
    for mark in ("❌", "ℹ️"):
        first = first.removeprefix(mark)
    return first.strip()


def _assert_one_refusal_line(cog: WeatherCog, interaction, command) -> None:
    """One "⛔" line was recorded, naming *command*, the member and the reply's reason."""
    cog.bot.output_router.post_log.assert_awaited_once()
    (line,) = cog.bot.output_router.post_log.await_args.args
    assert line == (
        f"⛔ `/{command.qualified_name}` refused for {MEMBER} — {_reason(_sent_text(interaction))}"
    )


# ---------------------------------------------------------------------------
# The module gate
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason=_NOT_YET_RECORDED)
@pytest.mark.parametrize("command, setter, value, _log", COMMANDS, ids=COMMAND_IDS)
async def test_command_is_refused_while_the_weather_module_is_disabled(
    command, setter, value, _log
):
    """"All three commands shall be rejected while the weather module is disabled." The
    refusal is recorded in the log channel, as every refusal of a command is."""
    cog = _make_cog(weather_enabled=False)
    interaction = _interaction()

    with _patched_setters() as mocks:
        await _invoke(command, cog, interaction, value)

        # Nothing was written for a module that is switched off.
        for name in ALL_SETTERS:
            mocks[name].assert_not_awaited()

    interaction.response.send_message.assert_awaited_once()
    assert _sent_text(interaction) == "❌ The weather module is not enabled."
    _assert_one_refusal_line(cog, interaction, command)


# ---------------------------------------------------------------------------
# The active-season gate
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason=_NOT_YET_RECORDED)
@pytest.mark.parametrize("command, setter, value, _log", COMMANDS, ids=COMMAND_IDS)
async def test_command_is_refused_while_a_season_is_active(command, setter, value, _log):
    """"If there is an ongoing season ... all three commands must be rejected."

    The deadlines in force for a season are those stored when it was approved, so letting a
    change through mid-season would silently disagree with the horizons its rounds were
    armed at.
    """
    cog = _make_cog(season=_season())
    interaction = _interaction()

    with _patched_setters() as mocks:
        await _invoke(command, cog, interaction, value)

        for name in ALL_SETTERS:
            mocks[name].assert_not_awaited()

    interaction.response.send_message.assert_awaited_once()
    assert "placements are confirmed" in _sent_text(interaction)
    _assert_one_refusal_line(cog, interaction, command)


# ---------------------------------------------------------------------------
# The minimum of 1
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason=_NOT_YET_RECORDED)
@pytest.mark.parametrize("command, setter, _value, _log", COMMANDS, ids=COMMAND_IDS)
@pytest.mark.parametrize("bad_value", [0, -1, -100], ids=["zero", "negative", "very_negative"])
async def test_command_rejects_a_value_below_one(command, setter, _value, _log, bad_value):
    """"All three commands shall reject any value below 1." """
    cog = _make_cog()
    interaction = _interaction()

    with _patched_setters() as mocks:
        await _invoke(command, cog, interaction, bad_value)

        for name in ALL_SETTERS:
            mocks[name].assert_not_awaited()

    interaction.response.send_message.assert_awaited_once()
    assert "at least 1" in _sent_text(interaction)
    _assert_one_refusal_line(cog, interaction, command)


@pytest.mark.parametrize("command, setter, value, _log", COMMANDS, ids=COMMAND_IDS)
async def test_one_is_accepted(command, setter, value, _log):
    """1 is the smallest legal value — the boundary the rule above sits on."""
    cog = _make_cog()
    interaction = _interaction()

    with _patched_setters() as mocks:
        await _invoke(command, cog, interaction, 1)

        mocks[setter].assert_awaited_once()
        assert mocks[setter].await_args.args == (DB_PATH, 1)


# ---------------------------------------------------------------------------
# The ordering invariant, as the league meets it
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason=_NOT_YET_RECORDED)
@pytest.mark.parametrize("command, setter, value, _log", COMMANDS, ids=COMMAND_IDS)
async def test_service_rejection_reaches_the_user_unchanged(command, setter, value, _log):
    """The service returns an error string on a violation; the command must relay it.

    The specification requires the rejection to state both offending values in hours, and
    the service composes that sentence. A command that swallowed it, or replaced it with a
    generic refusal, would leave the league unable to see which pair collided.
    """
    refusal = "Phase 2 deadline (2d = 48h) must be strictly greater than Phase 3 deadline (48h)."
    cog = _make_cog()
    interaction = _interaction()

    with _patched_setters(refusal):
        await _invoke(command, cog, interaction, value)

    assert _sent_text(interaction) == f"❌ {refusal}"
    # A refusal is recorded too, with the service's sentence as its reason.
    _assert_one_refusal_line(cog, interaction, command)


# ---------------------------------------------------------------------------
# The successful path
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("command, setter, value, _log", COMMANDS, ids=COMMAND_IDS)
async def test_success_reaches_the_service_with_the_value(command, setter, value, _log):
    cog = _make_cog()
    interaction = _interaction()

    with _patched_setters() as mocks:
        await _invoke(command, cog, interaction, value)

        mocks[setter].assert_awaited_once()
        assert mocks[setter].await_args.args == (DB_PATH, value)
        # Only the command's own setter runs.
        for name in ALL_SETTERS:
            if name != setter:
                mocks[name].assert_not_awaited()


@pytest.mark.parametrize("command, setter, value, _log", COMMANDS, ids=COMMAND_IDS)
async def test_success_reports_all_three_deadlines(command, setter, value, _log):
    """"Each successful command shall report the resulting values of all three deadlines."

    The reply must name the other two as well as the one just changed, so the league can see
    the whole ordering it now has without running `/weather config view` after it.
    """
    resulting = _config(phase_1_days=9, phase_2_days=4, phase_3_hours=8)
    cog = _make_cog()
    interaction = _interaction()

    with _patched_setters(resulting):
        await _invoke(command, cog, interaction, value)

    text = _sent_text(interaction)
    # Whichever command ran, the two it did not change are named from the returned config.
    others = {
        "set_phase_1_days": ["4d", "8h"],
        "set_phase_2_days": ["9d", "8h"],
        "set_phase_3_hours": ["9d", "4d"],
    }[setter]
    for token in others:
        assert token in text, f"{token!r} missing from {text!r}"


@pytest.mark.xfail(
    strict=True, reason="#482: a weather success line names a token, not the command"
)
@pytest.mark.parametrize("command, setter, value, _log", COMMANDS, ids=COMMAND_IDS)
async def test_success_is_written_to_the_log_channel(command, setter, value, _log):
    """"... and shall be written to the log channel." In the standard form: the member, the
    command a league types and the outcome, with the three deadlines now in force beneath."""
    resulting = _config(phase_1_days=9, phase_2_days=4, phase_3_hours=8)
    cog = _make_cog()
    interaction = _interaction()

    with _patched_setters(resulting):
        await _invoke(command, cog, interaction, value)

    cog.bot.output_router.post_log.assert_awaited_once()
    (logged_text,) = cog.bot.output_router.post_log.await_args.args
    assert logged_text.splitlines() == [
        f"{MEMBER} | /{command.qualified_name} | Success",
        "  Phase 1: 9 day(s)",
        "  Phase 2: 4 day(s)",
        "  Phase 3: 8 hour(s)",
    ]


@pytest.mark.parametrize("command, setter, value, _log", COMMANDS, ids=COMMAND_IDS)
async def test_success_defers_before_touching_the_database(command, setter, value, _log):
    """The reply is deferred first, so a slow write cannot overrun Discord's budget.

    Both gates answer with `response.send_message`, which means the deferral must come after
    them — deferring first would leave those refusals trying to respond twice.
    """
    cog = _make_cog()
    interaction = _interaction()

    with _patched_setters():
        await _invoke(command, cog, interaction, value)

    interaction.response.defer.assert_awaited_once()
    interaction.response.send_message.assert_not_awaited()
    interaction.followup.send.assert_awaited_once()


# ---------------------------------------------------------------------------
# A value already held
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True, reason="#482: a deadline given the value it holds is reported as a change"
)
@pytest.mark.parametrize("command, setter, value, deadline", COMMANDS, ids=COMMAND_IDS)
async def test_a_held_value_changes_nothing(command, setter, value, deadline):
    """A deadline given the value it already holds is not a change: the reply says so, and the
    log channel records one "Nothing changed" line, as the results and attendance setters do.
    The service decides it is held, and writes nothing (`test_weather_config_service.py`)."""
    phase, shown = deadline
    cog = _make_cog()
    interaction = _interaction()

    with _patched_setters(held=True) as mocks:
        await _invoke(command, cog, interaction, value)

        mocks[setter].assert_awaited_once_with(DB_PATH, value)

    assert _sent_text(interaction) == (
        f"ℹ️ Nothing changed: the {phase} deadline is already **{shown}**."
    )
    cog.bot.output_router.post_log.assert_awaited_once()
    (logged_text,) = cog.bot.output_router.post_log.await_args.args
    first, *details = logged_text.splitlines()
    assert first == f"{MEMBER} | /{command.qualified_name} | Nothing changed"
    assert any(shown in detail for detail in details), logged_text


@pytest.mark.xfail(strict=True, reason=_NOT_YET_RECORDED)
@pytest.mark.parametrize("command, setter, value, _log", COMMANDS, ids=COMMAND_IDS)
async def test_a_held_value_is_still_refused_while_a_season_is_active(
    command, setter, value, _log
):
    """Every refusal comes first: a value already held, sent while a season's placements are
    confirmed, is refused as any other value is, never answered as "Nothing changed"."""
    cog = _make_cog(season=_season())
    interaction = _interaction()

    with _patched_setters(held=True) as mocks:
        await _invoke(command, cog, interaction, value)

        mocks[setter].assert_not_awaited()

    assert "placements are confirmed" in _sent_text(interaction)
    assert "Nothing changed" not in _sent_text(interaction)
    _assert_one_refusal_line(cog, interaction, command)


# ---------------------------------------------------------------------------
# /weather config view
# ---------------------------------------------------------------------------


async def _invoke_view(cog: WeatherCog, interaction) -> None:
    """Call `/weather config view`'s body, stepping past `@league_manager_only` as `_invoke` does."""
    interaction.command = WeatherCog.config_view
    interaction.client = cog.bot
    await WeatherCog.config_view.callback.__wrapped__(cog, interaction)


def _patched_read(config: WeatherPipelineConfig):
    """Patch the service's reader. The cog imports it inside the body, as it does the setters."""
    return patch(
        "leaguebot.weather.services.weather_config_service.get_weather_pipeline_config",
        new=AsyncMock(return_value=config),
    )


async def test_view_reports_the_deadlines_with_no_season():
    """The case #118 was raised for: no season at all, and the deadlines still readable.

    Distinct values, so a line reading the wrong field cannot pass on a coincidence.
    """
    cog = _make_cog()
    interaction = _interaction()

    with _patched_read(_config(phase_1_days=7, phase_2_days=3, phase_3_hours=6)) as read:
        await _invoke_view(cog, interaction)

    read.assert_awaited_once_with(DB_PATH)
    interaction.followup.send.assert_awaited_once()
    assert interaction.followup.send.await_args.kwargs["ephemeral"] is True
    text = _sent_text(interaction)
    for line in (
        "Phase 1 deadline: 7 day(s) before race",
        "Phase 2 deadline: 3 day(s) before race",
        "Phase 3 deadline: 6h before race",
    ):
        assert line in text, f"{line!r} missing from {text!r}"


async def test_view_answers_while_a_season_is_confirmed():
    """The setters' season gate is theirs alone.

    Mid-season is when a league most needs to know when its forecasts land, and it is the
    stage at which neither review shows the deadlines.
    """
    cog = _make_cog(season=_season())
    interaction = _interaction()

    with _patched_read(_config()) as read:
        await _invoke_view(cog, interaction)

    read.assert_awaited_once()
    assert "placements are confirmed" not in _sent_text(interaction)
    assert "Phase 1 deadline: 5 day(s) before race" in _sent_text(interaction)


async def test_view_is_refused_while_the_weather_module_is_disabled():
    """The module gate the setters and every other module's view command hold. A read changes
    nothing, so its refusal is answered and not recorded in the log channel."""
    cog = _make_cog(weather_enabled=False)
    interaction = _interaction()

    with _patched_read(_config()) as read:
        await _invoke_view(cog, interaction)

    read.assert_not_awaited()
    interaction.response.send_message.assert_awaited_once()
    assert "not enabled" in _sent_text(interaction)
    interaction.response.defer.assert_not_awaited()
    cog.bot.output_router.post_log.assert_not_awaited()


async def test_view_writes_nothing_to_the_log_channel():
    """A read changes nothing, so it leaves no entry beside the changes that are logged."""
    cog = _make_cog()
    interaction = _interaction()

    with _patched_read(_config()):
        await _invoke_view(cog, interaction)

    cog.bot.output_router.post_log.assert_not_awaited()
