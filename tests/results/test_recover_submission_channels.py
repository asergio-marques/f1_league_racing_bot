"""What a restart does to a submission channel that was left open.

Issue #208. `_recover_orphaned_submission_channels` was uncovered. It runs on every start-up
and decides the fate of every round whose submission channel is still open — which, after a
crash, is every round that was mid-submission or mid-review. It is the difference between a
league carrying on and a division's race being silently abandoned.

**There are three kinds of orphan and they are treated in opposite ways.** A round
*mid-submission* has its partial results deleted and its channel destroyed, because a
half-submitted race is not a result and the wizard has to start again. A round *in penalty
review* keeps its channel and has the prompt re-posted, because the results are complete and
the stewards' work is the part that was interrupted. A round *awaiting appeals* keeps its
channel too and gets the appeals prompt back. Deleting a review channel would take a completed
race's results with it, so `test_a_round_in_review_keeps_its_channel_and_its_results` is the
one to read before simplifying the branches.

**A review is put back through the change queue** (#439, slice 2). Recovery no longer posts a
prompt itself: it asks, as the bot, for `results.review.open` (a round in its report stage) or
`results.appeals.open` (a round awaiting appeals), and the queue does the rest. What the change then
does is pinned in `test_review_open_change.py`; this file pins what recovery asks for. The queue
here is a stand-in that records each request.

**Interim results are not posted twice.** `results_posted` records whether the provisional
table reached the division before the crash, and the request carries `publish = not
results_posted` — otherwise every restart adds another copy of the same table to the channel a
league is reading.

**The old prompt is replaced, once the new one stands.** Two live prompts over one round mean two
staged lists, and whichever is approved second overwrites the first; the request carries the old
prompt's id for the change to delete after posting the new one.

**A mid-submission orphan is put to the queue** (#439, slice 6b). Recovery asks, as the bot, for
`results.submission.restart` of the round and does nothing of it itself; the change clears the
sessions and the row, deletes the channel, announces that the sessions submitted before the crash
are gone and must be re-entered, and opens the submission afresh. What the change does is pinned in
`test_submission_restart_change.py`; this file pins that recovery asks for it.

**A restart mid-resubmission keeps the round's results** (issue #210). The pastes collected so
far lived in memory and are gone, but nothing was written until the last session was in, so the
round is an ordinary review whose `resubmitting` flag has to come down. Treating it as
mid-submission would delete the results the resubmission was meant to supersede. The restart
asks for the review back returning from a restart, and the abandon is that opening's own jobs
(#439, slice 6b, C53), so these cases alone run on a real queue.

**Nothing here may raise.** This runs inside start-up, so one unrecoverable round must not stop
the rest being recovered — nor stop the bot finishing its start-up at all. Every failure path
is tested for it, because a guild missing from the cache is ordinary on a large bot and a
deleted channel is ordinary on any.
"""
from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

import leaguebot.__main__ as bot_module
from leaguebot.core.db.database import get_connection, run_migrations

SERVER_ID = 11708
SEASON_ID = 1
DIVISION_ID = 11
ROUND_ID = 21
CHANNEL_ID = 700
PROMPT_MESSAGE_ID = 8800


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _make_db(
    tmp_path,
    *,
    name: str = "recover_submission",
    closed: int = 0,
    in_penalty_review: int = 0,
    results_posted: int = 0,
    prompt_message_id: int | None = None,
    round_status: str = "AWAITING_RESULTS",
    with_results: bool = True,
    resubmitting: int = 0,
    resubmit_prompt_message_id: int | None = None,
) -> str:
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
        await db.execute(
            "INSERT INTO rounds (id, division_id, round_number, scheduled_at, format, "
            "status) VALUES (?, ?, 3, '2026-02-01T18:00:00+00:00', 'NORMAL', ?)",
            (ROUND_ID, DIVISION_ID, round_status),
        )
        await db.execute(
            "INSERT INTO round_submission_channels (round_id, channel_id, created_at, "
            "closed, in_penalty_review, results_posted, prompt_message_id, "
            "resubmitting, resubmit_prompt_message_id) "
            "VALUES (?, ?, '2026-02-01T00:00:00+00:00', ?, ?, ?, ?, ?, ?)",
            (
                ROUND_ID,
                CHANNEL_ID,
                closed,
                in_penalty_review,
                results_posted,
                prompt_message_id,
                resubmitting,
                resubmit_prompt_message_id,
            ),
        )
        if with_results:
            await db.execute(
                "INSERT INTO session_results (round_id, division_id, session_type, status) "
                "VALUES (?, ?, 'FEATURE_RACE', 'ACTIVE')",
                (ROUND_ID, DIVISION_ID),
            )
        await db.commit()
    return db_path


