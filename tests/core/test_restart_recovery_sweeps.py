"""Two of the sweeps a restart runs over work the previous process left behind.

Issue #208. `_recover_expired_review_prompts` and `_recover_orphaned_amend_channels` were
uncovered. Each cleans up after something that lived only in memory and died with the process —
a view's timeout, a `wait_for` loop — and between them they decide what a league finds when the
bot comes back. The third, `_recover_missed_phases`, is pinned in `test_bot_phase_recovery.py`.

**Every standing review prompt is expired by definition.** The approve button's five minutes are
a `discord.ui.View` timeout held in memory; the bot was down, so they cannot have been served,
and no view exists to serve them now. Each row's take-down is therefore asked of the change queue
(`season.review.take_down`, ending `restart`), as the bot, before the queue starts — there is no
"still valid" case to preserve, and a reader who added one would leave a button nothing is
listening to standing for ever. The sweep deletes, posts and writes nothing itself, and leaves
alone a review whose take-down is already in hand: the queue's jobs delete the question and the
report above it and drop the row, so a deletion Discord refuses stops the queue with the row
standing rather than losing the only record of a message still up. Those cases are pinned in
`test_review_prompt_sweep.py`.

**An abandoned amendment's lapse is asked of the change queue** (#439, slice 6b, C64). The sweep
puts nothing back, deletes nothing and writes nothing itself: it asks `results.amend.lapse` as the
bot, with the restart's wording, before the queue starts, and leaves alone an amendment whose
lapse is already in hand (core spec :133). The queue's jobs put the round back, delete the channel
and close its row with the deletion's mark, so a crash between them is resumed rather than run
again.

**Every sweep announces itself.** A league manager whose amendment vanished needs to know to
re-run the command, and a reviewer whose approve button disappeared needs to know to review
again. Neither is discoverable from the absence of a message.
"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock

import discord

import leaguebot.__main__ as bot_module
from leaguebot.core.db.database import get_connection, run_migrations

SERVER_ID = 11808
SEASON_ID = 1
DIVISION_ID = 11
ROUND_ID = 21
STARTER_ID = 4242


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _base_db(tmp_path, name: str) -> str:
    db_path = os.path.join(str(tmp_path), f"{name}.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 900, 100, 101)",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO seasons (id, season_number, start_date, status) "
            "VALUES (?, 1, '2026-01-01', 'ACTIVE')",
            (SEASON_ID,),
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
            "VALUES (?, ?, 'Pro', 1, 555)",
            (DIVISION_ID, SEASON_ID),
        )
        await db.commit()
    return db_path


def _stub_bot(db_path: str):
    stub = MagicMock()
    stub.config_service.get_league_server_id = AsyncMock(return_value=SERVER_ID)
    stub.db_path = db_path
    stub.module_service = MagicMock()
    stub.output_router = MagicMock()
    stub.output_router.post_log = AsyncMock(return_value=None)
    stub.get_guild = MagicMock(return_value=None)
    stub.get_channel = MagicMock(return_value=None)
    stub.fetch_channel = AsyncMock(return_value=None)
    return stub


# ---------------------------------------------------------------------------
# Standing season-review prompts
# ---------------------------------------------------------------------------


async def test_an_unreadable_prompt_table_does_not_stop_the_start_up(tmp_path):
    """This runs inside start-up; a failure to read one table must not stop the bot
    starting at all."""
    db_path = await _base_db(tmp_path, "review_unreadable")
    stub = _stub_bot(db_path)
    stub.db_path = os.path.join(str(tmp_path), "does-not-exist-at-all.db")

    await bot_module._recover_expired_review_prompts(stub)  # must not raise


# ---------------------------------------------------------------------------
# Orphaned amendment channels
# ---------------------------------------------------------------------------
#
# The league is `review_league`'s: round 3 of Pro (round 21 here as there), FINAL, its Feature
# Race won by Lewis ahead of Max, its submission channel closed. Alex (4242) opened
# `/results rounds amend` of its Feature Race, channel 705, and the bot stopped. The restart asks
# the queue for the amendment's lapse (`results.amend.lapse`, C64), as the bot, before the queue
# starts; the queue's jobs put the round back, delete the channel, close the row and write the
# restart's line.


async def _amend_league(tmp_path, *, amended: bool = False):
    """The league above with Alex's amendment open. *amended*: stage one was recorded, the round
    snapshotted and its Feature Race rewritten with Max ahead of Lewis."""
    from leaguebot.results.models.points_config import SessionType
    from tests.support.review_league import (
        AMEND_CHANNEL,
        NOW,
        review_league,
        snapshot_amendment,
    )

    league = await review_league(tmp_path, round_status="FINAL")
    async with get_connection(league.db_path) as db:
        await db.execute(
            "UPDATE round_submission_channels SET closed = 1, in_penalty_review = 0"
        )
        await db.execute(
            "INSERT INTO round_amend_channels (round_id, channel_id, session_types, created_at, "
            "started_by) VALUES (?, ?, '[\"FEATURE_RACE\"]', ?, ?)",
            (ROUND_ID, AMEND_CHANNEL, NOW.isoformat(), STARTER_ID),
        )
        await db.commit()
    if amended:
        await snapshot_amendment(league.db_path, ROUND_ID, [SessionType.FEATURE_RACE])
        async with get_connection(league.db_path) as db:
            await db.execute(
                "UPDATE race_session_results SET finishing_position = finishing_position + 10"
            )
            await db.execute(
                "UPDATE race_session_results SET finishing_position = 13 - finishing_position"
            )
            await db.commit()
    known = league.guild.get_member.side_effect

    def _member(user_id):
        if user_id != STARTER_ID:
            return known(user_id)
        alex = MagicMock(spec=discord.Member)
        alex.id = STARTER_ID
        alex.display_name = "Alex"
        alex.mention = f"<@{STARTER_ID}>"
        return alex

    league.guild.get_member.side_effect = _member
    league.guild.fetch_member = AsyncMock(side_effect=_member)
    return league


async def _restart(league) -> list[dict]:
    """The restart's sweep, then the lapses it asked of the queue."""
    from tests.support.change_queue import change_rows

    await bot_module._recover_orphaned_amend_channels(league.bot)
    return [row for row in await change_rows(league.db_path)
            if row["kind"] == "results.amend.lapse"]


