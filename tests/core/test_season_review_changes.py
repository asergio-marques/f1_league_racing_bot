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
  refuses it; one that will not draw is no stop: the question is withheld and the reviewer
  told why, in their reply or, once that can no longer be updated, the log channel.

The factories are `season_review_take_down_change()` and `season_review_post_change(drawing=...,
approve_view=..., scheduler=...)` in `leaguebot.core.services.season_review_changes`; the builder
registers them with the image module's drawing, the season cog's view factory and the scheduler
service, which `arm_expiry` arms its timer through (no step looks a service up on the bot).

The league: `league_double`'s, its review channel 700 found on the league's server, Alex (4242)
the reviewer of season 1's placements.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

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
    retry_job,
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
            reviewer_id=REVIEWER, review=PLACEMENTS, verb="approved",
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


async def test_an_expired_take_down_posts_the_expiry_notice_pinging_the_reviewer(standing):
    """Alex's review ran its five minutes and the bot asks for it to be taken down, expired. Once
    the queue runs the review is deleted, then one notice is posted in channel 700 pinging Alex
    alone and naming `/season placements-review` to run again, and the log channel records the
    review's lapse, started by Alex."""
    channel = standing.channel

    await _take_down(standing, "expired")
    await run_queue(standing.bot)

    # The notice is itself a message the channel now holds: the review's three are gone.
    assert [m for m in channel.messages if m in (channel.q, channel.r1, channel.r2)] == []
    [(notice, kwargs)] = channel.sent
    assert f"<@{REVIEWER}>" in notice and PLACEMENTS in notice
    mentions = kwargs.get("allowed_mentions")
    assert mentions is not None and mentions.roles is False and mentions.everyone is False
    assert "lapsed" in _log(standing) and PLACEMENTS in _log(standing)
    assert "Alex" in _log(standing)


async def test_a_confirmed_take_down_posts_no_notice(standing):
    """Alex's review was approved and the bot asks for it to be taken down, confirmed. Once the
    queue runs the review is deleted, nothing is posted in channel 700 and no lapse is recorded."""
    await _take_down(standing, "confirmed")
    await run_queue(standing.bot)

    assert standing.channel.sent == []
    assert "lapsed" not in _log(standing)


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

    # The expiry notice is itself a message the channel holds: of the review's three, R1 stands.
    assert [m for m in channel.messages if m in (channel.q, channel.r1, channel.r2)] == [channel.r1]
    assert await _prompts(standing.db_path) == []
    assert str(channel.r1.id) in _log(standing) and "by hand" in _log(standing)
    [change] = await _changes(standing, TAKE_DOWN)
    assert change["state"] == "DONE"


CANNOT_DISCARD_FORGET = (
    f"⛔ This job can't be discarded: without it <@{REVIEWER}>'s review would stay on the bot's "
    "record, and the next restart would tell them it expired and record it as lapsed. Fix what "
    "stopped it and press **Retry**."
)


