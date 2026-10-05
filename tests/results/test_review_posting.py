"""The shared posting jobs a round's review republishes its tables through (#439, slice 2).

`results/services/review_posting.py` gives every change type of a round's review the same jobs:
`post_session_results` and `post_standings` (each an `ACT`), `delete_message` (a `DELETE`), and
`post_batch_notice` and `delete_batch_notice` bracketing a republication. These tests drive them
through a real change queue on a database built by the migrations, with "now" pinned, by a change
type of their own whose one opening job asks the module what to post:

- `review_posting.posting_steps()` gives the jobs, keyed by their names, for a change type's
  `steps`;
- `await review_posting.plan_posts(db, round_id, label=..., later_rounds=..., notice=...)` reads,
  on the save it is handed, the jobs a republication of the round needs, in order;
- `review_posting.not_done(ctx)` gives, from a change's steps, the line naming each posting job
  discarded, for its outcome and its `Incomplete` line.

The Discord side is a fake channel per channel id, recording every send, edit and delete in one
list of events, in order. Image generation is off unless a test turns it on.

Everything of the module is imported inside a test, so this file collects while it is unbuilt.

The tests of `delete_and_repost_final_results` and `repost_subsequent_standings`, which this
module replaces, were deleted with them; what still holds of them is pinned here (a refusal leaving
the old message standing, each later round's posted standings posted again). Their delete-first
order is replaced by `test_a_replacement_is_posted_before_the_old_message_is_deleted`, and their
snapshots recomputed first by the approvals' own save (`test_report_approval_change.py`).
"""
from __future__ import annotations

import itertools
import json
import os
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from tests.support.change_queue import (
    attach_queue,
    discard_job,
    http_error,
    league_double,
    member_interaction,
    restart_queue,
    retry_job,
    run_queue,
    seed_server,
    step_rows,
    stopped_job,
    updated_reply,
)

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)
SEASON_ID = 1
DIVISION_ID = 11
ROUND_ID = 21
LATER_ROUND_ID = 22
UNPOSTED_ROUND_ID = 23
RESULTS_CHANNEL = 700
STANDINGS_CHANNEL = 701
SUBMISSION_CHANNEL = 702
NEW_RESULTS_CHANNEL = 703
OLD_RESULTS = 8800
OLD_STANDINGS = 8801
OLD_LATER_STANDINGS = 8802
LABEL = "Final Results"
KIND = "test.review_posting"

_ids = itertools.count(9000)


# ---------------------------------------------------------------------------
# The Discord side
# ---------------------------------------------------------------------------


def _channel(channel_id: int, events: list[tuple[str, int, int]]) -> Any:
    """A text channel holding its messages by id and recording each send, edit and delete.

    `send_fails`, an exception, refuses every send; `fail_send_at`, a number, refuses that send
    (counted from 1) alone; `delete_fails` refuses every delete. `messages` maps an id to the
    message standing; `seed(id, content)` places one as though posted earlier.
    """
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = channel_id
    channel.mention = f"<#{channel_id}>"
    channel.name = f"channel-{channel_id}"
    channel.messages = {}
    channel.send_fails = None
    channel.fail_send_at = None
    channel.delete_fails = None
    channel.sends = 0

    def _message(message_id: int, content: str) -> Any:
        message = MagicMock()
        message.id = message_id
        message.content = content
        message.channel = channel
        message.jump_url = f"https://discord.test/{channel_id}/{message_id}"

        async def _edit(**changes: Any) -> Any:
            if message_id not in channel.messages:
                raise http_error(discord.NotFound, status=404, text="Unknown Message")
            if "content" in changes:
                message.content = changes["content"]
            events.append(("edit", channel_id, message_id))
            return message

        async def _delete(*_args: Any, **_kwargs: Any) -> None:
            if message_id not in channel.messages:
                raise http_error(discord.NotFound, status=404, text="Unknown Message")
            if channel.delete_fails is not None:
                raise channel.delete_fails
            del channel.messages[message_id]
            events.append(("delete", channel_id, message_id))

        message.edit = AsyncMock(side_effect=_edit)
        message.delete = AsyncMock(side_effect=_delete)
        return message

    def seed(message_id: int, content: str = "earlier posting") -> None:
        channel.messages[message_id] = _message(message_id, content)

    async def _send(content: str = "", **_kwargs: Any) -> Any:
        channel.sends += 1
        if channel.send_fails is not None:
            raise channel.send_fails
        if channel.fail_send_at == channel.sends:
            raise http_error(text="Discord failed part-way")
        message = _message(next(_ids), content)
        channel.messages[message.id] = message
        events.append(("send", channel_id, message.id))
        return message

    async def _fetch(message_id: int) -> Any:
        if message_id not in channel.messages:
            raise http_error(discord.NotFound, status=404, text="Unknown Message")
        return channel.messages[message_id]

    def _partial(message_id: int) -> Any:
        return channel.messages.get(message_id) or _message(message_id, "")

    channel.seed = seed
    channel.send = AsyncMock(side_effect=_send)
    channel.fetch_message = AsyncMock(side_effect=_fetch)
    channel.get_partial_message = MagicMock(side_effect=_partial)
    return channel


