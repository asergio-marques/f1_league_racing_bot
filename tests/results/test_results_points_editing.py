"""Editing a points table, and the warning a broken one earns.

Issue #208, continuing `tests/results/test_results_config_commands.py`. Where that file covered
creating and destroying a configuration, this one covers filling it in: the per-position points,
the fastest-lap bonus and its eligibility limit, and attaching a configuration to a season.

**A points edit warns rather than refuses** (decided 2026-09-14), and the warning has a job
beyond naming the fault. The edit has *already been applied* by the time the notice is written,
so the notice has to say who will refuse later — the season's approval, or the amendment's —
otherwise a manager reads it as a note they can pass over. `_ordering_notice` returns an empty
string when nothing is wrong, so callers concatenate it unconditionally; that is why
`test_a_table_in_order_earns_no_notice` matters as much as its opposite.

**The rule the notice enforces is that a lower position cannot be worth as much as the one
above it.** A table that breaks it would award a driver more for finishing behind somebody,
which is not a scoring system a championship can be run on.

**The fastest-lap commands refuse qualifying outright.** There is no fastest lap to award in a
qualifying session, and the refusal is distinct from "config not found" — a manager who typed
the wrong session needs to be told which of the two went wrong.

**Attaching is refused outside SETUP.** The attachment decides how a whole season is scored, so
changing it mid-season would rescore the rounds already run by different rules than the ones
they were run under. The refusal for a configuration that does not exist says so separately, and
names the command that creates one — a manager attaching a mistyped name would otherwise think
the season was configured when it was not.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from leaguebot.results.cogs.results_cog import (
    BLOCKS_AMENDMENT,
    BLOCKS_APPROVAL,
    ResultsCog,
    _ordering_notice,
)
from leaguebot.results.models.points_config import SessionType
from leaguebot.results.services.points_config_service import (
    ConfigNotFoundError,
    InvalidSessionTypeError,
)
from leaguebot.results.services.season_points_service import SeasonNotInSetupError
from tests.support.undecorate import undecorate

SERVER_ID = 11308
SEASON_ID = 3
ACTOR_ID = 77
CONFIG = "100%"


# ---------------------------------------------------------------------------
# The ordering notice
# ---------------------------------------------------------------------------


def test_a_table_in_order_earns_no_notice():
    """Callers concatenate this unconditionally, so it has to be empty rather than a
    cheerful sentence — every successful edit would otherwise carry one."""
    assert _ordering_notice(CONFIG, "Feature Race", []) == ""


def test_a_broken_table_names_the_configuration_and_the_session():
    """A league can hold several configurations and each has four tables; a notice naming
    neither leaves a manager checking all of them."""
    notice = _ordering_notice(CONFIG, "Feature Race", ["P5 (10) ≥ P4 (10)"])

    assert CONFIG in notice
    assert "Feature Race" in notice


def test_the_notice_lists_every_violation():
    """One fix often reveals the next, and a manager working from a partial list would
    come back to the same command repeatedly."""
    notice = _ordering_notice(
        CONFIG, "Feature Race", ["P5 (10) ≥ P4 (10)", "P8 (4) ≥ P7 (3)"]
    )

    assert "P5" in notice
    assert "P8" in notice


def test_the_notice_states_the_rule_that_was_broken():
    """Naming the positions is not enough — a manager has to know *why* those two are a
    problem before they can decide which to change."""
    notice = _ordering_notice(CONFIG, "Feature Race", ["P5 (10) ≥ P4 (10)"])

    assert "cannot be worth as much as the one above it" in notice


def test_the_notice_says_the_change_was_saved_anyway():
    """The edit has already been applied. A warning that read like a refusal would have a
    manager re-running a command that had already worked."""
    notice = _ordering_notice(CONFIG, "Feature Race", ["P5 (10) ≥ P4 (10)"])

    assert "has been saved" in notice


def test_the_notice_says_which_refusal_is_coming():
    """Its whole job beyond naming the fault. The two consequences differ, and a manager
    mid-amendment is not blocked by the season's approval."""
    approval = _ordering_notice(CONFIG, "Feature Race", ["x"], BLOCKS_APPROVAL)
    amendment = _ordering_notice(CONFIG, "Feature Race", ["x"], BLOCKS_AMENDMENT)

    assert "season cannot be approved" in approval
    assert "amendment cannot be approved" in amendment
    assert approval != amendment


# ---------------------------------------------------------------------------
# Fixtures for the commands
# ---------------------------------------------------------------------------


