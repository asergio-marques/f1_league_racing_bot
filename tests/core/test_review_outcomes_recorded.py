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

#: Alex, the league manager who ran the review.
REVIEWER = 4242
#: Sam, another league manager, who may not answer Alex's review.
BYSTANDER = 99

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
    """The approve view asks whether an approval of its season is in hand, and the mid-season
    confirm view whether a confirmation of its placements is, reading the change queue's tables
    (#439); these reviews hold no database, so the answer is given here: none."""
    monkeypatch.setattr(
        "leaguebot.core.cogs.season_cog.approval_in_hand", AsyncMock(return_value=False)
    )
    monkeypatch.setattr(
        "leaguebot.core.cogs.season_cog.confirmation_in_hand", AsyncMock(return_value=False),
        raising=False,
    )


def _member(user_id: int, name: str):
    member = MagicMock(spec=discord.Member)
    member.id = user_id
    member.display_name = name
    member.roles = []
    return member


# ── A review standing on the change queue (#439, slice 6b) ───────────────────
#
# A review's five minutes are a timer the builder registers (`review_expiry`, C97a): its callback,
# handed the question's id, asks the queue to take the review down, and the queue's jobs delete the
# review, post the expiry notice and record the lapse (C92). These build the league's queue with
# Alex's review of season 1 standing in channel 700 (report R1 and R2, question Q), recorded in
# `season_review_prompts`, and fire the timer's callback as the scheduler would.

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
    """Every line the log channel was sent, with the log's code marks around mentions taken off."""
    return [line.replace("`", "") for line in env.bot.log_channel.sent]


# ── A press of the review's button (#439, slice 6b) ─────────────────────────
#
# The button reads its review from the review's row, not from its view's memory: the question's
# `posted_at` for its five minutes, and the `evidence` its report was computed from for whether the
# season has changed (C93, C97a). It deletes and posts nothing itself: a review it ends is taken
# down by the queue (C92, C97).

#: The area of the season the tests change, as the review's evidence names it.
AREA = "Division Pro's rounds"


def _view_on(env, view_class, helper: str):
    """Alex's review's button, built as the question's view is, on a cog whose approval (or
    confirmation), *helper*, is recorded and goes through."""
    cog = MagicMock()
    cog.bot = env.bot
    # A press that reaches the helper goes through: for the approval, asked of the change queue.
    setattr(cog, helper, AsyncMock(return_value=True))
    return view_class(cog, REVIEWER), cog


def asking_the_take_down(env, cog, helper: str, *, then=None) -> None:
    """Make *helper* ask the review's take-down, ending confirmed, as the real approval (or
    confirmation) does once its gates pass, ahead of its work; then run *then*, if given, and go
    through. The button asks none itself."""
    from leaguebot.core.services.season_review_changes import CONFIRMED, take_review_down

    async def _worked(interaction, **_kwargs):
        await take_review_down(env.bot.change_queue, env.bot.db_path, interaction.message.id,
                               CONFIRMED)
        if then is not None:
            await then()
        return True

    setattr(cog, helper, AsyncMock(side_effect=_worked))


def _press_on(env, user_id: int = REVIEWER, name: str = "Alex"):
    """A press of the button on Alex's question Q, in channel 700, by *name*."""
    from tests.support.change_queue import member_interaction

    interaction = member_interaction(env.bot, user=_member(user_id, name))
    interaction.channel_id = REVIEW_CHANNEL
    interaction.message = env.channel.q
    return interaction


async def _season_since_the_review(env, monkeypatch, now: str = "as reviewed") -> None:
    """The review's row records the evidence its report was computed from, Pro's rounds "as
    reviewed"; the season as it stands now has Pro's rounds as *now*."""
    import json

    from leaguebot.core.db.database import get_connection
    from leaguebot.core.services import season_fingerprint_service as sfs

    async with get_connection(env.db_path) as db:
        await db.execute("UPDATE season_review_prompts SET evidence = ? WHERE message_id = ?",
                         (json.dumps({AREA: "as reviewed"}), env.channel.q.id))
        await db.commit()

    async def _now(*_args, **_kwargs):
        return sfs.SeasonFingerprint({AREA: now})

    monkeypatch.setattr(sfs, "take_fingerprint", _now)


