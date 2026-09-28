"""The test roster changes only while the season is in Placements (issue #220).

Fake drivers are seated, removed and cleared where real drivers are placed: in Placements.
Test mode replicates the live flow rather than inventing one of its own. Listing the roster
stays free.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from leaguebot.core.cogs.test_mode_cog import TestModeCog as _Cog
from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.models.season import SeasonStage, status_of_stage
from tests.support.undecorate import undecorate

SERVER_ID = 22030


async def _cog(tmp_path, stage: SeasonStage | None) -> _Cog:
    path = str(tmp_path / "roster_stage.db")
    await run_migrations(path)
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id, test_mode_active) VALUES (?, 1, 2, 3, 1)",
            (SERVER_ID,),
        )
        if stage is not None:
            await db.execute(
                "INSERT INTO seasons (start_date, status, season_number, stage) "
                "VALUES ('2026-09-17', ?, 1, ?)",
                (status_of_stage(stage).value, stage.value),
            )
        await db.commit()
    cog = _Cog.__new__(_Cog)
    cog.bot = MagicMock()
    cog.bot.db_path = path
    cog.bot.config_service.get_server_config = AsyncMock(
        return_value=SimpleNamespace(test_mode_active=True)
    )
    return cog


def _interaction():
    """Nothing has answered it yet, so a refusal goes out as its response."""
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.response.is_done = MagicMock(return_value=False)
    interaction.response.send_message = AsyncMock()
    return interaction


async def test_the_roster_may_change_in_placements(tmp_path):
    cog = await _cog(tmp_path, SeasonStage.PLACEMENTS)
    interaction = _interaction()

    assert await cog._refuse_roster_change_outside_placements(interaction) is False
    interaction.response.send_message.assert_not_awaited()


@pytest.mark.parametrize(
    "stage", [None, SeasonStage.CONFIGURATION, SeasonStage.ONGOING, SeasonStage.COMPLETED]
)
async def test_the_roster_may_not_change_outside_placements(tmp_path, stage):
    cog = await _cog(tmp_path, stage)
    interaction = _interaction()

    assert await cog._refuse_roster_change_outside_placements(interaction) is True
    assert "only be changed while the season is in placements" in (
        interaction.response.send_message.await_args.args[0]
    )


@pytest.mark.parametrize(
    "command, args",
    [
        ("roster_add", ("Mock", "Redline", "Pro")),
        ("roster_add_bulk", ()),
        ("roster_remove", ("9000000000000000001",)),
        ("roster_clear", ("Pro",)),
    ],
)
async def test_every_roster_change_is_guarded(tmp_path, command, args):
    cog = await _cog(tmp_path, SeasonStage.ONGOING)
    interaction = _interaction()
    interaction.response.send_modal = AsyncMock()

    await undecorate(getattr(_Cog, command))(cog, interaction, *args)

    assert "only be changed while the season is in placements" in (
        interaction.response.send_message.await_args.args[0]
    )
    interaction.response.send_modal.assert_not_awaited()


# ── Every refusal is recorded (#442) ──────────────────────────────────────


def _recording(cog, interaction, command: str):
    """Give the refusal a log channel to reach and a member and a command to name."""
    cog.bot.output_router.post_log = AsyncMock()
    interaction.client = cog.bot
    interaction.user.id = 77
    interaction.user.display_name = "Admin"
    interaction.command.qualified_name = command
    return [str(c.args[0]) for c in cog.bot.output_router.post_log.await_args_list]


def _lines(cog) -> list[str]:
    return [str(c.args[0]) for c in cog.bot.output_router.post_log.await_args_list]


@pytest.mark.xfail(
    strict=True, reason="#442: the placements check writes no log line and takes no record switch"
)
@pytest.mark.parametrize("record", [True, False], ids=["recorded", "not-recorded"])
async def test_the_placements_check_logs_its_refusal_unless_record_is_false(tmp_path, record):
    """The check answers the member either way. With `record` left at its default it writes
    one line in the standard refusal form; with `record=False` it writes none."""
    cog = await _cog(tmp_path, SeasonStage.ONGOING)
    interaction = _interaction()
    _recording(cog, interaction, "test-mode roster add")

    refused = await cog._refuse_roster_change_outside_placements(interaction, record=record)

    assert refused is True
    assert "only be changed while the season is in placements" in (
        interaction.response.send_message.await_args.args[0]
    )
    if record:
        [line] = _lines(cog)
        assert line.startswith("⛔ ")
        assert "/test-mode roster add" in line
        assert "refused for Admin (<@77>)" in line
    else:
        assert _lines(cog) == []


#: Every refusal the four roster commands make through the placements check, and every
#: refusal of `/test-mode roster add` itself: the command, its arguments, the stage, how the
#: server is set, and a fragment of the reply.
ROSTER_REFUSALS = [
    pytest.param("roster_add", ("Mock", "Redline", "Pro"), SeasonStage.ONGOING, {},
                 "only be changed while the season is in placements", id="add-outside-placements"),
    pytest.param("roster_add_bulk", (), SeasonStage.ONGOING, {},
                 "only be changed while the season is in placements",
                 id="add-bulk-outside-placements"),
    pytest.param("roster_remove", ("9000000000000000001",), SeasonStage.ONGOING, {},
                 "only be changed while the season is in placements",
                 id="remove-outside-placements"),
    pytest.param("roster_clear", ("Pro",), SeasonStage.ONGOING, {},
                 "only be changed while the season is in placements", id="clear-outside-placements"),
    pytest.param("roster_add", ("Mock", "Redline", "Pro"), SeasonStage.PLACEMENTS,
                 {"test_mode_active": False}, "only available when test mode is enabled",
                 id="add-outside-test-mode"),
    pytest.param("roster_add", ("Mock", "Redline", "Pro", "British"), SeasonStage.PLACEMENTS,
                 {"test_mode_active": True, "test_mode_nationality_required": False},
                 "Nationality is switched off", id="add-with-nationality-switched-off"),
    pytest.param("roster_add", ("Mock", "Nobody", "Pro"), SeasonStage.PLACEMENTS,
                 {"test_mode_active": True, "test_mode_nationality_required": True},
                 "⛔", id="add-refused-by-the-roster"),
]


@pytest.mark.xfail(strict=True, reason="#442: a test roster refusal writes no log line")
@pytest.mark.parametrize("command, args, stage, config, said", ROSTER_REFUSALS)
async def test_every_test_roster_refusal_reaches_the_log_channel(
    tmp_path, command, args, stage, config, said
):
    """Each refusal answers the member as before and writes one line in the standard form,
    naming the command."""
    cog = await _cog(tmp_path, stage)
    if config:
        cog.bot.config_service.get_server_config = AsyncMock(
            return_value=SimpleNamespace(**config)
        )
    cog.bot.placement_service = None
    name = "test-mode roster " + command.removeprefix("roster_").replace("_", "-")
    interaction = _interaction()
    interaction.response.send_modal = AsyncMock()
    _recording(cog, interaction, name)

    await undecorate(getattr(_Cog, command))(cog, interaction, *args)

    assert said in interaction.response.send_message.await_args.args[0]
    interaction.response.send_modal.assert_not_awaited()
    [line] = _lines(cog)
    assert line.startswith("⛔ ")
    assert f"/{name}" in line
    assert "refused for Admin (<@77>)" in line
