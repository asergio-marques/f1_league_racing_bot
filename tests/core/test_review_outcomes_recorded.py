"""What the log channel holds for a season review's button: its refusals and its lapse (#482).

A season review ends in a public question with one button — ✅ Approve on a placements review
before the season starts, ✅ Confirm placements on one of a season being raced, ✅ Confirm
configuration on a configuration review. The core specification's "The record of what changed"
asks every outcome of it to be recorded: a press refused is one line naming the presser, the
button and its review, and a review left for its five minutes one lapse line naming the member
who ran it, with what became of it and what to do next.

A review refused because the season changed "shall end as an expired one does" ("The evidence
placements are confirmed upon"): the channel is cleared as a lapse clears it, but the log records
the press once, as the refusal. A press that hits a fault leaves the review standing, pressable
for the rest of its five minutes (owner, 2026-09-29), and the lapse it then records says an
earlier press failed rather than that nothing was done.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from leaguebot.core.cogs.season_cog import (
    _ApproveView,
    _ConfirmConfigurationView,
    _ConfirmMidSeasonPlacementsView,
)
from leaguebot.core.models.server_config import ServerConfig

#: Alex, the league manager who ran the review.
REVIEWER = 4242
#: Sam, another league manager, who may not answer Alex's review.
BYSTANDER = 99
ADMIN_ROLE = 444

#: Each review button: its view, its label without the mark, the review it belongs to, the
#: helper a press hands on to, and the verb its replies use.
_BUTTONS = [
    pytest.param(
        _ApproveView, "Approve", "/season placements-review", "_do_approve", "approved",
        id="approve",
    ),
    pytest.param(
        _ConfirmMidSeasonPlacementsView, "Confirm placements", "/season placements-review",
        "_do_confirm_mid_season_placements", "confirmed",
        id="confirm_placements",
    ),
    pytest.param(
        _ConfirmConfigurationView, "Confirm configuration", "/season config-review",
        "_do_confirm_configuration", "confirmed",
        id="confirm_configuration",
    ),
]


@pytest.fixture(autouse=True)
def _no_approval_in_hand(monkeypatch):
    """The approve view asks whether an approval of its season is in hand, reading the change
    queue's tables (#439); these reviews hold no database, so the answer is given here: none."""
    monkeypatch.setattr(
        "leaguebot.core.cogs.season_cog.approval_in_hand", AsyncMock(return_value=False)
    )


def _review(view_class, helper: str):
    """A review Alex ran, standing in the channel with its question bound, on the league's server.

    The bot finds Alex on the server by id, as a lapse (which has no interaction) names him, and
    every line it writes to the log channel is kept on `bot.output_router.post_log`.
    """
    cog = MagicMock()
    # A press that reaches the helper goes through: for the approval, asked of the change queue
    # (#439), which is the one press that takes the review down.
    setattr(cog, helper, AsyncMock(return_value=True))
    bot = cog.bot
    bot.db_path = "/nonexistent/nowhere.db"
    bot.config_service.get_server_config = AsyncMock(
        return_value=ServerConfig(
            server_id=7,
            interaction_role_id=222,
            league_admin_role_id=ADMIN_ROLE,
            interaction_channel_id=111,
            log_channel_id=333,
        )
    )
    bot.config_service.get_league_server_id = AsyncMock(return_value=7)
    alex = MagicMock()
    alex.id = REVIEWER
    alex.display_name = "Alex"
    guild = MagicMock()
    guild.get_member = MagicMock(side_effect=lambda uid: alex if uid == REVIEWER else None)
    bot.get_guild = MagicMock(return_value=guild)
    bot.output_router.post_log = AsyncMock()

    view = view_class(cog, REVIEWER)
    view._server_id = 7
    view._season_id = 1
    message = MagicMock()
    message.delete = AsyncMock()
    message.channel.send = AsyncMock()
    view._message = message
    return view, cog, message


def _member(user_id: int, name: str):
    member = MagicMock(spec=discord.Member)
    member.id = user_id
    member.display_name = name
    member.roles = []
    return member


