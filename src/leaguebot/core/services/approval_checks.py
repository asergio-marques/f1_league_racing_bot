"""The checks a season's review and its confirmations make of the league.

The approval is judged twice: at the press, by the review's workflow, and again when it comes up
to run on the change queue, by the change type, which may import no cog. So every check both make
lives here as a public function, and the review commands, the workflows and the change types all
call it; the same question is then answered the same way wherever it is asked. A check is handed
the services it reads, a service that several checks read by the name the bot carries it under, as
a keyword. `not_done_section` is the reply's list of what a confirmation could not do.

A caller reaches a check through this module (`approval_checks.team_name_problems`), never by
importing it by name, so that a test standing one in replaces it for every caller.
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol, TypeVar

import discord

from leaguebot.core.db.database import get_connection
from leaguebot.core.models.round import AttendanceWindows, WeatherWindows
from leaguebot.core.services.approval_window_service import calendar_faults
from leaguebot.core.services.config_service import ConfigService
from leaguebot.core.services.image_drawing import DivisionTeams, ImageDrawing, TeamSeats
from leaguebot.core.services.module_service import ModuleService
from leaguebot.core.services.season_service import SeasonService
from leaguebot.core.services.team_service import TeamService, validate_team_name
from leaguebot.core.utils.asset_slug import normalise
from leaguebot.core.utils.channel_guard import league_role_faults
from leaguebot.results.services import season_points_service

log = logging.getLogger(__name__)


def not_done_section(not_done: list[str]) -> str:
    """What a placements confirmation could not do, as a section of its reply."""
    if not not_done:
        return ""
    return "\n\n⚠️ **Not everything could be done**\n" + "\n".join(
        f"• {line}" for line in not_done
    )


@dataclass(frozen=True)
class DatesRefusal:
    """A calendar holding dates gone by, as a member is told it (*reply*) and as the log's
    refusal line states it (*reason*)."""

    reply: str
    reason: str


def _stamp(moment: datetime) -> str:
    """A Discord dynamic timestamp, full form, a naive moment taken as UTC. Written here and not
    imported from the weather module's message builder, which core may not reach from a service."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return f"<t:{int(moment.timestamp())}:F>"


def date_refusal(
    divisions: list[Any],
    rounds_by_division: dict[int, list[Any]],
    *,
    now: datetime,
    attendance: AttendanceWindows | None,
    weather: WeatherWindows | None,
) -> DatesRefusal | None:
    """The refusal for a season whose calendar holds a round already run or inside a window, or
    None where every division's calendar is clean (Gate 2d).

    **Both confirmations of a season's placements ask this**: the press, and the change queue's
    check as the approval comes up to run, where the clock has moved on. One bullet per division,
    and at most two findings within it. Each division's rounds are judged by `calendar_faults`.
    """
    problems: list[str] = []
    for division in divisions:
        fault = calendar_faults(
            rounds_by_division[division.id], now=now, attendance=attendance, weather=weather
        )
        if fault is None:
            continue
        bits = []
        if fault.latest_past is not None:
            bits.append(
                f"Round {fault.latest_past.round_number} has already run "
                f"({_stamp(fault.latest_past.scheduled_at)}), and so has every "
                f"round before it"
            )
        if fault.latest_window is not None:
            bits.append(
                f"Round {fault.latest_window.round_number} is inside its "
                f"{fault.latest_window.label.lower()}, due "
                f"{_stamp(fault.latest_window.fire_at)} "
                f"({fault.latest_window.lead})"
            )
        problems.append(f"• **{division.name}** — " + "; ".join(bits) + ".")
    if not problems:
        return None
    body = "\n".join(problems)
    return DatesRefusal(
        reply=(
            f"❌ Season cannot be approved — its calendar holds dates that have "
            f"already gone by:\n{body}\n"
            f"Move those rounds with `/round amend`, or shorten the windows, then run "
            f"`/season placements-review` again. **Nothing has been approved.**"
        ),
        reason=f"its calendar holds dates that have already gone by:\n{body}",
    )


