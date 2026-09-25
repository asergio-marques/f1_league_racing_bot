"""`/round add` reads its moment by the shared datetime rule (#362).

A round is stored naive, meaning UTC. The XML import converted a time given with a zone to that
form, but `/round add` kept the zone as typed, so the same moment could be stored two ways and
two rounds at one instant would not be seen to clash. Both now read by
`leaguebot.core.utils.input_validator.parse_datetime`.

A mystery round is added throughout, which needs no track and so no database.
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

from leaguebot.core.cogs.season_cog import PendingConfig, PendingDivision, SeasonCog
from leaguebot.core.models.round import RoundFormat
from tests.support.undecorate import undecorate

ACTOR_ID = 77


def _cog(cfg: PendingConfig) -> SeasonCog:
    cog = SeasonCog.__new__(SeasonCog)
    cog.bot = MagicMock()
    cog.bot.output_router.post_log = AsyncMock(return_value=None)
    cog._pending = {ACTOR_ID: cfg}
    cog._get_pending = MagicMock(return_value=cfg)
    cog._snapshot_pending = AsyncMock(return_value=None)
    cog._calendar_round_overflow = AsyncMock(return_value=None)
    return cog


def _interaction():
    interaction = MagicMock()
    interaction.user.id = ACTOR_ID
    interaction.user.display_name = "Manager"
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()
    return interaction


def _replied(interaction) -> str:
    return "\n".join(
        str(call.args[0]) for call in interaction.followup.send.await_args_list if call.args
    )


async def _add(division: PendingDivision, scheduled_at: str):
    interaction = _interaction()
    cog = _cog(PendingConfig(divisions=[division], season_id=7))
    await undecorate(SeasonCog.round_add)(
        cog, interaction, division.name, "MYSTERY", scheduled_at, ""
    )
    return interaction


async def test_round_add_stores_a_zoned_time_as_utc():
    division = PendingDivision(name="Pro", role_id=1, tier=1)

    await _add(division, "2026-06-14T20:00+02:00")

    stored = division.rounds[0]["scheduled_at"]
    assert stored == datetime(2026, 6, 14, 18, 0)
    assert stored.tzinfo is None


async def test_a_zoned_time_clashes_with_the_same_instant_given_without_one():
    """Stored alike, the two are seen to be one moment and the second is refused."""
    division = PendingDivision(
        name="Pro",
        role_id=1,
        tier=1,
        rounds=[
            {
                "round_number": 1,
                "format": RoundFormat.MYSTERY,
                "track_name": None,
                "scheduled_at": datetime(2026, 6, 14, 18, 0),
            }
        ],
    )

    interaction = await _add(division, "2026-06-14T20:00+02:00")

    assert len(division.rounds) == 1
    assert "cannot share a moment" in _replied(interaction)


async def test_an_unreadable_time_is_refused():
    division = PendingDivision(name="Pro", role_id=1, tier=1)

    interaction = await _add(division, "14/06/2026 18:00")

    assert division.rounds == []
    assert "Invalid datetime" in _replied(interaction)