def _channel(*, fetch_fails: bool = False, delete_fails: bool = False):
    channel = MagicMock()
    channel.id = CHANNEL_ID
    message = MagicMock()
    message.id = 9900
    message.delete = AsyncMock()
    channel.send = AsyncMock(return_value=message)
    if fetch_fails:
        channel.fetch_message = AsyncMock(
            side_effect=discord.NotFound(MagicMock(status=404), "gone")
        )
    else:
        old = MagicMock()
        old.delete = AsyncMock()
        channel.fetch_message = AsyncMock(return_value=old)
        channel._old = old
    channel.delete = AsyncMock(
        side_effect=discord.HTTPException(MagicMock(status=403), "no")
        if delete_fails
        else None
    )
    channel._message = message
    return channel


def _bot(db_path: str, *, guild_missing: bool = False, channel=None):
    stub = MagicMock()
    stub.config_service.get_league_server_id = AsyncMock(return_value=SERVER_ID)
    stub.db_path = db_path
    stub.add_view = MagicMock()
    stub.output_router = MagicMock()
    stub.output_router.post_log = AsyncMock(return_value=None)

    guild = MagicMock()
    guild.get_channel = MagicMock(return_value=channel)
    stub.get_guild = MagicMock(return_value=None if guild_missing else guild)
    stub._guild = guild
    stub._channel = channel
    stub.change_queue = MagicMock()
    stub.change_queue.ask = AsyncMock(return_value=1)
    return stub


async def _recover(stub):
    """Run recovery, with the submission wizard's re-run stubbed where the results module still
    has one; the queue's requests are recorded on the bot's stand-in queue."""
    with patch(
        "leaguebot.results.services.result_submission_service.run_result_submission_job",
        new=AsyncMock(), create=True,
    ) as rerun, patch("leaguebot.__main__.asyncio.create_task", new=MagicMock()) as create_task:
        await bot_module._recover_orphaned_submission_channels(stub)
    return {"rerun": rerun, "create_task": create_task}


def _asked(stub) -> list[tuple[str, dict]]:
    """Each request recovery put to the queue: its kind and its payload."""
    asked = []
    for call in stub.change_queue.ask.await_args_list:
        kind = call.args[0] if call.args else call.kwargs["kind"]
        payload = call.args[1] if len(call.args) > 1 else call.kwargs["payload"]
        asked.append((kind, payload))
    return asked


def _asked_once(stub, kind: str) -> dict:
    """The payload of the one request of *kind*, which is for round 3."""
    payloads = [payload for asked, payload in _asked(stub) if asked == kind]
    assert len(payloads) == 1, _asked(stub)
    assert payloads[0]["round_id"] == ROUND_ID
    return payloads[0]


async def _rows(db_path, table) -> int:
    async with get_connection(db_path) as db:
        cursor = await db.execute(f"SELECT COUNT(*) AS n FROM {table}")
        return (await cursor.fetchone())["n"]


def _logged(stub) -> str:
    return "\n".join(
        str(call.args[0]) for call in stub.output_router.post_log.await_args_list
    )


# ---------------------------------------------------------------------------
# A channel that is already closed
# ---------------------------------------------------------------------------


