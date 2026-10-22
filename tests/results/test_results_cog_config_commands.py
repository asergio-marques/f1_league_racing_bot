"""`/results config append` and `/results config remove` at the cog surface (#132).

Both commands used to take a name on trust. Append recorded a link to whatever was typed,
so a typo attached a phantom and reported success; remove deleted the store row and left
every link to it standing. Either one left a season attached to a configuration that was
not there, and the approval that then read it said nothing whatever.

These drive the callbacks rather than the services beneath them, because what the two fixes
change is what a manager is *told*: the refusal has to name the configuration, and the
removal has to ask before it costs a season its points.

Since #439 (slice 7) each command asks a settings change on the change queue: a refusal the
state when asked gives is answered at once, nothing queued; anything else is saved when the
change runs. The tests drive the real commands past their tier guards, on a league built by the
migrations with the real services and queue, and run the queue before reading what was saved.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from leaguebot.core.db.database import get_connection
from leaguebot.results.cogs.results_cog import ResultsCog, _ConfirmRemoveConfigView
from leaguebot.results.models.points_config import SessionType
from leaguebot.results.services import points_config_service, season_points_service
from tests.support import saves
from tests.support.change_queue import MEMBER_ID, change_rows, member, run_queue
from tests.support.settings_league import interaction, last_said, said, settings_league
from tests.support.undecorate import undecorate

SERVER_ID = 7700
SEASON_ID = 21

#: Why a test fails until appending a configuration runs on the change queue.
UNBUILT = "#439: appending a points configuration does not run on the change queue yet"


@pytest.fixture
async def league(tmp_path):
    """A league, results alone on, with Season #4 (id 21) in setup and no configuration."""
    league = await settings_league(tmp_path, modules=("results",))
    async with get_connection(league.db_path) as db:
        await db.execute(
            "INSERT INTO seasons (id, start_date, status, stage, season_number) "
            "VALUES (?, '2026-03-01', 'SETUP', 'CONFIGURATION', 4)",
            (SEASON_ID,),
        )
        await db.commit()
    return league


async def _run(league: Any, method: str, *args: Any, user: Any = None) -> Any:
    """Run `/results config <method>` with *args* as Admin (or *user*), then the queue."""
    asked = interaction(league.bot, f"results config {method.removeprefix('config_')}", user=user)
    await undecorate(getattr(ResultsCog, method))(ResultsCog(league.bot), asked, *args)
    await run_queue(league.bot)
    return asked


def _replies(asked) -> str:
    return "\n".join(said(asked))


def _views(asked) -> list:
    calls = asked.response.send_message.await_args_list + asked.followup.send.await_args_list
    return [call.kwargs["view"] for call in calls if call.kwargs.get("view") is not None]


async def _press(league: Any, button: str, name: str, *, user: Any = None) -> Any:
    """Press *button* of the removal's confirmation of *name*, which Admin opened, as *user*
    (Admin by default), then run the queue."""
    view = _ConfirmRemoveConfigView(ResultsCog(league.bot), MEMBER_ID, name)
    pressed = interaction(league.bot, user=user)
    await getattr(view, button).callback(pressed)
    await run_queue(league.bot)
    return pressed


async def _make_config(db_path, name: str) -> None:
    await saves.create_config(db_path, name)
    await saves.set_session_points(db_path, name, SessionType.FEATURE_RACE, 1, 25)


# ── /results config append ────────────────────────────────────────────────


async def test_config_append_refuses_an_unknown_name(league):
    """The typo #132 opens with. It used to report success and attach a phantom: refused at
    once, nothing queued or attached."""
    await _make_config(league.db_path, "Standard")

    asked = await _run(league, "config_append", "Standrad")

    replies = _replies(asked)
    assert "Standrad" in replies
    assert "does not exist" in replies
    assert await change_rows(league.db_path) == []
    assert await season_points_service.get_attached_config_names(league.db_path, SEASON_ID) == []


async def test_config_append_does_not_claim_success_for_a_typo(league):
    """Said separately: the reported fault was being *told it worked*."""
    asked = await _run(league, "config_append", "Standrad")

    assert "attached to the current season" not in _replies(asked)


@pytest.mark.xfail(strict=True, reason=UNBUILT)
async def test_config_append_still_attaches_a_real_name(league):
    """A gate that refuses everything is no better than one that refuses nothing: acknowledged
    at once, and attached when the change runs."""
    await _make_config(league.db_path, "Standard")

    asked = await _run(league, "config_append", "Standard")

    assert said(asked)[0].startswith("⏳ ")
    assert "Standard" in last_said(asked)
    assert await season_points_service.get_attached_config_names(league.db_path, SEASON_ID) == [
        "Standard"
    ]


# ── /results config remove ────────────────────────────────────────────────


async def test_config_remove_goes_straight_through_with_no_links(league):
    """Nothing is at stake, so nothing is asked — the configuration is gone once the change has
    run."""
    await _make_config(league.db_path, "Spare")

    asked = await _run(league, "config_remove", "Spare")

    assert "removed" in last_said(asked)
    assert _views(asked) == []
    assert await points_config_service.config_exists(league.db_path, "Spare") is False


async def test_config_remove_asks_before_clearing_a_setup_link(league):
    """The confirmation, and that it names the season the manager would be changing; nothing
    is queued or deleted before the manager answers."""
    await _make_config(league.db_path, "Standard")
    await saves.attach_config(league.db_path, SEASON_ID, "Standard", "SETUP")

    asked = await _run(league, "config_remove", "Standard")

    assert "Season #4" in _replies(asked), "the prompt must name the season that is affected"
    assert len(_views(asked)) == 1
    assert await change_rows(league.db_path) == []
    assert await points_config_service.config_exists(league.db_path, "Standard") is True, (
        "nothing may be deleted before the manager answers"
    )