async def channel_on_server(guild, channel_id: int) -> bool:
    """Whether *channel_id* is still a channel of *guild*, only NotFound answering no.

    The cache is asked first and the API second, the cache holding no channel it has not
    seen. Any failure but NotFound answers yes: a check that could not tell must not be the
    thing that refuses a season.
    """
    if guild.get_channel(channel_id) is not None:
        return True
    try:
        await guild.fetch_channel(channel_id)
    except discord.NotFound:
        return False
    except Exception as exc:  # noqa: BLE001 — cannot tell, so not a fault
        log.warning("channel check: could not fetch channel %s: %s", channel_id, exc, exc_info=True)
    return True


#: Every channel a division posts to, in the order a manager reads them: the column that
#: holds it, what a league calls it, the command that sets it, and the module that needs
#: it — None where every season does.
DIVISION_CHANNELS = (
    ("lineup_channel_id", "lineup channel", "/division lineup-channel", None),
    ("calendar_channel_id", "calendar channel", "/division calendar-channel", None),
    ("forecast_channel_id", "weather channel", "/weather channel", "weather"),
    ("results_channel_id", "results channel", "/results channel results", "results"),
    ("standings_channel_id", "standings channel", "/results channel standings", "results"),
    ("penalty_channel_id", "verdicts channel", "/results channel verdicts", "results"),
    ("rsvp_channel_id", "RSVP channel", "/attendance channel rsvp", "attendance"),
    (
        "attendance_channel_id",
        "attendance channel",
        "/attendance channel attendance",
        "attendance",
    ),
)


async def division_channel_faults(modules, db_path: str, season_id: int, guild=None) -> list[str]:
    """Every channel a division of the season posts to that is not set, or not on the server.

    **Both confirmations ask this**: the first, and the one mid-season. Nothing
    clears a channel's id when Discord deletes the channel, and no module can be enabled
    once a season is under way, so mid-season every channel is still *set*; the question
    worth asking is whether it is still *there*. Asked at both, so the two cannot come to
    disagree about what a division needs.

    A channel is looked for in the cache and then fetched, a fetch answering NotFound
    being the one proof it is gone. Any other failure is **not** a fault: a check that
    could not tell must not be what refuses a season. With no *guild*, only whether each
    channel is set is judged. A cancelled division posts nothing and is passed over.
    """
    enabled = {
        None: True,
        "weather": await modules.is_weather_enabled(),
        "results": await modules.is_results_enabled(),
        "attendance": await modules.is_attendance_enabled(),
    }
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT d.name, d.lineup_channel_id, d.calendar_channel_id, "
            "       d.forecast_channel_id, rc.results_channel_id, "
            "       rc.standings_channel_id, rc.penalty_channel_id, "
            "       ac.rsvp_channel_id, ac.attendance_channel_id "
            "FROM divisions d "
            "LEFT JOIN division_results_config rc ON rc.division_id = d.id "
            "LEFT JOIN attendance_division_config ac ON ac.division_id = d.id "
            "WHERE d.season_id = ? AND d.status != 'CANCELLED' ORDER BY d.tier",
            (season_id,),
        )
        rows = await cursor.fetchall()

    faults: list[str] = []
    for row in rows:
        for column, label, command, module in DIVISION_CHANNELS:
            if not enabled[module]:
                continue
            value = row[column]
            if not value:
                faults.append(f"**{row['name']}** has no {label} — `{command}`.")
            elif guild is not None and not await channel_on_server(guild, int(value)):
                faults.append(
                    f"**{row['name']}**'s {label} is no longer on the server — `{command}`."
                )
    return faults


