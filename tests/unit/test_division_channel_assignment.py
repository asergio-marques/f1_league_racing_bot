"""Assigning a division's output channels, and the rule that a channel does one job.

Issue #208. `season_cog.py` is the largest file in the bot and was at 44.1%. This file takes
`_set_division_channel` and the guard beneath it — the path every `/division …-channel` command
goes through.

**A channel serves one purpose across the whole server** (decided 2026-09-06). Two settings
sharing one channel interleave two kinds of posting, and several posting paths edit or delete
their last message by an id stored against the channel — so sharing is how one output comes to
delete another's. The guard runs **before** the write, so a refusal leaves the configuration
exactly as it stood.

**Re-running a command with the value it already holds is refused in its own words.** It is not
a collision with something else, and reporting it as one would send a manager looking for a
conflict that does not exist. `test_re_setting_the_same_channel_is_refused_as_unchanged` and
`test_a_channel_doing_another_job_is_refused_as_a_clash` sit either side of that distinction —
it is the whole reason `same_setting` is threaded through to the refusal text, and a reader
collapsing the two messages would lose it.

**The same-value case used to be written and only then reported as unchanged.** The guard
running first is what fixed that, and `test_a_refused_assignment_writes_nothing` holds it
against a later reader who moves the check after the upsert for tidiness.

**The reply follows whichever state the interaction is already in.** Some of these commands
defer and some do not, and a fresh response after a defer is a 404 — so the refusal has to ask
rather than assume. That is pinned in both directions, because it is invisible until a manager
meets it on the one command that defers.
"""
from __future__ import annotations

import json
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from cogs.season_cog import SeasonCog  # noqa: E402
from db.database import get_connection, run_migrations  # noqa: E402
from services.channel_registry_service import ChannelUse  # noqa: E402

SERVER_ID = 9708
SEASON_ID = 1
DIVISION_ID = 11
CHANNEL_ID = 770001
ACTOR_ID = 77


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _make_db(tmp_path) -> str:
    db_path = os.path.join(str(tmp_path), "division_channels.db")
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


def _division(name: str = "Division 1"):
    return SimpleNamespace(id=DIVISION_ID, name=name, tier=1)


def _make_cog(
    db_path: str,
    *,
    season=SimpleNamespace(id=SEASON_ID),
    divisions=None,
) -> SeasonCog:
    bot = MagicMock()
    bot.db_path = db_path
    bot.season_service = MagicMock()
    bot.season_service.get_setup_or_active_season = AsyncMock(return_value=season)
    bot.season_service.get_divisions = AsyncMock(
        return_value=divisions if divisions is not None else [_division()]
    )
    bot.season_service.set_division_forecast_channel = AsyncMock(return_value=None)
    bot.season_service.set_division_results_channel = AsyncMock(return_value=None)
    bot.season_service.set_division_standings_channel = AsyncMock(return_value=None)
    bot.output_router = MagicMock()
    bot.output_router.post_log = AsyncMock(return_value=None)

    cog = SeasonCog.__new__(SeasonCog)
    cog.bot = bot
    return cog


def _channel(channel_id: int = CHANNEL_ID, name: str = "forecasts"):
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = channel_id
    channel.name = name
    channel.mention = f"#{name}"
    return channel


def _interaction(*, done: bool = False):
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.user = MagicMock()
    interaction.user.id = ACTOR_ID
    interaction.user.display_name = "Manager"
    interaction.user.__str__ = lambda self: "Manager#0001"  # type: ignore[assignment]
    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.is_done = MagicMock(return_value=done)
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


async def _audit(db_path: str) -> list[dict]:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT change_type, old_value, new_value, division_id FROM audit_entries "
            " ORDER BY id",
        )
        return [dict(r) for r in await cursor.fetchall()]


def _free(monkeypatch):
    """No channel is in use anywhere."""
    import services.channel_registry_service as crs

    monkeypatch.setattr(crs, "find_channel_use", AsyncMock(return_value=None))


def _in_use(monkeypatch, use: ChannelUse):
    import services.channel_registry_service as crs

    monkeypatch.setattr(crs, "find_channel_use", AsyncMock(return_value=use))


# ---------------------------------------------------------------------------
# The refusal ladder
# ---------------------------------------------------------------------------


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

    await cog._set_division_channel(interaction, "Division 1", _channel(), "weather")

    cog.bot.season_service.get_setup_or_active_season.assert_awaited_once()
    assert "No season is live" in _replied(interaction)


async def test_a_server_with_no_season_is_refused(tmp_path, monkeypatch):
    """Channels hang off divisions, which hang off a season."""
    _free(monkeypatch)
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path, season=None)
    interaction = _interaction()

    await cog._set_division_channel(interaction, "Division 1", _channel(), "weather")

    assert "No season is live" in _replied(interaction)


async def test_an_unknown_division_is_refused_by_name(tmp_path, monkeypatch):
    _free(monkeypatch)
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    interaction = _interaction()

    await cog._set_division_channel(interaction, "Division 9", _channel(), "weather")

    assert "Division 9" in _replied(interaction)
    assert "not found" in _replied(interaction)


async def test_a_division_is_matched_regardless_of_case(tmp_path, monkeypatch):
    """A manager typing `division 1` means the same division."""
    _free(monkeypatch)
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    interaction = _interaction()

    await cog._set_division_channel(interaction, "dIvIsIoN 1", _channel(), "weather")

    cog.bot.season_service.set_division_forecast_channel.assert_awaited_once()


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

    await cog._set_division_channel(interaction, "Division 1", _channel(), "weather")

    cog.bot.season_service.set_division_forecast_channel.assert_not_awaited()
    assert _replied(interaction) != ""


