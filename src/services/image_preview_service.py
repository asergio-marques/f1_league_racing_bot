"""Resolution shared by every `/images test` preview (045).

The withdrawn `/images test <kind>` drew a fabricated "Test Division" and the packaged
artwork, which answered "does this template render at all" rather than the question a
league manager is actually asking — whether *their* template, filled with *their* data,
produces a picture they are happy to post.

Every preview therefore resolves the league's own division and, where the kind pertains to
one, its own round; draws its own teams and seated drivers; and resolves its assets in the
directories the league configured, exactly as the posting path does. Only the outcome data
a league cannot configure in advance is fabricated, and that lives in
:mod:`services.image_preview_data`.

Nothing here writes. A preview leaves the league's records exactly as it found them.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

from db.database import get_connection
from models.image_constants import PREVIEW_KINDS

log = logging.getLogger(__name__)


# ── Refusal ───────────────────────────────────────────────────────────────

#: The reasons a preview refuses, in the order they are evaluated. A caller reads the
#: reason; a league manager reads the message. Both come from one raise, so the two cannot
#: drift apart across eleven commands.

#: Withdrawn at 046 as a *refusal*. A server holding no season now draws a fabricated
#: league instead, so nothing raises this any more; the name survives only because callers
#: and tests still refer to it.
REASON_NO_SEASON = "no_season"
REASON_NO_DIVISION = "no_division"
REASON_NO_ROUNDS = "no_rounds"
REASON_NO_ROUND = "no_round"
REASON_NO_TEAMS = "no_teams"
REASON_MYSTERY_ROUND = "mystery_round"
REASON_NOT_MYSTERY_ROUND = "not_mystery_round"

#: A season exists, so it must be resolved against, and a required parameter was omitted.
REASON_MISSING_INPUT = "missing_input"

#: No season *and* no configured team, for a kind that draws a roster. Deliberately
#: distinct from REASON_NO_TEAMS: that one says a division holds no team, this one that the
#: server has configured none at all, and a manager must be able to tell them apart.
#:
#: These kinds draw a team or a driver and no seat exists to fabricate a driver into, which
#: is the whole of the reason. It is **not** that a lineup template names its fields after
#: real teams: that was true until v6.0.0 and is not why the refusal stands.
REASON_NO_SERVER_TEAMS = "no_server_teams"


class PreviewRefused(Exception):
    """A preview cannot be drawn, and the configuration is why.

    Raised before any render is attempted, so that a fault of configuration is never
    reported to a manager as a failure to render (FR-015).
    """

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


# ── Shapes ────────────────────────────────────────────────────────────────


@dataclass
class PreviewDriver:
    """One driver as a preview will draw them.

    ``fabricated`` exists so the reply can say the picture is not showing a real roster.
    A manager who has not yet seated anyone should never mistake invented names for their
    own league's.
    """

    key: int
    display_name: str
    team_name: str
    seat_number: int
    nationality: str | None = None
    fabricated: bool = False


@dataclass
class DirectoryFault:
    """A configured asset directory that could not be resolved.

    The posting path discards this — it resolves each directory inside a bare ``except``
    and simply omits the class — after which the renderer reports the class as *not
    configured*, which is not what happened and gives a manager nothing to act on. A
    preview exists to be acted on, so it keeps the reason (FR-038).
    """

    asset_class: str
    configured_value: str
    reason: str


@dataclass
class PreviewContext:
    """Everything the eleven previews resolve in common.

    The context is deliberately **self-sufficient**: every datum a builder draws is on it,
    and no builder reaches back to the database for something the context could carry.
    That is what lets one context invented wholesale — a league with no row behind it at
    all — flow through all eleven builders unchanged (046).
    """

    server_id: int
    season_number: int
    division_id: int
    division_name: str
    division_tier: int
    round: object | None = None
    #: The division's whole calendar, resolved once. Three builders used to re-query this
    #: by ``division_id``; a fabricated league has no such row, and re-querying would have
    #: drawn it an empty calendar rather than the one it invented.
    rounds: list = field(default_factory=list)
    teams: list = field(default_factory=list)
    drivers: list[PreviewDriver] = field(default_factory=list)
    display_names: dict[str, str] = field(default_factory=dict)
    nationality_collected: bool = True
    asset_directories: dict[str, Path] = field(default_factory=dict)
    directory_faults: list[DirectoryFault] = field(default_factory=list)
    #: Drivers invented into the empty seats of a **real** division (045).
    fabricated_drivers: bool = False
    #: The whole league is invented, there being no season to draw (046). Never true at the
    #: same time as ``season_pending_approval``: a league is fabricated precisely because
    #: no season exists to be pending anything.
    fabricated_league: bool = False
    #: The season drawn is still awaiting ``/season approve``.
    season_pending_approval: bool = False
    #: Seated drivers drawn with no flag where the league collects nationality. A test-mode
    #: mock driver records none, and a manager reading the reply should be told why the
    #: flags are absent rather than left to guess.
    drivers_without_nationality: int = 0


# ── Fabricated drivers ────────────────────────────────────────────────────

#: Synthetic keys for fabricated drivers, well clear of any real profile id.
_FABRICATED_KEY_BASE = 8_000_000_000_000_000_000

#: Surnames for fabricated drivers. Ordinary lengths, so that the picture a manager judges
#: is the one their own roster would produce.
_FABRICATED_NAMES = (
    "Alvarez", "Bergström", "Castellano", "Dubois", "Eriksson",
    "Fontaine", "Gallagher", "Hoffmann", "Ivanov", "Jankowski",
    "Kowalski", "Lindqvist", "Moretti", "Nakamura", "O'Sullivan",
    "Petrov", "Quintana", "Rasmussen", "Silva", "Tanaka",
    "Ueda", "Vasquez", "Whitfield", "Ximenes", "Yamamoto", "Zieliński",
)

#: Nationalities for fabricated drivers, among those the signup wizard accepts. "Other" is
#: included deliberately: it is what a driver who stated none records, and it resolves an
#: asset like any other, so a preview that never drew it would leave that case unjudged.
_FABRICATED_NATIONALITIES = (
    "British", "Dutch", "French", "Italian", "German",
    "Spanish", "Brazilian", "Japanese", "Other",
)


def _fabricated_driver(index: int, team_name: str, seat_number: int, *, collected: bool):
    """One invented driver. Deterministic in *index*, so a picture is reproducible."""
    from services.image_preview_data import LONG_DRIVER_NAME

    # The third fabricated driver carries a name no league controls the length of, so that
    # a template field's bound is exercised without waiting for an unlucky signup.
    if index == 2:
        name = LONG_DRIVER_NAME
    else:
        name = _FABRICATED_NAMES[index % len(_FABRICATED_NAMES)]

    return PreviewDriver(
        key=_FABRICATED_KEY_BASE + index,
        display_name=name,
        team_name=team_name,
        seat_number=seat_number,
        nationality=(
            _FABRICATED_NATIONALITIES[index % len(_FABRICATED_NATIONALITIES)]
            if collected
            else None
        ),
        fabricated=True,
    )


# ── Resolution ────────────────────────────────────────────────────────────


async def resolve_context(
    bot,
    server_id: int,
    division_name: str | None = None,
    *,
    guild=None,
    round_number: int | None = None,
    kind: str | None = None,
    require_rounds: bool = False,
    require_teams: bool = False,
    require_mystery: bool | None = None,
    rng=None,
    now=None,
) -> PreviewContext:
    """Resolve a league's own data for one preview, or refuse and say why.

    The refusals are evaluated in the order the command contract fixes, so that a mistyped
    division is never reported as a missing round and a wrong round number is never
    reported as a missing team list.

    Where the server holds **no season at all**, there is nothing to resolve against and a
    fabricated league is drawn instead (FR-009). The division name and round number are
    disregarded there, and *kind* decides what the fabricated league must satisfy.

    *kind* names the preview, and where it is given the three ``require_*`` flags are read
    from ``PREVIEW_KINDS`` rather than passed. They remain for callers that predate it.

    *require_mystery* is ``None`` where the kind places no constraint on the round's
    format, ``False`` where a mystery round must be refused, and ``True`` where anything
    but a mystery round must be.
    """
    if kind is not None:
        spec = PREVIEW_KINDS[kind]
        require_rounds = kind == "calendar"
        require_teams = bool(spec["draws_roster"])
        require_mystery = spec["format_demanded"]

    # The approved season where there is one, the season pending approval otherwise
    # (FR-001). A season pending approval holds its divisions, rounds, teams, seats and
    # driver assignments in the same tables and the same shape as an approved one, so
    # widening the lookup is the whole of what drawing it takes.
    season = await bot.season_service.get_previewable_season(server_id)

    if season is None:
        # No season to resolve against, so the league is invented rather than the command
        # refused (FR-009). Whatever division name or round number was supplied is
        # disregarded: there is nothing for either to name (FR-022).
        from services.image_preview_league import build_fabricated_context

        return await build_fabricated_context(
            bot, server_id, kind=kind or "calendar", rng=rng, now=now
        )

    pending_approval = str(getattr(season.status, "value", season.status)) == "SETUP"

    # A season exists, so it must be resolved against and nothing is fabricated to stand in
    # for configuration that is absent (FR-007, FR-008).
    if not (division_name or "").strip():
        raise PreviewRefused(
            REASON_MISSING_INPUT,
            "⛔ This server has a season, so a preview must name which division to "
            "draw. Supply the `division` option.",
        )
    if round_number is None and (
        kind is not None and PREVIEW_KINDS[kind]["needs_round"]
    ):
        raise PreviewRefused(
            REASON_MISSING_INPUT,
            "⛔ This preview is drawn for one round, and this server has a season to "
            "draw it from. Supply the `round` option.",
        )

    divisions = await bot.season_service.get_divisions(season.id)
    wanted = (division_name or "").strip().casefold()
    division = next(
        (d for d in divisions if d.name.strip().casefold() == wanted), None
    )
    if division is None:
        known = ", ".join(f"`{d.name}`" for d in divisions) or "none"
        raise PreviewRefused(
            REASON_NO_DIVISION,
            f"⛔ No division named `{division_name}` in the active season. "
            f"This season holds: {known}.",
        )

    context = PreviewContext(
        server_id=server_id,
        season_number=season.season_number,
        division_id=division.id,
        division_name=division.name,
        division_tier=division.tier,
        season_pending_approval=pending_approval,
    )

    rounds = await bot.season_service.get_division_rounds(division.id)
    context.rounds = rounds

    if require_rounds and not rounds:
        raise PreviewRefused(
            REASON_NO_ROUNDS,
            f"⛔ `{division.name}` holds no configured round, so there is no calendar "
            f"to draw. Add rounds with `/round add` first.",
        )

    if round_number is not None:
        match = next((r for r in rounds if r.round_number == round_number), None)
        if match is None:
            held = (
                ", ".join(str(r.round_number) for r in rounds) if rounds else "none"
            )
            raise PreviewRefused(
                REASON_NO_ROUND,
                f"⛔ `{division.name}` has no round {round_number}. "
                f"It holds rounds: {held}.",
            )
        context.round = match

    # The team read serves the drivers of every kind that draws them, and is two small
    # queries, so it is not worth conditioning on the kind.
    await _load_teams_and_drivers(bot, context, guild=guild)

    if require_teams and not [
        t for t in context.teams if not getattr(t, "is_reserve", False)
    ]:
        raise PreviewRefused(
            REASON_NO_TEAMS,
            f"⛔ `{division.name}` holds no team beyond the Reserve team, so there is "
            f"nothing to draw. Add one with `/team add` first.",
        )

    if require_mystery is not None and context.round is not None:
        is_mystery = _format_of(context.round) == "MYSTERY"
        if require_mystery and not is_mystery:
            raise PreviewRefused(
                REASON_NOT_MYSTERY_ROUND,
                f"⛔ Round {round_number} of `{division.name}` is not a mystery round, "
                f"so there is no mystery notice to draw.",
            )
        if not require_mystery and is_mystery:
            raise PreviewRefused(
                REASON_MYSTERY_ROUND,
                f"⛔ Round {round_number} of `{division.name}` is a mystery round, which "
                f"carries no forecast. Use `/images test weather-mystery` for its notice.",
            )

    context.asset_directories, context.directory_faults = await resolve_asset_directories(
        bot, server_id
    )
    return context


def _format_of(round_obj) -> str:
    """The round's format as a bare string, whatever shape the record carries it in."""
    raw = getattr(round_obj, "format", None)
    return str(getattr(raw, "value", raw) or "NORMAL").upper()


