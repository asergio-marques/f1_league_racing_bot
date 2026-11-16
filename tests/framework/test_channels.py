"""The cast of a configured channel to the text channel it is, which the framework owns (#544).

`src/leaguebot/framework/utils/channels.py` holds the reasoning: the cast rests on what the bot's
configuration guarantees, so it is the bot's machinery rather than a generic helper. What makes it
true, that a channel is only ever set as a text channel, is pinned in
`tests/core/test_channel_registry.py`.
"""
from __future__ import annotations

from unittest.mock import MagicMock


def test_as_text_channel_hands_back_the_channel_it_was_given():
    """A narrowing for the type check alone: the same object, and None stays None."""
    from leaguebot.framework.utils.channels import as_text_channel

    channel = MagicMock()
    assert as_text_channel(channel) is channel
    assert as_text_channel(None) is None