class _League:
    """The bot double, its channels, the database and the events the channels saw."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self.events: list[tuple[str, int, int]] = []
        self.bot = league_double(db_path)
        self.channels = {
            cid: _channel(cid, self.events)
            for cid in (RESULTS_CHANNEL, STANDINGS_CHANNEL, SUBMISSION_CHANNEL, NEW_RESULTS_CHANNEL)
        }
        self.channels[RESULTS_CHANNEL].seed(OLD_RESULTS, "provisional results")
        self.channels[STANDINGS_CHANNEL].seed(OLD_STANDINGS, "provisional standings")
        self.channels[STANDINGS_CHANNEL].seed(OLD_LATER_STANDINGS, "round 4 standings")
        self.gone: set[int] = set()
        known = {self.bot.log_channel.id: self.bot.log_channel,
                 self.bot.interaction_channel.id: self.bot.interaction_channel}

        def _get(cid: int) -> Any:
            if cid in self.gone:
                return None
            return self.channels.get(cid) or known.get(cid)

        async def _fetch(cid: int) -> Any:
            found = _get(cid)
            if found is None:
                raise http_error(discord.NotFound, status=404, text="Unknown Channel")
            return found

        guild = MagicMock(spec=discord.Guild)
        guild.id = 12408
        guild.get_channel = MagicMock(side_effect=_get)
        guild.get_member = MagicMock(return_value=None)
        guild.fetch_member = AsyncMock(
            side_effect=http_error(discord.NotFound, status=404, text="Unknown Member")
        )
        self.guild = guild
        self.bot.get_channel = MagicMock(side_effect=_get)
        self.bot.fetch_channel = AsyncMock(side_effect=_fetch)
        self.bot.get_guild = MagicMock(return_value=guild)
        self.bot.guilds = [guild]
        self.bot.module_service.is_images_enabled = AsyncMock(return_value=False)

    def channel(self, cid: int) -> Any:
        return self.channels[cid]

    def sent_to(self, cid: int) -> list[int]:
        return [mid for kind, ch, mid in self.events if kind == "send" and ch == cid]

    def deleted_in(self, cid: int) -> list[int]:
        return [mid for kind, ch, mid in self.events if kind == "delete" and ch == cid]


async def _make_db(
    tmp_path: Any,
    *,
    results_channel: int | None = RESULTS_CHANNEL,
    results_message_id: int | None = OLD_RESULTS,
) -> str:
    """Division 11 (Pro) of season 1, its round 3 at Silverstone with one Feature Race posted and
    its standings posted, round 4's standings posted and round 5's never; a submission channel."""
    db_path = os.path.join(str(tmp_path), "review_posting.db")
    await run_migrations(db_path)
    await seed_server(db_path)
    async with get_connection(db_path) as db:
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
        for round_id, number, track in (
            (ROUND_ID, 3, "Silverstone"), (LATER_ROUND_ID, 4, "Spa"),
            (UNPOSTED_ROUND_ID, 5, "Monza"),
        ):
            await db.execute(
                "INSERT INTO rounds (id, division_id, round_number, scheduled_at, format, "
                "track_name, status) VALUES (?, ?, ?, '2026-02-01T18:00:00+00:00', 'NORMAL', "
                "?, 'FINAL')",
                (round_id, DIVISION_ID, number, track),
            )
        await db.execute(
            "INSERT INTO division_results_config (division_id, results_channel_id, "
            "standings_channel_id, reserves_in_standings) VALUES (?, ?, ?, 1)",
            (DIVISION_ID, results_channel, STANDINGS_CHANNEL),
        )
        await db.execute(
            "INSERT INTO session_results (round_id, division_id, session_type, status, "
            "config_name, results_message_id, results_message_ids) "
            "VALUES (?, ?, 'FEATURE_RACE', 'ACTIVE', 'Standard', ?, ?)",
            (ROUND_ID, DIVISION_ID, results_message_id,
             json.dumps([results_message_id]) if results_message_id else None),
        )
        for round_id, message_id in (
            (ROUND_ID, OLD_STANDINGS), (LATER_ROUND_ID, OLD_LATER_STANDINGS),
            (UNPOSTED_ROUND_ID, None),
        ):
            await db.execute(
                "INSERT INTO driver_standings_snapshots (round_id, division_id, driver_user_id, "
                "standing_position, total_points, standings_message_id, standings_message_ids) "
                "VALUES (?, ?, 1001, 1, 25, ?, ?)",
                (round_id, DIVISION_ID, message_id,
                 json.dumps([message_id]) if message_id else None),
            )
        await db.execute(
            "INSERT INTO round_submission_channels (round_id, channel_id, created_at) "
            "VALUES (?, ?, '2026-02-01T20:00:00+00:00')",
            (ROUND_ID, SUBMISSION_CHANNEL),
        )
        await db.commit()
    return db_path


def _posting_type(*, later_rounds: bool = False, notice: bool = False) -> Any:
    """A change whose one opening job plans the round's republication through the module."""
    from leaguebot.core.models.change import PlannedStep, StepKind, StepResult, Verdict
    from leaguebot.core.services.change_queue import ChangeType, Step
    from leaguebot.results.services import review_posting

    async def plan(db: Any, ctx: Any) -> Any:
        planned = await review_posting.plan_posts(
            db, ctx.payload["round_id"], label=LABEL, later_rounds=later_rounds, notice=notice,
        )
        return StepResult(then=tuple(planned))

    async def check(_ctx: Any) -> Any:
        return Verdict.go()

    def outcome(ctx: Any) -> str:
        missing = review_posting.not_done(ctx)
        return "\n".join(["Republished."] + list(missing))

    return ChangeType(
        kind=KIND,
        opening=(PlannedStep("plan"),),
        steps={"plan": Step("plan", StepKind.SAVE, plan), **review_posting.posting_steps()},
        check=check,
        key=lambda payload: f"{KIND}:{payload['round_id']}",
        doing=lambda _payload: "Republishing round 3",
        outcome=outcome,
    )