async def _load_teams_and_drivers(bot, context: PreviewContext, *, guild=None) -> None:
    """Read the division's teams and seats, and fill the drivers the preview will draw.

    The team shape mirrors what ``image_lineup_post.build_drawing`` passes to the lineup's
    ``resolve_drawing``, so the preview and the posting path hand the same thing to the
    same function.
    """
    async with get_connection(bot.db_path) as db:
        instances = await (
            await db.execute(
                "SELECT id, name, max_seats, is_reserve FROM team_instances "
                "WHERE division_id = ? ORDER BY is_reserve ASC, id ASC",
                (context.division_id,),
            )
        ).fetchall()

        teams = []
        for instance in instances:
            seats = await (
                await db.execute(
                    # `signup_records` is keyed by (server_id, discord_user_id) and
                    # carries no driver_profile_id, so this is the join the table admits.
                    # The posting paths joined a phantom column until 2026-08-18 and could
                    # not render at all; they now join as this does.
                    "SELECT ts.seat_number, dp.id AS profile_id, dp.discord_user_id, "
                    "       dp.is_test_driver, dp.test_display_name, "
                    "       sr.server_display_name, sr.discord_username, sr.nationality "
                    "FROM team_seats ts "
                    "LEFT JOIN driver_season_assignments dsa "
                    "       ON dsa.team_seat_id = ts.id AND dsa.division_id = ? "
                    "LEFT JOIN driver_profiles dp ON dp.id = dsa.driver_profile_id "
                    "LEFT JOIN signup_records sr "
                    "       ON sr.server_id = dp.server_id "
                    "      AND sr.discord_user_id = CAST(dp.discord_user_id AS TEXT) "
                    "WHERE ts.team_instance_id = ? ORDER BY ts.seat_number",
                    (context.division_id, instance["id"]),
                )
            ).fetchall()
            teams.append(
                SimpleNamespace(
                    name=instance["name"],
                    is_reserve=bool(instance["is_reserve"]),
                    max_seats=instance["max_seats"],
                    seats=[
                        SimpleNamespace(
                            seat_number=row["seat_number"],
                            discord_user_id=row["discord_user_id"],
                            profile_id=row["profile_id"],
                            server_display_name=row["server_display_name"],
                            discord_username=row["discord_username"],
                            test_display_name=row["test_display_name"],
                            nationality=row["nationality"],
                        )
                        for row in seats
                    ],
                )
            )

    context.teams = teams
    context.nationality_collected = await _nationality_collected(bot, context.server_id)

    # The first link of the name chain is the account's display name on the server at the
    # moment of generation, which only the guild can answer.
    if guild is not None:
        for team in teams:
            for seat in team.seats:
                if seat.discord_user_id is None:
                    continue
                member = guild.get_member(int(seat.discord_user_id))
                if member is not None:
                    context.display_names[str(seat.discord_user_id)] = member.display_name

    context.drivers, context.fabricated_drivers = _drivers_from_teams(
        teams, context.display_names, collected=context.nationality_collected
    )

    # A seated driver with no nationality of their own is drawn without a flag, as a
    # posting would draw them (FR-028). Counting them lets the reply say why the flags are
    # missing — a test-mode mock driver records none, having no signup record, and a
    # maintainer would otherwise read the blank flags as a broken asset directory.
    if context.nationality_collected:
        context.drivers_without_nationality = sum(
            1
            for driver in context.drivers
            if not driver.fabricated and not driver.nationality
        )