async def _take_downs(env) -> list[dict]:
    import json

    from tests.support.change_queue import change_rows

    return [json.loads(row["payload"]) if isinstance(row["payload"], str) else row["payload"]
            for row in await change_rows(env.db_path) if row["kind"] == "season.review.take_down"]


def _lines(env, mark: str) -> list[str]:
    return [line for line in _queue_lines(env) if line.startswith(mark)]


def _expiry_timer_touched(env) -> list:
    """Every arming or disarming of a review's expiry timer."""
    scheduler = env.bot.scheduler_service
    return [call for call in scheduler.arm_once.call_args_list + scheduler.disarm.call_args_list
            if call.args and call.args[0] == "review_expiry"]


# ── A press refused ────────────────────────────────────────────────────────


@pytest.mark.parametrize("view_class,label,review,helper,verb", _BUTTONS)
async def test_a_press_by_someone_who_may_not_answer_is_recorded(
    tmp_path, view_class, label, review, helper, verb
):
    """Alex's review stands in channel 700, its question Q posted a moment ago. Sam, a league
    manager who is neither the reviewer nor a league admin, presses Alex's button: the case a
    public review creates, for only the reviewer or a league admin may answer it.

    Sam is answered as today, privately, and the review is left alone: nothing is deleted or
    posted, and no take-down is asked of the queue. The log channel gets one refusal line naming
    Sam, the button and the review it belongs to.
    """
    env = await _standing(tmp_path, review)
    view, cog = _view_on(env, view_class, helper)
    interaction = _press_on(env, BYSTANDER, "Sam")

    await view_class.approve(view, interaction, MagicMock())

    from tests.support.change_queue import acknowledgement

    getattr(cog, helper).assert_not_awaited()
    assert env.channel.deleted == [] and env.channel.sent == []
    assert await _take_downs(env) == []
    reply = acknowledgement(interaction)
    assert reply.startswith("⛔ Only the person who ran this review, or a league admin, can ")
    assert f"**Nothing has been {verb}.**" in reply
    assert interaction.response.send_message.await_args.kwargs["ephemeral"] is True
    lines = _queue_lines(env)
    assert len(lines) == 1, lines
    line = lines[0]
    assert line.startswith("⛔ "), line
    refused, _, reason = line.partition(" refused for Sam (<@99>) — ")
    assert reason, line
    assert label in refused and review in refused, line
    assert reason.startswith("Only the person who ran this review, or a league admin"), line


@pytest.mark.parametrize("view_class,label,review,helper,verb", _BUTTONS)
async def test_a_press_refused_because_the_season_changed_is_recorded_once(
    tmp_path, monkeypatch, view_class, label, review, helper, verb
):
    """Alex's review stands in channel 700 (report R1 and R2, question Q), posted a moment ago;
    its row records the evidence its report was computed from, Pro's rounds "as reviewed". Pro's
    rounds have changed since. Alex presses his own button.

    He is refused as today, privately, the reply naming what changed and the review's command to
    run again, and nothing is approved (or confirmed). The button deletes and posts nothing
    itself: it asks the queue to take the review down, ending refused. Once the queue runs, the
    review ends as an expired one does: Q, R1 and R2 deleted and the public notice pinging Alex
    posted, once. The log holds exactly one line for the press, the refusal, naming the button,
    the review and what changed; no lapse is recorded beside it.
    """
    from tests.support.change_queue import acknowledgement, run_queue

    env = await _standing(tmp_path, review)
    channel = env.channel
    await _season_since_the_review(env, monkeypatch, now="changed")
    view, cog = _view_on(env, view_class, helper)
    interaction = _press_on(env)

    await view_class.approve(view, interaction, MagicMock())

    getattr(cog, helper).assert_not_awaited()
    reply = acknowledgement(interaction)
    assert "has changed since this review" in reply
    assert f"**Nothing has been {verb}.**" in reply
    assert f"• {AREA}" in reply
    assert f"`{review}`" in reply
    assert channel.deleted == [] and channel.sent == []
    [payload] = await _take_downs(env)
    assert (payload["ending"], payload["question_id"]) == ("refused", channel.q.id)

    await run_queue(env.bot)

    assert {m.id for m in channel.deleted} == {channel.q.id, channel.r1.id, channel.r2.id}
    [notice] = channel.sent
    assert "<@4242> your review has expired" in notice
    [line] = _lines(env, "⛔ ")
    refused, _, reason = line.partition(" refused for Alex (<@4242>) — ")
    assert reason, line
    assert label in refused and review in refused, line
    assert AREA in reason, line
    assert _lines(env, "⌛ ") == [], _queue_lines(env)
    assert not any("lapsed" in line for line in _queue_lines(env)), _queue_lines(env)


