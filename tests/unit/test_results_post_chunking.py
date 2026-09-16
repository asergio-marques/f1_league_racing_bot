"""Posting a results or standings message too long for Discord, and taking it down again.

Issue #208. `results_post_service.py` was at 56.1%. This file takes the pair of helpers that
make a long post work: `_split_content` / `_send_chunked`, which break it up, and
`_delete_with_continuations`, which puts it back together well enough to delete.

**A division of twenty with team names and gaps overruns Discord's 2,000 characters**, and a
message over the limit is rejected outright — the league would get no results at all, on
exactly the rounds with the most to report. The split therefore has to happen, and it has to
break on newlines: a standings table cut mid-row is unreadable, and the row it cut is the one
somebody is looking for.

**Only the first chunk's id is stored**, which is the whole reason the delete helper exists.
Taking a two-part post down by its stored id alone leaves the second half standing, and the
league is left reading half a standings table with no heading — worse than either the old post
or the new one alone.

**The delete stops at the first message that is not the bot's.** A driver who replied under the
standings must not have their message deleted when the standings are refreshed, and the loop
breaks rather than skipping — skipping would delete a bot message *past* somebody's reply,
which is the same fault with an extra step. `test_the_deletion_stops_at_someone_else_s_message`
is the one that holds it.

**Every Discord failure along the way is logged and swallowed.** These helpers run inside the
results posting path, and a message that cannot be deleted must not stop the new one being
posted — a league would then have neither.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from services.results_post_service import (  # noqa: E402
    _MSG_MAX,
    _delete_with_continuations,
    _send_chunked,
    _split_content,
)

BOT_ID = 1
SOMEBODY_ELSE = 2
ANCHOR_ID = 900001


# ---------------------------------------------------------------------------
# Splitting
# ---------------------------------------------------------------------------


def test_a_short_message_is_not_split():
    """Most posts fit. Splitting one that does not need it would cost a second message for
    nothing."""
    assert _split_content("short") == ["short"]


def test_a_message_at_the_limit_is_not_split():
    """Sits on the boundary — `_MSG_MAX` already leaves a margin under Discord's own
    limit, so the comparison is `>` and not `>=`."""
    text = "x" * _MSG_MAX

    assert _split_content(text) == [text]


def test_a_message_over_the_limit_is_split():
    text = "\n".join("x" * 100 for _ in range(50))

    chunks = _split_content(text)

    assert len(chunks) > 1
    assert all(len(c) <= _MSG_MAX for c in chunks)


def test_the_split_falls_on_a_line_break():
    """A standings table cut mid-row is unreadable, and the row it cut is the one somebody
    is looking for."""
    lines = [f"{n:02d}. Driver Number {n} — Some Team — +{n}.000" for n in range(200)]
    text = "\n".join(lines)

    chunks = _split_content(text)

    assert len(chunks) > 1
    for chunk in chunks:
        for line in chunk.splitlines():
            assert line in lines


def test_no_line_is_lost_in_the_split():
    """An off-by-one in the accumulator would drop whichever line sat on a boundary, and
    a missing driver in a standings table is a result nobody can query."""
    lines = [f"line {n}" for n in range(500)]
    text = "\n".join(lines)

    rejoined = "\n".join(_split_content(text))

    for line in lines:
        assert line in rejoined


def test_no_line_is_duplicated_in_the_split():
    lines = [f"unique-line-{n}" for n in range(500)]

    rejoined = "\n".join(_split_content("\n".join(lines)))

    assert rejoined.count("unique-line-250") == 1


def test_a_single_line_too_long_to_split_is_still_returned():
    """Nothing to break on. Returning an empty list would post nothing at all, which is
    worse than posting something Discord may trim."""
    chunks = _split_content("x" * (_MSG_MAX * 2))

    assert chunks
    assert chunks[0]


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------


def _channel():
    channel = MagicMock(spec=discord.TextChannel)
    sent: list = []

    async def _send(content, *args, **kwargs):
        message = MagicMock()
        message.id = 900000 + len(sent)
        message.content = content
        sent.append(message)
        return message

    channel.send = AsyncMock(side_effect=_send)
    channel._sent = sent
    return channel


async def test_a_short_post_is_one_message():
    channel = _channel()

    await _send_chunked(channel, "short")

    assert len(channel._sent) == 1


async def test_a_long_post_becomes_several_messages_in_order():
    channel = _channel()
    text = "\n".join(f"line {n}" for n in range(500))

    await _send_chunked(channel, text)

    assert len(channel._sent) > 1
    assert "line 0" in channel._sent[0].content
    assert "line 499" in channel._sent[-1].content


async def test_the_first_message_is_the_one_returned():
    """Its id is what gets persisted, and the anchor the deletion later works from — the
    last chunk would leave the heading undeleteable."""
    channel = _channel()
    text = "\n".join(f"line {n}" for n in range(500))

    first = await _send_chunked(channel, text)

    assert first is channel._sent[0]


# ---------------------------------------------------------------------------
# Deleting
# ---------------------------------------------------------------------------


def _message(message_id: int, author_id: int = BOT_ID):
    message = MagicMock()
    message.id = message_id
    message.author = MagicMock()
    message.author.id = author_id
    message.delete = AsyncMock(return_value=None)
    return message


def _delete_channel(anchor, following: list):
    """A channel whose history after *anchor* yields *following*."""
    channel = MagicMock(spec=discord.TextChannel)
    channel.fetch_message = AsyncMock(return_value=anchor)

    def _history(*args, **kwargs):
        async def _gen():
            for message in following:
                yield message

        return _gen()

    channel.history = MagicMock(side_effect=_history)
    return channel


async def test_a_single_message_post_is_deleted():
    anchor = _message(ANCHOR_ID)
    channel = _delete_channel(anchor, [])

    await _delete_with_continuations(channel, ANCHOR_ID, "standings")

    anchor.delete.assert_awaited_once()


async def test_the_continuations_go_with_the_anchor():
    """Only the first id is stored. Deleting by it alone leaves the league reading half a
    standings table with no heading."""
    anchor = _message(ANCHOR_ID)
    second = _message(ANCHOR_ID + 1)
    third = _message(ANCHOR_ID + 2)
    channel = _delete_channel(anchor, [second, third])

    await _delete_with_continuations(channel, ANCHOR_ID, "standings")

    anchor.delete.assert_awaited_once()
    second.delete.assert_awaited_once()
    third.delete.assert_awaited_once()


async def test_the_deletion_stops_at_someone_else_s_message():
    """A driver who replied under the standings must not lose their message when the
    standings are refreshed."""
    anchor = _message(ANCHOR_ID)
    mine = _message(ANCHOR_ID + 1)
    theirs = _message(ANCHOR_ID + 2, author_id=SOMEBODY_ELSE)
    channel = _delete_channel(anchor, [mine, theirs])

    await _delete_with_continuations(channel, ANCHOR_ID, "standings")

    mine.delete.assert_awaited_once()
    theirs.delete.assert_not_awaited()


async def test_the_deletion_does_not_reach_past_someone_else_s_message():
    """It breaks rather than skips. Skipping would delete a bot message *past* somebody's
    reply — the same fault with an extra step, and harder to notice."""
    anchor = _message(ANCHOR_ID)
    theirs = _message(ANCHOR_ID + 1, author_id=SOMEBODY_ELSE)
    mine_after = _message(ANCHOR_ID + 2)
    channel = _delete_channel(anchor, [theirs, mine_after])

    await _delete_with_continuations(channel, ANCHOR_ID, "standings")

    theirs.delete.assert_not_awaited()
    mine_after.delete.assert_not_awaited()


@pytest.mark.parametrize(
    "error",
    [
        discord.NotFound(MagicMock(), "gone"),
        discord.Forbidden(MagicMock(), "no perms"),
        discord.HTTPException(MagicMock(), "boom"),
    ],
    ids=["not-found", "forbidden", "http-error"],
)
async def test_an_anchor_that_cannot_be_fetched_is_survived(error, caplog):
    """The stored id may point at a message deleted by hand. A new post must still follow,
    or the league has neither the old one nor the new."""
    channel = MagicMock(spec=discord.TextChannel)
    channel.fetch_message = AsyncMock(side_effect=error)

    with caplog.at_level("WARNING"):
        await _delete_with_continuations(channel, ANCHOR_ID, "standings")

    assert "could not fetch" in caplog.text


async def test_a_failing_history_still_deletes_the_anchor(caplog):
    """The anchor is the heading, and is the one message that must go — without it the
    channel carries two headings for one round."""
    anchor = _message(ANCHOR_ID)
    channel = MagicMock(spec=discord.TextChannel)
    channel.fetch_message = AsyncMock(return_value=anchor)
    channel.history = MagicMock(
        side_effect=discord.HTTPException(MagicMock(), "rate limited")
    )

    with caplog.at_level("WARNING"):
        await _delete_with_continuations(channel, ANCHOR_ID, "standings")

    anchor.delete.assert_awaited_once()


async def test_a_continuation_that_will_not_delete_does_not_stop_the_anchor(caplog):
    anchor = _message(ANCHOR_ID)
    stubborn = _message(ANCHOR_ID + 1)
    stubborn.delete = AsyncMock(side_effect=discord.Forbidden(MagicMock(), "no perms"))
    channel = _delete_channel(anchor, [stubborn])

    with caplog.at_level("WARNING"):
        await _delete_with_continuations(channel, ANCHOR_ID, "standings")

    anchor.delete.assert_awaited_once()


async def test_an_anchor_that_will_not_delete_is_logged_not_raised(caplog):
    """Raising here would abandon the repost half done."""
    anchor = _message(ANCHOR_ID)
    anchor.delete = AsyncMock(side_effect=discord.Forbidden(MagicMock(), "no perms"))
    channel = _delete_channel(anchor, [])

    with caplog.at_level("WARNING"):
        await _delete_with_continuations(channel, ANCHOR_ID, "standings")

    assert "could not delete" in caplog.text


async def test_the_label_names_what_failed_in_the_log(caplog):
    """The same helper takes down results and standings alike, so the log has to say which
    — otherwise a maintainer reading it cannot tell what is missing from the channel."""
    channel = MagicMock(spec=discord.TextChannel)
    channel.fetch_message = AsyncMock(
        side_effect=discord.NotFound(MagicMock(), "gone")
    )

    with caplog.at_level("WARNING"):
        await _delete_with_continuations(channel, ANCHOR_ID, "results")

    assert "results" in caplog.text
