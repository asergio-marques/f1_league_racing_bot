"""A season review on the change queue (#439, slice 6b, C92–C98, C97a).

Today `/season placements-review` and `/season config-review` draw and post their review at once,
off the queue: the pictures and the report as the command's follow-ups, the question bound to an
`_ApproveView` whose five minutes run in memory, and the view, a refused press, a confirmation and
the restart's sweep each delete the review themselves. On the queue (decision 5, "Whole review on
the queue"; decision 19):

- `season.review.take_down` (C92), the bot's: one `delete_message` job per message of the review,
  by id, a message already gone or a channel gone completing; then `post_expiry`, the notice
  pinging the reviewer, but where the review was confirmed; then `forget`, which drops the
  `season_review_prompts` row and, but where confirmed, records the review's lapse. A refused
  delete stops the queue; a Discard names the message for deletion by hand. Its payload is
  `{channel_id, question_id, report_ids, reviewer_id, review, ending}`, *ending* one of
  `confirmed`, `expired`, `restart` and `refused`.
- `season.review.post` (C93), the reviewer's: `post_drawing_notice` where pictures are due, one
  `draw_and_post` per picture in order, `delete_drawing_notice`, one `post_report` per section,
  `post_question` (whose record writes the prompt row with every report id and the evidence the
  report was computed from) and `arm_expiry`, which arms the `review_expiry` timer five minutes
  from the question's posting. A picture is drawn through the callable the builder hands the
  factory (`drawing`, run off the event loop), and posted as text in the same try where Discord
  refuses it; one that will not draw is no stop: the Approve button is withheld and the reviewer
  told why, in their reply or, once that can no longer be updated, the log channel.

The factories are `season_review_take_down_change()` and `season_review_post_change(drawing=...,
approve_view=...)` in `leaguebot.core.services.season_review_changes`; the builder registers them
with the image module's drawing and the season cog's view factory. Everything of them is imported
inside a test, so this file collects while it is unbuilt.

The league: `league_double`'s, its review channel 700 found on the league's server, Alex (4242)
the reviewer of season 1's placements.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from tests.support.change_queue import (
    INTERACTION_CHANNEL_ID,
    LOG_CHANNEL_ID,
    MEMBER_ID,
    SERVER_ID,
    Clock,
    acknowledgement,
    attach_queue,
    change_rows,
    discard_job,
    history_channel,
    http_error,
    league_double,
    member_interaction,
    post_in,
    run_queue,
    seed_server,
    step_rows,
    stopped_job,
    updated_reply,
)
from tests.support.review_prompts import store_review_prompt

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
REVIEW_CHANNEL = 700
REVIEWER = MEMBER_ID
PLACEMENTS = "/season placements-review"
TAKE_DOWN = "season.review.take_down"
BOT_USER = SimpleNamespace(id=1, name="LeagueBot")

NO_TAKE_DOWN = (
    "#439: a season review is taken down by its view and the restart's sweep at once, off the "
    "change queue"
)


def _alex() -> Any:
    alex = MagicMock()
    alex.id = REVIEWER
    alex.display_name = "Alex"
    alex.mention = f"<@{REVIEWER}>"
    return alex


def _server(bot: Any, *channels: Any) -> None:
    """Let *bot* and the league's server find *channels* beside the log and interaction channels,
    and Alex on the server; any other channel is gone."""
    known = {LOG_CHANNEL_ID: bot.log_channel, INTERACTION_CHANNEL_ID: bot.interaction_channel}
    known.update({channel.id: channel for channel in channels})

    async def _fetch(channel_id: int) -> Any:
        if channel_id in known:
            return known[channel_id]
        raise http_error(discord.NotFound, status=404, text="Unknown Channel")

    alex = _alex()
    guild = MagicMock(spec=discord.Guild)
    guild.id = SERVER_ID
    guild.me = BOT_USER
    guild.get_channel = MagicMock(side_effect=known.get)
    guild.get_member = MagicMock(side_effect=lambda uid: alex if uid == REVIEWER else None)
    for channel in channels:
        channel.guild = guild
    bot.get_channel = MagicMock(side_effect=known.get)
    bot.fetch_channel = AsyncMock(side_effect=_fetch)
    bot.get_guild = MagicMock(return_value=guild)


def _standing_review_channel() -> Any:
    """Review channel 700, holding a review the bot posted: its report R1 and R2, then its
    question Q. Each message deleted is kept in `deleted`; what the channel is sent, in `sent`."""
    channel = history_channel(REVIEW_CHANNEL)
    channel.sent = []

    async def _send(content: str = "", **kwargs: Any) -> Any:
        channel.sent.append((content, kwargs))
        return post_in(channel, BOT_USER)

    channel.send = AsyncMock(side_effect=_send)
    channel.r1 = post_in(channel, BOT_USER)
    channel.r2 = post_in(channel, BOT_USER)
    channel.q = post_in(channel, BOT_USER)
    return channel


@pytest.fixture
async def standing(tmp_path):
    """The league's queue with every change type the builder registers, and Alex's placements
    review of season 1 standing in channel 700, recorded in `season_review_prompts`."""
    db_path = str(tmp_path / "queue.db")
    await run_migrations(db_path)
    await seed_server(db_path)
    bot = league_double(db_path)
    bot.user = BOT_USER
    channel = _standing_review_channel()
    _server(bot, channel)
    clock = Clock(NOW)
    attach_queue(bot, db_path, now=clock)
    async with get_connection(db_path) as db:
        await store_review_prompt(
            db, season_id=1, channel_id=REVIEW_CHANNEL, message_id=channel.q.id,
            reviewer_id=REVIEWER, review=PLACEMENTS,
            report_message_ids=(channel.r1.id, channel.r2.id),
        )
        await db.commit()
    return SimpleNamespace(db_path=db_path, bot=bot, channel=channel, clock=clock)


async def _take_down(env: Any, ending: str) -> None:
    """The bot asks for Alex's review in channel 700 to be taken down, as *ending* has it."""
    from leaguebot.core.models.change import ChangeOrigin

    channel = env.channel
    await env.bot.change_queue.ask(
        TAKE_DOWN,
        {"channel_id": REVIEW_CHANNEL, "question_id": channel.q.id,
         "report_ids": [channel.r1.id, channel.r2.id], "reviewer_id": REVIEWER,
         "review": PLACEMENTS, "ending": ending},
        what="taking the placements review down", origin=ChangeOrigin.BOT,
    )


