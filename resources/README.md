# resources/

What the image module reads from disk.

**Everything the bot ships sits under `defaults/`.** That is the split this directory is
arranged around: `resources/defaults/` is ours and is replaced wholesale when you update the
bot, while the rest of `resources/` is yours to organise as you please. Keeping your own
artwork out of `defaults/` is the point — put it anywhere else and point the bot at it.

Every path below is a **default** — each is configurable per server, and a league that keeps
its files elsewhere points the bot at them with `/images config <directory>`.

| Directory | Default for | Aspect |
|---|---|---|
| `defaults/templates/` | `/images config template-directory` | declared by each template |
| `defaults/tracks/` | `/images config track-image-directory` | 120 × 120 |
| `defaults/teams/` | `/images config team-image-directory` | 120 × 120 |
| `defaults/drivers/` | `/images config driver-image-directory` | 120 × 120 |
| `defaults/flags/` | `/images config flag-directory` | 120 × 80 |
| `defaults/markers/` | `/images config marker-directory` | 64 × 64 |
| `defaults/weather/` | `/images config weather-icon-directory` | 64 × 64 |
| `defaults/tyres/` | `/images config tyre-directory` | 64 × 64 |

`defaults/tracks/` holds **circuit maps**, and only the calendar and check-in graphics draw from it.
Every other graphic pictures a round with its country flag from `defaults/flags/`, which serves a
driver's flag and a round's alike.

**The aspect in this table is per class and is enforced.** Every slot of a class carries it
on every template — flags 3:2, everything else 1:1 — and the bot refuses a template whose
slot is the wrong shape for its class. You author one file per value, so a class serving
two shapes would letterbox that file somewhere with no artwork able to fix it. The two
classes need not match each other, and flags and maps deliberately do not.

## What ships here, and what does not

**Shipped:**

- the fifteen default templates in `defaults/templates/`, one per image type;
- one `fallback.svg` in each asset directory;
- `defaults/tracks/mystery.svg` and `defaults/flags/mystery.svg`, drawn for a round whose
  track — and with it its country — is concealed until it is run;
- `defaults/markers/gained.svg`, `lost.svg` and `unchanged.svg`, the three directions
  a standing position can move;
- the eight `defaults/weather/` icons — `sunny.svg`, `mixed.svg` and `rain.svg` for the type of
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

So `Red Bull Racing` is looked up as `red_bull_racing.svg` in whichever directory the team
badge class points at, and `São Paulo` as `sao_paulo.svg` in the track directory. A name may
begin with a digit: `2Fast Motorsport` is looked up as `2fast_motorsport.svg`. Driver portraits are the one exception: they are keyed on the
Discord user ID, so a portrait does not go missing when a driver changes their nickname.

**Flags are keyed on a country**, never on a nationality. A driver who signed up as `British`
draws `united_kingdom.svg` from the flag directory — the bot maps the nationality to its country first — and a
round draws the flag of the country its circuit sits in, so every circuit in one country
shares one file. Spell the country as the bot's track list spells it: `United Kingdom`, not
`Great Britain`; `United States of America`, not `United States`. `Other`, recorded for a
driver who gave no nationality, is not a country and keeps `other.svg`.

## `fallback.svg`

When the file for a specific value is not there, the bot draws a `fallback.svg` instead and
logs which value needed it. It looks in **two** places, in order:

1. the directory you configured for that class — your own fallback, if you put one there;
2. failing that, `resources/defaults/<class>/fallback.svg`, the one that ships with the bot.

**You therefore no longer need to supply a fallback of your own.** Point the team badge
directory at a folder holding eight of your ten badges and every graphic still draws: the
two teams without a badge get the packaged placeholder and a notice naming them. Put a
`fallback.svg` of your own in that folder only when you want your placeholder rather than
ours.

The packaged directory is consulted for a **fallback and nothing else**. A file sitting in
`defaults/teams/` under the same name as one of your teams is never drawn for you — only
what you supplied, or a placeholder.

**When neither place holds a fallback, the graphic is not produced at all.** The bot will
not quietly post a card with a hole in it. Since one ships in every packaged directory, you
reach this only by pointing a class at a directory of your own *and* removing ours.

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
either, the one being read from the other. The bot draws `mystery.svg` from the track directory where a circuit
map belongs and `mystery.svg` from the flag directory where a round's flag belongs, and writes "Mystery GP"
where the grand prix name belongs — a Mystery round appears like any other round, marked as
such, and never leaves a hole.

It is a reserved name in the same way `fallback.svg` is, in **both** directories: replace the
artwork freely, but keep the filename, the aspect, and the no-text rule above.

## Authoring your own assets

Plain SVG, no `clipPath`, no gradients, no filters. Authored at exactly the aspect of the
slot, padded with transparent margins where the subject does not fill it.

The full authoring contract — template field naming, removable groups, text bounds — is in
the main [README](../README.md) under **Image Module → Templates: what the bot expects**.
