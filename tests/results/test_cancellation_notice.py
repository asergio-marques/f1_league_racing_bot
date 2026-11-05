"""Results' note of a round, a division or a season called off (#175, #283).

Results words, finds its channel for and posts its own note: to the division's results channel,
that no results are coming, sent as a Discord silent message that pings nobody. It is a job of its
own on the change queue: a channel never set answers "no channel is set", which the cancellation
names, and a fault of the bot's own is raised as it is.
"""
from __future__ import annotations

import pytest

from leaguebot.core.models.cancellation import SCOPE_DIVISION, SCOPE_ROUND, SCOPE_SEASON
from tests.support.cancellation_notice import (
    RESULTS,
    notice_bot,
    notice_db,
    notice_guild,
    post_notice,
)
from tests.support.mentions import NOBODY, notified


async def test_the_results_note_posts_only_to_the_results_channel(tmp_path):
    """Pro's note of round 3's cancellation, posted by results, reaches Pro's results channel and
    no other."""
    bot = notice_bot(await notice_db(tmp_path))
    guild, channels = notice_guild()
    await post_notice("results", bot, guild)
    for cid, channel in channels.items():
        if cid == RESULTS:
            channel.send.assert_awaited_once()
        else:
            channel.send.assert_not_awaited()


async def test_the_results_note_notifies_nobody_and_is_silent(tmp_path):
    """Pro's results channel is told round 3 is called off: silently, and notifying nobody."""
    bot = notice_bot(await notice_db(tmp_path))
    guild, channels = notice_guild()
    await post_notice("results", bot, guild)
    call = channels[RESULTS].send.await_args
    assert call.kwargs.get("silent") is True
    assert notified(call.kwargs.get("allowed_mentions")) == NOBODY


@pytest.mark.parametrize("scope", [SCOPE_ROUND, SCOPE_DIVISION, SCOPE_SEASON])
async def test_every_scope_says_something_in_the_results_channel(tmp_path, scope):
    """A round's, a division's and a season's cancellation each post a note in Pro's results
    channel, none of them answering that no channel is set."""
    bot = notice_bot(await notice_db(tmp_path))
    guild, channels = notice_guild()
    assert await post_notice("results", bot, guild, scope=scope) is None
    channels[RESULTS].send.assert_awaited_once()


def test_a_round_note_with_no_track_names_mystery():
    from leaguebot.results.services.cancellation_notice import results_note

    assert "Round 3 (Mystery)" in results_note(SCOPE_ROUND, "Pro", round_number=3)


async def test_a_raising_notice_lets_a_fault_of_the_bot_s_own_through_unchanged(tmp_path):
    """Sending the results note fails with a ValueError, a fault of the bot's own rather than
    Discord's. It is raised as it is, not turned into StepFailedOnDiscord."""
    from leaguebot.core.models.change import StepFailedOnDiscord

    bot = notice_bot(await notice_db(tmp_path))
    guild, channels = notice_guild()
    channels[RESULTS].send.side_effect = ValueError("bad content")
    with pytest.raises(ValueError, match="bad content") as caught:
        await post_notice("results", bot, guild)
    assert not isinstance(caught.value, StepFailedOnDiscord)


async def test_a_raising_notice_names_a_channel_never_set(tmp_path):
    """Pro has no results channel set. Posting the results note raises nothing, sends nothing
    and answers "no channel is set", which the cancellation names."""
    bot = notice_bot(await notice_db(tmp_path, results=False))
    guild, channels = notice_guild()
    assert await post_notice("results", bot, guild) == "no channel is set"
    for channel in channels.values():
        channel.send.assert_not_awaited()
