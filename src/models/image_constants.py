"""Static maps for the Image Module.

These are code constants rather than tables because no command addresses an individual
template's toggle: the toggle surface is the eight aspects, and the fifteen templates are
an implementation detail of what each aspect draws.
"""
from __future__ import annotations

# ── Templates ─────────────────────────────────────────────────────────────

#: Config column -> default filename. The fifteen templates, in report order.
TEMPLATE_COLUMNS: dict[str, str] = {
    "calendar_template": "calendar_template.svg",
    "lineup_template": "lineup_template.svg",
    "results_qualifying_template": "results_qualifying_template.svg",
    "results_race_template": "results_race_template.svg",
    "standings_drivers_template": "standings_drivers_template.svg",
    "standings_constructors_template": "standings_constructors_template.svg",
    "attendance_template": "attendance_template.svg",
    "rsvp_template": "rsvp_template.svg",
    "weather_p1_template": "weather_p1_template.svg",
    "weather_p2_template": "weather_p2_template.svg",
    "weather_p3_template": "weather_p3_template.svg",
    "weather_p2_sprint_template": "weather_p2_sprint_template.svg",
    "weather_p3_sprint_template": "weather_p3_sprint_template.svg",
    "weather_mystery_template": "weather_mystery_template.svg",
    "verdicts_template": "verdicts_template.svg",
}

#: Config column -> the subcommand name under ``/images template`` that sets it.
#:
#: These sit under ``/images template`` rather than ``/images config`` because Discord
#: allows at most 25 subcommands per group and forbids a third nesting level; ``config``
#: would otherwise carry 29. The redundant ``-template`` suffix is dropped, so the spec's
#: ``images config calendar-template`` is delivered as ``/images template calendar``.
TEMPLATE_COMMAND_NAMES: dict[str, str] = {
    "calendar_template": "calendar",
    "lineup_template": "lineup",
    "results_qualifying_template": "results-qualifying",
    "results_race_template": "results-race",
    "standings_drivers_template": "standings-drivers",
    "standings_constructors_template": "standings-constructors",
    "attendance_template": "attendance",
    "rsvp_template": "rsvp",
    "weather_p1_template": "weather-p1",
    "weather_p2_template": "weather-p2",
    "weather_p3_template": "weather-p3",
    "weather_p2_sprint_template": "weather-p2-sprint",
    "weather_p3_sprint_template": "weather-p3-sprint",
    "weather_mystery_template": "weather-mystery",
    "verdicts_template": "verdicts",
}

#: Config column -> human label used in a validity report. Must name the individual
#: template, never the group (FR-032): the weather entries carry phase *and* variant.
TEMPLATE_LABELS: dict[str, str] = {
    "calendar_template": "Calendar",
    "lineup_template": "Lineup",
    "results_qualifying_template": "Results — qualifying",
    "results_race_template": "Results — race",
    "standings_drivers_template": "Standings — drivers",
    "standings_constructors_template": "Standings — constructors",
    "attendance_template": "Attendance sheet",
    "rsvp_template": "Check-in call",
    "weather_p1_template": "Weather — phase 1",
    "weather_p2_template": "Weather — phase 2 (non-sprint)",
    "weather_p3_template": "Weather — phase 3 (non-sprint)",
    "weather_p2_sprint_template": "Weather — phase 2 (sprint)",
    "weather_p3_sprint_template": "Weather — phase 3 (sprint)",
    "weather_mystery_template": "Weather — mystery notice",
    "verdicts_template": "Verdicts",
}


# ── Aspects ───────────────────────────────────────────────────────────────

#: The eight toggleable aspects, in report order.
ASPECTS: tuple[str, ...] = (
    "calendar",
    "lineup",
    "results",
    "standings",
    "attendance",
    "rsvp",
    "weather",
    "verdicts",
)

