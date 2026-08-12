# templates/

Empty by design. No templates ship with the bot — a league's graphics are its own design
language, and this is where its SVG files go.

Until you put one here, `/images template <kind>` will refuse the filename you give it and
say the file was not found. That refusal is correct: it is the module declining to store a
configuration it cannot use.

The default filenames the bot looks for are `calendar_template.svg`, `lineup_template.svg`,
`results_qualifying_template.svg`, `results_race_template.svg`,
`standings_drivers_template.svg`, `standings_constructors_template.svg`,
`attendance_template.svg`, `rsvp_template.svg`, `weather_p1_template.svg`,
`weather_p2_template.svg`, `weather_p3_template.svg`, `weather_p2_sprint_template.svg`,
`weather_p3_sprint_template.svg`, `weather_mystery_template.svg` and
`verdicts_template.svg` — but any name ending `.svg` is accepted, set per kind.

What a template must contain is in the main [README](../../README.md) under
**Image Module → Templates: what the bot expects**.