async def test_forgetting_a_taken_down_review_cannot_be_discarded(standing):
    """Alex's review was approved and the bot asks for it to be taken down, confirmed. Its three
    messages are deleted, then forgetting the review's record fails and stops the queue. A league
    admin's Discard on it is refused, saying Alex's review would stay on the bot's record and the
    next restart would tell them it expired; the queue stays stopped there and the prompt's row
    stands. Once the fault is lifted, Retry forgets the review and the change is done."""
    with patch(
        "leaguebot.core.services.season_review_changes.mark_press",
        side_effect=RuntimeError("the press register is locked"),
    ):
        await _take_down(standing, "confirmed")
        await run_queue(standing.bot)
        job = await stopped_job(standing.db_path)
        assert job is not None and job["name"] == "forget"

        discarding = await discard_job(standing.bot)

        said = [str(call.args[0] if call.args else call.kwargs.get("content", ""))
                for call in (discarding.response.send_message.await_args_list
                             + discarding.followup.send.await_args_list)]
        assert CANNOT_DISCARD_FORGET in said
        job = await stopped_job(standing.db_path)
        assert job is not None and job["name"] == "forget"
        assert len(await _prompts(standing.db_path)) == 1

    await retry_job(standing.bot)

    assert await _prompts(standing.db_path) == []
    [change] = await _changes(standing, TAKE_DOWN)
    assert change["state"] == "DONE"


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
    """Register `season.review.post`, built by its factory with the test's drawing, a view
    factory recording each payload it makes the question's view for, and the bot's scheduler
    service, handed as the builder hands it."""
    from leaguebot.core.services.season_review_changes import season_review_post_change

    def approve_view(payload: Any) -> Any:
        env.views.append(payload)
        return APPROVE

    attach_queue(env.bot, env.db_path, now=env.clock, types=(
        season_review_post_change(drawing=env.drawing, approve_view=approve_view,
                                  scheduler=env.bot.scheduler_service),
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


async def test_a_posted_review_writes_one_line_naming_who_ran_it(posting):
    """Alex's `/season placements-review` is asked of the queue and the queue posts it, its
    question standing in channel 700. The log channel then holds one line naming Alex and
    `/season placements-review` as the review posted, so a later lapse or refusal reads against
    it (#482, criterion 6), and no refusal."""
    _attach(posting)
    await _review(posting)
    await run_queue(posting.bot)

    lines = [line.replace("`", "") for line in posting.bot.log_channel.sent]
    posted = [line for line in lines if PLACEMENTS in line and "Review posted" in line]
    assert len(posted) == 1, lines
    assert posted[0].startswith(f"Alex (<@{REVIEWER}>) | {PLACEMENTS}"), posted
    assert not any(line.startswith("⛔") for line in lines), lines


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


async def test_a_picture_that_will_not_draw_withholds_the_button_and_is_told_in_the_reviewer_s_reply(
        posting):
    """Pro's lineup will not draw (its template's font cannot be loaded). Once the queue runs the
    queue does not stop and the report is posted, but the question is withheld: none is posted, no
    view is made, no row is kept for it and no five minutes are armed. Alex's reply is updated
    naming "Pro lineup" as not drawn, and saying the season is not offered for approval."""
    posting.drawing.broken = {"Pro lineup"}
    _attach(posting)
    interaction = await _review(posting)
    await run_queue(posting.bot)

    assert await stopped_job(posting.db_path) is None
    [change] = await _changes(posting, POST)
    assert change["state"] == "DONE"
    assert QUESTION not in [m.content for m in _posts(posting.channel)]
    assert REPORT[0] in [m.content for m in _posts(posting.channel)]
    assert posting.views == []
    assert "Pro lineup" in updated_reply(interaction)
    assert "season is **not** offered for approval" in updated_reply(interaction)
    async with get_connection(posting.db_path) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM season_review_prompts")
        assert (await cursor.fetchone())[0] == 0


@pytest.mark.parametrize(("view", "withheld"), [
    ("mid_season", "the placements are **not** offered for confirmation"),
    ("configuration", "the configuration is **not** offered for confirmation"),
])
async def test_a_drawing_fault_words_what_is_withheld_by_the_review_s_family(
        posting, view, withheld):
    """Pro's lineup will not draw in a mid-season or a configuration review. Alex's reply says
    the placements (or the configuration) are not offered for confirmation, as the review's
    other faults say, and not that the season is not offered for approval."""
    posting.drawing.broken = {"Pro lineup"}
    _attach(posting)
    interaction = member_interaction(posting.bot, user=_alex())
    interaction.channel_id = REVIEW_CHANNEL
    await posting.bot.change_queue.ask(
        POST, {**_payload(), "view": view}, interaction=interaction,
        what="posting the review",
    )
    await run_queue(posting.bot)

    assert withheld in updated_reply(interaction)
    assert "season is **not** offered for approval" not in updated_reply(interaction)


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


@pytest.mark.parametrize("uncommitted", [False, True], ids=["placements", "mid_season"])
async def test_the_builder_s_drawing_of_a_review_lineup_obtains_missing_portraits(
        posting, monkeypatch, uncommitted):
    """The real change types are registered, as the builder registers them, and the drawing it
    hands `season.review.post` is kept. Asked, off the event loop, to draw Pro's lineup for
    Alex's review, it has the image module draw the lineup obtaining each seated driver's
    missing portrait (#407), so the review is not judged on a placeholder the league will not
    see (image specification, "The drawing of placements under review"). A mid-season review's
    lineup, asked with `include_uncommitted`, is drawn with its uncommitted drivers in it, as
    confirming will post it (#374); a placements review's, without."""
    import asyncio

    from leaguebot.core.services import season_review_changes
    from leaguebot.image.services import image_lineup_post

    handed: dict[str, Any] = {}
    built = season_review_changes.season_review_post_change

    def _capture(**kwargs: Any) -> Any:
        handed.update(kwargs)
        return built(**kwargs)

    monkeypatch.setattr(season_review_changes, "season_review_post_change", _capture)
    drawn = posting.drawing.folder / "lineup-11.png"
    drawn.write_bytes(b"\x89PNG\r\n\x1a\n")
    render = AsyncMock(return_value=SimpleNamespace(posts_image=True, png_paths=[drawn],
                                                    notices=[]))
    monkeypatch.setattr(image_lineup_post, "render_png", render)
    monkeypatch.setattr(image_lineup_post, "lineup_enabled", AsyncMock(return_value=True))
    attach_queue(posting.bot, posting.db_path, now=posting.clock)

    picture = dict(PICTURES[1])
    if uncommitted:
        picture["include_uncommitted"] = True

    await asyncio.to_thread(handed["drawing"], picture)

    assert render.await_count == 1
    assert render.await_args.kwargs["obtain_missing_portraits"] is True
    assert bool(render.await_args.kwargs.get("include_uncommitted")) is uncommitted


async def test_a_drawn_picture_s_render_directory_is_gone_once_its_job_completes(
        posting, tmp_path):
    """Alex's review carries two pictures, each drawn by the image module into a directory of its
    own (as `tempfile.mkdtemp(prefix="f1bot_render_")` makes it), the builder handing
    `discard_render` to the change. Once the queue runs, both pictures are posted and neither
    render directory is left behind."""
    import tempfile
    from pathlib import Path

    from leaguebot.core.services.season_review_changes import season_review_post_change
    from leaguebot.image.services.image_render_service import discard_render

    made: list[Path] = []

    def drawing(picture: Any) -> Path:
        folder = Path(tempfile.mkdtemp(prefix="f1bot_render_", dir=tmp_path))
        drawn = folder / f"{picture['kind']}.png"
        drawn.write_bytes(b"\x89PNG\r\n\x1a\n")
        made.append(drawn)
        return drawn

    attach_queue(posting.bot, posting.db_path, now=posting.clock, types=(
        season_review_post_change(drawing=drawing, approve_view=lambda payload: APPROVE,
                                  scheduler=posting.bot.scheduler_service,
                                  discard=discard_render),
    ))
    await _review(posting)
    await run_queue(posting.bot)

    assert len(made) == 2 and len([m for m in _posts(posting.channel) if m.picture]) == 2
    assert [path.parent.exists() for path in made] == [False, False]


async def test_the_builder_hands_the_review_the_image_module_s_discard_of_a_render(
        posting, monkeypatch):
    """The real change types are registered, as the builder registers them. The factory of
    `season.review.post` is handed the image module's `discard_render`, so that a picture's render
    directory goes with it."""
    from leaguebot.core.services import season_review_changes
    from leaguebot.image.services.image_render_service import discard_render

    handed: dict[str, Any] = {}
    built = season_review_changes.season_review_post_change

    def _capture(**kwargs: Any) -> Any:
        handed.update(kwargs)
        return built(**kwargs)

    monkeypatch.setattr(season_review_changes, "season_review_post_change", _capture)
    attach_queue(posting.bot, posting.db_path, now=posting.clock)

    assert handed["discard"] is discard_render


# ---------------------------------------------------------------------------
# The restart (C98)
# ---------------------------------------------------------------------------

CONFIG = "/season config-review"


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

    # Every message of both reviews is gone; the two notices just posted are what the channel holds.
    assert {channel.q, channel.r1, channel.r2, config_question}.isdisjoint(channel.messages)
    notices = [content for content, _ in channel.sent]
    assert len(notices) == len(channel.messages) == 2
    assert all("restarting" in notice and f"<@{REVIEWER}>" in notice for notice in notices)
    assert any(PLACEMENTS in notice for notice in notices)
    assert any(CONFIG in notice for notice in notices)
    assert await _prompts(standing.db_path) == []


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


# ---------------------------------------------------------------------------
# The question's button (C93–C97): what a press reads, and what it asks of the queue
# ---------------------------------------------------------------------------

#: The area of the season the tests change, as the review's evidence names it.
PRO_ROUNDS = "Division Pro's rounds"


def _approve_view(env: Any) -> tuple[Any, Any]:
    """An Approve view for Alex's review, built as the question's view is, on a cog whose approval
    (`_do_approve`) is recorded and goes through."""
    from leaguebot.core.cogs.season_cog import _ApproveView

    cog = MagicMock()
    cog.bot = env.bot
    cog._do_approve = AsyncMock(return_value=True)
    return _ApproveView(cog, REVIEWER), cog


def _press_on(env: Any, question: Any) -> Any:
    """Alex's press of the button on *question*, in channel 700."""
    interaction = member_interaction(env.bot, user=_alex())
    interaction.channel_id = REVIEW_CHANNEL
    interaction.message = question
    return interaction


async def _press_approve(view: Any, interaction: Any) -> None:
    await type(view).approve(view, interaction, MagicMock())


async def _posted_a_moment_ago(env: Any, question_id: int) -> None:
    """The question's row says it was posted just now, so its five minutes are still running."""
    async with get_connection(env.db_path) as db:
        await db.execute(
            "UPDATE season_review_prompts SET posted_at = ? WHERE message_id = ?",
            (datetime.now(timezone.utc).isoformat(), question_id),
        )
        await db.commit()


async def _evidence_of(env: Any, question_id: int, value: str) -> None:
    """The question's row records the evidence its report was computed from: Pro's rounds as
    *value*."""
    async with get_connection(env.db_path) as db:
        await db.execute(
            "UPDATE season_review_prompts SET evidence = ? WHERE message_id = ?",
            (json.dumps({PRO_ROUNDS: value}), question_id),
        )
        await db.commit()


def _season_now(monkeypatch: Any, value: str) -> None:
    """The season as it stands now: Pro's rounds as *value*."""
    from leaguebot.core.services import season_fingerprint_service as sfs

    async def _now(*_args: Any, **_kwargs: Any) -> Any:
        return sfs.SeasonFingerprint({PRO_ROUNDS: value})

    monkeypatch.setattr(sfs, "take_fingerprint", _now)


async def _take_downs(env: Any) -> list[dict[str, Any]]:
    return [json.loads(row["payload"]) for row in await _changes(env, TAKE_DOWN)]


def _refusals(env: Any) -> list[str]:
    return [line for line in env.bot.log_channel.sent if line.startswith("⛔")]


def _expiry_callback(env: Any) -> Any:
    """The callback the builder registered for the review's expiry timer."""
    [callback] = [call.args[1] for call in env.bot.scheduler_service.register_timer.call_args_list
                  if call.args and call.args[0] == TIMER]
    return callback


async def test_the_private_faults_stay_a_reply(standing):
    """Alex's placements review finds that Pro has no lineup channel. The fault is told to Alex
    alone, as a private follow-up of his command: nothing is posted in channel 700, nothing is
    kept as part of the review, and nothing is asked of the queue for it."""
    from leaguebot.core.cogs.season_cog import SeasonCog, _ReviewPoster

    interaction = member_interaction(standing.bot, user=_alex())
    poster = _ReviewPoster(interaction)

    await SeasonCog._send_channel_faults(
        poster, ["Pro has no lineup channel: set one with `/division lineup-channel`"]
    )

    calls = interaction.followup.send.await_args_list
    assert calls and all(call.kwargs.get("ephemeral") is True for call in calls)
    assert "Pro has no lineup channel" in calls[0].args[0]
    assert standing.channel.sent == []
    assert await change_rows(standing.db_path) == []


async def test_a_confirmation_asks_the_take_down_before_the_approval_and_the_review_is_gone_before_its_first_job(
        tmp_path, monkeypatch):
    """Season 3 is in placements, ready to approve, and the league admin's placements review of it
    stands in channel 610 (report R1 and R2, question Q), posted a moment ago, its row recording
    the season as it stands. The admin presses Approve, and the real approval runs past every gate.
    The review's take-down is asked of the queue before the season's approval, ending confirmed
    and naming Q, R1 and R2 in channel 610, so it runs on the queue ahead of the approval's jobs;
    the button deletes nothing itself. Run a job at a time, the queue takes the review down, its
    row with it, before any of the approval's jobs is done."""
    from leaguebot.core.cogs.season_cog import _ApproveView
    from leaguebot.core.services.season_approval_change import KIND as APPROVAL
    from leaguebot.core.services.season_fingerprint_service import take_fingerprint
    from tests.support.season_league import ADMIN_ID, SEASON_ID, Review, press_approve, season_league
    from tests.support.season_league import REVIEW_CHANNEL as CHANNEL_610

    league = await season_league(tmp_path, monkeypatch=monkeypatch)
    channel = history_channel(CHANNEL_610)
    channel.guild = league.guild
    league.channels[CHANNEL_610] = channel
    me = SimpleNamespace(id=league.bot.user.id, name="LeagueBot")
    r1, r2, q = (post_in(channel, me) for _ in range(3))
    evidence = (await take_fingerprint(league.bot, SEASON_ID)).areas
    async with get_connection(league.db_path) as db:
        await store_review_prompt(
            db, season_id=SEASON_ID, channel_id=CHANNEL_610, message_id=q.id,
            reviewer_id=ADMIN_ID, review=PLACEMENTS,
            posted_at=datetime.now(timezone.utc).isoformat(), report_message_ids=(r1.id, r2.id),
        )
        await db.execute("UPDATE season_review_prompts SET evidence = ? WHERE message_id = ?",
                         (json.dumps(evidence), q.id))
        await db.commit()
    review = Review(view=_ApproveView(league.cog, ADMIN_ID), prompt=q, report=[r1, r2])

    await press_approve(league, review)

    rows = await change_rows(league.db_path)
    take_downs = [row for row in rows if row["kind"] == TAKE_DOWN]
    approvals = [row for row in rows if row["kind"] == APPROVAL]
    assert (len(take_downs), len(approvals)) == (1, 1), (league.errors, rows)
    assert take_downs[0]["id"] < approvals[0]["id"]
    payload = json.loads(take_downs[0]["payload"])
    assert payload["ending"] == "confirmed"
    assert (payload["channel_id"], payload["question_id"]) == (CHANNEL_610, q.id)
    assert sorted(payload["report_ids"]) == sorted([r1.id, r2.id])
    assert channel.deleted == []

    async def _jobs_all_done(change_id: int) -> bool:
        return all(job["done_at"] for job in await step_rows(league.db_path, change_id))

    # A job at a time, until the take-down's last job is done: finishing the change is a unit of
    # its own, which the queue goes on from into the next change's first job.
    for _ in range(20):
        if await _jobs_all_done(take_downs[0]["id"]):
            break
        await run_queue(league.bot, steps=1)

    assert await _jobs_all_done(take_downs[0]["id"])
    assert channel.messages == []
    assert await _prompts(league.db_path) == []
    assert not [job for job in await step_rows(league.db_path, approvals[0]["id"])
                if job["done_at"]]


@pytest.mark.parametrize("ending", [
    pytest.param("expired", id="five_minutes"),
    pytest.param("refused", id="refused_press"),
])
async def test_the_five_minutes_and_a_refused_press_ask_the_take_down(standing, monkeypatch, ending):
    """Alex's review stands in channel 700. Either its five minutes run out (the review's expiry
    timer fires for Q), or Alex presses Approve a moment after it was posted but the season has
    changed since the report (Pro's rounds), so the press is refused. Either way nothing is
    deleted or posted at once: the review's take-down is asked of the queue as the bot's, naming
    channel 700, Q, R1, R2, Alex and `/season placements-review`, ending expired or refused."""
    channel = standing.channel
    if ending == "expired":
        await _expiry_callback(standing)(channel.q.id)
    else:
        await _posted_a_moment_ago(standing, channel.q.id)
        await _evidence_of(standing, channel.q.id, "before")
        _season_now(monkeypatch, "after")
        view, cog = _approve_view(standing)
        await _press_approve(view, _press_on(standing, channel.q))
        cog._do_approve.assert_not_awaited()

    assert channel.deleted == [] and channel.sent == []
    [payload] = await _take_downs(standing)
    assert payload["ending"] == ending
    assert (payload["channel_id"], payload["question_id"]) == (REVIEW_CHANNEL, channel.q.id)
    assert sorted(payload["report_ids"]) == sorted([channel.r1.id, channel.r2.id])
    assert (payload["reviewer_id"], payload["review"]) == (REVIEWER, PLACEMENTS)
    [change] = await _changes(standing, TAKE_DOWN)
    assert change["origin"] == "BOT"


async def test_approve_refuses_where_the_season_changed_while_the_review_waited(posting, monkeypatch):
    """The queue is stopped when Alex runs his review of season 1; its evidence is what the
    command computed the report from, Pro's rounds "before". While the review waits, Pro's
    rounds change. The stop clears and the review is posted. Alex presses Approve a moment after
    the question appears: he is refused, privately, the reply naming Pro's rounds as changed and
    saying nothing was approved; one ⛔ line is in the log channel; the approval is never asked;
    and the review's take-down is asked, ending refused."""
    from leaguebot.core.services.season_review_changes import season_review_take_down_change
    from tests.support.change_queue import register, retry_job
    from tests.support.review_league import block_queue

    _attach(posting)
    register(posting.bot, season_review_take_down_change())
    holder = await block_queue(SimpleNamespace(bot=posting.bot, db_path=posting.db_path))
    interaction = member_interaction(posting.bot, user=_alex())
    interaction.channel_id = REVIEW_CHANNEL
    await posting.bot.change_queue.ask(
        POST, {**_payload(), "evidence": {PRO_ROUNDS: "before"}}, interaction=interaction,
        what="posting the placements review",
    )
    _season_now(monkeypatch, "after")
    holder["fail"] = False
    await retry_job(posting.bot)
    await run_queue(posting.bot)

    question = _question(posting.channel)
    await _posted_a_moment_ago(posting, question.id)
    view, cog = _approve_view(posting)
    press = _press_on(posting, question)
    await _press_approve(view, press)

    cog._do_approve.assert_not_awaited()
    reply = acknowledgement(press)
    assert "has changed since this review" in reply and PRO_ROUNDS in reply
    assert "Nothing has been approved" in reply
    [line] = _refusals(posting)
    assert "Approve" in line
    [payload] = await _take_downs(posting)
    assert (payload["ending"], payload["question_id"]) == ("refused", question.id)


# ---------------------------------------------------------------------------
# An expired review's button (C97a)
# ---------------------------------------------------------------------------



def _refused_as_expired(env: Any, interaction: Any) -> None:
    """Alex was refused, privately, the review's five minutes being up, and the refusal is the one
    ⛔ line in the log channel, naming the button and its review."""
    reply = acknowledgement(interaction)
    assert "five minutes are up" in reply and "Nothing has been approved" in reply
    assert interaction.response.send_message.await_args.kwargs.get("ephemeral") is True
    [line] = _refusals(env)
    assert "Approve" in line and PLACEMENTS in line


async def test_a_press_on_a_review_whose_five_minutes_ran_is_refused_and_recorded(standing):
    """Alex's review stands in channel 700, its question Q posted on 7 September, so its five
    minutes ran long ago, but nothing has yet taken it down. Alex presses Approve on Q through a view just built for it. He is refused,
    privately, told the review's five minutes are up and nothing was approved; one ⛔ line naming
    Approve and `/season placements-review` is in the log channel; the approval is never asked;
    and the button itself deletes and posts nothing in channel 700."""
    view, cog = _approve_view(standing)
    interaction = _press_on(standing, standing.channel.q)

    await _press_approve(view, interaction)

    cog._do_approve.assert_not_awaited()
    _refused_as_expired(standing, interaction)
    assert standing.channel.deleted == [] and standing.channel.sent == []


@pytest.mark.parametrize("mark", ["UNDER_WAY", "TIMER_FIRED"])
async def test_a_late_press_beside_one_under_way_leaves_the_review_to_it(standing, mark):
    """Alex's review stands past its five minutes and his first press is being worked (or the
    timer has fired and waits for it). A second press is refused as expired, privately, and
    recorded, but asks no take-down: the first press settles how the review ends, so its approval
    cannot go through with the review taken down as an expired one."""
    from leaguebot.core.services import season_review_changes

    season_review_changes.mark_press(standing.channel.q.id, getattr(season_review_changes, mark))
    view, cog = _approve_view(standing)
    interaction = _press_on(standing, standing.channel.q)

    await _press_approve(view, interaction)

    cog._do_approve.assert_not_awaited()
    _refused_as_expired(standing, interaction)
    assert await _take_downs(standing) == []


async def test_a_press_refused_while_an_approval_is_in_hand_leaves_no_review_past_its_timer(
        standing, monkeypatch):
    """Alex's review stands in channel 700, posted a moment ago. He presses Approve while an
    approval of the season is in hand, and while the press is worked the review's five minutes
    run out: its timer fires and leaves the review to the press. The press is refused, the season
    being approved already, and the review's take-down is then asked, ending expired, so the
    review does not stand past its five minutes (core: "Upon their passing its message shall be
    deleted")."""
    from leaguebot.core.services.season_approval_change import ALREADY_BEING_APPROVED

    channel = standing.channel
    await _posted_a_moment_ago(standing, channel.q.id)
    fired = _expiry_callback(standing)

    async def _in_hand(*_args: Any, **_kwargs: Any) -> bool:
        await fired(channel.q.id)
        return True

    monkeypatch.setattr("leaguebot.core.cogs.season_cog.approval_in_hand", _in_hand)
    view, cog = _approve_view(standing)
    interaction = _press_on(standing, channel.q)

    await _press_approve(view, interaction)

    cog._do_approve.assert_not_awaited()
    assert acknowledgement(interaction) == ALREADY_BEING_APPROVED
    [payload] = await _take_downs(standing)
    assert (payload["ending"], payload["question_id"]) == ("expired", channel.q.id)


async def test_a_review_standing_after_a_restart_refuses_a_press_and_records_it(standing):
    """The bot restarts with Alex's review standing in channel 700, posted on 7 September. The
    restart's step routes the button on Q again, before the queue starts, rather than leaving it
    dead. Alex presses it before the review's take-down has run: he is refused, privately, its
    five minutes being up, the refusal is recorded in the log channel, and the button deletes
    nothing itself."""
    from leaguebot.__main__ import _recover_expired_review_prompts
    from leaguebot.core.cogs.season_cog import _ApproveView

    standing.bot.add_view = MagicMock()
    await _recover_expired_review_prompts(standing.bot)

    routed = [(call.args[0], call.kwargs.get("message_id"))
              for call in standing.bot.add_view.call_args_list
              if call.args and isinstance(call.args[0], _ApproveView)]
    [(view, message_id)] = routed
    assert message_id == standing.channel.q.id

    interaction = _press_on(standing, standing.channel.q)
    await _press_approve(view, interaction)

    _refused_as_expired(standing, interaction)
    assert standing.channel.deleted == []


async def test_a_press_hands_the_approval_what_remains_of_the_review_s_five_minutes(
        standing, monkeypatch):
    """Alex's review stands in channel 700, its question posted four minutes ago by its row, and
    the season is as its evidence describes it. Alex presses Approve on a view just built for it:
    the approval is asked once, handed as its deadline the question's posting time plus five
    minutes, about one minute on, so a backup question under test mode shares what is left of
    the review's five minutes rather than taking five of its own (core :281)."""
    from datetime import timedelta

    from leaguebot.core.cogs.season_cog import APPROVAL_WINDOW_SECONDS

    posted = datetime.now(timezone.utc) - timedelta(minutes=4)
    async with get_connection(standing.db_path) as db:
        await db.execute(
            "UPDATE season_review_prompts SET posted_at = ? WHERE message_id = ?",
            (posted.isoformat(), standing.channel.q.id),
        )
        await db.commit()
    await _evidence_of(standing, standing.channel.q.id, "as reviewed")
    _season_now(monkeypatch, "as reviewed")
    view, cog = _approve_view(standing)

    await _press_approve(view, _press_on(standing, standing.channel.q))

    cog._do_approve.assert_awaited_once()
    deadline = cog._do_approve.await_args.kwargs["deadline"]
    expected = posted + timedelta(seconds=APPROVAL_WINDOW_SECONDS)
    assert abs((deadline - expected).total_seconds()) < 2, (deadline, expected)


async def test_the_review_s_five_minutes_are_armed_by_a_job_after_the_question_and_ask_the_take_down(
        standing):
    """The builder registers the review's expiry timer, which the review's `arm_expiry` job arms
    for its question after it is posted. Fired for Alex's question Q, it reads Q's row again and
    asks the review's take-down once, as the bot, ending expired; nothing is deleted until the
    queue runs, and then the review is gone and its expiry notice posted pinging Alex. Fired again
    for Q, its row now gone, it asks nothing."""
    channel = standing.channel
    callback = _expiry_callback(standing)

    await callback(channel.q.id)

    assert channel.deleted == []
    [payload] = await _take_downs(standing)
    assert (payload["ending"], payload["question_id"]) == ("expired", channel.q.id)

    await run_queue(standing.bot)

    # The review's own messages are gone; the expiry notice just posted is the one left.
    assert {channel.q, channel.r1, channel.r2}.isdisjoint(channel.messages)
    [(notice, _kwargs)] = channel.sent
    assert f"<@{REVIEWER}>" in notice

    await callback(channel.q.id)

    assert len(await _changes(standing, TAKE_DOWN)) == 1
