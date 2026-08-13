# Phase 0 Research: Weather Image Generation

**Feature**: `042-weather-image-generation` | **Date**: 2026-08-13

The spec carries no `NEEDS CLARIFICATION` marker, so this document resolves design questions rather than
open requirements. Each decision below was checked against the shipped code before it was written; where
a check changed the answer, that is said.

---

## R1 — How is a capacity "fixed by the template slot" declared?

**Decision.** `RowSpec` and `NestedSpec` each gain `minimum: int | None = None`. Where it is set,
`declared_capacity` raises `CapacityError` if the template declares fewer members than the floor, and
returns the declared count unchanged otherwise. `capacity` stays `None` for such a collection — the count
still comes from the template — and the floor constrains it from below.

**Rationale.** Constitution XIV.12 (v4.7.0) admits three ways a capacity is fixed, and the module
implements two. The existing pair cannot express the third:

| Reading | Declared as | Under-declaration | Over-declaration |
|---|---|---|---|
| Fixed by the image type | `capacity=<int>` | fatal | **fatal** — wrong, FR-017 requires silence |
| Fixed by the template | `capacity=None` | **admitted** — wrong, FR-016 requires refusal | silent |
| Fixed by the template slot | `capacity=None, minimum=<int>` | fatal | silent |

The floor is a constant of the *game*, not of the league: a sprint round holds four sessions and its
longest session allows three weather slots, whoever is playing. That is what makes it declarable at all,
and what distinguishes it from a capacity fixed by the data, which must be re-read from configuration at
every check.

**Alternatives considered.**

- *Check the floor in the weather utility at generation.* Rejected: XIV.9 says a check needing no data is
  complete at every moment and must not be deferred, and FR-016 names the naming command and season
  review explicitly. A league would learn its phase 3 template was too small two hours before a race.
- *Encode the floor as a fixed `capacity`.* Rejected: it makes over-declaration fatal, contradicting
  FR-017, and forbids a template author from drawing a fifth session block as chrome.
- *A per-catalogue `minimum` rather than per-collection.* Rejected: a phase 3 template has two floors —
  four sessions and three slots per session — at two levels of nesting. One number per catalogue cannot
  carry both.

---

## R2 — At which moments does the floor refuse, and what must be added to make it so?

**Decision.** Nothing beyond R1. `CatalogueLayer.check` already wraps `all_mandatory_ids(root)` in
`try/except CapacityError` and returns the exception's message as the layer's failure reason. Because the
floor raises the same exception from the same call, it refuses at every moment that runs Layer 2 — the
naming command, season review, and the pre-render check — with no new call site.

**Rationale.** This is XIV.9's structural check working as designed: the check reads the template and a
constant, holds no data, and is therefore complete everywhere. The existing gap-in-numbering and
no-member-at-all checks already travel this path, and the floor is the same kind of statement about the
same file.

**Verified.** `image_validity_service.py` lines 195–204 catch `CapacityError` and report `str(exc)`
directly, so the floor's message reaches the league manager in its own words. The message must therefore
name the template, the count declared and the count required (FR-016) — it is the whole of what they will
be told.

---

## R3 — Does `session_<x>_slot_type` collide with the `slot` collection?

**Decision.** No. No code is needed for FR-009.

**Rationale.** This was raised with the author during the constitution amendment and ruled a non-problem
on the ground that the two belong to different phases. Checking the code confirms the ruling holds
mechanically, which the ruling alone did not establish:

- `NestedSpec.declared_capacity` builds `^session_1_slot_(\d+)(?:_.*)?$`. The literal `type` is not
  `(\d+)`, so `session_1_slot_type` does not match and **cannot be counted as a slot member**.
- `RowSpec.declared_capacity` builds `^session_(\d+)(?:_.*)?$`. `session_1_slot_type` matches with
  ordinal 1, which is correct: it *is* a field of session 1.
- `_canonical` replaces all-digit segments with `#`, giving `session_#_slot_type` and
  `session_#_slot_#_label` — distinct strings, so the sibling and unknown-field checks tell them apart.