async def test_re_setting_the_same_channel_is_refused_as_unchanged(tmp_path, monkeypatch):
    """Not a collision with something else. Telling a manager it clashes would send them
    looking for a conflict that does not exist."""
    _in_use(monkeypatch, ChannelUse("weather", "Division 1"))
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    interaction = _interaction()

    await cog._set_division_channel(interaction, "Division 1", _channel(), "weather")

    cog.bot.season_service.set_division_forecast_channel.assert_not_awaited()


async def test_the_two_refusals_do_not_read_alike(tmp_path, monkeypatch):
    """`same_setting` is threaded all the way to the refusal text for this reason alone;
    a reader collapsing the two messages would lose the distinction entirely."""
    db_path = await _make_db(tmp_path)

    _in_use(monkeypatch, ChannelUse("weather", "Division 1"))
    same = _interaction()
    await _make_cog(db_path)._set_division_channel(
        same, "Division 1", _channel(), "weather"
    )

    _in_use(monkeypatch, ChannelUse("results", "Division 1"))
    clash = _interaction()
    await _make_cog(db_path)._set_division_channel(
        clash, "Division 1", _channel(), "weather"
    )

    assert _replied(same) != _replied(clash)


async def test_a_refused_assignment_writes_nothing(tmp_path, monkeypatch):
    """The guard runs before the upsert. The same-value case used to be written and only
    then reported as unchanged — this holds the order against a later tidy-up."""
    _in_use(monkeypatch, ChannelUse("results", "Division 1"))
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)

    await cog._set_division_channel(_interaction(), "Division 1", _channel(), "weather")

    assert await _audit(db_path) == []


async def test_a_free_channel_is_accepted(tmp_path, monkeypatch):
    _free(monkeypatch)
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    interaction = _interaction()

    await cog._set_division_channel(interaction, "Division 1", _channel(), "weather")

    cog.bot.season_service.set_division_forecast_channel.assert_awaited_once()
    assert "set to" in _replied(interaction)


# ---------------------------------------------------------------------------
# Replying in whichever state the interaction is in
# ---------------------------------------------------------------------------


async def test_a_refusal_before_a_defer_answers_the_interaction(tmp_path, monkeypatch):
    _in_use(monkeypatch, ChannelUse("results", "Division 1"))
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    interaction = _interaction(done=False)

    await cog._refuse_channel_in_use(
        interaction, _channel(), "weather", division_name="Division 1"
    )

    interaction.response.send_message.assert_awaited_once()
    interaction.followup.send.assert_not_awaited()


async def test_a_refusal_after_a_defer_follows_up_instead(tmp_path, monkeypatch):
    """A fresh response after a defer is a 404, so the refusal asks rather than assumes.
    Invisible until a manager meets it on the one command that defers."""
    _in_use(monkeypatch, ChannelUse("results", "Division 1"))
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    interaction = _interaction(done=True)

    await cog._refuse_channel_in_use(
        interaction, _channel(), "weather", division_name="Division 1"
    )

    interaction.followup.send.assert_awaited_once()
    interaction.response.send_message.assert_not_awaited()


async def test_a_free_channel_reports_no_use(tmp_path, monkeypatch):
    _free(monkeypatch)
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    interaction = _interaction()

    refused = await cog._refuse_channel_in_use(
        interaction, _channel(), "weather", division_name="Division 1"
    )

    assert refused is False
    assert _replied(interaction) == ""


# ---------------------------------------------------------------------------
# Each channel type reaches its own setter
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "channel_type,setter,label",
    [
        ("weather", "set_division_forecast_channel", "Weather forecast"),
        ("results", "set_division_results_channel", "Results"),
        ("standings", "set_division_standings_channel", "Standings"),
    ],
)
async def test_each_channel_type_writes_its_own_column(
    tmp_path, monkeypatch, channel_type, setter, label
):
    """Three near-identical branches. One calling the wrong setter would point a
    division's forecasts at its standings channel, and only this would object."""
    _free(monkeypatch)
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    interaction = _interaction()

    await cog._set_division_channel(interaction, "Division 1", _channel(), channel_type)

    getattr(cog.bot.season_service, setter).assert_awaited_once()
    assert label in _replied(interaction)


@pytest.mark.parametrize("channel_type", ["weather", "results", "standings"])
async def test_each_assignment_is_audited_against_its_division(
    tmp_path, monkeypatch, channel_type
):
    _free(monkeypatch)
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)

    await cog._set_division_channel(
        _interaction(), "Division 1", _channel(), channel_type
    )

    entry = (await _audit(db_path))[0]
    assert entry["change_type"] == "DIVISION_CHANNEL_SET"
    assert entry["division_id"] == DIVISION_ID
    assert json.loads(entry["new_value"])["channel_type"] == channel_type
    assert json.loads(entry["new_value"])["channel_id"] == CHANNEL_ID


async def test_the_audit_records_the_channel_being_replaced(tmp_path, monkeypatch):
    """A manager re-pointing a division's forecasts needs the old channel recoverable from
    the log; without it there is no way back to what it was."""
    _free(monkeypatch)
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    cog.bot.season_service.set_division_forecast_channel = AsyncMock(return_value=660000)

    await cog._set_division_channel(_interaction(), "Division 1", _channel(), "weather")

    entry = (await _audit(db_path))[0]
    assert json.loads(entry["old_value"])["channel_id"] == 660000


async def test_a_successful_assignment_is_logged(tmp_path, monkeypatch):
    _free(monkeypatch)
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)

    await cog._set_division_channel(_interaction(), "Division 1", _channel(), "weather")

    logged = cog.bot.output_router.post_log.await_args.args[0]
    assert "weather-channel" in logged
    assert "Division 1" in logged
