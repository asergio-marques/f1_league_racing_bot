"""Post a round's two championships as graphics, or stand aside (040).

**One decision in one place**, as 039's results posting is. Every occasion that reposts the
textual standings reaches ``results_post_service.post_standings`` — all seven of them, over
five call sites — so the branch lives there and nowhere else.

Two things make this module unlike the five before it.

**It posts two messages from one call.** The textual standings are a single message carrying
both championships; the graphics are two, the driver standings first and the constructors
second, each with its own id column on the top-ranked driver's snapshot row. Every other
post module produces one attachment per call and lets its caller loop.

**Failure is per championship** (Constitution XIV.4). A drivers graphic that will not draw
falls back to the drivers section **alone** — never to the whole textual message, which
would print the constructors table a second time beside the graphic that just drew it. That
is why ``results_post_service`` keeps section formatting and message composition apart, and
why this module reports which championships fell back rather than simply whether it ran.

The shape the caller uses::

    outcome = await try_post(bot, guild, channel, ...)
    if not outcome.applicable:
        ...existing textual body, unchanged...
    elif outcome.rejects:
        return                          # commanded, and refused: nothing is posted
    else:
        ...post a section for each of outcome.fallback_championships...

**The textual path is not reformed by this module.** Where the image flow does not run — no
bot in scope, the module disabled, the `standings` toggle off, neither template valid — the
caller's body runs exactly as it did before 040 (Constitution XIV.7).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import discord

from db.database import get_connection
from models.image_module import PostingOrigin
from services.image_standings_service import (
    CONSTRUCTORS_TEMPLATE_KEY,
    DRIVERS_TEMPLATE_KEY,
)

log = logging.getLogger(__name__)

#: The image flow does not apply — the caller falls through to its textual body.
NOT_APPLICABLE = "NOT_APPLICABLE"
#: The graphic was produced and posted; the caller posts no section for it.
POSTED = "POSTED"
#: The graphic could not be produced, and the posting was uncommanded — the caller posts
#: this championship's section as text, and that section alone.
FELL_BACK = "FELL_BACK"
#: The graphic could not be produced and the posting was **commanded**, so nothing is
#: posted at all and the caller is told what is at fault.
REJECTED = "REJECTED"

#: Championship -> the template that draws it, in the order the two are posted.
TEMPLATE_KEYS: dict[str, str] = {
    "drivers": DRIVERS_TEMPLATE_KEY,
    "constructors": CONSTRUCTORS_TEMPLATE_KEY,
}

#: Championship -> the snapshot column naming the message that carries it. Mirrors
#: ``results_post_service._STANDINGS_ID_COLUMNS``, which is the writer.
_ATTACHMENT_NAMES = {
    "drivers": "standings_drivers.png",
    "constructors": "standings_constructors.png",
}


@dataclass
class ChampionshipOutcome:
    """What became of one championship's graphic."""

    action: str = NOT_APPLICABLE
    message: str | None = None
    message_id: int | None = None
    notices: list = field(default_factory=list)
    png_path: Path | None = None

    @property
    def posted(self) -> bool:
        return self.action == POSTED


@dataclass
class StandingsPostOutcome:
    """What became of both, and what the caller must do about it."""

    drivers: ChampionshipOutcome = field(default_factory=ChampionshipOutcome)
    constructors: ChampionshipOutcome = field(default_factory=ChampionshipOutcome)

    @property
    def applicable(self) -> bool:
        """True where the image flow ran at all.

        False means every reason the flow stands aside wholesale — no bot, the module off,
        the aspect off, neither template valid — and the caller's untouched textual body is
        what should run, message for message as before 040.
        """
        return (
            self.drivers.action != NOT_APPLICABLE
            or self.constructors.action != NOT_APPLICABLE
        )

    @property
    def rejects(self) -> bool:
        """True where a **commanded** posting failed: nothing is posted, anywhere."""
        return REJECTED in (self.drivers.action, self.constructors.action)

    @property
    def message(self) -> str | None:
        """The first rejection's reason, for the command that asked."""
        for outcome in (self.drivers, self.constructors):
            if outcome.action == REJECTED:
                return outcome.message
        return None

    @property
    def fallback_championships(self) -> list[str]:
        """The championships whose section the caller must post as text, in posting order.

        Empty where both graphics posted. Both, where the flow ran and neither drew — in
        which case the caller composes exactly the message the textual flow always posted,
        so a league reads each championship once either way.
        """
        return [
            name
            for name, outcome in (
                ("drivers", self.drivers),
                ("constructors", self.constructors),
            )
            if outcome.action == FELL_BACK
        ]

    @property
    def notices(self) -> list:
        return [*self.drivers.notices, *self.constructors.notices]