def _drivers_from_teams(
    teams, display_names: dict[str, str], *, collected: bool
) -> tuple[list[PreviewDriver], bool]:
    """The flat driver list, fabricating only where the division has seated nobody.

    The rule is per **division**, not per seat (FR-018, FR-020). A league that has seated
    some of its drivers is drawn as it stands, unoccupied seats included, because that is
    what its posting would look like. A league that has seated none would otherwise draw an
    empty grid that tells it nothing, so every seat is filled with an invented driver.
    """
    from services.image_lineup_service import resolve_driver_name

    seated: list[PreviewDriver] = []
    for team in teams:
        for seat in team.seats:
            if seat.discord_user_id is None and seat.profile_id is None:
                continue
            key = seat.profile_id if seat.profile_id is not None else seat.discord_user_id
            name = resolve_driver_name(
                discord_user_id=seat.discord_user_id,
                display_name=display_names.get(str(seat.discord_user_id)),
                signup_display_name=seat.server_display_name,
                signup_username=seat.discord_username,
                test_display_name=seat.test_display_name,
            )
            seated.append(
                PreviewDriver(
                    key=int(key),
                    display_name=name,
                    team_name=team.name,
                    seat_number=int(seat.seat_number),
                    nationality=seat.nationality if collected else None,
                    fabricated=False,
                )
            )

    if seated:
        return seated, False

    fabricated: list[PreviewDriver] = []
    index = 0
    for team in teams:
        for seat in team.seats:
            driver = _fabricated_driver(
                index, team.name, int(seat.seat_number), collected=collected
            )
            fabricated.append(driver)

            # Write the invented driver onto the seat as well. Every type but the lineup
            # draws from the flat list, but the lineup draws the seats themselves, and a
            # seat left unoccupied would render an empty grid — the very thing fabricating
            # a driver exists to avoid.
            seat.discord_user_id = driver.key
            seat.server_display_name = driver.display_name
            seat.discord_username = driver.display_name
            seat.nationality = driver.nationality
            index += 1
    return fabricated, bool(fabricated)