**Alternatives considered.** Renaming the field to `session_<x>_type` was offered to the author and
declined. Had the mechanics not held, the rename would have been forced regardless of the ruling; they
do, so the wip-spec's naming stands.

---

## R4 — Does the sibling relation need widening again?

**Decision.** No. This feature touches no sibling code.

**Rationale.** `ASPECT_TEMPLATES["weather"]` already lists all six template keys and
`ASPECT_SOURCE_MODULE["weather"]` is `"weather"`, so `sibling_keys` returns the other five for every one
of them by the aspect relation alone — before the source-module relation 041 added is even consulted.
FR-002, including its named instance of a slot field appearing on a phase 2 template, is satisfied by
`sibling_fields_declared` as it stands.

**Verified.** The six keys are registered in `image_constants.py`; `sibling_keys` is unchanged since 041.
The weather aspect is the case XIV.3 named from the beginning — "the six forecasts" appears in the
constitution's sibling clause verbatim — so the machinery was written in anticipation of exactly this.

---

## R5 — Where does the produce-before-destroy ordering go?

**Decision.** Reorder `phase2_service` and `phase3_service` so the previous phase's message is deleted
**after** the current phase's message has been posted successfully. Both paths inherit the corrected
ordering; the image branch adds none of its own.

**Rationale.** Both services today call `delete_forecast_message(..., phase_number=N-1)` and then
`post_forecast(...)`. XIV.8 and FR-045 require the reverse, and the reason is sharpest on the fallback
path: a phase 3 render that fails would, as the code stands, have already deleted the phase 2 forecast
before the fallback runs. The division holds nothing during the window in which something has already
gone wrong — the precise outcome the rule exists to prevent.

**Alternatives considered.** Giving the image path its own ordering and leaving the text path deleting
first was rejected on the same ground 041 rejected it for the attendance sheet: the fallback is the more
failure-prone path, and it is the path that would keep the defect. Two orderings in one flow also drift.

**Note on scope.** This is a change to the textual weather path. It is declared in Complexity Tracking
rather than absorbed silently, and it is the second such change this increment makes — FR-023a being the
first.

---

## R6 — How are the shared renderings made callable by both paths?

**Decision.** Extract three renderings from `message_builder.py`, leaving the message builders composing
messages out of them:

| Value | Today | After |
|---|---|---|
| Rain likelihood | `pct = round(rpc_pct * 100, 1)` inline in `phase1_message` | a named renderer, rounding to the **nearest whole number** (FR-023a) |
| Session weather type | `slot.capitalize()` inline in `phase2_message` | a named renderer both paths call |
| Slot sequence | `format_slots_for_forecast` returns `*Clear* → *Wet*` | the same function gains an unemphasised form; the message applies its own emphasis |

**Rationale.** XIV.7 requires one rendering for both presentations, and none of the three is reachable
today without building a whole Discord message around it. The third is the sharpest case: XIV.16 (v4.7.0)
says channel markup is not part of a value and that an image type stripping markup out of a string it was
handed "has been given the wrong thing, and the repair is in the code that handed it over". Returning the
value unadorned and letting the forecast message italicise it is that repair.

**Verified.** `session_type_label` already strips the length qualifier ("Short Sprint Qualifying" →
"Sprint Qualifying"), so FR-025 needs **no** work — the graphic calls the existing function.
`format_slots_for_forecast` already collapses a single-slot and a single-weather session as FR-029
requires; only the emphasis is at issue.

**Nothing pins the rounding.** No test asserts on `phase1_message`'s output, so the FR-023a correction
carries no test churn — but a test is added for it, the absence being what let the divergence stand.

---

## R7 — How is the template chosen by the format of the round?

**Decision.** A pure function in `image_weather_service.py`:
`weather_template_key(phase: int, round_format: RoundFormat) -> str`, reading nothing but its two
arguments. Sprint rounds take the `_sprint` key for phases 2 and 3; every other format takes the plain
key; phase 1 has one key for all formats; a mystery round reaches only the mystery key and no phase.

