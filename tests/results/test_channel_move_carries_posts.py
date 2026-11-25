"""Moving a division's results or standings channel carries the posts already up across (#667).

The league is `review_league`'s: division 11 (Pro) of season 1, its rounds 3 and 4 final, results
switched on, images off. Its results channel 700 holds round 3's Feature Race table as message
8800, and its standings channel 701 round 3's standings as message 8801; a test adds round 4's
where it needs a second round. Channel 703 stands on the server unused, and so does 704 where the
league is given no verdicts channel.

The manager Admin runs `/results channel results` (or `standings`) for Pro and the queue is run.
The setting saves at once, as any setting does, with its usual line, and never stops the queue.
Where the channel it moves away from still stands, it then asks the bot's own carry
(`results.channel.carry`), which waits behind any stopped job on the league's lane and posts as a
sync does: each post goes up in the new channel, then its old message is deleted from the old one.

The owner's decisions (2026-11-25): moving away from a channel that still stands is refused while
an amendment of one of the division's rounds is open or the season is pending completion;
replacing a deleted channel is always allowed, as a repair, and carries nothing; and a carry the
season reaches pending completion ahead of is dropped, the log channel saying why.
"""
from __future__ import annotations

from typing import Any

import discord
import pytest

from leaguebot.core.models.season import SeasonStage, status_of_stage
from tests.support.change_queue import (
    change_rows,
    discard_job,
    http_error,
    retry_job,
    run_queue,
    step_rows,
    stopped_job,
)
from tests.support.review_league import (
    AMEND_CHANNEL,
    DIVISION_ID,
    LATER_ROUND_ID,
    LEWIS,
    LEWIS_PROFILE,
    MAX,
    MAX_PROFILE,
    OLD_RESULTS,
    OLD_STANDINGS,
    RESULTS_CHANNEL,
    ROUND_ID,
    SEASON_ID,
    STANDINGS_CHANNEL,
    VERDICTS_CHANNEL,
    ReviewLeague,
    block_queue,
    channel,
    review_league,
)
from tests.support.settings_league import interaction as member_asks
from tests.support.settings_league import last_said
from tests.support.switches import answer_switch
from tests.support.undecorate import undecorate

CARRY = "results.channel.carry"
#: The channel the results move to.
NEW_RESULTS = 703
#: The channel the standings move to: the league's verdicts channel, given to none here.
NEW_STANDINGS = VERDICTS_CHANNEL
#: Round 4's Feature Race table and its standings, standing where a test posts them.
OLD_LATER_RESULTS = 8810
OLD_LATER_STANDINGS = 8811

#: Each command: its old channel, the channel it moves to, and the column it sets.
MOVES = {
    "results": (RESULTS_CHANNEL, NEW_RESULTS, "results_channel_id"),
    "standings": (STANDINGS_CHANNEL, NEW_STANDINGS, "standings_channel_id"),
}

NOT_CARRIED = "#667: a move does not yet carry the posts already up to the new channel"
NOT_REFUSED = "#667: a move away from a standing channel is not yet refused"
NOT_DROPPED = "#667: no carry is yet asked, so none is dropped with its log line"


async def _league(tmp_path: Any) -> ReviewLeague:
    """`review_league`'s league with round 3 final, no verdicts channel, results switched on, and
    the real season and module services the command and its check read. Channel 703 stands on
    the server, and the server proves a channel gone by its fetch, as Discord does."""
    league = await review_league(tmp_path, round_status="FINAL", verdicts_channel=False)
    await _write(league, "INSERT OR REPLACE INTO results_module_config (id, module_enabled) "
                         "VALUES (1, 1)")
    answer_switch(league.bot.module_service, "results", True)
    vars(league.bot.season_service).pop("get_divisions", None)
    league.channels[NEW_RESULTS] = channel(NEW_RESULTS, league.events)
    league.channels[NEW_RESULTS].guild = league.guild
    league.guild.fetch_channel = league.bot.fetch_channel
    return league


async def _write(league: ReviewLeague, sql: str, *args: Any) -> None:
    from leaguebot.framework.db.database import get_connection

    async with get_connection(league.db_path) as db:
        await db.execute(sql, args)
        await db.commit()


