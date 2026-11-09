"""The season-setup commands defer before they do their work.

Each of `/season setup`, `/division add`, `/round add` and `/round amend` writes the SETUP
season through `_snapshot_pending`, and `/round add` and `/round amend` also load and parse
the calendar template to check its capacity. Until issue #147 the write rebuilt the whole
season, and on a season holding more than one division that work outlasted Discord's
three-second window: the interaction token expired, and the reply raised `404 Unknown
interaction` *after* the round had already been written. What a league manager saw was a
command that appeared to fail while having silently succeeded. The write is small now, but
the template parse is not, and a deferral costs nothing.

Deferring first buys fifteen minutes, so these pin two things: that the deferral happens
before any of that work, and that every reply thereafter goes to `followup` — a
`response.send_message` after a defer is itself a 404.

**`/round add-bulk` and `/round add-xml` are deliberately not in this list.** They open a
modal, and `send_modal` must be an interaction's *first* response — a deferred interaction
cannot open one. They defer inside the modal's `on_submit` instead, where the work
actually happens, and `tests/core/test_round_import_cog.py` pins that. Adding them here
would be a plausible-looking change that breaks both commands.
"""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from leaguebot.core.cogs.season_cog import SeasonCog, PendingConfig, PendingDivision
from leaguebot.core.models.server_config import ServerConfig
from leaguebot.core.models.round import RoundFormat
from leaguebot.core.models.season import SeasonStage
from tests.support.setup_reads import hold_setup

SETUP_FROM_DB = "#283: the setup is read from the cog's memory, not the database"

CHANNEL = 111
MANAGER_ROLE = 222


def _role() -> MagicMock:
    role = MagicMock()
    role.id = MANAGER_ROLE
    role.name = "Stewards"
    return role


def _config() -> ServerConfig:
    return ServerConfig(
        server_id=1,
        interaction_role_id=MANAGER_ROLE,
        league_admin_role_id=444,
        interaction_channel_id=CHANNEL,
        log_channel_id=333,
    )


def _interaction() -> MagicMock:
    """An interaction that fails the way Discord does if a reply precedes the defer."""
    import discord

    interaction = MagicMock()
    interaction.guild_id = 1
    interaction.channel_id = CHANNEL
    # The tier guard admits a Member holding the interaction role, in the interaction
    # channel, and nothing else — so the user has to satisfy both for the command body to
    # be reached at all.
    interaction.user = MagicMock(spec=discord.Member)
    interaction.user.id = 42
    interaction.user.display_name = "Manager"
    interaction.user.roles = [_role()]
    interaction.user.guild_permissions.administrator = False
    interaction.guild.get_role = lambda role_id: _role() if role_id == MANAGER_ROLE else None
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
    # A configured server. An uninitialised one used to be passed straight through by both
    # guards, which is how these tests once reached the command body without one; the tier
    # guards refuse it instead, there being no channel to check and no role to hold.
    bot.config_service.get_server_config = AsyncMock(return_value=_config())
    bot.season_service.get_confirmed_season = AsyncMock(return_value=None)
    bot.season_service.get_setup_season = AsyncMock(return_value=None)
    bot.season_service.get_stage = AsyncMock(return_value=SeasonStage.PLACEMENTS)
    bot.season_service.sync_pending_config = AsyncMock(return_value=(42, 1, []))
    bot.season_service.get_divisions = AsyncMock(return_value=[])
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
    return PendingConfig(divisions=[div], season_id=7)


def _cog(pending: PendingConfig | None) -> SeasonCog:
    bot = _bot()
    # The season being set up, read from the database (#283).
    hold_setup(bot.season_service, pending)
    return SeasonCog(bot)


async def _call(coro_method, interaction, **kwargs):
    """Invoke a command through its decorators, as the command tree does."""
    await coro_method.callback(*coro_method.binding_args, interaction, **kwargs)


