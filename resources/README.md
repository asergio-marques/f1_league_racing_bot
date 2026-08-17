# resources/

What the image module reads from disk. Every path here is a **default** — each is
configurable per server, and a league that keeps its files elsewhere points the bot at them
with `/images config <directory>`.

| Directory | Default for | Aspect |
|---|---|---|
| `templates/` | `/images config template-directory` | declared by each template |
| `tracks/` | `/images config track-image-directory` | 120 × 120 |
| `teams/` | `/images config team-image-directory` | 120 × 120 |
| `drivers/` | `/images config driver-image-directory` | 120 × 120 |
| `flags/` | `/images config flag-directory` | 120 × 80 |
| `markers/` | `/images config marker-directory` | 64 × 64 |
| `weather/` | `/images config weather-icon-directory` | 64 × 64 |
| `tyres/` | `/images config tyre-directory` | 64 × 64 |

`tracks/` holds **circuit maps**, and only the calendar and check-in graphics draw from it.
Every other graphic pictures a round with its country flag from `flags/`, which serves a
driver's flag and a round's alike.

**The aspect in this table is per class and is enforced.** Every slot of a class carries it
on every template — flags 3:2, everything else 1:1 — and the bot refuses a template whose
slot is the wrong shape for its class. You author one file per value, so a class serving
two shapes would letterbox that file somewhere with no artwork able to fix it. The two
classes need not match each other, and flags and maps deliberately do not.

## What ships here, and what does not

**Shipped:**

- the fifteen default templates in `templates/`, one per image type;
- one `fallback.svg` in each asset directory;
- `tracks/mystery.svg` and `flags/mystery.svg`, drawn for a round whose track — and with it
  its country — is concealed until it is run;
- `markers/gained.svg`, `markers/lost.svg` and `markers/unchanged.svg`, the three directions
  a standing position can move;
- the eight `weather/` icons — `sunny.svg`, `mixed.svg` and `rain.svg` for the type of
  weather drawn for a session, and `clear.svg`, `light_cloud.svg`, `overcast.svg`,
  `wet.svg` and `very_wet.svg` for a concrete weather within one.

**Not shipped:** the assets for any particular circuit, team, driver, country or tyre.
Those are a league's own, and the module exists to let each league bring its own design
language rather than inherit one.

**Why the markers and the weather icons are different.** Those two sets are not a league's
values at all — they are the bot's own vocabulary, fixed and closed, and no league chose
them. A league cannot have an incomplete set of something it did not define, so the module
ships every file rather than leaving each directory to fall back on every render. Replace
them freely; keep the filenames, the aspect and the no-text rule below.

So a fresh clone draws every graphic, and draws each of them entirely out of fallbacks. That
is the intended starting point: the module works from the first render, and a league
replaces the placeholders class by class as it makes its own artwork, seeing its own files
appear as it goes.

The templates are a starting point in the same sense. Restyle them, replace them, or point
the bot at your own with `/images template <kind>` — the contract they satisfy is the only
thing that matters, not the design.

## How a file is looked up

The bot takes the value it wants to draw, normalises it, and appends `.svg`:

> trim → lowercase → strip accents → every run of non-alphanumeric characters becomes a
> single underscore → drop leading and trailing underscores

So `Red Bull Racing` is looked up as `teams/red_bull_racing.svg`, and `São Paulo` as
`tracks/sao_paulo.svg`. Driver portraits are the one exception: they are keyed on the
Discord user ID, so a portrait does not go missing when a driver changes their nickname.

**Flags are keyed on a country**, never on a nationality. A driver who signed up as `British`
draws `flags/united_kingdom.svg` — the bot maps the nationality to its country first — and a
round draws the flag of the country its circuit sits in, so every circuit in one country
shares one file. Spell the country as the bot's track list spells it: `United Kingdom`, not
`Great Britain`; `United States of America`, not `United States`. `Other`, recorded for a
driver who gave no nationality, is not a country and keeps `flags/other.svg`.

## `fallback.svg`

When the file for a specific value is not there, the bot draws that directory's
`fallback.svg` instead and logs which value needed it.

**When there is no fallback either, the graphic is not produced at all.** The bot will not
quietly post a card with a hole in it. This is why one ships in every directory: it is the
single file that keeps an incomplete asset set from stopping your images.

Replace them freely — they are deliberately plain grey so they read as placeholders against
any league's palette. Two things to keep if you do:

- **Author at the aspect in the table above.** The generator never pads. An asset of another
  shape is letterboxed, and the converter smears its edge pixels across the band rather than
  leaving it transparent.
- **Use no text.** Text in an asset is subject to font substitution, so a fallback carrying
  any would render differently from one machine to the next. The shipped ones are pure
  vector for that reason.

## `mystery.svg`

A Mystery round records no track, so there is no name to look one up by — and no country
either, the one being read from the other. The bot draws `tracks/mystery.svg` where a circuit
map belongs and `flags/mystery.svg` where a round's flag belongs, and writes "Mystery GP"
where the grand prix name belongs — a Mystery round appears like any other round, marked as
such, and never leaves a hole.

It is a reserved name in the same way `fallback.svg` is, in **both** directories: replace the
artwork freely, but keep the filename, the aspect, and the no-text rule above.

## Authoring your own assets

Plain SVG, no `clipPath`, no gradients, no filters. Authored at exactly the aspect of the
slot, padded with transparent margins where the subject does not fill it.

The full authoring contract — template field naming, removable groups, text bounds — is in
the main [README](../README.md) under **Image Module → Templates: what the bot expects**.
