# Feature Specification: Results Image Generation

**Feature Branch**: `039-results-image-generation`
**Created**: 2026-08-12
**Status**: Draft
**Input**: User description: the results image type — two field catalogues addressed by row ordinal,
data resolution shared with the textual table, sanction phases, the fastest-lap colour, mismatch
handling, generation and posting through the results lifecycle, and test data.

> **Normative source.** The rules this feature implements are stated in
> [`docs/wip-specs/image_module_specification.md`](../../docs/wip-specs/image_module_specification.md)
> — § "Conventions of every graphic" and § "Results image generation" — and are governed by
> Principles VII, XII and XIV of the constitution. This document does **not** restate those rules.
> It states what must be built, who it is for, and how each obligation is verified, and cites the
> wip-spec where the rule itself lives. Where this document and the wip-spec disagree, the wip-spec
> wins and this document is the one to correct.

> **What makes this type different.** Results is the first aspect drawn by **two templates**: one
> `results` toggle, two configured slots, two catalogues that share every field but the columns of
> their rows. It is also the first graphic that re-presents values the textual table already
> renders, so constitution v4.4.0 (XIV.7) requires the two to be produced by one and the same
> formatting code rather than by two implementations that happen to agree. Three further forms
> ratified at v4.4.0 appear here first: the **column group** (`postrace_penalty_group`), the **block
> group** (`fastest_lap_group`), and an **absent datum drawing the class fallback** (a tyre that was
> never recorded).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Preview both results graphics before a round depends on them (Priority: P1)

A league manager who has drawn a qualifying template and a race template names each with
`/images template results-qualifying` and `/images template results-race`, runs
`/images test results`, and receives two PNGs built from fabricated data — without a season, a
division, a round or a submitted result existing.

**Why this priority**: It is the whole rendering path — two catalogues, row collection, fill, asset
resolution, recolour, rasterise — behind one command that depends on no round data. Every later
story reuses it, and a manager can author and correct both templates with this alone.

**Independent Test**: Enable the images module, name both templates, run `/images test results`, and
confirm two PNGs return matching the fabricated session described in the wip-spec's § "Test data".

**Acceptance Scenarios**:

1. **Given** both templates named, **When** `/images test results` is run, **Then** two PNGs are
   returned — one from the qualifying template, one from the race template — both drawn for a
   division named "Test Division" of tier 1 and season number 1, at round 1 of a track of the
   server's track list, and both labelled "Final Results".
2. **Given** a template declaring N rows, **When** the test data are fabricated, **Then** N−1
   entries are built so an unused row can be seen; where N is 1, one entry is built and no unused
   row is evaluated.
3. **Given** the qualifying image, **When** it is drawn, **Then** it exhibits, so far as N−1 entries
   allow: the first-placed entry with an empty gap, a gap below one second and one above one
   minute, an entry with no tyre recorded, an entry that set no time, an entry disqualified in the
   penalty phase and another disqualified on appeal, an entry sanctioned by neither phase carrying a
   dash in both sanction fields, and an entry conferred no points.
4. **Given** the race image, **When** it is drawn, **Then** it exhibits, so far as N−1 entries allow:
   a first-placed total race time exceeding one hour, an interval below one second and one above one
   minute, an entry one lap behind and another more than one lap behind, an entry that did not
   finish, one that did not start and one disqualified in the penalty phase, an in-game penalty of a
   whole number of seconds, one of a fraction below a second and one absent, a penalty-phase time
   penalty, an entry sanctioned by neither phase, an entry disqualified in the penalty phase and
   again on appeal, an entry conferred no points, and the fastest-lap bonus held by the entry that
   did not finish rather than by the first-placed entry.
5. **Given** the fabricated race points configuration, **When** it is built, **Then** it confers the
   fastest-lap bonus with no limit upon the position of its holder.
6. **Given** the fabricated drivers, **When** they are built, **Then** their nationalities are drawn
   from those the signup wizard accepts, at least one being the value recorded for a driver who
   stated none, and their teams are drawn from the server's team configuration.
7. **Given** a fatal error, **When** the command is run, **Then** it is reported to the caller naming
   what is at fault and no image is posted — this command has no textual counterpart and never falls
   back.
8. **Given** the server holds no team beyond the reserve team, **When** the command is run, **Then**
   it is rejected with a clear error.

---

### User Story 2 - A division's results posted as graphics through their lifecycle (Priority: P2)

With the `results` toggle enabled, each session of a round is posted to the division's results
channel as a PNG carrying the classification, the sanctions and the points, while the heading and
the lifecycle label remain message text. Every occasion that reposts the textual table redraws the
graphic and replaces the message.

