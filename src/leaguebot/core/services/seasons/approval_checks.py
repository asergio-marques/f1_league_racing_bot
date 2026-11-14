"""The checks a season's review and its confirmations make of the league.

The approval is judged twice: at the press, by the review's workflow, and again when it comes up
to run on the change queue, by the change type, which may import no cog. So every check both make
lives here as a public function, and the review commands, the workflows and the change types all
call it; the same question is then answered the same way wherever it is asked. A check is handed
the services it reads: one reading several of the league's services takes them as keywords named as
the bot carries them, and one reading one or two takes them in order. `not_done_section` is the
reply's list of what a confirmation could not do.

A caller reaches a check through this module (`approval_checks.team_name_problems`), never by
importing it by name, so that a test standing one in replaces it for every caller.
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, TypeVar

import discord

from leaguebot.core.db.database import get_connection
from leaguebot.core.models.division import Division
from leaguebot.core.models.round import AttendanceWindows, Round, WeatherWindows
from leaguebot.core.services.seasons.approval_window_service import calendar_faults
from leaguebot.core.services.modules.confirmation_refusals import Confirmation, read_refusals
from leaguebot.core.services.modules.image_drawing import DivisionTeams, ImageDrawing, TeamSeats
from leaguebot.core.services.modules.module_service import ModuleService, SwitchNotSignedUp
from leaguebot.core.services.seasons.season_service import SeasonService
from leaguebot.core.services.drivers.team_service import TeamService, validate_team_name
from leaguebot.core.utils.asset_slug import normalise
from leaguebot.core.utils.formatting import discord_ts
from leaguebot.core.utils.hooks import AwaitedHook

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
    """A Discord dynamic timestamp, full form, of an aware moment. Written here and not imported
    from the weather module's message builder, which core may not reach from a service."""
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
    seen. Any failure but NotFound answers yes, logged on the host: a check that could not tell
    must not be the thing that refuses a season.
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


async def channel_fault(
    guild, division_name: str, value, label: str, command: str
) -> str | None:
    """The line saying a division's channel is not set, or no longer on the *guild*, or None
    where it is fine: the one wording every module's channel faults and core's own are laid in.

    *value* is the channel's id as the column holds it; *label* is what a league calls the
    channel and *command* sets it. With no *guild*, only whether it is set is judged.
    """
    if not value:
        return f"**{division_name}** has no {label} — `{command}`."
    if guild is not None and not await channel_on_server(guild, int(value)):
        return f"**{division_name}**'s {label} is no longer on the server — `{command}`."
    return None


async def division_channel_faults(
    db_path: str,
    season_id: int,
    guild=None,
    *,
    division_channel_faults_read: AwaitedHook,
) -> list[str]:
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

    Core names the lineup and calendar channels, which every season needs; each module names
    its own through *division_channel_faults_read*, its switch asked of itself. A division's
    lines read in tier order: its own, then the modules' in the order they signed up.
    """
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT id, name, lineup_channel_id, calendar_channel_id FROM divisions "
            "WHERE season_id = ? AND status != 'CANCELLED' ORDER BY tier",
            (season_id,),
        )
        rows = await cursor.fetchall()

    divisions = {row["id"]: row["name"] for row in rows}
    handed = await division_channel_faults_read.call(divisions=divisions, guild=guild)
    faults: list[str] = []
    for row in rows:
        for fault in (
            await channel_fault(
                guild, row["name"], row["lineup_channel_id"], "lineup channel",
                "/division lineup-channel",
            ),
            await channel_fault(
                guild, row["name"], row["calendar_channel_id"], "calendar channel",
                "/division calendar-channel",
            ),
        ):
            if fault:
                faults.append(fault)
        for module_lines in handed:
            faults.extend(module_lines.get(row["id"], []))
    return faults


async def unsettled_signups(db_path: str, driver_names_read: AwaitedHook) -> list[str]:
    """The signups not yet settled, named: what the review and the confirmation both list.

    Each driver is named by what *driver_names_read* hands back for them, the name signup holds
    from their signup, or by their account where it holds none.
    """
    from leaguebot.core.services.seasons.season_lifecycle_service import UNSETTLED_STATES

    placeholders = ",".join("?" for _ in UNSETTLED_STATES)
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            f"SELECT id, discord_user_id, current_state FROM driver_profiles "
            f"WHERE current_state IN ({placeholders}) "
            f"ORDER BY current_state, discord_user_id",
            (*UNSETTLED_STATES,),
        )
        unsettled_rows = await cursor.fetchall()

    names: dict[int, str] = {}
    for handed in await driver_names_read.call([row["id"] for row in unsettled_rows]):
        names.update(handed)

    state_labels = {
        "UNASSIGNED": "not yet placed",
        "PENDING_ADMIN_APPROVAL": "awaiting approval",
        "AWAITING_CORRECTION_PARAMETER": "awaiting approval",
        "PENDING_DRIVER_CORRECTION": "correcting their signup",
    }
    return [
        f"**{names.get(row['id']) or row['discord_user_id']}** — "
        f"{state_labels.get(row['current_state'], row['current_state'])}"
        for row in unsettled_rows
    ]


def divisions_without_rounds(
    divisions: Sequence[Division], rounds_of: Mapping[int, Sequence[Round]]
) -> list[str]:
    """The names of the divisions in *divisions* with no round in *rounds_of*, keyed by the
    division's id, in the order given: a season is approved only with a round in every one."""
    return [division.name for division in divisions if not rounds_of[division.id]]


