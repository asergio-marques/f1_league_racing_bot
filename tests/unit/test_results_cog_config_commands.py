"""`/results config append` and `/results config remove` at the cog surface (#132).

Both commands used to take a name on trust. Append recorded a link to whatever was typed,
so a typo attached a phantom and reported success; remove deleted the store row and left
every link to it standing. Either one left a season attached to a configuration that was
not there, and the approval that then read it said nothing whatever.

These drive the callbacks rather than the services beneath them, because what the two fixes
change is what a manager is *told*: the refusal has to name the configuration, and the
removal has to ask before it costs a season its points.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from cogs.results_cog import ResultsCog, _ConfirmRemoveConfigView  # noqa: E402
from db.database import get_connection, run_migrations  # noqa: E402
from models.points_config import SessionType  # noqa: E402
from models.server_config import ServerConfig  # noqa: E402
from services import points_config_service, season_points_service  # noqa: E402

SERVER_ID = 7700
SEASON_ID = 21
USER_ID = 88
CHANNEL_ID = 2
ADMIN_ROLE = 444


@pytest.fixture
async def db_path(tmp_path):
    path = str(tmp_path / "results_cog.db")
    await run_migrations(path)
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 1, 2, 3)",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO seasons (id, start_date, status, season_number) "
            "VALUES (?, '2026-03-01', 'SETUP', 4)",
            (SEASON_ID,),
        )
        await db.commit()
    return path


def _server_config() -> ServerConfig:
    return ServerConfig(
        server_id=SERVER_ID,
        interaction_role_id=222,
        league_admin_role_id=ADMIN_ROLE,
        interaction_channel_id=CHANNEL_ID,
        log_channel_id=3,
    )


def _cog(db_path):
    cog = ResultsCog.__new__(ResultsCog)
    cog.bot = AsyncMock()
    cog.bot.db_path = db_path
    cog.bot.season_service.get_season_for_server = AsyncMock(
        return_value=SimpleNamespace(id=SEASON_ID, status="SETUP")
    )
    # The channel-and-role guard wraps both callbacks. Satisfied rather than bypassed, so
    # these run the command the way a manager reaches it; the guard has its own tests.
    cog.bot.config_service.get_server_config = AsyncMock(return_value=_server_config())
    # The module gate is not what these are about; it has its own tests.
    cog._module_gate = AsyncMock(return_value=True)
    return cog


def _interaction():
    role = MagicMock()
    role.id = ADMIN_ROLE
    role.name = "League Admin"

    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.channel_id = CHANNEL_ID
    interaction.user = MagicMock(spec=discord.Member)
    interaction.user.id = USER_ID
    interaction.user.display_name = "Manager"
    interaction.user.roles = [role]
    interaction.guild.get_role = lambda role_id: role if role_id == ADMIN_ROLE else None
    interaction.response.defer = AsyncMock()
    interaction.response.send_message = AsyncMock()
    interaction.followup.send = AsyncMock()
    return interaction


def _replies(interaction) -> str:
    return "\n".join(
        str(call.args[0]) for call in interaction.followup.send.await_args_list if call.args
    )


def _views(interaction) -> list:
    return [
        call.kwargs["view"]
        for call in interaction.followup.send.await_args_list
        if call.kwargs.get("view") is not None
    ]


async def _make_config(db_path, name: str) -> None:
    await points_config_service.create_config(db_path, name)
    await points_config_service.set_session_points(
        db_path, name, SessionType.FEATURE_RACE, 1, 25
    )


# ── /results config append ────────────────────────────────────────────────


async def test_config_append_refuses_an_unknown_name(db_path):
    """The typo #132 opens with. It used to report success and attach a phantom."""
    cog = _cog(db_path)
    interaction = _interaction()
    await _make_config(db_path, "Standard")

    await ResultsCog.config_append.callback(cog, interaction, "Standrad")

    replies = _replies(interaction)
    assert "Standrad" in replies
    assert "does not exist" in replies
    assert await season_points_service.get_attached_config_names(db_path, SEASON_ID) == []


