# Quickstart: Validating Template Verification & Graphic Conventions

How to prove this feature works. Automated checks first, then the manual passes that only a human
eye or a live Discord server can settle.

## Prerequisites

- Python environment for the bot, with `pytest` available.
- **Inkscape** on the host, or the `INKSCAPE` environment variable pointing at the executable.
  Its PATH entry is known to be broken on the development box, so set the variable if
  `/images test` reports the converter absent.
- A test Discord server with the bot invited, an active season, and a calculation log channel
  configured.
- The `images` module enabled: `/module enable images`.

## 1. Automated checks

```bash
pytest tests/unit/test_svg_field_resolution.py \
       tests/unit/test_asset_resolver.py \
       tests/unit/test_svg_parse_faults.py \
       tests/unit/test_image_validity_layers.py -v

pytest tests/integration/test_image_module_flow.py -v

pytest tests/ -q          # nothing in 035 regressed
```

Expected: all pass. The engine tests need neither a database nor Discord — field resolution,
normalisation and parse-fault naming are pure.

## 2. Configuration is refused, and nothing is written

The core of the feature (FR-005). Run each and then confirm the stored value is untouched with
`/images config view`.

| Do this | Expect |
|---|---|
| `/images template calendar filename:calendar.txt` | Refused, naming the extension |
| `/images template calendar filename:nope.svg` | Refused, naming the full path searched |
| `/images template calendar filename:broken.svg` (a file whose comment contains `--`) | Refused as an invalid SVG, naming the double hyphen — **not** the parser's own text |
| `/images template calendar filename:good.svg` | Accepted; `/images config view` shows the new name |

After each refusal, `/images config view` must still show the **previous** filename. This is the
single most important observation in this guide: today the value is written before it is checked.

## 3. Season approval blocks on a bad template

1. Configure every template with a sound file; run `/season approve` — the image gate contributes
   no failure.
2. Rename one template file on disk so it is missing; run `/season approve`.
   - Expect: approval refused, and the message names **that one template** and its reason.
3. Break a second template differently (malformed SVG); run `/season approve`.
   - Expect: **both** named individually, with different reasons. Not "2 templates are invalid".
4. `/module disable images`; run `/season approve`.
   - Expect: no image finding at all (FR-009).

## 4. Authoring conventions

Author a template in Inkscape — this is the journey a league manager actually takes.

**Layer label instead of an id.** Draw a text object, put it on a layer, set the layer's label to
`season_name` in the Layers dialog, and do not touch the object's id. Confirm `/images test`
fills it.

**Both present.** Give a *different* node the id `season_name` as well. Confirm the node with the
id is the one filled, and the labelled layer is left alone (FR-020).

**A removable group.** Wrap a field and its label in a group named `sanctions_group`. Drive a
case where the value is absent and confirm the whole group leaves — label included — and that
the canvas is the same height as when it is present (FR-026).

**Without the group.** Remove the wrapper and repeat. Confirm only the field empties, and the
label is left pointing at nothing. That contrast is what the group exists for.

## 5. Asset resolution and the fallback

| Do this | Expect |
|---|---|
| Place `red_bull_racing.svg` in the teams directory, render a graphic showing that team | Badge drawn |
| Rename it to `red-bull-racing.svg` (hyphens) | **Not** found — the slug is underscores |
| Remove the file for one nationality, leave no `fallback.svg` in the flags directory, render with that nationality on an **optional** flag field | Image produced; flag field empty or its group gone; notice in the log |
| Same, but the field is **mandatory** | No image; problem reported |
| Drop a `fallback.svg` into the flags directory and repeat both | Image produced in both cases; fallback drawn; notice naming the field **and the nationality** |

## 6. Errors reach the right audience

The observation that cannot be automated. For each case below, watch **every** channel the bot
posts to.

| Case | Expect |
|---|---|
| A command that triggers a failing generation | Command refused; the caller told what is at fault; **nothing** posted anywhere |
| The same fault reached at a scheduled horizon | The traditional text output posted, as it always was |
| A non-fatal condition during a commanded generation | Image posted; condition reported in the log channel **and** alongside the command's output |
| Any of the above | Nothing in a channel drivers read (FR-032) |

The first two are the same defect producing opposite behaviour. If both fall back to text, FR-030
is not implemented.

## 7. Verify on the PNG, not the SVG

Constitution XIV.14. `/images test <kind>` returns PNGs — inspect those. A browser view of the
filled SVG disagrees with the rasteriser on exactly the things worth checking here: flowed text,
substituted fonts, and the crop. A layout that looks right in a browser and wrong in the PNG is
wrong.

## What "done" looks like

- Every refusal in §2 leaves the stored configuration unchanged.
- Every defective template in §3 is named individually.
- A layer label works in place of an id, and loses to one when both exist.
- A `_group` takes its chrome with it; without one, the chrome is stranded.
- Underscore slugs resolve; hyphens do not; `fallback.svg` rescues both classifications.
- Commanded and scheduled postings behave **differently** on the same fault.
- No error text ever appears in a driver-read channel.