def _press(cog, user_id: int = REVIEWER, name: str = "Alex"):
    """A press of the review's button, reading as Discord's does until it is answered."""
    interaction = MagicMock()
    interaction.guild_id = 7
    interaction.client = cog.bot
    interaction.user = _member(user_id, name)
    interaction.response.is_done = MagicMock(return_value=False)
    interaction.response.send_message = AsyncMock()
    interaction.followup.send = AsyncMock()
    return interaction


def _logged(cog) -> list[str]:
    return [call.args[0] for call in cog.bot.output_router.post_log.await_args_list]


def _changed_since_the_review(monkeypatch, area: str = "Division Pro's rounds") -> None:
    """The season as it stands differs from what the review described, in *area*."""
    from leaguebot.core.services import season_fingerprint_service as sfs

    async def _now(*_args, **_kwargs):
        return sfs.SeasonFingerprint({area: "after"})

    monkeypatch.setattr(sfs, "take_fingerprint", _now)


# ── A review standing on the change queue (#439, slice 6b) ───────────────────
#
# A review's five minutes are a timer the builder registers (`review_expiry`, C97a): its callback,
# handed the question's id, asks the queue to take the review down, and the queue's jobs delete the
# review, post the expiry notice and record the lapse (C92). These build the league's queue with
# Alex's review of season 1 standing in channel 700 (report R1 and R2, question Q), recorded in
# `season_review_prompts`, and fire the timer's callback as the scheduler would.

NO_EXPIRY_ON_THE_QUEUE = (
    "#439: a season review's five minutes run in its view's memory, and its on_timeout deletes "
    "the review and records the lapse itself, off the change queue"
)
REVIEW_CHANNEL = 700
NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


async def _standing(tmp_path, review: str):
    """The league's queue, and Alex's *review* of season 1 standing in channel 700: its report R1
    and R2, then its question Q, posted a moment ago. What the channel is sent is kept in `sent`;
    what is deleted, in `deleted`; every log line, in `bot.log_channel.sent`."""
    from types import SimpleNamespace

    from leaguebot.core.db.database import get_connection, run_migrations
    from tests.support.change_queue import (
        INTERACTION_CHANNEL_ID, LOG_CHANNEL_ID, SERVER_ID, Clock, attach_queue, history_channel,
        http_error, league_double, post_in, seed_server,
    )
    from tests.support.review_prompts import store_review_prompt

    db_path = str(tmp_path / "queue.db")
    await run_migrations(db_path)
    await seed_server(db_path)
    bot = league_double(db_path)
    me = SimpleNamespace(id=1, name="LeagueBot")
    bot.user = me
    channel = history_channel(REVIEW_CHANNEL)
    channel.sent = []

    async def _send(content: str = "", **_kwargs):
        channel.sent.append(content)
        return post_in(channel, me)

    channel.send = AsyncMock(side_effect=_send)
    channel.r1, channel.r2, channel.q = (post_in(channel, me) for _ in range(3))
    known = {LOG_CHANNEL_ID: bot.log_channel, INTERACTION_CHANNEL_ID: bot.interaction_channel,
             REVIEW_CHANNEL: channel}

    async def _fetch(channel_id: int):
        if channel_id in known:
            return known[channel_id]
        raise http_error(discord.NotFound, status=404, text="Unknown Channel")

    alex = _member(REVIEWER, "Alex")
    alex.mention = f"<@{REVIEWER}>"
    guild = MagicMock(spec=discord.Guild)
    guild.id = SERVER_ID
    guild.me = me
    guild.get_channel = MagicMock(side_effect=known.get)
    guild.get_member = MagicMock(side_effect=lambda uid: alex if uid == REVIEWER else None)
    channel.guild = guild
    bot.get_channel = MagicMock(side_effect=known.get)
    bot.fetch_channel = AsyncMock(side_effect=_fetch)
    bot.get_guild = MagicMock(return_value=guild)
    attach_queue(bot, db_path, now=Clock(NOW))
    async with get_connection(db_path) as db:
        await store_review_prompt(
            db, season_id=1, channel_id=REVIEW_CHANNEL, message_id=channel.q.id,
            reviewer_id=REVIEWER, review=review,
            report_message_ids=(channel.r1.id, channel.r2.id),
        )
        await db.execute(
            "UPDATE season_review_prompts SET posted_at = ? WHERE message_id = ?",
            (datetime.now(timezone.utc).isoformat(), channel.q.id),
        )
        await db.commit()
    return SimpleNamespace(db_path=db_path, bot=bot, channel=channel)