async def _nationality_collected(bot, server_id: int) -> bool:
    """True where the league collects a driver's nationality at all.

    Read from ``signup_module_settings``, which is where the setting lives. The posting
    paths read a ``signup_config`` table that no migration creates until 2026-08-18, and
    swallowed the failure, so the switch reached no graphic at all; they now read the same
    table this does.
    """
    try:
        async with get_connection(bot.db_path) as db:
            row = await (
                await db.execute(
                    "SELECT nationality_required FROM signup_module_settings "
                    "WHERE server_id = ?",
                    (server_id,),
                )
            ).fetchone()
    except Exception:  # noqa: BLE001 — a league without the signup module collects
        return True
    if row is None:
        return True
    return bool(row["nationality_required"])


# ── Asset directories ─────────────────────────────────────────────────────

#: Asset class -> the configuration column naming its directory. The same pairing every
#: posting path makes, gathered here once so a preview cannot resolve a different set.
ASSET_CLASS_COLUMNS: tuple[tuple[str, str], ...] = (
    ("track", "track_image_directory"),
    ("team", "team_image_directory"),
    ("flag", "flag_directory"),
    ("driver", "driver_image_directory"),
    ("marker", "marker_directory"),
    ("weather", "weather_icon_directory"),
    ("tyre", "tyre_directory"),
)


async def resolve_asset_directories(
    bot, server_id: int
) -> tuple[dict[str, Path], list[DirectoryFault]]:
    """The league's own asset directories, and the ones that would not resolve.

    This is the whole of FR-035: a preview resolves what the league configured, never the
    packaged directories the withdrawn command hardcoded. Where a configured path is
    rejected, the reason is kept rather than discarded, so the reply can say which value
    was refused and why instead of calling the class unconfigured (FR-038).
    """
    from services.image_render_service import resolve_configured_directories

    faults: list[DirectoryFault] = []

    config = await bot.image_config_service.get_config(server_id)
    if config is None:
        return {}, faults

    # The same resolution the posting paths perform, so a preview cannot resolve a
    # different set of directories from the post it is previewing.
    directories, rejected = resolve_configured_directories(
        config, ASSET_CLASS_COLUMNS, image_type="preview"
    )
    column_of = dict(ASSET_CLASS_COLUMNS)

    def _configured(asset_class: str) -> str:
        return str(getattr(config, column_of[asset_class], "") or "")

    for asset_class, reason in rejected.items():
        faults.append(
            DirectoryFault(
                asset_class=asset_class,
                configured_value=_configured(asset_class),
                reason=reason,
            )
        )

    # A path that resolves but names nothing on disk is reported and still passed on.
    # Containment is all the resolver judges, so this case reaches the renderer as an
    # ordinary directory holding no file, and every asset of the class falls back.
    # Reporting it is the difference between a manager seeing "every flag fell back"
    # and seeing "your flag directory does not exist".
    for asset_class, resolved in directories.items():
        if not resolved.is_dir():
            faults.append(
                DirectoryFault(
                    asset_class=asset_class,
                    configured_value=_configured(asset_class),
                    reason="the configured directory does not exist",
                )
            )

    return directories, faults


