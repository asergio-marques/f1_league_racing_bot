# Feature Specification: Calendar Image Generation

**Feature Branch**: `037-calendar-image-generation`
**Created**: 2026-08-12
**Status**: Draft
**Input**: User description: the calendar image type — field catalogue, vertical crop, data resolution, mismatch handling, generation and posting, and test data.

> **Normative source.** The rules this feature implements are stated in
> [`docs/wip-specs/image_module_specification.md`](../../docs/wip-specs/image_module_specification.md)
> — § "Conventions of every graphic" and § "Calendar image generation" — and are governed by
> Principle XIV of the constitution. This document does **not** restate those rules. It states
> what must be built, who it is for, and how each obligation is verified, and cites the wip-spec
> where the rule itself lives. Where this document and the wip-spec disagree, the wip-spec wins
> and this document is the one to correct.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Preview the calendar graphic before a season depends on it (Priority: P1)

A league manager who has drawn a calendar template names it with `/images template calendar`, then
runs `/images test calendar` and receives a PNG built from fabricated data. They can see the crop
land where they intended, read every field they declared, and find the ones they got wrong —
without a season, a division, or a real round existing.

**Why this priority**: This is the whole rendering path — catalogue, fill, crop, rasterise — behind
a command that depends on nothing else. It is the only slice that can be built and demonstrated on
its own, and every later story reuses it. A manager can author and correct a template with this
alone, which is real value even if nothing else in this feature ships.

**Independent Test**: Enable the images module, name a calendar template, run `/images test calendar`,
and confirm a PNG returns whose crop, fields and fallback behaviour match the fabricated division
described in the wip-spec's § "Test data".

**Acceptance Scenarios**:

1. **Given** a valid template declaring N rounds, **When** `/images test calendar` is run, **Then** a
   PNG is returned drawn for a division named "Test Division" of tier 1 and season 1, holding N−1
   rounds, cut at the crop point of round N−1.
2. **Given** a template declaring exactly one round, **When** the command is run, **Then** one round
   is fabricated and the image is drawn at the full height the template declares.
3. **Given** a template with room for them, **When** the command is run, **Then** the fabricated
   rounds include one of each format, one carrying no time, one whose track has no image file, and
   dates spanning more than one month.
4. **Given** a fabricated round whose track has no image file and a track directory holding
   `fallback.svg`, **When** the image is drawn, **Then** the fallback is drawn and a non-fatal
   error is reported alongside the command output.
5. **Given** a template that meets a fatal error, **When** the command is run, **Then** the error is
   reported to the caller naming what is at fault and no image is posted — this command has no
   textual counterpart and never falls back.
6. **Given** the server's track list is empty, **When** the command is run, **Then** it is rejected
   with a clear error.

---

### User Story 2 - Learn a template is unusable before a season is approved (Priority: P2)

A league manager names a calendar template and is told at once whether it can be drawn. A season
review lists any template at fault with its own reason, and approval is refused while one stands.

**Why this priority**: Without it, the first sign of a broken template is a season approval that
posts text where a league expected graphics. It depends on the catalogue from US1 but not on any
posting path, and it is what makes US3 safe to switch on.

**Independent Test**: Name templates with and without the mandatory fields and confirm the command
is rejected or accepted accordingly; run `/season review` with a faulty template and confirm it is
named with its reason and that approval is refused.

**Acceptance Scenarios**:

1. **Given** a template missing a mandatory field, **When** `/images template calendar` names it,
   **Then** the command is rejected naming the field, and the configuration is left as it stood.
2. **Given** a template declaring no round at all, or a gap in its round numbering, **When** it is
   named, **Then** the command is rejected naming what is at fault.
3. **Given** a template that is structurally valid, **When** it is named, **Then** it is accepted —
   round count is not judged at this moment, no division being in view.
4. **Given** a season whose most demanding division holds more rounds than the template declares,
   **When** `/season review` is run, **Then** the divergence is reported as a **warning** and
   approval is not refused on its account.
