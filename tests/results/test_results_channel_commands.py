"""`/results channel …`: setting the channels a division's results are posted to.

Results' own commands, under results' own group. They sat under core's `/division` until #462
moved them, and only their names changed. `/results channel results` and `/results channel
standings` share one body, `ResultsCog._set_division_channel`, which this file takes with the
check beneath it (issue #208 first covered it in core's cog).

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
against a later reader who moves the check after the upsert for tidiness. The refusal's own
rule, and the state of the interaction it is sent in, are pinned in `test_channel_registry.py`.
"""
from __future__ import annotations

import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.services.channel_registry_service import ChannelUse
from leaguebot.results.cogs.results_cog import ResultsCog
from tests.support.undecorate import undecorate

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
) -> ResultsCog:
    bot = MagicMock()
    bot.db_path = db_path
    bot.module_service = MagicMock()
    bot.module_service.is_results_enabled = AsyncMock(return_value=True)
    bot.season_service = MagicMock()
    bot.season_service.get_setup_or_active_season = AsyncMock(return_value=season)
    bot.season_service.get_divisions = AsyncMock(
        return_value=divisions if divisions is not None else [_division()]
    )
    bot.season_service.set_division_results_channel = AsyncMock(return_value=None)
    bot.season_service.set_division_standings_channel = AsyncMock(return_value=None)
    bot.output_router = MagicMock()
    bot.output_router.post_log = AsyncMock(return_value=None)

    cog = ResultsCog.__new__(ResultsCog)
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
    import leaguebot.core.services.channel_registry_service as crs

    monkeypatch.setattr(crs, "find_channel_use", AsyncMock(return_value=None))


def _in_use(monkeypatch, use: ChannelUse):
    import leaguebot.core.services.channel_registry_service as crs

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

    await cog._set_division_channel(interaction, "Division 1", _channel(), "results")

    cog.bot.season_service.get_setup_or_active_season.assert_awaited_once()
    assert "No season is live" in _replied(interaction)


async def test_a_server_with_no_season_is_refused(tmp_path, monkeypatch):
    """Channels hang off divisions, which hang off a season."""
    _free(monkeypatch)
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path, season=None)
    interaction = _interaction()

    await cog._set_division_channel(interaction, "Division 1", _channel(), "results")

    assert "No season is live" in _replied(interaction)


async def test_an_unknown_division_is_refused_by_name(tmp_path, monkeypatch):
    _free(monkeypatch)
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    interaction = _interaction()

    await cog._set_division_channel(interaction, "Division 9", _channel(), "results")

    assert "Division 9" in _replied(interaction)
    assert "not found" in _replied(interaction)


async def test_a_division_is_matched_regardless_of_case(tmp_path, monkeypatch):
    """A manager typing `division 1` means the same division."""
    _free(monkeypatch)
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    interaction = _interaction()

    await cog._set_division_channel(interaction, "dIvIsIoN 1", _channel(), "results")

    cog.bot.season_service.set_division_results_channel.assert_awaited_once()


# ---------------------------------------------------------------------------
# A channel does one job
# ---------------------------------------------------------------------------


async def test_a_channel_doing_another_job_is_refused_as_a_clash(tmp_path, monkeypatch):
    """Two settings sharing one channel interleave two kinds of posting, and the posting
    paths that edit or delete their last message would delete each other's."""
    _in_use(monkeypatch, ChannelUse("standings", "Division 1"))
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    interaction = _interaction()

    await cog._set_division_channel(interaction, "Division 1", _channel(), "results")

    cog.bot.season_service.set_division_results_channel.assert_not_awaited()
    assert _replied(interaction) != ""


async def test_re_setting_the_same_channel_is_refused_as_unchanged(tmp_path, monkeypatch):
    """Not a collision with something else. Telling a manager it clashes would send them
    looking for a conflict that does not exist."""
    _in_use(monkeypatch, ChannelUse("results", "Division 1"))
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    interaction = _interaction()

    await cog._set_division_channel(interaction, "Division 1", _channel(), "results")

    cog.bot.season_service.set_division_results_channel.assert_not_awaited()


async def test_the_two_refusals_do_not_read_alike(tmp_path, monkeypatch):
    """`same_setting` is threaded all the way to the refusal text for this reason alone;
    a reader collapsing the two messages would lose the distinction entirely."""
    db_path = await _make_db(tmp_path)

    _in_use(monkeypatch, ChannelUse("results", "Division 1"))
    same = _interaction()
    await _make_cog(db_path)._set_division_channel(
        same, "Division 1", _channel(), "results"
    )

    _in_use(monkeypatch, ChannelUse("standings", "Division 1"))
    clash = _interaction()
    await _make_cog(db_path)._set_division_channel(
        clash, "Division 1", _channel(), "results"
    )

    assert _replied(same) != _replied(clash)


