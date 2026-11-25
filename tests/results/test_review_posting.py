"""The shared posting jobs a round's review republishes its tables through (#439, slice 2).

`results/services/review/review_posting.py` gives every change type of a round's review the same jobs:
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


The tests of `delete_and_repost_final_results` and `repost_subsequent_standings`, which this
module replaces, were deleted with them; what still holds of them is pinned here (a refusal leaving
the old message standing, each later round's posted standings posted again). Their delete-first
order is replaced by `test_a_replacement_is_posted_before_the_old_message_is_deleted`, and their
snapshots recomputed first by the approvals' own save (`test_report_approval_change.py`).
"""
from __future__ import annotations

import contextlib
import itertools
import json
import os
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from leaguebot.framework.db.database import get_connection, run_migrations
from leaguebot.results.services.publishing.results_post_service import (
    STANDINGS_CONSTRUCTORS,
    get_standings_message_id,
    get_standings_message_ids,
    set_standings_message_id_on,
)
from tests.support.change_queue import (
    acknowledgement,
    attach_queue,
    discard_job,
    http_error,
    league_double,
    member_interaction,
    register,
    restart_queue,
    retry_job,
    run_queue,
    seed_server,
    step_rows,
    stopped_job,
    updated_reply,
)
from tests.support.channel_uses import sign_up_channel_uses_read
from tests.support.mentions import NOBODY, USERS_ONLY, notified
from tests.support.season_league import set_module_flag
from tests.support.undecorate import undecorate
from tests.support.switches import answer_switch

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
NEW_STANDINGS_CHANNEL = 704
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
            for cid in (RESULTS_CHANNEL, STANDINGS_CHANNEL, SUBMISSION_CHANNEL, NEW_RESULTS_CHANNEL,
                        NEW_STANDINGS_CHANNEL)
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
        answer_switch(self.bot.module_service, "images", False)

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
    results_posted_in: int | None = None,
    standings_channel: int = STANDINGS_CHANNEL,
    standings_posted_in: int | None = None,
) -> str:
    """Division 11 (Pro) of season 1, its round 3 at Silverstone with one Feature Race posted and
    its standings posted, round 4's standings posted and round 5's never; a submission channel.
    Where *results_posted_in* is given, the race's table is recorded as posted in that channel,
    and where *standings_posted_in* is given, each round's posted standings in that one."""
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
            (DIVISION_ID, results_channel, standings_channel),
        )
        await db.execute(
            "INSERT INTO session_results (round_id, division_id, session_type, status, "
            "config_name, results_message_id, results_message_ids) "
            "VALUES (?, ?, 'FEATURE_RACE', 'ACTIVE', 'Standard', ?, ?)",
            (ROUND_ID, DIVISION_ID, results_message_id,
             json.dumps([results_message_id]) if results_message_id else None),
        )
        if results_posted_in is not None:
            await db.execute(
                "UPDATE session_results SET results_message_channel_id = ? WHERE round_id = ?",
                (results_posted_in, ROUND_ID),
            )
        for round_id, message_id in (
            (ROUND_ID, OLD_STANDINGS), (LATER_ROUND_ID, OLD_LATER_STANDINGS),
            (UNPOSTED_ROUND_ID, None),
        ):
            await db.execute(
                "INSERT INTO driver_standings_snapshots (round_id, division_id, driver_user_id, "
                "standing_position, total_points) VALUES (?, ?, 1001, 1, 25)",
                (round_id, DIVISION_ID),
            )
            if message_id:
                await set_standings_message_id_on(
                    db, DIVISION_ID, round_id, message_id, message_ids=json.dumps([message_id])
                )
        if standings_posted_in is not None:
            await db.execute(
                "UPDATE round_standings_posts SET standings_message_channel_id = ? "
                "WHERE standings_message_id IS NOT NULL",
                (standings_posted_in,),
            )
        await db.execute(
            "INSERT INTO round_submission_channels (round_id, channel_id, created_at) "
            "VALUES (?, ?, '2026-02-01T20:00:00+00:00')",
            (ROUND_ID, SUBMISSION_CHANNEL),
        )
        await db.commit()
    return db_path


def _posting_type(
    *, later_rounds: bool = False, notice: bool = False, label: str = LABEL,
    drawing: Any = None,
) -> Any:
    """A change whose one opening job plans the round's republication through the module, under
    *label*."""
    from leaguebot.framework.models.change import PlannedStep, StepKind, StepResult, Verdict
    from leaguebot.framework.services.queue.change_types import ChangeType, Step
    from leaguebot.framework.utils.hooks import AwaitedHook
    from leaguebot.results.services.review import review_posting

    async def plan(db: Any, ctx: Any) -> Any:
        planned = await review_posting.plan_posts(
            db, ctx.payload["round_id"], label=label, later_rounds=later_rounds, notice=notice,
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
        steps={"plan": Step("plan", StepKind.SAVE, plan), **review_posting.posting_steps(drawing, AwaitedHook())},
        check=check,
        key=lambda payload: f"{KIND}:{payload['round_id']}",
        doing=lambda _payload: "Republishing round 3",
        outcome=outcome,
    )


def _a_picture() -> Any:
    """A picture drawn for a post, as the drawing hands one back."""
    import io

    return discord.File(io.BytesIO(b"\x89PNG"), filename="results.png")


async def _league(tmp_path: Any, **make: Any) -> _League:
    return _League(await _make_db(tmp_path, **make))


def _drawing_of(league: _League) -> Any:
    """The image drawing the posting change is handed: the builder's own where the league's bot is
    the builder's, else one over the bot's services, which the double's switches answer."""
    from leaguebot.image.services.render.image_drawing import ImageDrawing

    bot = league.bot
    if isinstance(bot.image_drawing, ImageDrawing):
        return bot.image_drawing
    return ImageDrawing(
        bot.module_service, MagicMock(), MagicMock(), renderer=MagicMock(), post_log=AsyncMock(),
        db_path=league.db_path,
    )


async def _ask(league: _League, **kinds: Any) -> Any:
    """Attach a queue with the posting change alone, ask it for round 3, and give the asker's
    interaction; the queue is not yet run."""
    attach_queue(league.bot, league.db_path, now=NOW,
                 types=[_posting_type(drawing=_drawing_of(league), **kinds)])
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


async def _session_posted_in(db_path: str) -> int | None:
    """The channel round 3's Feature Race table is recorded as posted in."""
    async with get_connection(db_path) as db:
        row = await (await db.execute(
            "SELECT results_message_channel_id FROM session_results "
            "WHERE round_id = ? AND session_type = 'FEATURE_RACE'", (ROUND_ID,),
        )).fetchone()
    return row[0]


#: Each case of a results channel kept, or moved from 700 to 703 since the old table was posted:
#: (the present results channel, the channel recorded beside the old table, or None).
CHANNEL_KEPT_OR_MOVED = [
    pytest.param(RESULTS_CHANNEL, None, id="kept"),
    pytest.param(NEW_RESULTS_CHANNEL, RESULTS_CHANNEL, id="moved"),
]


async def _stop_notice(league: _League) -> str:
    job = await stopped_job(league.db_path)
    assert job is not None, "the queue is not stopped"
    return "\n".join(league.bot.log_channel.sent)


# ---------------------------------------------------------------------------
# The order: post, then delete
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("present, posted_in", CHANNEL_KEPT_OR_MOVED)
async def test_a_replacement_is_posted_before_the_old_message_is_deleted(
    tmp_path, present, posted_in,
):
    """Round 3's results table is posted again: the new table goes out in the division's present
    results channel, notifying the drivers it names, before the old one (8800) is deleted from
    channel 700, where it was posted. Where the results channel has since moved to 703, the new
    table goes to 703 and 700 is left holding nothing."""
    league = await _league(tmp_path, results_channel=present, results_posted_in=posted_in)
    await _ask(league)
    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    new = league.sent_to(present)
    assert len(new) == 1
    events = league.events
    assert events.index(("send", present, new[0])) < events.index(
        ("delete", RESULTS_CHANNEL, OLD_RESULTS)
    )
    assert set(league.channel(present).messages) == {new[0]}
    assert OLD_RESULTS not in league.channel(RESULTS_CHANNEL).messages
    [sent] = league.channel(present).send.await_args_list
    assert notified(sent.kwargs.get("allowed_mentions")) == USERS_ONLY


