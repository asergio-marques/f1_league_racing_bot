"""Every `/weather config` deadline command holds its guards and saves through the change queue.

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
specification's "approved/active" is `get_confirmed_season`.

**Since #439 (slice 7) each deadline is a settings change on the change queue:** refused at
once in today's words and order where it cannot be carried out, nothing queued; otherwise
acknowledged at once naming what is being set, and updated with today's reply once the change
has run, its value, its `WEATHER_CONFIG_SET` audit entry and its log line saved together. A value
already held is no refusal: it is judged when the change runs and answered "Nothing changed".
The deadline tests drive the real command past its tier guard, on a database built by the
migrations, with the real services and the real queue, and run the queue; the change's own rules
are pinned in `test_weather_settings.py`.

The coverage is deliberately driven across all three commands rather than one, so a change
that stops halfway is caught on whichever command it lands on.

`/weather config view` is covered at the end (issue #118). It shares the setters' module gate
and must not share their season gate: the deadlines were once readable only in the two season
reviews, each tied to one stage of one season. It reads at once and is not a change, so its
tests keep their spec-bound doubles: **`spec=` is load-bearing there**, as it is in
`test_attendance_config_commands.py` — a cog calling a method the real service does not define
raises `AttributeError` rather than being silently recorded (#119). Do not relax it.

**Every outcome is recorded in the log channel** (#482; the core specification's "The record
of what changed"): a refusal as one "⛔" line naming the command, a success as one line naming
the command with the three deadlines beneath, and a value the deadline already holds as one
"Nothing changed" line.
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from leaguebot.core.db.database import get_connection
from leaguebot.core.models.season import Season, SeasonStatus
from leaguebot.core.services.season_service import SeasonService
from leaguebot.weather.cogs.weather_cog import WeatherCog
from leaguebot.weather.models.weather_config import WeatherPipelineConfig
from tests.support.change_queue import MEMBER_ID, acknowledgement, change_rows, run_queue
from tests.support.saves import set_deadline
from tests.support.settings_league import (
    audit_rows,
    interaction,
    last_said,
    log_lines,
    said,
    settings_league,
    switch,
)
from tests.support.undecorate import undecorate

SERVER_ID = 7161
DB_PATH = "/nonexistent/weather.db"  # the view tests' doubles never open it

#: The member as a log line names them, the mention wrapped so it notifies nobody.
NAMED = f"Admin (`<@{MEMBER_ID}>`)"
MODULE_OFF = "❌ The Weather module is not enabled."
CONFIRMED = (
    "❌ Phase deadline configuration cannot be changed once a season's placements are confirmed."
)
#: The defaults a league starts from: Phase 1 5 days, Phase 2 2 days, Phase 3 2 hours.
DEFAULTS = {"phase_1_days": 5, "phase_2_days": 2, "phase_3_hours": 2}

#: Each command: its method, its argument, its column, a legal value from the defaults, the
#: deadline as its replies name it with that value, and its refusal below one.
COMMANDS = {
    1: ("phase_1_deadline", "days", "phase_1_days", 10, ("Phase 1", "10 day(s)"),
        "❌ Phase 1 deadline must be at least 1 day."),
    2: ("phase_2_deadline", "days", "phase_2_days", 3, ("Phase 2", "3 day(s)"),
        "❌ Phase 2 deadline must be at least 1 day."),
    3: ("phase_3_deadline", "hours", "phase_3_hours", 6, ("Phase 3", "6 hour(s)"),
        "❌ Phase 3 deadline must be at least 1 hour."),
}
PHASES = [1, 2, 3]
COMMAND_IDS = ["phase_1", "phase_2", "phase_3"]


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _ask(league: Any, phase: int, value: int) -> Any:
    """Run `/weather config phase-<phase>-deadline <value>` as Admin; the queue is not run."""
    method, argument = COMMANDS[phase][:2]
    asked = interaction(league.bot, f"weather config phase-{phase}-deadline")
    await undecorate(getattr(WeatherCog, method))(WeatherCog(league.bot), asked, **{argument: value})
    return asked


async def _deadlines(db_path: str) -> dict[str, int]:
    """The three deadlines the league holds, the defaults where it has saved none."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT phase_1_days, phase_2_days, phase_3_hours FROM weather_pipeline_config"
        )
        row = await cursor.fetchone()
    return dict(row) if row is not None else dict(DEFAULTS)


async def _confirm_a_season(db_path: str) -> None:
    """A season whose placements are confirmed: active, under way."""
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO seasons (id, season_number, start_date, status, stage) "
            "VALUES (1, 1, '2026-01-01', 'ACTIVE', 'ONGOING')"
        )
        await db.commit()


def _refused(reply: str, phase: int) -> str:
    """The "⛔" line recording *reply* as the refusal of the Phase *phase* command."""
    return (
        f"⛔ `/weather config phase-{phase}-deadline` refused for {NAMED} — "
        f"{reply.removeprefix('❌ ')}"
    )


