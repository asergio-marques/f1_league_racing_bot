"""`/weather channel`: setting the channel a division's forecasts are posted to.

Weather's own command, under weather's own group. It sat under core's `/division` until #462
moved it, and only its name changed: it checks weather is on in its own words, then the live
season, then the division, then that the channel is free, and only then writes, records and
logs.

**A channel serves one purpose across the whole server** (decided 2026-09-06). Two settings
sharing one channel interleave two kinds of posting, and several posting paths edit or delete
their last message by an id stored against the channel, so sharing is how one output comes to
delete another's. The check runs **before** the write, so a refusal leaves the configuration
exactly as it stood.

**Re-running the command with the value it already holds is refused in its own words.** It is
not a collision with something else, and reporting it as one would send a manager looking for a
conflict that does not exist. `test_the_two_refusals_do_not_read_alike` holds the difference.
"""
from __future__ import annotations

import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.services.channel_registry_service import ChannelUse
from leaguebot.weather.cogs.weather_cog import WeatherCog
from tests.support.undecorate import undecorate

SERVER_ID = 9709
SEASON_ID = 1
DIVISION_ID = 11
CHANNEL_ID = 770001
ACTOR_ID = 77


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _make_db(tmp_path) -> str:
    db_path = os.path.join(str(tmp_path), "weather_channel.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 900, 100, 101)",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO seasons (id, season_number, start_date, status) "
            "VALUES (?, 1, '2026-01-01', 'SETUP')",
            (SEASON_ID,),
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
            "VALUES (?, ?, 'Division 1', 1, 555)",
            (DIVISION_ID, SEASON_ID),
        )
        await db.commit()
    return db_path


def _make_cog(
    db_path: str,
    *,
    season=SimpleNamespace(id=SEASON_ID),
    weather_enabled: bool = True,
) -> WeatherCog:
    bot = MagicMock()
    bot.db_path = db_path
    bot.module_service = MagicMock()
    bot.module_service.is_weather_enabled = AsyncMock(return_value=weather_enabled)
    bot.season_service = MagicMock()
    bot.season_service.get_setup_or_active_season = AsyncMock(return_value=season)
    bot.season_service.get_divisions = AsyncMock(
        return_value=[SimpleNamespace(id=DIVISION_ID, name="Division 1", tier=1)]
    )
    bot.season_service.set_division_forecast_channel = AsyncMock(return_value=None)
    bot.output_router = MagicMock()
    bot.output_router.post_log = AsyncMock(return_value=None)

    cog = WeatherCog.__new__(WeatherCog)
    cog.bot = bot
    return cog


def _channel(channel_id: int = CHANNEL_ID, name: str = "forecasts"):
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = channel_id
    channel.name = name
    channel.mention = f"#{name}"
    return channel


def _interaction():
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.user = MagicMock()
    interaction.user.id = ACTOR_ID
    interaction.user.display_name = "Manager"
    interaction.user.__str__ = lambda self: "Manager#0001"  # type: ignore[assignment]
    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.is_done = MagicMock(return_value=False)
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    return interaction


async def _run(cog, interaction, name: str = "Division 1", channel=None) -> None:
    await undecorate(WeatherCog.channel)(cog, interaction, name, channel or _channel())


def _replied(interaction) -> str:
    return "\n".join(
        str(call.args[0])
        for call in interaction.response.send_message.await_args_list
        + interaction.followup.send.await_args_list
        if call.args
    )


async def _audit(db_path: str) -> list[dict]:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT change_type, old_value, new_value, division_id, actor_id FROM audit_entries "
            " ORDER BY id",
        )
        return [dict(r) for r in await cursor.fetchall()]


def _free(monkeypatch):
    """No channel is in use anywhere."""
    import leaguebot.core.services.channel_registry_service as crs

    monkeypatch.setattr(crs, "find_channel_use", AsyncMock(return_value=None))


def _in_use(monkeypatch, use: ChannelUse):
    import leaguebot.core.services.channel_registry_service as crs

    monkeypatch.setattr(crs, "find_channel_use", AsyncMock(return_value=use))


# ---------------------------------------------------------------------------
# The refusal ladder
# ---------------------------------------------------------------------------


async def test_the_weather_channel_is_refused_while_weather_is_off(tmp_path, monkeypatch):
    """Setting a forecast channel for a module that is not running configures something no
    code reads. The words are the command's own, not the weather cog's gate, and nothing is
    read, written or logged."""
    _free(monkeypatch)
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path, weather_enabled=False)
    interaction = _interaction()

    await _run(cog, interaction)

    assert _replied(interaction) == "❌ The Weather module is not enabled."
    cog.bot.season_service.get_setup_or_active_season.assert_not_awaited()
    cog.bot.season_service.set_division_forecast_channel.assert_not_awaited()
    cog.bot.output_router.post_log.assert_not_awaited()
    assert await _audit(db_path) == []


async def test_the_live_season_is_the_one_whose_channels_are_set(tmp_path, monkeypatch):
    """A division's channels belong to the season being built or raced; an archived season's
    no longer matter, and are never reached (issue #220)."""
    _free(monkeypatch)
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path, season=None)
    cog.bot.season_service.get_season_for_server = AsyncMock(
        side_effect=AssertionError("the latest season, archived or not, is not the target")
    )
    interaction = _interaction()

    await _run(cog, interaction)

    cog.bot.season_service.get_setup_or_active_season.assert_awaited_once()
    assert "No season is live" in _replied(interaction)
    assert await _audit(db_path) == []


