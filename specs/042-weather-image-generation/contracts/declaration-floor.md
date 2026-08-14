# Contract: The Capacity Fixed by the Template Slot

**Feature**: `042-weather-image-generation` | Constitution XIV.12 (v4.7.0), XIV.9

The third way a capacity is fixed, ratified at v4.7.0 and implemented here for the first time. This
contract states the declaration, the derivation of the numbers, the behaviour in both directions, and the
moments it binds.

---

## The declaration

`RowSpec` and `NestedSpec` each gain:

```
minimum: int | None = None
```

Set alongside `capacity=None`. The count still comes from the template; the floor constrains it from
below. `None` is the default, so every catalogue written before this feature is unaffected.

## Behaviour

| Template declares | Outcome | Where detected |
|---|---|---|
| Fewer than `minimum` | **Problem.** `CapacityError`, naming the collection, the count declared and the count required | All three validity moments |
| Exactly `minimum` | Accepted | — |
| More than `minimum` | **Accepted.** The surplus is removed at generation by its group, silently, raising no notice | Generation |
| A gap in the numbering | **Problem**, as it already is | All three validity moments |
| None at all | **Problem**, as it already is | All three validity moments |

The floor is a lower bound and never an upper one. The upper bound remains the data actually drawn: a
round holding more sessions than the template declares, or a session drawn more slots than the template
declares for it, is a problem at generation however the floor was satisfied (FR-037, FR-039).

## The four floors, and how each is derived

Never written as a literal. Each is the greatest its slot's served formats can demand, computed from
`SESSIONS_BY_FORMAT` and `MAX_SLOTS` at module load (FR-015).

| Slot | Formats served | Sessions floor | Slots floor |
|---|---|---|---|
| `weather_p2_sprint_template` | Sprint | **4** | — |
| `weather_p2_template` | Normal, Endurance | **2** | — |
| `weather_p3_sprint_template` | Sprint | **4** | **3** |
| `weather_p3_template` | Normal, Endurance | **2** | **4** |

**Where the slot floors come from.** The greatest `MAX_SLOTS` value over every session type any served
format runs:

- *Sprint*: Short Sprint Qualifying 2, Long Sprint Race 1, Short Feature Qualifying 2, **Long Feature Race
  3** → floor **3**.
- *Normal*: Short Qualifying 2, Long Race 3. *Endurance*: Full Qualifying 3, **Full Race 4** → floor **4**.

> The wip-spec said **two** for the sprint slot, on the ground that "the longest [sprint session] allows
> two weather slots". That was an arithmetic slip: the Long Feature Race allows three. Corrected on the
> author's instruction of 2026-08-13 to follow the shipped textual functionality, and verified against
> `MAX_SLOTS[LONG_FEATURE_RACE] == 3` and the constitution's Round Formats table.

**A consequence worth stating to template authors.** The floor is the maximum across the *formats served*,
not across the sessions of any one round, so cells below it are routinely removed:

- on a sprint phase 3 template, the third slot cell is absent from the sprint qualifying, the feature
  qualifying and the sprint race, and present only on the feature race;
- on a plain phase 3 template, the fourth slot cell is reached by the endurance race alone, and a normal
  round removes the third and fourth from its qualifying.

This is FR-017's silent removal working as intended, not a degradation, and it raises no notice.

## The moments it binds

The floor reads the template and a constant of the module. It holds no data, so under XIV.9 it is a
**structural** check: complete at every moment, refusing at each with that moment's severity.

| Moment | Effect |
|---|---|
| `/images template weather-p2\|p3\|p2-sprint\|p3-sprint` | The command is rejected; the configuration is left as it stood |
| `season review` | The template is named individually — which phase, and whether sprint, plain or the mystery notice; approval is refused while it stands (FR-019) |
| Immediately before a render | The render fails and the phase falls back to text (FR-055) |

**No new call site is required.** `CatalogueLayer.check` already wraps `all_mandatory_ids(root)` in
`try/except CapacityError` and surfaces the message verbatim, and all three moments run Layer 2. This is
why the error message must name the collection, the declared count and the required count: it is the
entirety of what the league manager will be told.

## What this is not

- **Not a capacity fixed by the data.** That reading re-reads a configured value at every check and
  refuses in *both* directions. The floor here is a constant of the game — a sprint round holds four
  sessions whoever is playing — so it is knowable with no league in view, which is what lets the naming
  command refuse on it.
- **Not a collection floor.** XIV.12's *floor* on a collection (the calendar's rounds, the attendance
  sheet's drivers) is about the **subject** having nothing to draw, is checked against concrete data, and
  no weather type declares one. This is about the **template** being too small, is checked against the
  file, and every phase 2 and phase 3 type declares one. The two are unrelated despite the shared word.
- **Not applicable to phase 1 or the mystery notice.** Neither declares a collection at all.
