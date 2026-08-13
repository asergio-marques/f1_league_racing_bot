# Phase 0 Research: Attendance Image Generation

**Feature**: 041-attendance-image-generation | **Date**: 2026-08-13

Seven decisions. Each was taken against the code as it stands on `main` at 5f13b2f, not against the
shape the earlier increments are assumed to have left.

---

## R1 — The two catalogues need no new declaration form

**Decision.** Add two constants to `src/models/image_catalogues.py` and no dataclass field.

- `ATTENDANCE_CATALOGUE` — `columns=RowSpec(prefix="round", optional_unit=True, …)` for the round
  headings, and `rows=RowSpec(prefix="row", …, nested=NestedSpec(prefix="round", optional_unit=True,
  fields={"points"}))` for the driver rows and their per-round cells.
- `RSVP_CATALOGUE` — `mandatory` / `optional` top-level fields, plus
  `rows=RowSpec(prefix="session", optional_unit=True, fields={"group", "name"},
  mandatory_fields={"group", "name"})`.

**Rationale.** The sheet is the standings-drivers shape with the movement columns removed: one grid,
two ordinals, cells hanging off the row and chrome standing at top level. 040 added exactly those forms
— `FieldCatalogue.columns`, `RowSpec.nested`, `optional_unit` — and the docstring on `RowSpec.prefix`
already anticipates `session` as a collection name. The check-in graphic is simpler than the calendar.

This is the payoff 040's Complexity Tracking predicted in as many words: *"Every later grid type —
attendance's per-round points — reuses them unchanged."* It does.

**Alternatives considered.** Declaring the sheet's cells as a second top-level collection rather than a
nest under the row: rejected, because a cell belongs to its row and its column both and a node has one
parent (XIV.2), and `columns` is documented as carrying chrome alone. Giving the check-in graphic a
`KeyedSpec` for its sessions: rejected, sessions are ordered and ordinal, and XIV.11 forbids a key
where an ordinal would serve.

---

## R2 — The sibling check widens in two dimensions

**Decision.** Widen `sibling_row_fields` and `sibling_fields_declared` in
`src/models/image_catalogues.py`:

1. **Relation.** Siblings are drawn from `ASPECT_TEMPLATES` (one aspect) **union**
   `ASPECT_SOURCE_MODULE` (one source module). Both maps already exist in `image_constants.py`, and
   `ASPECT_SOURCE_MODULE` already maps `attendance` and `rsvp` to `"attendance"`.
2. **Surface.** The check compares the full addressable surface of the two catalogues — top-level
   `mandatory`/`optional` ids, and every collection's constructed ids — not `rows` alone.

**Rationale.** The existing implementation encodes "siblings are two templates of one aspect that
differ in their rows". That is true of qualifying/race results and of the two standings, and false
here in both halves:

- The sheet and the call are **different aspects** (`attendance`, `rsvp`), so the current relation
  returns no sibling at all and FR-006 would be unimplemented.
- Their overlap is in **top-level** fields — both declare `season_number`, `division_name`,
  `division_tier`, `round_number`, `race_name` — while their collections differ entirely. A sheet
  template declaring `round_format`, `round_date` or `session_1_name` is the wrong file in that slot,
  and the row-prefix regex in `sibling_fields_declared` matches none of them.

The union is what keeps the widening safe. Restricting the relation to the source module *alone* would
break the results and standings pairs, whose two templates are one aspect (`ASPECT_TEMPLATES["results"]`
holds both session templates) but whose source module is shared with types that are not their siblings.

**Alternatives considered.** A hard-coded sibling pair inside the attendance catalogues: rejected under
XIV.10 — a second, private relation beside the declared one is what the shared declaration exists to
prevent. Treating *any* two image types as siblings: rejected, it would make a calendar template
declaring `team_red_bull_name` a fault, which the constitution explicitly says is not the module's
business, and would break templates that render today.

---

## R3 — The floor is raised by the type's utility, not declared in the catalogue

**Decision.** `image_attendance_service.resolve_drawing` raises its data error when the sheet has no
driver, exactly as `image_calendar_service.py:150` already does for a division holding no round. No
`floor` field is added to `RowSpec`.

**Rationale.** The calendar has carried this same floor since 037 and implements it in the utility. The
constitution requires the floor to be *declared per image type and not inferred* (XIV.12, v4.6.0); the
type's own resolution utility is where the calendar declares it, and a second mechanism for the second
instance would leave two ways to express one rule.

It is also the right *kind* of place. A catalogue declares fields — what the template must carry and how
each is classified — and the floor is a statement about **data**, not about the template. A template
declaring one row is valid; a division holding no driver is not drawable. Those are checked at different
moments against different inputs, and XIV.12 says so: the floor is checked against the concrete data at
generation and MUST NOT be approximated earlier.

**Alternatives considered.** `RowSpec.floor: int = 0`, read by the capacity check: more discoverable, and
rejected because it puts a data rule in a field declaration, changes the shared declaration module for
something no template check can use, and would leave the calendar's existing floor as a second
implementation unless that were migrated too — scope this increment does not own.

**Consequence for tasks.** The floor error must be raised where the *drivers* are resolved, before any
template measurement, so that a division with no drivers reports "no driver" rather than a confusing
capacity divergence against a template that is perfectly fine.

---

## R4 — The check-in graphic's staticness is enforced by the call graph