def duplicate_round_times(
    divisions: Sequence[Division], rounds_of: Mapping[int, Sequence[Round]]
) -> list[str]:
    """One line for each division in *divisions* holding two rounds at the same moment, naming
    the first moment repeated, in the order given: no two rounds of a division may share one."""
    problems: list[str] = []
    for division in divisions:
        seen: set[datetime] = set()
        for round_ in rounds_of[division.id]:
            if round_.scheduled_at in seen:
                problems.append(
                    f"**{division.name}** has multiple rounds scheduled at "
                    f"{discord_ts(round_.scheduled_at)}"
                )
                break
            seen.add(round_.scheduled_at)
    return problems


async def team_name_problems(team_service: TeamService, season_id: int | None) -> list[str]:
    """Every team whose name cannot become an asset filename.

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
    nothing wrong, and each review and confirmation that checks the names carries on with them
    unchecked. The fault goes to the host's log alone.
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
    stated exception beside `TemplateCapacityService._ask_drawing`'s). The round guard of
    `/round add` and the round imports is the change type's own (`calendar_round_refusal`).
    """
    try:
        counted = await count()
        if counted is None:
            return nothing
        return await ask(counted)
    except SwitchNotSignedUp:
        raise  # a module that never signed up is a fault in the bot's assembly, not a reading
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
    """Where the lineup template cannot draw a division of this season.

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
    `.portrait_fault`; core/services/modules/image_drawing.py). **The one reading of them every
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
    except SwitchNotSignedUp:
        raise  # a module that never signed up is a fault in the bot's assembly, not a reading
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

    Every check the configuration review makes is made again when placements are confirmed
    mid-season, save those whose answer the season has already settled:

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
    db_path: str,
    season_id: int,
    guild: discord.Guild | None = None,
    *,
    driver_names_read: AwaitedHook,
    division_channel_faults_read: AwaitedHook,
) -> tuple[list[str], list[str]]:
    """What stops placements being confirmed that no older gate checks.

    Returns ``(unsettled, channels)``: the unsettled signups, named, and every channel a
    division posts to that is not set or no longer on the server. The review
    withholds its button on either, and the confirmation refuses on either, from this one
    reading — mid-season as at the first confirmation. The unsettled are named through
    *driver_names_read*, and each module names its own channels through
    *division_channel_faults_read*.
    """
    unsettled = await unsettled_signups(db_path, driver_names_read)
    return unsettled, await division_channel_faults(
        db_path, season_id, guild, division_channel_faults_read=division_channel_faults_read
    )


async def configuration_faults(
    season_id: int,
    guild: discord.Guild | None = None,
    *,
    module_service: ModuleService,
    team_service: TeamService,
    confirmation_refused: AwaitedHook,
    configuration_faults_read: AwaitedHook,
    image_drawing: ImageDrawing,
) -> list[str]:
    """Every fault that stops a season's configuration being confirmed.

    The configuration review checks everything that can be checked before the season has
    divisions: every check the placements review makes, save those concerning divisions,
    lineups, calendars and division channels. The placements review makes all of these
    again, the configuration of a module other than signup being free to change in
    between — save the league's two roles, which confirming the configuration fixes
    until the season ends, and so cannot have changed.

    **The league's two roles are signup's to judge**, through *configuration_faults_read*,
    which hands the season and *guild*: signup reads its own switch and settings, so this check
    holds no rule of its. A module that is off hands nothing.

    **One check for the review and the confirmation**, so the button is withheld on
    exactly what the confirmation refuses. Each fault is a line a league manager reads,
    naming what to run to put it right.
    """
    faults: list[str] = []

    # ── Signup ─────────────────────────────────────────────────────────────
    # What a module faults the configuration on before the team names, read through the hook
    # signup signs up to, in the place signup's lines have always stood.
    for handed in await configuration_faults_read.call(season_id, guild):
        faults.extend(handed)

    # ── Team names ─────────────────────────────────────────────────────────
    # The server's list alone: a season in configuration has no division to check.
    faults += [
        f"Team name: {problem}"
        for problem in await team_name_problems(team_service, None)
    ]

    # ── What the modules refuse the configuration on ───────────────────────
    faults += [
        refusal.message
        for refusal in await read_refusals(
            confirmation_refused, season_id, Confirmation.CONFIGURATION
        )
    ]

    # ── Images: rasteriser, templates, colours, portraits ─────────────────
    if await module_service.is_images_enabled():
        faults += await image_configuration_faults(image_drawing)

    return faults
