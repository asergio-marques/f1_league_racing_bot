"""The jobs the results dialogues share (#439, slice 6b, C44): `results/services/submission_steps.py`.

A round's submission channel, an amendment's channel and the prompts posted in them are made by jobs
of a change, so a refusal stops the queue with Retry and Discard rather than leaving a manager
waiting on a prompt that never came.

- **`create_channel`** (an `ACT`) creates a text channel beside the division's results channel, in
  its category. The dialogue hands it the `record` that saves the channel's id with the job's mark.
  A channel created whose save then fails is kept on the failure, and the next try deletes it
  before creating another, so a retry leaves no second channel.
- A creation Discord refuses stops the queue, naming the permission the bot lacks.
- **`post_prompt`** (an `ACT`) posts the session's prompt in the dialogue's channel; a refusal
  stops the queue.

The steps are driven through a dummy change type built from them, on round 3 of
`tests.support.review_league`'s league. They are imported inside each test, so the file collects
before they are built.
"""
from __future__ import annotations

import itertools
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from leaguebot.core.db.database import get_connection
from tests.support.change_queue import (
    attach_queue,
    http_error,
    member_interaction,
    retry_job,
    run_queue,
    step_rows,
    stopped_job,
)
from tests.support.review_league import (
    NOW,
    RESULTS_CHANNEL,
    ROUND_ID,
    ReviewLeague,
    channel,
    review_league,
)

CHANNEL_NAME = "pro-round-3-results"
_new_ids = itertools.count(7500)


def _with_channel_creation(league: ReviewLeague) -> list[int]:
    """Let the league's server create text channels, each a channel double of the league's.
    Gives the list the ids of the channels created are added to."""
    created: list[int] = []
    league.channel(RESULTS_CHANNEL).category = MagicMock(name="results category")

    async def _create(name: str, **kwargs: Any) -> Any:
        made = channel(next(_new_ids), league.events)
        made.name = name
        made.category = kwargs.get("category")
        made.guild = league.guild
        league.channels[made.id] = made
        league.events.append(("create_channel", made.id, made.id))
        created.append(made.id)
        return made

    league.guild.create_text_channel = AsyncMock(side_effect=_create)
    return created


async def _record_channel(db: Any, _ctx: Any, result: Any) -> None:
    """The dialogue's record: round 3's submission row takes the channel created."""
    await db.execute(
        "UPDATE round_submission_channels SET channel_id = ? WHERE round_id = ?",
        (int(result.result["channel_id"]), ROUND_ID),
    )


async def _run(league: ReviewLeague, *planned: tuple[Any, dict[str, Any]]) -> None:
    """Ask a member's dummy change running the *planned* steps, each with its payload, then run
    the queue."""
    from leaguebot.core.models.change import PlannedStep, Verdict
    from leaguebot.core.services.change_queue import ChangeType

    async def _go(_ctx: Any) -> Any:
        return Verdict.go()

    dummy = ChangeType(
        kind="dummy",
        opening=tuple(PlannedStep(step.name, payload) for step, payload in planned),
        steps={step.name: step for step, _ in planned},
        check=_go,
        key=lambda payload: f"dummy|{json.dumps(payload, sort_keys=True)}",
        doing=lambda _payload: "Opening the dialogue",
        outcome=lambda _ctx: "✅ Done.",
    )
    attach_queue(league.bot, league.db_path, now=NOW, types=(dummy,))
    await league.bot.change_queue.ask(
        "dummy", {}, interaction=member_interaction(league.bot), what="`/dummy`",
        refusal_what="`/dummy`",
    )
    await run_queue(league.bot)


async def _submission_channel(db_path: str) -> int:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT channel_id FROM round_submission_channels WHERE round_id = ?", (ROUND_ID,)
        )
        return int((await cursor.fetchone())[0])


def _create_payload() -> dict[str, Any]:
    return {"name": CHANNEL_NAME, "beside_channel_id": RESULTS_CHANNEL}