async def _prompts(db_path: str) -> list[Any]:
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT * FROM season_review_prompts")
        return list(await cursor.fetchall())


async def _changes(env: Any, kind: str) -> list[dict[str, Any]]:
    return [row for row in await change_rows(env.db_path) if row["kind"] == kind]


def _log(env: Any) -> str:
    return "\n".join(env.bot.log_channel.sent)


@pytest.mark.xfail(strict=True, reason=NO_TAKE_DOWN)
async def test_a_take_down_deletes_each_message_by_id_and_forgets_the_prompt(standing):
    """Alex's review stands in channel 700: report R1 and R2, question Q. The bot asks for it to
    be taken down, confirmed. Nothing is deleted until the queue runs; then Q, R1 and R2 are each
    deleted, the prompt's row is gone and the change is done."""
    channel = standing.channel

    await _take_down(standing, "confirmed")
    deleted_before = list(channel.deleted)
    await run_queue(standing.bot)

    assert deleted_before == []
    assert {m.id for m in channel.deleted} == {channel.q.id, channel.r1.id, channel.r2.id}
    assert channel.messages == []
    assert await _prompts(standing.db_path) == []
    [change] = await _changes(standing, TAKE_DOWN)
    assert change["state"] == "DONE"


@pytest.mark.xfail(strict=True, reason=NO_TAKE_DOWN)
async def test_an_expired_take_down_posts_the_expiry_notice_pinging_the_reviewer(standing):
    """Alex's review ran its five minutes and the bot asks for it to be taken down, expired. Once
    the queue runs the review is deleted, then one notice is posted in channel 700 pinging Alex
    alone and naming `/season placements-review` to run again, and the log channel records the
    review's lapse, started by Alex."""
    channel = standing.channel

    await _take_down(standing, "expired")
    await run_queue(standing.bot)

    assert channel.messages == []
    [(notice, kwargs)] = channel.sent
    assert f"<@{REVIEWER}>" in notice and PLACEMENTS in notice
    mentions = kwargs.get("allowed_mentions")
    assert mentions is not None and mentions.roles is False and mentions.everyone is False
    assert "lapsed" in _log(standing) and PLACEMENTS in _log(standing)
    assert "Alex" in _log(standing)