# ── Enablement ────────────────────────────────────────────────────────────


async def standings_enabled(bot, server_id: int, template_key: str) -> bool:
    """True where the module is on, the `standings` aspect is on, and *template_key* is valid.

    Read per template rather than per aspect, so a sound drivers template still draws while a
    faulty constructors template falls back to text (XIV.4 — the unit of failure is one
    graphic, and the two championships are two graphics).
    """
    try:
        if not await bot.module_service.is_images_enabled(server_id):
            return False
        toggles = await bot.image_config_service.get_toggles(server_id)
        if not toggles.get("standings"):
            return False
        reports = await bot.image_validity_service.template_reports(server_id)
        report = reports.get(template_key)
        return report is not None and report.valid
    except Exception as exc:  # noqa: BLE001 — never break a posting on this reader
        log.error("standings: enablement check failed for server %s: %s", server_id, exc)
        return False


# ── The data behind the two drawings ──────────────────────────────────────


async def _calendar(bot, division_id: int):
    """The division's whole calendar as ``RoundHeading``s, and the ordinal of each round id.

    Every round the division holds is headed, run or not: the grid is the season's shape,
    and a column that disappears when a round is cancelled would redraw the graphic's width
    from one posting to the next. A round's *cells* are what emptying handles.
    """
    from services.calendar_post_service import tracks_by_name
    from services.image_standings_service import RoundHeading

    tracks = await tracks_by_name(bot.db_path)

    async with get_connection(bot.db_path) as db:
        rows = await (
            await db.execute(
                "SELECT id, round_number, format, track_name FROM rounds "
                "WHERE division_id = ? ORDER BY round_number",
                (division_id,),
            )
        ).fetchall()

    headings = []
    ordinal_of_round: dict[int, int] = {}
    for ordinal, row in enumerate(rows, start=1):
        # A mystery round names no circuit until it is revealed, and its flag is the
        # mystery asset rather than a country's (044).
        track_name = None if row["format"] == "MYSTERY" else row["track_name"]
        record = tracks.get(track_name) if track_name else None
        headings.append(
            RoundHeading(
                ordinal=ordinal,
                number=str(row["round_number"]),
                track=track_name,
                country=getattr(record, "country", None) if record else None,
            )
        )
        ordinal_of_round[row["id"]] = ordinal

    return headings, ordinal_of_round


async def _round_session_results(bot, ordinal_of_round: dict[int, int]):
    """Round ordinal -> session type value -> the rows of that session.

    A round with no active session result is **absent** from the mapping, which is how the
    grid tells "not yet run, or cancelled" from "run, and this driver took no part".
    """
    from models.session_result import SessionType
    from services.results_post_service import _load_driver_rows

    if not ordinal_of_round:
        return {}

    placeholders = ",".join("?" * len(ordinal_of_round))
    async with get_connection(bot.db_path) as db:
        rows = await (
            await db.execute(
                f"SELECT id, round_id, session_type FROM session_results "
                f"WHERE round_id IN ({placeholders}) AND status = 'ACTIVE'",
                list(ordinal_of_round),
            )
        ).fetchall()

    results: dict[int, dict[str, list]] = {}
    for row in rows:
        ordinal = ordinal_of_round[row["round_id"]]
        try:
            session_type = SessionType(row["session_type"])
        except ValueError:
            log.warning(
                "standings: session result %s has an unknown session type %r",
                row["id"],
                row["session_type"],
            )
            continue
        driver_rows = await _load_driver_rows(bot.db_path, row["id"], session_type)
        results.setdefault(ordinal, {})[session_type.value] = driver_rows

    return results