async def _round_4_posted(league: ReviewLeague) -> None:
    """Round 4 at Spa holds a Feature Race too, Lewis winning it: its table stands in the results
    channel as message 8810, and its standings in the standings channel as message 8811."""
    from leaguebot.framework.db.database import get_connection
    from leaguebot.results.services.publishing.results_post_service import (
        set_standings_message_id_on,
    )

    async with get_connection(league.db_path) as db:
        session = await db.execute(
            "INSERT INTO session_results (round_id, division_id, session_type, status, "
            "config_name, results_message_id, results_message_ids) "
            "VALUES (?, ?, 'FEATURE_RACE', 'ACTIVE', 'Standard', ?, ?)",
            (LATER_ROUND_ID, DIVISION_ID, OLD_LATER_RESULTS, f"[{OLD_LATER_RESULTS}]"),
        )
        for position, (profile, driver, points) in enumerate(
            ((LEWIS_PROFILE, LEWIS, 25), (MAX_PROFILE, MAX, 18)), start=1,
        ):
            await db.execute(
                "INSERT INTO race_session_results (session_result_id, driver_user_id, "
                "team_instance_id, finishing_position, outcome, base_time_ms, points_awarded, "
                "driver_profile_id) VALUES (?, ?, 3001, ?, 'CLASSIFIED', ?, ?, ?)",
                (session.lastrowid, driver, position, 3_600_000 + position * 1000, points,
                 profile),
            )
        await set_standings_message_id_on(
            db, DIVISION_ID, LATER_ROUND_ID, OLD_LATER_STANDINGS,
            message_ids=f"[{OLD_LATER_STANDINGS}]",
        )
        await db.commit()
    league.channel(RESULTS_CHANNEL).seed(OLD_LATER_RESULTS, "round 4 results")
    league.channel(STANDINGS_CHANNEL).seed(OLD_LATER_STANDINGS, "round 4 standings")


async def _amendment_opens(league: ReviewLeague) -> None:
    """An amendment of round 3 opens in channel 705."""
    await _write(
        league,
        "INSERT INTO round_amend_channels (round_id, channel_id, session_types, created_at) "
        "VALUES (?, ?, '[\"FEATURE_RACE\"]', '2026-10-05T11:00:00+00:00')",
        ROUND_ID, AMEND_CHANNEL,
    )


async def _pending_completion(league: ReviewLeague) -> None:
    """Every division has finished, and the season stands at Pending completion."""
    await _write(
        league, "UPDATE seasons SET stage = ?, status = ? WHERE id = ?",
        SeasonStage.PENDING_COMPLETION.value,
        status_of_stage(SeasonStage.PENDING_COMPLETION).value, SEASON_ID,
    )


async def _move(league: ReviewLeague, which: str, name: str = "Pro") -> Any:
    """Admin (4242) runs `/results channel <which>` for *name* and the command's new channel; the
    queue is not run. Gives the interaction, whose words `last_said` reads."""
    from leaguebot.results.cogs.results_cog import ResultsCog

    cog = ResultsCog.__new__(ResultsCog)
    cog.bot = league.bot
    interaction = member_asks(league.bot, f"results channel {which}")
    interaction.guild = league.guild
    await undecorate(getattr(ResultsCog, f"channel_{which}"))(
        cog, interaction, name, league.channel(MOVES[which][1])
    )
    return interaction


async def _held(league: ReviewLeague, which: str) -> int | None:
    from leaguebot.framework.db.database import get_connection

    async with get_connection(league.db_path) as db:
        row = await (await db.execute(
            f"SELECT {MOVES[which][2]} FROM division_results_config "  # noqa: S608
            "WHERE division_id = ?", (DIVISION_ID,),
        )).fetchone()
    return None if row[0] is None else int(row[0])


async def _results_id(league: ReviewLeague, round_id: int) -> Any:
    from leaguebot.framework.db.database import get_connection

    async with get_connection(league.db_path) as db:
        row = await (await db.execute(
            "SELECT results_message_id FROM session_results WHERE round_id = ?", (round_id,),
        )).fetchone()
    return row["results_message_id"]


async def _carries(league: ReviewLeague) -> list[dict[str, Any]]:
    return [row for row in await change_rows(league.db_path) if row["kind"] == CARRY]


def _updated(which: str) -> str:
    """The setting's usual reply to a move to the command's new channel."""
    return f"✅ {which.capitalize()} channel for **Pro** updated to <#{MOVES[which][1]}>."


def _lines(league: ReviewLeague, since: int = 0) -> list[str]:
    return [str(line) for line in league.bot.log_channel.sent[since:]]