@pytest.mark.xfail(strict=True, reason=NO_TAKE_DOWN)
async def test_a_confirmed_take_down_posts_no_notice(standing):
    """Alex's review was approved and the bot asks for it to be taken down, confirmed. Once the
    queue runs the review is deleted, nothing is posted in channel 700 and no lapse is recorded."""
    await _take_down(standing, "confirmed")
    await run_queue(standing.bot)

    assert standing.channel.sent == []
    assert "lapsed" not in _log(standing)


@pytest.mark.xfail(strict=True, reason=NO_TAKE_DOWN)
async def test_a_refused_delete_stops_the_queue_and_a_discard_names_it(standing):
    """Discord refuses to delete report message R1. The bot asks for the review to be taken down,
    expired: the queue stops at R1's delete job. A league admin discards it: the change goes on,
    Q and R2 are deleted, R1 stands, the prompt's row is gone, and the log channel names R1 for
    deletion by hand."""
    channel = standing.channel
    channel.r1.fails = http_error(discord.Forbidden, status=403, text="Missing Permissions")

    await _take_down(standing, "expired")
    await run_queue(standing.bot)

    job = await stopped_job(standing.db_path)
    assert job is not None and job["name"] == "delete_message"

    await discard_job(standing.bot)
    await run_queue(standing.bot)

    assert channel.messages == [channel.r1]
    assert await _prompts(standing.db_path) == []
    assert str(channel.r1.id) in _log(standing) and "by hand" in _log(standing)
    [change] = await _changes(standing, TAKE_DOWN)
    assert change["state"] == "DONE"


@pytest.mark.xfail(strict=True, reason=NO_TAKE_DOWN)
async def test_a_take_down_whose_channel_is_gone_completes(standing):
    """Channel 700 has been deleted from the server, Alex's review with it. The bot asks for the
    review to be taken down, expired: the change completes without stopping the queue, posts no
    notice, and the prompt's row is gone."""
    _server(standing.bot)

    await _take_down(standing, "expired")
    await run_queue(standing.bot)

    assert await stopped_job(standing.db_path) is None
    assert standing.channel.sent == []
    assert await _prompts(standing.db_path) == []
    [change] = await _changes(standing, TAKE_DOWN)
    assert change["state"] == "DONE"


# ---------------------------------------------------------------------------
# season.review.post (C93)
# ---------------------------------------------------------------------------

POST = "season.review.post"
TIMER = "review_expiry"
PICTURES = [
    {"name": "Pro calendar", "division_id": 11, "kind": "calendar",
     "text": "\U0001f4c5 Pro calendar, as text"},
    {"name": "Pro lineup", "division_id": 11, "kind": "lineup",
     "text": "\U0001f465 Pro lineup, as text"},
]
REPORT = ["**Season 1 placements**", "Pro: Kimi to Ferrari, Lewis to Mercedes"]
QUESTION = "Approve these placements?"
EVIDENCE = {"season_id": 1, "fingerprint": "f00d"}
#: The view the builder's factory makes for the question: the season cog's `_ApproveView`.
APPROVE = SimpleNamespace(name="the Approve button")