async def _league(tmp_path: Any, **make: Any) -> _League:
    return _League(await _make_db(tmp_path, **make))


async def _ask(league: _League, **kinds: Any) -> Any:
    """Attach a queue with the posting change alone, ask it for round 3, and give the asker's
    interaction; the queue is not yet run."""
    attach_queue(league.bot, league.db_path, now=NOW, types=[_posting_type(**kinds)])
    interaction = member_interaction(league.bot)
    await league.bot.change_queue.ask(
        KIND, {"round_id": ROUND_ID}, interaction=interaction, what="the test's republication",
    )
    return interaction


async def _run_until_done(league: _League, job: str) -> None:
    """Run the queue one job at a time until the first *job* is done, as a stop just after it."""
    for _ in range(20):
        rows = [row for row in await step_rows(league.db_path) if row["name"] == job]
        if rows and rows[0]["done_at"] is not None:
            return
        await run_queue(league.bot, steps=1)
    raise AssertionError(f"{job} was never done")


async def _session_ids(db_path: str) -> tuple[int | None, list[int] | None]:
    async with get_connection(db_path) as db:
        row = await (await db.execute(
            "SELECT results_message_id, results_message_ids FROM session_results "
            "WHERE round_id = ?", (ROUND_ID,),
        )).fetchone()
    ids = json.loads(row["results_message_ids"]) if row["results_message_ids"] else None
    return row["results_message_id"], ids


async def _stop_notice(league: _League) -> str:
    job = await stopped_job(league.db_path)
    assert job is not None, "the queue is not stopped"
    return "\n".join(league.bot.log_channel.sent)


# ---------------------------------------------------------------------------
# The order: post, then delete
# ---------------------------------------------------------------------------


async def test_a_replacement_is_posted_before_the_old_message_is_deleted(tmp_path):
    league = await _league(tmp_path)
    await _ask(league)
    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    new = league.sent_to(RESULTS_CHANNEL)
    assert len(new) == 1
    events = league.events
    assert events.index(("send", RESULTS_CHANNEL, new[0])) < events.index(
        ("delete", RESULTS_CHANNEL, OLD_RESULTS)
    )
    assert set(league.channel(RESULTS_CHANNEL).messages) == {new[0]}


async def test_the_new_message_id_is_saved_with_the_post(tmp_path):
    league = await _league(tmp_path)
    await _ask(league)
    await _run_until_done(league, "post_session_results")

    new = league.sent_to(RESULTS_CHANNEL)
    assert len(new) == 1
    assert await _session_ids(league.db_path) == (new[0], [new[0]])
    deletes = [row for row in await step_rows(league.db_path) if row["name"] == "delete_message"]
    assert deletes and all(row["done_at"] is None for row in deletes)


async def test_a_stop_between_the_post_and_the_delete_deletes_the_old_message_on_restart(tmp_path):
    league = await _league(tmp_path)
    await _ask(league)
    await _run_until_done(league, "post_session_results")

    await restart_queue(league.bot)
    await run_queue(league.bot)

    new = league.sent_to(RESULTS_CHANNEL)
    assert len(new) == 1
    assert set(league.channel(RESULTS_CHANNEL).messages) == {new[0]}
    assert league.deleted_in(RESULTS_CHANNEL) == [OLD_RESULTS]


# ---------------------------------------------------------------------------
# A post Discord refuses
# ---------------------------------------------------------------------------


