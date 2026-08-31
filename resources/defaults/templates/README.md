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
alone, and so do the three kinds of verdict.

## The verdict template wraps its prose

`verdicts_template.svg` is the only shipped template with **wrapped** fields, and the only
one drawing text a person wrote rather than a value the bot computed. `description` and
`justification` each declare `shape-inside` naming a rectangle below them — `description_shape`
and `justification_shape` — and it is those rectangles, not the text elements, that decide how
much prose fits.

- **The rectangle is the field.** Its width is what the text wraps against; its height divided
  by the field's `line-height` is how many lines it may occupy. It carries no fill and no
  stroke and is never drawn: it is there to be moved, not seen. Narrowing one is how you take
  a measure down to something easier on the eye; the cost is fewer characters before a long
  verdict starts shrinking.
- **`line-height` is required and is not guessed.** A wrapped field without one is refused the
  moment you name the file. The bot will not substitute a leading, because the number it chose
  would silently decide how much of a steward's prose your league sees.
- **Keep the height a whole multiple of the line height.** The packaged file uses 156 ÷ 26 = six
  lines. Changing either number changes the budget.
- Long prose is set down half a pixel at a time until it fits. Passing below half the declared
  size raises a note naming the field, but **stops nothing** — the text is never cut and no
  ellipsis is ever drawn. Because the leading falls with the size, a field set smaller holds
  *more* lines rather than the same number spread wider.

`penalty` may be wrapped the same way. It is a single unbounded line in the packaged file,
which relies on sanction descriptions staying as short as they are today.

## Every other field declares its room in CSS

Prose aside, a field states the room it has with two properties on the text element itself —
no rectangle, no extra node:

- **`inline-size`** is the width it may use.
- **`max-lines`** is how many lines it may take. Leave it out and the field takes one.

A value too wide is broken at word boundaries up to that budget, and then set down half a
pixel at a time until it fits. **Nothing is ever cut.** A circuit whose name will not fit is
drawn small, not shortened to something that names no circuit.

The lines are centred on the `y` you drew the field at, so a field that takes one line sits
exactly where you put it and one that takes two grows half a line either side. That is what
lets you bound a field without moving anything else on the drawing.

Two rules to keep in mind when raising a budget above one:

- **`line-height` is required** for a field that may wrap, exactly as it is for prose, and the
  bot refuses the file without it rather than guessing a leading.
- **Leave the vertical room yourself.** The bot measures a field against its own box and knows
  nothing of what you drew above or below it. `max-lines:2` on a field with room for one turns
  a sideways overlap into a downward one. The packaged weather and check-in files moved their
  round label up and their subtitle row down to win that room; the calendar grew its cards from
  88 px to 136 px, and its row pitch from 104 px to 152 px. It also widened its cards from 528 px
  to 676 px to carry a circuit map and a flag standing the full height of the text between them,
  so the canvas it declares went from 1200 × 876 to 1496 × 1164.

**A box must also clear whatever is drawn beside it**, which is a width, not a height. In the
packaged files the grand prix name stops short of the flag slot, the circuit stops short of the
country, and a weather title stops short of the rain-probability figure and the artwork plate on
its right — which is why the same `.track` class carries a different `inline-size` from one
weather file to the next. Widen one of these and it will run under the picture next to it.

## The constructors grid names no driver

`standings_constructors_template.svg` draws two results per round for each of a team's cars —
sprint and feature — and **no driver name beside them**. Twelve rounds of that grid leave a name
about 70 px, which is not a width a name can be read at whatever size it is set in; the cars of a
row are told apart by the car number in the classification on the left instead. Dropping the
column took the file from 2000 px wide to 1128 px, which is the greater part of why it is now
legible.

The field itself has not gone away: `row_<x>_round_<z>_driver_<w>_name` is optional, exactly as
the lineup's reserve slots are, so a league that wants it may draw it in its own file and widen
the round pitch to suit. The packaged file simply declines it.

## The highlight chips on a standings grid

Both standings files stand a pair of transparent rects beneath each **race** cell, before the
`<text>` so they paint under it:

    row_<x>_round_<z>_<sprint|feature>_race_background
    row_<x>_round_<z>_<sprint|feature>_race_fastest_lap

and the constructors file the same under `..._driver_<w>_...`. They are recoloured — a podium
place, a points finish, the fastest lap — and are **never removed**, which is why they are
authored `fill="none"`: a cell that earns no highlight simply leaves them invisible, and a render
that removed them instead would put a thousand identifiers into every fill spec.

The colours are `.highlight_*` rules in each file's own `<style>`, with the gradients they name
in `<defs>`. Nothing outside the file decides them. A rule the file does not name is never
painted, and a cell without the rects is never highlighted, so the feature is opted into twice
over and deleting either half turns it off cleanly.

`.highlight_sprint_p1` is consulted before `.highlight_p1`; the packaged files use that to pitch
the sprint chips a shade darker than the feature ones. `_text` sets the colour of the result,
`_sup_text` that of the raised qualifying figure — which sits on the chip and would otherwise keep
its grey `.sup`. That recolour is contrast alone and says nothing about the qualifying result.

**The two qualifying cells decline a background**, though the catalogue admits the fields. The
raised figure is a `tspan` of the same text element as the race result and carries no `x` of its
own, so the pair centre as one run and neither has a fixed position for a chip to sit behind. A
file that gives qualifying a column of its own may declare
`row_<x>_round_<z>_<sprint|feature>_qualifying_background` and will be honoured.

The chips are regenerated by `tools/add_standings_cell_highlights.py`, which reads each cell's own
`x` and `y` rather than recomputing the column pitch, and skips a cell that already has them.

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
