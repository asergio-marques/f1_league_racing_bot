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
    "division_logo_directory": (
        "division-logo-directory",
        "resources/league/division-logos",
        "resources/defaults/division-logos",
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
    "division_logo": "division_logo_directory",
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
    "division_logo_directory": "Division logos",
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

#: A **packaged** file was drawn into a slot of a shape it was not authored at (2026-09-01).
#:
#: A league chooses the shape of each class for itself, but the artwork the bot ships is
#: drawn at one fixed shape per class -- see `PACKAGED_ASSET_ASPECTS` -- and answers for any
#: datum the league has not drawn. Re-shape a class and every one of those is stretched.
#:
#: Its own kind rather than a rider on `ASSET_FALLBACK_USED`, which fires constantly and
#: benignly wherever a league has simply not drawn a country yet. This one says something a
#: league can act on and that will not go away on its own: draw your own file for this class,
#: at the shape your templates use.
NOTICE_PACKAGED_ASSET_OFF_SHAPE = "PACKAGED_ASSET_OFF_SHAPE"

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
    "division_logo": "division_logo_directory",
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


#: Asset classes whose fallback stands for "nothing is drawn here" rather than for artwork a
#: league was expected to supply, so drawing it is reported to nobody (decided 2026-09-02).
#:
#: Every other class answers a datum the bot went looking for on the league's behalf -- a
#: country, a circuit, a compound -- and a fallback drawn there is a gap in the league's asset
#: set, worth naming once so it can be closed. `division_logo` is not that. It is decoration a
#: league opts into by declaring the slot in a template of its own, and the state of having
#: drawn no logo is the ordinary one rather than an omission. Reporting it would put a notice
#: on every graphic the league posts, for an element they never asked for, and the only way to
#: silence it would be to drop a blank `fallback.svg` of their own into the folder -- which is
#: the file we already ship.
#:
#: **The cost is real and is accepted.** A misnamed file, or a division renamed after its logo
#: was drawn, is silent: the artwork simply does not appear and nothing anywhere says why. That
#: is the price of the class being optional, and it is why the shipped fallback must be
#: genuinely empty -- a grey placeholder drawn silently would be worse than either.
#:
#: Consumed by `svg_fill`, which suppresses both `NOTICE_ASSET_FALLBACK_USED` and
#: `NOTICE_PACKAGED_ASSET_OFF_SHAPE` for a class named here. The second follows from the first:
#: a file with nothing drawn in it cannot be the wrong shape for a slot.
BLANK_FALLBACK_ASSET_CLASSES: frozenset[str] = frozenset({"division_logo"})

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

#: Asset classes whose slots must agree with one another on the shape they draw
#: (Constitution XIV.6, relaxed 2026-09-01).
#:
#: **The reference is the template, never this module.** Every non-stretching slot of one of
#: these classes must declare the ratio its siblings declare *on the template that holds
#: them*, and any ratio will do. A league drawing its flags 2:1 throughout is drawing them
#: correctly; one drawing twenty-three at 2:1 and the twenty-fourth square is not.
#:
#: The rule exists because a league authors **one file per datum** -- a single
#: `united_kingdom.svg` for every flag slot in the bot -- and the generator never pads. That
#: one file is letterboxed wherever a slot disagrees with the others, and no artwork the
#: league could supply would answer it. What that argument requires is *agreement*. It never
#: required a particular number, though this module asserted one anyway until 2026-09-01:
#: flags at 3:2 and everything else at 1:1, refused on any template, including one a league
#: had authored itself. That was stricter than its own reason, and is withdrawn. The numbers
#: survive at `PACKAGED_ASSET_ASPECTS`, describing our own artwork rather than governing a
#: league's.
#:
#: `marker` is absent, and unchecked altogether. It is the one class that genuinely draws
#: several shapes at once -- the 64 x 64 position-change arrows beside the standings and
#: attendance marks, whose cells are 52 x 22, 52 x 18 and 36 x 24 -- and the same fact that
#: gives it two fallbacks (see `fallback_names_for`) denies it a single shape to agree on.
#: The cost is accepted: an arrow slot drawn at the wrong shape letterboxes with nothing
#: said.
#:
#: **What is deliberately not checked** (decided 2026-09-01): agreement *between* templates.
#: `flag` is drawn by fourteen of the fifteen and `team` by seven, all from the same one file
#: per datum, so a league shaping flags 3:2 on the calendar and 2:1 on the standings has that
#: file letterboxed on one of them and is not told. Checking it would refuse the first file
#: of any re-shaping -- the other thirteen would still disagree with it -- and a league could
#: never move a class off the shape it started on. The gap is real and is documented to
#: leagues rather than closed.
#:
#: **`division_logo` is outside this set, and outside `STRETCHABLE_ASSET_CLASSES` too**
#: (2026-09-02). It is the first class governed by neither, which the partition test in
#: `test_asset_resolver` was written to anticipate. Agreement between slots is still a rule
#: about shape: it says one file is drawn into all of them, so they had better be the shape
#: that file is. A division logo is not one file -- it is one file *per division* -- and a
#: league drawing its crest large in a header and small in a corner of the same template is
#: doing nothing wrong, because each division supplies artwork for both. Nothing here has a
#: shape to hold it to. Stretching is still refused: `stretch_faults_of` faults any slot
#: declaring `preserveAspectRatio="none"` outside `STRETCHABLE_ASSET_CLASSES`, so the logo
#: letterboxes like everything else and only the box it letterboxes into is free.
RATIO_CONSISTENT_ASSET_CLASSES: frozenset[str] = frozenset(
    {"track", "team", "flag", "driver", "weather", "tyre"}
)

#: The only class whose slots may declare `preserveAspectRatio="none"` (v7.5.0, narrowed
#: 2026-09-01, made a check in its own right 2026-09-01).
#:
#: Such a slot stretches to fill the box the template gives it rather than being letterboxed
#: inside it. `marker` needs that: the standings result marks and the attendance ones are
#: drawn to the room their cell gives them, and those cells are three different shapes.
#:
#: **No other class may claim it, and that is enforced directly.** It was formerly enforced
#: only as a side effect of the shape comparison -- a driver slot that stretched but happened
#: to be square passed -- and once the shape is taken from the template rather than from a
#: table, the side effect disappears entirely: a lineup whose portrait slots *all* stretch
#: would agree with itself, be passed over by the agreement check, and draw every face in the
#: league squashed to the shape of the box with nothing said. So a stretching slot outside
#: this set is a fault of its own, reported before the shapes are compared at all.
#:
#: This is what still governs `division_logo`, which is held to no shape at all otherwise
#: (2026-09-02). Being free of a fixed shape is not licence to squash: a league's crest
#: letterboxes into whatever box its template gives it, and a slot claiming otherwise is a
#: fault here as it would be for a driver portrait.
STRETCHABLE_ASSET_CLASSES: frozenset[str] = frozenset({"marker"})

#: Asset class -> the aspect ratio the bot's **own** packaged artwork is drawn at.
#:
#: Not a rule any template must obey. `RATIO_CONSISTENT_ASSET_CLASSES` carries what is
#: enforced, and it names no numbers at all. This table does two other jobs.
#:
#: It is what `resources/defaults/` is authored and verified against, so the fifteen shipped
#: templates and the artwork that fills them stay coherent with one another now that nothing
#: in production forces it. And it is what a slot is compared with when a **packaged** file is
#: drawn into it: a league that re-shapes a class still gets our 3:2 flags for every country
#: it has not drawn itself, stretched, and `NOTICE_PACKAGED_ASSET_OFF_SHAPE` says so.
#: **`division_logo` is deliberately absent.** What we ship for it is a file with nothing drawn
#: in it, which has no shape to author against and cannot be letterboxed wrongly into anything;
#: recording 1:1 for it would be a fiction, and the two jobs above would both act on that
#: fiction. `_packaged_shape_notice` reads this table with `.get()` and says nothing for a class
#: it does not find, which is the right answer here rather than an oversight (2026-09-02).
PACKAGED_ASSET_ASPECTS: dict[str, float] = {
    "track": 1.0,          # 120 x 120 -- circuit maps
    "team": 1.0,           # 120 x 120
    "flag": 1.5,           # 120 x  80 -- country flags, 3:2
    "driver": 1.0,         # 120 x 120
    "marker": 1.0,         #  64 x  64 -- the arrows; the marks stretch and have no shape
    "weather": 1.0,        #  64 x  64
    "tyre": 1.0,           #  64 x  64
}

#: Relative tolerance for every aspect comparison (044, contracts/asset-aspect.md).
#:
#: Required rather than convenient. Template geometry is authored in Inkscape and
#: carries floating-point values -- 120.00001 / 80 is not exactly 1.5 in binary
#: floating point -- so an exact comparison would reject every template a human
#: drew. 1% admits honest authoring and still catches a square slot among 3:2 ones,
#: which is a 50% error. No plausible authoring mistake lands inside it.
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