async def test_a_post_discord_refuses_stops_the_queue_and_is_tried_again_as_text(tmp_path):
    league = await _league(tmp_path)
    league.bot.module_service.is_images_enabled = AsyncMock(return_value=True)
    results = league.channel(RESULTS_CHANNEL)
    results.send_fails = http_error(text="Discord is down")
    pictures: list[int] = []

    async def _picture(bot: Any, guild: Any, channel: Any, **_kwargs: Any) -> Any:
        message = await channel.send("IMAGE")
        pictures.append(message.id)
        return MagicMock(applicable=True, message_id=message.id)

    with patch(
        "leaguebot.image.services.image_results_post.try_post", new=AsyncMock(side_effect=_picture)
    ) as try_post:
        await _ask(league)
        await run_queue(league.bot)
        job = await stopped_job(league.db_path)
        assert job is not None and job["name"] == "post_session_results"
        assert OLD_RESULTS in results.messages

        results.send_fails = None
        await retry_job(league.bot)

    assert try_post.await_count == 1
    assert pictures == []
    new = league.sent_to(RESULTS_CHANNEL)
    assert len(new) == 1
    assert "Round 3" in results.messages[new[0]].content
    assert await stopped_job(league.db_path) is None


async def test_a_stopped_post_is_named_by_what_it_posts_and_where(tmp_path):
    league = await _league(tmp_path)
    league.channel(RESULTS_CHANNEL).send_fails = http_error(
        discord.Forbidden, status=403, text="Missing Permissions"
    )
    await _ask(league)
    await run_queue(league.bot)

    notice = await _stop_notice(league)
    assert "Feature Race" in notice
    assert "round 3" in notice.lower()
    assert f"<#{RESULTS_CHANNEL}>" in notice
    assert "sync" not in notice


async def test_a_post_refused_leaves_the_old_message_standing(tmp_path):
    """The regression #237 pinned, on the queue: a channel the bot may no longer post in keeps
    what the league already had, and its id stays recorded."""
    league = await _league(tmp_path)
    league.channel(RESULTS_CHANNEL).send_fails = http_error(
        discord.Forbidden, status=403, text="Missing Permissions"
    )
    await _ask(league)
    await run_queue(league.bot)

    assert OLD_RESULTS in league.channel(RESULTS_CHANNEL).messages
    assert league.deleted_in(RESULTS_CHANNEL) == []
    assert await _session_ids(league.db_path) == (OLD_RESULTS, [OLD_RESULTS])


async def test_a_discarded_post_leaves_the_old_message_and_is_named_with_the_sync_commands(
    tmp_path,
):
    league = await _league(tmp_path)
    league.channel(RESULTS_CHANNEL).send_fails = http_error(
        discord.Forbidden, status=403, text="Missing Permissions"
    )
    interaction = await _ask(league)
    await run_queue(league.bot)
    await discard_job(league.bot)

    assert await stopped_job(league.db_path) is None
    assert OLD_RESULTS in league.channel(RESULTS_CHANNEL).messages
    assert league.deleted_in(RESULTS_CHANNEL) == []
    assert await _session_ids(league.db_path) == (OLD_RESULTS, [OLD_RESULTS])
    reply = updated_reply(interaction)
    assert "Feature Race" in reply
    assert "/results rounds sync" in reply


async def test_a_discarded_delete_leaves_both_messages_and_links_the_old_one(tmp_path):
    league = await _league(tmp_path)
    results = league.channel(RESULTS_CHANNEL)
    results.delete_fails = http_error(discord.Forbidden, status=403, text="Missing Permissions")
    old_link = results.messages[OLD_RESULTS].jump_url
    interaction = await _ask(league)
    await run_queue(league.bot)

    job = await stopped_job(league.db_path)
    assert job is not None and job["name"] == "delete_message"
    await discard_job(league.bot)

    new = league.sent_to(RESULTS_CHANNEL)
    assert set(results.messages) == {OLD_RESULTS, *new}
    assert old_link in updated_reply(interaction)


async def test_a_post_repointed_to_a_new_channel_lands_there_on_retry(tmp_path):
    league = await _league(tmp_path, results_message_id=None)
    league.gone.add(RESULTS_CHANNEL)
    await _ask(league)
    await run_queue(league.bot)
    job = await stopped_job(league.db_path)
    assert job is not None and job["name"] == "post_session_results"

    async with get_connection(league.db_path) as db:
        await db.execute(
            "UPDATE division_results_config SET results_channel_id = ? WHERE division_id = ?",
            (NEW_RESULTS_CHANNEL, DIVISION_ID),
        )
        await db.commit()
    await retry_job(league.bot)

    new = league.sent_to(NEW_RESULTS_CHANNEL)
    assert len(new) == 1
    assert await _session_ids(league.db_path) == (new[0], [new[0]])
    assert await stopped_job(league.db_path) is None


async def _set_results_channel(league: _League, channel_id: int) -> Any:
    """Run `/results channel results` for Pro as the league manager Alex, through the command's
    own body and the real season service. Gives Alex's interaction."""
    from leaguebot.core.services.season_service import SeasonService
    from leaguebot.results.cogs.results_cog import ResultsCog

    league.bot.season_service = SeasonService(league.db_path)
    cog = ResultsCog.__new__(ResultsCog)
    cog.bot = league.bot
    interaction = member_interaction(league.bot)
    interaction.user.display_name = "Alex"
    await cog._set_division_channel(interaction, "Pro", league.channel(channel_id), "results")
    return interaction


