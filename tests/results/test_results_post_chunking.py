"""Posting a results or standings message too long for Discord, and taking it down again.

Issue #208. `results_post_service.py` was at 56.1%. This file takes the pair of helpers that
make a long post work: `_split_content` / `_send_chunked`, which break it up, and
`_delete_posting`, which takes down exactly the messages a posting recorded.

**A division of twenty with team names and gaps overruns Discord's 2,000 characters**, and a
message over the limit is rejected outright — the league would get no results at all, on
exactly the rounds with the most to report. The split therefore has to happen, and it has to
break on newlines: a standings table cut mid-row is unreadable, and the row it cut is the one
somebody is looking for.

**Every chunk's id is now stored, and the walk that guessed them is gone** (#345). Only the
first id was once recorded, and the rest were found again by walking forward over bot-authored
messages. That walk could not tell this posting's continuation from the *next posting down*, so
it was wrong wherever two of the bot's own postings sat together — which the amendment replay
makes ordinary, posting every replacement before destroying any original. It deleted the
replacements as it went. `_delete_posting` removes exactly the messages the posting recorded,
and a posting whose list was never recorded loses its anchor alone: one message too few rather
than four too many, and never a message belonging to somebody else.

**Every Discord failure along the way is logged and swallowed.** These helpers run inside the
results posting path, and a message that cannot be deleted must not stop the new one being
posted — a league would then have neither.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from leaguebot.results.services.results_post_service import (
    _MSG_MAX,
    _delete_posting,
    _ids_json,
    _parse_ids,
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


async def test_every_message_is_returned_with_the_anchor_first():
    """All of them, so all of them can be recorded, and the anchor at the head of the list.

    The anchor's id is what is persisted as `results_message_id` and what an older row's
    deletion works back from; returning the last chunk in its place would leave the heading
    undeleteable. The rest are returned so that deleting this posting later removes exactly
    the messages it created, rather than walking forward over whatever follows and taking the
    next posting down with it (#345).
    """
    channel = _channel()
    text = "\n".join(f"line {n}" for n in range(500))

    sent = await _send_chunked(channel, text)

    assert len(sent) > 1
    assert sent == channel._sent
    assert sent[0] is channel._sent[0]


async def test_a_short_post_returns_the_one_message_it_sent():
    """The single-chunk case is a list of one, not a bare message.

    Every caller persists `_ids_json(sent)`, so a lone message that came back unwrapped would
    be recorded as a list of its attributes rather than of its id.
    """
    channel = _channel()

    sent = await _send_chunked(channel, "short")

    assert sent == channel._sent
    assert len(sent) == 1


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

    await _delete_posting(channel, ANCHOR_ID, [ANCHOR_ID], "standings")

    anchor.delete.assert_awaited_once()


async def test_the_recorded_continuations_go_with_the_anchor():
    """Deleting by the anchor alone left the league reading half a standings table with no
    heading, which is why every chunk's id is recorded."""
    messages = [_message(ANCHOR_ID), _message(ANCHOR_ID + 1), _message(ANCHOR_ID + 2)]
    channel = _deletable_channel(messages)

    await _delete_posting(
        channel, ANCHOR_ID, [ANCHOR_ID, ANCHOR_ID + 1, ANCHOR_ID + 2], "standings"
    )

    assert channel._deleted == [ANCHOR_ID, ANCHOR_ID + 1, ANCHOR_ID + 2]


async def test_nothing_that_merely_follows_the_posting_is_deleted():
    """**The regression the whole change exists to prevent** (#345).

    The walk this replaced took every bot-authored message after the anchor, so a driver's reply
    stopped it — but the bot's own next posting did not, and under produce-then-destroy that is
    the replacement it has just put up. Deleting only what was recorded cannot reach either.
    """
    anchor = _message(ANCHOR_ID)
    someone_elses = _message(ANCHOR_ID + 1, author_id=SOMEBODY_ELSE)
    the_replacement = _message(ANCHOR_ID + 2)
    channel = _delete_channel(anchor, [someone_elses, the_replacement])

    await _delete_posting(channel, ANCHOR_ID, [ANCHOR_ID], "standings")

    someone_elses.delete.assert_not_awaited()
    the_replacement.delete.assert_not_awaited()
    channel.history.assert_not_called()


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
        await _delete_posting(channel, ANCHOR_ID, [ANCHOR_ID], "standings")

    assert "could not fetch" in caplog.text


async def test_a_chunk_that_will_not_delete_does_not_stop_the_rest(caplog):
    anchor = _message(ANCHOR_ID)
    stubborn = _message(ANCHOR_ID + 1)
    channel = _deletable_channel([anchor, stubborn])
    stubborn.delete = AsyncMock(side_effect=discord.Forbidden(MagicMock(), "no perms"))

    with caplog.at_level("WARNING"):
        await _delete_posting(channel, ANCHOR_ID, [ANCHOR_ID + 1, ANCHOR_ID], "standings")

    assert channel._deleted == [ANCHOR_ID]


async def test_an_anchor_that_will_not_delete_is_logged_not_raised(caplog):
    """Raising here would abandon the repost half done."""
    anchor = _message(ANCHOR_ID)
    anchor.delete = AsyncMock(side_effect=discord.Forbidden(MagicMock(), "no perms"))
    channel = _delete_channel(anchor, [])

    with caplog.at_level("WARNING"):
        await _delete_posting(channel, ANCHOR_ID, [ANCHOR_ID], "standings")

    assert "could not delete" in caplog.text


async def test_the_label_names_what_failed_in_the_log(caplog):
    """The same helper takes down results, standings and verdicts alike, so the log has to say
    which — otherwise a maintainer reading it cannot tell what is missing from the channel."""
    channel = MagicMock(spec=discord.TextChannel)
    channel.fetch_message = AsyncMock(
        side_effect=discord.NotFound(MagicMock(), "gone")
    )

    with caplog.at_level("WARNING"):
        await _delete_posting(channel, ANCHOR_ID, [ANCHOR_ID], "results")

    assert "results" in caplog.text


async def test_it_returns_what_it_could_not_remove():
    """**A caller that must tell a league what is still standing can** (#189).

    Disabling the results module deletes the rows that record a posting once it has tried to
    take it down, so a message the bot could not delete is left with nothing pointing at it.
    The purge names each one to the league, and has only this return value to name them by.
    """
    anchor = _message(ANCHOR_ID)
    stubborn = _message(ANCHOR_ID + 1)
    channel = _deletable_channel([anchor, stubborn])
    stubborn.delete = AsyncMock(side_effect=discord.Forbidden(MagicMock(), "no perms"))

    left = await _delete_posting(channel, ANCHOR_ID, [ANCHOR_ID, ANCHOR_ID + 1], "verdict")

    assert left == [ANCHOR_ID + 1]


async def test_a_message_already_gone_is_not_returned():
    """Deleted by hand, most often. Nothing of it is left to remove, so naming it to the league
    as still standing would send a manager looking for a message that is not there."""
    channel = MagicMock(spec=discord.TextChannel)
    channel.fetch_message = AsyncMock(side_effect=discord.NotFound(MagicMock(), "gone"))

    assert await _delete_posting(channel, ANCHOR_ID, [ANCHOR_ID], "verdict") == []


async def test_a_message_it_cannot_fetch_is_returned():
    """Forbidden on the fetch is the bot unable to read the channel, not the message gone."""
    channel = MagicMock(spec=discord.TextChannel)
    channel.fetch_message = AsyncMock(side_effect=discord.Forbidden(MagicMock(), "no perms"))

    assert await _delete_posting(channel, ANCHOR_ID, [ANCHOR_ID], "verdict") == [ANCHOR_ID]


async def test_clearing_standings_counts_what_went_and_returns_what_stayed(monkeypatch):
    """Both championships are cleared; the one that went is counted, the one refused is
    returned, and both ids are forgotten either way as before."""
    import leaguebot.results.services.results_post_service as rps

    postings = {
        rps.STANDINGS_DRIVERS: ANCHOR_ID,
        rps.STANDINGS_CONSTRUCTORS: ANCHOR_ID + 10,
    }
    forgotten: list[str] = []

    async def _get_id(db_path, division_id, round_id, championship):
        return postings[championship]

    async def _get_ids(db_path, division_id, round_id, championship):
        return [postings[championship]]

    async def _set_id(db_path, division_id, round_id, message_id, championship):
        forgotten.append(championship)

    monkeypatch.setattr(rps, "_get_standings_message_id", _get_id)
    monkeypatch.setattr(rps, "_get_standings_message_ids", _get_ids)
    monkeypatch.setattr(rps, "_set_standings_message_id", _set_id)
    drivers, constructors = _message(ANCHOR_ID), _message(ANCHOR_ID + 10)
    channel = _deletable_channel([drivers, constructors])
    constructors.delete = AsyncMock(side_effect=discord.Forbidden(MagicMock(), "no perms"))

    removed, left = await rps._clear_standings_messages("db", 1, 2, channel)

    assert (removed, left) == (1, [ANCHOR_ID + 10])
    assert forgotten == [rps.STANDINGS_DRIVERS, rps.STANDINGS_CONSTRUCTORS]


# ---------------------------------------------------------------------------
# Deleting what was recorded (#345)
# ---------------------------------------------------------------------------


def _deletable_channel(messages: list):
    """A channel whose `fetch_message` serves *messages* and records what was deleted."""
    channel = MagicMock(spec=discord.TextChannel)
    by_id = {m.id: m for m in messages}
    deleted: list = []

    async def _fetch(message_id):
        if message_id not in by_id:
            raise discord.NotFound(MagicMock(status=404), "unknown message")
        return by_id[message_id]

    for message in messages:
        message.delete = AsyncMock(side_effect=lambda m=message: deleted.append(m.id))

    channel.fetch_message = AsyncMock(side_effect=_fetch)
    channel._deleted = deleted
    return channel


def test_a_chunk_list_round_trips_through_the_stored_form():
    """What `_ids_json` writes is what `_parse_ids` reads back."""
    messages = [_message(11), _message(12), _message(13)]

    assert _parse_ids(_ids_json(messages)) == [11, 12, 13]


def test_nothing_recorded_reads_as_nothing_rather_than_an_empty_list():
    """None sends the caller to the fallback; `[]` would claim the posting occupies no messages.

    A delete honouring an empty list would remove nothing and report success, leaving the old
    posting standing beside the new one for good.
    """
    assert _parse_ids(None) is None
    assert _parse_ids("") is None
    assert _parse_ids("[]") is None


def test_a_malformed_chunk_list_reads_as_nothing_recorded():
    """A bad value means a bug in the writer, and deleting half a parse is worse than deleting
    nothing — the fallback at least stops at somebody else's message."""
    assert _parse_ids("not json") is None
    assert _parse_ids('{"id": 5}') is None
    assert _parse_ids('[1, "two"]') is None


async def test_the_recorded_messages_are_the_ones_deleted():
    """Exactly those, and in the order recorded."""
    messages = [_message(11), _message(12), _message(13)]
    channel = _deletable_channel(messages)

    await _delete_posting(channel, 11, [11, 12, 13], label="results message")

    assert channel._deleted == [11, 12, 13]


async def test_a_posting_below_the_recorded_one_is_untouched():
    """**The regression this whole change exists to prevent** (#345).

    The amendment replay posts the replacement *before* destroying the original, so the new
    posting sits directly beneath the old one and both are the bot's. The adjacency walk would
    take the new messages for continuations of the old and delete the replacement it had just
    made. Deleting what was recorded cannot reach them.
    """
    old_posting = [_message(11), _message(12)]
    new_posting = [_message(13), _message(14)]
    channel = _deletable_channel(old_posting + new_posting)

    await _delete_posting(channel, 11, [11, 12], label="results message")

    assert channel._deleted == [11, 12]
    assert 13 not in channel._deleted and 14 not in channel._deleted


async def test_a_message_already_gone_does_not_stop_the_rest():
    """A posting half-deleted by hand must not leave the other half standing for ever."""
    messages = [_message(11), _message(13)]
    channel = _deletable_channel(messages)

    await _delete_posting(channel, 11, [11, 12, 13], label="results message")

    assert channel._deleted == [11, 13]


async def test_a_posting_with_nothing_recorded_loses_its_anchor_and_nothing_else():
    """**No guessing where nothing was recorded** (#345).

    A row with no chunk list once fell back to adjacency, which is the very walk that deletes a
    posting's replacement. One message too few is a stale continuation somebody can delete by
    hand; four too many is a board the bot destroyed itself.
    """
    anchor = _message(ANCHOR_ID)
    below = _message(ANCHOR_ID + 1)
    channel = _deletable_channel([anchor, below])
    channel.history = MagicMock()

    await _delete_posting(channel, ANCHOR_ID, None, label="results message")

    assert channel._deleted == [ANCHOR_ID]
    channel.history.assert_not_called()


async def test_the_anchor_is_deleted_even_if_the_stored_list_omits_it():
    """Belt and braces on a leak that would be permanent (#345).

    Every list this module writes names the anchor first, but the anchor column and the list
    column are written and cleared through separate paths — so a list that ever lost it would
    leave that message standing for ever, with nothing left pointing at it. The anchor is the one
    id certain to belong to this posting, so it is deleted whether the list names it or not.
    """
    messages = [_message(11), _message(12)]
    channel = _deletable_channel(messages)

    await _delete_posting(channel, 11, [12], label="results message")

    assert sorted(channel._deleted) == [11, 12]


async def test_the_anchor_is_not_deleted_twice_when_the_list_names_it():
    """The ordinary case, so the safeguard cannot become a double delete."""
    messages = [_message(11), _message(12)]
    channel = _deletable_channel(messages)

    await _delete_posting(channel, 11, [11, 12], label="results message")

    assert channel._deleted == [11, 12]


async def test_a_posting_that_fails_part_way_takes_its_first_chunks_down():
    """**A posting is whole or absent** (#345). The chunks already sent are recorded nowhere
    until the whole posting is, so leaving them stranded the start of a table in the channel
    with no route by which the bot could ever take it down."""
    first = MagicMock()
    first.id = 1
    first.delete = AsyncMock()
    channel = MagicMock()
    channel.send = AsyncMock(side_effect=[first, discord.HTTPException(MagicMock(status=500), "x")])
    text = "\n".join(f"line {i:04d} " + "x" * 60 for i in range(60))

    with pytest.raises(discord.HTTPException):
        await _send_chunked(channel, text)

    first.delete.assert_awaited_once()