async def test_config_append_does_not_claim_success_for_a_typo(db_path):
    """Said separately: the reported fault was being *told it worked*."""
    cog = _cog(db_path)
    interaction = _interaction()

    await ResultsCog.config_append.callback(cog, interaction, "Standrad")

    assert "attached to the current season" not in _replies(interaction)


async def test_config_append_still_attaches_a_real_name(db_path):
    """A gate that refuses everything is no better than one that refuses nothing."""
    cog = _cog(db_path)
    interaction = _interaction()
    await _make_config(db_path, "Standard")

    await ResultsCog.config_append.callback(cog, interaction, "Standard")

    assert "Standard" in _replies(interaction)
    assert await season_points_service.get_attached_config_names(db_path, SEASON_ID) == [
        "Standard"
    ]


# ── /results config remove ────────────────────────────────────────────────


async def test_config_remove_goes_straight_through_with_no_links(db_path):
    """Nothing is at stake, so nothing is asked — the command behaves as it always did."""
    cog = _cog(db_path)
    interaction = _interaction()
    await _make_config(db_path, "Spare")

    await ResultsCog.config_remove.callback(cog, interaction, "Spare")

    assert "removed" in _replies(interaction)
    assert _views(interaction) == []
    assert await points_config_service.config_exists(db_path, "Spare") is False


async def test_config_remove_asks_before_clearing_a_setup_link(db_path):
    """The confirmation, and that it names the season the manager would be changing."""
    cog = _cog(db_path)
    interaction = _interaction()
    await _make_config(db_path, "Standard")
    await season_points_service.attach_config(
        db_path, SEASON_ID, "Standard", "SETUP"
    )

    await ResultsCog.config_remove.callback(cog, interaction, "Standard")

    replies = _replies(interaction)
    assert "Season #4" in replies, "the prompt must name the season that is affected"
    assert len(_views(interaction)) == 1
    assert await points_config_service.config_exists(db_path, "Standard") is True, (
        "nothing may be deleted before the manager answers"
    )


async def test_config_remove_reports_a_name_that_is_not_there(db_path):
    cog = _cog(db_path)
    interaction = _interaction()

    await ResultsCog.config_remove.callback(cog, interaction, "Ghost")

    assert "not found" in _replies(interaction)
    assert _views(interaction) == []


async def test_confirming_the_removal_deletes_it_and_clears_the_link(db_path):
    """`async def` throughout this file: apt's discord.py 2.5.0 wants a running loop in
    `View.__init__`, and a sync test building one passes on CI and fails on the Pi."""
    cog = _cog(db_path)
    await _make_config(db_path, "Standard")
    await season_points_service.attach_config(
        db_path, SEASON_ID, "Standard", "SETUP"
    )
    view = _ConfirmRemoveConfigView(cog, USER_ID, "Standard")
    interaction = _interaction()

    await view.confirm.callback(interaction)

    assert await points_config_service.config_exists(db_path, "Standard") is False
    assert await season_points_service.get_attached_config_names(db_path, SEASON_ID) == []


async def test_cancelling_the_removal_leaves_everything_alone(db_path):
    cog = _cog(db_path)
    await _make_config(db_path, "Standard")
    await season_points_service.attach_config(
        db_path, SEASON_ID, "Standard", "SETUP"
    )
    view = _ConfirmRemoveConfigView(cog, USER_ID, "Standard")
    interaction = _interaction()

    await view.cancel.callback(interaction)

    assert await points_config_service.config_exists(db_path, "Standard") is True
    assert await season_points_service.get_attached_config_names(db_path, SEASON_ID) == [
        "Standard"
    ]


async def test_only_the_manager_who_asked_may_answer(db_path):
    """An admin's irreversible delete is not something a bystander gets to confirm."""
    cog = _cog(db_path)
    await _make_config(db_path, "Standard")
    view = _ConfirmRemoveConfigView(cog, USER_ID, "Standard")
    interaction = _interaction()
    interaction.user.id = USER_ID + 1

    await view.confirm.callback(interaction)

    assert await points_config_service.config_exists(db_path, "Standard") is True