# ── The previews ──────────────────────────────────────────────────────────
#
# Each returns ``[(label, template_key, spec_builder), …]`` — one entry per picture the
# kind draws. The builder is handed to the render service, which owns the pipeline.
#
# Every one of them calls the same ``resolve_drawing`` the posting path calls, with the
# league's own values, and hands the league's own directories to ``build_fill_spec``. That
# is the whole of the feature: the preview differs from the post in where the outcome data
# came from, and in nothing else.


async def build_calendar_preview(bot, context: PreviewContext):
    """The calendar of the named division, exactly as configured (FR-016).

    Fabricates nothing: the rounds, their tracks, their formats and their dates are the
    league's own, and the crop falls where its own round count puts it.
    """
    from services.calendar_post_service import tracks_by_name
    from services.image_calendar_service import build_fill_spec, resolve_drawing

    config = await bot.image_config_service.get_config(context.server_id)
    rounds = context.rounds
    tracks = await tracks_by_name(bot.db_path)

    drawing = resolve_drawing(
        division_name=context.division_name,
        division_tier=context.division_tier,
        season_number=context.season_number,
        rounds=rounds,
        tracks=tracks,
        date_format=getattr(config, "date_format", None),
        time_format=getattr(config, "time_format", None),
        time_zone=getattr(config, "time_zone", None),
    )

    # The calendar names its two directories separately rather than taking the map every
    # other type takes; both come from the league's configuration all the same.
    track_directory = context.asset_directories.get("track")
    flag_directory = context.asset_directories.get("flag")

    def _spec(root):
        return build_fill_spec(
            drawing,
            root,
            track_directory=track_directory,
            flag_directory=flag_directory,
        )

    return [("Calendar", "calendar_template", _spec)]


async def build_lineup_preview(bot, context: PreviewContext):
    """The lineup of the named division: its own teams, and its own seated drivers.

    Where the division has seated nobody, every seat carries an invented driver — written
    onto the seats during resolution, so the lineup draws them as it draws any other.
    """
    from services.image_lineup_service import build_fill_spec, resolve_drawing

    drawing = resolve_drawing(
        division_name=context.division_name,
        division_tier=context.division_tier,
        season_number=context.season_number,
        teams=context.teams,
        display_names=context.display_names,
        nationality_collected=context.nationality_collected,
    )

    def _spec(root):
        spec = build_fill_spec(
            drawing, root, asset_directories=context.asset_directories
        )
        spec.asset_directory_faults = {
            fault.asset_class: fault.reason for fault in context.directory_faults
        }
        return spec

    return [("Lineup", "lineup_template", _spec)]


# ── Shared by the kinds that fabricate an outcome ─────────────────────────


def _spec_with_faults(build_fill_spec, drawing, context: PreviewContext):
    """A spec builder handing the league's directories, and its rejections, to the filler."""

    def _build(root):
        spec = build_fill_spec(
            drawing, root, asset_directories=context.asset_directories
        )
        spec.asset_directory_faults = {
            fault.asset_class: fault.reason for fault in context.directory_faults
        }
        return spec

    return _build


def _team_role_ids(context: PreviewContext) -> dict[str, int]:
    """A stable id per team name.

    The results and standings drawings key a team by its Discord role id. A preview draws no
    role, so the team's position in the division's own team list stands in — the identity
    only has to be consistent between the rows and the name map.
    """
    return {team.name: index + 1 for index, team in enumerate(context.teams)}


def _racing_drivers(context: PreviewContext) -> list[PreviewDriver]:
    """The drivers a classification is drawn over: the division's fielded cars.

    A reserve stands in for an absent regular; they do not add a car to the grid. Drawing
    them alongside would give a division of eleven teams twenty-four entries, which is not
    a classification any league would see — and would overflow a template sized for the
    field. The lineup draws them, because a lineup is a roster and not a classification.
    """
    reserve_teams = {
        team.name for team in context.teams if getattr(team, "is_reserve", False)
    }
    racing = [d for d in context.drivers if d.team_name not in reserve_teams]
    return racing or list(context.drivers)


def _racing_teams(context: PreviewContext) -> list:
    """The teams a constructor classification is drawn over — every team but the reserve.

    Mirrors :func:`_racing_drivers`: the reserve team stands in for an absent regular and is
    not itself a constructor, so drawing it as one would overflow a template sized for the
    division's real teams.
    """
    return [team for team in context.teams if not getattr(team, "is_reserve", False)]


