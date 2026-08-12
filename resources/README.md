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

## What ships here, and what does not

**Shipped:** one `fallback.svg` per asset directory, and nothing else.

**Not shipped:** the templates, and the assets for any particular track, team, driver,
nationality, tyre or weather condition. Those are a league's own design language, and the
module exists to let each league bring its own rather than inherit one.

`templates/` is therefore empty on a fresh clone, and the image module will refuse to accept
a template filename until you put a file there. That refusal is the intended behaviour, not
a fault.

## How a file is looked up

The bot takes the value it wants to draw, normalises it, and appends `.svg`:

> trim → lowercase → strip accents → every run of non-alphanumeric characters becomes a
> single underscore → drop leading and trailing underscores

So `Red Bull Racing` is looked up as `teams/red_bull_racing.svg`, and `São Paulo` as
`tracks/sao_paulo.svg`. Driver portraits are the one exception: they are keyed on the
Discord user ID, so a portrait does not go missing when a driver changes their nickname.

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

## Authoring your own assets

Plain SVG, no `clipPath`, no gradients, no filters. Authored at exactly the aspect of the
slot, padded with transparent margins where the subject does not fill it.

The full authoring contract — template field naming, removable groups, text bounds — is in
the main [README](../README.md) under **Image Module → Templates: what the bot expects**.
