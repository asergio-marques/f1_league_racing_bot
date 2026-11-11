"""`/season status` — the next round each division is to race.

A division's next round is its first round not cancelled, not a mystery round, and not yet past
all three weather phases. Which phases a round has had drawn is the weather module's own record
(architecture.md, "Each table is read and written by its owner alone"), so the command is run on
the bot the entry point builds, whose every reader is the builder's.
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

from leaguebot.core.cogs.season_cog import SeasonCog
from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.utils.formatting import discord_ts
from tests.support.change_queue import league_bot
from tests.support.undecorate import undecorate

SERVER_ID = 10_920
SPA_AT = "2026-03-15T18:00:00+00:00"


async def _make_db(tmp_path) -> str:
    """An active season with two divisions. Pro holds round 1 (Monza, all three phases drawn),
    round 2 (a mystery round, nothing drawn), round 3 (Silverstone, cancelled, nothing drawn) and
    round 4 (Spa, Phase 1 drawn); Am holds round 1 (Imola, all three phases drawn) alone."""
    db_path = str(tmp_path / "status.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 900, 100, 101)",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO seasons (id, season_number, start_date, status, stage) "
            "VALUES (1, 1, '2026-03-01', 'ACTIVE', 'ONGOING')"
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id, status) "
            "VALUES (11, 1, 'Pro', 1, 555, 'ACTIVE'), (12, 1, 'Am', 2, 556, 'ACTIVE')"
        )
        await db.execute(
            "INSERT INTO rounds (division_id, round_number, format, track_name, scheduled_at, "
            "phase1_done, phase2_done, phase3_done, status) VALUES "
            "(11, 1, 'NORMAL', 'Monza', '2026-03-01T18:00:00+00:00', 1, 1, 1, 'NOT_RUN'), "
            "(11, 2, 'MYSTERY', NULL, '2026-03-08T18:00:00+00:00', 0, 0, 0, 'NOT_RUN'), "
            "(11, 3, 'NORMAL', 'Silverstone', '2026-03-12T18:00:00+00:00', 0, 0, 0, "
            "'CANCELLED'), "
            f"(11, 4, 'NORMAL', 'Spa', '{SPA_AT}', 1, 0, 0, 'NOT_RUN'), "
            "(12, 1, 'NORMAL', 'Imola', '2026-03-01T18:00:00+00:00', 1, 1, 1, 'NOT_RUN')"
        )
        await db.commit()
    return db_path


async def test_a_division_s_next_round_passes_over_rounds_fully_forecast_and_mystery_rounds(
    tmp_path,
):
    """`/season status` run by a league manager on the season above: Pro's next round is round 4
    at Spa, passing over round 1 (all three phases drawn), the mystery round 2 and the cancelled
    round 3; Am, whose only round has had all three phases drawn, has none remaining."""
    db_path = await _make_db(tmp_path)
    bot = await league_bot(db_path)
    cog = SeasonCog.__new__(SeasonCog)
    cog.bot = bot
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.response.send_message = AsyncMock()

    await undecorate(SeasonCog.season_status)(cog, interaction)

    [reply] = [call.args[0] for call in interaction.response.send_message.await_args_list]
    lines = reply.splitlines()
    spa = discord_ts(datetime.fromisoformat(SPA_AT))
    assert f"\U0001f4c2 **Pro** \u2014 Next round: R4 @ Spa {spa}" in lines
    assert "\U0001f4c2 **Am** \u2014 Next round: None remaining" in lines