NO_REVIEW_POST = (
    "#439: a season review is drawn and posted by the command at once, its pictures and report "
    "as the command's follow-ups, off the change queue"
)


class _Drawing:
    """The drawing the builder hands: draws a picture to a file, recording each picture asked
    and the thread it ran on. A picture named in `broken` will not draw."""

    def __init__(self, folder: Any) -> None:
        self.folder = folder
        self.asked: list[str] = []
        self.threads: list[Any] = []
        self.broken: set[str] = set()
        #: Where set, the channel whose events are counted at each drawing, in `seen`.
        self.channel: Any = None
        self.seen: list[int] = []

    def __call__(self, picture: Any) -> Any:
        import threading

        self.asked.append(picture["name"])
        self.threads.append(threading.current_thread())
        if self.channel is not None:
            self.seen.append(len(self.channel.events))
        if picture["name"] in self.broken:
            raise RuntimeError("the template's font could not be loaded")
        drawn = self.folder / f"{picture['kind']}-{picture['division_id']}.png"
        drawn.write_bytes(b"\x89PNG\r\n\x1a\n")
        return drawn


def _review_channel() -> Any:
    """Review channel 700, empty. What it is sent is kept in `sent` (content and keywords) and
    `events` ("post" or "delete", the message) in order; `refuse_files`, set, refuses any message
    carrying a file."""
    channel = history_channel(REVIEW_CHANNEL)
    channel.sent = []
    channel.events = []
    channel.refuse_files = None

    async def _send(content: str = "", **kwargs: Any) -> Any:
        if channel.refuse_files is not None and (kwargs.get("file") or kwargs.get("files")):
            raise channel.refuse_files
        message = post_in(channel, BOT_USER)
        message.content = content
        message.view = kwargs.get("view")
        message.picture = kwargs.get("file") or kwargs.get("files")
        channel.sent.append((content, kwargs))
        channel.events.append(("post", message))
        deleting = message.delete.side_effect

        async def _delete(*args: Any, **kw: Any) -> None:
            await deleting(*args, **kw)
            channel.events.append(("delete", message))

        message.delete = AsyncMock(side_effect=_delete)
        return message

    channel.send = AsyncMock(side_effect=_send)
    return channel


@pytest.fixture
async def posting(tmp_path):
    """The league, its empty review channel 700, and the drawing the builder hands; the queue is
    attached by `_attach`."""
    db_path = str(tmp_path / "queue.db")
    await run_migrations(db_path)
    await seed_server(db_path)
    bot = league_double(db_path)
    bot.user = BOT_USER
    channel = _review_channel()
    _server(bot, channel)
    folder = tmp_path / "drawn"
    folder.mkdir()
    return SimpleNamespace(db_path=db_path, bot=bot, channel=channel, clock=Clock(NOW),
                           drawing=_Drawing(folder), views=[])


def _attach(env: Any) -> None:
    """Register `season.review.post`, built by its factory with the test's drawing and a view
    factory recording each payload it makes the question's view for."""
    from leaguebot.core.services.season_review_changes import season_review_post_change

    def approve_view(payload: Any) -> Any:
        env.views.append(payload)
        return APPROVE

    attach_queue(env.bot, env.db_path, now=env.clock, types=(
        season_review_post_change(drawing=env.drawing, approve_view=approve_view),
    ))


def _payload() -> dict[str, Any]:
    return {"channel_id": REVIEW_CHANNEL, "season_id": 1, "reviewer_id": REVIEWER,
            "review": PLACEMENTS, "pictures": PICTURES, "report": REPORT,
            "question": QUESTION, "evidence": EVIDENCE}


async def _review(env: Any) -> Any:
    """Alex's `/season placements-review` asks the queue to post his review of season 1 in
    channel 700; the queue is not run."""
    interaction = member_interaction(env.bot, user=_alex())
    interaction.channel_id = REVIEW_CHANNEL
    await env.bot.change_queue.ask(POST, _payload(), interaction=interaction,
                                   what="posting the placements review")
    return interaction


