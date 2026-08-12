# Phase 0 Research: Calendar Image Generation

Six questions had to be settled before the design could be written. Four arose from reading the
delivered 035 engine against the calendar's requirements; two are gaps in the data model that the
spec's requirements cannot be met without addressing.

---

## R1. Where the calendar's capacity comes from

**Decision**: `RowSpec` gains a capacity that may be **derived from the template** rather than
fixed in code. The catalogue declares the collection's name (`round`), its field suffixes and which
of them are mandatory; the *number of slots* is read from the template being checked or filled.

**Rationale**: Constitution XIV.12 requires an image type drawing a varying list to state that
list's capacity "in its catalogue, and that number MUST match the members the template declares."
For the standings and results types, the template is drawn to a capacity the league chooses once and
the catalogue can carry it. For the calendar it cannot: a league draws however many rounds its season
runs, and the wip-spec's validity rule says as much — at configuration time the module verifies "only
that the template declares at least one round, numbered continuously from 1." The capacity *is* what
the template declares, so the catalogue's job is to say *how to count it*, not *what it is*.

Concretely: a `RowSpec` whose `capacity` is `None` means "count the members the template declares",
found by scanning ids matching `<prefix>_<n>_` and requiring contiguity from 1. A `RowSpec` with an
integer capacity keeps today's behaviour untouched for every other type.

**Alternatives considered**:
- *Hard-code a capacity* (say 24). Rejected: forces every league onto one round count and makes the
  template's own layout a lie.
- *Skip the capacity check for the calendar.* Rejected: the author ruled on 2026-08-12 that overflow
  is fatal for every collection of every graphic, and this is the collection that ruling was about.
- *A separate `TemplateDerivedRowSpec` type.* Rejected: two shapes for one concept, and every
  consumer would have to branch on which it got.

---

## R2. How Layer 2 checks a template whose field set depends on itself

**Decision**: `CatalogueLayer` resolves the round count from the template first, then checks that
each declared round carries every mandatory round field, and that the numbering is contiguous from 1.
A template declaring no round at all fails the layer.

**Rationale**: `all_mandatory_ids()` today enumerates `row_1..row_N` from a constant N. With N
derived, the same method works — it just needs the root passed in. The layer already receives the
parsed tree via `ctx.tree(path)`, so the information is in hand. This keeps the constitution's "same
object consulted by the fill pipeline and by validity Layer 2" (XIV.10) true: both call the same
catalogue with the same root and get the same answer.

**Alternatives considered**:
- *A new Layer 5 for round structure.* Rejected: XIV.9 says a layer must be ratified before it is
  enforced, and the constitution ratifies no such layer. The check belongs to Layer 2's stated
  purpose — field-catalogue conformance — and needs no new layer.

---

## R3. Which command the round-capacity guard belongs on

**Decision**: leave `placement_service._guard_image_capacity` alone, and add a separate guard on the
command that adds a round to a division. The calendar's capacity is compared against the division's
**round count**; the driver-seat guard continues to serve the lineup, results and standings types.

**Rationale**: the existing guard counts seated drivers against `min(declared_capacities())`. Wiring
the calendar into it would refuse a *driver placement* because a *calendar template* is too small,
which is both wrong and baffling to a manager. XIV.12 requires rejection "at the earliest moment it
can be detected — including the command that would grow the division past the capacity"; for rounds
that command is round-add, not placement.

**Alternatives considered**:
- *One guard keyed by collection kind.* Rejected for this increment as premature: it would need a
  generalisation over collections that only two types currently exercise. Worth revisiting when the
  attendance and standings round-grids land, which face the same question.

---

## R4. Whether the crop machinery already suffices

**Decision**: reuse `svg_fill` unchanged. `FillSpec.crop` names the crop-point id; `_crop_to`
rewrites the root `height` and `viewBox`; `_is_below` decides which elements the cut removes.

**Rationale**: read against the wip-spec's § "The vertical crop", the delivered behaviour already
matches — the cut is applied to the SVG before rasterisation, the width is untouched, and elements
below the cut are left for the cut to remove. The calendar's remaining obligation is a *caller*
concern: deciding which rounds stand beside the final one and must be removed by their `_group`
rather than left to the cut. That is spec-building, not fill machinery.

**One extension**: FR-026 requires a non-fatal notice where the final declared round's crop point
does not stand at the template's declared height. `fill()` does not currently compare the two. This
is a small addition at the crop step, not a redesign.

---

## R5. A round with no time recorded — dormant by design; **no code owed**

**Decision**: write **no** calendar-specific handling. The test-data clause requiring such a round is
struck from the wip-spec; the emptying provision stays as a statement of what would happen, standing
against a round shape the bot does not hold.

**Rationale**: `Round.scheduled_at` is a non-nullable `datetime` carrying date and time as one moment,
with no `time_tbd` flag and no convention marking a time as unknown. Put to the author on 2026-08-12,
this was confirmed as a **deliberate design decision**, not an omission — so the case is unreachable
rather than unimplemented.

Two consequences follow, and the second is the useful one:

1. The test-data clause was **unsatisfiable**, not merely awkward: the fabricator cannot produce a
   round the model cannot express. It is removed from the wip-spec's § "Test data" and from FR-021.
2. **No branch is owed at all.** `round_<x>_time` is an *optional* field, and constitution XIV.3
   already has the engine empty an optional field whose value cannot be determined — via
   `FillSpec.empty`. Were the shape ever to exist, the generic path handles it. A calendar-specific
   emptying branch would have been dead code duplicating behaviour the engine already has.