async def test_the_new_message_id_is_saved_with_the_post(tmp_path):
    """The new table's ids are saved with the post, and the channel it was posted in (700) beside
    them, before the old table's delete has run."""
    league = await _league(tmp_path)
    await _ask(league)
    await _run_until_done(league, "post_session_results")

    new = league.sent_to(RESULTS_CHANNEL)
    assert len(new) == 1
    assert await _session_ids(league.db_path) == (new[0], [new[0]])
    assert await _session_posted_in(league.db_path) == RESULTS_CHANNEL
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
    answer_switch(league.bot.module_service, "images", True)
    results = league.channel(RESULTS_CHANNEL)
    results.send_fails = http_error(text="Discord is down")

    with _results_picture_is_due(), patch(
        "leaguebot.image.services.render.image_drawing.ImageDrawing.draw_results",
        new=AsyncMock(return_value=(_a_picture(), ())),
    ) as try_post:
        await _ask(league)
        await run_queue(league.bot)
        job = await stopped_job(league.db_path)
        assert job is not None and job["name"] == "post_session_results"
        assert OLD_RESULTS in results.messages

        results.send_fails = None
        await retry_job(league.bot)

    assert try_post.await_count == 1
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
    body and the real season service, with results on and results' channel setting registered on
    the queue as the builder registers it; the queue is not run. Gives Alex's interaction."""
    from leaguebot.__main__ import register_change_types
    from leaguebot.core.services.seasons.season_service import SeasonService
    from leaguebot.results.cogs.results_cog import ResultsCog

    await set_module_flag(league.db_path, "results", True)
    answer_switch(league.bot.module_service, "results", True)
    league.bot.season_service = SeasonService(league.db_path)
    builder = MagicMock()
    builder.module_service = league.bot.module_service
    builder.season_service = league.bot.season_service
    builder.get_channel = league.bot.get_channel
    # The registry asks each module for the channels it holds, through the bot's own hook, which
    # the builder hands the setting.
    sign_up_channel_uses_read(league.bot)
    builder.channel_uses_read = league.bot.channel_uses_read
    # The setting asks the drawing whether the channel's picture could attach a file.
    builder.image_drawing = _drawing_of(league)
    register_change_types(builder)
    built = {call.args[0].kind: call.args[0]
             for call in builder.change_queue.register.call_args_list}
    register(league.bot, built["results.setting.results_channel"])
    cog = ResultsCog.__new__(ResultsCog)
    cog.bot = league.bot
    interaction = member_interaction(league.bot)
    interaction.user.display_name = "Alex"
    await undecorate(ResultsCog.channel_results)(cog, interaction, "Pro",
                                                 league.channel(channel_id))
    return interaction


async def _results_channel(db_path: str) -> int | None:
    async with get_connection(db_path) as db:
        row = await (await db.execute(
            "SELECT results_channel_id FROM division_results_config WHERE division_id = ?",
            (DIVISION_ID,),
        )).fetchone()
    return None if row is None or row[0] is None else int(row[0])


async def test_a_stopped_post_lands_once_the_channel_is_set_and_retried(tmp_path):
    """Set the channel, then Retry: the remedy the stop notice leads a manager to, end to end.

    Pro's round 3 results are republished, and the post stops at `post_session_results`, its
    results channel gone. Alex runs `/results channel results` for Pro with channel 703 and is
    acknowledged; the queue runs. The setting does not wait behind the stopped post: it runs past
    it, saves 703 as Pro's results channel and tells Alex the channel was updated, while the post
    still stops the league's lane. Alex presses Retry: the results land once in 703, the session
    records that message, and nothing stops the queue."""
    league = await _league(tmp_path, results_message_id=None)
    league.gone.add(RESULTS_CHANNEL)
    await _ask(league)
    await run_queue(league.bot)
    job = await stopped_job(league.db_path)
    assert job is not None and job["name"] == "post_session_results"

    manager = await _set_results_channel(league, NEW_RESULTS_CHANNEL)
    assert acknowledgement(manager).startswith("⏳ ")
    await run_queue(league.bot)

    assert "updated to" in updated_reply(manager)
    assert await _results_channel(league.db_path) == NEW_RESULTS_CHANNEL
    job = await stopped_job(league.db_path)
    assert job is not None and job["name"] == "post_session_results", "setting the channel ran the job"
    await retry_job(league.bot)

    new = league.sent_to(NEW_RESULTS_CHANNEL)
    assert len(new) == 1
    assert await _session_ids(league.db_path) == (new[0], [new[0]])
    assert await stopped_job(league.db_path) is None


async def test_a_post_that_fails_part_way_removes_what_it_sent_before_its_next_try(tmp_path):
    league = await _league(tmp_path, results_message_id=None)
    results = league.channel(RESULTS_CHANNEL)
    results.fail_send_at = 2
    results.delete_fails = http_error(text="Discord is down")
    with patch("leaguebot.results.services.publishing.results_post_service._MSG_MAX", 40):
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
    # Besides the retry's own messages the channel holds only the unrelated one every test seeds.
    assert standing == set(ids) | {OLD_RESULTS}


async def test_a_discarded_part_posted_table_names_its_stranded_messages(tmp_path):
    """The results post sends its first chunk and Discord refuses the second; the chunk sent
    cannot be removed, nor on the retry. A league admin discards the job: the outcome names the
    message left standing and its channel for deletion by hand, beside the line naming the sync
    commands, as nothing else will remove it."""
    league = await _league(tmp_path, results_message_id=None)
    results = league.channel(RESULTS_CHANNEL)
    results.fail_send_at = 2
    results.delete_fails = http_error(text="Discord is down")
    with patch("leaguebot.results.services.publishing.results_post_service._MSG_MAX", 40):
        interaction = await _ask(league)
        await run_queue(league.bot)
        assert (await stopped_job(league.db_path))["name"] == "post_session_results"
        stranded = league.sent_to(RESULTS_CHANNEL)
        assert stranded and set(stranded) <= set(results.messages)

        await retry_job(league.bot)
        assert (await stopped_job(league.db_path))["name"] == "post_session_results"
        await discard_job(league.bot)

    assert await stopped_job(league.db_path) is None
    assert set(stranded) <= set(results.messages)
    reply = updated_reply(interaction)
    [line] = [line for line in reply.splitlines() if "delete it by hand" in line]
    assert f"<#{RESULTS_CHANNEL}>" in line
    assert all(str(message_id) in line for message_id in stranded)
    assert "/results rounds sync" in reply


#: Round 3's constructors' standings, posted as a text table of their own beside the drivers'.
OLD_CONSTRUCTORS_TABLE = 8809


async def _standings_as_two_tables(league: _League) -> None:
    """Round 3's standings stand as one text table per championship, each with its id recorded:
    the drivers' as `OLD_STANDINGS`, the constructors' as `OLD_CONSTRUCTORS_TABLE`."""
    async with get_connection(league.db_path) as db:
        await set_standings_message_id_on(
            db, DIVISION_ID, ROUND_ID, OLD_CONSTRUCTORS_TABLE, STANDINGS_CONSTRUCTORS,
            message_ids=json.dumps([OLD_CONSTRUCTORS_TABLE]),
        )
        await db.commit()
    league.channel(STANDINGS_CHANNEL).seed(OLD_CONSTRUCTORS_TABLE, "constructors' standings")


def _each_championship_as_text() -> Any:
    """The picture applies to the standings and draws neither championship, so each is posted
    as a text table of its own."""
    from leaguebot.image.services.aspects.image_standings_post import StandingsDrawn

    stack = contextlib.ExitStack()
    stack.enter_context(_pictures_are_due("standings"))
    stack.enter_context(patch(
        "leaguebot.image.services.render.image_drawing.ImageDrawing.draw_standings",
        new=AsyncMock(return_value=StandingsDrawn(applicable=True)),
    ))
    return stack


async def test_a_standings_post_that_fails_on_its_second_table_removes_the_first_before_its_next_try(
    tmp_path,
):
    """Round 3's standings are posted as one text table per championship. Discord takes the
    drivers' table and refuses the constructors', once: the queue stops, and the drivers' table
    the failed try sent, recorded nowhere, is deleted before the retry posts. The retry goes out
    as text, one table carrying both championships, so the channel is left with that table alone,
    its id recorded, and no drivers' table besides it."""
    league = await _league(tmp_path)
    await _standings_as_two_tables(league)
    standings = league.channel(STANDINGS_CHANNEL)
    standings.fail_send_at = 2
    with _each_championship_as_text():
        await _ask(league)
        await run_queue(league.bot)
        assert (await stopped_job(league.db_path))["name"] == "post_standings"
        stray = league.sent_to(STANDINGS_CHANNEL)
        assert len(stray) == 1 and stray[0] in standings.messages

        standings.fail_send_at = None
        await retry_job(league.bot)

    assert await stopped_job(league.db_path) is None
    posted = [mid for mid in league.sent_to(STANDINGS_CHANNEL) if mid not in stray]
    assert posted
    assert stray[0] not in standings.messages
    assert league.events.index(("delete", STANDINGS_CHANNEL, stray[0])) < league.events.index(
        ("send", STANDINGS_CHANNEL, posted[0])
    )
    # Besides the retry's table the channel holds only round 4's, which this round leaves alone.
    assert set(standings.messages) == set(posted) | {OLD_LATER_STANDINGS}
    assert await get_standings_message_ids(league.db_path, DIVISION_ID, ROUND_ID) == posted
    assert await get_standings_message_id(
        league.db_path, DIVISION_ID, ROUND_ID, STANDINGS_CONSTRUCTORS
    ) is None


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


OLD_CANCELLED_LINE = 8810