async def _restart_and_run(league) -> None:
    from tests.support.change_queue import run_queue

    await _restart(league)
    await run_queue(league.bot)


def _amend_lines(league) -> list[str]:
    """The log channel's lines about the amendment, as written: the mentions the router wraps in
    backticks unwrapped, and the rule closing each entry dropped."""
    lines = []
    for line in league.bot.log_channel.sent:
        if "/results rounds amend` of round 3" not in line:
            continue
        kept = [part for part in line.splitlines() if part.strip("―")]
        lines.append("\n".join(kept).replace("`<@", "<@").replace(">`", ">"))
    return lines


async def _amend_row(league) -> dict | None:
    async with get_connection(league.db_path) as db:
        cursor = await db.execute("SELECT * FROM round_amend_channels WHERE round_id = ?",
                                  (ROUND_ID,))
        row = await cursor.fetchone()
    return dict(row) if row is not None else None


async def _winner(league) -> int:
    from tests.support.review_league import race_rows

    rows = await race_rows(league.db_path)
    return next(driver for driver, row in rows.items() if row["finishing_position"] == 1)


def test_amendments_are_reverted_before_submission_channels_resume():
    """**The amend sweep runs before the submission-channel one** (#345, decided 2026-09-21).

    An amendment open at the restart has its unapproved corrections in the database, and a
    submission channel whose results were saved but never posted posts its standings from it on
    recovery. Swept the other way round, those standings would publish the corrections the
    amendment's revert then takes back. Read from the source, because the two run inside
    `on_ready` among a dozen start-up steps no test drives whole.
    """
    import inspect

    source = inspect.getsource(bot_module.main)
    amend = source.index("await _recover_orphaned_amend_channels(bot)")
    submission = source.index("await _recover_orphaned_submission_channels(bot)")
    assert amend < submission


async def test_a_second_restart_leaves_a_lapse_in_hand_alone(tmp_path):
    """The bot stops again before the queue has run the lapse the first restart asked for. The
    second restart finds that lapse in hand and asks no other (core :133): one lapse, one line."""
    from tests.support.change_queue import run_queue

    league = await _amend_league(tmp_path)
    await _restart(league)

    lapses = await _restart(league)
    await run_queue(league.bot)

    assert len(lapses) == 1
    assert len(_amend_lines(league)) == 1


async def test_an_amendment_cannot_outlive_its_round(tmp_path):
    """`round_amend_channels.round_id` carries `ON DELETE CASCADE`, so a round deleted while an
    amendment was open takes the amendment's row with it: the restart finds nothing, asks no
    lapse, and the log channel is told nothing."""
    from tests.support.change_queue import run_queue

    league = await _amend_league(tmp_path)
    async with get_connection(league.db_path) as db:
        await db.execute("DELETE FROM rounds WHERE id = ?", (ROUND_ID,))
        await db.commit()
    assert await _amend_row(league) is None

    lapses = await _restart(league)
    await run_queue(league.bot)

    assert lapses == []
    assert _amend_lines(league) == []


async def test_a_channel_already_deleted_still_clears_the_row(tmp_path):
    """Someone deleted the amendment channel by hand while the bot was down. The lapse's
    deletion finds it gone and completes: the queue does not stop, the row is closed and the
    line written."""
    from tests.support.change_queue import stopped_job
    from tests.support.review_league import AMEND_CHANNEL

    league = await _amend_league(tmp_path)
    del league.channels[AMEND_CHANNEL]

    await _restart_and_run(league)

    assert await stopped_job(league.db_path) is None
    row = await _amend_row(league)
    assert row is not None and row["closed_at"] is not None
    assert len(_amend_lines(league)) == 1


