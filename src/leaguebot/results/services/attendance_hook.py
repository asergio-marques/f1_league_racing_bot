"""What a round's review asks of attendance (#439, slice 2, plan 2.6).

`AttendanceAfterReview` is the hook the builder hands the review's change types, so that results
imports nothing of attendance: the change types call these methods and attendance implements them
(`leaguebot.attendance.services.attendance_after_review`). It is attached to the bot as
`bot.attendance_after_review` and declared on `LeagueBot`.

**Each method checks attendance's switch itself and does nothing while it is off**
(architecture.md, "How modules and core fit together"), save `change_sanction_roles`, which acts
whatever the switch says: a sanction already saved is owed its roles. The writers read the switch
on the connection they are handed. **The writers write on the save they are handed and commit nothing**, so the round's
attendance lands with the penalties and points or not at all. **The posting methods raise**
`StepFailedOnDiscord` where they could not post, for the queue to stop on and try again.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, Protocol, TypeVar

import aiosqlite
import discord

_Pardon = TypeVar("_Pardon")


class AttendanceAfterReview(Protocol):
    """Attendance's share of a round's review."""

    async def enabled(self) -> bool:
        """Whether attendance is on: asked by a job of results' own that serves only attendance's
        posts, the heading over a round's sanction cards, which has nothing to head once
        attendance is off."""

    async def record_on(
        self, db: aiosqlite.Connection, round_id: int, division_id: int, pardons: Any,
        now: datetime,
    ) -> None:
        """Record who attended the round, write the staged *pardons* (stamped *now*, kept if
        already there) and award the round's points, carrying every later round's running
        total on from it."""

    async def pardons_to_review(self, pardons: Sequence[_Pardon]) -> list[_Pardon]:
        """The *pardons* an amendment's review may offer the manager: all of them while
        attendance is on, none while it is off, where the approval would write none of them
        back."""

    async def rewrite_pardons_on(
        self, db: aiosqlite.Connection, round_id: int, pardons: Any, now: datetime
    ) -> None:
        """Leave the round carrying exactly *pardons*: an amendment's."""

    async def recalculate_on(
        self, db: aiosqlite.Connection, round_id: int, division_id: int
    ) -> None:
        """Rebuild the round's attended flags from its results, in both directions (FR-028),
        and carry the totals through every later final round: an amendment's.

        Saved on *db*, the caller's connection, and committed by the caller: the recalculation
        is one transaction with whatever else the approval writes (#187). Pardons already
        recorded are kept (FR-029) and the totals are carried forward through every later
        approved round (FR-030). The sheet and the sanctions that follow (FR-031) are planned
        against the latest approved round of the division, where the totals now stand (#238).
        Does nothing while attendance is off."""

    async def post_sheet(
        self, round_id: int, division_id: int, *, sanctioned: set[int], as_text: bool
    ) -> dict[str, Any] | None:
        """Post the division's attendance sheet as at *round_id*, annotating *sanctioned*
        profiles. Raises where it could not post; *as_text* leaves the graphic out.

        Saves and deletes nothing (#439). Gives the sheet posted and the one it replaces (`new`,
        `old`, `channel_id`, `takes_slot`) for the job to save and to plan the old one's
        deletion, or an empty result where nothing was due; None while attendance is off. Raises
        where the division has no attendance channel, for the job to stop the queue."""

    async def record_sheet_on(
        self, db: aiosqlite.Connection, division_id: int, posted: Mapping[str, Any]
    ) -> None:
        """Save the sheet's id as the division's live sheet, on the save handed, committing
        nothing; it reads attendance's switch on *db* and writes nothing while attendance is
        off."""

    async def sanction_candidates(self, round_id: int, division_id: int) -> list[dict[str, Any]]:
        """Each driver still owed a sanction at *round_id*: a dict of ``driver_profile_id``,
        ``driver_user_id``, ``sanction`` (``"AUTOSACK"`` or ``"AUTORESERVE"``) and
        ``other_divisions``, the divisions an autosack reaches, whose sheets are posted again.
        A driver already sanctioned is not listed, so a sanction tried again applies only what
        is owed."""

    async def check_sanction(
        self, round_id: int, division_id: int, candidate: dict[str, Any]
    ) -> None:
        """Refuse a candidate's sanction that cannot be saved, before it is: an autoreserve in a
        division with no Reserve team, or one the capacity guards refuse. Raises
        `StepFailedOnDiscord` carrying the reason; does nothing for an autosack, and while
        attendance is off."""

    async def apply_sanction_on(
        self, db: aiosqlite.Connection, round_id: int, division_id: int,
        candidate: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Save one candidate's sanction on the save handed, committing nothing, as the bot.

        Every read and write is on *db*, and nothing else is awaited: no role is changed and
        nothing posted. Returns ``line``, the log line the job carries, and ``roles``, None for
        a test driver, else the ``user_id`` and ``profile_id`` with the role ids to ``revoke``
        and to ``grant`` for `change_sanction_roles`. None where attendance is off, and only
        there, so None tells the job nothing was applied. Raises where the sanction does not
        apply."""

    async def change_sanction_roles(
        self, user_id: int, profile_id: int, *, revoke: list[int], grant: list[int]
    ) -> dict[str, Any]:
        """Change the roles of a driver whose sanction was saved: take *revoke* and give *grant*,
        less what the driver is entitled to as they stand now (so a retry after a manager
        placed them again keeps the roles they hold). **Acts whatever attendance's switch says**,
        the one method that does: a sanction already saved is owed its roles. Raises
        `StepFailedOnDiscord` where Discord refuses, where the member cannot be fetched for any
        reason but absence, or where a role to give is no longer on the server; returns
        ``{"absent": True}`` for a member who left the server."""

    async def announce_sanction(
        self, round_id: int, division_id: int, candidate: dict[str, Any], *, as_text: bool
    ) -> None:
        """Announce one candidate's sanction in the verdicts channel, beneath the banner of
        *round_id*, which is the round whose verdicts it follows. Raises where it could not
        post."""

    async def refresh_lineup(
        self, division_id: int, *, as_text: bool
    ) -> dict[str, Any] | None:
        """Post the division's lineup afresh, as text where *as_text* (a job tried again, #439).
        Raises where it could not post.

        Gives the message posted and the one it replaces (`new`, `old`, `channel_id`,
        `division_name`, `form`), for the job to save and to plan the old one's deletion; None
        while attendance is off."""

    async def sync_hint(self, division_id: int, round_id: int) -> str:
        """The line telling a manager how to finish sanctions that did not all apply."""
