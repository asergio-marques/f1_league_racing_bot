"""Unit tests: duplicate round-number guard and mutation helpers (US2, FR-006 – FR-013)."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from cogs.season_cog import (
    DuplicateRoundView,
    PendingConfig,
    PendingDivision,
    SeasonCog,
    _rounds_insert_after,
    _rounds_insert_before,
    _rounds_replace,
)
from models.round import RoundFormat


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _r(num: int, track: str = "United Kingdom") -> dict:
    return {
        "round_number": num,
        "format": RoundFormat.NORMAL,
        "track_name": track,
        "scheduled_at": datetime(2026, 5, 1, 14, 0, 0),
    }


def _rounds(*nums: int) -> list[dict]:
    return [_r(n) for n in nums]


def _round_nums(rounds: list[dict]) -> list[int]:
    return sorted(r["round_number"] for r in rounds)


def _make_interaction() -> MagicMock:
    interaction = MagicMock()
    interaction.response.edit_message = AsyncMock()
    return interaction


# ---------------------------------------------------------------------------
# Tests — pure mutation helpers
# ---------------------------------------------------------------------------

def test_insert_before_basic() -> None:
    """T007-1: Insert before shifts rounds >= conflict_num by 1."""
    rounds = _rounds(1, 2, 3, 4)
    new = _r(3, "Australia")
    _rounds_insert_before(rounds, conflict_num=3, new_round=new)
    assert _round_nums(rounds) == [1, 2, 3, 4, 5]
    added = next(r for r in rounds if r["track_name"] == "Australia")
    assert added["round_number"] == 3


def test_insert_after_basic() -> None:
    """T007-2: Insert after shifts rounds > conflict_num by 1; new round gets conflict_num + 1."""
    rounds = _rounds(1, 2, 3, 4)
    new = _r(3, "Australia")
    _rounds_insert_after(rounds, conflict_num=3, new_round=new)
    assert _round_nums(rounds) == [1, 2, 3, 4, 5]
    added = next(r for r in rounds if r["track_name"] == "Australia")
    assert added["round_number"] == 4
    # Old round 3 stays at 3
    old = next(r for r in rounds if r["track_name"] == "United Kingdom" and r["round_number"] == 3)
    assert old is not None


def test_replace_basic() -> None:
    """T007-3: Replace removes existing round and inserts new round at same number."""
    rounds = _rounds(1, 2, 3, 4)
    new = _r(3, "Australia")
    _rounds_replace(rounds, conflict_num=3, new_round=new)
    assert _round_nums(rounds) == [1, 2, 3, 4]
    r3 = next(r for r in rounds if r["round_number"] == 3)
    assert r3["track_name"] == "Australia"


def test_insert_before_cascades() -> None:
    """T007-4: Insert before with consecutive rounds cascades all shifts."""
    rounds = _rounds(3, 4, 5)
    new = _r(3, "Australia")
    _rounds_insert_before(rounds, conflict_num=3, new_round=new)
    assert _round_nums(rounds) == [3, 4, 5, 6]
    added = next(r for r in rounds if r["track_name"] == "Australia")
    assert added["round_number"] == 3


def test_insert_after_no_gap_when_adjacent() -> None:
    """T007 extra: Insert after with adjacent round correctly shifts all > conflict."""
    rounds = _rounds(3, 4, 5)
    new = _r(3, "Australia")
    _rounds_insert_after(rounds, conflict_num=3, new_round=new)
    assert _round_nums(rounds) == [3, 4, 5, 6]
    added = next(r for r in rounds if r["track_name"] == "Australia")
    assert added["round_number"] == 4


def test_replace_other_rounds_unchanged() -> None:
    """T007 extra: Replace leaves all other round numbers untouched."""
    rounds = _rounds(1, 2, 3, 4, 5)
    new = _r(3, "Australia")
    _rounds_replace(rounds, conflict_num=3, new_round=new)
    assert _round_nums(rounds) == [1, 2, 3, 4, 5]


# ---------------------------------------------------------------------------
# Tests — DuplicateRoundView button callbacks
# ---------------------------------------------------------------------------

async def test_view_insert_before_button() -> None:
    """T007-5: Insert Before button applies _rounds_insert_before and disables all buttons."""
    div = PendingDivision(name="Pro", role_id=1, channel_id=2, rounds=_rounds(1, 2, 3))
    new = _r(3, "Australia")
    view = DuplicateRoundView(div, new)
    interaction = _make_interaction()

    await view.insert_before_cb.callback(interaction)

    assert _round_nums(div.rounds) == [1, 2, 3, 4]
    added = next(r for r in div.rounds if r["track_name"] == "Australia")
    assert added["round_number"] == 3
    interaction.response.edit_message.assert_called_once()
    assert all(item.disabled for item in view.children)


async def test_view_insert_after_button() -> None:
    """T007-6: Insert After button applies _rounds_insert_after and disables all buttons."""
    div = PendingDivision(name="Pro", role_id=1, channel_id=2, rounds=_rounds(1, 2, 3))
    new = _r(3, "Australia")
    view = DuplicateRoundView(div, new)
    interaction = _make_interaction()

    await view.insert_after_cb.callback(interaction)

    assert _round_nums(div.rounds) == [1, 2, 3, 4]
    added = next(r for r in div.rounds if r["track_name"] == "Australia")
    assert added["round_number"] == 4
    interaction.response.edit_message.assert_called_once()
    assert all(item.disabled for item in view.children)


async def test_view_replace_button() -> None:
    """T007-7: Replace button applies _rounds_replace and disables all buttons."""
    div = PendingDivision(name="Pro", role_id=1, channel_id=2, rounds=_rounds(1, 2, 3))
    new = _r(3, "Australia")
    view = DuplicateRoundView(div, new)
    interaction = _make_interaction()

    await view.replace_cb.callback(interaction)

    assert _round_nums(div.rounds) == [1, 2, 3]
    r3 = next(r for r in div.rounds if r["round_number"] == 3)
    assert r3["track_name"] == "Australia"
    interaction.response.edit_message.assert_called_once()
    assert all(item.disabled for item in view.children)


async def test_view_cancel_button() -> None:
    """T007-8: Cancel button leaves rounds unchanged and disables all buttons."""
    original_rounds = _rounds(1, 2, 3)
    div = PendingDivision(name="Pro", role_id=1, channel_id=2, rounds=list(original_rounds))
    new = _r(3, "Australia")
    view = DuplicateRoundView(div, new)
    interaction = _make_interaction()

    await view.cancel_cb.callback(interaction)

    assert _round_nums(div.rounds) == [1, 2, 3]
    # Existing round 3 still has the original track
    r3 = next(r for r in div.rounds if r["round_number"] == 3)
    assert r3["track_name"] == "United Kingdom"
    interaction.response.edit_message.assert_called_once()
    assert all(item.disabled for item in view.children)


async def test_view_on_timeout() -> None:
    """T007-9: on_timeout leaves rounds unchanged and edits the stored message."""
    original_rounds = _rounds(1, 2, 3)
    div = PendingDivision(name="Pro", role_id=1, channel_id=2, rounds=list(original_rounds))
    new = _r(3, "Australia")
    view = DuplicateRoundView(div, new)

    msg_mock = AsyncMock()
    view.message = msg_mock

    await view.on_timeout()

    assert _round_nums(div.rounds) == [1, 2, 3]
    msg_mock.edit.assert_called_once()
    call_kwargs = msg_mock.edit.call_args[1]
    assert "Timed out" in call_kwargs.get("content", "") or "timed out" in call_kwargs.get("content", "").lower()
    assert all(item.disabled for item in view.children)


# ---------------------------------------------------------------------------
# Tests — round_add integration (no conflict / conflict detection)
# ---------------------------------------------------------------------------

async def test_round_add_no_conflict_continues_normally() -> None:
    """T007-10: /round-add with no conflict appends and sends success (existing path unchanged)."""
    bot = MagicMock()
    bot.season_service.save_pending_snapshot = AsyncMock(return_value=42)
    cog = SeasonCog(bot)

    # Build a pending config with one division containing round 1
    pending = PendingConfig(server_id=1, divisions=[
        PendingDivision(name="Pro", role_id=10, channel_id=20, rounds=[_r(1)])
    ])
    cog._pending[99] = pending  # key by user_id

    interaction = MagicMock()
    interaction.guild_id = 1
    interaction.user.id = 99
    interaction.response.send_message = AsyncMock()

    await cog.round_add.callback.__wrapped__.__wrapped__(cog, interaction,
        division_name="Pro",
        round_number=2,
        format="NORMAL",
        scheduled_at="2026-06-01T14:00:00",
        track="Australia",
    )

    # Round 2 should be appended
    div = pending.divisions[0]
    assert any(r["round_number"] == 2 for r in div.rounds)
    interaction.response.send_message.assert_called_once()
    args, kwargs = interaction.response.send_message.call_args
    msg = args[0] if args else kwargs.get("content", "")
    assert "✅" in msg


async def test_round_add_conflict_shows_duplicate_view() -> None:
    """T007-11: /round-add with duplicate round_number sends DuplicateRoundView, does NOT append."""
    bot = MagicMock()
    cog = SeasonCog(bot)

    pending = PendingConfig(server_id=1, divisions=[
        PendingDivision(name="Pro", role_id=10, channel_id=20, rounds=[_r(3)])
    ])
    cog._pending[99] = pending

    interaction = MagicMock()
    interaction.guild_id = 1
    interaction.user.id = 99
    interaction.response.send_message = AsyncMock()
    interaction.original_response = AsyncMock(return_value=MagicMock())

    await cog.round_add.callback.__wrapped__.__wrapped__(cog, interaction,
        division_name="Pro",
        round_number=3,
        format="NORMAL",
        scheduled_at="2026-07-01T14:00:00",
        track="Australia",
    )

    # Only the original round 3 should exist — new one must NOT be silently appended
    div = pending.divisions[0]
    assert len(div.rounds) == 1
    assert div.rounds[0]["track_name"] == "United Kingdom"

    # DuplicateRoundView was passed as the view argument
    interaction.response.send_message.assert_called_once()
    _, kwargs = interaction.response.send_message.call_args
    assert isinstance(kwargs.get("view"), DuplicateRoundView)
    assert kwargs.get("ephemeral") is True
