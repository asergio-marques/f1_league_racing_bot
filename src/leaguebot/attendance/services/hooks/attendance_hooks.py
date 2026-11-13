"""What attendance gives core's hooks: its deletes, its part of a round's window and of a review.

Core declares the hooks and attendance signs up to them in its wiring (architecture.md, "How
modules and core fit together"). Each delete clears attendance's own rows on the connection core's
delete was handed and commits nothing; the readers give core attendance's lead times, its section
of a season's review and the channel lines the placements review shows.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TYPE_CHECKING

import aiosqlite

from leaguebot.attendance.services.check_in.attendance_config_service import AttendanceService
from leaguebot.core.models.round import AttendanceWindows
from leaguebot.core.services.modules.review_sections import Review, ReviewSection
from leaguebot.core.services.seasons.approval_checks import channel_fault

if TYPE_CHECKING:
    from leaguebot.core.services.modules.module_service import ModuleService


async def rounds_deleted_on(db: aiosqlite.Connection, round_ids: list[int]) -> None:
    """Delete attendance's rows of *round_ids* on *db*, committing nothing: each driver's
    attendance of the round, with the pardons that hang from it, and the round's RSVP embed.

    Core hands it the round ids as it deletes the rounds, in the delete's own save, so that the
    module's rows go with the round or not at all (architecture.md, "The database").
    """
    marks = ",".join("?" * len(round_ids))
    await db.execute(
        f"DELETE FROM driver_round_attendance WHERE round_id IN ({marks})", round_ids  # noqa: S608
    )
    await db.execute(
        f"DELETE FROM rsvp_embed_messages WHERE round_id IN ({marks})", round_ids  # noqa: S608
    )


async def divisions_deleted_on(db: aiosqlite.Connection, division_ids: list[int]) -> None:
    """Delete attendance's settings of *division_ids* on *db*, committing nothing: the
    division's channels for the RSVP and the attendance sheet."""
    marks = ",".join("?" * len(division_ids))
    await db.execute(
        f"DELETE FROM attendance_division_config WHERE division_id IN ({marks})",  # noqa: S608
        division_ids,
    )


async def profiles_deleted_on(db: aiosqlite.Connection, profile_ids: list[int]) -> None:
    """Delete attendance's rows of the drivers *profile_ids* on *db*, committing nothing: each
    driver's attendance of every round, with the pardons that hang from it (they go by the
    cascade from the attendance row).

    Core hands it the profile ids as it deletes the drivers, in the delete's own save, so that
    the module's rows go with the drivers or not at all (architecture.md, "The database").
    """
    marks = ",".join("?" * len(profile_ids))
    await db.execute(
        f"DELETE FROM driver_round_attendance WHERE driver_profile_id IN ({marks})",  # noqa: S608
        profile_ids,
    )


async def profiles_merged_on(db: aiosqlite.Connection, kept_id: int, absorbed_id: int) -> None:
    """Move attendance's rows of the driver *absorbed_id* onto the driver *kept_id* on *db*,
    committing nothing: their attendance of every round.

    Core hands it both profile ids as it merges the drivers, inside the merge's one transaction,
    before the absorbed profile is deleted (architecture.md, "The database"). The merge's
    refusals guarantee that the two drivers never held one round between them.
    """
    await db.execute(
        "UPDATE driver_round_attendance SET driver_profile_id = ? WHERE driver_profile_id = ?",
        (kept_id, absorbed_id),
    )


async def bot_packed_on(db: aiosqlite.Connection) -> None:
    """Clear attendance's rows tied to the server the bot leaves, on *db*, committing nothing:
    the record of each RSVP embed posted, by which it is edited, and each division's attendance
    message id.

    Core hands it the pack's connection, so that attendance's rows go with the rest of the pack
    or not at all (architecture.md, "The database"). Only the ids go: a message already posted
    stays where it is, and every driver's attendance stays with its round.
    """
    await db.execute("DELETE FROM rsvp_embed_messages")
    await db.execute("UPDATE attendance_division_config SET attendance_message_id = NULL")


# ── Row-to-dataclass helpers ───────────────────────────────────────────────