# ── A review left to lapse ─────────────────────────────────────────────────


@pytest.mark.parametrize("view_class,label,review,helper,verb", _BUTTONS)
async def test_a_review_left_for_its_five_minutes_records_its_lapse(
    tmp_path, view_class, label, review, helper, verb
):
    """Alex runs a review and nobody presses its button; its five minutes run out, and the
    review's expiry timer fires for its question. The timer deletes and posts nothing itself: it
    asks the queue to take the review down. Once the queue runs, the question and its report are
    deleted and today's public notice pinging Alex and naming the review's command posted, and the
    log gets one lapse line naming the review and Alex as the member who started it, with beneath
    it what became of it (nothing done) and what to do next (run the review again)."""
    env = await _standing(tmp_path, review)
    channel = env.channel

    await _expiry_callback(env)(channel.q.id)

    assert channel.deleted == [] and channel.sent == []
    from tests.support.change_queue import run_queue

    await run_queue(env.bot)

    assert {m.id for m in channel.deleted} == {channel.q.id, channel.r1.id, channel.r2.id}
    [notice] = channel.sent
    assert "<@4242> your review has expired" in notice
    assert f"`{review}`" in notice
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


@pytest.mark.parametrize("view_class,label,review,helper,verb", _BUTTONS)
async def test_a_review_whose_press_failed_says_so_when_it_lapses(
    tmp_path, monkeypatch, view_class, label, review, helper, verb
):
    """Alex presses his button a moment after the question appears, and the bot hits a fault
    part-way, while reading the season to judge the press against the review's evidence (the
    press raises: 'the database is locked'), before anything is approved or asked of the queue.

    The review stays up and may be pressed again for the rest of its five minutes, as today:
    nothing is deleted, the approval (or confirmation) is never asked, and no take-down is asked.
    Left to lapse after that, its expiry timer fires and the queue takes it down; its lapse line
    says an earlier press failed and may have been partly done, to check the failure line before
    running the review again, and does not claim that nothing was done (core :105, :118, :282).
    A fault after the take-down is asked, in the approval's own asking, is not this case: the
    review is then already coming down, ending confirmed."""
    from leaguebot.core.services import season_fingerprint_service as sfs
    from tests.support.change_queue import run_queue

    env = await _standing(tmp_path, review)
    channel = env.channel
    await _season_since_the_review(env, monkeypatch)

    async def _locked(*_args, **_kwargs):
        raise RuntimeError("the database is locked")

    monkeypatch.setattr(sfs, "take_fingerprint", _locked)
    view, cog = _view_on(env, view_class, helper)

    with pytest.raises(RuntimeError):
        await view_class.approve(view, _press_on(env), MagicMock())

    getattr(cog, helper).assert_not_awaited()
    assert channel.deleted == [] and channel.sent == []
    assert await _take_downs(env) == []

    await _expiry_callback(env)(channel.q.id)
    await run_queue(env.bot)

    lines = _lines(env, "⌛ ")
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
# the posting of the review that carries it." The five minutes are the review's expiry timer,
# armed once when its question is posted (C97a): no press, a refused one included, re-arms it, and
# a press after them is judged by the question's `posted_at`.


