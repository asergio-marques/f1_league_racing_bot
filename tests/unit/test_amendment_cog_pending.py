"""Unit tests: /round-amend pending-config path (US1, FR-001 – FR-005)."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from cogs.amendment_cog import AmendmentCog
from cogs.season_cog import PendingConfig, PendingDivision
from models.round import RoundFormat


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_round(
    num: int,
    fmt: RoundFormat = RoundFormat.NORMAL,
    track: str | None = "United Kingdom",
) -> dict:
    return {
        "round_number": num,
        "format": fmt,
        "track_name": track,
        "scheduled_at": datetime(2026, 5, 1, 14, 0, 0),
    }


def _make_pending(server_id: int = 1) -> PendingConfig:
    div = PendingDivision(
        name="Pro",
        role_id=10,
        channel_id=20,
        rounds=[
            _make_round(1),
            _make_round(2, RoundFormat.SPRINT, "Australia"),
            _make_round(3, RoundFormat.MYSTERY, None),
        ],
    )
    return PendingConfig(server_id=server_id, divisions=[div])


def _make_interaction(guild_id: int = 1) -> MagicMock:
    interaction = MagicMock()
    interaction.guild_id = guild_id
    interaction.user.id = 42
    interaction.response.send_message = AsyncMock()
    return interaction


def _make_cog(pending_cfg: PendingConfig | None) -> tuple[AmendmentCog, MagicMock]:
    """Return (cog, bot_mock) with SeasonCog stub returning *pending_cfg*."""
    bot = MagicMock()
    bot.season_service.get_active_season = AsyncMock(return_value=None)
    bot.season_service.save_pending_snapshot = AsyncMock(return_value=42)

    if pending_cfg is not None:
        season_cog_mock = MagicMock()
        season_cog_mock._get_pending_for_server = MagicMock(return_value=pending_cfg)
    else:
        season_cog_mock = None

    bot.get_cog = MagicMock(return_value=season_cog_mock)
    cog = AmendmentCog(bot)
    return cog, bot


# ---------------------------------------------------------------------------
# Tests — happy paths
# ---------------------------------------------------------------------------

async def test_pending_amend_track_change() -> None:
    """T006-1: Track amendment updates the round dict in-memory."""
    pending = _make_pending()
    cog, _ = _make_cog(pending)
    interaction = _make_interaction()

    await cog.round_amend.callback.__wrapped__.__wrapped__(cog, interaction,
        division_name="Pro",
        round_number=1,
        track="Australia",
    )

    div = pending.divisions[0]
    rnd = next(r for r in div.rounds if r["round_number"] == 1)
    assert rnd["track_name"] == "Australia"
    # Confirm success message sent
    interaction.response.send_message.assert_called_once()
    args, kwargs = interaction.response.send_message.call_args
    assert "✅" in (args[0] if args else kwargs.get("content", ""))
    assert kwargs.get("ephemeral") is True


async def test_pending_amend_scheduled_at_change() -> None:
    """T006-2: scheduled_at amendment updates the datetime in-memory."""
    pending = _make_pending()
    cog, _ = _make_cog(pending)
    interaction = _make_interaction()

    new_dt = "2026-06-15T18:00:00"
    await cog.round_amend.callback.__wrapped__.__wrapped__(cog, interaction,
        division_name="Pro",
        round_number=2,
        scheduled_at=new_dt,
    )

    div = pending.divisions[0]
    rnd = next(r for r in div.rounds if r["round_number"] == 2)
    assert rnd["scheduled_at"] == datetime.fromisoformat(new_dt)
    interaction.response.send_message.assert_called_once()


async def test_pending_amend_format_to_mystery_clears_track() -> None:
    """T006-3: Changing format to MYSTERY must clear track_name (FR-003)."""
    pending = _make_pending()
    cog, _ = _make_cog(pending)
    interaction = _make_interaction()

    # Round 1 currently has track "United Kingdom"; change format to MYSTERY
    await cog.round_amend.callback.__wrapped__.__wrapped__(cog, interaction,
        division_name="Pro",
        round_number=1,
        format="MYSTERY",
    )

    div = pending.divisions[0]
    rnd = next(r for r in div.rounds if r["round_number"] == 1)
    assert rnd["format"] == RoundFormat.MYSTERY
    assert rnd["track_name"] is None
    interaction.response.send_message.assert_called_once()


async def test_pending_amend_format_away_from_mystery_no_track_empty_stored_rejects() -> None:
    """T006-4: Format ← MYSTERY, no track supplied, stored track empty → rejected (FR-004)."""
    pending = _make_pending()
    cog, _ = _make_cog(pending)
    interaction = _make_interaction()

    # Round 3 is MYSTERY with track_name=None
    await cog.round_amend.callback.__wrapped__.__wrapped__(cog, interaction,
        division_name="Pro",
        round_number=3,
        format="NORMAL",
        # track intentionally omitted
    )

    args, kwargs = interaction.response.send_message.call_args
    msg = args[0] if args else kwargs.get("content", "")
    assert "❌" in msg
    assert "track" in msg.lower()
    # Round format must remain MYSTERY (unchanged)
    div = pending.divisions[0]
    rnd = next(r for r in div.rounds if r["round_number"] == 3)
    assert rnd["format"] == RoundFormat.MYSTERY


async def test_pending_amend_format_away_from_mystery_preserves_existing_track() -> None:
    """T006-5: Format ← MYSTERY, no track supplied, stored track already has a value → succeeds."""
    pending = _make_pending()
    # Manually set round 3 to have both MYSTERY format AND an existing track
    div = pending.divisions[0]
    rnd3 = next(r for r in div.rounds if r["round_number"] == 3)
    rnd3["track_name"] = "Australia"
    # format is still MYSTERY

    cog, _ = _make_cog(pending)
    interaction = _make_interaction()

    await cog.round_amend.callback.__wrapped__.__wrapped__(cog, interaction,
        division_name="Pro",
        round_number=3,
        format="NORMAL",
        # track omitted — should preserve "Australia"
    )

    args, kwargs = interaction.response.send_message.call_args
    msg = args[0] if args else kwargs.get("content", "")
    assert "✅" in msg
    # Track preserved, format updated
    assert rnd3["format"] == RoundFormat.NORMAL
    assert rnd3["track_name"] == "Australia"


# ---------------------------------------------------------------------------
# Tests — error paths
# ---------------------------------------------------------------------------

async def test_pending_amend_division_not_found() -> None:
    """T006-6: Division name not in pending config → descriptive error."""
    pending = _make_pending()
    cog, _ = _make_cog(pending)
    interaction = _make_interaction()

    await cog.round_amend.callback.__wrapped__.__wrapped__(cog, interaction,
        division_name="Nonexistent",
        round_number=1,
        track="Australia",
    )

    args, kwargs = interaction.response.send_message.call_args
    msg = args[0] if args else kwargs.get("content", "")
    assert "❌" in msg
    assert "Nonexistent" in msg


async def test_pending_amend_round_not_found() -> None:
    """T006-7: Round number not in pending div → descriptive error."""
    pending = _make_pending()
    cog, _ = _make_cog(pending)
    interaction = _make_interaction()

    await cog.round_amend.callback.__wrapped__.__wrapped__(cog, interaction,
        division_name="Pro",
        round_number=99,
        track="Australia",
    )

    args, kwargs = interaction.response.send_message.call_args
    msg = args[0] if args else kwargs.get("content", "")
    assert "❌" in msg
    assert "99" in msg


async def test_pending_amend_no_fields_supplied() -> None:
    """T006-8: No amendment fields supplied → immediate validation error."""
    pending = _make_pending()
    cog, _ = _make_cog(pending)
    interaction = _make_interaction()

    await cog.round_amend.callback.__wrapped__.__wrapped__(cog, interaction,
        division_name="Pro",
        round_number=1,
        # all defaults (empty strings)
    )

    args, kwargs = interaction.response.send_message.call_args
    msg = args[0] if args else kwargs.get("content", "")
    assert "❌" in msg


async def test_no_pending_cfg_falls_through_to_db_path() -> None:
    """T006-9: No pending config → active-season DB lookup is called."""
    cog, bot = _make_cog(pending_cfg=None)
    interaction = _make_interaction()

    await cog.round_amend.callback.__wrapped__.__wrapped__(cog, interaction,
        division_name="Pro",
        round_number=1,
        track="Australia",
    )

    # get_active_season is what the DB path calls first
    bot.season_service.get_active_season.assert_called_once_with(interaction.guild_id)