async def test_a_closed_channel_is_left_alone(tmp_path):
    """It was closed in an orderly way by the run that opened it — there is nothing
    interrupted about it, and recovering it would destroy a completed round's results."""
    db_path = await _make_db(tmp_path, name="recover_closed", closed=1)
    stub = _bot(db_path, channel=_channel())

    stubs = await _recover(stub)

    assert await _rows(db_path, "round_submission_channels") == 1
    assert await _rows(db_path, "session_results") == 1
    stubs["rerun"].assert_not_awaited()
    assert _asked(stub) == []
    stubs["create_task"].assert_not_called()


# ---------------------------------------------------------------------------
# A round that was mid-submission
# ---------------------------------------------------------------------------


def _asked_restart(stub) -> dict:
    """The one `results.submission.restart` recovery asked, for round 3 in channel 700."""
    payload = _asked_once(stub, "results.submission.restart")
    assert payload["channel_id"] == CHANNEL_ID
    return payload


async def test_the_submission_wizard_is_reopened(tmp_path):
    """Production has no `/test-mode advance` to fall back on, so without this the round is
    silently abandoned and nobody submits its results at all. Recovery asks the queue, as the
    bot, for round 3's restart, naming its channel, and does the rest of it on no account itself:
    it starts no task, deletes no channel and posts nothing, and the half-submitted results and
    the submission row stand until the queue's jobs clear them
    (`test_submission_restart_change.py`)."""
    db_path = await _make_db(tmp_path, name="recover_rerun")
    channel = _channel()
    stub = _bot(db_path, channel=channel)

    stubs = await _recover(stub)

    _asked_restart(stub)
    assert [kind for kind, _ in _asked(stub)] == ["results.submission.restart"]
    stubs["create_task"].assert_not_called()
    stubs["rerun"].assert_not_awaited()
    channel.delete.assert_not_awaited()
    stub.output_router.post_log.assert_not_awaited()
    assert await _rows(db_path, "session_results") == 1
    assert await _rows(db_path, "round_submission_channels") == 1


async def test_a_guild_missing_from_the_cache_still_clears_the_row(tmp_path):
    """The database half can be put right without Discord, and leaving the row would have
    the round recovered again on every later restart. With the league's server missing from the
    cache, recovery still asks the queue for round 3's restart, and starts no task."""
    db_path = await _make_db(tmp_path, name="recover_noguild")
    stub = _bot(db_path, guild_missing=True, channel=None)

    stubs = await _recover(stub)

    _asked_restart(stub)
    stubs["create_task"].assert_not_called()


async def test_a_channel_already_gone_still_reopens_the_wizard(tmp_path):
    """Someone deleted it by hand between the crash and the restart, which changes nothing
    about the round needing its results: recovery still asks the queue for round 3's restart."""
    db_path = await _make_db(tmp_path, name="recover_nochannel")
    stub = _bot(db_path, channel=None)

    stubs = await _recover(stub)

    _asked_restart(stub)
    stubs["create_task"].assert_not_called()


# ---------------------------------------------------------------------------
# A round that was in penalty review
# ---------------------------------------------------------------------------


async def test_a_round_in_review_keeps_its_channel_and_its_results(tmp_path):
    """The results are complete; the stewards' work is what was interrupted. Deleting the
    channel here would take a completed race's results with it."""
    db_path = await _make_db(tmp_path, name="recover_review", in_penalty_review=1)
    channel = _channel()
    stub = _bot(db_path, channel=channel)

    await _recover(stub)

    assert await _rows(db_path, "session_results") == 1
    assert await _rows(db_path, "round_submission_channels") == 1
    channel.delete.assert_not_awaited()


async def test_the_penalty_review_prompt_is_re_posted(tmp_path):
    """Recovery asks the queue, as the bot, to open the review again; it posts nothing itself."""
    db_path = await _make_db(tmp_path, name="recover_reprompt", in_penalty_review=1)
    channel = _channel()
    stub = _bot(db_path, channel=channel)

    await _recover(stub)

    _asked_once(stub, "results.review.open")
    assert [kind for kind, _ in _asked(stub)] == ["results.review.open"]
    channel.send.assert_not_awaited()


