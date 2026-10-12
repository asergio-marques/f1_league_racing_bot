"""A lineup asked of the change queue (#439, slice 6): a command that changes the drivers makes
its own change, then asks the queue, as the bot's and repeatably, to post each lineup it touched
(`lineup.post`); its reply is unchanged and the post follows on the queue.

The league is `ongoing_league`'s: season 3 being raced, Pro (21) with its lineup channel 600 and
Am (22) with 700, images off. Where a command is run, its cog stands on a bot whose services are
stubbed to say which divisions' lineups the change touched, and whose queue and database are the
league's, so the asks are real and the queue run posts the lineups. Everything of the change is
imported inside a test, so this file collects while it is unbuilt.
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock

import discord
import pytest

from leaguebot.core.models.change import ChangeOrigin
from tests.support.change_queue import (
    change_rows,
    http_error,
    queued_log_lines,
    register,
    retry_job,
    run_queue,
    stopped_job,
)
from tests.support.season_league import AM, DIVISIONS, PRO, ongoing_league

UNBUILT = "#439: a lineup is not yet asked of the change queue"

KIND = "lineup.post"
LINEUP = {PRO: DIVISIONS[PRO][3].lineup, AM: DIVISIONS[AM][3].lineup}


async def _lineup_asks(league: Any) -> list[tuple[int, str]]:
    """Each `lineup.post` asked, in order: its division and its origin."""
    return [(json.loads(row["payload"])["division_id"], row["origin"])
            for row in await change_rows(league.db_path) if row["kind"] == KIND]


async def _said(league: Any) -> str:
    """Every log line, sent or still waiting on the old retry queue."""
    return "\n".join([league.log(), *await queued_log_lines(league.db_path)])


async def _ask(league: Any, *divisions: int) -> None:
    from leaguebot.core.services.lineup_posting import ask_lineup_posts

    await ask_lineup_posts(league.bot.change_queue, league.db_path, list(divisions))


def _refusing(league: Any, channel_id: int) -> Any:
    """Make *channel_id* refuse every send; gives its own send, to put back."""
    channel = league.channel(channel_id)
    own = channel.send
    channel.send = AsyncMock(side_effect=http_error(discord.Forbidden, status=403,
                                                    text="Missing Access"))
    return own


async def _stop_the_queue(league: Any) -> None:
    """A change of the bot's, asked before anything else, whose job Discord refuses: the queue
    stops at it, and whatever is asked after it waits."""
    from leaguebot.core.models.change import PlannedStep, StepFailedOnDiscord, StepKind, Verdict
    from leaguebot.core.services.change_queue import ChangeType, Step

    async def _refused(_ctx: Any) -> Any:
        raise StepFailedOnDiscord("Missing Access")

    async def _go(_ctx: Any) -> Any:
        return Verdict.go()

    register(league.bot, ChangeType(
        kind="blocker", opening=(PlannedStep("block", {}),),
        steps={"block": Step("block", StepKind.ACT, _refused)}, check=_go,
        key=lambda _payload: "blocker", doing=lambda _payload: "Blocking",
        outcome=lambda _ctx: "",
    ))
    await league.bot.change_queue.ask("blocker", {}, what="the blocker", origin=ChangeOrigin.BOT)
    await run_queue(league.bot)
    assert (await stopped_job(league.db_path))["name"] == "block"


# ── The commands ask ───────────────────────────────────────────────────────────────


async def _assign(league: Any, lineups: list[int]) -> Any:
    from tests.core.test_driver_cog import _assign, _interaction, _make_cog, _season

    cog = _make_cog(season=_season())
    cog.bot.placement_service.assign_driver.return_value = {
        "team_name": "Alpha", "division_name": "Pro", "lineups": lineups,
    }
    return cog, _interaction(), _assign


async def _unassign(league: Any, lineups: list[int]) -> Any:
    from tests.core.test_driver_cog import _interaction, _make_cog, _season, _unassign

    cog = _make_cog(season=_season())
    cog.bot.placement_service.unassign_driver.return_value = {
        "team_name": "Alpha", "division_name": "Pro", "lineups": lineups,
    }
    return cog, _interaction(), _unassign


async def _sack(league: Any, lineups: list[int]) -> Any:
    from tests.core.test_driver_cog import _interaction, _make_cog, _sack, _season

    cog = _make_cog(season=_season())
    cog.bot.placement_service.sack_driver.return_value = lineups
    return cog, _interaction(), _sack


async def _move(league: Any, lineups: list[int]) -> Any:
    from tests.core.test_driver_ongoing_commands import _cog, _interaction, _move

    cog = _cog()
    cog.bot.placement_service.move_driver.return_value = {
        "from_division": "Am", "to_division": "Pro", "from_team": "Alpha", "to_team": "Reserve",
        "lineups": lineups,
    }
    return cog, _interaction(), lambda c, i: _move(c, i, from_division="Pro",
                                                   to_division="Academy")


async def _release(league: Any, lineups: list[int]) -> Any:
    from tests.core.test_driver_ongoing_commands import _cog, _interaction, _release

    cog = _cog()
    cog.bot.placement_service.release_driver.return_value = {
        "division_name": "Pro", "lineups": lineups,
    }
    return cog, _interaction(), _release


@pytest.mark.xfail(strict=True, reason=UNBUILT)
@pytest.mark.parametrize("command, lineups", [
    pytest.param(_assign, [PRO], id="assign"),
    pytest.param(_unassign, [PRO], id="unassign"),
    pytest.param(_move, [AM, PRO], id="move to another division"),
    pytest.param(_release, [PRO], id="release"),
    pytest.param(_sack, [PRO], id="sack"),
])
async def test_a_driver_command_asks_the_queue_for_each_lineup_it_touched(tmp_path, command,
                                                                         lineups):
    league = await ongoing_league(tmp_path)
    cog, interaction, run = await command(league, lineups)
    cog.bot.db_path = league.db_path
    cog.bot.change_queue = league.bot.change_queue
    before = {division: len(league.texts(LINEUP[division])) for division in lineups}

    await run(cog, interaction)

    assert await _lineup_asks(league) == [(division, "BOT") for division in lineups]
    assert interaction.followup.send.await_count + interaction.response.send_message.await_count
    cog.bot.output_router.post_log.assert_awaited_once()
    for division in lineups:
        assert len(league.texts(LINEUP[division])) == before[division]

    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    for division in lineups:
        assert len(league.texts(LINEUP[division])) == before[division] + 1


# ── What the queue does with the ask ───────────────────────────────────────────────


@pytest.mark.xfail(strict=True, reason=UNBUILT)
async def test_a_lineup_a_command_asked_for_that_discord_refuses_stops_the_queue(tmp_path):
    league = await ongoing_league(tmp_path)
    own = _refusing(league, LINEUP[PRO])
    before = len(league.texts(LINEUP[PRO]))

    await _ask(league, PRO)
    await run_queue(league.bot)

    assert (await stopped_job(league.db_path))["name"] == "post_lineup"
    assert "The queue is stopped at job #" in await _said(league)

    league.channel(LINEUP[PRO]).send = own
    await retry_job(league.bot)

    assert await stopped_job(league.db_path) is None
    assert len(league.texts(LINEUP[PRO])) == before + 1


@pytest.mark.xfail(strict=True, reason=UNBUILT)
async def test_two_commands_in_a_row_each_ask_for_the_lineup(tmp_path):
    league = await ongoing_league(tmp_path)
    await _stop_the_queue(league)

    await _ask(league, PRO)
    await _ask(league, PRO)

    rows = [row for row in await change_rows(league.db_path) if row["kind"] == KIND]
    assert [row["state"] for row in rows] == ["QUEUED", "QUEUED"]


@pytest.mark.xfail(strict=True, reason=UNBUILT)
async def test_a_lineup_for_a_division_with_no_lineup_channel_stops_the_queue_naming_the_command(
    tmp_path,
):
    league = await ongoing_league(tmp_path)
    await league.write("UPDATE divisions SET lineup_channel_id = NULL WHERE id = ?", PRO)
    before = len(league.texts(LINEUP[PRO]))

    await _ask(league, PRO)
    await run_queue(league.bot)

    assert (await stopped_job(league.db_path))["name"] == "post_lineup"
    assert ("**Pro** has no lineup channel set: set one with `/division lineup-channel`, "
            "then press Retry.") in await _said(league)

    await league.write("UPDATE divisions SET lineup_channel_id = ? WHERE id = ?",
                       LINEUP[PRO], PRO)
    await retry_job(league.bot)

    assert await stopped_job(league.db_path) is None
    assert len(league.texts(LINEUP[PRO])) == before + 1

    # A division no longer of the season by the time its lineup is asked for: dropped.
    await league.bot.change_queue.ask(KIND, {"division_id": 999, "division_name": "Gone"},
                                      what="posting the lineup of **Gone**",
                                      origin=ChangeOrigin.BOT)
    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    gone = [row["state"] for row in await change_rows(league.db_path)
            if row["kind"] == KIND and json.loads(row["payload"])["division_id"] == 999]
    assert gone in ([], ["DROPPED"])