@pytest.mark.parametrize("view_class,label,review,helper,verb", _BUTTONS)
async def test_a_refused_press_does_not_put_off_the_reviews_expiry(
    tmp_path, view_class, label, review, helper, verb
):
    """Alex's review stands in channel 700, posted a moment ago. Sam, who may not answer it,
    presses its button and is refused.

    Sam's press neither arms nor disarms the review's expiry timer, and asks no take-down. When
    the timer fires for Q, at five minutes from the posting, the queue takes the review down: Q,
    R1 and R2 are deleted and today's notice pinging Alex is posted, and the log holds Sam's
    refusal and then Alex's lapse line."""
    from tests.support.change_queue import run_queue

    env = await _standing(tmp_path, review)
    channel = env.channel
    view, cog = _view_on(env, view_class, helper)

    await view_class.approve(view, _press_on(env, BYSTANDER, "Sam"), MagicMock())

    getattr(cog, helper).assert_not_awaited()
    assert _expiry_timer_touched(env) == []
    assert await _take_downs(env) == []

    await _expiry_callback(env)(channel.q.id)
    await run_queue(env.bot)

    assert {m.id for m in channel.deleted} == {channel.q.id, channel.r1.id, channel.r2.id}
    [notice] = channel.sent
    assert "<@4242> your review has expired" in notice
    lines = [line for line in _queue_lines(env) if line[:2] in ("⛔ ", "⌛ ")]
    assert len(lines) == 2, _queue_lines(env)
    assert lines[0].startswith("⛔ ") and " refused for Sam (<@99>) — " in lines[0], lines
    head = lines[1].splitlines()[0]
    assert head.startswith("⌛ "), head
    assert head.replace("`", "").endswith(
        "lapsed unconfirmed (started by Alex (<@4242>))"
    ), head


@pytest.mark.parametrize("view_class,label,review,helper,verb", _BUTTONS)
async def test_a_press_after_the_five_minutes_confirms_nothing(
    tmp_path, view_class, label, review, helper, verb
):
    """Alex's review stands in channel 700, its row saying the question was posted five minutes
    and a second ago, by the queue's clock and the host's alike; nothing has yet taken it down.
    Alex presses its button.

    The press is not taken as a confirmation: nothing is approved or confirmed, and the button
    deletes and posts nothing in the channel. Alex is answered privately that nothing has been
    approved (or confirmed), and the log holds exactly one line, the refusal of his press.
    """
    from leaguebot.core.db.database import get_connection
    from tests.support.change_queue import acknowledgement

    env = await _standing(tmp_path, review)
    posted = min(datetime.now(timezone.utc), NOW) - timedelta(minutes=5, seconds=1)
    async with get_connection(env.db_path) as db:
        await db.execute("UPDATE season_review_prompts SET posted_at = ? WHERE message_id = ?",
                         (posted.isoformat(), env.channel.q.id))
        await db.commit()
    view, cog = _view_on(env, view_class, helper)
    interaction = _press_on(env)

    await view_class.approve(view, interaction, MagicMock())

    getattr(cog, helper).assert_not_awaited()
    assert env.channel.deleted == [] and env.channel.sent == []
    assert f"Nothing has been {verb}" in acknowledgement(interaction)
    assert interaction.response.send_message.await_args.kwargs["ephemeral"] is True
    lines = _queue_lines(env)
    assert len(lines) == 1, lines
    assert lines[0].startswith("⛔ ") and " refused for Alex (<@4242>) — " in lines[0], lines


# ── A press under way is not expired under it (#482, F11) ──────────────────
#
# Once a press is accepted, the review's take-down is asked, ending confirmed, before the approval
# (or confirmation) is worked (C97). The review's expiry timer firing after that posts no expiry
# notice and records no lapse: the review was answered.