5. **Given** a template at fault, **When** `/season review` is run, **Then** it is named with its own
   reason; **And When** approval is attempted, **Then** approval is refused.
6. **Given** a validity report, **When** it is read, **Then** it states which layers were applied, so
   a template checked shallowly is not presented as fully valid.

---

### User Story 3 - The league sees its calendar as a graphic at season approval (Priority: P3)

On approving a season, each division's calendar is drawn as an image and posted to that division's
calendar channel, carrying the heading of the textual calendar as its message text. A division whose
generation fails gets the textual calendar instead, and the others are unaffected.

**Why this priority**: This is the feature's visible purpose, but it is worth nothing until a manager
can preview a template (US1) and trust it (US2). It carries the highest risk of harming an existing
flow, so it lands after both.

**Independent Test**: With the images module enabled and the `calendar` toggle on, approve a season
holding several divisions and confirm each calendar channel receives a PNG; force a fatal error in
one division and confirm it alone falls back to text.

**Acceptance Scenarios**:

1. **Given** the module enabled and the `calendar` toggle on, **When** a season is approved, **Then**
   one graphic is generated per division from the one configured template and posted to that
   division's calendar channel as an attachment.
2. **Given** the toggle is off or the module disabled, **When** a season is approved, **Then** the
   textual calendar is posted exactly as it is today.
3. **Given** a division whose generation meets a fatal error, **When** the season is approved,
   **Then** that division's calendar is posted as text, the error is reported in the logging channel,
   and every other division is still posted as an image.
4. **Given** a generation raising non-fatal errors, **When** the calendar is posted, **Then** those
   errors are reported in the logging channel naming the season and division, and **never** in the
   division's calendar channel.
5. **Given** a division holding more rounds than the template declares, **When** the season is
   approved, **Then** it is a fatal error naming the rounds, and that division falls back to text.
6. **Given** a posting that fails for a Discord service reason rather than a generation reason,
   **When** the failure occurs, **Then** the textual calendar is enqueued for retry.
7. **Given** a calendar is posted in either form, **When** the post succeeds, **Then** the id of the
   message is persisted against the division.
8. **Given** test mode is active and a season of past-dated rounds is approved, **When** the calendar
   is drawn, **Then** it is generated and posted exactly as for a live season, drawing the past dates
   its rounds record.

---

### User Story 4 - Redraw a calendar after the schedule changes (Priority: P4)

A league manager who has added, amended or cancelled a round after approval runs
`/division calendar sync`, and the division's calendar message is replaced by a freshly drawn one.

**Why this priority**: The calendar is drawn once and stands as the calendar the season was approved
with; this command is the only way to move it on. It depends on the persisted message id that US3
introduces, so it lands last.

**Independent Test**: Approve a season, amend a round, run `/division calendar sync` for that
division, and confirm the old message is gone, a new one stands in its place, and the new id is
persisted.

**Acceptance Scenarios**:

1. **Given** a division with a posted calendar, **When** `/division calendar sync` is run, **Then**
   the calendar is drawn anew, the previous message is deleted, the new one posted, and its id
   persisted.
2. **Given** the replacement cannot be produced, **When** the command runs, **Then** the previous
   message is **not** deleted and the command is rejected naming what is at fault.
3. **Given** a division with no calendar channel configured, **When** the command is run, **Then** it
   is rejected with a clear error.
4. **Given** a round cancelled after approval and no sync since, **When** the graphic is read,
   **Then** it still shows that round drawn as any other — the graphic stands as approved until
   synced.
5. **Given** the images module is disabled or the toggle is off, **When** the command is run, **Then**
   the textual calendar is reposted — the command refreshes whichever form the configuration calls
   for and is gated on neither module.
6. **Given** test mode is active, **When** `/division calendar sync` is run twice, **Then** the
   calendar channel holds exactly one calendar message after each run — the previous one is deleted
   as it would be in live mode, and no duplicate accumulates.