async def test_interim_results_already_posted_are_not_posted_again(tmp_path):
    """Otherwise every restart adds another copy of the same provisional table to the
    channel a league is reading."""
    db_path = await _make_db(
        tmp_path, name="recover_skip", in_penalty_review=1, results_posted=1
    )
    stub = _bot(db_path, channel=_channel())

    await _recover(stub)

    assert _asked_once(stub, "results.review.open")["publish"] is False


async def test_interim_results_never_posted_are_posted_now(tmp_path):
    """The crash came before they went out, so the division has seen nothing."""
    db_path = await _make_db(
        tmp_path, name="recover_noskip", in_penalty_review=1, results_posted=0
    )
    stub = _bot(db_path, channel=_channel())

    await _recover(stub)

    assert _asked_once(stub, "results.review.open")["publish"] is True


async def test_the_old_prompt_is_deleted_once_the_new_one_stands(tmp_path):
    """Two live prompts over one round mean two staged lists, and whichever is approved
    second overwrites the first. Recovery hands the old prompt's id to the change, which deletes
    it once the new prompt is posted (`test_review_open_change.py` pins the order); recovery
    itself deletes nothing."""
    db_path = await _make_db(
        tmp_path,
        name="recover_oldprompt",
        in_penalty_review=1,
        prompt_message_id=PROMPT_MESSAGE_ID,
    )
    channel = _channel()
    stub = _bot(db_path, channel=channel)

    await _recover(stub)

    assert _asked_once(stub, "results.review.open")["old_prompt_id"] == PROMPT_MESSAGE_ID
    channel._old.delete.assert_not_awaited()


async def test_a_prompt_already_gone_does_not_stop_the_re_post(tmp_path):
    """Deleted by hand, or by a previous partial recovery — either way the new prompt is
    what matters."""
    db_path = await _make_db(
        tmp_path,
        name="recover_promptgone",
        in_penalty_review=1,
        prompt_message_id=PROMPT_MESSAGE_ID,
    )
    stub = _bot(db_path, channel=_channel(fetch_fails=True))

    await _recover(stub)

    _asked_once(stub, "results.review.open")


async def test_a_round_with_no_recorded_prompt_fetches_nothing(tmp_path):
    """A crash before the id was stored; asking Discord for message `None` would raise."""
    db_path = await _make_db(
        tmp_path, name="recover_noprompt", in_penalty_review=1, prompt_message_id=None
    )
    channel = _channel()
    stub = _bot(db_path, channel=channel)

    await _recover(stub)

    channel.fetch_message.assert_not_awaited()


async def test_a_review_put_back_asks_the_queue_it_was_handed(tmp_path):
    """A service is handed the change queue rather than looking it up on the bot (#439 slice 6b,
    fold-in 6). A round left in its penalty review at a restart is put back by asking the queue
    the recovery was handed, and nothing is asked of the bot's own."""
    from leaguebot.results.services.review_recovery import recover_review

    db_path = await _make_db(tmp_path, name="recover_handed", in_penalty_review=1)
    stub = _bot(db_path, channel=_channel())
    handed = MagicMock()
    handed.ask = AsyncMock(return_value=1)
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT rsc.round_id, rsc.channel_id, rsc.in_penalty_review, rsc.results_posted, "
            "rsc.prompt_message_id, rsc.appeals_prompt_message_id, rsc.resubmitting, "
            "rsc.resubmit_prompt_message_id, rsc.resubmit_started_by, r.division_id, r.status, "
            "r.round_number, d.name AS division_name FROM round_submission_channels rsc "
            "JOIN rounds r ON r.id = rsc.round_id JOIN divisions d ON d.id = r.division_id"
        )
        row = await cursor.fetchone()

    assert await recover_review(stub, row, stub._guild, queue=handed) is True

    handed.ask.assert_awaited_once()
    assert handed.ask.await_args.args[0] == "results.review.open"
    assert handed.ask.await_args.args[1]["round_id"] == ROUND_ID
    stub.change_queue.ask.assert_not_awaited()