#: Aspect -> the templates backing it. 1 + 1 + 2 + 2 + 1 + 1 + 6 + 1 = 15.
ASPECT_TEMPLATES: dict[str, tuple[str, ...]] = {
    "calendar": ("calendar_template",),
    "lineup": ("lineup_template",),
    "results": ("results_qualifying_template", "results_race_template"),
    "standings": ("standings_drivers_template", "standings_constructors_template"),
    "attendance": ("attendance_template",),
    "rsvp": ("rsvp_template",),
    "weather": (
        "weather_p1_template",
        "weather_p2_template",
        "weather_p3_template",
        "weather_p2_sprint_template",
        "weather_p3_sprint_template",
        "weather_mystery_template",
    ),
    "verdicts": ("verdicts_template",),
}

#: Aspect -> the optional module whose output it replaces, or None for aspects drawn
#: from the foundational concepts alone. Drives the third state of FR-031.
ASPECT_SOURCE_MODULE: dict[str, str | None] = {
    "calendar": None,
    "lineup": None,
    "results": "results",
    "standings": "results",
    "attendance": "attendance",
    "rsvp": "attendance",
    "weather": "weather",
    "verdicts": "results",
}

ASPECT_LABELS: dict[str, str] = {
    "calendar": "Calendar",
    "lineup": "Lineup",
    "results": "Session results",
    "standings": "Standings",
    "attendance": "Attendance sheet",
    "rsvp": "Check-in call",
    "weather": "Weather forecasts",
    "verdicts": "Verdicts",
}

#: The aspects whose toggle changes what the bot posts. An aspect is live once its
#: source module calls the image module on the occasions it posts — in practice, once
#: it has an `image_<aspect>_post` module wired into it, the calendar excepted, whose
#: branch lives inline in `calendar_post_service`.
#:
#: All eight are live. The set is kept rather than dissolved into `ASPECTS` because it
#: is what the toggle reply and the `/images config view` footer read to decide whether
#: to warn that an aspect records intent alone: with the set full, `PENDING_POSTING_ASPECTS`
#: is empty and neither surface says anything, without either being edited. A ninth aspect
#: added ahead of its posting path gets that warning back by being left out of here.
LIVE_POSTING_ASPECTS: frozenset[str] = frozenset(
    {
        "calendar",
        "lineup",
        "results",
        "standings",
        "attendance",
        "rsvp",
        "weather",
        "verdicts",
    }
)

#: The aspects a toggle records but no posting path acts upon yet, in report order.
PENDING_POSTING_ASPECTS: tuple[str, ...] = tuple(
    aspect for aspect in ASPECTS if aspect not in LIVE_POSTING_ASPECTS
)


# ── Asset directories ─────────────────────────────────────────────────────

#: Config column -> (command name, default directory, packaged directory).
#:
#: The default is where a league's *own* artwork goes and is where the class looks first;
#: the packaged directory is what ships with the bot and is the second fallback tier. They
#: are deliberately different paths: `resources/league/` is gitignored and survives an
#: update, so a league drops a file in and it is drawn with no configuration command at
#: all, while `resources/defaults/` is replaced wholesale by an update and answers every
#: miss. Both are read from this one table so they cannot drift apart (047 FR-038).
ASSET_DIRECTORIES: dict[str, tuple[str, str, str]] = {
    "track_image_directory": (
        "track-image-directory", "resources/league/tracks", "resources/defaults/tracks",
    ),
    "team_image_directory": (
        "team-image-directory", "resources/league/teams", "resources/defaults/teams",
    ),
    "flag_directory": (
        "flag-directory", "resources/league/flags", "resources/defaults/flags",
    ),
    "driver_image_directory": (
        "driver-image-directory", "resources/league/drivers", "resources/defaults/drivers",
    ),
    "marker_directory": (
        "marker-directory", "resources/league/markers", "resources/defaults/markers",
    ),
    "weather_icon_directory": (
        "weather-icon-directory", "resources/league/weather", "resources/defaults/weather",
    ),
    "tyre_directory": (
        "tyre-directory", "resources/league/tyres", "resources/defaults/tyres",
    ),
}

