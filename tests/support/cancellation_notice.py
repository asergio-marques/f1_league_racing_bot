"""A division told of a cancellation, for the tests of each module's own notice (#175, #283).

Season 1's division Pro (11), whose role is 555, has a forecast channel (701), a results channel
(702) and a check-in channel (703) on the server, each a double recording its sends. Each module
words, finds its channel for and posts its own notice of a round's, a division's or a season's
cancellation (`post_notice` of `notice_module(<module>)`), as its own job does.
"""
from __future__ import annotations

import importlib
import os
from types import SimpleNamespace as NS
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.models.cancellation import SCOPE_ROUND
from tests.support.change_queue import in_a_job

SEASON_ID = 1
DIVISION_ID = 11
ROLE_ID = 555
FORECAST, RESULTS, RSVP = 701, 702, 703


async def notice_db(tmp_path: Any, *, results: bool = True, rsvp: bool = True) -> str:
    """Pro's database, its forecast channel set; with *results* its results channel, and with
    *rsvp* its check-in channel."""
    db_path = os.path.join(str(tmp_path), "cancel.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO seasons (id, season_number, start_date, status) "
            "VALUES (?, 1, '2026-01-01', 'ACTIVE')",
            (SEASON_ID,),
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id, "
            "forecast_channel_id) VALUES (?, ?, 'Pro', 1, ?, ?)",
            (DIVISION_ID, SEASON_ID, ROLE_ID, FORECAST),
        )
        if results:
            await db.execute(
                "INSERT INTO division_results_config (division_id, results_channel_id) "
                "VALUES (?, ?)",
                (DIVISION_ID, RESULTS),
            )
        if rsvp:
            await db.execute(
                "INSERT INTO attendance_division_config (division_id, rsvp_channel_id) "
                "VALUES (?, ?)",
                (DIVISION_ID, str(RSVP)),
            )
        await db.commit()
    return db_path


def notice_bot(db_path: str) -> Any:
    """A bot on *db_path* with weather, results and attendance on."""
    bot = MagicMock()
    bot.db_path = db_path
    bot.module_service.is_weather_enabled = AsyncMock(return_value=True)
    bot.module_service.is_results_enabled = AsyncMock(return_value=True)
    bot.module_service.is_attendance_enabled = AsyncMock(return_value=True)
    return bot


def notice_guild(missing: tuple[int, ...] = ()) -> tuple[Any, dict[int, Any]]:
    """The server, and Pro's three channels on it by id; the channels in *missing* are gone."""
    channels = {cid: NS(id=cid, send=AsyncMock()) for cid in (FORECAST, RESULTS, RSVP)}
    guild = MagicMock()
    guild.get_channel = MagicMock(
        side_effect=lambda cid: None if cid in missing else channels.get(cid)
    )
    return guild, channels


def pro(role_id: int | None = ROLE_ID) -> Any:
    """Pro, its role *role_id*."""
    return NS(id=DIVISION_ID, name="Pro", mention_role_id=role_id, calendar_message_id=None)


def notice_module(module: str) -> Any:
    """The module *module* ("attendance", "weather" or "results") keeps its cancellation notice
    in; results' sits in its `publishing` folder."""
    folder = ".publishing" if module == "results" else ""
    return importlib.import_module(f"leaguebot.{module}.services{folder}.cancellation_notice")


async def post_notice(
    module: str, bot: Any, guild: Any, *, scope: str = SCOPE_ROUND, division: Any = None
) -> str | None:
    """Post *module*'s ("attendance", "weather" or "results") notice of round 3 (Monza)'s
    cancellation, or of *scope*'s, to *division* (Pro where None), as its job does; what it
    answers."""
    posting = notice_module(module)
    post = posting.post_note if module == "results" else posting.post_notice
    with in_a_job():
        answer = await post(
            bot, guild, division or pro(), scope=scope, round_number=3, track_name="Monza",
        )
    # results' `post_note` answers the message it posted; the others answer only a refusal
    return answer if isinstance(answer, str) or answer is None else None