async def test_an_unknown_division_is_refused_by_name(tmp_path, monkeypatch):
    _free(monkeypatch)
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    interaction = _interaction()

    await _run(cog, interaction, name="Division 9")

    assert _replied(interaction) == "❌ Division **Division 9** not found in the current season."
    cog.bot.season_service.set_division_forecast_channel.assert_not_awaited()


async def test_a_division_is_matched_regardless_of_case(tmp_path, monkeypatch):
    """A manager typing `division 1` means the same division."""
    _free(monkeypatch)
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)

    await _run(cog, _interaction(), name="dIvIsIoN 1")

    cog.bot.season_service.set_division_forecast_channel.assert_awaited_once_with(
        DIVISION_ID, CHANNEL_ID
    )


# ---------------------------------------------------------------------------
# A channel does one job
# ---------------------------------------------------------------------------


async def test_a_channel_doing_another_job_is_refused_as_a_clash(tmp_path, monkeypatch):
    """Two settings sharing one channel interleave two kinds of posting, and the posting
    paths that edit or delete their last message would delete each other's."""
    _in_use(monkeypatch, ChannelUse("results", "Division 1"))
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    interaction = _interaction()

    await _run(cog, interaction)

    cog.bot.season_service.set_division_forecast_channel.assert_not_awaited()
    assert "#forecasts is already the results channel for **Division 1**" in _replied(
        interaction
    )


async def test_re_setting_the_same_channel_is_refused_as_unchanged(tmp_path, monkeypatch):
    """Not a collision with something else. Telling a manager it clashes would send them
    looking for a conflict that does not exist."""
    _in_use(monkeypatch, ChannelUse("weather", "Division 1"))
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    interaction = _interaction()

    await _run(cog, interaction)

    cog.bot.season_service.set_division_forecast_channel.assert_not_awaited()
    assert "Nothing was changed." in _replied(interaction)


async def test_the_two_refusals_do_not_read_alike(tmp_path, monkeypatch):
    """`same_setting` is threaded all the way to the refusal text for this reason alone;
    a reader collapsing the two messages would lose the distinction entirely."""
    db_path = await _make_db(tmp_path)

    _in_use(monkeypatch, ChannelUse("weather", "Division 1"))
    same = _interaction()
    await _run(_make_cog(db_path), same)

    _in_use(monkeypatch, ChannelUse("results", "Division 1"))
    clash = _interaction()
    await _run(_make_cog(db_path), clash)

    assert _replied(same) != _replied(clash)


async def test_a_refused_assignment_writes_nothing(tmp_path, monkeypatch):
    """The check runs before the write. The same-value case used to be written and only
    then reported as unchanged — this holds the order against a later tidy-up."""
    _in_use(monkeypatch, ChannelUse("weather", "Division 1"))
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)

    await _run(cog, _interaction())

    assert await _audit(db_path) == []
    cog.bot.output_router.post_log.assert_not_awaited()


# ---------------------------------------------------------------------------
# The assignment
# ---------------------------------------------------------------------------


async def test_a_free_channel_is_set(tmp_path, monkeypatch):
    _free(monkeypatch)
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    interaction = _interaction()

    await _run(cog, interaction)

    cog.bot.season_service.set_division_forecast_channel.assert_awaited_once_with(
        DIVISION_ID, CHANNEL_ID
    )
    assert _replied(interaction) == (
        "✅ Weather forecast channel for **Division 1** set to #forecasts."
    )


async def test_moving_a_channel_says_it_was_updated(tmp_path, monkeypatch):
    """A manager who meant a fresh assignment and is told it was *updated* has moved an
    existing one — worth noticing before the next forecast lands somewhere else (#212)."""
    _free(monkeypatch)
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    cog.bot.season_service.set_division_forecast_channel.return_value = 111
    interaction = _interaction()

    await _run(cog, interaction)

    assert _replied(interaction) == (
        "✅ Weather forecast channel for **Division 1** updated to #forecasts."
    )


async def test_the_assignment_is_audited_against_its_division(tmp_path, monkeypatch):
    """The record the command wrote while core held it: `DIVISION_CHANNEL_SET`, with its
    `channel_type`, so the audit of a channel's history reads the same across the move."""
    _free(monkeypatch)
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)

    await _run(cog, _interaction())

    entry = (await _audit(db_path))[0]
    assert entry["change_type"] == "DIVISION_CHANNEL_SET"
    assert entry["division_id"] == DIVISION_ID
    assert entry["actor_id"] == ACTOR_ID
    assert json.loads(entry["old_value"]) == {"channel_type": "weather", "channel_id": None}
    assert json.loads(entry["new_value"]) == {"channel_type": "weather", "channel_id": CHANNEL_ID}


async def test_the_audit_records_the_channel_being_replaced(tmp_path, monkeypatch):
    """A manager re-pointing a division's forecasts needs the old channel recoverable from
    the log; without it there is no way back to what it was."""
    _free(monkeypatch)
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    cog.bot.season_service.set_division_forecast_channel = AsyncMock(return_value=660000)

    await _run(cog, _interaction())

    entry = (await _audit(db_path))[0]
    assert json.loads(entry["old_value"])["channel_id"] == 660000


async def test_the_weather_channel_is_logged_as_weather_channel(tmp_path, monkeypatch):
    """A log line names the member, the command and its outcome, and the command is the one a
    league now types: `/weather channel`."""
    _free(monkeypatch)
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)

    await _run(cog, _interaction())

    logged = cog.bot.output_router.post_log.await_args.args[0]
    assert logged.splitlines() == [
        f"Manager (<@{ACTOR_ID}>) | /weather channel | Success",
        "  division: Division 1",
        "  channel: #forecasts",
    ]
