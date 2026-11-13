"""A division's attendance sheet: the rows it lists, the graphic and text it is drawn as, and its posting.

`post_attendance_sheet` posts a new sheet that replaces the prior one, `_sheet_rows` and
`_opening_attendance_rows` choose the drivers it lists, `_sheet_attachment` and `_round_grid` draw
it, and `record_sheet_on` saves the one posted as the division's live one. The change that asks for
a sheet is in `sheet_posting`.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import aiosqlite
import discord

from leaguebot.core.db.database import get_connection
from leaguebot.core.models.change import StepFailedOnDiscord
from leaguebot.core.models.classification_occasion import ClassificationOccasion
from leaguebot.core.models.round import RoundStatus
from leaguebot.core.services.drivers.driver_service import driver_identities_on
from leaguebot.core.services.drivers.team_service import TeamService
from leaguebot.core.services.seasons.season_service import (
    division_on,
    division_rounds_in_order_on,
    round_facts_on,
)
from leaguebot.core.services.settings.channel_registry_service import as_text_channel
from leaguebot.core.services.posting.standing_post_handler import post_standing
from leaguebot.core.utils.in_a_job import posts_in_a_job
from leaguebot.core.utils.league_bot import LeagueBot


log = logging.getLogger(__name__)


@posts_in_a_job
async def post_attendance_sheet(
    bot: LeagueBot,
    guild: discord.Guild,
    db_path: str,
    round_id: int,
    division_id: int,
    sanctioned_profile_ids: set[int] | None = None,
    occasion: ClassificationOccasion = ClassificationOccasion.AFTER_ROUND,
    *,
    as_text: bool = False,
) -> PostedSheet | None:
    """Post a new sheet to the division's attendance channel, which replaces the prior one.

    **This posts and nothing else** (#439): it saves no id and deletes no message. It returns
    the `PostedSheet` (the message posted, its channel, the sheet it replaces, whether the
    occasion takes the division's live slot, and a line for each fault the graphic fell back from) for the job to save with its mark
    (`record_sheet_on`) and to plan the old sheet's deletion after (`standing_posts`); `None`
    where nothing was posted.

    **A sheet that cannot be posted is raised** (#439): a channel the division was given and the
    guild no longer holds, or a send Discord refuses, raises `StepFailedOnDiscord`, so the queue
    stops on the job that called this and tries it again, and nothing is put on the old retry
    queue, which would post a second copy beside the retry. A division never given a channel
    raises too, its message naming `/attendance channel attendance`, so the queue stops there and
    does not complete having posted nothing; a cancelled round posts nothing and raises nothing.
    *as_text* leaves the graphic out, which is how a job retries (Constitution XIV rule 8).

    Pass ``sanctioned_profile_ids`` to annotate those drivers with "(reached point limit)"
    on this posting.

    **The replacement ordering (Constitution XIV.8).** The replacement is produced and posted
    **before** the message it replaces is deleted, so that at no instant is the channel
    without a sheet. A failed post therefore leaves the division holding the sheet it had,
    rather than none at all.

    This ordering belongs to the flow and the image path inherits it, rather than carrying a
    rule of its own: two orderings in one flow would drift, and the half left deleting first
    would be the fallback — the path reached precisely because something has already gone
    wrong. There is exactly one send site below; the deletion of the sheet replaced is the job
    after it.
    """
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT * FROM attendance_division_config WHERE division_id = ?",
            (division_id,),
        )
        row = await cursor.fetchone()

    if row is None or not row["attendance_channel_id"]:
        log.warning("post_attendance_sheet: no attendance_channel_id configured for division %s", division_id)
        async with get_connection(db_path) as db:
            named = await division_on(db, division_id)
        raise StepFailedOnDiscord(
            f"**{named.name if named else 'the division'}** has no attendance channel set: "
            "set one with `/attendance channel attendance`, then press Retry."
        )

    # A round recorded as cancelled distributes no attendance points and produces no sheet,
    # whatever the `attendance` toggle says (FR-047). The recording flow already skips a
    # cancelled round upstream; the guard stands here too so the rule holds wherever this is
    # called from, and so that no graphic is ever generated for a posting that will not happen
    # (XIV.8 — no posting, no graphic).
    async with get_connection(db_path) as db:
        round_facts = await round_facts_on(db, round_id)
    if (
        occasion.names_a_round
        and round_facts is not None
        and round_facts.status is RoundStatus.CANCELLED
    ):
        # A season-boundary sheet is about the season and not about a round, so no round's
        # cancellation bears on it (see `leaguebot.core.models.classification_occasion`).
        log.info("post_attendance_sheet: skipping cancelled round %s", round_id)
        return None

    channel_id = int(row["attendance_channel_id"])
    prior_msg_id = row["attendance_message_id"]

    channel = as_text_channel(guild.get_channel(channel_id))
    if channel is None:
        log.warning("post_attendance_sheet: channel %s not found for division %s", channel_id, division_id)
        raise StepFailedOnDiscord(
            f"the attendance channel (id {channel_id}) is not in the server"
        )

    # Build sheet content.
    async with get_connection(db_path) as db:
        driver_rows = await _sheet_rows(db, round_id, division_id)

        # The opening sheet stands before any round has been attended, so
        # `driver_round_attendance` holds nothing to read and the query above returns
        # nothing. The division's seated drivers, all on zero, are the sheet.
        if occasion is ClassificationOccasion.SEASON_OPENING:
            driver_rows = await _opening_attendance_rows(db, division_id)

        cursor2 = await db.execute(
            """
            SELECT autoreserve_threshold, autosack_threshold
            FROM attendance_config
            """,
        )
        cfg_row = await cursor2.fetchone()

    # Sort: descending total_points_after, then alphabetical by display name.
    def _sort_key(r):
        member = guild.get_member(int(r["discord_user_id"]))
        display = member.display_name if member else str(r["discord_user_id"])
        return (-(r["total_points_after"] or 0), display.lower())

    # Nobody has attended anything yet, so the points above separate nobody. The opening
    # sheet is ordered as the opening standings grid is — team, then driver — so that a
    # league's two opening sheets agree about the order of the same drivers.
    def _opening_sort_key(r):
        member = guild.get_member(int(r["discord_user_id"]))
        display = member.display_name if member else str(r["discord_user_id"])
        return ((r["team_name"] or "").casefold(), display.casefold())

    sorted_drivers = sorted(
        driver_rows,
        key=(
            _opening_sort_key
            if occasion is ClassificationOccasion.SEASON_OPENING
            else _sort_key
        ),
    )

    # The opening and final sheets say which they are; an ordinary one names no round here,
    # the sheet itself carrying that. The phrase heads the *textual* body either way — a bare
    # table of names and numbers says nothing about what it is a table of.
    heading = (
        "**Attendance Standings**"
        if occasion.names_a_round
        else f"**Attendance — {occasion.label()}**"
    )
    lines: list[str] = [heading, ""]
    for r in sorted_drivers:
        pts = r["total_points_after"] or 0
        mention = f"<@{r['discord_user_id']}>"
        if r["test_display_name"]:
            mention += f" ({r['test_display_name']})"
        suffix = " *(reached point limit)*" if sanctioned_profile_ids and r["driver_profile_id"] in sanctioned_profile_ids else ""
        lines.append(f"{mention} — {pts} attendance point{'s' if pts != 1 else ''}{suffix}")

    # Footer (FR-019).
    footer_lines: list[str] = []
    if cfg_row:
        ar = cfg_row["autoreserve_threshold"]
        as_ = cfg_row["autosack_threshold"]
        if ar:
            footer_lines.append(f"Drivers who reach {ar} points will be moved to reserve.")
        if as_:
            footer_lines.append(f"Drivers who reach {as_} points will be removed from all driving roles in all divisions.")

    if footer_lines:
        lines.append("")
        lines.extend(footer_lines)

    content = "\n".join(lines)

    # ── The graphic, where the league draws one ───────────────────────────
    # Rendered *before* anything is sent or destroyed, and entirely optional: a failure here
    # leaves ``attachment`` None and the textual sheet below is posted exactly as it always
    # was. The graphic never gates this posting, and this posting never gates a sanction
    # (XIV.7 — image output adds no precondition).
    faults: list[str] = []
    attachment = (
        None
        if as_text
        else await _sheet_attachment(
            bot,
            guild,
            db_path,
            round_id=round_id,
            division_id=division_id,
            sorted_drivers=sorted_drivers,
            cfg_row=cfg_row,
            sanctioned_profile_ids=sanctioned_profile_ids,
            occasion=occasion,
            faults=faults,
        )
    )

    # ── Produce ───────────────────────────────────────────────────────────
    # The one send site, for the graphic and the text alike. Nothing has been destroyed at this
    # point, so a failure here leaves the previously posted sheet standing (XIV.8).
    try:
        if attachment is not None:
            # **The graphic replaces the table, not merely decorates it** (FR-043, XIV.16).
            # The message keeps the heading and gives the sheet itself to the picture. Posting
            # the full textual body beside the attachment would tell a league everything twice
            # and ping every driver from a message whose point is the image — and the graphic
            # carries names precisely so that it carries no mention.
            # A season-boundary graphic goes out bare: the phrase naming the occasion is
            # drawn on the sheet, so a heading above it would only say it twice.
            new_message_id = await post_standing(
                channel, heading if occasion.names_a_round else None,
                mentions=discord.AllowedMentions.none(), file=attachment,
            )
        else:
            # The text names its drivers by mention, and notifies them, as it always has.
            new_message_id = await post_standing(
                channel, content, mentions=discord.AllowedMentions(users=True)
            )
    except discord.HTTPException as exc:
        log.warning("post_attendance_sheet: failed to post sheet for division %s: %s", division_id, exc)
        raise StepFailedOnDiscord(
            f"the attendance sheet could not be posted: {exc}"
        ) from exc
    finally:
        # Posted or not, the picture has done all it will ever do: a try again posts the text
        # and never the image, so nothing reads this file again.
        from leaguebot.image.services.image_render_service import discard_attachment

        discard_attachment(attachment)

    # The final sheet is terminal: it stands *beside* the last round's rather than replacing
    # it, so it neither claims the slot nor deletes what is in it. Claiming the slot would
    # only hand some later posting the means to delete the season's last word.
    return PostedSheet(
        message_id=new_message_id, channel_id=channel_id, previous=prior_msg_id,
        takes_slot=occasion.takes_the_live_slot,
        faults=await _fault_lines(db_path, division_id, faults),
    )


async def _fault_lines(db_path: str, division_id: int, faults: list[str]) -> tuple[str, ...]:
    """The faults the graphic fell back from, each as a line naming the division, for the job that
    posted the sheet to write beside its result."""
    if not faults:
        return ()
    name = await _division_name(db_path, division_id)
    return tuple(f"**{name}** — attendance sheet: {fault}" for fault in faults)


@dataclass(frozen=True)
class PostedSheet:
    """What `post_attendance_sheet` posted: *message_id* in *channel_id*, replacing *previous*
    (the id recorded as the division's live sheet when it ran, or None), and whether the
    occasion *takes_slot*, that is, replaces the live sheet and is recorded as it. *faults* are the
    lines for a fault the graphic fell back from, the sheet going out as text or drawn without a
    part (none where it was drawn whole or not asked for)."""

    message_id: int
    channel_id: int
    previous: str | None
    takes_slot: bool
    faults: tuple[str, ...] = ()


def sheet_result(posted: PostedSheet | None) -> dict[str, Any]:
    """A sheet job's result: the sheet posted (`new`, `old`, `channel_id`, `takes_slot`, and
    `faults` where the graphic fell back from any), or an empty result where nothing was due (a
    cancelled round, a division with no opening round)."""
    if posted is None:
        return {}
    result: dict[str, Any] = {
        "new": posted.message_id, "old": posted.previous, "channel_id": posted.channel_id,
        "takes_slot": posted.takes_slot,
    }
    if posted.faults:
        result["faults"] = list(posted.faults)
    return result


def sheet_lines(result: Mapping[str, Any]) -> tuple[str, ...]:
    """The lines a sheet job writes with its result: the faults the graphic fell back from."""
    return tuple(str(line) for line in result.get("faults") or ())


async def record_sheet_on(db: aiosqlite.Connection, division_id: int, posted: Mapping[str, Any]) -> None:
    """Save the sheet posted as the division's live one, on *db*, committing nothing.

    Only a sheet that takes the live slot is saved: the final sheet of a season stands beside the
    last round's and leaves the live sheet as it was.
    """
    if not posted.get("takes_slot") or posted.get("new") is None:
        return
    await db.execute(
        "UPDATE attendance_division_config SET attendance_message_id = ? WHERE division_id = ?",
        (str(posted["new"]), division_id),
    )


async def _sheet_rows(db, round_id: int, division_id: int) -> list:
    """The drivers a division's attendance sheet after *round_id* lists, with their totals.

    Issue #220: every driver currently seated full-time in the division, and every driver who
    has held a seat in it during the season — full-time, or as a Reserve placed into a seat for
    a round — whether or not they still hold it. A driver sanctioned upon this posting, sacked
    at an earlier round, or moved to Reserve therefore stays on the sheet, which is what lets
    the sheet say they reached the limit.

    Having held a seat for a round is read from the attendance record itself: a round scores
    the full-time drivers and the Reserves placed into a seat, and nobody else, so a total
    recorded for the driver in this division is the evidence. Each driver's total is the one
    recorded at their latest scored round up to this one, and nought where none is.
    """
    current = await round_facts_on(db, round_id)
    number_of = {
        rnd.id: rnd.round_number for rnd in await division_rounds_in_order_on(db, division_id)
    }
    cursor = await db.execute(
        """
        SELECT round_id, driver_profile_id, total_points_after FROM driver_round_attendance
        WHERE division_id = ? AND total_points_after IS NOT NULL
        """,
        (division_id,),
    )
    # Each driver's total at their latest scored round up to this one.
    latest: dict[int, tuple[int, int]] = {}
    for row in await cursor.fetchall():
        number = number_of.get(int(row["round_id"]))
        if current is None or number is None or number > current.round_number:
            continue
        profile_id = int(row["driver_profile_id"])
        if profile_id not in latest or number > latest[profile_id][0]:
            latest[profile_id] = (number, row["total_points_after"])
    seated = {
        driver.profile_id
        for driver in await TeamService.seat_holders_on(db, division_id)
        if not driver.is_reserve
    }
    identities = await driver_identities_on(db, latest.keys() | seated)
    return [
        {
            "driver_profile_id": profile_id,
            "discord_user_id": account,
            "test_display_name": name,
            "total_points_after": latest[profile_id][1] if profile_id in latest else 0,
        }
        for profile_id, (account, name) in sorted(identities.items())
    ]


async def _opening_attendance_rows(db, division_id: int) -> list[dict]:
    """The division's seated drivers, all on zero, shaped like the ordinary sheet's rows.

    Posted once, when the season is approved. Selects the same drivers the sheet after a
    round does — seated, non-reserve — and carries the team name besides, which is what the
    opening order is taken on.
    """
    seated = [
        driver
        for driver in await TeamService.seat_holders_on(db, division_id)
        if not driver.is_reserve
    ]
    identities = await driver_identities_on(db, [driver.profile_id for driver in seated])
    return [
        {
            "driver_profile_id": driver.profile_id,
            "total_points_after": 0,
            "discord_user_id": identities[driver.profile_id][0],
            "test_display_name": identities[driver.profile_id][1],
            "team_name": driver.team_name,
        }
        for driver in seated
        if driver.profile_id in identities
    ]


class _RoundGridUnreadable(Exception):
    """The grid of rounds the graphic draws could not be read from the database."""


def _stop_on_unreadable_grid(fault: Exception) -> None:
    """Raise *fault* where it is the grid of rounds failing to be read: a sheet drawn with an empty
    grid would replace the standing one with a wrong one, so its job stops and is retried. Any
    other fault of the drawing is left to the caller, the sheet posting as text alone."""
    if isinstance(fault, _RoundGridUnreadable):
        raise fault


async def _sheet_attachment(
    bot: LeagueBot,
    guild: discord.Guild,
    db_path: str,
    *,
    round_id: int,
    division_id: int,
    sorted_drivers,
    cfg_row,
    sanctioned_profile_ids: set[int] | None,
    occasion: ClassificationOccasion = ClassificationOccasion.AFTER_ROUND,
    faults: list[str] | None = None,
):
    """The sheet graphic to attach, or None to post the textual sheet alone.

    Every path out of here that is not a rendered PNG returns None, and the caller posts the
    text it would have posted anyway. That is the whole of the fallback: the module being off,
    the aspect being off, the template being invalid and the render having failed are not
    distinguished, because the answer to all four is the same.

    Nothing raised here escapes but the grid of rounds failing to be read (`_stop_on_unreadable_grid`): a
    graphic must never prevent, delay or condition the posting it rides on, still less the
    sanctions enforced beside it (XIV.7), but a sheet drawn with an empty grid would replace the
    standing one with a wrong one.

    **A fault the drawing falls back from is returned as well as logged** (architecture.md,
    "Errors and failures", place 6): each is appended to *faults*, if given, as a clause the
    posting turns into a line of its job, so that the league is told its graphic could not be
    drawn, or drawn without a part, and the full error stays in the host's log.
    """
    if bot is None or guild is None:
        return None

    try:
        from leaguebot.image.services.image_attendance_post import (
            attendance_enabled,
            render_sheet,
            report,
            report_notices,
        )
        from leaguebot.image.services.image_attendance_service import DriverRecord, resolve_drawing

        if not await attendance_enabled(bot):
            return None

        from leaguebot.image.services.image_results_post import (
            _driver_names,
            _nationalities,
            _nationality_collected,
        )

        records: list[DriverRecord] = []
        user_ids = [int(row["discord_user_id"]) for row in sorted_drivers]
        profile_ids = [int(row["driver_profile_id"]) for row in sorted_drivers]

        # The grid: every round the division holds, run or not (FR-016), and what each
        # conferred on each driver. The points are **read** from the record the module
        # persisted — already net of pardons — and never recomputed here (XIV.7).
        headings, cells = await _round_grid(db_path, division_id, profile_ids, faults=faults)
        profile_of = {
            int(row["discord_user_id"]): int(row["driver_profile_id"])
            for row in sorted_drivers
        }

        # The name each driver is drawn under, and their flag — both through the conventions
        # every graphic shares, called rather than restated (wip-spec § "The name of a person").
        display_names = await _driver_names(bot, guild, user_ids, division_id=division_id)
        nationalities = await _nationalities(bot, user_ids, division_id=division_id)
        collected = await _nationality_collected(db_path)

        # The team of a row is the team of the division seating the driver **at the moment of
        # generation** — the reserve team for a reserve — and never the team whose car they
        # drove in any one round (FR-020).
        team_names = await _seat_team_names(db_path, division_id, user_ids, faults=faults)
        # The shorthands are read from the same teams, so their fault is the names' and is told once.
        team_keys = await _seat_team_keys(db_path, division_id, user_ids)

        for row in sorted_drivers:
            key = int(row["discord_user_id"])
            display_names.setdefault(key, str(key))
            records.append(
                DriverRecord(
                    key=key,
                    total=row["total_points_after"] or 0,
                    round_points=cells.get(profile_of.get(key, -1), {}),
                    sanctioned=bool(
                        sanctioned_profile_ids
                        and row["driver_profile_id"] in sanctioned_profile_ids
                    ),
                )
            )

        division_name = await _division_name(db_path, division_id)
        round_number = await _round_number(db_path, round_id)

        drawing = resolve_drawing(
            division_name=division_name,
            occasion=occasion,
            round_number=round_number,
            records=records,
            display_names=display_names,
            team_names=team_names,
            team_keys=team_keys,
            nationalities=nationalities,
            rounds=headings,
            autoreserve_threshold=(cfg_row["autoreserve_threshold"] if cfg_row else None),
            autosack_threshold=(cfg_row["autosack_threshold"] if cfg_row else None),
            # Where the league switched nationality collection off at its source, a sheet with
            # no flags at all is exactly what was configured and raises nothing — rather than
            # one notice per driver, on every render, reporting a setting back to whoever
            # chose it (XIV.4's configured absence).
            nationality_collected=collected,
        )

        render = await render_sheet(bot, drawing)
        label = (
            f"{division_name} — attendance after round {round_number}"
            if occasion.names_a_round
            else f"{division_name} — attendance, {occasion.label()}"
        )
        if render.notices:
            await report_notices(bot, label, render.notices)
        if render.problem:
            await report(bot, label, render.problem)
        if render.png is None:  # `render.draws`, in a form the type check can follow
            return None

        return discord.File(str(render.png), filename=Path(render.png).name)
    except Exception as exc:  # noqa: BLE001 — the sheet must post whatever happens here
        _stop_on_unreadable_grid(exc)
        log.error(
            "post_attendance_sheet: the graphic could not be drawn for division %s: %s",
            division_id, exc, exc_info=True,
        )
        _note(faults, f"the graphic could not be drawn, so the sheet is posted as text: {exc}")
        return None


def _note(faults: list[str] | None, clause: str) -> None:
    """Add *clause* to *faults*, where the caller keeps them."""
    if faults is not None:
        faults.append(clause)


async def _round_grid(
    db_path: str, division_id: int, profile_ids: list[int], faults: list[str] | None = None
):
    """The sheet's round columns, and the points each round conferred on each driver.

    Returns ``(headings, cells)`` where *headings* is one :class:`RoundHeading` per round the
    division holds — **every** round, run or not (FR-016), unlike the standings grid which
    draws only those already run — and *cells* maps a driver profile id to
    ``{round ordinal: points}``.

    The ordinal is the round's place in the calendar, counted from 1, because that is what the
    template's ``round_<z>`` addresses. The *number* drawn on the heading is the round's own
    human-readable number, which need not agree with its position if rounds were removed.

    A round of the mystery format records no track and is drawn from the datum "Mystery", as
    every graphic draws one (wip-spec § "A round of the mystery format").

    **A grid that cannot be read from the database is raised** (`_RoundGridUnreadable`), never drawn
    empty. A missing cell and a stored zero are the same picture and the same meaning: the round
    counted nothing against that driver. Nothing here distinguishes the six ways that happens.

    A track registry that cannot be read is no fault of the grid: the headings are drawn without
    their flags, the full error goes to the host's log and a clause saying so is added to *faults*.
    """
    from leaguebot.core.services.rounds.calendar_post_service import tracks_by_name
    from leaguebot.image.services.image_attendance_service import RoundHeading
    from leaguebot.image.services.image_calendar_service import MYSTERY_DATUM

    headings: list = []
    cells: dict[int, dict[int, int | None]] = {}
    try:
        async with get_connection(db_path) as db:
            rounds = await division_rounds_in_order_on(db, division_id)

            # The heading's flag resolves by **country**, not by circuit (044 moved the
            # datum when the column heading became a flag rather than a track map), so the
            # registry is joined here. Without it every heading on the posted sheet drew no
            # flag at all, while `/images test attendance` drew them from the same rounds.
            #
            # Read on its own terms: a registry that cannot be read costs the sheet its
            # heading flags, and must not cost it the grid. The rounds, the drivers and
            # the points they scored are what the sheet is for.
            try:
                tracks = await tracks_by_name(db_path)
            except Exception as exc:  # noqa: BLE001
                log.error(
                    "post_attendance_sheet: the track registry could not be read, so the "
                    "round headings are drawn without their flags: %s",
                    exc, exc_info=True,
                )
                _note(
                    faults,
                    f"the track registry could not be read, so the round headings are drawn "
                    f"without their flags: {exc}",
                )
                tracks = {}

            ordinal_of: dict[int, int] = {}
            for ordinal, rnd in enumerate(rounds, start=1):
                ordinal_of[rnd.id] = ordinal
                mystery = str(rnd.format.value or "").upper().endswith("MYSTERY")
                track_name = None if mystery else (rnd.track_name or None)
                record = tracks.get(track_name) if track_name else None
                headings.append(
                    RoundHeading(
                        ordinal=ordinal,
                        number=str(rnd.round_number),
                        track=MYSTERY_DATUM if mystery else track_name,
                        country=(
                            MYSTERY_DATUM
                            if mystery
                            else (getattr(record, "country", None) if record else None)
                        ),
                    )
                )

            if ordinal_of and profile_ids:
                round_marks = ",".join("?" * len(ordinal_of))
                driver_marks = ",".join("?" * len(profile_ids))
                points_rows = await (
                    await db.execute(
                        f"SELECT round_id, driver_profile_id, points_awarded "
                        f"FROM driver_round_attendance "
                        f"WHERE division_id = ? "
                        f"  AND round_id IN ({round_marks}) "
                        f"  AND driver_profile_id IN ({driver_marks})",
                        [division_id, *ordinal_of.keys(), *profile_ids],
                    )
                ).fetchall()
                for row in points_rows:
                    points_ordinal = ordinal_of.get(int(row["round_id"]))
                    if points_ordinal is None:
                        continue
                    cells.setdefault(int(row["driver_profile_id"]), {})[points_ordinal] = (
                        row["points_awarded"]
                    )
    except aiosqlite.Error as exc:
        raise _RoundGridUnreadable(str(exc)) from exc

    return headings, cells


async def _seat_team_names(
    db_path: str, division_id: int, user_ids: list[int], faults: list[str] | None = None
) -> dict[int, str]:
    """The team of the division seating each driver **now**, keyed by Discord user id.

    A reserve driver's team is the reserve team, which is what the sheet draws for them —
    not the team whose car they drove in some round (FR-020). This is the name drawn; the
    badge is looked up by the team's shorthand, from ``_seat_team_keys`` (#381).
    """
    return await _seat_team_field(db_path, division_id, user_ids, "full_name", faults)


async def _seat_team_keys(
    db_path: str, division_id: int, user_ids: list[int], faults: list[str] | None = None
) -> dict[int, str]:
    """The shorthand of the team seating each driver now, which its badge is found by (#381)."""
    return await _seat_team_field(db_path, division_id, user_ids, "name", faults)


async def _seat_team_field(
    db_path: str, division_id: int, user_ids: list[int], field: str,
    faults: list[str] | None = None,
) -> dict[int, str]:
    """One field of the team seating each driver now — ``name`` or ``full_name``.

    Teams that cannot be read are drawn without names: the full error goes to the host's log and
    a clause saying so is added to *faults*."""
    if field not in ("name", "full_name"):
        raise ValueError(f"not a team name field: {field}")
    if not user_ids:
        return {}
    try:
        async with get_connection(db_path) as db:
            placed = await TeamService.placed_drivers_on(db, division_id)
    except Exception as exc:  # noqa: BLE001 — a nameless team is drawn empty, never fatal
        log.warning(
            "post_attendance_sheet: the teams of division %s could not be read, so they are "
            "drawn without names", division_id, exc_info=True,
        )
        _note(faults, f"the teams could not be read, so they are drawn without names: {exc}")
        return {}
    wanted = set(user_ids)
    return {
        driver.discord_user_id: (driver.team_name if field == "full_name" else driver.team_key)
        for driver in placed
        if driver.discord_user_id in wanted and (driver.team_name if field == "full_name" else driver.team_key)
    }


async def _division_name(db_path: str, division_id: int) -> str:
    async with get_connection(db_path) as db:
        division = await division_on(db, division_id)
    return division.name if division and division.name else f"Division {division_id}"


async def _round_number(db_path: str, round_id: int) -> str:
    async with get_connection(db_path) as db:
        facts = await round_facts_on(db, round_id)
    return str(facts.round_number) if facts and facts.round_number else str(round_id)