def _posts(channel: Any) -> list[Any]:
    return [message for event, message in channel.events if event == "post"]


def _question(channel: Any) -> Any:
    return [m for m in _posts(channel) if m.content == QUESTION][-1]


@pytest.mark.xfail(strict=True, reason=NO_REVIEW_POST)
async def test_a_review_posts_the_drawing_notice_before_drawing_and_deletes_it_after(posting):
    """Alex's review of season 1 carries Pro's calendar and lineup pictures. Once the queue runs,
    "🎨 Drawing the graphics for this review — one moment." is posted in channel 700 before the
    first picture is drawn, and deleted after both pictures are posted and before the report's
    first section appears."""
    _attach(posting)
    posting.drawing.channel = posting.channel
    await _review(posting)
    await run_queue(posting.bot)

    channel = posting.channel
    notice = _posts(channel)[0]
    assert notice.content.startswith("\U0001f3a8 Drawing the graphics")
    assert posting.drawing.seen and min(posting.drawing.seen) >= 1
    order = channel.events
    deleted_at = order.index(("delete", notice))
    pictures = [i for i, (event, m) in enumerate(order) if event == "post" and m.picture]
    first_report = next(i for i, (event, m) in enumerate(order)
                        if event == "post" and m.content == REPORT[0])
    assert len(pictures) == 2 and max(pictures) < deleted_at < first_report


@pytest.mark.xfail(strict=True, reason=NO_REVIEW_POST)
async def test_each_review_picture_is_drawn_and_posted_by_a_job_of_its_own_in_order(posting):
    """Alex's review carries Pro's calendar, then its lineup. Once the queue runs, the calendar is
    drawn and posted, then the lineup, each by a `draw_and_post` job of its own; the report's two
    sections follow in order and then the question, carrying the Approve view the builder's
    factory made for this review."""
    _attach(posting)
    await _review(posting)
    await run_queue(posting.bot)

    [change] = await _changes(posting, POST)
    assert change["state"] == "DONE"
    names = [job["name"] for job in await step_rows(posting.db_path, change["id"])]
    assert names.count("draw_and_post") == 2
    assert posting.drawing.asked == ["Pro calendar", "Pro lineup"]
    posts = _posts(posting.channel)
    pictures = [m for m in posts if m.picture]
    assert len(pictures) == 2
    contents = [m.content for m in posts]
    assert contents[-3:] == [*REPORT, QUESTION]
    assert _question(posting.channel).view is APPROVE
    assert posting.views and posting.views[0]["season_id"] == 1


@pytest.mark.xfail(strict=True, reason=NO_REVIEW_POST)
async def test_a_refused_review_picture_falls_back_to_text_in_the_same_try(posting):
    """Discord refuses every message carrying a file in channel 700 (no Attach Files). Once the
    queue runs, each picture is posted as its text instead, in the same try: the queue does not
    stop, and the report and question follow."""
    posting.channel.refuse_files = http_error(discord.Forbidden, status=403,
                                              text="Missing Permissions")
    _attach(posting)
    await _review(posting)
    await run_queue(posting.bot)

    assert await stopped_job(posting.db_path) is None
    contents = [m.content for m in _posts(posting.channel)]
    assert PICTURES[0]["text"] in contents and PICTURES[1]["text"] in contents
    assert contents.index(PICTURES[0]["text"]) < contents.index(PICTURES[1]["text"])
    assert contents[-1] == QUESTION