async def unsettled_signups(db_path: str) -> list[str]:
    """The signups not yet settled, named: what the review and the confirmation both list."""
    from leaguebot.core.services.driver_service import DRIVERS_SIGNUP_OF_DP_SQL
    from leaguebot.core.services.season_lifecycle_service import UNSETTLED_STATES

    placeholders = ",".join("?" for _ in UNSETTLED_STATES)
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            f"SELECT dp.discord_user_id, dp.current_state, sr.server_display_name "
            f"FROM driver_profiles dp "
            f"LEFT JOIN signup_records sr ON sr.id = {DRIVERS_SIGNUP_OF_DP_SQL} "
            f"WHERE dp.current_state IN ({placeholders}) "
            f"ORDER BY dp.current_state, dp.discord_user_id",
            (*UNSETTLED_STATES,),
        )
        unsettled_rows = await cursor.fetchall()

    state_labels = {
        "UNASSIGNED": "not yet placed",
        "PENDING_ADMIN_APPROVAL": "awaiting approval",
        "AWAITING_CORRECTION_PARAMETER": "awaiting approval",
        "PENDING_DRIVER_CORRECTION": "correcting their signup",
    }
    return [
        f"**{row['server_display_name'] or row['discord_user_id']}** — "
        f"{state_labels.get(row['current_state'], row['current_state'])}"
        for row in unsettled_rows
    ]


class SignupSettings(Protocol):
    """What the configuration's checks read of the signup module's settings."""

    @property
    def signup_channel_id(self) -> int | None: ...


class SignupModuleSettings(Protocol):
    """The signup module's settings, as the configuration's checks read them."""

    async def get_config(self) -> SignupSettings | None: ...


async def points_ordering_problems(db_path: str, season_id: int) -> list[str]:
    """Every place this season's points would score a lower position above a higher one.

    **One check because two surfaces ask the same question.** `/season placements-review` reports
    what would block an approval and the approval refuses on it; the review must look for every
    fault the approval refuses on, or a manager is sent to an approval that refuses for a fault
    the report told them nothing about.

    Two checks, because the points a season will run on can arrive from two directions.
    The attached server-level configs are what a first approval copies in, and until it
    does the season's own table is empty, so the season's table alone would pass every first
    approval. The season's own table is
    read as well, because the snapshot writes with INSERT OR REPLACE and clears nothing
    first, so a season being approved a second time can be holding entries an earlier
    approval left behind that no attached config would mention.

    Merged rather than concatenated: a re-approval sees the same fault from both sides,
    and a manager should read each broken position once.
    """
    return list(dict.fromkeys(
        await season_points_service.validate_attached_config_ordering(db_path, season_id)
        + await season_points_service.validate_monotonic_ordering(db_path, season_id)
    ))


async def missing_points_config_problems(db_path: str, season_id: int) -> list[str]:
    """Every points configuration this season is attached to that no longer exists.

    **One check because two surfaces ask the same question**, in the manner of
    `points_ordering_problems`: `/season placements-review` reports what would block an approval
    and the approval refuses on it, so a review never lists as attached a name the
    approval then chokes on.

    The attachment is a row in `season_points_links` carrying no foreign key, so a name
    that was never created would count towards the "a points configuration is attached"
    prerequisite and pass it, and the first thing to notice would be the points snapshot
    raising `ConfigNotFoundError` mid-command, after the defer, with no error handler on
    the tree to say so.
    """
    return await season_points_service.missing_attached_configs(db_path, season_id)


async def no_points_config_attached(db_path: str, season_id: int) -> bool:
    """Whether *season_id* has no points configuration attached at all.

    **One check because three surfaces ask the same question**: the configuration
    review refuses on it, the approval refuses on it, and `/season placements-review`
    withholds its button on it, so the review never offers Approve to a season the press
    would refuse — which can happen because `/results config detach` stays open in
    Placements.

    A link to a configuration that does not exist still counts as attached here; that
    one is `missing_points_config_problems`' fault. Test mode is no exception: it attaches Standard and Half Points when it is enabled and at no other
    moment, and a season that has had them detached is refused as any other.
    """
    return not await season_points_service.get_attached_config_names(db_path, season_id)


