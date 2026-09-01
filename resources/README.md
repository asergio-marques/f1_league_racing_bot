# resources/

What the image module reads from disk.

**Everything the bot ships sits under `defaults/`. Everything of yours goes under
`league/`.** That is the split this directory is arranged around:

| | |
|---|---|
| `defaults/` | Ours. Replaced wholesale when you update the bot — do not edit it |
| `league/` | Yours. One folder per class, ready to fill. Never committed, never touched by an update |

`league/` starts empty, with a folder per class mirroring `defaults/`, and **the bot already
looks there.** Every asset class points at its `league/` folder out of the box; `defaults/` is
the second place the bot looks, automatically, whenever your folder has nothing for a value.

The **Looks in** column is where the bot searches unless you tell it otherwise. Each is
configurable per server with `/images config <directory>`, and any path inside the project
root is accepted — but most leagues never need to run one.

| Class | Looks in | Falls back to | Set it with | Aspect |
|---|---|---|---|---|
| Templates | `defaults/templates/` | — nothing | `/images config template-directory` | declared by each template |
| Circuit maps | `league/tracks/` | `defaults/tracks/` | `/images config track-image-directory` | 120 × 120 |
| Team badges | `league/teams/` | `defaults/teams/` | `/images config team-image-directory` | 120 × 120 |
| Driver portraits | `league/drivers/` | `defaults/drivers/` | `/images config driver-image-directory` | 120 × 120 |
| Country flags | `league/flags/` | `defaults/flags/` | `/images config flag-directory` | 120 × 80 |
| Markers and marks | `league/markers/` | `defaults/markers/` | `/images config marker-directory` | 64 × 64 for the movement markers; the marks **stretch** — see below |
| Weather icons | `league/weather/` | `defaults/weather/` | `/images config weather-icon-directory` | 64 × 64 |
| Tyre compounds | `league/tyres/` | `defaults/tyres/` | `/images config tyre-directory` | 64 × 64 |

**Templates are the exception in the table.** They have nothing to fall back to — the folder
you configure is the only place a template is searched — so their folder is still
`defaults/templates/`, and `/images config template-directory` **refuses** a folder that does
not hold all fifteen, valid. Put the files in place first, then point the bot at it.

## Using `league/`

**Drop a file in and it is drawn.** There is nothing to configure:

```
resources/league/teams/red_bull_racing.svg
```

**Mixing is the ordinary case, and it is now the default.** Fill `teams` and `drivers` with
your own artwork and leave `markers` and `weather` empty — they ship complete under
`defaults/` and are the module's own vocabulary rather than anything you chose, so the bot
draws its own icons for them until you supply better.

**Nothing in `league/` is committed.** It is listed in `.gitignore` — bar the `.gitkeep`
markers that keep the folders in place — so your artwork never appears in a diff and pulling
an update to the bot can never conflict with it. **That also means git is not backing it up:
keep your source files somewhere of your own.**

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
- one `fallback.svg` in each asset directory, except `markers/`, which ships two — see below.
  The tyre one is a special case worth knowing: since every compound now ships and a
  submission cannot record anything else, it stands for **no compound recorded** and nothing
  else, so it is drawn as an empty dashed ring carrying no letter;
- `defaults/tracks/mystery.svg` and `defaults/flags/mystery.svg`, drawn for a round whose
  track — and with it its country — is concealed until it is run;
- `defaults/flags/other.svg`, drawn for a driver who stated no nationality in particular;
- `defaults/markers/position_change_gained.svg`, `position_change_lost.svg` and
  `position_change_none.svg`, the three directions a standing position can move;
- the eight `defaults/weather/` icons — `sunny.svg`, `mixed.svg` and `rain.svg` for the type of
  weather drawn for a session, and `clear.svg`, `light_cloud.svg`, `overcast.svg`,
  `wet.svg` and `very_wet.svg` for a concrete weather within one;
- the five `defaults/tyres/` icons — `soft.svg`, `medium.svg`, `hard.svg`,
  `intermediate.svg` and `wet.svg`, the compounds a session can be run on. Each is a
  coloured ring lettered `S`, `M`, `H`, `I` or `W`, with a see-through centre so the card
  shows through. There is no sixth: a qualifying submission naming anything else is refused
  rather than recorded;
- the nine standings marks, in `defaults/markers/` alongside the three above —
  `race_p1.svg`, `race_p2.svg`, `race_p3.svg` and `race_points.svg` for the plate drawn
  beneath a race result, `race_fastest_lap.svg` for the mark drawn in its top-left corner, and
  `qualifying_p1.svg` through `qualifying_points.svg` for the mark drawn in its top-right,
  which stands for where the driver qualified;
- the two attendance marks, also in `defaults/markers/` — `attendance_limit_near.svg`, an amber
  wash drawn beneath the total of a driver within two points of the limit you set, and
  `attendance_limit_reached.svg`, a red one drawn beneath the total of one who has reached it.
  The two are the same weight and are told apart by hue.

**Not shipped:** the assets for any particular circuit, team, driver or country.
Those are a league's own, and the module exists to let each league bring its own design
language rather than inherit one.