@pytest.mark.xfail(strict=True, reason=NOT_CARRIED)
async def test_a_results_move_carries_every_table_to_the_new_channel(tmp_path):
    """Rounds 3 and 4 each have a Feature Race table standing in 700 (8800, 8810). Admin moves
    Pro's results channel to 703, and the queue runs. The setting saves 703 with its usual
    Success line, and its reply says the channel was updated and that the tables already up are
    being moved. The carry then runs: round 3's table goes up in 703 before round 4's, each before
    its old message is deleted from 700, which ends empty; the sessions record the new messages;
    no standings are posted; nothing stops the queue; and the carry's line is a Success naming
    both channels."""
    league = await _league(tmp_path)
    await _round_4_posted(league)

    interaction = await _move(league, "results")
    await run_queue(league.bot)

    reply = last_said(interaction)
    assert reply.startswith(_updated("results")), reply
    assert "moved" in reply.removeprefix(_updated("results")), reply
    assert await _held(league, "results") == NEW_RESULTS
    lines = _lines(league)
    assert any("| /results channel results | Success" in line for line in lines), lines
    assert await stopped_job(league.db_path) is None
    assert [row["state"] for row in await _carries(league)] == ["DONE"]
    new_3, new_4 = league.sent_to(NEW_RESULTS)
    assert await _results_id(league, ROUND_ID) == new_3
    assert await _results_id(league, LATER_ROUND_ID) == new_4
    events = league.events
    assert events.index(("send", NEW_RESULTS, new_3)) < events.index(
        ("send", NEW_RESULTS, new_4))
    assert events.index(("send", NEW_RESULTS, new_3)) < events.index(
        ("delete", RESULTS_CHANNEL, OLD_RESULTS))
    assert events.index(("send", NEW_RESULTS, new_4)) < events.index(
        ("delete", RESULTS_CHANNEL, OLD_LATER_RESULTS))
    assert league.channel(RESULTS_CHANNEL).messages == {}
    assert league.sent_to(STANDINGS_CHANNEL) == []
    assert not [row for row in await step_rows(league.db_path)
                if row["name"].startswith("post_standings")]
    carried = [line for line in lines
               if "| Success" in line and "/results channel results" not in line]
    assert any(str(RESULTS_CHANNEL) in line and str(NEW_RESULTS) in line for line in carried), (
        lines)


@pytest.mark.xfail(strict=True, reason=NOT_CARRIED)
async def test_a_standings_move_carries_each_round_s_standings_to_the_new_channel(tmp_path):
    """Rounds 3 and 4 each have standings standing in 701 (8801, 8811). Admin moves Pro's
    standings channel to 704, and the queue runs. The reply says the channel was updated and that
    the standings already up are being moved; round 3's standings go up in 704 before round 4's,
    each before its old message is deleted from 701, which ends empty, and the rounds record the
    new messages. No results job runs, nothing is posted to 700, and the carry is done."""
    from leaguebot.results.services.publishing.results_post_service import (
        get_standings_message_id,
    )

    league = await _league(tmp_path)
    await _round_4_posted(league)

    interaction = await _move(league, "standings")
    await run_queue(league.bot)

    reply = last_said(interaction)
    assert reply.startswith(_updated("standings")), reply
    assert "moved" in reply.removeprefix(_updated("standings")), reply
    assert await _held(league, "standings") == NEW_STANDINGS
    assert await stopped_job(league.db_path) is None
    assert [row["state"] for row in await _carries(league)] == ["DONE"]
    new_3, new_4 = league.sent_to(NEW_STANDINGS)
    assert await get_standings_message_id(league.db_path, DIVISION_ID, ROUND_ID) == new_3
    assert await get_standings_message_id(league.db_path, DIVISION_ID, LATER_ROUND_ID) == new_4
    events = league.events
    assert events.index(("send", NEW_STANDINGS, new_3)) < events.index(
        ("send", NEW_STANDINGS, new_4))
    assert events.index(("send", NEW_STANDINGS, new_3)) < events.index(
        ("delete", STANDINGS_CHANNEL, OLD_STANDINGS))
    assert events.index(("send", NEW_STANDINGS, new_4)) < events.index(
        ("delete", STANDINGS_CHANNEL, OLD_LATER_STANDINGS))
    assert league.channel(STANDINGS_CHANNEL).messages == {}
    assert league.sent_to(RESULTS_CHANNEL) == []
    assert not [row for row in await step_rows(league.db_path)
                if row["name"] == "post_session_results"]