async def team_name_problems(team_service: TeamService, season_id: int | None) -> list[str]:
    """Every team whose name cannot become an asset filename (047 FR-032).

    Checks the server's team configuration and every division of the season under
    setup, naming **every** offending team rather than stopping at the first — a
    manager fixing them one review at a time is a manager the check is failing.

    **Not gated on the image module.** The normalised team name is the filename under
    which every graphic that draws a badge seeks that team's image, and Principle IX
    governs it as a structural invariant of teams rather than as an image-module
    concern. The rule stands on the filename alone.

    Only a season in SETUP reaches here, so an already-approved season is never
    re-validated.

    A stated exception (architecture.md, "A catch-all error handler"): the check only keeps a
    team name from becoming a bad artwork filename, so where the teams cannot be read it finds
    nothing wrong, and the review and the approval carry on with the names unchecked. The fault
    goes to the host's log alone.
    """
    problems: list[str] = []
    try:
        scopes: list[tuple[str, list[str]]] = [(
            "the server's team list",
            [team.name for team in await team_service.get_default_teams() if not team.is_reserve],
        )]
        if season_id is not None:
            scopes.extend(
                (f"division **{division_name}**", names)
                for division_name, names in await team_service.division_race_team_names(season_id)
            )
        for label, names in scopes:
            seen: dict[str, str] = {}
            for name in names:
                fault = validate_team_name(name, seen)
                if fault is not None:
                    problems.append(f"{label}: {fault}")
                key = normalise(name or "")
                if key:
                    seen.setdefault(key, name)
    except Exception as exc:  # noqa: BLE001 — a stated exception, see the docstring
        log.error("season: team name check failed: %s", exc, exc_info=True)
        return []
    return problems


_T = TypeVar("_T")
_R = TypeVar("_R")


async def ask_drawing(
    guard: str,
    count: Callable[[], Awaitable[_T | None]],
    ask: Callable[[_T], Awaitable[_R]],
    *,
    nothing: _R,
) -> _R:
    """Read the guard's own count with *count*, *ask* the image module's drawing about
    it, and hand back what it says, or *nothing* where no count applies (None), or
    either step could not run. *guard* names the guard in the host's log.

    One catch for the season's three capacity checks, the review's and the approval's
    (`lineup_problems`, and the review's calendar and attendance warnings). A
    capacity check that cannot run must not block an approval or a season, only a genuine
    over-capacity may, so the fault goes to the host's log in full and the check that asked
    answers as if told nothing was wrong (architecture.md, "A catch-all error handler", a
    stated exception beside `PlacementService._ask_drawing`'s). The round guard of `/round add`
    and the round imports is the change type's own (`calendar_round_refusal`).
    """
    try:
        counted = await count()
        if counted is None:
            return nothing
        return await ask(counted)
    except Exception as exc:  # noqa: BLE001 — a stated exception, see the docstring
        log.error("%s capacity check could not run: %s", guard, exc, exc_info=True)
        return nothing


async def lineup_problems(
    season_id: int,
    *,
    season_service: SeasonService,
    team_service: TeamService,
    image_drawing: ImageDrawing,
) -> list[str]:
    """Where the lineup template cannot draw a division of this season (047 FR-022).

    Counts each division's race teams, in tier order, and the drivers seated in each —
    an empty seat uncounted, the reserve team unhanded — and asks the drawing. The image
    module measures its own template and answers each problem as a sentence, unchanged.

    A check that cannot run lets the approval through (`ask_drawing`'s stated
    exception, architecture.md, "A catch-all error handler").
    """
    async def held() -> list[DivisionTeams]:
        divisions = await season_service.get_divisions(season_id)
        out: list[DivisionTeams] = []
        for division in sorted(divisions, key=lambda d: d.tier):
            entries = await team_service.get_division_teams(division.id)
            teams = tuple(
                TeamSeats(
                    entry["name"],
                    sum(
                        1 for seat in entry.get("seats", [])
                        if seat.get("discord_user_id")
                    ),
                )
                for entry in entries
                if not entry.get("is_reserve")
            )
            out.append(DivisionTeams(division.name, teams))
        return out

    return await ask_drawing(
        "season: lineup", held, image_drawing.lineup_review_problems, nothing=[],
    )


