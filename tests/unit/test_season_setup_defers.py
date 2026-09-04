"""The season-setup commands defer before they do their work.

Each of `/season setup`, `/division add`, `/round add` and `/round amend` rewrites the
whole SETUP season through `_snapshot_pending` — every division, team, seat and round
deleted and re-inserted — and `/round add` additionally loads and parses the calendar
template to check its capacity. On a season holding more than one division that work
outlasts Discord's three-second window, the interaction token expires, and the reply
raises `404 Unknown interaction` *after* the round has already been written. What a
league manager sees is a command that appears to fail while having silently succeeded.

Deferring first buys fifteen minutes, so these pin two things: that the deferral happens
before any of that work, and that every reply thereafter goes to `followup` — a
`response.send_message` after a defer is itself a 404.
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from cogs.season_cog import SeasonCog, PendingConfig, PendingDivision
from models.round import RoundFormat


def _interaction() -> MagicMock:
    """An interaction that fails the way Discord does if a reply precedes the defer."""
    import discord

    interaction = MagicMock()
    interaction.guild_id = 1
    # admin_only rejects anything that is not a Member holding Manage Server, so the
    # user has to satisfy both for the command body to be reached at all.
    interaction.user = MagicMock(spec=discord.Member)
    interaction.user.id = 42
    interaction.user.display_name = "Manager"
    interaction.user.guild_permissions.manage_guild = True
    interaction.response.defer = AsyncMock()
    interaction.response.send_message = AsyncMock(
        side_effect=AssertionError(
            "replied via response.send_message; after a defer this is a 404"
        )
    )
    interaction.followup.send = AsyncMock()
    return interaction


def _bot() -> MagicMock:
    bot = MagicMock()
    bot.db_path = ":memory:"
    # No ServerConfig: channel_guard and admin_only both pass an uninitialised
    # server straight through, which is what lets the command body be reached here.
    bot.config_service.get_server_config = AsyncMock(return_value=None)
    bot.season_service.get_active_season = AsyncMock(return_value=None)
    bot.season_service.get_setup_season = AsyncMock(return_value=None)
    bot.season_service.save_pending_snapshot = AsyncMock(return_value=(42, 1))
    bot.season_service.get_divisions = AsyncMock(return_value=[])
    bot.season_service.restore_driver_seats = AsyncMock()
    bot.team_service.seed_division_teams = AsyncMock()
    bot.output_router.post_log = AsyncMock()
    return bot


def _pending(server_id: int = 1) -> PendingConfig:
    div = PendingDivision(
        name="Pro",
        role_id=10,
        channel_id=20,
        tier=1,
        rounds=[
            {
                "round_number": 1,
                "format": RoundFormat.NORMAL,
                "track_name": "United Kingdom",
                "scheduled_at": datetime(2026, 5, 1, 14, 0, 0),
            }
        ],
    )
    return PendingConfig(server_id=server_id, divisions=[div], season_id=7)


def _cog(pending: PendingConfig | None) -> SeasonCog:
    cog = SeasonCog(_bot())
    if pending is not None:
        cog._pending[pending.server_id] = pending
    return cog


async def _call(coro_method, interaction, **kwargs):
    """Invoke a command through its decorators, as the command tree does."""
    await coro_method.callback(*coro_method.binding_args, interaction, **kwargs)


async def test_round_add_defers_before_touching_the_database(monkeypatch):
    """The reported bug: the round was written, then the reply hit an expired token."""
    cog = _cog(_pending())
    interaction = _interaction()

    order: list[str] = []
    interaction.response.defer = AsyncMock(side_effect=lambda **_: order.append("defer"))

    async def _snapshot(cfg):
        order.append("snapshot")

    async def _overflow(server_id, would_hold):
        order.append("overflow")
        return None

    monkeypatch.setattr(cog, "_snapshot_pending", _snapshot)
    monkeypatch.setattr(cog, "_calendar_round_overflow", _overflow)
    monkeypatch.setattr(
        "services.track_service.resolve_track_name",
        AsyncMock(return_value="Hungaroring"),
    )

    await cog.round_add.callback(
        cog,
        interaction,
        division_name="Pro",
        format="NORMAL",
        scheduled_at="2026-10-22T18:00",
        track="14 – Hungaroring",
    )

    assert order and order[0] == "defer", (
        f"deferred too late: {order} — the slow work must not precede the defer"
    )
    interaction.followup.send.assert_awaited()


@pytest.mark.parametrize(
    "command, kwargs",
    [
        ("season_setup", {"game_edition": 2026}),
        ("division_add", {}),
        ("round_add", {}),
        ("round_amend", {}),
    ],
)
def test_every_setup_command_defers_first(command, kwargs):
    """A source-level check: the defer is the first statement that awaits anything.

    Driving all four through their real bodies would need four different sets of
    stubs; what actually regresses is someone adding a query above the defer, and
    that is visible here without pretending to exercise the command.
    """
    import inspect

    src = inspect.getsource(getattr(SeasonCog, command).callback)
    body = src.split("-> None:", 1)[1]
    defer_at = body.index("interaction.response.defer")
    for earlier in ("await self.bot.", "get_connection(", "await interaction.followup"):
        found = body.find(earlier)
        assert found == -1 or found > defer_at, (
            f"{command}: {earlier!r} runs before the defer"
        )


@pytest.mark.parametrize(
    "command", ["season_setup", "division_add", "round_add", "round_amend"]
)
def test_no_setup_command_replies_through_response(command):
    """After a defer the only valid reply is a followup; send_message would 404."""
    import inspect

    src = inspect.getsource(getattr(SeasonCog, command).callback)
    assert "interaction.response.send_message" not in src, (
        f"{command} still replies via response.send_message after deferring"
    )
