"""Weather's note of a round, a division or a season called off (#175, #283).

Weather words, finds its channel for and posts its own note: to the division's forecast channel,
that no forecast is coming, sent as a Discord silent message that pings nobody. It is a job of its
own on the change queue: a send Discord refuses raises `StepFailedOnDiscord` from the refusal.
"""
from __future__ import annotations

import pytest

from leaguebot.core.models.cancellation import SCOPE_DIVISION, SCOPE_ROUND, SCOPE_SEASON
from tests.support.cancellation_notice import (
    FORECAST,
    notice_bot,
    notice_db,
    notice_guild,
    post_notice,
)
from tests.support.mentions import NOBODY, notified


async def test_the_forecast_note_posts_only_to_the_forecast_channel(tmp_path):
    """Pro's note of round 3's cancellation, posted by weather, reaches Pro's forecast channel and
    no other."""
    bot = notice_bot(await notice_db(tmp_path))
    guild, channels = notice_guild()
    await post_notice("weather", bot, guild)
    for cid, channel in channels.items():
        if cid == FORECAST:
            channel.send.assert_awaited_once()
        else:
            channel.send.assert_not_awaited()


async def test_the_forecast_note_notifies_nobody_and_is_silent(tmp_path):
    """Pro's forecast channel is told round 3 is called off: silently, and notifying nobody."""
    bot = notice_bot(await notice_db(tmp_path))
    guild, channels = notice_guild()
    await post_notice("weather", bot, guild)
    call = channels[FORECAST].send.await_args
    assert call.kwargs.get("silent") is True
    assert notified(call.kwargs.get("allowed_mentions")) == NOBODY


@pytest.mark.parametrize("scope", [SCOPE_ROUND, SCOPE_DIVISION, SCOPE_SEASON])
async def test_every_scope_says_something_in_the_forecast_channel(tmp_path, scope):
    """A round's, a division's and a season's cancellation each post a note in Pro's forecast
    channel, none of them answering that no channel is set."""
    bot = notice_bot(await notice_db(tmp_path))
    guild, channels = notice_guild()
    assert await post_notice("weather", bot, guild, scope=scope) is None
    channels[FORECAST].send.assert_awaited_once()


def test_a_round_note_names_the_round_and_its_track():
    from leaguebot.weather.services.cancellation_notice import weather_note

    note = weather_note(SCOPE_ROUND, "Pro", round_number=3, track_name="Monza")
    assert "Round 3 (Monza)" in note
    assert "No weather forecast" in note


async def test_a_raising_notice_raises_where_discord_refuses(tmp_path):
    """Discord refuses the forecast note in Pro's forecast channel (Forbidden). Posting it raises
    StepFailedOnDiscord, raised from Discord's refusal, which the stop notice names."""
    from unittest.mock import MagicMock

    import discord

    from leaguebot.core.models.change import StepFailedOnDiscord

    bot = notice_bot(await notice_db(tmp_path))
    guild, channels = notice_guild()
    refusal = discord.Forbidden(MagicMock(status=403), "Missing Access")
    channels[FORECAST].send.side_effect = refusal
    with pytest.raises(StepFailedOnDiscord) as caught:
        await post_notice("weather", bot, guild)
    assert caught.value.__cause__ is refusal