**Decision.** `image_rsvp_post.try_attach` is called from exactly one place — the initial post in
`rsvp_service.run_rsvp_notice` — and from nowhere else. The button handler (`RsvpView`), the reserve
distribution (`run_reserve_distribution`), the deadline handler (`run_rsvp_deadline`) and
`_rebuild_embed_for_round` import no image module. A test asserts this directly.

**Rationale.** XIV.17 places the obligation on the author and states plainly that the module cannot
detect a breach: a field's mutability is a fact about the attendance module, not a property visible in
the catalogue. Nothing here can change that. What the design can do is make the mistake structurally
hard rather than merely forbidden, and the single call site is the strongest available form — a future
session adding a redraw has to *add an import* to a module that has none, which is visible in review in
a way that a wrong catalogue entry is not.

The complementary half is negative and belongs in the catalogue: `RSVP_CATALOGUE` declares no driver
name, no team name, no RSVP status and no attendance point. That is FR-009, and it is what makes the
single call site *sufficient* rather than merely tidy.

**Alternatives considered.** A `static: bool` flag on `FieldCatalogue` read by an assertion at
generation: rejected, it can only assert that a static type is not being redrawn, which the call graph
already guarantees, and it cannot assert the thing that matters — that no field it draws is mutable.
It would read as a check while checking nothing. The author's ruling of 2026-08-13 chose the declared
form over a catalogue-derived one for this reason.

---

## R5 — The sheet's replacement ordering moves into the textual flow

**Decision.** Reorder `attendance_service.post_attendance_sheet` to build its replacement, post it, and
only then delete the prior message; the image branch sits inside that same function and inherits the
ordering rather than reimplementing it.

**Rationale.** The function today deletes the prior sheet at its top and sends the new one ~90 lines
later, returning early on an `HTTPException` from the send. A failed post therefore leaves the channel
with no sheet at all, and adding a render in front of it widens the window. FR-045 and XIV.8 require
produce-before-destroy, and the author's ruling of 2026-08-13 — "the image path should inherit this" —
places the ordering in the text flow rather than beside it.

`image_results_post.try_post` already implements the ordering correctly for the results type and its
docstring states the rule; this brings the attendance flow to the same shape.

**Alternatives considered.** Produce-before-destroy in the image branch only: rejected, it leaves the
fallback path — the one reached *because* something already failed — deleting first. Two orderings in
one function would also drift.

---

## R6 — The failed check-in report belongs to the RSVP flow, not the image module

**Decision.** FR-062's report is raised in `rsvp_service.run_rsvp_notice`'s existing
`except discord.HTTPException` branch, through `output_router.post_log`, and fires whether or not the
`rsvp` toggle is on.

**Rationale.** The fault is that the *call* did not post. Whether it would have carried a picture is
irrelevant to the league that now has a round nobody was asked to check in for. Putting the report
behind the image module's toggle would mean a league that never enables images never learns its calls
are failing — and the failure is currently invisible: the branch does `log.error` to the application
log, which reaches no Discord channel.

This is the one requirement in the feature that is not about images at all, and the plan places it
accordingly. It is also why FR-062 is worded to hold with the toggle off.

**Alternatives considered.** Enqueuing the failed call for retry: rejected on inspection —
`retry_service.enqueue` takes `content: str` and `attempt_delivery` reposts chunked text, so the call
would arrive with no embed, no roster and no buttons. Recorded in the spec's Out of Scope, and the
wip-spec was corrected, having required exactly that. Opening the attendance rows before the post so a
failure cannot lose them: rejected — every driver would then take a no-RSVP penalty at the deadline for
a call none of them saw, which is worse than the hazard.

---

## R7 — The check-in deadline is derived in the attendance service

**Decision.** Add `attendance_service.derive_checkin_deadline(scheduled_at, deadline_hours) -> datetime`
and call it from the image utility. The image utility performs no arithmetic on times.

**Rationale.** The deadline is the round's scheduled time less the configured `rsvp_deadline_hours`, a
configuration of 0 placing it at the round's own start. XIV.7's derived-presentation clause admits it —
it is arithmetic over figures the bot already holds and decides nothing — on the condition that the
derivation is written in the service owning the figures, so the text path can adopt it without a second
implementation. The embed carries no deadline today; this is the value that would be adopted.

**Rationale for the boundary.** The module already *enforces* this deadline when it schedules
`run_rsvp_deadline`. Drawing it is reading the result of a rule the module applies, not applying one —
which is the line XIV.7 draws between a measurement and a decision.

**Alternatives considered.** Computing `scheduled_at - timedelta(hours=n)` inline in the image utility:
rejected under XIV.7's first condition. Reading the scheduled job's fire time: rejected, it couples a
drawing to the scheduler's state and would draw nothing when the job has already fired.

---

## Resolved unknowns

| Question | Resolution |
|---|---|
| Does the sheet need a new declaration form? | No — 040 built every form it uses (R1) |
| Are the two graphics siblings under the current code? | No, and the check misses their overlap too (R2) |
| Where does the empty-division floor live? | The type's `resolve_drawing`, per the calendar precedent (R3) |
| Can the module detect a stale static graphic? | No. The call graph is the strongest available guard (R4) |
| Does the text sheet flow already produce before destroying? | No — it deletes first (R5) |
| Can a check-in call be enqueued for retry? | No — the queue carries text alone (R6) |
| Where is the check-in deadline computed today? | Nowhere; it must be added to `attendance_service` (R7) |
| Is a migration needed? | No — both message-id columns already exist |