#: Asset class -> the configuration column naming its directory. Kept beside
#: :data:`ASSET_DIRECTORIES` so the packaged path and the default configured path are read
#: from one table and cannot drift apart (047 FR-038).
ASSET_CLASS_TO_COLUMN: dict[str, str] = {
    "track": "track_image_directory",
    "team": "team_image_directory",
    "flag": "flag_directory",
    "driver": "driver_image_directory",
    "marker": "marker_directory",
    "weather": "weather_icon_directory",
    "tyre": "tyre_directory",
}


def packaged_directory_for(asset_class: str) -> str | None:
    """The directory shipped with the module for *asset_class*, or None if unknown.

    The **second tier** of asset resolution (Constitution XIV.13, 047 FR-040). Where a
    league's configured directory holds neither the datum's file nor a ``fallback.svg``,
    this directory is consulted for a fallback — and for a fallback only. The datum's own
    file is never sought here: a league that did not supply an image must not silently be
    given one that happens to ship under the same name.

    This is **never** the path the default configured directory names. The default points
    at ``resources/league/``, which a league fills with its own artwork and which an update
    to the bot cannot overwrite; this points at ``resources/defaults/``, which the bot ships
    and an update replaces wholesale. The two tiers are therefore always distinct, and it is
    the packaged tier that makes a fresh clone draw every graphic before a league has
    supplied anything at all.
    """
    column = ASSET_CLASS_TO_COLUMN.get(asset_class)
    if column is None:
        return None
    entry = ASSET_DIRECTORIES.get(column)
    return entry[2] if entry else None


ASSET_LABELS: dict[str, str] = {
    "track_image_directory": "Circuit images",
    "team_image_directory": "Team badges",
    "flag_directory": "Nationality flags",
    "driver_image_directory": "Driver portraits",
    "marker_directory": "Markers and result marks",
    "weather_icon_directory": "Weather icons",
    "tyre_directory": "Tyre compounds",
}


# ── Test kinds ────────────────────────────────────────────────────────────

#: The eleven `/images test` kinds -> the templates each renders.
#: The eight aspects with weather split into its four phases: 8 - 1 + 4 = 11.
#: Four kinds cover more than one template and must return every variant (FR-040).
TEST_KIND_TEMPLATES: dict[str, tuple[str, ...]] = {
    "calendar": ("calendar_template",),
    "lineup": ("lineup_template",),
    "results": ("results_qualifying_template", "results_race_template"),
    "standings": ("standings_drivers_template", "standings_constructors_template"),
    "attendance": ("attendance_template",),
    "rsvp": ("rsvp_template",),
    "weather-p1": ("weather_p1_template",),
    "weather-p2": ("weather_p2_template", "weather_p2_sprint_template"),
    "weather-p3": ("weather_p3_template", "weather_p3_sprint_template"),
    "weather-mystery": ("weather_mystery_template",),
    "verdicts": ("verdicts_template",),
}


# ── Presentation ──────────────────────────────────────────────────────────

#: Date-format token -> (strftime pattern, worked example shown in the choice list).
#: At least one carries the weekday, and it is the default: a season run on the same
#: weekday every second week makes the weekday the part of a date a driver reads for.
DATE_FORMATS: dict[str, tuple[str, str]] = {
    "DDD_DD_MON_YYYY": ("%a %d %b %Y", "Sun 14 Jun 2026"),
    "DD_MM_YYYY": ("%d/%m/%Y", "14/06/2026"),
    "MM_DD_YYYY": ("%m/%d/%Y", "06/14/2026"),
    "YYYY_MM_DD": ("%Y-%m-%d", "2026-06-14"),
    "DD_MON_YYYY": ("%d %b %Y", "14 Jun 2026"),
}

TIME_FORMATS: dict[str, str] = {
    "12H": "%I:%M %p",
    "24H": "%H:%M",
}

#: The element id in the race results template whose fill is the background behind the
#: fastest-lap field. FR-026a: the contrast check locates it by this single documented id.
FASTEST_LAP_BACKGROUND_ID = "fastest_lap_background"

#: Notice kinds (Constitution XIV.4).
NOTICE_FONT_SUBSTITUTED = "FONT_SUBSTITUTED"

