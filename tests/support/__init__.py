"""Shared fixtures for the test suite."""

#: Each `/images test` kind -> the templates it renders.
#:
#: Lived in `models/image_constants.py` until 2026-08-18, when the withdrawn
#: `/images test <kind>` command was replaced by eleven subcommands and nothing in `src/`
#: read it any longer. It is still a convenient enumeration for tests that want every kind
#: and its templates, so it is kept here rather than deleted.
#:
#: The eight aspects, with weather split into its four phases: 8 - 1 + 4 = 11. Four kinds
#: cover more than one template.
KIND_TEMPLATES: dict[str, tuple[str, ...]] = {
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
    # Singular, as the command is named.
    "verdict": ("verdicts_template",),
}