async def _assert_refused_at_once(league: Any, asked: Any, reply: str, phase: int) -> None:
    """*reply* was the member's whole answer, nothing was queued or saved, and one "⛔" line
    records it."""
    assert said(asked) == [reply]
    assert await change_rows(league.db_path) == []
    assert await _deadlines(league.db_path) == DEFAULTS
    assert await log_lines(league) == [_refused(reply, phase)]


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


@pytest.mark.parametrize("phase", PHASES, ids=COMMAND_IDS)
async def test_command_is_refused_while_the_weather_module_is_disabled(tmp_path, phase):
    """"All three commands shall be rejected while the weather module is disabled." With weather
    off, the command is refused at once in the module's words, as `/weather channel` and the other
    modules name it ("The Weather module"), nothing is queued or saved, and the refusal is recorded
    as one "⛔" line naming the command."""
    league = await settings_league(tmp_path)
    await switch(league.bot, ("weather",), on=False)

    asked = await _ask(league, phase, COMMANDS[phase][3])

    await _assert_refused_at_once(league, asked, MODULE_OFF, phase)


# ---------------------------------------------------------------------------
# The active-season gate
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("phase", PHASES, ids=COMMAND_IDS)
async def test_command_is_refused_while_a_season_is_active(tmp_path, phase):
    """"If there is an ongoing season ... all three commands must be rejected."

    The deadlines in force for a season are those stored when it was approved, so letting a
    change through mid-season would silently disagree with the horizons its rounds were
    armed at. With a season's placements confirmed, the command is refused at once, nothing
    queued or saved, one "⛔" line recording it.
    """
    league = await settings_league(tmp_path)
    await _confirm_a_season(league.db_path)

    asked = await _ask(league, phase, COMMANDS[phase][3])

    await _assert_refused_at_once(league, asked, CONFIRMED, phase)


# ---------------------------------------------------------------------------
# The minimum of 1
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("phase", PHASES, ids=COMMAND_IDS)
@pytest.mark.parametrize("bad_value", [0, -1, -100], ids=["zero", "negative", "very_negative"])
async def test_command_rejects_a_value_below_one(tmp_path, phase, bad_value):
    """"All three commands shall reject any value below 1." Refused at once, nothing queued or
    saved, one "⛔" line recording it."""
    league = await settings_league(tmp_path)

    asked = await _ask(league, phase, bad_value)

    await _assert_refused_at_once(league, asked, COMMANDS[phase][5], phase)


@pytest.mark.parametrize(
    "phase",
    [
        1,
        pytest.param(2),
        pytest.param(3),
    ],
    ids=COMMAND_IDS,
)
async def test_one_is_accepted(tmp_path, phase):
    """1 is the smallest legal value — the boundary the rule above sits on. Phase 2 at 1 day and
    Phase 3 at 1 hour pass every rule and are asked as a change carrying the column and the
    value 1. Phase 1 at 1 day passes the minimum and meets the ordering rule instead, since no
    Phase 2 can sit under one day: its refusal is the ordering's, never "at least 1"."""
    league = await settings_league(tmp_path)
    column = COMMANDS[phase][2]

    asked = await _ask(league, phase, 1)

    if phase == 1:
        assert said(asked) == [
            "❌ Phase 1 deadline (1d = 24h) must be strictly greater than "
            "Phase 2 deadline (2d = 48h)."
        ]
        return
    (row,) = await change_rows(league.db_path)
    assert row["kind"] == "weather.setting.deadline"
    assert json.loads(row["payload"]) == {"column": column, "value": 1}


# ---------------------------------------------------------------------------
# The ordering invariant, as the league meets it
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("phase", "value", "refusal"),
    [
        (1, 2, "Phase 1 deadline (2d = 48h) must be strictly greater than "
               "Phase 2 deadline (2d = 48h)."),
        (2, 6, "Phase 1 deadline (5d = 120h) must be strictly greater than "
               "Phase 2 deadline (6d = 144h)."),
        (3, 48, "Phase 2 deadline (2d = 48h) must be strictly greater than "
                "Phase 3 deadline (48h)."),
    ],
    ids=COMMAND_IDS,
)
async def test_service_rejection_reaches_the_user_unchanged(tmp_path, phase, value, refusal):
    """A value out of order with the deadlines held is refused at once with the ordering rule's
    own sentence, both offending values in hours, nothing queued or saved.

    The specification requires the rejection to state both offending values in hours. A command
    that swallowed it, or replaced it with a generic refusal, would leave the league unable to
    see which pair collided. The refusal is recorded too, with that sentence as its reason.
    """
    league = await settings_league(tmp_path)

    asked = await _ask(league, phase, value)

    await _assert_refused_at_once(league, asked, f"❌ {refusal}", phase)


# ---------------------------------------------------------------------------
# The successful path
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("phase", PHASES, ids=COMMAND_IDS)
async def test_success_reaches_the_service_with_the_value(tmp_path, phase):
    """The command asks one deadline change carrying its column and the value typed, and once it
    has run that column holds the value and the other two are as they were."""
    league = await settings_league(tmp_path)
    _method, _argument, column, value, _shown, _below = COMMANDS[phase]

    await _ask(league, phase, value)
    (row,) = await change_rows(league.db_path)
    assert row["kind"] == "weather.setting.deadline"
    assert json.loads(row["payload"]) == {"column": column, "value": value}
    await run_queue(league.bot)

    assert await _deadlines(league.db_path) == {**DEFAULTS, column: value}