async def _seats(bot, division_id: int, team_names_by_role: dict[int, str]):
    """The division's seating, in the three shapes the two drawings need.

    Returns ``(assignments, counts, driver_team_names)`` — the first two keyed by team
    **role** id for the constructors graphic's car allocation and seat trim, the third by
    driver user id, because a drivers row names the team its own driver sits in rather than
    the row's own subject.

    The classification keys a constructor by the Discord role its drivers' results record;
    the seats are held by the division's team *instance*, joined to that role by name at
    server scope. A role the division holds no instance of contributes no seats — its cars
    are then allocated to whoever drove, which is what FR-026 asks for.
    """
    assignments: dict[int, dict[int, int]] = {}
    counts: dict[int, int] = {}
    driver_team_names: dict[int, str] = {}

    name_to_role = {name: role_id for role_id, name in team_names_by_role.items()}

    async with get_connection(bot.db_path) as db:
        rows = await (
            await db.execute(
                "SELECT ti.name AS team_name, ti.max_seats AS max_seats, "
                "       ts.seat_number AS seat_number, "
                "       dp.discord_user_id AS discord_user_id "
                "FROM team_instances ti "
                "LEFT JOIN team_seats ts ON ts.team_instance_id = ti.id "
                "LEFT JOIN driver_season_assignments dsa "
                "       ON dsa.team_seat_id = ts.id AND dsa.division_id = ti.division_id "
                "LEFT JOIN driver_profiles dp ON dp.id = dsa.driver_profile_id "
                "WHERE ti.division_id = ?",
                (division_id,),
            )
        ).fetchall()

    for row in rows:
        role_id = name_to_role.get(row["team_name"])
        if role_id is not None:
            counts[role_id] = int(row["max_seats"] or 0)

        if row["discord_user_id"] is None or row["seat_number"] is None:
            continue
        try:
            driver_key = int(row["discord_user_id"])
            seat_number = int(row["seat_number"])
        except (TypeError, ValueError):
            continue

        driver_team_names[driver_key] = row["team_name"]
        if role_id is not None:
            assignments.setdefault(role_id, {})[driver_key] = seat_number

    return assignments, counts, driver_team_names


async def build_drawings(
    bot,
    guild,
    *,
    db_path: str,
    server_id: int,
    division_id: int,
    round_id: int,
    round_number,
    driver_snapshots,
    team_snapshots,
    reserve_user_ids: set[int],
    show_reserves: bool,
    result_status: str | None,
    division_name: str,
    division_tier=None,
    season_number=None,
    race_name: str | None = None,
):
    """Resolve both championships into drawings, or raise ``StandingsDataError``.

    Both are built from one pass over the shared data — the calendar, the session results
    behind the grid, the names — because the two graphics draw the same season and differ
    only in what a row stands for.
    """
    from services import standings_service
    from services.image_results_post import (
        _driver_names,
        _nationality_collected,
        _nationalities,
        _team_names,
    )
    from services.image_standings_service import resolve_drawing

    driver_keys = [s.driver_user_id for s in driver_snapshots]
    team_keys = [s.team_role_id for s in team_snapshots]

    names = await _driver_names(bot, guild, driver_keys)
    nationalities = await _nationalities(bot, driver_keys)
    collected = await _nationality_collected(db_path, server_id)

    # A constructors row *is* a team, so that graphic's names are keyed by role. A drivers
    # row names the team its own driver sits in, so that graphic's are keyed by driver.
    team_names_by_role = await _team_names(bot, guild, server_id, division_id, team_keys)

    headings, ordinal_of_round = await _calendar(bot, division_id)
    session_results = await _round_session_results(bot, ordinal_of_round)
    seat_assignments, seat_counts, driver_team_names = await _seats(
        bot, division_id, team_names_by_role
    )

    driver_current = [
        (s.driver_user_id, s.standing_position, s.total_points) for s in driver_snapshots
    ]
    team_current = [
        (s.team_role_id, s.standing_position, s.total_points) for s in team_snapshots
    ]

    driver_previous = await standings_service.previous_standing_positions(
        db_path, division_id, round_id
    )
    team_previous = await standings_service.previous_standing_positions(
        db_path, division_id, round_id, teams=True
    )

    shared = dict(
        division_name=division_name,
        round_number=round_number,
        result_status=result_status,
        division_tier=division_tier,
        season_number=season_number,
        race_name=race_name,
        nationality_collected=collected,
        rounds=headings,
        round_session_results=session_results,
    )

    drivers_drawing = resolve_drawing(
        template_key=DRIVERS_TEMPLATE_KEY,
        snapshots=driver_snapshots,
        display_names=names,
        team_names=driver_team_names,
        movements=standings_service.derive_movement(driver_current, driver_previous),
        gaps=standings_service.derive_gaps(driver_current),
        nationalities=nationalities,
        reserve_user_ids=reserve_user_ids,
        show_reserves=show_reserves,
        **shared,
    )
    constructors_drawing = resolve_drawing(
        template_key=CONSTRUCTORS_TEMPLATE_KEY,
        snapshots=team_snapshots,
        # Both maps name the same thing here, the row's own team; the drivers inside its
        # cars are named separately, keyed by user id.
        display_names=team_names_by_role,
        team_names=team_names_by_role,
        movements=standings_service.derive_movement(team_current, team_previous),
        gaps=standings_service.derive_gaps(team_current),
        team_seat_assignments=seat_assignments,
        team_seat_counts=seat_counts,
        driver_display_names=names,
        **shared,
    )

    return drivers_drawing, constructors_drawing


