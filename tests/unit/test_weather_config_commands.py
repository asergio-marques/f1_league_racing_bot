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
acknowledged and then raising on every invocation, with a green suite throughout. The
`# type: ignore[attr-defined]` comments on these very call sites would keep a type checker
quiet about the same mistake here. Do not relax the `spec=` to make a future test easier.

The coverage is deliberately driven across all three commands rather than one, so a change
that stops halfway is caught on whichever command it lands on.
"""
from __future__ import annotations

import contextlib
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from cogs.weather_cog import WeatherCog  # noqa: E402
from models.season import Season, SeasonStatus  # noqa: E402
from models.weather_config import WeatherPipelineConfig  # noqa: E402
from services.season_service import SeasonService  # noqa: E402

SERVER_ID = 7161
DB_PATH = "/nonexistent/weather.db"  # never opened: every service call is patched

#: Each command under test: its callback, the `weather_config_service` function it must
#: reach, a legal value to pass, and the token its log line must carry.
COMMANDS = [
    (WeatherCog.phase_1_deadline, "set_phase_1_days", 10, "WEATHER_CONFIG_PHASE1_DEADLINE"),
    (WeatherCog.phase_2_deadline, "set_phase_2_days", 3, "WEATHER_CONFIG_PHASE2_DEADLINE"),
    (WeatherCog.phase_3_deadline, "set_phase_3_hours", 6, "WEATHER_CONFIG_PHASE3_DEADLINE"),
]

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
        server_id=SERVER_ID,
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
    """A Discord interaction stub. `response` is sync-attribute, async-method, as discord.py's is."""
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.user.display_name = "Race Control"
    interaction.user.id = 4242
    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    return interaction


async def _invoke(command, cog: WeatherCog, interaction, value) -> None:
    """Call a slash command's body, stepping past the `@league_manager_only` guard.

    `_tier_guard` wraps with `functools.wraps`, so `__wrapped__` is the undecorated function.
    Permission is covered by the channel-guard tests; these tests are about what the body does.
    """
    await command.callback.__wrapped__(cog, interaction, value)


@contextlib.contextmanager
def _patched_setters(return_value=None):
    """Patch all three `weather_config_service` setters, yielding them by name.

    The cog imports each setter *inside* the command body, so the patch has to land on the
    service module rather than on a name bound into the cog at import time.

    `patch.multiple` is deliberately not used: given an explicit `new=`, it does not return
    the replacement in its dictionary, which leaves a test with no handle to assert on.
    """
    value = _config() if return_value is None else return_value
    with contextlib.ExitStack() as stack:
        yield {
            name: stack.enter_context(
                patch(
                    f"services.weather_config_service.{name}",
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


# ---------------------------------------------------------------------------
# The module gate
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("command, setter, value, _log", COMMANDS, ids=COMMAND_IDS)
async def test_command_is_refused_while_the_weather_module_is_disabled(
    command, setter, value, _log
):
    """"All three commands shall be rejected while the weather module is disabled." """
    cog = _make_cog(weather_enabled=False)
    interaction = _interaction()

    with _patched_setters() as mocks:
        await _invoke(command, cog, interaction, value)

        # Nothing was written for a module that is switched off.
        for name in ALL_SETTERS:
            mocks[name].assert_not_awaited()

    interaction.response.send_message.assert_awaited_once()
    assert "not enabled" in _sent_text(interaction)
    cog.bot.output_router.post_log.assert_not_awaited()


# ---------------------------------------------------------------------------
# The active-season gate
# ---------------------------------------------------------------------------


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
    cog.bot.output_router.post_log.assert_not_awaited()


# ---------------------------------------------------------------------------
# The minimum of 1
# ---------------------------------------------------------------------------


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
    cog.bot.output_router.post_log.assert_not_awaited()


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

    assert refusal in _sent_text(interaction)
    # A refusal is not a change, so nothing is written to the log channel.
    cog.bot.output_router.post_log.assert_not_awaited()


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
    the whole ordering it now has without a command to read it back — the specification
    provides none.
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


@pytest.mark.parametrize("command, setter, value, log_token", COMMANDS, ids=COMMAND_IDS)
async def test_success_is_written_to_the_log_channel(command, setter, value, log_token):
    """"... and shall be written to the log channel." """
    cog = _make_cog()
    interaction = _interaction()

    with _patched_setters():
        await _invoke(command, cog, interaction, value)

    cog.bot.output_router.post_log.assert_awaited_once()
    logged_server, logged_text = cog.bot.output_router.post_log.await_args.args
    assert logged_server == SERVER_ID
    assert log_token in logged_text
    assert str(value) in logged_text
    # The log names who made the change, not merely that it happened.
    assert "Race Control" in logged_text


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