**Why the markers, the weather icons and the tyres are different.** Those three sets
are not a league's values at all — they are the bot's own vocabulary, fixed and closed, and no league chose
them. A tyre compound is what the game offers, five of them and no sixth, in exactly the
sense that a change of standing position has three directions. A league cannot have an incomplete set of something it did not define, so the module
ships every file rather than leaving each directory to fall back on every render. The same
reasoning covers two individual filenames inside classes that are otherwise a league's own —
`mystery` and `other` — which the bot also named and therefore also supplies. Replace any of
them freely; keep the filenames, the aspect and the no-text rule below.

It follows that **deleting a file of one of these sets does not remove what it draws** — the
packaged file is drawn in its place. To suppress one, supply a fully transparent SVG under its
name. That is chiefly of use for the standings and attendance marks, where a league may want the
podium plates and not the points tint, or the sacking mark and not the warning.

So a fresh clone draws every graphic before a league has made anything at all. The markers,
the weather icons, the tyres, the standings marks and the two reserved flags are the bot's
own artwork, drawn properly; the
circuits, teams, drivers and countries are placeholders, those being a league's to
supply. That is the intended starting point: the module works from the first render, and a
league fills `league/` class by class as it makes its own artwork, seeing its own files
appear as it goes with nothing to configure.

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
`Great Britain`; `United States of America`, not `United States`. `Other`, recorded for a driver who
chose no nationality in particular, is not a country and keeps `other.svg`, which the bot
ships.

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

> **Every file in `markers/` says which of the three it belongs to** — `position_change_*`,
> `race_*`, `qualifying_*`, `attendance_*`. One folder holds three vocabularies, and a bare
> `p1.svg` beside `gained.svg` said which only by omission. The prefix is part of the name the
> bot looks for, so keep it.

### `markers/` has two, because its files are not one shape

One fallback per directory answers a class whose files are all drawn at one size. `markers/`
is not: it holds the 64 × 64 movement markers *and* the standings and attendance marks, which
stretch to whatever cell holds them. A single stand-in cannot be right for both — it would be
drawn as a 64 × 64 arrow squashed into a 52 × 22 result cell, or as a wide plate shrunk into a
square marker slot. So the folder ships two, and the bot picks by which kind of file is
missing:

| Missing file | Stand-in drawn |
|---|---|
| `position_change_gained.svg`, `position_change_lost.svg`, `position_change_none.svg` | `position_change_fallback.svg` |
| Any standings or attendance mark | `standings_attendance_fallback.svg` |

Both follow the two-tier rule above — yours first, the bot's second. A plain `fallback.svg`
you put in `league/markers/` is still honoured, but only after the matching one of the two, so
naming your stand-in for the shape it stands in for is what gets it drawn where you meant.

**This is worth doing rather than optional.** Your own folder's fallback is consulted *before*
the bot's copy of the missing file itself. Drop one unnamed `fallback.svg` into
`league/markers/` and it answers for a missing arrow and a missing podium plate alike — in
preference to the bot's own correct artwork for either.

**What the bot named, the bot supplies** — the one exception, for the reason given above.
Where a value is the bot's own vocabulary rather than one you chose, and your folder holds
neither its file nor a fallback of your own, the bot draws its **own correct image** for that
value — `lost.svg`, `very_wet.svg`, `mystery.svg`, `other.svg`, whichever it is — in
preference to the generic placeholder. This is the one respect in which the packaged
directory is searched for a file under your value's own name and not only for a fallback.

Two kinds of value qualify, which is one rule at two sizes rather than two rules:

- **whole classes** — every marker and every weather icon, since every value those two can
  ever be asked for is the bot's;
- **two reserved filenames** — `mystery` and `other` — inside the flag and circuit-map
  classes, whose other values are the countries and circuits you chose.

**Your own file always wins**, and this only ever fills a gap. It does not extend to the rest
of those classes: a country you have not drawn a flag for gets the placeholder, because that
country is yours to supply and not ours to guess at.

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
artwork freely, but keep the filename, the aspect, and the no-text rule above. `other.svg`, in
the flag directory, is reserved on exactly the same terms.

## Authoring your own assets

Plain SVG, no `clipPath` and no filters. **Gradients are fine** — the rasteriser draws each
asset as its own document, so a gradient in one cannot disturb another even where the two
happen to name it the same thing.

**No text.** Fonts substitute from one machine to the next, so an asset carrying any would
rasterise differently on the machine that drew it and the machine that serves it. Draw
lettering as paths.

Authored at exactly the aspect of the slot, padded with transparent margins where the subject
does not fill it.

**The marks stretch instead**, and the table above says so: the nine standings marks and the
two attendance ones. A result cell is a different shape on the drivers grid than on the
constructors one, the attendance total's box is a third shape again, and no single ratio serves
all three — so those slots draw the file to the room they have rather than fitting it inside.
Author at whatever size suits the artwork — the shipped set is 128 × 56 — and draw something
that survives being squashed a little. A rectangle or a corner shape does; a circle becomes an
ellipse.

It is the **slot** that stretches and not the folder: the movement markers sitting beside them
in `markers/` are still held to 64 × 64, because their slots are square and say nothing about
stretching. Everything else in this file is authored at its class's aspect as above.

The full authoring contract — template field naming, removable groups, text bounds — is in
the main [README](../README.md) under **Image Module → Templates: what the bot expects**.