@pytest.mark.parametrize(
    "round_status",
    ["AWAITING_RESULTS", "AWAITING_APPEAL_VERDICTS"],
    ids=["penalty review", "appeals"],
)
async def test_a_review_whose_guild_is_missing_is_left_intact(tmp_path, round_status: str):
    """Nothing can be posted without the guild, and clearing the row would lose the round's
    place in the review or in its appeals — so it is left for the next restart to find."""
    db_path = await _make_db(
        tmp_path,
        name="recover_review_noguild",
        in_penalty_review=1,
        round_status=round_status,
    )
    stub = _bot(db_path, guild_missing=True, channel=None)

    await _recover(stub)

    assert await _rows(db_path, "round_submission_channels") == 1
    assert await _rows(db_path, "session_results") == 1


async def test_a_review_whose_channel_is_missing_is_left_intact(tmp_path):
    db_path = await _make_db(tmp_path, name="recover_review_nochannel", in_penalty_review=1)
    stub = _bot(db_path, channel=None)

    await _recover(stub)

    assert await _rows(db_path, "round_submission_channels") == 1
    assert await _rows(db_path, "session_results") == 1


async def test_a_failing_re_prompt_does_not_stop_the_start_up(tmp_path):
    """This runs inside start-up: one unrecoverable round must not stop the rest being
    recovered, nor stop the bot finishing its start-up at all."""
    db_path = await _make_db(tmp_path, name="recover_review_fails", in_penalty_review=1)
    stub = _bot(db_path, channel=_channel())
    stub.change_queue.ask = AsyncMock(side_effect=RuntimeError("the database is locked"))

    await _recover(stub)  # must not raise


# ---------------------------------------------------------------------------
# A round that was awaiting appeals
# ---------------------------------------------------------------------------


async def test_a_round_awaiting_appeals_gets_the_appeals_prompt_back(tmp_path):
    """A different prompt from the penalty one: the penalties are settled and what is
    outstanding is the appeals against them. Recovery asks the queue for it, as the bot, and
    posts nothing itself."""
    db_path = await _make_db(
        tmp_path,
        name="recover_appeals",
        in_penalty_review=1,
        round_status="AWAITING_APPEAL_VERDICTS",
    )
    channel = _channel()
    stub = _bot(db_path, channel=channel)

    await _recover(stub)

    _asked_once(stub, "results.appeals.open")
    assert [kind for kind, _ in _asked(stub)] == ["results.appeals.open"]
    channel.send.assert_not_awaited()


async def test_an_appeals_round_keeps_its_results(tmp_path):
    """They are final but for the appeals, and deleting them would destroy a raced round."""
    db_path = await _make_db(
        tmp_path,
        name="recover_appeals_results",
        in_penalty_review=1,
        round_status="AWAITING_APPEAL_VERDICTS",
    )
    stub = _bot(db_path, channel=_channel())

    await _recover(stub)

    assert await _rows(db_path, "session_results") == 1


async def test_a_failing_appeals_re_post_does_not_stop_the_start_up(tmp_path):
    db_path = await _make_db(
        tmp_path,
        name="recover_appeals_fails",
        in_penalty_review=1,
        round_status="AWAITING_APPEAL_VERDICTS",
    )
    stub = _bot(db_path, channel=_channel())
    stub.change_queue.ask = AsyncMock(side_effect=RuntimeError("the database is locked"))

    await _recover(stub)  # must not raise


# ---------------------------------------------------------------------------
# A restart in the middle of a resubmission
# ---------------------------------------------------------------------------


# These cases run on a real queue (`test_review_open_change.py`'s league: round 3 of Pro, a
# Normal round, its Feature Race won by Lewis ahead of Max, its review open in submission
# channel 702 and resubmitting), because the abandon is the review opening's own jobs: the
# restart asks one change, `results.review.open` returning from a restart, whose jobs take the
# flag down and the drafts away (`abandon_resubmission`), take the Cancel button down
# (`take_down_cancel`) and post the restart notice (`post_restart_notice`) — results spec :417.