def _make_cog(*, results_enabled: bool = True, season=SimpleNamespace(id=SEASON_ID, status="SETUP")):
    bot = MagicMock()
    bot.db_path = "/tmp/does-not-matter.db"
    bot.module_service = MagicMock()
    bot.module_service.is_results_enabled = AsyncMock(return_value=results_enabled)
    bot.season_service = MagicMock()
    bot.season_service.get_season_for_server = AsyncMock(return_value=season)
    bot.output_router = MagicMock()
    bot.output_router.post_log = AsyncMock(return_value=None)

    cog = ResultsCog.__new__(ResultsCog)
    cog.bot = bot
    return cog


def _interaction():
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.user = MagicMock()
    interaction.user.id = ACTOR_ID
    interaction.user.display_name = "Manager"
    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    return interaction


def _replied(interaction) -> str:
    return "\n".join(
        str(call.args[0])
        for call in interaction.response.send_message.await_args_list
        + interaction.followup.send.await_args_list
        if call.args
    )


@contextmanager
def _points_service(**overrides):
    """Patch the points config service; yields the mocks by name."""
    mocks = {
        "set_session_points": AsyncMock(return_value=None),
        "set_fl_bonus": AsyncMock(return_value=None),
        "set_fl_position_limit": AsyncMock(return_value=None),
        "ordering_warnings": AsyncMock(return_value=[]),
    }
    mocks.update(overrides)
    with ExitStack() as stack:
        for name, mock in mocks.items():
            stack.enter_context(patch(f"leaguebot.results.services.points_config_service.{name}", new=mock))
        yield mocks


@contextmanager
def _season_service(**overrides):
    mocks = {"attach_config": AsyncMock(return_value=None)}
    mocks.update(overrides)
    with ExitStack() as stack:
        for name, mock in mocks.items():
            stack.enter_context(patch(f"leaguebot.results.services.season_points_service.{name}", new=mock))
        yield mocks


def _choice(value: str, label: str):
    return SimpleNamespace(value=value, name=label)


RACE = _choice(SessionType.FEATURE_RACE.value, "Feature Race")
QUALIFYING = _choice(SessionType.FEATURE_QUALIFYING.value, "Feature Qualifying")


# ---------------------------------------------------------------------------
# /results config session
# ---------------------------------------------------------------------------


async def _session(cog, interaction, *, position: int = 1, points: int = 25, choice=RACE):
    await undecorate(ResultsCog.config_session)(
        cog, interaction, CONFIG, choice, position, points
    )


async def test_a_position_s_points_are_set():
    cog = _make_cog()
    interaction = _interaction()

    with _points_service() as svc:
        await _session(cog, interaction)

    svc["set_session_points"].assert_awaited_once()
    assert "position 1" in _replied(interaction)


async def test_setting_points_on_a_configuration_that_does_not_exist_says_so():
    cog = _make_cog()
    interaction = _interaction()

    with _points_service(set_session_points=AsyncMock(side_effect=ConfigNotFoundError(CONFIG))):
        await _session(cog, interaction)

    assert "not found" in _replied(interaction)


async def test_an_edit_that_breaks_the_order_is_saved_with_a_warning():
    """The decision this file turns on: the edit is applied and the manager warned, rather
    than the command refusing and leaving them unable to build the table in any order but
    descending."""
    cog = _make_cog()
    interaction = _interaction()

    with _points_service(
        ordering_warnings=AsyncMock(return_value=["P5 (10) ≥ P4 (10)"])
    ) as svc:
        await _session(cog, interaction, position=5, points=10)

    svc["set_session_points"].assert_awaited_once()
    replied = _replied(interaction)
    assert "out of order" in replied
    assert "has been saved" in replied


async def test_an_edit_that_keeps_the_order_carries_no_warning():
    cog = _make_cog()
    interaction = _interaction()

    with _points_service(ordering_warnings=AsyncMock(return_value=[])):
        await _session(cog, interaction)

    assert "out of order" not in _replied(interaction)


async def test_the_edit_is_logged_with_what_changed():
    cog = _make_cog()

    with _points_service():
        await _session(cog, _interaction(), position=3, points=15)

    logged = cog.bot.output_router.post_log.await_args.args[0]
    assert "config session" in logged
    assert "position: 3" in logged
    assert "points: 15" in logged


async def test_editing_is_refused_while_the_module_is_disabled():
    cog = _make_cog(results_enabled=False)
    interaction = _interaction()

    with _points_service() as svc:
        await _session(cog, interaction)

    assert "not enabled" in _replied(interaction)
    svc["set_session_points"].assert_not_awaited()


# ---------------------------------------------------------------------------
# /results config fl and fl-plimit
# ---------------------------------------------------------------------------


async def _fl(cog, interaction, *, points: int = 1, choice=RACE):
    await undecorate(ResultsCog.config_fl)(cog, interaction, CONFIG, choice, points)


async def _plimit(cog, interaction, *, limit: int = 10, choice=RACE):
    await undecorate(ResultsCog.config_fl_plimit)(cog, interaction, CONFIG, choice, limit)


