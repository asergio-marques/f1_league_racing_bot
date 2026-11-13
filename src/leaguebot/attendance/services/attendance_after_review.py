"""Attendance's share of a round's review, as the hook the builder hands results' change types
(#439, slice 2, plan 2.6; the protocol is `results/services/module/attendance_hook.py`).

Built once by the entry point, handed the placement service, the team-role service and the lineup
poster and attached as `bot.attendance_after_review`. It uses the bot for Discord alone (the league's server, the log
channel, posting) and never looks another service up on it: the services are the ones it was
handed, and attendance's switch is read from the database, on the connection a writer is
handed.

**Each method does nothing while attendance is off**, save three: `enabled` reports the switch,
for the one job of results' own that serves attendance alone (the heading over the sanction cards);
`sync_hint` reads none; and `change_sanction_roles` acts whatever it says, a sanction already saved
being owed its roles (the attendance specification's "Sanctions that do not apply"). A writer
writes on the save it is handed and commits nothing, so a fault in the attendance fails
the approval whole. An amendment's recalculation rebuilds the amended round's attended flags both
ways (FR-028); the first pass records upgrade-only (FR-003).

**A sanction is one driver's**, and one that does not apply raises. The candidate carries the
profile, the Discord user, the sanction and the divisions an autosack reaches; the thresholds
and the driver's total are read again when the sanction is applied or announced. It is checked
(`check_sanction`, an autoreserve's capacity guards), saved on the caller's save
(`apply_sanction_on`, naming the roles to change) and has its roles changed
(`change_sanction_roles`) in three calls (#439, slice 8).

**The league's server** is core's `league_guild`, the one route from a job to it; where none is
claimed or it is not in the cache the method raises `GuildUnavailable`.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Any, TypeVar

import aiosqlite
import discord

from leaguebot.attendance.services.scoring import (
    attendance_pardons,
    attendance_recompute,
    attendance_scoring,
)
from leaguebot.attendance.services import attendance_sanctions, attendance_sheet
from leaguebot.attendance.services.check_in import check_in_record
from leaguebot.attendance.services.module_switch_settings import attendance_enabled_on
from leaguebot.core.db.database import get_connection
from leaguebot.core.models.change import GuildUnavailable, PlannedStep, StepFailedOnDiscord
from leaguebot.core.models.refusal import Refused
from leaguebot.core.services.drivers import lineup_posting
from leaguebot.core.services.drivers.lineup_posting import LineupPoster
from leaguebot.core.services.drivers.placement_service import PlacementService
from leaguebot.core.services.drivers.team_role_service import TeamRoleService
from leaguebot.core.utils.league_bot import LeagueBot
from leaguebot.core.utils.league_server import league_guild

_Pardon = TypeVar("_Pardon")


def sanction_card_texts(sanction_type: str, driver_ref: str, threshold: int) -> tuple[str, str, str]:
    """The label, description and justification of an attendance sanction's card.

    The wording is attendance's (its specification states it), so it is composed here and handed
    to results' `announce_sanction`, which posts it and composes none. *sanction_type* is
    ``AUTOSACK`` or ``AUTORESERVE``, *driver_ref* how the card names the driver and *threshold*
    the point limit the driver reached.
    """
    if sanction_type == "AUTOSACK":
        return (
            "Sacked",
            "Sacked due to accumulation of attendance points.",
            f"{driver_ref} has reached the {threshold} attendance point limit in order to be "
            "removed from their full-time seat. Therefore, they have been removed from all "
            "driving seats effective immediately, and their current full-time seat will be "
            "offered to another driver.",
        )
    return (
        "Moved to Reserve",
        "Moved to Reserve due to accumulation of attendance points.",
        f"{driver_ref} has reached the {threshold} attendance point limit in order to be "
        "removed from their full-time seat. Therefore, they have been demoted to a reserve "
        "driver effective immediately, and their current full-time seat will be offered to "
        "another driver.",
    )


class AttendanceAfterReview:
    """See the module docstring. Constructed as ``AttendanceAfterReview(bot, placement,
    roles=roles, lineups=lineups)``: *roles* is the team-role service that gives and takes a
    sanction's roles and *lineups* the poster of the lineups a review owes. Each is optional so
    that a hook which is never asked to change a sanction's roles or to post a lineup is built
    without one, and asking it then raises."""

    def __init__(
        self,
        bot: LeagueBot,
        placement: PlacementService,
        *,
        roles: TeamRoleService | None = None,
        lineups: LineupPoster | None = None,
    ) -> None:
        self._bot = bot
        self._placement = placement
        self._roles = roles
        self._lineups = lineups

    @property
    def _db_path(self) -> str:
        return self._bot.db_path

    async def enabled(self) -> bool:
        async with get_connection(self._db_path) as db:
            return await attendance_enabled_on(db)

    async def _guild(self) -> discord.Guild:
        guild = await league_guild(self._bot)
        if guild is None:
            raise GuildUnavailable("the league's server is not in the cache")
        return guild

    async def record_on(
        self, db: aiosqlite.Connection, round_id: int, division_id: int, pardons: Any,
        now: datetime,
    ) -> None:
        if not await attendance_enabled_on(db):
            return
        await attendance_scoring.record_attendance_from_results_on(db, round_id, division_id)
        for pardon in pardons:
            await db.execute(
                "INSERT OR IGNORE INTO attendance_pardons "
                "(attendance_id, pardon_type, justification, granted_by, granted_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (pardon.attendance_id, pardon.pardon_type, pardon.justification,
                 pardon.grantor_id, now.isoformat()),
            )
        await attendance_recompute._recalculate_forward(
            self._db_path, round_id, division_id, recompute="none", db=db
        )

    async def pardons_to_review(self, pardons: Sequence[_Pardon]) -> list[_Pardon]:
        if not await self.enabled():
            return []
        return list(pardons)

    async def rewrite_pardons_on(
        self, db: aiosqlite.Connection, round_id: int, pardons: Any, now: datetime
    ) -> None:
        if not await attendance_enabled_on(db):
            return
        await attendance_pardons._rewrite_round_pardons_on(db, round_id, list(pardons), now)

    async def pardons_of_on(self, db: aiosqlite.Connection, round_id: int) -> Any:
        return await attendance_pardons.pardons_of_on(db, round_id)

    async def put_back_pardons_on(
        self, db: aiosqlite.Connection, round_id: int, saved: Any
    ) -> None:
        if not await attendance_enabled_on(db):
            return
        await attendance_pardons.put_back_pardons_on(db, round_id, saved)

    async def attendance_row_of(
        self, round_id: int, division_id: int, profile_id: int
    ) -> dict[str, Any] | None:
        async with get_connection(self._db_path) as db:
            return await attendance_scoring.attendance_row_of(db, round_id, division_id, profile_id)

    async def latest_scored_round(self, division_id: int) -> int | None:
        async with get_connection(self._db_path) as db:
            return await attendance_scoring.latest_scored_round(db, division_id)

    async def recorded_pardons(self, round_id: int) -> list[dict[str, Any]]:
        return await attendance_pardons.recorded_pardons(self._db_path, round_id)

    async def recalculate_on(
        self, db: aiosqlite.Connection, round_id: int, division_id: int
    ) -> None:
        if not await attendance_enabled_on(db):
            return
        await attendance_recompute._recalculate_forward(
            self._db_path, round_id, division_id, recompute="round", db=db
        )

    async def check_in_read_on(
        self, db: aiosqlite.Connection, target: dict[str, Any]
    ) -> list[PlannedStep]:
        if not await attendance_enabled_on(db):
            return []
        return list(await check_in_record.read_check_in_on(db, target))

    async def recalculation_faults(self, season_id: int, guild: discord.Guild | None) -> list[str]:
        if not await self.enabled():
            return []
        return await attendance_recompute.recalculation_faults(
            self._db_path, season_id, guild, self._bot, cancelled_too=True
        )

    async def post_sheet(
        self, round_id: int, division_id: int, *, sanctioned: set[int], as_text: bool
    ) -> dict[str, Any] | None:
        if not await self.enabled():
            return None
        posted = await attendance_sheet.post_attendance_sheet(
            self._bot, await self._guild(), self._db_path, round_id, division_id,
            sanctioned_profile_ids=sanctioned or None,
            as_text=as_text,
        )
        return attendance_sheet.sheet_result(posted)

    async def record_sheet_on(
        self, db: aiosqlite.Connection, division_id: int, posted: Mapping[str, Any]
    ) -> None:
        if not await attendance_enabled_on(db):
            return
        await attendance_sheet.record_sheet_on(db, division_id, posted)

    async def sanction_candidates(self, round_id: int, division_id: int) -> list[dict[str, Any]]:
        if not await self.enabled():
            return []
        owed, _signed_off = await attendance_sanctions.owed_sanctions(self._db_path, round_id, division_id)
        return owed

    async def check_sanction(
        self, round_id: int, division_id: int, candidate: dict[str, Any]
    ) -> None:
        if not await self.enabled():
            return
        try:
            await attendance_sanctions.check_sanction(
                self._db_path, self._placement, round_id, division_id, candidate
            )
        except (attendance_sanctions.SanctionNotApplicable, Refused) as error:
            # Worded as a failure the queue stops on, so its notice names what to repair.
            raise StepFailedOnDiscord(str(error)) from None

    async def apply_sanction_on(
        self, db: aiosqlite.Connection, round_id: int, division_id: int,
        candidate: dict[str, Any], *, now: datetime,
    ) -> dict[str, Any] | None:
        if not await attendance_enabled_on(db):
            return None
        bot_user = self._bot.user
        # Sanctions are enforced by a bot that has logged in, which is when it has a user.
        assert bot_user is not None, "sanctions enforced before the bot logged in"
        try:
            return await attendance_sanctions.apply_sanction_on(
                db, self._placement, round_id, division_id, candidate,
                actor_id=bot_user.id, actor_name=str(bot_user), now=now,
            )
        except attendance_sanctions.SanctionNotApplicable as error:
            raise StepFailedOnDiscord(str(error)) from None

    async def change_sanction_roles(
        self, user_id: int, profile_id: int, *, revoke: list[int], grant: list[int]
    ) -> dict[str, Any]:
        # Acts whatever attendance's switch says, which `enabled` reports and `sync_hint` never
        # reads: a sanction already saved is owed its roles even once attendance is turned off
        # (the attendance specification's "Sanctions that do not apply").
        guild = await self._guild()
        roles = self._roles
        if roles is None:
            raise TypeError("the hook was built without a team-role service to change roles with")
        entitled = await self._placement.driver_role_ids(profile_id)
        to_revoke = [role_id for role_id in revoke if role_id not in entitled]
        to_grant = [role_id for role_id in grant if role_id in entitled]
        present = True
        if to_revoke:
            present = await roles.revoke_roles(
                guild, user_id, *to_revoke, reason="Attendance sanction"
            )
        if present and to_grant:
            present = await roles.grant_roles(guild, user_id, *to_grant)
        if not present:
            return {"absent": True}
        return {"revoked": to_revoke, "granted": to_grant}

    async def announce_sanction(
        self, round_id: int, division_id: int, candidate: dict[str, Any], *, as_text: bool
    ) -> Callable[[str, str], str] | None:
        if not await self.enabled():
            return None
        from leaguebot.results.services.review import verdict_announcement_service as vas

        autoreserve, autosack = await attendance_sanctions._thresholds(self._db_path)
        sanction = candidate["sanction"]
        driver_discord_id = int(candidate["driver_user_id"])
        display_name = await attendance_sanctions._display_name_of(self._db_path, driver_discord_id)
        driver_ref = f"<@{driver_discord_id}>"
        if display_name:
            driver_ref += f" ({display_name})"
        label, description, justification = sanction_card_texts(
            sanction, driver_ref, (autosack if sanction == "AUTOSACK" else autoreserve) or 0
        )
        return await vas.announce_sanction(
            self._bot, self._db_path, round_id, driver_discord_id, display_name,
            label, description, justification, as_text=as_text,
        )

    async def refresh_lineup(
        self, division_id: int, *, as_text: bool
    ) -> dict[str, Any] | None:
        if not await self.enabled():
            return None
        lineups = self._lineups
        if lineups is None:
            raise TypeError("the hook was built without a lineup poster to post a lineup with")
        posted = await lineups.refresh_lineup(
            await self._guild(), division_id, as_text=as_text
        )
        return lineup_posting.posted_result(posted)

    async def sync_hint(self, division_id: int, round_id: int) -> str:
        return await attendance_sanctions.sync_hint(self._db_path, division_id, round_id)