@pytest.mark.xfail(strict=True, reason=NO_REVIEW_POST)
async def test_the_question_s_record_writes_the_prompt_row_with_every_report_id(posting):
    """Once Alex's review is posted, `season_review_prompts` holds one row for its question: season
    1, channel 700, Alex as reviewer, `/season placements-review`, the ids of both pictures and
    both report sections as its report, and the evidence the report was computed from."""
    _attach(posting)
    await _review(posting)
    await run_queue(posting.bot)

    question = _question(posting.channel)
    report = [m.id for m in _posts(posting.channel)
              if m is not question and not m.content.startswith("\U0001f3a8")]
    [row] = await _prompts(posting.db_path)
    assert row["message_id"] == question.id
    assert (row["season_id"], row["channel_id"], row["reviewer_id"], row["review"]) == (
        1, REVIEW_CHANNEL, REVIEWER, PLACEMENTS)
    assert sorted(json.loads(row["report_message_ids"])) == sorted(report)
    assert len(report) == 4
    assert json.loads(row["evidence"]) == EVIDENCE


@pytest.mark.xfail(strict=True, reason=NO_REVIEW_POST)
async def test_the_question_s_five_minutes_start_when_it_is_posted(posting):
    """The queue is stopped at 12:00 when Alex runs his review; it clears at 12:03. The question is
    posted at 12:03, and a job after it arms the review's expiry timer for its question at 12:08,
    five minutes from its posting, not from the command."""
    from datetime import timedelta

    from tests.support.change_queue import retry_job
    from tests.support.review_league import block_queue

    _attach(posting)
    holder = await block_queue(SimpleNamespace(bot=posting.bot, db_path=posting.db_path))
    await _review(posting)
    posting.clock.advance(minutes=3)
    holder["fail"] = False
    await retry_job(posting.bot)
    await run_queue(posting.bot)

    question = _question(posting.channel)
    armed = [call.args for call in posting.bot.scheduler_service.arm_once.call_args_list
             if call.args and call.args[0] == TIMER]
    assert armed == [(TIMER, question.id, NOW + timedelta(minutes=8))]
    [change] = await _changes(posting, POST)
    names = [job["name"] for job in await step_rows(posting.db_path, change["id"])]
    assert names.index("arm_expiry") > names.index("post_question")


@pytest.mark.xfail(strict=True, reason=NO_REVIEW_POST)
async def test_a_review_while_the_queue_is_stopped_is_acknowledged_and_posted_when_it_clears(
        posting):
    """The queue is stopped when Alex runs his review. He is answered at once, and nothing is
    posted in channel 700 while it stands. Once the stop clears the whole review is posted, the
    question last."""
    from tests.support.change_queue import retry_job
    from tests.support.review_league import block_queue

    _attach(posting)
    holder = await block_queue(SimpleNamespace(bot=posting.bot, db_path=posting.db_path))
    interaction = await _review(posting)
    await run_queue(posting.bot)

    assert acknowledgement(interaction)
    assert posting.channel.sent == []

    holder["fail"] = False
    await retry_job(posting.bot)
    await run_queue(posting.bot)

    assert [m.content for m in _posts(posting.channel)][-1] == QUESTION


@pytest.mark.xfail(strict=True, reason=NO_REVIEW_POST)
async def test_a_picture_that_will_not_draw_withholds_the_button_and_is_told_in_the_reviewer_s_reply(
        posting):
    """Pro's lineup will not draw (its template's font cannot be loaded). Once the queue runs the
    queue does not stop and the review is posted, but its question carries no Approve button, no
    view is made for it, and Alex's reply is updated naming "Pro lineup" as not drawn."""
    posting.drawing.broken = {"Pro lineup"}
    _attach(posting)
    interaction = await _review(posting)
    await run_queue(posting.bot)

    assert await stopped_job(posting.db_path) is None
    [change] = await _changes(posting, POST)
    assert change["state"] == "DONE"
    assert _question(posting.channel).view is None
    assert posting.views == []
    assert "Pro lineup" in updated_reply(interaction)