async def test_the_fastest_lap_bonus_is_set():
    cog = _make_cog()
    interaction = _interaction()

    with _points_service() as svc:
        await _fl(cog, interaction, points=1)

    svc["set_fl_bonus"].assert_awaited_once()
    assert "Feature Race" in _replied(interaction)


async def test_the_eligibility_limit_is_set():
    cog = _make_cog()
    interaction = _interaction()

    with _points_service() as svc:
        await _plimit(cog, interaction, limit=10)

    svc["set_fl_position_limit"].assert_awaited_once()
    assert "top 10" in _replied(interaction)


@pytest.mark.parametrize(
    "caller,service_name",
    [(_fl, "set_fl_bonus"), (_plimit, "set_fl_position_limit")],
    ids=["fl", "fl-plimit"],
)
async def test_the_fastest_lap_commands_refuse_qualifying(caller, service_name):
    """There is no fastest lap to award in a qualifying session, and the refusal is
    distinct from "config not found" — a manager who typed the wrong session needs to be
    told which of the two went wrong."""
    cog = _make_cog()
    interaction = _interaction()

    with _points_service(
        **{service_name: AsyncMock(side_effect=InvalidSessionTypeError("qualifying"))}
    ):
        await caller(cog, interaction, choice=QUALIFYING)

    replied = _replied(interaction)
    assert "qualifying" in replied.lower()
    assert "not found" not in replied


@pytest.mark.parametrize(
    "caller,service_name",
    [(_fl, "set_fl_bonus"), (_plimit, "set_fl_position_limit")],
    ids=["fl", "fl-plimit"],
)
async def test_the_fastest_lap_commands_report_a_missing_configuration(caller, service_name):
    cog = _make_cog()
    interaction = _interaction()

    with _points_service(
        **{service_name: AsyncMock(side_effect=ConfigNotFoundError(CONFIG))}
    ):
        await caller(cog, interaction)

    assert "not found" in _replied(interaction)


@pytest.mark.parametrize("caller", [_fl, _plimit], ids=["fl", "fl-plimit"])
async def test_a_refused_fastest_lap_edit_is_not_logged(caller):
    cog = _make_cog()

    with _points_service(
        set_fl_bonus=AsyncMock(side_effect=ConfigNotFoundError(CONFIG)),
        set_fl_position_limit=AsyncMock(side_effect=ConfigNotFoundError(CONFIG)),
    ):
        await caller(cog, _interaction())

    cog.bot.output_router.post_log.assert_not_awaited()


# ---------------------------------------------------------------------------
# /results config append
# ---------------------------------------------------------------------------


async def _append(cog, interaction, name: str = CONFIG):
    await undecorate(ResultsCog.config_append)(cog, interaction, name)


async def test_a_configuration_is_attached_to_the_season():
    cog = _make_cog()
    interaction = _interaction()

    with _season_service() as svc:
        await _append(cog, interaction)

    svc["attach_config"].assert_awaited_once()
    assert "attached" in _replied(interaction)


async def test_attaching_without_a_season_is_refused():
    cog = _make_cog(season=None)
    interaction = _interaction()

    with _season_service() as svc:
        await _append(cog, interaction)

    assert "No season found" in _replied(interaction)
    svc["attach_config"].assert_not_awaited()


async def test_attaching_outside_setup_is_refused():
    """The attachment decides how a whole season is scored, so changing it mid-season
    would rescore rounds already run by rules they were not run under."""
    cog = _make_cog()
    interaction = _interaction()

    with _season_service(attach_config=AsyncMock(side_effect=SeasonNotInSetupError("active"))):
        await _append(cog, interaction)

    assert "only allowed for seasons in SETUP" in _replied(interaction)


async def test_attaching_a_configuration_that_does_not_exist_names_the_way_out():
    """A manager attaching a mistyped name would otherwise believe the season was
    configured when it was not — so the refusal says nothing was attached, and names the
    command that creates one."""
    cog = _make_cog()
    interaction = _interaction()

    with _season_service(attach_config=AsyncMock(side_effect=ConfigNotFoundError(CONFIG))):
        await _append(cog, interaction)

    replied = _replied(interaction)
    assert "nothing was attached" in replied
    assert "/results config add" in replied


async def test_a_successful_attachment_is_logged():
    cog = _make_cog()

    with _season_service():
        await _append(cog, _interaction())

    assert "config append" in cog.bot.output_router.post_log.await_args.args[0]


async def test_a_refused_attachment_is_not_logged():
    cog = _make_cog()

    with _season_service(attach_config=AsyncMock(side_effect=ConfigNotFoundError(CONFIG))):
        await _append(cog, _interaction())

    cog.bot.output_router.post_log.assert_not_awaited()