async def _cancel_round_3_s_qualifying(
    league: _League, *, posted_as: int | None = None, posted_in: int | None = None,
) -> str:
    """Round 3's Feature Qualifying was submitted as CANCELLED; where *posted_as* is given its
    line already stands in the results channel (700) as that message, recorded as the session's,
    and where *posted_in* is given, recorded as posted in that channel. Gives the session's label
    as a league reads it."""
    from leaguebot.core.models.round import RoundFormat
    from leaguebot.results.services.submission.result_submission_service import get_sessions_for_format
    from leaguebot.results.utils import results_formatter

    async with get_connection(league.db_path) as db:
        await db.execute(
            "INSERT INTO session_results (round_id, division_id, session_type, status, "
            "results_message_id, results_message_ids) "
            "VALUES (?, ?, 'FEATURE_QUALIFYING', 'CANCELLED', ?, ?)",
            (ROUND_ID, DIVISION_ID, posted_as,
             json.dumps([posted_as]) if posted_as else None),
        )
        if posted_in is not None:
            await db.execute(
                "UPDATE session_results SET results_message_channel_id = ? "
                "WHERE round_id = ? AND session_type = 'FEATURE_QUALIFYING'",
                (posted_in, ROUND_ID),
            )
        await db.commit()
    if posted_as is not None:
        league.channel(RESULTS_CHANNEL).seed(
            posted_as, "🚫 Round 3 — Qualifying (Pro): This session was cancelled.")
    qualifying = get_sessions_for_format(RoundFormat.NORMAL)[0]
    return results_formatter.format_session_label(qualifying, is_sprint=False)


async def _qualifying_ids(db_path: str) -> tuple[int | None, list[int] | None]:
    async with get_connection(db_path) as db:
        row = await (await db.execute(
            "SELECT results_message_id, results_message_ids FROM session_results "
            "WHERE round_id = ? AND session_type = 'FEATURE_QUALIFYING'", (ROUND_ID,),
        )).fetchone()
    ids = json.loads(row["results_message_ids"]) if row["results_message_ids"] else None
    return row["results_message_id"], ids


@pytest.mark.parametrize(
    "label", [LABEL, "Provisional Results (amended)"], ids=["first-posting", "resubmission"]
)
async def test_a_cancelled_session_s_line_is_posted_in_its_place_among_the_provisional_results_and_recorded(
    tmp_path, label,
):
    """Round 3's Feature Qualifying was cancelled and its Feature Race submitted. When the
    round's tables are posted, the results channel gets the qualifying's line, "Round 3 —
    Qualifying: This session was cancelled.", in the qualifying's place, before the race's table;
    the line's message is recorded as the qualifying's results message, so a later posting
    replaces it.

    A resubmission of round 3 entering its Feature Qualifying as cancelled and its Feature Race
    again leaves the qualifying's row cancelled with no message recorded, and its tables are
    posted as "Provisional Results (amended)"; the line is posted and recorded the same way."""
    league = await _league(tmp_path)
    session_label = await _cancel_round_3_s_qualifying(league)

    await _ask(league, label=label)
    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    line, table = league.sent_to(RESULTS_CHANNEL)
    content = league.channel(RESULTS_CHANNEL).messages[line].content
    assert "This session was cancelled" in content
    assert "Round 3" in content and session_label in content
    assert "This session was cancelled" not in league.channel(RESULTS_CHANNEL).messages[table].content
    assert await _qualifying_ids(league.db_path) == (line, [line])


@pytest.mark.parametrize("present, posted_in", CHANNEL_KEPT_OR_MOVED)
async def test_a_cancelled_session_s_line_stands_in_its_place_when_the_tables_are_posted_at_the_appeals_stage(
    tmp_path, present, posted_in,
):
    """Round 3's cancelled qualifying line already stands in channel 700 (message 8810, recorded
    as the qualifying's) when the round's tables are posted again, as the appeals approval and a
    points amendment post them. A new line is posted in the qualifying's place in the present
    results channel, before the race's table, and recorded; the old line is deleted from 700 after
    it. Where the results channel has since moved to 703, the new line goes to 703."""
    league = await _league(tmp_path, results_channel=present, results_posted_in=posted_in)
    await _cancel_round_3_s_qualifying(league, posted_as=OLD_CANCELLED_LINE, posted_in=posted_in)

    await _ask(league)
    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    line, _table = league.sent_to(present)
    assert "This session was cancelled" in league.channel(present).messages[line].content
    assert await _qualifying_ids(league.db_path) == (line, [line])
    assert OLD_CANCELLED_LINE in league.deleted_in(RESULTS_CHANNEL)
    assert league.events.index(("send", present, line)) < league.events.index(
        ("delete", RESULTS_CHANNEL, OLD_CANCELLED_LINE))


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
    """Round 3's standings (8801) still fit one message: they are edited in place, notifying
    the drivers it names, and nothing is posted or deleted."""
    league = await _league(tmp_path)
    await _ask(league)
    await run_queue(league.bot)

    standings = league.channel(STANDINGS_CHANNEL)
    assert ("edit", STANDINGS_CHANNEL, OLD_STANDINGS) in league.events
    assert league.sent_to(STANDINGS_CHANNEL) == []
    assert league.deleted_in(STANDINGS_CHANNEL) == []
    assert OLD_STANDINGS in standings.messages
    assert standings.messages[OLD_STANDINGS].content != "provisional standings"
    edit = standings.messages[OLD_STANDINGS].edit.await_args
    assert notified(edit.kwargs.get("allowed_mentions")) == USERS_ONLY


async def test_a_refused_standings_edit_stops_the_queue_and_posts_no_second_table(tmp_path):
    """Round 3's standings stand as 8801, short enough to be edited in place, and Discord refuses
    the edit (403). The queue stops at `post_standings`: no second table is sent, 8801 stands with
    its old text, and the round's saved standings id is still 8801."""
    league = await _league(tmp_path)
    standings = league.channel(STANDINGS_CHANNEL)
    standings.messages[OLD_STANDINGS].edit = AsyncMock(
        side_effect=http_error(discord.Forbidden, status=403, text="Missing Permissions")
    )
    await _ask(league)
    await run_queue(league.bot)

    job = await stopped_job(league.db_path)
    assert job is not None and job["name"] == "post_standings"
    assert league.sent_to(STANDINGS_CHANNEL) == []
    assert standings.messages[OLD_STANDINGS].content == "provisional standings"
    assert (await _standings_ids(league.db_path))[0] == OLD_STANDINGS


#: Round 3's interim standings as the picture posted them: a graphic per championship.
DRIVERS_GRAPHIC = 8807
CONSTRUCTORS_GRAPHIC = 8808


