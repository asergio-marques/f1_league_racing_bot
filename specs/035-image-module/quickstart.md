# Quickstart: Image Module — Initial Setup & Configuration

**Feature**: 035-image-module

How to validate the increment end to end. Written to be run against a bot on a scratch Discord
server; the unit and integration tests below run without one.

---

## Prerequisites

**Python dependencies** — already declared, nothing to add:

```
pip install -r requirements.txt
```

`lxml>=5.0` and `fonttools>=4.50` are already in `requirements.txt`. If they are absent from your
environment, that install is the whole fix.

**Inkscape** — the one prerequisite no package declaration installs (spec Dependencies, FR-008):

```
inkscape --version
```

If it is not on PATH but is installed, set `INKSCAPE` to the executable:

```
# Windows, typical install
$env:INKSCAPE = "C:\Program Files\Inkscape\bin\inkscape.exe"
```

On the development host Inkscape is installed but its PATH entry is broken, so `INKSCAPE` or the
absolute-path probe is what will find it.

**Deliberately test the absent case too** — unset `INKSCAPE` and rename the binary temporarily to
confirm SC-007: the reason is stated at season review, at config view and at test render, and no
render is attempted.

**Templates and assets** — the defaults point at `resources/templates` and seven directories under
`resources/`. Place the fifteen templates and the asset sets there, or point the configuration
elsewhere with the directory commands. Provisioning them is the operator's job, not the module's
(research R9); the module's part is to report accurately on what it finds, which Scenario 3 below
exercises directly.

---

## Automated tests

```
pytest tests/unit/test_image_config_service.py tests/unit/test_image_validity_layers.py \
       tests/unit/test_svg_fill.py tests/unit/test_colour.py tests/unit/test_font_metrics.py \
       tests/integration/test_image_module_flow.py
```

Expected: all pass, no bot and no Discord connection required. `pytest.ini` already sets
`asyncio_mode = auto` and `pythonpath = src`.

The four invariant tests in `test_image_validity_layers.py` are the ones that matter most — they are
what stops a later session from breaking the extension point. See
[contracts/validity-layers.md](./contracts/validity-layers.md).

---

## Scenario 1 — Enable, and the module lifecycle (US1)

```
/module enable images
```

**Expect**: enabled; an `image_config` row created with every default; eight
`image_aspect_toggles` rows at `enabled = 0`; a confirmation in the calculation log channel. If
Inkscape is absent, a fatal notice naming it — but the module still enables.

```
/images config view
```

**Expect**: template directory `resources/templates`, all fifteen filenames at their packaged
defaults, all seven asset directories at theirs, time zone `UTC`, 24-hour clock, weekday-carrying
date format, fastest-lap colour `#A020F0`, all eight aspects ❌ disabled — and a statement of the
depth templates were checked to (Layer 1).

```
/module disable images
/images config view
```

**Expect**: the view command rejected, naming `/module enable images` (FR-005).

---

## Scenario 2 — Retention across a disable (FR-004a, SC-008)

The scenario that proves the Principle X.6 exception.

```
/images config template-directory  directory: resources/my_templates
/images config fastest-lap-colour  colour: #00FF88
/images config toggle              aspect: standings
/module disable images
/module enable images
/images config view
```

**Expect**: every value exactly as set — directory `resources/my_templates`, colour `#00FF88`,
standings still toggled on. Nothing reset to a default. This is the assertion that distinguishes
this module from every other one on the bot.

---

## Scenario 3 — Template location and validity (US2, US3)

```
/images config template-directory  directory: resources/nonexistent
/images config view
```

**Expect**: all fifteen invalid, reported **once** against the missing directory rather than as
fifteen file-not-found lines (spec Edge Cases).

```
/images config template-directory  directory: resources/templates
/images config weather-p3-sprint-template  filename: not_a_real_file.svg
/images config toggle  aspect: weather
/images config view
```

**Expect**: the weather aspect ⚠️ **enabled but invalid**, naming *phase 3, sprint variant*
specifically — with phases 1 and 2, the non-sprint variants and the mystery notice all reported
valid (US3 scenario 3, FR-032). A report that says only "weather is invalid" fails this scenario.