def _expiry_callback(env):
    """The callback the builder registered for the review's expiry timer."""
    [callback] = [call.args[1] for call in env.bot.scheduler_service.register_timer.call_args_list
                  if call.args and call.args[0] == "review_expiry"]
    return callback


def _queue_lines(env) -> list[str]:
    return list(env.bot.log_channel.sent)


# ── A press refused ────────────────────────────────────────────────────────


@pytest.mark.parametrize("view_class,label,review,helper,verb", _BUTTONS)
async def test_a_press_by_someone_who_may_not_answer_is_recorded(
    view_class, label, review, helper, verb
):
    """Sam, a league manager who is neither the reviewer nor a league admin, presses Alex's button.

    Sam is answered as today, privately, and the review is left alone; the log channel gets one
    refusal line naming Sam, the button and the review it belongs to.
    """
    view, cog, message = _review(view_class, helper)
    interaction = _press(cog, BYSTANDER, "Sam")

    await view_class.approve(view, interaction, MagicMock())

    getattr(cog, helper).assert_not_awaited()
    message.delete.assert_not_awaited()
    reply = interaction.response.send_message.await_args.args[0]
    assert reply.startswith("⛔ Only the person who ran this review, or a league admin, can ")
    assert f"**Nothing has been {verb}.**" in reply
    assert interaction.response.send_message.await_args.kwargs["ephemeral"] is True
    lines = _logged(cog)
    assert len(lines) == 1, lines
    line = lines[0]
    assert line.startswith("⛔ "), line
    refused, _, reason = line.partition(" refused for Sam (<@99>) — ")
    assert reason, line
    assert label in refused and review in refused, line
    assert reason.startswith("Only the person who ran this review, or a league admin"), line


@pytest.mark.parametrize("view_class,label,review,helper,verb", _BUTTONS)
async def test_a_press_refused_because_the_season_changed_is_recorded_once(
    monkeypatch, view_class, label, review, helper, verb
):
    """Alex presses his own button, but the season has changed since the review was posted.

    He is refused as today, and the review ends as an expired one does: question deleted and the
    public notice posted. The log holds exactly one line for it, the refusal, naming the button,
    the review and what changed; no lapse is recorded beside it.
    """
    view, cog, message = _review(view_class, helper)
    from leaguebot.core.services.season_fingerprint_service import SeasonFingerprint

    view._fingerprint = SeasonFingerprint({"Division Pro's rounds": "before"})
    _changed_since_the_review(monkeypatch)
    interaction = _press(cog)

    await view_class.approve(view, interaction, MagicMock())

    getattr(cog, helper).assert_not_awaited()
    reply = interaction.response.send_message.await_args.args[0]
    assert "has changed since this review" in reply
    assert f"**Nothing has been {verb}.**" in reply
    message.delete.assert_awaited_once()
    message.channel.send.assert_awaited_once()
    lines = _logged(cog)
    assert len(lines) == 1, lines
    line = lines[0]
    assert line.startswith("⛔ "), line
    refused, _, reason = line.partition(" refused for Alex (<@4242>) — ")
    assert reason, line
    assert label in refused and review in refused, line
    assert "Division Pro's rounds" in reason, line
    assert "lapsed" not in line


# ── A review left to lapse ─────────────────────────────────────────────────