#: A field was set below **half** the size its template declared in order to hold its value
#: (XIV.5, v7.0.0). The floor stops nothing — the reduction continues past it until the text
#: fits — so this reports a field under pressure, never a field drawn short.
#:
#: It replaces `WRAP_TRUNCATED` and `INLINE_SIZE_TRUNCATED`, both withdrawn with the cut they
#: reported: text is no longer truncated or ellipsised anywhere in the module.
NOTICE_FIELD_REDUCED = "FIELD_REDUCED"

NOTICE_ASSET_FALLBACK_USED = "ASSET_FALLBACK_USED"
NOTICE_OPTIONAL_FIELD_EMPTIED = "OPTIONAL_FIELD_EMPTIED"

#: The last member a template declares should put its crop point at the declared canvas
#: height, so a division holding as many members as the template declares is drawn whole.
#: Where it does not, the cut is made there anyway and this is raised (037, FR-026).
NOTICE_CROP_POINT_OFF_CANVAS = "CROP_POINT_OFF_CANVAS"


# ── Asset classes ─────────────────────────────────────────────────────────

#: Asset class -> the ImageConfig column naming its directory.
#:
#: A catalogue names the *class* an image field draws from; the class names the column.
#: The indirection exists so a catalogue never mentions a configuration column, and a
#: column can be renamed without touching fifteen catalogues.
ASSET_CLASS_DIRECTORIES: dict[str, str] = {
    "track": "track_image_directory",
    "team": "team_image_directory",
    "flag": "flag_directory",
    "driver": "driver_image_directory",
    "marker": "marker_directory",
    "weather": "weather_icon_directory",
    "tyre": "tyre_directory",
}

#: Asset classes whose data are a closed set the module itself defines, not values a league
#: supplies (Constitution XIV.13). A league did not choose this vocabulary and cannot be
#: incomplete against it, so the packaged directory of one of these classes is searched for
#: the datum's own file — not only its `fallback.svg` — whether or not the league has pointed
#: the class at a directory of its own. Every other class is never searched this way.
CLOSED_SET_ASSET_CLASSES: frozenset[str] = frozenset({"marker", "weather", "tyre"})

#: Slugs that are the module's own vocabulary wherever they appear, in a class whose data
#: are otherwise the league's own. `mystery` stands for a round concealed until it is run
#: and `other` for a driver who stated no nationality in particular. Neither is a country,
#: a circuit, a team or anything else a league named.
CLOSED_SET_ASSET_DATA: frozenset[str] = frozenset({"mystery", "other"})


def is_closed_set_datum(asset_class: str, slug: str) -> bool:
    """Whether this datum is the module's own vocabulary rather than a league's value.

    The single question Constitution XIV.13 asks before searching the packaged directory
    for a datum's own file, rather than only for its `fallback.svg`. A league did not
    choose this vocabulary and cannot be incomplete against it, so a directory of its own
    that is missing one of these draws the bot's own correct file in preference to a
    generic placeholder.

    It has two ways of being true because the classes differ in kind, not because there
    are two rules. `marker`, `weather` and `tyre` are closed all the way down -- every
    datum they can be handed comes from the module -- so the class settles it, and naming
    the class is also what keeps those three vocabularies from being restated here where
    they would drift from the modules that define them: the position-change data in
    `standings_service`, the weathers in `math_utils`, the five compounds in
    `utils.tyre_compound`. `flag` and `track` name countries and circuits a league chose,
    and reserve two names within that, so there the datum settles it: asserting the whole
    class would hand a league our file for a country it simply had not drawn yet.

    A tyre compound joined the first kind at Constitution v7.8.0, having sat in the second
    by an accident of which list it was written into. It is what the game offers -- five
    and no sixth -- so a league can no more be incomplete against it than against the three
    directions a standing position can move, and leaving it there put a grey placeholder
    and a notice on every qualifying row of a league that had drawn no tyre artwork.
    """
    return asset_class in CLOSED_SET_ASSET_CLASSES or slug in CLOSED_SET_ASSET_DATA