STARTER = 4242
ANNOUNCEMENT = 6100


async def _resubmitting_league(tmp_path, *, started_by: int | None = None,
                               announcement: bool = False):
    """Round 3 of the review league resubmitting, its review prompt standing. Where *started_by*,
    that member pressed Resubmit, and the server knows them as Alex. Where *announcement*, the
    resubmission's announcement (6100) stands in channel 702 with its Cancel button."""
    from types import SimpleNamespace

    from tests.results.test_review_open_change import SUBMISSION_CHANNEL, _league
    from tests.support.review_league import PROMPT

    league = await _league(tmp_path, in_review=True, results_posted=True, resubmitting=True,
                           prompt=PROMPT, round_status="AWAITING_REPORT_VERDICTS")
    async with get_connection(league.db_path) as db:
        await db.execute(
            "UPDATE round_submission_channels SET resubmit_started_by = ?, "
            "resubmit_prompt_message_id = ? WHERE round_id = ?",
            (started_by, ANNOUNCEMENT, ROUND_ID),
        )
        await db.commit()
    if announcement:
        submission = league.channel(SUBMISSION_CHANNEL)
        submission.seed(ANNOUNCEMENT, "⚠️ **Results resubmission started.**")
        submission.messages[ANNOUNCEMENT].view = MagicMock()
    if started_by is not None:
        known = league.guild.get_member.side_effect
        league.guild.get_member.side_effect = lambda member_id: (
            SimpleNamespace(display_name="Alex", id=member_id, mention=f"<@{member_id}>")
            if member_id == started_by else known(member_id)
        )
    return league


async def _restart(league) -> list[str]:
    """The bot restarts and recovery runs, then the queue. Gives the names of the steps done by
    the one change the restart asked of round 3, which must be its review's opening."""
    from tests.results.test_review_open_change import _recover
    from tests.support.change_queue import change_rows, step_rows

    before = {change["id"] for change in await change_rows(league.db_path)}
    await _recover(league)
    asked = [change for change in await change_rows(league.db_path)
             if change["id"] not in before]
    assert [change["kind"] for change in asked] == ["results.review.open"], asked
    return [step["name"] for step in await step_rows(league.db_path, asked[0]["id"])
            if step["done_at"] is not None]


async def _resubmitting_flag(db_path) -> int:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT resubmitting FROM round_submission_channels WHERE round_id = ?", (ROUND_ID,)
        )
        return (await cursor.fetchone())[0]


def _plain(line: str) -> str:
    """A log channel's line as written: the mentions the router wraps in backticks unwrapped,
    and the rule it closes each entry with dropped."""
    kept = [part for part in line.splitlines() if part.strip("―")]
    return "\n".join(kept).replace("`<@", "<@").replace(">`", ">")


def _lapse_lines(league) -> list[str]:
    return [_plain(line) for line in league.bot.log_channel.sent if line.startswith("⌛")]


async def test_a_restart_mid_resubmission_keeps_the_results_and_restores_the_review(tmp_path):
    """The bot restarts while round 3 is resubmitting. The round's results stay, its channel is
    not deleted, and its review is asked back without publishing, returning from a restart; once
    the queue has run, the resubmission was abandoned by that opening's own job, its flag is down
    and one review prompt stands. Left set, the review channel's guard would stay down and let
    messages through; the flag comes down in the save of that `abandon_resubmission` job."""
    from tests.results.test_review_open_change import SUBMISSION_CHANNEL, _prompts
    from tests.support.change_queue import change_rows

    league = await _resubmitting_league(tmp_path)

    done = await _restart(league)

    assert "abandon_resubmission" in done
    assert await _resubmitting_flag(league.db_path) == 0
    assert await _rows(league.db_path, "session_results") == 1
    assert SUBMISSION_CHANNEL in league.channels
    [opening] = [change for change in await change_rows(league.db_path)
                 if change["kind"] == "results.review.open"][-1:]
    payload = json.loads(opening["payload"])
    assert payload["publish"] is False
    assert payload["returning"] == "restart"
    assert len(_prompts(league)) == 1