@pytest.mark.xfail(strict=True, reason=NO_EXPIRY_ON_THE_QUEUE)
@pytest.mark.parametrize("view_class,label,review,helper,verb", _BUTTONS)
async def test_a_review_left_for_its_five_minutes_records_its_lapse(
    tmp_path, view_class, label, review, helper, verb
):
    """Alex runs a review and nobody presses its button; its five minutes run out, and the
    review's expiry timer fires for its question. The timer deletes and posts nothing itself: it
    asks the queue to take the review down. Once the queue runs, the question and its report are
    deleted and today's public notice pinging Alex posted, and the log gets one lapse line naming
    the review and Alex as the member who started it, with beneath it what became of it (nothing
    done) and what to do next (run the review again)."""
    env = await _standing(tmp_path, review)
    channel = env.channel

    await _expiry_callback(env)(channel.q.id)

    assert channel.deleted == [] and channel.sent == []
    from tests.support.change_queue import run_queue

    await run_queue(env.bot)

    assert {m.id for m in channel.deleted} == {channel.q.id, channel.r1.id, channel.r2.id}
    [notice] = channel.sent
    assert "<@4242> your review has expired" in notice
    lines = [line for line in _queue_lines(env) if line.startswith("⌛ ")]
    assert len(lines) == 1, _queue_lines(env)
    head, *beneath = lines[0].splitlines()
    assert review in head, head
    assert head.replace("`", "").endswith(
        "lapsed unconfirmed (started by Alex (<@4242>))"
    ), head
    detail = "\n".join(beneath)
    assert "nothing has been" in detail.lower(), detail
    assert review in detail and "again" in detail, detail


@pytest.mark.xfail(strict=True, reason=NO_EXPIRY_ON_THE_QUEUE)
@pytest.mark.parametrize("view_class,label,review,helper,verb", _BUTTONS)
async def test_a_review_whose_press_failed_says_so_when_it_lapses(
    tmp_path, monkeypatch, view_class, label, review, helper, verb
):
    """Alex presses his button a moment after the question appears, the season as the review
    described it, and the bot hits a fault part-way (the press raises: 'the database is locked').

    The review stays up and may be pressed again for the rest of its five minutes, as today:
    nothing is deleted and no take-down is asked. Left to lapse after that, its expiry timer fires
    and the queue takes it down; its lapse line says an earlier press failed and may have been
    partly done, to check the failure line before running the review again, and does not claim
    that nothing was done (core :105, :118, :282)."""
    import json

    from leaguebot.core.db.database import get_connection
    from leaguebot.core.services import season_fingerprint_service as sfs
    from tests.support.change_queue import change_rows, member_interaction, run_queue

    env = await _standing(tmp_path, review)
    channel = env.channel
    area = "Division Pro's rounds"
    async with get_connection(env.db_path) as db:
        await db.execute("UPDATE season_review_prompts SET evidence = ? WHERE message_id = ?",
                         (json.dumps({area: "as reviewed"}), channel.q.id))
        await db.commit()

    async def _as_reviewed(*_args, **_kwargs):
        return sfs.SeasonFingerprint({area: "as reviewed"})

    monkeypatch.setattr(sfs, "take_fingerprint", _as_reviewed)
    cog = MagicMock()
    cog.bot = env.bot
    setattr(cog, helper, AsyncMock(side_effect=RuntimeError("the database is locked")))
    view = view_class(cog, REVIEWER)
    interaction = member_interaction(env.bot, user=_member(REVIEWER, "Alex"))
    interaction.channel_id = REVIEW_CHANNEL
    interaction.message = channel.q

    with pytest.raises(RuntimeError):
        await view_class.approve(view, interaction, MagicMock())

    getattr(cog, helper).assert_awaited_once()
    assert channel.deleted == [] and channel.sent == []
    assert not [row for row in await change_rows(env.db_path)
                if row["kind"] == "season.review.take_down"]

    await _expiry_callback(env)(channel.q.id)
    await run_queue(env.bot)

    lines = [line for line in _queue_lines(env) if line.startswith("⌛ ")]
    assert len(lines) == 1, _queue_lines(env)
    head, *beneath = lines[0].splitlines()
    assert head.replace("`", "").endswith(
        "lapsed unconfirmed (started by Alex (<@4242>))"
    ), head
    detail = "\n".join(beneath).lower()
    assert "failed" in detail and "partly" in detail, detail
    assert "nothing has been" not in detail, detail


# ── Five minutes from posting, whatever is pressed (#482, F10) ─────────────
#
# The core specification's "Confirming placements": "The button shall stand for five minutes from
# the posting of the review that carries it." discord.py restarts a view's timer on every press
# that reaches a button, a refused one included, so a review pressed now and then would never
# expire. Here the review's five minutes are scaled down to two seconds, so its timer runs in earnest.