#: The reserved filename standing in for a datum with no file of its own
#: (Constitution XIV.13). One per asset directory; optional.
FALLBACK_ASSET_NAME = "fallback.svg"

#: The two fallbacks of the `marker` class, which serves data of more than one shape
#: (v7.5.0).
#:
#: One fallback per directory answers a class whose data are all one shape. `marker` is not:
#: it draws the 64 x 64 position-change arrows, the standings result marks stretched into a
#: 52 x 22 or 52 x 18 cell, and the attendance marks stretched into a 36 x 24 one. A single
#: file cannot stand in for all three, and the one a league supplies would be drawn for
#: whichever of them it happened not to be drawn for.
#:
#: This matters because the **configured** directory's fallback is consulted before the
#: packaged tier's copy of the datum's own file. A league that drops one `fallback.svg` into
#: its marker folder would otherwise have that file answer for a missing arrow and a missing
#: plate alike, in preference to the correct packaged file for either.
POSITION_CHANGE_FALLBACK_ASSET_NAME = "position_change_fallback.svg"
MARK_FALLBACK_ASSET_NAME = "standings_attendance_fallback.svg"

#: The `marker` data that are position changes rather than marks. Restated here rather than
#: imported from `standings_service`, which would invert the dependency -- a model reaching
#: into a service -- and is held to that module's own constants by a test so it cannot drift.
#:
#: The set is stated for the arrows rather than for the marks because the arrows are the
#: closed half: three directions a position can move, and no fourth. The marks are added to
#: whenever a graphic finds something new worth calling out, and an unrecognised datum is
#: better routed to the mark fallback -- which stretches, and so cannot be the wrong shape --
#: than to an arrow's.
POSITION_CHANGE_DATA: frozenset[str] = frozenset(
    {"position_change_gained", "position_change_lost", "position_change_none"}
)


def fallback_names_for(asset_class: str, slug: str) -> tuple[str, ...]:
    """The fallback filenames to try for this datum, most specific first.

    Every class ends at :data:`FALLBACK_ASSET_NAME`, so a league that supplied only the
    generic fallback still gets it and no class behaves differently from before. `marker`
    alone puts a shape-appropriate name in front of it.
    """
    if asset_class != "marker":
        return (FALLBACK_ASSET_NAME,)
    specific = (
        POSITION_CHANGE_FALLBACK_ASSET_NAME
        if slug in POSITION_CHANGE_DATA
        else MARK_FALLBACK_ASSET_NAME
    )
    return (specific, FALLBACK_ASSET_NAME)

#: The reserved filename standing in for a round whose track -- and with it its
#: country -- is concealed until it is run (Constitution XIV.13). Reserved in the
#: track image directory and the flag directory alike.
MYSTERY_ASSET_NAME = "mystery.svg"

#: The reserved filename standing in for a driver who stated no nationality in particular
#: (Constitution XIV.13). Reserved in the flag directory. `Other` is a *value* a driver
#: chose, not an absence: a driver recording no nationality at all is drawn with no flag
#: field, where one recording `Other` is drawn with this.
OTHER_ASSET_NAME = "other.svg"

#: Asset class -> the aspect ratio (width / height) every slot of that class must
#: declare, on every template of every image type (Constitution XIV.6).
#:
#: The *ratio* binds, not the pixel size: a template may draw a flag slot at any
#: dimensions so long as they are 3:2. One class carries one aspect because a league
#: authors one file per datum and the generator never pads -- a class serving slots
#: of two aspects would letterbox that one file wherever it did not match, and no
#: artwork the league could supply would answer it.
#:
#: **Every class declares one, and a slot opts out for itself** (v7.5.0). A slot carrying
#: `preserveAspectRatio="none"` stretches to the box it is given, so there is no ratio for
#: it to be letterboxed against and `aspect_faults_of` passes over it whatever its shape.
#: The exemption belongs to the slot because that is where the fact lives: `marker` draws
#: both the square position-change arrows and the standings and attendance marks, whose
#: cells are 52 x 22, 52 x 18 and 36 x 24 -- three shapes no single class ratio could serve,
#: and which the slots themselves already declare they stretch to fill. An earlier version
#: made whole classes exempt instead, which cost a class of its own for the marks and left
#: the arrows beside them unchecked the moment the two were put in one folder.
#:
#: Two classes need not match each other, and flag and track deliberately do not.
#: The constraint is *within* a class, never *across* two.
#:
#: XIV.6 leaves these numbers out of governance on purpose ("The aspect a class
#: carries is not fixed by this Principle"), so this table is the authority and
#: ``resources/README.md`` is its league-facing statement.
ASSET_CLASS_ASPECTS: dict[str, float] = {
    "track": 1.0,          # 120 x 120 -- circuit maps
    "team": 1.0,           # 120 x 120
    "flag": 1.5,           # 120 x  80 -- country flags, 3:2
    "driver": 1.0,         # 120 x 120
    "marker": 1.0,         #  64 x  64
    "weather": 1.0,        #  64 x  64
    "tyre": 1.0,           #  64 x  64
}