async def image_configuration_faults(image_drawing: ImageDrawing) -> list[str]:
    """The image module's faults that need no division, round, lineup or calendar.

    The rasteriser, every template a switched-on output draws, the per-tier colours and
    the driver portrait settings, each in the image module's own words
    (`ImageDrawing.rasteriser_fault`, `.template_faults`, `.colour_shortfall_problems`,
    `.portrait_fault`; core/services/image_drawing.py). **The one reading of them every
    gate shares**: the configuration review and its confirmation, both placements reviews
    and both confirmations of placements. Each review withholds its button on exactly what
    its confirmation refuses, so a manager is never shown a fault as blocking and then
    let past it.

    A stated exception (architecture.md, "A catch-all error handler"): templates that
    cannot be read are a fault of their own, never a pass, so that fault is let through;
    the colour and portrait checks stand aside on a failure of theirs, since a reader that
    cannot answer must not be the thing that refuses a season. The caller decides whether
    the module is enabled; this assumes it is.
    """
    faults: list[str] = []
    rasteriser_fault = image_drawing.rasteriser_fault()
    if rasteriser_fault is not None:
        faults.append(rasteriser_fault)
    try:
        faults += await image_drawing.template_faults()
    except Exception as exc:  # noqa: BLE001 — a stated exception, see the docstring
        log.error("config review: the templates could not be read: %s", exc, exc_info=True)
        return [*faults, "The image templates could not be read."]
    try:
        faults += [
            f"Tier colour: {problem}"
            for problem in await image_drawing.colour_shortfall_problems()
        ]
    except Exception as exc:  # noqa: BLE001 — a stated exception, see the docstring
        log.error("config review: the per-tier colours could not be read: %s", exc, exc_info=True)
    try:
        portrait_fault = await image_drawing.portrait_fault()
    except Exception as exc:  # noqa: BLE001 — a stated exception, see the docstring
        log.error("config review: the portrait settings could not be read: %s", exc, exc_info=True)
        portrait_fault = None
    if portrait_fault is not None:
        faults.append(portrait_fault)
    return faults


async def mid_season_configuration_faults(
    module_service: ModuleService, image_drawing: ImageDrawing
) -> list[str]:
    """The configuration review's checks that confirming mid-season placements repeats.

    Every check the configuration review makes is made again when placements are
    confirmed mid-season save those whose answer the season
    has already settled:

    * the league's two roles and the signup configuration, which confirming the
      configuration fixed;
    * the team names, the team list being fixed with them;
    * the points configurations, recorded upon the season when its placements were first
      confirmed and changed since only by an amendment of the season's points, which
      judges its own table. The review's check reads the server's configurations instead,
      which the season no longer uses — and one removed from the server would refuse
      with a remedy, `/results config detach`, that is refused once a season is
      confirmed.

    What is left is the image module's, whose templates, artwork and settings may all
    change while a season is raced.
    """
    if not await module_service.is_images_enabled():
        return []
    return await image_configuration_faults(image_drawing)


#: Said by the placements review and its confirmation alike: a season with no division has
#: nothing to race and would never reach Pending completion.
NO_DIVISIONS_REFUSAL = (
    "\u26d4 **This season has no divisions.** Add one with `/division add`."
)