@pytest.mark.parametrize("view_class,label,review,helper,verb", _BUTTONS)
async def test_a_refused_press_does_not_put_off_the_reviews_expiry(
    monkeypatch, view_class, label, review, helper, verb
):
    """Alex's review is posted and its timer starts. Four-fifths of the way through (scaled: 1.6
    seconds into a two-second window) Sam, who may not answer it, presses its button and is
    refused.

    The review still expires at its five minutes from posting, not five minutes from Sam's press:
    the question is deleted, today's notice is posted, and the log holds Sam's refusal and then
    Alex's lapse line. The window is two seconds rather than one, and the waits leave 0.8 seconds
    either side of the deadline, so that a host under load cannot fail it.
    """
    from leaguebot.core.cogs import season_cog

    monkeypatch.setattr(season_cog, "APPROVAL_WINDOW_SECONDS", 2.0)
    view, cog, message = _review(view_class, helper)
    loop = asyncio.get_running_loop()
    posted = loop.time()
    # What discord.py does when the question is posted with its view: the timer starts.
    view._start_listening_from_store(MagicMock())
    try:
        await asyncio.sleep(1.6)
        button = next(item for item in view.children if isinstance(item, discord.ui.Button))
        await view._dispatch_item(button, _press(cog, BYSTANDER, "Sam"))

        # Due at two seconds from posting; a timer restarted by the press would run to 3.6.
        while not view.is_finished() and loop.time() < posted + 2.8:
            await asyncio.sleep(0.02)
        assert view.is_finished(), "the review outlived its window after a refused press"
        while not message.channel.send.await_count and loop.time() < posted + 3.6:
            await asyncio.sleep(0.02)
    finally:
        view.stop()

    getattr(cog, helper).assert_not_awaited()
    message.delete.assert_awaited_once()
    assert "<@4242> your review has expired" in message.channel.send.await_args.args[0]
    lines = _logged(cog)
    assert len(lines) == 2, lines
    assert lines[0].startswith("⛔ ") and " refused for Sam (<@99>) — " in lines[0], lines
    head = lines[1].splitlines()[0]
    assert head.startswith("⌛ "), head
    assert head.endswith("lapsed unconfirmed (started by Alex (<@4242>))"), head


@pytest.mark.parametrize("view_class,label,review,helper,verb", _BUTTONS)
async def test_a_press_after_the_five_minutes_confirms_nothing(
    view_class, label, review, helper, verb
):
    """Alex's review was posted more than five minutes ago (its window closed a second ago), and
    Alex presses its button.

    The press is not taken as a confirmation: nothing is approved or confirmed. Alex is answered
    privately that nothing has been approved (or confirmed), and the log holds exactly one line,
    the refusal of his press.
    """
    view, cog, message = _review(view_class, helper)
    view._deadline = datetime.now(timezone.utc) - timedelta(seconds=1)
    interaction = _press(cog)

    await view_class.approve(view, interaction, MagicMock())

    getattr(cog, helper).assert_not_awaited()
    reply = interaction.response.send_message.await_args
    assert f"Nothing has been {verb}" in reply.args[0], reply
    assert reply.kwargs["ephemeral"] is True
    lines = _logged(cog)
    assert len(lines) == 1, lines
    assert lines[0].startswith("⛔ ") and " refused for Alex (<@4242>) — " in lines[0], lines


# ── A press under way is not expired under it (#482, F11) ──────────────────
#
# While a press is being worked (under test mode the backup question, then the approval), the
# review's timer may fire. A review whose press is under way does not expire: the press records
# its own outcome, and the timer deletes nothing, posts no notice and records no lapse.