@pytest.mark.xfail(strict=True, reason=NO_REVIEW_POST)
async def test_a_drawing_fault_is_told_in_the_log_channel_once_the_reply_cannot_be_updated(
        posting):
    """Pro's lineup will not draw, and Alex's reply can no longer be updated (its token has run
    out). Once the queue runs, the log channel names "Pro lineup" as not drawn."""
    posting.drawing.broken = {"Pro lineup"}
    _attach(posting)
    interaction = await _review(posting)
    expired = http_error(discord.NotFound, status=404, text="Unknown Webhook")
    interaction.edit_original_response = AsyncMock(side_effect=expired)
    interaction.followup.send = AsyncMock(side_effect=expired)
    await run_queue(posting.bot)

    assert "Pro lineup" in _log(posting)


@pytest.mark.xfail(strict=True, reason=NO_REVIEW_POST)
async def test_the_review_s_drawing_runs_through_the_callable_the_builder_hands_off_the_event_loop(
        posting):
    """Once Alex's review runs, each of its pictures is drawn by the callable the builder handed
    the change's factory, given the picture as the review asked it, and on a thread other than
    the one the event loop runs on."""
    import threading

    _attach(posting)
    await _review(posting)
    await run_queue(posting.bot)

    assert posting.drawing.asked == ["Pro calendar", "Pro lineup"]
    assert posting.drawing.threads
    assert all(thread is not threading.current_thread() for thread in posting.drawing.threads)


# ---------------------------------------------------------------------------
# The restart (C98)
# ---------------------------------------------------------------------------

NO_RESTART_TAKE_DOWN = (
    "#439: a restart takes down each season review left standing itself, off the change queue"
)
CONFIG = "/season config-review"


@pytest.mark.xfail(strict=True, reason=NO_RESTART_TAKE_DOWN)
async def test_a_restart_asks_the_take_down_of_each_review_standing_before_the_queue_starts(
        standing):
    """Two reviews stand in channel 700 when the bot starts: Alex's placements review (report R1
    and R2, question Q) and his configuration review (question C). The restart's step deletes and
    posts nothing itself, and asks two take-downs of the queue. Once the queue runs, every message
    of both is deleted, each review's notice says it expired while the bot was restarting and pings
    Alex, and both prompts' rows are gone."""
    from leaguebot.__main__ import _recover_expired_review_prompts

    channel = standing.channel
    config_question = post_in(channel, BOT_USER)
    async with get_connection(standing.db_path) as db:
        await store_review_prompt(db, season_id=1, channel_id=REVIEW_CHANNEL,
                                  message_id=config_question.id, reviewer_id=REVIEWER,
                                  review=CONFIG)
        await db.commit()

    await _recover_expired_review_prompts(standing.bot)

    assert channel.deleted == [] and channel.sent == []
    assert len(await _changes(standing, TAKE_DOWN)) == 2

    await run_queue(standing.bot)

    assert channel.messages == []
    notices = [content for content, _ in channel.sent]
    assert len(notices) == 2
    assert all("restarting" in notice and f"<@{REVIEWER}>" in notice for notice in notices)
    assert any(PLACEMENTS in notice for notice in notices)
    assert any(CONFIG in notice for notice in notices)
    assert await _prompts(standing.db_path) == []


@pytest.mark.xfail(strict=True, reason=NO_RESTART_TAKE_DOWN)
async def test_a_review_whose_take_down_is_in_hand_is_not_asked_again_at_a_second_start(standing):
    """Discord refuses to delete Alex's report message R1. The bot starts and asks the review's
    take-down, which stops at R1's delete. The bot starts again with it still stopped: the
    restart's step asks no second take-down, and the review's row still stands."""
    from leaguebot.__main__ import _recover_expired_review_prompts

    standing.channel.r1.fails = http_error(discord.Forbidden, status=403,
                                           text="Missing Permissions")

    await _recover_expired_review_prompts(standing.bot)
    await run_queue(standing.bot)
    assert (await stopped_job(standing.db_path))["name"] == "delete_message"

    await _recover_expired_review_prompts(standing.bot)

    assert len(await _changes(standing, TAKE_DOWN)) == 1
    assert len(await _prompts(standing.db_path)) == 1
