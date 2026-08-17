# Contract: Country Flag Resolution

**Feature**: `044-track-imagery-split`
**Implemented in**: `src/utils/country_data.py` (new), the five drawing services that emit a flag datum
**Governs**: Constitution XIV.13 — "The flag class is keyed by the country, for every field that draws from it"

This contract is **general**, not a round's own. It changes what every flag on every graphic
resolves by, drivers included. The round's flag is simply the first field to arrive that is not a
driver's.

---

## The one directory

There is **one** flag directory, configured by `images config flag-directory`, defaulting to
`resources/flags`. It serves a driver's flag and a round's alike. There is no second directory and
no per-purpose split.

Every file in it is named for a **country**, normalised by the module's single normalisation rule
(XIV.13): trim → lowercase → strip diacritics → runs of non-alphanumerics to one underscore → drop
leading and trailing underscores → `.svg`.

## What resolves what

| Field stands for | Datum | Source |
|---|---|---|
| A driver | The country of their recorded nationality | `NATIONALITY_COUNTRIES[nationality]` |
| A round | The country of its circuit | `Track.country` |
| A round of the mystery format | The literal `Mystery` | The image type's mystery constant |

**A driver's flag never resolves from the nationality adjective.** `British` is mapped to
`United Kingdom` first, and `united_kingdom.svg` is the file. This is the change; everything else
in this contract follows from it.

### The country vocabulary is the seed's

`NATIONALITY_COUNTRIES` yields exactly the spellings `tracks.country` holds — `United Kingdom`,
`United States of America` — and **not** the shorter forms the prose examples used. See
[research R-001](../research.md). A driver and a circuit of the same country MUST produce the same
slug; a test asserts it (data-model V-4).

### `Other`

`Other`, recorded for a driver who stated no nationality, is present in the map and maps to
`Other`. It resolves `other.svg`, exactly as today. It is not a country and the map invents none
for it.

### Several circuits, one country

Miami, Las Vegas and the Circuit of the Americas all carry `United States of America` and therefore
all draw one `united_states_of_america.svg`. This is intended, ruled explicitly, and no attempt is
made to tell them apart.

## The map's obligations

`NATIONALITY_COUNTRIES` MUST be **total** over the canonical nationalities the signup wizard
admits. A nationality absent from it is a **defect of the module**, caught by a test over the map
itself (data-model V-1), and MUST NOT be answered by a fallback drawn at render.

This is the one place the increment refuses a graceful degradation on purpose: a missing map entry
is a bug that would otherwise hide behind a plausible-looking placeholder flag on every graphic
that driver appears on.

## Resolution outcomes — unchanged

XIV.13's three outcomes hold exactly as they stand, per class:

| Outcome | Result |
|---|---|
| `<country>.svg` found | Placed on the field |
| Not found, directory holds `fallback.svg` | Fallback placed; **notice** naming the field and the country |
| Not found, no fallback | **Problem.** Render abandoned |

**No cross-class fallback.** A flag that does not resolve NEVER draws a circuit map, and a circuit
map that does not resolve NEVER draws a flag. There is no fourth outcome.

## Interaction with the nationality toggle

A league that has switched nationality collection off draws **no driver flags at all** and is told
nothing — no notice, no problem. That suppression is unchanged and is per field, as XIV.4 has it.

**It does not touch a round's flag.** A round's country comes from the track registry, not from a
driver's signup, so a league collecting no nationalities still gets fully flagged round headings.
This is worth stating because the two now share a directory and the temptation to suppress both
together is real.

## Reserved names

| Name | Meaning |
|---|---|
| `fallback.svg` | Any country with no file of its own |
| `mystery.svg` | **New.** A round whose country is concealed with its track |

Both are reserved names of the flag directory: a league may replace the artwork, never the
filename. `mystery.svg` is authored at the flag class's 3:2 and carries no text.
