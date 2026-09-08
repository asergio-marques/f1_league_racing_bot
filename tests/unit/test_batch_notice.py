"""`batch_notice` — the message that says graphics are being drawn.

The helper is a courtesy wrapped around slow render batches, and the whole of its
contract is that it never becomes a reason a batch fails. These drive both halves of
that: the notice appears before the work and is gone after it, and every way Discord can
refuse either half leaves the body's own outcome untouched.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from utils.batch_notice import batch_notice  # noqa: E402


def _channel():
    channel = MagicMock()
    message = MagicMock()
    message.delete = AsyncMock()
    channel.send = AsyncMock(return_value=message)
    return channel, message


def _http_error(status: int = 500):
    response = MagicMock()
    response.status = status
    return discord.HTTPException(response, "boom")


async def test_the_notice_is_posted_before_the_body_runs():
    channel, _ = _channel()
    seen: list[str] = []

    async with batch_notice(channel, "drawing"):
        seen.append("body")
        channel.send.assert_awaited_once_with("drawing")

    assert seen == ["body"]


async def test_the_notice_is_deleted_when_the_body_finishes():
    channel, message = _channel()

    async with batch_notice(channel, "drawing"):
        message.delete.assert_not_awaited()

    message.delete.assert_awaited_once()


async def test_the_notice_is_deleted_when_the_body_raises():
    """The paths this wraps swallow their own render faults, so a stranded notice would
    never be tidied by anything else."""
    channel, message = _channel()

    with pytest.raises(ValueError):
        async with batch_notice(channel, "drawing"):
            raise ValueError("render blew up")

    message.delete.assert_awaited_once()


async def test_the_body_still_runs_when_the_notice_cannot_be_posted():
    channel, _ = _channel()
    channel.send = AsyncMock(side_effect=_http_error())
    ran = False

    async with batch_notice(channel, "drawing"):
        ran = True

    assert ran, "a failed courtesy message stopped the batch"


async def test_nothing_is_deleted_when_nothing_was_posted():
    channel, message = _channel()
    channel.send = AsyncMock(side_effect=_http_error())

    async with batch_notice(channel, "drawing"):
        pass

    message.delete.assert_not_awaited()


async def test_a_notice_already_gone_is_not_an_error():
    """`finalize_appeals_review` deletes the whole submission channel just after its
    batch, so losing the message with it is ordinary, not a fault."""
    channel, message = _channel()
    message.delete = AsyncMock(side_effect=discord.NotFound(MagicMock(status=404), "gone"))

    async with batch_notice(channel, "drawing"):
        pass

    message.delete.assert_awaited_once()


async def test_a_refused_delete_does_not_reach_the_caller():
    channel, message = _channel()
    message.delete = AsyncMock(side_effect=_http_error())

    async with batch_notice(channel, "drawing"):
        pass


async def test_a_failed_delete_does_not_mask_the_body_s_exception():
    """The caller's fault is the one worth seeing."""
    channel, message = _channel()
    message.delete = AsyncMock(side_effect=_http_error())

    with pytest.raises(ValueError, match="render blew up"):
        async with batch_notice(channel, "drawing"):
            raise ValueError("render blew up")


async def test_no_channel_is_allowed_and_posts_nothing():
    """Saves every caller a branch of its own where a guild or channel is unresolved."""
    ran = False

    async with batch_notice(None, "drawing"):
        ran = True

    assert ran