@pytest.mark.parametrize("view_class,label,review,helper,verb", _BUTTONS)
async def test_the_timer_firing_while_a_press_is_under_way_leaves_the_review_to_the_press(
    view_class, label, review, helper, verb
):
    """Alex presses his review's button, and while the approval (or confirmation) is being worked
    the review's five minutes run out and its timer fires, as discord.py fires it, in a task of
    its own.

    While the press is under way the timer deletes nothing and posts no expiry notice; once it
    ends, no notice has been posted and no lapse recorded, and the log holds only the press's own
    line.
    """
    view, cog, message = _review(view_class, helper)
    own_line = f"Alex (<@{REVIEWER}>) | {review} | Success"
    seen: dict = {}

    async def _worked(*_args, **_kwargs):
        # discord.py's timer: the view is stopped and `on_timeout` is run as a task.
        view._dispatch_timeout()
        [timer] = [
            task for task in asyncio.all_tasks()
            if task.get_name() == f"discord-ui-view-timeout-{view.id}"
        ]
        seen["timer"] = timer
        # Long enough for the timer to have done whatever it will do while the press is worked.
        await asyncio.wait({timer}, timeout=0.5)
        seen["deleted"] = message.delete.await_count
        seen["notices"] = message.channel.send.await_count
        seen["lines"] = list(_logged(cog))
        await cog.bot.output_router.post_log(own_line)
        # A press that went through: the approval was asked of the change queue (#439).
        return True

    getattr(cog, helper).side_effect = _worked

    await view_class.approve(view, _press(cog), MagicMock())
    await asyncio.wait_for(seen["timer"], timeout=2)

    assert seen["deleted"] == 0, "the review was deleted while its press was under way"
    assert seen["notices"] == 0, "the expiry notice was posted while the press was under way"
    assert seen["lines"] == [], seen["lines"]
    message.channel.send.assert_not_awaited()
    assert _logged(cog) == [own_line]


@pytest.mark.parametrize("view_class,label,review,helper,verb", _BUTTONS)
async def test_a_press_that_fails_after_the_timer_fired_leaves_the_review_to_expire_once_it_ends(
    view_class, label, review, helper, verb
):
    """Alex presses his review's button, through Discord's own dispatch, and while the approval
    (or confirmation) is being worked the review's five minutes run out and its timer fires, as
    discord.py fires it, in a task of its own. The press then hits a fault ('the database is
    locked') and raises.

    While the press is under way nothing is deleted and no notice posted. Once it has ended, the
    question and both report messages are deleted and today's public notice pinging Alex is
    posted, once. The log holds two lines: the press's failure line, and one lapse line naming
    Alex that says an earlier press failed and may have been partly done.
    """
    view, cog, message = _review(view_class, helper)
    report = [MagicMock(), MagicMock()]
    for part in report:
        part.delete = AsyncMock()
    view.carries(report)
    seen: dict = {}

    def _deleted() -> int:
        return message.delete.await_count + sum(part.delete.await_count for part in report)

    async def _worked(*_args, **_kwargs):
        # discord.py's timer: the view is stopped and `on_timeout` is run as a task.
        view._dispatch_timeout()
        [timer] = [
            task for task in asyncio.all_tasks()
            if task.get_name() == f"discord-ui-view-timeout-{view.id}"
        ]
        seen["timer"] = timer
        # Long enough for the timer to have done whatever it will do while the press is worked.
        await asyncio.wait({timer}, timeout=0.5)
        seen["deleted"] = _deleted()
        seen["notices"] = message.channel.send.await_count
        seen["lines"] = list(_logged(cog))
        raise RuntimeError("the database is locked")

    getattr(cog, helper).side_effect = _worked
    button = next(item for item in view.children if isinstance(item, discord.ui.Button))

    # What discord.py does with a press: the button runs in a task, and a raise reaches
    # the view's `on_error`, which records the failure.
    pressed = view._dispatch_item(button, _press(cog))
    assert pressed is not None
    await asyncio.wait_for(pressed, timeout=2)
    await asyncio.wait_for(seen["timer"], timeout=2)
    # Whatever the end of the press sets off is given a moment to run its course.
    loop = asyncio.get_running_loop()
    settled_by = loop.time() + 1.0
    while loop.time() < settled_by and (
        not message.channel.send.await_count or len(_logged(cog)) < 2
    ):
        await asyncio.sleep(0.02)

    assert seen["deleted"] == 0, "the review was deleted while its press was under way"
    assert seen["notices"] == 0, "the expiry notice was posted while the press was under way"
    assert not any(line.startswith("⌛ ") for line in seen["lines"]), seen["lines"]
    message.delete.assert_awaited_once()
    for part in report:
        part.delete.assert_awaited_once()
    message.channel.send.assert_awaited_once()
    assert "<@4242> your review has expired" in message.channel.send.await_args.args[0]
    lines = _logged(cog)
    assert len(lines) == 2, lines
    [failure] = [line for line in lines if line.startswith("❌ ")]
    assert "failed for Alex (<@4242>)" in failure, failure
    [lapse] = [line for line in lines if line.startswith("⌛ ")]
    head, *beneath = lapse.splitlines()
    assert head.endswith("lapsed unconfirmed (started by Alex (<@4242>))"), head
    detail = "\n".join(beneath).lower()
    assert "failed" in detail and "partly" in detail, detail
    assert "nothing has been" not in detail, detail


