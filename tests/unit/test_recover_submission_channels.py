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

**Interim results are not posted twice.** `results_posted` records whether the provisional
table reached the division before the crash, and it is passed straight through as
`skip_results_post` — otherwise every restart adds another copy of the same table to the
channel a league is reading.

**A restart mid-finalisation warns before it re-prompts.** If `staged_penalties` is set the
penalties were already written to the results before the crash, and the prompt comes back with
an empty list. A steward who re-added them would double every penalty on the round, so the
warning is posted first and says plainly not to.

**The old prompt is deleted before the new one goes up.** Two live prompts over one round mean
two staged lists, and whichever is approved second overwrites the first.

**A mid-submission orphan is announced.** The sessions submitted before the crash are gone and
the manager has to re-enter them; that is not something to discover from an empty wizard.

**Nothing here may raise.** This runs inside start-up, so one unrecoverable round must not stop
the rest being recovered — nor stop the bot finishing its start-up at all. Every failure path
is tested for it, because a guild missing from the cache is ordinary on a large bot and a
deleted channel is ordinary on any.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

import bot as bot_module  # noqa: E402
from db.database import get_connection, run_migrations  # noqa: E402

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
    staged_penalties: str | None = None,
    prompt_message_id: int | None = None,
    round_status: str = "AWAITING_RESULTS",
    with_results: bool = True,
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
            "INSERT INTO seasons (id, server_id, season_number, start_date, status) "
            "VALUES (?, ?, 1, '2026-01-01', 'ACTIVE')",
            (SEASON_ID, SERVER_ID),
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
            "closed, in_penalty_review, results_posted, staged_penalties, prompt_message_id) "
            "VALUES (?, ?, '2026-02-01T00:00:00+00:00', ?, ?, ?, ?, ?)",
            (
                ROUND_ID,
                CHANNEL_ID,
                closed,
                in_penalty_review,
                results_posted,
                staged_penalties,
                prompt_message_id,
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
    stub.db_path = db_path
    stub.add_view = MagicMock()
    stub.output_router = MagicMock()
    stub.output_router.post_log = AsyncMock(return_value=None)

    guild = MagicMock()
    guild.get_channel = MagicMock(return_value=channel)
    stub.get_guild = MagicMock(return_value=None if guild_missing else guild)
    stub._guild = guild
    stub._channel = channel
    return stub


async def _recover(stub):
    """Run recovery with everything it reaches into stubbed."""
    state = MagicMock()
    with patch(
        "services.result_submission_service._build_penalty_review_state",
        new=AsyncMock(return_value=state),
    ), patch(
        "services.result_submission_service.enter_penalty_state", new=AsyncMock()
    ) as enter, patch(
        "services.result_submission_service.run_result_submission_job", new=AsyncMock()
    ) as rerun, patch(
        "services.penalty_wizard.AppealsReviewView", new=MagicMock()
    ) as appeals_view, patch(
        "services.penalty_wizard._render_appeals_prompt_content",
        new=AsyncMock(return_value="appeals prompt"),
    ), patch("bot.asyncio.create_task", new=MagicMock()) as create_task:
        await bot_module._recover_orphaned_submission_channels(stub)
    return {
        "enter": enter,
        "rerun": rerun,
        "appeals_view": appeals_view,
        "create_task": create_task,
        "state": state,
    }


async def _rows(db_path, table) -> int:
    async with get_connection(db_path) as db:
        cursor = await db.execute(f"SELECT COUNT(*) AS n FROM {table}")
        return (await cursor.fetchone())["n"]


def _logged(stub) -> str:
    return "\n".join(
        str(call.args[1]) for call in stub.output_router.post_log.await_args_list
    )


def _posted(channel) -> str:
    return "\n".join(str(call.args[0]) for call in channel.send.await_args_list if call.args)


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
    stubs["enter"].assert_not_awaited()


async def test_a_restart_with_nothing_open_does_nothing(tmp_path):
    db_path = await _make_db(tmp_path, name="recover_none", closed=1)
    stub = _bot(db_path, channel=_channel())

    stubs = await _recover(stub)

    stubs["create_task"].assert_not_called()


# ---------------------------------------------------------------------------
# A round that was mid-submission
# ---------------------------------------------------------------------------


async def test_a_half_submitted_rounds_results_are_deleted(tmp_path):
    """A half-submitted race is not a result, and leaving the rows would have
    `get_next_pending_phase` treat the round as complete."""
    db_path = await _make_db(tmp_path, name="recover_midsub")
    stub = _bot(db_path, channel=_channel())

    await _recover(stub)

    assert await _rows(db_path, "session_results") == 0


async def test_the_orphaned_channel_row_is_removed(tmp_path):
    """Otherwise the next restart recovers the same round again, for ever."""
    db_path = await _make_db(tmp_path, name="recover_row")
    stub = _bot(db_path, channel=_channel())

    await _recover(stub)

    assert await _rows(db_path, "round_submission_channels") == 0


async def test_the_discord_channel_is_deleted(tmp_path):
    """The wizard opens a fresh one; leaving this would give the division two submission
    channels and no way to tell which is live."""
    db_path = await _make_db(tmp_path, name="recover_delchannel")
    channel = _channel()
    stub = _bot(db_path, channel=channel)

    await _recover(stub)

    channel.delete.assert_awaited_once()


async def test_the_submission_wizard_is_reopened(tmp_path):
    """Production has no `/test-mode advance` to fall back on, so without this the round is
    silently abandoned and nobody submits its results at all."""
    db_path = await _make_db(tmp_path, name="recover_rerun")
    stub = _bot(db_path, channel=_channel())

    stubs = await _recover(stub)

    stubs["create_task"].assert_called_once()


async def test_the_league_is_told_to_re_submit(tmp_path):
    """The sessions entered before the crash are gone, and that is not something to
    discover from a wizard that asks for them again."""
    db_path = await _make_db(tmp_path, name="recover_notice")
    stub = _bot(db_path, channel=_channel())

    await _recover(stub)

    logged = _logged(stub)
    assert "restarted mid-result-submission" in logged
    assert "re-submit" in logged


async def test_the_notice_names_the_round_by_its_number(tmp_path):
    """A round id means nothing to a league manager; "R3" is what they call it."""
    db_path = await _make_db(tmp_path, name="recover_notice_label")
    stub = _bot(db_path, channel=_channel())

    await _recover(stub)

    assert "R3" in _logged(stub)


async def test_a_channel_that_cannot_be_deleted_does_not_stop_the_recovery(tmp_path):
    """Permissions change; the round still needs its wizard back."""
    db_path = await _make_db(tmp_path, name="recover_delfail")
    stub = _bot(db_path, channel=_channel(delete_fails=True))

    stubs = await _recover(stub)

    stubs["create_task"].assert_called_once()


async def test_a_guild_missing_from_the_cache_still_clears_the_row(tmp_path):
    """The database half can be put right without Discord, and leaving the row would have
    the round recovered again on every later restart."""
    db_path = await _make_db(tmp_path, name="recover_noguild")
    stub = _bot(db_path, guild_missing=True, channel=None)

    stubs = await _recover(stub)

    assert await _rows(db_path, "round_submission_channels") == 0
    assert await _rows(db_path, "session_results") == 0
    stubs["create_task"].assert_not_called()


async def test_a_channel_already_gone_still_reopens_the_wizard(tmp_path):
    """Someone deleted it by hand between the crash and the restart, which changes nothing
    about the round needing its results."""
    db_path = await _make_db(tmp_path, name="recover_nochannel")
    stub = _bot(db_path, channel=None)

    stubs = await _recover(stub)

    stubs["create_task"].assert_called_once()


async def test_a_failing_log_does_not_stop_the_recovery(tmp_path):
    """The notice is the least important part of it, and the round is already recovered by
    the time it is posted."""
    db_path = await _make_db(tmp_path, name="recover_logfail")
    stub = _bot(db_path, channel=_channel())
    stub.output_router.post_log = AsyncMock(side_effect=RuntimeError("no log channel"))

    stubs = await _recover(stub)

    stubs["create_task"].assert_called_once()


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
    db_path = await _make_db(tmp_path, name="recover_reprompt", in_penalty_review=1)
    stub = _bot(db_path, channel=_channel())

    stubs = await _recover(stub)

    stubs["enter"].assert_awaited_once()


async def test_interim_results_already_posted_are_not_posted_again(tmp_path):
    """Otherwise every restart adds another copy of the same provisional table to the
    channel a league is reading."""
    db_path = await _make_db(
        tmp_path, name="recover_skip", in_penalty_review=1, results_posted=1
    )
    stub = _bot(db_path, channel=_channel())

    stubs = await _recover(stub)

    assert stubs["enter"].await_args.kwargs["skip_results_post"] is True


async def test_interim_results_never_posted_are_posted_now(tmp_path):
    """The crash came before they went out, so the division has seen nothing."""
    db_path = await _make_db(
        tmp_path, name="recover_noskip", in_penalty_review=1, results_posted=0
    )
    stub = _bot(db_path, channel=_channel())

    stubs = await _recover(stub)

    assert stubs["enter"].await_args.kwargs["skip_results_post"] is False


async def test_the_old_prompt_is_deleted_first(tmp_path):
    """Two live prompts over one round mean two staged lists, and whichever is approved
    second overwrites the first."""
    db_path = await _make_db(
        tmp_path,
        name="recover_oldprompt",
        in_penalty_review=1,
        prompt_message_id=PROMPT_MESSAGE_ID,
    )
    channel = _channel()
    stub = _bot(db_path, channel=channel)

    await _recover(stub)

    channel.fetch_message.assert_awaited_once_with(PROMPT_MESSAGE_ID)
    channel._old.delete.assert_awaited_once()


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

    stubs = await _recover(stub)

    stubs["enter"].assert_awaited_once()


async def test_a_round_with_no_recorded_prompt_fetches_nothing(tmp_path):
    """A crash before the id was stored; asking Discord for message `None` would raise."""
    db_path = await _make_db(
        tmp_path, name="recover_noprompt", in_penalty_review=1, prompt_message_id=None
    )
    channel = _channel()
    stub = _bot(db_path, channel=channel)

    await _recover(stub)

    channel.fetch_message.assert_not_awaited()


async def test_a_restart_mid_finalisation_warns_before_re_prompting(tmp_path):
    """The penalties were already written to the results before the crash, and the prompt
    comes back with an empty list — a steward who re-added them would double every one."""
    db_path = await _make_db(
        tmp_path,
        name="recover_staged",
        in_penalty_review=1,
        staged_penalties=(
            '[{"driver_user_id": 101, "session_type": "FEATURE_RACE", '
            '"penalty_type": "TIME", "penalty_seconds": 5}]'
        ),
    )
    channel = _channel()
    stub = _bot(db_path, channel=channel)

    await _recover(stub)

    posted = _posted(channel)
    assert "already been applied" in posted or "already applied" in posted
    assert "Do **not** re-add" in posted


async def test_the_warning_lists_the_penalties_that_were_applied(tmp_path):
    """A steward cannot check the results against a warning that does not say what to look
    for."""
    db_path = await _make_db(
        tmp_path,
        name="recover_staged_list",
        in_penalty_review=1,
        staged_penalties=(
            '[{"driver_user_id": 101, "session_type": "FEATURE_RACE", '
            '"penalty_type": "TIME", "penalty_seconds": 5}]'
        ),
    )
    channel = _channel()
    stub = _bot(db_path, channel=channel)

    await _recover(stub)

    posted = _posted(channel)
    assert "101" in posted
    assert "Feature Race" in posted
    assert "+5s" in posted


async def test_a_disqualification_is_named_rather_than_given_seconds(tmp_path):
    """A DSQ has no seconds, and "+Nones" would be the alternative."""
    db_path = await _make_db(
        tmp_path,
        name="recover_staged_dsq",
        in_penalty_review=1,
        staged_penalties=(
            '[{"driver_user_id": 101, "session_type": "FEATURE_RACE", '
            '"penalty_type": "DSQ", "penalty_seconds": null}]'
        ),
    )
    channel = _channel()
    stub = _bot(db_path, channel=channel)

    await _recover(stub)

    assert "DSQ" in _posted(channel)


async def test_an_unreadable_warning_does_not_stop_the_re_prompt(tmp_path):
    """Malformed JSON in that column is a broken state, and the prompt is what the round
    actually needs back."""
    db_path = await _make_db(
        tmp_path,
        name="recover_staged_bad",
        in_penalty_review=1,
        staged_penalties="not json at all",
    )
    stub = _bot(db_path, channel=_channel())

    stubs = await _recover(stub)

    stubs["enter"].assert_awaited_once()


async def test_a_review_whose_guild_is_missing_is_left_intact(tmp_path):
    """Nothing can be posted without the guild, and clearing the row would lose the round's
    place in the review — so it is left for the next restart to find."""
    db_path = await _make_db(tmp_path, name="recover_review_noguild", in_penalty_review=1)
    stub = _bot(db_path, guild_missing=True, channel=None)

    stubs = await _recover(stub)

    assert await _rows(db_path, "round_submission_channels") == 1
    assert await _rows(db_path, "session_results") == 1
    stubs["enter"].assert_not_awaited()


async def test_a_review_whose_channel_is_missing_is_left_intact(tmp_path):
    db_path = await _make_db(tmp_path, name="recover_review_nochannel", in_penalty_review=1)
    stub = _bot(db_path, channel=None)

    stubs = await _recover(stub)

    assert await _rows(db_path, "session_results") == 1
    stubs["enter"].assert_not_awaited()


async def test_a_failing_re_prompt_does_not_stop_the_start_up(tmp_path):
    """This runs inside start-up: one unrecoverable round must not stop the rest being
    recovered, nor stop the bot finishing its start-up at all."""
    db_path = await _make_db(tmp_path, name="recover_review_fails", in_penalty_review=1)
    stub = _bot(db_path, channel=_channel())

    with patch(
        "services.result_submission_service.enter_penalty_state",
        new=AsyncMock(side_effect=RuntimeError("Discord is down")),
    ), patch(
        "services.penalty_wizard.AppealsReviewView", new=MagicMock()
    ), patch("bot.asyncio.create_task", new=MagicMock()):
        await bot_module._recover_orphaned_submission_channels(stub)  # must not raise


# ---------------------------------------------------------------------------
# A round that was awaiting appeals
# ---------------------------------------------------------------------------


async def test_a_round_awaiting_appeals_gets_the_appeals_prompt_back(tmp_path):
    """A different prompt from the penalty one: the penalties are settled and what is
    outstanding is the appeals against them."""
    db_path = await _make_db(
        tmp_path,
        name="recover_appeals",
        in_penalty_review=1,
        round_status="AWAITING_APPEAL_VERDICTS",
    )
    channel = _channel()
    stub = _bot(db_path, channel=channel)

    stubs = await _recover(stub)

    stubs["appeals_view"].assert_called_once()
    stubs["enter"].assert_not_awaited()
    assert "appeals prompt" in _posted(channel)


async def test_the_appeals_view_is_registered_for_persistent_routing(tmp_path):
    """Its buttons have to keep working across the *next* restart too."""
    db_path = await _make_db(
        tmp_path,
        name="recover_appeals_view",
        in_penalty_review=1,
        round_status="AWAITING_APPEAL_VERDICTS",
    )
    stub = _bot(db_path, channel=_channel())

    await _recover(stub)

    stub.add_view.assert_called_once()
    assert stub.add_view.call_args.kwargs["message_id"] == 9900


async def test_the_appeals_prompt_message_id_is_kept_on_the_state(tmp_path):
    """The next recovery deletes the old prompt by it."""
    db_path = await _make_db(
        tmp_path,
        name="recover_appeals_id",
        in_penalty_review=1,
        round_status="AWAITING_APPEAL_VERDICTS",
    )
    stub = _bot(db_path, channel=_channel())

    stubs = await _recover(stub)

    assert stubs["state"].appeals_prompt_message_id == 9900


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

    with patch(
        "services.result_submission_service._build_penalty_review_state",
        new=AsyncMock(side_effect=RuntimeError("no state")),
    ), patch("bot.asyncio.create_task", new=MagicMock()):
        await bot_module._recover_orphaned_submission_channels(stub)  # must not raise


async def test_an_appeals_round_whose_guild_is_missing_is_left_intact(tmp_path):
    db_path = await _make_db(
        tmp_path,
        name="recover_appeals_noguild",
        in_penalty_review=1,
        round_status="AWAITING_APPEAL_VERDICTS",
    )
    stub = _bot(db_path, guild_missing=True, channel=None)

    stubs = await _recover(stub)

    stubs["appeals_view"].assert_not_called()
    assert await _rows(db_path, "session_results") == 1