# ── Render ────────────────────────────────────────────────────────────────


async def render_png(bot, server_id: int, drawing, origin: PostingOrigin):
    """Render one championship. Returns the render service's PostingDecision."""
    from services.image_render_service import (
        resolve_configured_directories,
        spec_builder_with_faults,
    )
    from services.image_standings_service import build_fill_spec

    config = await bot.image_config_service.get_config(server_id)
    directories, directory_faults = resolve_configured_directories(
        config,
        (
            ("team", "team_image_directory"),
            ("flag", "flag_directory"),
            ("track", "track_image_directory"),
            # The only aspect that draws position-change arrows.
            ("marker", "marker_directory"),
        ),
        image_type=drawing.template_key,
    )

    return await bot.image_render_service.render_for_posting(
        server_id,
        drawing.template_key,
        spec_builder_with_faults(
            build_fill_spec, drawing, directories, directory_faults
        ),
        posting_origin=origin,
        bot=bot,
    )


# ── Post ──────────────────────────────────────────────────────────────────


async def _post_one(
    bot,
    channel,
    *,
    server_id: int,
    db_path: str,
    division_id: int,
    round_id: int,
    championship: str,
    drawing,
    heading: str,
    label: str,
    subject: str,
    origin: PostingOrigin,
) -> ChampionshipOutcome:
    """Draw, post and replace one championship's message.

    **Produce before destroying** (FR-048): the PNG is rendered and the new message sent
    before the previous one is deleted, so a render that fails leaves the channel holding
    the standings it had, and the caller's text fallback replaces nothing prematurely.
    """
    from services.results_post_service import (
        _get_standings_message_id,
        _set_standings_message_id,
    )

    what = f"{subject} — {championship} standings"

    try:
        decision = await render_png(bot, server_id, drawing, origin)
    except Exception as exc:  # noqa: BLE001 — a resolution fault, reported like any other
        log.error("standings: %s render failed: %s", championship, exc)
        await report(bot, server_id, what, str(exc))
        if origin is PostingOrigin.COMMANDED:
            return ChampionshipOutcome(action=REJECTED, message=f"❌ {exc}")
        return ChampionshipOutcome(action=FELL_BACK)

    if decision.rejects:
        return ChampionshipOutcome(
            action=REJECTED,
            message=decision.caller_message(what),
            notices=decision.notices,
        )

    if not decision.posts_image:
        # Uncommanded, and it would not draw: this championship's section is posted as
        # text by the caller, and the other championship is untouched by it.
        if decision.problem is not None:
            await report(bot, server_id, what, decision.problem.detail)
        return ChampionshipOutcome(action=FELL_BACK, notices=decision.notices)

    png = decision.png_paths[0]
    try:
        message = await channel.send(
            f"{heading}\n{label}",
            file=discord.File(str(png), filename=_ATTACHMENT_NAMES[championship]),
        )
    except discord.HTTPException as exc:
        # A Discord failure rather than a generation one. The graphic was produced; it is
        # the delivery that was not, so it is the **textual** standings the caller posts
        # and, if need be, enqueues for retry (FR-056).
        log.error("standings: could not post the %s graphic: %s", championship, exc)
        return ChampionshipOutcome(action=FELL_BACK, notices=decision.notices)

    previous_id = await _get_standings_message_id(
        db_path, division_id, round_id, championship
    )
    if previous_id is not None:
        try:
            previous = await channel.fetch_message(previous_id)
            await previous.delete()
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass

    await _set_standings_message_id(
        db_path, division_id, round_id, message.id, championship
    )

    if decision.notices:
        await report_notices(bot, server_id, what, decision.notices)

    return ChampionshipOutcome(
        action=POSTED,
        message_id=message.id,
        notices=decision.notices,
        png_path=png,
    )