@pytest.mark.xfail(strict=True, reason=NOT_REFUSED)
@pytest.mark.parametrize("which", sorted(MOVES))
async def test_a_move_while_an_amendment_is_open_is_refused(tmp_path, which):
    """An amendment of round 3 is open in channel 705. Admin moves Pro's results (or standings)
    channel away from 700 (or 701), which still stands, and the queue runs. The move is refused:
    the reply names the amendment and its channel, the division keeps its old channel, no carry
    is asked, and nothing is posted to the new channel."""
    league = await _league(tmp_path)
    await _amendment_opens(league)
    old, new, _column = MOVES[which]

    interaction = await _move(league, which)
    await run_queue(league.bot)

    reply = last_said(interaction)
    assert "amend" in reply.lower() and f"<#{AMEND_CHANNEL}>" in reply, reply
    assert await _held(league, which) == old
    assert await _carries(league) == []
    assert league.sent_to(new) == []


@pytest.mark.xfail(strict=True, reason=NOT_REFUSED)
@pytest.mark.parametrize("which", sorted(MOVES))
async def test_a_move_while_the_season_is_pending_completion_is_refused(tmp_path, which):
    """Every division has finished and the season stands at Pending completion. Admin moves Pro's
    results (or standings) channel away from 700 (or 701), which still stands, and the queue runs.
    The move is refused: the reply says the season is pending completion, the division keeps its
    old channel, no carry is asked, and nothing is posted to the new channel."""
    league = await _league(tmp_path)
    await _pending_completion(league)
    old, new, _column = MOVES[which]

    interaction = await _move(league, which)
    await run_queue(league.bot)

    reply = last_said(interaction)
    assert "pending completion" in reply.lower(), reply
    assert await _held(league, which) == old
    assert await _carries(league) == []
    assert league.sent_to(new) == []


async def _first_set(league: ReviewLeague, which: str) -> None:
    await _write(league, f"UPDATE division_results_config SET {MOVES[which][2]} = NULL")


async def _deleted_while_amending(league: ReviewLeague, which: str) -> None:
    del league.channels[MOVES[which][0]]
    await _amendment_opens(league)


async def _deleted_while_pending_completion(league: ReviewLeague, which: str) -> None:
    del league.channels[MOVES[which][0]]
    await _pending_completion(league)


@pytest.mark.parametrize("which", sorted(MOVES))
@pytest.mark.parametrize("state,verb", [
    pytest.param(_first_set, "set", id="first set"),
    pytest.param(_deleted_while_amending, "updated", id="deleted, amendment open"),
    pytest.param(_deleted_while_pending_completion, "updated",
                 id="deleted, pending completion"),
])
async def test_a_channel_with_none_standing_before_saves_and_asks_no_carry(
    tmp_path, state, verb, which,
):
    """Pro has no results (or standings) channel yet; or its results channel 700 (or standings
    channel 701) has been deleted from the server while an amendment of round 3 is open, or while
    the season is pending completion. Admin sets Pro's results channel to 703 (or standings
    channel to 704), and the queue runs. The channel is saved, as a first set or as the repair of
    a lost channel, the reply is the setting's usual one and nothing more, no carry is asked, and
    nothing is posted to the new channel: there is nothing standing to carry."""
    league = await _league(tmp_path)
    await state(league, which)
    new = MOVES[which][1]

    interaction = await _move(league, which)
    await run_queue(league.bot)

    assert last_said(interaction) == (
        f"✅ {which.capitalize()} channel for **Pro** {verb} to <#{new}>."
    )
    assert await _held(league, which) == new
    assert await _carries(league) == []
    assert league.sent_to(new) == []


def _refused_in_new_channel(league: ReviewLeague) -> None:
    league.channel(NEW_RESULTS).send_fails = http_error(
        discord.Forbidden, status=403, text="Missing Access"
    )