**Rationale.** XIV.10 (v4.7.0) requires the catalogue to name the selecting datum and requires the
selection to be a function of that datum alone. A pure function of the format is the most direct way to
make that inspectable and testable. FR-012 explicitly forbids three temptations — reading a count of the
sessions present, reading further configuration, and falling back to the other slot when the selected one
is unconfigured or invalid — and a function taking only the format cannot do any of them.

**Alternatives considered.** Selecting by the number of sessions the round holds would give the same
answer for every round the bot can schedule today (sprint ⇒ 4, otherwise 2) and would silently pick the
wrong template the day a format is added. The format is the datum; the session count is a consequence of
it.

---

## R8 — What do the eight shipped weather icons have to satisfy?

**Decision.** Eight plain SVGs in `resources/weather/`, beside the existing `fallback.svg`:
`sunny`, `mixed`, `rain`, `clear`, `light_cloud`, `overcast`, `wet`, `very_wet`.

**Rationale.** XIV.13's closed-set clause obliges the module to ship a file for every member of a
vocabulary the module itself defines, because the league did not choose it and cannot be incomplete
against it. The position-change markers are the existing precedent and ship exactly this way
(`resources/markers/` holds `gained`, `lost`, `unchanged` and `fallback`). Without them, every session and
every slot of every forecast would draw the class fallback and raise a notice apiece — a degradation no
league can act on, for a picture the module never had to draw wrong.

**Constraints on the files** (XIV.6): plain SVG, no `clipPath`, no gradient, no filter, authored at the
aspect ratio of the slot they fill, padded with transparent margin by the author where the subject does
not fill it. They are placeholders in the same sense as the fifteen shipped templates — correct and
replaceable, not a league's final artwork — and `resources/` continues to hold no league-specific art.

---

## R9 — What does `/images test weather-*` have to produce?

**Decision.** Six images across four commands, matching `TEST_KIND_TEMPLATES` as already registered:
`weather-p1` → 1, `weather-p2` → 2, `weather-p3` → 2, `weather-mystery` → 1.

**Rationale.** FR-060 requires an image per template exercised, and the four kinds already map to the six
keys in `image_constants.py`. The two-image kinds draw a sprint round and an endurance round, which
between them reach the greatest session count (4) and the greatest slot count (4) the module can produce —
so the pair is not two arbitrary examples but the two extremes of the space.

**The fabrication constraints are real test coverage, not decoration.** FR-062's non-whole percentage is
what makes the FR-023a rounding visible; FR-063's three types and FR-064's five weathers are what make all
eight shipped icons visible; FR-064's single-slot and single-weather sessions are what make
`format_slots_for_forecast`'s two collapse branches visible.

---

## R10 — Where does the mystery notice's image branch go?

**Decision.** In `mystery_notice_service.run_mystery_notice`, beside the existing `post_forecast` call,
on the same pattern as the three phase services.

**Rationale.** XIV.8's "no posting, no graphic" means the graphic must hang off the posting the module
already makes, never create one. The service already resolves the division, the forecast channel and the
server, already guards against a round amended away from `MYSTERY` before the job fires, and already
records the message under phase `0`. The image branch changes what is posted and nothing about when.

**Verified.** The notice carries no role mention today (`post_forecast` is called with the message alone
and `mystery_notice_message()` tags nobody), which is what FR-052 requires of the graphic's message too.
Constitution Principle IV was corrected at v4.7.0 to record this posting; before that amendment the
principle forbade it, and the graphic would have been unspecifiable.

---

## Summary of code reached outside the image module

| File | Change | Requirement |
|---|---|---|
| `models/image_catalogues.py` | `minimum` on two specs; six catalogues | R1, FR-014 |
| `utils/message_builder.py` | three renderings extracted; rounding corrected | R6, FR-021, FR-023a, FR-029 |
| `services/phase1_service.py` | image branch | FR-042 |
| `services/phase2_service.py` | image branch; **reordered** | FR-042, FR-045, R5 |
| `services/phase3_service.py` | image branch; **reordered** | FR-042, FR-045, R5 |
| `services/mystery_notice_service.py` | image branch | FR-052, R10 |
| `cogs/image_cog.py` | four test-kind guards | FR-058 |
| `resources/weather/` | eight icons | FR-034 |

Everything else is new files inside the image module.