# ── Found in review of the hand-built part (#482) ───────────────────────────────


@pytest.mark.parametrize("view_class,label,review,helper,verb", _BUTTONS)
async def test_the_timer_firing_while_a_finished_press_clears_the_review_records_no_lapse(
    view_class, label, review, helper, verb
):
    """Alex's press is worked and finishes, and the review's five minutes run out while its report
    is being cleared away. The review was answered, so the timer posts no expiry notice and
    records no lapse: the log holds only what the press itself wrote."""
    view, cog, message = _review(view_class, helper)
    report = [MagicMock(), MagicMock()]

    async def _deleted_as_the_timer_fires():
        view._dispatch_timeout()
        # A delete is a request to Discord: the timer's task runs while it is awaited.
        await asyncio.sleep(0.01)

    async def _a_slow_delete():
        await asyncio.sleep(0.05)

    report[0].delete = AsyncMock(side_effect=_deleted_as_the_timer_fires)
    report[1].delete = AsyncMock()
    # The question's own delete takes as long as a request to Discord does.
    message.delete = AsyncMock(side_effect=_a_slow_delete)
    view.carries(report)

    await view_class.approve(view, _press(cog), MagicMock())
    # Whatever the timer set off is given a moment to run its course.
    await asyncio.sleep(0.2)

    getattr(cog, helper).assert_awaited_once()
    message.channel.send.assert_not_awaited()
    assert not any(line.startswith("⌛ ") for line in _logged(cog)), _logged(cog)


@pytest.mark.parametrize("view_class,label,review,helper,verb", _BUTTONS)
async def test_the_five_minutes_run_from_the_posting_of_the_question(
    view_class, label, review, helper, verb
):
    """Alex's review took longer than five minutes to draw and post: its view was built before
    the report, and the question went up only afterwards. Alex presses a moment after the
    question appears. The press is well inside five minutes from the posting, so it is taken:
    the approval (or confirmation) is worked, and nothing is refused."""
    view, cog, message = _review(view_class, helper)
    # Built long enough ago that five minutes from building have already gone by.
    view._deadline = datetime.now(timezone.utc) - timedelta(seconds=1)

    await view.bind(message)
    await view_class.approve(view, _press(cog), MagicMock())

    getattr(cog, helper).assert_awaited_once()
    assert not any(line.startswith("⛔ ") for line in _logged(cog)), _logged(cog)


@pytest.mark.parametrize("view_class,label,review,helper,verb", _BUTTONS)
async def test_a_second_press_while_one_is_being_worked_is_refused(
    view_class, label, review, helper, verb
):
    """F13. Alex presses his review's button, and presses it again (a double-click) while the
    first press is still being worked. The second press is refused, privately, and recorded;
    only the first is worked, so the season is approved (or confirmed) once."""
    view, cog, message = _review(view_class, helper)
    started = asyncio.Event()
    release = asyncio.Event()

    async def _worked(*_args, **_kwargs):
        started.set()
        await release.wait()

    getattr(cog, helper).side_effect = _worked
    first = asyncio.create_task(view_class.approve(view, _press(cog), MagicMock()))
    await asyncio.wait_for(started.wait(), timeout=2)

    second = _press(cog)
    second_press = asyncio.create_task(view_class.approve(view, second, MagicMock()))
    answered, _ = await asyncio.wait({second_press}, timeout=1)
    release.set()
    await asyncio.wait_for(asyncio.gather(first, second_press), timeout=2)

    assert answered, "the second press waited on the first instead of being refused"
    getattr(cog, helper).assert_awaited_once()
    reply = second.response.send_message.await_args
    assert "already being answered" in reply.args[0], reply
    assert f"Nothing has been {verb}" in reply.args[0], reply
    assert reply.kwargs["ephemeral"] is True
    refusals = [line for line in _logged(cog) if line.startswith("⛔ ")]
    assert len(refusals) == 1, _logged(cog)
    assert " refused for Alex (<@4242>) — " in refusals[0], refusals