@pytest.mark.xfail(strict=True, reason=SETUP_FROM_DB)
async def test_round_add_defers_before_touching_the_database(monkeypatch):
    """The reported bug: the round was written, then the reply hit an expired token."""
    cog = _cog(_pending())
    interaction = _interaction()

    order: list[str] = []
    interaction.response.defer = AsyncMock(side_effect=lambda **_: order.append("defer"))

    async def _snapshot(cfg):
        order.append("snapshot")

    async def _overflow(would_hold):
        order.append("overflow")
        return None

    monkeypatch.setattr(cog, "_snapshot_pending", _snapshot)
    monkeypatch.setattr(cog, "_calendar_round_overflow", _overflow)
    monkeypatch.setattr(
        "leaguebot.core.services.track_service.resolve_track_name",
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
    assert "snapshot" in order, f"the round was never written: {order}"
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


async def test_season_setup_begins_the_season_in_configuration():
    """Issue #220: a season is set up in Configuration, before any division exists."""
    from leaguebot.core.models.season import SeasonStage

    cog = _cog(None)
    interaction = _interaction()

    from tests.support.undecorate import undecorate

    await undecorate(SeasonCog.season_setup)(cog, interaction, game_edition=2026)

    kwargs = cog.bot.season_service.sync_pending_config.await_args.kwargs
    assert kwargs["initial_stage"] is SeasonStage.CONFIGURATION


@pytest.mark.parametrize(
    "standing, reply",
    [
        pytest.param(
            "pending",
            "\u274c A season setup is already in progress for this server. Use "
            "`/season placements-review` to approve, or `/season abort` to cancel it first.",
            id="setup-while-one-is-in-progress",
            marks=pytest.mark.xfail(strict=True, reason=SETUP_FROM_DB),
        ),
        pytest.param(
            "raced",
            "\u274c A season is currently active for this server. Complete it before starting a "
            "new one.",
            id="setup-while-a-season-is-being-raced",
        ),
    ],
)
async def test_every_season_setup_refusal_is_recorded(standing, reply):
    """The core specification's record of what changed: a refusal is one line naming the member,
    what was refused and why. The manager (id 42) runs /season setup while a setup is in
    progress, as the database holds it, or while a season is being raced: they get today's
    reply word for word and nothing else, no season is begun,
    and the log channel gets exactly one line, "⛔ `/season setup` refused for Manager (<@42>) — "
    and the reply's words."""
    from tests.support.undecorate import undecorate

    cog = _cog(_pending() if standing == "pending" else None)
    if standing == "raced":
        cog.bot.season_service.get_confirmed_season = AsyncMock(return_value=SimpleNamespace(id=3))
    interaction = _interaction()
    interaction.client = cog.bot
    interaction.command.qualified_name = "season setup"
    answered = {"done": False}

    async def _defer(*_args, **_kwargs):
        answered["done"] = True

    interaction.response.defer = AsyncMock(side_effect=_defer)
    interaction.response.is_done = MagicMock(side_effect=lambda: answered["done"])

    await undecorate(SeasonCog.season_setup)(cog, interaction, game_edition=2026)

    assert [call.args[0] for call in interaction.followup.send.await_args_list] == [reply]
    cog.bot.season_service.sync_pending_config.assert_not_awaited()
    logged = [str(call.args[0]) for call in cog.bot.output_router.post_log.await_args_list]
    assert logged == [f"\u26d4 `/season setup` refused for Manager (<@42>) \u2014 {reply[2:]}"]


# ---------------------------------------------------------------------------
# The setup is read from the database whenever a command asks for it (#283, S14a)
# ---------------------------------------------------------------------------


async def _bot_on(tmp_path) -> MagicMock:
    """`_bot()` with the real season service on a migrated database under *tmp_path*, which holds
    no season yet."""
    from leaguebot.core.db.database import run_migrations
    from leaguebot.core.services.season_service import SeasonService

    db_path = str(tmp_path / "league.db")
    await run_migrations(db_path)
    bot = _bot()
    bot.db_path = db_path
    bot.season_service = SeasonService(db_path)
    return bot


async def _setup_seasons(db_path: str) -> list[int]:
    from leaguebot.core.db.database import get_connection

    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT id FROM seasons WHERE status = 'SETUP'")
        return [row["id"] for row in await cursor.fetchall()]


@pytest.mark.xfail(strict=True, reason="#283: a failed first save leaves the setup held in memory")
async def test_a_failed_first_setup_save_leaves_no_setup_behind(tmp_path):
    """The manager runs `/season setup` for F1 25 on a league with no season, and the save fails
    (the database is locked). Nothing of the setup is left: the manager's next `/season setup`
    is not refused as a setup in progress, it starts the season afresh, and the database then
    holds that one season being set up."""
    from tests.support.undecorate import undecorate

    bot = await _bot_on(tmp_path)
    cog = SeasonCog(bot)
    bot.season_service.sync_pending_config = AsyncMock(side_effect=RuntimeError("database is locked"))
    with pytest.raises(RuntimeError):
        await undecorate(SeasonCog.season_setup)(cog, _interaction(), game_edition=25)
    assert await _setup_seasons(bot.db_path) == []
    del bot.season_service.sync_pending_config

    again = _interaction()
    await undecorate(SeasonCog.season_setup)(cog, again, game_edition=25)

    assert "Season setup started" in again.followup.send.await_args.args[0]
    assert len(await _setup_seasons(bot.db_path)) == 1


@pytest.mark.xfail(strict=True, reason="#283: a setup command reads the setup held in memory")
async def test_a_setup_begun_before_a_restart_is_found_by_the_next_setup_command(tmp_path):
    """The manager runs `/season setup` for F1 25 and the season is taken to placements. The bot
    then restarts, and nothing recovers the setup at start-up. The manager's `/division add` of
    Pro, tier 1, finds the season being set up in the database: it is not refused for want of a
    setup, and Pro is saved on that season."""
    from leaguebot.core.db.database import get_connection
    from tests.support.undecorate import undecorate

    bot = await _bot_on(tmp_path)
    await undecorate(SeasonCog.season_setup)(SeasonCog(bot), _interaction(), game_edition=25)
    [season_id] = await _setup_seasons(bot.db_path)
    async with get_connection(bot.db_path) as db:
        await db.execute("UPDATE seasons SET stage = 'PLACEMENTS' WHERE id = ?", (season_id,))
        await db.commit()
    role = MagicMock()
    role.id = 10
    role.mention = "<@&10>"

    restarted = SeasonCog(bot)
    interaction = _interaction()
    await undecorate(SeasonCog.division_add)(restarted, interaction, name="Pro", role=role, tier=1)

    assert "Division **Pro** (Tier 1) added" in interaction.followup.send.await_args.args[0]
    async with get_connection(bot.db_path) as db:
        cursor = await db.execute("SELECT name, tier FROM divisions WHERE season_id = ?",
                                  (season_id,))
        assert [tuple(row) for row in await cursor.fetchall()] == [("Pro", 1)]