async def test_a_text_repost_over_two_graphics_takes_both_down(tmp_path):
    """Round 3's interim standings stand as two graphics, the drivers' and the constructors',
    each with its id recorded. The republication's first try is refused and its retry goes out
    as text, one table carrying both championships: it is posted afresh rather than edited into
    the drivers' graphic, which would keep its picture attached, and both graphics come down, the
    constructors' id forgotten with nothing new to carry it."""
    league = await _league(tmp_path)
    async with get_connection(league.db_path) as db:
        await set_standings_message_id_on(
            db, DIVISION_ID, ROUND_ID, DRIVERS_GRAPHIC,
            message_ids=json.dumps([DRIVERS_GRAPHIC]),
        )
        await set_standings_message_id_on(
            db, DIVISION_ID, ROUND_ID, CONSTRUCTORS_GRAPHIC, STANDINGS_CONSTRUCTORS,
            message_ids=json.dumps([CONSTRUCTORS_GRAPHIC]),
        )
        await db.commit()
    standings = league.channel(STANDINGS_CHANNEL)
    standings.seed(DRIVERS_GRAPHIC, "drivers' standings graphic")
    standings.seed(CONSTRUCTORS_GRAPHIC, "constructors' standings graphic")
    standings.fail_send_at = 1
    await _ask(league)
    await run_queue(league.bot)
    job = await stopped_job(league.db_path)
    assert job is not None and job["name"] == "post_standings", (
        "the standings were not posted afresh, so Discord had no send to refuse"
    )

    await retry_job(league.bot)

    assert await stopped_job(league.db_path) is None
    new = league.sent_to(STANDINGS_CHANNEL)
    assert new
    assert ("edit", STANDINGS_CHANNEL, DRIVERS_GRAPHIC) not in league.events
    assert {DRIVERS_GRAPHIC, CONSTRUCTORS_GRAPHIC} <= set(league.deleted_in(STANDINGS_CHANNEL))
    assert not {DRIVERS_GRAPHIC, CONSTRUCTORS_GRAPHIC} & set(standings.messages)
    assert await get_standings_message_id(league.db_path, DIVISION_ID, ROUND_ID) == new[0]
    assert await get_standings_message_id(
        league.db_path, DIVISION_ID, ROUND_ID, STANDINGS_CONSTRUCTORS
    ) is None
    assert await get_standings_message_ids(
        league.db_path, DIVISION_ID, ROUND_ID, STANDINGS_CONSTRUCTORS
    ) is None


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
                "standing_position, total_points) VALUES (?, ?, 1001, 1, 25)",
                (round_id, DIVISION_ID),
            )
            await set_standings_message_id_on(
                db, DIVISION_ID, round_id, message_id, message_ids=json.dumps([message_id])
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
            "standing_position, total_points) VALUES (?, ?, 1001, 1, 25)",
            (CANCELLED_ROUND_ID, DIVISION_ID),
        )
        await set_standings_message_id_on(
            db, DIVISION_ID, CANCELLED_ROUND_ID, OLD_CANCELLED_STANDINGS,
            message_ids=json.dumps([OLD_CANCELLED_STANDINGS]),
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
        await set_standings_message_id_on(db, DIVISION_ID, LATER_ROUND_ID, None)
        await set_standings_message_id_on(
            db, DIVISION_ID, LATER_ROUND_ID, TEAMS_ONLY_STANDINGS, STANDINGS_CONSTRUCTORS,
            message_ids=json.dumps([TEAMS_ONLY_STANDINGS]),
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
            "standing_position, total_points) VALUES (?, ?, 1002, 2, 10)",
            (ROUND_ID, DIVISION_ID),
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



@contextlib.contextmanager
def _pictures_are_due(*aspects: str):
    """The pictures of *aspects* ("results", "standings") asked for and their drawings handed
    over, for a test that stubs the image drawing's `draw_results` or `draw_standings` itself.

    The results module asks the gate (`aspect_wanted`) and composes the drawing before it hands
    it over, so a stub is reached only where both answer: the gate yes for the aspect, and the
    composed drawing a stand-in, as the stubbed draw ignores it. Yields the results composer's
    stand-in.
    """
    with patch(
        "leaguebot.image.services.render.image_drawing.ImageDrawing.aspect_wanted",
        new=AsyncMock(side_effect=lambda aspect, template_key=None: aspect in aspects),
    ), patch(
        "leaguebot.results.services.publishing.results_drawing_composer.build_drawing",
        new=AsyncMock(return_value=MagicMock()),
    ) as composed, patch(
        "leaguebot.results.services.publishing.standings_drawing_composer.build_drawings",
        new=AsyncMock(return_value=(MagicMock(), MagicMock())),
    ):
        yield composed


def _results_picture_is_due():
    """The results picture asked for and its drawing handed over (`_pictures_are_due`)."""
    return _pictures_are_due("results")


async def _republish_with_the_picture_declined(league: _League) -> Any:
    """Republish round 3 with image generation on and the picture declined, so the results go
    out as text after the picture was asked for; give `build_drawing`'s stand-in, which the
    results module composed the drawing with and which the session's rows and heading reach."""
    answer_switch(league.bot.module_service, "images", True)
    with _results_picture_is_due() as composed, patch(
        "leaguebot.image.services.render.image_drawing.ImageDrawing.draw_results",
        new=AsyncMock(return_value=(None, ())),
    ):
        await _ask(league)
        await run_queue(league.bot)
    assert await stopped_job(league.db_path) is None
    return composed


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


async def test_the_drawing_notice_notifies_nobody(tmp_path):
    """A round's results are posted again with the drawing notice. The notice in the round's
    results submission channel notifies nobody."""
    league = await _league(tmp_path)
    await _ask(league, notice=True)
    await run_queue(league.bot)

    [call] = league.channel(SUBMISSION_CHANNEL).send.await_args_list
    assert notified(call.kwargs.get("allowed_mentions")) == NOBODY


async def test_a_batch_notice_discord_refuses_stops_the_queue_like_any_job(tmp_path):
    league = await _league(tmp_path)
    league.channel(SUBMISSION_CHANNEL).send_fails = http_error(text="Discord is down")
    await _ask(league, notice=True)
    await run_queue(league.bot)

    job = await stopped_job(league.db_path)
    assert job is not None and job["name"] == "post_batch_notice"
    assert league.sent_to(RESULTS_CHANNEL) == []
    assert league.sent_to(STANDINGS_CHANNEL) == []


# ---------------------------------------------------------------------------
# The drivers' display names
# ---------------------------------------------------------------------------


async def test_names_go_unresolved_where_the_server_is_not_in_the_cache(tmp_path):
    """The names only order a full tie, so a server the bot cannot see leaves them unresolved
    rather than stopping the queue: a post after the job stops it on the same fault."""
    from leaguebot.framework.models.change import PlannedStep, Verdict
    from leaguebot.framework.services.queue.change_types import ChangeType
    from leaguebot.results.services.review import review_posting

    league = await _league(tmp_path)
    league.bot.get_guild = MagicMock(return_value=None)
    league.bot.guilds = []

    async def check(_ctx: Any) -> Any:
        return Verdict.go()

    attach_queue(league.bot, league.db_path, now=NOW, types=[ChangeType(
        kind=KIND,
        opening=(PlannedStep(review_posting.NAMES),),
        steps=review_posting.posting_steps(),
        check=check,
        key=lambda payload: f"{KIND}:{payload['round_id']}",
        doing=lambda _payload: "Looking up the names",
        outcome=lambda _ctx: "Looked up.",
    )])
    await league.bot.change_queue.ask(
        KIND, {"round_id": ROUND_ID}, interaction=member_interaction(league.bot),
        what="the test's names",
    )
    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    [names] = [row for row in await step_rows(league.db_path) if row["name"] == "names"]
    assert names["done_at"] is not None
    assert names["result"] == {"names": None}


# ---------------------------------------------------------------------------
# Every division at once: a points amendment's approval (#439, slice 3)
#
# The approval reposts every division of the season, so it resolves one `names` job per division
# and reads each back by its division, and plans each division's posts carrying the division's
# name, which a discarded post's line then gives ("in Pro"). Slice 2's callers pass neither and
# read exactly as before.
# ---------------------------------------------------------------------------

def _outcome_context(*steps: Any) -> Any:
    from leaguebot.framework.services.queue.change_types import OutcomeContext

    return OutcomeContext(
        bot=MagicMock(), db_path="unused.db", change_id=1, kind=KIND, payload={},
        what="the test's approval", actor_id=77, actor_name="Admin", named="Admin (<@77>)",
        steps=tuple(steps),
    )


async def test_names_are_read_back_by_division():
    """Pro's (11) and Am's (12) `names` jobs both done, Beta's (13) not yet: asked for a division,
    the names of that division's job; for one whose job is not done, none; asked for no division,
    the first done, as slice 2's callers read it."""
    from leaguebot.framework.services.queue.change_types import StepView
    from leaguebot.results.services.review import review_posting

    ctx = _outcome_context(
        StepView(review_posting.NAMES, {"division_id": 11}, {"names": {"101": "Lewis"}}, True),
        StepView(review_posting.NAMES, {"division_id": 12}, {"names": {"201": "Charles"}}, True),
        StepView(review_posting.NAMES, {"division_id": 13}, None, False),
    )

    assert review_posting.display_names(ctx, 12) == {201: "Charles"}
    assert review_posting.display_names(ctx, 11) == {101: "Lewis"}
    assert review_posting.display_names(ctx, 13) is None
    assert review_posting.display_names(ctx) == {101: "Lewis"}


async def test_a_post_planned_with_its_division_names_it_when_discarded(tmp_path):
    """Pro's posts planned with its name: every results and standings job carries it, and each
    one a league admin discards is named with the division ("Round 3's Feature Race results in
    Pro were not posted."), the sync commands last. Planned with no name, as slice 2's rebuild
    plans them, the lines are today's."""
    from leaguebot.framework.services.queue.change_types import StepView
    from leaguebot.results.services.review import review_posting

    db_path = await _make_db(tmp_path)
    async with get_connection(db_path) as db:
        named = await review_posting.plan_division_posts(db, DIVISION_ID, division_name="Pro")
        unnamed = await review_posting.plan_division_posts(db, DIVISION_ID)

    posts = (review_posting.POST_SESSION_RESULTS, review_posting.POST_STANDINGS)
    assert [step.name for step in named] == list(posts)
    assert all(step.payload["division"] == "Pro" for step in named)
    assert all("division" not in step.payload for step in unnamed)

    def discarded(planned: list[Any]) -> list[str]:
        return review_posting.not_done(_outcome_context(*(
            StepView(step.name, step.payload, {"discarded": {"by": 77, "at": NOW.isoformat()}},
                     True)
            for step in planned
        )))

    assert discarded(named) == [
        "⚠️ Round 3's Feature Race results in Pro were not posted.",
        "⚠️ Round 3's standings in Pro were not posted.",
        "Repair the cause, then run `/results rounds sync` and `/results standings sync`.",
    ]
    assert discarded(unnamed) == [
        "⚠️ Round 3's Feature Race results were not posted.",
        "⚠️ Round 3's standings were not posted.",
        "Repair the cause, then run `/results rounds sync` and `/results standings sync`.",
    ]


# ---------------------------------------------------------------------------
# A table drawn as a picture is saved and replaced by the jobs, as a text table is
# ---------------------------------------------------------------------------

async def _built_league(tmp_path: Any, **make: Any) -> _League:
    """Round 3's league of `_league`, made as *make* asks, its bot the one the entry point's
    `build` makes, every service the builder's (the image module's among them), over the same
    channels and server."""
    from tests.support.change_queue import league_bot, set_guilds

    league = await _league(tmp_path, **make)
    double, bot = league.bot, await league_bot(league.db_path)
    bot.log_channel, bot.interaction_channel = double.log_channel, double.interaction_channel
    bot.get_channel, bot.fetch_channel = double.get_channel, double.fetch_channel
    bot.get_guild = double.get_guild
    set_guilds(bot, [league.guild])
    league.bot = bot
    return league


async def _drawn(league: _League, tmp_path: Any, picture: str, *,
                 championships: tuple[str, ...] = ("drivers", "constructors"),
                 notice: str | None = None) -> Any:
    """Image generation on for *picture* ("results" or "standings"), as the league stores it: the
    module switched on with its default configuration and its packaged templates, the *picture*
    aspect toggled on and every other aspect off; of the standings, each championship not among
    *championships* has its template named as a file the league does not have, so it is not
    drawn and goes out as text. Round 3's Feature Race classifies driver 1001, first for Team
    3001. *league* is `_built_league`'s: everything the builder made runs but the render, stubbed
    to give each picture a PNG and, where *notice* is given, one notice with that detail, a flag
    falling back, on the race's results or the drivers' championship alone. Gives the patch, to
    enter."""
    from leaguebot.image.models.image_module import RenderNotice, RenderOutcome
    from leaguebot.image.services.render.image_render_service import ImageRenderService
    from tests.support import saves
    from tests.support.teams import seed_team_instances

    noticed = "results_race_template" if picture == "results" else "standings_drivers_template"
    async with get_connection(league.db_path) as db:
        await seed_team_instances(db, DIVISION_ID, 3001)
        await db.execute(
            "INSERT INTO race_session_results (session_result_id, driver_user_id, "
            "team_instance_id, finishing_position, points_awarded) "
            "SELECT id, 1001, 3001, 1, 25 FROM session_results WHERE round_id = ?",
            (ROUND_ID,),
        )
        await db.commit()
    await saves.create_image_defaults(league.db_path)
    await set_module_flag(league.db_path, "images", True)
    await saves.set_aspect(league.db_path, picture, True)
    if picture == "standings":
        for name in {"drivers", "constructors"} - set(championships):
            await saves.set_field(league.db_path, f"standings_{name}_template", "missing.svg")
    renders = itertools.count(1)

    async def _render(_self: Any, image_type: str, _spec_builder: Any, **_kwargs: Any) -> Any:
        png = tmp_path / f"{image_type}_{next(renders)}.png"
        png.write_bytes(b"\x89PNG")
        notices = [] if notice is None or image_type != noticed else [RenderNotice(
            image_type=noticed, notice_kind="ASSET_FALLBACK_USED", detail=notice,
            field_id="flag",
        )]
        return RenderOutcome(png_paths=[png], notices=notices)

    return patch.object(ImageRenderService, "render", new=_render)


async def test_a_drawn_table_s_id_is_saved_with_its_post_and_the_old_table_deleted_by_a_job(
    tmp_path,
):
    """Round 3's Feature Race results stand as message 8800; they are posted again as a picture.
    Once `post_session_results` is done, the picture's id is the session's, saved with the job,
    and 8800 still stands; then a `delete_message` job deletes 8800."""
    league = await _built_league(tmp_path)
    with await _drawn(league, tmp_path, "results"):
        await _ask(league)
        await _run_until_done(league, "post_session_results")

        [new] = league.sent_to(RESULTS_CHANNEL)
        assert OLD_RESULTS in league.channel(RESULTS_CHANNEL).messages, (
            "the picture deleted the old table itself"
        )
        assert await _session_ids(league.db_path) == (new, [new])

        await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    assert set(league.channel(RESULTS_CHANNEL).messages) == {new}
    deletes = [row for row in await step_rows(league.db_path) if row["name"] == "delete_message"
               and json.loads(row["payload"])["message_id"] == OLD_RESULTS]
    assert len(deletes) == 1 and deletes[0]["done_at"] is not None


async def test_a_drawn_table_whose_old_message_will_not_delete_stops_the_queue(tmp_path):
    """Round 3's results are posted again as a picture, and Discord refuses to delete the old
    table, 8800: the queue stops at the `delete_message` job, both tables standing and the
    picture's id saved."""
    league = await _built_league(tmp_path)
    results = league.channel(RESULTS_CHANNEL)
    results.delete_fails = http_error(discord.Forbidden, status=403, text="Missing Permissions")
    with await _drawn(league, tmp_path, "results"):
        await _ask(league)
        await run_queue(league.bot)

    job = await stopped_job(league.db_path)
    assert job is not None and job["name"] == "delete_message"
    [new] = league.sent_to(RESULTS_CHANNEL)
    assert set(results.messages) == {OLD_RESULTS, new}
    assert await _session_ids(league.db_path) == (new, [new])


# ---------------------------------------------------------------------------
# A standings picture is saved and replaced by the jobs, as a text table is (#439 6b, fault 1)
# ---------------------------------------------------------------------------


async def _standings_ids(db_path: str) -> tuple[int | None, int | None]:
    return (
        await get_standings_message_id(db_path, DIVISION_ID, ROUND_ID),
        await get_standings_message_id(db_path, DIVISION_ID, ROUND_ID, STANDINGS_CONSTRUCTORS),
    )


#: Why a moved standings channel's case fails until standings keep the channel they were posted in.
STANDINGS_POSTED_IN = (
    "#645: posted standings keep no channel they were posted in, so the old ones are looked for "
    "in the present standings channel"
)


async def _standings_posted_in(db_path: str) -> int | None:
    """The channel round 3's drivers' standings are recorded as posted in."""
    async with get_connection(db_path) as db:
        row = await (await db.execute(
            "SELECT standings_message_channel_id FROM round_standings_posts WHERE round_id = ?",
            (ROUND_ID,),
        )).fetchone()
    return row[0]


@pytest.mark.xfail(strict=True, reason=STANDINGS_POSTED_IN)
async def test_standings_whose_channel_moved_are_posted_afresh_and_the_old_deleted_where_they_stand(
    tmp_path,
):
    """Round 3's standings stand as text in channel 701, message 8801, recorded as posted there,
    short enough to be edited in place; the division's standings channel has since moved to 704.
    They are posted again: not edited in 701, but posted afresh in 704 with 704 recorded beside
    the new id, and 8801 is deleted from 701 after the new standings were sent."""
    league = await _league(tmp_path, standings_channel=NEW_STANDINGS_CHANNEL,
                           standings_posted_in=STANDINGS_CHANNEL)
    await _ask(league)
    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    new = league.sent_to(NEW_STANDINGS_CHANNEL)
    assert new
    assert ("edit", STANDINGS_CHANNEL, OLD_STANDINGS) not in league.events
    assert league.events.index(("send", NEW_STANDINGS_CHANNEL, new[0])) < league.events.index(
        ("delete", STANDINGS_CHANNEL, OLD_STANDINGS)
    )
    assert OLD_STANDINGS not in league.channel(STANDINGS_CHANNEL).messages
    assert await get_standings_message_id(league.db_path, DIVISION_ID, ROUND_ID) == new[0]
    assert await _standings_posted_in(league.db_path) == NEW_STANDINGS_CHANNEL


@pytest.mark.parametrize("present, posted_in", [
    pytest.param(STANDINGS_CHANNEL, None, id="kept"),
    pytest.param(NEW_STANDINGS_CHANNEL, STANDINGS_CHANNEL, id="moved",
                 marks=pytest.mark.xfail(strict=True, reason=STANDINGS_POSTED_IN)),
])
async def test_a_standings_picture_s_old_message_is_deleted_by_a_job_of_its_own(
    tmp_path, present, posted_in,
):
    """Round 3's standings stand as text in channel 701, message 8801; they are posted again in
    the division's present standings channel, both championships drawn as pictures. Once
    `post_standings` is done the two pictures stand and 8801 still stands; then a
    `delete_message` job deletes 8801 from 701, after the pictures were sent. Where the standings
    channel has since moved to 704, the pictures go to 704 and 8801 still comes down from 701."""
    league = await _built_league(tmp_path, standings_channel=present,
                                 standings_posted_in=posted_in)
    standings = league.channel(STANDINGS_CHANNEL)
    with await _drawn(league, tmp_path, "standings"):
        await _ask(league)
        await _run_until_done(league, "post_standings")

        pictures = league.sent_to(present)
        assert len(pictures) == 2
        assert OLD_STANDINGS in standings.messages, (
            "the picture deleted the message it replaces itself"
        )

        await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    assert set(standings.messages) | set(league.channel(present).messages) == {
        *pictures, OLD_LATER_STANDINGS,
    }
    deletes = [row for row in await step_rows(league.db_path) if row["name"] == "delete_message"
               and json.loads(row["payload"])["message_id"] == OLD_STANDINGS]
    assert len(deletes) == 1 and deletes[0]["done_at"] is not None
    assert league.events.index(("send", present, pictures[0])) < league.events.index(
        ("delete", STANDINGS_CHANNEL, OLD_STANDINGS)
    )


async def test_a_refused_delete_of_an_old_standings_picture_stops_the_queue_keeping_both(
    tmp_path,
):
    """Round 3's standings stand as two pictures, 8807 (drivers) and 8808 (constructors), each
    recorded. They are drawn again, and Discord refuses to delete the old ones: the queue stops
    at the first `delete_message` job, the drivers' 8807, the old pictures and the new standing
    together, and the new pictures' ids saved with their post. A league admin discards it, the
    queue stops again at the constructors' 8808, and the admin discards that too: the outcome
    links the old picture left standing."""
    league = await _built_league(tmp_path)
    async with get_connection(league.db_path) as db:
        await set_standings_message_id_on(
            db, DIVISION_ID, ROUND_ID, DRIVERS_GRAPHIC,
            message_ids=json.dumps([DRIVERS_GRAPHIC]),
        )
        await set_standings_message_id_on(
            db, DIVISION_ID, ROUND_ID, CONSTRUCTORS_GRAPHIC, STANDINGS_CONSTRUCTORS,
            message_ids=json.dumps([CONSTRUCTORS_GRAPHIC]),
        )
        await db.commit()
    standings = league.channel(STANDINGS_CHANNEL)
    standings.seed(DRIVERS_GRAPHIC, "drivers' standings graphic")
    standings.seed(CONSTRUCTORS_GRAPHIC, "constructors' standings graphic")
    old_link = standings.messages[DRIVERS_GRAPHIC].jump_url
    standings.delete_fails = http_error(discord.Forbidden, status=403, text="Missing Permissions")
    with await _drawn(league, tmp_path, "standings"):
        interaction = await _ask(league)
        await run_queue(league.bot)

    job = await stopped_job(league.db_path)
    assert job is not None and job["name"] == "delete_message"
    assert json.loads(job["payload"])["message_id"] == DRIVERS_GRAPHIC
    drivers, constructors = league.sent_to(STANDINGS_CHANNEL)
    assert {DRIVERS_GRAPHIC, CONSTRUCTORS_GRAPHIC, drivers, constructors} <= set(
        standings.messages
    )
    assert await _standings_ids(league.db_path) == (drivers, constructors)

    await discard_job(league.bot)
    job = await stopped_job(league.db_path)
    assert job is not None and job["name"] == "delete_message"
    assert json.loads(job["payload"])["message_id"] == CONSTRUCTORS_GRAPHIC

    await discard_job(league.bot)
    assert await stopped_job(league.db_path) is None
    assert old_link in updated_reply(interaction)
    assert DRIVERS_GRAPHIC in standings.messages


async def test_a_standings_picture_s_id_is_saved_with_its_mark(tmp_path):
    """Round 3's standings are drawn as two pictures. Their ids are saved by `post_standings`'
    own record, with its done mark: the job's result names both pictures."""
    league = await _built_league(tmp_path)
    with await _drawn(league, tmp_path, "standings"):
        await _ask(league)
        await _run_until_done(league, "post_standings")

    drivers, constructors = league.sent_to(STANDINGS_CHANNEL)
    assert await _standings_ids(league.db_path) == (drivers, constructors)
    [post] = [row for row in await step_rows(league.db_path) if row["name"] == "post_standings"]
    assert {drivers, constructors} <= set(post["result"]["new"])


async def test_a_picture_sent_before_a_later_failure_is_removed_before_the_next_try(tmp_path):
    """Round 3's standings are posted again: the drivers' championship draws as a picture and
    the constructors' falls back to a text table, which Discord refuses. The queue stops at
    `post_standings` with the drivers' picture standing, recorded nowhere, and 8801 still
    standing. On Retry the picture is deleted before the retry's text is sent, so the channel
    is left with the retry's table alone beside round 4's, and 8801 comes down by its job."""
    league = await _built_league(tmp_path)
    standings = league.channel(STANDINGS_CHANNEL)
    standings.fail_send_at = 2
    with await _drawn(league, tmp_path, "standings", championships=("drivers",)):
        await _ask(league)
        await run_queue(league.bot)
        assert (await stopped_job(league.db_path))["name"] == "post_standings"
        [picture] = league.sent_to(STANDINGS_CHANNEL)
        assert picture in standings.messages
        assert OLD_STANDINGS in standings.messages
        assert await _standings_ids(league.db_path) == (OLD_STANDINGS, None)

        standings.fail_send_at = None
        await retry_job(league.bot)

    assert await stopped_job(league.db_path) is None
    retried = [mid for mid in league.sent_to(STANDINGS_CHANNEL) if mid != picture]
    assert retried
    assert picture not in standings.messages
    assert league.events.index(("delete", STANDINGS_CHANNEL, picture)) < league.events.index(
        ("send", STANDINGS_CHANNEL, retried[0])
    )
    assert set(standings.messages) == {*retried, OLD_LATER_STANDINGS}


# ---------------------------------------------------------------------------
# A picture that falls back to text says why in the log channel (image spec :822, :1035)
# ---------------------------------------------------------------------------

#: What the image module's posting step raises in these tests: a fault its own catches let
#: through (the drawn picture's file cannot be opened to attach), where a fault in drawing or
#: rasterising is caught there and already reported in the log channel by the image module. It is a
#: fault in the bot, whose words never reach the log channel.
PICTURE_FILE_FAULT = "the drawn picture could not be opened"
#: How the log channel names a fault in the bot: by its kind alone.
FAULT_IN_THE_BOT = "the bot hit an internal fault"
#: What the results composer refuses with in these tests: a fault the league can put right, which
#: keeps its words.
REFUSED = "the session names a driver the league has no record of"
#: The words the render gives where Inkscape is not installed, which the log channel keeps.
CONVERTER_ABSENT_WORDS = "Inkscape is not installed on this host."
#: What a line says puts a picture right where Inkscape is not installed, before what posts it.
CONVERTER_ABSENT_REMEDY = (
    "Inkscape is not installed on the machine running the bot, and the host must install it; "
    "once it has, "
)


def _fault_line(league: _League) -> str:
    """The one line the log channel gained naming round 3 of Pro, the posting's own, in lower
    case."""
    lines = [line for line in league.bot.log_channel.sent
             if "Pro" in line and "round 3" in line.lower()]
    assert len(lines) == 1, league.bot.log_channel.sent
    return lines[0].lower()


def _says_why(line: str, picture: str, source: str) -> bool:
    """*line* names the *picture* that failed ("results", "standings"), says the post went out
    as text in its place, gives the fault, and says what puts it right, by where the fault lies
    (*source*): for a fault in the bot ("bot"), its kind alone, never the words it was raised with,
    and that the fault is the bot's, its detail in the host's log, and that the sync command posts
    it again once it is cleared; for the league's own ("league"), its words, and to fix the
    template, then run that sync command."""
    sync = "rounds" if picture == "results" else "standings"
    if source == "bot":
        said = (FAULT_IN_THE_BOT in line and PICTURE_FILE_FAULT not in line
                and "to put it right: the fault is in the bot, and its detail is in the host's "
                f"log; once it is cleared, run `/results {sync} sync`" in line)
    else:
        said = (REFUSED in line
                and f"to put it right: fix the {picture} template, then run `/results {sync} sync`"
                in line)
    return (picture in line and ("picture" in line or "image" in line)
            and "text" in line and said)


def _says_the_host_must_install_it(league: _League, sync: str) -> bool:
    """The log channel keeps the render's words for Inkscape's absence on a line naming round 3
    of Pro, and that line says the host must install it, and that `/results {sync} sync` posts
    the picture once it has."""
    lines = [line for line in league.bot.log_channel.sent if CONVERTER_ABSENT_WORDS in line]
    return any(
        "Pro" in line and "round 3" in line.lower()
        and f"{CONVERTER_ABSENT_REMEDY}run `/results {sync} sync`" in line
        for line in lines
    )


def _render_without_the_converter(aspect: str) -> Any:
    """The image module's render of *aspect*'s picture ("results", "standings") comes back not
    drawn, for want of Inkscape on the host, and the text is posted in its place. Gives the
    patch, to enter."""
    from leaguebot.image.models.image_module import Problem
    from leaguebot.image.services.render.image_render_service import (
        POST_TEXT_FALLBACK,
        PostingDecision,
    )

    template = "results_race_template" if aspect == "results" else "standings_drivers_template"
    return patch(
        f"leaguebot.image.services.aspects.image_{aspect}_post.render_png",
        new=AsyncMock(return_value=PostingDecision(action=POST_TEXT_FALLBACK, problem=Problem(
            kind="CONVERTER_ABSENT", detail=CONVERTER_ABSENT_WORDS, template_key=template,
        ))),
    )


@pytest.mark.parametrize("source", [
    "bot",
    "league",
    pytest.param("converter absent"),
])
async def test_a_results_picture_that_fails_posts_the_table_as_text_and_says_why_in_the_log(
    tmp_path, source,
):
    """Round 3's results are posted again with image generation on, and the results picture is
    not posted: ("bot") the image module's posting step raises a fault its own catches let
    through (the drawn picture cannot be opened), a fault in the bot; ("league") the results
    composer refuses the drawing, naming a driver the league has no record of, a fault the
    league can put right; ("converter absent") the render comes back not drawn, as Inkscape is
    not installed on the host. The table goes out as text in the results channel and the queue
    runs to the end. For the first two, the log channel gains one line naming round 3 of Pro,
    the results picture, the fault (a fault in the bot by its kind alone, a refusal in its own
    words), that the table went out as text, and what puts it right by where the fault lies.
    Without Inkscape, the log channel keeps the render's words on a line naming round 3 of Pro,
    and says the host must install it and that `/results rounds sync` posts it once it has."""
    from leaguebot.image.models.results_drawing import ResultsDataError
    from leaguebot.image.services.aspects.image_standings_post import StandingsDrawn

    league = await _league(tmp_path)
    answer_switch(league.bot.module_service, "images", True)
    with contextlib.ExitStack() as stack:
        composed = stack.enter_context(_pictures_are_due("results", "standings"))
        stack.enter_context(patch(
            "leaguebot.image.services.render.image_drawing.ImageDrawing.draw_standings",
            new=AsyncMock(return_value=StandingsDrawn()),
        ))
        if source == "bot":
            stack.enter_context(patch(
                "leaguebot.image.services.render.image_drawing.ImageDrawing.draw_results",
                new=AsyncMock(side_effect=OSError(PICTURE_FILE_FAULT)),
            ))
        elif source == "league":
            composed.side_effect = ResultsDataError(REFUSED)
        else:
            composed.return_value = MagicMock(
                season_number=1, division_name="Pro", round_number=3,
                session_name="Feature Race",
            )
            stack.enter_context(_render_without_the_converter("results"))
        await _ask(league)
        await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    [new] = league.sent_to(RESULTS_CHANNEL)
    assert "Round 3" in league.channel(RESULTS_CHANNEL).messages[new].content
    if source == "converter absent":
        assert _says_the_host_must_install_it(league, "rounds"), league.bot.log_channel.sent
        return
    line = _fault_line(league)
    assert _says_why(line, "results", source), line


@pytest.mark.parametrize("source", [
    "bot",
    pytest.param("converter absent"),
])
async def test_a_standings_picture_that_fails_posts_the_standings_as_text_and_says_why_in_the_log(
    tmp_path, source,
):
    """Round 3's standings are posted again with image generation on, and the standings
    pictures are not posted: ("bot") the image module's posting step for them raises a fault its
    own catches let through (the drawn picture cannot be opened), a fault in the bot; ("converter
    absent") their renders come back not drawn, as Inkscape is not installed on the host. The
    standings stand as text in the standings channel and the queue runs to the end. For the
    fault in the bot, the log channel gains one line naming round 3 of Pro, the standings
    picture, the fault by its kind alone and never the words it was raised with, that the
    standings went out as text, and that the fault is the bot's, its detail in the host's log,
    and that `/results standings sync` posts it once it is cleared. Without Inkscape, the log
    channel keeps the render's words on a line naming round 3 of Pro, and says the host must
    install it and that `/results standings sync` posts it once it has."""
    league = await _league(tmp_path)
    answer_switch(league.bot.module_service, "images", True)
    with contextlib.ExitStack() as stack:
        stack.enter_context(_pictures_are_due("results", "standings"))
        stack.enter_context(patch(
            "leaguebot.image.services.render.image_drawing.ImageDrawing.draw_results",
            new=AsyncMock(return_value=(None, ())),
        ))
        if source == "bot":
            stack.enter_context(patch(
                "leaguebot.image.services.render.image_drawing.ImageDrawing.draw_standings",
                new=AsyncMock(side_effect=OSError(PICTURE_FILE_FAULT)),
            ))
        else:
            stack.enter_context(_render_without_the_converter("standings"))
        await _ask(league)
        await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    standings = league.channel(STANDINGS_CHANNEL)
    texts = [standings.messages[mid].content for kind, ch, mid in league.events
             if ch == STANDINGS_CHANNEL and kind in ("send", "edit") and mid in standings.messages]
    assert texts and all("Round 3" in text for text in texts)
    if source == "converter absent":
        assert _says_the_host_must_install_it(league, "standings"), league.bot.log_channel.sent
        return
    line = _fault_line(league)
    assert _says_why(line, "standings", source), line


async def test_a_picture_fallback_line_names_the_amendment_while_the_season_is_pending_completion(
    tmp_path,
):
    """Season 1 is pending completion, where the two sync commands are refused (results spec
    :587), and round 3's results and standings are posted again with both pictures failing on a
    fault in the bot. The queue runs to the end and the log channel gains one line for each
    picture, each naming the fault by its kind alone, saying the fault is the bot's and its
    detail is in the host's log, and that once it is cleared the round is amended with
    `/results rounds amend`, and neither naming a sync command (results spec :589)."""
    league = await _league(tmp_path)
    async with get_connection(league.db_path) as db:
        await db.execute("UPDATE seasons SET stage = 'PENDING_COMPLETION' WHERE id = ?",
                         (SEASON_ID,))
        await db.commit()
    answer_switch(league.bot.module_service, "images", True)
    with _pictures_are_due("results", "standings"), patch(
        "leaguebot.image.services.render.image_drawing.ImageDrawing.draw_results",
        new=AsyncMock(side_effect=OSError(PICTURE_FILE_FAULT)),
    ), patch(
        "leaguebot.image.services.render.image_drawing.ImageDrawing.draw_standings",
        new=AsyncMock(side_effect=OSError(PICTURE_FILE_FAULT)),
    ):
        await _ask(league)
        await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    lines = [line for line in league.bot.log_channel.sent if "posted as text" in line]
    assert len(lines) == 2, league.bot.log_channel.sent
    for picture in ("results", "standings"):
        [line] = [line for line in lines if f"{picture} picture" in line]
        assert FAULT_IN_THE_BOT in line and PICTURE_FILE_FAULT not in line, line
        assert ("to put it right: the fault is in the bot, and its detail is in the host's log; "
                "once it is cleared, amend the round with `/results rounds amend`") in line, line
        assert "sync" not in line, line


# ---------------------------------------------------------------------------
# A picture Discord refuses is posted as text in the same try, and the log channel says so
# ---------------------------------------------------------------------------

#: The reason the fake channel gives for refusing a send (`_channel`'s `fail_send_at`).
REFUSAL = "Discord failed part-way"


#: The command that posts each picture again while the season is being raced, which a refused
#: picture's line tells the league to run (owner's call p6, "Re-post, no template").
REPOST_COMMAND = {"results": "/results rounds sync", "standings": "/results standings sync"}


@pytest.mark.parametrize("text_refused", [False, pytest.param(True, id="text-also-refused-then-retried")])
@pytest.mark.parametrize("picture", ["results", "standings"])
async def test_a_picture_discord_refuses_is_posted_as_text_and_the_log_says_why(
    tmp_path, picture, text_refused,
):
    """Round 3's results and standings in Pro are posted again with *picture* drawn, and Discord
    refuses the picture's upload, the first send to its channel. The table goes out as text in the
    same try and the queue runs to the end, as it does today; and the log channel gains one line
    naming round 3 of Pro, the *picture* picture, that Discord refused it, Discord's reason, and
    that the table went out as text. What it says puts it right is to post the picture again with
    the sync command the league already uses, naming no template, which is not at fault.

    Where *text_refused*, Discord refuses every send to the channel on the first try, the picture
    and then its text, so the queue stops at that post; the channel is let accept and a Retry
    posts the table as text. The picture's line is still in the log channel once, never lost with
    the refused try."""
    league = await _built_league(tmp_path)
    if picture == "results":
        target, drawn = RESULTS_CHANNEL, await _drawn(league, tmp_path, "results")
    else:
        target, drawn = STANDINGS_CHANNEL, await _drawn(league, tmp_path, "standings",
                                                        championships=("drivers",))
    channel = league.channel(target)
    if text_refused:
        channel.send_fails = http_error(text=REFUSAL)
    else:
        channel.fail_send_at = 1
    with drawn:
        await _ask(league)
        await run_queue(league.bot)
        if text_refused:
            assert await stopped_job(league.db_path) is not None
            channel.send_fails = None
            await retry_job(league.bot)

    assert await stopped_job(league.db_path) is None
    texts = [channel.messages[mid].content for kind, ch, mid in league.events
             if ch == target and kind in ("send", "edit") and mid in channel.messages]
    assert texts and all("Round 3" in text for text in texts)
    lines = [line.lower() for line in league.bot.log_channel.sent
             if REFUSAL.lower() in line.lower()]
    assert len(lines) == 1, league.bot.log_channel.sent
    [line] = lines
    assert "pro" in line and "round 3" in line, line
    if not text_refused:
        assert line == _fault_line(league)
    assert picture in line and ("picture" in line or "image" in line), line
    assert "refused" in line and REFUSAL.lower() in line and "text" in line, line
    put_right = line.split("to put it right:", 1)[1]
    assert REPOST_COMMAND[picture] in put_right and "template" not in put_right, line


@pytest.mark.parametrize("picture", ["results", "standings"])
async def test_a_picture_whose_upload_is_cut_off_is_posted_as_text_in_the_same_try(
    tmp_path, picture,
):
    """Round 3's results and standings in Pro are posted again with *picture* drawn, its render
    coming back with one notice, a flag falling back; the picture's upload, the first send to its
    channel, is cut off by a connection error, not refused by Discord. The table goes out as text
    in the same try and the queue runs to the end, as at the base; the notice reaches the log
    channel once, and a line there names round 3 and the *picture* picture gone out as text."""
    import aiohttp

    league = await _built_league(tmp_path)
    if picture == "results":
        target = RESULTS_CHANNEL
        drawn = await _drawn(league, tmp_path, "results", notice=NOTICE_DETAIL)
    else:
        target = STANDINGS_CHANNEL
        drawn = await _drawn(league, tmp_path, "standings", championships=("drivers",),
                             notice=NOTICE_DETAIL)
    channel = league.channel(target)
    send, sends = channel.send.side_effect, itertools.count(1)

    async def _cut_off_first(*args: Any, **kwargs: Any) -> Any:
        if next(sends) == 1:
            raise aiohttp.ClientOSError(104, "Connection reset by peer")
        return await send(*args, **kwargs)

    channel.send.side_effect = _cut_off_first
    with drawn:
        await _ask(league)
        await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    texts = [channel.messages[mid].content for kind, ch, mid in league.events
             if ch == target and kind in ("send", "edit") and mid in channel.messages]
    assert texts and all("Round 3" in text for text in texts)
    log = "\n".join(league.bot.log_channel.sent)
    assert log.count(NOTICE_DETAIL) == 1, log
    assert any(picture in message.lower() and "text" in message.lower()
               and "round 3" in message.lower()
               for message in league.bot.log_channel.sent), log


# ---------------------------------------------------------------------------
# A drawn picture's render notices reach the log channel once, under what was drawn
# ---------------------------------------------------------------------------

#: The detail of the one render notice the picture below is drawn with.
NOTICE_DETAIL = "no `flag` image for “Lewis”"


@pytest.mark.parametrize(("picture", "refused"), [
    ("results", False),
    ("standings", False),
    pytest.param("standings", True, id="standings-refused-then-retried"),
])
async def test_a_drawn_picture_s_render_notices_reach_the_log_once_naming_the_round(
    tmp_path, picture, refused,
):
    """Round 3's results and standings in Pro are posted again with the *picture* picture drawn,
    its render coming back with one notice, a flag falling back. The picture is posted and the
    queue runs to the end; the log channel carries the notice once, in a block whose heading
    names Pro and round 3, and no block without a heading carries it a second time.

    Where *refused*, the standings channel refuses every send on the first try, the picture and
    then its text, so the queue stops at the standings' post; the channel is let accept and a
    Retry posts the standings as text. The notice is still carried once, never lost with the
    refused try."""
    league = await _built_league(tmp_path)
    target = RESULTS_CHANNEL if picture == "results" else STANDINGS_CHANNEL
    channel = league.channel(target)
    if refused:
        channel.send_fails = http_error(text=REFUSAL)
    with await _drawn(league, tmp_path, picture, notice=NOTICE_DETAIL):
        await _ask(league)
        await run_queue(league.bot)
        if refused:
            job = await stopped_job(league.db_path)
            assert job is not None and job["name"] == "post_standings", job
            channel.send_fails = None
            await retry_job(league.bot)

    assert await stopped_job(league.db_path) is None
    assert any(kwargs.get("file") is not None
               for _args, kwargs in league.channel(target).send.call_args_list)
    log = "\n".join(league.bot.log_channel.sent)
    assert log.count(NOTICE_DETAIL) == 1, log
    [block] = [message for message in league.bot.log_channel.sent if NOTICE_DETAIL in message]
    heading = block.splitlines()[0]
    assert "Pro" in heading and "round 3" in heading.lower(), block


@pytest.mark.parametrize(("draw", "job_name", "target"), [
    pytest.param("draw_results", "post_session_results", RESULTS_CHANNEL, id="results"),
    pytest.param("draw_standings", "post_standings", STANDINGS_CHANNEL, id="standings"),
])
async def test_a_picture_whose_images_switch_nobody_signed_up_for_stops_its_job(
    tmp_path, draw, job_name, target,
):
    """Round 3's results and standings in Pro are posted again with pictures asked for, but the
    images module's switch reader was never signed up (`SwitchNotSignedUp`), a fault in the bot's
    own wiring, raised as the *draw* picture is asked for. The fault reaches that post's job,
    which stops; its table is not posted as text in the picture's place."""
    from leaguebot.framework.services.switches.module_service import SwitchNotSignedUp

    league = await _league(tmp_path)
    answer_switch(league.bot.module_service, "images", True)
    other = "draw_standings" if draw == "draw_results" else "draw_results"
    with _pictures_are_due("results", "standings"), patch(
        f"leaguebot.image.services.render.image_drawing.ImageDrawing.{draw}",
        new=AsyncMock(side_effect=SwitchNotSignedUp("images")),
    ), patch(
        f"leaguebot.image.services.render.image_drawing.ImageDrawing.{other}",
        new=AsyncMock(return_value=(None, ())),
    ):
        await _ask(league)
        await run_queue(league.bot)

    job = await stopped_job(league.db_path)
    assert job is not None and job["name"] == job_name, job
    assert league.sent_to(target) == []


@pytest.mark.parametrize("picture", ["results", "standings"])
async def test_a_picture_that_cannot_be_drawn_keeps_its_line_when_its_text_is_refused(
    tmp_path, picture,
):
    """Round 3's results and standings are posted again with image generation on, and *picture*
    cannot be drawn (the drawn file cannot be opened, a fault in the bot). Discord refuses every
    send to its channel on the first try, so the text the picture fell back to is refused too and
    the queue stops; the channel is let accept and a Retry posts the text. The log channel carries
    the picture's line, naming the fault by its kind and never in its own words, once: it is not
    lost with the refused try."""
    from leaguebot.image.services.aspects.image_standings_post import StandingsDrawn

    league = await _league(tmp_path)
    answer_switch(league.bot.module_service, "images", True)
    target = RESULTS_CHANNEL if picture == "results" else STANDINGS_CHANNEL
    failing = AsyncMock(side_effect=OSError(PICTURE_FILE_FAULT))
    with _pictures_are_due("results", "standings"), patch(
        "leaguebot.image.services.render.image_drawing.ImageDrawing.draw_results",
        new=failing if picture == "results" else AsyncMock(return_value=(None, ())),
    ), patch(
        "leaguebot.image.services.render.image_drawing.ImageDrawing.draw_standings",
        new=failing if picture == "standings" else AsyncMock(return_value=StandingsDrawn()),
    ):
        league.channel(target).send_fails = http_error(text=REFUSAL)
        # The standings' earlier table is gone, so their text is sent afresh rather than edited
        # in place, and the refusal reaches it.
        league.channel(STANDINGS_CHANNEL).messages.pop(OLD_STANDINGS, None)
        await _ask(league)
        await run_queue(league.bot)
        assert await stopped_job(league.db_path) is not None
        league.channel(target).send_fails = None
        await retry_job(league.bot)

    assert await stopped_job(league.db_path) is None
    lines = [line.lower() for line in league.bot.log_channel.sent
             if FAULT_IN_THE_BOT in line.lower()]
    assert len(lines) == 1, league.bot.log_channel.sent
    assert picture in lines[0] and "text" in lines[0], lines[0]
    assert all(PICTURE_FILE_FAULT not in line.lower() for line in league.bot.log_channel.sent)


class _SendsRefused:
    """A `fail_send_at` refusing more than one send: equal to each send number in *numbers*."""

    def __init__(self, *numbers: int) -> None:
        self.numbers = numbers

    def __eq__(self, other: object) -> bool:
        return other in self.numbers

    __hash__ = None


async def test_a_refused_picture_s_line_survives_a_retry_that_cannot_remove_the_part_posted_text(
    tmp_path,
):
    """Round 3's results are posted again with the results picture drawn. Discord refuses the
    picture's upload, takes the first chunk of the text it falls back to and refuses the second,
    and refuses to delete the chunk, so the queue stops with it standing. On the first Retry
    Discord still refuses to delete the chunk, and the queue stops again; on the second the chunk
    is removed and the text posted. The
    picture's refusal, with Discord's reason, reaches the log channel once."""
    league = await _built_league(tmp_path)
    results = league.channel(RESULTS_CHANNEL)
    results.fail_send_at = _SendsRefused(1, 3)
    results.delete_fails = http_error(text="Discord is down")
    with await _drawn(league, tmp_path, "results"), patch(
        "leaguebot.results.services.publishing.results_post_service._MSG_MAX", 40,
    ):
        await _ask(league)
        await run_queue(league.bot)
        assert (await stopped_job(league.db_path))["name"] == "post_session_results"
        assert league.sent_to(RESULTS_CHANNEL), "the first chunk stands, for the retry to remove"
        await retry_job(league.bot)
        assert (await stopped_job(league.db_path))["name"] == "post_session_results"
        results.delete_fails = None
        results.fail_send_at = None
        await retry_job(league.bot)

    assert await stopped_job(league.db_path) is None
    lines = [line for line in league.bot.log_channel.sent if REFUSAL.lower() in line.lower()]
    assert len(lines) == 1, league.bot.log_channel.sent
    assert "results" in lines[0].lower() and "refused" in lines[0].lower(), lines[0]