**Why this priority**: This is the aspect a league actually turns on. It depends on the rendering
path of US1 and on nothing else.

**Independent Test**: Enable the toggle, submit a session's results, and confirm the results channel
carries a PNG under the heading and label; close the penalty phase and then the appeal phase, and
confirm the message is replaced each time with the sanction columns resolved in turn.

**Acceptance Scenarios**:

1. **Given** the `results` toggle enabled and a session's results first posted, **Then** a PNG is
   attached to the message carrying the heading and the lifecycle label, and the textual table is
   not posted in that channel.
2. **Given** results at the provisional stage, **When** the graphic is drawn, **Then** both sanction
   fields are empty on every row, and a template declaring `postrace_penalty_group` or
   `appeal_penalty_group` has that heading removed with its group.
3. **Given** the penalty phase closed, **When** the graphic is redrawn, **Then** the penalty field of
   every row is resolved and the appeal field remains empty; given the appeal phase closed, both are
   resolved.
4. **Given** an existing results message, **When** the graphic is redrawn, **Then** the new message
   is produced first, the previous one deleted only after it succeeds, and the id of the replacement
   persisted in place of the old.
5. **Given** a race session in which one entry holds the fastest-lap bonus, **When** the graphic is
   drawn, **Then** that entry's fastest-lap field carries the configured fastest-lap colour and no
   other row is recoloured.
6. **Given** a session recorded as cancelled, **When** it is posted, **Then** its textual notice is
   kept whatever the toggle says.
7. **Given** the round's results are resynchronised, an amendment is approved, or a points
   configuration change causes recalculation, **When** the table would be reposted, **Then** the
   graphic is redrawn and the message replaced.
8. **Given** the submission channel opened for a round's results, **When** anything is posted there,
   **Then** it remains textual in its entirety.
9. **Given** a round's standings are posted alongside its results, **When** either fails, **Then**
   the other is unaffected.

---

### User Story 3 - Learn a results template is unusable before a round depends on it (Priority: P3)

A league manager naming either results template is told at once what it cannot draw. A season review
names every faulty template, and approval of the season is refused while a fault stands.

**Why this priority**: Without it, the first sign of a faulty template is a round that posts text
where a league expected graphics. It depends on the catalogues of US1 and on no posting path.

**Independent Test**: Name templates with and without each mandatory field, with no row, with a gap
in the row numbering, and carrying a field of the sibling catalogue; confirm each is rejected or
accepted as specified, and that `/season review` names each fault and refuses approval.

**Acceptance Scenarios**:

1. **Given** a template lacking a mandatory field that does not depend on the entries of a session,
   **When** it is named, **Then** the command is rejected naming the field, and the configuration is
   left as it stood.
2. **Given** a template declaring no row at all, **When** it is named, **Then** it is rejected.
3. **Given** a template whose rows are not numbered continuously from 1, **When** it is named,
   **Then** it is rejected naming the gap.
4. **Given** a template declaring a row that lacks a mandatory row field, **When** it is named,
   **Then** it is rejected naming the field.
5. **Given** a qualifying template carrying a field of the race row catalogue, or a race template
   carrying a field of the qualifying row catalogue, **When** it is named, **Then** it is rejected
   naming the field and the catalogue it belongs to.
6. **Given** a template whose rows are sound, **When** it is named, **Then** it is accepted — the
   entries of a session are not known at that moment and are not approximated.
7. **Given** `/season review` with the module enabled and the `results` toggle on, **When** it is
   run, **Then** each of the qualifying and race templates is reported valid or invalid separately,
   and an invalid one refuses approval of the season while it stands.
8. **Given** a template that passed every earlier check, **When** a render is about to be made,
   **Then** the same checks are repeated against the concrete session and a fault fails that render.

---

### User Story 4 - Degradations reported to staff, never drawn for drivers (Priority: P4)

Non-fatal degradations met while drawing a session's graphic are reported in the server's logging
channel, naming the season, division, round and session; a fatal one falls back to the textual table
for an uncommanded posting and rejects the command for a commanded one. No problem and no notice
ever reaches a channel drivers read.

**Why this priority**: It makes US2 safe to leave switched on unattended. It depends on US2 and adds
no drawing of its own.

**Independent Test**: Draw a session whose driver has no nationality recorded, and one whose team has
no image file, and confirm each is reported in the logging channel and nowhere else; make a template
fault and confirm the textual table is posted in its place.

**Acceptance Scenarios**:

1. **Given** a non-fatal degradation, **When** the graphic is drawn, **Then** it is reported in the
   logging channel naming the season, the division, the round and the session, and additionally
   alongside the output of a command where a command triggered the generation.
2. **Given** any degradation or fault, **When** it is reported, **Then** it never appears in the
   results channel of a division.
3. **Given** a fatal error in a posting no command triggered, **When** it is met, **Then** the
   session's results are posted as the textual table instead and the error reported in the logging
   channel.
4. **Given** a fatal error in a posting a command triggered, **When** it is met, **Then** the command
   is rejected, nothing is posted in consequence, and the caller is told what is at fault.
5. **Given** one session of a round failing, **When** it fails, **Then** the other sessions of that
   round and the sessions of every other division are drawn and posted regardless.
6. **Given** a generated image that fails to post for a reason of the Discord service rather than of
   the generation, **When** it fails, **Then** it is the textual table that is enqueued for retry.

---

### Edge Cases

- A session with more entries than the template declares rows: fatal, naming the drivers that would
  have been dropped.
- A session with fewer entries than the template declares rows: the unused `row_<x>_group` is removed
  in its entirety and nothing is reported.
- A driver disqualified by the penalty wizard: dropped to the bottom and renumbered by the results
  module before the graphic is drawn; the position field is filled from the row's own ordinal.
- An entry disqualified in the penalty phase and again on appeal: the appeal field carries "DSQ" and
  the penalty field carries whatever time penalty that phase applied.
- A qualifying session in which no entry set a lap: every gap field is emptied.
- A race in which no time is recorded for the first-placed entry: every entry carries its own total
  race time in place of an interval.
- An entry that did not finish but holds the fastest-lap bonus within the configuration's position
  limit: points are shown against an outcome of "DNF".
- A session whose points configuration confers no fastest-lap bonus, or whose holder finished outside
  the position limit, did not start or was disqualified: no row is recoloured and
  `fastest_lap_group` is removed.
- A driver whose nationality is absent: the flag field is removed and a non-fatal error reported —
  unless nationality collection is switched off at its source, when nothing is reported.
- An entry with no tyre recorded: the tyre directory's fallback is drawn, and nothing is reported;
  where that directory holds no fallback, the field is removed and still nothing is reported.
- An entry whose team role matches no team of the division: the role's own name is placed and
  normalised for the team image.
- A reserve driver standing in for another: the team drawn is the team whose car they drove, never
  the reserve team.

## Requirements *(mandatory)*

### Functional Requirements

**Field catalogues**

- **FR-001**: The system MUST declare two field catalogues — one for the qualifying template and one
  for the race template — as code constants keyed by the template slot each fills, holding every
  field the wip-spec's § "Results image generation" lists with its stated classification and, for
  image fields, its asset class.
- **FR-002**: Both catalogues MUST declare their row collection by prefix and by the rule fixing its
  capacity, never as an enumerated list of identifiers and never as a fixed number.
- **FR-003**: The two catalogues MUST share the declaration of the fields they hold in common and
  remain separately addressable, so a report can name which of the two is at fault.
- **FR-004**: The row collection MUST be discriminated by ordinal, numbered continuously from 1
  without padding.
- **FR-005**: The system MUST treat a field belonging to the **sibling** catalogue's row as a fatal
  fault of the template, and MUST treat an identifier belonging to no catalogue as chrome.

**Data resolution**

- **FR-006**: Every value the graphic draws that the textual table also draws MUST be produced by the
  same formatting code the textual table uses; no rendering of such a value may be restated for the
  graphic.
- **FR-007**: The system MUST fill each row's position field from the ordinal of that row, making no
  comparison between it and any recorded value.
- **FR-008**: The system MUST render the qualifying gap as the entry's best lap less the session's
  reference lap, the reference lap being the best lap of the first-placed entry or, where it holds
  none, that of the first entry of the classification that does; the field MUST be empty for the
  entry holding the reference lap, and for every entry where no entry of the session holds a lap.
- **FR-009**: The system MUST render a race entry's time as the total race time for the first-placed
  entry and as the interval to that entry for any other classified entry on the same lap; where no
  time is recorded for the first-placed entry, every entry MUST carry its own total race time.
- **FR-010**: The system MUST render an entry finishing laps behind as the count of those laps in
  place of an interval, singular for one and plural beyond.
- **FR-011**: The system MUST place the outcome literal of an entry that did not finish, did not
  start or was disqualified in its best lap field or its time field, whatever time or lap count is
  recorded against it.