async def try_post(
    bot,
    guild,
    channel,
    *,
    db_path: str,
    division_id: int,
    round_id: int,
    round_number,
    heading: str,
    label: str,
    driver_snapshots,
    team_snapshots,
    reserve_user_ids: set[int],
    show_reserves: bool,
    result_status: str | None,
    division_name: str,
    division_tier=None,
    season_number=None,
    race_name: str | None = None,
    origin: PostingOrigin = PostingOrigin.SCHEDULED,
) -> StandingsPostOutcome:
    """Post both championships as graphics, or say which of them the caller must write out.

    The two are posted in order — the driver standings first, the constructor standings
    after — and each answers for itself. Neither the failure of one nor a template invalid
    for one stops the other.
    """
    if bot is None or guild is None or channel is None:
        return StandingsPostOutcome()

    server_id = guild.id
    wanted = {
        name: await standings_enabled(bot, server_id, key)
        for name, key in TEMPLATE_KEYS.items()
    }
    if not any(wanted.values()):
        return StandingsPostOutcome()

    subject = f"{division_name} round {round_number}"

    try:
        drivers_drawing, constructors_drawing = await build_drawings(
            bot,
            guild,
            db_path=db_path,
            server_id=server_id,
            division_id=division_id,
            round_id=round_id,
            round_number=round_number,
            driver_snapshots=driver_snapshots,
            team_snapshots=team_snapshots,
            reserve_user_ids=reserve_user_ids,
            show_reserves=show_reserves,
            result_status=result_status,
            division_name=division_name,
            division_tier=division_tier,
            season_number=season_number,
            race_name=race_name,
        )
    except Exception as exc:  # noqa: BLE001 — the data behind both, so both answer for it
        log.error("standings: the drawings could not be resolved: %s", exc)
        await report(bot, server_id, subject, str(exc))
        if origin is PostingOrigin.COMMANDED:
            return StandingsPostOutcome(
                drivers=ChampionshipOutcome(action=REJECTED, message=f"❌ {exc}"),
                constructors=ChampionshipOutcome(action=REJECTED, message=f"❌ {exc}"),
            )
        return StandingsPostOutcome(
            drivers=ChampionshipOutcome(action=FELL_BACK),
            constructors=ChampionshipOutcome(action=FELL_BACK),
        )

    outcome = StandingsPostOutcome()
    for championship, drawing in (
        ("drivers", drivers_drawing),
        ("constructors", constructors_drawing),
    ):
        if not wanted[championship]:
            # This championship's template is invalid, or nothing draws it. Its section is
            # posted as text beside the other's graphic.
            result = ChampionshipOutcome(action=FELL_BACK)
        else:
            result = await _post_one(
                bot,
                channel,
                server_id=server_id,
                db_path=db_path,
                division_id=division_id,
                round_id=round_id,
                championship=championship,
                drawing=drawing,
                heading=heading,
                label=label,
                subject=subject,
                origin=origin,
            )
        setattr(outcome, championship, result)

        # A commanded posting that fails posts nothing at all, so the second championship
        # is not attempted once the first has been refused.
        if result.action == REJECTED:
            break

    if outcome.rejects:
        for championship in ("drivers", "constructors"):
            if getattr(outcome, championship).action == NOT_APPLICABLE:
                setattr(
                    outcome,
                    championship,
                    ChampionshipOutcome(action=REJECTED, message=outcome.message),
                )

    return outcome


# ── Reporting ─────────────────────────────────────────────────────────────


async def report(bot, server_id: int, what: str, detail: str) -> None:
    """Report a fault to the server's logging channel, never to the standings channel.

    Drivers read the standings channel (FR-053); a template fault is the league manager's
    business and belongs where they look for it.
    """
    try:
        await bot.output_router.post_log(
            server_id, f"⚠️ Standings image — {what}: {detail}"
        )
    except Exception as exc:  # noqa: BLE001
        log.error("standings: could not report to the log channel: %s", exc)


async def report_notices(bot, server_id: int, what: str, notices) -> None:
    """Report every non-fatal degradation, in one grouped block like every other path.

    This used to post one Discord message per notice, which a twenty-driver championship
    turned into twenty. `ImageRenderService.report_notices` writes a single grouped block,
    and routing through it is also what stops this one path drifting from the format every
    other posting path uses.
    """
    if not notices:
        return
    try:
        from services.image_render_service import ImageRenderService

        await ImageRenderService.report_notices(bot, server_id, notices, subject=what)
    except Exception as exc:  # noqa: BLE001
        log.error("standings: could not report notices: %s", exc)