Repeat for the qualifying/race pair and the drivers/constructors pair (US3 scenarios 4 and 5).

Then, to distinguish Layer 1's failure modes (US2 scenario 4):

```
# point a template filename at a file that exists but is not valid SVG
/images config calendar-template  filename: some_text_file.svg
/images config view
```

**Expect**: a reason that reads differently from "file not found".

---

## Scenario 4 — Path containment (FR-011)

```
/images config template-directory  directory: ../../etc
```

**Expect**: rejected at the command with a clear error; the stored value unchanged. Verify with
`/images config view` that the previous directory survived.

---

## Scenario 5 — Toggles are inert (FR-017a, SC-004)

The scenario that proves this increment changed no output.

```
/images config toggle  aspect: standings
```

**Expect**: confirmation that says plainly it is not yet in effect. Then trigger a standings post
by whatever route the results module already uses.

**Expect**: exactly the text posted before the toggle was set. Byte-identical. No image, no
partial image, no skipped post.

---

## Scenario 6 — Presentation preferences (US6)

```
/images config fastest-lap-colour  colour: A020F0      # no hash
/images config fastest-lap-colour  colour: #A020F      # five digits
/images config fastest-lap-colour  colour: #GGGGGG     # not hex
```

**Expect**: each rejected with the required form stated; stored value unchanged (FR-025).

```
/images config fastest-lap-colour  colour: #A020F0
```

**Expect**: stored, with the measured contrast ratio against the race results template's
fastest-lap background reported.

```
/images config fastest-lap-colour  colour: #3A3A3A     # low contrast on a dark plate
```

**Expect**: **stored anyway**, ratio reported, legibility warning issued (FR-026). The warning does
not block the value — it is the league's to choose.

```
/images config results-race-template  filename: missing.svg
/images config fastest-lap-colour     colour: #FF0000
```

**Expect**: stored, and a statement that the contrast could not be measured **and why** — not an
omitted or guessed ratio (FR-027).

```
/images config time-zone  zone: Europe/Lis…
```

**Expect**: autocomplete offers `Europe/Lisbon`. An unrecognised zone is rejected.

---

## Scenario 7 — Test render (US7)

```
/images test  kind: standings
```

**Expect**: the command defers, then returns two attached PNGs — drivers and constructors — visible
only to you (FR-037, FR-040), with any notices listed alongside. On a host without Inter, expect
`FONT_SUBSTITUTED` notices; those are correct, not failures.

```
/images test  kind: weather-p2
```

**Expect**: two variants, sprint and non-sprint.

```
/images test  kind: calendar
```

**Expect**: one PNG, cropped to the sample division's round count.

**On a server with no season configured at all**, every kind must still render (FR-036, SC-005). If
any test render touches live data, it fails this scenario.

```
# with the converter unavailable
/images test  kind: calendar
```

**Expect**: rejected at once naming the converter, no render attempted (US7 scenario 4).

---

## Scenario 8 — Season review addendum (FR-033, FR-034)

```
/season setup      # …through to a pending configuration
/season review
```

**Expect**: an image section in the existing `**Modules**` block, carrying the same per-aspect and
per-template summary `/images config view` renders. The two are built from the same
`AspectStatus` list, so any divergence between them is a defect.

```
/module disable images
/season review
```

**Expect**: image module reported disabled, configuration detail omitted.

---

## Verifying a render by eye

Per project practice, inspect the **PNG**, not the SVG — a browser hides bugs the rasteriser
exposes, and the rasteriser is what a league will see. In particular, a field that overflows its
`inline-size` or wraps past its floor may look acceptable in a browser and be visibly clipped in the
rasterised output.

---

## Definition of done for this increment

- All six test files pass.
- Scenarios 1–8 behave as described.
- **Scenario 5 is the gate**: with every aspect toggled on, the bot's posted output is
  indistinguishable from a server that never enabled the module (SC-004).
- `/images config view` never overstates depth (SC-009).
- Disabling and re-enabling loses nothing (SC-008).