#: Relative tolerance for the aspect comparison (044, contracts/asset-aspect.md).
#:
#: Required rather than convenient. Template geometry is authored in Inkscape and
#: carries floating-point values -- 120.00001 / 80 is not exactly 1.5 in binary
#: floating point -- so an exact comparison would reject every template a human
#: drew. 1% admits honest authoring and still catches a square slot given a 3:2
#: flag, which is a 50% error. No plausible authoring mistake lands inside it.
ASSET_ASPECT_TOLERANCE = 0.01


# ─────────────────────────────────────────────────────────────────────────
# The `/images test` kinds (046)
# ─────────────────────────────────────────────────────────────────────────

#: One entry per `/images test` subcommand, describing what that kind needs and what it
#: draws. It replaces the ad-hoc `require_rounds` / `require_teams` / `require_mystery`
#: flags each call site passed at 045, so that three separate rules — which parameters a
#: command requires, whether a bare server may draw it, and what format its round must
#: carry — are read from one table rather than restated eleven times.
#:
#: ``draws_roster`` is the load-bearing column and is settled by reading each builder, not
#: by reading a specification. Two entries mislead anyone who assumes otherwise:
#:
#:   * ``rsvp`` draws division, round, format, track, schedule and deadline, and touches
#:     neither ``context.teams`` nor ``context.drivers`` — so it draws on a server that has
#:     configured no team at all.
#:   * ``verdict`` opens on ``context.drivers[0]`` and reads that driver's ``team_name``
#:     for the badge — so it does not.
#:
#: ``format_demanded`` is ``None`` where the kind accepts any round, ``False`` where a
#: mystery round must be refused, and ``True`` where anything but one must be. The same
#: tri-state ``require_mystery`` already uses.
PREVIEW_KINDS: dict[str, dict[str, object]] = {
    "calendar":        {"needs_round": False, "draws_roster": False, "format_demanded": None},
    "lineup":          {"needs_round": False, "draws_roster": True,  "format_demanded": None},
    "results":         {"needs_round": True,  "draws_roster": True,  "format_demanded": None},
    "standings":       {"needs_round": True,  "draws_roster": True,  "format_demanded": None},
    "attendance":      {"needs_round": True,  "draws_roster": True,  "format_demanded": None},
    "verdict":         {"needs_round": True,  "draws_roster": True,  "format_demanded": None},
    "rsvp":            {"needs_round": True,  "draws_roster": False, "format_demanded": None},
    "weather-p1":      {"needs_round": True,  "draws_roster": False, "format_demanded": False},
    "weather-p2":      {"needs_round": True,  "draws_roster": False, "format_demanded": False},
    "weather-p3":      {"needs_round": True,  "draws_roster": False, "format_demanded": False},
    "weather-mystery": {"needs_round": True,  "draws_roster": False, "format_demanded": True},
}

#: The kinds a division's team list is required for. Derived rather than written out, so
#: the two can never disagree.
ROSTER_DRAWING_KINDS: frozenset[str] = frozenset(
    kind for kind, spec in PREVIEW_KINDS.items() if spec["draws_roster"]
)