async def round_window(
    attendance: AttendanceService, modules: "ModuleService", *, amending: bool
) -> AttendanceWindows | None:
    """Attendance's part of a round's timing windows read: its three lead times, or None where
    the module is off, since a league without attendance has no check-in to lose or to be inside
    the window of. Counts as off for an amendment too, so *amending* changes nothing here. Reads
    only: with no configuration stored, the packaged defaults answer and no row is created."""
    if not await modules.is_attendance_enabled():
        return None
    config = await attendance.get_config_or_defaults()
    return AttendanceWindows(
        notice_days=config.rsvp_notice_days,
        last_notice_hours=config.rsvp_last_notice_hours,
        deadline_hours=config.rsvp_deadline_hours,
    )


async def review_section(
    service: AttendanceService,
    modules: "ModuleService",
    *,
    review: Review,
    season_id: int,
    division_ids: list[int],
) -> ReviewSection | None:
    """Attendance's section of a season review: its configuration; None where the module is off.

    Subscribed to core's `review_sections_read`. A module never configured has no configuration
    to show, its section then empty. Reads only.
    """
    if not await modules.is_attendance_enabled():
        return None
    config = await service.get_config()
    lines: list[str] = []
    if config:
        autoreserve = config.autoreserve_threshold
        autosack = config.autosack_threshold
        autoreserve_text = f"{autoreserve} pts" if autoreserve is not None else "*(not set)*"
        autosack_text = f"{autosack} pts" if autosack is not None else "*(not set)*"
        last_notice = (
            f"{config.rsvp_last_notice_hours}h before deadline"
            if config.rsvp_last_notice_hours
            else "*(disabled)*"
        )
        lines = [
            "**Attendance Config**",
            f"  • RSVP notice: {config.rsvp_notice_days} day(s) before race",
            f"  • Last notice: {last_notice}",
            f"  • Deadline: {config.rsvp_deadline_hours}h before race",
            f"  • No-RSVP penalty: {config.no_rsvp_penalty} pt(s)",
            f"  • Absent penalty: {config.absent_penalty} pt(s)",
            f"  • No-show penalty: {config.no_show_penalty} pt(s)",
            f"  • Auto-reserve threshold: {autoreserve_text}",
            f"  • Auto-sack threshold: {autosack_text}",
            "",
        ]
    return ReviewSection(module="attendance", lines=lines)


async def division_lines(
    service: AttendanceService,
    modules: "ModuleService",
    *,
    season_id: int,
    division_ids: list[int],
) -> dict[int, list[str]]:
    """The RSVP and attendance channel lines the placements review shows for each division in
    *division_ids*, "*(not set)*" where a channel is not; nothing where the module is off.

    Subscribed to core's `division_lines_read`, which hands the season (as it does every
    subscriber; the divisions named are what is read) and its divisions. Reads only.
    """
    if not await modules.is_attendance_enabled():
        return {}
    lines: dict[int, list[str]] = {}
    for division_id in division_ids:
        channels = await service.get_division_config(division_id)
        rsvp = (
            f"<#{channels.rsvp_channel_id}>"
            if channels and channels.rsvp_channel_id
            else "*(not set)*"
        )
        attendance = (
            f"<#{channels.attendance_channel_id}>"
            if channels and channels.attendance_channel_id
            else "*(not set)*"
        )
        lines[division_id] = [
            f"  RSVP channel: {rsvp}",
            f"  Attendance channel: {attendance}",
        ]
    return lines


async def division_channel_faults(
    service: AttendanceService,
    modules: "ModuleService",
    *,
    divisions: Mapping[int, str],
    guild: Any = None,
) -> dict[int, list[str]]:
    """Each division's RSVP channel and then its attendance channel, in that order, that is not
    set or not on the *guild*, by division id; nothing where the attendance module is off.

    Subscribed to core's `division_channel_faults_read`, which hands the divisions the
    confirmation judges (id to name) and the server. A division with no fault is given an empty
    list. With no *guild*, only whether each channel is set is judged. Reads only.
    """
    if not await modules.is_attendance_enabled():
        return {}
    found: dict[int, list[str]] = {}
    for division_id, name in divisions.items():
        channels = await service.get_division_config(division_id)
        lines: list[str] = []
        for value, label, command in (
            (channels.rsvp_channel_id if channels else None, "RSVP channel",
             "/attendance channel rsvp"),
            (channels.attendance_channel_id if channels else None, "attendance channel",
             "/attendance channel attendance"),
        ):
            fault = await channel_fault(guild, name, value, label, command)
            if fault:
                lines.append(fault)
        found[division_id] = lines
    return found
