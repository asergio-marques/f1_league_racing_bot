# Contract: the `/images test` command surface

The module's user-facing contract is its slash-command surface. This document defines the eleven commands, their parameters, their refusals and their replies.

## Shape

`test` is an `app_commands.Group` nested inside the existing `images` group — command → group → subcommand, the depth `/images config toggle` already uses. All eleven are restricted as the withdrawn command was (`@channel_guard`, `@admin_only`) and reply ephemerally.

| Command | Parameters |
|---|---|
| `/images test calendar` | `division` |
| `/images test lineup` | `division` |
| `/images test results` | `division`, `round` |
| `/images test standings` | `division`, `round` |
| `/images test attendance` | `division`, `round` |
| `/images test rsvp` | `division`, `round` |
| `/images test weather-p1` | `division`, `round` |
| `/images test weather-p2` | `division`, `round` |
| `/images test weather-p3` | `division`, `round` |
| `/images test weather-mystery` | `division`, `round` |
| `/images test verdict` | `division`, `round` |

Both parameters are mandatory. `division` is a string with autocomplete over the active season's divisions. `round` is an integer.

## Refusal contract

Refusals are evaluated in this order, and the first that applies is the one reported (FR-014, FR-015). Every refusal returns a message and no picture, and none is reached after a render has begun.

| # | Condition | Applies to | Message names |
|---|---|---|---|
| 1 | The rasteriser is absent | all | that Inkscape is not installed on this host |
| 2 | The images module is disabled | all | the module, as the withdrawn command did |
| 3 | No active season | all | that there is no active season to draw a division from |
| 4 | No division of that name in the active season | all | the name given, and that no division bears it |
| 5 | The division holds no configured round | `calendar` | that the division has no calendar to draw |
| 6 | The division holds no round of that number | the nine round-scoped commands | the number given |
| 7 | The division holds no team beyond Reserve | `lineup`, `results`, `standings`, `attendance` | which of the four subjects cannot be drawn |
| 8 | The round is of the mystery format | `weather-p1`, `weather-p2`, `weather-p3` | that a mystery round carries no forecast |
| 9 | The round is **not** of the mystery format | `weather-mystery` | that the round is not a mystery round |

Ordering note: #6 precedes #7 so that a wrong round number is never reported as a missing team list, and #4 precedes both so a mistyped division is never reported as a missing round.

## Reply contract

A successful reply carries:

1. **The pictures.** One per template the kind draws — one for `calendar`, `lineup`, `attendance`, `rsvp` and each weather phase; two for `standings`; one per session for `results`; one per case for `verdict`.
2. **A line per picture** saying whether it was produced, and for a failure, why.
3. **The notice block**, as today — every non-fatal notice the render raised, including asset fallbacks.
4. **The artwork report** (FR-037, FR-038), in three distinguishable forms:

| Form | Meaning | Wording carries |
|---|---|---|
| fallback drawn | the directory resolved; no file matched the datum | the asset class, the datum, the field |
| directory rejected | the configured path could not be resolved | the asset class, the configured value, why it was rejected |
| *(silence)* | every asset resolved to its own file | — |

5. **A fabrication notice** where drivers were fabricated (FR-018), so that a manager is never left thinking the picture shows a real roster.

## Invariants

- No command writes to, alters or deletes a league record (SC-006).
- No command posts to any channel of a division; pictures and errors alike go to the invoker (FR-005).
- Every asset resolves through the league's configured directories, never the packaged ones (FR-035).
- A fatal error yields no picture at all, never a partial one, and never falls back to a textual posting — no command here has one (FR-007).

## Withdrawn

`/images test <kind>` with its eleven-value choice parameter, and every value of it. The `verdicts` value becomes the `verdict` subcommand — note the singular (A-003).