async def test_a_stopped_post_lands_once_the_channel_is_set_and_retried(tmp_path):
    """Set the channel, then Retry: the remedy the stop notice leads a manager to, end to end."""
    league = await _league(tmp_path, results_message_id=None)
    league.gone.add(RESULTS_CHANNEL)
    await _ask(league)
    await run_queue(league.bot)
    job = await stopped_job(league.db_path)
    assert job is not None and job["name"] == "post_session_results"

    manager = await _set_results_channel(league, NEW_RESULTS_CHANNEL)
    assert "updated to" in str(manager.response.send_message.await_args.args[0])
    assert await stopped_job(league.db_path) is not None, "setting the channel ran the job"
    await retry_job(league.bot)

    new = league.sent_to(NEW_RESULTS_CHANNEL)
    assert len(new) == 1
    assert await _session_ids(league.db_path) == (new[0], [new[0]])
    assert await stopped_job(league.db_path) is None


@pytest.mark.xfail(strict=True, reason="#439: a part-sent post is not yet removed before a retry")
async def test_a_post_that_fails_part_way_removes_what_it_sent_before_its_next_try(tmp_path):
    league = await _league(tmp_path, results_message_id=None)
    results = league.channel(RESULTS_CHANNEL)
    results.fail_send_at = 2
    results.delete_fails = http_error(text="Discord is down")
    with patch("leaguebot.results.services.results_post_service._MSG_MAX", 40):
        await _ask(league)
        await run_queue(league.bot)
        assert (await stopped_job(league.db_path))["name"] == "post_session_results"
        first_try = league.sent_to(RESULTS_CHANNEL)
        assert first_try and set(first_try) <= set(results.messages)

        results.fail_send_at = None
        results.delete_fails = None
        await retry_job(league.bot)

    standing = set(results.messages)
    assert not standing & set(first_try)
    _anchor, ids = await _session_ids(league.db_path)
    assert ids is not None and len(ids) >= 2
    assert set(ids) == standing


async def test_a_channel_the_division_was_never_given_plans_no_post(tmp_path):
    league = await _league(tmp_path, results_channel=None)
    await _ask(league)
    await run_queue(league.bot)

    names = [row["name"] for row in await step_rows(league.db_path)]
    assert "post_session_results" not in names
    assert "post_standings" in names
    assert await stopped_job(league.db_path) is None


async def test_only_the_round_s_active_sessions_are_posted(tmp_path):
    """What `delete_and_repost_final_results` held: a session whose results were replaced is
    not published again, and its message is left alone."""
    league = await _league(tmp_path)
    async with get_connection(league.db_path) as db:
        await db.execute(
            "INSERT INTO session_results (round_id, division_id, session_type, status, "
            "config_name, results_message_id) "
            "VALUES (?, ?, 'FEATURE_QUALIFYING', 'SUPERSEDED', 'Standard', 8803)",
            (ROUND_ID, DIVISION_ID),
        )
        await db.commit()
    league.channel(RESULTS_CHANNEL).seed(8803, "replaced qualifying")
    await _ask(league)
    await run_queue(league.bot)

    posts = [row for row in await step_rows(league.db_path)
             if row["name"] == "post_session_results"]
    assert len(posts) == 1
    assert len(league.sent_to(RESULTS_CHANNEL)) == 1
    assert league.deleted_in(RESULTS_CHANNEL) == [OLD_RESULTS]
    assert 8803 in league.channel(RESULTS_CHANNEL).messages


async def test_an_old_message_already_gone_completes_its_delete(tmp_path):
    league = await _league(tmp_path)
    del league.channel(RESULTS_CHANNEL).messages[OLD_RESULTS]
    await _ask(league)
    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    deletes = [row for row in await step_rows(league.db_path) if row["name"] == "delete_message"]
    assert deletes and all(row["done_at"] is not None for row in deletes)
    assert len(league.sent_to(RESULTS_CHANNEL)) == 1


async def test_standings_that_still_fit_are_edited_in_place_and_nothing_is_deleted(tmp_path):
    league = await _league(tmp_path)
    await _ask(league)
    await run_queue(league.bot)

    standings = league.channel(STANDINGS_CHANNEL)
    assert ("edit", STANDINGS_CHANNEL, OLD_STANDINGS) in league.events
    assert league.sent_to(STANDINGS_CHANNEL) == []
    assert league.deleted_in(STANDINGS_CHANNEL) == []
    assert OLD_STANDINGS in standings.messages
    assert standings.messages[OLD_STANDINGS].content != "provisional standings"


async def test_each_later_round_s_posted_standings_are_posted_again(tmp_path):
    """What `repost_subsequent_standings` held: a later round whose standings were posted is
    posted again; one never posted is not."""
    league = await _league(tmp_path)
    await _ask(league, later_rounds=True)
    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    later = [row for row in await step_rows(league.db_path)
             if row["name"] == "post_standings"
             and row["payload"] and json.loads(row["payload"]).get("round_id") == LATER_ROUND_ID]
    unposted = [row for row in await step_rows(league.db_path)
                if row["name"] == "post_standings"
                and row["payload"]
                and json.loads(row["payload"]).get("round_id") == UNPOSTED_ROUND_ID]
    assert later and all(row["done_at"] is not None for row in later)
    assert unposted == []
    assert ("edit", STANDINGS_CHANNEL, OLD_LATER_STANDINGS) in league.events


