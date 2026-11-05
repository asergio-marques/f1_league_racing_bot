"""Attendance's notice of a round, a division or a season called off (#175, #283).

Attendance words, finds its channel for and posts its own notice: the one real notification of a
cancellation, posted to the division's check-in channel and mentioning the division role as the
check-in call does, since that is where drivers answer whether they are racing. It is a job of its
own on the change queue: a channel set and gone raises `StepFailedOnDiscord`, so that the queue
stops until the channel is set again and Retry is pressed.
"""
from __future__ import annotations

import pytest

from leaguebot.core.models.cancellation import SCOPE_DIVISION, SCOPE_ROUND, SCOPE_SEASON
from tests.support.cancellation_notice import (
    ROLE_ID,
    RSVP,
    notice_bot,
    notice_db,
    notice_guild,
    post_notice,
    pro,
)
from tests.support.mentions import NOBODY, notified

NOT_YET = "#283: attendance does not word and post its own cancellation notice yet"


@pytest.mark.xfail(strict=True, reason=NOT_YET)
async def test_the_check_in_notice_posts_only_to_the_check_in_channel(tmp_path):
    """Pro's notice of round 3's cancellation, posted by attendance, reaches Pro's check-in
    channel and no other."""
    bot = notice_bot(await notice_db(tmp_path))
    guild, channels = notice_guild()
    await post_notice("attendance", bot, guild)
    for cid, channel in channels.items():
        if cid == RSVP:
            channel.send.assert_awaited_once()
        else:
            channel.send.assert_not_awaited()


@pytest.mark.xfail(strict=True, reason=NOT_YET)
async def test_the_check_in_channel_carries_the_notification(tmp_path):
    """Not silent, and it mentions the division role as the check-in call does."""
    bot = notice_bot(await notice_db(tmp_path))
    guild, channels = notice_guild()
    await post_notice("attendance", bot, guild)
    call = channels[RSVP].send.await_args
    assert not call.kwargs.get("silent")
    assert call.args[0].startswith(f"<@&{ROLE_ID}>")
    assert "Round 3 Cancelled: Pro" in call.args[0]
    assert "no check-in to answer" in call.args[0]


@pytest.mark.xfail(strict=True, reason=NOT_YET)
async def test_the_attendance_notice_may_notify_the_division_role_and_nobody_else(tmp_path):
    """Pro, whose role is 555, has round 3 at Monza called off. The check-in channel's notice
    mentions the role, and may notify it alone: no member it might name, and not @everyone."""
    bot = notice_bot(await notice_db(tmp_path))
    guild, channels = notice_guild()
    await post_notice("attendance", bot, guild)
    call = channels[RSVP].send.await_args
    assert notified(call.kwargs.get("allowed_mentions")) == {
        "everyone": False, "users": set(), "roles": {ROLE_ID}
    }


@pytest.mark.xfail(strict=True, reason=NOT_YET)
async def test_the_attendance_notice_of_a_division_with_no_role_notifies_nobody(tmp_path):
    """Pro has no role set. Its check-in channel's notice of round 3's cancellation mentions no
    role, and notifies nobody."""
    bot = notice_bot(await notice_db(tmp_path))
    guild, channels = notice_guild()
    await post_notice("attendance", bot, guild, division=pro(role_id=None))
    call = channels[RSVP].send.await_args
    assert not call.args[0].startswith("<@&")
    assert notified(call.kwargs.get("allowed_mentions")) == NOBODY


@pytest.mark.xfail(strict=True, reason=NOT_YET)
@pytest.mark.parametrize("scope", [SCOPE_ROUND, SCOPE_DIVISION, SCOPE_SEASON])
async def test_every_scope_says_something_in_the_check_in_channel(tmp_path, scope):
    """A round's, a division's and a season's cancellation each post a notice in Pro's check-in
    channel, none of them answering that no channel is set."""
    bot = notice_bot(await notice_db(tmp_path))
    guild, channels = notice_guild()
    assert await post_notice("attendance", bot, guild, scope=scope) is None
    channels[RSVP].send.assert_awaited_once()


@pytest.mark.xfail(strict=True, reason=NOT_YET)
async def test_a_raising_notice_raises_where_its_channel_is_gone(tmp_path):
    """Pro's check-in channel is set but no longer on the server. Posting attendance's notice
    raises StepFailedOnDiscord, caused by nothing else, so the queue stops until the channel is
    set again and Retry is pressed."""
    from leaguebot.core.models.change import StepFailedOnDiscord

    bot = notice_bot(await notice_db(tmp_path))
    guild, _ = notice_guild(missing=(RSVP,))
    with pytest.raises(StepFailedOnDiscord) as caught:
        await post_notice("attendance", bot, guild)
    assert caught.value.__cause__ is None