The earlier plan to "write the branch anyway as dormant code" is withdrawn. Dead code that duplicates
a live generic path is worse than no code, and the wip-spec now records why a future session should
not read the absent test case as a gap.

**Alternatives considered**:
- *Add a `time_tbd` flag to make the rule reachable.* Rejected — outside this increment's scope and
  contrary to a decision the author has confirmed.
- *Strike the emptying provision from the wip-spec too.* Rejected — it states correct behaviour for a
  shape that may exist one day, and costs nothing standing there now that it says so explicitly.

---

## R6. Where the calendar's data comes from

**Decision**: read the division's rounds ordered by `round_number`; join each round's `track_name`
to the `Track` record for its country, grand prix name and canonical name.

| Template field | Source | Needs the `tracks` join? |
|---|---|---|
| `round_<x>_number` | `Round.round_number` | No |
| `round_<x>_format` | `Round.format` → "Sprint" / "Endurance" / "Mystery"; empty for `NORMAL` | No |
| `round_<x>_date` / `_time` | `Round.scheduled_at`, through the configured date format, time format and zone | No |
| `round_<x>_track_name` | `Round.track_name`, denormalised on the round | No |
| `round_<x>_image` | asset class `track`, datum `Round.track_name` | No |
| `round_<x>_country_name` | `Track.country` | **Yes** |
| `round_<x>_race_name` | `Track.gp_name` | **Yes** |
| `division_name` / `division_tier` | `Division.name` / `Division.tier` | No |
| `season_number` | the season's number | No |

**The graphic substitutes this exact textual posting**, which `season_cog` builds at approval:

```text
📅 **Elite — Race Calendar**
Round 1: Autódromo José Carlos Pace — <t:…:F>
Round 9: Mystery — <t:…:F>
```

Two things follow. The heading the image message carries is
`📅 **{division_name} — Race Calendar**` — the literal string the text path already emits, so the two
forms are indistinguishable above the fold. And the text line's track is `Round.track_name` with
`"Mystery"` substituted where it is null, which is the same value and the same substitution the
graphic's `round_<x>_track_name` uses; the graphic then adds the country, the grand prix name, the
format and the image on top.

**Information parity**: the graphic is a superset of the text — every datum the text carries appears
on it — with one deliberate reduction. `<t:…:F>` renders per reader in their own zone and includes the
weekday; the graphic carries one configured zone for all readers (XIV.15). The default `date-format`
(`Sun 14 Jun 2026`) carries the weekday, so that part of the parity holds.

**A mystery round holds `Round.track_name = None`** and joins to no track. Its three name fields take
the literals fixed in the wip-spec's § "A round of the mystery format" — "Mystery GP", "Mystery",
"Mystery" — and its image resolves from the datum "Mystery", which is why FR-027 ships
`resources/tracks/mystery.svg`.

**Note on the round's track key**: the wip-spec says the image is "derived from the track ID", but
`rounds` holds `track_name TEXT` with **no `track_id` foreign key** — the tracks registry is joined by
name. The name is therefore the key, which is what the existing `normalise()` slug rule and the
shipped asset filenames already assume.

---

## R7. What happens when a round's track name matches no track record

**The risk**: `round_<x>_country_name` and `round_<x>_race_name` are **mandatory**, and are the only
two fields needing the `tracks` join. A round whose `track_name` matches no `tracks.name` row yields
neither, which under XIV.3 is an undeterminable mandatory value — **fatal**, so the whole division's
calendar falls back to the textual posting.

**This makes the graphic strictly more fragile than the text it substitutes.** The text prints
`Round.track_name` and needs no join at all, so it cannot fail this way. A track renamed or removed
from the registry after a season's rounds were scheduled would silently revert that division to text.

**How narrow is it**: rounds normally take their track from the registry's autocomplete, so the names
match at the moment of scheduling. The failure mode is a *later* divergence — a renamed or deleted
track — not a mistyped one.

**Decision**: **fatal**, and the division falls back to the textual calendar. Put to the author on
2026-08-12 and confirmed. No special case is written: the generic undeterminable-mandatory-value path
already produces exactly this outcome, so the decision is a confirmation that the default is wanted
rather than a change to the design.

Recorded in the wip-spec's § "Resolution of the data to be placed", including the observation that
the graphic is the more fragile of the two forms here, so a future session does not read the
asymmetry as a defect.

**Alternatives considered**:
- *Draw it as a mystery round.* Rejected: it would show a real round as "Mystery GP", stating
  something false about the season rather than declining to draw it.
- *Reclassify country and race name as optional.* Rejected: diverges from the wip-spec, and a calendar
  entry with no race identity is close to worthless.
- *Add a `track_id` foreign key.* Rejected for this increment as a schema migration across rounds and
  their archived history, well beyond the calendar. It remains the only fix for the root cause and is
  worth raising when the schema is next opened.

---

## Resolved unknowns

Every NEEDS CLARIFICATION from the Technical Context is closed:

- **Capacity source** → R1, derived from the template.
- **Layer 2 applicability** → R2, extend the existing layer, ratify no new one.
- **Guard placement** → R3, a separate round-count guard on round-add.
- **Crop reuse** → R4, reuse as built plus one notice.
- **Round data sources** → R6.
- **A round with no time** → R5, dormant by design and owed no code.

**No open decisions remain.** The design is ready for `/speckit-tasks`.