async def test_a_channel_created_is_saved_with_its_mark(tmp_path):
    """The job creates `pro-round-3-results` in the results channel's category, and the
    dialogue's record saves its id on round 3's submission row in the same save as the job's
    mark."""
    from leaguebot.results.services.submission_steps import create_channel_step

    league = await review_league(tmp_path)
    created = _with_channel_creation(league)

    await _run(league, (create_channel_step(_record_channel), _create_payload()))

    assert await stopped_job(league.db_path) is None
    [made] = created
    assert league.channel(made).name == CHANNEL_NAME
    assert league.channel(made).category is league.channel(RESULTS_CHANNEL).category
    assert await _submission_channel(league.db_path) == made
    [job] = await step_rows(league.db_path)
    assert job["done_at"] is not None


async def test_a_channel_created_whose_save_failed_is_deleted_before_the_next_try(tmp_path):
    """The channel is created but the database refuses its row, so the queue stops with the
    channel standing. On Retry the first channel is deleted before a second is created, and the
    second is the one saved."""
    from leaguebot.results.services.submission_steps import create_channel_step

    league = await review_league(tmp_path)
    created = _with_channel_creation(league)
    async with get_connection(league.db_path) as db:
        await db.execute(
            "CREATE TRIGGER refuse_channel BEFORE UPDATE ON round_submission_channels "
            "BEGIN SELECT RAISE(ABORT, 'row refused'); END"
        )
        await db.commit()

    await _run(league, (create_channel_step(_record_channel), _create_payload()))

    assert await stopped_job(league.db_path) is not None
    [first] = created

    async with get_connection(league.db_path) as db:
        await db.execute("DROP TRIGGER refuse_channel")
        await db.commit()
    await retry_job(league.bot)

    assert await stopped_job(league.db_path) is None
    [_, second] = created
    assert ("delete_channel", first, first) in league.events
    assert league.events.index(("delete_channel", first, first)) < league.events.index(
        ("create_channel", second, second))
    assert await _submission_channel(league.db_path) == second


async def test_a_refused_channel_creation_stops_the_queue_naming_the_permission(tmp_path):
    """Discord refuses to create the channel (the bot lacks Manage Channels). The queue stops at
    the job, its failure naming the permission, and no row is saved."""
    from leaguebot.results.services.submission_steps import create_channel_step

    league = await review_league(tmp_path)
    _with_channel_creation(league)
    league.guild.create_text_channel = AsyncMock(
        side_effect=http_error(discord.Forbidden, status=403, text="Missing Permissions")
    )
    before = await _submission_channel(league.db_path)

    await _run(league, (create_channel_step(_record_channel), _create_payload()))

    job = await stopped_job(league.db_path)
    assert job is not None
    assert "Manage Channels" in (job["last_failure"] or "")
    assert await _submission_channel(league.db_path) == before


async def test_a_prompt_refused_stops_the_queue(tmp_path):
    """The prompt asking for round 3's Feature Race is refused by the submission channel. The
    queue stops at the job with nothing posted; on Retry the prompt naming the Feature Race is
    posted."""
    from leaguebot.results.services.submission_steps import post_prompt_step

    league = await review_league(tmp_path)
    async with get_connection(league.db_path) as db:
        await db.execute("UPDATE rounds SET format = 'SPRINT' WHERE id = ?", (ROUND_ID,))
        await db.commit()
    submission = await _submission_channel(league.db_path)
    target = league.channel(submission)
    target.send_fails = http_error(discord.Forbidden, status=403, text="Missing Access")
    before = set(target.messages)

    await _run(league, (post_prompt_step(), {
        "channel_id": submission, "round_id": ROUND_ID, "session_type": "FEATURE_RACE",
    }))

    assert await stopped_job(league.db_path) is not None
    assert set(target.messages) == before

    target.send_fails = None
    await retry_job(league.bot)

    assert await stopped_job(league.db_path) is None
    [posted] = [mid for mid in target.messages if mid not in before]
    assert "Feature Race" in target.messages[posted].content