EARLIER_ROUND_ID = 19
LAST_ROUND_ID = 18
OLD_EARLIER_STANDINGS = 8803
OLD_LAST_STANDINGS = 8805
TEAMS_ONLY_STANDINGS = 8806


async def _seed_rounds_either_side(league: _League) -> None:
    """Round 2 at Bahrain, before round 3, with its standings posted; round 7 at Suzuka, after
    round 4 but stored before it (a lower id), with its standings posted."""
    async with get_connection(league.db_path) as db:
        for round_id, number, track, message_id in (
            (EARLIER_ROUND_ID, 2, "Bahrain", OLD_EARLIER_STANDINGS),
            (LAST_ROUND_ID, 7, "Suzuka", OLD_LAST_STANDINGS),
        ):
            await db.execute(
                "INSERT INTO rounds (id, division_id, round_number, scheduled_at, format, "
                "track_name, status) VALUES (?, ?, ?, '2026-02-01T18:00:00+00:00', 'NORMAL', "
                "?, 'FINAL')",
                (round_id, DIVISION_ID, number, track),
            )
            await db.execute(
                "INSERT INTO driver_standings_snapshots (round_id, division_id, driver_user_id, "
                "standing_position, total_points, standings_message_id, standings_message_ids) "
                "VALUES (?, ?, 1001, 1, 25, ?, ?)",
                (round_id, DIVISION_ID, message_id, json.dumps([message_id])),
            )
        await db.commit()
    league.channel(STANDINGS_CHANNEL).seed(OLD_EARLIER_STANDINGS, "round 2 standings")
    league.channel(STANDINGS_CHANNEL).seed(OLD_LAST_STANDINGS, "round 7 standings")


async def _standings_rounds(league: _League) -> list[int]:
    """The rounds the `post_standings` jobs posted, each once, in the order the jobs ran."""
    rounds: list[int] = []
    for row in await step_rows(league.db_path):
        if row["name"] != "post_standings" or not row["payload"]:
            continue
        round_id = json.loads(row["payload"]).get("round_id")
        if round_id not in rounds:
            rounds.append(round_id)
    return rounds


async def test_later_rounds_standings_are_posted_again_in_round_order(tmp_path):
    """What `repost_subsequent_standings` held: a championship posted out of order reads as
    though the season ran that way, so the rounds go in round order, not in stored order."""
    league = await _league(tmp_path)
    await _seed_rounds_either_side(league)
    await _ask(league, later_rounds=True)
    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    assert await _standings_rounds(league) == [ROUND_ID, LATER_ROUND_ID, LAST_ROUND_ID]


async def test_an_earlier_round_s_standings_are_not_posted_again(tmp_path):
    """What `repost_subsequent_standings` held: a correction changes no round before it."""
    league = await _league(tmp_path)
    await _seed_rounds_either_side(league)
    await _ask(league, later_rounds=True)
    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    assert EARLIER_ROUND_ID not in await _standings_rounds(league)
    standings = league.channel(STANDINGS_CHANNEL)
    assert standings.messages[OLD_EARLIER_STANDINGS].content == "round 2 standings"
    assert OLD_EARLIER_STANDINGS not in league.deleted_in(STANDINGS_CHANNEL)


CANCELLED_ROUND_ID = 17
OLD_CANCELLED_STANDINGS = 8807


async def test_a_cancelled_later_round_s_standings_are_not_posted_again(tmp_path):
    """What `repost_subsequent_standings` held (`test_a_cancelled_round_is_skipped`, D135): a
    round cancelled after its standings were posted is not part of the championship, so a
    first-pass approval of an earlier round republishes no standings for it, and its old message
    is left alone."""
    league = await _league(tmp_path)
    async with get_connection(league.db_path) as db:
        await db.execute(
            "INSERT INTO rounds (id, division_id, round_number, scheduled_at, format, "
            "track_name, status) VALUES (?, ?, 6, '2026-02-01T18:00:00+00:00', 'NORMAL', "
            "'Silverstone', 'CANCELLED')",
            (CANCELLED_ROUND_ID, DIVISION_ID),
        )
        await db.execute(
            "INSERT INTO driver_standings_snapshots (round_id, division_id, driver_user_id, "
            "standing_position, total_points, standings_message_id, standings_message_ids) "
            "VALUES (?, ?, 1001, 1, 25, ?, ?)",
            (CANCELLED_ROUND_ID, DIVISION_ID, OLD_CANCELLED_STANDINGS,
             json.dumps([OLD_CANCELLED_STANDINGS])),
        )
        await db.commit()
    league.channel(STANDINGS_CHANNEL).seed(OLD_CANCELLED_STANDINGS, "round 6 standings")
    await _ask(league, later_rounds=True)
    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    assert CANCELLED_ROUND_ID not in await _standings_rounds(league)
    assert LATER_ROUND_ID in await _standings_rounds(league)
    standings = league.channel(STANDINGS_CHANNEL)
    assert standings.messages[OLD_CANCELLED_STANDINGS].content == "round 6 standings"
    assert OLD_CANCELLED_STANDINGS not in league.deleted_in(STANDINGS_CHANNEL)