@pytest.mark.parametrize("view_class,label,review,helper,verb", _BUTTONS)
async def test_the_timer_firing_while_a_press_is_under_way_leaves_the_review_to_the_press(
    tmp_path, monkeypatch, view_class, label, review, helper, verb
):
    """Alex presses his review's button a moment after the question appears, the season as the
    review described it, and while the approval (or confirmation) is being worked the review's
    five minutes run out and its expiry timer fires for Q.

    While the press is under way nothing is deleted or posted. Once the queue runs, the review is
    gone (Q, R1 and R2 deleted), but no expiry notice has been posted and no lapse recorded."""
    from tests.support.change_queue import run_queue

    env = await _standing(tmp_path, review)
    channel = env.channel
    await _season_since_the_review(env, monkeypatch)
    view, cog = _view_on(env, view_class, helper)
    seen: dict = {}

    async def _timer_fires():
        await _expiry_callback(env)(channel.q.id)
        seen["deleted"], seen["sent"] = list(channel.deleted), list(channel.sent)

    # A press that went through: the review's take-down is asked first, as the approval does.
    asking_the_take_down(env, cog, helper, then=_timer_fires)

    await view_class.approve(view, _press_on(env), MagicMock())
    await run_queue(env.bot)

    getattr(cog, helper).assert_awaited_once()
    assert seen == {"deleted": [], "sent": []}, seen
    assert {m.id for m in channel.deleted} == {channel.q.id, channel.r1.id, channel.r2.id}
    assert channel.sent == []
    assert _lines(env, "⌛ ") == [], _queue_lines(env)
    assert _lines(env, "⛔ ") == [], _queue_lines(env)


@pytest.mark.parametrize("view_class,label,review,helper,verb", _BUTTONS)
async def test_a_press_that_fails_after_the_timer_fired_leaves_the_review_to_expire_once_it_ends(
    tmp_path, monkeypatch, view_class, label, review, helper, verb
):
    """Alex presses his review's button, through Discord's own dispatch, a moment after the
    question appears. While the press is reading the season to judge it, the review's five
    minutes run out and its expiry timer fires for Q; the press then hits a fault ('the database
    is locked') and raises, before anything is approved or asked of the queue.

    While the press is under way nothing is deleted and no notice posted. Once it has ended and
    the queue runs, Q, R1 and R2 are deleted once each and today's public notice pinging Alex is
    posted, once. The log holds the press's failure line naming Alex, and one lapse line naming
    Alex that says an earlier press failed and may have been partly done: the lapse is told as
    the review stands when the take-down runs, not as it stood when the timer fired."""
    from leaguebot.core.services import season_fingerprint_service as sfs
    from tests.support.change_queue import run_queue

    env = await _standing(tmp_path, review)
    channel = env.channel
    await _season_since_the_review(env, monkeypatch)
    view, cog = _view_on(env, view_class, helper)
    seen: dict = {}

    async def _timer_fires_then_locked(*_args, **_kwargs):
        await _expiry_callback(env)(channel.q.id)
        seen["deleted"], seen["sent"] = list(channel.deleted), list(channel.sent)
        raise RuntimeError("the database is locked")

    monkeypatch.setattr(sfs, "take_fingerprint", _timer_fires_then_locked)
    button = next(item for item in view.children if isinstance(item, discord.ui.Button))

    # What discord.py does with a press: the button runs in a task, and a raise reaches the
    # view's `on_error`, which records the failure.
    pressed = view._dispatch_item(button, _press_on(env))
    assert pressed is not None
    await asyncio.wait_for(pressed, timeout=2)
    await run_queue(env.bot)

    getattr(cog, helper).assert_not_awaited()
    assert seen == {"deleted": [], "sent": []}, seen
    assert sorted(m.id for m in channel.deleted) == sorted([channel.q.id, channel.r1.id,
                                                            channel.r2.id])
    [notice] = channel.sent
    assert "<@4242> your review has expired" in notice
    [failure] = _lines(env, "❌ ")
    assert "failed for Alex (<@4242>)" in failure, failure
    [lapse] = _lines(env, "⌛ ")
    head, *beneath = lapse.splitlines()
    assert head.replace("`", "").endswith(
        "lapsed unconfirmed (started by Alex (<@4242>))"
    ), head
    detail = "\n".join(beneath).lower()
    assert "failed" in detail and "partly" in detail, detail
    assert "nothing has been" not in detail, detail