def _driver_maps(context: PreviewContext, drivers=None):
    """Names, teams and nationalities keyed as the drawings expect them."""
    drivers = list(context.drivers if drivers is None else drivers)
    role_of = _team_role_ids(context)
    names = {d.key: d.display_name for d in drivers}
    teams = {role_of[d.team_name]: d.team_name for d in drivers if d.team_name in role_of}
    flags = {d.key: d.nationality for d in drivers}
    return names, teams, flags, role_of


def _race_name(context: PreviewContext) -> str:
    """What the round is called, as a posting would name it."""
    from services.image_rsvp_service import MYSTERY_RACE_NAME

    round_obj = context.round
    if round_obj is None:
        return ""
    if _format_of(round_obj) == "MYSTERY":
        return MYSTERY_RACE_NAME
    return getattr(round_obj, "track_name", None) or MYSTERY_RACE_NAME


# ── Check-in call ─────────────────────────────────────────────────────────


async def build_rsvp_preview(bot, context: PreviewContext):
    """The check-in call for the named round. Fabricates nothing.

    The round's own format decides its session list, its own schedule the times, and the
    division's own check-in configuration the deadline.
    """
    from services.attendance_service import derive_checkin_deadline
    from services.image_rsvp_service import build_fill_spec, resolve_drawing

    config = await bot.image_config_service.get_config(context.server_id)
    round_obj = context.round
    is_mystery = _format_of(round_obj) == "MYSTERY"

    deadline_hours = 24
    try:
        division_config = await bot.attendance_service.get_division_config(
            context.division_id
        )
        if division_config is not None:
            deadline_hours = getattr(division_config, "rsvp_deadline_hours", 24)
    except Exception:  # noqa: BLE001 — a league without the attendance module still previews
        pass

    scheduled_at = round_obj.scheduled_at
    drawing = resolve_drawing(
        division_name=context.division_name,
        round_number=round_obj.round_number,
        round_format=_format_of(round_obj),
        scheduled_at=scheduled_at,
        deadline_at=derive_checkin_deadline(scheduled_at, deadline_hours),
        track_name=None if is_mystery else getattr(round_obj, "track_name", None),
        race_name=_race_name(context),
        country_name=await _country_of(bot, round_obj),
        is_mystery=is_mystery,
        division_tier=context.division_tier,
        season_number=context.season_number,
        date_format=getattr(config, "date_format", None),
        time_format=getattr(config, "time_format", None),
        time_zone=getattr(config, "time_zone", None),
    )

    return [
        ("Check-in call", "rsvp_template", _spec_with_faults(build_fill_spec, drawing, context))
    ]


async def _country_of(bot, round_obj) -> str | None:
    """The country the round is run in — the datum its flag resolves by."""
    if round_obj is None or _format_of(round_obj) == "MYSTERY":
        return None
    from services.calendar_post_service import tracks_by_name

    tracks = await tracks_by_name(bot.db_path)
    record = tracks.get(getattr(round_obj, "track_name", None))
    return getattr(record, "country", None) if record else None


# ── Session results ───────────────────────────────────────────────────────


async def build_results_preview(bot, context: PreviewContext):
    """One picture per session the named round is run over (FR-023).

    The drivers are the division's own; the classification over them is fabricated, because
    a round not yet run has none.
    """
    from models.round import RoundFormat
    from services.image_preview_data import (
        fabricate_qualifying_rows,
        fabricate_race_rows,
    )
    from services.image_results_service import build_fill_spec, resolve_drawing
    from services.result_submission_service import get_sessions_for_format

    config = await bot.image_config_service.get_config(context.server_id)
    drivers = _racing_drivers(context)
    names, teams, flags, role_of = _driver_maps(context, drivers)
    round_obj = context.round

    # The results module works in its own session vocabulary — Sprint/Feature Qualifying
    # and Race — which is not the schedule's (Short/Long/Full). `get_sessions_for_format`
    # is the authority on which sessions a round runs *for results*, and is called rather
    # than restated. The weather previews take the schedule's vocabulary instead, because
    # that is the one their slot ceilings are keyed by.
    try:
        round_format = RoundFormat(_format_of(round_obj))
    except ValueError:
        round_format = RoundFormat.NORMAL
    sessions = get_sessions_for_format(round_format)

    requests = []
    for session in sessions:
        is_qualifying = session.is_qualifying
        # A points map by finishing position, generous enough that a mid-field entry still
        # scores and the "conferred no points" case is still reachable at the tail.
        points_map = (
            {1: 3, 2: 2, 3: 1}
            if is_qualifying
            else {n: max(0, 26 - 2 * (n - 1)) for n in range(1, 14)}
        )
        rows = (
            fabricate_qualifying_rows(drivers, role_of, points_map)
            if is_qualifying
            else fabricate_race_rows(drivers, role_of, points_map)
        )

        drawing = resolve_drawing(
            session_type=session,
            is_sprint=_format_of(round_obj) == "SPRINT",
            result_status="FINAL",
            division_name=context.division_name,
            round_number=round_obj.round_number,
            race_name=_race_name(context),
            driver_rows=rows,
            points_map={row.driver_user_id: row.points_awarded for row in rows},
            driver_names=names,
            team_names=teams,
            nationalities=flags,
            division_tier=context.division_tier,
            season_number=context.season_number,
            fastest_lap_colour=getattr(config, "fastest_lap_colour", None),
            nationality_collected=context.nationality_collected,
        )
        requests.append(
            (
                f"Results — {session.value.replace('_', ' ').title()}",
                drawing.template_key,
                _spec_with_faults(build_fill_spec, drawing, context),
            )
        )
    return requests