async def test_a_later_round_with_only_its_team_standings_posted_is_posted_again(tmp_path):
    """What `repost_subsequent_standings` held: "posted" is either championship, not the
    drivers' alone, since the picture can leave the two in different states."""
    league = await _league(tmp_path)
    async with get_connection(league.db_path) as db:
        await db.execute(
            "UPDATE driver_standings_snapshots SET standings_message_id = NULL, "
            "standings_message_ids = NULL, constructor_standings_message_id = ?, "
            "constructor_standings_message_ids = ? WHERE round_id = ?",
            (TEAMS_ONLY_STANDINGS, json.dumps([TEAMS_ONLY_STANDINGS]), LATER_ROUND_ID),
        )
        await db.commit()
    league.channel(STANDINGS_CHANNEL).seed(TEAMS_ONLY_STANDINGS, "round 4 team standings")
    await _ask(league, later_rounds=True)
    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    assert LATER_ROUND_ID in await _standings_rounds(league)


async def test_each_later_round_s_standings_are_headed_with_its_own_round_number(tmp_path):
    """What `repost_subsequent_standings` held: every round posted again is headed with its own
    number, and one headed with the corrected round's would title the season after one race.
    The text heading carries the round's number; its track goes to the picture alone."""
    import re

    league = await _league(tmp_path)
    await _seed_rounds_either_side(league)
    await _ask(league, later_rounds=True)
    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    standings = league.channel(STANDINGS_CHANNEL)
    touched = [mid for kind, ch, mid in league.events
               if ch == STANDINGS_CHANNEL and kind in ("send", "edit")
               and mid in standings.messages]
    contents = [standings.messages[mid].content or "" for mid in touched]
    headed = {int(number) for content in contents
              for number in re.findall(r"Pro Round (\d+) ", content)}
    assert headed == {3, 4, 7}


# ---------------------------------------------------------------------------
# What a republished table shows
# ---------------------------------------------------------------------------


async def _seed_classification(
    db_path: str, *, round_format: str = "NORMAL", reserves: int | None = 1,
) -> None:
    """Round 3's Feature Race won by driver 1001 for Team 3001, scored 18 points with the
    fastest-lap point stored apart; driver 1002 seated in the division's reserve team and second
    in round 3's standings. *reserves* is the division's reserve setting, or None where the
    division never set it."""
    from tests.support.teams import seed_team_instances

    async with get_connection(db_path) as db:
        await db.execute("UPDATE rounds SET format = ? WHERE id = ?", (round_format, ROUND_ID))
        await seed_team_instances(db, DIVISION_ID, 3001)
        await db.execute(
            "INSERT INTO team_instances (id, division_id, name, full_name, max_seats, is_reserve) "
            "VALUES (3002, ?, 'Reserve', 'Reserve', 2, 1)",
            (DIVISION_ID,),
        )
        await db.execute(
            "INSERT INTO driver_profiles (id, discord_user_id, current_state) "
            "VALUES (52, '1002', 'ASSIGNED')"
        )
        await db.execute(
            "INSERT INTO team_seats (team_instance_id, seat_number, driver_profile_id) "
            "VALUES (3002, 1, 52)"
        )
        session_id = (await (await db.execute(
            "SELECT id FROM session_results WHERE round_id = ?", (ROUND_ID,),
        )).fetchone())["id"]
        await db.execute(
            "INSERT INTO race_session_results (session_result_id, driver_user_id, "
            "team_instance_id, finishing_position, outcome, base_time_ms, fastest_lap, "
            "fastest_lap_bonus, points_awarded) "
            "VALUES (?, 1001, 3001, 1, 'CLASSIFIED', 3600000, '1:30.000', 1, 18)",
            (session_id,),
        )
        await db.execute(
            "INSERT INTO driver_standings_snapshots (round_id, division_id, driver_user_id, "
            "standing_position, total_points, standings_message_id, standings_message_ids) "
            "VALUES (?, ?, 1002, 2, 10, ?, ?)",
            (ROUND_ID, DIVISION_ID, OLD_STANDINGS, json.dumps([OLD_STANDINGS])),
        )
        if reserves is None:
            await db.execute(
                "DELETE FROM division_results_config WHERE division_id = ?", (DIVISION_ID,),
            )
            await db.execute(
                "INSERT INTO division_results_config (division_id, results_channel_id, "
                "standings_channel_id) VALUES (?, ?, ?)",
                (DIVISION_ID, RESULTS_CHANNEL, STANDINGS_CHANNEL),
            )
        else:
            await db.execute(
                "UPDATE division_results_config SET reserves_in_standings = ? "
                "WHERE division_id = ?",
                (reserves, DIVISION_ID),
            )
        await db.commit()