# ── Found in review of the hand-built part (#482) ───────────────────────────────


@pytest.mark.parametrize("view_class,label,review,helper,verb", _BUTTONS)
async def test_the_timer_firing_while_a_finished_press_clears_the_review_records_no_lapse(
    tmp_path, monkeypatch, view_class, label, review, helper, verb
):
    """Alex's press is taken and finishes, the review's take-down asked ending confirmed, and the
    review's five minutes run out before the queue has cleared it away: its expiry timer fires
    for Q. The review was answered, so once the queue runs it is gone with no expiry notice
    posted and no lapse recorded."""
    from tests.support.change_queue import run_queue

    env = await _standing(tmp_path, review)
    channel = env.channel
    await _season_since_the_review(env, monkeypatch)
    view, cog = _view_on(env, view_class, helper)
    asking_the_take_down(env, cog, helper)

    await view_class.approve(view, _press_on(env), MagicMock())
    await _expiry_callback(env)(channel.q.id)
    await run_queue(env.bot)

    getattr(cog, helper).assert_awaited_once()
    assert {m.id for m in channel.deleted} == {channel.q.id, channel.r1.id, channel.r2.id}
    assert channel.sent == []
    assert _lines(env, "⌛ ") == [], _queue_lines(env)


@pytest.mark.parametrize("view_class,label,review,helper,verb", _BUTTONS)
async def test_the_five_minutes_run_from_the_posting_of_the_question(
    tmp_path, monkeypatch, view_class, label, review, helper, verb
):
    """Alex's review took a while to draw and post: its view was built by the command, and the
    question Q went up only afterwards; the review's row says Q was posted a moment ago. Alex
    presses a moment after Q appears, the season as the review described it. The press is well
    inside five minutes from the posting, so it is taken: the approval (or confirmation) is
    worked, and nothing is refused."""
    env = await _standing(tmp_path, review)
    await _season_since_the_review(env, monkeypatch)
    view, cog = _view_on(env, view_class, helper)

    await view_class.approve(view, _press_on(env), MagicMock())

    getattr(cog, helper).assert_awaited_once()
    assert _lines(env, "⛔ ") == [], _queue_lines(env)


@pytest.mark.parametrize("view_class,label,review,helper,verb", _BUTTONS)
async def test_a_second_press_while_one_is_being_worked_is_refused(
    tmp_path, monkeypatch, view_class, label, review, helper, verb
):
    """F13. Alex presses his review's button a moment after Q appears, the season as the review
    described it, and presses it again (a double-click) while the first press is still being
    worked. The second press is refused, privately, and recorded; only the first is worked, so
    the season is approved (or confirmed) once."""
    from tests.support.change_queue import acknowledgement

    env = await _standing(tmp_path, review)
    await _season_since_the_review(env, monkeypatch)
    view, cog = _view_on(env, view_class, helper)
    started = asyncio.Event()
    release = asyncio.Event()

    async def _worked(*_args, **_kwargs):
        started.set()
        await release.wait()
        return True

    setattr(cog, helper, AsyncMock(side_effect=_worked))
    first = asyncio.create_task(view_class.approve(view, _press_on(env), MagicMock()))
    await asyncio.wait_for(started.wait(), timeout=2)

    second = _press_on(env)
    second_press = asyncio.create_task(view_class.approve(view, second, MagicMock()))
    answered, _ = await asyncio.wait({second_press}, timeout=1)
    release.set()
    await asyncio.wait_for(asyncio.gather(first, second_press), timeout=2)

    assert answered, "the second press waited on the first instead of being refused"
    getattr(cog, helper).assert_awaited_once()
    reply = acknowledgement(second)
    assert "already being answered" in reply, reply
    assert f"Nothing has been {verb}" in reply, reply
    assert second.response.send_message.await_args.kwargs["ephemeral"] is True
    refusals = _lines(env, "⛔ ")
    assert len(refusals) == 1, _queue_lines(env)
    assert " refused for Alex (<@4242>) — " in refusals[0], refusals


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
