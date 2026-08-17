# Quickstart: Validating the Track Imagery Split

**Feature**: `044-track-imagery-split`
**Date**: 2026-08-17

How to prove this increment works. Everything here runs offline — **no step needs a live Discord
bot, a gateway connection or a real server.** Full system testing is done by hand outside this
repository and is out of scope.

---

## Prerequisites

- Python 3.13 with the repo's requirements installed.
- Inkscape, for the PNG verification steps. Its PATH entry is unreliable; the code probes the
  conventional install locations and the `INKSCAPE` environment variable overrides.
- A clean working tree on `044-track-imagery-split`.

---

## 1. Baseline

```
pytest tests/ -q
```

Record the result before touching anything. It was **1995 passed, 1 skipped** at the point this
plan was written. Compare against this after every task; the suite is expected to pass in full, and
any failure is a real one until confirmed on a clean tree.

---

## 2. The map is total and agrees with the seed

The three tests that matter most, because they catch the fault class that R-001 uncovered:

```
pytest tests/unit/test_country_data.py -q
```

Proves:

- every canonical nationality has a country (**V-1**, FR-003);
- every country the map yields is a key of `NATIONALITY_LOOKUP` (**V-2**);
- **every distinct `tracks.country` is reachable from the map (V-3)** — this is the one that stops a
  driver and a circuit of the same country drawing two different files;
- one country yields one slug whichever path asked for it (**V-4**).

V-3 is worth running on its own the first time. If it fails, R-001's decision has been implemented
inconsistently and no amount of template work will make the flags agree.

---

## 3. Flags resolve by country

```
pytest tests/unit/test_image_lineup_service.py tests/unit/test_image_standings_service.py -q
```

Expect: a `British` driver resolves `united_kingdom.svg`; a driver recorded `Other` resolves
`other.svg`; an unknown country draws `fallback.svg` with exactly one notice naming the field and
the country; a league with nationality collection off draws no driver flag and raises nothing.

---

## 4. A round is a flag everywhere it is a heading

```
pytest tests/unit/test_image_standings_service.py tests/unit/test_image_attendance_service.py tests/unit/test_image_weather_service.py -q
```

Expect: each round heading emits a `flag`-class datum carrying the round's country; **no
`track`-class datum is emitted by any of these three services**; and a league with nationality
collection off still gets fully flagged round headings, that switch governing drivers alone.

---

## 5. Templates are refused when a slot is the wrong shape

```
pytest tests/unit/test_image_validity_service.py -q
```

Expect: a square flag slot is refused with a message naming field, class, expected aspect and found
aspect; a 3:2 track slot is refused; a slot at 120.00001 × 80 **passes**, the tolerance case; and a
standings, attendance or weather template declaring a track-map field at all is refused.

---

## 6. Render and verify as PNG — not as SVG in a browser

This is the step the rest cannot substitute for. **Verify as PNG.** The rasteriser exposes what a
browser hides — flowed text, substituted fonts, unresolvable image hrefs — and a slot of the wrong
shape is visible in the raster and nowhere else.

Render each of the seven re-authored templates through the module's own test-render path and open
the **PNG**:

| Template | Look for |
|---|---|
| `calendar_template` | Every round draws a flag **and** a map, neither letterboxed. A mystery round draws each class's `mystery.svg`. |
| `rsvp_template` | Both `track_flag` and `track_image` drawn. |
| `standings_drivers_template` | Round headings are flags at 3:2, **not squashed**. No circuit map anywhere. |
| `standings_constructors_template` | As above. |
| `attendance_template` | As above. |
| `weather_p1/p2/p2_sprint/p3/p3_sprint/mystery` | `track_flag` drawn at 3:2. No circuit map. |

**What a failed re-geometry looks like**: the flag fills a square slot with its edge pixels smeared
into bands above and below — the converter carries the outermost pixels outward rather than leaving
the band transparent. It is unmistakable in a PNG and invisible in the SVG source.

---

## 7. A clean clone draws everything

The proof of US5. With no league artwork placed and no configuration set:

```
pytest tests/unit/test_packaged_resources.py -q
```

Expect: `resources/flags/` holds `fallback.svg` **and** `mystery.svg`; the new file is 3:2 and
carries no `<text>` element; `resources/tracks/` is unchanged with both of its reserved files; and
each of the seven packaged templates passes the Layer 2 aspect check for every image field it
declares.

Then render the calendar and check-in test images and confirm both classes appear, drawn entirely
out of packaged placeholders.

---

## 8. Full suite, and the docs

```
pytest tests/ -q
```

Compare against step 1. Then, before reporting the increment complete, invoke the **`close-out`**
skill: this increment changes what a league sees, so `README.md` and `resources/README.md` both
need bringing into step — the country-keyed naming rule, the rename callout for anyone with an
adjective-keyed folder, the two mystery files, and which graphics draw which class.

`docs/wip-specs/image_module_specification.md` already carries the rules and needs only the country
examples corrected per R-001.

---

## What is deliberately not here

- **Anything requiring a running bot.** No "invoke the command in Discord", no posting to a real
  channel, no manual check against a live server.
- **`poc/`.** Out of scope and untouched.
- **Migration steps.** There is no migration; the bot is not in production and no league's data
  needs converting. A league with an adjective-keyed flag folder renames files, which is a release
  note rather than a data task.