# ── Standings ─────────────────────────────────────────────────────────────


async def build_standings_preview(bot, context: PreviewContext):
    """Both championships, as they would stand after the named round (FR-025, FR-026).

    The grid holds the division's own calendar, so its width is the width a league would
    actually see — the case a fabricated calendar could never put a template through.
    Session results for the rounds already run are fabricated over the division's own
    drivers, through the same builders the results preview calls.
    """
    from types import SimpleNamespace

    from services.image_preview_data import fabricate_standings_round_results
    from services.image_standings_service import (
        CONSTRUCTORS_TEMPLATE_KEY,
        DRIVERS_TEMPLATE_KEY,
        RoundHeading,
        build_fill_spec,
        resolve_drawing,
    )

    drivers = _racing_drivers(context)
    names, teams, flags, role_of = _driver_maps(context, drivers)
    round_obj = context.round
    racing_teams = _racing_teams(context)
    tracks = await _tracks(bot)

    headings = []
    round_formats: dict[int, str] = {}
    for ordinal, entry in enumerate(
        sorted(context.rounds, key=lambda r: r.round_number), start=1
    ):
        entry_format = _format_of(entry)
        track_name = None if entry_format == "MYSTERY" else getattr(entry, "track_name", None)
        record = tracks.get(track_name) if track_name else None
        headings.append(
            RoundHeading(
                ordinal=ordinal,
                number=str(entry.round_number),
                track=track_name,
                country=getattr(record, "country", None) if record else None,
            )
        )
        round_formats[ordinal] = entry_format

    # Only the rounds up to and including the one named have been run.
    run_ordinals = [
        heading.ordinal
        for heading in headings
        if int(heading.number) <= int(round_obj.round_number)
    ]
    round_session_results = fabricate_standings_round_results(
        run_ordinals, round_formats, drivers, role_of
    )

    team_seat_assignments = {
        role_of[team.name]: {
            d.key: d.seat_number for d in drivers if d.team_name == team.name
        }
        for team in racing_teams
        if team.name in role_of
    }
    team_seat_counts = {
        role_of[team.name]: int(getattr(team, "max_seats", 0) or 0)
        for team in racing_teams
        if team.name in role_of
    }

    driver_snapshots = [
        SimpleNamespace(
            driver_user_id=driver.key,
            standing_position=position,
            total_points=max(0, 120 - (position - 1) * 9),
            finish_counts={},
            first_finish_rounds={},
            race_participant=True,
        )
        for position, driver in enumerate(drivers, start=1)
    ]

    team_snapshots = [
        SimpleNamespace(
            team_role_id=role_of[team.name],
            standing_position=position,
            total_points=max(0, 200 - (position - 1) * 17),
            finish_counts={},
            first_finish_rounds={},
        )
        for position, team in enumerate(racing_teams, start=1)
        if team.name in role_of
    ]

    shared = dict(
        division_name=context.division_name,
        round_number=round_obj.round_number,
        result_status="FINAL",
        division_tier=context.division_tier,
        season_number=context.season_number,
        race_name=_race_name(context),
        nationality_collected=context.nationality_collected,
        rounds=headings,
        round_session_results=round_session_results,
    )

    drivers_drawing = resolve_drawing(
        template_key=DRIVERS_TEMPLATE_KEY,
        snapshots=driver_snapshots,
        display_names=names,
        team_names={d.key: d.team_name for d in drivers},
        movements={d.key: None for d in drivers},
        nationalities=flags,
        **shared,
    )
    constructors_drawing = resolve_drawing(
        template_key=CONSTRUCTORS_TEMPLATE_KEY,
        snapshots=team_snapshots,
        display_names={role_of[t.name]: t.name for t in racing_teams if t.name in role_of},
        team_names={role_of[t.name]: t.name for t in racing_teams if t.name in role_of},
        movements={role_of[t.name]: None for t in racing_teams if t.name in role_of},
        team_seat_assignments=team_seat_assignments,
        team_seat_counts=team_seat_counts,
        driver_display_names=names,
        **shared,
    )

    return [
        (
            "Standings — drivers",
            DRIVERS_TEMPLATE_KEY,
            _spec_with_faults(build_fill_spec, drivers_drawing, context),
        ),
        (
            "Standings — constructors",
            CONSTRUCTORS_TEMPLATE_KEY,
            _spec_with_faults(build_fill_spec, constructors_drawing, context),
        ),
    ]


# ── Attendance sheet ──────────────────────────────────────────────────────