---

### Edge Cases

- **A template placing rounds down one column and then the next.** It cannot be cropped, the cut
  removing the foot of every column alike. The wip-spec directs authors to lay rounds across and
  then down; the module draws what it is given and the manager sees the result in `/images test`.
- **A round beyond the division's last that stands *beside* it rather than below.** Removed by its
  `round_<x>_group`, or field by field where no group is declared — the crop cannot reach it.
- **A template whose last declared round's crop point does not stand at the declared height.** See
  the clarification below.
- **A round of the mystery format.** Drawn and marked as such; never a reason to refuse a graphic.
- **A round for which no time is recorded.** Its time field would be emptied, not filled with a dash —
  but no round records no time, a round holding its date and time as one moment with no flag for a
  time not yet known. That is a deliberate design decision, so the case cannot arise, needs no
  calendar-specific handling, and is not fabricated in the test data.
- **A track with no image file and no `fallback.svg` in the directory.** Fatal — the graphic is not
  produced and the division falls back to text.
- **A division holding no round at all.** Fatal, and named as such.
- **A round whose track name matches no track record.** Rounds hold a track *name*, not an id, so a
  track renamed or removed mid-season leaves the mandatory country and grand prix name
  undeterminable. Fatal, and that division falls back to the textual calendar — which has no such
  failure mode, printing the name the round itself records.
- **The rasteriser is absent from the machine.** Rejected before any render is attempted, as
  `/images test` already does today.
- **Two divisions of one season, one drawable and one not.** The failure of one must not prevent the
  others being posted as images.

## Requirements *(mandatory)*

### Functional Requirements

**The catalogue and the render**

- **FR-001**: The system MUST declare the calendar's field catalogue as a code constant in the shared
  declaration module, classifying each field mandatory or optional and naming the asset class of its
  image field, per constitution Rule 10. The fields are those listed in the wip-spec's § "Calendar
  image generation".
- **FR-002**: The system MUST address a repeating round's fields as `round_<x>_<field>`, numbered
  from 1 without padding, and MUST express the collection in the catalogue as a name and a capacity
  rather than an enumerated list of ids.
- **FR-003**: The system MUST fill every field from the round and track records as the wip-spec's
  § "Resolution of the data to be placed" requires, drawing a mystery round with the values fixed in
  § "A round of the mystery format".
- **FR-004**: The system MUST render dates and times in the configured format and zone, appending the
  zone abbreviation to a time, and MUST NOT emit a per-reader timestamp on a graphic.
- **FR-005**: The system MUST resolve the round's track image from the configured track image
  directory by the normalised slug, with the three outcomes fixed by constitution Rule 13.

**The vertical crop**

- **FR-006**: The system MUST cut the image at the Y coordinate of the crop point of the division's
  final round, rewriting the root `height` and `viewBox` before rasterisation and leaving the width
  untouched, per the wip-spec's § "The vertical crop".
- **FR-007**: The system MUST remove a round standing beside the final round by its `_group`, or
  field by field where none is declared, and MUST leave a round falling wholly below the cut for the
  cut to remove.
- **FR-008**: Fields taken off the canvas by the crop or a group removal MUST NOT be treated as
  unresolved.

**Validity and failure**

- **FR-009**: The system MUST verify a named calendar template at the moment it is named, at season
  review, and immediately before every generation, all three reading one and the same evaluation,
  per constitution Rule 9.
- **FR-010**: At the moment a template is named, the system MUST verify only what is checkable
  without a division: that every division-independent mandatory field is present, that at least one
  round is declared, that rounds are numbered continuously from 1, and that each declares every
  mandatory round field including its crop point.
- **FR-011**: At season review, the system MUST additionally compare the template against the
  greatest round count of any division of the season, reporting a divergence as a **warning**, and
  MUST refuse **approval** where any template is at fault.