@pytest.mark.xfail(strict=True, reason=NOT_CARRIED)
async def test_a_carried_table_discord_refuses_stops_the_lane_and_leaves_the_old_table(tmp_path):
    """Discord refuses every post in 703. Admin moves Pro's results channel from 700 to 703, and
    the queue runs. The setting is done and 703 stays saved, but the carry stops the league's lane
    at round 3's table: the old table 8800 still stands in 700 and the session still records it,
    nothing having been deleted."""
    league = await _league(tmp_path)
    _refused_in_new_channel(league)

    await _move(league, "results")
    await run_queue(league.bot)

    job = await stopped_job(league.db_path)
    assert job is not None and job["name"] == "post_session_results"
    [carry] = await _carries(league)
    assert job["change_id"] == carry["id"]
    assert await _held(league, "results") == NEW_RESULTS
    assert OLD_RESULTS in league.channel(RESULTS_CHANNEL).messages
    assert await _results_id(league, ROUND_ID) == OLD_RESULTS
    assert league.deleted_in(RESULTS_CHANNEL) == []


@pytest.mark.xfail(strict=True, reason=NOT_CARRIED)
async def test_a_discarded_carried_table_ends_the_carry_incomplete(tmp_path):
    """Discord refuses every post in 703, so the carry stops at round 3's table, and a league
    admin discards it. The carry's line is Incomplete: it says round 3's Feature Race results
    were not posted and names `/results rounds sync` to finish them, and the old table 8800 still
    stands in 700."""
    league = await _league(tmp_path)
    _refused_in_new_channel(league)

    await _move(league, "results")
    await run_queue(league.bot)
    assert await _carries(league)
    await discard_job(league.bot)

    lines = _lines(league)
    assert any(
        "Incomplete" in line and "Round 3" in line and "/results rounds sync" in line
        for line in lines
    ), lines
    assert OLD_RESULTS in league.channel(RESULTS_CHANNEL).messages


@pytest.mark.xfail(strict=True, reason=NOT_DROPPED)
async def test_a_carry_an_amendment_opens_ahead_of_is_dropped_with_its_line(tmp_path):
    """The league's lane is stopped at a job of another change. Admin moves Pro's results channel
    from 700 to 703: the setting runs past the stop and saves 703, and its carry waits behind the
    stop. Meanwhile an amendment of round 3 opens in channel 705. Once the stop is retried and
    the queue moves, the carry is dropped as no longer due: nothing is posted to 703, the old
    table 8800 still stands in 700, and a new line in the log channel says so, naming the
    amendment."""
    league = await _league(tmp_path)
    holder = await block_queue(league)
    await _move(league, "results")
    await run_queue(league.bot)
    assert await _held(league, "results") == NEW_RESULTS
    assert await _carries(league)
    await _amendment_opens(league)
    since = len(league.bot.log_channel.sent)

    holder["fail"] = False
    await retry_job(league.bot)

    assert [row["state"] for row in await _carries(league)] == ["DROPPED"]
    assert league.sent_to(NEW_RESULTS) == []
    assert OLD_RESULTS in league.channel(RESULTS_CHANNEL).messages
    lines = _lines(league, since)
    assert any("amend" in line.lower() and f"<#{RESULTS_CHANNEL}>" in line
               for line in lines), lines


@pytest.mark.xfail(strict=True, reason=NOT_DROPPED)
async def test_a_carry_the_season_reaches_pending_completion_ahead_of_is_dropped_saying_why(
    tmp_path,
):
    """The league's lane is stopped at a job of another change. Admin moves Pro's results channel
    from 700 to 703: the setting runs past the stop and saves 703, and its carry waits behind the
    stop. Meanwhile every division finishes and the season reaches Pending completion. Once the
    stop is retried and the queue moves, the carry is dropped: nothing is posted to 703, the old
    table 8800 still stands in 700, and a new line in the log channel says the posts stay in <#700>
    because the season is pending completion (owner decision 2026-11-25)."""
    league = await _league(tmp_path)
    holder = await block_queue(league)
    await _move(league, "results")
    await run_queue(league.bot)
    assert await _held(league, "results") == NEW_RESULTS
    assert await _carries(league)
    await _pending_completion(league)
    since = len(league.bot.log_channel.sent)

    holder["fail"] = False
    await retry_job(league.bot)

    assert [row["state"] for row in await _carries(league)] == ["DROPPED"]
    assert league.sent_to(NEW_RESULTS) == []
    assert OLD_RESULTS in league.channel(RESULTS_CHANNEL).messages
    lines = _lines(league, since)
    assert any("pending completion" in line.lower() and f"<#{RESULTS_CHANNEL}>" in line
               for line in lines), lines