async def build_attendance_preview(bot, context: PreviewContext):
    """The sheet as it would stand after the named round (FR-027, FR-028).

    The grid holds every round of the division's calendar; records are fabricated up to and
    including the named one, and for none after it, so the emptying of a round yet to be run
    can be judged beside those already finalised.
    """
    from services.image_attendance_service import (
        ATTENDANCE_TEMPLATE_KEY,
        RoundHeading,
        build_fill_spec,
        resolve_drawing,
    )
    from services.image_preview_data import fabricate_attendance_records

    names, _teams, flags, _role_of = _driver_maps(context)
    round_obj = context.round
    rounds = context.rounds
    tracks = await _tracks(bot)

    headings = []
    for ordinal, entry in enumerate(sorted(rounds, key=lambda r: r.round_number), start=1):
        track_name = (
            None if _format_of(entry) == "MYSTERY" else getattr(entry, "track_name", None)
        )
        record = tracks.get(track_name) if track_name else None
        headings.append(
            RoundHeading(
                ordinal=ordinal,
                number=str(entry.round_number),
                track=track_name,
                country=getattr(record, "country", None) if record else None,
            )
        )

    # Only the rounds up to and including the one named have been run (FR-027).
    run_ordinals = [
        heading.ordinal
        for heading in headings
        if int(heading.number) <= int(round_obj.round_number)
    ]
    records = fabricate_attendance_records(context.drivers, run_ordinals)

    drawing = resolve_drawing(
        division_name=context.division_name,
        round_number=round_obj.round_number,
        records=records,
        display_names=names,
        team_names={d.key: d.team_name for d in context.drivers},
        nationalities=flags,
        rounds=headings,
        autoreserve_threshold=6,
        autosack_threshold=10,
        division_tier=context.division_tier,
        season_number=context.season_number,
        race_name=_race_name(context),
        nationality_collected=context.nationality_collected,
    )

    return [
        (
            "Attendance sheet",
            ATTENDANCE_TEMPLATE_KEY,
            _spec_with_faults(build_fill_spec, drawing, context),
        )
    ]


async def _tracks(bot):
    from services.calendar_post_service import tracks_by_name

    return await tracks_by_name(bot.db_path)


# ── Verdicts ──────────────────────────────────────────────────────────────


async def build_verdict_preview(bot, context: PreviewContext):
    """One picture per verdict case (FR-032 to FR-034).

    The driver is one of the division's own, the session one the named round is run over,
    and the sanction one the module can actually issue — never one it cannot.
    """
    from services.image_preview_data import fabricate_verdict_cases
    from services.image_rsvp_service import session_names
    from services.image_verdict_service import (
        VerdictDrawing,
        VerdictKind,
        build_fill_spec,
        sanction_text,
    )

    round_obj = context.round
    driver = context.drivers[0] if context.drivers else None
    if driver is None:
        return []

    cases = fabricate_verdict_cases(
        driver, list(session_names(_format_of(round_obj)))
    )

    requests = []
    for index, case in enumerate(cases, start=1):
        drawing = VerdictDrawing(
            kind=VerdictKind.PENALTY,
            division_name=context.division_name,
            round_number=round_obj.round_number,
            driver_name=driver.display_name,
            penalty=sanction_text(case["penalty_type"], case["time_seconds"]),
            description=case["description"],
            justification=case["justification"],
            season_number=context.season_number,
            division_tier=context.division_tier,
            race_name=_race_name(context),
            session_name=case["session_name"],
            team_name=driver.team_name,
            driver_nationality=driver.nationality,
            team_slug_source=driver.team_name,
            nationality_collected=context.nationality_collected,
        )
        requests.append(
            (
                f"Verdict {index} — {drawing.penalty}",
                "verdicts_template",
                _spec_with_faults(build_fill_spec, drawing, context),
            )
        )
    return requests


# ── Weather ───────────────────────────────────────────────────────────────


async def build_weather_preview(bot, context: PreviewContext, *, phase: int):
    """One forecast picture for the named round, at *phase* (FR-029 to FR-031).

    Phase 0 is the mystery notice, which holds no session and carries no forecast. The
    template drawn is the one the round's own format calls for.
    """
    from services.image_preview_data import (
        fabricate_phase2_sessions,
        fabricate_phase3_sessions,
        fabricate_rain_probability,
    )
    from services.image_weather_service import build_fill_spec, resolve_drawing

    round_obj = context.round
    round_format = _format_of(round_obj)
    is_mystery = round_format == "MYSTERY"

    sessions = None
    rain = None
    if phase == 1:
        rain = fabricate_rain_probability()
    elif phase == 2:
        sessions = fabricate_phase2_sessions(round_format)
        rain = fabricate_rain_probability()
    elif phase == 3:
        sessions = fabricate_phase3_sessions(round_format)
        rain = fabricate_rain_probability()

    drawing = resolve_drawing(
        phase=phase,
        division_name=context.division_name,
        round_number=round_obj.round_number,
        round_format=round_format,
        track_name=None if is_mystery else getattr(round_obj, "track_name", None),
        race_name=_race_name(context),
        country_name=await _country_of(bot, round_obj),
        rain_probability=rain,
        sessions=sessions,
        division_tier=context.division_tier,
        season_number=context.season_number,
    )

    label = "Mystery notice" if phase == 0 else f"Weather — phase {phase}"
    return [
        (label, drawing.template_key, _spec_with_faults(build_fill_spec, drawing, context))
    ]