- **FR-012**: The system MUST treat each condition listed as fatal in the wip-spec's § "Handling of
  mismatches between division and template" as a fatal error naming what is at fault, including a
  division holding more rounds than the template declares.
- **FR-013**: The system MUST report non-fatal errors to the server's logging channel naming the
  season and division, additionally alongside a command's output where a command triggered the
  generation, and MUST NOT report any error in a division's calendar channel.
- **FR-014**: A fatal error met by an uncommanded posting MUST fall back to the textual calendar for
  that division alone; a fatal error met by a commanded posting MUST reject the command with nothing
  posted in consequence.

**Posting**

- **FR-015**: The system MUST generate one graphic per division from the single configured template,
  posting it to that division's calendar channel as an attachment on a message carrying the heading
  of the textual calendar as its text.
- **FR-016**: The system MUST persist the id of the calendar message against the division, for both
  the graphic and the textual calendar, so that the two flows agree on which message is the calendar.
- **FR-017**: The system MUST replace a calendar message by deleting it and posting anew rather than
  editing it, and MUST NOT delete the previous message until its replacement has been produced
  successfully. This deletion MUST NOT be suppressed while test mode is active: it is half of a
  replacement rather than a cleanup, and the forecast flow's test-mode deletion guard does not
  extend to it.
- **FR-018**: The system MUST provide `/division calendar sync`, taking a division name, which
  redraws and replaces that division's calendar and persists the new message id; it MUST be rejected
  where the division has no calendar channel configured.
- **FR-019**: The system MUST draw the calendar at season approval and on that command alone,
  refreshing it at no other point in a season.
- **FR-020**: Where a posting fails for a Discord service reason rather than a generation reason, the
  system MUST enqueue the textual calendar for retry.

**Test data**

- **FR-021**: `/images test calendar` MUST build the fabricated division described in the wip-spec's
  § "Test data" — one round fewer than the template declares, spanning the formats, a round whose
  track has no image, and dates across more than one month — replacing the generic sample data that
  command draws on today.
- **FR-022**: `/images test calendar` MUST be rejected with a clear error where the fabricated
  division would hold no round or the server's track list is empty, and MUST NOT fall back to text on
  a fatal error.

**Scope boundaries**

- **FR-023**: The graphic MUST carry no driver name, team name, session result, lifecycle label or
  Discord mention.
- **FR-024**: The system MUST NOT alter the textual calendar path, which MUST remain functional when
  the module is disabled, the toggle is off, or a render fails.
- **FR-025**: No image type other than the calendar is in scope. The other fourteen templates MUST
  continue to be checked only to the depth currently ratified for them.
- **FR-026**: Where the crop point of the last round a template declares does not stand at the height
  that template declares, the system MUST cut at that crop point all the same and raise a non-fatal
  error naming the template. It MUST NOT reject such a template, which draws correctly for every
  division smaller than its declared capacity.
- **FR-027**: The system MUST ship a default `mystery.svg` in `resources/tracks/`, generic rather
  than league-specific, authored to the same aspect as the directory's ordinary assets and bound by
  the asset rules of constitution Rule 6.

**Test mode**

- **FR-028**: The calendar MUST be fully functional while test mode is active, behaving identically
  to live mode in generation, posting and replacement. No branch on the test-mode flag may be
  introduced into any of the three.
- **FR-029**: The system MUST NOT consult test mode's fake driver roster when drawing a calendar. The
  graphic names no driver and no team (FR-023), so the roster has no bearing on it.
- **FR-030**: The calendar MUST draw the dates and times its rounds record, without regard to whether
  they fall in the past or the future. A test-mode season is past-dated so that phases fire on
  advance, and its calendar is drawn exactly as a live season's is.

### Key Entities

- **Calendar field catalogue**: the authoritative list of the ids the calendar render addresses, the
  operation each receives, its mandatory/optional classification, the asset class of the image field,
  and the round collection's name and capacity. One entry in the shared catalogue module; the same
  object read by the fill pipeline and by validity checking.
