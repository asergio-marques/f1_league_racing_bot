# Contract: The Round Imagery Catalogue

**Feature**: `044-track-imagery-split`
**Implemented in**: `src/models/image_catalogues.py`, and the five drawing services that emit round imagery
**Governs**: Constitution XIV.10 (catalogue as code constant), XIV.11 (ids name their class), XIV.13 (which types may draw a map)

---

## The two fields

A round is pictured by **two distinct optional fields**, never by one serving both purposes:

| Field | Asset class | Directory | Datum |
|---|---|---|---|
| `…_flag` | `flag` | `flag_directory` | The round's country |
| `…_image` | `track` | `track_image_directory` | The round's track name |

Both are **optional** on every type that declares them. A template may declare either, both, or
neither, and each is removable on its own terms under XIV.3.

## Who may declare a map

**Only the calendar and the check-in graphic.** Every other image type that pictures a round draws
the country flag and nothing else.

A catalogue declaring a `track`-class field for any other type is **invalid**, and Layer 2 MUST
refuse a template carrying one, naming the field.

**Why**: a map earns its place where the round is the graphic's subject. On a standings table, an
attendance sheet or a forecast the round is a *column heading*, at a size no circuit outline
survives, and a flag is what reads there.

## Ids name their class (XIV.11)

An id MUST name the class it draws. This **obliges** the rename below rather than merely permitting
it: a field called `track_image` with a country flag drawn on it is exactly the disagreement XIV.11
exists to prevent.

## Per-type catalogue diff

| Image type | Field before | Field after | Class | Template work |
|---|---|---|---|---|
| **Calendar** | `round_<x>_image` | `round_<x>_image` **kept** + `round_<x>_flag` **added** | `track` + `flag` | 12 flag slots added, at 3:2 |
| **Check-in (RSVP)** | `track_image`, `track_image_group` | **kept** + `track_flag`, `track_flag_group` **added** | `track` + `flag` | 1 flag slot + group, at 3:2 |
| **Standings — drivers** | `round_<z>_image` | `round_<z>_flag` | `track` → `flag` | Renamed **and re-geometried** 1:1 → 3:2 |
| **Standings — constructors** | `round_<z>_image` | `round_<z>_flag` | `track` → `flag` | Renamed **and re-geometried** |
| **Attendance sheet** | `round_<z>_image` | `round_<z>_flag` | `track` → `flag` | Renamed **and re-geometried** |
| **Weather** (p1, p2, p2_sprint, p3, p3_sprint, mystery) | `track_image` | `track_flag` | `track` → `flag` | Renamed **and re-geometried** ×6 |

**The re-geometry is not optional and is the likeliest thing to be missed.** Those slots are square
today because they held circuit maps. Renaming the id without reshaping the slot leaves every flag
letterboxed, and the generator never pads — no artwork a league supplies could fix it. The Layer 2
aspect check ([asset-aspect.md](./asset-aspect.md)) is what catches this.

## The calendar chooses per round

The calendar's choice is made **for each round of its grid separately**. One round may draw both,
another one, another neither. Both fields are members of the existing round collection and take its
ordinal as every other field of that round does — `round_7_flag`, `round_7_image` — inheriting the
collection's capacity under XIV.12. No new collection and no new discriminator.

## Packaged templates declare both

The templates packaged with the module for the calendar and the check-in graphic MUST **each
declare both fields**, so that a clean clone draws the two classes from the first render and a
league has a working example of each to author against.

## The mystery round

A round of the mystery format conceals its track and thereby its country. Both fields fill from the
literal `Mystery` by XIV.3's literal-value paragraph and resolve by the ordinary slug rule:

- `…_flag` → `mystery.svg` of the **flag** directory *(new packaged file)*
- `…_image` → `mystery.svg` of the **track** directory *(already ships)*

No field is emptied, no notice is raised, and no mandatory field of any graphic is emptied for want
of a track.

## Absent data

Where a round has no resolvable track at all — as against a mystery round, which *has* one and
withholds it — the existing per-type behaviour is unchanged: the round's group is removed where one
is declared, or its cells emptied where none is. A template giving the country a card, or the flag
or the map a plate, declares the removable group of those fields so that nothing stands empty under
a label naming what is not there.

## What does not change

- `asset_resolver.py` — class-agnostic already; takes a directory and a datum.
- The results and verdicts catalogues — neither pictures a round. They inherit only the driver-flag
  rekey from [country-flag-resolution.md](./country-flag-resolution.md).
- Every capacity, discriminator and removable-group declaration on every affected type.