- **FR-012**: The system MUST draw the points the session conferred, the fastest-lap bonus included.
- **FR-013**: The system MUST empty a text field where the textual table would show a dash, and MUST
  remove an image field rather than empty it — the two sanction fields excepted.
- **FR-014**: The system MUST resolve each sanction field to one of three states: empty where the
  phase it stands for is not closed, a dash where the phase is closed and applied nothing, and the
  time penalty or "DSQ" where the phase is closed and applied something.
- **FR-015**: The system MUST render a time penalty in signed seconds to the precision recorded — no
  decimal part for a whole number of seconds, three decimal places for a fraction — and MUST NOT
  round one to a whole second for display.
- **FR-016**: The system MUST carry a disqualification in one sanction field only.
- **FR-017**: The system MUST treat the penalty phase as closed once the round's results leave the
  provisional stage and the appeal phase as closed once they reach the final stage, and MUST draw
  the same lifecycle label the message carries.
- **FR-018**: The system MUST fill a race entry's in-game penalty field with the penalty or with a
  dash, never leaving it empty.
- **FR-019**: The system MUST set the text colour of the fastest-lap field of the entry holding the
  bonus to the configured fastest-lap colour, merged into that element's inline style, and MUST fill
  that field as it fills any other; no other row may be recoloured.
- **FR-020**: The system MUST draw the name of a person as the conventions require, carrying no
  Discord mention.
- **FR-021**: The system MUST place, and normalise for the team image, the name of the division's
  team holding the entry's recorded team role, falling back to the name of the role itself where the
  division holds no such team.
- **FR-022**: The system MUST draw the team whose car a reserve driver drove, never the reserve team,
  and MUST draw no reserve block.
- **FR-023**: The system MUST draw the session name as the wip-spec states it for the sprint format
  and for every other.
- **FR-024**: The system MUST draw no image of the track, no name of the country, no date of the
  round and no name of the points configuration.

**Assets**

- **FR-025**: The system MUST resolve the driver flag from the configured flag directory by the
  recorded nationality; where the nationality is absent it MUST remove the field and report a
  non-fatal error, and where its collection is switched off at its source it MUST report nothing.
- **FR-026**: The system MUST resolve the tyre image from the configured tyre directory by the
  recorded compound; where no compound is recorded it MUST draw that directory's fallback, or remove
  the field where the directory holds none, reporting nothing in either case.
- **FR-027**: The system MUST resolve the team image from the configured team image directory by the
  normalised team name.

**Validity**

- **FR-028**: The system MUST verify the fields that do not depend on the entries of a session at
  every moment a template is verified — when it is named, at season review, and immediately before a
  render — a missing mandatory one being fatal.
- **FR-029**: The system MUST verify, at the moments no classification exists, only that the template
  declares at least one row, numbered continuously from 1, and holding every mandatory row field;
  and MUST NOT approximate the entries of a session at those moments.
- **FR-030**: The system MUST verify the entry-dependent fields against the session being drawn
  immediately before each render.
- **FR-031**: The system MUST report the qualifying and race templates separately at `/season review`
  and in `/images config view`, naming which of the two is invalid.
- **FR-032**: The system MUST refuse approval of a season while either template stands invalid, and
  MUST reject the `/images template results-qualifying` or `/images template results-race` command
  that names a faulty file, leaving the configuration as it stood.
- **FR-033**: The system MUST treat entries in excess of the rows a template declares as fatal,
  naming the drivers that would have been dropped, and MUST remove the `row_<x>_group` of each row in
  excess of the entries without reporting anything.

**Generation and posting**

- **FR-034**: The system MUST generate a graphic per session and attach it to the message carrying
  that session's heading and lifecycle label, which remain message text, when the `results` toggle is
  enabled.
- **FR-035**: The system MUST redraw the graphic and replace the message on every occasion the
  textual table is currently reposted: first provisional posting, penalty phase closed, appeal phase
  closed, results resynchronised by command, amendment approved, and points configuration change
  causing recalculation.
- **FR-036**: The system MUST produce the replacing message before deleting the message it replaces,
  and MUST persist the id of the replacement in place of the old.
- **FR-037**: The system MUST keep the textual notice of a cancelled session whatever the toggle says.
- **FR-038**: The system MUST replace the textual table in the division's results channel alone, and
  MUST leave the round's results submission channel textual in its entirety.
- **FR-039**: The system MUST report non-fatal errors in the server's logging channel naming the
  season, division, round and session, additionally alongside a triggering command's output, and
  never in a channel drivers read.