- **Division**: gains `calendar_message_id`, the id of the message carrying its calendar in its
  calendar channel. Sits beside the existing `lineup_message_id`. Written on every calendar posting,
  textual or graphic.
- **Round**: read for its number, format, scheduled date and time, and its track. Not modified.
- **Track**: read for its country, grand prix name and track name, and normalised to the slug that
  resolves the round's image. Not modified.
- **Render notice**: the existing non-fatal record, raised here for a substituted font, a truncated
  field, an emptied optional field, and a track image standing in from `fallback.svg`.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A league manager can go from a drawn template to a previewed calendar PNG using one
  command and no season data.
- **SC-002**: 100% of the fatal conditions listed in the wip-spec's mismatch section are reported
  naming the specific template, division or round at fault — never a generic failure and never a
  group of templates in place of the one at fault.
- **SC-003**: A season approval in which one division's calendar cannot be drawn still posts every
  other division's calendar as an image, and posts the failing one as text.
- **SC-004**: A division holding N rounds against a template declaring M ≥ N produces an image whose
  height equals the crop point of round N, for every N from 1 to M.
- **SC-005**: No error, fatal or otherwise, ever appears in a channel the drivers of the league read.
- **SC-006**: Approving a season with the module disabled or the toggle off produces byte-identical
  calendar output to the behaviour before this feature.
- **SC-007**: Every graphic verified during development and in tests is verified as a rasterised PNG,
  never as an SVG in a browser.

## Assumptions

- **The textual calendar's current shape is preserved.** It is posted today inline at season
  approval, one message per division, with rounds rendered as Discord timestamps. This feature adds
  a message id to it and an image alternative; it does not restyle it.
- **`/images test calendar` already exists** and renders generic sample data through the shared
  render service. This feature replaces its data source for the calendar kind alone, leaving the
  other kinds as they are.
- **`/division calendar sync` is modelled on `results standings sync` and `results rounds sync`**,
  which already exist, and follows their permission model and command shape.
- **A round always records a time.** `scheduled_at` is a single moment carrying both date and time,
  and there is deliberately no flag for a time not yet known. The wip-spec's emptying provision
  therefore stands against a round shape the bot does not hold; should that shape ever exist, the
  engine's generic handling of an optional field whose value cannot be determined already empties it,
  so no calendar-specific branch is owed either way.
- **No new configuration is introduced.** Every value this feature reads — template filename, track
  image directory, date format, time format, time zone, the `calendar` toggle — was configured in the
  035 increment.
- **Season review's existing image-module section is extended, not replaced.** The calendar becomes
  the first image type reported at a depth beyond Layer 1.
- **Test mode needs no new trigger for the calendar.** Unlike the weather and RSVP graphics, which
  the wip-spec ties to `/test-mode advance` walking the phase chain, the calendar's only triggers are
  season approval and `/division calendar sync` — both directly reachable while test mode is active,
  and season approval is already built for it. No `/test-mode` command is added or altered.
- **The forecast flow's test-mode deletion guard is left as it stands.** `delete_forecast_message`
  continues to suppress deletions under test mode and `flush_pending_deletions` to action them on
  disable. FR-017 states that the calendar does not join that scheme; nothing about the forecast
  behaviour changes.

## Clarifications Resolved

Both were put to the author on 2026-08-12 and answered. The decisions are carried in FR-026 and
FR-027 above, and the first is recorded in the wip-spec, which is where the rule itself lives.

- **A final crop point that does not stand at the declared height** is honoured as written and a
  non-fatal error raised. Rejecting the template was considered and declined: it would refuse a
  template that draws correctly for every division smaller than its own capacity, and the crop stays
  one unconditional behaviour rather than two.
- **A default `mystery.svg` ships** in `resources/tracks/`, so that a league drawing a Mystery round
  gets a sensible graphic without authoring one. It is generic, not league-specific, and so belongs
  in the tracked resources beside `fallback.svg`.