@pytest.mark.parametrize("phase", PHASES, ids=COMMAND_IDS)
async def test_success_reports_all_three_deadlines(tmp_path, phase):
    """"Each successful command shall report the resulting values of all three deadlines."

    With the league holding Phase 1 9 days, Phase 2 4 days and Phase 3 8 hours, the command is
    acknowledged at once, and once its change has run the reply names the other two as well as
    the one just changed, so the league can see the whole ordering it now has without running
    `/weather config view` after it.
    """
    league = await settings_league(tmp_path)
    for column, held in (("phase_1_days", 9), ("phase_2_days", 4), ("phase_3_hours", 8)):
        await set_deadline(league.db_path, column, held)

    asked = await _ask(league, phase, COMMANDS[phase][3])
    assert acknowledgement(asked).startswith("⏳ ")
    await run_queue(league.bot)

    text = last_said(asked)
    others = {1: ["4d", "8h"], 2: ["9d", "8h"], 3: ["9d", "4d"]}[phase]
    for token in others:
        assert token in text, f"{token!r} missing from {text!r}"


@pytest.mark.parametrize("phase", PHASES, ids=COMMAND_IDS)
async def test_success_is_written_to_the_log_channel(tmp_path, phase):
    """"... and shall be written to the log channel." Nothing is recorded when the command is
    asked; once its change has run, one line in the standard form — the member, the command a
    league types and the outcome, with the three deadlines now in force beneath."""
    league = await settings_league(tmp_path)
    for column, held in (("phase_1_days", 9), ("phase_2_days", 4), ("phase_3_hours", 8)):
        await set_deadline(league.db_path, column, held)
    column, value = COMMANDS[phase][2], COMMANDS[phase][3]
    now = {"phase_1_days": 9, "phase_2_days": 4, "phase_3_hours": 8, column: value}

    await _ask(league, phase, value)
    assert await log_lines(league) == []
    await run_queue(league.bot)

    assert await log_lines(league) == [
        f"{NAMED} | /weather config phase-{phase}-deadline | Success\n"
        f"  Phase 1: {now['phase_1_days']} day(s)\n"
        f"  Phase 2: {now['phase_2_days']} day(s)\n"
        f"  Phase 3: {now['phase_3_hours']} hour(s)"
    ]


@pytest.mark.parametrize("phase", PHASES, ids=COMMAND_IDS)
async def test_success_is_acknowledged_before_the_save(tmp_path, phase):
    """The command answers at once, without deferring, that the deadline is being set, and saves
    nothing until its change runs: the answer is then updated with the success."""
    league = await settings_league(tmp_path)
    _method, _argument, column, value, (deadline, shown), _below = COMMANDS[phase]

    asked = await _ask(league, phase, value)

    asked.response.defer.assert_not_awaited()
    assert said(asked) == [acknowledgement(asked)]
    assert acknowledgement(asked).startswith(f"⏳ Setting the {deadline} deadline to {shown}. ")
    assert await _deadlines(league.db_path) == DEFAULTS
    await run_queue(league.bot)
    assert last_said(asked).startswith(f"✅ {deadline} deadline set to **{shown}** before round.")
    assert (await _deadlines(league.db_path))[column] == value


# ---------------------------------------------------------------------------
# A value already held
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("phase", PHASES, ids=COMMAND_IDS)
async def test_a_held_value_changes_nothing(tmp_path, phase):
    """A deadline given the value it already holds is not a change, and no refusal: it is
    acknowledged, and once its change has run the reply says nothing changed, the log channel
    records one "Nothing changed" line naming the value, and no audit entry is written."""
    league = await settings_league(tmp_path)
    column = COMMANDS[phase][2]
    deadline = COMMANDS[phase][4][0]
    held = DEFAULTS[column]
    shown = f"{held} hour(s)" if phase == 3 else f"{held} day(s)"

    asked = await _ask(league, phase, held)
    assert acknowledgement(asked).startswith(f"⏳ Setting the {deadline} deadline to {shown}. ")
    await run_queue(league.bot)

    assert last_said(asked) == f"ℹ️ Nothing changed: the {deadline} deadline is already **{shown}**."
    assert await log_lines(league) == [
        f"{NAMED} | /weather config phase-{phase}-deadline | Nothing changed\n  {deadline}: {shown}"
    ]
    assert await audit_rows(league.db_path) == []


@pytest.mark.parametrize("phase", PHASES, ids=COMMAND_IDS)
async def test_a_held_value_is_still_refused_while_a_season_is_active(tmp_path, phase):
    """Every refusal comes first: a value already held, sent while a season's placements are
    confirmed, is refused at once as any other value is, never answered as "Nothing changed",
    and nothing is queued."""
    league = await settings_league(tmp_path)
    await _confirm_a_season(league.db_path)

    asked = await _ask(league, phase, DEFAULTS[COMMANDS[phase][2]])

    await _assert_refused_at_once(league, asked, CONFIRMED, phase)


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
