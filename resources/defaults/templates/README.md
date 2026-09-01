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

## The font is declared once, and inherits

Every packaged file states its stack once, as a `font-family` on the root `<svg>`:

```
font-family="Inter, 'Segoe UI', 'DejaVu Sans', Arial, sans-serif"
```

A field carrying no `font-family` of its own takes the nearest one above it — the root, or any
group in between — and a field's own declaration overrides what it would inherit, whether that
declaration is a presentation attribute, a CSS rule or an inline style. Restyling a whole file is
therefore one edit, and setting one section apart is a `font-family` on the group holding it.

**The stack is the whole of the fallback.** The first family the machine carries is the one used,
and the bot measures against the same family the rasteriser draws with — which is the point of
declaring it where both can read it. A generic name closing the stack (`sans-serif`) leaves the
last word to the host, and the host answers differently on Windows and on Linux, so a file that
must come out identical everywhere names a face installed everywhere.

`font-weight` and `font-style` inherit the same way, and measurement follows both. The box
properties above do **not** inherit — `inline-size`, `max-lines` and `shape-inside` each bound one
field, and a group declaring one would otherwise re-bound every field beneath it.

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

Both standings files stand **three** `<image>` slots beneath each race cell, before the
`<text>` so they paint under it, and all three share one box:

    row_<x>_round_<z>_<sprint|feature>_race_background      the plate
    row_<x>_round_<z>_<sprint|feature>_race_fastest_lap     top-left corner
    row_<x>_round_<z>_<sprint|feature>_qualifying_mark      top-right corner

and the constructors file the same under `..._driver_<w>_...`. They draw the **marker**
class — the same folder as the three position-change markers and the two attendance marks — so
what a mark looks like is artwork in that folder and not a colour written into the template.

The **datum** names the session: a race cell asks for `race_p1`, a qualifying one for
`qualifying_p1`, because one folder holds both sets. The **kind** does not — the stylesheet
selectors below stay `.highlight_p1_text`, so a template written before the files were renamed
still colours its numerals correctly.

They share a box deliberately: **where** a mark sits is the artwork's business. The plate fills
its box, the packaged `race_fastest_lap.svg` draws a triangle into the top-left of its own, and
`qualifying_p1.svg` and friends into the top-right. Redraw a file and its mark moves, with no
template to edit. Giving each a corner-sized slot instead would freeze that arrangement into
several thousand elements a league could not restyle.

The **qualifying mark hangs off the qualifying session's name though it is drawn over the race
cell**, because it marks the qualifying result. The raised qualifying figure shares one text
chunk with the race result and has no position of its own, so nothing can be drawn behind it —
a corner of the race cell is what can be, and that is what made qualifying markable at all. The
top-right is the corner nearest that figure.

All three are authored with **no href**, which is why a cell earning no highlight draws nothing
and is never removed: an `<image>` carrying no reference draws nothing, and removing the slot
instead would put thousands of identifiers into every fill spec.

All three carry `preserveAspectRatio="none"`, which is what makes the file stretch to the slot
rather than being letterboxed inside it — the drivers chip is 52 × 22 and the constructors chip
52 × 18, shapes fixed by two different row bands, and the position-change markers beside them in
the same folder are square. Draw artwork that survives being stretched.

`marker` is the one class the shape check ignores entirely, precisely because it holds both
kinds at once. It is also the **only** class whose slots may carry that declaration: a slot of
any other class declaring it is refused outright, whatever its shape. Were it merely ignored, a
template declaring it of every driver portrait slot would sail past the shape check and draw
every face in the league squashed.

What remains in each file's `<style>` is the **ink**: `.highlight_p1_text` and friends colour
the result itself, and `.highlight_p1_sup_text` the raised qualifying figure, which sits on the
*plate* and would otherwise keep its grey `.sup`. A file cannot colour text drawn over it, so
these cannot move to the artwork.

**Only the background takes ink; neither corner mark does.** Both occupy a corner while the
numerals sit inboard over the plate, so the plate is the only thing they are read against. This
was learnt twice: the fastest lap kept taking the ink after it became a triangle and painted
white numerals onto a gold plate, and the qualifying mark did the same onto the bare row band.
A template may still name `.highlight_fastest_lap_text`; the render ignores it.

## The mark on an attendance total

The attendance sheet stands one `<image>` slot beneath each driver's total, before the `<text>`
so it paints under it:

    row_<x>_points_background

It draws the **marker** class as the standings chips do, out of the same folder, and follows the
same three rules: authored with no href, so a driver earning no mark draws nothing and the slot
is never removed; `preserveAspectRatio="none"`, so the file stretches to the 36 × 24 box rather
than being letterboxed into it; and the artwork decides what the mark looks like, the template
deciding only where it sits.

Two files answer it — `attendance_limit_near.svg` for a driver within two points of the point
limit and `attendance_limit_reached.svg` for one who has reached it. The packaged pair is amber
and red at the **same weight**, told apart by hue rather than by strength: a warning drawn as a
fainter red reads as a weaker version of the sanction, which is not what it means. No stylesheet
rule goes with them: the total is white and stays white, both marks being washes that fade to
nothing rather than plates.

## The point limit block

The sheet declares **one** limit block, not one per functionality:

    limit_group      the block, removed whole when no limit is set
    limit_label      "RESERVE AT" or "SACKED AT"
    limit_value      the number

The label is a field rather than fixed chrome because a league can only have one limit — the
auto-reserve and auto-sack commands refuse each other — so a file declaring a block for each
would always draw one and delete the other, leaving a hole beside the survivor. The unit sits in
its own `<tspan>` inside the value's element, as the heading composes SEASON and its number.

The class is **closed**, so deleting a file from a league's folder does not suppress that mark —
the packaged file is drawn in its place, which is the rule that makes a fresh clone work. A
league suppresses one mark by supplying a fully transparent SVG under that name, and the whole
feature by deleting the slots from its template.

## The grid is 54 px per column

Both files run their season grid from x=360 at a round pitch of 110 — two 54 px session columns
and a 2 px gutter — which puts the canvas at 1728 wide.

The 54 is not arbitrary. A result cell may be asked to carry an outcome literal with another
raised beside it, `DSQ` over `DSQ`, which measures about 46 px in the font the Raspberry Pi and
CI resolve. The columns were 32 and 24, so that pair overran into the next round — invisibly,
because SVG text simply overruns and reports nothing. Narrow them again and it will.

`tools/relayout_standings_grid.py` computes the whole grid from those constants and is
idempotent; `tests/unit/test_image_standings_geometry.py` asserts the shipped files against the
same numbers from the other side, including a rasterised check that no text reaches a divider.
