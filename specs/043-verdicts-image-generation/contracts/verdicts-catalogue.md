# Contract: The Verdicts Field Catalogue

**Feature**: `043-verdicts-image-generation`
**Declared in**: `src/models/image_catalogues.py` as `VERDICTS_CATALOGUE`
**Registered as**: `CATALOGUES["verdicts_template"]`
**Governs**: Constitution XIV.2, XIV.3, XIV.10, XIV.11, XIV.13

---

## Shape

```
FieldCatalogue(
    mandatory = { 8 ids },
    optional  = { 6 ids + 3 group ids },
    assets    = { "driver_flag": "flag", "team_image": "team" },
    rows      = None,
    columns   = None,
    keyed     = None,
    singleton = None,
)
```

Four `None`s are the contract, not an omission. This type declares **no collection**, so XIV.11's
discriminator rules and XIV.12's capacity rules bind nothing in it. `WEATHER_MYSTERY_CATALOGUE` is the
precedent and the proof that the pipeline carries such a catalogue end to end.

## Ids

Full lists are in [data-model.md](../data-model.md). What this contract fixes beyond them:

1. **Every id is flat.** No `<collection>_<x>_<field>` form appears. A checker that finds a discriminator
   in a verdicts id has found a template authored against a different catalogue.
2. **The three `_group` ids are declared members of the catalogue**, not implicit. `team_name_group` is
   the interesting one: it wraps the team name, the team image *and* the TEAM label, so an attendance
   sanction removes the label with the values (XIV.2, removable groups).
3. **`session_name` is mandatory and may be drawn empty.** See below.

## The classification of `session_name`

Mandatory. The template must declare it; a template without it is refused when it is named.

For `VerdictKind.ATTENDANCE_SANCTION` the value is **determined to be nothing**:

- the field is emptied;
- `session_name_group` is removed if the template declares one;
- **no** notice is raised.

This is XIV.3's determined-empty clause, ratified at v4.8.0. It is *not* an unresolved mandatory field,
and an implementation that reports it as one is wrong. The distinguishing question the code must answer
is "could the value be determined?" — for an attendance sanction it could, and the answer is "there is
no session".

The label `"Attendance Sanction"` MUST NOT be written into `session_name`. It stands on `verdict_stage`
alone. The textual announcement carries it in its heading, which is a difference in arrangement and not
in rendering (XIV.7).

## Sibling relation

Verdicts is a sibling of every other catalogue drawing the same output aspect **or** belonging to the
same source module (XIV.3, as widened at v4.6.0). A verdicts template declaring a sibling's field is
refused at the moment it is named. An id belonging to **no** catalogue is ignored — hand-authored SVG
carries ids on everything, and only ids a catalogue claims are fields.

## Asset resolution

| Field | Class | Datum | Absent datum |
|---|---|---|---|
| `driver_flag` | `flag` | Driver's recorded nationality adjective | Field removed |
| `team_image` | `team` | Resolved team name, or the Discord role name | Field removed |

Resolution is by the module's normalised slug in the configured directory, with the three outcomes of
XIV.13 (found / fallback + notice / problem). The catalogue declares **no** per-field "absent datum draws
the fallback" licence for either: a flag for a driver who recorded no nationality, or a badge for a
verdict that names no team, would each be a picture standing for a thing that does not exist.

### The one notice suppression, justified per field

`driver_flag` carries XIV.4's **configured-absence** suppression: where the league has switched
nationality collection off via `signup nationality toggle`, the field is removed and **no notice
whatever** is raised. Where the league does collect nationality and this driver recorded none, the field
is removed and a notice **is** raised.

The distinction is already modelled in the lineup's handling and is carried, not re-derived. The author
ruled on 2026-08-14 that verdicts inherits it: same switch, same field, same reason.

## Static declaration

The type is declared **static** under XIV.17, on the second ground that rule admits — it draws a
**record of an event**, not a view of a state — together with the condition that makes the ground sound:
a correction of the decision arrives as a **new verdict**, never as an edit of the one standing.

**This declaration is part of the catalogue's contract.** Adding a field to it is an amendment of the
declaration and must be reviewed as one, the question being whether the new value was settled at the
moment the decision was taken. The steward module is expected to ask exactly this.