async def _republish_with_the_picture_declined(league: _League) -> Any:
    """Republish round 3 with image generation on and the picture declined, so the results go
    out as text after the picture was asked for; give the picture's stand-in."""
    league.bot.module_service.is_images_enabled = AsyncMock(return_value=True)
    with patch(
        "leaguebot.image.services.image_results_post.try_post",
        new=AsyncMock(return_value=MagicMock(applicable=False, message_id=None)),
    ) as try_post:
        await _ask(league)
        await run_queue(league.bot)
    assert await stopped_job(league.db_path) is None
    return try_post


async def test_the_republished_results_add_the_fastest_lap_bonus_to_the_points(tmp_path):
    """What `delete_and_repost_final_results` held: the fastest-lap point is stored apart from
    the points awarded, and a table showing only one of them understates whoever set it."""
    league = await _league(tmp_path)
    await _seed_classification(league.db_path)
    try_post = await _republish_with_the_picture_declined(league)

    try_post.assert_awaited_once()
    assert try_post.await_args.kwargs["points_map"] == {1001: 19}


async def test_the_republished_results_are_headed_with_the_round_s_number_and_track(tmp_path):
    """What `delete_and_repost_final_results` held: the round is posted again under its own
    number and track."""
    league = await _league(tmp_path)
    await _seed_classification(league.db_path)
    try_post = await _republish_with_the_picture_declined(league)

    kwargs = try_post.await_args.kwargs
    assert kwargs["round_number"] == 3
    assert kwargs["race_name"] == "Silverstone"
    new = league.sent_to(RESULTS_CHANNEL)
    assert len(new) == 1
    assert "Pro Round 3" in league.channel(RESULTS_CHANNEL).messages[new[0]].content


@pytest.mark.parametrize(
    "round_format, is_sprint, heading",
    [
        pytest.param(
            "SPRINT", True, "Round 3 — Feature Race",
            id="sprint",
        ),
        pytest.param(
            "NORMAL", False, "Round 3 — Race",
            id="normal",
        ),
    ],
)
async def test_a_sprint_round_is_republished_laid_out_as_one(
    tmp_path, round_format, is_sprint, heading,
):
    """What `delete_and_repost_final_results` held: the layout is read from the round's format,
    not inferred from the sessions present; a normal round's race is headed "Race"."""
    league = await _league(tmp_path)
    await _seed_classification(league.db_path, round_format=round_format)
    try_post = await _republish_with_the_picture_declined(league)

    assert try_post.await_args.kwargs["is_sprint"] is is_sprint
    new = league.sent_to(RESULTS_CHANNEL)
    content = league.channel(RESULTS_CHANNEL).messages[new[0]].content
    assert f"{heading}**" in content


@pytest.mark.parametrize(
    "reserves, shown",
    [
        pytest.param(
            0, False,
            id="left-out",
        ),
        pytest.param(
            1, True,
            id="shown",
        ),
        pytest.param(
            None, True,
            id="never-set",
        ),
    ],
)
async def test_the_republished_standings_follow_the_division_s_reserve_setting(
    tmp_path, reserves, shown,
):
    """What `delete_and_repost_final_results` and `repost_subsequent_standings` held: a league
    that leaves reserves out of its championship does not see them come back with a
    republication, and a division that never set it shows them."""
    league = await _league(tmp_path)
    await _seed_classification(league.db_path, reserves=reserves)
    await _ask(league)
    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    standings = league.channel(STANDINGS_CHANNEL)
    text = "\n".join(
        message.content for mid, message in standings.messages.items()
        if mid != OLD_LATER_STANDINGS
    )
    assert "<@1001>" in text
    assert ("<@1002>" in text) is shown


# ---------------------------------------------------------------------------
# The batch notice
# ---------------------------------------------------------------------------


async def test_the_batch_notice_brackets_the_republication(tmp_path):
    league = await _league(tmp_path)
    await _ask(league, notice=True)
    await run_queue(league.bot)

    notices = league.sent_to(SUBMISSION_CHANNEL)
    assert len(notices) == 1
    events = league.events
    first = events.index(("send", SUBMISSION_CHANNEL, notices[0]))
    last = events.index(("delete", SUBMISSION_CHANNEL, notices[0]))
    tables = [i for i, (_kind, ch, _mid) in enumerate(events)
              if ch in (RESULTS_CHANNEL, STANDINGS_CHANNEL)]
    assert tables and first < min(tables) and max(tables) < last
    assert league.channel(SUBMISSION_CHANNEL).messages == {}


async def test_a_batch_notice_discord_refuses_stops_the_queue_like_any_job(tmp_path):
    league = await _league(tmp_path)
    league.channel(SUBMISSION_CHANNEL).send_fails = http_error(text="Discord is down")
    await _ask(league, notice=True)
    await run_queue(league.bot)

    job = await stopped_job(league.db_path)
    assert job is not None and job["name"] == "post_batch_notice"
    assert league.sent_to(RESULTS_CHANNEL) == []
    assert league.sent_to(STANDINGS_CHANNEL) == []
