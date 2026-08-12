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


# ── Asset directories ─────────────────────────────────────────────────────

#: Config column -> (command name, default directory).
ASSET_DIRECTORIES: dict[str, tuple[str, str]] = {
    "track_image_directory": ("track-image-directory", "resources/tracks"),
    "team_image_directory": ("team-image-directory", "resources/teams"),
    "flag_directory": ("flag-directory", "resources/flags"),
    "driver_image_directory": ("driver-image-directory", "resources/drivers"),
    "marker_directory": ("marker-directory", "resources/markers"),
    "weather_icon_directory": ("weather-icon-directory", "resources/weather"),
    "tyre_directory": ("tyre-directory", "resources/tyres"),
}

ASSET_LABELS: dict[str, str] = {
    "track_image_directory": "Circuit images",
    "team_image_directory": "Team badges",
    "flag_directory": "Nationality flags",
    "driver_image_directory": "Driver portraits",
    "marker_directory": "Position-change markers",
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
NOTICE_WRAP_TRUNCATED = "WRAP_TRUNCATED"
NOTICE_INLINE_SIZE_TRUNCATED = "INLINE_SIZE_TRUNCATED"
NOTICE_ASSET_FALLBACK_USED = "ASSET_FALLBACK_USED"
NOTICE_OPTIONAL_FIELD_EMPTIED = "OPTIONAL_FIELD_EMPTIED"


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

#: The reserved filename standing in for a datum with no file of its own
#: (Constitution XIV.13). One per asset directory; optional.
FALLBACK_ASSET_NAME = "fallback.svg"