async def test_config_remove_reports_a_name_that_is_not_there(league):
    asked = await _run(league, "config_remove", "Ghost")

    assert "not found" in _replies(asked)
    assert _views(asked) == []
    assert await change_rows(league.db_path) == []


async def test_confirming_the_removal_deletes_it_and_clears_the_link(league):
    """`async def` throughout this file: apt's discord.py 2.5.0 wants a running loop in
    `View.__init__`, and a sync test building one passes on CI and fails on the Pi. Once the
    confirmation's change has run, the configuration and its link are gone."""
    await _make_config(league.db_path, "Standard")
    await saves.attach_config(league.db_path, SEASON_ID, "Standard", "SETUP")

    await _press(league, "confirm", "Standard")

    assert await points_config_service.config_exists(league.db_path, "Standard") is False
    assert await season_points_service.get_attached_config_names(league.db_path, SEASON_ID) == []


async def test_cancelling_the_removal_leaves_everything_alone(league):
    await _make_config(league.db_path, "Standard")
    await saves.attach_config(league.db_path, SEASON_ID, "Standard", "SETUP")

    await _press(league, "cancel", "Standard")

    assert await change_rows(league.db_path) == []
    assert await points_config_service.config_exists(league.db_path, "Standard") is True
    assert await season_points_service.get_attached_config_names(league.db_path, SEASON_ID) == [
        "Standard"
    ]


async def test_only_the_manager_who_asked_may_answer(league):
    """An admin's irreversible delete is not something a bystander gets to confirm."""
    await _make_config(league.db_path, "Standard")

    await _press(league, "confirm", "Standard", user=member(MEMBER_ID + 1, "Sam", "Sam#0001"))

    assert await change_rows(league.db_path) == []
    assert await points_config_service.config_exists(league.db_path, "Standard") is True


# ── /results config add ───────────────────────────────────────────────────


async def test_a_points_configuration_name_with_everyone_is_refused(league):
    """Held to the rules every name a league types is held to (#362): refused at once."""
    asked = await _run(league, "config_add", "@everyone 100%")

    assert "configuration name" in _replies(asked)
    assert await change_rows(league.db_path) == []
    assert not await points_config_service.config_exists(league.db_path, "@everyone 100%")


async def test_a_points_configuration_name_with_a_member_mention_is_refused(league):
    """A configuration's name is shown in every review that lists it (#388)."""
    asked = await _run(league, "config_add", "<@123456789012345678> 100%")

    assert "configuration name" in _replies(asked)
    assert "member" in _replies(asked)
    assert await change_rows(league.db_path) == []
    assert not await points_config_service.config_exists(
        league.db_path, "<@123456789012345678> 100%"
    )


async def test_a_plain_points_configuration_name_is_still_created(league):
    await _run(league, "config_add", "100%")

    assert await points_config_service.config_exists(league.db_path, "100%")


# ---------------------------------------------------------------------------
# The module gate's refusal is recorded, save for a view (#442)
# ---------------------------------------------------------------------------


def _gate_interaction(bot, command: str):
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.user.id = 77
    interaction.user.display_name = "Manager"
    interaction.client = bot
    interaction.command.qualified_name = command
    interaction.response.is_done = MagicMock(return_value=False)
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()
    return interaction


def _gate_bot(*, results_on: bool):
    bot = MagicMock()
    bot.module_service.is_results_enabled = AsyncMock(return_value=results_on)
    bot.season_service.get_setup_or_active_season = AsyncMock(return_value=None)
    bot.output_router.post_log = AsyncMock()
    return bot


def _gate_replies(interaction) -> str:
    return "\n".join(
        str(c.args[0])
        for c in interaction.response.send_message.await_args_list
        + interaction.followup.send.await_args_list
        if c.args
    )


@pytest.mark.parametrize("record", [True, False], ids=["recorded", "not-recorded"])
async def test_the_results_module_gate_logs_its_refusal_unless_record_is_false(record):
    """The gate answers the member either way. With `record` left at its default it writes one
    line in the standard refusal form, naming the command; with `record=False` it writes none."""
    bot = _gate_bot(results_on=False)
    cog = ResultsCog.__new__(ResultsCog)
    cog.bot = bot
    interaction = _gate_interaction(bot, "results config add")

    allowed = await cog._module_gate(interaction, record=record)

    assert allowed is False
    assert "not enabled" in _gate_replies(interaction)
    lines = [str(c.args[0]) for c in bot.output_router.post_log.await_args_list]
    if record:
        [line] = lines
        assert line.startswith("⛔ ")
        assert "/results config add" in line
        assert "refused for Manager (<@77>)" in line
        assert "not enabled" in line
    else:
        assert lines == []


@pytest.mark.parametrize(
    "command, name, args, results_on",
    [
        pytest.param("config_list", "results config list", 1, False, id="config-list-module-off"),
        pytest.param("config_view", "results config view", 3, False, id="config-view-module-off"),
    ],
)
async def test_a_view_refused_by_a_shared_check_writes_no_log_line(command, name, args, results_on):
    """Views, lists and previews change nothing and record nothing: a view the module gate turns
    away is answered, and the log channel is not written. `/results amend review` leads to a
    change, so its refusals are recorded, and tests/core/test_amend_review.py pins them."""
    bot = _gate_bot(results_on=results_on)
    cog = ResultsCog.__new__(ResultsCog)
    cog.bot = bot
    interaction = _gate_interaction(bot, name)

    await undecorate(getattr(ResultsCog, command))(cog, interaction, *[MagicMock()] * args)

    assert _gate_replies(interaction), "the member was not answered"
    bot.output_router.post_log.assert_not_awaited()
