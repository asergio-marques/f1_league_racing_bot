"""`/results config list` — naming the configurations a store holds (#200).

Every other `/results config` subcommand takes a configuration's *name* as input, and nothing
returned the names. A manager who mistyped one was told it did not exist and had no way to look
up the spelling — the dead end #132 created when it made `append` refuse an unknown name.

**The store is named by the caller, never guessed** (decided 2026-09-21). A season takes its own
copy of every attached configuration at approval, and the two diverge from that moment. A
command that picked a store for the manager would sometimes answer from the wrong one, so
`scope` is mandatory and has no default. `test_server_scope_needs_no_season` and
`test_season_scope_reads_the_season_store` are the pair that holds it.

**`scope: Server` works between seasons**, which is the case the issue was raised for: a league
returning wants to know what it already holds before building the next season. `scope: Season`
with no season refuses, and the refusal names `scope: Server` rather than dead-ending the way
`/results config view` did.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from leaguebot.results.cogs.results_cog import ResultsCog
from leaguebot.results.models.points_config import SessionType
from tests.support.undecorate import undecorate

SERVER_ID = 12008
SEASON_ID = 1


def _make_cog(*, enabled: bool = True, season: bool = True) -> ResultsCog:
    bot = MagicMock()
    bot.db_path = "/tmp/not-read.db"
    bot.module_service = MagicMock()
    bot.module_service.is_results_enabled = AsyncMock(return_value=enabled)
    bot.season_service = MagicMock()
    bot.season_service.get_season_for_server = AsyncMock(
        return_value=SimpleNamespace(id=SEASON_ID, status="ACTIVE", season_number=4)
        if season
        else None
    )
    bot.output_router = MagicMock()
    bot.output_router.post_log = AsyncMock()
    cog = ResultsCog.__new__(ResultsCog)
    cog.bot = bot
    return cog


def _interaction():
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.user = MagicMock()
    interaction.user.id = 77
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


def _scope(value: str):
    return SimpleNamespace(name=value, value=value)


async def _run(cog, interaction, scope: str):
    await undecorate(ResultsCog.config_list)(cog, interaction, _scope(scope))


# ---------------------------------------------------------------------------
# Server scope
# ---------------------------------------------------------------------------


async def test_server_scope_needs_no_season():
    """The between-seasons case #200 was raised for.

    `/results config view` refuses outright when there is no season. The server store is
    season-independent, so listing it must not.
    """
    cog = _make_cog(season=False)
    interaction = _interaction()

    with patch(
        "leaguebot.results.services.points_config_service.list_configs_with_sessions",
        AsyncMock(return_value=[("Standard", [SessionType.FEATURE_RACE])]),
    ):
        await _run(cog, interaction, "SERVER")

    reply = _replied(interaction)
    assert "Standard" in reply
    assert "No active or setup season" not in reply


async def test_server_scope_names_what_each_config_carries():
    cog = _make_cog()
    interaction = _interaction()

    with patch(
        "leaguebot.results.services.points_config_service.list_configs_with_sessions",
        AsyncMock(
            return_value=[
                ("Half Points", [SessionType.FEATURE_RACE]),
                ("Never Filled", []),
            ]
        ),
    ):
        await _run(cog, interaction, "SERVER")

    reply = _replied(interaction)
    assert "Half Points" in reply
    assert "Feature Race" in reply
    assert "no entries yet" in reply


async def test_server_scope_does_not_read_the_season_store():
    """Asked for the server, it must not answer from the season's copy."""
    cog = _make_cog()
    interaction = _interaction()
    season_listing = AsyncMock(return_value=[])

    with patch(
        "leaguebot.results.services.points_config_service.list_configs_with_sessions",
        AsyncMock(return_value=[]),
    ), patch(
        "leaguebot.results.services.season_points_service.list_season_configs_with_sessions", season_listing
    ):
        await _run(cog, interaction, "SERVER")

    season_listing.assert_not_awaited()


# ---------------------------------------------------------------------------
# Season scope
# ---------------------------------------------------------------------------


async def test_season_scope_reads_the_season_store():
    cog = _make_cog()
    interaction = _interaction()
    season_listing = AsyncMock(return_value=[("Standard", [SessionType.FEATURE_RACE])])

    with patch(
        "leaguebot.results.services.season_points_service.list_season_configs_with_sessions", season_listing
    ):
        await _run(cog, interaction, "SEASON")

    season_listing.assert_awaited_once()
    assert season_listing.await_args.args[1] == SEASON_ID
    assert "Standard" in _replied(interaction)


async def test_season_scope_names_the_season_it_read():
    """The reply says which store answered, since the manager chose one."""
    cog = _make_cog()
    interaction = _interaction()

    with patch(
        "leaguebot.results.services.season_points_service.list_season_configs_with_sessions",
        AsyncMock(return_value=[]),
    ):
        await _run(cog, interaction, "SEASON")

    assert "season 4" in _replied(interaction)


async def test_season_scope_without_a_season_points_at_server_scope():
    """The refusal names the way out rather than dead-ending (#200)."""
    cog = _make_cog(season=False)
    interaction = _interaction()

    await _run(cog, interaction, "SEASON")

    reply = _replied(interaction)
    assert "No active or setup season found" in reply
    assert "scope: Server" in reply


# ---------------------------------------------------------------------------
# Gating and logging
# ---------------------------------------------------------------------------


async def test_the_command_is_gated_on_the_results_module():
    cog = _make_cog(enabled=False)
    interaction = _interaction()
    listing = AsyncMock(return_value=[])

    with patch("leaguebot.results.services.points_config_service.list_configs_with_sessions", listing):
        await _run(cog, interaction, "SERVER")

    listing.assert_not_awaited()


async def test_a_successful_listing_reaches_the_log():
    cog = _make_cog()
    interaction = _interaction()

    with patch(
        "leaguebot.results.services.points_config_service.list_configs_with_sessions",
        AsyncMock(return_value=[]),
    ):
        await _run(cog, interaction, "SERVER")

    cog.bot.output_router.post_log.assert_awaited_once()
    assert "/results config list" in cog.bot.output_router.post_log.await_args.args[0]


async def test_the_reply_is_ephemeral():
    """A configuration listing is a manager's business, not the channel's."""
    cog = _make_cog()
    interaction = _interaction()

    with patch(
        "leaguebot.results.services.points_config_service.list_configs_with_sessions",
        AsyncMock(return_value=[]),
    ):
        await _run(cog, interaction, "SERVER")

    assert interaction.followup.send.await_args.kwargs["ephemeral"] is True
