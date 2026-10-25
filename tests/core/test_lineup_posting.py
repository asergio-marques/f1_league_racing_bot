"""A lineup asked of the change queue (#439, slice 6): a command that changes the drivers makes
its own change, then asks the queue, as the bot's and repeatably, to post each lineup it touched
(`lineup.post`); its reply is unchanged and the post follows on the queue.

The league is `ongoing_league`'s: season 3 being raced, Pro (21) with its lineup channel 600 and Am
(22) with 700, images off. Where a command is run, its cog stands on a bot whose services are
stubbed to say which divisions' lineups the change touched, and whose queue and database are the
league's, so the asks are real and the queue run posts the lineups.
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
    discard_job,
    http_error,
    queued_log_lines,
    register,
    retry_job,
    run_queue,
    step_rows,
    stopped_job,
)
from tests.support.season_league import AM, DIVISIONS, PRO, ongoing_league


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


async def test_two_commands_in_a_row_each_ask_for_the_lineup(tmp_path):
    league = await ongoing_league(tmp_path)
    await _stop_the_queue(league)

    await _ask(league, PRO)
    await _ask(league, PRO)

    rows = [row for row in await change_rows(league.db_path) if row["kind"] == KIND]
    assert [row["state"] for row in rows] == ["QUEUED", "QUEUED"]


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


# ── The post and the deletion of the lineup it replaces, as two jobs ─────────────────

#: Pro's lineup standing in its lineup channel before the ask.
OLD_LINEUP = 6100
#: The link Discord gives Pro's old lineup, which a discarded deletion names.
OLD_LINEUP_LINK = f"https://discord.test/{LINEUP[PRO]}/{OLD_LINEUP}"


async def _old_lineup_standing(league: Any) -> None:
    """Pro's lineup stands as message 6100, recorded against the division."""
    await league.write("UPDATE divisions SET lineup_message_id = ? WHERE id = ?", OLD_LINEUP, PRO)
    league.channel(LINEUP[PRO]).seed(OLD_LINEUP, "Pro's lineup")


async def _lineup_jobs(league: Any) -> list[dict[str, Any]]:
    """The jobs of the one `lineup.post` asked, in order, each with its payload read back."""
    [change] = [row for row in await change_rows(league.db_path) if row["kind"] == KIND]
    jobs = await step_rows(league.db_path, change["id"])
    for job in jobs:
        job["payload"] = json.loads(job["payload"] or "{}")
    return jobs


async def _run_lineup_through(league: Any, name: str) -> None:
    """Run the queue a job at a time until the lineup's job called *name* is done."""
    for _ in range(50):
        if any(job["name"] == name and job["done_at"] for job in await _lineup_jobs(league)):
            return
        await run_queue(league.bot, steps=1)
    raise AssertionError(f"the job {name} never finished: {await _lineup_jobs(league)}")


def _lineup_events(league: Any) -> list[tuple[str, int]]:
    """What Pro's lineup channel saw, in order: each send and delete, with its message."""
    return [(kind, mid) for kind, cid, mid in league.events if cid == LINEUP[PRO]]


async def _lineup_id(league: Any) -> Any:
    [row] = await league.rows("SELECT lineup_message_id FROM divisions WHERE id = ?", PRO)
    return row["lineup_message_id"]


async def _lineup_audits(league: Any) -> list[dict[str, Any]]:
    return await league.rows(
        "SELECT actor_name, new_value FROM audit_entries "
        "WHERE change_type = 'SIGNUP_LINEUP_POSTED' AND division_id = ?", PRO,
    )


def _pictures_on(monkeypatch: Any, tmp_path: Any) -> None:
    """The lineup is drawn as a picture: the image module's lineup aspect on, and its render
    giving a PNG."""
    from types import SimpleNamespace

    png = tmp_path / "lineup.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n")
    monkeypatch.setattr("leaguebot.image.services.image_lineup_post.lineup_enabled",
                        AsyncMock(return_value=True))
    monkeypatch.setattr(
        "leaguebot.image.services.image_lineup_post.render_png",
        AsyncMock(return_value=SimpleNamespace(rejects=False, posts_image=True,
                                               png_paths=[png], notices=[], problem=None)),
    )


async def test_the_text_lineup_is_posted_before_the_old_one_is_deleted(tmp_path):
    league = await ongoing_league(tmp_path)
    await _old_lineup_standing(league)

    await _ask(league, PRO)
    await _run_lineup_through(league, "post_lineup")

    [(sent, new)] = _lineup_events(league)
    assert sent == "send"
    assert OLD_LINEUP in league.channel(LINEUP[PRO]).messages

    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    assert _lineup_events(league) == [("send", new), ("delete", OLD_LINEUP)]
    names = [job["name"] for job in await _lineup_jobs(league)]
    assert names == ["post_lineup", "delete_replaced"]
    assert await _lineup_id(league) == new


async def test_a_refused_text_lineup_leaves_the_old_lineup_standing(tmp_path):
    league = await ongoing_league(tmp_path)
    await _old_lineup_standing(league)
    league.channel(LINEUP[PRO]).send_fails = http_error(
        discord.Forbidden, status=403, text="Missing Access"
    )

    await _ask(league, PRO)
    await run_queue(league.bot)

    assert (await stopped_job(league.db_path))["name"] == "post_lineup"
    assert OLD_LINEUP in league.channel(LINEUP[PRO]).messages
    assert ("delete", OLD_LINEUP) not in _lineup_events(league)
    assert await _lineup_id(league) == OLD_LINEUP