async def test_a_restart_mid_resubmission_says_the_earlier_results_stand(tmp_path):
    """The manager was part-way through pasting; the prompt coming back unexplained would
    look as though the pastes had been accepted. The review opening's `post_restart_notice` job
    tells channel 702 that the earlier results still stand."""
    from tests.results.test_review_open_change import SUBMISSION_CHANNEL, _contents

    league = await _resubmitting_league(tmp_path)

    done = await _restart(league)

    assert "post_restart_notice" in done
    assert any("The earlier results still stand" in text
               for text in _contents(league, SUBMISSION_CHANNEL))


async def test_a_restart_mid_resubmission_is_recorded_as_a_lapse(tmp_path):
    """Alex (id 4242) pressed 🔄 Resubmit Initial Results on round 3 (Pro) and was part-way
    through pasting when the bot restarted. Once the queue has run the review's opening, one
    line in the log channel records the resubmission as lapsed, naming Alex as its starter, with
    what became of it and what to do next beneath it."""
    league = await _resubmitting_league(tmp_path, started_by=STARTER)

    done = await _restart(league)

    assert "abandon_resubmission" in done
    records = _lapse_lines(league)
    assert len(records) == 1, records
    head, *detail = records[0].splitlines()
    assert head == (
        "⌛ the “Resubmit Initial Results” button of round 3 (Pro) lapsed unconfirmed "
        f"(started by Alex (<@{STARTER}>))"
    )
    assert detail == [
        "  The sessions entered were lost. The earlier results stand. "
        "Press Resubmit again to re-enter them."
    ]


async def test_a_restart_mid_resubmission_takes_down_the_cancel_button(tmp_path):
    """The view was not persistent, so after a restart pressing it would only fail. The review
    opening's `take_down_cancel` job edits the announcement (6100) without its button."""
    from tests.results.test_review_open_change import SUBMISSION_CHANNEL

    league = await _resubmitting_league(tmp_path, announcement=True)

    done = await _restart(league)

    assert "take_down_cancel" in done
    assert league.channel(SUBMISSION_CHANNEL).messages[ANNOUNCEMENT].view is None


async def test_a_resubmission_announcement_already_gone_does_not_stop_the_recovery(tmp_path):
    """The announcement (6100) was deleted by hand before the restart. Its take-down completes,
    the queue does not stop, the flag comes down and the review is asked back once."""
    from tests.support.change_queue import stopped_job

    league = await _resubmitting_league(tmp_path)

    done = await _restart(league)

    assert "take_down_cancel" in done
    assert await stopped_job(league.db_path) is None
    assert await _resubmitting_flag(league.db_path) == 0


async def test_a_restart_mid_resubmission_with_its_channel_gone_is_still_closed_out(tmp_path):
    """Alex (id 4242) pressed 🔄 Resubmit Initial Results on round 3 (Pro), and the bot
    restarted part-way through; meanwhile the submission channel was deleted. There is nowhere
    to post the notice or take down the Cancel button, but the round must not stay stuck as
    resubmitting: the review opening's restart notice completes posting nothing, the flag comes
    down, the round's results stay, and the one lapse line naming Alex is written as it would be
    with the channel there (results spec :417)."""
    from tests.results.test_review_open_change import SUBMISSION_CHANNEL

    league = await _resubmitting_league(tmp_path, started_by=STARTER)
    league.channels.pop(SUBMISSION_CHANNEL)

    done = await _restart(league)

    assert "post_restart_notice" in done
    assert await _resubmitting_flag(league.db_path) == 0
    assert await _rows(league.db_path, "session_results") == 1
    records = _lapse_lines(league)
    assert len(records) == 1, records
    head, *detail = records[0].splitlines()
    assert head == (
        "⌛ the “Resubmit Initial Results” button of round 3 (Pro) lapsed unconfirmed "
        f"(started by Alex (<@{STARTER}>))"
    )
    assert detail == [
        "  The sessions entered were lost. The earlier results stand. "
        "Press Resubmit again to re-enter them."
    ]
