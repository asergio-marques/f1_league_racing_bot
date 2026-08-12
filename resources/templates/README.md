# templates/

The fifteen default templates, one per image type. The bot looks for them here unless a
server points it elsewhere with `/images config template-directory`.

| File | Draws |
|---|---|
| `calendar_template.svg` | The season calendar of a division |
| `lineup_template.svg` | The lineup of a division |
| `results_qualifying_template.svg` | A qualifying classification |
| `results_race_template.svg` | A race classification |
| `standings_drivers_template.svg` | The drivers' championship |
| `standings_constructors_template.svg` | The constructors' championship |
| `attendance_template.svg` | The attendance sheet of a division |
| `rsvp_template.svg` | The check-in call for a round |
| `weather_p1_template.svg` | Phase 1 forecast |
| `weather_p2_template.svg` | Phase 2 forecast, non-sprint |
| `weather_p3_template.svg` | Phase 3 forecast, non-sprint |
| `weather_p2_sprint_template.svg` | Phase 2 forecast, sprint round |
| `weather_p3_sprint_template.svg` | Phase 3 forecast, sprint round |
| `weather_mystery_template.svg` | The mystery-round notice |
| `verdicts_template.svg` | A stewards' verdict |

Qualifying and race are separate files, as are the two championships and the attendance
sheet and check-in call — each pair shares too few columns to share one. A sprint and a
feature session of the same kind *do* share a template, told apart by the session-name field
alone.

## These are a starting point, not a fixture

Restyle them freely, or replace them with your own and set the filename per kind with
`/images template <kind>`. Any name ending `.svg` is accepted. What the bot requires is the
contract, not the design: the field names it fills, the groups it removes, and the bounds it
lays text within.

That contract is in the main [README](../../README.md) under **Image Module → Templates:
what the bot expects**. Before editing one, two things are worth knowing:

- **Check your work in the exported PNG, not in a browser.** They disagree on exactly what
  matters here — flowed text, substituted fonts, and the calendar crop. `/images test`
  returns the PNG.
- **The canvas is whatever the template declares.** Change `width` and `height` and the
  output changes with them; nothing assumes a fixed size.

## If a template will not load

`/images template <kind>` refuses a file it cannot use and leaves your previous filename in
force, telling you which of these it was: the name does not end `.svg`, no such file is in
this directory, the file will not parse as SVG, or it is missing a field the image needs.
A malformed file is described plainly — "a comment contains a double hyphen at line 12" —
rather than as a parser error.