async def test_a_restart_with_no_amendments_open_does_nothing(tmp_path):
    """No amendment is open: the restart asks no lapse and nothing is written."""
    from tests.support.change_queue import run_queue

    league = await _amend_league(tmp_path)
    async with get_connection(league.db_path) as db:
        await db.execute("DELETE FROM round_amend_channels")
        await db.commit()

    lapses = await _restart(league)
    await run_queue(league.bot)

    assert lapses == []
    assert _amend_lines(league) == []


async def test_an_abandoned_amendment_is_put_back_as_it_was(tmp_path):
    """Stage one commits, so abandoning an amendment is not a no-op (#345).

    Stage one of Alex's amendment wrote Max ahead of Lewis, and the bot stopped before the
    stages were approved. The restart asks the lapse and changes nothing itself; when the queue
    runs it, the round is put back with Lewis ahead, the channel deleted, and the line says the
    round was put back and to re-run the command.
    """
    from tests.support.change_queue import run_queue
    from tests.support.review_league import LEWIS, MAX

    league = await _amend_league(tmp_path, amended=True)

    await _restart(league)
    assert await _winner(league) == MAX

    await run_queue(league.bot)

    assert await _winner(league) == LEWIS
    [line] = _amend_lines(league)
    assert "put back as it was" in line
    assert "re-run /results rounds amend" in line


async def test_an_amendment_put_back_at_a_restart_is_recorded_as_a_lapse(tmp_path):
    """Alex started `/results rounds amend` of round 3 (Pro) and entered the corrections; the
    bot restarted before the amendment was approved, and the queue's lapse puts the round back.
    The README promises this is recorded as a lapse: one line, naming Alex as its starter, with
    the session being amended, what became of it and what to do next beneath it. The line is
    written before the channel's deletion, the job after it, runs, so it says the channel is
    being deleted, never that it was."""
    league = await _amend_league(tmp_path, amended=True)

    await _restart_and_run(league)

    [line] = _amend_lines(league)
    head, *detail = line.splitlines()
    assert head == (
        f"⌛ `/results rounds amend` of round 3 (Pro) lapsed unconfirmed "
        f"(started by Alex (<@{STARTER_ID}>))"
    )
    assert (
        "  Amendment channel being deleted, and the round put back as it was. Please re-run "
        "/results rounds amend."
    ) in detail
    assert "Feature Race" in "\n".join(detail), detail


async def test_an_amendment_with_nothing_to_put_back_is_recorded_neutrally(tmp_path):
    """Alex started `/results rounds amend` of round 3 (Pro); at the restart there is nothing to
    put back, so the bot cannot tell whether the corrections were never entered or the
    amendment had been approved. One line names Alex and says a restart ended it — never
    "lapsed" — with both cases, and what to run in each, beneath it: for the first, the command
    the manager now types; for the second, the two syncs. A manager with four sessions amended
    over an evening needs to know which one went, so the line names the round and its division,
    as every record names them (#482), and the session beneath.

    The log must not claim a round was put back when nothing was reverted: an amendment approved
    and rebuilding the channels when the bot stopped needs the sync commands, not a reassurance.
    The row is closed with its channel's deletion."""
    league = await _amend_league(tmp_path)

    await _restart_and_run(league)

    [line] = _amend_lines(league)
    head = line.splitlines()[0]
    assert (
        f"`/results rounds amend` of round 3 (Pro), started by Alex (<@{STARTER_ID}>), "
        "was ended by a restart"
    ) in head
    assert "lapsed" not in line
    assert (
        "  Amendment channel being deleted; nothing needed putting back. If the amendment had not "
        "been approved, re-run /results rounds amend. If it had, its channels may be "
        "part-rebuilt: run /results rounds sync and /results standings sync."
    ) in line.splitlines()
    assert "Feature Race" in line
    assert "put back as it was" not in line
    row = await _amend_row(league)
    assert row is not None and row["closed_at"] is not None


async def test_a_revert_that_fails_stops_the_queue_keeping_the_snapshot(tmp_path):
    """**The snapshot is the only way back, so a failed revert keeps it** (#345).

    Stage one of Alex's amendment is recorded, but its snapshot cannot be read back. The lapse
    the restart asked stops the league lane at its `revert` job: the row stands open with its
    snapshot, the channel is not deleted and the round is left as stage one wrote it, for Retry
    once the cause is put right.
    """
    from tests.support.change_queue import stopped_job
    from tests.support.review_league import AMEND_CHANNEL, MAX

    league = await _amend_league(tmp_path, amended=True)
    async with get_connection(league.db_path) as db:
        await db.execute("UPDATE round_amend_channels SET pre_amendment_state = '{not json'")
        await db.commit()

    await _restart_and_run(league)

    stopped = await stopped_job(league.db_path, "league")
    assert stopped is not None and stopped["name"] == "revert"
    row = await _amend_row(league)
    assert row is not None and row["closed_at"] is None
    assert row["pre_amendment_state"] == "{not json"
    assert ("delete_channel", AMEND_CHANNEL, AMEND_CHANNEL) not in league.events
    assert await _winner(league) == MAX