- **FR-040**: The system MUST fall back to the textual table on a fatal error in a posting no command
  triggered, and MUST reject the command and post nothing where a command triggered it.
- **FR-041**: The system MUST allow the failure of one session to prevent neither the other sessions
  of its round, nor the sessions of other divisions, nor the standings posted alongside them.
- **FR-042**: The system MUST enqueue the textual table for retry where a generated image fails to
  post for a reason of the Discord service rather than of the generation.

**Test data**

- **FR-043**: `/images test results` MUST generate two images — one per template — for a division
  named "Test Division" of tier 1 and season number 1, at round 1 of a track of the server's track
  list, both labelled "Final Results".
- **FR-044**: The command MUST fabricate one entry fewer than the rows the template declares, drawn
  from the server's team configuration, or exactly one where the template declares a single row.
- **FR-045**: The fabricated entries MUST exhibit the cases the wip-spec's § "Test data" enumerates
  for each of the two images, so far as the declared row count allows.
- **FR-046**: The command MUST fabricate a race points configuration conferring the fastest-lap bonus
  with no limit upon the position of its holder.
- **FR-047**: The command MUST reject with a clear error where the server holds no team beyond the
  reserve team, and MUST report a fatal error to its caller without falling back to any textual
  output.

### Key Entities

No new entity is introduced and none is amended. This feature reads what already exists:

- **SessionResult** — carries the results message id the image flow replaces, and the session type
  selecting which of the two templates draws it.
- **QualifyingSessionResult / RaceSessionResult** — one row of the classification each, holding the
  recorded position, outcome, tyre, laps, times, penalties by phase and points.
- **Round** — carries the results lifecycle stage from which the label and the two phase closures
  follow, and the round number and track the headings draw.
- **Team / Division** — resolve an entry's recorded team role to a name, and the division name and
  tier the graphic draws.
- **Points configuration** — decides the fastest-lap bonus and its position limit.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A league manager can author both results templates and confirm each draws correctly
  without a season, a division, a round or a submitted result existing, using one command.
- **SC-002**: Every value that appears in both the graphic and the textual table of the same session
  is identical, character for character, for every case the test data exhibit.
- **SC-003**: A results template that cannot draw a session is named, with its own reason and
  distinguished from its sibling, at the moment it is configured — not at the moment a round is
  posted.
- **SC-004**: No render fault or degradation is ever visible in a channel drivers read; every one is
  visible to staff in the logging channel.
- **SC-005**: A league that switches the `results` toggle off returns to the textual table with no
  loss of information, and a failed render leaves a league no worse informed than one that never
  enabled the module.
- **SC-006**: A round is never left partly posted by a failing render: every session, division and
  standings posting of that round completes in its own right.
- **SC-007**: Every generated image is verified as a rasterised PNG, never as an SVG previewed in a
  browser.

## Assumptions

- The track drawn by `/images test results` is the first of the server's track list; the wip-spec
  requires only that it be one of them.
- The renumbering of a driver disqualified by the penalty wizard is performed and persisted by the
  results module before the graphic is drawn, as the wip-spec states; this feature reads the order
  as it stands and does not reorder.
- "The name of a person" is the convention now stated in the wip-spec's § "Conventions of every
  graphic": the display name of the driver's Discord account on the server at the moment of
  generation, falling through the recorded signup names, the test display name, and the user ID.
- The rendering the graphic shares with the textual table is the one already delivered for the
  textual results tables. Making its values reachable one field at a time, rather than as a finished
  line of text, is expected to be the extent of the change to the textual path — no rendering rule
  is restated or altered.
- The eight-aspect toggle, the fastest-lap colour, the tyre, flag and team image directories, and the
  two template slots were all delivered with the module's configuration surface and are read as they
  stand.

## Out of Scope

- Every other image type: standings, attendance, RSVP, weather, verdicts, and any change to the
  calendar or lineup types already built.
- Any change to how results are submitted, computed, renumbered, penalised or appealed. The graphic
  is a second presentation of one output, not a second output.
- Any change to the standings posted alongside a round's results, which the standings section of the
  wip-spec governs.
- Any change to the textual results path beyond two things: making the values it already renders
  reachable for the graphic, and **correcting the precision with which it renders a time penalty**.
  The wip-spec states that rule for a penalty "wherever one is placed", and the textual table
  currently truncates a fractional penalty to a whole second — so a shared renderer cannot be
  correct for the graphic while leaving the text path as it stands. Recorded in
  [plan.md](./plan.md) § Complexity Tracking and in [research.md](./research.md) § R5.
