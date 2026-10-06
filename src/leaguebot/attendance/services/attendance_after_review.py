"""Attendance's share of a round's review, as the hook the builder hands results' change types
(#439, slice 2, plan 2.6; the protocol is `results/services/attendance_hook.py`).

Built once by the entry point, handed the placement service and attached as
`bot.attendance_after_review`. It uses the bot for Discord alone (the league's server, the log
channel, posting) and never looks another service up on it: the placement service is the one it
was handed, and attendance's switch is read from the database, on the connection a writer is
handed.

**Each method does nothing while attendance is off.** A writer writes on the save it is handed
and commits nothing, so a fault in the attendance fails the approval whole. An amendment's
recalculation rebuilds the amended round's attended flags both ways (FR-028); the first pass
records upgrade-only (FR-003).

**A sanction is one driver's**, and one that does not apply raises. The candidate carries the
profile, the Discord user, the sanction and the divisions an autosack reaches; the thresholds
and the driver's total are read again when the sanction is applied or announced.

**The league's server** is core's `league_guild`, the one route from a job to it; where none is
claimed or it is not in the cache the method raises `GuildUnavailable`.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

import aiosqlite
import discord

from leaguebot.attendance.services import attendance_service as _att
from leaguebot.core.db.database import get_connection
from leaguebot.core.models.change import GuildUnavailable, StepFailedOnDiscord
from leaguebot.core.services.placement_service import PlacementService
from leaguebot.core.utils.league_bot import LeagueBot
from leaguebot.core.utils.league_server import league_guild


class AttendanceAfterReview:
    """See the module docstring. Constructed as ``AttendanceAfterReview(bot, placement)``."""

    def __init__(self, bot: LeagueBot, placement: PlacementService) -> None:
        self._bot = bot
        self._placement = placement

    @property
    def _db_path(self) -> str:
        return self._bot.db_path

    async def _enabled(self) -> bool:
        async with get_connection(self._db_path) as db:
            return await self._enabled_on(db)

    @staticmethod
    async def _enabled_on(db: aiosqlite.Connection) -> bool:
        row = await (await db.execute("SELECT module_enabled FROM attendance_config")).fetchone()
        return bool(row and row["module_enabled"])

    async def _guild(self) -> discord.Guild:
        guild = await league_guild(self._bot)
        if guild is None:
            raise GuildUnavailable("the league's server is not in the cache")
        return guild

    async def record_on(
        self, db: aiosqlite.Connection, round_id: int, division_id: int, pardons: Any,
        now: datetime,
    ) -> None:
        if not await self._enabled_on(db):
            return
        await _att.record_attendance_from_results_on(db, round_id, division_id)
        for pardon in pardons:
            await db.execute(
                "INSERT OR IGNORE INTO attendance_pardons "
                "(attendance_id, pardon_type, justification, granted_by, granted_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (pardon.attendance_id, pardon.pardon_type, pardon.justification,
                 pardon.grantor_id, now.isoformat()),
            )
        await _att._recalculate_forward(
            self._db_path, round_id, division_id, recompute="none", db=db
        )

    async def rewrite_pardons_on(
        self, db: aiosqlite.Connection, round_id: int, pardons: Any, now: datetime
    ) -> None:
        if not await self._enabled_on(db):
            return
        await _att._rewrite_round_pardons_on(db, round_id, list(pardons), now)

    async def recalculate_on(
        self, db: aiosqlite.Connection, round_id: int, division_id: int
    ) -> None:
        if not await self._enabled_on(db):
            return
        await _att._recalculate_forward(
            self._db_path, round_id, division_id, recompute="round", db=db
        )

    async def post_sheet(
        self, round_id: int, division_id: int, *, sanctioned: set[int], as_text: bool
    ) -> None:
        if not await self._enabled():
            return
        await _att.post_attendance_sheet(
            self._bot, await self._guild(), self._db_path, round_id, division_id,
            sanctioned_profile_ids=sanctioned or None,
            raise_on_failure=True, as_text=as_text,
        )

    async def sanction_candidates(self, round_id: int, division_id: int) -> list[dict[str, Any]]:
        if not await self._enabled():
            return []
        owed, _signed_off = await _att.owed_sanctions(self._db_path, round_id, division_id)
        return owed

    async def apply_sanction(
        self, round_id: int, division_id: int, candidate: dict[str, Any],
        actor: discord.abc.User | None,
    ) -> str | None:
        if not await self._enabled():
            return None
        try:
            return await _att.apply_sanction(
                self._bot, await self._guild(), self._db_path, self._placement, round_id,
                division_id, candidate, actor=actor, post_line=False,
            )
        except _att.SanctionNotApplicable as error:
            # Worded as a failure the queue stops on, so its notice names what to repair.
            raise StepFailedOnDiscord(str(error)) from None

    async def announce_sanction(
        self, round_id: int, division_id: int, candidate: dict[str, Any], *, as_text: bool
    ) -> None:
        if not await self._enabled():
            return
        from leaguebot.results.services import verdict_announcement_service as vas

        autoreserve, autosack = await _att._thresholds(self._db_path)
        sanction = candidate["sanction"]
        await vas.announce_sanction(
            self._bot, self._db_path, round_id, int(candidate["driver_user_id"]),
            await _att._display_name_of(self._db_path, candidate["driver_profile_id"]),
            sanction, (autosack if sanction == "AUTOSACK" else autoreserve) or 0,
            as_text=as_text,
        )

    async def refresh_lineup(self, division_id: int) -> None:
        if not await self._enabled():
            return
        await self._placement.refresh_lineup(await self._guild(), division_id)

    async def sync_hint(self, division_id: int, round_id: int) -> str:
        return await _att.sync_hint(self._db_path, division_id, round_id)
