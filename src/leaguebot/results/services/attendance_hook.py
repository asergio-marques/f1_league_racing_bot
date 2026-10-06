"""What a round's review asks of attendance (#439, slice 2, plan 2.6).

`AttendanceAfterReview` is the hook the builder hands the review's change types, so that results
imports nothing of attendance: the change types call these methods and attendance implements them
(`leaguebot.attendance.services.attendance_after_review`). It is attached to the bot as
`bot.attendance_after_review` and declared on `LeagueBot`.

**Each method checks attendance's switch itself and does nothing while it is off**
(architecture.md, "How modules and core fit together"); the writers read it on the connection they
are handed. **The writers write on the save they are handed and commit nothing**, so the round's
attendance lands with the penalties and points or not at all. **The posting methods raise**
`StepFailedOnDiscord` where they could not post, for the queue to stop on and try again.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

import aiosqlite
import discord


class AttendanceAfterReview(Protocol):
    """Attendance's share of a round's review."""

    async def record_on(
        self, db: aiosqlite.Connection, round_id: int, division_id: int, pardons: Any,
        now: datetime,
    ) -> None:
        """Record who attended the round, write the staged *pardons* (stamped *now*, kept if
        already there) and award the round's points, carrying every later round's running
        total on from it."""

    async def rewrite_pardons_on(
        self, db: aiosqlite.Connection, round_id: int, pardons: Any, now: datetime
    ) -> None:
        """Leave the round carrying exactly *pardons*: an amendment's."""

    async def recalculate_on(
        self, db: aiosqlite.Connection, round_id: int, division_id: int
    ) -> None:
        """Rebuild the round's attended flags from its results, in both directions (FR-028),
        and carry the totals through every later final round: an amendment's."""

    async def post_sheet(
        self, round_id: int, division_id: int, *, sanctioned: set[int], as_text: bool
    ) -> None:
        """Post the division's attendance sheet as at *round_id*, annotating *sanctioned*
        profiles. Raises where it could not post; *as_text* leaves the graphic out."""

    async def sanction_candidates(self, round_id: int, division_id: int) -> list[dict[str, Any]]:
        """Each driver still owed a sanction at *round_id*: a dict of ``driver_profile_id``,
        ``driver_user_id``, ``sanction`` (``"AUTOSACK"`` or ``"AUTORESERVE"``) and
        ``other_divisions``, the divisions an autosack reaches, whose sheets are posted again.
        A driver already sanctioned is not listed, so a sanction tried again applies only what
        is owed."""

    async def apply_sanction(
        self, round_id: int, division_id: int, candidate: dict[str, Any],
        actor: discord.abc.User | None,
    ) -> str | None:
        """Apply one candidate's sanction as *actor* (None is the bot), and return its log line,
        which it does not post: the job carries it, so the queue writes it with the job's mark.
        None where attendance is off. Raises where it does not apply: a division with no
        Reserve team, or a role change Discord refuses."""

    async def announce_sanction(
        self, round_id: int, division_id: int, candidate: dict[str, Any], *, as_text: bool
    ) -> None:
        """Announce one candidate's sanction in the verdicts channel. Raises where it could
        not post."""

    async def refresh_lineup(self, division_id: int) -> None:
        """Post the division's lineup afresh. Raises where it could not post."""

    async def sync_hint(self, division_id: int, round_id: int) -> str:
        """The line telling a manager how to finish sanctions that did not all apply."""