async def test_a_refused_assignment_writes_nothing(tmp_path, monkeypatch):
    """The guard runs before the upsert. The same-value case used to be written and only
    then reported as unchanged — this holds the order against a later tidy-up."""
    _in_use(monkeypatch, ChannelUse("standings", "Division 1"))
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)

    await cog._set_division_channel(_interaction(), "Division 1", _channel(), "results")

    assert await _audit(db_path) == []


async def test_a_free_channel_is_accepted(tmp_path, monkeypatch):
    _free(monkeypatch)
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    interaction = _interaction()

    await cog._set_division_channel(interaction, "Division 1", _channel(), "results")

    cog.bot.season_service.set_division_results_channel.assert_awaited_once()
    assert "set to" in _replied(interaction)


@pytest.mark.parametrize(
    "channel_type, setter",
    [
        ("results", "set_division_results_channel"),
        ("standings", "set_division_standings_channel"),
    ],
)
async def test_moving_a_channel_says_it_was_updated(tmp_path, monkeypatch, channel_type, setter):
    """A manager who meant a fresh assignment and is told it was *updated* has moved an
    existing one — worth noticing before the next post lands somewhere else. These two
    always said "set to" (issue #212)."""
    _free(monkeypatch)
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    getattr(cog.bot.season_service, setter).return_value = 111
    interaction = _interaction()

    await cog._set_division_channel(interaction, "Division 1", _channel(), channel_type)

    assert "updated to" in _replied(interaction)


# ---------------------------------------------------------------------------
# Each channel type reaches its own setter
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "channel_type,setter,label",
    [
        ("results", "set_division_results_channel", "Results"),
        ("standings", "set_division_standings_channel", "Standings"),
    ],
)
async def test_each_channel_type_writes_its_own_column(
    tmp_path, monkeypatch, channel_type, setter, label
):
    """Two near-identical branches. One calling the wrong setter would point a
    division's results at its standings channel, and only this would object."""
    _free(monkeypatch)
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    interaction = _interaction()

    await cog._set_division_channel(interaction, "Division 1", _channel(), channel_type)

    getattr(cog.bot.season_service, setter).assert_awaited_once()
    assert label in _replied(interaction)


@pytest.mark.parametrize("channel_type", ["results", "standings"])
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
    """A manager re-pointing a division's results needs the old channel recoverable from
    the log; without it there is no way back to what it was."""
    _free(monkeypatch)
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    cog.bot.season_service.set_division_results_channel = AsyncMock(return_value=660000)

    await cog._set_division_channel(_interaction(), "Division 1", _channel(), "results")

    entry = (await _audit(db_path))[0]
    assert json.loads(entry["old_value"])["channel_id"] == 660000


async def test_a_successful_assignment_is_logged(tmp_path, monkeypatch):
    _free(monkeypatch)
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)

    await cog._set_division_channel(_interaction(), "Division 1", _channel(), "results")

    logged = cog.bot.output_router.post_log.await_args.args[0]
    assert "Division 1" in logged
    assert "#forecasts" in logged


# ---------------------------------------------------------------------------
# Each command checks its own module first, in its own words
# ---------------------------------------------------------------------------


async def _run_command(cog, command: str, interaction) -> None:
    """Run the body of one of the two commands `_set_division_channel` serves."""
    await undecorate(getattr(ResultsCog, command))(
        cog, interaction, "Division 1", _channel()
    )


def _assert_nothing_done(cog, interaction, refused: str) -> None:
    """Refused before the season is read: nothing written and nothing logged."""
    assert _replied(interaction) == refused
    cog.bot.season_service.get_setup_or_active_season.assert_not_awaited()
    cog.bot.season_service.set_division_results_channel.assert_not_awaited()
    cog.bot.season_service.set_division_standings_channel.assert_not_awaited()
    cog.bot.output_router.post_log.assert_not_awaited()


@pytest.mark.parametrize(
    "command", ["channel_results", "channel_standings"]
)
async def test_the_results_channels_are_refused_while_results_is_off(
    tmp_path, monkeypatch, command
):
    """The words are the commands' own, not the results cog's gate."""
    _free(monkeypatch)
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    cog.bot.module_service.is_results_enabled = AsyncMock(return_value=False)
    interaction = _interaction()

    await _run_command(cog, command, interaction)

    _assert_nothing_done(
        cog, interaction, "❌ The Results & Standings module is not enabled."
    )
    assert await _audit(db_path) == []


# ---------------------------------------------------------------------------
# The log line names the command by its module's group (#462)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "channel_type, command",
    [("results", "/results channel results"), ("standings", "/results channel standings")],
)
async def test_the_results_channels_are_logged_under_results_channel(
    tmp_path, monkeypatch, channel_type, command
):
    _free(monkeypatch)
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)

    await cog._set_division_channel(_interaction(), "Division 1", _channel(), channel_type)

    logged = cog.bot.output_router.post_log.await_args.args[0]
    assert logged.splitlines() == [
        f"Manager (<@{ACTOR_ID}>) | {command} | Success",
        "  division: Division 1",
        "  channel: #forecasts",
    ]