async def test_a_lineup_picture_is_posted_before_the_old_lineup_is_deleted_by_a_job(
    tmp_path, monkeypatch,
):
    league = await ongoing_league(tmp_path)
    await _old_lineup_standing(league)
    _pictures_on(monkeypatch, tmp_path)

    await _ask(league, PRO)
    await _run_lineup_through(league, "post_lineup")

    [(sent, new)] = _lineup_events(league)
    assert sent == "send"
    assert league.channel(LINEUP[PRO]).files[-1] is not None, "the lineup was not a picture"
    assert OLD_LINEUP in league.channel(LINEUP[PRO]).messages

    await run_queue(league.bot)

    assert _lineup_events(league) == [("send", new), ("delete", OLD_LINEUP)]
    assert [job["name"] for job in await _lineup_jobs(league)] == [
        "post_lineup", "delete_replaced",
    ]


@pytest.mark.parametrize("form", ["text", "image"])
async def test_the_lineup_s_id_and_audit_are_saved_with_its_post(tmp_path, monkeypatch, form):
    """Once the post job is done, and before the deletion after it runs, the division records
    the new lineup and one `SIGNUP_LINEUP_POSTED` audit stands, written by the system, saying
    "image" where the lineup was a picture."""
    league = await ongoing_league(tmp_path)
    await _old_lineup_standing(league)
    if form == "image":
        _pictures_on(monkeypatch, tmp_path)

    await _ask(league, PRO)
    await _run_lineup_through(league, "post_lineup")

    [(_sent, new)] = _lineup_events(league)
    assert await _lineup_id(league) == new
    assert OLD_LINEUP in league.channel(LINEUP[PRO]).messages, "the old lineup went too soon"
    [audit] = await _lineup_audits(league)
    assert audit["actor_name"] == "system"
    value = json.loads(audit["new_value"])
    assert value["division"] == "Pro"
    assert value.get("form") == ("image" if form == "image" else None)


async def test_an_old_lineup_discord_will_not_delete_stops_the_queue_and_once_discarded_is_named(
    tmp_path,
):
    """The new lineup is posted; Discord refuses to delete the old one: the queue stops at the
    deletion, keeping the old lineup's link. Once a league admin discards it, the log channel's
    Discard line names the deletion not done, and both lineups stand."""
    league = await ongoing_league(tmp_path)
    await _old_lineup_standing(league)
    league.channel(LINEUP[PRO]).messages[OLD_LINEUP].delete = AsyncMock(
        side_effect=http_error(discord.Forbidden, status=403, text="Missing Permissions")
    )

    await _ask(league, PRO)
    await run_queue(league.bot)

    stopped = await stopped_job(league.db_path)
    assert stopped is not None and stopped["name"] == "delete_replaced"
    assert OLD_LINEUP_LINK in str(stopped["result"])

    await discard_job(league.bot)

    assert await stopped_job(league.db_path) is None
    assert "deleting the earlier lineup of **Pro**" in await _said(league)
    assert OLD_LINEUP in league.channel(LINEUP[PRO]).messages
    assert len([kind for kind, _ in _lineup_events(league) if kind == "send"]) == 1


async def test_a_stop_between_the_lineup_post_and_its_delete_deletes_the_old_one_on_restart(
    tmp_path,
):
    league = await ongoing_league(tmp_path)
    await _old_lineup_standing(league)
    await _ask(league, PRO)
    await _run_lineup_through(league, "post_lineup")
    assert OLD_LINEUP in league.channel(LINEUP[PRO]).messages

    await league.restart()
    await run_queue(league.bot)

    assert OLD_LINEUP not in league.channel(LINEUP[PRO]).messages
    assert len([kind for kind, _ in _lineup_events(league) if kind == "send"]) == 1
    assert [job["name"] for job in await _lineup_jobs(league) if job["done_at"]].count(
        "delete_replaced") == 1


async def test_a_lineup_step_with_no_channel_raises(tmp_path):
    """Pro's lineup channel was never set. The lineup job a review or an approval runs for Pro
    (`lineup_step`, through the real placement service) raises, naming the command that sets the
    channel, for the queue to stop on; nothing is posted."""
    from types import SimpleNamespace

    from leaguebot.core.models.change import StepFailedOnDiscord
    from leaguebot.core.services.lineup_posting import lineup_step
    from tests.support.change_queue import in_a_job

    league = await ongoing_league(tmp_path)
    await league.write("UPDATE divisions SET lineup_channel_id = NULL WHERE id = ?", PRO)
    before = len(league.texts(LINEUP[PRO]))
    step = lineup_step(league.bot.placement_service)
    ctx = SimpleNamespace(
        bot=league.bot, kept=[], step_payload={"division_id": PRO, "division_name": "Pro"},
        payload={}, tries=0,
    )

    with in_a_job(), pytest.raises(StepFailedOnDiscord, match="/division lineup-channel"):
        await step.run(ctx)

    assert len(league.texts(LINEUP[PRO])) == before