@pytest.mark.xfail(strict=True, reason=NO_EXPIRY_ON_THE_QUEUE)
@pytest.mark.parametrize("view_class,label,review,helper,verb", _BUTTONS)
async def test_a_review_expired_twice_at_once_posts_one_notice(
    tmp_path, view_class, label, review, helper, verb
):
    """At the very end of Alex's five minutes the review's expiry timer fires twice at once, so
    two take-downs are asked together. The queue takes one: once it runs, the question is deleted
    once, the public notice posted once, and one lapse line at most is recorded."""
    from tests.support.change_queue import change_rows, run_queue

    env = await _standing(tmp_path, review)
    channel = env.channel
    callback = _expiry_callback(env)

    await asyncio.gather(callback(channel.q.id), callback(channel.q.id))
    await run_queue(env.bot)

    assert len([row for row in await change_rows(env.db_path)
                if row["kind"] == "season.review.take_down"]) == 1
    assert [m.id for m in channel.deleted].count(channel.q.id) == 1
    assert len(channel.sent) == 1
    lapses = [line for line in _queue_lines(env) if line.startswith("⌛ ")]
    assert len(lapses) <= 1, _queue_lines(env)


NO_CONFIRMATION_TELL = (
    "#439: a confirmation that cannot be sent privately tells its channel the fallback directly, "
    "off the queue, with no channel.tell change asked"
)


@pytest.mark.xfail(strict=True, reason=NO_CONFIRMATION_TELL)
async def test_a_private_confirmation_that_fails_asks_the_channel_to_be_told(tmp_path):
    """A mid-season confirmation is committed, and its private confirmation to the reviewer is
    refused (the interaction's token has lapsed). Nothing is posted in the channel at once: a
    `channel.tell` change is asked of the queue, naming that channel (707) and the fallback line.
    When the queue runs, the fallback line is posted there, once."""
    from types import SimpleNamespace

    from leaguebot.core.cogs.season_cog import _confirm_privately
    from leaguebot.core.db.database import run_migrations
    from tests.support.change_queue import (
        attach_queue,
        change_rows,
        http_error,
        league_double,
        member_interaction,
        run_queue,
        seed_server,
        text_channel,
    )

    db_path = str(tmp_path / "queue.db")
    await run_migrations(db_path)
    await seed_server(db_path)
    bot = league_double(db_path)
    review_channel = text_channel(707)
    bot.get_channel = MagicMock(side_effect={707: review_channel}.get)
    bot.fetch_channel = AsyncMock(return_value=review_channel)
    attach_queue(bot, db_path, now=datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc))
    lapsed = http_error(discord.HTTPException, status=401, text="Invalid Webhook Token")
    press = member_interaction(bot)
    press.response.is_done = MagicMock(return_value=True)
    press.followup.send = AsyncMock(side_effect=lapsed)
    press.channel = review_channel
    press.channel_id = 707
    fallback = "Placements were confirmed; see the log channel for what was confirmed."

    await _confirm_privately(press, "✅ Placements confirmed.", fallback=fallback)

    assert review_channel.sent == []
    [tell] = [SimpleNamespace(**row) for row in await change_rows(db_path)
              if row["kind"] == "channel.tell"]
    assert "707" in str(tell.payload) and fallback in str(tell.payload)

    await run_queue(bot)

    assert review_channel.sent == [fallback]