async def season_has_divisions(season_id: int, *, season_service: SeasonService) -> bool:
    """Whether *season_id* holds a division that is not cancelled.

    A season with none has nothing to race, and would never reach Pending completion —
    so its placements are not confirmed.
    """
    divisions = await season_service.get_divisions(season_id)
    return any(division.status != "CANCELLED" for division in divisions)


async def placement_confirmation_faults(
    module_service: ModuleService, db_path: str, season_id: int, guild: discord.Guild | None = None
) -> tuple[list[str], list[str]]:
    """What stops placements being confirmed that no older gate checks.

    Returns ``(unsettled, channels)``: the unsettled signups, named, and every channel a
    division posts to that is not set or no longer on the server. The review
    withholds its button on either, and the confirmation refuses on either, from this one
    reading — mid-season as at the first confirmation.
    """
    unsettled = await unsettled_signups(db_path)
    return unsettled, await division_channel_faults(module_service, db_path, season_id, guild)


async def configuration_faults(
    season_id: int,
    guild: discord.Guild | None = None,
    *,
    module_service: ModuleService,
    signup_module_service: SignupModuleSettings,
    config_service: ConfigService,
    team_service: TeamService,
    db_path: str,
    image_drawing: ImageDrawing,
) -> list[str]:
    """Every fault that stops a season's configuration being confirmed.

    The configuration review checks everything that can be checked before the season has
    divisions: every check the placements review makes, save those concerning divisions,
    lineups, calendars and division channels. The placements review makes all of these
    again, the configuration of a module other than signup being free to change in
    between — save the league's two roles, which confirming the configuration fixes
    until the season ends, and so cannot have changed.

    **The roles are judged upon the server, not merely as stored**, where *guild*
    is given: both must still be on it, and the driver role must be one the bot can grant.
    Confirming fixes them, so this is the last moment a league can choose others freely.

    **One check for the review and the confirmation**, so the button is withheld on
    exactly what the confirmation refuses. Each fault is a line a league manager reads,
    naming what to run to put it right.
    """
    faults: list[str] = []

    # ── Signup ─────────────────────────────────────────────────────────────
    # The two roles are the league's, but only the signup module needs
    # them, so they are faults only while it is enabled.
    if await module_service.is_signup_enabled():
        signup_cfg = await signup_module_service.get_config()
        server_cfg = await config_service.get_server_config()
        base_role_id = server_cfg.base_role_id if server_cfg is not None else None
        driver_role_id = server_cfg.driver_role_id if server_cfg is not None else None
        if signup_cfg is None or signup_cfg.signup_channel_id is None:
            faults.append("The signup module has no **signup channel** — `/signup channel`.")
        if base_role_id is None:
            faults.append(
                "The signup module needs the league's **base role** — `/bot base-role`."
            )
        if driver_role_id is None:
            faults.append(
                "The signup module needs the league's **driver role** — `/bot driver-role`."
            )
        faults += league_role_faults(guild, base_role_id, driver_role_id)

    # ── Team names ─────────────────────────────────────────────────────────
    # The server's list alone: a season in configuration has no division to check.
    faults += [
        f"Team name: {problem}"
        for problem in await team_name_problems(team_service, None)
    ]

    # ── Results: points configurations ────────────────────────────────────
    if await module_service.is_results_enabled():
        if await no_points_config_attached(db_path, season_id):
            faults.append(
                "No points configuration is attached to this season — "
                "`/results config append`."
            )
        for name in await missing_points_config_problems(db_path, season_id):
            faults.append(
                f"The attached points configuration **{name}** does not exist — build "
                f"it with `/results config add`, or drop it with `/results config detach`."
            )
        faults += [
            f"Points table out of order: {fault}"
            for fault in await points_ordering_problems(db_path, season_id)
        ]

    # ── Images: rasteriser, templates, colours, portraits ─────────────────
    if await module_service.is_images_enabled():
        faults += await image_configuration_faults(image_drawing)

    return faults
